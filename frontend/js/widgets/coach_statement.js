// widgets/coach_statement.js — Widgets.CoachStatement: THE per-coach settlement statement, shared by
// the ADMIN (any coach) and the COACH (their own). Same widget, config ONLY — golden rule, no fork.
//
// This is the coach-side equivalent of a client invoice: a document you can read, print and act on.
// It answers three questions, and they deliberately sit on DIFFERENT date bases — the document says
// so out loud, because "the numbers don't tie" is the first thing anyone assumes otherwise:
//
//   1. WHAT DID I TEACH?      sessions by client, by DAY — bounded on the SESSION's own date.
//   2. WHERE IS THAT MONEY?   paid to the club · collected by the coach · still outstanding.
//   3. WHAT DO WE OWE?        total collected × commission = owed to the club, minus what the club
//                             already holds, = NET. Bounded on when the money ARRIVED, because
//                             commission is paid on funds RECEIVED (docs/specs/01 §D7).
//
// A lesson taught in July and paid in August is therefore OUTSTANDING in July's work log and settles
// in August. That is the rule working, not a mismatch.
//
//   cfg.scope.role  'admin' | 'coach'          — wording ("you" vs the coach's name) + the payout action
//   cfg.coachUserId                            — admin only; the coach being viewed
//   cfg.month                                  — 'YYYY-MM'
//   cfg.load(coachUserId, month) -> the /api/admin/coach-statement payload
//   cfg.back {label, hash}?                    — a back bar
//   cfg.onRecordPayout(settlement, refresh)?   — admin: settle the net
(function () {
  function mount(host, cfg) {
    var UI = window.UI, el = UI.el;
    var isCoach = ((cfg.scope && cfg.scope.role) || "admin") === "coach";
    var MONTH = cfg.month || null;
    var CUR = "ZAR";
    function money(m) { return UI.money(m || 0, CUR); }
    // "you"/"your" for the coach reading their own; the coach's name for an admin reading it.
    function who(name) { return isCoach ? "you" : (name || "the coach"); }

    function monthLabel(ym) {
      try {
        var p = String(ym).split("-");
        return new Date(p[0], parseInt(p[1], 10) - 1, 1)
          .toLocaleDateString(undefined, { month: "long", year: "numeric" });
      } catch (e) { return ym; }
    }
    function shiftMonth(ym, d) {
      var p = String(ym).split("-");
      var dt = new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1 + d, 1);
      return dt.getFullYear() + "-" + String(dt.getMonth() + 1).padStart(2, "0");
    }
    function dayLabel(iso) {
      try {
        return new Date(iso + "T00:00:00").toLocaleDateString(undefined,
          { weekday: "short", day: "numeric", month: "short" });
      } catch (e) { return iso; }
    }

    var CUSTODY = {
      to_club:     { label: "Paid to club",   tone: "good" },
      with_coach:  { label: "Collected",      tone: "warn" },
      outstanding: { label: "Outstanding",    tone: "bad"  },
      not_charged: { label: "No charge",      tone: "mute" },
    };

    function row(label, minor, sub, tone, strong) {
      return el("div", { class: "cf-item" }, [
        el("div", {}, [
          el("div", { style: strong ? "font-weight:700" : "font-weight:600", text: label }),
          sub ? el("div", { class: "cf-muted", style: "font-size:.8rem", text: sub }) : null,
        ].filter(Boolean)),
        el("div", {
          style: "font-weight:" + (strong ? "800" : "700") + ";text-align:right"
            + (tone === "bad" ? ";color:var(--danger)" : tone === "good" ? ";color:var(--success)" : ""),
          text: money(minor),
        }),
      ]);
    }

    function load() {
      UI.clear(host);
      host.appendChild(el("div", { class: "cf-loading", style: "min-height:200px", text: "Loading statement…" }));
      Promise.resolve(cfg.load(cfg.coachUserId || null, MONTH)).then(render, function (e) {
        UI.clear(host);
        host.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) }));
      });
    }

    function render(data) {
      data = data || {};
      MONTH = data.month || MONTH;
      CUR = data.currency || CUR;
      var st = data.settlement || {};
      var sessions = data.sessions || { clients: [], totals: {} };
      var tot = sessions.totals || {};
      var name = data.coach_name || (cfg.coachName || null);

      var wrap = el("div", {});
      if (cfg.back) wrap.appendChild(UI.backBar(cfg.back.label, cfg.back.hash));

      wrap.appendChild(el("div", {
        class: "cf-row",
        style: "justify-content:space-between;align-items:center;margin-bottom:4px",
      }, [
        el("h1", { style: "margin:0", text: isCoach ? "Your statement" : (name ? name + " — statement" : "Coach statement") }),
        el("div", { class: "cf-row", style: "gap:6px;align-items:center" }, [
          el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "‹",
            onclick: function () { MONTH = shiftMonth(MONTH, -1); load(); } }),
          el("span", { style: "font-weight:600;min-width:104px;text-align:center", text: monthLabel(MONTH) }),
          el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "›",
            onclick: function () { MONTH = shiftMonth(MONTH, 1); load(); } }),
        ]),
      ]));

      // ---------------------------------------------------------------- 1. THE SETTLEMENT
      // Deliberately FIRST: it is the number both parties came for. Every line of the arithmetic is
      // shown rather than a single total, because a coach who cannot follow the maths will not trust
      // the answer — and this is the figure that decides who pays whom.
      var net = st.net_minor || 0;
      var owesCoach = net >= 0;
      var settleList = el("div", { class: "cf-list" }, [
        row("Paid to the club", st.club_held_minor, "Yoco + EFT — the only two ways it can receive", "good"),
        row(isCoach ? "Collected by you" : "Collected by the coach", st.coach_held_minor,
            "Taken courtside — never reached the club", "warn"),
        row("Total collected", st.total_collected_minor, null, null, true),
      ]);
      // WHAT that money was. "Paid to the club R17,000" against R6,000 of remembered lessons reads
      // as a threefold error until you can see that it also contains class seats and pack sales — a
      // lesson pack is charged (and its commission attributed) at the moment of sale, at the FULL
      // pack price, not spread across the sessions drawn from it.
      var KIND_LABEL = { lesson: "Lessons", "class": "Class seats", pack: "Session packs" };
      var kinds = st.by_kind || {};
      var kindKeys = Object.keys(kinds).filter(function (k) {
        return (kinds[k].club_minor || 0) || (kinds[k].coach_minor || 0);
      });
      var breakdown = null;
      if (kindKeys.length > 1) {
        breakdown = el("div", { class: "cf-list", style: "margin:6px 0 0" },
          [el("div", { class: "cf-muted", style: "font-size:.8rem;font-weight:600;padding:2px 0",
            text: "└ what that was" })].concat(kindKeys.map(function (k) {
              var v = kinds[k];
              return row("   " + (KIND_LABEL[k] || k) + " × " + (v.n || 0),
                         (v.club_minor || 0) + (v.coach_minor || 0),
                         (k === "pack"
                           ? "Charged in full when the pack was sold, not per session"
                           : null));
            })));
      }
      var settleList2 = el("div", { class: "cf-list" }, [
        row("Club commission" + (st.effective_pct != null ? " (" + st.effective_pct + "%)" : ""),
            -(st.commission_minor || 0), "On everything collected, however it was collected", "bad"),
        row("Less: already held by the club", -(st.club_held_minor || 0),
            "The club is holding this money already", null),
      ]);
      var netLine = el("div", {
        class: "cf-item",
        style: "border-top:2px solid var(--line);margin-top:6px;padding-top:12px",
      }, [
        el("div", {}, [
          el("div", { style: "font-weight:800;font-size:1.05rem",
            text: owesCoach ? (isCoach ? "The club owes you" : "The club owes " + (name || "the coach"))
                            : (isCoach ? "You owe the club" : (name || "The coach") + " owes the club") }),
          el("div", { class: "cf-muted", style: "font-size:.82rem",
            text: owesCoach
              ? "The club collected more than its commission, so the balance is the coach's."
              : "The coach collected more than they earned, so the club's commission is still with them." }),
        ]),
        el("div", {
          style: "font-weight:800;font-size:1.15rem;text-align:right;color:"
            + (owesCoach ? "var(--success)" : "var(--danger)"),
          text: money(Math.abs(net)),
        }),
      ]);

      var settleCard = [
        el("h3", { text: "Settlement for " + monthLabel(MONTH) }),
        el("p", { class: "cf-muted", style: "margin:-4px 0 8px;font-size:.86rem",
          text: "Based on money that ACTUALLY ARRIVED this month. Commission is only ever charged on "
              + "funds received — a lesson taught this month but paid next month settles next month." }),
        settleList,
      ].concat(breakdown ? [breakdown] : []).concat([settleList2, netLine]);

      // Rent, payouts and adjustments move the number that actually changes hands, so show them
      // rather than leaving the coach to wonder why the transfer differs from the net above.
      var led = data.ledger_detail || {};
      var extras = (led.rent_minor || 0) || (led.payouts_minor || 0) || (led.adjustments_minor || 0);
      if (extras) {
        settleCard.push(el("div", { class: "cf-list", style: "margin-top:10px" }, [
          led.rent_minor ? row("Court rent", led.rent_minor, "Charged for the month", "bad") : null,
          led.payouts_minor ? row("Already settled", led.payouts_minor, "Payments recorded this month") : null,
          led.adjustments_minor ? row("Adjustments", led.adjustments_minor, "Manual corrections") : null,
          row("Due now", st.due_now_minor, "After rent and anything already settled", null, true),
        ].filter(Boolean)));
      }

      // The audit line. If the two independent views of the same event disagree, SAY SO on the
      // document — a settlement statement that quietly averages a discrepancy is worse than useless.
      if (st.reconciles === false) {
        settleCard.push(el("div", {
          class: "cf-muted",
          style: "margin-top:10px;padding:8px 10px;border-radius:8px;background:var(--danger-bg,#fee);"
               + "color:var(--danger);font-size:.84rem;font-weight:600",
          text: "⚠ This month's commission entries (" + money(st.ledger_commission_minor)
              + ") don't match the settlement above (" + money(st.net_minor) + "). "
              + "Something posted one without the other — check before paying.",
        }));
      }
      wrap.appendChild(UI.card(settleCard));

      // Running balance with the club — all-time, so it is the figure a payout actually clears.
      wrap.appendChild(UI.card([
        el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center" }, [
          el("div", {}, [
            el("div", { style: "font-weight:700", text: "Running balance with the club" }),
            el("div", { class: "cf-muted", style: "font-size:.82rem",
              text: "All time, across every month — this is what a payout clears." }),
          ]),
          el("div", {
            style: "font-weight:800;font-size:1.05rem;color:"
              + ((led.balance_minor || 0) >= 0 ? "var(--success)" : "var(--danger)"),
            text: money(Math.abs(led.balance_minor || 0))
                + ((led.balance_minor || 0) >= 0 ? " to " + who(name) : " from " + who(name)),
          }),
        ]),
        (!isCoach && cfg.onRecordPayout) ? el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:10px" }, [
          el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Record payout",
            onclick: function () { cfg.onRecordPayout(data, load); } }),
        ]) : null,
      ].filter(Boolean)));

      // ---------------------------------------------------------------- 2. WHERE THE MONEY IS
      wrap.appendChild(UI.card([
        el("h3", { text: "Sessions this month" }),
        el("p", { class: "cf-muted", style: "margin:-4px 0 8px;font-size:.86rem",
          text: "Everything " + who(name) + " taught in " + monthLabel(MONTH)
              + ", by the day it ran. Outstanding sessions are money nobody has collected yet." }),
        el("div", { class: "cf-list" }, [
          row("Paid to the club", tot.to_club_minor, null, "good"),
          row(isCoach ? "Collected by you" : "Collected by the coach", tot.with_coach_minor, null, "warn"),
          row("Outstanding", tot.outstanding_minor, "Not collected by anyone", "bad"),
          tot.not_charged_minor ? row("Not charged", tot.not_charged_minor,
            "Covered by a membership, a pack, or written off") : null,
          row("Total delivered", tot.billed_minor,
              (tot.sessions || 0) + " session" + ((tot.sessions === 1) ? "" : "s"), null, true),
        ].filter(Boolean)),
      ]));

      // ---------------------------------------------------------------- 3. BY CLIENT, BY DAY
      if (!(sessions.clients || []).length) {
        wrap.appendChild(el("div", { class: "cf-empty", text: "No sessions in " + monthLabel(MONTH) + "." }));
      }
      (sessions.clients || []).forEach(function (c) {
        var t = c.totals || {};
        var head = el("div", {
          class: "cf-row",
          style: "justify-content:space-between;align-items:baseline;margin:18px 2px 4px",
        }, [
          el("div", { style: "font-weight:700;font-size:1.02rem", text: c.client_name || "Client" }),
          el("div", { class: "cf-muted", style: "font-size:.84rem",
            text: (t.sessions || 0) + " × · " + money(t.billed_minor)
                + ((t.outstanding_minor || 0) > 0 ? " · " + money(t.outstanding_minor) + " outstanding" : "") }),
        ]);
        wrap.appendChild(head);

        var tbl = el("table", { class: "cf-table" });
        tbl.appendChild(el("thead", {}, [el("tr", {}, ["Day", "Session", "Amount", "Status"].map(
          function (h) { return el("th", { text: h }); }))]));
        var tb = el("tbody");
        (c.rows || []).forEach(function (r) {
          var meta = CUSTODY[r.custody] || CUSTODY.not_charged;
          var chipCls = meta.tone === "good" ? "cf-chip-good"
                      : meta.tone === "bad" ? "cf-chip-bad" : "cf-chip-muted";
          tb.appendChild(el("tr", {}, [
            el("td", { text: dayLabel(r.date) }),
            el("td", { text: r.service || (r.kind === "class" ? "Class" : "Lesson") }),
            el("td", { text: money(r.amount_minor) }),
            el("td", {}, [el("span", { class: "cf-chip " + chipCls, text: r.custody_label || meta.label })]),
          ]));
        });
        tbl.appendChild(tb);
        wrap.appendChild(tbl);
      });

      wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:16px" }, [
        el("button", { class: "cf-btn cf-btn-sm", text: "Print / save as PDF",
          onclick: function () { window.print(); } }),
      ]));

      UI.clear(host);
      host.appendChild(wrap);
    }

    load();
  }

  window.Widgets = window.Widgets || {};
  window.Widgets.CoachStatement = { mount: mount };
})();

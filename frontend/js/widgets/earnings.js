// widgets/earnings.js — Widgets.Earnings: the ONE club-vs-coach earnings P&L, shared by the ADMIN (the
// whole club) and the COACH (their own slice). Same widget, config ONLY — like TransactionDetail /
// ClientRecord. Golden rule: no fork.
//
//   Admin:  CLUB earnings (direct services + commission from coaches) → a COACH (P&L) or a DIRECT service
//           → CLIENT → TRANSACTIONS → the shared record
//   Coach:  their OWN P&L (sales − w/off = net ; net = received + owed ; keep vs club commission)
//           → CLIENT → TRANSACTIONS → the record
//
//   cfg.scope.role   'admin' | 'coach'
//   cfg.title / cfg.month / cfg.back {label,hash}?
//   cfg.data.club(month)                      -> {direct[], coaches[], club{}}                (admin L0)
//   cfg.data.coachPnl(coachUserId|null, month)-> a coach P&L object                            (detail / coach L0)
//   cfg.data.clients({category?, earned_by?, month}) -> {clients[], totals}
//   cfg.data.txns({category?, user_id, earned_by?, month}) -> {transactions[], totals}
//   cfg.onNavigate({kind:'event'|'class'|'txn'|'person', id})
//   cfg.homeExtra(data) -> node?              — L0-only footer (coach disputes)
//   cfg.onRecordPayout(pnl, refresh)          — (admin) record a club↔coach payout from a coach's P&L
//
// The club P&L answers "how much do WE make" = court/membership/pack revenue (100% club) + the commission
// we take from each coach; a coach's row/detail shows their sales split into received (realised commission)
// + owed (projected commission — we always collect). A transaction drills to the SAME shared record.
(function () {
  function mount(host, cfg) {
    var UI = window.UI, CRMUI = window.CRMUI, el = UI.el;
    var role = (cfg.scope && cfg.scope.role) || "admin";
    var isCoach = role === "coach";
    var keepLabel = isCoach ? "You keep" : "Coach keeps";
    var MONTH = cfg.month || null;
    var CUR = "ZAR";
    function money(m) { return UI.money(m || 0, CUR); }

    function monthLabel(ym) { try { var p = String(ym).split("-"); return new Date(p[0], parseInt(p[1], 10) - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" }); } catch (e) { return ym; } }
    function shiftMonth(ym, d) { var p = String(ym).split("-"); var dt = new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1 + d, 1); return dt.getFullYear() + "-" + String(dt.getMonth() + 1).padStart(2, "0"); }
    function loading() { UI.clear(host); host.appendChild(el("div", { class: "cf-loading", style: "min-height:200px", text: "Loading…" })); }
    function fail(e) { UI.clear(host); host.appendChild(el("div", {}, [el("div", { class: "cf-empty", text: UI.errMsg(e) })])); }
    function show(node) { UI.clear(host); host.appendChild(node); }

    function pager(onShift) {
      return el("div", { class: "cf-row", style: "gap:6px;align-items:center" }, [
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "‹", onclick: function () { onShift(-1); } }),
        el("span", { style: "font-weight:600;min-width:104px;text-align:center", text: monthLabel(MONTH || "") }),
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "›", onclick: function () { onShift(1); } }),
      ]);
    }
    function backBtn(label, onBack) { return el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", style: "margin-bottom:8px", text: "‹ " + label, onclick: onBack }); }
    function titleRow(title, onShift) {
      return el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:10px" },
        [el("h1", { style: "margin:0", text: title })].concat(onShift ? [pager(onShift)] : []));
    }

    // A statement line: label (+ optional sub) on the left, a value on the right; tones + a top rule + indent.
    function stmtLine(label, value, o) {
      o = o || {};
      var left = el("div", { style: o.indent ? "padding-left:14px" : "" }, [
        el("span", { style: o.muted ? "color:var(--muted)" : "", text: label }),
        o.sub ? el("span", { class: "cf-muted", style: "font-size:.78rem;margin-left:6px", text: o.sub }) : null,
      ].filter(Boolean));
      var vs = "font-weight:" + (o.strong ? "700" : "600") + ";";
      if (o.tone === "good") vs += "color:var(--success);";
      else if (o.tone === "bad") vs += "color:var(--danger);";
      else if (o.muted) vs += "color:var(--muted);";
      return el("div", { class: "cf-row", style: "justify-content:space-between;align-items:baseline;padding:3px 0;" + (o.border ? "border-top:1px solid var(--border);margin-top:5px;padding-top:8px;" : "") },
        [left, el("span", { style: vs, text: value })]);
    }

    // A tap row: title + sub on the left, a value (+ optional secondary) on the right.
    function tapRow(title, sub, value, value2, onTap) {
      return el("div", { class: "cf-item cf-item-tap", onclick: onTap }, [
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: title }),
          el("div", { class: "cf-item-s", text: sub }),
        ]),
        el("div", { style: "text-align:right;min-width:92px" }, [
          el("div", { style: "font-weight:700", text: value }),
          value2 ? el("div", { style: "font-size:.76rem;color:var(--success);font-weight:600", text: value2 }) : null,
        ].filter(Boolean)),
      ]);
    }

    // THE coach card — ONE statement, top to bottom: sales − disc − w/off = net ; net = received +
    // owed ; commission on each ; then what actually changes hands after rent and anything already
    // paid. It was two cards for a day and that was a mistake: two money blocks that did not add up
    // to each other, and the owner asked which one to believe. One card, one story.
    function pnlCard(p, onRecordPayout) {
      var box = UI.card([]);
      box.appendChild(el("h1", { style: "margin:0 0 2px;font-size:1.2rem", text: p.name || "Coach" }));
      box.appendChild(el("div", { class: "cf-muted", style: "font-size:.82rem;margin-bottom:6px", text: monthLabel(MONTH) + " · " + (p.rate_pct || 0) + "% club commission" }));
      box.appendChild(stmtLine("Total sales", money(p.sales_minor)));
      if (p.discount_minor) box.appendChild(stmtLine("Less discount", "− " + money(p.discount_minor), { muted: true }));
      if (p.written_off_minor) box.appendChild(stmtLine("Less write-off", "− " + money(p.written_off_minor), { muted: true }));
      box.appendChild(stmtLine("Net", money(p.net_minor), { strong: true, border: true }));
      box.appendChild(stmtLine("Received", money(p.received_minor), { border: true }));
      // WHERE that received money is. A coach-collected lesson is settled from the client's side but
      // the cash never reached the club — it is the club's commission that is still owed, by him.
      if (p.banked_minor != null && (p.coach_held_minor || 0) > 0) {
        box.appendChild(stmtLine("in your bank", money(p.banked_minor), { indent: true, tone: "good", sub: "Yoco + EFT" }));
        box.appendChild(stmtLine("held by " + (isCoach ? "you" : "the coach"), money(p.coach_held_minor), { indent: true, tone: "bad", sub: "Collected at the court — never reached the club" }));
      }
      box.appendChild(stmtLine("Club commission", "+ " + money(p.club_comm_received_minor), { indent: true, tone: "good", sub: (p.rate_pct || 0) + "%" }));
      box.appendChild(stmtLine(keepLabel, money(p.coach_keeps_received_minor), { indent: true }));
      box.appendChild(stmtLine("Owed", money(p.owed_minor), { border: true }));
      box.appendChild(stmtLine("Projected commission", "+ " + money(p.club_comm_owed_minor), { indent: true, tone: "good", muted: true, sub: "on collect" }));
      box.appendChild(stmtLine(keepLabel, money(p.coach_keeps_owed_minor), { indent: true, muted: true }));
      box.appendChild(stmtLine(keepLabel + " (total)", money(p.coach_keeps_total_minor), { strong: true, border: true }));
      box.appendChild(stmtLine("Club commission (total)", money(p.club_comm_total_minor), { strong: true, tone: "good" }));

      // THE SETTLEMENT, ON THE SAME CARD. It was a second card below this one, and two money blocks
      // that did not add up to each other is what made the page unreadable — the owner asked which
      // one to believe. Everything above is the month; these last lines are what actually changes
      // hands after rent and anything already paid, which is the only figure anyone acts on.
      var st = p.settlement;
      if (st) {
        if (st.by_kind) {
          var bits = [];
          ["lesson", "class", "pack"].forEach(function (k) {
            var v = st.by_kind[k];
            if (!v) return;
            var amt = (v.club_minor || 0) + (v.coach_minor || 0);
            if (amt) bits.push((v.n ? v.n + " " : "") + k + " " + money(amt));
          });
          if (bits.length) {
            box.appendChild(el("p", { class: "cf-muted cf-tiny", style: "margin:8px 0 0",
              text: "Collected: " + bits.join(" · ")
                  + (st.by_kind.pack ? "  (a pack counts in full at the moment it is sold)" : "") }));
          }
        }
        if (p.ledger && p.ledger.rent_minor) {
          box.appendChild(stmtLine("Less rent", money(p.ledger.rent_minor), { border: true }));
        }
        if (p.ledger && p.ledger.payouts_minor) {
          box.appendChild(stmtLine("Less already paid", money(p.ledger.payouts_minor),
                                   { sub: "credited to this month" }));
        }
        if (st.due_now_minor != null) {
          var due = st.due_now_minor || 0;
          box.appendChild(stmtLine(
            due >= 0 ? (isCoach ? "DUE TO YOU NOW" : "DUE TO THE COACH NOW")
                     : (isCoach ? "YOU OWE THE CLUB" : "OWED BY THE COACH"),
            money(Math.abs(due)), { strong: true, border: true, tone: due >= 0 ? "" : "bad" }));
          box.appendChild(el("p", { class: "cf-muted cf-tiny", style: "margin:4px 0 0", text:
            "Commission is only ever calculated on money already collected, so this rises as "
            + "clients pay and “Owed” falls." }));
        }
        if (st.reconciles === false) {
          box.appendChild(el("div", { class: "cf-note cf-note-warn", style: "margin-top:10px", text:
            "These figures don't tie to the ledger. Don't settle from this screen until it's checked." }));
        }
        if (typeof onRecordPayout === "function") {
          box.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:10px" }, [
            el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Record payout",
                           onclick: function () { onRecordPayout(); } }),
          ]));
        }
      }
      // THE RUNNING BALANCE IS ALL TIME — every other figure on this card is the MONTH. That one
      // unlabelled difference is a five-figure trap: an owner opened July, read "Net balance with
      // the club R23,407 · owed to Allon Rock" directly above a Record-payout button, and R23,407
      // was June+July+August less what had already been paid, while July itself owed R3,682. So the
      // label says "all time", and the payout action moved DOWN to the Settlement card, which is
      // the block that actually computes what to pay for the month being viewed.
      if (p.ledger_balance_minor != null) {
        var bal = p.ledger_balance_minor || 0;
        var sub = isCoach ? (bal > 0 ? "the club owes you" : (bal < 0 ? "you owe the club" : "settled"))
                          : (bal > 0 ? "owed to " + (p.name || "the coach") : (bal < 0 ? "owed by " + (p.name || "the coach") : "settled"));
        box.appendChild(stmtLine("Net balance with the club · ALL TIME", money(Math.abs(bal)),
                                 { strong: true, border: true, sub: sub + " across every month" }));
      }
      return box;
    }


    // THE WORK LOG — sessions by the day they RAN, which is the other question a coach asks and the
    // P&L cannot answer ("what did I teach in July"). Deliberately a different date basis again.
    function sessionsCard(p) {
      var sess = p.sessions, t = (sess && sess.totals) || null;
      if (!t || !t.sessions) return null;
      var box = el("div", { class: "cf-card", style: "margin-top:14px" });
      box.appendChild(el("h3", { text: "Sessions delivered" }));
      box.appendChild(el("p", { class: "cf-muted cf-tiny", style: "margin:0 0 10px",
        text: "Dated by the day they RAN — the work, not the money. This will not equal the "
            + "settlement above, and is not meant to: one is what was taught, the other what was paid." }));
      box.appendChild(stmtLine(t.sessions + " session" + (t.sessions === 1 ? "" : "s"),
                               money(t.billed_minor), { strong: true, border: true }));
      box.appendChild(stmtLine("Paid to the club", money(t.to_club_minor), { indent: true, muted: true }));
      box.appendChild(stmtLine(isCoach ? "With you" : "With the coach",
                               money(t.with_coach_minor), { indent: true, muted: true }));
      box.appendChild(stmtLine("Still outstanding", money(t.outstanding_minor),
                               { indent: true, muted: true }));
      return box;
    }

    // The CLUB earnings card — direct services + commission from coaches → club total & club-vs-coach.
    function clubCard(d) {
      var c = d.club || {};
      var box = UI.card([]);
      box.appendChild(el("div", { class: "cf-muted", style: "font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px", text: "Club earnings · " + monthLabel(MONTH) }));
      box.appendChild(stmtLine("Total club earnings", money(c.earnings_projected_minor), { strong: true, sub: "projected" }));
      box.appendChild(stmtLine("Collected so far", money(c.earnings_collected_minor), { muted: true, sub: "banked" }));
      box.appendChild(stmtLine("Direct services", money(c.direct_net_minor), { border: true, sub: "100% club · " + money(c.direct_received_minor) + " in" }));
      if ((c.coach_held_minor || 0) > 0) {
        box.appendChild(stmtLine("Of what is settled, held by coaches", money(c.coach_held_minor), {
          tone: "bad", sub: "Collected at the court — the club's commission on it is still owed to you" }));
      }
      box.appendChild(stmtLine("Commission from coaches", money((c.commission_received_minor || 0) + (c.commission_owed_minor || 0)), { sub: money(c.commission_received_minor) + " in · " + money(c.commission_owed_minor) + " owed" }));
      box.appendChild(stmtLine("Club keeps", money(c.earnings_projected_minor), { strong: true, border: true, tone: "good" }));
      box.appendChild(stmtLine("Coaches keep", money(c.coaches_keep_projected_minor), { strong: true }));
      return box;
    }

    // ── L0 (admin) · CLUB ──────────────────────────────────────────────────────
    function renderClub() {
      loading();
      Promise.resolve(cfg.data.club(MONTH)).then(function (d) {
        MONTH = d.month || MONTH; CUR = d.currency || CUR;
        var wrap = el("div", {});
        if (cfg.back) wrap.appendChild(UI.backBar(cfg.back.label || "Back", cfg.back.hash));
        wrap.appendChild(titleRow(cfg.title || "Club earnings", function (n) { MONTH = shiftMonth(MONTH, n); renderClub(); }));
        wrap.appendChild(clubCard(d));

        var coaches = d.coaches || [], direct = (d.direct || []).filter(function (x) { return (x.billed_minor || 0) > 0; });
        var cc = UI.card([CRMUI.sectionHead("Coaches" + (coaches.length ? " · " + coaches.length : ""))]);
        if (!coaches.length) cc.appendChild(el("div", { class: "cf-empty", text: "No coach revenue this month." }));
        else { var cl = el("div", { class: "cf-list" }); coaches.forEach(function (p) { var held = p.coach_held_minor || 0;
          // "in" used to mean "the client settled" — which for a coach who collects at the court is
          // the CLUB's money in HIS pocket. Only Yoco + EFT reaches the club, so say which is which.
          var sub = money(p.banked_minor != null ? p.banked_minor : p.received_minor) + " in your bank"
                  + (held ? " · " + money(held) + " held by them" : "")
                  + " · " + money(p.owed_minor) + " owed by clients";
          cl.appendChild(tapRow(p.name, sub, money(p.net_minor), money(p.club_comm_total_minor) + " club", function () { renderCoach(p.coach_user_id, false); })); }); cc.appendChild(cl); }
        wrap.appendChild(cc);

        if (direct.length) {
          var dc = UI.card([CRMUI.sectionHead("Direct services (100% club)")]);
          var dl = el("div", { class: "cf-list" });
          direct.forEach(function (x) { dl.appendChild(tapRow(x.label, money(x.paid_minor) + " in" + ((x.outstanding_minor || 0) > 0 ? " · " + money(x.outstanding_minor) + " owed" : ""), money(x.invoiced_minor), null, function () { renderDirect(x); })); });
          dc.appendChild(dl); wrap.appendChild(dc);
        }

        if (typeof cfg.homeExtra === "function") { try { var extra = cfg.homeExtra(d); if (extra) wrap.appendChild(extra); } catch (e) {} }
        show(wrap);
      }, fail);
    }

    // ── COACH P&L ── admin detail (from a coach row) OR the coach app's own L0 landing ───────────────
    function renderCoach(coachId, isL0) {
      loading();
      Promise.resolve(cfg.data.coachPnl(coachId, MONTH)).then(function (p) {
        MONTH = p.month || MONTH; CUR = p.currency || CUR;
        var wrap = el("div", {});
        if (isL0) wrap.appendChild(titleRow(cfg.title || "Money", function (n) { MONTH = shiftMonth(MONTH, n); renderCoach(coachId, true); }));
        else wrap.appendChild(backBtn(cfg.title || "Club earnings", renderClub));
        var payoutFn = (!isCoach && typeof cfg.onRecordPayout === "function")
          ? function () { cfg.onRecordPayout(p, function () { renderCoach(coachId, isL0); }); }
          : null;
        wrap.appendChild(pnlCard(p, payoutFn));
        // ONE card for the money (above) + the work log. The settlement used to be a second money
        // card and two blocks that didn't add up to each other is what made the page unreadable.
        var sc = sessionsCard(p); if (sc) wrap.appendChild(sc);
        // By client (the coach's clients this month) → transactions.
        var q = { month: MONTH };
        if (!isCoach && p.coach_user_id) q.earned_by = p.coach_user_id;   // admin: filter to this coach
        Promise.resolve(cfg.data.clients(q)).then(function (cd) {
          var clients = cd.clients || [];
          var cc = UI.card([CRMUI.sectionHead("By client" + (clients.length ? " · " + clients.length : ""))]);
          if (!clients.length) cc.appendChild(el("div", { class: "cf-empty", text: "No clients this month." }));
          else { var cl = el("div", { class: "cf-list" }); clients.forEach(function (x) { cl.appendChild(clientRow(x, { earned_by: q.earned_by, backLabel: p.name, onBack: function () { renderCoach(coachId, isL0); } })); }); cc.appendChild(cl); }
          wrap.appendChild(cc);
          if (isL0 && typeof cfg.homeExtra === "function") { try { var extra = cfg.homeExtra(p); if (extra) wrap.appendChild(extra); } catch (e) {} }
          show(wrap);
        }, function () { show(wrap); });
      }, fail);
    }

    // ── DIRECT SERVICE (admin) · a club-run service → its clients ───────────────
    function renderDirect(svc) {
      loading();
      Promise.resolve(cfg.data.clients({ category: svc.key, earned_by: "club", month: MONTH })).then(function (cd) {
        CUR = cd.currency || CUR;
        var wrap = el("div", {});
        wrap.appendChild(backBtn(cfg.title || "Club earnings", renderClub));
        wrap.appendChild(el("h1", { style: "margin:0 0 2px;font-size:1.2rem", text: svc.label }));
        wrap.appendChild(el("div", { class: "cf-muted", style: "margin:0 0 10px;font-size:.85rem", text: monthLabel(MONTH) + " · 100% club · " + totalsLine(cd.totals) }));
        var c = UI.card([]), l = el("div", { class: "cf-list" });
        var clients = cd.clients || [];
        if (!clients.length) l.appendChild(el("div", { class: "cf-empty", text: "No clients this month." }));
        clients.forEach(function (x) { l.appendChild(clientRow(x, { category: svc.key, earned_by: "club", backLabel: svc.label, onBack: function () { renderDirect(svc); } })); });
        c.appendChild(l); wrap.appendChild(c);
        show(wrap);
      }, fail);
    }

    function totalsLine(t) { t = t || {}; return money(t.billed_minor) + " billed · " + money(t.paid_minor) + " paid · " + money(t.outstanding_minor) + " owed"; }
    function clientRow(x, ctx) {
      var owed = x.outstanding_minor || 0;
      return tapRow(x.name, money(x.paid_minor) + " paid" + (owed > 0 ? " · " + money(owed) + " owed" : ""),
        money(x.invoiced_minor), owed > 0 ? money(owed) + " owed" : null,
        function () { renderTxns(x, ctx); });
    }

    // ── TRANSACTIONS ── the leaf → the shared record ────────────────────────────
    function renderTxns(client, ctx) {
      ctx = ctx || {};
      loading();
      var q = { user_id: client.user_id, month: MONTH };
      if (ctx.category) q.category = ctx.category;
      if (ctx.earned_by) q.earned_by = ctx.earned_by;
      Promise.resolve(cfg.data.txns(q)).then(function (d) {
        CUR = d.currency || CUR;
        var wrap = el("div", {});
        wrap.appendChild(backBtn(ctx.backLabel || client.name, ctx.onBack || renderClub));
        wrap.appendChild(el("h1", { style: "margin:0 0 2px;font-size:1.2rem", text: client.name }));
        wrap.appendChild(el("div", { class: "cf-muted", style: "font-size:.85rem", text: monthLabel(MONTH) + " · " + totalsLine(d.totals) }));
        wrap.appendChild(el("p", { class: "cf-muted", style: "margin:4px 0 10px;font-size:.82rem", text: "Tap a transaction to open its record — pay, discount, void or refund. Get these right before month-end." }));
        var c = UI.card([]), l = el("div", { class: "cf-list" });
        var txns = d.transactions || [];
        if (!txns.length) l.appendChild(el("div", { class: "cf-empty", text: "No transactions." }));
        txns.forEach(function (x) {
          var chip = { paid: "confirmed", owed: "held" }[x.state] || "";
          l.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { drillTxn(x); } }, [
            el("span", { class: "cf-chip " + (x.category || ""), text: x.label }),
            el("div", { class: "cf-item-main" }, [
              el("div", { class: "cf-item-t", text: x.client_name }),
              el("div", { class: "cf-item-s", text: (x.at ? UI.fmtDate(x.at) : "") + (x.description ? " · " + x.description : "") }),
            ]),
            el("div", { style: "text-align:right" }, [
              el("div", { style: "font-weight:700", text: money(x.billed_minor) }),
              el("span", { class: "cf-chip " + chip, style: "font-size:.7rem", text: x.state }),
            ]),
          ]));
        });
        c.appendChild(l); wrap.appendChild(c);
        show(wrap);
      }, fail);
    }
    function drillTxn(x) {
      if (!cfg.onNavigate) return;
      if (x.booking_id) cfg.onNavigate({ kind: "event", id: x.booking_id });
      else if (x.enrolment_id) cfg.onNavigate({ kind: "class", id: x.enrolment_id });
      else if (x.order_id) cfg.onNavigate({ kind: "txn", id: x.order_id });
    }

    if (isCoach) renderCoach(null, true);
    else renderClub();
    return { refresh: function () { if (isCoach) renderCoach(null, true); else renderClub(); } };
  }

  window.Widgets = window.Widgets || {};
  window.Widgets.Earnings = { mount: mount };
})();

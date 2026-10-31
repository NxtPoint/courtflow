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

    // THE coach card — the owner's formula, in his order, every line feeding the next:
    //   carried over from last month + billed and received in the bank (card + EFT)
    //   - commission - fines = PAY TO COACH / OWED BY COACH
    //   then invoices outstanding - future commission, for the TOTAL AMOUNT PAYABLE.
    // The month is the month the work was done. Commission on cash the coach took at the court comes
    // out of the same balance, so it gets its own line, shown only when there is any: folded into
    // the commission line, the percentage printed beside it would not match the figure. Rent and
    // payouts likewise appear only when they exist, and must stay: they change what is payable.
    function pnlCard(p, onRecordPayout) {
      var box = UI.card([]);
      var who = isCoach ? "you" : "the coach";
      var st = p.settlement, L = p.ledger || {};
      var pct = (st && st.effective_pct != null) ? st.effective_pct : (p.rate_pct || 0);
      box.appendChild(el("h1", { style: "margin:0 0 2px;font-size:1.2rem", text: p.name || "Coach" }));
      box.appendChild(el("div", { class: "cf-muted", style: "font-size:.82rem;margin-bottom:6px", text: monthLabel(MONTH) + " · " + (p.rate_pct || 0) + "% club commission" }));
      if (!st) {   // no settlement in the payload — say what we can rather than nothing
        box.appendChild(stmtLine("Total sales", money(p.sales_minor)));
        box.appendChild(stmtLine("Received", money(p.received_minor)));
        box.appendChild(stmtLine("Owed by clients", money(p.owed_minor)));
        return box;
      }
      function signed(v) { return (v < 0 ? "− " : "+ ") + money(Math.abs(v)); }
      function owedWord(v) { return v > 0 ? "owed to " + who : (v < 0 ? "owed by " + who : "nothing carried over"); }
      function payLabel(v, total) {
        if (isCoach) return v < 0 ? (total ? "TOTAL YOU OWE THE CLUB" : "YOU OWE THE CLUB") : (total ? "TOTAL AMOUNT PAYABLE TO YOU" : "PAY TO YOU");
        return v < 0 ? (total ? "TOTAL OWED BY COACH" : "OWED BY COACH") : (total ? "TOTAL AMOUNT PAYABLE" : "PAY TO COACH");
      }
      var kinds = [];
      ["lesson", "class", "pack"].forEach(function (k) {
        var amt = ((st.by_kind || {})[k] || {}).club_minor;
        if (amt) kinds.push(k + (k === "class" ? "es " : "s ") + money(amt));
      });

      // 1) CARRIED OVER. Omitted (never shown as zero) when the server could not work it out.
      var hasBal = st.opening_minor != null && st.closing_minor != null;
      if (hasBal) {
        box.appendChild(stmtLine("Carried over from last month", (st.opening_minor < 0 ? "− " : "") + money(Math.abs(st.opening_minor)),
                                 { border: true, sub: owedWord(st.opening_minor) }));
      }

      // 2) WHAT REACHED THE BANK for this month's work.
      box.appendChild(stmtLine("Billed and received in the bank", "+ " + money(st.club_held_minor), { strong: true, border: !hasBal, sub: "card + EFT" }));
      if (kinds.length) {
        box.appendChild(el("p", { class: "cf-muted cf-tiny", style: "margin:0 0 4px",
          text: kinds.join(" · ") + (((st.by_kind || {}).pack || {}).club_minor ? " — a pack counts in full when it is sold" : "") }));
      }

      // 3) WHAT COMES OFF IT. A line that does not apply to this coach is not shown at all.
      box.appendChild(stmtLine("Club commission", "− " + money(st.commission_bank_minor), { sub: pct + "% of the money in the bank" }));
      if (st.commission_coach_held_minor) {
        box.appendChild(stmtLine("Commission on money collected at the court", signed(-st.commission_coach_held_minor),
                                 { sub: money(st.coach_held_minor) + " went straight to " + who }));
      }
      if (L.adjustments_minor) box.appendChild(stmtLine("Fines and adjustments", signed(L.adjustments_minor)));
      if (L.rent_minor) box.appendChild(stmtLine("Rent", signed(L.rent_minor)));
      if (L.payouts_minor) box.appendChild(stmtLine(L.payouts_minor < 0 ? "Already paid out" : "Already paid in", signed(L.payouts_minor), { sub: "for this month" }));

      // 4) THE AMOUNT TO PAY.
      var pay = hasBal ? st.closing_minor : (st.due_now_minor || 0);
      box.appendChild(stmtLine(payLabel(pay, false), money(Math.abs(pay)), {
        strong: true, border: true, tone: pay < 0 ? "bad" : "", sub: pay === 0 ? "settled" : null }));
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

      // 5) STILL TO COME. Nothing here is due until the client pays, so it sits below the payout.
      if (st.total_payable_minor == null) {
        box.appendChild(el("p", { class: "cf-muted cf-tiny", style: "margin:8px 0 0", text:
          "Outstanding invoices could not be worked out, so no total is shown." }));
      } else if (st.outstanding_total_minor) {
        // A coach who invoices his own clients receives this money himself, so it never joins the
        // total: only the club's commission on it is still to come.
        var direct = !!st.coach_collects_invoices, future = st.outstanding_total_commission_minor;
        box.appendChild(stmtLine("Invoices outstanding", money(st.outstanding_total_minor), { border: true,
          sub: direct ? "clients pay " + who + " directly, so this is not added" : "not yet paid by clients, this month and earlier" }));
        if (future || !direct) {
          box.appendChild(stmtLine(direct ? "Commission still to come" : "Less future commission", "− " + money(future),
                                   { sub: pct + "%" + (direct ? " of those invoices" : "") }));
          box.appendChild(stmtLine(payLabel(st.total_payable_minor, true), money(Math.abs(st.total_payable_minor)), {
            strong: true, border: true, tone: st.total_payable_minor < 0 ? "bad" : "",
            sub: "once every client has paid" }));
        }
      }

      // (Only when the running balance could not be worked out.)
      if (!hasBal && p.ledger_balance_minor != null) {
        var bal = p.ledger_balance_minor || 0;
        box.appendChild(stmtLine("All months together", money(Math.abs(bal)),
                                 { strong: true, border: true, sub: owedWord(bal) }));
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
        text: "Every lesson and class that ran this month. A session drawn from a pack shows no charge "
            + "here — the pack was counted above when it was sold." }));
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
        else { var cl = el("div", { class: "cf-list" }); coaches.forEach(function (p) {
          // The same figures as the card it opens: read from the settlement, never worked out twice.
          var st = p.settlement || null, open = function () { renderCoach(p.coach_user_id, false); };
          if (!st) { cl.appendChild(tapRow(p.name, "Tap to see this coach", money(p.net_minor), null, open)); return; }
          var pay = st.closing_minor != null ? st.closing_minor : (st.due_now_minor || 0);
          var sub = money(st.club_held_minor) + " in the bank · " + money(st.commission_minor) + " commission"
                  + (st.outstanding_total_minor ? " · " + money(st.outstanding_total_minor) + " invoices outstanding" : "");
          cl.appendChild(tapRow(p.name, sub, money(Math.abs(pay)), pay < 0 ? "owed by coach" : "pay to coach", open)); }); cc.appendChild(cl); }
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
              el("div", { class: "cf-item-s", text: (x.at ? UI.fmtDate(x.at) : "") + (x.description ? " · " + x.description : "")
                + (x.shared_head ? " · shared lesson" + (x.shared_with ? " with " + x.shared_with : "") : "") }),
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
      // A second player's OWN bill on a shared lesson opens ITS record. The booking's record shows
      // only the booker's charge — so an unpaid head opened onto somebody else's "paid".
      if (x.shared_head && x.order_id) cfg.onNavigate({ kind: "txn", id: x.order_id });
      else if (x.booking_id) cfg.onNavigate({ kind: "event", id: x.booking_id });
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

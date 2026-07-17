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

    // The coach P&L card — sales − disc − w/off = net ; net = received + owed ; commission split on each.
    function pnlCard(p) {
      var box = UI.card([]);
      box.appendChild(el("h1", { style: "margin:0 0 2px;font-size:1.2rem", text: p.name || "Coach" }));
      box.appendChild(el("div", { class: "cf-muted", style: "font-size:.82rem;margin-bottom:6px", text: monthLabel(MONTH) + " · " + (p.rate_pct || 0) + "% club commission" }));
      box.appendChild(stmtLine("Total sales", money(p.sales_minor)));
      if (p.discount_minor) box.appendChild(stmtLine("Less discount", "− " + money(p.discount_minor), { muted: true }));
      if (p.written_off_minor) box.appendChild(stmtLine("Less write-off", "− " + money(p.written_off_minor), { muted: true }));
      box.appendChild(stmtLine("Net", money(p.net_minor), { strong: true, border: true }));
      box.appendChild(stmtLine("Received", money(p.received_minor), { border: true }));
      box.appendChild(stmtLine("Club commission", "+ " + money(p.club_comm_received_minor), { indent: true, tone: "good", sub: (p.rate_pct || 0) + "%" }));
      box.appendChild(stmtLine(keepLabel, money(p.coach_keeps_received_minor), { indent: true }));
      box.appendChild(stmtLine("Owed", money(p.owed_minor), { border: true }));
      box.appendChild(stmtLine("Projected commission", "+ " + money(p.club_comm_owed_minor), { indent: true, tone: "good", muted: true, sub: "on collect" }));
      box.appendChild(stmtLine(keepLabel, money(p.coach_keeps_owed_minor), { indent: true, muted: true }));
      box.appendChild(stmtLine(keepLabel + " (total)", money(p.coach_keeps_total_minor), { strong: true, border: true }));
      box.appendChild(stmtLine("Club commission (total)", money(p.club_comm_total_minor), { strong: true, tone: "good" }));
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
        else { var cl = el("div", { class: "cf-list" }); coaches.forEach(function (p) { cl.appendChild(tapRow(p.name, money(p.received_minor) + " in · " + money(p.owed_minor) + " owed", money(p.net_minor), money(p.club_comm_total_minor) + " club", function () { renderCoach(p.coach_user_id, false); })); }); cc.appendChild(cl); }
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
        wrap.appendChild(pnlCard(p));
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

// widgets/earnings.js — Widgets.Earnings: the ONE "how am I earning" view, shared by the ADMIN (the
// aggregate of the whole club) and the COACH (their own slice). Same widget, config ONLY — like
// TransactionDetail / ClientRecord. Golden rule: no fork.
//
//   cfg.scope.role   'admin' | 'coach'  — picks the money-band extras + labels
//   cfg.title        heading (default "Earnings" / "My earnings")
//   cfg.month        initial 'YYYY-MM' (the widget owns the pager thereafter)
//   cfg.data.get(month)                       -> {month, currency, summary, services[], clients[]}
//   cfg.data.txns({category?, user_id?, month}) -> {transactions[], totals, label, ...}
//   cfg.onNavigate({kind:'event'|'class'|'txn'|'person', id})  — drill to the SHARED record / person
//   cfg.homeExtra(data) -> node?              — app-specific footer on the home view (admin actions /
//                                               coach disputes); appended after the by-client section
//
// The money band IS CRMUI.statementFold; a transaction drills to the SAME Widgets.TransactionDetail
// record the coach + admin already share. Admin is literally the coach's view, aggregated.
(function () {
  function mount(host, cfg) {
    var UI = window.UI, CRMUI = window.CRMUI, el = UI.el;
    function money(m, c) { return UI.money(m || 0, c || "ZAR"); }
    var role = (cfg.scope && cfg.scope.role) || "admin";
    var MONTH = cfg.month || null;

    function monthLabel(ym) { try { var p = String(ym).split("-"); return new Date(p[0], parseInt(p[1], 10) - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" }); } catch (e) { return ym; } }
    function shiftMonth(ym, d) { var p = String(ym).split("-"); var dt = new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1 + d, 1); return dt.getFullYear() + "-" + String(dt.getMonth() + 1).padStart(2, "0"); }
    function loading() { UI.clear(host); host.appendChild(el("div", { class: "cf-loading", style: "min-height:200px", text: "Loading…" })); }
    function fail(e) { UI.clear(host); host.appendChild(el("div", {}, [el("div", { class: "cf-empty", text: UI.errMsg(e) })])); }

    function pager(onShift) {
      return el("div", { class: "cf-row", style: "gap:6px;align-items:center" }, [
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "‹", onclick: function () { onShift(-1); } }),
        el("span", { style: "font-weight:600;min-width:104px;text-align:center", text: monthLabel(MONTH || "") }),
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "›", onclick: function () { onShift(1); } }),
      ]);
    }

    // The money-band extras below the fold — the ONLY scope difference.
    function bandExtras(s) {
      if (role === "coach") {
        var bal = s.balance_minor || 0;
        return [
          { label: "You keep", sub: "on paid", value_minor: s.coach_keeps_minor, tone: "good" },
          { label: "Club commission", sub: "on paid", value_minor: s.commission_minor },
          { label: "Net balance with club", value_minor: bal, tone: bal < 0 ? "bad" : "good", sub: bal > 0 ? "club owes you" : (bal < 0 ? "you owe the club" : "settled") },
        ];
      }
      var owed = s.total_owed_now_minor || 0, payouts = s.coach_payouts_due_minor || 0;
      var ex = [{ label: "Club keeps", sub: "est. after coach pay", value_minor: s.club_keeps_minor, tone: "good" }];
      if (payouts > 0) ex.push({ label: "Coach payouts due", sub: "to coaches now", value_minor: payouts });
      ex.push({ label: "Owed to the club", sub: "all unpaid, now", value_minor: owed, tone: owed > 0 ? "bad" : undefined });
      return ex;
    }

    // A service / client fold row: label + net (invoiced) + a paid/owed line, tap to drill to its transactions.
    function foldRow(label, x, cur, onTap) {
      var owed = x.outstanding_minor || 0;
      var bits = [money(x.paid_minor, cur) + " paid"];
      if (owed > 0) bits.push(money(owed, cur) + " owed");
      if (x.discount_minor) bits.push(money(x.discount_minor, cur) + " disc.");
      if (x.written_off_minor) bits.push(money(x.written_off_minor, cur) + " w/off");
      return el("div", { class: "cf-item cf-item-tap", onclick: onTap }, [
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: label }),
          el("div", { class: "cf-item-s", text: bits.join(" · ") }),
        ]),
        el("div", { style: "text-align:right;min-width:92px" }, [
          el("div", { style: "font-weight:700", text: money(x.invoiced_minor, cur) }),
          owed > 0 ? el("div", { style: "font-size:.76rem;color:var(--danger);font-weight:600", text: money(owed, cur) + " owed" }) : null,
        ].filter(Boolean)),
      ]);
    }

    // HOME — money band + by-service + by-client (both drill to transactions).
    function renderHome() {
      loading();
      Promise.resolve(cfg.data.get(MONTH)).then(function (d) {
        MONTH = d.month || MONTH;
        var cur = d.currency || "ZAR", s = d.summary || {};
        var wrap = el("div", {});
        if (cfg.back) wrap.appendChild(UI.backBar(cfg.back.label || "Back", cfg.back.hash));
        wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:10px" }, [
          el("h1", { style: "margin:0", text: cfg.title || (role === "coach" ? "My earnings" : "Earnings") }),
          pager(function (n) { MONTH = shiftMonth(MONTH, n); renderHome(); }),
        ]));
        wrap.appendChild(UI.card([CRMUI.statementFold({ currency: cur, month: MONTH, totals: s, extra: bandExtras(s) })]));

        var svc = (d.services || []).filter(function (x) { return (x.billed_minor || 0) > 0; });
        var scard = UI.card([CRMUI.sectionHead("By service")]);
        if (!svc.length) scard.appendChild(el("div", { class: "cf-empty", text: "No earnings in " + monthLabel(MONTH) + "." }));
        else { var sl = el("div", { class: "cf-list" }); svc.forEach(function (x) { sl.appendChild(foldRow(x.label, x, cur, function () { openTxns({ category: x.key, title: x.label }); })); }); scard.appendChild(sl); }
        wrap.appendChild(scard);

        var cls = (d.clients || []).filter(function (x) { return (x.billed_minor || 0) > 0; });
        var ccard = UI.card([CRMUI.sectionHead("By client" + (cls.length ? " · " + cls.length : ""))]);
        if (!cls.length) ccard.appendChild(el("div", { class: "cf-empty", text: "No clients this month." }));
        else { var cl = el("div", { class: "cf-list" }); cls.forEach(function (x) { cl.appendChild(foldRow(x.name, x, cur, function () { openTxns({ user_id: x.user_id, title: x.name }); })); }); ccard.appendChild(cl); }
        wrap.appendChild(ccard);

        if (typeof cfg.homeExtra === "function") { try { var extra = cfg.homeExtra(d); if (extra) wrap.appendChild(extra); } catch (e) {} }
        UI.clear(host); host.appendChild(wrap);
      }, fail);
    }

    // DRILL — the transactions behind a service or a client, each opening the shared record.
    function openTxns(opts) {
      loading();
      Promise.resolve(cfg.data.txns({ category: opts.category, user_id: opts.user_id, month: MONTH })).then(function (d) {
        var cur = d.currency || "ZAR", txns = d.transactions || [], tot = d.totals || {};
        var wrap = el("div", {});
        wrap.appendChild(el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", style: "margin-bottom:8px", text: "‹ Back to earnings", onclick: renderHome }));
        wrap.appendChild(el("h1", { style: "margin:0 0 2px;font-size:1.2rem", text: opts.title || d.label || "Transactions" }));
        wrap.appendChild(el("div", { class: "cf-muted", style: "margin-bottom:8px;font-size:.85rem", text: monthLabel(MONTH) + " · " + money(tot.billed_minor, cur) + " billed · " + money(tot.paid_minor, cur) + " paid · " + money(tot.outstanding_minor, cur) + " owed" }));
        wrap.appendChild(el("p", { class: "cf-muted", style: "margin:-2px 0 10px;font-size:.82rem", text: "Tap a transaction to open its record — pay, discount, void or refund. Get these right before month-end." }));
        var c = UI.card([]), l = el("div", { class: "cf-list" });
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
              el("div", { style: "font-weight:700", text: money(x.billed_minor, cur) }),
              el("span", { class: "cf-chip " + chip, style: "font-size:.7rem", text: x.state }),
            ]),
          ]));
        });
        c.appendChild(l); wrap.appendChild(c);
        UI.clear(host); host.appendChild(wrap);
      }, fail);
    }

    function drillTxn(x) {
      if (!cfg.onNavigate) return;
      if (x.booking_id) cfg.onNavigate({ kind: "event", id: x.booking_id });
      else if (x.enrolment_id) cfg.onNavigate({ kind: "class", id: x.enrolment_id });
      else if (x.order_id) cfg.onNavigate({ kind: "txn", id: x.order_id });
    }

    renderHome();
    return { refresh: renderHome };
  }

  window.Widgets = window.Widgets || {};
  window.Widgets.Earnings = { mount: mount };
})();

// widgets/earnings.js — Widgets.Earnings: the ONE "how am I earning" view, a NESTED revenue drill shared
// by the ADMIN (the aggregate of the whole club) and the COACH (their own slice). Same widget, config
// ONLY — like TransactionDetail / ClientRecord. Golden rule: no fork.
//
//   Admin drill:  Revenue per service → SERVICE → by COACH / Club → CLIENT → TRANSACTIONS → the record
//   Coach drill:  (their slice)       → SERVICE → CLIENT → TRANSACTIONS → the record   (skips by-coach —
//                                                                                       they ARE the coach)
//
//   cfg.scope.role   'admin' | 'coach'  — picks the money-band extras + whether the by-coach level shows
//   cfg.title        L0 heading (default "Revenue per service" / "My earnings")
//   cfg.month        initial 'YYYY-MM' (the widget owns the pager thereafter)
//   cfg.back         {label, hash}?  — a back link on the top (services) level (admin: back to Money menu)
//   cfg.data.service(month)                          -> {month,currency,summary,services[]}
//   cfg.data.coaches({category, month})              -> {coaches[], totals, label}          (admin only)
//   cfg.data.clients({category, earned_by?, month})  -> {clients[], totals, label}
//   cfg.data.txns({category, user_id, earned_by?, month}) -> {transactions[], totals}
//   cfg.onNavigate({kind:'event'|'class'|'txn'|'person', id})  — drill a transaction to the SHARED record
//   cfg.homeExtra(serviceData) -> node?              — L0-only footer (coach disputes queue)
//
// The money band IS CRMUI.statementFold; a transaction drills to the SAME Widgets.TransactionDetail the
// coach + admin already share. Admin is literally the coach's view, aggregated — plus one extra level.
(function () {
  function mount(host, cfg) {
    var UI = window.UI, CRMUI = window.CRMUI, el = UI.el;
    function money(m, c) { return UI.money(m || 0, c || "ZAR"); }
    var role = (cfg.scope && cfg.scope.role) || "admin";
    var isCoach = role === "coach";
    var MONTH = cfg.month || null;
    var CUR = "ZAR";

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

    // The money-band extras below the fold — the ONLY summary-level scope difference.
    function bandExtras(s) {
      if (isCoach) {
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

    // A drill row: label + net (invoiced) + a paid/owed line, tap to go a level deeper.
    function foldRow(label, x, onTap) {
      var owed = x.outstanding_minor || 0;
      var bits = [money(x.paid_minor, CUR) + " paid"];
      if (owed > 0) bits.push(money(owed, CUR) + " owed");
      if (x.discount_minor) bits.push(money(x.discount_minor, CUR) + " disc.");
      if (x.written_off_minor) bits.push(money(x.written_off_minor, CUR) + " w/off");
      return el("div", { class: "cf-item cf-item-tap", onclick: onTap }, [
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: label }),
          el("div", { class: "cf-item-s", text: bits.join(" · ") }),
        ]),
        el("div", { style: "text-align:right;min-width:92px" }, [
          el("div", { style: "font-weight:700", text: money(x.invoiced_minor, CUR) }),
          owed > 0 ? el("div", { style: "font-size:.76rem;color:var(--danger);font-weight:600", text: money(owed, CUR) + " owed" }) : null,
        ].filter(Boolean)),
      ]);
    }

    function backBtn(label, onBack) {
      return el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", style: "margin-bottom:8px", text: "‹ " + label, onclick: onBack });
    }
    function totalsLine(t) {
      t = t || {};
      return money(t.billed_minor, CUR) + " billed · " + money(t.paid_minor, CUR) + " paid · " + money(t.outstanding_minor, CUR) + " owed";
    }
    // A deeper-level screen: back + crumb title + a totals line + the rows (or an empty note).
    function drillScreen(opts) {
      var wrap = el("div", {});
      wrap.appendChild(backBtn(opts.backLabel, opts.onBack));
      wrap.appendChild(el("h1", { style: "margin:0 0 2px;font-size:1.2rem", text: opts.title }));
      if (opts.crumb) wrap.appendChild(el("div", { class: "cf-muted", style: "font-size:.82rem", text: opts.crumb }));
      wrap.appendChild(el("div", { class: "cf-muted", style: "margin:4px 0 10px;font-size:.85rem", text: monthLabel(MONTH) + " · " + totalsLine(opts.totals) }));
      if (opts.note) wrap.appendChild(el("p", { class: "cf-muted", style: "margin:-2px 0 10px;font-size:.82rem", text: opts.note }));
      var c = UI.card([]), l = el("div", { class: "cf-list" });
      if (!opts.rows.length) l.appendChild(el("div", { class: "cf-empty", text: opts.empty || "Nothing here." }));
      opts.rows.forEach(function (r) { l.appendChild(r); });
      c.appendChild(l); wrap.appendChild(c);
      return wrap;
    }

    // ── L0 · SERVICES ─────────────────────────────────────────────────────────
    function renderServices() {
      loading();
      Promise.resolve(cfg.data.service(MONTH)).then(function (d) {
        MONTH = d.month || MONTH; CUR = d.currency || CUR;
        var s = d.summary || {};
        var wrap = el("div", {});
        if (cfg.back) wrap.appendChild(UI.backBar(cfg.back.label || "Back", cfg.back.hash));
        wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:10px" }, [
          el("h1", { style: "margin:0", text: cfg.title || (isCoach ? "My earnings" : "Revenue per service") }),
          pager(function (n) { MONTH = shiftMonth(MONTH, n); renderServices(); }),
        ]));
        wrap.appendChild(UI.card([CRMUI.statementFold({ currency: CUR, month: MONTH, totals: s, extra: bandExtras(s) })]));

        var svc = (d.services || []).filter(function (x) { return (x.billed_minor || 0) > 0; });
        var scard = UI.card([CRMUI.sectionHead("By service")]);
        if (!svc.length) scard.appendChild(el("div", { class: "cf-empty", text: "No earnings in " + monthLabel(MONTH) + "." }));
        else { var sl = el("div", { class: "cf-list" }); svc.forEach(function (x) { sl.appendChild(foldRow(x.label, x, function () { onService(x); })); }); scard.appendChild(sl); }
        wrap.appendChild(scard);

        if (typeof cfg.homeExtra === "function") { try { var extra = cfg.homeExtra(d); if (extra) wrap.appendChild(extra); } catch (e) {} }
        show(wrap);
      }, fail);
    }
    // A service tap: admin → by coach/club; coach → straight to their clients (they're the only coach).
    function onService(svc) { if (isCoach) renderClients(svc, null); else renderCoaches(svc); }

    // ── L1 (admin) · BY COACH / CLUB ──────────────────────────────────────────
    function renderCoaches(svc) {
      loading();
      Promise.resolve(cfg.data.coaches({ category: svc.key, month: MONTH })).then(function (d) {
        CUR = d.currency || CUR;
        var rows = (d.coaches || []).map(function (co) {
          return foldRow(co.name, co, function () { renderClients(svc, { earned_by: co.is_club ? "club" : co.coach_user_id, name: co.name }); });
        });
        show(drillScreen({
          backLabel: "Revenue per service", onBack: renderServices,
          title: svc.label, crumb: "Revenue by coach / club",
          totals: d.totals, rows: rows, empty: "No revenue in " + svc.label + " this month.",
        }));
      }, fail);
    }

    // ── L2 (admin) / L1 (coach) · BY CLIENT ───────────────────────────────────
    function renderClients(svc, coachSel) {
      loading();
      var q = { category: svc.key, month: MONTH };
      if (coachSel) q.earned_by = coachSel.earned_by;
      Promise.resolve(cfg.data.clients(q)).then(function (d) {
        CUR = d.currency || CUR;
        var rows = (d.clients || []).map(function (cl) {
          return foldRow(cl.name, cl, function () { renderTxns(svc, coachSel, cl); });
        });
        show(drillScreen({
          backLabel: coachSel ? coachSel.name : "Revenue per service",
          onBack: function () { if (coachSel) renderCoaches(svc); else renderServices(); },
          title: coachSel ? (svc.label + " · " + coachSel.name) : svc.label,
          crumb: "By client",
          totals: d.totals, rows: rows, empty: "No clients this month.",
        }));
      }, fail);
    }

    // ── L3 · TRANSACTIONS ─────────────────────────────────────────────────────
    function renderTxns(svc, coachSel, client) {
      loading();
      var q = { category: svc.key, user_id: client.user_id, month: MONTH };
      if (coachSel) q.earned_by = coachSel.earned_by;
      Promise.resolve(cfg.data.txns(q)).then(function (d) {
        CUR = d.currency || CUR;
        var rows = (d.transactions || []).map(function (x) {
          var chip = { paid: "confirmed", owed: "held" }[x.state] || "";
          return el("div", { class: "cf-item cf-item-tap", onclick: function () { drillTxn(x); } }, [
            el("span", { class: "cf-chip " + (x.category || ""), text: x.label }),
            el("div", { class: "cf-item-main" }, [
              el("div", { class: "cf-item-t", text: x.client_name }),
              el("div", { class: "cf-item-s", text: (x.at ? UI.fmtDate(x.at) : "") + (x.description ? " · " + x.description : "") }),
            ]),
            el("div", { style: "text-align:right" }, [
              el("div", { style: "font-weight:700", text: money(x.billed_minor, CUR) }),
              el("span", { class: "cf-chip " + chip, style: "font-size:.7rem", text: x.state }),
            ]),
          ]);
        });
        show(drillScreen({
          backLabel: client.name, onBack: function () { renderClients(svc, coachSel); },
          title: client.name, crumb: svc.label + (coachSel ? " · " + coachSel.name : ""),
          totals: d.totals, rows: rows, empty: "No transactions.",
          note: "Tap a transaction to open its record — pay, discount, void or refund. Get these right before month-end.",
        }));
      }, fail);
    }

    function drillTxn(x) {
      if (!cfg.onNavigate) return;
      if (x.booking_id) cfg.onNavigate({ kind: "event", id: x.booking_id });
      else if (x.enrolment_id) cfg.onNavigate({ kind: "class", id: x.enrolment_id });
      else if (x.order_id) cfg.onNavigate({ kind: "txn", id: x.order_id });
    }

    renderServices();
    return { refresh: renderServices };
  }

  window.Widgets = window.Widgets || {};
  window.Widgets.Earnings = { mount: mount };
})();

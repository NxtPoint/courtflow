// account.js — the client ACCOUNT page (front-end redesign v2, 2026-06-28).
// Profile/Family editing moved to the Home greeting popups; Account is now the money + usage
// surface, fleshed out for good client visibility: plan, usage-over-time (12 months, derived from
// bookings client-side — no backend change), this-month usage, billing per month, account balance,
// next charge, coaching statement (if any), payments/receipts (+ request refund), refund requests.
(function () {
  var UI, el;
  var st = { fin: null, orders: [], refunds: [], statement: null, bookings: [], activity: [] };
  // Statement UI state (survives re-renders): which categories are expanded + which lines are ticked.
  var STMT = { open: {}, sel: {} };
  var CAT_ICON = { "Coaching": "🎾", "Court hire": "🏟", "Classes": "👥", "Membership": "⭐", "Session packs": "🎟", "Other": "•" };

  function money(minor, ccy) { return UI.money(minor, ccy || (st.fin && st.fin.currency) || "ZAR"); }
  function monthLabel(p) {
    if (!p) return p; var x = String(p).split("-"); if (x.length < 2) return p;
    var n = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return (n[parseInt(x[1], 10) - 1] || x[1]) + " " + x[0];
  }
  function monthsBack(n) {
    var out = [], d = new Date();
    for (var i = n - 1; i >= 0; i--) {
      var dt = new Date(d.getFullYear(), d.getMonth() - i, 1);
      out.push(dt.getFullYear() + "-" + ("0" + (dt.getMonth() + 1)).slice(-2));
    }
    return out;
  }
  function tile(t, s) { return el("div", { class: "cf-tile", style: "cursor:default" }, [el("div", { class: "cf-tile-t", text: t }), el("div", { class: "cf-tile-s", text: s })]); }
  function card(title) { var c = el("div", { class: "cf-card" }); if (title) c.appendChild(el("h3", { text: title })); return c; }

  // A simple bar chart (reuses the cf-bars look from the cockpit).
  function barChart(items, valueFn, labelFn, titleFn) {
    var max = items.reduce(function (m, it) { return Math.max(m, valueFn(it) || 0); }, 0) || 1;
    var bars = el("div", { class: "cf-bars", style: "margin-top:14px" });
    items.forEach(function (it) {
      var v = valueFn(it) || 0, pct = Math.round((v / max) * 100);
      bars.appendChild(el("div", { class: "cf-bar", title: titleFn ? titleFn(it) : "" }, [
        el("div", { class: "cf-bar-val", text: String(v) }),
        el("div", { class: "cf-bar-fill", style: "height:" + pct + "%" }),
        el("div", { class: "cf-bar-lbl", text: labelFn(it) }),
      ]));
    });
    return bars;
  }

  // ---- usage aggregation (client-side, from 12 months of bookings) -----------
  function aggregateUsage() {
    var keys = monthsBack(12);
    var idx = {}; keys.forEach(function (k) { idx[k] = { period: k, court: 0, lesson: 0, "class": 0, total: 0, minutes: 0 }; });
    var totals = { court: 0, lesson: 0, "class": 0, total: 0, minutes: 0 };
    st.bookings.forEach(function (b) {
      if (["confirmed", "completed"].indexOf(b.status) < 0) return;      // actual usage only
      var k = (b.starts_at || "").slice(0, 7);
      var row = idx[k]; if (!row) return;                                // outside the 12-mo window
      var t = b.booking_type === "lesson" ? "lesson" : (b.booking_type === "class" ? "class" : "court");
      var mins = Math.max(0, Math.round((new Date(b.ends_at) - new Date(b.starts_at)) / 60000));
      row[t] += 1; row.total += 1; row.minutes += mins;
      totals[t] += 1; totals.total += 1; totals.minutes += mins;
    });
    return { months: keys.map(function (k) { return idx[k]; }), totals: totals };
  }

  // ===========================================================================
  function render() {
    var host = document.getElementById("cf-account"); UI.clear(host);
    host.appendChild(el("div", { class: "cf-card" }, [
      el("h2", { text: "Account" }),
      el("p", { class: "cf-muted", text: "Your plan, your usage over time, billing and statements." }),
    ]));

    var f = st.fin || {}, ccy = f.currency || "ZAR", plan = f.plan || {}, nc = f.next_charge || {};

    // ---- plan ----
    var planCard = card(null);
    planCard.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center" }, [
      el("h3", { text: "Your plan" }),
      el("span", { class: "cf-chip", text: plan.name || (plan.active ? "Membership" : "Pay as you go") }),
    ]));
    var planLines = [];
    if (plan.is_trial && plan.trial_days_left != null) planLines.push("🎁 Free week — " + plan.trial_days_left + " day" + (plan.trial_days_left === 1 ? "" : "s") + " left.");
    if (plan.active && plan.membership_window_summary) planLines.push(plan.membership_window_summary + " (other times are pay-as-you-go).");
    else if (plan.active && !plan.is_trial) planLines.push("Court bookings are free, any time.");
    if (plan.active && plan.current_period_end) planLines.push("Renews / expires " + plan.current_period_end + ".");
    if (!plan.active) planLines.push("You pay per booking. A membership makes court bookings free.");
    if (nc && nc.amount_minor) planLines.push("Next charge: " + money(nc.amount_minor, ccy) + (nc.due_date ? " on " + nc.due_date : "") + ".");
    planCard.appendChild(el("div", { class: "cf-muted", style: "margin:6px 0 12px", text: planLines.join(" ") }));
    var planBtns = el("div", { class: "cf-row", style: "gap:8px;flex-wrap:wrap" }, [
      el("button", { class: "cf-btn cf-btn-primary", text: plan.active ? "Manage plan" : "Choose a plan", onclick: function () { if (window.PlanWizard) window.PlanWizard.open(); else window.location.href = "/plan"; } }),
    ]);
    // Self-cancel for a paid membership (not the free trial — that just lapses on its own).
    if (plan.active && !plan.is_trial) {
      planBtns.appendChild(el("button", { class: "cf-btn cf-btn-danger", text: "Cancel membership", onclick: function () { cancelMembership(); } }));
    }
    planCard.appendChild(planBtns);
    host.appendChild(planCard);

    // ---- usage over time (the fleshed-out part) ----
    var ag = aggregateUsage();
    var usageCard = card("Usage over time");
    var hours = Math.round(ag.totals.minutes / 60);
    usageCard.appendChild(el("div", { class: "cf-tiles", style: "margin-top:6px" }, [
      tile(String(ag.totals.total), "Bookings (12 mo)"),
      tile(hours + "h", "Court time"),
      tile(String(ag.totals.court), "Courts"),
      tile(String(ag.totals.lesson), "Lessons"),
      tile(String(ag.totals["class"]), "Classes"),
    ]));
    var anyUsage = ag.months.some(function (m) { return m.total > 0; });
    if (anyUsage) {
      usageCard.appendChild(el("p", { class: "cf-muted cf-tiny", style: "margin-top:16px", text: "Bookings per month" }));
      usageCard.appendChild(barChart(ag.months, function (m) { return m.total; },
        function (m) { return monthLabel(m.period).split(" ")[0]; },
        function (m) { return monthLabel(m.period) + ": " + m.total + " (" + m.court + " court · " + m.lesson + " lesson · " + m["class"] + " class)"; }));
    } else {
      usageCard.appendChild(el("div", { class: "cf-empty", style: "margin-top:10px", text: "No bookings in the last 12 months yet." }));
    }
    host.appendChild(usageCard);

    // ---- billing per month ----
    var spend = f.spend || {};
    var hist = (spend.history || []).slice().sort(function (a, b) { return a.period < b.period ? -1 : 1; });
    var billCard = card(null);
    billCard.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:baseline" }, [
      el("h3", { text: "Billing" }),
      el("div", { style: "font-weight:800;font-size:1.3rem", text: money(spend.this_month_minor || 0, ccy) }),
    ]));
    billCard.appendChild(el("p", { class: "cf-muted cf-tiny", text: "Paid this month" }));
    var paidHist = hist.filter(function (h) { return h.paid_minor > 0 || h.orders > 0; });
    if (paidHist.length) {
      billCard.appendChild(barChart(paidHist, function (h) { return Math.round((h.paid_minor || 0) / 100); },
        function (h) { return monthLabel(h.period).split(" ")[0]; },
        function (h) { return monthLabel(h.period) + ": " + money(h.paid_minor, ccy) + " · " + h.orders + " payment" + (h.orders === 1 ? "" : "s"); }));
      var tbl = el("div", { class: "cf-list", style: "margin-top:14px" });
      paidHist.slice().reverse().forEach(function (h) {
        tbl.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: monthLabel(h.period) }), el("div", { class: "cf-item-s", text: h.orders + (h.orders === 1 ? " payment" : " payments") })]),
          el("div", { style: "font-weight:700", text: money(h.paid_minor, ccy) }),
        ]));
      });
      billCard.appendChild(tbl);
    } else {
      billCard.appendChild(el("div", { class: "cf-empty", style: "margin-top:8px", text: "No payments yet." }));
    }
    host.appendChild(billCard);

    // ---- your statement (ONE reconciled view of everything owed — unpaid orders) ----
    // docs/specs/UNIFIED-STATEMENT.md: the total is exactly SUM(unpaid orders); no double count.
    // Grouped by category (collapsible +/-) with a tick per line so a client can PART-settle —
    // unticked lines stay owed. The backend settles only the chosen order_ids (reconciliation-safe).
    renderStatement(host, st.statement || {}, ccy);

    // ---- payments / receipts (+ request refund) ----
    var ordCard = card("Payments & receipts");
    if (!st.orders.length) { ordCard.appendChild(el("div", { class: "cf-empty", text: "No payments yet." })); }
    else {
      var ol = el("div", { class: "cf-list", style: "margin-top:10px" });
      st.orders.forEach(function (o) {
        var right;
        if (o.refundable) right = el("button", { class: "cf-btn cf-btn-sm", text: "Request refund", onclick: function () { refundModal(o); } });
        else if (o.has_open_refund) right = el("span", { class: "cf-chip", text: "Refund " + (o.refund_status || "requested") });
        else if (o.status === "refunded") right = el("span", { class: "cf-chip", text: "Refunded" });
        else right = el("span", { class: "cf-tiny cf-muted", text: "" });
        ol.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: o.description || "Payment" }), el("div", { class: "cf-item-s", text: (o.created_at ? o.created_at.slice(0, 10) : "") + " · " + money(o.amount_minor, o.currency_code) + " · " + (o.status || "") })]),
          right,
        ]));
      });
      ordCard.appendChild(ol);
    }
    host.appendChild(ordCard);

    // ---- activity (the full transparent transaction log) ----
    var actCard = card("Activity");
    actCard.appendChild(el("p", { class: "cf-muted", style: "margin:-2px 0 10px",
      text: "Everything that's happened on your account — payments, refunds, charges and coaching." }));
    actCard.appendChild(window.CRMUI.activityFeed(st.activity || [], { empty: "No activity yet." }));
    host.appendChild(actCard);

    // ---- refund requests ----
    if (st.refunds.length) {
      var rc = card("Refund requests");
      var rl = el("div", { class: "cf-list", style: "margin-top:10px" });
      st.refunds.forEach(function (r) {
        var actions = [el("span", { class: "cf-chip", text: r.status })];
        if (r.status === "pending") actions.push(el("button", { class: "cf-btn cf-btn-sm", text: "Withdraw", onclick: function () { cancelRefund(r); } }));
        rl.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: money(r.amount_minor) + (r.reason ? " — " + r.reason : "") }), el("div", { class: "cf-item-s", text: r.created_at ? r.created_at.slice(0, 10) : "" })]),
          el("div", { class: "cf-row", style: "gap:6px;align-items:center" }, actions),
        ]));
      });
      rc.appendChild(rl); host.appendChild(rc);
    }
  }

  function refundModal(order) {
    var bg = el("div", { class: "cf-modal-bg" });
    var reason = el("textarea", { class: "cf-input", rows: "3", placeholder: "Tell the club why you're requesting a refund (optional)" });
    var save = el("button", { class: "cf-btn cf-btn-primary", text: "Send request" });
    save.addEventListener("click", function () { submitRefund(order, reason.value.trim() || null, save, bg); });
    bg.appendChild(el("div", { class: "cf-modal" }, [
      el("h2", { text: "Request a refund" }),
      el("p", { class: "cf-muted cf-tiny", text: (order.description || "Payment") + " · " + money(order.amount_minor, order.currency_code) + ". Your coach or the club will review your request." }),
      el("div", { class: "cf-field" }, [el("label", { text: "Reason" }), reason]),
      el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px;gap:8px" }, [
        el("button", { class: "cf-btn", text: "Cancel", onclick: function () { document.body.removeChild(bg); } }), save,
      ]),
    ]));
    document.body.appendChild(bg);
  }
  async function submitRefund(order, reason, btn, bg) {
    btn.disabled = true; var o = btn.textContent; btn.textContent = "Sending…";
    try { await window.API.requestRefund({ order_id: order.id, reason: reason }); document.body.removeChild(bg); await reloadMoney(); UI.toast("Refund requested — the club will review it.", "info"); }
    catch (e) { btn.disabled = false; btn.textContent = o; UI.toast(UI.errMsg(e), "error"); }
  }
  // Grouped, collapsible, tick-to-pay statement. Renders the whole "Your statement" card and
  // re-renders itself in place on tick/expand (no full page reload).
  function renderStatement(host, stm, ccy) {
    var items = stm.items || [];
    if (!items.length) return;
    var scur = stm.currency || ccy;
    // Default every line ticked; keep prior choices across re-renders; drop lines that are gone.
    var live = {};
    items.forEach(function (it) { if (STMT.sel[it.order_id] === undefined) STMT.sel[it.order_id] = true; live[it.order_id] = true; });
    Object.keys(STMT.sel).forEach(function (k) { if (!live[k]) delete STMT.sel[k]; });

    var sc = card("Your statement");
    sc.appendChild(el("p", { class: "cf-muted cf-tiny", text: "Everything you owe, grouped. Open a heading to see the detail; untick anything you'd rather not pay yet, then settle the rest." }));

    // Group by category, in a stable order.
    var groups = {}, order = [];
    items.forEach(function (it) { var c = it.category || "Other"; if (!groups[c]) { groups[c] = []; order.push(c); } groups[c].push(it); });

    var listWrap = el("div", { style: "margin-top:8px" });
    function selectedTotal() { return items.reduce(function (n, it) { return n + (STMT.sel[it.order_id] ? it.amount_minor : 0); }, 0); }
    var footHost = el("div");

    function redraw() {
      UI.clear(listWrap);
      order.forEach(function (cat) {
        var gitems = groups[cat];
        var gtotal = gitems.reduce(function (n, it) { return n + it.amount_minor; }, 0);
        var open = !!STMT.open[cat];
        var allOn = gitems.every(function (it) { return STMT.sel[it.order_id]; });
        var someOn = gitems.some(function (it) { return STMT.sel[it.order_id]; });

        // group checkbox (tick all / none in the group)
        var gcb = el("input", { type: "checkbox" }); gcb.style.width = "auto"; gcb.checked = allOn; gcb.indeterminate = !allOn && someOn;
        gcb.addEventListener("click", function (ev) { ev.stopPropagation(); var on = gcb.checked; gitems.forEach(function (it) { STMT.sel[it.order_id] = on; }); redraw(); });

        var head = el("div", { class: "cf-item cf-pickable", style: "align-items:center" }, [
          gcb,
          el("span", { style: "font-size:1.05rem;width:22px;text-align:center", text: STMT.open[cat] ? "−" : "+" }),
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: (CAT_ICON[cat] ? CAT_ICON[cat] + " " : "") + cat }),
            el("div", { class: "cf-item-s", text: gitems.length + " item" + (gitems.length === 1 ? "" : "s") }),
          ]),
          el("div", { style: "font-weight:700", text: money(gtotal, scur) }),
        ]);
        head.addEventListener("click", function () { STMT.open[cat] = !STMT.open[cat]; redraw(); });
        listWrap.appendChild(head);

        if (open) {
          var sub = el("div", { class: "cf-list", style: "margin:6px 0 10px 14px" });
          gitems.forEach(function (it) {
            var cb = el("input", { type: "checkbox" }); cb.style.width = "auto"; cb.checked = !!STMT.sel[it.order_id];
            cb.addEventListener("change", function () { STMT.sel[it.order_id] = cb.checked; redraw(); });
            var sub2 = (it.date ? String(it.date).slice(0, 10) : "");
            if (it.coach_name) sub2 += (sub2 ? " · " : "") + it.coach_name;
            sub2 += (sub2 ? " · " : "") + (it.pay_label || it.settlement_mode || "");
            sub.appendChild(el("label", { class: "cf-item", style: "cursor:pointer;align-items:center" }, [
              cb,
              el("div", { class: "cf-item-main" }, [
                el("div", { class: "cf-item-t", text: it.description || "Booking" }),
                el("div", { class: "cf-item-s", text: sub2 }),
              ]),
              el("span", { class: "cf-chip held", text: it.status || "Owed" }),
              el("div", { style: "font-weight:700;min-width:78px;text-align:right", text: money(it.amount_minor, scur) }),
            ]));
          });
          listWrap.appendChild(sub);
        }
      });
      // footer: selected total + settle CTA
      UI.clear(footHost);
      var selN = selectedTotal();
      var allSelected = selN === stm.total_owed_minor;
      footHost.appendChild(el("div", { class: "cf-row", style: "margin-top:6px;justify-content:space-between;font-weight:700" }, [
        el("span", { text: "Total owed" }), el("span", { text: money(stm.total_owed_minor, scur) }),
      ]));
      var box = el("div", { class: "cf-card", style: "margin-top:12px;background:var(--green-050);border-color:var(--green)" });
      box.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px" }, [
        el("div", {}, [
          el("div", { style: "font-weight:800", text: (allSelected ? "Settle your balance — " : "Settle selected — ") + money(selN, scur) }),
          el("div", { class: "cf-muted cf-tiny", text: allSelected ? "Pay everything by card, or settle at the club." : "Only the ticked items will be paid; the rest stay owed." }),
        ]),
        el("button", { class: "cf-btn cf-btn-primary cf-btn-lg", text: "Settle " + money(selN, scur) + " online", disabled: selN <= 0 ? "" : null,
          onclick: function () { var ids = items.filter(function (it) { return STMT.sel[it.order_id]; }).map(function (it) { return it.order_id; }); payStatement(ids); } }),
      ]));
      footHost.appendChild(box);
    }

    redraw();
    sc.appendChild(listWrap);
    sc.appendChild(footHost);
    host.appendChild(sc);
  }

  async function payStatement(orderIds) {
    try {
      var body = (orderIds && orderIds.length) ? { order_ids: orderIds } : {};
      var res = await window.TFAuth.apiJSON("/api/me/statement/pay", { method: "POST", body: body });
      if (!res || !res.order_id) throw new Error("no order returned");
      if (window.Pay) { await window.Pay.startYocoCheckout(res.order_id); return; }
      UI.toast("Couldn't open the payment page — please refresh and try again.", "error");
    } catch (e) {
      var code = e && e.body && e.body.error;
      UI.toast(code === "NOTHING_OWED" ? "You have nothing outstanding to pay." : (UI.errMsg(e) || "Could not start payment."), "error");
    }
  }

  async function cancelRefund(r) {
    if (!confirm("Withdraw this refund request?")) return;
    try { await window.API.cancelRefundRequest(r.id); await reloadMoney(); UI.toast("Request withdrawn.", "info"); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  async function cancelMembership() {
    if (!confirm("Cancel your membership? Court bookings will revert to pay-as-you-go. This doesn't refund the current term — request a refund separately if you need one.")) return;
    try {
      await window.TFAuth.apiJSON("/api/me/membership/cancel", { method: "POST", body: {} });
      await reloadMoney();
      UI.toast("Membership cancelled — courts are now pay-as-you-go.", "info");
    } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  async function reloadMoney() {
    try { st.fin = await window.API.financials(); } catch (e) {}
    try { st.orders = (await window.API.myOrders()).orders || []; } catch (e) { st.orders = []; }
    try { st.refunds = (await window.API.refundRequests()).requests || []; } catch (e) { st.refunds = []; }
    try { st.activity = (await window.API.activity()).activity || []; } catch (e) { st.activity = []; }
    render();
  }

  async function loadAll() {
    await Promise.all([
      window.API.financials().then(function (r) { st.fin = r; }, function () {}),
      window.API.myOrders().then(function (r) { st.orders = r.orders || []; }, function () {}),
      window.API.refundRequests().then(function (r) { st.refunds = r.requests || []; }, function () {}),
      window.API.myStatement().then(function (r) { st.statement = r; }, function () {}),
      window.API.activity().then(function (r) { st.activity = r.activity || []; }, function () {}),
      window.API.bookings({ date_from: UI.dateKey(UI.addDays(new Date(), -365)), date_to: UI.dateKey(new Date()) }).then(function (r) { st.bookings = r.bookings || []; }, function () {}),
    ]);
  }

  window.Account = {
    start: async function () {
      UI = window.UI; el = UI.el;
      var host = document.getElementById("cf-account");
      UI.clear(host); host.appendChild(el("div", { class: "cf-loading", text: "Loading your account…" }));
      await loadAll();
      render();
    },
  };
})();

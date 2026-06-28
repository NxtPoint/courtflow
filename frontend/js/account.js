// account.js — the client ACCOUNT page (front-end redesign v2, 2026-06-28).
// Profile/Family editing moved to the Home greeting popups; Account is now the money + usage
// surface, fleshed out for good client visibility: plan, usage-over-time (12 months, derived from
// bookings client-side — no backend change), this-month usage, billing per month, account balance,
// next charge, coaching statement (if any), payments/receipts (+ request refund), refund requests.
(function () {
  var UI, el;
  var st = { fin: null, orders: [], refunds: [], statement: null, bookings: [] };

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
    if (plan.active && plan.current_period_end) planLines.push("Renews / expires " + plan.current_period_end + ".");
    if (!plan.active) planLines.push("You pay per booking. A membership makes court bookings free.");
    if (nc && nc.amount_minor) planLines.push("Next charge: " + money(nc.amount_minor, ccy) + (nc.due_date ? " on " + nc.due_date : "") + ".");
    planCard.appendChild(el("div", { class: "cf-muted", style: "margin:6px 0 12px", text: planLines.join(" ") }));
    planCard.appendChild(el("button", { class: "cf-btn cf-btn-primary", text: plan.active ? "Manage plan" : "Choose a plan", onclick: function () { if (window.PlanWizard) window.PlanWizard.open(); else window.location.href = "/plan"; } }));
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
    var acct = f.account || {};
    if (acct.balance_minor) {
      billCard.appendChild(el("div", { class: "cf-row", style: "margin-top:12px;justify-content:space-between;font-weight:700" }, [
        el("span", { text: "Account balance (pay end of month)" }), el("span", { text: money(acct.balance_minor, ccy) }),
      ]));
    }
    host.appendChild(billCard);

    // ---- coaching statement (only if there is coaching activity) ----
    var stm = st.statement;
    if (stm && stm.coaches && stm.coaches.length) {
      var sc = card("Coaching statement");
      sc.appendChild(el("p", { class: "cf-muted cf-tiny", text: "Lessons with your coaches this month — paid and outstanding." }));
      var sl = el("div", { class: "cf-list", style: "margin-top:10px" });
      stm.coaches.forEach(function (c) {
        sl.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: c.coach_name || "Coach" }), el("div", { class: "cf-item-s", text: (c.lessons || 0) + " lesson" + (c.lessons === 1 ? "" : "s") + " · paid " + money(c.paid_minor, stm.currency) + (c.owed_minor ? " · owed " + money(c.owed_minor, stm.currency) : "") })]),
          el("div", { style: "font-weight:700", text: money(c.net_minor, stm.currency) }),
        ]));
      });
      sc.appendChild(sl);
      var tot = stm.totals || {};
      sc.appendChild(el("div", { class: "cf-row", style: "margin-top:10px;justify-content:space-between;font-weight:700" }, [
        el("span", { text: "Total (this month)" }), el("span", { text: money(tot.net_minor || 0, stm.currency) }),
      ]));
      host.appendChild(sc);
    }

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
      el("p", { class: "cf-muted cf-tiny", text: (order.description || "Payment") + " · " + money(order.amount_minor, order.currency_code) + ". The club will review your request." }),
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
  async function cancelRefund(r) {
    if (!confirm("Withdraw this refund request?")) return;
    try { await window.API.cancelRefundRequest(r.id); await reloadMoney(); UI.toast("Request withdrawn.", "info"); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  async function reloadMoney() {
    try { st.fin = await window.API.financials(); } catch (e) {}
    try { st.orders = (await window.API.myOrders()).orders || []; } catch (e) { st.orders = []; }
    try { st.refunds = (await window.API.refundRequests()).requests || []; } catch (e) { st.refunds = []; }
    render();
  }

  async function loadAll() {
    await Promise.all([
      window.API.financials().then(function (r) { st.fin = r; }, function () {}),
      window.API.myOrders().then(function (r) { st.orders = r.orders || []; }, function () {}),
      window.API.refundRequests().then(function (r) { st.refunds = r.requests || []; }, function () {}),
      window.API.myStatement().then(function (r) { st.statement = r; }, function () {}),
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

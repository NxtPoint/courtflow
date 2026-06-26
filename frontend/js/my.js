// my.js — member "My bookings": list, cancel, reschedule own bookings + class enrolments,
// plus a ledger/statement summary line. Calls GET/POST/PATCH /api/diary/bookings*.
(function () {
  var UI, el, principal;

  async function load() {
    var box = document.getElementById("my-list");
    UI.clear(box); box.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
    try {
      var from = UI.dateKey(UI.addDays(new Date(), -1));
      var to = UI.dateKey(UI.addDays(new Date(), 90));
      var r = await window.API.bookings({ date_from: from, date_to: to });
      render(r.bookings || []);
    } catch (e) { box.innerHTML = ""; box.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
  }

  function render(bookings) {
    var box = document.getElementById("my-list"); UI.clear(box);
    var pending = bookings.filter(function (b) { return ["requested", "proposed"].indexOf(b.status) >= 0; });
    var active = bookings.filter(function (b) { return ["held", "confirmed"].indexOf(b.status) >= 0; });
    var past = bookings.filter(function (b) { return ["cancelled", "completed", "no_show"].indexOf(b.status) >= 0; });

    if (!bookings.length) { box.appendChild(el("div", { class: "cf-empty", text: "No bookings yet." })); return; }

    if (pending.length) {
      box.appendChild(el("h3", { text: "Needs your attention" }));
      var pend = el("div", { class: "cf-list" });
      pending.forEach(function (b) { pend.appendChild(pendingRow(b)); });
      box.appendChild(pend);
    }
    if (active.length) {
      box.appendChild(el("h3", { text: "Upcoming", style: pending.length ? "margin-top:16px" : "" }));
      var list = el("div", { class: "cf-list" });
      active.forEach(function (b) { list.appendChild(row(b, true)); });
      box.appendChild(list);
    }
    if (past.length) {
      box.appendChild(el("h3", { text: "Past & cancelled", style: "margin-top:16px" }));
      var pl = el("div", { class: "cf-list" });
      past.forEach(function (b) { pl.appendChild(row(b, false)); });
      box.appendChild(pl);
    }
  }

  // requested = your lesson request awaiting the coach (you can withdraw it).
  // proposed  = the coach offered a (possibly new) time — accept or decline.
  function pendingRow(b) {
    var isProposed = b.status === "proposed";
    var sub = isProposed
      ? ("Coach proposed: " + UI.fmtRange(b.starts_at, b.ends_at))
      : ("Requested: " + UI.fmtRange(b.starts_at, b.ends_at) + " · awaiting coach");
    var actions = [];
    if (isProposed) {
      actions.push(el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Accept", onclick: function () { acceptProposed(b); } }));
      actions.push(el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Decline", onclick: function () { declineProposed(b); } }));
    } else {
      actions.push(el("button", { class: "cf-btn cf-btn-sm", text: "Withdraw", onclick: function () { withdraw(b); } }));
    }
    return el("div", { class: "cf-item" }, [
      el("span", { class: "cf-chip held", text: b.status }),
      el("div", { class: "cf-item-main" }, [
        el("div", { class: "cf-item-t", text: b.resource_name || "Lesson" }),
        el("div", { class: "cf-item-s", text: sub }),
      ]),
    ].concat(actions));
  }

  async function acceptProposed(b) {
    try { await window.API.acceptBooking(b.id); UI.toast("Lesson confirmed.", "info"); load(); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  async function declineProposed(b) {
    if (!confirm("Decline this proposed time?")) return;
    try { await window.API.declineBooking(b.id, { reason: "member_declined" }); UI.toast("Declined.", "info"); load(); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  async function withdraw(b) {
    if (!confirm("Withdraw this lesson request?")) return;
    try { await window.API.cancelBooking(b.id, { reason: "member_withdraw" }); UI.toast("Request withdrawn.", "info"); load(); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  function row(b, actionable) {
    // A lesson is one line (the auto-held court is collapsed server-side); show the court inline.
    var sub = UI.fmtRange(b.starts_at, b.ends_at) + (b.court_name ? " · " + b.court_name : "")
      + " · " + UI.settlementLabel(b.settlement_mode);
    var main = el("div", { class: "cf-item-main" }, [
      el("div", { class: "cf-item-t", text: b.resource_name || b.booking_type }),
      el("div", { class: "cf-item-s", text: sub }),
    ]);
    var children = [
      el("span", { class: "cf-chip " + b.booking_type, text: b.booking_type }),
      main,
      el("span", { class: "cf-chip " + b.status, text: b.status }),
    ];
    if (actionable) {
      children.push(el("button", { class: "cf-btn cf-btn-sm", text: "Add to calendar", onclick: function () { addToCalendar(b); } }));
      children.push(el("button", { class: "cf-btn cf-btn-sm", text: "Reschedule", onclick: function () { reschedule(b); } }));
      children.push(el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Cancel", onclick: function () { cancel(b); } }));
    }
    return el("div", { class: "cf-item" }, children);
  }

  // Authed .ics download -> add the booking to Google/Apple/Outlook. (The same file the
  // confirmation email will attach once SES/Klaviyo is connected.)
  async function addToCalendar(b) {
    try {
      var res = await window.TFAuth.apiFetch("/api/diary/bookings/" + encodeURIComponent(b.id) + "/calendar.ics");
      if (!res || !res.ok) throw new Error("unavailable");
      var url = URL.createObjectURL(new Blob([await res.text()], { type: "text/calendar" }));
      var a = el("a", { href: url, download: "booking.ics" });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
    } catch (e) { UI.toast("Couldn't generate the calendar file.", "error"); }
  }

  async function cancel(b) {
    if (!confirm("Cancel this booking? Cancellation policy/fees may apply.")) return;
    try {
      await window.API.cancelBooking(b.id, { reason: "member_cancel" });
      UI.toast("Cancelled.", "info"); load();
    } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  function reschedule(b) {
    // Minimal reschedule: prompt for a new start (ISO) and keep the duration.
    var bg = el("div", { class: "cf-modal-bg" });
    var startLocal = b.starts_at.slice(0, 16); // yyyy-mm-ddThh:mm
    var input = el("input", { class: "cf-input", type: "datetime-local", value: startLocal });
    var modal = el("div", { class: "cf-modal" }, [
      el("h2", { text: "Reschedule" }),
      el("p", { class: "cf-muted", text: "Pick a new start time. The same duration is kept; conflicts are rejected." }),
      el("div", { class: "cf-field" }, [ el("label", { text: "New start" }), input ]),
      el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px" }, [
        el("button", { class: "cf-btn", text: "Cancel", onclick: function () { document.body.removeChild(bg); } }),
        el("button", { class: "cf-btn cf-btn-primary", text: "Save", onclick: function () { doReschedule(b, input.value, bg); } }),
      ]),
    ]);
    bg.appendChild(modal);
    document.body.appendChild(bg);
  }

  async function doReschedule(b, newStartLocal, bg) {
    if (!newStartLocal) return;
    // Preserve duration: compute new end from the original span.
    var oldS = new Date(b.starts_at), oldE = new Date(b.ends_at);
    var durMs = oldE - oldS;
    var newS = new Date(newStartLocal);
    var newE = new Date(newS.getTime() + durMs);
    try {
      await window.API.rescheduleBooking(b.id, { starts_at: newS.toISOString(), ends_at: newE.toISOString(), scope: "this" });
      document.body.removeChild(bg);
      UI.toast("Rescheduled.", "info"); load();
    } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  async function loadStatement() {
    // Ledger/statement summary: there is no public per-member statement GET yet
    // (build_statements runs server-side via the monthly cron, C lane). We show the
    // count of open (monthly_account) bookings as a balance proxy until C exposes
    // GET /api/billing/statement. See report — flagged as a needed endpoint.
    var box = document.getElementById("my-statement");
    try {
      var from = UI.dateKey(UI.addDays(new Date(), -90));
      var to = UI.dateKey(UI.addDays(new Date(), 30));
      var r = await window.API.bookings({ date_from: from, date_to: to });
      var onAccount = (r.bookings || []).filter(function (b) {
        return b.settlement_mode === "monthly_account" && b.status !== "cancelled";
      });
      UI.clear(box);
      box.appendChild(el("p", { class: "cf-muted", text:
        onAccount.length
          ? (onAccount.length + " booking(s) on your monthly account. Your statement is emailed monthly.")
          : "No outstanding account charges." }));
    } catch (e) { box.textContent = UI.errMsg(e); }
  }

  window.MyBookings = {
    start: function (p) {
      UI = window.UI; el = UI.el; principal = p;
      load(); loadStatement();
    },
  };
})();

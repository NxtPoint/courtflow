// client.js — the CLIENT app: a single-page, bottom-nav shell (Home · Book · Bookings · Billing)
// with drill-through everywhere. Every booking / charge / payment opens its FULL story — no dumps.
// Reuses the plumbing (TFAuth, API, Pay, UI, the cf-* design system) and mounts the existing
// booking flow (BookFlow) for the Book tab. Staff are redirected to their own consoles.
(function () {
  var UI, el, principal = null;
  var view;                       // #cf-main (the routed content area)
  var DATA = {};                  // small cache; refreshed after actions
  var NAME = "";

  function money(m, c) { return UI.money(m || 0, c || "ZAR"); }
  function go(hash) { location.hash = hash; }
  function esc(s) { return UI.esc(s); }

  // ---- boot ----------------------------------------------------------------
  async function start() {
    UI = window.UI; el = UI.el;
    await window.TFAuth.ready();
    if (!window.TFAuth.isAuthed()) { await window.TFAuth.requireAuth(); return; }
    try { principal = await window.API.whoami(); }
    catch (e) { if (e.status === 401) await window.TFAuth.requireAuth(); return; }
    if (!principal) return;
    // Staff live in their own consoles — the client app is for members/guests. Send a first-run
    // owner/coach to their onboarding first (the same gate portal.html used to run).
    if (principal.role === "coach") {
      try { var cob = await window.TFAuth.apiJSON("/api/coach/onboarding"); if (cob && !cob.completed) { location.href = "/coach-onboarding.html"; return; } } catch (e) {}
      location.href = "/coach.html"; return;
    }
    if (principal.role === "club_admin" || principal.role === "platform_admin") {
      try { var aob = await window.TFAuth.apiJSON("/api/admin/onboarding"); if (aob && !aob.completed) { location.href = "/onboarding.html"; return; } } catch (e) {}
      location.href = "/admin.html"; return;
    }
    if (!principal.club_id) { document.body.innerHTML =
      '<div style="padding:40px;font-family:Inter,system-ui">No active club is resolved for your account. Contact the club to be added.</div>'; return; }
    renderShell();
    window.addEventListener("hashchange", route);
    // Warm the profile name for the greeting + avatar.
    try { var pr = await window.API.getProfile(); DATA.profile = pr; NAME = fullName(pr); paintAvatar(); } catch (e) {}
    route();
  }

  function fullName(p) {
    if (!p) return "";
    return [p.first_name, p.surname].filter(Boolean).join(" ").trim() || (p.email || "").split("@")[0] || "there";
  }
  function initials() {
    var n = (NAME || (principal && principal.email) || "You").trim();
    var parts = n.split(/\s+/);
    return ((parts[0] || "Y")[0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
  }
  function paintAvatar() { var a = document.getElementById("cf-avatar"); if (a) a.textContent = initials(); }

  // ---- shell (appbar + view + bottom nav) ----------------------------------
  var NAV = [
    { k: "home", ic: "⌂", label: "Home" },
    { k: "book", ic: "+", label: "Book", cls: "book" },
    { k: "bookings", ic: "🎾", label: "Bookings" },
    { k: "billing", ic: "🧾", label: "Billing" },
  ];
  function renderShell() {
    document.body.classList.add("cf-app");
    var root = document.getElementById("cf-main") || document.body;
    // Appbar
    if (!document.getElementById("cf-appbar")) {
      var bar = el("div", { class: "cf-appbar", id: "cf-appbar" }, [
        el("div", { class: "cf-brand" }, [el("span", { class: "cf-logo", text: "NP" }), el("span", { text: "NextPoint" })]),
        el("span", { class: "cf-spacer" }),
        el("div", { class: "cf-bell-host", id: "cf-bell" }),
        el("div", { class: "cf-avatar", id: "cf-avatar", text: initials(), onclick: function () { go("#/profile"); } }),
      ]);
      document.body.insertBefore(bar, document.body.firstChild);
      mountBell(document.getElementById("cf-bell"));
    }
    // View container = #cf-main (booking.js also renders here).
    view = document.getElementById("cf-main");
    if (!view) { view = el("main", { class: "cf-main", id: "cf-main" }); document.body.appendChild(view); }
    // Bottom nav
    if (!document.getElementById("cf-bottomnav")) {
      var inner = el("div", { class: "cf-bottomnav-in" });
      NAV.forEach(function (n) {
        inner.appendChild(el("a", { href: "#/" + n.k, "data-nav": n.k, class: (n.cls || "") }, [
          el("span", { class: "ic", text: n.ic }), el("span", { text: n.label }),
        ]));
      });
      document.body.appendChild(el("nav", { class: "cf-bottomnav", id: "cf-bottomnav" }, [inner]));
    }
  }
  function setActive(k) {
    document.querySelectorAll("#cf-bottomnav a").forEach(function (a) {
      a.classList.toggle("active", a.getAttribute("data-nav") === k);
    });
  }
  function mountBell(hostEl) {
    if (!hostEl) return;
    if (window.Notifications) { window.Notifications.mount(hostEl); return; }
    var s = document.createElement("script"); s.src = "/js/notifications.js";
    s.onload = function () { if (window.Notifications) window.Notifications.mount(hostEl); };
    document.head.appendChild(s);
  }

  // ---- router --------------------------------------------------------------
  function route() {
    var h = (location.hash || "").replace(/^#\/?/, "");
    var parts = h.split("/").filter(Boolean);
    var top = parts[0] || "home";
    setActive(["home", "book", "bookings", "billing"].indexOf(top) >= 0 ? top : (top === "booking" ? "bookings" : (top === "profile" ? "" : top)));
    window.scrollTo(0, 0);
    if (top === "home") return renderHome();
    if (top === "book") return renderBook(parts[1]);
    if (top === "bookings") return renderBookings();
    if (top === "booking") return renderBookingStory(parts[1]);
    if (top === "billing") return parts[1] === "order" ? renderOrder(parts[2]) : renderBilling();
    if (top === "plan") return renderPlan();
    if (top === "profile") return parts[1] === "edit" ? renderProfileEdit() : (parts[1] === "child" ? renderChildEdit(parts[2]) : renderProfile());
    return renderHome();
  }

  // ---- small render helpers ------------------------------------------------
  function set(node) { view.style.opacity = 0; UI.clear(view); view.appendChild(node); requestAnimationFrame(function () { view.style.transition = "opacity .16s"; view.style.opacity = 1; }); }
  function loading() { set(el("div", { class: "cf-loading", style: "min-height:200px", text: "Loading…" })); }
  function card(children, extra) { return el("div", { class: "cf-card" + (extra ? " " + extra : "") }, children); }
  function backBar(label, hash) {
    return el("div", { class: "cf-backbar" }, [
      el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "‹ " + (label || "Back"), onclick: function () { hash ? go(hash) : history.back(); } }),
    ]);
  }
  function kv(k, v) { return el("div", { class: "cf-kv" }, [el("div", { class: "cf-kv-k", text: k }), el("div", { class: "cf-kv-v" }, typeof v === "string" ? [document.createTextNode(v)] : [v])]); }
  var TYPE_LABEL = { court: "Court", lesson: "Lesson", class: "Class" };
  function typeLabel(t) { return TYPE_LABEL[t] || "Booking"; }
  var DESC = { court: "Court booking", lesson: "Private lesson", class: "Class", membership: "Membership" };
  function pretty(s) { if (!s) return "Charge"; var k = String(s).toLowerCase(); return DESC[k] || (s.charAt(0).toUpperCase() + s.slice(1)); }
  function firstName() { var n = NAME || (principal && principal.email ? principal.email.split("@")[0] : ""); return (n || "there").split(" ")[0]; }
  function timeRange(b) {
    try { return UI.fmtTime(b.starts_at) + "–" + UI.fmtTime(b.ends_at); } catch (e) { return ""; }
  }
  // status → {cls,label}. Booking statuses + charge statuses share the chip vocabulary.
  function statusChip(status) {
    var map = {
      confirmed: ["confirmed", "Confirmed"], held: ["held", "Pending"], completed: ["ok", "Completed"],
      cancelled: ["cancelled", "Cancelled"], no_show: ["cancelled", "No-show"],
      requested: ["held", "Requested"], proposed: ["held", "Awaiting you"],
      paid: ["confirmed", "Paid"], owed: ["held", "Owed"], pending: ["held", "Pending"],
      refunded: ["cancelled", "Refunded"], covered: ["court", "Covered"], written_off: ["cancelled", "Written off"],
    };
    var m = map[status] || ["", status || ""];
    return el("span", { class: "cf-chip " + m[0], text: m[1] });
  }

  // ---- HOME ----------------------------------------------------------------
  async function renderHome() {
    loading();
    var fin = {}, bookings = [];
    try { fin = await window.API.financials(); } catch (e) {}
    try { bookings = (await window.API.bookings({ date_from: UI.dateKey(new Date()), date_to: UI.dateKey(UI.addDays(new Date(), 120)) })).bookings || []; } catch (e) {}
    DATA.fin = fin;
    var wrap = el("div", {});
    var plan = fin.plan || {}, cur = fin.currency || "ZAR";

    // Greeting ribbon
    var chip = plan.is_trial ? ("🎁 Free week · " + (plan.trial_days_left || 0) + "d")
      : (plan.active ? "⭐ Member" : "Pay as you go");
    wrap.appendChild(el("div", { class: "cf-greet" }, [
      el("div", {}, [el("h1", { text: greet() + ", " + firstName() }), el("p", { text: "Ready to play?" })]),
      el("span", { class: "cf-greet-plan", text: chip }),
    ]));

    // Needs attention (requested/proposed)
    var attn = bookings.filter(function (b) { return b.status === "proposed" || b.status === "requested"; });
    if (attn.length) {
      var ac = card([el("h2", { style: "margin:0 0 8px", text: "Needs your attention" })], "");
      var al = el("div", { class: "cf-list" });
      attn.forEach(function (b) { al.appendChild(attnRow(b)); });
      ac.appendChild(al); wrap.appendChild(ac);
    }

    // Next up
    var upcoming = bookings.filter(function (b) { return ["confirmed", "held", "completed"].indexOf(b.status) >= 0; });
    var nextCard = card([el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:8px" }, [
      el("h2", { style: "margin:0", text: "Next up" }),
      upcoming.length ? el("a", { href: "#/bookings", class: "cf-muted", style: "font-size:.85rem", text: "All bookings ›" }) : null,
    ].filter(Boolean))]);
    if (!upcoming.length) nextCard.appendChild(el("div", { class: "cf-empty", text: "No upcoming sessions — book one below." }));
    else { var nl = el("div", { class: "cf-list" }); upcoming.slice(0, 3).forEach(function (b) { nl.appendChild(bookingRow(b)); }); nextCard.appendChild(nl); }
    wrap.appendChild(nextCard);

    // Book quick tiles
    var qb = card([el("h2", { style: "margin:0 0 10px", text: "Book" })]);
    var tiles = el("div", { class: "cf-qb" });
    [["court", "🎾", "Court"], ["lesson", "🎓", "Lesson"], ["class", "👥", "Class"]].forEach(function (t) {
      tiles.appendChild(el("button", { class: "cf-qb-btn", onclick: function () { go("#/book/" + t[0]); } }, [
        el("span", { class: "cf-qb-ic", text: t[1] }), el("span", { class: "cf-qb-t", text: t[2] }),
      ]));
    });
    qb.appendChild(tiles); wrap.appendChild(qb);

    // What you owe
    var owe = (fin.account && fin.account.balance_minor) || 0;
    if (owe > 0) {
      wrap.appendChild(el("div", { class: "cf-card cf-tap", onclick: function () { go("#/billing"); } }, [
        el("div", { class: "cf-owe" }, [
          el("div", {}, [el("div", { class: "cf-muted", style: "font-size:.8rem;font-weight:700", text: "YOU OWE" }),
            el("div", { class: "cf-amountbig", text: money(owe, cur) })]),
          el("span", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Settle ›" }),
        ]),
      ]));
    } else if (!plan.active) {
      wrap.appendChild(el("div", { class: "cf-nudge cf-tap", onclick: function () { openPlan(); } }, [
        el("div", { class: "cf-nudge-t", text: "Save with a membership or pack" }),
        el("div", { class: "cf-nudge-s", text: "Free courts or prepaid sessions — see your options ›" }),
      ]));
    }
    set(wrap);
  }
  function greet() { var h = new Date().getHours(); return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"; }

  function bookingRow(b) {
    return el("div", { class: "cf-item cf-item-tap", onclick: function () { go("#/booking/" + b.id); } }, [
      el("span", { class: "cf-chip " + b.booking_type, text: typeLabel(b.booking_type) }),
      el("div", { class: "cf-item-main" }, [
        el("div", { class: "cf-item-t", text: (b.court_name || b.resource_name || typeLabel(b.booking_type)) }),
        el("div", { class: "cf-item-s", text: UI.fmtDate(b.starts_at) + " · " + timeRange(b) }),
      ]),
      statusChip(b.status),
    ]);
  }
  function attnRow(b) {
    var row = el("div", { class: "cf-item" }, [
      el("div", { class: "cf-item-main" }, [
        el("div", { class: "cf-item-t", text: typeLabel(b.booking_type) + " · " + UI.fmtDate(b.starts_at) }),
        el("div", { class: "cf-item-s", text: b.status === "proposed" ? "Your coach proposed " + timeRange(b) : "Awaiting the coach · " + timeRange(b) }),
      ]),
    ]);
    var acts = el("div", { class: "cf-row", style: "gap:6px" });
    if (b.status === "proposed") {
      acts.appendChild(el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Accept", onclick: function () { act(function () { return window.API.acceptBooking(b.id); }, "Confirmed."); } }));
      acts.appendChild(el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Decline", onclick: function () { act(function () { return window.API.declineBooking(b.id, {}); }, "Declined."); } }));
    } else {
      acts.appendChild(el("button", { class: "cf-btn cf-btn-sm", text: "Withdraw", onclick: function () { act(function () { return window.API.cancelBooking(b.id, { reason: "withdrawn" }); }, "Withdrawn."); } }));
    }
    row.appendChild(acts);
    return row;
  }
  function act(fn, okMsg) { fn().then(function () { UI.toast(okMsg, "info"); route(); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); }

  // ---- BOOK (mount the existing full-screen flow) --------------------------
  function renderBook(kind) {
    UI.clear(view);
    if (window.BookFlow) window.BookFlow.start(principal, kind || "court");
    else set(el("div", { class: "cf-empty", text: "Booking is unavailable." }));
  }

  // ---- BOOKINGS ------------------------------------------------------------
  async function renderBookings() {
    loading();
    var bookings = [];
    try { bookings = (await window.API.bookings({ date_from: UI.dateKey(UI.addDays(new Date(), -365)), date_to: UI.dateKey(UI.addDays(new Date(), 180)) })).bookings || []; } catch (e) {}
    var now = new Date();
    var up = [], past = [];
    bookings.forEach(function (b) {
      if (b.status === "cancelled" || b.status === "no_show") past.push(b);
      else if (new Date(b.ends_at || b.starts_at) >= now) up.push(b); else past.push(b);
    });
    up.sort(function (a, b) { return new Date(a.starts_at) - new Date(b.starts_at); });
    past.sort(function (a, b) { return new Date(b.starts_at) - new Date(a.starts_at); });
    var st = (renderBookings._tab = renderBookings._tab || "up");
    var wrap = el("div", {});
    wrap.appendChild(el("h1", { style: "margin:0 0 12px", text: "My bookings" }));
    var seg = el("div", { class: "cf-segment cf-seg-lg" });
    [["up", "Upcoming (" + up.length + ")"], ["past", "Past"]].forEach(function (s) {
      seg.appendChild(el("button", { type: "button", class: st === s[0] ? "on" : "", text: s[1], onclick: function () { renderBookings._tab = s[0]; renderBookings(); } }));
    });
    wrap.appendChild(seg);
    var list = (st === "up") ? up : past;
    if (!list.length) wrap.appendChild(el("div", { class: "cf-empty", text: st === "up" ? "Nothing booked yet — tap Book below." : "No past sessions." }));
    else {
      var box = el("div", { class: "cf-card", style: "padding:6px 14px" });
      var ll = el("div", { class: "cf-list" });
      list.forEach(function (b) { ll.appendChild(bookingRow(b)); });
      box.appendChild(ll); wrap.appendChild(box);
    }
    set(wrap);
  }

  // ---- BOOKING STORY (the full drill-through) ------------------------------
  async function renderBookingStory(id) {
    loading();
    var b;
    try { b = (await window.API.bookingStory(id)).booking; } catch (e) { set(el("div", {}, [backBar("Bookings", "#/bookings"), el("div", { class: "cf-empty", text: UI.errMsg(e) })])); return; }
    var ch = b.charge || {}, cur = ch.currency || "ZAR";
    var wrap = el("div", {});
    wrap.appendChild(backBar("Bookings", "#/bookings"));

    // Header
    var head = card([
      el("div", { class: "cf-detail-h" }, [
        el("div", {}, [
          el("span", { class: "cf-chip " + b.booking_type, text: typeLabel(b.booking_type) + (b.duration_minutes ? " · " + b.duration_minutes + " min" : "") }),
          el("h1", { style: "margin:8px 0 2px;font-size:1.35rem", text: UI.fmtDate(b.starts_at) }),
          el("div", { class: "cf-muted", text: timeRange(b) }),
        ]),
        statusChip(b.status),
      ]),
    ]);
    // Details
    var det = el("div", { style: "margin-top:6px" });
    if (b.venue && (b.venue.club_name || b.venue.address)) {
      det.appendChild(kv("Where", el("div", {}, [
        el("div", { text: [b.venue.club_name, b.court_name].filter(Boolean).join(" · ") || b.court_name || "—" }),
        b.venue.address ? el("div", { class: "cf-muted", style: "font-size:.85rem", text: b.venue.address }) : null,
      ].filter(Boolean))));
    } else if (b.court_name) det.appendChild(kv("Where", b.court_name));
    if (b.coach_name) det.appendChild(kv("Coach", b.coach_name));
    if (b.players && b.players.length) det.appendChild(kv("Who", b.players.map(function (p) { return p.name; }).join(", ")));
    // Charge row
    det.appendChild(kv("Charge", el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [
      el("span", { style: "font-weight:700", text: ch.status === "covered" ? "Covered" : money(ch.amount_minor, cur) }),
      statusChip(ch.status),
    ])));
    head.appendChild(det);
    wrap.appendChild(head);

    // Actions
    var acts = el("div", { class: "cf-row", style: "gap:8px;flex-wrap:wrap;margin-top:14px" });
    if (b.can.pay) acts.appendChild(el("button", { class: "cf-btn cf-btn-primary", text: "Pay now · " + money(ch.amount_minor, cur), onclick: function () { payOrders([ch.order_id]); } }));
    if (b.can.accept) acts.appendChild(el("button", { class: "cf-btn cf-btn-primary", text: "Accept time", onclick: function () { act(function () { return window.API.acceptBooking(b.id); }, "Confirmed."); } }));
    if (b.can.add_to_calendar) acts.appendChild(el("a", { class: "cf-btn cf-btn-ghost", href: b.ics_url, text: "Add to calendar" }));
    if (b.can.receipt) acts.appendChild(el("button", { class: "cf-btn cf-btn-ghost", text: "Receipt", onclick: function () { go("#/billing/order/" + ch.order_id); } }));
    if (b.can.reschedule) acts.appendChild(el("button", { class: "cf-btn cf-btn-ghost", text: "Reschedule", onclick: function () { rescheduleSheet(b); } }));
    if (b.can.cancel) acts.appendChild(el("button", { class: "cf-btn cf-btn-danger", text: "Cancel", onclick: function () { cancelBooking(b); } }));
    if (b.can.request_refund) acts.appendChild(el("button", { class: "cf-btn cf-btn-ghost", text: "Request refund", onclick: function () { requestRefund(ch.order_id); } }));
    if (b.can.withdraw) acts.appendChild(el("button", { class: "cf-btn cf-btn-danger", text: "Withdraw request", onclick: function () { act(function () { return window.API.cancelBooking(b.id, { reason: "withdrawn" }); }, "Withdrawn."); } }));
    if (b.can.decline && !b.can.accept) acts.appendChild(el("button", { class: "cf-btn cf-btn-danger", text: "Decline", onclick: function () { act(function () { return window.API.declineBooking(b.id, {}); }, "Declined."); } }));
    if (acts.childNodes.length) wrap.appendChild(acts);
    set(wrap);
  }

  function cancelBooking(b) {
    if (!window.confirm("Cancel this " + typeLabel(b.booking_type).toLowerCase() + " on " + UI.fmtDate(b.starts_at) + "?")) return;
    window.API.cancelBooking(b.id, { reason: "client cancelled" }).then(function () { UI.toast("Cancelled.", "info"); go("#/bookings"); }, function (e) { UI.toast(UI.errMsg(e), "error"); });
  }
  function requestRefund(orderId) {
    var reason = window.prompt("Request a refund for this booking? Add a reason (optional):", "");
    if (reason === null) return;
    window.API.requestRefund({ order_id: orderId, reason: reason }).then(function () { UI.toast("Refund requested — the club or your coach will review it.", "info"); route(); }, function (e) { UI.toast(UI.errMsg(e), "error"); });
  }
  function rescheduleSheet(b) {
    var m = modal("Reschedule");
    var start = el("input", { class: "cf-input", type: "datetime-local", value: toLocal(b.starts_at) });
    var dur = el("select", { class: "cf-input" }, [30, 45, 60, 90, 120].map(function (d) { return el("option", { value: String(d), text: d + " min" }); }));
    dur.value = String(b.duration_minutes || 60);
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "New time" }), start]));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Duration" }), dur]));
    m.body.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;gap:8px;margin-top:12px" }, [
      el("button", { class: "cf-btn", text: "Close", onclick: m.close }),
      el("button", { class: "cf-btn cf-btn-primary", text: "Reschedule", onclick: function () {
        if (!start.value) { UI.toast("Pick a time.", "warn"); return; }
        var s = new Date(start.value), e = new Date(s.getTime() + parseInt(dur.value, 10) * 60000);
        window.API.rescheduleBooking(b.id, { starts_at: s.toISOString(), ends_at: e.toISOString(), scope: "this" })
          .then(function () { UI.toast("Rescheduled.", "info"); m.close(); route(); }, function (er) { UI.toast(UI.errMsg(er), "error"); });
      } }),
    ]));
  }

  // ---- BILLING -------------------------------------------------------------
  async function renderBilling() {
    loading();
    var stmt = { items: [], total_owed_minor: 0 }, fin = {}, orders = [], refunds = [], wallets = [];
    try { stmt = await window.API.myStatement(); } catch (e) {}
    try { fin = await window.API.financials(); } catch (e) {}
    try { orders = (await window.API.myOrders()).orders || []; } catch (e) {}
    try { refunds = (await window.API.refundRequests()).requests || []; } catch (e) {}
    try { wallets = (await window.TFAuth.apiJSON("/api/billing/bundles/wallets?active=1")).wallets || []; } catch (e) {}
    var cur = stmt.currency || fin.currency || "ZAR";
    var plan = fin.plan || {};
    var wrap = el("div", {});
    wrap.appendChild(el("h1", { style: "margin:0 0 12px", text: "Billing" }));

    // What you owe
    var owe = stmt.total_owed_minor || 0;
    var oweCard = card([el("div", { class: "cf-owe", style: "margin-bottom:" + (owe ? "12px" : "0") }, [
      el("div", {}, [el("div", { class: "cf-muted", style: "font-size:.78rem;font-weight:700", text: "WHAT YOU OWE" }),
        el("div", { class: "cf-amountbig", text: money(owe, cur) })]),
      owe ? el("button", { class: "cf-btn cf-btn-primary", text: "Pay all", onclick: function () { payOrders(null); } }) : el("span", { class: "cf-chip confirmed", text: "All settled" }),
    ])]);
    if (owe) {
      var ol = el("div", { class: "cf-list" });
      (stmt.items || []).forEach(function (it) {
        ol.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { itemOpen(it); } }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: pretty(it.description || it.category) }),
            el("div", { class: "cf-item-s", text: [it.category, it.coach_name, it.date ? UI.fmtDate(it.date) : ""].filter(Boolean).join(" · ") }),
          ]),
          el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [
            el("span", { style: "font-weight:700", text: money(it.amount_minor, cur) }),
            el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Pay", onclick: function (ev) { ev.stopPropagation(); payOrders([it.order_id]); } }),
          ]),
        ]));
      });
      oweCard.appendChild(ol);
    }
    wrap.appendChild(oweCard);

    // Plan & credits
    var planCard = card([el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:8px" }, [
      el("h2", { style: "margin:0", text: "Plan & credits" }),
      el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Manage ›", onclick: function () { openPlan(); } }),
    ])]);
    var planLine = plan.is_trial ? ("🎁 Free week — " + (plan.trial_days_left || 0) + " days left")
      : plan.active ? (plan.name || "Membership") + (plan.current_period_end ? " · renews " + plan.current_period_end : "")
      : "Pay as you go — no membership";
    planCard.appendChild(el("div", { class: "cf-item" }, [
      el("span", { class: "cf-chip " + (plan.active ? "confirmed" : ""), text: plan.active ? "Active" : "PAYG" }),
      el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: planLine })]),
    ]));
    if (wallets.length) {
      var wl = el("div", { class: "cf-list" });
      wallets.forEach(function (w) {
        wl.appendChild(el("div", { class: "cf-item" }, [
          el("span", { class: "cf-chip " + (w.service_kind || ""), text: (w.service_kind || "pack") }),
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: w.label || "Session pack" }),
            el("div", { class: "cf-item-s", text: (w.sessions_remaining != null ? w.sessions_remaining : "–") + " sessions left" + (w.expires_at ? " · expires " + UI.fmtDate(w.expires_at) : "") }),
          ]),
        ]));
      });
      planCard.appendChild(wl);
    }
    wrap.appendChild(planCard);

    // History (paid/refunded orders → receipt)
    var histCard = card([el("h2", { style: "margin:0 0 8px", text: "History" })]);
    if (!orders.length) histCard.appendChild(el("div", { class: "cf-empty", text: "No payments yet." }));
    else {
      var hl = el("div", { class: "cf-list" });
      orders.slice(0, 20).forEach(function (o) {
        hl.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { go("#/billing/order/" + o.id); } }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: o.description || "Payment" }),
            el("div", { class: "cf-item-s", text: (o.created_at ? o.created_at.slice(0, 10) : "") + " · " + money(o.amount_minor, o.currency_code) }),
          ]),
          statusChip(o.status),
        ]));
      });
      histCard.appendChild(hl);
    }
    wrap.appendChild(histCard);

    // Refund requests
    var openRefunds = refunds.filter(function (r) { return ["pending", "approved", "refunded", "declined"].indexOf(r.status) >= 0; });
    if (openRefunds.length) {
      var rc = card([el("h2", { style: "margin:0 0 8px", text: "Refund requests" })]);
      var rl = el("div", { class: "cf-list" });
      openRefunds.slice(0, 8).forEach(function (r) {
        rl.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: money(r.amount_minor, cur) + (r.routed_to === "coach" ? " · with your coach" : "") }),
            el("div", { class: "cf-item-s", text: r.reason || "" }),
          ]),
          statusChip(r.status),
          r.status === "pending" ? el("button", { class: "cf-btn cf-btn-sm", text: "Withdraw", onclick: function () { window.API.cancelRefundRequest(r.id).then(function () { UI.toast("Withdrawn.", "info"); route(); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); } }) : null,
        ].filter(Boolean)));
      });
      rc.appendChild(rl); wrap.appendChild(rc);
    }
    set(wrap);
  }
  // A statement line points at its booking story when it's a booking; else the order receipt.
  function itemOpen(it) {
    if (it.kind === "court" || it.kind === "lesson" || it.kind === "class") {
      // No booking_id on the statement line — fall back to the order receipt (still full detail).
      go("#/billing/order/" + it.order_id);
    } else { go("#/billing/order/" + it.order_id); }
  }
  // Pay a set of owed orders (null = pay all) via the unified statement settlement → Yoco.
  function payOrders(orderIds) {
    var body = orderIds ? { order_ids: orderIds } : {};
    window.API.payStatement(body).then(function (res) {
      if (res && res.order_id && window.Pay) window.Pay.startYocoCheckout(res.order_id);
      else UI.toast("Nothing to pay.", "info");
    }, function (e) {
      if (e && e.status === 409) { UI.toast("Nothing owed.", "info"); route(); }
      else UI.toast(UI.errMsg(e), "error");
    });
  }

  // ---- ORDER / RECEIPT detail ---------------------------------------------
  async function renderOrder(orderId) {
    loading();
    var r;
    try { var raw = await window.TFAuth.apiJSON("/api/billing/receipt/" + encodeURIComponent(orderId)); r = raw.receipt || raw; }
    catch (e) { set(el("div", {}, [backBar("Billing", "#/billing"), el("div", { class: "cf-empty", text: UI.errMsg(e) })])); return; }
    var cur = r.currency || "ZAR";
    var refunded = (r.refunded_minor || 0) > 0;
    var paid = r.status === "paid" || refunded;
    var wrap = el("div", {});
    wrap.appendChild(backBar("Billing", "#/billing"));
    var c = card([
      el("div", { class: "cf-detail-h" }, [
        el("div", {}, [el("div", { class: "cf-muted", style: "font-size:.78rem;font-weight:700", text: (paid ? "RECEIPT " : "CHARGE ") + (r.receipt_no || "") }),
          el("h1", { style: "margin:4px 0 2px;font-size:1.3rem", text: money(r.amount_minor, cur) })]),
        statusChip(refunded ? "refunded" : (r.status === "open" ? "owed" : (r.status || "paid"))),
      ]),
      el("div", { class: "cf-muted", style: "margin-bottom:6px", text: (r.issued_at ? UI.fmtDate(r.issued_at) : "") + (r.payer_email ? " · " + r.payer_email : "") }),
    ]);
    var lines = el("div", { style: "margin-top:6px" });
    (r.lines || []).forEach(function (l) {
      lines.appendChild(el("div", { class: "cf-item" }, [
        el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: pretty(l.description) + (l.qty > 1 ? " ×" + l.qty : "") })]),
        el("span", { style: "font-weight:600", text: money(l.amount_minor, cur) }),
      ]));
    });
    c.appendChild(lines);
    c.appendChild(el("div", { class: "cf-kv", style: "border-top:2px solid var(--ink);margin-top:6px" }, [
      el("div", { class: "cf-kv-k", text: "Total" }), el("div", { class: "cf-kv-v", style: "font-weight:800;text-align:right", text: money(r.amount_minor, cur) })]));
    if (refunded) c.appendChild(kv("Refunded", el("span", { style: "color:var(--danger);font-weight:700", text: "−" + money(r.refunded_minor, cur) })));
    wrap.appendChild(c);
    var acts = el("div", { class: "cf-row", style: "gap:8px;margin-top:14px" });
    if (r.status === "open") acts.appendChild(el("button", { class: "cf-btn cf-btn-primary", text: "Pay now · " + money(r.amount_minor, cur), onclick: function () { payOrders([orderId]); } }));
    if (paid) acts.appendChild(el("a", { class: "cf-btn cf-btn-ghost", href: "/receipt.html?order=" + encodeURIComponent(orderId), target: "_blank", text: "Print / PDF" }));
    wrap.appendChild(acts);
    set(wrap);
  }

  // ---- PLAN (reuse the existing 3-purchasing-models wizard as an overlay) --
  function openPlan() {
    if (window.PlanWizard && window.PlanWizard.open) window.PlanWizard.open();
    else location.href = "/plan";
  }
  function renderPlan() { renderBilling(); openPlan(); }

  // ---- PROFILE + edit + family --------------------------------------------
  async function renderProfile() {
    loading();
    var pr = DATA.profile, deps = [];
    try { if (!pr) { pr = await window.API.getProfile(); DATA.profile = pr; } } catch (e) { pr = {}; }
    try { deps = (await window.API.dependents()).dependents || []; } catch (e) {}
    NAME = fullName(pr); paintAvatar();
    var wrap = el("div", {});
    wrap.appendChild(el("div", { class: "cf-greet" }, [
      el("div", {}, [el("h1", { text: NAME }), el("p", { text: pr.email || "" })]),
      el("span", { class: "cf-avatar", style: "background:rgba(255,255,255,.2);color:#fff;border-color:transparent", text: initials() }),
    ]));
    // Details
    var c = card([el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:6px" }, [
      el("h2", { style: "margin:0", text: "Your details" }),
      el("button", { class: "cf-btn cf-btn-sm", text: "Edit", onclick: function () { go("#/profile/edit"); } }),
    ])]);
    var det = el("div", {});
    det.appendChild(kv("Phone", pr.phone || "—"));
    det.appendChild(kv("DOB", pr.dob || "—"));
    var addr = [pr.address_line1, pr.address_line2, pr.city, pr.postal_code].filter(Boolean).join(", ");
    det.appendChild(kv("Address", addr || "—"));
    if (pr.emergency_contact_name) det.appendChild(kv("Emergency", pr.emergency_contact_name + (pr.emergency_contact_phone ? " · " + pr.emergency_contact_phone : "")));
    c.appendChild(det); wrap.appendChild(c);
    // Family
    var fc = card([el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:6px" }, [
      el("h2", { style: "margin:0", text: "Family" }),
      el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "+ Add child", onclick: function () { go("#/profile/child"); } }),
    ])]);
    if (!deps.length) fc.appendChild(el("div", { class: "cf-empty", text: "Add a child to book on their behalf." }));
    else { var dl = el("div", { class: "cf-list" }); deps.forEach(function (d) {
      dl.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { go("#/profile/child/" + d.id); } }, [
        el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: [d.first_name, d.surname].filter(Boolean).join(" ") }),
          el("div", { class: "cf-item-s", text: (d.relationship || "child") + (d.dob ? " · " + d.dob : "") })]),
        el("span", { class: "cf-muted", text: "›" }),
      ]));
    }); fc.appendChild(dl); }
    wrap.appendChild(fc);
    // Sign out
    wrap.appendChild(el("div", { style: "margin-top:14px;text-align:center" }, [
      el("button", { class: "cf-btn cf-btn-ghost", text: "Sign out", onclick: function () { window.TFAuth.signOut().then(function () { location.reload(); }); } }),
    ]));
    set(wrap);
  }

  var FIELDS = [
    ["first_name", "First name", "text"], ["surname", "Surname", "text"], ["phone", "Phone", "tel"],
    ["dob", "Date of birth", "date"], ["address_line1", "Address line 1", "text"], ["address_line2", "Address line 2", "text"],
    ["city", "City", "text"], ["postal_code", "Postal code", "text"],
    ["emergency_contact_name", "Emergency contact", "text"], ["emergency_contact_phone", "Emergency phone", "tel"],
  ];
  async function renderProfileEdit() {
    var pr = DATA.profile || {};
    try { pr = await window.API.getProfile(); DATA.profile = pr; } catch (e) {}
    var wrap = el("div", {});
    wrap.appendChild(backBar("Profile", "#/profile"));
    wrap.appendChild(el("h1", { style: "margin:0 0 12px", text: "Edit profile" }));
    var c = card([]);
    var inputs = {};
    c.appendChild(kv("Email", el("span", { class: "cf-muted", text: (pr.email || "") + "  (sign-in — can't change)" })));
    FIELDS.forEach(function (f) {
      var inp = el("input", { class: "cf-input", type: f[2], value: pr[f[0]] || "" });
      inputs[f[0]] = inp;
      c.appendChild(el("div", { class: "cf-field" }, [el("label", { text: f[1] }), inp]));
    });
    var mk = el("label", { class: "cf-row", style: "gap:8px;align-items:center;margin-top:6px;cursor:pointer" }, [
      (function () { var cb = el("input", { type: "checkbox" }); if (pr.marketing_opt_in) cb.checked = true; inputs._mk = cb; return cb; })(),
      el("span", { text: "Email me club news & offers" }),
    ]);
    c.appendChild(mk);
    wrap.appendChild(c);
    wrap.appendChild(el("div", { class: "cf-row", style: "gap:8px;margin-top:14px" }, [
      el("button", { class: "cf-btn cf-btn-primary cf-btn-block", text: "Save & close", onclick: function () {
        var body = {}; FIELDS.forEach(function (f) { body[f[0]] = inputs[f[0]].value.trim() || null; });
        body.marketing_opt_in = !!inputs._mk.checked;
        window.API.patchProfile(body).then(function (res) { DATA.profile = res; NAME = fullName(res); UI.toast("Saved.", "info"); go("#/profile"); },
          function (e) { UI.toast((e && e.body && e.body.error === "VALIDATION") ? "Please check the fields." : UI.errMsg(e), "error"); });
      } }),
    ]));
    set(wrap);
  }

  async function renderChildEdit(id) {
    var dep = null;
    if (id) { try { dep = ((await window.API.dependents()).dependents || []).filter(function (d) { return String(d.id) === String(id); })[0]; } catch (e) {} }
    var wrap = el("div", {});
    wrap.appendChild(backBar("Profile", "#/profile"));
    wrap.appendChild(el("h1", { style: "margin:0 0 12px", text: id ? "Edit child" : "Add child" }));
    var c = card([]); var inputs = {};
    [["first_name", "First name", "text"], ["surname", "Surname", "text"], ["dob", "Date of birth", "date"]].forEach(function (f) {
      var inp = el("input", { class: "cf-input", type: f[2], value: dep ? (dep[f[0]] || "") : "" }); inputs[f[0]] = inp;
      c.appendChild(el("div", { class: "cf-field" }, [el("label", { text: f[1] }), inp]));
    });
    var rel = el("select", { class: "cf-input" }, ["child", "spouse", "partner", "other"].map(function (o) { return el("option", { value: o, text: o[0].toUpperCase() + o.slice(1) }); }));
    if (dep && dep.relationship) rel.value = dep.relationship; inputs.relationship = rel;
    c.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Relationship" }), rel]));
    wrap.appendChild(c);
    var row = el("div", { class: "cf-row", style: "gap:8px;margin-top:14px" }, [
      el("button", { class: "cf-btn cf-btn-primary cf-btn-block", text: "Save & close", onclick: function () {
        var body = { first_name: inputs.first_name.value.trim(), surname: inputs.surname.value.trim() || null, dob: inputs.dob.value || null, relationship: inputs.relationship.value };
        if (!body.first_name) { UI.toast("First name is required.", "warn"); return; }
        var p = id ? window.API.patchDependent(id, body) : window.API.addDependent(body);
        p.then(function () { UI.toast("Saved.", "info"); go("#/profile"); }, function (e) { UI.toast(UI.errMsg(e), "error"); });
      } }),
    ]);
    wrap.appendChild(row);
    if (id) wrap.appendChild(el("div", { style: "margin-top:10px;text-align:center" }, [
      el("button", { class: "cf-btn cf-btn-ghost cf-btn-sm", text: "Remove child", onclick: function () {
        if (!window.confirm("Remove this child?")) return;
        window.API.removeDependent(id).then(function () { UI.toast("Removed.", "info"); go("#/profile"); }, function (e) { UI.toast(UI.errMsg(e), "error"); });
      } }),
    ]));
    set(wrap);
  }

  // ---- tiny modal ----------------------------------------------------------
  function modal(title) {
    var bg = el("div", { class: "cf-modal-bg" });
    var body = el("div", {});
    bg.appendChild(el("div", { class: "cf-modal" }, [
      el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:6px" }, [
        el("h2", { style: "margin:0", text: title }),
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "✕", onclick: function () { close(); } }),
      ]), body,
    ]));
    document.body.appendChild(bg);
    function close() { if (bg.parentNode) document.body.removeChild(bg); }
    return { body: body, close: close };
  }
  function toLocal(iso) { try { var d = new Date(iso), p = function (n) { return (n < 10 ? "0" : "") + n; }; return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + "T" + p(d.getHours()) + ":" + p(d.getMinutes()); } catch (e) { return ""; } }

  window.Client = { start: start };
})();

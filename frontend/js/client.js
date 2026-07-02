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
    { k: "book", ic: "🎾", label: "Book" },
  ];
  function renderShell() {
    // The client experience is ONE page — no bottom nav. (Book is reached from the Home tiles;
    // the coach & owner apps keep their bottom nav.) The TS avatar (top-right) opens the profile.
    document.body.style.paddingBottom = "20px";
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
    view = document.getElementById("cf-main");
    if (!view) { view = el("main", { class: "cf-main", id: "cf-main" }); document.body.appendChild(view); }
  }
  function setActive(k) { /* no bottom nav on the client */ }
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
    setActive(top === "book" ? "book" : "home");   // everything else lives on Home now
    window.scrollTo(0, 0);
    if (top === "book") return renderBook(parts[1]);
    if (top === "booking") return renderBookingStory(parts[1]);
    if (top === "billing") {                        // drill-through screens under Home's billing
      if (parts[1] === "order") return renderOrder(parts[2]);
      if (parts[1] === "cat") return renderBillingCategory(parts[2]);
      return renderHome();
    }
    if (top === "plan") return renderPlan();
    if (top === "profile") return parts[1] === "edit" ? renderProfileEdit() : (parts[1] === "child" ? renderChildEdit(parts[2]) : renderProfile());
    return renderHome();                            // home / bookings / anything else
  }

  // ---- small render helpers ------------------------------------------------
  function set(node) { view.style.opacity = 0; UI.clear(view); view.appendChild(node); requestAnimationFrame(function () { view.style.transition = "opacity .16s"; view.style.opacity = 1; }); }
  function loading() {
    var n = el("div", { class: "cf-loading", style: "min-height:200px", text: "Loading…" });
    set(n);
    // Free-tier API can cold-start (~30-60s). Reassure instead of a bare spinner that looks stuck.
    setTimeout(function () { if (n.isConnected && n.textContent === "Loading…") n.textContent = "Waking the club up — one moment…"; }, 3500);
  }
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

  // ---- HOME (the hub: everything on one page — no bottom nav for the client) --
  var HBMONTH = null;      // billing month
  function curMonth() { var d = new Date(); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0"); }
  function shiftM(ym, d) { var p = ym.split("-"); var dt = new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1 + d, 1); return dt.getFullYear() + "-" + String(dt.getMonth() + 1).padStart(2, "0"); }
  function mLabel(ym) { var p = ym.split("-"); try { return new Date(p[0], parseInt(p[1], 10) - 1, 1).toLocaleDateString(undefined, { month: "short", year: "numeric" }); } catch (e) { return ym; } }

  async function renderHome() {
    if (!HBMONTH) HBMONTH = curMonth();
    loading();
    var fin = {}, bookings = [];
    try { fin = await window.API.financials(); } catch (e) {}
    try { bookings = (await window.API.bookings({ date_from: UI.dateKey(UI.addDays(new Date(), -730)), date_to: UI.dateKey(UI.addDays(new Date(), 365)) })).bookings || []; } catch (e) {}
    DATA.fin = fin; DATA.bookings = bookings;
    var plan = fin.plan || {}, cur = fin.currency || "ZAR";
    var wrap = el("div", {});

    // Greeting ribbon — profile at a glance: name, email, membership + Manage + Edit profile.
    // (The TS avatar top-right also opens the profile — kept as a shortcut for those who spot it.)
    var email = (DATA.profile && DATA.profile.email) || (principal && principal.email) || "";
    var mLine = plan.is_trial ? ("🎁 Free week — " + (plan.trial_days_left || 0) + " days left")
      : plan.active ? ("⭐ " + (plan.name || "Member") + (plan.current_period_end ? " · renews " + plan.current_period_end : ""))
      : "Pay as you go — no membership";
    wrap.appendChild(el("div", { class: "cf-greet", style: "padding:22px 24px;align-items:flex-start" }, [
      el("div", { style: "flex:1" }, [
        el("h1", { text: greet() + ", " + firstName() }),
        email ? el("p", { style: "opacity:.92;margin-top:2px", text: email }) : null,
        el("p", { style: "margin-top:8px;font-weight:600", text: mLine }),
        el("div", { class: "cf-row", style: "gap:8px;margin-top:12px;flex-wrap:wrap" }, [
          el("button", { class: "cf-btn cf-btn-sm", text: "Edit profile", onclick: function () { go("#/profile/edit"); } }),
          el("button", { class: "cf-btn cf-btn-sm", text: "Manage membership", onclick: openPlan }),
        ]),
      ].filter(Boolean)),
    ]));

    // Needs attention
    var attn = bookings.filter(function (b) { return b.status === "proposed" || b.status === "requested"; });
    if (attn.length) {
      var ac = card([el("h2", { style: "margin:0 0 8px", text: "Needs your attention" })]);
      var al = el("div", { class: "cf-list" }); attn.forEach(function (b) { al.appendChild(attnRow(b)); }); ac.appendChild(al);
      wrap.appendChild(ac);
    }

    // Book quick tiles
    var qb = card([el("h2", { style: "margin:0 0 10px", text: "Book" })]);
    var tiles = el("div", { class: "cf-qb" });
    [["court", "🎾", "Court"], ["lesson", "🎓", "Lesson"], ["class", "👥", "Class"]].forEach(function (t) {
      tiles.appendChild(el("button", { class: "cf-qb-btn", onclick: function () { go("#/book/" + t[0]); } }, [el("span", { class: "cf-qb-ic", text: t[1] }), el("span", { class: "cf-qb-t", text: t[2] })]));
    });
    qb.appendChild(tiles); wrap.appendChild(qb);

    // Your sessions (Upcoming / Past) — the Bookings function, now on Home.
    wrap.appendChild(card([el("h2", { style: "margin:0 0 8px", text: "Your sessions" }), el("div", { id: "home-sessions" })]));

    // Billing — what you owe + a monthly breakdown by category.
    var owe = (fin.account && fin.account.balance_minor) || 0;
    var bc = card([el("h2", { style: "margin:0 0 8px", text: "Billing" })]);
    if (owe > 0) bc.appendChild(el("div", { class: "cf-owe cf-tap", style: "margin-bottom:12px", onclick: function () { payOrders(null); } }, [
      el("div", {}, [el("div", { class: "cf-muted", style: "font-size:.78rem;font-weight:700", text: "YOU OWE" }), el("div", { class: "cf-amountbig", text: money(owe, cur) })]),
      el("span", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Settle ›" }),
    ]));
    bc.appendChild(el("div", { id: "home-billing", class: "cf-loading", text: "…" }));
    wrap.appendChild(bc);

    // Plan & credits — moved to Home (the look you liked, with Manage).
    wrap.appendChild(planCard(plan, cur));

    set(wrap);
    paintSessions();
    loadHomeBilling(cur);
    loadWallets(cur);
  }
  function greet() { var h = new Date().getHours(); return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"; }

  // sessions: ALL of them — Upcoming then Past, organised, each drilling into its detail. Nothing hidden.
  function paintSessions() {
    var box = document.getElementById("home-sessions"); if (!box) return;
    UI.clear(box);
    var now = new Date(), bks = DATA.bookings || [], up = [], past = [];
    bks.forEach(function (b) {
      if (b.status === "cancelled") return;                       // cancelled sessions drop off
      if (new Date(b.ends_at || b.starts_at) >= now && b.status !== "no_show") up.push(b); else past.push(b);
    });
    up.sort(function (a, b) { return new Date(a.starts_at) - new Date(b.starts_at); });
    past.sort(function (a, b) { return new Date(b.starts_at) - new Date(a.starts_at); });
    if (!up.length && !past.length) { box.appendChild(el("div", { class: "cf-empty", text: "No sessions yet — book one above." })); return; }
    if (up.length) { box.appendChild(subHead("Upcoming")); var l1 = el("div", { class: "cf-list" }); up.forEach(function (b) { l1.appendChild(bookingRow(b)); }); box.appendChild(l1); }
    if (past.length) { box.appendChild(subHead("Past")); var l2 = el("div", { class: "cf-list" }); past.slice(0, 40).forEach(function (b) { l2.appendChild(bookingRow(b)); }); box.appendChild(l2); }
  }
  function subHead(t) { return el("div", { class: "cf-muted", style: "font-weight:700;font-size:.72rem;text-transform:uppercase;letter-spacing:.03em;margin:12px 2px 4px", text: t }); }

  // billing: monthly breakdown by category (month nav + tap-through)
  function billMonthNav(cur) {
    return el("div", { class: "cf-row", style: "gap:6px;align-items:center" }, [
      el("button", { class: "cf-btn cf-btn-sm", text: "‹", onclick: function () { HBMONTH = shiftM(HBMONTH, -1); loadHomeBilling(cur); } }),
      el("span", { class: "cf-chip", text: mLabel(HBMONTH) }),
      el("button", { class: "cf-btn cf-btn-sm", text: "›", onclick: function () { HBMONTH = shiftM(HBMONTH, 1); loadHomeBilling(cur); } }),
    ]);
  }
  async function loadHomeBilling(cur) {
    var box = document.getElementById("home-billing"); if (!box) return;
    var d = { categories: [] };
    try { d = await window.API.billingSummary(HBMONTH); } catch (e) {}
    cur = cur || d.currency || "ZAR";
    UI.clear(box);
    box.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:6px" }, [
      el("div", { class: "cf-muted", style: "font-size:.78rem;font-weight:700", text: "THIS MONTH" }), billMonthNav(cur),
    ]));
    if (!(d.categories || []).length) box.appendChild(el("div", { class: "cf-empty", text: "No activity in " + mLabel(HBMONTH) + "." }));
    else {
      var l = el("div", { class: "cf-list" });
      d.categories.forEach(function (c) {
        l.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { go("#/billing/cat/" + c.key + "/" + HBMONTH); } }, [
          el("span", { class: "cf-chip " + c.key, text: c.label }),
          el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: c.count + " " + (c.count === 1 ? "session" : "sessions") })]),
          el("span", { style: "font-weight:700", text: money(c.total_minor, cur) }),
          el("span", { class: "cf-muted", text: "›" }),
        ]));
      });
      box.appendChild(l);
    }
  }

  // plan & credits
  function planCard(plan, cur) {
    var c = card([el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:8px" }, [
      el("h2", { style: "margin:0", text: "Plan & credits" }),
      el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Manage ›", onclick: openPlan }),
    ])]);
    var line = plan.is_trial ? ("🎁 Free week — " + (plan.trial_days_left || 0) + " days left")
      : plan.active ? (plan.name || "Membership") + (plan.current_period_end ? " · renews " + plan.current_period_end : "")
      : "Pay as you go — no membership";
    c.appendChild(el("div", { class: "cf-item" }, [
      el("span", { class: "cf-chip " + (plan.active ? "confirmed" : ""), text: plan.active ? "Active" : "PAYG" }),
      el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: line })]),
    ]));
    c.appendChild(el("div", { id: "home-wallets" }));
    return c;
  }
  async function loadWallets(cur) {
    var box = document.getElementById("home-wallets"); if (!box) return;
    var wallets = [];
    try { wallets = (await window.TFAuth.apiJSON("/api/billing/bundles/wallets?active=1")).wallets || []; } catch (e) {}
    if (!wallets.length) return;
    var l = el("div", { class: "cf-list" });
    wallets.forEach(function (w) {
      l.appendChild(el("div", { class: "cf-item" }, [
        el("span", { class: "cf-chip " + (w.service_kind || ""), text: w.service_kind || "pack" }),
        el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: w.label || "Session pack" }), el("div", { class: "cf-item-s", text: (w.sessions_remaining != null ? w.sessions_remaining : "–") + " left" + (w.expires_at ? " · expires " + UI.fmtDate(w.expires_at) : "") })]),
      ]));
    });
    box.appendChild(l);
  }

  // billing category → its items → each drills into the booking story
  async function renderBillingCategory(key, month) {
    loading();
    var d;
    try { d = await window.API.billingSummary(month); } catch (e) { set(el("div", {}, [backBar("Home", "#/"), el("div", { class: "cf-empty", text: UI.errMsg(e) })])); return; }
    var cat = (d.categories || []).filter(function (c) { return c.key === key; })[0];
    var cur = d.currency || "ZAR";
    var wrap = el("div", {});
    wrap.appendChild(backBar("Home", "#/"));
    wrap.appendChild(el("h1", { style: "margin:0 0 2px", text: (cat ? cat.label : key) }));
    wrap.appendChild(el("p", { class: "cf-muted", style: "margin:0 0 12px", text: mLabel(month) + " · " + (cat ? cat.count : 0) + " · " + money(cat ? cat.total_minor : 0, cur) }));
    if (!cat || !cat.items.length) wrap.appendChild(el("div", { class: "cf-empty", text: "Nothing here." }));
    else {
      var box = el("div", { class: "cf-card", style: "padding:6px 14px" }), l = el("div", { class: "cf-list" });
      cat.items.forEach(function (it) {
        l.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { go(it.booking_id ? ("#/booking/" + it.booking_id) : ("#/billing/order/" + it.order_id)); } }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: UI.fmtDate(it.starts_at) + (it.coach_name ? " · " + it.coach_name : "") }),
            el("div", { class: "cf-item-s", text: [it.court_name, (function () { try { return UI.fmtTime(it.starts_at); } catch (e) { return ""; } })()].filter(Boolean).join(" · ") }),
          ]),
          el("span", { style: "font-weight:600", text: money(it.amount_minor, cur) }),
          statusChip(it.status),
        ]));
      });
      box.appendChild(l); wrap.appendChild(box);
    }
    set(wrap);
  }

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

  // ---- BOOKING STORY (the full drill-through) ------------------------------
  async function renderBookingStory(id) {
    loading();
    var b;
    try { b = (await window.API.bookingStory(id)).booking; } catch (e) { set(el("div", {}, [backBar("Home", "#/"), el("div", { class: "cf-empty", text: UI.errMsg(e) })])); return; }
    var ch = b.charge || {}, cur = ch.currency || "ZAR";
    var wrap = el("div", {});
    wrap.appendChild(backBar("Back"));

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
    window.API.cancelBooking(b.id, { reason: "client cancelled" }).then(function () { UI.toast("Cancelled.", "info"); go("#/"); }, function (e) { UI.toast(UI.errMsg(e), "error"); });
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
    catch (e) { set(el("div", {}, [backBar("Back"), el("div", { class: "cf-empty", text: UI.errMsg(e) })])); return; }
    var cur = r.currency || "ZAR";
    var refunded = (r.refunded_minor || 0) > 0;
    var paid = r.status === "paid" || refunded;
    var wrap = el("div", {});
    wrap.appendChild(backBar("Back"));
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
  function renderPlan() { renderHome(); openPlan(); }

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

// coach_app.js — the COACH app: a mobile-first, bottom-nav SPA that orbits the FULL CLIENT RECORD
// with drill-through on EVERY event. Same DNA as the client app (client.js): one shell, hash router,
// instant section switches, capability-driven detail pages. Reuses CoachAPI / CRMUI / ServiceEditor /
// CoachUI / the cf-* design system. Non-coaches are redirected to the client app.
(function () {
  var UI, el, principal = null, view;
  var PROFILE = null, COACH_RES = null;
  var money = function (m, c) { return UI.money(m || 0, c || "ZAR"); };
  function go(h) { location.hash = h; }

  // ---- boot ----------------------------------------------------------------
  async function start() {
    UI = window.UI; el = UI.el;
    await window.TFAuth.ready();
    if (!window.TFAuth.isAuthed()) { await window.TFAuth.requireAuth(); return; }
    try { principal = await window.API.whoami(); }
    catch (e) { if (e.status === 401) await window.TFAuth.requireAuth(); return; }
    if (!principal) return;
    var role = principal.role;
    if (role !== "coach" && role !== "club_admin" && role !== "platform_admin") { location.href = "/portal"; return; }
    if (role === "coach") {
      try { var ob = await window.CoachAPI.onboarding(); if (ob && !ob.completed) { location.href = "/coach-onboarding.html"; return; } } catch (e) {}
    }
    if (!principal.club_id) { document.body.innerHTML = '<div style="padding:40px;font-family:Inter">No club resolved.</div>'; return; }
    renderShell();
    window.addEventListener("hashchange", route);
    try { PROFILE = (await window.CoachAPI.profile()).profile || {}; paintAvatar(); } catch (e) {}
    route();
  }

  function coachName() { return (PROFILE && (PROFILE.display_name || [PROFILE.first_name, PROFILE.surname].filter(Boolean).join(" "))) || (principal && principal.email ? principal.email.split("@")[0] : "Coach"); }
  function initials() { var n = coachName().trim().split(/\s+/); return ((n[0] || "C")[0] + (n.length > 1 ? n[n.length - 1][0] : "")).toUpperCase(); }
  function paintAvatar() { var a = document.getElementById("cf-avatar"); if (a) a.textContent = initials(); }

  // ---- shell ---------------------------------------------------------------
  var NAV = [
    { k: "home", ic: "⌂", label: "Home" },
    { k: "schedule", ic: "📅", label: "Schedule" },
    { k: "clients", ic: "👥", label: "Clients" },
    { k: "money", ic: "💰", label: "Money" },
    { k: "setup", ic: "⚙", label: "Setup" },
  ];
  function renderShell() {
    document.body.classList.add("cf-app");
    if (!document.getElementById("cf-appbar")) {
      document.body.insertBefore(el("div", { class: "cf-appbar", id: "cf-appbar" }, [
        el("div", { class: "cf-brand" }, [el("span", { class: "cf-logo", text: "NP" }), el("span", { text: "Coach" })]),
        el("span", { class: "cf-spacer" }),
        el("div", { class: "cf-bell-host", id: "cf-bell" }),
        el("div", { class: "cf-avatar", id: "cf-avatar", text: initials(), onclick: function () { go("#/profile"); } }),
      ]), document.body.firstChild);
      mountBell(document.getElementById("cf-bell"));
    }
    view = document.getElementById("cf-main");
    if (!view) { view = el("main", { class: "cf-main", id: "cf-main" }); document.body.appendChild(view); }
    if (!document.getElementById("cf-bottomnav")) {
      var inner = el("div", { class: "cf-bottomnav-in" });
      NAV.forEach(function (n) { inner.appendChild(el("a", { href: "#/" + n.k, "data-nav": n.k }, [el("span", { class: "ic", text: n.ic }), el("span", { text: n.label })])); });
      document.body.appendChild(el("nav", { class: "cf-bottomnav", id: "cf-bottomnav" }, [inner]));
    }
  }
  function setActive(k) { document.querySelectorAll("#cf-bottomnav a").forEach(function (a) { a.classList.toggle("active", a.getAttribute("data-nav") === k); }); }
  function mountBell(h) { if (!h) return; if (window.Notifications) { window.Notifications.mount(h); return; } var s = document.createElement("script"); s.src = "/js/notifications.js"; s.onload = function () { if (window.Notifications) window.Notifications.mount(h); }; document.head.appendChild(s); }

  // ---- router --------------------------------------------------------------
  function route() {
    var parts = (location.hash || "").replace(/^#\/?/, "").split("/").filter(Boolean);
    var top = parts[0] || "home";
    setActive(["home", "schedule", "clients", "money", "setup"].indexOf(top) >= 0 ? top :
      (top === "client" ? "clients" : (top === "event" ? "schedule" : (top === "service" ? "setup" : ""))));
    window.scrollTo(0, 0);
    if (top === "home") return renderHome();
    if (top === "schedule") return renderSchedule();
    if (top === "clients") return renderClients();
    if (top === "client") return renderClient(parts[1]);
    if (top === "event") return renderEvent(parts[1]);
    if (top === "money") return renderMoney();
    if (top === "setup") return renderSetup();
    if (top === "service") return renderService(parts[1]);
    if (top === "profile") return renderProfilePage();
    if (top === "hours") return renderHoursPage();
    return renderHome();
  }

  // ---- helpers -------------------------------------------------------------
  function set(node) { view.style.opacity = 0; UI.clear(view); view.appendChild(node); requestAnimationFrame(function () { view.style.transition = "opacity .16s"; view.style.opacity = 1; }); }
  function loading() {
    var n = el("div", { class: "cf-loading", style: "min-height:200px", text: "Loading…" });
    set(n);
    setTimeout(function () { if (n.isConnected && n.textContent === "Loading…") n.textContent = "Waking the club up — one moment…"; }, 3500);
  }
  function card(children, extra) { return el("div", { class: "cf-card" + (extra ? " " + extra : "") }, children); }
  function backBar(label, hash) { return el("div", { class: "cf-backbar" }, [el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "‹ " + (label || "Back"), onclick: function () { hash ? go(hash) : history.back(); } })]); }
  function kv(k, v) { return el("div", { class: "cf-kv" }, [el("div", { class: "cf-kv-k", text: k }), el("div", { class: "cf-kv-v" }, typeof v === "string" ? [document.createTextNode(v)] : [v])]); }
  var TYPE_LABEL = { court: "Court", lesson: "Lesson", class: "Class" };
  function typeLabel(t) { return TYPE_LABEL[t] || "Session"; }
  function timeRange(b) { try { return UI.fmtTime(b.starts_at) + "–" + UI.fmtTime(b.ends_at); } catch (e) { return ""; } }
  function statusChip(status) {
    var map = { confirmed: ["confirmed", "Confirmed"], held: ["held", "Pending"], completed: ["ok", "Completed"],
      cancelled: ["cancelled", "Cancelled"], no_show: ["cancelled", "No-show"], requested: ["held", "Requested"], proposed: ["held", "Proposed"],
      paid: ["confirmed", "Paid"], owed: ["held", "Owed"], pending: ["held", "Pending"], refunded: ["cancelled", "Refunded"], covered: ["court", "Covered"], written_off: ["cancelled", "Written off"] };
    var m = map[status] || ["", status || ""]; return el("span", { class: "cf-chip " + m[0], text: m[1] });
  }
  function money2(m, c) { return money(m, c); }
  // month state (shared by Home/Clients/Money)
  var MONTH = null;
  function thisMonthKey() { var d = new Date(); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0"); }
  function shiftMonth(ym, d) { var p = ym.split("-"); var dt = new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1 + d, 1); return dt.getFullYear() + "-" + String(dt.getMonth() + 1).padStart(2, "0"); }
  function monthLabel(ym) { var p = ym.split("-"); try { return new Date(p[0], parseInt(p[1], 10) - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" }); } catch (e) { return ym; } }
  function monthNav(onChange) {
    return el("div", { class: "cf-row", style: "gap:6px;align-items:center" }, [
      el("button", { class: "cf-btn cf-btn-sm", text: "‹", onclick: function () { MONTH = shiftMonth(MONTH, -1); onChange(); } }),
      el("span", { class: "cf-chip", text: monthLabel(MONTH) }),
      el("button", { class: "cf-btn cf-btn-sm", text: "›", onclick: function () { MONTH = shiftMonth(MONTH, 1); onChange(); } }),
    ]);
  }
  function ensureMonth() { if (!MONTH) MONTH = thisMonthKey(); }
  function act(fn, ok, then) { fn().then(function () { UI.toast(ok, "info"); (then || route)(); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); }

  // ---- HOME (the business pulse + approval queue + today) ------------------
  async function renderHome() {
    ensureMonth(); loading();
    var ck = {}, pendReq = [], pendProp = [], today = [];
    try { ck = await window.CoachAPI.cockpit(MONTH); } catch (e) {}
    try { pendReq = (await window.CoachAPI.pendingLessons("requested")).bookings || []; } catch (e) {}
    try { pendProp = (await window.CoachAPI.pendingLessons("proposed")).bookings || []; } catch (e) {}
    var tk = UI.dateKey(new Date());
    try { today = (await window.API.bookings({ as_coach: 1, date_from: tk, date_to: tk })).bookings || []; } catch (e) {}
    var k = ck.kpis || {}, cur = "ZAR";
    var wrap = el("div", {});
    wrap.appendChild(el("div", { class: "cf-greet" }, [
      el("div", {}, [el("h1", { text: greet() + ", " + coachName().split(" ")[0] }), el("p", { text: "Here's your business." })]),
      el("span", { class: "cf-greet-plan", text: monthLabel(MONTH) }),
    ]));

    // Business KPIs
    var kpiCard = card([
      el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:8px" }, [
        el("h2", { style: "margin:0", text: "This month" }), monthNav(renderHome),
      ]),
      window.CRMUI.stats([
        { value: money(k.net_minor, cur), label: "Earned (net)" },
        { value: (k.lessons_count || 0), label: "Lessons" },
        { value: (k.hours != null ? k.hours : 0), label: "Hours" },
        { value: (k.fill_rate_pct != null ? k.fill_rate_pct + "%" : "—"), label: "Fill rate" },
      ]),
    ]);
    if ((k.arrears_owed_minor || 0) > 0) kpiCard.appendChild(el("div", { class: "cf-muted", style: "margin-top:8px;font-size:.85rem", text: "Outstanding on client tabs: " + money(k.arrears_owed_minor, cur) + " — chase it up in Money." }));
    wrap.appendChild(kpiCard);

    // Needs your attention (requests)
    if (pendReq.length) {
      var ac = card([el("h2", { style: "margin:0 0 8px", text: "Needs your attention" })]);
      ac.appendChild(window.CRMUI.requestQueue(pendReq.map(reqItem), {
        onAccept: function (it) { act(function () { return window.API.acceptBooking(it.id); }, "Lesson confirmed."); },
        onPropose: function (it) { proposeModal(it.id); },
        onDecline: function (it) { act(function () { return window.API.declineBooking(it.id, {}); }, "Declined."); },
        empty: "Nothing waiting.",
      }));
      wrap.appendChild(ac);
    }

    // Today
    var todayCard = card([el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:8px" }, [
      el("h2", { style: "margin:0", text: "Today" }),
      el("a", { href: "#/schedule", class: "cf-muted", style: "font-size:.85rem", text: "Schedule ›" }),
    ])]);
    var realToday = today.filter(function (b) { return ["confirmed", "held", "completed"].indexOf(b.status) >= 0; });
    if (!realToday.length) todayCard.appendChild(el("div", { class: "cf-empty", text: "No sessions today." }));
    else { var tl = el("div", { class: "cf-list" }); realToday.forEach(function (b) { tl.appendChild(eventRow(b)); }); todayCard.appendChild(tl); }
    wrap.appendChild(todayCard);

    // Quick actions
    wrap.appendChild(el("div", { class: "cf-row", style: "gap:8px;margin-top:4px" }, [
      el("button", { class: "cf-btn cf-btn-primary cf-btn-block", text: "+ Book a client in", onclick: bookForClient }),
    ]));
    set(wrap);
  }
  function greet() { var h = new Date().getHours(); return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"; }
  function reqItem(b) { return { id: b.id, title: (b.booked_by_name || "A client") + " · " + typeLabel(b.booking_type), sub: UI.fmtDate(b.starts_at) + " · " + timeRange(b), status: b.status, starts_at: b.starts_at, ends_at: b.ends_at }; }

  // A booking row (schedule/today/client history) → drills into the event story.
  function eventRow(b) {
    return el("div", { class: "cf-item cf-item-tap", onclick: function () { go("#/event/" + b.id); } }, [
      el("span", { class: "cf-chip " + b.booking_type, text: typeLabel(b.booking_type) }),
      el("div", { class: "cf-item-main" }, [
        el("div", { class: "cf-item-t", text: (b.booked_by_name || b.court_name || typeLabel(b.booking_type)) }),
        el("div", { class: "cf-item-s", text: UI.fmtDate(b.starts_at) + " · " + timeRange(b) + (b.court_name ? " · " + b.court_name : "") }),
      ]),
      statusChip(b.status),
    ]);
  }

  // ---- SCHEDULE (agenda + time-off + book-for-client) ----------------------
  async function renderSchedule() {
    loading();
    var from = UI.dateKey(new Date()), to = UI.dateKey(UI.addDays(new Date(), 28));
    var bookings = [];
    try { bookings = (await window.API.bookings({ as_coach: 1, date_from: from, date_to: to })).bookings || []; } catch (e) {}
    bookings = bookings.filter(function (b) { return ["confirmed", "held", "completed", "requested", "proposed"].indexOf(b.status) >= 0; });
    bookings.sort(function (a, b) { return new Date(a.starts_at) - new Date(b.starts_at); });
    var wrap = el("div", {});
    wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:12px" }, [
      el("h1", { style: "margin:0", text: "Schedule" }),
      el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "+ Book", onclick: bookForClient }),
    ]));
    if (!bookings.length) wrap.appendChild(el("div", { class: "cf-empty", text: "Nothing booked in the next 4 weeks." }));
    else {
      // group by day
      var byDay = {};
      bookings.forEach(function (b) { var d = (b.starts_at || "").slice(0, 10); (byDay[d] = byDay[d] || []).push(b); });
      Object.keys(byDay).sort().forEach(function (d) {
        wrap.appendChild(el("div", { class: "cf-muted", style: "font-weight:700;font-size:.8rem;margin:14px 4px 6px;text-transform:uppercase;letter-spacing:.03em", text: UI.fmtDate(d) }));
        var c = el("div", { class: "cf-card", style: "padding:6px 14px" });
        var l = el("div", { class: "cf-list" }); byDay[d].forEach(function (b) { l.appendChild(eventRow(b)); }); c.appendChild(l);
        wrap.appendChild(c);
      });
    }
    // Time off
    var toCard = card([el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:6px" }, [
      el("h2", { style: "margin:0", text: "Time off" }),
      el("button", { class: "cf-btn cf-btn-sm", text: "+ Block time", onclick: timeOffModal }),
    ])], "");
    toCard.style.marginTop = "16px";
    toCard.appendChild(el("div", { id: "coach-timeoff", class: "cf-loading", text: "Loading…" }));
    wrap.appendChild(toCard);
    set(wrap);
    loadTimeOff();
  }
  async function loadTimeOff() {
    var box = document.getElementById("coach-timeoff"); if (!box) return;
    try {
      var rows = (await window.CoachAPI.timeOff()).time_off || [];
      UI.clear(box);
      if (!rows.length) { box.appendChild(el("div", { class: "cf-empty", text: "No time blocked." })); return; }
      var l = el("div", { class: "cf-list" });
      rows.forEach(function (r) {
        l.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: UI.fmtDate(r.starts_at) + " – " + UI.fmtDate(r.ends_at) }), el("div", { class: "cf-item-s", text: r.reason || "" })]),
          el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Remove", onclick: function () { window.CoachAPI.deleteTimeOff(r.id).then(function () { UI.toast("Removed.", "info"); loadTimeOff(); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); } }),
        ]));
      });
      box.appendChild(l);
    } catch (e) { UI.clear(box); box.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
  }
  async function timeOffModal() {
    var m = modal("Block time off");
    var res = await ensureCoachResource();
    var s = el("input", { class: "cf-input", type: "datetime-local" });
    var e = el("input", { class: "cf-input", type: "datetime-local" });
    var reason = el("input", { class: "cf-input", placeholder: "Reason (optional)" });
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "From" }), s]));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "To" }), e]));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Reason" }), reason]));
    m.body.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;gap:8px;margin-top:10px" }, [
      el("button", { class: "cf-btn", text: "Close", onclick: m.close }),
      el("button", { class: "cf-btn cf-btn-primary", text: "Block", onclick: function () {
        if (!s.value || !e.value || !res) { UI.toast("Pick a from/to time.", "warn"); return; }
        window.TFAuth.apiJSON("/api/diary/time-off", { method: "POST", body: { resource_id: res, starts_at: new Date(s.value).toISOString(), ends_at: new Date(e.value).toISOString(), reason: reason.value } })
          .then(function () { UI.toast("Time blocked.", "info"); m.close(); loadTimeOff(); }, function (er) { UI.toast(UI.errMsg(er), "error"); });
      } }),
    ]));
  }
  async function ensureCoachResource() {
    if (COACH_RES) return COACH_RES;
    try { var rs = (await window.API.resources()).resources || []; var mine = rs.filter(function (r) { return r.kind === "coach" && String(r.coach_user_id) === String(principal.user_id); })[0]; COACH_RES = mine ? mine.id : null; } catch (e) {}
    return COACH_RES;
  }

  // Book a client in (lesson with me, or a court) — the on-behalf flow.
  async function bookForClient() {
    var m = modal("Book a client in");
    var email = el("input", { class: "cf-input", type: "email", placeholder: "Client email (or leave blank for a guest)" });
    var guest = el("input", { class: "cf-input", placeholder: "Guest name (if no account)" });
    var start = el("input", { class: "cf-input", type: "datetime-local" });
    var dur = el("select", { class: "cf-input" }, [30, 45, 60, 90, 120].map(function (d) { return el("option", { value: String(d), text: d + " min" }); })); dur.value = "60";
    m.body.appendChild(el("p", { class: "cf-muted", style: "margin:0 0 8px;font-size:.85rem", text: "Books a lesson with you (auto-confirmed). A court is assigned automatically." }));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Client email" }), email]));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "…or guest name" }), guest]));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "When" }), start]));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Duration" }), dur]));
    m.body.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;gap:8px;margin-top:10px" }, [
      el("button", { class: "cf-btn", text: "Close", onclick: m.close }),
      el("button", { class: "cf-btn cf-btn-primary", text: "Book", onclick: async function () {
        if (!start.value) { UI.toast("Pick a time.", "warn"); return; }
        var res = await ensureCoachResource();
        if (!res) { UI.toast("Set your weekly hours first (Setup).", "warn"); return; }
        var st = new Date(start.value), en = new Date(st.getTime() + parseInt(dur.value, 10) * 60000);
        var body = { booking_type: "lesson", resource_id: res, coach_user_id: principal.user_id,
          starts_at: st.toISOString(), ends_at: en.toISOString(), settlement_mode: "at_court" };
        if (email.value.trim()) body.for_email = email.value.trim();
        else if (guest.value.trim()) body.for_guest_name = guest.value.trim();
        else { UI.toast("Add a client email or guest name.", "warn"); return; }
        window.API.createBooking(body).then(function () { UI.toast("Booked.", "info"); m.close(); route(); }, function (e) { UI.toast(UI.errMsg(e), "error"); });
      } }),
    ]));
  }

  // ---- CLIENTS (list → full record) ---------------------------------------
  async function renderClients() {
    ensureMonth(); loading();
    var clients = [], money = {};
    try {
      var pair = await Promise.all([window.CoachAPI.clients({}), window.CoachAPI.statement(MONTH).catch(function () { return { clients: [] }; })]);
      clients = pair[0].clients || [];
      ((pair[1] && pair[1].clients) || []).forEach(function (m) { money[String(m.client_user_id)] = m; });
    } catch (e) {}
    var wrap = el("div", {});
    wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:8px" }, [
      el("h1", { style: "margin:0", text: "Clients" }), monthNav(renderClients),
    ]));
    wrap.appendChild(el("p", { class: "cf-muted", style: "margin:-2px 0 12px;font-size:.85rem", text: "Everyone who trains with you — what they've paid and still owe this month. Tap for their full record." }));
    if (!clients.length) wrap.appendChild(el("div", { class: "cf-empty", text: "No clients yet." }));
    else {
      var c = el("div", { class: "cf-card", style: "padding:6px 14px" }), l = el("div", { class: "cf-list" });
      clients.forEach(function (cl) {
        var m = money[String(cl.user_id)] || {};
        l.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { go("#/client/" + cl.user_id); } }, [
          el("div", { class: "cf-avatar", style: "width:34px;height:34px;font-size:.8rem", text: clInitials(cl) }),
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: clName(cl) }),
            el("div", { class: "cf-item-s", text: (cl.lessons_count || 0) + " lessons · paid " + money2(m.paid_minor) }),
          ]),
          (m.owed_minor ? el("span", { class: "cf-chip held", text: money2(m.owed_minor) }) : el("span", { class: "cf-muted", text: "›" })),
        ]));
      });
      c.appendChild(l); wrap.appendChild(c);
    }
    set(wrap);
  }
  function clName(c) { return [c.first_name, c.surname].filter(Boolean).join(" ").trim() || c.email || "Client"; }
  function clInitials(c) { var n = clName(c).split(/\s+/); return ((n[0] || "C")[0] + (n.length > 1 ? n[n.length - 1][0] : "")).toUpperCase(); }

  // The FULL client record — the heart of the coach app.
  async function renderClient(userId) {
    ensureMonth(); loading();
    var c;
    try { c = (await window.CoachAPI.client(userId, MONTH)).client; } catch (e) { set(el("div", {}, [backBar("Clients", "#/clients"), el("div", { class: "cf-empty", text: UI.errMsg(e) })])); return; }
    var m = c.money || {}, cur = m.currency || "ZAR";
    var wrap = el("div", {});
    wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:8px" }, [backBar("Clients", "#/clients"), monthNav(function () { renderClient(userId); })]));

    // Header
    var head = card([
      el("div", { class: "cf-detail-h" }, [
        el("div", { class: "cf-row", style: "gap:10px;align-items:center" }, [
          el("div", { class: "cf-avatar", text: clInitials(c) }),
          el("div", {}, [el("h1", { style: "margin:0;font-size:1.25rem", text: clName(c) }),
            el("div", { class: "cf-muted", style: "font-size:.85rem", text: [c.email, c.phone].filter(Boolean).join(" · ") || "—" })]),
        ]),
        el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Invoice →", onclick: function () { issueInvoice(userId, c); } }),
      ]),
    ]);
    head.appendChild(el("div", { style: "margin-top:12px" }, [window.CRMUI.stats([
      { value: (c.lessons_count || 0), label: "Lessons" },
      { value: money(m.paid_minor, cur), label: "Paid (mo)" },
      { value: money(m.owed_minor, cur), label: "Owed" },
      { value: money(m.written_off_minor, cur), label: "Written off" },
    ])]));
    wrap.appendChild(head);

    // Owed & written-off (with actions)
    wrap.appendChild(card([window.CRMUI.sectionHead("Owed & written-off"), window.CRMUI.lineItems((c.arrears || []), {
      currency: cur, label: function () { return "Lesson"; }, sub: function (it) { return it.starts_at ? UI.fmtDate(it.starts_at) : ""; },
      empty: "Nothing outstanding.",
      actions: [
        { label: "Collected", tone: "primary", onClick: function (it) { arr(it.id, "collect", userId); } },
        { label: "Discount", onClick: function (it) { arr(it.id, "discount", userId, it); } },
        { label: "Write off", tone: "danger", onClick: function (it) { arr(it.id, "writeoff", userId); } },
      ],
    })], "cf-mt"));

    // Upcoming + sessions this month → each drills to the event story
    var up = (c.upcoming || []);
    var upC = card([window.CRMUI.sectionHead("Upcoming")], "cf-mt");
    if (!up.length) upC.appendChild(el("div", { class: "cf-empty", text: "No upcoming sessions." }));
    else { var ul = el("div", { class: "cf-list" }); up.forEach(function (h) { ul.appendChild(sessionRow(h)); }); upC.appendChild(ul); }
    wrap.appendChild(upC);

    var ym = MONTH, hist = (c.history || []).filter(function (h) { return (h.starts_at || "").slice(0, 7) === ym; });
    var hC = card([window.CRMUI.sectionHead("Sessions in " + monthLabel(ym))], "cf-mt");
    if (!hist.length) hC.appendChild(el("div", { class: "cf-empty", text: "No sessions this month." }));
    else { var hl = el("div", { class: "cf-list" }); hist.forEach(function (h) { hl.appendChild(sessionRow(h)); }); hC.appendChild(hl); }
    wrap.appendChild(hC);
    set(wrap);
  }
  // A session row inside a client record (has booking_id) → event story.
  function sessionRow(h) {
    var tappable = !!h.booking_id;
    var node = el("div", { class: "cf-item" + (tappable ? " cf-item-tap" : "") }, [
      el("span", { class: "cf-chip " + (h.kind || ""), text: typeLabel(h.kind) }),
      el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: UI.fmtDate(h.starts_at) }), el("div", { class: "cf-item-s", text: (function () { try { return UI.fmtTime(h.starts_at); } catch (e) { return ""; } })() })]),
      statusChip(h.status),
    ]);
    if (tappable) node.addEventListener("click", function () { go("#/event/" + h.booking_id); });
    return node;
  }
  async function arr(id, action, userId, it) {
    try {
      if (action === "collect") { await window.CoachAPI.arrearsCollected(id); UI.toast("Marked collected.", "info"); }
      else if (action === "discount") { var v = window.prompt("New amount (e.g. 250.00):", (((it && it.gross_minor) || 0) / 100).toFixed(2)); if (v === null) return; var f = parseFloat(v); if (isNaN(f) || f < 0) { UI.toast("Enter a valid amount.", "warn"); return; } await window.CoachAPI.arrearsAdjust(id, { gross_minor: Math.round(f * 100) }); UI.toast("Discounted.", "info"); }
      else { var r = window.prompt("Write off this lesson? Reason (shown to the client & club):", ""); if (r === null) return; await window.CoachAPI.arrearsAdjust(id, { status: "written_off", reason: r }); UI.toast("Written off.", "info"); }
      renderClient(userId);
    } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  async function issueInvoice(userId, c) {
    if (!window.confirm("Send " + clName(c) + " their statement for " + monthLabel(MONTH) + "? They'll be notified with the amount owed + a pay link.")) return;
    try { var res = await window.CoachAPI.issueInvoice(userId, MONTH); UI.toast(res.notified ? "Statement sent — " + money(res.owed_minor) + " owed." : "Nothing owed to send.", "info"); window.open("/invoice.html?client=" + encodeURIComponent(userId) + "&month=" + encodeURIComponent(MONTH), "_blank"); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  // ---- EVENT STORY (the drill-through heart) -------------------------------
  async function renderEvent(id) {
    loading();
    var b;
    try { b = (await window.CoachAPI.bookingStory(id)).booking; } catch (e) { set(el("div", {}, [backBar("Back"), el("div", { class: "cf-empty", text: UI.errMsg(e) })])); return; }
    var ch = b.charge || {}, cur = ch.currency || "ZAR", cl = b.client || {};
    var wrap = el("div", {});
    wrap.appendChild(backBar("Back"));
    var head = card([
      el("div", { class: "cf-detail-h" }, [
        el("div", {}, [el("span", { class: "cf-chip " + b.booking_type, text: typeLabel(b.booking_type) + (b.duration_minutes ? " · " + b.duration_minutes + " min" : "") }),
          el("h1", { style: "margin:8px 0 2px;font-size:1.3rem", text: UI.fmtDate(b.starts_at) }), el("div", { class: "cf-muted", text: timeRange(b) })]),
        statusChip(b.status),
      ]),
    ]);
    var det = el("div", { style: "margin-top:6px" });
    // Client (tap → their record; call / email)
    if (cl.name) {
      var contacts = el("div", { class: "cf-row", style: "gap:8px;margin-top:4px;flex-wrap:wrap" });
      if (cl.phone) contacts.appendChild(el("a", { class: "cf-btn cf-btn-sm cf-btn-ghost", href: "tel:" + cl.phone, text: "📞 Call" }));
      if (cl.email) contacts.appendChild(el("a", { class: "cf-btn cf-btn-sm cf-btn-ghost", href: "mailto:" + cl.email, text: "✉ Email" }));
      if (cl.user_id) contacts.appendChild(el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Full record ›", onclick: function () { go("#/client/" + cl.user_id); } }));
      det.appendChild(kv("Client", el("div", {}, [el("div", { style: "font-weight:600", text: cl.name }), el("div", { class: "cf-muted", style: "font-size:.85rem", text: [cl.email, cl.phone].filter(Boolean).join(" · ") }), contacts])));
    }
    if (b.venue && (b.venue.club_name || b.court_name)) det.appendChild(kv("Where", el("div", {}, [el("div", { text: [b.venue.club_name, b.court_name].filter(Boolean).join(" · ") || "—" }), b.venue.address ? el("div", { class: "cf-muted", style: "font-size:.85rem", text: b.venue.address }) : null].filter(Boolean))));
    if (b.players && b.players.length) det.appendChild(kv("Players", b.players.map(function (p) { return p.name + (p.attended === true ? " ✓" : p.attended === false ? " ✗" : ""); }).join(", ")));
    det.appendChild(kv("Charge", el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [el("span", { style: "font-weight:700", text: ch.status === "covered" ? "Covered" : money(ch.amount_minor, cur) }), statusChip(ch.status)])));
    head.appendChild(det);
    wrap.appendChild(head);

    // Coach actions
    var a = el("div", { class: "cf-row", style: "gap:8px;flex-wrap:wrap;margin-top:14px" });
    var can = b.can || {};
    if (can.accept) a.appendChild(btn("Accept", "primary", function () { act(function () { return window.API.acceptBooking(b.id); }, "Confirmed.", function () { renderEvent(id); }); }));
    if (can.propose) a.appendChild(btn("Propose time", "", function () { proposeModal(b.id, function () { renderEvent(id); }); }));
    if (can.mark_completed) a.appendChild(btn("Mark completed", "primary", function () { act(function () { return window.API.setBookingStatus(b.id, { status: "completed" }); }, "Marked completed.", function () { renderEvent(id); }); }));
    if (can.mark_no_show) a.appendChild(btn("No-show", "", function () { act(function () { return window.API.setBookingStatus(b.id, { status: "no_show" }); }, "Marked no-show.", function () { renderEvent(id); }); }));
    if (can.reschedule) a.appendChild(btn("Reschedule", "", function () { rescheduleModal(b, function () { renderEvent(id); }); }));
    if (can.add_to_calendar) a.appendChild(el("a", { class: "cf-btn cf-btn-sm cf-btn-ghost", href: b.ics_url, text: "Add to calendar" }));
    if (can.decline) a.appendChild(btn("Decline", "danger", function () { act(function () { return window.API.declineBooking(b.id, {}); }, "Declined.", function () { history.back(); }); }));
    if (can.cancel && !can.decline) a.appendChild(btn("Cancel", "danger", function () { if (!window.confirm("Cancel this session?")) return; act(function () { return window.API.cancelBooking(b.id, { reason: "coach cancelled" }); }, "Cancelled.", function () { history.back(); }); }));
    if (a.childNodes.length) wrap.appendChild(a);
    set(wrap);
  }
  function btn(text, tone, onclick) { return el("button", { class: "cf-btn cf-btn-sm" + (tone ? " cf-btn-" + tone : ""), text: text, onclick: onclick }); }
  function proposeModal(id, then) {
    var m = modal("Propose a time");
    var s = el("input", { class: "cf-input", type: "datetime-local" });
    var dur = el("select", { class: "cf-input" }, [30, 45, 60, 90, 120].map(function (d) { return el("option", { value: String(d), text: d + " min" }); })); dur.value = "60";
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "New time" }), s]));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Duration" }), dur]));
    m.body.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;gap:8px;margin-top:10px" }, [
      el("button", { class: "cf-btn", text: "Close", onclick: m.close }),
      el("button", { class: "cf-btn cf-btn-primary", text: "Propose", onclick: function () { if (!s.value) { UI.toast("Pick a time.", "warn"); return; } var st = new Date(s.value), en = new Date(st.getTime() + parseInt(dur.value, 10) * 60000); window.API.proposeTime(id, { starts_at: st.toISOString(), ends_at: en.toISOString() }).then(function () { UI.toast("Proposed.", "info"); m.close(); (then || route)(); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); } }),
    ]));
  }
  function rescheduleModal(b, then) {
    var m = modal("Reschedule");
    var s = el("input", { class: "cf-input", type: "datetime-local", value: toLocal(b.starts_at) });
    var dur = el("select", { class: "cf-input" }, [30, 45, 60, 90, 120].map(function (d) { return el("option", { value: String(d), text: d + " min" }); })); dur.value = String(b.duration_minutes || 60);
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "New time" }), s]));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Duration" }), dur]));
    m.body.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;gap:8px;margin-top:10px" }, [
      el("button", { class: "cf-btn", text: "Close", onclick: m.close }),
      el("button", { class: "cf-btn cf-btn-primary", text: "Reschedule", onclick: function () { if (!s.value) { UI.toast("Pick a time.", "warn"); return; } var st = new Date(s.value), en = new Date(st.getTime() + parseInt(dur.value, 10) * 60000); window.API.rescheduleBooking(b.id, { starts_at: st.toISOString(), ends_at: en.toISOString(), scope: "this" }).then(function () { UI.toast("Rescheduled.", "info"); m.close(); (then || route)(); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); } }),
    ]));
  }

  // ---- MONEY (statement rollup + disputes + activity + my account) --------
  async function renderMoney() {
    ensureMonth(); loading();
    var st = {}, disputes = [], activity = [];
    try { st = await window.CoachAPI.statement(MONTH); } catch (e) {}
    try { disputes = (await window.CoachAPI.refundRequests("pending")).requests || []; } catch (e) {}
    try { activity = (await window.CoachAPI.activity()).activity || []; } catch (e) {}
    var t = st.totals || {}, cur = st.currency || "ZAR";
    var wrap = el("div", {});
    wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:8px" }, [el("h1", { style: "margin:0", text: "Money" }), monthNav(renderMoney)]));

    // My account
    var acct = card([window.CRMUI.stats([
      { value: money(t.paid_minor, cur), label: "Collected (net)" },
      { value: money(t.owed_minor, cur), label: "Outstanding" },
      { value: money(t.rent_minor, cur), label: "Rent" },
      { value: money(t.balance_minor, cur), label: "Balance" },
    ])]);
    if (t.written_off_minor) acct.appendChild(el("div", { class: "cf-muted", style: "margin-top:6px;font-size:.85rem", text: "Written off this month: " + money(t.written_off_minor, cur) }));
    wrap.appendChild(acct);

    // Disputes
    if (disputes.length) {
      var dc = card([el("h2", { style: "margin:0 0 6px", text: "Refund requests" }), el("p", { class: "cf-muted", style: "margin:-2px 0 8px;font-size:.85rem", text: "A client asked for a refund on your lesson — you decide." })]);
      dc.appendChild(window.CRMUI.lineItems(disputes.map(function (r) { return { id: r.id, gross_minor: (r.amount_minor != null ? r.amount_minor : r.order_amount_minor), currency: r.currency_code, _n: r.requester_name || "A client", _s: [r.item_description || "Lesson", r.reason ? "“" + r.reason + "”" : ""].filter(Boolean).join(" · ") }; }), {
        currency: cur, label: function (it) { return it._n; }, sub: function (it) { return it._s; }, empty: "None.",
        actions: [{ label: "Approve", tone: "primary", onClick: function (it) { decideDispute(it.id, "approve"); } }, { label: "Decline", tone: "danger", onClick: function (it) { decideDispute(it.id, "decline"); } }],
      }));
      wrap.appendChild(dc);
    }

    // Per-client rollup (tap → client record)
    var byClient = card([window.CRMUI.sectionHead("By client")]);
    byClient.appendChild(el("p", { class: "cf-muted", style: "margin:-6px 0 8px;font-size:.85rem", text: "Tap a client to manage or invoice them." }));
    byClient.appendChild(window.CRMUI.statementTable(st.clients, { nameKey: "client_name", nameLabel: "Client", currency: cur, onRow: function (r) { if (r.client_user_id) go("#/client/" + r.client_user_id); } }));
    wrap.appendChild(byClient);

    // Activity
    var ac = card([el("h2", { style: "margin:0 0 6px", text: "Activity" }), el("p", { class: "cf-muted", style: "margin:-2px 0 10px;font-size:.85rem", text: "Every lesson, collection, refund and adjustment." })]);
    ac.appendChild(window.CRMUI.activityFeed(activity, { empty: "No activity yet." }));
    wrap.appendChild(ac);
    set(wrap);
  }
  async function decideDispute(id, action) {
    var isA = action === "approve";
    var note = window.prompt(isA ? "Approve this refund? The client is refunded and your commission reversed.\n\nNote (optional):" : "Decline this refund?\n\nReason (shown to the client):", "");
    if (note === null) return;
    try { if (isA) await window.CoachAPI.approveRefund(id, { note: note }); else await window.CoachAPI.declineRefund(id, { note: note }); UI.toast(isA ? "Approved." : "Declined.", "info"); renderMoney(); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  // ---- SETUP (services + classes + commission + profile/hours links) ------
  async function renderSetup() {
    loading();
    var services = [], commission = {};
    try { services = (await window.TFAuth.apiJSON("/api/services")).services || []; } catch (e) {}
    try { commission = await window.CoachAPI.commission(); } catch (e) {}
    var wrap = el("div", {});
    wrap.appendChild(el("h1", { style: "margin:0 0 12px", text: "Setup" }));

    // You
    var you = card([el("h2", { style: "margin:0 0 8px", text: "You" })]);
    [["Edit profile", "#/profile"], ["Weekly hours", "#/hours"]].forEach(function (x) {
      you.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { go(x[1]); } }, [el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: x[0] })]), el("span", { class: "cf-muted", text: "›" })]));
    });
    wrap.appendChild(you);

    // Services & packages
    var sc = card([el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:6px" }, [el("h2", { style: "margin:0", text: "Services & packages" }), el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "+ New", onclick: newServiceModal })])]);
    var mine = services.filter(function (s) { return s.coach_user_id && String(s.coach_user_id) === String(principal.user_id); });
    var list = mine.length ? mine : services;
    if (!list.length) sc.appendChild(el("div", { class: "cf-empty", text: "No services yet — add your first." }));
    else { var sl = el("div", { class: "cf-list" }); list.forEach(function (s) {
      sl.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { go("#/service/" + s.id); } }, [
        el("span", { class: "cf-chip " + (s.service_kind || s.kind || ""), text: (s.service_kind || s.kind || "service") }),
        el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: s.name }), el("div", { class: "cf-item-s", text: (s.variation_count || (s.variations || []).length || 0) + " option(s) · from " + money(s.from_amount_minor) })]),
        el("span", { class: "cf-muted", text: "›" }),
      ]));
    }); sc.appendChild(sl); }
    wrap.appendChild(sc);

    // Commission (read-only)
    if (commission && commission.effective_pct != null) {
      wrap.appendChild(card([
        el("h2", { style: "margin:0 0 6px", text: "Club commission" }),
        el("p", { class: "cf-muted", style: "margin:0;font-size:.88rem", text: "The club keeps " + commission.effective_pct + "% of your coaching. You keep " + (100 - commission.effective_pct) + "%. (Set by the club.)" }),
      ]));
    }
    set(wrap);
  }
  function newServiceModal() {
    var m = modal("New service");
    var name = el("input", { class: "cf-input", placeholder: "e.g. Private lesson" });
    var dur = el("select", { class: "cf-input" }, [30, 45, 60, 90, 120].map(function (d) { return el("option", { value: String(d), text: d + " min" }); })); dur.value = "60";
    var price = el("input", { class: "cf-input", type: "number", placeholder: "Price e.g. 400" });
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Name" }), name]));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Duration" }), dur]));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Price (per session)" }), price]));
    m.body.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;gap:8px;margin-top:10px" }, [
      el("button", { class: "cf-btn", text: "Close", onclick: m.close }),
      el("button", { class: "cf-btn cf-btn-primary", text: "Create", onclick: function () {
        var p = parseFloat(price.value); if (!name.value.trim() || isNaN(p) || p < 0) { UI.toast("Add a name and price.", "warn"); return; }
        window.CoachAPI.createService({ name: name.value.trim(), duration_minutes: parseInt(dur.value, 10), amount_minor: Math.round(p * 100) })
          .then(function (res) { UI.toast("Service created.", "info"); m.close(); if (res && res.service && res.service.product_id) go("#/service/" + res.service.product_id); else renderSetup(); }, function (e) { UI.toast(UI.errMsg(e), "error"); });
      } }),
    ]));
  }
  // Mount the full-screen Service Editor (save & close → back to Setup).
  function renderService(id) {
    UI.clear(view);
    if (window.ServiceEditor) window.ServiceEditor.open(id, { host: view, onClose: function () { go("#/setup"); } });
    else { location.href = "/coach.html"; }
  }

  // ---- PROFILE + HOURS (full-screen editors, Save & close) ----------------
  async function renderProfilePage() {
    loading();
    var data = {};
    try { var ob = await window.CoachAPI.onboarding(); data = (ob && ob.profile) || {}; try { var pr = await window.CoachAPI.profile(); if (pr && pr.profile) data = Object.assign({}, data, pr.profile); } catch (e) {} } catch (e) {}
    var wrap = el("div", {});
    wrap.appendChild(backBar("Setup", "#/setup"));
    wrap.appendChild(el("h1", { style: "margin:0 0 12px", text: "Edit profile" }));
    var host = el("div", {}); wrap.appendChild(host);
    // sign out
    wrap.appendChild(el("div", { style: "margin-top:14px;text-align:center" }, [el("button", { class: "cf-btn cf-btn-ghost", text: "Sign out", onclick: function () { window.TFAuth.signOut().then(function () { location.reload(); }); } })]));
    set(wrap);
    if (window.CoachUI) window.CoachUI.profile(host, data, { saveLabel: "Save & close", onSaved: function () { UI.toast("Saved.", "info"); reloadProfile(); go("#/setup"); } });
    else host.appendChild(el("div", { class: "cf-empty", text: "Profile editor unavailable." }));
  }
  async function renderHoursPage() {
    loading();
    var data = {};
    try { var ob = await window.CoachAPI.onboarding(); data = (ob && ob.hours) || {}; } catch (e) {}
    var wrap = el("div", {});
    wrap.appendChild(backBar("Setup", "#/setup"));
    wrap.appendChild(el("h1", { style: "margin:0 0 12px", text: "Weekly hours" }));
    var host = el("div", {}); wrap.appendChild(host); set(wrap);
    if (window.CoachUI) window.CoachUI.hours(host, data, { saveLabel: "Save & close", onSaved: function () { UI.toast("Hours saved.", "info"); go("#/setup"); } });
    else host.appendChild(el("div", { class: "cf-empty", text: "Hours editor unavailable." }));
  }
  async function reloadProfile() { try { PROFILE = (await window.CoachAPI.profile()).profile || PROFILE; paintAvatar(); } catch (e) {} }

  // ---- modal + misc --------------------------------------------------------
  function modal(title) {
    var bg = el("div", { class: "cf-modal-bg" }), body = el("div", {});
    bg.appendChild(el("div", { class: "cf-modal cf-modal-lg" }, [el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:6px" }, [el("h2", { style: "margin:0", text: title }), el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "✕", onclick: function () { close(); } })]), body]));
    document.body.appendChild(bg);
    function close() { if (bg.parentNode) document.body.removeChild(bg); }
    return { body: body, close: close };
  }
  function toLocal(iso) { try { var d = new Date(iso), p = function (n) { return (n < 10 ? "0" : "") + n; }; return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + "T" + p(d.getHours()) + ":" + p(d.getMinutes()); } catch (e) { return ""; } }

  window.CoachApp = { start: start };
})();

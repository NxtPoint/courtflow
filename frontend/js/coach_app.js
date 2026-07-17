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
    // Fetch the coach profile IN PARALLEL with whoami so first paint isn't gated on a second
    // cross-region round trip (Starter removed cold starts; the round-trips remain).
    var pendingProfile = window.CoachAPI.profile().catch(function () { return null; });
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
    try { var pf = await pendingProfile; if (pf) { PROFILE = pf.profile || {}; paintAvatar(); } } catch (e) {}
    route();
  }

  function coachName() { return (PROFILE && (PROFILE.display_name || [PROFILE.first_name, PROFILE.surname].filter(Boolean).join(" "))) || (principal && principal.email ? principal.email.split("@")[0] : "Coach"); }
  function initials() { var n = coachName().trim().split(/\s+/); return ((n[0] || "C")[0] + (n.length > 1 ? n[n.length - 1][0] : "")).toUpperCase(); }
  function paintAvatar() { var a = document.getElementById("cf-avatar"); if (a) a.textContent = initials(); }

  // ONE shared account menu (UI.menu) on the top-right avatar — Edit profile / Switch profile / Sign out.
  function openAccountMenu(anchor) {
    UI.menu(anchor, [
      { label: "Edit profile", onClick: function () { go("#/profile"); } },
      { label: "Switch profile", onClick: function () { window.TFAuth.signOut().then(function () { location.href = "/login"; }); } },
      "-",
      { label: "Sign out", tone: "danger", onClick: function () { window.TFAuth.signOut().then(function () { location.reload(); }); } },
    ]);
  }

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
        el("div", { class: "cf-brand", style: "cursor:pointer", title: "Home", onclick: function () { go("#/"); } }, [el("span", { class: "cf-logo", text: "NP" }), el("span", { text: "Coach" })]),
        el("span", { class: "cf-spacer" }),
        el("div", { class: "cf-bell-host", id: "cf-bell" }),
        el("div", { class: "cf-avatar", id: "cf-avatar", text: initials(), title: "Account", onclick: function (ev) { openAccountMenu(ev.currentTarget); } }),
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
  function loadScript(src) { return new Promise(function (res, rej) { var s = document.createElement("script"); s.src = src; s.onload = res; s.onerror = function () { rej(new Error("Failed to load " + src)); }; document.head.appendChild(s); }); }
  async function ensureClassDeps() { if (!window.ClassUI) await loadScript("/js/class_ui.js"); }

  // ---- router --------------------------------------------------------------
  function route() {
    var parts = (location.hash || "").replace(/^#\/?/, "").split("/").filter(Boolean);
    var top = parts[0] || "home";
    setActive(["home", "schedule", "clients", "money", "setup"].indexOf(top) >= 0 ? top :
      (top === "client" ? "clients" : ((top === "event" || top === "class" || top === "roster") ? "schedule" : (top === "service" ? "setup" : ""))));
    window.scrollTo(0, 0);
    if (top === "home") return renderHome();
    if (top === "schedule") return renderSchedule();
    if (top === "clients") return renderClients();
    if (top === "client") return renderClient(parts[1]);
    if (top === "event") return renderEvent(parts[1]);
    if (top === "roster") return renderRoster(parts[1]);
    if (top === "class") return renderClassEvent(parts[1]);
    if (top === "money") return renderMoney();
    if (top === "setup") return renderSetup(parts[1]);
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
    setTimeout(function () { if (n.isConnected && n.textContent === "Loading…") n.textContent = "Still loading — one moment…"; }, 7000);
  }
  var card = window.UI.card, backBar = window.UI.backBar, kv = window.UI.kv;   // shared (FRONTEND-STANDARDISATION Wave 1)
  var TYPE_LABEL = { court: "Court", lesson: "Lesson", class: "Class" };
  function typeLabel(t) { return TYPE_LABEL[t] || "Session"; }
  function timeRange(b) { try { return UI.fmtTime(b.starts_at) + "–" + UI.fmtTime(b.ends_at); } catch (e) { return ""; } }
  var statusChip = window.UI.statusChip;   // shared status vocabulary (Wave 1/3 consolidation)
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
      el("div", {}, [el("h1", { text: greet() + ", " + coachName().split(" ")[0] }), el("p", { text: "Here's your business." }),
        el("div", { class: "cf-row", style: "gap:8px;margin-top:10px;flex-wrap:wrap" }, [
          el("button", { class: "cf-btn cf-btn-sm", text: "Edit profile", onclick: function () { go("#/profile"); } }),
        ])]),
      el("span", { class: "cf-greet-plan", text: monthLabel(MONTH) }),
    ]));

    // Business KPIs
    var kpiCard = card([
      el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:8px" }, [
        el("h2", { style: "margin:0", text: "This month" }), monthNav(renderHome),
      ]),
      window.CRMUI.stats([
        { value: money(k.billed_minor, cur), label: "Total billed" },
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
      el("button", { class: "cf-btn cf-btn-primary cf-btn-block", text: "+ Book a client in", onclick: function () { bookForClient(false); } }),
      el("button", { class: "cf-btn cf-btn-block", text: "Log a past session", onclick: function () { bookForClient(true); } }),
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
  // ---- SCHEDULE — the shared Widgets.Calendar over the WHOLE CLUB (courts · lessons · classes),
  // so a coach sees court usage + open gaps to book, not just their own diary. Court/Coach filters
  // (pick yourself to narrow to your own lessons). A lesson YOU run drills into the ONE event story;
  // every other block is view-only occupancy. GOLDEN RULE: the SAME calendar the admin uses — only
  // the data adapter (master feed) + filter lists differ. (Was a self-only hand-rolled week grid.)
  var DIARY_LISTS = null;
  async function ensureDiaryLists() {
    if (DIARY_LISTS) return DIARY_LISTS;
    var courts = [], coaches = [];
    try {
      var rs = (await window.API.resources()).resources || [];
      courts = rs.filter(function (r) { return r.kind === "court" && r.is_active !== false; })
                 .map(function (r) { return { id: r.id, name: r.name || "Court" }; });
      coaches = rs.filter(function (r) { return r.kind === "coach" && r.coach_user_id; })
                  .map(function (r) { return { id: r.coach_user_id, name: r.name || "Coach" }; });
    } catch (e) {}
    DIARY_LISTS = { courts: courts, coaches: coaches };
    return DIARY_LISTS;
  }
  async function renderSchedule() {
    var wrap = el("div", {});
    wrap.appendChild(el("div", { class: "cf-row", style: "align-items:center;gap:8px;margin-bottom:2px;flex-wrap:wrap" }, [
      el("h1", { style: "margin:0", text: "Schedule" }),
      el("span", { class: "cf-spacer" }),
      el("button", { class: "cf-btn cf-btn-sm", text: "Log past", onclick: function () { bookForClient(true); } }),
      el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "+ Book a client", onclick: function () { bookForClient(false); } }),
    ]));
    wrap.appendChild(el("p", { class: "cf-muted", style: "margin:0 0 6px;font-size:.85rem", text: "The whole club’s diary — courts, lessons & classes. Filter by court or coach (pick yourself) to spot open gaps to book." }));
    var calHost = el("div", {});
    wrap.appendChild(calHost);
    var toCard = card([el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:6px" }, [
      el("h2", { style: "margin:0", text: "Time off" }),
      el("button", { class: "cf-btn cf-btn-sm", text: "+ Block time", onclick: timeOffModal }),
    ])]);
    toCard.style.marginTop = "16px";
    toCard.appendChild(el("div", { id: "coach-timeoff", class: "cf-loading", text: "Loading…" }));
    wrap.appendChild(toCard);
    set(wrap);
    loadTimeOff();
    var lists = await ensureDiaryLists();
    window.Widgets.Calendar.mount(calHost, {
      grid: true,                 // Day view = the resource-timeline grid (courts + coaches as columns)
      date: UI.dateKey(new Date()),
      coachId: principal.user_id, // default to "just me"; clear the Coach dropdown to "All" for the whole club
      filterBar: { courts: lists.courts, coaches: lists.coaches },
      data: { events: function (r) { return window.API.master({ date_from: r.from, date_to: r.to }).then(function (x) { return x.events || []; }); } },
      onNavigate: function (ev) {
        var t = String(ev.booking_type || ev.kind || "").toLowerCase();
        var mine = String(ev.coach_user_id) === String(principal.user_id);
        // A coach can only open the event story for a lesson THEY run (the story API is self-scoped);
        // for everyone else's bookings we show read-only occupancy so they can still see the slot.
        if (t === "lesson" && ev.id && mine) { go("#/event/" + ev.id); return; }
        // A CLASS they run opens its ROSTER (their check-in / no-show list).
        if (t === "class" && ev.id && mine) { go("#/roster/" + ev.id); return; }
        var info = ev.resource_name || (t === "class" ? "Class" : (t === "lesson" ? "Lesson" : "Court booking"));
        try { info += " · " + UI.fmtRange(ev.starts_at, ev.ends_at); } catch (e) {}
        if (ev.status) info += " · " + ev.status;
        UI.toast(info, "info");
      },
    });
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
  // Book a client in = pick the client, then hand off to the ONE booking widget (window.BookFlow) in
  // on-behalf mode — the SAME flow clients use (service · duration · time · payment), coach locked to
  // self, no Yoco (staff collect at court / pack / account). GOLDEN RULE: one booking widget per role.
  async function bookForClient(backdate) {
    if (!window.BookFlow) { UI.toast("Booking module still loading — try again in a moment.", "warn"); return; }
    var m = modal(backdate ? "Log a past session" : "Book a client in");
    var selected = null;
    var searchInp = el("input", { class: "cf-input", placeholder: "Search client by name or email…" });
    var resultsBox = el("div", { class: "cf-list", style: "max-height:180px;overflow:auto" });
    var chosenBox = el("div", {});
    var guest = el("input", { class: "cf-input", placeholder: "Guest name (walk-in, no account)" });

    function renderChosen() {
      UI.clear(chosenBox);
      if (selected) {
        searchInp.style.display = "none"; resultsBox.style.display = "none"; guest.disabled = true;
        chosenBox.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: selected.name }),
            el("div", { class: "cf-item-s", text: [selected.email, selected.phone].filter(Boolean).join(" · ") || "—" }),
          ]),
          el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Change", onclick: function () { selected = null; searchInp.value = ""; UI.clear(resultsBox); renderChosen(); searchInp.focus(); } }),
        ]));
      } else {
        searchInp.style.display = ""; resultsBox.style.display = ""; guest.disabled = false;
      }
    }
    var tmr;
    searchInp.addEventListener("input", function () {
      clearTimeout(tmr);
      var q = searchInp.value.trim();
      if (q.length < 2) { UI.clear(resultsBox); return; }
      tmr = setTimeout(function () {
        window.CoachAPI.searchMembers(q).then(function (r) {
          UI.clear(resultsBox);
          var ms = (r && r.members) || [];
          if (!ms.length) { resultsBox.appendChild(el("div", { class: "cf-empty", style: "padding:8px", text: "No match — or use a guest name below." })); return; }
          ms.forEach(function (mem) {
            resultsBox.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { selected = mem; renderChosen(); } }, [
              el("div", { class: "cf-item-main" }, [
                el("div", { class: "cf-item-t", text: mem.name }),
                el("div", { class: "cf-item-s", text: [mem.email, mem.phone].filter(Boolean).join(" · ") }),
              ]),
            ]));
          });
        }, function () {});
      }, 250);
    });

    m.body.appendChild(el("p", { class: "cf-muted", style: "margin:0 0 8px;font-size:.85rem", text: backdate
      ? "Pick the client, then choose the lesson/class, the DAY it happened, time and how they'll pay — it bills them and credits you, without touching the calendar."
      : "Pick the client, then choose the service, time and payment on the next screen — the same booking flow clients use." }));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Client" }), searchInp, resultsBox, chosenBox]));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "…or guest name (walk-in, no account)" }), guest]));
    m.body.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;gap:8px;margin-top:10px" }, [
      el("button", { class: "cf-btn", text: "Close", onclick: m.close }),
      el("button", { class: "cf-btn cf-btn-primary", text: "Continue →", onclick: function () {
        var onBehalf = null;
        if (selected && selected.email) onBehalf = { name: selected.name, email: selected.email, user_id: selected.user_id };
        else if (guest.value.trim()) onBehalf = { name: guest.value.trim() };
        else { UI.toast("Search & pick a client, or enter a guest name.", "warn"); return; }
        m.close();
        window.BookFlow.start(principal, "lesson", {
          onBehalf: onBehalf,
          coachLock: principal.user_id,            // the coach books their OWN lessons
          backdate: !!backdate,                    // BACK-CAPTURE: log a lesson/class that already happened
          backTo: "#/schedule",
          onDone: function () { location.hash = "#/schedule"; route(); },
          // Auto-route to the client's prepaid pack with this coach (draw, not a new charge). The
          // coach endpoint is self-scoped, so the coachId arg is unused here.
          loadPackages: function (uid, coachId) { return window.CoachAPI.clientPackages(uid).then(function (r) { return (r && r.packages) || []; }); },
        });
      } }),
    ]));
    renderChosen();
  }

  // ---- CLIENTS (list → full record) ---------------------------------------
  async function renderClients() {
    ensureMonth(); loading();
    var clients = [], money = {}, packs = [];
    try {
      var pair = await Promise.all([
        window.CoachAPI.clients({}),
        window.CoachAPI.money(MONTH).catch(function () { return { clients: [] }; }),   // same fold as Money tab
        (window.CoachAPI.packages ? window.CoachAPI.packages() : Promise.resolve({ packages: [] })).catch(function () { return { packages: [] }; }),
      ]);
      clients = pair[0].clients || [];
      ((pair[1] && pair[1].clients) || []).forEach(function (m) { money[String(m.client_user_id)] = m; });
      packs = (pair[2] && pair[2].packages) || [];
    } catch (e) {}
    var wrap = el("div", {});
    wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:8px" }, [
      el("h1", { style: "margin:0", text: "Clients" }),
      el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [
        el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "+ New client", onclick: newClientModal }),
        monthNav(renderClients),
      ]),
    ]));
    wrap.appendChild(el("p", { class: "cf-muted", style: "margin:-2px 0 12px;font-size:.85rem", text: "Everyone who trains with you — what they've paid and still owe this month. Tap for their full record." }));

    // Prepaid packages — clients who bought a pack WITH you (already funded); a lesson you book for
    // them draws it down instead of raising a new charge. Tap → their full record.
    if (packs.length) {
      wrap.appendChild(el("h2", { style: "margin:4px 0 6px;font-size:1rem", text: "Prepaid packages" }));
      var pc = el("div", { class: "cf-card", style: "padding:6px 14px" }), pl = el("div", { class: "cf-list" });
      packs.forEach(function (p) {
        pl.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { if (p.client_user_id) go("#/client/" + p.client_user_id); } }, [
          el("div", { class: "cf-avatar", style: "width:34px;height:34px;font-size:.8rem", text: (p.client_name || "?").slice(0, 1).toUpperCase() }),
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: p.client_name || "Client" }),
            el("div", { class: "cf-item-s", text: (p.label || "Lesson pack") }),
          ]),
          el("span", { class: "cf-chip", text: p.sessions_remaining + " left" }),
        ]));
      });
      pc.appendChild(pl); wrap.appendChild(pc);
      wrap.appendChild(el("h2", { style: "margin:16px 0 6px;font-size:1rem", text: "All clients" }));
    }
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
          (m.outstanding_minor ? el("span", { class: "cf-chip held", text: money2(m.outstanding_minor) }) : el("span", { class: "cf-muted", text: "›" })),
        ]));
      });
      c.appendChild(l); wrap.appendChild(c);
    }
    set(wrap);
  }
  // The ONE shared new-client modal (CRMUI.createClientModal — same component the admin uses).
  function newClientModal() {
    window.CRMUI.createClientModal({
      onCreate: function (body) { return window.CoachAPI.createClient(body); },
      onDone: function (res) { if (res && res.user_id) go("#/client/" + res.user_id); else renderClients(); },
    });
  }
  function clName(c) { return [c.first_name, c.surname].filter(Boolean).join(" ").trim() || c.email || "Client"; }
  function clInitials(c) { var n = clName(c).split(/\s+/); return ((n[0] || "C")[0] + (n.length > 1 ? n[n.length - 1][0] : "")).toUpperCase(); }

  // The client record — the ONE shared widget (Widgets.ClientRecord), SAME as admin + client. COACH
  // scope is a strict FILTER: the composer returns only THIS coach's own bookings (events) + coaching
  // money fold + packages + coaching, and OMITS everything else server-side (membership, card payments,
  // full-club statement, dependents, refunds, PII, activity). Golden rule: one widget, role = config.
  function renderClient(userId) {
    var host = el("div", {});
    set(host);
    // The ONE widget owns its own month pager now — no external monthNav.
    window.Widgets.ClientRecord.mount(host, {
      scope: { id: userId, role: "coach" },
      back: { label: "Clients", hash: "#/clients" },
      fields: { showDetails: false, showDependents: false, showActivity: false, showEvents: false,
                showPackages: true, showCoaching: true, showBookings: true },
      data: { get: function (i, m) { return window.CoachAPI.client360(i, m).then(function (r) { return r.person; }); } },
      onNavigate: function (t) {
        if (!t || !t.id) return;
        if (t.kind === "person") go("#/client/" + t.id);
        else if (t.kind === "class") go("#/class/" + t.id);
        else go("#/event/" + t.id);   // lesson/court booking → the coach's ONE event story
      },
      actions: {
        // Coaching arrears — collect / discount (the same handler the event story uses).
        collect: { manual: true, run: function (it) { arr(it.id, "collect", function () { renderClient(userId); }); } },
        discount: { manual: true, run: function (it) { arr(it.id, "discount", function () { renderClient(userId); }, it); } },
      },
    });
  }
  async function arr(id, action, then, it) {
    try {
      if (!id) { UI.toast("No coaching charge to act on.", "warn"); return; }
      if (action === "collect") { await window.CoachAPI.arrearsCollected(id); UI.toast("Marked collected.", "info"); }
      else if (action === "discount") { var v = window.prompt("New amount (e.g. 250.00):", (((it && it.gross_minor) || 0) / 100).toFixed(2)); if (v === null) return; var f = parseFloat(v); if (isNaN(f) || f < 0) { UI.toast("Enter a valid amount.", "warn"); return; } await window.CoachAPI.arrearsAdjust(id, { gross_minor: Math.round(f * 100) }); UI.toast("Discounted.", "info"); }
      else { var r = window.prompt("Write off this lesson? Reason (shown to the client & club):", ""); if (r === null) return; await window.CoachAPI.arrearsAdjust(id, { status: "written_off", reason: r }); UI.toast("Written off.", "info"); }
      if (then) then();
    } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  // ---- EVENT STORY (the drill-through heart) -------------------------------
  // The ONE shared transaction detail (Widgets.TransactionDetail). Coach wires its action handlers;
  // proposeModal/rescheduleModal/arr below are the coach action UIs. (FRONTEND-STANDARDISATION Wave 2.)
  function renderEvent(id) {
    var host = el("div", {});
    set(host);
    window.Widgets.TransactionDetail.mount(host, {
      role: "coach",
      scope: { id: id },
      fields: { showCoach: false },   // the coach IS the coach — no coach row on their own session
      data: { get: function (i) { return window.CoachAPI.bookingStory(i).then(function (r) { return r.booking; }); } },
      onNavigate: function (t) { if (t.kind === "person") go("#/client/" + t.id); },
      actions: {
        accept: { done: "Confirmed.", run: function (b) { return window.API.acceptBooking(b.id); } },
        propose: { manual: true, run: function (b) { proposeModal(b.id, function () { renderEvent(id); }); } },
        decline: { tone: "danger", back: true, done: "Declined.", run: function (b) { return window.API.declineBooking(b.id, {}); } },
        mark_completed: { done: "Marked completed.", run: function (b) { return window.API.setBookingStatus(b.id, { status: "completed" }); } },
        mark_no_show: { done: "Marked no-show.", run: function (b) { return window.API.setBookingStatus(b.id, { status: "no_show" }); } },
        reschedule: { manual: true, run: function (b) { rescheduleModal(b, function () { renderEvent(id); }); } },
        add_player: { manual: true, run: function (b) { window.CRMUI.addLessonPlayerModal({ searchFn: function (q) { return window.API.searchBookingMembers(q); }, onSubmit: function (payload) { return window.API.addBookingPlayer(b.id, payload); }, onDone: function () { renderEvent(id); } }); } },
        cancel: { tone: "danger", back: true, confirm: "Cancel this session?", done: "Cancelled.", run: function (b) { return window.API.cancelBooking(b.id, { reason: "coach cancelled" }); } },
        add_to_calendar: { manual: true, run: function (b) { addToCalendar(b.ics_url); } },
        // Coaching money (arrears) — coach's write_off is the COACHING write-off, so it belongs in the
        // Coaching group (override the widget's default "Client charge" placement for this key).
        collect: { group: "Coaching charge", manual: true, run: function (b) { arr(b.arrears.id, "collect", function () { renderEvent(id); }); } },
        discount: { group: "Coaching charge", manual: true, run: function (b) { arr(b.arrears.id, "discount", function () { renderEvent(id); }, b.arrears); } },
        write_off: { group: "Coaching charge", tone: "danger", manual: true, run: function (b) { arr(b.arrears.id, "writeoff", function () { renderEvent(id); }); } },
      },
    });
  }
  // A CLASS enrolment's event story — the SAME shared widget (Widgets.TransactionDetail) over the class
  // sibling reader (CoachAPI.classStory → enrolment_story), so a class drills to the fold + Transactions
  // just like a lesson. Coach-side classes are view-first; the receipt opens for a paid seat.
  function renderClassEvent(id) {
    var host = el("div", {});
    set(host);
    window.Widgets.TransactionDetail.mount(host, {
      role: "coach",
      scope: { id: id },
      fields: { showCoach: false },
      data: { get: function (i) { return window.CoachAPI.classStory(i).then(function (r) { return r.booking; }); } },
      onNavigate: function (t) { if (t.kind === "person") go("#/client/" + t.id); },
      actions: {
        receipt: { manual: true, run: function (b) { var oid = b.charge && b.charge.order_id; if (oid) window.open("/receipt.html?order=" + encodeURIComponent(oid), "_blank"); } },
        add_to_calendar: { manual: true, run: function (b) { addToCalendar(b.ics_url); } },
      },
    });
  }
  // The class ROSTER page — a coach opens their own class from the schedule → the enrolled list, each
  // with Check-in / No-show (reuses CoachAPI.classRoster + classAttendance; the diary route gates it to
  // the class's own coach). Same shape as the admin roster; a player drills to the coach's client record.
  function renderRoster(sessionId) {
    loading();
    function rosterRow(p) {
      var chip = p.status === "attended" ? el("span", { class: "cf-chip confirmed", text: "Checked in" })
               : p.status === "no_show" ? el("span", { class: "cf-chip cf-btn-danger", text: "No-show" })
               : el("span", { class: "cf-chip held", text: "Enrolled" });
      var mark = function (attended) {
        window.CoachAPI.classAttendance(sessionId, { user_id: p.user_id, attended: attended })
          .then(function () { UI.toast(attended ? "Checked in." : "Marked no-show.", "info"); renderRoster(sessionId); },
                function (e) { UI.toast(UI.errMsg(e), "error"); });
      };
      var acts = el("div", { class: "cf-row", style: "gap:6px" });
      if (p.status !== "attended") acts.appendChild(el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Check in", onclick: function () { mark(true); } }));
      if (p.status !== "no_show") acts.appendChild(el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "No-show", onclick: function () { mark(false); } }));
      var main = el("div", { class: "cf-item-main" + (p.user_id ? " cf-item-tap" : "") }, [
        el("div", { class: "cf-item-t", text: p.name || p.email || "Player" }),
        el("div", { class: "cf-item-s", text: [p.email, p.phone].filter(Boolean).join(" · ") || "—" }),
      ]);
      if (p.user_id) main.addEventListener("click", function () { go("#/client/" + p.user_id); });
      return el("div", { class: "cf-item" }, [main, el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [chip, acts])]);
    }
    window.CoachAPI.classRoster(sessionId).then(function (r) {
      var sess = r.session || {}, enrolled = r.enrolled || [], waitlisted = r.waitlisted || [];
      var when = "";
      try { when = UI.fmtDate(sess.starts_at) + " · " + UI.fmtTime(sess.starts_at) + "–" + UI.fmtTime(sess.ends_at); } catch (e) {}
      var present = enrolled.filter(function (p) { return p.status === "attended"; }).length;
      var wrap = el("div", {}, [backBar("Schedule", "#/schedule")]);
      wrap.appendChild(card([
        el("h1", { style: "margin:0 0 2px;font-size:1.25rem", text: sess.class_name || "Class" }),
        el("div", { class: "cf-muted", text: [when, sess.coach_name].filter(Boolean).join(" · ") || "" }),
        el("div", { class: "cf-muted", style: "margin-top:4px;font-size:.85rem", text:
          enrolled.length + " enrolled" + (sess.capacity ? " / " + sess.capacity : "") +
          " · " + present + " checked in" + (waitlisted.length ? " · " + waitlisted.length + " waitlisted" : "") }),
      ]));
      var lc = card([el("h2", { style: "margin:0 0 8px;font-size:1.05rem", text: "Enrolled" })]);
      var list = el("div", { class: "cf-list" });
      if (!enrolled.length) list.appendChild(el("div", { class: "cf-empty", text: "No one enrolled yet." }));
      enrolled.forEach(function (p) { list.appendChild(rosterRow(p)); });
      lc.appendChild(list);
      wrap.appendChild(lc);
      if (waitlisted.length) {
        var wl = card([el("h2", { style: "margin:0 0 8px;font-size:1.05rem", text: "Waitlist" })]);
        var wlist = el("div", { class: "cf-list" });
        waitlisted.forEach(function (p) { wlist.appendChild(el("div", { class: "cf-item" }, [el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: p.name || p.email || "Player" }), el("div", { class: "cf-item-s", text: "Waitlisted" })])])); });
        wl.appendChild(wlist); wrap.appendChild(wl);
      }
      set(wrap);
    }, function (e) { set(el("div", {}, [backBar("Schedule", "#/schedule"), el("div", { class: "cf-empty", text: UI.errMsg(e) })])); });
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

  // ---- MONEY (the OUTCOME of this month's bookings — no transaction ream) --------
  // Money is derived entirely from the bookings: Billed − Discount − Written-off = Invoiced ; Invoiced
  // = Paid + Outstanding (CRMUI.statementFold). Then the coach's own cut on the paid, and the running
  // ledger balance. Per client, tap → the lean client view. The old activity ledger is GONE (the event
  // record is the truth); disputes stay (an action, not a log).
  async function renderMoney() {
    ensureMonth(); loading();
    var m = {}, disputes = [];
    try { m = await window.CoachAPI.money(MONTH); } catch (e) {}
    try { disputes = (await window.CoachAPI.refundRequests("pending")).requests || []; } catch (e) {}
    var t = m.totals || {}, cur = m.currency || "ZAR";
    var wrap = el("div", {});
    wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:8px" }, [el("h1", { style: "margin:0", text: "Money" }), monthNav(renderMoney)]));

    // The folded statement for the month + the coach's cut on what was PAID + net balance with the club.
    var bal = t.balance_minor || 0;
    var extra = [
      { label: "You keep", sub: "on paid", value_minor: t.net_minor, tone: "good" },
      { label: "Club commission", sub: (m.commission_pct ? m.commission_pct + "% on paid" : "on paid"), value_minor: t.commission_minor },
      { label: "Net balance with club", value_minor: bal, tone: bal < 0 ? "bad" : "good",
        sub: bal > 0 ? "club owes you" : (bal < 0 ? "you owe the club" : "settled") },
    ];
    wrap.appendChild(card([window.CRMUI.statementFold({ currency: cur, month: MONTH, totals: t, extra: extra })]));

    // Close out — the clients still owing this month (from the SAME fold, so it always agrees).
    var owing = (m.clients || []).filter(function (r) { return (r.outstanding_minor || 0) > 0; });
    var recCard = card([window.CRMUI.sectionHead("Close out " + monthLabel(MONTH) + (owing.length ? " · " + owing.length + " to settle" : ""))]);
    if (!owing.length) recCard.appendChild(el("div", { class: "cf-empty", text: "All settled for " + monthLabel(MONTH) + " — nothing to finalise. 🎉" }));
    else {
      recCard.appendChild(el("p", { class: "cf-muted", style: "margin:-6px 0 8px;font-size:.85rem", text: "Before month-end, clear these tabs so the books are accurate. Tap a client to review each booking, then collect (mark paid at court / off-platform), discount, or write off." }));
      var ol = el("div", { class: "cf-list" });
      owing.forEach(function (r) { ol.appendChild(clientMoneyRow(r, cur)); });
      recCard.appendChild(ol);
    }
    wrap.appendChild(recCard);

    // Disputes (an action queue, not a log — kept).
    if (disputes.length) {
      var dc = card([el("h2", { style: "margin:0 0 6px", text: "Refund requests" }), el("p", { class: "cf-muted", style: "margin:-2px 0 8px;font-size:.85rem", text: "A client asked for a refund on your lesson — you decide." })]);
      dc.appendChild(window.CRMUI.lineItems(disputes.map(function (r) { return { id: r.id, gross_minor: (r.amount_minor != null ? r.amount_minor : r.order_amount_minor), currency: r.currency_code, _n: r.requester_name || "A client", _s: [r.item_description || "Lesson", r.reason ? "“" + r.reason + "”" : ""].filter(Boolean).join(" · ") }; }), {
        currency: cur, label: function (it) { return it._n; }, sub: function (it) { return it._s; }, empty: "None.",
        actions: [{ label: "Approve", tone: "primary", onClick: function (it) { decideDispute(it.id, "approve"); } }, { label: "Decline", tone: "danger", onClick: function (it) { decideDispute(it.id, "decline"); } }],
      }));
      wrap.appendChild(dc);
    }

    // Every client this month (from the fold) → tap to their lean record.
    var byClient = card([window.CRMUI.sectionHead("By client")]);
    if (!(m.clients || []).length) byClient.appendChild(el("div", { class: "cf-empty", text: "No sessions this month." }));
    else {
      byClient.appendChild(el("p", { class: "cf-muted", style: "margin:-6px 0 8px;font-size:.85rem", text: "Tap a client to review their bookings and settle." }));
      var cl = el("div", { class: "cf-list" });
      m.clients.forEach(function (r) { cl.appendChild(clientMoneyRow(r, cur)); });
      byClient.appendChild(cl);
    }
    wrap.appendChild(byClient);
    set(wrap);
  }
  // ONE per-client money row (Close out + By client) — billed headline + paid/owed sub, tap → detail.
  function clientMoneyRow(r, cur) {
    var sub = money(r.paid_minor, cur) + " paid" + ((r.outstanding_minor || 0) > 0 ? " · " + money(r.outstanding_minor, cur) + " owed" : "");
    return el("div", { class: "cf-item cf-item-tap", onclick: function () { if (r.client_user_id) go("#/client/" + r.client_user_id); } }, [
      el("div", { class: "cf-avatar", style: "width:34px;height:34px;font-size:.8rem", text: (r.client_name || "?").slice(0, 1).toUpperCase() }),
      el("div", { class: "cf-item-main" }, [
        el("div", { class: "cf-item-t", text: r.client_name + "  ·  " + (r.count || 0) }),
        el("div", { class: "cf-item-s", text: sub }),
      ]),
      el("span", { style: "font-weight:700", text: money(r.invoiced_minor, cur) }),
      ((r.outstanding_minor || 0) > 0 ? el("span", { class: "cf-chip held", text: "owed" }) : el("span", { class: "cf-muted", text: "›" })),
    ]);
  }
  async function decideDispute(id, action) {
    var isA = action === "approve";
    var note = window.prompt(isA ? "Approve this refund? The client is refunded and your commission reversed.\n\nNote (optional):" : "Decline this refund?\n\nReason (shown to the client):", "");
    if (note === null) return;
    try { if (isA) await window.CoachAPI.approveRefund(id, { note: note }); else await window.CoachAPI.declineRefund(id, { note: note }); UI.toast(isA ? "Approved." : "Declined.", "info"); renderMoney(); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  // ---- SETUP — the shared Widgets.Setup shell (same gold standard as the owner). The coach sees ONLY
  // their own things; they cannot touch club profile / courts / memberships. FRONTEND-STD Wave 6. ----
  var COACH_SETUP = [
    { key: "myprofile", label: "Your profile", desc: "Photo, bio, languages, visibility", href: "#/profile" },
    { key: "myhours", label: "Weekly hours", desc: "When you're available to coach", href: "#/hours" },
    { key: "services", label: "Services & pricing", desc: "Your lessons & classes — prices, payment, packages",
      mount: function (h) { window.Widgets.ServiceList.mount(h, { role: "coach", userId: principal.user_id, kinds: ["lesson", "class"], allowCreate: true, onCreate: newServiceModal }); } },
    { key: "classes", label: "Classes", desc: "Create, schedule & manage rosters", mount: mountCoachClasses },
    { key: "commission", label: "Club commission", desc: "What the club keeps on your coaching", mount: mountCoachCommission },
  ];
  function renderSetup(section) {
    var host = el("div", {});
    set(host);
    window.Widgets.Setup.mount(host, {
      active: section, sections: COACH_SETUP, backHash: "#/setup",
      onOpen: function (k) { go("#/setup/" + k); },
      title: "Setup", intro: "Your profile, services, classes and commission.",
    });
  }
  function mountCoachClasses(host) {
    UI.clear(host);
    var c = card([el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:6px" }, [
      el("h2", { style: "margin:0", text: "Classes" }),
      el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "+ New class", onclick: openNewClass }),
    ])]);
    c.appendChild(el("p", { class: "cf-muted", style: "margin:-2px 0 8px;font-size:.85rem", text: "Create a class, schedule its sessions, and manage rosters & attendance. Scheduled sessions appear on your calendar and become bookable by clients." }));
    c.appendChild(el("div", { id: "coach-cls-list" }, [el("div", { class: "cf-loading", text: "Loading classes…" })]));
    c.appendChild(el("div", { id: "coach-cls-sessions" }));
    host.appendChild(c);
    loadClasses();
  }
  function mountCoachCommission(host) {
    UI.clear(host);
    host.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
    window.CoachAPI.commission().then(function (cm) {
      UI.clear(host);
      if (cm && cm.effective_pct != null) host.appendChild(card([el("h2", { style: "margin:0 0 6px", text: "Club commission" }), el("p", { class: "cf-muted", style: "margin:0;font-size:.88rem", text: "The club keeps " + cm.effective_pct + "% of your coaching. You keep " + (100 - cm.effective_pct) + "%. (Set by the club.)" })]));
      else host.appendChild(el("div", { class: "cf-empty", text: "No commission set by the club." }));
    }, function (e) { UI.clear(host); host.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
  }
  // ---- Classes (management) — ClassUI + CoachAPI, blueprint ported from the old console ----------
  async function loadClasses() {
    var box = document.getElementById("coach-cls-list"); if (!box) return;
    try { await ensureClassDeps(); } catch (e) { UI.clear(box); box.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); return; }
    window.CoachAPI.classes().then(function (r) {
      UI.clear(box);
      window.ClassUI.renderClassList({ host: box, classes: r.classes || [],
        onEdit: function (c) { window.ClassUI.openClassForm({ api: window.CoachAPI, title: "Edit class", cls: c, onSaved: function () { loadClasses(); } }); },
        onSchedule: function (c) { openSchedule(c); }, onSessions: function (c) { showSessions(c); } });
    }).catch(function (e) { UI.clear(box); box.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
  }
  function openNewClass() {
    ensureClassDeps().then(function () {
      window.ClassUI.openClassForm({ api: window.CoachAPI, title: "New class", onSaved: function () { loadClasses(); } });
    }).catch(function (e) { UI.toast(UI.errMsg(e), "error"); });
  }
  function openSchedule(c) {
    window.ClassUI.openScheduleForm({ api: window.CoachAPI,
      cls: { resource_id: c.resource_id, name: c.name, capacity: c.capacity, duration_minutes: c.duration_minutes, court_resource_ids: c.court_resource_ids },
      onSaved: function () { loadClasses(); showSessions(c); } });
  }
  // Tap a class on the calendar → its roster (enrolled/waitlist + attendance). c is a diary.classes session.
  function openClassRoster(c) {
    ensureClassDeps().then(function () {
      window.ClassUI.openRoster({ api: window.CoachAPI, cls: { name: c.class_name || "Class" },
        session: { session_id: c.id, starts_at: c.starts_at, ends_at: c.ends_at } });
    }).catch(function (e) { UI.toast(UI.errMsg(e), "error"); });
  }
  function showSessions(c) {
    var host = document.getElementById("coach-cls-sessions"); if (!host) return;
    UI.clear(host);
    host.appendChild(el("div", { style: "margin-top:14px" }, [el("h3", { style: "margin:0 0 6px", text: "Sessions · " + (c.name || "Class") }), el("div", { id: "coach-cls-sessions-body" })]));
    window.ClassUI.renderSessions({ api: window.CoachAPI, cls: { resource_id: c.resource_id, name: c.name, capacity: c.capacity }, host: document.getElementById("coach-cls-sessions-body") });
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
    // Sign out lives in the top-right account menu now (not a button at the bottom of the profile).
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
  var modal = function (t) { return UI.modal(t, { lg: true }); };   // shared (Wave 1)
  var toLocal = window.UI.toLocal, addToCalendar = window.UI.addToCalendar;

  window.CoachApp = { start: start };
})();

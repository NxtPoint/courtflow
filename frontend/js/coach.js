// coach.js — coach console (docs/08 §2): my week (lessons + classes I run), class
// rosters + mark attendance, my availability/time-off editor (view + remove), and a
// read-only "My clients" view (gross activity with this coach only).
// Calls GET /api/diary/bookings?as_coach=1, GET /api/diary/classes, GET /api/diary/resources,
// POST /api/diary/bookings/:id/status, POST /api/diary/time-off, GET/DELETE /api/coach/time-off,
// GET /api/coach/clients[/:id].
(function () {
  var UI, el, principal;
  var TAB = "dashboard";  // active console tab: dashboard · schedule · clients · money · setup
  var setupTab = "services";  // sub-tab within Setup: services · profile
  var wkStart = null;    // Monday of the visible week (Schedule timeline)
  var resCache = null;   // {coachRes:[...], courts:[...]} — cached resources for the booking modal
  var classState = { list: [] };

  // coach.html loads coach_api.js but not class_ui.js (the shared class components).
  // Lazy-load it once so the "My classes" area works without touching the HTML shell.
  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      if (document.querySelector('script[src="' + src + '"]')) return resolve();
      var s = document.createElement("script");
      s.src = src; s.onload = resolve; s.onerror = function () { reject(new Error("Failed to load " + src)); };
      document.head.appendChild(s);
    });
  }
  async function ensureClassDeps() {
    if (!window.ClassUI) await loadScript("/js/class_ui.js");
  }

  // ---- my schedule (week TIMELINE — master-diary style) ----------------------
  var WK_H0 = 7, WK_H1 = 21, WK_ROW = 46;   // 07:00–21:00 window; px per hour row (matches cf-cal-cell)
  function mondayOf(d) {
    var x = new Date(d); x.setHours(0, 0, 0, 0);
    x.setDate(x.getDate() - ((x.getDay() + 6) % 7));   // step back to Monday
    return x;
  }
  function coachWeek(host) {
    if (!wkStart) wkStart = mondayOf(new Date());
    var range = UI.fmtDate(wkStart.toISOString()) + " – " + UI.fmtDate(UI.addDays(wkStart, 6).toISOString());
    var nav = el("div", { class: "cf-row", style: "align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap" }, [
      el("h2", { text: "My schedule", style: "margin:0" }),
      el("span", { class: "cf-chip", text: range }),
      el("span", { class: "cf-spacer" }),
      el("button", { class: "cf-btn cf-btn-sm", text: "‹", title: "Previous week", onclick: function () { wkStart = UI.addDays(wkStart, -7); renderTab(); } }),
      el("button", { class: "cf-btn cf-btn-sm", text: "This week", onclick: function () { wkStart = mondayOf(new Date()); renderTab(); } }),
      el("button", { class: "cf-btn cf-btn-sm", text: "›", title: "Next week", onclick: function () { wkStart = UI.addDays(wkStart, 7); renderTab(); } }),
      el("a", { class: "cf-btn cf-btn-sm", href: "/book/court", text: "🎾 Book for myself" }),
      el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "+ Book for a client", onclick: function () { openBookForClient(); } }),
    ]);
    var gridHost = el("div", { class: "cf-loading", text: "Loading your week…" });
    host.appendChild(el("div", { class: "cf-card" }, [nav, gridHost]));
    var from = UI.dateKey(wkStart), to = UI.dateKey(UI.addDays(wkStart, 7));
    Promise.all([
      window.API.bookings({ date_from: from, date_to: to, as_coach: "1" }),
      window.API.classes({ date_from: from, date_to: to }),
    ]).then(function (res) {
      var lessons = res[0].bookings || [];
      var classes = (res[1].classes || []).filter(function (c) { return String(c.coach_user_id) === String(principal.user_id); });
      drawWeek(gridHost, lessons, classes);
    }, function (e) { UI.clear(gridHost); gridHost.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
  }

  function drawWeek(box, lessons, classes) {
    UI.clear(box);
    var days = []; for (var i = 0; i < 7; i++) days.push(UI.addDays(wkStart, i));
    var dayKey = {}; days.forEach(function (d, i) { dayKey[UI.dateKey(d)] = i; });
    var slots = WK_H1 - WK_H0;
    var todayKey = UI.dateKey(new Date());
    var wrap = el("div", { class: "cf-cal-wrap" });
    var grid = el("div", { class: "cf-cal" });
    grid.style.gridTemplateColumns = "52px repeat(7, minmax(84px,1fr))";
    grid.style.gridTemplateRows = "auto repeat(" + slots + ", " + WK_ROW + "px)";
    grid.appendChild(el("div", { class: "cf-cal-head" }));
    days.forEach(function (d) {
      grid.appendChild(el("div", { class: "cf-cal-head",
        text: d.toLocaleDateString("en-ZA", { weekday: "short", day: "numeric", timeZone: UI.CLUB_TZ }),
        style: UI.dateKey(d) === todayKey ? "color:var(--green-600);font-weight:800" : "" }));
    });
    var cellByDay = {};
    for (var s = 0; s < slots; s++) {
      grid.appendChild(el("div", { class: "cf-cal-time", text: ("0" + (WK_H0 + s)).slice(-2) + ":00" }));
      days.forEach(function (d, di) {
        var cell = el("div", { class: "cf-cal-cell", style: "cursor:default" });
        if (s === 0) cellByDay[di] = cell;
        grid.appendChild(cell);
      });
    }
    function place(startIso, endIso, kind, text, title, onclick, cancelled) {
      var start = new Date(startIso), end = new Date(endIso);
      var di = dayKey[UI.dateKey(start)];
      if (di === undefined || !cellByDay[di]) return;
      var mins = (start.getHours() - WK_H0) * 60 + start.getMinutes();
      var dur = Math.max(30, (end - start) / 60000);
      var winMins = (WK_H1 - WK_H0) * 60;
      if (mins >= winMins || mins + dur <= 0) return;               // outside the visible window
      var top = Math.max(0, mins) / 60 * WK_ROW;
      var height = Math.max(16, (Math.min(mins + dur, winMins) - Math.max(0, mins)) / 60 * WK_ROW - 2);
      cellByDay[di].appendChild(el("div", {
        class: "cf-ev " + kind + (cancelled ? " cancelled" : ""),
        style: "top:" + top + "px;height:" + height + "px", title: title,
        onclick: function (e) { e.stopPropagation(); if (onclick) onclick(); },
      }, [el("div", { text: text })]));
    }
    lessons.forEach(function (b) {
      var who = b.booked_by_name || "Lesson";
      place(b.starts_at, b.ends_at, "lesson", UI.fmtTime(b.starts_at) + " " + who,
        who + " · " + UI.fmtRange(b.starts_at, b.ends_at) + (b.court_name ? " · " + b.court_name : "") + " · " + (b.status || ""),
        function () { openLessonSheet(b); }, b.status === "cancelled" || b.status === "no_show");
    });
    classes.forEach(function (c) {
      place(c.starts_at, c.ends_at, "class", UI.fmtTime(c.starts_at) + " " + (c.class_name || "Class"),
        (c.class_name || "Class") + " · " + (c.enrolled || 0) + "/" + (c.capacity || 0) + " enrolled",
        function () { openWeekRoster(c); }, c.status === "cancelled");
    });
    if (!lessons.length && !classes.length) {
      box.appendChild(el("div", { class: "cf-empty", style: "margin-bottom:8px", text: "Nothing scheduled this week." }));
    }
    wrap.appendChild(grid); box.appendChild(wrap);
    box.appendChild(el("p", { class: "cf-muted cf-tiny", style: "margin-top:8px", text: "Tap a lesson to mark it done / no-show; tap a class for its roster." }));
  }

  // Tap a lesson block → mark completed / no-show (or just view).
  function openLessonSheet(b) {
    var bg = el("div", { class: "cf-modal-bg" });
    function close() { if (bg.parentNode) document.body.removeChild(bg); }
    var actions = [];
    if (["held", "confirmed"].indexOf(b.status) >= 0) {
      actions.push(el("button", { class: "cf-btn cf-btn-primary", text: "Mark completed", onclick: function () { close(); setStatus(b.id, "completed"); } }));
      actions.push(el("button", { class: "cf-btn cf-btn-danger", text: "No-show", onclick: function () { close(); setStatus(b.id, "no_show"); } }));
    }
    bg.appendChild(el("div", { class: "cf-modal" }, [
      el("h2", { text: b.booked_by_name || "Lesson" }),
      el("p", { class: "cf-muted", text: UI.fmtRange(b.starts_at, b.ends_at) + (b.court_name ? " · " + b.court_name : "") }),
      el("p", {}, [el("span", { class: "cf-chip " + b.status, text: b.status })]),
      el("div", { class: "cf-row", style: "gap:8px;justify-content:flex-end;margin-top:12px;flex-wrap:wrap" },
        actions.concat([el("button", { class: "cf-btn", text: "Close", onclick: close })])),
    ]));
    document.body.appendChild(bg);
  }

  // A class session from this week's /api/diary/classes glance -> open its roster via
  // the shared ClassUI (the same roster used in the "My classes" management area).
  async function openWeekRoster(c) {
    try { await ensureClassDeps(); } catch (e) { UI.toast(UI.errMsg(e), "error"); return; }
    window.ClassUI.openRoster({
      api: window.CoachAPI,
      cls: { name: c.class_name || "Class", resource_id: c.resource_id, capacity: c.capacity },
      session: { session_id: c.id, starts_at: c.starts_at, ends_at: c.ends_at,
        status: c.status, enrolled: c.enrolled, capacity: c.capacity },
    });
  }

  async function setStatus(id, status) {
    try { await window.API.setBookingStatus(id, { status: status }); UI.toast("Updated.", "info"); if (TAB === "schedule") renderTab(); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  // ---- book a session for a client ------------------------------------------
  // A coach books FOR a client: either an existing member (by email → shows in their
  // bookings via booked_for_user_id) or a walk-in (name/email → guest player party).
  // Type: "Lesson with me" (this coach's resource) or "Court". A court may also be
  // attached to a lesson. Time is a simple datetime-local + duration picker (v1).
  async function ensureResources() {
    if (resCache) return resCache;
    var r = await window.API.resources();
    var all = r.resources || [];
    resCache = {
      coachRes: all.filter(function (x) {
        return x.kind === "coach" && String(x.coach_user_id) === String(principal.user_id);
      }),
      courts: all.filter(function (x) { return x.kind === "court" && x.is_active; }),
    };
    return resCache;
  }

  function fieldEl(label, control) {
    return el("div", { class: "cf-field" }, [ el("label", { text: label }), control ]);
  }

  async function openBookForClient() {
    var rc;
    try { rc = await ensureResources(); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); return; }
    var myCoachRes = rc.coachRes[0] || null;

    var bg = el("div", { class: "cf-modal-bg" });
    function close() { if (bg.parentNode) document.body.removeChild(bg); }

    var email = el("input", { class: "cf-input", type: "email", placeholder: "Existing member email (optional)" });
    var guestName = el("input", { class: "cf-input", placeholder: "…or walk-in name" });
    var guestEmail = el("input", { class: "cf-input", type: "email", placeholder: "Walk-in email (optional)" });

    var typeSel = el("select", { class: "cf-select" }, [
      el("option", { value: "lesson", text: "Lesson with me" }),
      el("option", { value: "court", text: "Court" }),
    ]);
    if (!myCoachRes) { typeSel.value = "court"; }

    function courtOptions(includeNone) {
      var opts = [];
      if (includeNone) opts.push(el("option", { value: "", text: "No court" }));
      rc.courts.forEach(function (c) { opts.push(el("option", { value: c.id, text: c.name })); });
      return opts;
    }
    var courtSel = el("select", { class: "cf-select" }, courtOptions(true));

    var when = el("input", { class: "cf-input", type: "datetime-local" });
    var dur = el("select", { class: "cf-select" }, [
      el("option", { value: "60", text: "60 min" }), el("option", { value: "30", text: "30 min" }),
      el("option", { value: "90", text: "90 min" }),
    ]);
    var settle = el("select", { class: "cf-select" }, [
      el("option", { value: "at_court", text: "Pay at court" }),
      el("option", { value: "monthly_account", text: "Monthly account" }),
      el("option", { value: "free", text: "Complimentary" }),
    ]);
    var courtField = fieldEl("Court (optional, held alongside the lesson)", courtSel);
    function syncType() {
      var isLesson = typeSel.value === "lesson";
      // Lesson: optional court (with "No court"). Court booking: a court is required.
      UI.clear(courtSel);
      courtOptions(isLesson).forEach(function (o) { courtSel.appendChild(o); });
      courtField.querySelector("label").textContent = isLesson
        ? "Court (optional, held alongside the lesson)" : "Court";
    }
    typeSel.addEventListener("change", syncType);
    syncType();

    var modal = el("div", { class: "cf-modal" }, [
      el("h2", { text: "Book a session for a client" }),
      el("p", { class: "cf-muted", text: "Enter a member's email to book for them, or a walk-in name for a guest." }),
      fieldEl("Member email", email),
      fieldEl("Walk-in name", guestName),
      fieldEl("Walk-in email", guestEmail),
      fieldEl("Type", typeSel),
      courtField,
      fieldEl("When", when),
      fieldEl("Duration", dur),
      fieldEl("Settlement", settle),
      el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px" }, [
        el("button", { class: "cf-btn", text: "Cancel", onclick: close }),
        el("button", { class: "cf-btn cf-btn-primary", text: "Book", onclick: function () { submitBookForClient(); } }),
      ]),
    ]);
    bg.appendChild(modal); document.body.appendChild(bg);
    bg.addEventListener("click", function (e) { if (e.target === bg) close(); });

    async function submitBookForClient() {
      var em = email.value.trim(), gn = guestName.value.trim(), ge = guestEmail.value.trim();
      if (!em && !gn) { UI.toast("Enter a member email or a walk-in name.", "warn"); return; }
      if (!when.value) { UI.toast("Pick a date and time.", "warn"); return; }
      var isLesson = typeSel.value === "lesson";
      if (isLesson && !myCoachRes) { UI.toast("You have no coach resource — pick Court instead.", "warn"); return; }
      if (!isLesson && !courtSel.value) { UI.toast("Pick a court.", "warn"); return; }
      var s = new Date(when.value), e2 = new Date(s.getTime() + parseInt(dur.value, 10) * 60000);
      var body = {
        booking_type: isLesson ? "lesson" : "court",
        starts_at: s.toISOString(), ends_at: e2.toISOString(),
        settlement_mode: settle.value, audience: "member",
      };
      if (isLesson) {
        body.resource_id = myCoachRes.id;
        body.coach_user_id = myCoachRes.coach_user_id || principal.user_id;
        if (courtSel.value) body.court_resource_id = courtSel.value;
      } else {
        body.resource_id = courtSel.value;
      }
      // On-behalf fields — the server only honours these for coach/admin roles, and resolves
      // for_email to a club member (else treats it as a walk-in guest party).
      if (em) body.for_email = em;
      if (gn) { body.for_guest_name = gn; if (ge) body.for_guest_email = ge; }
      try {
        await window.API.createBooking(body);
        close();
        UI.toast("Booked for client.", "info");
        if (TAB === "schedule") renderTab();
      } catch (e3) { UI.toast(UI.errMsg(e3), "error"); }
    }
  }

  // ---- "My classes" management area -----------------------------------------
  // A coach creates/manages only their OWN classes: create a class type, schedule
  // recurring/one-off sessions, view/cancel sessions, open rosters + mark attendance.
  // Reuses the shared ClassUI components (same ones the admin console uses); the coach
  // form has no coach selector (the server attributes the class to the caller).
  // Injects its own card into #cf-main after the "My week" card (HTML shell is fixed).
  async function initMyClasses(host) {
    if (!host) return;
    var card = el("div", { class: "cf-card", id: "coach-classes-card" }, [
      el("div", { class: "cf-row", style: "margin-bottom:6px" }, [
        el("h2", { text: "My classes", style: "margin:0" }),
        el("span", { class: "cf-spacer" }),
        el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "New class",
          onclick: function () { openNewClass(); } }),
      ]),
      el("p", { class: "cf-muted", style: "margin:-2px 0 12px",
        text: "Create your classes, schedule sessions, and manage rosters & attendance." }),
      el("div", { id: "coach-cls-list", class: "cf-loading", text: "Loading classes…" }),
      el("div", { id: "coach-cls-sessions" }),
    ]);
    host.appendChild(card);

    try { await ensureClassDeps(); } catch (e) {
      var b = document.getElementById("coach-cls-list"); if (b) b.textContent = UI.errMsg(e); return;
    }
    loadClasses();
  }

  function loadClasses() {
    var box = document.getElementById("coach-cls-list"); if (!box) return;
    UI.clear(box); box.appendChild(el("div", { class: "cf-loading", text: "Loading classes…" }));
    window.CoachAPI.classes().then(function (r) {
      classState.list = r.classes || [];
      window.ClassUI.renderClassList({
        host: box, classes: classState.list,
        onSchedule: function (c) { openSchedule(c); },
        onSessions: function (c) { showSessions(c); },
      });
    }).catch(function (e) {
      UI.clear(box); box.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) }));
    });
  }

  function openNewClass() {
    window.ClassUI.openClassForm({
      api: window.CoachAPI, title: "New class",   // no coach selector — it's the caller's own
      onSaved: function () { loadClasses(); },
    });
  }
  function openSchedule(c) {
    window.ClassUI.openScheduleForm({
      api: window.CoachAPI,
      cls: { resource_id: c.resource_id, name: c.name, capacity: c.capacity, duration_minutes: c.duration_minutes },
      onSaved: function () { loadClasses(); showSessions(c); },
    });
  }
  function showSessions(c) {
    var host = document.getElementById("coach-cls-sessions"); if (!host) return;
    UI.clear(host);
    host.appendChild(el("div", { style: "margin-top:14px" }, [
      el("h3", { text: "Sessions · " + (c.name || "Class") }),
      el("div", { id: "coach-cls-sessions-body" }),
    ]));
    window.ClassUI.renderSessions({
      api: window.CoachAPI,
      cls: { resource_id: c.resource_id, name: c.name, capacity: c.capacity },
      host: document.getElementById("coach-cls-sessions-body"),
    });
  }

  // ---- availability / time-off ---------------------------------------------
  async function loadResources() {
    try {
      var r = await window.API.resources();
      var mine = (r.resources || []).filter(function (x) {
        return x.kind === "coach" && String(x.coach_user_id) === String(principal.user_id);
      });
      var sel = document.getElementById("to-resource");
      UI.clear(sel);
      if (!mine.length) { sel.appendChild(el("option", { value: "", text: "No coach resource for you" })); return; }
      mine.forEach(function (res) { sel.appendChild(el("option", { value: res.id, text: res.name })); });
    } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  async function submitTimeOff() {
    var resId = document.getElementById("to-resource").value;
    var start = document.getElementById("to-start").value;
    var end = document.getElementById("to-end").value;
    var reason = document.getElementById("to-reason").value;
    if (!resId || !start || !end) { UI.toast("Pick a resource and a time range.", "warn"); return; }
    try {
      await window.API.timeOff({
        resource_id: resId,
        starts_at: new Date(start).toISOString(),
        ends_at: new Date(end).toISOString(),
        reason: reason || "time off",
      });
      UI.toast("Time off blocked.", "info");
      document.getElementById("to-start").value = "";
      document.getElementById("to-end").value = "";
      loadTimeOff();
    } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  // List the coach's upcoming time-off blocks (GET /api/coach/time-off) with a Remove
  // action (DELETE /api/coach/time-off/:id). Injected under the "Block time off" card.
  function timeOffListHost() {
    var existing = document.getElementById("coach-timeoff-list");
    if (existing) return existing;
    var submit = document.getElementById("to-submit");
    var card = submit ? submit.closest(".cf-card") : null;
    if (!card) return null;
    var host = el("div", { id: "coach-timeoff-list", style: "margin-top:16px" });
    card.appendChild(host);
    return host;
  }

  async function loadTimeOff() {
    var host = timeOffListHost(); if (!host) return;
    UI.clear(host); host.appendChild(el("div", { class: "cf-loading", text: "Loading time off…" }));
    try {
      var r = await window.CoachAPI.timeOff();
      var rows = r.time_off || [];
      UI.clear(host);
      host.appendChild(el("h3", { text: "Upcoming time off", style: "margin:4px 0 8px" }));
      if (!rows.length) { host.appendChild(el("div", { class: "cf-empty", text: "No upcoming time off." })); return; }
      var list = el("div", { class: "cf-list" });
      rows.forEach(function (t) {
        var rm = el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Remove",
          onclick: function () { removeTimeOff(t.id, t.reason); } });
        list.appendChild(el("div", { class: "cf-item" }, [
          el("span", { class: "cf-chip", text: "blocked" }),
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: t.reason || "Time off" }),
            el("div", { class: "cf-item-s", text: UI.fmtRange(t.starts_at, t.ends_at) }),
          ]),
          el("span", { class: "cf-spacer" }), rm,
        ]));
      });
      host.appendChild(list);
    } catch (e) { UI.clear(host); host.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
  }

  async function removeTimeOff(id, reason) {
    if (!window.confirm("Remove the time-off block" + (reason ? " (" + reason + ")" : "") + "?")) return;
    try { await window.CoachAPI.deleteTimeOff(id); UI.toast("Time off removed.", "info"); loadTimeOff(); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  // ---- my clients -----------------------------------------------------------
  // A read-only table of the coach's own clients (GET /api/coach/clients), derived from
  // their lessons + class enrolments. Row -> a 360 drawer (GET /api/coach/clients/:id)
  // showing the client's full history WITH THIS COACH. Gross activity only (no commission).
  function fmtMoney(m) { return (m == null) ? "—" : "R" + (m / 100).toFixed(2); }
  function fmtDate(iso) {
    if (!iso) return "—";
    try { return new Date(iso).toLocaleDateString(); } catch (e) { return iso; }
  }
  function clientName(c) {
    var n = ((c.first_name || "") + " " + (c.surname || "")).trim();
    return n || c.email || "Client";
  }

  // ---- business cockpit (Dashboard) -----------------------------------------
  // The coach's read-only "how is my business doing?" landing surface: a KPI tile
  // strip, a no-library CSS-bar trend (last 6 months net + lessons), top clients,
  // upcoming sessions, a month selector, and a link to the month-end Statement.
  // GET /api/coach/cockpit[?month=YYYY-MM]; earnings are NET of commission. Injected
  // as the FIRST card in #cf-main so it's the coach's default view.
  var dashState = { month: null };

  function thisMonthKey() {
    var d = new Date();
    return d.getFullYear() + "-" + (d.getMonth() < 9 ? "0" : "") + (d.getMonth() + 1);
  }
  function shiftMonthKey(ym, delta) {
    var parts = (ym || thisMonthKey()).split("-");
    var y = parseInt(parts[0], 10), m = parseInt(parts[1], 10) - 1 + delta;
    while (m < 0) { m += 12; y -= 1; } while (m > 11) { m -= 12; y += 1; }
    return y + "-" + (m < 9 ? "0" : "") + (m + 1);
  }
  function monthLabel(ym) {
    try {
      var p = ym.split("-");
      return new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1, 1)
        .toLocaleDateString([], { month: "long", year: "numeric" });
    } catch (e) { return ym; }
  }
  function shortMonth(ym) {
    try {
      var p = ym.split("-");
      return new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1, 1)
        .toLocaleDateString([], { month: "short" });
    } catch (e) { return ym; }
  }
  function pct(v) { return (v == null) ? "—" : (Math.round(v * 10) / 10) + "%"; }
  function tile(t, s, hint) {
    var kids = [el("div", { class: "cf-tile-t", text: t }),
      el("div", { class: "cf-tile-s", text: s })];
    if (hint) kids.push(el("div", { class: "cf-tile-s cf-tiny", style: "margin-top:2px", text: hint }));
    return el("div", { class: "cf-tile", style: "cursor:default" }, kids);
  }

  function initDashboard(host) {
    if (!host) return;
    var card = el("div", { class: "cf-card", id: "coach-dash-card" }, [
      el("div", { class: "cf-row", style: "margin-bottom:6px;align-items:center;gap:8px" }, [
        el("h2", { text: "Dashboard", style: "margin:0" }),
        el("span", { class: "cf-spacer" }),
        el("button", { class: "cf-btn cf-btn-sm", id: "coach-dash-prev", text: "‹ Prev" }),
        el("span", { class: "cf-chip", id: "coach-dash-month", text: "…" }),
        el("button", { class: "cf-btn cf-btn-sm", id: "coach-dash-next", text: "Next ›" }),
        el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Money & statement →",
          onclick: function () { TAB = "money"; render(); } }),
      ]),
      el("p", { class: "cf-muted", style: "margin:-2px 0 12px",
        text: "Your coaching business at a glance. Earnings are net of commission." }),
      el("div", { id: "coach-dash-body", class: "cf-loading", text: "Loading your cockpit…" }),
    ]);
    host.appendChild(card);

    dashState.month = thisMonthKey();
    document.getElementById("coach-dash-prev").addEventListener("click", function () {
      dashState.month = shiftMonthKey(dashState.month, -1); loadDashboard();
    });
    document.getElementById("coach-dash-next").addEventListener("click", function () {
      dashState.month = shiftMonthKey(dashState.month, 1); loadDashboard();
    });
    loadDashboard();
  }

  async function loadDashboard() {
    var body = document.getElementById("coach-dash-body"); if (!body) return;
    var ml = document.getElementById("coach-dash-month");
    if (ml) ml.textContent = monthLabel(dashState.month);
    UI.clear(body); body.appendChild(el("div", { class: "cf-loading", text: "Loading your cockpit…" }));
    try {
      var d = await window.CoachAPI.cockpit(dashState.month);
      renderDashboard(d || {});
    } catch (e) { UI.clear(body); body.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
  }

  function renderDashboard(d) {
    var body = document.getElementById("coach-dash-body"); if (!body) return;
    UI.clear(body);
    var k = d.kpis || {};

    // KPI strip (shared CRMUI.stats — identical to the owner cockpit primitives).
    body.appendChild(window.CRMUI.stats([
      { value: fmtMoney(k.net_minor), label: "Net earnings (this month)" },
      { value: k.lessons_count || 0, label: "Sessions delivered" },
      { value: k.clients_active || 0, label: "Clients" },
      { value: k.no_shows || 0, label: "No-shows" },
      { value: pct(k.fill_rate_pct), label: "Fill rate" },
      { value: k.upcoming_7d || (d.upcoming || []).length || 0, label: "Upcoming (7d)" },
    ]));
    body.appendChild(el("div", { style: "margin-top:10px" }));
    body.appendChild(window.CRMUI.stats([
      { value: fmtMoney(k.gross_minor), label: "Gross collected" },
      { value: fmtMoney(k.commission_minor), label: "Commission to club" },
      { value: (Math.round((k.hours || 0) * 10) / 10) + "h", label: "Coaching hours" },
      { value: k.classes_count || 0, label: "Class sessions" },
      { value: fmtMoney(k.arrears_owed_minor), label: "Arrears owed" },
      { value: (d.plan_balances || {}).sessions_left || 0, label: "Lessons left on plans" },
    ]));

    // Monthly net-earnings trend via the shared CSS bars.
    body.appendChild(el("div", { style: "margin-top:18px" }));
    body.appendChild(window.CRMUI.sectionHead("Net earnings — recent months"));
    body.appendChild(window.CRMUI.bars((d.trend || []).map(function (t) {
      return { label: shortMonth(t.month), value: (t.net_minor || 0) / 100,
        title: monthLabel(t.month) + " · " + fmtMoney(t.net_minor) + " · " + (t.lessons || 0) + " lessons" };
    }), { fmt: function (v) { return fmtMoney(Math.round(v * 100)); }, empty: "No history yet." }));

    // Month-end position after commission — pulled from the statement totals (the SoR for
    // settlement). Loaded lazily so a cockpit error never blocks the statement card.
    var posBox = el("div", { id: "coach-dash-position", style: "margin-top:18px" });
    body.appendChild(posBox);
    renderMonthEndPosition(posBox);

    // Two-column: top clients + upcoming.
    var cols = el("div", { class: "cf-grid cf-grid-2", style: "margin-top:18px" });
    cols.appendChild(renderTopClients(d.top_clients || []));
    cols.appendChild(renderUpcoming(d.upcoming || []));
    body.appendChild(cols);
  }

  // The clear "what do I owe / am I owed at month end after commission" line — reuses the
  // same statement totals the Statement card shows (one source of truth).
  async function renderMonthEndPosition(box) {
    if (!box) return;
    UI.clear(box); box.appendChild(el("div", { class: "cf-loading", text: "Loading position…" }));
    try {
      var d = await window.CoachAPI.statement(dashState.month);
      var t = d.totals || {}; var cur = d.currency || "ZAR";
      UI.clear(box);
      box.appendChild(window.CRMUI.sectionHead("Month-end position (after commission)"));
      box.appendChild(window.CRMUI.stats([
        { value: window.CRMUI.money(t.paid_minor, cur), label: "Collected (net)" },
        { value: window.CRMUI.money(t.owed_minor, cur), label: "Outstanding" },
        { value: window.CRMUI.money(t.rent_minor, cur), label: "Rent this month" },
        { value: window.CRMUI.money(t.balance_minor, cur), label: "Account balance" },
      ]));
    } catch (e) {
      UI.clear(box);
      box.appendChild(el("div", { class: "cf-muted", style: "font-size:.85rem",
        text: "Settlement position unavailable: " + UI.errMsg(e) }));
    }
  }


  function renderTopClients(list) {
    var card = el("div", {}, [el("h3", { text: "Top clients this month", style: "margin:0 0 8px" })]);
    if (!list.length) {
      card.appendChild(el("div", { class: "cf-empty", text: "No client activity this month." }));
      return card;
    }
    var table = el("table", { class: "cf-table" });
    table.appendChild(el("thead", {}, [el("tr", {}, [
      el("th", { text: "Client" }), el("th", { text: "Sessions" }), el("th", { text: "Spend" }),
    ])]));
    var tb = el("tbody");
    list.forEach(function (c) {
      var tr = el("tr", { style: "cursor:pointer" });
      tr.addEventListener("click", function () { openClient(c.user_id); });
      tr.appendChild(el("td", {}, [el("strong", { text: c.name || "Client" })]));
      tr.appendChild(el("td", { text: String(c.sessions || 0) }));
      tr.appendChild(el("td", { text: fmtMoney(c.spend_minor) }));
      tb.appendChild(tr);
    });
    table.appendChild(tb);
    card.appendChild(table);
    return card;
  }

  function renderUpcoming(list) {
    var card = el("div", {}, [el("h3", { text: "Upcoming", style: "margin:0 0 8px" })]);
    if (!list.length) {
      card.appendChild(el("div", { class: "cf-empty", text: "Nothing booked yet." }));
      return card;
    }
    var ul = el("div", { class: "cf-list" });
    list.forEach(function (u) {
      var when = "—";
      try {
        when = new Date(u.when).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }) +
          " · " + new Date(u.when).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      } catch (e) {}
      ul.appendChild(el("div", { class: "cf-item" }, [
        el("span", { class: "cf-chip", text: u.type || "session" }),
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: u.client || "Client" }),
          el("div", { class: "cf-item-s", text: when }),
        ]),
      ]));
    });
    card.appendChild(ul);
    return card;
  }

  // The Clients tab is CLIENT-CENTRIC: a month-scoped list of clients (lessons / paid / owed with
  // you) that drills into ONE full-screen client view — everything about that client in one place
  // (bookings + money + manage actions + month-end "issue invoice"). clientView holds the state.
  var clientView = { selected: null, month: null };

  function initMyClients(host) {
    if (!host) return;
    if (!clientView.month) clientView.month = thisMonthKey();
    if (clientView.selected) return renderClientPage(host, clientView.selected);
    renderClientList(host);
  }

  function monthNav(idPrefix, onChange) {
    var wrap = el("div", { class: "cf-row", style: "gap:6px;align-items:center" }, [
      el("button", { class: "cf-btn cf-btn-sm", text: "‹ Prev",
        onclick: function () { clientView.month = shiftMonthKey(clientView.month, -1); onChange(); } }),
      el("span", { class: "cf-chip", text: monthLabel(clientView.month) }),
      el("button", { class: "cf-btn cf-btn-sm", text: "Next ›",
        onclick: function () { clientView.month = shiftMonthKey(clientView.month, 1); onChange(); } }),
    ]);
    return wrap;
  }

  // ---- the client LIST (month overview) --------------------------------------
  function renderClientList(host) {
    var card = el("div", { class: "cf-card", id: "coach-clients-card" }, [
      el("div", { class: "cf-row", style: "margin-bottom:6px;align-items:center;gap:8px;flex-wrap:wrap" }, [
        el("h2", { text: "My clients", style: "margin:0" }),
        el("span", { class: "cf-spacer" }),
        monthNav("clients", function () { renderClientList(host2()); }),
        el("input", { class: "cf-input", id: "coach-clients-search",
          placeholder: "Search name or email…", style: "max-width:220px" }),
      ]),
      el("p", { class: "cf-muted", style: "margin:-2px 0 12px",
        text: "Your clients and what they've paid / still owe you this month. Tap a client to see everything and run month-end." }),
      el("div", { id: "coach-clients-body", class: "cf-loading", text: "Loading clients…" }),
    ]);
    // renderClientList may be called to refresh; clear the host first when re-rendering.
    UI.clear(host); host.appendChild(card);
    var search = document.getElementById("coach-clients-search");
    var t = null;
    search.addEventListener("input", function () {
      clearTimeout(t); t = setTimeout(function () { loadClients(search.value.trim()); }, 250);
    });
    loadClients("");
  }
  function host2() { return document.getElementById("coach-tab"); }

  async function loadClients(q) {
    var box = document.getElementById("coach-clients-body"); if (!box) return;
    UI.clear(box); box.appendChild(el("div", { class: "cf-loading", text: "Loading clients…" }));
    try {
      var pair = await Promise.all([
        window.CoachAPI.clients(q ? { search: q } : {}),
        window.CoachAPI.statement(clientView.month).catch(function () { return { clients: [] }; }),
      ]);
      var clients = (pair[0].clients) || [];
      var money = {};
      ((pair[1] && pair[1].clients) || []).forEach(function (m) { money[String(m.client_user_id)] = m; });
      renderClients(clients.map(function (c) {
        var m = money[String(c.user_id)] || {};
        return Object.assign({}, c, { paid_minor: m.paid_minor || 0, owed_minor: m.owed_minor || 0 });
      }));
    } catch (e) { UI.clear(box); box.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
  }

  function renderClients(list) {
    var box = document.getElementById("coach-clients-body"); if (!box) return;
    UI.clear(box);
    if (!list.length) {
      box.appendChild(el("div", { class: "cf-empty",
        text: "No clients yet — once members book you, they'll appear here." }));
      return;
    }
    var table = el("table", { class: "cf-table" });
    table.appendChild(el("thead", {}, [el("tr", {}, [
      el("th", { text: "Client" }), el("th", { text: "Contact" }),
      el("th", { text: "Lessons" }),
      el("th", { class: "num", text: "Paid (mo)" }), el("th", { class: "num", text: "Owed" }),
      el("th", { text: "" }),
    ])]));
    var tbody = el("tbody");
    list.forEach(function (c) {
      var tr = el("tr", { style: "cursor:pointer" });
      tr.addEventListener("click", function () { openClient(c.user_id); });
      tr.appendChild(el("td", {}, [el("strong", { text: clientName(c) })]));
      tr.appendChild(el("td", { text: c.email || c.phone || "—" }));
      tr.appendChild(el("td", { text: String(c.lessons_count || 0) }));
      tr.appendChild(el("td", { class: "num", text: fmtMoney(c.paid_minor) }));
      tr.appendChild(el("td", { class: "num" }, [c.owed_minor
        ? el("span", { class: "cf-chip held", text: fmtMoney(c.owed_minor) })
        : el("span", { class: "cf-muted", text: "—" })]));
      tr.appendChild(el("td", { text: "›" }));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    box.appendChild(table);
  }

  function openClient(userId) { clientView.selected = userId; renderTab(); }
  function backToClients() { clientView.selected = null; renderTab(); }

  // ---- the full-screen SINGLE CLIENT view ------------------------------------
  async function renderClientPage(host, userId) {
    UI.clear(host);
    host.appendChild(el("div", { class: "cf-row", style: "margin-bottom:10px;gap:8px;align-items:center;flex-wrap:wrap" }, [
      el("button", { class: "cf-btn cf-btn-sm", text: "‹ Back to clients", onclick: backToClients }),
      el("span", { class: "cf-spacer" }),
      monthNav("client", function () { renderClientPage(host2(), userId); }),
    ]));
    var body = el("div", { id: "coach-client-body", class: "cf-loading", text: "Loading client…" });
    host.appendChild(body);
    var c;
    try { c = (await window.CoachAPI.client(userId, clientView.month)).client || {}; }
    catch (e) { UI.clear(body); body.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); return; }
    UI.clear(body);
    var money = c.money || {}, cur = money.currency || "ZAR";

    // Header: who + money summary + month-end "issue invoice".
    var head = el("div", { class: "cf-card" }, [
      el("div", { class: "cf-row", style: "justify-content:space-between;align-items:flex-start;gap:10px;flex-wrap:wrap" }, [
        el("div", {}, [
          el("h2", { style: "margin:0", text: clientName(c) }),
          el("div", { class: "cf-muted", text: (c.email || "—") + (c.phone ? " · " + c.phone : "") }),
        ]),
        el("button", { class: "cf-btn cf-btn-primary", text: "Issue invoice →",
          onclick: function () { issueInvoice(userId, c); } }),
      ]),
    ]);
    head.appendChild(el("div", { style: "margin-top:12px" }, [window.CRMUI.stats([
      { value: c.lessons_count || 0, label: "Lessons (all time)" },
      { value: fmtMoney(money.paid_minor), label: "Paid this month" },
      { value: fmtMoney(money.owed_minor), label: "Owed now" },
      { value: fmtMoney(money.written_off_minor), label: "Written off" },
    ])]));
    body.appendChild(head);

    // Owed lessons (with actions) — the heart of month-end review.
    var owedCard = el("div", { class: "cf-card", style: "margin-top:16px" }, [
      window.CRMUI.sectionHead("Owed & written-off lessons"),
      window.CRMUI.lineItems((c.arrears || []), {
        currency: cur,
        label: function () { return "Lesson"; },
        sub: function (it) { return it.starts_at ? fmtDate(it.starts_at) : ""; },
        empty: "Nothing outstanding with you.",
        actions: [
          { label: "Mark collected", tone: "primary", onClick: function (it) { cpArrears(it.id, "collect", userId); } },
          { label: "Discount", onClick: function (it) { cpArrears(it.id, "discount", userId, it); } },
          { label: "Write off", tone: "danger", onClick: function (it) { cpArrears(it.id, "writeoff", userId); } },
        ],
      }),
    ]);
    body.appendChild(owedCard);

    // Upcoming sessions — with cancel / reschedule on lessons (booking_id present).
    var upCard = el("div", { class: "cf-card", style: "margin-top:16px" }, [window.CRMUI.sectionHead("Upcoming")]);
    var up = (c.upcoming || []);
    if (!up.length) upCard.appendChild(el("div", { class: "cf-empty", text: "No upcoming sessions." }));
    else {
      var ul = el("div", { class: "cf-list" });
      up.forEach(function (h) { ul.appendChild(bookingRow(h, userId, true)); });
      upCard.appendChild(ul);
    }
    body.appendChild(upCard);

    // Sessions this month (history filtered to the selected month) — read-only record.
    var ym = clientView.month;
    var monthHist = (c.history || []).filter(function (h) { return (h.starts_at || "").slice(0, 7) === ym; });
    var histCard = el("div", { class: "cf-card", style: "margin-top:16px" }, [
      window.CRMUI.sectionHead("Sessions in " + monthLabel(ym)),
    ]);
    if (!monthHist.length) histCard.appendChild(el("div", { class: "cf-empty", text: "No sessions this month." }));
    else {
      var hl = el("div", { class: "cf-list" });
      monthHist.forEach(function (h) { hl.appendChild(bookingRow(h, userId, false)); });
      histCard.appendChild(hl);
    }
    body.appendChild(histCard);
  }

  function bookingRow(h, userId, allowActions) {
    var time = "";
    try { time = new Date(h.starts_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); } catch (e) {}
    var kids = [
      el("span", { class: "cf-chip " + (h.kind || ""), text: h.kind || "session" }),
      el("div", { class: "cf-item-main" }, [
        el("div", { class: "cf-item-t", text: fmtDate(h.starts_at) + (time ? " · " + time : "") }),
        el("div", { class: "cf-item-s", text: h.status || "" }),
      ]),
    ];
    // Manage a future LESSON (classes have no booking_id → managed on the class roster).
    if (allowActions && h.booking_id && h.kind === "lesson") {
      var row = el("div", { class: "cf-row", style: "gap:6px" }, [
        el("button", { class: "cf-btn cf-btn-sm", text: "Reschedule", onclick: function () { rescheduleModal(h, userId); } }),
        el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Cancel", onclick: function () { cancelBooking(h, userId); } }),
      ]);
      kids.push(row);
    }
    return el("div", { class: "cf-item" }, kids);
  }

  // arrears actions from the client page (refresh the client page, not the Money tab).
  async function cpArrears(id, action, userId, it) {
    try {
      if (action === "collect") { await window.CoachAPI.arrearsCollected(id); UI.toast("Marked collected.", "info"); }
      else if (action === "discount") {
        var v = window.prompt("New amount for this lesson (e.g. 250.00):", (((it && it.gross_minor) || 0) / 100).toFixed(2));
        if (v === null) return;
        var f = parseFloat(v); if (isNaN(f) || f < 0) { UI.toast("Enter a valid amount.", "warn"); return; }
        await window.CoachAPI.arrearsAdjust(id, { gross_minor: Math.round(f * 100) }); UI.toast("Discount applied.", "info");
      } else {
        var reason = window.prompt("Write off this lesson? Reason (shown to you, the client and the club):", "");
        if (reason === null) return;
        await window.CoachAPI.arrearsAdjust(id, { status: "written_off", reason: reason }); UI.toast("Written off.", "info");
      }
      renderClientPage(host2(), userId);
    } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  async function cancelBooking(h, userId) {
    if (!window.confirm("Cancel this lesson on " + fmtDate(h.starts_at) + "? The slot is freed and the client is notified.")) return;
    var reason = window.prompt("Reason (optional, shown to the client):", "") || "";
    try { await window.API.cancelBooking(h.booking_id, { reason: reason }); UI.toast("Lesson cancelled.", "info"); renderClientPage(host2(), userId); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  function rescheduleModal(h, userId) {
    var m = modalShell("Reschedule lesson");
    var startInput = el("input", { class: "cf-input", type: "datetime-local", value: toLocalInput(h.starts_at) });
    var durSel = el("select", { class: "cf-input" }, [30, 45, 60, 90, 120].map(function (d) {
      return el("option", { value: String(d), text: d + " min" + (d === 60 ? " (default)" : "") });
    }));
    durSel.value = "60";
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "New start" }), startInput]));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Duration" }), durSel]));
    m.body.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;gap:8px;margin-top:12px" }, [
      el("button", { class: "cf-btn", text: "Cancel", onclick: m.close }),
      el("button", { class: "cf-btn cf-btn-primary", text: "Reschedule", onclick: async function () {
        if (!startInput.value) { UI.toast("Pick a new time.", "warn"); return; }
        var start = new Date(startInput.value);
        var end = new Date(start.getTime() + parseInt(durSel.value, 10) * 60000);
        try {
          await window.API.rescheduleBooking(h.booking_id, { starts_at: start.toISOString(), ends_at: end.toISOString(), scope: "this" });
          UI.toast("Lesson rescheduled.", "info"); m.close(); renderClientPage(host2(), userId);
        } catch (e) { UI.toast(UI.errMsg(e), "error"); }
      } }),
    ]));
  }

  function toLocalInput(iso) {
    try {
      var d = new Date(iso);
      var pad = function (n) { return (n < 10 ? "0" : "") + n; };
      return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + "T" + pad(d.getHours()) + ":" + pad(d.getMinutes());
    } catch (e) { return ""; }
  }

  async function issueInvoice(userId, c) {
    if (!window.confirm("Send " + clientName(c) + " their statement for " + monthLabel(clientView.month) + "?\n\nThey'll be notified with the amount owed and a link to pay online.")) return;
    try {
      var res = await window.CoachAPI.issueInvoice(userId, clientView.month);
      if (res.notified) UI.toast("Statement sent — " + fmtMoney(res.owed_minor) + " owed.", "info");
      else UI.toast("Nothing owed — nothing to send.", "info");
      // Open the printable invoice in a new tab.
      window.open("/invoice.html?client=" + encodeURIComponent(userId) + "&month=" + encodeURIComponent(clientView.month), "_blank");
    } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  // ---- my profile (clean summary + edit popups) -----------------------------
  // Profile/hours editing live in MODALS (reusing the CoachUI builders the onboarding wizard
  // uses), so the Profile tab is a tidy at-a-glance summary — not a wall of form fields.
  var profileState = { data: null };

  function initials() {
    var pr = (profileState.data && profileState.data.profile) || {};
    var n = (pr.display_name || principal.email || "C").trim();
    var parts = n.split(/\s+/);
    return ((parts[0] || "C")[0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
  }

  function tabProfile(host) {
    var d = profileState.data || {}, pr = d.profile || {}, steps = d.steps || {};

    // Hours warning — a coach with no weekly hours is invisible/unbookable.
    if (!steps.hours) {
      host.appendChild(el("div", { class: "cf-card", style: "border-color:#E0A800;background:#FFF8E1;display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap" }, [
        el("div", {}, [
          el("div", { style: "font-weight:700;color:var(--ink)", text: "⚠️ Set your weekly hours to take bookings" }),
          el("div", { class: "cf-muted", style: "font-size:.88rem", text: "Until you add your availability, clients can't see or book you." }),
        ]),
        el("button", { class: "cf-btn cf-btn-primary", type: "button", text: "Set your hours", onclick: editHoursModal }),
      ]));
    }

    var avatar = pr.photo_url
      ? el("img", { src: pr.photo_url, alt: "", style: "width:72px;height:72px;border-radius:50%;object-fit:cover;border:1px solid var(--border)" })
      : el("div", { style: "width:72px;height:72px;border-radius:50%;background:var(--green-050);color:var(--green-600);display:flex;align-items:center;justify-content:center;font-weight:800;font-size:1.5rem", text: initials() });

    var chips = el("div", { class: "cf-row", style: "gap:6px;flex-wrap:wrap;margin-top:6px" });
    chips.appendChild(el("span", { class: "cf-chip " + (pr.is_bookable === false ? "" : "class"), text: pr.is_bookable === false ? "Hidden" : "Bookable" }));
    chips.appendChild(el("span", { class: "cf-chip", text: pr.review_bookings ? "Reviews lesson requests" : "Auto-confirms lessons" }));
    if (steps.hours) chips.appendChild(el("span", { class: "cf-chip", text: "Hours set" }));

    var card = el("div", { class: "cf-card" }, [
      el("div", { class: "cf-row", style: "gap:16px;align-items:flex-start;flex-wrap:wrap" }, [
        avatar,
        el("div", { style: "flex:1;min-width:200px" }, [
          el("h2", { style: "margin:0", text: pr.display_name || (principal.email || "").split("@")[0] }),
          pr.headline ? el("div", { class: "cf-muted", style: "font-weight:600;margin-top:2px", text: pr.headline }) : null,
          chips,
        ].filter(Boolean)),
        el("div", { class: "cf-row", style: "gap:8px" }, [
          el("button", { class: "cf-btn cf-btn-primary", text: "Edit profile", onclick: editProfileModal }),
          el("button", { class: "cf-btn", text: "Edit hours", onclick: editHoursModal }),
        ]),
      ]),
    ]);
    if (pr.bio) card.appendChild(el("p", { style: "margin-top:14px", text: pr.bio }));
    var facts = [];
    if ((pr.specialties || []).length) facts.push(["Specialties", pr.specialties.join(", ")]);
    if ((pr.languages || []).length) facts.push(["Languages", pr.languages.join(", ")]);
    if ((pr.qualifications || []).length) facts.push(["Qualifications", pr.qualifications.join(", ")]);
    if (pr.years_experience) facts.push(["Experience", pr.years_experience + " years"]);
    if (facts.length) {
      var grid = el("div", { class: "cf-grid cf-grid-2", style: "margin-top:14px" });
      facts.forEach(function (f) {
        grid.appendChild(el("div", {}, [
          el("div", { class: "cf-muted cf-tiny", text: f[0] }),
          el("div", { style: "font-weight:600", text: f[1] }),
        ]));
      });
      card.appendChild(grid);
    }
    host.appendChild(card);
  }

  function modalShell(title) {
    var bg = el("div", { class: "cf-modal-bg" });
    var body = el("div", {});
    var modal = el("div", { class: "cf-modal cf-modal-lg" }, [
      el("div", { class: "cf-row", style: "justify-content:space-between;align-items:flex-start" }, [
        el("h2", { text: title }),
        el("button", { class: "cf-btn cf-btn-sm", text: "✕", onclick: function () { document.body.removeChild(bg); } }),
      ]),
      body,
    ]);
    bg.appendChild(modal); document.body.appendChild(bg);
    return { bg: bg, body: body, close: function () { if (bg.parentNode) document.body.removeChild(bg); } };
  }
  function editProfileModal() {
    var m = modalShell("Edit profile");
    window.CoachUI.profile(m.body, (profileState.data && profileState.data.profile) || {}, {
      saveLabel: "Save changes", onSaved: function () { m.close(); loadProfile(); },
    });
  }
  function editHoursModal() {
    var m = modalShell("Weekly hours");
    window.CoachUI.hours(m.body, (profileState.data && profileState.data.hours) || {}, {
      saveLabel: "Save hours", onSaved: function () { m.close(); loadProfile(); },
    });
  }

  async function loadProfile() {
    try {
      var ob = await window.CoachAPI.onboarding();
      try {
        var pr = await window.CoachAPI.profile();
        if (pr && pr.profile) ob.profile = Object.assign({}, ob.profile || {}, pr.profile);
      } catch (e2) {}
      profileState.data = ob;
    } catch (e) { profileState.data = {}; }
    // Re-render so the greeting ribbon (name + Bookable/Hidden chip) reflects the loaded/edited
    // profile; renderTab() clears+rebuilds the current tab so nothing duplicates.
    render();
  }

  // ---- pending lesson queue (approval lifecycle) ----------------------------
  // requested = a client asked for a lesson with this coach (awaiting the coach);
  // proposed  = the coach (or the system) proposed a time (awaiting the client).
  // Accept/Propose-time/Decline drive the diary lifecycle; proposed rows are read-only
  // "sent — awaiting client". Injected right after the dashboard so it's high on the page.
  function bookingTitle(b) {
    // Lead with WHO requested (list_bookings now returns booked_by_name/email), then the
    // resource + settlement hint. Falls back to the resource if no name is available.
    var who = b.booked_by_name || b.booked_by_email;
    var what = (b.resource_name || "Lesson") + (b.settlement_mode ? " · " + UI.settlementLabel(b.settlement_mode) : "");
    return who ? (who + " · " + what) : what;
  }

  function initPending(host) {
    if (!host) return;
    var card = el("div", { class: "cf-card", id: "coach-pending-card" }, [
      el("div", { class: "cf-row", style: "margin-bottom:6px;align-items:center" }, [
        el("h2", { text: "Lesson requests", style: "margin:0" }),
        el("span", { class: "cf-spacer" }),
        el("button", { class: "cf-btn cf-btn-sm", id: "coach-pending-refresh", text: "Refresh" }),
      ]),
      el("p", { class: "cf-muted", style: "margin:-2px 0 12px",
        text: "Accept, propose a new time, or decline. Items you’ve proposed wait for the client to accept." }),
      el("div", { id: "coach-pending-body", class: "cf-loading", text: "Loading requests…" }),
    ]);
    host.appendChild(card);
    document.getElementById("coach-pending-refresh").addEventListener("click", loadPending);
    loadPending();
  }

  async function loadPending() {
    var body = document.getElementById("coach-pending-body"); if (!body) return;
    UI.clear(body); body.appendChild(el("div", { class: "cf-loading", text: "Loading requests…" }));
    try {
      var [reqR, propR] = await Promise.all([
        window.CoachAPI.pendingLessons("requested"),
        window.CoachAPI.pendingLessons("proposed"),
      ]);
      renderPending(reqR.bookings || [], propR.bookings || []);
    } catch (e) { UI.clear(body); body.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
  }

  function renderPending(requested, proposed) {
    var body = document.getElementById("coach-pending-body"); if (!body) return;
    UI.clear(body);
    if (!requested.length && !proposed.length) {
      body.appendChild(el("div", { class: "cf-empty", text: "No pending lesson requests." }));
      return;
    }
    if (requested.length) {
      body.appendChild(window.CRMUI.sectionHead("Awaiting you"));
      var items = requested.map(function (b) {
        return { id: b.id, status: "requested", title: bookingTitle(b),
          starts_at: b.starts_at, ends_at: b.ends_at };
      });
      body.appendChild(window.CRMUI.requestQueue(items, {
        onAccept: function (it) { acceptPending(it.id); },
        onPropose: function (it) { openProposeTime(it); },
        onDecline: function (it) { declinePending(it.id); },
      }));
    }
    if (proposed.length) {
      body.appendChild(el("div", { style: "margin-top:14px" }));
      body.appendChild(window.CRMUI.sectionHead("Sent — awaiting client"));
      // Read-only: a proposal you sent; the client accepts (or counters).
      var plist = el("div", { class: "cf-list" });
      proposed.forEach(function (b) {
        plist.appendChild(el("div", { class: "cf-item" }, [
          el("span", { class: "cf-chip lesson", text: "proposed" }),
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: bookingTitle(b) }),
            el("div", { class: "cf-item-s", text: UI.fmtRange(b.starts_at, b.ends_at) }),
          ]),
          el("span", { class: "cf-muted", style: "font-size:.82rem", text: "awaiting client" }),
        ]));
      });
      body.appendChild(plist);
    }
  }

  async function acceptPending(id) {
    try { await window.API.acceptBooking(id); UI.toast("Lesson confirmed.", "info"); loadPending(); if (TAB === "schedule") renderTab(); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  async function declinePending(id) {
    var reason = window.prompt("Decline this lesson? Optional reason for the client:", "");
    if (reason === null) return;
    try { await window.API.declineBooking(id, { reason: reason || undefined }); UI.toast("Lesson declined.", "info"); loadPending(); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  // Propose a new time for a requested lesson -> POST /bookings/:id/propose {starts_at,ends_at}.
  function openProposeTime(it) {
    var bg = el("div", { class: "cf-modal-bg" });
    function close() { if (bg.parentNode) document.body.removeChild(bg); }
    // Default to the originally-requested start; keep the same duration.
    var defStart = "";
    var durMin = 60;
    try {
      if (it.starts_at) {
        var s = new Date(it.starts_at);
        if (it.ends_at) durMin = Math.max(15, Math.round((new Date(it.ends_at) - s) / 60000));
        // datetime-local needs local YYYY-MM-DDTHH:MM
        var pad = function (n) { return ("0" + n).slice(-2); };
        defStart = s.getFullYear() + "-" + pad(s.getMonth() + 1) + "-" + pad(s.getDate()) +
          "T" + pad(s.getHours()) + ":" + pad(s.getMinutes());
      }
    } catch (e) {}
    var when = el("input", { class: "cf-input", type: "datetime-local", value: defStart });
    var dur = el("select", { class: "cf-select" }, [
      el("option", { value: "30", text: "30 min" }), el("option", { value: "45", text: "45 min" }),
      el("option", { value: "60", text: "60 min" }), el("option", { value: "90", text: "90 min" }),
      el("option", { value: "120", text: "120 min" }),
    ]);
    dur.value = String([30, 45, 60, 90, 120].indexOf(durMin) >= 0 ? durMin : 60);
    var modal = el("div", { class: "cf-modal" }, [
      el("h2", { text: "Propose a new time" }),
      el("p", { class: "cf-muted", text: "The client will be asked to accept this time." }),
      el("div", { class: "cf-field" }, [el("label", { text: "New start" }), when]),
      el("div", { class: "cf-field" }, [el("label", { text: "Duration" }), dur]),
      el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px" }, [
        el("button", { class: "cf-btn", text: "Cancel", onclick: close }),
        el("button", { class: "cf-btn cf-btn-primary", text: "Send proposal", onclick: submit }),
      ]),
    ]);
    bg.appendChild(modal); document.body.appendChild(bg);
    bg.addEventListener("click", function (e) { if (e.target === bg) close(); });
    async function submit() {
      if (!when.value) { UI.toast("Pick a date and time.", "warn"); return; }
      var s = new Date(when.value), e2 = new Date(s.getTime() + parseInt(dur.value, 10) * 60000);
      try {
        await window.API.proposeTime(it.id, { starts_at: s.toISOString(), ends_at: e2.toISOString() });
        close(); UI.toast("New time proposed.", "info"); loadPending();
      } catch (e3) { UI.toast(UI.errMsg(e3), "error"); }
    }
  }

  // ---- month-end statement (commission settlement) --------------------------
  // GET /api/admin/coach-statement (a coach sees their own) → per-client table +
  // arrears line items with Mark-collected / Discount / Write-off actions, plus
  // month navigation and totals (incl. the month-end position after commission).
  var stmtState = { month: null };

  function initStatement(host) {
    if (!host) return;
    // Disputes — refund requests on this coach's coaching services (they decide; club oversees).
    var dispCard = el("div", { class: "cf-card", id: "coach-disp-card", style: "margin-bottom:16px;display:none" }, [
      el("h2", { text: "Refund requests", style: "margin:0 0 4px" }),
      el("p", { class: "cf-muted", style: "margin:-2px 0 12px",
        text: "A client has asked for a refund on one of your lessons — you decide. Approving refunds the client and reverses your commission." }),
      el("div", { id: "coach-disp-body" }),
    ]);
    host.appendChild(dispCard);
    var card = el("div", { class: "cf-card", id: "coach-stmt-card" }, [
      el("div", { class: "cf-row", style: "margin-bottom:6px;align-items:center;gap:8px" }, [
        el("h2", { text: "Month-end statement", style: "margin:0" }),
        el("span", { class: "cf-spacer" }),
        el("button", { class: "cf-btn cf-btn-sm", id: "coach-stmt-prev", text: "‹ Prev" }),
        el("span", { class: "cf-chip", id: "coach-stmt-month", text: "…" }),
        el("button", { class: "cf-btn cf-btn-sm", id: "coach-stmt-next", text: "Next ›" }),
      ]),
      el("p", { class: "cf-muted", style: "margin:-2px 0 12px",
        text: "What you’ve collected and what’s still owed this month, net of commission. Mark off-platform payments collected, discount, or write off." }),
      el("div", { id: "coach-stmt-body", class: "cf-loading", text: "Loading statement…" }),
    ]);
    host.appendChild(card);
    // Transaction log — a transparent, chronological record of every money event on this coach.
    var actCard = el("div", { class: "cf-card", style: "margin-top:16px" }, [
      el("h2", { text: "Activity", style: "margin:0 0 4px" }),
      el("p", { class: "cf-muted", style: "margin:-2px 0 12px",
        text: "Every lesson, payment, refund and adjustment — the full record." }),
      el("div", { id: "coach-act-body", class: "cf-loading", text: "Loading activity…" }),
    ]);
    host.appendChild(actCard);
    stmtState.month = thisMonthKey();
    document.getElementById("coach-stmt-prev").addEventListener("click", function () {
      stmtState.month = shiftMonthKey(stmtState.month, -1); loadStatement();
    });
    document.getElementById("coach-stmt-next").addEventListener("click", function () {
      stmtState.month = shiftMonthKey(stmtState.month, 1); loadStatement();
    });
    loadStatement();
    loadActivity();
    loadDisputes();
  }

  async function loadDisputes() {
    var card = document.getElementById("coach-disp-card");
    var body = document.getElementById("coach-disp-body");
    if (!card || !body) return;
    try {
      var d = await window.CoachAPI.refundRequests("pending");
      var reqs = (d && d.requests) || [];
      if (!reqs.length) { card.style.display = "none"; return; }
      card.style.display = "";
      UI.clear(body);
      body.appendChild(window.CRMUI.lineItems(reqs.map(function (r) {
        return {
          id: r.id, gross_minor: (r.amount_minor != null ? r.amount_minor : r.order_amount_minor),
          currency: r.currency_code, _name: r.requester_name || "A client",
          _sub: [r.item_description || "Lesson", r.reason ? ("“" + r.reason + "”") : ""].filter(Boolean).join(" · "),
        };
      }), {
        currency: reqs[0].currency_code || "ZAR",
        label: function (it) { return it._name; },
        sub: function (it) { return it._sub; },
        empty: "No refund requests.",
        actions: [
          { label: "Approve refund", tone: "primary", onClick: function (it) { decideDispute(it.id, "approve"); } },
          { label: "Decline", tone: "danger", onClick: function (it) { decideDispute(it.id, "decline"); } },
        ],
      }));
    } catch (e) { card.style.display = "none"; }
  }

  async function decideDispute(id, action) {
    var isApprove = action === "approve";
    var note = window.prompt(isApprove
      ? "Approve this refund? The client is refunded and your commission is reversed.\n\nNote (optional):"
      : "Decline this refund request?\n\nReason (shown to the client):", "");
    if (note === null) return;
    try {
      if (isApprove) await window.CoachAPI.approveRefund(id, { note: note });
      else await window.CoachAPI.declineRefund(id, { note: note });
      UI.toast(isApprove ? "Refund approved." : "Request declined.", "info");
      loadDisputes(); loadStatement(); loadActivity();
    } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  async function loadActivity() {
    var body = document.getElementById("coach-act-body"); if (!body) return;
    try {
      var d = await window.CoachAPI.activity();
      UI.clear(body);
      body.appendChild(window.CRMUI.activityFeed((d && d.activity) || [],
        { empty: "No activity yet." }));
    } catch (e) { UI.clear(body); body.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
  }

  async function loadStatement() {
    var body = document.getElementById("coach-stmt-body"); if (!body) return;
    var ml = document.getElementById("coach-stmt-month");
    if (ml) ml.textContent = monthLabel(stmtState.month);
    UI.clear(body); body.appendChild(el("div", { class: "cf-loading", text: "Loading statement…" }));
    try {
      var d = await window.CoachAPI.statement(stmtState.month);
      renderStatement(d || {});
    } catch (e) { UI.clear(body); body.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
  }

  function renderStatement(d) {
    var body = document.getElementById("coach-stmt-body"); if (!body) return;
    UI.clear(body);
    var cur = d.currency || "ZAR";
    var t = d.totals || {};

    // Totals strip — incl. the month-end position after commission (ledger balance + rent).
    var strip = [
      { value: window.CRMUI.money(t.paid_minor, cur), label: "Collected (net)" },
      { value: window.CRMUI.money(t.owed_minor, cur), label: "Outstanding" },
      { value: window.CRMUI.money(t.rent_minor, cur), label: "Rent this month" },
      { value: window.CRMUI.money(t.balance_minor, cur), label: "Account balance" },
    ];
    if (t.written_off_minor) strip.push({ value: window.CRMUI.money(t.written_off_minor, cur), label: "Written off" });
    body.appendChild(window.CRMUI.stats(strip));

    // Per-client rollup — READ-ONLY here (the month-end overview). Managing an individual client's
    // lessons (collect / discount / write off) + issuing their invoice lives in the CLIENTS tab, so
    // there's ONE place per client. Tap a row to jump straight there.
    body.appendChild(el("div", { style: "margin-top:14px" }));
    body.appendChild(window.CRMUI.sectionHead("By client"));
    body.appendChild(el("p", { class: "cf-muted", style: "margin:-6px 0 8px;font-size:.85rem",
      text: "Tap a client to open their full record and manage or invoice them." }));
    body.appendChild(window.CRMUI.statementTable(d.clients, {
      nameKey: "client_name", nameLabel: "Client", currency: cur,
      onRow: function (r) {
        if (!r.client_user_id) return;
        clientView.selected = r.client_user_id; clientView.month = stmtState.month; TAB = "clients"; render();
      },
    }));
  }

  // ---- tab shell ------------------------------------------------------------
  var TABS = [
    { k: "dashboard", t: "Dashboard" },
    { k: "schedule", t: "Schedule" },
    { k: "clients", t: "Clients" },
    { k: "money", t: "Money" },
    { k: "setup", t: "Setup" },
  ];

  function render() {
    var main = document.getElementById("cf-main"); if (!main) return;
    UI.clear(main);
    var name = ((principal.email || "").split("@")[0]) || "Coach";
    var pr = (profileState.data && profileState.data.profile) || {};
    main.appendChild(window.CRMUI.greetBand({
      title: "Coach console",
      subtitle: (pr.display_name || name),
      chip: (pr.is_bookable === false ? "Hidden" : "Bookable"),
      actions: [
        { label: "Edit profile", onClick: editProfileModal },
        { label: "Edit hours", onClick: editHoursModal },
      ],
    }));
    var bar = el("nav", { class: "cf-nav", style: "margin:8px 0 18px;flex-wrap:wrap" });
    TABS.forEach(function (t) {
      var a = el("a", { href: "#" + t.k, text: t.t });
      if (TAB === t.k) a.classList.add("active");
      a.addEventListener("click", function (ev) { ev.preventDefault(); TAB = t.k; render(); });
      bar.appendChild(a);
    });
    main.appendChild(bar);
    main.appendChild(el("div", { id: "coach-tab" }));
    renderTab();
  }

  function renderTab() {
    var host = document.getElementById("coach-tab"); if (!host) return;
    UI.clear(host);
    if (TAB === "dashboard") tabDashboard(host);
    else if (TAB === "schedule") tabSchedule(host);
    else if (TAB === "clients") tabClients(host);
    else if (TAB === "money") tabMoney(host);
    else tabSetup(host);
  }

  // Dashboard — what needs action + the business cockpit (net of commission).
  function tabDashboard(host) {
    initPending(host);      // "Needs your attention" — lesson requests / proposed times
    initDashboard(host);    // KPIs · earnings trend · month-end position · top clients · upcoming
  }

  // Schedule — a week TIMELINE of my lessons + classes (master-diary style) + book-for-a-client
  // + block time off.
  function tabSchedule(host) {
    coachWeek(host);
    timeOffCard(host);
  }

  function timeOffCard(host) {
    var toResource = el("select", { class: "cf-select", id: "to-resource" }, [el("option", { text: "Loading…" })]);
    var toReason = el("input", { class: "cf-input", id: "to-reason", placeholder: "e.g. holiday" });
    var toStart = el("input", { class: "cf-input", id: "to-start", type: "datetime-local" });
    var toEnd = el("input", { class: "cf-input", id: "to-end", type: "datetime-local" });
    var toSubmit = el("button", { class: "cf-btn cf-btn-primary", id: "to-submit", text: "Block time" });
    host.appendChild(el("div", { class: "cf-card" }, [
      el("h2", { text: "Block time off" }),
      el("p", { class: "cf-muted cf-tiny", style: "margin:-2px 0 10px", text: "Blocked time is removed from your bookable slots." }),
      el("div", { class: "cf-grid cf-grid-2" }, [
        fieldEl("Resource", toResource), fieldEl("Reason", toReason),
        fieldEl("From", toStart), fieldEl("To", toEnd),
      ]),
      toSubmit,
    ]));
    toSubmit.addEventListener("click", submitTimeOff);
    loadResources(); loadTimeOff();
  }

  function tabClients(host) { initMyClients(host); }

  // Money — the month-end settlement statement (per-client paid/owed + arrears actions). This is the
  // coach's single money view (the old standalone /statement.html is superseded).
  function tabMoney(host) { initStatement(host); }

  // Setup — everything the coach configures, in one place: services & pricing (+ the club's
  // commission, greyed) + classes, and the coach's own profile. Sub-tabbed to stay clean.
  function tabSetup(host) {
    host.appendChild(UI.subtabs(setupTab, [["services", "Services & pricing"], ["profile", "My profile"]],
      function (k) { setupTab = k; renderTab(); }));
    var body = el("div"); host.appendChild(body);
    if (setupTab === "profile") { tabProfile(body); return; }
    var box = el("div"); body.appendChild(box); renderServiceList(box);
    commissionCard(body);   // read-only "what the club keeps" — surfaced here so the coach can see it
    initMyClasses(body);    // class scheduling (sessions/rosters)
  }

  // Services — each service is ONE summary card → "Manage" opens the unified Service Editor
  // (prices · payment · packages · commission, all in one place). Rendered inside the Setup tab.
  var svcFilter = "active";
  function renderServiceList(box) {
    UI.clear(box);
    box.appendChild(el("div", { class: "cf-card" }, [el("div", { class: "cf-loading", text: "Loading your services…" })]));
    window.TFAuth.apiJSON("/api/services").then(function (r) {
      var svcs = r.services || [];
      UI.clear(box);
      box.appendChild(el("div", { class: "cf-card" }, [
        el("h2", { text: "My services" }),
        el("p", { class: "cf-muted", text: "Everything for a service — prices, payment options and packages — in one place. Click a service to edit." }),
      ]));
      box.appendChild(UI.lifecycleBar(svcFilter, function (f) { svcFilter = f; renderServiceList(box); }));
      var shown = svcs.filter(function (s) { return svcFilter === "all" || s.status === svcFilter; });
      if (!shown.length) { box.appendChild(el("div", { class: "cf-card cf-empty", text: "No " + (svcFilter === "all" ? "" : svcFilter + " ") + "services yet." })); return; }
      shown.forEach(function (s) { box.appendChild(serviceCard(s, box)); });
    }, function (e) { UI.clear(box); box.appendChild(el("div", { class: "cf-card cf-empty", text: UI.errMsg(e) })); });
  }
  function priceSummary(s) {
    var v = s.variations || [];
    if (!v.length) return "No prices set yet";
    var bits = v.slice(0, 4).map(function (x) { var amt = UI.money(x.amount_minor); return x.duration_minutes ? (x.duration_minutes + " min " + amt) : amt; });
    return bits.join("  ·  ") + (v.length > 4 ? "  · +" + (v.length - 4) + " more" : "");
  }
  function serviceCard(s, box) {
    function setStatus(ns) { window.TFAuth.apiJSON("/api/services/" + s.id, { method: "PATCH", body: { status: ns } }).then(function () { renderServiceList(box); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); }
    var nameKids = [el("span", { class: "cf-chip " + s.service_kind, text: s.service_kind }), el("strong", { text: s.name || "Service" })];
    if (s.status !== "active") nameKids.push(UI.statusChip(s.status));
    var card = el("div", { class: "cf-card cf-pickable" }, [
      el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap" }, [
        el("div", {}, [
          el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, nameKids),
          el("div", { class: "cf-muted cf-tiny", style: "margin-top:5px", text: priceSummary(s) }),
        ]),
        el("div", { class: "cf-row", style: "gap:6px" }, UI.lifeActions(s.status, setStatus, { terminateConfirm: "Terminate this service? Kept for history, removed from use." })),
      ]),
    ]);
    card.addEventListener("click", function () { window.ServiceEditor.open(s.id, { host: box, onClose: function () { renderServiceList(box); } }); });
    if (s.status !== "active") card.style.opacity = "0.6";
    return card;
  }

  // The club's commission on the coach's lessons — READ-ONLY (greyed). The owner sets the default %
  // (global) + per-service overrides in the admin portal; the coach only sees it here.
  function commissionCard(host) {
    UI.clear(host);
    host.appendChild(el("div", { class: "cf-card", style: "opacity:.85" }, [el("div", { class: "cf-loading", text: "Loading commission…" })]));
    window.CoachAPI.commission().then(function (d) {
      d = d || {};
      var keep = Math.max(0, 100 - (d.effective_pct || 0));
      var rows = [
        el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center" }, [
          el("h3", { text: "Commission", style: "margin:0" }),
          el("span", { class: "cf-chip", text: "Set by the club" }),
        ]),
        el("p", { class: "cf-muted", style: "margin:6px 0 10px", text: "What the club keeps on your lessons. You keep the rest. Only the club can change this." }),
        el("div", { class: "cf-tiles" }, [
          el("div", { class: "cf-tile", style: "cursor:default" }, [el("div", { class: "cf-tile-t", text: (d.effective_pct || 0) + "%" }), el("div", { class: "cf-tile-s", text: "Club commission" })]),
          el("div", { class: "cf-tile", style: "cursor:default" }, [el("div", { class: "cf-tile-t", text: keep + "%" }), el("div", { class: "cf-tile-s", text: "You keep" })]),
        ]),
      ];
      // Per-service overrides where they differ from the default.
      var diff = (d.services || []).filter(function (s) { return s.effective_pct !== d.effective_pct; });
      if (diff.length) {
        var list = el("div", { class: "cf-list", style: "margin-top:10px" });
        diff.forEach(function (s) {
          list.appendChild(el("div", { class: "cf-item" }, [
            el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: s.name || "Service" })]),
            el("span", { class: "cf-chip", text: s.effective_pct + "%" }),
          ]));
        });
        rows.push(el("div", { class: "cf-muted cf-tiny", style: "margin-top:8px", text: "Per-service rates:" }));
        rows.push(list);
      }
      UI.clear(host);
      host.appendChild(el("div", { class: "cf-card" }, rows));
    }, function () { UI.clear(host); });
  }

  window.CoachConsole = {
    start: function (p) {
      UI = window.UI; el = UI.el; principal = p;
      render();
      loadProfile();   // loads profile data in the background (used by the Profile tab + initials)
    },
  };
})();

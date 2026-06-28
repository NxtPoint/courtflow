// coach.js — coach console (docs/08 §2): my week (lessons + classes I run), class
// rosters + mark attendance, my availability/time-off editor (view + remove), and a
// read-only "My clients" view (gross activity with this coach only).
// Calls GET /api/diary/bookings?as_coach=1, GET /api/diary/classes, GET /api/diary/resources,
// POST /api/diary/bookings/:id/status, POST /api/diary/time-off, GET/DELETE /api/coach/time-off,
// GET /api/coach/clients[/:id].
(function () {
  var UI, el, principal;
  var TAB = "schedule";  // active console tab: schedule · services · clients · reporting · profile
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

  async function loadWeek() {
    var box = document.getElementById("coach-week");
    UI.clear(box); box.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
    try {
      var from = UI.dateKey(new Date());
      var to = UI.dateKey(UI.addDays(new Date(), 7));
      var [bk, cls] = await Promise.all([
        window.API.bookings({ date_from: from, date_to: to, as_coach: "1" }),
        window.API.classes({ date_from: from, date_to: to }),
      ]);
      renderWeek(bk.bookings || [], (cls.classes || []).filter(function (c) {
        return String(c.coach_user_id) === String(principal.user_id);
      }));
    } catch (e) { box.innerHTML = ""; box.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
  }

  function renderWeek(lessons, classes) {
    var box = document.getElementById("coach-week"); UI.clear(box);
    // "Book a session for a client" — a coach can create a booking ON BEHALF of a member
    // (it shows in THAT member's bookings) or a walk-in guest (docs/08).
    box.appendChild(el("div", { class: "cf-row", style: "margin-bottom:12px" }, [
      el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Book a session for a client",
        onclick: function () { openBookForClient(); } }),
    ]));
    if (!lessons.length && !classes.length) { box.appendChild(el("div", { class: "cf-empty", text: "Nothing scheduled this week." })); return; }

    if (lessons.length) {
      box.appendChild(el("h3", { text: "Lessons" }));
      var ll = el("div", { class: "cf-list" });
      lessons.forEach(function (b) {
        var actions = [];
        if (["held", "confirmed"].indexOf(b.status) >= 0) {
          actions.push(el("button", { class: "cf-btn cf-btn-sm", text: "Completed", onclick: function () { setStatus(b.id, "completed"); } }));
          actions.push(el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "No-show", onclick: function () { setStatus(b.id, "no_show"); } }));
        }
        // One line per lesson (the auto-held court is collapsed server-side). Title = the
        // CLIENT (the coach's own name as the resource is unhelpful here); the court the lesson
        // sits on is shown inline as "· Court 3".
        var sub = UI.fmtRange(b.starts_at, b.ends_at) + (b.court_name ? " · " + b.court_name : "");
        ll.appendChild(el("div", { class: "cf-item" }, [
          el("span", { class: "cf-chip lesson", text: "lesson" }),
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: b.booked_by_name || b.resource_name || "Lesson" }),
            el("div", { class: "cf-item-s", text: sub }),
          ]),
          el("span", { class: "cf-chip " + b.status, text: b.status }),
        ].concat(actions)));
      });
      box.appendChild(ll);
    }

    if (classes.length) {
      box.appendChild(el("h3", { text: "Class sessions this week", style: "margin-top:16px" }));
      var cl = el("div", { class: "cf-list" });
      classes.forEach(function (c) {
        cl.appendChild(el("div", { class: "cf-item" }, [
          el("span", { class: "cf-chip class", text: "class" }),
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: c.class_name || "Class" }),
            el("div", { class: "cf-item-s", text: UI.fmtRange(c.starts_at, c.ends_at) +
              " · " + (c.enrolled || 0) + " enrolled" + (c.waitlisted ? " · " + c.waitlisted + " waitlisted" : "") }),
          ]),
          el("button", { class: "cf-btn cf-btn-sm", text: "Roster", onclick: function () { openWeekRoster(c); } }),
        ]));
      });
      box.appendChild(cl);
    }
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
    try { await window.API.setBookingStatus(id, { status: status }); UI.toast("Updated.", "info"); loadWeek(); }
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
        loadWeek(); loadPending();
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
        el("a", { class: "cf-btn cf-btn-sm cf-btn-primary", href: "/statement.html",
          text: "Month-end statement →" }),
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

  // No-library trend: a row of vertical bars whose height is proportional to net
  // earnings that month, with the lesson count under each. Pure cf-* + inline layout
  // (heights are data-driven, which CSS classes can't express) — vanilla JS principle.
  function renderTrend(trend) {
    var wrap = el("div", { style: "margin-top:18px" }, [
      el("h3", { text: "Net earnings — last 6 months", style: "margin:0 0 10px" }),
    ]);
    if (!trend.length) {
      wrap.appendChild(el("div", { class: "cf-empty", text: "No history yet." }));
      return wrap;
    }
    var maxNet = trend.reduce(function (m, t) { return Math.max(m, t.net_minor || 0); }, 0);
    var bars = el("div", {
      style: "display:flex;align-items:flex-end;flex-wrap:nowrap;gap:10px;height:170px;" +
             "padding:6px 2px;overflow-x:auto" });
    trend.forEach(function (t) {
      var h = maxNet > 0 ? Math.max(4, Math.round((t.net_minor || 0) / maxNet * 120)) : 4;
      var lbl = "font-size:.8rem;margin:0";
      var col = el("div", {
        style: "flex:1;min-width:46px;align-items:center;gap:4px;display:flex;" +
               "flex-direction:column;justify-content:flex-end" }, [
        el("div", { class: "cf-muted", style: lbl, text: fmtMoney(t.net_minor) }),
        el("div", { title: monthLabel(t.month) + " · " + fmtMoney(t.net_minor) + " · " + (t.lessons || 0) + " lessons",
          style: "width:100%;height:" + h + "px;border-radius:8px 8px 0 0;" +
                 "background:linear-gradient(180deg,var(--green,#2e9e6b),#1f7d52)" }),
        el("div", { style: lbl + ";font-weight:700", text: shortMonth(t.month) }),
        el("div", { class: "cf-muted", style: lbl, text: (t.lessons || 0) + "L" }),
      ]);
      bars.appendChild(col);
    });
    wrap.appendChild(bars);
    return wrap;
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

  function initMyClients(host) {
    if (!host) return;
    var card = el("div", { class: "cf-card", id: "coach-clients-card" }, [
      el("div", { class: "cf-row", style: "margin-bottom:6px;align-items:center" }, [
        el("h2", { text: "My clients", style: "margin:0" }),
        el("span", { class: "cf-spacer" }),
        el("input", { class: "cf-input", id: "coach-clients-search",
          placeholder: "Search name or email…", style: "max-width:240px" }),
      ]),
      el("p", { class: "cf-muted", style: "margin:-2px 0 12px",
        text: "Everyone who has had a lesson or class with you. Gross activity with you only." }),
      el("div", { id: "coach-clients-body", class: "cf-loading", text: "Loading clients…" }),
    ]);
    host.appendChild(card);
    var search = document.getElementById("coach-clients-search");
    var t = null;
    search.addEventListener("input", function () {
      clearTimeout(t); t = setTimeout(function () { loadClients(search.value.trim()); }, 250);
    });
    loadClients("");
  }

  async function loadClients(q) {
    var box = document.getElementById("coach-clients-body"); if (!box) return;
    UI.clear(box); box.appendChild(el("div", { class: "cf-loading", text: "Loading clients…" }));
    try {
      var r = await window.CoachAPI.clients(q ? { search: q } : {});
      renderClients(r.clients || []);
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
    var thead = el("thead", {}, [el("tr", {}, [
      el("th", { text: "Client" }), el("th", { text: "Contact" }),
      el("th", { text: "Lessons" }), el("th", { text: "Classes" }),
      el("th", { text: "No-shows" }), el("th", { text: "Last seen" }),
      el("th", { text: "Spend (with you)" }),
    ])]);
    var tbody = el("tbody");
    list.forEach(function (c) {
      var tr = el("tr", { style: "cursor:pointer" });
      tr.addEventListener("click", function () { openClient(c.user_id); });
      tr.appendChild(el("td", {}, [el("strong", { text: clientName(c) })]));
      tr.appendChild(el("td", { text: c.email || c.phone || "—" }));
      tr.appendChild(el("td", { text: String(c.lessons_count || 0) }));
      tr.appendChild(el("td", { text: String(c.classes_count || 0) }));
      tr.appendChild(el("td", { text: String(c.no_show_count || 0) }));
      tr.appendChild(el("td", { text: fmtDate(c.last_seen) }));
      tr.appendChild(el("td", { text: fmtMoney(c.lifetime_spend_minor) }));
      tbody.appendChild(tr);
    });
    table.appendChild(thead); table.appendChild(tbody);
    box.appendChild(table);
  }

  // Client 360 — a slide-over (CRMUI.drawer) showing this client's history WITH THIS
  // coach only: headline metrics, spend-with-you, attendance, upcoming + full history.
  async function openClient(userId) {
    // Show the drawer immediately with a loading section, then refill once loaded.
    var close = window.CRMUI.drawer({ title: "Client", sections: [{ node: el("div", { class: "cf-loading", text: "Loading…" }) }] });
    var c;
    try {
      var r = await window.CoachAPI.client(userId);
      c = r.client || {};
    } catch (e) {
      close();
      window.CRMUI.drawer({ title: "Client", sections: [{ node: el("div", { class: "cf-empty", text: UI.errMsg(e) }) }] });
      return;
    }
    close();  // replace the loading drawer with the full one

    // Spend-with-this-coach + attendance summary as a stat strip.
    var statsNode = window.CRMUI.stats([
      { value: c.lessons_count || 0, label: "Lessons" },
      { value: c.classes_count || 0, label: "Classes" },
      { value: c.no_show_count || 0, label: "No-shows" },
      { value: fmtMoney(c.lifetime_spend_minor), label: "Spend with you" },
    ]);

    // Upcoming sessions (if the payload carries them; else derived from history is omitted).
    var upcomingNode = null;
    if ((c.upcoming || []).length) {
      var ul = el("div", { class: "cf-list" });
      (c.upcoming || []).forEach(function (h) {
        ul.appendChild(el("div", { class: "cf-item" }, [
          el("span", { class: "cf-chip " + (h.kind || ""), text: h.kind || "session" }),
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: fmtDate(h.starts_at) }),
            el("div", { class: "cf-item-s", text: (function () {
              try { return new Date(h.starts_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); } catch (e) { return ""; } })() }),
          ]),
        ]));
      });
      upcomingNode = ul;
    }

    // Full history with this coach.
    var histNode;
    var hist = c.history || [];
    if (!hist.length) { histNode = el("div", { class: "cf-empty", text: "No sessions yet." }); }
    else {
      var list = el("div", { class: "cf-list" });
      hist.forEach(function (h) {
        var time = "";
        try { time = new Date(h.starts_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); } catch (e3) {}
        list.appendChild(el("div", { class: "cf-item" }, [
          el("span", { class: "cf-chip " + (h.kind || ""), text: h.kind || "session" }),
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: fmtDate(h.starts_at) }),
            el("div", { class: "cf-item-s", text: time }),
          ]),
          el("span", { class: "cf-chip " + (h.status || ""), text: h.status || "" }),
        ]));
      });
      histNode = list;
    }

    var sections = [
      { node: statsNode },
      { h: "At a glance", rows: [
        ["Contact", (c.email || "—") + (c.phone ? " · " + c.phone : "")],
        ["First seen", fmtDate(c.first_seen)],
        ["Last seen", fmtDate(c.last_seen)],
        ["Upcoming", (c.upcoming_count || 0)],
      ] },
    ];
    if (upcomingNode) sections.push({ h: "Upcoming", node: upcomingNode });
    sections.push({ h: "History with you", node: histNode });

    window.CRMUI.drawer({
      title: clientName(c),
      subtitle: c.email || c.phone || "",
      sections: sections,
    });
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
    if (TAB === "profile") renderTab();
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
    try { await window.API.acceptBooking(id); UI.toast("Lesson confirmed.", "info"); loadPending(); loadWeek(); }
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
    stmtState.month = thisMonthKey();
    document.getElementById("coach-stmt-prev").addEventListener("click", function () {
      stmtState.month = shiftMonthKey(stmtState.month, -1); loadStatement();
    });
    document.getElementById("coach-stmt-next").addEventListener("click", function () {
      stmtState.month = shiftMonthKey(stmtState.month, 1); loadStatement();
    });
    loadStatement();
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
    body.appendChild(window.CRMUI.stats([
      { value: window.CRMUI.money(t.paid_minor, cur), label: "Collected (net)" },
      { value: window.CRMUI.money(t.owed_minor, cur), label: "Outstanding" },
      { value: window.CRMUI.money(t.rent_minor, cur), label: "Rent this month" },
      { value: window.CRMUI.money(t.balance_minor, cur), label: "Account balance" },
    ]));

    // Per-client table.
    body.appendChild(el("div", { style: "margin-top:14px" }));
    body.appendChild(window.CRMUI.sectionHead("By client"));
    body.appendChild(window.CRMUI.statementTable(d.clients, {
      nameKey: "client_name", nameLabel: "Client", currency: cur,
    }));

    // Outstanding arrears with actions.
    body.appendChild(el("div", { style: "margin-top:16px" }));
    body.appendChild(window.CRMUI.sectionHead("Outstanding lessons (off-platform)"));
    body.appendChild(window.CRMUI.lineItems(d.arrears_items, {
      currency: cur,
      label: function (it) { return it.client_name || "Lesson"; },
      sub: function (it) { return it.starts_at ? UI.fmtDate(it.starts_at) : ""; },
      empty: "Nothing outstanding.",
      actions: [
        { label: "Mark collected", tone: "primary", onClick: function (it) { arrearsCollected(it.id); } },
        { label: "Discount", onClick: function (it) { arrearsDiscount(it); } },
        { label: "Write off", tone: "danger", onClick: function (it) { arrearsWriteOff(it.id); } },
      ],
    }));
  }

  async function arrearsCollected(id) {
    try { await window.CoachAPI.arrearsCollected(id); UI.toast("Marked collected.", "info"); loadStatement(); loadDashboard(); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  async function arrearsDiscount(it) {
    var cur = window.prompt("New amount for this lesson (in your currency, e.g. 250.00):",
      ((it.gross_minor || 0) / 100).toFixed(2));
    if (cur === null) return;
    var f = parseFloat(cur);
    if (isNaN(f) || f < 0) { UI.toast("Enter a valid amount.", "warn"); return; }
    try { await window.CoachAPI.arrearsAdjust(it.id, { gross_minor: Math.round(f * 100) }); UI.toast("Discount applied.", "info"); loadStatement(); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  async function arrearsWriteOff(id) {
    if (!window.confirm("Write off this lesson? No commission will be charged and the client won’t owe it.")) return;
    try { await window.CoachAPI.arrearsAdjust(id, { status: "written_off" }); UI.toast("Written off.", "info"); loadStatement(); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  // ---- tab shell ------------------------------------------------------------
  var TABS = [
    { k: "schedule", t: "Schedule" },
    { k: "services", t: "Services" },
    { k: "clients", t: "Clients" },
    { k: "reporting", t: "Reporting" },
    { k: "profile", t: "Profile" },
  ];

  function render() {
    var main = document.getElementById("cf-main"); if (!main) return;
    UI.clear(main);
    var name = ((principal.email || "").split("@")[0]) || "Coach";
    main.appendChild(el("div", { class: "cf-row", style: "align-items:baseline;gap:10px;margin-bottom:4px" }, [
      el("h1", { text: "Coach console" }),
      el("span", { class: "cf-muted", text: name }),
    ]));
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
    if (TAB === "schedule") tabSchedule(host);
    else if (TAB === "services") tabServices(host);
    else if (TAB === "clients") tabClients(host);
    else if (TAB === "reporting") tabReporting(host);
    else tabProfile(host);
  }

  // Schedule — lesson requests + my week + book-for-a-client + time off.
  function tabSchedule(host) {
    initPending(host);
    host.appendChild(el("div", { class: "cf-card" }, [
      el("h2", { text: "My week" }),
      el("div", { id: "coach-week" }, [el("div", { class: "cf-loading", text: "Loading…" })]),
    ]));
    loadWeek();
    var toResource = el("select", { class: "cf-select", id: "to-resource" }, [el("option", { text: "Loading…" })]);
    var toReason = el("input", { class: "cf-input", id: "to-reason", placeholder: "e.g. holiday" });
    var toStart = el("input", { class: "cf-input", id: "to-start", type: "datetime-local" });
    var toEnd = el("input", { class: "cf-input", id: "to-end", type: "datetime-local" });
    var toSubmit = el("button", { class: "cf-btn cf-btn-primary", id: "to-submit", text: "Block time" });
    host.appendChild(el("div", { class: "cf-card" }, [
      el("h2", { text: "Block time off" }),
      el("div", { class: "cf-grid cf-grid-2" }, [
        fieldEl("Resource", toResource), fieldEl("Reason", toReason),
        fieldEl("From", toStart), fieldEl("To", toEnd),
      ]),
      toSubmit,
    ]));
    toSubmit.addEventListener("click", submitTimeOff);
    loadResources(); loadTimeOff();
  }

  // Services — each service is ONE summary card → "Manage" opens the unified Service Editor
  // (prices · payment · packages · commission, all in one place). Class scheduling stays separate
  // (operational), below. The editor is the single place a service is edited.
  function tabServices(host) {
    var box = el("div"); host.appendChild(box); renderServiceList(box);
    initMyClasses(host);  // class scheduling (sessions/rosters) — operational, not config
  }
  function renderServiceList(box) {
    UI.clear(box);
    box.appendChild(el("div", { class: "cf-card" }, [el("div", { class: "cf-loading", text: "Loading your services…" })]));
    window.TFAuth.apiJSON("/api/services").then(function (r) {
      var svcs = r.services || [];
      UI.clear(box);
      box.appendChild(el("div", { class: "cf-card" }, [
        el("h2", { text: "My services" }),
        el("p", { class: "cf-muted", text: "Everything for a service — prices, payment options and packages — in one place. Tap Manage." }),
      ]));
      if (!svcs.length) { box.appendChild(el("div", { class: "cf-card cf-empty", text: "No services yet — your lesson rates appear here once set in onboarding." })); return; }
      svcs.forEach(function (s) { box.appendChild(serviceCard(s, box)); });
    }, function (e) { UI.clear(box); box.appendChild(el("div", { class: "cf-card cf-empty", text: UI.errMsg(e) })); });
  }
  function serviceCard(s, box) {
    var hidden = s.active === false;
    var bits = [s.variation_count + " price" + (s.variation_count === 1 ? "" : "s")];
    if (s.from_amount_minor != null) bits.push("from " + UI.money(s.from_amount_minor));
    if (hidden) bits.push("hidden");
    function setActive(a) { window.TFAuth.apiJSON("/api/services/" + s.id, { method: "PATCH", body: { active: a } }).then(function () { renderServiceList(box); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); }
    var nameKids = [el("span", { class: "cf-chip " + s.service_kind, text: s.service_kind }), el("strong", { text: s.name || "Service" })];
    function edit() { window.ServiceEditor.open(s.id, { host: box, onClose: function () { renderServiceList(box); } }); }
    var card = el("div", { class: "cf-card cf-pickable" }, [
      el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap" }, [
        el("div", {}, [
          el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, nameKids),
          el("div", { class: "cf-muted cf-tiny", style: "margin-top:4px", text: bits.join(" · ") }),
        ]),
        el("button", { class: "cf-btn cf-btn-sm", text: hidden ? "Unhide" : "Hide", onclick: function (ev) { ev.stopPropagation(); setActive(hidden); } }),
      ]),
    ]);
    card.addEventListener("click", edit);
    if (hidden) card.style.opacity = "0.6";
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

  function tabClients(host) { initMyClients(host); }

  // Reporting — the business cockpit + the month-end settlement statement.
  function tabReporting(host) { initDashboard(host); initStatement(host); }

  window.CoachConsole = {
    start: function (p) {
      UI = window.UI; el = UI.el; principal = p;
      render();
      loadProfile();   // loads profile data in the background (used by the Profile tab + initials)
    },
  };
})();

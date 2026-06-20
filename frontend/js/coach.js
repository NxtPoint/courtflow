// coach.js — coach console (docs/08 §2): my week (lessons + classes I run), class
// rosters + mark attendance, my availability/time-off editor. No pricing/finance.
// Calls GET /api/diary/bookings?as_coach=1, GET /api/diary/classes, GET /api/diary/resources,
// POST /api/diary/bookings/:id/status, POST /api/diary/time-off.
(function () {
  var UI, el, principal;
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
        ll.appendChild(el("div", { class: "cf-item" }, [
          el("span", { class: "cf-chip lesson", text: "lesson" }),
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: b.resource_name || "Lesson" }),
            el("div", { class: "cf-item-s", text: UI.fmtRange(b.starts_at, b.ends_at) }),
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
        close(); UI.toast("Booked for client.", "info"); loadWeek();
      } catch (e3) { UI.toast(UI.errMsg(e3), "error"); }
    }
  }

  // ---- "My classes" management area -----------------------------------------
  // A coach creates/manages only their OWN classes: create a class type, schedule
  // recurring/one-off sessions, view/cancel sessions, open rosters + mark attendance.
  // Reuses the shared ClassUI components (same ones the admin console uses); the coach
  // form has no coach selector (the server attributes the class to the caller).
  // Injects its own card into #cf-main after the "My week" card (HTML shell is fixed).
  async function initMyClasses() {
    var main = document.getElementById("cf-main"); if (!main) return;
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
    // Place it right after the "My week" card (the first cf-card in main).
    var weekCard = document.getElementById("coach-week");
    var anchor = weekCard ? weekCard.closest(".cf-card") : null;
    if (anchor && anchor.nextSibling) main.insertBefore(card, anchor.nextSibling);
    else main.appendChild(card);

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
    } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  // ---- my profile editor ----------------------------------------------------
  // A tabbed editor (Profile · Hours · Services) reusing the CoachUI section
  // components from coach_api.js — the same builders the onboarding wizard uses, so
  // the wizard and the console stay 1:1. Lets a coach edit everything anytime.
  var profileState = { data: null, tab: "profile" };
  var PROFILE_TABS = [
    { k: "profile", t: "Profile" },
    { k: "hours", t: "Hours" },
    { k: "services", t: "Services" },
  ];

  function profileRoot() { return document.getElementById("coach-profile"); }

  function profileTabBar() {
    var nav = el("nav", { class: "cf-nav", style: "margin-bottom:16px" });
    PROFILE_TABS.forEach(function (tab) {
      var a = el("a", { href: "#" + tab.k, text: tab.t });
      if (tab.k === profileState.tab) a.classList.add("active");
      a.addEventListener("click", function (ev) { ev.preventDefault(); selectProfileTab(tab.k); });
      nav.appendChild(a);
    });
    return nav;
  }

  function selectProfileTab(k) { profileState.tab = k; renderProfile(); }

  function renderProfile() {
    var host = profileRoot(); if (!host) return;
    UI.clear(host);
    host.appendChild(el("div", { class: "cf-card" }, [
      el("h2", { text: "My profile" }),
      el("p", { class: "cf-muted", text: "Edit your coaching profile, working hours and services. Changes save per section." }),
    ]));
    host.appendChild(profileTabBar());
    var sectionHost = el("div");
    host.appendChild(sectionHost);

    var d = profileState.data || {};
    if (profileState.tab === "profile") {
      window.CoachUI.profile(sectionHost, d.profile || {}, { saveLabel: "Save changes" });
    } else if (profileState.tab === "hours") {
      window.CoachUI.hours(sectionHost, d.hours || {}, { saveLabel: "Save hours" });
    } else if (profileState.tab === "services") {
      window.CoachUI.services(sectionHost, {});
    }
  }

  async function loadProfile() {
    var host = profileRoot(); if (!host) return;
    UI.clear(host); host.appendChild(el("div", { class: "cf-card" }, [el("div", { class: "cf-loading", text: "Loading your profile…" })]));
    try {
      profileState.data = await window.CoachAPI.onboarding();
    } catch (e) {
      profileState.data = {};
      UI.toast(UI.errMsg(e), "error");
    }
    renderProfile();
  }

  window.CoachConsole = {
    start: function (p) {
      UI = window.UI; el = UI.el; principal = p;
      loadWeek(); loadResources(); loadProfile(); initMyClasses();
      document.getElementById("to-submit").addEventListener("click", submitTimeOff);
    },
  };
})();

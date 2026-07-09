// class_ui.js — shared class-management UI components, reused by BOTH the admin
// console (admin.js) and the coach console (coach.js). The class *enrol* path (member
// side) lives in book.js and is NOT touched here — this is the MANAGEMENT side.
//
// Every builder takes an `api` object (window.AdminAPI for the admin console, or
// window.CoachAPI for the coach console) that exposes the same class wrappers —
//   createClass, scheduleClass, classSessions, cancelClassSession, classRoster,
//   classAttendance — so the two consoles share one set of components. The only
// difference is the admin form offers a coach selector; the coach form does not.
//
// Pure presentation + the api calls above; depends on window.UI. Vanilla JS, no deps.
// Reuses the cf-* design system (cf-card, cf-btn, cf-table, cf-chip, cf-field/input,
// cf-modal, cf-list/item). Exposes window.ClassUI.
(function () {
  var UI, el;
  function init() { if (!UI) { UI = window.UI; el = UI.el; } }

  var WEEKDAYS = [
    { wd: 0, lbl: "Mon" }, { wd: 1, lbl: "Tue" }, { wd: 2, lbl: "Wed" },
    { wd: 3, lbl: "Thu" }, { wd: 4, lbl: "Fri" }, { wd: 5, lbl: "Sat" },
    { wd: 6, lbl: "Sun" },
  ];
  var DURATIONS = [
    { value: 45, label: "45 min" }, { value: 60, label: "60 min" },
    { value: 75, label: "75 min" }, { value: 90, label: "90 min" }, { value: 120, label: "120 min" },
  ];

  // ---- small form helpers (same shapes as AdminUI/CoachUI) -------------------
  function field(label, control) {
    return el("div", { class: "cf-field" }, [el("label", { text: label }), control]);
  }
  function input(opts) { return el("input", Object.assign({ class: "cf-input" }, opts || {})); }
  function textarea(opts) { return el("textarea", Object.assign({ class: "cf-input" }, opts || {})); }
  function select(value, options) {
    var s = el("select", { class: "cf-select" });
    (options || []).forEach(function (o) {
      var val = (typeof o === "object") ? o.value : o;
      var lbl = (typeof o === "object") ? o.label : o;
      var opt = el("option", { value: val, text: lbl });
      if (String(val) === String(value)) opt.selected = true;
      s.appendChild(opt);
    });
    return s;
  }
  function num(v) { var n = parseInt(v, 10); return isNaN(n) ? null : n; }
  // major currency string -> amount_minor (cents). "85.50" -> 8550.
  function toMinor(v) {
    if (v === "" || v == null) return null;
    var f = parseFloat(v); if (isNaN(f)) return null;
    return Math.round(f * 100);
  }
  function fromMinor(m) { return (m == null) ? "" : (m / 100).toFixed(2); }

  // ---- modal helper (same shape as admin.js / coach.js) ----------------------
  function modal(title, build) {
    var bg = el("div", { class: "cf-modal-bg" });
    function close() { if (bg.parentNode) document.body.removeChild(bg); }
    var m = el("div", { class: "cf-modal" }, [el("h2", { text: title })]);
    build(m, close);
    bg.appendChild(m); document.body.appendChild(bg);
    bg.addEventListener("click", function (e) { if (e.target === bg) close(); });
    return { bg: bg, close: close };
  }

  // ---------------------------------------------------------------------------
  // CLASS FORM — name, coach (admin only), capacity, price, duration, description.
  //   -> api.createClass({name, coach_user_id?, capacity, price_amount_minor,
  //                        duration_minutes, description}).
  // opts: {api, coaches?:[{user_id,name}] (admin only — shows a coach selector),
  //        title?, onSaved?(class)}. Renders into a fresh modal.
  // ---------------------------------------------------------------------------
  // Create OR EDIT a class type. opts.cls (edit) prefills name/coach/capacity/description + its courts;
  // editing calls api.updateClass(resource_id, …) and can (re)assign the coach + courts of upcoming
  // sessions. Create keeps price + duration (billing set-up); those are billing-side edits afterwards.
  function openClassForm(opts) {
    init(); opts = opts || {};
    var api = opts.api, editing = !!opts.cls, cls = opts.cls || {};
    var mc = modal(opts.title || (editing ? "Edit class" : "New class"), function (m, close) {
      var name = input({ placeholder: "Class name (e.g. Cardio Tennis)", value: cls.name || "" });
      var coachSel = null;
      if (opts.coaches && opts.coaches.length) {
        coachSel = select(cls.coach_user_id || "", [{ value: "", label: "— Select coach —" }].concat(
          opts.coaches.map(function (c) { return { value: c.user_id, label: c.name }; })));
      }
      var capacity = input({ type: "number", min: "1", value: cls.capacity != null ? String(cls.capacity) : "8", placeholder: "Max players" });
      var price = input({ placeholder: "0.00", style: "max-width:140px" });
      var dur = select(cls.duration_minutes || 60, DURATIONS);
      var desc = textarea({ rows: 3, placeholder: "Optional — what to expect, level, kit needed…" });
      desc.value = cls.description || "";

      // Court multi-select — EDIT only (courts apply to a class's upcoming sessions; on create there
      // are no sessions yet — courts are chosen when you Schedule). Pre-ticked for the class's courts.
      var courtToggles = [], courtBox = null;
      if (editing) {
        var preCourts = cls.court_resource_ids || [];
        courtBox = el("div", { class: "cf-row", style: "flex-wrap:wrap;gap:8px" }, [el("span", { class: "cf-muted", text: "Loading courts…" })]);
        if (window.API && typeof window.API.resources === "function") {
          window.API.resources().then(function (r) {
            UI.clear(courtBox); courtToggles = [];
            var courts = (r && r.resources || []).filter(function (x) { return x.kind === "court"; });
            if (!courts.length) { courtBox.appendChild(el("span", { class: "cf-muted", text: "No courts configured." })); return; }
            courts.forEach(function (c) {
              var cb = input({ type: "checkbox" });
              if (preCourts.indexOf(String(c.id)) >= 0) cb.checked = true;
              courtToggles.push({ id: String(c.id), cb: cb });
              courtBox.appendChild(el("label", { class: "cf-row", style: "gap:6px;min-width:90px;font-weight:600" }, [cb, el("span", { text: c.name })]));
            });
          }, function () { UI.clear(courtBox); courtBox.appendChild(el("span", { class: "cf-muted", text: "Couldn't load courts." })); });
        }
      }

      m.appendChild(field("Class name", name));
      if (coachSel) m.appendChild(field("Coach", coachSel));
      if (editing) m.appendChild(field("Capacity", capacity));
      else m.appendChild(el("div", { class: "cf-grid cf-grid-2" }, [field("Capacity", capacity), field("Duration", dur)]));
      if (!editing) m.appendChild(field("Price per session", price));
      if (editing && courtBox) m.appendChild(field("Courts (applied to upcoming sessions)", courtBox));
      m.appendChild(field("Description", desc));

      var save = el("button", { class: "cf-btn cf-btn-primary", text: editing ? "Save changes" : "Create class" });
      m.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px" }, [
        el("button", { class: "cf-btn", text: "Cancel", onclick: close }), save,
      ]));
      save.addEventListener("click", async function () {
        var nm = name.value.trim();
        if (!nm) { UI.toast("Enter a class name.", "warn"); return; }
        var cap = num(capacity.value);
        if (!cap || cap < 1) { UI.toast("Capacity must be at least 1.", "warn"); return; }
        var body = { name: nm, capacity: cap, description: desc.value.trim() };
        // A class must belong to a coach (its enrolments + commission attribute to them).
        if (coachSel) {
          if (!coachSel.value) { UI.toast("Pick the coach who runs this class.", "warn"); return; }
          body.coach_user_id = coachSel.value;
        }
        if (!editing) { body.price_amount_minor = toMinor(price.value); body.duration_minutes = num(dur.value) || 60; }
        else body.court_resource_ids = courtToggles.filter(function (t) { return t.cb.checked; }).map(function (t) { return t.id; });
        save.disabled = true; save.textContent = editing ? "Saving…" : "Creating…";
        try {
          var res = editing ? await api.updateClass(cls.resource_id, body) : await api.createClass(body);
          var conflicts = (res && res.coach_conflicts) || [];
          UI.toast(editing ? ("Class updated." + (conflicts.length ? " ⚠ " + conflicts.length + " upcoming session(s) clash with the coach's diary." : "")) : "Class created.", conflicts.length ? "warn" : "info");
          close();
          if (typeof opts.onSaved === "function") opts.onSaved(res || {});
        } catch (e) {
          save.disabled = false; save.textContent = editing ? "Save changes" : "Create class";
          UI.toast(UI.errMsg(e), "error");
        }
      });
    });
    return mc;
  }

  // ---------------------------------------------------------------------------
  // SCHEDULE FORM — recurring (weekday checkboxes Mon–Sun + start time + duration +
  //   date range) OR one-off date(s). -> api.scheduleClass(resourceId, {...}).
  // opts: {api, cls:{resource_id,name,capacity,duration_minutes}, onSaved?(result)}.
  // ---------------------------------------------------------------------------
  function openScheduleForm(opts) {
    init(); opts = opts || {};
    var api = opts.api, cls = opts.cls || {};
    var mc = modal("Schedule sessions · " + (cls.name || "Class"), function (m, close) {
      // mode toggle: recurring | one-off
      var modeRecurring = el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Recurring weekly" });
      var modeOneOff = el("button", { class: "cf-btn cf-btn-sm", text: "One-off date(s)" });
      m.appendChild(el("div", { class: "cf-row", style: "margin-bottom:12px" }, [modeRecurring, modeOneOff]));

      var startTime = input({ type: "time", value: "17:00", style: "max-width:140px" });
      var dur = select(cls.duration_minutes || 60, DURATIONS);
      var cap = input({ type: "number", min: "1", value: cls.capacity != null ? String(cls.capacity) : "",
        placeholder: "Default (class capacity)", style: "max-width:160px" });

      // Reserve one or MORE physical courts so the class books them out exactly like member court
      // bookings (both the coach AND every court are held; a busy court auto-repicks a free one).
      // Tick none to keep the class virtual (no court reserved). Pre-ticked for the class's courts on edit.
      var courtToggles = [];
      var preCourts = (opts.cls && opts.cls.court_resource_ids) || [];
      var courtBox = el("div", { class: "cf-row", style: "flex-wrap:wrap;gap:8px" }, [el("span", { class: "cf-muted", text: "Loading courts…" })]);
      if (window.API && typeof window.API.resources === "function") {
        window.API.resources().then(function (r) {
          UI.clear(courtBox); courtToggles = [];
          var courts = (r && r.resources || []).filter(function (x) { return x.kind === "court"; });
          if (!courts.length) { courtBox.appendChild(el("span", { class: "cf-muted", text: "No courts configured." })); return; }
          courts.forEach(function (c) {
            var cb = input({ type: "checkbox" });
            if (preCourts.indexOf(String(c.id)) >= 0) cb.checked = true;
            courtToggles.push({ id: String(c.id), cb: cb });
            courtBox.appendChild(el("label", { class: "cf-row", style: "gap:6px;min-width:90px;font-weight:600" }, [cb, el("span", { text: c.name })]));
          });
        }, function () { UI.clear(courtBox); courtBox.appendChild(el("span", { class: "cf-muted", text: "Couldn't load courts." })); });
      }

      // recurring fields
      var dayToggles = WEEKDAYS.map(function (d) {
        var cb = input({ type: "checkbox" });
        return { wd: d.wd, cb: cb,
          node: el("label", { class: "cf-row", style: "gap:6px;min-width:70px;font-weight:600" },
            [cb, el("span", { text: d.lbl })]) };
      });
      var dayGrid = el("div", { class: "cf-row", style: "flex-wrap:wrap;gap:8px" },
        dayToggles.map(function (d) { return d.node; }));
      var dateFrom = input({ type: "date", style: "max-width:170px", value: UI.dateKey(new Date()) });
      var dateUntil = input({ type: "date", style: "max-width:170px", value: UI.dateKey(UI.addDays(new Date(), 28)) });
      var recurringBox = el("div", {}, [
        field("On these days", dayGrid),
        el("div", { class: "cf-grid cf-grid-2" }, [
          field("From date", dateFrom), field("Until date", dateUntil),
        ]),
      ]);

      // one-off fields — repeatable date rows
      var oneOffRows = el("div", { class: "cf-list" });
      function addDateRow(val) {
        var di = input({ type: "date", style: "max-width:170px", value: val || "" });
        var rm = el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "✕" });
        var row = el("div", { class: "cf-row", style: "gap:8px" }, [di, rm]);
        rm.addEventListener("click", function () { if (row.parentNode) oneOffRows.removeChild(row); });
        row._date = di;
        oneOffRows.appendChild(row);
      }
      addDateRow(UI.dateKey(new Date()));
      var addDateBtn = el("button", { class: "cf-btn cf-btn-sm", text: "+ Add date",
        onclick: function () { addDateRow(""); } });
      var oneOffBox = el("div", { style: "display:none" }, [
        field("Dates", el("div", {}, [oneOffRows, el("div", { class: "cf-row", style: "margin-top:6px" }, [addDateBtn])])),
      ]);

      m.appendChild(recurringBox);
      m.appendChild(oneOffBox);
      m.appendChild(el("div", { class: "cf-grid cf-grid-2" }, [
        field("Start time", startTime), field("Duration", dur),
      ]));
      m.appendChild(field("Capacity per session (optional)", cap));
      m.appendChild(field("Reserve courts (optional — held so no one else can book them)", courtBox));

      var mode = "recurring";
      function setMode(next) {
        mode = next;
        var rec = next === "recurring";
        recurringBox.style.display = rec ? "" : "none";
        oneOffBox.style.display = rec ? "none" : "";
        modeRecurring.className = "cf-btn cf-btn-sm" + (rec ? " cf-btn-primary" : "");
        modeOneOff.className = "cf-btn cf-btn-sm" + (rec ? "" : " cf-btn-primary");
      }
      modeRecurring.addEventListener("click", function () { setMode("recurring"); });
      modeOneOff.addEventListener("click", function () { setMode("oneoff"); });

      var save = el("button", { class: "cf-btn cf-btn-primary", text: "Schedule" });
      m.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px" }, [
        el("button", { class: "cf-btn", text: "Cancel", onclick: close }), save,
      ]));

      save.addEventListener("click", async function () {
        if (!startTime.value) { UI.toast("Pick a start time.", "warn"); return; }
        var body = { start_time: startTime.value, duration_minutes: num(dur.value) || undefined };
        if (cap.value) body.capacity = num(cap.value);
        var chosenCourts = courtToggles.filter(function (t) { return t.cb.checked; }).map(function (t) { return t.id; });
        if (chosenCourts.length) body.court_resource_ids = chosenCourts;
        if (mode === "recurring") {
          var days = dayToggles.filter(function (d) { return d.cb.checked; }).map(function (d) { return d.wd; });
          if (!days.length) { UI.toast("Pick at least one weekday.", "warn"); return; }
          if (!dateFrom.value || !dateUntil.value) { UI.toast("Pick a date range.", "warn"); return; }
          body.weekdays = days;
          body.date_from = dateFrom.value;
          body.date_until = dateUntil.value;
        } else {
          var dates = Array.prototype.slice.call(oneOffRows.children)
            .map(function (r) { return r._date.value; }).filter(Boolean);
          if (!dates.length) { UI.toast("Add at least one date.", "warn"); return; }
          body.dates = dates;
        }
        save.disabled = true; save.textContent = "Scheduling…";
        try {
          var res = await api.scheduleClass(cls.resource_id, body);
          var created = (res && res.created != null) ? res.created : 0;
          var skipped = (res && res.skipped != null) ? res.skipped : 0;
          var busy = (res && res.court_busy != null) ? res.court_busy : 0;
          var coachBusy = (res && res.coach_busy != null) ? res.coach_busy : 0;
          UI.toast("Scheduled " + created + " session" + (created === 1 ? "" : "s") +
            (skipped ? " (" + skipped + " already there)" : "") +
            (busy ? " — " + busy + " skipped (no court free)" : "") +
            (coachBusy ? " — " + coachBusy + " skipped (coach busy)" : "") + ".",
            (busy || coachBusy) ? "warn" : "info");
          close();
          if (typeof opts.onSaved === "function") opts.onSaved(res || {});
        } catch (e) {
          save.disabled = false; save.textContent = "Schedule";
          UI.toast(UI.errMsg(e), "error");
        }
      });

      setMode("recurring");
    });
    return mc;
  }

  // ---------------------------------------------------------------------------
  // SESSIONS TABLE — date/time · enrolled/capacity · waitlist · status · cancel.
  //   Loads api.classSessions(resourceId, {date_from,date_to}); each row opens the
  //   roster modal and can cancel a session. Renders into `host`.
  // opts: {api, cls, host, dateFrom?, dateTo?}.
  // ---------------------------------------------------------------------------
  function renderSessions(opts) {
    init(); opts = opts || {};
    var api = opts.api, cls = opts.cls, host = opts.host;
    var from = opts.dateFrom || UI.dateKey(UI.addDays(new Date(), -1));
    var to = opts.dateTo || UI.dateKey(UI.addDays(new Date(), 60));

    UI.clear(host);
    host.appendChild(el("div", { class: "cf-loading", text: "Loading sessions…" }));

    api.classSessions(cls.resource_id, { date_from: from, date_to: to }).then(function (r) {
      var sessions = (r && r.sessions) || [];
      UI.clear(host);
      if (!sessions.length) {
        host.appendChild(el("div", { class: "cf-empty", text: "No sessions scheduled yet. Use “Schedule sessions” to add some." }));
        return;
      }
      var t = el("table", { class: "cf-table" });
      t.appendChild(el("thead", {}, [el("tr", {}, ["When", "Enrolled", "Waitlist", "Status", ""].map(function (h) {
        return el("th", { text: h });
      }))]));
      var tb = el("tbody");
      sessions.forEach(function (s) {
        var cap = (s.capacity != null) ? s.capacity : (cls.capacity != null ? cls.capacity : "—");
        var enrolledTxt = (s.enrolled != null ? s.enrolled : 0) + " / " + cap;
        var cancelled = s.status === "cancelled";
        var rosterBtn = el("button", { class: "cf-btn cf-btn-sm", text: "Roster",
          onclick: function () { openRoster({ api: api, cls: cls, session: s }); } });
        var cancelBtn = el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Cancel",
          onclick: function () { cancelSession(api, s, function () { renderSessions(opts); }); } });
        var actions = el("div", { class: "cf-row", style: "gap:6px;justify-content:flex-end" },
          cancelled ? [rosterBtn] : [rosterBtn, cancelBtn]);
        tb.appendChild(el("tr", {}, [
          el("td", { text: UI.fmtRange(s.starts_at, s.ends_at) }),
          el("td", { text: enrolledTxt }),
          el("td", { text: String(s.waitlisted != null ? s.waitlisted : 0) }),
          el("td", {}, [el("span", { class: "cf-chip " + (s.status || ""), text: s.status || "—" })]),
          el("td", {}, [actions]),
        ]));
      });
      t.appendChild(tb);
      host.appendChild(t);
    }).catch(function (e) {
      UI.clear(host);
      host.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) }));
    });
  }

  async function cancelSession(api, session, after) {
    init();
    if (!window.confirm("Cancel this session? Enrolled players will be released.")) return;
    try {
      await api.cancelClassSession(session.session_id, { reason: "admin_cancel" });
      UI.toast("Session cancelled.", "info");
      if (typeof after === "function") after();
    } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  // ---------------------------------------------------------------------------
  // ROSTER MODAL — enrolled + waitlisted lists with attendance toggles.
  //   Loads api.classRoster(session_id); attendance via api.classAttendance(
  //   session_id, {user_id, attended}). opts: {api, cls, session}.
  // ---------------------------------------------------------------------------
  function openRoster(opts) {
    init(); opts = opts || {};
    var api = opts.api, cls = opts.cls, session = opts.session;
    modal((cls.name || "Class") + " roster", function (m, close) {
      m.appendChild(el("p", { class: "cf-muted", text: UI.fmtRange(session.starts_at, session.ends_at) }));
      var body = el("div", { id: "cf-roster-body" }, [el("div", { class: "cf-loading", text: "Loading roster…" })]);
      m.appendChild(body);
      m.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px" }, [
        el("button", { class: "cf-btn", text: "Close", onclick: close }),
      ]));

      api.classRoster(session.session_id).then(function (r) {
        renderRoster(body, api, session, (r && r.enrolled) || [], (r && r.waitlisted) || []);
      }).catch(function (e) {
        UI.clear(body); body.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) }));
      });
    });
  }

  function personLine(p) {
    var name = p.name || p.email || "Player";
    return el("div", { class: "cf-item-main" }, [
      el("div", { class: "cf-item-t", text: name }),
      el("div", { class: "cf-item-s", text: p.email || "" }),
    ]);
  }

  function renderRoster(body, api, session, enrolled, waitlisted) {
    UI.clear(body);
    if (!enrolled.length && !waitlisted.length) {
      body.appendChild(el("div", { class: "cf-empty", text: "No-one enrolled yet." }));
      return;
    }
    if (enrolled.length) {
      body.appendChild(el("h3", { text: "Enrolled (" + enrolled.length + ")", style: "margin-top:6px" }));
      var list = el("div", { class: "cf-list" });
      enrolled.forEach(function (p) {
        var attended = p.status === "attended" || p.attended === true;
        var btn = el("button", { class: "cf-btn cf-btn-sm" + (attended ? " cf-btn-primary" : ""),
          text: attended ? "✓ Attended" : "Mark attended" });
        btn.addEventListener("click", async function () {
          var next = !(btn.classList.contains("cf-btn-primary"));
          btn.disabled = true;
          try {
            await api.classAttendance(session.session_id, { user_id: p.user_id, attended: next });
            btn.classList.toggle("cf-btn-primary", next);
            btn.textContent = next ? "✓ Attended" : "Mark attended";
            UI.toast("Attendance saved.", "info");
          } catch (e) { UI.toast(UI.errMsg(e), "error"); }
          finally { btn.disabled = false; }
        });
        list.appendChild(el("div", { class: "cf-item" }, [
          el("span", { class: "cf-chip class", text: "enrolled" }), personLine(p), btn,
        ]));
      });
      body.appendChild(list);
    }
    if (waitlisted.length) {
      body.appendChild(el("h3", { text: "Waitlisted (" + waitlisted.length + ")", style: "margin-top:14px" }));
      var wl = el("div", { class: "cf-list" });
      waitlisted.forEach(function (p) {
        wl.appendChild(el("div", { class: "cf-item" }, [
          el("span", { class: "cf-chip waitlisted", text: "waitlist" }), personLine(p),
        ]));
      });
      body.appendChild(wl);
    }
  }

  // ---------------------------------------------------------------------------
  // CLASS LIST — a cf-table of class types with per-row actions (Schedule / Sessions).
  //   Pure render; caller supplies the classes + the action handlers. Renders into host.
  // opts: {host, classes:[...], onSchedule(cls), onSessions(cls), currency?}.
  // ---------------------------------------------------------------------------
  function renderClassList(opts) {
    init(); opts = opts || {};
    var host = opts.host, classes = opts.classes || [];
    UI.clear(host);
    if (!classes.length) {
      host.appendChild(el("div", { class: "cf-empty", text: "No classes yet. Create your first class above." }));
      return;
    }
    var t = el("table", { class: "cf-table" });
    t.appendChild(el("thead", {}, [el("tr", {}, ["Class", "Coach", "Capacity", "Price", "Length", "Upcoming", ""].map(function (h) {
      return el("th", { text: h });
    }))]));
    var tb = el("tbody");
    classes.forEach(function (c) {
      var price = (c.price_amount_minor != null) ? UI.money(c.price_amount_minor, opts.currency) : "—";
      var editBtn = opts.onEdit ? el("button", { class: "cf-btn cf-btn-sm", text: "Edit",
        onclick: function () { opts.onEdit(c); } }) : null;
      var schedBtn = el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Schedule",
        onclick: function () { opts.onSchedule && opts.onSchedule(c); } });
      var sessBtn = el("button", { class: "cf-btn cf-btn-sm", text: "Sessions",
        onclick: function () { opts.onSessions && opts.onSessions(c); } });
      tb.appendChild(el("tr", {}, [
        el("td", {}, [el("div", { class: "cf-item-t", text: c.name || "Class" }),
          c.description ? el("div", { class: "cf-item-s", text: c.description }) : null]),
        el("td", {}, [c.coach_name ? el("span", { text: c.coach_name })
          : el("span", { class: "cf-chip held", text: "No coach — Edit" })]),
        el("td", { text: c.capacity != null ? String(c.capacity) : "—" }),
        el("td", { text: price }),
        el("td", { text: (c.duration_minutes != null ? c.duration_minutes : "—") + " min" }),
        el("td", { text: String(c.upcoming_sessions != null ? c.upcoming_sessions : 0) }),
        el("td", {}, [el("div", { class: "cf-row", style: "gap:6px;justify-content:flex-end" }, [editBtn, schedBtn, sessBtn].filter(Boolean))]),
      ]));
    });
    t.appendChild(tb);
    host.appendChild(t);
  }

  window.ClassUI = {
    openClassForm: openClassForm,
    openScheduleForm: openScheduleForm,
    renderSessions: renderSessions,
    openRoster: openRoster,
    renderClassList: renderClassList,
  };
})();

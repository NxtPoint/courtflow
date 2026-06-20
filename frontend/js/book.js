// book.js — member booking wizard (docs/03 §9, docs/05 §5).
// Flow (3 steps, modelled on Wix "Schedule your service"):
//   1. Choose a service  — Book a court / Book a lesson / Attend a class (cf-tile cards).
//   2. Schedule          — 3-column: month calendar | time blocks | preferences (coach +
//                          court dropdowns, service-details summary, Next). For a class the
//                          middle column lists that day's class sessions instead.
//   3. Pay & confirm     — settlement blocks (Pay online / Pay at the court / Pay later) +
//                          tight summary + slick animated success.
//
// Calls GET /api/diary/availability + GET /api/diary/resources + GET /api/diary/classes,
// then POST /api/diary/bookings (court/lesson) or POST /api/diary/classes/:id/enrol (class).
//
// Lessons reserve a court: on submit we send coach_user_id + resource_id (the coach slot)
// AND court_resource_id (the available/chosen court) so the backend auto-holds the court.
(function () {
  var UI, el;
  var state = {
    principal: null,
    billing: { online_enabled: false, currency: "ZAR", provider: "manual" },
    policy: null,            // allowed settlement modes (best-effort; falls back to defaults)
    type: null,              // court | lesson | class
    resources: [],
    coaches: [],
    courts: [],
    selCoach: "ANY",         // lesson: chosen coach resource or "ANY"
    selCourt: "ANY",         // court/lesson: chosen court resource or "ANY"
    selClass: null,          // chosen class session
    durations: [],           // [{duration_minutes, amount_minor, price_id}] for the chosen service
    selDuration: null,       // chosen duration in minutes (court/lesson)
    selDurationPrice: null,  // amount_minor for the chosen duration (null when covered/unpriced)
    membershipCovered: false,// court + active membership -> free (set from /durations)
    calMonth: null,          // Date pinned to the 1st of the visible calendar month
    day: null,               // Date — chosen day in the calendar
    slotsCache: {},          // cacheKey -> slots[] (per-day availability cache)
    slot: null,              // {start,end,resource_id,resource_name,kind,price,court_resource_id?}
    guest: null,             // {name,email} optional member-guest
    settlement: "at_court",
  };

  // Priced durations + membership-covered flag for the current service (Duration step).
  // Calls GET /api/diary/durations directly via TFAuth (book.js owns this small wrapper).
  function qs(params) {
    var parts = [];
    Object.keys(params || {}).forEach(function (k) {
      var v = params[k];
      if (v !== undefined && v !== null && v !== "") parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(v));
    });
    return parts.length ? ("?" + parts.join("&")) : "";
  }
  function fetchDurations(q) {
    return window.TFAuth.apiJSON("/api/diary/durations" + qs(q));
  }

  // ---- step rendering -------------------------------------------------------
  // The wizard has a Duration step for court/lesson (Service → Duration → Schedule → Confirm);
  // a class skips it (sessions have fixed times): Service → Schedule → Confirm. We model steps
  // by NAME so the indices stay correct whichever flow is active.
  function stepNames() {
    return state.type === "class"
      ? ["service", "schedule", "confirm"]
      : ["service", "duration", "schedule", "confirm"];
  }
  var STEP_LABELS = { service: "Service", duration: "Duration", schedule: "Schedule", confirm: "Confirm" };

  function steps(activeName) {
    var names = stepNames();
    var active = names.indexOf(activeName);
    var wrap = el("div", { class: "cf-steps" });
    names.forEach(function (name, i) {
      var done = i < active;
      var s = el("div", {
        class: "cf-step" + (i === active ? " on" : "") + (done ? " done" : ""),
        onclick: function () { goToStep(name); },
      }, [ el("span", { class: "n", text: done ? "✓" : String(i + 1) }),
           el("span", { text: STEP_LABELS[name] }) ]);
      wrap.appendChild(s);
    });
    return wrap;
  }
  function goToStep(name) {
    // Allow jumping back to a completed/reachable step; never skip forward past prerequisites.
    if (name === "service") return stepService();
    if (name === "duration" && state.type && state.type !== "class") return stepDuration();
    if (name === "schedule" && state.type) {
      if (state.type !== "class" && !state.selDuration) return stepDuration();
      return stepSchedule();
    }
    if (name === "confirm" &&
        ((state.slot && state.type !== "class") || (state.type === "class" && state.selClass))) {
      return stepConfirm();
    }
  }
  function host() { return document.getElementById("cf-wizard"); }
  function render(active, body) {
    var h = host(); UI.clear(h);
    h.appendChild(steps(active));
    h.appendChild(body);
  }

  // ---- Step 1: choose a service --------------------------------------------
  function stepService() {
    var tiles = el("div", { class: "cf-tiles" });
    [
      { k: "court", t: "Book a court", s: "Reserve a court — find the first one free", icon: "🎾" },
      { k: "lesson", t: "Book a lesson", s: "A session with one of our coaches", icon: "🏆" },
      { k: "class", t: "Attend a class", s: "Cardio, juniors, socials & clinics", icon: "👥" },
    ].forEach(function (o) {
      tiles.appendChild(el("div", {
        class: "cf-tile cf-tile-tap cf-svc-tile" + (state.type === o.k ? " sel" : ""),
        onclick: function () {
          var changed = state.type !== o.k;
          state.type = o.k;
          if (changed) {
            state.slot = null; state.selClass = null;
            state.selCoach = "ANY"; state.selCourt = "ANY";
            state.durations = []; state.selDuration = null; state.selDurationPrice = null;
            state.membershipCovered = false;
            state.slotsCache = {}; state.day = null; state.calMonth = null;
          }
          // Court/lesson go through Duration first (live per-duration price); class skips it.
          if (o.k === "class") stepSchedule(); else stepDuration();
        },
      }, [
        el("div", { class: "cf-tile-icon", text: o.icon }),
        el("div", {}, [
          el("div", { class: "cf-tile-t", text: o.t }),
          el("div", { class: "cf-tile-s", text: o.s }),
        ]),
      ]));
    });
    render("service", el("div", { class: "cf-card" }, [
      el("h2", { text: "What would you like to book?" }),
      el("p", { class: "cf-muted", style: "margin-top:-4px", text: "Pick a service to get started." }),
      tiles,
    ]));
  }

  // ---- Step 2: choose a duration (court/lesson) -----------------------------
  // Loads the priced durations for the service. Each tile shows its per-duration price via
  // UI.money — OR, when the caller's court bookings are membership-covered, "Covered by your
  // membership · R0". The chosen duration drives the Schedule step (slot length) + checkout.
  async function stepDuration() {
    if (state.type === "class") return stepSchedule();
    var card = el("div", { class: "cf-card" }, [
      el("h2", { text: state.type === "lesson" ? "How long a lesson?" : "How long do you need the court?" }),
      el("p", { class: "cf-muted", style: "margin-top:-4px",
        text: "Pick a duration — the price updates live." }),
      el("div", { id: "cf-durations", class: "cf-loading", text: "Loading durations…" }),
    ]);
    card.appendChild(el("div", { class: "cf-row", style: "margin-top:16px" }, [
      el("button", { class: "cf-btn cf-btn-ghost", text: "← Back", onclick: stepService }),
    ]));
    render("duration", card);

    // Fetch (court|lesson) priced durations + membership_covered for the caller.
    try {
      var q = { kind: state.type, audience: "member" };
      if (state.type === "lesson" && state.selCoach !== "ANY" && state.selCoach.coach_user_id) {
        q.coach_id = state.selCoach.coach_user_id;
      }
      var r = await fetchDurations(q);
      state.durations = r.durations || [];
      state.membershipCovered = !!r.membership_covered;
      renderDurations();
    } catch (e) {
      var b = document.getElementById("cf-durations");
      if (b) { b.className = ""; b.textContent = UI.errMsg(e); }
    }
  }

  function renderDurations() {
    var box = document.getElementById("cf-durations"); if (!box) return;
    box.className = ""; UI.clear(box);
    if (!state.durations.length) {
      box.appendChild(el("div", { class: "cf-empty",
        text: "No durations are priced for this service yet. Please contact the club." }));
      return;
    }
    var tiles = el("div", { class: "cf-tiles" });
    state.durations.forEach(function (d) {
      var on = state.selDuration === d.duration_minutes;
      var priceText = state.membershipCovered
        ? "Covered by your membership · R0"
        : UI.money(d.amount_minor, state.billing.currency);
      tiles.appendChild(el("div", {
        class: "cf-tile cf-tile-tap" + (on ? " sel" : ""),
        onclick: function () {
          state.selDuration = d.duration_minutes;
          state.selDurationPrice = state.membershipCovered ? 0 : d.amount_minor;
          state.slot = null; state.slotsCache = {};   // duration changed -> re-fetch slots
          stepSchedule();
        },
      }, [
        el("div", { class: "cf-tile-t", text: d.duration_minutes + " min" }),
        el("div", { class: "cf-tile-s", text: priceText }),
      ]));
    });
    box.appendChild(tiles);
  }

  // ---- Step 3: Schedule your service (3-column) -----------------------------
  async function stepSchedule() {
    // Load resources for court/lesson before laying out the panel.
    if (state.type !== "class" && !state.resources.length) {
      try {
        var rr = await window.API.resources();
        state.resources = rr.resources || [];
        state.courts = state.resources.filter(function (x) { return x.kind === "court"; });
        state.coaches = state.resources.filter(function (x) { return x.kind === "coach"; });
      } catch (e) { /* fall through — calendar still renders, slots will surface the error */ }
    }
    state.day = state.day || new Date();
    state.calMonth = state.calMonth || firstOfMonth(state.day);

    var grid = el("div", { class: "cf-sched" });
    grid.appendChild(calColumn());
    grid.appendChild(midColumn());
    grid.appendChild(prefColumn());

    var card = el("div", { class: "cf-card cf-sched-card" }, [ grid ]);
    card.appendChild(el("div", { class: "cf-row", style: "margin-top:16px" }, [
      // Back to Duration for court/lesson; back to Service for a class (no duration step).
      el("button", { class: "cf-btn cf-btn-ghost", text: "← Back",
        onclick: state.type === "class" ? stepService : stepDuration }),
    ]));
    render("schedule", card);

    loadSlots(); // kick off availability for the selected day
  }

  // -- left column: month calendar -------------------------------------------
  function firstOfMonth(d) { var x = new Date(d); x.setDate(1); x.setHours(0, 0, 0, 0); return x; }
  function sameDay(a, b) { return a && b && UI.dateKey(a) === UI.dateKey(b); }

  function inWindow(d) {
    // Selectable if today..today+window_days (best-effort window; backend re-validates).
    var t0 = new Date(); t0.setHours(0, 0, 0, 0);
    var winDays = (state.policy && state.policy.booking_window_days) || 14;
    var max = UI.addDays(t0, winDays);
    var dd = new Date(d); dd.setHours(0, 0, 0, 0);
    return dd >= t0 && dd <= max;
  }

  function calColumn() {
    var col = el("div", { class: "cf-sched-col cf-cal-col" });
    col.appendChild(el("h2", { class: "cf-sched-h", text: "Select a Date and Time" }));
    col.appendChild(el("p", { class: "cf-cal-tz", text: "Times shown in " + UI.CLUB_TZ.replace(/_/g, " ") }));

    var head = el("div", { class: "cf-cal-nav" }, [
      el("button", { class: "cf-cal-navbtn", text: "‹", "aria-label": "Previous month",
        onclick: function () { state.calMonth = addMonths(state.calMonth, -1); stepSchedule(); } }),
      el("div", { class: "cf-cal-title",
        text: state.calMonth.toLocaleDateString("en-ZA", { month: "long", year: "numeric", timeZone: UI.CLUB_TZ }) }),
      el("button", { class: "cf-cal-navbtn", text: "›", "aria-label": "Next month",
        onclick: function () { state.calMonth = addMonths(state.calMonth, 1); stepSchedule(); } }),
    ]);
    col.appendChild(head);

    var dow = el("div", { class: "cf-cal-dow" });
    ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].forEach(function (w) {
      dow.appendChild(el("span", { text: w }));
    });
    col.appendChild(dow);

    var gridEl = el("div", { class: "cf-cal-grid" });
    var first = firstOfMonth(state.calMonth);
    var lead = first.getDay(); // 0=Sun
    for (var i = 0; i < lead; i++) gridEl.appendChild(el("span", { class: "cf-cal-pad" }));
    var dim = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
    for (var dnum = 1; dnum <= dim; dnum++) {
      (function (dnum) {
        var d = new Date(first.getFullYear(), first.getMonth(), dnum);
        var ok = inWindow(d);
        var on = sameDay(d, state.day);
        var cls = "cf-cal-day" + (ok ? "" : " off") + (on ? " sel" : "");
        var cell = el("button", {
          class: cls, type: "button", disabled: ok ? null : "disabled",
          onclick: ok ? function () { state.day = d; stepSchedule(); } : null,
        }, [
          el("span", { class: "cf-cal-dnum", text: String(dnum) }),
          ok ? el("span", { class: "cf-cal-dot" }) : null,
        ]);
        gridEl.appendChild(cell);
      })(dnum);
    }
    col.appendChild(gridEl);
    return col;
  }
  function addMonths(d, n) { var x = new Date(d); x.setMonth(x.getMonth() + n); return x; }

  // -- middle column: time blocks (or class list) ----------------------------
  function midColumn() {
    var col = el("div", { class: "cf-sched-col cf-mid-col" });
    var dl = state.day.toLocaleDateString("en-ZA",
      { weekday: "long", month: "long", day: "numeric", timeZone: UI.CLUB_TZ });
    var head = state.type === "class" ? "Classes for " + dl : "Availability for " + dl;
    col.appendChild(el("h3", { class: "cf-mid-h", text: head }));
    col.appendChild(el("div", { id: "cf-slots", class: "cf-loading", text: "Finding times…" }));
    return col;
  }

  // -- right column: preferences + service details + Next ---------------------
  function prefColumn() {
    var col = el("div", { class: "cf-sched-col cf-pref-col" });
    col.appendChild(el("h3", { class: "cf-pref-h", text: "Preferences" }));

    if (state.type === "lesson") {
      var coachSel = el("select", { class: "cf-select", onchange: function (ev) {
        state.selCoach = ev.target.value === "ANY" ? "ANY"
          : state.coaches.filter(function (c) { return c.id === ev.target.value; })[0] || "ANY";
        state.slot = null; state.slotsCache = {}; stepSchedule();
      } });
      coachSel.appendChild(el("option", { value: "ANY", text: "Any coach",
        selected: state.selCoach === "ANY" ? "selected" : null }));
      state.coaches.forEach(function (c) {
        coachSel.appendChild(el("option", { value: c.id, text: c.name,
          selected: (state.selCoach && state.selCoach.id === c.id) ? "selected" : null }));
      });
      col.appendChild(el("div", { class: "cf-field" }, [ el("label", { text: "Coach" }), coachSel ]));
    }

    if (state.type !== "class") {
      var courtSel = el("select", { class: "cf-select", onchange: function (ev) {
        state.selCourt = ev.target.value === "ANY" ? "ANY"
          : state.courts.filter(function (c) { return c.id === ev.target.value; })[0] || "ANY";
        state.slot = null; state.slotsCache = {}; stepSchedule();
      } });
      courtSel.appendChild(el("option", { value: "ANY", text: "Any available court",
        selected: state.selCourt === "ANY" ? "selected" : null }));
      state.courts.forEach(function (c) {
        courtSel.appendChild(el("option", { value: c.id, text: c.name,
          selected: (state.selCourt && state.selCourt.id === c.id) ? "selected" : null }));
      });
      col.appendChild(el("div", { class: "cf-field" }, [
        el("label", { text: "Court" }), courtSel,
        state.type === "lesson"
          ? el("div", { class: "cf-pref-note", text: "We'll reserve this court for your lesson." }) : null,
      ]));
    }

    col.appendChild(el("h3", { class: "cf-pref-h", style: "margin-top:18px", text: "Service Details" }));
    col.appendChild(serviceDetails());

    var next = el("button", {
      class: "cf-btn cf-btn-primary cf-btn-block cf-btn-lg", style: "margin-top:14px",
      text: "Next", disabled: readyForNext() ? null : "disabled",
      onclick: function () { if (readyForNext()) stepConfirm(); },
    });
    col.appendChild(next);
    return col;
  }

  function readyForNext() {
    return state.type === "class" ? !!state.selClass : !!state.slot;
  }

  function serviceDetails() {
    var box = el("div", { class: "cf-svc-details" });
    function row(k, v) {
      box.appendChild(el("div", { class: "cf-svc-row" }, [
        el("span", { class: "cf-svc-k", text: k }),
        el("span", { class: "cf-svc-v", text: v == null ? "—" : String(v) }),
      ]));
    }
    if (state.type === "class") {
      row("Service", "Class");
      if (state.selClass) {
        row("Class", state.selClass.class_name || "Class");
        row("When", UI.fmtRange(state.selClass.starts_at, state.selClass.ends_at));
        var cp = state.selClass.price_minor != null ? state.selClass.price_minor : state.selClass.price;
        if (cp != null) row("Price", UI.money(cp, state.billing.currency));
      } else {
        row("When", "Pick a class →");
      }
      return box;
    }
    row("Service", state.type === "lesson" ? "Lesson" : "Court booking");
    if (state.type === "lesson") {
      row("Coach", state.selCoach === "ANY"
        ? (state.slot && state.slot.resource_name || "Any coach")
        : (state.selCoach && state.selCoach.name));
    }
    row("Court", courtSummaryLabel());
    if (state.selDuration) row("Duration", state.selDuration + " min");
    if (state.slot) {
      row("When", UI.fmtRange(state.slot.start, state.slot.end));
      row("Price", priceLabel());
    } else {
      row("When", "Pick a time →");
    }
    return box;
  }

  // Price label for the summary: membership-covered courts read "Covered…", else the chosen
  // duration's price (slot price when a slot is picked, else the duration tile price).
  function priceLabel() {
    if (state.membershipCovered && state.type === "court") return "Covered by your membership · R0";
    var minor = (state.slot && state.slot.price != null) ? state.slot.price : state.selDurationPrice;
    return minor != null ? UI.money(minor, state.billing.currency) : "—";
  }

  // The court that will actually be reserved (resolved from the slot for "Any").
  function courtSummaryLabel() {
    if (state.selCourt !== "ANY") return (state.selCourt && state.selCourt.name) || "—";
    if (state.type === "lesson") {
      return (state.slot && (state.slot.court_resource_name || "Any available court")) || "Any available court";
    }
    // court booking: the slot resolves to a specific free court
    return (state.slot && state.slot.resource_name) || "Any available court";
  }

  // ---- availability load + time blocks --------------------------------------
  function slotCacheKey() {
    var parts = [state.type, UI.dateKey(state.day), "d" + (state.selDuration || "")];
    if (state.type === "lesson") parts.push(state.selCoach === "ANY" ? "anycoach" : state.selCoach.id);
    parts.push(state.selCourt === "ANY" ? "anycourt" : state.selCourt.id);
    return parts.join("|");
  }

  async function loadSlots() {
    if (state.type === "class") { loadClasses(); return; }
    var ck = slotCacheKey();
    if (state.slotsCache[ck]) { renderSlots(state.slotsCache[ck]); return; }
    var dk = UI.dateKey(state.day);
    try {
      // Slots are the chosen duration's length; the server prices them per-duration (0 when
      // membership-covered), so the price shown on each block matches the duration step.
      var q = { date_from: dk, date_to: dk, audience: "member" };
      if (state.selDuration) q.duration = state.selDuration;
      if (state.type === "lesson") {
        q.kind = "coach";
        if (state.selCoach !== "ANY" && state.selCoach.id) q.coach_id = state.selCoach.id;
        else q.any = "1";
      } else {
        q.kind = "court";
        if (state.selCourt === "ANY") q.any = "1";
        else if (state.selCourt && state.selCourt.id) q.resource_id = state.selCourt.id;
      }
      var r = await window.API.availability(q);
      state.slotsCache[ck] = r.slots || [];
      if (slotCacheKey() === ck) renderSlots(state.slotsCache[ck]);
    } catch (e) {
      var b = document.getElementById("cf-slots");
      if (b) { b.className = ""; b.textContent = UI.errMsg(e); }
    }
  }

  function renderSlots(slots) {
    var box = document.getElementById("cf-slots"); if (!box) return;
    box.className = ""; UI.clear(box);
    if (!slots.length) {
      box.appendChild(el("div", { class: "cf-empty", text: "No free times on this day. Try another day"
        + (state.type === "court" ? ", or 'Any available court'." : ".") }));
      return;
    }
    var grid = el("div", { class: "cf-timeblocks" });
    slots.forEach(function (sl) {
      var on = state.slot && state.slot.start === sl.start && state.slot.resource_id === sl.resource_id;
      var kids = [ el("span", { class: "cf-tb-time", text: UI.fmtTime(sl.start) }) ];
      if (sl.price != null) kids.push(el("span", { class: "cf-tb-price", text: UI.money(sl.price, state.billing.currency) }));
      grid.appendChild(el("button", {
        class: "cf-timeblock" + (on ? " sel" : ""), type: "button",
        onclick: function () { state.slot = sl; stepSchedule(); },
      }, kids));
    });
    box.appendChild(grid);
  }

  async function loadClasses() {
    var dk = UI.dateKey(state.day);
    var ck = "class|" + dk;
    if (state.slotsCache[ck]) { renderClassList(state.slotsCache[ck]); return; }
    try {
      var r = await window.API.classes({ date_from: dk, date_to: dk });
      state.slotsCache[ck] = r.classes || [];
      if (UI.dateKey(state.day) === dk) renderClassList(state.slotsCache[ck]);
    } catch (e) {
      var b = document.getElementById("cf-slots");
      if (b) { b.className = ""; b.textContent = UI.errMsg(e); }
    }
  }

  function renderClassList(classes) {
    var box = document.getElementById("cf-slots"); if (!box) return;
    box.className = ""; UI.clear(box);
    if (!classes.length) {
      box.appendChild(el("div", { class: "cf-empty", text: "No classes on this day. Try another day." }));
      return;
    }
    var list = el("div", { class: "cf-list" });
    classes.forEach(function (c) {
      var full = c.spots_left === 0;
      var on = state.selClass && state.selClass.id === c.id;
      var priceMinor = c.price_minor != null ? c.price_minor : c.price;
      var price = priceMinor != null ? UI.money(priceMinor, state.billing.currency) : null;
      var sub = UI.fmtTime(c.starts_at) + "–" + UI.fmtTime(c.ends_at);
      if (c.spots_left != null) sub += full ? " · Full" : " · " + c.spots_left + " spots left";
      if (price) sub += " · " + price;
      list.appendChild(el("div", {
        class: "cf-item cf-item-tap" + (on ? " sel" : ""),
        onclick: function () { state.selClass = c; stepSchedule(); },
      }, [
        el("span", { class: "cf-chip class", text: full ? "waitlist" : "class" }),
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: c.class_name || "Class" }),
          el("div", { class: "cf-item-s", text: sub }),
        ]),
      ]));
    });
    box.appendChild(list);
  }

  // ---- settlement helpers ---------------------------------------------------
  function allowedModes() {
    // club.policy may restrict; default at launch is at_court + monthly_account +
    // membership_covered. online is offered only if billing config enabled.
    var allow = (state.policy && state.policy.allowed_settlement_modes) ||
                ["at_court", "monthly_account", "membership_covered"];
    var modes = allow.slice();
    if (state.billing.online_enabled && modes.indexOf("online") < 0) modes.push("online");
    return modes.filter(function (m) { return UI.SETTLEMENT[m]; });
  }

  // ---- Step 4: pay & confirm ------------------------------------------------
  function stepConfirm() {
    captureGuest(); // preserve typed guest details across settlement re-renders
    // Membership-covered court booking -> free: settlement is fixed to membership_covered.
    var modes = state.membershipCovered && state.type === "court"
      ? ["membership_covered"] : allowedModes();
    if (modes.indexOf(state.settlement) < 0) state.settlement = modes[0] || "at_court"; // smart default

    var card = el("div", { class: "cf-card" });
    card.appendChild(el("h2", { text: "Pay & confirm" }));

    // --- summary block ---
    card.appendChild(summaryCard());

    // --- member-guest (court/lesson only) ---
    if (state.type !== "class") {
      var gName = el("input", { class: "cf-input", placeholder: "Guest name", value: (state.guest && state.guest.name) || "" });
      var gEmail = el("input", { class: "cf-input", type: "email", placeholder: "Guest email (optional)", value: (state.guest && state.guest.email) || "" });
      state._gName = gName; state._gEmail = gEmail;
      var detail = el("div", { class: "cf-confirm-sec" }, [
        el("h3", { text: "Playing with a guest?" }),
        el("p", { class: "cf-muted cf-tiny", text: "Optional — leave blank for a solo booking." }),
        el("div", { class: "cf-grid cf-grid-2" }, [
          el("div", { class: "cf-field" }, [ el("label", { text: "Guest name" }), gName ]),
          el("div", { class: "cf-field" }, [ el("label", { text: "Guest email" }), gEmail ]),
        ]),
      ]);
      card.appendChild(detail);
    } else {
      state._gName = null; state._gEmail = null;
    }

    // --- settlement (selectable blocks, smart default pre-selected) ---
    card.appendChild(el("div", { class: "cf-confirm-sec" }, [
      el("h3", { text: "How would you like to pay?" }),
      settlementBlocks(modes),
    ]));

    if (state.settlement === "online" && state.billing.online_enabled) {
      card.appendChild(el("p", { class: "cf-muted cf-tiny", style: "margin-top:6px",
        text: "Online payment (" + state.billing.provider + ") opens at confirmation. The booking is held until paid." }));
    }

    // --- prominent confirm CTA ---
    var btn = el("button", { class: "cf-btn cf-btn-primary cf-btn-block cf-btn-lg", text: confirmLabel() });
    btn.addEventListener("click", function () { submit(btn); });
    card.appendChild(el("div", { style: "margin-top:16px" }, [ btn ]));
    card.appendChild(el("button", { class: "cf-btn cf-btn-ghost cf-btn-block", style: "margin-top:8px",
      text: "← Back", onclick: stepSchedule }));

    render("confirm", card);
  }

  function confirmLabel() {
    if (state.type === "class") {
      return (state.selClass && state.selClass.spots_left === 0) ? "Join waitlist" : "Confirm & enrol";
    }
    if (state.settlement === "online" && state.billing.online_enabled) return "Confirm & pay";
    return "Confirm booking";
  }

  function summaryCard() {
    var rows = [];
    if (state.type === "class") {
      rows.push(["What", (state.selClass.class_name || "Class")]);
      rows.push(["When", UI.fmtRange(state.selClass.starts_at, state.selClass.ends_at)]);
      var cp = state.selClass.price_minor != null ? state.selClass.price_minor : state.selClass.price;
      if (cp != null) rows.push(["Price", UI.money(cp, state.billing.currency)]);
    } else {
      if (state.type === "lesson") {
        rows.push(["Coach", state.selCoach === "ANY"
          ? (state.slot && state.slot.resource_name || "Any coach")
          : (state.selCoach && state.selCoach.name)]);
        rows.push(["Court", courtSummaryLabel()]);
      } else {
        rows.push(["Court", courtSummaryLabel()]);
      }
      if (state.selDuration) rows.push(["Duration", state.selDuration + " min"]);
      if (state.slot) rows.push(["When", UI.fmtRange(state.slot.start, state.slot.end)]);
      rows.push(["Price", priceLabel()]);
    }
    var box = el("div", { class: "cf-summary" });
    rows.forEach(function (r) {
      box.appendChild(el("div", { class: "cf-summary-row" }, [
        el("span", { class: "cf-summary-k", text: r[0] }),
        el("span", { class: "cf-summary-v", text: r[1] == null ? "—" : String(r[1]) }),
      ]));
    });
    return box;
  }

  function settlementBlocks(modes) {
    var wrap = el("div", { class: "cf-settlechips" });
    modes.forEach(function (m) {
      var meta = UI.SETTLEMENT[m];
      wrap.appendChild(el("button", {
        class: "cf-settlechip" + (state.settlement === m ? " sel" : ""),
        onclick: function () { state.settlement = m; stepConfirm(); },
      }, [
        el("span", { class: "cf-settlechip-t", text: meta.label }),
        el("span", { class: "cf-settlechip-s", text: meta.hint }),
      ]));
    });
    return wrap;
  }

  // ---- submit (data calls preserved EXACTLY — payments lane builds on this) --
  async function submit(btn) {
    btn.disabled = true; btn.textContent = "Booking…";
    try {
      var res;
      if (state.type === "class") {
        res = await window.API.enrol(state.selClass.id, { settlement_mode: state.settlement, audience: "member" });
        success("class", res);
      } else {
        var parties = [];
        if (state.guest) {
          // Member-guest: the booking member is the host (required when
          // policy.guest_requires_member — diary/bookings.py GUEST_REQUIRES_HOST).
          parties.push({ party_role: "host", user_id: state.principal.user_id });
          parties.push({ party_role: "guest", guest_name: state.guest.name, guest_email: state.guest.email || null });
        }
        var body = {
          booking_type: state.type === "lesson" ? "lesson" : "court",
          starts_at: state.slot.start, ends_at: state.slot.end,
          settlement_mode: state.settlement, parties: parties, audience: "member",
        };
        if (state.type === "lesson") {
          body.coach_user_id = (state.selCoach !== "ANY" && state.selCoach.coach_user_id) || null;
          body.resource_id = state.slot.resource_id; // the coach resource slot
          // Lessons reserve a court: pass the chosen court, or the slot's available one ("Any").
          body.court_resource_id = (state.selCourt !== "ANY" && state.selCourt.id)
            || state.slot.court_resource_id || null;
        } else {
          body.resource_id = state.slot.resource_id; // the actual court resolved by availability
        }
        res = await window.API.createBooking(body);
        // The booking API returns {booking:{...order_id,status}, checkout}. For an online
        // booking the order is 'awaiting_payment' + the booking is 'held' → kick off the Yoco
        // hosted checkout (redirects to Yoco; the webhook confirms the booking server-side;
        // /pay-return.html shows the outcome). pay.js is loaded by book.html.
        var orderId = res.order_id || (res.booking && res.booking.order_id);
        if (state.settlement === "online" && orderId) {
          if (window.Pay) { await window.Pay.startYocoCheckout(orderId); return; }
          UI.toast("Couldn't open the payment page — please refresh and try again.", "error");
          return;
        }
        // Fallback: an inline checkout intent on the response (older path).
        if (res.checkout && res.checkout.redirect_url) {
          location.href = res.checkout.redirect_url; return;
        }
        success(state.type, res);
      }
    } catch (e) {
      btn.disabled = false; btn.textContent = confirmLabel();
      // Surface a just-taken slot gracefully and bounce back to the schedule step.
      var code = (e && e.body && e.body.error) || "";
      if (e && (e.status === 409 || code === "SLOT_TAKEN")) {
        UI.toast("That slot was just taken — pick another.", "error");
        if (state.type !== "class") {
          state.slot = null;
          state.slotsCache = {}; // force a fresh fetch
          stepSchedule();
        }
        return;
      }
      UI.toast(UI.errMsg(e), "error");
    }
  }

  // ---- slick success state --------------------------------------------------
  function success(kind, res) {
    var h = host(); UI.clear(h);
    // class enrol returns status at the top level; court/lesson nest it under booking.
    var st = res.status || (res.booking && res.booking.status);
    var title, msg;
    if (kind === "class") {
      if (st === "waitlisted") { title = "You're on the waitlist"; msg = "We'll email you the moment a spot opens."; }
      else { title = "You're enrolled!"; msg = "A confirmation email is on its way."; }
    } else if (st === "held") {
      title = "Booking held"; msg = "We're holding your slot until payment completes.";
    } else {
      title = "You're booked!"; msg = "A confirmation email is on its way.";
    }

    var detail = el("div", { class: "cf-summary cf-success-detail" });
    summaryRowsForSuccess().forEach(function (r) {
      detail.appendChild(el("div", { class: "cf-summary-row" }, [
        el("span", { class: "cf-summary-k", text: r[0] }),
        el("span", { class: "cf-summary-v", text: r[1] == null ? "—" : String(r[1]) }),
      ]));
    });

    h.appendChild(el("div", { class: "cf-card cf-success" }, [
      el("div", { class: "cf-success-tick", text: "✓" }),
      el("h2", { class: "cf-success-h", text: title }),
      el("p", { class: "cf-muted", text: msg }),
      detail,
      el("p", { class: "cf-muted cf-tiny", style: "margin-top:10px", text: "Confirmations are sent via email (Klaviyo)." }),
      el("div", { class: "cf-row cf-success-actions" }, [
        el("a", { class: "cf-btn cf-btn-primary cf-btn-lg", href: "/my.html", text: "View my bookings" }),
        el("button", { class: "cf-btn cf-btn-ghost cf-btn-lg", text: "Book another", onclick: function () {
          state.type = null; state.slot = null; state.selClass = null;
          state.selCoach = "ANY"; state.selCourt = "ANY";
          state.durations = []; state.selDuration = null; state.selDurationPrice = null;
          state.membershipCovered = false;
          state.guest = null; state.day = null; state.calMonth = null; state.slotsCache = {};
          stepService();
        } }),
      ]),
    ]));
  }

  function summaryRowsForSuccess() {
    var rows = [];
    if (state.type === "class") {
      rows.push(["Class", state.selClass.class_name || "Class"]);
      rows.push(["When", UI.fmtRange(state.selClass.starts_at, state.selClass.ends_at)]);
    } else {
      if (state.type === "lesson") {
        rows.push(["Coach", state.selCoach === "ANY"
          ? (state.slot && state.slot.resource_name || "Any coach")
          : (state.selCoach && state.selCoach.name)]);
        rows.push(["Court", courtSummaryLabel()]);
      } else {
        rows.push(["Court", courtSummaryLabel()]);
      }
      if (state.slot) rows.push(["When", UI.fmtRange(state.slot.start, state.slot.end)]);
      if (state.guest) rows.push(["Guest", state.guest.name]);
    }
    rows.push(["Settlement", UI.settlementLabel(state.settlement)]);
    return rows;
  }

  // ---- small shared bits ----------------------------------------------------
  // Capture guest inputs before re-rendering confirm (settlement block tap) or leaving.
  function captureGuest() {
    if (state.type !== "class" && state._gName) {
      var n = state._gName.value.trim(), em = state._gEmail.value.trim();
      state.guest = n ? { name: n, email: em } : null;
    }
  }

  // ---- boot -----------------------------------------------------------------
  window.BookWizard = {
    start: async function (principal) {
      UI = window.UI; el = UI.el;
      state.principal = principal;
      try { state.billing = await window.API.billingConfig(principal.club_id); } catch (e) {}
      // policy: pulled from principal/club if exposed; otherwise defaults apply.
      state.policy = principal.policy || null;
      stepService();
    },
  };
})();

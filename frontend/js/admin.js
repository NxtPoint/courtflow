// admin.js — club-admin master diary + console (docs/08 §1, docs/03 §9).
// Master diary: all resources on one day timeline (GET /api/diary/master), click-to-create,
// cancel, reschedule (PATCH), block time, walk-in booking, take pay-at-court
// (POST /api/billing/desk-payment). Plus console section stubs (Resources/People/Pricing/
// Billing/Cockpit) wired to the live reads that exist; the rest link to where they will live.
//
// Calendar: a lightweight CSS-grid resource-timeline (one column per resource, hourly rows).
// docs/09 recommends FullCalendar resource-timeline; we keep it dependency-free + simple.
(function () {
  var UI, el, principal;
  var DAY_START = 6, DAY_END = 22;            // 06:00–22:00 club hours
  var SLOT_MIN = 30;                          // 30-min rows on the time axis
  var ROW_H = 46;                             // px per slot row (matches cf-cal-cell min-height)
  var state = { date: new Date(), resources: [], events: [], billing: { currency: "ZAR" },
    classes: [], coaches: [] };

  // admin.html loads api.js but not admin_api.js / class_ui.js (those power onboarding/
  // settings + the shared class components). Lazy-load them once so the Classes tab and
  // the master-diary class events work without touching the HTML shell.
  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      if (document.querySelector('script[src="' + src + '"]')) return resolve();
      var s = document.createElement("script");
      s.src = src; s.onload = resolve; s.onerror = function () { reject(new Error("Failed to load " + src)); };
      document.head.appendChild(s);
    });
  }
  async function ensureClassDeps() {
    if (!window.AdminAPI) await loadScript("/js/admin_api.js");
    if (!window.ClassUI) await loadScript("/js/class_ui.js");
  }

  // Minutes from the visible window start (DAY_START) for an ISO time, in the browser tz.
  function minsFromDayStart(iso) {
    var d = new Date(iso);
    return (d.getHours() - DAY_START) * 60 + d.getMinutes();
  }

  // ---- top controls + tabs --------------------------------------------------
  function shell() {
    var main = document.getElementById("cf-main"); UI.clear(main);

    var tabs = el("div", { class: "cf-nav", style: "margin-bottom:12px" });
    [["diary", "Master diary"], ["classes", "Classes"], ["resources", "Resources"], ["people", "People"],
     ["billing", "Billing"], ["cockpit", "Cockpit"], ["overview", "Overview"]].forEach(function (t) {
      tabs.appendChild(el("a", { href: "#", text: t[1], "data-tab": t[0],
        onclick: function (e) { e.preventDefault(); showTab(t[0]); } }));
    });
    main.appendChild(tabs);
    main.appendChild(el("div", { id: "admin-panel" }));
    showTab("diary");
  }

  function showTab(tab) {
    document.querySelectorAll("#cf-main .cf-nav a").forEach(function (a) {
      a.classList.toggle("active", a.getAttribute("data-tab") === tab);
    });
    var p = document.getElementById("admin-panel"); UI.clear(p);
    if (tab === "diary") return renderDiary(p);
    if (tab === "classes") return renderClasses(p);
    if (tab === "resources") return renderResources(p);
    if (tab === "people") return renderPeople(p);
    if (tab === "billing") return renderBilling(p);
    if (tab === "cockpit") return renderCockpit(p);
    if (tab === "overview") return renderOverview(p);
  }

  // Business Overview — rendered INLINE in this tab (no iframe, no navigation). We build the
  // structure overview.js expects, lazy-load ECharts + overview.js, then run its renderer here.
  var OV_CSS = ".ov-embed .ov-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:12px;margin-bottom:16px}" +
    ".ov-embed .kpi{background:#fff;border:1px solid #e7e9ee;border-radius:14px;padding:14px 16px}" +
    ".ov-embed .kpi .lbl{color:#6b7280;font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.03em}" +
    ".ov-embed .kpi .val{font-size:1.5rem;font-weight:800;margin-top:6px}" +
    ".ov-embed .kpi .sub{color:#6b7280;font-size:.76rem;margin-top:2px}" +
    ".ov-embed .ov-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}" +
    ".ov-embed .ovc{background:#fff;border:1px solid #e7e9ee;border-radius:14px;padding:16px 18px;margin-bottom:14px}" +
    ".ov-embed .ovc h3{font-size:.95rem;margin:0 0 10px}.ov-embed .chart{width:100%;height:260px}" +
    ".ov-embed table{width:100%;border-collapse:collapse}.ov-embed th,.ov-embed td{text-align:left;padding:7px 6px;font-size:.88rem;border-bottom:1px solid #eef0f4}" +
    ".ov-embed th{color:#6b7280;font-weight:600;font-size:.72rem;text-transform:uppercase}.ov-embed td.num,.ov-embed th.num{text-align:right}" +
    ".ov-embed .bar{height:8px;border-radius:4px;background:#2563eb;display:inline-block;vertical-align:middle}" +
    ".ov-embed .empty{color:#6b7280;padding:18px 0;text-align:center;font-size:.88rem}" +
    ".ov-embed .nps-buckets{display:flex;gap:10px;margin-top:10px}.ov-embed .nps-buckets div{flex:1;text-align:center;padding:10px;border-radius:10px;font-weight:700}" +
    "@media(max-width:760px){.ov-embed .ov-grid{grid-template-columns:1fr}}";

  function renderOverview(p) {
    if (!document.getElementById("ov-embed-style")) {
      var st = document.createElement("style"); st.id = "ov-embed-style"; st.textContent = OV_CSS;
      document.head.appendChild(st);
    }
    function ovc(title, inner) { return el("div", { class: "ovc" }, [el("h3", { text: title }), inner]); }
    var wrap = el("div", { class: "ov-embed" }, [
      el("div", { class: "cf-row", style: "align-items:center;gap:10px;margin-bottom:14px" }, [
        el("strong", { text: "Business Overview" }),
        el("span", { id: "ov-scope", class: "cf-muted" }),
        el("span", { style: "flex:1" }),
        el("select", { id: "ov-club", class: "cf-input", style: "width:auto;display:none" }),
        el("select", { id: "ov-days", class: "cf-input", style: "width:auto" }, [
          el("option", { value: "7", text: "Last 7 days" }),
          el("option", { value: "30", text: "Last 30 days", selected: "selected" }),
          el("option", { value: "90", text: "Last 90 days" }),
        ]),
      ]),
      el("div", { id: "ov-kpis", class: "ov-kpis" }),
      el("div", { class: "ov-grid" }, [ovc("Website traffic", el("div", { id: "ch-visits", class: "chart" })),
                                        ovc("Sign-ups", el("div", { id: "ch-signups", class: "chart" }))]),
      el("div", { class: "ov-grid" }, [ovc("Traffic sources", el("div", { id: "tbl-sources" })),
                                        ovc("Top pages", el("div", { id: "tbl-pages" }))]),
      el("div", { class: "ov-grid" }, [ovc("Visitors by country", el("div", { id: "tbl-geo" })),
                                        ovc("Settlement mix", el("div", { id: "tbl-settle" }))]),
      ovc("Net Promoter Score", el("div", { id: "ov-nps" })),
      el("p", { class: "cf-muted", style: "font-size:.8rem;margin-top:6px",
        text: "First-party analytics · website traffic accrues from when the beacon went live." }),
    ]);
    p.appendChild(wrap);
    (async function () {
      try { if (!window.echarts) await loadScript("https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"); } catch (e) {}
      try { if (!window.Overview) await loadScript("/js/overview.js"); } catch (e) {}
      if (window.Overview && window.Overview.start) window.Overview.start();
    })();
  }

  // ---- master diary ---------------------------------------------------------
  function renderDiary(panel) {
    var picker = el("input", { id: "diary-picker", class: "cf-input", type: "date",
      style: "width:auto", value: UI.dateKey(state.date),
      onchange: function (e) { if (e.target.value) { state.date = new Date(e.target.value + "T00:00:00"); loadDiary(); } } });
    var bar = el("div", { class: "cf-row", style: "margin-bottom:12px" }, [
      el("button", { class: "cf-btn cf-btn-sm", text: "‹ Prev", onclick: function () { state.date = UI.addDays(state.date, -1); loadDiary(); } }),
      el("button", { class: "cf-btn cf-btn-sm", text: "Today", onclick: function () { state.date = new Date(); loadDiary(); } }),
      el("button", { class: "cf-btn cf-btn-sm", text: "Next ›", onclick: function () { state.date = UI.addDays(state.date, 1); loadDiary(); } }),
      picker,
      el("strong", { id: "diary-date", text: UI.fmtDate(state.date.toISOString()) }),
      el("span", { class: "cf-spacer" }),
      el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Walk-in booking", onclick: function () { openWalkIn(); } }),
    ]);
    panel.appendChild(bar);
    panel.appendChild(el("div", { id: "diary-cal", class: "cf-loading", text: "Loading…" }));
    loadDiary();
  }

  async function loadDiary() {
    var d = document.getElementById("diary-date"); if (d) d.textContent = UI.fmtDate(state.date.toISOString());
    var pk = document.getElementById("diary-picker"); if (pk) pk.value = UI.dateKey(state.date);
    var cal = document.getElementById("diary-cal");
    UI.clear(cal); cal.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
    try {
      if (!state.resources.length) { var r = await window.API.resources(); state.resources = r.resources || []; }
      var from = UI.dateKey(state.date) + "T00:00:00";
      var to = UI.dateKey(state.date) + "T23:59:59";
      var m = await window.API.master({ date_from: from, date_to: to });
      state.events = m.events || [];
      drawGrid();
    } catch (e) { cal.innerHTML = ""; cal.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
  }

  // Resource-timeline: a sticky time axis (col 1) + one column per bookable resource.
  // Rows are SLOT_MIN-minute slots. Each slot cell is click-to-create; events are
  // absolutely positioned cf-ev blocks whose top/height map to their start + duration.
  function drawGrid() {
    var cal = document.getElementById("diary-cal"); UI.clear(cal);
    // Bookable resources only: courts + coaches that take lessons.
    var cols = state.resources.filter(function (x) {
      return x.is_active && (x.kind === "court" || x.kind === "coach");
    });
    // Class sessions aren't a court/coach resource — they get their own "Classes"
    // column (shown only when there are class events on the visible day).
    var classEvents = state.events.filter(function (ev) {
      return (ev.booking_type || "").toLowerCase() === "class";
    });
    var hasClasses = classEvents.length > 0;
    if (!cols.length && !hasClasses) { cal.appendChild(el("div", { class: "cf-empty", text: "No resources configured." })); return; }

    var slots = ((DAY_END - DAY_START) * 60) / SLOT_MIN;     // number of time rows
    var totalCols = cols.length + (hasClasses ? 1 : 0);

    var wrap = el("div", { class: "cf-cal-wrap" });
    var grid = el("div", { class: "cf-cal" });
    grid.style.gridTemplateColumns = "62px repeat(" + totalCols + ", minmax(120px, 1fr))";
    // Header row + one row per slot, each ROW_H tall so event geometry is exact.
    grid.style.gridTemplateRows = "auto repeat(" + slots + ", " + ROW_H + "px)";

    // Header: empty corner + one head per resource (sticky via cf-cal-head) + Classes.
    grid.appendChild(el("div", { class: "cf-cal-head" }));
    cols.forEach(function (c) {
      grid.appendChild(el("div", { class: "cf-cal-head", title: c.surface || c.kind,
        text: c.name + (c.kind === "coach" ? " (coach)" : "") }));
    });
    if (hasClasses) grid.appendChild(el("div", { class: "cf-cal-head", title: "Group classes", text: "Classes" }));

    // Build the time axis + empty clickable cells, keeping a handle on each column
    // cell so we can append absolutely-positioned events afterwards.
    var CLASS_COL = "__classes__";
    var cellByCol = {};                                       // resource_id -> first cell node (positioning anchor)
    for (var s = 0; s < slots; s++) {
      var mins = s * SLOT_MIN;
      var hh = DAY_START + Math.floor(mins / 60), mm = mins % 60;
      var label = ("0" + hh).slice(-2) + ":" + ("0" + mm).slice(-2);
      // Only label whole hours to keep the axis clean; half-hour rows show blank.
      grid.appendChild(el("div", { class: "cf-cal-time", text: mm === 0 ? label : "" }));
      cols.forEach(function (c) {
        var cell = el("div", { class: "cf-cal-cell", title: "Click to book " + c.name + " at " + label });
        (function (col, h, m) {
          cell.addEventListener("click", function (e) { if (e.target === cell) openCreate(col, h, m); });
        })(c, hh, mm);
        if (s === 0) cellByCol[c.id] = cell;                  // first row of each column anchors its events
        grid.appendChild(cell);
      });
      if (hasClasses) {
        // Classes column cells are read-only (scheduling happens in the Classes tab).
        var ccell = el("div", { class: "cf-cal-cell", style: "cursor:default" });
        if (s === 0) cellByCol[CLASS_COL] = ccell;
        grid.appendChild(ccell);
      }
    }

    // Place events as cf-ev blocks. Each event lives in its column's first cell
    // (position:relative), offset by its start time and sized by its duration.
    state.events.forEach(function (ev) {
      var type = (ev.booking_type || "court").toLowerCase();
      var isClass = type === "class";
      var anchor = isClass ? cellByCol[CLASS_COL] : cellByCol[ev.resource_id];
      if (!anchor) return;                                    // event for a non-displayed resource
      var startMin = minsFromDayStart(ev.starts_at);
      var endMin = minsFromDayStart(ev.ends_at);
      if (endMin <= 0 || startMin >= (DAY_END - DAY_START) * 60) return;  // outside the visible window
      var top = Math.max(0, startMin) / SLOT_MIN * ROW_H;
      var height = Math.max(18, (Math.min(endMin, (DAY_END - DAY_START) * 60) - Math.max(0, startMin)) / SLOT_MIN * ROW_H - 2);
      var klass = ["court", "lesson", "class"].indexOf(type) >= 0 ? type : "court";
      var cancelled = ev.status === "cancelled" || ev.status === "no_show";
      var who = ev.resource_name || ev.booking_type || "Booking";
      var capTxt = isClass && ev.capacity != null
        ? " · " + (ev.enrolled != null ? ev.enrolled : 0) + "/" + ev.capacity : "";
      var block = el("div", {
        class: "cf-ev " + klass + (cancelled ? " cancelled" : ""),
        style: "top:" + top + "px;height:" + height + "px",
        title: who + " · " + UI.fmtRange(ev.starts_at, ev.ends_at) + " · " + (ev.status || "") + capTxt,
        onclick: function (e) { e.stopPropagation(); if (isClass) openClassEvent(ev); else openEvent(ev); },
      }, [
        el("div", { text: UI.fmtTime(ev.starts_at) + (isClass ? " " + who : " " + type) + capTxt }),
      ]);
      anchor.appendChild(block);
    });

    wrap.appendChild(grid); cal.appendChild(wrap);
  }

  // A class-session event on the master diary -> open its roster (and offer cancel).
  // The master feed carries session_id (diary lane) for class events.
  async function openClassEvent(ev) {
    try { await ensureClassDeps(); } catch (e) { UI.toast(UI.errMsg(e), "error"); return; }
    var sessionId = ev.session_id || ev.class_session_id || ev.id;
    var cls = { name: ev.resource_name || "Class", resource_id: ev.resource_id, capacity: ev.capacity };
    var session = { session_id: sessionId, starts_at: ev.starts_at, ends_at: ev.ends_at,
      status: ev.status, enrolled: ev.enrolled, capacity: ev.capacity };
    window.ClassUI.openRoster({ api: window.AdminAPI, cls: cls, session: session });
  }

  // ---- click-to-create / block ---------------------------------------------
  function openCreate(resource, hour, minute) {
    minute = minute || 0;
    var at = ("0" + hour).slice(-2) + ":" + ("0" + minute).slice(-2);
    // Coach columns default to a lesson; court columns to a court booking.
    var isCoach = resource.kind === "coach";
    var bg = modal("New on " + resource.name + " · " + at, function (m) {
      var dur = el("select", { class: "cf-select" }, [
        el("option", { value: "60", text: "60 min" }), el("option", { value: "90", text: "90 min" }), el("option", { value: "30", text: "30 min" }),
      ]);
      var kind = el("select", { class: "cf-select" }, [
        el("option", { value: "court", text: "Court booking" }),
        el("option", { value: "lesson", text: "Lesson" }),
        el("option", { value: "block", text: "Block time (time-off)" }),
      ]);
      kind.value = isCoach ? "lesson" : "court";
      m.appendChild(field("Type", kind));
      m.appendChild(field("Duration", dur));
      m.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px" }, [
        el("button", { class: "cf-btn", text: "Cancel", onclick: close }),
        el("button", { class: "cf-btn cf-btn-primary", text: "Create", onclick: function () {
          create(resource, hour, minute, parseInt(dur.value, 10), kind.value);
        } }),
      ]));
    });
    function close() { document.body.removeChild(bg); }
    window._closeAdminModal = close;
  }

  async function create(resource, hour, minute, durMin, kind) {
    var start = new Date(state.date); start.setHours(hour, minute || 0, 0, 0);
    var end = new Date(start.getTime() + durMin * 60000);
    try {
      if (kind === "block") {
        await window.API.timeOff({ resource_id: resource.id, starts_at: start.toISOString(), ends_at: end.toISOString(), reason: "blocked" });
      } else {
        await window.API.createBooking({
          booking_type: kind, resource_id: resource.id,
          starts_at: start.toISOString(), ends_at: end.toISOString(),
          settlement_mode: "at_court", audience: "member", parties: [],
          coach_user_id: resource.kind === "coach" ? resource.coach_user_id : null,
        });
      }
      window._closeAdminModal && window._closeAdminModal();
      UI.toast("Created.", "info"); loadDiary();
    } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  // ---- event detail: cancel / reschedule / take payment --------------------
  function openEvent(ev) {
    var bg = modal(ev.booking_type + " · " + UI.fmtTime(ev.starts_at), function (m) {
      m.appendChild(el("div", { class: "cf-row", style: "margin-bottom:8px" }, [
        el("span", { class: "cf-chip " + (ev.booking_type || ""), text: ev.booking_type || "" }),
        el("span", { class: "cf-chip " + (ev.status || ""), text: ev.status || "" }),
      ]));
      m.appendChild(el("p", { class: "cf-muted", text:
        (ev.resource_name || "") + " · " + UI.fmtRange(ev.starts_at, ev.ends_at) +
        " · " + UI.settlementLabel(ev.settlement_mode) }));
      var actions = el("div", { class: "cf-row", style: "margin-top:12px;flex-wrap:wrap" });
      if (["held", "confirmed"].indexOf(ev.status) >= 0) {
        actions.appendChild(el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Cancel", onclick: function () { cancelEv(ev); } }));
        actions.appendChild(el("button", { class: "cf-btn cf-btn-sm", text: "Mark completed", onclick: function () { statusEv(ev, "completed"); } }));
      }
      if (ev.order_id && ev.settlement_mode === "at_court") {
        actions.appendChild(el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Take payment", onclick: function () { takePayment(ev); } }));
      }
      m.appendChild(actions);
      m.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px" }, [
        el("button", { class: "cf-btn", text: "Close", onclick: close }),
      ]));
    });
    function close() { document.body.removeChild(bg); }
    window._closeAdminModal = close;
  }

  async function cancelEv(ev) {
    try { await window.API.cancelBooking(ev.id, { reason: "admin_cancel" }); done(); } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  async function statusEv(ev, st) {
    try { await window.API.setBookingStatus(ev.id, { status: st }); done(); } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  function done() { window._closeAdminModal && window._closeAdminModal(); UI.toast("Done.", "info"); loadDiary(); }

  function takePayment(ev) {
    var bg = modal("Take pay-at-court", function (m) {
      var amt = el("input", { class: "cf-input", type: "number", placeholder: "Amount (cents) — blank = full order" });
      var prov = el("select", { class: "cf-select" }, [
        el("option", { value: "cash", text: "Cash" }), el("option", { value: "card_at_desk", text: "Card at desk" }), el("option", { value: "eft", text: "EFT" }),
      ]);
      var ref = el("input", { class: "cf-input", placeholder: "Receipt # (optional)" });
      m.appendChild(field("Amount (minor units)", amt));
      m.appendChild(field("Method", prov));
      m.appendChild(field("Reference", ref));
      m.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px" }, [
        el("button", { class: "cf-btn", text: "Cancel", onclick: function () { document.body.removeChild(bg); } }),
        el("button", { class: "cf-btn cf-btn-primary", text: "Record payment", onclick: async function () {
          try {
            var body = { order_id: ev.order_id, provider: prov.value, provider_payment_id: ref.value || null };
            if (amt.value) body.amount_minor = parseInt(amt.value, 10);
            await window.API.deskPayment(body);
            document.body.removeChild(bg); UI.toast("Payment recorded.", "info"); loadDiary();
          } catch (e) { UI.toast(UI.errMsg(e), "error"); }
        } }),
      ]));
    });
  }

  // ---- walk-in / book-for-a-member booking ----------------------------------
  // An admin can book for an EXISTING member by email (it shows in that member's bookings)
  // OR for a walk-in by name (guest player). If the member email resolves to a club member
  // server-side (via for_email), it's booked for them; otherwise it falls back to a walk-in
  // guest party — same as before (docs/08).
  async function openWalkIn() {
    if (!state.resources.length) { var r = await window.API.resources(); state.resources = r.resources || []; }
    var courts = state.resources.filter(function (x) { return x.kind === "court" && x.is_active; });
    var bg = modal("Book a court (member or walk-in)", function (m) {
      var court = el("select", { class: "cf-select" }, courts.map(function (c) { return el("option", { value: c.id, text: c.name }); }));
      var when = el("input", { class: "cf-input", type: "datetime-local" });
      var dur = el("select", { class: "cf-select" }, [el("option", { value: "60", text: "60 min" }), el("option", { value: "30", text: "30 min" }), el("option", { value: "90", text: "90 min" })]);
      var clientEmail = el("input", { class: "cf-input", type: "email", placeholder: "Existing member email (optional)" });
      var guest = el("input", { class: "cf-input", placeholder: "…or walk-in player / guest name" });
      m.appendChild(field("Court", court));
      m.appendChild(field("When", when));
      m.appendChild(field("Duration", dur));
      m.appendChild(field("Member email", clientEmail));
      m.appendChild(field("Walk-in name", guest));
      m.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px" }, [
        el("button", { class: "cf-btn", text: "Cancel", onclick: function () { document.body.removeChild(bg); } }),
        el("button", { class: "cf-btn cf-btn-primary", text: "Book", onclick: async function () {
          if (!court.value || !when.value) { UI.toast("Pick a court and time.", "warn"); return; }
          var em = clientEmail.value.trim(), gn = guest.value.trim();
          var s = new Date(when.value), e2 = new Date(s.getTime() + parseInt(dur.value, 10) * 60000);
          var body = {
            booking_type: "court", resource_id: court.value,
            starts_at: s.toISOString(), ends_at: e2.toISOString(),
            // member email → "member" billing audience; pure walk-in → visitor.
            settlement_mode: "at_court", audience: em ? "member" : "visitor", parties: [],
          };
          // On-behalf: the server honours these for admins. A member email books FOR that
          // member; a non-member email or a name becomes a walk-in guest player party
          // (so the guest_requires_member guard does not reject it).
          if (em) body.for_email = em;
          if (gn) body.for_guest_name = gn;
          if (!em && !gn) { UI.toast("Enter a member email or a walk-in name.", "warn"); return; }
          try {
            await window.API.createBooking(body);
            document.body.removeChild(bg); UI.toast("Booked.", "info"); loadDiary();
          } catch (e3) { UI.toast(UI.errMsg(e3), "error"); }
        } }),
      ]));
    });
  }

  // ---- classes tab ----------------------------------------------------------
  // List class types (cf-table) + "New class" -> ClassUI form; per class ->
  // "Schedule sessions" (recurring/one-off) + view/cancel sessions + open roster.
  // Reuses the shared ClassUI components (same ones the coach console uses).
  async function renderClasses(panel) {
    var card = el("div", { class: "cf-card" }, [
      el("div", { class: "cf-row", style: "margin-bottom:6px" }, [
        el("h2", { text: "Classes", style: "margin:0" }),
        el("span", { class: "cf-spacer" }),
        el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "New class",
          onclick: function () { openNewClass(); } }),
      ]),
      el("p", { class: "cf-muted", style: "margin:-2px 0 12px",
        text: "Create class types, schedule recurring or one-off sessions, and manage rosters & attendance." }),
      el("div", { id: "cls-list", class: "cf-loading", text: "Loading classes…" }),
      el("div", { id: "cls-sessions" }),
    ]);
    panel.appendChild(card);
    try { await ensureClassDeps(); } catch (e) {
      document.getElementById("cls-list").textContent = UI.errMsg(e); return;
    }
    // Coaches power the admin-only coach selector on the class form.
    try { var cr = await window.AdminAPI.coaches(); state.coaches = (cr.coaches || []).map(function (c) {
      return { user_id: c.user_id || c.id, name: c.display_name || c.email || "Coach" }; }); }
    catch (e) { state.coaches = []; }
    loadClasses();
  }

  function loadClasses() {
    var box = document.getElementById("cls-list"); if (!box) return;
    UI.clear(box); box.appendChild(el("div", { class: "cf-loading", text: "Loading classes…" }));
    window.AdminAPI.classes().then(function (r) {
      state.classes = r.classes || [];
      window.ClassUI.renderClassList({
        host: box, classes: state.classes, currency: state.billing.currency,
        onSchedule: function (c) { openSchedule(c); },
        onSessions: function (c) { showSessions(c); },
      });
    }).catch(function (e) {
      UI.clear(box); box.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) }));
    });
  }

  function openNewClass() {
    window.ClassUI.openClassForm({
      api: window.AdminAPI, coaches: state.coaches, title: "New class",
      onSaved: function () { loadClasses(); },
    });
  }
  function openSchedule(c) {
    window.ClassUI.openScheduleForm({
      api: window.AdminAPI,
      cls: { resource_id: c.resource_id, name: c.name, capacity: c.capacity, duration_minutes: c.duration_minutes },
      onSaved: function () { loadClasses(); showSessions(c); },
    });
  }
  function showSessions(c) {
    var host = document.getElementById("cls-sessions"); if (!host) return;
    UI.clear(host);
    host.appendChild(el("div", { class: "cf-card" }, [
      el("h3", { text: "Sessions · " + (c.name || "Class"), style: "margin-top:0" }),
      el("div", { id: "cls-sessions-body" }),
    ]));
    window.ClassUI.renderSessions({
      api: window.AdminAPI,
      cls: { resource_id: c.resource_id, name: c.name, capacity: c.capacity },
      host: document.getElementById("cls-sessions-body"),
    });
  }

  // ---- console section reads (live where available) ------------------------
  async function renderResources(panel) {
    panel.appendChild(el("div", { class: "cf-card" }, [ el("h2", { text: "Resources" }), el("div", { id: "res-list", class: "cf-loading", text: "Loading…" }) ]));
    try {
      var r = await window.API.resources();
      var box = document.getElementById("res-list"); UI.clear(box);
      var t = el("table", { class: "cf-table" });
      t.appendChild(el("tr", {}, [el("th", { text: "Name" }), el("th", { text: "Kind" }), el("th", { text: "Surface" }), el("th", { text: "Capacity" }), el("th", { text: "Active" })]));
      (r.resources || []).forEach(function (res) {
        t.appendChild(el("tr", {}, [
          el("td", { text: res.name }), el("td", { text: res.kind }), el("td", { text: res.surface || "—" }),
          el("td", { text: res.capacity != null ? String(res.capacity) : "—" }), el("td", { text: res.is_active ? "✓" : "—" }),
        ]));
      });
      box.appendChild(t);
      box.appendChild(el("p", { class: "cf-muted", style: "margin-top:8px", text: "Editing resources (create/disable/reorder) needs a club-admin write API — see report." }));
    } catch (e) { document.getElementById("res-list").textContent = UI.errMsg(e); }
  }

  // Cache of recent online payments, lazily fetched once, so the People 360 drawer can show a
  // person's payment history without a per-person endpoint (composed client-side by email).
  var _paymentsByEmail = null;
  async function paymentsForEmail(email) {
    if (!email) return [];
    if (_paymentsByEmail === null) {
      _paymentsByEmail = {};
      try {
        var r = await window.AdminAPI.payments();
        (r.payments || []).forEach(function (p) {
          var k = (p.payer_email || "").toLowerCase();
          if (!k) return;
          (_paymentsByEmail[k] = _paymentsByEmail[k] || []).push(p);
        });
      } catch (e) { /* leave empty — drawer shows "no payments" */ }
    }
    return _paymentsByEmail[email.toLowerCase()] || [];
  }

  async function renderPeople(panel) {
    var card = el("div", { class: "cf-card" }, [ el("h2", { text: "People" }) ]);
    card.appendChild(el("p", { class: "cf-muted", style: "margin:-4px 0 12px",
      text: "Everyone in the club. Click a row to see their detail. To invite a coach, go to Settings → Coaches; the coach completes their own profile when they first log in with that email." }));
    var box = el("div", { id: "ppl-list", class: "cf-loading", text: "Loading people…" });
    card.appendChild(box);
    panel.appendChild(card);
    try {
      if (!window.AdminAPI) await ensureClassDeps();
      _paymentsByEmail = null;  // refresh the payment cache each time the tab opens
      var r = await window.TFAuth.apiJSON("/api/admin/people");
      UI.clear(box);
      if (!r.people || !r.people.length) {
        box.appendChild(el("div", { class: "cf-empty", text: "No members or coaches yet — invite a coach from Settings → Coaches." }));
        return;
      }
      var roleChip = { platform_admin: "confirmed", club_admin: "confirmed", coach: "lesson", member: "court", guest: "class" };
      var t = el("table", { class: "cf-table" });
      t.appendChild(el("thead", {}, [ el("tr", {}, ["Name", "Email", "Phone", "Role", "Status", "Membership", ""].map(function (h) {
        return el("th", { text: h }); })) ]));
      var tb = el("tbody");
      r.people.forEach(function (pp) {
        var name = pp.display_name || [pp.first_name, pp.surname].filter(Boolean).join(" ") || "—";
        var status = (pp.role === "coach" && pp.invite_status) ? pp.invite_status : (pp.member_status || "—");
        // Membership (free courts) applies to bookers — members/guests, not coaches/admins.
        var canHaveMembership = (pp.role === "member" || pp.role === "guest");
        var chipCell = el("td");
        chipCell.appendChild(pp.has_membership
          ? el("span", { class: "cf-chip confirmed", text: "Active" })
          : el("span", { class: "cf-muted", text: "—" }));
        var actCell = el("td");
        if (canHaveMembership) {
          var ab = el("button", { class: "cf-btn cf-btn-sm" + (pp.has_membership ? " cf-btn-danger" : " cf-btn-primary"),
            text: pp.has_membership ? "Revoke" : "Grant" });
          // Stop the row-click 360 drawer from also firing when granting/revoking.
          ab.addEventListener("click", function (ev) { ev.stopPropagation(); toggleMembership(pp, ab, chipCell); });
          actCell.appendChild(ab);
        } else {
          actCell.appendChild(el("span", { class: "cf-muted", text: "—" }));
        }
        var tr = el("tr", { style: "cursor:pointer" }, [
          el("td", { text: name }),
          el("td", { text: pp.email || "—" }),
          el("td", { text: pp.phone || "—" }),
          el("td", {}, [ el("span", { class: "cf-chip " + (roleChip[pp.role] || "court"), text: (pp.role || "").replace("_", " ") }) ]),
          el("td", { text: status }),
          chipCell,
          actCell,
        ]);
        tr.addEventListener("click", function () { openPersonDrawer(pp, name, status); });
        tb.appendChild(tr);
      });
      t.appendChild(tb);
      box.appendChild(t);
    } catch (e) { box.textContent = UI.errMsg(e); }
  }

  // Grant / revoke a member's membership (free courts) in place — updates the row, no reload.
  function toggleMembership(pp, btn, chipCell) {
    var has = !!pp.has_membership;
    if (!window.confirm((has ? "Revoke" : "Grant") + " membership for " + (pp.email || "this member") + "?")) return;
    btn.disabled = true;
    var path = "/api/admin/members/" + encodeURIComponent(pp.user_id) + "/membership";
    var req = has ? window.TFAuth.apiJSON(path, { method: "DELETE" })
                  : window.TFAuth.apiJSON(path, { method: "POST", body: { months: 1 } });
    req.then(function () {
      pp.has_membership = !has;
      UI.clear(chipCell);
      chipCell.appendChild(pp.has_membership
        ? el("span", { class: "cf-chip confirmed", text: "Active" })
        : el("span", { class: "cf-muted", text: "—" }));
      btn.className = "cf-btn cf-btn-sm" + (pp.has_membership ? " cf-btn-danger" : " cf-btn-primary");
      btn.textContent = pp.has_membership ? "Revoke" : "Grant";
      btn.disabled = false;
      UI.toast(pp.has_membership ? "Membership granted (1 month) — courts now free." : "Membership revoked.", "info");
    }).catch(function (e) { UI.toast(UI.errMsg(e), "error"); btn.disabled = false; });
  }

  // People 360 — a slide-over (shared CRMUI.drawer) with the person's profile, role, membership
  // and their online-payment history. Composed from /api/admin/people (the row) + /api/admin/payments
  // filtered by email — there is no dedicated per-person 360 endpoint yet (see report).
  function openPersonDrawer(pp, name, status) {
    var cur = state.billing.currency || "ZAR";
    var roleLabel = (pp.role || "").replace("_", " ");
    var sections = [{
      h: "Profile",
      rows: [
        ["Email", pp.email || "—"],
        ["Phone", pp.phone || "—"],
        ["Role", roleLabel || "—"],
        ["Status", status || "—"],
        ["Membership", pp.has_membership ? "Active (free courts)" : "None"],
      ],
    }];
    // Payments section is filled async (cached after the first open).
    var payHost = el("div", {}, [el("div", { class: "cf-loading", text: "Loading payments…" })]);
    sections.push({ h: "Online payments", node: payHost });
    window.CRMUI.drawer({ title: name || pp.email || "Person", subtitle: roleLabel, sections: sections });
    paymentsForEmail(pp.email).then(function (pays) {
      UI.clear(payHost);
      if (!pays.length) { payHost.appendChild(el("div", { class: "cf-empty", text: "No online payments." })); return; }
      var list = el("div", { class: "cf-list" });
      pays.forEach(function (p) {
        list.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: UI.money(p.amount_minor, p.currency_code || cur) }),
            el("div", { class: "cf-item-s", text: String(p.created_at || "").replace("T", " ").slice(0, 16) }),
          ]),
          el("span", { class: "cf-chip " + (p.refunded ? "cancelled" : "confirmed"), text: p.refunded ? "refunded" : "paid" }),
        ]));
      });
      payHost.appendChild(list);
    });
  }

  async function renderBilling(panel) {
    panel.appendChild(el("div", { class: "cf-card" }, [
      el("h2", { text: "Billing & settlement" }),
      el("div", { id: "bill-cfg", class: "cf-loading", text: "Loading config…" }),
    ]));
    try {
      var cfg = await window.API.billingConfig(principal.club_id);
      var box = document.getElementById("bill-cfg"); UI.clear(box);
      box.appendChild(el("p", { text: "Online payments: " + (cfg.online_enabled ? "ENABLED (" + cfg.provider + ")" : "disabled (pay-at-court)") +
        " · Currency: " + cfg.currency }));
    } catch (e) { document.getElementById("bill-cfg").textContent = UI.errMsg(e); }

    // Client refund requests (queue) — approve (executes the refund) / decline.
    panel.appendChild(el("div", { class: "cf-card" }, [
      el("h2", { text: "Refund requests" }),
      el("p", { class: "cf-muted", style: "margin:-4px 0 12px", text:
        "Refunds your members have asked for. Approve to refund the money via Yoco (you can also " +
        "cancel the booking), or decline with a note. They're notified either way." }),
      el("div", { id: "bill-refreq", class: "cf-loading", text: "Loading refund requests…" }),
    ]));

    // Recent online payments + refunds.
    panel.appendChild(el("div", { class: "cf-card" }, [
      el("h2", { text: "Recent online payments" }),
      el("p", { class: "cf-muted", style: "margin:-4px 0 12px", text:
        "Card payments taken via Yoco. A refund returns the money to the customer (record-only — " +
        "cancel the booking separately if you also want to release the slot)." }),
      el("div", { id: "bill-pay", class: "cf-loading", text: "Loading payments…" }),
    ]));
    loadRefundRequests();
    loadPayments();
  }

  // ---- client refund requests ------------------------------------------------
  function loadRefundRequests() {
    var box = document.getElementById("bill-refreq");
    if (!box) return;
    window.AdminAPI.refundRequests().then(function (r) {
      UI.clear(box);
      var reqs = r.requests || [];
      if (!reqs.length) {
        box.appendChild(el("div", { class: "cf-empty", text: "No refund requests." })); return;
      }
      var t = el("table", { class: "cf-table" });
      t.appendChild(el("thead", {}, [ el("tr", {}, ["When", "Member", "Order", "Requested", "Reason", "Status", ""]
        .map(function (h) { return el("th", { text: h }); })) ]));
      var tb = el("tbody");
      reqs.forEach(function (rq) {
        var cur = rq.currency_code || "ZAR";
        var pending = rq.status === "pending";
        // Reuse existing chip styles (no app.css change): refunded→green, declined/cancelled→red,
        // pending→amber (the 'held' warning style).
        var chipClass = (rq.status === "refunded") ? "confirmed"
          : (rq.status === "declined" || rq.status === "cancelled") ? "cancelled" : "held";
        var actionCell;
        if (pending) {
          var bApprove = el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Approve" });
          bApprove.addEventListener("click", function () { decideRefund(rq, bApprove, true); });
          var bDecline = el("button", { class: "cf-btn cf-btn-sm cf-btn-danger",
            style: "margin-left:6px", text: "Decline" });
          bDecline.addEventListener("click", function () { decideRefund(rq, bDecline, false); });
          actionCell = [ bApprove, bDecline ];
        } else {
          actionCell = [ el("span", { class: "cf-muted", text: rq.note || "—" }) ];
        }
        tb.appendChild(el("tr", {}, [
          el("td", { text: String(rq.created_at || "").replace("T", " ").slice(0, 16) }),
          el("td", { text: rq.requester_name || rq.requester_email || "—" }),
          el("td", { class: "num", text: UI.money(rq.order_amount_minor, cur) }),
          el("td", { class: "num", text: UI.money(rq.amount_minor, cur) }),
          el("td", { text: rq.reason || "—" }),
          el("td", {}, [ el("span", { class: "cf-chip " + chipClass, text: rq.status }) ]),
          el("td", {}, actionCell),
        ]));
      });
      t.appendChild(tb); box.appendChild(t);
    }).catch(function (e) { box.textContent = UI.errMsg(e); });
  }

  function decideRefund(rq, btn, approve) {
    var cur = rq.currency_code || "ZAR";
    var amt = UI.money(rq.amount_minor, cur);
    var who = rq.requester_name || rq.requester_email || "the member";
    var label = btn.textContent;
    if (approve) {
      if (!window.confirm("Approve and refund " + amt + " to " + who + " via Yoco?")) return;
      var alsoCancel = window.confirm("Also CANCEL the booking and free the slot?\n\nOK = refund + cancel.   Cancel = refund only (booking kept).");
      btn.disabled = true; btn.textContent = "Refunding…";
      window.AdminAPI.approveRefundRequest(rq.id, { cancel_booking: !!alsoCancel })
        .then(function (res) {
          UI.toast((alsoCancel && res && res.cancelled) ? "Refunded & booking cancelled." : "Refund approved.", "info");
          loadRefundRequests(); loadPayments();
        })
        .catch(function (e) { UI.toast(UI.errMsg(e), "error"); btn.disabled = false; btn.textContent = label; });
    } else {
      var note = window.prompt("Decline this refund request? Add an optional note for the member:", "");
      if (note === null) return;  // cancelled the prompt
      btn.disabled = true; btn.textContent = "Declining…";
      window.AdminAPI.declineRefundRequest(rq.id, { note: (note || "").trim() || undefined })
        .then(function () { UI.toast("Refund request declined.", "info"); loadRefundRequests(); })
        .catch(function (e) { UI.toast(UI.errMsg(e), "error"); btn.disabled = false; btn.textContent = label; });
    }
  }

  async function loadPayments() {
    var box = document.getElementById("bill-pay");
    if (!box) return;
    try {
      var r = await window.TFAuth.apiJSON("/api/admin/payments");
      UI.clear(box);
      if (!r.payments || !r.payments.length) {
        box.appendChild(el("div", { class: "cf-empty", text: "No online payments yet." })); return;
      }
      var t = el("table", { class: "cf-table" });
      t.appendChild(el("thead", {}, [ el("tr", {}, ["When", "Payer", "Amount", "Status", ""].map(function (h) {
        return el("th", { text: h }); })) ]));
      var tb = el("tbody");
      r.payments.forEach(function (pay) {
        var refunded = !!pay.refunded;
        var actionCell;
        if (refunded) {
          var done = el("button", { class: "cf-btn cf-btn-sm", text: "Refunded" });
          done.disabled = true;
          actionCell = [ done ];
        } else {
          // Two choices: refund only (booking stays) vs refund + cancel (frees the slot).
          var bRefund = el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Refund only" });
          bRefund.addEventListener("click", function () { doRefund(pay, bRefund, false); });
          var bCancel = el("button", { class: "cf-btn cf-btn-sm cf-btn-danger",
            style: "margin-left:6px", text: "Refund & cancel" });
          bCancel.addEventListener("click", function () { doRefund(pay, bCancel, true); });
          actionCell = [ bRefund, bCancel ];
        }
        tb.appendChild(el("tr", {}, [
          el("td", { text: String(pay.created_at || "").replace("T", " ").slice(0, 16) }),
          el("td", { text: pay.payer_email || "—" }),
          el("td", { class: "num", text: UI.money(pay.amount_minor, pay.currency_code) }),
          el("td", {}, [ el("span", { class: "cf-chip " + (refunded ? "cancelled" : "confirmed"),
            text: refunded ? "refunded" : "paid" }) ]),
          el("td", {}, actionCell),
        ]));
      });
      t.appendChild(tb); box.appendChild(t);
    } catch (e) { box.textContent = UI.errMsg(e); }
  }

  function doRefund(pay, btn, cancel) {
    var amt = UI.money(pay.amount_minor, pay.currency_code);
    var who = pay.payer_email || "the customer";
    var msg = cancel
      ? ("Refund " + amt + " to " + who + " AND cancel the booking (frees the slot)?")
      : ("Refund " + amt + " to " + who + "? The booking stays booked — cancel it separately to free the slot.");
    if (!window.confirm(msg)) return;
    var label = btn.textContent;
    btn.disabled = true; btn.textContent = "Refunding…";
    window.TFAuth.apiJSON("/api/billing/yoco/refund",
        { method: "POST", body: { order_id: pay.order_id, cancel_booking: !!cancel } })
      .then(function (r) {
        UI.toast((cancel && r && r.cancelled) ? "Refunded & booking cancelled." : "Refund issued.", "info");
        loadPayments();
      })
      .catch(function (e) { UI.toast(UI.errMsg(e), "error"); btn.disabled = false; btn.textContent = label; });
  }

  // ---- cockpit / financials (Phase D owner lane) ----------------------------
  // Owner financial cockpit: KPI strip + revenue-by-service + per-coach commission/rent/net.
  // Reads /api/admin/cockpit/* (admin-gated, club-scoped). Reuses cf-* (no app.css change):
  // KPI "stat" cards are cf-card with inline emphasis.
  function monthRange(which) {
    // 'this' | 'last' -> {from, to} ISO date strings (from inclusive, to exclusive).
    var now = new Date();
    var y = now.getFullYear(), m = now.getMonth();
    if (which === "last") { m -= 1; if (m < 0) { m = 11; y -= 1; } }
    var pad = function (n) { return (n < 10 ? "0" : "") + n; };
    var from = y + "-" + pad(m + 1) + "-01";
    var ny = (m === 11) ? y + 1 : y, nm = (m === 11) ? 0 : m + 1;
    var to = ny + "-" + pad(nm + 1) + "-01";
    return { from: from, to: to };
  }

  var cockpitState = { range: "this" };

  // The owner cockpit reuses the SHARED reporting library (window.CRMUI) — the same
  // primitives the coach console renders from (one engine, two lenses). admin.html loads
  // crm_ui.js; the AdminAPI wrappers come via ensureClassDeps (already preloaded on boot).
  async function renderCockpit(panel) {
    panel.appendChild(el("div", { class: "cf-card" }, [
      el("h2", { text: "Financials" }),
      el("p", { class: "cf-muted", style: "margin:-4px 0 10px", text:
        "Revenue, per-coach commission and rent. Commission accrues on collected (ex-VAT) lesson revenue " +
        "— online at payment, arrears when the coach marks it collected." }),
    ]));
    var ctrl = el("div", { class: "cf-row", style: "gap:8px;margin-bottom:12px" });
    [["this", "This month"], ["last", "Last month"], ["all", "All time"]].forEach(function (r) {
      var a = el("button", { class: "cf-btn cf-btn-sm" + (cockpitState.range === r[0] ? " cf-btn-primary" : ""),
        "data-range": r[0], text: r[1] });
      a.addEventListener("click", function () { cockpitState.range = r[0]; syncRangeButtons(ctrl); renderCockpit2(); });
      ctrl.appendChild(a);
    });
    panel.appendChild(ctrl);
    panel.appendChild(el("div", { id: "cockpit-host" }));
    renderCockpit2();
  }

  function syncRangeButtons(ctrl) {
    Array.prototype.forEach.call(ctrl.querySelectorAll("button"), function (b) {
      b.classList.toggle("cf-btn-primary", b.getAttribute("data-range") === cockpitState.range);
    });
  }

  async function renderCockpit2() {
    var host = document.getElementById("cockpit-host");
    if (!host) return;
    UI.clear(host);
    host.appendChild(el("div", { class: "cf-loading", text: "Loading financials…" }));
    var opts = (cockpitState.range === "all") ? {} : monthRange(cockpitState.range);
    try {
      if (!window.AdminAPI) await ensureClassDeps();
      var summary = await window.AdminAPI.cockpitSummary(opts);
      var earnings = await window.AdminAPI.cockpitCoachEarnings(opts);
      var revenue = await window.AdminAPI.cockpitRevenue(opts);
      var cur = summary.currency || "ZAR";
      UI.clear(host);
      var C = window.CRMUI;

      // KPI strip — shared CRMUI.stats (matches the coach lens). "You keep" = owner cut
      // of commission; "Net to coaches" = what the coaches earn after rent.
      var coaches = (earnings && earnings.coaches) || [];
      var netToCoaches = coaches.reduce(function (a, c) { return a + (c.net_to_coach_minor || 0); }, 0);
      host.appendChild(C.stats([
        { value: UI.money(summary.net_revenue_minor, cur), label: "Net revenue" },
        { value: UI.money(summary.commission_earned_minor, cur), label: "Commission — you keep" },
        { value: UI.money(netToCoaches, cur), label: "Net to coaches" },
        { value: UI.money(summary.rent_due_minor, cur), label: "Rent due" },
        { value: String(summary.active_members) , label: "Active members" },
        { value: UI.money(summary.mrr_minor, cur), label: "MRR (active value)" },
        { value: String(summary.lessons_paid), label: "Lessons paid" },
      ]));

      // Monthly revenue trend — CRMUI.bars (net revenue per month, newest last).
      var byMonth = {};
      ((revenue && revenue.revenue) || []).forEach(function (r) {
        byMonth[r.month] = (byMonth[r.month] || 0) + (r.net_minor || 0);
      });
      var months = Object.keys(byMonth).sort();
      var trend = months.map(function (m) {
        return { label: m.slice(5) + "/" + m.slice(2, 4), value: byMonth[m] / 100,
          title: m + " · " + UI.money(byMonth[m], cur) };
      });
      var trendCard = el("div", { class: "cf-card" });
      trendCard.appendChild(C.sectionHead("Net revenue — by month"));
      trendCard.appendChild(C.bars(trend, { fmt: function (v) { return UI.money(Math.round(v * 100), cur); },
        empty: "No revenue history yet." }));
      host.appendChild(trendCard);

      // Per-coach commission/rent/net — a fuller table than CRMUI.statementTable (8 cols),
      // built on cf-table (the same class statementTable uses). Rows open the coach drawer.
      var ce = el("div", { class: "cf-card" });
      ce.appendChild(C.sectionHead("Per coach"));
      if (!coaches.length) {
        ce.appendChild(el("div", { class: "cf-empty", text: "No coach agreements yet — set them up in Settings → Coach pay." }));
      } else {
        var t = el("table", { class: "cf-table" });
        t.appendChild(el("thead", {}, [el("tr", {}, ["Coach", "Lessons", "Gross", "Commission (you)", "Coach earns", "Rent due", "Net to coach", "Balance"].map(function (h, i) {
          return el("th", { class: i === 0 ? "" : "num", text: h }); }))]));
        var tb = el("tbody");
        coaches.forEach(function (c) {
          var tr = el("tr", { style: "cursor:pointer" }, [
            el("td", { text: c.coach_name || "Coach" }),
            el("td", { class: "num", text: String(c.lesson_count) }),
            el("td", { class: "num", text: UI.money(c.gross_lesson_minor, cur) }),
            el("td", { class: "num", text: UI.money(c.commission_earned_minor, cur) }),
            el("td", { class: "num", text: UI.money(c.coach_earning_minor, cur) }),
            el("td", { class: "num", text: UI.money(c.rent_due_minor, cur) }),
            el("td", { class: "num", text: UI.money(c.net_to_coach_minor, cur) }),
            el("td", { class: "num", text: UI.money(c.lifetime_balance_minor, cur) }),
          ]);
          tr.addEventListener("click", function () { openCoachDrawer(c, cur); });
          tb.appendChild(tr);
        });
        t.appendChild(tb); ce.appendChild(t);
      }
      host.appendChild(ce);

      // Revenue by service kind (gross / refunds / net) — honours the refund gotcha: the
      // server's cockpit_revenue counts refunded rows, so Net = gross − refunds here.
      var rv = el("div", { class: "cf-card" });
      rv.appendChild(C.sectionHead("Revenue by service"));
      var rows = (revenue && revenue.revenue) || [];
      if (!rows.length) {
        rv.appendChild(el("div", { class: "cf-empty", text: "No revenue in this period yet." }));
      } else {
        var rt = el("table", { class: "cf-table" });
        rt.appendChild(el("thead", {}, [el("tr", {}, ["Month", "Service", "Gross", "Refunds", "Net"].map(function (h, i) {
          return el("th", { class: i < 2 ? "" : "num", text: h }); }))]));
        var rtb = el("tbody");
        rows.forEach(function (r) {
          rtb.appendChild(el("tr", {}, [
            el("td", { text: r.month }),
            el("td", { text: (r.service_kind || "other").replace("_", " ") }),
            el("td", { class: "num", text: UI.money(r.gross_minor, cur) }),
            el("td", { class: "num", text: UI.money(r.refund_minor, cur) }),
            el("td", { class: "num", text: UI.money(r.net_minor, cur) }),
          ]));
        });
        rt.appendChild(rtb); rv.appendChild(rt);
      }
      host.appendChild(rv);
    } catch (e) {
      UI.clear(host); host.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) }));
    }
  }

  // Coach 360 (financial lens) — opens the shared CRMUI.drawer with this coach's
  // period figures. Same drawer component the People 360 uses.
  function openCoachDrawer(c, cur) {
    window.CRMUI.drawer({
      title: c.coach_name || "Coach",
      subtitle: c.lesson_count + " lesson" + (c.lesson_count === 1 ? "" : "s") + " this period",
      sections: [{
        h: "This period",
        rows: [
          ["Gross lesson revenue", UI.money(c.gross_lesson_minor, cur)],
          ["Commission you keep", UI.money(c.commission_earned_minor, cur)],
          ["Coach earns", UI.money(c.coach_earning_minor, cur)],
          ["Rent due", UI.money(c.rent_due_minor, cur)],
          ["Net to coach", UI.money(c.net_to_coach_minor, cur)],
        ],
      }, {
        h: "Lifetime",
        rows: [["Ledger balance", UI.money(c.lifetime_balance_minor, cur)]],
      }],
    });
  }

  // ---- modal helper ---------------------------------------------------------
  function modal(title, build) {
    var bg = el("div", { class: "cf-modal-bg" });
    var m = el("div", { class: "cf-modal" }, [ el("h2", { text: title }) ]);
    build(m);
    bg.appendChild(m); document.body.appendChild(bg);
    bg.addEventListener("click", function (e) { if (e.target === bg) document.body.removeChild(bg); });
    return bg;
  }
  function field(label, control) {
    return el("div", { class: "cf-field" }, [ el("label", { text: label }), control ]);
  }

  window.AdminConsole = {
    start: async function (p) {
      UI = window.UI; el = UI.el; principal = p;
      try { state.billing = await window.API.billingConfig(p.club_id); } catch (e) {}
      // Preload the class deps so master-diary class events can open their roster on
      // first click without a load hitch; ignore failures (the Classes tab retries).
      ensureClassDeps().catch(function () {});
      shell();
    },
  };
})();

// widgets/calendar.js — Widgets.Calendar: the ONE staff diary calendar (FRONTEND-STANDARDISATION
// Wave 5). Day / Week / Month views + optional Court & Coach filters, every event drilling out via
// cfg.onNavigate. Used by the admin console (whole club, all filters) and the coach app (self, via
// its adapter) — the owner's "one calendar, only the filters differ."
//
//   cfg.data.events({from, to}) -> Promise<CalEvent[]>   (adapter fetches + NORMALISES; the widget
//        never calls an endpoint). CalEvent = { id, kind|booking_type, starts_at, ends_at, status,
//        resource_name?, coach_name?, booked_by_name?, resource_id?, coach_user_id?, _class? }.
//   cfg.filterBar = { courts:[{id,name}], coaches:[{id,name}] }  — a dropdown shows only when its
//        list is provided (admin passes both; the coach passes neither — its adapter is self-scoped).
//   cfg.onNavigate(ev)   — the app routes it (a booking -> its event story; a class -> its roster).
//   cfg.classicLink      — show the "open the classic drag-timeline" link (admin only).
//   cfg.view / cfg.date  — optional initial view ("day"|"week"|"month") + date (YYYY-MM-DD).
//   cfg.courtId / cfg.coachId — optional INITIAL filter selection (the coach app defaults coachId
//        to the signed-in coach = "just me"; the user clears it to "All" to see the whole club).
(function () {
  function mount(host, cfg) {
    var UI = window.UI, el = UI.el;
    // cfg.courtId / cfg.coachId — optional INITIAL filter selection (e.g. the coach app defaults the
    // coach filter to the signed-in coach = "just me"; clear the dropdown to "All" to see everyone).
    var state = { view: cfg.view || "day", date: cfg.date || UI.dateKey(new Date()),
                  courtId: cfg.courtId ? String(cfg.courtId) : "", coachId: cfg.coachId ? String(cfg.coachId) : "" };
    var fb = cfg.filterBar || {};
    // Resource-timeline grid geometry (the classic day view, ported for cfg.grid). Club hours
    // 06:00–22:00, 30-min rows, ROW_H px each (matches .cf-cal-cell min-height in app.css).
    var DAY_START = 6, DAY_END = 22, SLOT_MIN = 30, ROW_H = 46, CLASS_COL = "__classes__";
    function minsFromDayStart(iso) { var d = new Date(iso); return (d.getHours() - DAY_START) * 60 + d.getMinutes(); }

    function ymd(d) { return UI.dateKey(d); }
    function parseDay(day) { return new Date(day + "T12:00:00"); }
    function weekStartOf(d) { var x = new Date(d); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; }
    function longDay(d) { try { return d.toLocaleDateString("en-ZA", { weekday: "long", day: "numeric", month: "long" }); } catch (e) { return ymd(d); } }
    function monthLabel(ym) { try { var p = ym.split("-"); return new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1, 1).toLocaleDateString("en-ZA", { month: "long", year: "numeric" }); } catch (e) { return ym; } }
    function dayLabel(iso) { try { return new Date(iso + "T12:00:00").toLocaleDateString("en-ZA", { weekday: "short", day: "numeric", month: "short" }); } catch (e) { return iso; } }
    function evKind(ev) { return String(ev.kind || ev.booking_type || "court").toLowerCase(); }
    function evDateKey(ev) { try { return UI.dateKey(new Date(ev.starts_at)); } catch (e) { return (ev.starts_at || "").slice(0, 10); } }
    function typeLabel(t) { return ({ court: "Court", lesson: "Lesson", class: "Class" })[t] || "Session"; }
    // Send FULL-DAY bounds (T00:00:00 → T23:59:59). A bare "YYYY-MM-DD" casts to MIDNIGHT
    // server-side, so a same-day query became a zero-width window (from==to==00:00) that showed
    // nothing — the classic diary works because it sends these explicit day bounds.
    function dayStart(k) { return k + "T00:00:00"; }
    function dayEnd(k) { return k + "T23:59:59"; }
    function rangeFor() {
      var d = parseDay(state.date);
      if (state.view === "week") { var s = weekStartOf(d), e = new Date(s); e.setDate(s.getDate() + 6); return { from: dayStart(ymd(s)), to: dayEnd(ymd(e)) }; }
      if (state.view === "month") { return { from: dayStart(ymd(new Date(d.getFullYear(), d.getMonth(), 1))), to: dayEnd(ymd(new Date(d.getFullYear(), d.getMonth() + 1, 0))) }; }
      return { from: dayStart(state.date), to: dayEnd(state.date) };
    }

    function eventRow(ev) {
      var t = evKind(ev), tappable = !!(ev.id && cfg.onNavigate);
      return el("div", { class: "cf-item" + (tappable ? " cf-item-tap" : ""), onclick: function () { if (tappable) cfg.onNavigate(ev); } }, [
        el("span", { class: "cf-chip " + (["court", "lesson", "class"].indexOf(t) >= 0 ? t : "court"), text: (function () { try { return UI.fmtTime(ev.starts_at); } catch (e) { return t; } })() }),
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: ev.resource_name || typeLabel(t) }),
          el("div", { class: "cf-item-s", text: [ev.booked_by_name, ev.coach_name].filter(Boolean).join(" · ") || t }),
        ]),
        UI.statusChip(ev.status),
      ]);
    }
    function dayView(events) {
      if (!events.length) return el("div", { class: "cf-empty", text: "Nothing booked." });
      var c = UI.card([]), l = el("div", { class: "cf-list" });
      events.forEach(function (ev) { l.appendChild(eventRow(ev)); });
      c.appendChild(l); return c;
    }
    // The resource-timeline grid (one column per court + coach, time down the side; events are
    // absolutely-positioned blocks) — ported from the classic diary, but every block drills to the
    // SHARED event story via cfg.onNavigate (not the old minimal popup). Court/coach dropdowns
    // filter the COLUMNS. Empty cells are non-interactive (walk-ins live in the classic diary).
    function gridColumns(events) {
      var courts = fb.courts || [], coaches = fb.coaches || [];
      if (state.courtId) courts = courts.filter(function (c) { return String(c.id) === String(state.courtId); });
      if (state.coachId) {
        coaches = coaches.filter(function (c) { return String(c.id) === String(state.coachId); });
        // Only the courts THIS coach's events actually use (their held courts) — not every court —
        // so picking a coach shows just their day, not the whole club's court grid.
        var used = {};
        (events || []).forEach(function (ev) {
          if (evKind(ev) === "court" && ev.resource_id != null) used["court:" + ev.resource_id] = true;
          if (evKind(ev) === "class" && ev.court_resource_id != null) used["court:" + ev.court_resource_id] = true;
        });
        courts = courts.filter(function (c) { return used["court:" + c.id]; });
      } else {
        // Hide coaches with NO lessons this day — keeps the grid court-focused on quiet days.
        // (A coach explicitly picked in the dropdown above is always shown, even if empty.)
        var active = {};
        (events || []).forEach(function (ev) { if (evKind(ev) === "lesson" && ev.coach_user_id != null) active["coach:" + ev.coach_user_id] = true; });
        coaches = coaches.filter(function (c) { return active["coach:" + c.id]; });
      }
      var cols = [];
      courts.forEach(function (c) { cols.push({ key: "court:" + c.id, name: c.name || "Court" }); });
      coaches.forEach(function (c) { cols.push({ key: "coach:" + c.id, name: (c.name || "Coach") + " (coach)" }); });
      return cols;
    }
    function evColKey(ev) {
      var t = evKind(ev);
      // A class ON a court sits under THAT court column (the diary feed fans a multi-court class into
      // one event per court, each carrying court_resource_id); a courtless class → the Classes column.
      if (t === "class") return ev.court_resource_id ? ("court:" + ev.court_resource_id) : CLASS_COL;
      if (t === "lesson") return "coach:" + ev.coach_user_id;   // lessons sit under their coach
      return "court:" + ev.resource_id;                          // court bookings under the court
    }
    function gridDayView(events) {
      var cols = gridColumns(events);
      // Show the Classes column whenever there are class events in view (incl. a coach's own classes
      // when they're filtered) — only a COURT filter hides it (courts have no classes).
      // Only COURTLESS classes need the Classes column now — a class on a court renders under it.
      var hasClasses = !state.courtId && events.some(function (ev) { return evKind(ev) === "class" && !ev.court_resource_id; });
      if (!cols.length && !hasClasses) return el("div", { class: "cf-empty", text: "No courts or coaches configured." });
      var slots = ((DAY_END - DAY_START) * 60) / SLOT_MIN, totalCols = cols.length + (hasClasses ? 1 : 0);
      var wrap = el("div", { class: "cf-cal-wrap" }), grid = el("div", { class: "cf-cal" });
      grid.style.gridTemplateColumns = "62px repeat(" + totalCols + ", minmax(120px, 1fr))";
      grid.style.gridTemplateRows = "auto repeat(" + slots + ", " + ROW_H + "px)";
      grid.appendChild(el("div", { class: "cf-cal-head" }));
      cols.forEach(function (c) { grid.appendChild(el("div", { class: "cf-cal-head", text: c.name })); });
      if (hasClasses) grid.appendChild(el("div", { class: "cf-cal-head", text: "Classes" }));
      var cellByCol = {};
      for (var s = 0; s < slots; s++) {
        var mins = s * SLOT_MIN, hh = DAY_START + Math.floor(mins / 60), mm = mins % 60;
        grid.appendChild(el("div", { class: "cf-cal-time", text: mm === 0 ? (("0" + hh).slice(-2) + ":00") : "" }));
        cols.forEach(function (c) { var cell = el("div", { class: "cf-cal-cell", style: "cursor:default" }); if (s === 0) cellByCol[c.key] = cell; grid.appendChild(cell); });
        if (hasClasses) { var cc = el("div", { class: "cf-cal-cell", style: "cursor:default" }); if (s === 0) cellByCol[CLASS_COL] = cc; grid.appendChild(cc); }
      }
      events.forEach(function (ev) {
        var anchor = cellByCol[evColKey(ev)];
        if (!anchor) return;                                     // event for a hidden column
        var startMin = minsFromDayStart(ev.starts_at), endMin = minsFromDayStart(ev.ends_at);
        if (endMin <= 0 || startMin >= (DAY_END - DAY_START) * 60) return;
        var top = Math.max(0, startMin) / SLOT_MIN * ROW_H;
        var height = Math.max(18, (Math.min(endMin, (DAY_END - DAY_START) * 60) - Math.max(0, startMin)) / SLOT_MIN * ROW_H - 2);
        var t = evKind(ev), klass = ["court", "lesson", "class"].indexOf(t) >= 0 ? t : "court", tappable = !!(ev.id && cfg.onNavigate);
        var who = ev.booked_by_name || ev.resource_name || typeLabel(t);
        var block = el("div", {
          class: "cf-ev " + klass + (tappable ? " cf-item-tap" : ""),
          style: "top:" + top + "px;height:" + height + "px" + (tappable ? ";cursor:pointer" : ""),
          title: who + " · " + (function () { try { return UI.fmtRange(ev.starts_at, ev.ends_at); } catch (e) { return ""; } })() + (ev.status ? " · " + ev.status : ""),
          onclick: function (e) { e.stopPropagation(); if (tappable) cfg.onNavigate(ev); },
        }, [el("div", { text: (function () { try { return UI.fmtTime(ev.starts_at); } catch (e) { return ""; } })() + " · " + who })]);
        anchor.appendChild(block);
      });
      wrap.appendChild(grid); return wrap;
    }
    function weekView(events) {
      var s = weekStartOf(parseDay(state.date)), byDate = {};
      events.forEach(function (ev) { var k = evDateKey(ev); (byDate[k] = byDate[k] || []).push(ev); });
      var box = el("div", {});
      for (var i = 0; i < 7; i++) {
        var dd = new Date(s); dd.setDate(s.getDate() + i); var k = ymd(dd), evs = byDate[k] || [];
        box.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin:14px 2px 6px" }, [
          el("div", { style: "font-weight:700", text: dayLabel(k) }),
          el("div", { class: "cf-muted", style: "font-size:.82rem", text: evs.length ? (evs.length + " booked") : "—" }),
        ]));
        if (evs.length) { var c = UI.card([]), l = el("div", { class: "cf-list" }); evs.forEach(function (ev) { l.appendChild(eventRow(ev)); }); c.appendChild(l); box.appendChild(c); }
      }
      return box;
    }
    function monthView(events) {
      var d = parseDay(state.date), y = d.getFullYear(), m = d.getMonth(), counts = {}, maxC = 0;
      events.forEach(function (ev) { var k = evDateKey(ev); counts[k] = (counts[k] || 0) + 1; });
      Object.keys(counts).forEach(function (k) { if (counts[k] > maxC) maxC = counts[k]; });
      var lead = (new Date(y, m, 1).getDay() + 6) % 7, dim = new Date(y, m + 1, 0).getDate(), todayKey = UI.dateKey(new Date());
      var grid = el("div", { style: "display:grid;grid-template-columns:repeat(7,1fr);gap:5px" });
      ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"].forEach(function (h) { grid.appendChild(el("div", { style: "font-size:11px;color:var(--muted);text-align:center", text: h })); });
      for (var i = 0; i < lead; i++) grid.appendChild(el("div", {}));
      for (var dn = 1; dn <= dim; dn++) {
        var k = ymd(new Date(y, m, dn)), cnt = counts[k] || 0;
        var bg = cnt ? "rgba(31,122,77," + (0.12 + 0.7 * (maxC ? cnt / maxC : 0)).toFixed(2) + ")" : "var(--canvas,#f1f4ef)";
        grid.appendChild(el("div", {
          class: "cf-item-tap",
          style: "border-radius:8px;padding:8px 6px;min-height:52px;cursor:pointer;background:" + bg + ";border:" + (k === todayKey ? "2px solid var(--green,#1f7a4d)" : "1px solid transparent"),
          onclick: (function (kk) { return function () { state.view = "day"; state.date = kk; render(); }; })(k),
        }, [
          el("div", { style: "font-size:12px;font-weight:600", text: String(dn) }),
          cnt ? el("div", { style: "font-size:11px;color:var(--muted);margin-top:2px", text: cnt + (cnt === 1 ? " booking" : " bookings") }) : null,
        ].filter(Boolean)));
      }
      return el("div", {}, [el("div", { class: "cf-card" }, [grid])]);
    }

    function render() {
      var wrap = el("div", {});
      if (cfg.title) wrap.appendChild(el("h1", { style: "margin:0 0 8px", text: cfg.title }));
      function seg(k, label) { return el("button", { class: state.view === k ? "on" : "", text: label, onclick: function () { state.view = k; render(); } }); }
      wrap.appendChild(el("div", { class: "cf-segment cf-seg-lg" }, [seg("day", "Day"), seg("week", "Week"), seg("month", "Month")]));

      // Filters (only those the app supplied options for).
      var filterRow = el("div", { class: "cf-row", style: "gap:8px;flex-wrap:wrap;margin:10px 0" });
      if (fb.courts && fb.courts.length) {
        var courtSel = el("select", { class: "cf-input", style: "max-width:180px" }, [el("option", { value: "", text: "All courts" })].concat(fb.courts.map(function (c) { return el("option", { value: c.id, text: c.name || "Court" }); })));
        courtSel.value = state.courtId; courtSel.addEventListener("change", function () { state.courtId = courtSel.value; render(); });
        filterRow.appendChild(courtSel);
      }
      if (fb.coaches && fb.coaches.length) {
        var coachSel = el("select", { class: "cf-input", style: "max-width:180px" }, [el("option", { value: "", text: "All coaches" })].concat(fb.coaches.map(function (c) { return el("option", { value: String(c.id), text: c.name || "Coach" }); })));
        coachSel.value = state.coachId; coachSel.addEventListener("change", function () { state.coachId = coachSel.value; render(); });
        filterRow.appendChild(coachSel);
      }
      if (filterRow.children.length) wrap.appendChild(filterRow);

      // Date navigation (unit follows the view).
      var d = parseDay(state.date);
      function shift(n) { var x = new Date(d); if (state.view === "month") x.setMonth(x.getMonth() + n); else if (state.view === "week") x.setDate(x.getDate() + 7 * n); else x.setDate(x.getDate() + n); state.date = ymd(x); render(); }
      var label = state.view === "month" ? monthLabel(state.date.slice(0, 7)) : (state.view === "week" ? ("Week of " + dayLabel(ymd(weekStartOf(d)))) : longDay(d));
      wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:10px" }, [
        el("div", { style: "font-weight:600", text: label }),
        el("div", { class: "cf-row", style: "gap:6px" }, [
          el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "‹", onclick: function () { shift(-1); } }),
          el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Today", onclick: function () { state.date = UI.dateKey(new Date()); render(); } }),
          el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "›", onclick: function () { shift(1); } }),
        ]),
      ]));

      var body = el("div", {}, [el("div", { class: "cf-loading", text: "Loading…" })]);
      wrap.appendChild(body);
      if (cfg.classicLink) {
        wrap.appendChild(el("p", { class: "cf-muted", style: "font-size:.82rem;margin-top:12px" }, [
          document.createTextNode("Need the full drag-and-drop timeline (walk-ins · block time · desk-pay)? "),
          el("a", { href: "/admin-classic#diary", text: "Open the classic diary ›" }),
        ]));
      }
      UI.clear(host); host.appendChild(wrap);

      var useGrid = !!cfg.grid && state.view === "day";
      var range = rangeFor();
      Promise.resolve(cfg.data.events(range)).then(function (events) {
        events = (events || []).filter(function (ev) {
          if (ev.status === "cancelled") return false;
          // Agenda mode filters the events themselves. Grid mode normally lets the COLUMNS carry the
          // court filter — BUT a COACH filter must show ONLY that coach's activity (their lessons +
          // the courts those lessons hold; a held court carries the coach_user_id, a standalone court
          // does not), so we filter events by coach even in grid mode.
          if (!useGrid) {
            if (state.courtId && String(ev.resource_id) !== String(state.courtId)) return false;
            if (state.coachId && String(ev.coach_user_id) !== String(state.coachId)) return false;
          } else if (state.coachId && String(ev.coach_user_id) !== String(state.coachId)) {
            return false;
          }
          return true;
        }).sort(function (a, b) { return String(a.starts_at).localeCompare(String(b.starts_at)); });
        UI.clear(body);
        if (state.view === "month") body.appendChild(monthView(events));
        else if (state.view === "week") body.appendChild(weekView(events));
        else if (useGrid) body.appendChild(gridDayView(events));
        else body.appendChild(dayView(events));
      }, function (e) { UI.clear(body); body.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }

    render();
    return { refresh: render, destroy: function () { UI.clear(host); } };
  }

  window.Widgets = window.Widgets || {};
  window.Widgets.Calendar = { mount: mount };
})();

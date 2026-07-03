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
(function () {
  function mount(host, cfg) {
    var UI = window.UI, el = UI.el;
    var state = { view: cfg.view || "day", date: cfg.date || UI.dateKey(new Date()), courtId: "", coachId: "" };
    var fb = cfg.filterBar || {};

    function ymd(d) { return UI.dateKey(d); }
    function parseDay(day) { return new Date(day + "T12:00:00"); }
    function weekStartOf(d) { var x = new Date(d); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; }
    function longDay(d) { try { return d.toLocaleDateString("en-ZA", { weekday: "long", day: "numeric", month: "long" }); } catch (e) { return ymd(d); } }
    function monthLabel(ym) { try { var p = ym.split("-"); return new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1, 1).toLocaleDateString("en-ZA", { month: "long", year: "numeric" }); } catch (e) { return ym; } }
    function dayLabel(iso) { try { return new Date(iso + "T12:00:00").toLocaleDateString("en-ZA", { weekday: "short", day: "numeric", month: "short" }); } catch (e) { return iso; } }
    function evKind(ev) { return String(ev.kind || ev.booking_type || "court").toLowerCase(); }
    function evDateKey(ev) { try { return UI.dateKey(new Date(ev.starts_at)); } catch (e) { return (ev.starts_at || "").slice(0, 10); } }
    function typeLabel(t) { return ({ court: "Court", lesson: "Lesson", class: "Class" })[t] || "Session"; }
    function rangeFor() {
      var d = parseDay(state.date);
      if (state.view === "week") { var s = weekStartOf(d), e = new Date(s); e.setDate(s.getDate() + 6); return { from: ymd(s), to: ymd(e) }; }
      if (state.view === "month") { return { from: ymd(new Date(d.getFullYear(), d.getMonth(), 1)), to: ymd(new Date(d.getFullYear(), d.getMonth() + 1, 0)) }; }
      return { from: state.date, to: state.date };
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

      var range = rangeFor();
      Promise.resolve(cfg.data.events(range)).then(function (events) {
        events = (events || []).filter(function (ev) {
          if (ev.status === "cancelled") return false;
          if (state.courtId && String(ev.resource_id) !== String(state.courtId)) return false;
          if (state.coachId && String(ev.coach_user_id) !== String(state.coachId)) return false;
          return true;
        }).sort(function (a, b) { return String(a.starts_at).localeCompare(String(b.starts_at)); });
        UI.clear(body);
        if (state.view === "month") body.appendChild(monthView(events));
        else if (state.view === "week") body.appendChild(weekView(events));
        else body.appendChild(dayView(events));
      }, function (e) { UI.clear(body); body.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
    }

    render();
    return { refresh: render, destroy: function () { UI.clear(host); } };
  }

  window.Widgets = window.Widgets || {};
  window.Widgets.Calendar = { mount: mount };
})();

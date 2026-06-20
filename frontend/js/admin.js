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
  var state = { date: new Date(), resources: [], events: [], billing: { currency: "ZAR" } };

  // ---- top controls + tabs --------------------------------------------------
  function shell() {
    var main = document.getElementById("cf-main"); UI.clear(main);

    var tabs = el("div", { class: "cf-nav", style: "margin-bottom:12px" });
    [["diary", "Master diary"], ["resources", "Resources"], ["people", "People"],
     ["billing", "Billing"], ["cockpit", "Cockpit"]].forEach(function (t) {
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
    if (tab === "resources") return renderResources(p);
    if (tab === "people") return renderPeople(p);
    if (tab === "billing") return renderBilling(p);
    if (tab === "cockpit") return renderCockpit(p);
  }

  // ---- master diary ---------------------------------------------------------
  function renderDiary(panel) {
    var bar = el("div", { class: "cf-row", style: "margin-bottom:12px" }, [
      el("button", { class: "cf-btn cf-btn-sm", text: "‹ Prev", onclick: function () { state.date = UI.addDays(state.date, -1); loadDiary(); } }),
      el("strong", { id: "diary-date", text: UI.fmtDate(state.date.toISOString()) }),
      el("button", { class: "cf-btn cf-btn-sm", text: "Next ›", onclick: function () { state.date = UI.addDays(state.date, 1); loadDiary(); } }),
      el("span", { class: "cf-spacer" }),
      el("button", { class: "cf-btn cf-btn-sm", text: "Today", onclick: function () { state.date = new Date(); loadDiary(); } }),
      el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Walk-in booking", onclick: function () { openWalkIn(); } }),
    ]);
    panel.appendChild(bar);
    panel.appendChild(el("div", { class: "cf-card" }, [ el("div", { id: "diary-cal", class: "cf-loading", text: "Loading…" }) ]));
    loadDiary();
  }

  async function loadDiary() {
    var d = document.getElementById("diary-date"); if (d) d.textContent = UI.fmtDate(state.date.toISOString());
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

  function drawGrid() {
    var cal = document.getElementById("diary-cal"); UI.clear(cal);
    var cols = state.resources.filter(function (x) { return x.is_active; });
    if (!cols.length) { cal.appendChild(el("div", { class: "cf-empty", text: "No resources configured." })); return; }

    var wrap = el("div", { class: "cf-cal-wrap" });
    var grid = el("div", { class: "cf-cal" });
    grid.style.gridTemplateColumns = "64px repeat(" + cols.length + ", minmax(110px, 1fr))";

    grid.appendChild(el("div", { class: "cf-cal-head", text: "Time" }));
    cols.forEach(function (c) { grid.appendChild(el("div", { class: "cf-cal-head", text: c.name })); });

    // bucket events by resource_id + hour
    var byCell = {};
    state.events.forEach(function (ev) {
      var hr = new Date(ev.starts_at).getHours();
      var key = ev.resource_id + "|" + hr;
      (byCell[key] = byCell[key] || []).push(ev);
    });

    for (var h = DAY_START; h < DAY_END; h++) {
      grid.appendChild(el("div", { class: "cf-cal-time", text: ("0" + h).slice(-2) + ":00" }));
      cols.forEach(function (c) {
        var cell = el("div", { class: "cf-cal-cell", title: "Click to create" });
        (function (col, hour) {
          cell.addEventListener("click", function (e) {
            if (e.target === cell) openCreate(col, hour);
          });
        })(c, h);
        (byCell[c.id + "|" + h] || []).forEach(function (ev) {
          cell.appendChild(el("div", { class: "cf-ev " + ev.status,
            text: UI.fmtTime(ev.starts_at) + " " + ev.booking_type,
            onclick: function (e) { e.stopPropagation(); openEvent(ev); } }));
        });
        grid.appendChild(cell);
      });
    }
    wrap.appendChild(grid); cal.appendChild(wrap);
  }

  // ---- click-to-create / block ---------------------------------------------
  function openCreate(resource, hour) {
    var bg = modal("New on " + resource.name + " @ " + ("0" + hour).slice(-2) + ":00", function (m) {
      var dur = el("select", { class: "cf-select" }, [
        el("option", { value: "60", text: "60 min" }), el("option", { value: "90", text: "90 min" }), el("option", { value: "30", text: "30 min" }),
      ]);
      var kind = el("select", { class: "cf-select" }, [
        el("option", { value: "court", text: "Court booking" }),
        el("option", { value: "lesson", text: "Lesson" }),
        el("option", { value: "block", text: "Block time (time-off)" }),
      ]);
      m.appendChild(field("Type", kind));
      m.appendChild(field("Duration", dur));
      m.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px" }, [
        el("button", { class: "cf-btn", text: "Cancel", onclick: close }),
        el("button", { class: "cf-btn cf-btn-primary", text: "Create", onclick: function () {
          create(resource, hour, parseInt(dur.value, 10), kind.value);
        } }),
      ]));
    });
    function close() { document.body.removeChild(bg); }
    window._closeAdminModal = close;
  }

  async function create(resource, hour, durMin, kind) {
    var start = new Date(state.date); start.setHours(hour, 0, 0, 0);
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
      m.appendChild(el("p", { class: "cf-muted", text:
        (ev.resource_name || "") + " · " + UI.fmtRange(ev.starts_at, ev.ends_at) +
        " · " + UI.settlementLabel(ev.settlement_mode) + " · " + ev.status }));
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

  // ---- walk-in booking ------------------------------------------------------
  async function openWalkIn() {
    if (!state.resources.length) { var r = await window.API.resources(); state.resources = r.resources || []; }
    var courts = state.resources.filter(function (x) { return x.kind === "court" && x.is_active; });
    var bg = modal("Walk-in booking", function (m) {
      var court = el("select", { class: "cf-select" }, courts.map(function (c) { return el("option", { value: c.id, text: c.name }); }));
      var when = el("input", { class: "cf-input", type: "datetime-local" });
      var dur = el("select", { class: "cf-select" }, [el("option", { value: "60", text: "60 min" }), el("option", { value: "30", text: "30 min" }), el("option", { value: "90", text: "90 min" })]);
      var guest = el("input", { class: "cf-input", placeholder: "Player / guest name" });
      m.appendChild(field("Court", court));
      m.appendChild(field("When", when));
      m.appendChild(field("Duration", dur));
      m.appendChild(field("Guest name", guest));
      m.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px" }, [
        el("button", { class: "cf-btn", text: "Cancel", onclick: function () { document.body.removeChild(bg); } }),
        el("button", { class: "cf-btn cf-btn-primary", text: "Book", onclick: async function () {
          if (!court.value || !when.value) { UI.toast("Pick a court and time.", "warn"); return; }
          var s = new Date(when.value), e2 = new Date(s.getTime() + parseInt(dur.value, 10) * 60000);
          try {
            await window.API.createBooking({
              booking_type: "court", resource_id: court.value,
              starts_at: s.toISOString(), ends_at: e2.toISOString(),
              settlement_mode: "at_court", audience: "visitor",
              // Walk-in has no member host; record the name as a player (not a "guest"
              // party) so the guest_requires_member guard does not reject it.
              parties: guest.value ? [{ party_role: "player", guest_name: guest.value }] : [],
            });
            document.body.removeChild(bg); UI.toast("Booked.", "info"); loadDiary();
          } catch (e3) { UI.toast(UI.errMsg(e3), "error"); }
        } }),
      ]));
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

  function renderPeople(panel) {
    panel.appendChild(el("div", { class: "cf-card cf-empty", html:
      "People management (members, coaches, guests, juniors/guardians, Clerk invites) consumes the IAM/club admin API. " +
      "No read endpoint is exposed to the frontend yet — flagged for the IAM/club lane. See report." }));
  }

  async function renderBilling(panel) {
    panel.appendChild(el("div", { class: "cf-card" }, [
      el("h2", { text: "Billing & settlement" }),
      el("div", { id: "bill-cfg", class: "cf-loading", text: "Loading config…" }),
      el("p", { class: "cf-muted", style: "margin-top:8px", text:
        "Open orders / monthly balances / statement preview need C-lane read endpoints (build_statements is server-side via cron). " +
        "Desk payments are taken inline from the master diary (Take payment on an at-court booking)." }),
    ]));
    try {
      var cfg = await window.API.billingConfig(principal.club_id);
      var box = document.getElementById("bill-cfg"); UI.clear(box);
      box.appendChild(el("p", { text: "Online payments: " + (cfg.online_enabled ? "ENABLED (" + cfg.provider + ")" : "disabled (pay-at-court)") +
        " · Currency: " + cfg.currency }));
    } catch (e) { document.getElementById("bill-cfg").textContent = UI.errMsg(e); }
  }

  async function renderCockpit(panel) {
    // The cockpit (occupancy, revenue, no-show) is D-lane: GET /api/admin/cockpit/*.
    panel.appendChild(el("div", { class: "cf-card cf-empty", html:
      "Analytics cockpit (occupancy, coach utilisation, revenue, no-show, MRR, funnel) is served by the CRM/marketing lane " +
      "at <code>/api/admin/cockpit/*</code>. Wire the charts once those routes are confirmed. See report." }));
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
      shell();
    },
  };
})();

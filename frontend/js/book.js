// book.js — member booking wizard (docs/03 §9, docs/05 §5).
// Flow: type (court / lesson / class) -> pick resource/coach/class -> slot
//       -> parties (member-guest) -> settlement -> confirm.
// Calls GET /api/diary/availability + GET /api/diary/resources + GET /api/diary/classes,
// then POST /api/diary/bookings (court/lesson) or POST /api/diary/classes/:id/enrol (class).
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
    selResource: null,       // chosen resource (court or coach)
    selClass: null,          // chosen class session
    courtForLesson: null,    // optional court for a lesson
    slot: null,              // {start,end,resource_id,resource_name,kind,price}
    guest: null,             // {name,email} optional member-guest
    settlement: "at_court",
  };

  // ---- step rendering -------------------------------------------------------
  function steps(active) {
    var labels = ["Type", "Choose", "Slot", "Settle", "Confirm"];
    var wrap = el("div", { class: "cf-steps" });
    labels.forEach(function (l, i) {
      var s = el("div", { class: "cf-step" + (i === active ? " on" : "") }, [
        el("span", { class: "n", text: String(i + 1) }), el("span", { text: l }),
      ]);
      wrap.appendChild(s);
    });
    return wrap;
  }
  function host() { return document.getElementById("cf-wizard"); }
  function render(active, body) {
    var h = host(); UI.clear(h);
    h.appendChild(steps(active));
    h.appendChild(body);
  }

  // ---- Step 1: type ---------------------------------------------------------
  function stepType() {
    var tiles = el("div", { class: "cf-tiles" });
    [
      { k: "court", t: "Court", s: "Book a court" },
      { k: "lesson", t: "Lesson", s: "Book a named coach" },
      { k: "class", t: "Class", s: "Cardio, juniors, socials" },
    ].forEach(function (o) {
      tiles.appendChild(el("div", {
        class: "cf-tile" + (state.type === o.k ? " sel" : ""),
        onclick: function () { state.type = o.k; state.slot = null; state.selResource = null; state.selClass = null; stepChoose(); },
      }, [ el("div", { class: "cf-tile-t", text: o.t }), el("div", { class: "cf-tile-s", text: o.s }) ]));
    });
    render(0, el("div", { class: "cf-card" }, [ el("h2", { text: "What would you like to book?" }), tiles ]));
  }

  // ---- Step 2: choose resource / coach / class ------------------------------
  async function stepChoose() {
    var card = el("div", { class: "cf-card" }, [ el("h2", { text: "Choose" }), el("div", { id: "choose", class: "cf-loading", text: "Loading…" }) ]);
    render(1, card);
    try {
      if (state.type === "class") {
        var today = UI.dateKey(new Date());
        var to = UI.dateKey(UI.addDays(new Date(), 21));
        var r = await window.API.classes({ date_from: today, date_to: to });
        renderClassList(r.classes || []);
        return;
      }
      if (!state.resources.length) {
        var rr = await window.API.resources();
        state.resources = rr.resources || [];
        state.courts = state.resources.filter(function (x) { return x.kind === "court"; });
        state.coaches = state.resources.filter(function (x) { return x.kind === "coach"; });
      }
      renderResourcePicker();
    } catch (e) { document.getElementById("choose").textContent = UI.errMsg(e); }
  }

  function renderResourcePicker() {
    var box = document.getElementById("choose"); UI.clear(box);
    var pool = state.type === "lesson" ? state.coaches : state.courts;
    if (!pool.length) { box.appendChild(el("div", { class: "cf-empty", text: "No " + (state.type === "lesson" ? "coaches" : "courts") + " available." })); return; }
    box.appendChild(el("p", { class: "cf-muted", text: state.type === "lesson" ? "Pick your coach:" : "Pick a court (or any):" }));
    var tiles = el("div", { class: "cf-tiles" });
    if (state.type === "court") {
      tiles.appendChild(el("div", {
        class: "cf-tile" + (state.selResource === "ANY" ? " sel" : ""),
        onclick: function () { state.selResource = "ANY"; afterResource(); },
      }, [ el("div", { class: "cf-tile-t", text: "Any court" }), el("div", { class: "cf-tile-s", text: "First free" }) ]));
    }
    pool.forEach(function (res) {
      tiles.appendChild(el("div", {
        class: "cf-tile" + (state.selResource && state.selResource.id === res.id ? " sel" : ""),
        onclick: function () { state.selResource = res; afterResource(); },
      }, [ el("div", { class: "cf-tile-t", text: res.name }), el("div", { class: "cf-tile-s", text: res.surface || res.kind }) ]));
    });
    box.appendChild(tiles);
    var b = el("div", { class: "cf-row", style: "margin-top:12px" }, [
      el("button", { class: "cf-btn", text: "← Back", onclick: stepType }),
    ]);
    box.appendChild(b);
  }

  function afterResource() {
    renderResourcePicker(); // reflect selection
    stepSlot();
  }

  function renderClassList(classes) {
    var box = document.getElementById("choose"); UI.clear(box);
    if (!classes.length) { box.appendChild(el("div", { class: "cf-empty", text: "No upcoming classes." })); return; }
    var list = el("div", { class: "cf-list" });
    classes.forEach(function (c) {
      var full = c.spots_left === 0;
      list.appendChild(el("div", { class: "cf-item" }, [
        el("span", { class: "cf-chip class", text: "class" }),
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: c.class_name || "Class" }),
          el("div", { class: "cf-item-s", text: UI.fmtRange(c.starts_at, c.ends_at) +
            (c.spots_left != null ? (full ? " · Full (join waitlist)" : " · " + c.spots_left + " spots left") : "") }),
        ]),
        el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: full ? "Waitlist" : "Enrol",
          onclick: function () { state.selClass = c; stepSettle(); } }),
      ]));
    });
    box.appendChild(list);
    box.appendChild(el("button", { class: "cf-btn", style: "margin-top:12px", text: "← Back", onclick: stepType }));
  }

  // ---- Step 3: slot ---------------------------------------------------------
  async function stepSlot() {
    var card = el("div", { class: "cf-card" }, [ el("h2", { text: "Pick a time" }), el("div", { id: "slots", class: "cf-loading", text: "Finding slots…" }) ]);
    render(2, card);
    try {
      var q = {
        date_from: UI.dateKey(new Date()),
        date_to: UI.dateKey(UI.addDays(new Date(), 14)),
        audience: "member",
      };
      if (state.type === "lesson") { q.kind = "coach"; if (state.selResource && state.selResource.id) q.coach_id = state.selResource.id; }
      else if (state.type === "court") {
        q.kind = "court";
        if (state.selResource === "ANY") q.any = "1";
        else if (state.selResource && state.selResource.id) q.resource_id = state.selResource.id;
      }
      var r = await window.API.availability(q);
      renderSlots(r.slots || []);
    } catch (e) { document.getElementById("slots").textContent = UI.errMsg(e); }
  }

  function renderSlots(slots) {
    var box = document.getElementById("slots"); UI.clear(box);
    if (!slots.length) { box.appendChild(el("div", { class: "cf-empty", text: "No free slots in the next 2 weeks. Try another court or coach." })); }
    var by = UI.groupByDay(slots);
    Object.keys(by).sort().forEach(function (day) {
      box.appendChild(el("div", { class: "cf-day-h", text: UI.fmtDate(by[day][0].start) }));
      var row = el("div", { class: "cf-slots" });
      by[day].forEach(function (sl) {
        row.appendChild(el("button", {
          class: "cf-slot" + (state.slot && state.slot.start === sl.start && state.slot.resource_id === sl.resource_id ? " sel" : ""),
          text: UI.fmtTime(sl.start) + (state.selResource === "ANY" && sl.resource_name ? " · " + sl.resource_name : ""),
          onclick: function () { state.slot = sl; stepSettle(); },
        }));
      });
      box.appendChild(row);
    });
    box.appendChild(el("button", { class: "cf-btn", style: "margin-top:12px", text: "← Back", onclick: stepChoose }));
  }

  // ---- Step 4: parties + settlement ----------------------------------------
  function allowedModes() {
    // club.policy may restrict; default at launch is at_court + monthly_account +
    // membership_covered. online is offered only if billing config enabled.
    var allow = (state.policy && state.policy.allowed_settlement_modes) ||
                ["at_court", "monthly_account", "membership_covered"];
    var modes = allow.slice();
    if (state.billing.online_enabled && modes.indexOf("online") < 0) modes.push("online");
    return modes.filter(function (m) { return UI.SETTLEMENT[m]; });
  }

  function stepSettle() {
    var card = el("div", { class: "cf-card" });
    card.appendChild(el("h2", { text: "Details" }));

    // member-guest (only for court/lesson, not class enrolment of self)
    if (state.type !== "class") {
      card.appendChild(el("h3", { text: "Playing with a guest? (optional)" }));
      var gName = el("input", { class: "cf-input", placeholder: "Guest name", value: (state.guest && state.guest.name) || "" });
      var gEmail = el("input", { class: "cf-input", type: "email", placeholder: "Guest email (optional)", value: (state.guest && state.guest.email) || "" });
      card.appendChild(el("div", { class: "cf-grid cf-grid-2" }, [
        el("div", { class: "cf-field" }, [ el("label", { text: "Guest name" }), gName ]),
        el("div", { class: "cf-field" }, [ el("label", { text: "Guest email" }), gEmail ]),
      ]));
      state._gName = gName; state._gEmail = gEmail;
    }

    // settlement mode
    card.appendChild(el("h3", { text: "How would you like to settle?" }));
    var modes = allowedModes();
    if (modes.indexOf(state.settlement) < 0) state.settlement = modes[0] || "at_court";
    var tiles = el("div", { class: "cf-tiles" });
    modes.forEach(function (m) {
      var meta = UI.SETTLEMENT[m];
      tiles.appendChild(el("div", {
        class: "cf-tile" + (state.settlement === m ? " sel" : ""),
        onclick: function () { state.settlement = m; stepSettle(); },
      }, [ el("div", { class: "cf-tile-t", text: meta.label }), el("div", { class: "cf-tile-s", text: meta.hint }) ]));
    });
    card.appendChild(tiles);

    if (state.settlement === "online" && state.billing.online_enabled) {
      card.appendChild(el("p", { class: "cf-muted", style: "margin-top:8px",
        text: "Online payment (" + state.billing.provider + ") opens at confirmation. The booking is held until paid." }));
    }

    card.appendChild(el("div", { class: "cf-row", style: "margin-top:14px" }, [
      el("button", { class: "cf-btn", text: "← Back", onclick: state.type === "class" ? stepChoose : stepSlot }),
      el("button", { class: "cf-btn cf-btn-primary", text: "Review →", onclick: function () {
        if (state.type !== "class") {
          var n = state._gName.value.trim(), em = state._gEmail.value.trim();
          state.guest = n ? { name: n, email: em } : null;
        }
        stepConfirm();
      } }),
    ]));
    render(3, card);
  }

  // ---- Step 5: confirm ------------------------------------------------------
  function summaryRows() {
    var rows = [];
    rows.push(["Type", state.type]);
    if (state.type === "class") {
      rows.push(["Class", state.selClass.class_name || "Class"]);
      rows.push(["When", UI.fmtRange(state.selClass.starts_at, state.selClass.ends_at)]);
    } else {
      rows.push([state.type === "lesson" ? "Coach" : "Court",
        state.selResource === "ANY" ? (state.slot && state.slot.resource_name || "Any") :
          (state.selResource && state.selResource.name) || (state.slot && state.slot.resource_name)]);
      if (state.slot) rows.push(["When", UI.fmtRange(state.slot.start, state.slot.end)]);
      if (state.slot && state.slot.price != null) rows.push(["Price", UI.money(state.slot.price, state.billing.currency)]);
      if (state.guest) rows.push(["Guest", state.guest.name]);
    }
    rows.push(["Settlement", UI.settlementLabel(state.settlement)]);
    return rows;
  }

  function stepConfirm() {
    var card = el("div", { class: "cf-card" });
    card.appendChild(el("h2", { text: "Confirm booking" }));
    var t = el("table", { class: "cf-table" });
    summaryRows().forEach(function (r) {
      t.appendChild(el("tr", {}, [ el("th", { text: r[0] }), el("td", { text: r[1] == null ? "" : String(r[1]) }) ]));
    });
    card.appendChild(t);
    var btn = el("button", { class: "cf-btn cf-btn-primary", text: "Confirm booking" });
    btn.addEventListener("click", function () { submit(btn); });
    card.appendChild(el("div", { class: "cf-row", style: "margin-top:14px" }, [
      el("button", { class: "cf-btn", text: "← Back", onclick: stepSettle }), btn,
    ]));
    render(4, card);
  }

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
          body.coach_user_id = (state.selResource && state.selResource.coach_user_id) || null;
          body.resource_id = state.slot.resource_id; // the coach resource slot
        } else {
          body.resource_id = state.slot.resource_id; // the actual court resolved by availability
        }
        res = await window.API.createBooking(body);
        // online mode returns a checkout intent (Phase 4) — surface it.
        if (res.checkout && res.checkout.redirect_url) {
          location.href = res.checkout.redirect_url; return;
        }
        success(state.type, res);
      }
    } catch (e) {
      btn.disabled = false; btn.textContent = "Confirm booking";
      UI.toast(UI.errMsg(e), "error");
    }
  }

  function success(kind, res) {
    var h = host(); UI.clear(h);
    var msg = kind === "class"
      ? (res.status === "waitlisted" ? "You're on the waitlist — we'll email you if a spot opens."
                                     : "You're enrolled! A confirmation email is on its way.")
      : "Booking " + (res.status === "held" ? "held (awaiting payment)" : "confirmed") + "! A confirmation email is on its way.";
    h.appendChild(el("div", { class: "cf-card" }, [
      el("h2", { text: "✓ Done" }),
      el("p", { text: msg }),
      el("p", { class: "cf-muted", style: "margin-top:6px", text: "We send confirmations via email (Klaviyo)." }),
      el("div", { class: "cf-row", style: "margin-top:14px" }, [
        el("a", { class: "cf-btn cf-btn-primary", href: "/my.html", text: "View my bookings" }),
        el("button", { class: "cf-btn", text: "Book another", onclick: function () {
          state.type = null; state.slot = null; state.selResource = null; state.selClass = null; state.guest = null; stepType();
        } }),
      ]),
    ]));
  }

  // ---- boot -----------------------------------------------------------------
  window.BookWizard = {
    start: async function (principal) {
      UI = window.UI; el = UI.el;
      state.principal = principal;
      try { state.billing = await window.API.billingConfig(principal.club_id); } catch (e) {}
      // policy: pulled from principal/club if exposed; otherwise defaults apply.
      state.policy = principal.policy || null;
      stepType();
    },
  };
})();

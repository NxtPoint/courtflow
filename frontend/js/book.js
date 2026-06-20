// book.js — member booking wizard (docs/03 §9, docs/05 §5).
// Flow: type (court / lesson / class) -> pick resource/coach/class -> slot
//       -> confirm (settlement + parties folded in for fewer taps).
// Calls GET /api/diary/availability + GET /api/diary/resources + GET /api/diary/classes,
// then POST /api/diary/bookings (court/lesson) or POST /api/diary/classes/:id/enrol (class).
//
// UX goal: Playtomic-class. Court bookable in ~3 taps (court/any -> time -> Confirm).
// The slot step is a horizontal date strip + a clean time grid fetched per-day.
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
    selResource: null,       // chosen resource (court or coach) or "ANY"
    selClass: null,          // chosen class session
    addCourt: false,         // lesson: "also book a court" toggle (presentation hint)
    day: null,               // Date — chosen day in the date strip
    slotsCache: {},          // dateKey -> slots[] (per-day availability cache)
    slot: null,              // {start,end,resource_id,resource_name,kind,price}
    guest: null,             // {name,email} optional member-guest
    settlement: "at_court",
  };

  // ---- step rendering -------------------------------------------------------
  function steps(active) {
    var labels = ["Type", "Choose", "Time", "Confirm"];
    var wrap = el("div", { class: "cf-steps" });
    labels.forEach(function (l, i) {
      var done = i < active;
      var s = el("div", {
        class: "cf-step" + (i === active ? " on" : "") + (done ? " done" : ""),
        onclick: function () { goToStep(i); },
      }, [ el("span", { class: "n", text: done ? "✓" : String(i + 1) }), el("span", { text: l }) ]);
      wrap.appendChild(s);
    });
    return wrap;
  }
  function goToStep(i) {
    // Allow jumping back to a completed step; never skip forward.
    if (i === 0) return stepType();
    if (i === 1 && state.type) return stepChoose();
    if (i === 2 && (state.selResource || state.selClass)) {
      if (state.type === "class") return stepConfirm();
      return stepSlot();
    }
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
      { k: "court", t: "Court", s: "Book a court · ~3 taps", icon: "🎾" },
      { k: "lesson", t: "Lesson", s: "Book a named coach", icon: "🏆" },
      { k: "class", t: "Class", s: "Cardio, juniors, socials", icon: "👥" },
    ].forEach(function (o) {
      tiles.appendChild(el("div", {
        class: "cf-tile cf-tile-tap" + (state.type === o.k ? " sel" : ""),
        onclick: function () {
          state.type = o.k;
          state.slot = null; state.selResource = null; state.selClass = null;
          state.slotsCache = {}; state.day = null; state.addCourt = false;
          stepChoose();
        },
      }, [
        el("div", { class: "cf-tile-icon", text: o.icon }),
        el("div", {}, [
          el("div", { class: "cf-tile-t", text: o.t }),
          el("div", { class: "cf-tile-s", text: o.s }),
        ]),
      ]));
    });
    render(0, el("div", { class: "cf-card" }, [
      el("h2", { text: "What would you like to book?" }), tiles,
    ]));
  }

  // ---- Step 2: choose resource / coach / class ------------------------------
  async function stepChoose() {
    var title = state.type === "lesson" ? "Choose your coach"
              : state.type === "class" ? "Upcoming classes" : "Choose a court";
    var card = el("div", { class: "cf-card" }, [
      el("h2", { text: title }),
      el("div", { id: "choose", class: "cf-loading", text: "Loading…" }),
    ]);
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
      if (state.type === "lesson") renderCoachPicker();
      else renderCourtPicker();
    } catch (e) { var b = document.getElementById("choose"); if (b) b.textContent = UI.errMsg(e); }
  }

  function renderCourtPicker() {
    var box = document.getElementById("choose"); UI.clear(box);
    if (!state.courts.length) {
      box.appendChild(el("div", { class: "cf-empty", text: "No courts available." }));
      box.appendChild(backRow(stepType));
      return;
    }
    box.appendChild(el("p", { class: "cf-muted", text: "Pick a court — or let us find the first one free." }));
    var tiles = el("div", { class: "cf-tiles" });
    // "Any available court" — the fast path (smart default for ~3-tap booking).
    tiles.appendChild(el("div", {
      class: "cf-tile cf-tile-tap cf-tile-any" + (state.selResource === "ANY" ? " sel" : ""),
      onclick: function () { state.selResource = "ANY"; afterResource(); },
    }, [
      el("div", { class: "cf-tile-icon", text: "⚡" }),
      el("div", {}, [
        el("div", { class: "cf-tile-t", text: "Any available court" }),
        el("div", { class: "cf-tile-s", text: "Fastest — we pick the first free court" }),
      ]),
    ]));
    state.courts.forEach(function (res) {
      tiles.appendChild(el("div", {
        class: "cf-tile cf-tile-tap" + (state.selResource && state.selResource.id === res.id ? " sel" : ""),
        onclick: function () { state.selResource = res; afterResource(); },
      }, [
        el("div", { class: "cf-tile-icon", text: "🎾" }),
        el("div", {}, [
          el("div", { class: "cf-tile-t", text: res.name }),
          el("div", { class: "cf-tile-s", text: res.surface || res.kind }),
        ]),
      ]));
    });
    box.appendChild(tiles);
    box.appendChild(backRow(stepType));
  }

  function renderCoachPicker() {
    var box = document.getElementById("choose"); UI.clear(box);
    if (!state.coaches.length) {
      box.appendChild(el("div", { class: "cf-empty", text: "No coaches available." }));
      box.appendChild(backRow(stepType));
      return;
    }
    box.appendChild(el("p", { class: "cf-muted", text: "Pick your coach:" }));
    var list = el("div", { class: "cf-coachgrid" });
    state.coaches.forEach(function (res) {
      var headline = res.headline || res.specialties || res.bio || "Tennis coach";
      var rate = res.rate_minor != null ? UI.money(res.rate_minor, state.billing.currency) + " / hr"
               : (res.price_minor != null ? UI.money(res.price_minor, state.billing.currency) + " / hr" : null);
      var initials = (res.name || "?").split(/\s+/).map(function (w) { return w[0]; }).join("").slice(0, 2).toUpperCase();
      var card = el("div", {
        class: "cf-coach" + (state.selResource && state.selResource.id === res.id ? " sel" : ""),
        onclick: function () { state.selResource = res; afterResource(); },
      }, [
        el("div", { class: "cf-coach-av", text: initials }),
        el("div", { class: "cf-coach-main" }, [
          el("div", { class: "cf-coach-name", text: res.name }),
          el("div", { class: "cf-coach-head", text: headline }),
          rate ? el("div", { class: "cf-coach-rate", text: rate }) : null,
        ]),
      ]);
      list.appendChild(card);
    });
    box.appendChild(list);
    box.appendChild(backRow(stepType));
  }

  function afterResource() {
    // Default the day to today and jump straight into the time grid.
    state.day = state.day || new Date();
    state.slot = null; state.slotsCache = {};
    stepSlot();
  }

  function renderClassList(classes) {
    var box = document.getElementById("choose"); UI.clear(box);
    if (!classes.length) {
      box.appendChild(el("div", { class: "cf-empty", text: "No upcoming classes." }));
      box.appendChild(backRow(stepType));
      return;
    }
    var list = el("div", { class: "cf-list" });
    classes.forEach(function (c) {
      var full = c.spots_left === 0;
      var priceMinor = c.price_minor != null ? c.price_minor : c.price;
      var price = priceMinor != null ? UI.money(priceMinor, state.billing.currency) : null;
      var sub = UI.fmtRange(c.starts_at, c.ends_at);
      if (c.spots_left != null) sub += full ? " · Full" : " · " + c.spots_left + " spots left";
      if (price) sub += " · " + price;
      list.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () {
        state.selClass = c; stepConfirm();
      } }, [
        el("span", { class: "cf-chip class", text: "class" }),
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: c.class_name || "Class" }),
          el("div", { class: "cf-item-s", text: sub }),
        ]),
        el("button", {
          class: "cf-btn cf-btn-primary cf-btn-sm", text: full ? "Waitlist" : "Enrol",
          onclick: function (ev) { ev.stopPropagation(); state.selClass = c; stepConfirm(); },
        }),
      ]));
    });
    box.appendChild(list);
    box.appendChild(backRow(stepType));
  }

  // ---- Step 3: slot — date strip + time grid (per-day, instant feel) --------
  function dateLabel(d) {
    var t0 = UI.dateKey(new Date()), t1 = UI.dateKey(UI.addDays(new Date(), 1));
    var k = UI.dateKey(d);
    if (k === t0) return "Today";
    if (k === t1) return "Tomorrow";
    return d.toLocaleDateString("en-ZA", { weekday: "short", timeZone: UI.CLUB_TZ });
  }

  function dateStrip() {
    var strip = el("div", { class: "cf-datestrip" });
    for (var i = 0; i < 14; i++) {
      var d = UI.addDays(new Date(), i);
      (function (d) {
        var on = state.day && UI.dateKey(state.day) === UI.dateKey(d);
        strip.appendChild(el("div", {
          class: "cf-date" + (on ? " sel" : ""),
          onclick: function () { state.day = d; stepSlot(); },
        }, [
          el("span", { class: "cf-date-dow", text: dateLabel(d) }),
          el("span", { class: "cf-date-num", text: String(d.getDate()) }),
          el("span", { class: "cf-date-mon", text: d.toLocaleDateString("en-ZA", { month: "short", timeZone: UI.CLUB_TZ }) }),
        ]));
      })(d);
    }
    return strip;
  }

  async function stepSlot() {
    state.day = state.day || new Date();
    var who = state.type === "lesson"
      ? "with " + ((state.selResource && state.selResource.name) || "your coach")
      : (state.selResource === "ANY" ? "any available court"
         : (state.selResource && state.selResource.name) || "your court");

    var card = el("div", { class: "cf-card" });
    card.appendChild(el("h2", { text: "Pick a time" }));
    card.appendChild(el("p", { class: "cf-muted cf-slot-sub", text: who }));
    card.appendChild(dateStrip());

    // Lesson: optional "also book a court" toggle (presentation hint only).
    if (state.type === "lesson") {
      var toggle = el("label", { class: "cf-toggle" }, [
        el("input", { type: "checkbox", checked: state.addCourt ? "checked" : null,
          onchange: function (ev) { state.addCourt = !!ev.target.checked; } }),
        el("span", { text: "Also book a court for this lesson" }),
      ]);
      card.appendChild(toggle);
    }

    var grid = el("div", { id: "slots", class: "cf-loading", text: "Finding slots…" });
    card.appendChild(grid);
    card.appendChild(backRow(stepChoose));
    render(2, card);

    var dk = UI.dateKey(state.day);
    if (state.slotsCache[dk]) { renderSlots(state.slotsCache[dk]); return; }
    try {
      var q = {
        date_from: dk,
        date_to: dk,
        audience: "member",
      };
      if (state.type === "lesson") { q.kind = "coach"; if (state.selResource && state.selResource.id) q.coach_id = state.selResource.id; }
      else if (state.type === "court") {
        q.kind = "court";
        if (state.selResource === "ANY") q.any = "1";
        else if (state.selResource && state.selResource.id) q.resource_id = state.selResource.id;
      }
      var r = await window.API.availability(q);
      state.slotsCache[dk] = r.slots || [];
      // Guard against a stale response if the user tapped another day meanwhile.
      if (UI.dateKey(state.day) === dk) renderSlots(state.slotsCache[dk]);
    } catch (e) { var b = document.getElementById("slots"); if (b) { b.className = ""; b.textContent = UI.errMsg(e); } }
  }

  function renderSlots(slots) {
    var box = document.getElementById("slots"); if (!box) return;
    box.className = ""; UI.clear(box);
    if (!slots.length) {
      box.appendChild(el("div", { class: "cf-empty", text: "No free times on this day. Try another day above" +
        (state.type === "court" ? " or 'Any available court'." : ".") }));
      return;
    }
    var grid = el("div", { class: "cf-slots" });
    slots.forEach(function (sl) {
      var on = state.slot && state.slot.start === sl.start && state.slot.resource_id === sl.resource_id;
      var kids = [ document.createTextNode(UI.fmtTime(sl.start)) ];
      var sub = [];
      if (state.selResource === "ANY" && sl.resource_name) sub.push(sl.resource_name);
      if (sl.price != null) sub.push(UI.money(sl.price, state.billing.currency));
      if (sub.length) kids.push(el("small", { text: sub.join(" · ") }));
      grid.appendChild(el("button", {
        class: "cf-slot" + (on ? " sel" : ""),
        onclick: function () { state.slot = sl; stepConfirm(); },
      }, kids));
    });
    box.appendChild(grid);
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

  // ---- Step 4: confirm (summary + settlement + guest, one screen) -----------
  function stepConfirm() {
    captureGuest(); // preserve typed guest details across settlement re-renders
    var modes = allowedModes();
    if (modes.indexOf(state.settlement) < 0) state.settlement = modes[0] || "at_court"; // smart default

    var card = el("div", { class: "cf-card" });
    card.appendChild(el("h2", { text: "Confirm booking" }));

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

    // --- settlement (chips, smart default pre-selected) ---
    card.appendChild(el("div", { class: "cf-confirm-sec" }, [
      el("h3", { text: "How would you like to settle?" }),
      settlementChips(modes),
    ]));

    if (state.settlement === "online" && state.billing.online_enabled) {
      card.appendChild(el("p", { class: "cf-muted cf-tiny", style: "margin-top:6px",
        text: "Online payment (" + state.billing.provider + ") opens at confirmation. The booking is held until paid." }));
    }

    // --- prominent confirm CTA ---
    var btn = el("button", { class: "cf-btn cf-btn-primary cf-btn-block cf-btn-lg", text: confirmLabel() });
    btn.addEventListener("click", function () { submit(btn); });
    card.appendChild(el("div", { style: "margin-top:16px" }, [ btn ]));
    var backTo = state.type === "class" ? stepChoose : stepSlot;
    card.appendChild(el("button", { class: "cf-btn cf-btn-ghost cf-btn-block", style: "margin-top:8px",
      text: "← Back", onclick: backTo }));

    render(3, card);
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
      rows.push([state.type === "lesson" ? "Coach" : "Court",
        state.selResource === "ANY" ? (state.slot && state.slot.resource_name || "Any available court") :
          (state.selResource && state.selResource.name) || (state.slot && state.slot.resource_name)]);
      if (state.slot) rows.push(["When", UI.fmtRange(state.slot.start, state.slot.end)]);
      if (state.type === "lesson" && state.addCourt) rows.push(["Court", "Also requested"]);
      if (state.slot && state.slot.price != null) rows.push(["Price", UI.money(state.slot.price, state.billing.currency)]);
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

  function settlementChips(modes) {
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
      btn.disabled = false; btn.textContent = confirmLabel();
      // Surface a just-taken slot gracefully and bounce back to the time grid.
      var code = (e && e.body && e.body.error) || "";
      if (e && (e.status === 409 || code === "SLOT_TAKEN")) {
        UI.toast("That slot was just taken — pick another.", "error");
        if (state.type !== "class") {
          state.slot = null;
          if (state.day) delete state.slotsCache[UI.dateKey(state.day)]; // force a fresh fetch
          stepSlot();
        }
        return;
      }
      UI.toast(UI.errMsg(e), "error");
    }
  }

  // ---- slick success state --------------------------------------------------
  function success(kind, res) {
    var h = host(); UI.clear(h);
    var title, msg;
    if (kind === "class") {
      if (res.status === "waitlisted") { title = "You're on the waitlist"; msg = "We'll email you the moment a spot opens."; }
      else { title = "You're enrolled!"; msg = "A confirmation email is on its way."; }
    } else if (res.status === "held") {
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
          state.type = null; state.slot = null; state.selResource = null; state.selClass = null;
          state.guest = null; state.day = null; state.slotsCache = {}; state.addCourt = false;
          stepType();
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
      rows.push([state.type === "lesson" ? "Coach" : "Court",
        state.selResource === "ANY" ? (state.slot && state.slot.resource_name || "Any available court") :
          (state.selResource && state.selResource.name) || (state.slot && state.slot.resource_name)]);
      if (state.slot) rows.push(["When", UI.fmtRange(state.slot.start, state.slot.end)]);
      if (state.guest) rows.push(["Guest", state.guest.name]);
    }
    rows.push(["Settlement", UI.settlementLabel(state.settlement)]);
    return rows;
  }

  // ---- small shared bits ----------------------------------------------------
  function backRow(onBack) {
    return el("div", { class: "cf-row", style: "margin-top:14px" }, [
      el("button", { class: "cf-btn cf-btn-ghost", text: "← Back", onclick: onBack }),
    ]);
  }

  // Capture guest inputs before re-rendering confirm (settlement chip tap) or leaving.
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
      stepType();
    },
  };
})();

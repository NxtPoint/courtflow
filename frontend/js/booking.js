// booking.js — the full-screen member booking flow (client-journey redesign, Phase 1, increment 3.5).
//
// Replaces BOTH the old multi-step wizard (book.js) and the quick-book popup sheet (quickbook.js)
// with ONE full-screen surface, on /book. Best-of-both per the owner's brief:
//   • a month CALENDAR to pick the day (today preselected, so the common path stays ~2 taps),
//   • DURATION chosen INLINE as chips right above the time grid (never its own screen) — changing
//     duration re-prices the times live,
//   • a service switch (Court / Lesson / Class) so you never bounce back to the dashboard,
//   • a clean Confirm view (summary + Who's-playing + optional guest + payment, auto-skipped free).
//
// The booking/money path is UNCHANGED — same APIs, same online seam, server is the source of truth:
//   durations (lesson coach_id=coach_user_id), availability (kind=coach, coach_id=resource id),
//   classes, createBooking/enrol, res.order_id||res.booking.order_id → Pay.startYocoCheckout,
//   covered-court free-skip, token-wallet auto-apply, dependents/guests, NO_TOKEN / 409 recovery.
(function () {
  var UI, el;

  var ctx = {
    loaded: false, principal: null,
    billing: { online_enabled: false, currency: "ZAR", provider: "manual" },
    dependents: [], walletsByKind: {}, plan: null, coaches: [], courts: [], policy: null,
  };
  var st = null;
  var container = null;

  function qs(params) {
    var parts = [];
    Object.keys(params || {}).forEach(function (k) {
      var v = params[k];
      if (v !== undefined && v !== null && v !== "") parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(v));
    });
    return parts.length ? ("?" + parts.join("&")) : "";
  }
  function fetchDurations(q) { return window.TFAuth.apiJSON("/api/diary/durations" + qs(q)); }

  async function ensureCtx(principal, onBehalf) {
    ctx.principal = principal;
    // Reload when the mode changes (a coach app is always on-behalf; the client app never is — but
    // guard anyway so the same module is safe if ever reused within one page).
    if (ctx.loaded && ctx._onBehalf === !!onBehalf) return;
    ctx._onBehalf = !!onBehalf;
    ctx.dependents = []; ctx.walletsByKind = {}; ctx.plan = null;
    try { ctx.billing = await window.API.billingConfig(principal.club_id); } catch (e) {}
    // Dependents / prepaid wallets / membership are the SUBJECT's (self). On-behalf we don't have the
    // client's — token is matched server-side (NO_TOKEN if none) and membership coverage isn't offered.
    if (!onBehalf) {
      try { ctx.dependents = (await window.API.dependents()).dependents || []; } catch (e) {}
      try {
        var wr = await window.TFAuth.apiJSON("/api/billing/bundles/wallets");
        (wr.wallets || []).forEach(function (w) {
          (ctx.walletsByKind[w.service_kind] = ctx.walletsByKind[w.service_kind] || []).push(w);
        });
      } catch (e) {}
      try { ctx.plan = await window.TFAuth.apiJSON("/api/me/plan"); } catch (e) { ctx.plan = null; }
    }
    try {
      var rs = (await window.API.resources()).resources || [];
      // Only offer coaches who can actually be booked: active, with weekly hours set
      // (has_hours), and accepting bookings (is_bookable). A coach missing any of these has no
      // bookable availability, so we never present them. Absent flags → keep (backward-safe).
      ctx.coaches = rs.filter(function (r) {
        return r.kind === "coach" && r.is_active && r.has_hours !== false && r.is_bookable !== false;
      });
      ctx.courts = rs.filter(function (r) { return r.kind === "court" && r.is_active; });
    } catch (e) {}
    ctx.policy = (principal && principal.policy) || null;
    ctx.loaded = true;
  }

  // ---- coverage / settlement helpers (identical to the proven booking logic) -
  function bookingServiceKind() { return st.type === "lesson" ? "lesson" : (st.type === "class" ? "class" : "court"); }
  function membershipWindow() { return ctx.plan && ctx.plan.membership_window; }
  function withinWindow(d, w) {
    if (!w || !d) return true;
    var iso = ((d.getDay() + 6) % 7) + 1;
    if (w.days && w.days.length && w.days.indexOf(iso) < 0) return false;
    var mod = d.getHours() * 60 + d.getMinutes();
    if (w.start_min != null && mod < w.start_min) return false;
    if (w.end_min != null && mod >= w.end_min) return false;
    return true;
  }
  function courtCovered() {
    if (st.type !== "court" || !st.membershipCovered) return false;
    // The server already priced the selected slot per its membership window — covered iff it's R0.
    // (Falls back to the client window check only if a slot somehow lacks a price.)
    if (st.slot && st.slot.price != null) return st.slot.price === 0;
    var w = membershipWindow();
    if (!w) return true;
    var start = st.slot && st.slot.start ? new Date(st.slot.start) : null;
    return start ? withinWindow(start, w) : false;
  }
  function coveredLabel() {
    return (ctx.plan && ctx.plan.is_trial) ? "Free this week · R0" : "Covered by your membership · R0";
  }
  function walletMinutesLeft(w) { return (w.minutes_remaining != null) ? w.minutes_remaining : (w.tokens_remaining || 0) * 60; }
  function chosenCoachUserId() {
    if (st.type !== "lesson") return null;
    // On-behalf: the coach books their OWN lessons — always price/schedule/book against that coach id,
    // even if they aren't in the bookable-coaches list (so the rate card + times are always theirs).
    if (st.coachLock) return st.coachLock;
    if (st.selCoach !== "ANY" && st.selCoach && st.selCoach.coach_user_id) return st.selCoach.coach_user_id;
    return (st.slot && st.slot.coach_user_id) || null;
  }
  function matchTokenWallet() {
    var wallets = ctx.walletsByKind[bookingServiceKind()] || [];
    var coach = chosenCoachUserId();
    var hit = wallets.filter(function (w) {
      if (w.status !== "active" || walletMinutesLeft(w) <= 0) return false;
      if (w.coach_user_id != null && coach != null && String(w.coach_user_id) !== String(coach)) return false;
      if (w.coach_user_id != null && coach == null) return false;
      return true;
    }).sort(function (a, b) {
      var ax = a.expires_at || "9999", bx = b.expires_at || "9999";
      return ax < bx ? -1 : (ax > bx ? 1 : walletMinutesLeft(a) - walletMinutesLeft(b));
    })[0] || null;
    st.tokenWallet = hit;
    return hit;
  }
  function walletSessionsLeft(w) {
    var n = (w.sessions_remaining != null) ? w.sessions_remaining : (w.tokens_remaining || 0);
    return Math.round(n * 10) / 10;
  }
  function payModes() {
    // Only offer modes the club's policy actually allows (mirrors the backend _settlement_allowed
    // guard, surfaced via /api/billing/config) so the picker can never present a mode that would be
    // rejected at checkout. membership_covered is never a manual option (auto-applied when free).
    var modes = [];
    if (ctx.billing.allow_at_court !== false) modes.push("at_court");
    if (ctx.billing.allow_monthly !== false) modes.push("monthly_account");
    if (!st.skipOnline && ctx.billing.online_enabled && modes.indexOf("online") < 0) modes.push("online");
    if (st.onBehalf) {
      // Staff override: collect at court / account — no Yoco. An online-only service preference does
      // NOT restrict staff (owner's decision). If the CLIENT holds a pack with this coach, offer it
      // FIRST (default) so we draw their prepaid pack instead of raising a new charge.
      if (onBehalfMatchWallet() && modes.indexOf("token") < 0) modes.unshift("token");
      return modes.filter(function (m) { return m === "token" || UI.SETTLEMENT[m]; });
    }
    // Per-service payment preference: keep only the methods THIS service offers (token = the
    // member's own prepaid pack, always allowed; null = no per-service restriction).
    if (st.paymentModes) modes = modes.filter(function (m) { return st.paymentModes.indexOf(m) >= 0; });
    if (matchTokenWallet() && modes.indexOf("token") < 0) modes.unshift("token");
    return modes.filter(function (m) { return m === "token" || UI.SETTLEMENT[m]; });
  }
  // On-behalf: match a pack the CLIENT holds with THIS coach (loaded via opts.loadPackages). When
  // one exists we default to it and DRAW it down (no new charge) instead of raising a fresh order.
  function onBehalfMatchWallet() {
    // Only lessons: the packs we load on-behalf are LESSON packs — don't offer one for a class.
    if (!st.onBehalf || st.type !== "lesson") return null;
    var list = st.clientWallets || [];
    var coach = chosenCoachUserId();
    var hit = list.filter(function (w) {
      if (w.status !== "active") return false;
      if ((w.minutes_remaining || 0) <= 0 && (w.sessions_remaining || 0) <= 0) return false;
      if (w.coach_user_id != null && coach != null && String(w.coach_user_id) !== String(coach)) return false;
      return true;
    })[0] || null;
    st.tokenWallet = hit;
    return hit;
  }
  function tokenChipMeta() {
    var w = st.tokenWallet, mine = st.onBehalf ? "their pack" : "your pack";
    if (!w) return { label: "Use " + mine, hint: "" };
    return { label: "Covered by " + mine,
             hint: walletSessionsLeft(w) + " of " + (w.tokens_total || 0) + " sessions left · this booking is free" };
  }
  function priceLabel() {
    if (st.settlement === "token" && st.tokenWallet) return "Covered by your pack · R0";
    if (courtCovered()) return coveredLabel();
    // Lessons price by the chosen SERVICE's duration (fixed) — never the availability slot price
    // (which can't distinguish a coach's multiple services). Courts keep the per-slot price (covered).
    var minor = (st.type !== "lesson" && st.slot && st.slot.price != null) ? st.slot.price : st.selDurationPrice;
    return minor != null ? UI.money(minor, ctx.billing.currency) : "—";
  }

  // ---- data loads ------------------------------------------------------------
  async function loadDurations() {
    // Lessons: pick a COACH → their SERVICES (Private / Semi-private), each with its OWN durations +
    // rate card (a coach can offer several; merging them showed the wrong price).
    if (st.type === "lesson") { await loadLessonServices(); return; }
    var q = { kind: st.type, audience: "member" };
    try {
      var r = await fetchDurations(q);
      st.durations = r.durations || [];
      st.membershipCovered = !!r.membership_covered;
      st.paymentModes = r.payment_modes || null;   // per-service payment preference (null = all)
    } catch (e) { st.durations = []; st.membershipCovered = false; st.paymentModes = null; }
    applyDurationSelection();
  }
  async function loadLessonServices() {
    var cid = chosenCoachUserId();
    st.services = [];
    if (cid) {
      try { st.services = (await window.TFAuth.apiJSON("/api/diary/services" + qs({ kind: "lesson", coach_id: cid, audience: "member" }))).services || []; } catch (e) { st.services = []; }
    }
    var keep = st.selService && st.services.filter(function (x) { return x.product_id === st.selService.product_id; })[0];
    st.selService = keep || st.services[0] || null;
    applyServiceDurations();
    await loadOnBehalfPackages(cid);   // detect the client's pack WITH this coach (draw, not new charge)
  }
  function applyServiceDurations() {
    var svc = st.selService;
    st.durations = (svc && svc.durations) || [];
    st.paymentModes = (svc && svc.payment_modes) || null;
    st.membershipCovered = false;   // a lesson is never membership-covered
    applyDurationSelection();
  }
  function applyDurationSelection() {
    if (st.durations.length) {
      var keep = st.durations.filter(function (x) { return x.duration_minutes === st.selDuration; })[0];
      if (!keep) { st.selDuration = st.durations[0].duration_minutes; st.selDurationPrice = st.durations[0].amount_minor; }
      else { st.selDurationPrice = keep.amount_minor; }
    } else { st.selDuration = null; st.selDurationPrice = null; }
  }

  function slotCacheKey() {
    var parts = [st.type, UI.dateKey(st.day), "d" + (st.selDuration || "")];
    if (st.type === "lesson") parts.push(st.selCoach === "ANY" ? "anycoach" : st.selCoach.id);
    parts.push(st.selCourt === "ANY" ? "anycourt" : st.selCourt.id);
    return parts.join("|");
  }
  async function loadSlots() {
    var box = document.getElementById("bk-slots"); if (!box) return;
    if (st.type === "class") { loadClasses(); return; }
    if (st.type === "lesson" && !chosenCoachUserId()) { box.className = ""; UI.clear(box); box.appendChild(el("div", { class: "cf-empty", text: "Choose a coach to see their available times." })); return; }
    if (!st.durations.length) { box.className = ""; UI.clear(box); box.appendChild(el("div", { class: "cf-empty", text: "This service isn't priced yet — please contact the club." })); return; }
    var ck = slotCacheKey();
    if (st.slotsCache[ck]) { renderSlots(st.slotsCache[ck]); return; }
    box.className = "cf-loading"; box.textContent = "Finding times…";
    var dk = UI.dateKey(st.day);
    var q = { date_from: dk, date_to: dk, audience: "member" };
    if (st.selDuration) q.duration = st.selDuration;
    if (st.type === "lesson") {
      q.kind = "coach";
      // The availability API filters by coach_user_id (diary/routes.py), so send the coach's
      // USER id — not the resource id. (Bug: sending .id matched no resource → zero lesson slots
      // for a specific coach. loadDurations + createBooking already use coach_user_id.)
      var _cid = chosenCoachUserId(); if (_cid) q.coach_id = _cid; else q.any = "1";
    } else {
      q.kind = "court";
      if (st.selCourt === "ANY") q.any = "1";
      else if (st.selCourt && st.selCourt.id) q.resource_id = st.selCourt.id;
    }
    try {
      var r = await window.API.availability(q);
      st.slotsCache[ck] = r.slots || [];
      if (slotCacheKey() === ck) renderSlots(st.slotsCache[ck]);
    } catch (e) { box.className = ""; UI.clear(box); box.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
  }
  async function loadClasses() {
    var box = document.getElementById("bk-slots"); if (!box) return;
    var dk = UI.dateKey(st.day), ck = "class|" + dk;
    if (st.slotsCache[ck]) { renderClasses(st.slotsCache[ck]); return; }
    box.className = "cf-loading"; box.textContent = "Finding classes…";
    try {
      var r = await window.API.classes({ date_from: dk, date_to: dk });
      st.slotsCache[ck] = r.classes || [];
      if (UI.dateKey(st.day) === dk) renderClasses(st.slotsCache[ck]);
    } catch (e) { box.className = ""; UI.clear(box); box.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
  }

  // ---- schedule render -------------------------------------------------------
  var TITLES = { court: "Book a court", lesson: "Book a lesson", class: "Attend a class" };

  function render() {
    UI.clear(container);
    if (st.view === "confirm") renderConfirm(); else renderSchedule();
  }

  function serviceSwitch() {
    var seg = el("div", { class: "cf-segment", style: "max-width:420px" });
    [["court", "Court"], ["lesson", "Lesson"], ["class", "Class"]].forEach(function (s) {
      seg.appendChild(el("button", { type: "button", class: st.type === s[0] ? "on" : "", text: s[1],
        onclick: function () { switchService(s[0]); } }));
    });
    return seg;
  }
  async function switchService(type) {
    if (type === st.type) return;
    st.type = type; st.slot = null; st.selClass = null; st.selCoach = "ANY"; st.selCourt = "ANY";
    st.selDuration = null; st.selDurationPrice = null; st.durations = []; st.slotsCache = {};
    if (st.coachLock && type === "lesson") {
      st.selCoach = ctx.coaches.filter(function (c) { return String(c.coach_user_id) === String(st.coachLock); })[0] || "ANY";
    }
    if (!st.onBehalf) { try { history.replaceState(null, "", "/book/" + type); } catch (e) {} }
    if (type !== "class") await loadDurations();
    render();
  }
  // The staff "booking on behalf of …" banner (+ back to the coach/admin app). Client flow: null.
  function onBehalfBanner() {
    if (!st.onBehalf) return null;
    return el("div", { class: "cf-card", style: "padding:10px 14px;margin-bottom:12px;display:flex;align-items:center;gap:10px;background:var(--green-050,#eef7f1);border:1px solid #cfe4d8" }, [
      el("span", { style: "font-size:1.1rem", text: "👤" }),
      el("div", { style: "flex:1" }, [
        el("div", { style: "font-weight:700;font-size:.9rem", text: "Booking for " + (st.onBehalf.name || st.onBehalf.email || "a client") }),
        el("div", { class: "cf-muted cf-tiny", text: "They'll be notified; collect payment at court, from their pack, or on their account." }),
      ]),
      st.backTo ? el("a", { class: "cf-btn cf-btn-sm cf-btn-ghost", href: st.backTo, text: "Cancel" }) : null,
    ].filter(Boolean));
  }

  function renderSchedule() {
    var banner = onBehalfBanner();
    if (banner) container.appendChild(banner);
    container.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:14px" }, [
      el("h1", { text: st.onBehalf ? "Book a client in" : "Book", style: "margin:0" }),
      serviceSwitch(),
    ]));

    var cols = [ el("div", { class: "cf-sched-col" }, [ el("div", { class: "cf-sched-h", text: "When" }), el("div", { id: "bk-cal" }) ]) ];

    if (st.type === "class") {
      cols.push(el("div", { class: "cf-sched-col" }, [ el("div", { class: "cf-mid-h cf-muted", text: "Pick a class" }), el("div", { id: "bk-slots", class: "cf-loading", text: "Finding classes…" }) ]));
      cols.push(el("div", { class: "cf-sched-col" }, [ el("div", { class: "cf-pref-h", text: "Your booking" }), summaryBox() ]));
    } else {
      cols.push(el("div", { class: "cf-sched-col" }, [
        el("div", { class: "cf-mid-h cf-muted", text: "How long?" }),
        el("div", { id: "bk-dur", class: "cf-durchips" }),
        el("div", { class: "cf-mid-h cf-muted", style: "margin-top:14px", text: "Pick a time" }),
        el("div", { id: "bk-slots", class: "cf-loading", text: "Finding times…" }),
      ]));
      cols.push(el("div", { class: "cf-sched-col" }, [
        el("div", { class: "cf-pref-h", text: st.type === "lesson" ? "Coach" : "Court" }),
        pickerControl(),
        el("div", { class: "cf-pref-h", style: "margin-top:14px", text: "Your booking" }),
        summaryBox(),
      ]));
    }

    container.appendChild(el("div", { class: "cf-card cf-sched-card" }, [ el("div", { class: "cf-sched" }, cols) ]));
    renderCalendar();
    if (st.type !== "class") renderDurations();
    loadSlots();
  }

  function pickerControl() {
    if (st.type === "lesson") {
      var fields = [];
      // COACH FIRST (services + rates are per-coach, so no 'Any coach' for lessons). On-behalf locks it.
      if (st.coachLock) {
        var lockedName = (st.selCoach !== "ANY" && st.selCoach.name) || "You";
        fields.push(el("div", { class: "cf-field" }, [ el("label", { text: "Coach" }),
          el("div", { class: "cf-select", style: "display:flex;align-items:center;background:var(--canvas,#f1f4ef)", text: lockedName }) ]));
      } else {
        var coachSel = el("select", { class: "cf-select", onchange: async function (ev) {
          st.selCoach = ctx.coaches.filter(function (c) { return c.id === ev.target.value; })[0] || "ANY";
          st.selService = null; st.slot = null; st.slotsCache = {}; await loadDurations(); render();
        } });
        if (st.selCoach === "ANY") coachSel.appendChild(el("option", { value: "", text: "Choose a coach…", selected: "selected" }));
        ctx.coaches.forEach(function (c) {
          coachSel.appendChild(el("option", { value: c.id, text: c.name || "Coach",
            selected: (st.selCoach !== "ANY" && st.selCoach.id === c.id) ? "selected" : null }));
        });
        fields.push(el("div", { class: "cf-field" }, [ el("label", { text: "Coach" }), coachSel ]));
      }
      // SERVICE (Private / Semi-private …) — a dropdown when the coach offers several; else the one.
      if (st.services && st.services.length > 1) {
        var svcSel = el("select", { class: "cf-select", onchange: function (ev) {
          st.selService = st.services.filter(function (x) { return x.product_id === ev.target.value; })[0] || null;
          st.slot = null; st.slotsCache = {}; applyServiceDurations(); render();
        } });
        st.services.forEach(function (sv) {
          svcSel.appendChild(el("option", { value: sv.product_id, text: sv.name,
            selected: (st.selService && st.selService.product_id === sv.product_id) ? "selected" : null }));
        });
        fields.push(el("div", { class: "cf-field" }, [ el("label", { text: "Service" }), svcSel ]));
      } else if (st.selService) {
        fields.push(el("div", { class: "cf-field" }, [ el("label", { text: "Service" }),
          el("div", { class: "cf-select", style: "background:var(--canvas,#f1f4ef)", text: st.selService.name }) ]));
      }
      fields.push(el("div", { class: "cf-pref-note", text: "We'll reserve a court for the lesson." }));
      return el("div", {}, fields);
    }
    var courtSel = el("select", { class: "cf-select", onchange: function (ev) {
      var v = ev.target.value;
      st.selCourt = v === "ANY" ? "ANY" : ctx.courts.filter(function (c) { return c.id === v; })[0] || "ANY";
      st.slot = null; st.slotsCache = {}; loadSlots(); refreshSummary();
    } });
    courtSel.appendChild(el("option", { value: "ANY", text: "Any available court", selected: st.selCourt === "ANY" ? "selected" : null }));
    ctx.courts.forEach(function (c) {
      courtSel.appendChild(el("option", { value: c.id, text: c.name || "Court",
        selected: (st.selCourt !== "ANY" && st.selCourt.id === c.id) ? "selected" : null }));
    });
    return el("div", { class: "cf-field" }, [ courtSel ]);
  }

  function renderDurations() {
    var box = document.getElementById("bk-dur"); if (!box) return; UI.clear(box);
    if (!st.durations.length) { box.appendChild(el("span", { class: "cf-muted cf-tiny", text: "—" })); return; }
    // "covered" on the duration chip only when the membership covers ANY time (no window). With an
    // off-peak window, coverage depends on the slot time, so show the price and let the slots show "free".
    var fullyCovered = st.type === "court" && st.membershipCovered && !membershipWindow();
    st.durations.forEach(function (d) {
      var sel = d.duration_minutes === st.selDuration;
      var priceTxt = fullyCovered ? "covered"
        : (d.amount_minor != null ? UI.money(d.amount_minor, ctx.billing.currency) : "");
      box.appendChild(el("button", { type: "button", class: "cf-durchip" + (sel ? " sel" : ""), onclick: function () {
        st.selDuration = d.duration_minutes; st.selDurationPrice = d.amount_minor; st.slot = null;
        renderDurations(); loadSlots(); refreshSummary();
      } }, [
        el("span", { text: d.duration_minutes + " min" }),
        priceTxt ? el("small", { text: priceTxt }) : null,
      ].filter(Boolean)));
    });
  }

  function renderSlots(slots) {
    var box = document.getElementById("bk-slots"); if (!box) return;
    box.className = ""; UI.clear(box);
    if (!slots.length) {
      box.appendChild(el("div", { class: "cf-empty", text: "No free times this day — try another day" + (st.type === "court" ? ", or 'Any available court'." : ".") }));
      return;
    }
    var grid = el("div", { class: "cf-timeblocks" });
    slots.forEach(function (sl) {
      // The server prices each slot PER-SLOT (0 only inside the membership window; peak slots keep
      // their PAYG price), so "free" is simply price === 0 for a covered court — no client guess.
      var covered = st.type === "court" && st.membershipCovered && sl.price === 0;
      // Lessons show the SERVICE's fixed duration price (the slot price can't tell a coach's services
      // apart); courts show the per-slot price (0 inside a membership window).
      var slPrice = st.type === "lesson" ? st.selDurationPrice : sl.price;
      var kids = [ el("span", { class: "cf-tb-time", text: UI.fmtTime(sl.start) }) ];
      if (covered) kids.push(el("span", { class: "cf-tb-price", text: "free" }));
      else if (slPrice != null) kids.push(el("span", { class: "cf-tb-price", text: UI.money(slPrice, ctx.billing.currency) }));
      grid.appendChild(el("button", { class: "cf-timeblock", type: "button",
        onclick: function () { st.slot = sl; st.view = "confirm"; render(); } }, kids));
    });
    box.appendChild(grid);
  }
  function renderClasses(classes) {
    var box = document.getElementById("bk-slots"); if (!box) return;
    box.className = ""; UI.clear(box);
    if (!classes.length) { box.appendChild(el("div", { class: "cf-empty", text: "No classes this day — try another." })); return; }
    var list = el("div", { class: "cf-list" });
    classes.forEach(function (c) {
      var full = c.spots_left === 0;
      var priceMinor = c.price_minor != null ? c.price_minor : c.price;
      var sub = UI.fmtTime(c.starts_at) + "–" + UI.fmtTime(c.ends_at);
      if (c.spots_left != null) sub += full ? " · Full" : " · " + c.spots_left + " spots left";
      if (priceMinor != null) sub += " · " + UI.money(priceMinor, ctx.billing.currency);
      list.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { st.selClass = c; st.view = "confirm"; render(); } }, [
        el("span", { class: "cf-chip class", text: full ? "waitlist" : "class" }),
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: c.class_name || "Class" }),
          el("div", { class: "cf-item-s", text: sub }),
        ]),
      ]));
    });
    box.appendChild(list);
  }

  // ---- month calendar --------------------------------------------------------
  function startOfMonth(d) { return new Date(d.getFullYear(), d.getMonth(), 1); }
  function renderCalendar() {
    var box = document.getElementById("bk-cal"); if (!box) return; UI.clear(box);
    var m = st.calMonth || startOfMonth(new Date()); st.calMonth = m;
    var today = new Date(); today.setHours(0, 0, 0, 0);
    var maxDay = UI.addDays(today, (ctx.policy && ctx.policy.booking_window_days) || 14); maxDay.setHours(0, 0, 0, 0);
    var curMonth = startOfMonth(today);

    var prevOff = m.getFullYear() === curMonth.getFullYear() && m.getMonth() === curMonth.getMonth();
    var nav = el("div", { class: "cf-cal-nav" }, [
      el("button", { class: "cf-cal-navbtn", type: "button", text: "‹", disabled: prevOff ? "disabled" : null,
        onclick: function () { if (prevOff) return; st.calMonth = new Date(m.getFullYear(), m.getMonth() - 1, 1); renderCalendar(); } }),
      el("div", { class: "cf-cal-title", text: m.toLocaleDateString("en-ZA", { month: "long", year: "numeric", timeZone: UI.CLUB_TZ }) }),
      el("button", { class: "cf-cal-navbtn", type: "button", text: "›",
        onclick: function () { st.calMonth = new Date(m.getFullYear(), m.getMonth() + 1, 1); renderCalendar(); } }),
    ]);
    box.appendChild(nav);

    var dow = el("div", { class: "cf-cal-dow" });
    ["S", "M", "T", "W", "T", "F", "S"].forEach(function (d) { dow.appendChild(el("span", { text: d })); });
    box.appendChild(dow);

    var grid = el("div", { class: "cf-cal-grid" });
    for (var i = 0; i < m.getDay(); i++) grid.appendChild(el("div", { class: "cf-cal-pad" }));
    var daysIn = new Date(m.getFullYear(), m.getMonth() + 1, 0).getDate();
    for (var day = 1; day <= daysIn; day++) {
      (function (dd) {
        dd.setHours(0, 0, 0, 0);
        var off = dd < today || dd > maxDay;
        var sel = UI.dateKey(dd) === UI.dateKey(st.day);
        grid.appendChild(el("button", { type: "button", class: "cf-cal-day" + (off ? " off" : "") + (sel ? " sel" : ""),
          disabled: off ? "disabled" : null,
          onclick: function () { if (off) return; st.day = dd; st.slot = null; st.selClass = null; renderCalendar(); loadSlots(); refreshSummary(); } }, [
          el("span", { class: "cf-cal-dnum", text: String(day) }),
        ]));
      })(new Date(m.getFullYear(), m.getMonth(), day));
    }
    box.appendChild(grid);
  }

  // ---- summary (schedule col3) ----------------------------------------------
  function summaryRows() {
    var rows = [["What", TITLES[st.type]]];
    if (st.type !== "class") {
      if (st.type === "lesson") {
        if (st.selService) rows.push(["Service", st.selService.name]);
        rows.push(["Coach", st.coachLock ? "You" : (st.selCoach !== "ANY" ? (st.selCoach.name || "Coach") : "Any coach")]);
      }
      else rows.push(["Court", st.selCourt !== "ANY" ? (st.selCourt.name || "Court") : (st.slot && st.slot.resource_name) || "Any court"]);
      rows.push(["Duration", (st.selDuration || "—") + " min"]);
      rows.push(["When", st.slot ? UI.fmtRange(st.slot.start, st.slot.end) : (UI.fmtDate(st.day) + " · pick a time")]);
    } else {
      rows.push(["Class", st.selClass ? (st.selClass.class_name || "Class") : "Pick a class"]);
      rows.push(["When", st.selClass ? UI.fmtRange(st.selClass.starts_at, st.selClass.ends_at) : UI.fmtDate(st.day)]);
    }
    rows.push(["Price", priceLabel()]);
    return rows;
  }
  function summaryBox() {
    var box = el("div", { class: "cf-svc-details", id: "bk-summary" });
    summaryRows().forEach(function (r) {
      box.appendChild(el("div", { class: "cf-svc-row" }, [ el("span", { class: "cf-svc-k", text: r[0] }), el("span", { class: "cf-svc-v", text: r[1] == null ? "—" : String(r[1]) }) ]));
    });
    return box;
  }
  function refreshSummary() {
    var old = document.getElementById("bk-summary"); if (!old || !old.parentNode) return;
    old.parentNode.replaceChild(summaryBox(), old);
  }

  // ---- confirm view (full-screen card) --------------------------------------
  function renderConfirm() {
    var free = courtCovered();
    var modes = payModes();
    if (free) st.settlement = "membership_covered";
    else if (modes.indexOf(st.settlement) < 0) st.settlement = modes[0] || "at_court";

    var card = el("div", { class: "cf-card", style: "max-width:560px;margin:0 auto" });
    card.appendChild(el("button", { class: "cf-sheet-back", type: "button", text: "‹ Back to times",
      onclick: function () { st.view = "schedule"; render(); } }));
    card.appendChild(el("h2", { text: "Confirm your booking" }));

    var sm = el("div", { class: "cf-summary" });
    summaryRows().forEach(function (r) {
      sm.appendChild(el("div", { class: "cf-summary-row" }, [ el("span", { class: "cf-summary-k", text: r[0] }), el("span", { class: "cf-summary-v", text: r[1] == null ? "—" : String(r[1]) }) ]));
    });
    card.appendChild(sm);

    if (ctx.dependents && ctx.dependents.length) card.appendChild(playerSection());

    // On-behalf: the client IS the booked party (posted via for_email) — no self player/guest step.
    if (st.type !== "class" && !st.onBehalf) {
      var gName = el("input", { class: "cf-input", placeholder: "Guest name", value: (st.guest && st.guest.name) || "" });
      var gEmail = el("input", { class: "cf-input", type: "email", placeholder: "Guest email (optional)", value: (st.guest && st.guest.email) || "" });
      st._gName = gName; st._gEmail = gEmail;
      card.appendChild(el("div", { class: "cf-confirm-sec" }, [
        el("h3", { text: "Playing with a guest?" }),
        el("p", { class: "cf-muted cf-tiny", text: "Optional — leave blank for a solo booking." }),
        el("div", { class: "cf-grid cf-grid-2" }, [
          el("div", { class: "cf-field" }, [ el("label", { text: "Guest name" }), gName ]),
          el("div", { class: "cf-field" }, [ el("label", { text: "Guest email" }), gEmail ]),
        ]),
      ]));
    } else { st._gName = null; st._gEmail = null; }

    if (free) {
      var trial = ctx.plan && ctx.plan.is_trial;
      card.appendChild(freePanel(trial ? "Free this week — enjoy the club." : "Covered by your membership — free.",
        trial && ctx.plan.trial_days_left != null
          ? ("Your free week — " + ctx.plan.trial_days_left + " day" + (ctx.plan.trial_days_left === 1 ? "" : "s") + " left")
          : "No charge for this court."));
    } else if (st.settlement === "token" && !st.showPayOptions) {
      var w = st.tokenWallet, whose = st.onBehalf ? "their" : "your";
      card.appendChild(freePanel("Free with " + whose + " pack.", w ? (walletSessionsLeft(w) + " of " + (w.tokens_total || 0) + " sessions left in " + whose + " pack") : null));
      card.appendChild(el("p", { style: "text-align:center;margin-top:8px" }, [
        el("a", { href: "#", class: "cf-muted cf-tiny", text: "Pay another way instead", onclick: function (ev) { ev.preventDefault(); st.showPayOptions = true; renderConfirm(); } }),
      ]));
    } else if (modes.length <= 1) {
      // One way to pay → no choice to present (the rule). Online → the button says "Confirm & pay";
      // a single offline method → just show what it is.
      var only = modes[0] || "at_court";
      if (only !== "online" && UI.SETTLEMENT[only]) {
        card.appendChild(el("div", { class: "cf-confirm-sec" }, [
          el("h3", { text: "Payment" }),
          el("p", { class: "cf-muted cf-tiny", text: UI.SETTLEMENT[only].label + " — " + (UI.SETTLEMENT[only].hint || "") }),
        ]));
      }
    } else {
      card.appendChild(el("div", { class: "cf-confirm-sec" }, [ el("h3", { text: "How would you like to pay?" }), settlementBlocks(modes) ]));
    }

    var btn = el("button", { class: "cf-btn cf-btn-primary cf-btn-lg cf-btn-block", type: "button", style: "margin-top:16px", text: confirmLabel() });
    btn.addEventListener("click", function () { submit(btn); });
    card.appendChild(btn);

    UI.clear(container);
    var cb = onBehalfBanner(); if (cb) container.appendChild(cb);
    container.appendChild(card);
  }

  function confirmLabel() {
    if (st.type === "class") return (st.selClass && st.selClass.spots_left === 0) ? "Join the waitlist" : "Confirm enrolment";
    if (!courtCovered() && st.settlement === "online") return "Confirm & pay";
    return "Confirm booking";
  }
  function playerName(d) { return ((d.first_name || "") + " " + (d.surname || "")).trim() || "Child"; }
  function playerSection() {
    var sel = el("select", { class: "cf-select", onchange: function (ev) {
      var v = ev.target.value;
      st.player = v === "ME" ? null : ctx.dependents.filter(function (d) { return d.dependent_user_id === v; })[0] || null;
    } });
    sel.appendChild(el("option", { value: "ME", text: "Myself", selected: st.player ? null : "selected" }));
    ctx.dependents.forEach(function (d) {
      sel.appendChild(el("option", { value: d.dependent_user_id, text: playerName(d),
        selected: (st.player && st.player.dependent_user_id === d.dependent_user_id) ? "selected" : null }));
    });
    return el("div", { class: "cf-confirm-sec" }, [ el("h3", { text: "Who's playing?" }), el("div", { class: "cf-field" }, [ el("label", { text: "Player" }), sel ]) ]);
  }
  function freePanel(title, sub) {
    return el("div", { class: "cf-confirm-sec" }, [
      el("div", { style: "display:flex;gap:12px;align-items:center;padding:14px 16px;border-radius:12px;background:var(--green-050);border:1px solid #cfe4d8" }, [
        el("span", { style: "font-size:1.4rem;line-height:1;color:var(--green)", text: "✓" }),
        el("div", {}, [ el("div", { style: "font-weight:800", text: title }), sub ? el("div", { class: "cf-muted cf-tiny", text: sub }) : null ].filter(Boolean)),
      ]),
    ]);
  }
  function settlementBlocks(modes) {
    var wrap = el("div", { class: "cf-settlechips" });
    modes.forEach(function (m) {
      var meta = m === "token" ? tokenChipMeta() : UI.SETTLEMENT[m];
      if (!meta) return;
      wrap.appendChild(el("button", { type: "button", class: "cf-settlechip" + (st.settlement === m ? " sel" : ""),
        onclick: function () { st.settlement = m; renderConfirm(); } }, [
        el("span", { class: "cf-settlechip-t", text: meta.label }),
        el("span", { class: "cf-settlechip-s", text: meta.hint }),
      ]));
    });
    return wrap;
  }
  function captureGuest() {
    if (st.type !== "class" && st._gName) {
      var n = st._gName.value.trim(), em = st._gEmail.value.trim();
      st.guest = n ? { name: n, email: em } : null;
    }
  }

  // ---- submit ----------------------------------------------------------------
  async function submit(btn) {
    captureGuest();
    btn.disabled = true; btn.textContent = "Booking…";
    try {
      var res, playerDepId = st.player && st.player.dependent_user_id;
      if (st.type === "class") {
        var enrolBody = { settlement_mode: st.settlement, audience: "member" };
        if (playerDepId) enrolBody.dependent_user_id = playerDepId;
        if (st.onBehalf) {
          // Staff on-behalf: enrol the CLIENT (the enrol route honours user_id for coach/admin). A
          // class needs a member account, so a walk-in guest (no user_id) can't be enrolled here.
          if (!st.onBehalf.user_id) { btn.disabled = false; btn.textContent = confirmLabel();
            UI.toast("A class booking needs a member account — pick a member, not a guest.", "warn"); return; }
          enrolBody.user_id = st.onBehalf.user_id;
        }
        res = await window.API.enrol(st.selClass.id, enrolBody);
        success("class", res); return;
      }
      var parties = [];
      if (playerDepId) parties.push({ party_role: "player", user_id: playerDepId });
      if (st.guest) {
        parties.push({ party_role: "host", user_id: ctx.principal.user_id });
        parties.push({ party_role: "guest", guest_name: st.guest.name, guest_email: st.guest.email || null });
      }
      var body = {
        booking_type: st.type === "lesson" ? "lesson" : "court",
        starts_at: st.slot.start, ends_at: st.slot.end,
        settlement_mode: st.settlement, parties: parties, audience: "member",
      };
      // On-behalf: the server resolves for_email → booked_for_user_id (a member) or a walk-in guest;
      // the booking auto-confirms and no self parties are sent.
      if (st.onBehalf) { body.parties = []; body.for_email = st.onBehalf.email || undefined;
        if (!body.for_email && st.onBehalf.name) body.for_guest_name = st.onBehalf.name; }
      if (st.type === "lesson") {
        body.coach_user_id = (st.selCoach !== "ANY" && st.selCoach.coach_user_id) || st.coachLock || null;
        if (st.selService) body.product_id = st.selService.product_id;   // charge the CHOSEN service exactly
        body.resource_id = st.slot.resource_id;
        body.court_resource_id = (st.selCourt !== "ANY" && st.selCourt.id) || st.slot.court_resource_id || null;
      } else {
        body.resource_id = st.slot.resource_id;
      }
      res = await window.API.createBooking(body);
      var orderId = res.order_id || (res.booking && res.booking.order_id);
      // Staff on-behalf never goes to Yoco (they collect at court / pack / account).
      if (!st.skipOnline && st.settlement === "online" && orderId) {
        if (window.Pay) { await window.Pay.startYocoCheckout(orderId); return; }
        UI.toast("Couldn't open the payment page — please refresh and try again.", "error"); return;
      }
      if (res.checkout && res.checkout.redirect_url) { location.href = res.checkout.redirect_url; return; }
      success(st.type, res);
    } catch (e) {
      btn.disabled = false; btn.textContent = confirmLabel();
      var code = (e && e.body && e.body.error) || "";
      if (code === "NO_TOKEN") {
        UI.toast("No matching session pack — please choose another way to pay.", "error");
        st.tokenWallet = null; st.showPayOptions = true;
        var fallback = payModes().filter(function (m) { return m !== "token"; });
        st.settlement = fallback[0] || "at_court";
        renderConfirm(); return;
      }
      if (e && (e.status === 409 || code === "SLOT_TAKEN")) {
        UI.toast("That slot was just taken — pick another.", "error");
        st.slot = null; st.slotsCache = {}; st.view = "schedule"; render(); return;
      }
      UI.toast(UI.errMsg(e), "error");
    }
  }

  function success(kind, res) {
    var stt = res.status || (res.booking && res.booking.status);
    var title, msg;
    if (kind === "class") {
      if (stt === "waitlisted") { title = "You're on the waitlist"; msg = "We'll email you the moment a spot opens."; }
      else { title = "You're enrolled!"; msg = "A confirmation email is on its way."; }
    } else if (stt === "held") { title = "Booking held"; msg = "We're holding your slot until payment completes."; }
    else { title = "You're booked!"; msg = "A confirmation email is on its way."; }
    if (st.onBehalf) {
      title = "Booked for " + (st.onBehalf.name || st.onBehalf.email || "your client");
      msg = "They've been notified. Collect payment at court, from their pack, or on their account.";
    }

    var detail = el("div", { class: "cf-summary cf-success-detail" });
    summaryRows().forEach(function (r) {
      detail.appendChild(el("div", { class: "cf-summary-row" }, [ el("span", { class: "cf-summary-k", text: r[0] }), el("span", { class: "cf-summary-v", text: r[1] == null ? "—" : String(r[1]) }) ]));
    });
    UI.clear(container);
    container.appendChild(el("div", { class: "cf-card cf-success", style: "max-width:480px;margin:0 auto" }, [
      el("div", { class: "cf-success-tick", text: "✓" }),
      el("h2", { class: "cf-success-h", text: title }),
      el("p", { class: "cf-muted", text: msg }),
      detail,
      el("div", { class: "cf-row cf-success-actions", style: "margin-top:16px" }, [
        st.onBehalf
          ? el("button", { class: "cf-btn cf-btn-primary cf-btn-lg", type: "button", text: "Done", onclick: function () { if (st.onDone) st.onDone(); else if (st.backTo) location.hash = st.backTo; } })
          : el("a", { class: "cf-btn cf-btn-primary cf-btn-lg", href: window.Client ? "#/bookings" : "/my.html", text: "View my bookings" }),
        el("button", { class: "cf-btn cf-btn-ghost cf-btn-lg", type: "button", text: st.onBehalf ? "Book another" : "Book another", onclick: function () {
          st.slot = null; st.selClass = null; st.view = "schedule"; st.showPayOptions = false; render();
        } }),
      ]),
    ]));
  }

  // ---- public API ------------------------------------------------------------
  var ALLOWED = { court: 1, lesson: 1, class: 1 };
  // start(principal, service[, opts]). opts (ONLY for staff on-behalf; omit for the client flow — the
  // client path is unchanged): { onBehalf: {name, email}, coachLock: coach_user_id, backTo: '#/route' }.
  //   onBehalf  → book FOR this client (posts for_email); no Yoco (staff collect at court/token/later),
  //               no self dependents/wallets/membership, an "on behalf of …" banner.
  //   coachLock → lock the lesson to this coach (their own client) and hide the coach picker.
  //   backTo    → hash route to return to when done (the coach/admin app).
  async function start(principal, service, opts) {
    opts = opts || {};
    UI = window.UI; el = UI.el;
    container = document.getElementById("cf-main");
    if (!container) return;
    await ensureCtx(principal, opts.onBehalf);
    var type = ALLOWED[service] ? service : "court";
    st = {
      type: type, calMonth: startOfMonth(new Date()), day: new Date(),
      durations: [], selDuration: null, selDurationPrice: null, membershipCovered: false,
      selCoach: "ANY", selCourt: "ANY", slot: null, selClass: null, player: null, guest: null,
      settlement: "at_court", tokenWallet: null, showPayOptions: false, slotsCache: {},
      view: "schedule", _gName: null, _gEmail: null,
      services: [], selService: null,
      onBehalf: opts.onBehalf || null, coachLock: opts.coachLock || null,
      backTo: opts.backTo || null, onDone: opts.onDone || null, skipOnline: !!opts.onBehalf,
      clientWallets: [], loadPackagesFn: opts.loadPackages || null,
    };
    // Coach booking their OWN client: preselect + lock the coach.
    if (st.coachLock && type === "lesson") {
      st.selCoach = ctx.coaches.filter(function (c) {
        return String(c.coach_user_id) === String(st.coachLock);
      })[0] || "ANY";
    }
    // Package auto-detection happens in loadLessonServices (once a coach is resolved) — the coach may
    // be picked in the widget (admin on-behalf), so we can't know it up front.
    if (type !== "class") await loadDurations();
    render();
  }
  // On-behalf: fetch the client's packs WITH the resolved coach; if they hold one, DEFAULT to drawing
  // it (auto-route to their prepaid pack instead of raising a new charge). Server does the precise draw.
  async function loadOnBehalfPackages(coachId) {
    st.clientWallets = [];
    if (!(st.onBehalf && st.onBehalf.user_id && coachId && typeof st.loadPackagesFn === "function")) return;
    try { st.clientWallets = (await st.loadPackagesFn(st.onBehalf.user_id, coachId)) || []; } catch (e) { st.clientWallets = []; }
    if (onBehalfMatchWallet()) st.settlement = "token";
  }

  window.BookFlow = { start: start };
})();

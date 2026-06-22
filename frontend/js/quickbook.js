// quickbook.js — the on-dashboard quick-book SHEET (client-journey redesign, Phase 1, increment 2).
//
// A slide-up sheet launched from the dashboard's quick-book cards (portal.html). Goal: book a
// court in ~2 taps. Two panes inside one sheet:
//   • Schedule — a day strip (today default) + inline duration/court(or coach) controls + the
//     day's time blocks (court/lesson) or class sessions (class). Tap a time → Confirm pane.
//   • Confirm  — summary + "Who's playing?" + optional guest + payment (auto-skipped when the
//     booking is free) + Confirm.
//
// It honours book.js's EXACT booking contract (the money path is unchanged — this is a new UI over
// the same APIs, with the server as the source of truth):
//   - durations: GET /api/diary/durations?kind=court|lesson&audience=member[&coach_id=<coach_user_id>]
//   - availability: API.availability({kind:'court'|'coach', any|resource_id|coach_id=<resource id>, duration, date_from/to, audience})
//   - classes: API.classes({date_from,date_to})
//   - create: API.createBooking({booking_type, resource_id, starts_at, ends_at, settlement_mode,
//             parties, audience, coach_user_id?, court_resource_id?})  /  API.enrol(classId, body)
//   - online seam: orderId = res.order_id || res.booking.order_id → Pay.startYocoCheckout(orderId)
//   - NO_TOKEN → fall back to PAYG; 409/SLOT_TAKEN → bounce back to the Schedule pane.
(function () {
  var UI, el;

  // Cross-open context, loaded once per session (billing config, dependents, wallets, plan, resources).
  var ctx = {
    loaded: false, principal: null,
    billing: { online_enabled: false, currency: "ZAR", provider: "manual" },
    dependents: [], walletsByKind: {}, plan: null, coaches: [], courts: [], policy: null,
  };
  var st = null;     // per-open booking state
  var overlay = null;

  function qs(params) {
    var parts = [];
    Object.keys(params || {}).forEach(function (k) {
      var v = params[k];
      if (v !== undefined && v !== null && v !== "") parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(v));
    });
    return parts.length ? ("?" + parts.join("&")) : "";
  }
  function fetchDurations(q) { return window.TFAuth.apiJSON("/api/diary/durations" + qs(q)); }

  async function ensureCtx(principal) {
    ctx.principal = principal;
    if (ctx.loaded) return;
    try { ctx.billing = await window.API.billingConfig(principal.club_id); } catch (e) {}
    try { ctx.dependents = (await window.API.dependents()).dependents || []; } catch (e) {}
    try {
      var wr = await window.TFAuth.apiJSON("/api/billing/bundles/wallets");
      (wr.wallets || []).forEach(function (w) {
        (ctx.walletsByKind[w.service_kind] = ctx.walletsByKind[w.service_kind] || []).push(w);
      });
    } catch (e) {}
    try { ctx.plan = await window.TFAuth.apiJSON("/api/me/plan"); } catch (e) { ctx.plan = null; }
    try {
      var rs = (await window.API.resources()).resources || [];
      ctx.coaches = rs.filter(function (r) { return r.kind === "coach" && r.is_active; });
      ctx.courts = rs.filter(function (r) { return r.kind === "court" && r.is_active; });
    } catch (e) {}
    ctx.policy = (principal && principal.policy) || null;
    ctx.loaded = true;
  }

  // ---- coverage / settlement helpers (mirror book.js exactly) ----------------
  function bookingServiceKind() { return st.type === "lesson" ? "lesson" : (st.type === "class" ? "class" : "court"); }
  function membershipWindow() { return ctx.plan && ctx.plan.membership_window; }
  function withinWindow(d, w) {
    if (!w || !d) return true;
    var iso = ((d.getDay() + 6) % 7) + 1;          // JS Sun=0 → ISO Mon=1..Sun=7
    if (w.days && w.days.length && w.days.indexOf(iso) < 0) return false;
    var mod = d.getHours() * 60 + d.getMinutes();
    if (w.start_min != null && mod < w.start_min) return false;
    if (w.end_min != null && mod >= w.end_min) return false;
    return true;
  }
  // Court covered FREE for the chosen slot: court + active court membership AND (no window OR inside it).
  function courtCovered() {
    if (st.type !== "court" || !st.membershipCovered) return false;
    var w = membershipWindow();
    if (!w) return true;
    var start = st.slot && st.slot.start ? new Date(st.slot.start) : null;
    return start ? withinWindow(start, w) : false;
  }
  function coveredLabel() {
    return (ctx.plan && ctx.plan.is_trial) ? "Free this week · R0" : "Covered by your membership · R0";
  }
  function walletMinutesLeft(w) {
    return (w.minutes_remaining != null) ? w.minutes_remaining : (w.tokens_remaining || 0) * 60;
  }
  function chosenCoachUserId() {
    if (st.type !== "lesson") return null;
    if (st.selCoach !== "ANY" && st.selCoach && st.selCoach.coach_user_id) return st.selCoach.coach_user_id;
    return (st.slot && st.slot.coach_user_id) || null;
  }
  // Best usable wallet for this service (+coach): active, positive balance, coach matches or is ANY.
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
  // The manual pay chooser. membership_covered is NEVER a manual option (it's auto-applied free when
  // courtCovered); token is offered first when a usable wallet matches.
  function payModes() {
    var allow = (ctx.policy && ctx.policy.allowed_settlement_modes)
      ? ctx.policy.allowed_settlement_modes.filter(function (m) { return m !== "membership_covered"; })
      : ["at_court", "monthly_account"];
    var modes = allow.slice();
    if (ctx.billing.online_enabled && modes.indexOf("online") < 0) modes.push("online");
    if (matchTokenWallet() && modes.indexOf("token") < 0) modes.unshift("token");
    return modes.filter(function (m) { return m === "token" || UI.SETTLEMENT[m]; });
  }
  function tokenChipMeta() {
    var w = st.tokenWallet;
    if (!w) return { label: "Use your pack", hint: "" };
    return { label: "Covered by your pack",
             hint: walletSessionsLeft(w) + " of " + (w.tokens_total || 0) + " sessions left · this booking is free" };
  }
  function priceLabel() {
    if (st.settlement === "token" && st.tokenWallet) return "Covered by your pack · R0";
    if (courtCovered()) return coveredLabel();
    var minor = (st.slot && st.slot.price != null) ? st.slot.price : st.selDurationPrice;
    return minor != null ? UI.money(minor, ctx.billing.currency) : "—";
  }

  // ---- data loads ------------------------------------------------------------
  async function loadDurations() {
    var q = { kind: st.type, audience: "member" };
    if (st.type === "lesson" && st.selCoach !== "ANY" && st.selCoach.coach_user_id) q.coach_id = st.selCoach.coach_user_id;
    try {
      var r = await fetchDurations(q);
      st.durations = r.durations || [];
      st.membershipCovered = !!r.membership_covered;
    } catch (e) { st.durations = []; st.membershipCovered = false; }
    if (st.durations.length && st.selDuration == null) {
      st.selDuration = st.durations[0].duration_minutes;
      st.selDurationPrice = st.durations[0].amount_minor;
    } else if (st.selDuration != null) {
      var d = st.durations.filter(function (x) { return x.duration_minutes === st.selDuration; })[0];
      st.selDurationPrice = d ? d.amount_minor : st.selDurationPrice;
    }
  }

  function slotCacheKey() {
    var parts = [st.type, UI.dateKey(st.day), "d" + (st.selDuration || "")];
    if (st.type === "lesson") parts.push(st.selCoach === "ANY" ? "anycoach" : st.selCoach.id);
    parts.push(st.selCourt === "ANY" ? "anycourt" : st.selCourt.id);
    return parts.join("|");
  }

  async function loadSlots() {
    var box = document.getElementById("qb-slots"); if (!box) return;
    if (st.type === "class") { loadClasses(); return; }
    if (!st.durations.length) { UI.clear(box); box.appendChild(el("div", { class: "cf-empty", text: "This service isn't priced yet — please contact the club." })); return; }
    var ck = slotCacheKey();
    if (st.slotsCache[ck]) { renderSlots(st.slotsCache[ck]); return; }
    box.className = "cf-loading"; box.textContent = "Finding times…";
    var dk = UI.dateKey(st.day);
    var q = { date_from: dk, date_to: dk, audience: "member" };
    if (st.selDuration) q.duration = st.selDuration;
    if (st.type === "lesson") {
      q.kind = "coach";
      if (st.selCoach !== "ANY" && st.selCoach.id) q.coach_id = st.selCoach.id; else q.any = "1";
    } else {
      q.kind = "court";
      if (st.selCourt === "ANY") q.any = "1";
      else if (st.selCourt && st.selCourt.id) q.resource_id = st.selCourt.id;
    }
    try {
      var r = await window.API.availability(q);
      st.slotsCache[ck] = r.slots || [];
      if (slotCacheKey() === ck) renderSlots(st.slotsCache[ck]);
    } catch (e) { UI.clear(box); box.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
  }

  async function loadClasses() {
    var box = document.getElementById("qb-slots"); if (!box) return;
    var dk = UI.dateKey(st.day), ck = "class|" + dk;
    if (st.slotsCache[ck]) { renderClasses(st.slotsCache[ck]); return; }
    box.className = "cf-loading"; box.textContent = "Finding classes…";
    try {
      var r = await window.API.classes({ date_from: dk, date_to: dk });
      st.slotsCache[ck] = r.classes || [];
      if (UI.dateKey(st.day) === dk) renderClasses(st.slotsCache[ck]);
    } catch (e) { UI.clear(box); box.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
  }

  function renderSlots(slots) {
    var box = document.getElementById("qb-slots"); if (!box) return;
    box.className = ""; UI.clear(box);
    if (!slots.length) {
      box.appendChild(el("div", { class: "cf-empty", text: "No free times this day — try another, or 'Any'." }));
      return;
    }
    var grid = el("div", { class: "cf-timeblocks" });
    slots.forEach(function (sl) {
      var kids = [ el("span", { class: "cf-tb-time", text: UI.fmtTime(sl.start) }) ];
      if (sl.price != null && !(st.type === "court" && st.membershipCovered)) {
        kids.push(el("span", { class: "cf-tb-price", text: UI.money(sl.price, ctx.billing.currency) }));
      } else if (st.type === "court" && st.membershipCovered) {
        kids.push(el("span", { class: "cf-tb-price", text: "free" }));
      }
      grid.appendChild(el("button", { class: "cf-timeblock", type: "button",
        onclick: function () { st.slot = sl; st.pane = "confirm"; render(); } }, kids));
    });
    box.appendChild(grid);
  }

  function renderClasses(classes) {
    var box = document.getElementById("qb-slots"); if (!box) return;
    box.className = ""; UI.clear(box);
    if (!classes.length) { box.appendChild(el("div", { class: "cf-empty", text: "No classes this day — try another." })); return; }
    var list = el("div", { class: "cf-list" });
    classes.forEach(function (c) {
      var full = c.spots_left === 0;
      var priceMinor = c.price_minor != null ? c.price_minor : c.price;
      var sub = UI.fmtTime(c.starts_at) + "–" + UI.fmtTime(c.ends_at);
      if (c.spots_left != null) sub += full ? " · Full" : " · " + c.spots_left + " spots left";
      if (priceMinor != null) sub += " · " + UI.money(priceMinor, ctx.billing.currency);
      list.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () {
        st.selClass = c; st.pane = "confirm"; render();
      } }, [
        el("span", { class: "cf-chip class", text: full ? "waitlist" : "class" }),
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: c.class_name || "Class" }),
          el("div", { class: "cf-item-s", text: sub }),
        ]),
      ]));
    });
    box.appendChild(list);
  }

  // ---- sheet shell + render --------------------------------------------------
  var TITLES = { court: "Book a court", lesson: "Book a lesson", class: "Attend a class" };

  function mountSheet() {
    closeSheet();
    overlay = el("div", { class: "cf-sheet-bg", onclick: function (ev) { if (ev.target === overlay) closeSheet(); } });
    var sheet = el("div", { class: "cf-sheet" }, [
      el("div", { class: "cf-sheet-head" }, [
        el("h2", { id: "qb-title", text: TITLES[st.type] || "Book" }),
        el("button", { class: "cf-sheet-x", type: "button", text: "✕", onclick: closeSheet, title: "Close" }),
      ]),
      el("div", { id: "qb-body", class: "cf-sheet-body" }),
      el("div", { id: "qb-foot", class: "cf-sheet-foot", style: "display:none" }),
    ]);
    overlay.appendChild(sheet);
    document.body.appendChild(overlay);
    document.addEventListener("keydown", onKey);
  }
  function onKey(e) { if (e.key === "Escape") closeSheet(); }
  function closeSheet() {
    if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
    overlay = null;
    document.removeEventListener("keydown", onKey);
  }

  function render() {
    var t = document.getElementById("qb-title"); if (t) t.textContent = TITLES[st.type] || "Book";
    if (st.pane === "confirm") renderConfirm(); else renderSchedule();
  }

  // -- Schedule pane --
  function renderSchedule() {
    var body = document.getElementById("qb-body"), foot = document.getElementById("qb-foot");
    if (!body) return;
    UI.clear(body); if (foot) { foot.style.display = "none"; UI.clear(foot); }

    // day strip — today + the booking window (default 14 days).
    var windowDays = (ctx.policy && ctx.policy.booking_window_days) || 14;
    var strip = el("div", { class: "cf-datestrip" });
    for (var i = 0; i <= windowDays; i++) {
      (function (d) {
        var sel = UI.dateKey(d) === UI.dateKey(st.day);
        strip.appendChild(el("button", { type: "button", class: "cf-date" + (sel ? " sel" : ""), onclick: function () {
          st.day = d; st.slot = null; st.selClass = null; renderSchedule();
        } }, [
          el("span", { class: "cf-date-dow", text: d.toLocaleDateString("en-ZA", { weekday: "short", timeZone: UI.CLUB_TZ }) }),
          el("span", { class: "cf-date-num", text: d.toLocaleDateString("en-ZA", { day: "numeric", timeZone: UI.CLUB_TZ }) }),
          el("span", { class: "cf-date-mon", text: d.toLocaleDateString("en-ZA", { month: "short", timeZone: UI.CLUB_TZ }) }),
        ]));
      })(UI.addDays(new Date(), i));
    }
    body.appendChild(strip);

    // inline controls (duration + court/coach) — court/lesson only.
    if (st.type !== "class") {
      var row = el("div", { class: "cf-qbrow" });

      var durSel = el("select", { class: "cf-select", onchange: function (ev) {
        st.selDuration = parseInt(ev.target.value, 10);
        var d = st.durations.filter(function (x) { return x.duration_minutes === st.selDuration; })[0];
        st.selDurationPrice = d ? d.amount_minor : null;
        st.slot = null; loadSlots();
      } });
      st.durations.forEach(function (d) {
        var lbl = d.duration_minutes + " min";
        if (st.type === "court" && st.membershipCovered) lbl += " · covered";
        else if (d.amount_minor != null) lbl += " · " + UI.money(d.amount_minor, ctx.billing.currency);
        durSel.appendChild(el("option", { value: d.duration_minutes, text: lbl,
          selected: d.duration_minutes === st.selDuration ? "selected" : null }));
      });
      row.appendChild(el("div", { class: "cf-field" }, [ el("label", { text: "Duration" }), durSel ]));

      if (st.type === "lesson") {
        var coachSel = el("select", { class: "cf-select", onchange: async function (ev) {
          var v = ev.target.value;
          st.selCoach = v === "ANY" ? "ANY" : ctx.coaches.filter(function (c) { return c.id === v; })[0] || "ANY";
          st.slot = null; st.selDuration = null; await loadDurations(); renderSchedule();
        } });
        coachSel.appendChild(el("option", { value: "ANY", text: "Any coach", selected: st.selCoach === "ANY" ? "selected" : null }));
        ctx.coaches.forEach(function (c) {
          coachSel.appendChild(el("option", { value: c.id, text: c.name || "Coach",
            selected: (st.selCoach !== "ANY" && st.selCoach.id === c.id) ? "selected" : null }));
        });
        row.appendChild(el("div", { class: "cf-field" }, [ el("label", { text: "Coach" }), coachSel ]));
      } else {
        var courtSel = el("select", { class: "cf-select", onchange: function (ev) {
          var v = ev.target.value;
          st.selCourt = v === "ANY" ? "ANY" : ctx.courts.filter(function (c) { return c.id === v; })[0] || "ANY";
          st.slot = null; loadSlots();
        } });
        courtSel.appendChild(el("option", { value: "ANY", text: "Any available court", selected: st.selCourt === "ANY" ? "selected" : null }));
        ctx.courts.forEach(function (c) {
          courtSel.appendChild(el("option", { value: c.id, text: c.name || "Court",
            selected: (st.selCourt !== "ANY" && st.selCourt.id === c.id) ? "selected" : null }));
        });
        row.appendChild(el("div", { class: "cf-field" }, [ el("label", { text: "Court" }), courtSel ]));
      }
      body.appendChild(row);
    }

    body.appendChild(el("div", { class: "cf-mid-h cf-muted", text: st.type === "class" ? "Pick a class" : "Pick a time" }));
    body.appendChild(el("div", { id: "qb-slots", class: "cf-loading", text: "Finding times…" }));
    loadSlots();
  }

  // -- Confirm pane --
  function renderConfirm() {
    var body = document.getElementById("qb-body"), foot = document.getElementById("qb-foot");
    if (!body) return;
    UI.clear(body); UI.clear(foot);

    // settlement default: covered court → membership_covered; else first pay mode (token if matched).
    var free = courtCovered();
    var modes = payModes();
    if (free) st.settlement = "membership_covered";
    else if (modes.indexOf(st.settlement) < 0) st.settlement = modes[0] || "at_court";

    body.appendChild(el("button", { class: "cf-sheet-back", type: "button", text: "‹ Back",
      onclick: function () { st.pane = "schedule"; render(); } }));

    // summary
    body.appendChild(summaryCard());

    // who's playing (dependents)
    if (ctx.dependents && ctx.dependents.length) body.appendChild(playerSection());

    // guest (court/lesson)
    if (st.type !== "class") {
      var gName = el("input", { class: "cf-input", placeholder: "Guest name", value: (st.guest && st.guest.name) || "" });
      var gEmail = el("input", { class: "cf-input", type: "email", placeholder: "Guest email (optional)", value: (st.guest && st.guest.email) || "" });
      st._gName = gName; st._gEmail = gEmail;
      body.appendChild(el("div", { class: "cf-confirm-sec" }, [
        el("h3", { text: "Playing with a guest?" }),
        el("p", { class: "cf-muted cf-tiny", text: "Optional — leave blank for a solo booking." }),
        el("div", { class: "cf-grid cf-grid-2" }, [
          el("div", { class: "cf-field" }, [ el("label", { text: "Guest name" }), gName ]),
          el("div", { class: "cf-field" }, [ el("label", { text: "Guest email" }), gEmail ]),
        ]),
      ]));
    } else { st._gName = null; st._gEmail = null; }

    // payment: free → reassurance; token → free-with-pack (+ pay another way); else the chooser.
    if (free) {
      var trial = ctx.plan && ctx.plan.is_trial;
      body.appendChild(freePanel(trial ? "Free this week — enjoy the club." : "Covered by your membership — free.",
        trial && ctx.plan.trial_days_left != null
          ? ("Your free week — " + ctx.plan.trial_days_left + " day" + (ctx.plan.trial_days_left === 1 ? "" : "s") + " left")
          : "No charge for this court."));
    } else if (st.settlement === "token" && !st.showPayOptions) {
      var w = st.tokenWallet;
      body.appendChild(freePanel("Free with your pack.",
        w ? (walletSessionsLeft(w) + " of " + (w.tokens_total || 0) + " sessions left in your pack") : null));
      body.appendChild(el("p", { style: "text-align:center;margin-top:8px" }, [
        el("a", { href: "#", class: "cf-muted cf-tiny", text: "Pay another way instead",
          onclick: function (ev) { ev.preventDefault(); st.showPayOptions = true; renderConfirm(); } }),
      ]));
    } else {
      body.appendChild(el("div", { class: "cf-confirm-sec" }, [
        el("h3", { text: "How would you like to pay?" }),
        settlementBlocks(modes),
      ]));
    }

    // foot: the confirm CTA
    foot.style.display = "block";
    var btn = el("button", { class: "cf-btn cf-btn-primary cf-btn-lg cf-btn-block", type: "button", text: confirmLabel() });
    btn.addEventListener("click", function () { submit(btn); });
    foot.appendChild(btn);
  }

  function confirmLabel() {
    if (st.type === "class") return (st.selClass && st.selClass.spots_left === 0) ? "Join the waitlist" : "Confirm enrolment";
    if (!courtCovered() && st.settlement === "online") return "Confirm & pay";
    return "Confirm booking";
  }

  function summaryCard() {
    var rows = [];
    rows.push(["What", TITLES[st.type]]);
    if (st.type === "class" && st.selClass) {
      rows.push(["Class", st.selClass.class_name || "Class"]);
      rows.push(["When", UI.fmtRange(st.selClass.starts_at, st.selClass.ends_at)]);
    } else if (st.slot) {
      if (st.type === "lesson") rows.push(["Coach", st.selCoach !== "ANY" ? (st.selCoach.name || "Coach") : "Any coach"]);
      rows.push(["Court", courtSummaryLabel()]);
      rows.push(["Duration", (st.selDuration || "") + " min"]);
      rows.push(["When", UI.fmtRange(st.slot.start, st.slot.end)]);
    }
    rows.push(["Price", priceLabel()]);
    var box = el("div", { class: "cf-summary" });
    rows.forEach(function (r) {
      box.appendChild(el("div", { class: "cf-summary-row" }, [
        el("span", { class: "cf-summary-k", text: r[0] }),
        el("span", { class: "cf-summary-v", text: r[1] == null ? "—" : String(r[1]) }),
      ]));
    });
    return box;
  }
  function courtSummaryLabel() {
    if (st.selCourt !== "ANY") return (st.selCourt && st.selCourt.name) || "—";
    if (st.type === "lesson") return (st.slot && (st.slot.court_resource_name || "Any available court")) || "Any available court";
    return (st.slot && st.slot.resource_name) || "Any available court";
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
    return el("div", { class: "cf-confirm-sec" }, [
      el("h3", { text: "Who's playing?" }),
      el("div", { class: "cf-field" }, [ el("label", { text: "Player" }), sel ]),
    ]);
  }

  function freePanel(title, sub) {
    return el("div", { class: "cf-confirm-sec" }, [
      el("div", { style: "display:flex;gap:12px;align-items:center;padding:14px 16px;border-radius:12px;background:var(--green-050);border:1px solid #cfe4d8" }, [
        el("span", { style: "font-size:1.4rem;line-height:1;color:var(--green)", text: "✓" }),
        el("div", {}, [
          el("div", { style: "font-weight:800", text: title }),
          sub ? el("div", { class: "cf-muted cf-tiny", text: sub }) : null,
        ].filter(Boolean)),
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

  // ---- submit (mirrors book.js submit() — same APIs + online seam) -----------
  async function submit(btn) {
    captureGuest();
    btn.disabled = true; btn.textContent = "Booking…";
    try {
      var res, playerDepId = st.player && st.player.dependent_user_id;
      if (st.type === "class") {
        var enrolBody = { settlement_mode: st.settlement, audience: "member" };
        if (playerDepId) enrolBody.dependent_user_id = playerDepId;
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
      if (st.type === "lesson") {
        body.coach_user_id = (st.selCoach !== "ANY" && st.selCoach.coach_user_id) || null;
        body.resource_id = st.slot.resource_id;                 // the coach resource slot
        body.court_resource_id = (st.selCourt !== "ANY" && st.selCourt.id) || st.slot.court_resource_id || null;
      } else {
        body.resource_id = st.slot.resource_id;                 // the court resolved by availability
      }
      res = await window.API.createBooking(body);
      var orderId = res.order_id || (res.booking && res.booking.order_id);
      if (st.settlement === "online" && orderId) {
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
        st.slot = null; st.slotsCache = {}; st.pane = "schedule"; render(); return;
      }
      UI.toast(UI.errMsg(e), "error");
    }
  }

  // ---- success ---------------------------------------------------------------
  function success(kind, res) {
    var body = document.getElementById("qb-body"), foot = document.getElementById("qb-foot");
    if (!body) return;
    UI.clear(body); if (foot) { foot.style.display = "none"; UI.clear(foot); }
    var stt = res.status || (res.booking && res.booking.status);
    var title, msg;
    if (kind === "class") {
      if (stt === "waitlisted") { title = "You're on the waitlist"; msg = "We'll email you the moment a spot opens."; }
      else { title = "You're enrolled!"; msg = "A confirmation email is on its way."; }
    } else if (stt === "held") { title = "Booking held"; msg = "We're holding your slot until payment completes."; }
    else { title = "You're booked!"; msg = "A confirmation email is on its way."; }

    body.appendChild(el("div", { class: "cf-success" }, [
      el("div", { class: "cf-success-tick", text: "✓" }),
      el("h2", { class: "cf-success-h", text: title }),
      el("p", { class: "cf-muted", text: msg }),
      el("div", { class: "cf-row cf-success-actions", style: "margin-top:16px" }, [
        el("a", { class: "cf-btn cf-btn-primary", href: "/my.html", text: "View my bookings" }),
        el("button", { class: "cf-btn cf-btn-ghost", type: "button", text: "Done", onclick: function () { closeSheet(); location.reload(); } }),
      ]),
    ]));
  }

  // ---- public API ------------------------------------------------------------
  async function open(type, principal) {
    UI = window.UI; el = UI.el;
    if (!principal || !principal.club_id) { if (UI) UI.toast("Please sign in to book.", "error"); return; }
    await ensureCtx(principal);
    st = {
      type: type, day: new Date(), durations: [], selDuration: null, selDurationPrice: null,
      membershipCovered: false, selCoach: "ANY", selCourt: "ANY", slot: null, selClass: null,
      player: null, guest: null, settlement: "at_court", tokenWallet: null, showPayOptions: false,
      slotsCache: {}, pane: "schedule", _gName: null, _gEmail: null,
    };
    mountSheet();
    if (type !== "class") await loadDurations();
    render();
  }

  window.QuickBook = { open: open, close: closeSheet };
})();

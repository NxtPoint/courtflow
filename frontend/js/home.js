// home.js — the client Home page (front-end redesign v2, 2026-06-28).
// ONE top menu only (no second chip strip). Home = a greeting block (identity + Edit-profile /
// Add-child popups) → Book launchers → My Bookings (Upcoming/Past toggle, nearest-first, full
// edit / cancel / request-refund). Usage + statements live on the separate Account page (account.js).
(function () {
  var UI, el, P;
  var st = { profile: null, deps: [], bookings: [], fin: null, wallets: [], view: "upcoming" };
  function openWizard() { if (window.PlanWizard) window.PlanWizard.open(); else window.location.href = "/plan"; }

  // ---- helpers ---------------------------------------------------------------
  function field(label, input, hint) {
    var kids = [el("label", { text: label }), input];
    if (hint) kids.push(el("div", { class: "cf-pref-note", text: hint }));
    return el("div", { class: "cf-field" }, kids);
  }
  function inp(value, attrs) { return el("input", Object.assign({ class: "cf-input", value: value == null ? "" : value }, attrs || {})); }
  function fullName() {
    var pr = st.profile || {};
    return ((pr.first_name || "") + " " + (pr.surname || "")).trim() || (P.email || "").split("@")[0];
  }

  // ===========================================================================
  // greeting — green band: greeting · name · email · [Edit profile] [Add child]
  // ===========================================================================
  function greeting() {
    var hr = new Date().getHours();
    var greet = hr < 12 ? "Good morning" : (hr < 18 ? "Good afternoon" : "Good evening");
    var pr = st.profile || {};
    var plan = (st.fin && st.fin.plan) || null;

    var left = el("div", {}, [
      el("h1", { text: greet }),
      el("p", { style: "font-weight:700;color:#fff;opacity:.96;margin-top:2px", text: fullName() }),
      el("p", { style: "opacity:.85", text: pr.email || P.email || "" }),
      el("div", { class: "cf-row", style: "gap:8px;margin-top:12px" }, [
        el("button", { class: "cf-btn cf-btn-sm", text: "Edit profile", onclick: editProfileModal }),
        el("button", { class: "cf-btn cf-btn-sm", text: "+ Add child", onclick: function () { dependentModal(null); } }),
      ]),
    ]);
    var kids = [left];
    if (plan) {
      var chip = plan.is_trial && plan.trial_days_left != null ? ("🎁 Free week · " + plan.trial_days_left + "d left")
        : (plan.type === "membership" && plan.active ? "⭐ Member" : "Pay as you go");
      kids.push(el("span", { class: "cf-greet-plan", text: chip }));
    }
    return el("div", { class: "cf-greet" }, kids);
  }

  // trial / coverage nudge (→ plan wizard; today /plan)
  function nudgeMaybe(host) {
    var plan = (st.fin && st.fin.plan) || null; if (!plan) return;
    if (plan.is_trial && plan.trial_days_left != null && plan.trial_days_left <= 3) {
      host.appendChild(nudge("Your free week ends in " + plan.trial_days_left + " day" + (plan.trial_days_left === 1 ? "" : "s"),
        "Keep playing — go unlimited with membership, or grab a session pack.", "Choose a plan"));
    } else if (!plan.active && plan.sold) {
      host.appendChild(nudge("Make your court bookings free", "Go unlimited with a membership, or buy a prepaid pack.", "See plans"));
    }
  }
  function nudge(t, s, cta) {
    return el("div", { class: "cf-nudge" }, [
      el("div", {}, [el("div", { class: "cf-nudge-t", text: t }), el("div", { class: "cf-nudge-s", text: s })]),
      el("button", { class: "cf-btn cf-btn-primary", text: cta, onclick: openWizard }),
    ]);
  }

  // ===========================================================================
  // book — service launchers (full-screen /book/<service>) + staff console links
  // ===========================================================================
  function bookCard() {
    var qb = el("div", { class: "cf-card" }, [el("h2", { text: "Book" })]);
    var grid = el("div", { class: "cf-qb" });
    [{ type: "court", ic: "🎾", t: "Book a court", s: "Hard courts + the clay court" },
     { type: "lesson", ic: "🏆", t: "Book a lesson", s: "1:1 or group with a coach" },
     { type: "class", ic: "👥", t: "Attend a class", s: "Squads, Cardio, socials" }
    ].forEach(function (sv) {
      grid.appendChild(el("button", { class: "cf-qb-btn", type: "button", onclick: function () { window.location.href = "/book/" + sv.type; } }, [
        el("span", { class: "cf-qb-ic", text: sv.ic }),
        el("span", {}, [el("span", { class: "cf-qb-t", text: sv.t, style: "display:block" }), el("span", { class: "cf-qb-s", text: sv.s })]),
      ]));
    });
    qb.appendChild(grid);
    return qb;
  }
  function staffCard() {
    var staff = [
      { href: "/coach.html", t: "Coach console", s: "My week & rosters", roles: ["coach", "club_admin", "platform_admin"] },
      { href: "/admin.html", t: "Master diary", s: "Club admin & desk", roles: ["club_admin", "platform_admin"] },
      { href: "/settings.html", t: "Settings", s: "Club, hours, courts, pricing", roles: ["club_admin", "platform_admin"] },
    ].filter(function (l) { return l.roles.indexOf(P.role) >= 0; });
    if (!staff.length) return null;
    var sc = el("div", { class: "cf-card" }, [el("h2", { text: "Manage" })]);
    var sg = el("div", { class: "cf-tiles" });
    staff.forEach(function (l) { sg.appendChild(el("a", { href: l.href, class: "cf-tile" }, [el("div", { class: "cf-tile-t", text: l.t }), el("div", { class: "cf-tile-s", text: l.s })])); });
    sc.appendChild(sg);
    return sc;
  }

  // ===========================================================================
  // my bookings — needs-attention + Upcoming/Past toggle + full per-booking actions
  // ===========================================================================
  function bookingsCard() {
    var card = el("div", { class: "cf-card" }, [el("h2", { text: "My bookings" })]);
    var pending = st.bookings.filter(function (b) { return ["requested", "proposed"].indexOf(b.status) >= 0; });
    if (pending.length) {
      card.appendChild(el("h3", { text: "Needs your attention" }));
      var p = el("div", { class: "cf-list" }); pending.forEach(function (b) { p.appendChild(pendingRow(b)); }); card.appendChild(p);
    }
    // toggle
    var toggle = el("div", { class: "cf-segment", style: "margin:14px 0 8px;max-width:280px" });
    ["upcoming", "past"].forEach(function (v) {
      var b = el("button", { class: st.view === v ? "on" : "", text: v === "upcoming" ? "Upcoming" : "Past",
        onclick: function () { st.view = v; var bs = toggle.querySelectorAll("button"); for (var i = 0; i < bs.length; i++) bs[i].classList.remove("on"); b.classList.add("on"); renderBkList(); } });
      toggle.appendChild(b);
    });
    card.appendChild(toggle);
    card.appendChild(el("div", { id: "home-bk-list" }));
    return card;
  }
  function renderBkList() {
    var box = document.getElementById("home-bk-list"); if (!box) return;
    UI.clear(box);
    var now = Date.now();
    var rows;
    if (st.view === "upcoming") {
      rows = st.bookings.filter(function (b) { return ["held", "confirmed"].indexOf(b.status) >= 0 && new Date(b.ends_at).getTime() >= now; })
        .sort(function (a, b) { return new Date(a.starts_at) - new Date(b.starts_at); });          // nearest first
    } else {
      rows = st.bookings.filter(function (b) { return ["cancelled", "completed", "no_show"].indexOf(b.status) >= 0 || (["held", "confirmed"].indexOf(b.status) >= 0 && new Date(b.ends_at).getTime() < now); })
        .sort(function (a, b) { return new Date(b.starts_at) - new Date(a.starts_at); });          // most recent first
    }
    if (!rows.length) { box.appendChild(el("div", { class: "cf-empty", text: st.view === "upcoming" ? "No upcoming bookings — book above to get started." : "No past bookings yet." })); return; }
    var list = el("div", { class: "cf-list" });
    rows.forEach(function (b) { list.appendChild(bookingRow(b, st.view === "upcoming")); });
    box.appendChild(list);
  }
  function pendingRow(b) {
    var isProposed = b.status === "proposed";
    var sub = isProposed ? ("Coach proposed: " + UI.fmtRange(b.starts_at, b.ends_at)) : ("Requested: " + UI.fmtRange(b.starts_at, b.ends_at) + " · awaiting coach");
    var actions = [];
    if (isProposed) {
      actions.push(el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Accept", onclick: function () { act(window.API.acceptBooking(b.id), "Lesson confirmed."); } }));
      actions.push(el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Decline", onclick: function () { if (confirm("Decline this proposed time?")) act(window.API.declineBooking(b.id, { reason: "member_declined" }), "Declined."); } }));
    } else {
      actions.push(el("button", { class: "cf-btn cf-btn-sm", text: "Withdraw", onclick: function () { if (confirm("Withdraw this lesson request?")) act(window.API.cancelBooking(b.id, { reason: "member_withdraw" }), "Request withdrawn."); } }));
    }
    return el("div", { class: "cf-item" }, [
      el("span", { class: "cf-chip held", text: b.status }),
      el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: b.resource_name || "Lesson" }), el("div", { class: "cf-item-s", text: sub })]),
    ].concat(actions));
  }
  function bookingRow(b, upcoming) {
    var sub = UI.fmtRange(b.starts_at, b.ends_at) + (b.court_name ? " · " + b.court_name : "") + " · " + UI.settlementLabel(b.settlement_mode);
    var kids = [
      el("span", { class: "cf-chip " + b.booking_type, text: b.booking_type }),
      el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: b.resource_name || b.booking_type }), el("div", { class: "cf-item-s", text: sub })]),
      el("span", { class: "cf-chip " + b.status, text: b.status }),
    ];
    var paid = b.order_id && ["online", "at_court", "monthly_account"].indexOf(b.settlement_mode) >= 0;
    if (upcoming) {
      kids.push(el("button", { class: "cf-btn cf-btn-sm", text: "Add to calendar", onclick: function () { addToCalendar(b); } }));
      kids.push(el("button", { class: "cf-btn cf-btn-sm", text: "Reschedule", onclick: function () { rescheduleModal(b); } }));
      kids.push(el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Cancel", onclick: function () { if (confirm("Cancel this booking? Cancellation policy/fees may apply.")) act(window.API.cancelBooking(b.id, { reason: "member_cancel" }), "Cancelled."); } }));
    }
    if (paid) kids.push(el("button", { class: "cf-btn cf-btn-sm", text: "Request refund", onclick: function () { refundModal(b); } }));
    return el("div", { class: "cf-item" }, kids);
  }
  async function act(promise, okMsg) {
    try { await promise; UI.toast(okMsg, "info"); await reloadBookings(); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  async function reloadBookings() {
    try { st.bookings = (await window.API.bookings({ date_from: UI.dateKey(UI.addDays(new Date(), -365)), date_to: UI.dateKey(UI.addDays(new Date(), 90)) })).bookings || []; } catch (e) {}
    var main = document.getElementById("cf-main"); render(main);
  }
  async function addToCalendar(b) {
    try {
      var res = await window.TFAuth.apiFetch("/api/diary/bookings/" + encodeURIComponent(b.id) + "/calendar.ics");
      if (!res || !res.ok) throw new Error("unavailable");
      var url = URL.createObjectURL(new Blob([await res.text()], { type: "text/calendar" }));
      var a = el("a", { href: url, download: "booking.ics" }); document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
    } catch (e) { UI.toast("Couldn't generate the calendar file.", "error"); }
  }
  function rescheduleModal(b) {
    var bg = el("div", { class: "cf-modal-bg" });
    var input = el("input", { class: "cf-input", type: "datetime-local", value: b.starts_at.slice(0, 16) });
    bg.appendChild(el("div", { class: "cf-modal" }, [
      el("h2", { text: "Reschedule" }), el("p", { class: "cf-muted", text: "Pick a new start time. The same duration is kept; conflicts are rejected." }),
      el("div", { class: "cf-field" }, [el("label", { text: "New start" }), input]),
      el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px;gap:8px" }, [
        el("button", { class: "cf-btn", text: "Cancel", onclick: function () { document.body.removeChild(bg); } }),
        el("button", { class: "cf-btn cf-btn-primary", text: "Save", onclick: function () { doReschedule(b, input.value, bg); } }),
      ]),
    ]));
    document.body.appendChild(bg);
  }
  async function doReschedule(b, v, bg) {
    if (!v) return;
    var durMs = new Date(b.ends_at) - new Date(b.starts_at);
    var ns = new Date(v), ne = new Date(ns.getTime() + durMs);
    try { await window.API.rescheduleBooking(b.id, { starts_at: ns.toISOString(), ends_at: ne.toISOString(), scope: "this" }); document.body.removeChild(bg); UI.toast("Rescheduled.", "info"); await reloadBookings(); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  function refundModal(b) {
    if (!b.order_id) { UI.toast("This booking has no payment to refund.", "error"); return; }
    var bg = el("div", { class: "cf-modal-bg" });
    var reason = el("textarea", { class: "cf-input", rows: "3", placeholder: "Tell the club why you're requesting a refund (optional)" });
    var save = el("button", { class: "cf-btn cf-btn-primary", text: "Send request" });
    save.addEventListener("click", function () { submitRefund(b, reason.value.trim() || null, save, bg); });
    bg.appendChild(el("div", { class: "cf-modal" }, [
      el("h2", { text: "Request a refund" }),
      el("p", { class: "cf-muted cf-tiny", text: (b.resource_name || b.booking_type) + " · " + UI.fmtRange(b.starts_at, b.ends_at) + ". The club will review your request." }),
      field("Reason", reason),
      el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px;gap:8px" }, [
        el("button", { class: "cf-btn", text: "Cancel", onclick: function () { document.body.removeChild(bg); } }), save,
      ]),
    ]));
    document.body.appendChild(bg);
  }
  async function submitRefund(b, reason, btn, bg) {
    btn.disabled = true; var o = btn.textContent; btn.textContent = "Sending…";
    try { await window.API.requestRefund({ order_id: b.order_id, reason: reason }); document.body.removeChild(bg); UI.toast("Refund requested — the club will review it. Track it under Account.", "info"); }
    catch (e) { btn.disabled = false; btn.textContent = o; UI.toast(UI.errMsg(e), "error"); }
  }

  // ===========================================================================
  // profile + family popups (edit profile from the greeting)
  // ===========================================================================
  function editProfileModal() {
    var pr = st.profile || {};
    var bg = el("div", { class: "cf-modal-bg" });
    var fn = inp(pr.first_name), sn = inp(pr.surname), ph = inp(pr.phone, { type: "tel" }), dob = inp(pr.dob, { type: "date" });
    var a1 = inp(pr.address_line1), a2 = inp(pr.address_line2), city = inp(pr.city), pc = inp(pr.postal_code), country = inp(pr.country);
    var ecn = inp(pr.emergency_contact_name), ecp = inp(pr.emergency_contact_phone, { type: "tel" });
    var consent = el("input", { type: "checkbox" }); consent.checked = !!pr.marketing_opt_in; consent.style.width = "auto";
    var consentLbl = el("label", { class: "cf-row", style: "cursor:pointer;gap:10px;margin-top:10px" }, [consent, el("span", { style: "font-weight:600", text: "Send me news, offers and club updates by email" })]);
    var save = el("button", { class: "cf-btn cf-btn-primary", text: "Save changes" });
    var modal = el("div", { class: "cf-modal cf-modal-lg" }, [
      el("h2", { text: "Edit profile" }),
      field("Email", inp(pr.email, { type: "email", disabled: "disabled" }), "This is your login — contact the club to change it."),
      el("div", { class: "cf-grid cf-grid-2" }, [field("First name", fn), field("Surname", sn), field("Phone", ph), field("Date of birth", dob)]),
      el("h3", { text: "Address", style: "margin-top:14px" }),
      field("Address line 1", a1), field("Address line 2", a2),
      el("div", { class: "cf-grid cf-grid-2" }, [field("City", city), field("Postal code", pc)]),
      field("Country", country),
      el("h3", { text: "Emergency contact", style: "margin-top:14px" }),
      el("div", { class: "cf-grid cf-grid-2" }, [field("Contact name", ecn), field("Contact phone", ecp)]),
      consentLbl,
      el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:14px;gap:8px" }, [
        el("button", { class: "cf-btn", text: "Cancel", onclick: function () { document.body.removeChild(bg); } }), save,
      ]),
    ]);
    save.addEventListener("click", function () {
      saveProfile({
        first_name: fn.value.trim(), surname: sn.value.trim(), phone: ph.value.trim(), dob: dob.value || null,
        address_line1: a1.value.trim(), address_line2: a2.value.trim(), city: city.value.trim(), postal_code: pc.value.trim(), country: country.value.trim(),
        emergency_contact_name: ecn.value.trim(), emergency_contact_phone: ecp.value.trim(), marketing_opt_in: consent.checked,
      }, save, bg);
    });
    bg.appendChild(modal);
    document.body.appendChild(bg);
  }
  async function saveProfile(body, btn, bg) {
    btn.disabled = true; var o = btn.textContent; btn.textContent = "Saving…";
    try { st.profile = await window.API.patchProfile(body); document.body.removeChild(bg); UI.toast("Profile saved.", "info"); render(document.getElementById("cf-main")); }
    catch (e) {
      btn.disabled = false; btn.textContent = o;
      var fields = e && e.body && e.body.fields;
      if (e && e.status === 422 && fields) { var f = Object.keys(fields)[0]; UI.toast("Please check " + f.replace(/_/g, " ") + ": " + fields[f], "error"); }
      else UI.toast(UI.errMsg(e), "error");
    }
  }
  function dependentModal(dep) {
    var editing = !!dep, bg = el("div", { class: "cf-modal-bg" });
    var fn = inp(dep && dep.first_name, { placeholder: "First name" }), sn = inp(dep && dep.surname, { placeholder: "Surname (optional)" });
    var dob = inp(dep && dep.dob, { type: "date" });
    var rel = el("select", { class: "cf-select" });
    [["child", "Child"], ["spouse", "Spouse"], ["partner", "Partner"], ["other", "Other"]].forEach(function (o) { rel.appendChild(el("option", { value: o[0], text: o[1], selected: (dep && dep.relationship === o[0]) ? "selected" : null })); });
    var notes = inp(dep && dep.notes, { placeholder: "Notes (optional)" });
    var save = el("button", { class: "cf-btn cf-btn-primary", text: editing ? "Save" : "Add child" });
    save.addEventListener("click", function () {
      var body = { first_name: fn.value.trim(), surname: sn.value.trim() || null, dob: dob.value || null, relationship: rel.value, notes: notes.value.trim() || null };
      if (!body.first_name) { UI.toast("First name is required.", "error"); return; }
      saveDependent(dep, body, save, bg);
    });
    var children = [
      el("h2", { text: editing ? "Edit family member" : "Add a child" }),
      el("p", { class: "cf-muted cf-tiny", text: "Children don't need a login — you book and pay for them." }),
      field("First name", fn), field("Surname", sn),
      el("div", { class: "cf-grid cf-grid-2" }, [field("Date of birth", dob), field("Relationship", rel)]),
      field("Notes", notes),
    ];
    if (editing) children.push(el("div", { style: "margin-top:6px" }, [el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Remove this child", onclick: function () { removeDependent(dep, bg); } })]));
    children.push(el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px;gap:8px" }, [el("button", { class: "cf-btn", text: "Cancel", onclick: function () { document.body.removeChild(bg); } }), save]));
    bg.appendChild(el("div", { class: "cf-modal" }, children));
    document.body.appendChild(bg);
  }
  async function saveDependent(dep, body, btn, bg) {
    btn.disabled = true; var o = btn.textContent; btn.textContent = "Saving…";
    try { if (dep) await window.API.patchDependent(dep.id, body); else await window.API.addDependent(body); document.body.removeChild(bg); await reloadDeps(); UI.toast(dep ? "Saved." : "Child added.", "info"); }
    catch (e) { btn.disabled = false; btn.textContent = o; UI.toast(UI.errMsg(e), "error"); }
  }
  async function removeDependent(dep, bg) {
    if (!confirm("Remove " + (dep.first_name || "this family member") + "?")) return;
    try { await window.API.removeDependent(dep.id); if (bg && bg.parentNode) document.body.removeChild(bg); await reloadDeps(); UI.toast("Removed.", "info"); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  async function reloadDeps() {
    try { st.deps = (await window.API.dependents()).dependents || []; } catch (e) {}
  }

  // ===========================================================================
  // render + boot
  // ===========================================================================
  function render(main) {
    UI.clear(main);
    main.appendChild(greeting());
    nudgeMaybe(main);
    main.appendChild(bookCard());
    var sc = staffCard(); if (sc) main.appendChild(sc);
    main.appendChild(bookingsCard());
    renderBkList();
  }

  async function loadAll() {
    await Promise.all([
      window.API.getProfile().then(function (r) { st.profile = r; }, function () {}),
      window.API.dependents().then(function (r) { st.deps = r.dependents || []; }, function () {}),
      window.API.financials().then(function (r) { st.fin = r; }, function () {}),
      window.API.bookings({ date_from: UI.dateKey(UI.addDays(new Date(), -365)), date_to: UI.dateKey(UI.addDays(new Date(), 90)) }).then(function (r) { st.bookings = r.bookings || []; }, function () {}),
      window.TFAuth.apiJSON("/api/billing/bundles/wallets").then(function (r) { st.wallets = (r && r.wallets) || []; }, function () {}),
    ]);
  }

  window.Home = {
    start: async function (principal) {
      UI = window.UI; el = UI.el; P = principal;
      var main = document.getElementById("cf-main");
      UI.clear(main); main.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
      await loadAll();
      render(main);
      // The "money moment": if there's no coverage and no credits, open the plan wizard.
      if (window.PlanWizard) window.PlanWizard.maybeAutoOpen(st.fin, st.wallets);
    },
  };
})();

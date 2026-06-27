// home.js — the client ONE-PAGE Home (front-end redesign, 2026-06-27).
// A single page with a sticky chip nav → four sections: Book · My Bookings · Profile · Money.
// Replaces the old launchpad that linked out to /my, /account, /plan (those stay as fallback).
// Self-contained: composes the proven /api/me + /api/diary calls into one clean, simple page.
// Booking itself stays full-screen (/book/<service>); this page launches it.
(function () {
  var UI, el, P;
  var st = { fin: null, profile: null, deps: [], orders: [], refunds: [], bookings: [] };

  var SECTIONS = [
    { k: "book", t: "Book" },
    { k: "bookings", t: "My bookings" },
    { k: "profile", t: "Profile" },
    { k: "money", t: "Money" },
  ];

  // ---- shell: sticky chip nav + section hosts -------------------------------
  function buildShell(main) {
    UI.clear(main);
    var chips = el("div", { class: "cf-chipnav" });
    SECTIONS.forEach(function (s) {
      var a = el("a", { class: "cf-chip-nav", href: "#sec-" + s.k, text: s.t, "data-sec": s.k });
      a.addEventListener("click", function (ev) { ev.preventDefault(); go(s.k); });
      chips.appendChild(a);
    });
    main.appendChild(chips);
    SECTIONS.forEach(function (s) {
      main.appendChild(el("section", { id: "sec-" + s.k, class: "cf-section" },
        [el("div", { class: "cf-loading", text: "Loading…" })]));
    });
    setActive("book");
  }
  function go(k) {
    var node = sec(k);
    if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
    setActive(k);
  }
  function setActive(k) {
    var links = document.querySelectorAll(".cf-chip-nav");
    for (var i = 0; i < links.length; i++) {
      links[i].classList.toggle("active", links[i].getAttribute("data-sec") === k);
    }
  }
  function sec(k) { return document.getElementById("sec-" + k); }

  // ===========================================================================
  // 1. BOOK — greeting + plan chip + nudge + service launchers (+ staff links)
  // ===========================================================================
  function renderBook(host) {
    UI.clear(host);
    var plan = (st.fin && st.fin.plan) || null;
    var firstName = (P.email || "").split("@")[0];
    var hr = new Date().getHours();
    var greet = hr < 12 ? "Good morning" : (hr < 18 ? "Good afternoon" : "Good evening");

    var planChip = "";
    if (plan) {
      if (plan.is_trial && plan.trial_days_left != null) planChip = "🎁 Free week · " + plan.trial_days_left + "d left";
      else if (plan.type === "membership" && plan.active) planChip = "⭐ Member";
      else planChip = "Pay as you go";
    }
    var greetKids = [el("div", {}, [
      el("h1", { text: greet + (firstName ? ", " + firstName : "") }),
      el("p", { text: "Book a court, a lesson or a class." }),
    ])];
    if (planChip) greetKids.push(el("span", { class: "cf-greet-plan", text: planChip }));
    host.appendChild(el("div", { class: "cf-greet" }, greetKids));

    // trial / coverage nudge → the plan wizard (today: /plan; becomes the wizard next increment)
    if (plan) {
      if (plan.is_trial && plan.trial_days_left != null && plan.trial_days_left <= 3) {
        host.appendChild(nudge("Your free week ends in " + plan.trial_days_left + " day" + (plan.trial_days_left === 1 ? "" : "s"),
          "Keep playing — go unlimited with membership, or grab a session pack.", "Choose a plan"));
      } else if (!plan.active && plan.sold) {
        host.appendChild(nudge("Make your court bookings free",
          "Go unlimited with a membership, or buy a prepaid pack.", "See plans"));
      }
    }

    var qb = el("div", { class: "cf-card" }, [el("h2", { text: "Book" })]);
    var grid = el("div", { class: "cf-qb" });
    [{ type: "court", ic: "🎾", t: "Book a court", s: "Hard courts + the clay court" },
     { type: "lesson", ic: "🏆", t: "Book a lesson", s: "1:1 or group with a coach" },
     { type: "class", ic: "👥", t: "Attend a class", s: "Squads, Cardio, socials" }
    ].forEach(function (sv) {
      grid.appendChild(el("button", { class: "cf-qb-btn", type: "button",
        onclick: function () { window.location.href = "/book/" + sv.type; } }, [
        el("span", { class: "cf-qb-ic", text: sv.ic }),
        el("span", {}, [
          el("span", { class: "cf-qb-t", text: sv.t, style: "display:block" }),
          el("span", { class: "cf-qb-s", text: sv.s }),
        ]),
      ]));
    });
    qb.appendChild(grid);
    host.appendChild(qb);

    // staff get quick links to their consoles (the Home page itself is the same for everyone)
    var staff = [
      { href: "/coach.html", t: "Coach console", s: "My week & rosters", roles: ["coach", "club_admin", "platform_admin"] },
      { href: "/admin.html", t: "Master diary", s: "Club admin & desk", roles: ["club_admin", "platform_admin"] },
      { href: "/settings.html", t: "Settings", s: "Club, hours, courts, pricing", roles: ["club_admin", "platform_admin"] },
    ].filter(function (l) { return l.roles.indexOf(P.role) >= 0; });
    if (staff.length) {
      var sc = el("div", { class: "cf-card" }, [el("h2", { text: "Manage" })]);
      var sg = el("div", { class: "cf-tiles" });
      staff.forEach(function (l) {
        sg.appendChild(el("a", { href: l.href, class: "cf-tile" }, [
          el("div", { class: "cf-tile-t", text: l.t }),
          el("div", { class: "cf-tile-s", text: l.s }),
        ]));
      });
      sc.appendChild(sg); host.appendChild(sc);
    }
  }
  function nudge(t, s, cta) {
    return el("div", { class: "cf-nudge" }, [
      el("div", {}, [el("div", { class: "cf-nudge-t", text: t }), el("div", { class: "cf-nudge-s", text: s })]),
      el("a", { class: "cf-btn cf-btn-primary", href: "/plan", text: cta }),
    ]);
  }

  // ===========================================================================
  // 2. MY BOOKINGS — needs-attention / upcoming / past (was my.js)
  // ===========================================================================
  function renderBookings(host) {
    UI.clear(host);
    var card = el("div", { class: "cf-card" }, [el("h2", { text: "My bookings" })]);
    host.appendChild(card);
    var b = st.bookings;
    var pending = b.filter(function (x) { return ["requested", "proposed"].indexOf(x.status) >= 0; });
    var active = b.filter(function (x) { return ["held", "confirmed"].indexOf(x.status) >= 0; });
    var past = b.filter(function (x) { return ["cancelled", "completed", "no_show"].indexOf(x.status) >= 0; });
    if (!b.length) { card.appendChild(el("div", { class: "cf-empty", text: "No bookings yet — book above to get started." })); return; }
    if (pending.length) {
      card.appendChild(el("h3", { text: "Needs your attention" }));
      var p = el("div", { class: "cf-list" }); pending.forEach(function (x) { p.appendChild(pendingRow(x)); }); card.appendChild(p);
    }
    if (active.length) {
      card.appendChild(el("h3", { text: "Upcoming", style: pending.length ? "margin-top:16px" : "" }));
      var l = el("div", { class: "cf-list" }); active.forEach(function (x) { l.appendChild(bookingRow(x, true)); }); card.appendChild(l);
    }
    if (past.length) {
      card.appendChild(el("h3", { text: "Past & cancelled", style: "margin-top:16px" }));
      var pl = el("div", { class: "cf-list" }); past.forEach(function (x) { pl.appendChild(bookingRow(x, false)); }); card.appendChild(pl);
    }
  }
  function pendingRow(b) {
    var isProposed = b.status === "proposed";
    var sub = isProposed ? ("Coach proposed: " + UI.fmtRange(b.starts_at, b.ends_at))
      : ("Requested: " + UI.fmtRange(b.starts_at, b.ends_at) + " · awaiting coach");
    var actions = [];
    if (isProposed) {
      actions.push(el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Accept", onclick: function () { act(window.API.acceptBooking(b.id), "Lesson confirmed."); } }));
      actions.push(el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Decline", onclick: function () { if (confirm("Decline this proposed time?")) act(window.API.declineBooking(b.id, { reason: "member_declined" }), "Declined."); } }));
    } else {
      actions.push(el("button", { class: "cf-btn cf-btn-sm", text: "Withdraw", onclick: function () { if (confirm("Withdraw this lesson request?")) act(window.API.cancelBooking(b.id, { reason: "member_withdraw" }), "Request withdrawn."); } }));
    }
    return el("div", { class: "cf-item" }, [
      el("span", { class: "cf-chip held", text: b.status }),
      el("div", { class: "cf-item-main" }, [
        el("div", { class: "cf-item-t", text: b.resource_name || "Lesson" }),
        el("div", { class: "cf-item-s", text: sub }),
      ]),
    ].concat(actions));
  }
  function bookingRow(b, actionable) {
    var sub = UI.fmtRange(b.starts_at, b.ends_at) + (b.court_name ? " · " + b.court_name : "")
      + " · " + UI.settlementLabel(b.settlement_mode);
    var kids = [
      el("span", { class: "cf-chip " + b.booking_type, text: b.booking_type }),
      el("div", { class: "cf-item-main" }, [
        el("div", { class: "cf-item-t", text: b.resource_name || b.booking_type }),
        el("div", { class: "cf-item-s", text: sub }),
      ]),
      el("span", { class: "cf-chip " + b.status, text: b.status }),
    ];
    if (actionable) {
      kids.push(el("button", { class: "cf-btn cf-btn-sm", text: "Add to calendar", onclick: function () { addToCalendar(b); } }));
      kids.push(el("button", { class: "cf-btn cf-btn-sm", text: "Reschedule", onclick: function () { rescheduleModal(b); } }));
      kids.push(el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Cancel", onclick: function () { if (confirm("Cancel this booking? Cancellation policy/fees may apply.")) act(window.API.cancelBooking(b.id, { reason: "member_cancel" }), "Cancelled."); } }));
    }
    return el("div", { class: "cf-item" }, kids);
  }
  async function act(promise, okMsg) {
    try { await promise; UI.toast(okMsg, "info"); await reloadBookings(); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  async function addToCalendar(b) {
    try {
      var res = await window.TFAuth.apiFetch("/api/diary/bookings/" + encodeURIComponent(b.id) + "/calendar.ics");
      if (!res || !res.ok) throw new Error("unavailable");
      var url = URL.createObjectURL(new Blob([await res.text()], { type: "text/calendar" }));
      var a = el("a", { href: url, download: "booking.ics" });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
    } catch (e) { UI.toast("Couldn't generate the calendar file.", "error"); }
  }
  function rescheduleModal(b) {
    var bg = el("div", { class: "cf-modal-bg" });
    var input = el("input", { class: "cf-input", type: "datetime-local", value: b.starts_at.slice(0, 16) });
    bg.appendChild(el("div", { class: "cf-modal" }, [
      el("h2", { text: "Reschedule" }),
      el("p", { class: "cf-muted", text: "Pick a new start time. The same duration is kept; conflicts are rejected." }),
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
    try {
      await window.API.rescheduleBooking(b.id, { starts_at: ns.toISOString(), ends_at: ne.toISOString(), scope: "this" });
      document.body.removeChild(bg); UI.toast("Rescheduled.", "info"); await reloadBookings();
    } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  async function reloadBookings() {
    try {
      var r = await window.API.bookings({ date_from: UI.dateKey(UI.addDays(new Date(), -1)), date_to: UI.dateKey(UI.addDays(new Date(), 90)) });
      st.bookings = r.bookings || [];
    } catch (e) { st.bookings = []; }
    renderBookings(sec("bookings"));
  }

  // ===========================================================================
  // 3. PROFILE — details + family (was account.js Profile + Family)
  // ===========================================================================
  function field(label, input, hint) {
    var kids = [el("label", { text: label }), input];
    if (hint) kids.push(el("div", { class: "cf-pref-note", text: hint }));
    return el("div", { class: "cf-field" }, kids);
  }
  function inp(value, attrs) { return el("input", Object.assign({ class: "cf-input", value: value == null ? "" : value }, attrs || {})); }

  function renderProfile(host) {
    UI.clear(host);
    var pr = st.profile || {};
    var card = el("div", { class: "cf-card" }, [el("h2", { text: "Profile" }), el("h3", { text: "Your details" })]);
    var emailIn = inp(pr.email, { type: "email", disabled: "disabled" });
    card.appendChild(field("Email", emailIn, "This is your login — contact the club to change it."));
    var fn = inp(pr.first_name), sn = inp(pr.surname), ph = inp(pr.phone, { type: "tel" }), dob = inp(pr.dob, { type: "date" });
    card.appendChild(el("div", { class: "cf-grid cf-grid-2" }, [field("First name", fn), field("Surname", sn), field("Phone", ph), field("Date of birth", dob)]));
    card.appendChild(el("h3", { text: "Address", style: "margin-top:18px" }));
    var a1 = inp(pr.address_line1), a2 = inp(pr.address_line2), city = inp(pr.city), pc = inp(pr.postal_code), country = inp(pr.country);
    card.appendChild(field("Address line 1", a1)); card.appendChild(field("Address line 2", a2));
    card.appendChild(el("div", { class: "cf-grid cf-grid-2" }, [field("City", city), field("Postal code", pc)]));
    card.appendChild(field("Country", country));
    card.appendChild(el("h3", { text: "Emergency contact", style: "margin-top:18px" }));
    var ecn = inp(pr.emergency_contact_name), ecp = inp(pr.emergency_contact_phone, { type: "tel" });
    card.appendChild(el("div", { class: "cf-grid cf-grid-2" }, [field("Contact name", ecn), field("Contact phone", ecp)]));
    var consentLbl = el("label", { class: "cf-row", style: "cursor:pointer;gap:10px;margin-top:14px" });
    var consentCb = el("input", { type: "checkbox" }); consentCb.checked = !!pr.marketing_opt_in; consentCb.style.width = "auto";
    consentLbl.appendChild(consentCb);
    consentLbl.appendChild(el("span", { style: "font-weight:600", text: "Send me news, offers and club updates by email" }));
    card.appendChild(consentLbl);
    var save = el("button", { class: "cf-btn cf-btn-primary cf-btn-lg", style: "margin-top:18px", text: "Save changes" });
    save.addEventListener("click", function () {
      saveProfile({
        first_name: fn.value.trim(), surname: sn.value.trim(), phone: ph.value.trim(), dob: dob.value || null,
        address_line1: a1.value.trim(), address_line2: a2.value.trim(), city: city.value.trim(),
        postal_code: pc.value.trim(), country: country.value.trim(),
        emergency_contact_name: ecn.value.trim(), emergency_contact_phone: ecp.value.trim(),
        marketing_opt_in: consentCb.checked,
      }, save);
    });
    card.appendChild(el("div", { style: "margin-top:4px" }, [save]));
    host.appendChild(card);
    host.appendChild(familyCard());
  }
  async function saveProfile(body, btn) {
    btn.disabled = true; var o = btn.textContent; btn.textContent = "Saving…";
    try { st.profile = await window.API.patchProfile(body); UI.toast("Profile saved.", "info"); renderProfile(sec("profile")); }
    catch (e) {
      btn.disabled = false; btn.textContent = o;
      var fields = e && e.body && e.body.fields;
      if (e && e.status === 422 && fields) { var f = Object.keys(fields)[0]; UI.toast("Please check " + f.replace(/_/g, " ") + ": " + fields[f], "error"); }
      else UI.toast(UI.errMsg(e), "error");
    }
  }
  function ageFromDob(dob) {
    if (!dob) return null; var d = new Date(dob); if (isNaN(d)) return null; var n = new Date();
    var a = n.getFullYear() - d.getFullYear(); var m = n.getMonth() - d.getMonth();
    if (m < 0 || (m === 0 && n.getDate() < d.getDate())) a--; return a >= 0 ? a : null;
  }
  function familyCard() {
    var card = el("div", { class: "cf-card" });
    card.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center" }, [
      el("h3", { text: "Children & family" }),
      el("button", { class: "cf-btn cf-btn-primary", text: "+ Add child", onclick: function () { dependentModal(null); } }),
    ]));
    card.appendChild(el("p", { class: "cf-muted cf-tiny", text: "Add a child to book on their behalf — bookings stay on your account." }));
    if (!st.deps.length) { card.appendChild(el("div", { class: "cf-empty", text: "No children added yet." })); return card; }
    var list = el("div", { class: "cf-list", style: "margin-top:10px" });
    st.deps.forEach(function (d) {
      var age = ageFromDob(d.dob), sub = [];
      if (d.relationship && d.relationship !== "child") sub.push(d.relationship);
      if (age != null) sub.push(age + " yrs");
      list.appendChild(el("div", { class: "cf-item" }, [
        el("span", { class: "cf-chip", text: "👤" }),
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: (d.first_name || "") + " " + (d.surname || "") }),
          el("div", { class: "cf-item-s", text: sub.join(" · ") || "Family member" }),
        ]),
        el("div", { class: "cf-row", style: "gap:6px" }, [
          el("button", { class: "cf-btn cf-btn-sm", text: "Edit", onclick: function () { dependentModal(d); } }),
          el("button", { class: "cf-btn cf-btn-sm", text: "Remove", onclick: function () { removeDependent(d); } }),
        ]),
      ]));
    });
    card.appendChild(list); return card;
  }
  function dependentModal(dep) {
    var editing = !!dep, bg = el("div", { class: "cf-modal-bg" });
    var fn = inp(dep && dep.first_name, { placeholder: "First name" }), sn = inp(dep && dep.surname, { placeholder: "Surname (optional)" });
    var dob = inp(dep && dep.dob, { type: "date" });
    var rel = el("select", { class: "cf-select" });
    [["child", "Child"], ["spouse", "Spouse"], ["partner", "Partner"], ["other", "Other"]].forEach(function (o) {
      rel.appendChild(el("option", { value: o[0], text: o[1], selected: (dep && dep.relationship === o[0]) ? "selected" : null }));
    });
    var notes = inp(dep && dep.notes, { placeholder: "Notes (optional)" });
    var save = el("button", { class: "cf-btn cf-btn-primary", text: editing ? "Save" : "Add child" });
    save.addEventListener("click", function () {
      var body = { first_name: fn.value.trim(), surname: sn.value.trim() || null, dob: dob.value || null, relationship: rel.value, notes: notes.value.trim() || null };
      if (!body.first_name) { UI.toast("First name is required.", "error"); return; }
      saveDependent(dep, body, save, bg);
    });
    bg.appendChild(el("div", { class: "cf-modal" }, [
      el("h2", { text: editing ? "Edit family member" : "Add a child" }),
      el("p", { class: "cf-muted cf-tiny", text: "Children don't need a login — you book and pay for them." }),
      field("First name", fn), field("Surname", sn),
      el("div", { class: "cf-grid cf-grid-2" }, [field("Date of birth", dob), field("Relationship", rel)]),
      field("Notes", notes),
      el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px;gap:8px" }, [
        el("button", { class: "cf-btn", text: "Cancel", onclick: function () { document.body.removeChild(bg); } }), save,
      ]),
    ]));
    document.body.appendChild(bg);
  }
  async function saveDependent(dep, body, btn, bg) {
    btn.disabled = true; var o = btn.textContent; btn.textContent = "Saving…";
    try {
      if (dep) await window.API.patchDependent(dep.id, body); else await window.API.addDependent(body);
      document.body.removeChild(bg); await reloadDeps(); UI.toast(dep ? "Saved." : "Child added.", "info");
    } catch (e) { btn.disabled = false; btn.textContent = o; UI.toast(UI.errMsg(e), "error"); }
  }
  async function removeDependent(dep) {
    if (!confirm("Remove " + (dep.first_name || "this family member") + "?")) return;
    try { await window.API.removeDependent(dep.id); await reloadDeps(); UI.toast("Removed.", "info"); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  async function reloadDeps() {
    try { st.deps = (await window.API.dependents()).dependents || []; } catch (e) { st.deps = []; }
    renderProfile(sec("profile"));
  }

  // ===========================================================================
  // 4. MONEY — plan + usage + spend + payments + refunds (was account.js Financials)
  // ===========================================================================
  function money(minor, ccy) { return UI.money(minor, ccy || (st.fin && st.fin.currency)); }
  function monthLabel(p) {
    if (!p) return p; var x = p.split("-"); if (x.length !== 2) return p;
    var n = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return (n[parseInt(x[1], 10) - 1] || x[1]) + " " + x[0];
  }
  function tile(t, s) { return el("div", { class: "cf-tile", style: "cursor:default" }, [el("div", { class: "cf-tile-t", text: t }), el("div", { class: "cf-tile-s", text: s })]); }

  function renderMoney(host) {
    UI.clear(host);
    var f = st.fin || {}, ccy = f.currency || "ZAR", plan = f.plan || {};
    host.appendChild(el("div", { class: "cf-card" }, [el("h2", { text: "Money" })]));

    var planCard = el("div", { class: "cf-card" });
    planCard.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center" }, [
      el("h3", { text: "Your plan" }),
      el("span", { class: "cf-chip", text: plan.active ? "Membership" : "Pay as you go" }),
    ]));
    planCard.appendChild(el("div", { style: "margin:6px 0 12px" }, [
      el("div", { class: "cf-muted", text: (plan.active && plan.current_period_end) ? ("Renews / expires " + plan.current_period_end) : "You pay per booking. A membership makes court bookings free." }),
    ]));
    planCard.appendChild(el("a", { class: "cf-btn cf-btn-primary", href: "/plan", text: plan.active ? "Manage plan" : "Choose a plan" }));
    host.appendChild(planCard);

    var u = f.usage_this_month || {};
    host.appendChild(el("div", { class: "cf-card" }, [
      el("h3", { text: "Usage this month" }),
      el("div", { class: "cf-tiles", style: "margin-top:10px" }, [tile(String(u.court || 0), "Courts"), tile(String(u.lesson || 0), "Lessons"), tile(String(u["class"] || 0), "Classes")]),
    ]));

    var spend = f.spend || {};
    var spendCard = el("div", { class: "cf-card" }, [
      el("div", { class: "cf-row", style: "justify-content:space-between;align-items:baseline" }, [
        el("h3", { text: "Spend" }),
        el("div", { style: "font-weight:800;font-size:1.3rem", text: money(spend.this_month_minor || 0, ccy) }),
      ]),
      el("p", { class: "cf-muted cf-tiny", text: "Paid this month" }),
    ]);
    var hist = (spend.history || []).filter(function (h) { return h.paid_minor > 0 || h.orders > 0; });
    if (hist.length) {
      var hl = el("div", { class: "cf-list", style: "margin-top:8px" });
      hist.forEach(function (h) {
        hl.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: monthLabel(h.period) }), el("div", { class: "cf-item-s", text: h.orders + (h.orders === 1 ? " payment" : " payments") })]),
          el("div", { style: "font-weight:700", text: money(h.paid_minor, ccy) }),
        ]));
      });
      spendCard.appendChild(hl);
    }
    var acct = f.account || {};
    if (acct.balance_minor) {
      spendCard.appendChild(el("div", { class: "cf-row cf-muted", style: "margin-top:10px;justify-content:space-between" }, [
        el("span", { text: "Account balance (pay end of month)" }), el("span", { style: "font-weight:700", text: money(acct.balance_minor, ccy) }),
      ]));
    }
    host.appendChild(spendCard);

    var ordCard = el("div", { class: "cf-card" }, [el("h3", { text: "Recent payments" })]);
    if (!st.orders.length) { ordCard.appendChild(el("div", { class: "cf-empty", text: "No payments yet." })); }
    else {
      var ol = el("div", { class: "cf-list", style: "margin-top:10px" });
      st.orders.forEach(function (o) {
        var right;
        if (o.refundable) right = el("button", { class: "cf-btn cf-btn-sm", text: "Request refund", onclick: function () { refundModal(o); } });
        else if (o.has_open_refund) right = el("span", { class: "cf-chip", text: "Refund " + (o.refund_status || "requested") });
        else if (o.status === "refunded") right = el("span", { class: "cf-chip", text: "Refunded" });
        else right = el("span", { class: "cf-tiny cf-muted", text: "" });
        ol.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: o.description || "Payment" }), el("div", { class: "cf-item-s", text: (o.created_at ? o.created_at.slice(0, 10) : "") + " · " + money(o.amount_minor, o.currency_code) })]),
          right,
        ]));
      });
      ordCard.appendChild(ol);
    }
    host.appendChild(ordCard);

    if (st.refunds.length) {
      var rc = el("div", { class: "cf-card" }, [el("h3", { text: "Refund requests" })]);
      var rl = el("div", { class: "cf-list", style: "margin-top:10px" });
      st.refunds.forEach(function (r) {
        var actions = [el("span", { class: "cf-chip", text: r.status })];
        if (r.status === "pending") actions.push(el("button", { class: "cf-btn cf-btn-sm", text: "Withdraw", onclick: function () { cancelRefund(r); } }));
        rl.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: money(r.amount_minor, ccy) + (r.reason ? " — " + r.reason : "") }), el("div", { class: "cf-item-s", text: r.created_at ? r.created_at.slice(0, 10) : "" })]),
          el("div", { class: "cf-row", style: "gap:6px;align-items:center" }, actions),
        ]));
      });
      rc.appendChild(rl); host.appendChild(rc);
    }
  }
  function refundModal(order) {
    var bg = el("div", { class: "cf-modal-bg" });
    var reason = el("textarea", { class: "cf-input", rows: "3", placeholder: "Tell the club why you're requesting a refund (optional)" });
    var save = el("button", { class: "cf-btn cf-btn-primary", text: "Send request" });
    save.addEventListener("click", function () { submitRefund(order, reason.value.trim() || null, save, bg); });
    bg.appendChild(el("div", { class: "cf-modal" }, [
      el("h2", { text: "Request a refund" }),
      el("p", { class: "cf-muted cf-tiny", text: (order.description || "Payment") + " · " + money(order.amount_minor, order.currency_code) + ". The club will review your request." }),
      field("Reason", reason),
      el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:12px;gap:8px" }, [
        el("button", { class: "cf-btn", text: "Cancel", onclick: function () { document.body.removeChild(bg); } }), save,
      ]),
    ]));
    document.body.appendChild(bg);
  }
  async function submitRefund(order, reason, btn, bg) {
    btn.disabled = true; var o = btn.textContent; btn.textContent = "Sending…";
    try { await window.API.requestRefund({ order_id: order.id, reason: reason }); document.body.removeChild(bg); await reloadMoney(); UI.toast("Refund requested — the club will review it.", "info"); }
    catch (e) { btn.disabled = false; btn.textContent = o; UI.toast(UI.errMsg(e), "error"); }
  }
  async function cancelRefund(r) {
    if (!confirm("Withdraw this refund request?")) return;
    try { await window.API.cancelRefundRequest(r.id); await reloadMoney(); UI.toast("Request withdrawn.", "info"); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  async function reloadMoney() {
    try { st.fin = await window.API.financials(); } catch (e) {}
    try { st.orders = (await window.API.myOrders()).orders || []; } catch (e) { st.orders = []; }
    try { st.refunds = (await window.API.refundRequests()).requests || []; } catch (e) { st.refunds = []; }
    renderMoney(sec("money")); renderBook(sec("book"));
  }

  // ===========================================================================
  // boot — load everything once, render every section
  // ===========================================================================
  async function loadAll() {
    var jobs = [
      window.API.financials().then(function (r) { st.fin = r; }, function () {}),
      window.API.getProfile().then(function (r) { st.profile = r; }, function () {}),
      window.API.dependents().then(function (r) { st.deps = r.dependents || []; }, function () {}),
      window.API.bookings({ date_from: UI.dateKey(UI.addDays(new Date(), -1)), date_to: UI.dateKey(UI.addDays(new Date(), 90)) }).then(function (r) { st.bookings = r.bookings || []; }, function () {}),
      window.API.myOrders().then(function (r) { st.orders = r.orders || []; }, function () {}),
      window.API.refundRequests().then(function (r) { st.refunds = r.requests || []; }, function () {}),
    ];
    await Promise.all(jobs);
  }

  window.Home = {
    start: async function (principal) {
      UI = window.UI; el = UI.el; P = principal;
      var main = document.getElementById("cf-main");
      buildShell(main);
      await loadAll();
      renderBook(sec("book"));
      renderBookings(sec("bookings"));
      renderProfile(sec("profile"));
      renderMoney(sec("money"));
    },
  };
})();

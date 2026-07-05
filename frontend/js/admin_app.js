// admin_app.js — the OWNER/ADMIN console: a RESPONSIVE, drill-through SPA (bottom-nav on mobile,
// left side-rail on desktop — the owner runs the club from both). Same DNA as the client + coach
// apps (one shell, hash router, capability-driven detail pages, ONE event story reused everywhere).
// Reuses AdminAPI / AdminUI / CRMUI / ClassUI / window.API — this lane is an IA re-skin, not a rebuild.
// Design spec: docs/specs/ADMIN-REDESIGN.md. Non-admins are bounced to their own app.
(function () {
  var UI, el, principal = null, view, CLUB = null, PROFILE = null;
  var DIARY_TAB = "diary";  // diary (the shared Calendar widget) | classes
  var DIARY_LISTS = null;   // cached {courts, coaches} for the calendar filters
  var money = function (m, c) { return UI.money(m || 0, c || "ZAR"); };
  function go(h) { location.hash = h; }

  // ---- boot ----------------------------------------------------------------
  async function start() {
    UI = window.UI; el = UI.el;
    await window.TFAuth.ready();
    if (!window.TFAuth.isAuthed()) { await window.TFAuth.requireAuth(); return; }
    // Fetch the club brand IN PARALLEL with whoami so first paint isn't gated on a second
    // cross-region round trip (Starter removed cold starts; the round-trips remain).
    var pendingClub = window.AdminAPI.club().catch(function () { return null; });
    var pendingProfile = window.API.getProfile().catch(function () { return null; });
    try { principal = await window.API.whoami(); }
    catch (e) { if (e.status === 401) await window.TFAuth.requireAuth(); return; }
    if (!principal) return;
    var role = principal.role;
    if (role !== "club_admin" && role !== "platform_admin") {
      location.href = role === "coach" ? "/coach" : "/portal"; return;
    }
    if (!principal.club_id) { document.body.innerHTML = '<div style="padding:40px;font-family:Inter">No club resolved.</div>'; return; }
    renderShell();
    window.addEventListener("hashchange", route);
    try { var cb = await pendingClub; if (cb) { CLUB = cb.club || {}; paintBrand(); } } catch (e) {}
    try { var pf = await pendingProfile; if (pf) { PROFILE = pf; paintBrand(); } } catch (e) {}
    route();
  }

  function clubName() { return (CLUB && CLUB.name) || "Your club"; }
  function ownerName() {
    var n = PROFILE && (PROFILE.first_name || [PROFILE.first_name, PROFILE.surname].filter(Boolean).join(" ").trim());
    return n || (principal && principal.email ? principal.email.split("@")[0] : "there");
  }
  function ownerFull() { return (PROFILE && [PROFILE.first_name, PROFILE.surname].filter(Boolean).join(" ").trim()) || ownerName(); }
  // The top-right avatar is the PERSON (owner initials); the top-left brand stays the club name.
  function initials() { var n = ownerFull().trim().split(/\s+/); return ((n[0] || "?")[0] + (n.length > 1 ? n[n.length - 1][0] : "")).toUpperCase(); }
  function paintBrand() { var a = document.getElementById("cf-avatar"); if (a) a.textContent = initials(); var b = document.getElementById("cf-brandname"); if (b) b.textContent = clubName(); }
  function greet() { var h = new Date().getHours(); return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"; }

  // ---- shell ---------------------------------------------------------------
  var NAV = [
    { k: "home", ic: "⌂", label: "Home" },
    { k: "people", ic: "👥", label: "People" },
    { k: "money", ic: "💰", label: "Money" },
    { k: "diary", ic: "📅", label: "Diary" },
    { k: "overview", ic: "📊", label: "Overview" },
    { k: "setup", ic: "⚙", label: "Setup" },
  ];
  function renderShell() {
    document.body.classList.add("cf-app", "cf-admin");
    if (!document.getElementById("cf-appbar")) {
      document.body.insertBefore(el("div", { class: "cf-appbar", id: "cf-appbar" }, [
        el("div", { class: "cf-brand", style: "cursor:pointer", title: "Home", onclick: function () { go("#/"); } }, [el("span", { class: "cf-logo", text: "NP" }), el("span", { id: "cf-brandname", text: clubName() })]),
        el("span", { class: "cf-spacer" }),
        el("div", { class: "cf-avatar", id: "cf-avatar", text: initials(), title: "Account", onclick: function (ev) { openAccountMenu(ev.currentTarget); } }),
      ]), document.body.firstChild);
    }
    view = document.getElementById("cf-main");
    if (!view) { view = el("main", { class: "cf-main", id: "cf-main" }); document.body.appendChild(view); }
    if (!document.getElementById("cf-bottomnav")) {
      var inner = el("div", { class: "cf-bottomnav-in" });
      NAV.forEach(function (n) { inner.appendChild(el("a", { href: "#/" + n.k, "data-nav": n.k }, [el("span", { class: "ic", text: n.ic }), el("span", { text: n.label })])); });
      document.body.appendChild(el("nav", { class: "cf-bottomnav", id: "cf-bottomnav" }, [inner]));
    }
  }
  function setActive(k) { document.querySelectorAll("#cf-bottomnav a").forEach(function (a) { a.classList.toggle("active", a.getAttribute("data-nav") === k); }); }
  function loadScript(src) { return new Promise(function (res, rej) { var s = document.createElement("script"); s.src = src; s.onload = res; s.onerror = function () { rej(new Error("Failed to load " + src)); }; document.head.appendChild(s); }); }
  async function ensureClassDeps() { if (!window.ClassUI) await loadScript("/js/class_ui.js"); }

  // ---- router --------------------------------------------------------------
  var TOP = ["home", "people", "money", "diary", "overview", "setup", "insights"];
  function route() {
    disposeCharts();   // tear down any Overview ECharts instances + resize listeners before leaving
    var parts = (location.hash || "").replace(/^#\/?/, "").split("/").filter(Boolean);
    var top = parts[0] || "home";
    setActive(TOP.indexOf(top) >= 0 ? top :
      (top === "person" ? "people" : (top === "event" ? "diary" : (top === "service" ? "setup" : ""))));
    window.scrollTo(0, 0);
    if (top === "home") return renderHome();
    if (top === "people") return renderPeople();
    if (top === "money") return renderMoney(parts[1]);
    if (top === "diary") return renderDiary(parts[1]);
    if (top === "setup") return renderSetup(parts[1]);
    if (top === "overview" || top === "insights") return renderOverview(parts[1]);
    if (top === "person") return renderPerson(parts[1]);
    if (top === "event") return renderEvent(parts[1]);
    if (top === "class") return renderClassEvent(parts[1]);
    if (top === "profile") return renderProfile();
    return renderHome();
  }

  // ---- helpers -------------------------------------------------------------
  function set(node) { view.style.opacity = 0; UI.clear(view); view.appendChild(node); requestAnimationFrame(function () { view.style.transition = "opacity .16s"; view.style.opacity = 1; }); }
  function loading() {
    var n = el("div", { class: "cf-loading", style: "min-height:200px", text: "Loading…" });
    set(n);
    setTimeout(function () { if (n.isConnected && n.textContent === "Loading…") n.textContent = "Still loading — one moment…"; }, 7000);
  }
  var card = window.UI.card, backBar = window.UI.backBar;   // shared (FRONTEND-STANDARDISATION Wave 1)

  // ---- account menu (top-right avatar) + owner personal profile -----------
  function openAccountMenu(anchor) {
    UI.menu(anchor, [
      { label: "Edit profile", onClick: function () { go("#/profile"); } },
      { label: "Switch profile", onClick: function () { window.TFAuth.signOut().then(function () { location.href = "/login"; }); } },
      "-",
      { label: "Sign out", tone: "danger", onClick: function () { window.TFAuth.signOut().then(function () { location.reload(); }); } },
    ]);
  }
  // The owner has a personal account profile (name + phone), separate from the club config in Setup.
  // Reuses the same /api/me/profile endpoint every role uses (manage_own_profile covers club_admin).
  var PROFILE_FIELDS = [["first_name", "First name", "text"], ["surname", "Surname", "text"], ["phone", "Phone", "tel"]];
  async function renderProfile() {
    loading();
    var pr = {};
    try { pr = await window.API.getProfile(); } catch (e) {}
    var wrap = el("div", {});
    wrap.appendChild(UI.pageHeader("Your profile", "Home", "#/"));
    var inputs = {};
    var c = card([el("h2", { style: "margin:0 0 10px", text: "Your details" })]);
    c.appendChild(UI.kv("Email", el("span", { class: "cf-muted", text: (pr.email || "") + "  (sign-in — can't change)" })));
    PROFILE_FIELDS.forEach(function (f) {
      var inp = el("input", { class: "cf-input", type: f[2], value: pr[f[0]] || "" });
      inputs[f[0]] = inp;
      c.appendChild(el("div", { class: "cf-field" }, [el("label", { text: f[1] }), inp]));
    });
    c.appendChild(el("div", { class: "cf-row", style: "margin-top:12px" }, [
      el("button", { class: "cf-btn cf-btn-primary cf-btn-block", text: "Save", onclick: function () {
        var body = {}; PROFILE_FIELDS.forEach(function (f) { body[f[0]] = inputs[f[0]].value.trim() || null; });
        window.API.patchProfile(body).then(function (res) { PROFILE = res || PROFILE; paintBrand(); UI.toast("Saved.", "info"); },
          function (e) { UI.toast((e && e.body && e.body.error === "VALIDATION") ? "Please check the fields." : UI.errMsg(e), "error"); });
      } }),
    ]));
    wrap.appendChild(c);
    set(wrap);
  }
  function soon(title, note) { return el("div", {}, [el("h1", { style: "margin:0 0 12px", text: title }), card([el("div", { class: "cf-empty", text: note || "Coming next in the redesign." })])]); }
  // A tappable focus card for the Home command-center.
  function focusCard(opts) {
    var head = el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:8px" }, [
      el("h2", { style: "margin:0;font-size:1.05rem", text: opts.title }),
      opts.to ? el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: opts.cta || "Open ›", onclick: function () { go(opts.to); } }) : null,
    ].filter(Boolean));
    var c = card([head]);
    (opts.body || []).forEach(function (n) { if (n) c.appendChild(n); });
    return c;
  }
  function statLine(pairs) {
    return el("div", { class: "cf-row", style: "gap:18px;flex-wrap:wrap" }, pairs.map(function (p) {
      return el("div", {}, [el("div", { style: "font-size:1.25rem;font-weight:800;color:" + (p.tone === "bad" ? "var(--danger,#c0392b)" : "inherit"), text: p.value }),
        el("div", { class: "cf-muted", style: "font-size:.8rem", text: p.label })]);
    }));
  }

  // ---- HOME (the command center — money · today · people · approvals) ------
  async function renderHome() {
    loading();
    var hub = {}, today = { events: [] };
    var tk = UI.dateKey(new Date());
    try { hub = await window.AdminAPI.home(); } catch (e) {}
    try { today = await window.API.master({ date_from: tk + "T00:00:00", date_to: tk + "T23:59:59" }); } catch (e) {}
    var mo = hub.money || {}, pe = hub.people || {}, ap = hub.approvals || {}, cur = mo.currency || "ZAR";
    var wrap = el("div", {});
    wrap.appendChild(el("div", { class: "cf-greet" }, [
      el("div", {}, [el("h1", { text: greet() + ", " + ownerName() }), el("p", { text: clubName() + " — here's what needs you." }),
        el("div", { class: "cf-row", style: "gap:8px;margin-top:10px;flex-wrap:wrap" }, [
          el("button", { class: "cf-btn cf-btn-sm", text: "Edit profile", onclick: function () { go("#/profile"); } }),
        ])]),
    ]));

    // 1) Today at the club
    var live = (today.events || []).filter(function (e) { return e.status !== "cancelled"; })
      .sort(function (a, b) { return String(a.starts_at).localeCompare(String(b.starts_at)); });
    var byType = { court: 0, lesson: 0, class: 0 };
    live.forEach(function (e) { var t = (e.booking_type || "court").toLowerCase(); if (byType[t] != null) byType[t]++; });
    var todayBody = [statLine([
      { value: live.length, label: "Booked today" },
      { value: byType.lesson, label: "Lessons" },
      { value: byType.class, label: "Classes" },
      { value: byType.court, label: "Courts" },
    ])];
    if (live.length) {
      var tl = el("div", { class: "cf-list", style: "margin-top:10px" });
      live.slice(0, 4).forEach(function (ev) {
        var t = (ev.booking_type || "court").toLowerCase();
        var row = el("div", { class: "cf-item" + (ev.id ? " cf-item-tap" : "") }, [
          el("span", { class: "cf-chip " + (["court", "lesson", "class"].indexOf(t) >= 0 ? t : "court"), text: t }),
          el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: UI.fmtTime(ev.starts_at) + "  " + (ev.resource_name || "Booking") }),
            el("div", { class: "cf-item-s", text: (ev.booked_by_name || "") })]),
        ]);
        if (ev.id) row.addEventListener("click", function () { go("#/event/" + ev.id); });
        tl.appendChild(row);
      });
      todayBody.push(tl);
    } else { todayBody.push(el("div", { class: "cf-empty", text: "Nothing booked today yet." })); }
    wrap.appendChild(focusCard({ title: "Today at the club", to: "#/diary", cta: "Open diary ›", body: todayBody }));

    // 2) Money
    wrap.appendChild(focusCard({ title: "Money", to: "#/money", cta: "Settle & review ›", body: [statLine([
      { value: money(mo.owed_to_club_minor, cur), label: "Owed to the club", tone: (mo.owed_to_club_minor > 0 ? "bad" : "") },
      { value: money(mo.net_revenue_minor, cur), label: "Net revenue (mo)" },
      { value: money(mo.rent_due_minor, cur), label: "Coach settlements due" },
      { value: (mo.active_members || 0), label: "Active members" },
    ])] }));

    // 3) People needing attention
    var pBody = [statLine([
      { value: pe.new_signups_7d || 0, label: "New signups (7d)" },
      { value: pe.coach_invites_pending || 0, label: "Coach invites pending" },
      { value: pe.memberships_expiring_14d || 0, label: "Memberships expiring" },
    ])];
    wrap.appendChild(focusCard({ title: "People needing attention", to: "#/people", cta: "View people ›", body: pBody }));

    // 4) Approvals / decisions
    var nRef = ap.refund_requests_pending || 0;
    wrap.appendChild(focusCard({ title: "To approve / decide", to: "#/money", cta: "Go to approvals ›", body: [
      nRef ? el("div", { class: "cf-item cf-item-tap", onclick: function () { go("#/money"); } }, [
        el("span", { class: "cf-chip held", text: nRef }),
        el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: "Refund request" + (nRef === 1 ? "" : "s") + " awaiting you" }), el("div", { class: "cf-item-s", text: "Approve or decline in Money" })]),
        el("span", { class: "cf-muted", text: "›" }),
      ]) : el("div", { class: "cf-empty", text: "Nothing waiting for a decision. 🎉" }),
    ] }));

    // Reports shortcut → the Overview tab
    wrap.appendChild(card([el("div", { class: "cf-item cf-item-tap", onclick: function () { go("#/overview"); } }, [
      el("span", { class: "cf-chip", text: "📊" }),
      el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: "Business overview" }), el("div", { class: "cf-item-s", text: "Traffic, bookings, revenue, members, NPS — daily" })]),
      el("span", { class: "cf-muted", text: "›" }),
    ])]));
    set(wrap);
  }

  // ---- PEOPLE (roster + slicer + search → the unified person 360) -----------
  var PEOPLE = { rows: [], slice: "all", q: "" };
  var SLICES = [["all", "All"], ["members", "Members"], ["coaches", "Coaches"], ["guests", "Guests"], ["admins", "Admins"]];
  function pName(r) { return r.display_name || [r.first_name, r.surname].filter(Boolean).join(" ").trim() || r.email || "Member"; }
  function pInit(r) { var n = pName(r).split(/\s+/); return ((n[0] || "?")[0] + (n.length > 1 ? n[n.length - 1][0] : "")).toUpperCase(); }
  function pSlice(r) {
    if (r.role === "coach") return "coaches";
    if (r.role === "guest") return "guests";
    if (r.role === "club_admin" || r.role === "platform_admin") return "admins";
    return "members";
  }
  function pRoleLabel(r) { return { coach: "Coach", guest: "Guest", club_admin: "Admin", platform_admin: "Admin", member: "Member" }[r.role] || r.role; }
  function peopleFiltered() {
    var q = (PEOPLE.q || "").trim().toLowerCase(), seen = {}, out = [];
    PEOPLE.rows.forEach(function (r) {
      if (PEOPLE.slice !== "all" && pSlice(r) !== PEOPLE.slice) return;
      if (q && (pName(r) + " " + (r.email || "")).toLowerCase().indexOf(q) < 0) return;
      if (PEOPLE.slice === "all") { if (seen[r.user_id]) return; seen[r.user_id] = 1; }
      out.push(r);
    });
    return out;
  }
  function sliceCount(k) {
    if (k === "all") { var s = {}; PEOPLE.rows.forEach(function (r) { s[r.user_id] = 1; }); return Object.keys(s).length; }
    return PEOPLE.rows.filter(function (r) { return pSlice(r) === k; }).length;
  }
  async function renderPeople() {
    loading();
    try { PEOPLE.rows = (await window.AdminAPI.people()).people || []; }
    catch (e) { set(el("div", {}, [el("h1", { text: "People" }), el("div", { class: "cf-empty", text: UI.errMsg(e) })])); return; }
    paintPeople();
  }
  function paintPeople() {
    var wrap = el("div", {});
    wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin:0 0 10px" }, [
      el("h1", { style: "margin:0", text: "People" }),
      el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "+ New client", onclick: function () { newClient(); } }),
    ]));
    // Search (list-only re-render so focus is kept while typing).
    var listBox = el("div", {});
    wrap.appendChild(el("input", {
      class: "cf-input", type: "search", placeholder: "Search name or email…", value: PEOPLE.q,
      style: "margin-bottom:10px",
      oninput: function (e) { PEOPLE.q = e.target.value; paintPeopleList(listBox); },
    }));
    // Slicer (segmented control).
    var seg = el("div", { class: "cf-segment cf-seg-lg" });
    SLICES.forEach(function (sl) {
      seg.appendChild(el("button", {
        class: PEOPLE.slice === sl[0] ? "on" : "", text: sl[1] + " · " + sliceCount(sl[0]),
        onclick: function () { PEOPLE.slice = sl[0]; paintPeople(); },
      }));
    });
    wrap.appendChild(seg);
    wrap.appendChild(listBox);
    paintPeopleList(listBox);
    set(wrap);
  }
  function paintPeopleList(box) {
    UI.clear(box);
    var rows = peopleFiltered();
    if (!rows.length) { box.appendChild(el("div", { class: "cf-empty", text: "No one here yet." })); return; }
    var c = card([]), l = el("div", { class: "cf-list" });
    rows.forEach(function (r) {
      l.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { go("#/person/" + r.user_id); } }, [
        el("div", { class: "cf-avatar", style: "width:34px;height:34px;font-size:.8rem", text: pInit(r) }),
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: pName(r) }),
          el("div", { class: "cf-item-s", text: r.email || "—" }),
        ]),
        r.has_membership ? el("span", { class: "cf-chip member", text: "Member" }) : null,
        el("span", { class: "cf-chip", text: pRoleLabel(r) }),
        el("span", { class: "cf-muted", text: "›" }),
      ].filter(Boolean)));
    });
    c.appendChild(l); box.appendChild(c);
  }

  // ---- PERSON 360 (unified record — members + coaches, one page) ------------
  async function renderPerson(id) {
    loading();
    var pn;
    try { pn = (await window.AdminAPI.person(id)).person; }
    catch (e) { set(el("div", {}, [backBar("People", "#/people"), el("div", { class: "cf-empty", text: UI.errMsg(e) })])); return; }
    var cur = pn.currency || "ZAR";
    var wrap = el("div", {});
    wrap.appendChild(backBar("People", "#/people"));

    // Header — identity, role/status chips, membership line (+ grant/revoke), total owed.
    var chips = el("div", { class: "cf-row", style: "gap:6px;flex-wrap:wrap;margin-top:6px" });
    (pn.roles || []).forEach(function (role) { chips.appendChild(el("span", { class: "cf-chip", text: pRoleLabel({ role: role }) })); });
    chips.appendChild(el("span", { class: "cf-chip " + (pn.member_status === "active" ? "confirmed" : "held"), text: pn.member_status || "—" }));
    var head = card([
      el("div", { class: "cf-detail-h" }, [
        el("div", { class: "cf-row", style: "gap:10px;align-items:center" }, [
          el("div", { class: "cf-avatar", text: pInit(pn) }),
          el("div", {}, [
            el("h1", { style: "margin:0;font-size:1.25rem", text: pn.name }),
            el("div", { class: "cf-muted", style: "font-size:.85rem", text: [pn.email, pn.phone].filter(Boolean).join(" · ") || "—" }),
            chips,
          ]),
        ]),
      ]),
    ]);
    head.appendChild(membershipLine(pn, cur, id));
    wrap.appendChild(head);

    // Coach settlement (if they coach here) — gross / commission / rent / net / balance.
    if (pn.is_coach && pn.settlement) {
      var st = pn.settlement;
      wrap.appendChild(card([
        window.CRMUI.sectionHead("Coaching settlement"),
        window.CRMUI.stats([
          { value: money(st.gross_lesson_minor, cur), label: "Gross lessons" },
          { value: money(st.commission_earned_minor, cur), label: "Club commission" },
          { value: money(st.rent_due_minor, cur), label: "Rent due" },
          { value: money(st.net_to_coach_minor, cur), label: "Net to coach" },
        ]),
        el("div", { class: "cf-muted", style: "font-size:.8rem;margin-top:8px", text: "Ledger balance: " + money(st.lifetime_balance_minor, cur) + " · full per-client settlement in Money." }),
      ], "cf-mt"));
    }

    // Money — what they owe the club (each order Void / Write-off) + online payments.
    var moneyCard = card([window.CRMUI.sectionHead("Money")], "cf-mt");
    moneyCard.appendChild(el("div", { class: "cf-row", style: "margin:2px 0 10px" }, [
      el("div", {}, [el("div", { style: "font-size:1.25rem;font-weight:800;color:" + (pn.owed_minor > 0 ? "var(--danger,#c0392b)" : "inherit"), text: money(pn.owed_minor, cur) }),
        el("div", { class: "cf-muted", style: "font-size:.8rem", text: "Owed to the club" })]),
    ]));
    // Owed orders → the shared CRMUI.lineItems (same widget the coach money uses), with Void/Write-off.
    var owed = (pn.statement && pn.statement.items) || [];
    moneyCard.appendChild(window.CRMUI.lineItems(owed.map(function (it) { return Object.assign({}, it, { gross_minor: it.amount_minor }); }), {
      currency: cur,
      empty: "Nothing owed — all settled. 🎉",
      label: function (it) { return it.description || it.category || "Owed"; },
      sub: function (it) { return [it.category, it.coach_name, (function () { try { return it.date ? UI.fmtDate(it.date) : ""; } catch (e) { return ""; } })()].filter(Boolean).join(" · "); },
      actions: [
        { label: "Void", onClick: function (it) { voidOrder(id, it, false); } },
        { label: "Write off", tone: "danger", onClick: function (it) { voidOrder(id, it, true); } },
      ],
    }));
    var pays = pn.payments || [];
    if (pays.length) {
      moneyCard.appendChild(el("div", { class: "cf-muted", style: "margin:14px 0 4px;font-size:.8rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em", text: "Online payments" }));
      var pl = el("div", { class: "cf-list" });
      pays.forEach(function (pay) {
        pl.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: money(pay.amount_minor, pay.currency_code || cur) }),
            el("div", { class: "cf-item-s", text: (pay.provider || "card") + " · " + (function () { try { return UI.fmtDate(pay.created_at); } catch (e) { return ""; } })() }),
          ]),
          el("span", { class: "cf-chip " + (pay.refunded ? "held" : "confirmed"), text: pay.refunded ? "refunded" : "paid" }),
        ]));
      });
      moneyCard.appendChild(pl);
    }
    wrap.appendChild(moneyCard);

    // Bookings — upcoming + history, each lesson/court row → the admin event story (golden rule).
    wrap.appendChild(bookingsCard("Upcoming", pn.upcoming || [], "Nothing upcoming."));
    wrap.appendChild(bookingsCard("History", pn.history || [], "No past bookings."));
    set(wrap);
  }

  function membershipLine(pn, cur, id) {
    var box = el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-top:12px;padding-top:12px;border-top:1px solid var(--border)" });
    var m = pn.membership;
    var label = m ? ("Member" + (m.plan_label ? " · " + m.plan_label : "") + (m.current_period_end ? " · until " + m.current_period_end : "")) : "No active membership";
    box.appendChild(el("div", {}, [el("div", { style: "font-weight:700", text: m ? "Membership active" : "Not a member" }),
      el("div", { class: "cf-muted", style: "font-size:.82rem", text: label })]));
    var actions = el("div", { class: "cf-row", style: "gap:6px" });
    if (m) actions.appendChild(el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Revoke", onclick: function () { revokeMembership(id, pn.name); } }));
    actions.appendChild(el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Issue package", onclick: function () { issuePackage(id, pn.name); } }));
    box.appendChild(actions);
    return box;
  }
  function bookingsCard(title, rows, empty) {
    var c = card([window.CRMUI.sectionHead(title)], "cf-mt");
    if (!rows.length) { c.appendChild(el("div", { class: "cf-empty", text: empty })); return c; }
    var l = el("div", { class: "cf-list" });
    rows.forEach(function (b) {
      var k = (b.kind || "court").toLowerCase();
      var tap = !!(b.booking_id || b.enrolment_id);
      var row = el("div", { class: "cf-item" + (tap ? " cf-item-tap" : "") }, [
        el("span", { class: "cf-chip " + (["court", "lesson", "class"].indexOf(k) >= 0 ? k : "court"), text: k }),
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: (function () { try { return UI.fmtDate(b.starts_at) + "  " + UI.fmtTime(b.starts_at); } catch (e) { return b.starts_at || ""; } })() }),
          el("div", { class: "cf-item-s", text: [b.resource_name, b.coach_name].filter(Boolean).join(" · ") || "" }),
        ]),
        el("span", { class: "cf-chip " + (b.status === "confirmed" ? "confirmed" : "held"), text: b.status }),
        tap ? el("span", { class: "cf-muted", text: "›" }) : null,
      ].filter(Boolean));
      if (tap) row.addEventListener("click", function () { go(b.booking_id ? ("#/event/" + b.booking_id) : ("#/class/" + b.enrolment_id)); });
      l.appendChild(row);
    });
    c.appendChild(l); return c;
  }
  // ---- person money/membership actions -------------------------------------
  async function issuePackage(id, name) {
    var m = UI.modal("Issue a package" + (name ? " · " + name : ""), { lg: true });
    var mplans = [], bplans = [];
    try { mplans = (await window.AdminAPI.membershipPlans()).plans || []; } catch (e) {}
    try { bplans = (await window.AdminAPI.bundlePlans()).plans || []; } catch (e) {}
    mplans = mplans.filter(function (p) { return p.active !== false; });
    bplans = bplans.filter(function (p) { return p.active !== false; });

    var kind = "membership";
    var kmem = el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Membership" });
    var kpack = el("button", { class: "cf-btn cf-btn-sm", text: "Session pack" });

    // Membership: package (tier) → term, TWO steps. Pack: single select.
    var memGroups = [], byTier = {};
    mplans.forEach(function (p) {
      var key = p.tier || "Membership";
      if (!byTier[key]) { byTier[key] = { name: key, rows: [] }; memGroups.push(byTier[key]); }
      byTier[key].rows.push(p);
    });
    var memPkg = el("select", { class: "cf-input" });
    var memTerm = el("select", { class: "cf-input" });
    var start = el("input", { class: "cf-input", type: "date", value: UI.dateKey(new Date()) });
    var memBox = el("div", {}, [
      el("div", { class: "cf-grid cf-grid-2" }, [
        el("div", { class: "cf-field" }, [el("label", { text: "Package" }), memPkg]),
        el("div", { class: "cf-field" }, [el("label", { text: "Term" }), memTerm]),
      ]),
      el("div", { class: "cf-field" }, [el("label", { text: "Start date (when cover begins)" }), start]),
    ]);
    var packSel = el("select", { class: "cf-input" });
    var packBox = el("div", {}, [el("div", { class: "cf-field" }, [el("label", { text: "Pack" }), packSel])]);
    var amountLine = el("div", { style: "margin:8px 0 10px;font-weight:800" });

    function termLabel(p) { return p.term_months ? (p.term_months + (p.term_months === 1 ? " month" : " months")) : (p.label || "term"); }
    function selectedPlan() {
      if (kind === "membership") { var g = memGroups[memPkg.selectedIndex]; return g ? g.rows[memTerm.selectedIndex] : null; }
      return bplans[packSel.selectedIndex] || null;
    }
    function syncAmount() { var p = selectedPlan(); amountLine.textContent = p ? ("Amount: " + money(p.amount_minor, p.currency)) : "—"; }
    function fillTerms() {
      UI.clear(memTerm);
      var g = memGroups[memPkg.selectedIndex];
      if (g) g.rows.forEach(function (p) { memTerm.appendChild(el("option", { text: termLabel(p) + " · " + money(p.amount_minor, p.currency) })); });
      syncAmount();
    }
    memPkg.addEventListener("change", fillTerms);
    memTerm.addEventListener("change", syncAmount);
    packSel.addEventListener("change", syncAmount);
    if (memGroups.length) { memGroups.forEach(function (g) { memPkg.appendChild(el("option", { text: g.name })); }); fillTerms(); }
    else { memPkg.appendChild(el("option", { value: "", text: "None configured — add in Setup" })); }
    if (bplans.length) { bplans.forEach(function (p) { packSel.appendChild(el("option", { text: p.label + (p.sessions_count ? " · " + p.sessions_count + " sessions" : "") + " · " + money(p.amount_minor, p.currency) })); }); }
    else { packSel.appendChild(el("option", { value: "", text: "None configured — add in Setup" })); }

    function setKind(k) {
      kind = k;
      kmem.className = "cf-btn cf-btn-sm" + (k === "membership" ? " cf-btn-primary" : "");
      kpack.className = "cf-btn cf-btn-sm" + (k === "pack" ? " cf-btn-primary" : "");
      memBox.style.display = k === "membership" ? "" : "none";
      packBox.style.display = k === "pack" ? "" : "none";
      syncAmount();
    }
    kmem.addEventListener("click", function () { setKind("membership"); });
    kpack.addEventListener("click", function () { setKind("pack"); });

    // Payment: owe (PAYG) or mark paid now (offline).
    var payOwe = el("input", { type: "radio", name: "pkgpay", checked: true });
    var payPaid = el("input", { type: "radio", name: "pkgpay" });
    var provSel = el("select", { class: "cf-input", style: "max-width:190px;display:none;margin-top:6px" }, [
      el("option", { value: "cash", text: "Cash" }),
      el("option", { value: "eft", text: "EFT / transfer" }),
      el("option", { value: "card_at_desk", text: "Card at desk" }),
    ]);
    function syncPay() { provSel.style.display = payPaid.checked ? "" : "none"; }
    payOwe.addEventListener("change", syncPay); payPaid.addEventListener("change", syncPay);

    m.body.appendChild(el("p", { class: "cf-muted", style: "margin:0 0 8px;font-size:.85rem",
      text: "Issue a membership or a prepaid session pack. It activates immediately; choose whether it's already paid or owed (they can then pay online, or you mark it paid)." }));
    m.body.appendChild(el("div", { class: "cf-row", style: "gap:8px;margin-bottom:12px" }, [kmem, kpack]));
    m.body.appendChild(memBox);
    m.body.appendChild(packBox);
    m.body.appendChild(amountLine);
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Payment" }),
      el("div", {}, [
        el("label", { class: "cf-row", style: "gap:8px;margin-bottom:6px" }, [payOwe, el("span", { text: "Owe — collect later (PAYG)" })]),
        el("label", { class: "cf-row", style: "gap:8px;align-items:center" }, [payPaid, el("span", { text: "Mark as paid now (offline)" }), provSel]),
      ])]));
    var btn = el("button", { class: "cf-btn cf-btn-primary", text: "Issue package" });
    m.body.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;gap:8px;margin-top:12px" }, [
      el("button", { class: "cf-btn", text: "Cancel", onclick: m.close }), btn,
    ]));
    setKind("membership");

    btn.addEventListener("click", async function () {
      var p = selectedPlan();
      if (!p) { UI.toast("No package to issue — add one in Setup first.", "warn"); return; }
      var body = { kind: kind, mark_paid: payPaid.checked };
      if (kind === "membership") { body.price_id = p.price_id; if (start.value) body.start_date = start.value; }
      else { body.bundle_plan_id = p.id; }
      if (payPaid.checked) body.pay_provider = provSel.value;
      btn.disabled = true;
      try { await window.AdminAPI.issuePackage(id, body); UI.toast(payPaid.checked ? "Issued + marked paid." : "Issued — owed on their statement.", "info"); m.close(); renderPerson(id); }
      catch (e) { btn.disabled = false; UI.toast(UI.errMsg(e), "error"); }
    });
  }
  async function newClient() {
    var m = UI.modal("New client", { lg: true });
    var nm = el("input", { class: "cf-input", placeholder: "Full name" });
    var em = el("input", { class: "cf-input", type: "email", placeholder: "Email" });
    var ph = el("input", { class: "cf-input", placeholder: "Contact number (optional)" });
    m.body.appendChild(el("p", { class: "cf-muted", style: "margin:0 0 10px;font-size:.85rem",
      text: "Adds a client to the system now. They link to their login automatically when they first sign in with this email. You can issue their membership on the next screen." }));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Name" }), nm]));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Email" }), em]));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Contact number" }), ph]));
    var btn = el("button", { class: "cf-btn cf-btn-primary", text: "Create client" });
    m.body.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;gap:8px;margin-top:12px" }, [
      el("button", { class: "cf-btn", text: "Cancel", onclick: m.close }), btn,
    ]));
    btn.addEventListener("click", async function () {
      var email = em.value.trim();
      if (!email) { UI.toast("Email is required.", "warn"); return; }
      btn.disabled = true;
      try {
        var res = await window.AdminAPI.createClient({ name: nm.value.trim(), email: email, phone: ph.value.trim() });
        UI.toast(res.created === false ? "Client already existed — opening their record." : "Client created.", "info");
        m.close();
        go("#/person/" + res.user_id);   // straight to their record so you can issue the membership
      } catch (e) { btn.disabled = false; UI.toast(UI.errMsg(e), "error"); }
    });
  }
  async function revokeMembership(id, name) {
    if (!window.confirm("Cancel " + (name || "this member") + "'s membership? Their courts revert to pay-as-you-go.")) return;
    try { await window.AdminAPI.revokeMembership(id); UI.toast("Membership cancelled.", "info"); renderPerson(id); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  async function voidOrder(id, it, writeOff) {
    var verb = writeOff ? "Write off" : "Void";
    if (!window.confirm(verb + " " + money(it.amount_minor, it.currency) + " (" + (it.description || it.category || "this charge") + ")? This clears it off their statement.")) return;
    try { await window.AdminAPI.voidOrder(it.order_id, { write_off: !!writeOff }); UI.toast(verb + " done.", "info"); renderPerson(id); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  // ---- MONEY (Setup-style: a clean section menu → focused pages) ----------------------------
  var MONEY_MONTH = null; // 'YYYY-MM' for Sales by day (null = current month)
  var MONEY_SECTIONS = [
    ["sales", "Sales by day", "Daily takings — client, service and amount"],
    ["bookings", "Bookings by day", "Every booking — client, service and coach"],
    ["revenue", "Revenue by service", "Net revenue split by service line"],
    ["settlement", "Coach settlement", "What each coach is owed · the club's cut"],
    ["approvals", "Approvals", "Refund requests awaiting your decision"],
    ["payments", "Online payments", "Recent card payments · refund"],
    ["activity", "Club activity", "Every payment, refund and adjustment"],
  ];
  function clubCur() { return (CLUB && CLUB.currency_code) || "ZAR"; }
  function renderMoney(section) {
    if (section === "sales") return moneySales();
    if (section === "bookings") return moneyBookings();
    if (section === "revenue") return moneyRevenue();
    if (section === "settlement") return moneySettlement();
    if (section === "approvals") return moneyApprovals();
    if (section === "payments") return moneyPayments();
    if (section === "activity") return moneyActivity();
    return moneyMenu();
  }
  async function moneyMenu() {
    loading();
    var summary = {}, pending = 0;
    try { summary = await window.AdminAPI.cockpitSummary(); } catch (e) {}
    try { pending = ((await window.AdminAPI.refundRequests({ status: "pending" })).requests || []).length; } catch (e) {}
    var cur = summary.currency || clubCur();
    var wrap = el("div", {});
    wrap.appendChild(el("h1", { style: "margin:0 0 12px", text: "Money" }));
    wrap.appendChild(card([window.CRMUI.stats([
      { value: money(summary.net_revenue_minor, cur), label: "Net revenue" },
      { value: money(summary.commission_earned_minor, cur), label: "Commission kept" },
      { value: money(summary.rent_due_minor, cur), label: "Rent due" },
      { value: money(summary.mrr_minor, cur), label: "MRR" },
      { value: (summary.active_members || 0), label: "Active members" },
    ])]));
    var c = card([]), l = el("div", { class: "cf-list" });
    MONEY_SECTIONS.forEach(function (s) {
      var badge = (s[0] === "approvals" && pending) ? el("span", { class: "cf-chip held", text: pending })
        : el("span", { class: "cf-muted", text: "›" });
      l.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { go("#/money/" + s[0]); } }, [
        el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: s[1] }), el("div", { class: "cf-item-s", text: s[2] })]),
        badge,
      ]));
    });
    c.appendChild(l); wrap.appendChild(c);
    set(wrap);
  }

  // Sales by day — the daily takings, one month at a time; each sale drills to its detail.
  async function moneySales() {
    loading();
    var data;
    try { data = await window.AdminAPI.salesByDay(MONEY_MONTH); }
    catch (e) { set(el("div", {}, [backBar("Money", "#/money"), el("div", { class: "cf-empty", text: UI.errMsg(e) })])); return; }
    MONEY_MONTH = data.month;
    var cur = data.currency || clubCur();
    var wrap = el("div", {});
    wrap.appendChild(backBar("Money", "#/money"));
    function shiftMonth(n) { MONEY_MONTH = addMonth(data.month, n); moneySales(); }
    wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:4px" }, [
      el("h1", { style: "margin:0", text: "Sales by day" }),
      el("div", { class: "cf-row", style: "gap:6px;align-items:center" }, [
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "‹", onclick: function () { shiftMonth(-1); } }),
        el("span", { style: "font-weight:600;min-width:104px;text-align:center", text: monthLabel(data.month) }),
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "›", onclick: function () { shiftMonth(1); } }),
      ]),
    ]));
    wrap.appendChild(el("div", { class: "cf-muted", style: "margin:-2px 0 12px;font-size:.9rem", text: "Total " + money(data.total_minor, cur) + " · " + (data.count || 0) + " sale" + (data.count === 1 ? "" : "s") }));
    var days = data.days || [];
    if (!days.length) wrap.appendChild(el("div", { class: "cf-empty", text: "No sales this month." }));
    else days.forEach(function (d) {
      wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin:14px 2px 6px" }, [
        el("div", { style: "font-weight:700", text: dayLabel(d.date) }),
        el("div", { class: "cf-muted", style: "font-weight:600", text: money(d.total_minor, cur) }),
      ]));
      var c = card([]), l = el("div", { class: "cf-list" });
      (d.sales || []).forEach(function (x) {
        var t = (["court", "lesson", "class"].indexOf(x.service_type) >= 0) ? x.service_type : "court";
        l.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { openSale(x); } }, [
          el("span", { class: "cf-chip " + t, text: x.service_type }),
          el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: x.client_name }), el("div", { class: "cf-item-s", text: x.description || x.service_type })]),
          el("span", { style: "font-weight:700", text: money(x.amount_minor, cur) }),
        ]));
      });
      c.appendChild(l); wrap.appendChild(c);
    });
    set(wrap);
  }
  // Standard click-to-detail: a booking-backed sale → the event story; else its receipt.
  function openSale(x) {
    if (x.booking_id) go("#/event/" + x.booking_id);
    else if (x.order_id) window.open("/receipt.html?order=" + encodeURIComponent(x.order_id), "_blank");
    else UI.toast("No detail available for this sale.", "warn");
  }

  // Bookings by day — the diary as a daily list (client · service · coach), one month at a time.
  // Sibling of Sales by day, but over the bookings themselves; each row drills to the SAME event
  // story widget (#/event/<id> → Widgets.TransactionDetail) — never a second booking sheet.
  async function moneyBookings() {
    loading();
    var data;
    try { data = await window.AdminAPI.bookingsByDay(MONEY_MONTH); }
    catch (e) { set(el("div", {}, [backBar("Money", "#/money"), el("div", { class: "cf-empty", text: UI.errMsg(e) })])); return; }
    MONEY_MONTH = data.month;
    var wrap = el("div", {});
    wrap.appendChild(backBar("Money", "#/money"));
    function shiftMonth(n) { MONEY_MONTH = addMonth(data.month, n); moneyBookings(); }
    wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:4px" }, [
      el("h1", { style: "margin:0", text: "Bookings by day" }),
      el("div", { class: "cf-row", style: "gap:6px;align-items:center" }, [
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "‹", onclick: function () { shiftMonth(-1); } }),
        el("span", { style: "font-weight:600;min-width:104px;text-align:center", text: monthLabel(data.month) }),
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "›", onclick: function () { shiftMonth(1); } }),
      ]),
    ]));
    var bt = data.by_type || {};
    wrap.appendChild(el("div", { class: "cf-muted", style: "margin:-2px 0 12px;font-size:.9rem", text: (data.count || 0) + " booking" + (data.count === 1 ? "" : "s") + " · " + (bt.court || 0) + " court · " + (bt.lesson || 0) + " lesson · " + (bt.class || 0) + " class" }));
    var days = data.days || [];
    if (!days.length) wrap.appendChild(el("div", { class: "cf-empty", text: "No bookings this month." }));
    else days.forEach(function (d) {
      wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin:14px 2px 6px" }, [
        el("div", { style: "font-weight:700", text: dayLabel(d.date) }),
        el("div", { class: "cf-muted", style: "font-weight:600", text: d.count + " booking" + (d.count === 1 ? "" : "s") }),
      ]));
      var c = card([]), l = el("div", { class: "cf-list" });
      (d.bookings || []).forEach(function (x) {
        var t = (["court", "lesson", "class"].indexOf(x.booking_type) >= 0) ? x.booking_type : "court";
        var when = ""; try { when = UI.fmtTime(x.starts_at); } catch (e) {}
        // Subtitle mirrors the event-story convention: coach for a lesson, name for a class, court otherwise.
        var subj = x.booking_type === "lesson" ? (x.coach_name ? "with " + x.coach_name : "")
                 : x.booking_type === "class" ? (x.description || "")
                 : (x.court_name || "");
        var meta = [when, subj].filter(Boolean).join(" · ");
        l.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { go("#/event/" + x.booking_id); } }, [
          el("span", { class: "cf-chip " + t, text: x.booking_type }),
          el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: x.client_name }), el("div", { class: "cf-item-s", text: meta })]),
          UI.statusChip(x.status),
        ]));
      });
      c.appendChild(l); wrap.appendChild(c);
    });
    set(wrap);
  }
  function addMonth(ym, n) { try { var p = (ym || "").split("-"), d = new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1 + n, 1); return d.getFullYear() + "-" + (d.getMonth() + 1 < 10 ? "0" : "") + (d.getMonth() + 1); } catch (e) { return ym; } }
  function monthLabel(ym) { try { var p = ym.split("-"); return new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1, 1).toLocaleDateString("en-ZA", { month: "long", year: "numeric" }); } catch (e) { return ym; } }
  function dayLabel(iso) { try { return new Date(iso + "T12:00:00").toLocaleDateString("en-ZA", { weekday: "short", day: "numeric", month: "short" }); } catch (e) { return iso; } }

  async function moneyRevenue() {
    loading();
    var revenue = [];
    try { revenue = (await window.AdminAPI.cockpitRevenue()).revenue || []; } catch (e) {}
    var cur = clubCur();
    var wrap = el("div", {}, [backBar("Money", "#/money"), el("h1", { style: "margin:0 0 12px", text: "Revenue by service" })]);
    var byKind = {};
    revenue.forEach(function (x) { byKind[x.service_kind] = (byKind[x.service_kind] || 0) + (x.net_minor || 0); });
    var keys = Object.keys(byKind).sort(function (a, b) { return byKind[b] - byKind[a]; });
    if (!keys.length) wrap.appendChild(el("div", { class: "cf-empty", text: "No revenue yet." }));
    else {
      var c = card([]), l = el("div", { class: "cf-list" });
      keys.forEach(function (k) {
        l.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: k })]),
          el("span", { style: "font-weight:700", text: money(byKind[k], cur) }),
        ]));
      });
      c.appendChild(l); wrap.appendChild(c);
    }
    set(wrap);
  }

  async function moneySettlement() {
    loading();
    var coaches = [];
    try { coaches = (await window.AdminAPI.cockpitCoachEarnings()).coaches || []; } catch (e) {}
    var cur = clubCur();
    var wrap = el("div", {}, [backBar("Money", "#/money"), el("h1", { style: "margin:0 0 12px", text: "Coach settlement" })]);
    if (!coaches.length) wrap.appendChild(el("div", { class: "cf-empty", text: "No coach settlement yet." }));
    else {
      var c = card([]), l = el("div", { class: "cf-list" });
      coaches.forEach(function (co) {
        l.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { go("#/person/" + co.coach_user_id); } }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: co.coach_name || "Coach" }),
            el("div", { class: "cf-item-s", text: (co.lesson_count || 0) + " lessons · commission " + money(co.commission_earned_minor, cur) }),
          ]),
          el("span", { style: "font-weight:700", text: money(co.net_to_coach_minor, cur) }),
          el("span", { class: "cf-muted", text: "›" }),
        ]));
      });
      c.appendChild(l); wrap.appendChild(c);
    }
    set(wrap);
  }

  async function moneyApprovals() {
    loading();
    var reqs = [];
    try { reqs = (await window.AdminAPI.refundRequests({ status: "pending" })).requests || []; } catch (e) {}
    var cur = clubCur();
    var wrap = el("div", {}, [backBar("Money", "#/money"), el("h1", { style: "margin:0 0 12px", text: "Approvals" })]);
    // Shared CRMUI.lineItems — the same refund-queue widget the coach money uses.
    wrap.appendChild(card([window.CRMUI.lineItems(reqs.map(function (r) { return Object.assign({}, r, { gross_minor: (r.amount_minor != null ? r.amount_minor : r.order_amount_minor) }); }), {
      currency: cur,
      empty: "Nothing waiting for a decision. 🎉",
      label: function (it) { return it.requester_name || "A member"; },
      sub: function (it) { return [it.item_description || "Order", it.reason ? "“" + it.reason + "”" : ""].filter(Boolean).join(" · "); },
      actions: [
        { label: "Approve", tone: "primary", onClick: function (it) { decideRefund(it, "approve"); } },
        { label: "Decline", tone: "danger", onClick: function (it) { decideRefund(it, "decline"); } },
      ],
    })]));
    set(wrap);
  }

  async function moneyPayments() {
    loading();
    var pays = [];
    try { pays = (await window.AdminAPI.payments()).payments || []; } catch (e) {}
    var cur = clubCur();
    var wrap = el("div", {}, [backBar("Money", "#/money"), el("h1", { style: "margin:0 0 12px", text: "Online payments" })]);
    if (!pays.length) wrap.appendChild(el("div", { class: "cf-empty", text: "No online payments yet." }));
    else {
      var c = card([]), l = el("div", { class: "cf-list" });
      pays.slice(0, 50).forEach(function (pay) {
        // A booking/class payment drills to its transaction RECORD (the ONE place refunds happen);
        // a pure sale (membership/pack — no event record yet) keeps an inline Refund.
        var recHash = pay.booking_id ? ("#/event/" + pay.booking_id) : (pay.enrolment_id ? ("#/class/" + pay.enrolment_id) : null);
        var trailing = pay.refunded ? el("span", { class: "cf-chip held", text: "refunded" })
          : recHash ? el("span", { class: "cf-muted", text: "›" })
            : el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Refund", onclick: function () { refundPayment(pay, cur); } });
        var row = el("div", { class: "cf-item" + (recHash ? " cf-item-tap" : "") }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: money(pay.amount_minor, pay.currency_code || cur) + (pay.payer_email ? " · " + pay.payer_email : "") }),
            el("div", { class: "cf-item-s", text: (function () { try { return UI.fmtDate(pay.created_at); } catch (e) { return ""; } })() }),
          ]),
          trailing,
        ]);
        if (recHash) row.addEventListener("click", function () { go(recHash); });
        l.appendChild(row);
      });
      c.appendChild(l); wrap.appendChild(c);
    }
    set(wrap);
  }

  async function moneyActivity() {
    loading();
    var activity = [];
    try { activity = (await window.AdminAPI.activity(150)).activity || []; } catch (e) {}
    var wrap = el("div", {}, [backBar("Money", "#/money"), el("h1", { style: "margin:0 0 12px", text: "Club activity" })]);
    wrap.appendChild(card([window.CRMUI.activityFeed(activity, { empty: "No activity yet." })]));
    set(wrap);
  }

  async function decideRefund(rq, action) {
    var isA = action === "approve";
    var note = window.prompt(isA ? "Approve & refund this via Yoco? Optional note:" : "Decline this request? Reason (shown to the member):", "");
    if (note === null) return;
    try {
      if (isA) { var alsoCancel = window.confirm("Also CANCEL the booking + free the slot?\n\nOK = refund + cancel.   Cancel = refund only."); await window.AdminAPI.approveRefundRequest(rq.id, { note: note, cancel_booking: alsoCancel }); }
      else await window.AdminAPI.declineRefundRequest(rq.id, { note: note });
      UI.toast(isA ? "Approved." : "Declined.", "info"); moneyApprovals();
    } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  async function refundPayment(pay, cur) {
    if (!window.confirm("Refund " + money(pay.amount_minor, pay.currency_code || cur) + " to " + (pay.payer_email || "the customer") + " via Yoco?")) return;
    var alsoCancel = window.confirm("Also CANCEL the booking + free the slot?\n\nOK = refund + cancel.   Cancel = refund only.");
    try { await window.AdminAPI.yocoRefund({ order_id: pay.order_id, cancel_booking: alsoCancel }); UI.toast("Refunded.", "info"); moneyPayments(); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  // ---- DIARY (standard calendar: Day / Week / Month + court & coach filters, default today) --
  async function ensureDiaryLists() {
    if (DIARY_LISTS) return DIARY_LISTS;
    var courts = [], coaches = [];
    try { courts = ((await window.AdminAPI.resources()).resources || []).filter(function (r) { return r.kind === "court" && r.is_active !== false; }); } catch (e) {}
    try { coaches = (await window.AdminAPI.coaches()).coaches || []; } catch (e) {}
    DIARY_LISTS = { courts: courts, coaches: coaches };
    return DIARY_LISTS;
  }
  // Diary — the shared Calendar widget (Widgets.Calendar) over the whole club (court + coach
  // filters), plus a Classes tab. FRONTEND-STANDARDISATION Wave 5. Router passes an optional date.
  async function renderDiary(dateKey) {
    loading();
    var lists = await ensureDiaryLists();
    var wrap = el("div", {});
    wrap.appendChild(el("h1", { style: "margin:0 0 8px", text: "Diary" }));
    function seg(k, label) { return el("button", { class: DIARY_TAB === k ? "on" : "", text: label, onclick: function () { DIARY_TAB = k; renderDiary(dateKey); } }); }
    wrap.appendChild(el("div", { class: "cf-segment cf-seg-lg" }, [seg("diary", "Calendar"), seg("classes", "Classes")]));
    var body = el("div", {});
    wrap.appendChild(body);
    set(wrap);
    if (DIARY_TAB === "classes") { renderDiaryClasses(body); return; }
    window.Widgets.Calendar.mount(body, {
      date: dateKey || UI.dateKey(new Date()),
      grid: true,                 // Day view = the resource-timeline grid (blocks drill to the event story)
      classicLink: true,
      filterBar: {
        courts: (lists.courts || []).map(function (c) { return { id: c.id, name: c.name || "Court" }; }),
        coaches: (lists.coaches || []).map(function (c) { return { id: c.user_id, name: c.display_name || [c.first_name, c.surname].filter(Boolean).join(" ") || c.email }; }),
      },
      data: { events: function (r) { return window.API.master({ date_from: r.from, date_to: r.to }).then(function (x) { return x.events || []; }); } },
      onNavigate: function (ev) { if (ev && ev.id) go("#/event/" + ev.id); },
    });
  }
  async function renderDiaryClasses(box) {
    box.appendChild(el("div", { class: "cf-loading", text: "Loading classes…" }));
    try { await ensureClassDeps(); } catch (e) { UI.clear(box); box.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); return; }
    box.appendChild(el("div", { class: "cf-row", style: "margin:8px 0" }, [
      el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "＋ New class", onclick: function () { window.ClassUI.openClassForm({ api: window.AdminAPI, title: "New class", onSaved: function () { renderDiaryClasses(box); } }); } }),
    ]));
    var listBox = el("div", {}), sessBox = el("div", {});
    box.appendChild(listBox); box.appendChild(sessBox);
    try {
      var r = await window.AdminAPI.classes();
      UI.clear(listBox);
      window.ClassUI.renderClassList({
        host: listBox, classes: r.classes || [],
        onSchedule: function (c) { window.ClassUI.openScheduleForm({ api: window.AdminAPI, cls: { resource_id: c.resource_id, name: c.name, capacity: c.capacity, duration_minutes: c.duration_minutes }, onSaved: function () { renderDiaryClasses(box); } }); },
        onSessions: function (c) { UI.clear(sessBox); sessBox.appendChild(el("div", { style: "margin-top:14px" }, [el("h3", { style: "margin:0 0 6px", text: "Sessions · " + (c.name || "Class") }), el("div", { id: "adm-cls-sessions" })])); window.ClassUI.renderSessions({ api: window.AdminAPI, cls: { resource_id: c.resource_id, name: c.name, capacity: c.capacity }, host: document.getElementById("adm-cls-sessions") }); },
      });
    } catch (e) { UI.clear(listBox); listBox.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
  }
  // ---- EVENT STORY (the ONE shared god-view — Home/People/Money/Diary all drill here) ------
  var TYPE_LABEL = { court: "Court", lesson: "Lesson", class: "Class" };
  function typeLabel(t) { return TYPE_LABEL[t] || "Session"; }
  function timeRange(b) { try { return UI.fmtTime(b.starts_at) + "–" + UI.fmtTime(b.ends_at); } catch (e) { return ""; } }
  var kv = window.UI.kv;   // shared (Wave 1)

  // The ONE shared transaction detail (Widgets.TransactionDetail). Admin wires the god-view action
  // handlers; the widget renders. The modals below (timeModal/deskPay/refund/reassign) are the admin
  // action UIs those handlers open. (FRONTEND-STANDARDISATION Wave 2.)
  function renderEvent(id) {
    var host = el("div", {});
    set(host);
    window.Widgets.TransactionDetail.mount(host, {
      role: "admin",
      scope: { id: id },
      fields: { showCoach: true, showNotes: true },
      data: { get: function (i) { return window.AdminAPI.bookingStory(i).then(function (r) { return r.booking; }); } },
      onNavigate: function (t) { if (t.kind === "person") go("#/person/" + t.id); },
      actions: {
        accept: { done: "Confirmed.", run: function (b) { return window.API.acceptBooking(b.id); } },
        propose: { manual: true, run: function (b) { timeModal("Propose a time", b, function (body) { return window.API.proposeTime(b.id, body); }, "Proposed.", function () { renderEvent(id); }); } },
        decline: { tone: "danger", back: true, done: "Declined.", run: function (b) { return window.API.declineBooking(b.id, {}); } },
        mark_completed: { done: "Marked completed.", run: function (b) { return window.API.setBookingStatus(b.id, { status: "completed" }); } },
        mark_no_show: { done: "Marked no-show.", run: function (b) { return window.API.setBookingStatus(b.id, { status: "no_show" }); } },
        reschedule: { manual: true, run: function (b) { timeModal("Reschedule", b, function (body) { return window.API.rescheduleBooking(b.id, { starts_at: body.starts_at, ends_at: body.ends_at, scope: "this" }); }, "Rescheduled.", function () { renderEvent(id); }); } },
        reassign_coach: { manual: true, run: function (b) { reassignModal(b, function () { renderEvent(id); }); } },
        cancel: { tone: "danger", back: true, confirm: "Cancel this booking and free the slot?", done: "Cancelled.", run: function (b) { return window.API.cancelBooking(b.id, { reason: "admin cancelled" }); } },
        add_to_calendar: { manual: true, run: function (b) { addToCalendar(b.ics_url); } },
        desk_pay: { manual: true, run: function (b) { deskPayModal(b.order_id, b.charge, function () { renderEvent(id); }); } },
        refund: { manual: true, run: function (b) { refundModal(b.order_id, b.charge, function () { renderEvent(id); }); } },
        void: { confirm: "Void this charge (a mistake)? It drops off the client's statement.", done: "Voided.", run: function (b) { return window.AdminAPI.voidOrder(b.order_id, { write_off: false }); } },
        write_off: { tone: "danger", confirm: "Write off (forgive) this charge? No money is collected.", done: "Written off.", run: function (b) { return window.AdminAPI.voidOrder(b.order_id, { write_off: true }); } },
        collect: { done: "Marked collected.", run: function (b) { return window.AdminAPI.arrearsCollected(b.arrears.id); } },
        discount: { manual: true, run: function (b) { var v = window.prompt("New coaching amount (e.g. 250.00):", ((b.arrears.gross_minor || 0) / 100).toFixed(2)); if (v === null) return; var f = parseFloat(v); if (isNaN(f) || f < 0) { UI.toast("Enter a valid amount.", "warn"); return; } window.AdminAPI.arrearsAdjust(b.arrears.id, { gross_minor: Math.round(f * 100) }).then(function () { UI.toast("Discounted.", "info"); renderEvent(id); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); } },
        write_off_coaching: { tone: "danger", manual: true, run: function (b) { var r = window.prompt("Write off this coaching charge? Reason (shown to coach & client):", ""); if (r === null) return; window.AdminAPI.arrearsAdjust(b.arrears.id, { status: "written_off", reason: r }).then(function () { UI.toast("Written off.", "info"); renderEvent(id); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); } },
      },
    });
  }

  // The admin CLASS record — the SAME widget as a booking event, fed by enrolment_story.
  function renderClassEvent(id) {
    var host = el("div", {});
    set(host);
    window.Widgets.TransactionDetail.mount(host, {
      role: "admin",
      scope: { id: id },
      fields: { showCoach: true, showNotes: true },
      data: { get: function (i) { return window.TFAuth.apiJSON("/api/admin/classes/" + encodeURIComponent(i)).then(function (r) { return r.booking; }); } },
      onNavigate: function (t) { if (t.kind === "person") go("#/person/" + t.id); },
      actions: {
        cancel: { tone: "danger", back: true, confirm: "Cancel this enrolment and free the seat?", done: "Cancelled.", run: function (b) { return window.API.cancelEnrolment(b.class_session_id, b.player_user_id ? { user_id: b.player_user_id } : {}); } },
        desk_pay: { manual: true, run: function (b) { deskPayModal(b.charge.order_id, b.charge, function () { renderClassEvent(id); }); } },
        refund: { manual: true, run: function (b) { refundModal(b.charge.order_id, b.charge, function () { renderClassEvent(id); }); } },
        void: { confirm: "Void this charge (a mistake)? It drops off the client's statement.", done: "Voided.", run: function (b) { return window.AdminAPI.voidOrder(b.charge.order_id, { write_off: false }); } },
        write_off: { tone: "danger", confirm: "Write off (forgive) this charge?", done: "Written off.", run: function (b) { return window.AdminAPI.voidOrder(b.charge.order_id, { write_off: true }); } },
        receipt: { manual: true, run: function (b) { window.open("/receipt.html?order=" + encodeURIComponent(b.charge.order_id), "_blank"); } },
      },
    });
  }

  // ---- event-story modals + helpers ----------------------------------------
  var modal = function (t) { return UI.modal(t, { lg: true }); };   // shared (Wave 1)
  var toLocal = window.UI.toLocal, addToCalendar = window.UI.addToCalendar;
  // Reused by Propose + Reschedule — pick a datetime + duration, POST via `send`.
  function timeModal(title, b, send, okMsg, then) {
    var m = modal(title);
    var s = el("input", { class: "cf-input", type: "datetime-local", value: toLocal(b.starts_at) });
    var dur = el("select", { class: "cf-input" }, [30, 45, 60, 90, 120].map(function (d) { return el("option", { value: String(d), text: d + " min" }); })); dur.value = String(b.duration_minutes || 60);
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Time" }), s]));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Duration" }), dur]));
    m.body.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;gap:8px;margin-top:10px" }, [
      el("button", { class: "cf-btn", text: "Close", onclick: m.close }),
      el("button", { class: "cf-btn cf-btn-primary", text: "Save", onclick: function () { if (!s.value) { UI.toast("Pick a time.", "warn"); return; } var st = new Date(s.value), en = new Date(st.getTime() + parseInt(dur.value, 10) * 60000); send({ starts_at: st.toISOString(), ends_at: en.toISOString() }).then(function () { UI.toast(okMsg, "info"); m.close(); (then || route)(); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); } }),
    ]));
  }
  function deskPayModal(orderId, ch, then) {
    var m = modal("Mark as paid");
    var amt = el("input", { class: "cf-input", type: "number", step: "0.01", value: ((ch.amount_minor || 0) / 100).toFixed(2) });
    var prov = el("select", { class: "cf-input" }, [["cash", "Cash"], ["card_at_desk", "Card at desk"], ["eft", "EFT"]].map(function (o) { return el("option", { value: o[0], text: o[1] }); }));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Amount" }), amt]));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Method" }), prov]));
    m.body.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;gap:8px;margin-top:10px" }, [
      el("button", { class: "cf-btn", text: "Close", onclick: m.close }),
      el("button", { class: "cf-btn cf-btn-primary", text: "Record payment", onclick: function () { var f = parseFloat(amt.value); if (isNaN(f) || f < 0) { UI.toast("Enter a valid amount.", "warn"); return; } window.API.deskPayment({ order_id: orderId, amount_minor: Math.round(f * 100), provider: prov.value }).then(function () { UI.toast("Payment recorded.", "info"); m.close(); (then || route)(); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); } }),
    ]));
  }
  function refundModal(orderId, ch, then) {
    var m = modal("Refund");
    m.body.appendChild(el("p", { class: "cf-muted", style: "margin:0 0 10px;font-size:.9rem", text: "Refund " + money(ch.amount_minor, ch.currency || "ZAR") + " to the customer via Yoco." }));
    var cancel = el("input", { type: "checkbox" });
    m.body.appendChild(el("label", { class: "cf-row", style: "gap:8px;align-items:center" }, [cancel, el("span", { text: "Also cancel the booking + free the slot" })]));
    m.body.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;gap:8px;margin-top:12px" }, [
      el("button", { class: "cf-btn", text: "Close", onclick: m.close }),
      el("button", { class: "cf-btn cf-btn-primary", text: "Refund", onclick: function () { window.AdminAPI.yocoRefund({ order_id: orderId, cancel_booking: cancel.checked }).then(function () { UI.toast("Refunded.", "info"); m.close(); (then || route)(); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); } }),
    ]));
  }
  async function reassignModal(b, then) {
    var m = modal("Reassign coach");
    var sel = el("select", { class: "cf-input" }, [el("option", { value: "", text: "Loading coaches…" })]);
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "New coach" }), sel]));
    var footer = el("div", { class: "cf-row", style: "justify-content:flex-end;gap:8px;margin-top:10px" }, [el("button", { class: "cf-btn", text: "Close", onclick: m.close })]);
    m.body.appendChild(footer);
    try {
      var coaches = ((await window.AdminAPI.coaches()).coaches || []).filter(function (c) { return String(c.user_id) !== String((b.coach || {}).user_id) && c.is_bookable !== false; });
      UI.clear(sel);
      if (!coaches.length) { sel.appendChild(el("option", { value: "", text: "No other bookable coaches" })); return; }
      sel.appendChild(el("option", { value: "", text: "Choose a coach…" }));
      coaches.forEach(function (c) { sel.appendChild(el("option", { value: c.user_id, text: c.display_name || [c.first_name, c.surname].filter(Boolean).join(" ") || c.email })); });
      footer.appendChild(el("button", { class: "cf-btn cf-btn-primary", text: "Reassign", onclick: function () { if (!sel.value) { UI.toast("Pick a coach.", "warn"); return; } window.AdminAPI.reassignCoach(b.id, { coach_user_id: sel.value }).then(function () { UI.toast("Reassigned.", "info"); m.close(); (then || route)(); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); } }));
    } catch (e) { UI.clear(sel); sel.appendChild(el("option", { value: "", text: UI.errMsg(e) })); }
  }
  // ---- SETUP (config brought into the SPA — reuses the AdminUI editor library) --------------
  // Setup — the shared Widgets.Setup shell (gold standard) over the owner's sections. Services use the
  // shared Widgets.ServiceList (all services + edit + lifecycle). FRONTEND-STANDARDISATION Wave 6.
  var ADMIN_SETUP = [
    { key: "profile", label: "Club profile & payments", desc: "Name, contact, branding, accepted payment methods",
      mount: function (h) { window.AdminAPI.onboarding().then(function (d) { UI.clear(h); window.AdminUI.clubProfile(h, d || {}, { saveLabel: "Save changes" }); setupPayments(h, (d && d.policy) || {}); }, function (e) { UI.clear(h); h.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }); } },
    { key: "courts", label: "Courts & hours", desc: "Courts, surfaces and weekly playing hours",
      mount: function (h) { UI.clear(h); window.AdminUI.courtsManage(h); } },
    { key: "services", label: "Services & pricing", desc: "Lessons, classes, court hire — prices, packages, commission",
      mount: function (h) { window.Widgets.ServiceList.mount(h, { role: "admin", kinds: ["lesson", "class", "court"], allowCreate: true, onCreate: adminNewService }); } },
    { key: "memberships", label: "Memberships", desc: "Membership tiers and term plans",
      mount: function (h) { UI.clear(h); window.AdminUI.membershipServices(h); } },
    { key: "packs", label: "Session packs", desc: "Prepaid bundles",
      mount: function (h) { UI.clear(h); window.AdminUI.bundlePlans(h); } },
    { key: "coaches", label: "Coaches & commission", desc: "Invite, hide or remove coaches · rent + commission",
      mount: function (h) { UI.clear(h); window.AdminUI.coachManage(h); } },
  ];
  // Owner creates a service. Lessons are created PER COACH (pick the coach); classes & courts
  // have their own homes. Delegates to POST /api/services (owner may name any coach).
  async function adminNewService(kind) {
    if (kind === "class") { UI.toast("Create classes under Diary → Classes.", "info"); return; }
    if (kind === "court") { UI.toast("Add courts under Setup → Courts & hours.", "info"); return; }
    var m = modal("New lesson");
    var sel = el("select", { class: "cf-input" }, [el("option", { value: "", text: "Loading coaches…" })]);
    var name = el("input", { class: "cf-input", placeholder: "e.g. Private lesson" });
    var dur = el("select", { class: "cf-input" }, [30, 45, 60, 90, 120].map(function (d) { return el("option", { value: String(d), text: d + " min" }); })); dur.value = "60";
    var price = el("input", { class: "cf-input", type: "number", placeholder: "Price e.g. 400" });
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Coach" }), sel]));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Name" }), name]));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Duration" }), dur]));
    m.body.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Price (per session)" }), price]));
    var footer = el("div", { class: "cf-row", style: "justify-content:flex-end;gap:8px;margin-top:10px" }, [el("button", { class: "cf-btn", text: "Close", onclick: m.close })]);
    m.body.appendChild(footer);
    try {
      var coaches = (await window.AdminAPI.coaches()).coaches || [];
      UI.clear(sel);
      if (!coaches.length) { sel.appendChild(el("option", { value: "", text: "No coaches — invite one first" })); return; }
      sel.appendChild(el("option", { value: "", text: "Choose a coach…" }));
      coaches.forEach(function (c) { sel.appendChild(el("option", { value: c.user_id || c.id, text: c.display_name || [c.first_name, c.surname].filter(Boolean).join(" ") || c.email })); });
      footer.appendChild(el("button", { class: "cf-btn cf-btn-primary", text: "Create", onclick: function () {
        var pr = parseFloat(price.value);
        if (!sel.value) { UI.toast("Pick a coach.", "warn"); return; }
        if (!name.value.trim() || isNaN(pr) || pr < 0) { UI.toast("Add a name and price.", "warn"); return; }
        window.AdminAPI.createService({ service_kind: "lesson", coach_user_id: sel.value, name: name.value.trim(), duration_minutes: parseInt(dur.value, 10), amount_minor: Math.round(pr * 100) })
          .then(function () { UI.toast("Lesson created — tap it to add durations, packages or commission.", "info"); m.close(); renderSetup("services"); }, function (e) { UI.toast(UI.errMsg(e), "error"); });
      } }));
    } catch (e) { UI.clear(sel); sel.appendChild(el("option", { value: "", text: UI.errMsg(e) })); }
  }
  function renderSetup(section) {
    var host = el("div", {});
    set(host);
    window.Widgets.Setup.mount(host, {
      active: section, sections: ADMIN_SETUP, backHash: "#/setup",
      onOpen: function (k) { go("#/setup/" + k); },
      title: "Setup", intro: "Configure your club. Changes save per section.",
      footer: el("p", { class: "cf-muted", style: "font-size:.82rem;margin-top:14px" }, [
        document.createTextNode("Classes are scheduled under Diary → Classes. Prefer the classic console? "),
        el("a", { href: "/admin-classic", text: "Open classic ›" }),
      ]),
    });
  }
  function setupPayments(host, policy) {
    var c = el("div", { class: "cf-card" }, [
      el("h3", { text: "Payment methods" }),
      el("p", { class: "cf-muted", text: "Which ways the club accepts payment. Each service can then choose which of these it offers." }),
    ]);
    function flag(key, label, hint) {
      var lbl = el("label", { class: "cf-row", style: "cursor:pointer;gap:10px;align-items:flex-start;margin-top:8px" });
      var cb = el("input", { type: "checkbox" }); cb.checked = !!policy[key]; cb.style.width = "auto";
      cb.addEventListener("change", async function () {
        cb.disabled = true; var body = {}; body[key] = cb.checked;
        try { await window.AdminAPI.patchPolicy(body); policy[key] = cb.checked; UI.toast("Saved.", "info"); }
        catch (e) { cb.checked = !cb.checked; UI.toast(UI.errMsg(e), "error"); }
        finally { cb.disabled = false; }
      });
      lbl.appendChild(cb);
      lbl.appendChild(el("div", {}, [el("div", { style: "font-weight:600", text: label }), hint ? el("div", { class: "cf-muted", style: "font-size:.82rem", text: hint }) : null].filter(Boolean)));
      return lbl;
    }
    c.appendChild(flag("allow_online_payment", "Pay online (card)", "Yoco — card + Apple/Google/Samsung Pay."));
    c.appendChild(flag("allow_pay_at_court", "Pay at the club", "Settle at the front desk."));
    c.appendChild(flag("allow_monthly_account", "Monthly account", "Charges accrue on a tab, invoiced monthly."));
    host.appendChild(c);
  }

  // ---- INSIGHTS (Phase-2 court-utilisation heatmap + the Business Overview dashboard) -------
  // ---- OVERVIEW (business insights as a first-class tab) ------------------------------------------
  // Month pager + sub-tabs + daily graphs, all bound to ONE endpoint (GET /api/insights/overview)
  // that RECONCILES with Money → Sales/Bookings by day. Charts go through ONE shared ECharts seam
  // (ovBase/mountChart) — every panel is config, never a forked chart. ECharts is lazy-loaded (the
  // one sanctioned charting dep, already used by /overview.html); the old iframe is retired.
  var OV_MONTH = null, OV_TAB = "traffic", OV_DATA = null, OV_CHARTS = [];
  var OV_TABS = [["traffic", "Traffic"], ["bookings", "Bookings"], ["revenue", "Revenue"], ["members", "Members"], ["experience", "NPS"], ["courts", "Courts"]];
  function ensureECharts() { return window.echarts ? Promise.resolve() : loadScript("https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"); }
  function disposeCharts() { (OV_CHARTS || []).forEach(function (c) { try { window.removeEventListener("resize", c.ro); c.chart.dispose(); } catch (e) {} }); OV_CHARTS = []; }
  function mjr(v) { return (v || 0) / 100; }
  function ovDayLabels(days) { return (days || []).map(function (d) { return parseInt(d.slice(8), 10); }); }
  function ovMoneyShort(v) { return v >= 1000 ? ((v / 1000).toFixed(v % 1000 ? 1 : 0) + "k") : String(Math.round(v)); }
  // The ONE chart-option builder: a day-of-month x-axis + N bar/line series. `opts.money` (a currency)
  // formats the y-axis + tooltip in money (series data is already major units).
  function ovBase(days, series, opts) {
    opts = opts || {}; var fmt = opts.money;
    return {
      color: series.map(function (s) { return s.color; }),
      grid: { left: fmt ? 52 : 38, right: 14, top: 30, bottom: 24 },
      tooltip: { trigger: "axis", valueFormatter: fmt ? function (v) { return money(Math.round(v * 100), fmt); } : undefined },
      legend: { top: 0, itemWidth: 11, itemHeight: 11, textStyle: { fontSize: 11 } },
      xAxis: { type: "category", data: ovDayLabels(days), axisTick: { show: false }, axisLabel: { fontSize: 10, color: "#6b7280" }, axisLine: { lineStyle: { color: "#e5e7eb" } } },
      yAxis: { type: "value", minInterval: fmt ? undefined : 1, splitLine: { lineStyle: { color: "#f0f2ef" } }, axisLabel: { fontSize: 10, color: "#6b7280", formatter: fmt ? ovMoneyShort : undefined } },
      series: series.map(function (s) {
        return {
          name: s.name, type: s.type || "bar", data: s.data, stack: s.stack, smooth: true, symbol: "none",
          barMaxWidth: 20, itemStyle: { borderRadius: s.type === "line" ? 0 : 3 },
          lineStyle: s.type === "line" ? { width: 2 } : undefined, areaStyle: s.area ? { opacity: 0.12 } : undefined,
        };
      }),
    };
  }
  function mountChart(container, buildOption) {
    container.style.minHeight = "260px";
    ensureECharts().then(function () {
      try {
        var chart = window.echarts.init(container);
        chart.setOption(buildOption());
        var ro = function () { try { chart.resize(); } catch (e) {} };
        window.addEventListener("resize", ro);
        OV_CHARTS.push({ chart: chart, ro: ro });
      } catch (e) { container.appendChild(el("div", { class: "cf-empty", text: "Chart unavailable." })); }
    }, function () { UI.clear(container); container.appendChild(el("div", { class: "cf-empty", text: "Charts couldn't load — check your connection." })); });
  }
  function ovChartCard(body, title, height) {
    var c = card([window.CRMUI.sectionHead(title)]);
    var cc = el("div", { style: "height:" + (height || 300) + "px" });
    c.appendChild(cc); body.appendChild(c); return cc;
  }
  function ovBreakdown(title, rows) {
    var c = card([window.CRMUI.sectionHead(title)]);
    if (!rows || !rows.length) { c.appendChild(el("div", { class: "cf-empty", text: "No data yet." })); return c; }
    var l = el("div", { class: "cf-list" });
    rows.forEach(function (r) {
      l.appendChild(el("div", { class: "cf-item" }, [
        el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: r.label })]),
        el("span", { style: "font-weight:700", text: r.visits }),
      ]));
    });
    c.appendChild(l); return c;
  }

  async function renderOverview() {
    loading();
    try { OV_DATA = await window.AdminAPI.overview(OV_MONTH); }
    catch (e) { set(el("div", {}, [el("h1", { text: "Overview" }), el("div", { class: "cf-empty", text: UI.errMsg(e) })])); return; }
    OV_MONTH = OV_DATA.month;
    paintOverview();
  }
  function paintOverview() {
    disposeCharts();
    var data = OV_DATA, cur = data.currency || clubCur();
    var wrap = el("div", {});
    function shift(n) { OV_MONTH = addMonth(data.month, n); renderOverview(); }
    wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:8px" }, [
      el("h1", { style: "margin:0", text: "Overview" }),
      el("div", { class: "cf-row", style: "gap:6px;align-items:center" }, [
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "‹", onclick: function () { shift(-1); } }),
        el("span", { style: "font-weight:600;min-width:104px;text-align:center", text: monthLabel(data.month) }),
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "›", onclick: function () { shift(1); } }),
      ]),
    ]));
    wrap.appendChild(UI.subtabs(OV_TAB, OV_TABS, function (k) { OV_TAB = k; paintOverview(); }));
    var body = el("div", { style: "margin-top:12px" });
    wrap.appendChild(body);
    set(wrap);
    renderOvTab(body, data, cur);
  }
  function renderOvTab(body, data, cur) {
    if (OV_TAB === "bookings") return ovBookings(body, data, cur);
    if (OV_TAB === "revenue") return ovRevenue(body, data, cur);
    if (OV_TAB === "members") return ovMembers(body, data, cur);
    if (OV_TAB === "experience") return ovExperience(body, data, cur);
    if (OV_TAB === "courts") return ovCourts(body);
    return ovTraffic(body, data, cur);
  }
  function ovTraffic(body, data, cur) {
    var s = data.series, k = data.kpis;
    body.appendChild(card([window.CRMUI.stats([
      { value: k.visits, label: "Visits" },
      { value: k.unique_visitors, label: "Unique visitors" },
    ])]));
    mountChart(ovChartCard(body, "Visitors per day"), function () {
      return ovBase(data.days, [
        { name: "Visits", data: s.visits, color: "#9cc4b0" },
        { name: "Unique", data: s.unique_visitors, color: "#1f7a4d" },
      ]);
    });
    if (!k.visits) body.appendChild(el("div", { class: "cf-muted", style: "font-size:.82rem;margin:8px 2px", text: "No website visits recorded for this month yet." }));
    body.appendChild(ovBreakdown("Top sources", data.breakdowns.sources));
    body.appendChild(ovBreakdown("Top pages", data.breakdowns.top_pages));
    body.appendChild(ovBreakdown("Devices", data.breakdowns.by_device));
  }
  function ovBookings(body, data, cur) {
    var s = data.series, k = data.kpis;
    body.appendChild(card([window.CRMUI.stats([
      { value: k.bookings, label: "Bookings" },
      { value: k.member_bookings, label: "Member-covered" },
    ])]));
    mountChart(ovChartCard(body, "Bookings per day · by type"), function () {
      return ovBase(data.days, [
        { name: "Court", data: s.bookings_court, color: "#1f7a4d", stack: "b" },
        { name: "Lesson", data: s.bookings_lesson, color: "#e0a63c", stack: "b" },
        { name: "Class", data: s.bookings_class, color: "#4a7fb5", stack: "b" },
      ]);
    });
    body.appendChild(el("div", { class: "cf-muted", style: "font-size:.8rem;margin-top:8px", text: "Reconciles with Money → Bookings by day (confirmed / completed / no-show)." }));
  }
  function ovRevenue(body, data, cur) {
    var s = data.series, k = data.kpis;
    body.appendChild(card([window.CRMUI.stats([
      { value: money(k.revenue_gross_minor, cur), label: "Gross" },
      { value: money(k.revenue_net_minor, cur), label: "Net" },
      { value: money(k.refunded_minor, cur), label: "Refunded" },
    ])]));
    mountChart(ovChartCard(body, "Revenue per day"), function () {
      return ovBase(data.days, [
        { name: "Net", type: "bar", data: s.revenue_net_minor.map(mjr), color: "#1f7a4d" },
        { name: "Gross", type: "line", data: s.revenue_gross_minor.map(mjr), color: "#c79a3e" },
      ], { money: cur });
    });
    body.appendChild(el("div", { class: "cf-muted", style: "font-size:.8rem;margin-top:8px", text: "Gross ties out to Money → Sales by day. Net = gross − refunds." }));
  }
  function ovMembers(body, data, cur) {
    var s = data.series, k = data.kpis;
    body.appendChild(card([window.CRMUI.stats([
      { value: k.active_members, label: "Active members" },
      { value: k.new_clients, label: "New clients" },
      { value: k.total_clients, label: "Total clients" },
    ])]));
    mountChart(ovChartCard(body, "Active members per day", 260), function () {
      return ovBase(data.days, [{ name: "Active members", type: "line", data: s.active_members, color: "#4a7fb5", area: true }]);
    });
    mountChart(ovChartCard(body, "New clients per day", 240), function () {
      return ovBase(data.days, [{ name: "New clients", data: s.new_clients, color: "#1f7a4d" }]);
    });
  }
  function ovExperience(body, data, cur) {
    var s = data.series, k = data.kpis;
    body.appendChild(card([window.CRMUI.stats([
      { value: (k.nps_score == null ? "—" : k.nps_score), label: "NPS score" },
      { value: k.nps_responses, label: "Responses" },
    ])]));
    if (!k.nps_responses) { body.appendChild(el("div", { class: "cf-empty", text: "No NPS responses this month." })); return; }
    mountChart(ovChartCard(body, "NPS responses per day", 260), function () {
      return ovBase(data.days, [{ name: "Responses", data: s.nps_responses, color: "#1f7a4d" }]);
    });
  }
  async function ovCourts(body) {
    var box = card([window.CRMUI.sectionHead("Court utilisation"), el("div", { class: "cf-loading", text: "Loading…" })]);
    body.appendChild(box);
    try {
      var u = await window.AdminAPI.courtUtilisation(30);
      UI.clear(box);
      box.appendChild(window.CRMUI.sectionHead("Court utilisation · last 30 days"));
      box.appendChild(el("div", { class: "cf-muted", style: "font-size:.85rem;margin:-4px 0 10px", text: (u.overall_pct == null ? "Set court playing hours to see utilisation." : ("Overall " + u.overall_pct + "% of open court-hours booked. Cold cells are quiet slots to fill.")) }));
      box.appendChild(utilHeatmap(u));
    } catch (e) { UI.clear(box); box.appendChild(window.CRMUI.sectionHead("Court utilisation")); box.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
  }
  // A dependency-free CSS-grid heatmap: 7 weekday rows x hour columns, cells shaded by utilisation %
  // (or booking intensity where hours aren't set). Reuses cf-* tokens; scrolls on narrow screens.
  function utilHeatmap(u) {
    var cells = u.cells || [], byKey = {}, minH = 23, maxH = 6, has = false, maxBooked = 0;
    cells.forEach(function (c) {
      byKey[c.weekday + ":" + c.hour] = c;
      if (c.available_hours > 0 || c.booked_hours > 0) { has = true; minH = Math.min(minH, c.hour); maxH = Math.max(maxH, c.hour); }
      if (c.booked_hours > maxBooked) maxBooked = c.booked_hours;
    });
    if (!has) { minH = 6; maxH = 21; }
    var days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], hours = [];
    for (var h = minH; h <= maxH; h++) hours.push(h);
    function color(c) {
      if (!c) return "var(--canvas,#f1f4ef)";
      if (c.pct != null) return "rgba(31,122,77," + (0.10 + 0.85 * (c.pct / 100)).toFixed(2) + ")";
      if (c.booked_hours > 0 && maxBooked > 0) return "rgba(31,122,77," + (0.10 + 0.7 * (c.booked_hours / maxBooked)).toFixed(2) + ")";
      return "var(--canvas,#f1f4ef)";
    }
    var scroller = el("div", { style: "overflow-x:auto" });
    var grid = el("div", { style: "display:grid;grid-template-columns:auto repeat(" + hours.length + ",1fr);gap:3px;min-width:" + (60 + hours.length * 26) + "px" });
    grid.appendChild(el("div", {}));
    hours.forEach(function (hr) { grid.appendChild(el("div", { style: "font-size:10px;color:var(--muted);text-align:center", text: (hr < 10 ? "0" : "") + hr })); });
    days.forEach(function (dn, wd) {
      grid.appendChild(el("div", { style: "font-size:11px;color:var(--muted);align-self:center;padding-right:6px", text: dn }));
      hours.forEach(function (hr) {
        var c = byKey[wd + ":" + hr];
        var tip = dn + " " + hr + ":00 — " + (c ? (c.pct != null ? (c.pct + "% · " + c.booked_hours + "/" + c.available_hours + "h") : (c.booked_hours + "h booked")) : "closed");
        grid.appendChild(el("div", { title: tip, style: "height:22px;border-radius:4px;background:" + color(c) }));
      });
    });
    scroller.appendChild(grid);
    return scroller;
  }

  window.AdminApp = { start: start };
})();

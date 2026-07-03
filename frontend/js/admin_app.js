// admin_app.js — the OWNER/ADMIN console: a RESPONSIVE, drill-through SPA (bottom-nav on mobile,
// left side-rail on desktop — the owner runs the club from both). Same DNA as the client + coach
// apps (one shell, hash router, capability-driven detail pages, ONE event story reused everywhere).
// Reuses AdminAPI / AdminUI / CRMUI / ClassUI / window.API — this lane is an IA re-skin, not a rebuild.
// Design spec: docs/specs/ADMIN-REDESIGN.md. Non-admins are bounced to their own app.
(function () {
  var UI, el, principal = null, view, CLUB = null;
  var DIARY_VIEW = "day";   // day | week | month | classes (standard calendar views)
  var DIARY_COURT = "";     // resource_id filter ("" = all courts)
  var DIARY_COACH = "";     // coach_user_id filter ("" = all coaches)
  var DIARY_LISTS = null;   // cached {courts, coaches} for the filter dropdowns
  var money = function (m, c) { return UI.money(m || 0, c || "ZAR"); };
  function go(h) { location.hash = h; }

  // ---- boot ----------------------------------------------------------------
  async function start() {
    UI = window.UI; el = UI.el;
    await window.TFAuth.ready();
    if (!window.TFAuth.isAuthed()) { await window.TFAuth.requireAuth(); return; }
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
    try { CLUB = (await window.AdminAPI.club()).club || {}; paintBrand(); } catch (e) {}
    route();
  }

  function clubName() { return (CLUB && CLUB.name) || "Your club"; }
  function ownerName() { return (principal && principal.email ? principal.email.split("@")[0] : "there"); }
  function initials() { var n = clubName().trim().split(/\s+/); return ((n[0] || "C")[0] + (n.length > 1 ? n[n.length - 1][0] : "")).toUpperCase(); }
  function paintBrand() { var a = document.getElementById("cf-avatar"); if (a) a.textContent = initials(); var b = document.getElementById("cf-brandname"); if (b) b.textContent = clubName(); }
  function greet() { var h = new Date().getHours(); return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"; }

  // ---- shell ---------------------------------------------------------------
  var NAV = [
    { k: "home", ic: "⌂", label: "Home" },
    { k: "people", ic: "👥", label: "People" },
    { k: "money", ic: "💰", label: "Money" },
    { k: "diary", ic: "📅", label: "Diary" },
    { k: "setup", ic: "⚙", label: "Setup" },
  ];
  function renderShell() {
    document.body.classList.add("cf-app", "cf-admin");
    if (!document.getElementById("cf-appbar")) {
      document.body.insertBefore(el("div", { class: "cf-appbar", id: "cf-appbar" }, [
        el("div", { class: "cf-brand" }, [el("span", { class: "cf-logo", text: "NP" }), el("span", { id: "cf-brandname", text: clubName() })]),
        el("span", { class: "cf-spacer" }),
        el("div", { class: "cf-avatar", id: "cf-avatar", text: initials(), onclick: function () { go("#/setup"); } }),
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
  var TOP = ["home", "people", "money", "diary", "setup", "insights"];
  function route() {
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
    if (top === "insights") return renderInsights();
    if (top === "person") return renderPerson(parts[1]);
    if (top === "event") return renderEvent(parts[1]);
    return renderHome();
  }

  // ---- helpers -------------------------------------------------------------
  function set(node) { view.style.opacity = 0; UI.clear(view); view.appendChild(node); requestAnimationFrame(function () { view.style.transition = "opacity .16s"; view.style.opacity = 1; }); }
  function loading() {
    var n = el("div", { class: "cf-loading", style: "min-height:200px", text: "Loading…" });
    set(n);
    setTimeout(function () { if (n.isConnected && n.textContent === "Loading…") n.textContent = "Waking the club up — one moment…"; }, 3500);
  }
  var card = window.UI.card, backBar = window.UI.backBar;   // shared (FRONTEND-STANDARDISATION Wave 1)
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
    try { today = await window.API.master({ date_from: tk, date_to: tk }); } catch (e) {}
    var mo = hub.money || {}, pe = hub.people || {}, ap = hub.approvals || {}, cur = mo.currency || "ZAR";
    var wrap = el("div", {});
    wrap.appendChild(el("div", { class: "cf-greet" }, [
      el("div", {}, [el("h1", { text: greet() + ", " + ownerName() }), el("p", { text: clubName() + " — here's what needs you." })]),
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

    // Reports shortcut (Insights lives off the nav)
    wrap.appendChild(card([el("div", { class: "cf-item cf-item-tap", onclick: function () { go("#/insights"); } }, [
      el("span", { class: "cf-chip", text: "📊" }),
      el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: "Business insights" }), el("div", { class: "cf-item-s", text: "Traffic, customers, revenue, NPS" })]),
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
    wrap.appendChild(el("h1", { style: "margin:0 0 10px", text: "People" }));
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
    if (m) box.appendChild(el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Revoke", onclick: function () { revokeMembership(id, pn.name); } }));
    else box.appendChild(el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Grant membership", onclick: function () { grantMembership(id, pn.name); } }));
    return box;
  }
  function bookingsCard(title, rows, empty) {
    var c = card([window.CRMUI.sectionHead(title)], "cf-mt");
    if (!rows.length) { c.appendChild(el("div", { class: "cf-empty", text: empty })); return c; }
    var l = el("div", { class: "cf-list" });
    rows.forEach(function (b) {
      var k = (b.kind || "court").toLowerCase();
      var tap = !!b.booking_id;
      var row = el("div", { class: "cf-item" + (tap ? " cf-item-tap" : "") }, [
        el("span", { class: "cf-chip " + (["court", "lesson", "class"].indexOf(k) >= 0 ? k : "court"), text: k }),
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: (function () { try { return UI.fmtDate(b.starts_at) + "  " + UI.fmtTime(b.starts_at); } catch (e) { return b.starts_at || ""; } })() }),
          el("div", { class: "cf-item-s", text: [b.resource_name, b.coach_name].filter(Boolean).join(" · ") || "" }),
        ]),
        el("span", { class: "cf-chip " + (b.status === "confirmed" ? "confirmed" : "held"), text: b.status }),
        tap ? el("span", { class: "cf-muted", text: "›" }) : null,
      ].filter(Boolean));
      if (tap) row.addEventListener("click", function () { go("#/event/" + b.booking_id); });
      l.appendChild(row);
    });
    c.appendChild(l); return c;
  }
  // ---- person money/membership actions -------------------------------------
  async function grantMembership(id, name) {
    var v = window.prompt("Grant " + (name || "this member") + " a membership for how many months?", "1");
    if (v === null) return;
    var months = parseInt(v, 10); if (isNaN(months) || months < 1) { UI.toast("Enter a whole number of months.", "warn"); return; }
    try { await window.AdminAPI.grantMembership(id, { months: months }); UI.toast("Membership granted.", "info"); renderPerson(id); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
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
    ["revenue", "Revenue by service", "Net revenue split by service line"],
    ["settlement", "Coach settlement", "What each coach is owed · the club's cut"],
    ["approvals", "Approvals", "Refund requests awaiting your decision"],
    ["payments", "Online payments", "Recent card payments · refund"],
    ["activity", "Club activity", "Every payment, refund and adjustment"],
  ];
  function clubCur() { return (CLUB && CLUB.currency_code) || "ZAR"; }
  function renderMoney(section) {
    if (section === "sales") return moneySales();
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
        l.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: money(pay.amount_minor, pay.currency_code || cur) + (pay.payer_email ? " · " + pay.payer_email : "") }),
            el("div", { class: "cf-item-s", text: (function () { try { return UI.fmtDate(pay.created_at); } catch (e) { return ""; } })() }),
          ]),
          pay.refunded ? el("span", { class: "cf-chip held", text: "refunded" })
            : el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Refund", onclick: function () { refundPayment(pay, cur); } }),
        ]));
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
  function ymd(d) { return UI.dateKey(d); }
  function parseDay(day) { return new Date(day + "T12:00:00"); }
  function weekStartOf(d) { var x = new Date(d); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; } // Monday
  function longDay(d) { try { return d.toLocaleDateString("en-ZA", { weekday: "long", day: "numeric", month: "long" }); } catch (e) { return ymd(d); } }
  function evDateKey(ev) { try { return UI.dateKey(new Date(ev.starts_at)); } catch (e) { return (ev.starts_at || "").slice(0, 10); } }
  function diaryRange(view, day) {
    var d = parseDay(day);
    if (view === "week") { var s = weekStartOf(d), e = new Date(s); e.setDate(s.getDate() + 6); return { from: ymd(s), to: ymd(e) }; }
    if (view === "month") { return { from: ymd(new Date(d.getFullYear(), d.getMonth(), 1)), to: ymd(new Date(d.getFullYear(), d.getMonth() + 1, 0)) }; }
    return { from: day, to: day };
  }
  async function renderDiary(dateKey) {
    var day = dateKey || UI.dateKey(new Date());
    loading();
    var lists = await ensureDiaryLists();
    var wrap = el("div", {});
    wrap.appendChild(el("h1", { style: "margin:0 0 8px", text: "Diary" }));
    function seg(k, label) { return el("button", { class: DIARY_VIEW === k ? "on" : "", text: label, onclick: function () { DIARY_VIEW = k; renderDiary(day); } }); }
    wrap.appendChild(el("div", { class: "cf-segment cf-seg-lg" }, [seg("day", "Day"), seg("week", "Week"), seg("month", "Month"), seg("classes", "Classes")]));

    if (DIARY_VIEW === "classes") { var cbox = el("div", {}); wrap.appendChild(cbox); set(wrap); renderDiaryClasses(cbox); return; }

    // Filters: court + coach.
    var courtSel = el("select", { class: "cf-input", style: "max-width:180px" }, [el("option", { value: "", text: "All courts" })].concat(lists.courts.map(function (c) { return el("option", { value: c.id, text: c.name || "Court" }); })));
    courtSel.value = DIARY_COURT; courtSel.addEventListener("change", function () { DIARY_COURT = courtSel.value; renderDiary(day); });
    var coachSel = el("select", { class: "cf-input", style: "max-width:180px" }, [el("option", { value: "", text: "All coaches" })].concat(lists.coaches.map(function (c) { return el("option", { value: String(c.user_id), text: c.display_name || [c.first_name, c.surname].filter(Boolean).join(" ") || c.email }); })));
    coachSel.value = DIARY_COACH; coachSel.addEventListener("change", function () { DIARY_COACH = coachSel.value; renderDiary(day); });
    wrap.appendChild(el("div", { class: "cf-row", style: "gap:8px;flex-wrap:wrap;margin:10px 0" }, [courtSel, coachSel]));

    // Date navigation (unit follows the view).
    var d = parseDay(day);
    function shift(n) { var x = new Date(d); if (DIARY_VIEW === "month") x.setMonth(x.getMonth() + n); else if (DIARY_VIEW === "week") x.setDate(x.getDate() + 7 * n); else x.setDate(x.getDate() + n); go("#/diary/" + ymd(x)); }
    var label = DIARY_VIEW === "month" ? monthLabel(ymd(d).slice(0, 7)) : (DIARY_VIEW === "week" ? ("Week of " + dayLabel(ymd(weekStartOf(d)))) : longDay(d));
    wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:10px" }, [
      el("div", { style: "font-weight:600", text: label }),
      el("div", { class: "cf-row", style: "gap:6px" }, [
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "‹", onclick: function () { shift(-1); } }),
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Today", onclick: function () { go("#/diary/" + UI.dateKey(new Date())); } }),
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "›", onclick: function () { shift(1); } }),
      ]),
    ]));
    var body = el("div", {}, [el("div", { class: "cf-loading", text: "Loading…" })]);
    wrap.appendChild(body);
    set(wrap);

    var range = diaryRange(DIARY_VIEW, day);
    var events = [];
    try { events = (await window.API.master({ date_from: range.from, date_to: range.to })).events || []; } catch (e) {}
    events = events.filter(function (ev) {
      if (ev.status === "cancelled") return false;
      if (DIARY_COURT && String(ev.resource_id) !== String(DIARY_COURT)) return false;
      if (DIARY_COACH && String(ev.coach_user_id) !== String(DIARY_COACH)) return false;
      return true;
    }).sort(function (a, b) { return String(a.starts_at).localeCompare(String(b.starts_at)); });

    UI.clear(body);
    if (DIARY_VIEW === "month") body.appendChild(diaryMonthView(day, events));
    else if (DIARY_VIEW === "week") body.appendChild(diaryWeekView(day, events));
    else body.appendChild(diaryDayView(events));
    body.appendChild(el("p", { class: "cf-muted", style: "font-size:.82rem;margin-top:12px" }, [
      document.createTextNode("Need the full drag-and-drop timeline (walk-ins · block time · desk-pay)? "),
      el("a", { href: "/admin-classic#diary", text: "Open the classic diary ›" }),
    ]));
  }
  function diaryEventRow(ev) {
    var t = (ev.booking_type || "court").toLowerCase();
    return el("div", { class: "cf-item" + (ev.id ? " cf-item-tap" : ""), onclick: function () { if (ev.id) go("#/event/" + ev.id); } }, [
      el("span", { class: "cf-chip " + (["court", "lesson", "class"].indexOf(t) >= 0 ? t : "court"), text: (function () { try { return UI.fmtTime(ev.starts_at); } catch (e) { return t; } })() }),
      el("div", { class: "cf-item-main" }, [
        el("div", { class: "cf-item-t", text: ev.resource_name || typeLabel(t) }),
        el("div", { class: "cf-item-s", text: [ev.booked_by_name, ev.coach_name].filter(Boolean).join(" · ") || t }),
      ]),
      UI.statusChip(ev.status),
    ]);
  }
  function diaryDayView(events) {
    if (!events.length) return el("div", { class: "cf-empty", text: "Nothing booked." });
    var c = card([]), l = el("div", { class: "cf-list" });
    events.forEach(function (ev) { l.appendChild(diaryEventRow(ev)); });
    c.appendChild(l); return c;
  }
  function diaryWeekView(day, events) {
    var s = weekStartOf(parseDay(day)), byDate = {};
    events.forEach(function (ev) { var k = evDateKey(ev); (byDate[k] = byDate[k] || []).push(ev); });
    var box = el("div", {});
    for (var i = 0; i < 7; i++) {
      var dd = new Date(s); dd.setDate(s.getDate() + i); var k = ymd(dd); var evs = byDate[k] || [];
      box.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin:14px 2px 6px" }, [
        el("div", { style: "font-weight:700", text: dayLabel(k) }),
        el("div", { class: "cf-muted", style: "font-size:.82rem", text: evs.length ? (evs.length + " booked") : "—" }),
      ]));
      if (evs.length) { var c = card([]), l = el("div", { class: "cf-list" }); evs.forEach(function (ev) { l.appendChild(diaryEventRow(ev)); }); c.appendChild(l); box.appendChild(c); }
    }
    return box;
  }
  function diaryMonthView(day, events) {
    var d = parseDay(day), y = d.getFullYear(), m = d.getMonth();
    var counts = {}; events.forEach(function (ev) { var k = evDateKey(ev); counts[k] = (counts[k] || 0) + 1; });
    var maxC = 0; Object.keys(counts).forEach(function (k) { if (counts[k] > maxC) maxC = counts[k]; });
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
        onclick: (function (kk) { return function () { DIARY_VIEW = "day"; go("#/diary/" + kk); }; })(k),
      }, [
        el("div", { style: "font-size:12px;font-weight:600", text: String(dn) }),
        cnt ? el("div", { style: "font-size:11px;color:var(--muted);margin-top:2px", text: cnt + (cnt === 1 ? " booking" : " bookings") }) : null,
      ].filter(Boolean)));
    }
    return el("div", {}, [el("div", { class: "cf-card" }, [grid])]);
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
    var m = modal("Settle at desk");
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
  var SETUP_SECTIONS = [
    ["profile", "Club profile & payments", "Name, contact, branding, accepted payment methods"],
    ["courts", "Courts & hours", "Courts, surfaces and weekly playing hours"],
    ["services", "Services & pricing", "Lessons, classes, court hire — prices, packages, commission"],
    ["memberships", "Memberships", "Membership tiers and term plans"],
    ["packs", "Session packs", "Prepaid bundles"],
    ["coaches", "Coaches & commission", "Invite, hide or remove coaches · rent + commission"],
  ];
  function renderSetup(section) {
    if (!section) return setupMenu();
    var host = el("div", {});
    set(el("div", {}, [backBar("Setup", "#/setup"), host]));
    host.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
    (async function () {
      try {
        if (section === "profile") {
          var d = await window.AdminAPI.onboarding().catch(function () { return {}; });
          UI.clear(host);
          window.AdminUI.clubProfile(host, d || {}, { saveLabel: "Save changes" });
          setupPayments(host, (d && d.policy) || {});
        } else if (section === "courts") { UI.clear(host); window.AdminUI.courtsManage(host); }
        else if (section === "memberships") { UI.clear(host); window.AdminUI.membershipServices(host); }
        else if (section === "packs") { UI.clear(host); window.AdminUI.bundlePlans(host); }
        else if (section === "coaches") { UI.clear(host); window.AdminUI.coachManage(host); }
        else if (section === "services") setupServices(host);
        else { UI.clear(host); host.appendChild(el("div", { class: "cf-empty", text: "Unknown section." })); }
      } catch (e) { UI.clear(host); host.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
    })();
  }
  function setupMenu() {
    var wrap = el("div", {});
    wrap.appendChild(el("h1", { style: "margin:0 0 4px", text: "Setup" }));
    wrap.appendChild(el("p", { class: "cf-muted", style: "margin:0 0 14px", text: "Configure your club. Changes save per section." }));
    var c = card([]), l = el("div", { class: "cf-list" });
    SETUP_SECTIONS.forEach(function (s) {
      l.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { go("#/setup/" + s[0]); } }, [
        el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: s[1] }), el("div", { class: "cf-item-s", text: s[2] })]),
        el("span", { class: "cf-muted", text: "›" }),
      ]));
    });
    c.appendChild(l); wrap.appendChild(c);
    wrap.appendChild(el("p", { class: "cf-muted", style: "font-size:.82rem;margin-top:14px" }, [
      document.createTextNode("Classes are scheduled under Diary → Classes. Prefer the classic console? "),
      el("a", { href: "/admin-classic", text: "Open classic ›" }),
    ]));
    set(wrap);
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
  var SVC_KIND = "lesson", SVC_LIFE = "active";
  function setupServices(host) {
    UI.clear(host);
    host.appendChild(el("div", { class: "cf-loading", text: "Loading services…" }));
    window.TFAuth.apiJSON("/api/services").then(function (res) {
      drawSetupServices(host, (res && res.services) || []);
    }, function (e) { UI.clear(host); host.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
  }
  function drawSetupServices(host, svcs) {
    UI.clear(host);
    host.appendChild(el("div", { class: "cf-card" }, [
      el("h2", { text: "Services" }),
      el("p", { class: "cf-muted", text: "Lessons, classes and court hire. Prices, payment, packages and commission live behind each block — tap to edit." }),
    ]));
    host.appendChild(UI.subtabs(SVC_KIND, [["lesson", "Lessons"], ["class", "Classes"], ["court", "Courts"]], function (k) { SVC_KIND = k; drawSetupServices(host, svcs); }));
    host.appendChild(UI.lifecycleBar(SVC_LIFE, function (f) { SVC_LIFE = f; drawSetupServices(host, svcs); }));
    var shown = svcs.filter(function (s) { return s.service_kind === SVC_KIND && (SVC_LIFE === "all" || s.status === SVC_LIFE); });
    if (!shown.length) { host.appendChild(el("div", { class: "cf-card cf-empty", text: "No " + (SVC_LIFE === "all" ? "" : SVC_LIFE + " ") + SVC_KIND + " services." })); return; }
    shown.forEach(function (s) {
      var sub = [];
      if ((s.service_kind === "lesson" || s.service_kind === "class") && s.coach_name) sub.push("Coach: " + s.coach_name);
      var v = s.variations || [];
      sub.push(v.length ? v.slice(0, 4).map(function (x) { return x.duration_minutes ? (x.duration_minutes + " min " + money(x.amount_minor)) : money(x.amount_minor); }).join("  ·  ") : "No prices set yet");
      var cardEl = el("div", { class: "cf-card cf-pickable" }, [
        el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap" }, [
          el("div", {}, [
            el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [el("span", { class: "cf-chip " + s.service_kind, text: s.service_kind }), el("strong", { text: s.name || "Service" }), s.status !== "active" ? UI.statusChip(s.status) : null].filter(Boolean)),
            el("div", { class: "cf-muted", style: "font-size:.82rem;margin-top:5px", text: sub.join("  ·  ") }),
          ]),
          el("span", { class: "cf-muted", text: "Edit ›" }),
        ]),
      ]);
      if (s.status !== "active") cardEl.style.opacity = "0.6";
      cardEl.addEventListener("click", function () { window.ServiceEditor.open(s.id, { host: host, onClose: function () { setupServices(host); } }); });
      host.appendChild(cardEl);
    });
  }

  // ---- INSIGHTS (Phase-2 court-utilisation heatmap + the Business Overview dashboard) -------
  function renderInsights() {
    var wrap = el("div", {});
    wrap.appendChild(backBar("Home", "#/home"));
    wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:10px" }, [
      el("h1", { style: "margin:0", text: "Business insights" }),
      el("a", { class: "cf-btn cf-btn-sm cf-btn-ghost", href: "/overview.html", target: "_blank", text: "Open full page ›" }),
    ]));
    var utilBox = card([window.CRMUI.sectionHead("Court utilisation"), el("div", { class: "cf-loading", text: "Loading…" })]);
    wrap.appendChild(utilBox);
    wrap.appendChild(el("iframe", { src: "/overview.html", title: "Business Overview", style: "width:100%;height:calc(100vh - 260px);min-height:520px;border:1px solid var(--border,#e5e7eb);border-radius:14px;background:#fff" }));
    set(wrap);
    (async function () {
      try {
        var u = await window.AdminAPI.courtUtilisation(30);
        UI.clear(utilBox);
        utilBox.appendChild(window.CRMUI.sectionHead("Court utilisation · last 30 days"));
        utilBox.appendChild(el("div", { class: "cf-muted", style: "font-size:.85rem;margin:-4px 0 10px", text: (u.overall_pct == null ? "Set court playing hours to see utilisation." : ("Overall " + u.overall_pct + "% of open court-hours booked. Cold cells are quiet slots to fill.")) }));
        utilBox.appendChild(utilHeatmap(u));
      } catch (e) { UI.clear(utilBox); utilBox.appendChild(window.CRMUI.sectionHead("Court utilisation")); utilBox.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
    })();
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

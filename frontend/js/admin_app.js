// admin_app.js — the OWNER/ADMIN console: a RESPONSIVE, drill-through SPA (bottom-nav on mobile,
// left side-rail on desktop — the owner runs the club from both). Same DNA as the client + coach
// apps (one shell, hash router, capability-driven detail pages, ONE event story reused everywhere).
// Reuses AdminAPI / AdminUI / CRMUI / ClassUI / window.API — this lane is an IA re-skin, not a rebuild.
// Design spec: docs/specs/ADMIN-REDESIGN.md. Non-admins are bounced to their own app.
(function () {
  var UI, el, principal = null, view, CLUB = null;
  var DIARY_TAB = "agenda";
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
    if (top === "money") return renderMoney();
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
  function card(children, extra) { return el("div", { class: "cf-card" + (extra ? " " + extra : "") }, children); }
  function backBar(label, hash) { return el("div", { class: "cf-backbar" }, [el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "‹ " + (label || "Back"), onclick: function () { hash ? go(hash) : history.back(); } })]); }
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
    var owed = (pn.statement && pn.statement.items) || [];
    if (!owed.length) moneyCard.appendChild(el("div", { class: "cf-empty", text: "Nothing owed — all settled. 🎉" }));
    else {
      var ol = el("div", { class: "cf-list" });
      owed.forEach(function (it) { ol.appendChild(owedRow(it, cur, id)); });
      moneyCard.appendChild(ol);
    }
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
  function owedRow(it, cur, id) {
    var actions = el("div", { class: "cf-row", style: "gap:6px" }, [
      el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Void", onclick: function (e) { e.stopPropagation(); voidOrder(id, it, false); } }),
      el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Write off", onclick: function (e) { e.stopPropagation(); voidOrder(id, it, true); } }),
    ]);
    return el("div", { class: "cf-item" }, [
      el("div", { class: "cf-item-main" }, [
        el("div", { class: "cf-item-t", text: it.description || it.category || "Owed" }),
        el("div", { class: "cf-item-s", text: [it.category, it.coach_name, (function () { try { return it.date ? UI.fmtDate(it.date) : ""; } catch (e) { return ""; } })()].filter(Boolean).join(" · ") }),
      ]),
      el("span", { style: "font-weight:700", text: money(it.amount_minor, it.currency || cur) }),
      actions,
    ]);
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
  // ---- MONEY (financial cockpit — drill-through, reuses the cockpit endpoints) --------------
  async function renderMoney() {
    loading();
    var r = await Promise.all([
      window.AdminAPI.cockpitSummary().catch(function () { return {}; }),
      window.AdminAPI.cockpitCoachEarnings().catch(function () { return { coaches: [] }; }),
      window.AdminAPI.cockpitRevenue().catch(function () { return { revenue: [] }; }),
      window.AdminAPI.refundRequests({ status: "pending" }).catch(function () { return { requests: [] }; }),
      window.AdminAPI.payments().catch(function () { return { payments: [] }; }),
      window.AdminAPI.activity(80).catch(function () { return { activity: [] }; }),
    ]);
    var summary = r[0] || {}, coaches = r[1].coaches || [], revenue = r[2].revenue || [],
        reqs = r[3].requests || [], pays = r[4].payments || [], activity = r[5].activity || [];
    var cur = summary.currency || "ZAR";
    var wrap = el("div", {});
    wrap.appendChild(el("h1", { style: "margin:0 0 12px", text: "Money" }));

    wrap.appendChild(card([window.CRMUI.stats([
      { value: money(summary.net_revenue_minor, cur), label: "Net revenue" },
      { value: money(summary.commission_earned_minor, cur), label: "Commission kept" },
      { value: money(summary.rent_due_minor, cur), label: "Rent due" },
      { value: money(summary.mrr_minor, cur), label: "MRR" },
      { value: (summary.active_members || 0), label: "Active members" },
      { value: (summary.lessons_paid || 0), label: "Lessons" },
    ])]));

    // Approvals — refund requests (approve executes the Yoco refund; decline just closes it).
    if (reqs.length) {
      var apc = card([window.CRMUI.sectionHead("To approve · refund requests")], "cf-mt");
      var al = el("div", { class: "cf-list" });
      reqs.forEach(function (rq) {
        al.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: (rq.requester_name || "A member") + " · " + money(rq.amount_minor != null ? rq.amount_minor : rq.order_amount_minor, rq.currency_code || cur) }),
            el("div", { class: "cf-item-s", text: [rq.item_description || "Order", rq.reason ? "“" + rq.reason + "”" : ""].filter(Boolean).join(" · ") }),
          ]),
          el("div", { class: "cf-row", style: "gap:6px" }, [
            el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "Approve", onclick: function () { decideRefund(rq, "approve"); } }),
            el("button", { class: "cf-btn cf-btn-sm cf-btn-danger", text: "Decline", onclick: function () { decideRefund(rq, "decline"); } }),
          ]),
        ]));
      });
      apc.appendChild(al); wrap.appendChild(apc);
    }

    // Per-coach settlement → drill to the coach's record (the settlement block from the person 360).
    var sc = card([window.CRMUI.sectionHead("Coach settlement")], "cf-mt");
    if (!coaches.length) sc.appendChild(el("div", { class: "cf-empty", text: "No coach settlement yet." }));
    else {
      var sl = el("div", { class: "cf-list" });
      coaches.forEach(function (c) {
        sl.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { go("#/person/" + c.coach_user_id); } }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: c.coach_name || "Coach" }),
            el("div", { class: "cf-item-s", text: (c.lesson_count || 0) + " lessons · commission " + money(c.commission_earned_minor, cur) }),
          ]),
          el("span", { style: "font-weight:700", text: money(c.net_to_coach_minor, cur) }),
          el("span", { class: "cf-muted", text: "›" }),
        ]));
      });
      sc.appendChild(sl); wrap.appendChild(sc);
    }

    // Revenue by service line (net).
    if (revenue.length) {
      var byKind = {};
      revenue.forEach(function (x) { byKind[x.service_kind] = (byKind[x.service_kind] || 0) + (x.net_minor || 0); });
      var rc = card([window.CRMUI.sectionHead("Revenue by service line")], "cf-mt");
      var rl = el("div", { class: "cf-list" });
      Object.keys(byKind).sort(function (a, b) { return byKind[b] - byKind[a]; }).forEach(function (k) {
        rl.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: k })]),
          el("span", { style: "font-weight:700", text: money(byKind[k], cur) }),
        ]));
      });
      rc.appendChild(rl); wrap.appendChild(rc);
    }

    // Recent online payments (refund-only / refund & cancel).
    if (pays.length) {
      var pc = card([window.CRMUI.sectionHead("Recent online payments")], "cf-mt");
      var pl = el("div", { class: "cf-list" });
      pays.slice(0, 20).forEach(function (pay) {
        pl.appendChild(el("div", { class: "cf-item" }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: money(pay.amount_minor, pay.currency_code || cur) + (pay.payer_email ? " · " + pay.payer_email : "") }),
            el("div", { class: "cf-item-s", text: (function () { try { return UI.fmtDate(pay.created_at); } catch (e) { return ""; } })() }),
          ]),
          pay.refunded ? el("span", { class: "cf-chip held", text: "refunded" })
            : el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Refund", onclick: function () { refundPayment(pay, cur); } }),
        ]));
      });
      pc.appendChild(pl); wrap.appendChild(pc);
    }

    // Club transaction log.
    var actc = card([window.CRMUI.sectionHead("Club activity")], "cf-mt");
    actc.appendChild(window.CRMUI.activityFeed(activity, { empty: "No activity yet." }));
    wrap.appendChild(actc);
    set(wrap);
  }
  async function decideRefund(rq, action) {
    var isA = action === "approve";
    var note = window.prompt(isA ? "Approve & refund this via Yoco? Optional note:" : "Decline this request? Reason (shown to the member):", "");
    if (note === null) return;
    try {
      if (isA) { var alsoCancel = window.confirm("Also CANCEL the booking + free the slot?\n\nOK = refund + cancel.   Cancel = refund only."); await window.AdminAPI.approveRefundRequest(rq.id, { note: note, cancel_booking: alsoCancel }); }
      else await window.AdminAPI.declineRefundRequest(rq.id, { note: note });
      UI.toast(isA ? "Approved." : "Declined.", "info"); renderMoney();
    } catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }
  async function refundPayment(pay, cur) {
    if (!window.confirm("Refund " + money(pay.amount_minor, pay.currency_code || cur) + " to " + (pay.payer_email || "the customer") + " via Yoco?")) return;
    var alsoCancel = window.confirm("Also CANCEL the booking + free the slot?\n\nOK = refund + cancel.   Cancel = refund only.");
    try { await window.AdminAPI.yocoRefund({ order_id: pay.order_id, cancel_booking: alsoCancel }); UI.toast("Refunded.", "info"); renderMoney(); }
    catch (e) { UI.toast(UI.errMsg(e), "error"); }
  }

  // ---- DIARY (day agenda + Classes; every booking → the event story) ------------------------
  async function renderDiary(dateKey) {
    var day = dateKey || UI.dateKey(new Date());
    loading();
    var events = [];
    try { events = (await window.API.master({ date_from: day, date_to: day })).events || []; } catch (e) {}
    events = events.filter(function (e) { return e.status !== "cancelled"; })
      .sort(function (a, b) { return String(a.starts_at).localeCompare(String(b.starts_at)); });
    var d = new Date(day + "T12:00:00");
    function shift(n) { var x = new Date(d.getTime() + n * 86400000); go("#/diary/" + UI.dateKey(x)); }
    var wrap = el("div", {});
    wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:6px" }, [
      el("h1", { style: "margin:0", text: "Diary" }),
      el("div", { class: "cf-row", style: "gap:6px;align-items:center" }, [
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "‹", onclick: function () { shift(-1); } }),
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Today", onclick: function () { go("#/diary/" + UI.dateKey(new Date())); } }),
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "›", onclick: function () { shift(1); } }),
      ]),
    ]));
    wrap.appendChild(el("p", { class: "cf-muted", style: "margin:-2px 0 10px;font-size:.9rem", text: (function () { try { return d.toLocaleDateString("en-ZA", { weekday: "long", day: "numeric", month: "long" }); } catch (e) { return day; } })() }));
    wrap.appendChild(el("div", { class: "cf-segment cf-seg-lg" }, [
      el("button", { class: DIARY_TAB === "agenda" ? "on" : "", text: "Agenda", onclick: function () { DIARY_TAB = "agenda"; renderDiary(day); } }),
      el("button", { class: DIARY_TAB === "classes" ? "on" : "", text: "Classes", onclick: function () { DIARY_TAB = "classes"; renderDiary(day); } }),
    ]));
    var body = el("div", {});
    wrap.appendChild(body);
    if (DIARY_TAB === "classes") { set(wrap); renderDiaryClasses(body); return; }

    if (!events.length) body.appendChild(el("div", { class: "cf-empty", text: "Nothing booked on this day." }));
    else {
      var c = card([]), l = el("div", { class: "cf-list" });
      events.forEach(function (ev) {
        var t = (ev.booking_type || "court").toLowerCase();
        l.appendChild(el("div", { class: "cf-item" + (ev.id ? " cf-item-tap" : "") , onclick: function () { if (ev.id) go("#/event/" + ev.id); } }, [
          el("span", { class: "cf-chip " + (["court", "lesson", "class"].indexOf(t) >= 0 ? t : "court"), text: (function () { try { return UI.fmtTime(ev.starts_at); } catch (e) { return t; } })() }),
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: ev.resource_name || typeLabel(t) }),
            el("div", { class: "cf-item-s", text: [ev.booked_by_name, ev.coach_name].filter(Boolean).join(" · ") || t }),
          ]),
          UI.statusChip(ev.status),
        ]));
      });
      c.appendChild(l); body.appendChild(c);
    }
    // Full drag-and-drop timeline (walk-ins, block time, desk-pay) still lives in the classic console.
    body.appendChild(el("p", { class: "cf-muted", style: "font-size:.82rem;margin-top:12px" }, [
      document.createTextNode("Need the full drag-and-drop timeline (walk-ins · block time · desk-pay)? "),
      el("a", { href: "/admin-classic#diary", text: "Open the classic diary ›" }),
    ]));
    set(wrap);
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
  function evBtn(text, tone, onclick) { return el("button", { class: "cf-btn cf-btn-sm" + (tone ? " cf-btn-" + tone : ""), text: text, onclick: onclick }); }
  function evAct(fn, ok, then) { fn().then(function () { UI.toast(ok, "info"); (then || route)(); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); }
  function kv(k, v) { return el("div", { class: "cf-kv" }, [el("div", { class: "cf-kv-k", text: k }), el("div", { class: "cf-kv-v" }, typeof v === "string" ? [document.createTextNode(v)] : [v])]); }

  async function renderEvent(id) {
    loading();
    var b;
    try { b = (await window.AdminAPI.bookingStory(id)).booking; }
    catch (e) { set(el("div", {}, [backBar("Back"), el("div", { class: "cf-empty", text: UI.errMsg(e) })])); return; }
    var ch = b.charge || {}, cur = ch.currency || "ZAR", cl = b.client || {}, co = b.coach || {}, can = b.can || {};
    var refresh = function () { renderEvent(id); };
    var wrap = el("div", {});
    wrap.appendChild(backBar("Back"));

    // Header — type chip, date, time range, status.
    var head = card([
      el("div", { class: "cf-detail-h" }, [
        el("div", {}, [
          el("span", { class: "cf-chip " + b.booking_type, text: typeLabel(b.booking_type) + (b.duration_minutes ? " · " + b.duration_minutes + " min" : "") }),
          el("h1", { style: "margin:8px 0 2px;font-size:1.3rem", text: (function () { try { return UI.fmtDate(b.starts_at); } catch (e) { return b.starts_at || ""; } })() }),
          el("div", { class: "cf-muted", text: timeRange(b) }),
        ]),
        UI.statusChip(b.status),
      ]),
    ]);
    var det = el("div", { style: "margin-top:6px" });
    // Client — contact + drill to their person 360.
    if (cl.name) {
      var cc = el("div", { class: "cf-row", style: "gap:8px;margin-top:4px;flex-wrap:wrap" });
      if (cl.phone) cc.appendChild(el("a", { class: "cf-btn cf-btn-sm cf-btn-ghost", href: "tel:" + cl.phone, text: "📞 Call" }));
      if (cl.email) cc.appendChild(el("a", { class: "cf-btn cf-btn-sm cf-btn-ghost", href: "mailto:" + cl.email, text: "✉ Email" }));
      if (cl.user_id) cc.appendChild(el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Full record ›", onclick: function () { go("#/person/" + cl.user_id); } }));
      det.appendChild(kv("Client", el("div", {}, [el("div", { style: "font-weight:600", text: cl.name }), el("div", { class: "cf-muted", style: "font-size:.85rem", text: [cl.email, cl.phone].filter(Boolean).join(" · ") }), cc])));
    }
    // Coach — drill to their record too.
    if (co.name) {
      var coBox = el("div", { style: "font-weight:600" }, [document.createTextNode(co.name)]);
      if (co.user_id) coBox = el("div", {}, [el("div", { style: "font-weight:600", text: co.name }), el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", style: "margin-top:4px", text: "Coach record ›", onclick: function () { go("#/person/" + co.user_id); } })]);
      det.appendChild(kv("Coach", coBox));
    }
    if (b.venue && (b.venue.club_name || b.court_name)) det.appendChild(kv("Where", el("div", {}, [el("div", { text: [b.venue.club_name, b.court_name].filter(Boolean).join(" · ") || "—" }), b.venue.address ? el("div", { class: "cf-muted", style: "font-size:.85rem", text: b.venue.address }) : null].filter(Boolean))));
    if (b.players && b.players.length) det.appendChild(kv("Players", b.players.map(function (p) { return p.name + (p.attended === true ? " ✓" : p.attended === false ? " ✗" : ""); }).join(", ")));
    det.appendChild(kv("Charge", el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [el("span", { style: "font-weight:700", text: ch.status === "covered" ? "Covered" : money(ch.amount_minor, cur) }), UI.statusChip(ch.status)])));
    if (b.arrears) det.appendChild(kv("Coaching", el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [el("span", { style: "font-weight:700", text: money(b.arrears.gross_minor, cur) }), UI.statusChip(b.arrears.status)])));
    if (b.notes) det.appendChild(kv("Notes", b.notes));
    head.appendChild(det);
    wrap.appendChild(head);

    // Actions — grouped: approval · lifecycle · order money · coaching money.
    var order_id = b.order_id;
    var groups = [
      ["Approval", [
        can.accept && evBtn("Accept", "primary", function () { evAct(function () { return window.API.acceptBooking(b.id); }, "Confirmed.", refresh); }),
        can.propose && evBtn("Propose time", "", function () { timeModal("Propose a time", b, function (body) { return window.API.proposeTime(b.id, body); }, "Proposed.", refresh); }),
        can.decline && evBtn("Decline", "danger", function () { evAct(function () { return window.API.declineBooking(b.id, {}); }, "Declined.", function () { history.back(); }); }),
      ]],
      ["Session", [
        can.mark_completed && evBtn("Mark completed", "primary", function () { evAct(function () { return window.API.setBookingStatus(b.id, { status: "completed" }); }, "Marked completed.", refresh); }),
        can.mark_no_show && evBtn("No-show", "", function () { evAct(function () { return window.API.setBookingStatus(b.id, { status: "no_show" }); }, "Marked no-show.", refresh); }),
        can.reschedule && evBtn("Reschedule", "", function () { timeModal("Reschedule", b, function (body) { return window.API.rescheduleBooking(b.id, { starts_at: body.starts_at, ends_at: body.ends_at, scope: "this" }); }, "Rescheduled.", refresh); }),
        can.reassign_coach && evBtn("Reassign coach", "", function () { reassignModal(b, refresh); }),
        can.cancel && !can.decline && evBtn("Cancel booking", "danger", function () { if (!window.confirm("Cancel this booking and free the slot?")) return; evAct(function () { return window.API.cancelBooking(b.id, { reason: "admin cancelled" }); }, "Cancelled.", function () { history.back(); }); }),
        can.add_to_calendar && el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Add to calendar", onclick: function () { addToCalendar(b.ics_url); } }),
      ]],
      ["Client charge", [
        can.desk_pay && order_id && evBtn("Settle at desk", "primary", function () { deskPayModal(order_id, ch, refresh); }),
        can.refund && order_id && evBtn("Refund", "", function () { refundModal(order_id, ch, refresh); }),
        can.void && order_id && evBtn("Void", "", function () { if (!window.confirm("Void this charge (a mistake)? It drops off the client's statement.")) return; evAct(function () { return window.AdminAPI.voidOrder(order_id, { write_off: false }); }, "Voided.", refresh); }),
        can.write_off && order_id && evBtn("Write off", "danger", function () { if (!window.confirm("Write off (forgive) this charge? No money is collected.")) return; evAct(function () { return window.AdminAPI.voidOrder(order_id, { write_off: true }); }, "Written off.", refresh); }),
      ]],
      ["Coaching charge", [
        can.collect && b.arrears && evBtn("Mark collected", "primary", function () { evAct(function () { return window.AdminAPI.arrearsCollected(b.arrears.id); }, "Marked collected.", refresh); }),
        can.discount && b.arrears && evBtn("Discount", "", function () { var v = window.prompt("New coaching amount (e.g. 250.00):", ((b.arrears.gross_minor || 0) / 100).toFixed(2)); if (v === null) return; var f = parseFloat(v); if (isNaN(f) || f < 0) { UI.toast("Enter a valid amount.", "warn"); return; } evAct(function () { return window.AdminAPI.arrearsAdjust(b.arrears.id, { gross_minor: Math.round(f * 100) }); }, "Discounted.", refresh); }),
        can.write_off_coaching && b.arrears && evBtn("Write off coaching", "danger", function () { var r = window.prompt("Write off this coaching charge? Reason (shown to coach & client):", ""); if (r === null) return; evAct(function () { return window.AdminAPI.arrearsAdjust(b.arrears.id, { status: "written_off", reason: r }); }, "Written off.", refresh); }),
      ]],
    ];
    groups.forEach(function (g) {
      var kids = (g[1] || []).filter(Boolean);
      if (!kids.length) return;
      var row = el("div", { class: "cf-row", style: "gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px" }, [el("span", { class: "cf-muted", style: "font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;width:100%", text: g[0] })].concat(kids));
      wrap.appendChild(row);
    });
    set(wrap);
  }

  // ---- event-story modals + helpers ----------------------------------------
  function modal(title) {
    var bg = el("div", { class: "cf-modal-bg" }), body = el("div", {});
    bg.appendChild(el("div", { class: "cf-modal cf-modal-lg" }, [el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:6px" }, [el("h2", { style: "margin:0", text: title }), el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "✕", onclick: function () { close(); } })]), body]));
    document.body.appendChild(bg);
    function close() { if (bg.parentNode) document.body.removeChild(bg); }
    return { body: body, close: close };
  }
  function toLocal(iso) { try { var d = new Date(iso), p = function (n) { return (n < 10 ? "0" : "") + n; }; return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + "T" + p(d.getHours()) + ":" + p(d.getMinutes()); } catch (e) { return ""; } }
  function addToCalendar(icsUrl) {
    window.TFAuth.apiFetch(icsUrl).then(function (r) { if (!r.ok) throw new Error("Couldn't build the calendar file."); return r.blob(); })
      .then(function (blob) { var u = URL.createObjectURL(blob); var a = document.createElement("a"); a.href = u; a.download = "booking.ics"; document.body.appendChild(a); a.click(); a.remove(); setTimeout(function () { URL.revokeObjectURL(u); }, 1500); })
      .catch(function (e) { UI.toast(UI.errMsg(e), "error"); });
  }
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

  // ---- INSIGHTS (embed the Business Overview dashboard) -------------------------------------
  function renderInsights() {
    var wrap = el("div", {});
    wrap.appendChild(backBar("Home", "#/home"));
    wrap.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:10px" }, [
      el("h1", { style: "margin:0", text: "Business insights" }),
      el("a", { class: "cf-btn cf-btn-sm cf-btn-ghost", href: "/overview.html", target: "_blank", text: "Open full page ›" }),
    ]));
    wrap.appendChild(el("iframe", { src: "/overview.html", title: "Business Overview", style: "width:100%;height:calc(100vh - 210px);min-height:560px;border:1px solid var(--border,#e5e7eb);border-radius:14px;background:#fff" }));
    set(wrap);
  }

  window.AdminApp = { start: start };
})();

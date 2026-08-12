// client.js — the CLIENT app: a single-page, bottom-nav shell (Home · Book · Bookings · Billing)
// with drill-through everywhere. Every booking / charge / payment opens its FULL story — no dumps.
// Reuses the plumbing (TFAuth, API, Pay, UI, the cf-* design system) and mounts the existing
// booking flow (BookFlow) for the Book tab. Staff are redirected to their own consoles.
(function () {
  var UI, el, principal = null;
  var view;                       // #cf-main (the routed content area)
  var DATA = {};                  // small cache; refreshed after actions
  var NAME = "";
  var PROFILE_RETURN = null;      // where to go after a profile Save (set when opened from the record)
  // Ten-Fifty5 (AI match analysis / technique) embed URL, injected server-side (web_app.py).
  // Empty -> the members-area entry + route stay hidden. The member is signed in inside the
  // iframe via the auth_client.js token relay (no second login).
  var TF5_URL = (window.__CF && window.__CF.__TF5_EMBED_URL) || "";
  // Optional email allowlist for a PRIVATE prod test before community launch. Injected from
  // env (TF5_EMBED_ALLOW_EMAILS). EMPTY => every member sees it (launch); set => only these
  // emails. Widening to everyone is a one-line env change, no code redeploy.
  var TF5_ALLOW = ((window.__CF && window.__CF.__TF5_EMBED_ALLOW) || "")
    .split(",").map(function (s) { return s.trim().toLowerCase(); }).filter(Boolean);
  function tf5Enabled() {
    if (!TF5_URL) return false;
    if (!TF5_ALLOW.length) return true;   // no allowlist => open to all members
    var e = ((principal && principal.email) || (DATA.profile && DATA.profile.email) || "").toLowerCase();
    return TF5_ALLOW.indexOf(e) >= 0;
  }

  function money(m, c) { return UI.money(m || 0, c || "ZAR"); }
  function go(hash) { location.hash = hash; }
  function esc(s) { return UI.esc(s); }
  var addToCalendar = window.UI.addToCalendar;   // shared (FRONTEND-STANDARDISATION Wave 1)

  // ---- boot ----------------------------------------------------------------
  async function start() {
    UI = window.UI; el = UI.el;
    await window.TFAuth.ready();
    if (!window.TFAuth.isAuthed()) { await window.TFAuth.requireAuth(); return; }
    // Kick the profile fetch off IN PARALLEL with whoami so first paint isn't gated on a
    // second South Africa→Frankfurt round trip (Starter killed cold starts; the round-trips remain).
    var pendingProfile = window.API.getProfile().catch(function () { return null; });
    try { principal = await window.API.whoami(); }
    catch (e) { if (e.status === 401) await window.TFAuth.requireAuth(); return; }
    if (!principal) return;
    // Staff live in their own consoles — the client app is for members/guests. Send a first-run
    // owner/coach to their onboarding first (the same gate portal.html used to run).
    //
    // …UNLESS they are deliberately VIEWING AS A MEMBER (/app.html?as=<user_id>). That feature exists
    // precisely so an owner can see a member's real screens, and this bounce fired first — so it sent
    // the owner straight back to /admin and the feature could never work for the only role that has
    // it. Reads are already confined to GET /api/me/* with as_user by auth_client.withViewAs, so
    // staying here is read-only by construction, not by convention.
    var VIEWING_AS = (function () {
      try { return !!new URLSearchParams(location.search).get("as"); } catch (e) { return false; }
    })();
    if (!VIEWING_AS && principal.role === "coach") {
      try { var cob = await window.TFAuth.apiJSON("/api/coach/onboarding"); if (cob && !cob.completed) { location.href = "/coach-onboarding.html"; return; } } catch (e) {}
      location.href = "/coach"; return;
    }
    if (!VIEWING_AS && (principal.role === "club_admin" || principal.role === "platform_admin")) {
      try { var aob = await window.TFAuth.apiJSON("/api/admin/onboarding"); if (aob && !aob.completed) { location.href = "/onboarding.html"; return; } } catch (e) {}
      location.href = "/admin"; return;
    }
    if (!principal.club_id) { document.body.innerHTML =
      '<div style="padding:40px;font-family:Inter,system-ui">No active club is resolved for your account. Contact the club to be added.</div>'; return; }
    renderShell();
    window.addEventListener("hashchange", route);
    // Warm the profile name for the greeting + avatar (already in flight — usually resolved by now).
    try { var pr = await pendingProfile; if (pr) { DATA.profile = pr; NAME = fullName(pr); paintAvatar(); } } catch (e) {}
    route();
  }

  function fullName(p) {
    if (!p) return "";
    return [p.first_name, p.surname].filter(Boolean).join(" ").trim() || (p.email || "").split("@")[0] || "there";
  }
  function initials() {
    var n = (NAME || (principal && principal.email) || "You").trim();
    var parts = n.split(/\s+/);
    return ((parts[0] || "Y")[0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
  }
  function paintAvatar() { var a = document.getElementById("cf-avatar"); if (a) a.textContent = initials(); }

  // The top-right avatar opens ONE account menu (shared UI.menu) — Edit profile / Switch profile /
  // Sign out. Switch profile returns to Clerk to sign in as a different user; Sign out logs out.
  function openAccountMenu(anchor) {
    UI.menu(anchor, [
      { label: "Edit profile", onClick: function () { go("#/profile"); } },
      { label: "Switch profile", onClick: switchProfile },
      "-",
      { label: "Sign out", tone: "danger", onClick: signOut },
    ]);
  }
  function switchProfile() { window.TFAuth.signOut().then(function () { location.href = "/login"; }); }
  function signOut() { window.TFAuth.signOut().then(function () { location.reload(); }); }

  // ---- shell (appbar + view + bottom nav) ----------------------------------
  var NAV = [
    { k: "home", ic: "⌂", label: "Home" },
    { k: "book", ic: "", label: "Book" },
  ];
  function renderShell() {
    // The client experience is ONE page — no bottom nav. (Book is reached from the Home tiles;
    // the coach & owner apps keep their bottom nav.) The TS avatar (top-right) opens the profile.
    document.body.style.paddingBottom = "20px";
    if (!document.getElementById("cf-appbar")) {
      var bar = el("div", { class: "cf-appbar", id: "cf-appbar" }, [
        el("div", { class: "cf-brand", style: "cursor:pointer", title: "Home", onclick: function () { go("#/"); } }, [el("span", { class: "cf-logo", text: "NP" }), el("span", { text: "NextPoint" })]),
        el("span", { class: "cf-spacer" }),
        el("div", { class: "cf-bell-host", id: "cf-bell" }),
        el("div", { class: "cf-avatar", id: "cf-avatar", text: initials(), title: "Account", onclick: function (ev) { openAccountMenu(ev.currentTarget); } }),
      ]);
      document.body.insertBefore(bar, document.body.firstChild);
      mountBell(document.getElementById("cf-bell"));
      // VIEWING AS A MEMBER — say so, loudly and permanently. An owner who forgets which account
      // they are looking at is how a support view turns into a mistake, and the banner is the only
      // thing standing between "reading their screen" and "believing it is yours".
      if (window.TFAuth.viewingAs && window.TFAuth.viewingAs()) {
        var b = el("div", { style: "background:var(--warn,#8a5a00);color:#fff;padding:6px 12px;"
                                 + "font-size:.82rem;font-weight:600;text-align:center" },
          [el("span", { text: "Viewing as a member — read only. Nothing you do here can book, pay or change anything." })]);
        document.body.insertBefore(b, document.body.firstChild);
      }
    }
    view = document.getElementById("cf-main");
    if (!view) { view = el("main", { class: "cf-main", id: "cf-main" }); document.body.appendChild(view); }
  }
  function setActive(k) { /* no bottom nav on the client */ }
  function mountBell(hostEl) {
    if (!hostEl) return;
    if (window.Notifications) { window.Notifications.mount(hostEl); return; }
    var s = document.createElement("script"); s.src = "/js/notifications.js";
    s.onload = function () { if (window.Notifications) window.Notifications.mount(hostEl); };
    document.head.appendChild(s);
  }

  // ---- router --------------------------------------------------------------
  function route() {
    var h = (location.hash || "").replace(/^#\/?/, "");
    var parts = h.split("/").filter(Boolean);
    var top = parts[0] || "home";
    setActive(top === "book" ? "book" : "home");   // everything else lives on Home now
    window.scrollTo(0, 0);
    if (top === "book") return renderBook(parts[1]);
    if (top === "booking") return renderBookingStory(parts[1]);
    if (top === "class") return renderClassStory(parts[1]);
    if (top === "billing") {                        // drill-through screens under Home's billing
      if (parts[1] === "order") return renderTxn(parts[2]);   // an order → its transaction record (not a bare receipt)
      if (parts[1] === "cat") return renderBillingCategory(parts[2], parts[3]);
      return renderHome();
    }
    if (top === "activity") return renderRecord();   // "Full activity ›" now opens the ONE Client-360 record
    if (top === "invoices") return renderInvoices();  // the member's issued invoices (view/download PDF)
    if (top === "txn") return renderTxn(parts[1]);    // a purchase (pack/membership/invoice) transaction record
    if (top === "play") {                             // Find a Game
      if (parts[1] === "profile") return renderPlayerProfile();   // level + preferences + opt-in
      if (parts[1] === "players") return renderFindAPlayer();     // suggested opponents
      return renderFindAGame();                                   // the open-games feed
    }
    if (top === "game") return renderGame(parts[1]);  // one game: seats, money, chat
    if (top === "analysis") return renderAnalysis();  // embedded Ten-Fifty5 match analysis / technique
    if (top === "plan") return renderPlan(parts[1]);
    if (top === "profile") return parts[1] === "child" ? renderChildEdit(parts[2]) : renderProfile();  // /profile/edit → the same one screen
    return renderHome();                            // home / bookings / anything else
  }

  // ---- small render helpers ------------------------------------------------
  function set(node) { view.style.opacity = 0; UI.clear(view); view.appendChild(node); requestAnimationFrame(function () { view.style.transition = "opacity .16s"; view.style.opacity = 1; }); }
  function loading() {
    var n = el("div", { class: "cf-loading", style: "min-height:200px", text: "Loading…" });
    set(n);
    // On a slow first load (Clerk init + cross-region round trips) reassure instead of a bare spinner.
    setTimeout(function () { if (n.isConnected && n.textContent === "Loading…") n.textContent = "Still loading — one moment…"; }, 7000);
  }
  var card = window.UI.card, kv = window.UI.kv, pageHeader = window.UI.pageHeader;   // shared (Wave 1)
  var TYPE_LABEL = { court: "Court", lesson: "Lesson", class: "Class" };
  function typeLabel(t) { return TYPE_LABEL[t] || "Booking"; }
  var DESC = { court: "Court booking", lesson: "Private lesson", class: "Class", membership: "Membership" };
  function pretty(s) { if (!s) return "Charge"; var k = String(s).toLowerCase(); return DESC[k] || (s.charAt(0).toUpperCase() + s.slice(1)); }
  function firstName() { var n = NAME || (principal && principal.email ? principal.email.split("@")[0] : ""); return (n || "there").split(" ")[0]; }
  function timeRange(b) {
    try { return UI.fmtTime(b.starts_at) + "–" + UI.fmtTime(b.ends_at); } catch (e) { return ""; }
  }
  var statusChip = window.UI.statusChip;   // shared status vocabulary (Wave 1/3 consolidation)

  // ---- HOME (the hub: everything on one page — no bottom nav for the client) --
  var HBMONTH = null;      // billing month
  function curMonth() { var d = new Date(); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0"); }
  function shiftM(ym, d) { var p = ym.split("-"); var dt = new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1 + d, 1); return dt.getFullYear() + "-" + String(dt.getMonth() + 1).padStart(2, "0"); }
  function mLabel(ym) { var p = ym.split("-"); try { return new Date(p[0], parseInt(p[1], 10) - 1, 1).toLocaleDateString(undefined, { month: "short", year: "numeric" }); } catch (e) { return ym; } }

  async function renderHome() {
    if (!HBMONTH) HBMONTH = curMonth();
    loading();
    var fin = {}, bookings = [];
    try { fin = await window.API.financials(); } catch (e) {}
    try { bookings = (await window.API.bookings({ date_from: UI.dateKey(UI.addDays(new Date(), -730)), date_to: UI.dateKey(UI.addDays(new Date(), 365)) })).bookings || []; } catch (e) {}
    try { DATA.enrolments = (await window.API.myEnrolments()).enrolments || []; } catch (e) { DATA.enrolments = []; }
    // Featured equipment (e.g. the ball machine) → a Home hero tile that starts a court booking with it added.
    try { DATA.equipment = ((await window.TFAuth.apiJSON("/api/diary/equipment")).equipment || []).filter(function (e) { return e.feature_on_home && e.active !== false; }); } catch (e) { DATA.equipment = []; }
    // Community config + the member's own player profile, loaded WITH everything else rather than
    // patched in afterwards. Find a match is a peer of Court/Lesson/Class, so the tile row cannot be
    // built until we know whether the club offers it and whether this member has a level yet.
    try { DATA.community = await window.TFAuth.apiJSON("/api/community/config"); } catch (e) { DATA.community = null; }
    DATA.player = null;
    if (DATA.community && DATA.community.community_enabled) {
      try { DATA.player = await window.API.playerProfile(); } catch (e) {}
    }
    DATA.fin = fin; DATA.bookings = bookings;
    var plan = fin.plan || {}, cur = fin.currency || "ZAR";
    var wrap = el("div", {});

    // Greeting — name, email, membership standing as a quiet chip (no emoji). Edit profile shortcut.
    var email = (DATA.profile && DATA.profile.email) || (principal && principal.email) || "";
    var mLine = plan.is_trial ? ("7-day trial — " + (plan.trial_days_left || 0) + " days left · courts free")
      : plan.active ? ((plan.name || "Member") + (plan.current_period_end ? " · renews " + plan.current_period_end : ""))
        : "Pay as you go";
    wrap.appendChild(el("div", { class: "cf-greet", style: "padding:22px 24px;align-items:flex-start" }, [
      el("div", { style: "flex:1" }, [
        el("h1", { text: greet() + ", " + firstName() }),
        email ? el("p", { style: "opacity:.92;margin-top:2px", text: email }) : null,
        el("div", { class: "cf-row", style: "gap:10px;margin-top:12px;flex-wrap:wrap;align-items:center" }, [
          el("span", { class: "cf-chip", style: "background:rgba(255,255,255,.18);color:#fff", text: mLine }),
          el("button", { class: "cf-btn cf-btn-sm", text: "Edit profile", onclick: function () { go("#/profile"); } }),
        ]),
      ].filter(Boolean)),
    ]));

    // First-login nudge: gently prompt to complete a sparse profile.
    if (DATA.profile && !DATA.profile.phone && !nudgeDismissed()) wrap.appendChild(profileNudge());

    // BOOK — services FIRST so a member can pick one straight away (Court / Lesson / Class,
    // drawn glyphs, no emoji).
    var qb = card([el("h2", { style: "margin:0 0 10px", text: "Book a session" })]);
    var tiles = el("div", { class: "cf-qb" });
    // ONE tile shape for every service: [icon] [name / grey sub-line], left-aligned. Court/Lesson/Class
    // and the featured equipment (ball machine) all read identically.
    function bookTile(glyphKind, name, sub, onClick) {
      return el("button", { class: "cf-qb-btn", onclick: onClick }, [
        svcGlyph(glyphKind),
        el("div", { class: "cf-qb-main" }, [
          el("div", { class: "cf-qb-t", text: name }),
          sub ? el("div", { class: "cf-qb-s", text: sub }) : null,
        ].filter(Boolean)),
      ]);
    }
    var TILE_SUB = { court: "Book a court", lesson: "With a coach", class: "Group session" };
    ["court", "lesson", "class"].forEach(function (k) {
      tiles.appendChild(bookTile(k, TYPE_LABEL[k], TILE_SUB[k], function () { go("#/book/" + k); }));
    });
    // Featured equipment (e.g. the ball machine) — same tile; tapping starts a court booking with it pre-added.
    (DATA.equipment || []).forEach(function (eq) {
      var sub = "On a court" + (eq.amount_minor != null ? " · from " + UI.money(eq.amount_minor, cur) : "");
      tiles.appendChild(bookTile("court", eq.name, sub, function () { PENDING_EQUIP = eq.id; go("#/book/court"); }));
    });
    qb.appendChild(tiles);

    // ---- TWO BLOCKS, and the order is ONE CONSTANT ----------------------------------------------
    //
    // They are not two menus. They are two MODES, which is why merging them muddled the page:
    //
    //   BOOK (this block) — SYNCHRONOUS. Pick a time, pay, done in ninety seconds. But every option in
    //                       it depends on somebody ELSE being available: a friend who is free, a coach
    //                       on shift, a class that is scheduled.
    //   FIND (home-play)  — ASYNCHRONOUS. Post and wait, or browse and join; it may resolve tomorrow.
    //                       It depends on nothing but the members who want to play — which is exactly
    //                       why it is the only one of the two that can fill a Sunday, when there is no
    //                       coach, no class, and the courts sit empty anyway.
    //
    // BOOK leads while the feature is being tested, so the members who already know what they want are
    // undisturbed. Flip this one constant to lead with FIND once it has earned the position — the
    // owner's call, and deliberately a single line because it WILL be revisited.
    var PLAY_BLOCK_FIRST = false;
    var playHost = el("div", { id: "home-play" });   // filled by loadPlayCard() AFTER set(wrap)
    if (PLAY_BLOCK_FIRST) { wrap.appendChild(playHost); wrap.appendChild(qb); }
    else { wrap.appendChild(qb); wrap.appendChild(playHost); }

    // Your sessions (Upcoming / Past) — what's next, right after choosing a service.
    wrap.appendChild(card([el("h2", { style: "margin:0 0 8px", text: "Your sessions" }), el("div", { id: "home-sessions" })]));

    // Match analysis & technique — directly under bookings (the embedded Ten-Fifty5 product).
    // Allowlisted (private test) → the working embed; everyone else → a "Coming soon" teaser.
    if (TF5_URL) wrap.appendChild(tf5Enabled() ? analysisPromo() : analysisSoon());

    // BILLING + ACTIVITY — a month-navigable summary section (‹ month › re-fetches). Tap a block → 360.
    wrap.appendChild(el("div", { id: "home-summary" }));

    // Plan & credits — the member's standing + manage/cancel.
    wrap.appendChild(planCard(plan, cur));

    set(wrap);
    paintSessions();
    loadHomeSummary(HBMONTH);
    loadWallets(cur);
    loadPlayCard();
  }

  // FIND A MATCH on Home — a two-state block, because a member without a level and a member with one
  // need completely different things from this screen.
  //
  // NO LEVEL YET  -> the only thing worth asking for is the assessment. Showing them a feed first is
  //                 the wrong order: matching by level is the whole promise, and an unlevelled member
  //                 browsing an unfiltered list is exactly the experience that makes people conclude
  //                 the feature doesn't work.
  // LEVEL SET     -> lead with the level (it is theirs, and it is why the matches are relevant), then
  //                 the marketplace: take a game, post one, or see who suits you.
  //
  // Called AFTER set(wrap), like every other async section — renderHome builds `wrap` detached and
  // only attaches it at set(wrap), so a getElementById before that finds nothing.
  var LEVEL_WORD_HOME = ["", "Beginner", "Beginner+", "Recreational", "Intermediate", "Intermediate+",
                         "Strong club", "Advanced", "Tournament", "High-performance", "Elite"];
  function levelLabel(n) {
    return n == null ? "Not set"
      : (LEVEL_WORD_HOME[Math.round(n)] || "Level") + " · " + Number(n).toFixed(1);
  }

  async function loadPlayCard() {
    var host = document.getElementById("home-play"); if (!host) return;
    var cfg = DATA.community;
    if (!cfg || !cfg.community_enabled || !host.isConnected) return;
    var prof = DATA.player || {};
    UI.clear(host);

    if (prof.level_num == null) {
      // THE ASSESSMENT, framed as the thing it buys them — not as a form.
      host.appendChild(card([
        el("h2", { style: "margin:0 0 6px", text: "Need someone to play with?" }),
        el("p", { class: "cf-muted", style: "margin:0 0 12px",
          text: "The hard part of tennis isn't the court — it's finding someone at your level who's "
              + "free when you are. Five quick questions and we'll match you. Takes a minute, and you "
              + "can change it any time as you improve." }),
        el("div", { class: "cf-row", style: "gap:8px;flex-wrap:wrap" }, [
          el("button", { class: "cf-btn cf-btn-primary", text: "Work out my level",
            onclick: function () { go("#/play/profile"); } }),
          el("button", { class: "cf-btn cf-btn-ghost", text: "Just show me the games",
            onclick: function () { go("#/play"); } }),
        ]),
      ]));
      return;
    }

    // LEVEL SET — lead with it, then the marketplace.
    var games = [], gamesFailed = false;
    try { games = (await window.API.openGames({ days: 14, near: 1.5 })).games || []; }
    catch (e) { gamesFailed = true; }
    var joinable = games.filter(function (g) { return !g.im_in; });
    var body = el("div", {});
    body.appendChild(el("div", { class: "cf-row", style: "gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px" }, [
      el("span", { class: "cf-chip ok", text: levelLabel(prof.level_num) }),
      prof.level_source === "coach" ? el("span", { class: "cf-muted cf-tiny", text: "set by your coach" })
        : el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Update",
            onclick: function () { go("#/play/profile"); } }),
      // "Not findable yet" was a dead end: the state that stops the whole feature working for this
      // member, shown as a grey chip with nothing to tap. It is the single most valuable action on
      // the screen, so it is now the way IN to the setting rather than a label about it.
      !prof.visible_in_community
        ? el("button", { class: "cf-btn cf-btn-sm", text: "Not findable yet — turn on",
            onclick: function () { go("#/play/profile"); } }) : null,
    ].filter(Boolean)));
    body.appendChild(el("p", { class: "cf-muted", style: "margin:0 0 12px",
      // A failed read must never render as "no games" — that is a confident lie, and the member
      // acts on it by not looking again (GOTCHAS § Reads that lie).
      text: gamesFailed
        ? "We couldn't check for games just now — pull down to refresh, or try Find a match."
        : (joinable.length
            ? (joinable.length === 1 ? "There's 1 game near your level looking for a player."
                                     : "There are " + joinable.length + " games near your level looking for players.")
            : "No games near your level right now — post one and let someone come to you.") }));
    body.appendChild(el("div", { class: "cf-row", style: "gap:8px;flex-wrap:wrap" }, [
      el("button", { class: joinable.length ? "cf-btn cf-btn-primary" : "cf-btn",
        text: "Find a match", onclick: function () { go("#/play"); } }),
      el("button", { class: joinable.length ? "cf-btn" : "cf-btn cf-btn-primary",
        text: "Post a match", onclick: function () { go("#/book/court"); } }),
      el("button", { class: "cf-btn cf-btn-ghost", text: "Players for you",
        onclick: function () { go("#/play/players"); } }),
    ]));
    host.appendChild(card([el("h2", { style: "margin:0 0 8px", text: "Need someone to play with?" }), body]));
  }

  // The month-navigable Billing + Activity summary (re-fetches on ‹ ›). Reuses HBMONTH.
  async function loadHomeSummary(month) {
    var box = document.getElementById("home-summary"); if (!box) return;
    UI.clear(box); box.appendChild(el("div", { class: "cf-loading", style: "min-height:90px", text: "…" }));
    var a = null;
    try { a = await window.API.activitySummary(month); } catch (e) {}
    UI.clear(box);
    // Month switcher — outside the tappable blocks so the arrows don't trigger a drill-through.
    box.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin:2px 2px 12px" }, [
      el("h2", { style: "margin:0;font-size:1.05rem", text: "Your month" }),
      el("div", { class: "cf-row", style: "gap:6px;align-items:center" }, [
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "‹", onclick: function () { HBMONTH = shiftM(month, -1); loadHomeSummary(HBMONTH); } }),
        el("span", { class: "cf-chip", style: "min-width:96px;text-align:center", text: mLabel(month) }),
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "›", onclick: function () { HBMONTH = shiftM(month, 1); loadHomeSummary(HBMONTH); } }),
      ]),
    ]));
    if (!a) { box.appendChild(el("div", { class: "cf-empty", text: "Couldn't load this month." })); return; }
    box.appendChild(window.CRMUI.spendBlock(a, { onSettle: function () { payOrders(null); }, onOpen: function () { go("#/activity"); } }));
    box.appendChild(window.CRMUI.activityBlock(a, { onOpen: function () { go("#/activity"); } }));
  }

  // A drawn line-glyph per service type (no emoji) — court net, lesson mortarboard, class group.
  function svcGlyph(kind) {
    var svg = {
      court: "<rect x='3' y='4' width='18' height='16' rx='2' stroke='var(--green)' stroke-width='2'/><path d='M12 4v16M3 12h18' stroke='var(--green)' stroke-width='2'/>",
      lesson: "<path d='M3 9l9-5 9 5-9 5-9-5z' stroke='var(--info)' stroke-width='2' stroke-linejoin='round'/><path d='M7 11v4c0 1.1 2.2 2 5 2s5-.9 5-2v-4' stroke='var(--info)' stroke-width='2'/>",
      class: "<circle cx='9' cy='8' r='3' stroke='var(--lime-700)' stroke-width='2'/><circle cx='17' cy='9' r='2.4' stroke='var(--lime-700)' stroke-width='2'/><path d='M3.5 19c.4-3 2.8-4.5 5.5-4.5S14.1 16 14.5 19M15 15.5c2.2.2 4 1.4 4.4 3.5' stroke='var(--lime-700)' stroke-width='2' stroke-linecap='round'/>",
      // Two players either side of a net — a MATCH, deliberately not another court icon.
      match: "<circle cx='6.5' cy='7' r='2.4' stroke='var(--green)' stroke-width='2'/><circle cx='17.5' cy='7' r='2.4' stroke='var(--green)' stroke-width='2'/><path d='M12 3v18' stroke='var(--green)' stroke-width='2' stroke-dasharray='2 2'/><path d='M2.5 20c.3-2.4 2-3.6 4-3.6s3.7 1.2 4 3.6M13.5 20c.3-2.4 2-3.6 4-3.6s3.7 1.2 4 3.6' stroke='var(--green)' stroke-width='2' stroke-linecap='round'/>",
    }[kind] || "";
    var box = el("span", { class: "cf-gtile " + kind });
    box.innerHTML = "<svg width='22' height='22' viewBox='0 0 24 24' fill='none'>" + svg + "</svg>";
    return box;
  }
  function greet() { var h = new Date().getHours(); return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"; }

  // "Complete your profile" nudge — dismissible, remembered per user in localStorage.
  function nudgeKey() { return "cf_profile_nudge:" + ((principal && (principal.user_id || principal.email)) || ""); }
  function nudgeDismissed() { try { return localStorage.getItem(nudgeKey()) === "1"; } catch (e) { return false; } }
  function dismissNudge() { try { localStorage.setItem(nudgeKey(), "1"); } catch (e) {} }
  function profileNudge() {
    var c = card([
      el("h2", { style: "margin:0 0 4px", text: "Complete your profile" }),
      el("p", { class: "cf-muted", style: "margin:0", text: "Add your phone and details so the club can reach you about your bookings — it only takes a minute." }),
      el("div", { class: "cf-row", style: "gap:8px;margin-top:12px" }, [
        el("button", { class: "cf-btn cf-btn-primary cf-btn-sm", text: "Fill in", onclick: function () { go("#/profile"); } }),
        el("button", { class: "cf-btn cf-btn-ghost cf-btn-sm", text: "Skip", onclick: function () { dismissNudge(); route(); } }),
      ]),
    ]);
    return c;
  }

  // ---- Match analysis & technique (embedded Ten-Fifty5) ---------------------
  // A Home card that drills into the embedded product. Inside the iframe the member is
  // signed in with their own Clerk token (relayed by auth_client.js) — no second login.
  // A drawn "analysis" glyph (a signal waveform + a spark) on the AI panel's glass tile — no emoji.
  function aiGlyph() {
    var g = el("span", { class: "cf-ai-ic" });
    g.innerHTML = "<svg width='22' height='22' viewBox='0 0 24 24' fill='none'>" +
      "<path d='M2.5 13c3-6.5 5-6.5 6.5 0S12.5 19.5 14 13s3-4.2 6.5-1.2' stroke='#fff' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/>" +
      "<circle cx='20' cy='5' r='1.7' fill='var(--lime)'/></svg>";
    return g;
  }
  function aiPanel(bodyText, tail) {
    var box = el("div", { class: "cf-ai" });
    box.appendChild(el("div", { class: "cf-ai-top" }, [aiGlyph(), el("span", { class: "cf-ai-badge", text: "AI · Ten-Fifty5" })]));
    box.appendChild(el("h2", { text: "Match analysis & technique" }));
    box.appendChild(el("p", { text: bodyText }));
    box.appendChild(tail);
    return box;
  }
  function analysisPromo() {
    return aiPanel(
      "AI match stats and stroke-by-stroke technique breakdowns from your video — spot patterns, track progress, and sharpen your game. Opens right here, already signed in.",
      el("button", { class: "cf-ai-cta", text: "Open analysis ›", onclick: function () { go("#/analysis"); } }));
  }

  // Non-allowlisted members see this teaser on Home (private test in progress).
  function analysisSoon() {
    return aiPanel(
      "AI match stats and stroke-by-stroke technique breakdowns from your video. Landing in your account soon.",
      el("span", { class: "cf-ai-soon", text: "Coming soon" }));
  }

  // ---- PLAYER PROFILE (the gate on everything else in Find a Game) ---------
  // Nothing in the discovery half is reachable without this screen: a member cannot set a level, say
  // what kind of tennis they want, or become findable at all. The engine existed for a day before
  // this did, which is exactly the "built but not wired" trap FEATURE-FLAGS.md §B is a list of.
  //
  // DISCOVERY IS OPT-IN and the toggle is the LAST thing on the page, after they can see what they'd
  // be sharing. Being findable by a thousand strangers is a choice, not a side effect of filling in a
  // form.
  var LEVEL_WORDS = ["", "Beginner", "Beginner+", "Recreational", "Intermediate", "Intermediate+",
                     "Strong club", "Advanced", "Tournament", "High-performance", "Elite"];
  function levelWord(n) {
    if (n == null) return "Not set";
    return (LEVEL_WORDS[Math.round(n)] || "") + " (" + Number(n).toFixed(1) + ")";
  }

  async function renderPlayerProfile() {
    loading();
    var prof = {};
    // A failed load must NOT fall through to an empty profile. This screen carries the discovery
    // opt-in, and `{}` renders that checkbox UNCHECKED — so a member who IS findable would be shown
    // "off", which is a lie about their own privacy setting and one they might then act on.
    try { prof = await window.API.playerProfile(); }
    catch (e) {
      var errWrap = el("div", {});
      errWrap.appendChild(pageHeader("Your tennis profile", "Find a game", "#/play"));
      errWrap.appendChild(card([
        el("div", { class: "cf-empty" }, [
          el("div", { text: "We couldn't load your profile." }),
          el("div", { class: "cf-muted cf-tiny", style: "margin-top:6px", text: UI.errMsg(e) }),
          el("button", { class: "cf-btn cf-btn-sm", style: "margin-top:10px", text: "Try again",
            onclick: function () { renderPlayerProfile(); } }),
        ]),
      ]));
      set(errWrap);
      return;
    }
    var wrap = el("div", {});
    wrap.appendChild(pageHeader("Your tennis profile", "Find a game", "#/play"));

    // 1) LEVEL — the single biggest determinant of whether this works for anyone.
    var lvlBox = el("div", {});
    function paintLevel() {
      UI.clear(lvlBox);
      lvlBox.appendChild(el("div", { class: "cf-row", style: "align-items:center;gap:10px" }, [
        el("div", { class: "cf-stat-v", text: levelWord(prof.level_num) }),
        prof.level_source === "coach"
          ? el("span", { class: "cf-chip ok", text: "set by your coach" }) : null,
      ].filter(Boolean)));
      lvlBox.appendChild(el("p", { class: "cf-muted cf-tiny",
        text: prof.level_source === "coach"
          ? "A coach has assessed your level. Ask them if you think it needs changing."
          : "Five quick questions. It only decides who you get matched with — nobody else sees how you answered." }));
      if (prof.level_source !== "coach") {
        lvlBox.appendChild(el("button", {
          class: "cf-btn cf-btn-primary cf-btn-sm", style: "margin-top:8px",
          text: prof.level_num == null ? "Work out my level" : "Redo the questions",
          onclick: openQuiz,
        }));
      }
    }
    paintLevel();
    wrap.appendChild(card([el("h2", { style: "margin:0 0 8px", text: "Your level" }), lvlBox]));

    // 2) WHAT KIND OF TENNIS + WHEN. Both feed the matching score.
    // Saving must NOT rebuild the page. renderPlayerProfile() opens with loading(), so re-entering it
    // on every tap flashed the whole screen to "Loading…" and threw the member back to the top — with
    // 14 day-part buttons below, choosing four times meant four full-page flashes and four scrolls
    // back down. The opt-in checkbox further down already had this right: repaint locally, revert on
    // failure. These controls now do the same.
    function chips(title, hint, keyName, opts) {
      var box = el("div", {});
      box.appendChild(el("p", { class: "cf-muted cf-tiny", text: hint }));
      var row = el("div", { class: "cf-row", style: "gap:8px;flex-wrap:wrap;margin-top:6px" });
      var btns = [];
      function paint() {
        btns.forEach(function (x) {
          x.b.className = "cf-btn cf-btn-sm" + (prof[keyName] === x.key ? " cf-btn-primary" : "");
        });
      }
      opts.forEach(function (o) {
        var b = el("button", { type: "button", text: o.label });
        b.addEventListener("click", function () {
          var prev = prof[keyName];
          prof[keyName] = o.key;
          paint();                                   // optimistic — the tap feels instant
          var patch = {}; patch[keyName] = o.key;
          window.API.savePlayerProfile(patch).then(
            function (r) { prof = r; paint(); },
            function (e) { prof[keyName] = prev; paint(); UI.toast(UI.errMsg(e), "error"); });
        });
        btns.push({ b: b, key: o.key });
        row.appendChild(b);
      });
      paint();
      box.appendChild(row);
      return card([el("h2", { style: "margin:0 0 8px", text: title }), box]);
    }
    // ONE vocabulary — window.CFIntent (crm_ui.js). These chips used to hard-code their own labels
    // ("A social hit" / "Practice"), which is the precise drift CFIntent was created to kill: both
    // read as hitting, so the only option that read as a MATCH was "Competitive". A member set
    // "Practice" here and then saw "Just a hit" on the game card for the same stored value.
    wrap.appendChild(chips("What are you looking for?",
      "This is what most people get wrong for each other — turning up for a relaxed hit against someone playing a practice match spoils it for both of you.",
      "prefers_play", window.CFIntent.OPTIONS.map(function (o) {
        return { key: o.key, label: o.label };
      })));
    wrap.appendChild(chips("Singles or doubles?", "We'll show you more of what you pick.",
      "prefers_format", [{ key: "singles", label: "Singles" }, { key: "doubles", label: "Doubles" },
                         { key: "both", label: "Either" }]));

    // 3) WHEN — a simple day-part grid; it is the second-heaviest term in the match score.
    var times = (prof.prefers_times || []).slice();
    var tBox = el("div", {});
    tBox.appendChild(el("p", { class: "cf-muted cf-tiny", text: "Tap the times you usually play." }));
    var grid = el("div", { class: "cf-row", style: "gap:6px;flex-wrap:wrap;margin-top:6px" });
    [["mon", "Mon"], ["tue", "Tue"], ["wed", "Wed"], ["thu", "Thu"], ["fri", "Fri"],
     ["sat", "Sat"], ["sun", "Sun"]].forEach(function (d) {
      [["am", "am"], ["pm", "pm"]].forEach(function (part) {
        var keyName = d[0] + "_" + part[0];
        var b = el("button", {
          type: "button", text: d[1] + " " + part[1],
          class: "cf-btn cf-btn-sm" + (times.indexOf(keyName) >= 0 ? " cf-btn-primary" : ""),
        });
        b.addEventListener("click", function () {
          // Local toggle first — this grid has 14 buttons and a member picking four of them was
          // getting four full-page reloads. Revert the button if the save is refused.
          var i = times.indexOf(keyName);
          var added = i < 0;
          if (added) times.push(keyName); else times.splice(i, 1);
          b.className = "cf-btn cf-btn-sm" + (added ? " cf-btn-primary" : "");
          window.API.savePlayerProfile({ prefers_times: times }).then(function (r) { prof = r; },
            function (e) {
              var j = times.indexOf(keyName);
              if (added && j >= 0) times.splice(j, 1); else if (!added) times.push(keyName);
              b.className = "cf-btn cf-btn-sm" + (added ? "" : " cf-btn-primary");
              UI.toast(UI.errMsg(e), "error");
            });
        });
        grid.appendChild(b);
      });
    });
    tBox.appendChild(grid);
    wrap.appendChild(card([el("h2", { style: "margin:0 0 8px", text: "When do you play?" }), tBox]));

    // 4) THE OPT-IN — last, once they can see what it means.
    var vBox = el("div", {});
    var vLbl = el("label", { class: "cf-row", style: "cursor:pointer;gap:10px;align-items:flex-start" });
    var vCb = el("input", { type: "checkbox" });
    vCb.checked = !!prof.visible_in_community;
    vCb.addEventListener("change", function () {
      window.API.savePlayerProfile({ visible_in_community: vCb.checked }).then(function (r) {
        prof = r;
        UI.toast(vCb.checked ? "Other members can find you now." : "You're hidden again.", "info");
      }, function (e) { vCb.checked = !vCb.checked; UI.toast(UI.errMsg(e), "error"); });
    });
    vLbl.appendChild(vCb);
    vLbl.appendChild(el("div", {}, [
      el("div", { text: "Let other members find me" }),
      el("div", { class: "cf-muted cf-tiny",
        text: "They'll see your first name and your level — never your email or phone. You can turn this off whenever you like." }),
    ]));
    vBox.appendChild(vLbl);
    wrap.appendChild(card([el("h2", { style: "margin:0 0 8px", text: "Being findable" }), vBox]));

    set(wrap);

    async function openQuiz() {
      var qs = [];
      try { qs = (await window.API.levelQuestions()).questions || []; } catch (e) {
        UI.toast(UI.errMsg(e), "error"); return;
      }
      var m = UI.modal("Work out your level");
      var answers = {};
      m.body.appendChild(el("p", { class: "cf-muted", style: "margin:0 0 12px;font-size:.85rem",
        text: "Answer honestly — it decides who you're matched with, and being put against the wrong standard is the fastest way to stop enjoying this." }));
      qs.forEach(function (q) {
        var f = el("div", { class: "cf-field" }, [el("label", { text: q.q })]);
        var row = el("div", { class: "cf-row", style: "gap:6px;flex-wrap:wrap" });
        (q.options || []).forEach(function (o) {
          var b = el("button", { type: "button", class: "cf-btn cf-btn-sm", text: o[0] });
          b.addEventListener("click", function () {
            answers[q.key] = o[1];
            Array.prototype.forEach.call(row.children, function (x) { x.className = "cf-btn cf-btn-sm"; });
            b.className = "cf-btn cf-btn-sm cf-btn-primary";
          });
          row.appendChild(b);
        });
        f.appendChild(row);
        m.body.appendChild(f);
      });
      var go2 = el("button", { class: "cf-btn cf-btn-primary", text: "Set my level" });
      go2.addEventListener("click", function () {
        if (Object.keys(answers).length < qs.length) { UI.toast("Answer all five first.", "warn"); return; }
        window.API.submitLevelAnswers(answers).then(function (r) {
          m.close();
          UI.toast("You're a " + levelWord(r.level_num) + ".", "info");
          renderPlayerProfile();
        }, function (e) { UI.toast(UI.errMsg(e), "error"); });
      });
      m.body.appendChild(el("div", { class: "cf-row", style: "justify-content:flex-end;gap:8px;margin-top:12px" },
        [el("button", { class: "cf-btn", text: "Cancel", onclick: m.close }), go2]));
    }
  }

  // ---- FIND A PLAYER -------------------------------------------------------
  async function renderFindAPlayer() {
    loading();
    var wrap = el("div", {});
    wrap.appendChild(pageHeader("Players for you", "Find a game", "#/play"));
    var list = el("div", { class: "cf-list" });
    wrap.appendChild(list);
    set(wrap);
    // A failed read must not render as "nobody is here". Swallowing it told the member the club was
    // empty when the request had actually failed — GOTCHAS § Reads that lie.
    var players = [], loadErr = null;
    try { players = (await window.API.suggestedPlayers({ limit: 12 })).players || []; }
    catch (e) { loadErr = e; }
    UI.clear(list);
    if (loadErr) {
      list.appendChild(el("div", { class: "cf-empty" }, [
        el("div", { text: "We couldn't load suggestions just now." }),
        el("div", { class: "cf-muted cf-tiny", style: "margin-top:6px", text: UI.errMsg(loadErr) }),
        el("button", { class: "cf-btn cf-btn-sm", style: "margin-top:10px", text: "Try again",
          onclick: function () { renderFindAPlayer(); } }),
      ]));
      return;
    }
    if (!players.length) {
      list.appendChild(el("div", { class: "cf-empty" }, [
        el("div", { text: "Nobody to suggest yet." }),
        // Accurate: the discovery query filters on visible_in_community ONLY (matching.py). A level
        // is not required to appear — this used to say it was, which sent members off to do the quiz
        // to fix a problem the quiz does not fix.
        el("div", { class: "cf-muted cf-tiny", style: "margin-top:6px",
          text: "Members appear here once they've chosen to be findable." }),
        el("button", { class: "cf-btn cf-btn-primary", style: "margin-top:10px",
          text: "Set up your own profile", onclick: function () { go("#/play/profile"); } }),
      ]));
      return;
    }
    // Level is HALF the match score, so with no level of your own every candidate scores the same
    // neutral 0.5 on it (matching.py::_level_score) and the percentages below are mostly noise —
    // which is how a member with no level gets shown a High-performance player as their best match.
    // Say so rather than presenting a confident number built on a blank.
    if (!(DATA.player && DATA.player.level_num != null)) {
      wrap.insertBefore(el("div", { class: "cf-banner cf-banner-warn" }, [
        el("span", { text: "We don't know your level yet, so these are rough. " }),
        el("button", { class: "cf-btn cf-btn-sm", text: "Work out my level",
          onclick: function () { go("#/play/profile"); } }),
      ]), list);
    }
    players.forEach(function (p2) {
      list.appendChild(el("div", { class: "cf-item" }, [
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: p2.name }),
          // Through CFIntent, not raw. These printed the stored values straight out of the DB, so a
          // member read "social · both" — and worse, "practice" on the FORMAT axis means on their
          // own, a seat count of one, not a hit with somebody (crm_ui.js FORMAT_WORD).
          el("div", { class: "cf-item-s", text: [levelWord(p2.level),
            window.CFIntent.word(p2.prefers_play),
            window.CFIntent.format(p2.prefers_format)].filter(Boolean).join(" · ") }),
        ]),
        el("span", { class: "cf-chip", text: p2.match_pct + "% match" }),
      ]));
    });
    wrap.appendChild(el("p", { class: "cf-muted cf-tiny", style: "margin-top:10px",
      text: "Post a game and they'll see it — that's how you invite someone without swapping numbers." }));
  }

  // ---- FIND A GAME ---------------------------------------------------------
  // Both screens are the SHARED widgets (Widgets.GameList / Widgets.Game) — the client app supplies
  // only the data adapter, the actions map and the routing. A second render of a game anywhere else
  // in the three apps is a bug (the GOLDEN RULE).
  // The feed defaults to games AROUND MY LEVEL (near=1.5) rather than everything. Being repeatedly
  // shown games far above or below your standard is the single most reliable way to make someone stop
  // opening this screen — but "show me everything" stays one tap away, because a small club can be
  // thin at any given level and an empty feed is worse than a mismatched one.
  var PLAY_FILTER = { near: 1.5, intent: null };

  function renderFindAGame() {
    var wrap = el("div", {});
    wrap.appendChild(pageHeader("Find a game", "Home", "#/"));

    var bar = el("div", { class: "cf-row", style: "gap:8px;flex-wrap:wrap;margin-bottom:12px" });
    function chip(label, isOn, onTap) {
      var b = el("button", { type: "button", text: label,
        class: "cf-btn cf-btn-sm" + (isOn ? " cf-btn-primary" : "") });
      b.addEventListener("click", onTap);
      return b;
    }
    // "Around my level" only means something once the member HAS a level: the server ignores the
    // band otherwise (list_open_games: `if mine is not None`). It used to render ON by default
    // regardless, so a member with no level was shown a lit-up filter that did nothing — the feed
    // was unfiltered while the screen insisted it was narrowed to them. Offer the fix instead.
    var haveLevel = !!(DATA.player && DATA.player.level_num != null);
    if (haveLevel) {
      bar.appendChild(chip("Around my level", !!PLAY_FILTER.near, function () {
        PLAY_FILTER.near = PLAY_FILTER.near ? null : 1.5; renderFindAGame();
      }));
    }
    window.CFIntent.OPTIONS.forEach(function (o) {
      bar.appendChild(chip(o.label, PLAY_FILTER.intent === o.key, function () {
        PLAY_FILTER.intent = PLAY_FILTER.intent === o.key ? null : o.key; renderFindAGame();
      }));
    });
    wrap.appendChild(bar);
    if (!haveLevel) {
      wrap.appendChild(el("div", { class: "cf-muted cf-tiny", style: "margin:-6px 0 12px" }, [
        el("span", { text: "Showing every game. " }),
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Set your level",
          onclick: function () { go("#/play/profile"); } }),
        el("span", { text: " and we'll show the ones near it." }),
      ]));
    }

    var host = el("div", {});
    wrap.appendChild(host);
    wrap.appendChild(el("div", { class: "cf-row", style: "gap:8px;margin-top:14px;flex-wrap:wrap" }, [
      el("button", { class: "cf-btn cf-btn-sm", text: "Players for you",
        onclick: function () { go("#/play/players"); } }),
      el("button", { class: "cf-btn cf-btn-sm", text: "Your tennis profile",
        onclick: function () { go("#/play/profile"); } }),
    ]));
    set(wrap);
    window.Widgets.GameList.mount(host, {
      data: {
        list: function () {
          var q = { days: 14 };
          if (PLAY_FILTER.near) q.near = PLAY_FILTER.near;
          if (PLAY_FILTER.intent) q.intent = PLAY_FILTER.intent;
          return window.API.openGames(q);
        },
      },
      actions: { create: { run: function () { go("#/book/court"); } } },
      onNavigate: function (n) { if (n.kind === "game") go("#/game/" + n.id); },
    });
  }

  function renderGame(id) {
    if (!id) { go("#/play"); return; }
    var wrap = el("div", {});
    wrap.appendChild(pageHeader("Game", "Find a game", "#/play"));
    var host = el("div", {});
    wrap.appendChild(host);
    set(wrap);
    var w = window.Widgets.Game.mount(host, {
      id: id,
      me: principal && principal.user_id,
      data: {
        get: function (gid) { return window.API.game(gid); },
        chat: function (gid) { return window.API.gameChat(gid); },
      },
      actions: {
        join: { run: function (g) { act(function () { return window.API.joinGame(g.booking_id); }, "You're in."); } },
        leave: { run: function (g) { act(function () { return window.API.leaveGame(g.booking_id); }, "Seat given back."); } },
        post: { run: function (g, body) { return window.API.postGameChat(g.booking_id, body); } },
        invite: {
          run: function (g) {
            // The ONE add-a-player modal, reused: it already does name search, a parent's children
            // and an email fallback. Here it invites by email, which is what brings a NON-member in.
            window.CRMUI.addLessonPlayerModal({
              searchFn: null,
              toast: "Invitation sent — they get a free week to try the club.",
              onSubmit: function (payload) { return window.API.inviteToGame(g.booking_id, payload.email); },
              onDone: function () { w.refresh(); },
            });
          }
        },
        // A seat's own share is paid through the SAME Yoco seam every other online order uses —
        // there is no second payment path for community money.
        pay: { run: function (seat) { window.Pay.startYocoCheckout(seat.order_id); } },
        // The widget owns the result form and hands back the finished body; the app only posts it.
        result: { run: function (g, body) { return window.API.recordGameResult(g.booking_id, body); } },
        confirmResult: {
          run: function (g) {
            act(function () { return window.API.confirmGameResult(g.booking_id); }, "Result confirmed.");
          }
        },
        // PRIVATE — no toast, no confirmation. Announcing it would tell the room something only the
        // rater is meant to know, and the button state is feedback enough.
        playAgain: {
          run: function (g, userId, again) {
            return window.API.ratePlayAgain(g.booking_id, userId, again);
          }
        },
      },
      onNavigate: function (n) { if (n.kind === "booking") go("#/booking/" + n.id); },
    });
  }

  function renderAnalysis() {
    if (!tf5Enabled()) { go("#/"); return; }   // non-allowlisted see the "Coming soon" card on Home
    var wrap = el("div", {});
    wrap.appendChild(pageHeader("Match analysis & technique", "Home", "#/"));
    var frameWrap = el("div", { style: "position:relative;border-radius:12px;overflow:hidden;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.08)" });
    var frame = el("iframe", {
      src: TF5_URL,
      title: "Ten-Fifty5 match analysis",
      // height is set by fit() below; allow="fullscreen" (no legacy allowfullscreen attr → no console warning).
      style: "display:block;width:100%;height:70vh;border:0",
      allow: "fullscreen; encrypted-media; clipboard-write",
    });
    var fsBtn = el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", style: "position:absolute;top:10px;right:10px;z-index:2",
      text: "⤢ Fullscreen", onclick: function () {
        var fn = frameWrap.requestFullscreen || frameWrap.webkitRequestFullscreen;
        if (fn) { try { fn.call(frameWrap); } catch (e) {} }
      } });
    frameWrap.appendChild(frame);
    frameWrap.appendChild(fsBtn);
    wrap.appendChild(frameWrap);
    set(wrap);
    // Fill from the iframe's top to the viewport bottom (accounts for the appbar, page header and
    // cf-main's bottom padding) so the OUTER page never scrolls — only Ten-Fifty5's own content does.
    // Uses live window.innerHeight so it's correct on mobile too; re-fits on resize/orientation change.
    function fit() {
      if (!frameWrap.isConnected) { window.removeEventListener("resize", fit); return; }
      var main = document.getElementById("cf-main");
      var padB = main ? (parseFloat(getComputedStyle(main).paddingBottom) || 0) : 0;
      var top = frameWrap.getBoundingClientRect().top;
      frame.style.height = Math.max(320, window.innerHeight - top - padB - 24) + "px";
    }
    // Fire the initial fit through several settle points — a single rAF can land before layout
    // is stable (leaving the 70vh fallback), so also fit synchronously, after the iframe loads,
    // and after a short delay. Then keep it fitted on resize / orientation change.
    fit();
    requestAnimationFrame(fit);
    frame.addEventListener("load", fit);
    setTimeout(fit, 300);
    window.addEventListener("resize", fit);
  }

  // sessions: a Current / Past toggle (default Current) on the shared cf-segment filter look — Home
  // leads with what's coming up, past is one tap away. Court/lesson BOOKINGS and class ENROLMENTS are
  // merged into one chronological list (a class enrolment isn't a diary.booking). Each drills to detail.
  var SESSVIEW = "current";
  function paintSessions() {
    var box = document.getElementById("home-sessions"); if (!box) return;
    UI.clear(box);
    var today0 = new Date(); today0.setHours(0, 0, 0, 0);        // "Current" = today onward (incl. earlier today), not just future
    var up = [], past = [];
    function add(when, status, node) {
      if (status === "cancelled") return;                          // cancelled sessions drop off
      var it = { t: new Date(when), node: node };
      (it.t >= today0 && status !== "no_show") ? up.push(it) : past.push(it);
    }
    (DATA.bookings || []).forEach(function (b) { add(b.starts_at, b.status, bookingRow(b)); });
    (DATA.enrolments || []).forEach(function (e) { add(e.starts_at, e.status, classRow(e)); });
    if (!up.length && !past.length) { box.appendChild(el("div", { class: "cf-empty", text: "No sessions yet — book one above." })); return; }
    up.sort(function (a, b) { return a.t - b.t; });
    past.sort(function (a, b) { return b.t - a.t; });
    var seg = el("div", { class: "cf-segment", style: "margin-bottom:12px" });
    [["current", "Current", up.length], ["past", "Past", past.length]].forEach(function (t) {
      seg.appendChild(el("button", { class: (SESSVIEW === t[0] ? "on" : ""), type: "button",
        text: t[1] + " (" + t[2] + ")", onclick: function () { SESSVIEW = t[0]; paintSessions(); } }));
    });
    box.appendChild(seg);
    var rows = SESSVIEW === "past" ? past.slice(0, 40) : up;
    if (!rows.length) { box.appendChild(el("div", { class: "cf-empty", text: SESSVIEW === "past" ? "No past sessions." : "Nothing upcoming — book one above." })); return; }
    var l = el("div", { class: "cf-list" }); rows.forEach(function (it) { l.appendChild(it.node); }); box.appendChild(l);
  }
  function isForChild(e) { return !!(principal && e.player_user_id && String(e.player_user_id) !== String(principal.user_id)); }
  function classRow(e) {
    // An online seat whose payment is still outstanding isn't confirmed — show "Awaiting payment" so the
    // client knows to finish (or that it will auto-release), rather than an "enrolled" chip implying it's done.
    var chip = e.awaiting_payment
      ? el("span", { class: "cf-chip", style: "background:#fde68a;color:#7c2d12", text: "Awaiting payment" })
      : statusChip(e.status);
    return el("div", { class: "cf-item cf-item-tap", onclick: function () { go("#/class/" + e.enrolment_id); } }, [
      el("span", { class: "cf-chip class", text: "Class" }),
      el("div", { class: "cf-item-main" }, [
        el("div", { class: "cf-item-t", text: (e.class_name || "Class") + (isForChild(e) && e.player_name ? " · " + e.player_name : "") }),
        el("div", { class: "cf-item-s", text: UI.fmtDate(e.starts_at) + " · " + timeRange(e) }),
      ]),
      chip,
    ]);
  }

  // billing: monthly breakdown by category (month nav + tap-through)
  function billMonthNav(cur) {
    return el("div", { class: "cf-row", style: "gap:6px;align-items:center" }, [
      el("button", { class: "cf-btn cf-btn-sm", text: "‹", onclick: function () { HBMONTH = shiftM(HBMONTH, -1); loadHomeBilling(cur); } }),
      el("span", { class: "cf-chip", text: mLabel(HBMONTH) }),
      el("button", { class: "cf-btn cf-btn-sm", text: "›", onclick: function () { HBMONTH = shiftM(HBMONTH, 1); loadHomeBilling(cur); } }),
    ]);
  }
  async function loadHomeBilling(cur) {
    var box = document.getElementById("home-billing"); if (!box) return;
    var d = { categories: [] };
    try { d = await window.API.billingSummary(HBMONTH); } catch (e) {}
    cur = cur || d.currency || "ZAR";
    UI.clear(box);
    box.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:6px" }, [
      el("div", { class: "cf-muted", style: "font-size:.78rem;font-weight:700", text: "THIS MONTH" }), billMonthNav(cur),
    ]));
    if (!(d.categories || []).length) box.appendChild(el("div", { class: "cf-empty", text: "No activity in " + mLabel(HBMONTH) + "." }));
    else {
      var l = el("div", { class: "cf-list" });
      d.categories.forEach(function (c) {
        l.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { go("#/billing/cat/" + c.key + "/" + HBMONTH); } }, [
          el("span", { class: "cf-chip " + c.key, text: c.label }),
          el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: c.count + " " + (c.count === 1 ? "session" : "sessions") })]),
          el("span", { style: "font-weight:700", text: money(c.total_minor, cur) }),
          el("span", { class: "cf-muted", text: "›" }),
        ]));
      });
      box.appendChild(l);
    }
    box.appendChild(el("div", { class: "cf-row", style: "margin-top:8px" }, [
      el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Billing by month ›", onclick: function () { go("#/invoices"); } }),
    ]));
  }

  // ---- INVOICES (the member's issued invoice documents — view / download PDF) ----
  // BILLING, MONTH BY MONTH — the shape a member thinks in, and the same one the invoice and the
  // coach settlement now use. It replaced a flat invoice list sitting beside a single running
  // balance: mid-August a member saw July's unpaid R5,000 and August's part-month as ONE figure
  // here and as separate documents there, and the only settle action paid the lot.
  //
  // The CURRENT month has no invoice yet — charges accrue and the sweep bills them on the 1st. It
  // is still payable: paying early simply leaves less for the month-end invoice, because the sweep
  // only ever picks up orders still open. The row says "Not yet invoiced" so that reads as normal
  // rather than as something forgotten.
  async function renderInvoices() {
    loading();
    var data = null;
    try { data = await window.TFAuth.apiJSON("/api/me/statement/months"); }
    catch (e) { set(el("div", {}, [pageHeader("Billing", "Home", "#/"), el("div", { class: "cf-empty", text: UI.errMsg(e) })])); return; }
    var months = (data && data.months) || [];
    var wrap = el("div", {}, [pageHeader("Billing", "Home", "#/")]);
    if (!months.length) { wrap.appendChild(el("div", { class: "cf-empty", text: "Nothing billed yet." })); set(wrap); return; }
    var chipCls = { "Paid": "confirmed", "Unpaid": "owed", "Partially paid": "owed",
                    "Not yet invoiced": "", "Void": "", "Cancelled": "" };
    var l = el("div", { class: "cf-list" });
    months.forEach(function (m) {
      var owed = m.outstanding_minor || 0;
      var right = el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [
        el("span", { style: "font-weight:700", text: money(m.billed_minor, m.currency) }),
      ]);
      if (m.invoice) {
        right.appendChild(el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "PDF",
          onclick: function () { UI.openAuthedFile("/api/billing/invoice/" + m.invoice.invoice_id + "/pdf", (m.invoice.number || "invoice") + ".pdf"); } }));
      }
      // Pay THIS month — never the whole account. A part payment is fine and expected: it just
      // reduces what the month-end invoice picks up.
      if (owed > 0 && (m.open_order_ids || []).length) {
        right.appendChild(el("button", { class: "cf-btn cf-btn-sm cf-btn-primary",
          text: "Pay " + money(owed, m.currency),
          onclick: function () { payOrders(m.open_order_ids); } }));
      }
      var sub = [];
      if (m.paid_minor) sub.push(money(m.paid_minor, m.currency) + " paid");
      if (owed > 0) sub.push(money(owed, m.currency) + " due");
      if (m.written_off_minor) sub.push(money(m.written_off_minor, m.currency) + " written off");
      if (m.invoice && m.invoice.number) sub.push(m.invoice.number);
      l.appendChild(el("div", { class: "cf-item" }, [
        el("span", { class: "cf-chip " + (chipCls[m.status_label] || ""), text: m.status_label }),
        el("div", { class: "cf-item-main" }, [
          el("div", { class: "cf-item-t", text: monthName(m.period) }),
          el("div", { class: "cf-item-s", text: sub.join(" · ") }),
        ]),
        right,
      ]));
      // WHAT THE MONTH IS MADE OF. A closed month has an invoice you can open; the CURRENT one has
      // nothing to click, so without this its row is a bare total and a member cannot see what they
      // are accruing. Shown inline for the un-invoiced month only — for the rest the PDF is the
      // detail, and repeating it here would just be noise.
      if (!m.invoice && (m.by_category || []).length) {
        var det = el("div", { style: "padding:2px 0 10px 12px" });
        m.by_category.forEach(function (c) {
          det.appendChild(el("div", { class: "cf-row", style: "justify-content:space-between;padding:2px 0" }, [
            el("span", { class: "cf-muted", style: "font-size:.84rem",
                         text: c.label + (c.n ? " · " + c.n : "") }),
            el("span", { style: "font-size:.84rem;font-weight:600", text: money(c.amount_minor, m.currency) }),
          ]));
        });
        det.appendChild(el("div", { class: "cf-muted", style: "font-size:.76rem;margin-top:4px",
          text: "Accrued so far this month — your invoice is issued on the 1st." }));
        l.appendChild(det);
      }
    });
    wrap.appendChild(l);
    set(wrap);
  }

  function monthName(ym) {
    try {
      var p = String(ym).split("-");
      return new Date(p[0], parseInt(p[1], 10) - 1, 1)
        .toLocaleDateString(undefined, { month: "long", year: "numeric" });
    } catch (e) { return ym; }
  }
  // ---- MY RECORD (the ONE Client-360 widget, client scope — golden rule) ----
  // The member's full account is a VIEW off the one client360 composer (API.my360 →
  // GET /api/me/360, scope='client') rendered by the SAME Widgets.ClientRecord used in the
  // admin + coach apps — identity, membership, packages, owed statement, online payments,
  // bookings (drill), refunds, dependents, activity. Role differences are CONFIG ONLY: the
  // client `can` map allows pay + request_refund and nothing else (staff actions never wire,
  // so their controls never render). Reached via Home's "Full activity ›" (route #/activity).
  function renderRecord() {
    var host = el("div", {});
    set(host);
    window.Widgets.ClientRecord.mount(host, {
      scope: { id: null, role: "client" },   // self — the adapter ignores the id
      back: { label: "Home", hash: "#/home" },
      // The "This month" summary + the owed statement are the headline; the raw money activity feed is
      // OFF for the client (it was the confusing transaction firehose — refunds/voids/write-offs).
      fields: { showActivity: false, showDependents: true, showPackages: true, showCoaching: false },
      onEditProfile: function () { PROFILE_RETURN = "#/activity"; go("#/profile"); },
      onSettleAll: function () { payOrders(null); },   // the spend rollup's "Settle" → pay all outstanding
      data: { get: function (i, m) { return window.API.my360(m).then(function (r) { return r.person; }); } },
      onNavigate: function (t) {
        if (!t || !t.id) return;
        if (t.kind === "class") go("#/class/" + t.id);
        else if (t.kind === "order") go("#/txn/" + t.id);   // a purchase (pack/membership/invoice) record
        else go("#/booking/" + t.id);        // event → the client's booking story (same hash as everywhere)
      },
      actions: {
        // Owed statement line → settle it online (unified statement → Yoco). Reuses payOrders.
        pay: { manual: true, run: function (it) { payOrders([it.order_id]); } },
        // Raise a refund request for the club/coach to review. Reuses requestRefund.
        request_refund: { manual: true, run: function (it) { requestRefund(it.order_id); } },
      },
    });
  }

  // plan & credits
  function planCard(plan, cur) {
    var c = card([el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:8px" }, [
      el("h2", { style: "margin:0", text: "Plan & credits" }),
      el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost", text: "Manage ›", onclick: openPlan }),
    ])]);
    var line = plan.is_trial ? ("7 Day Trial Period — " + (plan.trial_days_left || 0) + " days left · courts free")
      : plan.active ? (plan.name || "Membership") + (plan.current_period_end ? " · renews " + plan.current_period_end : "")
      : "Pay as you go — no membership";
    c.appendChild(el("div", { class: "cf-item" }, [
      el("span", { class: "cf-chip " + (plan.active ? "confirmed" : ""), text: plan.active ? "Active" : (plan.owed_membership ? "Owed" : "PAYG") }),
      el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: plan.owed_membership && !plan.active ? "Membership — awaiting payment" : line })]),
    ]));
    // Cancel is available for a paid/active membership OR an owed-but-unpaid one (else it's stuck owed).
    if ((plan.active && !plan.is_trial) || plan.owed_membership) {
      c.appendChild(el("div", { class: "cf-row", style: "margin-top:8px" }, [
        el("button", { class: "cf-btn cf-btn-sm cf-btn-ghost cf-btn-danger", text: "Cancel membership", onclick: cancelMembership }),
      ]));
    }
    c.appendChild(el("div", { id: "home-wallets" }));
    return c;
  }
  function cancelMembership() {
    if (!window.confirm("Cancel your membership? An unpaid membership charge is cleared; a paid term isn't refunded here (request a refund separately).")) return;
    window.TFAuth.apiJSON("/api/me/membership/cancel", { method: "POST" })
      .then(function () { UI.toast("Membership cancelled.", "info"); renderHome(); },
            function (e) { UI.toast(UI.errMsg(e), "error"); });
  }
  async function loadWallets(cur) {
    var box = document.getElementById("home-wallets"); if (!box) return;
    var wallets = [];
    try { wallets = (await window.TFAuth.apiJSON("/api/billing/bundles/wallets?active=1")).wallets || []; } catch (e) {}
    if (!wallets.length) return;
    var l = el("div", { class: "cf-list" });
    wallets.forEach(function (w) {
      l.appendChild(el("div", { class: "cf-item" }, [
        el("span", { class: "cf-chip " + (w.service_kind || ""), text: w.service_kind || "pack" }),
        el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: w.label || "Session pack" }), el("div", { class: "cf-item-s", text: (w.sessions_remaining != null ? w.sessions_remaining : "–") + " left" + (w.expires_at ? " · expires " + UI.fmtDate(w.expires_at) : "") })]),
      ]));
    });
    box.appendChild(l);
  }

  // billing category → its items → each drills into the booking story
  async function renderBillingCategory(key, month) {
    // Never render without a month — a missing one made mLabel() throw and hang the spinner.
    month = month || HBMONTH || curMonth();
    HBMONTH = month;   // keep Home's month nav in sync with the month drilled into
    loading();
    var d;
    try { d = await window.API.billingSummary(month); } catch (e) { set(el("div", {}, [pageHeader("Billing", "Home", "#/"), el("div", { class: "cf-empty", text: UI.errMsg(e) })])); return; }
    var cat = (d.categories || []).filter(function (c) { return c.key === key; })[0];
    var cur = d.currency || "ZAR";
    var wrap = el("div", {});
    wrap.appendChild(pageHeader(cat ? cat.label : key, "Home", "#/"));
    wrap.appendChild(el("p", { class: "cf-muted", style: "margin:0 0 12px", text: mLabel(month) + " · " + (cat ? cat.count : 0) + " · " + money(cat ? cat.total_minor : 0, cur) }));
    if (!cat || !cat.items.length) wrap.appendChild(el("div", { class: "cf-empty", text: "Nothing here." }));
    else {
      var box = el("div", { class: "cf-card", style: "padding:6px 14px" }), l = el("div", { class: "cf-list" });
      cat.items.forEach(function (it) {
        l.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { go(it.booking_id ? ("#/booking/" + it.booking_id) : it.enrolment_id ? ("#/class/" + it.enrolment_id) : ("#/txn/" + it.order_id)); } }, [
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: UI.fmtDate(it.starts_at) + (it.coach_name ? " · " + it.coach_name : "") }),
            el("div", { class: "cf-item-s", text: [it.court_name, (function () { try { return UI.fmtTime(it.starts_at); } catch (e) { return ""; } })()].filter(Boolean).join(" · ") }),
          ]),
          el("span", { style: "font-weight:600" + (it.status === "written_off" ? ";text-decoration:line-through;opacity:.55" : ""), text: money(it.amount_minor, cur) }),
          statusChip(it.status),
        ]));
      });
      box.appendChild(l); wrap.appendChild(box);
    }
    set(wrap);
  }

  var INTENT_WORD = window.CFIntent;   // ONE vocabulary, defined in crm_ui.js

  function bookingRow(b) {
    // A GAME reads as a game. Before this, a member who had just published an open game saw a row
    // identical to every other court booking — no badge, no seat count, and tapping it opened the
    // plain booking record rather than the game. Nothing on the screen reflected the thing they had
    // just done, which is why posting one "felt like a normal booking".
    var isGame = b.visibility === "open" || (b.seats > 1 && b.seats_taken != null);
    var open = isGame ? Math.max(0, (b.seats || 0) - (b.seats_taken || 0)) : 0;
    var sub = UI.fmtDate(b.starts_at) + " · " + timeRange(b);
    if (isGame) {
      var bits = [];
      if (INTENT_WORD.word(b.play_intent)) bits.push(INTENT_WORD.word(b.play_intent));
      bits.push(open > 0 ? (open + (open === 1 ? " seat open" : " seats open")) : "Full");
      sub += " · " + bits.join(" · ");
    }
    return el("div", { class: "cf-item cf-item-tap", onclick: function () {
      // An open game drills to the GAME (who's in, chat), not the bare booking record.
      go(isGame ? "#/game/" + b.id : "#/booking/" + b.id);
    } }, [
      el("span", { class: "cf-chip " + b.booking_type, text: typeLabel(b.booking_type) }),
      el("div", { class: "cf-item-main" }, [
        el("div", { class: "cf-item-t", text: (b.court_name || b.resource_name || typeLabel(b.booking_type)) }),
        el("div", { class: "cf-item-s", text: sub }),
      ]),
      isGame && open > 0
        ? el("span", { class: "cf-chip ok", text: "Open game" })
        : statusChip(b.status),
      openUpButton(b),
    ].filter(Boolean));
  }

  // THE SEAM between the two blocks, and the thing that stops a wrong turn becoming a support message.
  //
  // Someone books a court with a friend (BOOK block), and on Thursday the friend drops out. Without
  // this they are holding a court they cannot use and the club gets an email. `set_visibility` already
  // exists server-side; all that was missing was somewhere to press it. Offered on the member's own
  // upcoming COURT bookings that are not open yet — and only where the club offers the community.
  function openUpButton(b) {
    if (!DATA.community || !DATA.community.community_enabled) return null;
    if (b.booking_type !== "court" || b.visibility === "open") return null;
    if (new Date(b.starts_at) <= new Date()) return null;
    var btn = el("button", { class: "cf-btn cf-btn-sm", text: "Open to the club" });
    btn.addEventListener("click", function (ev) {
      ev.stopPropagation();                       // the row itself drills through; this must not
      window.API.setGameVisibility(b.id, true).then(function () {
        UI.toast("Posted — another member can now take the spare seat.", "info");
        renderHome();
      }, function (e) { UI.toast(UI.errMsg(e), "error"); });
    });
    return btn;
  }
  function act(fn, okMsg) { fn().then(function () { UI.toast(okMsg, "info"); route(); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); }

  // ---- BOOK (mount the existing full-screen flow) --------------------------
  var PENDING_EQUIP = null;   // a featured-equipment id parked by a Home hero tile → pre-added to the booking
  function renderBook(kind) {
    UI.clear(view);
    if (window.BookFlow) {
      var opts = { plansHref: "#/plan" };   // a PAYG member can jump to buy a membership/pack at checkout
      if (PENDING_EQUIP) { opts.featureEquipment = PENDING_EQUIP; PENDING_EQUIP = null; }
      window.BookFlow.start(principal, kind || "court", opts);
    } else set(el("div", { class: "cf-empty", text: "Booking is unavailable." }));
  }

  // ---- BOOKING STORY (the full drill-through) ------------------------------
  // The ONE shared transaction detail (Widgets.TransactionDetail), flat action row for the client's
  // simpler set. Its action UIs (payOrders/rescheduleSheet/cancelBooking/requestRefund) stay local.
  function renderBookingStory(id) {
    var host = el("div", {});
    set(host);
    window.Widgets.TransactionDetail.mount(host, {
      role: "client",
      scope: { id: id },
      grouped: false,
      data: { get: function (i) { return window.API.bookingStory(i).then(function (r) { return r.booking; }); } },
      actions: {
        pay: { manual: true, run: function (b) { payOrders([b.charge.order_id]); } },
        add_to_calendar: { manual: true, run: function (b) { addToCalendar(b.ics_url); } },
        receipt: { manual: true, run: function (b) { window.open("/receipt.html?order=" + encodeURIComponent(b.charge.order_id), "_blank"); } },
        reschedule: { manual: true, run: function (b) { rescheduleSheet(b); } },
        add_player: { manual: true, run: function (b) { window.CRMUI.addLessonPlayerModal({ onSubmit: function (payload) { return window.API.addBookingPlayer(b.id, payload); }, onDone: function () { renderBookingStory(b.id); } }); } },
        cancel: { manual: true, run: function (b) { cancelBooking(b); } },
        request_refund: { manual: true, run: function (b) { requestRefund(b.charge.order_id); } },
      },
    });
  }

  // A purchase (pack / membership / invoice) as the SAME transaction record — money card + audit log +
  // pay/receipt/request-refund. Reached from an order-only event (instead of a read-only receipt).
  function renderTxn(orderId) {
    var host = el("div", {});
    set(host);
    window.Widgets.TransactionDetail.mount(host, {
      role: "client",
      scope: { id: orderId },
      grouped: false,
      data: { get: function (i) { return window.API.orderRecord(i).then(function (r) { return r.booking; }); } },
      actions: {
        pay: { manual: true, run: function (b) { payOrders([b.order_id || (b.charge && b.charge.order_id)]); } },
        receipt: { manual: true, run: function (b) { window.open("/receipt.html?order=" + encodeURIComponent(b.order_id || b.charge.order_id), "_blank"); } },
        request_refund: { manual: true, run: function (b) { requestRefund(b.order_id || b.charge.order_id); } },
      },
    });
  }

  function cancelBooking(b) {
    var fee = b.cancel_fee_minor || 0;
    var msg = "Cancel this " + typeLabel(b.booking_type).toLowerCase() + " on " + UI.fmtDate(b.starts_at) + "?";
    if (fee > 0) msg += "\n\nThis is a late cancellation — a fee of " + money(fee) + " applies.";
    if (!window.confirm(msg)) return;
    var orderId = b.charge && b.charge.order_id;
    window.API.cancelBooking(b.id, { reason: "client cancelled" }).then(function (res) {
      UI.toast(fee > 0 ? ("Cancelled — a " + money(fee) + " late fee applies.") : "Cancelled.", "info");
      // L1: a PAID booking isn't auto-refunded on cancel — offer to request one right away.
      if (res && res.was_paid && orderId &&
          window.confirm("You paid for this booking. Request a refund now?")) { requestRefund(orderId); return; }
      go("#/");
    }, function (e) { UI.toast(UI.errMsg(e), "error"); });
  }
  function requestRefund(orderId) {
    var reason = window.prompt("Request a refund for this booking? Add a reason (optional):", "");
    if (reason === null) return;
    window.API.requestRefund({ order_id: orderId, reason: reason }).then(function () { UI.toast("Refund requested — the club or your coach will review it.", "info"); route(); }, function (e) { UI.toast(UI.errMsg(e), "error"); });
  }
  // Reschedule is the ONE shared CRMUI.rescheduleModal (its configured-durations behaviour came FROM
  // this fork). A member may move their own COURT booking to another court; a LESSON's court stays
  // club-allocated — the standing rule is that a client picks the coach, never the court.
  function rescheduleSheet(b) {
    CRMUI.rescheduleModal({
      booking: b,
      canChangeCourt: b.booking_type === "court",
      onDone: route,
    });
  }

  // Pay a set of owed orders (null = pay all) via the unified statement settlement → Yoco.
  function payOrders(orderIds) {
    var body = orderIds ? { order_ids: orderIds } : {};
    window.API.payStatement(body).then(function (res) {
      if (res && res.order_id && window.Pay) window.Pay.startYocoCheckout(res.order_id);
      else UI.toast("Nothing to pay.", "info");
    }, function (e) {
      if (e && e.status === 409) { UI.toast("Nothing owed.", "info"); route(); }
      else UI.toast(UI.errMsg(e), "error");
    });
  }

  // ---- ORDER / RECEIPT detail ---------------------------------------------
  // (renderOrder — the old client receipt PAGE — was retired: an order now opens its transaction
  //  record via renderTxn (#/txn / #/billing/order both route there), with a Receipt action that
  //  prints receipt.html. One record per transaction; the receipt is a print output, not a page.)

  // ---- CLASS record — the SAME transaction-record widget as bookings (via /api/me/classes/:id) -----
  function renderClassStory(id) {
    var host = el("div", {});
    set(host);
    window.Widgets.TransactionDetail.mount(host, {
      role: "client",
      scope: { id: id },
      grouped: false,
      data: { get: function (i) { return window.TFAuth.apiJSON("/api/me/classes/" + encodeURIComponent(i)).then(function (r) { return r.booking; }); } },
      actions: {
        pay: { manual: true, run: function (b) { payOrders([b.charge.order_id]); } },
        settle: { manual: true, run: function (b) { payOrders([b.charge.order_id]); } },
        receipt: { manual: true, run: function (b) { window.open("/receipt.html?order=" + encodeURIComponent(b.charge.order_id), "_blank"); } },
        request_refund: { manual: true, run: function (b) { requestRefund(b.charge.order_id); } },
        cancel: { tone: "danger", back: true, done: "Cancelled.", run: function (b) { return window.API.cancelEnrolment(b.class_session_id, isForChild(b) ? { user_id: b.player_user_id } : {}); } },
      },
    });
  }

  // ---- PLAN (reuse the existing 3-purchasing-models wizard as an overlay) --
  function openPlan(kind) {
    // Scope the wizard to the service the member came from (court/lesson/class) so it only offers THAT
    // service's plans — a class shows class packs, a lesson lesson packs, a court packs + membership.
    var opts = (kind === "court" || kind === "lesson" || kind === "class") ? { forKind: kind } : {};
    if (window.PlanWizard && window.PlanWizard.open) window.PlanWizard.open(opts);
    else location.href = "/plan";
  }
  function renderPlan(kind) { renderHome(); openPlan(kind); }

  // ---- PROFILE (ONE screen: your details + family, editable inline) --------
  var FIELDS = [
    ["first_name", "First name", "text"], ["surname", "Surname", "text"], ["phone", "Phone", "tel"],
    ["dob", "Date of birth", "date"], ["address_line1", "Address line 1", "text"], ["address_line2", "Address line 2", "text"],
    ["city", "City", "text"], ["postal_code", "Postal code", "text"],
    ["emergency_contact_name", "Emergency contact", "text"], ["emergency_contact_phone", "Emergency phone", "tel"],
  ];
  async function renderProfile() {
    loading();
    var pr = DATA.profile, deps = [];
    try { pr = await window.API.getProfile(); DATA.profile = pr; } catch (e) { pr = pr || {}; }
    try { deps = (await window.API.dependents()).dependents || []; } catch (e) {}
    NAME = fullName(pr); paintAvatar();
    var wrap = el("div", {});
    // Identity band — this IS the profile's own header (name · email · avatar), not a repeated greeting.
    wrap.appendChild(el("div", { class: "cf-greet" }, [
      el("div", {}, [el("h1", { text: NAME || "Your profile" }), el("p", { text: pr.email || "" })]),
      el("span", { class: "cf-avatar", style: "background:rgba(255,255,255,.2);color:#fff;border-color:transparent", text: initials() }),
    ]));
    // Your details — the account holder (parent), editable inline.
    var inputs = {};
    var dc = card([el("h2", { style: "margin:0 0 10px", text: "Your details" })]);
    dc.appendChild(kv("Email", el("span", { class: "cf-muted", text: (pr.email || "") + "  (sign-in — can't change)" })));
    FIELDS.forEach(function (f) {
      var inp = el("input", { class: "cf-input", type: f[2], value: pr[f[0]] || "" });
      inputs[f[0]] = inp;
      dc.appendChild(el("div", { class: "cf-field" }, [el("label", { text: f[1] }), inp]));
    });
    dc.appendChild(el("label", { class: "cf-toggle" }, [
      (function () { var cb = el("input", { type: "checkbox" }); if (pr.marketing_opt_in) cb.checked = true; inputs._mk = cb; return cb; })(),
      el("span", { text: "Email me club news & offers" }),
    ]));
    dc.appendChild(el("div", { class: "cf-row", style: "margin-top:12px" }, [
      el("button", { class: "cf-btn cf-btn-primary cf-btn-block", text: "Save", onclick: function () {
        var body = {}; FIELDS.forEach(function (f) { body[f[0]] = inputs[f[0]].value.trim() || null; });
        body.marketing_opt_in = !!inputs._mk.checked;
        window.API.patchProfile(body).then(function (res) {
          DATA.profile = res; NAME = fullName(res); paintAvatar(); UI.toast("Saved.", "info");
          // Close back to wherever the edit was launched from (the record → Client 360); else stay.
          if (PROFILE_RETURN) { var r = PROFILE_RETURN; PROFILE_RETURN = null; go(r); }
        }, function (e) { UI.toast((e && e.body && e.body.error === "VALIDATION") ? "Please check the fields." : UI.errMsg(e), "error"); });
      } }),
    ]));
    wrap.appendChild(dc);
    // Family — the children you book for (their details), below yours. Add/edit drills to the child editor.
    var fc = card([el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;margin-bottom:6px" }, [
      el("h2", { style: "margin:0", text: "Family" }),
      el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "+ Add child", onclick: function () { go("#/profile/child"); } }),
    ])]);
    if (!deps.length) fc.appendChild(el("div", { class: "cf-empty", text: "Add a child to book on their behalf." }));
    else { var dl = el("div", { class: "cf-list" }); deps.forEach(function (d) {
      dl.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { go("#/profile/child/" + d.id); } }, [
        el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: [d.first_name, d.surname].filter(Boolean).join(" ") }),
          el("div", { class: "cf-item-s", text: (d.relationship || "child") + (d.dob ? " · " + d.dob : "") })]),
        el("span", { class: "cf-muted", text: "›" }),
      ]));
    }); fc.appendChild(dl); }
    wrap.appendChild(fc);
    set(wrap);
  }

  async function renderChildEdit(id) {
    var dep = null;
    if (id) { try { dep = ((await window.API.dependents()).dependents || []).filter(function (d) { return String(d.id) === String(id); })[0]; } catch (e) {} }
    var wrap = el("div", {});
    wrap.appendChild(pageHeader(id ? "Edit child" : "Add child", "Profile", "#/profile"));
    var c = card([]); var inputs = {};
    [["first_name", "First name", "text"], ["surname", "Surname", "text"], ["dob", "Date of birth", "date"]].forEach(function (f) {
      var inp = el("input", { class: "cf-input", type: f[2], value: dep ? (dep[f[0]] || "") : "" }); inputs[f[0]] = inp;
      c.appendChild(el("div", { class: "cf-field" }, [el("label", { text: f[1] }), inp]));
    });
    var rel = el("select", { class: "cf-input" }, ["child", "spouse", "partner", "other"].map(function (o) { return el("option", { value: o, text: o[0].toUpperCase() + o.slice(1) }); }));
    if (dep && dep.relationship) rel.value = dep.relationship; inputs.relationship = rel;
    c.appendChild(el("div", { class: "cf-field" }, [el("label", { text: "Relationship" }), rel]));
    wrap.appendChild(c);
    var row = el("div", { class: "cf-row", style: "gap:8px;margin-top:14px" }, [
      el("button", { class: "cf-btn cf-btn-primary cf-btn-block", text: "Save & close", onclick: function () {
        var body = { first_name: inputs.first_name.value.trim(), surname: inputs.surname.value.trim() || null, dob: inputs.dob.value || null, relationship: inputs.relationship.value };
        if (!body.first_name) { UI.toast("First name is required.", "warn"); return; }
        var p = id ? window.API.patchDependent(id, body) : window.API.addDependent(body);
        p.then(function () { UI.toast("Saved.", "info"); go("#/profile"); }, function (e) { UI.toast(UI.errMsg(e), "error"); });
      } }),
    ]);
    wrap.appendChild(row);
    if (id) wrap.appendChild(el("div", { style: "margin-top:10px;text-align:center" }, [
      el("button", { class: "cf-btn cf-btn-ghost cf-btn-sm", text: "Remove child", onclick: function () {
        if (!window.confirm("Remove this child?")) return;
        window.API.removeDependent(id).then(function () { UI.toast("Removed.", "info"); go("#/profile"); }, function (e) { UI.toast(UI.errMsg(e), "error"); });
      } }),
    ]));
    set(wrap);
  }

  var modal = window.UI.modal, toLocal = window.UI.toLocal;   // shared (Wave 1)

  window.Client = { start: start };
})();

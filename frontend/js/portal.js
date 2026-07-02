// portal.js — shared portal bootstrap: auth gate, principal resolve, role-aware nav.
// Used by portal.html and every section page (book/my/coach/admin) so each can run
// standalone or inside the portal shell. Renders the top bar + nav into #cf-topbar.
(function () {
  var principalP = null;

  // Resolve and cache the principal (GET /api/whoami). Redirects to sign-in if needed.
  function principal() {
    if (principalP) return principalP;
    principalP = (async function () {
      await window.TFAuth.ready();
      if (!window.TFAuth.isAuthed()) {
        await window.TFAuth.requireAuth();
        return null;
      }
      try {
        return await window.API.whoami();
      } catch (e) {
        if (e.status === 401) { await window.TFAuth.requireAuth(); }
        return null;
      }
    })();
    return principalP;
  }

  // Absolute hrefs so nav works at any URL depth. Front-end redesign (2026-07): the nav is now
  // ROLE-FOCUSED — a coach/owner no longer sees the CLIENT "Home" (the booking cockpit) as their
  // face. Members book (Home + Account); a coach lives in their Coach console; an owner in the
  // Admin console + Settings. `landingFor()` sends staff straight to their console on sign-in.
  //   member/guest → Home · Account
  //   coach        → Coach · Account      (Account = their own money/bookings as a member)
  //   owner/admin  → Admin · Settings
  var NAV = [
    { href: "/portal.html",   label: "Home",     roles: ["member", "guest"] },
    { href: "/coach.html",    label: "Coach",    roles: ["coach"] },
    { href: "/admin.html",    label: "Admin",    roles: ["club_admin", "platform_admin"] },
    { href: "/account.html",  label: "Account",  roles: ["member", "guest", "coach"] },
    { href: "/settings.html", label: "Settings", roles: ["club_admin", "platform_admin"] },
  ];

  // Where a role lands on sign-in (the client Home is not a coach/owner's face).
  function landingFor(role) {
    if (role === "coach") return "/coach.html";
    if (role === "club_admin" || role === "platform_admin") return "/admin.html";
    return "/portal.html";
  }

  function allowed(item, role) {
    return item.roles.indexOf("*") >= 0 || item.roles.indexOf(role) >= 0;
  }

  // Render the top bar into the page. `active` is the current page filename.
  function renderShell(p, active) {
    var UI = window.UI, el = UI.el;
    var host = document.getElementById("cf-topbar");
    if (!host) return;
    UI.clear(host);

    var brand = el("div", { class: "cf-brand" }, [
      el("span", { class: "cf-logo", text: "NP" }),
      el("span", { text: "NextPoint" }),
    ]);

    var nav = el("nav", { class: "cf-nav" });
    NAV.forEach(function (item) {
      if (!p || !allowed(item, p.role)) return;
      var a = el("a", { href: item.href, text: item.label });
      // Normalise both sides (strip leading "/") so "/book.html" matches active "book.html".
      if (item.href.replace(/^\//, "") === String(active || "").replace(/^\//, "")) a.classList.add("active");
      nav.appendChild(a);
    });

    var user = el("div", { class: "cf-user" }, [
      el("span", { text: (p && (p.email || p.role)) || "" }),
      el("button", {
        class: "cf-btn cf-btn-sm", text: "Sign out",
        onclick: function () { window.TFAuth.signOut().then(function () { location.reload(); }); },
      }),
    ]);

    host.appendChild(brand);
    host.appendChild(nav);
    host.appendChild(el("span", { class: "cf-spacer" }));

    // Notification bell (only for a signed-in club member — it needs a club-scoped inbox).
    if (p && p.club_id) {
      var bell = el("div", { class: "cf-bell-host" });
      host.appendChild(bell);
      mountBell(bell);
    }

    host.appendChild(user);
  }

  // Mount the notification bell, lazy-loading notifications.js once if a shell didn't include it.
  function mountBell(hostEl) {
    if (window.Notifications) { window.Notifications.mount(hostEl); return; }
    var existing = document.querySelector('script[data-cf-notif]');
    if (existing) {
      existing.addEventListener("load", function () {
        if (window.Notifications) window.Notifications.mount(hostEl);
      });
      return;
    }
    var s = document.createElement("script");
    s.src = "/js/notifications.js";
    s.setAttribute("data-cf-notif", "1");
    s.onload = function () { if (window.Notifications) window.Notifications.mount(hostEl); };
    document.head.appendChild(s);
  }

  // Standard page boot: resolve principal, gate by required roles, render shell.
  // opts: {active, requireRoles?:[...], onReady(principal)}
  async function boot(opts) {
    opts = opts || {};
    var p = await principal();
    renderShell(p, opts.active);
    if (!p) return; // redirecting to sign-in
    if (opts.requireRoles && opts.requireRoles.indexOf(p.role) < 0) {
      var main = document.getElementById("cf-main");
      if (main) main.innerHTML =
        '<div class="cf-card cf-empty">This area is not available for your role (' +
        window.UI.esc(p.role || "unknown") + ").</div>";
      return;
    }
    if (!p.club_id) {
      var m = document.getElementById("cf-main");
      if (m) m.innerHTML =
        '<div class="cf-card cf-empty">No active club is resolved for your account. ' +
        "Contact the club to be added as a member.</div>";
      return;
    }
    if (typeof opts.onReady === "function") opts.onReady(p);
  }

  window.Portal = { principal: principal, boot: boot, renderShell: renderShell, landingFor: landingFor };
})();

// notifications.js — the portal topbar notification bell + inbox dropdown.
//
// Lightweight, vanilla, design-system-consistent (cf-bell* / cf-notif* in app.css). Driven by
// the member API (GET /api/me/notifications, POST /api/me/notifications/read). Mounted by
// portal.js into the topbar. Degrades silently if the API is unavailable (no bell shown).
//
// UX: a bell with an unread badge; click toggles a dropdown of recent items. Clicking an item
// marks it read and (if it has a link) navigates there. "Mark all read" clears the badge.
(function () {
  var UI = window.UI;
  var state = { items: [], unread: 0, open: false, loaded: false };

  function api() { return window.API; }

  function fmtAgo(iso) {
    if (!iso) return "";
    var then = new Date(iso).getTime();
    if (isNaN(then)) return "";
    var s = Math.max(0, (Date.now() - then) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    return Math.floor(s / 86400) + "d ago";
  }

  function render(host) {
    UI.clear(host);
    var btn = UI.el("button", {
      class: "cf-bell" + (state.unread ? " has-unread" : ""),
      type: "button", "aria-label": "Notifications", title: "Notifications",
      onclick: function (e) { e.stopPropagation(); toggle(host); },
    }, [UI.el("span", { class: "cf-bell-ico", html: BELL_SVG })]);
    if (state.unread) {
      btn.appendChild(UI.el("span", { class: "cf-bell-badge",
        text: state.unread > 9 ? "9+" : String(state.unread) }));
    }
    host.appendChild(btn);

    if (state.open) host.appendChild(panel(host));
  }

  function panel(host) {
    var head = UI.el("div", { class: "cf-notif-head" }, [
      UI.el("span", { text: "Notifications" }),
      UI.el("button", {
        class: "cf-notif-allread", type: "button", text: "Mark all read",
        onclick: function (e) { e.stopPropagation(); markAll(host); },
      }),
    ]);

    var list = UI.el("div", { class: "cf-notif-list" });
    if (!state.items.length) {
      list.appendChild(UI.el("div", { class: "cf-notif-empty",
        text: state.loaded ? "You're all caught up." : "Loading…" }));
    } else {
      state.items.forEach(function (n) {
        var item = UI.el("button", {
          class: "cf-notif-item" + (n.read_at ? "" : " unread"),
          type: "button",
          onclick: function (e) { e.stopPropagation(); openItem(host, n); },
        }, [
          UI.el("div", { class: "cf-notif-title", text: n.title || "" }),
          n.body ? UI.el("div", { class: "cf-notif-body", text: n.body }) : null,
          UI.el("div", { class: "cf-notif-time", text: fmtAgo(n.created_at) }),
        ]);
        list.appendChild(item);
      });
    }

    return UI.el("div", { class: "cf-notif-panel", onclick: function (e) { e.stopPropagation(); } },
      [head, list]);
  }

  function toggle(host) {
    state.open = !state.open;
    render(host);
    if (state.open) refresh(host);
  }

  function close(host) {
    if (!state.open) return;
    state.open = false;
    render(host);
  }

  async function refresh(host) {
    try {
      var r = await api().notifications({ limit: 20 });
      state.items = r.notifications || [];
      state.unread = r.unread_count || 0;
      state.loaded = true;
    } catch (e) {
      state.loaded = true; // show empty rather than spin forever
    }
    render(host);
  }

  async function openItem(host, n) {
    try {
      if (!n.read_at) { await api().markNotificationsRead({ id: n.id }); }
    } catch (e) { /* non-fatal */ }
    if (n.link) { window.location.href = n.link; return; }
    refresh(host);
  }

  async function markAll(host) {
    try {
      var r = await api().markNotificationsRead({ all: true });
      state.unread = (r && r.unread_count) || 0;
      state.items = state.items.map(function (n) {
        if (!n.read_at) n.read_at = new Date().toISOString();
        return n;
      });
    } catch (e) { /* non-fatal */ }
    render(host);
  }

  // Public: mount the bell into a topbar host element + do an initial unread fetch.
  function mount(host) {
    if (!host || !window.API || !window.UI) return;
    render(host);
    // Initial unread-count fetch (cheap) so the badge shows without opening the panel.
    (async function () {
      try {
        var r = await api().notifications({ unread: 1, limit: 1 });
        state.unread = r.unread_count || 0;
        render(host);
      } catch (e) { /* no bell state — stays at 0 */ }
    })();
    // Close on outside click.
    document.addEventListener("click", function () { close(host); });
  }

  var BELL_SVG =
    '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" ' +
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/>' +
    '<path d="M13.7 21a2 2 0 0 1-3.4 0"/></svg>';

  window.Notifications = { mount: mount };
})();

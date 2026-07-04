// widgets/setup.js — the shared Setup framework (FRONTEND-STANDARDISATION Wave 6).
//
// Widgets.Setup: the gold-standard "section menu → full-screen focused editor" shell, shared by the
// OWNER and COACH consoles so both get the identical, owner-loved Setup interaction. Each app passes
// its OWN role-appropriate section list; the shell (menu · drill-in · back) is ONE component.
//
//   Widgets.Setup.mount(host, {
//     sections: [{ key, label, desc, mount(sectionHost) | href }],  // app supplies, already role-scoped
//     active,          // current section key (from the router) | falsy = show the menu
//     onOpen(key),     // navigate INTO an inline section (the app sets the hash → #/setup/<key>)
//     backHash,        // where the section's Back goes (default "#/setup")
//     title, intro, footer,   // optional chrome
//   })
//
// A section with `mount` is an inline focused editor (menu → back). A section with `href` is just a
// menu link that navigates elsewhere (e.g. a coach's profile/hours which are their own routes).
//
// Widgets.ServiceList: the ONE services list (lessons/classes/courts) both consoles render — edit
// (ServiceEditor) + lifecycle (deactivate/reactivate/terminate) + optional create. The owner sees
// ALL services; a coach sees only their OWN. /api/services enforces who may change what, so this is
// purely presentation.
(function () {
  window.Widgets = window.Widgets || {};

  window.Widgets.Setup = {
    mount: function (host, cfg) {
      var UI = window.UI, el = UI.el;
      cfg = cfg || {};
      var sections = cfg.sections || [];
      var active = cfg.active && sections.filter(function (s) { return s.key === cfg.active && s.mount; })[0];

      if (active) {
        var secHost = el("div", {});
        UI.clear(host);
        host.appendChild(el("div", {}, [UI.backBar(cfg.title || "Setup", cfg.backHash || "#/setup"), secHost]));
        secHost.appendChild(el("div", { class: "cf-loading", text: "Loading…" }));
        try { active.mount(secHost); }
        catch (e) { UI.clear(secHost); secHost.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); }
        return;
      }

      // The section menu (the gold-standard list).
      var wrap = el("div", {});
      wrap.appendChild(el("h1", { style: "margin:0 0 4px", text: cfg.title || "Setup" }));
      if (cfg.intro) wrap.appendChild(el("p", { class: "cf-muted", style: "margin:0 0 14px", text: cfg.intro }));
      var c = el("div", { class: "cf-card" }), l = el("div", { class: "cf-list" });
      sections.forEach(function (s) {
        l.appendChild(el("div", { class: "cf-item cf-item-tap", onclick: function () { if (s.href) location.hash = s.href.replace(/^#/, ""); else cfg.onOpen(s.key); } }, [
          el("div", { class: "cf-item-main" }, [el("div", { class: "cf-item-t", text: s.label }), s.desc ? el("div", { class: "cf-item-s", text: s.desc }) : null].filter(Boolean)),
          el("span", { class: "cf-muted", text: "›" }),
        ]));
      });
      c.appendChild(l); wrap.appendChild(c);
      if (cfg.footer) wrap.appendChild(cfg.footer);
      UI.clear(host); host.appendChild(wrap);
    },
  };

  window.Widgets.ServiceList = {
    mount: function (host, cfg) {
      var UI = window.UI, el = UI.el;
      cfg = cfg || {};
      var kinds = cfg.kinds || ["lesson", "class", "court"];
      var KIND_LABEL = { lesson: "Lessons", class: "Classes", court: "Courts" };
      var state = { kind: kinds[0], life: "active", services: null };
      function money(m) { return UI.money(m || 0, "ZAR"); }

      function load() {
        UI.clear(host);
        host.appendChild(el("div", { class: "cf-loading", text: "Loading services…" }));
        window.TFAuth.apiJSON("/api/services").then(function (res) {
          var svcs = (res && res.services) || [];
          if (cfg.role === "coach" && cfg.userId) svcs = svcs.filter(function (s) { return s.coach_user_id && String(s.coach_user_id) === String(cfg.userId); });
          state.services = svcs; draw();
        }, function (e) { UI.clear(host); host.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) })); });
      }

      function draw() {
        UI.clear(host);
        host.appendChild(el("div", { class: "cf-card" }, [
          el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap" }, [
            el("div", {}, [el("h2", { style: "margin:0", text: "Services & pricing" }),
              el("p", { class: "cf-muted", style: "margin:4px 0 0;font-size:.85rem", text: "Prices, payment, packages & commission live behind each — tap to edit; use the buttons to deactivate or terminate." })]),
            cfg.allowCreate ? el("button", { class: "cf-btn cf-btn-sm cf-btn-primary", text: "+ New", onclick: function () { if (cfg.onCreate) cfg.onCreate(state.kind); } }) : null,
          ].filter(Boolean)),
        ]));
        if (kinds.length > 1) host.appendChild(UI.subtabs(state.kind, kinds.map(function (k) { return [k, KIND_LABEL[k] || k]; }), function (k) { state.kind = k; draw(); }));
        host.appendChild(UI.lifecycleBar(state.life, function (f) { state.life = f; draw(); }));
        var shown = (state.services || []).filter(function (s) { return s.service_kind === state.kind && (state.life === "all" || (s.status || "active") === state.life); });
        if (!shown.length) { host.appendChild(el("div", { class: "cf-card cf-empty", text: "No " + (state.life === "all" ? "" : state.life + " ") + state.kind + " services." })); return; }
        shown.forEach(function (s) {
          function setStatus(ns) { window.TFAuth.apiJSON("/api/services/" + s.id, { method: "PATCH", body: { status: ns } }).then(function () { UI.toast("Updated.", "info"); load(); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); }
          var sub = [];
          if ((s.service_kind === "lesson" || s.service_kind === "class") && s.coach_name) sub.push("Coach: " + s.coach_name);
          var v = s.variations || [];
          sub.push(v.length ? v.slice(0, 4).map(function (x) { return x.duration_minutes ? (x.duration_minutes + " min " + money(x.amount_minor)) : money(x.amount_minor); }).join("  ·  ") : "No prices set yet");
          var main = el("div", { style: "cursor:pointer;flex:1" }, [
            el("div", { class: "cf-row", style: "gap:8px;align-items:center;flex-wrap:wrap" }, [el("span", { class: "cf-chip " + s.service_kind, text: s.service_kind }), el("strong", { text: s.name || "Service" }), (s.status && s.status !== "active") ? UI.statusChip(s.status) : null].filter(Boolean)),
            el("div", { class: "cf-muted", style: "font-size:.82rem;margin-top:5px", text: sub.join("  ·  ") + "  ·  Edit ›" }),
          ]);
          main.addEventListener("click", function () { window.ServiceEditor.open(s.id, { host: host, onClose: function () { load(); } }); });
          var acts = el("div", { class: "cf-row", style: "gap:6px;flex-wrap:wrap" }, UI.lifeActions(s.status || "active", setStatus, { terminateConfirm: "Terminate “" + (s.name || "this service") + "”? Kept for history, removed from use." }));
          host.appendChild(el("div", { class: "cf-card", style: "display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap" + ((s.status && s.status !== "active") ? ";opacity:.6" : "") }, [main, acts]));
        });
      }

      load();
      return { refresh: load };
    },
  };
})();

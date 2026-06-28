// settings.js — club Settings (docs/08 §1.7). The post-onboarding home where the
// owner edits everything. Same data + same API calls as the onboarding wizard,
// presented as editable tabs that reuse the AdminUI section components.
//
// Tabs: Club profile · Hours · Courts · Services & pricing · Coaches.
// Gated to club_admin / platform_admin via Portal.boot.
(function () {
  var UI, el;
  var state = { data: null, tab: "profile" };

  // Consolidated (2026-06-28): Hours folded into Courts ("Courts & hours" — everything court-
  // specific in one block); the global payment methods folded into Club profile (checkboxes).
  var TABS = [
    { k: "profile", t: "Club profile" },
    { k: "courts", t: "Courts & hours" },
    { k: "services", t: "Services" },
    { k: "pricing", t: "Memberships" },
    { k: "coaches", t: "Coaches" },
  ];

  function root() { return document.getElementById("cf-settings"); }

  function tabBar() {
    var nav = el("nav", { class: "cf-nav", style: "margin-bottom:16px" });
    TABS.forEach(function (tab) {
      var a = el("a", { href: "#" + tab.k, text: tab.t });
      if (tab.k === state.tab) a.classList.add("active");
      a.addEventListener("click", function (ev) { ev.preventDefault(); select(tab.k); });
      nav.appendChild(a);
    });
    return nav;
  }

  function select(k) {
    state.tab = k;
    try { history.replaceState(null, "", "#" + k); } catch (e) {}
    render();
  }

  function render() {
    var host = root(); UI.clear(host);
    host.appendChild(el("div", { class: "cf-card" }, [
      el("h2", { text: "Settings" }),
      el("p", { class: "cf-muted", text: "Edit your club setup. Changes save per section." }),
    ]));
    host.appendChild(tabBar());
    var sectionHost = el("div");
    host.appendChild(sectionHost);

    var d = state.data || {};
    if (state.tab === "profile") {
      window.AdminUI.clubProfile(sectionHost, d, { saveLabel: "Save changes" });
      renderPayments(sectionHost, (state.data && state.data.policy) || {});  // global payment methods
    } else if (state.tab === "courts") {
      window.AdminUI.courts(sectionHost, {});            // courts + …
      window.AdminUI.hours(sectionHost, d.hours || {}, { saveLabel: "Save hours" });  // …their hours, one block
    } else if (state.tab === "pricing") {
      // Memberships-as-services: each membership (tier) is one service with term variants inside it
      // (show → Edit). Court rates + packs live in the Service Editor (Services tab).
      window.AdminUI.membershipServices(sectionHost);
    } else if (state.tab === "services") {
      renderServices(sectionHost);          // unified service list → the ONE Service Editor
    } else if (state.tab === "coaches") {
      window.AdminUI.coachManage(sectionHost);   // merged Coaches + Coach pay (summary → edit)
    }
  }

  // The club's GLOBAL payment methods (what the whole club can offer). A service/coach/class then
  // chooses which of these enabled methods IT accepts (per-service preference). Three checkboxes,
  // each a club.policy flag → PATCH /api/admin/policy.
  function renderPayments(host, policy) {
    var card = el("div", { class: "cf-card" }, [
      el("h3", { text: "Payment methods" }),
      el("p", { class: "cf-muted", text: "Which ways the club accepts payment. Each service can then choose which of these it offers." }),
    ]);
    function flag(key, label, hint) {
      var lbl = el("label", { class: "cf-row", style: "cursor:pointer;gap:10px;align-items:flex-start;margin-top:8px" });
      var cb = el("input", { type: "checkbox" }); cb.checked = !!policy[key]; cb.style.width = "auto";
      cb.addEventListener("change", async function () {
        cb.disabled = true;
        var body = {}; body[key] = cb.checked;
        try {
          await window.AdminAPI.patchPolicy(body);
          if (state.data && state.data.policy) state.data.policy[key] = cb.checked;
          UI.toast("Saved.", "info");
        } catch (e) { cb.checked = !cb.checked; UI.toast(UI.errMsg(e), "error"); }
        finally { cb.disabled = false; }
      });
      lbl.appendChild(cb);
      lbl.appendChild(el("div", {}, [el("div", { style: "font-weight:600", text: label }), hint ? el("div", { class: "cf-muted cf-tiny", text: hint }) : null].filter(Boolean)));
      return lbl;
    }
    card.appendChild(flag("allow_online_payment", "Pay online (card)", "Yoco — card + Apple/Google/Samsung Pay."));
    card.appendChild(flag("allow_pay_at_court", "Pay at the club", "Settle at the front desk."));
    card.appendChild(flag("allow_monthly_account", "Monthly account (invoice in arrears)", "Charges accrue on a tab, invoiced monthly."));
    host.appendChild(card);
  }

  // Unified services — every service (court / lesson / class) as a summary card → "Manage" opens
  // the ONE Service Editor (prices · payment · packages · commission). The same editor the coach
  // uses; the owner additionally edits commission. One place, no duplication.
  function renderServices(host) {
    UI.clear(host);
    host.appendChild(el("div", { class: "cf-card" }, [el("div", { class: "cf-loading", text: "Loading services…" })]));
    window.TFAuth.apiJSON("/api/services").then(function (r) {
      var svcs = r.services || [];
      UI.clear(host);
      host.appendChild(el("div", { class: "cf-card" }, [
        el("h2", { text: "Services" }),
        el("p", { class: "cf-muted", text: "Courts, lessons and classes. Everything for a service — prices, payment, packages and commission — lives behind the block (click to edit)." }),
      ]));
      host.appendChild(UI.lifecycleBar(state.svcFilter || "active", function (f) { state.svcFilter = f; renderServices(host); }));
      var filter = state.svcFilter || "active";
      var shown = svcs.filter(function (s) { return filter === "all" || s.status === filter; });
      if (!shown.length) { host.appendChild(el("div", { class: "cf-card cf-empty", text: "No " + (filter === "all" ? "" : filter + " ") + "services." })); return; }
      shown.forEach(function (s) {
        var bits = [s.variation_count + " price" + (s.variation_count === 1 ? "" : "s")];
        if (s.from_amount_minor != null) bits.push("from " + UI.money(s.from_amount_minor));
        function setStatus(ns) { window.TFAuth.apiJSON("/api/services/" + s.id, { method: "PATCH", body: { status: ns } }).then(function () { renderServices(host); }, function (e) { UI.toast(UI.errMsg(e), "error"); }); }
        var cardEl = el("div", { class: "cf-card cf-pickable" }, [
          el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap" }, [
            el("div", {}, [
              el("div", { class: "cf-row", style: "gap:8px;align-items:center" }, [el("span", { class: "cf-chip " + s.service_kind, text: s.service_kind }), el("strong", { text: s.name || "Service" }), s.status !== "active" ? UI.statusChip(s.status) : null].filter(Boolean)),
              el("div", { class: "cf-muted cf-tiny", style: "margin-top:4px", text: bits.join(" · ") }),
            ]),
            el("div", { class: "cf-row", style: "gap:6px" }, UI.lifeActions(s.status, setStatus, { terminateConfirm: "Terminate “" + (s.name || "this service") + "”? Kept for history, removed from use." })),
          ]),
        ]);
        cardEl.addEventListener("click", function () { window.ServiceEditor.open(s.id, { host: host, onClose: function () { renderServices(host); } }); });
        if (s.status !== "active") cardEl.style.opacity = "0.6";
        host.appendChild(cardEl);
      });
    }, function (e) { UI.clear(host); host.appendChild(el("div", { class: "cf-card cf-empty", text: UI.errMsg(e) })); });
  }

  window.Settings = {
    start: async function (principal) {
      UI = window.UI; el = UI.el;
      var host = root();
      UI.clear(host); host.appendChild(el("div", { class: "cf-loading", text: "Loading settings…" }));
      try {
        state.data = await window.AdminAPI.onboarding();
      } catch (e) {
        state.data = {};
        UI.toast(UI.errMsg(e), "error");
      }
      var hash = (location.hash || "").replace("#", "");
      if (TABS.some(function (t) { return t.k === hash; })) state.tab = hash;
      render();
    },
  };
})();

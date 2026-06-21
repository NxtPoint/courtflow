// settings.js — club Settings (docs/08 §1.7). The post-onboarding home where the
// owner edits everything. Same data + same API calls as the onboarding wizard,
// presented as editable tabs that reuse the AdminUI section components.
//
// Tabs: Club profile · Hours · Courts · Services & pricing · Coaches.
// Gated to club_admin / platform_admin via Portal.boot.
(function () {
  var UI, el;
  var state = { data: null, tab: "profile" };

  var TABS = [
    { k: "profile", t: "Club profile" },
    { k: "hours", t: "Hours" },
    { k: "courts", t: "Courts" },
    { k: "services", t: "Services & pricing" },
    { k: "coaches", t: "Coaches" },
    { k: "coachpay", t: "Coach pay" },
    { k: "payments", t: "Payments" },
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
    } else if (state.tab === "hours") {
      window.AdminUI.hours(sectionHost, d.hours || {}, { saveLabel: "Save hours" });
    } else if (state.tab === "courts") {
      window.AdminUI.courts(sectionHost, {});
    } else if (state.tab === "services") {
      window.AdminUI.services(sectionHost, {});
    } else if (state.tab === "coaches") {
      window.AdminUI.coaches(sectionHost, {});
    } else if (state.tab === "coachpay") {
      window.AdminUI.coachAgreements(sectionHost, {});
    } else if (state.tab === "payments") {
      renderPayments(sectionHost, (state.data && state.data.policy) || {});
    }
  }

  // Per-club online-payments switch. The booking flow offers "Pay online" (Yoco) only when
  // this is on AND payments are globally enabled — so turning a club live is one toggle.
  function renderPayments(host, policy) {
    var card = el("div", { class: "cf-card" }, [
      el("h2", { text: "Online payments" }),
      el("p", { class: "cf-muted", text:
        "When on, members can pay for bookings online by card (Yoco — card + Apple/Google/Samsung Pay). " +
        "When off, they pay at the court or on a monthly account." }),
    ]);
    var lbl = el("label", { class: "cf-row", style: "cursor:pointer;gap:10px" });
    var cb = el("input", { type: "checkbox" });
    cb.checked = !!policy.allow_online_payment;
    cb.style.width = "auto";
    cb.addEventListener("change", async function () {
      cb.disabled = true;
      try {
        await window.AdminAPI.patchPolicy({ allow_online_payment: cb.checked });
        if (state.data && state.data.policy) state.data.policy.allow_online_payment = cb.checked;
        UI.toast(cb.checked ? "Online payments enabled." : "Online payments turned off.", "info");
      } catch (e) {
        cb.checked = !cb.checked;
        UI.toast(UI.errMsg(e), "error");
      } finally { cb.disabled = false; }
    });
    lbl.appendChild(cb);
    lbl.appendChild(el("span", { style: "font-weight:600", text: "Accept online card payments" }));
    card.appendChild(lbl);
    host.appendChild(card);

    // Configurable membership term plans (label + price + duration) live under Payments.
    var plansHost = el("div");
    host.appendChild(plansHost);
    window.AdminUI.membershipPlans(plansHost, {});

    // Configurable session packs (token bundles) — prepaid packs across court/lesson/class.
    var bundleHost = el("div");
    host.appendChild(bundleHost);
    window.AdminUI.bundlePlans(bundleHost, {});
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

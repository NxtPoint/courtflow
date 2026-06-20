// onboarding.js — first-run owner onboarding wizard (docs/08 §4).
//
// A 5-step guided setup that writes the same rows Settings later edits. Reuses the
// AdminUI section components (admin_api.js) so the wizard and Settings stay 1:1.
// Each step saves to the admin API and can be revisited; pre-filled from
// GET /api/admin/onboarding. Gated to club_admin / platform_admin via Portal.boot.
//
// Steps: 1 Club profile -> 2 Opening hours -> 3 Courts -> 4 Services & rates
//        -> 5 Invite coaches -> Done (POST /onboarding/complete) -> /portal.
(function () {
  var UI, el;
  var state = { step: 0, data: null };
  var LABELS = ["Profile", "Hours", "Courts", "Services", "Coaches"];

  function host() { return document.getElementById("cf-wizard"); }

  function stepsBar(active) {
    var wrap = el("div", { class: "cf-steps" });
    LABELS.forEach(function (l, i) {
      var s = el("div", {
        class: "cf-step" + (i === active ? " on" : ""),
        style: "cursor:pointer",
        onclick: function () { go(i); },
      }, [el("span", { class: "n", text: String(i + 1) }), el("span", { text: l })]);
      wrap.appendChild(s);
    });
    return wrap;
  }

  function frame(active, sectionHost) {
    var h = host(); UI.clear(h);
    h.appendChild(stepsBar(active));
    h.appendChild(sectionHost);
  }

  function backBtn() {
    return el("button", { class: "cf-btn", text: "← Back", onclick: function () { go(state.step - 1); } });
  }

  function go(i) {
    if (i < 0) i = 0;
    if (i > LABELS.length) i = LABELS.length;
    state.step = i;
    if (i >= LABELS.length) { renderDone(); return; }
    render();
  }

  function render() {
    var sectionHost = el("div");
    frame(state.step, sectionHost);
    var d = state.data || {};
    var advance = function () { go(state.step + 1); };

    if (state.step === 0) {
      window.AdminUI.clubProfile(sectionHost, d, {
        saveLabel: "Save & continue →", onSaved: advance,
      });
    } else if (state.step === 1) {
      window.AdminUI.hours(sectionHost, d.hours || {}, {
        saveLabel: "Save & continue →", before: [backBtn()], onSaved: advance,
      });
    } else if (state.step === 2) {
      window.AdminUI.courts(sectionHost, { before: [backBtn(), nextBtn()] });
    } else if (state.step === 3) {
      window.AdminUI.services(sectionHost, { before: [backBtn(), nextBtn()] });
    } else if (state.step === 4) {
      window.AdminUI.coaches(sectionHost, { before: [backBtn(), finishBtn()] });
    }
  }

  // For list-style steps (courts/services/coaches) the user adds rows inline, then
  // clicks Continue/Finish to advance — no single "save" gates the step.
  function nextBtn() {
    return el("button", { class: "cf-btn cf-btn-primary", text: "Continue →",
      onclick: function () { go(state.step + 1); } });
  }
  function finishBtn() {
    return el("button", { class: "cf-btn cf-btn-primary", text: "Finish setup →",
      onclick: function () { go(LABELS.length); } });
  }

  async function renderDone() {
    var h = host(); UI.clear(h);
    h.appendChild(stepsBar(LABELS.length - 1));
    var card = el("div", { class: "cf-card" }, [
      el("h2", { text: "Finishing up…" }),
      el("div", { class: "cf-loading", text: "Saving your setup…" }),
    ]);
    h.appendChild(card);
    try {
      await window.AdminAPI.completeOnboarding();
      UI.clear(card);
      card.appendChild(el("h2", { text: "✓ You're all set" }));
      card.appendChild(el("p", { text: "Your club is configured. You can change anything later in Settings." }));
      card.appendChild(el("div", { class: "cf-row", style: "margin-top:14px" }, [
        el("a", { class: "cf-btn cf-btn-primary", href: "/portal.html", text: "Go to dashboard" }),
        el("a", { class: "cf-btn", href: "/settings.html", text: "Open Settings" }),
      ]));
      setTimeout(function () { window.location.replace("/portal.html"); }, 1500);
    } catch (e) {
      UI.clear(card);
      card.appendChild(el("h2", { text: "Almost there" }));
      card.appendChild(el("p", { class: "cf-muted", text: UI.errMsg(e) }));
      card.appendChild(el("div", { class: "cf-row", style: "margin-top:14px" }, [
        backBtn(),
        el("button", { class: "cf-btn cf-btn-primary", text: "Retry", onclick: renderDone }),
      ]));
    }
  }

  window.Onboarding = {
    start: async function (principal) {
      UI = window.UI; el = UI.el;
      var h = host();
      UI.clear(h); h.appendChild(el("div", { class: "cf-loading", text: "Loading your setup…" }));
      try {
        state.data = await window.AdminAPI.onboarding();
      } catch (e) {
        state.data = {};
        UI.toast(UI.errMsg(e), "error");
      }
      // If already completed, drop straight into the dashboard.
      if (state.data && state.data.completed) { window.location.replace("/portal.html"); return; }
      state.step = 0;
      render();
    },
  };
})();

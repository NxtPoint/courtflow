// coach_onboarding.js — first-run coach onboarding wizard.
//
// A guided multi-step setup that writes the same rows the coach console later edits.
// Reuses the CoachUI section components (coach_api.js) so the wizard and the console
// "My profile" editor stay 1:1. Each step saves to the coach API and can be revisited;
// EVERY field pre-fills from GET /api/coach/onboarding MERGED with the full
// GET /api/coach/profile (so visibility/bookable/review/languages/quals also pre-fill).
// Gated to coach / club_admin / platform_admin via Portal.boot.
//
// Steps: 1 Profile -> 2 Working hours -> 3 Services & rates -> 4 Packs (optional)
//        -> Done (POST /coach/onboarding/complete) -> /coach.html.
(function () {
  var UI, el;
  var state = { step: 0, data: null };
  var LABELS = ["Profile", "Hours", "Services"];
  // Short, friendly explainer shown above each step.
  var HELP = [
    "Tell members who you are. A photo, headline and bio help them choose you. Toggle whether you’re taking bookings, whether you appear on the public site, and whether new bookings should wait for your approval.",
    "Set the hours you’re available to coach each day. Members can only book you inside these windows — without hours you’re invisible.",
    "Add the lessons you offer and a price for each length. One lesson can have several durations (e.g. 30 min and 60 min) — use “Add another duration”. Prepaid packs are added per lesson (open a lesson to add its packs). Settlement (pay at court, pay online, or use a pack) is chosen by the client at booking.",
  ];

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
    if (HELP[active]) {
      h.appendChild(el("p", { class: "cf-muted", style: "margin:4px 2px 14px", text: HELP[active] }));
    }
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
      window.CoachUI.profile(sectionHost, d.profile || {}, {
        saveLabel: "Save & continue →", onSaved: advance,
      });
    } else if (state.step === 1) {
      window.CoachUI.hours(sectionHost, d.hours || {}, {
        saveLabel: "Save & continue →", before: [backBtn()], onSaved: advance,
      });
    } else if (state.step === 2) {
      // Services is the FINAL step: add lessons + prices. Packs live under each lesson (added by
      // opening a lesson in the console) — no standalone packs step.
      window.CoachUI.services(sectionHost, { before: [backBtn(), finishBtn()] });
    }
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
      await window.CoachAPI.completeOnboarding();
      UI.clear(card);
      card.appendChild(el("h2", { text: "✓ You're all set" }));
      card.appendChild(el("p", { text: "Your coaching profile is live. You can change anything later from the Coach console." }));
      card.appendChild(el("div", { class: "cf-row", style: "margin-top:14px" }, [
        el("a", { class: "cf-btn cf-btn-primary", href: "/coach.html", text: "Go to Coach console" }),
        el("a", { class: "cf-btn", href: "/portal.html", text: "Dashboard" }),
      ]));
      setTimeout(function () { window.location.replace("/coach.html"); }, 1500);
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

  window.CoachOnboarding = {
    start: async function (principal) {
      UI = window.UI; el = UI.el;
      var h = host();
      UI.clear(h); h.appendChild(el("div", { class: "cf-loading", text: "Loading your setup…" }));
      try {
        state.data = await window.CoachAPI.onboarding();
        // The onboarding payload carries only a profile SUBSET. Merge the FULL profile so
        // languages/qualifications/years/visibility/bookable/review_bookings pre-fill too.
        try {
          var pr = await window.CoachAPI.profile();
          if (pr && pr.profile) {
            state.data.profile = Object.assign({}, state.data.profile || {}, pr.profile);
          }
        } catch (e2) { /* fall back to the onboarding subset */ }
      } catch (e) {
        state.data = {};
        UI.toast(UI.errMsg(e), "error");
      }
      // If already completed, drop straight into the coach console.
      if (state.data && state.data.completed) { window.location.replace("/coach.html"); return; }
      state.step = 0;
      render();
    },
  };
})();

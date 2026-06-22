// plan.js — the consolidated "Plan" page (client-journey redesign, Phase 1, increment 3).
//
// ONE surface for the three purchasing models (replaces the separate Membership + Packs pages):
//   • Membership — go unlimited on courts; pick a configured term plan (tier/duration in the label).
//   • Packs      — prepaid session packs (court/lesson/class); your live wallets shown up top.
//   • Pay as you go — no commitment; pay per booking (desk / monthly account / online).
//
// A status header shows the member's current standing (free-week countdown / member-until / PAYG).
// Trial members are shown the buyable plans (so they can choose what to keep after the free week),
// not an "active" card. Checkout reuses the EXACT existing contracts:
//   membership: POST /api/billing/membership/checkout {price_id} -> Pay.startYocoCheckout(order_id)
//   packs:      POST /api/billing/bundles/checkout {bundle_plan_id} -> Pay.startYocoCheckout(order_id)
// Self-contained over window.TFAuth (these billing routes aren't in api.js's wrappers), like
// membership.js / packs.js which this supersedes.
(function () {
  function auth() {
    if (!window.TFAuth) throw new Error("auth_client.js must load before plan.js");
    return window.TFAuth;
  }
  var UI = window.UI, el = function () { return UI.el.apply(UI, arguments); };
  function host() { return document.getElementById("cf-plan"); }

  var KIND_LABEL = { court: "Court sessions", lesson: "Lessons", class: "Classes" };
  function kindLabel(k) { return KIND_LABEL[k] || k; }

  function fmtDate(iso) {
    if (!iso) return "";
    try {
      return new Date(iso + "T00:00:00").toLocaleDateString("en-ZA",
        { day: "numeric", month: "long", year: "numeric", timeZone: UI.CLUB_TZ });
    } catch (e) { return iso; }
  }
  function planTerm(months) { var m = parseInt(months, 10) || 0; return m === 1 ? "1 month" : (m + " months"); }
  function packTerms(p) {
    var bits = [];
    bits.push(p.duration_minutes ? (p.duration_minutes + " min sessions") : "any duration");
    bits.push(p.validity_days ? ("valid " + p.validity_days + " days") : "no expiry");
    return bits.join(" · ");
  }

  var state = { tab: "membership", mem: {}, bundles: {}, wallets: [], plan: null, selMem: null, selPack: null };
  function isTrial() { return !!(state.plan && state.plan.is_trial); }
  function isRealMember() { return !!(state.mem && state.mem.active) && !isTrial(); }

  // ---- status header ---------------------------------------------------------
  function statusHeader() {
    var line, sub;
    if (isTrial()) {
      var n = state.plan.trial_days_left;
      line = "🎁 You're on your free week";
      sub = (n != null) ? (n + " day" + (n === 1 ? "" : "s") + " left — courts are free. Pick a plan below to keep playing after.")
                        : "Courts are free this week. Pick a plan below to keep playing after.";
    } else if (isRealMember()) {
      line = "⭐ Membership active";
      sub = state.mem.current_period_end ? ("Your courts are free until " + fmtDate(state.mem.current_period_end) + ".") : "Your court bookings are free.";
    } else {
      line = "Pay as you go";
      sub = "Pay per booking — or go unlimited / grab a pack below.";
    }
    return el("div", { class: "cf-card" }, [
      el("div", { style: "font-weight:800;font-size:1.05rem", text: line }),
      el("div", { class: "cf-muted", style: "margin-top:3px", text: sub }),
    ]);
  }

  // ---- segmented tabs --------------------------------------------------------
  function segment() {
    var tabs = [
      { k: "membership", t: "Membership" },
      { k: "packs", t: "Packs" },
      { k: "payg", t: "Pay as you go" },
    ];
    var seg = el("div", { class: "cf-segment" });
    tabs.forEach(function (t) {
      seg.appendChild(el("button", { type: "button", class: state.tab === t.k ? "on" : "",
        onclick: function () { state.tab = t.k; render(); } , text: t.t }));
    });
    return seg;
  }

  // ---- Membership panel ------------------------------------------------------
  function panelMembership() {
    var wrap = el("div", {});
    if (isRealMember()) {
      wrap.appendChild(el("div", { class: "cf-card" }, [
        el("div", { class: "cf-membership-badge cf-membership-badge-on", text: "Active" }),
        el("h2", { text: "Unlimited Courts" }),
        el("p", { class: "cf-membership-sub", text: state.mem.current_period_end
          ? ("Active until " + fmtDate(state.mem.current_period_end) + ".") : "Your membership is active." }),
        el("ul", { class: "cf-membership-perks" }, [
          el("li", { text: "Court bookings are free — covered by your membership." }),
          el("li", { text: "Book any available court at no charge." }),
        ]),
        el("p", { class: "cf-membership-note", text: state.mem.current_period_end
          ? ("Renew after " + fmtDate(state.mem.current_period_end) + " to keep your courts free.")
          : "Renew when it lapses to keep your courts free." }),
      ]));
      return wrap;
    }

    var plans = (state.mem.plans || []).filter(function (p) { return p.active !== false; });
    var canBuy = plans.length > 0 && state.mem.sold && state.mem.online_enabled;
    if (state.selMem == null && plans.length) state.selMem = plans[0].price_id;

    var card = el("div", { class: "cf-card" }, [
      el("h2", { text: "Go unlimited" }),
      el("p", { class: "cf-membership-sub", text: "Membership makes every court booking free. Pick the term that suits you — lessons and classes are charged as usual." }),
    ]);

    if (plans.length) {
      var opts = el("div", { class: "cf-planopts" });
      plans.forEach(function (p) {
        var sel = state.selMem === p.price_id;
        var card2 = el("div", { class: "cf-planopt" + (sel ? " sel" : ""), role: "button", tabindex: "0" }, [
          el("div", { class: "cf-planopt-name", text: p.label || planTerm(p.term_months) }),
          el("div", { class: "cf-planopt-price", text: UI.money(p.amount_minor, p.currency || state.mem.currency) }),
          el("div", { class: "cf-planopt-sub", text: "for " + planTerm(p.term_months) }),
          p.access_summary ? el("div", { class: "cf-planopt-meta", text: "🕐 " + p.access_summary }) : null,
        ].filter(Boolean));
        function pick() { state.selMem = p.price_id; render(); }
        card2.addEventListener("click", pick);
        card2.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } });
        opts.appendChild(card2);
      });
      card.appendChild(opts);
    }

    var btn = el("button", { class: "cf-btn cf-btn-primary cf-btn-lg cf-btn-block", text: isTrial() ? "Get this membership" : "Buy membership" });
    if (!canBuy) btn.disabled = true;
    else btn.addEventListener("click", function () { buyMembership(btn, state.selMem); });
    card.appendChild(btn);

    if (!state.mem.sold || !plans.length) card.appendChild(note("Membership isn't offered by your club yet."));
    else if (!state.mem.online_enabled) card.appendChild(note("Online payment isn't enabled yet — please contact the front desk."));
    else card.appendChild(note("Secure payment (card, Apple/Google Pay). Lasts for your chosen term; renew when it lapses."));
    wrap.appendChild(card);
    return wrap;
  }

  // ---- Packs panel -----------------------------------------------------------
  function panelPacks() {
    var wrap = el("div", {});

    // Your packs (wallets)
    if (state.wallets.length) {
      var wc = el("div", { class: "cf-card" }, [ el("h2", { text: "Your packs" }) ]);
      var list = el("div", { class: "cf-list" });
      state.wallets.forEach(function (w) {
        var left = (w.sessions_remaining != null) ? Math.round(w.sessions_remaining * 10) / 10 : w.tokens_remaining;
        var mins = (w.minutes_remaining != null) ? w.minutes_remaining : null;
        var sub = left + " of " + w.tokens_total + " sessions left";
        if (w.base_minutes && mins != null) sub += " (" + mins + " min)";
        if (w.expires_at) sub += " · expires " + fmtDate(w.expires_at);
        var exhausted = (mins != null ? mins <= 0 : w.tokens_remaining <= 0) || w.status !== "active";
        if (exhausted) sub += " · " + (w.status === "expired" ? "expired" : "used up");
        list.appendChild(el("div", { class: "cf-item" }, [
          el("span", { class: "cf-chip", text: kindLabel(w.service_kind) }),
          el("div", { class: "cf-item-main" }, [
            el("div", { class: "cf-item-t", text: w.label || kindLabel(w.service_kind) }),
            el("div", { class: "cf-item-s", text: sub }),
          ]),
          el("span", { class: "cf-chip" + (exhausted ? "" : " class"), text: String(left) }),
        ]));
      });
      wc.appendChild(list);
      wc.appendChild(note("Your pack applies automatically at booking — longer sessions simply use more of it. Cancel in time and it's credited straight back."));
      wrap.appendChild(wc);
    }

    // Buy a pack
    var plans = (state.bundles.plans || []).filter(function (p) { return p.active !== false; });
    var canBuy = plans.length > 0 && state.bundles.online_enabled;
    if (state.selPack == null && plans.length) state.selPack = plans[0].id;

    var card = el("div", { class: "cf-card" }, [
      el("h2", { text: "Buy a pack" }),
      el("p", { class: "cf-membership-sub", text: "Pay once for several sessions and draw them down as you book — courts, lessons or classes." }),
    ]);
    if (plans.length) {
      var opts = el("div", { class: "cf-planopts" });
      plans.forEach(function (p) {
        var sel = state.selPack === p.id;
        var c = el("div", { class: "cf-planopt" + (sel ? " sel" : ""), role: "button", tabindex: "0" }, [
          el("div", { class: "cf-chip", text: kindLabel(p.service_kind) }),
          el("div", { class: "cf-planopt-name", style: "margin-top:6px", text: p.label || (p.sessions_count + " sessions") }),
          el("div", { class: "cf-planopt-price", text: UI.money(p.price_minor, p.currency || state.bundles.currency) }),
          el("div", { class: "cf-planopt-sub", text: p.sessions_count + " sessions" }),
          el("div", { class: "cf-planopt-meta", text: packTerms(p) }),
        ]);
        function pick() { state.selPack = p.id; render(); }
        c.addEventListener("click", pick);
        c.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } });
        opts.appendChild(c);
      });
      card.appendChild(opts);
    }
    var btn = el("button", { class: "cf-btn cf-btn-primary cf-btn-lg cf-btn-block", text: "Buy pack" });
    if (!canBuy) btn.disabled = true;
    else btn.addEventListener("click", function () { buyPack(btn, state.selPack); });
    card.appendChild(btn);
    if (!plans.length) card.appendChild(note("Your club doesn't offer session packs yet."));
    else if (!state.bundles.online_enabled) card.appendChild(note("Online payment isn't enabled yet — please contact the front desk."));
    else card.appendChild(note("Secure payment (card, Apple/Google Pay). Sessions are added the moment payment completes."));
    wrap.appendChild(card);
    return wrap;
  }

  // ---- Pay-as-you-go panel ---------------------------------------------------
  function panelPayg() {
    return el("div", { class: "cf-card" }, [
      el("h2", { text: "Pay as you go" }),
      el("p", { class: "cf-membership-sub", text: "No commitment — just book and pay per session. You'll see the exact price as you book." }),
      el("ul", { class: "cf-membership-perks" }, [
        el("li", { text: "Pay online by card (Apple / Google Pay supported)." }),
        el("li", { text: "Or settle at the front desk." }),
        el("li", { text: "Members on a monthly account can charge it to their tab." }),
      ]),
      el("p", { class: "cf-membership-note", text: "Booking often? A membership makes courts free, and packs save on lessons & classes — see the other tabs." }),
      el("a", { class: "cf-btn cf-btn-primary cf-btn-lg", href: "/portal.html", text: "Book now" }),
    ]);
  }

  function note(t) { return el("p", { class: "cf-membership-note", text: t }); }

  // ---- checkout --------------------------------------------------------------
  function buyMembership(btn, priceId) {
    btn.disabled = true; var lbl = btn.textContent; btn.textContent = "Starting checkout…";
    auth().apiJSON("/api/billing/membership/checkout", { method: "POST", body: priceId ? { price_id: priceId } : {} })
      .then(function (res) { if (!res || !res.order_id) throw new Error("no order returned"); return window.Pay.startYocoCheckout(res.order_id); })
      .catch(function (e) { btn.disabled = false; btn.textContent = lbl; UI.toast(UI.errMsg(e) || "Could not start checkout.", "error"); });
  }
  function buyPack(btn, planId) {
    if (!planId) { UI.toast("Pick a pack first.", "error"); return; }
    btn.disabled = true; var lbl = btn.textContent; btn.textContent = "Starting checkout…";
    auth().apiJSON("/api/billing/bundles/checkout", { method: "POST", body: { bundle_plan_id: planId } })
      .then(function (res) { if (!res || !res.order_id) throw new Error("no order returned"); return window.Pay.startYocoCheckout(res.order_id); })
      .catch(function (e) { btn.disabled = false; btn.textContent = lbl; UI.toast(UI.errMsg(e) || "Could not start checkout.", "error"); });
  }

  // ---- render ----------------------------------------------------------------
  function render() {
    var h = host(); if (!h) return;
    UI.clear(h);
    h.appendChild(statusHeader());
    h.appendChild(segment());
    if (state.tab === "membership") h.appendChild(panelMembership());
    else if (state.tab === "packs") h.appendChild(panelPacks());
    else h.appendChild(panelPayg());
  }

  var Plan = {
    start: function () {
      var h = host(); if (h) { h.className = "cf-loading"; h.textContent = "Loading…"; }
      Promise.all([
        auth().apiJSON("/api/billing/membership/status").catch(function () { return {}; }),
        auth().apiJSON("/api/billing/bundles").catch(function () { return {}; }),
        auth().apiJSON("/api/billing/bundles/wallets").catch(function () { return { wallets: [] }; }),
        auth().apiJSON("/api/me/plan").catch(function () { return null; }),
      ]).then(function (out) {
        state.mem = out[0] || {}; state.bundles = out[1] || {};
        state.wallets = (out[2] && out[2].wallets) || []; state.plan = out[3];
        if (h) h.className = "";
        render();
      }).catch(function (e) {
        if (h) { UI.clear(h); h.appendChild(el("div", { class: "cf-card cf-empty", text: UI.errMsg(e) || "Could not load plans." })); }
      });
    },
  };
  window.Plan = Plan;
})();

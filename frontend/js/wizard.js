// wizard.js — the needs-based PLAN wizard (the "money moment"), front-end redesign 2026-06-28.
// Fires when a client has no coverage (free week over / membership expired / out of credits).
// Step 1: Pay-as-you-go vs Membership (PAYG nudged — it's the profitable, flexible default).
// Step 2: pick a PACK size (PAYG: 1/3/5/10 court sessions, prepaid + drawn down) OR a TERM (membership
//   tier — Student/Family/etc with per-month framing). Step 3: Yoco checkout.
// Self-contained over window.TFAuth + window.Pay (mirrors plan.js's exact checkout contracts).
(function () {
  var UI = window.UI, el = function () { return UI.el.apply(UI, arguments); };
  var data = { mem: {}, bundles: {}, plan: null, wallets: [] };
  var ui = { step: 1, choice: null, selMem: null, selPack: null, bg: null, loaded: false };

  function money(minor, ccy) { return UI.money(minor, ccy || "ZAR"); }
  function perMonth(amount, months) { var m = parseInt(months, 10) || 1; return Math.round(amount / m); }
  function termLabel(m) { m = parseInt(m, 10) || 0; return m === 1 ? "1 month" : m + " months"; }
  function courtPacks() { return (data.bundles.plans || []).filter(function (p) { return p.service_kind === "court" && p.active !== false; }).sort(function (a, b) { return a.sessions_count - b.sessions_count; }); }
  function memPlans() { return (data.mem.plans || []).filter(function (p) { return p.active !== false; }); }
  function hasActiveCourtWallet() {
    return (data.wallets || []).some(function (w) { return w.service_kind === "court" && w.status === "active" && (w.minutes_remaining == null || w.minutes_remaining > 0); });
  }

  // ---- load (once per open) --------------------------------------------------
  async function load() {
    var out = await Promise.all([
      window.TFAuth.apiJSON("/api/billing/membership/status").catch(function () { return {}; }),
      window.TFAuth.apiJSON("/api/billing/bundles").catch(function () { return {}; }),
      window.TFAuth.apiJSON("/api/billing/bundles/wallets").catch(function () { return { wallets: [] }; }),
      window.TFAuth.apiJSON("/api/me/plan").catch(function () { return null; }),
    ]);
    data.mem = out[0] || {}; data.bundles = out[1] || {};
    data.wallets = (out[2] && out[2].wallets) || []; data.plan = out[3];
    ui.loaded = true;
  }

  // ---- shell -----------------------------------------------------------------
  function open(opts) {
    opts = opts || {};
    ui.step = 1; ui.choice = null; ui.selMem = null; ui.selPack = null;
    if (ui.bg) close();
    var bg = el("div", { class: "cf-modal-bg" });
    var modal = el("div", { class: "cf-modal cf-modal-lg", id: "cf-wiz" }, [el("div", { class: "cf-loading", text: "Loading your options…" })]);
    bg.appendChild(modal);
    document.body.appendChild(bg);
    ui.bg = bg;
    (ui.loaded ? Promise.resolve() : load()).then(render).catch(function (e) {
      modal.innerHTML = ""; modal.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) || "Could not load plans." }));
      modal.appendChild(closeRow());
    });
  }
  function close() { if (ui.bg && ui.bg.parentNode) document.body.removeChild(ui.bg); ui.bg = null; }
  function closeRow() {
    return el("div", { class: "cf-row", style: "justify-content:flex-end;margin-top:14px" }, [el("button", { class: "cf-btn", text: "Close", onclick: close })]);
  }

  function header(title, sub) {
    return el("div", {}, [
      el("div", { class: "cf-row", style: "justify-content:space-between;align-items:flex-start" }, [
        el("h2", { text: title }),
        el("button", { class: "cf-btn cf-btn-sm", text: "✕", title: "Close", onclick: close }),
      ]),
      sub ? el("p", { class: "cf-muted", style: "margin-top:2px", text: sub }) : null,
    ].filter(Boolean));
  }

  // ---- step 1: pay-as-you-go vs membership -----------------------------------
  function renderChoose(modal) {
    modal.appendChild(header("How would you like to pay?", "Two ways to play — pick what suits you. You can change any time."));
    var grid = el("div", { class: "cf-qb", style: "margin-top:14px" });

    // PAYG — nudged (most flexible / pay only for what you use)
    grid.appendChild(choiceCard("payg", "🎾", "Pay as you go", "Buy court sessions upfront and draw them down as you book — from R150 a session. No monthly commitment.", "Most flexible"));
    // Membership
    grid.appendChild(choiceCard("membership", "⭐", "Membership", "Unlimited courts for a fixed term — play as much as you want. Best if you play often.", null));

    modal.appendChild(grid);
  }
  function choiceCard(key, ic, t, s, ribbon) {
    var card = el("button", { class: "cf-qb-btn", type: "button", style: "align-items:flex-start;text-align:left",
      onclick: function () { ui.choice = key; ui.step = 2; render(); } }, [
      el("span", { class: "cf-qb-ic", text: ic }),
      el("span", {}, [
        el("span", { class: "cf-qb-t", text: t, style: "display:block" }),
        el("span", { class: "cf-qb-s", text: s }),
        ribbon ? el("span", { class: "cf-chip class", style: "margin-top:8px;display:inline-block", text: ribbon }) : null,
      ].filter(Boolean)),
    ]);
    return card;
  }

  // ---- step 2a: PAYG packs ----------------------------------------------------
  function renderPacks(modal) {
    modal.appendChild(header("Pay as you go", "Buy a few sessions upfront and draw them down — longer bookings simply use a bit more. Cancel in time and it's credited straight back."));
    var packs = courtPacks();
    if (!packs.length) {
      modal.appendChild(el("div", { class: "cf-empty", style: "margin-top:12px", text: "No session packs are set up yet. You can still just book and pay per session." }));
      modal.appendChild(navRow(true, false));
      return;
    }
    if (ui.selPack == null) ui.selPack = packs[0].id;
    var opts = el("div", { class: "cf-planopts", style: "margin-top:14px" });
    packs.forEach(function (p) {
      var per = p.sessions_count ? Math.round(p.price_minor / p.sessions_count) : p.price_minor;
      var sel = ui.selPack === p.id;
      var c = el("div", { class: "cf-planopt" + (sel ? " sel" : ""), role: "button", tabindex: "0" }, [
        el("div", { class: "cf-planopt-name", text: p.label || (p.sessions_count + " sessions") }),
        el("div", { class: "cf-planopt-price", text: money(p.price_minor, p.currency || data.bundles.currency) }),
        el("div", { class: "cf-planopt-sub", text: p.sessions_count + " session" + (p.sessions_count === 1 ? "" : "s") + " · " + money(per, p.currency || data.bundles.currency) + " each" }),
        p.validity_days ? el("div", { class: "cf-planopt-meta", text: "valid " + p.validity_days + " days" }) : el("div", { class: "cf-planopt-meta", text: "no expiry" }),
      ]);
      function pick() { ui.selPack = p.id; render(); }
      c.addEventListener("click", pick);
      c.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } });
      opts.appendChild(c);
    });
    modal.appendChild(opts);
    if (!data.bundles.online_enabled) modal.appendChild(el("p", { class: "cf-membership-note", text: "Online payment isn't enabled yet — please contact the front desk." }));
    modal.appendChild(navRow(true, data.bundles.online_enabled, "Buy & pay", function (btn) { buyPack(btn, ui.selPack); }));
    modal.appendChild(el("p", { class: "cf-muted cf-tiny", style: "text-align:center;margin-top:8px" }, [
      el("a", { href: "#", text: "or just pay each time you book", onclick: function (ev) { ev.preventDefault(); close(); window.location.href = "/book/court"; } }),
    ]));
  }

  // ---- step 2b: membership tiers ---------------------------------------------
  function renderMembership(modal) {
    modal.appendChild(header("Membership", "Courts free for your whole term — play as much as you like. Pick the tier that fits."));
    var plans = memPlans();
    if (!plans.length) { modal.appendChild(el("div", { class: "cf-empty", style: "margin-top:12px", text: "Your club doesn't offer memberships yet." })); modal.appendChild(navRow(true, false)); return; }
    if (ui.selMem == null) ui.selMem = plans[0].price_id;
    var opts = el("div", { class: "cf-planopts", style: "margin-top:14px" });
    plans.forEach(function (p) {
      var sel = ui.selMem === p.price_id;
      var pm = perMonth(p.amount_minor, p.term_months);
      var c = el("div", { class: "cf-planopt" + (sel ? " sel" : ""), role: "button", tabindex: "0" }, [
        el("div", { class: "cf-planopt-name", text: p.label || termLabel(p.term_months) }),
        el("div", { class: "cf-planopt-price", text: money(p.amount_minor, p.currency || data.mem.currency) }),
        el("div", { class: "cf-planopt-sub", text: "once-off · " + termLabel(p.term_months) + " access (" + money(pm, p.currency || data.mem.currency) + "/mo)" }),
        p.access_summary ? el("div", { class: "cf-planopt-meta", text: "🕐 " + p.access_summary }) : null,
      ].filter(Boolean));
      function pick() { ui.selMem = p.price_id; render(); }
      c.addEventListener("click", pick);
      c.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } });
      opts.appendChild(c);
    });
    modal.appendChild(opts);
    if (!data.mem.online_enabled) modal.appendChild(el("p", { class: "cf-membership-note", text: "Online payment isn't enabled yet — please contact the front desk." }));
    modal.appendChild(navRow(true, data.mem.online_enabled, "Buy membership", function (btn) { buyMembership(btn, ui.selMem); }));
  }

  // back / primary action row
  function navRow(showBack, canBuy, ctaText, onBuy) {
    var kids = [];
    if (showBack) kids.push(el("button", { class: "cf-btn", text: "← Back", onclick: function () { ui.step = 1; render(); } }));
    kids.push(el("span", { class: "cf-spacer" }));
    if (ctaText) {
      var btn = el("button", { class: "cf-btn cf-btn-primary cf-btn-lg", text: ctaText });
      if (!canBuy) btn.disabled = true; else btn.addEventListener("click", function () { onBuy(btn); });
      kids.push(btn);
    }
    return el("div", { class: "cf-row", style: "margin-top:18px;align-items:center" }, kids);
  }

  // ---- checkout --------------------------------------------------------------
  function buyMembership(btn, priceId) {
    btn.disabled = true; var lbl = btn.textContent; btn.textContent = "Starting checkout…";
    window.TFAuth.apiJSON("/api/billing/membership/checkout", { method: "POST", body: priceId ? { price_id: priceId } : {} })
      .then(function (res) { if (!res || !res.order_id) throw new Error("no order returned"); return window.Pay.startYocoCheckout(res.order_id); })
      .catch(function (e) { btn.disabled = false; btn.textContent = lbl; UI.toast(UI.errMsg(e) || "Could not start checkout.", "error"); });
  }
  function buyPack(btn, planId) {
    if (!planId) { UI.toast("Pick a pack first.", "error"); return; }
    btn.disabled = true; var lbl = btn.textContent; btn.textContent = "Starting checkout…";
    window.TFAuth.apiJSON("/api/billing/bundles/checkout", { method: "POST", body: { bundle_plan_id: planId } })
      .then(function (res) { if (!res || !res.order_id) throw new Error("no order returned"); return window.Pay.startYocoCheckout(res.order_id); })
      .catch(function (e) { btn.disabled = false; btn.textContent = lbl; UI.toast(UI.errMsg(e) || "Could not start checkout.", "error"); });
  }

  // ---- render ----------------------------------------------------------------
  function render() {
    var modal = document.getElementById("cf-wiz"); if (!modal) return;
    modal.innerHTML = "";
    if (ui.step === 1) renderChoose(modal);
    else if (ui.choice === "payg") renderPacks(modal);
    else renderMembership(modal);
  }

  // Auto-open when a client has NO coverage and NO credits (free week over / membership expired /
  // out of sessions). Respects a per-session dismissal so it doesn't nag after they close it.
  function maybeAutoOpen(fin, wallets) {
    try {
      if (sessionStorage.getItem("cf_wizard_dismissed") === "1") return false;
    } catch (e) {}
    var plan = (fin && fin.plan) || null;
    var covered = plan && plan.active;                              // membership or live free week
    var hasCredits = (wallets || []).some(function (w) { return w.service_kind === "court" && w.status === "active" && (w.minutes_remaining == null || w.minutes_remaining > 0); });
    if (covered || hasCredits) return false;
    open({ auto: true });
    return true;
  }

  // remember dismissal for the session (so an auto-open doesn't immediately reappear)
  var _close = close;
  close = function () { try { sessionStorage.setItem("cf_wizard_dismissed", "1"); } catch (e) {} _close(); };

  window.PlanWizard = { open: open, close: close, maybeAutoOpen: maybeAutoOpen };
})();

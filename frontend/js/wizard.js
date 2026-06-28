// wizard.js — the needs-based PLAN wizard (the "money moment"), front-end redesign 2026-06-28.
// ONE page, build-up-the-quote drill-down — fully driven off the club's configured catalogue
// (golden rule: only ever shows what the owner created in Settings → Pricing):
//   1. Pay as you go  vs  Membership
//   2. service TYPE      — PAYG: session length (billing.bundle_plan.duration_minutes)
//                          Membership: tier (billing.price.label on the membership product)
//   3. the NUMBER/TERM   — PAYG: #sessions (bundle_plan.sessions_count)
//                          Membership: term (price.term_months)
// A live quote updates as they pick; one "Pay" button routes to Yoco. Fires when a client has no
// coverage + no credits (free week over / membership expired / out of sessions). Self-contained over
// window.TFAuth + window.Pay (mirrors plan.js's exact checkout contracts).
(function () {
  var UI = window.UI, el = function () { return UI.el.apply(UI, arguments); };
  var data = { mem: {}, bundles: {}, plan: null, wallets: [] };
  var ui = { mode: "payg", paygDur: null, selPackId: null, memTier: null, selPriceId: null, bg: null, loaded: false };

  // ---- formatting ------------------------------------------------------------
  function money(minor, ccy) { return UI.money(minor, ccy || "ZAR"); }
  function perMonth(amount, months) { var m = parseInt(months, 10) || 1; return Math.round(amount / m); }
  function termLabel(m) { m = parseInt(m, 10) || 0; return m === 1 ? "1 month" : m + " months"; }
  function durLabel(d) { return d ? (d + " min") : "Any length"; }

  // ---- catalogue (ONLY what's configured) ------------------------------------
  function courtPacks() { return (data.bundles.plans || []).filter(function (p) { return p.service_kind === "court" && p.active !== false; }); }
  function memPlans() { return (data.mem.plans || []).filter(function (p) { return p.active !== false; }); }
  function paygDurations() {
    var durs = []; courtPacks().forEach(function (p) { var d = p.duration_minutes || 0; if (durs.indexOf(d) < 0) durs.push(d); });
    return durs.sort(function (a, b) { return a - b; });
  }
  function packsForDur(d) { return courtPacks().filter(function (p) { return (p.duration_minutes || 0) === d; }).sort(function (a, b) { return a.sessions_count - b.sessions_count; }); }
  // tier = the explicit membership_tier (admin field); falls back to the label when unset.
  function tierOf(p) { return p.tier || p.label || termLabel(p.term_months); }
  function memTiers() {
    var t = []; memPlans().forEach(function (p) { var l = tierOf(p); if (t.indexOf(l) < 0) t.push(l); });
    return t;
  }
  function plansForTier(l) { return memPlans().filter(function (p) { return tierOf(p) === l; }).sort(function (a, b) { return (a.term_months || 0) - (b.term_months || 0); }); }
  // When the owner uses named tiers (memberships-as-services), drill tier → term: the tiers are the
  // catalogue (Adult Anytime / Off-peak / Junior / Family), then the period. Only fall back to a flat
  // plan list when no plan carries an explicit tier (legacy term-labelled defaults).
  function memMultiTier() { return memPlans().some(function (p) { return !!p.tier; }); }
  function selectedPack() { return courtPacks().filter(function (p) { return p.id === ui.selPackId; })[0] || null; }
  function selectedPlan() { return memPlans().filter(function (p) { return p.price_id === ui.selPriceId; })[0] || null; }

  // pre-select sensible defaults (first type, first option) so the quote shows immediately
  function ensureDefaults() {
    if (ui.mode === "payg") {
      var durs = paygDurations();
      if (durs.length && (ui.paygDur == null || durs.indexOf(ui.paygDur) < 0)) ui.paygDur = durs[0];
      var packs = ui.paygDur != null ? packsForDur(ui.paygDur) : [];
      if (packs.length && !packs.some(function (p) { return p.id === ui.selPackId; })) ui.selPackId = packs[0].id;
    } else if (memMultiTier()) {
      var tiers = memTiers();
      if (ui.memTier == null || tiers.indexOf(ui.memTier) < 0) ui.memTier = tiers[0];
      var plans = plansForTier(ui.memTier);
      if (plans.length && !plans.some(function (p) { return p.price_id === ui.selPriceId; })) ui.selPriceId = plans[0].price_id;
    } else {
      ui.memTier = null;
      var all = memPlans();
      if (all.length && !all.some(function (p) { return p.price_id === ui.selPriceId; })) ui.selPriceId = all[0].price_id;
    }
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
  function open() {
    ui.mode = "payg"; ui.paygDur = null; ui.selPackId = null; ui.memTier = null; ui.selPriceId = null;
    if (ui.bg) _close();
    var bg = el("div", { class: "cf-modal-bg" });
    bg.appendChild(el("div", { class: "cf-modal cf-modal-lg", id: "cf-wiz" }, [el("div", { class: "cf-loading", text: "Loading your options…" })]));
    document.body.appendChild(bg);
    ui.bg = bg;
    (ui.loaded ? Promise.resolve() : load()).then(render).catch(function (e) {
      var m = document.getElementById("cf-wiz"); if (!m) return;
      m.innerHTML = ""; m.appendChild(el("div", { class: "cf-empty", text: UI.errMsg(e) || "Could not load plans." }));
    });
  }
  function _close() { if (ui.bg && ui.bg.parentNode) document.body.removeChild(ui.bg); ui.bg = null; }
  function close() { try { sessionStorage.setItem("cf_wizard_dismissed", "1"); } catch (e) {} _close(); }

  function header() {
    return el("div", { class: "cf-row", style: "justify-content:space-between;align-items:flex-start" }, [
      el("div", {}, [
        el("h2", { text: "Choose how you'd like to pay" }),
        el("p", { class: "cf-muted", style: "margin-top:2px", text: "Pick what fits — your price updates as you go." }),
      ]),
      el("button", { class: "cf-btn cf-btn-sm", text: "✕", title: "Close", onclick: close }),
    ]);
  }

  function modeSegment() {
    var seg = el("div", { class: "cf-segment", style: "margin-top:14px" });
    [["payg", "Pay as you go"], ["membership", "Membership"]].forEach(function (m) {
      seg.appendChild(el("button", { type: "button", class: ui.mode === m[0] ? "on" : "", text: m[1],
        onclick: function () { ui.mode = m[0]; render(); } }));
    });
    return seg;
  }

  function chipRow(items, isOn, onPick) {
    var row = el("div", { class: "cf-row", style: "flex-wrap:wrap;gap:8px;margin-bottom:14px" });
    items.forEach(function (it) {
      row.appendChild(el("button", { type: "button", class: "cf-durchip" + (isOn(it) ? " sel" : ""), onclick: function () { onPick(it); } }, [el("span", { text: it.label })]));
    });
    return row;
  }

  function optionCard(name, price, sub, meta, on, onPick) {
    var c = el("div", { class: "cf-planopt" + (on ? " sel" : ""), role: "button", tabindex: "0" }, [
      el("div", { class: "cf-planopt-name", text: name }),
      el("div", { class: "cf-planopt-price", text: price }),
      el("div", { class: "cf-planopt-sub", text: sub }),
      meta ? el("div", { class: "cf-planopt-meta", text: meta }) : null,
    ].filter(Boolean));
    c.addEventListener("click", onPick);
    c.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); onPick(); } });
    return c;
  }

  // ---- PAYG: session length (type) → #sessions (number) ----------------------
  function renderPayg(body) {
    var packs = courtPacks();
    if (!packs.length) {
      body.appendChild(el("div", { class: "cf-empty", text: "No session packs are set up by your club yet." }));
      body.appendChild(el("p", { class: "cf-muted cf-tiny", style: "text-align:center;margin-top:8px" }, [
        el("a", { href: "#", text: "Just pay each time you book →", onclick: function (ev) { ev.preventDefault(); close(); window.location.href = "/book/court"; } })]));
      return;
    }
    var durs = paygDurations();
    if (durs.length > 1) {
      body.appendChild(el("div", { class: "cf-pref-h", text: "Session length" }));
      body.appendChild(chipRow(durs.map(function (d) { return { d: d, label: durLabel(d) }; }),
        function (it) { return ui.paygDur === it.d; },
        function (it) { ui.paygDur = it.d; ui.selPackId = null; render(); }));
    }
    body.appendChild(el("div", { class: "cf-pref-h", text: "How many sessions" }));
    var opts = el("div", { class: "cf-planopts" });
    packsForDur(ui.paygDur).forEach(function (p) {
      var per = p.sessions_count ? Math.round(p.price_minor / p.sessions_count) : p.price_minor;
      opts.appendChild(optionCard(
        (p.sessions_count + " session" + (p.sessions_count === 1 ? "" : "s")),
        money(p.price_minor, p.currency || data.bundles.currency),
        money(per, p.currency || data.bundles.currency) + " each",
        p.validity_days ? ("valid " + p.validity_days + " days") : "no expiry",
        ui.selPackId === p.id, function () { ui.selPackId = p.id; render(); }));
    });
    body.appendChild(opts);
  }

  // ---- Membership: tier (type) → term --------------------------------------
  function renderMembership(body) {
    var plans = memPlans();
    if (!plans.length) { body.appendChild(el("div", { class: "cf-empty", text: "Your club doesn't offer memberships yet." })); return; }
    var multi = memMultiTier();
    var cards;
    if (multi) {
      var tiers = memTiers();
      body.appendChild(el("div", { class: "cf-pref-h", text: "Membership" }));
      body.appendChild(chipRow(tiers.map(function (l) { return { l: l, label: l }; }),
        function (it) { return ui.memTier === it.l; },
        function (it) { ui.memTier = it.l; ui.selPriceId = null; render(); }));
      body.appendChild(el("div", { class: "cf-pref-h", text: "Term" }));
      cards = plansForTier(ui.memTier);
    } else {
      body.appendChild(el("div", { class: "cf-pref-h", text: "Choose your membership" }));
      cards = plans;
    }
    var opts = el("div", { class: "cf-planopts" });
    cards.forEach(function (p) {
      opts.appendChild(optionCard(
        multi ? termLabel(p.term_months) : (p.label || termLabel(p.term_months)),
        money(p.amount_minor, p.currency || data.mem.currency),
        (multi ? "" : termLabel(p.term_months) + " · ") + money(perMonth(p.amount_minor, p.term_months), p.currency || data.mem.currency) + "/mo",
        p.access_summary ? ("🕐 " + p.access_summary) : null,
        ui.selPriceId === p.price_id, function () { ui.selPriceId = p.price_id; render(); }));
    });
    body.appendChild(opts);
  }

  // ---- live quote + pay ------------------------------------------------------
  function quoteBar() {
    var line, total = null, onBuy = null, buyable = false, note = null;
    var chooserHost = el("div", { style: "margin-top:6px" });   // membership pay-mode chooser renders here
    if (ui.mode === "payg") {
      var p = selectedPack();
      var online = data.bundles.online_enabled;
      if (p) {
        line = "Pay as you go · " + durLabel(p.duration_minutes || 0) + " · " + p.sessions_count + " session" + (p.sessions_count === 1 ? "" : "s") + " (drawn down as you book)";
        total = money(p.price_minor, p.currency || data.bundles.currency);
        buyable = !!online;
        if (!online) note = "Online payment isn't enabled yet — please contact the front desk.";
        onBuy = function (btn) { buyPack(btn, p.id); };
      } else line = "Pick a pack above to see your price.";
    } else {
      var pl = selectedPlan();
      var memModes = data.mem.allowed_payment_modes || [];
      if (pl) {
        line = "Membership · " + (pl.label || termLabel(pl.term_months)) + " · " + termLabel(pl.term_months) + " (courts free for the term)";
        total = money(pl.amount_minor, pl.currency || data.mem.currency);
        buyable = memModes.length > 0;
        if (!buyable) note = "This membership can't be purchased online yet — please contact the front desk.";
        onBuy = function (btn) { buyMembership(btn, pl.price_id, memModes, chooserHost); };
      } else line = "Pick a plan above to see your price.";
    }
    var btn = el("button", { class: "cf-btn cf-btn-primary cf-btn-lg", text: total ? ("Pay " + total) : "Pay" });
    if (!total || !buyable) btn.disabled = true; else btn.addEventListener("click", function () { onBuy(btn); });
    var wrap = el("div", { class: "cf-card", style: "margin-top:18px;background:var(--green-050);border-color:var(--green)" }, [
      el("div", { class: "cf-row", style: "justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px" }, [
        el("div", {}, [
          el("div", { style: "font-weight:800;font-size:1.15rem", text: total ? total : "Build your plan" }),
          el("div", { class: "cf-muted cf-tiny", text: line }),
        ]),
        btn,
      ]),
    ]);
    if (note) wrap.appendChild(el("p", { class: "cf-membership-note", text: note }));
    wrap.appendChild(chooserHost);
    return wrap;
  }

  // ---- checkout --------------------------------------------------------------
  // Membership purchase via the shared rule (Pay.buyMembership): online → Yoco; a single non-online
  // mode → immediate activation (no payment screen); multiple modes → a chooser in `host`.
  function buyMembership(btn, priceId, modes, host) {
    window.Pay.buyMembership({
      priceId: priceId, modes: modes, host: host,
      onActivated: function () { membershipActivated(); },
      onError: function (e) { UI.toast(UI.errMsg(e) || "Could not complete.", "error"); },
    });
  }
  function membershipActivated() {
    var m = document.getElementById("cf-wiz");
    if (m) {
      m.innerHTML = "";
      m.appendChild(el("div", { class: "cf-card", style: "text-align:center;padding:30px" }, [
        el("div", { style: "font-size:2.2rem", text: "🎾" }),
        el("h2", { text: "You're a member!" }),
        el("p", { class: "cf-muted", text: "Courts are free for your term. Please settle at the front desk. Reloading your options…" }),
      ]));
    }
    setTimeout(function () { try { ui.loaded = false; } catch (e) {} location.reload(); }, 1600);
  }
  function buyPack(btn, planId) {
    if (!planId) { UI.toast("Pick a pack first.", "error"); return; }
    btn.disabled = true; var lbl = btn.textContent; btn.textContent = "Starting checkout…";
    window.TFAuth.apiJSON("/api/billing/bundles/checkout", { method: "POST", body: { bundle_plan_id: planId } })
      .then(function (res) { if (!res || !res.order_id) throw new Error("no order returned"); return window.Pay.startYocoCheckout(res.order_id); })
      .catch(function (e) { btn.disabled = false; btn.textContent = lbl; UI.toast(UI.errMsg(e) || "Could not start checkout.", "error"); });
  }

  function render() {
    var modal = document.getElementById("cf-wiz"); if (!modal) return;
    ensureDefaults();
    modal.innerHTML = "";
    modal.appendChild(header());
    modal.appendChild(modeSegment());
    var body = el("div", { style: "margin-top:16px" });
    if (ui.mode === "payg") renderPayg(body); else renderMembership(body);
    modal.appendChild(body);
    modal.appendChild(quoteBar());
  }

  // Auto-open when a client has NO coverage and NO credits. Session-dismissible so it doesn't nag.
  function maybeAutoOpen(fin, wallets) {
    try { if (sessionStorage.getItem("cf_wizard_dismissed") === "1") return false; } catch (e) {}
    var plan = (fin && fin.plan) || null;
    var covered = plan && plan.active;
    var hasCredits = (wallets || []).some(function (w) { return w.service_kind === "court" && w.status === "active" && (w.minutes_remaining == null || w.minutes_remaining > 0); });
    if (covered || hasCredits) return false;
    open();
    return true;
  }

  window.PlanWizard = { open: open, close: close, maybeAutoOpen: maybeAutoOpen };
})();

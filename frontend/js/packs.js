// packs.js — the member-facing Session packs page (prepaid token bundles, docs/specs/02).
//
// The club configures PACKS (service · #sessions · duration · price · validity · optional coach).
// The member sees:
//   1. "Your packs" — wallets they already hold (tokens remaining + expiry).
//   2. "Buy a pack" — the active plans as selectable cards; choosing one + Buy ->
//      POST /api/billing/bundles/checkout {bundle_plan_id} -> Pay.startYocoCheckout(order_id).
// On return, the Yoco webhook activates the wallet (grants tokens); this page then shows them.
//
// Self-contained over window.TFAuth (auth_client.js), mirroring membership.js — the bundle routes
// aren't in api.js's wrappers.
(function () {
  function auth() {
    if (!window.TFAuth) throw new Error("auth_client.js must load before packs.js");
    return window.TFAuth;
  }
  var UI = window.UI, el = function () { return UI.el.apply(UI, arguments); };

  function host() { return document.getElementById("cf-packs"); }

  var KIND_LABEL = { court: "Court sessions", lesson: "Lessons", class: "Classes" };
  function kindLabel(k) { return KIND_LABEL[k] || k; }

  function fmtDate(iso) {
    if (!iso) return "";
    try {
      return new Date(iso + "T00:00:00").toLocaleDateString("en-ZA",
        { day: "numeric", month: "long", year: "numeric", timeZone: UI.CLUB_TZ });
    } catch (e) { return iso; }
  }

  function packTerms(p) {
    var bits = [];
    if (p.duration_minutes) bits.push(p.duration_minutes + " min sessions");
    else bits.push("any duration");
    if (p.validity_days) bits.push("valid " + p.validity_days + " days");
    else bits.push("no expiry");
    return bits.join(" · ");
  }

  // ---- "Your packs" (wallets) -------------------------------------------------
  function walletCard(w) {
    // Sessions can be fractional now (a pack covers any duration): "4.5 of 10 left".
    var left = (w.sessions_remaining != null) ? Math.round(w.sessions_remaining * 10) / 10 : w.tokens_remaining;
    var minsLeft = (w.minutes_remaining != null) ? w.minutes_remaining : null;
    var sub = left + " of " + w.tokens_total + " sessions left";
    if (w.base_minutes && minsLeft != null) sub += " (" + minsLeft + " min)";
    if (w.expires_at) sub += " · expires " + fmtDate(w.expires_at);
    var exhausted = (minsLeft != null ? minsLeft <= 0 : w.tokens_remaining <= 0) || w.status !== "active";
    return el("div", { class: "cf-item" }, [
      el("span", { class: "cf-chip", text: kindLabel(w.service_kind) }),
      el("div", { class: "cf-item-main" }, [
        el("div", { class: "cf-item-t", text: w.label || kindLabel(w.service_kind) }),
        el("div", { class: "cf-item-s", text: sub + (exhausted ? " · " + (w.status === "expired" ? "expired" : "used up") : "") }),
      ]),
      el("span", { class: "cf-chip" + (exhausted ? "" : " class"), text: String(left) }),
    ]);
  }

  function renderWallets(wallets) {
    var card = el("div", { class: "cf-card cf-membership-card" }, [
      el("h2", { text: "Your packs" }),
    ]);
    if (!wallets.length) {
      card.appendChild(el("p", { class: "cf-membership-sub", text: "You don't have any session packs yet." }));
    } else {
      var list = el("div", { class: "cf-list" });
      wallets.forEach(function (w) { list.appendChild(walletCard(w)); });
      card.appendChild(list);
      card.appendChild(el("p", { class: "cf-membership-note",
        text: "Your pack applies automatically at booking — longer sessions simply use more of it. Cancel in time and it's credited straight back." }));
    }
    return card;
  }

  // ---- "Buy a pack" (plans) ---------------------------------------------------
  function renderBuy(st) {
    var plans = (st.plans || []).filter(function (p) { return p.active !== false; });
    var canBuy = plans.length > 0 && st.online_enabled;
    var children = [
      el("div", { class: "cf-membership-badge", text: "Session packs" }),
      el("h2", { text: "Buy a pack" }),
      el("p", { class: "cf-membership-sub",
        text: "Pay once for several sessions and draw them down as you book." }),
    ];

    var selected = { id: plans.length ? plans[0].id : null };

    if (plans.length) {
      var ON = "2px solid var(--accent, #2563eb)";
      var OFF = "2px solid var(--border, #e5e7eb)";
      var grid = el("div", { class: "cf-row", style: "flex-wrap:wrap;gap:12px;margin:14px 0" });
      var cards = [];
      plans.forEach(function (p, i) {
        var c = el("div", {
          class: "cf-card", role: "button", tabindex: "0",
          style: "flex:1 1 180px;min-width:170px;cursor:pointer;text-align:center;border:" + (i === 0 ? ON : OFF),
        }, [
          el("div", { class: "cf-chip", text: kindLabel(p.service_kind) }),
          el("div", { style: "font-weight:700;font-size:1.05rem;margin-top:6px", text: p.label || (p.sessions_count + " sessions") }),
          el("div", { style: "font-weight:700;font-size:1.3rem;margin:6px 0",
            html: UI.esc(UI.money(p.price_minor, p.currency || st.currency)) }),
          el("div", { class: "cf-muted", text: p.sessions_count + " sessions" }),
          el("div", { class: "cf-muted cf-tiny", text: packTerms(p) }),
        ]);
        cards.push(c);
        function pick() {
          selected.id = p.id;
          cards.forEach(function (x) { x.style.border = OFF; });
          c.style.border = ON;
        }
        c.addEventListener("click", pick);
        c.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } });
        grid.appendChild(c);
      });
      children.push(grid);
    }

    var btn = el("button", { class: "cf-btn cf-btn-primary cf-membership-buy", text: "Buy pack" });
    if (!canBuy) btn.disabled = true;
    else btn.addEventListener("click", function () { buy(btn, selected.id); });
    children.push(btn);

    if (!plans.length) {
      children.push(el("p", { class: "cf-membership-note", text: "Your club doesn't offer session packs yet." }));
    } else if (!st.online_enabled) {
      children.push(el("p", { class: "cf-membership-note",
        text: "Online payment isn't enabled for your club yet — please contact the front desk." }));
    } else {
      children.push(el("p", { class: "cf-membership-note",
        text: "You'll be redirected to a secure payment page (card, Apple/Google Pay). " +
              "Your sessions are added the moment payment completes." }));
    }
    return el("div", { class: "cf-card cf-membership-card" }, children);
  }

  function buy(btn, planId) {
    if (!planId) { UI.toast("Pick a pack first.", "warn"); return; }
    btn.disabled = true;
    var label = btn.textContent;
    btn.textContent = "Starting checkout…";
    auth().apiJSON("/api/billing/bundles/checkout", { method: "POST", body: { bundle_plan_id: planId } })
      .then(function (res) {
        if (!res || !res.order_id) throw new Error("no order returned");
        return window.Pay.startYocoCheckout(res.order_id);
      })
      .catch(function (e) {
        btn.disabled = false; btn.textContent = label;
        UI.toast(UI.errMsg(e) || "Could not start checkout.", "error");
      });
  }

  function renderError(msg) {
    var h = host(); UI.clear(h);
    h.appendChild(el("div", { class: "cf-card cf-empty", text: msg }));
  }

  var Packs = {
    start: function () {
      Promise.all([
        auth().apiJSON("/api/billing/bundles"),
        auth().apiJSON("/api/billing/bundles/wallets"),
      ]).then(function (out) {
        var st = out[0] || {}, wl = out[1] || {};
        var h = host(); UI.clear(h);
        h.appendChild(renderWallets(wl.wallets || []));
        h.appendChild(renderBuy(st));
      }).catch(function (e) {
        renderError(UI.errMsg(e) || "Could not load session packs.");
      });
    },
  };

  window.Packs = Packs;
})();

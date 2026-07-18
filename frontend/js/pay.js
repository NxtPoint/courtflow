// pay.js — drop-in Yoco checkout launcher (payments lane).
//
// HANDOFF: the booking-wizard agent wires the "Pay online" button to call
//     Pay.startYocoCheckout(orderId)
// after createBooking() returns an order_id for an 'online' booking. That one call hits the
// server (which creates the Yoco hosted checkout) and redirects the browser to Yoco — where
// card + Apple/Google/Samsung Pay are offered automatically. On return, /pay-return.html
// shows the outcome; the booking itself is confirmed by the Yoco webhook, not the browser.
//
// Self-contained: talks to the API through window.TFAuth (auth_client.js) — no api.js
// dependency, so it never collides with the wizard agent's files.
(function () {
  function auth() {
    if (!window.TFAuth) throw new Error("auth_client.js must load before pay.js");
    return window.TFAuth;
  }

  // The ONE payment rule, shared by booking + membership + pack purchases:
  //   • >1 allowed mode      → the client chooses (render a chooser into `host`)
  //   • exactly 1, online    → straight to Yoco
  //   • exactly 1, NOT online → check out immediately, no payment prompt
  // `modes` are the server-validated allowed settlement modes for the thing being bought.
  function modeLabel(m) {
    var S = (window.UI && window.UI.SETTLEMENT) || {};
    return (S[m] && S[m].label) || m;
  }

  var Pay = {
    // POST /api/billing/yoco/checkout {order_id} -> {redirect_url}; then redirect.
    // Returns the response if no redirect happened; throws on error (caller can show it).
    startYocoCheckout: function (orderId) {
      if (!orderId) return Promise.reject(new Error("orderId required"));
      return auth().apiJSON("/api/billing/yoco/checkout", {
        method: "POST",
        body: { order_id: orderId },
      }).then(function (res) {
        if (res && res.redirect_url) {
          window.location.href = res.redirect_url;
          return res;
        }
        throw new Error("no redirect_url from checkout");
      });
    },

    // Buy something (membership / pack) applying the one rule above.
    //   opts = { endpoint, bodyBase:{}, modes:[...], host?:Element, onActivated?(res), onError?(err) }
    // host is where the mode chooser renders when there's a real choice (>1 mode). For a single
    // non-online mode it never prompts — it just completes and calls onActivated.
    purchase: function (opts) {
      opts = opts || {};
      var UI = window.UI, el = UI && UI.el;
      var modes = (opts.modes || []).filter(function (m) { return m === "online" || m === "at_court" || m === "monthly_account"; });
      var onErr = opts.onError || function (e) { if (UI) UI.toast(UI.errMsg(e), "error"); };

      function promoErr(e) {
        // Surface a friendly promo reason (the server 400s with {error:'promo_failed', promo_error}).
        if (e && e.body && e.body.promo_error && UI) { UI.toast(e.body.promo_error, "error"); return; }
        onErr(e);
      }
      function checkout(mode) {
        var body = {};
        Object.keys(opts.bodyBase || {}).forEach(function (k) { body[k] = opts.bodyBase[k]; });
        if (mode) body.settlement_mode = mode;
        return auth().apiJSON(opts.endpoint, { method: "POST", body: body })
          .then(function (res) {
            // Promo feedback (a successful code discounts the order OR adds bonus months; a soft failure).
            if (res && res.promo && UI) {
              var pm = res.promo;
              if (pm.is_bonus) { var n = pm.bonus_qty || 0; UI.toast("Offer applied — " + n + " free month" + (n === 1 ? "" : "s") + " added to your membership.", "info"); }
              else UI.toast("Code applied — you saved " + UI.money(pm.discount_minor) + ".", "info");
            } else if (res && res.promo_error && UI) UI.toast(res.promo_error, "warn");
            if (res && (res.settlement_mode === "online" || res.needs_checkout) && res.order_id) {
              return Pay.startYocoCheckout(res.order_id);
            }
            if (res && res.activated) { if (opts.onActivated) opts.onActivated(res); return res; }
            if (res && res.allowed) { renderChooser(res.allowed); return res; }
            throw new Error("unexpected checkout response");
          }, promoErr);
      }

      function renderChooser(ms) {
        if (!opts.host || !el) { return checkout(ms[0]); }   // no host → take the first allowed mode
        var host = opts.host; UI.clear(host);
        host.appendChild(el("div", { class: "cf-pref-h", text: "How would you like to pay?" }));
        var wrap = el("div", { class: "cf-row", style: "gap:8px;flex-wrap:wrap;margin-top:8px" });
        ms.forEach(function (m) {
          wrap.appendChild(el("button", { class: "cf-btn" + (m === "online" ? " cf-btn-primary" : ""), text: modeLabel(m),
            onclick: function () { checkout(m); } }));
        });
        host.appendChild(wrap);
      }

      if (modes.length > 1) { renderChooser(modes); return; }
      checkout(modes[0]);   // 0 or 1 mode: the server resolves the single allowed mode safely
    },

    buyMembership: function (opts) {
      opts = opts || {};
      var base = opts.priceId ? { price_id: opts.priceId } : {};
      if (opts.promoCode) base.promo_code = opts.promoCode;
      Pay.purchase({ endpoint: "/api/billing/membership/checkout", bodyBase: base,
        modes: opts.modes, host: opts.host, onActivated: opts.onActivated, onError: opts.onError });
    },

    buyPack: function (opts) {
      opts = opts || {};
      var base = { bundle_plan_id: opts.planId };
      if (opts.promoCode) base.promo_code = opts.promoCode;
      Pay.purchase({ endpoint: "/api/billing/bundles/checkout", bodyBase: base,
        modes: opts.modes, host: opts.host, onActivated: opts.onActivated, onError: opts.onError });
    },
  };

  window.Pay = Pay;
})();

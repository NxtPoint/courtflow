// pay_return.js — landing page after Yoco's hosted checkout redirects back.
//
// Reads ?order=<id>&r=success|cancel, then polls the order status until the Yoco webhook has
// flipped it to 'paid' (the webhook confirms the booking — this page is UX only). Self-
// contained via window.TFAuth so it doesn't depend on the wizard agent's api.js.
(function () {
  function qp(name) { return new URLSearchParams(location.search).get(name) || ""; }
  function el(id) { return document.getElementById(id); }

  function setStatus(title, msg) {
    if (el("pay-title")) el("pay-title").textContent = title;
    var s = el("pay-status");
    if (s) { s.textContent = msg; s.className = ""; }
  }
  function actions(html) { var a = el("pay-actions"); if (a) a.innerHTML = html; }

  var BTN = 'class="cf-pay-btn"';
  var GHOST = 'class="cf-pay-btn cf-pay-btn-ghost"';

  var PayReturn = {
    start: async function () {
      var orderId = qp("order");
      var outcome = qp("r"); // success | cancel
      var auth = window.TFAuth;
      if (auth && auth.ready) { try { await auth.ready(); } catch (e) {} }

      if (outcome === "cancel") {
        setStatus("Payment cancelled",
          "No charge was made. The slot was held briefly and will be released.");
        actions('<a ' + BTN + ' href="/book">Try again</a> ' +
                '<a ' + GHOST + ' href="/my.html">My bookings</a>');
        return;
      }
      if (!orderId) {
        setStatus("Payment", "We couldn't find your order reference.");
        actions('<a ' + BTN + ' href="/my.html">My bookings</a>');
        return;
      }
      if (!auth || !auth.apiJSON) {
        setStatus("Payment received",
          "If your payment went through, your booking will appear in My Bookings shortly.");
        actions('<a ' + BTN + ' href="/my.html">My bookings</a>');
        return;
      }

      var receiptLink = '<a ' + BTN + ' href="/receipt.html?order=' +
        encodeURIComponent(orderId) + '">View receipt</a> ';

      // Poll order status until 'paid' / 'refunded' (webhook-driven), up to ~30s. If it's still
      // pending part-way through, ask the server to RECONCILE — it checks Yoco directly and
      // recovers a payment whose webhook was missed/slow (free-tier cold-start safety net).
      var tries = 0, maxTries = 15, reconciledAt = -1;
      async function poll() {
        tries++;
        try {
          var res = await auth.apiJSON("/api/billing/yoco/order/" + encodeURIComponent(orderId));
          if (res && res.status === "paid") {
            // Google Ads / GA4 conversion (go-live cutover §5b). Safe no-op until the tag
            // is configured (cfConversion is always defined by web_app's head injection).
            // Fires once — we return immediately after. Covers online booking / membership
            // / pack revenue (all online-paid orders land here).
            try {
              if (window.cfConversion) window.cfConversion("purchase", {
                value: (res.amount_minor || 0) / 100,
                currency: res.currency_code || "ZAR",
                transaction_id: orderId
              });
            } catch (e) {}
            setStatus("Payment received ✓",
              "Your booking is confirmed. A receipt is on its way to your inbox.");
            actions(receiptLink + '<a ' + GHOST + ' href="/my.html">My bookings</a>');
            return;
          }
          if (res && res.status === "refunded") {
            setStatus("Payment refunded", "This order was refunded.");
            actions(receiptLink + '<a ' + GHOST + ' href="/my.html">My bookings</a>');
            return;
          }
          // Still awaiting payment after a few polls → trigger one reconcile, then keep polling.
          if (tries === 4 && reconciledAt !== tries) {
            reconciledAt = tries;
            try { await auth.apiJSON("/api/billing/yoco/reconcile/" + encodeURIComponent(orderId),
              { method: "POST" }); } catch (e2) {}
          }
        } catch (e) {
          // 401/transient — fall through to the retry/timeout message.
        }
        if (tries >= maxTries) {
          setStatus("Almost there…",
            "Your payment is processing. If it went through, your booking will appear in " +
            "My Bookings shortly — and you can grab your receipt below.");
          actions(receiptLink + '<a ' + GHOST + ' href="/my.html">My bookings</a> ' +
                  '<a ' + GHOST + ' href="#" onclick="location.reload();return false;">Refresh</a>');
          return;
        }
        setTimeout(poll, 2000);
      }
      poll();
    },
  };

  window.PayReturn = PayReturn;
})();

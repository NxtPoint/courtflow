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

      // Poll order status until 'paid' / 'refunded' (webhook-driven), up to ~30s.
      var tries = 0, maxTries = 15;
      async function poll() {
        tries++;
        try {
          var res = await auth.apiJSON("/api/billing/yoco/order/" + encodeURIComponent(orderId));
          if (res && res.status === "paid") {
            setStatus("Payment received ✓",
              "Your booking is confirmed. A receipt is on its way.");
            actions('<a ' + BTN + ' href="/my.html">View my bookings</a>');
            return;
          }
          if (res && res.status === "refunded") {
            setStatus("Payment refunded", "This order was refunded.");
            actions('<a ' + BTN + ' href="/my.html">My bookings</a>');
            return;
          }
        } catch (e) {
          // 401/transient — fall through to the retry/timeout message.
        }
        if (tries >= maxTries) {
          setStatus("Almost there…",
            "Your payment is processing. If it went through, your booking will appear in " +
            "My Bookings shortly.");
          actions('<a ' + BTN + ' href="/my.html">My bookings</a> ' +
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

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
  };

  window.Pay = Pay;
})();

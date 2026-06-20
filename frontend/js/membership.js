// membership.js — the member-facing Membership page (self-serve purchase, v1).
//
// v1: buys ONE MONTH of the club's "Unlimited Courts" membership via the existing Yoco one-off
// hosted checkout. Auto-renewing is a later iteration. PAYG members see an Upgrade card with a
// Buy button -> POST /api/billing/membership/checkout -> Pay.startYocoCheckout(order_id) (redirect
// to Yoco). On return, the Yoco webhook activates the membership; this page then shows "Active".
//
// Self-contained over window.TFAuth (auth_client.js) like pay_return.js — no api.js dependency
// for the membership routes (those aren't in api.js's diary/billing wrappers).
(function () {
  function auth() {
    if (!window.TFAuth) throw new Error("auth_client.js must load before membership.js");
    return window.TFAuth;
  }
  var UI = window.UI, el = function () { return UI.el.apply(UI, arguments); };

  function host() { return document.getElementById("cf-membership"); }

  function fmtDate(iso) {
    if (!iso) return "";
    try {
      return new Date(iso + "T00:00:00").toLocaleDateString("en-ZA",
        { day: "numeric", month: "long", year: "numeric", timeZone: UI.CLUB_TZ });
    } catch (e) { return iso; }
  }

  // ---- Active membership card -------------------------------------------------
  function renderActive(st) {
    var h = host(); UI.clear(h);
    var end = st.current_period_end;
    var card = el("div", { class: "cf-card cf-membership-card cf-membership-active" }, [
      el("div", { class: "cf-membership-badge cf-membership-badge-on", text: "Active" }),
      el("h2", { text: "Unlimited Courts" }),
      el("p", { class: "cf-membership-sub",
        text: end ? ("Your membership is active until " + fmtDate(end) + ".")
                  : "Your membership is active." }),
      el("ul", { class: "cf-membership-perks" }, [
        el("li", { text: "Court bookings are free — covered by your membership." }),
        el("li", { text: "Book any available court at no charge." }),
      ]),
      el("p", { class: "cf-membership-note",
        text: end ? ("This is a one-month membership. Renew after " + fmtDate(end) +
                     " to keep your courts free.")
                  : "This is a one-month membership; renew when it lapses." }),
    ]);
    h.appendChild(card);
  }

  // ---- PAYG / upgrade card ----------------------------------------------------
  function renderUpgrade(st) {
    var h = host(); UI.clear(h);
    var priced = st.price_minor != null;
    var canBuy = priced && st.sold && st.online_enabled;

    var children = [
      el("div", { class: "cf-membership-badge", text: "Pay as you go" }),
      el("h2", { text: "Upgrade to Unlimited Courts" }),
      el("p", { class: "cf-membership-price",
        html: priced
          ? (UI.esc(UI.money(st.price_minor, st.currency)) +
             '<span class="cf-membership-per"> / month</span>')
          : "Price on request" }),
      el("ul", { class: "cf-membership-perks" }, [
        el("li", { text: "All your court bookings become free." }),
        el("li", { text: "One simple monthly membership." }),
        el("li", { text: "Lessons and classes are charged as usual." }),
      ]),
    ];

    var btn = el("button", {
      class: "cf-btn cf-btn-primary cf-membership-buy",
      text: "Buy · 1 month",
    });
    if (!canBuy) {
      btn.disabled = true;  // .cf-btn:disabled dims it
    } else {
      btn.addEventListener("click", function () { buy(btn); });
    }
    children.push(btn);

    if (!st.sold) {
      children.push(el("p", { class: "cf-membership-note",
        text: "Membership isn't offered by your club yet." }));
    } else if (!st.online_enabled) {
      children.push(el("p", { class: "cf-membership-note",
        text: "Online payment isn't enabled for your club yet — please contact the front desk." }));
    } else {
      children.push(el("p", { class: "cf-membership-note",
        text: "One-month membership. You'll be redirected to a secure payment page " +
              "(card, Apple/Google Pay). Renew when it lapses." }));
    }

    h.appendChild(el("div", { class: "cf-card cf-membership-card" }, children));
  }

  function buy(btn) {
    btn.disabled = true;
    var label = btn.textContent;
    btn.textContent = "Starting checkout…";
    auth().apiJSON("/api/billing/membership/checkout", { method: "POST", body: {} })
      .then(function (res) {
        if (!res || !res.order_id) throw new Error("no order returned");
        // Hand off to the shared Yoco launcher — redirects to the hosted page.
        return window.Pay.startYocoCheckout(res.order_id);
      })
      .catch(function (e) {
        btn.disabled = false;
        btn.textContent = label;
        UI.toast(UI.errMsg(e) || "Could not start checkout.", "error");
      });
  }

  function renderError(msg) {
    var h = host(); UI.clear(h);
    h.appendChild(el("div", { class: "cf-card cf-empty", text: msg }));
  }

  var Membership = {
    start: function () {
      auth().apiJSON("/api/billing/membership/status")
        .then(function (st) {
          if (st && st.active) renderActive(st);
          else renderUpgrade(st || {});
        })
        .catch(function (e) {
          renderError(UI.errMsg(e) || "Could not load your membership.");
        });
    },
  };

  window.Membership = Membership;
})();

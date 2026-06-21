// membership.js — the member-facing Membership page (self-serve purchase, configurable terms).
//
// The club configures TERM PLANS (label · price · duration). PAYG members see the plans as
// selectable cards; choosing one + Buy -> POST /api/billing/membership/checkout {price_id} ->
// Pay.startYocoCheckout(order_id) (redirect to Yoco). On return, the Yoco webhook activates the
// membership for that plan's term; this page then shows "Active". When only one plan exists it's
// pre-selected. There's no auto-renew yet — the member re-buys when the term lapses.
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

  function planTerm(months) {
    var m = parseInt(months, 10) || 0;
    return m === 1 ? "1 month" : (m + " months");
  }
  function planLabel(p) { return p.label || planTerm(p.term_months); }

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
        text: end ? ("Renew after " + fmtDate(end) + " to keep your courts free.")
                  : "Renew when it lapses to keep your courts free." }),
    ]);
    h.appendChild(card);
  }

  // ---- PAYG / upgrade card with selectable term plans -------------------------
  function renderUpgrade(st) {
    var h = host(); UI.clear(h);
    var plans = (st.plans || []).filter(function (p) { return p.active !== false; });
    var canBuy = plans.length > 0 && st.sold && st.online_enabled;

    var children = [
      el("div", { class: "cf-membership-badge", text: "Pay as you go" }),
      el("h2", { text: "Upgrade to Unlimited Courts" }),
      el("ul", { class: "cf-membership-perks" }, [
        el("li", { text: "All your court bookings become free." }),
        el("li", { text: "Pick the membership term that suits you." }),
        el("li", { text: "Lessons and classes are charged as usual." }),
      ]),
    ];

    var selected = { price_id: plans.length ? plans[0].price_id : null };

    if (plans.length) {
      // Selectable plan cards. Layout via inline flex (the bright selected state uses the design
      // system's accent border — no app.css change). Selection = an accent ring on the chosen card.
      var ON = "2px solid var(--accent, #2563eb)";
      var OFF = "2px solid var(--border, #e5e7eb)";
      var grid = el("div", { class: "cf-row", style: "flex-wrap:wrap;gap:12px;margin:14px 0" });
      var cards = [];
      plans.forEach(function (p, i) {
        var planCard = el("div", {
          class: "cf-card cf-membership-plan", role: "button", tabindex: "0",
          style: "flex:1 1 150px;min-width:140px;cursor:pointer;text-align:center;border:" +
                 (i === 0 ? ON : OFF),
        }, [
          el("div", { style: "font-weight:700;font-size:1.05rem", text: planLabel(p) }),
          el("div", { style: "font-weight:700;font-size:1.3rem;margin:6px 0",
            html: UI.esc(UI.money(p.amount_minor, p.currency || st.currency)) }),
          el("div", { class: "cf-muted", text: "for " + planTerm(p.term_months) }),
        ]);
        cards.push(planCard);
        function pick() {
          selected.price_id = p.price_id;
          cards.forEach(function (c) { c.style.borderBottom = OFF; c.style.border = OFF; });
          planCard.style.border = ON;
        }
        planCard.addEventListener("click", pick);
        planCard.addEventListener("keydown", function (ev) {
          if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); }
        });
        grid.appendChild(planCard);
      });
      children.push(grid);
    }

    var btn = el("button", {
      class: "cf-btn cf-btn-primary cf-membership-buy",
      text: "Buy membership",
    });
    if (!canBuy) {
      btn.disabled = true;  // .cf-btn:disabled dims it
    } else {
      btn.addEventListener("click", function () { buy(btn, selected.price_id); });
    }
    children.push(btn);

    if (!st.sold || !plans.length) {
      children.push(el("p", { class: "cf-membership-note",
        text: "Membership isn't offered by your club yet." }));
    } else if (!st.online_enabled) {
      children.push(el("p", { class: "cf-membership-note",
        text: "Online payment isn't enabled for your club yet — please contact the front desk." }));
    } else {
      children.push(el("p", { class: "cf-membership-note",
        text: "You'll be redirected to a secure payment page (card, Apple/Google Pay). " +
              "Your membership lasts for the term you choose; renew when it lapses." }));
    }

    h.appendChild(el("div", { class: "cf-card cf-membership-card" }, children));
  }

  function buy(btn, priceId) {
    btn.disabled = true;
    var label = btn.textContent;
    btn.textContent = "Starting checkout…";
    var body = priceId ? { price_id: priceId } : {};
    auth().apiJSON("/api/billing/membership/checkout", { method: "POST", body: body })
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

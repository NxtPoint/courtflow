# 05 — Payments: Provider-Agnostic Abstraction

> Tomo: *"store plans etc and then pass to any payment gateway."* + *"Yoco first (I have the keys),
> keep it vanilla like we did PayPal in 2 hours."* + settlement must support **pay online / pay at
> court / pay end of month**.
>
> This is **designed‑in now, switched on in Phase 4** (or earlier for Yoco, since keys exist). The
> diary launches with `at_court` + `monthly_account` settlement; `online` is a flag flip.

## 1. The pattern we're copying (it already exists in 1050)

1050's billing has the exact shape we want: **many providers, one grant path**. Wix and PayPal both
feed `subscriptions_api.apply_subscription_event(payload, provider)`; each provider has a thin adapter
(`paypal_billing/`), prices live in a catalogue (`plans.py` + `catalog.json`), and a public
`GET /api/billing/paypal/config` probe tells the frontend what's enabled. **We generalise this.**

```
            ┌── Yoco adapter ───┐
checkout →  │  PayPal adapter   │ ──normalize──► apply_payment_event(payload, provider) ──► billing.* (order/payment/ledger)
            │  "manual" (desk)  │                         (idempotent, one path)
            └───────────────────┘
            each adapter: create_checkout(), verify_webhook(), parse_event()
```

## 2. The gateway interface (provider‑agnostic)

A Python `PaymentGateway` protocol; each provider implements it. **No core code knows about Yoco
specifics** — only the adapter does.

```python
class PaymentGateway(Protocol):
    name: str                                   # 'yoco' | 'paypal'
    def create_checkout(self, *, order, success_url, cancel_url) -> CheckoutIntent: ...
        # returns {intent_id, redirect_url|client_token, provider}
    def verify_webhook(self, request) -> bool: ...      # signature check
    def parse_event(self, payload) -> NormalizedPaymentEvent: ...
        # → {provider, provider_payment_id, order_ref, amount_minor, currency, status, kind}
    def refund(self, *, payment, amount_minor) -> RefundResult: ...      # later
```

`NormalizedPaymentEvent.kind ∈ {charge_succeeded, charge_failed, refunded, subscription_active,
subscription_cancelled}`. Everything downstream consumes the **normalized** shape.

## 3. The single grant/settlement path

```python
def apply_payment_event(event: NormalizedPaymentEvent):
    # 1. idempotency: insert billing.payment_attempt(event_hash) — skip if exists (1050 pattern)
    # 2. find order by event.order_ref (we set custom_id/metadata=order_id at checkout)
    # 3. record billing.payment(provider, provider_payment_id, amount, direction, status) — unique
    # 4. on charge_succeeded: order.status='paid'; confirm the held booking(s); ledger entry
    #    on refunded: record refund payment (do NOT auto-reverse booking — business decision, 1050-style)
    #    on subscription_active: membership_subscription.status='active', set period_end
    # 5. emit payment_succeeded / membership_started event → Klaviyo + core.usage_event
```

This function is provider‑independent. Adding a new gateway = write an adapter + register it; this
core never changes.

## 4. Plan / price catalogue

- **Prices are ours** (`billing.price`, in DB, ZAR per NextPoint) — the source of truth, exactly like
  1050's `plans.py` being canonical.
- **Provider plan/product ids** (for recurring memberships that the gateway must know about) live in a
  small per‑provider catalogue table/JSON: `billing.provider_plan(provider, price_id, provider_plan_id)`.
  One‑off charges (court/lesson/class) don't need a provider plan — just an amount at checkout.
- `GET /api/billing/config` (public) returns `{ online_enabled, provider, currency, public_key }` so
  the frontend renders the right checkout (or hides it and shows pay‑at‑court). Mirrors 1050's config
  probe → instant rollback by flipping `allow_online_payment`/`PAYMENTS_ENABLED`.

## 5. The three settlement modes (launch behaviour)

| Mode | When | Flow |
|---|---|---|
| **`at_court`** | Default at launch | Booking `confirmed` immediately; `order.status='open'`; settled at desk (admin records `billing.payment` provider=`cash`/`card_at_desk`). No gateway. |
| **`monthly_account`** | Members with a tab | Booking `confirmed`; `order` → `account_ledger` charge; balance accrues; `cron_monthly_invoice` produces a statement; settle by EFT/card later. |
| **`membership_covered`** | Member booking a court under "unlimited courts R220/mo" | Booking `confirmed`; `order.amount=0`/covered; no payment. Membership itself may be recurring (online when live, or manual). |
| **`online`** | Phase 4 (Yoco) | Booking `held` → `create_checkout` → redirect/popup → webhook `charge_succeeded` → `apply_payment_event` → `confirmed`. Expired holds released by sweep. |
| **`free`** | Complimentary lesson funnel | Booking `confirmed`, amount 0. |

The booking API takes `settlement_mode`; allowed modes are gated by `club.policy` + role (admins can
force any; members see what the club allows).

## 6. Yoco adapter (✅ IMPLEMENTED & LIVE)

Yoco is the live first provider — hosted **Checkout** (redirect) with **card + Apple Pay / Google Pay /
Samsung Pay** (wallets render automatically on Yoco's hosted page). Built vanilla; `billing/` core untouched.

`yoco_billing/` (mirrors `paypal_billing/`):
- `client.py` — REST client: `create_checkout` (`POST https://payments.yoco.com/api/checkouts`),
  `refund_checkout` (`POST /api/checkouts/{id}/refund`), `get_checkout` (`GET /api/checkouts/{id}`, used by
  reconciliation), and `verify_signature` (**Standard-Webhooks / svix** scheme: `whsec_` secret,
  HMAC-SHA256 over `{id}.{timestamp}.{body}`, ±3-min replay window).
- `adapter.py` — `YocoGateway` implements `PaymentGateway` (`create_checkout`/`verify_webhook`/`parse_event`/
  `refund`); self-registers via `register_gateway("yoco", …)` on import.
- `routes.py` — `POST /api/billing/yoco/checkout` (server-side create, amount/order from the DB — never the
  client), `POST /api/billing/yoco/webhook` (verify → `apply_payment_event`), `POST /api/billing/yoco/refund`
  (`{order_id, amount_minor?, cancel_booking?}`), `POST /api/billing/yoco/reconcile/<order_id>` +
  `POST /api/cron/reconcile-payments` (missed-webhook recovery), `GET /api/billing/receipt/<order_id>`
  (receipt JSON). `GET /api/billing/config` advertises the provider/public key.
- Frontend (payments-owned): `frontend/js/pay.js` (`Pay.startYocoCheckout`), `pay-return.html` + `pay_return.js`
  (return + reconcile fallback + receipt link), `receipt.html` + `receipt.js` (printable receipt).
- Env (all in `render.yaml` on `courtflow-api`): `YOCO_SECRET_KEY`/`YOCO_PUBLIC_KEY`/`YOCO_WEBHOOK_SECRET`
  (`sync:false`), `PAYMENTS_ENABLED=1`, `PAYMENTS_PROVIDER=yoco`, `APP_BASE_URL`.

**Reconciliation (`reconcile.py`):** on the free tier the API sleeps; a missed webhook can leave an order
`awaiting_payment` though the customer paid. `get_checkout` asks Yoco; if `completed`+`paymentId` it replays a
`charge_succeeded` through `apply_payment_event` (idempotent). Safe-by-design if the GET surface is absent.

**Memberships:** Yoco hosted checkout is one-off, so self-serve membership = a one-off term purchase (no native
recurring); the `subscription_*` event handlers exist but stay dormant. Auto-renewing is a later iteration.

## 7. PayPal adapter (second provider, mostly free)

Port 1050's `paypal_billing/` as a second `PaymentGateway` implementation so the abstraction is
proven with ≥2 providers from the start (and so any USD/international club can use it). Keep it behind
the same interface; do not let PayPal‑isms leak into core.

## 8. Refunds & disputes (✅ IMPLEMENTED)

`billing.payment.direction='refund'`; record-only by default (don't auto-reverse bookings) — the exact
1050 decision. The admin **"Recent online payments"** view offers two actions: **"Refund only"** (reverse
the charge, keep the booking) and **"Refund & cancel"** (also cancel the order's booking(s) + free the slot
via `diary.cancel_booking`, admin-fee waived). Both call `POST /api/billing/yoco/refund`. A full refund sends
NO `amount` (Yoco's `amount` is nullable = full); the lookup uses the Yoco **checkout** id (`ch_`), not the
webhook **payment** id (`p_`). The `refund.succeeded` webhook writes the ledger row (record-only). Disputes/
chargebacks log as `payment_attempt` events.

## 9. Build order for payments

1. **✅ Done (with the diary):** `billing.*` tables, `order`/`order_line`/`account_ledger`, the
   `at_court` / `monthly_account` / `membership_covered` / `free` modes, the `apply_payment_event`
   core + the `manual` provider (desk payments).
2. **✅ Done — Yoco LIVE:** `yoco_billing/` adapter + `online` mode + config probe + frontend checkout +
   refunds (refund-only / refund-and-cancel) + reconciliation + printable receipts. `allow_online_payment=true`
   for NextPoint; `PAYMENTS_ENABLED=1`.
3. **PayPal:** port adapter; multi-provider proven. (Not yet built.)
4. **Memberships online (one-off term purchase) — DONE; auto-renewing subscriptions + monthly statement pay
   links — later.**

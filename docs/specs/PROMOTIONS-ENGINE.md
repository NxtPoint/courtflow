# Promotions Engine

**Status: COMPLETE — PHASE 1 + 2 BUILT & LIVE (2026-07-18).** A billing sub-module to run **specials with
promo codes redeemed at checkout**. Phase 1 = %/fixed off. Phase 2a = `bonus_period` ("3 months → +1 free"
membership) + unique per-recipient codes. **Phase 2b = `bonus_units` ("buy a 10-pack, get 12").** All four
promo kinds + unique codes are live across every checkout surface.

## Phase 2b — what shipped (2026-07-18)
- **`bonus_units`** (packs): a promo grants FREE extra sessions on top of the pack — reuses the existing
  `adjust_wallet` primitive (raises minutes_total + remaining, audited). Symmetric with `bonus_period`:
  **online** buy → the sessions are added at the first grant in `bundles._grant_wallet_now` (reads
  `_bonus_units_for_order`; gated to the fresh grant so a webhook replay never re-adds); **offline** buy →
  `apply_to_order` adds them to the already-active wallet via `adjust_wallet` on the fresh redemption. Never
  double-granted. Admin UI locks the scope to packs; checkout shows "N free sessions added".
- `kind` CHECK grown to include `bonus_units` (same DO-block migration); `bonus_qty` reused as the session count.

## Phase 2a — what shipped (2026-07-18)
- **`bonus_period`** (membership): a promo grants FREE extra months on top of the paid term — the member pays
  the 3-month price, gets 4. Reuses the existing period grant: the bonus is just extra `months`. **Online** buy
  → `activate_membership_for_order` adds `_bonus_months_for_order` at activation (idempotent via the
  already-active guard). **Offline** buy → `apply_to_order` extends the already-active subscription directly on
  the fresh redemption. Never double-granted. Scope is locked to memberships in the admin UI.
- **Unique per-recipient codes** (`billing.promotion_code`): mint a batch (one code per member) for a Klaviyo
  campaign; each is single-use (`max_uses`), optionally bound to a recipient, unguessable (no 0/O/1/I). Lookup
  checks the shared `promotion.code` first, then the per-recipient table. Admin: **Setup → Promotions → a promo →
  "Unique codes →"** (generate N, copy-all for pasting into Klaviyo, revoke). Routes `POST/GET
  /api/admin/promotions/<id>/codes` + `POST …/codes/revoke`.
- **Checkout:** unchanged flow — a `bonus_period` code shows "N free months added" instead of a rand discount
  (`pay.js`); the order price is untouched (bonus ≠ discount).
- **✅ VERIFIED (Phase 1 + 2, both grant paths):** Tomo ran `python -m scripts.test_all` — **all harnesses
  passed** (booking/billing/statement, incl. billing 311) after Phase 2a AND after Phase 2b, 2026-07-18. The
  membership-term grant AND the pack-wallet grant are both clean; the whole engine is safe to switch on.

## Phase 2b — ✅ SHIPPED (see above). The engine is now feature-complete:
percent_off · amount_off · bonus_period (membership) · bonus_units (packs) · shared + unique per-recipient codes.

## Phase 1 — what shipped
- **Schema:** `billing.promotion` + `billing.promotion_redemption` (idempotent DDL, `billing/schema.py`).
- **Engine:** `billing/promotions.py` — `validate` (preview), `apply_to_order` (delegates to
  `statement.discount_order`), `reverse_for_order` (refund/void frees the slot), + admin CRUD. Eligibility:
  scope / window / min-spend / first-time / global + per-customer caps / stacking-off.
- **Admin UI:** **Setup → "Promotions & offers"** (`AdminUI.promotions`) — create/edit/pause/archive a promo
  (name, code, % or R off, scope, caps, min-spend, end date, first-time), and view its redemptions.
- **Checkout wiring (LIVE — all surfaces):** **membership + pack** purchases pass `promo_code` in the
  checkout body (`plan.js` → `Pay.buyMembership/buyPack`), applied server-side before payment; a bad code on
  an online buy aborts cleanly (voids the un-paid order). **Court/lesson bookings** (`booking.js`) apply the
  code via `POST /api/billing/promo/apply` between `createBooking` and `Pay.startYocoCheckout` — an online
  booking HALTS on a bad code (the held booking + unpaid order lazy-expire), an owed booking treats it as a
  soft warning and still stands. The promo field shows only when there's a payable amount (not covered/pack/
  on-behalf).
- **Customer API:** `POST /api/billing/promo/validate` (preview) · `POST /api/billing/promo/apply`
  (owner of the order, or staff/desk). Admin: `/api/admin/promotions*`.
- **Measurement:** every redemption emits `promo_redeemed` (→ usage_event + Klaviyo) — ties a sale back to
  the campaign that carried the code.

> Verify on a sandbox before relying on it in anger: `python -m db` twice (idempotency) +
> `python -m scripts.test_all` (booking/billing/statement harnesses). The engine is additive; existing
> billing paths only gained the two `reverse_for_order` hooks + the opt-in checkout promo helper.

---

## Original plan (below) — Phase 1 above is the realised subset; Phase 2/3 remain.

## 1. What this is (and what it is NOT)
- **Promotion** = the OFFER + its rules + a redeemable CODE. This module owns it.
- **Campaign** = the *distribution* of an offer (a Klaviyo email/SMS send to a segment). Klaviyo owns that
  (see `KLAVIYO-MASTER-PLAN.md`). The promotions engine is what a campaign **points at**: the Jan "20% off
  membership" email carries a code this engine validates + honours at checkout.
- So the deliverable is the **offer + code + redemption** machinery, wired into the existing checkout flows
  and an admin surface to create/track promos. It is NOT a new send channel.

## 2. Design principles (fit the existing billing model — don't fork it)
- **Reuse the ONE debt store.** A redeemed promo is just a discount on the order. `billing.statement.
  discount_order(session, club_id, order_id, discount_minor=…, reason=…)` ALREADY reduces any open order's
  total, splits multi-line pro-rata, keeps `coach_arrears`/commission in **lockstep**, and preserves the
  pre-discount price in `order_line.original_amount_minor` ("was → now"). **The promo engine computes a
  discount and DELEGATES to `discount_order` — it never invents a second debt or a new settlement path.**
- **One debt = one order** (unchanged). A promo mutates the order total before payment; Yoco then charges
  the discounted total (checkout reads the order total, so online "just works").
- **Provider-agnostic.** No Yoco-specific logic — the discount lands on the order, upstream of any gateway.
- **Multi-tenant + club-scoped** (Iron rule). Every promotion row is `club_id`-scoped; codes are unique
  **per club**, never globally.
- **Idempotent + audited.** A redemption is recorded once; a refund/void reverses it (frees the usage slot).

## 3. Data model (new — `billing.schema.py`, idempotent DDL)
```
billing.promotion
  id                uuid pk
  club_id           uuid  not null            -- Iron rule
  code              text                       -- redeem code, UNIQUE per club, case-insensitive; NULL = automatic (no code)
  name              text  not null             -- admin label ("January Membership 20%")
  description       text
  kind              text  not null             -- percent_off | amount_off | (P2) bonus_period | bonus_units | free_item
  value_minor       int                        -- amount_off: cents ; percent_off: use percent_bps instead
  percent_bps       int                        -- percent_off: basis points (2000 = 20%)
  applies_to        text  not null default 'all' -- all | court | lesson | class | membership | pack | product
  product_id        uuid                       -- when applies_to='product' (a specific service/plan)
  min_spend_minor   int                        -- eligibility floor (NULL = none)
  first_time_only   bool  default false        -- customer's FIRST purchase of this scope only
  max_redemptions   int                        -- global cap (NULL = unlimited)
  per_customer_cap  int   default 1            -- redemptions per customer
  stackable         bool  default false        -- may combine with another promo / an admin discount
  starts_at         timestamptz
  ends_at           timestamptz
  status            text  default 'active'      -- active | paused | expired | archived
  created_by        uuid ; created_at ; updated_at

billing.promotion_redemption            -- the usage ledger (drives caps + reporting)
  id                uuid pk
  club_id           uuid not null
  promotion_id      uuid not null -> promotion
  order_id          uuid not null -> "order"   -- the order it discounted
  user_id           uuid                        -- who redeemed (iam.user)
  discount_minor    int  not null               -- what it actually took off
  status            text default 'applied'      -- applied | reversed (on refund/void)
  redeemed_at       timestamptz default now()
  UNIQUE (promotion_id, order_id)               -- one promo per order (no self-stack)
```
> A **unique code per recipient** (Klaviyo dynamic coupons) is a Phase-2 add: a `billing.promotion_code`
> child table (many codes → one promotion, each with its own per-code cap). Phase 1 = one shared code.

## 4. Promotion kinds
| kind | Phase | Mechanic | Example |
|---|---|---|---|
| `percent_off` | **1** | `discount = round(total × percent_bps/10000)`, scoped to eligible lines | 20% off membership |
| `amount_off` | **1** | `discount = min(value_minor, total)` | R100 off first lesson |
| `bonus_period` | 2 | extend the membership term (`current_period_end += N`) at same price | 3 months → +1 free |
| `bonus_units` | 2 | add free units to a pack wallet on purchase | buy 10-pack, get 12 |
| `free_item` | 3 | a $0 line added (e.g. free racket hire with a lesson) | — |

**"Buy 3 months, get 1 free"** is `bonus_period` (Phase 2) — it grants EXTRA value, not a price cut, so it's
a different mechanic from `discount_order`. Phase-1 equivalent that ships now: model it as `percent_off` 25%
on a 4-month plan (pay for 3). The plan recommends starting there and adding `bonus_period` in Phase 2.

## 5. Eligibility (validated server-side, in order)
1. Promo exists, `status='active'`, now within `[starts_at, ends_at]`.
2. `applies_to` matches the order's products (all / kind / specific `product_id`). A membership-only promo
   is refused on a court order.
3. Order total ≥ `min_spend_minor`.
4. `first_time_only` → the customer has no prior paid order of that scope.
5. Global cap: `count(redemptions where status='applied') < max_redemptions`.
6. Per-customer cap: this user's applied redemptions `< per_customer_cap`.
7. Stacking: unless `stackable`, refuse if the order already carries a promo or an admin discount
   (`original_amount_minor` already set).
A failure returns a typed reason (`PROMO_NOT_FOUND` / `EXPIRED` / `NOT_ELIGIBLE_SCOPE` / `MIN_SPEND` /
`ALREADY_USED` / `LIMIT_REACHED` / `NOT_STACKABLE`) the checkout UI shows inline.

## 6. Checkout flow (where it plugs in)
The existing pattern: create the order (`open`/`awaiting_payment`) → **apply promo** → pay.
```
Pay step (booking.js / membership checkout / pack buy) shows a "Promo code" field
   │  optional: POST /api/billing/promo/validate {code, scope, amount_minor}  → live preview (no write)
   ▼
create_booking / membership checkout / pack purchase accepts optional promo_code
   → creates the order
   → promo.apply(session, code, order_id, user_id):
        validate (§5) → discount_minor → discount_order(...) → record redemption
   → returns the DISCOUNTED order (booking.order_id → Pay.startYocoCheckout charges the new total)
```
- **Online (Yoco):** no change — the checkout reads the order total, now discounted.
- **Owed / desk / pack draw:** the discounted order flows through the same settlement.
- **Refund/void:** reverse the redemption (`status='reversed'`) so the usage slot frees + reporting is honest
  (hook into the existing refund path in `billing/events.py`).

## 7. API surface
- `POST /api/billing/promo/validate` — `{code, applies_to, amount_minor, product_id?}` → `{ok, discount_minor,
  label}` or `{ok:false, reason}`. Preview only, no write. Called from the pay step.
- Booking/checkout endpoints gain an optional **`promo_code`** field (apply-on-create, above).
- **Admin CRUD** (Admin lane): `GET/POST/PATCH /api/admin/promotions`, `POST …/<id>/pause|archive`,
  `GET /api/admin/promotions/<id>/redemptions` (who/when/how much → performance).
- Emit **`promo_redeemed`** through the ONE `emit()` funnel → `core.usage_event` + Klaviyo (measures the
  campaign that drove it; a gclid'd redeemer also feeds the Google Ads offline-conversion loop).

## 8. Admin UI (reuse-first, per the Golden Rule)
A **Promotions** section under Admin → Money or Setup (owner + `club_admin`):
- List of promos with status + redemption count + revenue impact.
- Create/edit via an `AdminUI`-style full-screen editor (`cf-*`): code, kind, value, scope (service picker
  reuses `Widgets.ServiceList`), window, caps, first-time toggle.
- A promo's drill-through = its redemptions (reuse the shared transaction record).
- No new widget family — extend the existing admin editor + list patterns.

## 9. Phasing
- **Phase 1 (core):** `percent_off` + `amount_off`, shared code, eligibility (§5), checkout apply on
  membership + pack + booking, admin CRUD + redemptions report, `promo_redeemed` emit, refund reversal.
  → covers "20% off memberships" and most specials.
- **Phase 2 (value promos + campaign tie-in):** `bonus_period` (3+1 months) + `bonus_units` (pack bonus);
  **unique per-recipient codes** synced to/from **Klaviyo coupons** so a campaign email carries a one-time
  code; automatic (no-code) promos targeted by segment.
- **Phase 3 (advanced):** stacking rules, referral codes, scheduled auto-activate/expire, free_item.

## 10. Decisions (2026-07-18)
**LOCKED (Tomo):**
1. **Code model v1 = one SHARED code per promo** (e.g. `MEMBER20`) with total + per-customer caps. Unique
   per-recipient codes are deferred to Phase 2.
4. **First special = a straight % / fixed off** (Phase 1, e.g. 20% off membership). The **3-months-get-1-free**
   offer comes in **Phase 2** via `bonus_period`. → build **both, in phase order**.

**Default unless Tomo objects (confirm at build):**
2. **Stacking = OFF** — a promo can't combine with an admin discount or another promo.
3. **Member self-serve entry = ON** — members type the code in the booking/checkout UI (that's the point).
5. **VAT = ignored** — NextPoint is not VAT-registered, so promos just reduce the total; no VAT interplay.

## 11. Gotchas / guards (bake in at build)
- **Never double-discount:** the `stackable=false` guard keys off `order_line.original_amount_minor` already
  being set (the same field an admin discount uses).
- **`membership_covered` orders are R0** — a percent-off of R0 is R0; refuse a promo on a fully-covered order
  (`MIN_SPEND` / nothing to discount) rather than record a meaningless redemption.
- **Per-EXACT-product scope** (mirror the payment-modes gotcha): resolve eligibility by the order's real
  `product_id`, never by `kind` alone, so a "clay court" promo can't leak onto a hardcourt order.
- **Caps are race-safe:** count applied redemptions inside the same transaction as the insert (or a UNIQUE
  partial index) so two simultaneous redemptions can't both pass a `max_redemptions=1` gate.
- **Refund reverses the redemption** (frees the slot) — wire into the existing refund fan-out.
- **Codes are case-insensitive + trimmed**, unique per club (a partial unique index on `lower(code)` where
  `status <> 'archived'`).

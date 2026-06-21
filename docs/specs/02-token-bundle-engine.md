# 02 — Token / Bundle engine (prepaid session packs)

A **generic, owner-configurable prepaid-pack ("token bundle") capability** for CourtFlow.
A member buys a **pack of N prepaid sessions** upfront via Yoco; booking a matching service
**draws down** from the pack (settling the order at R0); cancelling **credits it back**. It is the
**unit (time)-based** sibling of **PAYG** (per-use) and **membership** (term-based).

> **As-built note (unit model).** The balance is held in **MINUTES**, not a count. A pack has a base
> session length; a booking draws minutes **proportional to its duration**, so **one pack covers any
> length** — a 90-min court off a 60-min unit costs **1.5 sessions**, a 30-min off a 30-unit costs 1,
> a class draws **one full unit** (per-session). This replaced the original strictly count-based
> draw-down (1 booking = 1 token, duration as an exact-match gate). See §2.

It works generically across **courts, lessons, AND classes**, and is **fully configurable** by the
owner (and, for lesson packs, by the **coach** — see §5): any service kind, base session duration,
price, number of sessions, optional validity window, optional coach-specificity for lesson packs.
**Nothing is hardcoded** — a pack is data (a `billing.bundle_plan` row), like membership term plans.

Design rules carried from the rest of billing:
- **Reuse, don't reinvent** — the purchase flow mirrors `billing/membership.py` (a pending row +
  an `awaiting_payment` online order linked by `order_id`; activation in the Yoco webhook next to
  `activate_membership_for_order`). The booking seam mirrors `membership_covered` (a new
  `settlement_mode='token'`). The commission feed reuses the existing engine via a proper order line.
- **Atomic, idempotent, no double-spend, no lost minutes** — the draw and the booking commit in ONE
  transaction; the draw/credit are each recorded at most once per booking (a `token_ledger` UNIQUE);
  concurrent draws are serialised with `SELECT … FOR UPDATE`.
- **Multi-tenant** — every row carries `club_id`.

---

## 1. Data model (3 tables in `billing/schema.py`)

### `billing.bundle_plan` — the owner-configured offer (a pack you can buy)
| column | type | notes |
|---|---|---|
| `id` | uuid PK | |
| `club_id` | uuid NOT NULL → club.club | tenant |
| `service_kind` | text CHECK in (`court`,`lesson`,`class`) | what the pack is spendable on |
| `coach_user_id` | uuid NULL | lesson packs may be coach-specific; NULL = any coach |
| `label` | text | display name ("10 court hours") |
| `sessions_count` | int NOT NULL | number of base-length sessions granted |
| `duration_minutes` | int NULL | **the pack's BASE unit length** (the divisor). NULL → 60 default at activation |
| `price_minor` | int NOT NULL | pack price (cents) |
| `validity_days` | int NULL | NULL = never expires; else `expires_at = today + validity_days` |
| `active` | bool | = (`status`='active'); kept in sync |
| `status` | text CHECK in (`active`,`dormant`,`retired`) | **lifecycle** — dormant = configured but hidden from customers; retired = soft-deleted |
| `created_at` | timestamptz | |

### `billing.token_wallet` — a member's purchased pack (denormalised for fast matching)
| column | type | notes |
|---|---|---|
| `id` | uuid PK | |
| `club_id` | uuid NOT NULL | tenant |
| `user_id` | uuid | the owner (iam.user.id) |
| `bundle_plan_id` | uuid → bundle_plan | the plan bought |
| `order_id` | uuid NULL → billing.order | the Yoco purchase order (the webhook's recognition key) |
| `service_kind` | text | denormalised from the plan |
| `coach_user_id` | uuid NULL | denormalised (NULL = any) |
| `duration_minutes` | int NULL | denormalised plan unit length (informational) |
| `base_minutes` | int | **the unit length (divisor)** — granted minutes = `sessions_count × base_minutes` |
| `minutes_total` | int | **authoritative** total granted, in minutes |
| `minutes_remaining` | int CHECK ≥ 0 | **authoritative live balance, in minutes** (a booking draws its duration) |
| `tokens_total` | int | nominal session count, for "of N" display |
| `tokens_remaining` | int | legacy/display only — `CEIL(minutes_remaining / base_minutes)` (a half-unit still shows ≥1) |
| `status` | text CHECK in (`pending`,`active`,`exhausted`,`expired`) | `pending` until paid |
| `purchased_at` | timestamptz NULL | set on activation |
| `expires_at` | date NULL | NULL = no expiry |
| `created_at` | timestamptz | |

> Reads also expose **`sessions_remaining`** (a fraction = `minutes_remaining / base_minutes`, e.g.
> 4.5) for the UI ("4.5 of 10 left"). Existing count wallets auto-migrate to minutes on boot
> (`tokens × base`).

### `billing.token_ledger` — audit + idempotency
| column | type | notes |
|---|---|---|
| `id` | bigserial PK | |
| `club_id` | uuid NOT NULL | tenant |
| `wallet_id` | uuid → token_wallet | |
| `booking_id` | uuid NULL | the diary.booking/enrolment the draw/credit is for |
| `kind` | text CHECK in (`draw`,`credit`,`grant`,`expire`) | |
| `delta` | int | signed, in **MINUTES** (draw = −drawn, credit = +exact-drawn, grant = +`sessions×base`) |
| `reason` | text | |
| `created_at` | timestamptz | |

**THE idempotency guard:** `UNIQUE (wallet_id, booking_id, kind)` **NULLS NOT DISTINCT** — a `draw`
and a `credit` are each recorded at most once per `(wallet, booking)`. The balance change is applied
**only when the ledger row actually inserts** (`ON CONFLICT DO NOTHING … RETURNING`), so replays are
strict no-ops. `grant`/`expire` rows carry `booking_id = NULL` (so at most one grant per wallet).

Idempotent `CREATE … IF NOT EXISTS` + `ADD COLUMN IF NOT EXISTS` throughout → `python -m db` twice is
a no-op (existing wallets backfill from the count balance once, `WHERE base_minutes IS NULL`).

---

## 2. Engine (`billing/bundles.py`) — pure SQL, explicit `session`, never commits

- `list_plans(club_id[, service_kind, coach_user_id], active_only=True)` + owner CRUD
  (`create_plan`, `update_plan`, `set_plan_status`, `deactivate_plan`=retire, `get_plan`). `active_only`
  hides dormant/retired from customers; admin/coach lists pass `active_only=False` and see `status`.
- `match_wallet(session, club_id, user_id, service_kind[, coach_user_id])` → the best active wallet.
  Match: `service_kind` equal; wallet `coach_user_id` = the booking's OR NULL (any); `status='active'`;
  **`minutes_remaining > 0`**; not past `expires_at`. **Duration is NO LONGER a match gate** — a pack
  covers any length (the draw computes the cost). Preference: expiring soonest (NULLs last), then
  fewest minutes left (drain partial packs), then oldest. `SELECT … FOR UPDATE` locks the wallet.
- `draw_token(session, wallet, booking_id, reason, duration_minutes)` → draw the booking's worth of
  **minutes**: its own duration (court/lesson) or one full unit `base_minutes` (a class = one session).
  **Customer-wins tail:** `drawn = LEAST(duration, minutes_remaining)` — never more than the balance, so
  the last credit covers a booking of any length and the wallet lands exactly at 0. Insert a
  `('draw', −drawn)` ledger row (idempotent unique); **only if it inserted**, `minutes_remaining −= drawn`
  (+ flip `active→exhausted` at 0; `tokens_remaining = CEIL(minutes/base)`). Runs **inside the caller's
  booking transaction**.
- `credit_token(session, booking_id, reason)` → find the wallet that drew for this booking; credit back
  **exactly the minutes that booking drew** (read the draw row's `delta`); insert `('credit', +minutes)`
  (idempotent unique); **only if inserted**, `minutes_remaining += credited` (+ reactivate
  `exhausted→active` when not expired). Never credits twice; tail-safe.
- `wallets_for(user_id[, service_kind])` → remaining (`sessions_remaining` fraction + minutes + expiry).
- `expire_due(club_id)` (lazy: flip past-`expires_at` `active→expired`; called opportunistically).
- `activate_wallet_for_order(session, order_id, provider='yoco')` — webhook activation
  (`pending→active`; `base_minutes`, `minutes_total = minutes_remaining = sessions_count × base`;
  `tokens_total = sessions_count`; `expires_at`), **idempotent keyed off `order_id`**.

---

## 3. Purchase + activation (mirrors membership)

- `GET  /api/billing/bundles?service_kind=` → active plans the member can buy.
- `GET  /api/billing/bundles/wallets` → the member's wallets (sessions/minutes remaining + expiry).
- `POST /api/billing/bundles/checkout {bundle_plan_id}` → an `online`/`awaiting_payment` order for
  `price_minor` + a `pending` `token_wallet` (carrying `base_minutes`) linked by `order_id`. Returns
  `{order_id}` → `Pay.startYocoCheckout(order_id)`.
- **Activation hook** in `yoco_billing/routes.yoco_webhook`, next to the membership hook: on a paid
  `charge_succeeded` whose order is a bundle purchase (`bundles.is_bundle_order`), call
  `bundles.activate_wallet_for_order` — idempotent (replay = still N sessions, never 2N).

**Commission:** a coach **lesson** pack's checkout order line carries that coach's lesson `billing.product`
(`price_id`), so the existing `record_split_for_order` fan-out attributes the collected purchase.
Court/class packs (no coach) → no split. The commission engine is fed a proper line, not rebuilt.

---

## 4. Booking integration (`settlement_mode='token'`)

The single translation point `diary/bookings._create_order_guarded` is used by BOTH `create_booking`
(court/lesson) AND `classes.enrol` (class). When `settlement_mode='token'`:
1. resolve the booking's `(service_kind, duration_minutes, coach_user_id)`,
2. `bundles.match_wallet(... FOR UPDATE)` (service_kind + coach + any positive balance),
3. if found → `bundles.draw_token(wallet, booking_id, duration_minutes)` (proportional minutes) and
   settle the order at **R0** with `settlement_mode='token'` (status `paid`, amount 0, booking confirmed),
4. if **no matching wallet** → return `NO_TOKEN` so the booking is rejected cleanly and the UI falls
   back to PAYG.

Because the draw and the diary.booking insert share ONE transaction, **a failed booking never burns
minutes, and burned minutes always have a confirmed booking.** `match_wallet`'s `FOR UPDATE` plus the
`token_ledger` unique guarantee no double-spend and no balance below zero under concurrency.

**Credit-back on cancel:** `cancel_booking` (court/lesson) and `classes.cancel_enrolment` (class) call
`bundles.credit_token(booking_id)` — idempotent, restoring the **exact** minutes drawn. Default policy:
**always credit back** (a too-late forfeit is a future option). `expire_due` runs lazily.

---

## 5. Owner / coach config + member UI

- **Owner** — `AdminUI.bundlePlans` (in `admin_api.js`) under the consolidated **Settings → Pricing**
  tab (`AdminUI.pricingHome`: court rates + packs + memberships in one place). CRUD `bundle_plan`s:
  service kind, label, #sessions, **base duration**, price, validity, coach (lesson packs), and the
  3-state lifecycle control (Active / Dormant / Retired). `/api/admin/bundle-plans*`.
- **Coach** — a coach configures **their own lesson packs** in the coach console's **Packs** tab
  (`CoachUI.packs`). `GET/POST /api/coach/bundle-plans` + `PATCH /api/coach/bundle-plans/<id>` force
  `service_kind='lesson'` + `coach_user_id = the principal` and guard ownership (a coach can only edit
  their own pack, else 404). Reuses the generic engine, so coach packs get the unit draw-down + lifecycle.
- **Member** — the **Packs** page (`frontend/app/packs.html` + `frontend/js/packs.js`): packs to buy +
  the member's wallets ("4.5 of 10 sessions left (270 min)" + expiry).
- **Booking** — `book.js` **auto-applies** a matching pack (no manual chip-hunt): a usable pack is the
  pre-selected settlement default, shown as **"Covered by your pack · R0"**; a free booking skips the
  payment chooser entirely (just confirm). When a pack runs dry, a re-buy nudge links to `/packs`.

---

## 6. Invariants (the careful bits)

1. **No double-spend / no negative balance** — `match_wallet` locks the wallet `FOR UPDATE`; the
   balance only decrements when the `draw` ledger row inserts (unique per `(wallet,booking)`);
   `minutes_remaining` CHECK ≥ 0 is the backstop.
2. **No lost minutes** — credit-back is idempotent, restores the exact minutes drawn, and only
   re-credits a wallet that actually drew for that booking; cancel can run twice with no double credit.
3. **Atomicity** — draw + booking are one tx (the diary caller's). A rollback (e.g. SLOT_TAKEN on the
   linked court) un-draws the minutes automatically.
4. **Customer wins** — a booking is never blocked while a positive balance remains; the last credit
   covers any length (`LEAST(duration, minutes_remaining)`).
5. **Idempotent grant** — purchase activation is keyed off `order_id`; a replayed webhook grants once.
6. **Generic + configurable** — every dimension (kind/base-duration/coach/count/price/validity/status)
   is data on `bundle_plan`; courts, lessons and classes all flow through the same `_create_order_guarded`
   seam.

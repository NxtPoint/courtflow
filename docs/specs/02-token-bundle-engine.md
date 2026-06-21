# 02 — Token / Bundle engine (prepaid session packs)

A **generic, owner-configurable prepaid-pack ("token bundle") capability** for CourtFlow.
A member buys a **pack of N prepaid sessions** upfront via Yoco; booking a matching service
**draws down** one token (settling the order at R0); cancelling **credits one back**. It is the
count-based sibling of **PAYG** (per-use) and **membership** (time-based).

It works generically across **courts, lessons, AND classes**, and is **fully configurable** by
the owner: any service kind, any session duration, any price, any number of sessions, optional
validity window, optional coach-specificity for lesson packs. **Nothing is hardcoded** — a pack
is data (a `billing.bundle_plan` row), exactly like membership term plans are data.

Design rules carried from the rest of billing:
- **Reuse, don't reinvent** — the purchase flow mirrors `billing/membership.py` (a pending row +
  an `awaiting_payment` online order linked by `order_id`; activation in the Yoco webhook next to
  `activate_membership_for_order`). The booking seam mirrors `membership_covered` (a new
  `settlement_mode='token'`). The commission feed reuses the existing engine via a proper order line.
- **Atomic, idempotent, no double-spend, no lost tokens** — the draw and the booking commit in ONE
  transaction; the draw/credit are each recorded at most once per booking (a `token_ledger` UNIQUE);
  concurrent draws are serialised with `SELECT … FOR UPDATE`.
- **Multi-tenant** — every row carries `club_id`.

---

## 1. Data model (3 tables, appended to `billing/schema.py`)

### `billing.bundle_plan` — the owner-configured offer (a pack you can buy)
| column | type | notes |
|---|---|---|
| `id` | uuid PK | |
| `club_id` | uuid NOT NULL → club.club | tenant |
| `service_kind` | text CHECK in (`court`,`lesson`,`class`) | what the pack's tokens are spendable on |
| `coach_user_id` | uuid NULL | lesson packs may be coach-specific; NULL = any coach |
| `label` | text | display name ("10 court sessions") |
| `sessions_count` | int NOT NULL | tokens granted on purchase |
| `duration_minutes` | int NULL | the per-token session length matched against the booking; NULL = any duration |
| `price_minor` | int NOT NULL | pack price (cents) |
| `validity_days` | int NULL | NULL = never expires; else `expires_at = today + validity_days` |
| `active` | bool | offered to members when true |
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
| `duration_minutes` | int NULL | denormalised (NULL = any) |
| `tokens_total` | int | granted on activation |
| `tokens_remaining` | int | the live balance (draw −1, credit +1) |
| `status` | text CHECK in (`pending`,`active`,`exhausted`,`expired`) | `pending` until paid |
| `purchased_at` | timestamptz NULL | set on activation |
| `expires_at` | date NULL | NULL = no expiry |
| `created_at` | timestamptz | |

### `billing.token_ledger` — audit + idempotency
| column | type | notes |
|---|---|---|
| `id` | bigserial PK | |
| `club_id` | uuid NOT NULL | tenant |
| `wallet_id` | uuid → token_wallet | |
| `booking_id` | uuid NULL | the diary.booking/enrolment the draw/credit is for |
| `kind` | text CHECK in (`draw`,`credit`,`grant`,`expire`) | |
| `delta` | int | signed (draw −1, credit +1, grant +N) |
| `reason` | text | |
| `created_at` | timestamptz | |

**THE idempotency guard:** `UNIQUE (wallet_id, booking_id, kind)` **NULLS NOT DISTINCT** — a `draw`
and a `credit` are each recorded at most once per `(wallet, booking)`. The balance change is applied
**only when the ledger row actually inserts** (`ON CONFLICT DO NOTHING … RETURNING`), so replays are
strict no-ops. `grant`/`expire` rows carry `booking_id = NULL` (the NULLS-NOT-DISTINCT means at most
one grant per wallet, which is what we want).

The block is appended at the very END of `billing/schema._DDL`, after the `refund_request` block,
under a clearly-marked `# --- token / bundle engine ---` banner. Idempotent `CREATE … IF NOT EXISTS`
throughout → `python -m db` twice is a no-op.

---

## 2. Engine (`billing/bundles.py`) — pure SQL, explicit `session`, never commits

- `list_plans(club_id[, service_kind], active_only=True)` + owner CRUD
  (`create_plan`, `update_plan`, `deactivate_plan`, `get_plan`).
- `match_wallet(session, club_id, user_id, service_kind, duration_minutes, coach_user_id)` →
  the best active wallet to draw from. Match: `service_kind` equal; wallet `duration_minutes` = the
  booking's OR NULL (any); wallet `coach_user_id` = the booking's OR NULL (any); `status='active'`;
  `tokens_remaining > 0`; not past `expires_at`. **Prefer the wallet expiring soonest**
  (use-it-or-lose-it; NULLs last), then fewest tokens remaining, then oldest. `SELECT … FOR UPDATE`
  locks the chosen wallet against concurrent draws.
- `draw_token(session, wallet, booking_id, reason)` → insert `('draw', −1)` (idempotent unique);
  **only if it actually inserted**, `tokens_remaining −= 1` (+ flip `active→exhausted` at 0). Returns
  whether a token was consumed. Runs **inside the caller's booking transaction**.
- `credit_token(session, booking_id, reason)` → find the wallet that drew for this booking; insert
  `('credit', +1)` (idempotent unique); **only if inserted**, `tokens_remaining += 1` (+ reactivate
  `exhausted→active` when not expired). Never credits twice.
- `wallets_for(user_id[, service_kind])` (remaining + expiry, for the member UI).
- `expire_due(club_id)` (lazy: flip past-`expires_at` `active→expired`; called opportunistically like
  `release_expired_holds`).
- `activate_wallet_for_order(session, order_id, provider='yoco')` — the webhook activation
  (`pending→active`, `tokens_remaining=sessions_count`, `expires_at`), **idempotent keyed off
  `order_id`** (an already-active wallet for this order is a no-op → no second grant).

---

## 3. Purchase + activation (mirrors membership)

- `GET  /api/billing/bundles?service_kind=` → active plans the member can buy.
- `GET  /api/billing/bundles/wallets` → the member's wallets (remaining + expiry).
- `POST /api/billing/bundles/checkout {bundle_plan_id}` → create an `online`/`awaiting_payment`
  order for `price_minor` + a `pending` `token_wallet` linked by `order_id`; the order line carries
  the plan's `coach_user_id`/product so commission can attribute it. Returns `{order_id}` →
  `Pay.startYocoCheckout(order_id)`.
- **Activation hook** in `yoco_billing/routes.yoco_webhook`, next to the membership hook: on a paid
  `charge_succeeded` whose order is a bundle purchase (`bundles.is_bundle_order`), call
  `bundles.activate_wallet_for_order` — idempotent (replay = still N tokens, never 2N).

**Commission:** a bundle purchase is a collected payment. When the plan is a **coach lesson** pack
(`coach_user_id` set), the checkout order line carries that coach's lesson `billing.product`
(`price_id`), so the existing `record_split_for_order` fan-out (invoked from `apply_payment_event`
on the paid order) attributes the commission on the collected purchase. Court/class packs (no coach)
resolve to a non-lesson product kind → no split. The commission engine is **not** rebuilt — it is just
fed a proper line.

---

## 4. Booking integration (`settlement_mode='token'`)

The single translation point `diary/bookings._create_order_guarded` is used by BOTH `create_booking`
(court/lesson) AND `classes.enrol` (class) — so handling `token` there gives generic coverage in one
place. When `settlement_mode='token'`:
1. resolve the booking's `(service_kind, duration_minutes, coach_user_id)`,
2. `bundles.match_wallet(... FOR UPDATE)`,
3. if found → `bundles.draw_token(wallet, booking_id)` and settle the order at **R0** with
   `settlement_mode='token'` (a new `free`-like mode: status `paid`, amount 0, booking confirmed),
4. if **no matching wallet** → return `NO_TOKEN` so the booking is rejected cleanly and the UI falls
   back to PAYG.

Because the draw and the diary.booking insert share ONE transaction, **a failed booking never burns a
token, and a burned token always has a confirmed booking.** `match_wallet`'s `FOR UPDATE` plus the
`token_ledger` unique guarantee no double-spend and no balance below zero under concurrency.

**Credit-back on cancel:** `cancel_booking` (court/lesson) and `classes.cancel_enrolment` (class) call
`bundles.credit_token(booking_id)` for any token-settled booking — idempotent, so re-cancel never
credits twice. Default policy: **always credit back** (a too-late forfeit is a future option, noted).
`expire_due` is called lazily at the top of availability/booking (cheap).

`settlement_mode='token'` is added to the `billing.order.settlement_mode` CHECK and the diary booking
status maps (`token → confirmed`, order `paid`, amount 0) — alongside `membership_covered`/`free`.

---

## 5. Owner config + member UI

- **Owner** — `admin/routes` + `admin/repositories` + `AdminUI.bundlePlans` (in `admin_api.js`),
  rendered under **Settings → Payments**. CRUD `bundle_plan`s: service kind, label, #sessions,
  duration, price, validity, coach (for lesson packs). `/api/admin/bundle-plans*`.
- **Member** — a new **Packs** page (`frontend/app/packs.html` + `frontend/js/packs.js`, mirroring
  membership): available packs to buy + the member's wallets (tokens remaining + expiry). A **Packs**
  nav link in `portal.js`.
- **Booking** — at the Pay/confirm step, `book.js` offers **"Use 1 token (N left)"** when the member
  has a matching active wallet for the chosen service+duration(+coach) → submits
  `settlement_mode='token'`. The existing online seam (`res.booking.order_id` →
  `Pay.startYocoCheckout`) is preserved untouched.

---

## 6. Invariants (the careful bits)

1. **No double-spend / no negative balance** — `match_wallet` locks the wallet `FOR UPDATE`; the
   balance only decrements when the `('draw')` ledger row inserts (unique per `(wallet,booking)`).
2. **No lost tokens** — credit-back is idempotent and only re-credits a wallet that actually drew for
   that booking; cancel can run twice with no double credit.
3. **Atomicity** — draw + booking are one tx (the diary caller's). A rollback (e.g. SLOT_TAKEN on the
   linked court) un-draws the token automatically.
4. **Idempotent grant** — purchase activation is keyed off `order_id`; a replayed webhook grants once.
5. **Generic + configurable** — every dimension (kind/duration/coach/count/price/validity) is data on
   `bundle_plan`; courts, lessons and classes all flow through the same `_create_order_guarded` seam.

# SYSTEM — architecture (how it's wired)

CourtFlow is a **multi-tenant, white-label tennis-club management platform**. NextPoint Tennis is
club #1. It re-assembles ~80% of the proven Ten-Fifty5 ("1050") architecture around one new domain
model: the **diary** (booking). See [INVENTORY.md](INVENTORY.md) for the exhaustive list.

## Services
Two Render web services + one Postgres:
- **`courtflow-api`** (`wsgi:app`) — Flask + Gunicorn + psycopg. Owns the DB, all `/api/*` business
  logic, Clerk-JWT auth. Boots + idempotently creates all schemas on start.
- **`courtflow-web`** (`web_wsgi:app`) — DB-less. **Host-switched** (`web_app.py`, mirroring 1050's
  `locker_room_app.py`): a marketing host serves the public site at `/` and the portal SPA shells at
  `/portal`, `/book`, `/admin`, …; a club host would serve that club's branded site. Serves static
  `cf-*` pages + JS, proxies nothing — the SPAs call `courtflow-api` directly.
- **Postgres** (separate Render DB). **One DB, six schemas** (below).

The browser holds a **Clerk** session; the SPA attaches the JWT to every `/api/*` call; the API verifies
it (JWKS) and resolves a club-scoped `Principal`.

## Auth & multi-tenancy (the spine)
`auth/principal.py`: verify the Clerk JWT → upsert `iam.user` (link-by-email) → load memberships →
resolve the **active `(club_id, role)`**. Resolution order: explicit `X-Club` header (admin switcher) →
host→club → the user's single membership → platform_admin wildcard. **New rule:** a user with **no**
membership is **auto-enrolled as an active `member`** of the host/sole club (so new sign-ups land in the
portal, defaulting to PAYG; admins/coaches are seeded/invited).

`Principal{user_id, club_id, role, email}`. Roles: `platform_admin`, `club_admin`, `coach`, `member`,
`guest`. **Every domain query is `club_id`-scoped** — multi-tenant is a discipline (RLS is a future
phase). The client can never assert a `club_id`; it's derived server-side.

## The five schemas (idempotent boot DDL — NO migration framework)
`club` (tenants/branding/location/policy) · `iam` (identity/membership/coach/dependents) · `diary`
(the booking engine) · `billing` (orders/ledger + the commercial engines) · `core` (ported 1050
account/usage_event/consent/nps + notifications). *(The Business Overview analytics are read-only
views over `core.usage_event` — no separate schema.)*
`db.py` runs each registered module's `init()` on boot; **`python -m db` twice must be a no-op** — that's
the schema gate. Extensions: `btree_gist` (the no-double-book EXCLUDE constraint), `pgcrypto` (UUIDs).

## The diary (the heart)
`diary/` computes availability **on read** (expand `availability_rule` − `time_off` − bookings −
class_sessions), and books with a Postgres **GiST EXCLUDE** constraint so two bookings can never overlap
a resource (concurrency-safe; the loser gets `SLOT_TAKEN`). Lessons reserve a **coach ∩ court**
intersection (a lesson holds a court too, one `order_id` across both rows). Classes have capacity +
waitlist (auto-promote on cancel). **Lazy expiry replaces the capacity-sweep cron:**
`release_expired_holds` runs at the top of availability + booking, cancelling `held` rows past
`held_until` — no paid cron needed.

## Billing & the commercial engines
`billing/` core (`orders`, `ledger`, `gateway` registry, `apply_payment_event` — idempotent) is
provider-agnostic. On top of it:
- **`yoco_billing/`** — the Yoco adapter (hosted checkout, Standard-Webhooks verify, refund, reconcile,
  receipt) behind `register_gateway`/`get_gateway`. `billing/` core is untouched.
- **`billing/membership.py`** — configurable membership **term plans** (price × duration).
- **`billing/bundles.py`** — the generic **token/bundle** engine (prepaid session packs; atomic
  draw-down, idempotent credit-back) across court/lesson/class.
- **`billing/commission.py`** — the coach **commission/rent** engine: scoped dated rules
  (`coach+product > product > coach > club`), split computed **on collection** inside `apply_payment_event`
  (idempotent), arrears statement, owner cockpit aggregations.
- **`billing/refunds.py`** — client refund-request workflow + admin approve/decline.
- **`billing/me.py`** — client financial reads.

## Events, CRM & notifications
Producers call `marketing_crm.emit(event, payload)` → writes `core.usage_event` (the decoupled event
contract, `contracts/events.md`). `emit()` also drives **notifications** (`marketing_crm/notifications.py`,
non-fatal): mapped transactional kinds → a `core.notification` (in-app inbox, always) + a transactional
email (SES, dark without keys). Klaviyo lifecycle flows hang off the same event feed (dark without
`KLAVIYO_API_KEY`). Child bookings route notifications to the **guardian**.

## Request flow (a booking, end to end)
1. SPA `book.js`: pick service → duration (live per-duration price, or "covered by membership") →
   schedule (calendar + coach/court "Any") → settlement (at-court / monthly / membership / **token** /
   online).
2. `POST /api/diary/bookings` → `create_booking` (club-scoped, exclusion-constrained); creates the order
   per settlement mode (online → `awaiting_payment` + booking `held`; token → draws a wallet token at R0;
   membership-covered → R0).
3. Online: `book.js` reads **`res.booking.order_id`** → `Pay.startYocoCheckout` → Yoco hosted page →
   `POST yoco/webhook` (verified) → `apply_payment_event` → order `paid` + booking `confirmed` +
   commission split accrued (if a coach lesson) + `emit('payment_succeeded')` → receipt notification.
4. Cancel → frees the slot, credits a token back / records a refund as configured.

## Deploy
Render auto-deploys `master` (push → both services rebuild). Go-live flags are committed in
`render.yaml` so a blueprint sync can't wipe them; secrets are `sync:false`. Free plan = cold starts +
no Shell (hence `SEED_NEXTPOINT=1` boot seed) + no paid crons (hence lazy expiry + on-read accrual +
the reconcile sweep for missed webhooks).

## Key conventions
- **Nothing hardcoded** — prices/durations/plans/commission/bundles are owner-configured data
  (white-label). Build configurable *capabilities*.
- **Idempotent everything** — boot DDL, payment events, commission splits, token draws, notifications.
- **Vanilla-JS SPAs**, one `cf-*` design system (`app.css`), absolute asset/nav links.
- **Reuse, don't import** from `C:\dev\webhook-server` (1050, READ-ONLY).

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
  `/portal`, `/book`, `/admin`, `/settings`, …; a club host would serve that club's branded site. Serves
  static `cf-*` pages + JS, proxies nothing — the SPAs call `courtflow-api` directly. **Nav is
  role-focused** (`Portal.landingFor`): members/guests land on the client **Home · Account**, coaches on
  their **Coach** console, owners on **Admin · Settings**. The coach and owner consoles are
  **business-health-first cockpits**: the **Coach console** (`coach.html`, business tabs Dashboard ·
  Schedule · Clients · Money · Setup — cockpit KPIs, a week-timeline schedule, and the settlement statement
  folded into Money) and the **Admin console** (`admin.html`, Dashboard · Diary · People · Money · Insights
  — a money-KPI + Today-at-the-club dashboard, the master diary, the financial cockpit, and the analytics
  Overview), with **Settings** (`settings.html`) holding configuration (Club profile · Courts & hours ·
  Services · Memberships · Coaches).
- **Postgres** (separate Render DB). **One DB, five schemas** (below).

The browser holds a **Clerk** session; the SPA attaches the JWT to every `/api/*` call; the API verifies
it (JWKS) and resolves a club-scoped `Principal`.

## Front-end — three role SPAs (the 2026-07-02 redesign)
The tab-based consoles above are superseded by **three mobile-first, drill-through single-page apps**, one
per role, all built on the **one shared design system** (`frontend/app/app.css`, every page in `cf-*`
classes — the single source; no inline component styles). **GOLDEN RULE:** each app has exactly **one**
booking capability — the "**event story**" — and every list row (a session, a billing line, a client's
service) drills into that same story; there is never a second booking sheet.
- **Client** — `frontend/app/app.html` + `frontend/js/client.js`. ONE page, no bottom nav: Home (book tiles
  + Your sessions + Billing-by-category) drilling into the booking story (`GET /api/me/bookings/<id>`) and
  the ORDER-based billing view (`GET /api/me/billing/summary`). Served at `/`, `/portal`, `/app`.
- **Coach** — `frontend/app/coach_app.html` + `frontend/js/coach_app.js`. Bottom nav Home · Schedule ·
  Clients · Money · Setup; Schedule is a weekly calendar; the **one coach event story**
  (`GET /api/coach/bookings/<id>`) carries the arrears actions (mark-collected / discount / write-off).
  Served at `/coach` (non-coaches bounced).
- **Admin (in progress)** — `frontend/app/admin_app.html` + `frontend/js/admin_app.js`, served at
  **`/admin-app`** (the classic `/admin` console stays live until sign-off). It is **responsive** —
  bottom-nav on mobile, a **left side-rail on desktop** (`.cf-admin`). Step 1 shipped: shell + nav +
  command-center Home (`GET /api/admin/home`); steps 2–7 follow the build order in
  [ADMIN-REDESIGN.md](ADMIN-REDESIGN.md). Full blueprint: [FRONTEND-REDESIGN.md](FRONTEND-REDESIGN.md).

Old standalone pages 302-redirect into the client SPA; `admin.html`/`coach.html` are kept as fallbacks.

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
intersection (a lesson holds a court too, one `order_id` across both rows); `create_booking` auto-assigns
a free court and refuses without a free coach AND court. **Lesson approval lifecycle:** a coach with
`iam.coach_profile.review_bookings` ON turns a client's self-booked lesson into a **`requested`** booking
that reserves NOTHING until the coach **accepts** (auto-assign court + settle → `confirmed`), **proposes**
a new time (→ **`proposed`**, awaiting the client), or **declines** (→ `cancelled`); on-behalf bookings
always auto-confirm. `requested`/`proposed` are outside the GiST exclusion (they hold no slot). Classes
have capacity + waitlist (auto-promote on cancel). **Lazy expiry replaces the capacity-sweep cron:**
`release_expired_holds` runs at the top of availability + booking, cancelling `held` rows past
`held_until` — no paid cron needed.

## Billing & the commercial engines
`billing/` core (`orders`, `ledger`, `gateway` registry, `apply_payment_event` — idempotent) is
provider-agnostic. On top of it:
- **`yoco_billing/`** — the Yoco adapter (hosted checkout, Standard-Webhooks verify, refund, reconcile,
  receipt) behind `register_gateway`/`get_gateway`. `billing/` core is untouched.
- **`billing/statement.py`** — the **single source of truth for what a client owes** (full spec:
  [UNIFIED-STATEMENT.md](UNIFIED-STATEMENT.md)). The invariant: one debt = one `billing.order`, settled
  exactly once. A client owes the SUM of their unpaid orders (`status='open'`) — nothing else; `account_ledger`
  and `coach_arrears` are **demoted to internal** consequences of a paid order, held in lockstep so they can
  never double-count the debt. A **settlement order** (new column `billing.order.settled_by_order_id` links a
  child owed order to its 'pay all' parent) is the pay-all/part-pay vehicle: `create_settlement_order(order_ids?)`
  builds it, and on its `charge_succeeded` the **`billing/events.py` fan-out** (`settle_settlement_order`) marks
  each child order paid + accrues its commission once. `void_order` writes off / voids a debt (a paid order can't
  be voided).
- **`billing/membership.py`** — configurable membership **term plans** (price × duration). Plans buy
  **online OR offline** (`create_membership_order(settlement_mode)`: online → `awaiting_payment` + webhook
  activate; at-court/monthly → an `open` owed order that **activates immediately**).
- **`billing/bundles.py`** — the generic **token/bundle** engine (prepaid session packs) across
  court/lesson/class. **Unit/minute-based:** a pack covers any length, drawing minutes proportional to
  the booking's duration (90min off a 60-unit = 1.5 sessions; class = one full unit); customer-wins tail;
  atomic draw-down, idempotent credit-back of the exact minutes. Packs also buy **online OR offline**
  (`create_bundle_order(settlement_mode)`: offline → an `open` order + grant the wallet immediately).
  Coaches configure their own lesson packs (`/api/coach/bundle-plans`). Catalogue items (services,
  memberships, packs) share **ONE lifecycle vocabulary** — Active / Deactivated / Terminated
  (`billing.product.status`; memberships derive theirs from their term plans' active/dormant/retired
  state) — with filter bars, status chips and per-row Deactivate/Reactivate/Terminate actions.
- **Free week** — new members are auto-granted a 7-day courts-free trial membership on signup
  (`billing.membership.grant_signup_trial`, `provider='trial'`, fired from `auth/principal.py`;
  `SIGNUP_TRIAL_DAYS` env). **Membership access windows** — a tier can be time-boxed
  (`billing.price.access_days/access_start_min/access_end_min`), enforced server-side by
  `diary.pricing.membership_covers(starts_at)` (outside the window → PAYG). Off-peak coverage is priced
  **per slot**: `compute_availability` surfaces R0 only inside a member's window and PAYG outside it
  (`diary.pricing.active_membership_windows` / `any_window_covers`), so the calendar's free/charged display
  matches what `create_booking` actually charges.
- **`billing/commission.py`** — the coach **commission/rent** engine: scoped dated rules
  (`coach+product > product > coach > club`), split computed **on collection** inside `apply_payment_event`
  (idempotent), arrears statement, owner cockpit aggregations.
- **`billing/refunds.py`** — client refund-request workflow + admin approve/decline.
- **`billing/me.py`** — client financial reads.

**The payment rule (one shared rule across every purchase).** What payment methods a purchase offers is
configurable per service (`billing.product.payment_modes`) and **per membership tier** (new column
`billing.price.payment_modes`), resolved in layers (tier price-pref → product default → the club's globally
enabled methods). The front end (`Pay.purchase` → `buyMembership`/`buyPack` in `pay.js`, and `booking.js`)
applies ONE rule: more than one allowed mode → the client chooses; exactly one non-online mode → check out
immediately (no prompt); online → Yoco. Offline modes settle through the unified statement as `open` orders.

## Events, CRM & notifications
Producers call `marketing_crm.emit(event, payload)` → writes `core.usage_event` (the decoupled event
contract, `contracts/events.md`; includes the `lesson_requested|proposed|accepted|declined` lifecycle
events). `emit()` also drives **notifications** (`marketing_crm/notifications.py`, non-fatal): mapped
transactional kinds → a `core.notification` (in-app inbox, always) + a transactional email (SES).
Child bookings route notifications to the **guardian**. Every booking has a downloadable **`.ics`**
(`diary/calendar.py` → `GET /api/diary/bookings/<id>/calendar.ics`; `ics_url` on the confirmation
payload) — in-app "Add to calendar" now, the email attaches the same file once email is live.

**Transactional email — multi-tenant SES (code-complete, config-gated).** Improving on 1050's
single-tenant bare-From sender: ONE verified CourtFlow domain (`SES_SENDER`, e.g. `no-reply@courtflow.app`)
carries **every** club, so adding a tenant needs no new SES verification. Each club rides it with its OWN
identity — a **From display name** (`club.name`) + **Reply-To** (its first `club.location` email) —
resolved by `marketing_crm/notifications.py::_club_identity`. `marketing_crm/email/ses.py` self-gates on
creds (no `SES_SENDER` → email is dark, in-app notifications only, never errors); `send_raw_email` (MIME
`SendRawEmail`) attaches the booking **.ics** to confirmations (the piece 1050 lacked), `send_email` takes
`from_name`/`reply_to`, and `send_booking_confirmation` is club-branded. `notifications.deliver` threads
the club identity into every mapped event. No new endpoints, no schema change. Klaviyo lifecycle flows hang
off the same event feed (dark without `KLAVIYO_API_KEY`). Config to go live (AWS only): verify the domain
in SES `af-south-1`, exit the sandbox, set `SES_SENDER` + the club contact email — full guide in
[SES-SETUP.md](SES-SETUP.md).

## Request flow (a booking, end to end)
1. SPA `booking.js` (full-screen): pick service → schedule on a month calendar with **inline per-duration
   price** (or "covered by membership"; coach/court default "Any") → settlement (at-court / monthly /
   membership / **token** / online / free).
2. `POST /api/diary/bookings` → `create_booking` (club-scoped, exclusion-constrained); creates the order
   per settlement mode (online → `awaiting_payment` + booking `held`; token → draws a wallet token at R0;
   membership-covered → R0). A **gated lesson** (review-coach, client self-book) → `requested`, with **no
   order/court** until the coach accepts.
3. Online: `booking.js` reads **`res.booking.order_id`** → `Pay.startYocoCheckout` → Yoco hosted page →
   `POST yoco/webhook` (verified) → `apply_payment_event` → order `paid` + booking `confirmed` +
   commission split accrued (if a coach lesson) + `emit('payment_succeeded')` → receipt notification.
4. Cancel/withdraw → frees the slot (also cancels a pending `requested`/`proposed` lesson), credits a
   token back / records a refund as configured.

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

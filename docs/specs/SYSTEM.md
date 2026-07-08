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
  their **Coach** console, owners on **Admin**. The role apps are the three drill-through SPAs described
  in the next section; the classic tab consoles have been retired (the owner's is preserved at
  `/admin-classic` as a fallback).
- **Postgres** (`courtflow-db`, a separate Render DB). **One DB, five schemas** (below).

All three run in Render's **Frankfurt** region — both web services are **co-located with the DB** (the API
uses the DB's internal same-region URL). *(Until 2026-07-05 the web services were mistakenly in Oregon while
the DB was in Frankfurt, so every query crossed the Atlantic; Render's region is immutable per service, so
the fix was to delete + recreate both from the blueprint in-region.)*

The browser holds a **Clerk** session; the SPA attaches the JWT to every `/api/*` call; the API verifies
it (JWKS) and resolves a club-scoped `Principal`.

## Front-end — three role SPAs + the shared widget architecture (2026-07 redesign, COMPLETE)
Three mobile-first, drill-through single-page apps, one per role, on the **one shared design system**
(`frontend/app/app.css`, every page in `cf-*` classes — no inline component styles).
- **Client** — `app.html` + `client.js`. ONE page, no bottom nav: Home (book tiles + Your sessions +
  Billing-by-category) drilling into the event story (`GET /api/me/bookings/<id>`) + the ORDER-based
  billing view (`GET /api/me/billing/summary`). Served at `/`, `/portal`, `/app`.
- **Coach** — `coach_app.html` + `coach_app.js`. Bottom nav Home · Schedule · Clients · Money · Setup;
  Schedule is an hour-by-hour week time-grid (+ time-off + book-a-client); the event story
  (`GET /api/coach/bookings/<id>`) carries the arrears actions. Served at `/coach` (+ `/coach.html`).
- **Admin / Owner — COMPLETE + LIVE** — `admin_app.html` + `admin_app.js`, served at **`/admin`** (also
  `/admin.html` and `/admin-app`). Responsive: bottom-nav on mobile, a **left side-rail on desktop**
  (`.cf-admin`). Home (command-center, `GET /api/admin/home`) · People (roster → unified person 360,
  `GET /api/admin/people/<id>`) · Money (Setup-style sections incl. **Sales by day**) · Diary (the shared
  Calendar widget + Classes — **Day view = resource-timeline grid**, Week/Month agenda, blocks drill to the
  event story) · **Overview** (first-class nav tab since 2026-07-05: month pager + ECharts sub-tabs
  Traffic/Bookings/Revenue/Members/NPS/Courts on `GET /api/insights/overview`; Courts = the court-utilisation
  heatmap) · Setup. Money also carries **Bookings by day** next to Sales by day. Booking counts across
the console **exclude the auto-held court row of a lesson** (it shares the lesson's order, so a lesson
counts ONCE, not as a lesson + a phantom court) — a NULL-safe `notes IS DISTINCT FROM '(court held for
lesson)'` filter applied in `insights.repositories` (`bookings_by_day` + `overview`), the person-360
(`admin.repositories.get_person`) and `diary.list_bookings`. The **classic tab console**
  is preserved at **`/admin-classic`** (its full drag-timeline **editing** — walk-in/block-time/desk-pay —
  is not yet ported). `admin.html`/`admin.js` remain on disk; the dead classic **coach** console
  (`coach.js`/`coach.html`) was deleted.

**GOLDEN RULE — one widget per capability** (the enshrined frontend architecture; full detail in
[FRONTEND-STANDARDISATION.md](FRONTEND-STANDARDISATION.md)). Every capability is rendered by exactly ONE
widget shared across the three apps; role differences are **configuration** — a `data` adapter, an
`actions` capability-map, `fields`, `onNavigate` — never forked render code. A second render of "a
transaction" / "the calendar" is a bug: extend the widget's config. Shared widgets (`window.Widgets`,
`frontend/js/widgets/`): **TransactionDetail** (the one event story — every session/billing-line/service
row across all three apps drills into it), **Calendar** (the admin diary agenda; the coach time-grid +
client Home agenda are kept as legitimately distinct views), and **Setup** + **ServiceList** (owner +
coach share the gold-standard Setup). Common DOM helpers
(`card/backBar/kv/modal/toLocal/addToCalendar/statusChip`) live once on `window.UI`; composed presenters
(`stats/lineItems/activityFeed/…`) on `window.CRMUI`.

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
have capacity + waitlist (auto-promote on cancel) and can **optionally reserve a court**
(`class_session.court_resource_id`/`court_booking_id` — a GiST-blocking booking, freed on cancel).
**Lazy expiry replaces the capacity-sweep cron:**
`release_expired_holds` runs at the top of availability + booking, cancelling `held` rows past
`held_until` — no paid cron needed.

**Coach/product-scoped pricing is STRICT TWO-TIER.** `diary/pricing.py` resolves a service's rate card
against the coach's **own** active product if they have one (`_coach_has_own_product`), **else** the
shared (NULL-coach) product — the two tiers are **never merged** (mixing leaked phantom durations +
zero-rated prices). `price_for` / `durations_for` / `payment_modes_for` all honour this, and
`services_for(club_id, kind, coach_user_id, audience)` returns the per-product picker list
(`{product_id, name, payment_modes, currency_code, durations:[…]}`) so a coach with several services
(e.g. Private vs Semi-private) offers each separately.

## Billing & the commercial engines
`billing/` core (`orders`, `ledger`, `gateway` registry, `apply_payment_event` — idempotent) is
provider-agnostic. `billing.orders.reprice_booking_order(club_id, booking_id, duration_minutes)`
re-prices an **unpaid** booking order (+ its owed coaching arrears) to a new duration's price from the
**same product** (so a rescheduled lesson is charged its actual length, never another coach's rate); a
guarded no-op when the order is settled, a real charge has succeeded, it's an R0 mode
(membership/token/free), or the new duration has no configured price. On top of the core:
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
Child bookings route notifications to the **guardian**. Booking/class emails carry a **rich detail
block** (`marketing_crm/email/booking_detail.py`, `DETAIL_KINDS`) — a guarded, read-only lookup
(`load` → `html_block`/`text_block`) that renders the full booking under the green banner: client
name+surname · email · cell · service (via `order_line→price→product`) · date & time in the club's
timezone (**SAST**/`Africa/Johannesburg`, fixed +02:00 fallback where no zoneinfo) · court · coach ·
duration · price + payment status. On a **lesson** the coach is **BCC'd** (`booking_detail.coach_email`
adds to the `bcc` list in `notifications.py`). Every booking has a downloadable **`.ics`**
(`diary/calendar.py` → `GET /api/diary/bookings/<id>/calendar.ics`; `ics_url` on the confirmation
payload) — in-app "Add to calendar" works now; the email *attachment* is gated OFF (`EMAIL_ICS_ENABLED=0`).

**Transactional email — multi-tenant SES — LIVE.** Improving on 1050's single-tenant bare-From sender:
ONE verified domain (`SES_SENDER`) carries **every** club, so adding a tenant needs no new SES
verification. Each club rides it with its OWN identity — a **From display name** (`club.name`) + **Reply-To**
(its first `club.location` email) — resolved by `marketing_crm/notifications.py::_club_identity`.
`marketing_crm/email/ses.py` self-gates on creds and takes its OWN AWS keys (`SES_AWS_ACCESS_KEY_ID` /
`SES_AWS_SECRET_ACCESS_KEY` / `SES_REGION`) so it can ride a different AWS account from S3; `send_email`
takes `from_name`/`reply_to` and `send_booking_confirmation` is club-branded; `send_raw_email` (MIME
`SendRawEmail`) attaches the booking **.ics** — **gated by `EMAIL_ICS_ENABLED` (default `0`)** until the
sending key carries `ses:SendRawEmail`. `notifications.deliver` threads the club identity into every mapped
event. No new endpoints, no schema change. **Running now** via the **interim** Ten-Fifty5 AWS account
(`eu-north-1`, `SES_SENDER=noreply@ten-fifty5.com`) — the long-term proper CourtFlow-domain setup is in
[SES-SETUP.md](SES-SETUP.md). Klaviyo lifecycle flows hang off the same event feed (dark without
`KLAVIYO_API_KEY`). Diagnostic: `POST /api/cron/ses-selftest?to=<email>` (OPS-guarded).

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
Render auto-deploys `master` (push → both services rebuild). Both web services + the DB are pinned to the
**Frankfurt** region and the **Starter** plan in `render.yaml`. Go-live flags are committed in `render.yaml`
so a blueprint sync can't wipe them (`SEED_NEXTPOINT=1` boot seed, `SES_REGION=eu-north-1`); secrets are
`sync:false`. No paid crons (hence lazy expiry + on-read accrual + the reconcile sweep for missed webhooks).

## Key conventions
- **Nothing hardcoded** — prices/durations/plans/commission/bundles are owner-configured data
  (white-label). Build configurable *capabilities*.
- **Idempotent everything** — boot DDL, payment events, commission splits, token draws, notifications.
- **Vanilla-JS SPAs**, one `cf-*` design system (`app.css`), absolute asset/nav links.
- **Reuse, don't import** from `C:\dev\webhook-server` (1050, READ-ONLY).

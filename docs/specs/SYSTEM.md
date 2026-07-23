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
  in the next section; the classic tab consoles have all been retired (the owner's classic console +
  `/admin-classic` were DELETED 2026-07-18 — `/admin-classic` now 301→`/admin`).
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
  billing view (`GET /api/me/billing/summary`). Served at `/`, `/portal`, `/app`. Home and the 360 rollup
  open on a **month-at-a-glance** summary (`GET /api/me/activity-summary`) rendered by the shared
  `CRMUI.activityBlock`/`spendBlock`/`weekChart` presenters (month navigation, an AI-styled analysis panel,
  no emoji) — the same blocks the person-360 uses, so the client sees exactly what staff see. **Also embeds
  Ten-Fifty5** (`#/analysis`, 2026-07-11): the separate live 1050 product (AI match analysis) iframed inside
  the members area, the member signed in with their OWN NextPoint Clerk token relayed via `postMessage` — see
  "Cross-app SSO" under Auth below.
- **Coach** — `coach_app.html` + `coach_app.js`. Bottom nav Home · Schedule · Clients · Money · Setup;
  Schedule is an hour-by-hour week time-grid (+ time-off + book-a-client); the event story
  (`GET /api/coach/bookings/<id>`) carries the arrears actions. Served at `/coach` (+ `/coach.html`).
- **Admin / Owner — COMPLETE + LIVE** — `admin_app.html` + `admin_app.js`, served at **`/admin`** (also
  `/admin.html` and `/admin-app`). Responsive: bottom-nav on mobile, a **left side-rail on desktop**
  (`.cf-admin`). Home (command-center, `GET /api/admin/home`) · People (roster → unified person 360,
  `GET /api/admin/people/<id>`) · Money (Setup-style sections incl. **Club earnings** + **Sales by day**) · Diary (the shared
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
(`stats/lineItems/activityFeed/statementFold/moneySummary/…`) on `window.CRMUI` — including the **ONE shared
create-client modal** (`CRMUI.createClientModal`: first-name/surname split + phone country-code, +27 default;
used by admin AND coach — coach `POST /api/coach/clients` reuses `admin.create_client`, admin
`PATCH /api/admin/clients/<id>` edits via `iam.patch_profile`) and the shared **add-a-player** modal
(`CRMUI.addLessonPlayerModal`, serving both the upfront squad step and add-later).

## Auth & multi-tenancy (the spine)
`auth/principal.py`: verify the Clerk JWT → upsert `iam.user` (link-by-email) → load memberships →
resolve the **active `(club_id, role)`**. Resolution order: explicit `X-Club` header (admin switcher) →
host→club → the user's single membership → platform_admin wildcard. **New rule:** a user with **no**
membership is **auto-enrolled as an active `member`** of the host/sole club (so new sign-ups land in the
portal, defaulting to PAYG; admins/coaches are seeded/invited).

`Principal{user_id, club_id, role, email}`. Roles: `platform_admin`, `club_admin`, `coach`, `member`,
`guest`. **Every domain query is `club_id`-scoped** — multi-tenant is a discipline (RLS is a future
phase). The client can never assert a `club_id`; it's derived server-side.

**Cross-app SSO — the Ten-Fifty5 embed (2026-07-11).** NextPoint and Ten-Fifty5 (the 1050 product) are
**separate Clerk apps** (`clerk.nextpointtennis.com` vs `clerk.ten-fifty5.com`), so a NextPoint token doesn't
natively verify on Ten-Fifty5. To sign a member into the embedded Ten-Fifty5 iframe with no second login:
the NextPoint portal (parent) mints its own Clerk JWT and relays it to the iframe via the shared
`auth_client.js` `postMessage` seam (a **multi-hop** relay — Ten-Fifty5's portal nests each page in a content
iframe, so a middle frame proxies its grandchild's request up to its own parent). Ten-Fifty5's verifier was
taught to **also** trust NextPoint's issuer (**multi-issuer federation**); **email** is the cross-system
identity key (Ten-Fifty5 auto-provisions the member by email). The NextPoint side only *relays* the token —
the verifier change lives in the Ten-Fifty5 repo (`auth_v2/verifier.py`). Full write-up: root `CLAUDE.md`.

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
a free court and refuses without a free coach AND court. A lesson service may be **semi-private (squad)** —
`billing.product.max_clients > 1` lets >1 client ride the ONE slot with **per-head billing**:
`create_booking(extra_clients=[…])` (and `add_lesson_partner` for an add-later) records each extra as a
`diary.booking_party(role='partner')` + a **separate order** at the service price (never merged), billed to
the player or — for a login-less child — their **guardian** (`_bill_owner` → `iam.guardian_user_id_for`);
cancelling voids EVERY order on the booking. The staff-only picker `GET /api/diary/members/search` →
`iam.search_members_with_dependents` lists members + a parent's kids as their own rows. **Lesson approval lifecycle:** a coach with
`iam.coach_profile.review_bookings` ON turns a client's self-booked lesson into a **`requested`** booking
that reserves NOTHING until the coach **accepts** (auto-assign court + settle → `confirmed`), **proposes**
a new time (→ **`proposed`**, awaiting the client), or **declines** (→ `cancelled`); on-behalf bookings
always auto-confirm. `requested`/`proposed` are outside the GiST exclusion (they hold no slot). Classes
have capacity + waitlist (auto-promote on cancel) and can reserve **one or more courts**
(`diary.class_session_court` link table + the scalar `class_session.court_resource_id`/`court_booking_id`
for legacy readers — each a GiST-blocking `diary.booking(booking_type='class')`, auto-repicked if busy,
freed on cancel). A class **enrolment goes through the same money path as a booking**: an `online`
enrolment creates an `awaiting_payment` order and the frontend drives Yoco (was previously confirmed
unpaid — the paywall bypass, fixed 2026-07-10).
**Lazy expiry replaces the capacity-sweep cron:**
`release_expired_holds` runs at the top of availability + booking, cancelling `held` rows past
`held_until` — no paid cron needed. **Classes have the same seam:** an unpaid `online` enrolment holds its
seat (`diary.enrolment.held_until`); `release_expired_enrolments` (top of `list_sessions` + `enrol`)
cancels the lapsed seat, voids its still-`awaiting_payment` order and promotes the waitlist — a paid seat
is never touched.

**Coach/product-scoped pricing is STRICT TWO-TIER.** `diary/pricing.py` resolves a service's rate card
against the coach's **own** active product if they have one (`_coach_has_own_product`), **else** the
shared (NULL-coach) product — the two tiers are **never merged** (mixing leaked phantom durations +
zero-rated prices). `price_for` / `durations_for` / `payment_modes_for` all honour this, and
`services_for(club_id, kind, coach_user_id, audience)` returns the per-product picker list
(`{product_id, name, payment_modes, currency_code, durations:[…]}`) so a coach with several services
(e.g. Private vs Semi-private) offers each separately.

**Court SERVICES (per-court-group court hire).** Courts can belong to distinct court services — e.g.
"Hardcourt Hire" over the hard courts vs "Clay Hire" over the clay court — each a
`billing.product(kind='court_booking')` with its **own** per-duration prices (multiple court products are
now supported) and its **own** allocated courts (`diary.resource.product_id`). `diary/pricing.py::
court_service_for_resource` resolves a court's service: the court's own `product_id`, else the club's
single default court product, else the unscoped product. `price_for` / `durations_for` / availability /
`create_booking` are all **court-service-aware** (fixing the old "cheapest across court products" leak), and
a court booked under the wrong service is rejected (`COURT_NOT_IN_SERVICE`). **Single-court-service clubs
are unchanged.** The client picks a court service like a lesson service and sees only its courts at its price.

**Membership entitlement + peak pricing + equipment (2026-07-12).** A new **`diary/entitlement.py`** is the
ONE resolver for "what does this member get at this time" — read by BOTH `compute_availability` (to shape the
shown options/prices) AND `create_booking` (to enforce), so **shown == charged == allowed**. It layers the
SILENT anti-abuse caps on top of the access window: `max_covered_minutes` (over-length durations are hidden
from the member's picker), `max_covered_per_day`, `max_courts_per_day`, and a court-service `members_covered`
flag (a clay court sold PAYG-only). Every cap **downgrades to PAYG, never blocks**. **PEAK court pricing** is a
club-wide window (`club.policy.peak_*`) + an explicit per-duration `billing.price.peak_amount_minor`, resolved
in `diary.pricing.price_for(at_local)` and applied in availability + booking in lockstep (coverage still wins
first). **EQUIPMENT hire** (`diary/equipment.py`) is a `diary.resource(kind='equipment')` with a `quantity`,
booked as a flat-fee add-on line on a court booking's order (one payment, no double-bill), availability-checked
by TIME and race-safe (FOR UPDATE inside the booking savepoint). Full spec:
[EQUIPMENT-AND-CONSTRAINTS.md](EQUIPMENT-AND-CONSTRAINTS.md).

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
  **A pack belongs to ONE specific service** — `billing.bundle_plan.product_id` + `billing.token_wallet.
  product_id` carry the exact service the pack draws for (a "Private Lesson" pack only draws for Private, a
  "Clay" pack only for Clay), the owner+kind **inherited** from that product (`create_plan` derives them). The
  draw matcher `match_wallet` is **product-aware and backward-compatible:** a product-scoped wallet draws only
  for its product; a **legacy NULL-product** wallet still matches by coach+kind (product-specific wins the
  tie-break). Callers pass the booking's product (lesson = chosen product, court = its court service, class =
  the class product). **Packs are configured ONLY under a service** (the service editor's packages card, via
  `POST/PATCH/DELETE /api/services/<product_id>/packages` → `create_plan`/`update_plan`/`deactivate_plan`); the
  standalone admin/coach pack editors + their `/api/{admin,coach}/bundle-plans` **write** routes were removed
  (`GET /api/admin/bundle-plans` is kept for the offline "issue a pack" picker). Existing live packs keep
  working (`product_id` NULL = legacy) until `scripts/backfill_pack_products.py` maps them to their service.
  Catalogue items (services,
  memberships, packs) share **ONE lifecycle vocabulary** — Active / Deactivated / Terminated
  (`billing.product.status`; memberships derive theirs from their term plans' active/dormant/retired
  state) — with filter bars, status chips and per-row Deactivate/Reactivate/Terminate actions.
- **"7 Day Trial Period"** — a genuinely-NEW member is auto-granted a 7-day courts-free trial on signup
  (`billing.membership.grant_signup_trial`, `provider='trial'`, court-only, auto-lapses → PAYG;
  `SIGNUP_TRIAL_DAYS` env). Gated in `auth/principal.py` on `upsert_user_by_clerk_id` returning
  `_created=True` — a returning/seeded/imported user (matched by clerk_id or email) is NEVER trialed, so the
  Wix imports stay PAYG. Audit: `scripts/audit_trials.py`. **Membership access windows** — a tier can be time-boxed
  (`billing.price.access_days/access_start_min/access_end_min`), enforced server-side by
  `diary.pricing.membership_covers(starts_at)` (outside the window → PAYG). Off-peak coverage is priced
  **per slot**: `compute_availability` surfaces R0 only inside a member's window and PAYG outside it
  (`diary.pricing.active_membership_windows` / `any_window_covers`), so the calendar's free/charged display
  matches what `create_booking` actually charges.
- **`billing/commission.py`** — the coach **commission/rent** engine: scoped dated rules
  (`coach+product > product > coach > club`), split computed **on collection** inside `apply_payment_event`
  (idempotent), arrears statement, owner cockpit aggregations. **The club↔coach settlement loop is CLOSED:**
  a new `billing.coach_payout` (record/settle/list) nets the append-only **`coach_ledger`** — the ONE net-owed
  figure per coach — so recording a payout draws the balance down and the aging view `GET /api/admin/financials/
  settlement` shows what each coach is still owed.
- **`billing/refunds.py`** — client refund-request workflow + admin approve/decline.
- **`billing/me.py`** — client financial reads.

**`client360/` — the ONE cross-lane client read model (2026-07-09; coach view folded in 2026-07).**
`client360.get_client_360(session, *, club_id, user_id, scope, coach_user_id, month)` is the single source of
truth every client-record view derives from. It is **reuse-first** — it does not query tables directly but
**composes the existing lane readers** (`billing.statement`/`membership`/`bundles`/`commission`/`refunds`/
`activity`, core notifications, diary bookings/enrolments, `iam.dependent`) into ONE guarded, club-scoped
payload. It is now **month-aware**: it takes `month=` and returns `month_events` (renamed from `events` —
one flat, newest-first list of the month's events that the person-360 groups by service type),
`statement_fold` (the reconciling money fold, below), and `_month_extra_orders` folded in (non-booking
orders — invoices/memberships/packs — so the service groups reconcile to the fold). The **coach view is the
SAME composer, not a fork:** `scope='coach'` (+`coach_user_id`) is a **strict SERVER-SIDE filter** — the coach
sees ONLY their own events + their own coaching fold + their own packages + a `service_breakdown`, while
membership, card-payments, the full statement, dependents, refunds, PII and activity are **OMITTED
server-side** (not merely UI-hidden). For `admin`/`client` scope it returns the full superset (identity,
membership(+status), packages{active,history}, statement + owed, payments, bookings, dependents, refunds,
coaching, activity, notifications-unread) with a per-`scope` `can{}` capability map. It is a **superset** of
the old admin person-360, so **`admin.repositories.get_person` now delegates to it** (`scope='admin'`) —
existing consumers and the `sc_person_360` harness are unchanged — and the coach/client views read the same
model scope-filtered (`GET /api/coach/clients/<id>/360`, `GET /api/me/360`). One read model → the ONE
`Widgets.ClientRecord` renders it across all three apps.

**The ONE money model — money as an OUTCOME of bookings.** Every money surface (coach console, admin Money,
the client record) reports the SAME month-scoped reconciling fold — **Billed − Discount − Written-off =
Invoiced; Invoiced = Paid + Outstanding** — with a cancelled/void booking counting **R0** and you-keep vs
club-commission taken from the ACTUAL `commission_split` rows. It is single-sourced on the front end through
`CRMUI.statementFold` + `CRMUI.moneySummary` (a Billed→Collected→Outstanding band) and on the server through
`client360.statement_fold` / `coach.repositories` / `admin.repositories`. An EVENT = the sum of its
transactions, drilling to the shared `Widgets.TransactionDetail`.

**The Money tab = ONE `Widgets.Earnings` (a CLUB-vs-COACH P&L), admin + coach** (`frontend/js/widgets/
earnings.js`). Admin Money opens on the reconciling money band + a section MENU: New invoice · Sales by day ·
**Club earnings** · Bookings by day · Approvals · Club activity — the old "Coach settlement" tab and the
"Online payments" tab (a duplicate of Sales by day) were RETIRED. **Club earnings** (`#/money/revenue`) is a
nested P&L drill: L0 = the CLUB's earnings = its DIRECT services (court/membership/pack it runs, 100% club) +
the COMMISSION taken from each coach → Total club earnings (collected-now + projected-when-all-owed) +
**Club-keeps vs Coaches-keep**; it lists each coach (net + club commission) and drillable direct-service rows.
Tap a coach → that coach's P&L card (Total sales − discount − write-off = Net ; Net = **Received + Owed** ; the
commission split is **realised** on Received and **projected on Owed** at the same effective rate ;
Coach-keeps-total vs Club-commission-total) → by-client → transactions → the shared `Widgets.TransactionDetail`;
a direct service drills service → clients → transactions. The **coach app's Money is the SAME widget** showing
the coach's OWN P&L ("You keep" wording). Backing (`admin/repositories.py`): the ONE `_earnings_cte` gained a
per-order **coach-attribution** column (lesson booking / class session / pack sold → that coach; court/membership
→ NULL = Club), and every drill level rides that ONE CTE so it reconciles exactly — readers `revenue_club_overview`,
`revenue_coach_pnl` (admin or coach-scope), `earnings_clients(category?, earned_by?)`, `earnings_transactions`,
with `earnings_by_service` still feeding the Money-menu band (the old `earnings_coaches` was retired). The
commission split is **realised** from `cockpit_coach_earnings` and **projected on owed** at that effective (else
the coach's `commission_rule` default) rate. `diary.bookings.order_story` gained a read-only `coach` scope.
**Sales by day** (`insights.sales_by_day`) now splits each day + the month total into **Online (Yoco)** vs
**Cash/EFT** takings (counting every `billing.payment` row, cash-basis by payment date).

**The payment rule (one shared rule across every purchase).** What payment methods a purchase offers is
configurable per service (`billing.product.payment_modes`) and **per membership tier** (new column
`billing.price.payment_modes`), resolved in layers (tier price-pref → product default → the club's globally
enabled methods). The front end (`Pay.purchase` → `buyMembership`/`buyPack` in `pay.js`, and `booking.js`)
applies ONE rule: more than one allowed mode → the client chooses; exactly one non-online mode → check out
immediately (no prompt); online → Yoco. Offline modes settle through the unified statement as `open` orders.
The rule is **enforced server-side against the SPECIFIC service**, not a generic first-of-kind product:
`create_booking`'s `_service_payment_modes_guarded` passes the resolved `product_id` (so a card-only Clay
service refuses pay-at-court), `billing.bundles.allowed_purchase_modes` intersects a pack with its own
service's modes (a card-only pack has NO at-court fallback — refuse rather than grant unpaid), and
`diary.classes.enrol` is gated like `create_booking` (`membership_covered` downgraded to at-court — classes
are court-only-free, `free` admin-only, money mode must be club-enabled AND offered by the class's service;
staff override via `role`). This closed a member self-enrol-for-R0 exploit.

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
duration · price + payment status. On a **lesson/class** the coach is **BCC'd** (`booking_detail.coach_email`
adds to the `bcc` list in `notifications.py`) — never on court bookings or purchases.
**ONE confirm+receipt email per purchase (audited + signed off 2026-07-11):** `load` resolves an
order-keyed event (`payment_succeeded` etc.) to its booking (`order_line.booking_id`) or class
(`enrolment.order_id`) and shows the SAME rich block, else a **purchase block** for a membership/pack
(`_load_order`, tagged `order_kind`; Item · Period/Validity · Amount · Payment). `deliver()` then makes an
online booking's payment email the single confirmation (retitled **"Booking confirmed"**) and **suppresses**
the redundant `payment_succeeded` email for pack + class orders (their own "Pack activated"/"enrolment
confirmed" email is the one). The **payment-status wording is single-sourced** in
`billing.statement.settlement_status_label(state, mode)` — email + Client 360 both delegate, so a receipt,
an email and a client-record row never disagree. Shell = full doctype + viewport + table (Outlook-safe);
client links → `/portal`. Every booking has a downloadable **`.ics`**
(`diary/calendar.py` → `GET /api/diary/bookings/<id>/calendar.ics`; `ics_url` on the confirmation
payload) — in-app "Add to calendar" works now; the email *attachment* is gated OFF (`EMAIL_ICS_ENABLED=0`).

**Transactional email — multi-tenant SES — LIVE.** Improving on 1050's single-tenant bare-From sender:
ONE verified domain (`SES_SENDER`) carries **every** club, so adding a tenant needs no new SES
verification. Each club rides it with its OWN identity — a **From display name** (`club.name`) + **Reply-To**
(its first `club.location` email) — resolved by `marketing_crm/notifications.py::_club_identity`.
`marketing_crm/email/ses.py` self-gates on creds and takes its OWN AWS keys (`SES_AWS_ACCESS_KEY_ID` /
`SES_AWS_SECRET_ACCESS_KEY` / `SES_REGION`) so it can ride a different AWS account from S3; `send_email`
takes `from_name`/`reply_to` and `send_booking_confirmation` is club-branded; `send_raw_email` (MIME
`SendRawEmail`) attaches the booking **.ics** - **gated by `EMAIL_ICS_ENABLED` (default `0`) BY CHOICE.**
The key already carries `ses:SendRawEmail` (`AmazonSESFullAccess`) - that is how the invoice PDF attaches; flip the flag to enable .ics. `notifications.deliver` threads the club identity into every mapped
event. No new endpoints, no schema change. **Running now** via the **interim** Ten-Fifty5 AWS account
(`eu-north-1`, `SES_SENDER=noreply@ten-fifty5.com`) — the long-term proper CourtFlow-domain setup is in
[SES-SETUP.md](SES-SETUP.md). Klaviyo lifecycle flows hang off the same event feed (dark without
`KLAVIYO_API_KEY`). Diagnostic: `POST /api/cron/ses-selftest?to=<email>` (OPS-guarded).

## Request flow (a booking, end to end)
1. SPA `booking.js` (full-screen), **resource-first**: pick service → **pick WHERE (the court / coach /
   class) at the top** → then schedule on a month calendar with **inline per-duration price** (or "covered by
   membership") — **time is the LAST action** (no auto-advance, with a Back button), the SAME order for court,
   lesson and class flows → settlement (at-court / monthly / membership / **token** / online / free).
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
`sync:false`. No paid Render crons (hence lazy expiry + on-read accrual + the reconcile sweep for missed
webhooks). Scheduled work rides **GitHub Actions** instead: `.github/workflows/month-end.yml` fires the
**month-end sweep** `POST /api/cron/month-end` (OPS-guarded) **on the 25th** (the club's billing day) — it
accrues arrears + court rent, then for each client with an OPEN balance consolidates their open orders into
ONE numbered statement invoice + pay-link email (a client who owes nothing gets NO email), and is
**idempotent per (club, user, month)** (a re-run is a no-op).

## Key conventions
- **Nothing hardcoded** — prices/durations/plans/commission/bundles are owner-configured data
  (white-label). Build configurable *capabilities*.
- **Idempotent everything** — boot DDL, payment events, commission splits, token draws, notifications.
- **Vanilla-JS SPAs**, one `cf-*` design system (`app.css`), absolute asset/nav links.
- **Reuse, don't import** from `C:\dev\webhook-server` (1050, READ-ONLY).

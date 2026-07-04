# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This repo is the **multi-tenant tennis club management platform** (working name "CourtFlow").
NextPoint Tennis is club #1, migrating off Wix.

## Quick orientation (30-second map)
- **Entrypoints:** API = `wsgi:app` (has DB) · web/portal = `web_wsgi:app` (DB-less, host-switched in `web_app.py`).
- **Boot/schema runner:** `python -m db` (idempotent — run **twice**, second run must be a no-op).
- **Source of truth for current state:** start at **`docs/specs/README.md`** (not the `docs/00→12` design docs).
- **Gates before merge:** `python -m py_compile` over the tree + `python -m db` twice. There is no pytest suite.
- **Iron rule:** every domain row is `club_id`-scoped — never query domain data without it.

## Current state (read this first) — LIVE on Render
- **Deployed and operational end-to-end.** Repo `NxtPoint/courtflow` (Render auto-deploys `master`).
  Two web services (Render, Frankfurt, **Free** plan pre-launch): **`courtflow-api`** (`wsgi:app`, has DB)
  `https://courtflow-api.onrender.com`, and **`courtflow-web`** (`web_wsgi:app`, no DB; marketing + portal
  shells + `/login`) `https://courtflow-web.onrender.com`. Postgres = a separate Render DB (Frankfurt).
  Auth = a dedicated **CourtFlow Clerk DEV app** (`settling-alien-23.clerk.accounts.dev`, `pk_test_…`,
  values inline in `render.yaml`); `AUTH_ENABLED=1`. `SEED_NEXTPOINT=1` on the api re-seeds club #1 on
  boot (idempotent). Platform admin = `info@nextpointtennis.com`.
- **The onrender host is a marketing host** (`MARKETING_HOSTS`), so `courtflow-web.onrender.com/` serves
  the **public site** and the app is at `/portal`, `/book`, `/admin`, … (host-switch in `web_app.py`).
  Real domains (`nextpointtennis.com`) cut over at go-live.
- **Source of truth:** **`docs/specs/README.md` is the authoritative current-state index — START THERE**
  (`SYSTEM.md` architecture · `BUSINESS-RULES.md` capabilities · `INVENTORY.md` every endpoint/table/page ·
  `OUTSTANDING.md` what's left · `UNIFIED-STATEMENT.md` the money-reconciliation design). The original
  design docs are `docs/` (`00`→`12`); `docs/11` = locked decisions + the 1050 reuse map. Where they
  differ, `docs/specs/` reflects as-built reality.
- **2026-07-02 — FRONT-END REDESIGN (three role SPAs) + ADMIN in progress:** the old tab consoles are
  replaced by mobile-first (admin: responsive) **drill-through SPAs** on the one `cf-*` design system,
  each with exactly ONE booking "event story" reused everywhere (the **golden rule**). **Client** =
  one-page, no bottom nav (`frontend/app/app.html` + `frontend/js/client.js`; billing-by-category +
  `GET /api/me/bookings/<id>` booking-story drill). **Coach** = bottom-nav SPA (`coach_app.html` +
  `coach_app.js`): weekly calendar, client record drilling **by service → sessions (real paid/owed/
  written-off/discounted state) → the event story** (`GET /api/coach/bookings/<id>`), **Total billed**
  on cockpit + record, money actions (collect/discount/write-off) inside the event story, and **classes
  create/schedule/roster in Setup** (now bookable end-to-end). **.ics add-to-calendar** fixed on both
  (authed `apiFetch` → blob). `cancel_booking` voids the linked unpaid order (no phantom debt).
- **2026-07-03/04 — ADMIN CONSOLE COMPLETE + LIVE, then FRONT-END STANDARDISED (the widget GOLDEN RULE):**
  **Owner/Admin** = the responsive drill-through SPA (`admin_app.html` + `admin_app.js`), now served at
  **`/admin`** (bottom-nav ↔ desktop side-rail): Home (`GET /api/admin/home`) · People → unified person
  360 (`GET /api/admin/people/<id>`) · Money as Setup-style sections incl. **Sales by day** · Diary (the
  shared Calendar widget + Classes) · Setup · Insights (court-utilisation heatmap + Business Overview).
  The **classic tab console is preserved at `/admin-classic`**. New backend: `admin.repositories.get_person`,
  `diary.bookings.admin_booking_story`/`admin_reassign_coach`, and the **`insights/` lane**
  (`court_utilisation`, `sales_by_day` → `/api/insights/*`, registered in `app.py`). Then the whole front
  end was **standardised onto ONE WIDGET PER CAPABILITY — the enshrined GOLDEN RULE**
  (`docs/specs/FRONTEND-STANDARDISATION.md`): a shared `frontend/js/widgets/` layer
  (`Widgets.TransactionDetail` = the one event story across all three apps · `Widgets.Calendar` = the
  admin diary · `Widgets.Setup` + `Widgets.ServiceList` = owner+coach setup); role differences are
  **config, never forked render code**; common helpers (`card/backBar/kv/modal/statusChip/…`) promoted to
  `window.UI`; the dead classic coach console (`coach.js`/`coach.html`) deleted. **A second render of a
  capability is a bug — extend the widget's config.**
  **Gates: `python -m scripts.test_all` → booking 43 / billing 142 / statement 35.**
- **2026-06-28 additions (all live + harness-gated):** the **unified client statement** (`billing/statement.py`
  — one debt = one `billing.order`, settled once; account page shows ONE reconciled "Your statement",
  grouped by category with tick-to-part-settle; admin void/write-off; coach `coach_arrears` kept in
  **lockstep** with orders so commission accrues exactly once — `docs/specs/UNIFIED-STATEMENT.md`) ·
  **service-specific & per-membership-tier payment options** (`billing.price.payment_modes`) + **one payment
  rule** (>1 mode → choose · single non-online → immediate · online → Yoco; `frontend/js/pay.js`
  `Pay.purchase/buyMembership/buyPack`) · **memberships & packs purchasable offline** (at-court/monthly →
  owed order, activated immediately) · **off-peak membership coverage priced PER SLOT** (free in-window,
  PAYG at peak) · **Operate (Admin console) vs Configure (Settings)** split + Settings on the nav · unified
  **Active/Deactivated/Terminated** lifecycle for services/memberships/coaches + **real coach/court delete**
  (hard when no history, else archive). New columns: `billing.product.status`, `billing.price.payment_modes`,
  `billing.order.settled_by_order_id`.
- **Lanes / modules:**
  - **Foundation:** `app.py`, `wsgi.py`, `db.py` (boot runner + `BOOT_MODULES`), `auth/` (Clerk JWKS +
    club-scoped `Principal`; single-membership default, platform_admin wildcard), `iam/`, `club/`, `core/`,
    `scripts/` (seed/provision), `crons/`, `render.yaml`.
  - **Diary:** `diary/` — GiST no-double-book constraint; `bookings.py` (court/lesson/class lifecycle +
    **book-on-behalf** via `booked_for_user_id`; role-scoped `list_bookings`), `availability.py`,
    `classes.py`, `recurrence.py`, `routes.py` (`/api/diary/*`).
  - **Billing + commercial engines:** `billing/` — `apply_payment_event` (idempotent), `gateway.py`
    (`PaymentGateway` Protocol + registry), `orders.py`, `ledger.py`, `routes.py`, **plus the engines
    built on top:** `membership.py` (configurable term plans; **per-tier payment options** +
    **offline purchase** via `create_membership_order(settlement_mode)`; `cancel_membership`),
    `bundles.py` (generic token/bundle packs: atomic draw-down + idempotent credit-back; **offline
    purchase** via `create_bundle_order(settlement_mode)`), `commission.py` (coach rent +/or % split on
    collection, arrears, ledger — arrears now held in **lockstep** with orders), `refunds.py` (client
    refund-request → admin approve/decline), `me.py` (client financial reads), `events.py` (commission
    accrual + settlement-order fan-out hook), **`statement.py` (the UNIFIED client statement — single
    source of truth for what a client owes = unpaid `billing.order` rows; `create_settlement_order`
    (pay-all / part-settle) · `settle_settlement_order` · `void_order`).**
  - **Payments — Yoco (online):** `yoco_billing/` — `client.py` (Yoco REST + Standard-Webhooks signature
    verify), `adapter.py` (`YocoGateway` implementing `PaymentGateway`, self-registers on import), `routes.py`
    (`/api/billing/yoco/checkout|webhook|refund` + `/order/<id>`). Hosted-redirect checkout (card +
    Apple/Google/Samsung Pay). LIVE-configured: `YOCO_*` keys in Render, webhook registered, `PAYMENTS_ENABLED=1`.
    `billing/` core is untouched — this is a pure adapter behind the registry.
  - **CRM + notifications:** `marketing_crm/` — `emit()`→`core.usage_event` (and drives notifications
    non-fatally), `notifications.py` (in-app `core.notification` inbox + transactional email; child→guardian
    routing), Klaviyo sync (dark w/o `KLAVIYO_API_KEY`), consent, cockpit, `email/ses.py` fallback (dark
    w/o `SES_SENDER`); `contracts/events.md`. **Confirmation EMAIL is LIVE (2026-07-03, interim via the
    Ten-Fifty5 SES account) — bookings/invites email + notify in-app.** A booking **`.ics` calendar** is built
    (`diary/calendar.py` + `GET /api/diary/bookings/<id>/calendar.ics`; `ics_url` on the confirmation payload) —
    the in-app "Add to calendar" download works; the EMAIL attachment is currently OFF (`EMAIL_ICS_ENABLED=0`,
    interim key lacks `ses:SendRawEmail`).
  - **Admin (owner self-service):** `admin/` — `/api/admin/*` write APIs + onboarding; powers the owner
    onboarding wizard, Settings, the People tab (360 drawer), the **per-service commission editor**
    (club/coach/per-service incl. classes → `commission_rule`), the **financial cockpit** (per-coach
    settlement, refund-aware), and **statement arrears adjust** (`PATCH /api/admin/coach-statement/arrears/<id>`).
    Console (as-built) = the `/admin` SPA (`admin_app.js`; classic `admin.js` at `/admin-classic`). Added `club.onboarding_completed`, `iam.coach_invite`.
  - **Service editing (owner + coach):** `services/` — `/api/services/*` is the ONE API a service is
    edited through by BOTH roles; the route enforces who may change what (owner = everything incl.
    commission; coach = their OWN lesson/class name/variations/payment/packages, NEVER commission).
    Writes delegate to the existing `billing/`/`admin/` repos (no duplicated logic — this lane just
    unifies the surface); reads via `services.repositories.get_service` (one composed payload).
  - **Coach (self-service):** `coach/` — `/api/coach/*`; **4-step onboarding** (profile/photo/languages/
    quals/visibility + `review_bookings`, weekly hours → `diary.resource(kind=coach)`, per-duration
    services/rates + classes/packs; full pre-fill); **lesson approval queue** (accept/propose/decline);
    **book-for-a-client** (auto-confirms); **My Clients** 360 (derived, private; history + upcoming);
    **statement** (per-client paid/owed/net, mark-collected + **discount/write-off**); **Dashboard cockpit**
    (`/cockpit`: lessons/hours/net-of-commission earnings/fill-rate/trend + **lessons-left-on-plans** +
    month-end-after-commission). Console (as-built) = the `/coach` SPA (`coach_app.js`; the classic `coach.js` was deleted).
  - **Client (self-service):** `me/` — `/api/me/*`; profile/demographics (email read-only), **dependents**
    (`iam.dependent`, login-less child users → booking party), financials, **statement** (`GET /api/me/statement`,
    the client mirror of the coach statement), refund-requests, notifications. **My Bookings** has a
    **"Needs your attention"** section (accept/decline a coach's proposed time, withdraw a pending request)
    + **"Add to calendar"** (.ics) on upcoming bookings.
  - **Analytics:** `analytics/` + `/api/analytics/*` + `overview.html` — **Business Overview dashboard**
    (built & live): visits/visitors/sources/geo + customers/bookings/revenue/NPS; first-party page-view
    beacon (`analytics.js` → `/api/track/page`, geo via `CF-IPCountry`). **Embedded** as the admin console's
    "Overview" tab + standalone `/overview.html`. Per-business (the Ten-Fifty5 bridge was deprecated).
  - **Frontend:** `frontend/app/` (shells) + `frontend/js/` — **ONE design system in `frontend/app/app.css`**
    (bright/modern; every page uses its `cf-*` classes — keep it the single source, do NOT inline component
    styles). **THREE role SPAs, all built on ONE WIDGET PER CAPABILITY — the enshrined GOLDEN RULE**
    (`docs/specs/FRONTEND-STANDARDISATION.md`; role differences = config via a data adapter + actions map +
    fields, never forked render code): **client** (`app.html`+`client.js`, one page), **coach**
    (`coach_app.html`+`coach_app.js`, bottom nav + hour-grid schedule + by-service client record + classes),
    **admin** (`admin_app.html`+`admin_app.js`, responsive, **COMPLETE + LIVE at `/admin`**; classic at
    `/admin-classic`). The shared render layer is **`frontend/js/widgets/`** (`Widgets.TransactionDetail` =
    the ONE event story across all three apps · `Widgets.Calendar` = the admin diary · `Widgets.Setup` +
    `Widgets.ServiceList` = owner+coach setup) + promoted `window.UI` helpers
    (`card/backBar/kv/modal/statusChip/…`) + `crm_ui.js` (`CRMUI.*`). They also reuse `booking.js`
    (full-screen booking), `service_editor.js`, `class_ui.js`, `admin_api.js`/`coach_api.js`. The dead
    classic `coach.js`/`coach.html` were **deleted**; `admin.js`/`admin.html` remain for `/admin-classic`;
    `portal.js`/`my.js`/`plan.js` still serve legacy shells (onboarding, `/book`, `/admin-classic`).
    **Asset/nav links are ABSOLUTE** (`/app.css`, `/js/…`) so pages work at sub-paths.
  - **Web/SEO:** `web_app.py` (+ `web_wsgi.py`), `frontend/marketing/`, `frontend/_shared/` (`theme.css` +
    **`marketing.css`** + `chrome.py` + `branding.py` host→club resolver), `build_blog.py`,
    `frontend/login.html`, `migration/`. **PUBLIC-SITE REDESIGN — SHIPPED & LIVE (2026-07-02):** a
    cinematic, photo-rich, conversion-focused public site across the lean page set (`home` · `coaches` ·
    `programs` · `pricing` · `contact` + `careers`/`404`), hero → free-week hook → ticker marquee →
    numbered service features → clay statement → portal-cockpit showcase → founders → **HP video** →
    Ten-Fifty5 band → testimonials → gallery → CTA. **TWO-STYLESHEET MODEL (respect it):**
    `frontend/_shared/theme.css` is the **cross-lane design-system contract** (consumed by the portal +
    login) — **never add marketing styling there**; all public-site CSS lives in **`frontend/_shared/marketing.css`**
    (the `mk-*` layer, additive, marketing-only, loads **Fraunces** display type per-page so the portal is
    unaffected). Marketing pages link BOTH (`/shared/theme.css` then `/shared/marketing.css`), use the
    server-injected **`<!--#include nav-->`/`<!--#include footer-->`** chrome (nav logo = `branding.logo_url`
    → `/img/logo.webp`), ABSOLUTE `/img` `/shared` paths, and **local optimized WebP only** (no external
    stock). Real NextPoint photography is localized in `frontend/img/` (hero splash, clay aerial, feature +
    gallery shots, coach portraits) plus the **HP video** (`hp-intro.mp4`, transcoded ~6MB, served from
    `/img/` via `send_file` — range requests work; poster `hp-intro-poster.webp`, click-to-play). Club fact:
    **7 hard courts + 1 clay = 8 total** (the only clay in Gauteng). Approved visual source of truth =
    **`docs/public-site/prototype-home-v3.html`**; spec folder **`docs/public-site/`** (START at `README.md`).
    Verify with the DB-less Flask test-client (routes 200 + chrome injection, sitemap, branded 404); preview
    locally with `MARKETING_HOSTS=localhost python -c "import web_app; web_app.app.run(port=5061, threaded=True)"`
    (Chrome needs `threaded=True` to load parallel assets).
- **Shipped & working (~90%):** owner/coach onboarding + **auto-member** signup · book courts/lessons
  (coach∩court)/classes (recurring, waitlists, rosters, attendance) · book-on-behalf + **book-for-a-child** ·
  **three configurable purchasing models — PAYG (per-duration) · membership (term plans) · tokens/bundles
  (prepaid packs, atomic draw-down + credit-back)** · membership-covered free courts (+ admin grant/revoke) ·
  **Yoco** online pay + reconcile + receipts + **refunds (admin direct + client request→approve)** ·
  **commission/coaching-settlement engine** (rent +/or %, split on collection, arrears + **discount/write-off**,
  owner cockpit) · **lesson approval lifecycle** (per-coach review → request/propose/accept/decline;
  on-behalf auto-confirms) · **redesigned self-service for all three roles** (client action-first cockpit +
  ~2-tap booking + family/financials/**statement** · coach console: onboarding/services/**approval queue**/
  clients-360/**statement-edits**/cockpit · owner console: per-service commission/financial cockpit/People-360
  — now all on the shared `frontend/js/widgets/` layer + `window.UI`/`crm_ui.js`) · **in-app
  notifications** + booking **`.ics` calendar** (confirmation email **LIVE via SES**; `.ics` in-app only) ·
  unified master diary · bright/modern UI + public site ·
  **UNIFIED CLIENT STATEMENT** (one reconciled "what you owe" from unpaid orders, grouped + tick-to-part-
  settle, settle online anytime, admin void/write-off) · **service-specific & per-membership-tier payment
  options + one payment rule** · **memberships & packs buy offline** · **off-peak coverage priced per slot** ·
  **Operate-vs-Configure** consoles · **Active/Deactivated/Terminated lifecycle + real coach/court delete**.
  **Remaining:** see `docs/specs/OUTSTANDING.md`.

## Payments, pricing & booking flow — LIVE end-to-end
**Online payments (Yoco) — wired & verified.** `yoco_billing/` is a pure adapter behind
`register_gateway`/`get_gateway` (`billing/` core untouched). An `online` booking creates an
`awaiting_payment` order + `held` booking → `booking.js` calls `Pay.startYocoCheckout(order_id)` →
`POST /api/billing/yoco/checkout` returns Yoco's `redirect_url` → hosted page (card + Apple/Google/Samsung
Pay) → `POST /api/billing/yoco/webhook` (Standard-Webhooks verified) → `apply_payment_event` → order `paid`
+ booking `confirmed`. **GOTCHA the booking API returns `{booking:{order_id,status}, checkout}` — read
`res.booking.order_id`, NOT `res.order_id`** (that bug silently confirmed online bookings without
redirecting; fixed). **Two gates, both on:** `PAYMENTS_ENABLED=1` (global, in `render.yaml`)
+ per-club `club.policy.allow_online_payment` (**Admin → Settings → Payments** toggle; the policy upsert is
**INSERT-ONLY** so the boot re-seed can't reset it). Frontend: `frontend/js/pay.js` + `pay-return.html` +
`pay_return.js` (auto-served at `/pay-return.html`).
- **Refunds (built):** **Admin → Billing & settlement → "Recent online payments".** Two buttons:
  **"Refund only"** (record-only, booking kept) and **"Refund & cancel"** (also cancels the order's
  booking(s) + frees the slot via `diary.cancel_booking`, admin-fee waived). Both → `POST /api/billing/yoco/refund`
  (`{order_id, amount_minor?, cancel_booking?}`). Full refund sends NO amount (Yoco's `amount` is nullable =
  full); the lookup uses the CHECKOUT id (`ch_`, `payment_attempt.status='created'`), NOT the webhook's
  payment id (`p_`) — refunding a `p_` 404s.
- **Reconciliation (missed-webhook recovery):** `yoco_billing/reconcile.py` — if the free-tier API misses a
  webhook while asleep, an order can stay `awaiting_payment` though the customer paid. `client.get_checkout`
  asks Yoco; if `completed`+`paymentId` it replays a `charge_succeeded` through `apply_payment_event`
  (idempotent). `POST /api/billing/yoco/reconcile/<order_id>` (pay-return page calls it when polling stays
  pending) + `POST /api/cron/reconcile-payments` (OPS bulk sweep). Safe-by-design: a 404/405 GET surface →
  "unverifiable", never an error.
- **Receipts:** `GET /api/billing/receipt/<order_id>` (`yoco_billing/receipt.py`) → receipt JSON (lines,
  totals, payments, refunds) for online AND desk payments; `frontend/app/receipt.html` + `receipt.js` render a
  printable/PDF receipt, linked from the pay-return page.

## Business Overview dashboard + first-party analytics (`analytics/`)
A **platform-owner analytics dashboard** (separate lane from the per-club operational cockpit). `analytics/`
is read-only: `repositories.py` are **guarded** aggregations (a missing/empty table → empty panel, never a
500), `routes.py` exposes `GET /api/analytics/overview?days=&club_id=` (platform_admin = all clubs or
`?club_id` filter; club_admin = own club) + `GET /api/analytics/clubs`. Frontend `frontend/app/overview.html`
+ `overview.js` (ECharts) at **`/overview.html`** — KPIs (visits, unique/new/returning, customers, bookings,
revenue), traffic + sign-up lines, traffic-source / top-page / by-country / **by-device** tables,
**time-on-site**, settlement mix, NPS.
- **Source data:** website traffic from `core.usage_event` (`event_type='page_view'`); customers from
  `core.account`; bookings/revenue from `diary.*`/`billing.*`; NPS from `core.nps_response`.
- **First-party beacon (NEW — none existed before):** `frontend/js/analytics.js` (localStorage `anon_id` for
  unique visitors, referrer, UTM) → `POST /api/track/page` on load + SPA route change; **loaded on every page
  via the `web_app.py` head-injection** (single point). `beacon.py` captures **country from Cloudflare's
  `CF-IPCountry`** header (falling back to the `Accept-Language` region when no CDN geo header), plus
  device + time-on-site. No cookies, no third parties. **Website-traffic panels accrue data from go-live**
  (historical events lack page-views/geo).
- **Embedded in the admin console:** the dashboard is the **"Overview" tab** in `admin.html`/`admin.js`
  (an iframe of `/overview.html`, auth via the parent's `auth_client` relay) + the standalone `/overview.html`.
- **Per-business by design:** shows THIS platform only. The cross-business "Ten-Fifty5 bridge" was
  **DEPRECATED 2026-06-21** (removed `analytics/bridge.py`, the `?property=` switcher, `BRIDGE_TENFIFTY5_*`
  env) — each app shows its own overview; Ten-Fifty5 has its own `/backoffice` cockpit. `docs/12` is retired.

**Pricing model — per-duration PAYG + membership-covered courts.** A service carries ONE `billing.price`
row per offered duration (`duration_minutes` set, `unit='per_booking'`, `audience='any'`). `diary/pricing.py`:
`price_for(kind, duration_minutes)` (exact→nearest≤→any), `durations_for(kind[,coach])`, `has_active_membership`.
Seed: Court Hire 30/60/90/120 = R90/150/210/280; Private Lesson 30/60 = R250/400; classes per_session. **The
Wix-era "member R0" court tier is GONE** (the seed deactivates legacy no-duration court prices). An **active
membership makes COURT bookings free** (`settlement_mode=membership_covered`, resolved server-side via
`has_active_membership` — guarded: courts only, never lessons). Admin grants/revokes in **People**
(`POST|DELETE /api/admin/members/<user_id>/membership` → `billing.membership_subscription`, provider='manual').
**Self-serve membership purchase (Yoco one-off) is BUILT** (on the consolidated **`/plan`** page; the old
`/membership` + `/packs` 301-redirect there). Memberships also support
**typed tiers + optional access windows** (`billing.price.access_days/access_start_min/access_end_min`,
enforced by `diary.pricing.membership_covers(starts_at)` — outside the window → PAYG) and a **7-day
free-week trial** auto-granted on signup (`grant_signup_trial`, `provider='trial'`). Bundles are now
**unit/minute-based** (a pack covers any length; 90min off a 60-unit = 1.5 sessions). Catalogue items
carry a lifecycle **`status`** (active/dormant/retired). See `docs/specs/02-token-bundle-engine.md` +
`docs/specs/BUSINESS-RULES.md §4`. **(Current source of truth: `docs/specs/`.)**

**Booking flow (`frontend/js/booking.js`, full-screen — replaced `book.js`/`quickbook.js`):** Service →
**Schedule** (month calendar with **inline per-duration chips** for court/lesson — pick the duration right
on the day; live price or "Covered by your membership"; coach/court dropdowns default to "Any") → **Pay &
confirm** (at court / monthly / membership / online) → animated success. Also a **~2-tap quick-book** off
the cockpit (`portal.js`). Classes have fixed session times: Service → pick a session → enrol. **When
editing `booking.js`, PRESERVE** the `createBooking` call + the online seam (`res.booking.order_id` →
`Pay.startYocoCheckout`).

**Booking-validation principle — the front end only ever offers CONFIGURED services.** The picker shows only
durations with an active `billing.price` row (`durations_for`); the old per-club `min_booking_minutes`
rejection is GONE (a priced duration is always bookable). A **lesson reserves coach∩court**: `create_booking`
auto-assigns a free court (`_first_free_court`) and refuses if no coach OR no court is free
(`COACH_REQUIRED`/`NO_COURT_AVAILABLE`); only coaches with weekly hours + `is_bookable` are offered.

**Lesson approval lifecycle (accept / propose / decline).** Per-coach `iam.coach_profile.review_bookings`:
ON → a CLIENT self-booking that coach creates a **`requested`** booking reserving NOTHING (no court/order/
payment) until the coach acts; a coach/admin booking **on-behalf ALWAYS auto-confirms** (client notified,
can reschedule/cancel). Coach actions `POST /api/diary/bookings/<id>/{accept,propose,decline}` (only the
awaited party; admin always): **accept** → auto-assign court + settle → `confirmed` (online prepay coerced
to at-court for an unconfirmed lesson); **propose** a new time → **`proposed`** (awaiting the client, who
accepts/declines/withdraws in **My Bookings → "Needs your attention"**); **decline** → `cancelled`.
`requested`/`proposed` are in the booking `status` CHECK but NOT the GiST exclusion (they hold no slot).
Events: `lesson_requested|proposed|accepted|declined`. Verified idempotent (`python -m db`) + the full
request→accept→settle chain green on a scratch DB.

**Capacity-sweep WITHOUT a cron:** abandoned `held` bookings are released by **lazy expiry** —
`diary.bookings.release_expired_holds` runs at the top of `compute_availability` + `create_booking` (cancels
`held` rows past `held_until`). No paid cron needed; the four `render.yaml` crons stay commented out.

## Commands
- **Compile gate (CI-style, no infra):** `python -m py_compile $(git ls-files '*.py')` — there is no
  pytest suite; this + the integration script below are the gates (match 1050). **The `$(…)` substitution is
  bash** — run it from the Bash tool, or in PowerShell use `python -m py_compile (git ls-files '*.py')`.
- **Boot all schemas / idempotency gate:** `python -m db` (run it **twice** → second run must be a no-op).
- **Seed club #1:** `python -m scripts.seed_nextpoint` · **provision another tenant:** `python -m scripts.provision_club`
- **Run the API locally:** `gunicorn wsgi:app` (or `python -m app`) — needs `DATABASE_URL`.
- **Run the web/portal service locally:** `python web_wsgi.py` (DB-less; defaults to `PORT=5060`).
- **Fire a cron job by hand:** `python -m crons.trigger <reminders|capacity-sweep|monthly-invoice|membership-refill>`
  (needs `CRON_API_BASE` + `OPS_KEY`; the trigger only POSTs to `/api/cron/*` — see cron note below).
- **Rebuild the blog/SEO output:** `python build_blog.py`

## Verifying (no live infra needed)
- **Compile:** `python -m py_compile` over the tree (CI-style gate; there is no pytest suite — match 1050).
- **Scratch-DB scenario harnesses — the primary gate:** `python -m scripts.test_all` runs THREE
  rollback-only harnesses against the local sandbox DB (each its own scratch club, always rolled back):
  **booking** (`test_booking_scenarios`, 43 checks — double-book, lesson coach∩court, off-peak per-slot
  pricing, lifecycle), **billing/commercial** (`test_billing_scenarios`, 142 — settlement modes, commission,
  tokens, membership incl. offline + per-tier modes, refunds, refund clawback, dispute routing, void/
  lockstep, the client/coach event stories + the by-service breakdown), and **statement reconciliation**
  (`test_statement_reconciliation`, 35 — no double-count, pay-all-once, part-settle, reclaim,
  membership-covered R0 never owed, void/write-off, arrears↔orders lockstep, pack offline). Run alongside
  `python -m db` twice + `py_compile`.
- **Backend integration:** boot all schemas + a booking→order→event chain against a throwaway Postgres
  (`docker run postgres:16`, set `DATABASE_URL`, `python -m db` twice for the idempotency gate, then
  `python -m scripts.seed_nextpoint`). The cross-lane flow (diary→billing→CRM), the double-book refusal,
  and desk-payment idempotency were proven this way (12/12). **Against the REAL Render Postgres:**
  `python -m scripts.verify_live` reads `DATABASE_URL` from a gitignored `.env.local` (never printed),
  proves boot + seed are idempotent, and reports status only — safe to re-run.
- **Web service:** Flask test client against `web_app.py` (DB-less) — host-switch, portal-shell serving,
  robots/sitemap, branded 404 (14/14).
- **Yoco payments:** offline signature verify (valid / tampered / stale / missing / wrong-secret) +
  `parse_event` mapping (21/21); scratch-DB settlement chain (online order → `charge_succeeded` → order
  `paid` + booking `held→confirmed` → replay = no-op → `refunded` record-only, booking NOT reversed) (15/15);
  full HTTP webhook path via Flask test client (bad sig → 401 + order untouched, good sig → 200 paid+confirmed,
  replay idempotent, config probe advertises yoco without leaking the secret) (10/10). All green.

## Still needs Tomo (config, not code) — infra is otherwise live
- **S3** (`S3_BUCKET` + AWS keys) for coach **photo uploads** — until set, coaches paste a photo URL.
- **SES** — transactional email is **LIVE (2026-07-03)** end-to-end (booking confirmations, coach
  invites). **Interim setup:** it rides the **Ten-Fifty5 (1050) AWS account** (CourtFlow's own AWS was
  locked out) — `SES_AWS_ACCESS_KEY_ID`/`SES_AWS_SECRET_ACCESS_KEY` = 1050's keys, `SES_REGION=eu-north-1`
  (must match the verified identity), `SES_SENDER=noreply@ten-fifty5.com` (branded "NextPoint Tennis",
  Reply-To `info@nextpointtennis.com`). Multi-tenant HTML+text. **NB: the `.ics` email attachment is
  currently OFF** (`EMAIL_ICS_ENABLED=0`) because the interim IAM key lacks `ses:SendRawEmail` — plain
  `SendEmail` is used; the in-app "Add to calendar" download still works. Long-term (verify
  `nextpointtennis.com`/`courtflow.app` DKIM once CourtFlow's AWS is back): **`docs/specs/SES-SETUP.md`**.
  Klaviyo marketing still dark until `KLAVIYO_API_KEY`.
- **Yoco keys** (`YOCO_*`) — DONE (set in Render; payments live). Each club still opts in via the
  Settings → Payments toggle.
- **DNS / SEO cutover** for `nextpointtennis.com` (supervised — never an agent). See `docs/11 §5`, `docs/07`.
- Full pre-flight checklist (incl. `DATABASE_URL`, Clerk app, Klaviyo sender-domain auth): `BUILD_PROMPT.md`.

## Architecture (big picture — from docs/01, docs/02, docs/09)
The platform re-assembles ~80% of the proven **Ten-Fifty5 (1050)** architecture around one new
domain model: the **diary**. Same shape as 1050, fewer services (no ML/GPU/video).

- **Services (new Render blueprint `render.yaml`):** start with 2 web + crons.
  - `courtflow-api` — Flask+Gunicorn booking/diary/billing API; Clerk-JWT auth; every query `club_id`-scoped.
  - `courtflow-web` — host-switched: serves the per-club marketing site **and** the portal SPAs
    (member/coach/admin). Mirrors 1050's `locker_room_app.py` host-switch.
  - **crons** — `render.yaml` declares **four** cron services (reminders / capacity-sweep /
    monthly-invoice / membership-refill), each running `python -m crons.trigger <job>`. The trigger
    (`crons/trigger.py`) is a **thin dispatcher**: it carries no business logic and no DB access — it
    makes one authenticated POST to `/api/cron/<job>` (guarded by `OPS_KEY`) and exits non-zero on
    failure. Lanes own the handlers (B-Diary: reminders/capacity-sweep/membership-refill; C-Billing:
    monthly-invoice); until a handler exists the job is a visible no-op (404).
- **One Postgres DB, five schemas created** (idempotent boot DDL, no migration framework; `support.*`
  was designed but is not booted — `docs/specs/` is authoritative):
  - `club.*` tenants/config/branding/location/policies · `iam.*` user↔Clerk, membership, coach_profile
  - `diary.*` resources, availability, booking, class_session, enrolment, waitlist, recurrence (**the heart**, `docs/03`)
  - `billing.*` product, price, order, payment, account_ledger, membership_subscription, bundle_plan/token_wallet (`docs/05`)
  - `core.*` account/user/person, usage_event, consent, nps (ported from 1050 `core_db`)
- **Integrations (reused accounts, new project-scoped values):** Clerk (identity), Yoco/PayPal
  (provider-agnostic gateway, signed webhooks), AWS S3 (assets) + SES (transactional fallback),
  Klaviyo (all booking/lesson/class confirmations + lifecycle, fed by `core.*` event feed).

### Decoupling interfaces (why parallel lanes work)
- **Schema** (`docs/02`) is the contract between the diary, billing, and CRM lanes — agreed first.
- **Event contract** (`contracts/events.md`) decouples producers (diary, billing → `emit(event, payload)`)
  from the consumer (CRM/Klaviyo).
- **Gateway protocol** (`docs/05 §2`, `apply_payment_event(provider)`) isolates each payment adapter.

## Module ownership map (historical build order — docs/09)
**The platform is built; this table is now a reference for which lane owns which path** (touch only your
lane; coordinate on shared interface files). The original sequencing — **Phase 0** (foundation: repo,
`render.yaml`, DB connect, schema bootstrap, Clerk auth port, club resolution) + **Phase 1** (tenancy
schemas, permissions, seed NextPoint as club #1) done sequentially, then parallel lanes — is kept below
for context:

| Agent | Lane (owns) | Builds |
|---|---|---|
| A — Foundation | `app.py`, `wsgi.py`, `render.yaml`, `db.py`, `iam/`, `auth/` | Skeleton, boot/schema runner, Clerk port, club resolution. **Runs first.** |
| B — Diary | `diary/` | Booking/lesson/class CRUD, exclusion constraint, recurrence, waitlist, crons. |
| C — Billing | `billing/`, `yoco_billing/`, `paypal_billing/` *(PayPal adapter planned — not yet on disk)* | order/ledger, `apply_payment_event`, gateway adapters. |
| D — CRM | `core/`, `marketing_crm/` | `core.*` port, tracking, crm_sync, consent, Klaviyo. |
| E — Frontend | `frontend/` | Booking wizard, coach diary, club-admin console, `/login`. |
| F — Marketing/SEO | `frontend/marketing/`, `build_blog.py`, `migration/` | Host-switched site, blog, sitemap, URL inventory + 301 map. |

Use **git worktrees per lane** (or branch-per-lane); merge to `main` per phase. Don't fan out before
the schema + boot runner exist. **Shared interface files** (`contracts/events.md`, schema docs,
`render.yaml` env list): coordinate edits, Agent A is authoritative.

## Tech defaults (match 1050 so reuse is clean — docs/09 §6)
- Python 3.12 + Flask + Gunicorn + Postgres. **DB access = SQLAlchemy Core** (`db.get_engine`/`text()`,
  explicit `session`, repos never commit — callers compose via `db.session_scope()`) over the **psycopg 3**
  driver — not raw psycopg cursors. **Idempotent boot DDL** (`init()` on boot, `ADD COLUMN IF NOT EXISTS`)
  — no Alembic/migrations. Add `btree_gist` + `pgcrypto` extensions (`btree_gist` powers the diary's
  no-double-booking exclusion constraint).
- Vanilla-JS SPAs (no heavy framework), reusing 1050's CSS/chart conventions; Clerk JS on `/login`.
  The one place to add a dependency is a calendar lib for the diary UI (evaluate FullCalendar resource-timeline).

## Verification gates (run before merging — docs/03 §10, docs/09 §5)
There is no automated test runner — the gates are `py_compile` + the `python -m db` idempotency check plus
the scratch-DB integration scripts under "Verifying" above. Each phase had a concrete "done when":
- **Phase 0/1:** app boots; `init()` is idempotent (**run twice → no error**); Clerk JWT resolves a
  principal with `club_id` + role; NextPoint seed present.
- **Phase 2 (booking integrity — do not skip):** concurrent double-booking → exactly one wins;
  reschedule conflict is atomic; capacity/waitlist; cancellation policy. Run as automated asserts
  against a scratch DB.
- **Phase 3:** each settlement mode (online / at-court / monthly account) writes correct order/ledger
  rows; `apply_payment_event` is idempotent (replay = no-op).
- **Phase 4:** `booking_confirmed` triggers a Klaviyo confirmation (SES fallback); marketing send
  blocked without opt-in; **no minor PII** in any payload.

## Ground rules
- **Multi-tenant from day one:** every domain row carries `club_id`; **never query domain data without
  it.** Phase 8 adds RLS; until then this is a discipline, not a guardrail.
- **Reuse, don't import.** Copy patterns from the Ten-Fifty5 repo at `C:\dev\webhook-server`
  (**READ-ONLY reference** — never touch its repo/DB). Key references: `auth_v2/`, `models_billing.py`,
  `db_init.py`, `subscriptions_api.py`, `paypal_billing/`, `marketing_crm/`, `core_db/`,
  `locker_room_app.py`, `build_blog.py`. Do **not** bring over the ML/T5/GPU/video machinery.
- **New repo, NEW Postgres DB**; reuse existing Render/Clerk/AWS/Klaviyo accounts with new
  project-scoped values only. Secrets are `sync:false` in `render.yaml`; go-live flags
  (`PAYMENTS_ENABLED`, provider env) committed so a blueprint sync can't wipe them.
- Payments are **provider-agnostic** (Yoco adapter first, behind a flag); the diary launches without
  mandatory online pay. Klaviyo sends confirmations; marketing email is opt-in only.

## Gotchas
- **`api.nextpointtennis.com` is already live on the 1050 service** (`docs/01 §6`). Do not break it.
  Give the new platform its own API host (`api.courtflow.app`) — changing a Render custom domain can
  recreate a service.
- **Never let an agent change DNS.** The Wix→Render SEO cutover (`docs/07`) is supervised by Tomo.
- **`marketing/` (untracked) is NOT platform code** — it holds ad-ops notes (adspirer setup, Google Ads
  audit) and is not in `.gitignore`. Don't commit it with platform changes (`git add <paths>`, NOT
  `git add -A`), and don't confuse it with `frontend/marketing/` (the host-switched marketing site) or
  `marketing_crm/` (the CRM lane).
- **`UI.clear(node)` must drop the `cf-loading` class** (it does, in `frontend/js/ui.js`). `.cf-loading`
  paints a CSS `::before` spinner — emptying a node's children WITHOUT removing the class leaves the spinner
  animating *over* the new content. This caused "spinners on every admin page" until fixed. When you add a
  loading placeholder, render the result with `UI.clear(box)` before appending.
- **Free-tier cold starts → use timeouts, not infinite spinners.** Render Free web services sleep after
  ~15 min idle (~30–60s wake). `auth_client.js` puts a **70s timeout** on every `apiFetch` (+ Clerk-load /
  token-mint timeouts) so a cold/hung call shows a clear error, never an endless spinner. A GitHub Action
  (`.github/workflows/keep-warm.yml`) pings both services every 10 min **07:00–21:59 SAST** to avoid mid-use
  cold starts (free; sleeps overnight). At go-live, bump the Render services to **Starter** (never sleep)
  and the keep-warm can be removed.
- **SQL `:param IS NULL` needs a CAST** (psycopg `AmbiguousParameter`): write `CAST(:df AS timestamptz) IS
  NULL`, never a bare `:df IS NULL` — Postgres can't type the bare placeholder. (This 500'd the master diary.)
- **Cockpit revenue must let refunds through** — refund `billing.payment` rows have `status='refunded'`, so a
  `WHERE status='succeeded'` filter silently drops them (refunds showed R0, Net overstated). Filter as
  `(direction='charge' AND status='succeeded') OR (direction='refund' AND status IN ('succeeded','refunded'))`.

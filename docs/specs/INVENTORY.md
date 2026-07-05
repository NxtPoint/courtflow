# INVENTORY — everything that exists

Exhaustive as-built inventory (generated from the live code, 2026-06-21; refreshed 2026-06-26). Paths relative to repo root.

## 1. Services (Render, **Frankfurt**, **Starter** plan)
- **`courtflow-api`** (`wsgi:app`) — the Flask API, has the DB. `https://courtflow-api.onrender.com`.
  Boots all schemas (`python -m db`), `SEED_NEXTPOINT=1` re-seeds club #1 on boot. `AUTH_ENABLED=1`.
- **`courtflow-web`** (`web_wsgi:app`) — DB-less; host-switched marketing site + portal SPA shells +
  `/login`. `https://courtflow-web.onrender.com` (an entry in `MARKETING_HOSTS`, so `/` = public site,
  app lives at `/portal`, `/book`, `/admin`, …).
- **Postgres** — `courtflow-db`, a separate Render DB (Frankfurt). **Clerk DEV app** for auth (`pk_test_…`).
- **Region — all three in Frankfurt, co-located** (`region: frankfurt` + `plan: starter` pinned on both web
  services in `render.yaml`; the api uses the DB's internal same-region URL). Until 2026-07-05 the web
  services ran in Oregon while the DB was in Frankfurt (cross-Atlantic ~150ms/query); recreated in-region.
- **Crons** — declared in `render.yaml` but **commented out** (no paid crons). Their HTTP
  handlers exist (see §3 crons) and can be triggered manually; hold-release + waitlist run lazily instead.
- **Keep-warm** — `.github/workflows/keep-warm.yml` (GitHub Action) pings both services every 10 min
  07:00–21:59 SAST so the Free tier doesn't cold-start mid-use; sleeps overnight. Free. (Frontend also has
  a 70s `apiFetch` timeout so a cold/hung call errors instead of spinning forever — `frontend/js/auth_client.js`.)
  Now that both services are on **Starter** (2026-07-05) they no longer sleep, so the keep-warm is redundant
  and can be removed.

## 2. Code lanes (Python)
| Lane | Owns | Purpose |
|---|---|---|
| `app.py`, `wsgi.py`, `db.py` | boot, app factory, schema runner (`BOOT_MODULES`) | Foundation |
| `auth/` | `principal.py`, `verifier.py` | Clerk JWKS verify → club-scoped `Principal`; **auto-enrol** new users as members |
| `iam/` | `schema.py`, `repositories.py`, `permissions.py` | user, membership, coach_profile, coach_invite, player_profile, **dependent** |
| `club/` | `schema.py` | club, branding, location, policy |
| `core/` | `schema.py`, `repositories/` | core.user/account/person, usage_event, consent, nps, **notification** |
| `diary/` | bookings, availability, classes, recurrence, pricing, routes | The booking engine (the heart) |
| `billing/` | orders, ledger, gateway, membership, bundles, commission, refunds, statement, me, activity, events, routes | Orders/ledger + the commercial engines (`statement.py` = unified client statement; `activity.py::transaction_log` = unified per-client/coach money log; `me.py::billing_summary` = ORDER-based monthly by-category) |
| `yoco_billing/` | client, adapter, routes, reconcile, receipt | Yoco online payments (adapter behind the gateway registry) |
| `marketing_crm/` | tracking (`emit`), notifications (+`_club_identity`), email/ses, klaviyo, consent, cockpit | Event feed + **notifications** + CRM + **club-branded transactional email** |
| `admin/` | routes, repositories, schema | `/api/admin/*` owner self-service + config |
| `coach/` | routes, repositories, schema | `/api/coach/*` coach self-service + cockpit |
| `services/` | routes, repositories | `/api/services/*` — the ONE unified service-edit surface for owner + coach (owner can create a lesson per coach via `POST /api/services`); delegates to `coach/`/`billing/` repos |
| `me/` | routes | `/api/me/*` client self-service (profile, dependents, financials, refund-requests, notifications) |
| `analytics/` | repositories, routes | **Business Overview dashboard** (read-only over `core.usage_event`/`diary`/`billing`); `/api/analytics/*`; embedded as the admin "Insights" tab |
| `insights/` | repositories, routes | **Phase-2 P1 read-layer** (guarded aggregations, no new tables): court-utilisation heatmap + sales-by-day; `/api/insights/*`; feeds the admin Insights + Money "Sales by day" |
| `crons/` | trigger | thin dispatcher → `/api/cron/*` |
| `scripts/` | seed_nextpoint, provision_club | seed/provision tenants |
| `web_app.py`, `frontend/` | host-switch + SPA shells + marketing | The web service |
| `migration/` | Wix→Render URL/301 helper | SEO migration |

## 3. API endpoints (by lane)
**Diary `/api/diary/*`:** `GET availability` (membership coverage priced PER-SLOT — R0 only inside the
access window, PAYG outside) · `GET resources` · `GET durations` · `GET/POST bookings` ·
`GET bookings/<id>` · `PATCH bookings/<id>` (reschedule) · `POST bookings/<id>/cancel` (now **voids the
linked unpaid order** via `billing.statement.void_order` — a cancelled court no longer stays phantom-owed) ·
`POST bookings/<id>/status` · **`POST bookings/<id>/{accept,propose,decline}`** (lesson lifecycle — only
the awaited party; admin always) · **`GET bookings/<id>/calendar.ics`** (booking .ics) ·
`GET master` · `GET classes` · `POST classes` ·
`GET classes/<rid>/sessions` · `POST classes/<rid>/schedule` · `GET classes/<sid>/roster` ·
`POST classes/<sid>/enrol` · `POST classes/<sid>/cancel-enrolment` · `POST classes/<sid>/attendance` ·
`POST classes/sessions/<sid>/cancel`.

**Billing `/api/billing/*`:** `GET config` · `GET receipt/<order_id>` · `POST desk-payment` ·
`GET bundles` (+`allowed_payment_modes`) · `GET bundles/wallets` · `POST bundles/checkout` ·
`GET membership/status` (+`allowed_payment_modes` per plan) · `POST membership/checkout`. The two
`checkout` routes accept `settlement_mode` (offline → 'open'/owed order + grant immediately; online → Yoco).

**Yoco `/api/billing/yoco/*`:** `POST checkout` · `POST webhook` · `POST refund` · `GET order/<id>` ·
`POST reconcile/<order_id>`.

**Admin `/api/admin/*`:** **`GET home`** (the owner **command-center** for the redesigned admin app —
guarded focus cards: today / money (owed-to-club, net revenue, rent due) / people-needing-attention /
approvals; `admin/repositories.py::admin_home`) · `GET/POST onboarding` (+`/complete`) · `GET/PATCH club` · `PUT location` ·
`PATCH branding` · `PATCH policy` · `GET/POST resources` (+`PATCH/DELETE /<id>` — DELETE now real:
hard-delete a court with no bookings/sessions, else soft-archive) · `GET/PUT hours` ·
`GET/POST products` (+`PATCH /<id>`) · `GET/POST prices` (+`PATCH/DELETE /<id>`) ·
`GET coaches` · `POST coaches/invite` · `POST coaches/<id>/resend-invite` ·
**`PATCH coaches/<id>`** (lifecycle status) · **`DELETE coaches/<id>`** (real: hard-delete if no
history, else archive) · `GET people` · `GET payments` · `POST|DELETE members/<id>/membership` ·
**`GET members/<id>/statement`** · **`POST orders/<id>/void`** (`{write_off}` — void/write-off an owed order) ·
`GET/POST membership-plans` (+`PATCH/DELETE /<id>`) ·
**`GET/PATCH membership-config`** (per-tier payment options) · `GET/POST bundle-plans` (+`PATCH/DELETE /<id>`) ·
`GET coach-agreements` · `PUT coach-agreements/<coach_id>` · `GET/POST commission-rules`
(+`DELETE /<id>`, `GET /preview`) · `GET financials/{summary,revenue,coach-earnings,memberships}` ·
`GET coach-statement` · `POST coach-statement/arrears/<id>/collected` ·
**`PATCH coach-statement/arrears/<id>`** (discount/write-off) · `GET refund-requests` ·
`POST refund-requests/<id>/{approve,decline}` · **`GET people/<id>`** (the unified **person 360** —
profile + all roles + active membership + owed statement + online payments + bookings; if the person is a
coach, a settlement summary; `admin/repositories.py::get_person`) · **`GET bookings/<id>`** (the **admin
event story** / god-view — client + coach + charge + coaching-arrears + full action eligibility;
`diary/bookings.py::admin_booking_story`) · **`POST bookings/<id>/reassign-coach`** (move a future/unpaid
lesson to another bookable coach; `admin_reassign_coach`).

**Insights `/api/insights/*` (Phase-2 P1 read-layer, lane `insights/`):** **`GET court-utilisation`**
(`?days=` — booked-vs-available court-hours by weekday×hour + overall % → the Insights heatmap) ·
**`GET sales-by-day`** (`?month=` — daily takings grouped by day, each sale = client + service type +
amount → Money → Sales by day). Admin-gated, guarded (missing/empty → empty payload, never 500).
`insights/repositories.py`; registered in `app.py`.

**Coach `/api/coach/*`:** `GET/PATCH profile` · `GET onboarding` · `POST/PATCH services`
(+`POST services/<pid>/rate`, `PATCH/DELETE services/<id>`) · `GET/POST bundle-plans`
(+`PATCH bundle-plans/<id>` — own lesson packs, scoped + ownership-guarded) · `PUT hours` ·
`GET/POST/DELETE time-off` · `GET clients` · **`GET clients/<id>`** (`?month=` — the client 360;
now returns a **by-service breakdown** `services[]` + `services_billed_minor` with the REAL per-session
state paid/owed/written_off/discounted/covered, via `billing/commission.py::client_service_breakdown`) ·
**`GET bookings/<id>`** (the coach **event story** — client/contact, court, charge, coaching-arrears line,
players+attendance, can-flags for accept/propose/decline/reschedule/cancel/mark-completed/no-show +
mark-collected/discount/write-off; `diary/bookings.py::coach_booking_story`) ·
`GET cockpit` (+ **plan_balances**, month-end-after-commission, + **`billed_minor`** = gross coaching value
for the month before write-off/discount/collection, distinct from collected `gross_minor`;
`coach/repositories.py::_coach_billed`) · `POST photo-presign` ·
`GET classes*` (shared) · `POST coach-statement/...` (shared admin route, coach-gated for own).

**Services `/api/services/*`** (`services/routes.py` — the ONE surface a service is edited through by BOTH
owner + coach; the route enforces who may change what): read via `services.repositories.get_service` ·
**`POST /api/services`** — create a lesson: a coach creates one for themselves; the **OWNER creates one FOR
A CHOSEN COACH** (body `coach_user_id`, validated by `admin/repositories.is_club_coach`) → delegates to
`coach.repositories.create_service`. Frontend: `Widgets.ServiceList` `onCreate(kind)` → admin Setup → Services
"+ New" coach-picker modal (`AdminAPI.createService`).

**Client `/api/me/*`:** `GET/PATCH profile` · `GET/POST dependents` (+`PATCH/DELETE /<id>`) ·
`GET plan` (current plan + `is_trial`/`trial_days_left` + `membership_window`) ·
**`POST membership/cancel`** (self-cancel a paid membership) · `GET financials` ·
**`GET billing/summary`** (`?month=` — the client SPA's ORDER-based monthly by-category billing view;
`billing/me.py::billing_summary`) · **`GET bookings/<id>`** (the client **event story** for a booking —
`diary/bookings.py::booking_story`) ·
**`GET statement`** (unified statement — unpaid `billing.order` rows, grouped by category) ·
**`POST statement/pay`** (`{order_ids?}` → `create_settlement_order` → Yoco; pay all or a subset) · `GET orders` ·
`GET/POST refund-requests` (+`POST /<id>/cancel`) · `GET notifications` · `POST notifications/read`.

**Web service (`web_app.py`, marketing host):** `GET/POST /contact` (the public contact form posts
here — emails the club via SES, self-gating; logs the lead if SES unset).

**Crons `/api/cron/*`** (handlers exist; cron services off): `POST capacity-sweep` · `POST reminders` ·
`POST monthly-invoice` · `POST membership-refill` · `POST reconcile-payments` · **`POST ses-selftest`**
(OPS-guarded — sends a live SES test + surfaces the real SES error; `diary/routes.py`).

**Analytics `/api/analytics/*`:** `GET overview` (`?days`, `?club_id`) · `GET clubs`. **Tracking:**
`POST /api/track/page` (first-party page-view beacon;
geolocation via Cloudflare `CF-IPCountry`). **Core:** `GET /healthz` · `GET /api/whoami`.

**Transactional email (`marketing_crm/email/ses.py`)** — no HTTP surface; called from `notifications.deliver`.
**LIVE since 2026-07-03**, riding the **Ten-Fifty5 (1050) AWS account** interim (CourtFlow's own AWS was
locked out): the module takes its OWN creds `SES_AWS_ACCESS_KEY_ID`/`SES_AWS_SECRET_ACCESS_KEY` +
`SES_REGION=eu-north-1` + `SES_SENDER=noreply@ten-fifty5.com` (`SES_FROM_EMAIL` also read). Still self-gates
(no creds → in-app only, never errors). Functions: `_from_source`, `html_wrap`, `send_email(…, from_name,
reply_to)`, `send_raw_email(…, attachments=)`, `send_booking_confirmation`. **NB: the `.ics` email attachment
is currently OFF** (`EMAIL_ICS_ENABLED=0` — the interim IAM key lacks `ses:SendRawEmail`; plain `SendEmail`
is used) — the in-app `.ics` download still works. **Multi-tenant identity:** each club rides the one sender
with its own From display name (`club.club.name`) + Reply-To (its first `club.location.email`), resolved in
`notifications.py::_club_identity`. Long-term (verify `nextpointtennis.com`/`courtflow.app` DKIM once the
CourtFlow AWS account is back): **`docs/specs/SES-SETUP.md`**. No schema change.

## 4. Database — 5 schemas (idempotent boot DDL)
- **`club`**: `club`, `branding`, `location`, `policy`
- **`iam`**: `user`, `membership`, `coach_profile` (+`review_bookings`), `coach_invite`, `player_profile`, `dependent`
- **`diary`**: `resource`, `availability_rule`, `booking`, `booking_party`, `time_off`, `class_session`,
  `enrolment`, `waitlist`, `recurrence`, `reminder_log`
- **`billing`**: `product`, `price`, `order`, `order_line`, `payment`, `payment_attempt`,
  `account_ledger`, `membership_subscription`, `refund_request`, `bundle_plan`, `token_wallet`,
  `token_ledger`, `coach_agreement`, `commission_rule`, `commission_split`, `coach_ledger`,
  `coach_arrears`
  - *Key recent columns:* `product.status` + `price.status` (active/dormant/retired — the unified
    3-state lifecycle; `active` boolean kept in sync); `product.payment_modes` + **`price.payment_modes`**
    (per-tier/per-service allowed payment methods, CSV — layered tier→product→club resolution);
    `term_months`/`label` (membership plans) + `access_days`/`access_start_min`/`access_end_min`
    (membership access window); **`order.settled_by_order_id`** (links an owed child order to its
    'pay all' settlement order); **`order.status` now allows `void`/`written_off`** (admin void / debt
    write-off; a paid order can't be voided); `bundle_plan.status`;
    `token_wallet.base_minutes`/`minutes_total`/`minutes_remaining` (the unit
    engine's authoritative minute balance — `tokens_*` are display only); **`booking.status` now allows
    `requested`/`proposed`** (lesson approval lifecycle — NOT in the GiST exclusion, so they hold no
    slot; gated by `iam.coach_profile.review_bookings`).
- **`core`**: account/user/person, `usage_event`, consent, nps, `notification`
  *(the Business Overview analytics are read-only views over `core.usage_event` — no separate schema)*

Settlement modes on `billing.order`: `at_court`, `monthly_account`, `online`, `membership_covered`,
`token`, `free` (complimentary). Boot order + `BOOT_MODULES` in `db.py`.

## 5. Frontend (host-switched by `web_app.py`)
**Three role SPAs (the 2026-07-02 redesign — mobile-first drill-through; one `cf-*` design system).**
The old tab-based consoles are superseded by three single-page drill-through apps where every list row opens
its full **event story** (GOLDEN RULE: exactly ONE booking capability per app, reused everywhere). Blueprints:
[FRONTEND-REDESIGN.md](FRONTEND-REDESIGN.md) + [ADMIN-REDESIGN.md](ADMIN-REDESIGN.md).

**GOLDEN RULE — one widget per capability** ([FRONTEND-STANDARDISATION.md](FRONTEND-STANDARDISATION.md)):
the shared **`frontend/js/widgets/`** layer — **`Widgets.TransactionDetail`** (the one event story across
all three apps), **`Widgets.Calendar`** (the admin diary — Day view = resource-timeline grid, Week/Month
agenda; see below), **`Widgets.Setup`** + **`Widgets.ServiceList`**
(owner + coach setup) — plus promoted `window.UI` helpers (`card/backBar/kv/modal/statusChip/…`) and
`crm_ui.js` (`CRMUI.*`). Role differences = configuration (a data adapter + an actions capability-map +
`fields`), never forked render code.
- **Client** — `frontend/app/app.html` + `frontend/js/client.js`. ONE page, **no bottom nav** (Book from
  Home tiles; avatar top-right → profile). Home = greeting + book tiles + **Your sessions** (all,
  upcoming+past) + **Billing by category** (month nav → category → items → booking story / receipt) + Plan &
  credits. Drills via `GET /api/me/bookings/<id>` + `GET /api/me/billing/summary`. Served at `/`, `/portal`, `/app`.
- **Coach** — `frontend/app/coach_app.html` + `frontend/js/coach_app.js`. **Bottom nav Home · Schedule ·
  Clients · Money · Setup.** Schedule = a **weekly calendar** (tap lesson → the event story; tap class →
  roster). Clients → full client record (by-service breakdown). Money = account + disputes + per-client
  rollup. Setup = Services (lifecycle) + Classes (create/schedule/roster via `ClassUI`) + commission +
  Edit-profile/Weekly-hours pages. **THE ONE COACH EVENT STORY** (`#/event/:id` → `GET /api/coach/bookings/<id>`)
  carries the arrears actions (mark-collected / discount / write-off). Served at `/coach`, `/coach.html`
  (non-coaches bounced).
- **Admin / Owner — COMPLETE + LIVE** — `frontend/app/admin_app.html` + `frontend/js/admin_app.js`, served
  at **`/admin`** (also `/admin.html`, `/admin-app`). **Responsive:** bottom-nav on mobile, **left
  side-rail on desktop** (`.cf-admin`). Nav Home · People · Money · Diary · Setup (Insights off a Home
  tile). **Home** = command-center (4 focus cards, `GET /api/admin/home`) · **People** = roster → the
  **unified person 360** (`#/person/:id`, `GET /api/admin/people/<id>`) · **Money** = Setup-style section
  menu (Sales by day · Revenue by service · Coach settlement · Approvals · Online payments · Activity) ·
  **Diary** = the shared **`Widgets.Calendar`** (court/coach filters) + Classes — the **Day view is the
  resource-timeline GRID** (courts + coaches as columns, 06:00–22:00 rows, `cf-ev` blocks; config-driven via
  `cfg.grid`, empty coach columns hidden, courts always shown), **Week/Month stay agenda**; any block drills
  to the shared `Widgets.TransactionDetail` event story. Walk-in / block-time / desk-pay editing were NOT
  ported — they stay in the classic diary at `/admin-classic` · **Setup**
  = the shared **`Widgets.Setup`** (Club profile+payments · Courts · Services · Memberships · Packs ·
  Coaches) · **Insights** = court-utilisation heatmap + the Business Overview. The **classic tab console is
  preserved at `/admin-classic`** (its full drag-timeline is linked from the new Diary).
- **Web routes / redirects (`web_app.py`):** `/`,`/portal`,`/app` → `app.html` · `/coach`,`/coach.html` →
  `coach_app.html` · **`/admin`,`/admin.html`,`/admin-app` → `admin_app.html` (the NEW SPA)** ·
  **`/admin-classic` → `admin.html` (the classic console)**. Old standalone pages **302 → the client SPA**
  (`/book.html`→`/portal#/book/court`, `/my`→`/portal#/bookings`, `/account.html`→`/portal#/billing`). The
  classic **coach** console (`coach.html`/`coach.js`) was **deleted**; `admin.html`/`admin.js` remain for
  `/admin-classic`. Post-login role routing (`client.js`) lands admins on `/admin`, coaches on `/coach`.

**Portal SPA shells** (`frontend/app/*.html`, each `cf-*` design system, absolute asset links):
`portal` (dashboard) · `book` (full-screen booking) · `my` (my bookings) · `plans` (consolidated
Membership/Packs/PAYG — served at `/plan`; `/membership` + `/packs` 301 here) · `account` (profile/family/
financials) · `coach` (+`coach-onboarding`) · `statement` (**superseded** — the coach month-end statement
now lives in the coach console's **Money** tab; `/statement.html` is kept as a fallback page, no longer
linked) · `admin` · `onboarding` (owner) · `settings` · `overview` (**Business Overview dashboard**, ECharts) ·
`receipt` · `pay-return` · `styleguide`.

**Role-focused nav (`frontend/js/portal.js` + `home.js`).** Nav is role-precise — the client booking Home +
Account no longer show to staff:
- member/guest → **Home · Account**
- coach → **Coach** (landing) · Account
- club_admin/platform_admin → **Admin** (landing) · Settings

Post-login role routing (`client.js`, the SPA entry) lands members on the client Home, coaches on `/coach`,
admins on `/admin`; `Portal.landingFor` is the legacy equivalent for the old `*.html` shells.

**Classic tab consoles — RETIRED.** The coach console (`coach.js`/`coach.html`) was **deleted**. The owner
console (`admin.js`) is preserved at **`/admin-classic`** as a fallback (5 tabs: Dashboard · Diary
[Timeline + Classes] · People · Money [Billing + financial cockpit] · Insights) — chiefly for its full
drag-and-drop master-diary timeline until that ports into the new Diary. The live consoles are the three
SPAs above, all on the shared widget layer.

**JS modules** (`frontend/js/*.js`): **`client`** (client SPA — Home/sessions/billing-by-category/event
story) · **`coach_app`** (coach SPA — bottom-nav Home·Schedule·Clients·Money·Setup + the one coach event
story) · **`admin_app`** (admin SPA, in progress — responsive shell + command-center Home) ·
`portal` (role-focused nav + `landingFor` + notification bell) ·
`home` (client Home + staff redirect) · `booking` (full-screen; replaced `book`/`quickbook`) · `my` ·
`plan` · `account` · `coach` (5-tab console; +`coach_api`, `coach_onboarding`) · `statement` (fallback page) ·
`admin` (5-tab console; +`admin_api`, `class_ui`; `AdminUI.courtsManage` = per-court click-to-edit hours) ·
**`crm_ui`** (shared CRMUI components for both consoles) · `settings` · `onboarding` · `notifications` ·
`pay` (`Pay.purchase`/`buyMembership`/`buyPack` — THE payment rule) · `pay_return` · `receipt` ·
`analytics` (page-view beacon) · `overview` (Business Overview dashboard) · `api` · `auth_client` ·
`ui` (+`UI.lifecycleBar`/`lifeActions`/`statusChip`/`subtabs` lifecycle helpers). `account.js` renders the
grouped tick-to-pay "Your statement" card. **One design system:** `frontend/app/app.css` (all `cf-*`
classes — incl. `.cf-lifefilter`/`.cf-subtabs`/`.cf-cal*`; the SPA redesign added `.cf-bottomnav*`,
`.cf-appbar`, `.cf-avatar`, `.cf-kv*`, `.cf-owe`, `.cf-amountbig`, and `.cf-admin` for the desktop
side-rail). Marketing site: `frontend/marketing/`, `frontend/_shared/`.

## 6. Env / config
**Full reference: `docs/specs/ENV-STATUS.md`** — every var, live/dark status, copy-paste checklist.
Live now: `DATABASE_URL`, `OPS_KEY`, Clerk `AUTH_*`, Yoco (`PAYMENTS_ENABLED=1`, `PAYMENTS_PROVIDER=yoco`,
`YOCO_SECRET_KEY`/`YOCO_PUBLIC_KEY`/`YOCO_WEBHOOK_SECRET`), `APP_BASE_URL`, `SEED_NEXTPOINT=1`,
`MARKETING_HOSTS`, and **transactional email** — LIVE via the interim Ten-Fifty5 AWS account
(`SES_SENDER=noreply@ten-fifty5.com`, `SES_AWS_ACCESS_KEY_ID`/`SES_AWS_SECRET_ACCESS_KEY`,
`SES_REGION=eu-north-1`; **`EMAIL_ICS_ENABLED=0`** — the .ics attachment is OFF until the key gains
`ses:SendRawEmail`, so confirmations attach nothing yet). Dark until keyed: `KLAVIYO_API_KEY`
(CRM/marketing — self-gates), `S3_BUCKET`+AWS keys (photo uploads), Google-tag/GSC vars
(`GA4_MEASUREMENT_ID`/`GOOGLE_ADS_*`/`GSC_*` — set at go-live cutover).
**Note:** the old `*_ENABLED` toggles (`YOCO_/TRACKING_/CONSENT_/CRM_SYNC_`) plus the dead
`BRIDGE_TENFIFTY5_*` trio (`_ADMIN_EMAIL`/`_CLIENT_KEY`/`_URL`) were dead config (never read) — dropped from
the live services on the Frankfurt recreate and **must not be re-added**; those features are always-on or
self-gate on their keys. `render.yaml` now also pins `region: frankfurt` + `plan: starter` on both web
services and declares `SES_REGION=eu-north-1` + `SEED_NEXTPOINT=1`; secrets are still entered in the Render
dashboard (`sync:false`).

## 7. Verify gates (no live infra)
- Compile: `python -m py_compile $(git ls-files '*.py')`.
- Schema idempotency: `python -m db` **twice** → second run a no-op.
- Integration: throwaway `postgres:16` + `python -m scripts.seed_nextpoint`; scenario harnesses
  `python -m scripts.test_all` (booking / billing / **`scripts/test_statement_reconciliation.py`** —
  35 checks: no double-count, pay-all-once, partial settle, void/write-off, arrears↔orders lockstep).
- Frontend: `node --check <file>.js`.

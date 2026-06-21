# INVENTORY — everything that exists

Exhaustive as-built inventory (generated from the live code, 2026-06-21). Paths relative to repo root.

## 1. Services (Render, Frankfurt, Free plan)
- **`courtflow-api`** (`wsgi:app`) — the Flask API, has the DB. `https://courtflow-api.onrender.com`.
  Boots all schemas (`python -m db`), `SEED_NEXTPOINT=1` re-seeds club #1 on boot. `AUTH_ENABLED=1`.
- **`courtflow-web`** (`web_wsgi:app`) — DB-less; host-switched marketing site + portal SPA shells +
  `/login`. `https://courtflow-web.onrender.com` (an entry in `MARKETING_HOSTS`, so `/` = public site,
  app lives at `/portal`, `/book`, `/admin`, …).
- **Postgres** — a separate Render DB (Frankfurt). **Clerk DEV app** for auth (`pk_test_…`).
- **Crons** — declared in `render.yaml` but **commented out** (Free plan, no paid crons). Their HTTP
  handlers exist (see §3 crons) and can be triggered manually; hold-release + waitlist run lazily instead.

## 2. Code lanes (Python)
| Lane | Owns | Purpose |
|---|---|---|
| `app.py`, `wsgi.py`, `db.py` | boot, app factory, schema runner (`BOOT_MODULES`) | Foundation |
| `auth/` | `principal.py`, `verifier.py` | Clerk JWKS verify → club-scoped `Principal`; **auto-enrol** new users as members |
| `iam/` | `schema.py`, `repositories.py`, `permissions.py` | user, membership, coach_profile, coach_invite, player_profile, **dependent** |
| `club/` | `schema.py` | club, branding, location, policy |
| `core/` | `schema.py`, `repositories/` | core.user/account/person, usage_event, consent, nps, **notification** |
| `diary/` | bookings, availability, classes, recurrence, pricing, routes | The booking engine (the heart) |
| `billing/` | orders, ledger, gateway, membership, bundles, commission, refunds, me, events, routes | Orders/ledger + the commercial engines |
| `yoco_billing/` | client, adapter, routes, reconcile, receipt | Yoco online payments (adapter behind the gateway registry) |
| `marketing_crm/` | tracking (`emit`), notifications, email/ses, klaviyo, consent, cockpit | Event feed + **notifications** + CRM |
| `admin/` | routes, repositories, schema | `/api/admin/*` owner self-service + config |
| `coach/` | routes, repositories, schema | `/api/coach/*` coach self-service + cockpit |
| `me/` | routes | `/api/me/*` client self-service (profile, dependents, financials, refund-requests, notifications) |
| `analytics/` | (4 files) | **Page/traffic analytics — another agent's lane (in progress); `/api/analytics/*`, `/stream`** |
| `crons/` | trigger | thin dispatcher → `/api/cron/*` |
| `scripts/` | seed_nextpoint, provision_club | seed/provision tenants |
| `web_app.py`, `frontend/` | host-switch + SPA shells + marketing | The web service |
| `migration/` | Wix→Render URL/301 helper | SEO migration |

## 3. API endpoints (by lane)
**Diary `/api/diary/*`:** `GET availability` · `GET resources` · `GET durations` · `GET/POST bookings` ·
`GET bookings/<id>` · `PATCH bookings/<id>` (reschedule) · `POST bookings/<id>/cancel` ·
`POST bookings/<id>/status` · `GET master` · `GET classes` · `POST classes` ·
`GET classes/<rid>/sessions` · `POST classes/<rid>/schedule` · `GET classes/<sid>/roster` ·
`POST classes/<sid>/enrol` · `POST classes/<sid>/cancel-enrolment` · `POST classes/<sid>/attendance` ·
`POST classes/sessions/<sid>/cancel`.

**Billing `/api/billing/*`:** `GET config` · `GET receipt/<order_id>` · `POST desk-payment` ·
`GET bundles` · `GET bundles/wallets` · `POST bundles/checkout` · `GET membership/status` ·
`POST membership/checkout`.

**Yoco `/api/billing/yoco/*`:** `POST checkout` · `POST webhook` · `POST refund` · `GET order/<id>` ·
`POST reconcile/<order_id>`.

**Admin `/api/admin/*`:** `GET/POST onboarding` (+`/complete`) · `GET/PATCH club` · `PUT location` ·
`PATCH branding` · `PATCH policy` · `GET/POST resources` (+`PATCH/DELETE /<id>`) · `GET/PUT hours` ·
`GET/POST products` (+`PATCH /<id>`) · `GET/POST prices` (+`PATCH/DELETE /<id>`) ·
`GET coaches` · `POST coaches/invite` · `POST coaches/<id>/resend-invite` · `DELETE coaches/<id>` ·
`GET people` · `GET payments` · `POST|DELETE members/<id>/membership` ·
`GET/POST membership-plans` (+`PATCH/DELETE /<id>`) · `GET/POST bundle-plans` (+`PATCH/DELETE /<id>`) ·
`GET coach-agreements` · `PUT coach-agreements/<coach_id>` · `GET/POST commission-rules`
(+`DELETE /<id>`, `GET /preview`) · `GET financials/{summary,revenue,coach-earnings,memberships}` ·
`GET coach-statement` · `POST coach-statement/arrears/<id>/collected` · `GET refund-requests` ·
`POST refund-requests/<id>/{approve,decline}`.

**Coach `/api/coach/*`:** `GET/PATCH profile` · `GET onboarding` · `POST/PATCH services`
(+`POST services/<pid>/rate`, `PATCH/DELETE services/<id>`) · `PUT hours` · `GET/POST/DELETE time-off` ·
`GET clients` · `GET clients/<id>` · `GET cockpit` · `POST photo-presign` · `GET classes*` (shared) ·
`POST coach-statement/...` (shared admin route, coach-gated for own).

**Client `/api/me/*`:** `GET/PATCH profile` · `GET/POST dependents` (+`PATCH/DELETE /<id>`) ·
`GET financials` · `GET orders` · `GET/POST refund-requests` (+`POST /<id>/cancel`) ·
`GET notifications` · `POST notifications/read`.

**Crons `/api/cron/*`** (handlers exist; cron services off): `POST capacity-sweep` · `POST reminders` ·
`POST monthly-invoice` · `POST membership-refill` · `POST reconcile-payments`.

**Analytics `/api/analytics/*`** (another agent): `GET overview` · `GET clubs` · `GET properties` ·
`GET /stream`. **Core:** `GET /healthz` · `GET /api/whoami`.

## 4. Database — 6 schemas (idempotent boot DDL)
- **`club`**: `club`, `branding`, `location`, `policy`
- **`iam`**: `user`, `membership`, `coach_profile`, `coach_invite`, `player_profile`, `dependent`
- **`diary`**: `resource`, `availability_rule`, `booking`, `booking_party`, `time_off`, `class_session`,
  `enrolment`, `waitlist`, `recurrence`, `reminder_log`
- **`billing`**: `product`, `price`, `order`, `order_line`, `payment`, `payment_attempt`,
  `account_ledger`, `membership_subscription`, `refund_request`, `bundle_plan`, `token_wallet`,
  `token_ledger`, `coach_agreement`, `commission_rule`, `commission_split`, `coach_ledger`,
  `coach_arrears`
- **`core`**: account/user/person, `usage_event`, consent, nps, `notification`
- **`analytics`**: (another agent's lane — traffic/sessions)

Settlement modes on `billing.order`: `at_court`, `monthly_account`, `online`, `membership_covered`,
`token`. Boot order + `BOOT_MODULES` in `db.py`.

## 5. Frontend (host-switched by `web_app.py`)
**Portal SPA shells** (`frontend/app/*.html`, each `cf-*` design system, absolute asset links):
`portal` (dashboard) · `book` · `my` (my bookings) · `membership` · `packs` · `account` (profile/family/
financials) · `coach` (+`coach-onboarding`) · `statement` (coach month-end) · `admin` · `onboarding`
(owner) · `settings` · `overview` (analytics, another agent) · `receipt` · `pay-return` · `styleguide`.

**JS modules** (`frontend/js/*.js`): `portal` (nav + notification bell) · `book` · `my` · `membership` ·
`packs` · `account` · `coach` (+`coach_api`, `coach_onboarding`) · `statement` · `admin` (+`admin_api`,
`class_ui`) · `settings` · `onboarding` · `notifications` · `pay` · `pay_return` · `receipt` ·
`analytics`/`overview` (another agent) · `api` · `auth_client` · `ui`. **One design system:**
`frontend/app/app.css` (all `cf-*` classes). Marketing site: `frontend/marketing/`, `frontend/_shared/`.

## 6. Env / config (render.yaml)
Auth: `AUTH_ENABLED`, Clerk `pk_test_…` (inline). Payments: `PAYMENTS_ENABLED=1`, `YOCO_ENABLED=1`,
`PAYMENTS_PROVIDER=yoco`, `YOCO_SECRET_KEY`/`YOCO_PUBLIC_KEY`/`YOCO_WEBHOOK_SECRET` (sync:false),
`APP_BASE_URL`. CRM: `TRACKING_ENABLED`, `KLAVIYO_API_KEY` (sync:false, **unset → CRM/email dark**).
Assets/email: `S3_BUCKET`+AWS keys (sync:false, **unset → photo uploads via URL paste, SES dark**).
Tenancy: `SEED_NEXTPOINT=1`, `MARKETING_HOSTS`, `OPS_KEY` (cron auth). DB: `DATABASE_URL`.

## 7. Verify gates (no live infra)
- Compile: `python -m py_compile $(git ls-files '*.py')`.
- Schema idempotency: `python -m db` **twice** → second run a no-op.
- Integration: throwaway `postgres:16` + `python -m scripts.seed_nextpoint`.
- Frontend: `node --check <file>.js`.

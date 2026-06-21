# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This repo is the **multi-tenant tennis club management platform** (working name "CourtFlow").
NextPoint Tennis is club #1, migrating off Wix.

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
  `OUTSTANDING.md` what's left). The original design docs are `docs/` (`00`→`12`); `docs/11` = locked
  decisions + the 1050 reuse map. Where they differ, `docs/specs/` reflects as-built reality.
- **Lanes / modules:**
  - **Foundation:** `app.py`, `wsgi.py`, `db.py` (boot runner + `BOOT_MODULES`), `auth/` (Clerk JWKS +
    club-scoped `Principal`; single-membership default, platform_admin wildcard), `iam/`, `club/`, `core/`,
    `scripts/` (seed/provision), `crons/`, `render.yaml`.
  - **Diary:** `diary/` — GiST no-double-book constraint; `bookings.py` (court/lesson/class lifecycle +
    **book-on-behalf** via `booked_for_user_id`; role-scoped `list_bookings`), `availability.py`,
    `classes.py`, `recurrence.py`, `routes.py` (`/api/diary/*`).
  - **Billing + commercial engines:** `billing/` — `apply_payment_event` (idempotent), `gateway.py`
    (`PaymentGateway` Protocol + registry), `orders.py`, `ledger.py`, `routes.py`, **plus the engines
    built on top:** `membership.py` (configurable term plans), `bundles.py` (generic token/bundle packs:
    atomic draw-down + idempotent credit-back), `commission.py` (coach rent +/or % split on collection,
    arrears, ledger), `refunds.py` (client refund-request → admin approve/decline), `me.py` (client
    financial reads), `events.py` (commission accrual hook).
  - **Payments — Yoco (online):** `yoco_billing/` — `client.py` (Yoco REST + Standard-Webhooks signature
    verify), `adapter.py` (`YocoGateway` implementing `PaymentGateway`, self-registers on import), `routes.py`
    (`/api/billing/yoco/checkout|webhook|refund` + `/order/<id>`). Hosted-redirect checkout (card +
    Apple/Google/Samsung Pay). LIVE-configured: `YOCO_*` keys in Render, webhook registered, `PAYMENTS_ENABLED=1`.
    `billing/` core is untouched — this is a pure adapter behind the registry.
  - **CRM + notifications:** `marketing_crm/` — `emit()`→`core.usage_event` (and drives notifications
    non-fatally), `notifications.py` (in-app `core.notification` inbox + transactional email; child→guardian
    routing), Klaviyo sync (dark w/o `KLAVIYO_API_KEY`), consent, cockpit, SES fallback; `contracts/events.md`.
  - **Admin (owner self-service):** `admin/` — `/api/admin/*` write APIs + onboarding; powers the owner
    onboarding wizard, Settings, and the People tab. Added `club.onboarding_completed`, `iam.coach_invite`.
  - **Coach (self-service):** `coach/` — `/api/coach/*`; onboarding (profile/photo, weekly hours →
    `diary.resource(kind=coach)`, per-duration services/rates); **My Clients** (derived, private); **Dashboard
    cockpit** (`/cockpit`: lessons/hours/net-of-commission earnings/fill-rate/trend); statement page.
  - **Client (self-service):** `me/` — `/api/me/*`; profile/demographics (email read-only), **dependents**
    (`iam.dependent`, login-less child users → booking party), financials, refund-requests, notifications.
  - **Analytics:** `analytics/` + `/api/analytics/*` + `overview.html` — **page/traffic analytics, owned by a
    separate agent (in progress)**; do not edit that lane.
  - **Frontend:** `frontend/app/` (shells) + `frontend/js/` — **ONE design system in `frontend/app/app.css`**
    (bright/modern; every page uses its `cf-*` classes — keep it the single source, do NOT inline component
    styles). Booking wizard, my-bookings, coach console, **master-diary calendar** (custom resource-timeline),
    owner/coach onboarding + Settings. **Asset/nav links are ABSOLUTE** (`/app.css`, `/js/…`, `/book.html`)
    so pages work at sub-paths like `/book/court`.
  - **Web/SEO:** `web_app.py` (+ `web_wsgi.py`), `frontend/marketing/` (restyled to the design system, stock
    court imagery), `frontend/_shared/` (`theme.css` + `chrome.py` + `branding.py` host→club resolver),
    `build_blog.py`, `frontend/login.html`, `migration/`.
- **Shipped & working (~90%):** owner/coach onboarding + **auto-member** signup · book courts/lessons
  (coach∩court)/classes (recurring, waitlists, rosters, attendance) · book-on-behalf + **book-for-a-child** ·
  **three configurable purchasing models — PAYG (per-duration) · membership (term plans) · tokens/bundles
  (prepaid packs, atomic draw-down + credit-back)** · membership-covered free courts (+ admin grant/revoke) ·
  **Yoco** online pay + reconcile + receipts + **refunds (admin direct + client request→approve)** ·
  **commission/coaching-settlement engine** (rent +/or %, split on collection, arrears statement, owner
  cockpit) · **self-service for all three roles** (client account/family/financials · coach
  console/clients/cockpit/statement · owner console/config/cockpit) · **in-app notifications** (email-ready) ·
  unified master diary · bright/modern UI + public site. **Remaining:** see `docs/specs/OUTSTANDING.md`.

## Payments, pricing & booking flow — LIVE end-to-end
**Online payments (Yoco) — wired & verified.** `yoco_billing/` is a pure adapter behind
`register_gateway`/`get_gateway` (`billing/` core untouched). An `online` booking creates an
`awaiting_payment` order + `held` booking → `book.js` calls `Pay.startYocoCheckout(order_id)` →
`POST /api/billing/yoco/checkout` returns Yoco's `redirect_url` → hosted page (card + Apple/Google/Samsung
Pay) → `POST /api/billing/yoco/webhook` (Standard-Webhooks verified) → `apply_payment_event` → order `paid`
+ booking `confirmed`. **GOTCHA the booking API returns `{booking:{order_id,status}, checkout}` — read
`res.booking.order_id`, NOT `res.order_id`** (that bug silently confirmed online bookings without
redirecting; fixed). **Two gates, both on:** `PAYMENTS_ENABLED=1`/`YOCO_ENABLED=1` (global, in `render.yaml`)
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
revenue), traffic + sign-up lines, traffic-source / top-page / by-country tables, settlement mix, NPS.
- **Source data:** website traffic from `core.usage_event` (`event_type='page_view'`); customers from
  `core.account`; bookings/revenue from `diary.*`/`billing.*`; NPS from `core.nps_response`.
- **First-party beacon (NEW — none existed before):** `frontend/js/analytics.js` (localStorage `anon_id` for
  unique visitors, referrer, UTM) → `POST /api/track/page` on load + SPA route change; **loaded on every page
  via the `web_app.py` head-injection** (single point). `beacon.py` captures **country from Cloudflare's
  `CF-IPCountry`** header. No cookies, no third parties. **Website-traffic panels accrue data from go-live**
  (historical events lack page-views/geo).
- **1050 (Ten-Fifty5) bridge (built — `analytics/bridge.py`):** a **business switcher** (platform-admin)
  CourtFlow · Ten-Fifty5 · All. The bridge fetches 1050's existing cockpit metrics over HTTPS (guarded,
  ~5-min cache) and normalises them; **All** sums COUNT metrics only (USD vs ZAR revenue is never summed —
  shown per-business). Config via `BRIDGE_TENFIFTY5_*` env (`sync:false`): **Option A** (live, no 1050 change)
  = URL + CLIENT_KEY + ADMIN_EMAIL; **Option B** (least-privilege) = URL + OPS_KEY against a dedicated 1050
  endpoint — the bridge auto-switches on which env is set. Unset → the 1050 column shows "not configured".
  See **`docs/12-tenfifty5-bridge.md`** (incl. the paste-in Option-B endpoint for the 1050 repo).

**Pricing model — per-duration PAYG + membership-covered courts.** A service carries ONE `billing.price`
row per offered duration (`duration_minutes` set, `unit='per_booking'`, `audience='any'`). `diary/pricing.py`:
`price_for(kind, duration_minutes)` (exact→nearest≤→any), `durations_for(kind[,coach])`, `has_active_membership`.
Seed: Court Hire 30/60/90/120 = R90/150/210/280; Private Lesson 30/60 = R250/400; classes per_session. **The
Wix-era "member R0" court tier is GONE** (the seed deactivates legacy no-duration court prices). An **active
membership makes COURT bookings free** (`settlement_mode=membership_covered`, resolved server-side via
`has_active_membership` — guarded: courts only, never lessons). Admin grants/revokes in **People**
(`POST|DELETE /api/admin/members/<user_id>/membership` → `billing.membership_subscription`, provider='manual').
Self-serve membership purchase (a Yoco subscription) is the next piece.

**Booking flow (`book.js`, Wix "Schedule your service" style):** Service → **Duration** (court/lesson; live
per-duration price, or "Covered by your membership") → **Schedule** (month calendar | 2-col time blocks |
coach/court dropdowns with "Any" defaults) → **Pay & confirm** (at court / monthly / membership / online) →
slick animated success. Class flow skips Duration (sessions have fixed times): Service → Schedule (pick a
session) → enrol. **When editing `book.js`, PRESERVE** the `createBooking` call + the online seam
(`res.booking.order_id` → `Pay.startYocoCheckout`).

**Capacity-sweep WITHOUT a cron:** abandoned `held` bookings are released by **lazy expiry** —
`diary.bookings.release_expired_holds` runs at the top of `compute_availability` + `create_booking` (cancels
`held` rows past `held_until`). No paid cron needed; the four `render.yaml` crons stay commented out.

## Commands
- **Compile gate (CI-style, no infra):** `python -m py_compile $(git ls-files '*.py')` — there is no
  pytest suite; this + the integration script below are the gates (match 1050).
- **Boot all schemas / idempotency gate:** `python -m db` (run it **twice** → second run must be a no-op).
- **Seed club #1:** `python -m scripts.seed_nextpoint` · **provision another tenant:** `python -m scripts.provision_club`
- **Run the API locally:** `gunicorn wsgi:app` (or `python -m app`) — needs `DATABASE_URL`.
- **Run the web/portal service locally:** `python web_wsgi.py` (DB-less; defaults to `PORT=5060`).
- **Fire a cron job by hand:** `python -m crons.trigger <reminders|capacity-sweep|monthly-invoice|membership-refill>`
  (needs `CRON_API_BASE` + `OPS_KEY`; the trigger only POSTs to `/api/cron/*` — see cron note below).
- **Rebuild the blog/SEO output:** `python build_blog.py`

## Verifying (no live infra needed)
- **Compile:** `python -m py_compile` over the tree (CI-style gate; there is no pytest suite — match 1050).
- **Backend integration:** boot all schemas + a booking→order→event chain against a throwaway Postgres
  (`docker run postgres:16`, set `DATABASE_URL`, `python -m db` twice for the idempotency gate, then
  `python -m scripts.seed_nextpoint`). The cross-lane flow (diary→billing→CRM), the double-book refusal,
  and desk-payment idempotency were proven this way (12/12).
- **Web service:** Flask test client against `web_app.py` (DB-less) — host-switch, portal-shell serving,
  robots/sitemap, branded 404 (14/14).
- **Yoco payments:** offline signature verify (valid / tampered / stale / missing / wrong-secret) +
  `parse_event` mapping (21/21); scratch-DB settlement chain (online order → `charge_succeeded` → order
  `paid` + booking `held→confirmed` → replay = no-op → `refunded` record-only, booking NOT reversed) (15/15);
  full HTTP webhook path via Flask test client (bad sig → 401 + order untouched, good sig → 200 paid+confirmed,
  replay idempotent, config probe advertises yoco without leaking the secret) (10/10). All green.

## Still needs Tomo (config, not code) — infra is otherwise live
- **S3** (`S3_BUCKET` + AWS keys) for coach **photo uploads** — until set, coaches paste a photo URL.
- **SES** verified sender for **invite/confirmation emails** — until then coach invite links are shared
  manually; Klaviyo marketing also dark until `KLAVIYO_API_KEY`.
- **Yoco keys** (`YOCO_*`) — DONE (set in Render; payments live). Each club still opts in via the
  Settings → Payments toggle.
- **DNS / SEO cutover** for `nextpointtennis.com` (supervised — never an agent). See `docs/11 §5`, `docs/07`.

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
- **One Postgres DB, six schemas** (idempotent boot DDL, no migration framework):
  - `club.*` tenants/config/branding/location/policies · `iam.*` user↔Clerk, membership, coach_profile
  - `diary.*` resources, availability, booking, class_session, enrolment, waitlist, recurrence (**the heart**, `docs/03`)
  - `billing.*` price_list, product, order, payment, account_ledger, membership_subscription (`docs/05`)
  - `core.*` account/user/person, usage_event, consent, nps (ported from 1050 `core_db`) · `support.*` optional FAQ bot
- **Integrations (reused accounts, new project-scoped values):** Clerk (identity), Yoco/PayPal
  (provider-agnostic gateway, signed webhooks), AWS S3 (assets) + SES (transactional fallback),
  Klaviyo (all booking/lesson/class confirmations + lifecycle, fed by `core.*` event feed).

### Decoupling interfaces (why parallel lanes work)
- **Schema** (`docs/02`) is the contract between the diary, billing, and CRM lanes — agreed first.
- **Event contract** (`contracts/events.md`) decouples producers (diary, billing → `emit(event, payload)`)
  from the consumer (CRM/Klaviyo).
- **Gateway protocol** (`docs/05 §2`, `apply_payment_event(provider)`) isolates each payment adapter.

## Build order & multi-agent lanes (docs/09)
Do **Phase 0** (foundation: repo, `render.yaml`, DB connect, schema bootstrap, Clerk auth port,
club resolution) + **Phase 1** (tenancy schemas, permissions, seed NextPoint as club #1) **sequentially
and commit**. Only then fan out parallel lane agents — each owns a path lane and touches only it:

| Agent | Lane (owns) | Builds |
|---|---|---|
| A — Foundation | `app.py`, `wsgi.py`, `render.yaml`, `db.py`, `iam/`, `auth/` | Skeleton, boot/schema runner, Clerk port, club resolution. **Runs first.** |
| B — Diary | `diary/` | Booking/lesson/class CRUD, exclusion constraint, recurrence, waitlist, crons. |
| C — Billing | `billing/`, `yoco_billing/`, `paypal_billing/` | order/ledger, `apply_payment_event`, gateway adapters. |
| D — CRM | `core/`, `marketing_crm/` | `core.*` port, tracking, crm_sync, consent, Klaviyo. |
| E — Frontend | `frontend/` | Booking wizard, coach diary, club-admin console, `/login`. |
| F — Marketing/SEO | `frontend/marketing/`, `build_blog.py`, `migration/` | Host-switched site, blog, sitemap, URL inventory + 301 map. |

Use **git worktrees per lane** (or branch-per-lane); merge to `main` per phase. Don't fan out before
the schema + boot runner exist. **Shared interface files** (`contracts/events.md`, schema docs,
`render.yaml` env list): coordinate edits, Agent A is authoritative.

## Tech defaults (match 1050 so reuse is clean — docs/09 §6)
- Python 3.12 + Flask + Gunicorn + psycopg + Postgres. **Idempotent boot DDL** (`init()` on boot,
  `ADD COLUMN IF NOT EXISTS`) — no Alembic/migrations. Add `btree_gist` + `pgcrypto` extensions
  (`btree_gist` powers the diary's no-double-booking exclusion constraint).
- Vanilla-JS SPAs (no heavy framework), reusing 1050's CSS/chart conventions; Clerk JS on `/login`.
  The one place to add a dependency is a calendar lib for the diary UI (evaluate FullCalendar resource-timeline).

## Verification gates (run before merging — docs/03 §10, docs/09 §5)
There is no test runner yet; create one. Each phase has a concrete "done when":
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
  audit) and is not in `.gitignore`. Don't commit it with platform changes, and don't confuse it with
  `frontend/marketing/` (the host-switched marketing site) or `marketing_crm/` (the CRM lane).

## Needs Tomo (an agent cannot do these)
See the `BUILD_PROMPT.md` pre-flight checklist: `DATABASE_URL`, a new Clerk app, S3/SES, Klaviyo sender
domain auth, Yoco keys — and the DNS / SEO cutover (supervised).

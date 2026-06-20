# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This repo is the **multi-tenant tennis club management platform** (working name "CourtFlow").
NextPoint Tennis is club #1, migrating off Wix.

## Current state (read this first)
- **Backend + frontend built, integration-verified, NOT yet run against live infra.** Phases 0–6 are
  scaffolded on `master`. The genuinely-new code (diary engine, multi-tenancy, settlement) is done;
  Yoco online pay (Phase 7) and the supervised DNS/SEO cutover (Phase 6 execution) remain.
- **Source of truth:** `docs/` (`00`→`11`). `docs/11-build-readiness-and-decisions.md` has the locked
  decisions + the validated 1050 reuse map. When a decision isn't in `docs/`, ask.
- **What exists now (lane → modules):**
  - **A Foundation:** `app.py` (api factory), `wsgi.py`, `db.py` (lazy engine + idempotent boot runner,
    `BOOT_MODULES`), `auth/` (Clerk JWKS verifier + club-scoped `Principal`), `iam/` (user/membership/
    permissions), `club/` (tenant schemas), `core/` (CRM identity + consent), `scripts/` (seed/provision),
    `crons/` (thin cron dispatcher), `render.yaml`.
  - **B Diary:** `diary/` — schema (the GiST no-double-book `EXCLUDE` constraint), `bookings.py`,
    `classes.py`, `availability.py`, `recurrence.py`, `crons.py`, `routes.py` (`/api/diary/*`).
  - **C Billing:** `billing/` — schema, `events.py` (`apply_payment_event`, idempotent), `gateway.py`
    (`PaymentGateway` Protocol + `ManualGateway`; Yoco/PayPal adapters are the Phase-7 extension point),
    `orders.py`, `ledger.py`, `routes.py` (`/api/billing/*`).
  - **D CRM:** `marketing_crm/` — `tracking/` (`emit()`→`core.usage_event`), `crm_sync/` (Klaviyo +
    HubSpot adapters, dark until `KLAVIYO_API_KEY`), `consent/`, `backoffice/` (cockpit), `email/` (SES fallback);
    `contracts/events.md` is the producer/consumer contract.
  - **E Frontend:** `frontend/app/` + `frontend/js/` — booking wizard, my-bookings, coach console,
    admin master diary, `auth_client.js` (Clerk Bearer helper).
  - **F Web/SEO:** `web_app.py` + `web_wsgi.py` (the **`courtflow-web`** host-switched, DB-less service),
    `frontend/marketing/`, `frontend/_shared/` (per-club theme), `build_blog.py`, `frontend/login.html`,
    `migration/` (301 map + cutover runbook — never auto-executed).
- **Two services:** `courtflow-api` (`wsgi:app`, has DB) and `courtflow-web` (`web_wsgi:app`, no DB,
  serves marketing + portal shells + `/login`).

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

## Still needs Tomo before it can RUN/deploy
New Postgres `DATABASE_URL`; new Clerk app (`AUTH_JWKS_URL`/`AUTH_ISSUER`/`CLERK_PUBLISHABLE_KEY` + an
`email` JWT-template claim); then Klaviyo sender, Yoco keys, S3/SES, and the supervised DNS cutover at
their phases. See `docs/11 §5`. **git is already initialized** (history starts at the spec commit).

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

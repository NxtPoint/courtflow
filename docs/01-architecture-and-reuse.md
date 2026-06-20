# 01 — Architecture & The 1050 Reuse Map

## 1. Target architecture (one picture)

```
                         ┌───────────────────────────── Browser ─────────────────────────────┐
                         │  Marketing site (club-themed)   Portal SPA (member/coach/admin)     │
                         └───────────┬───────────────────────────────┬────────────────────────┘
                                     │ HTTPS                          │ HTTPS (Clerk JWT, Bearer)
                                     ▼                                ▼
   Clerk (identity, reused) ───────────────────────►  ┌──────────── Render (new "courtflow" blueprint) ───────────┐
                                                       │  web: api          (Flask+Gunicorn) — booking/diary API   │
   Yoco / PayPal (gateways) ──signed webhook────────► │  web: portal+site  (Flask, host-switched, static SPAs)    │
                                                       │  cron: reminders / capacity-sweep / monthly-invoice       │
                                                       └───────────┬───────────────────────────────────┬──────────┘
                                                                   │                                   │
                                                                   ▼                                   ▼
                                                     Postgres (NEW DB, schemas:           AWS (reused acct):
                                                     club / iam / diary / billing /        S3 (assets, exports)
                                                     core / support)                       SES (transactional fallback)
                                                                   │
                                                                   ▼
                                                     Klaviyo (reused acct) ◄── core.* event feed (crm_sync)
```

Same shape as 1050 — **fewer services** (no GPU/ML pipeline, no video workers). The booking platform
is CRUD‑heavy and latency‑sensitive, not compute‑heavy.

## 2. Services (new Render blueprint `render.yaml`)

| # | Service | Type | Role |
|---|---|---|---|
| 1 | `courtflow-api` | web (Python 3.12, Flask+Gunicorn) | The booking/diary/billing API. Clerk‑JWT auth. All `club_id`‑scoped. Custom domain `api.courtflow.app` (or `api.nextpointtennis.com` if NextPoint keeps the API subdomain — see §6). |
| 2 | `courtflow-web` | web (Python, Flask+Gunicorn) | Serves the **public marketing site** (host‑switched per club) **and** the portal SPAs (member/coach/admin). Mirrors 1050's `locker-room` host‑switch pattern. |
| 3 | `courtflow-cron` *(or Render cron jobs)* | cron | Booking reminders, no‑show sweep, monthly‑account invoice run, subscription/membership refill. |

> Start with **2 web services + crons** (api + web). Split marketing into its own service later only
> if deploy‑coupling hurts (1050 kept `marketing_app.py` as that escape hatch — we mirror it).

Reuse 1050's gunicorn/timeouts conventions; booking endpoints are fast so default timeouts (60–120s)
are fine — no 1800s upload timeouts needed.

## 3. The reuse map — copy vs build‑new

### ✅ Copy / port almost verbatim (the 80%)

| Capability | 1050 source | How we reuse |
|---|---|---|
| **Render blueprint pattern** | `render.yaml` | New blueprint, same env‑var discipline (`sync:false` secrets, public Clerk key inline). |
| **Clerk auth verification** | `auth_v2/` (JWKS verify → principal, dual‑mode) | Port wholesale. Add `club_id` resolution on top (see `04-auth-and-roles.md`). |
| **Idempotent schema bootstrap** | `models_billing.py::billing_init()`, `db_init.py`, `_ensure_*` | Same pattern for all new schemas — `init()` on boot, `ADD COLUMN IF NOT EXISTS`, no migration framework. |
| **Provider‑agnostic billing path** | `subscriptions_api.apply_subscription_event(payload, provider)` + `billing.*` | This is the template for the payment abstraction. Generalise to `apply_payment_event(provider)`. See `05`. |
| **Plan/price catalogue** | `paypal_billing/plans.py` + `catalog.json` | Same idea: prices in code/DB, provider plan‑ids in a catalogue, `GET /api/billing/config` probe. |
| **Own‑CRM + event tracking** | `core_db/` (`core.*`), `marketing_crm/tracking/`, `crm_sync/` | Port `core.account/user/person/usage_event`, the page‑view beacon, and the Klaviyo/HubSpot sync (Klaviyo‑on, HubSpot‑dormant). |
| **Klaviyo sync** | `marketing_crm/crm_sync/klaviyo.py` (self‑gates on key) | Reuse; feed booking lifecycle events. |
| **Marketing site engine** | `locker_room_app.py` host‑switch, `build_blog.py`, shared nav/footer, JSON‑LD, sitemap/robots, branded 404 | Port the whole static‑site + SEO toolkit. Make it **per‑club themed**. |
| **Consent capture** | `marketing_crm/consent/` + `consent.js` | Reuse for marketing opt‑in + (important) **parental consent for minors** — NextPoint has many junior players. |
| **Support bot** | `support_bot/` (Haiku + FAQ) | Optional MVP+; port later with a NextPoint FAQ. |
| **Cockpit (admin analytics)** | `marketing_crm/backoffice/` + `cockpit.html` | Reuse the pattern for the **club‑admin** revenue/occupancy dashboard. |
| **SES transactional email** | `coach_invite/email_sender.py` | Reuse as the *fallback* transactional channel behind Klaviyo. |

### 🟡 Adapt heavily

| Capability | Why it changes |
|---|---|
| `billing.*` schema | Becomes **bookings + memberships + settlement**, not video credits. Keep the *grant/consume/idempotency* discipline; change the nouns. |
| Roles | 1050 has `player_parent` / `coach`. We add `member`, `guest`, `club_admin`, `platform_admin`, all **club‑scoped**. |
| Dashboards | Not match analytics — instead **diary views, occupancy, coach utilisation, revenue, attendance**. Same "thin gold view" technique. |

### 🔴 Build new (the genuinely new 20%)

| New thing | Where specified |
|---|---|
| **Multi‑tenant core** (`club_id` everywhere, club config, theming, tenancy isolation) | `02-data-model-multitenant.md` |
| **The diary/booking engine** (resources, availability, booking lifecycle, recurrence, conflicts, waitlists) | `03-diary-booking-engine.md` |
| **Yoco gateway adapter** + 3 settlement modes (online / at‑court / monthly account) | `05-payments-abstraction.md` |
| **Club admin console + club onboarding** | `08-admin-and-club-onboarding.md` |

### ⛔ Do NOT bring over

- The ML/T5 pipeline, AWS Batch GPU, SportAI/Technique APIs, video trim workers, `bronze/silver/gold`
  match analytics, `ml_analysis.*`. None of it is relevant to bookings. (Keep the *naming discipline*
  of thin views; drop the machinery.)

## 4. New database schemas (one Postgres, like 1050)

```
club.*      tenants: club, club_config, club_branding, location, currency, policies
iam.*       identity mapping: user (↔ Clerk), membership (user↔club↔role), coach_profile
diary.*     resources, availability_rule, time_off, booking, booking_party, class_session,
            enrolment, waitlist, recurrence, cancellation
billing.*   price_list, product (court/lesson/class/membership), order, order_line, payment,
            payment_attempt, account_ledger (monthly tab), membership_subscription
core.*      account/user/person, usage_event, consent, nps   (ported from 1050 core_db)
support.*   (optional) FAQ bot
```

Full DDL sketch in `02-data-model-multitenant.md`.

## 5. Environment & secrets (reuse accounts, new values)

Reuse the **same Render org, Clerk, AWS, Klaviyo accounts**; provision **new** project‑scoped values:

- `DATABASE_URL` — **new** Postgres instance (separate DB for clean separation/scale).
- Clerk: a **new Clerk application/instance** for the platform (e.g. `clerk.courtflow.app`) OR reuse
  the existing instance with org/club claims — see `04-auth-and-roles.md` §2 (recommend new instance).
- `S3_BUCKET` — new bucket (e.g. `courtflow-prod-assets`); reuse AWS keys/region.
- `SES_FROM_EMAIL` — `bookings@nextpointtennis.com` (per club later; one verified sender for MVP).
- `KLAVIYO_API_KEY` — reuse the account; consider a separate Klaviyo *list/segment* per club, or a
  `club` profile property for segmentation (see `06`).
- Gateway: `YOCO_SECRET_KEY`, `YOCO_PUBLIC_KEY`, `YOCO_WEBHOOK_SECRET` (Tomo has these).
- Standard: `OPS_KEY`, `AUTH_JWKS_URL`, `AUTH_ISSUER`, `CLERK_PUBLISHABLE_KEY`.

Keep the 1050 rule: **secrets are `sync:false` in `render.yaml`**, public Clerk key inline, go‑live
flags (`PAYMENTS_ENABLED`, provider env) committed so a blueprint sync can't wipe them.

## 6. Domains & DNS (and the api.nextpointtennis.com wrinkle)

> ⚠️ **Important:** 1050's API already runs on **`api.nextpointtennis.com`** (see `render.yaml` line 8
> and `AUTH_API_BASE`). That subdomain is currently pointed at the 1050 service. When we build the
> NextPoint platform we must **not break that**. Options:
> - Give the new platform its own API host (`api.courtflow.app`) and point **`www`/apex
>   nextpointtennis.com** at the new `courtflow-web` service (host‑switched marketing).
> - If we want NextPoint's API on its own brand, use `api2.nextpointtennis.com` or move 1050's API to
>   `api.ten-fifty5.com` first (coordinate — changing a Render custom domain can recreate the service).
>
> **Action for build:** confirm current DNS for `nextpointtennis.com` (registrar + which records point
> to Wix vs Render) before the SEO cutover. Detailed cutover steps in `07-marketing-site-and-seo-migration.md`.

## 7. Why this is safe to build fast

Every hard problem here (auth, idempotent schema, provider‑agnostic billing, host‑switched SEO site,
Klaviyo sync, consent) is **already solved in 1050 and running in production**. We are re‑assembling
proven parts around a new domain model (the diary). That's why a multi‑agent single‑session build is
realistic — see `09-build-plan-and-agents.md`.

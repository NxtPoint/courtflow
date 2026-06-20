# 10 — Reuse Port Map: Ten-Fifty5 (1050) → NextPoint/CourtFlow

> **Purpose.** Make reuse *mechanical*. For each capability, this names the **exact 1050 source files**
> (in `C:\dev\webhook-server`, READ-ONLY), what to **copy**, what to **change** (mostly: add `club_id`
> + multi-tenancy), and what to **drop**. If you find yourself writing one of these from scratch, stop
> and open the 1050 file first — it already works in production.
>
> **Rule:** copy & adapt into this repo's own modules. Do **not** `import` from `C:\dev\webhook-server`
> and never write to it. This is a port, not a dependency.

## 0. Prerequisite — both repos visible

Open the workspace so Claude Code can read 1050 while writing here. Recommended: open at **`C:\dev`**
(both `webhook-server` and `nextpoint` show as roots), or add `webhook-server` as a second workspace
folder. Build/commit ONLY in `nextpoint`; treat `webhook-server` as read-only reference.

## 1. Auth — `auth_v2/`  → `auth/`  (copy ~verbatim)

| 1050 file | Port to | Change |
|---|---|---|
| `auth_v2/verifier.py` | `auth/verifier.py` | Clerk JWKS fetch/cache + JWT verify (`iss`, signature). Keep almost as-is; point at the **new** Clerk app's JWKS URL. |
| `auth_v2/principal.py` | `auth/principal.py` | Resolves `{user_id, email}` from the token. **Extend**: upsert `iam.user`, load `iam.membership`, resolve active `club_id` + role by host/`X-Club`/default (`docs/04 §3`). |
| `auth_v2/__init__.py`, `selftest.py` | `auth/__init__.py`, `auth/selftest.py` | Keep the public surface + the selftest (handy boot check). |

Drop 1050's legacy shared-`CLIENT_API_KEY` client path — keep only an `OPS_KEY` server-to-server path
for crons/admin. Add a central `iam/permissions.py` (`can(principal, action, resource)`, `docs/04 §4`).

## 2. Billing core & the provider-agnostic pattern — THE most valuable port

The whole payment abstraction (`docs/05`) is a generalisation of code that already exists:

| 1050 source | Port to | What to take |
|---|---|---|
| `subscriptions_api.py::apply_subscription_event(payload, provider)` | `billing/events.py::apply_payment_event(event)` | **The template.** One normalize→record→grant path; idempotent via an event hash; multiple providers, one path. Rename nouns to bookings/orders/ledger; keep the idempotency discipline. |
| `models_billing.py::billing_init()` + `_ensure_*` | `billing/schema.py::init()` | The **idempotent boot DDL** pattern (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`). Reproduce for `billing.*` (`docs/02 §5`). `billing.payment` (record-only money log, unique `(provider, provider_payment_id)`) ports almost directly. |
| `entitlements_api.py` (derived-flags UPSERT) | `billing/derive.py` (optional) | The "one SQL statement derives all flags" technique — reuse for any derived booking/membership flags. |
| `db_init.py` (bronze bootstrap) | `db.py` boot runner | The **boot-time schema runner** pattern (each module exposes `init()`, called on app start). Drop all bronze/silver/ML content. |

## 3. Payment gateway adapter — `paypal_billing/` is the shape for `yoco_billing/`

`paypal_billing/` is a clean, vanilla adapter. Use it as the literal blueprint:

| 1050 file | Yoco port | Note |
|---|---|---|
| `paypal_billing/client.py` | `yoco_billing/client.py` | Thin REST client. Swap PayPal endpoints/auth for Yoco's (**fetch Yoco's current API docs first** — `docs/05 §6`). |
| `paypal_billing/webhook.py` | `yoco_billing/routes.py` | Signature-verify → refetch → call `apply_payment_event`. Server-side create-checkout (amount/order set server-side, never trust client). `GET /api/billing/config` probe. |
| `paypal_billing/plans.py` + `catalog.json` + `catalog.py` | `billing/price` table + `billing/provider_plan` | The **catalogue pattern**: prices are ours (DB), provider plan-ids in a small catalogue. |
| `paypal_billing/README.md` | `yoco_billing/README.md` | Mirror the runbook (env, rollback flag, test flow). |

Also **port `paypal_billing/` itself** as the *second* `PaymentGateway` implementation so the
abstraction is proven with ≥2 providers (USD/international clubs later). Implement the protocol from
`docs/05 §2`; keep provider-specifics inside adapters only.

## 4. Own-CRM — `core_db/`  → `core/`  (port, add `club_id`)

| 1050 file | Port | Change |
|---|---|---|
| `core_db/db.py`, `schema.py` | `core/db.py`, `core/schema.py` | Connection + idempotent schema. Add `club_id` to relevant tables. |
| `core_db/models.py` | `core/models.py` | `account/user/person/usage_event/consent/nps`. Keep; add tenancy. |
| `core_db/repositories/accounts.py`, `consent.py`, `feedback.py`, `subscriptions.py` | `core/repositories/*` | Reuse account/consent/feedback repos. **`consent.py` is important** — it's the parental/marketing consent write-path for minors (`docs/04 §5`, `docs/06 §5`). |
| `core_db/repositories/matches.py` | — | **Drop** (1050 match-specific). Replace with booking-domain repos in `diary/`. |
| `core_db/seed.py`, `backfill.py` | `scripts/seed_nextpoint.py` | Reuse the seeding pattern; new content (club #1, courts, coaches, prices). |

## 5. Marketing/CRM stack — `marketing_crm/`  → `marketing_crm/`  (port, re-author contracts)

| 1050 file | Port | Change |
|---|---|---|
| `marketing_crm/tracking/{events,beacon,client}.py` | same | Event emit + page-view beacon → `core.usage_event`. Add `club_id`. Producers (diary/billing) call `emit(event, payload)`. |
| `marketing_crm/crm_sync/{klaviyo,sync}.py` | same | Klaviyo profile/event forwarding (self-gates on `KLAVIYO_API_KEY`). Add `club` profile trait for per-club segmentation. |
| `marketing_crm/crm_sync/hubspot.py` | keep dormant | Same as 1050: retained, no key, do not invest. |
| `marketing_crm/consent/blueprint.py` | same | Consent capture endpoints + screens. |
| `marketing_crm/feedback/blueprint.py` | same | In-app feedback + NPS (post-lesson prompt later). |
| `marketing_crm/backoffice/{blueprint,views.py}` | `club-admin cockpit` | The **cockpit pattern** (thin views over the SoR). Re-point views at booking metrics: occupancy, coach utilisation, revenue by settlement mode, attendance, membership MRR. |
| `marketing_crm/klaviyo/flow_builder.py` + `flow_build_spec.md` | same | **Gold** — the Klaviyo template/flow-build helper. Reuse to create the booking-confirmation templates. |
| `marketing_crm/contracts/events.md`, `lifecycle_stages.md`, `data_dictionary.md` | `contracts/*` | **Re-author** for the booking domain (events list in `docs/06 §2`). Keep the format; change the content. |

## 6. Marketing site + SEO — `locker_room_app.py` + `build_blog.py` → `courtflow-web`

| 1050 source | Port | Change |
|---|---|---|
| `locker_room_app.py` (host-switch `_is_marketing_host()`, `_html()`, SPA serving, branded 404, robots/sitemap routes) | `web_app.py` | The host-switched serving engine. **Parameterise by `club.branding`** (theme/domain per club) instead of hardcoded ten-fifty5 hosts. |
| `build_blog.py` | `build_blog.py` | Static blog generator (JSON-LD, OG, sitemap) — port as-is, theme per club. |
| `frontend/` shared nav/footer/CSS, `og/`, favicon | `frontend/` | Reuse the design-system conventions + JSON-LD; restyle to NextPoint branding. |
| `docs/business/marketing-and-seo.md` + `_archive/wix-migration-record.md` | reference | The **proven Wix→Render cutover playbook** — reuse the reversible-cutover sequence (`docs/07`). |

## 7. Crons — `cron_*.py` pattern → `courtflow-cron`

Reuse 1050's "**cron = thin HTTP trigger, endpoint owns the logic**" pattern
(`cron_monthly_refill.py`, `cron_capacity_sweep.py`). New crons: reminders, capacity/hold-sweep,
monthly-account invoice, membership refill (`docs/03 §7`).

## 8. Render blueprint — `render.yaml` → new `render.yaml`

Copy the **structure + env-var discipline** (secrets `sync:false`, public Clerk key inline, go-live
flags committed). New services: `courtflow-api`, `courtflow-web`, crons. Drop the ingest/video/Batch
services entirely.

## 9. Do NOT port (1050-specific, irrelevant to bookings)

`ml_pipeline/`, `ml_analysis.*`, AWS Batch/GPU, SportAI/Technique APIs, `video_pipeline/`, ingest +
video-trim workers, `bronze/silver/gold` match analytics, the T5 docs. Keep only the *naming
discipline* of thin presentation views — drop the machinery.

## 10. Net effect

Auth, billing core, gateway adapter, own-CRM, Klaviyo sync, consent, marketing site + SEO toolkit, and
the cron/blueprint patterns are **all copy-adapt from running code**. The genuinely new build is the
**diary engine** (`docs/03`), **multi-tenancy** (`docs/02 §1`), and the **Yoco adapter** (`docs/05 §6`)
— which had to be written regardless. That's why this is a port, not a from-scratch project.

# 11 — Build Readiness & Locked Decisions

> Written after a full read of `docs/00`–`10` **and** a line-level study of the Ten-Fifty5 (1050)
> reference code at `C:\dev\webhook-server` (read-only). Purpose: lock the open decisions, record what
> the reuse study confirmed, and define the exact first cut so agents can build with no re-derivation.
> **Status: ready to build Phase 0–1.** The only true blockers are pre-flight *secrets* (needed to
> *run/verify*, not to *write* code).

## 1. Decisions locked (ratify these)

| # | Decision | Choice | Source |
|---|---|---|---|
| D1 | **Bookings billing is its own schema** | `billing.*` (product/price/order/order_line/account_ledger/payment/payment_attempt/membership_subscription) is **separate** from 1050's credit-grant model. We reuse only the *idempotency discipline* + the provider-agnostic `apply_payment_event` core — **not** the nouns. | Tomo 2026-06-20; already modelled in `docs/02 §5`, `docs/05` |
| D2 | **Reuse the engine, run separately where tennis differs** | Copy-adapt auth, billing core, CRM, Klaviyo, consent, marketing/SEO, crons, blueprint. Do **not** tightly couple to 1050 (no imports; it's a port). Keep clean event + gateway interfaces so **Ten-Fifty5 can later plug in as a service academies consume** — loose coupling at the boundary, not shared internals. | Tomo 2026-06-20 |
| D3 | **One Klaviyo, NextPoint brand, for now** | Single Klaviyo account; tag every profile with a `club` trait (`club.branding.klaviyo_list_id` optional). Consolidate under the NextPoint brand short-term. Per-club accounts deferred. | Tomo 2026-06-20; `docs/06 §6` |
| D4 | **REUSE 1050's existing Clerk** (`clerk.ten-fifty5.com`) — no new app (Tomo 2026-06-20, supersedes `docs/04 §2`'s "new app" recommendation). Its session token already carries `email`; `AUTH_JWKS_URL`/`AUTH_ISSUER`/`CLERK_PUBLISHABLE_KEY` are committed inline in `render.yaml`. A token is valid platform-wide; access is gated by `iam.membership`, so a 1050-only user authenticates but has no club/role until granted one (safe default). Per-club login branding stays at the app layer (read `club.branding` by host). | Tomo 2026-06-20 |
| D5 | **Member memberships are per (club_id, user_id)**; the **platform fee** (club → us) is a *separate, later* concern (Phase 5, `docs/08 §3`). No conflation. | `docs/02 §5`, `docs/08 §3` |
| D6 | **Drop 1050's legacy client auth.** Port the Clerk JWT verifier verbatim; keep dual-mode (`OPS_KEY`) **only** for server-to-server/cron/admin — never as a client path. Replace hardcoded `ADMIN_EMAILS` with the central `iam/permissions.py` role model from day one. | `docs/04 §1,§4`; reuse study |
| D7 | **Tenancy = shared schema + `club_id` discriminator**, enforced app-side now; **RLS designed-in, enforcement deferrable to Phase 8.** Tables carry `club_id NOT NULL` so RLS is a drop-in. | `docs/02 §1` |
| D8 | **Settlement launches without a gateway.** `at_court` + `monthly_account` + `membership_covered` + `free` ship first; `online` (Yoco) is a flag flip behind the same `apply_payment_event` core. | `docs/05 §5,§9` |

## 2. What the reuse study confirmed (validated against real 1050 code)

The `docs/10` port map is **accurate** — every "copy this" points at working production code:

- **Auth** — `auth_v2/verifier.py` is copy-verbatim (JWKS cache + RS256 verify, fail-closed, lazy PyJWT
  import). `principal.py` is the one heavy-adapt: extend `Principal` with `club_id`/`role`, resolve
  `iam.membership` after JWT verify. Drop `_legacy_principal()` + `ADMIN_EMAILS`.
- **Billing core** — `subscriptions_api.apply_subscription_event(payload, provider)` is already
  **domain-agnostic**: normalize → SHA-256 event-hash dedupe → upsert state → grant. It maps onto our
  `apply_payment_event` almost unchanged. `billing.payment` (record-only, `unique(provider,
  provider_payment_id)`) ports directly. Refunds record-only, never auto-reverse (keep that rule).
- **Gateway** — `paypal_billing/` is a clean 3-tier adapter (client / webhook-receiver / server-side
  checkout). There is **no explicit `PaymentGateway` ABC today** — the protocol is implicit. Our
  `docs/05 §2` formalises it as a `Protocol`; the PayPal port becomes the second implementation that
  proves the abstraction.
- **CRM/Klaviyo** — `marketing_crm/tracking` (`emit(event, payload)` → `core.usage_event`, fire-and-
  forget thread) + `crm_sync/klaviyo.py` (self-gates on `KLAVIYO_API_KEY`) port cleanly. `consent/`
  already implements the **parental/minor** model we need (subject=junior, granted_by=guardian).
- **Web/infra** — `locker_room_app.py` host-switch (`_is_marketing_host()` → `MARKETING_HOSTS`) is the
  template for per-club theming by host. `build_blog.py` (JSON-LD + sitemap + OG, dependency-free)
  ports as-is. Cron = thin HTTP trigger, endpoint owns logic. `render.yaml` env discipline
  (`sync:false` secrets, committed go-live flags) is the blueprint; **drop ingest/video/GPU services.**
- **Boot/schema** — `db_init.py` engine (pool_pre_ping, pool_recycle=1800 for Render) + the
  `CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` pattern, each module exposing `init()`
  called on boot, try/except-wrapped. No migration framework. Copy verbatim.

**Net:** the genuinely-new build is exactly three things — the **diary engine** (`docs/03`, the GiST
exclusion constraint is the crown jewel), **multi-tenancy** (`club_id` everywhere), and the **Yoco
adapter** (`docs/05 §6`). Everything else is copy-adapt from running code.

## 3. Phase 0–1 — the exact first cut (sequential, one agent, commit before fan-out)

Per `docs/09`, Phase 0+1 are the dependency root; they land and **verify** before B–F fan out.

**Phase 0 — Foundation (Agent A lane: `app.py`, `wsgi.py`, `render.yaml`, `db.py`, `auth/`, `iam/`)**
1. Repo skeleton + `requirements.txt` (Flask, gunicorn, psycopg, SQLAlchemy, PyJWT[crypto], boto3).
2. `db.py` — port 1050's engine + boot-runner; enable `pgcrypto` + `btree_gist` on boot.
3. `auth/` — port `auth_v2/` verifier verbatim; new Clerk app's JWKS/issuer.
4. `iam/` — `iam.user`/`membership`/`coach_profile`/`player_profile` schema + `init()`; principal
   extension (resolve `club_id` + role by host/`X-Club`/default); central `permissions.py` (`can()`).
5. `render.yaml` — `courtflow-api` + `courtflow-web` + crons; env discipline; **no** ingest/video/GPU.
6. **DoD:** app boots; `init()` idempotent (run twice = no error); a Clerk JWT resolves a principal
   with `club_id` + role. *(Verification needs the new `DATABASE_URL` + Clerk app — see §5.)*

**Phase 1 — Tenancy + seed (Agent A continues)**
1. `club.*` schema (club/location/branding/policy) + `init()`.
2. `core.*` port (account/user/person/usage_event/consent/nps) + `club_id`.
3. `scripts/seed_nextpoint.py` (idempotent) — NextPoint club, Killarney location, branding (ZAR/JHB),
   Wix policies, 8 hard + 1 clay court, coach profiles (Neville Godwin, Ross Nemeth), class resources.
4. `scripts/provision_club.py` + a "template club" so club #2 is a clone (`docs/08 §4`).
5. **DoD:** NextPoint seed present and idempotent; membership/role resolution works end-to-end.

**Then fan out** (after Phase 0–1 verified + committed): **B**-Diary (`diary/`, the exclusion
constraint + the `docs/03 §10` edge-case asserts), **C**-Billing (`billing/`, `apply_payment_event` +
manual provider), **D**-CRM (`core/` events + Klaviyo), **F**-Marketing (`web_app.py` host-switch +
`build_blog.py`). **E**-Frontend integrates last against B/C/D (mock until live). Each agent in its own
worktree/lane; `contracts/events.md` + schema are shared interface files (Agent A authoritative).

## 4. Forward-compat principle (for the Ten-Fifty5-as-a-service future)

Per D2, keep two boundaries clean so 1050 can later attach without a rewrite:
- **Event contract** (`contracts/events.md`) — producers only `emit(event, payload)`; never reach into
  a consumer. A future "video analysis" capability becomes just another consumer/producer of events.
- **Service boundary** — the platform talks to external capabilities (payments today, possibly 1050
  video later) over HTTP + a normalized contract, never shared DB tables. `club_id` is the join key.
- Do **not** build the integration now; just don't foreclose it. No 1050 imports, no shared schema.

## 5. Pre-flight blockers — *run/verify* vs *build*

| Item | Blocks writing code? | Blocks verifying/running? | Owner |
|---|---|---|---|
| New Postgres `DATABASE_URL` | No | **Yes** (Phase 0 DoD = "boots + init idempotent") | Tomo |
| New Clerk app (JWKS/issuer/publishable key) | No | **Yes** (Phase 0 DoD = "JWT resolves principal") | Tomo |
| Klaviyo sender domain auth (`bookings@nextpointtennis.com`) | No | Only at Phase 4 (sends) | Tomo |
| Yoco keys + **current API doc check** | No | Only at Phase 7 (online pay) | Tomo |
| S3 bucket / SES fallback sender | No | Phase 4 (confirmations) | Tomo |
| DNS / SEO cutover | No | Phase 6 (supervised; **never an agent**) | Tomo |

**Implication:** agents can **write** all of Phase 0–1 now. To **verify** the Phase-0 DoD (the gate
before fan-out) we need the new Postgres + Clerk app. Fastest path = provision those two in parallel
with the Foundation build.

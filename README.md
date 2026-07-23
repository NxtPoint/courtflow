# CourtFlow — NextPoint Tennis

**CourtFlow** is a white-label, **multi-tenant tennis club management platform** — courts, coaching and
classes behind one diary, with billing, memberships, packs and a commission engine on top. It is built to
be sold cookie-cutter to other clubs, reusing ~80% of the proven Ten-Fifty5 architecture (Render, Clerk,
Postgres, AWS SES/S3, Klaviyo, provider-agnostic payments).

**NextPoint Tennis is club #1** — migrated off Wix and **live in production at
[nextpointtennis.com](https://nextpointtennis.com)**.

> ### This README is the front door, not the source of truth
> - **`CLAUDE.md`** — current build state, architecture, invariants and gotchas. *Read this first.*
> - **`docs/specs/README.md`** — the as-built spec set, with the dated build history.
>
> Where anything below disagrees with those two, **they win.** This file is deliberately short so it
> can't rot into a second, competing index.

---

## Status

| Track | State |
|---|---|
| **Platform** | ✅ **LIVE in production**, feature-complete for launch. **No current build phase** — what remains is config + backlog. |
| **Verification gates** | `python -m scripts.test_all` → **booking 263 / billing 439 / statement 64** (rollback-only scratch-DB harnesses; there is no pytest suite). |
| **SEO migration** | ✅ Wix→Render cutover executed 2026-07-05 — 48-rule 301 map live, canonical→apex, prod Clerk + Google login. |
| **Measurement** | GA4 + Google Ads live; gclid capture → `core.acquisition`; the Google Ads offline-conversion CSV feed is built (still needs its scheduled upload configured). |
| **Transactional email** | ✅ LIVE via SES (interim: rides the Ten-Fifty5 AWS account). Invoice PDFs attach. |
| **Marketing email** | ⏸ Dark until `KLAVIYO_API_KEY` is set — the event feed already emits, so it lights up on the key alone. |
| **What's left** | **[`docs/specs/OUTSTANDING.md`](docs/specs/OUTSTANDING.md)** — config owed by Tomo, code backlog, owner decisions, hardening, and two specced roadmaps. Nothing is launch-blocking. |

---

## Run it locally

Everything needs `DATABASE_URL` pointed at a **local sandbox** — never production.

```bash
python -m db                 # boot/schema runner (idempotent — run TWICE, 2nd must be a no-op)
gunicorn wsgi:app            # the API (has DB).  Or: python -m app
python web_wsgi.py           # the web/portal service (DB-less, PORT=5060)
python -m scripts.seed_nextpoint     # seed club #1
```

Preview the public marketing site (Chrome needs `threaded=True` for parallel assets):

```bash
MARKETING_HOSTS=localhost python -c "import web_app; web_app.app.run(port=5061, threaded=True)"
```

### Gates — run all three before every merge

```bash
python -m py_compile $(git ls-files '*.py')   # PowerShell: python -m py_compile (git ls-files '*.py')
python -m db && python -m db                  # the 2nd run must be a clean no-op
python -m scripts.test_all                    # booking / billing / statement harnesses
```

Each harness can be run standalone while iterating — `python -m scripts.test_booking_scenarios`,
`test_billing_scenarios`, `test_statement_reconciliation`. All three build their own scratch club inside
one transaction and **always roll back**.

Operational one-offs (audits, backfills, imports, live verification) are indexed in
**[`scripts/README.md`](scripts/README.md)**.

---

## Repo map

| Path | What it is |
|---|---|
| `app.py` `wsgi.py` `db.py` | API entrypoint + the idempotent boot/schema runner (`BOOT_MODULES`). |
| `web_app.py` `web_wsgi.py` | The DB-less web service — host-switched marketing site **and** the portal SPAs. |
| `club/` `iam/` `auth/` `core/` | Tenants + identity (Clerk JWKS → club-scoped `Principal`) + the own-CRM tables. |
| `diary/` | **The heart** — resources, availability, bookings, classes, recurrence; a GiST exclusion constraint enforces no-double-booking. |
| `billing/` `yoco_billing/` | Orders/ledger, memberships, packs, commission, refunds, invoicing, promotions + the Yoco adapter. |
| `client360/` | The ONE cross-lane read-model every client view is a view off. |
| `admin/` `coach/` `me/` `services/` `insights/` | Role APIs + the unified service editor + the admin insight read-layer. |
| `marketing_crm/` `offline_conversions/` | Event feed → notifications (in-app + SES), consent, feedback/re-permission, Klaviyo sync, Google Ads offline conversions. |
| `analytics/` | Read-only guarded aggregations + the first-party beacon. |
| `frontend/` | Three role SPAs (client / coach / admin) on **one** shared widget layer. |
| `crons/` `.github/workflows/` | Recurring jobs — **all of them fire from GitHub Actions**, not Render crons. |
| `migration/` | Wix→Render take-on scripts + the live 301 redirect engine. |
| `marketing_digest/` | The keyless cross-brand GA4/Search Console digest (CI-only). |
| `docs/` | Specs — see below. |

---

## Where the docs live

**Current state (read these):**
- **[`CLAUDE.md`](CLAUDE.md)** — build state, architecture, lane ownership, invariants, gotchas.
- **[`docs/specs/`](docs/specs/)** — the as-built spec set. Start at
  [`README.md`](docs/specs/README.md) → `SYSTEM` → `BUSINESS-RULES` → `INVENTORY` → `OUTSTANDING`.
- **[`BUILD_PROMPT.md`](BUILD_PROMPT.md)** — the orchestrator + per-lane agent prompts.

**Frozen design docs (`docs/00`→`11`)** — the original pre-build spec. Still useful for *intent*, but
where they differ from `docs/specs/`, the specs reflect reality.

| Doc | Covers |
|---|---|
| `00-overview-and-vision.md` | Vision, personas, MVP vs later. |
| `01-architecture-and-reuse.md` | Target architecture, services, hosting, the Ten-Fifty5 reuse map. |
| `02-data-model-multitenant.md` | The multi-tenant schema + tenancy isolation. |
| `03-diary-booking-engine.md` | The unified diary: availability, booking, conflicts, recurrence, waitlists. |
| `04-auth-and-roles.md` | Clerk reuse, multi-tenant identity, roles & permissions. |
| `05-payments-abstraction.md` | Provider-agnostic gateway, `apply_payment_event`, settlement modes. |
| `06-crm-and-klaviyo.md` | Own-CRM (`core.*`), the event contract, lifecycle email, consent. |
| `07-marketing-site-and-seo-migration.md` | Public site + the SEO-preserving Wix→Render migration. |
| `08-admin-and-club-onboarding.md` | Club admin console + cookie-cutter new-club onboarding. |
| `09-build-plan-and-agents.md` | The phased build + workstream→agent lanes. |
| `10-reuse-from-1050-port-map.md` | File-by-file port map: Ten-Fifty5 source → new module. |
| `11-build-readiness-and-decisions.md` | Locked decisions + the validated reuse map. |

> **`marketing/` is local-only ad-ops notes** — *not* platform code, and **gitignored** so it can't be
> committed by accident. Don't confuse it with `frontend/marketing/` (the public site) or `marketing_crm/`
> (the CRM lane).

---

## Decisions locked (planning session, 2026-06)

| # | Decision | Choice |
|---|---|---|
| 1 | Infra boundary | New repo + new Postgres DB, reusing the existing Render / Clerk / AWS / Klaviyo accounts. |
| 2 | Multi-tenancy | Multi-tenant from day one — `club_id` on every domain row. NextPoint = club #1. |
| 3 | Booking → payment | Multiple settlement modes: pay online, pay at court, pay end-of-month. The diary launches without mandatory online pay. |
| 4 | Payment gateway | Provider-agnostic behind a registry; **Yoco** adapter first. |
| 5 | CRM | We are our own CRM (`core.*`); Klaviyo is the marketing/lifecycle engine on top. |
| 6 | Email | ⚠️ **As-built differs from the original decision.** The plan was Klaviyo for confirmations with SES as fallback; in practice **SES sends all transactional email today** (`marketing_crm/email/ses.py` — one confirm+receipt per purchase) and **Klaviyo is marketing-only and still dark**. |

---

## Build history

Not kept here — it lives in **git history** and the dated log in
**[`docs/specs/README.md`](docs/specs/README.md)**, which records each sprint against the as-built specs.

# CourtFlow / NextPoint — Documentation Index (START HERE)

This folder is the **authoritative current-state documentation** for the platform. A new session
should read this file first, then the four core docs below. The repo root `CLAUDE.md` is the short
operating guide; **this folder is the detail.**

> **Status:** LIVE on Render, deployed end-to-end. Build sessions 2026-06-20 → 06-28. Earlier: the public
> site + the **three purchasing models** end-to-end (unit/minute bundles, free week, active/dormant/retired
> lifecycle, membership tiers + access windows). **2026-06-25/26:** a **redesigned client journey**
> (action-first cockpit, full-screen calendar booking, consolidated `/plan`), the **lesson approval
> lifecycle** (request/propose/accept/decline + per-coach review; on-behalf auto-confirms), **coach & owner
> consoles** (onboarding, approval queue, clients-360, statements with discount/write-off, per-service
> commission, financial cockpits — both on the shared `crm_ui.js`), and a booking **`.ics` calendar**.
> **2026-06-28:** the **UNIFIED CLIENT STATEMENT** ([UNIFIED-STATEMENT.md](UNIFIED-STATEMENT.md)) — one
> reconciled "what you owe" from unpaid orders, grouped + tick-to-part-settle, admin void/write-off, coach
> arrears held in lockstep (commission accrues exactly once); **service-specific & per-membership-tier
> payment options** + one payment rule (choose / immediate / online); **memberships & packs buy offline**;
> **off-peak coverage priced per slot**; **Operate (Admin) vs Configure (Settings)** split; unified
> **Active/Deactivated/Terminated lifecycle** + real coach/court deletes.
> **2026-07-02:** **business-first coach & owner consoles** (coach: Dashboard/Schedule-week-timeline/Clients/
> Money/Setup; owner: Dashboard-"Today at the club"/Diary/People/Money/Insights) behind **role-focused nav**
> (staff land on their own console, not the client screen) + a today-glimpse + **"Book for myself"**; and
> **multi-tenant transactional SES email is CODE-COMPLETE** (one verified domain, per-club From-name +
> Reply-To, `.ics` attachment — dark until AWS is keyed; see [SES-SETUP.md](SES-SETUP.md)).
> Remaining: **OUTSTANDING.md**.

## Read in this order
1. **[SYSTEM.md](SYSTEM.md)** — architecture: services, the 5 Postgres schemas, the code lanes,
   request/auth flow, integrations, deploy. *"How it's wired."*
2. **[BUSINESS-RULES.md](BUSINESS-RULES.md)** — every business rule + capability we built: booking,
   the three purchasing models (PAYG / membership / tokens), payments & refunds, the commission /
   coaching-settlement engine, self-service per role, notifications. *"What it does and why."*
3. **[INVENTORY.md](INVENTORY.md)** — the exhaustive list: every code lane, **every API endpoint**,
   **every DB table**, every frontend page/JS module, env vars. *"What exists."*
4. **[OUTSTANDING.md](OUTSTANDING.md)** — everything still to do: build items, config (needs Tomo),
   and consciously-deferred pieces. *"What's left."*
5. **[TESTING.md](TESTING.md)** — the **end-to-end test plan** (3 profiles, role-by-role, with expected
   results). *"How to verify it all."* **← CURRENT PHASE: Tomo is running E2E testing.**
6. **[FEATURES.md](FEATURES.md)** — the **white-label feature & function catalogue** (plain-language,
   grouped by area, with automated-test coverage flags). *"What can it do."* The scratch-DB scenario
   harnesses (`python -m scripts.test_all`) back the ✅-flagged items.
7. **[PERMISSIONS.md](PERMISSIONS.md)** — the **roles × screens × actions review map**: who sees/does
   what per console, the 3-layer enforcement model, a straw-man staff-role split to react to, and the
   built-but-unsurfaced endpoints. *"Who can do what."* (Review artifact — mark it up, then we build.)
8. **[UNIFIED-STATEMENT.md](UNIFIED-STATEMENT.md)** — the unified client-statement design + reconciliation
   plan (BUILT 2026-06-28): one debt = one `billing.order`, settled once; no double-count; the
   reconciliation harness that gates it. *"How the money reconciles."*
9. **[FRONTEND-REDESIGN.md](FRONTEND-REDESIGN.md)** — the front-end simplification log (one-page-per-role,
   Operate-vs-Configure, the statement card, lifecycle UI). *"How the UI got simpler."*
10. **[SES-SETUP.md](SES-SETUP.md)** — the config guide to turn transactional email on: verify `courtflow.app`
    in SES af-south-1, exit the sandbox, IAM keys, `SES_SENDER`. Multi-tenant (one domain, per-club identity),
    `.ics`-attaching engine is code-complete; this is the AWS-side setup. *"How to switch email on."*

## The build-era spec docs (design intent, still useful)
- [00-roadmap.md](00-roadmap.md) — the phased self-service/CRM roadmap (most phases now built).
- [01-commission-and-coaching-decisions.md](01-commission-and-coaching-decisions.md) — the owner's
  LOCKED commercial decisions (ex-VAT, rent +/or %, PAYG/bundle/arrears, commission-on-collection,
  nothing-hardcoded). **Authoritative for the commission engine.**
- [02-token-bundle-engine.md](02-token-bundle-engine.md) — the generic token/bundle design.
- `client-self-service-spec.md`, `coach-self-service-spec.md`, `owner-self-service-spec.md`,
  `crm-and-foundations-spec.md` — the deep role specs (built from these).

## The original pre-build design docs
`docs/00`→`docs/12` (one level up) are the original architecture/decision docs written before the
build. They remain the source of the big-picture design and the Ten-Fifty5 (1050) reuse map
(`docs/10`, `docs/11`). Where they and the `specs/` docs differ, **`specs/` reflects as-built reality.**

## Ground rules that still hold (see SYSTEM.md for detail)
- **Multi-tenant:** every domain row carries `club_id`; every query is club-scoped.
- **No migration framework:** idempotent boot DDL (`CREATE/ALTER ... IF NOT EXISTS`), `python -m db`
  twice = no-op. Verify schema changes with the boot-twice gate.
- **Nothing hardcoded:** prices, durations, plans, commission, bundles are owner-configured *data* —
  build configurable capabilities (white-label).
- **Reuse, don't import** from the 1050 repo at `C:\dev\webhook-server` (READ-ONLY reference).

# CourtFlow / NextPoint — Documentation Index (START HERE)

This folder is the **authoritative current-state documentation** for the platform. A new session
should read this file first, then the four core docs below. The repo root `CLAUDE.md` is the short
operating guide; **this folder is the detail.**

> **Status:** LIVE on Render, deployed end-to-end. ~90% of the product is built. Today is the
> documentation wrap-up of a very large build session (2026-06-20 → 06-21).

## Read in this order
1. **[SYSTEM.md](SYSTEM.md)** — architecture: services, the 6 Postgres schemas, the code lanes,
   request/auth flow, integrations, deploy. *"How it's wired."*
2. **[BUSINESS-RULES.md](BUSINESS-RULES.md)** — every business rule + capability we built: booking,
   the three purchasing models (PAYG / membership / tokens), payments & refunds, the commission /
   coaching-settlement engine, self-service per role, notifications. *"What it does and why."*
3. **[INVENTORY.md](INVENTORY.md)** — the exhaustive list: every code lane, **every API endpoint**,
   **every DB table**, every frontend page/JS module, env vars. *"What exists."*
4. **[OUTSTANDING.md](OUTSTANDING.md)** — everything still to do: build items, config (needs Tomo),
   and consciously-deferred pieces. *"What's left."*

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

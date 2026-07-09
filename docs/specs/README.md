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
> **2026-07-02:** the **FRONT-END REDESIGN — three role SPAs** on one design system, drill-through
> everywhere, one booking "event story" per app (the golden rule). **Client** = one-page, no bottom nav
> (`app.html`+`client.js`); **Coach** = bottom-nav SPA (`coach_app.html`+`coach_app.js`) with a weekly
> calendar, a client record that drills **by service → sessions (real paid/owed/written-off/discounted
> state) → the event story**, **Total billed**, money actions in the event story, and **classes wired
> into Setup** (create/schedule/roster — now bookable end-to-end); **.ics add-to-calendar** fixed on both.
> **2026-07-03/04:** the **OWNER/ADMIN console redesign is COMPLETE + LIVE** — `/admin` now serves the
> responsive drill-through SPA (Home command-center · People → unified person-360 · Money as Setup-style
> sections incl. **Sales by day** + **Bookings by day** · Diary on the shared Calendar widget · **Overview**
> [a first-class nav tab as of 2026-07-05: month pager + ECharts sub-tabs Traffic/Bookings/Revenue/Members/
> NPS/Courts, incl. public-vs-member + logged-in traffic split] · Setup); the classic tab console is preserved at `/admin-classic`
> ([ADMIN-REDESIGN.md](ADMIN-REDESIGN.md)). A **Phase-2 insights lane** landed its flagship
> (`insights/`; [ADMIN-PHASE2.md](ADMIN-PHASE2.md)). The whole front end was then **standardised onto ONE
> widget per capability — the enshrined GOLDEN RULE** ([FRONTEND-STANDARDISATION.md](FRONTEND-STANDARDISATION.md)):
> `Widgets.TransactionDetail` / `Calendar` / `Setup` + `ServiceList` shared across all three apps, role
> differences expressed as configuration, ~1,700 lines lighter. And **transactional SES email is now LIVE
> end-to-end** (interim via the Ten-Fifty5 AWS account; the long-term CourtFlow setup is
> [SES-SETUP.md](SES-SETUP.md)). Gated green: **booking 43 / billing 142 / statement 35**.
> **2026-07-05 — CUTOVER DAY:** both web services were **recreated in Render's Frankfurt region**, now
> **co-located with the Postgres DB** (they had mistakenly been in Oregon — every query crossed the Atlantic;
> plan bumped Free → **Starter**, `DATABASE_URL` on the internal same-region URL). The admin **Diary Day view
> is now the resource-timeline grid** (courts + coaches as columns, drilling to the shared event story;
> Week/Month stay agenda; drag-timeline editing still at `/admin-classic`), the **owner can create a lesson
> for a chosen coach** (`POST /api/services`), and a batch of cutover-day E2E fixes landed (single-club
> principal resolution, coach-invite status flip, calendar full-day bounds, billing-category drill, parallel
> startup + preconnect). Payments confirmed working end-to-end on the Frankfurt stack. Gates unchanged.
>
> **2026-07-05 — 🚀 GO-LIVE (production):** the platform is **live on `https://nextpointtennis.com`** (apex
> canonical, `www`→apex 301, HTTPS; DNS flipped at Wix, `api.` untouched). Prod Clerk + **Google login** (Clerk
> custom Google OAuth); **GA4** `G-EKQP47P8M9` + **Google Ads** `AW-17077631191` (purchase) on courtflow-web;
> `TRANSACTIONAL_BCC` copies the club on transactional email; 48-rule 301 map + GSC live; canonical repointed
> www→apex. Shipped alongside: admin **walk-up client** create + **issue membership/pack offline**
> (`POST /api/admin/clients`, `POST /api/admin/members/<id>/issue`), client **monthly Activity** view
> (`GET /api/me/activity`), **classes can reserve a court**, coach **book-a-client** picks service+duration+
> payment (`GET /api/coach/members/search`), and **5 new email templates** (cancel/reschedule/refund/class-
> cancel/reminder) + club BCC. Post-launch cleanup: retired the unreachable `my.js`/`account.js`/`book.html`
> shells + a duplicate `/api/me/activity` route. Remaining: **OUTSTANDING.md**.
>
> **2026-07-08 — BOOKING-FLOW AUDIT SPRINT (live):** a multi-agent end-to-end audit of the whole booking
> flow, then fixes. **Coach/product-scoped pricing is now STRICT TWO-TIER** (a service uses the coach's own
> active product if they have one, else the shared NULL-coach product — never merged; `diary.pricing.
> _coach_has_own_product` gates pricing AND order creation), which fixed blank/R0 coach rate cards and a
> class client being billed another coach's cheaper rate. **Per-service selection** (`services_for` →
> `GET /api/diary/services`) offers each named service's own durations/modes. **The ONE booking widget now
> does on-behalf across all three roles** (client · coach book-for-client · admin book-for-client picking the
> coach — config, not a fork), auto-drawing a matching **pack wallet** and skipping Yoco. **Rich transactional
> email** (full detail, **SAST** times, green banner kept, coach **BCC** on lessons). **Coach diary shows all
> club bookings** with a self-filter (default just-me) + a **"clients with packages"** view. Booking-integrity
> fixes: a lesson's held court is never billed and confirms with the lesson, reschedule re-prices + reassigns
> the court, a paid booking can't be extended, a covered booking can't move to an uncovered time free, trial/
> members never book a coach free, late-cancel fee billed, paid-cancel prompts a refund. Gated green:
> **booking 43 / billing 176 / statement 40**. Edge backlog + the subscriptions/plans review plan are in
> **OUTSTANDING.md §B**.
>
> **2026-07-09 — CLIENT 360 CONSOLIDATION (live):** a new **`client360/`** lane composes the existing lane
> readers into ONE scoped client read model (`get_client_360` — identity + membership(+status) + packages
> {active,history} + statement/owed + payments + bookings + dependents + refunds + coaching + activity + a
> per-scope `can{}` map); it is a **superset** of the old admin person-360, so `admin.get_person` now
> **delegates** to it. New endpoints: `GET /api/coach/clients/<id>/360`, `GET /api/me/360`, plus admin
> holdings actions `POST /api/admin/orders/<id>/discount` (reprice ANY open order — original preserved,
> `coach_arrears` in lockstep, paid→refund path) and `POST /api/admin/clients/<id>/wallets/<wid>/{adjust,expire}`
> (manual pack top-up/subtract clamped ≥0 / soft-expire, audited via a new `token_ledger` kind). The People
> roster gains subscription/holdings slicers (membership tier · on-trial · has-pack · no-membership). One new
> **`Widgets.ClientRecord`** renders the client record across all three apps (role diffs = config) — the three
> hand-built renderers were deleted, **reversing** the FRONTEND-STANDARDISATION §7 "kept split" exception now
> that the data is single-sourced. This closes **OUTSTANDING.md §B** (subscriptions/plans holdings). Gated
> green: **booking 43 / billing 195 / statement 47**.

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
   results). *"How to verify it all."*
6. **[FEATURES.md](FEATURES.md)** — the **white-label feature & function catalogue** (plain-language,
   grouped by area, with automated-test coverage flags). *"What can it do."* The scratch-DB scenario
   harnesses (`python -m scripts.test_all`) back the ✅-flagged items.
7. **[PERMISSIONS.md](PERMISSIONS.md)** — the **roles × screens × actions review map**: who sees/does
   what per console, the 3-layer enforcement model, a straw-man staff-role split to react to, and the
   built-but-unsurfaced endpoints. *"Who can do what."* (Review artifact — mark it up, then we build.)
8. **[UNIFIED-STATEMENT.md](UNIFIED-STATEMENT.md)** — the unified client-statement design + reconciliation
   plan (BUILT 2026-06-28): one debt = one `billing.order`, settled once; no double-count; the
   reconciliation harness that gates it. *"How the money reconciles."*
9. **[FRONTEND-REDESIGN.md](FRONTEND-REDESIGN.md)** — the front-end simplification log + the **three
   role SPAs** (client/coach/admin) drill-through redesign. *"How the UI got simpler."*
10. **[ADMIN-REDESIGN.md](ADMIN-REDESIGN.md)** — the owner/admin console SPA redesign (responsive,
    command-center Home, unified person 360, one admin event story). **COMPLETE + LIVE at `/admin`** (all
    7 steps). *"The owner console (as built)."*
11. **[FRONTEND-STANDARDISATION.md](FRONTEND-STANDARDISATION.md)** — **the enshrined GOLDEN RULE:** one
    widget per capability, role differences = configuration. The widget contract, the shared widget set
    (`TransactionDetail`/`Calendar`/`Setup`/`ServiceList`/**`ClientRecord`**), guardrails, and what was
    deliberately not merged (§7 — the person/client-record split was **reversed 2026-07-09** once
    `client360` single-sourced the data). *"How the front end is architected — read before any new UI work."*
12. **[ADMIN-PHASE2.md](ADMIN-PHASE2.md)** — the "world-class admin portal" backlog: 5 reusable
    primitives + ~40 features, no table sprawl. **Flagship shipped** (P1 insights lane: court-utilisation
    + sales-by-day); the rest awaits prioritisation. *"Where the admin portal goes next."*
13. **[SES-SETUP.md](SES-SETUP.md)** — email is now **LIVE** (interim via the Ten-Fifty5 AWS account,
    `SES_AWS_*` creds + `eu-north-1` + `SES_SENDER=noreply@ten-fifty5.com`). This doc is the long-term
    proper CourtFlow setup (verify `courtflow.app` / `nextpointtennis.com` DKIM once the CourtFlow AWS
    account is back). *"Email: live now, the clean setup later."*

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

# OUTSTANDING — what's left to do

The single source of truth for remaining work. Grouped by type. (Everything NOT here is built & live —
see [BUSINESS-RULES.md](BUSINESS-RULES.md) / [INVENTORY.md](INVENTORY.md).)

> **▶ NO CURRENT BUILD PHASE — the platform is feature-complete for launch; what remains is config +
> the backlog below.** The **OWNER/ADMIN console redesign is COMPLETE + LIVE 2026-07-03/04** (all 7
> steps): `/admin` now serves the responsive drill-through SPA (`admin_app.html`+`admin_app.js`) — Home
> command-center + `GET /api/admin/home` · People → unified **person 360** (`GET /api/admin/people/<id>`)
> · the ONE admin **event story** (`GET /api/admin/bookings/<id>`, god-view) · Money as Setup-style
> sections (Sales by day · Revenue · Coach settlement · Approvals · Payments · Activity) · Diary on the
> shared **Calendar widget** (Day view = resource-timeline grid, Week/Month agenda; the drag-timeline
> **editing** — walk-in/block-time/desk-pay — stays at `/admin-classic`; see §B "Diary timeline port")
> · Setup (`Widgets.Setup`) · Insights (court-utilisation heatmap + Business Overview). The whole front
> end was then **standardised onto ONE widget per capability** — the enshrined golden rule in
> **[FRONTEND-STANDARDISATION.md](FRONTEND-STANDARDISATION.md)**. Design record: **[ADMIN-REDESIGN.md](ADMIN-REDESIGN.md)**.

> **Recently shipped (2026-07-02 — NOT outstanding): the FRONT-END REDESIGN — three role SPAs.** The
> old tab-based consoles are replaced by mobile-first (admin: responsive) **drill-through SPAs** on one
> design system, with the **golden rule** of exactly one booking "event story" per app reused everywhere.
> **Client** = one-page, no bottom nav (`app.html`+`client.js`), billing-by-category + booking-story drill.
> **Coach** = bottom-nav SPA (`coach_app.html`+`coach_app.js`): **weekly calendar**, client record that
> drills **BY SERVICE** → sessions (each showing its REAL state paid/owed/**written-off**/**discounted**)
> → the event story, **Total billed** on the cockpit + record, money actions (collect/discount/write-off)
> living in the event story, and **classes** (create/schedule/roster) wired into Setup — classes now work
> end-to-end and are bookable. **Add-to-calendar (.ics)** download fixed on both apps (authed fetch).
> New backend: `GET /api/me/bookings/<id>` + `/api/me/billing/summary`, `GET /api/coach/bookings/<id>`,
> `commission.client_service_breakdown` + `_coach_billed`. Gated green (**booking 43 / billing 142 /
> statement 35**). `cancel_booking` now voids the linked unpaid order (no more phantom-owed courts).

> **Recently shipped (2026-07-02 — NOT outstanding):** **role-focused nav** (member→Home·Account,
> coach→Coach·Account, owner→Admin·Settings; staff land on their own console, never the client screen);
> the **business-first coach console** (Dashboard cockpit + "needs your attention" · Schedule **week
> timeline** · Clients-360 · Money settlement · Setup) and **owner console** (Dashboard **"Today at the
> club"** + money KPIs + growth/NPS + quick actions · Diary · People · Money · Insights); a **today-glimpse**
> on both dashboards + **"Book for myself"** (coach & owner → /book/court). Plus **transactional SES email
> is now LIVE** (multi-tenant: one verified domain, per-club From-name + Reply-To, HTML+text) — see §A
> (interim via the Ten-Fifty5 AWS account; the `.ics` attachment stays off until the key gains `ses:SendRawEmail`).
>
> **Recently shipped (2026-06-28 — NOT outstanding):** the **unified client statement**
> (`billing/statement.py` single source of truth = unpaid `billing.order` rows; grouped tick-to-pay
> client UI + part-settle; admin void/write-off in the People 360; coach_arrears/account_ledger kept in
> lockstep, no double-count — see `docs/specs/UNIFIED-STATEMENT.md`); **service-specific + per-membership-
> tier payment options** (`billing.price.payment_modes`) + the **one payment rule** (`Pay.purchase`:
> one mode → checkout immediately, many → client chooses); **memberships & packs buy offline**
> (at-court/monthly = owed order, activate immediately); the **off-peak per-slot membership pricing fix**
> (peak slots no longer show R0); **self-cancel membership** (`POST /api/me/membership/cancel`); the
> **unified lifecycle** (Active / Deactivated / Terminated across services/memberships/coaches) with
> **real coach & court deletes**; the **Admin-vs-Settings split** (Operate vs Configure; Resources tab
> retired; Settings on the nav); the **People category slicer**; and **stopped seeding demo coaches**.
> Gated green (`python -m scripts.test_all` → booking 43 / billing 142 / statement 35).
>
> **Recently shipped (2026-06-25/26 — NOT outstanding):** the redesigned client journey (action-first
> cockpit + full-screen calendar booking + consolidated `/plan`), the **lesson approval lifecycle**
> (request/propose/accept/decline + per-coach review; on-behalf auto-confirms; client-side accept/decline/
> withdraw), the **coach & owner consoles** (onboarding, approval queue, clients-360, statements with
> discount/write-off, **per-service commission editor**, financial cockpits — on the shared `crm_ui.js`),
> and the booking **`.ics` calendar** (in-app "Add to calendar"). Verified on a scratch DB.

## A. Config — needs Tomo (not code; flips features from dark → live)
- [x] **SES transactional email — LIVE** (2026-07). Running on an **interim** setup via the Ten-Fifty5 AWS
      account (`eu-north-1`, `SES_SENDER=noreply@ten-fifty5.com`, `SES_AWS_*` creds) — invite + booking-
      confirmation + statement emails send now, per-club From-name + Reply-To. **Two follow-ups remain:**
      (a) the `.ics` attachment is **OFF** (`EMAIL_ICS_ENABLED=0`) because the interim IAM key lacks
      `ses:SendRawEmail` — flip to `1` once the key gains it; (b) move to the **proper CourtFlow-domain**
      setup (verify `courtflow.app`/`nextpointtennis.com` DKIM in the CourtFlow AWS account) — full guide:
      **[SES-SETUP.md](SES-SETUP.md)**.
- [ ] **`KLAVIYO_API_KEY`** → CRM lifecycle/marketing flows go live (event feed already emits).
- [ ] **`S3_BUCKET` + AWS keys** → coach **photo uploads** (until then coaches paste a photo URL).
- [ ] **DNS / SEO cutover** for `nextpointtennis.com` (supervised — never an agent; see `docs/07`,
      `docs/11 §5`). Give the platform its own API host (`api.courtflow.app`) — `api.nextpointtennis.com`
      is the live 1050 service, do not break it.
- [ ] Confirm **Yoco fee accounting** assumption in practice (fees = owner's account, recovered via
      commission — currently not deducted from coach splits).

## B. Build items — remaining functionality
- [ ] **Commission engine tail (Phase D deferrals):**
  - [x] **Refund clawback** — a refund now reverses the coach's accrued commission proportionally
        (arrears kept in lockstep); gated by `sc_refund_clawback` in the billing harness. **DONE.**
  - [ ] **Coach payout objects** — `coach_payout` records (owner↔coach settlement). Today the cockpit
        *reports* who owes what; settlement is offline.
  - [ ] **Rent auto-accrual** — `accrue_rent_for_club` exists + is idempotent; it runs on-read. A
        scheduled monthly accrual would be cleaner (needs a scheduler — see crons below).
- [ ] **Bundle/arrears edges:** bundle **expiry** policy for unused minutes/credits (refund/transfer?); a
      "too-late cancellation forfeits the credit" option (today cancel always credits back the exact
      minutes). *(Paying a statement online is now DONE — `POST /api/me/statement/pay` → settlement order
      → Yoco; 2026-06-28.)*
- [ ] **Drop coach_arrears / account_ledger as internal tables (OPTIONAL cosmetic cleanup).** The unified
      statement (`billing/statement.py`) made `billing.order` the single source of truth; `coach_arrears`
      and `account_ledger` are now kept only in **lockstep** (no double-count). Fully removing them ("option
      B" in `docs/specs/UNIFIED-STATEMENT.md`) is a pure internal cleanup — **not blocking**.
- [ ] **Membership upgrades / downgrades** — a member changing tier mid-term (proration, when it takes
      effect, credit/refund). Backlog — needs a proper spec before building.
- [ ] **Platform / super-admin cockpit** — cross-club view (all clubs' revenue/health) for
      `platform_admin`. Low priority while there's one club; the `scope_clause` design supports it.
- [x] **Owner per-person 360 endpoint** — `GET /api/admin/people/<id>` (unified member+coach 360:
      identity + roles + membership grant/revoke + owed + payments + bookings; if coach, settlement).
      **BUILT + LIVE** in the admin SPA (`admin/repositories.get_person`); gated by `sc_person_360`.
- [ ] **Reminders** — booking reminders (the `/api/cron/reminders` handler exists but cron services are
      off). Needs a scheduler: re-enable a Render cron, or an external pinger, or a lazy "due reminders"
      sweep. Same blocker for scheduled rent accrual + the reconcile/membership-refill sweeps.
- [~] **Diary timeline port (PARTIALLY DONE)** — the resource-timeline **grid VIEW** now ships in the new
      admin Diary **Day view** (courts + coaches as columns, config-driven via `cfg.grid`; blocks drill to
      the shared event story). Still to port: the drag-timeline **editing actions** — click-to-create /
      **walk-in** / **block time** / **desk-pay** — which remain only in the classic diary at `/admin-classic`.
- [ ] **Orphaned `awaiting_payment` order cleanup (safeguard)** — when an online booking's `held` slot is
      released by lazy-expiry, its linked `awaiting_payment` order is **not** auto-voided, leaving an
      orphaned owed order. Candidate: void the linked unpaid order on hold-expiry (mirror `cancel_booking`),
      or a periodic sweep.
- [ ] **Reschedule UX polish** — `PATCH /api/diary/bookings/<id>` exists; ensure member/admin
      reschedule flows are smooth + policy-guarded.
- [ ] **My Bookings** — confirm the client SPA (`client.js`) cancel path surfaces token credit-back /
      refund clearly (the standalone `/my.html` now 302-redirects into the SPA).
- [ ] **Self-serve coach/admin role transitions** — e.g. a dependent **aging out at 18** into their own
      login (foundations spec open question).

### Public marketing site — polish follow-ups (rebuilt 2026-06-21; `frontend/marketing/`, spec in `docs/public-site/`)
- [ ] **Lighthouse / LCP verification** on a real throttled-mobile profile (target ≥90 perf, LCP < 2.5s).
      The hero is preloaded + `fetchpriority="high"` + `srcset` and everything else lazy, but it was never
      measured on-device (no headless Chrome in the build env).
- [ ] **`coach-ross.webp` is low-res** (200×200 source) — looks soft on the founder card; swap if a better
      original exists. Coach photos come from the owner's `marketing material/coaches/` folder only.
- [ ] **Homepage "cockpit" showcase uses a faux CSS device mock** (the real portal is behind auth). Swap a
      real `/portal` screenshot (`/img/portal-cockpit.webp`) at go-live.
- [ ] **Contact form delivery** — SES is now live (§A), so enquiries can email; confirm the web-service
      form is wired to the live sender (it also logs to Render logs as a never-lost fallback).
- [ ] Two homepage feature images are polished **Unsplash stock** with `onerror` fallbacks to real club
      photos — swap for real shots when available.

## C. Analytics — BUILT ✅ (follow-ups only)
- [x] **Business Overview dashboard** (`analytics/`, `/overview.html`): website visits / unique / new-vs-
      returning, traffic sources, top pages, by-country, customers, bookings, revenue, settlement mix, NPS —
      platform-admin with a club filter. First-party page-view beacon (`analytics.js` → `/api/track/page`,
      geo via Cloudflare `CF-IPCountry`).
- [x] **Embedded in the admin console** as the "Overview" tab (+ standalone `/overview.html`).
- [x] **Per-business by design** — the cross-business "Ten-Fifty5 bridge" was **deprecated 2026-06-21**
      (removed); each app shows its own overview. Ten-Fifty5 uses its own `/backoffice` cockpit.
- [ ] Follow-up: per-club web-traffic attribution (set `window.__CLUB_ID__` in the beacon).

## D. Hardening / pre-launch (later phases, from the original docs)
- [ ] **RLS** (row-level security) on domain tables — Phase 8; today multi-tenant is a query discipline.
- [ ] An automated **test runner** (there's no pytest suite; gates are `py_compile` + boot-twice +
      per-build scratch-DB scripts). Consider formalising the integration scripts.
- [ ] **VAT/tax** registration + invoice formatting (commission base is treated ex-VAT today).
- [ ] **Consent/PII review** for any new email/notification payloads (no minor PII in marketing sends).
- [ ] Revisit the **four `render.yaml` crons** (capacity-sweep / reminders / monthly-invoice /
      membership-refill) if/when off the Free plan — handlers exist; only the schedulers are disabled.

## How to pick up (next session)
1. Read [README.md](README.md) → SYSTEM → BUSINESS-RULES → INVENTORY.
2. Pick an item above. The deep design for most lives in the role specs + `01`/`02` decision docs.
3. Build in a worktree, verify (`py_compile`, `node --check`, `python -m db` twice), merge to `master`,
   confirm the Render deploy. Keep every new table `club_id`-scoped + idempotent.

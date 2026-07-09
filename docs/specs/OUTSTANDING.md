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

> **Recently shipped (2026-07-09 — NOT outstanding): the CLIENT 360 CONSOLIDATION.** A new `client360/`
> lane composes the existing lane readers into ONE client read model (`get_client_360`, scoped
> admin/coach/client) — identity + membership(+status) + packages{active,history} + statement/owed +
> payments + bookings + dependents + refunds + coaching + activity + a per-scope `can{}` map; it is a
> superset of the old admin person-360, so `admin.get_person` now **delegates** to it. New endpoints:
> `GET /api/coach/clients/<id>/360`, `GET /api/me/360`, plus admin holdings actions `POST /api/admin/orders/
> <id>/discount` (reprice any OPEN order, original preserved, coach_arrears in lockstep) and
> `POST /api/admin/clients/<id>/wallets/<wid>/{adjust,expire}` (manual pack top-up/subtract clamped ≥0 /
> soft-expire, audited via a new `token_ledger` kind). The People roster gains subscription/holdings slicers
> (membership tier · on-trial · has-pack · no-membership). Frontend: ONE new `Widgets.ClientRecord` renders
> the client record across all three apps (role diffs = config) — the three hand-built person/client
> renderers were deleted. Gated green: **booking 43 / billing 195 / statement 47**.
>
> **Recently shipped (2026-07-09 — NOT outstanding): COURT SERVICES + PER-SERVICE PACKS.** **(1)** Courts can
> belong to distinct court **services** (Hardcourt Hire vs Clay Hire) — each a `billing.product(kind='court_
> booking')` with its own price + allocated courts (`diary.resource.product_id`; resolution own→club-default→
> unscoped via `diary.pricing.court_service_for_resource`); pricing/availability/`create_booking` are
> court-service-aware (fixed the "cheapest across court products" leak), a wrong-service court is rejected
> (`COURT_NOT_IN_SERVICE`), single-service clubs unchanged. Owner allocates in Setup → Courts & hours
> (`PATCH /api/admin/resources`) + creates a court service via Services "+ New". **(2)** A pack/wallet now
> carries `product_id` = the SPECIFIC service it draws for (owner+kind inherited); `match_wallet` is
> product-aware + backward-compatible (legacy NULL-product still matches by coach+kind). Packs are managed
> ONLY under a service now (the standalone Setup "Session packs" + coach-onboarding "Packs" step + the
> `/api/{admin,coach}/bundle-plans` **write** routes were DELETED; `GET /api/admin/bundle-plans` kept for the
> offline issue-a-pack picker; write via `/api/services/<product_id>/packages`). Legacy packs stay NULL until
> `scripts/backfill_pack_products.py`. Gated green: **booking 61 / billing 239 / statement 47**.
>
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
- [x] **DNS / SEO cutover — DONE 2026-07-05.** Live at `https://nextpointtennis.com` (apex canonical,
      `www`→apex 301, HTTPS). DNS at Wix: apex A→`216.24.57.1`, www CNAME→`courtflow-web.onrender.com`;
      `api.nextpointtennis.com` (1050) untouched. Prod Clerk + Google login live; 48-rule 301 map; GA4
      (`G-EKQP47P8M9`) + Ads (`AW-17077631191`) + GSC live; canonical/OG/JSON-LD repointed www→apex.
      Wix kept warm on its `*.wixsite.com` URL as the rollback (~2-week watch). See `GO-LIVE-CUTOVER.md`.
- [ ] Confirm **Yoco fee accounting** assumption in practice (fees = owner's account, recovered via
      commission — currently not deducted from coach splits).

## B. Build items — remaining functionality
- [x] **BOOKING-FLOW AUDIT SPRINT (2026-07-08) — DONE.** A multi-agent audit of the whole booking flow
      shipped: coach-scoped + per-SERVICE pricing (lessons AND classes — a coach's rate card is used
      exactly, never merged with other coaches'), online-lesson court-orphan fix, crafted-mode R0 bypasses
      closed (`free` / `membership_covered`), online-only enforcement (client self-book), reporting
      reconciliation (coach/owner/client agree), the **ONE booking widget** for client + coach + admin
      on-behalf (per-service picker, lesson **and** class pack auto-draw, skip Yoco), the coach **"clients
      with packages"** view, no-show fee now billed, a paid booking can't be extended (M7), a covered court
      can't move to peak for free (M5), token→coach credited at purchase, refund prompt on a paid cancel
      (L1), court auto-reassign on lesson reschedule (L2), and the diary/schedule **coach filter** now shows
      only that coach's day. Gates: **booking 43 / billing 176 / statement 40** (+34 harness assertions).
      **Edge backlog** — unreachable from today's UI or self-healing (low priority):
  - [ ] **L5** — a lesson's auto-held court is orphaned only if billing FAILED at create (order_id NULL) —
        the cancel/void by-order path can't reach a null-order court. Add a fallback link.
  - [ ] **L7** — a *multi-player gated* lesson under-bills on accept (`accept_booking` passes `parties=[]`).
        Not reachable (UI sends ≤ 1 billable player); pass the stored parties to match the create path.
  - [ ] **L8** — a client WITHDRAWING a pending lesson request doesn't push-notify the coach (their queue
        self-updates; decline DOES notify). Add a `lesson_withdrawn` notification template + emit.
  - [ ] **M8** — `_create_order_guarded` bills one line PER member party; a COURT with 2+ member parties
        would charge N×. Not reachable (UI sends ≤ host+guest). Add a court collapse-to-one-line guard.
  - [ ] **M3 tail** — a gated (review) lesson request skips `_settlement_allowed` + booking-window
        (payment_modes IS now enforced in the gate). Move the gate AFTER those checks, or re-run on accept.
  - [ ] **On-behalf class-pack draw** — shipped in the widget; add a backend harness assertion for the
        on-behalf class token draw to lock it in.
- [x] **SUBSCRIPTIONS / PLANS — one place to review who HOLDS what — DONE (CLIENT 360 CONSOLIDATION, 2026-07-09).**
      The plan *catalogue* already lived in **Setup**; the missing piece — a single place to review HOLDINGS —
      shipped as the owner's own lean idea (People filter → client 360, no new tab), built on a new
      `client360/` single-source read model:
  - **Person 360 = the client's full holdings.** ✅ `client360.get_client_360` composes membership
    (+`membership_status`), **active packages/wallets** (sessions left · expiry · coach) AND history, owed
    statement, payments, bookings, dependents, refunds, coaching and activity into ONE payload;
    `admin.repositories.get_person` now **delegates** to it (`scope='admin'`), so one client record = membership
    + packs + owed statement in one view (the wallets gap is closed). Coach + client get the same read model
    scope-filtered (`GET /api/coach/clients/<id>/360`, `GET /api/me/360`).
  - **People slicer = subscription filters.** ✅ `admin.list_people` now returns `on_trial`, `has_active_pack`
    and `membership_tier` (alongside `has_membership`); the People segmented control gains **membership-tier ·
    On-trial · Has-pack · No-membership** slices (empty ones hidden) that drill to the person-360.
  - **PLUS new admin holdings actions** (beyond the original plan): the owner can now **adjust/top-up or
    soft-expire a client's prepaid pack** (`POST /api/admin/clients/<id>/wallets/<wid>/{adjust,expire}`) and
    **discount ANY open order** (`POST /api/admin/orders/<order_id>/discount`) right from the client record —
    see [BUSINESS-RULES.md](BUSINESS-RULES.md) §4/§6. Gates: **booking 43 / billing 195 / statement 47**.
  - **Coach** already has **Clients → "Prepaid packages"** (SHIPPED) = which of my clients hold a pack with me;
    their pack *catalogue* stays in Setup — now edited **under each service** (the service editor's packages
    card; the standalone coach-onboarding "Packs" step was removed 2026-07-09). No new coach surface.
  - **Admin plan catalogue** stays in **Setup** (not duplicated in People); packs are now edited **under a
    service**, not a standalone "Session packs" section (removed 2026-07-09). *Optional later:* an Insights
    "Subscriptions" panel (active memberships by tier + active packs + recurring value) — only if the
    People-filter + 360 isn't enough. Remaining plan edges below (upgrades/downgrades, bundle expiry policy)
    are unchanged.
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
- [ ] **Guest fee (Phase 2)** — charge a court guest a **fixed fee (e.g. R80) collected FROM THE GUEST**,
      not added to the member's account. Guests are currently **non-billable** (a guest party rides on the
      booking but generates no billing line — `_create_order_guarded` skips guest parties). Needs: a guest
      fee price/config + a guest-facing collection path (at-court or a guest payment link), kept off the
      member's statement.
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
- [x] Follow-up **DONE (2026-07-05)**: per-club web-traffic attribution — the DB-less web can't emit the
      club UUID, so `beacon.py` resolves `club_id` server-side from the browsing host (Origin/Referer →
      `iam.resolve_club_by_host`), falling back to `sole_club_id` for a single-club deploy (cached per host).
- [x] **NATIVE ADMIN OVERVIEW TAB (2026-07-05/06)** — Business Insights promoted to a first-class `#/overview`
      nav tab (the `/overview.html` iframe retired): month pager + sub-tabs (Traffic/Bookings/Revenue/Members/
      NPS/Courts), all **daily** graphs via one shared ECharts seam, on the `insights/` lane (`GET /api/insights/
      overview`). Fixed the **NPS bug** (analytics filtered a non-existent `created_at` → silent zeros; now
      `submitted_at`). Added **public-site vs member-area** traffic split (path-based) + a **precise logged-in
      signal** (`analytics.js` sends a non-PII `authed` flag once Clerk resolves; `beacon.py` stores
      `metadata.authed`; logged-in data accrues from 2026-07-06). Added `billing.membership_subscription.
      period_start`/`cancelled_at` for an accurate active-members-per-day curve.

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

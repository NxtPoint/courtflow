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
> NPS/Courts, incl. public-vs-member + logged-in traffic split] · Setup); the classic tab console was retired 2026-07-18 (`/admin-classic` 301→`/admin`)
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
>
> **2026-07-09 — COURT SERVICES + PER-SERVICE PACKS (live):** two shipped features. **(1) Court → service
> allocation** — courts can belong to distinct court **services** (e.g. "Hardcourt Hire" over the hard courts
> vs "Clay Hire" over the clay), each a `billing.product(kind='court_booking')` with its own price + allocated
> courts (new `diary.resource.product_id`; resolution = own product → club default court product → unscoped,
> `diary.pricing.court_service_for_resource`). Pricing/availability/`create_booking` are court-service-aware
> (fixing the old "cheapest across court products" leak); a wrong-service court is rejected
> (`COURT_NOT_IN_SERVICE`); single-service clubs unchanged. Owner allocates courts in Setup → Courts & hours
> (a "Court service" picker, `PATCH /api/admin/resources`) + creates a court service via Services "+ New"; the
> client picks a court service like a lesson service. **(2) Per-service packs + kill the standalone pack
> editors** — a pack (`bundle_plan`) + wallet (`token_wallet`) now carry `product_id` = the SPECIFIC service it
> draws for (a "Private Lesson" pack only draws for Private, a "Clay" pack only for Clay), owner+kind inherited
> from the service; `match_wallet` is product-aware + BACKWARD-COMPATIBLE (legacy NULL-product wallet still
> matches by coach+kind). **Golden-rule consolidation:** packs are created/edited **ONLY under a service** (the
> service editor's packages card); the standalone Setup "Session packs" section + the coach-onboarding "Packs"
> step (+ `AdminUI.bundlePlans`/`CoachUI.packs`, the `AdminAPI`/`CoachAPI` bundle-plan methods, and the
> `POST/PATCH/DELETE /api/{admin,coach}/bundle-plans` routes) were DELETED — `GET /api/admin/bundle-plans` is
> kept for the offline "issue a pack" picker; write goes through `/api/services/<product_id>/packages`. Live
> packs keep working (`product_id` NULL = legacy) until `scripts/backfill_pack_products.py` maps them. Gated
> green: **booking 61 / billing 239 / statement 47**.
> **2026-07-09/11 — classes reserve courts, the CLIENT-360 finishing pass + the transactional-email audit.**
> **Classes** now hold MULTIPLE real courts (GiST-blocking, auto-repick), show under their court column in the
> diary, and are editable; **online class enrolment goes through the Yoco paywall** (was silently confirmed
> unpaid) and an unpaid online seat is **lazily released** like a court hold (`diary.enrolment.held_until` →
> `release_expired_enrolments`). **Client 360** is complete + hardened: each block runs inside a **SAVEPOINT**
> (a failing block never rolls back the caller's transaction — fixed a real latent bug + the `sc_person_360`
> gate), and booking rows now carry the **service + payment status**. **Transactional email** was fully
> audited (all 21 kinds): **ONE confirm+receipt email per purchase** (an online booking's payment email shows
> the rich booking block, retitled "Booking confirmed"; pack/class payment emails suppressed for their own),
> exact membership-tier + pack names, **times on every receipt**, aligned layout, an Outlook-safe HTML shell,
> `/portal` links, coach BCC only on his own lesson/class, and ONE canonical payment-status vocabulary
> (`billing.statement.settlement_status_label`, shared by email + Client 360 so wording never drifts). Gated
> green: **booking 98 / billing 239 / statement 47**.
>
> **2026-07 — LESSON→BILLING→SETTLEMENT TIGHTENING + CLIENT-HOME REDESIGN (live):** booking/billing
> **integrity guards** (no cancel-after-start, desk-payment cashier audit + amount guard, reschedule stays in
> the coach's hours, no completing a future session, no silent R0, a partial refund → `part_refunded`); ONE
> **month-aware Client 360** (`get_client_360(month=)` + per-service breakdown + activity summary — the parallel
> `coach.get_client` reader retired, so the coach client view is month→client→service→transaction on the shared
> `TransactionDetail`); the **club↔coach settlement loop CLOSED** (new `billing.coach_payout` record/settle/list
> nets the append-only `coach_ledger` to ONE net-owed figure, aging view `GET /api/admin/financials/settlement`,
> month-end sweep `POST /api/cron/month-end` fired by `.github/workflows/month-end.yml` — accrues arrears+rent,
> notifies open-balance clients, idempotent per month); and a **client account/home redesign** (month-at-a-glance
> `GET /api/me/activity-summary`, shared `CRMUI.activityBlock`/`spendBlock`/`weekChart` on Home + the 360
> rollup, month navigation, AI-styled analysis panel, no emoji; pack "assign to this service" fix). Gated green:
> **booking 103 / billing 267 / statement 47**.
>
> **2026-07-11 — TEN-FIFTY5 EMBED (members-area SSO, private test):** a logged-in member opens **Ten-Fifty5**
> (AI match analysis / technique — the separate live 1050 product) **inside** the client SPA in an iframe,
> signed in with their OWN NextPoint Clerk token — **no second login**. The two products are separate Clerk
> apps; the seam is a `postMessage` **token relay** + **issuer federation** on Ten-Fifty5's verifier (it now
> trusts both issuers), with **email as the cross-system key** (Ten-Fifty5 auto-provisions by email). NextPoint
> side: `client.js` `#/analysis` route + an auto-fitting iframe + a Home card (**"Coming soon"** for
> non-allowlisted); `auth_client.js` parent relay allowlist + `mode` field; `web_app.py`/`render.yaml` inject
> the `TF5_EMBED_*` config. **Gated to a private test** (`TF5_EMBED_ALLOW_EMAILS`; launch = clear it) + a public
> "Match analysis" marketing CTA → ten-fifty5.com. The 1050 repo was modified (additive/flag-guarded) — the ONE
> exception to "read-only reference." Full write-up: root `CLAUDE.md` → "Ten-Fifty5 embed"; env: `ENV-STATUS.md`.
>
> **2026-07-12 — EQUIPMENT+CONSTRAINTS MERGED + a coach-feedback batch + the CLOSE-OUT.** The
> `feat/equipment-and-constraints` work is **merged + live on prod** (equipment hire · peak PAYG court pricing
> · silent membership entitlement caps `diary/entitlement.py` · configurable trial-as-a-tier —
> [EQUIPMENT-AND-CONSTRAINTS.md](EQUIPMENT-AND-CONSTRAINTS.md)). Then a batch off owner feedback: class fixes
> (payment-modes respected, class packs present, hung-online-seat clears) + **15-minute** booking grid;
> **admin ad-hoc invoice builder** (Money → New invoice: service×qty and/or custom fee − rand discount → one
> owed order + emailed `/portal` paylink); **coach/admin back-capture of a PAST lesson/class** (same on-behalf
> flow, `allow_past`, no calendar hold); **creating a client now requires a name + valid email**; **per-service
> packs are coach+service-scoped at BOTH the draw (checkout) AND the buy-wizard** ("Save on your lessons" no
> longer shows every coach's packs), retired packs stay deleted; the **lesson↔court collapse rule** (a lesson's
> auto-held court is ONE row in every agenda/list view, never two); and class-pick UX polish. **This was also
> the full close-out sweep** — docs reconciled to as-built, `OUTSTANDING.md` rewritten as one clean backlog,
> and a new **[FEATURE-FLAGS.md](FEATURE-FLAGS.md)** capturing every built-but-dark capability + how to switch
> it on.
>
> **2026-07-14 — MONEY-AS-AN-OUTCOME + THE ONE CLIENT-360 + SEMI-PRIVATE LESSONS.** Money is now the
> **outcome of bookings**: an order-status fold (Billed − Discount − Written-off = Invoiced = Paid +
> Outstanding) single-sourced across the coach, admin and client consoles (`CRMUI.statementFold` /
> `moneySummary`). The coach client view is no longer a fork — coach, admin and client all render the ONE
> **`Widgets.ClientRecord`** off the single `client360` composer (coach = a server-scoped filter); the
> person-360 was restructured **headline-first** with a richer admin roster, and creating a client is now
> ONE shared modal (admin + coach, `CRMUI.createClientModal`). Booking is **resource-first**. **Semi-private
> (squad) lessons** shipped — `billing.product.max_clients > 1` puts >1 client on one lesson slot with
> **per-head billing** (one owed order each; a child's head bills the guardian), an add-a-player-later step
> (`CRMUI.addLessonPlayerModal`), and cancel that voids every order. Plus a **payment-gate correctness
> sweep** — every service purchase enforces its OWN `billing.product.payment_modes` (a card-only Clay
> refuses pay-at-court; a pack inherits its service's modes with no at-court fallback; class enrolment
> gated, and a member-self-enrol-for-free exploit closed).
>
> **2026-07-17/18 — MONEY TAB = ONE CLUB-vs-COACH EARNINGS P&L.** The Money tab is now ONE
> **`Widgets.Earnings`** (`frontend/js/widgets/earnings.js`) shared by admin + coach — same widget, config
> only (no fork, like `TransactionDetail`/`ClientRecord`). **Admin "Club earnings"** answers "how much do WE
> make" = direct services (court/membership/pack, 100% club) **+** the commission taken from each coach,
> drilling into a **per-coach P&L** (sales − discount − written-off = net; net = received + owed; commission
> −coach/+club, realised on received + projected on owed — we always collect) → **by client → transaction →
> the shared record**; the **coach** sees their OWN P&L only. **Sales by day** now splits **Online (Yoco)**
> vs **Cash/EFT** (desk-recorded) takings, NET of reversals. The month-end statement sweep now fires on the
> **25th** (the club billing day, `.github/workflows/month-end.yml`), and the **invoice PDF email attachment
> is ON** (`EMAIL_INVOICE_PDF_ENABLED=1`). Two new **read-only** integrity scripts back coach payouts /
> month-end: `scripts/reconcile_coach_commission` (every paid coaching line has its coach split — should read
> CLEAN) and `scripts/diagnose_coach_packs` (where each pack lands in coach earnings, sale-based). **Current
> gate baseline: `python -m scripts.test_all` → booking 180 / billing 311 / statement 47.**

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
4b. **[FEATURE-FLAGS.md](FEATURE-FLAGS.md)** — every capability that is **built but currently dark**
   (env-gated / unwired / commented-out) and exactly how to switch it on. *"What we can turn on."*
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
- [01-commission-and-coaching-decisions.md](01-commission-and-coaching-decisions.md) — the owner's
  LOCKED commercial decisions (ex-VAT, rent +/or %, PAYG/bundle/arrears, commission-on-collection,
  nothing-hardcoded). **Authoritative for the commission engine.**
- [02-token-bundle-engine.md](02-token-bundle-engine.md) — the generic token/bundle design.

### Archived (`_archive/`) — superseded by the as-built docs, kept for provenance
Moved out of the authoritative set in the 2026-07-12 close-out (all were AS-BUILT-superseded): the phased
roadmap `00-roadmap.md` (phases all shipped → see FEATURES/OUTSTANDING), the four deep role specs
`client-/coach-/owner-self-service-spec.md` + `crm-and-foundations-spec.md` (the SPAs + `client360/` composer
replaced them → see ADMIN-REDESIGN/FRONTEND-STANDARDISATION/BUSINESS-RULES), and `12-tenfifty5-bridge.md`
(self-deprecated 2026-06-21; the analytics bridge was removed — unrelated to the live members-area TF5 embed).

## The original pre-build design docs
`docs/00`→`docs/11` (one level up) are the original architecture/decision docs written before the
build. They remain the source of the big-picture design and the Ten-Fifty5 (1050) reuse map
(`docs/10`, `docs/11`). Where they and the `specs/` docs differ, **`specs/` reflects as-built reality.**
(`docs/12-tenfifty5-bridge.md` was the deprecated cross-business analytics bridge — now in `specs/_archive/`.)

## Ground rules that still hold (see SYSTEM.md for detail)
- **Multi-tenant:** every domain row carries `club_id`; every query is club-scoped.
- **No migration framework:** idempotent boot DDL (`CREATE/ALTER ... IF NOT EXISTS`), `python -m db`
  twice = no-op. Verify schema changes with the boot-twice gate.
- **Nothing hardcoded:** prices, durations, plans, commission, bundles are owner-configured *data* —
  build configurable capabilities (white-label).
- **Reuse, don't import** from the 1050 repo at `C:\dev\webhook-server` (READ-ONLY reference — the ONE
  exception is the 2026-07-11 Ten-Fifty5 members-area embed, which required additive auth changes there).

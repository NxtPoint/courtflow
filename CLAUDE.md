# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This repo is the **multi-tenant tennis club management platform** (working name "CourtFlow").
NextPoint Tennis is club #1, migrating off Wix. The platform is **feature-complete for launch and LIVE
in production at `https://nextpointtennis.com`** — what remains is config + backlog, not a build phase.

## Quick orientation (30-second map)
- **Entrypoints:** API = `wsgi:app` (has DB) · web/portal = `web_wsgi:app` (DB-less, host-switched in `web_app.py`).
- **Boot/schema runner:** `python -m db` (idempotent — run **twice**, second run must be a no-op).
- **Source of truth for current state:** start at **`docs/specs/README.md`** (not the `docs/00→11` design docs).
  Where the specs and the original design docs differ, `docs/specs/` reflects as-built reality.
- **The root `README.md` is the front door, not a source of truth** (rewritten 2026-07-22 to defer to this
  file + `docs/specs/`, so it can't rot into a competing index). Keep it short — status, how to run it
  locally, the repo map, the doc map. Build detail belongs here; as-built detail belongs in `docs/specs/`.
- **Iron rule:** every domain row is `club_id`-scoped — **never query domain data without it.** (Phase 8
  adds RLS; until then this is a discipline, not a guardrail.)

## Gates (run before every merge — there is no pytest suite)
1. `python -m py_compile $(git ls-files '*.py')` — the `$(…)` is bash; from PowerShell use
   `python -m py_compile (git ls-files '*.py')`.
2. `python -m db` **twice** — second run must be a clean no-op (idempotency gate).
3. `python -m scripts.test_all` — three rollback-only scratch-DB harnesses. Current green baseline:
   **booking 241 / billing 402 / statement 47**. Each uses its own scratch club and always rolls back.
   Run one lane's harness standalone while iterating (each needs `DATABASE_URL` = a local sandbox):
   `python -m scripts.test_booking_scenarios` (diary) · `python -m scripts.test_billing_scenarios` (billing) ·
   `python -m scripts.test_statement_reconciliation`.
   - `test_booking_scenarios` (241) — double-book, lesson coach∩court, off-peak per-slot pricing, lifecycle,
     **court→service allocation (per-service courts + pricing), classes reserve N courts (held +
     conflict guard + auto-repick) + editable, online class seat held → lazy-expired on abandonment →
     waitlister promoted (paid seat never expired), cancel-after-start refused, unpriced booking refused,
     PEAK court pricing (shown==charged), membership entitlement caps (duration/courts-per-day → PAYG) +
     clay-court exclusion, configurable trial inherits its tier's caps, equipment hire (one order/no
     double-bill + time-based availability, single ball machine can't double-book, cancel voids the add-on),
     coach back-capture of a PAST lesson (staff-only allow_past, resource resolved from coach_user_id),
     SEMI-PRIVATE (squad) lessons — per-head billing (one owed order per client), add-a-player-later,
     a parent's kids bill the guardian, a member can't add a stranger/another family's child, cancel
     voids every head; a card-only SERVICE refuses pay-at-court on the booking; a class enrolment is
     payment-gated (no free seat via membership_covered/free, card-only class refuses pay-at-court),
     **RESCHEDULE CAN MOVE THE COURT (a court booking's own resource; a lesson keeps the coach and its
     held-court row moves), a busy target refuses with COURT_NOT_AVAILABLE, re-picking the SAME court
     doesn't block itself; COACH PREFERRED COURT honoured when free → falls back when busy (never
     blocks a lesson) → an explicit court still wins,
     **CLASS PAYMENT STATE — the roster FLAGS an unpaid seat (`unpaid` + `payment_label`, never a bare
     "Enrolled"), CHECK-IN settles a held online seat into a real owed debt (an `awaiting_payment` order
     is invisible to statement/month-end/invoicing and the sweep only matches 'enrolled', so marking
     attendance used to strand it forever), promotion treats a VOIDED order_id as NOT-billed (a stale id
     used to hand out a FREE class), and a LATE payment RE-INSTATES a swept seat — but never overbooks a
     full class (that logs a refund case), **CLASS PRICE SURVIVES A SERVICE RENAME — a class resolves
     its service through `diary.resource.product_id` (the DURABLE link, set at create_class_type and
     boot-backfilled), never a name join; an orphaned class REFUSES with PRICE_NOT_CONFIGURED rather
     than billing another class's rate, and a retired price variation can never enrol at R0**.
   - `test_billing_scenarios` (402) — settlement modes, commission, tokens, membership (offline + per-tier),
     refunds + clawback, dispute routing, void/lockstep, event stories, two-tier pricing, cancel/resize guards,
     **wallet adjust/expire, general order discount, 7-day-trial grant guard, lesson+class pack coach-linking,
     class↔coach commission parity, per-service packs (product-aware draw), desk-payment amount guard,
     partial-refund state, coach payout nets the ledger, month-end sweep idempotent, pack service-isolation
     (assign + buy-wizard coach/product scoping), admin ad-hoc invoice (service×qty + fee − discount,
     tamper-proof), client activity-summary (counts/minutes/by-service/by-week), a pack respects its
     SERVICE's payment rule (a card-only pack is card-only — no at-court fallback that grants it unpaid),
     PAID PACK NEVER BYPASSED (owed-mode booking auto-draws a matching pack), RECONCILE activates the
     pack/wallet (behavioural GUARD — reconcile must call activate_purchase, not just mark paid),
     **PROMOTIONS** — a redeemed code discounts the ONE order (asserted: no second debt row + coach-arrears
     lockstep + `original_amount_minor` was→now), `validate()` writes nothing, `amount_off` clamps at the
     total, reverse frees the usage slot, every refusal by ERROR CODE (window/scope/min-spend/per-customer +
     global caps/first-time/stacking/paid-order), unique per-recipient codes (single-use, own cap not the
     shared one, revoke, recipient-bound), and the **bonus REPLAY GUARDS** — `bonus_period` 3+1 and
     `bonus_units` "buy 10 get 12" grant exactly once on BOTH the online (at activation) and offline (at
     redemption) paths, and a replayed activation/grant does NOT re-add them**, **`membership_started` fires
     ONCE per real activation (online + offline) carrying the email, never on a replay, and NEVER on the
     7-day trial (a `_EmitRecorder` context manager swaps the stubbed `marketing_crm.tracking.emit` for a
     recorder — late binding is what makes that work)**.
   - `test_statement_reconciliation` (47) — no double-count, pay-all-once, part-settle, reclaim,
     membership-covered R0 never owed, void/write-off, arrears↔orders lockstep, **discount reprices one debt**.

## Deployment (LIVE on Render)
- Repo `NxtPoint/courtflow`; Render auto-deploys `master`. Two web services + a Postgres DB, **all
  co-located in Frankfurt** (region is immutable — recreate from the blueprint to move region; `DATABASE_URL`
  uses the DB's **internal** Frankfurt URL). **`courtflow-api`** (`wsgi:app`, has DB) and **`courtflow-web`**
  (`web_wsgi:app`, no DB — marketing + portal shells + `/login`).
- Production is `https://nextpointtennis.com` (apex canonical, `www` 301→apex). The `courtflow-*.onrender.com`
  hosts remain as fallback. Prod Clerk auth + Google login; `AUTH_ENABLED=1`, `SEED_NEXTPOINT=1` (re-seeds
  club #1 on boot, idempotent). Platform admin = `info@nextpointtennis.com`. GA4 + Google Ads on the web service.
- **Volatile infra values** (exact Clerk subdomains, DNS records, GA/Ads IDs, SES keys) live in
  `docs/specs/ENV-STATUS.md` — keep them there, not here, so they can rot independently of code.

## Architecture (big picture)
The platform re-assembles ~80% of the proven **Ten-Fifty5 (1050)** architecture around one new domain
model: the **diary**. Same shape as 1050, fewer services (no ML/GPU/video).

**Services** (`render.yaml`): `courtflow-api` (booking/diary/billing API, Clerk-JWT auth) + `courtflow-web`
(host-switched marketing site **and** the portal SPAs) + **four cron services** (reminders / capacity-sweep /
monthly-invoice / membership-refill), each running `python -m crons.trigger <job>`. The trigger is a thin
dispatcher — no business logic, no DB — it POSTs once to `/api/cron/<job>` (guarded by `OPS_KEY`); lanes own
the handlers. **All four `render.yaml` crons are commented out and stay that way** — every recurring job
now fires from GitHub Actions instead (below).

**Scheduled jobs — ALL of them are GitHub Actions, never Render crons.** The pattern: a free CI job POSTs to
an `OPS_KEY`-guarded `/api/cron/<job>` inside the keep-warm window (so the API is awake). Each **no-ops
without the `OPS_KEY` repo secret rather than failing the run**, and each handler is **idempotent**, so a
re-run or a doubled schedule is safe. When adding a recurring job, add a workflow here — do NOT uncomment a
`render.yaml` cron.

| Workflow | Cadence | Fires |
|---|---|---|
| `keep-warm.yml` | every 10 min, 07:00–22:00 SAST | pings both services (free tier sleeps after ~15 min) |
| `reminders.yml` | hourly, 07:00–22:00 SAST | `diary.crons.run_reminders` — T-24h/T-2h booking + class reminders, deduped via `diary.reminder_log`, emits `booking_reminder` (LIVE via SES; a no-show reducer) |
| `membership-refill.yml` | daily 07:30 SAST | membership-lapse sweep — `current_period_end` passed → `expired` + emits `membership_lapsed` (drives the Klaviyo E2 win-back) |
| `month-end.yml` | monthly, the **25th** 08:00 SAST | `billing.commission.run_month_end` — coach arrears + rent, then one consolidated statement invoice + pay-link per client owing |
| `reconcile-payments.yml` | hourly, 07:00–22:00 SAST | `yoco_billing.reconcile.reconcile_pending` — recovers payments whose webhook never arrived (Render Free sleeps, so CLAUDE.md calls reconcile "the common path"). The handler shipped at launch but **nothing ever called it** until 2026-07-22 |
| `marketing-digest.yml` | daily 07:00 SAST | cross-brand GA4/GSC organic report + the `core.web_daily` ingest push (see the analytics section) |

**Capacity-sweep needs no job at all** — abandoned holds are released by lazy expiry (see Gotchas).

**One Postgres DB, five schemas** (idempotent boot DDL, no migration framework; `db.py` runs `BOOT_MODULES`):
- `club.*` — tenants/config/branding/location/policies
- `iam.*` — user↔Clerk, membership, coach_profile, dependents, coach_invite
- `diary.*` — resources, availability, booking, class_session, enrolment, waitlist, recurrence (**the heart**);
  a **GiST exclusion constraint** (needs `btree_gist`) enforces no-double-booking
- `billing.*` — product, price, order, payment (carries `recorded_by_user_id` = who took a desk payment),
  membership_subscription, bundle_plan/token_wallet, commission engine (`coach_agreement`/`commission_rule`/
  `commission_split`/`coach_ledger`/`coach_arrears`), **`coach_payout`** (recorded club↔coach settlements —
  nets the ledger) + **`month_end_notice`** (month-end-sweep idempotency)
- `core.*` — account/user/person, usage_event, consent, nps (ported from 1050 `core_db`)

**Decoupling interfaces** (why the lanes stay independent): the **schema** is the contract between diary,
billing, and CRM; `contracts/events.md` is the producer→consumer **event contract** (diary/billing `emit()`
→ CRM/Klaviyo); the **gateway protocol** (`apply_payment_event(provider)` + a `PaymentGateway` registry)
isolates each payment adapter.

## Lanes / module ownership map
Touch only your lane; coordinate on shared interface files (`contracts/events.md`, schema docs,
`render.yaml` env list — Agent A / Foundation is authoritative on those).

| Lane | Owns | Responsibility |
|---|---|---|
| **Foundation** | `app.py`, `wsgi.py`, `db.py`, `render.yaml`, `auth/`, `iam/`, `club/`, `core/`, `scripts/`, `crons/` | Boot/schema runner, Clerk JWKS + club-scoped `Principal`, seed/provision. |
| **Diary** | `diary/` | Court/lesson/class lifecycle, GiST constraint, availability, classes, recurrence, book-on-behalf, `/api/diary/*`. |
| **Billing** | `billing/`, `yoco_billing/` | orders/ledger, `apply_payment_event` (idempotent), membership/bundles/commission/refunds/statement engines, Yoco adapter, `/api/billing/*`. |
| **CRM** | `core/`, `marketing_crm/`, `offline_conversions/` | `emit()`→`core.usage_event`, notifications (in-app inbox + transactional email), Klaviyo sync, consent. **Identity bridge** `core.repositories.persons.link_person_for_user` (iam.user ↔ `core.person.iam_user_id`, adopt-or-create by email; 911 backfilled) — feeds Client-360. **gclid capture** → `core.acquisition` + the **Google Ads offline-conversion feed** (`offline_conversions/`). Two **public, token-guarded** surfaces (no login — the SIGNED token IS the authorization and names the recipient + club, so club scope never comes from the body): `marketing_crm/feedback/` → `GET/POST /api/feedback` (the gated NPS→Google-review funnel; page `frontend/app/feedback.html`, writes `core.nps_response`, routes a happy score to the `g.page` review link and an unhappy one to a private form) and `marketing_crm/repermission/` → `GET/POST /api/subscribe` (re-permission opt-in for the non-consented members; records consent in OUR DB first, THEN fire-and-forget subscribes to Klaviyo). |
| **Client 360** | `client360/` | The ONE cross-lane read-model — `get_client_360(scope, coach_user_id, month)` composes existing lane readers into a single client payload (identity/memberships/packages/statement/payments/bookings/refunds/coaching/activity + `month_events` + the reconciling `statement_fold` + `can{}`; booking rows carry service + pay-status + their own head's amount). Read-only, reuse-first. **`scope='coach'` is a STRICT SERVER-SIDE filter** (the coach fork was retired — coach = a filter, not a fork): it returns ONLY the coach's own events + own coaching fold + own packages + coaching; membership/card-payments/full-statement/dependents/refunds/PII/activity are OMITTED server-side (never sent to a coach's browser). **Each block runs in a SAVEPOINT (`_guard`→`begin_nested`), NEVER a bare `session.rollback()`** — the composer runs inside the caller's `session_scope`, so a full rollback would discard the caller's writes. `admin.get_person` delegates here; coach `/clients/<id>/360` + client `/me/360` call it. **The single source of truth every client view is a view off**, and the money everywhere is the ONE reconciling fold: **Billed − Discount − Written-off = Invoiced = Paid + Outstanding** (`CRMUI.statementFold`/`moneySummary`, coach + admin + client all reconcile). |
| **Admin** | `admin/`, `services/`, `insights/` | Owner write APIs + onboarding, per-service commission editor, financial cockpit, person-360, the insights composer, **general order discount + pack-wallet adjust/expire**. |
| **Coach / Client** | `coach/`, `me/` | Coach self-service (onboarding, approval queue, clients-360, statement, cockpit) + client self-service (profile, dependents, statement, refund requests). |
| **Analytics** | `analytics/` | Read-only guarded aggregations → `/api/analytics/*` (the standalone `/overview.html`); first-party beacon in `beacon.py`. |
| **Frontend** | `frontend/` | Three role SPAs on one widget layer (below). |
| **Marketing/SEO** | `frontend/marketing/`, `frontend/_shared/`, `build_blog.py`, `migration/`, `marketing_digest/` | Host-switched public site, blog, sitemap, Wix→Render migration scripts, cross-brand organic-growth digest (below). |

**Service editing** (`services/`) is the ONE API a service is edited through by BOTH owner and coach —
`/api/services/*` enforces who may change what (owner = everything incl. commission; coach = their OWN
lesson/class name/variations/payment/packages, NEVER commission), delegating to the billing/admin repos.

## Frontend — the enshrined GOLDEN RULE
**ONE widget per capability, across all three role SPAs. A second render of a capability is a bug — extend
the widget's config.** Role differences are **config (data adapter + actions map + fields), never forked
render code.** Full contract: `docs/specs/FRONTEND-STANDARDISATION.md`.
- **ONE design system** in `frontend/app/app.css` (`cf-*` classes) — the single source; do NOT inline
  component styles.
- **Three SPAs:** client (`app.html` + `client.js`, one page, no bottom nav) · coach (`coach_app.html` +
  `coach_app.js`, bottom-nav) · admin/owner (`admin_app.html` + `admin_app.js`, responsive, served at
  `/admin`). The old classic tab console (`admin.html`/`admin.js` + `/admin-classic`) was **DELETED
  2026-07-18** — its last unique surface (**block time / time-off**) was ported into the new Diary
  (a "Block time" action → `POST /api/diary/time-off`); walk-ins + desk-pay already lived in the new console.
- **Shared render layer** `frontend/js/widgets/`: `Widgets.TransactionDetail` = the ONE booking "event story"
  everywhere · `Widgets.ClientRecord` = the ONE client/person-360 record across admin/coach/client (fed by the
  `client360` composer; admin scope adds staff edits — discount/wallet-adjust/void/refund) · `Widgets.Calendar`
  = the admin diary (Day view = resource-timeline grid, config via `cfg.grid`) · `Widgets.Setup` +
  `Widgets.ServiceList`. Common helpers promoted to `window.UI` (`card/backBar/kv/modal/statusChip/…`);
  `crm_ui.js` = `CRMUI.*`. Also reuse `booking.js`, `service_editor.js`, `class_ui.js`.
- **Asset/nav links are ABSOLUTE** (`/app.css`, `/js/…`) so pages work at sub-paths.
- **Two-stylesheet marketing model (respect it):** `frontend/_shared/theme.css` = the cross-lane design-system
  contract (portal + login) — **never add marketing styling there.** All public-site CSS lives in
  `frontend/_shared/marketing.css` (the `mk-*` layer, additive, loads Fraunces per-page). Marketing pages link
  BOTH, use server-injected `<!--#include nav-->`/`<!--#include footer-->` chrome, ABSOLUTE `/img` `/shared`
  paths, and **local optimized WebP only.** Visual source of truth: `docs/public-site/prototype-home-v3.html`.

## Payments, pricing & booking flow (LIVE end-to-end)
**Pricing model — per-duration PAYG + membership-covered courts.** A service carries ONE `billing.price` row
per offered duration. `diary/pricing.py`: `price_for(kind, duration_minutes)`, `durations_for`, `payment_modes_for`,
`services_for`, `has_active_membership`, `membership_covers(starts_at)`. **Coach/product-scoped pricing is
STRICT TWO-TIER** — a service uses the coach's OWN active product if they have one, ELSE the shared (NULL-coach)
product, **never merged** (`_coach_has_own_product` gates the pricing reads AND `_create_order_guarded`).
An **active membership makes COURT bookings free** (`settlement_mode=membership_covered`, resolved server-side,
guarded to courts only); memberships support typed tiers + optional access windows (outside the window → PAYG)
and the **"7 Day Trial Period"** on signup (`provider='trial'`, court-only, auto-lapses → PAYG). **The trial
is granted ONLY to a genuinely-new member** — `auth/principal.py` gates it on `upsert_user_by_clerk_id`
returning `_created=True` (a fresh INSERT); a returning login or a seeded/imported Wix user (matched by
clerk_id/email, `_created=False`) is NEVER trialed, so the ~880 Wix imports stay PAYG. Audit/cleanup:
`scripts/audit_trials.py`. Bundles are unit/minute-based (a pack covers any length). The Wix-era
"member R0" court tier is GONE.
**Court SERVICES:** courts can belong to distinct court services (e.g. "Hardcourt Hire" vs "Clay Hire"),
each `billing.product(kind='court_booking')` with its own price + allocated courts via
`diary.resource.product_id` (NULL → the club's single default court product; single-service clubs
unchanged). Pricing/availability/booking are court-service-aware (`diary.pricing.court_service_for_resource`).
**Per-service PACKS:** a pack (`billing.bundle_plan`) + wallet carry `product_id` = the SPECIFIC service
it belongs to; `match_wallet` is product-aware + backward-compatible (legacy NULL-product = coach+kind
match). **Packs are created/edited ONLY under a service** (the service editor → `/api/services/<id>/packages`);
the standalone "Session packs" section + `AdminUI.bundlePlans` + the coach-onboarding packs step + the
admin/coach bundle-plan write routes were DELETED (GET `/api/admin/bundle-plans` kept for offline
issue-pack). Backfill existing packs onto their service with `scripts/backfill_pack_products.py`.
**SEMI-PRIVATE (squad) lessons:** a lesson SERVICE can carry >1 client on one slot via
`billing.product.max_clients` (int, default 1; set in the service editor's "Semi-private (squad)" card,
lessons only, 1–12). Billing is **PER HEAD** — each client gets their OWN owed order at the service price,
never merged. `create_booking(extra_clients=[…])` inserts each as a `diary.booking_party` (role `partner`)
+ a separate order linked via `order_line.booking_id` (booking.order_id stays the PRIMARY's). Each head is
billed to whoever **PAYS**: the player if a member, else their **GUARDIAN** (`_bill_owner` →
`iam.guardian_user_id_for`) — so a parent's two kids raise two orders BOTH owned by the parent (spend rolls
up to the payer, activity to the player). **Add a player LATER** (squad confirmations land late):
`diary.bookings.add_lesson_partner` + `POST /api/diary/bookings/<id>/add-player` (email or user_id; same
edit gate as reschedule) — surfaced as an "Add player" action on the shared `Widgets.TransactionDetail`
(`can.add_player`, true only when the lesson is semi-private + below its cap). The player PICKER is
`GET /api/diary/members/search` (staff-only) → `iam.search_members_with_dependents` (members AND a parent's
kids as their own rows); the shared `CRMUI.addLessonPlayerModal` (staff = name search, self-booking member =
own-kids search, email fallback) serves BOTH the add-later modal and the upfront booking-flow squad step.
`_addable_player_uid` (route) validates each extra player: a non-staff booker may add only club members +
their OWN kids, never an arbitrary account or another family's child; staff add any in-club member/child.
**Cancel voids EVERY order on the booking** (primary + per-head partners), so no partner is left owing.

**Three purchasing models:** PAYG (per-duration) · membership (term plans) · tokens/bundles (prepaid packs,
atomic draw-down + idempotent credit-back). Memberships & packs are also purchasable **offline**
(at-court/monthly → owed order, activated immediately). **A paid pack is NEVER bypassed:** `create_booking`
(and the squad/partner path) AUTO-DRAWS a matching active pack even when an OWED method (at_court/monthly_account)
is chosen — so a pack-holder can't be double-charged by a wrong tap or a stale client (the front-end also
defaults a pack-holder to "Covered by your pack"). Don't regress the draw to fire only on `settlement_mode='token'`.
**One payment rule** (`billing.product.payment_modes`):
>1 mode → choose · single non-online → immediate · online → Yoco. Frontend: `frontend/js/pay.js`.
- **Every service purchase respects its OWN `payment_modes` — enforced SERVER-SIDE, per the EXACT service.**
  A COURT/LESSON booking scopes the guard to the resolved `product_id` (`_service_payment_modes_guarded`),
  so a card-only Clay court refuses pay-at-court/month-end (not just the UI). A PACK inherits its service's
  modes (`billing.bundles.allowed_purchase_modes` intersects the pack's product modes with the club-enabled
  set — a card-only pack is card-only, with **NO at-court fallback**: an unpayable restricted pack is
  refused, never granted on an unpaid/owed order). A CLASS enrolment (`diary.classes.enrol`) is gated like
  a booking — `membership_covered` is downgraded to at-court (classes are court-only-free), `free` is
  admin-only, and the money mode must be club-enabled AND offered by that class's service. Members/guests
  are bound to these; admins/coaches override. (Membership checkout already scoped to its own modes.)

**Promotions — specials with promo codes, redeemed at checkout (`billing/promotions.py`, LIVE).** A promotion
is an OFFER + a redeemable CODE (`billing.promotion` + `promotion_redemption` + `promotion_code`). **The
invariant, same shape as the invoice rule: redeeming DELEGATES to `billing.statement.discount_order` — it
NEVER invents a second debt store** (one debt = one order), so the pro-rata multi-line split, the
coach-commission lockstep and the "was → now" audit all come for free. Four kinds, all live:
`percent_off` · `amount_off` · **`bonus_period`** (membership "3 months → +1 free" — the bonus is just extra
`months` on the existing period grant) · **`bonus_units`** (pack "buy a 10-pack, get 12" — reuses
`adjust_wallet`). **A bonus is NOT a discount** — the order price is untouched and checkout says "N free
months/sessions added". Both bonus kinds grant on BOTH paths (online → at activation/first wallet grant;
offline → on the fresh redemption) and are **guarded against double-granting on a webhook replay** — don't
regress that. Codes are either one shared `promotion.code` or a batch of **unique per-recipient codes**
(`billing.promotion_code`, single-use, unguessable — minted for a Klaviyo campaign); lookup checks shared
first, then per-recipient. Eligibility = scope/window/caps/min-spend/first-time/stacking; a refund or void
reverses the redemption. Admin UI: **Setup → Promotions & offers** (+ "Unique codes →"). Emits
`promo_redeemed`. Spec: `docs/specs/PROMOTIONS-ENGINE.md`.

**Online payments (Yoco) — wired & verified.** `yoco_billing/` is a pure adapter behind
`register_gateway`/`get_gateway` (`billing/` core untouched). An `online` booking creates an `awaiting_payment`
order + `held` booking → `booking.js` calls `Pay.startYocoCheckout(order_id)` → `POST /api/billing/yoco/checkout`
returns Yoco's `redirect_url` → hosted page → `POST /api/billing/yoco/webhook` (Standard-Webhooks verified) →
`apply_payment_event` → order `paid` + booking `confirmed`. **Two gates, both on:** `PAYMENTS_ENABLED=1` (global)
+ per-club `club.policy.allow_online_payment` (Admin → Settings → Payments; the upsert is INSERT-ONLY so the
boot re-seed can't reset it).
- **Refunds:** Admin → Billing → "Recent online payments" → "Refund only" or "Refund & cancel" →
  `POST /api/billing/yoco/refund`. Full refund sends NO amount; the lookup uses the CHECKOUT id (`ch_`), NOT
  the webhook's payment id (`p_`) — refunding a `p_` 404s.
- **Reconciliation (missed-webhook recovery):** `yoco_billing/reconcile.py` — `client.get_checkout` asks Yoco;
  a `completed`+`paymentId` replays `charge_succeeded` (idempotent). `POST /api/billing/yoco/reconcile/<order_id>`
  + `POST /api/cron/reconcile-payments`. **Recovering the payment is NOT enough — the purchase must also be
  ACTIVATED.** Both the webhook AND reconcile call the ONE shared `yoco_billing/activation.py::activate_purchase`
  (activate the membership/pack + emit `bundle_activated`); it's idempotent and runs even on an `{ignored}`
  replay, so a webhook-after-reconcile REPAIRS an un-granted pack. **Never let reconcile settle without calling
  it** — the historic gap left online packs `paid` but `pending`/unusable with no email (Render Free sleeps →
  webhook missed → reconcile is the common path). Remediate stragglers with `scripts/fix_bypassed_packs.py`.
- **Receipts:** `GET /api/billing/receipt/<order_id>` (online AND desk payments) → `frontend/app/receipt.html`
  (+ a professional PDF at `GET /api/billing/receipt/<order_id>/pdf`).

**Invoice & receipt DOCUMENTS (`billing/invoicing.py` — the ONE module; `billing/invoice_pdf.py` = reportlab
renderer).** An invoice is a **document that RENDERS over live orders, NEVER a second debt store** — the debt
stays on `billing."order"` (one debt = one order). An invoice's line amounts FREEZE at issue (an immutable
document + seller/bill-to snapshot); its **paid/outstanding derives LIVE** from the orders it references — so a
mid-month card payment flips the invoice to Paid and double-counting is structurally impossible. Numbering is
**gapless per club** (`club.billing_profile.invoice_prefix` + `next_invoice_seq`, allocated atomically at issue).
- **Company financial identity** = `club.billing_profile` (registered name, company reg no., **bank details** for
  EFT-payable invoices, invoice terms/footer, + a **DORMANT VAT block** — NextPoint is NOT VAT-registered, so
  `vat_number` is NULL and no VAT line shows; flip it on later without a rebuild). Edited at Admin → Setup →
  **"Company & billing details"** (`AdminUI.billingDetails`, `club_admin`+). Letterhead logo = `club.branding.logo_url`.
- **Three issue paths, one document type:** admin **ad-hoc** invoice (`create_invoice` → numbered doc, emails it) ·
  **intra-month** "invoice the outstanding balance" (`POST /api/admin/clients/<id>/statement-invoice`) · **month-end**
  auto-consolidation (`run_month_end` rolls each client's open orders into ONE statement invoice). `issue_invoice`
  skips orders already on an active invoice (one active invoice per open order — no double-issue).
- **Serve/act:** `GET /api/billing/invoice/<id>` (+ `/pdf`), `POST …/mark-paid` (EFT/cash → settles every open order
  via the desk-payment core → receipts fire → invoice derives Paid), `POST …/void`. Lists: `GET /api/me/invoices` ·
  `GET /api/admin/clients/<id>/invoices`. Client UI: `#/invoices` (view + download PDF + pay-outstanding).
- **Email:** the `invoice_issued` event reuses the booking-confirmation shell + a statement summary + a **"Pay online"**
  box + the **PDF attached** — attachment is **flag-gated `EMAIL_INVOICE_PDF_ENABLED`, now ON** (verified
  2026-07-18; the SES key carries `AmazonSESFullAccess`/`ses:SendRawEmail`, so MIME attachments send).
  `EFT` desk payments carry a **reference** (`provider_payment_id`, captured in the "Mark as paid" modal).

**Booking flow** (`frontend/js/booking.js`, full-screen): Service → **Schedule** (month calendar with inline
per-duration chips for court/lesson; live price or "Covered by your membership"; a court booking defaults the
court to "Any", but a **lesson is coach-FIRST** — pick the coach up front, see THAT coach's rate card, no "Any
coach") → **Pay & confirm** → success. Classes have fixed session times: pick a session → enrol. **The SAME
widget does ON-BEHALF for all three roles** via `BookFlow.start(principal, service, {onBehalf, coachLock,
loadPackages})` (client self-book · coach book-for-client, coach-locked · admin book-for-client, owner picks
coach); on-behalf auto-draws a matching pack wallet (lesson = coach-scoped, class = coach-agnostic) and skips
Yoco. **When editing `booking.js`, PRESERVE** the `createBooking` call + the online seam
(`res.booking.order_id` → `Pay.startYocoCheckout`).

**Booking-validation principle — the front end only ever offers CONFIGURED services.** The picker shows only
durations with an active `billing.price` row (`durations_for`). A **lesson reserves coach∩court**:
`create_booking` auto-assigns a free court and refuses if no coach OR no court is free
(`COACH_REQUIRED`/`NO_COURT_AVAILABLE`); only coaches with weekly hours + `is_bookable` are offered.

**Courts on a lesson — the client picks the COACH, the club allocates the COURT.** A client never sees a
court picker for a lesson (they do for court hire). When `create_booking` isn't given a `court_resource_id`
it calls `diary.bookings._pick_court_for_lesson`: the **coach's preferred court**
(`iam.coach_profile.preferred_court_resource_id`, set at Coach → profile → "Preferred court") when it's FREE,
else `_first_free_court`. It is a **preference, never a lock** — a busy favourite must never make a lesson
unbookable. An explicitly-passed court always wins. The **staff** on-behalf booking flow shows a court
dropdown pre-defaulted to that coach's preference (`booking.js`, gated on `st.onBehalf || st.coachLock`);
`/api/diary/resources` carries `preferred_court_resource_id` on each coach row so the picker needs no extra
fetch.

**Reschedule moves TIME and/or COURT** — `reschedule_booking(..., new_court_resource_id=)`, body key
`court_resource_id` on `PATCH /api/diary/bookings/<id>`. A **court** booking's own `resource_id` changes; a
**lesson** stays on the coach resource and its auto-held court row moves instead. The target is validated up
front (`_court_is_free`, excluding the booking's OWN rows via `_linked_booking_ids` so it can't block itself)
→ `COURT_NOT_AVAILABLE` rather than a bare `SLOT_TAKEN`. Court moves are single-booking only, never a series
(`COURT_MOVE_SINGLE_ONLY`). Omitting the key preserves the old behaviour exactly.
**A court move re-runs the MONEY guards a time move runs** — a COURT booking may not cross court
SERVICES (`COURT_SERVICE_CHANGED`: it is priced by its service, and `reprice_booking_order` re-prices
on the SAME product so it could never correct the change), and a `membership_covered` booking
re-runs the FULL entitlement against the TARGET court (`COURT_NOT_COVERED` — the time-window check
alone let a free booking move onto a clay court members are never covered for). The service compare
NORMALISES None (`str(a or "") != str(b or "")`): in a multi-service club an unallocated court
resolves to an ambiguous None, and a short-circuit would wave that move through. A lesson's held
court may move freely — a lesson is priced by its LESSON service. `CRMUI.rescheduleModal` filters the
court list to the booking's own service so the UI never offers a move the server will refuse.
**Frontend: `CRMUI.rescheduleModal` is the ONE reschedule UI** (date/time + configured durations + court),
shared by client · coach · admin · home — it replaced four drifted forks, none of which could move a court.
Role differences are config: `canChangeCourt` is false for a member's LESSON (the court is club-allocated)
and true for their court hire and for all staff.

**Lesson approval lifecycle (accept / propose / decline).** Per-coach `iam.coach_profile.review_bookings`:
ON → a CLIENT self-booking with that coach creates a **`requested`** booking reserving NOTHING until the coach
acts; a coach/admin **on-behalf** booking ALWAYS auto-confirms. Coach actions `POST /api/diary/bookings/<id>/
{accept,propose,decline}`: accept → assign court + settle → `confirmed`; propose → `proposed` (client
accepts/declines/withdraws in My Bookings → "Needs your attention"); decline → `cancelled`. `requested`/
`proposed` are in the status CHECK but NOT the GiST exclusion (they hold no slot).

**Unified client statement** (`billing/statement.py`): one debt = one `billing.order`, settled once. The account
page shows ONE reconciled "Your statement", grouped by category with tick-to-part-settle; admin void/write-off;
coach `coach_arrears` kept in **lockstep** with orders so commission accrues exactly once. Design:
`docs/specs/UNIFIED-STATEMENT.md`.

**The Money tab = ONE `Widgets.Earnings` (`frontend/js/widgets/earnings.js`) — a club-vs-coach P&L across
admin + coach, config-only (no fork).** Admin Money HOME is the reconciling band + a section menu (New invoice ·
Sales by day · **Club earnings** · Bookings by day · Approvals · Club activity). **"Club earnings"**
(`#/money/revenue`) is the roll-up: **CLUB earnings = the DIRECT services it runs** (court/membership/pack, 100%
club) **+ the COMMISSION taken from each coach** → Total (collected-now + projected-when-all-owed) + **Club keeps
vs Coaches keep**; drill a coach → their **P&L** (Total sales − discount − write-off = Net ; Net = Received +
Owed ; commission −coach/+club REALISED on received + PROJECTED on owed at the same rate ; Coach-keeps-total vs
Club-commission-total) → by client → transaction → the shared record; a direct service drills to its clients.
The **coach app** Money is the coach's OWN P&L (same widget, "You keep" wording — never other coaches / the club
roll-up). All off the ONE `_earnings_cte` (per-order coach attribution — lesson/class/pack → that coach,
court/membership → NULL = club) via `admin.repositories.revenue_club_overview` / `revenue_coach_pnl` /
`earnings_clients` / `earnings_transactions`; commission split = realised from `cockpit_coach_earnings`,
projected-on-owed at the `commission_rule` rate. Retired: the admin **Coach-settlement** + **Online-payments**
tabs (+ `earnings_coaches`). **Commission accrues to the coach on EVERY collection method** (Yoco / invoice
paylink / cash-EFT desk / 'pay-all' statement) through the ONE payment core — no method short-changes a coach
(monthly guard: `python -m scripts.reconcile_coach_commission`).

**Club↔coach settlement.** The coach's running `coach_ledger` balance surfaces in the coach P&L (net balance
with the club) + the roll-up's "Coach payouts due" (`billing.commission.settlement_overview`); a recorded
**`coach_payout`** (`record_coach_payout`, both directions + offset, idempotent on `ref_id=payout.id`) nets it —
routes `POST/PATCH/GET /api/admin/coach-payouts` + `GET /api/admin/financials/settlement` remain. The standalone
Settlement Money tab was retired, but the **Record-payout action was re-homed onto the coach P&L card** —
`revenue_coach_pnl` returns `ledger_balance_minor`, and the admin drill's coach P&L shows "Net balance with the
club" + a **Record payout** button (`Widgets.Earnings` `cfg.onRecordPayout` → `recordPayoutModal` →
`AdminAPI.recordCoachPayout`, prefilled to settle) that posts the netting `coach_ledger` entry. **Month-end sweep**
(`billing.commission.run_month_end` → `POST /api/cron/month-end`, `OPS_KEY`-guarded): accrues coach arrears +
rent, then for each client with an OPEN balance **consolidates their open orders into ONE numbered statement
invoice + a pay-link email** (`invoice_issued`; else a plain `statement_ready` reminder — a client who owes
nothing gets NO email), idempotent per `(club,user,period)` via `billing.month_end_notice`. Fired by
**`.github/workflows/month-end.yml`** on the **25th** (the club billing day; rides the keep-warm CI pattern — the
four `render.yaml` crons stay commented out).

**Client month-at-a-glance + the ONE month-aware 360.** `billing.me.activity_summary(month)` →
`GET /api/me/activity-summary`: sessions PLAYED (lessons/court/classes, standalone courts only) + minutes +
spend-by-service + billed/paid/outstanding + weekly buckets. Surfaced on `get_client_360` (now takes `month=`,
adds a per-service breakdown — the **month → client → service → transaction** coach drill; the parallel
`coach.get_client` reader was retired, so every coach client view is a view off the ONE composer). Frontend:
`CRMUI.activityBlock / spendBlock / weekChart` = ONE shared renderer for the client Home modules AND the Client
360 rollup (no chart on the 360). The client Home is Book(services) → Your sessions → Match-analysis (an "AI"
gradient panel) → a month-navigable Billing+Activity summary → Plan; **no emoji** (drawn line-glyphs).

## First-party analytics + the admin Overview tab
`analytics/` is a read-only, platform-owner dashboard (`/overview.html`, rolling `?days=`) built on **guarded**
aggregations (a missing/empty table → empty panel, never a 500). The admin console's **native Overview tab**
(`#/overview` in `admin_app.js`) is driven by the `insights/` lane instead (`GET /api/insights/overview?month=`
— month-scoped daily ECharts; the old `/overview.html` iframe was retired). First-party beacon: `analytics.js`
→ `POST /api/track/page`; **`beacon.py` resolves `club_id` server-side** (browsing host → `iam.resolve_club_by_host`,
else `sole_club_id`) because the DB-less web can't emit the UUID, and stores a non-PII `metadata.authed` flag
(set client-side via `window.cfAuthed` in `auth_client.js` once Clerk resolves) for the logged-in-visitors metric.
**Public vs members-area:** the portal is an SPA, so a signed-in member fires a `page_view` on
every route change — which used to swamp the "website traffic" numbers. Every public-traffic panel in
`analytics/repositories.py` now filters `metadata.authed != 'true'` (marketing traffic = PUBLIC visitors only)
and `members_area()` reports signed-in in-app activity separately; the KPI headline is **Unique visitors**
(people), "Website visits" was relabelled **Page views**.

**The `insights/` read-layer is six admin-gated endpoints** (`/api/insights/…`, all `club_admin`+, club_id
FROM THE PRINCIPAL never the body, every repo read `_guard`-wrapped): `overview` · `bookings-by-day` ·
`sales-by-day` · `court-utilisation` · `trial-cohorts` (trial→paid by start-month cohort, 14d/30d/ever) ·
`web-metrics`.

**Google data reaches the dashboard by CI PUSH, not an API call — this is the seam to understand.** The org
security policy blocks downloadable service-account keys, so **the live app can never call GA4/GSC**; only
the keyless-WIF `marketing-digest` GitHub Action can. It therefore POSTs the day's structured metrics to
`POST /api/cron/analytics-ingest` (`OPS_KEY`-guarded, in `diary/routes.py`) → **`core.web_daily`** (the
snapshot store, `core/schema.py`) → `insights.web_metrics` renders it. **No Google credentials ever touch
Render.** Consequence: if the Acquisition panel goes stale, suspect the Action or the ingest, not the app —
and never "fix" it by adding a Google API client to the API service.

## Growth & acquisition measurement (Google Ads / GA4 / gclid) — LIVE
Know which ad clicks become paying members, and feed that back to Google so bidding chases buyers, not clickers.
- **Google tag (GA4 + Ads)** injected by `web_app._google_tag_head` — dark until `GA4_MEASUREMENT_ID` /
  `GOOGLE_ADS_ID` set. `window.cfConversion(name)` maps a semantic event → the Ads conversion `send_to`
  (`GOOGLE_ADS_CONVERSIONS` env JSON); `cfTrack` fires GA4. Sign-up CTAs + booking-complete fire client-side.
- **gclid capture** (`frontend/js/attribution.js`, injected on every served page): records the FIRST
  gclid/gbraid/wbraid/utm on landing → flushes once via `TFAuth` to `POST /api/me/acquisition` after sign-in →
  `core.repositories.acquisition.record_acquisition` persists onto `core.acquisition` (FIRST-TOUCH WINS).
  Populated the previously-dark `core.acquisition.gclid`.
- **Offline conversions** (`offline_conversions/` — a SHARED, PORTABLE package kept **byte-identical** with the
  1050/ten-fifty5 repo, like the analytics engine): when a gclid'd buyer PAYS, the `emit()` funnel's 4th forward
  (`recorder.record_from_emit`, event `payment_succeeded`) ledgers a `core.offline_conversion` row; the feed
  `GET /feeds/google-ads/offline-conversions.csv` (HTTP Basic auth via `GOOGLE_ADS_FEED_USER`/`PASS`, **dark/404
  until set**) serves it to Google Ads' scheduled upload. **NO developer token / manager account needed** — the
  API Center is manager-only, which is exactly why we use the CSV-upload route. The Google Ads conversion action
  MUST stay named exactly **`Offline purchase`** (matches `recorder.CONVERSION_MAP`); the only per-repo glue is
  that map. `schema.py` owns `core.offline_conversion` (in `db.BOOT_MODULES`); registered in `app.py`.
- **Account (NextPoint Tennis Centre, `AW-17077631191`)**: 2 primary web conversions (start_free_week, booking)
  + `Offline purchase` (Purchase, value-based ZAR); GA4↔Ads linked (auto-tagging + Personalized Advertising on);
  GA4↔Search Console linked; a "High-intent visitors (booking/pricing)" remarketing audience. Full runbook +
  final state: `docs/specs/GOOGLE-ADS-PLAN.md`. Bidding: Maximize Clicks R15 cap → revert to Max Conversions
  after ~15–30 conversions accrue.

## Cross-brand marketing measurement — the daily digest (GitHub Actions, keyless)
A **CI-only** report covers organic growth **across BOTH brands** (NextPoint + Ten-Fifty5). It lives in
`.github/workflows/marketing-digest.yml` + `marketing_digest/`, rides the free-Actions keep-warm pattern, and
touches NO app code — so `frontend/marketing/` and `marketing/` (the untracked ad-ops notes) are separate.
- **`marketing-digest.yml`** (07:00 SAST daily) runs `marketing_digest/digest.py`: a per-brand GA4 (7d) +
  Search Console (28d) organic-growth report — active users, sessions, top pages/queries, and **striking-distance
  queries** (avg position 8–20 = what to write next). Auth is **KEYLESS Workload Identity Federation** (org policy
  blocks SA key downloads) → the `marketing-engine@marketing-engine-502809` SA reads whatever GA4/GSC properties
  it's been **granted in the consoles** — coverage is grant-controlled, **add a brand = add a `BRANDS` row +
  grant the SA, no other code**. Output commits to `marketing_digest/reports/` (the frequent `chore(marketing):
  daily digest` commits) + emails each brand its own slice via the OPS-guarded API (`OPS_KEY` unset → digest
  still runs, skips email).
- **Tag-breakage monitoring = the digest itself** (a GitHub-Actions `marketing-canary.yml` tripwire was tried
  and **DELETED 2026-07-18**: both sites + their Render origins sit behind Cloudflare, which blocks GitHub's CI
  IPs, so it could never verify the live tag from Actions — only false-fails). If a tag ever goes dark, that
  brand's GA4 traffic flatlines to zero in the morning digest — a louder, more reliable alarm. (The blank-tag-ID
  blueprint-sync gotcha that caused the original week-long blackout is guarded by committing the IDs INLINE in
  `render.yaml`, never blank — see the render.yaml marketing-tag comments.)
- **Repo model (where marketing work lives):** the ENGINE (digest + keyless WIF access) lives HERE and
  covers BOTH brands; each brand's SITE + blog CONTENT lives in ITS repo — NextPoint here (`frontend/blog/_posts/`,
  images `/img/`), **Ten-Fifty5 in the 1050 repo** (`frontend/blog/_posts/`, images `/blog/images/`, published via
  its own `build_blog.py`, commit `CLAUDE_CODE=1`; weekly coworker SEO-scan→post workflow). Full spec:
  **`docs/specs/MARKETING-ENGINE.md`**. NextPoint also has a Google Business Profile playbook (physical club →
  local map pack). Ten-Fifty5 is **Render-only for users** (Clerk auth + PayPal, no Wix) but retains dormant,
  DB-coupled Wix scaffolding — a decommission is scoped (DO NOT rush) in the 1050 repo's `docs/DE-WIX-DECOMMISSION.md`.

## Ten-Fifty5 embed — match analysis inside the members area (LIVE, private test)
A logged-in member opens **Ten-Fifty5** (AI match analysis / technique — the 1050 product; web at
`ten-fifty5.com`, API at `api.nextpointtennis.com`) **inside** the client SPA in an iframe, signed in with
their OWN NextPoint Clerk token — **no second login**. The two products are **separate Clerk apps**
(`clerk.nextpointtennis.com` vs `clerk.ten-fifty5.com`); the seam is a `postMessage` **token relay** (both
repos' `auth_client.js` share the Wix-era lineage) + **issuer federation** on Ten-Fifty5's verifier (it now
trusts BOTH issuers via `AUTH_ISSUERS`). **Email is the cross-system key** — Ten-Fifty5 auto-provisions the
member by email on the first authenticated hit.
- **NextPoint side:** `client.js` `#/analysis` route + `renderAnalysis()` (auto-fits the iframe height —
  `innerHeight − frameTop − cf-main paddingBottom − 24`, re-fit on resize — so the OUTER page never scrolls) +
  a Home card (**"Coming soon"** card for non-allowlisted); `auth_client.js` parent `serveChild` serves a token
  ONLY to the allowlisted Ten-Fifty5 origin (`TF5_EMBED_ORIGINS`) and its status payload carries **`mode`** (the
  TF5 child reads `status.mode`, NextPoint children read `status.authed`); `web_app.py` injects
  `__TF5_EMBED_URL`/`__TF5_EMBED_ALLOW` + substitutes `__TF5_EMBED_ORIGINS__`.
- **Gated to a PRIVATE prod test** via `TF5_EMBED_ALLOW_EMAILS` (courtflow-web). **Launch = clear that env**
  (empty → all members). Marketing funnel: a public **"Match analysis"** CTA on `frontend/marketing/home.html`
  → `ten-fifty5.com` (this is separate from the embed and stays live).
- **The 1050 repo IS modified for this** (the ONE exception to "read-only reference" below): `auth_v2/verifier.py`
  (multi-issuer allowlist), `frontend/auth_client.js` (trusted-parent guard + **multi-hop relay** — the portal
  nests each page in a content iframe, so a middle frame proxies its grandchild's auth up to its own parent;
  without this only the empty portal shell authed), `locker_room_app.py`, `render.yaml`. All additive +
  flag-guarded; **commit code in that repo with `CLAUDE_CODE=1`** (its lane-guard hook blocks code commits
  otherwise). Rollback = clear `AUTH_ISSUERS` (Ten-Fifty5) or `TF5_EMBED_URL` (NextPoint). Env values +
  the Render-service-name map → `docs/specs/ENV-STATUS.md`.

## Commands
- **Run the API locally:** `gunicorn wsgi:app` (or `python -m app`) — needs `DATABASE_URL`.
- **Run the web/portal locally:** `python web_wsgi.py` (DB-less; `PORT=5060`). Preview marketing:
  `MARKETING_HOSTS=localhost python -c "import web_app; web_app.app.run(port=5061, threaded=True)"`
  (Chrome needs `threaded=True` for parallel assets).
- **Seed club #1:** `python -m scripts.seed_nextpoint` · **provision a tenant:** `python -m scripts.provision_club`
- **Operational scripts index:** `scripts/README.md` — the audit/backfill/import/verify one-offs
  (`audit_trials.py`, `backfill_pack_products.py`, `import_wix.py`, `verify_live.py`, …) with when-to-run notes.
- **Fire a cron by hand:** `python -m crons.trigger <reminders|capacity-sweep|monthly-invoice|membership-refill>`
  (needs `CRON_API_BASE` + `OPS_KEY`).
- **Rebuild blog/SEO:** `python build_blog.py`
- **Verify against REAL Render Postgres (read-only, safe):** `python -m scripts.verify_live` (reads
  `DATABASE_URL` from a gitignored `.env.local`, never printed).
- **Wix→Render cutover (SUPERVISED — runbook `migration/CUTOVER_RUNBOOK.md`):** take-on scripts default to
  `--dry-run` (print counts, ROLLBACK), are idempotent, and only an explicit `--commit`/typed `YES` writes.
  Wrappers: `scripts/import_members.py`, `import_subscriptions.py` (matched to plans BY LABEL), `import_lessons.py`.
  The 301 redirect engine (`migration/redirects.py`) IS wired into `web_app` (`register_redirects(app)` at
  boot, before the catch-all) — it loads `migration/redirects.csv` (48-rule Wix→Render map, live since cutover).
  **Never let an agent change DNS or flip the SEO cutover — Tomo does this.**

## Tech defaults (match 1050 so reuse is clean)
- Python 3.12 + Flask + Gunicorn + Postgres. **DB access = SQLAlchemy Core** (`db.get_engine`/`text()`,
  explicit `session`; **repos never commit** — callers compose via `db.session_scope()`) over **psycopg 3**.
  **Idempotent boot DDL** (`ADD COLUMN IF NOT EXISTS`) — no Alembic. Extensions: `btree_gist` + `pgcrypto`.
- Vanilla-JS SPAs (no heavy framework). The one dependency added for the diary UI is a calendar/ECharts seam
  (lazy-loaded).
- **Reuse, don't import.** Copy patterns from the Ten-Fifty5 repo at `C:\dev\webhook-server` (**READ-ONLY
  reference — never touch its repo/DB**). Do NOT bring over the ML/T5/GPU/video machinery. **ONE exception:**
  the Ten-Fifty5 members-area embed (above) required careful, additive, flag-guarded changes to that repo's
  auth (`auth_v2/verifier.py`, `frontend/auth_client.js`); commit there with `CLAUDE_CODE=1`. Its live DB
  (`sportai-db`) is still off-limits.

## Gotchas
- **`api.nextpointtennis.com` is already live on the 1050 service** — do not break it. The new platform has its
  own API host; changing a Render custom domain can recreate a service. (The members-area **Ten-Fifty5 embed**
  now *deliberately* calls this API with federated NextPoint tokens — see the embed section.)
- **Ten-Fifty5 embed — Render service names ≠ `render.yaml` `name:`.** The live 1050 API is the Render service
  **"Sport AI - API call"** (custom domain `api.nextpointtennis.com`), NOT the service literally named
  `webhook-server` (that's a **cron**). Set env on the real service; the blueprint does **not** auto-sync env.
  Federation trap: **`AUTH_ISSUER` (singular) vs `AUTH_ISSUERS` (plural)** — the multi-issuer allowlist is
  `AUTH_ISSUERS` (a comma-list in the singular var is now tolerated, but use the plural); leave `AUTH_JWKS_URLS`
  UNSET (JWKS derived from each issuer, no ordering to break). The nested-portal iframe needs the **multi-hop
  relay** in `auth_client.js` (a middle frame proxies its grandchild's auth up) or nested pages fall back to
  legacy → "Missing email or API key".
- **Never let an agent change DNS.** The Wix→Render SEO cutover is supervised by Tomo.
- **The booking API returns `{booking:{order_id,status}, checkout}`** — read `res.booking.order_id`, NOT
  `res.order_id` (that bug silently confirmed online bookings without redirecting).
- **A service's `payment_modes` is enforced SERVER-SIDE per the EXACT `product_id`** — resolve allowed modes
  by the resolved service product, NEVER by `kind` alone (a kind-only resolve reads the club's default court
  product and lets a card-only Clay court/pack/class be taken pay-at-court on an owed/unpaid order). Bookings
  pass `product_id` to `_service_payment_modes_guarded`; packs use `billing.bundles.allowed_purchase_modes`
  (no at-court fallback for a restricted pack — refuse if unpayable); `diary.classes.enrol` gates the mode
  (and never lets a member conjure a free seat via `membership_covered`/`free`). Don't regress these to a
  kind-level check.
- **A CLASS resolves its service through `diary.resource.product_id`, NEVER by joining on names.**
  `create_class_type` links the resource to its `billing.product` at birth, and `diary/schema.py` boot-
  backfills legacy rows (conservatively — only a NULL link with exactly ONE name+coach match; ambiguous
  or already-drifted rows are left for a human). The old name join broke silently on a service RENAME
  (which updates `billing.product.name` only), leaving `class_session.price_id` NULL → a kind-level
  fallback billed the class at some OTHER class's rate under that class's payment rules. Resolvers:
  `_class_service_product_id` (durable link) · `_class_product_for_session` (price → else resource) ·
  `_class_effective_price_id` (re-resolves when the frozen price row was RETIRED, so a removed
  variation can't bill R0). `enrol` refuses `PRICE_NOT_CONFIGURED` like `create_booking` does.
- **`membership_started` is emitted from `billing.membership.emit_membership_started`, NOT from the gateway.**
  `apply_payment_event`'s `subscription_active` branch looks like the producer but **nothing produces that
  kind** — NextPoint sells memberships as ONE-OFF ORDERS (`charge_succeeded` → `activate_membership_for_order`),
  never provider-managed subscriptions, so that branch is unreachable and the event silently never fired
  (which also killed the `on_trial=false` conversion flip that keys off it). The real emit sits in
  **`_apply_term_grant`** — the ONE function every PURCHASE flows through (online webhook/reconcile AND the
  offline desk buy) — fired only from its two non-replay branches, so a replayed webhook can't double-count.
  **`admin.repositories.grant_membership` emits it too** (`source='admin_grant'`; an extension carries
  `is_renewal=true` so the on_trial flip still runs but conversion measurement can filter renewals) — this club
  grants most memberships by hand, so excluding them left the flag stale. It passes `provider='manual'`
  **explicitly, never read off the row**: the extend branch matches ANY active subscription including a TRIAL
  row, and the emitter drops `provider='trial'`, so reading it back would silently skip the very trialist you
  need flipped. The trial (`grant_signup_trial`) and the Wix import stay excluded — they INSERT directly and
  never reach either path. **It must carry `email`** — that's what the Klaviyo forward keys on. Guarded by
  `sc_membership_started_emit`.
- **`marketing/` is NOT platform code** — local-only ad-ops notes, **gitignored as `/marketing/` since
  2026-07-22** (previously untracked-but-committable, one `git add -A` away from being published). The
  **leading slash is load-bearing**: a bare `marketing/` would also match the tracked public site at
  `frontend/marketing/`, silently ignoring any new page added there. Don't confuse the three: `marketing/`
  (ad-ops, ignored) · `frontend/marketing/` (the public site) · `marketing_crm/` (the CRM lane).
- **`UI.clear(node)` must drop the `cf-loading` class** (it does, in `frontend/js/ui.js`) — `.cf-loading` paints
  a CSS `::before` spinner; emptying children without removing the class leaves the spinner over new content.
  Render results with `UI.clear(box)` before appending.
- **Free-tier cold starts → use timeouts, not infinite spinners.** `auth_client.js` puts a 70s timeout on every
  `apiFetch`. A GitHub Action (`.github/workflows/keep-warm.yml`) pings both services 07:00–21:59 SAST.
- **SQL `:param IS NULL` needs a CAST** (psycopg `AmbiguousParameter`): write `CAST(:df AS timestamptz) IS NULL`,
  never a bare `:df IS NULL`. (This 500'd the master diary.)
- **Cockpit revenue must let refunds through** — refund `billing.payment` rows have `status='refunded'`, so a
  `WHERE status='succeeded'` filter silently drops them. Use
  `(direction='charge' AND status='succeeded') OR (direction='refund' AND status IN ('succeeded','refunded'))`.
- **Guarded analytics reads hide column typos as ZEROS, not errors** — every `analytics/`+`insights/` query is
  `_guard`-wrapped, so a wrong column name returns the empty default and the panel silently shows 0 (e.g. NPS
  read a non-existent `created_at`; the column is **`submitted_at`**). When a panel reads zero, check the SQL
  columns against the actual schema first.
- **`core.usage_event` page_view `club_id` is set server-side in `beacon.py`**, not by the client. The client
  sends NO email/identity (so `account_id` is effectively always NULL — for "logged in" use `metadata->>'authed'`,
  not `account_id`).
- **Capacity-sweep needs no cron:** abandoned `held` bookings are released by **lazy expiry** —
  `release_expired_holds` runs at the top of `compute_availability` + `create_booking`. **It also VOIDS
  the abandoned order** (via `_void_orders_with_no_live_bookings`, and only once EVERY booking on that
  order is dead — a lesson is coach + held court on ONE order, a squad is many heads on one order).
  It used to cancel the booking and orphan the order, leaving `awaiting_payment` rows pointing at
  cancelled bookings that the statement self-heal (`_void_phantom_cancelled_orders`, `open`-only)
  never cleared. A late payment is still safe — `_confirm_held_bookings` re-instates a booking
  cancelled as `hold_expired`. Backlog cleanup: `scripts/void_orphaned_orders.py`.
  **RECONCILE MUST ALSO REACH THAT VOID** — `yoco_billing.reconcile._is_expired_hold_void` re-opens the
  door for an order voided *purely* by hold expiry (the member paid after the hold lapsed and the
  webhook was missed → money with Yoco, no booking, no receipt, invisible in every pending view). An
  order an ADMIN voided has no `hold_expired` booking behind it and stays untouchable, so a cancelled
  sale can't be resurrected. Guarded by `sc_expired_void_is_recoverable`. The four `render.yaml`
  crons stay commented out. **Classes have the same seam:** an `online` class enrolment holds its seat
  (`diary.enrolment.held_until`) pending the Yoco payment; `release_expired_enrolments` (top of
  `list_sessions` + `enrol`) cancels the lapsed-unpaid seat, voids its `awaiting_payment` order, and promotes
  the waitlist — a **paid** seat (order no longer `awaiting_payment`) is never touched.
- **Transactional email = ONE confirm+receipt per purchase** (`marketing_crm/notifications.py::deliver`):
  `booking_detail.load` resolves an order-keyed event (`payment_succeeded`) to its booking/class → the RICH
  block (retitled "Booking confirmed"), else a purchase block for membership/pack. The client block always
  names **"Booked by"** (the actor on an on-behalf/staff booking, the client themselves on a self-book; for a
  class, the guardian when a child's seat is paid by them) and the client's **exact membership tier**
  ("Adult Anytime Play", via `_MEMBERSHIP_LABEL_SQL`) — PAYG simply omits the row. Guarded by
  `sc_confirmation_email_block`; `deliver` SUPPRESSES the
  `payment_succeeded` email for pack + class orders (their own email is the one). **Payment-status wording is
  single-sourced** in `billing.statement.settlement_status_label(state, mode)` — email AND `client360` both
  delegate, so a receipt/email/client-record never disagree. **Coach BCC only on his own lesson/class.** Every
  order-keyed email needs `booking_detail.load` to import `text` (a missing import silently blanks the block).

## Still needs Tomo (config, not code)
- **S3** (`S3_BUCKET` + AWS keys) for coach photo uploads — until set, coaches paste a photo URL.
- **SES** transactional email is **LIVE** (interim — rides the Ten-Fifty5 AWS account, `eu-north-1`). The
  sending key carries **`AmazonSESFullAccess`** (`ses:*`, so `ses:SendRawEmail`/MIME **attachments work** — the
  earlier "interim key lacks SendRawEmail" note was wrong). **Invoice PDF email attachment is ON + confirmed
  working** (`EMAIL_INVOICE_PDF_ENABLED=1`, verified 2026-07-18 — issued invoices email with the PDF attached).
  The booking **`.ics`** attachment can be turned on the SAME way (`EMAIL_ICS_ENABLED=1`) — optional; the in-app
  "Add to calendar" download works regardless. Long-term CourtFlow-domain setup: `docs/specs/SES-SETUP.md`.
  Klaviyo marketing stays dark until `KLAVIYO_API_KEY`.
- **DNS / SEO cutover** for `nextpointtennis.com` — supervised, never an agent.
- **Done (config that WAS pending):** `OPS_KEY` GitHub Actions secret set → the monthly statement sweep
  (`.github/workflows/month-end.yml`) now fires on the **25th** (club billing day), issuing each client's
  consolidated statement invoice + pay-link email; Admin → Setup → **Company & billing details** filled (bank
  details → EFT instructions on invoices); invoice PDF email attachment on (above).
- Volatile env/infra values and full pre-flight: `docs/specs/ENV-STATUS.md` + `BUILD_PROMPT.md`.

## Ground rules
- **Multi-tenant from day one** (the Iron rule, above).
- **New repo, NEW Postgres DB**; reuse existing Render/Clerk/AWS/Klaviyo accounts with project-scoped values
  only. Secrets are `sync:false` in `render.yaml`; go-live flags (`PAYMENTS_ENABLED`, provider env) are
  committed so a blueprint sync can't wipe them.
- Payments are **provider-agnostic** (Yoco adapter first, behind a flag); the diary launches without mandatory
  online pay. **SES sends the transactional confirmations** (`marketing_crm/email/ses.py` — the original plan
  was Klaviyo-sends-confirmations; as-built it is the other way round). Klaviyo is MARKETING-only, opt-in
  only, still dark until `KLAVIYO_API_KEY` — and no minor PII goes in any payload.

## Build history
This file is present-state only. For the dated build history (the booking-flow audit sprint, Frankfurt
migration, admin console redesign, frontend standardisation, unified statement, etc.), see the memory index at
`MEMORY.md` and the authoritative specs under `docs/specs/` (START at `README.md` → `SYSTEM.md` →
`BUSINESS-RULES.md` → `INVENTORY.md` → `OUTSTANDING.md`). `docs/` (`00`→`11`) are the original design docs;
`docs/11` = locked decisions + the 1050 reuse map.

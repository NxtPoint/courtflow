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
**"Guarded by `sc_…`" throughout this file names a scenario function in one of the three harnesses
below** — `grep -rn "def sc_the_name" scripts/` to read the war story it encodes. The static gate is
`py_compile` for the Python **and `node --check` for the frontend JS** — those two, and nothing else:
no ruff/black/mypy/pytest config exists, by choice. Deps: `pip install -r requirements.txt` (Python 3.12).
1. `python -m py_compile $(git ls-files '*.py')` — the `$(…)` is bash; from PowerShell use
   `python -m py_compile (git ls-files '*.py')`.
2. `python -m scripts.check_frontend_js` — **the JS PARSE gate.** `node --check` over every
   `frontend/js/**/*.js`. No DB, no env, ~1s. A JS file that doesn't parse is dead in the browser in its
   ENTIRETY — nothing it defines exists — so `test_all` runs it first too. Run it here as well: it's the
   only gate needing no `DATABASE_URL`, so it still works when the others can't. Added 2026-08-09 after
   real newlines inside a string stopped `admin_app.js` parsing and took `/admin` down for 11 hours
   (Gotchas). **Fails CLOSED if `node` is absent** — a gate that can't verify must not report success.
3. `python -m db` **twice** — second run must be a clean no-op (idempotency gate).
4. `python -m scripts.audit_docs` — **the DOCS gate.** Prose doesn't fail `py_compile`, so docs rot
   invisibly and are then trusted precisely when they're wrong. This extracts the real routes, tables,
   shared widgets, emitted events, scenarios and scripts from SOURCE and reports what the docs haven't
   caught up with (plus broken internal links + disagreeing gate baselines). **Currently 0 misses —
   keep it there.** It would have caught, on the day: an approval lifecycle documented as LIVE in six
   files two days after deletion; `Widgets.CoachStatement` missing from the golden-rule register; and
   13 live events absent from `contracts/events.md`. `--strict` exits 1 for a pre-merge gate.
5. `python -m scripts.test_all` — the JS parse gate (first, no DB) then three rollback-only
   scratch-DB harnesses. Current green baseline:
   **booking 521 / billing 693 / statement 64**. Each uses its own scratch club and always rolls back.
   Run one lane's harness standalone while iterating (each needs `DATABASE_URL` = a local sandbox):
   `python -m scripts.test_booking_scenarios` (diary) · `python -m scripts.test_billing_scenarios` (billing) ·
   `python -m scripts.test_statement_reconciliation`.
   **There is no per-test filter** — each harness runs its whole `SCENARIOS` list (73/95/12 `sc_*`
   functions, each in its own SAVEPOINT). To iterate on ONE scenario, temporarily narrow that list;
   don't commit the narrowing. **Update the "Current green baseline" line above and nothing else**, so
   the numbers can't drift apart (`scripts.audit_docs` fails any doc that claims a DIFFERENT current
   baseline). **What each harness actually covers is catalogued in
   [`docs/specs/SCENARIOS.md`](docs/specs/SCENARIOS.md)** — read it when you need to know whether a
   rule is already guarded; you don't need it to run the gate.

## Local setup (what every gate except the JS one needs)
`pip install -r requirements.txt` (Python 3.12), then **`DATABASE_URL` pointed at a LOCAL sandbox
Postgres — never production.** The harnesses roll back, but they roll back on whatever DB you give
them. Extensions `btree_gist` + `pgcrypto` are created by `python -m db`; the role needs rights to
`CREATE EXTENSION`. There is no `.env.example` — export it in your shell, or drop it in a gitignored
`.env.local` (the only script that reads that file is `scripts/verify_live.py`, which is read-only
against REAL Render Postgres and never prints the URL).
**This is a Windows/PowerShell box.** Bash-isms in the examples below (`$(git ls-files …)`) need the
PowerShell form `(git ls-files '*.py')`; the Bash tool is available for POSIX scripts when you want it.
**Commits are conventional-commits whose subject carries the WHY, not the what** — the house style is
`fix(scripts): reconcile_coach_cash judged the residual by size, not direction`, not `fix: bug`.

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
The platform re-assembles ~80% of the proven **Ten-Fifty5** architecture around one new domain
model: the **diary**. Same shape as Ten-Fifty5, fewer services (no ML/GPU/video).

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
| `month-end.yml` | monthly, the **1st** ~06:00 SAST | `billing.commission.run_month_end` — coach arrears + rent, then one consolidated statement invoice + pay-link per client owing **for the month just ended** (it ran on the 25th until 2026-08-08, so every invoice was issued with days of the month still to come) |
| `reconcile-payments.yml` | hourly, 07:00–22:00 SAST | `yoco_billing.reconcile.reconcile_pending` — recovers payments whose webhook never arrived (Render Free sleeps, so CLAUDE.md calls reconcile "the common path"). The handler shipped at launch but **nothing ever called it** until 2026-07-22 |
| `reconcile-deep.yml` | weekly, Sun 07:40 SAST | the SAFETY NET behind the hourly sweep — `reconcile_pending` defaults to **`hours=72`** and the hourly job passes nothing, so an order that slips past 3 days **ages out and is never checked again**. Sweeps `hours=2400` (100 days) so nothing can hide unverified |
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
- `core.*` — account/user/person, usage_event, consent, nps (ported from Ten-Fifty5 `core_db`)

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
| **Coach / Client** | `coach/`, `me/` | Coach self-service (onboarding, clients-360, statement, cockpit; reschedule/cancel own lessons + move own class sessions) + client self-service (profile, dependents, statement, refund requests). |
| **Analytics** | `analytics/` | Read-only guarded aggregations → `/api/analytics/*` (the standalone `/overview.html`); first-party beacon in `beacon.py`. |
| **Frontend** | `frontend/` | Three role SPAs on one widget layer (below). |
| **Marketing/SEO** | `frontend/marketing/`, `frontend/_shared/`, `build_blog.py`, `migration/`, `marketing_digest/` | Host-switched public site, blog, sitemap, Wix→Render migration scripts, cross-brand organic-growth digest (below). |

**Service editing** (`services/`) is the ONE API a service is edited through by BOTH owner and coach —
`/api/services/*` enforces who may change what (owner = everything incl. commission; coach = their OWN
lesson/class name/variations/payment/packages, NEVER commission), delegating to the billing/admin repos.
**A NESTED id must be checked against its parent, not just the product in the path.** `_load_manageable`
authorises `<product_id>`; the SECOND id used to be taken verbatim, and `patch_price`/`set_plan_status`
scope by `(club_id, id)` only — so a coach could `PATCH /api/services/<their service>/variations/<the
club's COURT price_id>` to zero-rate court hire club-wide, or reprice/retire/adopt any pack. `_own_price`
and `_own_plan` are the guards; `_own_plan` deliberately mirrors `get_service`'s packages query so a
LEGACY unscoped pack stays manageable (that's what `adopt` re-homes). Both are **predicates**, so they're
callable outside a Flask app context and the route owns the response. Guarded by
`sc_service_editor_child_ownership`.
**The editor's variations read filters `active = true`** — "Remove" PATCHes `status='retired'` (→
`active=false`) rather than deleting, so without it a removed variation reappears on the next open; and
`seed_nextpoint` retires legacy NULL-duration court prices by setting `active=false` **without** touching
status, so a status-only filter leaves blank rows on screen forever (`sc_removed_variation_stays_removed`).

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
`diary.bookings.add_lesson_partner` + `POST /api/diary/bookings/<id>/add-player`, same edit gate as reschedule,
surfaced on the shared `Widgets.TransactionDetail`. `_addable_player_uid` (route) validates each extra player:
**a non-staff booker may add only club members + their OWN kids**, never an arbitrary account or another
family's child; staff add any in-club member/child. The picker is `GET /api/diary/members/search` (staff-only)
→ `iam.search_members_with_dependents`, rendered by the shared `CRMUI.addLessonPlayerModal` which serves BOTH
the add-later modal and the upfront booking-flow squad step.
**Cancel voids EVERY order on the booking** (primary + per-head partners), so no partner is left owing.

**Three purchasing models:** PAYG (per-duration) · membership (term plans) · tokens/bundles (prepaid packs,
atomic draw-down + idempotent credit-back). Memberships & packs are also purchasable **offline**
(at-court/monthly → owed order, activated immediately). **A paid pack is NEVER bypassed:** `create_booking`
(and the squad/partner path) AUTO-DRAWS a matching active pack even when an OWED method (at_court/monthly_account)
is chosen — so a pack-holder can't be double-charged by a wrong tap or a stale client (the front-end also
defaults a pack-holder to "Covered by your pack"). Don't regress the draw to fire only on `settlement_mode='token'`.
**One payment rule** (`billing.product.payment_modes`):
>1 mode → choose · single non-online → immediate · online → Yoco. Frontend: `frontend/js/pay.js`.
- **Every service purchase respects its OWN `payment_modes` — enforced SERVER-SIDE, per the EXACT service**
  (never by `kind` alone). Bookings pass `product_id` to `_service_payment_modes_guarded`; a pack inherits its
  service's modes via `billing.bundles.allowed_purchase_modes` (**no at-court fallback** — an unpayable
  restricted pack is refused, not granted); `diary.classes.enrol` gates the mode the same way. Members/guests
  are bound to these; admins/coaches override. Why, and what a kind-level check leaks:
  [GOTCHAS.md#pricing--payment-rules](docs/specs/GOTCHAS.md#pricing--payment-rules).

**Promotions — specials with promo codes, redeemed at checkout (`billing/promotions.py`, LIVE).** A promotion
is an OFFER + a redeemable CODE (`billing.promotion` + `promotion_redemption` + `promotion_code`). **The
invariant, same shape as the invoice rule: redeeming DELEGATES to `billing.statement.discount_order` — it
NEVER invents a second debt store** (one debt = one order), so the pro-rata multi-line split, the
coach-commission lockstep and the "was → now" audit all come for free. Four kinds, all live: `percent_off` ·
`amount_off` · `bonus_period` (membership 3+1) · `bonus_units` (pack "buy 10 get 12"). **A bonus is NOT a
discount** — the order price is untouched — and both bonus kinds are **guarded against double-granting on a
webhook replay**; don't regress that. Admin UI: **Setup → Promotions & offers** (+ "Unique codes →"). Emits
`promo_redeemed`. Kinds, eligibility order, the unique-code batches and both grant paths in full:
**[`docs/specs/PROMOTIONS-ENGINE.md`](docs/specs/PROMOTIONS-ENGINE.md)**.

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
- **Reconciliation (missed-webhook recovery):** `yoco_billing/reconcile.py` asks Yoco about the checkout and
  replays `charge_succeeded` (idempotent). `POST /api/billing/yoco/reconcile/<order_id>` +
  `POST /api/cron/reconcile-payments`. **Recovering the payment is NOT enough — the purchase must also be
  ACTIVATED** via the ONE shared `yoco_billing/activation.py::activate_purchase`; never let reconcile settle
  without calling it. Remediate stragglers with `scripts/fix_bypassed_packs.py`. Why this is the COMMON path,
  not the exception: [GOTCHAS.md](docs/specs/GOTCHAS.md#reconciliation-must-activate-not-just-settle).
- **A successful charge may NOT re-open a CLOSED debt** — `_mark_order_paid` allows only
  `open`/`awaiting_payment`/`paid` plus the one recoverable void (a lapsed hold, via
  `order_void_is_recoverable`). A refusal still RECORDS the payment but skips the whole fan-out and returns
  `needs_attention='payment_on_closed_order'`. `sc_payment_cannot_reopen_a_closed_debt` ·
  [why](docs/specs/GOTCHAS.md#a-successful-charge-may-not-re-open-a-closed-debt).
- **Refund REQUESTS are decided on the transaction record, not in a separate queue** — Money → Refund requests
  is an INBOX; each row opens `#/txn/<order_id>` where a `Decision needed` banner offers Approve & refund /
  Decline beside the payment history. `sc_refund_request_visibility` ·
  [why](docs/specs/GOTCHAS.md#refund-requests-are-decided-on-the-transaction-record-not-in-a-separate-queue).
- **Receipts:** `GET /api/billing/receipt/<order_id>` (online AND desk payments) → `frontend/app/receipt.html`
  (+ a professional PDF at `GET /api/billing/receipt/<order_id>/pdf`).

**Invoice & receipt DOCUMENTS (`billing/invoicing.py` — the ONE module; `billing/invoice_pdf.py` = reportlab
renderer).** The invariants, all load-bearing: an invoice is a **document that RENDERS over live orders, NEVER
a second debt store** (the debt stays on `billing."order"`) — line amounts FREEZE at issue but paid/outstanding
derives LIVE, so double-counting is structurally impossible; numbering is **gapless per club**; **AN INVOICE
COVERS A PERIOD** — the month the SERVICE WAS DELIVERED (`invoicing.DELIVERED_AT_SQL`, the ONE resolver), with
earlier debt frozen as DISPLAY-ONLY `brought_forward_minor`, never re-billed; ONE `invoice_paid` receipt per
payment however many lines it settles; a part-payment settles WHOLE lines, oldest first. The three issue paths,
the routes, the PDF/email flag, the EFT reference and company/bank setup:
**[UNIFIED-STATEMENT.md § As-built](docs/specs/UNIFIED-STATEMENT.md#invoice--receipt-documents)**.

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
unbookable. An explicitly-passed court always wins. The **staff** on-behalf flow shows a court dropdown
pre-defaulted to that coach's preference (`booking.js`, gated on `st.onBehalf || st.coachLock`).

**Reschedule moves TIME and/or COURT** — `reschedule_booking(..., new_court_resource_id=)`, body key
`court_resource_id` on `PATCH /api/diary/bookings/<id>`. A **court** booking's own `resource_id` changes; a
**lesson** stays on the coach resource and its auto-held court row moves instead. The target is validated up
front (`_court_is_free`, excluding the booking's OWN rows via `_linked_booking_ids` so it can't block itself)
→ `COURT_NOT_AVAILABLE` rather than a bare `SLOT_TAKEN`. Court moves are single-booking only, never a series
(`COURT_MOVE_SINGLE_ONLY`). Omitting the key preserves the old behaviour exactly.
**A court move re-runs the MONEY guards a time move runs** — a COURT booking may not cross court SERVICES
(`COURT_SERVICE_CHANGED`) and a `membership_covered` booking re-runs the FULL entitlement against the TARGET
court (`COURT_NOT_COVERED`). A lesson's held court may move freely — a lesson is priced by its LESSON service.
[Why, and the None-normalisation that a short-circuit would skip](docs/specs/GOTCHAS.md#a-court-move-re-runs-the-money-guards-a-time-move-runs).
**Frontend: `CRMUI.rescheduleModal` is the ONE reschedule UI** (date/time + configured durations + court),
shared by client · coach · admin · home — it replaced four drifted forks, none of which could move a court.
Role differences are config: `canChangeCourt` is false for a member's LESSON (the court is club-allocated)
and true for their court hire and for all staff.

**`diary.booking.product_id` remembers WHICH service was booked.** A coach can sell several lesson services
(Private R400, Semi-private R250, Cardio R120) and pricing must use the exact one, never a `kind`-level
fallback (whose tie-break is `amount_minor ASC LIMIT 1` — the coach's CHEAPEST service). **If you add a column
here, add it to `_booking_dict`'s SELECT too** — it returns `None` otherwise and the fallback silently bites
again. `sc_gated_lesson_bills_the_booked_service` ·
[why](docs/specs/GOTCHAS.md#diarybookingproduct_id-remembers-which-service-was-booked).

**Lesson lifecycle — there is ONE flow, and no approval gate.** A lesson books exactly like a court:
it reserves coach ∩ court immediately, and the settlement mode alone decides `held` (online, awaiting
payment) vs `confirmed`. `iam.coach_profile.review_bookings` gates nothing; `accept`/`propose`/`decline`
and the `requested`/`proposed` statuses were **deleted 2026-07-29**. A coach who doesn't want a time
**reschedules or cancels** it — and a paid lesson cancelled by the club refunds itself. Full reasoning +
what must never be restored: [GOTCHAS.md § The lesson lifecycle](docs/specs/GOTCHAS.md#the-lesson-lifecycle).

**"Pay all" settlement orders — the wrapper OWNS its contents.** A settlement order stands in for N real
debts, snapshotted **immutably** on `billing."order".covered_order_ids` at creation; the child-side
`settled_by_order_id` is MUTABLE and must **never** be the only record. **Refunding a wrapper must UN-SETTLE
its children** (or the club loses the cash AND the receivable), and **changing a COVERED debt kills the
wrapper** (`void_order`/`discount_order` → `invalidate_live_settlement_for`). Guarded by
`sc_settlement_refund_restores_debt` + `sc_settlement_survives_reclaim` +
`sc_settlement_invalidated_when_a_debt_changes`; the 30-min-reclaim-vs-72h-retry race and the `surplus_minor`
report are in
**[UNIFIED-STATEMENT.md § As-built](docs/specs/UNIFIED-STATEMENT.md#pay-all-settlement-orders--the-wrapper-owns-its-contents)**.
**Unified client statement** (`billing/statement.py`): one debt = one `billing.order`, settled once. The account
page shows ONE reconciled "Your statement", grouped by category with tick-to-part-settle; admin void/write-off;
coach `coach_arrears` kept in **lockstep** with orders so commission accrues exactly once. Design:
`docs/specs/UNIFIED-STATEMENT.md`.

**The Money tab = ONE `Widgets.Earnings` (`frontend/js/widgets/earnings.js`) — a club-vs-coach P&L across
admin + coach, config-only (no fork).** **CLUB earnings = the DIRECT services it runs** (court/membership/pack,
100% club) **+ the COMMISSION taken from each coach**; drill coach → client → transaction → the shared record.
The coach app renders the SAME widget as the coach's OWN P&L ("You keep" wording — never other coaches or the
club roll-up). All of it off the ONE `_earnings_cte` (per-order coach attribution — lesson/class/pack → that
coach, court/membership → NULL = club) via `admin.repositories.revenue_club_overview` / `revenue_coach_pnl` /
`earnings_clients` / `earnings_transactions`. **Commission accrues to the coach on EVERY collection method**
(Yoco / invoice paylink / cash-EFT desk / 'pay-all' statement) through the ONE payment core — no method
short-changes a coach (monthly guard: `python -m scripts.reconcile_coach_commission`). Screen-by-screen
breakdown: [BUSINESS-RULES.md § 6](docs/specs/BUSINESS-RULES.md).

**Club↔coach settlement.** The coach's running `coach_ledger` balance surfaces in the coach P&L + the roll-up's
"Coach payouts due" (`billing.commission.settlement_overview`); a recorded **`coach_payout`**
(`record_coach_payout`, both directions + offset, idempotent on `ref_id=payout.id`) nets it, actioned from the
**Record payout** button on the coach P&L card. **Month-end sweep** (`billing.commission.run_month_end` →
`POST /api/cron/month-end`, `OPS_KEY`-guarded): accrues coach arrears + rent, then consolidates each client's
open orders into ONE numbered statement invoice + pay-link email (a client who owes nothing gets NO email),
idempotent per `(club,user,period)` via `billing.month_end_notice`. **The sweep is PER-CLIENT-TRANSACTIONAL,
TIME-BOXED and RESUMABLE** — the CRON ROUTE runs `month_end_client` in its own `session_scope()` per client and
stops at `max_seconds` (default 90, under gunicorn's 120s reaper) for the caller to loop; `run_month_end` is the
single-transaction form (one club, harness only). Fired by **`.github/workflows/month-end.yml`** on the **1st**
(billing the month just ended); it loops until `complete` and **FAILS THE JOB LOUDLY**. Why one big transaction
is not an option (gapless numbers + emails that don't roll back):
**[UNIFIED-STATEMENT.md § As-built](docs/specs/UNIFIED-STATEMENT.md#the-month-end-sweep-is-per-client-transactional-time-boxed-and-resumable)**.

**Client month-at-a-glance + the ONE month-aware 360.** `billing.me.activity_summary(month)` →
`GET /api/me/activity-summary`: sessions PLAYED + minutes + spend-by-service + billed/paid/outstanding + weekly
buckets. `get_client_360` takes `month=` and adds the per-service breakdown — the **month → client → service →
transaction** coach drill; the parallel `coach.get_client` reader was retired, so **every coach client view is a
view off the ONE composer**. `CRMUI.activityBlock / spendBlock / weekChart` = ONE shared renderer for the client
Home modules AND the Client 360 rollup. The client Home is Book(services) → Your sessions → Match-analysis →
a month-navigable Billing+Activity summary → Plan; **no emoji** (drawn line-glyphs).

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
  Ten-Fifty5 repo, like the analytics engine): when a gclid'd buyer PAYS, the `emit()` funnel's 4th forward
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
  images `/img/`), **Ten-Fifty5 in its own repo** (`frontend/blog/_posts/`, images `/blog/images/`, published via
  its own `build_blog.py`, commit `CLAUDE_CODE=1`; weekly coworker SEO-scan→post workflow). Full spec:
  **`docs/specs/MARKETING-ENGINE.md`**. NextPoint also has a Google Business Profile playbook (physical club →
  local map pack). Ten-Fifty5 is **Render-only for users** (Clerk auth + PayPal, no Wix) but retains dormant,
  DB-coupled Wix scaffolding — a decommission is scoped (DO NOT rush) in the Ten-Fifty5 repo's `docs/DE-WIX-DECOMMISSION.md`.

## Ten-Fifty5 embed — match analysis inside the members area (LIVE, private test)
A logged-in member opens **Ten-Fifty5** (AI match analysis / technique — the Ten-Fifty5 product; web at
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
- **The Ten-Fifty5 repo IS modified for this** (the ONE exception to "read-only reference" below): `auth_v2/verifier.py`
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

## Tech defaults (match Ten-Fifty5 so reuse is clean)
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
**The war stories live in [`docs/specs/GOTCHAS.md`](docs/specs/GOTCHAS.md) — 55 entries, moved out
verbatim. Below is the INDEX: the rule, and the `sc_…` scenario that pins it.** Follow the link before
you change the code an entry names — each one is a bug that reached production, and every one of them
looks like a harmless simplification until you read what it cost.

**The short ones, in full — no story needed:**
- **`api.nextpointtennis.com` is already live on the Ten-Fifty5 service** — do not break it. The new platform has its
  own API host; changing a Render custom domain can recreate a service. (The members-area **Ten-Fifty5 embed**
  now *deliberately* calls this API with federated NextPoint tokens — see the embed section.)
- **Never let an agent change DNS.** The Wix→Render SEO cutover is supervised by Tomo.
- **The booking API returns `{booking:{order_id,status}, checkout}`** — read `res.booking.order_id`, NOT
  `res.order_id` (that bug silently confirmed online bookings without redirecting).
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
- **`UI.clear(node)` must drop the `cf-loading` class** (it does, in `frontend/js/ui.js`) — `.cf-loading` paints
  a CSS `::before` spinner; emptying children without removing the class leaves the spinner over new content.
  Render results with `UI.clear(box)` before appending.
- **Free-tier cold starts → use timeouts, not infinite spinners.** `auth_client.js` puts a 70s timeout on every
  `apiFetch`. A GitHub Action (`.github/workflows/keep-warm.yml`) pings both services 07:00–21:59 SAST.
- **`marketing/` is NOT platform code** — local-only ad-ops notes, **gitignored as `/marketing/` since
  2026-07-22** (previously untracked-but-committable, one `git add -A` away from being published). The
  **leading slash is load-bearing**: a bare `marketing/` would also match the tracked public site at
  `frontend/marketing/`, silently ignoring any new page added there. Don't confuse the three: `marketing/`
  (ad-ops, ignored) · `frontend/marketing/` (the public site) · `marketing_crm/` (the CRM lane).
- **`.claude/` is ignored EXCEPT `skills/`** — same shape, same reason to be careful. It is written
  `.claude/*` + `!.claude/skills/`, **not** `.claude/`, because git cannot re-include anything inside a
  directory that is itself excluded. The skills (`marketing-manager`, `tennis-reel`) are shared team
  assets and must stay version-controlled; `settings.local.json` is this machine's grants and stays out.

**Everything else — the rule here, the reasoning in [GOTCHAS.md](docs/specs/GOTCHAS.md):**

**Booking & the diary** — [GOTCHAS.md#booking--the-diary](docs/specs/GOTCHAS.md#booking--the-diary)
- `booking_type` must match the resource, and `'class'` is NOT bookable via `/api/diary/bookings` — `sc_booking_type_must_match_resource`
- A posted `product_id` is VALIDATED before anything uses it — `sc_posted_service_must_be_real`
- ONE PERSON, ONE PLACE — the GiST constraint can't express it — `sc_one_coach_one_place_at_a_time` · `sc_member_second_concurrent_court_is_payg`
- ENTITLEMENT IS EVALUATED ON THE BOOKING'S DATE, NEVER `CURRENT_DATE` (2026-07-27) — `sc_membership_cannot_book_past_its_own_expiry`
- Capacity-sweep needs no cron — `sc_expired_void_is_recoverable`
- A repeated "Buy" must RE-OFFER the unpaid order, not mint a second debt — `sc_buy_click_never_mints_a_duplicate_debt`
- An abandoned purchase has no booking, so nothing ever swept it — `sc_abandoned_purchases_expire_by_themselves`
- A RENT coach bills his own clients — and books lessons, not courts in his own name — `sc_a_rent_coach_lesson_raises_no_club_charge`
- VOID MEANS CANCEL — an invoice void cancels its charges too (`cascade=False` = the re-issue path) — `sc_bulk_void_cancels_charges_not_just_the_document`
- ONE ACTIVE PRICE PER (service, duration) — a second row means the cheaper one silently wins — `sc_one_active_price_per_duration`
- AN INVOICE MUST RECONCILE after a late discount/write-off (`adjustments_minor`) — `sc_partial_payment_leaves_the_invoice_open`
- A DESK PAYMENT RECORDED IN ERROR CAN BE UNDONE — not a refund; the commission comes back too — `sc_a_desk_payment_recorded_in_error_can_be_undone`
- ONE COACH MONEY SCREEN — the P&L carries the settlement; a coach is settled on the month he WORKED (commission still only on money COLLECTED), and a payout credits the month it SETTLES — `sc_coach_earnings_carries_the_settlement`

**Courts, peak hours & equipment** — [GOTCHAS.md#courts-peak-hours--equipment](docs/specs/GOTCHAS.md#courts-peak-hours--equipment)
- THE COURT IS THE ONE PLACE TO SEE A COURT (2026-07-29)
- PEAK HOURS ARE PER COURT (2026-07-29) — `sc_peak_hours_can_differ_per_court`
- EQUIPMENT IS SCOPED TO A COURT SERVICE, AND THE COURT IS STILL CHARGED (2026-07-29) — `sc_equipment_court_is_charged_and_both_are_booked_out` · `sc_equipment_is_scoped_to_its_court_service`
- EQUIPMENT IS A SERVICE AND PAYS LIKE ONE — `sc_equipment_follows_its_own_payment_rule`

**The lesson lifecycle** — [GOTCHAS.md#the-lesson-lifecycle](docs/specs/GOTCHAS.md#the-lesson-lifecycle)
- THERE IS ONE LESSON FLOW (2026-07-29) — `sc_one_lesson_flow` · `sc_paying_is_the_acceptance`
- THE COACH IS TOLD, ONCE, ABOUT EVERY LESSON
- A PAID lesson cancelled BY THE CLUB refunds itself
- accept / propose / decline are GONE (deleted 2026-07-29 once production's queue was empty)
- A lesson email must state THIS booking's state, not the usual one

**Classes** — [GOTCHAS.md#classes](docs/specs/GOTCHAS.md#classes)
- A class name can NEVER break the class — enforced at THREE layers (2026-07-26) — `sc_class_name_cannot_break_the_class`
- A CLASS resolves its service through `diary.resource.product_id`, NEVER by joining on names
- A CLASS HAS THREE VERBS, AND CANCEL GIVES THE MONEY BACK (2026-07-29) — `sc_class_session_lifecycle`
- A WAITLIST PROMOTION CANNOT CONFIRM AN UNPAID CARD-ONLY SEAT — `sc_waitlist_promotion_into_a_cardonly_class_is_held`

**Memberships, the trial & entitlement caps** — [GOTCHAS.md#memberships-the-trial--entitlement-caps](docs/specs/GOTCHAS.md#memberships-the-trial--entitlement-caps)
- THE SIGNUP TRIAL IS A TIER-LEVEL FLAG, AND A TIER IS SEVERAL PRICES — `sc_signup_trial_is_a_tier_level_flag`
- THE TRIAL IS A MEMBERSHIP — it has no separate court rules — `sc_trial_obeys_the_same_court_rules_as_a_membership`
- `/api/me/plan` MUST REPORT THE CAP THE SERVER WILL ENFORCE (2026-07-29) — `sc_plan_reports_the_cap_the_server_will_enforce`
- MEMBERSHIP CAPS HAVE A CLUB-LEVEL FLOOR — the per-tier ones alone never reached the trial — `sc_club_default_caps_cover_every_membership`
- `membership_started` is emitted from `billing.membership.emit_membership_started`, NOT from the gateway — `sc_membership_started_emit`

**Pricing & payment rules** — [GOTCHAS.md#pricing--payment-rules](docs/specs/GOTCHAS.md#pricing--payment-rules)
- A service's `payment_modes` is enforced SERVER-SIDE per the EXACT `product_id`
- A DUPLICATE DURATION ON ONE SERVICE SILENTLY BILLS THE CHEAPER ROW (found live 2026-07-31)

**Invoicing & the month-end close** — [GOTCHAS.md#invoicing--the-month-end-close](docs/specs/GOTCHAS.md#invoicing--the-month-end-close)
- AN INVOICE COVERS ITS OWN MONTH, or no month can ever be closed — `sc_an_invoice_covers_its_own_month`
- BILL THE MONTH AFTER IT ENDS, and let a month swept early be closed — `sc_a_month_swept_early_can_still_be_closed`
- ONE PAYMENT IS ONE RECEIPT, however many lines it settles — `sc_one_payment_one_receipt`
- A PART PAYMENT SETTLES WHOLE LINES, oldest first — it never part-settles an order — `sc_partial_payment_leaves_the_invoice_open`

**Refunds & the Yoco gateway** — [GOTCHAS.md#refunds--the-yoco-gateway](docs/specs/GOTCHAS.md#refunds--the-yoco-gateway)
- ONE REFUND MODAL, AND IT OFFERS AN AMOUNT (2026-07-28) — `sc_partial_refund_reaches_yoco_as_a_partial`
- A REFUND'S IDEMPOTENCY KEY MUST NOT OUTLIVE A FAILED ATTEMPT (2026-07-28) — `sc_refund_retry_is_not_poisoned_by_the_idempotency_key`
- A REFUND NEEDS EVIDENCE OF A CARD PAYMENT, NOT A CHECKOUT (2026-07-28) — `sc_refund_refuses_an_order_never_paid_by_card`
- A REFUND MUST TARGET THE CHECKOUT THAT HOLDS THE MONEY, NOT THE FIRST ONE CREATED (2026-07-28) — `sc_refund_finds_the_checkout_that_holds_the_money`

**Money custody & the coach ledger** — [GOTCHAS.md#money-custody--the-coach-ledger](docs/specs/GOTCHAS.md#money-custody--the-coach-ledger)
- THE CLUB CAN ONLY RECEIVE YOCO AND EFT (2026-07-29) — `sc_only_yoco_and_eft_reach_the_club`
- THE COACH LEDGER'S DIRECTION FOLLOWS WHO HOLDS THE CASH (2026-07-28) — `sc_ledger_direction_follows_who_holds_the_cash`
- "PAID" IS NOT "IN THE BANK" — Money → Coach statement is the split
- CLUB EARNINGS AND THE COACH STATEMENT MUST AGREE ON WHERE THE MONEY IS (2026-07-31) — `sc_club_earnings_agrees_with_the_coach_statement`
- THE COLLECTED FIGURE MUST SAY WHAT IT WAS (2026-07-30) — `sc_settlement_says_what_the_money_was`
- A PACK SALE RESOLVES ITS COACH FROM THE WALLET (2026-07-30)
- THE COACH STATEMENT is the coach-side of a client invoice — `sc_coach_settlement_statement`

**Reads that lie** — [GOTCHAS.md#reads-that-lie](docs/specs/GOTCHAS.md#reads-that-lie)
- A SILENT ZERO IS A BUG, AND `try/except: return 0` IS NOT A GUARD (2026-07-31)
- `billing.me.activity_summary` buckets EVERYTHING by the SESSION's month

**Email & notifications** — [GOTCHAS.md#email--notifications](docs/specs/GOTCHAS.md#email--notifications)
- Transactional email = ONE confirm+receipt per purchase — `sc_confirmation_email_block` · `sc_email_payment_status_not_racy`

**Infrastructure & environment** — [GOTCHAS.md#infrastructure--environment](docs/specs/GOTCHAS.md#infrastructure--environment)
- Ten-Fifty5 embed — Render service names ≠ `render.yaml` `name:`
- A JS FILE THAT DOESN'T PARSE IS DEAD IN ITS ENTIRETY, AND PRESENTS AS A BROKEN LOGIN (2026-08-09) —
  guarded by `python -m scripts.check_frontend_js`. A page that loads and then makes ZERO API calls is
  a dead script, not an auth problem.

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
  (`.github/workflows/month-end.yml`) now fires on the **1st**, billing the month just ended, issuing each client's
  consolidated statement invoice + pay-link email; Admin → Setup → **Company & billing details** filled (bank
  details → EFT instructions on invoices); invoice PDF email attachment on (above).
- Volatile env/infra values: `docs/specs/ENV-STATUS.md`. **Session handover** (what to read, how we
  work, the gates): `BUILD_PROMPT.md` — rewritten 2026-08-02 from the old build-kickoff prompt.

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
`MEMORY.md` and the authoritative specs under `docs/specs/` (START at `README.md` → `GOTCHAS.md` →
`SYSTEM.md` → `BUSINESS-RULES.md` → `INVENTORY.md` → `OUTSTANDING.md`). `docs/` (`00`→`11`) are the original design docs;
`docs/11` = locked decisions + the Ten-Fifty5 reuse map.

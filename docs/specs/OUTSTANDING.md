# OUTSTANDING — what's left to do

The single source of truth for **remaining** work. Everything NOT here is built & live — see
[BUSINESS-RULES.md](BUSINESS-RULES.md) / [INVENTORY.md](INVENTORY.md). Dark-but-built features (env
switches, unwired endpoints) live in their own doc: **[FEATURE-FLAGS.md](FEATURE-FLAGS.md)**.

> **▶ NO CURRENT BUILD PHASE.** The platform is **LIVE on `https://nextpointtennis.com`** and
> feature-complete for launch. What remains is (A) config owed by Tomo, (B) code backlog, (C) owner
> decisions, (D) hardening, and (E) two large well-specced roadmaps (Admin Phase 2 + CRM Missions).
> **Nothing below is launch-blocking.** Gate baseline: **`python -m scripts.test_all` → booking 263 /
> billing 439 / statement 64** (2026-07-23).
>
> **Klaviyo, 2026-07-22 — `membership_started` never fired** (wired to a gateway branch nothing produces);
> **fixed in code + backfill RUN on prod** (12 members corrected, no emails sent). `KLAVIYO-MASTER-PLAN.md`
> §7f/§7g. Two follow-ups remain:
> - ~~**Owner decision:** all 12 active memberships are `provider='manual'`…~~ ✅ **RESOLVED — option (a),
>   2026-07-22.** `admin.grant_membership` now emits too (`source='admin_grant'`; extensions tagged
>   `is_renewal=true` so conversion measurement can filter them). The backfill is no longer a recurring
>   chore. Trial + Wix import stay excluded. (§7g)
> - **Builder, not Code:** C1 and the converter-guard are now unblocked — bind to the **API-source** metrics
>   `SzgJKC` (`lesson_completed`) and `WRb7TK` (`membership_started`), never the MCP test twins.

> Per-sprint changelog is NOT kept here anymore — it lives in git history + the memory index
> (`.claude/.../MEMORY.md`). This file is the forward-looking backlog only.

---

## A. Config — owed by Tomo (flips dark features → live; no code)
See **[FEATURE-FLAGS.md](FEATURE-FLAGS.md)** for the full switch-on detail of each.

**P1**
- [x] ~~**`OPS_KEY` GitHub repository secret**~~ — **DONE 2026-07-18.** Set; `.github/workflows/month-end.yml`
      now fires `POST /api/cron/month-end` on the **25th** (moved off the 1st — the club billing day).
- [ ] **Google Ads scheduled CSV upload** — set `GOOGLE_ADS_FEED_USER`/`PASS`, then schedule the daily
      upload (Uploads → Schedules) pointed at `/feeds/google-ads/offline-conversions.csv`. The recorder half
      is already live. (`GOOGLE-ADS-PLAN.md`.)
- [ ] **Complete Google advertiser verification** (in progress). (`GOOGLE-ADS-PLAN.md`.)

**CARRY-OVER as of 2026-07-23 — the ONLY things known to be open.** Everything else in section B was
closed out in the 2026-07-22/23 sweep (see `README.md`'s dated entry).

- [ ] **Klaviyo console work** (3 items, all in the Klaviyo UI, no code — full detail in
      `KLAVIYO-MASTER-PLAN.md` §7e/§8):
      **(a)** flow **`WSWr2C`** ("Court feedback") has `trigger_filter = null` — add **`booking_type` equals
      `court`** or it fires on lessons and classes too. It is still **Draft**, so nothing is misfiring today;
      this must happen *before* it goes live.
      **(b)** build the **C1 post-lesson email** in Flow Builder, triggered on the REAL `lesson_completed`
      metric **`SzgJKC`** — NOT the MCP test twin `RfeMhj`. **Verify the trigger via `get_flow`
      `definition.triggers` BEFORE saving**: triggers cannot be changed after save (one flow already had to be
      deleted and rebuilt for this).
      **(c)** re-check the Unconverted-trial segment **`XxUZCt`** has shrunk now the `membership_started`
      backfill has run, *before* sending the January offer — otherwise it aims "you haven't converted" at
      paying members.
- [ ] **Peak pricing is LOST on reschedule.** `billing.orders.reprice_booking_order` takes
      `duration_minutes` only — no start time — and selects `p2.amount_minor`, which is the **BASE
      (off-peak) amount**. It never reads `peak_amount_minor` and never calls `in_peak_window`, so a
      reschedule re-prices at base regardless of the new time: **moving a booking INTO a peak window
      under-charges it.** (It does not "keep the original band" — peak is dropped entirely.) The fix is to
      thread the new `starts_at` through and price the way `diary.pricing.price_for(at_local=…)` does at
      CREATE time, which is correct and harness-covered. **Dormant**: no club has peak pricing configured
      (`peak_amount_minor` is NULL everywhere), so it cannot bite today — but this MUST be fixed before peak
      pricing is enabled anywhere.
- [ ] **3 abandoned-checkout orders** were held back by `void_orphaned_orders.py`'s 7-day age floor on
      2026-07-23 (created 18-22 July). Cosmetic only — Yoco confirmed all unpaid. Re-run the script whenever;
      they will clear once past 7 days.

**P2**
- [ ] **Ten-Fifty5 embed → all members** — clear `TF5_EMBED_ALLOW_EMAILS` (currently one test email; others
      see a "Coming soon" card). Depends on the TF5-side env staying set.
- [ ] **`KLAVIYO_API_KEY`** → CRM lifecycle/marketing flows go live (event feed already emits). Then schedule
      the two manual cohort scripts (`scripts/klaviyo_reactivation.py`, `scripts/klaviyo_trial_cohort.py`).
- [ ] **`S3_BUCKET` + AWS keys** → coach photo uploads (coaches paste a URL until then).
- [ ] **SES follow-ups:** ~~SendRawEmail dependency~~ **RESOLVED 2026-07-18** — the sending key carries
      `AmazonSESFullAccess` (`ses:*`, incl. `ses:SendRawEmail`), so `EMAIL_INVOICE_PDF_ENABLED=1` is **ON**
      (invoices email the PDF attached). Remaining: (a) optionally flip `EMAIL_ICS_ENABLED=1` (permission now
      exists — the booking `.ics` attachment; in-app "Add to calendar" works regardless); (b) verify
      `nextpointtennis.com` DKIM in the CourtFlow AWS account + move `SES_SENDER` off the interim ten-fifty5
      account. (`SES-SETUP.md`.)
- [ ] **Revert Ads bidding** Max Clicks → Max Conversions after ~15–30 conversions accrue; set up a Google
      Business Profile. (`GOOGLE-ADS-PLAN.md`.)

**P3**
- [ ] Confirm the **Yoco fee-accounting** assumption in practice (fees = owner's account, recovered via
      commission, not deducted from coach splits).
- [ ] Post-cutover data tidy from the Wix import: fix Allon's pack (10×90 not 10×60); create the "Monthly
      Adult – Squad" class; ensure Colbert accepts his coach invite. (`CUTOVER-PROGRESS.md`.)

## B. Code — backlog (real deferred functionality)

**P1 (correctness / launch-adjacent)** — *empty. The two items that lived here are DONE:*
- [x] **Orphaned `awaiting_payment` order cleanup** — DONE. `release_expired_holds` now calls
      `_void_orders_with_no_live_bookings`, voiding the abandoned order once EVERY booking on it is dead.
      Reconcile can still re-open a purely hold-expiry void (`order_void_is_recoverable`), so a late
      payment is never stranded. Backlog cleared with `scripts/void_orphaned_orders.py` (which gained a
      second pass for abandoned checkouts that never had a booking — memberships, packs, class seats).
- [x] **A scheduler for reminders / reconcile / membership-refill** — DONE, all on **GitHub Actions**, not
      Render crons: `reminders.yml` (hourly), `membership-refill.yml` (daily), `reconcile-payments.yml`
      (hourly, 72h lookback) + `reconcile-deep.yml` (weekly, 100-day — the safety net for anything that
      ages out of 72h), `month-end.yml` (25th), `marketing-digest.yml` (daily), `keep-warm.yml`. The four
      `render.yaml` crons stay commented out **by design** — add a workflow, never uncomment one.

**P2 (valuable)**
- [x] ~~**Diary timeline editing port**~~ — **DONE / MOOT 2026-07-18.** The classic console (`/admin-classic` +
      `admin.html`/`admin.js`) was DELETED (301→`/admin`). Its editing actions now live in the new admin:
      **walk-in** (Book a client → guest name), **desk-pay** (transaction record), and **block time** (new Diary
      "Block time" action → `POST /api/diary/time-off`). Only the drag-to-create/drag-to-move *gesture* is gone.
- [x] ~~**Block-time — show + remove in the new admin Diary**~~ — **DONE 2026-07-18.** The master feed now
      emits `time_off` as `booking_type='block'` events (`is_time_off`, overlap-filtered), so blocks render on
      the diary grid/agenda with a hatched "Blocked" style; tapping one → confirm → `DELETE /api/diary/time-off/
      <id>` (new staff route; owner=any resource, coach=own only) → the window frees again. Create + show +
      enforce + remove all proven. Block-time is now fully first-class in the new admin.
- [ ] **Client 360 month navigation** — the client Home has a month pager but the person-360 record is
      current-month only; add month-nav + promote a shared `UI.monthNav` (Home/Insights/360 share ONE pager).
- [ ] **Coach-lane aliases for holdings/arrears write routes** — discount / wallet adjust-expire / payout sit
      on the **admin** blueprint; add coach-lane aliases guarded to the coach's own clients.
- [x] ~~**Re-home a "Record payout" action**~~ — **DONE 2026-07-18** (b33540b): the coach P&L card now shows
      "Net balance with the club" + an admin-only **Record payout** button (`Widgets.Earnings` `cfg.onRecordPayout`
      → `recordPayoutModal` → `AdminAPI.recordCoachPayout`), prefilled to settle the balance; `revenue_coach_pnl`
      returns `ledger_balance_minor`. Posts the netting `coach_ledger` entry (fixture-proven: R700 → R0).
- [ ] **Guest fee (Phase 2)** — charge a court guest a fixed fee collected **FROM THE GUEST** (not the
      member's account). Guests are non-billable today. Needs a guest-fee price/config + a guest-facing
      collection path (at-court or a guest payment link), kept off the member's statement.
- [ ] **Membership upgrades / downgrades** — mid-term tier change (proration, effective date, credit/refund).
      Needs a proper spec before building.
- [ ] **Bundle/arrears edges** — expiry policy for unused pack minutes/credits (refund/transfer?); an optional
      "too-late cancellation forfeits the credit."
- [ ] **Reschedule UX polish** — `PATCH /api/diary/bookings/<id>` exists; make member/admin reschedule flows
      smooth + policy-guarded.
- [ ] **Marketing contact-form delivery** — SES is live; confirm the web-service contact form is wired to the
      live sender (it also logs to Render as a fallback).

**P3 (edge / cleanup)**
- [ ] **Booking-flow edge backlog** (unreachable from today's UI or self-healing): **L5** null-order held-court
      fallback link · **L7** multi-player gated lesson under-bills on accept (`accept_booking` passes
      `parties=[]`) · **L8** `lesson_withdrawn` notification to the coach · **M8** court collapse-to-one-line
      guard for 2+ member parties · **M3 tail** gated-lesson settlement/window ordering · an on-behalf
      class-pack draw harness assertion.
- [ ] **My Bookings cancel-path clarity** — confirm the client SPA surfaces token credit-back / refund clearly.
- [ ] **Self-serve role transitions** — e.g. a dependent aging out at 18 into their own login.
- [ ] **Drop `coach_arrears` / `account_ledger` internal tables** — pure cosmetic cleanup now that
      `billing.order` is the single source (kept only in lockstep). Not blocking. (`UNIFIED-STATEMENT.md`.)
- [ ] **Platform / super-admin cross-club cockpit** — for `platform_admin`; low priority while single-club
      (`scope_clause` already supports it). Note: `analytics/`'s `/overview.html` already has a multi-club
      filter (FEATURE-FLAGS B2).
- [ ] **Retire or wire the CRM `backoffice` cockpit lane** (`/api/admin/cockpit/*`) — half-built, no UI, and
      its live half overlaps the shipped `insights/`+`analytics/` lanes. Confirm dead, then delete, or finish +
      surface it. (FEATURE-FLAGS B1/D.)
- [ ] **Marketing site polish:** Lighthouse/LCP on-device verification (≥90 perf, LCP<2.5s — never measured);
      swap low-res `coach-ross.webp`; swap the faux CSS cockpit mock for a real `/portal` screenshot; swap two
      Unsplash stock feature images for real club shots.

## C. Owner decisions (parked pending Tomo)
- [ ] **Coach pay for R0 (membership-covered) lessons** — a covered lesson settles at R0, so there's no base
      for commission; how/whether the coach is paid is an open owner call.

## D. Hardening (later phases)
- [ ] **RLS** (row-level security) on domain tables — Phase 8; today multi-tenancy is a query discipline.
- [ ] **Automated test runner** — no pytest suite; consider formalising the scratch-DB scenario scripts.
- [ ] **VAT/tax** registration + invoice formatting (commission base treated ex-VAT today).
- [ ] **Consent/PII review** for any new email/notification payloads (no minor PII in marketing sends).
- [ ] **Dunning automation** — only the aging VIEW shipped; automated unpaid-statement reminders/escalation
      remain.

## E. Large roadmaps — specced, awaiting owner priority (not design)
These are whole programmes of work with their own specs — pull items into A–D as they're prioritised.
- **[ADMIN-PHASE2.md](ADMIN-PHASE2.md)** — the "world-class admin portal" backlog (~40 features on 5 reusable
  primitives + one new `automation.rule` table). **P1 flagship shipped** (insights lane: court-utilisation
  heatmap + sales-by-day + native Overview KPI board). Next highest-leverage: **#7 rule-builder console**
  (unlocks win-back, dunning, welcome journeys, alerts as config), then at-risk detection / alert centre /
  acquisition funnel / line-of-business scorecard.
- **[CLIENT-360-CRM-PLAN.md](CLIENT-360-CRM-PLAN.md)** — CRM Mission 1 remaining slices: **1.1** minimum-data
  gate (admin name+email DONE; **phone + first-booking-checkout gate still open**), `UNIQUE(lower(email))`
  after de-dup, unify the two `marketing_opt_in` flags; **1.2** true Client-360 (demographics/consent + unified
  activity timeline); **1.3** interaction capture (`account_created`/`payment_succeeded`/`login` events);
  **1.4** NPS & surveys (**DONE** — the gated `/feedback` page writes `core.nps_response` via
  `GET/POST /api/feedback` and routes a happy score to the Google review link; a post-lesson prompt is the
  remaining nice-to-have); **1.5** preferences model (`iam.preference`). Then Mission 2 (marketing engine: Klaviyo activation,
  segmentation, churn/fill scoring, WhatsApp/SMS). **§6 shared-code convergence** (extract the drifted
  CRM/analytics/beacon/SES forks into a pinned package) is a cross-cutting decision already made.

## How to pick up (next session)
1. Read [README.md](README.md) → SYSTEM → BUSINESS-RULES → INVENTORY → this file → FEATURE-FLAGS.
2. Pick an item. Deep design for most lives in the role specs + the `01`/`02` decision docs + the two §E specs.
3. Build in a worktree, verify (`py_compile`, `node --check`, `python -m db` twice, `python -m scripts.test_all`),
   merge to `master`, confirm the Render deploy. Keep every new table `club_id`-scoped + idempotent.

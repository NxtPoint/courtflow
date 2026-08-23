# OUTSTANDING — what's left to do

**THE single place for remaining work.** Everything not here is built and live — see
[BUSINESS-RULES.md](BUSINESS-RULES.md) / [INVENTORY.md](INVENTORY.md). Dark-but-built features live
in [FEATURE-FLAGS.md](FEATURE-FLAGS.md). Resolved items are NOT kept here: they are in git history
and the dated log in [README.md](README.md). This file is the forward-looking backlog only.

> **▶ NO CURRENT BUILD PHASE.** The platform is **LIVE on `https://nextpointtennis.com`** and
> feature-complete. What remains is (A) config owed by Tomo, (B) code backlog, (C) owner decisions,
> (D) hardening, (E) two specced roadmaps. **Nothing below is launch-blocking.**
> Gate baseline lives in **CLAUDE.md** and nowhere else (`scripts.audit_docs` fails any doc that
> disagrees).

---

## ⭐ NEXT SESSION — switch Find a Game's MONEY on, then market it

Everything for this is built, scenario-covered and configured. What is left is **clicking**, in this
order. Do not reorder 2 and 3: the second switch changes what members pay.

**1 · Finish the browser walkthrough (the only real gap).** The harnesses call Python directly —
never HTTP, never DOM — and *five of this lane's bugs were findable only in a browser*. Open, join
and chat are done (2026-08-11/12, Tomo + Tshepo). Still never clicked:
   - **enter a result → the other player confirms it** (check the reporter is NOT offered Confirm on
     their own claim)
   - **would-you-play-again** (check it is never visible to its subject — use a DOUBLES game so a
     third player is rated too)
   - **leave** a game · **invite** a non-member → accept via `join.html` · save the **level quiz**
     (Tomo's own profile still reads *level: Not set*, so that write has never completed)

**2 · Prove the money with the switch still OFF.** Read [TESTING.md](TESTING.md) §5b. Each must
reconcile in all three reads: the client statement, the Client-360 fold
(`Billed − Discount − Written-off = Invoiced = Paid + Outstanding + Refunded`) and Money → Club
earnings.

**3 · Flip `seat_rule_enforced`** (Admin → Setup → **Community & games**). Tell members first.
Rollback is the same switch — flipping it off restores the previous behaviour exactly
(`sc_seat_rule_off_changes_nothing`). Seat debts already raised are real orders and stay; void them
if you don't want them.

**4 · Then market it.** `/marketing-manager` covers both brands. The feature's story is
*"never play alone"* — but see §A **Google Ads** below first: the Ads account optimises toward
`start_free_week` and `booking`, and joining a game creates neither, so spend pointed at it would
look like it converts nothing until the offline-conversion loop is running.

**The rules as they now stand** (all live, all scenario-covered — [COMMUNITY-ENGINE.md](COMMUNITY-ENGINE.md)):

| | Booker | Guest / joiner |
|---|---|---|
| **Find a Game** (spare seat published) | a share | a share; unfilled seats collapse; court holds until every online seat settles |
| **Book a court** (named guests) | the court's **normal price** | a share **only** behind a membership-covered booker; nothing ever holds the court |
| **Trial member** | free off-peak, **charged at peak** | — |

---

## A. Config — owed by Tomo (flips dark features → live; no code)
See **[FEATURE-FLAGS.md](FEATURE-FLAGS.md)** for the full switch-on detail of each.

**P1**
- [ ] ⭐ **COMMUNITY — the switch-on.** Fully specified at the top of this file under
      **NEXT SESSION**; not repeated here. Detail: [FEATURE-FLAGS.md](FEATURE-FLAGS.md) §A-bis ·
      walkthrough: [TESTING.md](TESTING.md) §5b · design: [COMMUNITY-ENGINE.md](COMMUNITY-ENGINE.md).
- [ ] **Google Ads scheduled CSV upload — ONLY the Google-side schedule is left.** ~~set
      `GOOGLE_ADS_FEED_USER`/`PASS`~~ ✅ **the env is SET and the feed is ARMED** (verified live
      2026-08-12: `GET https://courtflow-api.onrender.com/feeds/google-ads/offline-conversions.csv`
      returns **401**, i.e. waiting for Google's Basic auth — it would 404 if still dark). Remaining:
      Google Ads → Goals → Conversions → **Uploads → Schedules → New schedule**, Source **HTTPS**,
      that URL, Auth **HTTP Basic**, Frequency **Daily**. Note the feed lives on the **api** host —
      `nextpointtennis.com` 404s it, which is correct, not a fault. (`GOOGLE-ADS-PLAN.md`.)
- [ ] **Complete Google advertiser verification** (in progress). (`GOOGLE-ADS-PLAN.md`.)

**CARRY-OVER as of 2026-07-26 — the ONLY things known to be open.** The first real month-end ran on the
25th (33 clients, R41,170). The 2026-07-25/26 follow-up sweep is closed: **invoice lines are now itemised**
(date + duration + service + coach for a lesson, court for court hire); **month-end never sends a bare
"pay online" reminder for an owed balance** (a line-less order is synthesised; an already-invoiced balance
re-sends the real PDF); **refund requests are decided on the transaction record** (the queue is an inbox,
the full-refund amount bug is fixed, a direct refund closes the request, and a void that still holds cash
stays visible); and **a class name can never break the class again** (durable-link resolution + a DB trigger
mirroring product→resource name + boot heal). See `README.md`'s dated entries.

**CLOSED 2026-07-27 → 29 — the live-use hardening run.** Five revenue leaks reported from real use, then
the refund, lesson and class lifecycles. All scenario-guarded, each verified by re-breaking the fix:
- **Five revenue leaks (27th).** Entitlement was judged on `CURRENT_DATE` not the booking's date (book past
  your own expiry → R0 **permanently**, all memberships not just trials) · no one-person-one-place rule (the
  GiST constraint is per-*resource*, so a coach could hold a court AND teach AND run a class at 09:00) ·
  equipment hard-coded `at_court` on covered courts (an uncollectable debt in a card-only club) · the caps
  never reached the price-less trial, **and a NULL cap wiped a paid tier's** · a waitlist promotion
  confirmed an unpaid card-only seat.
- **Refunds (28th).** Four distinct failure modes that all presented as *"insufficient funds"*: a **frozen
  idempotency key** (`int(None or 0)` → one fixed key forever, so every retry replayed the first failure) ·
  refunding the **oldest** checkout rather than the one holding the money · refunding an order **never paid
  by card** · and the genuine empty-balance case. Plus **partial refunds became reachable** — supported the
  whole way down but no UI ever sent an amount.
- **The coach ledger's direction (28th)** — off-platform collections posted `+coach_net` as if the club held
  the cash, so the balance was **wrong by the whole gross every time** and told the owner to pay a coach who
  was holding the club's money. Fixed forward; production corrected 29th (2 coaches, R14,800 net).
- **One lesson flow (29th)** — the approval gate deleted (it made a card-only coach unbookable and let two
  clients hold one slot), the coach now told once via `lesson_booked`, a club-cancelled paid lesson refunds.
- **The class lifecycle (29th)** — `reschedule_session` (the verb that never existed), `class_booked` to the
  coach, and **cancelling a class now refunds its paid seats** (it voided, which no-ops on a paid order, so
  players lost seat *and* money under an email promising a refund).
- **Per-court peak windows + the court as the one place to see a court (29th)**, equipment scoped to its
  court service, the signup trial fixed as a **tier**-level flag, and `/api/me/plan` reporting the
  **effective** cap the server will enforce.

**⚠ CONFIG STILL OWED BY TOMO — the code above is INERT until set** (see §A):
- Admin → Settings → "What a membership includes (per day)": **1 booking / 90 minutes** (that IS the
  owner's rule — `default_max_covered_per_day=1` + `default_max_covered_minutes=90`).
- Setup → Equipment hire: tick each item's **payment options** (an item with none resolvable is now
  refused rather than silently billed at-court).

### OPEN AS OF 2026-08-02 — from the live-screen review

- [ ] **ROOT-CAUSE `admin_home`'s failing block.** Home reported `refund_requests_error` while the
      Refund-requests SECTION worked — a query in `admin_home` was aborting the transaction and every
      later block returned its own zero (so the People counts 0/0/0 were probably false too). Each
      block is now savepointed **and logs its own name**, so the next deploy names the culprit in the
      Render logs. **Check the logs and fix the actual query.** The symptom is gone; the cause is not.
### OPEN AS OF 2026-08-10 — from the invoicing/settlement re-engineering

- [ ] **But confirm WHICH free they are — it decides who bills their clients.** "Free" settles what
      the coach owes the club; it does not say whether the CLUB raises the client's charge:
      **`commission` + no rule** → the club bills the client and keeps 0% (the coach is paid through
      the club). **`rent` + R0** → the coach bills their own clients directly, and a lesson they book
      against themselves raises **no club charge at all**.
      Getting this backwards is the Ross/Terkaa story: four rent coaches on the commission model
      accumulated **R68,000 of phantom "outstanding"** against clients the club was never going to
      bill. Both settings are "free"; only one is right per coach.
      Check all three in one read-only table: `python -m scripts.set_coach_billing_model` (no args).
- [ ] **Label the remaining unlabelled coach payouts.** A payout credits the month it SETTLES
      (`coach_payout.period_label`); an unlabelled one falls back to the day the cash moved, so
      July's commission paid in August credits **August** and July still reads as owing — one click
      from paying a coach twice. The Record-payout modal now asks (prefilled from the month on the
      card), so this is only for payouts recorded before 2026-08-10. Audit and fix per coach:
      `python -m scripts.tag_coach_payout --coach "<name>"` lists them and flags the unlabelled.
      **Allon is done** (both his are on 2026-07).
- [ ] **Check Allon's June and August.** July settles to R0 once both payouts are labelled, but his
      all-time balance was R13,800 against R9,607 of July — the remainder is other months and was
      never examined.
- [ ] **Two duplicate "Tomo" coach accounts** showed in the coach list. Harmless today (neither
      carries money), but they will confuse any per-coach report. Merge or deactivate one.
- [ ] **A coach's P&L shows nothing for work already BOOKED but not yet delivered.** "Owed" counts
      delivered-and-unpaid only, so a coach with a full diary next month reads as having no pipeline.
      Not a bug — the settlement rule is deliberate (§D7, commission only on money COLLECTED) — but a
      projected line off future confirmed bookings would answer the question coaches actually ask.
      Wants a decision on where it sits before it is built: it must never be mistaken for money owed.

- [ ] **Trials on the LEGACY grant keep the OLD rules.** 38 active as of 2026-08-22 (no linked price
      → free at ANY time, including peak, and no caps). They are 7-day grants so they clear within a
      week; only NEW signups pick up the "Trail" tier. Nothing to do unless one is long-lived —
      `python -m scripts.audit_trials` (read-only; `--cancel-flagged` reverts wrong ones to PAYG).
- [ ] **Set the CLUB-WIDE peak windows** (Setup → Club profile & payments → Peak court hours). The
      seven hard courts each carry the right two windows (Mon–Thu 17:00–19:00 + Sat 08:00–12:00), but
      the CLUB default is still the old single window — so a court added later inherits Mon–Thu only,
      silently missing Saturday. Add both windows there and the trap is gone. Clay correctly has no
      peak PRICE, so its inherited window charges nothing (verified 2026-08-22).
      Check any time with `python -m scripts.audit_peak_and_trial --club "NextPoint Tennis"`, which
      reports what the RESOLVER decides — window AND whether a peak price exists to apply.
- [ ] **Rename the trial tier "Trail" → "Trial".** Cosmetic; it is not member-visible on Home (the
      client shows "7-day trial — N days left"), but it shows in admin and on the person record.
- [ ] **Family Plan is the odd one out:** 120 min and **NO CAP** on distinct courts per day, where the
      other four tiers are 90 / 1 / 1. Deliberate for a family with several kids, or an oversight?
      No-cap on courts-per-day is the exact leak the entitlement caps exist to close.
- [ ] **~1,000 Wix imports show as raw email addresses** in People (no first/last name), so the list
      sorts and reads by email. Cosmetic, but it makes the roster hard to use.
- [ ] **FINISH THE PAGE-BY-PAGE REVIEW.** Covered 2026-07-31: Home · Refund requests · Coach statement
      (summary + a coach) · Club earnings · People · Setup (menu, club profile, memberships, services).
      Covered 2026-08-08/10: **Club earnings / the coach P&L** (collapsed with Coach statement into ONE
      card) · the **transaction record** (Un-receipt added) · the **client account** (now month-by-month)
      · the **Record-payout modal**. **Not yet reviewed: Diary · Overview · the rest of Setup · the
      whole coach app · the rest of the client app · mobile widths.**
      **This is the highest-yield activity in the backlog and the evidence is now overwhelming:** every
      bug it has found was invisible in the code and passing its own tests — a `try/except: return 0`
      reporting zeros, a coach's cash called "banked" while he held it, a R0 price beside a R600 one,
      and a payout modal that never sent the month it settled. Do it with the console open, against
      live data, one screen at a time.
- [ ] **Bring the MONTH ROWS into the admin People record.** The member's own account now reads month
      by month (billed / invoiced / paid / outstanding per month, each month payable on its own), but
      People → a client still shows the flat invoice list. Two renderings of one capability is exactly
      what the golden rule forbids; the month view should be the shared widget with admin adding staff
      actions. **View as member** (People → a client → the button) closes the gap for now by showing
      the member's real screen, but it is a workaround, not the fix.

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

**P1 (correctness / launch-adjacent)** — *empty.*

**P2 (valuable)**
- [ ] **Client 360 month navigation** — the client Home has a month pager but the person-360 record is
      current-month only; add month-nav + promote a shared `UI.monthNav` (Home/Insights/360 share ONE pager).
- [ ] **Coach-lane aliases for holdings/arrears write routes** — discount / wallet adjust-expire / payout sit
      on the **admin** blueprint; add coach-lane aliases guarded to the coach's own clients.
- [ ] **Guest fee (Phase 2)** — charge a court guest a fixed fee collected **FROM THE GUEST** (not the
      member's account). Guests are non-billable today. Needs a guest-fee price/config + a guest-facing
      collection path (at-court or a guest payment link), kept off the member's statement.
- [ ] **Membership upgrades / downgrades** — mid-term tier change (proration, effective date, credit/refund).
      Needs a proper spec before building.
- [ ] **Bundle/arrears edges** — expiry policy for unused pack minutes/credits (refund/transfer?); an optional
      "too-late cancellation forfeits the credit."
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
1. Read `CLAUDE.md` first (operating guide + the gate baseline), then the **NEXT SESSION** block at the
   top of this file. For depth: [README.md](README.md) -> SYSTEM -> BUSINESS-RULES -> INVENTORY ->
   FEATURE-FLAGS.
2. Pick an item. Deep design lives in the role specs, the `01`/`02` decision docs and the two roadmap specs in section E.
3. Verify before merging: `py_compile`, `python -m scripts.check_frontend_js`, `python -m db` TWICE,
   `python -m scripts.test_all`, `python -m scripts.audit_docs --strict`. Keep every new table
   `club_id`-scoped and idempotent.
4. **Run the gates through `.venv\Scripts\python.exe`, not bare `python`** — bare python on the dev
   box is 3.14 with the requirements installed globally, so it passes cheerfully while proving the
   wrong interpreter (prod pins 3.12.3).
5. **Another agent is often working in this repo at the same time.** Stage by explicit path, never
   `git add -A`, and re-read `git log` rather than trusting a snapshot from earlier in the session.

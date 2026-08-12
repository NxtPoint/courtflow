# OUTSTANDING — what's left to do

The single source of truth for **remaining** work. Everything NOT here is built & live — see
[BUSINESS-RULES.md](BUSINESS-RULES.md) / [INVENTORY.md](INVENTORY.md). Dark-but-built features (env
switches, unwired endpoints) live in their own doc: **[FEATURE-FLAGS.md](FEATURE-FLAGS.md)**.

> **▶ NO CURRENT BUILD PHASE.** The platform is **LIVE on `https://nextpointtennis.com`** and
> feature-complete for launch. What remains is (A) config owed by Tomo, (B) code backlog, (C) owner
> decisions, (D) hardening, and (E) two large well-specced roadmaps (Admin Phase 2 + CRM Missions).
> **Nothing below is launch-blocking.** Gate baseline: see **CLAUDE.md**, which is the single place
> the current numbers are written (`scripts.audit_docs` fails any doc that claims a different one).
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
      fires `POST /api/cron/month-end`. **Moved to the 1st on 2026-08-08**, billing the month just
      ENDED: on the 25th every invoice was issued with days of the month still to run, so the client
      saw one number on the invoice and a larger one online days later, and no month could be closed.
- [ ] ⭐ **COMMUNITY / FIND A GAME — switch it on (2 flags, in order).** The whole lane is built, tested
      and DARK. Admin → Setup → **Community & games**:
      1. **Community features** first, on its own — members get Find a Game, open games, invitations and
         match chat. **Nobody's bill changes.** Zero-risk, and it is the half members will love.
      2. Check **What one player pays** — the screen shows the actual rands per duration (R50/80/110/140
         at the default 50% rounded up to R10). Note two payers settle R160 on a R150 court (rounding),
         and doubles with four non-members pays four shares, i.e. more than one court fee.
      3. **Tell the members** — the second switch changes what they pay.
      4. **Charge for every seat**. Then watch Setup → Games & invitations → *Games* → the **owed** column.
      Rollback is the same switch: flipping it off restores the previous booking behaviour exactly
      (`sc_seat_rule_off_changes_nothing`). Existing seat debts are real orders and stay — void them if
      you don't want them. Detail: [FEATURE-FLAGS.md](FEATURE-FLAGS.md) §A-bis · walkthrough:
      [TESTING.md](TESTING.md) §5b · design: [COMMUNITY-ENGINE.md](COMMUNITY-ENGINE.md).
- [ ] ⭐ **COMMUNITY — prove the MONEY before switching the money on (carried over, 2026-08-10).** The
      social half is live and being tested with real members; `seat_rule_enforced` is still OFF, and the
      seat rule has been proved only by the harness, which never speaks HTTP and never renders DOM. The
      order that matters: (a) two members join a game and **nobody is billed**; (b) a non-member joins
      and **one share** appears as a real order on the client statement; (c) two PAYG seats — the court
      stays `held` until BOTH settle; (d) an unfilled seat **collapses onto the holder** at the cutoff;
      (e) a refund **restores the split**. Each must reconcile in all three reads it now touches — the
      client statement, the Client-360 fold (`Billed − Discount − Written-off = Invoiced = Paid +
      Outstanding`) and Money → Club earnings. ~~**Two money scenarios are still owed**~~ ✅ **BOTH
      EXIST (verified 2026-08-12)** — `sc_refunding_a_seat_restores_the_split` and
      `sc_a_collapsed_seat_respects_the_courts_payment_modes`. This item is now purely the
      REAL-BROWSER walkthrough (a)–(e); the harness half is done, so nothing here blocks the switch
      except doing it with actual members.
- [ ] **COMMUNITY — the WRITE paths, in a real browser, with a real second person.** Five of this
      lane's bugs were findable only that way, so green gates say nothing here. **This is the single
      biggest untested surface in the platform.**
      - [x] **Open a game** — ✅ done 2026-08-11 (Tomo + Tshepo, live app, worked first time). The
            first browser-exercised write in this lane.
      - [ ] **Join** a game as the second player · **leave** it again · **chat** · **invite** a
            non-member and accept via `join.html` · save the **level quiz**.
      - [ ] **Enter a result, and have the other player confirm it** (wired 2026-08-12, never clicked)
            — check the reporter is NOT offered a Confirm button on their own claim.
      - [ ] **Would-you-play-again** (wired 2026-08-12, never clicked) — check the answer is never
            visible to its subject, in a **doubles** game where a third player is also rated.
- [x] ~~**COMMUNITY — three surfaces have an engine and no UI**~~ ✅ **RESOLVED 2026-08-12.**
      - **`result/confirm` — WIRED.** `game_detail` now returns the result plus `can.record_result` /
        `can.confirm_result`, and `Widgets.Game` renders the result card, the entry modal and the
        "that's right — confirm" button. **Whether the game is over is now the CLUB's clock**: the old
        widget compared `new Date(game.ends_at) < new Date()`, so a wrong phone clock or a change of
        time zone could offer the button mid-match. Guarded by
        `sc_the_result_screen_offers_only_what_the_server_allows`.
      - **`play-again` — WIRED (and it was never really about the 10 points).** It is the
        **"don't match me with them again" filter**: `matching.py` DROPS a player the viewer has
        thumbed down, rather than merely ranking them lower. Deleting it would have removed the only
        way a member can avoid someone, so it got the UI instead — folded into the same result screen,
        because that is the moment you know the answer. Still PRIVATE: `rate[]` is filtered to the
        viewer's OWN answers, proved on a DOUBLES game (in a 2-player game the subject uniquely
        identifies the rater, so a 2-player assertion passes for the wrong reason and guards nothing).
      - **`favourites` — DELETED.** Built engine-first, never given a screen, no client ever called
        either route, and the table was empty in every environment. The boot DDL now carries an
        idempotent `DROP TABLE IF EXISTS community.favourite` so the orphan does not survive in
        databases that already booted. `matching.py`'s history term rests on the surviving signal
        (two good games = full credit); the weights still sum to 100.
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

- [x] ~~**A coach's 60-min lesson billed R0.00**~~ — **FIXED BY TOMO 2026-08-08** (the duplicate R0.00
      60-minute variation was removed in Setup → Services). Kept here for the WHY, because the shape
      recurs: `price_for` resolves the exact duration then tie-breaks on **`amount_minor ASC`**, so two
      price rows for one duration are never both offered — **the cheaper one always wins, silently**.
      This was DATA, not code, which is why no gate caught it and only looking at the live screen did.
- [x] ~~**CODE GUARD so it cannot recur.**~~ **DONE 2026-08-09/10, both doors.** `create_price`
      refuses a second ACTIVE price for a duration (`DURATION_ALREADY_PRICED`, 409 with the existing
      amount named) — and, from the 10th, so does `patch_price`, which had walked straight around it
      two ways: **move** a 90-min row onto 60, or **re-activate** a removed 60 beside the live one.
      Both land in the identical end state the guard exists to prevent. A guard on creation only was
      a speed bump on one of two doors, and the editor's own Remove/re-add loop was the other.
      Guarded by `sc_one_active_price_per_duration` (both paths, plus "retiring can never clash").
      **The residual R0 case is deliberately NOT refused:** with duplicates impossible, a R0 row can
      only be the ONLY price for that duration — i.e. visible on the rate card and sometimes correct
      (a free intro session). `scripts/audit_zero_prices.py` reports those monthly instead.
- [ ] **ROOT-CAUSE `admin_home`'s failing block.** Home reported `refund_requests_error` while the
      Refund-requests SECTION worked — a query in `admin_home` was aborting the transaction and every
      later block returned its own zero (so the People counts 0/0/0 were probably false too). Each
      block is now savepointed **and logs its own name**, so the next deploy names the culprit in the
      Render logs. **Check the logs and fix the actual query.** The symptom is gone; the cause is not.
### OPEN AS OF 2026-08-10 — from the invoicing/settlement re-engineering

- [x] ~~**JP, Tshepo and Wonder have NO rent figure on file.**~~ **NOT A GAP — Tomo's decision,
      2026-08-10: all three are FREE for now.** No rent, nothing owed to the club. Left here so the
      next session doesn't "fix" it: an unset `rent_minor` on these three is deliberate, and the
      month-end sweep accruing nothing from them is correct.
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

- [ ] **51 members show as ON TRIAL** (People → Trial). The trial gives free courts, so verify these
      are genuine new signups and not mis-granted: `python -m scripts.audit_trials` (read-only;
      `--cancel-flagged` reverts wrong ones to PAYG).
- [ ] **Confirm PEAK PRICES are set.** The peak WINDOW is live (Mon–Thu 17:00–19:00, Setup → Club
      profile), but peak only charges more where a **`peak_amount_minor` per duration** exists on the
      court service. Window without amounts = inert.
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
- [x] ~~**Peak pricing is LOST on reschedule.**~~ **FIXED 2026-07-29** (same day it was raised, before
      peak was configured anywhere). It was **two** bugs, and the second hid the first:
      **(a)** `reprice_booking_order` took `duration_minutes` only and selected `p2.amount_minor` — the
      off-peak column — so it never read `peak_amount_minor` and never asked whether the new time was
      peak. It did not "keep the original band"; peak was dropped entirely.
      **(b)** The CALL only fired when the **duration** changed. Moving a 60-min court from 10:00 to
      18:00 is the same length, so nothing re-priced at all — the commonest move of the lot, and the
      reason (a) could never have been caught by testing length changes.
      Now `reprice_booking_order(..., starts_at=, resource_id=)` re-decides the band exactly as
      `diary.pricing.price_for` does (same club-local conversion, same `in_peak_window` call), and the
      reschedule fires it on **duration OR time OR court** — the court included because the peak window
      is per court, so a swap changes the price at an unchanged time. Both directions are pinned
      (into peak charges more, out of peak charges less), plus two regressions: a duration change still
      re-prices, and a **settled** order still never does. Guarded by `sc_peak_survives_a_reschedule`.
- [x] ~~**3 abandoned-checkout orders** held back by `void_orphaned_orders.py`'s 7-day age floor.~~
      **CLOSED 2026-08-09 — the class is gone, not just those three.** Lazy expiry was driven by
      expired BOOKING rows, and a pack or membership has no booking, so nothing ever swept them:
      they sat `awaiting_payment` for ever and were reported as outstanding. **R43,960 of July's
      "outstanding" was this**, and none of it collectable — a pack is granted ON PAYMENT, so an
      unpaid cluster is a failed sale, not a receivable. They now expire by themselves, voided as
      `abandoned_purchase` (which `order_void_is_recoverable` treats like a lapsed hold, so Yoco's
      72h retries and the 100-day reconcile can still settle a late payment). Duplicate Buy clicks
      that CREATED most of them are refused separately (`reusable_pending_purchase`, 120-min reuse
      window). `sc_abandoned_purchases_expire_by_themselves` · `sc_buy_click_never_mints_a_duplicate_debt`.

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
- [x] ~~**Reschedule UX polish**~~ — **DONE 2026-07-22/29.** `CRMUI.rescheduleModal` is the ONE reschedule
      UI (client · coach · admin · home), replacing four drifted forks none of which could move a court; it
      offers the service's configured durations + a court picker (config-driven `canChangeCourt`), and the
      same widget now also **moves a class session**. Original note: make member/admin reschedule flows
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

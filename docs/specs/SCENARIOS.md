# SCENARIOS — what the three harnesses actually guard

The scenario harnesses ARE the test suite (there is no pytest). This file is the **coverage
catalogue**: what each lane already proves, so you can tell at a glance whether the rule you are
about to change is guarded — and by which `sc_…`.

- **You do not need this file to run the gate.** The gate is `python -m scripts.test_all`; the
  commands, the current green baseline and the "no per-test filter" rule live in
  [`CLAUDE.md` § Gates](../../CLAUDE.md).
- **"Guarded by `sc_…`" anywhere in the docs names a function in one of these three files** —
  `grep -rn "def sc_the_name" scripts/` to read the war story it encodes.
- Each harness builds its own scratch club inside one transaction, runs every `sc_*` in its own
  SAVEPOINT, and **always rolls back**. Current green baseline:
  **booking 405 / billing 659 / statement 64** (56 / 92 / 12 `sc_*` functions).
- The **war stories** — why each rule exists and what it cost in production — are in
  [`GOTCHAS.md`](GOTCHAS.md). This file is the index of what is *covered*; that one is *why*.

---

## `test_booking_scenarios` — the diary (56 scenarios)

Double-book, lesson coach∩court, off-peak per-slot pricing, lifecycle,
**court→service allocation** (per-service courts + pricing), **classes reserve N courts** (held +
conflict guard + auto-repick) + editable, online class seat held → lazy-expired on abandonment →
waitlister promoted (paid seat never expired), cancel-after-start refused, unpriced booking refused,
PEAK court pricing (shown == charged), membership entitlement caps (duration / courts-per-day → PAYG) +
clay-court exclusion, configurable trial inherits its tier's caps, equipment hire (one order / no
double-bill + time-based availability, single ball machine can't double-book, cancel voids the add-on),
coach back-capture of a PAST lesson (staff-only `allow_past`, resource resolved from `coach_user_id`).

**SEMI-PRIVATE (squad) lessons** — per-head billing (one owed order per client), add-a-player-later,
a parent's kids bill the guardian, a member can't add a stranger / another family's child, cancel
voids every head. A card-only SERVICE refuses pay-at-court on the booking; a class enrolment is
payment-gated (no free seat via `membership_covered`/free, card-only class refuses pay-at-court).

**RESCHEDULE CAN MOVE THE COURT** — a court booking's own resource; a lesson keeps the coach and its
held-court row moves; a busy target refuses with `COURT_NOT_AVAILABLE`; re-picking the SAME court
doesn't block itself. **COACH PREFERRED COURT** honoured when free → falls back when busy (never
blocks a lesson) → an explicit court still wins.

**CLASS PAYMENT STATE** — the roster FLAGS an unpaid seat (`unpaid` + `payment_label`, never a bare
"Enrolled"); CHECK-IN settles a held online seat into a real owed debt (an `awaiting_payment` order
is invisible to statement/month-end/invoicing and the sweep only matches `'enrolled'`, so marking
attendance used to strand it forever); promotion treats a VOIDED `order_id` as NOT-billed (a stale id
used to hand out a FREE class); and a LATE payment RE-INSTATES a swept seat — but never overbooks a
full class (that logs a refund case).

**CLASS PRICE SURVIVES A SERVICE RENAME** — a class resolves its service through
`diary.resource.product_id` (the DURABLE link, set at `create_class_type` and boot-backfilled), never
a name join; an orphaned class REFUSES with `PRICE_NOT_CONFIGURED` rather than billing another class's
rate, and a retired price variation can never enrol at R0.

---

## `test_billing_scenarios` — money (92 scenarios)

Settlement modes, commission, tokens, membership (offline + per-tier), refunds + clawback, dispute
routing, void/lockstep, event stories, two-tier pricing, cancel/resize guards, **wallet adjust/expire**,
general order discount, 7-day-trial grant guard, lesson + class pack coach-linking, class↔coach
commission parity, per-service packs (product-aware draw), desk-payment amount guard, partial-refund
state, coach payout nets the ledger, month-end sweep idempotent, pack service-isolation (assign +
buy-wizard coach/product scoping), admin ad-hoc invoice (service×qty + fee − discount, tamper-proof),
client activity-summary (counts / minutes / by-service / by-week).

A pack respects its **SERVICE's** payment rule (a card-only pack is card-only — no at-court fallback
that grants it unpaid). **PAID PACK NEVER BYPASSED** — an owed-mode booking auto-draws a matching pack.
**RECONCILE ACTIVATES the pack/wallet** (behavioural GUARD — reconcile must call `activate_purchase`,
not just mark paid).

**PROMOTIONS** — a redeemed code discounts the ONE order (asserted: no second debt row + coach-arrears
lockstep + `original_amount_minor` was→now); `validate()` writes nothing; `amount_off` clamps at the
total; reverse frees the usage slot; every refusal by ERROR CODE (window / scope / min-spend /
per-customer + global caps / first-time / stacking / paid-order); unique per-recipient codes
(single-use, own cap not the shared one, revoke, recipient-bound). The **bonus REPLAY GUARDS** —
`bonus_period` 3+1 and `bonus_units` "buy 10 get 12" grant exactly once on BOTH the online (at
activation) and offline (at redemption) paths, and a replayed activation/grant does NOT re-add them.

**`membership_started` fires ONCE** per real activation (online + offline) carrying the email, never on
a replay, and NEVER on the 7-day trial (a `_EmitRecorder` context manager swaps the stubbed
`marketing_crm.tracking.emit` for a recorder — late binding is what makes that work).

**REVENUE-LEAK HARDENING** (2026-07-27, +43 checks) — a membership is judged on the BOOKING'S date not
today (book past your expiry → PAYG, never a locked-in R0); one coach can't be in two places
(court↔lesson↔class both ways, a class's many court-holds excepted); a member's 2nd CONCURRENT covered
court is PAYG (the booking still succeeds); equipment obeys its OWN `payment_modes` and HOLDS the
booking when the resolved method is online (unpayable → refused); club-default caps reach EVERY
membership incl. the price-less trial (and a NULL-cap tier no longer wipes a capped one); and a
waitlist promotion into a card-only class is HELD pending payment with the confirmation deferred
(never `class_enrolled` on an unpaid seat).

---

## `test_statement_reconciliation` — the statement folds (12 scenarios)

No double-count, pay-all-once, part-settle, reclaim, membership-covered R0 never owed, void/write-off,
arrears↔orders lockstep, **discount reprices one debt**.

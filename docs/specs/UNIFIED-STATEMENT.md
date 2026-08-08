# UNIFIED CLIENT STATEMENT — design + reconciliation plan

Status: **BUILT (Stage 1 + Stage 2), 2026-06-28.** Engine + harness, client switch-over, coach lockstep,
admin void/write-off, packs offline — all shipped + green (`scripts/test_statement_reconciliation.py`,
in `test_all`). The arrears table is kept internally and held in LOCKSTEP with orders (the user-visible
result is the clean single statement); fully dropping `coach_arrears`/`account_ledger` is an optional
later cosmetic cleanup. Authored 2026-06-28.
Goal: ONE client-facing statement listing every owed service once, payable online anytime in the
month, with a hard guarantee that nothing can over- or under-charge.

> **As-built extension (2026-07-12) — the reconciling FOLD is now the single money model everywhere.**
> The order-status-driven fold **Billed − Discount − Written-off = Invoiced; Invoiced = Paid +
> Outstanding** (month-scoped) is the one shape money is shown in across the **coach, admin, AND client**
> surfaces, single-sourced in `CRMUI.statementFold` (the reconciling headline) + `CRMUI.moneySummary`
> (the Billed→Collected→Outstanding band). A cancelled/void booking folds to R0 across the board;
> You-keep / club-commission come from ACTUAL `commission_split` rows. It fans out to the ONE client
> record — `client360.get_client_360` returns a `statement_fold` (via `billing/statement` +
> `_statement_fold`; coach scope folds only the coach's own coaching) that `Widgets.ClientRecord`
> renders through the same `statementFold`, so the client record, the coach console and the admin
> console can never show two different "paid" figures. Payment-status wording stays single-sourced in
> `billing.statement.settlement_status_label`.

---

## As-built: the three things built ON the invariant

*Moved verbatim out of `CLAUDE.md` on 2026-08-08 — each is a corollary of "one debt = one order", and each
is a place where a plausible-looking change re-introduces a second debt store or loses money outright.
`CLAUDE.md` keeps the one-line rule and links here.*

### Invoice & receipt DOCUMENTS

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

### "Pay all" settlement orders — the wrapper OWNS its contents

**"Pay all" settlement orders — the wrapper OWNS its contents.** A settlement order stands in for N real
debts. Its coverage is snapshotted **immutably** on `billing."order".covered_order_ids` at creation; the
child-side `settled_by_order_id` is MUTABLE (`_reclaim_abandoned_settlements` NULLs it after 30 min so an
abandoned checkout stops hiding the debt) and must **never** be the only record — Yoco retries/reconcile run
for **72 hours** against that 30-minute reclaim, and a payment landing after it used to mark the wrapper paid
and settle **ZERO** children (debt survived a successful payment; the member was billed twice; the orphaned
wrapper double-counted as revenue). `settle_settlement_order` and `is_settlement_order` read the snapshot
first, back-reference second. **Refunding a wrapper must UN-SETTLE its children** (`unsettle_settlement_order`
→ `open` **and** `settled_by_order_id = NULL`, or the debt returns invisible) — otherwise the club loses the
cash AND the receivable. That un-settle runs **strictly AFTER `_accrue_refund_clawback`**, which only reverses
commission on children still `paid`. A PARTIAL refund of a wrapper can't be allocated per child — it logs and
flags rather than half-settling. **Changing a COVERED debt kills the wrapper**: `void_order` and
`discount_order` call `invalidate_live_settlement_for` first, which detaches every child and voids the
stale wrapper — its total froze at creation, so paying it would collect R900 for R600 of debt with the
surplus landing nowhere (the fold still reconciles, because a voided child is excluded). `cancel_booking`
voids unconditionally, so a member cancelling mid-checkout used to do this to themselves. We cannot
retract a Yoco page already open, so `settle_settlement_order` also reports a **`surplus_minor`** when it
collects more than it settles — prevention where possible, detection where not. Guarded by
`sc_settlement_refund_restores_debt` + `sc_settlement_survives_reclaim` +
`sc_settlement_invalidated_when_a_debt_changes`.

### The month-end sweep is PER-CLIENT-TRANSACTIONAL, TIME-BOXED and RESUMABLE

**The sweep is
PER-CLIENT-TRANSACTIONAL, TIME-BOXED and RESUMABLE** — `run_month_end` is the single-transaction form (one
club, harness); the CRON ROUTE instead drives `month_end_period`/`month_end_accrue`/`month_end_targets` then
`month_end_client` in **its own `session_scope()` per client**, stopping at `max_seconds` (default 90, under
gunicorn's 120s reaper) and returning `{ok, complete, remaining, failed}` for the caller to loop. This is not
optional polish: the sweep allocates **gapless invoice numbers** and `emit()`s the email from a **background
thread with its own session**, so the email does NOT roll back — one big transaction meant a worker reaped at
client #400 rolled back 400 invoices whose numbered emails had already landed, and a re-run allocated
different numbers. One client's failure now costs one client, and `month_end_notice` makes every later pass
skip the done. Fired by
**`.github/workflows/month-end.yml`** on the **25th** (the club billing day) — it **loops until `complete`** and
**FAILS THE JOB LOUDLY** on any non-200/`ok:false` (it used to end in `|| echo`, so the run went green even if
nothing was billed — invisible for 30 days).

---

## 1. The problem (what's wrong today)

A client's "what I owe" is currently computed by **two independent systems that overlap**:

| Surface (Account page) | Source table | Contains |
|---|---|---|
| **Account balance (pay end of month)** | `billing.account_ledger.balance_after_minor` | every **monthly_account** booking (lessons + courts + …) |
| **Coaching statement — owed** | `billing.coach_arrears` (commission engine) | every unpaid **lesson** (at_court **or** monthly_account) |

**The overlap (double count):** a lesson booked on the monthly account writes to **both**
`account_ledger` *and* `coach_arrears`. So the same R2200 of lessons appears in the account balance
**and** the coaching statement. Observed on screen: account balance R2350 = lessons R2200 + court R150;
coaching statement R2200 = the same lessons.

**Why it's only "latent" right now:** the **only payable surface is the coaching statement**
(`create_statement_payment`). It settles `coach_arrears` + its own online order, but does **not** touch
`account_ledger` or the original monthly-account order. So:
- After paying the statement, the account balance still shows the lessons as owed.
- The **court PAYG and any package have no online pay path at all** — they're not line items anywhere.

**The hazard if we naively "let them pay everything":** statement (R2200) + account balance (R2350)
both include the lessons → client pays R4550 for R2350 of tennis. **Double charge.** This is precisely
the risk to design out.

### Root cause
`billing.order` is *already* created for every booking/purchase, but two **derived** ledgers
(`account_ledger` = the monthly tab, `coach_arrears` = the coach-commission tab) are each treated as a
**client-facing debt**. They were built by different lanes and never unified.

---

## 2. The invariant (the rule everything must obey)

> **One debt = one `billing.order`, settled exactly once.**
> The client owes the **sum of their unpaid orders** — nothing more, nothing less. Commission splits
> and the monthly tab are **internal/derived consequences** of an order, never a second debt.

If we hold this invariant, reconciliation is automatic: paying an order is the only thing that clears a
debt, and an order can only be paid once (idempotent via `apply_payment_event`).

---

## 3. Target model

### 3.1 Source of truth = unpaid orders
The statement reads **`billing.order` where `status IN ('open','awaiting_payment')`** for the client
(plus their order lines for descriptions). Each order is ONE statement line:

```
{ order_id, created_at, description (from order_line), kind (lesson/court/class/membership/pack),
  amount_minor, settlement_mode, status, payable: bool }
```

- `open` = owed (at_court / monthly_account) — **payable online now** OR collectible at desk.
- `awaiting_payment` = an online order mid-checkout — payable (resume), not double-created.
- `paid` / `refunded` / `void` / `written_off` = not owed (shown under history).
- `membership_covered` / `free` / `token` orders are `paid` at R0 — they never appear as owed.

### 3.2 Coach commission becomes a pure consequence
`coach_arrears` stops being a client debt. The coach's earnings still accrue, but **only when the
underlying order is paid** (online → `record_split_for_order`; desk → on the desk-payment event).
The coach statement (coach-facing) reads from the splits/orders, not from a parallel client tab.

Two implementation options (decide in §7):
- **(A) Keep `coach_arrears` as an internal mirror** but REMOVE it from the client statement and from
  `account_ledger` overlap — i.e. arrears is never shown to the client as "owed"; the client owes the
  ORDER. Lowest blast radius.
- **(B) Retire `coach_arrears` as a debt entirely**, deriving the coach's "owed by client" view from
  unpaid lesson orders directly. Cleaner, larger change.

### 3.3 The monthly tab becomes a presentation of unpaid orders
`account_ledger` is no longer summed as a separate "balance you owe." The "pay end of month" total =
the sum of the client's unpaid orders (same source as every line). If we keep `account_ledger` at all,
it's an internal audit journal, not the headline number.

### 3.4 Settlement (pay)
- **Pay one line** → pay that order (online via Yoco, or it's already collectible at desk).
- **Pay all** → ONE online order whose lines reference the set of unpaid orders being cleared; on
  `charge_succeeded`, mark each referenced order `paid` + fire its consequence (commission split).
  This generalises today's `create_statement_payment` (which already does this for arrears) to
  **all** unpaid orders, keyed by `order_id` instead of `arrears_id`.
- **Anytime in the month**: nothing is time-gated; the statement is live. Month-end is a soft
  reminder, not a lock.

> **Month-end sweep (shipped).** `POST /api/cron/month-end` (`billing.commission.run_month_end`,
> OPS-guarded, fired by `.github/workflows/month-end.yml`) accrues coach arrears + rent for the period,
> then notifies every client with an OPEN statement balance by consolidating their open orders into ONE numbered statement invoice + pay-link email
(`invoice_issued`); a plain `statement_ready` is only the FALLBACK when there is nothing new to
invoice. The sweep commits PER CLIENT, is time-boxed and resumable (in-app +
> best-effort email). Idempotent per `(club, user, period)` through `billing.month_end_notice`, so a
> re-run never re-notifies. It's a **soft snapshot + notify** — it does NOT month-box or freeze the live
> statement, which stays current-unpaid with no month-boxing (§9.4).

### 3.5 Online + offline, per the existing payment rule
Reuse the per-service payment rule already shipped: an order owed at_court is settle-at-desk by default
but **also** payable online from the statement if the client chooses; a single-option service behaves
as configured. No new settlement vocabulary.

---

## 4. Reconciliation rules (the guarantees)

1. **No line appears twice.** The statement is built from `billing.order` only. `coach_arrears` and
   `account_ledger` are never *added* to the order total.
2. **Pay-once.** Paying an order sets `status='paid'` under the existing idempotent
   `apply_payment_event`; a replay is a no-op. A "pay all" order references child orders; settling a
   child twice is impossible (status guard).
3. **Sum identity.** `client_owes == SUM(amount_minor of unpaid orders)`. The coach statement's
   "owed by this client" MUST equal the subset of that sum attributable to the coach's lessons — same
   orders, filtered — never an independent figure.
4. **Consequence-after-payment.** Commission/coach-earning rows are written only on a paid order, and
   are derived from that order's lines — so coach earnings can never exceed what the client paid.
5. **Refund symmetry.** A refund reverses the order (`refunded`) AND reverses its consequence (the
   split), so a refunded lesson removes the coach accrual too (already partly handled — verify).

---

## 5. What changes, file by file (when approved)

- `billing/me.py` — `member_financials`: replace the `account_ledger` balance headline + the separate
  arrears read with a single `unpaid_orders` list + total. Add `statement(client)` returning the line
  items.
- `billing/commission.py` — generalise `create_statement_payment` → `create_settlement_order(order_ids)`
  (pay any set of unpaid orders); keep arrears settlement as the lesson-specific consequence. Stop
  surfacing arrears as the client's owed number.
- `billing/orders.py` / `billing/ledger.py` — `account_ledger` demoted to internal audit (or left
  as-is but no longer summed as the client headline).
- `me/routes.py` — `GET /api/me/statement` returns the unified line items; `POST /api/me/statement/pay`
  accepts `{order_ids?}` (default = all unpaid) and routes online vs desk via the payment rule.
- `frontend/js/account.js` — ONE "Your statement" card: line per service (date · description · amount ·
  status) + a single reconciled total + "Pay all online" / per-line pay. Remove the separate "Account
  balance" headline and the parallel "Coaching statement" pay button (fold into the one statement).
- `yoco_billing/` webhook — on `charge_succeeded` for a settlement order, mark all referenced orders
  paid + fan out consequences (extends the current membership/arrears hook).

No schema change is strictly required (orders already exist); a nullable
`order.parent_settlement_order_id` (or a join table) may help link a "pay all" order to its children —
decide in §7.

---

## 6. Reconciliation test harness (built BEFORE the UI; gates the change)

A new `scripts/test_statement_reconciliation.py` (scratch DB, rollback-only) asserting the invariants on
every settlement mode and service type:

1. **No double count** — book a lesson on monthly_account → the statement total == the lesson order
   amount (NOT lesson×2); account balance is not added on top.
2. **Pay-once** — pay the statement → every covered order `paid`, total owed → 0; replay the webhook →
   still 0 (no negative, no re-charge).
3. **Mixed basket** — 3 lessons + 1 court PAYG + 1 pack, mixed at_court/monthly → statement lists 5
   lines, total == Σ order amounts; pay all → all paid once; coach earnings == Σ paid lessons' splits.
4. **Partial pay** — pay one line → only that order clears; the rest still owed; totals reconcile.
5. **Membership-covered R0** — a covered court never appears as owed; paying the statement ignores it.
6. **Refund symmetry** — refund a paid lesson → order `refunded`, coach split reversed, statement and
   coach views both drop it.
7. **Desk + online same debt** — an at_court order paid at the desk == the same order paid online:
   never both (status guard); coach accrues once.
8. **Idempotent re-accrual** — re-running the month-end accrual adds nothing for already-settled orders.

Gate: this harness must be green (and `python -m db` twice) before the UI ships. Existing
`test_billing_scenarios.py` (now 118/118) must stay green.

---

## 7. Open decisions for Tomo (need answers before build)

1. **Arrears model:** option (A) keep `coach_arrears` as an internal mirror (smaller change) or (B)
   retire it and derive the coach's client-owed view from unpaid lesson orders (cleaner)?
2. **"Pay all" linkage:** add `order.parent_settlement_order_id` (schema, clean) vs a side table vs
   reuse the arrears `pay_order_id` pattern generalised?
3. **Packs offline:** bring packs onto the at_court/offline path too (so a pack bought "at club" is an
   owed order on the statement), or keep packs online-only? (Today they're online-only — the "package
   paid at club" you tried likely didn't create an owed line.)
4. **Statement window:** purely live "current unpaid" (recommended) vs a month-boxed invoice with
   carry-over?
5. **Migration of existing data:** for club #1's current `account_ledger` / `coach_arrears` rows, do we
   backfill them onto orders, or treat go-live as the cut-over (only new orders use the unified path)?

---

## 8. Recommended sequence (once decisions are made)
1. Lock decisions in §7.
2. Build the reconciliation harness (§6) against the *current* code to capture today's behaviour +
   prove the double-count, then drive the new behaviour to green.
3. Backend: order-driven statement read + generalised settle, behind the harness.
4. Webhook fan-out for settlement orders.
5. Frontend: single statement card.
6. Demote `account_ledger`/`coach_arrears` from client-facing debts.
7. Verify: harness green, `python -m db` twice, `test_billing_scenarios` green, manual basket test.

## 9. Decisions — LOCKED (Tomo, 2026-06-28)
1. **Arrears:** option **(B)** — retire `coach_arrears` as a client debt; derive the coach's
   "owed by this client" from **unpaid lesson orders**, "paid" from commission splits. Cleaner.
2. **Pay-all:** covers **every** unpaid order; each line shows its **payment type** (e.g. "Pay at club").
   "Settle all" clears the lot → **balance = 0**. Mechanism: a `billing.order.settled_by_order_id`
   link (child unpaid order → the settlement order that paid it).
3. **Packs:** bring them in — a pack not paid online (pay-at-club / month-end) is an **owed order** on
   the statement, same offline logic as bookings.
4. **Window:** live **current-unpaid** (no month-boxing).
5. **Migration:** none — **clean cut-over** (no real customers yet). PLUS: build **void / cancel /
   write-off** for unpaid orders so a test bill (or a genuine mistake) can be cleared — cancelling a
   booking voids its unpaid order; an admin can void (mistake) or write-off (forgive) any owed line;
   voided/written-off lines drop off the statement and the balance.

### Build order (revised)
- **Stage 1 (foundation, harness-first):** `order.settled_by_order_id` column · `billing/statement.py`
  (unpaid_orders · statement · create_settlement_order · settle_settlement_order · void/write-off) ·
  webhook fan-out · **`scripts/test_statement_reconciliation.py`** proving the invariants. NOT yet wired
  to the live UI.
- **Stage 2:** point `me/statement` + `account.js` at the new engine (one statement card); migrate the
  coach statement to orders; retire `coach_arrears`/`account_ledger` as client debts; packs offline
  purchase path; admin void/write-off UI.

Stage 1 is the safety foundation; Stage 2 is the visible switch-over (done only with Stage 1 green).

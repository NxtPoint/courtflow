# Commission, Coaching Settlement & Bundles — Owner Decisions (01)

> Owner's answers (2026-06-21) to the open questions in [`00-roadmap.md`]. These are LOCKED
> decisions — build Phase D (commission/rental) and the coach cockpit to this. Supersedes the
> owner/coach specs where they differ. **White-label principle:** everything here is per-club
> configurable — services, pricing, commission, bundles. Don't hard-code NextPoint's choices.

## Commission / rental model
- **Everything is ex-VAT.** Commission is computed on **net (ex-VAT)** amounts.
- **Rent AND/OR commission — freely combinable, per coach.** A coach agreement can be: just rent
  (fixed monthly), just commission (%), or **both** (e.g. a lower monthly fee + a reduced commission).
  It is **additive**, NOT "whichever is greater." So `coach_agreement` = optional `rent_minor` +
  optional `commission_pct`, either/both, configurable per coach (and the % still resolves by the
  `coach+product > product > coach > club` precedence from the owner spec).
- **No commission on membership-covered free courts** (gross is R0 → nothing to split).
- **Classes: support commission too.** Solve for both lessons AND classes — a club may run classes
  in-house OR via a coach who owes commission. The commission engine keys off `billing.product`
  (lesson or class), so the same rules apply; just make it configurable per service/product.

## Coaching settlement — the key model
Two settlement timings on the platform:
- **Point-of-sale (settles immediately):** **classes** and **online court/lesson bookings** are paid
  via Yoco at the moment of booking. Already built — no change.
- **Coaching can settle later (month-end arrears):** lessons not paid online accrue to the coach's
  account and the **coach invoices the client directly at month-end** for the unpaid amount.

### Three coach pricing options (per coach / per service, configurable)
1. **PAYG** — pay per lesson, **online only** (Yoco at booking).
2. **Bundles** — prepaid **5 / 10-lesson packages**, **online only** (Yoco upfront); lessons draw
   down against the prepaid credit.
3. **Monthly in arrears** — lessons booked through the month, **coach invoices manually at month-end**.

Options **1 & 2 are online (Yoco)**; option **3 the coach invoices manually** and marks it collected.

### The critical coach month-end cockpit (highest priority for the coach)
Every client with lessons **automatically posts to the coach's account**. At month-end the coach sees,
**per client**, a statement:
- lessons taken (count + value),
- **paid via Yoco** (PAYG + bundle draw-downs),
- **plus amounts still owed** (arrears),
- → a **net balance** per client.

The coach can issue each client a **final statement / invoice** for the owed amount and **mark it
collected** when paid. (Statement delivery: generated on-platform; sent via email when SES/Klaviyo is
live — until then shareable/printable.)

### Owner visibility & commission timing
- The owner must see, per coach at month-end: **what was invoiced and what was collected** (online +
  manually-marked). The coach **pays the owner commission on the COLLECTED amount** — so **commission
  accrues on collection, not on billing** (online = at payment; arrears = when the coach marks the
  invoice collected). This keeps the owner from chasing payments — the platform tracks collected →
  commission owed per coach.
- Owner cockpit: per-coach **invoiced vs collected vs commission owed (+ rent due)** → a running
  `coach_ledger` balance the coach owes the club.

## Data-model implications (refines the owner/coach specs)
- **`billing.product`** gains a coach **pricing_mode** per service: `payg | bundle | arrears` (+ bundle
  size/price for bundles). Per-club, per-service configurable.
- **Bundles** = a prepaid credit: a `billing.lesson_bundle` (or credit wallet) — purchase via Yoco
  grants N credits; each lesson booking draws one credit; track remaining + expiry (expiry TBD).
- **Lesson order/settlement** must support `arrears` (an unpaid lesson posts to the coach's per-client
  ledger; not an online order until invoiced/collected).
- **Coach per-client ledger + statement**: an invoice/statement object (lessons, paid, owed, net) the
  coach generates + marks collected; collection event → commission accrual.
- **`commission_split` accrues on collection** (online charge OR arrears-collected), ex-VAT.

## RESOLVED (2026-06-21, second round)
- **NOTHING IS HARDCODED — build fully CONFIGURABLE CAPABILITIES.** This is the platform's spine
  (white-label). Every commercial value (prices, durations, plans, commission %, rent, term lengths)
  is owner-configured data, never a constant in code. Design each feature as a *capability* (e.g. the
  plan capability is "a function of amount + duration") that the owner parameterises per club.
- **Memberships/plans are TERM-based, not recurring** (we have no recurring billing). The owner sets up
  **plan terms** = (label, **amount**, **duration**), e.g. 1 month R220, 3 months R600, 6 months R1100.
  A member buys a chosen term via Yoco (one-off) → membership granted for that term's duration. Replace
  the hardcoded "1 month R220" with these configurable terms. Same capability covers lesson "bundles"
  (a term/pack the coach or owner configures: amount + count/duration).
- **Arrears = OFF-PLATFORM.** The coach sends the statement to the client and chases the **EFT** payment
  himself; the platform records it and the coach **marks it collected** (no Yoco "pay statement" link for
  now). Commission still accrues when the coach marks it collected.
- **Yoco fees are for the OWNER's account** — the owner recovers them through commission. So the
  commission/split math does NOT deduct the gateway fee from the coach; the owner bears it.
- Commission base = **ex-VAT net**, on **collected** amounts.

### Still-open (non-blocking; default sensibly)
- Plan/bundle **expiry** edge cases (unused-credit refund/transfer) — default: no refund of a started term.
- VAT registration / invoicing format — later.

## RESOLVED (2026-07-29, third round) — CASH CUSTODY + COMMISSION TIMING

> Owner's answers after a year-one month of live operation. These settle the two questions the code
> could not infer, and they are now **built and scenario-guarded**. The 2026-06-21 decisions above stand;
> this round makes the *direction* and the *timing* explicit.

### D6 — WHO HOLDS THE CASH decides the ledger's direction
`billing.coach_ledger` is **SIGNED: + = the club owes the coach, − = the coach owes the club.**

| How the money arrived | Who physically holds it | Ledger entry |
|---|---|---|
| Yoco online checkout / invoice pay-link | **Club** | `+coach_net` (`commission_earning`) |
| EFT or cash recorded at the desk | **Club** | `+coach_net` |
| Coach collects courtside / off-platform | **Coach** | `−owner_cut` (`commission_due`) |
| Monthly account → month-end invoice → Yoco/EFT | **Club** | `+coach_net` when it lands |

**Owner's words:** *"paid online, goes to club. eft is club. pay at court means paid to coach directly —
they received the funds, we get the comm, but don't need to refund them anything. paid at end of month
assumes the club will get the funds either through the invoice process or paid eft after the fact."*

**As built, the direction follows WHO RECORDED THE PAYMENT, not what the booking said** — which is
deliberately more precise than the flat rule, and needs no policy enforcement to stay true:
- `POST /api/billing/desk-payment` is **`club_admin`-only** (`take_pay_at_court`). A coach physically
  cannot record a desk payment.
- A coach's only collection verb is **"Mark collected"** (`mark_arrears_collected`) — the off-platform
  path, which posts `−owner_cut`.
- So an `at_court` lesson is **not** automatically coach-held. If the client pays at reception, or settles
  the month-end invoice, the **club** holds it and the coach is owed his net. The club can take a lesson
  payment at the desk without the ledger going wrong.

**What must be enforced with coaches is behavioural, not financial:** mark the collection promptly, or the
club's commission on that cash stays invisible.

> **This restores the original intent.** §"Owner visibility & commission timing" above already described
> *"a running `coach_ledger` balance the coach owes the club"*. The as-built had drifted:
> `mark_arrears_collected` posted `+coach_net` like a club-held collection, so the ledger was **wrong by
> the whole gross on every off-platform collection** and told the owner to pay a coach who was holding the
> club's money. Fixed 2026-07-28 (`_write_split_pair(cash_held_by=)`); historical rows corrected in
> production 2026-07-29 via `scripts/fix_inverted_coach_ledger.py` (2 coaches, R14,800 net).
> The `commission_split` rows were always right — the sale divides the same whoever holds the cash — so
> commission **reporting** never lied; only the running **balance** did.

### D7 — Commission is paid on FUNDS RECEIVED, and there is no monthly commission run
The club invoices on the **25th** and collects by the **1st**. Commission is only ever paid on money
actually received — and this needs no period logic, because there is no commission run to time:
- The split posts at `charge_succeeded`, i.e. **at collection**. Unpaid work never accrues a ledger earning.
- `coach_ledger` is a **live running balance**, not a period bucket. A payment landing on 2 August adds to
  the balance on 2 August; when a `coach_payout` is recorded, the balance is exactly what has been
  collected at that instant. **A payment arriving in the new month is simply in the next settlement** —
  there is no window it can fall between.
- The 25th sweep accrues **arrears + rent** and issues **client** invoices. It does **not** pay commission.

### D8 — The coach owns the client relationship, therefore the coach chases the payment
**Owner's words:** *"otherwise I am running around chasing payments. The coaches have the relationship with
the client, and if a client doesn't pay then I lose in the process. What we are doing is creating a platform
for the coaches to run and manage their business. They must take control of their finances and make it
work."*

This is a **product principle**, not just an accounting one, and it is what the money model already
expresses: unpaid coaching sits on the coach's tab as **projected** commission that never realises until
the client pays. The coach sees the gap in their own P&L (`Widgets.Earnings`, "You keep"). The club does
not carry the cost of a client who doesn't pay, and does not do the chasing.

**Design consequence — do not "help" by moving unpaid coaching onto the club's books.** Any future feature
that auto-settles, auto-writes-off or fronts a coach their commission before collection breaks D7 *and*
D8 at once. Escalation is a *reporting* problem (show the coach their ageing debt), never a cash one.

## Build order impact
Phase D becomes: (D1) `coach_agreement` + `commission_rule` + resolution; (D2) accrue
`commission_split` **on collection** for online lessons/classes; (D3) **bundles** (prepaid credits +
draw-down); (D4) **arrears**: per-client coach ledger + statement/invoice + mark-collected →
commission accrual; (D5) owner cockpit (invoiced/collected/commission/rent per coach). The coach
month-end statement (D4) is the coach's most-wanted surface — prioritise it within D.

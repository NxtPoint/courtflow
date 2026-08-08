# GOTCHAS — the war stories behind the rules

Every entry here is a bug that reached production (or came within one merge of it), the reasoning that
explains why the code looks the way it does, and the `sc_…` scenario that pins it. **`CLAUDE.md` carries
the one-line RULE; this file carries the WHY.** Read the entry before you change the code it describes —
most of these look like harmless simplifications from the outside, which is precisely how they happened.

Moved out of `CLAUDE.md` verbatim on 2026-08-08 (that file was 1,155 lines, two thirds of it these
stories, and it loads into every session). Nothing was reworded. `grep -rn "def sc_the_name" scripts/`
still reads the scenario that guards any entry.

> Adding one? Put the RULE in `CLAUDE.md`'s Gotchas index (one line, with its `sc_…` name) and the
> STORY here under the matching heading. `python -m scripts.audit_docs` checks that every `sc_…` a doc
> names still exists in a harness.

## Contents

- [Booking & the diary](#booking--the-diary) — 10 entries
- [Courts, peak hours & equipment](#courts-peak-hours--equipment) — 4 entries
- [The lesson lifecycle](#the-lesson-lifecycle) — 5 entries
- [Classes](#classes) — 4 entries
- [Memberships, the trial & entitlement caps](#memberships-the-trial--entitlement-caps) — 5 entries
- [Pricing & payment rules](#pricing--payment-rules) — 2 entries
- [Refunds & the Yoco gateway](#refunds--the-yoco-gateway) — 7 entries
- [Invoicing & the month-end close](#invoicing--the-month-end-close) — 5 entries
- [Money custody & the coach ledger](#money-custody--the-coach-ledger) — 7 entries
- [Reads that lie](#reads-that-lie) — 2 entries
- [Email & notifications](#email--notifications) — 1 entry
- [Infrastructure & environment](#infrastructure--environment) — 1 entry


---

## Booking & the diary

### `booking_type` must match the resource, and `'class'` is NOT bookable via `/api/diary/bookings`

**`booking_type` must match the resource, and `'class'` is NOT bookable via `/api/diary/bookings`**
(`BOOKING_TYPE_NOT_ALLOWED` / `RESOURCE_KIND_MISMATCH`). The kind check used to live only inside the
lesson branch and the court-service guard only inside the court branch, while `'class'` is legal in the
schema CHECK (that's how a class GiST-reserves its court) — so POSTing a COURT as a `'class'` skipped
the court block entirely: cheapest class rate, class payment rules (usually none), a class pack drawn
for a court, and **a court GiST-blocked but INVISIBLE to staff** (the master feed excludes
`booking_type='class'` and a crafted row has no `class_session` behind it) — a phantom hold nobody could
see or cancel. A real class court hold is inserted by `diary.classes._reserve_court_for_class`, and
`create_booking` has exactly ONE caller (the route). Guarded by `sc_booking_type_must_match_resource`.

### A posted `product_id` is VALIDATED before anything uses it

**A posted `product_id` is VALIDATED before anything uses it** (`SERVICE_NOT_VALID`): it must be an
ACTIVE product of this club whose `kind` matches the booking type, and for a lesson/class either shared
(NULL coach) or the RESOLVED coach's own. It arrives off the request body and used to be checked only on
the court branch — yet it drives the payment gate, the price guard AND the order price, and
`pricing.price_for`'s product branch carries **no kind, coach or `product.active` predicate** (those live
in its kind branch), falling through to `amount_minor ASC LIMIT 1`. Posting another service's id billed
the club's cheapest price, evaluated the card-only rule against the SUBSTITUTED service, and — if the id
named a court product — made commission classify a delivered lesson as court, so **the coach accrued
nothing**. Service ids are public to any member via `GET /api/diary/services`. Guarded by
`sc_posted_service_must_be_real`.

### ONE PERSON, ONE PLACE — the GiST constraint can't express it

`booking_no_overlap` is keyed on
`resource_id`, so it stops one COURT (or coach RESOURCE) being taken twice and says nothing about a
human. Three things slipped through: a class books NO row on the coach's resource (only court
holds), a coach's own court booking sits on the COURT's resource, and the court↔coach direction was
checked in neither `_coach_class_conflict` (lesson branch only) nor `classes._coach_busy_at`
(`booking_type='lesson'` only) — so a coach could hold a court AND deliver a lesson AND run a class
at 09:00 with no constraint violated. `bookings._coach_commitment_at` checks all three shapes on
create/accept/reschedule and returns which one clashed; a class's several court-holds are
`booking_type='class'` and never read as a clash with itself. **Members are NOT blocked** (a
doubles group legitimately holds two courts) — their 2nd *concurrent* covered court simply
downgrades to PAYG via `entitlement._has_overlapping_covered`. Guarded by
`sc_one_coach_one_place_at_a_time` + `sc_member_second_concurrent_court_is_payg`.

### ENTITLEMENT IS EVALUATED ON THE BOOKING'S DATE, NEVER `CURRENT_DATE` (2026-07-27)

`membership_covers` + `entitlement.active_caps` used to test `current_period_end >= CURRENT_DATE`
— "is this plan alive right now" — while `starts_at` only ever drove the access WINDOW. So a
member could book FORWARD past their own expiry and the row was written `membership_covered` at
R0 **permanently**: the term lapsing later changed nothing, the price was already fixed. Reported
as trialists booking beyond their 7 days; it was never trial-specific — a monthly member could
book out all of next month on the last day of this one and not renew, and
`club.policy.booking_window_days` (default 14) was the only limit on the reach. Both now compare
against the booking's CLUB-LOCAL date (`entitlement.local_date`), and the picker fetches
`pricing.membership_covered_until` once per range so it can't advertise "Covered" on a day the
server will charge for. Guarded by `sc_membership_cannot_book_past_its_own_expiry`.

### Capacity-sweep needs no cron

**Capacity-sweep needs no cron:** abandoned `held` bookings are released by **lazy expiry** —
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

### A court move re-runs the MONEY guards a time move runs

**A court move re-runs the MONEY guards a time move runs** — a COURT booking may not cross court
SERVICES (`COURT_SERVICE_CHANGED`: it is priced by its service, and `reprice_booking_order` re-prices
on the SAME product so it could never correct the change), and a `membership_covered` booking
re-runs the FULL entitlement against the TARGET court (`COURT_NOT_COVERED` — the time-window check
alone let a free booking move onto a clay court members are never covered for). The service compare
NORMALISES None (`str(a or "") != str(b or "")`): in a multi-service club an unallocated court
resolves to an ambiguous None, and a short-circuit would wave that move through. A lesson's held
court may move freely — a lesson is priced by its LESSON service. `CRMUI.rescheduleModal` filters the
court list to the booking's own service so the UI never offers a move the server will refuse.

### `diary.booking.product_id` remembers WHICH service was booked

**`diary.booking.product_id` remembers WHICH service was booked.** A coach can sell several lesson
services (Private R400, Semi-private R250, Cardio R120). `create_booking` resolves the exact one — and
used to discard it, which was invisible until the review gate: a `requested` lesson creates **no order**,
so on accept the service was gone and `_create_order_guarded` fell back to `price_for(kind='lesson',
coach)`, whose tie-break is **`amount_minor ASC LIMIT 1`** — the coach's CHEAPEST service. A R400 lesson
billed R250, commission accrued on the wrong base, earnings attributed the sale to the wrong service, and
the pack match degraded identically (a NULL request product matches anything). `accept_booking` now prices
AND matches the pack off `bk["product_id"]`. **If you add a column here, add it to `_booking_dict`'s SELECT
too** — it returns `None` otherwise and the fallback silently bites again. Guarded by
`sc_gated_lesson_bills_the_booked_service`.


### A REPEATED "BUY" MUST RE-OFFER THE UNPAID ORDER, NOT MINT A SECOND DEBT (2026-08-08)

`create_bundle_order` and `create_membership_order` both INSERTed unconditionally with no reuse
guard, so every tap of Buy minted ANOTHER `awaiting_payment` order — plus another pending
`token_wallet` or placeholder `membership_subscription` behind it. Production carried **five
identical R5,000 pack orders for one member on ONE DAY**, and R43,960 of July 2026's reported
"outstanding" was this. Same defect as the documented Yoco one ("POST /checkout mints a FRESH
checkout on every call"), one level up: there the duplicate was a checkout, here it is a DEBT. And
worse than noise — an online pack is granted ON PAYMENT, so an unpaid cluster means the member owns
nothing and owes nothing: it was a **failed sale** being chased as a receivable.
`billing.orders.reusable_pending_purchase` is the ONE guard both paths call, so a pack and a
membership cannot drift apart. It re-offers only an unpaid ONLINE intent, only when the product,
the amount, and the absence of any charge all match, and only inside `_PENDING_REUSE_MINUTES` —
that window is load-bearing in BOTH directions: long enough for a real retry, short enough that a
purchase abandoned last month can never be revived into this month's order, because `created_at`
dates a non-booking purchase for period-scoped invoicing. An at_court/monthly purchase is NEVER
reused: it writes a real `open` debt and grants immediately, so re-offering one would hand out a
second pack for one payment. Guarded by `sc_buy_click_never_mints_a_duplicate_debt`.

### AN ABANDONED PURCHASE HAS NO BOOKING, SO NOTHING EVER SWEPT IT (2026-08-08)

Lazy expiry is driven by expired BOOKING rows (`_void_orders_with_no_live_bookings`), so it cleans up
an abandoned court/lesson order and voids it. A pack or membership has **no booking at all**, so it
never entered that set and sat `awaiting_payment` for ever, reported as outstanding for ever.
`billing.statement.release_abandoned_purchases` is the booking-less half, hooked into the same
statement self-heal chain, with the same guards as the manual cleanup. **A late payment is not
lost:** Yoco retries 72h and reconcile sweeps 100 days, so the void records
`void_reason='abandoned_purchase'` and `order_void_is_recoverable` treats it exactly like a lapsed
booking hold — two shapes of the same thing, a void nobody chose. An ADMIN void carries neither
marker and stays untouchable. (`billing.order.void_reason` is new and worth having anyway:
`void_order` had always taken a `reason` and thrown it away, so the audit trail could not say whether
a void was a decision or an expiry.) Guarded by `sc_abandoned_purchases_expire_by_themselves`.

### A RENT COACH BILLS HIS OWN CLIENTS — AND BOOKS LESSONS, NOT COURTS (2026-08-08)

Four coaches at NextPoint pay monthly **rent** and invoice their clients **directly**. They hold
their teaching slots by booking lessons **against themselves** — and nothing read
`billing.coach_agreement` when deciding whether to bill, so each of those bookings raised a real
client debt against the coach **for his own work**. R68,000 of phantom "outstanding" accumulated
across four accounts, had to be voided by hand, and made every People and earnings figure
untrustworthy until it was.

`coach_agreement.billing_model` (`'commission'` default | `'rent'`) is the record the club already
half-had: `rent_minor` existed, but nothing consumed it at billing time. `create_booking` resolves
it and raises no order when a rent coach books **himself**.

**Deliberately narrow — only when the billed party IS the coach.** "Never bill a rent coach's
lessons at all" is a bigger claim about the relationship than the evidence supports, and it fails
dangerously: if the club stops billing a NAMED client while the coach invoices them directly,
**nobody** is billed. A rent coach booking a named client still bills that client.

**A coach may not put a club court in his own name** (`COACH_CANNOT_BOOK_COURT`). He never needs to
— the lesson flow allocates a court itself — and what a raw self-booked court allows is holding club
courts for friends outside any service, with a payer who was never going to pay. **On-behalf is
still allowed**: booking a court for a NAMED client is a real service action and that client is
billed, which is the point. **This NARROWS a documented rule** — BUSINESS-RULES' staff override let
a coach book a court to override a service's payment modes. That override still stands for staff and
for on-behalf bookings, and `sc_member_cannot_bypass_online_only` now asserts it through an admin plus the
coach's refusal by its own error code, so neither rule can silently lapse. Owner decision.

Guarded by `sc_a_rent_coach_lesson_raises_no_club_charge`.

---

## Courts, peak hours & equipment

### THE COURT IS THE ONE PLACE TO SEE A COURT (2026-07-29)

Setup → Courts & hours → a court now
carries everything about it: details + service allocation, **its own peak window**, playing hours,
and a READ-ONLY **"Pricing & payment"** summary of the court SERVICE it sits on (price per
duration incl. peak, payment methods, members-covered, packs) with one button into THE service
editor (`window.ServiceEditor`, mounted in place, returning to the court on close).
**Price/payment/cover are NOT editable on the court and must not become so** — they belong to the
SERVICE, which several courts share (eight hard courts are one price list). Editing them per court
would either fork the model or silently mean "change this for all eight", which is worse than
sending the owner to the place that says so. Summarise here, edit there.

### PEAK HOURS ARE PER COURT (2026-07-29)

The peak AMOUNT was always per service+duration
(`billing.price.peak_amount_minor`); only the WINDOW was club-wide, so "peak on the show courts
only" was unexpressible. `diary.resource.peak_override` + `peak_days/start_min/end_min` give three
states — and **the third is why the flag exists**: `override=false` inherits the club window (every
court, unchanged); `override=true` makes the court's own window authoritative **including when it
is EMPTY**, which is how a club with peak hours marks a court never-peak. A nullable window alone
could only ever ADD peak, never remove it. `pricing.in_peak_window(..., resource_id=)` resolves it
and **BOTH price paths must pass the court** — `availability._slot_price` (what the grid shows)
and `_create_order_guarded._price` (what the order charges) — or the grid quotes the club window
while the booking charges the court's. Edited at Setup → Courts & hours → a court → "Peak hours
for this court". Guarded by `sc_peak_hours_can_differ_per_court` (asserts shown == charged for all
three states).

### EQUIPMENT IS SCOPED TO A COURT SERVICE, AND THE COURT IS STILL CHARGED (2026-07-29)

Equipment
was club-wide — every item offered on every court booking whatever service it belonged to, so
clay-only kit could be hired on a hard court. `diary.equipment_service` links an item to the court
SERVICES it's offered on, many-to-many, and **NO ROWS MEANS ALL SERVICES** so every pre-existing
item is unchanged. The picker filters (`GET /api/diary/equipment?court_product_id=`), and
`create_booking` re-checks server-side (`EQUIPMENT_NOT_FOR_SERVICE`) because `addons` arrives off
the request body — the same reason a posted `product_id` is validated rather than trusted.
`?starts=&ends=` returns `available` per item so the stepper clamps to what is FREE for the slot,
not to what the club owns (it used to let a client pick 4 racquets already out and refuse at the
very end). **The COURT is charged in full alongside the kit unless a membership genuinely covers
it** — a covered court + kit is an order for the kit ONLY, which is indistinguishable from a leak
by looking at the order, so all three cases are pinned: PAYG pays, covered doesn't, and covered
OVER THE CAP pays again. Both the court (GiST) and the kit (time-overlap count) are reserved, so
neither double-books. Guarded by `sc_equipment_court_is_charged_and_both_are_booked_out` +
`sc_equipment_is_scoped_to_its_court_service`.

### EQUIPMENT IS A SERVICE AND PAYS LIKE ONE

It rides the court's order, so where the court was
FREE (`membership_covered`) or prepaid (`token`) `_create_order_guarded` **hard-coded the order to
`at_court`** to collect the fee — assumed, never checked — and `create_booking` had already picked
`confirmed` from the COURT's free mode. A card-only club therefore got an owed pay-at-court debt
for the ball machine on a confirmed booking nobody could collect against: the "equipment always
comes as pay at court" report. Now `equipment.quote` prices the kit and intersects every requested
item's own `payment_modes` BEFORE any insert, `create_booking` resolves a method the club **and**
the kit both offer (empty → `EQUIPMENT_NOT_PAYABLE`, refused not granted — the pack rule), the hold
decision is made from BOTH modes, and the response carries **`requires_payment`** because the
client can no longer infer it from its own choice (`booking.js` redirects to Yoco on
`st.settlement === "online"`). Set the modes at Setup → Equipment hire. Guarded by
`sc_equipment_follows_its_own_payment_rule`.


---

## The lesson lifecycle

### THERE IS ONE LESSON FLOW (2026-07-29)

A lesson is booked exactly like a court: it holds the
coach AND a court immediately, and the SETTLEMENT alone decides `held` (online, pending payment)
vs `confirmed`. **Nothing about the coach changes the shape of the booking.**
`iam.coach_profile.review_bookings` no longer gates anything.
The gate it replaced created a `requested` lesson that reserved NOTHING and raised no order — a
different booking, a different notification and a different money path for the same act — so a
card-only coach was unbookable (no order ⇒ the client is never sent to checkout, and `can.pay`
needs an order), and TWO clients could each hold and pay for one slot. It also wasn't buying what
it looked like: **a coach can RESCHEDULE or CANCEL**, so a bad time is moved or returned, not
refused up front. Guarded by `sc_one_lesson_flow` + `sc_paying_is_the_acceptance`.

### THE COACH IS TOLD, ONCE, ABOUT EVERY LESSON

`_emit_confirmed` → **`lesson_booked`**, addressed
to the coach (in-app + email: "open it to reschedule or cancel"). Who got told used to depend on
the settlement mode AND the review flag — only a gated booking emailed him directly, elsewhere he
was a BCC on the CLIENT's receipt, and a client who made a request got nothing at all. **The coach
BCC is dropped entirely** — a class now emits `class_booked` to him the same way, so nothing is
left to blind-copy. An ONLINE lesson confirms in the PAYMENT path, which `_emit_confirmed` never reaches,
so `billing.events` calls `notify_coach_of_confirmed_order` — without it the coach would hear
about every lesson EXCEPT the paid ones.

### A PAID lesson cancelled BY THE CLUB refunds itself

Cancel used to void owed orders and leave a
paid one intact ("refunding is a separate explicit flow") — fine while a coach who didn't want a
lesson DECLINED it and that path refunded. With the gate gone **cancel IS how a coach returns a
lesson**, so leaving the money would keep payment for a lesson the club just cancelled. A CLIENT's
own cancellation is deliberately NOT auto-refunded — that is a request decided under the
cancellation policy (`was_paid` still flags it).

### accept / propose / decline are GONE (deleted 2026-07-29 once production's queue was empty)

The approval lifecycle, its four email templates, the coach's approval queue, the client's
"needs your attention" blocks and the `requested`/`proposed` statuses were all removed — the
status CHECK is now `held|confirmed|cancelled|completed|no_show`, narrowed inside a **guarded**
DO block (it runs every boot; an ALTER that fails takes the deploy with it, so it only narrows
when no such rows remain). If a lesson ever needs approving again, do NOT restore the gate —
the coach reschedules or cancels, and a paid cancel refunds.

### A lesson email must state THIS booking's state, not the usual one

`lesson_accepted` always
read "Your lesson is confirmed" — including when the booking was HELD and unpaid, so the one
email that could have prompted payment said there was nothing to do. It now says "one step left"
when `status='held'`, and `lesson_declined` says the money is coming back when `refunded`. Both
read state the PRODUCER passes on the payload (`_payload` carries `status`; `_lesson_event(...,
extra=)` carries outcomes) — emit dispatches on a background thread whose session cannot see the
caller's uncommitted work.


---

## Classes

### A class name can NEVER break the class — enforced at THREE layers (2026-07-26)

A class is two
linked rows (`billing.product` = the service the editor renames + `diary.resource` = the class type
the diary schedules against), joined by `diary.resource.product_id`, with the name duplicated in both.
(1) **Identity, not names:** EVERY class resolver — `list_class_types`, `class_type_dict`, pricing,
`update_class_type` — resolves via `product_id` and DISPLAYS `billing.product.name`; the last
name-based resolver (`_class_product_for_resource`) was DELETED. A stale resource name can't affect
resolution. (2) **The DB makes drift impossible:** a trigger `diary._sync_class_resource_name` mirrors
`billing.product.name` → `diary.resource.name` on any UPDATE — so a rename via the service editor, the
diary edit, a SCRIPT or a manual SQL all stay in lockstep. (3) **Boot heals legacy:** `diary/schema.py`
backfills NULL `product_id` links (conservative, by name+coach) and force-syncs any linked-but-drifted
name every deploy. An unlinked+renamed class (the orphan) is a human call — `scripts/reconcile_class_names.py
--link-orphans` pairs a lone renamed class to its lone service. Editing a class name is SAFE and must
never be disabled. Guarded by `sc_class_name_cannot_break_the_class` (incl. a RAW-SQL trigger proof).

### A CLASS resolves its service through `diary.resource.product_id`, NEVER by joining on names

`create_class_type` links the resource to its `billing.product` at birth, and `diary/schema.py` boot-
backfills legacy rows (conservatively — only a NULL link with exactly ONE name+coach match; ambiguous
or already-drifted rows are left for a human). The old name join broke silently on a service RENAME
(which updates `billing.product.name` only), leaving `class_session.price_id` NULL → a kind-level
fallback billed the class at some OTHER class's rate under that class's payment rules. Resolvers:
`_class_service_product_id` (durable link) · `_class_product_for_session` (price → else resource) ·
`_class_effective_price_id` (re-resolves when the frozen price row was RETIRED, so a removed
variation can't bill R0). `enrol` refuses `PRICE_NOT_CONFIGURED` like `create_booking` does.

### A CLASS HAS THREE VERBS, AND CANCEL GIVES THE MONEY BACK (2026-07-29)

The class lifecycle got
the same treatment as the lesson one, and had three holes:
(a) **`reschedule_session` — the verb that didn't exist.** A class could be created, scheduled
forward and cancelled, never MOVED. A coach needing one session shifted an hour had to cancel it —
which releases every player and refunds — and re-schedule, losing the roster; so in practice the
session just ran at the wrong time. It is deliberately the same shape as
`bookings.reschedule_booking` and REUSES the same guards: `_coach_busy_at` (excluding this session
so it can't block itself) → `COACH_NOT_AVAILABLE`, and courts re-reserved through
`_reserve_courts_for_class` so the GiST exclusion, the busy-court auto-repick and the court-grid
visibility all behave as they do at schedule time. **The old holds are released inside the SAME
savepoint that re-takes them** — a small move (11:00→11:30, same court) overlaps itself, so the old
hold must go first or it GiST-blocks its own session; a failed re-take unwinds via `_NoCourt` and
the class is left exactly as it was. A class that HELD courts and can secure none at the target
**REFUSES (`NO_COURT_AVAILABLE`)** rather than half-moving: `schedule_sessions` may drop a court
when laying out a term, but a class people have PAID for may not move to a time it can't run at.
Enrolments/orders/waitlist are untouched — the seat follows the session. Routes: `PATCH
/api/admin/classes/sessions/<id>` (+ coach, own sessions only — a coach may not reassign the class
to another coach, so `coach_user_id` is deliberately not read off his body). UI: a **"Move"**
action on the sessions table, reusing the shared `CRMUI.rescheduleModal` — `canChangeCourt` became
config-driven rather than `booking_type !== 'class'`, and a class on SEVERAL courts gets no
single-court dropdown (it would silently drop the rest; they move with it).
(b) **The coach is now told: `class_booked`**, addressed to him, fired at the ONE point a seat
becomes real — at enrolment when owed/prepaid, on PAYMENT when online (a hold that may lapse is not
news), and on waitlist promotion, plus the late-payment re-instate (which clears `held_until`, so
`confirm_paid_enrolments`' own query no longer sees that seat — it must emit from there or never).
He previously only ever got a blind copy of the player's "you're enrolled" receipt. **The coach BCC
is now dropped everywhere** (`notifications.deliver`), lesson and class alike.
(c) **Cancelling a class REFUNDS the paid seats.** `cancel_session` called `void_order`, and
`void_order` deliberately no-ops on a paid order ("a paid order must be refunded, not voided") — so
every online payer lost the seat AND the money, under a `class_cancelled` email that literally
promised "any payment will be refunded or credited". Now a paid order refunds
(`execute_order_refund`, per-player guarded so one gateway failure doesn't abandon the rest of the
cancel) and only an unpaid one is voided; the producer states `refunded` on the payload and the
email says what actually happened. All three guarded by `sc_class_session_lifecycle`.

### A WAITLIST PROMOTION CANNOT CONFIRM AN UNPAID CARD-ONLY SEAT

`_bill_promoted_enrolment`
rewrote an `online` intent to `at_court` ("async promotion can't drive a checkout") and
`_promote_waitlist` then marked the seat `enrolled` and emitted `class_enrolled` — on a card-only
class, a confirmed seat plus a "you're enrolled" email against an owed order, straight past the
payment gate `enrol` enforces two functions up. The downgrade now happens ONLY when the class
actually offers at-court; otherwise the seat is billed `online`, stamped `held_until` (the same
lazy-expiry rails as an online self-enrolment, so an unpaid promotion frees the seat and promotes
the next player) and emits **`class_seat_awaiting_payment`** instead — `class_enrolled` stays the
single confirmation and fires from `confirm_paid_enrolments` when the charge lands. Guarded by
`sc_waitlist_promotion_into_a_cardonly_class_is_held`.


---

## Memberships, the trial & entitlement caps

### THE SIGNUP TRIAL IS A TIER-LEVEL FLAG, AND A TIER IS SEVERAL PRICES

`billing.price.is_trial` /
`trial_days` mark the tier a brand-new member is granted (Setup → Memberships → a tier → **"Signup
trial"**); `grant_signup_trial` prefers it and uses its `trial_days`, and `membership_plans`
excludes it from sale. Exclusivity (`_make_sole_trial`) clears **OTHER TIERS, never sibling
TERMS** — a tier is one price per term (1/3/12 months) and the editor saves it by PATCHing each
term in turn, so clearing by `p.id <> :pid` had every save undo the previous ones and only the
LAST term kept the flag. The trial still worked (the grant finds any flagged row) but the editor
reads the FIRST term, so re-opening showed the toggle OFF — tick, save, still off, forever. Tiers
are matched by `membership_tier` (`IS DISTINCT FROM`, so NULL-tier legacy rows group together).
Guarded by `sc_signup_trial_is_a_tier_level_flag`.

### THE TRIAL IS A MEMBERSHIP — it has no separate court rules

`provider='trial'` goes through the
SAME resolver as a paid tier: the court service's **`members_covered`** flag, the duration/day caps
and the access window all apply identically. So "is clay in the free trial?" is answered by ONE
switch — Setup → Services → the court service → **"Included with a membership?"** (owner-only,
`billing.product.members_covered`, already wired through `services/` read+PATCH). Turning it off
makes that court PAYG for members AND trialists; the booking is never blocked, just billed.
Resist any urge to special-case the trial — a club would be giving its most expensive courts away
to every new signup. Guarded by `sc_trial_obeys_the_same_court_rules_as_a_membership`.

### `/api/me/plan` MUST REPORT THE CAP THE SERVER WILL ENFORCE (2026-07-29)

The booking UI hides
over-cap durations using `membership_status.max_covered_minutes`, while the ENGINE decides whether
to charge using `entitlement.active_caps` — which resolves a NULL tier cap to the **club default**
(`club.policy.default_max_covered_minutes`). `membership_status` returned the tier's RAW column, so
a club-level 90-minute cap was invisible to the UI: it kept offering a 2-hour court, told the
member "Covered by your membership", and the server then correctly charged for it. The owner sees
a cap that "isn't working"; the member gets a surprise bill. It now COALESCEs to the club default,
and a tier's own value still overrides. **Any new surface that shows entitlement must read the
EFFECTIVE cap, never a raw column** — shown == charged. Guarded by
`sc_plan_reports_the_cap_the_server_will_enforce`.

### MEMBERSHIP CAPS HAVE A CLUB-LEVEL FLOOR — the per-tier ones alone never reached the trial

The
caps live on `billing.price`, so they only applied to a tier that HAD a price row; the signup trial
usually doesn't (`grant_signup_trial` links one only when a trial TIER is configured), so trial
members were uncapped — and `active_caps._best` treated a NULL cap as "an unconstrained tier wins",
so merely HOLDING the price-less trial cancelled a paid tier's caps too. `club.policy.default_max_*`
is the floor every membership inherits (a tier's own value still overrides; NULL = inherit, as
`payment_modes` already works). **The owner's rule — one covered booking a day, 90 min max — is
exactly `default_max_covered_per_day=1` + `default_max_covered_minutes=90`**; no daily-minutes
accumulator exists or is needed. Every cap DOWNGRADES to PAYG, never blocks. Admin → Settings →
"What a membership includes (per day)". Guarded by `sc_club_default_caps_cover_every_membership`.

### `membership_started` is emitted from `billing.membership.emit_membership_started`, NOT from the gateway

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


---

## Pricing & payment rules

### A service's `payment_modes` is enforced SERVER-SIDE per the EXACT `product_id`

**A service's `payment_modes` is enforced SERVER-SIDE per the EXACT `product_id`** — resolve allowed modes
by the resolved service product, NEVER by `kind` alone (a kind-only resolve reads the club's default court
product and lets a card-only Clay court/pack/class be taken pay-at-court on an owed/unpaid order). Bookings
pass `product_id` to `_service_payment_modes_guarded`; packs use `billing.bundles.allowed_purchase_modes`
(no at-court fallback for a restricted pack — refuse if unpayable); `diary.classes.enrol` gates the mode
(and never lets a member conjure a free seat via `membership_covered`/`free`). Don't regress these to a
kind-level check.

### A DUPLICATE DURATION ON ONE SERVICE SILENTLY BILLS THE CHEAPER ROW (found live 2026-07-31)

`pricing.price_for` resolves the EXACT duration first and then tie-breaks on **`amount_minor ASC`**,
so two price rows for the same length are never both offered — the cheaper one always wins, in
silence. Production had a coach with **60 min R0.00 AND 60 min R600.00**: every 60-minute lesson
with him billed **nothing** (Club earnings: R12,680 billed, R0.00 in), and a second coach with
R550/R700 where the R550 quietly won. **A R0 variation on a paid service, and any duplicate
duration on one product, should be refused or loudly flagged by the service editor** — see
OUTSTANDING. Nothing in the code is wrong here; the DATA is, which is why no gate caught it and
only looking at the screen did.


---

## Refunds & the Yoco gateway

### ONE REFUND MODAL, AND IT OFFERS AN AMOUNT (2026-07-28)

`admin_app.refundModal` is THE dialog
for both ways a refund starts — the **Refund** action on a transaction record and **Approve &
refund** on a member's request — and it is the ONLY place `yocoRefund` / `approveRefundRequest`
are called. Partial refunds were supported by Yoco, `client.refund_checkout`, the route AND
`approve_refund_request` the whole way down, but **neither UI path ever sent an amount**, so a
part-refund was unreachable; approving also ran on `window.prompt`/`confirm`, which can neither
show a figure nor validate one. The modal now takes an amount (defaulted to what's still
refundable — collected MINUS already-returned), a note, and the cancel-booking choice.
**A FULL refund must still send NO amount** — an explicit figure equal to the total is the form
Yoco has refused before — so the modal omits it unless the figure is a genuine partial. DECLINE
stays a prompt (no money moves). Guarded by `sc_partial_refund_reaches_yoco_as_a_partial`.

### A REFUND'S IDEMPOTENCY KEY MUST NOT OUTLIVE A FAILED ATTEMPT (2026-07-28)

The Yoco adapter
keyed refunds `refund:{checkout_id}:{int(amount_minor or 0)}` — and a FULL refund passes
`amount_minor=None`, so `int(None or 0)` collapsed to **0**: ONE FIXED KEY per checkout, for all
time. Yoco honours `Idempotency-Key` by REPLAYING the response first stored against it, so once
any attempt failed, **every retry replayed that failure forever** regardless of what changed —
while the Yoco dashboard refunded the same payment without complaint. It presented as an
unchanging "insufficient funds" against an account with plenty in it, which sends you hunting a
balance problem that does not exist. The key now carries a **minute bucket**
(`refund:{ch}:full|{amount}:{YYYYMMDDHHMM}`): still collapses an accidental double-submit, no
longer blocks a deliberate retry. **The real double-refund guard belongs in OUR ledger, not the
gateway key** — `execute_order_refund` refuses `already_refunded` / `refund_exceeds_payment` by
summing `billing.payment` refunds against the charge, without calling Yoco at all. A frozen key
was never protecting the money; it was only preventing the retry. Guarded by
`sc_refund_retry_is_not_poisoned_by_the_idempotency_key`.

### A REFUND NEEDS EVIDENCE OF A CARD PAYMENT, NOT A CHECKOUT (2026-07-28)

`billing.payment_attempt`
is written the moment a member taps "Pay online" — **BEFORE any money moves** — so it proves an
INTENT, never a payment. An order whose member started an online checkout, abandoned it and then
settled at the desk (or had a coach collect it, or an admin mark it paid) still carries that `ch_`
id while the money arrived elsewhere. `execute_order_refund` looked ONLY for that intent, so it
asked Yoco to return money it never took — and **Yoco answers "insufficient funds" about THAT
CHECKOUT's balance, not the merchant's**, which reads as an empty Yoco account and gets chased in
entirely the wrong place (a club with a day's takings sitting in Yoco still sees it). It now checks
`billing.payment` for a succeeded **yoco** charge first and refuses with a message NAMING the real
method (`not_paid_by_card`) — but ONLY when another provider positively succeeded. **No payment
rows at all is AMBIGUOUS, not refused**: the charge may sit on a 'Pay all' wrapper or a webhook may
never have been recorded while the money is genuinely at Yoco, so it reaches the gateway and lets
Yoco decide. (The already-refunded check is likewise gated on `charged > 0` — `0 >= 0` would
otherwise read an empty charge total as "already refunded in full".) **The order's own facts are checked BEFORE
`get_gateway`** — "this was never a card sale" is true whether or not Yoco is reachable, and
answering "online payments are not available" to that question helps nobody. Diagnose with
`scripts/diagnose_refund.py`. Guarded by `sc_refund_refuses_an_order_never_paid_by_card`.

### A REFUND MUST TARGET THE CHECKOUT THAT HOLDS THE MONEY, NOT THE FIRST ONE CREATED (2026-07-28)

`POST /checkout` mints a FRESH Yoco checkout on every call and writes a `billing.payment_attempt`
row — **there is no reuse guard** — so a member who taps Pay, abandons, returns and pays leaves TWO
`ch_` ids on one order and only the second carries money. Both `execute_order_refund` and
`reconcile` picked `ORDER BY created_at ASC LIMIT 1` — the **oldest**, i.e. the abandoned one. Two
consequences, both money: a refund aimed at an uncollected checkout fails and **Yoco reports it as
INSUFFICIENT FUNDS**, which reads exactly like the club's own Yoco balance being empty (so it looks
like a banking problem, not a wrong id); and missed-webhook RECOVERY silently gave up on any order
with a retry — reconcile asked about the abandoned checkout, was correctly told it was never paid,
and left real money unrecovered. **`reconcile.paid_checkout_id_for_order` is now the ONE resolver**
both use: one attempt → returned with no API call (the common case, unchanged); several → ask Yoco
newest-first and take the one it says completed; unreachable → fall back to the NEWEST (the old
ordering had it backwards). Ordering carries an `id` tiebreak because `now()` is the TRANSACTION
timestamp, so same-transaction rows are indistinguishable by time. Guarded by
`sc_refund_finds_the_checkout_that_holds_the_money`. **NOTE:** "insufficient funds" is *also*
genuinely what Yoco says when the club's Yoco balance can't fund the refund (refunds draw on the
balance, not the bank) — check whether the order has >1 `payment_attempt` row before assuming which.

### A successful charge may NOT re-open a CLOSED debt

**A successful charge may NOT re-open a CLOSED debt.** `_mark_order` was an unconditional UPDATE, so a
late/replayed `charge_succeeded` flipped **any** status to `paid` — and Yoco retries for 72h while
reconcile sweeps 100 days back, so 'late' is routine. `refunded`→`paid` re-books returned cash as
collected revenue; `written_off`→`paid` silently reverses the club's own decision; `void`→`paid`
resurrects a cancelled sale. `_mark_order_paid` allows only `open`/`awaiting_payment`/`paid`, **plus
the one void that IS recoverable** — a lapsed hold (`order_void_is_recoverable`, the SINGLE source of
truth; `yoco_billing.reconcile._is_expired_hold_void` delegates to it so the two can't drift apart and
silently widen the door). A refusal still RECORDS the payment (cash stays visible) but skips the whole
fan-out — no booking confirm, no pack grant, no commission, no "payment succeeded" email — and returns
`needs_attention='payment_on_closed_order'` for a human. Guarded by
`sc_payment_cannot_reopen_a_closed_debt`.

### Reconciliation must ACTIVATE, not just settle

**Reconciliation (missed-webhook recovery):** `yoco_billing/reconcile.py` — `client.get_checkout` asks Yoco;
a `completed`+`paymentId` replays `charge_succeeded` (idempotent). `POST /api/billing/yoco/reconcile/<order_id>`
+ `POST /api/cron/reconcile-payments`. **Recovering the payment is NOT enough — the purchase must also be
ACTIVATED.** Both the webhook AND reconcile call the ONE shared `yoco_billing/activation.py::activate_purchase`
(activate the membership/pack + emit `bundle_activated`); it's idempotent and runs even on an `{ignored}`
replay, so a webhook-after-reconcile REPAIRS an un-granted pack. **Never let reconcile settle without calling
it** — the historic gap left online packs `paid` but `pending`/unusable with no email (Render Free sleeps →
webhook missed → reconcile is the common path). Remediate stragglers with `scripts/fix_bypassed_packs.py`.

### Refund REQUESTS are decided on the transaction record, not in a separate queue

**Refund REQUESTS are decided on the transaction record, not in a separate queue.** Money → **Refund
requests** is an INBOX: each row opens `#/txn/<order_id>`, where a `Decision needed` banner offers
Approve & refund / Decline beside the payment history and audit trail. Deciding used to happen in the
list itself via `window.prompt`/`confirm` — no order context, nowhere to go when the gateway refused.
Three fixes behind it: (a) `approve_refund_request` passed the member's REQUESTED figure to Yoco, so
the ordinary "give me all of it back" sent an explicit amount equal to the order total and Yoco
refused — while the transaction record's button (which sends **no** amount = full refund) worked
seconds later; anything that isn't a strict partial now resolves to a full refund. (b) A direct refund
now calls `refunds.resolve_pending_requests_for_order`, so the member's ask closes instead of nagging
forever for money already paid back. (c) The queue hid any request whose order was `void` — but **void
≠ the money came back**, and `_mark_order_paid` deliberately leaves a succeeded charge on a void order;
`_PENDING_STILL_REFUNDABLE` keeps those visible, hiding only `refunded`/`written_off`. The home
approvals count no longer reports a FAILED read as `0` (`refund_requests_error`) — a false all-clear on
money is indistinguishable from the real thing. Guarded by `sc_refund_request_visibility`.


---

## Invoicing & the month-end close

*Added 2026-08-08, after an owner review of the whole month-end process. Every one of these was
reported as "the invoices don't balance" or "outstanding looks wrong"; all four turned out to be one
missing idea — an invoice with no PERIOD — plus the debris it left behind.*

### VOID MEANS CANCEL — AND THAT WAS A DELIBERATE REVERSAL (2026-08-08)

`void_invoice` originally voided the DOCUMENT only and left every charge owed, because an invoice
renders over live orders and is never a second debt store. Technically right; it read as a bug. The
owner voided an invoice for R12,680, saw the balance unchanged, and reported it — one word meaning
two things on the same screen. Relabelling it ("Void document", a prompt naming the money that would
stay owed) was the first attempt, and it was not enough: the mental model is *void = cancel*, and
making someone perform two actions for one intent is how mistakes happen.

So the DEFAULT now cancels the charges with the document. **`cascade=False` is not decoration** — it
is the only way to void a document you intend to RE-ISSUE (wrong bill-to, wrong period, a missing
line). Cascading there would destroy the charges, and **nothing un-voids an order**: the debt would
be gone for good. It is surfaced as a second button, "Void, keep charges". A PAID charge is never
touched either way, so voiding a part-paid invoice cancels what is still owed and leaves the money
that arrived alone. Both directions are asserted, so neither can silently flip back.

Guarded by `sc_bulk_void_cancels_charges_not_just_the_document`.

### AN INVOICE COVERS ITS OWN MONTH, OR NO MONTH CAN EVER BE CLOSED (2026-08-08)

`month_end_targets` and `invoicing.open_order_ids` filtered on `status='open'` with **no date bound at
all**, so a "month-end" invoice was a photograph of everything owed at the instant the sweep ran.
`billing.invoice.period_label` existed — but only as a LABEL; nothing selected on it. Both reported
symptoms fall out of that one fact: a lesson played after the sweep was **missing from the document
while the client's live balance already counted it** (the invoice "not balancing"), and an unpaid
June debt **rode onto the July invoice**, so no month was ever closed and "what is still outstanding
for July" had no answer in any view. A charge belongs to the month the SERVICE WAS DELIVERED —
`invoicing.DELIVERED_AT_SQL` is the ONE resolver (booking session → class session →
`order.service_date` → `created_at`), and it must stay the one, because
`billing.me.activity_summary` already buckets by the session's month: two resolvers means the invoice
and the client's own summary disagree by construction. `order.service_date` exists for a charge with
no session — production carries a July order described "Lessons - April -", a catch-up bill that
would otherwise date itself to July for ever. Earlier debt is **never re-billed**:
`brought_forward_minor` freezes it at issue as DISPLAY ONLY, never an `invoice_line` and never in
`total_minor`, because those orders are already invoiced on their own month's document and a line
with no `order_id` could never be settled by `mark_invoice_paid`. Guarded by
`sc_an_invoice_covers_its_own_month`.

### BILL THE MONTH AFTER IT ENDS, AND LET A MONTH SWEPT EARLY BE CLOSED (2026-08-08)

The sweep ran on the **25th** and defaulted to the CURRENT month, so every invoice was issued with
five or six days still to come — it could not be complete, whatever else was fixed. It now runs on
the **1st** for the month just ended (`month_end_period` defaults to `now() - 1 month`). That leaves
one hole worth knowing about: a month swept BEFORE it ended has `month_end_notice` rows saying the
client was already notified, so an ordinary re-run returns `already` and the late arrivals are
invoiced by **nothing, ever** — 55 such orders in July 2026. `reissue=True` (a `workflow_dispatch`
input and a body flag on `/api/cron/month-end`) ignores that claim; `issue_invoice` still skips any
order on an ACTIVE invoice, so the second pass bills only what is still uninvoiced — a supplementary
invoice, not a duplicate. A reissue with nothing new is SILENT — it neither issues nor
re-sends, because the ordinary sweep's helpful "you owe money and have no fresh document, here
is your existing one" fallback becomes ~21 unwanted emails when you are closing a month.
Guarded by `sc_a_month_swept_early_can_still_be_closed`.

### ONE PAYMENT IS ONE RECEIPT, HOWEVER MANY LINES IT SETTLES (2026-08-08)

`mark_invoice_paid` loops every order the invoice covers and calls `record_desk_payment` on each;
each emits `payment_succeeded`, and the notification engine turned every one into an email. Settling
a 12-lesson month by EFT sent the client **TWELVE "Booking confirmed" emails for ONE payment**, and
the owner had to receipt each one by hand. (The ONLINE path was always fine — a 'Pay all' wrapper
settles its children and emits once.) A settled order now carries `settlement_batch` through to the
payload and `notifications.deliver` returns early on it, while `mark_invoice_paid` emits ONE
`invoice_paid` — which is also the only place that can state the real figure, because no individual
order knows the payment total. **The suppression is in the NOTIFIER, not the producer**: the event
must still reach `core.usage_event`, Klaviyo and the offline-conversion recorder; only the
client-facing noise stops. Guarded by `sc_one_payment_one_receipt`, including the control that the
same payload WITHOUT the batch key still notifies.

### A PART PAYMENT SETTLES WHOLE LINES, OLDEST FIRST — IT NEVER PART-SETTLES AN ORDER (2026-08-08)

"Full closes the invoice, partial leaves it open", with the debt model intact. An ORDER is never
part-settled — one debt = one order settled once, which is exactly why `record_desk_payment` refuses
a short amount — so `mark_invoice_paid(amount_minor=)` clears whole LINES and the remainder stays
owed, and the document derives "Partially paid" from its own orders rather than storing a second
figure that could disagree. Two traps, both hit while building it: **`ORDER BY created_at` cannot
order these** (several orders are routinely raised in ONE transaction, where `now()` is identical for
all of them — the same trap that made the refund path pick the abandoned checkout), so it orders by
delivery date then `created_at` then `id`; and the loop must **STOP at the first line it cannot
cover, never skip ahead to a smaller one** — paying R800 against R500/R300/R200 must clear the R500
and R300 and leave the R200, not settle a NEWER R200 and leave the older R300 open. Money that
cannot fill the next line comes back as `unallocated_minor` for a human to place, never silently
absorbed. Guarded by `sc_partial_payment_leaves_the_invoice_open`.


---

## Money custody & the coach ledger

### THE CLUB CAN ONLY RECEIVE YOCO AND EFT (2026-07-29)

It has no facility for collecting on a
coach's behalf, so anything else recorded against a COACHING order is cash the coach took from the
client directly. ONE rule, ONE function — **`billing.commission.cash_custody_for(provider)`**:
`'club'` for `yoco`/`eft`, `'coach'` for everything else *including no provider at all* (an
arrears collection writes no `billing.payment` row). Read by `record_split_for_order` (the ledger
DIRECTION), `coach_settlement` (the statement) and `coach_sessions_by_day` (the work log), so the
three can never drift. **The provider is the only thing factually known** — `recorded_by_user_id`
can't decide it, because `/api/billing/desk-payment` is `club_admin`-only and every desk payment
is admin-recorded whoever actually took the note. `record_split_for_order` used to hard-code
`cash_held_by='club'` for EVERY path, so a lesson settled in cash at the court booked the coach's
net as owed TO him while he stood there holding it — wrong by the whole gross, the same shape as
the arrears inversion below. Historical rows: `scripts/fix_desk_cash_coach_ledger.py` (dry-run
default, idempotent, appends a correcting adjustment). Guarded by
`sc_only_yoco_and_eft_reach_the_club`.

### THE COACH LEDGER'S DIRECTION FOLLOWS WHO HOLDS THE CASH (2026-07-28)

`billing.coach_ledger` is
SIGNED — **+ = the club owes the coach, − = the coach owes the club** (the rent entry is
deliberately negative). A `commission_earning` of `+coach_net` is correct ONLY when the club took
the gross and is holding it. But `mark_arrears_collected` — **off-platform by definition**
(docs/specs/01: the coach chases the EFT into their OWN account) — posted the SAME `+coach_net`.
The coach already held the full gross, so the club was owed its commission and the ledger said the
exact opposite: **wrong by the whole gross, every time**, surfacing as "Coach payouts due" telling
the owner to pay a coach who was holding the club's money. `_write_split_pair` now takes
**`cash_held_by`** — `'club'` → `+coach_net` as `commission_earning`; `'coach'` → **`−owner_cut` as
`commission_due`** (a new entry type; no read filters on `commission_earning`, balances just
`SUM(amount_minor)`, so it flows through everywhere). **The `commission_split` rows are IDENTICAL
either way** — the sale was divided the same whoever held the cash — so commission REPORTING
(`cockpit_coach_earnings`, the Money P&L) is untouched; only the running balance changes, which is
the thing that was lying. Historical rows: `scripts/fix_inverted_coach_ledger.py` (dry-run by
default) appends ONE correcting `adjustment` per coach rather than rewriting history, idempotent on
a fixed `ref_id`. Guarded by `sc_ledger_direction_follows_who_holds_the_cash`.

### "PAID" IS NOT "IN THE BANK" — Money → Coach statement is the split

`order.status='paid'` merges
Yoco + EFT (the club's bank — the ONLY two ways it can receive) with cash/card taken at the court (the
COACH; there is no facility for collecting on his behalf) and
a coach's off-platform collection (the coach only). The last is **exactly derivable**:
`mark_arrears_collected` flips the order to `paid` with **no `billing.payment` row at all** (the
money never touched the platform), so *paid + zero succeeded charges* == the coach collected it.
`admin.repositories.coach_statement_report` classifies off the ORDER (not the payment rows) so the
custody buckets sum EXACTLY to the Money tab's `paid`, and pairs it with `payments_received` — an
INDEPENDENT read of `billing.payment` by landing date, which is the bank-reconciliation figure and
deliberately not derived from the fold (it sees money the order CTE excludes, e.g. both sides of a
settled 'Pay all' wrapper, and counts when cash landed, not when the sale happened).

### CLUB EARNINGS AND THE COACH STATEMENT MUST AGREE ON WHERE THE MONEY IS (2026-07-31)

Club
earnings had no concept of custody: `_earnings_cte`'s `collected` was `status='paid'`, i.e. "the
CLIENT settled" — and it was rendered under a headline reading **"Collected so far · banked"**. For
a coach who collects at the court that is the club's money in HIS pocket. On production one coach
showed **R20,200 "in"** of which **R15,950 was never banked**. The CTE now carries `in_bank`
(EXISTS a succeeded `yoco`/`eft` charge — the same rule as `commission.cash_custody_for`), the fold
splits into **`banked_minor` + `coach_held_minor`**, and both the coach P&L and the club roll-up
carry them. **`banked + coach_held == collected`**, so the Money band and Home still reconcile and
nothing double-counts. Guarded by `sc_club_earnings_agrees_with_the_coach_statement` — which also
pins that the STATEMENT reports the identical split for the same coach+month.
**The `mark_arrears_collected` path writes NO payment row, so it classifies coach-held even under a
broken provider test** — a scenario must include a CASH desk payment or it isn't testing the rule
(found by re-breaking: the first version caught nothing).

### THE COLLECTED FIGURE MUST SAY WHAT IT WAS (2026-07-30)

"Paid to the club R17,000" against the
R6,000 of lessons an owner remembers reads as a threefold error. It isn't one: a lesson/class PACK
is deliberately hung on the coach's own lesson/class `price_id` so its commission attributes to him,
which means the **FULL pack price** lands in the collected figure at the moment of **SALE** (pack
revenue is sale-based, not spread over the sessions drawn from it) — and CLASS SEATS are in there
too. `st.by_kind` breaks it into lesson / class / pack and the statement renders it under "Total
collected". **`basis` alone cannot produce that split** (a pack writes `lesson_commission`); a pack
is identified the way `_earnings_cte` does it — the order granted a `token_wallet`. Diagnose a
disputed figure with `scripts/diagnose_coach_statement.py --coach <name> --detail` (read-only; it
totals the month four independent ways). Guarded by `sc_settlement_says_what_the_money_was`.

### A PACK SALE RESOLVES ITS COACH FROM THE WALLET (2026-07-30)

`record_split_for_order` resolved
the earning coach as `product.coach_user_id or booking.coach_user_id` — but a pack has **no
booking**, so a pack sold on a SHARED (coach-less) service wrote `coach_user_id = NULL`: commission
accrued to NOBODY and the coach's own statement couldn't see the sale, while `_earnings_cte` (which
always resolved a pack via `token_wallet.coach_user_id`) showed the revenue against him. The two
now agree — `_wallet_coach_for_order` is the third fallback.

### THE COACH STATEMENT is the coach-side of a client invoice

**THE COACH STATEMENT is the coach-side of a client invoice** (`billing.commission.coach_settlement`
+ `coach_sessions_by_day`, rendered by the ONE shared `Widgets.CoachStatement`; admin and coach
differ by config only). Three blocks on TWO DELIBERATE DATE BASES: **sessions** by client by day
(bounded on the SESSION's date — "what did I teach"), **custody** (paid to club / collected by
coach / outstanding), and the **settlement** — total collected × commission = owed to the club,
minus what the club already holds, = net (bounded on when the money ARRIVED, per §D7). A lesson
taught in July and paid in August is outstanding in July and settles in August; the page says so.
The net equals the `coach_ledger` movement **by construction**, and `reconciles` asserts it on
every render — a mismatch shows a WARNING BANNER rather than a number nobody can check. Admin:
Money → Coach statement = summary + a coach dropdown (ONE coach at a time). Guarded by
`sc_coach_settlement_statement`.


---

## Reads that lie

### A SILENT ZERO IS A BUG, AND `try/except: return 0` IS NOT A GUARD (2026-07-31)

In Postgres a
failing statement ABORTS the transaction, so every query after it raises and returns its own
fallback. `admin_home` guarded each block that way: one broken query zeroed the People counts
(indistinguishable from "nothing needs attention") and errored the refund check, which is the only
reason it was noticed — the Refund-requests SECTION was fine throughout because it runs in a fresh
session. Use **`session.begin_nested()`** (a savepoint) and **log the block name**, NEVER a bare
`session.rollback()` inside a composer that runs in the caller's `session_scope`. **This exact
antipattern has now been found three times** — `client360`, `admin_home`, `coach_settlement`.

### `billing.me.activity_summary` buckets EVERYTHING by the SESSION's month

`paid_minor` used to
filter on the PAYMENT's own `created_at`, putting the money in a different month from the thing it
paid for whenever someone books ahead: an August session paid in July read "billed R400, paid R0"
in August and "paid R400, nothing played" in July — a fold that reconciles in NEITHER month, on
the client's own summary. One month view, one date basis. (Found because a harness scenario asked
for *today's* month while the fixture books 3 days out — **date-dependent assertions must derive
their date from the fixture**, or they fail for the last 3 days of every month and look like a
regression.)


---

## Email & notifications

### Transactional email = ONE confirm+receipt per purchase

**Transactional email = ONE confirm+receipt per purchase** (`marketing_crm/notifications.py::deliver`):
`booking_detail.load` resolves an order-keyed event (`payment_succeeded`) to its booking/class → the RICH
block (retitled "Booking confirmed"), else a purchase block for membership/pack. The client block always
names **"Booked by"** (the actor on an on-behalf/staff booking, the client themselves on a self-book; for a
class, the guardian when a child's seat is paid by them) and the client's **exact membership tier**
("Adult Anytime Play", via `_MEMBERSHIP_LABEL_SQL`) — PAYG simply omits the row. Guarded by
`sc_confirmation_email_block`; `deliver` SUPPRESSES the
`payment_succeeded` email for pack + class orders (their own email is the one). **`emit()` DISPATCHES ON A BACKGROUND THREAD WITH ITS OWN SESSION**, so anything the email re-reads
runs in a transaction that CANNOT see the caller's uncommitted work. A `payment_succeeded` email
therefore read the PRE-payment order status and labelled the confirmation from it — "Awaiting online
payment" on a paid booking, and once expiry began voiding abandoned orders, **"Cancelled" on a
payment that succeeded** (including every order a reconcile sweep recovers). The producer now states
the outcome — `payment_state="paid"` on the emit payload → `ctx["payment_state"]` →
`_pay_status(state_override=)` — and the email stops re-deriving it. **Any new emit whose email
reflects state the caller just wrote must pass that state explicitly.** Guarded by
`sc_email_payment_status_not_racy`. **Payment-status wording is
single-sourced** in `billing.statement.settlement_status_label(state, mode)` — email AND `client360` both
delegate, so a receipt/email/client-record never disagree. **Coach BCC only on his own lesson/class.** Every
order-keyed email needs `booking_detail.load` to import `text` (a missing import silently blanks the block).


---

## Infrastructure & environment

### Ten-Fifty5 embed — Render service names ≠ `render.yaml` `name:`

The live 1050 API is the Render service
**"Sport AI - API call"** (custom domain `api.nextpointtennis.com`), NOT the service literally named
`webhook-server` (that's a **cron**). Set env on the real service; the blueprint does **not** auto-sync env.
Federation trap: **`AUTH_ISSUER` (singular) vs `AUTH_ISSUERS` (plural)** — the multi-issuer allowlist is
`AUTH_ISSUERS` (a comma-list in the singular var is now tolerated, but use the plural); leave `AUTH_JWKS_URLS`
UNSET (JWKS derived from each issuer, no ordering to break). The nested-portal iframe needs the **multi-hop
relay** in `auth_client.js` (a middle frame proxies its grandchild's auth up) or nested pages fall back to
legacy → "Missing email or API key".

# BUSINESS-RULES — what it does and the rules

Every capability + business rule built, by domain. **White-label principle throughout: nothing is
hardcoded — every commercial value is owner-configured data.** Authoritative commercial decisions live
in [01-commission-and-coaching-decisions.md](01-commission-and-coaching-decisions.md).

## 1. Identity, roles & onboarding
- Roles: `platform_admin`, `club_admin`, `coach`, `member`, `guest`. Auth via Clerk; `iam.user` links
  by email so a seeded/invited person links on first login.
- **Auto-member:** any authenticated user with no membership becomes an **active `member`** of the
  club on login (defaults to PAYG). No more "no active club" for new sign-ups.
- **Owner onboarding** (wizard): club profile, location, branding, policy, courts, hours, services &
  prices, invite coaches. `club.onboarding_completed` gates first-run redirect.
- **Coach onboarding (4-step):** invited by the owner (`iam.coach_invite`) → on first login the coach
  completes profile/photo/bio + languages/qualifications/visibility + **preferred court**,
  weekly hours (creates their `diary.resource(kind=coach)`), and services/rates + classes/packs (fully
  pre-filled on return).

### Lifecycle states & real deletes (Active / Deactivated / Terminated)
Services, memberships and coaches share **ONE lifecycle vocabulary** — a filter bar + per-row
Deactivate/Reactivate/Terminate actions + status chips (UI: `UI.lifecycleBar` / `UI.lifeActions` /
`UI.statusChip`). **Deactivated** = configured but hidden from customers (still editable); **Terminated**
= retired. Backing: `billing.product.status` (`active`|`deactivated`|`terminated`, keeps `product.active`
in sync); a coach's three states map onto `iam.membership.member_status` + `iam.coach_profile.is_bookable`;
a membership tier's lifecycle derives from its term plans' status.
- **Real coach delete:** a coach with **no bookings / financial history** is HARD-deleted (invite,
  agreement, commission rules, `diary.resource`, coach_profile, membership all removed); otherwise the
  coach is **archived** (membership lapsed). `DELETE /api/admin/coaches/<user_id>` → `{ok, outcome}`.
- **Real court delete:** a court with **no bookings/sessions** is HARD-deleted; otherwise soft-archived
  (`is_active=false`, filtered out of the courts list). `DELETE /api/admin/resources/<id>` → `{ok, outcome}`.

## 2. The diary / booking
- **Services:** book a **court**, a **lesson** (**COACH-FIRST** — you pick the coach up front; there is
  deliberately no "Any coach", because services and rates are per-coach), or **attend a class**.
- **No double-booking:** a Postgres GiST EXCLUDE constraint guarantees one booking per resource per
  time; concurrent clashes → exactly one wins (`SLOT_TAKEN`).
- **Lessons reserve a court:** availability for a lesson = slots where a **coach AND a court are both
  free** (coach ∩ court); booking a lesson auto-holds a court (two rows, one `order_id`). The held court
  is **never billed separately** — the lesson's single order covers both, and the court row confirms
  alongside the lesson when the order is paid.
- **Semi-private (squad) lessons — PER-HEAD billing.** A lesson SERVICE can carry more than one client on
  the ONE slot (`billing.product.max_clients`, int NOT NULL DEFAULT 1, lessons only, 1–12; set via the service
  editor's "Semi-private (squad)" card). Each player gets their **OWN owed order at the service price — never
  merged**: `create_booking(extra_clients=[…])` records each extra as a `diary.booking_party` (role `partner`)
  plus a separate order linked via `order_line.booking_id` (the primary keeps `booking.order_id`). Billing
  follows **whoever pays** (`_bill_owner` → `iam.guardian_user_id_for`): the player if they're a member, else
  their **guardian** — so a parent's two kids on one squad raise **two** orders BOTH owned by the parent (spend
  rolls up to the payer, activity to the player). **Add a player later:** `add_lesson_partner`
  (`POST /api/diary/bookings/<id>/add-player`) + an **"Add player"** action on the shared event story
  (`Widgets.TransactionDetail`, offered only while the lesson is semi-private and below its cap). The player
  picker is staff-only (`GET /api/diary/members/search` → `iam.search_members_with_dependents`, members + a
  parent's kids as their own rows); a non-staff caller may add only club members + their OWN kids, staff any
  in-club member/child. **Cancel voids EVERY order on the booking**, not just the primary's.
- **Classes:** owner/coach create class types + schedule **recurring or one-off** sessions; capacity +
  **waitlist** (auto-promote the next person on a cancellation); rosters + attendance; shown on the
  master diary. A class **reserves N real courts** (court-blocking `booking_type='class'` rows under the
  GiST exclusion, with auto-repick when a desired court is busy).
- **A CLASS HAS THREE VERBS — schedule, MOVE, cancel (2026-07-29).** The class lifecycle got the same
  treatment as the lesson one, and had three holes:
  - **`reschedule_session` — the verb that didn't exist.** A class could be created, scheduled forward and
    cancelled, never MOVED. A coach needing one session shifted an hour had to cancel it (which releases
    every player and refunds) and re-schedule, losing the roster — so in practice the session just ran at
    the wrong time. It is deliberately the same shape as `bookings.reschedule_booking` and REUSES the same
    guards: `_coach_busy_at` (excluding this session so it can't block itself) → `COACH_NOT_AVAILABLE`, and
    courts re-reserved through `_reserve_courts_for_class` so the GiST exclusion, the busy-court auto-repick
    and the court-grid visibility all behave as at schedule time. **The old holds are released inside the
    SAME savepoint that re-takes them** — a small move (11:00→11:30, same court) overlaps itself, so the
    old hold must go first or it GiST-blocks its own session; a failed re-take unwinds and the class is left
    exactly as it was. A class that HELD courts and can secure none at the target **REFUSES**
    (`NO_COURT_AVAILABLE`) rather than half-moving: `schedule_sessions` may drop a court laying out a term,
    but a class people have PAID for may not move to a time it can't run at. Enrolments / orders / waitlist
    are untouched — the seat follows the session, and each player is emitted `class_rescheduled` carrying
    the OLD time as well as the new. Routes: `PATCH /api/{admin,coach}/classes/sessions/<id>` (a coach may
    move only his own, and may not reassign the class to another coach). UI: a **"Move"** action on the
    sessions table reusing the shared `CRMUI.rescheduleModal`; a class on SEVERAL courts gets no
    single-court dropdown (it would silently drop the rest — they move with it).
  - **The coach is told (`class_booked`)** — see §8.
  - **Cancelling a class REFUNDS the paid seats.** `cancel_session` called `void_order`, and `void_order`
    deliberately no-ops on a paid order ("a paid order must be refunded, not voided") — so every online
    payer lost the seat AND the money, under a `class_cancelled` email that literally promised *"any payment
    will be refunded or credited"*. Now a paid order **refunds** (`execute_order_refund`, per-player guarded
    so one gateway failure doesn't abandon the rest of the cancel) and only an unpaid one is voided; the
    producer states `refunded` on the payload so the email says what actually happened rather than promising
    it. All three guarded by `sc_class_session_lifecycle`.
- **Book-on-behalf:** a coach/admin can book FOR a client (owned by the client via `booked_for_user_id`)
  — this **auto-confirms** (the client is just notified, and can reschedule/cancel). **Book-for-a-child:**
  a parent picks a dependent in "Who's playing?" — the booking is FOR the child but **owned and billed to
  the parent**. **On-behalf pack auto-draw:** when a coach/admin books for a client who already holds a
  matching prepaid pack, the booking **draws that client's wallet** (settlement `token`, R0) instead of
  raising a new charge — a lesson matches a **coach-scoped** wallet, a class a **coach-agnostic** one. Staff
  on-behalf settlement is desk-only (at-court / monthly / the client's pack) — it **skips online (Yoco)**,
  and an online-only per-service preference does not restrict staff.
- **Back-capture (log a PAST session):** the SAME on-behalf flow opened for a past date. A coach/admin can
  log a lesson/class that already happened — it **bills the client + credits the coach** but holds **no
  calendar slot**. Staff-only: `allow_past` is role-gated AND requires an on-behalf booking, so a member can
  never back-date (that would dodge the booking window / late-cancel logic). A past lesson resolves the
  coach's resource from `coach_user_id` (no availability slot carries it).
- **THERE IS ONE LESSON FLOW — no approval gate (2026-07-29).** A lesson books exactly like a court: it
  reserves **coach ∩ court immediately**, and the **settlement mode alone** decides `held` (online, awaiting
  payment) vs `confirmed`. Nothing about the coach changes the shape of the booking.
  - The old gate (`iam.coach_profile.review_bookings` → a `requested` lesson reserving nothing, then
    accept / propose / decline) was **deleted**, along with the `requested`/`proposed` statuses, the four
    approval emails, the coach's approval queue and the client's "needs your attention" blocks. The status
    CHECK is now `held|confirmed|cancelled|completed|no_show`.
  - **Why it had to go:** a gated lesson raised **no order**, so a card-only coach was literally unbookable
    — the client was never sent to checkout and `can.pay` needs an order. It reserved nothing, so **two
    clients could each hold and pay for the same slot**. And it wasn't buying what it looked like: a coach
    can **reschedule or cancel**, so a bad time is moved or returned rather than refused up front.
  - **If a lesson ever needs approving again, do NOT restore the gate.** The coach reschedules or cancels,
    and a paid lesson cancelled **by the club** refunds itself.
  Guarded by `sc_one_lesson_flow` + `sc_paying_is_the_acceptance`.
- **Only configured services are offered.** The booking UI can only present what's been built: a duration
  is bookable iff it has an active `billing.price` row; a lesson is offered only where a **bookable coach**
  (weekly hours set, `is_bookable`) **and a court** are both free. No "minimum booking" rule can contradict
  a configured price.
- **Holds expire lazily** (no cron): abandoned `held` bookings past `held_until` are released whenever
  anyone checks availability or books.
- **Booking window / lead time / cancellation cutoff** come from `club.policy` (configurable).
- **Reschedule rules.** Rescheduling **re-prices** the order + owed coaching to the new duration (from the
  **same product**, so it's the coach's own rate for that length, never another coach's). But: a **paid**
  booking can't be **extended** into a longer/pricier slot (`PAID_CANNOT_EXTEND`, 422 — cancel and rebook to
  lengthen; a same-length or shorter move is fine), and a **membership-covered** court can't be moved to a
  time the membership doesn't cover for free (`NOT_COVERED_AT_NEW_TIME`, 422 — pick a covered slot or book a
  paid court). A **member reschedule of a LESSON must stay inside the coach's PUBLISHED hours**
  (`OUTSIDE_COACH_HOURS`, 422 — via `availability.resource_hours_cover`), matching what the picker already
  enforces on create; admins/coaches override. A lesson's auto-held court is **reassigned to a free court**
  at the new time.
- **A reschedule can move the COURT, not just the time** (`court_resource_id` on
  `PATCH /api/diary/bookings/<id>`). A **court** booking's own `resource_id` changes; a **lesson** stays on
  the coach resource and its held-court row moves instead. **A court move re-runs the MONEY guards a time
  move runs:** a court booking may not cross court **services** (`COURT_SERVICE_CHANGED` — it is priced by
  its service, and repricing happens on the SAME product so it could never correct the change), and a
  `membership_covered` booking re-runs the **full entitlement** against the target court
  (`COURT_NOT_COVERED` — the time-window check alone let a free booking move onto a clay court members are
  never covered for). Court moves are single-booking only (`COURT_MOVE_SINGLE_ONLY`), and a busy target
  refuses with `COURT_NOT_AVAILABLE` rather than a bare `SLOT_TAKEN`.
- **A RESCHEDULE RE-PRICES ON LENGTH, TIME *AND* COURT (2026-07-29).** Repricing fires whenever any of
  the three could have changed the price, and the new band is resolved exactly as it is at create:
  `reprice_booking_order(..., starts_at=, resource_id=)` → the club-local start + that court's peak
  window. Previously it fired only on a **duration** change and always wrote the **base** amount, so
  moving a booking into a peak window under-charged it and moving one out over-charged it — and since
  the peak window is **per court**, a court swap changes the price at an unchanged time too. Unpaid
  orders only: a **settled** order is never silently re-charged (that needs a refund). Guarded by
  `sc_peak_survives_a_reschedule`.
- **Courts on a lesson: the client picks the COACH, the club allocates the COURT.** A client never sees a
  court picker for a lesson (they do for court hire). Unassigned, `create_booking` takes the coach's
  **preferred court** (`iam.coach_profile.preferred_court_resource_id`) when free, else the first free
  court. It is a **preference, never a lock** — a busy favourite must never make a lesson unbookable; an
  explicitly-passed court always wins.
- **Late-cancellation fee.** When the club's cancellation policy applies at cancel time, a small **owed fee
  order** is raised on the client's statement (owner decision M6); cancelling voids the booking's own unpaid
  order so no phantom debt remains.
- **A PAID lesson cancelled BY THE CLUB refunds itself** (2026-07-29). Cancelling used to void owed orders
  and leave a paid one intact, on the reasoning that refunding is a separate explicit flow — fine while a
  coach who didn't want a lesson **declined** it and the decline path refunded. With the approval gate gone,
  **cancel IS how a coach returns a lesson**, so leaving the money would keep payment for a lesson the club
  just cancelled. A **client's own** cancellation is deliberately NOT auto-refunded — that is a request
  decided under the cancellation policy (`was_paid` still flags it so the UI can prompt).
- **Can't cancel a delivered session.** A member/guest may **not** cancel a lesson/class that has already
  **started** (`CANNOT_CANCEL_STARTED`, 422) — otherwise a delivered-but-owed booking could be cancelled
  after the fact, voiding its order and **erasing the debt**. Admins/coaches may still cancel a started
  booking (correction/no-show handling).
- **Can't complete a future booking.** A booking can't be marked **completed / no-show** before it has
  started (`CANNOT_COMPLETE_FUTURE`, 422) — completion attests a session was actually delivered.

## 3. Pricing (per-duration PAYG)
- A service carries **one `billing.price` row per offered duration** (`duration_minutes`,
  `unit='per_booking'`). `price_for(kind, duration)` resolves exact → nearest≤ → any.
- **Coach/product pricing is STRICT TWO-TIER (never merged).** For a lesson/class, a service uses the
  **coach's OWN active product if they have one, ELSE the shared (NULL-coach) product** — the two are never
  mixed. This governs `price_for` / `durations_for` / `payment_modes_for` / `services_for` **and** order
  creation in `create_booking`, so the coach's own rate card is applied **exactly** (their R400 60-min is
  charged as R400), there are no phantom durations or zero-rated "cheapest matching row" leaks, and there is
  **no "Any coach" R0 lesson** — a lesson is always coach-first. Classes charge the **enrolled session's own
  `price_id`**, so a client on coach A's class is never given coach B's cheaper class rate.
- **Per-service selection.** A lesson or class kind can have **several named services** (e.g. Private vs
  Semi-private), each its own product with its own durations + payment modes. The picker offers the specific
  service (`services_for` → a per-product list) and books that exact `product_id`; the two-tier coach scope
  above still applies.
- **No silent R0 order.** A billable booking whose duration has **no configured `billing.price` row** is
  **refused up-front** (`PRICE_NOT_CONFIGURED`, 422) — a delivered service must never fall through to a
  zero-rated order that's never owed. (Membership-covered courts are the only legitimate R0, resolved
  server-side.)
- **Court SERVICES (per-court-group court hire).** Courts can belong to distinct court services — e.g.
  "Hardcourt Hire" over the hard courts vs "Clay Hire" over the clay court — each a
  `billing.product(kind='court_booking')` with its **own** per-duration prices (multiple court products are
  now supported), **own** allocated courts (`diary.resource.product_id`), and **own** packs. A court's service
  resolves as the court's own `product_id`, else the club's single default court product, else unscoped
  (`diary.pricing.court_service_for_resource`). `price_for` / `durations_for` / availability / `create_booking`
  are court-service-aware (fixing the old "cheapest across court products" leak); a court booked under the
  wrong service is rejected (`COURT_NOT_IN_SERVICE`). **Single-court-service clubs are unchanged.** The client
  picks a court service like a lesson service and sees only its courts at its price; the owner allocates courts
  in Setup → Courts & hours (a "Court service" picker per court, `PATCH /api/admin/resources`) and creates a
  court service via "+ New" in Services.
- Seeded defaults (editable): Court 30/60/90/120 = R90/150/210/280; Lesson 30/60 = R250/400; classes
  per session. The legacy Wix "member R0 court" tier is gone.
- **PEAK court pricing — the WINDOW IS PER COURT (2026-07-29; court hire only).** The peak **amount** was
  always per service+duration (`billing.price.peak_amount_minor`); only the **window** was club-wide, so
  "peak on the show courts only" was unexpressible. `diary.resource.peak_override` +
  `peak_days`/`peak_start_min`/`peak_end_min` give **three states**, and the third is why the flag exists:
  - `peak_override = false` → **inherit** the club window (`club.policy.peak_*`) — every existing court,
    unchanged.
  - `peak_override = true` with a window → that court's **own** window is authoritative.
  - `peak_override = true` with an **EMPTY** window → the court is **never peak**. A nullable window alone
    could only ever ADD peak, never remove it — this is how a club with peak hours marks one court exempt.

  Resolved by `diary.pricing.in_peak_window(..., resource_id=)`, and **BOTH price paths must pass the
  court** — `availability._slot_price` (what the grid shows) and `_create_order_guarded._price` (what the
  order charges) — or the grid quotes the club window while the booking charges the court's. Membership
  coverage still wins first (covered inside the window is free; outside → the peak PAYG price). Owner:
  Setup → Courts & hours → a court → "Peak hours for this court". Guarded by
  `sc_peak_hours_can_differ_per_court` (asserts shown == charged in all three states).
- **THE COURT IS THE ONE PLACE TO SEE A COURT (2026-07-29).** Setup → Courts & hours → a court carries
  everything about it: details + service allocation, its own peak window, playing hours, and a **READ-ONLY**
  "Pricing & payment" summary of the court SERVICE it sits on (price per duration incl. peak, payment
  methods, members-covered, packs) with one button into the service editor.
  **Price / payment / cover are NOT editable on the court and must not become so** — they belong to the
  SERVICE, which several courts share (eight hard courts are one price list). Editing them per court would
  either fork the model or silently mean "change this for all eight", which is worse than sending the owner
  to the place that says so. **Summarise here, edit there.**
- **EQUIPMENT HIRE (2026-07-12).** A ball machine / racquets / balls are owner-configured **flat-fee add-ons**
  (Setup → Equipment hire) that ride a **court** booking. Each is a `diary.resource(kind='equipment')` with a
  **`quantity`** you own + a `billing.product(kind='equipment')` flat price. Selecting them on the court
  confirm step adds `order_line`(s) to the **SAME order** (one payment, no double-bill); availability is by
  **TIME** (a single ball machine can't be hired twice for overlapping times, regardless of court) and is
  race-safe (FOR UPDATE, the class-capacity pattern) — a clash is `EQUIPMENT_UNAVAILABLE`. cancel voids
  the whole order incl. the add-on. A `feature_on_home` item gets a client-Home hero tile.
  `diary/equipment.py` + `diary.booking_equipment`.
  - **EQUIPMENT IS A SERVICE AND PAYS LIKE ONE (2026-07-27).** It rides the court's order, so where the
    court was FREE (`membership_covered`) or prepaid (`token`), `_create_order_guarded` used to **hard-code
    the order to `at_court`** to collect the fee — assumed, never checked — while `create_booking` had
    already picked `confirmed` from the COURT's free mode. A card-only club therefore got an owed
    pay-at-court debt for the ball machine on a confirmed booking nobody could collect against. Now
    `equipment.quote` prices the kit and **intersects every requested item's own `payment_modes`** before
    any insert, `create_booking` resolves a method the club **and** the kit both offer (empty →
    `EQUIPMENT_NOT_PAYABLE`, **refused not granted** — the pack rule), the hold decision is made from BOTH
    modes, and the response carries **`requires_payment`** because the client can no longer infer it from
    its own choice. Set the modes at Setup → Equipment hire.
  - **THE COURT IS STILL CHARGED alongside the kit** unless a membership genuinely covers it. A covered
    court + kit is an order for the kit ONLY, which is indistinguishable from a leak by looking at the
    order, so all three cases are pinned by scenario: PAYG pays, covered doesn't, and covered **over the
    cap** pays again. Both the court (GiST) and the kit (time-overlap count) are reserved, so neither
    double-books.
  - **EQUIPMENT IS SCOPED TO A COURT SERVICE (2026-07-29).** Kit was club-wide — every item offered on
    every court booking whatever service it belonged to, so clay-only kit could be hired on a hard court.
    `diary.equipment_service` links an item to the court SERVICES it's offered on, many-to-many, and
    **NO ROWS MEANS ALL SERVICES** so every pre-existing item is unchanged. The picker filters
    (`GET /api/diary/equipment?court_product_id=`) and `create_booking` **re-checks server-side**
    (`EQUIPMENT_NOT_FOR_SERVICE`) because `addons` arrives off the request body — the same reason a posted
    `product_id` is validated rather than trusted. `?starts=&ends=` returns `available` per item so the
    stepper clamps to what is FREE for the slot, not to what the club owns.
- The booking flow (`booking.js`, full-screen): **Service → Schedule (month calendar with inline
  per-duration price) → Pay/confirm** (+ an "Add equipment" step for courts). Duration is picked right on the
  calendar, not a separate screen.

## 4. The three purchasing models (all configurable)
1. **PAYG** — pay per booking (online / at-court / monthly account) at the per-duration price.
2. **Membership (term-based)** — configurable **term plans** = (label, amount, **duration in months**),
   e.g. 1mo R220 / 3mo R600 / 6mo R1100. Grants membership for that term. **Bought online OR offline**
   (`create_membership_order(settlement_mode)`): online → `awaiting_payment` order, the webhook activates;
   at-court / monthly → an `open` (owed) order that **activates the membership IMMEDIATELY** (the debt
   lands on the client's statement). An **active membership makes COURT bookings free**
   (`settlement_mode=membership_covered`, server-resolved — courts only, never lessons). Admin can also
   **grant/revoke** a membership manually (People tab). The client can **self-cancel** a paid membership
   (`POST /api/me/membership/cancel`) — coverage ends and bookings revert to PAYG (the free trial just lapses).
   - **Tiers + access windows (abuse guard), priced PER SLOT.** A plan can carry an optional **access
     window** (`billing.price.access_days` / `access_start_min` / `access_end_min`) so a cheap tier only
     covers courts during set hours/days — e.g. *Student = weekdays 06:00–17:00*. Enforced **server-side**
     by `diary.pricing.membership_covers(starts_at)`: a court **outside** the window falls back to PAYG
     (never blocked, just not free — the member can still book peak slots and simply pays per-booking).
     Coverage is resolved **per slot**: `compute_availability` (via `active_membership_windows` /
     `any_window_covers`) shows R0 only **inside** the window and the real PAYG price at peak, matching what
     `create_booking` actually charges. Owner sets it via the **"Access hours"** editor; the purchase page
     shows each tier's summary ("Courts free weekdays 06:00–17:00"). A plan with no window covers any time.
     Tiers (Student / Family / Single) are simply labelled plans, each with its own price.
   - **SILENT entitlement caps (anti-abuse, 2026-07-12).** Beyond the access window, a tier can carry
     `max_covered_minutes` (longest covered booking), `max_covered_per_day` and `max_courts_per_day` (the
     "one member invites friends and grabs several courts" abuse). A court-SERVICE can be excluded from
     membership entirely (`billing.product.members_covered=false`, e.g. a clay court sold PAYG-only). ALL of
     these are **silent** — a member only ever sees what their membership covers (over-length durations are
     **hidden** from the picker; once a daily cap is hit further courts show the PAYG price) — and every cap
     **DOWNGRADES to PAYG, never blocks** (the same behaviour off-peak already uses). Enforced by ONE resolver,
     **`diary/entitlement.py`**, read by BOTH `compute_availability` (shape the shown options/prices) AND
     `create_booking` (enforce) so **shown == charged == allowed**. Owner: the tier editor's "Member limits"
     card + a "Members covered?" toggle on the court service.
   - **CAPS HAVE A CLUB-LEVEL FLOOR (2026-07-27).** The caps live on `billing.price`, so they only ever
     applied to a tier that HAD a price row — and the signup trial usually doesn't. Trial members were
     therefore **uncapped**, and `active_caps._best` treated a NULL cap as "an unconstrained tier wins", so
     merely HOLDING the price-less trial **cancelled a paid tier's caps too**. `club.policy.default_max_*`
     is now the floor every membership inherits (a tier's own value still overrides; NULL = inherit, the
     way `payment_modes` already works). **The owner's rule — one covered booking a day, 90 min max — is
     exactly `default_max_covered_per_day=1` + `default_max_covered_minutes=90`**; no daily-minutes
     accumulator exists or is needed. Admin → Settings → "What a membership includes (per day)".
     Guarded by `sc_club_default_caps_cover_every_membership`.
   - **`/api/me/plan` MUST REPORT THE CAP THE SERVER WILL ENFORCE (2026-07-29).** The booking UI hides
     over-cap durations using `membership_status.max_covered_minutes`, while the ENGINE decides whether to
     charge using `entitlement.active_caps` — which resolves a NULL tier cap to the club default.
     `membership_status` returned the tier's RAW column, so a club-level 90-minute cap was invisible to the
     UI: it kept offering a 2-hour court, said "Covered by your membership", and the server then correctly
     charged for it. The owner sees a cap that "isn't working"; the member gets a surprise bill. It now
     COALESCEs to the club default. **Any new surface that shows entitlement must read the EFFECTIVE cap,
     never a raw column** — shown == charged.
   - **ENTITLEMENT IS EVALUATED ON THE BOOKING'S DATE, NEVER `CURRENT_DATE` (2026-07-27).**
     `membership_covers` + `active_caps` used to ask "is this plan alive **right now**", while `starts_at`
     only drove the access window. So a member could book **forward past their own expiry** and the row was
     written `membership_covered` at R0 **permanently** — the term lapsing later changed nothing, the price
     was already fixed. Reported as trialists booking beyond their 7 days; it was never trial-specific — a
     monthly member could book out all of next month on the last day of this one and not renew, with
     `club.policy.booking_window_days` (default 14) the only limit on the reach. Both now compare against
     the booking's **club-local** date (`entitlement.local_date`), and the picker fetches
     `pricing.membership_covered_until` once per range so it can't advertise "Covered" on a day the server
     will charge for. Guarded by `sc_membership_cannot_book_past_its_own_expiry`.
   - **ONE PERSON, ONE PLACE — the GiST constraint can't express it (2026-07-27).** `booking_no_overlap` is
     keyed on `resource_id`, so it stops one COURT (or coach RESOURCE) being taken twice and says nothing
     about a **human**. Three shapes slipped through: a class books NO row on the coach's resource (only
     court holds), a coach's own court booking sits on the COURT's resource, and the court↔coach direction
     was checked in neither `_coach_class_conflict` (lesson branch only) nor `classes._coach_busy_at`
     (`booking_type='lesson'` only) — so a coach could hold a court AND deliver a lesson AND run a class at
     09:00 with no constraint violated. `bookings._coach_commitment_at` checks all three on
     create/reschedule and reports which clashed; a class's several court-holds are `booking_type='class'`
     and never read as a clash with itself.
     **MEMBERS are deliberately NOT blocked** (a doubles group legitimately holds two courts) — a member's
     2nd *concurrent* covered court simply **downgrades to PAYG** (`entitlement._has_overlapping_covered`).
     Guarded by `sc_one_coach_one_place_at_a_time` + `sc_member_second_concurrent_court_is_payg`.
     (A club-wide "N courts for members at peak" concurrent cap was considered and **dropped** — it charged
     a well-behaved member for others' timing.)
   - **CONFIGURABLE TRIAL — the signup trial is a real tier, and the flag is TIER-LEVEL (2026-07-29).**
     A membership tier flagged `is_trial` (+ `trial_days`, 0 = off) IS the "7 Day Trial Period" granted to a
     brand-new member; `grant_signup_trial` links that tier's `price_id` so the trial **inherits its access
     window + every cap above**. The genuinely-new-member guard (`auth/principal.py`, `_created=True`) is
     preserved, and with no trial tier configured the legacy NULL-price, `SIGNUP_TRIAL_DAYS`-length trial is
     granted exactly as before. The trial tier is excluded from the buyable list (`membership_plans`).
     **A TIER IS SEVERAL PRICES.** Exclusivity (`_make_sole_trial`) clears **OTHER TIERS, never sibling
     TERMS** — a tier is one price row per term (1 / 3 / 12 months) and the editor saves it by PATCHing each
     term in turn, so clearing by `p.id <> :pid` had every save undo the previous ones and only the LAST
     term kept the flag. The trial still worked (the grant finds any flagged row) but the editor reads the
     FIRST term, so re-opening showed the toggle OFF — tick, save, still off, forever. Tiers are matched by
     `membership_tier` (`IS DISTINCT FROM`, so NULL-tier legacy rows group together). Owner: Setup →
     Memberships → a tier → "Signup trial". Guarded by `sc_signup_trial_is_a_tier_level_flag`.
   - **THE TRIAL IS A MEMBERSHIP — it has no separate court rules (2026-07-29).** `provider='trial'` goes
     through the SAME resolver as a paid tier: the court service's **`members_covered`** flag, the
     duration/day caps and the access window all apply identically. So *"is clay in the free trial?"* is
     answered by ONE switch — Setup → Services → the court service → **"Included with a membership?"**
     (owner-only, `billing.product.members_covered`). Turning it off makes that court PAYG for members AND
     trialists; the booking is never blocked, just billed. **Resist any urge to special-case the trial** —
     a club would be giving its most expensive courts away to every new signup. Guarded by
     `sc_trial_obeys_the_same_court_rules_as_a_membership`.
3. **Tokens / bundles (UNIT / minute-based)** — a generic engine: an owner-configured **pack** =
   (service_kind court|lesson|class, label, **# sessions**, **base session length**, price, validity,
   optional coach). **Bought online OR offline** (`create_bundle_order(settlement_mode)`): online → paid
   then granted; at-court / monthly → an `open` (owed) order that **grants the wallet IMMEDIATELY** (the
   debt lands on the statement). Either way → a **token wallet** whose balance is held in **MINUTES**
   (`sessions_count × base_minutes`). Booking draws minutes **proportional to its duration** (R0), so
   **one pack covers any length**: a 90-min court off a 60-min unit = **1.5 sessions**, a class draws
   **one full unit**. **Customer-wins tail** — any positive balance books any length (the last credit
   covers a full booking). **Cancellation credits back the exact minutes** drawn. Draw-down is **atomic**
   (no double-spend), credit-back **idempotent** (no double-credit). Expiry + use-it-or-lose-it (drains
   the soonest-expiring wallet first). Consumption is **seamless** — a matching pack auto-applies at
   checkout ("Covered by your pack · R0"); run-dry prompts a re-buy. Full spec: `02-token-bundle-engine.md`.
   - **A pack belongs to ONE specific service (2026-07-09).** `billing.bundle_plan.product_id` +
     `billing.token_wallet.product_id` carry the exact service the pack draws for, so a "Private Lesson" pack
     only draws for Private lessons, a "Clay" pack only for Clay hire, etc.; the pack's coach + kind are
     **inherited from the service** (`create_plan` derives them from the product). The draw matcher
     (`match_wallet`) is **product-aware and BACKWARD-COMPATIBLE:** a product-scoped wallet draws only for its
     product; a **legacy NULL-product** wallet still matches by coach+kind (product-specific wins the
     tie-break). Draw callers pass the booking's product (lesson = chosen product, court = its court service,
     class = the class product), so two services under one coach no longer show each other's packs.
   - **Packs are managed ONLY under a service now (golden rule).** A pack is created/edited from the service
     editor's packages card (label + validity/expiry); the standalone Setup "Session packs" section + the
     coach-onboarding "Packs" step were removed. Existing live packs keep working (`product_id` NULL = legacy)
     until `scripts/backfill_pack_products.py` maps them to their service.
   - **Manual admin adjust / soft-expire (2026-07-09).** From a client's record the owner can **top-up or
     subtract** a wallet (`POST /api/admin/clients/<id>/wallets/<wid>/adjust`, `billing.bundles.adjust_wallet`)
     or **expire** it (`.../expire`, `expire_wallet`). Admin edits are in **SESSIONS**, converted to minutes via
     the wallet's base length. The balance is **clamped ≥ 0** (a top-up also raises `minutes_total` so the
     wallet reads correctly); a **soft-expire** sets `status='expired'` and zeroes the balance but **keeps the
     wallet row + its ledger** — never a hard-delete. Every change is **audited**: it writes a `billing.token_ledger`
     row of a new `kind='adjust'`/`'expire'` carrying a **`reason`** + the **`actor_user_id`** (the token_ledger
     idempotency index is now PARTIAL — `WHERE kind <> 'adjust'` — so system draws/credits stay idempotent while
     manual adjusts stack).

### "7 Day Trial Period" (signup gift)
The trial's canonical name is **"7 Day Trial Period"** (`membership_status.plan_name`; the composer's
membership line labels a `provider='trial'` sub the same). A **genuinely-new member** is auto-granted a
7-day courts-free trial on first login — a time-boxed `billing.membership_subscription`
(`provider='trial'`, `current_period_end = today + N days`) that makes COURT bookings free via the
membership engine and **auto-lapses → PAYG** (no cron; the active-check is date-bounded, so after 7 days
— and whenever ANY membership drops off — the client is PAYG).
- **COURT-ONLY:** lessons/classes/packs stay paid. Membership coverage (trial or paid) is court-only —
  `membership_covered` is honoured ONLY for `booking_type='court'` (`diary.bookings`); a lesson can never
  settle `free`/`membership_covered`, and classes never use membership coverage at all.
- **"Email not in history" guard (the Wix-import rule):** the trial is granted **ONLY when the login
  creates a brand-new `iam.user`** — `upsert_user_by_clerk_id` returns `_created=True` only on a fresh
  INSERT; a returning login (matched by clerk_id) OR a seeded/imported user linking by email
  (`_created=False`) is NEVER trialed. So none of the ~880 Wix imports (nor a coach) can be auto-trialed,
  even if they somehow reach the auto-enrol path — they become active **PAYG** members instead.
  `auth/principal.py` gates the grant on `user["_created"]`; `grant_signup_trial` is additionally one-shot
  (never granted if any subscription ever existed). Length via `SIGNUP_TRIAL_DAYS` (default 7; 0 disables).
- **Audit/cleanup:** `python scripts/audit_trials.py` (read-only) lists every active trial + flags
  wrongly-granted ones (coach · pre-existing user · prior activity); `--cancel-flagged` reverts them to PAYG.
`GET /api/me/plan` exposes `is_trial` / `trial_days_left`. Granted in `auth/principal.py`;
`billing.membership.grant_signup_trial`.

### Plan lifecycle (active / dormant / retired)
Every catalogue item — court rates, packs, membership plans — carries a `status`
(`active` | `dormant` | `retired`) on `billing.price` / `billing.bundle_plan`. **Dormant** = configured
but **hidden from customers** (kept editable); **retired** = soft-deleted. `active` is kept in sync
(`active = status='active'`), so customer reads (`price_for`, `membership_plans`, pack lists) only ever
show active items — dormant/retired vanish for customers but stay visible to the owner with their status.

## 5. Payments & refunds (Yoco)
- **THE PAYMENT RULE (one rule, everywhere — bookings, memberships, packs).** Each purchasable offers a
  set of allowed payment methods; the client experience follows from how many: **>1 allowed → the client
  CHOOSES**; **exactly one non-online method → checkout completes IMMEDIATELY** (no payment prompt, owed
  order); **online → Yoco** hosted checkout. Shared front end: `Pay.purchase` → `Pay.buyMembership` /
  `Pay.buyPack` (`frontend/js/pay.js`); `booking.js` hides the chooser when there's a single way to pay.
- **Service-specific payment options (layered).** Payment methods are configured **per service** in the
  Service Editor (`billing.product.payment_modes`) **and per membership tier** (`billing.price.payment_modes`,
  a "Payment options" card per tier). Resolution is layered: a tier's price-level preference → the
  membership product default → the club's globally-enabled methods (`billing.membership.membership_modes_pref`).
  Admin endpoints: `GET/PATCH /api/admin/membership-config`; `/membership/status` & `/api/billing/bundles`
  return `allowed_payment_modes`, and the `*/checkout` endpoints validate the chosen `settlement_mode`.
- **Every purchase enforces ITS OWN payment_modes server-side (no fallback leak).** The one rule above is
  policed at every purchase point against the SPECIFIC service, not a generic first-of-kind product:
  - **Court/lesson booking** — `_service_payment_modes_guarded` now passes the resolved `product_id`, so a
    **card-only** service (e.g. Clay) **refuses** pay-at-court / month-end (`SETTLEMENT_NOT_ALLOWED`, 422); a
    member can't post a mode the service doesn't offer.
  - **Packs** — `billing.bundles.allowed_purchase_modes` **intersects** the club's enabled methods with the
    pack's SERVICE modes, so a card-only pack is **card-only with NO at-court fallback** (an empty result →
    the buy is refused rather than granted on an unpaid owed order — the old fallback let a card-only pack be
    taken unpaid).
  - **Class enrolment** (`diary.classes.enrol`) is gated exactly like `create_booking`: `membership_covered`
    is **downgraded to at-court** (classes are court-only-free — you can't conjure an R0 seat via a
    membership), `free` stays **admin-only**, and a money mode must be both club-enabled AND offered by THIS
    class's service. The route passes `role` for the staff override. (Closed a member self-enrol-for-R0
    exploit.) Membership checkout + the admin offline "issue a pack" flow were already correct and are
    unchanged.
- **Online:** `online` booking → `awaiting_payment` order + `held` booking → Yoco hosted checkout (card +
  Apple/Google/Samsung Pay) → verified webhook → `apply_payment_event` → order `paid` + booking
  `confirmed`. **Gotcha:** the booking API returns `{booking:{order_id,status}, checkout}` — read
  `res.booking.order_id`.
- **Classes obey the same paywall:** an `online` class enrolment creates an `awaiting_payment` order and
  the frontend drives Yoco (fixed 2026-07-10 — it previously confirmed the seat unpaid). The unpaid seat is
  **held then lazily released** (`diary.enrolment.held_until` → `release_expired_enrolments`) like a court
  hold; a paid seat is never released, and the waitlist is promoted into a freed seat. If an unpaid client
  turns up, a coach/admin **books them in on-behalf** (owed, collect at the desk).
- **Confirmation email = the receipt (audited + signed off 2026-07-11):** an online booking gets ONE
  "Booking confirmed" email showing the rich booking block incl. its **Paid online** status; a membership /
  pack gets ONE "Membership confirmed" / "Pack activated" email (the redundant "Payment received" is
  suppressed for those). See [SYSTEM.md](SYSTEM.md) "Events, CRM & notifications".
- **Two gates** for online pay: global `PAYMENTS_ENABLED=1` + per-club `club.policy.allow_online_payment`
  (Settings → Payments toggle; the policy upsert is INSERT-ONLY so the boot re-seed can't reset it).
- **At-court / monthly account** settlement modes for desk/credit flows. A desk/at-court payment **stamps
  who recorded the money** (`billing.payment.recorded_by_user_id`, distinct from the payer) and **refuses an
  amount that isn't the order's outstanding balance** (`AMOUNT_MISMATCH`, 422; an `allow_partial` override
  exists) — so a short amount can never mark a bill fully paid. (A coach's "mark collected" already records
  `collected_by`.)
- **Reconciliation:** if the free-tier API misses a webhook, `reconcile` asks Yoco and replays the
  charge (idempotent) — on the pay-return page + a bulk cron.
- **Receipts:** `/api/billing/receipt/<order_id>` → a printable receipt page (online + desk).
- **Refunds start two ways and end in ONE modal (2026-07-28).** (a) the **Refund** action on a transaction
  record; (b) a client **refund request**, decided on that same record — Money → Refund requests is an
  **INBOX**: each row opens `#/txn/<order_id>`, where a "Decision needed" banner offers Approve & refund /
  Decline beside the payment history and audit trail. `admin_app.refundModal` is THE dialog for both and the
  ONLY caller of `yocoRefund` / `approveRefundRequest`. Deciding used to happen in the list via
  `window.prompt`/`confirm` — no order context, nowhere to go when the gateway refused. **Yoco fees are the
  owner's account** (recovered via commission), never deducted from the coach.
- **Partial vs full refund.** A **partial** refund keeps the order `paid` and reports **`part_refunded`**
  (derived from the `billing.payment` charge/refund sums); only a **full** refund flips the order to
  `refunded`. The proportional commission clawback is unchanged.
  **Partial refunds were unreachable until 2026-07-28** — supported by Yoco, `client.refund_checkout`, the
  route AND `approve_refund_request` the whole way down, but **neither UI path ever sent an amount**. The
  modal now takes an amount (defaulted to what is still refundable — collected MINUS already-returned), a
  note, and the cancel-booking choice. **A FULL refund must still send NO amount** — an explicit figure
  equal to the total is a form Yoco has refused before — so the modal omits it unless the figure is a
  genuine partial. Guarded by `sc_partial_refund_reaches_yoco_as_a_partial`.

#### Four refund failure modes, all found in live use (2026-07-28)
These presented identically — as *"insufficient funds"* — and sent us hunting a Yoco balance problem that
did not exist. Read this before diagnosing a failed refund; `scripts/diagnose_refund.py` automates it.
- **A frozen idempotency key.** The adapter keyed refunds `refund:{checkout}:{int(amount_minor or 0)}`, and
  a FULL refund passes `amount_minor=None` → `int(None or 0)` collapsed to **0**: ONE FIXED KEY per
  checkout, for all time. Yoco honours `Idempotency-Key` by **replaying the response first stored against
  it**, so once any attempt failed, **every retry replayed that failure forever** — while the Yoco
  dashboard refunded the same payment without complaint. The key now carries a **minute bucket**. **The
  real double-refund guard belongs in OUR ledger, not the gateway key**: `execute_order_refund` refuses
  `already_refunded` / `refund_exceeds_payment` by summing `billing.payment` refunds against the charge,
  without calling Yoco at all. A frozen key was never protecting the money; it only prevented the retry.
- **Refunding the wrong checkout.** `POST /checkout` mints a FRESH Yoco checkout every call — **there is no
  reuse guard** — so a member who taps Pay, abandons, returns and pays leaves TWO `ch_` ids on one order
  and only the second holds money. Both the refund and reconcile picked `ORDER BY created_at ASC LIMIT 1`,
  i.e. the **abandoned** one. `reconcile.paid_checkout_id_for_order` is now the ONE resolver both use.
  (This also silently broke missed-webhook RECOVERY on any order with a retry.)
- **Never a card sale at all.** `billing.payment_attempt` is written when a member taps "Pay online" —
  **before any money moves** — so it proves INTENT, never payment. An order whose member abandoned checkout
  and then paid at the desk still carries that `ch_` id. The refund now checks `billing.payment` for a
  succeeded **yoco** charge first and refuses naming the real method (`not_paid_by_card`) — but **only when
  another provider positively succeeded**; no payment rows at all is **ambiguous, not refused** (the charge
  may sit on a 'Pay all' wrapper), so it reaches the gateway and lets Yoco decide. The order's own facts are
  checked **before** `get_gateway` — "this was never a card sale" is true whether or not Yoco is reachable.
- **Genuinely no Yoco balance.** Refunds draw on the Yoco balance, not the bank, so this really can be the
  answer. Check whether the order has >1 `payment_attempt` row before assuming which of the four it is.

## 6. Commission / coaching-settlement engine (the commercial core)
- **THE ONE money model — money is an OUTCOME of bookings (order-status-driven fold).** Every money surface
  — coach console, admin Money, and the client record — reports the SAME month-scoped reconciling fold:
  **Billed − Discount − Written-off = Invoiced; Invoiced = Paid + Outstanding.** A cancelled/void booking is
  **R0** across the board; **you-keep vs club-commission** come from the ACTUAL `commission_split` rows (not a
  re-derived %); an EVENT is the **sum of its transactions** (the fold headline + a Transactions log on the
  shared `Widgets.TransactionDetail`, class events drilling to the same story). It is single-sourced through
  `CRMUI.statementFold` (the fold) + `CRMUI.moneySummary` (a Billed→Collected→Outstanding band) on both
  consoles, and the **Money tab is now ONE `Widgets.Earnings`** — a **CLUB-vs-COACH P&L** shared by admin and
  coach. **Club earnings** = the club's DIRECT services (court/membership/pack it runs, 100% club) **plus** the
  commission it takes from each coach → Total club earnings (collected-now + projected-when-all-owed) +
  Club-keeps vs Coaches-keep, drilling coach → client → transaction. A **coach P&L** card reads Total sales −
  discount − write-off = Net ; Net = **Received + Owed** ; the commission split is **realised on Received** and
  **projected on Owed** at the same effective rate. The coach app shows the coach's OWN P&L ("You keep").
  Backing readers (`admin/repositories.py`): `revenue_club_overview` / `revenue_coach_pnl` / `earnings_clients`
  / `earnings_transactions` / `earnings_by_service`, all riding the ONE `_earnings_cte` (now carrying a
  per-order coach-attribution column: lesson booking / class session / pack sold → that coach; court/membership
  → NULL = Club) so every drill reconciles; plus `coach/repositories.py`, `billing/commission.py`.
- The owner monetises each coach via **rent and/or commission %** — freely combinable, per coach.
  Tables: `coach_agreement` (rent), `commission_rule` (scoped, dated %), `commission_split` (per-payment
  decomposition), `coach_ledger` (running balance), `coach_arrears`.
- **% resolution:** most-specific then latest-effective — `coach+product > product > coach > club > 0`.
- **Base = ex-VAT.** Commission **accrues on every COLLECTION, regardless of payment method** — the split
  posts wherever an order flips to `paid`: a Yoco checkout, an invoice pay-link, AND a desk cash/EFT/card
  payment (`record_desk_payment`) all route through the ONE payment core (`apply_payment_event` →
  `_accrue_commission` → `record_split_for_order`), and a 'pay-all' statement fans each child order's split out
  (`settle_settlement_order`) — so NO payment method short-changes a coach. `void_order`/write-off drops the
  lesson off the coach's tab; a refund claws the commission back proportionally. **No commission on
  membership-covered free courts** (gross 0). Coach-lesson **bundle** purchases accrue at the (collected)
  purchase — pack revenue is **sale-based** (recognised at the pack sale to the wallet's coach). (Read-only
  guard scripts: `scripts/reconcile_coach_commission.py`, `scripts/diagnose_coach_packs.py`.)
- **Coach pricing modes:** PAYG (online) · bundles (online) · **monthly arrears** (off-platform: the
  coach sends a statement and chases EFT, then **marks collected** → commission accrues).

#### WHO HOLDS THE CASH decides the ledger's DIRECTION (owner rule, confirmed 2026-07-29)
`billing.coach_ledger` is **SIGNED: + = the club owes the coach, − = the coach owes the club**. A
`commission_earning` of `+coach_net` is correct **only when the club took the gross and is holding it**.
When the COACH holds the cash, the club is owed its commission and the entry must be **`−owner_cut` as
`commission_due`** (`_write_split_pair(cash_held_by=)`).

| How the money arrived | Who holds it | Ledger entry |
|---|---|---|
| Yoco online checkout / invoice pay-link | **Club** | `+coach_net` (club owes coach) |
| EFT or cash recorded at the desk (`record_desk_payment`) | **Club** | `+coach_net` |
| Coach collects courtside / off-platform (**"Mark collected"**) | **Coach** | `−owner_cut` (coach owes club) |
| Monthly account → month-end invoice → paid by Yoco/EFT | **Club** | `+coach_net` when it lands |

**The direction follows WHO RECORDED THE PAYMENT, not what the booking said.**
`POST /api/billing/desk-payment` is **`club_admin`-only** (`take_pay_at_court`) — a coach physically
cannot use it; his one collection verb is "Mark collected" (`mark_arrears_collected`), which is the
off-platform path. So an `at_court` lesson is **not** automatically coach-held: if the client pays at
reception, or pays the month-end invoice, the club holds it and the coach is owed his net. This is
deliberately more accurate than a flat "pay-at-court means the coach has it" rule, and it means the club
can take a lesson payment at the desk without the ledger going wrong.
**What still needs enforcing with coaches is behavioural, not financial:** mark the collection promptly,
or the club's commission on that cash stays invisible.

- **THE BUG THIS FIXED (2026-07-28).** `mark_arrears_collected` — off-platform **by definition** — posted
  the same `+coach_net` as a club-held collection. The coach already held the full gross, so the club was
  owed its commission and the ledger said **the exact opposite: wrong by the whole gross, every time.** It
  surfaced as "Coach payouts due" telling the owner to pay a coach who was holding the club's money.
  The **`commission_split` rows are IDENTICAL either way** — the sale was divided the same whoever held the
  cash — so commission REPORTING (`cockpit_coach_earnings`, the Money P&L) is untouched; only the running
  balance changes, which is the thing that was lying. No read filters on `commission_earning` (balances just
  `SUM(amount_minor)`), so the new `commission_due` type flows through everywhere. Historical rows:
  `scripts/fix_inverted_coach_ledger.py` appends ONE correcting `adjustment` per coach rather than rewriting
  history, idempotent on a fixed `ref_id` (**run against production 2026-07-29: 2 coaches, R14,800 net
  correction**). Guarded by `sc_ledger_direction_follows_who_holds_the_cash`.

#### Commission is paid on FUNDS RECEIVED (owner rule, 2026-07-29)
The club invoices on the **25th** and collects by the **1st**. **Commission is only ever paid on money
actually received** — and this needs no monthly commission run, because there isn't one:
- The split posts at `charge_succeeded`, i.e. **at collection**. Unpaid work never accrues a ledger earning.
- `coach_ledger` is a **live running balance**, not a period bucket. A payment landing on 2 August adds to
  the balance on 2 August; whenever a `coach_payout` is recorded, the balance is exactly what has been
  collected at that instant. **A payment that arrives in the new month is simply in the next settlement** —
  there is no window it can fall between.
- The 25th sweep accrues **arrears + rent** and issues **client** invoices. **It does not pay commission.**
- Unpaid coaching sits on the coach's tab as **projected** commission that never realises until the client
  pays. That is deliberate: the coach holds the client relationship, so **the coach chases the payment**.
  The club does not carry the cost of a client who doesn't pay.

- **"PAID" IS NOT "IN THE BANK" — Money → Coach statement is the split.** `order.status='paid'` merges Yoco
  + EFT (the club's bank), cash/card at the desk (the till OR the coach, genuinely ambiguous) and a coach's
  off-platform collection (the coach only). The last is **exactly derivable**: `mark_arrears_collected`
  flips the order to `paid` with **no `billing.payment` row at all** (the money never touched the platform),
  so *paid + zero succeeded charges* == the coach collected it. `admin.repositories.coach_statement_report`
  classifies off the ORDER (not the payment rows) so the custody buckets sum EXACTLY to the Money tab's
  `paid`, and pairs it with `payments_received` — an INDEPENDENT read of `billing.payment` by landing date,
  which is the **bank-reconciliation** figure and deliberately not derived from the fold (it sees money the
  order CTE excludes, e.g. both sides of a settled 'Pay all' wrapper, and counts when cash landed, not when
  the sale happened).
- **Coach month-end statement** (the coach app's **Money** tab = `Widgets.Earnings`; the standalone
  + owed (arrears) = **net balance**; mark arrears collected, and **discount / write-off** owed lines
  (`PATCH /api/admin/coach-statement/arrears/<id>`). The **client sees the same statement** (`GET
  /api/me/statement`) — one engine, two lenses.
- **Owner cockpit** (`/api/admin/financials/*`): revenue by service, **commission owed + rent due per
  coach**, membership MRR; reconciles (collected − commission = coach net).
- **Club↔coach settlement (`coach_payout`).** The running `coach_ledger` balance (**+ = club owes coach,
  − = coach owes club**) is the single authoritative **net-owed** figure, and is settled by a recorded
  **`coach_payout`** in **either direction** — **append-only + idempotent** (never mutated, no double-pay).
- **Month-end sweep (the 25th — the club's billing day).** A **GitHub Action** fires the sweep on the
  **25th** of each month (was the 1st): it **accrues arrears + rent**, then for each client with an OPEN
  balance > 0 **consolidates their open orders into ONE numbered statement invoice + pay-link email** (else a
  plain balance reminder); a client who owes **nothing gets NO email**. **Idempotent per (club, user, YYYY-MM)**
  (a re-run never re-issues/re-notifies) — no always-on cron.
- Splits/accruals are **idempotent** (a replayed webhook never double-charges).

### Unified client statement (one debt = one order)
`billing/statement.py` is the **single source of truth for what a client owes**: every debt is exactly
ONE `billing.order`, and the amount owed = **SUM of the client's unpaid (`status='open'`) orders** — never
double-counted (account_ledger and coach_arrears are tracked internally but never added into the total).
Full spec: [UNIFIED-STATEMENT.md](UNIFIED-STATEMENT.md).
- **Admin ad-hoc invoice.** Money → New invoice bills a client for a **service × how-many** (price
  re-derived server-side, tamper-proof) and/or a **custom fee**, less an optional rand discount → ONE owed
  `billing.order` (settlement `monthly_account`, on this same statement, settleable online) + emails the
  client a `/portal` pay link (`POST /api/admin/clients/<id>/invoice` → `admin.create_invoice`). Not booked
  to the calendar. (Creating the client first requires a **name + valid email** — the pay-link address.)
- **Pay-all or part-settle.** `GET /api/me/statement` returns the unpaid orders **grouped by category**
  (Coaching / Court hire / Classes / Membership / Session packs / Other, with coach name + date + status).
  `POST /api/me/statement/pay {order_ids?}` creates ONE **settlement order** (`create_settlement_order` —
  all orders, or just the ticked lines; reclaims abandoned settlements) → Yoco. On its `charge_succeeded`
  each child order is marked paid and its commission accrues **exactly once** (`settled_by_order_id` links
  child → settlement; fan-out in `billing/events.py`). The Account page shows ONE "Your statement" card.
- **Coach arrears kept in lockstep.** `accrue_arrears` excludes paid/void/written-off orders; settling a
  settlement order marks each lesson's arrears `collected`; `mark_arrears_collected` marks the linked order
  paid. Commission accrues once and the coach's and client's views always agree.
- **Admin void / write-off.** `GET /api/admin/members/<id>/statement` + `POST /api/admin/orders/<id>/void
  {write_off}` (`void_order`): **void** a mistaken order or **write-off** a forgiven debt (a paid order
  can't be voided). Surfaced in the People-360 drawer "Outstanding" section.
- **Admin discount any open order (2026-07-09).** `POST /api/admin/orders/<order_id>/discount
  {discount_minor|new_amount_minor, reason}` (`billing.statement.discount_order`) **reprices ANY open/awaiting
  order** — court, lesson, class, pack or membership — down to a lower amount. It **mutates the ONE debt** (no
  new debt row, no settlement order). A **multi-line** order splits the discount **pro-rata** (remainder on the
  last line, so lines re-sum exactly) and preserves each line's **`order_line.original_amount_minor`** as the
  audit trail. A linked **`coach_arrears`** line is kept in **LOCKSTEP** (delegates to `commission.adjust_arrears`),
  so the coach's commission base moves with the discount. A **PAID** order rejects with `NOT_OPEN` — reducing a
  paid charge is a **refund** (the separate path), not a discount.

## 7. Self-service per role
- **Client (the client SPA `app.html`/`client.js`; the old `/account.html` was deleted and 302s here):** edit profile/demographics (**email
  read-only = identity**); manage **children/dependents**; **Financials** + the **unified statement**
  (`/api/me/statement` — unpaid orders grouped by category, with **pay-all or tick-to-part-settle**); raise
  **refund requests**. Buy membership + packs on the consolidated **`/plan`** page (each via the one payment
  rule — choose / immediate-owed / Yoco). The client can **self-cancel** a paid membership. **My Bookings** carries **reschedule / cancel**
  (the shared `CRMUI.rescheduleModal`) and **"Add to calendar"** (.ics) on upcoming bookings. The old
  *"Needs your attention"* section went with the approval gate — there is nothing to accept any more.
- **Coach (`/coach`, the `coach_app.js` SPA):** 4-step onboarding + edit profile (bio, photo,
  specialties, languages, qualifications, visibility, **preferred court**); set **per-duration
  lesson rates** + classes; **own lesson packs** (scoped + ownership-guarded); availability + time-off;
  **reschedule or cancel** any of his own lessons and **move** any of his own class sessions (there is no
  approval queue — see §2); **book a session for a client** (auto-confirms);
  **My Clients** 360 (derived, private; history + upcoming); **Statement** (month-end money — mark
  collected + discount/write-off); **Dashboard cockpit** (lessons, hours, gross + **net-of-commission**
  earnings, fill rate, new-vs-returning, top clients, trend, **lessons-left-on-plans**, month-end-after-
  commission).
- **Owner (`/admin`, the `admin_app.js` SPA; the classic console was retired 2026-07-18):** master diary; resources/courts;
  **People** (360 drawer + membership grant); classes; a consolidated **Settings → Pricing** tab (court
  rates · packs · memberships, each with the active/dormant/retired control + membership "Access hours");
  **Coach pay** — a **per-service commission editor** (club / per-coach / per-service, lessons AND classes)
  on top of rent; payments (online-payments toggle) + refunds + refund-requests; **financial Cockpit**
  (per-coach settlement, refund-aware); onboarding; branding; policy.

## 8. Notifications
- In-app **bell + inbox** (topbar) for every member; driven non-fatally off `emit()`. Kinds:
  booking confirmed, payment receipt (links to the receipt), membership active, pack activated,
  refund requested/decided, class enrolled/waitlisted/spot-open/awaiting-payment, class cancelled,
  class **rescheduled**, coach invited.
- **The COACH is told, once, about every lesson and every class — addressed to him, never a BCC.**
  `lesson_booked` (at creation when owed/prepaid; **on payment** when online — an unpaid hold is not news)
  and `class_booked` (the same three real-seat moments: enrolment, payment, waitlist promotion). Who got
  told used to depend on the settlement mode and the review flag, and otherwise he was blind-copied on the
  **client's** receipt — written for her, not him. **The coach BCC is now dropped everywhere.** An online
  lesson confirms in the PAYMENT path, so `billing.events` calls `notify_coach_of_confirmed_order`, or the
  coach would hear about every lesson except the paid ones.
- **Calendar:** every booking has a downloadable **`.ics`** (`GET /api/diary/bookings/<id>/calendar.ics`);
  the confirmation payload carries `ics_url`. The in-app **"Add to calendar"** works now; the email
  *attachment* is gated OFF (`EMAIL_ICS_ENABLED=0`) **by choice** — the SES key already carries
  `ses:SendRawEmail` (that is how the invoice PDF attaches), so this is flag-only.
- **Email** (SES transactional) is **LIVE** — interim via the Ten-Fifty5 AWS account (`eu-north-1`,
  `SES_SENDER=noreply@ten-fifty5.com`): invites + booking/statement confirmations send from each club's
  From-name + Reply-To, alongside the in-app inbox. Child events notify the **guardian**. Booking emails
  carry a **full detail block** (`marketing_crm/email/booking_detail.py`) — client name/email/cell, service,
  **SAST** date & time, court, price and payment status — and a **lesson** booking **BCCs the coach** (on top
  of the club's oversight BCC). **Klaviyo** marketing stays dark until keyed. (See ENV-STATUS.md /
  OUTSTANDING.md.)

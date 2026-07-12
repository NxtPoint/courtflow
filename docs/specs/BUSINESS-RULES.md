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
  completes profile/photo/bio + languages/qualifications/visibility + **review-bookings** preference,
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
- **Services:** book a **court**, a **lesson** (with a named or "Any" coach), or **attend a class**.
- **No double-booking:** a Postgres GiST EXCLUDE constraint guarantees one booking per resource per
  time; concurrent clashes → exactly one wins (`SLOT_TAKEN`).
- **Lessons reserve a court:** availability for a lesson = slots where a **coach AND a court are both
  free** (coach ∩ court); booking a lesson auto-holds a court (two rows, one `order_id`). The held court
  is **never billed separately** — the lesson's single order covers both, and the court row confirms
  alongside the lesson when the order is paid.
- **Classes:** owner/coach create class types + schedule **recurring or one-off** sessions; capacity +
  **waitlist** (auto-promote the next person on a cancellation); rosters + attendance; shown on the
  master diary.
- **Book-on-behalf:** a coach/admin can book FOR a client (owned by the client via `booked_for_user_id`)
  — this **auto-confirms** (the client is just notified, and can reschedule/cancel). **Book-for-a-child:**
  a parent picks a dependent in "Who's playing?" — the booking is FOR the child but **owned and billed to
  the parent**. **On-behalf pack auto-draw:** when a coach/admin books for a client who already holds a
  matching prepaid pack, the booking **draws that client's wallet** (settlement `token`, R0) instead of
  raising a new charge — a lesson matches a **coach-scoped** wallet, a class a **coach-agnostic** one. Staff
  on-behalf settlement is desk-only (at-court / monthly / the client's pack) — it **skips online (Yoco)**,
  and an online-only per-service preference does not restrict staff.
- **Lesson approval lifecycle (accept / propose / decline).** A coach can require approval of lessons
  clients book with them (`iam.coach_profile.review_bookings`). When ON, a client self-booking that coach
  creates a **`requested`** lesson that **reserves nothing** (no court, no order, no payment) until the
  coach acts: **accept** → a court is auto-assigned, settlement runs, status → `confirmed`; **propose** a
  new time → **`proposed`** (the client accepts/declines/withdraws in *My Bookings → "Needs your
  attention"*); **decline** → `cancelled`. When OFF, lessons auto-confirm. `requested`/`proposed` hold no
  slot (not in the GiST exclusion); only the awaited party may act (admin always).
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
- **Late-cancellation fee.** When the club's cancellation policy applies at cancel time, a small **owed fee
  order** is raised on the client's statement (owner decision M6); cancelling voids the booking's own unpaid
  order so no phantom debt remains, and a **paid** cancellation is flagged (`was_paid`) so the UI can prompt a
  refund (the refund itself stays a separate, explicit flow).
- **Can't cancel a delivered session.** A member/guest may **not** cancel a lesson/class that has already
  **started** (`CANNOT_CANCEL_STARTED`, 422) — otherwise a delivered-but-owed booking could be cancelled
  after the fact, voiding its order and **erasing the debt**. Admins/coaches may still cancel a started
  booking (correction/no-show handling). A pending `requested`/`proposed` booking can always be withdrawn —
  it holds no slot and no debt.
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
- **PEAK court pricing (2026-07-12; court hire only).** A club sets ONE peak window (`club.policy.peak_days`
  / `peak_start_min` / `peak_end_min`, e.g. weekdays 17:00–19:00) and an **explicit peak price per court
  duration** (`billing.price.peak_amount_minor`). A court booking whose LOCAL start falls in the window is
  charged its peak price; membership coverage still wins first (a covered member inside their window is free,
  outside → the peak PAYG price). Resolved once in `diary.pricing.price_for(at_local)` and applied in the two
  places that must agree — `compute_availability` (shown) and `create_booking` (charged). Owner: Setup → Club
  profile → "Peak hours" + a "peak R" field per court duration in the service editor. Full spec:
  [EQUIPMENT-AND-CONSTRAINTS.md](EQUIPMENT-AND-CONSTRAINTS.md).
- **EQUIPMENT HIRE (2026-07-12).** A ball machine / racquets / balls are owner-configured **flat-fee add-ons**
  (Setup → Equipment hire) that ride a **court** booking. Each is a `diary.resource(kind='equipment')` with a
  **`quantity`** you own + a `billing.product(kind='equipment')` flat price. Selecting them on the court
  confirm step adds `order_line`(s) to the **SAME order** (one payment, no double-bill); availability is by
  **TIME** (a single ball machine can't be hired twice for overlapping times, regardless of court) and is
  race-safe (FOR UPDATE, the class-capacity pattern) — a clash is `EQUIPMENT_UNAVAILABLE`. On a covered/free
  court the equipment still bills (the order becomes an owed at-court charge for just the add-on); cancel voids
  the whole order incl. the add-on. A `feature_on_home` item gets a client-Home hero tile. `diary/equipment.py`
  + `diary.booking_equipment`.
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
     card + a "Members covered?" toggle on the court service. (A club-wide "N courts for members at peak"
     concurrent cap was considered and **dropped** — it charged a well-behaved member for others' timing.)
   - **CONFIGURABLE TRIAL (2026-07-12) — the signup trial is a real tier.** A membership tier flagged
     `is_trial` (+ `trial_days`, 0 = off) IS the "7 Day Trial Period" granted to a brand-new member;
     `grant_signup_trial` links that tier's `price_id` so the trial **inherits its access window + every cap
     above**. The genuinely-new-member guard (`auth/principal.py`, `_created=True`) is preserved, and it stays
     backward-compatible — with no trial tier configured the legacy NULL-price, `SIGNUP_TRIAL_DAYS`-length
     trial (covers any time) is granted exactly as before. The trial tier is excluded from the buyable list.
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
- **Refunds, two paths:** (a) **admin direct** — Billing → Recent online payments → "Refund only" or
  "Refund & cancel"; (b) **client refund-request** — the client raises a request → admin **approves**
  (executes the Yoco refund, money-first, then marks refunded) or **declines** → the client is notified.
  Refunds are record-only unless the admin also cancels the booking. **Yoco fees are the owner's
  account** (recovered via commission), never deducted from the coach.
- **Partial vs full refund.** A **partial** refund keeps the order `paid` and reports **`part_refunded`**
  (derived from the `billing.payment` charge/refund sums); only a **full** refund flips the order to
  `refunded` — either a Yoco full refund (no amount) or cumulative refunds reaching ≥ the amount paid. The
  proportional commission clawback is unchanged.

## 6. Commission / coaching-settlement engine (the commercial core)
- The owner monetises each coach via **rent and/or commission %** — freely combinable, per coach.
  Tables: `coach_agreement` (rent), `commission_rule` (scoped, dated %), `commission_split` (per-payment
  decomposition), `coach_ledger` (running balance), `coach_arrears`.
- **% resolution:** most-specific then latest-effective — `coach+product > product > coach > club > 0`.
- **Base = ex-VAT.** Commission **accrues on COLLECTION**: online lessons/classes at payment; arrears
  when the coach marks an invoice collected. **No commission on membership-covered free courts**
  (gross 0). Coach-lesson **bundle** purchases accrue at the (collected) purchase.
- **Coach pricing modes:** PAYG (online) · bundles (online) · **monthly arrears** (off-platform: the
  coach sends a statement and chases EFT, then **marks collected** → commission accrues).
- **Coach month-end statement** (`/statement.html` + in the console): per client — lessons, paid-via-Yoco
  + owed (arrears) = **net balance**; mark arrears collected, and **discount / write-off** owed lines
  (`PATCH /api/admin/coach-statement/arrears/<id>`). The **client sees the same statement** (`GET
  /api/me/statement`) — one engine, two lenses.
- **Owner cockpit** (`/api/admin/financials/*`): revenue by service, **commission owed + rent due per
  coach**, membership MRR; reconciles (collected − commission = coach net).
- **Club↔coach settlement (`coach_payout`).** The running `coach_ledger` balance (**+ = club owes coach,
  − = coach owes club**) is the single authoritative **net-owed** figure, and is settled by a recorded
  **`coach_payout`** in **either direction** — **append-only + idempotent** (never mutated, no double-pay).
- **Month-end sweep.** A month-end job **accrues arrears + rent** and **notifies the clients who owe**,
  **idempotent per month** (a re-run is a no-op). It is fired by a **GitHub Action** — no always-on cron.
- Splits/accruals are **idempotent** (a replayed webhook never double-charges).

### Unified client statement (one debt = one order)
`billing/statement.py` is the **single source of truth for what a client owes**: every debt is exactly
ONE `billing.order`, and the amount owed = **SUM of the client's unpaid (`status='open'`) orders** — never
double-counted (account_ledger and coach_arrears are tracked internally but never added into the total).
Full spec: [UNIFIED-STATEMENT.md](UNIFIED-STATEMENT.md).
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
- **Client (`/account.html` + action-first `/portal` cockpit):** edit profile/demographics (**email
  read-only = identity**); manage **children/dependents**; **Financials** + the **unified statement**
  (`/api/me/statement` — unpaid orders grouped by category, with **pay-all or tick-to-part-settle**); raise
  **refund requests**. Buy membership + packs on the consolidated **`/plan`** page (each via the one payment
  rule — choose / immediate-owed / Yoco). The client can **self-cancel** a paid membership. **My Bookings** has a *"Needs your attention"* section (accept/decline a coach's proposed
  time, withdraw a pending request) and **"Add to calendar"** (.ics) on upcoming bookings.
- **Coach (`/coach`, the `coach_app.js` SPA):** 4-step onboarding + edit profile (bio, photo,
  specialties, languages, qualifications, visibility, **review-bookings** toggle); set **per-duration
  lesson rates** + classes; **own lesson packs** (scoped + ownership-guarded); availability + time-off;
  **lesson approval queue** (accept/propose/decline); **book a session for a client** (auto-confirms);
  **My Clients** 360 (derived, private; history + upcoming); **Statement** (month-end money — mark
  collected + discount/write-off); **Dashboard cockpit** (lessons, hours, gross + **net-of-commission**
  earnings, fill rate, new-vs-returning, top clients, trend, **lessons-left-on-plans**, month-end-after-
  commission).
- **Owner (`/admin`, the `admin_app.js` SPA; classic console at `/admin-classic`):** master diary; resources/courts;
  **People** (360 drawer + membership grant); classes; a consolidated **Settings → Pricing** tab (court
  rates · packs · memberships, each with the active/dormant/retired control + membership "Access hours");
  **Coach pay** — a **per-service commission editor** (club / per-coach / per-service, lessons AND classes)
  on top of rent; payments (online-payments toggle) + refunds + refund-requests; **financial Cockpit**
  (per-coach settlement, refund-aware); onboarding; branding; policy.

## 8. Notifications
- In-app **bell + inbox** (topbar) for every member; driven non-fatally off `emit()`. Kinds:
  booking confirmed, payment receipt (links to the receipt), membership active, pack activated,
  refund requested/decided, class enrolled/waitlisted/spot-open, coach invited, **lesson
  requested/proposed/accepted/declined**.
- **Calendar:** every booking has a downloadable **`.ics`** (`GET /api/diary/bookings/<id>/calendar.ics`);
  the confirmation payload carries `ics_url`. The in-app **"Add to calendar"** works now; the email
  *attachment* is gated OFF (`EMAIL_ICS_ENABLED=0`) until the interim SES key gains `ses:SendRawEmail`.
- **Email** (SES transactional) is **LIVE** — interim via the Ten-Fifty5 AWS account (`eu-north-1`,
  `SES_SENDER=noreply@ten-fifty5.com`): invites + booking/statement confirmations send from each club's
  From-name + Reply-To, alongside the in-app inbox. Child events notify the **guardian**. Booking emails
  carry a **full detail block** (`marketing_crm/email/booking_detail.py`) — client name/email/cell, service,
  **SAST** date & time, court, price and payment status — and a **lesson** booking **BCCs the coach** (on top
  of the club's oversight BCC). **Klaviyo** marketing stays dark until keyed. (See ENV-STATUS.md /
  OUTSTANDING.md.)

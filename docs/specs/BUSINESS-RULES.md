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
  free** (coach ∩ court); booking a lesson auto-holds a court (two rows, one `order_id`).
- **Classes:** owner/coach create class types + schedule **recurring or one-off** sessions; capacity +
  **waitlist** (auto-promote the next person on a cancellation); rosters + attendance; shown on the
  master diary.
- **Book-on-behalf:** a coach/admin can book FOR a client (owned by the client via `booked_for_user_id`)
  — this **auto-confirms** (the client is just notified, and can reschedule/cancel). **Book-for-a-child:**
  a parent picks a dependent in "Who's playing?" — the booking is FOR the child but **owned and billed to
  the parent**.
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

## 3. Pricing (per-duration PAYG)
- A service carries **one `billing.price` row per offered duration** (`duration_minutes`,
  `unit='per_booking'`). `price_for(kind, duration)` resolves exact → nearest≤ → any.
- Seeded defaults (editable): Court 30/60/90/120 = R90/150/210/280; Lesson 30/60 = R250/400; classes
  per session. The legacy Wix "member R0 court" tier is gone.
- The booking flow (`booking.js`, full-screen): **Service → Schedule (month calendar with inline
  per-duration price) → Pay/confirm.** Duration is picked right on the calendar, not a separate screen.

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

### Free week (signup gift)
A brand-new member is **auto-granted a 7-day courts-free trial** on first login — a time-boxed
`billing.membership_subscription` (`provider='trial'`, `current_period_end = today + N days`) that makes
COURT bookings free via the membership engine and **auto-lapses** (no cron). Lessons/packs stay paid.
One-shot + idempotent (never double-granted, never re-issued; existing/paid members get nothing). Length
via `SIGNUP_TRIAL_DAYS` env (default 7; 0 disables). The booking page shows a "free week — N days left"
banner; `GET /api/me/plan` exposes `is_trial` / `trial_days_left`. Granted in `auth/principal.py`
auto-enrol; `billing.membership.grant_signup_trial`.

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
- **Two gates** for online pay: global `PAYMENTS_ENABLED=1` + per-club `club.policy.allow_online_payment`
  (Settings → Payments toggle; the policy upsert is INSERT-ONLY so the boot re-seed can't reset it).
- **At-court / monthly account** settlement modes for desk/credit flows.
- **Reconciliation:** if the free-tier API misses a webhook, `reconcile` asks Yoco and replays the
  charge (idempotent) — on the pay-return page + a bulk cron.
- **Receipts:** `/api/billing/receipt/<order_id>` → a printable receipt page (online + desk).
- **Refunds, two paths:** (a) **admin direct** — Billing → Recent online payments → "Refund only" or
  "Refund & cancel"; (b) **client refund-request** — the client raises a request → admin **approves**
  (executes the Yoco refund, money-first, then marks refunded) or **declines** → the client is notified.
  Refunds are record-only unless the admin also cancels the booking. **Yoco fees are the owner's
  account** (recovered via commission), never deducted from the coach.

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

## 7. Self-service per role
- **Client (`/account.html` + action-first `/portal` cockpit):** edit profile/demographics (**email
  read-only = identity**); manage **children/dependents**; **Financials** + the **unified statement**
  (`/api/me/statement` — unpaid orders grouped by category, with **pay-all or tick-to-part-settle**); raise
  **refund requests**. Buy membership + packs on the consolidated **`/plan`** page (each via the one payment
  rule — choose / immediate-owed / Yoco). The client can **self-cancel** a paid membership. **My Bookings** has a *"Needs your attention"* section (accept/decline a coach's proposed
  time, withdraw a pending request) and **"Add to calendar"** (.ics) on upcoming bookings.
- **Coach (`/coach.html`, on the shared `crm_ui.js`):** 4-step onboarding + edit profile (bio, photo,
  specialties, languages, qualifications, visibility, **review-bookings** toggle); set **per-duration
  lesson rates** + classes; **own lesson packs** (scoped + ownership-guarded); availability + time-off;
  **lesson approval queue** (accept/propose/decline); **book a session for a client** (auto-confirms);
  **My Clients** 360 (derived, private; history + upcoming); **Statement** (month-end money — mark
  collected + discount/write-off); **Dashboard cockpit** (lessons, hours, gross + **net-of-commission**
  earnings, fill rate, new-vs-returning, top clients, trend, **lessons-left-on-plans**, month-end-after-
  commission).
- **Owner (`/admin.html`, `/settings.html`, on the shared `crm_ui.js`):** master diary; resources/courts;
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
  the confirmation payload carries `ics_url`. The in-app **"Add to calendar"** works now; the email will
  attach the same file once email is live.
- **Email** path (SES transactional) lights up when keys are set; until then the inbox works fully and
  email is `skipped`. Child events notify the **guardian**. (See OUTSTANDING.md for the keys.)

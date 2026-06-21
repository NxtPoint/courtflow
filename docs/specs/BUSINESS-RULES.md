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
- **Coach onboarding:** invited by the owner (`iam.coach_invite`) → on first login the coach completes
  their own profile/photo/bio, weekly hours (creates their `diary.resource(kind=coach)`), and
  services/rates.

## 2. The diary / booking
- **Services:** book a **court**, a **lesson** (with a named or "Any" coach), or **attend a class**.
- **No double-booking:** a Postgres GiST EXCLUDE constraint guarantees one booking per resource per
  time; concurrent clashes → exactly one wins (`SLOT_TAKEN`).
- **Lessons reserve a court:** availability for a lesson = slots where a **coach AND a court are both
  free** (coach ∩ court); booking a lesson auto-holds a court (two rows, one `order_id`).
- **Classes:** owner/coach create class types + schedule **recurring or one-off** sessions; capacity +
  **waitlist** (auto-promote the next person on a cancellation); rosters + attendance; shown on the
  master diary.
- **Book-on-behalf:** a coach/admin can book FOR a client (the booking is owned by the client via
  `booked_for_user_id`). **Book-for-a-child:** a parent picks a dependent in "Who's playing?" — the
  booking is FOR the child but **owned and billed to the parent**.
- **Holds expire lazily** (no cron): abandoned `held` bookings past `held_until` are released whenever
  anyone checks availability or books.
- **Booking window / lead time / cancellation cutoff** come from `club.policy` (configurable).

## 3. Pricing (per-duration PAYG)
- A service carries **one `billing.price` row per offered duration** (`duration_minutes`,
  `unit='per_booking'`). `price_for(kind, duration)` resolves exact → nearest≤ → any.
- Seeded defaults (editable): Court 30/60/90/120 = R90/150/210/280; Lesson 30/60 = R250/400; classes
  per session. The legacy Wix "member R0 court" tier is gone.
- The booking flow: **Service → Duration (live price) → Schedule → Pay/confirm.**

## 4. The three purchasing models (all configurable)
1. **PAYG** — pay per booking (online / at-court / monthly account) at the per-duration price.
2. **Membership (term-based)** — configurable **term plans** = (label, amount, **duration in months**),
   e.g. 1mo R220 / 3mo R600 / 6mo R1100. Bought via Yoco (one-off, no recurring billing); grants
   membership for that term. An **active membership makes COURT bookings free**
   (`settlement_mode=membership_covered`, server-resolved — courts only, never lessons). Admin can also
   **grant/revoke** a membership manually (People tab).
   - **Tiers + access windows (abuse guard).** A plan can carry an optional **access window**
     (`billing.price.access_days` / `access_start_min` / `access_end_min`) so a cheap tier only covers
     courts during set hours/days — e.g. *Student = weekdays 06:00–17:00*. Enforced **server-side** by
     `diary.pricing.membership_covers(starts_at)`: a court **outside** the window falls back to PAYG
     (never blocked, just not free). Owner sets it via the **"Access hours"** editor; the booking flow
     resolves coverage per slot and shows the real price; the purchase page shows each tier's summary
     ("Courts free weekdays 06:00–17:00"). A plan with no window covers any time. Tiers (Student / Family
     / Single) are simply labelled plans, each with its own price.
3. **Tokens / bundles (UNIT / minute-based)** — a generic engine: an owner-configured **pack** =
   (service_kind court|lesson|class, label, **# sessions**, **base session length**, price, validity,
   optional coach). Bought via Yoco upfront → a **token wallet** whose balance is held in **MINUTES**
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
- **Coach month-end statement** (`/statement.html`): per client — lessons, paid-via-Yoco + owed
  (arrears) = **net balance**; mark arrears collected.
- **Owner cockpit** (`/api/admin/financials/*`): revenue by service, **commission owed + rent due per
  coach**, membership MRR; reconciles (collected − commission = coach net).
- Splits/accruals are **idempotent** (a replayed webhook never double-charges).

## 7. Self-service per role
- **Client (`/account.html`):** edit profile/demographics (**email read-only = identity**); manage
  **children/dependents**; **Financials** (current plan, usage this month, spend per month + history,
  next charge); raise **refund requests**. Buy membership (`/membership.html`) + packs (`/packs.html`).
- **Coach (`/coach.html`):** edit profile (bio, photo, specialties, languages, qualifications,
  visibility toggles); set **per-duration lesson rates**; **own lesson packs** (Packs tab →
  `/api/coach/bundle-plans`, scoped + ownership-guarded); availability + time-off; **My Clients**
  (derived, private to that coach); **Dashboard cockpit** (lessons, hours, gross + **net-of-commission**
  earnings, fill rate, new-vs-returning, top clients, trend); **Statement** (month-end money).
- **Owner (`/admin.html`, `/settings.html`):** master diary; resources/courts; people (+ membership
  grant); classes; a consolidated **Settings → Pricing** tab (court rates · packs · memberships, each
  with the active/dormant/retired control + membership "Access hours"); **Coach pay** (rent +
  commission rules); payments (online-payments toggle) + refunds + refund-requests; **Cockpit/Financials**;
  onboarding; branding; policy.

## 8. Notifications
- In-app **bell + inbox** (topbar) for every member; driven non-fatally off `emit()`. Kinds:
  booking confirmed, payment receipt (links to the receipt), membership active, pack activated,
  refund requested/decided, class enrolled/waitlisted/spot-open, coach invited.
- **Email** path (SES transactional) lights up when keys are set; until then the inbox works fully and
  email is `skipped`. Child events notify the **guardian**. (See OUTSTANDING.md for the keys.)

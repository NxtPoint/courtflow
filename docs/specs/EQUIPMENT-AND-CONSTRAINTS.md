# EQUIPMENT & CONSTRAINTS — equipment hire · peak pricing · membership entitlements · configurable trial

Status: **SHIPPED + LIVE on `master` / prod (merged 2026-07-12).** All four capabilities shipped reuse-first
on the existing engines. Every value is owner-configured data (white-label); every new row is `club_id`-scoped;
all boot DDL is idempotent (`python -m db` twice = no-op). Gate baseline at merge: **booking 180 / billing 311
/ statement 47**; current **booking 474 / billing 687 / statement 64**.
Commits: peak `9703ee2` · membership+trial `08c9820` · equipment `db24db9` (spec `36450d6`).

> ## ⚠ SUPERSEDED IN FOUR PLACES (2026-07-27/29) — read this first
> Live use found real money leaking through three of the four capabilities in this doc. The **as-built
> behaviour is now in [BUSINESS-RULES.md](BUSINESS-RULES.md) §2–§4**; where this doc and that one differ,
> BUSINESS-RULES is right. The deltas:
>
> 1. **PEAK IS PER COURT, not one club window.** `diary.resource.peak_override` + its own
>    `peak_days`/`peak_start_min`/`peak_end_min` give three states — inherit the club window, use the
>    court's own, or (with an **EMPTY** window + override on) **never peak**. That third state is the point:
>    a nullable window alone could only ever ADD peak, never remove it. BOTH price paths must pass the
>    court (`availability._slot_price` = shown, `_create_order_guarded._price` = charged).
>    Guarded by `sc_peak_hours_can_differ_per_court`.
> 2. **CAPS HAVE A CLUB-LEVEL FLOOR** (`club.policy.default_max_*`). Caps living only on `billing.price`
>    never reached the price-less signup trial, and `active_caps._best` read a NULL cap as "an
>    unconstrained tier wins" — so merely HOLDING the trial cancelled a paid tier's caps too. **The owner's
>    rule — one covered booking a day, 90 min max — is exactly `default_max_covered_per_day=1` +
>    `default_max_covered_minutes=90`.** Also: `/api/me/plan` must report the **effective** cap (COALESCEd
>    to the club default), or the picker offers a duration the server will then charge for.
> 3. **ENTITLEMENT IS EVALUATED ON THE BOOKING'S DATE, never `CURRENT_DATE`.** Members could book forward
>    past their own expiry and the row was written `membership_covered` at R0 **permanently**. Not
>    trial-specific — any member could book out next month on their last day and not renew.
> 4. **EQUIPMENT PAYS LIKE A SERVICE, is SCOPED TO A COURT SERVICE, and THE COURT IS STILL CHARGED.**
>    `_create_order_guarded` used to **hard-code** the kit's order to `at_court` on a covered/free court —
>    so a card-only club got an uncollectable owed debt on a confirmed booking. Now every requested item's
>    own `payment_modes` are intersected before any insert (`EQUIPMENT_NOT_PAYABLE` → **refused, not
>    granted**). `diary.equipment_service` scopes kit to court services (**no rows = all services**), and
>    the guard is re-checked server-side (`EQUIPMENT_NOT_FOR_SERVICE`) because `addons` arrives off the
>    request body.
>
> Also new since this doc: **ONE PERSON, ONE PLACE** — the GiST constraint is keyed on `resource_id`, so it
> could never stop a coach holding a court AND teaching a lesson AND running a class at 09:00.
> `bookings._coach_commitment_at` checks all three shapes; members are deliberately NOT blocked (their 2nd
> concurrent covered court just downgrades to PAYG).

The client-Home **hero tile** for a `feature_on_home` equipment item is now in (`client.js` → a Home "Book a
session" tile that starts a court booking with the item pre-added via `BookFlow.start(..., {featureEquipment})`).
Live Clerk-authenticated click-through of all three apps is the recommended acceptance check (not possible
from the build env).

> Read alongside [BUSINESS-RULES.md](BUSINESS-RULES.md) §3–§4 (pricing + purchasing models) and
> [SYSTEM.md](SYSTEM.md) (the diary + billing engines). This doc is the delta.

---

## 0. THE GOVERNING PRINCIPLE — silent constraints, one resolver

Owner mandate (verbatim intent): *"when we constrain a court for members they should just not have the
option … the 120-min should be hidden … they should never feel it or feel like their membership is
diminished. It should be silent, and this applies to all constraints."*

Therefore a member's **booking options and prices are pre-shaped to their entitlement** — there is never a
denial, a "you exceeded" message, or a downgrade-at-checkout surprise. A single new guarded module is the
source of truth both the calendar and the booking engine read from, so **shown == charged == allowed**:

**`diary/entitlement.py`** —
```
resolve_entitlement(session, *, club_id, user_id, starts_local, booking_type, resource_id, product_id)
  → { covered: bool,            # is a court booking here free under an active membership?
      max_covered_minutes,      # longest covered single booking at this time (None = no cap)
      courts_left_today,        # distinct covered courts still allowed today (None = no cap)
      bookings_left_today,      # covered bookings still allowed today (None = no cap)
      service_members_covered } # is this court service member-eligible at all (clay = False)?
```
- **`diary/availability.compute_availability`** reads it → for a member: covered slots show free; **durations
  above `max_covered_minutes` are simply omitted** from the picker at that time; once the daily court/booking
  entitlement is used, further slots that day show the **normal/PAYG price** (fair — the member's own usage,
  owner-approved); a non-member-eligible court service (clay) shows its PAYG price. No errors, ever.
- **`diary/bookings.create_booking`** reads the SAME resolver → any request beyond entitlement silently
  settles PAYG (`settlement_mode='at_court'`) — the invisible server-side backstop for a crafted/stale
  request. It mirrors the existing `membership_covered → at_court` downgrade (`bookings.py`).

Guarded like `diary/pricing.py`: billing absent or anything unexpected → treat as unconstrained/uncovered,
never block a booking.

---

## 1. Equipment hire (ball machine, racquets, balls)

Equipment is an **availability-tracked, flat-fee add-on on a court booking** — NOT a separate booking type,
NOT holding its own court (the court is already booked). One definition; visibility is a per-item flag.

### Schema (idempotent)
- `billing.product.kind` CHECK → add `'equipment'`.
- `diary.resource.kind` CHECK → add `'equipment'`; new columns `quantity int NOT NULL DEFAULT 1`,
  `feature_on_home boolean NOT NULL DEFAULT false`. (`product_id` already exists → the item's price product.)
- New `diary.booking_equipment(id, club_id, booking_id, resource_id, qty, price_id, amount_minor, created_at)`
  — the items attached to a booking (drives billing AND availability counting). Indexed on `(booking_id)`
  and `(club_id, resource_id)`.
- `billing.price` for each item = one **flat** `unit='per_booking'` row (no duration).

### Availability — by TIME, not court; race-safe
- `diary/equipment.py::available_units(session, *, club_id, resource_id, starts, ends)` =
  `quantity − SUM(be.qty)` over `booking_equipment be JOIN diary.booking b` where `b.status IN
  ('held','confirmed')` and `[b.starts_at, b.ends_at)` overlaps `[starts, ends)`. Pure time overlap — a single
  ball machine is unavailable in its hired hour regardless of which court.
- At booking: `SELECT … FOR UPDATE` on the equipment `diary.resource` row, re-check the count inside the
  transaction, refuse the unit that would exceed `quantity` (`EQUIPMENT_UNAVAILABLE`). Mirrors the
  class-capacity concurrency pattern (`diary/classes.py`). The ball machine can never double-book.

### Billing — exactly one line, no double bill
- Equipment lines are appended to the court booking's `lines` in `diary/bookings._create_order_guarded`,
  **inside the same transaction** → one order, one payment, one Yoco/desk settle. Cannot be written twice.
- Cancel voids the whole order incl. equipment lines (no orphan charge); reschedule keeps the flat fee once
  (never re-adds). Booking-count/insights readers ignore equipment lines (no phantom bookings).
- Equipment lines carry no coach → commission never accrues on them.

### Frontend (`frontend/js/booking.js`)
- **Hero "Ball Machine" tile** on client Home, gated by `feature_on_home` → opens the court flow with the
  machine pre-selected/featured + live "1 available".
- Quiet **"Add equipment"** section in `renderConfirm` for `feature_on_home=false` items ("Racquets — 8 of
  10 left, +R20"). Selection → `body.addons=[{resource_id, qty}]` → route reads it → `create_booking(addons=)`.
  Preserve `res.booking.order_id → Pay.startYocoCheckout`.

### Setup
- New **Equipment** section: `AdminUI.equipmentManage` (clone of `courtsManage`) → `/api/admin/equipment`
  CRUD (name · flat price · quantity · feature-on-home · lifecycle). `club_admin+`.

---

## 2. Peak PAYG pricing — court hire only

Explicit peak price per duration + one club-wide peak window. Coverage still wins first (a covered member
inside their window is free; outside → the peak PAYG price).

### Schema
- `club.policy` → `peak_days text` (CSV ISO weekdays), `peak_start_min int`, `peak_end_min int` (minutes
  from midnight; same shape as membership access windows). NULL = no peak. Confirmed default target:
  Mon–Fri **17:00–19:00** (`peak_start_min=1020, peak_end_min=1140`), owner-editable.
- Court `billing.price` → `peak_amount_minor int` (NULL = no uplift for that duration). **Explicit** amount
  (customer sees a clean R250, not a % remainder).

### Engine (the two-places-in-lockstep rule)
- `diary/pricing.price_for(…, at_local=None)` → returns `peak_amount_minor` when `at_local` is inside the
  club peak window AND a peak amount is set, else `amount_minor`. A small `_in_peak_window(session, club_id,
  local_dt)` helper (mirrors `any_window_covers`).
- `diary/availability.compute_availability._slot_price` passes the slot's local start → calendar shows R150
  off-peak / R250 peak.
- `diary/bookings._create_order_guarded._price` passes the booking's local start → charges the same. Peak
  applies to `booking_type='court'` only.

### Setup
- **Peak hours** card in Club-profile (reuse the access-hours day+time editor).
- **Peak price** field beside each court duration in `frontend/js/service_editor.js` `variationsCard`, plumbed
  through `POST/PATCH /api/services/<id>/variations` → `admin_repo.create_price`/`patch_price`.

---

## 3. Membership entitlements (anti-abuse, silent)

All on the membership tier (`billing.price`), reusing the access-window shape; enforced via §0's resolver.

### Schema
- Existing: `access_days` / `access_start_min` / `access_end_min` (covered hours — kept).
- New on `billing.price`: `max_covered_minutes int` · `max_covered_per_day int` · `max_courts_per_day int`
  (all NULL = no cap).
- **Court-service exclusion** — `billing.product` (a court service) → `members_covered boolean NOT NULL
  DEFAULT true`. The clay court's service = `false` → **never** covered, PAYG for all. Reuses court services;
  `resolve_entitlement` checks it.

### Behaviour (all silent — see §0)
- **Access hours** — covered inside the window; outside → PAYG price (existing per-slot behaviour).
- **Max covered minutes** — the member's duration picker at a covered time shows only durations ≤ cap
  (the 120 chip is hidden at 17:00 under a 90-min cap). Any longer request → PAYG server-side.
- **Max covered bookings/day & max courts/day** — once used, further slots that day show the normal/PAYG
  price (owner-approved: their own usage; not hidden, because real availability shouldn't be masked).
- **Clay court** — always shows its PAYG price to members.
- **DROPPED (owner call):** a club-level "only N courts for members at peak" concurrent cap — it punished a
  well-behaved member for others' timing and made membership feel worthless. The per-member caps above solve
  the actual abuse ("one member + friends hog courts") fairly and predictably; peak reservation is achieved
  through tier design (a tier's access window) + peak pricing instead.

### Setup
- Extend the existing tier editor (`AdminUI.membershipServices` `openTier`, `admin_api.js`) with a
  **"Member limits"** sub-card (max minutes · max bookings/day · max courts/day) beside the Access-hours card.
- **"Members covered?"** flag on the court-service editor.

---

## 4. Configurable trial — a real membership tier

Make the trial a first-class, owner-configured membership (the owner's explicit ask) so ALL the §3 rules apply
to trial members automatically.

### Schema / behaviour
- `billing.price` → `trial_days int` · `is_trial boolean NOT NULL DEFAULT false`. A tier with `is_trial=true`
  IS the signup trial; `trial_days` scales the length (0 = trials off).
- `billing.membership.grant_signup_trial` → links the new subscription to the **trial tier's `price_id`** and
  sets `current_period_end = today + tier.trial_days`. Falls back to the `SIGNUP_TRIAL_DAYS` env only when no
  trial tier is configured (so nothing regresses before the owner sets one up).
- The trial then inherits every §3 entitlement (access hours, caps, clay exclusion, max courts/day) with no
  special-casing.
- **Preserve** the "genuinely-new `iam.user` only" grant guard (`auth/principal.py`, `_created=True`), the
  one-shot idempotency, and the court-only rule.
- **Backward-compatible:** existing live trials (`price_id=NULL`) already read as "unconstrained" in
  `membership_covers` and keep working untouched until/if re-granted.

### Setup
- A **"This tier is the signup trial (N days)"** toggle + days field on the tier editor.

---

## 5. Build order, gates & safety (live system)

1. **Branch** `feat/equipment-and-constraints` (not master). Each feature an independent, always-green
   increment; merged to `master` only after Tomo's review (auto-deploy = production).
2. **Order:** Peak pricing → Membership entitlements + `entitlement.py` → Configurable trial (builds on it)
   → Equipment. Each shippable alone.
3. **Every step gated:** `python -m py_compile (git ls-files '*.py')` · `python -m db` **twice** (idempotency)
   · `python -m scripts.test_all` · `node --check` on touched JS.
4. **New scenario tests prove:** no double-booking (1 ball machine, overlap → refused) · no double-billing
   (equipment = exactly one line; cancel voids it) · silent duration cap (member at peak sees no 120, charged
   what's shown) · clay never covered · caps → PAYG · trial inherits caps · peak shown == peak charged.
5. **Defaults are inert** — every new control defaults to off/unconstrained, so existing customer behaviour is
   unchanged until the owner configures it. Nothing hardcoded; every new row `club_id`-scoped + idempotent.

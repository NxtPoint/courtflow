# 03 — The Diary & Booking Engine (the heart)

> Tomo: *"the diary management system is huge — ONE single diary solution with full edit, cancel,
> book, reschedule, that coaches and clients can use."* This doc is the contract for that engine.

## 1. Core principle: one diary, many lenses

There is **one** set of bookings (`diary.booking` + `diary.class_session`/`enrolment`). What changes
is the **lens** you view it through:

| Lens | Who | Shows |
|---|---|---|
| **Club master diary** | club_admin, front desk | All resources (courts + coaches + classes) on one timeline; drag to reschedule; click to edit/cancel. |
| **Coach diary** | coach | Only *my* lessons + classes I run + my availability/time‑off. |
| **Member diary** | member | "My bookings" (courts I booked, lessons, classes I'm enrolled in) + a public availability view to book new. |
| **Public availability** | guest/visitor | Read‑only court/lesson/class availability → book (with login). |

Same underlying rows; role + `club_id` decide what's returned. No separate "coach calendar" data —
that was the Wix mistake.

## 2. The three booking types (unified lifecycle)

All three are `diary.booking` rows (classes also use `class_session`/`enrolment`) and share the
**same lifecycle**: `held → confirmed → (completed | cancelled | no_show)`.

### 2.1 Court booking
- Pick **court** (or "any available hard court"), date, time, duration (≥ `club.policy.min_booking_minutes`).
- Audience pricing: **member** (covered by membership, R0 or membership‑covered), **visitor** (R150),
  **member‑guest** (R80, requires a member host → `booking_party.party_role='host'`).
- Clay court = its own resource + premium price.
- Settlement: membership‑covered / pay‑at‑court / monthly‑account / online (when live).

### 2.2 Lesson booking (book a named coach)
- Pick **coach** → see *that coach's* availability (coach resource availability minus existing
  bookings/time‑off) → pick slot + duration → 1:1 or small group (`booking_party` holds participants).
- Optionally also reserves a **court** (a lesson usually needs a court): create a linked court
  booking in the same transaction (two `diary.booking` rows sharing an `order_id`), or model the
  lesson as occupying both the coach and a court via two resource holds. **Recommended:** one lesson
  booking + one auto‑held court booking, linked by `order_id`, both under the conflict guard.
- Price from `iam.coach_profile.default_lesson_price_id` or a per‑coach price list.

### 2.3 Class enrolment (Cardio Tennis, junior squads, socials)
- `class_session` is a scheduled instance (recurring via `diary.recurrence` → generated sessions).
- Member enrols → `enrolment` row; capacity enforced; over‑capacity → `waitlisted`.
- Confirmation per enrolment; reminders per session; attendance marked by coach.

## 3. Availability computation

`GET /api/diary/availability` is the workhorse. Inputs: `club_id`, `resource_id` (or kind + filters),
date range, duration, audience. Algorithm:

1. Expand `availability_rule` for the resource across the range into candidate slots
   (`slot_minutes`).
2. Subtract `time_off` blocks.
3. Subtract existing `booking`/`class_session` rows in `held`/`confirmed` for that resource.
4. Apply `club.policy.booking_window_days` (can't book beyond the window) and lead‑time (can't book
   in the past / within min lead).
5. For "any court", union across all matching court resources and collapse to free slots.
6. Return free slots with the **price for the caller's audience** attached.

Keep it a **server‑side computed view** (don't materialise availability; compute on read with good
indexes). Cache per (resource, day) for a few seconds if needed.

## 4. Booking creation — the concurrency‑safe path

Double‑booking is the cardinal sin. Two guarantees:

1. **DB‑level exclusion constraint** on `diary.booking`:
   `EXCLUDE USING gist (resource_id WITH =, tstzrange(starts_at, ends_at) WITH &&) WHERE (status IN
   ('held','confirmed'))`. Postgres physically refuses an overlapping confirmed/held row → the second
   concurrent booking gets a constraint violation we translate to `409 SLOT_TAKEN`.
2. **Short‑lived `held` state** for multi‑step flows (esp. when online payment is added): create the
   booking as `held` with a `held_until` (e.g. 5 min) while the user pays; a cron/`capacity-sweep`
   releases expired holds back to free. On payment/confirm → `confirmed`.

Creation flow (transaction):
```
BEGIN
  insert booking(status='held', ...) -- exclusion constraint enforces no overlap
  -- for lessons: insert linked court hold (same order_id)
  insert order(settlement_mode=..., status='open'|'awaiting_payment')
  insert order_line(s)
COMMIT
→ if settlement=at_court/monthly/membership: immediately set booking.status='confirmed',
     order.status='paid'(membership)/'open'(tab)
→ if settlement=online: keep 'held', return checkout intent (doc 05); confirm on webhook
→ emit booking_confirmed event → Klaviyo confirmation (doc 06)
```

## 5. Edit / reschedule / cancel (full CRUD, coach + client)

| Action | Who | Rules |
|---|---|---|
| **Reschedule** | booker, coach (own), club_admin | New slot checked via the same exclusion constraint (atomic move = update `starts_at/ends_at` in one tx; rollback if conflict). Honour `cancellation_cutoff_hours` for member‑initiated; admins/coaches override. Recurring: "this / this+future / whole series" choice. |
| **Cancel** | booker, coach (own), club_admin | Set `status='cancelled'`, stamp `cancelled_by/at/reason`. If within free‑cancel window → no fee + release; if past cutoff → apply `no_show_fee` / keep charge per policy. Free the slot (waitlist promotion, §6). Emit `booking_cancelled` → Klaviyo. |
| **Edit details** | booker, coach, admin | Change participants (`booking_party`), notes, audience/price (admin), add/remove court on a lesson. |
| **No‑show / complete** | coach, admin | Mark `no_show` (may trigger fee) or `completed` (enables attendance, lesson notes, and—future—a feedback/NPS prompt). |
| **Block time** | coach, admin | Create `time_off` (coach leave, court maintenance) — removes availability without a "booking". |

**Recurring edits** use the `recurrence_id`: editing one occurrence detaches it (exception); editing
the series updates the RRULE and regenerates future, non‑modified occurrences.

## 6. Waitlists & slot release

- Court: if a desired slot is full, user joins `diary.waitlist` for (resource, desired_start). When a
  booking cancels, the sweep notifies the earliest matching waitlister (Klaviyo "a slot opened") with
  a short claim window.
- Class: enrolment beyond `capacity` → `waitlisted`; on a cancellation the earliest waitlisted
  enrolment auto‑promotes to `enrolled` + confirmation.

## 7. Reminders, no‑show sweep, monthly run (crons)

Reuse 1050's "cron = thin trigger, endpoint owns logic" pattern.

| Cron | Cadence | Does |
|---|---|---|
| `cron_reminders` | hourly | Find bookings/sessions starting in T‑24h and T‑2h without a reminder sent → emit `booking_reminder` (Klaviyo). |
| `cron_capacity_sweep` | every few min | Release expired `held` bookings; promote waitlists; mark past `confirmed` lessons needing attendance. |
| `cron_monthly_invoice` | 1st of month | For each user with an open `account_ledger` balance (monthly settlement) → produce a statement order + Klaviyo "your monthly statement" (and, when online pay is live, a pay link). |
| `cron_membership_refill` | per period | Roll membership periods, mark lapsed memberships. |

## 8. Diary API surface (booking endpoints)

All under `/api/diary/*`, Clerk‑JWT auth, `club_id` from principal, role‑gated.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/diary/availability` | Free slots for court/coach/class (filters: kind, resource_id, coach_id, date range, duration, audience). |
| GET | `/api/diary/resources` | List bookable resources for the club (courts, coaches, classes). |
| POST | `/api/diary/bookings` | Create a court/lesson booking (body: type, resource, time, parties, settlement_mode). Returns booking (+ checkout intent if online). |
| GET | `/api/diary/bookings` | My bookings (member) / all (admin) / mine‑as‑coach. Filters by date, status, resource. |
| PATCH | `/api/diary/bookings/:id` | Reschedule / edit (atomic, conflict‑checked). |
| POST | `/api/diary/bookings/:id/cancel` | Cancel (applies policy, frees slot, waitlist promote). |
| POST | `/api/diary/bookings/:id/status` | Mark completed / no_show / attended (coach/admin). |
| GET | `/api/diary/classes` | List class sessions (with capacity/spots left). |
| POST | `/api/diary/classes/:id/enrol` | Enrol / waitlist a player. |
| POST | `/api/diary/classes/:id/cancel-enrolment` | Cancel an enrolment. |
| POST | `/api/diary/time-off` | Coach/admin block time. |
| GET | `/api/diary/master` | Club master diary (admin) — all resources, date range, for the calendar UI. |

## 9. The diary UI (what to build)

- **Calendar component** (week/day views, resource columns) — the master diary. Drag‑to‑reschedule,
  click‑to‑create, colour by booking_type/status. (Use a mature calendar lib, e.g. FullCalendar
  resource‑timeline, or build on a lightweight grid; keep it a single SPA page like 1050's dashboards.)
- **Booking wizard** (member): choose type → court/coach/class → slot → parties → settlement → confirm.
- **Coach view**: my week, my classes (rosters + mark attendance), my availability editor.
- **Mobile‑first**: members book on phones (Playtomic UX bar). Reuse 1050's responsive CSS system,
  green palette adapted to NextPoint branding.

## 10. Edge cases to honour (write tests/asserts for these)

- Concurrent identical court booking → exactly one wins (`409 SLOT_TAKEN`).
- Lesson that needs a court but no court free → block with a clear message (offer alternative slots).
- Reschedule into a conflicting slot → rejected atomically, original preserved.
- Cancel inside cutoff → fee applied per policy; outside cutoff → clean release.
- Class at capacity → waitlist; promotion on cancel; never exceed capacity.
- Member‑guest booking without a member host → rejected if `policy.guest_requires_member`.
- Timezone correctness: store `timestamptz`, compute/display in `club.timezone` (JHB). Never naive
  local times. DST is moot for JHB but keep it correct for future clubs.
- Minor booking a lesson → guardian linkage + parental‑consent check.

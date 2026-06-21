# Coach Self-Service + Business Cockpit — Spec

Status: DRAFT (implementation-ready). Owner: Coach lane (`coach/`). Coordinates with: Billing (`billing/`),
Diary (`diary/`), CRM (`marketing_crm/`, `core/`), Admin/Owner (`admin/`).

The product vision (owner's brief): **"Uber for coaches."** The club gives the client the booking platform;
each coach gets a **cockpit view of their world** — their clients, their monthly usage, their revenue
earned, their schedule. That cockpit is the CRM tooling proven in Ten-Fifty5 (1050). This spec scopes the
full coach surface and **phases** it: self-service editing first, the analytics cockpit (CRM) as Phase 2.

---

## 0. What exists today vs what's missing

### EXISTS (shipped, verified by reading source)
| Capability | Where |
|---|---|
| Coach profile read/patch (display_name, headline, bio, photo_url, specialties; + first/surname/email/phone on `iam.user`) | `coach/routes.py` `GET/PATCH /api/coach/profile`; `coach/repositories.py` `get_profile`/`patch_profile` |
| Weekly working hours editor → coach's `diary.resource(kind='coach')` + `diary.availability_rule` | `PUT /api/coach/hours`; `replace_hours` |
| Lesson services & rates CRUD (`billing.product kind='lesson'` + `billing.price`, scoped by `coach_user_id`) | `GET/POST/PATCH/DELETE /api/coach/services`; `list/create/patch/deactivate_service` |
| Coach owns/creates classes; schedule recurring/one-off sessions; cancel session | `GET/POST /api/coach/classes`, `/classes/<rid>/schedule`, `/classes/<rid>/sessions`, `/classes/sessions/<sid>/cancel`; `diary/classes.py` |
| Onboarding wizard + step derivation + completion flag | `GET /api/coach/onboarding`, `POST /api/coach/onboarding/complete`; `onboarding_steps`; `iam.coach_profile.onboarding_completed` |
| Photo upload presign (S3 when configured, else URL-paste fallback) | `POST /api/coach/photo-presign` |
| Coach console: "My week" (lessons + classes I run), mark completed/no-show, rosters + attendance, time-off editor, profile/hours/services tabs, **book-on-behalf** of a client | `frontend/js/coach.js`, `coach_api.js`, `class_ui.js` |
| Coach's lessons listing (`coach_user_id` scope), book-on-behalf via `booked_for_user_id`, reschedule, cancel, completed/no-show | `diary/bookings.py` `list_bookings(as_coach=True)`, `create_booking(booked_for_user_id=...)`, `reschedule_booking`, `cancel_booking`, `set_status` |
| Per-lesson/per-party attendance | `diary.booking_party.attended` (bool); `set_attendance` |
| Per-class attendance / no-show | `diary.enrolment.status IN ('attended','no_show')`; `mark_attendance`; `roster` |
| Time-off / holiday blocks | `diary.time_off` table; `POST /api/diary/time-off` (coach blocks own resource); subtracted in `compute_availability` |
| Admin payments list (succeeded charges) | `admin/repositories.py` `list_payments`; `GET /api/admin/payments` |

### MISSING (this spec adds)
- **Profile fields:** `languages`, `qualifications`, `years_experience`, `public_visibility` toggle (distinct
  from `is_bookable`), and surfacing/editing `is_bookable` + `rank` in the coach editor.
- **Per-duration lesson rates in the coach editor** — `create_service` defaults `unit='per_hour'` with a free
  `duration_minutes`; the platform pricing model is **per-duration `unit='per_booking'`** (`diary/pricing.py`,
  CLAUDE.md). The coach services editor must produce per-duration `per_booking` rows to match how booking
  prices resolve. **This is a real gap/bug today.**
- **Clients view** — no endpoint derives "who has booked me." Entirely new.
- **Business cockpit / CRM** — no coach-facing analytics endpoints, views, or queries exist. Entirely new.
- **Commission model** — there is **NO commission concept anywhere** in the codebase (grep: zero hits in
  schema, billing, diary, docs). "Revenue earned net of commission" requires introducing a commission rate
  and a net-revenue calc. **This must be coordinated with the owner/admin spec** (the owner sets the rate;
  the coach sees the result). See §6.
- **Reschedule from the coach console UI** (the API exists; the console only does completed/no-show).

---

## 1. Scope & non-goals

In scope: everything a coach edits about themselves and their offering; managing their lessons & classes;
seeing their clients; and the business cockpit (usage + revenue + trends). Out of scope (separate specs):
owner/admin cockpit and commission-rate administration (coordinated, not built here); coach payouts/settlement
banking; self-serve membership purchase; messaging/chat with clients (Phase 3 candidate).

---

## 2. Roles, scoping & security (non-negotiable)

Mirror the existing `coach/routes.py` discipline exactly:
- `_COACH_ROLES = ("coach", "club_admin", "platform_admin")`. Resolve `principal` via `auth.resolve_principal`.
- **`club_id` AND `user_id` come from the principal, never the body.** Every query is scoped
  `WHERE club_id = :c AND <coach_user_id|owner-of-resource> = :u`.
- **A coach sees only their own world.** Clients, revenue, usage are all filtered to bookings/classes where
  `coach_user_id = principal.user_id`. Cross-coach access is impossible by construction (same pattern as
  `owns_class_resource`/`owns_class_session`).
- **Privacy boundary on the Clients view:** a coach sees a client's name, contact, and *the history of that
  client with THIS coach* — never the client's bookings with other coaches, other-coach spend, or
  club-wide PII. No minor PII in any payload (Phase 4 CRM rule). Contact details shown only for clients who
  have an actual booking relationship with the coach.

---

## 3. Editable coach profile / details (Phase 1)

### 3.1 Data model additions (`coach/schema.py`, idempotent `ADD COLUMN IF NOT EXISTS`)
The coach lane owns additions to `iam.coach_profile` (it must not edit `iam/schema.py`). Add:

```sql
ALTER TABLE iam.coach_profile ADD COLUMN IF NOT EXISTS languages          text[];
ALTER TABLE iam.coach_profile ADD COLUMN IF NOT EXISTS qualifications     text[];
ALTER TABLE iam.coach_profile ADD COLUMN IF NOT EXISTS years_experience   int;
ALTER TABLE iam.coach_profile ADD COLUMN IF NOT EXISTS public_visibility  boolean NOT NULL DEFAULT true;
-- is_bookable, rank, display_name, headline, bio, photo_url, specialties, default_lesson_price_id already exist.
```

`public_visibility` = appears on the public marketing/booking directory. `is_bookable` = accepts new lesson
bookings (a coach can be visible-but-not-bookable, e.g. fully booked). Keep both.

### 3.2 Full field list & validation
| Field | Source table | Type | Validation |
|---|---|---|---|
| `display_name` | `iam.coach_profile` | text | ≤ 80 chars; trimmed; falls back to user name if empty |
| `headline` | `iam.coach_profile` | text | ≤ 120 chars |
| `bio` | `iam.coach_profile` | text | ≤ 2000 chars |
| `photo_url` | `iam.coach_profile` | text | https URL or S3 key from presign; ≤ 500 |
| `specialties` | `iam.coach_profile` | text[] | list; each ≤ 40; ≤ 12 items |
| `languages` | `iam.coach_profile` (NEW) | text[] | list; ≤ 10 items |
| `qualifications` | `iam.coach_profile` (NEW) | text[] | list; each ≤ 120; ≤ 20 |
| `years_experience` | `iam.coach_profile` (NEW) | int | 0–80 |
| `is_bookable` | `iam.coach_profile` | bool | — |
| `public_visibility` | `iam.coach_profile` (NEW) | bool | — |
| `rank` | `iam.coach_profile` | int | admin-only writable; coach read-only |
| `first_name`,`surname`,`phone` | `iam.user` | text | names ≤ 60; phone E.164-ish ≤ 20 |
| `email` | `iam.user` | text | read-only in coach editor (identity-linked) |

### 3.3 Endpoints (extend `/api/coach/*`)
- `GET /api/coach/profile` — unchanged shape, **add the new fields** to the SELECT in `get_profile` and to the
  serialized payload.
- `PATCH /api/coach/profile` — extend `patch_profile` to COALESCE the new columns (`languages`,
  `qualifications`, `years_experience`, `is_bookable`, `public_visibility`). Reject non-list arrays for
  `languages`/`qualifications` (same guard as `specialties`). `rank` ignored from coach body.
- Hours: `PUT /api/coach/hours` — unchanged.
- Time-off: **add `GET /api/coach/time-off` + `DELETE /api/coach/time-off/<id>`** (today only `POST` exists,
  via diary). Coach must own the resource (`owns_coach_resource`). List shows upcoming blocks so the coach can
  remove a holiday.

### 3.4 Services & rates — align to per-duration model (Phase 1, fixes gap)
`create_service` currently writes `unit='per_hour'`. Change the **coach services editor + `create_service`** to
write **one `billing.price` row per offered duration** with `unit='per_booking'`, `duration_minutes` set,
`audience='any'` — exactly what `diary/pricing.py price_for(kind, duration_minutes)` resolves against.

UI (Services tab): a coach defines a lesson product (name) and adds rate rows: `{duration_minutes, amount_minor}`
(e.g. 30 min = R250, 60 min = R400). `default_lesson_price_id` points at the first/primary rate.
Endpoints unchanged in shape; `create_service`/`patch_service` semantics adjusted:
- `POST /api/coach/services` body `{name, duration_minutes, amount_minor, unit:'per_booking'}` — default `unit`
  becomes `per_booking`.
- Add `POST /api/coach/services/<product_id>/rate` to add additional durations to an existing lesson product
  (so a coach offers 30/60 under one "Private lesson" product), or keep one product per duration (simpler;
  matches current `list_services` flattening). **Open question §11.**

---

## 4. Lessons & classes management (Phase 1 — mostly built, close gaps)

Already built: list my lessons (`as_coach`), book-on-behalf, mark completed/no-show, create/schedule/cancel
classes, rosters, attendance. **Gaps to close:**
1. **Reschedule from the console** — wire `PATCH /api/diary/bookings/<id>` into `coach.js` (drag or a "Reschedule"
   modal). API + conflict-safety already exist.
2. **Per-party attendance for lessons** — `set_attendance` exists but the console only sets booking-level
   completed/no-show. Surface per-party attendance where a lesson has multiple parties (rare for 1:1; needed
   for pairs/groups).
3. **Cancel a lesson with reason** — `cancel_booking` exists (applies/skips fee per policy + role). Add a
   "Cancel" action to the lesson row (coach role bypasses the member cutoff). Surfaces `fee_applied`/`waitlist_notified`.
4. **Class roster export/contact** — roster already returns name/email; add a "message/export" affordance
   (Phase 3 for messaging).

No new tables. Reuse `diary/bookings.py` + `diary/classes.py` wholesale.

---

## 5. Clients view (Phase 2a — the CRM foundation)

A coach's client = any user who has a **lesson or class** with this coach. There is no `client` table; **derive
it from bookings + enrolments.** The owning user is `booking.booked_by_user_id` (note: book-on-behalf persists
the *client* as `booked_by_user_id`, so this is correct — verified in `diary/bookings.py`, the
`booked_for_user_id` param maps to `owner_user_id` → persisted `booked_by_user_id`).

### 5.1 Read endpoints
- `GET /api/coach/clients?search=&limit=` — the coach's client list.
- `GET /api/coach/clients/<user_id>` — a single client's 360 *with this coach only* (history, attendance,
  lifetime spend with this coach, last seen, upcoming).

### 5.2 What's shown
List row: `{user_id, name, email, phone, first_seen, last_seen, lessons_count, classes_count,
upcoming_count, lifetime_spend_minor, no_show_count}` — **`lifetime_spend_minor` and revenue are
gross-with-this-coach** (commission applies to the coach's *earnings*, not what the client paid — §6).
Detail adds: booking history (date, type, status, amount, settlement_mode), attendance record, NPS/feedback if
fed by CRM (Phase 2b).

### 5.3 Aggregation SQL sketch (clients list)
```sql
-- Lessons the coach ran, grouped by the client (booked_by_user_id), this club only.
WITH coach_bookings AS (
  SELECT b.booked_by_user_id AS user_id, b.id AS booking_id, b.booking_type,
         b.starts_at, b.status, b.order_id
  FROM diary.booking b
  WHERE b.club_id = :c AND b.coach_user_id = :u
    AND b.booked_by_user_id IS NOT NULL
    AND b.status IN ('confirmed','completed','no_show')
),
-- Classes the coach ran, grouped by the enrolled user.
coach_classes AS (
  SELECT e.user_id, e.id AS enrolment_id, cs.starts_at, e.status, e.order_id
  FROM diary.class_session cs
  JOIN diary.enrolment e ON e.class_session_id = cs.id AND e.club_id = cs.club_id
  WHERE cs.club_id = :c AND cs.coach_user_id = :u
    AND e.status IN ('enrolled','attended','no_show')
),
spend AS (   -- gross paid that maps to this coach's orders (see §6 for net)
  SELECT o.user_id, SUM(p.amount_minor) AS paid_minor
  FROM billing.payment p
  JOIN billing."order" o ON o.id = p.order_id AND o.club_id = p.club_id
  WHERE p.club_id = :c AND p.direction = 'charge' AND p.status = 'succeeded'
    AND o.id IN (SELECT order_id FROM coach_bookings WHERE order_id IS NOT NULL
                 UNION SELECT order_id FROM coach_classes WHERE order_id IS NOT NULL)
  GROUP BY o.user_id
)
SELECT u.id AS user_id, u.first_name, u.surname, u.email, u.phone,
       MIN(x.starts_at) AS first_seen, MAX(x.starts_at) AS last_seen,
       COUNT(*) FILTER (WHERE x.kind='lesson') AS lessons_count,
       COUNT(*) FILTER (WHERE x.kind='class')  AS classes_count,
       COUNT(*) FILTER (WHERE x.status='no_show') AS no_show_count,
       COALESCE(s.paid_minor,0) AS lifetime_spend_minor
FROM (
  SELECT user_id, 'lesson' AS kind, starts_at, status FROM coach_bookings
  UNION ALL
  SELECT user_id, 'class'  AS kind, starts_at, status FROM coach_classes
) x
JOIN iam.user u ON u.id = x.user_id
LEFT JOIN spend s ON s.user_id = x.user_id
GROUP BY u.id, u.first_name, u.surname, u.email, u.phone, s.paid_minor
ORDER BY last_seen DESC NULLS LAST
LIMIT :limit;
```
Implement these as **plain-SQL repositories** in `coach/repositories.py` (no SQL views needed for Phase 2a; see
§7 for the views-vs-repos call). `new vs returning` is derivable from `first_seen` within a period.

---

## 6. Business cockpit — the CRM (Phase 2b)

The cockpit answers: *how is my coaching business doing this month?* It is **read-only**: usage + revenue +
trends + schedule, scoped to the coach.

### 6.1 Revenue earned — gross vs net-of-commission (THE calc)

**There is no commission model in the platform today.** To show "revenue earned" honestly we introduce one.
This is a **coordination point with the owner/admin spec** — the owner configures the rate; the coach sees the
result. Recommended minimal model:

- Add **`iam.coach_profile.commission_rate_bps int`** (basis points; e.g. 2000 = 20% to the club) — owned by
  the coach lane's `coach/schema.py` as `ADD COLUMN IF NOT EXISTS ... DEFAULT NULL`, **but written only by the
  owner/admin surface** (a coach cannot set their own rate). NULL → fall back to a club-level default
  `club.policy.coach_commission_rate_bps` (owner spec owns the policy key). Effective rate =
  `COALESCE(coach.commission_rate_bps, club.policy default, 0)`.
- **Gross** = sum of succeeded charge payments on orders for the coach's lessons/classes (what the client paid),
  minus refunds. **Net to coach** = `gross * (1 - rate_bps/10000)`. **Club commission** = `gross - net`.
- Membership-covered / free / complimentary lessons have `order.amount_minor = 0` → contribute 0 gross. Surface
  a separate **"sessions delivered (uncharged)"** count so a coach on a salary/membership club still sees volume.
- Monthly-account (tab) bookings: revenue is **recognized when the order is created/charged on the tab**
  (ledger CHARGE), not when the tab is settled — show as "billed" with a settled/outstanding split if useful.

Definition used by the cockpit (the canonical revenue scope for a coach):

```sql
-- Orders attributable to this coach = orders that have at least one order_line whose
-- booking_id/enrolment_id belongs to a lesson/class this coach ran.
CREATE OR REPLACE VIEW core.vw_coach_order AS  -- (or a repo CTE; see §7)
SELECT DISTINCT o.id AS order_id, o.club_id, o.user_id, o.amount_minor,
       o.settlement_mode, o.status, o.created_at, b.coach_user_id
FROM billing."order" o
JOIN billing.order_line ol ON ol.order_id = o.id
JOIN diary.booking b ON b.id = ol.booking_id
WHERE b.coach_user_id IS NOT NULL
UNION
SELECT DISTINCT o.id, o.club_id, o.user_id, o.amount_minor,
       o.settlement_mode, o.status, o.created_at, cs.coach_user_id
FROM billing."order" o
JOIN billing.order_line ol ON ol.order_id = o.id
JOIN diary.enrolment e ON e.id = ol.enrolment_id
JOIN diary.class_session cs ON cs.id = e.class_session_id;
```

Monthly revenue rollup (net-of-commission applied in the read layer using the effective rate):
```sql
SELECT date_trunc('month', p.created_at)::date AS month,
       SUM(p.amount_minor) FILTER (WHERE p.direction='charge')  AS gross_charge_minor,
       SUM(p.amount_minor) FILTER (WHERE p.direction='refund')  AS refund_minor,
       SUM(p.amount_minor) FILTER (WHERE p.direction='charge')
         - COALESCE(SUM(p.amount_minor) FILTER (WHERE p.direction='refund'),0) AS net_gross_minor
FROM billing.payment p
JOIN core.vw_coach_order co ON co.order_id = p.order_id
WHERE p.club_id = :c AND co.coach_user_id = :u AND p.status = 'succeeded'
GROUP BY 1 ORDER BY 1;
-- coach_earnings_minor = net_gross_minor * (1 - effective_rate_bps/10000.0), computed per row.
```

### 6.2 Usage metrics (monthly)
`{month, lessons_delivered, lesson_hours, classes_delivered, class_hours, distinct_clients, new_clients,
returning_clients, no_shows, fill_rate}`.

- **Lessons delivered / hours:** `COUNT(*)` and `SUM(EXTRACT(EPOCH FROM (ends_at-starts_at))/3600)` over
  `diary.booking WHERE coach_user_id=:u AND status='completed'` per month.
- **Fill rate:** delivered hours ÷ *available* hours. Available = expanded `availability_rule` minus `time_off`
  for the coach's resource over the period (reuse `compute_availability`'s candidate-slot logic or a simpler
  `availability_rule` sum). Phase 2b can ship a coarse fill rate (booked hours ÷ open hours from rules) and
  refine later.
- **New vs returning:** a client is *new* in month M if their `first_seen` (from §5.3) is in M, else returning.
- **No-shows:** `status='no_show'` bookings + `enrolment.status='no_show'`.

```sql
SELECT date_trunc('month', starts_at)::date AS month,
       COUNT(*) FILTER (WHERE status='completed') AS lessons_delivered,
       SUM(EXTRACT(EPOCH FROM (ends_at-starts_at))/3600.0)
         FILTER (WHERE status='completed') AS lesson_hours,
       COUNT(*) FILTER (WHERE status='no_show') AS no_shows,
       COUNT(DISTINCT booked_by_user_id) FILTER (WHERE status IN ('completed','confirmed')) AS distinct_clients
FROM diary.booking
WHERE club_id=:c AND coach_user_id=:u AND booking_type='lesson'
GROUP BY 1 ORDER BY 1;
```

### 6.3 Upcoming schedule & at-a-glance
Reuse `list_bookings(as_coach=True, date_from=today)` + `diary/classes.py list_type_sessions`. The cockpit
header shows: next 7 days count, today's sessions, this-month earnings (net), this-month sessions delivered,
distinct clients this month.

### 6.4 Cockpit read endpoints (`/api/coach/cockpit/*`, thin passthroughs)
| Endpoint | Returns |
|---|---|
| `GET /api/coach/cockpit/summary` | KPI scalars: this-month net earnings, gross, sessions delivered, distinct clients, new clients, no-shows, upcoming-7d count, fill_rate |
| `GET /api/coach/cockpit/revenue?from=&to=&granularity=month` | time-series `[{month, gross_minor, refund_minor, net_gross_minor, coach_earnings_minor, commission_minor, sessions}]` |
| `GET /api/coach/cockpit/usage?from=&to=` | time-series `[{month, lessons_delivered, lesson_hours, classes_delivered, no_shows, new_clients, returning_clients, fill_rate}]` |
| `GET /api/coach/cockpit/clients` | = §5 clients list (the cockpit's "my clients" tab) |
| `GET /api/coach/cockpit/schedule?from=&to=` | upcoming lessons + class sessions (calendar feed) |

All are **admin/coach-gated, club+user scoped, aggregation in SQL, endpoint is a passthrough** (1050 Rule:
SQL views own aggregation, Python is a thin passthrough, frontend is pure rendering).

### 6.5 Where Klaviyo / `core.usage_event` helps (Phase 2c, optional)
The diary/billing lanes already `emit()` events (`booking_confirmed`, `lesson_completed`, `booking_cancelled`)
into `core.usage_event` via `marketing_crm`. For the **core cockpit numbers, prefer billing+diary as the
system of record** (money and sessions are authoritative there; 1050 made the same call — cockpit reads
billing SoR, LEFT JOINs core for sparse extras). Use `core.usage_event` only for engagement extras a coach
might like (e.g. "client viewed your profile", "client opened booking") and for feeding Klaviyo lifecycle
(e.g. a coach monthly digest email). Do **not** compute revenue from events.

---

## 7. One CRM for both (coach + owner)? — recommendation

**Recommendation: ONE shared CRM data/aggregation layer, two scoped views over it.** Build the cockpit
aggregations as `core.vw_coach_*` SQL views (or shared repo query-builders) parameterized by scope, where:
- The **coach cockpit** passes `coach_user_id = principal.user_id` (their slice).
- The **owner/admin cockpit** passes no coach filter (whole club) and can group-by coach.

Rationale: the source of truth is identical (`billing.*` payments/orders + `diary.booking/class_session/enrolment`
+ `iam`/`core`). Maintaining two aggregation engines guarantees drift (the owner's "club revenue" must equal
the sum of coaches' gross). 1050 explicitly chose a single cockpit data layer reading the live SoR and
LEFT-JOINing `core.*` for extras — we mirror that. The **commission split is the one place they diverge in
meaning** (coach sees net-to-coach; owner sees the commission as club income) — but it's the *same numbers*
viewed from two sides, computed once.

Implementation choice — **SQL views vs repo CTEs:** Phase 2 can start as **plain-SQL repositories** in
`coach/repositories.py` (consistent with the lane's current style, no cross-lane DDL, easy to scope). If/when
the owner cockpit lands, **promote the shared aggregations to `core.vw_coach_*` views** (CRM/`core` lane owns
`core.*` DDL) so both surfaces read one definition. Recommend: ship Phase 2a as repos, introduce the shared
views with the owner cockpit. Keep `core.vw_coach_order` (the coach↔order attribution) as the first shared view
since both surfaces need it.

---

## 8. UX (reuse `cf-*` design system)

Reuse `frontend/app/app.css` `cf-*` classes only (no inline component styles). The coach console
(`coach.html` + `coach.js`) gains a **cockpit** as the landing tab.

Layout (cockpit landing):
- **KPI strip** (`cf-card` row of stat tiles): This month — Net earned · Sessions delivered · Clients ·
  No-shows · Upcoming (7d) · Fill rate. (Reuse the admin stat-tile pattern from `admin.js`.)
- **Revenue trend** chart (`cf-*` + the calendar/chart lib already in use) — gross vs net, monthly.
- **Usage trend** — sessions delivered + new/returning clients.
- **My clients** table (search, sortable; row → client 360 drawer).
- **Upcoming schedule** list (reuse "My week" component).

Console tab structure: **Cockpit · My week · My classes · My profile (Profile/Hours/Services) · Time off.**
Profile editor extends the existing tabbed `CoachUI` builders with the new fields (languages, qualifications,
years_experience, visibility/bookable toggles). Per-section save (existing pattern).

Empty states: a brand-new coach sees "No sessions yet — once clients book you, your cockpit fills in." Money
hidden / shown as "—" for commission-not-configured until the owner sets a rate (don't show misleading net).

---

## 9. Build phasing (ordered)

**Phase 1 — Self-service edit (small, high value, low risk).**
1. Profile field additions (`languages`, `qualifications`, `years_experience`, `public_visibility`) + editor.
2. Surface `is_bookable` toggle in the editor; `GET`/`DELETE` time-off endpoints + UI.
3. **Fix services to per-duration `per_booking`** (align with `diary/pricing.py`).
4. Wire **reschedule** + **cancel-with-reason** + per-party attendance into the console (APIs exist).

**Phase 2a — Clients view (CRM foundation).**
5. `GET /api/coach/clients` + `GET /api/coach/clients/<id>` (repo CTEs, §5). Clients table + 360 drawer.

**Phase 2b — Business cockpit (the CRM).**
6. **Commission model** (coordinate with owner spec): `coach_profile.commission_rate_bps` +
   `club.policy.coach_commission_rate_bps` default; owner-only writes.
7. `core.vw_coach_order` attribution + cockpit endpoints (`summary`, `revenue`, `usage`, `schedule`).
8. Cockpit UI (KPI strip, revenue/usage trends, schedule).

**Phase 2c — Engagement extras + lifecycle (optional).**
9. Surface selected `core.usage_event` extras; Klaviyo coach monthly digest.

**Phase 3 — (future) coach↔client messaging, payouts/settlement banking, public coach directory pages.**

---

## 10. REUSE-vs-NEW map

| Need | REUSE | NEW |
|---|---|---|
| Profile/hours/services CRUD | `coach/routes.py` + `coach/repositories.py` (extend) | +4 profile columns; per-duration services fix; time-off GET/DELETE |
| Lessons mgmt | `diary/bookings.py` (`list_bookings`,`reschedule`,`cancel`,`set_status`,`set_attendance`) | console wiring only |
| Classes mgmt | `diary/classes.py` (`roster`,`mark_attendance`,`schedule_sessions`,`cancel_session`) | — |
| Clients derivation | `diary.booking.booked_by_user_id`, `diary.enrolment`, `billing.payment/order` | `coach/repositories.py` client CTEs; 2 endpoints |
| Revenue calc | `billing.payment`/`order`/`order_line`, `admin.list_payments` pattern | `vw_coach_order`, commission columns, net calc, cockpit endpoints |
| Usage/trends | `diary.booking`/`class_session`/`availability_rule`/`time_off` | rollup queries; fill-rate calc |
| Cockpit pattern (views factory, thin endpoints, customer-360) | 1050 `marketing_crm/backoffice/views.py` + `blueprint.py` (study; adapt patterns) | `core.vw_coach_*` (or repos), `/api/coach/cockpit/*` |
| Event feed | `marketing_crm` `emit()` → `core.usage_event`; Klaviyo sync | coach digest (Phase 2c) |
| UI | `frontend/app/app.css` `cf-*`, `coach.js`, `coach_api.js`, `class_ui.js`, admin stat tiles/charts | cockpit tab + components |

> Note on 1050: its CRM is **player/account-centric** (matches uploaded, subscriptions, credits) — the
> *patterns* (views-own-aggregation, admin-gated thin endpoints, customer-360 drawer, monthly rollups, lifecycle
> stages) port cleanly; the *nouns* (matches/credits/coach-link-gating) do not. Reuse the architecture, not the
> schema.

---

## 11. Open questions

1. **Commission ownership & granularity.** Per-coach rate, club default, or both (recommended: both, coach
   overrides default)? Different rate for lessons vs classes? Who can edit (owner only — assumed)? **Must be
   settled in the owner/admin spec before Phase 2b.**
2. **Revenue recognition timing for `monthly_account`/`at_court`.** Recognize at order creation (billed) or at
   payment (settled)? Recommend showing **billed** (matches when the session happened) with a settled badge.
3. **Membership-covered & complimentary lessons.** They yield R0 gross — does the coach earn anything (a
   club-paid per-session rate)? If clubs pay coaches for membership lessons, we need a **per-session coach pay
   rate** distinct from the client price. Out of scope here; flag for owner spec.
4. **Services model:** one lesson product with multiple duration rates, vs one product per duration (current
   `list_services` flattens per-price). Recommend one product, many rates (matches "Private lesson 30/60").
5. **Fill-rate denominator:** open hours from `availability_rule` (simple) vs true bookable slots from
   `compute_availability` (accurate, heavier). Ship simple first.
6. **Cross-coach clients / shared courts:** if a court booking has multiple parties, attribution to a coach is
   only via lessons/classes (correct). Confirm no court-only bookings should appear in a coach's clients.
7. **Public visibility vs marketing directory:** does `public_visibility` drive the `frontend/marketing/`
   coach directory now, or later? (Coordinate with Marketing/SEO lane.)
8. **Views vs repos:** ship Phase 2a as repos, promote to `core.vw_coach_*` with the owner cockpit (recommended)
   — confirm the `core`/CRM lane owns those views.

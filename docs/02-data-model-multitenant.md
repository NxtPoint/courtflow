# 02 — Multi-Tenant Data Model

> Postgres, single database, multiple schemas (1050 style). **Idempotent boot‑time DDL**, no migration
> framework: each module exposes `init()` that runs `CREATE TABLE IF NOT EXISTS` + `ADD COLUMN IF NOT
> EXISTS`, called on app boot. UUID PKs (`gen_random_uuid()`), `created_at/updated_at` everywhere.

## 1. Tenancy strategy

**Shared schema, shared tables, `club_id` discriminator** (the standard B2B‑SaaS pattern, and the
fastest path to "cookie‑cutter"). Every domain row carries `club_id UUID NOT NULL REFERENCES club.club(id)`.

Isolation enforced at **three** layers (defence in depth):

1. **Application** — a single `with_club(club_id)` query helper; every repository method takes
   `club_id` and includes it in the `WHERE`. Code review rule: *no domain query without `club_id`.*
2. **Auth** — the resolved principal (from Clerk JWT) yields the caller's `club_id` + role
   server‑side; the client can never assert a different `club_id`. (Mirrors 1050's "email derived
   server‑side, spoofed `?email` ignored".)
3. **Database (recommended) — Postgres Row‑Level Security.** Enable RLS on `diary.*`/`billing.*` with a
   policy keyed on `current_setting('app.club_id')`, set per request/transaction. Belt‑and‑braces so a
   missed `WHERE` can't leak across clubs. *(Can be deferred to Phase 2 if it slows MVP — but design
   tables so it's a drop‑in.)*

Platform‑admin (us) can cross clubs via an explicit elevated path (`OPS_KEY` / `platform_admin` role)
that sets `app.club_id` to a wildcard — never the default.

## 2. `club.*` — tenants & config

```sql
club.club
  id uuid pk, slug text unique,            -- 'nextpoint'
  name text, legal_name text,
  status text check (status in ('active','trialing','suspended')) default 'active',
  currency_code text not null default 'ZAR',
  timezone text not null default 'Africa/Johannesburg',
  locale text default 'en-ZA',
  created_at, updated_at

club.location                              -- a club can have >1 venue
  id uuid pk, club_id fk,
  name text, address_line text, city text, postal_code text, country text,
  lat numeric, lng numeric, phone text, email text

club.branding                              -- white-label
  club_id fk pk,
  primary_color text, accent_color text, logo_url text, favicon_url text,
  domain text,                             -- 'nextpointtennis.com'
  marketing_hosts text[],                  -- host-switch (mirrors 1050 MARKETING_HOSTS)
  og_image_url text,
  klaviyo_list_id text                     -- per-club Klaviyo segmentation (optional)

club.policy                                -- booking & cancellation rules (see doc 03)
  club_id fk pk,
  booking_window_days int default 14,      -- how far ahead members can book
  min_booking_minutes int default 60,
  cancellation_cutoff_hours int default 12,-- free cancel up to N hrs before
  no_show_fee_minor numeric default 0,
  guest_requires_member bool default true,
  allow_pay_at_court bool default true,
  allow_monthly_account bool default true,
  allow_online_payment bool default false  -- flip on when Yoco goes live
```

NextPoint seed: `slug='nextpoint'`, ZAR, `Africa/Johannesburg`, 9 courts (see §4), the Wix policies.

## 3. `iam.*` — identity, membership, roles

> Identity (login) lives in **Clerk**; `iam.*` maps Clerk users to clubs + roles + profile.

```sql
iam.user                                   -- 1 per human (global, cross-club)
  id uuid pk,
  clerk_user_id text unique,               -- from the verified JWT 'sub'
  email text, first_name text, surname text, phone text,
  created_at, updated_at

iam.membership                             -- user's relationship to a club (n per user)
  id uuid pk, club_id fk, user_id fk,
  role text check (role in
    ('platform_admin','club_admin','coach','member','guest')) not null,
  member_status text check (member_status in
    ('active','lapsed','prospect','none')) default 'none',
  joined_at, updated_at,
  unique (club_id, user_id, role)

iam.coach_profile                          -- richer data for coaches (powers "book a named coach")
  id uuid pk, club_id fk, user_id fk unique,
  display_name text, headline text, bio text, photo_url text,
  is_bookable bool default true,
  rank int default 0,                      -- display order on coach page
  -- carried from NextPoint: Neville Godwin (Program Director), Ross Nemeth (Head Coach)
  specialties text[],                      -- e.g. {'high_performance','junior','cardio'}
  default_lesson_price_id uuid             -- fk billing.price (nullable)

iam.player_profile                         -- optional player detail (juniors, UTR, hand)
  id uuid pk, club_id fk, user_id fk,
  dob date, skill_level text, dominant_hand text, utr numeric,
  guardian_user_id uuid,                   -- for minors → links to parent
  notes text
```

> **Minors:** NextPoint coaches many juniors. `player_profile.dob` + `guardian_user_id` drive a
> **parental‑consent gate** at signup (reuse 1050's consent module). No minor PII into Klaviyo — the
> contact is always the adult guardian. See `06-crm-and-klaviyo.md`.

## 4. `diary.*` — resources, availability, the diary

> The detailed *behaviour* (lifecycle, conflicts, recurrence) is in `03-diary-booking-engine.md`. This
> is the storage shape.

```sql
diary.resource                             -- anything bookable that has a calendar
  id uuid pk, club_id fk, location_id fk,
  kind text check (kind in ('court','coach','class')) not null,
  name text,                               -- 'Court 1', 'Clay Court', 'Cardio Tennis'
  surface text,                            -- 'hard' | 'clay' (courts)
  coach_user_id uuid,                      -- when kind='coach' (→ iam.coach_profile)
  capacity int default 1,                  -- courts=1; classes=N
  is_active bool default true,
  rank int default 0
-- NextPoint seed: 8 courts (surface='hard') + 1 (surface='clay') + coach resources for each
--   bookable coach + class resources (Cardio Tennis, Junior Beginner, Junior Intermediate, Socials).

diary.availability_rule                    -- recurring open hours per resource
  id uuid pk, club_id fk, resource_id fk,
  weekday int,                             -- 0=Mon..6=Sun
  start_time time, end_time time,
  slot_minutes int default 60,
  valid_from date, valid_to date           -- seasonal overrides

diary.time_off                             -- one-off blocks (holiday, maintenance, coach leave)
  id uuid pk, club_id fk, resource_id fk,
  starts_at timestamptz, ends_at timestamptz, reason text

diary.booking                              -- THE core row (a court hold OR a lesson)
  id uuid pk, club_id fk,
  booking_type text check (booking_type in ('court','lesson','class')) not null,
  resource_id fk,                          -- the court OR coach OR class resource
  coach_user_id uuid,                      -- denormalised for lesson queries
  starts_at timestamptz not null, ends_at timestamptz not null,
  status text check (status in
    ('held','confirmed','cancelled','completed','no_show')) not null default 'confirmed',
  booked_by_user_id uuid,                  -- who made it (member/admin/coach)
  recurrence_id uuid,                      -- groups a recurring series (nullable)
  order_id uuid,                           -- → billing.order (settlement)
  cancellation_reason text, cancelled_at timestamptz, cancelled_by uuid,
  notes text,
  created_at, updated_at,
  -- conflict guard: see doc 03 (exclusion constraint on (resource_id, tstzrange))
  exclude using gist (resource_id with =, tstzrange(starts_at, ends_at) with &&)
        where (status in ('held','confirmed'))

diary.booking_party                        -- participants on a booking (members/guests)
  id uuid pk, booking_id fk, club_id fk,
  user_id uuid,                            -- nullable for ad-hoc guests
  party_role text check (party_role in ('host','partner','guest','player')) default 'player',
  guest_name text, guest_email text,       -- when no account
  price_id uuid,                           -- which price applied to THIS party (member/visitor/guest)
  attended bool

diary.class_session                        -- a scheduled instance of a class (e.g. Cardio, Tue 6pm)
  id uuid pk, club_id fk, resource_id fk,  -- resource.kind='class'
  coach_user_id uuid, starts_at, ends_at,
  capacity int, price_id uuid,
  status text default 'scheduled'

diary.enrolment                            -- a player joining a class_session
  id uuid pk, club_id fk, class_session_id fk, user_id uuid,
  status text check (status in ('enrolled','waitlisted','cancelled','attended','no_show')),
  order_id uuid, enrolled_at, unique (class_session_id, user_id)

diary.waitlist                             -- generic waitlist (court time freeing up, full class)
  id uuid pk, club_id fk, resource_id fk, class_session_id uuid,
  user_id uuid, desired_start timestamptz, created_at, notified_at

diary.recurrence                           -- series definition (coach's weekly squad, member's regular slot)
  id uuid pk, club_id fk,
  rrule text,                              -- iCal RRULE string
  until date, created_by uuid
```

## 5. `billing.*` — products, prices, orders, settlement

> Keeps 1050's **grant/consume idempotency discipline**; nouns change to bookings/memberships.
> The payment **provider abstraction** is in `05-payments-abstraction.md`.

```sql
billing.product                            -- a sellable thing
  id uuid pk, club_id fk,
  kind text check (kind in
    ('court_booking','lesson','class','membership','guest_booking')) not null,
  name text, description text, active bool default true

billing.price                              -- price points (member/visitor/guest tiers etc.)
  id uuid pk, club_id fk, product_id fk,
  audience text check (audience in ('member','visitor','guest','any')) default 'any',
  amount_minor int not null,               -- cents/ZAR cents
  currency_code text not null,             -- = club currency
  unit text check (unit in ('per_booking','per_hour','per_session','per_month')) default 'per_booking',
  duration_minutes int,                    -- for per_session/lessons
  active bool default true
-- NextPoint seed (ZAR): hard-court member (membership R220/mo), hard-court visitor (R150),
--   member-guest (R80), clay court (premium), junior beginner (R120/30min),
--   junior intermediate (R150), Cardio Tennis (TBD), lesson prices per coach.

billing.membership_subscription            -- recurring memberships (R220/mo "unlimited courts")
  id uuid pk, club_id fk, user_id uuid,
  price_id fk, status text check (status in ('active','cancelled','expired')) default 'active',
  provider text,                           -- 'yoco'|'paypal'|'manual'|null(at-desk)
  provider_subscription_id text,
  current_period_end date, created_at, updated_at

billing.order                              -- one settlement unit (a booking, a class, a tab line)
  id uuid pk, club_id fk, user_id uuid,
  amount_minor int, currency_code text,
  settlement_mode text check (settlement_mode in
    ('online','at_court','monthly_account','membership_covered','free')) not null,
  status text check (status in
    ('open','awaiting_payment','paid','void','refunded','written_off')) not null default 'open',
  due_date date,                           -- for monthly_account
  created_at, updated_at

billing.order_line
  id uuid pk, order_id fk, club_id fk,
  description text, price_id uuid, qty int default 1, amount_minor int,
  booking_id uuid, enrolment_id uuid       -- what this line is for

billing.payment                            -- record-only money log (mirrors 1050 billing.payment)
  id uuid pk, club_id fk, order_id fk,
  provider text,                           -- 'yoco'|'paypal'|'cash'|'card_at_desk'|'eft'
  provider_payment_id text,
  amount_minor int, currency_code text,
  direction text check (direction in ('charge','refund')) default 'charge',
  status text check (status in ('pending','succeeded','failed','refunded')),
  created_at,
  unique (provider, provider_payment_id)   -- idempotency (1050 pattern)

billing.payment_attempt                    -- gateway round-trips (audit, idempotency, webhooks)
  id uuid pk, club_id fk, order_id fk,
  provider text, intent_id text,           -- gateway checkout/charge id
  status text, raw_event jsonb,
  event_hash text unique,                  -- sha256 dedup (1050 subscription_event_log pattern)
  created_at

billing.account_ledger                     -- the "pay end of month" tab
  id uuid pk, club_id fk, user_id uuid,
  order_id uuid, entry_type text check (entry_type in ('charge','payment','adjustment')),
  amount_minor int, balance_after_minor int, note text, created_at
```

## 6. `core.*` — own‑CRM (ported from 1050)

Port `core.account / core.user / core.person / core.usage_event / core.consent / core.nps` largely
as‑is from `core_db/`, adding `club_id` where relevant. `core.usage_event` is the canonical event
stream that `crm_sync` forwards to Klaviyo (see `06`). This is "we are our own CRM".

## 7. Seed data for NextPoint (club #1)

A seed script (`seed_nextpoint.py`, idempotent) creates:
- `club.club` (nextpoint, ZAR, JHB tz) + `club.location` (Killarney Country Club) + `club.branding`
  (`domain='nextpointtennis.com'`, logo, colours from current site) + `club.policy` (Wix rules).
- `diary.resource`: Courts 1–8 (hard) + Clay Court; coach resources for each bookable coach
  (Neville Godwin, Ross Nemeth, +team); class resources (Cardio Tennis, Junior Beginner, Junior
  Intermediate, Saturday/Wednesday Social).
- `iam.coach_profile` for Neville & Ross (bios from the site).
- `billing.product` + `billing.price` for every tier above (ZAR, member/visitor/guest audiences).
- `diary.availability_rule` for court open hours + each coach's bookable hours (placeholder, admin
  edits later).

## 8. Indexing & integrity notes

- `diary.booking`: GiST **exclusion constraint** prevents double‑booking a resource for overlapping
  confirmed/held times (Postgres `btree_gist` + `tstzrange`). This is the single most important
  integrity guarantee — detailed in `03`.
- B‑tree indexes: `booking (club_id, resource_id, starts_at)`, `booking (club_id, coach_user_id,
  starts_at)`, `booking (club_id, booked_by_user_id, starts_at)`, `enrolment (class_session_id)`.
- All FKs `ON DELETE` chosen conservatively; **never hard‑delete bookings/payments** — use status
  (`cancelled`/`void`) — inheriting 1050's soft‑delete/audit discipline.
- Enable extensions on boot: `pgcrypto` (uuid), `btree_gist` (exclusion constraint).

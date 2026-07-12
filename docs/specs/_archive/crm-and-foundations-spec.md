# Spec: CRM/Cockpit strategy + cross-cutting foundations

**Status:** Implementation-ready · **Owner of the SHARED layer** the client / coach / owner role specs
reference. Those specs are written in parallel; this one owns the primitives they all depend on
(dependents, usage/spend aggregation, refund-request, notifications) and the single CRM/analytics engine.

**Scope:** answers the owner's question — *"do we use one CRM for both [coach & owner]? Perhaps the CRM
view should be a Phase 2 where we define it for both entities"* — and defines the data primitives so the
three role specs don't each invent their own.

**Read first:** `CLAUDE.md`, `docs/05` (payments), `docs/06` (CRM/Klaviyo), `contracts/events.md`,
`core/schema.py`, `billing/schema.py`, `iam/schema.py`, `diary/schema.py`.

---

## 0. TL;DR — the recommendation

1. **ONE CRM/analytics engine, role-scoped views.** Do **not** build a separate coach CRM and owner CRM.
   Build a single cockpit data layer over `core.usage_event` + `billing.*` + `diary.*`, expose it through
   one set of SQL views/helpers, and **filter every read by the caller's `Principal`**: a coach sees only
   rows tied to their `coach_user_id`; an owner sees their whole `club_id`; the platform admin sees all
   clubs. Same engine, three lenses. (Mirrors how 1050 ran a single backoffice over `billing.* + core.*`
   and gated by role — but our multi-tenant `club_id` + `Principal.role` make the scoping a first-class
   query predicate instead of an `ADMIN_EMAILS` hardcode.)

2. **The cockpit *UI* is Phase 2; its *foundations* are Phase 1.** The owner is right that the rich CRM
   *view* can land later. But three primitives the role specs depend on must land **first** because the
   client/coach/owner feature work cannot ship without them: **dependents/children**, **usage & spend
   aggregation helpers**, and the **refund-request workflow**. Those are prerequisites; the cockpit dashboards
   that consume them are Phase 2.

3. **Notifications stay event-driven.** Everything already flows through `emit()` → `core.usage_event` →
   Klaviyo (transactional vs marketing gate) with an SES fallback for `booking_confirmed`. We standardise
   the event→notification map and add an optional in-app **notification log** — we do **not** introduce a
   second notification system.

---

## 1. What already exists (build on this, do not reinvent)

| Layer | What's there | File |
|---|---|---|
| **Event stream** | `emit(event, payload)` — fire-and-forget, writes `core.usage_event` (SoR), best-effort Klaviyo forward gated transactional/marketing; SES fallback for `booking_confirmed` | `marketing_crm/tracking/`, `contracts/events.md` |
| **Event store** | `core.usage_event(club_id, account_id, user_id, person_id, event_type, ref_type, ref_id, metadata, occurred_at)` | `core/schema.py` |
| **CRM identity** | `core.account` (BIGINT), `core.app_user`, `core.person` (role player\|parent\|coach, `is_minor`), `core.relationship` (`parent_junior`/`coach_player`), `core.consent`, `core.nps_response`, `core.acquisition`, `core.data_subject_request` | `core/schema.py`, `core/repositories/` |
| **Auth actor** | `iam.user` (**UUID** — the canonical actor on diary/billing rows), `iam.membership` (role + member_status, per club), `iam.coach_profile (UNIQUE user_id)`, `iam.player_profile (guardian_user_id)` | `iam/schema.py` |
| **Spend/revenue SoR** | `billing.order` (`user_id`, `amount_minor`, `settlement_mode`, `status`), `billing.order_line` (`booking_id`/`enrolment_id`/`price_id`), `billing.payment` (`direction charge\|refund`, idempotent), `billing.account_ledger` (running balance), `billing.membership_subscription` | `billing/schema.py`, `orders.py`, `ledger.py` |
| **Activity SoR** | `diary.booking` (`booking_type`, `resource_id`, `coach_user_id`, `booked_by_user_id`, `order_id`, `status`), `diary.booking_party` (`user_id`/guest, `party_role`, `attended`), `diary.class_session`, `diary.enrolment` | `diary/schema.py` |
| **Principal** | `Principal(user_id, club_id, role, email, method, memberships)`; `is_platform_admin`; JWT or OPS | `auth/principal.py`, `iam/permissions.py` |
| **Cockpit skeleton** | `GET /api/admin/cockpit/{signups,usage,consent,nps}` LIVE over `core.*`; `{occupancy,revenue,coach-utilization,attendance}` stubbed (501) pending these aggregation views | `marketing_crm/backoffice/blueprint.py` |

**The one architectural seam to understand.** There are two identity spines:
- `iam.user` (**UUID**) is the actor on every `diary.*` and `billing.*` row (`booked_by_user_id`,
  `booking_party.user_id`, `order.user_id`).
- `core.*` (BIGINT `account`/`person`) is the CRM/compliance spine, linked to events **by email**
  best-effort.

The aggregation layer therefore keys on **`iam.user.id` + `club_id`** (the money/activity spine), and joins
to `core.*` only for marketing/consent/NPS context. The CRM cockpit reads the **billing/diary** spine for
spend and revenue (never stale), and `core.*` for engagement/NPS/consent — exactly the 1050 "Option C: read
live SoR, core is sparse" pattern.

---

## 2. CRM architecture — ONE engine, role-scoped views

### 2.1 Recommendation & justification

**ONE shared cockpit data layer. Reject separate coach/owner engines.** Justification:

- **Same source tables.** A coach's "my revenue this month" and an owner's "club revenue this month" are the
  *same* `billing.order/payment` aggregation with a different `WHERE`. Two engines would duplicate every
  rollup and drift.
- **Multi-tenant already forces a scoping predicate.** Every domain row carries `club_id` (decision D7). Role
  scoping is one more predicate (`coach_user_id = :me`) on top of the tenant predicate we already apply
  everywhere — not a new subsystem.
- **1050 proved the single-engine shape** (one backoffice over `billing.* + core.*`, gated by role). The only
  thing we change is replacing 1050's `member.role != 'coach'` / `ADMIN_EMAILS` checks with our
  `Principal.role` + `club_id` (decision D6: role comes from `iam.membership`, never a hardcoded list).
- **Platform view falls out for free.** Platform admin = the same queries with the `club_id` predicate lifted
  (cross-club), used for the SaaS operator console.

**The owner's "Phase 2" instinct is correct for the *cockpit UI*, wrong for the *foundations*.** Split:
- **Phase 1 (now):** the shared aggregation **helpers/views** + dependents + refund-request — because the
  client/coach/owner *feature* specs call them.
- **Phase 2:** the cockpit **dashboards** (coach console analytics, owner business-health, platform operator
  view) that render those helpers. One engine, defined once for both entities, surfaced when each role's UI
  is built.

### 2.2 Data-flow diagram

```
  PRODUCERS                AGGREGATION (SQL, idempotent CREATE OR REPLACE VIEW)        ROLE-SCOPED READ
  ─────────                ────────────────────────────────────────────────────       ────────────────
  diary.booking ─┐
  diary.enrolment├──┐      crm.vw_booking_activity   (one row / booking, +coach,       coach  → WHERE coach_user_id = me
  billing.order ─┤  ├────► +party, +amount, +settlement, +status, +month)              owner  → WHERE club_id = my_club
  billing.payment┘  │      crm.vw_spend_monthly       (per user_id × month)             platform→ (club predicate lifted)
                    │      crm.vw_revenue_monthly      (per club × [coach] × month)          │
  emit()→           ├────► crm.vw_coach_revenue        (per coach × month)                   ▼
  core.usage_event ─┘      crm.vw_member_360           (per user_id: spend, last seen,   scope_clause(principal)
                           bookings, membership, NPS)                                   applied by the route layer
  core.nps_response ─────► crm.vw_nps_summary / _monthly                                       │
  core.consent ─────────► (cockpit /consent already live)                                     ▼
                                                                                       Cockpit JSON  +  Klaviyo traits
```

Two consumers of the same aggregation: **the cockpit** (role-scoped JSON) and **Klaviyo** (per-club + per-role
segment traits). NPS/feedback and consent already flow; we add the spend/revenue/activity rollups the four
stubbed cockpit endpoints need.

### 2.3 The scoping / authorization model (THE shared pattern)

Every cockpit/analytics read passes the caller's `Principal` through **one** scope helper that returns a SQL
predicate. Role specs MUST use this rather than rolling their own filter.

```python
# marketing_crm/scoping.py  (NEW — the single source of the role-scope predicate)

def scope_clause(principal, *, coach_col="coach_user_id", club_col="club_id",
                 user_col=None) -> tuple[str, dict]:
    """Return (sql_fragment, params) the caller ANDs into a WHERE over an aggregation view.

    platform_admin : "TRUE"                              (all clubs; OPS console)
    club_admin     : "{club} = :club_id"                 (their whole club)
    coach          : "{club} = :club_id AND {coach} = :uid"   (only their lessons/clients/revenue)
    member/guest   : "{club} = :club_id AND {user} = :uid"    (only their own rows; requires user_col)
    """
    role = principal.role
    if principal.is_platform_admin:
        return "TRUE", {}
    p = {"club_id": principal.club_id}
    base = f"{club_col} = :club_id"
    if role == "club_admin":
        return base, p
    if role == "coach":
        p["uid"] = principal.user_id
        return f"{base} AND {coach_col} = :uid", p
    # member / guest — self only
    if user_col is None:
        raise PermissionError("member scope needs a user column")
    p["uid"] = principal.user_id
    return f"{base} AND {user_col} = :uid", p
```

Rules:
- **Tenant predicate is never optional** (except platform_admin) — `club_id` always present (D7).
- **Coach scoping is `coach_user_id`** = the coach's `iam.user.id`, which is denormalised onto
  `diary.booking.coach_user_id` and `diary.resource.coach_user_id` (already there) and must be carried into
  `billing.order_line`/the revenue view (see §4). A coach NEVER sees another coach's revenue or a court-only
  booking they weren't the coach for.
- **Member scoping is `user_col` = the booking party / order `user_id`** — a member sees only their own
  spend/bookings (and their dependents' — see §3.4).
- **Enforced at the route layer**, not in the view. Views are role-agnostic; the route ANDs `scope_clause`.
  This keeps one view definition serving all three roles (no per-role view duplication).
- **`can(principal, "view_club_analytics")`** (already `club_admin`+) still gates owner/platform cockpit
  routes; coach console routes gate on `role == coach` and force the coach predicate.

---

## 3. Shared primitive: Dependents / children

### 3.1 Decision — extend `iam`, do not invent a parallel model

A dependent (a child booked by a guardian) must be **bookable** — it has to flow into a `diary.booking_party`
and onto rosters. The booking spine keys on `iam.user.id`. Therefore a dependent is **a first-class
`iam.user` with no login**, linked to a guardian. We already have `iam.player_profile.guardian_user_id` and
`core.relationship(type='parent_junior')`; we formalise the dependent as a **non-login `iam.user`** and add a
thin `iam.dependent` link for the guardian relationship + management metadata. This:
- lets a dependent be a `booking_party.user_id` / `enrolment.user_id` with **zero changes** to diary/billing;
- keeps minor PII out of events (the **adult guardian** remains the `email`/contact on every `emit`, per the
  events contract §0 rule);
- mirrors 1050's `core.relationship(parent_junior)` + `person.is_minor`, but on our UUID actor spine.

**Rejected alternative:** a child as only a `core.person` row — it could not be a `booking_party.user_id`
(wrong id type/spine) without a lookup table on every booking; more moving parts, more drift.

### 3.2 DDL — `iam.dependent` (NEW)

```sql
-- iam.dependent : a guardian-managed minor/dependent who can be booked FOR but does not log in.
-- The dependent IS an iam.user (no clerk_user_id) so it can be a booking_party.user_id /
-- enrolment.user_id with no change to diary/billing. This table carries the guardianship +
-- management metadata. Adults can also be dependents (e.g. a spouse on one account) — minor=false.
CREATE TABLE IF NOT EXISTS iam.dependent (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    club_id          uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
    guardian_user_id uuid NOT NULL REFERENCES iam.user(id) ON DELETE CASCADE,   -- the adult/payer
    dependent_user_id uuid NOT NULL REFERENCES iam.user(id) ON DELETE CASCADE,  -- the non-login user
    relationship     text DEFAULT 'child' CHECK (relationship IN
                         ('child','spouse','partner','other')),
    is_minor         boolean DEFAULT true,    -- drives parental-consent + PII rules
    can_self_book    boolean DEFAULT false,   -- reserved (a teen given limited self-service later)
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (guardian_user_id, dependent_user_id)
);
CREATE INDEX IF NOT EXISTS ix_dependent_guardian ON iam.dependent (club_id, guardian_user_id);
CREATE INDEX IF NOT EXISTS ix_dependent_dependent ON iam.dependent (dependent_user_id);
```

The dependent's `iam.user` row holds `first_name`/`surname` (and `iam.player_profile` holds `dob`,
`skill_level`, etc., with `guardian_user_id` set). `clerk_user_id` stays NULL → never authenticates.
**One guardian per dependent for billing** (the payer); co-guardians are a future `parent_junior`
`core.relationship` if needed.

### 3.3 How a dependent flows into a booking

No diary change is required. The booking party simply references the dependent's user id:

```python
# When a guardian books "for" a child:
#   booked_by_user_id = guardian.id          (who made the booking — auth actor)
#   booking_party.user_id = dependent.id     (who it is FOR — the player)
#   booking_party.party_role = 'player'
#   order.user_id = guardian.id              (the PAYER is the guardian, so spend rolls up to them)
```

Critical rule for aggregation: **spend rolls up to the PAYER (`order.user_id` = guardian)**, while **activity
(attendance, lessons, class rosters) rolls up to the PLAYER (`booking_party.user_id` = dependent)**. This is
exactly what the client cockpit needs ("my spend" includes my kids' lessons; "my kids' upcoming sessions" is
the activity view). §4 codifies both.

### 3.4 Authorization additions

- A guardian may read/act on a dependent's bookings: extend `iam.permissions.can` ownership checks so
  `booked_by_user_id == principal.user_id` **OR** the target `user_id ∈ dependents_of(principal.user_id)`
  passes member-scoped reads.
- Coach rosters: a coach seeing a class roster sees the **dependent's display name** (legitimate — they coach
  the child) but the **contact is the guardian** (no child email/phone surfaced). The minor-PII rule applies
  only to **outbound events/Klaviyo**, not to the in-club roster a coach needs.
- `helper dependents_of(session, club_id, guardian_user_id) -> list[uuid]` (NEW, `iam/repositories.py`) — used
  by client cockpit member scoping and by the booking "book for" picker.

### 3.5 Endpoints (client spec consumes; owned here as the shared shape)

```
GET    /api/me/dependents                       list my dependents (+ player_profile summary)
POST   /api/me/dependents                       {first_name, surname, dob?, relationship, ...}
                                                 -> creates non-login iam.user + iam.dependent
                                                    + iam.player_profile(guardian_user_id=me)
PATCH  /api/me/dependents/<dependent_user_id>    edit profile
DELETE /api/me/dependents/<dependent_user_id>    soft-remove (block if future bookings exist)
```
On create, **emit `dependent_added`** (marketing; payload carries `club_id` + guardian `email` only — never
the child's PII, per the contract).

---

## 4. Shared primitive: Usage & spend aggregation

### 4.1 Source of truth

- **Spend (what a user/guardian paid)** and **revenue (what the club/coach earned)** = `billing.order` +
  `billing.payment` + `billing.account_ledger`. **Money never aggregates from events** — events are for
  engagement/lifecycle, billing is the financial SoR (the 1050 lesson: cockpit reads live billing, never a
  stale cache).
- **Usage / activity (bookings, lessons, classes, attendance)** = `diary.booking` + `diary.booking_party` +
  `diary.enrolment`.
- **Engagement (logins, page views, NPS)** = `core.usage_event` + `core.nps_response`.

Recognised-revenue convention (state once, reuse): **a `billing.order` with `status='paid'` counts as revenue
in the month of its settling `billing.payment` (direction `charge`), net of any `refund` payment.** Orders
that are `membership_covered`/`free` contribute **R0 revenue but count as activity**. This matches
`apply_payment_event` already flipping orders to `paid` on `charge_succeeded`.

### 4.2 Carry `coach_user_id` onto the revenue path (the one schema gap)

Today `coach_user_id` is on `diary.booking` but **not** on `billing.order`/`order_line`. The coach-revenue
view needs it without a fragile join through diary. **Add a nullable, denormalised `coach_user_id` to
`billing.order_line`** (cross-lane unconstrained UUID, same pattern as the existing `booking_id`), populated
by `create_order_for_booking` from the booking's coach. Backfill is a one-off
`UPDATE … FROM diary.booking`. (Alternative: derive via `order_line.booking_id → diary.booking.coach_user_id`
in the view — acceptable as the v1 if we want zero billing-schema change; documented as Open Q1.)

### 4.3 Aggregation views (sketch — `crm.*` schema, idempotent `CREATE OR REPLACE VIEW`)

Put these in a **new `crm` (or `analytics`) schema** owned by `marketing_crm` so they're clearly the read
model, not a producer table.

```sql
-- One row per booking, enriched — the spine the activity/occupancy/coach views build on.
CREATE OR REPLACE VIEW crm.vw_booking_activity AS
SELECT b.id            AS booking_id,
       b.club_id,
       b.booking_type,                         -- court | lesson | class
       b.resource_id,
       r.name          AS resource_name,
       b.coach_user_id,
       b.booked_by_user_id,
       bp.user_id      AS player_user_id,      -- WHO it was for (dependent-aware)
       bp.attended,
       b.status,
       b.starts_at,
       date_trunc('month', b.starts_at)::date AS activity_month,
       o.id            AS order_id,
       o.user_id       AS payer_user_id,       -- WHO paid (guardian for a child)
       o.settlement_mode,
       o.status        AS order_status,
       o.amount_minor,
       o.currency_code
FROM diary.booking b
JOIN diary.resource r          ON r.id = b.resource_id
LEFT JOIN diary.booking_party bp ON bp.booking_id = b.id AND bp.party_role IN ('host','player')
LEFT JOIN billing."order" o      ON o.id = b.order_id
WHERE b.status IN ('confirmed','completed','no_show');

-- Per-user spend per month (CLIENT cockpit "my spend"; rolls up to the PAYER).
CREATE OR REPLACE VIEW crm.vw_spend_monthly AS
SELECT o.club_id,
       o.user_id,                                   -- payer
       date_trunc('month', p.created_at)::date AS month,
       SUM(p.amount_minor) FILTER (WHERE p.direction='charge'  AND p.status='succeeded') AS gross_minor,
       SUM(p.amount_minor) FILTER (WHERE p.direction='refund')                            AS refund_minor,
       SUM(CASE WHEN p.direction='refund' THEN -p.amount_minor ELSE p.amount_minor END)
           FILTER (WHERE p.status IN ('succeeded','refunded'))                            AS net_minor,
       o.currency_code
FROM billing."order" o
JOIN billing.payment p ON p.order_id = o.id
GROUP BY o.club_id, o.user_id, date_trunc('month', p.created_at), o.currency_code;

-- Per-club (and optionally per-coach) revenue per month (OWNER + COACH cockpit).
CREATE OR REPLACE VIEW crm.vw_revenue_monthly AS
SELECT o.club_id,
       ol.coach_user_id,                            -- NULL for court-only revenue (see §4.2)
       date_trunc('month', p.created_at)::date AS month,
       SUM(p.amount_minor) FILTER (WHERE p.direction='charge'  AND p.status='succeeded') AS gross_minor,
       SUM(p.amount_minor) FILTER (WHERE p.direction='refund')                            AS refund_minor,
       o.currency_code
FROM billing."order" o
JOIN billing.payment p     ON p.order_id = o.id
LEFT JOIN billing.order_line ol ON ol.order_id = o.id
GROUP BY o.club_id, ol.coach_user_id, date_trunc('month', p.created_at), o.currency_code;

-- Member 360 (CLIENT self-view + OWNER People tab). One row per user.
CREATE OR REPLACE VIEW crm.vw_member_360 AS
SELECT u.id                AS user_id,
       m.club_id,
       u.first_name, u.surname, u.email,
       m.role, m.member_status,
       COALESCE(act.bookings_90d, 0)        AS bookings_90d,
       act.last_activity_at,
       COALESCE(spend.lifetime_net_minor,0) AS lifetime_net_minor,
       ms.status            AS membership_status,
       ms.current_period_end,
       nps.score            AS nps_latest
FROM iam.user u
JOIN iam.membership m ON m.user_id = u.id
LEFT JOIN LATERAL (
    SELECT count(*) FILTER (WHERE starts_at > now()-interval '90 days') AS bookings_90d,
           max(starts_at) AS last_activity_at
    FROM crm.vw_booking_activity a WHERE a.player_user_id = u.id AND a.club_id = m.club_id
) act ON true
LEFT JOIN LATERAL (
    SELECT SUM(net_minor) AS lifetime_net_minor
    FROM crm.vw_spend_monthly s WHERE s.user_id = u.id AND s.club_id = m.club_id
) spend ON true
LEFT JOIN billing.membership_subscription ms
       ON ms.user_id = u.id AND ms.club_id = m.club_id AND ms.status='active'
LEFT JOIN LATERAL (
    SELECT n.score FROM core.nps_response n
    JOIN core.app_user au ON au.id = n.user_id
    WHERE lower(au.email) = lower(u.email)
    ORDER BY n.submitted_at DESC LIMIT 1
) nps ON true;
```

### 4.4 Reusable helpers (Python, `marketing_crm/aggregation.py` — NEW)

So role specs call functions, not raw SQL:

```python
def spend_for_user(session, *, club_id, user_id, since=None, until=None) -> dict
    # client "my spend" — includes dependents' bookings because their orders' user_id is the guardian.

def usage_for_user(session, *, club_id, user_id, since=None, until=None) -> dict
    # bookings/lessons/classes count by type, attended; player_user_id-keyed (dependent-aware).

def revenue_for_scope(session, principal, *, since=None, until=None) -> dict
    # OWNER -> whole club; COACH -> their coach_user_id; via scope_clause over crm.vw_revenue_monthly.

def member_360(session, principal, *, user_id) -> dict
    # one user's card; member can only request self/dependents (enforced by scope + dependents_of).
```

Each applies `scope_clause(principal, …)` so the **same helper** serves client, coach, and owner.

---

## 5. Shared primitive: Refund-request workflow

A **client-initiated refund REQUEST** is distinct from the existing **admin direct Yoco refund**
(`POST /api/billing/yoco/refund`, record-only). The request is a lightweight approval object; on approval the
**admin still executes the actual refund** through the existing gateway path (we do not auto-call Yoco from a
member action). Booking is **never auto-reversed** (decision D8 holds).

### 5.1 DDL — `billing.refund_request` (NEW)

```sql
CREATE TABLE IF NOT EXISTS billing.refund_request (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    club_id         uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
    order_id        uuid NOT NULL REFERENCES billing."order"(id) ON DELETE CASCADE,
    requested_by    uuid NOT NULL,                 -- iam.user.id (the member/guardian)
    amount_minor    int  NOT NULL,                 -- requested amount (<= order paid)
    currency_code   text NOT NULL,
    reason          text,
    status          text NOT NULL DEFAULT 'pending' CHECK (status IN
                        ('pending','approved','declined','refunded','cancelled')),
    decided_by      uuid,                           -- admin/coach iam.user.id
    decided_at      timestamptz,
    decision_note   text,
    payment_id      uuid REFERENCES billing.payment(id) ON DELETE SET NULL, -- the refund payment, once executed
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_refundreq_club_status ON billing.refund_request (club_id, status);
CREATE INDEX IF NOT EXISTS ix_refundreq_order      ON billing.refund_request (order_id);
CREATE INDEX IF NOT EXISTS ix_refundreq_requester  ON billing.refund_request (requested_by);
-- at most one open request per order
CREATE UNIQUE INDEX IF NOT EXISTS ux_refundreq_open ON billing.refund_request (order_id)
    WHERE status = 'pending';
```

### 5.2 State machine

```
            (member)            (owner/coach)           (owner executes Yoco refund)
  pending ──approve──► approved ──record refund payment──► refunded   (terminal)
     │                    │
     ├──decline──► declined (terminal, with decision_note)
     └──cancel───► cancelled (member withdrew before decision; terminal)
```
- `pending → approved|declined` : `can(principal,'run_billing')` (club_admin; owner). A coach may be granted
  `approve` for their own lessons via a policy flag (Open Q3) but default is owner-only.
- `approved → refunded` : set when the admin runs the existing `POST /api/billing/yoco/refund` (or records a
  manual refund); link `payment_id`. The refund webhook / `apply_payment_event(refunded)` is the existing
  record-only path; we just stamp the request `refunded`.
- `cancel` : only from `pending`, only by the requester.
- Idempotency: at most **one non-terminal** request per `(order_id)` (the partial unique index above).

### 5.3 Endpoints

```
POST   /api/me/refund-requests                 {order_id, amount_minor?, reason}  -> pending
                                               (validates order belongs to requester or their dependent's payer)
GET    /api/me/refund-requests                 my requests
DELETE /api/me/refund-requests/<id>            cancel (pending only)

GET    /api/admin/refund-requests              owner/coach queue (scope_clause: owner=club, coach=own lessons)
POST   /api/admin/refund-requests/<id>/approve {decision_note?}   -> approved
POST   /api/admin/refund-requests/<id>/decline {decision_note}    -> declined
# Executing the money refund reuses the EXISTING POST /api/billing/yoco/refund;
# its handler stamps the linked request -> refunded + sets payment_id.
```

### 5.4 Events

- `refund_requested` (transactional → owner/coach notification; payload: `ref_type=order`, `ref_id`,
  `amount_minor`, `reason`).
- `refund_approved` / `refund_declined` (transactional → member notification).
- `refund_processed` (transactional; on the actual `refunded` payment — reuses the receipt rail). Note the
  existing `payment_succeeded` covers charges; refunds get their own event so the member is told.

---

## 6. Shared primitive: Notifications / receipts

We do **not** add a new notification engine. The rail is **`emit()` → `core.usage_event` → (transactional|
marketing gate) → Klaviyo, with SES fallback**. Two additions:

1. **An optional in-app notification log** so the portal can show a bell/inbox without depending on email
   delivery — a thin projection of transactional events.
2. **A standardised event → notification map** (below) so each role spec knows what fires when, instead of
   guessing.

### 6.1 DDL — `core.notification` (NEW, optional but recommended)

```sql
CREATE TABLE IF NOT EXISTS core.notification (
    id           bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    club_id      uuid NOT NULL,
    user_id      uuid NOT NULL,            -- iam.user.id (recipient; the ADULT contact)
    event_type   text NOT NULL,            -- mirrors the usage_event
    title        text NOT NULL,
    body         text,
    ref_type     text,
    ref_id       text,
    read_at      timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_notification_user ON core.notification (club_id, user_id, created_at DESC);
```
Written inside the same `emit()` thread for **transactional** events only (booking/payment/refund/membership),
keyed to the resolved recipient `iam.user`. Marketing events do **not** create in-app notifications.
`GET /api/me/notifications`, `POST /api/me/notifications/<id>/read`.

### 6.2 Event → notification map

| Event | Recipient | Channel(s) | When |
|---|---|---|---|
| `booking_confirmed` | booker (adult) | Klaviyo + **SES fallback** + in-app | now |
| `booking_cancelled` / `booking_rescheduled` | booker | Klaviyo + in-app | now |
| `booking_reminder` | booker | Klaviyo (T-24h/T-2h cron) | now |
| `class_enrolled` / `class_waitlisted` / `waitlist_slot_open` | enrollee/guardian | Klaviyo + in-app | now |
| `payment_succeeded` | payer | Klaviyo (receipt) + in-app | now |
| `monthly_statement_ready` | account holder | Klaviyo + in-app | now |
| `membership_started` / `membership_lapsed` | member | Klaviyo (marketing) | now |
| `refund_requested` | **owner** (+ coach if own-lesson) | Klaviyo + in-app | **NEW** |
| `refund_approved` / `refund_declined` / `refund_processed` | member | Klaviyo + in-app | **NEW** |
| `lesson_completed` | client | Klaviyo (NPS/rebook nudge, marketing) | now |
| `dependent_added` | guardian | (none / silent) | **NEW** |

Minor-PII rule unchanged: recipient/contact is always the **adult**; no child PII in any payload (§0).

---

## 7. Events to add / standardise (extend `contracts/events.md`)

Add these names (with the §0 payload conventions). Producers: client/coach/owner/billing lanes.

| Event | txn? | Fired when | Payload (beyond `club_id`,`email`) |
|---|---|---|---|
| `profile_updated` | marketing | member/coach edits profile | `role?`, `fields?` (changed keys, non-PII) |
| `dependent_added` | marketing | guardian adds a dependent | `relationship`, `is_minor` (NO child PII) |
| `plan_changed` | marketing | membership plan up/down/cancel intent | `ref_type=membership_subscription`,`ref_id`,`from_plan?`,`to_plan?` |
| `refund_requested` | **transactional** | member raises a refund request | `ref_type=order`,`ref_id`,`amount_minor`,`reason?` |
| `refund_approved` | **transactional** | owner approves | `ref_type=order`,`ref_id`,`amount_minor` |
| `refund_declined` | **transactional** | owner declines | `ref_type=order`,`ref_id`,`reason?` |
| `refund_processed` | **transactional** | refund payment recorded | `ref_type=order`,`ref_id`,`amount_minor`,`provider` |
| `lesson_completed` | marketing | *(already in contract)* | — |
| `commission_accrued` | system | coach revenue/commission line created | `ref_type=order`,`ref_id`,`coach_user_id`,`amount_minor` (Open Q2 — only if coach payouts ship) |
| `attendance_marked` | system | coach marks roster attendance | `ref_type=booking\|enrolment`,`ref_id`,`attended` (drives utilisation/no-show analytics) |

`commission_accrued`/`attendance_marked` are `system` (state/analytics, no member send). Add to the contract
table; keep one-name-everywhere discipline.

---

## 8. Build phasing

**Phase 1 — Foundations (prerequisites; the role specs block on these).** Land before/with client/coach/owner
feature work, behind the existing compile + scratch-DB gates.
1. `iam.dependent` DDL + `dependents_of` + `/api/me/dependents` CRUD + `dependent_added` event + permission
   ownership extension. *(client + coach rosters depend on it)*
2. `marketing_crm/scoping.py` `scope_clause` + `marketing_crm/aggregation.py` helpers +
   `billing.order_line.coach_user_id` (or the view-derived alternative) + the `crm.vw_*` views.
   *(every cockpit + client spend + coach revenue depend on it)*
3. `billing.refund_request` DDL + state machine + member/admin endpoints + the four refund events; wire the
   existing Yoco refund handler to stamp `refunded`.
4. Extend `contracts/events.md` with §7 events. `core.notification` table + `/api/me/notifications`.

**Phase 2 — The CRM cockpit (one engine, three lenses).** The owner's "Phase 2".
5. **Owner cockpit**: light up the four stubbed endpoints (`/revenue`,`/occupancy`,`/coach-utilization`,
   `/attendance`) over the `crm.vw_*` views with `scope_clause(club_admin)`; business-health scalars
   (MRR-equiv from active memberships, bookings, no-show rate); People tab uses `vw_member_360`.
6. **Coach console analytics**: the *same* helpers with `scope_clause(coach)` — my upcoming lessons, my
   clients (via roster), my revenue/utilisation this month.
7. **Client cockpit**: `spend_for_user`/`usage_for_user` (self + dependents), notification inbox, refund-request
   UI, membership/plan status.
8. **Platform operator view**: cross-club (predicate lifted) — totals, per-club MRR, churn. SaaS console.
9. Klaviyo segment traits per role (coach vs member) on top of the existing per-club `club` trait.

**Ordering rationale:** 1→2→3 are independent enough to parallelise after `scope_clause`/views exist; the
cockpits (5–8) all consume the Phase-1 helpers, so they cannot start first. NPS/consent cockpit panes already
work and need no Phase-1 dependency.

---

## 9. Reuse-vs-NEW map (what ports from 1050)

| Concern | 1050 reference | Here |
|---|---|---|
| Single backoffice over billing+core, role-gated | `marketing_crm/backoffice/views.py`, `crm_api.py` | **PORT the pattern** (one engine, role-scoped). Replace `ADMIN_EMAILS`/`member.role!='coach'` with `Principal` + `scope_clause` (D6). |
| Derived analytics views (`vw_account_lifecycle`, `vw_business_health`, `vw_revenue_monthly`, `vw_usage_daily`, `vw_nps_*`) | `marketing_crm/backoffice/views.py` | **PORT & adapt** to `crm.vw_*` over our `billing.*`+`diary.*`+`core.*`; add `club_id`/`coach_user_id` to every grouping. |
| Append-only ledger, balance = SUM(grant)−SUM(consume), idempotent | `billing_service.py`, `models_billing.py` | **ALREADY HAVE** `billing.account_ledger` + `order`/`payment`. Reuse; no port. |
| Parent↔junior / coach↔player relationships, `is_minor`, parental consent | `core.relationship`, `core.person`, `core.consent` | **ALREADY PORTED** to our `core.*`. NEW: `iam.dependent` to make the child a bookable UUID actor. |
| NPS collection + sentiment + detractor signal | `marketing_crm/feedback/`, `core.nps_response` | **ALREADY HAVE** `core.nps_response` + cockpit `/nps`. Reuse; optionally port detractor-follow-up flow. |
| Klaviyo profile upsert + event forward + opt-in gate | `marketing_crm/crm_sync/` | **ALREADY HAVE** `emit()`+`forward_event()`. Reuse; add per-role trait + refund events. |
| Refund **request** workflow | *(none — 1050 had no member-initiated refund request)* | **NEW** (`billing.refund_request`). |
| Per-coach revenue/utilisation scoping | *(1050 coaches were view-only, no revenue)* | **NEW** (`coach_user_id` on revenue path + coach `scope_clause`). |
| In-app notification log | *(1050 leaned on Klaviyo only)* | **NEW** (`core.notification`, optional). |

**Do NOT port:** ML/T5/GPU/video, `entitlement_grant`/credit-per-match model (our pricing is per-duration
PAYG + membership-covered, not a credit ledger), the Wix/PayPal-specific subscription_state mirror.

---

## 10. Open questions

1. **`coach_user_id` on revenue:** denormalise onto `billing.order_line` (cleaner queries, one backfill) vs
   derive in the view via `order_line.booking_id → diary.booking.coach_user_id` (zero billing-schema change)?
   *Recommendation: denormalise — class enrolments and tab lines don't always have a booking_id.*
2. **Coach payouts / commission:** is coach revenue just *reporting* (what their lessons grossed) for v1, or do
   we accrue a payable commission (`commission_accrued`, a `billing.payout` ledger)? Affects whether
   `commission_accrued` ships now. *Assume reporting-only for v1.*
3. **Can a coach approve refunds for their own lessons,** or owner-only? Default owner-only; a club policy flag
   `allow_coach_refund_approval` could open it.
4. **`crm` vs `analytics` schema name** for the read-model views, and whether they're plain views (simplest,
   always-fresh) or materialised (faster cockpit, needs refresh cron). *Start with plain views; materialise
   only if a cockpit query is slow.*
5. **Dependent → adult transition:** when a minor turns 18 / wants their own login, do we re-point their
   `iam.user` to a real `clerk_user_id` (preserving booking history) — yes, the non-login-user design makes
   this a single `clerk_user_id` set + remove the `iam.dependent` row.
6. **Cross-club person identity:** an `iam.user` is global; `core.account` is email-linked. For a member at two
   clubs, spend/usage aggregation is per `(club_id, user_id)` — confirm the role specs never need a cross-club
   "my spend" (they don't today).

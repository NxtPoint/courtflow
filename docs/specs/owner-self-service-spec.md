# Owner Self-Service + Commission/Rental Revenue Model + Owner Cockpit — Spec

> **AS-BUILT (2026-07-02): owner SPA in progress → see [ADMIN-REDESIGN.md](ADMIN-REDESIGN.md).** A new
> responsive drill-through admin SPA (`frontend/app/admin_app.html` + `frontend/js/admin_app.js`) is
> being built, served at **`/admin-app`** — the classic `/admin` console (`admin.js`) stays live until
> sign-off. The detailed sections below reflect the classic console; the SPA is the emerging shape. In short:
> - **Responsive** — bottom-nav on mobile, **left side-rail on desktop** (`.cf-admin` CSS).
> - **Nav: Home · People · Money · Diary · Setup** (+ Insights).
> - **Step 1 SHIPPED** — shell + nav + a **command-center Home**: four focus cards (Today / Money /
>   People-attention / Approvals) via new `GET /api/admin/home` (`admin.repositories.admin_home`, guarded).
> - **Steps 2–7 = placeholders** (People / Money / Diary / Setup / Insights build out per the
>   [ADMIN-REDESIGN.md](ADMIN-REDESIGN.md) build order).
> - One design system (`cf-*`), the same drill-through + single-event-story golden rule as the client and
>   coach SPAs.

> **AS-BUILT NOTE (2026-06-26):** the owner console SHIPPED (2026-06-25/26) on the shared `crm_ui.js`:
> a **per-service commission editor** (club/coach/per-service incl. classes), the financial cockpit
> (per-coach settlement, refund-aware), and People-360. Read this for design intent; see
> **`BUSINESS-RULES.md`** §6–7 + **`INVENTORY.md`** for what's live.

Status: DRAFT (implementation-ready). Lane owner: **Agent A (Foundation) / Admin lane**, with billing-ledger
work coordinated with **Agent C (Billing)** and reporting with **Agent D (CRM)**.
Author POV: the **club owner** of NextPoint.

This spec scopes three things the owner needs and CourtFlow does not yet have:

1. **A commission/rental revenue model** — how the owner monetises coaches: a flat **monthly rent** per coach
   AND/OR a **commission %** on lessons that can vary **per lesson type** (and per coach).
2. **An owner financial cockpit** — revenue, commissions earned / rent due per coach, payouts, MRR,
   utilisation, top coaches, trends.
3. **The gaps in existing owner self-service** — the config the owner should be able to edit but currently can't.

> Source-of-truth research is summarised in §11 (current-state map). The headline facts:
> - **CourtFlow has NO commission / rent / payout / revenue-split / coach-earnings schema anywhere.** The full
>   order amount is implicitly the club's; nothing tracks a coach's cut or what a coach owes.
> - **1050 also has no commission engine** — coaches there are an acquisition channel, not a revenue line. But
>   1050 gives us strong reusable patterns: the **idempotent record-only payment log**, a **signed-delta
>   single-table ledger** (`core.credit_ledger`), an **in-SQL pricing-map join**, and a **views-only reporting
>   layer** consumed by thin passthrough endpoints. We copy those shapes; the commission engine is greenfield.
> - The **cockpit backend scaffold already exists** (`marketing_crm/backoffice/blueprint.py`, registered at
>   `/api/admin/cockpit/*`) but `revenue` / `occupancy` / `coach-utilisation` / `attendance` are **stubs with no
>   SQL**, and the frontend Cockpit tab is a **static placeholder**.

---

## 0. Design principles (carried from CLAUDE.md + 1050)

- **Multi-tenant from day one.** Every new row carries `club_id`; never query without it.
- **Reuse, don't import.** Copy 1050 patterns (`record_payment` idempotency, signed-delta ledger, views-only
  reporting). Do not touch `C:\dev\webhook-server`.
- **`billing/` core stays a clean seam.** The commission split is computed and recorded **at the same point
  `apply_payment_event` records a successful charge** — by *fanning out* split rows after the payment insert,
  not by rewriting order/ledger logic.
- **Idempotent boot DDL** (`ADD COLUMN/TABLE IF NOT EXISTS`); no migration framework. `python -m db` twice = no-op.
- **Idempotent money.** Every split/payout write is guarded by a natural unique key, mirroring
  `billing.payment`'s `(provider, provider_payment_id)` and 1050's `task_id`/`event_id` discipline.
- **One design system.** Config screen + cockpit reuse `cf-*` classes from `frontend/app/app.css`; we add a small
  **stat/KPI-card** set (none exists today) and nothing else.
- **Reporting is views-only.** Aggregation lives in `CREATE OR REPLACE VIEW`s; HTTP endpoints are thin
  passthroughs (1050's "rule #2"). This keeps reconciliation a single SQL query.

---

## 1. The commission / rental revenue model

### 1.1 Concepts and vocabulary

| Term | Meaning |
|---|---|
| **Coach agreement** | The standing monetisation arrangement between the club (owner) and one coach: optional monthly **rent** and/or **commission**. |
| **Rent** | A flat amount the **coach owes the club** every month, independent of lessons taught (court/facility rental). |
| **Commission** | A **percentage of lesson revenue** the **club keeps**; the remainder is the **coach's earning**. |
| **Lesson type** | The thing commission can vary by. In CourtFlow's data model a "lesson type" is a `billing.product(kind='lesson')` (coach-scoped via `product.coach_user_id`). See §11.3 — there is no separate `service`/`lesson_type` table; the product is the lesson type. We commission by `product_id` (and a fallback for `kind='class'`). |
| **Commission rule** | One configurable rate row, scoped to `club` (default), a `product` (lesson type), and/or a `coach`. |
| **Split** | The decomposition of one lesson payment into **owner cut** + **coach earning** (+ provider fee, out of scope v1). |
| **Coach ledger** | A signed running account per coach: commissions reduce earnings to the coach side, rent accrues a debit, payouts settle it. |
| **Payout** | A settlement event recording money moved (or netted) between club and coach for a period. |

**Resolution of "rent and/or commission":** an agreement may set rent only, commission only, or both. Rent and
commission are **independent** — a coach can be on pure rent (no commission), pure commission (no rent), or a
hybrid (e.g. R1,500/mo rent + 10% on lessons).

### 1.2 Commission resolution algorithm (precedence)

When a lesson payment settles, we must pick **one** commission rate for that lesson. Rules are scored by
**specificity**; the **most specific active rule wins**. Scope dimensions and precedence (highest → lowest):

```
1. coach + product   (this coach, this lesson type)        — most specific
2. product           (any coach, this lesson type)
3. coach             (this coach, any lesson type)
4. club              (default rate for the whole club)      — least specific
   (none)            -> commission_pct = 0  (coach keeps 100%, club takes nothing)
```

Among candidate rules of equal specificity, pick the one with the latest `effective_from` that is `<= now`
and (`effective_to IS NULL OR effective_to > now`) and `active = true`. Ties beyond that → highest `id`
(deterministic). This mirrors `diary/pricing.py::price_for`'s exact→nearest→any cascade so it will feel
familiar in the codebase.

**Pseudocode:**
```
def resolve_commission_pct(session, *, club_id, product_id, coach_user_id, at):
    rows = SELECT * FROM billing.commission_rule
           WHERE club_id = :club_id AND active
             AND effective_from <= :at
             AND (effective_to IS NULL OR effective_to > :at)
             AND (product_id   IS NULL OR product_id   = :product_id)
             AND (coach_user_id IS NULL OR coach_user_id = :coach_user_id)
    # score: coach? +2, product? +1  -> higher = more specific
    best = max(rows, key=lambda r: (score(r), r.effective_from, r.id), default=None)
    return best.commission_pct if best else Decimal(0)
```

> **Rent** is NOT resolved per-payment — it is a property of the **agreement** (one figure per coach per month),
> accrued monthly by a cron, not at payment time.

### 1.3 When the split is computed and recorded

**At payment time, inside `apply_payment_event` on `charge_succeeded`** — immediately after the existing
idempotent `billing.payment` insert and order/booking confirmation, we add a **commission fan-out step**:

```
on charge_succeeded(order):
    record_payment(...)              # EXISTING (idempotent)
    mark_order_paid + confirm_booking# EXISTING
    --- NEW ---
    for each lesson order_line in order:
        booking   = booking for order_line.booking_id          # diary.booking
        if booking.booking_type != 'lesson': continue          # courts/classes: see §1.7
        coach     = booking.coach_user_id
        product_id= product behind order_line.price_id          # price -> product
        pct       = resolve_commission_pct(club_id, product_id, coach, at=paid_at)
        gross     = order_line.amount_minor                      # see §1.6 gross-vs-net Q
        owner_cut = round(gross * pct / 100)
        coach_earn= gross - owner_cut
        write commission_split rows (idempotent on (payment_id, order_line_id, party))
        post coach_ledger entry (+coach_earn to coach)           # signed-delta
```

This runs in the **same transaction** as the payment record (savepoint-guarded like `_confirm_held_bookings`,
so a split failure never blocks settlement — it logs and continues, leaving a backfillable gap that the
reconciliation view surfaces). It is **idempotent**: the unique key `(payment_id, order_line_id, party_type)`
means a webhook replay re-enters `apply_payment_event`, dedupes on `event_hash` (existing), and even if it
reached the fan-out, `ON CONFLICT DO NOTHING` makes it a no-op.

**Refund handling:** a `refunded` event writes a **negative** clawback split (`basis='refund_clawback'`) and a
negative coach-ledger entry, mirroring 1050's signed convention — but, consistent with CourtFlow's record-only
refund policy (docs/05 §8), it does **not** reverse the booking. Open question §10 on whether commission is
clawed back on refund (default: yes, proportional).

### 1.4 Data model — new tables (DDL)

New DDL lives in **`billing/schema.py`** (commission is billing-domain) registered in `db.BOOT_MODULES`.

```sql
-- ── 1. Coach agreement: rent + commission posture, one current row per coach ──
CREATE TABLE IF NOT EXISTS billing.coach_agreement (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    club_id         uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
    coach_user_id   uuid NOT NULL,                  -- iam.user (coach)
    rent_minor      integer NOT NULL DEFAULT 0,     -- monthly rent the coach owes (cents)
    rent_currency   text    NOT NULL DEFAULT 'ZAR',
    rent_day        integer NOT NULL DEFAULT 1      -- day-of-month rent accrues (1..28)
                      CHECK (rent_day BETWEEN 1 AND 28),
    -- a club-wide default commission lives as a club-scoped commission_rule (uniform resolution).
    -- this row is the "is this coach monetised, and what rent" record.
    status          text NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active','ended')),
    effective_from  date NOT NULL DEFAULT CURRENT_DATE,
    effective_to    date,                            -- null = open
    notes           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_coach_agreement_club  ON billing.coach_agreement(club_id, coach_user_id);
-- one active agreement per coach at a time:
CREATE UNIQUE INDEX IF NOT EXISTS ux_coach_agreement_active
    ON billing.coach_agreement(club_id, coach_user_id)
    WHERE status = 'active' AND effective_to IS NULL;

-- ── 2. Commission rule: scoped, dated rate rows (the engine's input) ──
CREATE TABLE IF NOT EXISTS billing.commission_rule (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    club_id         uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
    scope           text NOT NULL                    -- denormalised for clarity/queries
                      CHECK (scope IN ('club','product','coach','coach_product')),
    product_id      uuid,                            -- billing.product (lesson type); null = any
    coach_user_id   uuid,                            -- null = any coach
    commission_pct  numeric(5,2) NOT NULL            -- 0.00..100.00, % the CLUB keeps
                      CHECK (commission_pct >= 0 AND commission_pct <= 100),
    effective_from  timestamptz NOT NULL DEFAULT now(),
    effective_to    timestamptz,                     -- null = open
    active          boolean NOT NULL DEFAULT true,
    note            text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_commission_rule_resolve
    ON billing.commission_rule(club_id, active, product_id, coach_user_id);
-- (scope is derivable: coach_product if both set, product if only product, coach if only coach, else club.
--  We store it explicitly to make the config UI and cockpit listing trivial.)

-- ── 3. Commission split: the per-payment decomposition (record-only, signed) ──
CREATE TABLE IF NOT EXISTS billing.commission_split (
    id              bigserial PRIMARY KEY,
    club_id         uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
    payment_id      uuid NOT NULL REFERENCES billing.payment(id) ON DELETE CASCADE,
    order_line_id   uuid,                            -- which line was split (billing.order_line)
    booking_id      uuid,                            -- diary.booking (coach/lesson attribution)
    coach_user_id   uuid,
    product_id      uuid,
    rule_id         uuid REFERENCES billing.commission_rule(id),
    party_type      text NOT NULL                    -- 'owner' | 'coach'
                      CHECK (party_type IN ('owner','coach')),
    basis           text NOT NULL                    -- 'lesson_commission' | 'class_commission' | 'refund_clawback'
                      CHECK (basis IN ('lesson_commission','class_commission','refund_clawback')),
    gross_minor     integer NOT NULL,                -- line gross used as the base
    commission_pct  numeric(5,2),                    -- snapshot of resolved rate
    amount_minor    integer NOT NULL,                -- SIGNED: owner cut and coach earn (refund => negative)
    currency        text NOT NULL DEFAULT 'ZAR',
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now()
);
-- idempotency: one (owner,coach) pair per payment line
CREATE UNIQUE INDEX IF NOT EXISTS ux_commission_split
    ON billing.commission_split(payment_id, order_line_id, party_type);
CREATE INDEX IF NOT EXISTS ix_commission_split_coach
    ON billing.commission_split(club_id, coach_user_id, occurred_at);
-- INVARIANT (cheap reconciliation): SUM(amount_minor) over a payment line's owner+coach = gross_minor.

-- ── 4. Coach ledger: signed running account per coach (earnings +, rent -, payouts -) ──
CREATE TABLE IF NOT EXISTS billing.coach_ledger (
    id              bigserial PRIMARY KEY,
    club_id         uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
    coach_user_id   uuid NOT NULL,
    entry_type      text NOT NULL                    -- 'commission_earning' | 'rent_charge' | 'payout' | 'adjustment'
                      CHECK (entry_type IN ('commission_earning','rent_charge','payout','adjustment')),
    amount_minor    integer NOT NULL,                -- SIGNED: + owed TO coach, - owed BY coach / paid out
    currency        text NOT NULL DEFAULT 'ZAR',
    ref_type        text,                            -- 'split' | 'rent_period' | 'payout' | 'manual'
    ref_id          text,                            -- split.id / 'YYYY-MM' / payout.id
    note            text,
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_coach_ledger ON billing.coach_ledger(club_id, coach_user_id, occurred_at);
-- balance(coach) = SUM(amount_minor): positive = club owes coach, negative = coach owes club (net rent).
-- idempotency for accrual entries via deterministic ref:
CREATE UNIQUE INDEX IF NOT EXISTS ux_coach_ledger_rent
    ON billing.coach_ledger(club_id, coach_user_id, ref_id)
    WHERE entry_type = 'rent_charge';
CREATE UNIQUE INDEX IF NOT EXISTS ux_coach_ledger_earning
    ON billing.coach_ledger(club_id, coach_user_id, ref_id)
    WHERE entry_type = 'commission_earning';

-- ── 5. Payout: a settlement event for a coach for a period ──
CREATE TABLE IF NOT EXISTS billing.coach_payout (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    club_id         uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
    coach_user_id   uuid NOT NULL,
    period_start    date NOT NULL,
    period_end      date NOT NULL,
    gross_earnings_minor integer NOT NULL DEFAULT 0, -- commissions earned in period
    rent_minor      integer NOT NULL DEFAULT 0,      -- rent charged in period
    net_minor       integer NOT NULL DEFAULT 0,      -- earnings - rent (signed)
    currency        text NOT NULL DEFAULT 'ZAR',
    status          text NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft','approved','paid','void')),
    method          text,                            -- 'eft' | 'cash' | 'offset' (rent netted)
    paid_at         timestamptz,
    note            text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_coach_payout ON billing.coach_payout(club_id, coach_user_id, period_start);
```

**Why this shape (reuse map):** `commission_split` = 1050's `billing.payment` record-only-log discipline +
`core.credit_ledger`'s signed-delta; `coach_ledger` = a generalisation of `core.credit_ledger` to money with a
party; resolution cascade = `diary/pricing.py::price_for`; idempotency keys = `billing.payment`'s `(provider,
provider_payment_id)` and `monthly_refill_log`'s `(account_id, year_month)`.

### 1.5 Rent accrual (cron, not payment-time)

A monthly job posts a `rent_charge` coach-ledger entry (negative) per active agreement with `rent_minor > 0`,
keyed by `ref_id = 'YYYY-MM'` so it's idempotent. **Reuse the existing cron seam** (`crons/trigger.py` →
`/api/cron/<job>`; CLAUDE.md notes crons are dispatcher-only and handlers live in lanes). Add a
**`coach-rent-accrual`** job (Billing lane handler). Until wired, rent can also be accrued **lazily** at
cockpit-read time (like `release_expired_holds`) to avoid a paid cron pre-launch — recommended for v1: compute
accrued-rent-to-date on the fly in the reporting view and only materialise ledger rows when a payout is drawn.

### 1.6 Gross vs net base

v1 default: **commission is on `order_line.amount_minor` (gross of provider fees, inclusive of VAT if prices are
VAT-inclusive)** — the simplest, matches how prices are stored today (`amount_minor` only, no tax line; see
§11.5). The Yoco processing fee and VAT treatment are **open questions for the owner** (§10). The schema carries
`gross_minor` + `commission_pct` snapshots so a future net-of-fee or ex-VAT basis is a config flag, not a
migration.

### 1.7 Courts, classes, memberships — what is commissionable

- **Lessons** (`booking_type='lesson'`): commissionable. The primary case.
- **Membership-covered courts** (`settlement_mode='membership_covered'`, `amount_minor=0`): **gross is 0 → split
  is 0**. Naturally excluded. Court rental is the club's anyway.
- **Court bookings** (PAYG courts): club revenue, **no coach** → no commission. (A future "coach rents a court"
  flow could create a coach-side rent debit; out of v1 scope.)
- **Classes** (`booking_type='class'`, coach-led): commissionable via the same engine using
  `basis='class_commission'` and the class's `billing.product(kind='class')` as the "lesson type". v1 may ship
  lessons-only and add classes in phase 2 (the code path is identical).

---

## 2. The commission/rental config screen (owner setup)

### 2.1 Where it lives

A new **"Coach payments"** (working title) tab in **Settings** (alongside Club profile · Hours · Courts ·
Services & pricing · Coaches · Payments). It reuses `window.AdminUI` components and the `cf-*` design system.
Optionally a **"Coach agreement"** sub-panel surfaces on each coach in the existing Coaches tab — but the
dedicated tab is the primary surface because commission rules can be club-wide.

### 2.2 Sections & fields

**A. Club default commission** (one field)
- `Default commission %` — number `0–100`, step `0.5`. Helper: "% of every lesson the club keeps. Coaches keep
  the rest. Override per coach or per lesson type below." → writes a `scope='club'` `commission_rule`.

**B. Per coach** (a card per coach, from `GET /api/admin/coaches`)
- `Monthly rent` (amount, currency from club) — "Flat monthly fee this coach pays you. Leave 0 for none."
- `Rent day` (1–28) — when it accrues.
- `Commission % (this coach)` — optional override of the club default for all of this coach's lessons →
  `scope='coach'` rule.
- A **per-lesson-type override sub-list**: for each of the coach's `billing.product(kind='lesson')`, an optional
  `%` → `scope='coach_product'` rule.
- Read-only **"effective rate" preview** per lesson type, computed by the resolution algorithm, so the owner
  sees exactly what will apply (mirrors the booking wizard's live-price preview).

**C. Per lesson type, club-wide** (optional)
- For each lesson `product`, an optional `%` applying to **any** coach → `scope='product'` rule. Surfaced
  inline on the Services & pricing tab too (a "club commission %" field next to each lesson product) is a nice
  shortcut; the canonical editor is here.

### 2.3 Validation

- `commission_pct` ∈ [0, 100], max 2 decimals. `rent_minor` ≥ 0 integer cents. `rent_day` ∈ [1, 28].
- A new rate **supersedes** rather than edits in place: setting a new rate closes the prior rule
  (`effective_to = now`, `active=false`) and inserts a new one (preserves history for the cockpit). Same INSERT
  pattern guarding history as 1050's grants.
- Currency is the club's `currency_code` (read-only).
- Warn (don't block) if a coach has neither rent nor commission (they cost the club nothing — that's valid).

### 2.4 Endpoints (new, `admin_bp`, `/api/admin/*`, `_admin()`-guarded, principal-scoped `club_id`)

```
GET    /api/admin/coach-agreements
       -> { club_default_pct, coaches:[ { coach_user_id, name,
              rent_minor, rent_day, currency,
              coach_pct,                         # resolved scope='coach' override or null
              lesson_types:[ {product_id, name, club_pct, coach_pct, effective_pct} ] } ],
            rules:[ <commission_rule rows> ] }   # full list for audit/history

PUT    /api/admin/coach-agreements/<coach_user_id>
       body { rent_minor?, rent_day?, status? }  # upsert billing.coach_agreement (supersede on change)

POST   /api/admin/commission-rules
       body { scope, product_id?, coach_user_id?, commission_pct }
       -> closes any matching active rule, inserts new; returns the rule

DELETE /api/admin/commission-rules/<rule_id>      # deactivate (active=false, effective_to=now)

GET    /api/admin/commission-rules/preview?coach_user_id=&product_id=
       -> { effective_pct, winning_rule_id, scope }   # drives the "effective rate" UI preview
```

Repository helpers go in `admin/repositories.py` (plain SQL, no DDL) + a small
`billing/commission.py` for `resolve_commission_pct`, `record_split`, `post_coach_earning`, `accrue_rent`,
`coach_balance` (so the engine is testable and callable from `apply_payment_event`).

---

## 3. The owner financial cockpit

### 3.1 Backend: views-only, thin endpoints

Implement the **already-registered stub** `GET /api/admin/cockpit/revenue` and add coach-finance endpoints, all
club-scoped and admin-gated (the existing `cockpit_bp` / `_admin_ctx()` already do this). Aggregation lives in
`CREATE OR REPLACE VIEW`s (1050 pattern); endpoints `SELECT * FROM` them.

**Endpoints (cockpit_bp, `/api/admin/cockpit/*`):**

```
GET /revenue?from=&to=         -> gross / refunds / net by month + by service kind (court/lesson/class/membership)
GET /coach-earnings?from=&to=  -> per coach: gross lesson revenue, commission earned (owner cut),
                                  coach earning, rent due, net owed, payout status
GET /coach-utilisation?from=&to= -> per coach: booked hours, available hours, utilisation %, lesson count
GET /occupancy?from=&to=       -> court utilisation: booked vs available slot-minutes by court/day
GET /memberships               -> active count, MRR, new/churned this month, PAYG-vs-membership court mix
GET /summary                   -> KPI header scalars (the cockpit landing strip)
```

Existing live cockpit endpoints (`signups`, `usage`, `consent`, `nps`) stay; we are filling the financial gap.

### 3.2 Aggregation SQL sketches

**Revenue by month + service kind** (net from the payment log, 1050's `vw_revenue_monthly` shape):
```sql
SELECT date_trunc('month', p.created_at)::date AS month,
       prod.kind AS service_kind,
       SUM(ol.amount_minor) FILTER (WHERE p.direction='charge') AS gross_minor,
       SUM(ol.amount_minor) FILTER (WHERE p.direction='refund') AS refund_minor,
       SUM(CASE WHEN p.direction='charge' THEN ol.amount_minor
                ELSE -ol.amount_minor END)                       AS net_minor,
       count(DISTINCT p.id) FILTER (WHERE p.direction='charge')  AS payments
FROM billing.payment p
JOIN billing."order" o      ON o.id = p.order_id
JOIN billing.order_line ol  ON ol.order_id = o.id
LEFT JOIN billing.price pr  ON pr.id = ol.price_id
LEFT JOIN billing.product prod ON prod.id = pr.product_id
WHERE p.club_id = :club_id AND p.status='succeeded'
GROUP BY 1, 2 ORDER BY 1, 2;
```

**Per-coach earnings / rent / net owed** (the heart of the cockpit, straight off split + ledger):
```sql
WITH splits AS (
  SELECT coach_user_id,
         SUM(amount_minor) FILTER (WHERE party_type='coach') AS coach_earn_minor,
         SUM(amount_minor) FILTER (WHERE party_type='owner') AS owner_cut_minor,
         SUM(gross_minor)  FILTER (WHERE party_type='owner') AS gross_lesson_minor
  FROM billing.commission_split
  WHERE club_id=:club_id AND occurred_at >= :from AND occurred_at < :to
  GROUP BY coach_user_id),
rent AS (
  SELECT coach_user_id, SUM(amount_minor) AS rent_minor   -- negative
  FROM billing.coach_ledger
  WHERE club_id=:club_id AND entry_type='rent_charge'
    AND occurred_at >= :from AND occurred_at < :to
  GROUP BY coach_user_id),
bal AS (
  SELECT coach_user_id, SUM(amount_minor) AS balance_minor  -- lifetime net owed
  FROM billing.coach_ledger WHERE club_id=:club_id GROUP BY coach_user_id)
SELECT u.id AS coach_user_id, u.full_name,
       COALESCE(s.gross_lesson_minor,0) AS gross_lesson_minor,
       COALESCE(s.owner_cut_minor,0)    AS commission_earned_minor,   -- owner keeps
       COALESCE(s.coach_earn_minor,0)   AS coach_earning_minor,
       COALESCE(-r.rent_minor,0)        AS rent_due_minor,
       COALESCE(s.coach_earn_minor,0) + COALESCE(r.rent_minor,0) AS net_to_coach_minor, -- signed
       COALESCE(b.balance_minor,0)      AS lifetime_balance_minor
FROM iam.user u
LEFT JOIN splits s ON s.coach_user_id=u.id
LEFT JOIN rent   r ON r.coach_user_id=u.id
LEFT JOIN bal    b ON b.coach_user_id=u.id
WHERE u.id IN (SELECT coach_user_id FROM billing.coach_agreement WHERE club_id=:club_id)
ORDER BY commission_earned_minor DESC;   -- "top coaches by commission"
```

**Coach utilisation** (booked vs available hours; available from `diary.availability_rule`):
```sql
SELECT b.coach_user_id,
       SUM(EXTRACT(epoch FROM (b.ends_at-b.starts_at))/3600.0)
         FILTER (WHERE b.status IN ('confirmed','completed')) AS booked_hours,
       count(*) FILTER (WHERE b.booking_type='lesson'
                          AND b.status IN ('confirmed','completed')) AS lesson_count
FROM diary.booking b
WHERE b.club_id=:club_id AND b.booking_type='lesson'
  AND b.starts_at >= :from AND b.starts_at < :to
GROUP BY 1;
```

**Court occupancy** (slot-minutes booked vs available per court):
```sql
SELECT r.id AS court_id, r.name,
       SUM(EXTRACT(epoch FROM (b.ends_at-b.starts_at))/60.0)
         FILTER (WHERE b.status IN ('confirmed','completed')) AS booked_minutes
FROM diary.resource r
LEFT JOIN diary.booking b ON b.resource_id=r.id
       AND b.starts_at >= :from AND b.starts_at < :to
WHERE r.club_id=:club_id AND r.kind='court' AND r.is_active
GROUP BY 1,2;
```

**Memberships / MRR / PAYG mix** (1050's `vw_mrr` shape; CourtFlow membership price = `price.amount_minor`):
```sql
SELECT count(*) FILTER (WHERE ms.status='active') AS active_members,
       COALESCE(SUM(pr.amount_minor) FILTER (WHERE ms.status='active'),0) AS mrr_minor
FROM billing.membership_subscription ms
LEFT JOIN billing.price pr ON pr.id = ms.price_id
WHERE ms.club_id=:club_id;
-- PAYG vs membership court mix: count court orders by settlement_mode='membership_covered' vs paid.
```

### 3.3 Cockpit UI

A single **Cockpit** tab (replace the placeholder in `admin.js::renderCockpit`). Layout (cf-* + new KPI cards):

- **KPI strip** (`cf-grid-3` of new `cf-stat` cards): Net revenue (period) · Active members / MRR · Commission
  earned (owner) · Rent due · Court utilisation % · Lessons booked.
- **Revenue card** — by-month bars (lightweight inline SVG/CSS bars; no chart lib today — see §6) + a
  `cf-table` breakdown by service kind, `.num` cells.
- **Coaches card** — `cf-table`: Coach · Lessons · Gross · Commission (you keep) · Coach earns · Rent due · Net
  owed · Payout status (+ a **"Record payout"** action → `POST /api/admin/coach-payouts`). Sorted by commission
  (top coaches). Status via `cf-chip`.
- **Utilisation card** — court occupancy + coach utilisation tables/bars.
- **Trends** — month-over-month revenue + new/churned members.

Period filter (this month / last month / custom range) drives all cards (one `from/to` propagated to fetches).

### 3.4 Payout endpoints

```
GET    /api/admin/coach-payouts?coach_user_id=&status=     # list
POST   /api/admin/coach-payouts                            # body {coach_user_id, period_start, period_end}
        -> materialises rent ledger rows for the period (idempotent), snapshots earnings/rent/net, status='draft'
PATCH  /api/admin/coach-payouts/<id>                       # status -> approved/paid (+ method, paid_at);
        -> on 'paid' posts a coach_ledger 'payout' entry (negative) settling the balance
```

---

## 4. Gaps in existing owner self-service (beyond commission/cockpit)

Current owner self-service (§11.1) covers club profile, location, branding, hours, courts, services/pricing,
coaches/invite, people, membership grant, and the single online-payments toggle. **Backend `PATCH /policy`
already accepts the full policy set but there is no UI for most of it.** Gaps the owner should be able to edit:

| Gap | State | Recommendation |
|---|---|---|
| **Cancellation / no-show policy UI** | Cols + `PATCH /policy` exist (`cancellation_cutoff_hours`, `no_show_fee_minor`, `guest_requires_member`); **no UI**. | Surface in a **Policies** settings tab. Low effort (backend done). |
| **Booking rules UI** | `booking_window_days`, `min_booking_minutes`, `allow_pay_at_court`, `allow_monthly_account` — backend done, no UI. | Same Policies tab. |
| **Tax / VAT** | **MISSING entirely** (prices are `amount_minor` only; no tax line). | Add `club.tax` config (rate, inclusive flag, VAT number) + show on prices/statements. Affects commission base (§1.6). Phase 3. |
| **Payout details (bank account)** | **MISSING**. | Add per-coach payout method/details to `coach_agreement` (or a `coach_payout_account` table). Needed before real payouts. |
| **Staff roles / permissions** | Roles exist in `iam.membership.role` and gate APIs, but **no UI** to promote a member to `club_admin` or manage staff. | Add a "Staff" section in People to set role (member↔coach↔club_admin). Coordinate w/ auth lane. |
| **Multi-location** | Schema supports it (`club.location`, `resource.location_id`) but every helper uses the **primary** location only; no add/manage UI. | Out of v1 scope; note as later phase. Single-location today. |
| **Notifications config** | `club.branding.klaviyo_list_id` col exists; SES/Klaviyo env-gated globally; **no per-club UI** (which emails, reminder timing). | Phase 3 "Notifications" settings tab. Coordinate w/ CRM lane. |
| **Self-serve membership purchase** | Grant/revoke manual exists; member self-purchase is "next piece" (CLAUDE.md). | Out of this spec (separate membership-billing spec). |

The commission config (§2) + cockpit (§3) are the focus; the **Policies tab** (cancellation/no-show/booking
rules) is a high-value low-effort add bundled here because the backend already exists.

---

## 5. The shared-CRM question (recommendation)

**Recommendation: owner cockpit and coach cockpit share ONE analytics/CRM engine over `core.usage_event` +
`billing.*`, exposed as club-scoped, role-filtered views.** Reasons:

- The substrate already exists and is registered: `cockpit_bp` (`marketing_crm/backoffice/`) over `core.*`
  views, with `_admin_ctx()` club-scoping and `emit()`→`core.usage_event` event feed (booking/payment/membership
  events already emitted). 1050 proved the views-only pattern.
- The coach cockpit is a **filtered subset** of the owner cockpit: a coach sees *their own* utilisation,
  lessons, and earnings (`commission_split`/`coach_ledger` filtered to `coach_user_id`); the owner sees all
  coaches + revenue + rent. Same views, different `WHERE`/role gate. Building two engines duplicates the
  aggregation SQL and risks drift.
- **Coordinate:** the **coach spec** should consume the same `coach-earnings` / `coach-utilisation` views
  (read-only, self-scoped) rather than define its own; the **CRM spec** owns `core.usage_event` + the
  `cockpit_bp` blueprint. Agree ownership: **billing owns the `commission_*`/`coach_*` views, CRM owns the
  blueprint that serves them**, matching the lane boundaries in CLAUDE.md.

Caveat: keep **minor-PII out of usage_event payloads** (docs/06) — coach earnings reporting reads `billing.*`
directly, not the marketing event feed, so this is naturally respected.

---

## 6. UX notes

- **One design system.** Config screen and cockpit reuse `cf-card`, `cf-grid-2/3`, `cf-table` (+`.num` for
  money), `cf-field`/`cf-input`/`cf-select`, `cf-btn-*`, `cf-chip` (payout/agreement status), `cf-summary-row`
  (financial breakdowns), `cf-nav` tabs, `cf-empty`/`cf-loading`.
- **New CSS (small, additive):** a `cf-stat` KPI-card set (label, big number, sub/delta) — **none exists today**.
  Add `cf-stat`, `cf-stat-k`, `cf-stat-v`, `cf-stat-sub`, `cf-stat-up`/`cf-stat-down`. Keep it in `app.css`
  (single source — do not inline).
- **Charts:** no chart library is loaded (diary is hand-rolled). v1: render trend bars as **CSS/inline-SVG bars**
  from the monthly view data (cheap, on-brand). Defer evaluating a chart lib unless the owner wants richer viz
  (open question §10).
- **Money formatting:** all amounts are `*_minor` (cents) integers; format client-side with the club
  `currency_code`. Right-align in `.num` cells.
- **Effective-rate preview** in the config screen (live, like the booking wizard's price preview) is the key UX
  affordance — the owner must trust the precedence resolves the way they expect.

---

## 7. Build phasing (ordered)

**Phase 1 — Commission/rental config + split ledger (foundation; do first).**
- DDL: `coach_agreement`, `commission_rule`, `commission_split`, `coach_ledger` in `billing/schema.py`
  (idempotent; `python -m db` twice = no-op).
- `billing/commission.py`: `resolve_commission_pct`, `record_split`, `post_coach_earning`.
- Wire the fan-out into `apply_payment_event` (`charge_succeeded` + `refunded`), savepoint-guarded, idempotent.
- Config endpoints (`/api/admin/coach-agreements`, `/api/admin/commission-rules`, `/preview`) + the Settings
  "Coach payments" tab UI.
- **Done when:** a lesson payment writes correct owner/coach split rows + a coach-ledger earning; replay = no-op;
  resolution precedence verified against a scratch DB (coach_product > product > coach > club > 0).

**Phase 2 — Owner financial cockpit (reporting).**
- Implement the financial views + fill the `/cockpit/revenue` stub; add `/coach-earnings`,
  `/coach-utilisation`, `/occupancy`, `/memberships`, `/summary`.
- Replace `admin.js::renderCockpit` placeholder with the real cockpit (KPI strip + cards), add `cf-stat` CSS.
- Rent accrual (lazy-at-read first; `coach-rent-accrual` cron handler optional).
- Payout endpoints + "Record payout" action.
- **Done when:** cockpit shows revenue by service kind, per-coach commission/rent/net, utilisation, MRR; numbers
  reconcile against `billing.payment` (gross/refunds/net) and `SUM(commission_split)=gross` per line.

**Phase 3 — Self-service gaps.**
- Policies tab (cancellation/no-show/booking rules — backend exists; low effort).
- Tax/VAT config (affects commission base — revisit §1.6). Payout bank details. Staff roles UI.
- Notifications config; classes-commission (if deferred from P1). Multi-location later.

---

## 8. REUSE vs NEW map

| Piece | Reuse / New | Source |
|---|---|---|
| Idempotent payment record | **Reuse (exists)** | `billing.payment` + `apply_payment_event` (`billing/events.py`) |
| Settlement seam to hook splits | **Reuse (extend)** | `apply_payment_event` `charge_succeeded`/`refunded` |
| Resolution cascade pattern | **Reuse (pattern)** | `diary/pricing.py::price_for` exact→nearest→any |
| Signed-delta ledger shape | **Reuse (pattern, from 1050)** | `core.credit_ledger` (`webhook-server/core_db`) |
| Idempotency keys (period/event) | **Reuse (pattern, from 1050)** | `monthly_refill_log (account,year_month)`, `payment (provider,id)` |
| Views-only reporting + thin endpoints | **Reuse (pattern, from 1050)** | `marketing_crm/backoffice/views.py` + `blueprint.py` |
| Cockpit blueprint + admin gate + club-scope | **Reuse (exists)** | `cockpit_bp`, `_admin_ctx()` (`marketing_crm/backoffice/`) |
| Revenue/coach/occupancy views | **NEW** | `billing.*` (owned by Billing lane) |
| `commission_rule` / `coach_agreement` / `commission_split` / `coach_ledger` / `coach_payout` | **NEW** | `billing/schema.py` |
| `billing/commission.py` engine | **NEW** | — |
| Config endpoints + Settings tab | **NEW** | `admin/routes.py`, `frontend/js/settings.js` + `admin_api.js` |
| Cockpit UI | **NEW** (replaces placeholder) | `admin.js::renderCockpit` |
| `cf-stat` KPI card CSS | **NEW (small)** | `frontend/app/app.css` |
| Policies tab (cancellation/no-show/booking) | **NEW UI over existing backend** | `PATCH /api/admin/policy` already exists |
| Tax/VAT, payout bank details, staff-role UI, multi-location, notifications | **NEW** | per §4 |
| Rent accrual cron | **Reuse seam / NEW handler** | `crons/trigger.py` + `/api/cron/coach-rent-accrual` |
| `coach-rent-accrual` lazy fallback | **Reuse pattern** | `release_expired_holds` lazy-expiry |

---

## 9. Endpoint summary (new)

```
# Config (admin_bp, /api/admin)
GET    /coach-agreements
PUT    /coach-agreements/<coach_user_id>
POST   /commission-rules
DELETE /commission-rules/<rule_id>
GET    /commission-rules/preview?coach_user_id=&product_id=
GET    /coach-payouts?coach_user_id=&status=
POST   /coach-payouts
PATCH  /coach-payouts/<id>
# (existing PATCH /policy gains a Policies UI; no new route)

# Cockpit (cockpit_bp, /api/admin/cockpit) — fill stub + add
GET    /revenue?from=&to=
GET    /coach-earnings?from=&to=
GET    /coach-utilisation?from=&to=
GET    /occupancy?from=&to=
GET    /memberships
GET    /summary
```

---

## 10. Open questions for the owner

1. **Commission base: gross or net?** On the full lesson price (gross), or net of the Yoco processing fee? Net of
   VAT? (v1 default: gross, VAT-inclusive — §1.6.)
2. **Refund clawback.** If a lesson is refunded, does the coach's commission get clawed back proportionally?
   (Default: yes, negative split + negative coach-ledger entry; booking not reversed.)
3. **Rent vs commission interaction.** Is rent ever waived/offset against commission, or always additive? Does
   rent accrue even in a month with zero lessons? (Default: additive, accrues regardless.)
4. **Membership-covered lessons.** Lessons are never membership-covered today (guard is courts-only). Confirm
   coaches are never commissioned on a R0 lesson. (Membership-covered **courts** → no commission, by design.)
5. **Classes.** Commission group classes from day one, or lessons-only in v1?
6. **Payout mechanics.** Does CourtFlow *move* money to coaches (would need a payout rail / per-coach bank
   details), or only *report* what's owed and the owner settles offline / by netting rent? (v1 assumes
   report-and-record; offset/EFT/cash methods on the payout.)
7. **Who pays the gateway fee** on a commissioned lesson — owner or coach? (Affects whether to surface a fee
   line.)
8. **Mid-month rate changes.** Does a rate change apply to the whole month or only lessons after the change
   date? (Default: per-payment resolution at `paid_at` — only lessons after the change.)
9. **Tax/VAT.** Is the club VAT-registered? Are listed prices VAT-inclusive? (Drives §4 Tax config + the base.)
10. **Cockpit charts.** Is CSS-bar trend viz enough, or do you want a charting library?

---

## 11. Current-state map (research findings, condensed)

### 11.1 Owner self-service today (`admin/routes.py`, `frontend/js/settings.js`, `admin.js`)
Console tabs: **Master diary · Classes · Resources · People · Billing · Cockpit** (Cockpit = static placeholder).
Settings tabs: **Club profile · Hours · Courts · Services & pricing · Coaches · Payments** (Payments = single
`allow_online_payment` toggle). APIs: club/location/branding/policy patch, resources CRUD (soft-delete), hours
replace, products/prices CRUD, classes create+schedule, coaches invite/resend/revoke, people list, payments
list, membership grant/revoke, refund (via yoco lane). `admin/schema.py` owns only `club.onboarding_completed` +
`iam.coach_invite`.

### 11.2 Billing today (`billing/schema.py`, `events.py`, `orders.py`, `ledger.py`)
`product(kind, coach_user_id[added by coach lane])`, `price(product_id, audience, amount_minor, unit,
duration_minutes)`, `order(amount_minor, settlement_mode∈{online,at_court,monthly_account,membership_covered,
free}, status)`, `order_line(price_id, qty, amount_minor, booking_id, enrolment_id)`, `payment(provider,
provider_payment_id, amount_minor, direction∈{charge,refund}, status)` (idempotent on
`(provider,provider_payment_id)`), `account_ledger` (**single-entry, member-tab only**),
`membership_subscription`. `apply_payment_event` is the single idempotent settlement path (dedupe on
`event_hash`). **No commission/rent/payout/split/coach-earnings anywhere.** `account_ledger` has no club/coach
account; the only ledger write is the `monthly_account` charge at order creation.

### 11.3 Diary today (`bookings.py`, `pricing.py`, `schema.py`)
Coach = `diary.resource(kind='coach', coach_user_id)`; a lesson booking is `booking_type='lesson'` carrying
denormalised `coach_user_id`. **No `service_id`/`product_id` on `diary.booking`** — the product link is only via
`order_line.price_id → price → product`. A "lesson type" = `billing.product(kind='lesson')` (coach-scoped via
`product.coach_user_id`). `pricing.price_for` resolves exact→nearest≤→any (the cascade we mirror for commission).
`has_active_membership` makes courts (only) free.

### 11.4 1050 reference (`webhook-server`)
**No commission/payout/split engine** (coaches = acquisition channel by design). Reusable: `billing.payment`
record-only signed log + idempotency; `core.credit_ledger` signed single-table ledger; `vw_mrr`/
`vw_revenue_monthly`/`vw_subs_by_plan`/`vw_customer_360` reporting views consumed by thin passthrough endpoints
(`marketing_crm/backoffice/`); in-SQL pricing-map `VALUES` join; monthly-refill idempotency keyed on
`(account, year_month)`; webhook discipline (verify → re-fetch authoritative amount → record).

### 11.5 Cockpit scaffold (`marketing_crm/backoffice/`)
`cockpit_bp` registered at `/api/admin/cockpit/*`, admin-gated, club-scoped. LIVE: `signups`, `usage`
(usage_event aggregation), `consent`, `nps`. **STUBS (no SQL):** `revenue`, `occupancy`, `coach-utilisation`,
`attendance`. Frontend Cockpit tab is a **static `cf-empty` placeholder**. No KPI-card CSS, no chart lib.
`emit()`→`core.usage_event` taxonomy includes `BOOKING_CONFIRMED`, `PAYMENT_SUCCEEDED`,
`MEMBERSHIP_STARTED/LAPSED`, etc.

### 11.6 Tax / payout / roles / multi-location / notifications
**Tax/VAT: missing.** **Payout bank details: missing.** **Staff-role UI: missing** (roles exist in
`iam.membership.role`, gate APIs, but no assign UI). **Multi-location: schema present, primary-location-only in
practice.** **Notifications config: missing** (`klaviyo_list_id` col exists, no UI).

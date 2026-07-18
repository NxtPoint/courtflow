# billing/schema.py — idempotent boot DDL for the `billing.*` schema.
#
# Per docs/02 §5 + docs/05. This is bookings billing — its OWN schema (decision D1):
# we reuse 1050's idempotency DISCIPLINE (record-only payments unique on
# (provider, provider_payment_id); event-hash dedupe on payment_attempt) but NOT its
# credit-grant nouns. Raw idempotent SQL (CREATE TABLE/INDEX IF NOT EXISTS, ADD COLUMN
# IF NOT EXISTS) — same discipline as club.schema / iam.schema, no migration framework.
#
# Tables (docs/02 §5):
#   billing.product                 sellable thing (court_booking/lesson/class/membership/guest_booking)
#   billing.price                   price points (member/visitor/guest tiers), amount_minor in cents
#   billing.membership_subscription recurring memberships (R220/mo "unlimited courts")
#   billing.order                   one settlement unit (a booking, a class, a tab line)
#   billing.order_line              what an order is for (booking_id / enrolment_id)
#   billing.payment                 RECORD-ONLY money log; unique(provider, provider_payment_id)
#   billing.payment_attempt         gateway round-trips; event_hash unique (idempotency)
#   billing.account_ledger          the "pay end of month" tab (charge/payment/adjustment + balance)
#
# Every domain row carries club_id NOT NULL (multi-tenancy, decision D7). Never hard-delete:
# status-based (order.status void/written_off; payment.direction refund). UUID PKs via
# gen_random_uuid() (pgcrypto, enabled by db.run_boot_init()). Requires club.* (FKs to club.club).

from sqlalchemy import text

SCHEMA = "billing"

# Settlement modes (docs/05 §5) and order statuses (docs/02 §5) — enforced as CHECK constraints.
# 'token' = settled by drawing one prepaid session token (docs/specs/02): order paid, amount 0,
# booking confirmed — the count-based sibling of membership_covered/free.
SETTLEMENT_MODES = ("online", "at_court", "monthly_account", "membership_covered", "free", "token")
ORDER_STATUSES = ("open", "awaiting_payment", "paid", "void", "refunded", "written_off")

_DDL = [
    f"CREATE SCHEMA IF NOT EXISTS {SCHEMA};",

    # --- billing.product : a sellable thing -------------------------------
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.product (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id     uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        kind        text NOT NULL CHECK (kind IN
                        ('court_booking','lesson','class','membership','guest_booking')),
        name        text,
        description text,
        active      boolean NOT NULL DEFAULT true,
        created_at  timestamptz NOT NULL DEFAULT now(),
        updated_at  timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_product_club ON {SCHEMA}.product (club_id);",

    # --- billing.price : price points (member/visitor/guest tiers) ---------
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.price (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id         uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        product_id      uuid NOT NULL REFERENCES {SCHEMA}.product(id) ON DELETE CASCADE,
        audience        text NOT NULL DEFAULT 'any'
                            CHECK (audience IN ('member','visitor','guest','any')),
        amount_minor    int NOT NULL,                 -- cents (ZAR per NextPoint)
        currency_code   text NOT NULL,                -- = club currency
        unit            text NOT NULL DEFAULT 'per_booking'
                            CHECK (unit IN ('per_booking','per_hour','per_session','per_month')),
        duration_minutes int,                         -- for per_session/lessons
        active          boolean NOT NULL DEFAULT true, -- = (status='active'); kept in sync
        status          text NOT NULL DEFAULT 'active',-- active|dormant(hidden, kept)|retired
        -- Membership access window (membership plans only): a cheap tier can be time-boxed so it
        -- only covers courts during certain hours/days (else PAYG). NULL = unconstrained.
        access_days      text,                         -- ISO weekdays allowed, CSV '1'..'7' (Mon=1); NULL=all
        access_start_min int,                          -- minutes from midnight (>=); NULL=no earliest
        access_end_min   int,                          -- minutes from midnight (<, exclusive); NULL=no latest
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_price_club ON {SCHEMA}.price (club_id);",
    # Membership access-window columns on an EXISTING db (NULL = unconstrained). Idempotent.
    f"ALTER TABLE {SCHEMA}.price ADD COLUMN IF NOT EXISTS access_days text;",
    f"ALTER TABLE {SCHEMA}.price ADD COLUMN IF NOT EXISTS access_start_min int;",
    f"ALTER TABLE {SCHEMA}.price ADD COLUMN IF NOT EXISTS access_end_min int;",
    # Membership TIER name (Student/Family/…) — the grouping the plan wizard drills (tier → term),
    # distinct from `label`. NULL = ungrouped. Only meaningful on membership term plans. Idempotent.
    f"ALTER TABLE {SCHEMA}.price ADD COLUMN IF NOT EXISTS membership_tier text;",
    # PEAK price (court durations only): the amount charged instead of amount_minor when a court booking's
    # local start falls inside the club peak window (club.policy.peak_*). NULL = no peak uplift for this
    # duration (charged amount_minor at all times). Explicit amount so the customer sees a clean price.
    f"ALTER TABLE {SCHEMA}.price ADD COLUMN IF NOT EXISTS peak_amount_minor int;",
    # MEMBERSHIP entitlement caps (membership term plans only) — anti-abuse, enforced SILENTLY server-side
    # via diary/entitlement.py and mirrored in the availability picker. NULL = no cap. max_covered_minutes:
    # longest covered single booking; max_covered_per_day: covered bookings/day; max_courts_per_day: distinct
    # covered courts/day. A booking beyond any cap falls back to PAYG (never blocked).
    f"ALTER TABLE {SCHEMA}.price ADD COLUMN IF NOT EXISTS max_covered_minutes int;",
    f"ALTER TABLE {SCHEMA}.price ADD COLUMN IF NOT EXISTS max_covered_per_day int;",
    f"ALTER TABLE {SCHEMA}.price ADD COLUMN IF NOT EXISTS max_courts_per_day int;",
    # TRIAL config (membership term plans only): is_trial marks the tier granted on signup; trial_days scales
    # the free period (0 = trials off). grant_signup_trial links a new member to this tier's price_id so the
    # trial inherits every entitlement cap above. NULL/false = not the trial tier.
    f"ALTER TABLE {SCHEMA}.price ADD COLUMN IF NOT EXISTS trial_days int;",
    f"ALTER TABLE {SCHEMA}.price ADD COLUMN IF NOT EXISTS is_trial boolean NOT NULL DEFAULT false;",
    # Per-SERVICE payment preference: a CSV of allowed settlement modes this service offers
    # (subset of the club-enabled methods), e.g. 'online,at_court'. NULL = all club-enabled. The
    # single source of truth the unified service editor writes + the booking flow reads. Idempotent.
    f"ALTER TABLE {SCHEMA}.product ADD COLUMN IF NOT EXISTS payment_modes text;",
    # Court-SERVICE membership eligibility: a court product with members_covered=false is NEVER free for a
    # member (e.g. a clay court sold as a PAYG-only premium surface) — every booking of it is PAYG for all.
    # Only meaningful for kind='court_booking'. Default true = unchanged (a member's court is covered).
    f"ALTER TABLE {SCHEMA}.product ADD COLUMN IF NOT EXISTS members_covered boolean NOT NULL DEFAULT true;",
    # Semi-private / squad LESSONS: how many CLIENTS one lesson booking may hold (1 = private). When > 1
    # the booking flow lets a coach add up to this many members to ONE lesson (coach ∩ court, one slot),
    # and EACH client is invoiced their own order at the service's per-head price. Only meaningful for a
    # lesson product. Default 1 = unchanged (a private lesson).
    f"ALTER TABLE {SCHEMA}.product ADD COLUMN IF NOT EXISTS max_clients int NOT NULL DEFAULT 1;",
    # Widen product.kind to accept 'equipment' (ball machine / racquets / balls — a flat-fee booking add-on).
    # A plain CREATE TABLE IF NOT EXISTS never re-applies the inline CHECK, so migrate it on an existing db.
    # Idempotent: drop the auto-named CHECK if present, re-add the full set (a second boot = same end state).
    f"""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.constraint_column_usage
                   WHERE table_schema = '{SCHEMA}' AND table_name = 'product'
                     AND constraint_name = 'product_kind_check') THEN
            ALTER TABLE {SCHEMA}.product DROP CONSTRAINT product_kind_check;
        END IF;
        ALTER TABLE {SCHEMA}.product ADD CONSTRAINT product_kind_check
            CHECK (kind IN ('court_booking','lesson','class','membership','guest_booking','equipment'));
    END $$;
    """,
    # Unified statement: a child unpaid order, once cleared by a 'pay all' settlement order, points at
    # that settlement order. The settlement order pays the SUM of its children; on its charge_succeeded
    # we mark each child paid + fan out its consequence (commission split). NULL = a standalone order.
    f'ALTER TABLE {SCHEMA}.order ADD COLUMN IF NOT EXISTS settled_by_order_id uuid '
    f'REFERENCES {SCHEMA}."order"(id) ON DELETE SET NULL;',
    # Per-price payment preference — lets a SINGLE membership tier (one price row, or the rows of a
    # tier) carry its OWN payment options, since all membership tiers share one product. NULL =
    # inherit the product's payment_modes, then the club's global enabled methods. CSV of modes.
    f"ALTER TABLE {SCHEMA}.price ADD COLUMN IF NOT EXISTS payment_modes text;",
    # Service lifecycle (active | deactivated | terminated) — same 3-state model as memberships/coaches.
    # `active` boolean is kept in sync (active = status='active') so customer reads Just Work. Idempotent.
    f"ALTER TABLE {SCHEMA}.product ADD COLUMN IF NOT EXISTS status text;",
    f"UPDATE {SCHEMA}.product SET status = CASE WHEN active THEN 'active' ELSE 'deactivated' END "
    f"WHERE status IS NULL;",
    f"ALTER TABLE {SCHEMA}.product ALTER COLUMN status SET DEFAULT 'active';",
    f"CREATE INDEX IF NOT EXISTS ix_price_product ON {SCHEMA}.price (product_id);",
    # Lifecycle (3-state) on an EXISTING db: add status, backfill from the active boolean
    # (active->'active', inactive->'retired'; only WHERE status IS NULL so a later 'dormant'
    # is preserved across boots), then pin default/NOT NULL/CHECK. Idempotent.
    f"ALTER TABLE {SCHEMA}.price ADD COLUMN IF NOT EXISTS status text;",
    f"UPDATE {SCHEMA}.price SET status = CASE WHEN active THEN 'active' ELSE 'retired' END "
    f"WHERE status IS NULL;",
    f"ALTER TABLE {SCHEMA}.price ALTER COLUMN status SET DEFAULT 'active';",
    f"ALTER TABLE {SCHEMA}.price ALTER COLUMN status SET NOT NULL;",
    f"""
    DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'price_status_chk') THEN
            ALTER TABLE {SCHEMA}.price ADD CONSTRAINT price_status_chk
                CHECK (status IN ('active','dormant','retired'));
        END IF;
    END $$;
    """,

    # --- billing.membership_subscription : recurring memberships ----------
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.membership_subscription (
        id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id                 uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        user_id                 uuid,                 -- iam.user.id (kept unconstrained: cross-lane)
        price_id                uuid REFERENCES {SCHEMA}.price(id),
        status                  text NOT NULL DEFAULT 'active'
                                    CHECK (status IN ('active','cancelled','expired')),
        provider                text,                 -- 'yoco'|'paypal'|'manual'|null(at-desk)
        provider_subscription_id text,
        current_period_end      date,
        created_at              timestamptz NOT NULL DEFAULT now(),
        updated_at              timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_membership_sub_club ON {SCHEMA}.membership_subscription (club_id);",
    f"CREATE INDEX IF NOT EXISTS ix_membership_sub_user "
    f"ON {SCHEMA}.membership_subscription (club_id, user_id);",
    # One provider subscription id maps to one membership row (idempotent webhook upsert).
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_membership_sub_provider "
    f"ON {SCHEMA}.membership_subscription (provider, provider_subscription_id) "
    f"WHERE provider_subscription_id IS NOT NULL;",

    # --- billing.order : one settlement unit ------------------------------
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.order (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id         uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        user_id         uuid,                         -- iam.user.id (nullable: ad-hoc/guest)
        amount_minor    int NOT NULL DEFAULT 0,
        currency_code   text NOT NULL,
        settlement_mode text NOT NULL CHECK (settlement_mode IN
                            ('online','at_court','monthly_account','membership_covered','free','token')),
        status          text NOT NULL DEFAULT 'open' CHECK (status IN
                            ('open','awaiting_payment','paid','void','refunded','written_off')),
        due_date        date,                         -- for monthly_account
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_order_club ON {SCHEMA}.order (club_id);",
    f"CREATE INDEX IF NOT EXISTS ix_order_club_user ON {SCHEMA}.order (club_id, user_id);",
    f"CREATE INDEX IF NOT EXISTS ix_order_club_status ON {SCHEMA}.order (club_id, status);",

    # --- billing.order_line : what the order is for -----------------------
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.order_line (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        order_id     uuid NOT NULL REFERENCES {SCHEMA}.order(id) ON DELETE CASCADE,
        club_id      uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        description  text,
        price_id     uuid REFERENCES {SCHEMA}.price(id),
        qty          int NOT NULL DEFAULT 1,
        amount_minor int NOT NULL DEFAULT 0,
        booking_id   uuid,                            -- diary.booking.id (cross-lane: unconstrained)
        enrolment_id uuid,                            -- diary.enrolment.id (cross-lane: unconstrained)
        created_at   timestamptz NOT NULL DEFAULT now()
    );
    """,
    # The ORIGINAL charge before a coaching discount (so the by-service view can show "was → now"
    # while amount_minor holds the CURRENT/discounted figure the client actually owes). NULL = never discounted.
    f"ALTER TABLE {SCHEMA}.order_line ADD COLUMN IF NOT EXISTS original_amount_minor int;",
    f"CREATE INDEX IF NOT EXISTS ix_order_line_order ON {SCHEMA}.order_line (order_id);",
    f"CREATE INDEX IF NOT EXISTS ix_order_line_club ON {SCHEMA}.order_line (club_id);",
    f"CREATE INDEX IF NOT EXISTS ix_order_line_booking "
    f"ON {SCHEMA}.order_line (booking_id) WHERE booking_id IS NOT NULL;",

    # --- billing.payment : RECORD-ONLY money log (mirrors 1050) -----------
    # Idempotency: unique(provider, provider_payment_id) so a webhook retry / replay
    # never double-records a money movement. Refunds are direction='refund' (record-only;
    # NEVER auto-reverse a booking — docs/05 §8, the exact 1050 decision).
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.payment (
        id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id             uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        order_id            uuid REFERENCES {SCHEMA}.order(id) ON DELETE SET NULL,
        provider            text NOT NULL,            -- 'yoco'|'paypal'|'cash'|'card_at_desk'|'eft'
        provider_payment_id text,
        amount_minor        int NOT NULL DEFAULT 0,
        currency_code       text NOT NULL,
        direction           text NOT NULL DEFAULT 'charge'
                                CHECK (direction IN ('charge','refund')),
        status              text NOT NULL DEFAULT 'succeeded'
                                CHECK (status IN ('pending','succeeded','failed','refunded')),
        created_at          timestamptz NOT NULL DEFAULT now()
    );
    """,
    # Cash-audit: WHO recorded this money (the acting admin/coach at the desk/court), distinct from
    # order.user_id (the PAYER). Null for gateway (Yoco) charges — those have no human cashier. The
    # METHOD is already the `provider` column (cash|card_at_desk|eft). (A2 — desk-payment audit trail.)
    f"ALTER TABLE {SCHEMA}.payment ADD COLUMN IF NOT EXISTS recorded_by_user_id uuid;",
    f"CREATE INDEX IF NOT EXISTS ix_payment_club ON {SCHEMA}.payment (club_id);",
    f"CREATE INDEX IF NOT EXISTS ix_payment_order ON {SCHEMA}.payment (order_id);",
    # The idempotency guard (1050 pattern). Partial: desk payments may carry no provider id.
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_provider_id "
    f"ON {SCHEMA}.payment (provider, provider_payment_id) WHERE provider_payment_id IS NOT NULL;",

    # --- billing.payment_attempt : gateway round-trips (idempotency/audit) -
    # event_hash UNIQUE is THE webhook dedupe key (1050 subscription_event_log pattern):
    # apply_payment_event inserts the hash first and skips if it already exists.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.payment_attempt (
        id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id    uuid REFERENCES club.club(id) ON DELETE CASCADE,
        order_id   uuid REFERENCES {SCHEMA}.order(id) ON DELETE SET NULL,
        provider   text,
        intent_id  text,                              -- gateway checkout/charge id
        status     text,
        raw_event  jsonb,
        event_hash text UNIQUE,                       -- sha256 dedupe (the idempotency key)
        created_at timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_payment_attempt_order ON {SCHEMA}.payment_attempt (order_id);",

    # --- billing.account_ledger : RETIRED (the monthly "pay end of month" tab) ------------
    # No longer written or read: the unified statement (unpaid billing.order rows) is the single
    # debt of record. Table kept (not dropped — destructive) as a harmless empty orphan.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.account_ledger (
        id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id            uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        user_id            uuid,                       -- iam.user.id
        order_id           uuid REFERENCES {SCHEMA}.order(id) ON DELETE SET NULL,
        entry_type         text NOT NULL CHECK (entry_type IN ('charge','payment','adjustment')),
        amount_minor       int NOT NULL,               -- +charge / -payment (signed, see ledger.py)
        balance_after_minor int NOT NULL,
        note               text,
        created_at         timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_account_ledger_club ON {SCHEMA}.account_ledger (club_id);",
    f"CREATE INDEX IF NOT EXISTS ix_account_ledger_user "
    f"ON {SCHEMA}.account_ledger (club_id, user_id, created_at);",

    # --- self-serve membership purchase link (runs AFTER billing.order exists) ----
    # A membership bought online ties to its billing.order so the Yoco webhook can recognise
    # the paid order as a membership purchase and activate it idempotently (keyed off order_id).
    # NULL for admin-granted / future recurring rows. ADD COLUMN IF NOT EXISTS — safe on every boot.
    f"ALTER TABLE {SCHEMA}.membership_subscription "
    f'ADD COLUMN IF NOT EXISTS order_id uuid REFERENCES {SCHEMA}."order"(id) ON DELETE SET NULL;',
    f"CREATE INDEX IF NOT EXISTS ix_membership_sub_order "
    f"ON {SCHEMA}.membership_subscription (order_id) WHERE order_id IS NOT NULL;",

    # --- dated lifecycle: when a membership STARTED and when it was CANCELLED --------------------
    # `current_period_end` alone can't reconstruct "who was active on day X" across a month (a
    # cancellation lost its date). period_start + cancelled_at give an accurate active-members-per-day
    # curve going forward, powering the Overview 'Members' series. period_start carries a CURRENT_DATE
    # default so EVERY insert path auto-populates it (no need to touch each INSERT); existing rows are
    # backfilled from created_at. cancelled_at is event-driven — stamped by the cancel paths (COALESCE
    # so a re-cancel never moves the date) + a best-effort historical backfill from updated_at.
    # All idempotent: second `python -m db` = no-op (columns exist, defaults set, backfills match 0 rows).
    f"ALTER TABLE {SCHEMA}.membership_subscription ADD COLUMN IF NOT EXISTS period_start date;",
    f"ALTER TABLE {SCHEMA}.membership_subscription ADD COLUMN IF NOT EXISTS cancelled_at timestamptz;",
    f"ALTER TABLE {SCHEMA}.membership_subscription ALTER COLUMN period_start SET DEFAULT CURRENT_DATE;",
    f"UPDATE {SCHEMA}.membership_subscription SET period_start = created_at::date WHERE period_start IS NULL;",
    f"UPDATE {SCHEMA}.membership_subscription SET cancelled_at = updated_at "
    f"WHERE status = 'cancelled' AND cancelled_at IS NULL;",

    # --- configurable membership TERM PLANS (nothing-hardcoded) -----------------
    # A membership term plan = one billing.price row on the club's kind='membership' product:
    #   {price_id, label, amount_minor, term_months, active}. `term_months` is the membership
    #   duration this plan grants (NULL for non-term prices — per-duration court/lesson pricing
    #   leaves term_months NULL and is undisturbed). `label` is an optional display name
    #   ("3 months"); the UI falls back to deriving it from term_months. The owner CRUDs these
    #   in Settings; the member picks one at checkout and activation grants exactly term_months.
    #   ADD COLUMN IF NOT EXISTS — safe on every boot and twice in a row.
    f"ALTER TABLE {SCHEMA}.price ADD COLUMN IF NOT EXISTS term_months int;",
    f"ALTER TABLE {SCHEMA}.price ADD COLUMN IF NOT EXISTS label text;",

    # ===========================================================================
    # --- commission engine (owner) --- (Phase D, owner-self-service lane)
    #
    # SHARED-FILE PROTOCOL: this block is appended at the very END of billing's _DDL by
    # the commission/coaching-settlement (owner) lane. The Client-financials lane also
    # appends here — both keep their own clearly-marked `# ---  ... ---` block so a merge
    # preserves both. Idempotent CREATE TABLE/INDEX IF NOT EXISTS throughout (python -m db
    # twice = no-op). All tables carry club_id (multi-tenant). Money in *_minor cents,
    # ex-VAT net (docs/specs/01). See billing/commission.py for the engine logic.
    #
    #   coach_agreement  — per coach: optional rent + effective dates (the "is this coach
    #                      monetised, and what rent" record). Commission % lives in rules.
    #   commission_rule  — scoped (club|product|coach|coach_product), dated rate rows; the
    #                      resolution input (coach+product > product > coach > club).
    #   commission_split — per-payment-line decomposition (owner cut + coach net), signed,
    #                      idempotent on (payment_id, order_line_id, party_type).
    #   coach_ledger     — signed running account per coach (earnings +, rent -, payout -).
    #   coach_arrears    — an unpaid (off-platform) lesson on the coach's per-client tab;
    #                      coach marks it collected -> commission accrues (docs/specs/01).
    # ---------------------------------------------------------------------------

    # 1. coach_agreement — rent posture, one active row per coach.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.coach_agreement (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id         uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        coach_user_id   uuid NOT NULL,                  -- iam.user (coach)
        rent_minor      integer NOT NULL DEFAULT 0,     -- monthly rent the coach owes (cents, ex-VAT)
        rent_currency   text    NOT NULL DEFAULT 'ZAR',
        rent_day        integer NOT NULL DEFAULT 1
                          CHECK (rent_day BETWEEN 1 AND 28),
        status          text NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active','ended')),
        effective_from  date NOT NULL DEFAULT CURRENT_DATE,
        effective_to    date,
        notes           text,
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_coach_agreement_club "
    f"ON {SCHEMA}.coach_agreement (club_id, coach_user_id);",
    # one active, open-ended agreement per coach at a time:
    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_coach_agreement_active "
    f"ON {SCHEMA}.coach_agreement (club_id, coach_user_id) "
    f"WHERE status = 'active' AND effective_to IS NULL;",

    # 2. commission_rule — scoped, dated rate rows.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.commission_rule (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id         uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        scope           text NOT NULL
                          CHECK (scope IN ('club','product','coach','coach_product')),
        product_id      uuid,                            -- billing.product; null = any
        coach_user_id   uuid,                            -- null = any coach
        commission_pct  numeric(5,2) NOT NULL            -- % the CLUB keeps (0..100)
                          CHECK (commission_pct >= 0 AND commission_pct <= 100),
        effective_from  timestamptz NOT NULL DEFAULT now(),
        effective_to    timestamptz,
        active          boolean NOT NULL DEFAULT true,
        note            text,
        created_at      timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_commission_rule_resolve "
    f"ON {SCHEMA}.commission_rule (club_id, active, product_id, coach_user_id);",

    # 3. commission_split — per-payment-line decomposition (signed, record-only).
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.commission_split (
        id              bigserial PRIMARY KEY,
        club_id         uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        payment_id      uuid REFERENCES {SCHEMA}.payment(id) ON DELETE CASCADE,
        order_line_id   uuid,                            -- billing.order_line
        booking_id      uuid,                            -- diary.booking
        coach_user_id   uuid,
        product_id      uuid,
        rule_id         uuid REFERENCES {SCHEMA}.commission_rule(id),
        party_type      text NOT NULL
                          CHECK (party_type IN ('owner','coach')),
        basis           text NOT NULL
                          CHECK (basis IN ('lesson_commission','class_commission',
                                           'arrears_commission','refund_clawback')),
        gross_minor     integer NOT NULL,                -- ex-VAT line gross used as the base
        commission_pct  numeric(5,2),                    -- snapshot of the resolved rate
        amount_minor    integer NOT NULL,                -- SIGNED: owner cut / coach net
        currency        text NOT NULL DEFAULT 'ZAR',
        occurred_at     timestamptz NOT NULL DEFAULT now(),
        created_at      timestamptz NOT NULL DEFAULT now()
    );
    """,
    # THE idempotency guard for the on-collection accrual: one (owner,coach) pair per
    # payment line. NULLS NOT DISTINCT so arrears splits (payment_id NULL) dedupe on
    # (order_line_id, party_type) too. A re-delivered webhook re-enters the fan-out and
    # ON CONFLICT DO NOTHING makes it a strict no-op.
    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_commission_split "
    f"ON {SCHEMA}.commission_split (payment_id, order_line_id, party_type) NULLS NOT DISTINCT;",
    f"CREATE INDEX IF NOT EXISTS ix_commission_split_coach "
    f"ON {SCHEMA}.commission_split (club_id, coach_user_id, occurred_at);",

    # 4. coach_ledger — signed running account per coach.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.coach_ledger (
        id              bigserial PRIMARY KEY,
        club_id         uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        coach_user_id   uuid NOT NULL,
        entry_type      text NOT NULL
                          CHECK (entry_type IN ('commission_earning','rent_charge',
                                                'payout','adjustment')),
        amount_minor    integer NOT NULL,                -- SIGNED: + owed TO coach, - owed BY coach
        currency        text NOT NULL DEFAULT 'ZAR',
        ref_type        text,                            -- 'split' | 'rent_period' | 'payout'
        ref_id          text,                            -- split.id / 'YYYY-MM' / payout.id
        note            text,
        occurred_at     timestamptz NOT NULL DEFAULT now(),
        created_at      timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_coach_ledger "
    f"ON {SCHEMA}.coach_ledger (club_id, coach_user_id, occurred_at);",
    # idempotency for accrual entries via a deterministic ref (period rent / split id):
    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_coach_ledger_rent "
    f"ON {SCHEMA}.coach_ledger (club_id, coach_user_id, ref_id) "
    f"WHERE entry_type = 'rent_charge';",
    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_coach_ledger_earning "
    f"ON {SCHEMA}.coach_ledger (club_id, coach_user_id, ref_id) "
    f"WHERE entry_type = 'commission_earning';",

    # 5. coach_arrears — an unpaid (off-platform) lesson on the coach's per-client tab.
    # Created lazily from confirmed-but-unpaid lesson bookings; the coach marks it
    # collected (off-platform EFT) -> commission accrues (docs/specs/01). Idempotent on
    # the source booking so the lazy upsert never double-posts.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.coach_arrears (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id         uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        coach_user_id   uuid NOT NULL,
        client_user_id  uuid,
        booking_id      uuid,                            -- diary.booking (the source lesson)
        order_line_id   uuid,                            -- billing.order_line (commission base)
        product_id      uuid,
        gross_minor     integer NOT NULL DEFAULT 0,      -- ex-VAT owed
        currency        text NOT NULL DEFAULT 'ZAR',
        status          text NOT NULL DEFAULT 'owed'
                          CHECK (status IN ('owed','collected','written_off')),
        collected_at    timestamptz,
        collected_by    uuid,
        note            text,
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_coach_arrears_coach "
    f"ON {SCHEMA}.coach_arrears (club_id, coach_user_id, status);",
    # one arrears row per source booking (the lazy upsert key):
    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_coach_arrears_booking "
    f"ON {SCHEMA}.coach_arrears (club_id, booking_id) WHERE booking_id IS NOT NULL;",
    # The online statement-payment order that's settling this arrears (set when the client pays
    # their month-end statement online; on charge_succeeded the arrears is marked collected). Idempotent.
    f"ALTER TABLE {SCHEMA}.coach_arrears ADD COLUMN IF NOT EXISTS pay_order_id uuid;",
    # A CLASS enrolment has NO diary.booking (it keys off diary.enrolment) — so an OWED class
    # enrolment can't dedupe on booking_id. Track its source enrolment here and dedupe on it, the
    # exact mirror of ux_coach_arrears_booking for lessons. Additive + partial (existing lesson rows
    # keep enrolment_id NULL → not indexed → no conflict). Idempotent (python -m db twice = no-op).
    f"ALTER TABLE {SCHEMA}.coach_arrears ADD COLUMN IF NOT EXISTS enrolment_id uuid;",
    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_coach_arrears_enrolment "
    f"ON {SCHEMA}.coach_arrears (club_id, enrolment_id) WHERE enrolment_id IS NOT NULL;",

    # 6. coach_payout — a recorded club<->coach SETTLEMENT (the missing half of the loop). The cockpit
    # REPORTS the running coach_ledger balance; a payout is how it gets paid DOWN. Recording a 'paid'
    # payout posts ONE append-only coach_ledger 'payout' entry (idempotent on ref_id=payout.id via
    # ux_coach_ledger_payout below), signed to net the balance toward zero. Both directions:
    # club_to_coach (club pays the coach) / coach_to_club (coach settles commission+rent) / offset (net).
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.coach_payout (
        id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id            uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        coach_user_id      uuid NOT NULL,
        direction          text NOT NULL
                             CHECK (direction IN ('club_to_coach','coach_to_club','offset')),
        amount_minor       integer NOT NULL,             -- POSITIVE magnitude; ledger sign is derived
        currency           text NOT NULL DEFAULT 'ZAR',
        method             text NOT NULL DEFAULT 'eft'
                             CHECK (method IN ('eft','cash','offset')),
        reference          text,                         -- EFT reference / note the admin captured
        period_label       text,                         -- 'YYYY-MM' this settlement covers (optional)
        status             text NOT NULL DEFAULT 'paid'
                             CHECK (status IN ('draft','paid','void')),
        note               text,
        created_by_user_id uuid,
        created_at         timestamptz NOT NULL DEFAULT now(),
        paid_at            timestamptz
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_coach_payout_coach "
    f"ON {SCHEMA}.coach_payout (club_id, coach_user_id, created_at);",
    # Payout ledger idempotency (mirrors the rent/earning guards): one 'payout' entry per payout row.
    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_coach_ledger_payout "
    f"ON {SCHEMA}.coach_ledger (club_id, coach_user_id, ref_id) WHERE entry_type = 'payout';",

    # 7. month_end_notice — idempotency marker for the month-end statement sweep: ONE 'statement_ready'
    # notice per (club, user, period) so a re-run of the sweep never re-notifies a client. (C3.)
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.month_end_notice (
        club_id      uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        user_id      uuid NOT NULL,
        period_label text NOT NULL,
        owed_minor   integer NOT NULL DEFAULT 0,
        sent_at      timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (club_id, user_id, period_label)
    );
    """,
    # --- end commission engine (owner) ---

    # ===========================================================================
    # --- refund_request (client self-service) ---  (Phase B, client-financials lane)
    #
    # SHARED-FILE PROTOCOL: this block is appended at the very END of billing's _DDL,
    # AFTER the commission engine block, by the Client-financials (My Account) lane. It
    # touches nothing above. Idempotent CREATE TABLE/INDEX IF NOT EXISTS throughout
    # (python -m db twice = no-op). All rows carry club_id (multi-tenant). Money in
    # *_minor cents.
    #
    # A client raises a refund REQUEST against one of THEIR paid orders; an admin later
    # approves/declines it. This is DISTINCT from the admin's direct Yoco refund
    # (yoco_billing — a record-only money movement, docs/05 §8). The request is a
    # lightweight approval object: the member never moves money; on approval the admin
    # still executes the actual refund through the existing gateway path. The booking is
    # never auto-reversed.
    #
    # State machine (crm-and-foundations-spec §5.2):
    #   pending --approve--> approved --(admin runs the real refund)--> refunded (terminal)
    #      |                    |
    #      |--decline--> declined (terminal, with note)
    #      |--cancel---> cancelled (member withdrew before a decision; terminal)
    # ---------------------------------------------------------------------------
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.refund_request (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id       uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        order_id      uuid NOT NULL REFERENCES {SCHEMA}."order"(id) ON DELETE CASCADE,
        user_id       uuid,                          -- requester (iam.user.id)
        amount_minor  int,                           -- requested amount (default: full paid order)
        reason        text,
        status        text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','approved','declined','refunded','cancelled')),
        decided_by    uuid,                          -- admin iam.user.id
        decided_at    timestamptz,
        note          text,                          -- admin decision note
        created_at    timestamptz NOT NULL DEFAULT now(),
        updated_at    timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_refund_request_club_status "
    f"ON {SCHEMA}.refund_request (club_id, status);",
    f"CREATE INDEX IF NOT EXISTS ix_refund_request_order "
    f"ON {SCHEMA}.refund_request (order_id);",
    f"CREATE INDEX IF NOT EXISTS ix_refund_request_user "
    f"ON {SCHEMA}.refund_request (club_id, user_id);",
    # Dispute routing: the coaching service's coach owns coaching disputes (coach decides, club
    # oversees). NULL = a non-coaching dispute (court/membership) — the club decides. Idempotent add.
    f"ALTER TABLE {SCHEMA}.refund_request ADD COLUMN IF NOT EXISTS coach_user_id uuid;",
    f"CREATE INDEX IF NOT EXISTS ix_refund_request_coach "
    f"ON {SCHEMA}.refund_request (club_id, coach_user_id, status) WHERE coach_user_id IS NOT NULL;",
    # At most ONE open (pending) request per order — the member can't spam duplicates.
    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_refund_request_open "
    f"ON {SCHEMA}.refund_request (order_id) WHERE status = 'pending';",
    # --- end refund_request (client self-service) ---

    # ===========================================================================
    # --- token / bundle engine (prepaid session packs) --- (docs/specs/02)
    #
    # SHARED-FILE PROTOCOL: appended at the very END of billing's _DDL, after the
    # refund_request block. Touches nothing above. Idempotent CREATE/ALTER ... IF NOT EXISTS
    # throughout (python -m db twice = no-op). All rows carry club_id (multi-tenant). Money in
    # *_minor cents.
    #
    # A GENERIC, owner-configurable prepaid-pack capability: a member buys N prepaid sessions
    # (tokens); a matching booking DRAWS one (settling at R0, settlement_mode='token'); a
    # cancel CREDITS one back. The count-based sibling of PAYG (per-use) + membership (time).
    # Works for court / lesson / class. Nothing is hardcoded — a pack is a bundle_plan row.
    #
    #   bundle_plan   — the owner offer (service_kind, optional coach, label, sessions_count,
    #                   optional duration, price, optional validity). NOTHING hardcoded.
    #   token_wallet  — a member's purchased pack (denormalised kind/coach/duration for fast
    #                   matching; tokens_total/remaining; status pending|active|exhausted|expired;
    #                   order_id links the Yoco purchase so the webhook activates idempotently).
    #   token_ledger  — audit + idempotency. UNIQUE (wallet_id, booking_id, kind) NULLS NOT
    #                   DISTINCT so a draw AND a credit-back are each recorded at most once per
    #                   booking (idempotent re-runs); the balance only moves when the row inserts.
    # ---------------------------------------------------------------------------

    # 1. bundle_plan — the configurable offer.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.bundle_plan (
        id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id          uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        service_kind     text NOT NULL CHECK (service_kind IN ('court','lesson','class')),
        coach_user_id    uuid,                          -- lesson packs may be coach-specific; NULL = any
        label            text,
        sessions_count   int  NOT NULL CHECK (sessions_count > 0),
        duration_minutes int,                           -- per-token session length; NULL = any
        price_minor      int  NOT NULL CHECK (price_minor >= 0),
        currency_code    text NOT NULL DEFAULT 'ZAR',
        validity_days    int,                            -- NULL = no expiry
        active           boolean NOT NULL DEFAULT true,   -- = (status='active'); kept in sync
        status           text NOT NULL DEFAULT 'active',  -- active|dormant(hidden, kept)|retired
        created_at       timestamptz NOT NULL DEFAULT now(),
        updated_at       timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_bundle_plan_club "
    f"ON {SCHEMA}.bundle_plan (club_id, service_kind, active);",
    # Lifecycle (3-state) on an EXISTING db — mirror of billing.price above. Idempotent.
    f"ALTER TABLE {SCHEMA}.bundle_plan ADD COLUMN IF NOT EXISTS status text;",
    f"UPDATE {SCHEMA}.bundle_plan SET status = CASE WHEN active THEN 'active' ELSE 'retired' END "
    f"WHERE status IS NULL;",
    f"ALTER TABLE {SCHEMA}.bundle_plan ALTER COLUMN status SET DEFAULT 'active';",
    f"ALTER TABLE {SCHEMA}.bundle_plan ALTER COLUMN status SET NOT NULL;",
    f"""
    DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'bundle_plan_status_chk') THEN
            ALTER TABLE {SCHEMA}.bundle_plan ADD CONSTRAINT bundle_plan_status_chk
                CHECK (status IN ('active','dormant','retired'));
        END IF;
    END $$;
    """,
    # PER-SERVICE packs: tie a pack to its SPECIFIC billing.product (Private vs Semi-private, Clay vs
    # Hardcourt) so it only draws for THAT service — not any service of the same kind+coach. NULL =
    # a legacy unscoped pack (matches by kind+coach, exactly as before, until a backfill sets it).
    # Additive + idempotent. service_kind/coach_user_id stay as the denormalised (product-derived) copy.
    f"ALTER TABLE {SCHEMA}.bundle_plan ADD COLUMN IF NOT EXISTS product_id uuid;",
    f"CREATE INDEX IF NOT EXISTS ix_bundle_plan_product "
    f"ON {SCHEMA}.bundle_plan (club_id, product_id);",

    # 2. token_wallet — a member's purchased pack (denormalised for matching).
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.token_wallet (
        id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id          uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        user_id          uuid,                          -- iam.user.id (owner)
        bundle_plan_id   uuid REFERENCES {SCHEMA}.bundle_plan(id) ON DELETE SET NULL,
        order_id         uuid REFERENCES {SCHEMA}."order"(id) ON DELETE SET NULL,
        service_kind     text NOT NULL CHECK (service_kind IN ('court','lesson','class')),
        coach_user_id    uuid,                          -- denormalised (NULL = any)
        duration_minutes int,                           -- denormalised (NULL = any)
        base_minutes     int,                           -- the pack's UNIT length (the divisor); NULL=legacy/count
        tokens_total     int  NOT NULL DEFAULT 0,        -- nominal session count (display "of N")
        tokens_remaining int  NOT NULL DEFAULT 0 CHECK (tokens_remaining >= 0),  -- legacy/display (ceil of sessions)
        -- AUTHORITATIVE balance, held in MINUTES so a pack covers any duration (90min off a 60-unit = 1.5).
        minutes_total    int  NOT NULL DEFAULT 0,
        minutes_remaining int NOT NULL DEFAULT 0 CHECK (minutes_remaining >= 0),
        status           text NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','active','exhausted','expired')),
        purchased_at     timestamptz,
        expires_at       date,                           -- NULL = no expiry
        created_at       timestamptz NOT NULL DEFAULT now(),
        updated_at       timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_token_wallet_club "
    f"ON {SCHEMA}.token_wallet (club_id);",
    # The hot path: match_wallet scans a member's active wallets for a service_kind.
    f"CREATE INDEX IF NOT EXISTS ix_token_wallet_match "
    f"ON {SCHEMA}.token_wallet (club_id, user_id, service_kind, status);",
    f"CREATE INDEX IF NOT EXISTS ix_token_wallet_order "
    f"ON {SCHEMA}.token_wallet (order_id) WHERE order_id IS NOT NULL;",

    # Migrate an EXISTING db to the unit (minute-balance) model (docs/specs/02). A token used to be a
    # count; it is now MINUTES, so a pack covers any duration proportionally. Add the columns, then
    # backfill: base_minutes = the pack's unit length (its duration_minutes, default 60), and convert
    # the existing integer token balance to minutes (tokens * base). Idempotent (IF NOT EXISTS + only
    # backfill rows not yet migrated). The legacy tokens_* columns stay for display/back-compat.
    f"ALTER TABLE {SCHEMA}.token_wallet ADD COLUMN IF NOT EXISTS base_minutes int;",
    f"ALTER TABLE {SCHEMA}.token_wallet ADD COLUMN IF NOT EXISTS minutes_total int NOT NULL DEFAULT 0;",
    f"ALTER TABLE {SCHEMA}.token_wallet ADD COLUMN IF NOT EXISTS minutes_remaining int NOT NULL DEFAULT 0;",
    f"""
    DO $$
    BEGIN
        UPDATE {SCHEMA}.token_wallet
        SET base_minutes      = COALESCE(base_minutes, duration_minutes, 60),
            minutes_total     = tokens_total     * COALESCE(base_minutes, duration_minutes, 60),
            minutes_remaining = tokens_remaining * COALESCE(base_minutes, duration_minutes, 60)
        WHERE base_minutes IS NULL;
    END $$;
    """,

    # PER-SERVICE packs: the wallet inherits its plan's product_id, so a Private-lesson pack only
    # draws for Private lessons (NULL = a legacy pack, matches by kind+coach). Additive + idempotent.
    f"ALTER TABLE {SCHEMA}.token_wallet ADD COLUMN IF NOT EXISTS product_id uuid;",
    f"CREATE INDEX IF NOT EXISTS ix_token_wallet_product "
    f"ON {SCHEMA}.token_wallet (club_id, product_id);",

    # 3. token_ledger — audit + THE idempotency guard.
    # kind: draw/credit/grant/expire are SYSTEM movements (idempotent — at most one per wallet+booking);
    # 'adjust' is a MANUAL admin balance change (money-adjacent, deliberately REPEATABLE — see the
    # partial unique index below). reason + actor_user_id carry the audit trail for manual actions.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.token_ledger (
        id            bigserial PRIMARY KEY,
        club_id       uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        wallet_id     uuid NOT NULL REFERENCES {SCHEMA}.token_wallet(id) ON DELETE CASCADE,
        booking_id    uuid,                              -- diary.booking / enrolment (cross-lane)
        kind          text NOT NULL CHECK (kind IN ('draw','credit','grant','expire','adjust')),
        delta         int  NOT NULL,                     -- signed: draw -N, credit +N, grant +N, adjust +/-N
        reason        text,
        actor_user_id uuid,                              -- admin who made a manual 'adjust'/'expire' (NULL = system)
        created_at    timestamptz NOT NULL DEFAULT now()
    );
    """,
    # Audit columns on an EXISTING db (reason predates this; actor_user_id is new). Additive, idempotent.
    f"ALTER TABLE {SCHEMA}.token_ledger ADD COLUMN IF NOT EXISTS reason text;",
    f"ALTER TABLE {SCHEMA}.token_ledger ADD COLUMN IF NOT EXISTS actor_user_id uuid;",
    # Allow the manual 'adjust' kind on an EXISTING db (a plain CREATE TABLE IF NOT EXISTS never
    # re-applies the inline CHECK above). Drop the auto-named CHECK if present, then add the full set.
    # Idempotent: a second boot drops the 5-value CHECK and re-adds the identical one → same end state.
    f"""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.constraint_column_usage
                   WHERE table_schema = '{SCHEMA}' AND table_name = 'token_ledger'
                     AND constraint_name = 'token_ledger_kind_check') THEN
            ALTER TABLE {SCHEMA}.token_ledger DROP CONSTRAINT token_ledger_kind_check;
        END IF;
        ALTER TABLE {SCHEMA}.token_ledger ADD CONSTRAINT token_ledger_kind_check
            CHECK (kind IN ('draw','credit','grant','expire','adjust'));
    END $$;
    """,
    f"CREATE INDEX IF NOT EXISTS ix_token_ledger_wallet "
    f"ON {SCHEMA}.token_ledger (wallet_id, created_at);",
    f"CREATE INDEX IF NOT EXISTS ix_token_ledger_booking "
    f"ON {SCHEMA}.token_ledger (booking_id) WHERE booking_id IS NOT NULL;",
    # THE idempotency guard: a draw and a credit-back are each recorded at most ONCE per
    # (wallet, booking). NULLS NOT DISTINCT so grant/expire rows (booking_id NULL) also dedupe
    # — at most one grant / one auto-expire per wallet. The balance only moves when the row inserts.
    # PARTIAL on `kind <> 'adjust'`: a MANUAL admin adjustment (booking_id NULL, kind 'adjust') must be
    # allowed REPEATEDLY, so those rows sit OUTSIDE this guard; draw/credit/grant/expire keep their
    # exact per-(wallet,booking,kind) idempotency. Recreated only when not already partial so a second
    # boot is a true no-op. NB: every ON CONFLICT (wallet_id,booking_id,kind) in bundles.py MUST carry
    # the matching `WHERE kind <> 'adjust'` predicate so Postgres can infer this partial arbiter index.
    f"""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_indexes
            WHERE schemaname = '{SCHEMA}' AND indexname = 'ux_token_ledger_once'
              AND indexdef ILIKE '%kind <> ''adjust''%'
        ) THEN
            DROP INDEX IF EXISTS {SCHEMA}.ux_token_ledger_once;
            CREATE UNIQUE INDEX ux_token_ledger_once
                ON {SCHEMA}.token_ledger (wallet_id, booking_id, kind) NULLS NOT DISTINCT
                WHERE kind <> 'adjust';
        END IF;
    END $$;
    """,

    # Migrate the order settlement_mode CHECK on an EXISTING db so 'token' is accepted (a plain
    # CREATE TABLE IF NOT EXISTS never re-applies the inline CHECK above). Idempotent: drop the
    # old constraint if present, then add the full set. DO block keeps it a no-op when already current.
    f"""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.constraint_column_usage
                   WHERE table_schema = '{SCHEMA}' AND table_name = 'order'
                     AND constraint_name = 'order_settlement_mode_check') THEN
            ALTER TABLE {SCHEMA}."order" DROP CONSTRAINT order_settlement_mode_check;
        END IF;
        ALTER TABLE {SCHEMA}."order" ADD CONSTRAINT order_settlement_mode_check
            CHECK (settlement_mode IN
                ('online','at_court','monthly_account','membership_covered','free','token'));
    END $$;
    """,
    # --- end token / bundle engine ---

    # ===========================================================================
    # --- invoice DOCUMENTS (billing/invoicing.py) ---
    #
    # SHARED-FILE PROTOCOL: appended at the very END of billing's _DDL. Touches nothing
    # above. Idempotent CREATE/ALTER ... IF NOT EXISTS throughout (python -m db twice = no-op).
    #
    # THE INVARIANT THIS PRESERVES: an invoice is a *document that RENDERS over live orders*,
    # NEVER a second debt store. The debt lives on billing."order" and is settled exactly
    # once (a client can still pay any open order online in real time — issuing an invoice
    # does NOT change an order). An invoice's LINE AMOUNTS are frozen at issue (an immutable
    # legal document + seller/bill-to snapshot), but its PAID / OUTSTANDING status is DERIVED
    # LIVE from the orders its lines reference — so a mid-month card payment simply flips the
    # invoice to Paid, and double-counting is structurally impossible (one debt store: orders).
    #
    #   invoice      — one issued document. Gapless per-club number (club.billing_profile seq).
    #                  status is DOCUMENT lifecycle only (issued|void); paid-ness is derived.
    #   invoice_line — one row per covered order (statement invoice) or per billed item
    #                  (ad-hoc invoice). Carries order_id so paid/outstanding derives live.
    # ---------------------------------------------------------------------------
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.invoice (
        id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id            uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        invoice_number     text NOT NULL,                  -- gapless per-club (prefix + seq)
        user_id            uuid,                            -- bill-to = the PAYER (iam.user.id)
        kind               text NOT NULL DEFAULT 'adhoc'
                             CHECK (kind IN ('adhoc','statement')),  -- adhoc=own lines; statement=covers open orders
        status             text NOT NULL DEFAULT 'issued'
                             CHECK (status IN ('issued','void')),    -- DOCUMENT lifecycle only (paid-ness derived)
        currency_code      text NOT NULL,
        total_minor        int  NOT NULL DEFAULT 0,          -- frozen sum at issue (snapshot)
        issued_at          timestamptz NOT NULL DEFAULT now(),
        due_date           date,
        period_label       text,                            -- 'YYYY-MM' for a month-end statement invoice
        bill_to            jsonb,                            -- {{name,email,phone,address}} snapshot at issue
        seller             jsonb,                            -- {{registered_name,reg_no,vat_number,bank,address,logo_url}} snapshot
        notes              text,                             -- footer / terms / custom note snapshot
        created_by_user_id uuid,                             -- who issued it
        created_at         timestamptz NOT NULL DEFAULT now()
    );
    """,
    # Gapless per-club numbering must be unique.
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_number "
    f"ON {SCHEMA}.invoice (club_id, invoice_number);",
    f"CREATE INDEX IF NOT EXISTS ix_invoice_club_user ON {SCHEMA}.invoice (club_id, user_id);",
    f"CREATE INDEX IF NOT EXISTS ix_invoice_club_status ON {SCHEMA}.invoice (club_id, status);",
    f"CREATE INDEX IF NOT EXISTS ix_invoice_club_period "
    f"ON {SCHEMA}.invoice (club_id, period_label) WHERE period_label IS NOT NULL;",

    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.invoice_line (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        invoice_id   uuid NOT NULL REFERENCES {SCHEMA}.invoice(id) ON DELETE CASCADE,
        club_id      uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        order_id     uuid,                                  -- the order this line RENDERS over (paid-status source)
        description  text,
        qty          int  NOT NULL DEFAULT 1,
        amount_minor int  NOT NULL DEFAULT 0,               -- frozen snapshot
        created_at   timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_invoice_line_invoice ON {SCHEMA}.invoice_line (invoice_id);",
    # Find every active invoice that already covers a given order (so month-end never
    # re-invoices a debt already on a live invoice — one active invoice per open order).
    f"CREATE INDEX IF NOT EXISTS ix_invoice_line_order "
    f"ON {SCHEMA}.invoice_line (order_id) WHERE order_id IS NOT NULL;",
    # --- end invoice documents ---

    # ===========================================================================
    # --- PROMOTIONS (billing/promotions.py) ---
    #
    # SHARED-FILE PROTOCOL: appended at the very END of billing's _DDL. Touches nothing above.
    # Idempotent CREATE/ALTER ... IF NOT EXISTS throughout (python -m db twice = no-op).
    #
    # A promotion is an OFFER + a redeemable CODE (a "special"). Redeeming it at checkout just
    # DISCOUNTS the order via billing.statement.discount_order — it NEVER invents a second debt
    # store (one debt = one order stays true). See docs/specs/PROMOTIONS-ENGINE.md.
    #   promotion            — the offer + its rules (kind/value/scope/caps/window/code)
    #   promotion_redemption — the usage ledger (drives caps + reporting; reversed on refund/void)
    # ---------------------------------------------------------------------------
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.promotion (
        id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id           uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        code              text,                           -- redeem code (unique per club, case-insensitive); NULL = automatic
        name              text NOT NULL,                  -- admin label ("January Membership 20%")
        description       text,
        kind              text NOT NULL DEFAULT 'percent_off'
                            CHECK (kind IN ('percent_off','amount_off')),   -- Phase 2 adds bonus_period/bonus_units
        percent_bps       int,                            -- percent_off: basis points (2000 = 20%)
        value_minor       int,                            -- amount_off: cents
        applies_to        text NOT NULL DEFAULT 'all'
                            CHECK (applies_to IN ('all','membership','pack','court','lesson','class','product')),
        product_id        uuid REFERENCES {SCHEMA}.product(id) ON DELETE CASCADE,   -- when applies_to='product'
        min_spend_minor   int,                            -- eligibility floor (NULL = none)
        first_time_only   boolean NOT NULL DEFAULT false, -- customer's FIRST purchase of this scope only
        max_redemptions   int,                            -- global cap (NULL = unlimited)
        per_customer_cap  int NOT NULL DEFAULT 1,         -- redemptions per customer
        stackable         boolean NOT NULL DEFAULT false, -- may combine with another promo / an admin discount
        starts_at         timestamptz,
        ends_at           timestamptz,
        status            text NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active','paused','archived')),
        created_by        uuid,
        created_at        timestamptz NOT NULL DEFAULT now(),
        updated_at        timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_promotion_club ON {SCHEMA}.promotion (club_id, status);",
    # Codes unique per club, case-insensitive, among live (non-archived) promos — an archived code frees up.
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_promotion_code "
    f"ON {SCHEMA}.promotion (club_id, lower(code)) WHERE code IS NOT NULL AND status <> 'archived';",

    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.promotion_redemption (
        id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id        uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        promotion_id   uuid NOT NULL REFERENCES {SCHEMA}.promotion(id) ON DELETE CASCADE,
        order_id       uuid NOT NULL REFERENCES {SCHEMA}."order"(id) ON DELETE CASCADE,
        user_id        uuid,                              -- who redeemed (iam.user.id)
        discount_minor int NOT NULL DEFAULT 0,            -- what it actually took off
        status         text NOT NULL DEFAULT 'applied' CHECK (status IN ('applied','reversed')),
        redeemed_at    timestamptz NOT NULL DEFAULT now()
    );
    """,
    # One promo per order (no self-stack) — also the belt-and-braces guard against a double-apply race.
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_redemption_order "
    f"ON {SCHEMA}.promotion_redemption (promotion_id, order_id);",
    f"CREATE INDEX IF NOT EXISTS ix_redemption_promo ON {SCHEMA}.promotion_redemption (promotion_id, status);",
    f"CREATE INDEX IF NOT EXISTS ix_redemption_user "
    f"ON {SCHEMA}.promotion_redemption (club_id, user_id, status);",
    # --- end promotions ---
]


def init(engine=None):
    """Create / update the billing.* schema idempotently. Requires club.* to exist first
    (FKs to club.club) — db.BOOT_MODULES orders club before billing. Safe on every boot
    and twice in a row (CREATE ... IF NOT EXISTS throughout)."""
    if engine is None:
        from db import get_engine
        engine = get_engine()
    with engine.begin() as conn:
        for stmt in _DDL:
            conn.execute(text(stmt))
    return engine


if __name__ == "__main__":
    init()
    print("billing.* schema initialised")

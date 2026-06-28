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

    # --- billing.account_ledger : the "pay end of month" tab --------------
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

    # 3. token_ledger — audit + THE idempotency guard.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.token_ledger (
        id          bigserial PRIMARY KEY,
        club_id     uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        wallet_id   uuid NOT NULL REFERENCES {SCHEMA}.token_wallet(id) ON DELETE CASCADE,
        booking_id  uuid,                                -- diary.booking / enrolment (cross-lane)
        kind        text NOT NULL CHECK (kind IN ('draw','credit','grant','expire')),
        delta       int  NOT NULL,                        -- signed: draw -1, credit +1, grant +N
        reason      text,
        created_at  timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_token_ledger_wallet "
    f"ON {SCHEMA}.token_ledger (wallet_id, created_at);",
    f"CREATE INDEX IF NOT EXISTS ix_token_ledger_booking "
    f"ON {SCHEMA}.token_ledger (booking_id) WHERE booking_id IS NOT NULL;",
    # THE idempotency guard: a draw and a credit-back are each recorded at most ONCE per
    # (wallet, booking). NULLS NOT DISTINCT so grant/expire rows (booking_id NULL) also dedupe
    # — at most one grant per wallet. The balance only moves when the row actually inserts.
    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_token_ledger_once "
    f"ON {SCHEMA}.token_ledger (wallet_id, booking_id, kind) NULLS NOT DISTINCT;",

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

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
SETTLEMENT_MODES = ("online", "at_court", "monthly_account", "membership_covered", "free")
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
        active          boolean NOT NULL DEFAULT true,
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_price_club ON {SCHEMA}.price (club_id);",
    f"CREATE INDEX IF NOT EXISTS ix_price_product ON {SCHEMA}.price (product_id);",

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
                            ('online','at_court','monthly_account','membership_covered','free')),
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

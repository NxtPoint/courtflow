# offline_conversions/schema.py — core.offline_conversion table.
#
# SHARED, PORTABLE (see package docstring). Raw DDL, no ORM, so it drops into either repo's boot with
# zero model coupling. Registered in db.BOOT_MODULES right after core.schema (it references nothing
# but its own schema; core.acquisition/person/app_user only matter at READ time in the recorder).

from sqlalchemy import text

SCHEMA = "core"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}.offline_conversion (
    id            bigserial   PRIMARY KEY,
    gclid         text        NOT NULL,
    action_name   text        NOT NULL,       -- must match the Google Ads conversion action name
    occurred_at   timestamptz NOT NULL,       -- when the purchase happened (always after the click)
    value_minor   bigint      NOT NULL DEFAULT 0,
    currency      text        NOT NULL DEFAULT 'ZAR',
    source_event  text,                       -- the emit() event that produced it (e.g. payment_succeeded)
    source_ref    text,                       -- the order/subscription id (dedup key)
    account_id    bigint,
    uploaded_at   timestamptz,                -- optional bookkeeping; Google itself dedupes on re-serve
    created_at    timestamptz NOT NULL DEFAULT now()
);
"""

_SUPPLEMENTAL = [
    # Idempotency: one money event (order) is ledgered exactly once.
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_offline_conv_ref "
    f"ON {SCHEMA}.offline_conversion (action_name, source_ref) WHERE source_ref IS NOT NULL",
    # Fallback dedup when a conversion has no order ref (dedupe on click + action + second).
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_offline_conv_click "
    f"ON {SCHEMA}.offline_conversion (gclid, action_name, occurred_at)",
    f"CREATE INDEX IF NOT EXISTS ix_offline_conv_created "
    f"ON {SCHEMA}.offline_conversion (created_at)",
]


def init(engine=None):
    """Create core.offline_conversion idempotently. Registered in db.BOOT_MODULES after core.schema."""
    if engine is None:
        from db import get_engine
        engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        conn.execute(text(_DDL))
        for stmt in _SUPPLEMENTAL:
            conn.execute(text(stmt))
    return engine

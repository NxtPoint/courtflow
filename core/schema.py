# core/schema.py — idempotent bootstrap for the canonical `core.*` schema.
#
# Ported from 1050 core_db/schema.py (core_init). The three-phase idempotent pattern:
#   1. CREATE SCHEMA IF NOT EXISTS core
#   2. Base.metadata.create_all(checkfirst=True)  — all core.* tables
#   3. supplementary DDL ORM create_all can't express (functional/partial unique indexes,
#      hot-path secondary indexes, cross-schema FKs to club.club, club_id indexes)
#
# init() is the public entrypoint (matches every other schema module's init(engine=None)).
# Additive + safe to run repeatedly (no migration framework). Requires Postgres 13+
# (gen_random_uuid via pgcrypto) and that club.* exists first (for the club_id FKs).

from sqlalchemy import text

from core.models import Base, SCHEMA

# Supplementary DDL — each statement idempotent. Run after create_all.
_SUPPLEMENTAL = [
    # Case-insensitive unique email (replaces a citext dependency).
    f'CREATE UNIQUE INDEX IF NOT EXISTS uq_account_email_lower ON {SCHEMA}.account (lower(email)) WHERE deleted_at IS NULL',
    f'CREATE UNIQUE INDEX IF NOT EXISTS uq_app_user_email_lower ON {SCHEMA}.app_user (lower(email)) WHERE deleted_at IS NULL',
    f'CREATE UNIQUE INDEX IF NOT EXISTS uq_account_public_id ON {SCHEMA}.account (public_id)',
    f'CREATE UNIQUE INDEX IF NOT EXISTS uq_app_user_public_id ON {SCHEMA}.app_user (public_id)',
    f'CREATE UNIQUE INDEX IF NOT EXISTS uq_person_public_id ON {SCHEMA}.person (public_id)',

    # One account owner per account.
    f'CREATE UNIQUE INDEX IF NOT EXISTS uq_one_owner_per_account ON {SCHEMA}.app_user (account_id) WHERE is_account_owner',

    # Hot-path secondary indexes.
    f'CREATE INDEX IF NOT EXISTS ix_person_account ON {SCHEMA}.person (account_id)',
    f'CREATE INDEX IF NOT EXISTS ix_app_user_account ON {SCHEMA}.app_user (account_id)',
    f'CREATE INDEX IF NOT EXISTS ix_relationship_to ON {SCHEMA}.relationship (to_person_id)',
    f'CREATE INDEX IF NOT EXISTS ix_relationship_from ON {SCHEMA}.relationship (from_person_id)',
    f'CREATE INDEX IF NOT EXISTS ix_usage_event_account_time ON {SCHEMA}.usage_event (account_id, occurred_at)',
    f'CREATE INDEX IF NOT EXISTS ix_usage_event_type_time ON {SCHEMA}.usage_event (event_type, occurred_at)',
    f'CREATE INDEX IF NOT EXISTS ix_consent_subject ON {SCHEMA}.consent (subject_person_id, consent_type)',

    # Multi-tenancy: club_id discriminator indexes (every domain query scopes by club_id).
    f'CREATE INDEX IF NOT EXISTS ix_account_club ON {SCHEMA}.account (club_id)',
    f'CREATE INDEX IF NOT EXISTS ix_app_user_club ON {SCHEMA}.app_user (club_id)',
    f'CREATE INDEX IF NOT EXISTS ix_person_club ON {SCHEMA}.person (club_id)',
    f'CREATE INDEX IF NOT EXISTS ix_usage_event_club_time ON {SCHEMA}.usage_event (club_id, occurred_at)',
    f'CREATE INDEX IF NOT EXISTS ix_consent_club ON {SCHEMA}.consent (club_id)',
]

# Cross-schema FKs to club.club, added defensively (ADD CONSTRAINT has no IF NOT EXISTS,
# so we guard each with a catalog check). Keeps the tenant discriminator referential.
_CLUB_FKS = [
    ("account", "fk_account_club"),
    ("app_user", "fk_app_user_club"),
    ("person", "fk_person_club"),
    ("usage_event", "fk_usage_event_club"),
    ("consent", "fk_consent_club"),
    ("nps_response", "fk_nps_club"),
    ("relationship", "fk_relationship_club"),
    ("data_subject_request", "fk_dsar_club"),
]

_ADD_CLUB_FK = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = '{conname}'
    ) THEN
        ALTER TABLE {schema}.{table}
            ADD CONSTRAINT {conname}
            FOREIGN KEY (club_id) REFERENCES club.club(id) ON DELETE SET NULL;
    END IF;
END $$;
"""


def init(engine=None):
    """Create / update the core.* schema idempotently. Returns the engine used."""
    if engine is None:
        from db import get_engine
        engine = get_engine()

    # Schema first (create_all does not create non-default schemas).
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))

    # Tables (checkfirst=True -> idempotent).
    Base.metadata.create_all(engine, checkfirst=True)

    # Supplementary indexes + guarded cross-schema FKs.
    with engine.begin() as conn:
        for stmt in _SUPPLEMENTAL:
            conn.execute(text(stmt))
        for table, conname in _CLUB_FKS:
            conn.execute(text(_ADD_CLUB_FK.format(schema=SCHEMA, table=table, conname=conname)))

    return engine


if __name__ == "__main__":
    eng = init()
    print(f"core.* schema initialised on {eng.url.render_as_string(hide_password=True)}")

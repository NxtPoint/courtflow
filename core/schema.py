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

# ---------------------------------------------------------------------------
# core.notification — the in-app inbox + transactional-email delivery ledger.
#
# Driven off the existing emit() event stream (marketing_crm.notifications): for a mapped
# set of transactional usage_event kinds we render a notification and (a) ALWAYS write an
# in-app row here, (b) attempt a best-effort email (SES/Klaviyo) recording its outcome in
# email_status. Delivery is non-fatal — a failure here never touches a booking/payment.
#
# user_id references iam.user(id) (a UUID — the platform identity producers speak), NOT
# core.app_user (a bigint). This is the inbox the member sees in the portal (GET
# /api/me/notifications), so it must key on the same id the principal carries.
#
# Raw idempotent DDL (matches iam/schema.py's style) rather than an ORM model, because it
# FKs across to iam.user + club.club which the core ORM Base deliberately doesn't import.
# ---------------------------------------------------------------------------
_NOTIFICATION_DDL = [
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.notification (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id       uuid NOT NULL,
        user_id       uuid NOT NULL,                 -- iam.user(id): recipient (guardian for a minor)
        kind          text NOT NULL,                 -- the usage_event kind that produced it
        title         text NOT NULL,
        body          text,
        link          text,                          -- e.g. /receipt.html?order=<id>
        data          jsonb,                         -- non-PII context (the rendered ctx)
        read_at       timestamptz,                   -- NULL = unread
        email_status  text NOT NULL DEFAULT 'skipped', -- skipped|sent|failed|pending
        created_at    timestamptz NOT NULL DEFAULT now()
    )
    """,
    # Hot path: the inbox query is (user_id, unread) most-recent-first.
    f"CREATE INDEX IF NOT EXISTS ix_notification_user_read "
    f"ON {SCHEMA}.notification (user_id, read_at)",
    f"CREATE INDEX IF NOT EXISTS ix_notification_user_created "
    f"ON {SCHEMA}.notification (user_id, created_at DESC)",
    f"CREATE INDEX IF NOT EXISTS ix_notification_club "
    f"ON {SCHEMA}.notification (club_id)",
]

# Cross-schema FKs, guarded (ADD CONSTRAINT has no IF NOT EXISTS). CASCADE: a notification
# is meaningless once its user/club is gone.
_NOTIFICATION_FKS = [
    ("fk_notification_club", "club_id", "club.club(id)", "CASCADE"),
    ("fk_notification_user", "user_id", "iam.user(id)", "CASCADE"),
]

# ---------------------------------------------------------------------------
# core.web_daily — the Google (GA4 + Search Console) metrics snapshot store (ANALYTICS-PLAN Phase B).
#
# The org blocks downloadable service-account keys, so the LIVE app can't call GA4/GSC — only the
# keyless-WIF GitHub Action can. So we PUSH, not pull: the daily marketing-digest (which already holds
# that access) POSTs a structured metrics snapshot to POST /api/cron/analytics-ingest, which upserts
# here; the admin Overview → Acquisition panel reads it (no Google credentials ever touch Render).
#
# Row model = one (source, metric, label) datum per snapshot day. `label` is the dimension value
# (channel / page / query / city; '' for a scalar total); `value` the number; `meta` carries extra
# dims a single value can't (a GSC query's position + clicks alongside its impressions). `day` is the
# ingest run-date (the snapshot as-of), NOT a per-day series — the dashboard reads the LATEST snapshot.
# Idempotent per (club_id, day, source, metric, label): a same-day re-run replaces the datum.
# ---------------------------------------------------------------------------
_WEB_DAILY_DDL = [
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.web_daily (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id     uuid NOT NULL,
        day         date NOT NULL,                 -- snapshot as-of (the ingest run date)
        source      text NOT NULL,                 -- 'ga4' | 'gsc'
        metric      text NOT NULL,                 -- 'active_users','sessions_by_channel','clicks','striking',…
        label       text NOT NULL DEFAULT '',      -- dimension value (channel/page/query/city); '' for a scalar
        value       double precision NOT NULL DEFAULT 0,
        window_days int,                           -- lookback the value covers (GA4 7d, GSC 28d)
        meta        jsonb,                         -- extra dims (e.g. a query's position + clicks)
        updated_at  timestamptz NOT NULL DEFAULT now()
    )
    """,
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_web_daily "
    f"ON {SCHEMA}.web_daily (club_id, day, source, metric, label)",
    f"CREATE INDEX IF NOT EXISTS ix_web_daily_club_day "
    f"ON {SCHEMA}.web_daily (club_id, day DESC)",
]

_WEB_DAILY_FKS = [
    ("fk_web_daily_club", "club_id", "club.club(id)", "CASCADE"),
]

# Generic guarded ADD CONSTRAINT (the notification one hardcodes its table); used for web_daily.
_ADD_TABLE_FK = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{conname}') THEN
        ALTER TABLE {schema}.{table}
            ADD CONSTRAINT {conname}
            FOREIGN KEY ({col}) REFERENCES {ref} ON DELETE {ondelete};
    END IF;
END $$;
"""

_ADD_NOTIFICATION_FK = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{conname}') THEN
        ALTER TABLE {schema}.notification
            ADD CONSTRAINT {conname}
            FOREIGN KEY ({col}) REFERENCES {ref} ON DELETE {ondelete};
    END IF;
END $$;
"""

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

    # Client-360 bridge (Slice-0 Step 1): core.person <-> the canonical identity iam.user (UUID).
    # ADD COLUMN here because create_all() only creates missing TABLES, never alters an existing
    # one (the prod core.person predates this column). The partial-unique index enforces the 1:1
    # link while allowing many NULLs during the Step-3 backfill; the plain index is the lookup path
    # for the forward-create helper. FK to iam.user is added post-backfill. See CLIENT-360-CRM-PLAN §10.
    f'ALTER TABLE {SCHEMA}.person ADD COLUMN IF NOT EXISTS iam_user_id uuid',
    f'CREATE UNIQUE INDEX IF NOT EXISTS uq_person_iam_user ON {SCHEMA}.person (iam_user_id) WHERE iam_user_id IS NOT NULL',
    f'CREATE INDEX IF NOT EXISTS ix_person_iam_user ON {SCHEMA}.person (iam_user_id)',
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

    # core.notification — raw DDL (FKs to iam.user + club.club; both boot earlier). The FK to
    # iam.user is added best-effort: if iam.* hasn't booted yet in some isolated path, the table
    # still stands (the FK is added on a later boot — idempotent, ADD CONSTRAINT IF NOT EXISTS-style).
    with engine.begin() as conn:
        for stmt in _NOTIFICATION_DDL:
            conn.execute(text(stmt))
    for conname, col, ref, ondelete in _NOTIFICATION_FKS:
        try:
            with engine.begin() as conn:
                conn.execute(text(_ADD_NOTIFICATION_FK.format(
                    schema=SCHEMA, conname=conname, col=col, ref=ref, ondelete=ondelete)))
        except Exception:
            # Referenced table not present yet in an isolated boot — the FK is added on a
            # later full boot. The table + indexes are already in place. Idempotent.
            pass

    # core.web_daily — the Google metrics snapshot store (same raw-DDL + guarded-FK pattern).
    with engine.begin() as conn:
        for stmt in _WEB_DAILY_DDL:
            conn.execute(text(stmt))
    for conname, col, ref, ondelete in _WEB_DAILY_FKS:
        try:
            with engine.begin() as conn:
                conn.execute(text(_ADD_TABLE_FK.format(
                    schema=SCHEMA, table="web_daily", conname=conname, col=col, ref=ref, ondelete=ondelete)))
        except Exception:
            pass

    return engine


if __name__ == "__main__":
    eng = init()
    print(f"core.* schema initialised on {eng.url.render_as_string(hide_password=True)}")

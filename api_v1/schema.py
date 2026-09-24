# api_v1/schema.py — idempotent boot DDL for the public API's own state (`api.*`).
#
#   api.idempotency  one row per (club, caller, Idempotency-Key): the first successful response to a
#                    write is stored and REPLAYED for a retry with the same key, so a front end that
#                    lost its connection mid-booking can safely try again without a second booking or a
#                    second charge. A failed attempt removes its row, so it can be retried for real.

from sqlalchemy import text

SCHEMA = "api"

_DDL = [
    f"CREATE SCHEMA IF NOT EXISTS {SCHEMA};",
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.idempotency (
        club_id      uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        user_id      uuid NOT NULL,
        idem_key     text NOT NULL,
        endpoint     text NOT NULL,
        status_code  int,
        body         jsonb,
        created_at   timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (club_id, user_id, idem_key)
    );
    """,
]


def init(engine=None):
    """Create / update the api.* schema idempotently. Safe on every boot."""
    if engine is None:
        from db import get_engine
        engine = get_engine()
    with engine.begin() as conn:
        for stmt in _DDL:
            conn.execute(text(stmt))
    return engine

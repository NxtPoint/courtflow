# iam/schema.py — idempotent boot DDL for the `iam.*` schema.
#
# Per docs/02 §3. Identity (login) lives in Clerk; iam.* maps a Clerk user to clubs +
# roles + profile. Raw idempotent SQL (same discipline as 1050 / club.schema).
#
# Tables:
#   iam.user           1 per human (global, cross-club), keyed by clerk_user_id
#   iam.membership     user's relationship to a club (n per user): role + member_status,
#                      unique (club_id, user_id, role)
#   iam.coach_profile  richer coach data (powers "book a named coach")
#   iam.player_profile optional player detail (juniors: dob + guardian_user_id)
#
# UUID PKs via gen_random_uuid() (pgcrypto). All FKs to club.club(id).

from sqlalchemy import text

SCHEMA = "iam"

_DDL = [
    f"CREATE SCHEMA IF NOT EXISTS {SCHEMA};",

    # --- iam.user : one per human (cross-club). 'sub' from the verified Clerk JWT.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.user (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        clerk_user_id text UNIQUE,
        email         text,
        first_name    text,
        surname       text,
        phone         text,
        created_at    timestamptz NOT NULL DEFAULT now(),
        updated_at    timestamptz NOT NULL DEFAULT now()
    );
    """,
    # Case-insensitive email lookup (link-by-email at first Clerk login).
    f"CREATE INDEX IF NOT EXISTS ix_user_email_lower ON {SCHEMA}.user (lower(email));",

    # --- iam.membership : user <-> club, role + member status.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.membership (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id       uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        user_id       uuid NOT NULL REFERENCES {SCHEMA}.user(id) ON DELETE CASCADE,
        role          text NOT NULL CHECK (role IN
                          ('platform_admin','club_admin','coach','member','guest')),
        member_status text DEFAULT 'none' CHECK (member_status IN
                          ('active','lapsed','prospect','none')),
        joined_at     timestamptz NOT NULL DEFAULT now(),
        updated_at    timestamptz NOT NULL DEFAULT now(),
        UNIQUE (club_id, user_id, role)
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_membership_user ON {SCHEMA}.membership (user_id);",
    f"CREATE INDEX IF NOT EXISTS ix_membership_club ON {SCHEMA}.membership (club_id);",

    # --- iam.coach_profile : one per coach (unique user per club).
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.coach_profile (
        id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id                 uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        user_id                 uuid NOT NULL REFERENCES {SCHEMA}.user(id) ON DELETE CASCADE,
        display_name            text,
        headline                text,
        bio                     text,
        photo_url               text,
        is_bookable             boolean DEFAULT true,
        rank                    int DEFAULT 0,
        specialties             text[],
        default_lesson_price_id uuid,   -- -> billing.price (Agent C); kept nullable + unconstrained
        created_at              timestamptz NOT NULL DEFAULT now(),
        updated_at              timestamptz NOT NULL DEFAULT now(),
        UNIQUE (user_id)
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_coach_profile_club ON {SCHEMA}.coach_profile (club_id);",

    # --- iam.player_profile : optional player detail (juniors).
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.player_profile (
        id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id          uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        user_id          uuid NOT NULL REFERENCES {SCHEMA}.user(id) ON DELETE CASCADE,
        dob              date,
        skill_level      text,
        dominant_hand    text,
        utr              numeric,
        guardian_user_id uuid REFERENCES {SCHEMA}.user(id) ON DELETE SET NULL,  -- minors -> parent
        notes            text,
        created_at       timestamptz NOT NULL DEFAULT now(),
        updated_at       timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_player_profile_club ON {SCHEMA}.player_profile (club_id);",
    f"CREATE INDEX IF NOT EXISTS ix_player_profile_user ON {SCHEMA}.player_profile (user_id);",
    f"CREATE INDEX IF NOT EXISTS ix_player_profile_guardian "
    f"ON {SCHEMA}.player_profile (guardian_user_id);",
]


def init(engine=None):
    """Create / update the iam.* schema idempotently. Requires club.* to exist first
    (FKs to club.club) — db.BOOT_MODULES orders club before iam. Safe on every boot."""
    if engine is None:
        from db import get_engine
        engine = get_engine()
    with engine.begin() as conn:
        for stmt in _DDL:
            conn.execute(text(stmt))
    return engine


if __name__ == "__main__":
    init()
    print("iam.* schema initialised")

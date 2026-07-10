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
    # Client-360 Slice-0 Step 5: one human = one row. UNIQUE on lower(email), PARTIAL so
    # login-less dependents (NULL email) are exempt. Safe: upsert_user_by_clerk_id INSERTs only
    # for a genuinely-new email (else it links by email), and the audit confirmed 0 collisions.
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_user_email_lower ON {SCHEMA}.user (lower(email)) "
    f"WHERE email IS NOT NULL;",

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

    # --- iam.user : self-service demographics (client "My Account" spec §2.2 — option A:
    # ADD COLUMN on iam.user, 1:1 with the human, cross-club). Email stays the identity key
    # (read-only in the UI). Idempotent — safe on every boot.
    f"ALTER TABLE {SCHEMA}.user ADD COLUMN IF NOT EXISTS dob                     date;",
    f"ALTER TABLE {SCHEMA}.user ADD COLUMN IF NOT EXISTS address_line1           text;",
    f"ALTER TABLE {SCHEMA}.user ADD COLUMN IF NOT EXISTS address_line2           text;",
    f"ALTER TABLE {SCHEMA}.user ADD COLUMN IF NOT EXISTS city                    text;",
    f"ALTER TABLE {SCHEMA}.user ADD COLUMN IF NOT EXISTS postal_code             text;",
    f"ALTER TABLE {SCHEMA}.user ADD COLUMN IF NOT EXISTS country                 text;",
    f"ALTER TABLE {SCHEMA}.user ADD COLUMN IF NOT EXISTS emergency_contact_name  text;",
    f"ALTER TABLE {SCHEMA}.user ADD COLUMN IF NOT EXISTS emergency_contact_phone text;",
    f"ALTER TABLE {SCHEMA}.user ADD COLUMN IF NOT EXISTS marketing_opt_in        boolean "
    f"NOT NULL DEFAULT false;",

    # --- iam.dependent : a guardian-managed child/dependent who can be BOOKED FOR but never
    # logs in. The dependent IS an iam.user (clerk_user_id NULL → inert to auth) so it can be
    # a diary.booking_party.user_id / diary.enrolment.user_id with ZERO change to diary/billing.
    # This table carries the guardianship + management metadata. Shared foundation: the canonical
    # iam.dependent model from the CRM/foundations spec §3.2 (other roles reuse it). Spend rolls
    # up to the PAYER (order.user_id = guardian); activity rolls up to the PLAYER (this user).
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.dependent (
        id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id           uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        guardian_user_id  uuid NOT NULL REFERENCES {SCHEMA}.user(id) ON DELETE CASCADE,  -- the adult/payer
        dependent_user_id uuid NOT NULL REFERENCES {SCHEMA}.user(id) ON DELETE CASCADE,  -- the login-less child user
        first_name        text NOT NULL,
        surname           text,
        dob               date,
        relationship      text DEFAULT 'child' CHECK (relationship IN
                              ('child','spouse','partner','other')),
        is_minor          boolean NOT NULL DEFAULT true,
        can_self_book     boolean NOT NULL DEFAULT false,  -- reserved (a teen given limited self-service later)
        notes             text,
        is_active         boolean NOT NULL DEFAULT true,
        created_at        timestamptz NOT NULL DEFAULT now(),
        updated_at        timestamptz NOT NULL DEFAULT now(),
        UNIQUE (guardian_user_id, dependent_user_id)
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_dependent_guardian "
    f"ON {SCHEMA}.dependent (club_id, guardian_user_id);",
    f"CREATE INDEX IF NOT EXISTS ix_dependent_dependent "
    f"ON {SCHEMA}.dependent (dependent_user_id);",
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

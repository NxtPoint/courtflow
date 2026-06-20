# admin/schema.py — idempotent boot DDL OWNED by the admin lane.
#
# The admin lane must not edit club/schema.py or iam/schema.py. The two schema additions
# it needs are added here, idempotently (ADD COLUMN IF NOT EXISTS / CREATE TABLE IF NOT
# EXISTS) — same discipline as every other schema module. Registered in db.BOOT_MODULES
# AFTER club/iam (it touches club.club and references iam.* / club.club FKs).
#
#   1. club.club.onboarding_completed — flips true when the owner finishes the wizard.
#   2. iam.coach_invite               — token-invite rows for the coach-invite flow
#                                       (mirrors 1050 coach_invite/ token + status pattern).
#
# init() is safe on every boot and twice in a row.

from sqlalchemy import text

_DDL = [
    # 1) onboarding flag on the tenant root (admin lane owns this column).
    "ALTER TABLE club.club ADD COLUMN IF NOT EXISTS "
    "onboarding_completed boolean NOT NULL DEFAULT false;",

    # 2) iam.coach_invite : token-invite + status for invited coaches.
    """
    CREATE TABLE IF NOT EXISTS iam.coach_invite (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id     uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        user_id     uuid NOT NULL REFERENCES iam.user(id) ON DELETE CASCADE,
        token       text UNIQUE NOT NULL,
        status      text NOT NULL DEFAULT 'invited'
                        CHECK (status IN ('invited','accepted','revoked')),
        created_at  timestamptz NOT NULL DEFAULT now(),
        accepted_at timestamptz
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_coach_invite_club ON iam.coach_invite (club_id);",
    "CREATE INDEX IF NOT EXISTS ix_coach_invite_user ON iam.coach_invite (club_id, user_id);",
    "CREATE INDEX IF NOT EXISTS ix_coach_invite_status ON iam.coach_invite (club_id, status);",
]


def init(engine=None):
    """Create / update the admin lane's schema additions idempotently. Requires club.* and
    iam.* to exist first (FK to club.club / iam.user) — db.BOOT_MODULES orders admin.schema
    after club/iam/billing/diary. Safe on every boot and twice in a row."""
    if engine is None:
        from db import get_engine
        engine = get_engine()
    with engine.begin() as conn:
        for stmt in _DDL:
            conn.execute(text(stmt))
    return engine


if __name__ == "__main__":
    init()
    print("admin.* schema additions initialised")

# coach/schema.py — idempotent boot DDL OWNED by the coach lane.
#
# The coach lane must not edit iam/schema.py, billing/schema.py or diary/schema.py. The
# additions it needs are added HERE, idempotently (ADD COLUMN IF NOT EXISTS) — same
# discipline as admin/schema.py. Registered in db.BOOT_MODULES AFTER iam/billing/diary
# (it references those tables; the ALTERs target iam.coach_profile + billing.product).
#
#   1. iam.coach_profile.onboarding_completed — flips true when the coach finishes the
#      self-service onboarding wizard (mirrors club.club.onboarding_completed).
#   2. billing.product.coach_user_id          — links a lesson product to the coach who
#      owns it (iam.coach_profile has no product list; this is the clean per-coach tie so
#      the booking flow can find "this coach's lesson rate"). Nullable + unconstrained
#      (cross-lane, like billing's other cross-lane uuid columns).
#
# Every column iam.coach_profile needs for the profile editor (display_name, headline,
# bio, photo_url, specialties, is_bookable, rank, default_lesson_price_id) ALREADY exists
# in iam/schema.py, so we only add what's genuinely missing.
#
# init() is safe on every boot and twice in a row.

from sqlalchemy import text

_DDL = [
    # 1) coach self-service onboarding flag (coach lane owns this column).
    "ALTER TABLE iam.coach_profile ADD COLUMN IF NOT EXISTS "
    "onboarding_completed boolean NOT NULL DEFAULT false;",

    # 2) per-coach link on lesson products so the booking flow can resolve a coach's
    #    lesson products/rates directly (billing.product has no coach column of its own).
    "ALTER TABLE billing.product ADD COLUMN IF NOT EXISTS coach_user_id uuid;",
    "CREATE INDEX IF NOT EXISTS ix_product_coach "
    "ON billing.product (club_id, coach_user_id);",
]


def init(engine=None):
    """Create / update the coach lane's schema additions idempotently. Requires iam.* and
    billing.* to exist first (ALTERs iam.coach_profile + billing.product) — db.BOOT_MODULES
    orders coach.schema after iam/billing/diary/admin. Safe on every boot and twice in a row."""
    if engine is None:
        from db import get_engine
        engine = get_engine()
    with engine.begin() as conn:
        for stmt in _DDL:
            conn.execute(text(stmt))
    return engine


if __name__ == "__main__":
    init()
    print("coach.* schema additions initialised")

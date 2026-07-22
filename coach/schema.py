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
# The base iam.coach_profile columns (display_name, headline, bio, photo_url, specialties,
# is_bookable, rank, default_lesson_price_id) ALREADY exist in iam/schema.py. The richer
# editor fields (languages, qualifications, years_experience, public_visibility) are added
# HERE (coach-self-service spec §3.1) idempotently — the coach lane must not edit iam/schema.py.
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

    # 3) editable-profile field additions (coach self-service spec §3.1). The coach lane
    #    owns these — it must not edit iam/schema.py. languages/qualifications are text[]
    #    (same shape as specialties); years_experience is an int; public_visibility is the
    #    "appears in the public/marketing directory" flag, DISTINCT from is_bookable
    #    ("accepts new lesson bookings"). A coach can be visible-but-not-bookable.
    "ALTER TABLE iam.coach_profile ADD COLUMN IF NOT EXISTS languages text[];",
    "ALTER TABLE iam.coach_profile ADD COLUMN IF NOT EXISTS qualifications text[];",
    "ALTER TABLE iam.coach_profile ADD COLUMN IF NOT EXISTS years_experience int;",
    "ALTER TABLE iam.coach_profile ADD COLUMN IF NOT EXISTS "
    "public_visibility boolean NOT NULL DEFAULT true;",

    # 4) per-coach "review bookings before confirming" toggle. When true, a client's lesson
    #    booking with this coach starts as 'requested' (awaiting the coach's accept / propose
    #    new time / decline) instead of auto-confirming. Default false = today's behaviour.
    "ALTER TABLE iam.coach_profile ADD COLUMN IF NOT EXISTS "
    "review_bookings boolean NOT NULL DEFAULT false;",

    # 5) per-coach PREFERRED COURT. Clients never pick a court for a lesson (the system allocates it),
    #    which scattered a coach's lessons across the club. When set, diary.bookings._pick_court_for_lesson
    #    holds THIS court whenever it's free at the requested time, else falls back to the first free
    #    court — a preference, never a hard lock, so a lesson is never blocked by a busy favourite.
    #    Nullable = no preference (unchanged behaviour). Deliberately NOT a FK: diary.resource is a
    #    cross-lane table (same convention as billing.product.coach_user_id above); a deleted court
    #    just stops matching and the fallback takes over.
    "ALTER TABLE iam.coach_profile ADD COLUMN IF NOT EXISTS "
    "preferred_court_resource_id uuid;",
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

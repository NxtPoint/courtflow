# scripts/seed_nextpoint.py — idempotent seed for NextPoint Tennis (club #1).
#
# Seeds everything Phase 0-1 OWNS (club / iam / core), and GUARDS the inserts that
# depend on tables owned by other lanes (diary.resource -> Agent B, billing.product/price
# -> Agent C): if those schemas aren't present yet, the seed logs a TODO and skips them,
# so it runs cleanly now and becomes fully populating once B/C land. Re-runnable (every
# write is keyed/upserted).
#
# Run:
#   python -m scripts.seed_nextpoint           # boots schema, then seeds
#   python -m scripts.seed_nextpoint --skip-init
#
# Sources: docs/02 §7 (seed spec), docs/11 §3.

import logging

from sqlalchemy import text

from db import session_scope, run_boot_init
from scripts.provision_club import provision_club, ensure_template_club

log = logging.getLogger("seed_nextpoint")

SLUG = "nextpoint"

# --- club.* content -------------------------------------------------------
CLUB = dict(
    slug=SLUG,
    name="NextPoint Tennis",
    legal_name="NextPoint Tennis",
    currency_code="ZAR",
    timezone="Africa/Johannesburg",
    locale="en-ZA",
)
BRANDING = dict(
    primary_color="#0B5D1E",
    accent_color="#F2C200",
    logo_url=None,
    favicon_url=None,
    domain="nextpointtennis.com",
    marketing_hosts=["nextpointtennis.com", "www.nextpointtennis.com"],
    og_image_url=None,
    klaviyo_list_id=None,
)
# Wix policies (placeholders confirmed admin-editable later).
POLICY = dict(
    booking_window_days=14,
    min_booking_minutes=60,
    cancellation_cutoff_hours=12,
    guest_requires_member=True,
    allow_pay_at_court=True,
    allow_monthly_account=True,
    allow_online_payment=False,
)
LOCATION = dict(
    name="Killarney Country Club",
    city="Johannesburg",
    country="South Africa",
)

# --- iam coach profiles (docs/02 §3, §7) ----------------------------------
COACHES = [
    dict(email="neville@nextpointtennis.com", first_name="Neville", surname="Godwin",
         display_name="Neville Godwin", headline="Program Director",
         bio="Program Director at NextPoint Tennis.",
         specialties=["high_performance", "junior"], rank=1),
    dict(email="ross@nextpointtennis.com", first_name="Ross", surname="Nemeth",
         display_name="Ross Nemeth", headline="Head Coach",
         bio="Head Coach at NextPoint Tennis.",
         specialties=["junior", "cardio"], rank=2),
]

# --- diary.resource content (Agent B owns the schema; we seed IF it exists) ----
# 8 hard courts + 1 clay (docs/02 §4, §7). Class resources are left to Agent B.
COURTS = [dict(name=f"Court {i}", surface="hard") for i in range(1, 9)]
COURTS.append(dict(name="Clay Court", surface="clay"))


def _table_exists(session, schema, table):
    row = session.execute(
        text("SELECT 1 FROM information_schema.tables "
             "WHERE table_schema = :s AND table_name = :t"),
        {"s": schema, "t": table},
    ).first()
    return row is not None


# --- iam helpers (own lane) ----------------------------------------------

def _upsert_iam_user(session, *, email, first_name, surname):
    """iam.user keyed by email (no Clerk id yet — coaches link on first login)."""
    row = session.execute(
        text("SELECT id FROM iam.user WHERE lower(email) = lower(:e) LIMIT 1"),
        {"e": email},
    ).mappings().first()
    if row:
        session.execute(
            text("UPDATE iam.user SET first_name=:fn, surname=:sn, updated_at=now() WHERE id=:id"),
            {"fn": first_name, "sn": surname, "id": row["id"]},
        )
        return row["id"]
    row = session.execute(
        text("INSERT INTO iam.user (email, first_name, surname) VALUES (:e, :fn, :sn) "
             "RETURNING id"),
        {"e": email, "fn": first_name, "sn": surname},
    ).mappings().first()
    return row["id"]


def _upsert_membership(session, *, club_id, user_id, role, member_status="active"):
    session.execute(
        text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
             "VALUES (:c, :u, :r, :ms) "
             "ON CONFLICT (club_id, user_id, role) "
             "DO UPDATE SET member_status = EXCLUDED.member_status, updated_at = now()"),
        {"c": club_id, "u": user_id, "r": role, "ms": member_status},
    )


def _upsert_coach_profile(session, *, club_id, user_id, display_name, headline, bio,
                          specialties, rank):
    row = session.execute(
        text("SELECT id FROM iam.coach_profile WHERE user_id = :u"),
        {"u": user_id},
    ).mappings().first()
    if row:
        session.execute(
            text("UPDATE iam.coach_profile SET club_id=:c, display_name=:dn, headline=:hl, "
                 "bio=:bio, specialties=:sp, rank=:rank, updated_at=now() WHERE id=:id"),
            {"c": club_id, "dn": display_name, "hl": headline, "bio": bio,
             "sp": specialties, "rank": rank, "id": row["id"]},
        )
        return row["id"]
    row = session.execute(
        text("INSERT INTO iam.coach_profile (club_id, user_id, display_name, headline, bio, "
             "is_bookable, rank, specialties) "
             "VALUES (:c, :u, :dn, :hl, :bio, true, :rank, :sp) RETURNING id"),
        {"c": club_id, "u": user_id, "dn": display_name, "hl": headline, "bio": bio,
         "rank": rank, "sp": specialties},
    ).mappings().first()
    return row["id"]


# --- diary helpers (guarded — Agent B owns diary.*) -----------------------

def _seed_courts_if_possible(session, *, club_id, location_id):
    """Seed the 8 hard + 1 clay courts as diary.resource rows IF that table exists.
    Otherwise log a TODO and skip (Agent B builds diary.* — docs/03)."""
    if not _table_exists(session, "diary", "resource"):
        log.info("TODO(diary): diary.resource not present yet — skipping court seed "
                 "(Agent B owns diary.*; re-run seed after diary lands).")
        return 0
    n = 0
    for rank, court in enumerate(COURTS, start=1):
        existing = session.execute(
            text("SELECT id FROM diary.resource "
                 "WHERE club_id = :c AND kind = 'court' AND name = :n"),
            {"c": club_id, "n": court["name"]},
        ).first()
        if existing:
            continue
        session.execute(
            text("INSERT INTO diary.resource (club_id, location_id, kind, name, surface, "
                 "capacity, is_active, rank) "
                 "VALUES (:c, :loc, 'court', :n, :surface, 1, true, :rank)"),
            {"c": club_id, "loc": location_id, "n": court["name"],
             "surface": court["surface"], "rank": rank},
        )
        n += 1
    log.info("seeded %d new court resources", n)
    return n


def _note_billing_todo(session, *, club_id):
    """billing.* is Agent C's lane. Leave a clear boundary marker (no inserts here)."""
    if not _table_exists(session, "billing", "price"):
        log.info("TODO(billing): billing.product/price not present yet — skipping price "
                 "seed (Agent C owns billing.*). Tiers to seed (ZAR, docs/02 §5/§7): "
                 "hard-court member membership R220/mo, hard-court visitor R150, "
                 "member-guest R80, clay premium, junior beginner R120/30min, "
                 "junior intermediate R150, Cardio Tennis TBD, per-coach lesson prices.")
        return
    log.info("billing.* present — price seeding belongs to Agent C's seed extension; "
             "not duplicated here.")


def seed(session):
    """Idempotently seed NextPoint. Returns a summary dict."""
    # Ensure a template club exists (so club #2 can clone) before seeding club #1.
    ensure_template_club(session)

    club_id = provision_club(
        session,
        slug=CLUB["slug"], name=CLUB["name"], legal_name=CLUB["legal_name"],
        currency_code=CLUB["currency_code"], timezone=CLUB["timezone"], locale=CLUB["locale"],
        branding=BRANDING, policy=POLICY, locations=[LOCATION],
    )

    location_id = session.execute(
        text("SELECT id FROM club.location WHERE club_id = :c AND name = :n"),
        {"c": club_id, "n": LOCATION["name"]},
    ).mappings().first()["id"]

    # Coaches: iam.user + membership(role=coach) + coach_profile.
    coach_ids = []
    for c in COACHES:
        uid = _upsert_iam_user(session, email=c["email"], first_name=c["first_name"],
                               surname=c["surname"])
        _upsert_membership(session, club_id=club_id, user_id=uid, role="coach")
        _upsert_coach_profile(session, club_id=club_id, user_id=uid,
                              display_name=c["display_name"], headline=c["headline"],
                              bio=c["bio"], specialties=c["specialties"], rank=c["rank"])
        coach_ids.append(uid)

    # Guarded cross-lane content.
    courts_seeded = _seed_courts_if_possible(session, club_id=club_id, location_id=location_id)
    _note_billing_todo(session, club_id=club_id)

    return {
        "club_id": str(club_id),
        "location_id": str(location_id),
        "coaches": len(coach_ids),
        "courts_seeded": courts_seeded,
    }


def main():
    import argparse
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description="Seed NextPoint Tennis (club #1), idempotent.")
    ap.add_argument("--skip-init", action="store_true", help="assume schema already booted")
    args = ap.parse_args()

    if not args.skip_init:
        run_boot_init()

    with session_scope() as s:
        summary = seed(s)
    print("NextPoint seed complete:", summary)


if __name__ == "__main__":
    main()

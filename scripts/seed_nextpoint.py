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

# --- platform admins (docs/04 §4) -----------------------------------------
# Seeded by email; iam.user.upsert links by email, so on first Clerk login this user
# inherits the platform_admin membership (lands straight in the admin master diary).
ADMINS = [
    dict(email="info@nextpointtennis.com", first_name="Tomo", surname="", role="platform_admin"),
]

# --- billing price catalogue (ZAR, amount_minor = cents; docs/02 §5/§7) ----
# Audiences match how diary.pricing.price_for queries (by product kind + audience). Member
# court is R0 (covered by the R220/mo membership); visitor/guest pay per booking. Editable
# in the admin console later — these are sensible launch defaults from the Wix site.
PRICES_ZAR = [
    # (kind, product name, [ (audience, amount_minor, unit, duration_minutes), ... ])
    ("court_booking", "Court Hire", [
        ("member", 0, "per_booking", None),       # covered by membership
        ("visitor", 15000, "per_booking", None),  # R150
        ("guest", 8000, "per_booking", None),     # R80 member-guest
    ]),
    ("lesson", "Private Lesson", [("any", 35000, "per_hour", 60)]),                  # R350/hr
    ("membership", "Unlimited Courts Membership", [("member", 22000, "per_month", None)]),  # R220/mo
    ("class", "Cardio Tennis", [("any", 12000, "per_session", 45)]),                 # R120
    ("class", "Junior Beginner", [("any", 12000, "per_session", 30)]),               # R120/30min
    ("class", "Junior Intermediate", [("any", 15000, "per_session", 45)]),           # R150
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


def _seed_billing_if_possible(session, *, club_id, currency_code):
    """Seed the NextPoint product/price catalogue (PRICES_ZAR) as billing.product +
    billing.price rows IF billing.* exists. Idempotent: products keyed by (club, kind, name),
    prices by (club, product, audience, unit). Skips cleanly if Agent C's schema isn't present."""
    if not _table_exists(session, "billing", "price"):
        log.info("TODO(billing): billing.product/price not present yet — skipping price seed.")
        return 0
    n = 0
    for kind, name, tiers in PRICES_ZAR:
        prod_id = session.execute(
            text("SELECT id FROM billing.product WHERE club_id=:c AND kind=:k AND name=:n"),
            {"c": club_id, "k": kind, "n": name},
        ).scalar()
        if not prod_id:
            prod_id = session.execute(
                text("INSERT INTO billing.product (club_id, kind, name, active) "
                     "VALUES (:c, :k, :n, true) RETURNING id"),
                {"c": club_id, "k": kind, "n": name},
            ).scalar_one()
        for audience, amount_minor, unit, duration in tiers:
            exists = session.execute(
                text("SELECT 1 FROM billing.price WHERE club_id=:c AND product_id=:p "
                     "AND audience=:a AND unit=:u"),
                {"c": club_id, "p": prod_id, "a": audience, "u": unit},
            ).first()
            if exists:
                continue
            session.execute(
                text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                     "currency_code, unit, duration_minutes, active) "
                     "VALUES (:c, :p, :a, :amt, :cur, :u, :dur, true)"),
                {"c": club_id, "p": prod_id, "a": audience, "amt": amount_minor,
                 "cur": currency_code, "u": unit, "dur": duration},
            )
            n += 1
    log.info("seeded %d new billing prices", n)
    return n


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

    # Platform admins: seed the user + platform_admin membership by email. On first Clerk
    # login the iam.user is linked by email, so the role attaches automatically (docs/04 §4).
    for a in ADMINS:
        uid = _upsert_iam_user(session, email=a["email"], first_name=a["first_name"],
                               surname=a["surname"])
        _upsert_membership(session, club_id=club_id, user_id=uid, role=a["role"])

    # Guarded cross-lane content.
    courts_seeded = _seed_courts_if_possible(session, club_id=club_id, location_id=location_id)
    prices_seeded = _seed_billing_if_possible(session, club_id=club_id,
                                              currency_code=CLUB["currency_code"])

    return {
        "club_id": str(club_id),
        "location_id": str(location_id),
        "coaches": len(coach_ids),
        "admins": len(ADMINS),
        "courts_seeded": courts_seeded,
        "prices_seeded": prices_seeded,
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

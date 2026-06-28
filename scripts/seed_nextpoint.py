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
# PER-DURATION pricing (PAYG): a service carries ONE billing.price row per offered duration
# (duration_minutes set, unit='per_booking', audience='any', the fixed price). Everyone pays
# the per-duration price; an ACTIVE membership makes COURT bookings free (resolved at booking
# time via has_active_membership — NOT a R0 price row). The Wix-era "member R0 court" tier is
# REMOVED. Editable in the admin console later — these are sensible launch defaults.
PRICES_ZAR = [
    # (kind, product name, [ (audience, amount_minor, unit, duration_minutes), ... ])
    ("court_booking", "Court Hire", [
        ("any", 9000, "per_booking", 30),    # R90  / 30 min
        ("any", 15000, "per_booking", 60),   # R150 / 60 min
        ("any", 21000, "per_booking", 90),   # R210 / 90 min
        ("any", 28000, "per_booking", 120),  # R280 / 120 min
    ]),
    ("lesson", "Private Lesson", [
        ("any", 25000, "per_booking", 30),   # R250 / 30 min
        ("any", 40000, "per_booking", 60),   # R400 / 60 min
    ]),
    # Membership is NOT a single hardcoded price anymore — it's a set of configurable TERM PLANS
    # (MEMBERSHIP_PLANS_ZAR below), seeded via _seed_membership_plans_if_possible. Each plan = a
    # billing.price with term_months + label; the owner edits them in Settings.
    ("class", "Cardio Tennis", [("any", 12000, "per_session", 45)]),                 # R120
    ("class", "Junior Beginner", [("any", 12000, "per_session", 30)]),               # R120/30min
    ("class", "Junior Intermediate", [("any", 15000, "per_session", 45)]),           # R150
]

# --- configurable membership TERM PLANS (label, amount_minor, term_months) -------
# A term plan = one billing.price row (term_months SET, unit='per_month', audience='member') on
# the "Unlimited Courts Membership" product. The member picks one; activation grants term_months.
# These are sensible launch defaults — the owner edits/adds/deactivates them in Settings.
MEMBERSHIP_PRODUCT = ("membership", "Unlimited Courts Membership")
MEMBERSHIP_PLANS_ZAR = [
    # (label, amount_minor, term_months)
    ("1 month",  22000, 1),    # R220 / 1 month
    ("3 months", 60000, 3),    # R600 / 3 months
    ("6 months", 110000, 6),   # R1100 / 6 months
]

# --- configurable COURT PACKS (prepaid PAYG: buy N sessions upfront, draw down) ---------
# Court is R150 / 60-min session PAYG; a pack is the SAME per-session price for a single, then a
# growing discount for buying ahead (encourages upfront purchase — the profitable, drawn-down PAYG
# the wizard sells). Each = one billing.bundle_plan(service_kind='court', duration_minutes=60). The
# token engine draws minutes proportional to the booking length (90 min = 1.5 sessions). Sensible
# launch defaults — the owner edits/adds/removes them in Settings → Pricing → Packs.
COURT_PACKS_ZAR = [
    # (label, sessions_count, price_minor)   ~per-session: 150 / 140 / 135 / 130
    ("1 court session",  1,  15000),
    ("3 court sessions", 3,  42000),
    ("5 court sessions", 5,  67500),
    ("10 court sessions", 10, 130000),
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


def _seed_availability_if_possible(session, *, club_id):
    """Seed default open hours (Mon–Sun 06:00–22:00, 60-min slots) for every court resource
    so the booking wizard has slots to offer (the availability engine expands these rules).
    Idempotent per (resource, weekday). Skips if diary.availability_rule isn't present."""
    if not _table_exists(session, "diary", "availability_rule"):
        return 0
    courts = session.execute(
        text("SELECT id FROM diary.resource WHERE club_id = :c AND kind = 'court'"),
        {"c": club_id},
    ).scalars().all()
    n = 0
    for rid in courts:
        for weekday in range(7):   # 0=Mon .. 6=Sun
            exists = session.execute(
                text("SELECT 1 FROM diary.availability_rule "
                     "WHERE club_id = :c AND resource_id = :r AND weekday = :w"),
                {"c": club_id, "r": rid, "w": weekday},
            ).first()
            if exists:
                continue
            session.execute(
                text("INSERT INTO diary.availability_rule "
                     "(club_id, resource_id, weekday, start_time, end_time, slot_minutes) "
                     "VALUES (:c, :r, :w, '06:00', '22:00', 60)"),
                {"c": club_id, "r": rid, "w": weekday},
            )
            n += 1
    log.info("seeded %d new availability rules", n)
    return n


def _seed_billing_if_possible(session, *, club_id, currency_code):
    """Seed the NextPoint product/price catalogue (PRICES_ZAR) as billing.product +
    billing.price rows IF billing.* exists. Idempotent: products keyed by (club, kind, name),
    prices by (club, product, audience, unit, duration_minutes) — duration is part of the key now
    that a service carries one PER-DURATION price row. Skips cleanly if Agent C's schema isn't present."""
    if not _table_exists(session, "billing", "price"):
        log.info("TODO(billing): billing.product/price not present yet — skipping price seed.")
        return 0
    # One-time migration: deactivate the legacy no-duration COURT prices (the Wix-era
    # member-R0 / visitor / guest tiers). Court is now priced PER DURATION; an active
    # membership makes courts free at booking time (not via a R0 row). Idempotent.
    session.execute(
        text("UPDATE billing.price p SET active=false, updated_at=now() "
             "FROM billing.product pr "
             "WHERE p.product_id = pr.id AND pr.club_id = :c "
             "  AND pr.kind = 'court_booking' AND p.duration_minutes IS NULL AND p.active = true"),
        {"c": club_id},
    )
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
                     "AND audience=:a AND unit=:u "
                     "AND duration_minutes IS NOT DISTINCT FROM :dur"),
                {"c": club_id, "p": prod_id, "a": audience, "u": unit, "dur": duration},
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


def _seed_membership_plans_if_possible(session, *, club_id, currency_code):
    """Seed the configurable membership TERM PLANS (MEMBERSHIP_PLANS_ZAR) as billing.price rows
    (term_months SET, unit='per_month', audience='member') on the membership product. Idempotent:
    a plan is keyed by (club, membership product, term_months) so re-running adds nothing. Also a
    one-time migration: deactivate the legacy no-term membership price (the old single R220/mo row
    without term_months) so it stops being picked as 'the' membership price. Skips cleanly if
    billing.* / the term_months column isn't present yet."""
    if not _table_exists(session, "billing", "price"):
        log.info("TODO(billing): billing.price not present yet — skipping membership plans seed.")
        return 0
    has_term = session.execute(
        text("SELECT 1 FROM information_schema.columns "
             "WHERE table_schema='billing' AND table_name='price' AND column_name='term_months'"),
    ).first()
    if not has_term:
        log.info("TODO(billing): billing.price.term_months not present yet — skipping plans seed.")
        return 0

    kind, name = MEMBERSHIP_PRODUCT
    prod_id = session.execute(
        text("SELECT id FROM billing.product WHERE club_id=:c AND kind=:k ORDER BY created_at LIMIT 1"),
        {"c": club_id, "k": kind},
    ).scalar()
    if not prod_id:
        prod_id = session.execute(
            text("INSERT INTO billing.product (club_id, kind, name, active) "
                 "VALUES (:c, :k, :n, true) RETURNING id"),
            {"c": club_id, "k": kind, "n": name},
        ).scalar_one()

    # One-time migration: deactivate the legacy single membership price (no term_months) — term
    # plans now carry the duration. Idempotent (only flips still-active no-term rows).
    session.execute(
        text("UPDATE billing.price SET active=false, updated_at=now() "
             "WHERE club_id=:c AND product_id=:p AND term_months IS NULL AND active=true"),
        {"c": club_id, "p": prod_id},
    )

    n = 0
    for label, amount_minor, term_months in MEMBERSHIP_PLANS_ZAR:
        exists = session.execute(
            text("SELECT 1 FROM billing.price "
                 "WHERE club_id=:c AND product_id=:p AND term_months=:tm"),
            {"c": club_id, "p": prod_id, "tm": term_months},
        ).first()
        if exists:
            continue
        session.execute(
            text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                 "currency_code, unit, term_months, label, active) "
                 "VALUES (:c, :p, 'member', :amt, :cur, 'per_month', :tm, :lbl, true)"),
            {"c": club_id, "p": prod_id, "amt": amount_minor, "cur": currency_code,
             "tm": term_months, "lbl": label},
        )
        n += 1
    log.info("seeded %d new membership term plans", n)
    return n


def _seed_court_packs_if_possible(session, *, club_id, currency_code):
    """Seed the default COURT PACKS (COURT_PACKS_ZAR) as billing.bundle_plan rows. Seeds ONLY when
    the club has NO court packs yet — so a re-seed never resurrects packs the owner deleted/retired
    (unlike the per-key membership seed, a deleted pack should stay deleted). Skips cleanly if
    billing.bundle_plan isn't present."""
    if not _table_exists(session, "billing", "bundle_plan"):
        log.info("TODO(billing): billing.bundle_plan not present yet — skipping court packs seed.")
        return 0
    existing = session.execute(
        text("SELECT count(*) FROM billing.bundle_plan WHERE club_id=:c AND service_kind='court'"),
        {"c": club_id},
    ).scalar()
    if existing and int(existing) > 0:
        return 0  # the owner already has court packs configured — leave their catalogue alone
    n = 0
    for label, sessions, price_minor in COURT_PACKS_ZAR:
        session.execute(
            text("INSERT INTO billing.bundle_plan (club_id, service_kind, label, sessions_count, "
                 "duration_minutes, price_minor, currency_code, validity_days, active) "
                 "VALUES (:c, 'court', :lbl, :n, 60, :p, :cur, NULL, true)"),
            {"c": club_id, "lbl": label, "n": sessions, "p": price_minor, "cur": currency_code},
        )
        n += 1
    log.info("seeded %d new court packs", n)
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
    hours_seeded = _seed_availability_if_possible(session, club_id=club_id)
    prices_seeded = _seed_billing_if_possible(session, club_id=club_id,
                                              currency_code=CLUB["currency_code"])
    plans_seeded = _seed_membership_plans_if_possible(session, club_id=club_id,
                                                      currency_code=CLUB["currency_code"])
    packs_seeded = _seed_court_packs_if_possible(session, club_id=club_id,
                                                 currency_code=CLUB["currency_code"])

    return {
        "club_id": str(club_id),
        "location_id": str(location_id),
        "coaches": len(coach_ids),
        "admins": len(ADMINS),
        "courts_seeded": courts_seeded,
        "availability_rules": hours_seeded,
        "prices_seeded": prices_seeded,
        "membership_plans_seeded": plans_seeded,
        "court_packs_seeded": packs_seeded,
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

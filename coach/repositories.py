# coach/repositories.py — plain-SQL helpers for the coach self-service APIs.
#
# Same discipline as admin/repositories.py: SQLAlchemy Core text(), every fn takes an
# explicit `session` and NEVER commits (callers compose via db.session_scope()). Both
# club_id AND user_id are ALWAYS passed in (resolved from the principal — never the body)
# and scope every query. A coach only ever reads/writes THEIR OWN row (WHERE user_id = the
# principal's user_id), so cross-coach access is impossible by construction.
#
# Helpers back: profile read/patch (iam.coach_profile + iam.user), hours replace on the
# coach's own diary.resource(kind='coach') (created on demand), lesson services
# (billing.product kind='lesson' + billing.price) CRUD, and onboarding step derivation.

import logging

from sqlalchemy import text

log = logging.getLogger("coach.repositories")


# ---------------------------------------------------------------------------
# serialization helpers (mirror admin/repositories.py)
# ---------------------------------------------------------------------------

def _row(row):
    """Map a Row -> dict, stringifying uuid/uuid-ish fields and isoformatting datetimes."""
    if row is None:
        return None
    d = dict(row)
    for k, v in list(d.items()):
        if v is None:
            continue
        if (k == "id" or k.endswith("_id")) and not isinstance(v, (str, int)):
            d[k] = str(v)
        elif hasattr(v, "isoformat") and not isinstance(v, str):
            d[k] = v.isoformat()
    return d


def _rows(rows):
    return [_row(r) for r in rows]


# ---------------------------------------------------------------------------
# profile (iam.coach_profile + iam.user) — always scoped to (club_id, user_id)
# ---------------------------------------------------------------------------

def get_profile(session, *, club_id, user_id):
    """The coach's own profile: coach_profile fields + the user's name/email/phone.
    Scoped to (club_id, user_id) so a coach can only ever read their own row."""
    return _row(session.execute(
        text("""
            SELECT cp.id, cp.club_id, cp.user_id, cp.display_name, cp.headline, cp.bio,
                   cp.photo_url, cp.specialties, cp.languages, cp.qualifications,
                   cp.years_experience, cp.is_bookable, cp.public_visibility, cp.rank,
                   cp.review_bookings,
                   cp.default_lesson_price_id, cp.onboarding_completed,
                   u.first_name, u.surname, u.email, u.phone,
                   cp.created_at, cp.updated_at
            FROM iam.coach_profile cp
            JOIN iam.user u ON u.id = cp.user_id
            WHERE cp.club_id = :c AND cp.user_id = :u
        """),
        {"c": club_id, "u": user_id},
    ).mappings().first())


def ensure_profile(session, *, club_id, user_id):
    """Idempotently ensure a coach_profile row exists for this (club, user). Returns nothing;
    callers re-read via get_profile. Defensive — the owner-invite flow normally creates it,
    but a coach landing here without one shouldn't 500."""
    existing = session.execute(
        text("SELECT id FROM iam.coach_profile WHERE user_id = :u"),
        {"u": user_id},
    ).mappings().first()
    if existing:
        # Keep club_id aligned with the acting club (a coach has one profile row, unique user).
        session.execute(
            text("UPDATE iam.coach_profile SET club_id = :c, updated_at = now() "
                 "WHERE id = :id AND club_id IS DISTINCT FROM :c"),
            {"c": club_id, "id": existing["id"]},
        )
        return
    session.execute(
        text("INSERT INTO iam.coach_profile (club_id, user_id, is_bookable) "
             "VALUES (:c, :u, true)"),
        {"c": club_id, "u": user_id},
    )


def patch_profile(session, *, club_id, user_id, display_name=None, headline=None, bio=None,
                  photo_url=None, specialties=None, languages=None, qualifications=None,
                  years_experience=None, is_bookable=None, public_visibility=None,
                  review_bookings=None, phone=None, first_name=None, surname=None):
    """COALESCE-style partial update of the coach's OWN profile + linked user. Only supplied
    (non-None) fields change. `specialties`/`languages`/`qualifications` are text[] (a Python
    list or None); `is_bookable`/`public_visibility` are booleans (None = leave unchanged).
    `rank` is NOT writable here — it's admin-only (the route never forwards it). Scoped to
    (club_id, user_id)."""
    ensure_profile(session, club_id=club_id, user_id=user_id)
    session.execute(
        text("""
            UPDATE iam.coach_profile SET
                display_name      = COALESCE(:display_name, display_name),
                headline          = COALESCE(:headline, headline),
                bio               = COALESCE(:bio, bio),
                photo_url         = COALESCE(:photo_url, photo_url),
                specialties       = COALESCE(:specialties, specialties),
                languages         = COALESCE(:languages, languages),
                qualifications    = COALESCE(:qualifications, qualifications),
                years_experience  = COALESCE(:years_experience, years_experience),
                is_bookable       = COALESCE(:is_bookable, is_bookable),
                public_visibility = COALESCE(:public_visibility, public_visibility),
                review_bookings   = COALESCE(:review_bookings, review_bookings),
                updated_at        = now()
            WHERE club_id = :c AND user_id = :u
        """),
        {"c": club_id, "u": user_id, "display_name": display_name, "headline": headline,
         "bio": bio, "photo_url": photo_url, "specialties": specialties,
         "languages": languages, "qualifications": qualifications,
         "years_experience": years_experience, "is_bookable": is_bookable,
         "public_visibility": public_visibility, "review_bookings": review_bookings},
    )
    # Names + phone live on iam.user (the global identity row).
    session.execute(
        text("""
            UPDATE iam.user SET
                first_name = COALESCE(:fn, first_name),
                surname    = COALESCE(:sn, surname),
                phone      = COALESCE(:ph, phone),
                updated_at = now()
            WHERE id = :u
        """),
        {"u": user_id, "fn": first_name, "sn": surname, "ph": phone},
    )
    # Keep the coach's diary.resource name in step with their display_name (if a resource exists).
    if display_name is not None:
        session.execute(
            text("UPDATE diary.resource SET name = :n, updated_at = now() "
                 "WHERE club_id = :c AND kind = 'coach' AND coach_user_id = :u"),
            {"c": club_id, "u": user_id, "n": display_name},
        )
    return get_profile(session, club_id=club_id, user_id=user_id)


def set_onboarding_completed(session, *, club_id, user_id, completed=True):
    ensure_profile(session, club_id=club_id, user_id=user_id)
    session.execute(
        text("UPDATE iam.coach_profile SET onboarding_completed = :v, updated_at = now() "
             "WHERE club_id = :c AND user_id = :u"),
        {"c": club_id, "u": user_id, "v": completed},
    )


# ---------------------------------------------------------------------------
# the coach's own diary.resource (kind='coach') + hours (availability_rule)
# ---------------------------------------------------------------------------

def get_coach_resource(session, *, club_id, user_id):
    """The coach's bookable calendar resource (kind='coach', coach_user_id=user_id), or None."""
    return _row(session.execute(
        text("SELECT id, club_id, location_id, kind, name, coach_user_id, capacity, "
             "       is_active, rank, created_at, updated_at "
             "FROM diary.resource "
             "WHERE club_id = :c AND kind = 'coach' AND coach_user_id = :u "
             "ORDER BY created_at LIMIT 1"),
        {"c": club_id, "u": user_id},
    ).mappings().first())


def ensure_coach_resource(session, *, club_id, user_id, name=None):
    """Ensure the coach has exactly one diary.resource(kind='coach') for this club; create
    it if absent. Returns the resource_id (str). Idempotent — re-running returns the same row."""
    existing = get_coach_resource(session, club_id=club_id, user_id=user_id)
    if existing:
        if name and not existing.get("name"):
            session.execute(
                text("UPDATE diary.resource SET name = :n, updated_at = now() WHERE id = :id"),
                {"n": name, "id": existing["id"]},
            )
        return existing["id"]
    rid = session.execute(
        text("INSERT INTO diary.resource (club_id, kind, name, coach_user_id, capacity) "
             "VALUES (:c, 'coach', :n, :u, 1) RETURNING id"),
        {"c": club_id, "n": name, "u": user_id},
    ).scalar_one()
    return str(rid)


def list_hours(session, *, club_id, resource_id):
    return _rows(session.execute(
        text("SELECT id, club_id, resource_id, weekday, start_time, end_time, slot_minutes "
             "FROM diary.availability_rule WHERE club_id = :c AND resource_id = :r "
             "ORDER BY weekday, start_time"),
        {"c": club_id, "r": resource_id},
    ).mappings().all())


def hours_week(session, *, club_id, user_id):
    """Collapse the coach's own availability_rule into one representative week for UI pre-fill
    (mirrors admin.hours_week but for the coach's resource). Returns
    {week:[{weekday,open,start_time'HH:MM',end_time'HH:MM',slot_minutes}]} for weekdays 0-6.
    Closed default week if the coach has no resource/rules yet. Guarded against diary absence."""
    default = {"week": [
        {"weekday": wd, "open": False, "start_time": "08:00", "end_time": "18:00",
         "slot_minutes": 60} for wd in range(7)]}
    res = get_coach_resource(session, club_id=club_id, user_id=user_id)
    if not res:
        return default
    try:
        rows = session.execute(
            text("SELECT DISTINCT ON (weekday) weekday, "
                 "to_char(start_time,'HH24:MI') AS start_time, "
                 "to_char(end_time,'HH24:MI') AS end_time, slot_minutes "
                 "FROM diary.availability_rule "
                 "WHERE club_id = :c AND resource_id = :r "
                 "ORDER BY weekday, start_time"),
            {"c": club_id, "r": res["id"]},
        ).mappings().all()
    except Exception:
        session.rollback()
        return default
    by_wd = {int(r["weekday"]): r for r in rows}
    week = []
    for wd in range(7):
        r = by_wd.get(wd)
        if r:
            week.append({"weekday": wd, "open": True, "start_time": r["start_time"],
                         "end_time": r["end_time"], "slot_minutes": int(r["slot_minutes"])})
        else:
            week.append({"weekday": wd, "open": False, "start_time": "08:00",
                         "end_time": "18:00", "slot_minutes": 60})
    return {"week": week}


def replace_hours(session, *, club_id, user_id, week, display_name=None):
    """Ensure the coach's resource exists, then REPLACE availability_rule rows on THAT resource
    (delete+insert per weekday, like admin.replace_hours). `week` is a list of dicts:
        {weekday:int(0-6), open:bool, start_time:'HH:MM', end_time:'HH:MM', slot_minutes:int}
    Idempotent — re-running with the same `week` yields the same rows. Returns
    (resource_id, inserted_count)."""
    resource_id = ensure_coach_resource(session, club_id=club_id, user_id=user_id,
                                         name=display_name)
    week = week or []
    weekdays = sorted({int(d["weekday"]) for d in week})
    for wd in weekdays:
        session.execute(
            text("DELETE FROM diary.availability_rule "
                 "WHERE club_id = :c AND resource_id = :r AND weekday = :w"),
            {"c": club_id, "r": resource_id, "w": wd},
        )
    inserted = 0
    for d in week:
        if not d.get("open"):
            continue
        session.execute(
            text("INSERT INTO diary.availability_rule "
                 "(club_id, resource_id, weekday, start_time, end_time, slot_minutes) "
                 "VALUES (:c, :r, :w, :st, :et, :sm)"),
            {"c": club_id, "r": resource_id, "w": int(d["weekday"]),
             "st": d.get("start_time"), "et": d.get("end_time"),
             "sm": int(d.get("slot_minutes") or 60)},
        )
        inserted += 1
    return resource_id, inserted


# ---------------------------------------------------------------------------
# lesson services (billing.product kind='lesson' + billing.price) — per coach
# ---------------------------------------------------------------------------
#
# A "service" = a lesson product owned by the coach (billing.product.coach_user_id = the
# coach) + its price(s). We surface one row per price to the frontend (the lesson rate).
# The coach's default_lesson_price_id points at their first service's price.
#
# PRICING MODEL (the bug fix — coach-self-service spec §3.4): the platform prices lessons
# PER-DURATION as unit='per_booking' rows (one billing.price per offered length, with
# duration_minutes set, audience='any') — EXACTLY what diary/pricing.py price_for()/
# durations_for() resolve against. The old default unit='per_hour' produced rows the booking
# flow could never match, so a coach's rates never surfaced. create_service/add_service_rate
# now write per-duration per_booking rows so a coach's lessons actually price + book.

def _club_currency(session, *, club_id):
    cur = session.execute(
        text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": club_id},
    ).scalar()
    return cur or "ZAR"


def search_members(session, *, club_id, q, limit=10):
    """Search the club's members by NAME or email for the coach's 'book a client' lookup — returns
    contact details (email + phone) so a coach picks a real member instead of free-typing an email.
    Club-scoped. Matches on the full name or the email."""
    like = "%" + (q or "").strip() + "%"
    rows = session.execute(
        text("SELECT DISTINCT u.id AS user_id, u.email, u.phone, u.first_name, u.surname "
             "FROM iam.membership m JOIN iam.\"user\" u ON u.id = m.user_id "
             "WHERE m.club_id = :c AND m.role IN ('member','guest') "
             "  AND ( u.email ILIKE :q "
             "        OR COALESCE(u.first_name,'') || ' ' || COALESCE(u.surname,'') ILIKE :q ) "
             "ORDER BY u.surname NULLS LAST, u.first_name NULLS LAST LIMIT :lim"),
        {"c": club_id, "q": like, "lim": int(limit)},
    ).mappings().all()
    out = []
    for r in rows:
        name = " ".join(x for x in [r["first_name"], r["surname"]] if x).strip() or r["email"]
        out.append({"user_id": str(r["user_id"]), "name": name, "email": r["email"], "phone": r["phone"]})
    return out


def list_services(session, *, club_id, user_id):
    """The coach's OWN lesson products + their active prices, flattened to one row per price
    (the booking-relevant shape). Scoped to (club_id, coach_user_id)."""
    rows = session.execute(
        text("""
            SELECT pr.id AS price_id, pr.product_id, p.name, pr.amount_minor,
                   pr.currency_code, pr.unit, pr.duration_minutes, pr.audience,
                   pr.active, p.coach_user_id
            FROM billing.product p
            JOIN billing.price pr ON pr.product_id = p.id AND pr.club_id = p.club_id
            WHERE p.club_id = :c AND p.kind = 'lesson' AND p.coach_user_id = :u
              AND p.active = true AND pr.active = true
            ORDER BY p.name, pr.amount_minor
        """),
        {"c": club_id, "u": user_id},
    ).mappings().all()
    return _rows(rows)


def get_service(session, *, club_id, user_id, price_id):
    """A single service (price) the coach owns, or None. Scoped so a coach can't touch another
    coach's price (the JOIN requires p.coach_user_id = the principal's user_id)."""
    return _row(session.execute(
        text("""
            SELECT pr.id AS price_id, pr.product_id, p.name, pr.amount_minor,
                   pr.currency_code, pr.unit, pr.duration_minutes, pr.audience,
                   pr.active, p.coach_user_id
            FROM billing.price pr
            JOIN billing.product p ON p.id = pr.product_id AND p.club_id = pr.club_id
            WHERE pr.club_id = :c AND pr.id = :pid AND p.coach_user_id = :u
              AND p.kind = 'lesson'
        """),
        {"c": club_id, "pid": price_id, "u": user_id},
    ).mappings().first())


def create_service(session, *, club_id, user_id, name=None, duration_minutes=None,
                   amount_minor=0, audience="any", unit="per_booking"):
    """Create a lesson product owned by the coach + one PER-DURATION price, and set the
    coach's default_lesson_price_id if unset. Returns the created service (price) dict.

    The price is written as unit='per_booking' with duration_minutes set and audience='any'
    by default — the shape diary/pricing.py resolves (spec §3.4). duration_minutes defaults
    to 60 so a rate is always priceable; pass an explicit value for 30/45/90/120 lessons."""
    ensure_profile(session, club_id=club_id, user_id=user_id)
    pid = session.execute(
        text("INSERT INTO billing.product (club_id, kind, name, coach_user_id, active) "
             "VALUES (:c, 'lesson', :n, :u, true) RETURNING id"),
        {"c": club_id, "n": name, "u": user_id},
    ).scalar_one()
    price_id = session.execute(
        text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
             "currency_code, unit, duration_minutes, active) "
             "VALUES (:c, :prod, :a, :amt, :cur, :u, :dur, true) RETURNING id"),
        {"c": club_id, "prod": pid, "a": audience or "any", "amt": int(amount_minor or 0),
         "cur": _club_currency(session, club_id=club_id), "u": unit or "per_booking",
         "dur": int(duration_minutes) if duration_minutes else 60},
    ).scalar_one()
    # Set default_lesson_price_id only if the coach hasn't got one yet.
    session.execute(
        text("UPDATE iam.coach_profile SET default_lesson_price_id = :p, updated_at = now() "
             "WHERE club_id = :c AND user_id = :u AND default_lesson_price_id IS NULL"),
        {"c": club_id, "u": user_id, "p": price_id},
    )
    return get_service(session, club_id=club_id, user_id=user_id, price_id=str(price_id))


def add_service_rate(session, *, club_id, user_id, product_id, duration_minutes=None,
                     amount_minor=0, audience="any", unit="per_booking"):
    """Add an additional PER-DURATION rate to an existing lesson product the coach owns (so
    one 'Private lesson' product can carry 30/60/90 rates). Returns the new service (price)
    dict, or None if the product isn't a lesson product owned by this coach."""
    owns = session.execute(
        text("SELECT 1 FROM billing.product WHERE club_id = :c AND id = :prod "
             "AND kind = 'lesson' AND coach_user_id = :u AND active = true"),
        {"c": club_id, "prod": product_id, "u": user_id},
    ).first()
    if owns is None:
        return None
    price_id = session.execute(
        text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
             "currency_code, unit, duration_minutes, active) "
             "VALUES (:c, :prod, :a, :amt, :cur, :u, :dur, true) RETURNING id"),
        {"c": club_id, "prod": product_id, "a": audience or "any",
         "amt": int(amount_minor or 0), "cur": _club_currency(session, club_id=club_id),
         "u": unit or "per_booking", "dur": int(duration_minutes) if duration_minutes else 60},
    ).scalar_one()
    session.execute(
        text("UPDATE iam.coach_profile SET default_lesson_price_id = :p, updated_at = now() "
             "WHERE club_id = :c AND user_id = :u AND default_lesson_price_id IS NULL"),
        {"c": club_id, "u": user_id, "p": price_id},
    )
    return get_service(session, club_id=club_id, user_id=user_id, price_id=str(price_id))


def patch_service(session, *, club_id, user_id, price_id, name=None, amount_minor=None,
                  duration_minutes=None):
    """Update a service the coach owns: amount + duration on the price, name on the parent
    product. Returns the updated service, or None if not found / not theirs. duration_minutes
    stays per_booking — editing it keeps the row in the price-resolvable shape."""
    svc = get_service(session, club_id=club_id, user_id=user_id, price_id=price_id)
    if svc is None:
        return None
    if amount_minor is not None:
        session.execute(
            text("UPDATE billing.price SET amount_minor = :amt, updated_at = now() "
                 "WHERE club_id = :c AND id = :pid"),
            {"c": club_id, "pid": price_id, "amt": int(amount_minor)},
        )
    if duration_minutes is not None:
        session.execute(
            text("UPDATE billing.price SET duration_minutes = :dur, updated_at = now() "
                 "WHERE club_id = :c AND id = :pid"),
            {"c": club_id, "pid": price_id, "dur": int(duration_minutes)},
        )
    if name is not None:
        session.execute(
            text("UPDATE billing.product SET name = :n, updated_at = now() "
                 "WHERE club_id = :c AND id = :prod AND coach_user_id = :u"),
            {"c": club_id, "prod": svc["product_id"], "u": user_id, "n": name},
        )
    return get_service(session, club_id=club_id, user_id=user_id, price_id=price_id)


def deactivate_service(session, *, club_id, user_id, price_id):
    """Soft-delete: price.active=false (only if the coach owns it). Returns True/False.
    If it was the coach's default_lesson_price_id, clears that pointer."""
    svc = get_service(session, club_id=club_id, user_id=user_id, price_id=price_id)
    if svc is None:
        return False
    session.execute(
        text("UPDATE billing.price SET active = false, updated_at = now() "
             "WHERE club_id = :c AND id = :pid"),
        {"c": club_id, "pid": price_id},
    )
    session.execute(
        text("UPDATE iam.coach_profile SET default_lesson_price_id = NULL, updated_at = now() "
             "WHERE club_id = :c AND user_id = :u AND default_lesson_price_id = :pid"),
        {"c": club_id, "u": user_id, "pid": price_id},
    )
    return True


# ---------------------------------------------------------------------------
# onboarding step derivation
# ---------------------------------------------------------------------------

def onboarding_steps(session, *, club_id, user_id):
    """Derive the coach's onboarding step booleans (each guarded so a not-yet-present lane
    degrades to False). profile = bio set; hours = >=1 availability_rule on their coach
    resource; services = >=1 active lesson price they own."""
    profile = get_profile(session, club_id=club_id, user_id=user_id)
    profile_done = bool(profile and (profile.get("bio") or "").strip())

    def _count(sql, params):
        try:
            return int(session.execute(text(sql), params).scalar() or 0)
        except Exception:
            session.rollback()
            return 0

    res = get_coach_resource(session, club_id=club_id, user_id=user_id)
    hours_count = 0
    if res:
        hours_count = _count(
            "SELECT count(*) FROM diary.availability_rule "
            "WHERE club_id = :c AND resource_id = :r",
            {"c": club_id, "r": res["id"]},
        )
    services_count = _count(
        "SELECT count(*) FROM billing.price pr "
        "JOIN billing.product p ON p.id = pr.product_id "
        "WHERE pr.club_id = :c AND p.coach_user_id = :u AND p.kind = 'lesson' "
        "  AND pr.active = true AND p.active = true",
        {"c": club_id, "u": user_id},
    )
    return {
        "profile":  profile_done,
        "hours":    hours_count >= 1,
        "services": services_count >= 1,
    }


# ---------------------------------------------------------------------------
# class ownership checks (the coach owns a class type / session iff its
# diary.resource(kind='class').coach_user_id == the coach's user_id)
# ---------------------------------------------------------------------------

def owns_class_resource(session, *, club_id, user_id, resource_id):
    """True iff resource_id is a class resource in this club owned by this coach."""
    return session.execute(
        text("SELECT 1 FROM diary.resource "
             "WHERE club_id = :c AND id = :r AND kind = 'class' AND coach_user_id = :u"),
        {"c": club_id, "r": resource_id, "u": user_id},
    ).first() is not None


def owns_class_session(session, *, club_id, user_id, session_id):
    """True iff session_id is a class_session whose class resource is owned by this coach."""
    return session.execute(
        text("SELECT 1 FROM diary.class_session cs "
             "JOIN diary.resource r ON r.id = cs.resource_id "
             "WHERE cs.club_id = :c AND cs.id = :s AND r.kind = 'class' "
             "  AND r.coach_user_id = :u"),
        {"c": club_id, "s": session_id, "u": user_id},
    ).first() is not None


# ---------------------------------------------------------------------------
# time-off (view + remove) — the coach's OWN diary.resource(kind='coach') blocks.
# POST lives in the diary lane (/api/diary/time-off); the coach lane owns the GET
# (list upcoming) + DELETE (remove a block) so a coach can manage their own holidays.
# Every query is double-scoped: club_id + a JOIN to the coach's own resource, so a
# coach can only ever see/remove time-off on THEIR resource.
# ---------------------------------------------------------------------------

def owns_coach_resource(session, *, club_id, user_id, resource_id):
    """True iff resource_id is the coach's own diary.resource(kind='coach')."""
    return session.execute(
        text("SELECT 1 FROM diary.resource "
             "WHERE club_id = :c AND id = :r AND kind = 'coach' AND coach_user_id = :u"),
        {"c": club_id, "r": resource_id, "u": user_id},
    ).first() is not None


def list_time_off(session, *, club_id, user_id, upcoming_only=True):
    """The coach's time-off blocks on their own coach resource. Upcoming-by-default (ends in
    the future) so the editor shows blocks the coach can still remove. Scoped via a JOIN to
    diary.resource(kind='coach', coach_user_id=the principal)."""
    where = ["t.club_id = :c", "r.kind = 'coach'", "r.coach_user_id = :u"]
    if upcoming_only:
        where.append("t.ends_at >= now()")
    rows = session.execute(
        text("SELECT t.id, t.club_id, t.resource_id, r.name AS resource_name, "
             "       t.starts_at, t.ends_at, t.reason, t.created_at "
             "FROM diary.time_off t "
             "JOIN diary.resource r ON r.id = t.resource_id AND r.club_id = t.club_id "
             "WHERE " + " AND ".join(where) +
             " ORDER BY t.starts_at"),
        {"c": club_id, "u": user_id},
    ).mappings().all()
    return _rows(rows)


def delete_time_off(session, *, club_id, user_id, time_off_id):
    """Remove a time-off block, but ONLY if it sits on the coach's own resource. Returns
    True if a row was deleted, False otherwise (not found / not theirs)."""
    res = session.execute(
        text("DELETE FROM diary.time_off t "
             "USING diary.resource r "
             "WHERE t.id = :tid AND t.club_id = :c "
             "  AND r.id = t.resource_id AND r.kind = 'coach' AND r.coach_user_id = :u"),
        {"tid": time_off_id, "c": club_id, "u": user_id},
    )
    return (res.rowcount or 0) > 0


# ---------------------------------------------------------------------------
# "My Clients" — READ-ONLY derivation from diary.booking + diary.enrolment.
# A coach's client = any user who has a lesson (diary.booking.coach_user_id = me,
# grouped by booked_by_user_id) or a class (diary.enrolment on a class_session I run).
# No new tables. PRIVACY: every query is scoped to coach_user_id = the principal, so a
# coach sees ONLY their own clients and only that client's history WITH THIS COACH.
# lifetime_spend_minor is GROSS (what the client paid on orders attributable to this
# coach) — net-of-commission is the commission-engine agent's job, NOT here.
# ---------------------------------------------------------------------------

# Statuses that count as a real coaching relationship (exclude cancelled/held-only).
_CLIENT_LESSON_STATUSES = "('confirmed','completed','no_show')"
_CLIENT_CLASS_STATUSES = "('enrolled','attended','no_show')"

# A CTE that unions this coach's lesson activity (per client) and class activity (per
# enrolled user) into one (user_id, kind, starts_at, status, order_id) stream. Shared by
# the list + the single-client 360 so the two never drift. Spend is attributed via orders
# referenced by those bookings/enrolments (gross succeeded charges minus refunds).
_COACH_ACTIVITY_CTE = """
WITH coach_bookings AS (
    SELECT b.booked_by_user_id AS user_id, 'lesson' AS kind,
           b.starts_at, b.status, b.order_id, b.id AS booking_id
    FROM diary.booking b
    WHERE b.club_id = :c AND b.coach_user_id = :u
      AND b.booking_type = 'lesson'
      AND b.booked_by_user_id IS NOT NULL
      AND b.status IN """ + _CLIENT_LESSON_STATUSES + """
),
coach_classes AS (
    SELECT e.user_id, 'class' AS kind, cs.starts_at, e.status, e.order_id,
           NULL::uuid AS booking_id      -- classes are enrolments; managed separately
    FROM diary.class_session cs
    JOIN diary.enrolment e ON e.class_session_id = cs.id AND e.club_id = cs.club_id
    WHERE cs.club_id = :c AND cs.coach_user_id = :u
      AND e.user_id IS NOT NULL
      AND e.status IN """ + _CLIENT_CLASS_STATUSES + """
),
activity AS (
    SELECT * FROM coach_bookings
    UNION ALL
    SELECT * FROM coach_classes
),
spend AS (
    -- Gross succeeded charges (minus refunds) on the orders those bookings/enrolments
    -- reference, grouped by the order's user. Guarded join keeps R0/free sessions at 0.
    SELECT o.user_id,
           COALESCE(SUM(p.amount_minor) FILTER (WHERE p.direction='charge'),0)
         - COALESCE(SUM(p.amount_minor) FILTER (WHERE p.direction='refund'),0) AS paid_minor
    FROM billing."order" o
    JOIN billing.payment p ON p.order_id = o.id AND p.club_id = o.club_id
                          AND p.status = 'succeeded'
    WHERE o.club_id = :c
      AND o.id IN (SELECT order_id FROM activity WHERE order_id IS NOT NULL)
    GROUP BY o.user_id
)
"""

# The same coach activity union WITHOUT the billing-referencing `spend` CTE. The cockpit
# helpers use this so they don't parse-error on a billing-less (diary-only) scratch DB —
# they bolt on a spend CTE only when billing.payment is present (see _coach_top_clients).
_COACH_ACTIVITY_BASE = """
WITH coach_bookings AS (
    SELECT b.booked_by_user_id AS user_id, 'lesson' AS kind,
           b.starts_at, b.status, b.order_id
    FROM diary.booking b
    WHERE b.club_id = :c AND b.coach_user_id = :u
      AND b.booking_type = 'lesson'
      AND b.booked_by_user_id IS NOT NULL
      AND b.status IN """ + _CLIENT_LESSON_STATUSES + """
),
coach_classes AS (
    SELECT e.user_id, 'class' AS kind, cs.starts_at, e.status, e.order_id
    FROM diary.class_session cs
    JOIN diary.enrolment e ON e.class_session_id = cs.id AND e.club_id = cs.club_id
    WHERE cs.club_id = :c AND cs.coach_user_id = :u
      AND e.user_id IS NOT NULL
      AND e.status IN """ + _CLIENT_CLASS_STATUSES + """
),
activity AS (
    SELECT * FROM coach_bookings
    UNION ALL
    SELECT * FROM coach_classes
)
"""


def _billing_present(session):
    """billing.order/payment exist? The clients view degrades to 0 spend if not (the lane
    can run against a diary-only scratch DB). Cached per session."""
    cache = getattr(session, "_cf_billing_present", None)
    if cache is not None:
        return cache
    try:
        row = session.execute(
            text("SELECT 1 FROM information_schema.tables "
                 "WHERE table_schema='billing' AND table_name='payment'")
        ).first()
        present = row is not None
    except Exception:
        present = False
    try:
        session._cf_billing_present = present
    except Exception:
        pass
    return present


def list_clients(session, *, club_id, user_id, search=None, limit=200):
    """The coach's client list, derived from their lessons + classes. One row per client with
    counts, first/last seen, no-shows and gross lifetime spend WITH THIS COACH. Optional
    case-insensitive `search` over name/email. Privacy: scoped to coach_user_id = the
    principal — a coach never sees another coach's clients."""
    with_spend = _billing_present(session)
    spend_select = "COALESCE(s.paid_minor,0) AS lifetime_spend_minor" if with_spend else \
        "0 AS lifetime_spend_minor"
    spend_join = "LEFT JOIN spend s ON s.user_id = a.user_id" if with_spend else ""
    params = {"c": club_id, "u": user_id, "lim": int(limit or 200)}
    search_clause = ""
    if search:
        search_clause = ("WHERE (lower(coalesce(u.first_name,'')||' '||"
                         "coalesce(u.surname,'')) LIKE :q OR lower(coalesce(u.email,'')) "
                         "LIKE :q)")
        params["q"] = "%" + str(search).strip().lower() + "%"
    sql = _COACH_ACTIVITY_CTE + f"""
        SELECT u.id AS user_id, u.first_name, u.surname, u.email, u.phone,
               MIN(a.starts_at) AS first_seen, MAX(a.starts_at) AS last_seen,
               COUNT(*) FILTER (WHERE a.kind='lesson') AS lessons_count,
               COUNT(*) FILTER (WHERE a.kind='class')  AS classes_count,
               COUNT(*) FILTER (WHERE a.status='no_show') AS no_show_count,
               COUNT(*) FILTER (WHERE a.starts_at >= now()) AS upcoming_count,
               {spend_select}
        FROM activity a
        JOIN iam.user u ON u.id = a.user_id
        {spend_join}
        {search_clause}
        GROUP BY u.id, u.first_name, u.surname, u.email, u.phone{', s.paid_minor' if with_spend else ''}
        ORDER BY last_seen DESC NULLS LAST
        LIMIT :lim
    """
    rows = session.execute(text(sql), params).mappings().all()
    return _rows(rows)


def get_client(session, *, club_id, user_id, client_user_id, month=None):
    """A single client's 360 WITH THIS COACH only: the same headline counts as the list row
    plus the full session history (lessons + classes, with booking_id so the coach can
    reschedule/cancel a lesson). Returns None if the user has no coaching relationship with
    this coach (privacy: can't probe arbitrary users — they must appear in this coach's activity).

    When `month` (YYYY-MM) is given, also returns the per-client MONEY for that month
    (paid/owed/net/written-off) + this client's owed/written-off arrears line items (each with
    collect/discount/write-off affordances) — the single client-centric statement the coach
    reviews at month-end. Reuses billing.commission.coach_statement so the figures never drift."""
    with_spend = _billing_present(session)
    params = {"c": club_id, "u": user_id, "cu": client_user_id}

    # Headline (same shape as a list row, filtered to the one client).
    spend_select = "COALESCE(s.paid_minor,0) AS lifetime_spend_minor" if with_spend else \
        "0 AS lifetime_spend_minor"
    spend_join = "LEFT JOIN spend s ON s.user_id = a.user_id" if with_spend else ""
    head_sql = _COACH_ACTIVITY_CTE + f"""
        SELECT u.id AS user_id, u.first_name, u.surname, u.email, u.phone,
               MIN(a.starts_at) AS first_seen, MAX(a.starts_at) AS last_seen,
               COUNT(*) FILTER (WHERE a.kind='lesson') AS lessons_count,
               COUNT(*) FILTER (WHERE a.kind='class')  AS classes_count,
               COUNT(*) FILTER (WHERE a.status='no_show') AS no_show_count,
               COUNT(*) FILTER (WHERE a.starts_at >= now()) AS upcoming_count,
               {spend_select}
        FROM activity a
        JOIN iam.user u ON u.id = a.user_id
        {spend_join}
        WHERE a.user_id = :cu
        GROUP BY u.id, u.first_name, u.surname, u.email, u.phone{', s.paid_minor' if with_spend else ''}
    """
    head = _row(session.execute(text(head_sql), params).mappings().first())
    if head is None:
        return None

    # Full per-session history (most recent first), scoped to this coach + this client.
    # booking_id lets the client view wire reschedule/cancel to a lesson (classes carry none).
    hist_sql = _COACH_ACTIVITY_CTE + """
        SELECT a.kind, a.starts_at, a.status, a.order_id, a.booking_id
        FROM activity a
        WHERE a.user_id = :cu
        ORDER BY a.starts_at DESC
        LIMIT 200
    """
    head["history"] = _rows(session.execute(text(hist_sql), params).mappings().all())

    # Upcoming sessions (future, soonest first) — with booking_id for reschedule/cancel.
    upcoming_sql = _COACH_ACTIVITY_CTE + """
        SELECT a.kind, a.starts_at, a.status, a.booking_id
        FROM activity a
        WHERE a.user_id = :cu AND a.starts_at >= now()
        ORDER BY a.starts_at ASC
        LIMIT 20
    """
    head["upcoming"] = _rows(session.execute(text(upcoming_sql), params).mappings().all())

    # Month-end money for THIS client (reuse the coach statement so figures never drift).
    if month:
        head["month"] = month
        try:
            from billing import commission as _comm
            stmt = _comm.coach_statement(session, club_id=club_id, coach_user_id=user_id, month=month)
            cid = str(client_user_id)
            row = next((c for c in (stmt.get("clients") or [])
                        if str(c.get("client_user_id")) == cid), None)
            head["money"] = {
                "currency": stmt.get("currency", "ZAR"),
                "paid_minor": int((row or {}).get("paid_minor") or 0),
                "owed_minor": int((row or {}).get("owed_minor") or 0),
                "net_minor": int((row or {}).get("net_minor") or 0),
                "written_off_minor": sum(int(a.get("gross_minor") or 0)
                    for a in (stmt.get("arrears_items") or [])
                    if str(a.get("client_user_id")) == cid and a.get("status") == "written_off"),
            }
            head["arrears"] = [a for a in (stmt.get("arrears_items") or [])
                               if str(a.get("client_user_id")) == cid]
            # By-service breakdown (client → services → sessions → event story) — same drill as the
            # client billing pattern.
            bd = _comm.client_service_breakdown(session, club_id=club_id, coach_user_id=user_id,
                                                client_user_id=client_user_id, month=month)
            head["services"] = bd["services"]
            head["services_total_minor"] = bd["total_minor"]
            head["services_billed_minor"] = bd["billed_minor"]
        except Exception:
            log.info("get_client: month money skipped (commission unavailable) client=%s", client_user_id)
            head["money"] = None
            head["arrears"] = []
            head["services"] = []
    return head


# ---------------------------------------------------------------------------
# BUSINESS COCKPIT — the coach's read-only "how is my business doing?" overview
# (coach-self-service-spec §6). Pure SQL aggregation over what already exists:
#   activity  -> diary.booking (lessons coach ran) + diary.enrolment/class_session
#   earnings  -> billing.commission_split (party_type='coach' = NET; gross_minor = GROSS;
#                commission = owner party). NET-of-commission per docs/specs/01 (ex-VAT,
#                accrues on collection). If no agreement/splits exist -> commission 0,
#                net = gross (derived from succeeded payments).
#   fill rate -> booked lesson hours / available hours (availability_rule over the month).
#   clients   -> new (first_seen in-period) vs returning; top by spend/sessions.
#   arrears   -> billing.coach_arrears (status='owed') if readable; else 0.
# PRIVACY: every query is scoped to coach_user_id = the principal's user_id (passed in,
# never the body), so a coach sees ONLY their own numbers. Each billing/commission read is
# guarded by _table_present so the lane degrades gracefully on a diary-only scratch DB.
# ---------------------------------------------------------------------------

def _table_present(session, schema, table):
    """Does schema.table exist? Cached per session so a degraded (diary-only) DB returns
    0/None instead of erroring. Mirrors _billing_present but generic."""
    attr = "_cf_present_%s_%s" % (schema, table)
    cache = getattr(session, attr, None)
    if cache is not None:
        return cache
    try:
        present = session.execute(
            text("SELECT 1 FROM information_schema.tables "
                 "WHERE table_schema = :s AND table_name = :t"),
            {"s": schema, "t": table},
        ).first() is not None
    except Exception:
        present = False
    try:
        setattr(session, attr, present)
    except Exception:
        pass
    return present


def _month_bounds(session, month=None):
    """Resolve a YYYY-MM (default current month, server tz) to (ym, start_date, end_date) —
    a half-open [start, end) of date objects. Done in SQL so it matches Postgres' clock."""
    row = session.execute(
        text("""
            SELECT to_char(COALESCE(to_date(:m,'YYYY-MM'), date_trunc('month', now())),
                          'YYYY-MM') AS ym,
                   date_trunc('month',
                              COALESCE(to_date(:m,'YYYY-MM'), now()))::date AS start_d,
                   (date_trunc('month',
                              COALESCE(to_date(:m,'YYYY-MM'), now())) +
                    interval '1 month')::date AS end_d
        """),
        {"m": month},
    ).mappings().first()
    return row["ym"], row["start_d"], row["end_d"]


def _coach_activity_kpis(session, *, club_id, user_id, start_d, end_d):
    """Lessons/hours/classes/no-shows/active clients for the month, scoped to this coach.
    Lessons: diary.booking(coach_user_id=me, type=lesson, status confirmed/completed/no_show)
    in [start,end). Classes: class_session(coach_user_id=me) scheduled/completed in-period."""
    lessons = session.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE status IN ('confirmed','completed')) AS lessons_count,
                COALESCE(SUM(EXTRACT(EPOCH FROM (ends_at - starts_at))/3600.0)
                         FILTER (WHERE status IN ('confirmed','completed')),0) AS hours,
                COUNT(*) FILTER (WHERE status = 'no_show') AS no_shows,
                COUNT(DISTINCT booked_by_user_id)
                    FILTER (WHERE status IN ('confirmed','completed')) AS clients_active
            FROM diary.booking
            WHERE club_id = :c AND coach_user_id = :u AND booking_type = 'lesson'
              AND starts_at >= :s AND starts_at < :e
              AND status IN ('confirmed','completed','no_show')
        """),
        {"c": club_id, "u": user_id, "s": start_d, "e": end_d},
    ).mappings().first()
    classes = session.execute(
        text("""
            SELECT COUNT(*) AS classes_count
            FROM diary.class_session cs
            WHERE cs.club_id = :c AND cs.coach_user_id = :u
              AND cs.starts_at >= :s AND cs.starts_at < :e
              AND cs.status IN ('scheduled','completed')
        """),
        {"c": club_id, "u": user_id, "s": start_d, "e": end_d},
    ).mappings().first()
    return {
        "lessons_count": int(lessons["lessons_count"] or 0),
        "hours": round(float(lessons["hours"] or 0.0), 2),
        "no_shows": int(lessons["no_shows"] or 0),
        "clients_active": int(lessons["clients_active"] or 0),
        "classes_count": int(classes["classes_count"] or 0),
    }


def _coach_billed(session, *, club_id, user_id, start_d, end_d):
    """Total GROSS coaching billed in the month: the sum of what was charged for this coach's
    lessons — BEFORE any write-off / discount / (non-)collection. This is the 'Total billed'
    headline that mirrors the client record, and unlike gross_minor (collected payments only)
    it reflects the real business done. Guarded → 0."""
    try:
        v = session.execute(
            text("""
                SELECT COALESCE(SUM(ol.amount_minor), 0)
                FROM diary.booking b
                JOIN billing.order_line ol ON ol.booking_id = b.id AND ol.club_id = b.club_id
                WHERE b.club_id = :c AND b.coach_user_id = :u AND b.booking_type = 'lesson'
                  AND b.starts_at >= :s AND b.starts_at < :e
                  AND b.status IN ('confirmed','completed','no_show','held')
            """),
            {"c": club_id, "u": user_id, "s": start_d, "e": end_d},
        ).scalar()
        return int(v or 0)
    except Exception:
        return 0


def _coach_earnings(session, *, club_id, user_id, start_d, end_d):
    """Earnings for the month, NET of commission (docs/specs/01). Two reads, both guarded:
      gross  = succeeded charge payments (minus refunds) on orders attributable to this
               coach's lessons/classes, in-period (the canonical 'what the client paid').
      net/commission = from billing.commission_split (party_type='coach' = coach net;
               party_type='owner' = the commission). If commission_split has no rows for the
    period (no agreement / nothing collected), net = gross and commission = 0 — exactly the
    graceful-degradation contract. We anchor the headline on payments so 'no commission engine
    yet' still shows real gross/net."""
    out = {"gross_minor": 0, "net_minor": 0, "commission_minor": 0}
    if not _table_present(session, "billing", "payment"):
        return out  # diary-only DB: no money surface at all.

    # Gross from succeeded payments on coach-attributable orders (lessons + classes).
    gross = session.execute(
        text("""
            WITH coach_orders AS (
                SELECT DISTINCT b.order_id AS order_id
                FROM diary.booking b
                WHERE b.club_id = :c AND b.coach_user_id = :u
                  AND b.booking_type = 'lesson' AND b.order_id IS NOT NULL
                  AND b.status IN ('confirmed','completed')
                UNION
                SELECT DISTINCT e.order_id
                FROM diary.class_session cs
                JOIN diary.enrolment e ON e.class_session_id = cs.id AND e.club_id = cs.club_id
                WHERE cs.club_id = :c AND cs.coach_user_id = :u AND e.order_id IS NOT NULL
            )
            SELECT
                COALESCE(SUM(p.amount_minor) FILTER (WHERE p.direction='charge'),0)
              - COALESCE(SUM(p.amount_minor) FILTER (WHERE p.direction='refund'),0) AS gross_minor
            FROM billing.payment p
            WHERE p.club_id = :c AND p.status = 'succeeded'
              AND p.order_id IN (SELECT order_id FROM coach_orders)
              AND p.created_at >= :s AND p.created_at < :e
        """),
        {"c": club_id, "u": user_id, "s": start_d, "e": end_d},
    ).scalar()
    out["gross_minor"] = int(gross or 0)

    # Net + commission from the commission engine, if present + populated for the period.
    if _table_present(session, "billing", "commission_split"):
        split = session.execute(
            text("""
                SELECT
                    COALESCE(SUM(amount_minor) FILTER (WHERE party_type='coach'),0) AS net_minor,
                    COALESCE(SUM(amount_minor) FILTER (WHERE party_type='owner'),0) AS commission_minor,
                    COUNT(*) AS n
                FROM billing.commission_split
                WHERE club_id = :c AND coach_user_id = :u
                  AND basis IN ('lesson_commission','class_commission')
                  AND occurred_at >= :s AND occurred_at < :e
            """),
            {"c": club_id, "u": user_id, "s": start_d, "e": end_d},
        ).mappings().first()
        if split and int(split["n"] or 0) > 0:
            out["net_minor"] = int(split["net_minor"] or 0)
            out["commission_minor"] = int(split["commission_minor"] or 0)
            return out
    # No agreement / no splits -> commission 0, net = gross.
    out["net_minor"] = out["gross_minor"]
    out["commission_minor"] = 0
    return out


def _coach_arrears_owed(session, *, club_id, user_id):
    """Total arrears still owed to the coach (status='owed') across all clients — read-only.
    Degrades to 0 if billing.coach_arrears is absent (no commission engine on this DB)."""
    if not _table_present(session, "billing", "coach_arrears"):
        return 0
    try:
        v = session.execute(
            text("SELECT COALESCE(SUM(gross_minor),0) FROM billing.coach_arrears "
                 "WHERE club_id = :c AND coach_user_id = :u AND status = 'owed'"),
            {"c": club_id, "u": user_id},
        ).scalar()
    except Exception:
        session.rollback()
        return 0
    return int(v or 0)


def _coach_fill_rate(session, *, club_id, user_id, start_d, end_d):
    """Coarse fill rate (%) for the month = booked lesson hours / available hours.
    Available hours = SUM over the coach's availability_rule of (end_time-start_time) per
    open weekday, weighted by how many times that weekday occurs in [start,end). Returns
    None if the coach has no availability_rule (can't divide) so the UI shows '—'."""
    res = get_coach_resource(session, club_id=club_id, user_id=user_id)
    if not res:
        return None
    avail = session.execute(
        text("""
            WITH days AS (
                SELECT d::date AS d, EXTRACT(ISODOW FROM d)::int - 1 AS weekday
                FROM generate_series(CAST(:s AS timestamp),
                                     CAST(:e AS timestamp) - interval '1 day',
                                     interval '1 day') d
            ),
            wd_counts AS (
                SELECT weekday, COUNT(*) AS n FROM days GROUP BY weekday
            )
            SELECT COALESCE(SUM(
                       EXTRACT(EPOCH FROM (ar.end_time - ar.start_time))/3600.0 * wc.n
                   ),0) AS available_hours
            FROM diary.availability_rule ar
            JOIN wd_counts wc ON wc.weekday = ar.weekday
            WHERE ar.club_id = :c AND ar.resource_id = :r
        """),
        {"c": club_id, "r": res["id"], "s": start_d, "e": end_d},
    ).scalar()
    available_hours = float(avail or 0.0)
    if available_hours <= 0:
        return None
    booked = session.execute(
        text("""
            SELECT COALESCE(SUM(EXTRACT(EPOCH FROM (ends_at - starts_at))/3600.0),0)
            FROM diary.booking
            WHERE club_id = :c AND coach_user_id = :u AND booking_type = 'lesson'
              AND status IN ('confirmed','completed')
              AND starts_at >= :s AND starts_at < :e
        """),
        {"c": club_id, "u": user_id, "s": start_d, "e": end_d},
    ).scalar()
    booked_hours = float(booked or 0.0)
    pct = (booked_hours / available_hours) * 100.0
    return round(min(pct, 100.0), 1)


def _coach_new_clients(session, *, club_id, user_id, start_d, end_d):
    """clients_new = clients whose FIRST EVER session with this coach (lesson or class) falls
    in [start,end); the rest active this month are 'returning'. Uses the same activity union
    as the clients view (lessons + class enrolments), scoped to this coach."""
    row = session.execute(
        text(_COACH_ACTIVITY_BASE + """
            , first_seen AS (
                SELECT user_id, MIN(starts_at) AS fs FROM activity GROUP BY user_id
            ),
            active_this_month AS (
                SELECT DISTINCT user_id FROM activity
                WHERE starts_at >= :s AND starts_at < :e
            )
            SELECT COUNT(*) FILTER (WHERE fs.fs >= :s AND fs.fs < :e) AS clients_new
            FROM active_this_month a
            JOIN first_seen fs ON fs.user_id = a.user_id
        """),
        {"c": club_id, "u": user_id, "s": start_d, "e": end_d},
    ).mappings().first()
    return int(row["clients_new"] or 0)


def _coach_top_clients(session, *, club_id, user_id, start_d, end_d, limit=5):
    """Top clients for the month by spend then sessions, scoped to this coach. Spend is GROSS
    (what the client paid on this coach's orders in-period); degrades to 0 if billing absent."""
    with_spend = _table_present(session, "billing", "payment")
    spend_cte = ""
    spend_select = "0 AS spend_minor"
    spend_join = ""
    if with_spend:
        spend_cte = """
            , period_spend AS (
                SELECT o.user_id,
                       COALESCE(SUM(p.amount_minor) FILTER (WHERE p.direction='charge'),0)
                     - COALESCE(SUM(p.amount_minor) FILTER (WHERE p.direction='refund'),0) AS spend_minor
                FROM billing."order" o
                JOIN billing.payment p ON p.order_id = o.id AND p.club_id = o.club_id
                                      AND p.status = 'succeeded'
                WHERE o.club_id = :c
                  AND o.id IN (SELECT order_id FROM activity
                               WHERE order_id IS NOT NULL AND starts_at >= :s AND starts_at < :e)
                GROUP BY o.user_id
            )
        """
        spend_select = "COALESCE(ps.spend_minor,0) AS spend_minor"
        spend_join = "LEFT JOIN period_spend ps ON ps.user_id = a.user_id"
    sql = _COACH_ACTIVITY_BASE + spend_cte + f"""
        SELECT u.id AS user_id, u.first_name, u.surname, u.email,
               COUNT(*) AS sessions, {spend_select}
        FROM activity a
        JOIN iam."user" u ON u.id = a.user_id
        {spend_join}
        WHERE a.starts_at >= :s AND a.starts_at < :e
        GROUP BY u.id, u.first_name, u.surname, u.email{', ps.spend_minor' if with_spend else ''}
        ORDER BY spend_minor DESC, sessions DESC, u.surname ASC
        LIMIT :lim
    """
    rows = session.execute(text(sql),
                           {"c": club_id, "u": user_id, "s": start_d, "e": end_d,
                            "lim": int(limit)}).mappings().all()
    out = []
    for r in rows:
        name = " ".join(x for x in [r["first_name"], r["surname"]] if x).strip()
        out.append({
            "user_id": str(r["user_id"]),
            "name": name or r["email"] or "Client",
            "sessions": int(r["sessions"] or 0),
            "spend_minor": int(r["spend_minor"] or 0),
        })
    return out


def _coach_trend(session, *, club_id, user_id, months=6):
    """Last `months` calendar months (oldest->newest) of {month, net_minor, lessons}. Net is
    from commission_split (coach party) if present+populated, else falls back to gross from
    payments so the bars still reflect real money on a pre-commission DB."""
    has_split = _table_present(session, "billing", "commission_split")
    has_pay = _table_present(session, "billing", "payment")
    spine = session.execute(
        text("""
            SELECT to_char(m,'YYYY-MM') AS ym, m::date AS start_d,
                   (m + interval '1 month')::date AS end_d
            FROM generate_series(
                date_trunc('month', now()) - (:k - 1) * interval '1 month',
                date_trunc('month', now()), interval '1 month') m
            ORDER BY m
        """),
        {"k": int(months)},
    ).mappings().all()
    out = []
    for row in spine:
        s, e = row["start_d"], row["end_d"]
        lessons = session.execute(
            text("""
                SELECT COUNT(*) FROM diary.booking
                WHERE club_id = :c AND coach_user_id = :u AND booking_type = 'lesson'
                  AND status IN ('confirmed','completed')
                  AND starts_at >= :s AND starts_at < :e
            """),
            {"c": club_id, "u": user_id, "s": s, "e": e},
        ).scalar()
        net_minor = 0
        if has_split:
            sp = session.execute(
                text("""
                    SELECT COALESCE(SUM(amount_minor) FILTER (WHERE party_type='coach'),0) AS net,
                           COUNT(*) AS n
                    FROM billing.commission_split
                    WHERE club_id = :c AND coach_user_id = :u
                      AND basis IN ('lesson_commission','class_commission')
                      AND occurred_at >= :s AND occurred_at < :e
                """),
                {"c": club_id, "u": user_id, "s": s, "e": e},
            ).mappings().first()
            if sp and int(sp["n"] or 0) > 0:
                net_minor = int(sp["net"] or 0)
        if net_minor == 0 and has_pay:
            ern = _coach_earnings(session, club_id=club_id, user_id=user_id, start_d=s, end_d=e)
            net_minor = ern["net_minor"]
        out.append({"month": row["ym"], "net_minor": int(net_minor),
                    "lessons": int(lessons or 0)})
    return out


def _coach_upcoming(session, *, club_id, user_id, limit=6):
    """The coach's next N confirmed lessons + scheduled class sessions (whichever is sooner),
    scoped to this coach. Returns [{when, client, type}]. Lessons resolve the booked client's
    name; classes show the class/resource name + enrolled count."""
    lessons = session.execute(
        text("""
            SELECT b.starts_at AS when_at, 'lesson' AS type, r.name AS resource_name,
                   u.first_name, u.surname, u.email
            FROM diary.booking b
            LEFT JOIN iam."user" u ON u.id = b.booked_by_user_id
            LEFT JOIN diary.resource r ON r.id = b.resource_id
            WHERE b.club_id = :c AND b.coach_user_id = :u AND b.booking_type = 'lesson'
              AND b.status = 'confirmed' AND b.starts_at >= now()
            ORDER BY b.starts_at
            LIMIT :lim
        """),
        {"c": club_id, "u": user_id, "lim": int(limit)},
    ).mappings().all()
    classes = session.execute(
        text("""
            SELECT cs.starts_at AS when_at, 'class' AS type, r.name AS resource_name,
                   COUNT(e.id) FILTER (WHERE e.status IN ('enrolled','attended')) AS enrolled
            FROM diary.class_session cs
            LEFT JOIN diary.resource r ON r.id = cs.resource_id
            LEFT JOIN diary.enrolment e ON e.class_session_id = cs.id AND e.club_id = cs.club_id
            WHERE cs.club_id = :c AND cs.coach_user_id = :u
              AND cs.status = 'scheduled' AND cs.starts_at >= now()
            GROUP BY cs.starts_at, r.name
            ORDER BY cs.starts_at
            LIMIT :lim
        """),
        {"c": club_id, "u": user_id, "lim": int(limit)},
    ).mappings().all()
    merged = []
    for r in lessons:
        client = " ".join(x for x in [r["first_name"], r["surname"]] if x).strip()
        merged.append({
            "when": r["when_at"].isoformat() if r["when_at"] else None,
            "client": client or r["email"] or "Client",
            "type": (r["resource_name"] or "Lesson"),
        })
    for r in classes:
        merged.append({
            "when": r["when_at"].isoformat() if r["when_at"] else None,
            "client": (str(int(r["enrolled"] or 0)) + " enrolled"),
            "type": (r["resource_name"] or "Class"),
        })
    merged.sort(key=lambda x: x["when"] or "")
    return merged[:limit]


def cockpit(session, *, club_id, user_id, month=None):
    """The coach business cockpit payload for a month (default current). Read-only aggregation,
    scoped to coach_user_id = user_id (the principal — never the body). Composes the helpers
    above into the single payload the dashboard renders. Every billing/commission read is
    guarded so a diary-only DB degrades to 0/None instead of erroring."""
    ym, start_d, end_d = _month_bounds(session, month=month)

    act = _coach_activity_kpis(session, club_id=club_id, user_id=user_id,
                               start_d=start_d, end_d=end_d)
    earn = _coach_earnings(session, club_id=club_id, user_id=user_id,
                           start_d=start_d, end_d=end_d)
    billed = _coach_billed(session, club_id=club_id, user_id=user_id,
                           start_d=start_d, end_d=end_d)
    arrears = _coach_arrears_owed(session, club_id=club_id, user_id=user_id)
    fill = _coach_fill_rate(session, club_id=club_id, user_id=user_id,
                            start_d=start_d, end_d=end_d)
    clients_new = _coach_new_clients(session, club_id=club_id, user_id=user_id,
                                     start_d=start_d, end_d=end_d)

    kpis = {
        "lessons_count": act["lessons_count"],
        "hours": act["hours"],
        "classes_count": act["classes_count"],
        "billed_minor": billed,
        "gross_minor": earn["gross_minor"],
        "net_minor": earn["net_minor"],
        "commission_minor": earn["commission_minor"],
        "arrears_owed_minor": arrears,
        "fill_rate_pct": fill,
        "clients_active": act["clients_active"],
        "clients_new": clients_new,
        "no_shows": act["no_shows"],
    }
    return {
        "period": ym,
        "kpis": kpis,
        "trend": _coach_trend(session, club_id=club_id, user_id=user_id, months=6),
        "top_clients": _coach_top_clients(session, club_id=club_id, user_id=user_id,
                                          start_d=start_d, end_d=end_d, limit=5),
        "upcoming": _coach_upcoming(session, club_id=club_id, user_id=user_id, limit=6),
        "plan_balances": _coach_plan_balances(session, club_id=club_id, user_id=user_id),
    }


def _coach_plan_balances(session, *, club_id, user_id):
    """Outstanding prepaid pack liability for THIS coach: active wallets bought against this
    coach's lesson packs (token_wallet.coach_user_id = the coach) — total sessions/minutes left
    and how many clients hold them. Lets the cockpit show 'lessons left on clients' plans'.
    Guarded -> zeros if the token engine isn't present."""
    if not _table_present(session, "billing", "token_wallet"):
        return {"wallets": 0, "clients": 0, "sessions_left": 0, "minutes_left": 0}
    try:
        r = session.execute(
            text("""
                SELECT count(*) AS wallets, count(DISTINCT user_id) AS clients,
                       COALESCE(SUM(tokens_remaining),0) AS sessions_left,
                       COALESCE(SUM(minutes_remaining),0) AS minutes_left
                FROM billing.token_wallet
                WHERE club_id = :c AND coach_user_id = :u
                  AND status = 'active' AND COALESCE(minutes_remaining,0) > 0
            """),
            {"c": club_id, "u": str(user_id)},
        ).mappings().first()
        return {"wallets": int(r["wallets"] or 0), "clients": int(r["clients"] or 0),
                "sessions_left": int(r["sessions_left"] or 0),
                "minutes_left": int(r["minutes_left"] or 0)}
    except Exception:
        session.rollback()
        return {"wallets": 0, "clients": 0, "sessions_left": 0, "minutes_left": 0}


# ---------------------------------------------------------------------------
# commission (READ-ONLY for the coach) — the club's cut on the coach's lessons.
# The OWNER sets this in admin (global default / per-coach / per-service); the coach
# only SEES it (greyed in the console). Pure resolution via the commission engine,
# scoped to the calling coach. Guarded: billing absent -> zeros.
# ---------------------------------------------------------------------------
def coach_commission_overview(session, *, club_id, user_id):
    """{club_default_pct, coach_default_pct, effective_pct, currency, services:[{product_id,
    name, effective_pct}]} — what the CLUB keeps on this coach's lessons. coach KEEPS (100-pct)."""
    out = {"club_default_pct": 0.0, "coach_default_pct": 0.0, "effective_pct": 0.0,
           "currency": "ZAR", "services": []}
    try:
        from billing.commission import resolve_commission_pct
        out["currency"] = session.execute(
            text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": club_id}).scalar() or "ZAR"
        club_default = resolve_commission_pct(session, club_id=club_id)
        coach_default = resolve_commission_pct(session, club_id=club_id, coach_user_id=user_id)
        out["club_default_pct"] = float(club_default)
        out["coach_default_pct"] = float(coach_default)
        out["effective_pct"] = float(coach_default)
        rows = session.execute(
            text("SELECT id, name FROM billing.product "
                 "WHERE club_id = :c AND kind = 'lesson' AND coach_user_id = :u AND active = true "
                 "ORDER BY created_at"),
            {"c": club_id, "u": user_id},
        ).mappings().all()
        for r in rows:
            eff = resolve_commission_pct(session, club_id=club_id, product_id=r["id"], coach_user_id=user_id)
            out["services"].append({"product_id": str(r["id"]), "name": r["name"],
                                    "effective_pct": float(eff)})
    except Exception:
        session.rollback()
    return out

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

from sqlalchemy import text


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
                  phone=None, first_name=None, surname=None):
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
                updated_at        = now()
            WHERE club_id = :c AND user_id = :u
        """),
        {"c": club_id, "u": user_id, "display_name": display_name, "headline": headline,
         "bio": bio, "photo_url": photo_url, "specialties": specialties,
         "languages": languages, "qualifications": qualifications,
         "years_experience": years_experience, "is_bookable": is_bookable,
         "public_visibility": public_visibility},
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


def get_client(session, *, club_id, user_id, client_user_id):
    """A single client's 360 WITH THIS COACH only: the same headline counts as the list row
    plus the full session history (lessons + classes, date/type/status/spend). Returns None
    if the user has no coaching relationship with this coach (privacy: can't probe arbitrary
    users — they must appear in this coach's activity)."""
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
    hist_sql = _COACH_ACTIVITY_CTE + """
        SELECT a.kind, a.starts_at, a.status, a.order_id
        FROM activity a
        WHERE a.user_id = :cu
        ORDER BY a.starts_at DESC
        LIMIT 200
    """
    history = _rows(session.execute(text(hist_sql), params).mappings().all())
    head["history"] = history
    return head

# diary/classes.py — class enrolment / waitlist (docs/03 §2.3, §6).
#
# A class_session is a scheduled instance (recurring via diary.recurrence -> generated
# sessions). A member enrols -> diary.enrolment row; capacity enforced; over-capacity ->
# waitlisted. On a cancellation the earliest waitlisted enrolment auto-promotes to
# enrolled (FIFO via enrolment.waitlist_seq) and never exceeds capacity (docs/03 §10).
#
# Concurrency: capacity is enforced by counting enrolled rows under a row lock on the
# class_session (SELECT ... FOR UPDATE) so two simultaneous enrols can't both slip into
# the last seat. The UNIQUE(class_session_id, user_id) stops double-enrolment.
#
# Emits (guarded, via diary.events): class_enrolled, class_waitlisted, waitlist_slot_open.

import logging
from datetime import datetime, timedelta, time as _time

from sqlalchemy import text

from diary import events
from diary.bookings import _create_order_guarded

log = logging.getLogger("diary.classes")


def _err(error, status, **extra):
    d = {"ok": False, "error": error, "status": status}
    d.update(extra)
    return d


def _session_row(session, club_id, class_session_id, lock=False):
    sql = ("SELECT id, club_id, resource_id, coach_user_id, starts_at, ends_at, capacity, "
           "       price_id, status "
           "FROM diary.class_session WHERE club_id=:c AND id=:id")
    if lock:
        sql += " FOR UPDATE"
    row = session.execute(text(sql), {"c": club_id, "id": class_session_id}).mappings().first()
    return dict(row) if row else None


def _enrolled_count(session, class_session_id):
    return session.execute(
        text("SELECT count(*) FROM diary.enrolment "
             "WHERE class_session_id=:id AND status='enrolled'"),
        {"id": class_session_id},
    ).scalar() or 0


def enrol(session, *, club_id, class_session_id, user_id, settlement_mode="at_court",
          audience="member", payer_user_id=None):
    """Enrol a player; over-capacity -> waitlisted. Capacity-safe via FOR UPDATE on the
    session row. Idempotent-ish: a prior cancelled enrolment is reactivated; an existing
    active/waitlisted enrolment is returned as-is.

    `payer_user_id` (My Account / dependents): when a GUARDIAN enrols a CHILD, the enrolment's
    `user_id` is the child (activity → player) while the ORDER is billed to the guardian (spend →
    payer). Defaults to user_id so a normal self-enrolment bills the player themselves — no change."""
    payer_user_id = payer_user_id or user_id
    cs = _session_row(session, club_id, class_session_id, lock=True)
    if not cs:
        return _err("SESSION_NOT_FOUND", 404)
    if cs["status"] != "scheduled":
        return _err("SESSION_CLOSED", 409, status_value=cs["status"])

    existing = session.execute(
        text("SELECT id, status FROM diary.enrolment "
             "WHERE class_session_id=:cs AND user_id=:u"),
        {"cs": class_session_id, "u": user_id},
    ).mappings().first()

    capacity = cs["capacity"] or 0
    enrolled = _enrolled_count(session, class_session_id)
    target = "enrolled" if (capacity == 0 or enrolled < capacity) else "waitlisted"

    if existing and existing["status"] in ("enrolled", "waitlisted"):
        return {"ok": True, "enrolment": _enrolment_dict(session, existing["id"]),
                "status_value": existing["status"]}

    if existing:  # reactivate a previously cancelled enrolment
        session.execute(
            text("UPDATE diary.enrolment SET status=:st, updated_at=now() WHERE id=:id"),
            {"st": target, "id": existing["id"]},
        )
        enrol_id = existing["id"]
    else:
        row = session.execute(
            text("INSERT INTO diary.enrolment (club_id, class_session_id, user_id, status) "
                 "VALUES (:c, :cs, :u, :st) RETURNING id"),
            {"c": club_id, "cs": class_session_id, "u": user_id, "st": target},
        ).mappings().first()
        enrol_id = row["id"]

    # Order only for a real (enrolled) seat; waitlist doesn't bill until promoted.
    if target == "enrolled":
        order = _create_order_guarded(
            session, club_id=club_id, user_id=payer_user_id, booking_id=None,
            booking_type="class", settlement_mode=settlement_mode, parties=[],
            resource_id=cs["resource_id"], starts_at=cs["starts_at"], ends_at=cs["ends_at"],
            enrolment_id=str(enrol_id), audience=audience,
        )
        if order.get("order_id"):
            session.execute(
                text("UPDATE diary.enrolment SET order_id=:o WHERE id=:id"),
                {"o": order["order_id"], "id": enrol_id},
            )

    enrolment = _enrolment_dict(session, enrol_id)
    payload = _payload(cs, enrolment)
    if target == "enrolled":
        events.emit("class_enrolled", payload)
    else:
        events.emit("class_waitlisted", payload)
    return {"ok": True, "enrolment": enrolment, "status_value": target}


def cancel_enrolment(session, *, club_id, class_session_id, user_id, actor_user_id=None):
    """Cancel an enrolment and auto-promote the earliest waitlisted player (FIFO). Never
    exceeds capacity (we only promote when a confirmed seat actually frees)."""
    cs = _session_row(session, club_id, class_session_id, lock=True)
    if not cs:
        return _err("SESSION_NOT_FOUND", 404)
    row = session.execute(
        text("SELECT id, status FROM diary.enrolment "
             "WHERE class_session_id=:cs AND user_id=:u"),
        {"cs": class_session_id, "u": user_id},
    ).mappings().first()
    if not row or row["status"] in ("cancelled",):
        return _err("ENROLMENT_NOT_FOUND", 404)

    was_enrolled = row["status"] == "enrolled"
    session.execute(
        text("UPDATE diary.enrolment SET status='cancelled', updated_at=now() WHERE id=:id"),
        {"id": row["id"]},
    )

    promoted = None
    if was_enrolled:
        promoted = _promote_waitlist(session, club_id=club_id, cs=cs)
    return {"ok": True, "promoted": promoted}


def _promote_waitlist(session, *, club_id, cs):
    """Promote the earliest waitlisted enrolment to enrolled IFF a seat is free. Runs under
    the session lock the caller holds."""
    capacity = cs["capacity"] or 0
    if capacity:
        enrolled = _enrolled_count(session, cs["id"])
        if enrolled >= capacity:
            return None
    nxt = session.execute(
        text("SELECT id, user_id FROM diary.enrolment "
             "WHERE class_session_id=:cs AND status='waitlisted' "
             "ORDER BY waitlist_seq LIMIT 1"),
        {"cs": cs["id"]},
    ).mappings().first()
    if not nxt:
        return None
    session.execute(
        text("UPDATE diary.enrolment SET status='enrolled', updated_at=now() WHERE id=:id"),
        {"id": nxt["id"]},
    )
    enrolment = _enrolment_dict(session, nxt["id"])
    events.emit("waitlist_slot_open", _payload(cs, enrolment))
    events.emit("class_enrolled", _payload(cs, enrolment))
    return str(nxt["id"])


def list_sessions(session, *, club_id, date_from=None, date_to=None, resource_id=None):
    """Class sessions with capacity + spots_left (docs/03 §8 GET /classes)."""
    where = ["cs.club_id = :c", "cs.status = 'scheduled'"]
    params = {"c": club_id}
    if date_from:
        where.append("cs.starts_at >= :df"); params["df"] = date_from
    if date_to:
        where.append("cs.starts_at <= :dt"); params["dt"] = date_to
    if resource_id:
        where.append("cs.resource_id = :rid"); params["rid"] = resource_id
    rows = session.execute(
        text("SELECT cs.id, cs.resource_id, r.name AS class_name, cs.coach_user_id, "
             "       cs.starts_at, cs.ends_at, cs.capacity, cs.price_id, "
             "       pr.amount_minor AS price_minor, "
             "       cu.first_name AS coach_first, cu.surname AS coach_surname, "
             "       cp.display_name AS coach_display, "
             "       (SELECT count(*) FROM diary.enrolment e "
             "          WHERE e.class_session_id = cs.id AND e.status='enrolled') AS enrolled, "
             "       (SELECT count(*) FROM diary.enrolment e "
             "          WHERE e.class_session_id = cs.id AND e.status='waitlisted') AS waitlisted "
             "FROM diary.class_session cs "
             "LEFT JOIN diary.resource r ON r.id = cs.resource_id "
             "LEFT JOIN billing.price pr ON pr.id = cs.price_id "
             "LEFT JOIN iam.user cu ON cu.id = cs.coach_user_id "
             "LEFT JOIN iam.coach_profile cp ON cp.user_id = cs.coach_user_id "
             "       AND cp.club_id = cs.club_id "
             "WHERE " + " AND ".join(where) + " ORDER BY cs.starts_at"),
        params,
    ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        cap = d.get("capacity") or 0
        d["spots_left"] = max(cap - (d["enrolled"] or 0), 0) if cap else None
        d["coach_name"] = (d.pop("coach_display", None)
                           or " ".join(x for x in (d.pop("coach_first", None),
                                                   d.pop("coach_surname", None)) if x).strip()
                           or None)
        d.pop("coach_first", None); d.pop("coach_surname", None); d.pop("coach_display", None)
        for k in ("id", "resource_id", "coach_user_id", "price_id"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        for k in ("starts_at", "ends_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        out.append(d)
    return out


def _enrolment_dict(session, enrol_id):
    row = session.execute(
        text("SELECT id, club_id, class_session_id, user_id, status, order_id, enrolled_at "
             "FROM diary.enrolment WHERE id=:id"),
        {"id": enrol_id},
    ).mappings().first()
    if not row:
        return None
    d = dict(row)
    for k in ("id", "club_id", "class_session_id", "user_id", "order_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    if d.get("enrolled_at") is not None:
        d["enrolled_at"] = d["enrolled_at"].isoformat()
    return d


def _payload(cs, enrolment):
    return {
        "club_id": str(cs["club_id"]),
        "user_id": (enrolment or {}).get("user_id"),
        "class_session_id": str(cs["id"]),
        "starts_at": cs["starts_at"].isoformat() if hasattr(cs["starts_at"], "isoformat") else cs["starts_at"],
        "ends_at": cs["ends_at"].isoformat() if hasattr(cs["ends_at"], "isoformat") else cs["ends_at"],
    }


# ===========================================================================
# class TYPES (admin/coach create) + session SCHEDULING + roster/attendance.
#
# A "class type" is the template a member enrols in occurrences of:
#   diary.resource(kind='class', coach_user_id, capacity)
#   + billing.product(kind='class', coach_user_id)
#   + billing.price(audience='any', unit='per_session', duration_minutes).
# Scheduling generates diary.class_session rows (one per occurrence), idempotent on
# (resource_id, starts_at) so re-running a schedule skips duplicates. Enrolment +
# waitlist + promotion are the existing functions above — unchanged.
# ===========================================================================

_TZ_CACHE = {}


def _club_tz(session, club_id):
    """The club's IANA timezone (default Africa/Johannesburg). Cached per club_id."""
    key = str(club_id)
    if key in _TZ_CACHE:
        return _TZ_CACHE[key]
    tz_name = session.execute(
        text("SELECT timezone FROM club.club WHERE id = :c"), {"c": club_id},
    ).scalar() or "Africa/Johannesburg"
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:
        from datetime import timezone as _utc
        tz = _utc.utc
    _TZ_CACHE[key] = tz
    return tz


def _club_currency(session, club_id):
    return session.execute(
        text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": club_id},
    ).scalar() or "ZAR"


def _parse_time(s):
    """'HH:MM' (or 'HH:MM:SS') -> datetime.time. Raises ValueError on a bad value."""
    parts = str(s).strip().split(":")
    hh = int(parts[0]); mm = int(parts[1]) if len(parts) > 1 else 0
    ss = int(parts[2]) if len(parts) > 2 else 0
    return _time(hour=hh, minute=mm, second=ss)


def _parse_date(s):
    """'YYYY-MM-DD' -> datetime.date."""
    return datetime.strptime(str(s).strip()[:10], "%Y-%m-%d").date()


def class_type_dict(session, *, club_id, resource_id):
    """The {resource_id,name,coach_user_id,capacity,price_id,price_amount_minor,
    duration_minutes} contract shape for one class type, or None."""
    row = session.execute(
        text("""
            SELECT r.id AS resource_id, r.name, r.coach_user_id, r.capacity,
                   pr.id AS price_id, pr.amount_minor AS price_amount_minor,
                   pr.duration_minutes
            FROM diary.resource r
            LEFT JOIN billing.product p
                   ON p.club_id = r.club_id AND p.kind = 'class'
                  AND p.coach_user_id IS NOT DISTINCT FROM r.coach_user_id
                  AND lower(p.name) = lower(r.name) AND p.active = true
            LEFT JOIN billing.price pr
                   ON pr.product_id = p.id AND pr.club_id = p.club_id AND pr.active = true
            WHERE r.club_id = :c AND r.id = :r AND r.kind = 'class'
            ORDER BY pr.created_at
            LIMIT 1
        """),
        {"c": club_id, "r": resource_id},
    ).mappings().first()
    if not row:
        return None
    d = dict(row)
    for k in ("resource_id", "coach_user_id", "price_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    return d


def create_class_type(session, *, club_id, name, capacity, price_amount_minor,
                      duration_minutes, coach_user_id=None, description=None):
    """Create a class type = resource(kind='class') + product(kind='class') + price.
    Returns {class:{...}}. Plain SQL (we don't import billing modules — docs lane rules)."""
    if not name:
        return _err("NAME_REQUIRED", 400)
    cap = int(capacity or 0)
    dur = int(duration_minutes or 0)
    amt = int(price_amount_minor or 0)
    rid = session.execute(
        text("INSERT INTO diary.resource (club_id, kind, name, coach_user_id, capacity) "
             "VALUES (:c, 'class', :n, :coach, :cap) RETURNING id"),
        {"c": club_id, "n": name, "coach": coach_user_id, "cap": cap},
    ).scalar_one()
    pid = session.execute(
        text("INSERT INTO billing.product (club_id, kind, name, description, coach_user_id, "
             "active) VALUES (:c, 'class', :n, :d, :coach, true) RETURNING id"),
        {"c": club_id, "n": name, "d": description, "coach": coach_user_id},
    ).scalar_one()
    price_id = session.execute(
        text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
             "currency_code, unit, duration_minutes, active) "
             "VALUES (:c, :p, 'any', :amt, :cur, 'per_session', :dur, true) RETURNING id"),
        {"c": club_id, "p": pid, "amt": amt, "cur": _club_currency(session, club_id),
         "dur": dur},
    ).scalar_one()
    return {"ok": True, "class": {
        "resource_id": str(rid), "name": name,
        "coach_user_id": str(coach_user_id) if coach_user_id else None,
        "capacity": cap, "price_id": str(price_id),
        "price_amount_minor": amt, "duration_minutes": dur,
    }}


def list_class_types(session, *, club_id, coach_user_id=None):
    """List class types (one row per class resource) with the joined product/price + a count
    of upcoming non-cancelled sessions. Filter to a coach's own when coach_user_id is set."""
    where = ["r.club_id = :c", "r.kind = 'class'", "r.is_active = true"]
    params = {"c": club_id}
    if coach_user_id is not None:
        where.append("r.coach_user_id = :coach"); params["coach"] = coach_user_id
    rows = session.execute(
        text("""
            SELECT r.id AS resource_id, r.name, r.coach_user_id, r.capacity,
                   pr.id AS price_id, pr.amount_minor AS price_amount_minor,
                   pr.duration_minutes,
                   cu.first_name AS coach_first, cu.surname AS coach_surname,
                   cp.display_name AS coach_display,
                   (SELECT count(*) FROM diary.class_session cs
                      WHERE cs.club_id = r.club_id AND cs.resource_id = r.id
                        AND cs.status = 'scheduled' AND cs.starts_at >= now())
                       AS upcoming_sessions
            FROM diary.resource r
            LEFT JOIN billing.product p
                   ON p.club_id = r.club_id AND p.kind = 'class'
                  AND p.coach_user_id IS NOT DISTINCT FROM r.coach_user_id
                  AND lower(p.name) = lower(r.name) AND p.active = true
            LEFT JOIN billing.price pr
                   ON pr.product_id = p.id AND pr.club_id = p.club_id AND pr.active = true
            LEFT JOIN iam.user cu ON cu.id = r.coach_user_id
            LEFT JOIN iam.coach_profile cp
                   ON cp.user_id = r.coach_user_id AND cp.club_id = r.club_id
            WHERE """ + " AND ".join(where) + """
            ORDER BY r.rank, r.name
        """),
        params,
    ).mappings().all()
    out = []
    seen = set()
    for r in rows:
        d = dict(r)
        if d["resource_id"] in seen:  # one row per resource even if >1 price
            continue
        seen.add(d["resource_id"])
        coach_name = (d.pop("coach_display", None)
                      or " ".join(x for x in (d.pop("coach_first", None),
                                              d.pop("coach_surname", None)) if x).strip()
                      or None)
        d["coach_name"] = coach_name
        d.pop("coach_first", None); d.pop("coach_surname", None); d.pop("coach_display", None)
        for k in ("resource_id", "coach_user_id", "price_id"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        out.append(d)
    return out


def _resource_for_schedule(session, *, club_id, resource_id):
    return session.execute(
        text("SELECT id, club_id, name, coach_user_id, capacity FROM diary.resource "
             "WHERE club_id = :c AND id = :r AND kind = 'class'"),
        {"c": club_id, "r": resource_id},
    ).mappings().first()


def _class_price_id(session, *, club_id, resource_id, name, coach_user_id):
    row = session.execute(
        text("""
            SELECT pr.id, pr.duration_minutes
            FROM billing.product p
            JOIN billing.price pr ON pr.product_id = p.id AND pr.club_id = p.club_id
            WHERE p.club_id = :c AND p.kind = 'class' AND p.active = true
              AND pr.active = true AND lower(p.name) = lower(:n)
              AND p.coach_user_id IS NOT DISTINCT FROM :coach
            ORDER BY pr.created_at LIMIT 1
        """),
        {"c": club_id, "n": name, "coach": coach_user_id},
    ).mappings().first()
    return (str(row["id"]) if row else None,
            (row["duration_minutes"] if row else None))


def schedule_sessions(session, *, club_id, resource_id, weekdays=None, start_time=None,
                      date_from=None, date_until=None, dates=None, duration_minutes=None,
                      capacity=None, price_id=None):
    """Generate diary.class_session rows for a class resource. Two modes:
      recurring: {weekdays:[0-6], start_time:'HH:MM', date_from, date_until}
      one-off:   {dates:['YYYY-MM-DD'], start_time:'HH:MM'}
    Idempotent on (resource_id, starts_at) — an existing session at that start is skipped.
    coach_user_id + (default) capacity + price come from the class type. Returns
    {created, skipped}."""
    res = _resource_for_schedule(session, club_id=club_id, resource_id=resource_id)
    if not res:
        return _err("CLASS_NOT_FOUND", 404)

    default_price_id, price_dur = _class_price_id(
        session, club_id=club_id, resource_id=resource_id,
        name=res["name"], coach_user_id=res["coach_user_id"])
    eff_price_id = price_id or default_price_id
    eff_capacity = int(capacity) if capacity is not None else int(res["capacity"] or 0)
    eff_dur = int(duration_minutes) if duration_minutes else int(price_dur or 0)
    if not eff_dur:
        return _err("DURATION_REQUIRED", 400)

    tz = _club_tz(session, club_id)
    try:
        st = _parse_time(start_time) if start_time else None
    except (ValueError, TypeError, IndexError):
        return _err("BAD_START_TIME", 400)

    # Build the list of local-date occurrences.
    occ_dates = []
    if dates:
        try:
            occ_dates = [_parse_date(d) for d in dates]
        except (ValueError, TypeError):
            return _err("BAD_DATES", 400)
    elif weekdays is not None and date_from and date_until:
        try:
            d0 = _parse_date(date_from); d1 = _parse_date(date_until)
        except (ValueError, TypeError):
            return _err("BAD_DATE_RANGE", 400)
        wd = {int(w) for w in weekdays}  # 0=Mon..6=Sun (Python weekday())
        cur = d0
        guard = 0
        while cur <= d1 and guard < 1000:
            if cur.weekday() in wd:
                occ_dates.append(cur)
            cur += timedelta(days=1)
            guard += 1
    else:
        return _err("SCHEDULE_SPEC_REQUIRED", 400)

    if st is None:
        return _err("START_TIME_REQUIRED", 400)

    created = 0
    skipped = 0
    for d in occ_dates:
        starts_at = datetime(d.year, d.month, d.day, st.hour, st.minute, st.second, tzinfo=tz)
        ends_at = starts_at + timedelta(minutes=eff_dur)
        exists = session.execute(
            text("SELECT 1 FROM diary.class_session "
                 "WHERE club_id = :c AND resource_id = :r AND starts_at = :sa"),
            {"c": club_id, "r": resource_id, "sa": starts_at},
        ).first()
        if exists:
            skipped += 1
            continue
        session.execute(
            text("INSERT INTO diary.class_session (club_id, resource_id, coach_user_id, "
                 "starts_at, ends_at, capacity, price_id, status) "
                 "VALUES (:c, :r, :coach, :sa, :ea, :cap, :pid, 'scheduled')"),
            {"c": club_id, "r": resource_id, "coach": res["coach_user_id"],
             "sa": starts_at, "ea": ends_at, "cap": eff_capacity, "pid": eff_price_id},
        )
        created += 1
    return {"ok": True, "created": created, "skipped": skipped}


def list_type_sessions(session, *, club_id, resource_id, date_from=None, date_to=None):
    """Sessions for one class type (any status), with enrolled/waitlisted/spots_left. Used by
    the admin/coach 'manage this class' view (GET .../sessions)."""
    where = ["cs.club_id = :c", "cs.resource_id = :r"]
    params = {"c": club_id, "r": resource_id}
    if date_from:
        where.append("cs.starts_at >= CAST(:df AS timestamptz)"); params["df"] = date_from
    if date_to:
        where.append("cs.starts_at <= CAST(:dt AS timestamptz)"); params["dt"] = date_to
    rows = session.execute(
        text("""
            SELECT cs.id AS session_id, cs.starts_at, cs.ends_at, cs.capacity, cs.status,
                   (SELECT count(*) FROM diary.enrolment e
                      WHERE e.class_session_id = cs.id AND e.status = 'enrolled') AS enrolled,
                   (SELECT count(*) FROM diary.enrolment e
                      WHERE e.class_session_id = cs.id AND e.status = 'waitlisted') AS waitlisted
            FROM diary.class_session cs
            WHERE """ + " AND ".join(where) + " ORDER BY cs.starts_at"),
        params,
    ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        cap = d.get("capacity") or 0
        d["spots_left"] = max(cap - (d["enrolled"] or 0), 0) if cap else None
        d["session_id"] = str(d["session_id"])
        for k in ("starts_at", "ends_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        out.append(d)
    return out


def cancel_session(session, *, club_id, session_id):
    """Cancel a class_session: status='cancelled'; best-effort emit('class_cancelled')."""
    cs = _session_row(session, club_id, session_id, lock=True)
    if not cs:
        return _err("SESSION_NOT_FOUND", 404)
    session.execute(
        text("UPDATE diary.class_session SET status='cancelled', updated_at=now() "
             "WHERE club_id=:c AND id=:id"),
        {"c": club_id, "id": session_id},
    )
    try:
        events.emit("class_cancelled", {
            "club_id": str(club_id), "class_session_id": str(session_id),
            "resource_id": str(cs["resource_id"]),
            "starts_at": cs["starts_at"].isoformat() if hasattr(cs["starts_at"], "isoformat") else cs["starts_at"],
        })
    except Exception:
        log.debug("class_cancelled emit skipped")
    return {"ok": True, "session_id": str(session_id), "status_value": "cancelled"}


def session_owner_coach(session, *, club_id, session_id):
    """coach_user_id (str) of a session's class, or None — for coach ownership gating."""
    cs = _session_row(session, club_id, session_id)
    if not cs:
        return None, None
    return (str(cs["coach_user_id"]) if cs.get("coach_user_id") else None, cs)


def roster(session, *, club_id, session_id):
    """{enrolled:[{user_id,name,email,status}], waitlisted:[...]} for a class session."""
    rows = session.execute(
        text("""
            SELECT e.user_id, e.status, u.first_name, u.surname, u.email
            FROM diary.enrolment e
            LEFT JOIN iam.user u ON u.id = e.user_id
            WHERE e.club_id = :c AND e.class_session_id = :s
              AND e.status IN ('enrolled','waitlisted','attended','no_show')
            ORDER BY e.status, e.waitlist_seq
        """),
        {"c": club_id, "s": session_id},
    ).mappings().all()
    enrolled, waitlisted = [], []
    for r in rows:
        name = " ".join(x for x in (r.get("first_name"), r.get("surname")) if x).strip() or None
        entry = {"user_id": str(r["user_id"]) if r.get("user_id") else None,
                 "name": name, "email": r.get("email"), "status": r["status"]}
        if r["status"] == "waitlisted":
            waitlisted.append(entry)
        else:
            enrolled.append(entry)
    return {"ok": True, "enrolled": enrolled, "waitlisted": waitlisted}


def mark_attendance(session, *, club_id, session_id, user_id, attended):
    """Mark an enrolment attended/no_show. 'attended' True -> status='attended'; False ->
    'no_show'. The enrolment must exist for this (session, user)."""
    cs = _session_row(session, club_id, session_id)
    if not cs:
        return _err("SESSION_NOT_FOUND", 404)
    new_status = "attended" if attended else "no_show"
    row = session.execute(
        text("UPDATE diary.enrolment SET status=:st, updated_at=now() "
             "WHERE club_id=:c AND class_session_id=:s AND user_id=:u "
             "AND status IN ('enrolled','attended','no_show') RETURNING id"),
        {"st": new_status, "c": club_id, "s": session_id, "u": user_id},
    ).mappings().first()
    if not row:
        return _err("ENROLMENT_NOT_FOUND", 404)
    return {"ok": True, "user_id": str(user_id), "status_value": new_status}


def master_class_events(session, *, club_id, date_from=None, date_to=None):
    """Class sessions shaped as master-diary events (alongside bookings). Non-cancelled by
    default; includes enrolled + capacity so the admin calendar can show fill."""
    rows = session.execute(
        text("""
            SELECT cs.id, cs.resource_id, r.name AS resource_name, cs.coach_user_id,
                   cs.starts_at, cs.ends_at, cs.status, cs.capacity,
                   (SELECT count(*) FROM diary.enrolment e
                      WHERE e.class_session_id = cs.id AND e.status = 'enrolled') AS enrolled
            FROM diary.class_session cs
            LEFT JOIN diary.resource r ON r.id = cs.resource_id
            WHERE cs.club_id = :c AND cs.status IN ('scheduled','completed')
              AND (CAST(:df AS timestamptz) IS NULL OR cs.starts_at >= CAST(:df AS timestamptz))
              AND (CAST(:dt AS timestamptz) IS NULL OR cs.starts_at <= CAST(:dt AS timestamptz))
            ORDER BY cs.starts_at
        """),
        {"c": club_id, "df": date_from, "dt": date_to},
    ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["booking_type"] = "class"
        for k in ("id", "resource_id", "coach_user_id"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        for k in ("starts_at", "ends_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        out.append(d)
    return out

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
          audience="member"):
    """Enrol a player; over-capacity -> waitlisted. Capacity-safe via FOR UPDATE on the
    session row. Idempotent-ish: a prior cancelled enrolment is reactivated; an existing
    active/waitlisted enrolment is returned as-is."""
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
            session, club_id=club_id, user_id=user_id, booking_id=None,
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
             "       (SELECT count(*) FROM diary.enrolment e "
             "          WHERE e.class_session_id = cs.id AND e.status='enrolled') AS enrolled, "
             "       (SELECT count(*) FROM diary.enrolment e "
             "          WHERE e.class_session_id = cs.id AND e.status='waitlisted') AS waitlisted "
             "FROM diary.class_session cs "
             "LEFT JOIN diary.resource r ON r.id = cs.resource_id "
             "WHERE " + " AND ".join(where) + " ORDER BY cs.starts_at"),
        params,
    ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        cap = d.get("capacity") or 0
        d["spots_left"] = max(cap - (d["enrolled"] or 0), 0) if cap else None
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

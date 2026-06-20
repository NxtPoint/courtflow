# diary/bookings.py — concurrency-safe booking creation, reschedule, cancel, status.
#
# The cardinal sin is double-booking (docs/03 §4). Two guarantees:
#   1. DB-level EXCLUDE constraint on diary.booking (diary/schema.py) physically refuses
#      an overlapping held/confirmed row for a resource.
#   2. We catch that violation and translate it to a clean 409 SLOT_TAKEN result, instead
#      of leaking a 500.
#
# This module returns RESULT dicts (not HTTP) — routes.py maps them to responses:
#     {"ok": True,  "booking": {...}, "checkout": {...}|None}
#     {"ok": False, "error": "SLOT_TAKEN"|"GUEST_REQUIRES_HOST"|..., "status": 409, ...}
#
# Settlement (docs/05 §5): at_court/monthly/membership/free -> confirm immediately;
# online -> keep 'held' with held_until + return a checkout intent (Agent C).
#
# Cross-lane (LAZY + GUARDED so this lane self-verifies in isolation):
#   billing.orders.create_order_for_booking(session, *, club_id, user_id, booking_id,
#       booking_type, settlement_mode, parties, resource_id, starts_at, ends_at,
#       linked_booking_id=None) -> {"order_id": str, "status": str,
#                                   "checkout": {...}|None, "amount_minor": int}
#   marketing_crm.tracking.emit(event, payload)   (via diary.events.emit)

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from diary import events
from diary.schema import EXCLUSION_CONSTRAINT

log = logging.getLogger("diary.bookings")

HOLD_MINUTES_DEFAULT = 5  # short-lived hold for online checkout (docs/03 §4)

# Settlement modes that confirm immediately (no gateway round-trip).
_IMMEDIATE_CONFIRM = ("at_court", "monthly_account", "membership_covered", "free")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _err(error, status, **extra):
    d = {"ok": False, "error": error, "status": status}
    d.update(extra)
    return d


def _is_slot_taken(exc):
    """True if an IntegrityError is our exclusion-constraint violation (overlap)."""
    msg = str(getattr(exc, "orig", exc)).lower()
    return EXCLUSION_CONSTRAINT in msg or "exclusion constraint" in msg or "overlap" in msg


def _resource(session, club_id, resource_id):
    row = session.execute(
        text("SELECT id, club_id, kind, name, surface, capacity, coach_user_id, is_active "
             "FROM diary.resource WHERE club_id = :c AND id = :rid"),
        {"c": club_id, "rid": resource_id},
    ).mappings().first()
    return dict(row) if row else None


def _policy(session, club_id):
    row = session.execute(
        text("SELECT booking_window_days, min_booking_minutes, cancellation_cutoff_hours, "
             "no_show_fee_minor, guest_requires_member, allow_pay_at_court, "
             "allow_monthly_account, allow_online_payment "
             "FROM club.policy WHERE club_id = :c"),
        {"c": club_id},
    ).mappings().first()
    return dict(row) if row else {}


def _booking_dict(session, booking_id):
    row = session.execute(
        text("SELECT id, club_id, booking_type, resource_id, coach_user_id, starts_at, "
             "ends_at, status, held_until, booked_by_user_id, recurrence_id, order_id, "
             "settlement_mode, notes, cancellation_reason, cancelled_at, cancelled_by "
             "FROM diary.booking WHERE id = :id"),
        {"id": booking_id},
    ).mappings().first()
    if not row:
        return None
    d = dict(row)
    for k in ("id", "club_id", "resource_id", "coach_user_id", "booked_by_user_id",
              "recurrence_id", "order_id", "cancelled_by"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    for k in ("starts_at", "ends_at", "held_until", "cancelled_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


def _parse_dt(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _settlement_allowed(mode, policy, role):
    """A member sees only what the club allows; admins/coaches may force any mode."""
    if role in ("club_admin", "platform_admin", "coach"):
        return True
    if mode == "at_court":
        return bool(policy.get("allow_pay_at_court", True))
    if mode == "monthly_account":
        return bool(policy.get("allow_monthly_account", True))
    if mode == "online":
        return bool(policy.get("allow_online_payment", False))
    if mode in ("membership_covered", "free"):
        return True
    return False


# booking_type -> billing.product.kind (docs/02 §5). The diary speaks booking types;
# billing speaks product kinds. This adapter is the single translation point.
_KIND_BY_BOOKING_TYPE = {"court": "court_booking", "lesson": "lesson", "class": "class"}


def _create_order_guarded(session, *, club_id, user_id, booking_id=None, booking_type="court",
                          settlement_mode="at_court", parties=None, resource_id=None,
                          starts_at=None, ends_at=None, linked_booking_id=None,
                          audience="member", enrolment_id=None):
    """Adapter between the diary and Agent C's billing.orders.create_order_for_booking.

    The diary speaks bookings; billing speaks order *lines*. We translate here: price each
    party (or the booking) via diary.pricing.price_for, assemble C's `lines`, call C (which
    returns an order_id str), and return the dict the diary callers expect
    {order_id, status, checkout, amount_minor}.

    Guarded: if billing isn't present (self-verify mode) the booking still succeeds with no
    order; if pricing isn't seeded yet, lines carry amount 0 (admin sets price later)."""
    try:
        from billing.orders import create_order_for_booking, booking_status_for_mode
    except Exception:
        log.debug("billing.orders absent — booking proceeds without an order (self-verify mode)")
        return {"order_id": None, "status": "open", "checkout": None, "amount_minor": None}

    from diary.pricing import price_for
    kind = _KIND_BY_BOOKING_TYPE.get(booking_type, "court_booking")
    ref = {"booking_id": booking_id, "enrolment_id": enrolment_id}
    parties = parties or []

    # One billing line per party (priced by that party's audience), else a single line for
    # the booker's audience. The linked lesson court shares the order but isn't billed twice.
    lines = []
    if parties:
        for p in parties:
            aud = "guest" if (p.get("party_role") == "guest" or p.get("guest_name")) else "member"
            pr = price_for(session, club_id=club_id, audience=aud, kind=kind) or {}
            lines.append({"description": f"{booking_type} ({aud})", "price_id": pr.get("price_id"),
                          "qty": 1, "amount_minor": pr.get("amount_minor") or 0, **ref})
    else:
        pr = price_for(session, club_id=club_id, audience=audience, kind=kind) or {}
        lines.append({"description": booking_type, "price_id": pr.get("price_id"),
                      "qty": 1, "amount_minor": pr.get("amount_minor") or 0, **ref})

    try:
        order_id = create_order_for_booking(
            session, club_id=club_id, user_id=user_id, lines=lines,
            settlement_mode=settlement_mode)
        total = sum(int(l["amount_minor"]) * int(l.get("qty") or 1) for l in lines)
        return {"order_id": str(order_id) if order_id else None,
                "status": booking_status_for_mode(settlement_mode),
                "checkout": None,  # gateway checkout intent comes later (Phase 7, online mode)
                "amount_minor": total}
    except Exception:
        log.warning("create_order_for_booking failed — booking kept, order deferred", exc_info=False)
        return {"order_id": None, "status": "open", "checkout": None, "amount_minor": None}


# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------

def create_booking(session, *, club_id, booked_by_user_id, role, booking_type, resource_id,
                   starts_at, ends_at, settlement_mode="at_court", parties=None,
                   coach_user_id=None, court_resource_id=None, audience="member",
                   notes=None, recurrence_id=None, hold_minutes=HOLD_MINUTES_DEFAULT,
                   now=None):
    """Create a court/lesson/class booking, concurrency-safe (docs/03 §4).

    For a lesson, pass court_resource_id to auto-hold a court in the SAME transaction (two
    diary.booking rows sharing one order_id). The whole thing is one tx: if EITHER hold
    overlaps, the exclusion constraint aborts the tx -> we report SLOT_TAKEN and nothing
    is persisted (the original/other slot is untouched).
    """
    now = now or datetime.now(timezone.utc)
    parties = parties or []
    starts = _parse_dt(starts_at)
    ends = _parse_dt(ends_at)
    if ends <= starts:
        return _err("BAD_RANGE", 400, message="ends_at must be after starts_at")
    if starts < now:
        return _err("IN_THE_PAST", 400, message="cannot book a past slot")

    res = _resource(session, club_id, resource_id)
    if not res or not res["is_active"]:
        return _err("RESOURCE_NOT_FOUND", 404)

    policy = _policy(session, club_id)

    # Booking window + min duration (members; admins/coaches relax).
    if role in ("member", "guest"):
        window_days = policy.get("booking_window_days") or 14
        if starts > now + timedelta(days=window_days):
            return _err("OUTSIDE_BOOKING_WINDOW", 422,
                        message=f"can't book more than {window_days} days ahead")
        min_min = policy.get("min_booking_minutes") or 60
        if (ends - starts) < timedelta(minutes=min_min):
            return _err("TOO_SHORT", 422, message=f"minimum booking is {min_min} minutes")

    if not _settlement_allowed(settlement_mode, policy, role):
        return _err("SETTLEMENT_NOT_ALLOWED", 422, settlement_mode=settlement_mode)

    # Member-guest guard (docs/03 §10): if a guest party is present and the club requires a
    # member host, exactly one party must be party_role='host'.
    has_guest = any((p.get("party_role") == "guest") or p.get("guest_name") for p in parties)
    if has_guest and policy.get("guest_requires_member", True):
        has_host = any(p.get("party_role") == "host" for p in parties)
        if not has_host:
            return _err("GUEST_REQUIRES_HOST", 422,
                        message="a member host is required for a guest booking")

    online = (settlement_mode == "online")
    status = "held" if online else "confirmed"
    held_until = (now + timedelta(minutes=hold_minutes)) if online else None
    coach_uid = coach_user_id or (res["coach_user_id"] if res["kind"] == "coach" else None)

    # --- the concurrency-safe insert(s) inside one transaction ----------
    try:
        with session.begin_nested():  # SAVEPOINT — lets us catch the overlap cleanly
            booking_id = _insert_booking(
                session, club_id=club_id, booking_type=booking_type, resource_id=resource_id,
                coach_user_id=coach_uid, starts_at=starts, ends_at=ends, status=status,
                held_until=held_until, booked_by_user_id=booked_by_user_id,
                recurrence_id=recurrence_id, settlement_mode=settlement_mode, notes=notes,
            )
            linked_court_id = None
            if booking_type == "lesson" and court_resource_id:
                court = _resource(session, club_id, court_resource_id)
                if not court or not court["is_active"]:
                    return _err("COURT_NOT_FOUND", 404)
                linked_court_id = _insert_booking(
                    session, club_id=club_id, booking_type="court",
                    resource_id=court_resource_id, coach_user_id=coach_uid,
                    starts_at=starts, ends_at=ends, status=status, held_until=held_until,
                    booked_by_user_id=booked_by_user_id, recurrence_id=recurrence_id,
                    settlement_mode=settlement_mode, notes="(court held for lesson)",
                )
            for p in parties:
                _insert_party(session, booking_id=booking_id, club_id=club_id, party=p)
    except IntegrityError as e:
        # The overlap (or the linked court's overlap) — nothing persisted past the savepoint.
        if _is_slot_taken(e):
            return _err("SLOT_TAKEN", 409, message="that slot was just taken")
        log.exception("booking insert integrity error")
        return _err("INTEGRITY_ERROR", 409)

    # --- order / settlement (guarded billing call) ----------------------
    order = _create_order_guarded(
        session, club_id=club_id, user_id=booked_by_user_id, booking_id=booking_id,
        booking_type=booking_type, settlement_mode=settlement_mode, parties=parties,
        resource_id=resource_id, starts_at=starts, ends_at=ends,
        linked_booking_id=linked_court_id, audience=audience,
    )
    order_id = order.get("order_id")
    if order_id:
        _attach_order(session, booking_id, order_id)
        if linked_court_id:
            _attach_order(session, linked_court_id, order_id)

    booking = _booking_dict(session, booking_id)

    # Online stays held -> return checkout; everything else is confirmed -> emit.
    if online:
        return {"ok": True, "booking": booking, "checkout": order.get("checkout")}

    _emit_confirmed(session, booking, res, settlement_mode)
    return {"ok": True, "booking": booking, "checkout": None}


def _insert_booking(session, *, club_id, booking_type, resource_id, coach_user_id,
                    starts_at, ends_at, status, held_until, booked_by_user_id,
                    recurrence_id, settlement_mode, notes):
    row = session.execute(
        text("INSERT INTO diary.booking "
             "(club_id, booking_type, resource_id, coach_user_id, starts_at, ends_at, "
             " status, held_until, booked_by_user_id, recurrence_id, settlement_mode, notes) "
             "VALUES (:c, :bt, :rid, :coach, :sa, :ea, :st, :hu, :by, :rec, :sm, :notes) "
             "RETURNING id"),
        {"c": club_id, "bt": booking_type, "rid": resource_id, "coach": coach_user_id,
         "sa": starts_at, "ea": ends_at, "st": status, "hu": held_until,
         "by": booked_by_user_id, "rec": recurrence_id, "sm": settlement_mode, "notes": notes},
    ).mappings().first()
    return row["id"]


def _insert_party(session, *, booking_id, club_id, party):
    session.execute(
        text("INSERT INTO diary.booking_party "
             "(booking_id, club_id, user_id, party_role, guest_name, guest_email, price_id) "
             "VALUES (:b, :c, :u, :pr, :gn, :ge, :pid)"),
        {"b": booking_id, "c": club_id, "u": party.get("user_id"),
         "pr": party.get("party_role", "player"), "gn": party.get("guest_name"),
         "ge": party.get("guest_email"), "pid": party.get("price_id")},
    )


def _attach_order(session, booking_id, order_id):
    session.execute(
        text("UPDATE diary.booking SET order_id = :o, updated_at = now() WHERE id = :id"),
        {"o": order_id, "id": booking_id},
    )


# ---------------------------------------------------------------------------
# CONFIRM (online webhook path calls this after payment)
# ---------------------------------------------------------------------------

def confirm_held_booking(session, *, club_id, booking_id):
    """Promote a held booking (+ any linked court sharing its order_id) to confirmed.
    Called by the billing webhook path on charge_succeeded (docs/05 §3)."""
    bk = _booking_dict(session, booking_id)
    if not bk or bk["club_id"] != str(club_id):
        return _err("NOT_FOUND", 404)
    if bk["status"] not in ("held", "confirmed"):
        return _err("BAD_STATE", 409, status_value=bk["status"])
    session.execute(
        text("UPDATE diary.booking SET status='confirmed', held_until=NULL, updated_at=now() "
             "WHERE club_id=:c AND (id=:id OR (order_id IS NOT NULL AND order_id=:oid)) "
             "  AND status='held'"),
        {"c": club_id, "id": booking_id, "oid": bk.get("order_id")},
    )
    booking = _booking_dict(session, booking_id)
    res = _resource(session, club_id, booking["resource_id"])
    _emit_confirmed(session, booking, res, booking.get("settlement_mode"))
    return {"ok": True, "booking": booking}


# ---------------------------------------------------------------------------
# RESCHEDULE (atomic move, conflict-checked)
# ---------------------------------------------------------------------------

def reschedule_booking(session, *, club_id, booking_id, new_starts_at, new_ends_at,
                       actor_user_id, role, scope="this", now=None):
    """Atomically move a booking to a new time. The exclusion constraint validates the new
    slot; on conflict we roll back the savepoint so the ORIGINAL time is preserved
    (docs/03 §10). Honours cancellation_cutoff for member-initiated moves; admins/coaches
    override. `scope` ∈ this|this_future|series for recurring (docs/03 §5)."""
    now = now or datetime.now(timezone.utc)
    bk = _booking_dict(session, booking_id)
    if not bk or bk["club_id"] != str(club_id):
        return _err("NOT_FOUND", 404)
    if bk["status"] not in ("held", "confirmed"):
        return _err("BAD_STATE", 409, status_value=bk["status"])

    new_s = _parse_dt(new_starts_at)
    new_e = _parse_dt(new_ends_at)
    if new_e <= new_s:
        return _err("BAD_RANGE", 400)
    if new_s < now:
        return _err("IN_THE_PAST", 400)

    if role in ("member", "guest"):
        cutoff_h = _policy(session, club_id).get("cancellation_cutoff_hours") or 0
        if _parse_dt(bk["starts_at"]) - now < timedelta(hours=cutoff_h):
            return _err("PAST_CUTOFF", 422,
                        message=f"reschedule not allowed within {cutoff_h}h of start")

    # series / this_future reschedule shifts each affected occurrence by the same delta.
    targets = _reschedule_targets(session, bk, scope)
    delta = new_s - _parse_dt(bk["starts_at"])

    try:
        with session.begin_nested():
            for t_id, t_start, t_end in targets:
                ts = (new_s, new_e) if t_id == booking_id else (
                    _parse_dt(t_start) + delta, _parse_dt(t_end) + delta)
                session.execute(
                    text("UPDATE diary.booking SET starts_at=:sa, ends_at=:ea, updated_at=now() "
                         "WHERE id=:id"),
                    {"sa": ts[0], "ea": ts[1], "id": t_id},
                )
                # Move a linked court (same order_id, different resource) too.
                if bk.get("order_id"):
                    session.execute(
                        text("UPDATE diary.booking SET starts_at=:sa, ends_at=:ea, updated_at=now() "
                             "WHERE club_id=:c AND order_id=:o AND id<>:id "
                             "  AND status IN ('held','confirmed')"),
                        {"sa": ts[0], "ea": ts[1], "c": club_id, "o": bk["order_id"], "id": t_id},
                    )
    except IntegrityError as e:
        if _is_slot_taken(e):
            return _err("SLOT_TAKEN", 409, message="the new slot conflicts")
        return _err("INTEGRITY_ERROR", 409)

    booking = _booking_dict(session, booking_id)
    res = _resource(session, club_id, booking["resource_id"])
    events.emit("booking_rescheduled", _payload(booking, res))
    return {"ok": True, "booking": booking}


def _reschedule_targets(session, bk, scope):
    """Return [(id, starts_at, ends_at)] to move. 'this' = just this booking; 'series'/
    'this_future' = the recurrence_id group (from this start onward for this_future)."""
    if scope == "this" or not bk.get("recurrence_id"):
        return [(bk["id"], bk["starts_at"], bk["ends_at"])]
    sql = ("SELECT id, starts_at, ends_at FROM diary.booking "
           "WHERE recurrence_id = :rec AND status IN ('held','confirmed')")
    params = {"rec": bk["recurrence_id"]}
    if scope == "this_future":
        sql += " AND starts_at >= :from"
        params["from"] = _parse_dt(bk["starts_at"])
    rows = session.execute(text(sql), params).mappings().all()
    return [(str(r["id"]), r["starts_at"], r["ends_at"]) for r in rows]


# ---------------------------------------------------------------------------
# CANCEL (policy: free-cancel window vs fee; free the slot; waitlist promote)
# ---------------------------------------------------------------------------

def cancel_booking(session, *, club_id, booking_id, actor_user_id, role, reason=None,
                   now=None):
    """Cancel a booking. Inside the free-cancel window -> clean release; past the cutoff ->
    a no_show_fee per policy is flagged (docs/03 §5/§6). Setting status='cancelled' frees
    the slot (the exclusion constraint only covers held/confirmed). Promotes the earliest
    matching waitlister. Admins/coaches bypass the cutoff."""
    now = now or datetime.now(timezone.utc)
    bk = _booking_dict(session, booking_id)
    if not bk or bk["club_id"] != str(club_id):
        return _err("NOT_FOUND", 404)
    if bk["status"] in ("cancelled", "completed", "no_show"):
        return _err("ALREADY_CLOSED", 409, status_value=bk["status"])

    policy = _policy(session, club_id)
    cutoff_h = policy.get("cancellation_cutoff_hours") or 0
    within_cutoff = (_parse_dt(bk["starts_at"]) - now) < timedelta(hours=cutoff_h)
    member_initiated = role in ("member", "guest")
    fee_applies = bool(member_initiated and within_cutoff and policy.get("no_show_fee_minor"))
    fee_minor = int(policy.get("no_show_fee_minor") or 0) if fee_applies else 0

    # Cancel this booking + any linked booking sharing the order_id (lesson + its court).
    session.execute(
        text("UPDATE diary.booking SET status='cancelled', cancelled_at=now(), "
             "cancelled_by=:by, cancellation_reason=:reason, held_until=NULL, updated_at=now() "
             "WHERE club_id=:c AND (id=:id OR (order_id IS NOT NULL AND order_id=:oid)) "
             "  AND status IN ('held','confirmed')"),
        {"by": actor_user_id, "reason": reason, "c": club_id, "id": booking_id,
         "oid": bk.get("order_id")},
    )

    booking = _booking_dict(session, booking_id)
    res = _resource(session, club_id, booking["resource_id"])
    payload = _payload(booking, res)
    payload["fee_minor"] = fee_minor
    payload["fee_applied"] = fee_applies
    events.emit("booking_cancelled", payload)

    promoted = _promote_court_waitlist(session, club_id=club_id,
                                       resource_id=booking["resource_id"],
                                       desired_start=_parse_dt(booking["starts_at"]))
    return {"ok": True, "booking": booking, "fee_applied": fee_applies,
            "fee_minor": fee_minor, "waitlist_notified": promoted}


def _promote_court_waitlist(session, *, club_id, resource_id, desired_start):
    """Notify the earliest matching court waitlister that a slot opened (docs/03 §6). We
    stamp notified_at and emit waitlist_slot_open; the claim itself is a fresh booking."""
    row = session.execute(
        text("SELECT id, user_id FROM diary.waitlist "
             "WHERE club_id=:c AND resource_id=:rid AND notified_at IS NULL "
             "  AND (desired_start IS NULL OR desired_start = :ds) "
             "ORDER BY created_at LIMIT 1"),
        {"c": club_id, "rid": resource_id, "ds": desired_start},
    ).mappings().first()
    if not row:
        return None
    session.execute(
        text("UPDATE diary.waitlist SET notified_at=now() WHERE id=:id"), {"id": row["id"]}
    )
    events.emit("waitlist_slot_open", {
        "club_id": str(club_id), "user_id": str(row["user_id"]) if row["user_id"] else None,
        "resource_id": str(resource_id),
        "desired_start": desired_start.isoformat() if desired_start else None,
    })
    return str(row["id"])


# ---------------------------------------------------------------------------
# STATUS transitions (completed / no_show / attended)
# ---------------------------------------------------------------------------

def set_status(session, *, club_id, booking_id, new_status, actor_user_id, role):
    """Mark completed / no_show (coach/admin). 'completed' on a lesson enables attendance
    + emits lesson_completed (feedback/NPS prompt later, docs/06)."""
    if new_status not in ("completed", "no_show"):
        return _err("BAD_STATUS", 400, message="status must be completed|no_show")
    bk = _booking_dict(session, booking_id)
    if not bk or bk["club_id"] != str(club_id):
        return _err("NOT_FOUND", 404)
    if bk["status"] not in ("confirmed", "held"):
        return _err("BAD_STATE", 409, status_value=bk["status"])
    session.execute(
        text("UPDATE diary.booking SET status=:st, updated_at=now() WHERE id=:id"),
        {"st": new_status, "id": booking_id},
    )
    booking = _booking_dict(session, booking_id)
    if new_status == "completed" and booking["booking_type"] == "lesson":
        res = _resource(session, club_id, booking["resource_id"])
        events.emit("lesson_completed", _payload(booking, res))
    return {"ok": True, "booking": booking}


def set_attendance(session, *, club_id, booking_id, party_id=None, attended=True):
    """Mark a party's attendance on a booking (coach/admin)."""
    if party_id:
        session.execute(
            text("UPDATE diary.booking_party SET attended=:a WHERE id=:pid AND club_id=:c"),
            {"a": attended, "pid": party_id, "c": club_id},
        )
    else:
        session.execute(
            text("UPDATE diary.booking_party SET attended=:a "
                 "WHERE booking_id=:b AND club_id=:c"),
            {"a": attended, "b": booking_id, "c": club_id},
        )
    return {"ok": True}


# ---------------------------------------------------------------------------
# emit helpers
# ---------------------------------------------------------------------------

def _payload(booking, res):
    """Non-PII confirmation payload (docs/06 §2): club, contact, resource name, time,
    settlement. Never minor PII (callers must not pass it)."""
    return {
        "club_id": booking["club_id"],
        "user_id": booking.get("booked_by_user_id"),
        "booking_id": booking["id"],
        "booking_type": booking["booking_type"],
        "resource_name": (res or {}).get("name"),
        "starts_at": booking["starts_at"],
        "ends_at": booking["ends_at"],
        "settlement_mode": booking.get("settlement_mode"),
    }


def _emit_confirmed(session, booking, res, settlement_mode):
    events.emit("booking_confirmed", _payload(booking, res))


# ---------------------------------------------------------------------------
# queries (for routes: my bookings / all / as-coach)
# ---------------------------------------------------------------------------

def list_bookings(session, *, club_id, role, user_id, date_from=None, date_to=None,
                  status=None, resource_id=None, as_coach=False, limit=500):
    """Role-scoped booking list. member -> own; coach (as_coach) -> lessons they run;
    admin -> all in club. Always club-scoped."""
    where = ["b.club_id = :c"]
    params = {"c": club_id, "lim": limit}
    if role in ("member", "guest"):
        where.append("b.booked_by_user_id = :uid")
        params["uid"] = user_id
    elif role == "coach":
        # as_coach -> the lessons/sessions this coach RUNS; otherwise the coach's OWN
        # bookings (as a person). Never the whole club — only admins see everything.
        where.append("b.coach_user_id = :uid" if as_coach else "b.booked_by_user_id = :uid")
        params["uid"] = user_id
    # club_admin / platform_admin -> no user filter (all bookings in the club).
    if date_from:
        where.append("b.starts_at >= :df"); params["df"] = _parse_dt(date_from)
    if date_to:
        where.append("b.starts_at <= :dt"); params["dt"] = _parse_dt(date_to)
    if status:
        where.append("b.status = :st"); params["st"] = status
    if resource_id:
        where.append("b.resource_id = :rid"); params["rid"] = resource_id
    rows = session.execute(
        text("SELECT b.id, b.booking_type, b.resource_id, r.name AS resource_name, "
             "       b.coach_user_id, b.starts_at, b.ends_at, b.status, b.order_id, "
             "       b.settlement_mode, b.booked_by_user_id "
             "FROM diary.booking b LEFT JOIN diary.resource r ON r.id = b.resource_id "
             "WHERE " + " AND ".join(where) + " ORDER BY b.starts_at LIMIT :lim"),
        params,
    ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("id", "resource_id", "coach_user_id", "order_id", "booked_by_user_id"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        for k in ("starts_at", "ends_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        out.append(d)
    return out


def get_booking(session, *, club_id, booking_id):
    bk = _booking_dict(session, booking_id)
    if not bk or bk["club_id"] != str(club_id):
        return None
    bk["parties"] = _parties(session, booking_id)
    return bk


def _parties(session, booking_id):
    rows = session.execute(
        text("SELECT id, user_id, party_role, guest_name, guest_email, price_id, attended "
             "FROM diary.booking_party WHERE booking_id = :b"),
        {"b": booking_id},
    ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("id", "user_id", "price_id"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        out.append(d)
    return out

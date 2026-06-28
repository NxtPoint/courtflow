# diary/availability.py — server-side availability computation (docs/03 §3).
#
# GET /api/diary/availability is the workhorse. We compute on read (do NOT materialise),
# with the good indexes from diary/schema.py. Algorithm (docs/03 §3):
#   1. Expand availability_rule for the resource across the range into candidate slots.
#   2. Subtract time_off blocks.
#   3. Subtract existing held/confirmed bookings + class_sessions for that resource.
#   4. Apply club.policy.booking_window_days + lead-time (no past / within-min-lead slots).
#   5. For "any court", union across matching court resources -> collapse to free slots.
#   6. Attach the price for the caller's audience (guarded; None if billing absent).
#
# All times are timezone-aware UTC internally; weekday/start_time of availability_rule are
# interpreted in the club's timezone (docs/03 §10 — never naive local). We use Python's
# zoneinfo (stdlib, 3.12) for the club tz.

import logging
from datetime import datetime, timedelta, time, timezone, date

from sqlalchemy import text

from diary import pricing

log = logging.getLogger("diary.availability")

# Default booking start cadence (minutes): a booking may start every 30 min. This is the slot
# GRID granularity, independent of a booking's length. A club may configure a finer cadence per
# availability_rule (slot_minutes); we never offer starts coarser than this.
BOOKING_GRANULARITY_MIN = 30


def _club_tz(session, club_id):
    """zoneinfo for the club's timezone (defaults to JHB). Falls back to UTC if the tz db
    is unavailable on the platform."""
    name = "Africa/Johannesburg"
    try:
        row = session.execute(
            text("SELECT timezone FROM club.club WHERE id = :c"), {"c": club_id}
        ).first()
        if row and row[0]:
            name = row[0]
    except Exception:
        pass
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        log.warning("zoneinfo for %s unavailable — using UTC", name)
        return timezone.utc


def _policy(session, club_id):
    row = session.execute(
        text("SELECT booking_window_days, min_booking_minutes, cancellation_cutoff_hours "
             "FROM club.policy WHERE club_id = :c"), {"c": club_id}
    ).mappings().first()
    return dict(row) if row else {
        "booking_window_days": 14, "min_booking_minutes": 60,
        "cancellation_cutoff_hours": 12,
    }


def _resources(session, *, club_id, resource_id, kind, coach_user_id, surface):
    """Resolve the set of resources to compute over. A single resource_id, or a filtered
    set (kind/coach/surface) for the 'any court' / 'any coach' union."""
    if resource_id:
        rows = session.execute(
            text("SELECT id, name, kind, surface, capacity, coach_user_id FROM diary.resource "
                 "WHERE club_id = :c AND id = :rid AND is_active = true"),
            {"c": club_id, "rid": resource_id},
        ).mappings().all()
        return [dict(r) for r in rows]
    where = ["club_id = :c", "is_active = true"]
    params = {"c": club_id}
    if kind:
        where.append("kind = :kind"); params["kind"] = kind
    if coach_user_id:
        where.append("coach_user_id = :coach"); params["coach"] = coach_user_id
    if surface:
        where.append("surface = :surface"); params["surface"] = surface
    # Exclude coaches not accepting bookings (is_bookable=false) from the union ("any coach"). Court
    # resources have coach_user_id NULL so they're unaffected. (booking-validation sprint #8)
    where.append("NOT EXISTS (SELECT 1 FROM iam.coach_profile cp "
                 "WHERE cp.club_id = :c AND cp.user_id = diary.resource.coach_user_id "
                 "AND cp.is_bookable = false)")
    rows = session.execute(
        text("SELECT id, name, kind, surface, capacity, coach_user_id FROM diary.resource "
             "WHERE " + " AND ".join(where) + " ORDER BY rank, name"),
        params,
    ).mappings().all()
    return [dict(r) for r in rows]


def _candidate_slots(session, *, club_id, resource_id, tz, range_start, range_end,
                     duration_min):
    """Expand availability_rule rows into concrete (start,end) tz-aware UTC slots across
    [range_start, range_end]. Each rule gives weekday + local start/end + slot_minutes."""
    rules = session.execute(
        text("SELECT weekday, start_time, end_time, slot_minutes, valid_from, valid_to "
             "FROM diary.availability_rule "
             "WHERE club_id = :c AND resource_id = :rid"),
        {"c": club_id, "rid": resource_id},
    ).mappings().all()
    if not rules:
        return []

    slots = []
    day = range_start.astimezone(tz).date()
    last_day = range_end.astimezone(tz).date()
    while day <= last_day:
        wd = day.weekday()  # 0=Mon..6=Sun (matches our convention)
        for r in rules:
            if r["weekday"] != wd:
                continue
            if r["valid_from"] and day < r["valid_from"]:
                continue
            if r["valid_to"] and day > r["valid_to"]:
                continue
            step = timedelta(minutes=duration_min)
            # Start cadence (how OFTEN a booking can start) is separate from its LENGTH (step =
            # duration). Default to a 30-min grid so a 30-min booking doesn't sterilise the
            # following half-hour (a 60-min slot_minutes would only ever offer :00 starts, leaving
            # a 09:30 gap unbookable). A club may configure a FINER cadence via slot_minutes; we
            # never go coarser than 30. (booking-validation: half-hour starts.)
            slot_min = min(r["slot_minutes"] or BOOKING_GRANULARITY_MIN, BOOKING_GRANULARITY_MIN)
            cursor = _combine(day, r["start_time"], tz)
            window_end = _combine(day, r["end_time"], tz)
            stride = timedelta(minutes=slot_min)
            while cursor + step <= window_end:
                s_utc = cursor.astimezone(timezone.utc)
                e_utc = (cursor + step).astimezone(timezone.utc)
                if e_utc > range_start and s_utc < range_end:
                    slots.append((s_utc, e_utc))
                cursor += stride
        day += timedelta(days=1)
    return slots


def _combine(d, t, tz):
    if t is None:
        t = time(0, 0)
    return datetime(d.year, d.month, d.day, t.hour, t.minute, t.second, tzinfo=tz)


def _busy_ranges(session, *, club_id, resource_id, range_start, range_end, coach_user_id=None):
    """All held/confirmed bookings + scheduled class_sessions + time_off for the resource
    in the window — as (start,end) UTC tuples to subtract from candidates.

    When coach_user_id is given (a coach resource), ALSO subtract the class_sessions that
    coach RUNS — a class lives on its own kind='class' resource (class_session.resource_id),
    so it would otherwise never block the coach's lesson availability (the coach is the
    class_session.coach_user_id, not its resource). This is the read-side half of the
    coach∩class guard (the write-side half lives in diary.bookings)."""
    out = []
    for row in session.execute(
        text("SELECT starts_at, ends_at FROM diary.booking "
             "WHERE club_id = :c AND resource_id = :rid "
             "  AND status IN ('held','confirmed') "
             "  AND ends_at > :rs AND starts_at < :re"),
        {"c": club_id, "rid": resource_id, "rs": range_start, "re": range_end},
    ):
        out.append((row[0], row[1]))
    for row in session.execute(
        text("SELECT starts_at, ends_at FROM diary.class_session "
             "WHERE club_id = :c AND resource_id = :rid AND status = 'scheduled' "
             "  AND ends_at > :rs AND starts_at < :re"),
        {"c": club_id, "rid": resource_id, "rs": range_start, "re": range_end},
    ):
        out.append((row[0], row[1]))
    if coach_user_id:
        for row in session.execute(
            text("SELECT starts_at, ends_at FROM diary.class_session "
                 "WHERE club_id = :c AND coach_user_id = :coach AND status = 'scheduled' "
                 "  AND ends_at > :rs AND starts_at < :re"),
            {"c": club_id, "coach": coach_user_id, "rs": range_start, "re": range_end},
        ):
            out.append((row[0], row[1]))
    for row in session.execute(
        text("SELECT starts_at, ends_at FROM diary.time_off "
             "WHERE club_id = :c AND resource_id = :rid "
             "  AND ends_at > :rs AND starts_at < :re"),
        {"c": club_id, "rid": resource_id, "rs": range_start, "re": range_end},
    ):
        out.append((row[0], row[1]))
    return out


def _overlaps(a_start, a_end, busy):
    for b_start, b_end in busy:
        if a_start < b_end and b_start < a_end:
            return True
    return False


_LESSON_KINDS = ("coach", "lesson")


def compute_availability(session, *, club_id, resource_id=None, kind=None,
                         coach_user_id=None, surface=None, date_from=None, date_to=None,
                         duration_minutes=None, audience="member", any_resource=False,
                         membership_covered=False, membership_windows=None, now=None):
    """Return free slots for the resolved resource(s). Each slot:
        {start, end, resource_id, resource_name, kind, price}
    where price is the per-duration price for the chosen duration_minutes (guarded; None if
    billing absent). When membership_covered=True (a court booking by an active member) the
    slot price is forced to 0. When any_resource=True (or no resource_id with a court kind),
    overlapping resources' slots are unioned and collapsed so each distinct (start,end) appears
    once (the first free resource wins).

    Lesson requests (kind in {coach, lesson}, optional coach_id; "any coach" allowed) are
    special: a lesson needs BOTH a free coach AND a free court at the same time. We compute
    the free coach slots, then intersect each with court availability, returning ONLY slots
    where >=1 court is also free and attaching that court as `court_resource_id` (the default
    "any available court" — the frontend may override to a specific court). Court and class
    availability are unchanged.
    """
    now = now or datetime.now(timezone.utc)
    # Lazy expiry (no cron): free abandoned 'held' slots before computing availability, so an
    # unpaid online checkout doesn't block the slot once its hold window passes.
    try:
        from diary.bookings import release_expired_holds
        release_expired_holds(session, club_id, now=now)
    except Exception:
        pass
    tz = _club_tz(session, club_id)
    policy = _policy(session, club_id)
    duration_min = int(duration_minutes or policy["min_booking_minutes"] or 60)

    # Window clamps: no past, no beyond booking_window_days (members; admins relax upstream).
    win_end_default = now + timedelta(days=policy["booking_window_days"] or 14)
    range_start = _parse_dt(date_from, tz) or now
    range_end = _parse_dt(date_to, tz, end_of_day=True) or win_end_default
    range_start = max(range_start, now)
    range_end = min(range_end, win_end_default) if range_end else win_end_default
    if range_end <= range_start:
        return []

    resources = _resources(session, club_id=club_id, resource_id=resource_id, kind=kind,
                           coach_user_id=coach_user_id, surface=surface)
    # Per-duration PAYG price for the chosen slot length (always computed — an off-peak member still
    # pays this at PEAK times). amount_minor (cents) or None when unpriced. Coverage is then decided
    # PER SLOT below: 0 only when an active membership window covers that slot's local start; outside
    # the window (or no membership) the PAYG price stands. This is what makes "free until 16:00,
    # then RX" correct in the calendar — and matches the server's settle decision (membership_covers).
    pr = pricing.price_for(session, club_id=club_id, kind=_price_kind(kind),
                           duration_minutes=duration_min, coach_user_id=coach_user_id,
                           audience=audience)
    payg_price = pr.get("amount_minor") if pr else None
    windows = membership_windows or []
    covers_any_time = membership_covered and not windows  # legacy bool with no windows = full cover
    def _slot_price(s_utc):
        if covers_any_time:
            return 0
        if windows and pricing.any_window_covers(windows, s_utc.astimezone(tz)):
            return 0
        return payg_price

    is_lesson = kind in _LESSON_KINDS

    # For a lesson we need to know which courts are free at a given (start,end). Build a map
    # keyed by the slot's (start,end) -> the first free court at that time. A court is free if
    # it has a candidate slot covering the time and no busy overlap. Courts and coaches share
    # the same slot grid (duration + start_time), so we key on the exact (start,end).
    court_free = {}  # (start_iso, end_iso) -> {"resource_id", "resource_name"}
    if is_lesson:
        courts = _resources(session, club_id=club_id, resource_id=None, kind="court",
                            coach_user_id=None, surface=None)
        for court in courts:
            ccand = _candidate_slots(
                session, club_id=club_id, resource_id=court["id"], tz=tz,
                range_start=range_start, range_end=range_end, duration_min=duration_min,
            )
            if not ccand:
                continue
            cbusy = _busy_ranges(session, club_id=club_id, resource_id=court["id"],
                                 range_start=range_start, range_end=range_end)
            for cs, ce in ccand:
                if cs < now or _overlaps(cs, ce, cbusy):
                    continue
                key = (cs.isoformat(), ce.isoformat())
                if key not in court_free:  # first free court wins (the "any" default)
                    court_free[key] = {"resource_id": str(court["id"]),
                                       "resource_name": court.get("name")}

    # Collapse identical (start,end) for "any" unions. For a lesson with "any coach"
    # (no coach filter) we also collapse so each time appears once (the first free coach).
    collapse = (any_resource or (resource_id is None and kind == "court")
                or (is_lesson and not coach_user_id and not resource_id))

    seen = set()
    out = []
    for res in resources:
        candidates = _candidate_slots(
            session, club_id=club_id, resource_id=res["id"], tz=tz,
            range_start=range_start, range_end=range_end, duration_min=duration_min,
        )
        if not candidates:
            continue
        busy = _busy_ranges(session, club_id=club_id, resource_id=res["id"],
                            range_start=range_start, range_end=range_end,
                            coach_user_id=(res.get("coach_user_id")
                                           if res.get("kind") == "coach" else None))
        for s_utc, e_utc in candidates:
            if s_utc < now:
                continue
            if _overlaps(s_utc, e_utc, busy):
                continue
            key = (s_utc.isoformat(), e_utc.isoformat())
            court = None
            if is_lesson:
                # A lesson slot is only valid when a court is also free at this time.
                court = court_free.get(key)
                if not court:
                    continue
            if collapse:
                if key in seen:
                    continue
                seen.add(key)
            slot = {
                "start": s_utc.isoformat(),
                "end": e_utc.isoformat(),
                "resource_id": str(res["id"]),
                "resource_name": res.get("name"),
                "kind": res.get("kind"),
                "price": _slot_price(s_utc),
            }
            if court:
                slot["court_resource_id"] = court["resource_id"]
                slot["court_resource_name"] = court["resource_name"]
            out.append(slot)
    out.sort(key=lambda x: (x["start"], x["resource_name"] or ""))
    return out


def _price_kind(kind):
    return {"court": "court_booking", "coach": "lesson", "class": "class"}.get(kind)


def _parse_dt(value, tz, end_of_day=False):
    """Parse an ISO date or datetime (string) into a tz-aware UTC datetime. A bare date is
    interpreted in the club tz (start or end of day)."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=tz)
    s = str(value).strip()
    try:
        if "T" in s or " " in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            return dt.astimezone(timezone.utc)
        d = date.fromisoformat(s)
        t = time(23, 59, 59) if end_of_day else time(0, 0)
        return datetime(d.year, d.month, d.day, t.hour, t.minute, t.second,
                        tzinfo=tz).astimezone(timezone.utc)
    except ValueError:
        log.warning("availability: unparseable date %r", value)
        return None

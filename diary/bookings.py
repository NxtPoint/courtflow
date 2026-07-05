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

HOLD_MINUTES_DEFAULT = 30  # online-checkout hold — long enough that a real Yoco payment (incl. a
                           # cold-webhook delay) completes while the booking is still 'held', so the
                           # slot isn't freed out from under a paying customer (docs/03 §4). The
                           # payment-confirm path also re-instates a JUST-expired hold if the slot is
                           # still free (billing.events._confirm_held_bookings), as a backstop.

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


def _first_free_court(session, club_id, starts, ends):
    """The first active court with no held/confirmed booking overlapping [starts, ends). Used to
    auto-assign a court to a lesson when the caller didn't pick one (coach ∩ court) so a lesson can
    never be created without a court. The GiST exclusion constraint still has the final say on the
    insert (a concurrent grab -> SLOT_TAKEN)."""
    return session.execute(
        text("SELECT id FROM diary.resource r "
             "WHERE r.club_id = :c AND r.kind = 'court' AND r.is_active = true "
             "  AND NOT EXISTS (SELECT 1 FROM diary.booking b "
             "      WHERE b.club_id = :c AND b.resource_id = r.id "
             "        AND b.status IN ('held','confirmed') "
             "        AND b.ends_at > :s AND b.starts_at < :e) "
             "  AND NOT EXISTS (SELECT 1 FROM diary.time_off t "
             "      WHERE t.club_id = :c AND t.resource_id = r.id "
             "        AND t.ends_at > :s AND t.starts_at < :e) "
             "ORDER BY r.rank, r.name LIMIT 1"),
        {"c": club_id, "s": starts, "e": ends},
    ).scalar()


_PRODUCT_KIND_BY_BOOKING = {"court": "court_booking", "lesson": "lesson", "class": "class"}


def _service_payment_modes_guarded(session, club_id, booking_type, coach_user_id):
    """The per-service allowed payment methods (or None = no restriction). Guarded — never raises,
    so a missing billing.* can never block a booking."""
    try:
        from diary.pricing import payment_modes_for
        return payment_modes_for(session, club_id=club_id,
                                 kind=_PRODUCT_KIND_BY_BOOKING.get(booking_type, booking_type),
                                 coach_user_id=coach_user_id)
    except Exception:
        return None


def _coach_class_conflict(session, club_id, coach_user_id, starts, ends):
    """True if the coach RUNS a scheduled class overlapping [starts, ends). A class_session is
    NOT a diary.booking, so the GiST exclusion constraint can't arbitrate a lesson-vs-class clash
    for the coach — we must check it explicitly (the write-side half of the coach∩class guard;
    the read-side half lives in diary.availability._busy_ranges)."""
    if not coach_user_id:
        return False
    return bool(session.execute(
        text("SELECT 1 FROM diary.class_session "
             "WHERE club_id = :c AND coach_user_id = :u AND status = 'scheduled' "
             "  AND ends_at > :s AND starts_at < :e LIMIT 1"),
        {"c": club_id, "u": coach_user_id, "s": starts, "e": ends},
    ).first())


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


def _parse_dt(value, end_of_day=False):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # A BARE date (YYYY-MM-DD) used as an inclusive upper bound must cover the whole day — else a
    # same-day query (date_from==date_to) collapses to a zero-width midnight window and returns
    # nothing (this silently emptied the coach "Today" cockpit). A caller that already sends an
    # explicit time (…T23:59:59) is left untouched.
    if end_of_day and "T" not in s and " " not in s and len(s) <= 10:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt


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
    if mode in ("membership_covered", "free", "token"):
        # token: a member spending their own prepaid pack is always allowed; the real gate is
        # whether they actually HOLD a matching wallet (match_wallet → NO_TOKEN otherwise).
        return True
    return False


def _has_active_membership_guarded(session, *, club_id, user_id):
    """True if the user holds an active membership (delegates to diary.pricing, which guards
    against billing.* being absent in isolation). Never raises."""
    try:
        from diary.pricing import has_active_membership
        return has_active_membership(session, club_id=club_id, user_id=user_id)
    except Exception:
        return False


def _membership_covers_guarded(session, *, club_id, user_id, starts_at):
    """True if an active membership covers a COURT booking STARTING at starts_at — active AND inside
    the plan's access window (Phase 5). Outside the window -> False -> the booking is billed PAYG.
    Guarded; never raises."""
    try:
        from diary.pricing import membership_covers
        return membership_covers(session, club_id=club_id, user_id=user_id, starts_at=starts_at)
    except Exception:
        return False


def release_expired_holds(session, club_id, now=None):
    """Lazy expiry (NO cron): cancel 'held' bookings whose held_until has passed, freeing the
    slot. Called opportunistically at the start of availability + booking creation, so an
    abandoned online checkout (held → never paid) is released the moment anyone looks at that
    diary again. Cheap, indexed UPDATE; safe to run on every request."""
    session.execute(
        text("UPDATE diary.booking "
             "SET status='cancelled', cancellation_reason='hold_expired', "
             "    cancelled_at=now(), updated_at=now() "
             "WHERE club_id=:c AND status='held' "
             "  AND held_until IS NOT NULL AND held_until < now()"),
        {"c": club_id},
    )


# booking_type -> billing.product.kind (docs/02 §5). The diary speaks booking types;
# billing speaks product kinds. This adapter is the single translation point.
_KIND_BY_BOOKING_TYPE = {"court": "court_booking", "lesson": "lesson", "class": "class"}

# booking_type -> bundle service_kind (docs/specs/02). The token engine speaks the diary's
# booking-type vocabulary directly (court|lesson|class).
_SERVICE_KIND_BY_BOOKING_TYPE = {"court": "court", "lesson": "lesson", "class": "class"}


def _match_token_wallet_guarded(session, *, club_id, user_id, booking_type,
                                duration_minutes=None, coach_user_id=None):
    """Find (and LOCK, FOR UPDATE) the best token wallet for this booking, or None. Guarded so the
    diary self-verifies without billing.* present. The wallet is held under the caller's tx so the
    subsequent draw_token can't race. service_kind/duration/coach drive the match (docs/specs/02)."""
    try:
        from billing import bundles
    except Exception:
        return None
    service_kind = _SERVICE_KIND_BY_BOOKING_TYPE.get(booking_type)
    if not service_kind:
        return None
    try:
        return bundles.match_wallet(
            session, club_id=club_id, user_id=user_id, service_kind=service_kind,
            duration_minutes=duration_minutes, coach_user_id=coach_user_id)
    except Exception:
        log.debug("token match_wallet suppressed (bundles/tables absent)", exc_info=False)
        return None


def _draw_token_guarded(session, *, club_id, wallet, booking_id, reason="booking",
                        duration_minutes=None):
    """Draw a booking's worth of MINUTES from an already-matched wallet (its duration, or one full
    unit for a class). Idempotent + guarded."""
    try:
        from billing import bundles
        return bundles.draw_token(session, club_id=club_id, wallet=wallet,
                                  booking_id=booking_id, reason=reason,
                                  duration_minutes=duration_minutes)
    except Exception:
        log.warning("token draw failed (booking kept, order deferred)", exc_info=False)
        return False


def _credit_token_guarded(session, *, club_id, booking_id, reason="cancellation"):
    """Credit one token back for a token-settled booking on cancel. Idempotent + guarded so a
    re-cancel (or a non-token booking) is a clean no-op."""
    try:
        from billing import bundles
        return bundles.credit_token(session, club_id=club_id, booking_id=booking_id, reason=reason)
    except Exception:
        log.debug("token credit suppressed (bundles/tables absent)", exc_info=False)
        return False


def _create_order_guarded(session, *, club_id, user_id, booking_id=None, booking_type="court",
                          settlement_mode="at_court", parties=None, resource_id=None,
                          starts_at=None, ends_at=None, linked_booking_id=None,
                          audience="member", enrolment_id=None, duration_minutes=None,
                          token_wallet=None, token_ref=None):
    """Adapter between the diary and Agent C's billing.orders.create_order_for_booking.

    The diary speaks bookings; billing speaks order *lines*. We translate here: price each
    party (or the booking) via diary.pricing.price_for for the booking's DURATION (per-duration
    pricing), assemble C's `lines`, call C (which returns an order_id str), and return the dict
    the diary callers expect {order_id, status, checkout, amount_minor}.

    membership_covered -> the order is free: lines carry amount 0 (the membership pays for it).
    token -> the order is free (R0, settlement 'token'): a prepaid session token is drawn from
    `token_wallet` (matched + locked by the caller). If settlement is 'token' but no wallet was
    matched, return {"error":"NO_TOKEN"} so the caller rejects the booking cleanly (UI falls back
    to PAYG) — and NOTHING is billed.

    Guarded: if billing isn't present (self-verify mode) the booking still succeeds with no
    order; if pricing isn't seeded yet, lines carry amount 0 (admin sets price later)."""
    # --- token settlement (docs/specs/02): draw a prepaid token, settle the order at R0. -------
    is_token = (settlement_mode == "token")
    if is_token and token_wallet is None:
        # No matching wallet was found at the pre-flight match — reject so the booking rolls back.
        return {"order_id": None, "status": None, "checkout": None,
                "amount_minor": None, "error": "NO_TOKEN"}

    try:
        from billing.orders import create_order_for_booking, booking_status_for_mode
    except Exception:
        log.debug("billing.orders absent — booking proceeds without an order (self-verify mode)")
        return {"order_id": None, "status": "open", "checkout": None, "amount_minor": None}

    from diary.pricing import price_for
    kind = _KIND_BY_BOOKING_TYPE.get(booking_type, "court_booking")
    ref = {"booking_id": booking_id, "enrolment_id": enrolment_id}
    parties = parties or []
    covered = (settlement_mode == "membership_covered" or is_token)  # free — token/membership pays

    def _amount(pr):
        return 0 if covered else (pr.get("amount_minor") or 0)

    # One billing line per party, else a single line for the booking — priced per DURATION.
    # The linked lesson court shares the order but isn't billed twice.
    lines = []
    # Guests are NON-BILLABLE for now — never charged to the member's account. Bill only the
    # member/host parties; a guest still rides on the booking as a party, there's just no line for
    # them. (Phase 2: charge a guest a fixed fee collected FROM THE GUEST, not the member's tab.)
    member_parties = [p for p in parties
                      if not (p.get("party_role") == "guest" or p.get("guest_name"))]
    if member_parties:
        for p in member_parties:
            pr = price_for(session, club_id=club_id, audience="member", kind=kind,
                           duration_minutes=duration_minutes) or {}
            lines.append({"description": booking_type, "price_id": pr.get("price_id"),
                          "qty": 1, "amount_minor": _amount(pr), **ref})
    else:
        pr = price_for(session, club_id=club_id, audience=audience, kind=kind,
                       duration_minutes=duration_minutes) or {}
        lines.append({"description": booking_type, "price_id": pr.get("price_id"),
                      "qty": 1, "amount_minor": _amount(pr), **ref})

    try:
        order_id = create_order_for_booking(
            session, club_id=club_id, user_id=user_id, lines=lines,
            settlement_mode=settlement_mode)
        total = sum(int(l["amount_minor"]) * int(l.get("qty") or 1) for l in lines)
        # token settlement: draw ONE token from the locked wallet for THIS booking, in the SAME
        # transaction (so a rollback un-draws it; a committed draw always has a confirmed booking).
        # The draw is idempotent per (wallet, ref); a False return means it was already drawn. The
        # ledger ref is the booking_id for court/lesson, or the enrolment_id (token_ref) for a class.
        draw_ref = token_ref or booking_id
        if is_token and draw_ref and token_wallet is not None:
            # court/lesson draw their own duration; a class passes None -> one full unit (per-session).
            drawn = _draw_token_guarded(session, club_id=club_id, wallet=token_wallet,
                                        booking_id=draw_ref, reason=f"{booking_type} booking",
                                        duration_minutes=duration_minutes)
            if not drawn:
                # Re-run for the same booking (idempotent) — fine; the token already moved.
                log.debug("token already drawn for booking=%s (idempotent)", booking_id)
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
                   booked_for_user_id=None, propose=False, now=None):
    """Create a court/lesson/class booking, concurrency-safe (docs/03 §4).

    For a lesson, pass court_resource_id to auto-hold a court in the SAME transaction (two
    diary.booking rows sharing one order_id). The whole thing is one tx: if EITHER hold
    overlaps, the exclusion constraint aborts the tx -> we report SLOT_TAKEN and nothing
    is persisted (the original/other slot is untouched).

    `booked_for_user_id` — on-behalf booking (docs/08): a coach/admin creating a booking FOR
    a client. When set, the persisted booking.booked_by_user_id is the CLIENT (so it appears
    in their "my bookings"), not the actor. The actor is still linked as coach_user_id for a
    lesson. The route is the ONLY place that authorises this override (role-gated) — by the
    time it reaches here the value is already trusted. Default (None) keeps the original
    behaviour: the booking is owned by booked_by_user_id (the actor).
    """
    now = now or datetime.now(timezone.utc)
    release_expired_holds(session, club_id, now=now)  # lazy expiry: free abandoned holds (no cron)
    # On-behalf override: persist the booking under the client, not the actor.
    owner_user_id = booked_for_user_id or booked_by_user_id
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

    # ---- lesson approval gate (accept/propose/decline lifecycle) ----------------------------
    # A lesson is "gated" — created as 'requested' (reserving NOTHING: no court, no order, no
    # payment) when a CLIENT self-books a coach who reviews bookings, so the coach accepts/declines
    # first. A coach/admin booking ON-BEHALF of a client ALWAYS auto-confirms (no client-acceptance
    # step) — the client is just notified and can reschedule/cancel themselves if it doesn't suit.
    # (A coach can still counter-propose a new time on a request via propose_time -> 'proposed',
    # which the client then accepts/declines.) On accept the court is auto-assigned + the normal
    # settlement runs (online prepay -> pay-at-court for an unconfirmed lesson). Everything else
    # flows through the immediate path below unchanged.
    if booking_type == "lesson" and res["kind"] == "coach":
        _gate_coach = coach_user_id or res.get("coach_user_id")
        _gate_status = None
        if booked_for_user_id is None and role in ("member", "guest") and \
                _coach_reviews(session, club_id, _gate_coach):
            _gate_status = "requested"
        if _gate_status and _gate_coach:
            _gate_sm = "at_court" if settlement_mode == "online" else settlement_mode
            _gid = _insert_booking(
                session, club_id=club_id, booking_type="lesson", resource_id=resource_id,
                coach_user_id=_gate_coach, starts_at=starts, ends_at=ends, status=_gate_status,
                held_until=None, booked_by_user_id=owner_user_id, recurrence_id=recurrence_id,
                settlement_mode=_gate_sm, notes=notes)
            for _party in parties:
                _insert_party(session, booking_id=_gid, club_id=club_id, party=_party)
            _gb = _booking_dict(session, _gid)
            _lesson_event(session, _gb,
                          "lesson_requested" if _gate_status == "requested" else "lesson_proposed",
                          _gate_coach if _gate_status == "requested" else owner_user_id)
            return {"ok": True, "booking": _gb, "checkout": None}

    # Lesson integrity (coach ∩ court): a lesson is a COACH booking that ALSO holds a court in the
    # same transaction. The primary resource MUST be an active coach; and a court MUST be held — if
    # the caller didn't choose one (e.g. the coach console booking on-behalf), auto-assign the first
    # free court. No coach, or no free court at this time -> reject. This makes a coachless or
    # courtless "lesson" impossible across every path (member, admin, book-on-behalf).
    if booking_type == "lesson":
        if res["kind"] != "coach":
            return _err("COACH_REQUIRED", 422, message="a lesson must be booked with a coach")
        # A coach running a class can't simultaneously take a lesson — the class isn't a
        # diary.booking so the exclusion constraint can't see it (guard explicitly).
        if _coach_class_conflict(session, club_id,
                                 coach_user_id or res.get("coach_user_id"), starts, ends):
            return _err("COACH_BUSY", 409,
                        message="the coach is running a class at this time")
        if not court_resource_id:
            court_resource_id = _first_free_court(session, club_id, starts, ends)
        if not court_resource_id:
            return _err("NO_COURT_AVAILABLE", 422,
                        message="no court is free at this time — a lesson needs a court")

    policy = _policy(session, club_id)

    # Membership-covered guard: 'membership_covered' (free) is ONLY honoured for a COURT booking by
    # a user whose active membership covers THIS start time (Phase 5 access window — a time-boxed
    # tier like Student is free only inside its hours). Anyone else who asks for it (no membership,
    # a lesson/class, or a court OUTSIDE the membership window) falls back to per-duration PAYG at
    # the court — the booking still succeeds, just billed normally. Never trust the client's claim.
    if settlement_mode == "membership_covered":
        if booking_type == "court" and _membership_covers_guarded(
                session, club_id=club_id, user_id=owner_user_id, starts_at=starts):
            pass  # legitimately covered (active membership + inside its access window)
        else:
            settlement_mode = "at_court"

    # A booking must have a positive duration (an empty/inverted range is invalid + would break
    # the GiST exclusion constraint). This is the only LENGTH floor we enforce.
    if ends <= starts:
        return _err("TOO_SHORT", 422, message="booking must have a positive duration")

    # Booking window (members; admins/coaches relax). NOTE: we deliberately do NOT enforce
    # club.policy.min_booking_minutes here. Per-duration pricing is the single source of truth for
    # length — a member can only pick a duration the club/coach has CONFIGURED a price for
    # (diary.pricing.durations_for powers the picker), so a separate minimum is redundant and was
    # actively harmful: it rejected configured services (e.g. a 30-min court the owner priced),
    # contradicting what the picker offered. Configured == bookable.
    if role in ("member", "guest"):
        window_days = policy.get("booking_window_days") or 14
        if starts > now + timedelta(days=window_days):
            return _err("OUTSIDE_BOOKING_WINDOW", 422,
                        message=f"can't book more than {window_days} days ahead")

    if not _settlement_allowed(settlement_mode, policy, role):
        return _err("SETTLEMENT_NOT_ALLOWED", 422, settlement_mode=settlement_mode)

    # Member-guest guard (docs/03 §10): if a guest party is present and the club requires a
    # member host, exactly one party must be party_role='host'. Members/guests are held to
    # this; admins/coaches relax it (same as the window/min-duration/settlement relaxations
    # above) — they legitimately book walk-ins on a client's behalf (docs/08).
    if role in ("member", "guest"):
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
    if booking_type == "lesson" and not coach_uid:
        return _err("COACH_REQUIRED", 422, message="a lesson must be booked with a coach")

    # Per-service payment preference (members/guests only; admins/coaches override). A service may
    # offer only a subset of the club-enabled methods — the booking UI already hides the rest, this
    # refuses a crafted request that picks a method the service doesn't offer. Only the money modes
    # are constrained (token/membership_covered/free are not "methods" a service restricts).
    if role in ("member", "guest") and settlement_mode in ("online", "at_court", "monthly_account"):
        pm = _service_payment_modes_guarded(session, club_id, booking_type, coach_uid)
        if pm is not None and settlement_mode not in pm:
            return _err("SETTLEMENT_NOT_ALLOWED", 422, settlement_mode=settlement_mode,
                        message="this service doesn't offer that payment method")

    # Token settlement (docs/specs/02): PRE-FLIGHT match a prepaid wallet BEFORE we insert the
    # booking, so a NO-token request never persists anything (clean NO_TOKEN — the UI falls back to
    # PAYG). The matched wallet is locked FOR UPDATE and held through the insert; the draw happens
    # in the SAME transaction right after order creation. service_kind/duration/coach drive the
    # match: a lesson token may be coach-specific (coach_uid), a court/class token is coach-agnostic.
    token_wallet = None
    if settlement_mode == "token":
        duration_for_match = int((ends - starts).total_seconds() // 60)
        match_coach = coach_uid if booking_type == "lesson" else None
        token_wallet = _match_token_wallet_guarded(
            session, club_id=club_id, user_id=owner_user_id, booking_type=booking_type,
            duration_minutes=duration_for_match, coach_user_id=match_coach)
        if token_wallet is None:
            return _err("NO_TOKEN", 422,
                        message="no matching prepaid token — choose another way to pay")

    # --- the concurrency-safe insert(s) inside one transaction ----------
    try:
        with session.begin_nested():  # SAVEPOINT — lets us catch the overlap cleanly
            booking_id = _insert_booking(
                session, club_id=club_id, booking_type=booking_type, resource_id=resource_id,
                coach_user_id=coach_uid, starts_at=starts, ends_at=ends, status=status,
                held_until=held_until, booked_by_user_id=owner_user_id,
                recurrence_id=recurrence_id, settlement_mode=settlement_mode, notes=notes,
            )
            linked_court_id = None
            if booking_type == "lesson" and court_resource_id:
                court = _resource(session, club_id, court_resource_id)
                if not court or not court["is_active"] or court["kind"] != "court":
                    return _err("COURT_NOT_FOUND", 404, message="a valid court is required for a lesson")
                linked_court_id = _insert_booking(
                    session, club_id=club_id, booking_type="court",
                    resource_id=court_resource_id, coach_user_id=coach_uid,
                    starts_at=starts, ends_at=ends, status=status, held_until=held_until,
                    booked_by_user_id=owner_user_id, recurrence_id=recurrence_id,
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
    duration_minutes = int((ends - starts).total_seconds() // 60)
    order = _create_order_guarded(
        session, club_id=club_id, user_id=owner_user_id, booking_id=booking_id,
        booking_type=booking_type, settlement_mode=settlement_mode, parties=parties,
        resource_id=resource_id, starts_at=starts, ends_at=ends,
        linked_booking_id=linked_court_id, audience=audience,
        duration_minutes=duration_minutes, token_wallet=token_wallet,
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

    # A lesson must not be moved onto a time the coach runs a scheduled class — a class_session is
    # not a diary.booking, so the GiST exclusion can't catch it (mirror the create/accept guard).
    if _coach_class_conflict(session, club_id, bk.get("coach_user_id"), new_s, new_e):
        return _err("COACH_BUSY", 409, message="the coach runs a class at the new time")

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
    # A still-pending request/proposal was never confirmed, so withdrawing it never incurs a fee.
    fee_applies = bool(member_initiated and within_cutoff and policy.get("no_show_fee_minor")
                       and bk["status"] in ("held", "confirmed"))
    fee_minor = int(policy.get("no_show_fee_minor") or 0) if fee_applies else 0

    # Cancel this booking + any linked booking sharing the order_id (lesson + its court).
    session.execute(
        text("UPDATE diary.booking SET status='cancelled', cancelled_at=now(), "
             "cancelled_by=:by, cancellation_reason=:reason, held_until=NULL, updated_at=now() "
             "WHERE club_id=:c AND (id=:id OR (order_id IS NOT NULL AND order_id=:oid)) "
             # include requested/proposed so a client can WITHDRAW a still-pending lesson request.
             "  AND status IN ('held','confirmed','requested','proposed')"),
        {"by": actor_user_id, "reason": reason, "c": club_id, "id": booking_id,
         "oid": bk.get("order_id")},
    )

    # Token credit-back (docs/specs/02): if this booking (or its linked lesson court) was settled
    # by a prepaid token, return the token to the wallet. Idempotent — a re-cancel credits nothing.
    # Default policy: ALWAYS credit back on cancel (a too-late forfeit is a future option). The
    # lesson + its court share the order_id but only ONE token was drawn (against booking_id), so
    # we credit the cancelled booking_id (and, defensively, any linked booking that drew a token).
    credited = _credit_token_guarded(session, club_id=club_id, booking_id=booking_id,
                                     reason=reason or "cancellation")
    if bk.get("order_id"):
        _credit_linked_tokens(session, club_id=club_id, order_id=bk["order_id"],
                              except_booking_id=booking_id, reason=reason or "cancellation")

    # A cancelled booking must NOT leave a phantom debt: void its owed order (open/awaiting_payment)
    # so it drops off the client's statement AND off the coach's tab (void_order clears arrears too).
    # A PAID order is left intact — refunding a paid booking is a separate, explicit flow. Mirrors
    # cancel_membership. (Without this, a cancelled court still showed as owed in Billing.)
    if bk.get("order_id"):
        try:
            from billing.statement import void_order
            void_order(session, club_id=club_id, order_id=bk["order_id"], reason="booking cancelled")
        except Exception:
            log.info("cancel_booking: order void skipped (billing unavailable) order=%s", bk.get("order_id"))

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
            "fee_minor": fee_minor, "waitlist_notified": promoted,
            "token_credited": credited}


def _credit_linked_tokens(session, *, club_id, order_id, except_booking_id, reason):
    """Credit back any token drawn against OTHER bookings sharing this order_id (e.g. a lesson and
    its auto-held court — both cancelled together). Each credit is idempotent per (wallet, booking).
    Cheap + guarded; a no-op when no linked token bookings exist."""
    try:
        rows = session.execute(
            text("SELECT id FROM diary.booking "
                 "WHERE club_id=:c AND order_id=:o AND id<>:b"),
            {"c": club_id, "o": order_id, "b": except_booking_id},
        ).mappings().all()
    except Exception:
        return
    for r in rows:
        _credit_token_guarded(session, club_id=club_id, booking_id=str(r["id"]), reason=reason)


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
# Lesson approval lifecycle: accept / propose new time / decline.
# requested = awaiting coach · proposed = awaiting client. A gated lesson
# reserves nothing until accepted; accept assigns a court + runs settlement.
# ---------------------------------------------------------------------------

def _coach_reviews(session, club_id, coach_user_id):
    """True when this coach reviews bookings before they confirm (coach_profile.review_bookings)."""
    if not coach_user_id:
        return False
    try:
        return bool(session.execute(
            text("SELECT review_bookings FROM iam.coach_profile WHERE club_id=:c AND user_id=:u"),
            {"c": club_id, "u": coach_user_id}).scalar())
    except Exception:
        return False


def _as_dt(v):
    return v if isinstance(v, datetime) else _parse_dt(v)


def _gated_actor_ok(bk, actor_user_id, role):
    """Only the AWAITED party may act on a gated lesson (admins always)."""
    if role in ("club_admin", "platform_admin"):
        return True
    if bk["status"] == "requested":            # awaiting the coach
        return str(bk.get("coach_user_id")) == str(actor_user_id)
    if bk["status"] == "proposed":             # awaiting the client (booker)
        return str(bk.get("booked_by_user_id")) == str(actor_user_id)
    return False


def _lesson_event(session, booking, event, recipient_user_id):
    """Emit a lesson-lifecycle event routed to the recipient (best-effort, non-fatal)."""
    try:
        res = _resource(session, booking["club_id"], booking["resource_id"])
        payload = _payload(booking, res)
        if recipient_user_id:
            payload["user_id"] = str(recipient_user_id)
        events.emit(event, payload)
    except Exception:
        log.debug("lesson event emit failed", exc_info=False)


def accept_booking(session, *, club_id, booking_id, actor_user_id, role, now=None):
    """The awaited party accepts a requested/proposed lesson → assign a free court, confirm both
    rows (the GiST exclusion constraint arbitrates the slot), run the normal settlement, and notify
    the requester. Reuses the same seam create_booking uses (no money-path duplication)."""
    now = now or datetime.now(timezone.utc)
    bk = _booking_dict(session, booking_id)
    if not bk or str(bk["club_id"]) != str(club_id):
        return _err("NOT_FOUND", 404)
    if bk["status"] not in ("requested", "proposed"):
        return _err("BAD_STATE", 409, status_value=bk["status"])
    if not _gated_actor_ok(bk, actor_user_id, role):
        return _err("NOT_AWAITED", 403, message="only the awaited party can accept this lesson")

    starts = _as_dt(bk["starts_at"]); ends = _as_dt(bk["ends_at"])
    owner_user_id = bk.get("booked_by_user_id")
    coach_uid = bk.get("coach_user_id")
    settlement_mode = bk.get("settlement_mode") or "at_court"
    online = settlement_mode == "online"
    status = "held" if online else "confirmed"
    held_until = (now + timedelta(minutes=HOLD_MINUTES_DEFAULT)) if online else None

    if _coach_class_conflict(session, club_id, coach_uid, starts, ends):
        return _err("COACH_BUSY", 409, message="the coach is running a class at this time")

    court_resource_id = _first_free_court(session, club_id, starts, ends)
    if not court_resource_id:
        return _err("NO_COURT_AVAILABLE", 422, message="no court is free at this time")

    token_wallet = None
    if settlement_mode == "token":
        dur = int((ends - starts).total_seconds() // 60)
        token_wallet = _match_token_wallet_guarded(
            session, club_id=club_id, user_id=owner_user_id, booking_type="lesson",
            duration_minutes=dur, coach_user_id=coach_uid)
        if token_wallet is None:
            return _err("NO_TOKEN", 422, message="no matching prepaid token — choose another way to pay")

    linked_court_id = None
    try:
        with session.begin_nested():
            session.execute(
                text("UPDATE diary.booking SET status=:st, held_until=:hu, updated_at=now() WHERE id=:id"),
                {"st": status, "hu": held_until, "id": booking_id})
            court = _resource(session, club_id, court_resource_id)
            if not court or not court["is_active"] or court["kind"] != "court":
                return _err("COURT_NOT_FOUND", 404, message="a valid court is required for a lesson")
            linked_court_id = _insert_booking(
                session, club_id=club_id, booking_type="court", resource_id=court_resource_id,
                coach_user_id=coach_uid, starts_at=starts, ends_at=ends, status=status,
                held_until=held_until, booked_by_user_id=owner_user_id, recurrence_id=None,
                settlement_mode=settlement_mode, notes="(court held for lesson)")
    except IntegrityError as e:
        if _is_slot_taken(e):
            return _err("SLOT_TAKEN", 409, message="that slot was just taken")
        log.exception("accept booking integrity error")
        return _err("INTEGRITY_ERROR", 409)

    duration_minutes = int((ends - starts).total_seconds() // 60)
    order = _create_order_guarded(
        session, club_id=club_id, user_id=owner_user_id, booking_id=booking_id,
        booking_type="lesson", settlement_mode=settlement_mode, parties=[],
        resource_id=bk["resource_id"], starts_at=starts, ends_at=ends,
        linked_booking_id=linked_court_id, audience="member",
        duration_minutes=duration_minutes, token_wallet=token_wallet)
    order_id = order.get("order_id")
    if order_id:
        _attach_order(session, booking_id, order_id)
        if linked_court_id:
            _attach_order(session, linked_court_id, order_id)

    booking = _booking_dict(session, booking_id)
    res = _resource(session, club_id, booking["resource_id"])
    other = owner_user_id if bk["status"] == "requested" else coach_uid
    _lesson_event(session, booking, "lesson_accepted", other)
    if not online:
        _emit_confirmed(session, booking, res, settlement_mode)
    return {"ok": True, "booking": booking, "checkout": order.get("checkout")}


def propose_time(session, *, club_id, booking_id, actor_user_id, role, starts_at, ends_at, now=None):
    """The awaited party proposes a different time → flips the turn to the other party."""
    now = now or datetime.now(timezone.utc)
    bk = _booking_dict(session, booking_id)
    if not bk or str(bk["club_id"]) != str(club_id):
        return _err("NOT_FOUND", 404)
    if bk["status"] not in ("requested", "proposed"):
        return _err("BAD_STATE", 409, status_value=bk["status"])
    if not _gated_actor_ok(bk, actor_user_id, role):
        return _err("NOT_AWAITED", 403, message="only the awaited party can propose a new time")
    starts = _parse_dt(starts_at); ends = _parse_dt(ends_at)
    if not starts or not ends or ends <= starts:
        return _err("BAD_RANGE", 400, message="ends_at must be after starts_at")
    if starts < now:
        return _err("IN_THE_PAST", 400, message="cannot propose a past slot")
    new_status = "proposed" if bk["status"] == "requested" else "requested"
    session.execute(
        text("UPDATE diary.booking SET starts_at=:sa, ends_at=:ea, status=:st, updated_at=now() WHERE id=:id"),
        {"sa": starts, "ea": ends, "st": new_status, "id": booking_id})
    booking = _booking_dict(session, booking_id)
    recipient = booking.get("booked_by_user_id") if new_status == "proposed" else booking.get("coach_user_id")
    _lesson_event(session, booking, "lesson_proposed", recipient)
    return {"ok": True, "booking": booking}


def decline_booking(session, *, club_id, booking_id, actor_user_id, role, reason=None):
    """The awaited party declines a requested/proposed lesson → cancelled; notify the requester."""
    bk = _booking_dict(session, booking_id)
    if not bk or str(bk["club_id"]) != str(club_id):
        return _err("NOT_FOUND", 404)
    if bk["status"] not in ("requested", "proposed"):
        return _err("BAD_STATE", 409, status_value=bk["status"])
    if not _gated_actor_ok(bk, actor_user_id, role):
        return _err("NOT_AWAITED", 403, message="only the awaited party can decline")
    session.execute(
        text("UPDATE diary.booking SET status='cancelled', cancellation_reason=:r, "
             "cancelled_by=:by, cancelled_at=now(), updated_at=now() WHERE id=:id"),
        {"r": reason or "declined", "by": actor_user_id, "id": booking_id})
    booking = _booking_dict(session, booking_id)
    other = booking.get("booked_by_user_id") if bk["status"] == "requested" else booking.get("coach_user_id")
    _lesson_event(session, booking, "lesson_declined", other)
    return {"ok": True, "booking": booking}


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
        # The .ics download (in-app 'Add to calendar'; the confirmation email attaches the same
        # file once SES/Klaviyo is wired). Matches contracts/events.md booking_confirmed.ics_url.
        "ics_url": "/api/diary/bookings/" + str(booking["id"]) + "/calendar.ics",
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
        where.append("b.starts_at <= :dt"); params["dt"] = _parse_dt(date_to, end_of_day=True)
    if status:
        where.append("b.status = :st"); params["st"] = status
    if resource_id:
        where.append("b.resource_id = :rid"); params["rid"] = resource_id
    # A lesson is TWO rows (the coach row + an auto-held court row sharing one order_id). In a
    # booking LIST we collapse that to ONE line: hide the linked court row (always inserted with
    # this exact note) and surface its court name on the lesson row as `court_name`. Standalone
    # court bookings are untouched. (The admin master diary deliberately still shows the court row
    # — it's a resource timeline.)
    where.append("NOT (b.booking_type = 'court' AND b.notes = '(court held for lesson)')")
    rows = session.execute(
        text("SELECT b.id, b.booking_type, b.resource_id, r.name AS resource_name, "
             "       b.coach_user_id, b.starts_at, b.ends_at, b.status, b.order_id, "
             "       b.settlement_mode, b.booked_by_user_id, "
             # The court auto-held for a lesson (same order_id), so a lesson line can show
             # "Lesson · Court 3" without a second row.
             "       (SELECT cr.name FROM diary.booking cb "
             "          JOIN diary.resource cr ON cr.id = cb.resource_id "
             "         WHERE cb.club_id = b.club_id AND cb.order_id = b.order_id "
             "           AND cb.booking_type = 'court' AND b.order_id IS NOT NULL "
             "           AND b.booking_type = 'lesson' LIMIT 1) AS court_name, "
             # The booker's name/email so coach/admin lists (esp. the accept/propose/decline
             # queue) can show WHO requested — not just the resource. Non-PII for the coach who
             # runs the lesson; the client is their own client.
             "       NULLIF(TRIM(COALESCE(ub.first_name,'') || ' ' || COALESCE(ub.surname,'')),'') "
             "         AS booked_by_name, "
             "       ub.email AS booked_by_email "
             "FROM diary.booking b "
             "LEFT JOIN diary.resource r ON r.id = b.resource_id "
             'LEFT JOIN iam."user" ub ON ub.id = b.booked_by_user_id '
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


def _booking_charge(session, club_id, order_id, settlement_mode):
    """The charge + payment status for a booking's order (guarded cross-lane read). Maps the order
    status to a client word: paid / owed / pending / refunded / cancelled / covered."""
    covered = settlement_mode in ("membership_covered", "free", "token")
    base = {"amount_minor": 0, "currency": "ZAR",
            "status": "covered" if covered else "none",
            "settlement_mode": settlement_mode, "order_id": str(order_id) if order_id else None,
            "refundable": False, "has_open_refund": False}
    if not order_id:
        return base
    try:
        o = session.execute(
            text('SELECT amount_minor, currency_code, status FROM billing."order" '
                 "WHERE id = :o AND club_id = :c"),
            {"o": str(order_id), "c": str(club_id)},
        ).mappings().first()
        if not o:
            return base
        st_map = {"paid": "paid", "open": "owed", "awaiting_payment": "pending",
                  "refunded": "refunded", "void": "cancelled", "written_off": "written_off"}
        amt = int(o["amount_minor"] or 0)
        status = "covered" if (covered and amt == 0) else st_map.get(o["status"], o["status"])
        openref = session.execute(
            text("SELECT 1 FROM billing.refund_request WHERE order_id = :o AND status = 'pending' LIMIT 1"),
            {"o": str(order_id)},
        ).first()
        return {"amount_minor": amt, "currency": o["currency_code"] or "ZAR", "status": status,
                "settlement_mode": settlement_mode, "order_id": str(order_id),
                "refundable": (o["status"] == "paid" and not openref),
                "has_open_refund": bool(openref)}
    except Exception:
        base["status"] = "unknown"
        return base


def booking_story(session, *, club_id, user_id, booking_id):
    """The full client-facing STORY of one booking — assembled from diary + club + billing so the UI
    can show the whole picture in one screen: what & when, WHERE (club + address + court), WHO played
    (resolved names), the CHARGE + payment status, the .ics link, and action eligibility. Scoped to
    the caller's OWN booking (they must be the booker). Returns None if not found / not theirs."""
    b = session.execute(
        text("""
            SELECT b.id, b.club_id, b.booking_type, b.status, b.starts_at, b.ends_at,
                   b.resource_id, r.name AS resource_name, b.coach_user_id,
                   b.order_id, b.settlement_mode, b.booked_by_user_id, b.notes,
                   (SELECT cr.name FROM diary.booking cb JOIN diary.resource cr ON cr.id = cb.resource_id
                     WHERE cb.club_id = b.club_id AND cb.order_id = b.order_id
                       AND cb.booking_type = 'court' AND b.order_id IS NOT NULL
                       AND b.booking_type = 'lesson' LIMIT 1) AS held_court,
                   COALESCE(cp.display_name,
                            NULLIF(TRIM(COALESCE(cu.first_name,'') || ' ' || COALESCE(cu.surname,'')),''))
                     AS coach_name
            FROM diary.booking b
            LEFT JOIN diary.resource r ON r.id = b.resource_id
            LEFT JOIN iam."user" cu ON cu.id = b.coach_user_id
            LEFT JOIN iam.coach_profile cp ON cp.user_id = b.coach_user_id AND cp.club_id = b.club_id
            WHERE b.id = :bid AND b.club_id = :c
        """),
        {"bid": str(booking_id), "c": str(club_id)},
    ).mappings().first()
    if not b:
        return None
    if user_id is not None and str(b["booked_by_user_id"]) != str(user_id):
        return None

    venue = session.execute(
        text("SELECT c.name AS club_name, l.address_line, l.city "
             "FROM club.club c LEFT JOIN club.location l ON l.club_id = c.id "
             "WHERE c.id = :c ORDER BY l.id LIMIT 1"),
        {"c": str(club_id)},
    ).mappings().first() or {}

    parties = session.execute(
        text("""
            SELECT bp.user_id, bp.party_role, bp.guest_name,
                   NULLIF(TRIM(COALESCE(u.first_name,'') || ' ' || COALESCE(u.surname,'')),'') AS name
            FROM diary.booking_party bp
            LEFT JOIN iam."user" u ON u.id = bp.user_id
            WHERE bp.booking_id = :b
        """),
        {"b": str(booking_id)},
    ).mappings().all()
    players = []
    for p in parties:
        if p["guest_name"]:
            players.append({"name": p["guest_name"], "kind": "guest"})
        elif user_id is not None and str(p["user_id"]) == str(user_id):
            players.append({"name": "You", "kind": "you"})
        else:
            players.append({"name": p["name"] or "Player", "kind": "player"})
    if not players:
        players.append({"name": "You", "kind": "you"})   # the booker is always a player

    charge = _booking_charge(session, club_id, b["order_id"], b["settlement_mode"])

    starts = b["starts_at"]
    ends = b["ends_at"]
    dur = int((ends - starts).total_seconds() // 60) if (starts and ends) else None
    is_future = bool(starts and starts > datetime.now(timezone.utc))
    status = b["status"]
    court = b["held_court"] if b["booking_type"] == "lesson" else b["resource_name"]
    addr = ", ".join(x for x in [venue.get("address_line"), venue.get("city")] if x) or None

    # Member reschedule is refused inside the cancellation cutoff (reschedule_booking → PAST_CUTOFF),
    # so don't offer a button that will 422. Cancel still SUCCEEDS inside the cutoff but incurs the
    # late fee — surface it so the client can be told BEFORE they confirm.
    policy = _policy(session, club_id)
    cutoff_h = policy.get("cancellation_cutoff_hours") or 0
    within_cutoff = bool(starts and (starts - datetime.now(timezone.utc)) < timedelta(hours=cutoff_h))
    cancel_fee_minor = int(policy.get("no_show_fee_minor") or 0) if (within_cutoff and status in ("held", "confirmed")) else 0

    can = {
        "add_to_calendar": status in ("confirmed", "held", "completed"),
        "cancel": status in ("confirmed", "held", "requested", "proposed") and is_future,
        "reschedule": status in ("confirmed", "held") and is_future and not within_cutoff,
        "pay": charge["status"] in ("owed", "pending"),
        "receipt": charge["status"] in ("paid", "refunded"),
        "request_refund": charge["refundable"],
        "accept": status == "proposed",
        # A client can only DECLINE a coach's proposed time; on a `requested` lesson the awaited party
        # is the coach, so the client's action is WITHDRAW (below) — showing Decline 403'd.
        "decline": status == "proposed",
        "withdraw": status == "requested",
    }
    return {
        "id": str(b["id"]),
        "booking_type": b["booking_type"],
        "status": status,
        "starts_at": starts.isoformat() if starts else None,
        "ends_at": ends.isoformat() if ends else None,
        "duration_minutes": dur,
        "is_future": is_future,
        "court_name": court,
        "coach_name": b["coach_name"],
        "venue": {"club_name": venue.get("club_name"), "address": addr},
        "players": players,
        "charge": charge,
        "cancel_fee_minor": cancel_fee_minor,   # late-cancellation fee if cancelled NOW (0 = none)
        "ics_url": "/api/diary/bookings/" + str(b["id"]) + "/calendar.ics",
        "can": can,
    }


def coach_booking_story(session, *, club_id, coach_user_id, booking_id):
    """The COACH's full view of a lesson/class they RUN: the client (name + contact), when, court,
    the charge + payment status, players + attendance, and the coach's action eligibility
    (accept/propose/decline/reschedule/cancel/mark-completed/mark-no-show). Scoped so a coach only
    sees a booking they run (coach_user_id = them). Composes diary + club + billing. None if not
    found / not theirs — the coach-side mirror of booking_story."""
    b = session.execute(
        text("""
            SELECT b.id, b.club_id, b.booking_type, b.status, b.starts_at, b.ends_at,
                   b.resource_id, r.name AS resource_name, b.coach_user_id, b.order_id,
                   b.settlement_mode, b.booked_by_user_id,
                   (SELECT cr.name FROM diary.booking cb JOIN diary.resource cr ON cr.id = cb.resource_id
                     WHERE cb.club_id = b.club_id AND cb.order_id = b.order_id
                       AND cb.booking_type = 'court' AND b.order_id IS NOT NULL
                       AND b.booking_type = 'lesson' LIMIT 1) AS held_court,
                   u.first_name, u.surname, u.email, u.phone
            FROM diary.booking b
            LEFT JOIN diary.resource r ON r.id = b.resource_id
            LEFT JOIN iam."user" u ON u.id = b.booked_by_user_id
            WHERE b.id = :bid AND b.club_id = :c
        """),
        {"bid": str(booking_id), "c": str(club_id)},
    ).mappings().first()
    if not b or str(b["coach_user_id"]) != str(coach_user_id):
        return None

    venue = session.execute(
        text("SELECT c.name AS club_name, l.address_line, l.city "
             "FROM club.club c LEFT JOIN club.location l ON l.club_id = c.id "
             "WHERE c.id = :c ORDER BY l.id LIMIT 1"),
        {"c": str(club_id)},
    ).mappings().first() or {}

    parties = session.execute(
        text("""
            SELECT bp.user_id, bp.party_role, bp.guest_name, bp.attended,
                   NULLIF(TRIM(COALESCE(u.first_name,'') || ' ' || COALESCE(u.surname,'')),'') AS name
            FROM diary.booking_party bp
            LEFT JOIN iam."user" u ON u.id = bp.user_id
            WHERE bp.booking_id = :b
        """),
        {"b": str(booking_id)},
    ).mappings().all()
    players = [{"name": (p["guest_name"] or p["name"] or "Player"),
                "kind": ("guest" if p["guest_name"] else "player"),
                "attended": p["attended"]} for p in parties]

    charge = _booking_charge(session, club_id, b["order_id"], b["settlement_mode"])
    # The coaching money for THIS lesson (the coach's arrears line) — so collect / discount / write-off
    # live inside the one event story rather than a separate list. Accrue first (idempotent) so an owed
    # lesson has a line to act on.
    arrears = None
    if b["booking_type"] == "lesson":
        try:
            from billing.commission import accrue_arrears_for_club
            accrue_arrears_for_club(session, club_id=club_id)
        except Exception:
            pass
        arr = session.execute(
            text("SELECT id, status, gross_minor FROM billing.coach_arrears "
                 "WHERE club_id = :c AND booking_id = :b ORDER BY created_at DESC LIMIT 1"),
            {"c": str(club_id), "b": str(booking_id)},
        ).mappings().first()
        arrears = ({"id": str(arr["id"]), "status": arr["status"], "gross_minor": int(arr["gross_minor"] or 0)}
                   if arr else None)
    starts, ends = b["starts_at"], b["ends_at"]
    dur = int((ends - starts).total_seconds() // 60) if (starts and ends) else None
    is_future = bool(starts and starts > datetime.now(timezone.utc))
    started = bool(starts and starts <= datetime.now(timezone.utc))
    status = b["status"]
    is_lesson = b["booking_type"] == "lesson"
    court = b["held_court"] if is_lesson else b["resource_name"]
    addr = ", ".join(x for x in [venue.get("address_line"), venue.get("city")] if x) or None
    client_name = " ".join(x for x in [b["first_name"], b["surname"]] if x).strip() or (b["email"] or "Client")

    can = {
        "accept": status == "requested",
        "propose": status in ("requested", "proposed"),
        "decline": status in ("requested", "proposed"),
        "reschedule": status in ("confirmed", "held") and is_future,
        "cancel": status in ("confirmed", "held", "requested", "proposed"),
        "mark_completed": status == "confirmed" and started and is_lesson,
        "mark_no_show": status == "confirmed" and started and is_lesson,
        "add_to_calendar": status in ("confirmed", "held", "completed"),
        # coaching-money actions live here (the one event story), when there's an OWED line to act on
        "collect": bool(arrears and arrears["status"] == "owed"),
        "discount": bool(arrears and arrears["status"] == "owed"),
        "write_off": bool(arrears and arrears["status"] == "owed"),
    }
    return {
        "id": str(b["id"]),
        "booking_type": b["booking_type"],
        "status": status,
        "starts_at": starts.isoformat() if starts else None,
        "ends_at": ends.isoformat() if ends else None,
        "duration_minutes": dur,
        "is_future": is_future,
        "court_name": court,
        "client": {"name": client_name, "email": b["email"], "phone": b["phone"],
                   "user_id": str(b["booked_by_user_id"]) if b["booked_by_user_id"] else None},
        "venue": {"club_name": venue.get("club_name"), "address": addr},
        "players": players,
        "charge": charge,
        "arrears": arrears,
        "ics_url": "/api/diary/bookings/" + str(b["id"]) + "/calendar.ics",
        "can": can,
    }


def admin_booking_story(session, *, club_id, booking_id):
    """The ADMIN god-view of ANY booking in the club — the ONE shared event story that Home,
    People, Money and Diary all drill into (docs/specs/ADMIN-REDESIGN.md — the golden rule). A
    superset of the coach + client stories: the client (name + contact + user_id → person 360),
    the coach (name + user_id), when / where / court, players + attendance, the CHARGE (client
    order) AND the coaching arrears line, plus FULL action eligibility — accept/propose/decline ·
    reschedule/cancel · mark completed/no-show · settle-at-desk/refund/void/write-off (the order) ·
    collect/discount/write-off (the coaching arrears) · reassign coach. NOT user-scoped (an admin
    sees everything in their club). Reuses _booking_charge + the arrears accrual so figures never
    drift from the coach/client views. None if not found in this club."""
    b = session.execute(
        text("""
            SELECT b.id, b.club_id, b.booking_type, b.status, b.starts_at, b.ends_at,
                   b.resource_id, r.name AS resource_name, b.coach_user_id, b.order_id,
                   b.settlement_mode, b.booked_by_user_id, b.notes,
                   (SELECT cr.name FROM diary.booking cb JOIN diary.resource cr ON cr.id = cb.resource_id
                     WHERE cb.club_id = b.club_id AND cb.order_id = b.order_id
                       AND cb.booking_type = 'court' AND b.order_id IS NOT NULL
                       AND b.booking_type = 'lesson' LIMIT 1) AS held_court,
                   cl.first_name AS cl_first, cl.surname AS cl_surname, cl.email AS cl_email,
                   cl.phone AS cl_phone,
                   COALESCE(cp.display_name,
                            NULLIF(TRIM(COALESCE(co.first_name,'') || ' ' || COALESCE(co.surname,'')),''))
                     AS coach_name
            FROM diary.booking b
            LEFT JOIN diary.resource r ON r.id = b.resource_id
            LEFT JOIN iam."user" cl ON cl.id = b.booked_by_user_id
            LEFT JOIN iam."user" co ON co.id = b.coach_user_id
            LEFT JOIN iam.coach_profile cp ON cp.user_id = b.coach_user_id AND cp.club_id = b.club_id
            WHERE b.id = :bid AND b.club_id = :c
        """),
        {"bid": str(booking_id), "c": str(club_id)},
    ).mappings().first()
    if not b:
        return None

    venue = session.execute(
        text("SELECT c.name AS club_name, l.address_line, l.city "
             "FROM club.club c LEFT JOIN club.location l ON l.club_id = c.id "
             "WHERE c.id = :c ORDER BY l.id LIMIT 1"),
        {"c": str(club_id)},
    ).mappings().first() or {}

    parties = session.execute(
        text("""
            SELECT bp.user_id, bp.party_role, bp.guest_name, bp.attended,
                   NULLIF(TRIM(COALESCE(u.first_name,'') || ' ' || COALESCE(u.surname,'')),'') AS name
            FROM diary.booking_party bp
            LEFT JOIN iam."user" u ON u.id = bp.user_id
            WHERE bp.booking_id = :b
        """),
        {"b": str(booking_id)},
    ).mappings().all()
    players = [{"name": (p["guest_name"] or p["name"] or "Player"),
                "kind": ("guest" if p["guest_name"] else "player"),
                "attended": p["attended"]} for p in parties]

    charge = _booking_charge(session, club_id, b["order_id"], b["settlement_mode"])
    is_lesson = b["booking_type"] == "lesson"
    # Coaching money for a lesson (the coach's arrears line) — accrue first (idempotent) so an owed
    # lesson has a line the admin can collect/discount/write-off from inside the one story.
    arrears = None
    if is_lesson:
        try:
            from billing.commission import accrue_arrears_for_club
            accrue_arrears_for_club(session, club_id=club_id)
        except Exception:
            pass
        arr = session.execute(
            text("SELECT id, status, gross_minor FROM billing.coach_arrears "
                 "WHERE club_id = :c AND booking_id = :b ORDER BY created_at DESC LIMIT 1"),
            {"c": str(club_id), "b": str(booking_id)},
        ).mappings().first()
        arrears = ({"id": str(arr["id"]), "status": arr["status"], "gross_minor": int(arr["gross_minor"] or 0)}
                   if arr else None)

    starts, ends = b["starts_at"], b["ends_at"]
    dur = int((ends - starts).total_seconds() // 60) if (starts and ends) else None
    is_future = bool(starts and starts > datetime.now(timezone.utc))
    started = bool(starts and starts <= datetime.now(timezone.utc))
    status = b["status"]
    court = b["held_court"] if is_lesson else b["resource_name"]
    addr = ", ".join(x for x in [venue.get("address_line"), venue.get("city")] if x) or None
    client_name = " ".join(x for x in [b["cl_first"], b["cl_surname"]] if x).strip() or (b["cl_email"] or "Client")

    order_settleable = charge["status"] in ("owed", "pending")
    can = {
        "accept": status == "requested",
        "propose": status in ("requested", "proposed"),
        "decline": status in ("requested", "proposed"),
        "reschedule": status in ("confirmed", "held") and is_future,
        "cancel": status in ("confirmed", "held", "requested", "proposed"),
        "mark_completed": status == "confirmed" and started and is_lesson,
        "mark_no_show": status == "confirmed" and started and is_lesson,
        "add_to_calendar": status in ("confirmed", "held", "completed"),
        # order money (client charge)
        "desk_pay": order_settleable,
        "refund": charge["refundable"],
        "void": order_settleable,
        "write_off": order_settleable,
        # coaching money (coach arrears line)
        "collect": bool(arrears and arrears["status"] == "owed"),
        "discount": bool(arrears and arrears["status"] == "owed"),
        "write_off_coaching": bool(arrears and arrears["status"] == "owed"),
        # reassign is only clean while the lesson is future + unpaid (commission not yet attributed)
        "reassign_coach": is_lesson and is_future and charge["status"] not in ("paid", "refunded"),
    }
    return {
        "id": str(b["id"]),
        "booking_type": b["booking_type"],
        "status": status,
        "starts_at": starts.isoformat() if starts else None,
        "ends_at": ends.isoformat() if ends else None,
        "duration_minutes": dur,
        "is_future": is_future,
        "court_name": court,
        "coach": {"name": b["coach_name"],
                  "user_id": str(b["coach_user_id"]) if b["coach_user_id"] else None},
        "client": {"name": client_name, "email": b["cl_email"], "phone": b["cl_phone"],
                   "user_id": str(b["booked_by_user_id"]) if b["booked_by_user_id"] else None},
        "venue": {"club_name": venue.get("club_name"), "address": addr},
        "players": players,
        "order_id": str(b["order_id"]) if b["order_id"] else None,
        "charge": charge,
        "arrears": arrears,
        "notes": b["notes"],
        "ics_url": "/api/diary/bookings/" + str(b["id"]) + "/calendar.ics",
        "can": can,
    }


def admin_reassign_coach(session, *, club_id, booking_id, new_coach_user_id):
    """Admin god-view action (docs/specs/ADMIN-REDESIGN.md): move a lesson to a different coach.
    Only a FUTURE, not-yet-paid lesson is reassignable — so commission attribution stays clean
    (it accrues on collection to whoever runs the lesson). The lesson's PRIMARY resource IS the
    coach resource, so we point it at the new coach's resource + update coach_user_id; the GiST
    exclusion constraint then enforces no double-book for free (a busy coach -> COACH_BUSY, nothing
    changes). A class the new coach runs at that time (not a diary.booking, so invisible to the
    constraint) is caught explicitly. Returns {ok, booking} or {ok:False, error, status}."""
    from sqlalchemy.exc import IntegrityError
    bk = _booking_dict(session, booking_id)
    if not bk or str(bk["club_id"]) != str(club_id):
        return _err("NOT_FOUND", 404)
    if bk["booking_type"] != "lesson":
        return _err("NOT_A_LESSON", 422, message="only a lesson can be reassigned to a coach")
    if bk["status"] not in ("confirmed", "held", "requested", "proposed"):
        return _err("BAD_STATUS", 422, message="this lesson can't be reassigned")
    starts = _parse_dt(bk["starts_at"])
    ends = _parse_dt(bk["ends_at"])
    if starts <= datetime.now(timezone.utc):
        return _err("IN_THE_PAST", 422, message="only a future lesson can be reassigned")
    if bk.get("order_id"):
        ost = session.execute(
            text('SELECT status FROM billing."order" WHERE id = :o'),
            {"o": str(bk["order_id"])}).scalar()
        if ost in ("paid", "refunded"):
            return _err("ALREADY_PAID", 422, message="a paid lesson can't be reassigned — refund first")
    # The target must be an active, bookable coach in THIS club with a coach resource.
    new_res = session.execute(
        text("""
            SELECT r.id
            FROM diary.resource r
            JOIN iam.membership m ON m.user_id = r.coach_user_id AND m.club_id = r.club_id
                                 AND m.role = 'coach' AND m.member_status = 'active'
            LEFT JOIN iam.coach_profile cp ON cp.user_id = r.coach_user_id AND cp.club_id = r.club_id
            WHERE r.club_id = :c AND r.kind = 'coach' AND r.coach_user_id = :u AND r.is_active
              AND COALESCE(cp.is_bookable, true)
            LIMIT 1
        """),
        {"c": str(club_id), "u": str(new_coach_user_id)},
    ).scalar()
    if not new_res:
        return _err("COACH_NOT_BOOKABLE", 422, message="that coach isn't available for booking")
    if str(new_res) == str(bk["resource_id"]):
        return _err("SAME_COACH", 422, message="the lesson is already with this coach")
    if _coach_class_conflict(session, club_id, new_coach_user_id, starts, ends):
        return _err("COACH_BUSY", 409, message="that coach is running a class at this time")
    sp = session.begin_nested()
    try:
        session.execute(
            text("UPDATE diary.booking SET resource_id = :r, coach_user_id = :u, updated_at = now() "
                 "WHERE id = :b AND club_id = :c"),
            {"r": str(new_res), "u": str(new_coach_user_id), "b": str(booking_id), "c": str(club_id)})
        sp.commit()
    except IntegrityError:
        sp.rollback()
        return _err("COACH_BUSY", 409, message="that coach is already booked at this time")
    return {"ok": True, "booking": _booking_dict(session, booking_id)}


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

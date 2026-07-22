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

# Equipment-hire add-on signal (raised inside the booking savepoint so the whole booking rolls back if an
# item can't fit). Guarded import keeps this lane importable even if the equipment module is absent.
try:
    from diary.equipment import EquipmentUnavailable as _EquipmentUnavailable
except Exception:  # pragma: no cover
    class _EquipmentUnavailable(Exception):
        pass

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


def _court_is_free(session, club_id, court_id, starts, ends, ignore_booking_ids=()):
    """True if `court_id` is an ACTIVE court of this club with nothing held/confirmed and no time-off
    overlapping [starts, ends). `ignore_booking_ids` excludes the rows we're about to move (a booking
    must not block itself on a reschedule). Same shape as _first_free_court, scoped to one court."""
    if not court_id:
        return False
    ignore = [str(x) for x in (ignore_booking_ids or []) if x]
    return bool(session.execute(
        text("SELECT 1 FROM diary.resource r "
             "WHERE r.id = :court AND r.club_id = :c AND r.kind = 'court' AND r.is_active = true "
             "  AND NOT EXISTS (SELECT 1 FROM diary.booking b "
             "      WHERE b.club_id = :c AND b.resource_id = r.id "
             "        AND b.status IN ('held','confirmed') "
             "        AND (:no_ignore OR NOT (b.id = ANY(CAST(:ignore AS uuid[])))) "
             "        AND b.ends_at > :s AND b.starts_at < :e) "
             "  AND NOT EXISTS (SELECT 1 FROM diary.time_off t "
             "      WHERE t.club_id = :c AND t.resource_id = r.id "
             "        AND t.ends_at > :s AND t.starts_at < :e)"),
        {"court": str(court_id), "c": club_id, "s": starts, "e": ends,
         "ignore": ignore, "no_ignore": not ignore},
    ).first())


def _coach_preferred_court(session, club_id, coach_user_id):
    """The coach's configured preferred court (iam.coach_profile.preferred_court_resource_id), or None.
    Guarded: the column is added by the coach lane's boot DDL, so a pre-migration DB just gets None."""
    if not coach_user_id:
        return None
    try:
        return session.execute(
            text("SELECT preferred_court_resource_id FROM iam.coach_profile "
                 "WHERE club_id = :c AND user_id = :u"),
            {"c": club_id, "u": str(coach_user_id)},
        ).scalar()
    except Exception:
        return None


def _pick_court_for_lesson(session, club_id, coach_user_id, starts, ends, ignore_booking_ids=()):
    """Choose the court a lesson holds when the caller didn't name one. The COACH'S PREFERRED COURT
    wins when it's free (coaches asked for their lessons to stop scattering — Colbert always wants
    court 6); otherwise fall back to the first free court so a lesson is never blocked by a busy
    preference. Returns None only when NO court is free."""
    pref = _coach_preferred_court(session, club_id, coach_user_id)
    if pref and _court_is_free(session, club_id, pref, starts, ends, ignore_booking_ids):
        return pref
    return _first_free_court(session, club_id, starts, ends)


_PRODUCT_KIND_BY_BOOKING = {"court": "court_booking", "lesson": "lesson", "class": "class"}


def _service_payment_modes_guarded(session, club_id, booking_type, coach_user_id, product_id=None):
    """The per-service allowed payment methods (or None = no restriction). Guarded — never raises,
    so a missing billing.* can never block a booking. `product_id` scopes to the EXACT service (this
    court service — e.g. Clay — or this lesson service), so a card-only service's rule is enforced;
    without it we'd resolve the generic first-of-kind product and miss a per-service restriction."""
    try:
        from diary.pricing import payment_modes_for
        return payment_modes_for(session, club_id=club_id,
                                 kind=_PRODUCT_KIND_BY_BOOKING.get(booking_type, booking_type),
                                 coach_user_id=coach_user_id, product_id=product_id)
    except Exception:
        return None


def _court_service_guarded(session, club_id, resource_id):
    """The court SERVICE (billing.product id) a court belongs to — its own resource.product_id, else
    the club's default court product. Guarded → None (billing/column absent). Used to price a court
    at ITS service's rate and to reject a court booked under the wrong service."""
    try:
        from diary.pricing import court_service_for_resource
        return court_service_for_resource(session, club_id=club_id, resource_id=resource_id)
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
             "settlement_mode, notes, cancellation_reason, cancelled_at, cancelled_by, "
             # The service this was booked against — accept_booking prices off it, so it MUST be
             # selected here or the read silently returns None and the fallback bites again.
             "product_id "
             "FROM diary.booking WHERE id = :id"),
        {"id": booking_id},
    ).mappings().first()
    if not row:
        return None
    d = dict(row)
    for k in ("id", "club_id", "resource_id", "coach_user_id", "booked_by_user_id",
              "recurrence_id", "order_id", "cancelled_by", "product_id"):
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
    if mode in ("membership_covered", "token"):
        # token: a member spending their own prepaid pack is always allowed; the real gate is
        # whether they actually HOLD a matching wallet (match_wallet → NO_TOKEN otherwise).
        # membership_covered is re-validated as COURT-only + inside the access window downstream.
        return True
    # 'free' is a complimentary/admin-only mode the client UI never offers — a member POSTing it
    # would otherwise get an R0 'paid' booking with no charge. Non-admins are refused here.
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


def _court_covered_guarded(session, *, club_id, user_id, starts_at, ends_at, resource_id, now=None):
    """True if the member's FULL entitlement covers this court booking for free — active membership + inside
    the access window + court-service member-eligible + within max_covered_minutes + under the daily
    booking/court caps (diary.entitlement.court_covered). Outside entitlement -> False -> billed PAYG.
    Guarded; never raises (a missing entitlement module must never block a booking)."""
    try:
        from diary.entitlement import court_covered
        return court_covered(session, club_id=club_id, user_id=user_id, starts_at=starts_at,
                             ends_at=ends_at, resource_id=resource_id, now=now)
    except Exception:
        return False


def release_expired_holds(session, club_id, now=None):
    """Lazy expiry (NO cron): cancel 'held' bookings whose held_until has passed, freeing the
    slot. Called opportunistically at the start of availability + booking creation, so an
    abandoned online checkout (held → never paid) is released the moment anyone looks at that
    diary again. Cheap, indexed UPDATE; safe to run on every request.

    ALSO VOIDS THE ABANDONED ORDER. This used to cancel the booking and leave its unpaid order
    behind, which is where 37 phantom 'awaiting_payment' orders on cancelled bookings came from in
    production. They bill nobody (awaiting_payment is excluded from the statement) but they pollute
    every money read and make an abandoned checkout look like an unpaid debt forever — and the
    statement self-heal (billing.statement._void_phantom_cancelled_orders) only rescues 'open' ones.
    Mirrors cancel_booking, which has always voided the order it cancels.

    A late payment is still safe: billing.events._confirm_held_bookings deliberately RE-INSTATES a
    booking cancelled with reason 'hold_expired' when its charge finally lands."""
    rows = session.execute(
        text("UPDATE diary.booking "
             "SET status='cancelled', cancellation_reason='hold_expired', "
             "    cancelled_at=now(), updated_at=now() "
             "WHERE club_id=:c AND status='held' "
             "  AND held_until IS NOT NULL AND held_until < now() "
             "RETURNING id, order_id"),
        {"c": club_id},
    ).mappings().all()
    _void_orders_with_no_live_bookings(
        session, club_id, {str(r["order_id"]) for r in rows if r.get("order_id")})


def _void_orders_with_no_live_bookings(session, club_id, order_ids):
    """Void each unpaid order that has NO live booking left on it. Scoped this way because one order
    can carry several rows — a lesson plus its auto-held court, or a squad's per-head partners — and
    voiding while any of them is still held/confirmed would erase a real debt. void_order is a no-op
    on a PAID order, so the refund path is untouched. Guarded: money hygiene must never break the
    read that triggered it."""
    for oid in (order_ids or set()):
        try:
            live = session.execute(
                text("SELECT 1 FROM diary.booking WHERE club_id = :c AND order_id = :o "
                     "  AND status IN ('held','confirmed','requested','proposed') LIMIT 1"),
                {"c": club_id, "o": oid},
            ).first()
            if live:
                continue
            from billing.statement import void_order
            void_order(session, club_id=club_id, order_id=oid, reason="hold expired")
        except Exception:
            log.debug("expired-hold order void skipped", exc_info=False)


# booking_type -> billing.product.kind (docs/02 §5). The diary speaks booking types;
# billing speaks product kinds. This adapter is the single translation point.
_KIND_BY_BOOKING_TYPE = {"court": "court_booking", "lesson": "lesson", "class": "class"}

# booking_type -> bundle service_kind (docs/specs/02). The token engine speaks the diary's
# booking-type vocabulary directly (court|lesson|class).
_SERVICE_KIND_BY_BOOKING_TYPE = {"court": "court", "lesson": "lesson", "class": "class"}


def _match_token_wallet_guarded(session, *, club_id, user_id, booking_type,
                                duration_minutes=None, coach_user_id=None, product_id=None):
    """Find (and LOCK, FOR UPDATE) the best token wallet for this booking, or None. Guarded so the
    diary self-verifies without billing.* present. The wallet is held under the caller's tx so the
    subsequent draw_token can't race. service_kind/duration/coach/product drive the match
    (docs/specs/02): a per-service pack (product_id) only draws for THAT service; a legacy unscoped
    pack still draws by kind+coach."""
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
            duration_minutes=duration_minutes, coach_user_id=coach_user_id,
            product_id=product_id)
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
                          token_wallet=None, token_ref=None, coach_user_id=None, product_id=None,
                          price_id=None, addon_lines=None):
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
    # Price the CHOSEN service (product_id) exactly when given — so a coach with several lesson
    # products (Private / Semi-private) charges the one that was booked, not the cheapest. Falls back
    # to coach-scoped kind pricing when no product was specified (older callers / court bookings).
    # PEAK court pricing: convert the booking's UTC start to the club-local time so price_for can pick the
    # peak amount when it falls in the club peak window (charged == what compute_availability showed). Only
    # court rows carry a peak amount, so lessons/classes are unaffected. Guarded -> no peak (base amount).
    at_local = None
    if starts_at is not None:
        try:
            from diary.availability import _club_tz
            at_local = starts_at.astimezone(_club_tz(session, club_id))
        except Exception:
            at_local = None

    def _price(aud):
        # EXACT price row (a class session's own price_id) — never re-resolved/merged, so a class
        # enrolment charges THAT class's rate, not the cheapest class across coaches.
        if price_id:
            row = session.execute(
                text("SELECT id AS price_id, amount_minor, currency_code, unit, duration_minutes "
                     "FROM billing.price WHERE id = :p AND active = true"), {"p": str(price_id)},
            ).mappings().first()
            return dict(row) if row else {}
        if product_id:
            return price_for(session, club_id=club_id, audience=aud, product_id=product_id,
                             duration_minutes=duration_minutes, at_local=at_local) or {}
        return price_for(session, club_id=club_id, audience=aud, kind=kind,
                         duration_minutes=duration_minutes, coach_user_id=coach_user_id,
                         at_local=at_local) or {}
    if member_parties:
        for p in member_parties:
            pr = _price("member")
            lines.append({"description": booking_type, "price_id": pr.get("price_id"),
                          "qty": 1, "amount_minor": _amount(pr), **ref})
    else:
        pr = _price(audience)
        lines.append({"description": booking_type, "price_id": pr.get("price_id"),
                      "qty": 1, "amount_minor": _amount(pr), **ref})

    # EQUIPMENT add-ons ride the SAME order at their REAL flat fee (never zeroed, even on a covered court).
    addon_lines = addon_lines or []
    addon_total = sum(int(l.get("amount_minor") or 0) * int(l.get("qty") or 1) for l in addon_lines)
    lines.extend(addon_lines)
    # If the COURT base is R0 (membership_covered / token / free) but equipment adds a real charge, the
    # ORDER must be collectable — make it an owed at-court order so the equipment is billed while the court
    # stays free (its lines are R0, and the booking row keeps its own settlement_mode). A PAYG court just
    # adds the equipment to its existing order/payment (one order, one payment).
    order_settlement = settlement_mode
    if covered and addon_total > 0:
        order_settlement = "at_court"

    try:
        order_id = create_order_for_booking(
            session, club_id=club_id, user_id=user_id, lines=lines,
            settlement_mode=order_settlement)
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
                   booked_for_user_id=None, propose=False, product_id=None, addons=None,
                   allow_past=False, now=None, extra_clients=None):
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
    # SEMI-PRIVATE (squad) lessons: extra CLIENTS ride the SAME lesson slot but are billed PER HEAD —
    # each gets their OWN owed order at the service price (never merged onto the primary's order). They
    # are booking_party rows on this booking, so they show in each client's own statement / person-360,
    # and a cancel voids every order via order_line.booking_id. Only meaningful for a lesson.
    extra_parties = []
    if extra_clients and booking_type == "lesson":
        seen_extra = {str(owner_user_id)}
        for ec in extra_clients:
            uid = ec.get("user_id") if isinstance(ec, dict) else ec
            if not uid or str(uid) in seen_extra:
                continue
            seen_extra.add(str(uid))
            extra_parties.append({"user_id": str(uid), "party_role": "partner"})
    starts = _parse_dt(starts_at)
    ends = _parse_dt(ends_at)
    if ends <= starts:
        return _err("BAD_RANGE", 400, message="ends_at must be after starts_at")
    # BACK-CAPTURE: a coach/admin logging a lesson/class that ALREADY happened (an on-behalf booking for a
    # past date) may bypass the past-slot guard. Gated hard to staff on-behalf — a member self-booking can
    # NEVER back-date (that would dodge the booking window / late-cancel-fee logic). The route ANDs allow_past
    # with the staff-role check before it reaches here, but we re-assert booked_for_user_id + role locally.
    staff_backcapture = bool(allow_past and booked_for_user_id
                             and role in ("coach", "club_admin", "platform_admin"))
    if starts < now and not staff_backcapture:
        return _err("IN_THE_PAST", 400, message="cannot book a past slot")

    # BACK-CAPTURE fallback: a PAST lesson has no availability slot to carry the coach's resource id
    # (compute_availability never emits past slots), so resolve it from coach_user_id here. The normal
    # path always passes resource_id from the chosen slot, so this only fires for a staff back-capture.
    if booking_type == "lesson" and not resource_id and coach_user_id:
        resource_id = session.execute(
            text("SELECT id FROM diary.resource WHERE club_id = :c AND kind = 'coach' "
                 "AND coach_user_id = :u AND is_active = true LIMIT 1"),
            {"c": club_id, "u": str(coach_user_id)},
        ).scalar()

    res = _resource(session, club_id, resource_id)
    if not res or not res["is_active"]:
        return _err("RESOURCE_NOT_FOUND", 404)

    # BOOKING TYPE MUST MATCH THE RESOURCE, AND 'class' IS NOT BOOKABLE HERE.
    #
    # `booking_type` comes off the request body. The resource-kind check used to live only inside the
    # lesson branch, and the court-service resolution + COURT_NOT_IN_SERVICE guard only inside the
    # court branch — while 'class' is legal in the schema CHECK (that is how a class GiST-reserves its
    # court). So POSTing a COURT resource as booking_type='class' skipped the court block entirely:
    # the payment gate resolved the club's OLDEST class product (usually no payment_modes → gate
    # skipped) and the price resolved to the cheapest class row, so a 120-min hard court billed R120
    # instead of R280, pay-at-court, on a service that may be card-only — and a class PACK holder
    # drew it for a court.
    #
    # Worse than the money: diary/routes.py's staff master feed EXCLUDES booking_type='class' (a class
    # is rendered from its class_session), and a crafted row has no class_session behind it. The court
    # was genuinely GiST-blocked but INVISIBLE to the club — a phantom hold nobody could see or
    # cancel. A real class court hold is inserted by diary.classes._reserve_court_for_class, never by
    # a client POST, and create_booking has exactly one caller (the route), so refusing it here is safe.
    if booking_type not in ("court", "lesson"):
        return _err("BOOKING_TYPE_NOT_ALLOWED", 422,
                    message="only a court or lesson can be booked here")
    _want_res_kind = "coach" if booking_type == "lesson" else "court"
    if res["kind"] != _want_res_kind:
        return _err("RESOURCE_KIND_MISMATCH", 422,
                    message="that resource can't be booked as a " + booking_type)

    # THE POSTED SERVICE MUST BE A REAL SERVICE OF THIS KIND. `product_id` arrives straight off the
    # request body (diary/routes.py) and was validated ONLY on the court branch below. For a LESSON or
    # CLASS it then drove all three of: the per-service payment gate, the PRICE_NOT_CONFIGURED probe,
    # and the ORDER PRICE — and pricing.price_for's product branch carries no kind, coach or
    # product.active predicate (those live in its kind branch), falling through to
    # `amount_minor ASC LIMIT 1`. So posting another service's id billed a R400 lesson at the club's
    # cheapest price, evaluated the card-only rule against the SUBSTITUTED service, and — if the id
    # named a court product — made commission classify a delivered lesson as court, so the coach
    # accrued nothing. Service ids are public to any authenticated member via GET /api/diary/services.
    # The booking UI always posts the right id; this refuses a crafted one, exactly as the guards
    # below are documented to do.
    if product_id:
        want_kind = _PRODUCT_KIND_BY_BOOKING.get(booking_type, booking_type)
        prod_ok = session.execute(
            text("SELECT coach_user_id FROM billing.product "
                 "WHERE club_id = :c AND id = CAST(:p AS uuid) AND kind = :k AND active = true"),
            {"c": club_id, "p": str(product_id), "k": want_kind},
        ).mappings().first()
        if not prod_ok:
            return _err("SERVICE_NOT_VALID", 422,
                        message="that service doesn't exist for this booking type")
        # A LESSON/CLASS service is either shared (NULL coach) or the RESOLVED coach's own — never
        # another coach's, which would price this lesson off a rate card its coach never set.
        _svc_coach = prod_ok.get("coach_user_id")
        if _svc_coach is not None and booking_type in ("lesson", "class"):
            _for_coach = coach_user_id or res.get("coach_user_id")
            if not _for_coach or str(_svc_coach) != str(_for_coach):
                return _err("SERVICE_NOT_VALID", 422,
                            message="that service belongs to a different coach")

    # Court SERVICE resolution + guard (Hardcourt vs Clay). `product_id` from the caller is the CHOSEN
    # court service; the court MUST belong to it (its own resource.product_id, else the club's default
    # court product) — a mismatch is rejected so a hard court can't be booked (and cheaply priced)
    # under the Clay service. When no product_id is posted (single-service club) we still resolve the
    # court's own service so the order is priced by THAT service, and the guard is skipped.
    if booking_type == "court":
        court_own_service = _court_service_guarded(session, club_id, resource_id)
        if product_id and court_own_service and str(product_id) != str(court_own_service):
            return _err("COURT_NOT_IN_SERVICE", 422,
                        message="that court isn't part of the chosen court service")
        product_id = product_id or court_own_service

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
            # A gated (requested) lesson holds NO money yet, but we PRESERVE the client's real intent:
            # online stays online (the coach's accept will keep it HELD + return a Yoco checkout so the
            # client prepays — an online-only coach is never left owed); at_court/monthly/token pass
            # through; membership_covered (COURT-only) / free (admin-only) collapse to at_court.
            _gate_sm = settlement_mode if settlement_mode in ("at_court", "monthly_account", "token", "online") \
                else "at_court"
            # The gate returns BEFORE the main _settlement_allowed / per-service payment_modes checks,
            # so enforce the coach's payment preference HERE for a client self-booking — an online-only
            # coach can't be booked owed (M1). Staff booking on-behalf (booked_for_user_id set) is not
            # gated (handled above) so their at-court override is unaffected.
            if role in ("member", "guest") and _gate_sm in ("online", "at_court", "monthly_account"):
                # Resolve modes by the EXACT chosen lesson service (product_id), not coach/kind alone —
                # a coach with two differently-priced lesson services must be gated on the one booked,
                # else a kind-level resolve reads the first-of-kind product (the known leak pattern).
                _pm = _service_payment_modes_guarded(session, club_id, "lesson", _gate_coach, product_id=product_id)
                if _pm is not None and _gate_sm not in _pm:
                    return _err("SETTLEMENT_NOT_ALLOWED", 422, settlement_mode=_gate_sm,
                                message="this coach doesn't offer that payment method")
            _gid = _insert_booking(
                session, club_id=club_id, booking_type="lesson", resource_id=resource_id,
                coach_user_id=_gate_coach, starts_at=starts, ends_at=ends, status=_gate_status,
                held_until=None, booked_by_user_id=owner_user_id, recurrence_id=recurrence_id,
                created_by_user_id=booked_by_user_id, settlement_mode=_gate_sm, notes=notes,
                product_id=product_id)   # REMEMBER the chosen service — accept_booking prices off it
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
            # No court named → the coach's PREFERRED court if it's free, else the first free one.
            court_resource_id = _pick_court_for_lesson(
                session, club_id, coach_user_id or res.get("coach_user_id"), starts, ends)
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
        # Full entitlement check (the SILENT anti-abuse rules — access window + court-service eligibility
        # + max covered minutes + daily booking/court caps). Anything outside entitlement DOWNGRADES to
        # PAYG (never blocks) — the same behaviour off-peak already uses. resource_id is the primary court.
        if booking_type == "court" and _court_covered_guarded(
                session, club_id=club_id, user_id=owner_user_id, starts_at=starts, ends_at=ends,
                resource_id=resource_id, now=now):
            pass  # legitimately covered (active membership, in window, within caps, member-eligible court)
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

    # Token / PREPAID PACK settlement (docs/specs/02): match a prepaid wallet BEFORE inserting anything
    # (a NO-token request persists nothing → clean NO_TOKEN, UI falls back to PAYG). The matched wallet
    # is locked FOR UPDATE and drawn in the SAME transaction right after order creation. service_kind/
    # duration/coach drive the match: a lesson token may be coach-specific, a court/class token is
    # coach-agnostic; product_id scopes a per-service pack to its exact service.
    #
    # GUARDRAIL (owner decision 2026-07-15): a paid pack must NEVER be silently bypassed. If the booker
    # holds a matching ACTIVE pack and picked an OWED method (pay-at-court / monthly account), auto-route
    # the booking to DRAW the pack (R0 'token') instead of raising a duplicate owed order. This is the
    # server-side safety net BEHIND the UI's pack default — a wrong tap or a stale client can't double-
    # charge a client who already paid for a pack. (Runs before the payment-mode guard so a pack draw is
    # never blocked by a service that restricts cash/online methods.)
    token_wallet = None
    _wants_token = (settlement_mode == "token")
    if _wants_token or settlement_mode in ("at_court", "monthly_account"):
        duration_for_match = int((ends - starts).total_seconds() // 60)
        match_coach = coach_uid if booking_type == "lesson" else None
        token_wallet = _match_token_wallet_guarded(
            session, club_id=club_id, user_id=owner_user_id, booking_type=booking_type,
            duration_minutes=duration_for_match, coach_user_id=match_coach,
            product_id=product_id)
        if token_wallet is not None:
            settlement_mode = "token"          # draw the pack — R0, never a duplicate owed order
        elif _wants_token:
            return _err("NO_TOKEN", 422,
                        message="no matching prepaid token — choose another way to pay")

    # Per-service payment preference (members/guests only; admins/coaches override). A service may
    # offer only a subset of the club-enabled methods — the booking UI already hides the rest, this
    # refuses a crafted request that picks a method the service doesn't offer. Only the money modes
    # are constrained (token/membership_covered/free are not "methods" a service restricts).
    if role in ("member", "guest") and settlement_mode in ("online", "at_court", "monthly_account"):
        # Scope to the EXACT service (the resolved court service / chosen lesson service) so a card-only
        # service (e.g. Clay) actually refuses at-court / month-end — a kind-only resolve would read the
        # club's default court product and silently allow the wrong method.
        pm = _service_payment_modes_guarded(session, club_id, booking_type, coach_uid, product_id=product_id)
        if pm is not None and settlement_mode not in pm:
            return _err("SETTLEMENT_NOT_ALLOWED", 422, settlement_mode=settlement_mode,
                        message="this service doesn't offer that payment method")

    # A BILLABLE booking must have a CONFIGURED price for its service + duration — otherwise
    # _create_order_guarded would silently write an R0 line and a delivered lesson/court would never
    # be owed (a revenue leak). Refuse UP-FRONT (before any insert, like NO_TOKEN above) so nothing
    # persists. Covered/token/free bookings are legitimately R0 and skip this. The picker only offers
    # configured durations, so this only bites a crafted or mis-seeded request. (A5.)
    if settlement_mode not in ("membership_covered", "token", "free"):
        from diary.pricing import price_for as _price_for
        _dur = int((ends - starts).total_seconds() // 60)
        _kind = _KIND_BY_BOOKING_TYPE.get(booking_type, "court_booking")
        def _priced(aud):
            if product_id:
                return _price_for(session, club_id=club_id, audience=aud, product_id=product_id,
                                  duration_minutes=_dur) or {}
            return _price_for(session, club_id=club_id, audience=aud, kind=_kind,
                              duration_minutes=_dur, coach_user_id=coach_uid) or {}
        if not (_priced(audience).get("price_id") or _priced("member").get("price_id")):
            return _err("PRICE_NOT_CONFIGURED", 422,
                        message="this service has no configured price for that duration")

    # --- the concurrency-safe insert(s) inside one transaction ----------
    addon_lines = []
    try:
        with session.begin_nested():  # SAVEPOINT — lets us catch the overlap cleanly
            booking_id = _insert_booking(
                session, club_id=club_id, booking_type=booking_type, resource_id=resource_id,
                coach_user_id=coach_uid, starts_at=starts, ends_at=ends, status=status,
                held_until=held_until, booked_by_user_id=owner_user_id,
                created_by_user_id=booked_by_user_id,   # the ACTOR (staff/parent/self), for the audit + email
                recurrence_id=recurrence_id, settlement_mode=settlement_mode, notes=notes,
                product_id=product_id,
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
                    booked_by_user_id=owner_user_id, created_by_user_id=booked_by_user_id,
                    recurrence_id=recurrence_id,
                    settlement_mode=settlement_mode, notes="(court held for lesson)",
                )
            for p in parties:
                _insert_party(session, booking_id=booking_id, club_id=club_id, party=p)
            for p in extra_parties:  # semi-private squad members (billed on their own orders below)
                _insert_party(session, booking_id=booking_id, club_id=club_id, party=p)
            # EQUIPMENT add-ons (court bookings only): lock each item, re-check availability by TIME,
            # insert booking_equipment rows, and collect their billing lines — all inside THIS savepoint,
            # so an unavailable item (or a race for the last unit) rolls the whole booking back cleanly.
            if addons and booking_type == "court":
                from diary.equipment import reserve_equipment
                addon_lines = reserve_equipment(session, club_id=club_id, booking_id=booking_id,
                                                addons=addons, starts=starts, ends=ends)
    except _EquipmentUnavailable as e:
        return _err("EQUIPMENT_UNAVAILABLE", 409, message=str(e) or "that equipment isn't available")
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
        coach_user_id=coach_uid,   # price a lesson on THIS coach's own rate card (not the cheapest coach's)
        product_id=product_id,     # …and on the CHOSEN service (Private vs Semi-private) when given
        addon_lines=addon_lines,   # equipment hire → extra order lines on the SAME order (no double bill)
    )
    order_id = order.get("order_id")
    if order_id:
        _attach_order(session, booking_id, order_id)
        if linked_court_id:
            _attach_order(session, linked_court_id, order_id)

    # SEMI-PRIVATE per-head billing: one owed order per extra client at the SAME service price. Never
    # attached to booking.order_id (that stays the primary's) — they link via order_line.booking_id, so
    # cancel_booking voids them all and each client sees only their own line. Extras always settle at the
    # desk (owed) — a single Yoco checkout can't collect from multiple payers.
    extra_order_ids = []
    for p in extra_parties:
        # Bill whoever PAYS for this player: the player if a member, else their guardian (a login-less
        # dependent child bills to the adult) — so a parent's two kids raise two orders BOTH owned by them.
        eo = _create_order_guarded(
            session, club_id=club_id, user_id=_bill_owner(session, p["user_id"]), booking_id=booking_id,
            booking_type=booking_type, settlement_mode="at_court", parties=[p],
            resource_id=resource_id, starts_at=starts, ends_at=ends, audience=audience,
            duration_minutes=duration_minutes, coach_user_id=coach_uid, product_id=product_id,
        )
        if eo.get("order_id"):
            extra_order_ids.append(eo["order_id"])

    booking = _booking_dict(session, booking_id)
    if extra_order_ids:
        booking["extra_order_ids"] = extra_order_ids

    # Online stays held -> return checkout; everything else is confirmed -> emit.
    if online:
        return {"ok": True, "booking": booking, "checkout": order.get("checkout")}

    _emit_confirmed(session, booking, res, settlement_mode)
    return {"ok": True, "booking": booking, "checkout": None}


def _insert_booking(session, *, club_id, booking_type, resource_id, coach_user_id,
                    starts_at, ends_at, status, held_until, booked_by_user_id,
                    recurrence_id, settlement_mode, notes, created_by_user_id=None,
                    product_id=None):
    row = session.execute(
        text("INSERT INTO diary.booking "
             "(club_id, booking_type, resource_id, coach_user_id, starts_at, ends_at, "
             " status, held_until, booked_by_user_id, created_by_user_id, recurrence_id, "
             " settlement_mode, notes, product_id) "
             "VALUES (:c, :bt, :rid, :coach, :sa, :ea, :st, :hu, :by, :cby, :rec, :sm, :notes, "
             "        CAST(:pid AS uuid)) "
             "RETURNING id"),
        {"c": club_id, "bt": booking_type, "rid": resource_id, "coach": coach_user_id,
         "sa": starts_at, "ea": ends_at, "st": status, "hu": held_until,
         "by": booked_by_user_id, "cby": created_by_user_id, "rec": recurrence_id,
         "sm": settlement_mode, "notes": notes,
         # Remember WHICH service this was booked against. A gated lesson creates no order, so
         # without this the chosen service is lost by the time it's accepted.
         "pid": str(product_id) if product_id else None},
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

def _order_has_succeeded_charge(session, order_id):
    """True if this order has taken real money (a succeeded charge payment). Guarded → False."""
    if not order_id:
        return False
    try:
        return session.execute(
            text("SELECT 1 FROM billing.payment WHERE order_id = :o AND direction = 'charge' "
                 "AND status = 'succeeded' LIMIT 1"), {"o": str(order_id)}).first() is not None
    except Exception:
        return False


def reschedule_booking(session, *, club_id, booking_id, new_starts_at, new_ends_at,
                       actor_user_id, role, scope="this", now=None, new_court_resource_id=None):
    """Atomically move a booking to a new time AND/OR a new court. The exclusion constraint validates
    the new slot; on conflict we roll back the savepoint so the ORIGINAL booking is preserved
    (docs/03 §10). Honours cancellation_cutoff for member-initiated moves; admins/coaches
    override. `scope` ∈ this|this_future|series for recurring (docs/03 §5).

    `new_court_resource_id` moves the COURT (clients + coaches kept asking to swap courts):
      · a COURT booking  — its own `resource_id` becomes that court;
      · a LESSON         — the lesson stays on the COACH resource and its auto-held court row moves
                           instead (a lesson is coach + held court, one order_id).
    Omit it to keep today's behaviour (a court booking keeps its court; a lesson's court is
    re-picked automatically, preferring the coach's court). Applies to the PRIMARY booking only —
    a series/this_future move shifts the other occurrences in time, never onto one court."""
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
        # Keep a member/guest LESSON move inside the coach's PUBLISHED hours — the picker enforces
        # this on create, so reschedule must too, else a member could move a lesson to a time the
        # coach never offered. Admins/coaches move freely (they already override the cutoff above).
        if bk.get("booking_type") == "lesson":
            from diary.availability import resource_hours_cover
            if not resource_hours_cover(session, club_id=club_id, resource_id=bk["resource_id"],
                                        starts_at=new_s, ends_at=new_e):
                return _err("OUTSIDE_COACH_HOURS", 422,
                            message="the coach isn't available at that time — pick an offered slot")

    # Can't stretch a PAID booking into a LONGER (pricier) slot — that would under-bill (the order is
    # already settled and we don't silently re-charge). Cancel & rebook to change a paid booking's
    # length. A same-length move (or a shorter one) is fine. (Owner decision M7.)
    old_dur = _parse_dt(bk["ends_at"]) - _parse_dt(bk["starts_at"])
    if (new_e - new_s) > old_dur and _order_has_succeeded_charge(session, bk.get("order_id")):
        return _err("PAID_CANNOT_EXTEND", 422,
                    message="this booking is already paid — cancel and rebook to make it longer")

    # A membership-COVERED court can't be moved into a time the membership doesn't cover (off-peak →
    # peak) without a charge — refuse rather than silently keep it free. Book a paid court instead.
    # (Owner decision M5.)
    if bk.get("settlement_mode") == "membership_covered" and bk.get("booked_by_user_id") and \
            not _membership_covers_guarded(session, club_id=club_id,
                                           user_id=bk["booked_by_user_id"], starts_at=new_s):
        return _err("NOT_COVERED_AT_NEW_TIME", 422,
                    message="your membership doesn't cover that time — pick a covered slot, or book a paid court")

    # A lesson must not be moved onto a time the coach runs a scheduled class — a class_session is
    # not a diary.booking, so the GiST exclusion can't catch it (mirror the create/accept guard).
    if _coach_class_conflict(session, club_id, bk.get("coach_user_id"), new_s, new_e):
        return _err("COACH_BUSY", 409, message="the coach runs a class at the new time")

    # An explicit court change is validated up front so the caller gets a precise error instead of a
    # bare SLOT_TAKEN from the exclusion constraint. The booking's OWN rows are excluded from the
    # busy-check — a court booking staying on its court (or shifting within its own slot) must not
    # block itself. Court moves apply to the primary booking only, never a whole series.
    move_court_to = None
    if new_court_resource_id:
        if scope != "this":
            return _err("COURT_MOVE_SINGLE_ONLY", 422,
                        message="change the court on a single booking, not a whole series")
        if bk.get("booking_type") not in ("court", "lesson"):
            return _err("COURT_MOVE_UNSUPPORTED", 422,
                        message="only a court or lesson booking sits on a court")
        own_ids = [booking_id] + _linked_booking_ids(session, club_id, bk.get("order_id"))
        if not _court_is_free(session, club_id, new_court_resource_id, new_s, new_e, own_ids):
            return _err("COURT_NOT_AVAILABLE", 422,
                        message="that court isn't free at the new time — pick another")
        # A court booking is PRICED BY ITS COURT SERVICE (Hardcourt vs Clay), and each service has its
        # own rate, payment rules and membership eligibility. A move ACROSS services would keep the
        # old price and the old settlement mode — a R100 pay-at-court Hardcourt booking could become a
        # card-only R250 Clay court and stay both cheap and owed. reprice_booking_order re-prices on
        # the SAME product, so it cannot correct a service change either. Refuse and let them cancel
        # and rebook, exactly as a PAID booking refuses to be extended. (A lesson is priced by its
        # LESSON service, so its held court may move freely between court services.)
        if bk.get("booking_type") == "court":
            now_service = _court_service_guarded(session, club_id, bk.get("resource_id"))
            new_service = _court_service_guarded(session, club_id, new_court_resource_id)
            # Compare with None NORMALISED, not `a and b and a != b`. In a MULTI-service club an
            # unallocated court (resource.product_id NULL) resolves to None — ambiguous — and a
            # short-circuit would wave through a move from that court onto an allocated one, which
            # changes the effective service just as much. Both-None (the single-service club, or a
            # club with no court product) still compares equal and is allowed, so nothing regresses.
            if str(now_service or "") != str(new_service or ""):
                return _err("COURT_SERVICE_CHANGED", 422,
                            message="that court belongs to a different court service — "
                                    "cancel and rebook to change service")
        # A membership-COVERED court is free only for courts the membership actually covers (clay is
        # commonly excluded). The time-window check above is not enough — re-run the FULL entitlement
        # against the TARGET court, or a member could move a free booking onto a court they are never
        # covered for and keep it free.
        if bk.get("settlement_mode") == "membership_covered" and bk.get("booked_by_user_id"):
            if not _court_covered_guarded(session, club_id=club_id, user_id=bk["booked_by_user_id"],
                                          starts_at=new_s, ends_at=new_e,
                                          resource_id=new_court_resource_id):
                return _err("COURT_NOT_COVERED", 422,
                            message="your membership doesn't cover that court — pick another, "
                                    "or book it as a paid court")
        move_court_to = str(new_court_resource_id)

    # A lesson's auto-held court was auto-assigned, so on a move we reassign it to a FREE court at the
    # new time rather than failing if the original court is busy there. An explicit choice wins;
    # otherwise prefer the COACH'S preferred court, else the first free one. Pre-check one is free. (L2.)
    reassign_court_to = None
    if bk.get("order_id") and bk.get("booking_type") == "lesson" and scope == "this":
        reassign_court_to = move_court_to or _pick_court_for_lesson(
            session, club_id, bk.get("coach_user_id"), new_s, new_e,
            [booking_id] + _linked_booking_ids(session, club_id, bk.get("order_id")))
        if reassign_court_to is None:
            return _err("NO_COURT_AVAILABLE", 422, message="no court is free at the new time")

    # series / this_future reschedule shifts each affected occurrence by the same delta.
    targets = _reschedule_targets(session, bk, scope)
    delta = new_s - _parse_dt(bk["starts_at"])

    try:
        with session.begin_nested():
            for t_id, t_start, t_end in targets:
                ts = (new_s, new_e) if t_id == booking_id else (
                    _parse_dt(t_start) + delta, _parse_dt(t_end) + delta)
                # A COURT booking's own resource IS the court, so an explicit court move rewrites it
                # here. A LESSON sits on the coach resource — its court moves on the linked row below.
                if move_court_to and t_id == booking_id and bk.get("booking_type") == "court":
                    session.execute(
                        text("UPDATE diary.booking SET starts_at=:sa, ends_at=:ea, resource_id=:r, "
                             "updated_at=now() WHERE id=:id"),
                        {"sa": ts[0], "ea": ts[1], "r": move_court_to, "id": t_id},
                    )
                else:
                    session.execute(
                        text("UPDATE diary.booking SET starts_at=:sa, ends_at=:ea, updated_at=now() "
                             "WHERE id=:id"),
                        {"sa": ts[0], "ea": ts[1], "id": t_id},
                    )
                # Move the linked court (lesson's auto-held court, same order_id). Reassign it to a
                # free court at the new time when we resolved one (L2); else move it as-is (by delta).
                if bk.get("order_id") and reassign_court_to and t_id == booking_id:
                    session.execute(
                        text("UPDATE diary.booking SET resource_id=:r, starts_at=:sa, ends_at=:ea, "
                             "updated_at=now() WHERE club_id=:c AND order_id=:o AND id<>:id "
                             "  AND booking_type='court' AND status IN ('held','confirmed')"),
                        {"r": reassign_court_to, "sa": ts[0], "ea": ts[1], "c": club_id,
                         "o": bk["order_id"], "id": t_id},
                    )
                elif bk.get("order_id"):
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

    # If the reschedule changed the DURATION (not just the start time), re-price the order + owed
    # coaching to the new length so the charge always matches the booked time — a 30-min lesson costs
    # the 30-min price even if it was first booked (and priced) as 45 min. Guarded, unpaid-only; only
    # the primary target can change length (series occurrences just shift by the same delta).
    old_dur = _parse_dt(bk["ends_at"]) - _parse_dt(bk["starts_at"])
    if abs((new_e - new_s).total_seconds() - old_dur.total_seconds()) >= 60:
        try:
            from billing.orders import reprice_booking_order
            reprice_booking_order(session, club_id=club_id, booking_id=booking_id,
                                  duration_minutes=int((new_e - new_s).total_seconds() // 60))
        except Exception:
            log.debug("reprice after reschedule skipped (non-fatal)", exc_info=False)

    booking = _booking_dict(session, booking_id)
    res = _resource(session, club_id, booking["resource_id"])
    events.emit("booking_rescheduled", _payload(booking, res))
    return {"ok": True, "booking": booking}


def _linked_booking_ids(session, club_id, order_id):
    """Every other booking row sharing this order (a lesson's auto-held court, squad partners). Used
    to exclude a booking's OWN rows from an availability check so it can't block itself on a move."""
    if not order_id:
        return []
    return [str(r[0]) for r in session.execute(
        text("SELECT id FROM diary.booking WHERE club_id = :c AND order_id = :o"),
        {"c": club_id, "o": str(order_id)}).all()]


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

    member_initiated = role in ("member", "guest")
    # A member/guest may NOT cancel a lesson/court that has already STARTED: otherwise a
    # delivered-but-owed booking could be cancelled after the fact, voiding its owed order and
    # erasing the debt (a real leak on clubs with no late-cancel fee). Admins/coaches may still
    # cancel a started booking (a PAID order keeps the usual separate-refund prompt). A still-pending
    # requested/proposed booking holds no slot or debt, so withdrawing it stays allowed.
    if (member_initiated and bk["status"] in ("held", "confirmed")
            and _parse_dt(bk["starts_at"]) <= now):
        return _err("CANNOT_CANCEL_STARTED", 409)

    policy = _policy(session, club_id)
    cutoff_h = policy.get("cancellation_cutoff_hours") or 0
    within_cutoff = (_parse_dt(bk["starts_at"]) - now) < timedelta(hours=cutoff_h)
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

    # A cancelled booking must NOT leave a phantom debt: void its owed order(s) (open/awaiting_payment)
    # so it drops off each client's statement AND off the coach's tab (void_order clears arrears too).
    # A PAID order is left intact — refunding a paid booking is a separate, explicit flow. Mirrors
    # cancel_membership. SEMI-PRIVATE: a squad lesson raises ONE order PER head (all linked via
    # order_line.booking_id, not booking.order_id), so we void EVERY order referencing this booking —
    # a bare booking.order_id would leave the partners' debts stranded.
    try:
        from billing.statement import void_order
        order_ids = [r[0] for r in session.execute(
            text("SELECT DISTINCT order_id FROM billing.order_line WHERE booking_id = :b"),
            {"b": booking_id}).all() if r[0]]
        if bk.get("order_id") and bk["order_id"] not in order_ids:
            order_ids.append(bk["order_id"])
        for oid in order_ids:
            void_order(session, club_id=club_id, order_id=oid, reason="booking cancelled")
    except Exception:
        log.info("cancel_booking: order void skipped (billing unavailable) order=%s", bk.get("order_id"))

    # Actually BILL the late-cancel / no-show fee (owner decision M6): raise a small owed order on
    # the client so it lands on their statement to collect. Guarded + best-effort — never blocks the
    # cancel. (Idempotent by construction: a re-cancel returns ALREADY_CLOSED before reaching here.)
    if fee_applies and fee_minor > 0 and bk.get("booked_by_user_id"):
        try:
            from billing.orders import create_order_for_booking
            create_order_for_booking(
                session, club_id=club_id, user_id=bk["booked_by_user_id"],
                lines=[{"description": "Late cancellation fee", "amount_minor": fee_minor, "qty": 1}],
                settlement_mode="at_court")
        except Exception:
            log.info("cancel_booking: fee order skipped (billing unavailable)")

    booking = _booking_dict(session, booking_id)
    res = _resource(session, club_id, booking["resource_id"])
    payload = _payload(booking, res)
    payload["fee_minor"] = fee_minor
    payload["fee_applied"] = fee_applies
    events.emit("booking_cancelled", payload)

    promoted = _promote_court_waitlist(session, club_id=club_id,
                                       resource_id=booking["resource_id"],
                                       desired_start=_parse_dt(booking["starts_at"]))
    # Flag a PAID cancellation so the UI can prompt a refund (the paid order is left intact — a refund
    # is a separate, explicit flow, so without this the client got no indication). (L1.)
    return {"ok": True, "booking": booking, "fee_applied": fee_applies,
            "fee_minor": fee_minor, "waitlist_notified": promoted,
            "token_credited": credited,
            "was_paid": _order_has_succeeded_charge(session, bk.get("order_id"))}


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
    # Can't complete / no-show a booking that hasn't STARTED yet — the event story's
    # can.mark_completed already requires `started`; this closes the raw API so a coach can't mark a
    # FUTURE lesson done (which would corrupt attendance + feedback/NPS signals).
    if _parse_dt(bk["starts_at"]) > datetime.now(timezone.utc):
        return _err("CANNOT_COMPLETE_FUTURE", 409)
    session.execute(
        text("UPDATE diary.booking SET status=:st, updated_at=now() WHERE id=:id"),
        {"st": new_status, "id": booking_id},
    )
    booking = _booking_dict(session, booking_id)
    if new_status == "completed" and booking["booking_type"] == "lesson":
        res = _resource(session, club_id, booking["resource_id"])
        payload = _payload(booking, res)
        # The lesson is done → this is the review moment. Carry the CLIENT's email (so the Klaviyo
        # forward can fire the post-lesson flow) + a signed, per-recipient /feedback URL the email
        # CTA links to (happy → Google review → local reach; unhappy → private form). Best-effort +
        # self-gating: a resolution miss just drops the extras, never blocks the status change.
        try:
            client_uid = booking.get("booked_by_user_id")
            if client_uid:
                em = session.execute(
                    text("SELECT email FROM iam.user WHERE id = CAST(:u AS uuid)"),
                    {"u": client_uid},
                ).scalar()
                if em:
                    payload["email"] = em
                from marketing_crm.feedback import feedback_url_for
                fb = feedback_url_for(client_uid, booking["club_id"], context="lesson")
                if fb:
                    payload["feedback_url"] = fb
        except Exception:
            pass
        events.emit("lesson_completed", payload)
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
    # Defence in depth: a lesson is never membership_covered/free (court-only / admin-only). If a row
    # somehow carries one, settle it at the desk rather than mint an R0 'paid' lesson on accept.
    if settlement_mode in ("membership_covered", "free"):
        settlement_mode = "at_court"
    online = settlement_mode == "online"
    status = "held" if online else "confirmed"
    held_until = (now + timedelta(minutes=HOLD_MINUTES_DEFAULT)) if online else None

    if _coach_class_conflict(session, club_id, coach_uid, starts, ends):
        return _err("COACH_BUSY", 409, message="the coach is running a class at this time")

    court_resource_id = _first_free_court(session, club_id, starts, ends)
    if not court_resource_id:
        return _err("NO_COURT_AVAILABLE", 422, message="no court is free at this time")

    # Same GUARDRAIL as create_booking: a matching prepaid pack is drawn even when an OWED method was
    # picked, so a paid pack is never bypassed (no duplicate owed order). Explicit 'token' with no
    # wallet still errors NO_TOKEN.
    # THE SERVICE THIS LESSON WAS ACTUALLY BOOKED AGAINST, remembered on the booking row at request
    # time. A gated lesson creates no order, so without it the chosen service was gone by now and
    # pricing fell back to the coach's CHEAPEST service (price_for's tie-break is amount_minor ASC) —
    # a R400 Private billed as a R250 Semi-private, with commission and earnings attribution wrong to
    # match. NULL for rows predating the column: behaviour is then exactly as before.
    gated_product_id = bk.get("product_id")

    token_wallet = None
    _wants_token = (settlement_mode == "token")
    if _wants_token or settlement_mode in ("at_court", "monthly_account"):
        dur = int((ends - starts).total_seconds() // 60)
        token_wallet = _match_token_wallet_guarded(
            session, club_id=club_id, user_id=owner_user_id, booking_type="lesson",
            duration_minutes=dur, coach_user_id=coach_uid,
            # Scope the pack to the SAME service too — a NULL request product matches anything and
            # prefers a product-scoped wallet, so a pack bought for another service was being burned.
            product_id=gated_product_id)
        if token_wallet is not None:
            settlement_mode = "token"
        elif _wants_token:
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
                created_by_user_id=actor_user_id,
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
        duration_minutes=duration_minutes, token_wallet=token_wallet,
        coach_user_id=coach_uid,       # price on THIS coach's own rate card…
        product_id=gated_product_id)   # …and on the EXACT service they booked, not its cheapest sibling
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
    # Validate the proposed slot is actually honourable, so the OTHER party can always accept it
    # (otherwise a coach proposes a time they can't run → the client accepts → accept fails → a
    # dead-end 'proposed'). Mirror accept_booking's coach∩court checks.
    if _coach_class_conflict(session, club_id, bk.get("coach_user_id"), starts, ends):
        return _err("COACH_BUSY", 409, message="the coach is running a class at that time")
    if _first_free_court(session, club_id, starts, ends) is None:
        return _err("NO_COURT_AVAILABLE", 422, message="no court is free at that time")
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
    # NULL-safe: a standalone court hire has notes NULL — a bare `b.notes = '…'` makes the whole
    # NOT(...) evaluate to NULL and the row is dropped, hiding real court bookings. IS DISTINCT FROM
    # keeps NULL-notes courts and still hides the auto-held-for-lesson court row.
    where.append("(b.booking_type <> 'court' OR b.notes IS DISTINCT FROM '(court held for lesson)')")
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


def _bill_owner(session, player_user_id):
    """Who PAYS for a player: the player themselves if they're a member with their own account, else
    their GUARDIAN — a login-less dependent (child) is billed to the adult (docs: spend rolls up to the
    payer = order.user_id = guardian; activity rolls up to the player). So a semi-private with two of a
    parent's kids raises two per-head orders, BOTH owned by the parent."""
    try:
        from iam.repositories import guardian_user_id_for
        g = guardian_user_id_for(session, str(player_user_id))
        return str(g) if g else str(player_user_id)
    except Exception:
        return str(player_user_id)


def _lesson_head_count(session, booking_id):
    """How many billed CLIENTS (heads) a lesson currently has — one order per head (per-head billing),
    so a distinct-order count is the head count regardless of members vs dependents."""
    return int(session.execute(
        text("SELECT count(DISTINCT order_id) FROM billing.order_line "
             "WHERE booking_id = :b AND order_id IS NOT NULL"), {"b": booking_id}).scalar() or 0)


def _lesson_max_clients(session, booking_id, order_id):
    """The semi-private cap for a lesson — read from the service (product) the primary order priced under."""
    mc = session.execute(
        text("SELECT COALESCE(pp.max_clients, 1) FROM billing.order_line ol "
             "JOIN billing.price prc ON prc.id = ol.price_id "
             "JOIN billing.product pp ON pp.id = prc.product_id "
             "WHERE ol.booking_id = :b AND ol.order_id = :o ORDER BY ol.created_at LIMIT 1"),
        {"b": booking_id, "o": order_id}).scalar()
    return int(mc or 1)


def _can_add_lesson_partner(session, *, booking_id, order_id, booking_type, status):
    """True if this lesson is semi-private (service max_clients > 1) and still has room for another
    client — drives the 'Add player' action in the event story (add a squad member after booking)."""
    if booking_type != "lesson" or status not in ("confirmed", "held", "completed"):
        return False
    max_clients = _lesson_max_clients(session, booking_id, order_id)
    if max_clients <= 1:
        return False
    return _lesson_head_count(session, booking_id) < max_clients   # still below the per-head cap


def add_lesson_partner(session, *, club_id, booking_id, new_user_id, actor_user_id, role, now=None):
    """Add ANOTHER client to an EXISTING semi-private lesson, AFTER it was first booked. Squad
    confirmations typically land later, so a coach/admin (or the original booker) can attach the
    partner when they commit — no need to know both players up front.

    Identical billing to booking two clients together: the new client becomes a booking_party
    ('partner') and gets their OWN owed order at the same service price (per-head, at_court), linked
    via order_line.booking_id. Respects the service's max_clients cap. Nothing on the primary changes.
    Permission is enforced at the route (same gate as reschedule — staff or the booking's owner)."""
    now = now or datetime.now(timezone.utc)
    bk = _booking_dict(session, booking_id)
    if not bk or str(bk["club_id"]) != str(club_id):
        return _err("NOT_FOUND", 404)
    if bk["booking_type"] != "lesson":
        return _err("NOT_A_LESSON", 422, message="only a lesson can be semi-private")
    if bk["status"] == "cancelled":
        return _err("BOOKING_CANCELLED", 409, message="that lesson was cancelled")
    new_user_id = str(new_user_id) if new_user_id else None
    if not new_user_id or new_user_id == str(bk["booked_by_user_id"]):
        return _err("BAD_CLIENT", 422, message="pick a different client")
    # Already on this lesson (as a non-guest party or the primary)?
    if session.execute(
        text("SELECT 1 FROM diary.booking_party WHERE booking_id = :b AND user_id = :u "
             "AND party_role <> 'guest'"), {"b": booking_id, "u": new_user_id}).first():
        return _err("ALREADY_ON_LESSON", 409, message="that client is already on this lesson")
    # Resolve the SERVICE (product_id) + its max_clients from the primary order's line, so the added
    # head is priced by exactly the same service the lesson was booked under (two-tier coach pricing).
    prod = session.execute(
        text("SELECT prc.product_id AS pid, COALESCE(pp.max_clients, 1) AS mc "
             "FROM billing.order_line ol JOIN billing.price prc ON prc.id = ol.price_id "
             "JOIN billing.product pp ON pp.id = prc.product_id "
             "WHERE ol.booking_id = :b AND ol.order_id = :o "
             "ORDER BY ol.created_at LIMIT 1"),
        {"b": booking_id, "o": bk["order_id"]}).mappings().first()
    product_id = str(prod["pid"]) if prod and prod["pid"] else None
    max_clients = int(prod["mc"]) if prod else 1
    # Cap by billed HEADS (one order per head) — robust whether the players are members or a parent's
    # dependents. Already-full → refuse before touching anything.
    if _lesson_head_count(session, booking_id) + 1 > max_clients:
        return _err("LESSON_FULL", 409,
                    message="this lesson is already at its client limit (raise Max clients on the service)")
    # Insert the partner (the PLAYER — a member or a login-less dependent child) + raise the owed order
    # to whoever PAYS (the player if a member, else their guardian), linked via order_line.booking_id.
    _insert_party(session, booking_id=booking_id, club_id=club_id,
                  party={"user_id": new_user_id, "party_role": "partner"})
    bill_user_id = _bill_owner(session, new_user_id)
    starts = _parse_dt(bk["starts_at"]); ends = _parse_dt(bk["ends_at"])
    dur = int((ends - starts).total_seconds() // 60)
    eo = _create_order_guarded(
        session, club_id=club_id, user_id=bill_user_id, booking_id=booking_id,
        booking_type="lesson", settlement_mode="at_court",
        parties=[{"user_id": new_user_id, "party_role": "partner"}],
        resource_id=bk["resource_id"], starts_at=starts, ends_at=ends, audience="member",
        duration_minutes=dur, coach_user_id=bk["coach_user_id"], product_id=product_id)
    booking = _booking_dict(session, booking_id)
    booking["added_order_id"] = eo.get("order_id")
    return {"ok": True, "booking": booking, "order_id": eo.get("order_id")}


def _booking_charge(session, club_id, order_id, settlement_mode):
    """The charge + payment status for a booking's order (guarded cross-lane read). Maps the order
    status to a client word: paid / owed / pending / refunded / cancelled / covered."""
    covered = settlement_mode in ("membership_covered", "free", "token")
    base = {"amount_minor": 0, "currency": "ZAR",
            "status": "covered" if covered else "none",
            "settlement_mode": settlement_mode, "order_id": str(order_id) if order_id else None,
            "refundable": False, "has_open_refund": False,
            # corrected reconciliation block (derived from payment ROWS, not order.status alone)
            "gross_minor": 0, "paid_minor": 0, "refunded_minor": 0, "net_paid_minor": 0,
            "owed_minor": 0, "written_off_minor": 0, "state": "covered" if covered else "none"}
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
        # The fold: BILLED (pre-discount, from the order lines' original amount) − DISCOUNT = the
        # current amount; − WRITTEN-OFF = INVOICED. So the event's money is the sum of its transactions.
        billed = int(session.execute(
            text("SELECT COALESCE(SUM(COALESCE(original_amount_minor, amount_minor)),0) "
                 "FROM billing.order_line WHERE order_id = :o"),
            {"o": str(order_id)},
        ).scalar() or 0) or amt
        # A voided (cancelled) order is R0 across the board — never bill/invoice it.
        if o["status"] == "void":
            billed = 0
        discount = max(0, billed - amt)
        openref = session.execute(
            text("SELECT 1 FROM billing.refund_request WHERE order_id = :o AND status = 'pending' LIMIT 1"),
            {"o": str(order_id)},
        ).first()
        # Money actually moved — from the payment rows, so a PARTIAL refund reports the real net kept
        # (order.status flips fully to 'refunded' on any refund, which is lossy).
        ps = session.execute(
            text("SELECT "
                 "COALESCE(SUM(CASE WHEN direction='charge' AND status='succeeded' THEN amount_minor END),0) AS paid, "
                 "COALESCE(SUM(CASE WHEN direction='refund' AND status IN ('refunded','succeeded') THEN amount_minor END),0) AS refunded "
                 "FROM billing.payment WHERE order_id = :o"),
            {"o": str(order_id)},
        ).mappings().first()
        paid = int(ps["paid"] or 0); refunded = int(ps["refunded"] or 0)
        owed = amt if o["status"] == "open" else 0
        written_off = amt if o["status"] == "written_off" else 0
        if covered and amt == 0:
            state = "covered"
        elif o["status"] == "open":
            state = "owed"
        elif o["status"] == "awaiting_payment":
            state = "pending"
        elif o["status"] == "written_off":
            state = "written_off"
        elif o["status"] == "void":
            state = "void"
        elif refunded > 0 and refunded < paid:
            state = "part_refunded"
        elif refunded > 0:
            state = "refunded"
        else:
            state = "paid"
        return {"amount_minor": amt, "currency": o["currency_code"] or "ZAR", "status": status,
                "settlement_mode": settlement_mode, "order_id": str(order_id),
                "refundable": (o["status"] == "paid" and not openref),
                "has_open_refund": bool(openref),
                "gross_minor": amt, "paid_minor": paid, "refunded_minor": refunded,
                "net_paid_minor": paid - refunded, "owed_minor": owed,
                "written_off_minor": written_off, "state": state,
                # the fold (billed − discount − written_off = invoiced):
                "billed_minor": billed, "discount_minor": discount,
                "invoiced_minor": (0 if o["status"] in ("written_off", "void") else amt)}
    except Exception:
        base["status"] = "unknown"; base["state"] = "unknown"
        return base


def _event_log(session, club_id, *, scope, user_id, order_id, booking_id):
    """The per-event chronological history (oldest→newest) for the transaction record — the SHARED
    transaction_log filtered to this one event, role-scoped (client hides commission; coach/owner see
    it). Guarded → [] so it never breaks a story."""
    try:
        from billing.activity import transaction_log
        rows = transaction_log(session, club_id=club_id, scope=scope, user_id=user_id, limit=100,
                               event={"order_id": order_id, "booking_id": booking_id})
        rows.reverse()   # story reads oldest → newest
        return rows
    except Exception:
        return []


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
        # A semi-private lesson the client booked — they can add a fellow player later (each billed).
        "add_player": _can_add_lesson_partner(session, booking_id=b["id"], order_id=b["order_id"],
                                              booking_type=b["booking_type"], status=status),
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
        "log": _event_log(session, club_id, scope="client", user_id=user_id,
                          order_id=b["order_id"], booking_id=b["id"]),
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
        # Semi-private: the coach can add another client to this lesson later (each billed per-head).
        "add_player": _can_add_lesson_partner(session, booking_id=b["id"], order_id=b["order_id"],
                                              booking_type=b["booking_type"], status=status),
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
        "log": _event_log(session, club_id, scope="coach", user_id=coach_user_id,
                          order_id=b["order_id"], booking_id=b["id"]),
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
        # Semi-private: admin can add another client to this lesson later (each billed per-head).
        "add_player": _can_add_lesson_partner(session, booking_id=b["id"], order_id=b["order_id"],
                                              booking_type=b["booking_type"], status=status),
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
        "log": _event_log(session, club_id, scope="owner", user_id=None,
                          order_id=b["order_id"], booking_id=b["id"]),
        "ics_url": "/api/diary/bookings/" + str(b["id"]) + "/calendar.ics",
        "can": can,
    }


def order_story(session, *, club_id, order_id, scope="owner", user_id=None):
    """The transaction record for a STANDALONE PURCHASE order (session pack / membership / ad-hoc
    invoice — an order with NO booking). Returns the SAME 'event story' shape a booking does, so the
    shared Widgets.TransactionDetail renders it identically: the money card (reuses _booking_charge)
    + the full-lifecycle audit LOG (reuses _event_log: created → paid → activated → cancelled/voided
    → refunded) + `can` actions. So a package/membership purchase is a FIRST-CLASS transaction record
    (audit trail + void/cancel/refund) instead of a dead-end receipt.

    scope: 'owner'/'admin' → full actions (desk_pay/void/write_off/refund/receipt); 'client' → the
    caller's OWN order only (pay/receipt/request_refund). None if not found / not the caller's."""
    o = session.execute(
        text('SELECT o.id, o.user_id, o.amount_minor, o.currency_code, o.settlement_mode, o.status, '
             '       o.created_at, u.first_name, u.surname, u.email, u.phone '
             'FROM billing."order" o LEFT JOIN iam."user" u ON u.id = o.user_id '
             'WHERE o.id = :o AND o.club_id = :c'),
        {"o": str(order_id), "c": str(club_id)},
    ).mappings().first()
    if not o:
        return None
    if scope == "client" and (not user_id or str(o["user_id"]) != str(user_id)):
        return None
    if scope == "coach":
        # A coach may open ONLY an order that is THEIR earning — a pack they sold (token_wallet), or an
        # order whose booking/enrolment is their own session. Same linkage the earnings CTE uses.
        if not user_id:
            return None
        owns = session.execute(
            text("""
                SELECT 1
                FROM billing.order_line ol
                LEFT JOIN diary.booking b ON b.id = ol.booking_id
                LEFT JOIN diary.enrolment en ON en.id = ol.enrolment_id
                LEFT JOIN diary.class_session cs ON cs.id = en.class_session_id
                LEFT JOIN billing.token_wallet tw ON tw.order_id = ol.order_id
                WHERE ol.order_id = :o
                  AND (b.coach_user_id = :u OR cs.coach_user_id = :u OR tw.coach_user_id = :u)
                LIMIT 1
            """),
            {"o": str(order_id), "u": str(user_id)},
        ).first()
        if not owns:
            return None

    line = session.execute(
        text("SELECT COALESCE(p.name, NULLIF(ol.description,'')) AS service "
             "FROM billing.order_line ol LEFT JOIN billing.price pr ON pr.id = ol.price_id "
             "LEFT JOIN billing.product p ON p.id = pr.product_id "
             "WHERE ol.order_id = :o ORDER BY ol.created_at LIMIT 1"),
        {"o": str(order_id)},
    ).mappings().first() or {}

    # Purchase kind drives the chip + the membership-aware void cleanup.
    from billing import bundles as _bn, membership as _mb
    if _bn.is_bundle_order(session, order_id=str(order_id)):
        kind = "pack"
    elif _mb.is_membership_order(session, order_id=str(order_id)):
        kind = "membership"
    else:
        kind = "invoice"

    charge = _booking_charge(session, club_id, str(order_id), o["settlement_mode"])
    state = charge.get("state")
    owed_or_pending = state in ("owed", "pending")

    if scope == "client":
        client_block = None                    # the client IS the caller — the widget omits it
        can = {"pay": owed_or_pending,
               "receipt": state in ("paid", "refunded", "part_refunded"),
               "request_refund": bool(charge.get("refundable"))}
    else:
        name = " ".join(x for x in [o["first_name"], o["surname"]] if x).strip() or (o["email"] or "Client")
        client_block = {"name": name, "email": o["email"], "phone": o["phone"],
                        "user_id": str(o["user_id"]) if o["user_id"] else None}
        if scope == "coach":
            # A coach sees their own earning read-only — the fold + audit log + a receipt, but NEVER the
            # club's money actions (desk_pay/void/write_off/refund stay owner-only).
            can = {"receipt": state in ("paid", "refunded", "part_refunded")}
        else:
            can = {"desk_pay": owed_or_pending, "void": owed_or_pending, "write_off": owed_or_pending,
                   "refund": bool(charge.get("refundable")), "receipt": True}

    return {
        "id": str(o["id"]), "order_id": str(o["id"]),
        "booking_type": kind,                  # pack | membership | invoice (chip + label)
        "service": line.get("service"),
        "status": o["status"],
        "starts_at": o["created_at"].isoformat() if o["created_at"] else None,
        "ends_at": None,
        "client": client_block,
        "charge": charge,
        "log": _event_log(session, club_id,
                          scope=("client" if scope == "client" else "owner"),
                          user_id=(str(user_id) if scope == "client" else None),
                          order_id=str(order_id), booking_id=None),
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

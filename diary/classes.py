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
from datetime import datetime, timedelta, time as _time, timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from diary import events
from diary.bookings import _create_order_guarded, _parse_dt

log = logging.getLogger("diary.classes")

# Unpaid ONLINE class seat: how long the seat is held before lazy expiry frees it. Mirrors the court
# hold (diary.bookings.HOLD_MINUTES_DEFAULT) so a class checkout behaves like a court checkout.
ONLINE_HOLD_MINUTES = 30


def _err(error, status, **extra):
    d = {"ok": False, "error": error, "status": status}
    d.update(extra)
    return d


def _session_row(session, club_id, class_session_id, lock=False):
    sql = ("SELECT id, club_id, resource_id, coach_user_id, starts_at, ends_at, capacity, "
           "       price_id, status, court_resource_id, court_booking_id "
           "FROM diary.class_session WHERE club_id=:c AND id=:id")
    if lock:
        sql += " FOR UPDATE"
    row = session.execute(text(sql), {"c": club_id, "id": class_session_id}).mappings().first()
    return dict(row) if row else None


def _class_product_id(session, club_id, price_id):
    """The billing.product this class's price belongs to — so a PER-SERVICE class pack draws only for
    THIS class's own product (Cardio pack ≠ Yoga pack), never any class of the same coach. Guarded →
    None (no price / billing absent) so the match falls back to kind+coach (legacy)."""
    if not price_id:
        return None
    try:
        return session.execute(
            text("SELECT product_id FROM billing.price WHERE club_id=:c AND id=:p"),
            {"c": club_id, "p": str(price_id)},
        ).scalar()
    except Exception:
        return None


def _enrolled_count(session, class_session_id):
    return session.execute(
        text("SELECT count(*) FROM diary.enrolment "
             "WHERE class_session_id=:id AND status='enrolled'"),
        {"id": class_session_id},
    ).scalar() or 0


def _class_payment_modes(session, club_id, price_id):
    """The class SERVICE's allowed payment methods (a subset) or None (no per-service restriction).
    Scoped to THIS class's own product so a card-only class actually refuses at-court / month-end."""
    try:
        from diary.pricing import payment_modes_for
        return payment_modes_for(session, club_id=club_id, kind="class",
                                 product_id=_class_product_id(session, club_id, price_id))
    except Exception:
        return None


def enrol(session, *, club_id, class_session_id, user_id, settlement_mode="at_court",
          audience="member", payer_user_id=None, role="member"):
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

    # PAYMENT GATE — a class is a SERVICE, so enrolment must respect the same rules as a court/lesson
    # booking (create_booking), else a member could post a mode that bypasses the gate:
    #   • 'membership_covered' / 'free' → an R0 seat (a membership covers COURTS only; 'free' is admin-
    #     only). Downgrade covered → at_court, and let _settlement_allowed refuse 'free' for a member.
    #   • a money mode the club disabled OR that THIS class's service doesn't offer (a card-only class
    #     taken pay-at-court) → refuse. Staff (admin/coach) override, exactly as create_booking does.
    if settlement_mode == "membership_covered":
        settlement_mode = "at_court"   # classes are NEVER membership-covered (court-only benefit)
    if role in ("member", "guest"):
        from diary.bookings import _settlement_allowed, _policy
        if not _settlement_allowed(settlement_mode, _policy(session, club_id), role):
            return _err("SETTLEMENT_NOT_ALLOWED", 422, settlement_mode=settlement_mode)
        if settlement_mode in ("online", "at_court", "monthly_account"):
            pm = _class_payment_modes(session, club_id, cs.get("price_id"))
            if pm is not None and settlement_mode not in pm:
                return _err("SETTLEMENT_NOT_ALLOWED", 422, settlement_mode=settlement_mode,
                            message="this class doesn't offer that payment method")

    # Free any lapsed unpaid-online seats on THIS session first, so a new enrolee can take a freed
    # seat instead of queueing on the waitlist behind someone's abandoned checkout.
    release_expired_enrolments(session, club_id=club_id, class_session_id=class_session_id)

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

    # Capture the billing intent on the enrolment (payer / mode / audience) so a WAITLIST promotion
    # can settle the seat exactly as an enrol would — even for a parent-paid child.
    intent = {"payer": payer_user_id, "mode": settlement_mode, "aud": audience}
    # An ONLINE seat is HELD pending the Yoco payment; abandonment lazily frees it (see
    # release_expired_enrolments). Firm modes (at-court/monthly/token/membership) get no hold, and a
    # waitlisted seat holds nothing (it bills only on promotion). CAST guards the psycopg :param-IS-NULL.
    hold_mins = ONLINE_HOLD_MINUTES if (settlement_mode == "online" and target == "enrolled") else None
    held_expr = ("CASE WHEN CAST(:hold_mins AS int) IS NULL THEN NULL "
                 "ELSE now() + make_interval(mins => :hold_mins) END")
    if existing:  # reactivate a previously cancelled enrolment (refresh the billing intent)
        session.execute(
            text("UPDATE diary.enrolment SET status=:st, payer_user_id=:payer, "
                 "settlement_mode=:mode, audience=:aud, held_until=" + held_expr + ", "
                 "updated_at=now() WHERE id=:id"),
            dict(intent, st=target, id=existing["id"], hold_mins=hold_mins),
        )
        enrol_id = existing["id"]
    else:
        row = session.execute(
            text("INSERT INTO diary.enrolment (club_id, class_session_id, user_id, status, "
                 "payer_user_id, settlement_mode, audience, held_until) "
                 "VALUES (:c, :cs, :u, :st, :payer, :mode, :aud, " + held_expr + ") RETURNING id"),
            dict(intent, c=club_id, cs=class_session_id, u=user_id, st=target, hold_mins=hold_mins),
        ).mappings().first()
        enrol_id = row["id"]

    # Order only for a real (enrolled) seat; waitlist doesn't bill until promoted.
    order = None
    if target == "enrolled":
        # Token settlement (docs/specs/02): PRE-FLIGHT match a prepaid CLASS wallet for the PAYER
        # before billing. The token is keyed off the enrolment_id (a class has no booking_id). If
        # token settlement is asked but no wallet matches, reject cleanly (NO_TOKEN) so the seat is
        # rolled back and the UI falls back to PAYG. A class pack is COACH-SCOPED (owner rule: it
        # belongs to the coach who sold it), so we match on THIS class's coach — a pack for coach X
        # only draws for X's classes, never coach Y's (whose classes bill instead). Duration is
        # agnostic (a class = one per-session unit).
        token_wallet = None
        if settlement_mode == "token":
            from diary.bookings import _match_token_wallet_guarded
            token_wallet = _match_token_wallet_guarded(
                session, club_id=club_id, user_id=payer_user_id, booking_type="class",
                duration_minutes=None, coach_user_id=cs["coach_user_id"],
                product_id=_class_product_id(session, club_id, cs.get("price_id")))
            if token_wallet is None:
                return _err("NO_TOKEN", 422,
                            message="no matching prepaid class token — choose another way to pay")
        order = _create_order_guarded(
            session, club_id=club_id, user_id=payer_user_id, booking_id=None,
            booking_type="class", settlement_mode=settlement_mode, parties=[],
            resource_id=cs["resource_id"], starts_at=cs["starts_at"], ends_at=cs["ends_at"],
            enrolment_id=str(enrol_id), audience=audience, token_wallet=token_wallet,
            token_ref=str(enrol_id),
            price_id=cs.get("price_id"),   # charge THIS class's own rate (not the cheapest class)
        )
        if order.get("order_id"):
            session.execute(
                text("UPDATE diary.enrolment SET order_id=:o WHERE id=:id"),
                {"o": order["order_id"], "id": enrol_id},
            )

    enrolment = _enrolment_dict(session, enrol_id)
    payload = _payload(cs, enrolment)
    if target == "enrolled":
        # An ONLINE seat is HELD pending the Yoco payment — do NOT send the "you're enrolled"
        # confirmation until the charge actually succeeds (else the client gets a confirmation while
        # payment is still pending, and the seat may lazy-expire unpaid). The confirmation is emitted
        # ON PAYMENT instead (billing.events → diary.classes.confirm_paid_enrolments). Firm modes
        # (at-court / monthly / token / covered) confirm immediately, so they emit now.
        if settlement_mode != "online":
            events.emit("class_enrolled", payload)
    else:
        events.emit("class_waitlisted", payload)
    resp = {"ok": True, "enrolment": enrolment, "status_value": target}
    # Surface the order so the client can be driven through the SAME paywall as a court/lesson: an
    # 'online' enrolment creates an awaiting_payment order → the frontend redirects to Yoco. Without
    # this the enrolment silently succeeded with an UNPAID order (classes bypassed the paywall).
    if order and order.get("order_id"):
        resp["order_id"] = order["order_id"]
        resp["order_status"] = order.get("status")
        resp["checkout"] = order.get("checkout")
    return resp


def confirm_paid_enrolments(session, *, club_id, order_id):
    """Called on a class order's PAYMENT SUCCESS (billing.events → webhook AND reconcile): clear the
    online hold on the paid enrolment(s) and emit the class_enrolled confirmation NOW — the online
    enrol path deliberately DEFERRED it until payment (so no 'you're enrolled' email fires while a
    Yoco charge is still pending). Idempotent + guarded: a re-run finds no still-held enrolment and
    emits nothing. Returns the count confirmed."""
    try:
        rows = session.execute(
            text("SELECT e.id, e.class_session_id FROM diary.enrolment e "
                 "JOIN billing.order_line ol ON ol.enrolment_id = e.id "
                 "WHERE ol.order_id = :o AND e.club_id = :c AND e.status = 'enrolled' "
                 "  AND e.held_until IS NOT NULL"),
            {"o": str(order_id), "c": str(club_id)},
        ).mappings().all()
    except Exception:
        return 0
    n = 0
    for r in rows:
        session.execute(
            text("UPDATE diary.enrolment SET held_until = NULL, updated_at = now() WHERE id = :id"),
            {"id": r["id"]},
        )
        try:
            cs = _session_row(session, club_id, str(r["class_session_id"]))
            enrolment = _enrolment_dict(session, r["id"])
            if cs and enrolment:
                events.emit("class_enrolled", _payload(cs, enrolment))
        except Exception:
            pass
        n += 1
    return n


def cancel_enrolment(session, *, club_id, class_session_id, user_id, actor_user_id=None,
                     role=None):
    """Cancel an enrolment and auto-promote the earliest waitlisted player (FIFO). Never
    exceeds capacity (we only promote when a confirmed seat actually frees)."""
    cs = _session_row(session, club_id, class_session_id, lock=True)
    if not cs:
        return _err("SESSION_NOT_FOUND", 404)
    row = session.execute(
        text("SELECT id, status, order_id FROM diary.enrolment "
             "WHERE class_session_id=:cs AND user_id=:u"),
        {"cs": class_session_id, "u": user_id},
    ).mappings().first()
    if not row or row["status"] in ("cancelled",):
        return _err("ENROLMENT_NOT_FOUND", 404)

    # A member/guest may NOT cancel a seat once the class has STARTED — mirrors cancel_booking:
    # otherwise a delivered-but-owed seat could be cancelled after the fact, voiding its owed order
    # and erasing the debt. Admins/coaches may still cancel a started seat (paid orders keep the
    # separate-refund prompt). Waitlisted seats hold no debt, so this only guards a live seat.
    if (role in ("member", "guest") and row["status"] in ("enrolled", "held")
            and _parse_dt(cs["starts_at"]) <= datetime.now(timezone.utc)):
        return _err("CANNOT_CANCEL_STARTED", 409)

    was_enrolled = row["status"] == "enrolled"
    session.execute(
        text("UPDATE diary.enrolment SET status='cancelled', updated_at=now() WHERE id=:id"),
        {"id": row["id"]},
    )

    # Drop the debt for a cancelled class: void the UNPAID order so it doesn't linger as 'owed'
    # (mirrors cancel_booking; void_order no-ops on a PAID order — that stays for the refund path).
    if row.get("order_id"):
        try:
            from billing.statement import void_order
            void_order(session, club_id=club_id, order_id=row["order_id"], reason="class cancelled")
        except Exception:
            log.debug("class order void skipped", exc_info=False)

    # Token credit-back (docs/specs/02): if this enrolment was settled by a prepaid CLASS token,
    # return it to the wallet. Idempotent per (wallet, enrolment) — a re-cancel credits nothing.
    try:
        from diary.bookings import _credit_token_guarded
        _credit_token_guarded(session, club_id=club_id, booking_id=str(row["id"]),
                              reason="enrolment cancelled")
    except Exception:
        pass

    promoted = None
    if was_enrolled:
        promoted = _promote_waitlist(session, club_id=club_id, cs=cs)
    return {"ok": True, "promoted": promoted}


def _bill_promoted_enrolment(session, *, club_id, cs, enrol):
    """Bill a seat just promoted off the waitlist — the waitlist itself never billed. Async promotion
    can't drive an online checkout, so an 'online' intent becomes an OWED at-court order; a 'token'
    intent draws a still-matching prepaid class wallet (else falls back to at-court rather than
    rejecting a promotion). Guarded — a billing hiccup never blocks the promotion."""
    if enrol.get("order_id"):
        return                                   # already billed (defensive) — leave it
    mode = enrol.get("settlement_mode") or "at_court"
    if mode == "online":
        mode = "at_court"                        # collect at the club; can't check out asynchronously
    payer = enrol.get("payer_user_id") or enrol.get("user_id")
    audience = enrol.get("audience") or "member"
    enrol_id = str(enrol["id"])
    token_wallet = None
    if mode == "token":
        try:
            from diary.bookings import _match_token_wallet_guarded
            token_wallet = _match_token_wallet_guarded(
                session, club_id=club_id, user_id=payer, booking_type="class",
                duration_minutes=None, coach_user_id=cs["coach_user_id"],
                product_id=_class_product_id(session, club_id, cs.get("price_id")))  # coach+product-scoped pack
        except Exception:
            token_wallet = None
        if token_wallet is None:
            mode = "at_court"                    # intended a token but none left → owe it, don't reject
    try:
        order = _create_order_guarded(
            session, club_id=club_id, user_id=payer, booking_id=None, booking_type="class",
            settlement_mode=mode, parties=[], resource_id=cs["resource_id"],
            starts_at=cs["starts_at"], ends_at=cs["ends_at"], enrolment_id=enrol_id,
            audience=audience, token_wallet=token_wallet, token_ref=enrol_id,
            price_id=cs.get("price_id"))   # charge THIS class's own rate (not the cheapest class)
        if order.get("order_id"):
            session.execute(
                text("UPDATE diary.enrolment SET order_id=:o WHERE id=:id"),
                {"o": order["order_id"], "id": enrol_id})
    except Exception:
        log.debug("promoted-enrolment billing skipped", exc_info=False)


def _promote_waitlist(session, *, club_id, cs):
    """Promote the earliest waitlisted enrolment to enrolled IFF a seat is free, and bill the seat
    (the waitlist itself didn't). Runs under the session lock the caller holds."""
    capacity = cs["capacity"] or 0
    if capacity:
        enrolled = _enrolled_count(session, cs["id"])
        if enrolled >= capacity:
            return None
    nxt = session.execute(
        text("SELECT id, user_id, order_id, payer_user_id, settlement_mode, audience "
             "FROM diary.enrolment WHERE class_session_id=:cs AND status='waitlisted' "
             "ORDER BY waitlist_seq LIMIT 1"),
        {"cs": cs["id"]},
    ).mappings().first()
    if not nxt:
        return None
    session.execute(
        text("UPDATE diary.enrolment SET status='enrolled', updated_at=now() WHERE id=:id"),
        {"id": nxt["id"]},
    )
    _bill_promoted_enrolment(session, club_id=club_id, cs=cs, enrol=nxt)
    enrolment = _enrolment_dict(session, nxt["id"])
    events.emit("waitlist_slot_open", _payload(cs, enrolment))
    events.emit("class_enrolled", _payload(cs, enrolment))
    return str(nxt["id"])


def release_expired_enrolments(session, *, club_id, class_session_id=None):
    """Lazy expiry of UNPAID online class seats — the class analogue of release_expired_holds for
    courts. An 'online' enrolment holds its seat (status='enrolled') the moment it's created, stamped
    with held_until; if the client abandons the Yoco checkout the seat would otherwise sit unpaid
    forever. Here we cancel each enrolment whose hold lapsed AND whose order is STILL awaiting_payment
    (a seat that got paid — or was manually collected/converted — is NEVER touched), void the pending
    order, and promote the waitlist into the freed seat. Runs at the top of the class read + enrol
    paths (session_scope commits), so freed seats reappear with no cron. Returns the count released."""
    where = ["e.club_id = :c", "e.status = 'enrolled'", "e.held_until IS NOT NULL",
             "e.held_until < now()", "o.status = 'awaiting_payment'"]
    params = {"c": club_id}
    if class_session_id:
        where.append("e.class_session_id = :cs")
        params["cs"] = class_session_id
    rows = session.execute(
        text('SELECT e.id, e.class_session_id FROM diary.enrolment e '
             'JOIN billing."order" o ON o.id = e.order_id '
             'WHERE ' + " AND ".join(where)),
        params,
    ).mappings().all()
    if not rows:
        return 0
    # Group by session so we lock each session once and promote AFTER freeing all its lapsed seats.
    by_session = {}
    for r in rows:
        by_session.setdefault(r["class_session_id"], []).append(r["id"])
    released = 0
    for cs_id, enrol_ids in by_session.items():
        cs = _session_row(session, club_id, cs_id, lock=True)
        if not cs:
            continue
        for eid in enrol_ids:
            oid = session.execute(
                text("SELECT order_id FROM diary.enrolment WHERE id=:id"), {"id": eid}).scalar()
            session.execute(
                text("UPDATE diary.enrolment SET status='cancelled', held_until=NULL, "
                     "updated_at=now() WHERE id=:id"),
                {"id": eid})
            # Void the still-unpaid order (void_order no-ops if it somehow got paid between the
            # SELECT above and here — the paid seat then simply stays cancelled-but-that-won't-happen).
            if oid:
                try:
                    from billing.statement import void_order
                    void_order(session, club_id=club_id, order_id=oid,
                               reason="online class hold expired (unpaid)")
                except Exception:
                    log.debug("expired-hold order void skipped", exc_info=False)
            released += 1
        # A freed seat may let a waitlisted player in — billed as an owed at-court seat (async
        # promotion can't drive an online checkout), exactly as a normal cancellation would.
        _promote_waitlist(session, club_id=club_id, cs=cs)
    return released


def list_sessions(session, *, club_id, date_from=None, date_to=None, resource_id=None):
    """Class sessions with capacity + spots_left (docs/03 §8 GET /classes)."""
    # Lazy expiry (mirrors courts releasing holds inside compute_availability): free any lapsed
    # unpaid-online seats first so spots_left reflects real availability without a cron.
    release_expired_enrolments(session, club_id=club_id)
    where = ["cs.club_id = :c", "cs.status = 'scheduled'"]
    params = {"c": club_id}
    if date_from:
        where.append("cs.starts_at >= :df"); params["df"] = date_from
    if date_to:
        # date_to is usually a bare day (YYYY-MM-DD); a naked "<=" coerces it to 00:00 and drops every
        # intraday session (a single-day query then matches ONLY 00:00 sessions → classes never show).
        # Inclusive day: everything before the START of the day AFTER date_to.
        where.append("cs.starts_at < CAST(:dt AS date) + INTERVAL '1 day'"); params["dt"] = date_to
    if resource_id:
        where.append("cs.resource_id = :rid"); params["rid"] = resource_id
    rows = session.execute(
        text("SELECT cs.id, cs.resource_id, r.name AS class_name, cs.coach_user_id, "
             "       cs.starts_at, cs.ends_at, cs.capacity, cs.price_id, pp.id AS product_id, "
             "       pr.amount_minor AS price_minor, pp.payment_modes AS payment_modes, "
             "       cu.first_name AS coach_first, cu.surname AS coach_surname, "
             "       cp.display_name AS coach_display, "
             "       (SELECT count(*) FROM diary.enrolment e "
             "          WHERE e.class_session_id = cs.id AND e.status='enrolled') AS enrolled, "
             "       (SELECT count(*) FROM diary.enrolment e "
             "          WHERE e.class_session_id = cs.id AND e.status='waitlisted') AS waitlisted "
             "FROM diary.class_session cs "
             "LEFT JOIN diary.resource r ON r.id = cs.resource_id "
             "LEFT JOIN billing.price pr ON pr.id = cs.price_id "
             "LEFT JOIN billing.product pp ON pp.id = pr.product_id "
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
        for k in ("id", "resource_id", "coach_user_id", "price_id", "product_id"):
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
    Returns {class:{...}}. Plain SQL (we don't import billing modules — docs lane rules).

    A class MUST have a coach: every enrolment pays the coach who runs it (owner rule 2026-07), so
    without a coach the class's revenue can't attribute (no commission / arrears). Raise
    ValueError('COACH_REQUIRED') when it's missing/empty (the route/UI provide + validate it)."""
    if not name:
        return _err("NAME_REQUIRED", 400)
    if not coach_user_id or not str(coach_user_id).strip():
        raise ValueError("COACH_REQUIRED")
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
                       AS upcoming_sessions,
                   -- The courts this class's UPCOMING sessions hold (for the edit form's prefill).
                   (SELECT array_agg(DISTINCT csc.court_resource_id)
                      FROM diary.class_session cs2
                      JOIN diary.class_session_court csc ON csc.class_session_id = cs2.id
                      WHERE cs2.club_id = r.club_id AND cs2.resource_id = r.id
                        AND cs2.status = 'scheduled' AND cs2.starts_at >= now()
                        AND csc.court_resource_id IS NOT NULL) AS court_resource_ids
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
        d["court_resource_ids"] = [str(x) for x in (d.get("court_resource_ids") or [])]
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


def _reserve_court_for_class(session, *, club_id, court_resource_id, coach_user_id, name,
                             starts_at, ends_at):
    """Hold ONE physical court for a class occurrence by inserting a court-blocking diary.booking
    (booking_type='class'), so the court is reserved EXACTLY like a member court booking — same GiST
    exclusion, so it shows on the court grid and can't be double-booked. Returns the booking id, or
    None if the court is already busy at that time (savepoint-guarded so a clash never poisons the txn)."""
    try:
        with session.begin_nested():
            row = session.execute(
                text("INSERT INTO diary.booking (club_id, booking_type, resource_id, coach_user_id, "
                     "starts_at, ends_at, status, notes) "
                     "VALUES (:c, 'class', :r, :coach, :sa, :ea, 'confirmed', :notes) RETURNING id"),
                {"c": club_id, "r": court_resource_id, "coach": coach_user_id,
                 "sa": starts_at, "ea": ends_at, "notes": "Class: " + (name or "")},
            ).mappings().first()
            return str(row["id"])
    except IntegrityError:
        return None


def _free_courts(session, club_id, starts, ends, exclude=None):
    """Active courts with no held/confirmed booking and no time-off overlapping [starts, ends),
    ordered rank/name, minus `exclude` (a set of str ids). The set version of _first_free_court used
    by the class auto-repick so it can skip courts already held this occurrence AND courts another
    desired entry will claim."""
    exclude = exclude or set()
    rows = session.execute(
        text("SELECT id FROM diary.resource r "
             "WHERE r.club_id = :c AND r.kind = 'court' AND r.is_active = true "
             "  AND NOT EXISTS (SELECT 1 FROM diary.booking b "
             "      WHERE b.club_id = :c AND b.resource_id = r.id "
             "        AND b.status IN ('held','confirmed') "
             "        AND b.ends_at > :s AND b.starts_at < :e) "
             "  AND NOT EXISTS (SELECT 1 FROM diary.time_off t "
             "      WHERE t.club_id = :c AND t.resource_id = r.id "
             "        AND t.ends_at > :s AND t.starts_at < :e) "
             "ORDER BY r.rank, r.name"),
        {"c": club_id, "s": starts, "e": ends},
    ).scalars().all()
    return [str(x) for x in rows if str(x) not in exclude]


def _reserve_courts_for_class(session, *, club_id, court_resource_ids, coach_user_id, name,
                              starts_at, ends_at):
    """Reserve a LIST of desired courts for ONE class occurrence. For each desired court: try to hold
    it (GiST-guarded via _reserve_court_for_class); if it's BUSY, auto-repick a free court — the owner's
    rule. The repick PREFERS a free court that isn't another entry in the desired list (so substituting
    a busy court never cannibalises a court a later desired entry will legitimately claim); only if none
    such is free does it fall back to any free court. A desired court is DROPPED only when no court is
    free anywhere at that time.

    Returns a list of (court_resource_id, court_booking_id) actually reserved (order follows the desired
    list, substitutions in place). `used` stops the same court being held twice in one occurrence."""
    desired_set = {str(c) for c in court_resource_ids if c}
    reserved = []
    used = set()
    for desired in court_resource_ids:
        if not desired or str(desired) in used:
            continue
        court_id = str(desired)
        bid = _reserve_court_for_class(session, club_id=club_id, court_resource_id=desired,
                                       coach_user_id=coach_user_id, name=name,
                                       starts_at=starts_at, ends_at=ends_at)
        if bid is None:
            # Busy → prefer a free court OUTSIDE the desired list; else any free court not yet used.
            candidates = _free_courts(session, club_id, starts_at, ends_at, exclude=used | desired_set)
            if not candidates:
                candidates = _free_courts(session, club_id, starts_at, ends_at, exclude=used)
            sub = candidates[0] if candidates else None
            if sub is None:
                continue                              # nothing free anywhere → drop this desired court
            bid = _reserve_court_for_class(session, club_id=club_id, court_resource_id=sub,
                                           coach_user_id=coach_user_id, name=name,
                                           starts_at=starts_at, ends_at=ends_at)
            if bid is None:
                continue
            court_id = sub
        used.add(court_id)
        reserved.append((court_id, bid))
    return reserved


def _coach_busy_at(session, *, club_id, coach_user_id, starts_at, ends_at, exclude_session_id=None):
    """True if the coach can't run a class at [starts_at, ends_at): they already RUN another scheduled
    class OR have a held/confirmed LESSON overlapping. This is the schedule-time half of the coach∩class
    guard (the lesson-side lives in diary.bookings._coach_class_conflict) — it stops a class being laid
    over the coach's own lesson. A class's OWN court-blocking booking (booking_type='class') is NOT a
    lesson, so it never counts the coach busy against itself. exclude_session_id skips the session being
    edited/re-scheduled."""
    if not coach_user_id:
        return False
    class_clash = session.execute(
        text("SELECT 1 FROM diary.class_session "
             "WHERE club_id = :c AND coach_user_id = :u AND status = 'scheduled' "
             "  AND ends_at > :s AND starts_at < :e "
             "  AND (CAST(:excl AS uuid) IS NULL OR id <> CAST(:excl AS uuid)) LIMIT 1"),
        {"c": club_id, "u": coach_user_id, "s": starts_at, "e": ends_at,
         "excl": str(exclude_session_id) if exclude_session_id else None},
    ).first()
    if class_clash:
        return True
    lesson_clash = session.execute(
        text("SELECT 1 FROM diary.booking "
             "WHERE club_id = :c AND coach_user_id = :u AND booking_type = 'lesson' "
             "  AND status IN ('held','confirmed') AND ends_at > :s AND starts_at < :e LIMIT 1"),
        {"c": club_id, "u": coach_user_id, "s": starts_at, "e": ends_at},
    ).first()
    return bool(lesson_clash)


def _valid_court(session, *, club_id, court_resource_id):
    return bool(session.execute(
        text("SELECT 1 FROM diary.resource WHERE club_id=:c AND id=:r "
             "AND kind='court' AND is_active=true"),
        {"c": club_id, "r": court_resource_id},
    ).first())


def schedule_sessions(session, *, club_id, resource_id, weekdays=None, start_time=None,
                      date_from=None, date_until=None, dates=None, duration_minutes=None,
                      capacity=None, price_id=None, court_resource_id=None,
                      court_resource_ids=None):
    """Generate diary.class_session rows for a class resource. Two modes:
      recurring: {weekdays:[0-6], start_time:'HH:MM', date_from, date_until}
      one-off:   {dates:['YYYY-MM-DD'], start_time:'HH:MM'}
    Idempotent on (resource_id, starts_at) — an existing session at that start is skipped.
    coach_user_id + (default) capacity + price come from the class type. When court_resource_ids is
    given (a list; the legacy scalar court_resource_id is accepted as a 1-element list), EACH
    occurrence reserves those courts — a busy court is auto-repicked to another free court (owner's
    rule); an occurrence is skipped (court_busy) only when courts were desired but NONE is free
    anywhere at that time. Before creating an occurrence the coach must be FREE (no overlapping class
    or held/confirmed lesson) — else it's skipped (coach_busy).
    Returns {created, skipped, court_busy, coach_busy}."""
    res = _resource_for_schedule(session, club_id=club_id, resource_id=resource_id)
    if not res:
        return _err("CLASS_NOT_FOUND", 404)

    # Normalise the desired courts (list preferred; scalar kept for back-compat = 1-element list).
    courts = []
    if court_resource_ids:
        courts = [c for c in court_resource_ids if c]
    elif court_resource_id:
        courts = [court_resource_id]

    # Validate each linked court belongs to this club and is a real, active court.
    for c in courts:
        if not _valid_court(session, club_id=club_id, court_resource_id=c):
            return _err("COURT_NOT_FOUND", 404)

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
    court_busy = 0
    coach_busy = 0
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
        # The coach must be free — no overlapping class or held/confirmed lesson. Skip the occurrence
        # otherwise so a class is never laid over the coach's own lesson (neither can be double-booked).
        if _coach_busy_at(session, club_id=club_id, coach_user_id=res["coach_user_id"],
                          starts_at=starts_at, ends_at=ends_at):
            coach_busy += 1
            continue
        # Reserve the desired courts FIRST (busy → auto-repick another free court). If courts were
        # desired but NONE is free anywhere, skip the whole occurrence (court_busy) — a class that
        # needs a court can't run without one (mirrors the old single-court skip). No shadow booking
        # is written when the reserve list comes back empty.
        court_rows = []
        if courts:
            court_rows = _reserve_courts_for_class(
                session, club_id=club_id, court_resource_ids=courts,
                coach_user_id=res["coach_user_id"], name=res["name"],
                starts_at=starts_at, ends_at=ends_at)
            if not court_rows:
                court_busy += 1
                continue
        first_court = court_rows[0][0] if court_rows else None
        first_bid = court_rows[0][1] if court_rows else None
        sid = session.execute(
            text("INSERT INTO diary.class_session (club_id, resource_id, coach_user_id, "
                 "starts_at, ends_at, capacity, price_id, status, court_resource_id, court_booking_id) "
                 "VALUES (:c, :r, :coach, :sa, :ea, :cap, :pid, 'scheduled', :court, :cbid) RETURNING id"),
            {"c": club_id, "r": resource_id, "coach": res["coach_user_id"],
             "sa": starts_at, "ea": ends_at, "cap": eff_capacity, "pid": eff_price_id,
             "court": first_court, "cbid": first_bid},
        ).scalar_one()
        for court_id, bid in court_rows:
            session.execute(
                text("INSERT INTO diary.class_session_court (club_id, class_session_id, "
                     "court_resource_id, court_booking_id) VALUES (:c, :cs, :court, :bid)"),
                {"c": club_id, "cs": sid, "court": court_id, "bid": bid},
            )
        created += 1
    return {"ok": True, "created": created, "skipped": skipped,
            "court_busy": court_busy, "coach_busy": coach_busy}


def _class_product_for_resource(session, *, club_id, name, coach_user_id):
    """The billing.product(kind='class') row backing this class type. Matched by NAME, PREFERRING the
    row whose coach matches (the create_class_type pairing) but falling back to the same-named product
    regardless of coach — so the coach lockstep is resilient once the resource + product coach diverge
    (e.g. a legacy class edited to assign a coach: the product is still coachless, so a strict name+coach
    match would never re-find it and the coach would never propagate to Services). Returns its id, or None."""
    return session.execute(
        text("SELECT id FROM billing.product WHERE club_id=:c AND kind='class' AND active=true "
             "AND lower(name)=lower(:n) "
             "ORDER BY (coach_user_id IS NOT DISTINCT FROM :coach) DESC, created_at "
             "LIMIT 1"),
        {"c": club_id, "n": name, "coach": coach_user_id},
    ).scalar()


def _cancel_session_court_booking(session, *, club_id, booking_id, reason="class court reassigned"):
    if not booking_id:
        return
    session.execute(
        text("UPDATE diary.booking SET status='cancelled', cancelled_at=now(), "
             "cancellation_reason=:reason, updated_at=now() WHERE club_id=:c AND id=:id"),
        {"c": club_id, "id": str(booking_id), "reason": reason},
    )


def update_class_type(session, *, club_id, resource_id, coach_user_id, name=None,
                      capacity=None, description=None, court_resource_ids=None,
                      court_resource_id=None):
    """Edit a class type. coach_user_id is REQUIRED (a class must attribute to a coach — commission /
    arrears). Changing the coach updates, IN LOCKSTEP: diary.resource.coach_user_id, the class's
    billing.product(kind='class').coach_user_id (so commission attributes to the new coach), and every
    FUTURE class_session.coach_user_id + those sessions' shadow court bookings' coach_user_id.
    Changing courts re-reserves every FUTURE session onto the new court list (cancel old holds, reserve
    new — with the same auto-repick). Best-effort: reports (never hard-fails on) any future session at
    which the new coach is already busy. Returns {class, coach_conflicts:[iso,...]}."""
    if not coach_user_id or not str(coach_user_id).strip():
        raise ValueError("COACH_REQUIRED")
    res = _resource_for_schedule(session, club_id=club_id, resource_id=resource_id)
    if not res:
        return _err("CLASS_NOT_FOUND", 404)
    new_coach = str(coach_user_id)
    old_name = res["name"]
    new_name = name if name is not None else old_name
    new_capacity = int(capacity) if capacity is not None else int(res["capacity"] or 0)

    # Resolve the backing product BEFORE we change name/coach (the join keys off both).
    prod_id = _class_product_for_resource(session, club_id=club_id, name=old_name,
                                          coach_user_id=res["coach_user_id"])

    # 1) The class resource.
    session.execute(
        text("UPDATE diary.resource SET coach_user_id=:coach, name=:n, capacity=:cap, updated_at=now() "
             "WHERE club_id=:c AND id=:r AND kind='class'"),
        {"coach": new_coach, "n": new_name, "cap": new_capacity, "c": club_id, "r": resource_id},
    )
    # 2) The billing.product — coach LOCKSTEP (+ name/description so the join stays intact).
    if prod_id:
        session.execute(
            text("UPDATE billing.product SET coach_user_id=:coach, name=:n, "
                 "description=COALESCE(:d, description), updated_at=now() WHERE club_id=:c AND id=:p"),
            {"coach": new_coach, "n": new_name, "d": description, "c": club_id, "p": prod_id},
        )
    # 3) Future sessions' coach + their shadow court bookings' coach (link table AND legacy scalar).
    session.execute(
        text("UPDATE diary.class_session SET coach_user_id=:coach, updated_at=now() "
             "WHERE club_id=:c AND resource_id=:r AND status='scheduled' AND starts_at >= now()"),
        {"coach": new_coach, "c": club_id, "r": resource_id},
    )
    session.execute(
        text("UPDATE diary.booking SET coach_user_id=:coach, updated_at=now() "
             "WHERE club_id=:c AND booking_type='class' AND status IN ('held','confirmed') "
             "  AND id IN (SELECT csc.court_booking_id FROM diary.class_session_court csc "
             "             JOIN diary.class_session cs ON cs.id = csc.class_session_id "
             "             WHERE cs.club_id=:c AND cs.resource_id=:r AND cs.status='scheduled' "
             "               AND cs.starts_at >= now() AND csc.court_booking_id IS NOT NULL)"),
        {"coach": new_coach, "c": club_id, "r": resource_id},
    )
    session.execute(   # legacy single-court sessions (scalar only, no link rows)
        text("UPDATE diary.booking SET coach_user_id=:coach, updated_at=now() "
             "WHERE club_id=:c AND booking_type='class' AND status IN ('held','confirmed') "
             "  AND id IN (SELECT cs.court_booking_id FROM diary.class_session cs "
             "             WHERE cs.club_id=:c AND cs.resource_id=:r AND cs.status='scheduled' "
             "               AND cs.starts_at >= now() AND cs.court_booking_id IS NOT NULL)"),
        {"coach": new_coach, "c": club_id, "r": resource_id},
    )

    # 4) Court reassignment (only when the caller sent a court spec — None = leave courts untouched).
    if court_resource_ids is not None or court_resource_id is not None:
        new_courts = []
        if court_resource_ids is not None:
            new_courts = [c for c in court_resource_ids if c]
        elif court_resource_id:
            new_courts = [court_resource_id]
        for c in new_courts:
            if not _valid_court(session, club_id=club_id, court_resource_id=c):
                return _err("COURT_NOT_FOUND", 404)
        future = session.execute(
            text("SELECT id, starts_at, ends_at FROM diary.class_session "
                 "WHERE club_id=:c AND resource_id=:r AND status='scheduled' AND starts_at >= now() "
                 "ORDER BY starts_at"),
            {"c": club_id, "r": resource_id},
        ).mappings().all()
        for fs in future:
            # Free every currently-held court for this session (link rows + legacy scalar), then drop
            # the link rows, then re-reserve the new list on the now-free slot.
            old_bids = session.execute(
                text("SELECT court_booking_id FROM diary.class_session_court "
                     "WHERE class_session_id=:cs AND court_booking_id IS NOT NULL"),
                {"cs": fs["id"]},
            ).scalars().all()
            for bid in old_bids:
                _cancel_session_court_booking(session, club_id=club_id, booking_id=bid)
            scalar_old = session.execute(
                text("SELECT court_booking_id FROM diary.class_session WHERE id=:cs"),
                {"cs": fs["id"]},
            ).scalar()
            _cancel_session_court_booking(session, club_id=club_id, booking_id=scalar_old)
            session.execute(
                text("DELETE FROM diary.class_session_court WHERE class_session_id=:cs"),
                {"cs": fs["id"]},
            )
            new_rows = _reserve_courts_for_class(
                session, club_id=club_id, court_resource_ids=new_courts,
                coach_user_id=new_coach, name=new_name,
                starts_at=fs["starts_at"], ends_at=fs["ends_at"]) if new_courts else []
            for court_id, bid in new_rows:
                session.execute(
                    text("INSERT INTO diary.class_session_court (club_id, class_session_id, "
                         "court_resource_id, court_booking_id) VALUES (:c, :cs, :court, :bid)"),
                    {"c": club_id, "cs": fs["id"], "court": court_id, "bid": bid},
                )
            session.execute(
                text("UPDATE diary.class_session SET court_resource_id=:court, court_booking_id=:bid, "
                     "updated_at=now() WHERE id=:cs"),
                {"court": (new_rows[0][0] if new_rows else None),
                 "bid": (new_rows[0][1] if new_rows else None), "cs": fs["id"]},
            )

    # 5) Best-effort conflict note: any future session where the new coach is now busy (excl. itself).
    conflicts = []
    future_all = session.execute(
        text("SELECT id, starts_at, ends_at FROM diary.class_session "
             "WHERE club_id=:c AND resource_id=:r AND status='scheduled' AND starts_at >= now()"),
        {"c": club_id, "r": resource_id},
    ).mappings().all()
    for fs in future_all:
        if _coach_busy_at(session, club_id=club_id, coach_user_id=new_coach,
                          starts_at=fs["starts_at"], ends_at=fs["ends_at"],
                          exclude_session_id=fs["id"]):
            conflicts.append(fs["starts_at"].isoformat() if hasattr(fs["starts_at"], "isoformat")
                             else str(fs["starts_at"]))

    updated = class_type_dict(session, club_id=club_id, resource_id=resource_id) or {}
    updated["court_resource_ids"] = _session_default_courts(session, club_id=club_id,
                                                            resource_id=resource_id)
    return {"ok": True, "class": updated, "coach_conflicts": conflicts}


def _session_default_courts(session, *, club_id, resource_id):
    """The court ids of the NEXT upcoming session of this class (a reasonable 'current default' for the
    editor to pre-fill). Reads the link table, falling back to the scalar. [] when none."""
    nxt = session.execute(
        text("SELECT id, court_resource_id FROM diary.class_session "
             "WHERE club_id=:c AND resource_id=:r AND status='scheduled' AND starts_at >= now() "
             "ORDER BY starts_at LIMIT 1"),
        {"c": club_id, "r": resource_id},
    ).mappings().first()
    if not nxt:
        return []
    rows = session.execute(
        text("SELECT court_resource_id FROM diary.class_session_court WHERE class_session_id=:cs "
             "ORDER BY created_at"),
        {"cs": nxt["id"]},
    ).scalars().all()
    if rows:
        return [str(x) for x in rows]
    return [str(nxt["court_resource_id"])] if nxt.get("court_resource_id") else []


def enrolment_story(session, *, club_id, enrolment_id, scope, user_id=None):
    """The unified transaction record for a CLASS enrolment — the class sibling of booking_story, in the
    SAME shape (summary + charge + chronological log + action eligibility) so the one widget renders it.
    scope in client|coach|owner: client = the player OR their guardian; coach = a class they run; owner =
    any enrolment in the club. Returns None if not found / not visible to this viewer."""
    r = session.execute(
        text('SELECT e.id, e.status, e.class_session_id, e.user_id AS player_user_id, e.order_id, '
             '       e.settlement_mode, cs.starts_at, cs.ends_at, cs.coach_user_id, '
             '       res.name AS class_name, '
             "       COALESCE(cp.display_name, NULLIF(TRIM(COALESCE(cu.first_name,'')||' '||COALESCE(cu.surname,'')),'')) AS coach_name, "
             "       NULLIF(TRIM(COALESCE(pu.first_name,'')||' '||COALESCE(pu.surname,'')),'') AS player_name "
             'FROM diary.enrolment e '
             'JOIN diary.class_session cs ON cs.id = e.class_session_id '
             'LEFT JOIN diary.resource res ON res.id = cs.resource_id '
             'LEFT JOIN iam."user" cu ON cu.id = cs.coach_user_id '
             'LEFT JOIN iam.coach_profile cp ON cp.user_id = cs.coach_user_id AND cp.club_id = cs.club_id '
             'LEFT JOIN iam."user" pu ON pu.id = e.user_id '
             'WHERE e.id = :e AND e.club_id = :c'),
        {"e": str(enrolment_id), "c": str(club_id)},
    ).mappings().first()
    if not r:
        return None
    if scope == "client":
        if str(r["player_user_id"]) != str(user_id) and not is_guardian_of(session, user_id, r["player_user_id"]):
            return None
    elif scope == "coach":
        if str(r["coach_user_id"]) != str(user_id):
            return None
    # owner sees any enrolment in the club.
    from diary.bookings import _booking_charge, _event_log
    charge = _booking_charge(session, club_id, r["order_id"], r["settlement_mode"] or "at_court")
    log = _event_log(session, club_id, scope=scope,
                     user_id=(user_id if scope in ("client", "coach") else None),
                     order_id=r["order_id"], booking_id=None)
    status = r["status"]
    starts, ends = r["starts_at"], r["ends_at"]
    dur = int((ends - starts).total_seconds() // 60) if (starts and ends) else None
    is_future = bool(starts and starts > datetime.now(timezone.utc))
    is_you = (scope == "client" and str(r["player_user_id"]) == str(user_id))
    state = charge.get("state")
    can = {
        "add_to_calendar": False,   # class .ics not built yet
        "cancel": status in ("enrolled", "waitlisted"),
        "pay": scope == "client" and state in ("owed", "pending"),
        "settle": scope == "client" and state == "owed",
        "receipt": state in ("paid", "refunded", "part_refunded"),
        "request_refund": scope == "client" and bool(charge.get("refundable")),
        "refund": scope == "owner" and bool(charge.get("refundable")),
        "desk_pay": scope == "owner" and state == "owed",
        "void": scope == "owner" and state == "owed",
        "write_off": scope == "owner" and state == "owed",
    }
    return {
        "record_id": "enrolment:" + str(r["id"]),
        "id": str(r["id"]),
        "kind": "class",
        "booking_type": "class",
        "class_session_id": str(r["class_session_id"]),
        "status": status,
        "starts_at": starts.isoformat() if starts else None,
        "ends_at": ends.isoformat() if ends else None,
        "duration_minutes": dur,
        "is_future": is_future,
        "class_name": r["class_name"],
        "coach_name": r["coach_name"],
        "players": [{"name": "You" if is_you else (r["player_name"] or "Player"),
                     "kind": "you" if is_you else "player"}],
        "player_name": r["player_name"],
        "player_user_id": str(r["player_user_id"]) if r["player_user_id"] else None,
        "charge": charge,
        "log": log,
        "can": can,
    }


def is_guardian_of(session, guardian_user_id, dependent_user_id):
    """True if guardian_user_id is the registered guardian of dependent_user_id (iam.dependent) —
    lets a parent manage (e.g. cancel) a class booked for their child."""
    return bool(session.execute(
        text("SELECT 1 FROM iam.dependent WHERE guardian_user_id = :g AND dependent_user_id = :d LIMIT 1"),
        {"g": guardian_user_id, "d": dependent_user_id},
    ).first())


def list_my_enrolments(session, *, club_id, user_id):
    """A member's OWN class enrolments — classes they're a PLAYER in AND classes they booked for a
    DEPENDENT (junior classes are often a parent enrolling a child; the enrolment.user_id is the child
    but the guardian manages it). Enrolled + waitlisted only, with the session time / class name /
    coach so the client sees them in 'Your sessions' and can cancel. club-scoped."""
    # Lazy expiry FIRST so a client viewing their own sessions clears any of their OWN abandoned
    # unpaid-online seats (they'd otherwise linger looking booked until someone else opened the class).
    release_expired_enrolments(session, club_id=club_id)
    rows = session.execute(
        text("SELECT e.id AS enrolment_id, e.status, e.class_session_id, e.user_id AS player_user_id, "
             "       cs.starts_at, cs.ends_at, r.name AS class_name, cs.coach_user_id, e.settlement_mode, "
             "       o.status AS order_status, "
             "       cu.first_name AS coach_first, cu.surname AS coach_surname, cp.display_name AS coach_display, "
             "       pu.first_name AS player_first, pu.surname AS player_surname "
             "FROM diary.enrolment e "
             "JOIN diary.class_session cs ON cs.id = e.class_session_id "
             "LEFT JOIN diary.resource r ON r.id = cs.resource_id "
             "LEFT JOIN billing.\"order\" o ON o.id = e.order_id "
             "LEFT JOIN iam.user cu ON cu.id = cs.coach_user_id "
             "LEFT JOIN iam.coach_profile cp ON cp.user_id = cs.coach_user_id AND cp.club_id = cs.club_id "
             "LEFT JOIN iam.user pu ON pu.id = e.user_id "
             "WHERE e.club_id = :c AND e.status IN ('enrolled','waitlisted') "
             "  AND (e.user_id = :u "
             "       OR e.user_id IN (SELECT d.dependent_user_id FROM iam.dependent d "
             "                        WHERE d.guardian_user_id = :u)) "
             "ORDER BY cs.starts_at DESC"),
        {"c": club_id, "u": user_id},
    ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["coach_name"] = (d.pop("coach_display", None)
                           or " ".join(x for x in (d.pop("coach_first", None), d.pop("coach_surname", None)) if x).strip()
                           or None)
        d["player_name"] = " ".join(x for x in (d.pop("player_first", None), d.pop("player_surname", None)) if x).strip() or None
        for k in ("enrolment_id", "class_session_id", "coach_user_id", "player_user_id"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        for k in ("starts_at", "ends_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        # An online seat with a still-unpaid order isn't a confirmed booking — flag it so the client sees
        # "Awaiting payment", not a firm session (it lazily auto-cancels if the payment is abandoned).
        d["awaiting_payment"] = (d.pop("order_status", None) == "awaiting_payment")
        d["can_cancel"] = True
        out.append(d)
    return out


def list_type_sessions(session, *, club_id, resource_id, date_from=None, date_to=None):
    """Sessions for one class type (any status), with enrolled/waitlisted/spots_left. Used by
    the admin/coach 'manage this class' view (GET .../sessions)."""
    where = ["cs.club_id = :c", "cs.resource_id = :r"]
    params = {"c": club_id, "r": resource_id}
    if date_from:
        where.append("cs.starts_at >= CAST(:df AS timestamptz)"); params["df"] = date_from
    if date_to:
        # inclusive day (see list_sessions): a bare-date date_to must not truncate to midnight.
        where.append("cs.starts_at < CAST(:dt AS date) + INTERVAL '1 day'"); params["dt"] = date_to
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
    """Cancel a class_session: status='cancelled'; drop each enrolled/waitlisted player's debt (void
    the unpaid order + credit back a token) and emit('class_cancelled') PER PLAYER so each is emailed +
    notified (the raw session carries no recipient — a bare emit would notify nobody)."""
    cs = _session_row(session, club_id, session_id, lock=True)
    if not cs:
        return _err("SESSION_NOT_FOUND", 404)
    class_name = session.execute(
        text("SELECT r.name FROM diary.resource r WHERE r.id = :rid"),
        {"rid": cs["resource_id"]},
    ).scalar()
    session.execute(
        text("UPDATE diary.class_session SET status='cancelled', updated_at=now() "
             "WHERE club_id=:c AND id=:id"),
        {"c": club_id, "id": session_id},
    )
    # Free ALL reserved courts (cancel every blocking booking → the slots reopen on the grid). The
    # multi-court source of truth is class_session_court; also cancel the legacy scalar for old rows.
    court_bids = session.execute(
        text("SELECT court_booking_id FROM diary.class_session_court "
             "WHERE class_session_id=:cs AND court_booking_id IS NOT NULL"),
        {"cs": session_id},
    ).scalars().all()
    court_bids = list(court_bids)
    if cs.get("court_booking_id"):
        court_bids.append(cs["court_booking_id"])
    for bid in {str(b) for b in court_bids}:
        session.execute(
            text("UPDATE diary.booking SET status='cancelled', cancelled_at=now(), "
                 "cancellation_reason='class cancelled', updated_at=now() "
                 "WHERE club_id=:c AND id=:id"),
            {"c": club_id, "id": bid},
        )
    starts = cs["starts_at"].isoformat() if hasattr(cs["starts_at"], "isoformat") else cs["starts_at"]

    # Every still-active enrolment: cancel it, void its unpaid order (so it stops showing as 'owed'),
    # credit back a prepaid token, and notify the player. Waitlisted players are told too.
    players = session.execute(
        text("SELECT id, user_id, order_id, status FROM diary.enrolment "
             "WHERE class_session_id=:cs AND status IN ('enrolled','waitlisted')"),
        {"cs": session_id},
    ).mappings().all()
    for p in players:
        session.execute(
            text("UPDATE diary.enrolment SET status='cancelled', updated_at=now() WHERE id=:id"),
            {"id": p["id"]},
        )
        if p.get("order_id"):
            try:
                from billing.statement import void_order
                void_order(session, club_id=club_id, order_id=p["order_id"], reason="class cancelled")
            except Exception:
                log.debug("class order void skipped", exc_info=False)
        try:
            from diary.bookings import _credit_token_guarded
            _credit_token_guarded(session, club_id=club_id, booking_id=str(p["id"]),
                                  reason="class cancelled")
        except Exception:
            pass
        try:
            events.emit("class_cancelled", {
                "club_id": str(club_id), "class_session_id": str(session_id),
                "resource_id": str(cs["resource_id"]), "class_name": class_name,
                "user_id": str(p["user_id"]) if p.get("user_id") else None,
                "starts_at": starts,
            })
        except Exception:
            log.debug("class_cancelled emit skipped")
    return {"ok": True, "session_id": str(session_id), "status_value": "cancelled",
            "notified": len(players)}


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
    """Class sessions shaped as master-diary events (alongside bookings). Non-cancelled by default;
    includes enrolled + capacity so the admin calendar can show fill.

    A class that RESERVES courts emits ONE event PER court, each carrying `court_resource_id`, so the
    grid places the class chip UNDER each court column (the shadow court-hold rows are de-duped out of
    the master BOOKINGS feed — see diary/routes.master_diary — so the class renders exactly once per
    court, never twice). A courtless class emits a single event with court_resource_id=None (it stays
    in the Classes column). The LEFT JOIN to class_session_court fans a multi-court session into N rows;
    a session with no link rows falls back to the legacy scalar court_resource_id."""
    rows = session.execute(
        text("""
            SELECT cs.id, cs.resource_id, r.name AS resource_name, cs.coach_user_id,
                   cs.starts_at, cs.ends_at, cs.status, cs.capacity,
                   cs.court_resource_id AS scalar_court,
                   csc.court_resource_id AS link_court,
                   cu.first_name AS coach_first, cu.surname AS coach_surname, cp.display_name AS coach_display,
                   (SELECT count(*) FROM diary.enrolment e
                      WHERE e.class_session_id = cs.id AND e.status = 'enrolled') AS enrolled
            FROM diary.class_session cs
            LEFT JOIN diary.resource r ON r.id = cs.resource_id
            LEFT JOIN diary.class_session_court csc ON csc.class_session_id = cs.id
            LEFT JOIN iam.user cu ON cu.id = cs.coach_user_id
            LEFT JOIN iam.coach_profile cp ON cp.user_id = cs.coach_user_id AND cp.club_id = cs.club_id
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
        # A link row (multi-court) wins; else the legacy scalar; else courtless (Classes column).
        court = d.pop("link_court", None) or d.pop("scalar_court", None)
        d["court_resource_id"] = str(court) if court else None
        d["booking_type"] = "class"
        d["kind"] = "class"
        d["coach_name"] = (d.pop("coach_display", None)
                           or " ".join(x for x in (d.pop("coach_first", None),
                                                   d.pop("coach_surname", None)) if x).strip() or None)
        for k in ("id", "resource_id", "coach_user_id"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        for k in ("starts_at", "ends_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        out.append(d)
    return out

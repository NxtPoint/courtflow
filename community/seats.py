# community/seats.py — THE SEAT RULE. The only place the money split lives.
#
#   A court booking has SEATS. Every seat is held by a covered member (free), a payer (owes a share),
#   or is OPEN. The court's price for that duration is SPLIT EQUALLY among the seats that are not
#   covered. An OPEN seat unfilled at the cutoff COLLAPSES onto the booking holder as a charged seat.
#
# The club therefore banks exactly one court fee for every court hour unless every player on it is a
# member. Membership decides WHO pays, never WHETHER the court is paid for:
#
#   member + member       -> R0                     both covered
#   member + non-member   -> R150 on the non-member  (the whole fee — the member's seat is covered)
#   non-member x2         -> R75 + R75               both must settle before the court confirms
#   member, seat unfilled -> R150 on the member      at the cutoff (nobody to share with)
#
# WHY THIS MODULE RAISES INSTEAD OF GUARDING
# Every read in analytics/ and insights/ is _guard-wrapped so a panel degrades to empty instead of
# 500ing. That is right for a dashboard and WRONG here. GOTCHAS "a silent zero is a bug, and
# `try/except: return 0` is not a guard" was written after three separate money reads returned a
# confident zero. A seat whose share silently computes to R0 is a court given away free, and nobody
# would ever see it. So: everything on the money path raises SeatError; display helpers may not.
#
# WHAT IT REUSES (deliberately — this module invents no coverage logic and no pricing)
#   diary.entitlement.court_covered  — is THIS seat free? (access window, court-service eligibility,
#                                      duration cap, daily caps, one-concurrent-covered-court)
#   diary.pricing.price_for          — the court's price, incl. PEAK, resolved exactly as
#                                      _create_order_guarded resolves it, so shown == charged
#   billing.orders.create_order_for_booking — one debt = one order. Seats raise ORDERS; this module
#                                      never invents a second debt store.
#   diary.bookings._bill_owner       — bill the payer, not the player (a child bills the guardian)

import logging
from datetime import timezone

from sqlalchemy import text

log = logging.getLogger("community.seats")


class SeatError(Exception):
    """A money-path failure the caller must handle, never swallow. `code` is the stable error code
    the routes surface (the same vocabulary as diary.bookings._err)."""

    def __init__(self, code, message=None, **extra):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.extra = extra


# A format's seat count IS the denominator of the split, so it is explicit rather than inferred from
# how many people happen to have joined. 'practice' is the one honest single-seat case (a member
# hitting alone or against the ball machine) — there is no second seat to account for.
SEATS_BY_FORMAT = {"singles": 2, "doubles": 4, "practice": 1}

# Seat states that OCCUPY a seat (so it isn't open) and therefore count in the split denominator when
# they are not covered.
#
# 'collapsed' belongs here and it is not obvious. A collapsed seat is one nobody filled, whose share
# was re-billed to the booking holder — so it is paid for, and it is no longer open. Leaving it out
# made collapse_open_seats NON-IDEMPOTENT: seat_plan recomputed open_count from the live seats only,
# saw the same empty seat a second time, and the hourly sweep would have charged the holder again on
# every run until the game started. Found by sc_open_seat_collapses_onto_the_holder_at_cutoff before
# it ever ran against a club.
#
# 'released' is correctly absent: an invitee who never paid has vacated the seat, and it goes back to
# being open (or collapses).
_LIVE_SEAT = ("invited", "held", "confirmed", "collapsed")

# ...but a COLLAPSED seat does not HOLD THE COURT. all_prepaid_seats_settled deliberately excludes it:
# the collapse happens BECAUSE the member is keeping the court, hours before they play, so letting a
# non-payment then lazy-expire the booking out from under them would be perverse. It becomes a debt on
# their statement like any other, which is what the month-end sweep is for.
_HOLDING_SEAT = ("invited", "held", "confirmed")


# ---------------------------------------------------------------------------
# policy + primitives
# ---------------------------------------------------------------------------

def policy(session, club_id):
    """The club's community switches. A club with no policy row gets the defaults, which are OFF —
    the conservative direction: a missing configuration must never start charging members."""
    row = session.execute(
        text("SELECT community_enabled, seat_rule_enforced, open_game_cutoff_hours, "
             "       seat_pay_hours, guest_trial_days "
             "FROM club.policy WHERE club_id = :c"),
        {"c": str(club_id)},
    ).mappings().first()
    if not row:
        return {"community_enabled": False, "seat_rule_enforced": False,
                "open_game_cutoff_hours": 12, "seat_pay_hours": 24, "guest_trial_days": 7}
    return {"community_enabled": bool(row["community_enabled"]),
            "seat_rule_enforced": bool(row["seat_rule_enforced"]),
            "open_game_cutoff_hours": int(row["open_game_cutoff_hours"] or 12),
            "seat_pay_hours": int(row["seat_pay_hours"] or 24),
            "guest_trial_days": int(row["guest_trial_days"] or 7)}


def split_minor(total_minor, n):
    """Split a court fee across n un-covered seats, in integer minor units, giving the remainder to
    the FIRST seat. Returns a list of n ints that re-sums to total_minor EXACTLY.

    The exactness is the whole point. R150 across 3 seats is 5000/5000/5000, but R100 across 3 is
    3334/3333/3333 — and if those shares don't add back up to the court fee, the club either loses a
    cent on every such booking or the statement fold (Billed - Discount - Written-off = Invoiced =
    Paid + Outstanding) stops reconciling, which is the one invariant the whole money layer rests on.
    Guarded by sc_seat_split_covers_the_court_exactly."""
    total = int(total_minor or 0)
    n = int(n or 0)
    if n <= 0:
        return []
    if total < 0:
        raise SeatError("BAD_SPLIT", "a court fee cannot be negative")
    base, rem = divmod(total, n)
    return [base + rem] + [base] * (n - 1)


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------

def _booking(session, club_id, booking_id):
    row = session.execute(
        text("SELECT id, club_id, booking_type, resource_id, product_id, starts_at, ends_at, "
             "       status, settlement_mode, booked_by_user_id, visibility, play_format, seats, "
             "       open_until, split_locked_at "
             "FROM diary.booking WHERE club_id = :c AND id = :b"),
        {"c": str(club_id), "b": str(booking_id)},
    ).mappings().first()
    if not row:
        raise SeatError("BOOKING_NOT_FOUND")
    return dict(row)


def _seats(session, booking_id):
    """Every party row on the booking, in a STABLE, MEANINGFUL order. The order decides who carries
    the rounding remainder, so it must be deterministic across re-runs — otherwise re-pricing a split
    moves a cent between two people's orders for no reason either of them can see.

    THE HOST SORTS FIRST, deliberately: the organiser carries the odd cent, which is the one answer
    that needs no explanation. `created_at` alone does NOT order these — `now()` is transaction-stable
    in Postgres, so every seat inserted by one create_booking call shares a timestamp to the
    microsecond and the tie fell through to `gen_random_uuid()`. That is stable per row (a uuid does
    not change) but arbitrary between people, so the remainder landed on whoever's random id sorted
    first. Deterministic, but indefensible if anyone ever asked."""
    return [dict(r) for r in session.execute(
        text("SELECT id, user_id, party_role, guest_name, guest_email, seat_status, order_id, "
             "       share_minor, covered, invited_by_user_id, joined_at, created_at "
             "FROM diary.booking_party WHERE booking_id = :b "
             "ORDER BY (party_role = 'host') DESC, created_at, id"),
        {"b": str(booking_id)},
    ).mappings().all()]


def seat_count(booking):
    """How many seats this booking has. Explicit `seats` wins; else the format's count; else — for
    every booking made before this module existed — 1, which makes the split a no-op."""
    if booking.get("seats"):
        return int(booking["seats"])
    return SEATS_BY_FORMAT.get(booking.get("play_format") or "", 1)


def court_price_minor(session, club_id, booking):
    """The court's price for this booking's duration, resolved EXACTLY as _create_order_guarded
    resolves it — same product_id, same duration, same club-local instant (so PEAK pricing applies)
    and same resource_id (so a court's own peak window wins over the club's). Any other resolution
    here would mean the seats split a number the booking flow never quoted.

    Raises rather than returning 0: an unpriced court is the revenue leak PRICE_NOT_CONFIGURED
    already refuses at booking time, and it must not become a free game here."""
    from diary.pricing import price_for

    starts, ends = booking["starts_at"], booking["ends_at"]
    duration = int((ends - starts).total_seconds() // 60)
    at_local = None
    try:
        from diary.availability import _club_tz
        at_local = starts.astimezone(_club_tz(session, club_id))
    except Exception:
        at_local = None

    pr = None
    if booking.get("product_id"):
        pr = price_for(session, club_id=club_id, audience="member",
                       product_id=str(booking["product_id"]), duration_minutes=duration,
                       at_local=at_local, resource_id=booking.get("resource_id"))
    if not pr:
        pr = price_for(session, club_id=club_id, audience="member", kind="court_booking",
                       duration_minutes=duration, at_local=at_local,
                       resource_id=booking.get("resource_id"))
    amount = int((pr or {}).get("amount_minor") or 0)
    if not pr or amount <= 0:
        raise SeatError("PRICE_NOT_CONFIGURED",
                        "this court has no configured price for that duration")
    return amount, (pr or {}).get("price_id")


def _overlapping_covered_seat(session, *, club_id, user_id, starts_at, ends_at,
                              exclude_booking_id=None):
    """True if this user ALREADY holds a COVERED seat overlapping this time, in anyone's game.

    diary.entitlement._has_overlapping_covered asks the same question of BOOKINGS the member made
    themselves (booked_by_user_id), which is the only shape that existed before seats. A seat in
    someone else's game is not a booking of theirs, so without this a member could be the free second
    player in three simultaneous games — the membership would be covering three courts at once, which
    is exactly the leak this module exists to close, wearing a different hat."""
    params = {"c": str(club_id), "u": str(user_id), "s": starts_at, "e": ends_at}
    ex = ""
    if exclude_booking_id:
        ex = "AND b.id <> CAST(:ex AS uuid) "
        params["ex"] = str(exclude_booking_id)
    return bool(session.execute(
        text("SELECT 1 FROM diary.booking_party bp "
             "JOIN diary.booking b ON b.id = bp.booking_id "
             "WHERE bp.club_id = :c AND bp.user_id = :u AND bp.covered = true "
             "  AND bp.seat_status IN ('held','confirmed') "
             "  AND b.booking_type = 'court' AND b.status IN ('held','confirmed') "
             "  AND b.ends_at > :s AND b.starts_at < :e " + ex + "LIMIT 1"),
        params,
    ).first())


def _seat_covered(session, *, club_id, booking, seat):
    """Is THIS seat free? Delegates the entire question to the existing entitlement resolver — this
    module deliberately owns no coverage rules of its own, so a change to membership windows, court
    eligibility or the daily caps reaches seats automatically.

    A seat with no user_id (an ad-hoc guest with only a name) is never covered: coverage is a
    property of a membership, and an anonymous guest has none."""
    uid = seat.get("user_id")
    if not uid:
        return False
    from diary.entitlement import court_covered
    covered = court_covered(
        session, club_id=club_id, user_id=str(uid),
        starts_at=booking["starts_at"], ends_at=booking["ends_at"],
        resource_id=booking.get("resource_id"),
        # This booking is the one being priced — it must not count itself as the member's existing
        # concurrent covered court, or the holder's own seat would never be covered.
        exclude_booking_id=str(booking["id"]),
    )
    if not covered:
        return False
    if _overlapping_covered_seat(session, club_id=club_id, user_id=str(uid),
                                 starts_at=booking["starts_at"], ends_at=booking["ends_at"],
                                 exclude_booking_id=str(booking["id"])):
        return False
    return True


def seat_plan(session, *, club_id, booking_id):
    """Resolve the whole money picture for a booking WITHOUT writing anything.

    Returns {booking, seats_total, occupied, open_count, court_price_minor, price_id, locked,
             rows: [{seat, covered, share_minor}]}. `rows` carries one entry per LIVE seat, with
             share_minor set only on the un-covered ones.

    Pure read, so the booking flow, the sweep and the UI all price a game the same way — the
    'shown == charged' discipline the availability grid already follows."""
    booking = _booking(session, club_id, booking_id)
    if booking["booking_type"] != "court":
        raise SeatError("NOT_A_COURT_BOOKING", "the seat rule applies to court bookings only")

    rows = _seats(session, booking_id)
    live = [s for s in rows if s.get("seat_status") in _LIVE_SEAT]
    total_seats = seat_count(booking)
    open_count = max(0, total_seats - len(live))

    price, price_id = court_price_minor(session, club_id, booking)
    locked = booking.get("split_locked_at") is not None

    out_rows = []
    uncovered = []
    for s in live:
        # A locked split keeps whatever each seat was told. Recomputing coverage after somebody has
        # paid could flip a paid seat to 'covered' (refund) or a free seat to 'owing' (a bill they
        # never agreed to) purely because a membership lapsed in between.
        cov = bool(s.get("covered")) if locked else _seat_covered(
            session, club_id=club_id, booking=booking, seat=s)
        entry = {"seat": s, "covered": cov, "share_minor": None}
        out_rows.append(entry)
        if not cov:
            uncovered.append(entry)

    if locked:
        # A locked seat keeps the share it was told. A seat that joined AFTER the lock has no share
        # at all, and it must NOT fall through to zero — `int(None or 0)` handed a post-lock joiner a
        # free court, which is the silent zero this module's header refuses. It is left UNPRICED here
        # (a truthful read the UI can render) and apply_seat_orders refuses to bill it, so the
        # decision — close the game, or let the club allow a free joiner once the fee is banked —
        # is made deliberately in the join path rather than by a fallthrough in the money core.
        for e in uncovered:
            raw = e["seat"].get("share_minor")
            e["share_minor"] = int(raw) if raw is not None else None
            e["unpriced"] = raw is None
    else:
        for e, share in zip(uncovered, split_minor(price, len(uncovered))):
            e["share_minor"] = share

    return {"booking": booking, "seats_total": total_seats, "occupied": len(live),
            "open_count": open_count, "court_price_minor": price, "price_id": price_id,
            "locked": locked, "rows": out_rows}


# ---------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------

def _seat_payment_mode(session, *, club_id, booking, role="member"):
    """Which method must an un-covered seat settle by? The EXACT court service decides — the same
    per-product rule bookings, packs and class enrolments already enforce server-side. A kind-level
    resolve would read the club's default court product and quietly allow a method a card-only
    service never offered.

    Preference order is online (so the court is genuinely held until paid — what Tomo asked for),
    then the desk, then the tab. An empty intersection is REFUSED, never granted unpaid: the rule
    billing.bundles.allowed_purchase_modes already sets for an unpayable restricted pack."""
    from diary.bookings import _policy, _service_payment_modes_guarded, _settlement_allowed

    pol = _policy(session, club_id)
    modes = _service_payment_modes_guarded(session, club_id, "court", None,
                                           product_id=booking.get("product_id"))
    allowed = [m for m in ("online", "at_court", "monthly_account")
               if _settlement_allowed(m, pol, role) and (modes is None or m in modes)]
    if not allowed:
        raise SeatError("SEAT_NOT_PAYABLE",
                        "this court can't be paid for by any method the club offers")
    return allowed[0]


def apply_seat_orders(session, *, club_id, booking_id, now=None):
    """Create or refresh ONE order per un-covered seat. Idempotent: a seat that already carries a
    live order keeps it (its amount is corrected if the split moved and nothing is paid yet).

    ONE DEBT = ONE ORDER. Seats raise real billing."order" rows through the same interface the diary
    already uses, which is what makes them appear — with no extra work — on the client statement, in
    Client-360, in the month-end consolidation and in Money -> Club earnings. A parallel 'seat charge'
    store would have had to be taught to every one of those.

    Returns {orders: [...], covered: n, charged: n, total_minor: n}."""
    from billing.orders import create_order_for_booking
    from diary.bookings import _bill_owner

    plan = seat_plan(session, club_id=club_id, booking_id=booking_id)
    booking = plan["booking"]
    mode = None
    made = []
    charged = 0
    total = 0

    for entry in plan["rows"]:
        seat, cov, share = entry["seat"], entry["covered"], entry["share_minor"]
        if cov:
            # A covered seat has no debt. Record WHY (covered=true) so the money is auditable later,
            # when the member's tier may have changed and "why was this free?" needs an answer.
            session.execute(
                text("UPDATE diary.booking_party SET covered = true, share_minor = NULL, "
                     "       seat_status = CASE WHEN seat_status = 'invited' THEN 'confirmed' "
                     "                          ELSE seat_status END "
                     "WHERE id = :id"),
                {"id": str(seat["id"])})
            continue

        if entry.get("unpriced"):
            # An un-covered seat added after the split locked. Refusing is the only safe answer the
            # money core can give: billing it a guessed share would charge someone an amount nobody
            # quoted them, and billing it zero would hand out a free court.
            raise SeatError("SPLIT_LOCKED",
                            "this game's split is already locked by a payment — a new paying seat "
                            "can't be priced into it", seat_id=str(seat["id"]))

        charged += 1
        total += int(share or 0)
        if seat.get("order_id"):
            # An un-covered seat that ALREADY carries an order — normally the holder's, which rides
            # the booking's own order. Record `covered = false` here too: the resync path used to
            # leave it NULL, so the holder's seat was the one row in the game that could not say why
            # it had been charged. `covered` is the audit answer to "why was this seat free (or not)?"
            # long after the member's tier has changed, and a NULL is not an answer.
            session.execute(
                text("UPDATE diary.booking_party SET covered = false WHERE id = :id"),
                {"id": str(seat["id"])})
            if not plan["locked"]:
                _resync_seat_order(session, seat_id=seat["id"], order_id=seat["order_id"],
                                   share_minor=share)
            continue

        if mode is None:
            mode = _seat_payment_mode(session, club_id=club_id, booking=booking)
        payer = _bill_owner(session, seat["user_id"]) if seat.get("user_id") else None
        order_id = create_order_for_booking(
            session, club_id=club_id, user_id=payer,
            lines=[{"description": "court seat", "price_id": plan["price_id"], "qty": 1,
                    "amount_minor": int(share or 0), "booking_id": str(booking_id)}],
            settlement_mode=mode)
        session.execute(
            text("UPDATE diary.booking_party "
                 "SET order_id = CAST(:o AS uuid), share_minor = :s, covered = false, "
                 "    seat_status = CASE WHEN seat_status = 'confirmed' AND :prepaid THEN 'held' "
                 "                       ELSE seat_status END "
                 "WHERE id = :id"),
            {"o": str(order_id), "s": int(share or 0), "id": str(seat["id"]),
             "prepaid": (mode == "online")})
        made.append({"seat_id": str(seat["id"]), "order_id": str(order_id),
                     "amount_minor": int(share or 0), "settlement_mode": mode})

    return {"orders": made, "covered": plan["occupied"] - charged, "charged": charged,
            "total_minor": total}


# The order-line descriptions this module owns. Re-pricing must touch ONLY these.
_COURT_LINES = ("court", "court seat", "unfilled court seat")


def _resync_seat_order(session, *, seat_id, order_id, share_minor):
    """Re-price an EXISTING seat order when the split moved (someone joined or left before anyone
    paid). Refuses to touch an order that has taken money — that is what split_locked_at exists to
    prevent, and this is the belt to its braces.

    ONLY the court line is re-priced, and the order total is then RECOMPUTED from its lines rather
    than assumed. The holder's seat rides the booking's own order, and on a court with equipment hire
    that order also carries the equipment lines — blanket-updating every line to the seat share would
    have silently rewritten a R250 ball-machine line to R75 and lost the hire fee. Recomputing the
    total from the lines is also what keeps the order honest if a line is ever added later."""
    st = session.execute(
        text('SELECT status FROM billing."order" WHERE id = :o'), {"o": str(order_id)},
    ).scalar()
    if st in ("paid", "refunded", "void"):
        return False
    session.execute(
        text("UPDATE billing.order_line SET amount_minor = :a "
             "WHERE order_id = :o AND description = ANY(:d)"),
        {"a": int(share_minor or 0), "o": str(order_id), "d": list(_COURT_LINES)})
    session.execute(
        text('UPDATE billing."order" SET amount_minor = COALESCE((SELECT SUM(amount_minor * qty) '
             "  FROM billing.order_line WHERE order_id = :o), 0), updated_at = now() WHERE id = :o"),
        {"o": str(order_id)})
    session.execute(
        text("UPDATE diary.booking_party SET share_minor = :a WHERE id = :id"),
        {"a": int(share_minor or 0), "id": str(seat_id)})
    return True


def lock_split(session, *, club_id, booking_id, now=None):
    """Freeze the split. Called by the FIRST successful seat payment.

    After this, shares never move again: a later joiner can only be a COVERED member (who owes
    nothing), so nobody who has paid can be re-billed and nobody rides free off someone else's
    payment. Idempotent — a replayed webhook must not move the timestamp."""
    return bool(session.execute(
        text("UPDATE diary.booking SET split_locked_at = COALESCE(split_locked_at, now()), "
             "       updated_at = now() "
             "WHERE club_id = :c AND id = :b AND split_locked_at IS NULL"),
        {"c": str(club_id), "b": str(booking_id)},
    ).rowcount)


def all_prepaid_seats_settled(session, *, club_id, booking_id):
    """True when every seat that must be PREPAID has been paid — the gate the booking's confirmation
    hangs on.

    "Two PAYG players, and the court confirms only when both settle." A seat settling at the desk or
    on the monthly tab is a real debt on the statement and does NOT hold the court; only an `online`
    seat does, which is exactly how a single online booking already behaves. This just widens that
    from one order to N.

    Deliberately NOT guarded: on any doubt the caller must NOT confirm. A court held one payment too
    long is a support message; a court confirmed for someone who never paid is the leak."""
    unpaid = session.execute(
        text('SELECT count(*) FROM diary.booking_party bp '
             '  JOIN billing."order" o ON o.id = bp.order_id '
             " WHERE bp.booking_id = :b AND bp.club_id = :c "
             "   AND bp.seat_status = ANY(:holding) "
             "   AND o.settlement_mode = 'online' AND o.status <> 'paid'"),
        {"b": str(booking_id), "c": str(club_id), "holding": list(_HOLDING_SEAT)},
    ).scalar()
    return int(unpaid or 0) == 0


def seat_a_new_booking(session, *, club_id, booking_id, holder_user_id, holder_order_id=None,
                       extra_user_ids=(), play_format=None, seats=None, visibility="private",
                       open_until=None, now=None):
    """THE ONE ENTRY POINT the diary calls. Turn a freshly-created court booking into a seated game
    and bill its seats. Returns None when the club has not enabled the rule — the caller then behaves
    exactly as it always has.

    Called at the END of create_booking, after the booking and the holder's order already exist, so
    everything before it is untouched: the GiST insert, the payment-mode gate, equipment, pack draws
    and the price guard all ran first. That ordering is deliberate — the seat rule decides who pays
    what, not whether the booking is legal.

    The holder's seat is linked to the booking's OWN order (`holder_order_id`), so apply_seat_orders
    re-prices that existing order down to their share instead of raising a second debt beside it. The
    alternative — a booking order for the full fee PLUS seat orders — would double-bill the club's
    own member, which is the failure mode this whole module exists to avoid, pointing the other way.

    Returns {applied: True, holds_court: bool, …} so the caller can decide the booking's status."""
    pol = policy(session, club_id)
    if not pol["seat_rule_enforced"]:
        return None

    booking = _booking(session, club_id, booking_id)
    if booking["booking_type"] != "court":
        return None

    fmt = play_format or booking.get("play_format") or "singles"
    if fmt not in SEATS_BY_FORMAT:
        fmt = "singles"
    # A crafted `seats` can only change how many people SHARE the fee, never what the fee is — but an
    # absurd one would still divide the court into unpayable slivers, so it is clamped to the format's
    # own count as a floor and a sane ceiling above it.
    total_seats = int(seats or booking.get("seats") or SEATS_BY_FORMAT.get(fmt, 1))
    total_seats = max(SEATS_BY_FORMAT.get(fmt, 1), min(total_seats, 8))

    # The fill deadline is the CLUB's, never the caller's: it decides when an unfilled seat becomes
    # the holder's to pay for, so a deadline that outlives the game is a court held for free.
    #
    # CLAMPED, not defaulted. The first cut only computed this when the caller passed nothing, which
    # left a supplied value to sail straight through — and the route passing None was the only reason
    # that wasn't reachable over HTTP. create_booking is a public function; the money core has to hold
    # this on its own. A caller may bring the deadline FORWARD (a shorter window is their business);
    # it can never be pushed past the club's cutoff.
    if visibility == "open":
        from datetime import timedelta
        cutoff_h = int(pol.get("open_game_cutoff_hours") or 12)
        club_cutoff = booking["starts_at"] - timedelta(hours=cutoff_h)
        open_until = club_cutoff if open_until is None else min(open_until, club_cutoff)
    else:
        open_until = None
    session.execute(
        text("UPDATE diary.booking SET play_format = :f, seats = :n, visibility = :v, "
             "       open_until = :ou, updated_at = now() WHERE club_id = :c AND id = :b"),
        {"f": fmt, "n": total_seats, "v": visibility, "ou": open_until,
         "c": str(club_id), "b": str(booking_id)})

    # The holder's own seat. Linked to the booking's existing order so it is RE-PRICED, never
    # duplicated. A 'practice' booking is a single seat and simply keeps the whole fee.
    existing = {str(s["user_id"]) for s in _seats(session, booking_id) if s.get("user_id")}
    if str(holder_user_id) not in existing:
        session.execute(
            text("INSERT INTO diary.booking_party (booking_id, club_id, user_id, party_role, "
                 "       seat_status, order_id, joined_at) "
                 "VALUES (:b, :c, :u, 'host', 'confirmed', CAST(:o AS uuid), now())"),
            {"b": str(booking_id), "c": str(club_id), "u": str(holder_user_id),
             "o": str(holder_order_id) if holder_order_id else None})
        existing.add(str(holder_user_id))

    for uid in (extra_user_ids or ()):
        if not uid or str(uid) in existing:
            continue
        existing.add(str(uid))
        session.execute(
            text("INSERT INTO diary.booking_party (booking_id, club_id, user_id, party_role, "
                 "       seat_status, joined_at) "
                 "VALUES (:b, :c, :u, 'player', 'confirmed', now())"),
            {"b": str(booking_id), "c": str(club_id), "u": str(uid)})

    applied = apply_seat_orders(session, club_id=club_id, booking_id=booking_id, now=now)
    holds = not all_prepaid_seats_settled(session, club_id=club_id, booking_id=booking_id)
    return {"applied": True, "holds_court": holds, "seats": total_seats,
            "pay_hours": pol["seat_pay_hours"], **applied}


def collapse_open_seats(session, *, club_id, booking_id, now=None):
    """At the cutoff, an OPEN seat becomes the holder's to pay for.

    This is the rule that closes the leak without adding friction anywhere else. A member may book a
    court and go looking for a partner — they simply cannot end up having held a second seat, free,
    that nobody ever filled. They are TOLD (game_seat_collapsed), so a charge never just appears.

    Idempotent: a seat already collapsed is skipped, so the hourly sweep can re-run all day.
    Returns {collapsed: n, order_id, amount_minor}."""
    from billing.orders import create_order_for_booking

    plan = seat_plan(session, club_id=club_id, booking_id=booking_id)
    booking = plan["booking"]
    if booking["status"] not in ("held", "confirmed"):
        return {"collapsed": 0, "order_id": None, "amount_minor": 0}
    if plan["open_count"] <= 0:
        return {"collapsed": 0, "order_id": None, "amount_minor": 0}

    holder = booking.get("booked_by_user_id")
    if not holder:
        raise SeatError("NO_HOLDER", "a booking with open seats must have a holder to bill")

    # The collapsed seats are un-covered by definition (nobody is in them), so they re-split the
    # court fee with whatever else is un-covered. Recomputing through seat_plan keeps ONE split
    # formula rather than a second one that only the sweep uses.
    n_uncovered_now = sum(1 for r in plan["rows"] if not r["covered"])
    shares = split_minor(plan["court_price_minor"], n_uncovered_now + plan["open_count"])
    amount = sum(shares[n_uncovered_now:])
    if amount <= 0:
        return {"collapsed": 0, "order_id": None, "amount_minor": 0}

    mode = _seat_payment_mode(session, club_id=club_id, booking=booking)
    order_id = create_order_for_booking(
        session, club_id=club_id, user_id=str(holder),
        lines=[{"description": "unfilled court seat", "price_id": plan["price_id"],
                "qty": 1, "amount_minor": int(amount), "booking_id": str(booking_id)}],
        settlement_mode=mode)
    session.execute(
        text("INSERT INTO diary.booking_party "
             "(booking_id, club_id, user_id, party_role, seat_status, order_id, share_minor, covered) "
             "VALUES (:b, :c, :u, 'player', 'collapsed', CAST(:o AS uuid), :s, false)"),
        {"b": str(booking_id), "c": str(club_id), "u": str(holder),
         "o": str(order_id), "s": int(amount)})
    session.execute(
        text("UPDATE diary.booking SET open_until = NULL, visibility = 'private', "
             "       updated_at = now() WHERE club_id = :c AND id = :b"),
        {"c": str(club_id), "b": str(booking_id)})
    return {"collapsed": plan["open_count"], "order_id": str(order_id),
            "amount_minor": int(amount)}

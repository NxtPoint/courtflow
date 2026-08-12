# community/games.py — Find a Game: open a game, join one, leave one.
#
# A GAME IS A BOOKING. There is no game object here — every function below operates on a
# diary.booking with visibility='open' and its diary.booking_party seats. That is what lets a game
# inherit the GiST no-double-book constraint, the diary grid, reschedule/cancel, the unified
# statement, Client-360 and month-end without a line of new code in any of them.
#
# The money is never decided here. Every path that changes who is on a court ends by calling
# community.seats.apply_seat_orders, so the split has exactly one implementation.

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from community import seats as _seats

log = logging.getLogger("community.games")


class GameError(Exception):
    def __init__(self, code, message=None):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _require_enabled(session, club_id):
    pol = _seats.policy(session, club_id)
    if not pol["community_enabled"]:
        raise GameError("COMMUNITY_DISABLED", "Find a Game isn't switched on for this club")
    return pol


def default_open_until(session, club_id, starts_at):
    """When an open seat stops being fillable and collapses onto the holder. Never in the past, and
    never after the game starts."""
    pol = _seats.policy(session, club_id)
    cutoff = starts_at - timedelta(hours=int(pol["open_game_cutoff_hours"] or 12))
    return max(min(cutoff, starts_at), datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def list_open_games(session, *, club_id, user_id=None, days=14, level_band=None, play_format=None,
                    play_intent=None, near_my_level=None, now=None):
    """The Find-a-Game feed: upcoming games with a seat still open.

    THREE FILTERS, and the two that matter are LEVEL and INTENT. People stop using a feature like this
    when they are repeatedly matched far above or below their standard — or when they turn up for a
    friendly hit against someone grinding out a practice match. Intent ruins a session as reliably as
    level does, which is why it is a first-class field on the game rather than a note in the chat.

    `near_my_level` (a +/- band, e.g. 1.5) resolves the caller's OWN level and shows games around it.
    It is the sane default for browsing, and it degrades to "show everything" for a member who has not
    set a level yet — never to an empty feed, which would read as "no games" rather than "tell us your
    level".

    PRIVACY: returns first names and levels only. No email, no phone — which is precisely why match
    chat exists. A game the caller is already in is flagged rather than hidden, so "my games" and
    "games I could join" come off ONE read."""
    if near_my_level and not level_band and user_id:
        mine = session.execute(
            text("SELECT level_num FROM iam.player_profile WHERE club_id = :c AND user_id = :u"),
            {"c": str(club_id), "u": str(user_id)},
        ).scalar()
        if mine is not None:
            band = float(near_my_level)
            level_band = (float(mine) - band, float(mine) + band)
    now = now or datetime.now(timezone.utc)
    rows = session.execute(
        text("""
            SELECT b.id, b.starts_at, b.ends_at, b.play_format, b.play_intent, b.seats, b.open_until,
                   b.split_locked_at, b.booked_by_user_id,
                   r.name AS court_name,
                   h.first_name AS host_name,
                   pp.level_num AS host_level,
                   (SELECT count(*) FROM diary.booking_party bp
                     WHERE bp.booking_id = b.id
                       AND bp.seat_status IN ('invited','held','confirmed','collapsed')) AS taken,
                   EXISTS (SELECT 1 FROM diary.booking_party mp
                            WHERE mp.booking_id = b.id AND mp.user_id = CAST(:me AS uuid)
                              AND mp.seat_status IN ('invited','held','confirmed')) AS im_in
              FROM diary.booking b
              LEFT JOIN diary.resource r ON r.id = b.resource_id
              LEFT JOIN iam."user" h ON h.id = b.booked_by_user_id
              LEFT JOIN iam.player_profile pp
                     ON pp.user_id = b.booked_by_user_id AND pp.club_id = b.club_id
             WHERE b.club_id = :c
               AND b.visibility = 'open'
               AND b.booking_type = 'court'
               AND b.status IN ('held','confirmed')
               AND b.starts_at > :now
               AND b.starts_at < :until
               AND (CAST(:fmt AS text) IS NULL OR b.play_format = :fmt)
               AND (CAST(:intent AS text) IS NULL OR b.play_intent = :intent)
             ORDER BY b.starts_at
             LIMIT 100
        """),
        {"c": str(club_id), "me": str(user_id) if user_id else None, "now": now,
         "until": now + timedelta(days=int(days or 14)), "fmt": play_format,
         "intent": play_intent},
    ).mappings().all()

    out = []
    for r in rows:
        seats_total = int(r["seats"] or _seats.SEATS_BY_FORMAT.get(r["play_format"] or "", 1))
        open_seats = max(0, seats_total - int(r["taken"] or 0))
        if open_seats <= 0 and not r["im_in"]:
            continue
        if level_band and r["host_level"] is not None:
            lo, hi = level_band
            if not (float(lo) <= float(r["host_level"]) <= float(hi)):
                continue
        out.append({
            "booking_id": str(r["id"]),
            "starts_at": r["starts_at"].isoformat() if r["starts_at"] else None,
            "ends_at": r["ends_at"].isoformat() if r["ends_at"] else None,
            "court_name": r["court_name"],
            "play_format": r["play_format"],
            "play_intent": r["play_intent"],
            "seats_total": seats_total,
            "open_seats": open_seats,
            "host_name": r["host_name"],
            "host_level": float(r["host_level"]) if r["host_level"] is not None else None,
            "im_in": bool(r["im_in"]),
            "closed_to_payers": r["split_locked_at"] is not None,
            "open_until": r["open_until"].isoformat() if r["open_until"] else None,
        })
    return out


def game_detail(session, *, club_id, booking_id, viewer_user_id=None):
    """One game, for the shared Widgets.Game render. Money is shown per seat — but ONLY the viewer's
    own amount and the bare fact that another seat is paid or not. What someone else owes is between
    them and the club."""
    plan = _seats.seat_plan(session, club_id=club_id, booking_id=booking_id)
    booking = plan["booking"]
    seats = []
    for row in plan["rows"]:
        s = row["seat"]
        mine = viewer_user_id and str(s.get("user_id") or "") == str(viewer_user_id)
        name = session.execute(
            text('SELECT first_name FROM iam."user" WHERE id = :u'), {"u": str(s["user_id"])},
        ).scalar() if s.get("user_id") else (s.get("guest_name") or "Open seat")
        paid = None
        if s.get("order_id"):
            paid = session.execute(
                text('SELECT status = \'paid\' FROM billing."order" WHERE id = :o'),
                {"o": str(s["order_id"])}).scalar()
        seats.append({
            "party_id": str(s["id"]),
            "user_id": str(s["user_id"]) if s.get("user_id") else None,
            "name": name,
            "role": s.get("party_role"),
            "seat_status": s.get("seat_status"),
            "covered": row["covered"],
            "paid": paid,
            "is_me": bool(mine),
            # Only ever tell the viewer THEIR OWN number — and only ever hand them THEIR OWN order
            # id. The id is what /api/billing/yoco/checkout takes, and although that route re-checks
            # ownership server-side, handing one player a handle on another's debt is not something
            # this read should do in the first place.
            "amount_minor": (row["share_minor"] if mine else None),
            "order_id": (str(s["order_id"]) if (mine and s.get("order_id")) else None),
        })
    # A result can only be recorded once the game is OVER — the button must not exist mid-match.
    # `ends_at` is the club's own clock, so the gate is computed here rather than trusted from a
    # browser whose time zone is whatever the member's phone says.
    over = bool(booking["ends_at"]) and session.execute(
        text("SELECT :e < now()"), {"e": booking["ends_at"]}).scalar()
    im_in = any(x["is_me"] for x in seats)

    result = session.execute(
        text("SELECT outcome, winner_user_id, score_text, reported_by_user_id, "
             "       confirmed_by_user_id, confirmed_at "
             "  FROM community.match_result WHERE club_id = :c AND booking_id = :b"),
        {"c": str(club_id), "b": str(booking_id)},
    ).mappings().first()
    res = None
    if result:
        res = {
            "outcome": result["outcome"],
            "winner_user_id": str(result["winner_user_id"]) if result["winner_user_id"] else None,
            "score_text": result["score_text"],
            "reported_by_user_id": str(result["reported_by_user_id"]),
            "reported_by_me": bool(viewer_user_id
                                   and str(result["reported_by_user_id"]) == str(viewer_user_id)),
            "confirmed": bool(result["confirmed_at"]),
        }

    # The would-play-again row. PRIVATE: this returns the viewer's OWN answers and nobody else's —
    # never a count, never who rated whom. Surfacing it is precisely what would stop people
    # answering honestly and turn it into the reputation system results.py refuses to build.
    rate = []
    if over and im_in and viewer_user_id:
        mine = {str(r["subject_user_id"]): r["again"] for r in session.execute(
            text("SELECT subject_user_id, again FROM community.play_again "
                 " WHERE booking_id = :b AND rater_user_id = CAST(:u AS uuid)"),
            {"b": str(booking_id), "u": str(viewer_user_id)},
        ).mappings().all()}
        for x in seats:
            if x["user_id"] and not x["is_me"] and x["seat_status"] in ("held", "confirmed", "collapsed"):
                rate.append({"user_id": x["user_id"], "name": x["name"],
                             "again": mine.get(x["user_id"])})

    return {
        "booking_id": str(booking["id"]),
        "starts_at": booking["starts_at"].isoformat() if booking["starts_at"] else None,
        "ends_at": booking["ends_at"].isoformat() if booking["ends_at"] else None,
        "status": booking["status"],
        "play_format": booking.get("play_format"),
        "play_intent": booking.get("play_intent"),
        "visibility": booking.get("visibility"),
        "seats_total": plan["seats_total"],
        "open_seats": plan["open_count"],
        "court_price_minor": plan["court_price_minor"],
        "locked": plan["locked"],
        "seats": seats,
        "result": res,
        "rate": rate,
        "can": {
            "join": plan["open_count"] > 0 and not any(x["is_me"] for x in seats),
            "leave": any(x["is_me"] and x["role"] != "host" for x in seats),
            "invite": plan["open_count"] > 0,
            "record_result": bool(over and im_in),
            # Mirrors confirm_result's own guard: someone else has to agree, so the reporter never
            # sees a Confirm button on their own claim.
            "confirm_result": bool(over and im_in and res and not res["reported_by_me"]
                                   and not res["confirmed"]),
        },
    }


# ---------------------------------------------------------------------------
# join / leave
# ---------------------------------------------------------------------------

def join_game(session, *, club_id, booking_id, user_id, now=None):
    """Take an open seat. The money follows immediately: apply_seat_orders decides whether this seat
    is covered by the joiner's own membership or owes a share, and raises their debt if it does.

    A late joiner is simply priced — no refusal, and nothing about anyone else's seat moves. That is
    the pay-off of a share being a FIXED FRACTION of the court rather than a division of it: the
    game's quoted share is frozen on the booking, so whoever arrives last pays exactly what the people
    already in it paid, however many of them turned out to be members."""
    now = now or datetime.now(timezone.utc)
    _require_enabled(session, club_id)
    plan = _seats.seat_plan(session, club_id=club_id, booking_id=booking_id)
    booking = plan["booking"]
    if booking["status"] not in ("held", "confirmed"):
        raise GameError("GAME_NOT_OPEN", "that game is no longer available")
    if booking.get("visibility") != "open":
        raise GameError("GAME_NOT_OPEN", "that game isn't open to the community")
    if booking["starts_at"] <= now:
        raise GameError("GAME_STARTED", "that game has already started")
    if plan["open_count"] <= 0:
        raise GameError("GAME_FULL", "that game is full")
    already = any(str(r["seat"].get("user_id") or "") == str(user_id) for r in plan["rows"])
    if already:
        raise GameError("ALREADY_IN_GAME", "you're already in that game")

    # ONE PERSON, ONE PLACE — the rule the diary has always held, applied to seats.
    #
    # A seat is a diary.booking_party row, not a resource booking, so the GiST exclusion constraint
    # has nothing to say about it: without this, a player could hold seats in two games at the same
    # hour, and a COACH could take a social game at 13:00 while contracted to teach a lesson at 13:00.
    # That is exactly the hole _coach_commitment_at was written to close for bookings
    # (sc_one_coach_one_place_at_a_time); joining bypassed it entirely.
    #
    # Both are REFUSALS rather than downgrades. Elsewhere the platform prefers "don't block, just
    # don't cover" — a member's second concurrent court is PAYG rather than refused — but that is about
    # what something COSTS. This is about being in two places at once, which no price makes possible.
    from diary.bookings import _coach_commitment_at, _is_coach
    if _is_coach(session, club_id, user_id):
        busy = _coach_commitment_at(session, club_id, user_id,
                                    booking["starts_at"], booking["ends_at"])
        if busy:
            raise GameError("COACH_IS_WORKING",
                            "you're coaching at that time — the club's diary has you booked")
    clash = session.execute(
        text("SELECT 1 FROM diary.booking_party bp "
             "  JOIN diary.booking b ON b.id = bp.booking_id "
             " WHERE bp.club_id = :c AND bp.user_id = :u AND b.id <> :b "
             "   AND bp.seat_status IN ('invited','held','confirmed') "
             "   AND b.status IN ('held','confirmed') "
             "   AND b.ends_at > :s AND b.starts_at < :e LIMIT 1"),
        {"c": str(club_id), "u": str(user_id), "b": str(booking_id),
         "s": booking["starts_at"], "e": booking["ends_at"]},
    ).first()
    if clash:
        raise GameError("ALREADY_PLAYING_THEN",
                        "you're already in another game at that time")

    party_id = session.execute(
        text("INSERT INTO diary.booking_party (booking_id, club_id, user_id, party_role, "
             "       seat_status, joined_at) "
             "VALUES (:b, :c, :u, 'player', 'held', now()) RETURNING id"),
        {"b": str(booking_id), "c": str(club_id), "u": str(user_id)},
    ).scalar()

    try:
        applied = _seats.apply_seat_orders(session, club_id=club_id, booking_id=booking_id, now=now)
    except _seats.SeatError as e:
        raise GameError(e.code, e.message) from e

    _system_message(session, club_id=club_id, booking_id=booking_id, user_id=user_id,
                    body="joined the game")
    _emit(session, "game_seat_taken", club_id=club_id, booking_id=booking_id, user_id=user_id)
    if plan["open_count"] - 1 <= 0:
        _emit(session, "game_full", club_id=club_id, booking_id=booking_id,
              user_id=booking.get("booked_by_user_id"))
    return {"ok": True, "party_id": str(party_id), **applied}


def leave_game(session, *, club_id, booking_id, user_id, now=None):
    """Give a seat back. The HOST cannot leave — that is a cancel, and cancel_booking already knows
    how to refund and void every seat. A seat whose money has already moved cannot be walked away
    from either: that is a refund decision, not a self-service one."""
    now = now or datetime.now(timezone.utc)
    seat = session.execute(
        text("SELECT id, party_role, order_id FROM diary.booking_party "
             " WHERE booking_id = :b AND user_id = :u "
             "   AND seat_status IN ('invited','held','confirmed') LIMIT 1"),
        {"b": str(booking_id), "u": str(user_id)},
    ).mappings().first()
    if not seat:
        raise GameError("NOT_IN_GAME", "you're not in that game")
    if seat["party_role"] == "host":
        raise GameError("HOST_CANNOT_LEAVE",
                        "you booked this court — cancel the booking instead")
    if seat.get("order_id"):
        st = session.execute(text('SELECT status FROM billing."order" WHERE id = :o'),
                             {"o": str(seat["order_id"])}).scalar()
        if st == "paid":
            raise GameError("SEAT_ALREADY_PAID",
                            "you've already paid for this seat — ask the club for a refund")
        try:
            from billing.statement import void_order
            void_order(session, club_id=club_id, order_id=str(seat["order_id"]),
                       reason="left the game")
        except Exception:
            log.debug("seat order void skipped", exc_info=False)

    session.execute(
        text("UPDATE diary.booking_party SET seat_status = 'released', order_id = NULL, "
             "       share_minor = NULL WHERE id = :id"),
        {"id": str(seat["id"])})
    # The remaining players' shares GO UP — the court fee is unchanged and there are fewer of them to
    # carry it. Re-pricing is safe only while nobody has paid; apply_seat_orders enforces that.
    try:
        _seats.apply_seat_orders(session, club_id=club_id, booking_id=booking_id, now=now)
    except _seats.SeatError:
        log.info("re-split after leave skipped (split locked) booking=%s", booking_id)
    _system_message(session, club_id=club_id, booking_id=booking_id, user_id=user_id,
                    body="left the game")
    _emit(session, "game_seat_released", club_id=club_id, booking_id=booking_id, user_id=user_id)
    return {"ok": True}


def set_visibility(session, *, club_id, booking_id, user_id, open_it, now=None):
    """Publish an existing booking to the community, or take it private again. Only the holder may."""
    now = now or datetime.now(timezone.utc)
    b = session.execute(
        text("SELECT booked_by_user_id, starts_at, status FROM diary.booking "
             " WHERE club_id = :c AND id = :b"),
        {"c": str(club_id), "b": str(booking_id)},
    ).mappings().first()
    if not b:
        raise GameError("NOT_FOUND")
    if str(b["booked_by_user_id"]) != str(user_id):
        raise GameError("NOT_YOURS", "only whoever booked the court can open it up")
    if b["status"] not in ("held", "confirmed"):
        raise GameError("GAME_NOT_OPEN")
    session.execute(
        text("UPDATE diary.booking SET visibility = :v, open_until = :ou, updated_at = now() "
             " WHERE club_id = :c AND id = :b"),
        {"v": "open" if open_it else "private",
         "ou": default_open_until(session, club_id, b["starts_at"]) if open_it else None,
         "c": str(club_id), "b": str(booking_id)})
    if open_it:
        _emit(session, "game_opened", club_id=club_id, booking_id=booking_id, user_id=user_id)
    return {"ok": True, "visibility": "open" if open_it else "private"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _system_message(session, *, club_id, booking_id, user_id, body):
    """A timeline line in the match chat. Guarded: chat is a nicety, the money is not."""
    try:
        name = session.execute(text('SELECT first_name FROM iam."user" WHERE id = :u'),
                               {"u": str(user_id)}).scalar() or "A player"
        session.execute(
            text("INSERT INTO community.message (club_id, booking_id, user_id, body, system) "
                 "VALUES (:c, :b, NULL, :body, true)"),
            {"c": str(club_id), "b": str(booking_id), "body": f"{name} {body}"})
    except Exception:
        log.debug("system message skipped", exc_info=False)


def _emit(session, event, *, club_id, booking_id, user_id=None):
    try:
        from marketing_crm.tracking import emit
        em = session.execute(text('SELECT email FROM iam."user" WHERE id = :u'),
                             {"u": str(user_id)}).scalar() if user_id else None
        b = session.execute(
            text("SELECT b.starts_at, r.name AS court_name FROM diary.booking b "
                 "  LEFT JOIN diary.resource r ON r.id = b.resource_id WHERE b.id = :b"),
            {"b": str(booking_id)}).mappings().first()
        emit(event, {"club_id": str(club_id), "email": em, "user_id": str(user_id) if user_id else None,
                     "ref_type": "booking", "ref_id": str(booking_id),
                     "starts_at": b["starts_at"].isoformat() if b and b["starts_at"] else None,
                     "resource_name": (b or {}).get("court_name")})
    except Exception:
        log.debug("%s emit skipped (benign)", event, exc_info=False)

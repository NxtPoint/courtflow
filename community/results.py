# community/results.py — what happened, and would you do it again.
#
# Two separate things, deliberately kept apart:
#
#   community.match_result  — the PUBLIC record: played / cancelled / no-show, and a score. Reported
#                             by one player and CONFIRMED by another, because an unconfirmed result is
#                             a claim, not evidence, and must never feed a rating.
#   community.play_again    — the PRIVATE signal: "would you play them again?" NEVER rendered, never
#                             aggregated into a public score, never shown to its subject. It exists
#                             only to weight matching, which is exactly why people answer honestly. If
#                             it is ever surfaced, the answers stop being useful and it becomes a
#                             reputation system nobody asked for.
#
# NO-SHOWS matter more than scores. Someone who accepts games and doesn't turn up poisons the feature
# for everyone they stand up, so that outcome is recorded first-class and is what a reliability
# reading should later be built on.

import logging

from sqlalchemy import text

log = logging.getLogger("community.results")

OUTCOMES = ("played", "cancelled", "no_show")


class ResultError(Exception):
    def __init__(self, code, message=None):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _players(session, booking_id):
    return [str(r[0]) for r in session.execute(
        text("SELECT DISTINCT user_id FROM diary.booking_party "
             " WHERE booking_id = :b AND user_id IS NOT NULL "
             "   AND seat_status IN ('held','confirmed','collapsed')"),
        {"b": str(booking_id)},
    ).all()]


def record_result(session, *, club_id, booking_id, user_id, outcome, winner_user_id=None,
                  score_text=None):
    """Record what happened. One result per booking — a second report UPDATES the first rather than
    stacking, so there is never a pile of contradictory claims to reconcile."""
    if outcome not in OUTCOMES:
        raise ResultError("BAD_OUTCOME", "that isn't a result we record")
    players = _players(session, booking_id)
    if str(user_id) not in players:
        raise ResultError("NOT_IN_GAME", "only someone who played can record the result")
    if winner_user_id and str(winner_user_id) not in players:
        raise ResultError("BAD_WINNER", "the winner must have been in the game")

    existing = session.execute(
        text("SELECT id, reported_by_user_id FROM community.match_result WHERE booking_id = :b"),
        {"b": str(booking_id)},
    ).mappings().first()
    if existing:
        session.execute(
            text("UPDATE community.match_result SET outcome = :o, winner_user_id = :w, "
                 "       score_text = :s, reported_by_user_id = :u, "
                 # A re-report is a NEW claim, so any previous confirmation is withdrawn — otherwise
                 # one player could confirm a scoreline and the other quietly rewrite it afterwards.
                 "       confirmed_by_user_id = NULL, confirmed_at = NULL "
                 " WHERE id = :id"),
            {"o": outcome, "w": str(winner_user_id) if winner_user_id else None,
             "s": score_text, "u": str(user_id), "id": str(existing["id"])})
        rid = existing["id"]
    else:
        rid = session.execute(
            text("INSERT INTO community.match_result "
                 "(club_id, booking_id, reported_by_user_id, outcome, winner_user_id, score_text) "
                 "VALUES (:c, :b, :u, :o, :w, :s) RETURNING id"),
            {"c": str(club_id), "b": str(booking_id), "u": str(user_id), "o": outcome,
             "w": str(winner_user_id) if winner_user_id else None, "s": score_text},
        ).scalar()

    _emit(session, club_id=club_id, booking_id=booking_id, user_id=user_id, outcome=outcome)
    return {"ok": True, "result_id": str(rid), "confirmed": False}


def confirm_result(session, *, club_id, booking_id, user_id):
    """The other player agrees. Confirmation must come from SOMEONE ELSE — a result you confirm
    yourself is just the same claim twice."""
    row = session.execute(
        text("SELECT id, reported_by_user_id, confirmed_at FROM community.match_result "
             " WHERE club_id = :c AND booking_id = :b"),
        {"c": str(club_id), "b": str(booking_id)},
    ).mappings().first()
    if not row:
        raise ResultError("NO_RESULT", "there's no result to confirm yet")
    if str(row["reported_by_user_id"]) == str(user_id):
        raise ResultError("CANNOT_SELF_CONFIRM", "someone else in the game has to confirm it")
    if str(user_id) not in _players(session, booking_id):
        raise ResultError("NOT_IN_GAME", "only someone who played can confirm the result")
    session.execute(
        text("UPDATE community.match_result SET confirmed_by_user_id = :u, confirmed_at = now() "
             " WHERE id = :id"),
        {"u": str(user_id), "id": str(row["id"])})
    return {"ok": True, "confirmed": True}


def play_again(session, *, club_id, booking_id, rater_user_id, subject_user_id, again):
    """The private signal. Idempotent per (booking, rater, subject) — changing your mind updates it
    rather than stacking two contradictory rows."""
    players = _players(session, booking_id)
    if str(rater_user_id) not in players or str(subject_user_id) not in players:
        raise ResultError("NOT_IN_GAME", "you can only rate someone you actually played")
    if str(rater_user_id) == str(subject_user_id):
        raise ResultError("CANNOT_RATE_SELF")
    session.execute(
        text("INSERT INTO community.play_again "
             "(club_id, booking_id, rater_user_id, subject_user_id, again) "
             "VALUES (:c, :b, :r, :s, :a) "
             "ON CONFLICT (booking_id, rater_user_id, subject_user_id) "
             "DO UPDATE SET again = EXCLUDED.again"),
        {"c": str(club_id), "b": str(booking_id), "r": str(rater_user_id),
         "s": str(subject_user_id), "a": bool(again)})
    return {"ok": True}


def reliability(session, *, club_id, user_id):
    """Attendance, shown POSITIVELY or not at all.

    Playtomic makes no-shows explicit; we record them, but a public "62% reliable" badge is a scarlet
    letter that drives the person off the platform rather than improving their behaviour. So the read
    returns the number and the caller shows it only when it is good — the same judgement the
    would-play-again signal makes by staying private entirely."""
    row = session.execute(
        text("SELECT count(*) FILTER (WHERE bp.attended IS TRUE)  AS played, "
             "       count(*) FILTER (WHERE bp.attended IS FALSE) AS missed "
             "FROM diary.booking_party bp JOIN diary.booking b ON b.id = bp.booking_id "
             " WHERE bp.club_id = :c AND bp.user_id = :u AND bp.attended IS NOT NULL "
             "   AND b.starts_at < now()"),
        {"c": str(club_id), "u": str(user_id)},
    ).mappings().first() or {}
    played, missed = int(row.get("played") or 0), int(row.get("missed") or 0)
    total = played + missed
    return {"played": played, "missed": missed, "total": total,
            "pct": int(round(100.0 * played / total)) if total else None}


def _emit(session, *, club_id, booking_id, user_id, outcome):
    try:
        from marketing_crm.tracking import emit
        em = session.execute(text('SELECT email FROM iam."user" WHERE id = :u'),
                             {"u": str(user_id)}).scalar()
        emit("game_result_recorded", {"club_id": str(club_id), "email": em,
                                      "user_id": str(user_id), "ref_type": "booking",
                                      "ref_id": str(booking_id), "outcome": outcome})
    except Exception:
        log.debug("game_result_recorded emit skipped (benign)", exc_info=False)

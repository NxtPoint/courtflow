# community/routes.py — the /api/community/* surface (blueprint community_bp) + the open-game cron.
#
# Thin routes, same discipline as me/ and diary/: resolve the principal, gate it, take club_id and
# user_id FROM THE PRINCIPAL and never from the body, call the lane module, map to JSON. All the
# rules live behind these — this file decides WHO may ask, not what the answer is.
#
# Endpoints:
#   GET   /api/community/games                  -> the Find-a-Game feed (open seats, next 14 days)
#   GET   /api/community/games/<booking_id>     -> one game for the shared Widgets.Game render
#   POST  /api/community/games/<id>/join        -> take an open seat (bills it)
#   POST  /api/community/games/<id>/leave       -> give a seat back (voids an unpaid share)
#   POST  /api/community/games/<id>/visibility  -> publish an existing booking, or take it private
#   POST  /api/community/games/<id>/invite      -> invite by email into a seat (+ the free week)
#   GET   /api/community/games/<id>/chat        -> match chat (players only)
#   POST  /api/community/games/<id>/chat        -> post to it
#   POST  /api/community/games/<id>/result      -> record what happened
#   POST  /api/community/games/<id>/result/confirm  -> the other player agrees
#   POST  /api/community/games/<id>/play-again  -> the PRIVATE would-play-again signal
#   GET   /api/community/players                -> suggested opponents (deterministic score)
#   GET   /api/community/profile                -> my player profile (level + preferences)
#   PATCH /api/community/profile                -> edit it (incl. the visibility opt-in)
#   GET   /api/community/onboarding             -> the 5 level questions
#   POST  /api/community/onboarding             -> answers -> a starting NextPoint Level
#   GET   /api/community/favourites             -> "My Tennis Circle"
#   POST  /api/community/favourites             -> add/remove
#   GET   /api/community/invites/<token>        -> PUBLIC: what this invite is (no PII)
#   POST  /api/community/invites/accept         -> redeem it (signed-in) -> seat + free week
#   POST  /api/cron/open-games                  -> OPS_KEY-guarded sweep

import logging
import os

from flask import Blueprint, jsonify, request

from auth import resolve_principal
from db import session_scope

log = logging.getLogger("community.routes")

community_bp = Blueprint("community", __name__, url_prefix="/api/community")
community_cron_bp = Blueprint("community_cron", __name__)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _principal():
    p = resolve_principal(request)
    if p is None or not p.authenticated or not p.club_id or not p.user_id:
        return None
    return p


def _body():
    return request.get_json(silent=True) or {}


def _err(e, status=422):
    """Lane errors all carry a stable `.code` + `.message`, so the client can branch on the code and
    show the sentence. Same shape diary/routes.py returns."""
    return jsonify(error=getattr(e, "code", "ERROR"),
                   message=getattr(e, "message", str(e))), status


def _staff(p):
    return p.role in ("coach", "club_admin", "platform_admin")


# ---------------------------------------------------------------------------
# discovery + games
# ---------------------------------------------------------------------------

@community_bp.get("/games")
def list_games():
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    from community import games
    band = None
    if request.args.get("level_min") and request.args.get("level_max"):
        band = (request.args["level_min"], request.args["level_max"])
    with session_scope() as s:
        out = games.list_open_games(s, club_id=p.club_id, user_id=p.user_id,
                                    days=int(request.args.get("days") or 14),
                                    level_band=band,
                                    play_format=request.args.get("format") or None)
    return jsonify(games=out, count=len(out)), 200


@community_bp.get("/games/<booking_id>")
def get_game(booking_id):
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    from community import games, seats
    with session_scope() as s:
        try:
            out = games.game_detail(s, club_id=p.club_id, booking_id=booking_id,
                                    viewer_user_id=p.user_id)
        except seats.SeatError as e:
            return _err(e, 404 if e.code == "BOOKING_NOT_FOUND" else 422)
    return jsonify(out), 200


@community_bp.post("/games/<booking_id>/join")
def join(booking_id):
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    from community import games
    with session_scope() as s:
        try:
            out = games.join_game(s, club_id=p.club_id, booking_id=booking_id, user_id=p.user_id)
        except games.GameError as e:
            return _err(e, 409 if e.code in ("GAME_FULL", "ALREADY_IN_GAME") else 422)
    return jsonify(out), 200


@community_bp.post("/games/<booking_id>/leave")
def leave(booking_id):
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    from community import games
    with session_scope() as s:
        try:
            out = games.leave_game(s, club_id=p.club_id, booking_id=booking_id, user_id=p.user_id)
        except games.GameError as e:
            return _err(e)
    return jsonify(out), 200


@community_bp.post("/games/<booking_id>/visibility")
def visibility(booking_id):
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    from community import games
    with session_scope() as s:
        try:
            out = games.set_visibility(s, club_id=p.club_id, booking_id=booking_id,
                                       user_id=p.user_id, open_it=bool(_body().get("open")))
        except games.GameError as e:
            return _err(e, 403 if e.code == "NOT_YOURS" else 422)
    return jsonify(out), 200


@community_bp.post("/games/<booking_id>/invite")
def invite(booking_id):
    """Invite someone into a seat. Only a player already in the game may — otherwise anyone could
    fill a stranger's court with people they have never met."""
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    from community import invites, chat
    with session_scope() as s:
        if not (_staff(p) or chat.is_in_game(s, booking_id=booking_id, user_id=p.user_id)):
            return jsonify(error="forbidden"), 403
        try:
            out = invites.invite_player(s, club_id=p.club_id, inviter_user_id=p.user_id,
                                        email=_body().get("email"), booking_id=booking_id,
                                        party_id=_body().get("party_id"))
        except invites.InviteError as e:
            return _err(e)
    return jsonify(out), 200


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------

@community_bp.get("/games/<booking_id>/chat")
def get_chat(booking_id):
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    from community import chat
    with session_scope() as s:
        try:
            out = chat.list_messages(s, club_id=p.club_id, booking_id=booking_id,
                                     user_id=p.user_id, staff=_staff(p))
        except chat.ChatError as e:
            return _err(e, 403)
    return jsonify(messages=out, count=len(out)), 200


@community_bp.post("/games/<booking_id>/chat")
def post_chat(booking_id):
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    from community import chat
    with session_scope() as s:
        try:
            out = chat.post_message(s, club_id=p.club_id, booking_id=booking_id,
                                    user_id=p.user_id, body=_body().get("body"))
        except chat.ChatError as e:
            return _err(e, 403 if e.code == "NOT_IN_GAME" else 422)
    return jsonify(out), 200


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------

@community_bp.post("/games/<booking_id>/result")
def result(booking_id):
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    from community import results
    b = _body()
    with session_scope() as s:
        try:
            out = results.record_result(s, club_id=p.club_id, booking_id=booking_id,
                                        user_id=p.user_id, outcome=b.get("outcome"),
                                        winner_user_id=b.get("winner_user_id"),
                                        score_text=b.get("score_text"))
        except results.ResultError as e:
            return _err(e)
    return jsonify(out), 200


@community_bp.post("/games/<booking_id>/result/confirm")
def confirm(booking_id):
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    from community import results
    with session_scope() as s:
        try:
            out = results.confirm_result(s, club_id=p.club_id, booking_id=booking_id,
                                         user_id=p.user_id)
        except results.ResultError as e:
            return _err(e)
    return jsonify(out), 200


@community_bp.post("/games/<booking_id>/play-again")
def rate(booking_id):
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    from community import results
    b = _body()
    with session_scope() as s:
        try:
            out = results.play_again(s, club_id=p.club_id, booking_id=booking_id,
                                     rater_user_id=p.user_id,
                                     subject_user_id=b.get("user_id"),
                                     again=bool(b.get("again")))
        except results.ResultError as e:
            return _err(e)
    return jsonify(out), 200


# ---------------------------------------------------------------------------
# players, profile, favourites
# ---------------------------------------------------------------------------

@community_bp.get("/players")
def players():
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    from community import matching
    with session_scope() as s:
        out = matching.suggest_players(s, club_id=p.club_id, user_id=p.user_id,
                                       limit=int(request.args.get("limit") or 12))
    return jsonify(players=out, count=len(out)), 200


_PROFILE_FIELDS = ("level_num", "prefers_format", "prefers_play", "prefers_times", "photo_url",
                   "visible_in_community")


@community_bp.get("/profile")
def get_profile():
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    from community import repositories as repo
    with session_scope() as s:
        return jsonify(repo.player_profile(s, club_id=p.club_id, user_id=p.user_id)), 200


@community_bp.patch("/profile")
def patch_profile():
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    from community import repositories as repo
    b = {k: v for k, v in _body().items() if k in _PROFILE_FIELDS}
    # A member may set their own level, but the SOURCE is recorded as self-declared so a coach can see
    # it was never assessed — "how would you rate yourself?" is how everybody becomes advanced.
    with session_scope() as s:
        out = repo.upsert_player_profile(s, club_id=p.club_id, user_id=p.user_id, fields=b,
                                         source="self")
    return jsonify(out), 200


@community_bp.get("/onboarding")
def onboarding():
    from community import matching
    return jsonify(questions=matching.ONBOARDING_QUESTIONS), 200


@community_bp.post("/onboarding")
def onboarding_submit():
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    from community import matching, repositories as repo
    level = matching.level_from_answers(_body().get("answers") or {})
    if level is None:
        return jsonify(error="NO_ANSWERS", message="answer the questions first"), 422
    with session_scope() as s:
        out = repo.upsert_player_profile(s, club_id=p.club_id, user_id=p.user_id,
                                         fields={"level_num": level}, source="onboarding")
    return jsonify(out), 200


@community_bp.get("/favourites")
def favourites():
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    from community import results
    with session_scope() as s:
        return jsonify(players=results.list_favourites(s, club_id=p.club_id, user_id=p.user_id)), 200


@community_bp.post("/favourites")
def set_favourite():
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    from community import results
    b = _body()
    with session_scope() as s:
        try:
            out = results.add_favourite(s, club_id=p.club_id, user_id=p.user_id,
                                        favourite_user_id=b.get("user_id"),
                                        on=bool(b.get("on", True)))
        except results.ResultError as e:
            return _err(e)
    return jsonify(out), 200


# ---------------------------------------------------------------------------
# invites — the ONE public surface (the token IS the authorization)
# ---------------------------------------------------------------------------

@community_bp.get("/invites/<token>")
def peek_invite(token):
    """PUBLIC by design, like /api/feedback and /api/subscribe: the signed token is the
    authorization. Returns the club, the inviter's FIRST NAME and the game's time — never the
    invitee's email, never the other players' details."""
    from community import invites
    with session_scope() as s:
        out = invites.peek(s, token)
    if not out:
        return jsonify(error="INVITE_NOT_FOUND"), 404
    return jsonify(out), 200


@community_bp.post("/invites/accept")
def accept_invite():
    """Redeem an invite. Requires a signed-in user — that is the deliberate product choice: the friend
    signs up, which is what makes them a real CRM record, makes the free week grantable, and makes the
    seat billable to a person rather than a name."""
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    from community import invites
    with session_scope() as s:
        try:
            out = invites.accept_invite(s, token=_body().get("token"), user_id=p.user_id,
                                        club_id=p.club_id)
        except invites.InviteError as e:
            return _err(e, 409 if e.code in ("INVITE_USED", "SEAT_UNAVAILABLE") else 422)
    return jsonify(out), 200


# ---------------------------------------------------------------------------
# the sweep (OPS_KEY-guarded, fired by .github/workflows/open-games.yml)
# ---------------------------------------------------------------------------

@community_cron_bp.post("/api/cron/open-games")
def cron_open_games():
    key = (os.getenv("OPS_KEY") or "").strip()
    if not key or request.headers.get("X-Ops-Key", "") != key:
        return jsonify(error="forbidden"), 403
    from community.crons import sweep_all_clubs
    with session_scope() as s:
        out = sweep_all_clubs(s, max_seconds=int(request.args.get("max_seconds") or 90))
    log.info("open-games sweep: %s", out)
    return jsonify(out), 200

# community/chat.py — match chat.
#
# It exists for one reason: so two players can agree the details without swapping phone numbers. That
# is what lets every community read stay free of contact details, which is the privacy promise the
# whole feature rests on (and the same principle USTA applies to its Flex matches).
#
# Deliberately NOT a messaging product: no attachments, no edits, no threads, no read receipts, no
# typing indicators. Anything richer competes with WhatsApp, which we will lose, and the thing
# WhatsApp cannot do is put the conversation next to the court booking and the money.
#
# Only PLAYERS IN THE GAME may read or post. That check is the whole authorisation model.

import logging

from sqlalchemy import text

log = logging.getLogger("community.chat")

MAX_BODY = 2000


class ChatError(Exception):
    def __init__(self, code, message=None):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def is_in_game(session, *, booking_id, user_id):
    """A player in the game, or the person who booked it. Staff are handled by the route's own
    permission check — this answers the member question only."""
    if not user_id:
        return False
    return bool(session.execute(
        text("SELECT 1 FROM diary.booking b "
             " WHERE b.id = :b AND (b.booked_by_user_id = :u "
             "    OR EXISTS (SELECT 1 FROM diary.booking_party bp "
             "                WHERE bp.booking_id = b.id AND bp.user_id = :u "
             "                  AND bp.seat_status IN ('invited','held','confirmed','collapsed'))) "
             "LIMIT 1"),
        {"b": str(booking_id), "u": str(user_id)},
    ).first())


def post_message(session, *, club_id, booking_id, user_id, body):
    body = (body or "").strip()
    if not body:
        raise ChatError("EMPTY_MESSAGE", "say something first")
    if len(body) > MAX_BODY:
        raise ChatError("MESSAGE_TOO_LONG", "that message is too long")
    if not is_in_game(session, booking_id=booking_id, user_id=user_id):
        raise ChatError("NOT_IN_GAME", "only the players in a game can post in its chat")
    mid = session.execute(
        text("INSERT INTO community.message (club_id, booking_id, user_id, body, system) "
             "VALUES (:c, :b, :u, :body, false) RETURNING id"),
        {"c": str(club_id), "b": str(booking_id), "u": str(user_id), "body": body},
    ).scalar()
    return {"ok": True, "message_id": str(mid)}


def list_messages(session, *, club_id, booking_id, user_id, limit=100, staff=False):
    if not staff and not is_in_game(session, booking_id=booking_id, user_id=user_id):
        raise ChatError("NOT_IN_GAME", "only the players in a game can read its chat")
    rows = session.execute(
        text('SELECT m.id, m.body, m.system, m.created_at, m.user_id, u.first_name '
             "FROM community.message m "
             ' LEFT JOIN iam."user" u ON u.id = m.user_id '
             " WHERE m.club_id = :c AND m.booking_id = :b "
             " ORDER BY m.created_at DESC, m.id DESC LIMIT :n"),
        {"c": str(club_id), "b": str(booking_id), "n": int(limit or 100)},
    ).mappings().all()
    out = [{"id": str(r["id"]), "body": r["body"], "system": bool(r["system"]),
            "at": r["created_at"].isoformat() if r["created_at"] else None,
            "name": r["first_name"] or ("NextPoint" if r["system"] else "A player"),
            "is_me": bool(user_id and r["user_id"] and str(r["user_id"]) == str(user_id))}
           for r in rows]
    out.reverse()          # oldest first for rendering
    return out

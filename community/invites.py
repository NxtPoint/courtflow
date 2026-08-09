# community/invites.py — "bring a friend", and the free week that makes it fair.
#
# THE RULE TOMO ASKED FOR: a member may bring anyone, but a friend plays free for SEVEN DAYS and pays
# after that. This module implements the first half; the second half needs NO code at all, which is
# the point of doing it this way.
#
# The free week IS the existing 7-day trial membership (billing.membership.grant_signup_trial,
# provider='trial', court-only, auto-lapsing with no cron). So while it is live the friend's seats
# resolve covered=True through the ORDINARY entitlement path — community/seats.py has no idea a trial
# is involved — and the moment it lapses they are PAYG and the seat rule bills them like anyone else.
# There is no second kind of free play to police, no expiry sweep to write, and no way for the two
# mechanisms to disagree.
#
# It is also already ANTI-ABUSE: grant_signup_trial refuses if the member has EVER held any
# subscription, so a friend cannot be re-invited into a second free week, and the ~880 imported Wix
# members (who all have subscription history) can never be trialed by an invite.
#
# The link is a marketing_crm.signing token: HMAC-SHA256, context-tagged 'play_invite' so it cannot be
# replayed at /feedback or /subscribe, and carrying NO PII — the invitee's email is resolved
# server-side from the row this module wrote.

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

log = logging.getLogger("community.invites")

CONTEXT = "play_invite"
DEFAULT_TTL_DAYS = 14


class InviteError(Exception):
    def __init__(self, code, message=None):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _norm_email(email):
    e = (email or "").strip().lower()
    if "@" not in e or "." not in e.split("@")[-1]:
        raise InviteError("BAD_EMAIL", "that doesn't look like an email address")
    return e


def invite_player(session, *, club_id, inviter_user_id, email, booking_id=None, party_id=None,
                  ttl_days=DEFAULT_TTL_DAYS, now=None):
    """Invite someone to play — optionally into a specific SEAT on a specific game.

    Re-inviting the same person RE-SENDS rather than minting a second invite (the partial unique
    index uq_player_invite_live enforces one live invite per club+email). That matters because the
    invite is what carries the free week: two live invites would be two chances to look like a new
    member."""
    now = now or datetime.now(timezone.utc)
    e = _norm_email(email)

    # Already a member of this club? Then this is a plain seat invitation, not a recruitment — say so,
    # so the caller doesn't promise them a free week they will never be granted.
    existing_user = session.execute(
        text('SELECT u.id FROM iam."user" u JOIN iam.membership m ON m.user_id = u.id '
             " WHERE m.club_id = :c AND lower(u.email) = :e LIMIT 1"),
        {"c": str(club_id), "e": e},
    ).scalar()

    row = session.execute(
        text("SELECT id FROM community.player_invite "
             " WHERE club_id = :c AND lower(email) = :e AND status = 'sent'"),
        {"c": str(club_id), "e": e},
    ).mappings().first()

    from marketing_crm import signing
    if row:
        invite_id = row["id"]
        session.execute(
            text("UPDATE community.player_invite SET booking_id = COALESCE(:b, booking_id), "
                 "       party_id = COALESCE(:p, party_id), expires_at = :x, updated_at = now() "
                 " WHERE id = :id"),
            {"b": str(booking_id) if booking_id else None,
             "p": str(party_id) if party_id else None,
             "x": now + timedelta(days=ttl_days), "id": str(invite_id)})
        token = session.execute(
            text("SELECT token FROM community.player_invite WHERE id = :id"), {"id": str(invite_id)},
        ).scalar()
        resent = True
    else:
        # The token is minted against the INVITER (a real iam.user) because the invitee may not exist
        # yet — the signer's contract is "linkage, never PII", and the invite row is what ties the
        # token to the email.
        token = signing.mint(inviter_user_id, club_id, context=CONTEXT, ttl_days=ttl_days) \
            + "." + secrets.token_urlsafe(8)
        invite_id = session.execute(
            text("INSERT INTO community.player_invite "
                 "(club_id, inviter_user_id, email, token, status, booking_id, party_id, expires_at) "
                 "VALUES (:c, :i, :e, :t, 'sent', :b, :p, :x) RETURNING id"),
            {"c": str(club_id), "i": str(inviter_user_id), "e": e, "t": token,
             "b": str(booking_id) if booking_id else None,
             "p": str(party_id) if party_id else None,
             "x": now + timedelta(days=ttl_days)},
        ).scalar()
        resent = False

    url = f"{signing.base_url()}/join.html?t={token}"
    _emit_invite(session, club_id=club_id, email=e, inviter_user_id=inviter_user_id,
                 booking_id=booking_id, url=url, is_member=bool(existing_user))
    return {"ok": True, "invite_id": str(invite_id), "token": token, "url": url,
            "resent": resent, "already_a_member": bool(existing_user)}


def _emit_invite(session, *, club_id, email, inviter_user_id, booking_id, url, is_member):
    """Tell them. Guarded — a CRM hiccup must never lose the invite that was already written, the same
    contract the trial grant and account_created emits already follow."""
    try:
        from marketing_crm.tracking import emit
        inviter = session.execute(
            text('SELECT first_name FROM iam."user" WHERE id = :u'), {"u": str(inviter_user_id)},
        ).scalar()
        emit("player_invited", {"club_id": str(club_id), "email": email,
                                "inviter_name": inviter or "A member",
                                "ref_type": "booking" if booking_id else None,
                                "ref_id": str(booking_id) if booking_id else None,
                                "join_url": url, "already_a_member": is_member})
    except Exception:
        log.debug("player_invited emit skipped (benign)", exc_info=False)


def peek(session, token):
    """What a /join.html page may show BEFORE anyone signs in. Deliberately thin: the club and the
    game, never the invitee's email or the other players' contact details."""
    row = session.execute(
        text("SELECT i.id, i.club_id, i.booking_id, i.status, i.expires_at, "
             "       u.first_name AS inviter_name "
             "FROM community.player_invite i "
             '  LEFT JOIN iam."user" u ON u.id = i.inviter_user_id '
             " WHERE i.token = :t"),
        {"t": token},
    ).mappings().first()
    if not row:
        return None
    out = {"status": row["status"], "inviter_name": row["inviter_name"],
           "club_id": str(row["club_id"]), "booking_id": str(row["booking_id"]) if row["booking_id"] else None}
    if row["expires_at"] and row["expires_at"] < datetime.now(timezone.utc):
        out["status"] = "expired"
    if row["booking_id"]:
        g = session.execute(
            text("SELECT b.starts_at, b.ends_at, r.name AS court_name "
                 "FROM diary.booking b LEFT JOIN diary.resource r ON r.id = b.resource_id "
                 " WHERE b.id = :b"),
            {"b": str(row["booking_id"])},
        ).mappings().first()
        if g:
            out["game"] = {"starts_at": g["starts_at"].isoformat() if g["starts_at"] else None,
                           "ends_at": g["ends_at"].isoformat() if g["ends_at"] else None,
                           "court_name": g["court_name"]}
    return out


def accept_invite(session, *, token, user_id, club_id=None, grant_trial=True, now=None):
    """Redeem an invite for a signed-in user: take the seat that was held for them, and grant the free
    week if they are genuinely new.

    THE TRIAL IS GRANTED BY THE EXISTING ENGINE, unchanged. grant_signup_trial refuses when the member
    has EVER held a subscription, so a second invite cannot buy a second free week and an imported Wix
    member is never trialed — the guard scripts/audit_trials.py already audits."""
    now = now or datetime.now(timezone.utc)
    row = session.execute(
        text("SELECT * FROM community.player_invite WHERE token = :t FOR UPDATE"), {"t": token},
    ).mappings().first()
    if not row:
        raise InviteError("INVITE_NOT_FOUND", "that invitation link isn't valid")
    if row["status"] == "revoked":
        raise InviteError("INVITE_REVOKED", "that invitation was withdrawn")
    if row["status"] == "accepted" and str(row["accepted_user_id"] or "") != str(user_id):
        raise InviteError("INVITE_USED", "that invitation has already been used")
    if row["expires_at"] and row["expires_at"] < now:
        session.execute(text("UPDATE community.player_invite SET status='expired', updated_at=now() "
                             "WHERE id = :id"), {"id": str(row["id"])})
        raise InviteError("INVITE_EXPIRED", "that invitation has expired")

    cid = club_id or row["club_id"]
    # Make sure they belong to the club at all — an invitee is a member-of-record from the moment they
    # accept, which is what puts them in the CRM and makes the seat billable to a real person.
    session.execute(
        text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
             "VALUES (:c, :u, 'member', 'prospect') ON CONFLICT (club_id, user_id, role) DO NOTHING"),
        {"c": str(cid), "u": str(user_id)})

    trial = {"granted": False, "reason": "not_requested"}
    if grant_trial:
        try:
            from billing.membership import grant_signup_trial
            days = session.execute(
                text("SELECT guest_trial_days FROM club.policy WHERE club_id = :c"), {"c": str(cid)},
            ).scalar() or 7
            trial = grant_signup_trial(session, club_id=cid, user_id=user_id, days=int(days))
        except Exception:
            log.debug("invite trial grant skipped (benign)", exc_info=False)
            trial = {"granted": False, "reason": "unavailable"}

    seat_taken = False
    if row["booking_id"]:
        seat_taken = _claim_seat(session, club_id=cid, booking_id=row["booking_id"],
                                 party_id=row["party_id"], user_id=user_id, now=now)

    session.execute(
        text("UPDATE community.player_invite SET status='accepted', accepted_user_id=:u, "
             "       trial_granted_at = CASE WHEN :granted THEN now() ELSE trial_granted_at END, "
             "       updated_at=now() WHERE id = :id"),
        {"u": str(user_id), "granted": bool(trial.get("granted")), "id": str(row["id"])})

    if trial.get("granted"):
        try:
            from marketing_crm.tracking import emit
            em = session.execute(text('SELECT email FROM iam."user" WHERE id = :u'),
                                 {"u": str(user_id)}).scalar()
            emit("trial_started", {"club_id": str(cid), "email": em, "user_id": str(user_id),
                                   "trial_ends_at": trial.get("current_period_end"),
                                   "source": "player_invite"})
        except Exception:
            log.debug("trial_started emit skipped (benign)", exc_info=False)

    return {"ok": True, "club_id": str(cid), "trial": trial, "seat_taken": seat_taken,
            "booking_id": str(row["booking_id"]) if row["booking_id"] else None}


def _claim_seat(session, *, club_id, booking_id, party_id, user_id, now):
    """Attach the accepting user to the seat that was held for them, then RE-PRICE the game.

    The seat may have been billed to nobody while it sat 'invited' (no user_id → no payer), so the
    re-price is what turns it into a real debt owned by a real person. Delegates to
    community.seats.apply_seat_orders — the split lives in exactly one place."""
    from community import seats as _seats

    if party_id:
        hit = session.execute(
            text("UPDATE diary.booking_party SET user_id = :u, seat_status = 'held', "
                 "       joined_at = now() "
                 " WHERE id = :p AND booking_id = :b AND seat_status IN ('open','invited') "
                 "RETURNING id"),
            {"u": str(user_id), "p": str(party_id), "b": str(booking_id)},
        ).first()
    else:
        hit = session.execute(
            text("UPDATE diary.booking_party SET user_id = :u, seat_status = 'held', "
                 "       joined_at = now() "
                 " WHERE booking_id = :b AND user_id IS NULL AND seat_status IN ('open','invited') "
                 "   AND id = (SELECT id FROM diary.booking_party WHERE booking_id = :b "
                 "             AND user_id IS NULL AND seat_status IN ('open','invited') "
                 "             ORDER BY created_at, id LIMIT 1) RETURNING id"),
            {"u": str(user_id), "b": str(booking_id)},
        ).first()
    if not hit:
        return False
    try:
        _seats.apply_seat_orders(session, club_id=club_id, booking_id=booking_id, now=now)
    except _seats.SeatError as e:
        # The game locked or filled while the invitation sat in an inbox. The seat is released rather
        # than left in a state where somebody is on a court nobody is billing for.
        session.execute(
            text("UPDATE diary.booking_party SET seat_status = 'released', user_id = NULL "
                 " WHERE id = :id"), {"id": str(hit[0])})
        raise InviteError("SEAT_UNAVAILABLE",
                          "that game filled up or was already paid for — ask them for another time") \
            from e
    return True


def revoke_invite(session, *, club_id, invite_id, actor_user_id=None):
    n = session.execute(
        text("UPDATE community.player_invite SET status='revoked', updated_at=now() "
             " WHERE club_id = :c AND id = :id AND status = 'sent'"),
        {"c": str(club_id), "id": str(invite_id)},
    ).rowcount
    return {"ok": True, "revoked": bool(n)}

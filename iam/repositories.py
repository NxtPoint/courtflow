# iam/repositories.py — user upsert + membership/club resolution.
#
# Plain-SQL (SQLAlchemy Core text) repositories — no ORM models for iam.* (the schema
# is owned by iam/schema.py as raw idempotent DDL). Every function takes an explicit
# `session` and never commits; callers compose via db.session_scope() (1050 discipline).
#
# These power auth/principal.py:
#   - upsert_user_by_clerk_id : first-login provisioning / link-by-email, then resolve.
#   - memberships_for_user    : load all (club_id, role, member_status) rows for a user.
#   - resolve_club_by_host    : host -> club_id (docs/04 §3 signal #1).

from sqlalchemy import text

from db import norm_email


def get_user_by_clerk_id(session, clerk_user_id):
    if not clerk_user_id:
        return None
    row = session.execute(
        text("SELECT id, clerk_user_id, email, first_name, surname, phone "
             "FROM iam.user WHERE clerk_user_id = :cid"),
        {"cid": clerk_user_id},
    ).mappings().first()
    return dict(row) if row else None


def get_user_by_email(session, email):
    email = norm_email(email)
    if not email:
        return None
    row = session.execute(
        text("SELECT id, clerk_user_id, email, first_name, surname, phone "
             "FROM iam.user WHERE lower(email) = :e ORDER BY created_at LIMIT 1"),
        {"e": email},
    ).mappings().first()
    return dict(row) if row else None


def upsert_user_by_clerk_id(session, *, clerk_user_id, email=None, first_name=None,
                            surname=None, phone=None):
    """Resolve (and on first login provision) an iam.user for a verified Clerk token.

    Order, mirroring 1050's ensure_user_for_claims:
      1. Known clerk_user_id -> return it (refresh email if newly supplied).
      2. Else, an existing row with the SAME email (e.g. a seeded/imported user) ->
         LINK it by stamping clerk_user_id (no duplicate human).
      3. Else, INSERT a fresh iam.user.
    Returns the user row as a dict. Caller must have an open transaction."""
    email = norm_email(email)

    existing = get_user_by_clerk_id(session, clerk_user_id)
    if existing:
        if email and not existing.get("email"):
            session.execute(
                text("UPDATE iam.user SET email = :e, updated_at = now() WHERE id = :id"),
                {"e": email, "id": existing["id"]},
            )
            existing["email"] = email
        return existing

    if email:
        by_email = get_user_by_email(session, email)
        if by_email:
            session.execute(
                text("UPDATE iam.user SET clerk_user_id = :cid, updated_at = now() "
                     "WHERE id = :id"),
                {"cid": clerk_user_id, "id": by_email["id"]},
            )
            by_email["clerk_user_id"] = clerk_user_id
            return by_email

    row = session.execute(
        text("INSERT INTO iam.user (clerk_user_id, email, first_name, surname, phone) "
             "VALUES (:cid, :e, :fn, :sn, :ph) "
             "RETURNING id, clerk_user_id, email, first_name, surname, phone"),
        {"cid": clerk_user_id, "e": email, "fn": first_name, "sn": surname, "ph": phone},
    ).mappings().first()
    return dict(row)


def memberships_for_user(session, user_id):
    """All membership rows for a user (across clubs). Each: club_id, role, member_status."""
    rows = session.execute(
        text("SELECT id, club_id, user_id, role, member_status "
             "FROM iam.membership WHERE user_id = :uid ORDER BY joined_at"),
        {"uid": user_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def resolve_club_by_host(session, host):
    """Host -> club_id via club.branding (domain or marketing_hosts). docs/04 §3 #1.
    Strips a port and a leading 'www.'. Returns club_id (uuid) or None."""
    if not host:
        return None
    h = host.split(":", 1)[0].strip().lower()
    if h.startswith("www."):
        h = h[4:]
    row = session.execute(
        text("SELECT club_id FROM club.branding "
             "WHERE lower(domain) = :h "
             "   OR :h = ANY (SELECT lower(x) FROM unnest(marketing_hosts) AS x) "
             "LIMIT 1"),
        {"h": h},
    ).mappings().first()
    return row["club_id"] if row else None


def upsert_membership(session, *, club_id, user_id, role, member_status="none"):
    """Idempotent on (club_id, user_id, role). Used by signup + the seed/provision scripts."""
    session.execute(
        text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
             "VALUES (:club_id, :user_id, :role, :ms) "
             "ON CONFLICT (club_id, user_id, role) "
             "DO UPDATE SET member_status = EXCLUDED.member_status, updated_at = now()"),
        {"club_id": club_id, "user_id": user_id, "role": role, "ms": member_status},
    )

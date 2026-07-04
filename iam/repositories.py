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


def get_user_by_id(session, user_id):
    """Resolve an iam.user by id (UUID). Returns id, email, first_name, surname or None."""
    if not user_id:
        return None
    row = session.execute(
        text("SELECT id, email, first_name, surname FROM iam.user WHERE id = :id"),
        {"id": str(user_id)},
    ).mappings().first()
    return dict(row) if row else None


def guardian_user_id_for(session, dependent_user_id):
    """If `dependent_user_id` is an ACTIVE dependent (a login-less child), return the
    guardian-payer's iam.user id; else None. Used to route a notification ABOUT a dependent's
    booking/enrolment to the adult who actually has a login + inbox (child→guardian)."""
    if not dependent_user_id:
        return None
    row = session.execute(
        text("SELECT guardian_user_id FROM iam.dependent "
             "WHERE dependent_user_id = :d AND is_active = true "
             "ORDER BY created_at LIMIT 1"),
        {"d": str(dependent_user_id)},
    ).mappings().first()
    return row["guardian_user_id"] if row else None


def resolve_notification_recipient(session, *, user_id=None, email=None):
    """Resolve the deliverable recipient for a notification: an iam.user with a real id (the
    inbox owner) + their email/display name. Child→guardian: if `user_id` is a login-less
    dependent, redirect to the guardian-payer. Falls back to `email` lookup when no user_id.

    Returns {user_id, email, name} (user_id is a str UUID) or None if nothing resolvable."""
    user = None
    if user_id:
        user = get_user_by_id(session, user_id)
        # A login-less dependent has no email → route to the guardian's inbox.
        if user is not None and not (user.get("email") or "").strip():
            g_id = guardian_user_id_for(session, user_id)
            if g_id:
                guardian = get_user_by_id(session, g_id)
                if guardian:
                    user = guardian
    if user is None and email:
        user = get_user_by_email(session, email)
    if user is None or not user.get("id"):
        return None
    name = (user.get("first_name") or "").strip() or None
    return {"user_id": str(user["id"]), "email": (user.get("email") or None), "name": name}


def memberships_for_user(session, user_id):
    """All membership rows for a user (across clubs). Each: club_id, role, member_status."""
    rows = session.execute(
        text("SELECT id, club_id, user_id, role, member_status "
             "FROM iam.membership WHERE user_id = :uid ORDER BY joined_at"),
        {"uid": user_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def accept_coach_invites(session, user_id):
    """Mark any OUTSTANDING coach invite for this user as accepted — the coach has now
    signed in (claimed the account). Idempotent: only touches 'invited' rows, so repeated
    logins are no-ops. Runs on every authenticated login; a non-coach simply updates nothing.
    Returns the number of invites flipped."""
    if not user_id:
        return 0
    res = session.execute(
        text("UPDATE iam.coach_invite SET status = 'accepted', accepted_at = now() "
             "WHERE user_id = :uid AND status = 'invited'"),
        {"uid": str(user_id)},
    )
    return res.rowcount or 0


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


def sole_club_id(session):
    """The single club's id when this deployment has exactly one real (non-template) club.
    Used to auto-enrol a brand-new user as a member when the host doesn't map to a club
    (e.g. the onrender URL). Returns None if there are 0 or >1 clubs (ambiguous)."""
    rows = session.execute(
        text("SELECT id FROM club.club WHERE COALESCE(is_template, false) = false LIMIT 2")
    ).mappings().all()
    return rows[0]["id"] if len(rows) == 1 else None


def upsert_membership(session, *, club_id, user_id, role, member_status="none"):
    """Idempotent on (club_id, user_id, role). Used by signup + the seed/provision scripts."""
    session.execute(
        text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
             "VALUES (:club_id, :user_id, :role, :ms) "
             "ON CONFLICT (club_id, user_id, role) "
             "DO UPDATE SET member_status = EXCLUDED.member_status, updated_at = now()"),
        {"club_id": club_id, "user_id": user_id, "role": role, "ms": member_status},
    )


# ---------------------------------------------------------------------------
# Client self-service: profile (demographics) — "My Account" spec §2.3.
# ---------------------------------------------------------------------------

# The full editable demographics column set on iam.user. `email`, `clerk_user_id`, `role`
# and `club_id` are NEVER in here — email is the identity/ledger key (read-only in the UI),
# the rest are not self-service. patch_profile whitelists strictly to this set.
_PROFILE_COLUMNS = (
    "first_name", "surname", "phone", "dob",
    "address_line1", "address_line2", "city", "postal_code", "country",
    "emergency_contact_name", "emergency_contact_phone", "marketing_opt_in",
)


def get_profile(session, *, user_id):
    """The account holder's profile row (email is read-only display). Returns a dict or None."""
    cols = ", ".join(_PROFILE_COLUMNS)
    row = session.execute(
        text(f"SELECT id, email, {cols} FROM iam.user WHERE id = :uid"),
        {"uid": user_id},
    ).mappings().first()
    if not row:
        return None
    d = dict(row)
    if d.get("id") is not None:
        d["id"] = str(d["id"])
    if d.get("dob") is not None:
        d["dob"] = d["dob"].isoformat()
    return d


def patch_profile(session, *, user_id, fields):
    """UPDATE only whitelisted demographics. `fields` is the request body; any key not in
    _PROFILE_COLUMNS (notably email / clerk_user_id / role / club_id) is IGNORED. Empty strings
    on optional fields are stored as NULL. Returns the refreshed profile dict."""
    updates = {}
    for k in _PROFILE_COLUMNS:
        if k in fields:
            v = fields[k]
            if isinstance(v, str):
                v = v.strip()
                if v == "" and k not in ("first_name", "surname"):
                    v = None
            updates[k] = v
    if updates:
        set_sql = ", ".join(f"{k} = :{k}" for k in updates)
        params = dict(updates)
        params["uid"] = user_id
        session.execute(
            text(f"UPDATE iam.user SET {set_sql}, updated_at = now() WHERE id = :uid"),
            params,
        )
    return get_profile(session, user_id=user_id)


# ---------------------------------------------------------------------------
# Client self-service: dependents / children — "My Account" spec §3,
# CRM/foundations spec §3. A dependent is a LOGIN-LESS iam.user (clerk_user_id NULL)
# linked to a guardian via iam.dependent, so it can be a booking_party.user_id.
# ---------------------------------------------------------------------------

def list_dependents(session, *, club_id, guardian_user_id, include_inactive=False):
    """The guardian's dependents in this club. Always scoped by club_id + guardian_user_id."""
    sql = ("SELECT id, dependent_user_id, first_name, surname, dob, relationship, "
           "       is_minor, notes, is_active "
           "FROM iam.dependent WHERE club_id = :c AND guardian_user_id = :g")
    if not include_inactive:
        sql += " AND is_active = true"
    sql += " ORDER BY created_at"
    rows = session.execute(text(sql), {"c": club_id, "g": guardian_user_id}).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["dependent_user_id"] = str(d["dependent_user_id"])
        if d.get("dob") is not None:
            d["dob"] = d["dob"].isoformat()
        out.append(d)
    return out


def get_dependent(session, *, club_id, guardian_user_id, dependent_id):
    """One dependent BY ITS iam.dependent.id, ownership-checked (guardian + club). None if not
    the caller's — this ownership predicate is the write-path guard the spec requires."""
    row = session.execute(
        text("SELECT id, dependent_user_id, first_name, surname, dob, relationship, "
             "       is_minor, notes, is_active "
             "FROM iam.dependent WHERE id = :id AND club_id = :c AND guardian_user_id = :g"),
        {"id": dependent_id, "c": club_id, "g": guardian_user_id},
    ).mappings().first()
    if not row:
        return None
    d = dict(row)
    d["id"] = str(d["id"])
    d["dependent_user_id"] = str(d["dependent_user_id"])
    if d.get("dob") is not None:
        d["dob"] = d["dob"].isoformat()
    return d


def owns_dependent_user(session, *, club_id, guardian_user_id, dependent_user_id):
    """True iff dependent_user_id is an ACTIVE dependent of guardian_user_id in this club.
    Keyed on the dependent's iam.user.id — used to validate a booking/enrol player party."""
    if not dependent_user_id:
        return False
    row = session.execute(
        text("SELECT 1 FROM iam.dependent "
             "WHERE club_id = :c AND guardian_user_id = :g "
             "  AND dependent_user_id = :d AND is_active = true LIMIT 1"),
        {"c": club_id, "g": guardian_user_id, "d": dependent_user_id},
    ).first()
    return row is not None


def create_dependent(session, *, club_id, guardian_user_id, first_name, surname=None,
                     dob=None, relationship="child", is_minor=True, notes=None):
    """Create a login-less iam.user + the iam.dependent link (+ a player_profile carrying the
    guardian) in one transaction. Returns the new dependent dict (the iam.dependent row)."""
    # 1) the login-less human (clerk_user_id NULL, email NULL → inert to auth).
    dep_user = session.execute(
        text("INSERT INTO iam.user (clerk_user_id, email, first_name, surname) "
             "VALUES (NULL, NULL, :fn, :sn) RETURNING id"),
        {"fn": first_name, "sn": surname},
    ).mappings().first()
    dependent_user_id = dep_user["id"]

    # 2) the guardianship link + management metadata.
    row = session.execute(
        text("INSERT INTO iam.dependent "
             "(club_id, guardian_user_id, dependent_user_id, first_name, surname, dob, "
             " relationship, is_minor, notes) "
             "VALUES (:c, :g, :d, :fn, :sn, :dob, :rel, :minor, :notes) "
             "RETURNING id, dependent_user_id, first_name, surname, dob, relationship, "
             "          is_minor, notes, is_active"),
        {"c": club_id, "g": guardian_user_id, "d": dependent_user_id,
         "fn": first_name, "sn": surname, "dob": dob or None,
         "rel": relationship or "child", "minor": bool(is_minor), "notes": notes},
    ).mappings().first()

    # 3) carry junior detail on player_profile (reuses the existing guardian model). Best-effort.
    try:
        session.execute(
            text("INSERT INTO iam.player_profile (club_id, user_id, dob, guardian_user_id) "
                 "VALUES (:c, :u, :dob, :g)"),
            {"c": club_id, "u": dependent_user_id, "dob": dob or None, "g": guardian_user_id},
        )
    except Exception:
        pass  # player_profile is optional richer detail; the dependent stands without it

    d = dict(row)
    d["id"] = str(d["id"])
    d["dependent_user_id"] = str(d["dependent_user_id"])
    if d.get("dob") is not None:
        d["dob"] = d["dob"].isoformat()
    return d


_DEPENDENT_EDITABLE = ("first_name", "surname", "dob", "relationship", "is_minor", "notes")


def update_dependent(session, *, club_id, guardian_user_id, dependent_id, fields):
    """Patch an owned dependent (ownership re-checked in the WHERE). Returns the row or None."""
    existing = get_dependent(session, club_id=club_id, guardian_user_id=guardian_user_id,
                             dependent_id=dependent_id)
    if existing is None:
        return None
    updates = {}
    for k in _DEPENDENT_EDITABLE:
        if k in fields:
            v = fields[k]
            if isinstance(v, str):
                v = v.strip()
                if v == "" and k != "first_name":
                    v = None
            updates[k] = v
    if updates:
        set_sql = ", ".join(f"{k} = :{k}" for k in updates)
        params = dict(updates)
        params.update({"id": dependent_id, "c": club_id, "g": guardian_user_id})
        session.execute(
            text(f"UPDATE iam.dependent SET {set_sql}, updated_at = now() "
                 "WHERE id = :id AND club_id = :c AND guardian_user_id = :g"),
            params,
        )
        # keep the mirrored player_profile dob in step (best-effort).
        if "dob" in updates:
            try:
                session.execute(
                    text("UPDATE iam.player_profile SET dob = :dob, updated_at = now() "
                         "WHERE club_id = :c AND user_id = :u"),
                    {"dob": updates["dob"], "c": club_id, "u": existing["dependent_user_id"]},
                )
            except Exception:
                pass
    return get_dependent(session, club_id=club_id, guardian_user_id=guardian_user_id,
                         dependent_id=dependent_id)


def deactivate_dependent(session, *, club_id, guardian_user_id, dependent_id):
    """Soft-delete (is_active=false) so historical bookings/rosters keep a valid party ref.
    Ownership-checked. Returns True if a row was deactivated."""
    res = session.execute(
        text("UPDATE iam.dependent SET is_active = false, updated_at = now() "
             "WHERE id = :id AND club_id = :c AND guardian_user_id = :g AND is_active = true"),
        {"id": dependent_id, "c": club_id, "g": guardian_user_id},
    )
    return (res.rowcount or 0) > 0

# diary/booking_request.py — the rules a booking REQUEST obeys before it reaches create_booking,
# shared by the member app's route (diary/routes.py) and the public API (api_v1/). These used to live
# inside the route, which meant a second front door would have had to copy them — and a copy drifts.
#
#   member_by_email / addable_player_uid / service_max_clients / extra_players
#       who may be added as an extra player on a slot (a squad lesson's clients, a court's playmates)
#   apply_min_profile
#       the first-booking "confirm your details" rule (Client-360 Step 4)

import logging

from sqlalchemy import text

from db import session_scope

log = logging.getLogger("diary.booking_request")


def member_by_email(session, club_id, email):
    """Resolve an email to an iam.user that has ANY membership in this club (case-
    insensitive). Returns the user id (str) or None. Club-scoped — we never resolve a user
    who isn't a member of the actor's club."""
    if not email:
        return None
    row = session.execute(
        text("SELECT u.id FROM iam.user u "
             "JOIN iam.membership m ON m.user_id = u.id AND m.club_id = :c "
             "WHERE lower(u.email) = lower(:e) LIMIT 1"),
        {"c": club_id, "e": email.strip()},
    ).mappings().first()
    return str(row["id"]) if row else None


def service_max_clients(session, club_id, product_id):
    """How many clients a service (billing.product) allows on one slot — 1 for a normal private
    lesson, >1 for a semi-private / squad. Club-scoped; defaults to 1 (no product → private)."""
    if not product_id:
        return 1
    row = session.execute(
        text("SELECT COALESCE(max_clients, 1) AS mc FROM billing.product "
             "WHERE id = :p AND club_id = :c"),
        {"p": str(product_id), "c": club_id},
    ).scalar()
    try:
        return max(1, int(row or 1))
    except (TypeError, ValueError):
        return 1


def addable_player_uid(session, club_id, uid, *, owner_uid, is_staff):
    """Validate an extra PLAYER before billing them. Returns the uid (str) if allowed, else None.
    Allowed: a club MEMBER (adult with their own account) — anyone may add one (they get their own
    bill) — OR a DEPENDENT (child): staff may add any in-club child; a member may add only their OWN.
    Blocks a member from dumping a bill on an arbitrary account by posting a raw user_id."""
    if not uid:
        return None
    uid = str(uid)
    if session.execute(
        text("SELECT 1 FROM iam.membership WHERE club_id = :c AND user_id = :u LIMIT 1"),
        {"c": club_id, "u": uid}).first():
        return uid
    guardian = session.execute(
        text("SELECT guardian_user_id FROM iam.dependent WHERE club_id = :c AND dependent_user_id = :u "
             "AND is_active = true LIMIT 1"), {"c": club_id, "u": uid}).scalar()
    if guardian and (is_staff or str(guardian) == str(owner_uid)):
        return uid
    return None


def extra_players(session, p, body, *, owner_uid, is_staff):
    """The validated extra players for a booking request (`body['extra_clients']`): member emails,
    member user_ids, or a DEPENDENT's user_id — each through addable_player_uid.

    SQUAD LESSON: per-head clients, capped at the service's max_clients - 1.
    COURT: the named playmates (the seat step), capped at seats - 1. This used to be read for
    lessons only, so every name a member put on a court was silently dropped before community.seats
    saw it — the game showed one player and, with the seat rule on, the friends would never have been
    billed their share."""
    raw = body.get("extra_clients") or []
    btype = body.get("booking_type")
    if not raw or btype not in ("lesson", "court"):
        return []
    out = []
    for item in raw:
        if isinstance(item, str) and "@" in item:
            uid = member_by_email(session, p.club_id, item.strip())
        elif isinstance(item, dict):
            uid = item.get("user_id") or member_by_email(session, p.club_id,
                                                         (item.get("email") or "").strip())
        else:
            uid = item
        uid = addable_player_uid(session, p.club_id, uid, owner_uid=owner_uid, is_staff=is_staff)
        if uid:
            out.append(uid)
    if btype == "lesson":
        cap = max(0, service_max_clients(session, p.club_id, body.get("product_id")) - 1)
    else:   # a court seats the booker + (seats - 1) named players
        try:
            cap = max(0, int(body.get("seats") or 2) - 1)
        except (TypeError, ValueError):
            cap = 1
    return out[:cap]


def apply_min_profile(p, body):
    """Client-360 Step 4 — minimum-data capture at the first booking. For a SELF-booking member
    (staff + on-behalf are exempt), persist any name/surname/cell supplied, sync the CRM satellite,
    record a marketing opt-in if ticked, then return the list of fields STILL missing ([] = proceed).
    See docs/specs/CLIENT-360-CRM-PLAN.md §10 Step 4. Runs in its own transaction: the details are
    kept even if the booking that follows is refused."""
    from iam import repositories as iam_repo
    from iam.validation import missing_min_fields
    if p.role != "member":
        return []
    supplied = {k: (body.get(k) or "").strip() for k in ("first_name", "surname", "phone")}
    supplied = {k: v for k, v in supplied.items() if v}
    opted_in = str(body.get("marketing_opt_in")).lower() in ("1", "true", "yes", "on")
    with session_scope() as s:
        if supplied:
            iam_repo.patch_profile(s, user_id=p.user_id, fields=supplied)
            try:  # keep the CRM satellite in step (best-effort — never blocks the booking)
                prof = iam_repo.get_profile(s, user_id=p.user_id)
                from core.repositories.persons import link_person_for_user
                link_person_for_user(
                    s, iam_user_id=p.user_id, club_id=p.club_id, email=prof.get("email"),
                    first_name=prof.get("first_name"), surname=prof.get("surname"),
                    phone=prof.get("phone"))
            except Exception:
                log.debug("satellite sync at booking skipped (benign)", exc_info=False)
        if opted_in and p.email:  # marketing opt-in ticked in the "confirm your details" step
            try:
                from marketing_crm.consent.blueprint import grant_marketing_consent
                grant_marketing_consent(s, email=p.email, club_id=p.club_id, source="first_booking")
            except Exception:
                log.debug("marketing consent record skipped (benign)", exc_info=False)
        prof = iam_repo.get_profile(s, user_id=p.user_id)
    if opted_in and p.email:  # after commit: sync + subscribe to the Klaviyo marketing list
        try:
            from marketing_crm.crm_sync import sync as _crm
            _crm.subscribe_member(p.email, club_id=p.club_id)
        except Exception:
            log.debug("subscribe_member skipped (benign)", exc_info=False)
    return missing_min_fields(prof or {})

# diary/routes.py — the /api/diary/* surface (docs/03 §8) + the cron endpoints (§7).
#
# Thin routes (1050 style): resolve the principal (auth.resolve_principal), gate with
# iam.permissions.can(), pull club_id FROM THE PRINCIPAL (never the client — docs/02 §1),
# call the logic modules, map result dicts to JSON. Two blueprints:
#   diary_bp  — the member/coach/admin booking API.
#   cron_bp   — /api/cron/* handlers (OPS_KEY-guarded) the thin trigger hits.
#
# Result dicts from bookings/classes carry {"ok": bool, "status": int, "error": str}; we
# translate ok=False into the carried HTTP status (e.g. 409 SLOT_TAKEN).

import logging

from flask import Blueprint, jsonify, request, Response

from auth import resolve_principal
from db import session_scope
from iam.permissions import can
from iam import repositories as iam_repo
from iam.validation import missing_min_fields
from diary import availability as availability_mod
from diary import bookings as bookings_mod
from diary import classes as classes_mod
from diary import crons as crons_mod
from diary import pricing as pricing_mod

log = logging.getLogger("diary.routes")

diary_bp = Blueprint("diary", __name__, url_prefix="/api/diary")
cron_bp = Blueprint("diary_cron", __name__, url_prefix="/api/cron")


# ---------------------------------------------------------------------------
# auth helpers
# ---------------------------------------------------------------------------

def _principal():
    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return None
    return p


def _need_club(p):
    """A club-scoped action needs a resolved club_id (platform_admin may pass one via
    X-Club, already resolved into p.club_id by the principal resolver)."""
    return p.club_id is not None


def _result(res):
    """Map a logic result dict to a Flask (json, status) response."""
    if res is None:
        return jsonify(error="NOT_FOUND"), 404
    if res.get("ok"):
        body = {k: v for k, v in res.items() if k != "ok"}
        return jsonify(body), 200
    status = res.get("status", 400)
    return jsonify(error=res.get("error"), message=res.get("message"),
                   **{k: v for k, v in res.items()
                      if k not in ("ok", "status", "error", "message")}), status


def _body():
    return request.get_json(silent=True) or {}


def _min_profile_gate(p, b):
    """Client-360 Step 4 — minimum-data capture at the first booking. For a SELF-booking member
    (staff + on-behalf are exempt), persist any name/surname/cell the SPA supplied, sync the CRM
    satellite, then return a 422 {needs_profile:[...]} response if the profile is still incomplete
    (the booking widget renders a 'confirm your details' step + re-submits). Returns None to
    proceed. See docs/specs/CLIENT-360-CRM-PLAN.md §10 Step 4."""
    if p.role != "member":
        return None
    supplied = {k: (b.get(k) or "").strip() for k in ("first_name", "surname", "phone")}
    supplied = {k: v for k, v in supplied.items() if v}
    opted_in = str(b.get("marketing_opt_in")).lower() in ("1", "true", "yes", "on")
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
        if opted_in and p.email:  # marketing opt-in ticked in the "confirm your details" modal
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
    missing = missing_min_fields(prof or {})
    if missing:
        return jsonify(error="profile_incomplete", needs_profile=missing), 422
    return None


# Roles permitted to book ON BEHALF of someone else (docs/08). A member/guest may only
# ever book for themselves, so for_email/for_guest_name are silently ignored for them.
_ON_BEHALF_ROLES = ("coach", "club_admin", "platform_admin")


def _can_manage_class(p, coach_user_id):
    """Admins manage any class; a coach manages only their OWN classes (the class resource's
    coach_user_id == the coach). Mirrors the ownership gate used for lessons."""
    if p.role in ("club_admin", "platform_admin"):
        return True
    if p.role == "coach":
        return coach_user_id is not None and str(coach_user_id) == str(p.user_id)
    return False


def _member_by_email(session, club_id, email):
    """Resolve an email to an iam.user that has ANY membership in this club (case-
    insensitive). Returns the user id (str) or None. Club-scoped — we never resolve a user
    who isn't a member of the actor's club. Used by the on-behalf booking flow only."""
    if not email:
        return None
    from sqlalchemy import text
    row = session.execute(
        text("SELECT u.id FROM iam.user u "
             "JOIN iam.membership m ON m.user_id = u.id AND m.club_id = :c "
             "WHERE lower(u.email) = lower(:e) LIMIT 1"),
        {"c": club_id, "e": email.strip()},
    ).mappings().first()
    return str(row["id"]) if row else None


def _service_max_clients(session, club_id, product_id):
    """How many clients a service (billing.product) allows on one slot — 1 for a normal private
    lesson, >1 for a semi-private / squad. Club-scoped; defaults to 1 (no product → private)."""
    if not product_id:
        return 1
    from sqlalchemy import text
    row = session.execute(
        text("SELECT COALESCE(max_clients, 1) AS mc FROM billing.product "
             "WHERE id = :p AND club_id = :c"),
        {"p": str(product_id), "c": club_id},
    ).scalar()
    try:
        return max(1, int(row or 1))
    except (TypeError, ValueError):
        return 1


def _addable_player_uid(session, club_id, uid, *, owner_uid, is_staff):
    """Validate a semi-private extra PLAYER before billing them. Returns the uid (str) if allowed, else
    None. Allowed: a club MEMBER (adult with their own account) — anyone may add one (they get their own
    bill, per the squad rule) — OR a DEPENDENT (child): staff may add any in-club child; a member may add
    only their OWN. Blocks a member from dumping a bill on an arbitrary account by posting a raw user_id."""
    if not uid:
        return None
    from sqlalchemy import text
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


# ---------------------------------------------------------------------------
# availability + resources (read)
# ---------------------------------------------------------------------------

@diary_bp.get("/availability")
def availability():
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    q = request.args
    audience = q.get("audience") or ("member" if p.role in ("member", "coach", "club_admin")
                                     else "visitor")
    kind = q.get("kind")
    with session_scope() as s:
        # A COURT booking by a member with an active membership is free INSIDE that membership's
        # access window (an off-peak tier is free only in its hours; full/trial covers any time).
        # We pass the windows so each slot is priced individually: 0 inside the window, PAYG outside
        # — matching the server's settle decision (diary.pricing.membership_covers). Lessons/classes
        # are never auto-covered.
        windows = (pricing_mod.active_membership_windows(s, club_id=p.club_id, user_id=p.user_id)
                   if kind == "court" else [])
        slots = availability_mod.compute_availability(
            s, club_id=p.club_id,
            resource_id=q.get("resource_id"), kind=kind,
            coach_user_id=q.get("coach_id"), surface=q.get("surface"),
            date_from=q.get("date_from"), date_to=q.get("date_to"),
            duration_minutes=q.get("duration", type=int),
            audience=audience,
            any_resource=(q.get("any") in ("1", "true", "yes")),
            membership_covered=bool(windows), membership_windows=windows,
            product_id=q.get("product_id"),   # court SERVICE scope (Hardcourt vs Clay)
            # The member whose entitlement (caps + court-service eligibility) silently shapes coverage.
            member_user_id=(p.user_id if kind == "court" else None),
        )
    return jsonify(slots=slots, count=len(slots)), 200


@diary_bp.get("/equipment")
def equipment_list():
    """Active equipment items (ball machine / racquets / balls) for the booking add-on picker.
    Each = {id, name, quantity, feature_on_home, price_id, amount_minor, currency_code}."""
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    with session_scope() as s:
        from diary.equipment import list_equipment
        items = list_equipment(s, club_id=p.club_id, active_only=True)
    return jsonify(equipment=items, count=len(items)), 200


@diary_bp.get("/durations")
def durations():
    """Priced durations for a service + whether the caller's COURT bookings are membership-
    covered. Powers the booking wizard's Duration step (Service → Duration → Schedule).
        GET /api/diary/durations?kind=court|lesson&coach_id=&audience=
        -> {durations:[{duration_minutes, amount_minor, price_id}], membership_covered, currency}
    membership_covered is true only for kind=court when the caller holds an active membership."""
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    q = request.args
    kind = q.get("kind") or "court"
    audience = q.get("audience") or ("member" if p.role in ("member", "coach", "club_admin")
                                     else "visitor")
    # The booking 'court'/'lesson' kind maps to the billing product kind (court_booking/lesson).
    price_kind = {"court": "court_booking", "lesson": "lesson", "coach": "lesson"}.get(kind, kind)
    product_id = q.get("product_id")   # a specific court/lesson SERVICE (Hardcourt vs Clay, etc.)
    with session_scope() as s:
        rows = pricing_mod.durations_for(
            s, club_id=p.club_id, kind=price_kind,
            coach_user_id=q.get("coach_id"), audience=audience, product_id=product_id)
        covered = bool(kind == "court" and pricing_mod.has_active_membership(
            s, club_id=p.club_id, user_id=p.user_id))
        # Per-service payment preference (which methods THIS service offers) — the booking flow
        # intersects its pay options with this. None = no restriction (all club-enabled).
        pay_modes = pricing_mod.payment_modes_for(
            s, club_id=p.club_id, kind=price_kind, coach_user_id=q.get("coach_id"),
            product_id=product_id)
    currency = rows[0]["currency_code"] if rows else None
    out = [{"duration_minutes": r["duration_minutes"], "amount_minor": r["amount_minor"],
            "price_id": r["price_id"]} for r in rows]
    return jsonify(durations=out, membership_covered=covered, currency=currency,
                   payment_modes=pay_modes), 200


@diary_bp.get("/services")
def services():
    """Bookable SERVICES for a coach — each product (e.g. Private / Semi-private lesson) with its OWN
    durations + payment modes — so the booking wizard offers the service name before the duration.
        GET /api/diary/services?kind=lesson&coach_id=&audience= -> {services:[{product_id,name,
        durations:[{duration_minutes,amount_minor,price_id}], payment_modes, currency_code}]}"""
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    q = request.args
    kind = q.get("kind") or "lesson"
    price_kind = {"court": "court_booking", "lesson": "lesson", "coach": "lesson",
                  "class": "class"}.get(kind, kind)
    audience = q.get("audience") or ("member" if p.role in ("member", "coach", "club_admin")
                                     else "visitor")
    with session_scope() as s:
        svcs = pricing_mod.services_for(s, club_id=p.club_id, kind=price_kind,
                                        coach_user_id=q.get("coach_id"), audience=audience)
    return jsonify(services=svcs), 200


@diary_bp.get("/resources")
def resources():
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    from sqlalchemy import text
    with session_scope() as s:
        rows = s.execute(
            text("SELECT r.id, r.kind, r.name, r.surface, r.coach_user_id, r.capacity, "
                 "       r.is_active, r.rank, r.product_id, "
                 # has_hours / is_bookable: a coach with no availability_rule OR marked not-bookable
                 # is unbookable — the client picker filters these out so they're never offered
                 # (booking-validation sprint). Courts have coach_user_id NULL -> is_bookable true.
                 "       EXISTS (SELECT 1 FROM diary.availability_rule ar "
                 "               WHERE ar.club_id = r.club_id AND ar.resource_id = r.id) AS has_hours, "
                 "       COALESCE(cp.is_bookable, true) AS is_bookable "
                 "FROM diary.resource r "
                 "LEFT JOIN iam.coach_profile cp ON cp.club_id = r.club_id AND cp.user_id = r.coach_user_id "
                 "WHERE r.club_id=:c AND r.is_active=true "
                 "ORDER BY r.kind, r.rank, r.name"),
            {"c": p.club_id},
        ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("id", "coach_user_id", "product_id"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        out.append(d)
    return jsonify(resources=out), 200


# ---------------------------------------------------------------------------
# bookings CRUD
# ---------------------------------------------------------------------------

@diary_bp.post("/bookings")
def create_booking():
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    if not can(p, "create_booking", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    b = _body()
    audience = b.get("audience") or ("member" if p.role in ("member", "coach", "club_admin")
                                     else "visitor")
    parties = list(b.get("parties") or [])
    # On-behalf booking (docs/08): a coach/admin may book FOR a client. The owner override
    # is the ONLY booking field the actor can set away from themselves, and ONLY when their
    # role allows it (the club_id + actor stay from the principal — never the body).
    booked_for_user_id = None
    for_email = (b.get("for_email") or "").strip()
    for_guest_name = (b.get("for_guest_name") or "").strip()
    for_guest_email = (b.get("for_guest_email") or "").strip()
    if p.role in _ON_BEHALF_ROLES and (for_email or for_guest_name):
        with session_scope() as s:
            booked_for_user_id = _member_by_email(s, p.club_id, for_email)
        if booked_for_user_id is None:
            # Not a club member -> treat as a walk-in: actor stays booked_by, the client
            # rides along as a guest player party (no member host required for a player).
            guest_name = for_guest_name or (for_email or "Guest")
            parties.append({"party_role": "player", "guest_name": guest_name,
                            "guest_email": for_guest_email or for_email or None})

    # Client-360 Step 4 — capture name+surname+cell at the first booking (self-booking members).
    if booked_for_user_id is None:
        gate = _min_profile_gate(p, b)
        if gate is not None:
            return gate

    with session_scope() as s:
        # SEMI-PRIVATE (squad) lesson: extra PLAYERS ride the same slot, each billed their own order
        # (per-head). Accept member emails, member user_ids, or a member's DEPENDENT (child) user_id.
        # Each is validated as addable (see _addable_player_uid — a non-staff booker may only add club
        # members + their OWN kids, never an arbitrary account) and CAPPED at the service's max_clients.
        extra_clients = []
        raw_extra = b.get("extra_clients") or []
        if raw_extra and b.get("booking_type") == "lesson":
            is_staff = p.role in _ON_BEHALF_ROLES
            owner_uid = booked_for_user_id or p.user_id   # whose OWN dependents may be added (non-staff)
            for item in raw_extra:
                uid = None
                if isinstance(item, str) and "@" in item:
                    uid = _member_by_email(s, p.club_id, item.strip())
                elif isinstance(item, dict):
                    uid = item.get("user_id") or _member_by_email(s, p.club_id, (item.get("email") or "").strip())
                else:
                    uid = item
                uid = _addable_player_uid(s, p.club_id, uid, owner_uid=owner_uid, is_staff=is_staff)
                if uid:
                    extra_clients.append(uid)
            cap = max(0, _service_max_clients(s, p.club_id, b.get("product_id")) - 1)
            extra_clients = extra_clients[:cap]
        res = bookings_mod.create_booking(
            s, club_id=p.club_id, booked_by_user_id=p.user_id, role=p.role,
            booking_type=b.get("booking_type", "court"),
            resource_id=b.get("resource_id"),
            starts_at=b.get("starts_at"), ends_at=b.get("ends_at"),
            settlement_mode=b.get("settlement_mode", "at_court"),
            parties=parties,
            coach_user_id=b.get("coach_user_id"),
            court_resource_id=b.get("court_resource_id"),
            product_id=b.get("product_id"),   # the chosen SERVICE (Private/Semi-private) → price exactly it
            addons=b.get("addons"),            # equipment hire [{resource_id, qty}] on a court booking
            extra_clients=extra_clients,       # semi-private squad members (each billed their own order)
            audience=audience, notes=b.get("notes"),
            recurrence_id=b.get("recurrence_id"),
            booked_for_user_id=booked_for_user_id,
            # BACK-CAPTURE: allow a PAST date only for a STAFF on-behalf booking (a coach/admin logging a
            # lesson that already happened). ANDed with the role here — a member self-book can never backdate.
            allow_past=(bool(b.get("allow_past")) and p.role in _ON_BEHALF_ROLES),
            propose=bool(b.get("propose")),
        )
    return _result(res)


@diary_bp.get("/bookings")
def list_bookings():
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    q = request.args
    with session_scope() as s:
        rows = bookings_mod.list_bookings(
            s, club_id=p.club_id, role=p.role, user_id=p.user_id,
            date_from=q.get("date_from"), date_to=q.get("date_to"),
            status=q.get("status"), resource_id=q.get("resource_id"),
            as_coach=(q.get("as_coach") in ("1", "true", "yes")),
        )
    return jsonify(bookings=rows, count=len(rows)), 200


@diary_bp.get("/bookings/<booking_id>")
def get_booking(booking_id):
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    with session_scope() as s:
        bk = bookings_mod.get_booking(s, club_id=p.club_id, booking_id=booking_id)
        if not bk:
            return jsonify(error="NOT_FOUND"), 404
        if not can(p, "edit_booking", bk):
            # members can read their own; admins/coaches per can(); else hide.
            if not (p.role in ("club_admin", "platform_admin")
                    or str(bk.get("booked_by_user_id")) == str(p.user_id)
                    or str(bk.get("coach_user_id")) == str(p.user_id)):
                return jsonify(error="forbidden"), 403
    return jsonify(booking=bk), 200


@diary_bp.patch("/bookings/<booking_id>")
def reschedule_booking(booking_id):
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    b = _body()
    with session_scope() as s:
        bk = bookings_mod.get_booking(s, club_id=p.club_id, booking_id=booking_id)
        if not bk:
            return jsonify(error="NOT_FOUND"), 404
        if not can(p, "reschedule_booking", bk):
            return jsonify(error="forbidden"), 403
        res = bookings_mod.reschedule_booking(
            s, club_id=p.club_id, booking_id=booking_id,
            new_starts_at=b.get("starts_at"), new_ends_at=b.get("ends_at"),
            actor_user_id=p.user_id, role=p.role, scope=b.get("scope", "this"),
        )
    return _result(res)


@diary_bp.get("/members/search")
def search_members():
    """Staff-only member picker for the semi-private 'add player' flow: match members by name/email and
    surface their dependents (kids) as their own rows — so a coach can add a member OR a parent's child
    by NAME (a parent account with two kids shows both)."""
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    if p.role not in ("coach", "club_admin", "platform_admin"):
        return jsonify(error="forbidden"), 403
    q = (request.args.get("q") or "").strip()
    with session_scope() as s:
        results = iam_repo.search_members_with_dependents(s, club_id=p.club_id, q=q, limit=8)
    return jsonify(results=results, count=len(results)), 200


@diary_bp.post("/bookings/<booking_id>/add-player")
def add_lesson_player(booking_id):
    """Add ANOTHER client to an existing semi-private lesson AFTER it was booked (squad confirmations
    land late). Same edit gate as reschedule (staff or the booking's owner). The new client is billed
    their own owed order at the service price (per-head)."""
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    b = _body()
    with session_scope() as s:
        bk = bookings_mod.get_booking(s, club_id=p.club_id, booking_id=booking_id)
        if not bk:
            return jsonify(error="NOT_FOUND"), 404
        if not can(p, "reschedule_booking", bk):
            return jsonify(error="forbidden"), 403
        uid = (b.get("user_id") or "").strip() or None
        if not uid:
            uid = _member_by_email(s, p.club_id, (b.get("email") or "").strip())
        if not uid:
            return jsonify(error="MEMBER_NOT_FOUND",
                           message="No member with that email in your club."), 404
        res = bookings_mod.add_lesson_partner(
            s, club_id=p.club_id, booking_id=booking_id, new_user_id=uid,
            actor_user_id=p.user_id, role=p.role)
    return _result(res)


@diary_bp.post("/bookings/<booking_id>/cancel")
def cancel_booking(booking_id):
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    b = _body()
    with session_scope() as s:
        bk = bookings_mod.get_booking(s, club_id=p.club_id, booking_id=booking_id)
        if not bk:
            return jsonify(error="NOT_FOUND"), 404
        if not can(p, "cancel_booking", bk):
            return jsonify(error="forbidden"), 403
        res = bookings_mod.cancel_booking(
            s, club_id=p.club_id, booking_id=booking_id, actor_user_id=p.user_id,
            role=p.role, reason=b.get("reason"),
        )
    return _result(res)


@diary_bp.post("/bookings/<booking_id>/status")
def set_status(booking_id):
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    b = _body()
    new_status = b.get("status")
    with session_scope() as s:
        bk = bookings_mod.get_booking(s, club_id=p.club_id, booking_id=booking_id)
        if not bk:
            return jsonify(error="NOT_FOUND"), 404
        if not can(p, "mark_attendance", bk):
            return jsonify(error="forbidden"), 403
        if new_status == "attended":
            res = bookings_mod.set_attendance(
                s, club_id=p.club_id, booking_id=booking_id,
                party_id=b.get("party_id"), attended=b.get("attended", True))
        else:
            res = bookings_mod.set_status(
                s, club_id=p.club_id, booking_id=booking_id, new_status=new_status,
                actor_user_id=p.user_id, role=p.role)
    return _result(res)


# --- lesson approval lifecycle: accept / propose new time / decline --------
@diary_bp.post("/bookings/<booking_id>/accept")
def accept_booking(booking_id):
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    with session_scope() as s:
        bk = bookings_mod.get_booking(s, club_id=p.club_id, booking_id=booking_id)
        if not bk:
            return jsonify(error="NOT_FOUND"), 404
        if not can(p, "accept_booking", bk):
            return jsonify(error="forbidden"), 403
        res = bookings_mod.accept_booking(
            s, club_id=p.club_id, booking_id=booking_id, actor_user_id=p.user_id, role=p.role)
    return _result(res)


@diary_bp.post("/bookings/<booking_id>/propose")
def propose_time(booking_id):
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    b = _body()
    with session_scope() as s:
        bk = bookings_mod.get_booking(s, club_id=p.club_id, booking_id=booking_id)
        if not bk:
            return jsonify(error="NOT_FOUND"), 404
        if not can(p, "propose_time", bk):
            return jsonify(error="forbidden"), 403
        res = bookings_mod.propose_time(
            s, club_id=p.club_id, booking_id=booking_id, actor_user_id=p.user_id, role=p.role,
            starts_at=b.get("starts_at"), ends_at=b.get("ends_at"))
    return _result(res)


@diary_bp.post("/bookings/<booking_id>/decline")
def decline_booking(booking_id):
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    b = _body()
    with session_scope() as s:
        bk = bookings_mod.get_booking(s, club_id=p.club_id, booking_id=booking_id)
        if not bk:
            return jsonify(error="NOT_FOUND"), 404
        if not can(p, "decline_booking", bk):
            return jsonify(error="forbidden"), 403
        res = bookings_mod.decline_booking(
            s, club_id=p.club_id, booking_id=booking_id, actor_user_id=p.user_id, role=p.role,
            reason=b.get("reason"))
    return _result(res)


@diary_bp.get("/bookings/<booking_id>/calendar.ics")
def booking_calendar(booking_id):
    """An iCalendar (.ics) for a booking — powers the in-app 'Add to calendar' download, and is
    the file the confirmation email will attach once SES/Klaviyo is wired. The booker, the coach
    who runs it, or an admin may fetch it."""
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    from sqlalchemy import text
    with session_scope() as s:
        bk = bookings_mod.get_booking(s, club_id=p.club_id, booking_id=booking_id)
        if not bk:
            return jsonify(error="NOT_FOUND"), 404
        is_admin = p.role in ("club_admin", "platform_admin")
        owns = (str(bk.get("booked_by_user_id")) == str(p.user_id)
                or str(bk.get("coach_user_id")) == str(p.user_id))
        if not (is_admin or owns):
            return jsonify(error="forbidden"), 403
        loc = s.execute(
            text("SELECT c.name AS club, l.address_line, l.city "
                 "FROM club.club c LEFT JOIN club.location l ON l.club_id = c.id "
                 "WHERE c.id = :c ORDER BY l.id LIMIT 1"),
            {"c": p.club_id}).mappings().first()
    from diary import calendar as cal
    club = (loc and loc.get("club")) or "the club"
    where = ", ".join(x for x in [(loc or {}).get("address_line"), (loc or {}).get("city")] if x) or club
    bt = bk.get("booking_type") or "booking"
    summary = club + " · " + (bk.get("resource_name") or bt.title())
    desc = bt.title() + " at " + club + " — settlement: " + (bk.get("settlement_mode") or "n/a") + "."
    confirmed = bk.get("status") in ("confirmed", "held", "completed")
    ics = cal.build_ics(
        uid="booking-" + str(booking_id) + "@courtflow",
        summary=summary, starts_at=bk["starts_at"], ends_at=bk["ends_at"],
        description=desc, location=where,
        status="CONFIRMED" if confirmed else "TENTATIVE")
    return Response(ics, mimetype="text/calendar",
                    headers={"Content-Disposition": "attachment; filename=booking.ics"})


# ---------------------------------------------------------------------------
# classes
# ---------------------------------------------------------------------------

@diary_bp.get("/classes")
def list_classes():
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    q = request.args
    with session_scope() as s:
        rows = classes_mod.list_sessions(
            s, club_id=p.club_id, date_from=q.get("date_from"),
            date_to=q.get("date_to"), resource_id=q.get("resource_id"))
    return jsonify(classes=rows, count=len(rows)), 200


@diary_bp.get("/classes/mine")
def my_enrolments():
    """The caller's OWN class enrolments (self + dependents) — so the client can see & cancel the
    classes they booked (a class enrolment isn't a diary.booking, so it's not in /diary/bookings)."""
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    with session_scope() as s:
        rows = classes_mod.list_my_enrolments(s, club_id=p.club_id, user_id=p.user_id)
    return jsonify(enrolments=rows, count=len(rows)), 200


@diary_bp.post("/classes/<class_session_id>/enrol")
def enrol(class_session_id):
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    if not can(p, "book_class", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    b = _body()
    # Client-360 Step 4 — capture the minimum profile on a member's own class enrolment too
    # (so classes aren't a bypass of the first-booking capture).
    gate = _min_profile_gate(p, b)
    if gate is not None:
        return gate
    # admins/coaches may enrol another user; members enrol themselves.
    target_user = b.get("user_id") if p.role in ("club_admin", "platform_admin", "coach") else p.user_id
    target_user = target_user or p.user_id
    # "Who's playing?" — a member may enrol their OWN dependent (child). The enrolment's player is
    # the child (activity → player) but the order bills the GUARDIAN (spend → payer). Ownership is
    # validated here; an unowned/unknown dependent_user_id is ignored (falls back to the caller).
    payer_user = None
    dep = (b.get("dependent_user_id") or "").strip() or None
    with session_scope() as s:
        if dep and iam_repo.owns_dependent_user(
                s, club_id=p.club_id, guardian_user_id=p.user_id, dependent_user_id=dep):
            target_user = dep
            payer_user = p.user_id   # bill the guardian, not the child
        res = classes_mod.enrol(
            s, club_id=p.club_id, class_session_id=class_session_id, user_id=target_user,
            settlement_mode=b.get("settlement_mode", "at_court"),
            audience=b.get("audience", "member"), payer_user_id=payer_user, role=p.role)
    return _result(res)


@diary_bp.get("/classes/<session_id>/roster")
def class_roster(session_id):
    """Coach (own class) / admin: the enrolled + waitlisted players for a session."""
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    with session_scope() as s:
        coach_uid, cs = classes_mod.session_owner_coach(s, club_id=p.club_id,
                                                        session_id=session_id)
        if cs is None:
            return jsonify(error="SESSION_NOT_FOUND"), 404
        if not _can_manage_class(p, coach_uid):
            return jsonify(error="forbidden"), 403
        res = classes_mod.roster(s, club_id=p.club_id, session_id=session_id)
    return _result(res)


@diary_bp.post("/classes/<session_id>/attendance")
def class_attendance(session_id):
    """Coach (own class) / admin: mark a player's enrolment attended / no-show."""
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    b = _body()
    user_id = b.get("user_id")
    if not user_id:
        return jsonify(error="user_id required"), 400
    with session_scope() as s:
        coach_uid, cs = classes_mod.session_owner_coach(s, club_id=p.club_id,
                                                        session_id=session_id)
        if cs is None:
            return jsonify(error="SESSION_NOT_FOUND"), 404
        if not _can_manage_class(p, coach_uid):
            return jsonify(error="forbidden"), 403
        res = classes_mod.mark_attendance(
            s, club_id=p.club_id, session_id=session_id, user_id=user_id,
            attended=bool(b.get("attended", True)))
    return _result(res)


@diary_bp.post("/classes/<class_session_id>/cancel-enrolment")
def cancel_enrolment(class_session_id):
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    b = _body()
    req_user = b.get("user_id")
    with session_scope() as s:
        if p.role in ("club_admin", "platform_admin", "coach"):
            target_user = req_user or p.user_id
        elif req_user and req_user != p.user_id and classes_mod.is_guardian_of(s, p.user_id, req_user):
            target_user = req_user          # a parent cancelling their child's place in a class
        else:
            target_user = p.user_id
        res = classes_mod.cancel_enrolment(
            s, club_id=p.club_id, class_session_id=class_session_id, user_id=target_user,
            actor_user_id=p.user_id, role=p.role)
    return _result(res)


# ---------------------------------------------------------------------------
# time-off (coach/admin block time)
# ---------------------------------------------------------------------------

@diary_bp.post("/time-off")
def time_off():
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    b = _body()
    resource_id = b.get("resource_id")
    from sqlalchemy import text
    with session_scope() as s:
        res_row = s.execute(
            text("SELECT id, coach_user_id FROM diary.resource WHERE club_id=:c AND id=:r"),
            {"c": p.club_id, "r": resource_id},
        ).mappings().first()
        if not res_row:
            return jsonify(error="RESOURCE_NOT_FOUND"), 404
        gate = {"club_id": p.club_id, "coach_user_id": res_row["coach_user_id"]}
        if not can(p, "manage_own_time_off", gate):
            return jsonify(error="forbidden"), 403
        if p.role == "coach" and str(res_row["coach_user_id"]) != str(p.user_id):
            return jsonify(error="forbidden"), 403
        row = s.execute(
            text("INSERT INTO diary.time_off (club_id, resource_id, starts_at, ends_at, "
                 "reason, created_by) VALUES (:c, :r, :sa, :ea, :reason, :by) RETURNING id"),
            {"c": p.club_id, "r": resource_id, "sa": b.get("starts_at"),
             "ea": b.get("ends_at"), "reason": b.get("reason"), "by": p.user_id},
        ).mappings().first()
    return jsonify(time_off_id=str(row["id"])), 201


# ---------------------------------------------------------------------------
# master diary (admin)
# ---------------------------------------------------------------------------

def _pay_label(settlement_mode, order_status):
    """A short payment-status word for a diary block (staff glance). Derived from the settlement mode +
    order status — consistent with the canonical vocabulary but compressed for grid density. None = no
    order / nothing to show."""
    if settlement_mode == "membership_covered":
        return "Covered"
    if settlement_mode == "free":
        return "Free"
    if settlement_mode == "token":
        return "Pack"
    if order_status == "paid":
        return "Paid"
    if order_status == "awaiting_payment":
        return "Awaiting"
    if order_status == "open":
        return "Owed"
    return None


@diary_bp.get("/master")
def master_diary():
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    # Whole-club diary READ (occupancy/gaps). Admins + coaches. Staff-only, so it now carries WHO booked
    # (primary booker name — never a guest), the payment status, coach + equipment, so the owner can see
    # at a glance who has a court and whether it's paid. `view_club_diary` gates it to staff.
    if not can(p, "view_club_diary", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    q = request.args
    from sqlalchemy import text
    out = []
    try:
        with session_scope() as s:
            rows = s.execute(
                text("SELECT b.id, b.booking_type, b.resource_id, r.name AS resource_name, "
                     "       r.kind, b.coach_user_id, b.starts_at, b.ends_at, b.status, "
                     "       b.booked_by_user_id, b.order_id, b.settlement_mode, b.notes, "
                     "       ub.first_name AS booker_first, ub.surname AS booker_surname, "
                     "       cu.first_name AS coach_first, cu.surname AS coach_surname, "
                     "       cp.display_name AS coach_display, o.status AS order_status, eq.equipment "
                     "FROM diary.booking b "
                     "LEFT JOIN diary.resource r ON r.id=b.resource_id "
                     "LEFT JOIN iam.user ub ON ub.id = b.booked_by_user_id "
                     "LEFT JOIN iam.user cu ON cu.id = b.coach_user_id "
                     "LEFT JOIN iam.coach_profile cp ON cp.user_id = b.coach_user_id AND cp.club_id = b.club_id "
                     "LEFT JOIN billing.\"order\" o ON o.id = b.order_id "
                     "LEFT JOIN LATERAL ("
                     "   SELECT string_agg(er.name || (CASE WHEN be.qty>1 THEN ' x'||be.qty ELSE '' END), ', ') AS equipment "
                     "   FROM diary.booking_equipment be JOIN diary.resource er ON er.id = be.resource_id "
                     "   WHERE be.booking_id = b.id) eq ON true "
                     "WHERE b.club_id=:c AND b.status IN ('held','confirmed','completed','no_show') "
                     # Exclude the class court-HOLD rows (booking_type='class'): the class is rendered
                     # per court via master_class_events, so feeding these too would double-render it.
                     # The rows STAY in the DB — they do the GiST court-blocking; just not on the grid.
                     "  AND b.booking_type <> 'class' "
                     "  AND (CAST(:df AS timestamptz) IS NULL OR b.starts_at >= CAST(:df AS timestamptz)) "
                     "  AND (CAST(:dt AS timestamptz) IS NULL OR b.starts_at <= CAST(:dt AS timestamptz)) "
                     "ORDER BY b.starts_at"),
                {"c": p.club_id, "df": q.get("date_from"), "dt": q.get("date_to")},
            ).mappings().all()
            for r in rows:
                d = dict(r)
                # The primary booker's name (staff diary shows who has the court); a lesson's auto-held
                # court row is labelled by COACH instead (below), never by client/payment/equipment.
                d["booked_by_name"] = " ".join(x for x in (d.pop("booker_first", None),
                                                           d.pop("booker_surname", None)) if x).strip() or None
                d["coach_name"] = (d.pop("coach_display", None)
                                   or " ".join(x for x in (d.pop("coach_first", None),
                                                           d.pop("coach_surname", None)) if x).strip() or None)
                d["held_for_lesson"] = (d.get("notes") == "(court held for lesson)")
                d.pop("notes", None)
                d["pay_label"] = _pay_label(d.get("settlement_mode"), d.pop("order_status", None))
                for k in ("id", "resource_id", "coach_user_id", "booked_by_user_id", "order_id"):
                    if d.get(k) is not None:
                        d[k] = str(d[k])
                for k in ("starts_at", "ends_at"):
                    if d.get(k) is not None:
                        d[k] = d[k].isoformat()
                out.append(d)
            # Class sessions on the same calendar (docs/03 §1). GUARDED: a class-events failure
            # must not 500 the whole master diary — show the bookings regardless.
            try:
                out.extend(classes_mod.master_class_events(
                    s, club_id=p.club_id, date_from=q.get("date_from"), date_to=q.get("date_to")))
            except Exception:
                log.exception("master diary: class_events failed (showing bookings only)")
    except Exception as e:
        # Surface the real reason (logged to Render + returned in detail) so a 500 is diagnosable.
        log.exception("master diary failed club=%s", p.club_id)
        return jsonify(error="master_failed",
                       message=("Master diary error — %s: %s" % (type(e).__name__, e))[:400]), 500
    out.sort(key=lambda e: e.get("starts_at") or "")
    return jsonify(events=out, count=len(out)), 200


# ---------------------------------------------------------------------------
# cron endpoints (OPS_KEY-guarded; the thin trigger POSTs here)
# ---------------------------------------------------------------------------

def _ops_only():
    """Cron endpoints accept ONLY the server-to-server OPS principal (never a client)."""
    p = resolve_principal(request)
    return p is not None and p.method == "ops"


@cron_bp.post("/reminders")
def cron_reminders():
    if not _ops_only():
        return jsonify(error="forbidden"), 403
    from db import get_engine
    return jsonify(crons_mod.run_reminders(get_engine())), 200


@cron_bp.post("/capacity-sweep")
def cron_capacity_sweep():
    if not _ops_only():
        return jsonify(error="forbidden"), 403
    from db import get_engine
    return jsonify(crons_mod.run_capacity_sweep(get_engine())), 200


@cron_bp.post("/membership-refill")
def cron_membership_refill():
    if not _ops_only():
        return jsonify(error="forbidden"), 403
    from db import get_engine
    return jsonify(crons_mod.run_membership_refill(get_engine())), 200


@cron_bp.post("/db-fingerprint")
def cron_db_fingerprint():
    """OPS-only read-only census of the DB the app is ACTUALLY using — confirms the migrated data
    survived a region/DB recreate. Reports the club + the counts the migration created + a probe of
    a few known imported emails."""
    if not _ops_only():
        return jsonify(error="forbidden"), 403
    from db import session_scope
    from sqlalchemy import text
    out = {}
    with session_scope() as s:
        def q(sql, **p):
            try:
                return int(s.execute(text(sql), p).scalar() or 0)
            except Exception:
                return None
        club = s.execute(text("SELECT id, slug FROM club.club "
                              "WHERE COALESCE(is_template,false)=false ORDER BY created_at LIMIT 1")
                         ).mappings().first()
        out["club"] = {"id": str(club["id"]), "slug": club["slug"]} if club else None
        out["users_total"] = q("SELECT count(*) FROM iam.user")
        out["users_with_email"] = q("SELECT count(*) FROM iam.user WHERE email IS NOT NULL")
        out["members_active"] = q("SELECT count(*) FROM iam.membership "
                                  "WHERE role='member' AND member_status='active'")
        out["membership_subs_active"] = q("SELECT count(*) FROM billing.membership_subscription "
                                          "WHERE status='active'")
        out["membership_subs_manual"] = q("SELECT count(*) FROM billing.membership_subscription "
                                          "WHERE status='active' AND provider='manual'")
        out["lesson_wallets_active"] = q("SELECT count(*) FROM billing.token_wallet "
                                         "WHERE service_kind='lesson' AND status='active'")
        out["trial_subs"] = q("SELECT count(*) FROM billing.membership_subscription "
                              "WHERE provider='trial'")
        found = []
        for e in ("aseedat6763@gmail.com", "gila.tobias@gmail.com", "ejoylee1979@gmail.com",
                  "simonnebugai@gmail.com", "rmjacq@gmail.com"):
            if s.execute(text("SELECT 1 FROM iam.user WHERE lower(email)=:e LIMIT 1"), {"e": e}).first():
                found.append(e)
        out["sample_imported_emails_found"] = found
    return jsonify(out), 200


@cron_bp.post("/ses-suppress")
def cron_ses_suppress():
    """OPS-only: check (and optionally clear) an address on SES's account suppression list — via API,
    no AWS console needed. ?email=<addr>&action=check|delete. A suppressed address gets sends ACCEPTED
    but silently dropped (and each send logs a bounce), which is why 'send_ok but nothing arrives'."""
    if not _ops_only():
        return jsonify(error="forbidden"), 403
    from marketing_crm.email import ses
    body = request.get_json(silent=True) or {}
    email = (request.args.get("email") or body.get("email") or "").strip()
    action = (request.args.get("action") or "check").strip().lower()
    if not email:
        return jsonify(error="email required"), 400
    out = {"email": email, "action": action}
    try:
        import boto3
        v2 = boto3.client("sesv2", region_name=ses._region(), **ses._ses_creds())
        try:
            rec = v2.get_suppressed_destination(EmailAddress=email).get("SuppressedDestination", {})
            out["suppressed"] = True
            out["reason"] = rec.get("Reason")
            out["last_update"] = str(rec.get("LastUpdateTime"))
        except Exception as e:
            if "NotFound" in type(e).__name__ or "NotFound" in str(e):
                out["suppressed"] = False
            else:
                out["error_check"] = "%s: %s" % (type(e).__name__, e)
        if action == "delete" and out.get("suppressed"):
            v2.delete_suppressed_destination(EmailAddress=email)
            out["deleted"] = True
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
    return jsonify(out), 200


@cron_bp.post("/ses-account")
def cron_ses_account():
    """OPS-only SES ACCOUNT-STATE probe (read-only). Queries SES via the API — works even though the
    AWS console is locked. Reveals why SES accepts a send (send_ok) yet nothing delivers: an unhealthy
    EnforcementStatus (under review/paused), sending disabled, sandbox, over-quota, or a bounce/complaint
    spike. Guarded by OPS_KEY."""
    if not _ops_only():
        return jsonify(error="forbidden"), 403
    from marketing_crm.email import ses
    out = {"region": ses._region()}
    try:
        import boto3
        v2 = boto3.client("sesv2", region_name=ses._region(), **ses._ses_creds())
        acct = v2.get_account()
        out["sending_enabled"] = acct.get("SendingEnabled")
        out["production_access"] = acct.get("ProductionAccessEnabled")  # False = sandbox
        out["enforcement_status"] = acct.get("EnforcementStatus")       # want "HEALTHY"
        q = acct.get("SendQuota") or {}
        out["quota_max_24h"] = q.get("Max24HourSend")
        out["sent_last_24h"] = q.get("SentLast24Hours")
        out["max_send_rate"] = q.get("MaxSendRate")
        sup = acct.get("SuppressionAttributes") or {}
        out["suppressed_reasons"] = sup.get("SuppressedReasons")
    except Exception as e:
        out["error_get_account"] = "%s: %s" % (type(e).__name__, e)
    try:
        import boto3
        v1 = boto3.client("ses", region_name=ses._region(), **ses._ses_creds())
        pts = (v1.get_send_statistics().get("SendDataPoints") or [])
        pts.sort(key=lambda d: d.get("Timestamp") or "")
        out["recent_stats"] = [{"t": str(d.get("Timestamp")), "attempts": d.get("DeliveryAttempts"),
                                "bounces": d.get("Bounces"), "complaints": d.get("Complaints"),
                                "rejects": d.get("Rejects")} for d in pts[-6:]]
    except Exception as e:
        out["error_stats"] = "%s: %s" % (type(e).__name__, e)
    return jsonify(out), 200


@cron_bp.post("/ses-selftest")
def cron_ses_selftest():
    """OPS-only SES diagnostic. Reports the LIVE service's SES state and, with ?to=<email>, attempts
    a RAW send (bypassing the app's error-swallowing wrapper) so the exact boto3 error is returned —
    the fastest way to tell a wrong region / bad key / missing permission / sandbox apart. Sends
    nothing but a one-line test message; never touches the DB. Guarded by OPS_KEY."""
    if not _ops_only():
        return jsonify(error="forbidden"), 403
    from marketing_crm.email import ses
    to = (request.args.get("to")
          or (request.get_json(silent=True) or {}).get("to") or "").strip()
    out = {
        "enabled": ses.enabled(),
        "sender": ses._sender(),
        "region": ses._region(),
        "creds": "SES_AWS_* (own account)" if ses._ses_creds() else "default AWS_* chain",
    }
    try:
        import boto3  # noqa: F401
        out["boto3"] = True
    except Exception as e:
        out["boto3"] = False
        out["boto3_error"] = str(e)
    if to:
        # RAW send so the true error surfaces (the app path catches + returns False).
        try:
            import boto3
            client = boto3.client("ses", region_name=ses._region(), **ses._ses_creds())
            client.send_email(
                Source=ses._from_source("NextPoint Tennis"),
                Destination={"ToAddresses": [to]},
                Message={"Subject": {"Data": "CourtFlow SES self-test", "Charset": "UTF-8"},
                         "Body": {"Text": {"Data": "SES self-test OK.", "Charset": "UTF-8"}}},
            )
            out["send_ok"] = True
        except Exception as e:
            out["send_ok"] = False
            out["send_error"] = "%s: %s" % (type(e).__name__, e)
    return jsonify(out), 200

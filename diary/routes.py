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

from flask import Blueprint, jsonify, request

from auth import resolve_principal
from db import session_scope
from iam.permissions import can
from iam import repositories as iam_repo
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
        # A COURT booking by a member with an active membership is free — surface 0 on the
        # slots so the schedule step shows "R0 · covered". (Lessons/classes are never auto-covered.)
        covered = bool(kind == "court" and pricing_mod.has_active_membership(
            s, club_id=p.club_id, user_id=p.user_id))
        slots = availability_mod.compute_availability(
            s, club_id=p.club_id,
            resource_id=q.get("resource_id"), kind=kind,
            coach_user_id=q.get("coach_id"), surface=q.get("surface"),
            date_from=q.get("date_from"), date_to=q.get("date_to"),
            duration_minutes=q.get("duration", type=int),
            audience=audience,
            any_resource=(q.get("any") in ("1", "true", "yes")),
            membership_covered=covered,
        )
    return jsonify(slots=slots, count=len(slots)), 200


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
    with session_scope() as s:
        rows = pricing_mod.durations_for(
            s, club_id=p.club_id, kind=price_kind,
            coach_user_id=q.get("coach_id"), audience=audience)
        covered = bool(kind == "court" and pricing_mod.has_active_membership(
            s, club_id=p.club_id, user_id=p.user_id))
    currency = rows[0]["currency_code"] if rows else None
    out = [{"duration_minutes": r["duration_minutes"], "amount_minor": r["amount_minor"],
            "price_id": r["price_id"]} for r in rows]
    return jsonify(durations=out, membership_covered=covered, currency=currency), 200


@diary_bp.get("/resources")
def resources():
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    from sqlalchemy import text
    with session_scope() as s:
        rows = s.execute(
            text("SELECT id, kind, name, surface, coach_user_id, capacity, is_active, rank "
                 "FROM diary.resource WHERE club_id=:c AND is_active=true "
                 "ORDER BY kind, rank, name"),
            {"c": p.club_id},
        ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("id", "coach_user_id"):
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
    with session_scope() as s:
        res = bookings_mod.create_booking(
            s, club_id=p.club_id, booked_by_user_id=p.user_id, role=p.role,
            booking_type=b.get("booking_type", "court"),
            resource_id=b.get("resource_id"),
            starts_at=b.get("starts_at"), ends_at=b.get("ends_at"),
            settlement_mode=b.get("settlement_mode", "at_court"),
            parties=parties,
            coach_user_id=b.get("coach_user_id"),
            court_resource_id=b.get("court_resource_id"),
            audience=audience, notes=b.get("notes"),
            recurrence_id=b.get("recurrence_id"),
            booked_for_user_id=booked_for_user_id,
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


@diary_bp.post("/classes/<class_session_id>/enrol")
def enrol(class_session_id):
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    if not can(p, "book_class", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    b = _body()
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
            audience=b.get("audience", "member"), payer_user_id=payer_user)
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
    target_user = b.get("user_id") if p.role in ("club_admin", "platform_admin", "coach") else p.user_id
    target_user = target_user or p.user_id
    with session_scope() as s:
        res = classes_mod.cancel_enrolment(
            s, club_id=p.club_id, class_session_id=class_session_id, user_id=target_user,
            actor_user_id=p.user_id)
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

@diary_bp.get("/master")
def master_diary():
    p = _principal()
    if not p or not _need_club(p):
        return jsonify(error="unauthorized"), 401
    if not can(p, "view_master_diary", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    q = request.args
    from sqlalchemy import text
    out = []
    try:
        with session_scope() as s:
            rows = s.execute(
                text("SELECT b.id, b.booking_type, b.resource_id, r.name AS resource_name, "
                     "       r.kind, b.coach_user_id, b.starts_at, b.ends_at, b.status, "
                     "       b.booked_by_user_id, b.order_id, b.settlement_mode "
                     "FROM diary.booking b LEFT JOIN diary.resource r ON r.id=b.resource_id "
                     "WHERE b.club_id=:c AND b.status IN ('held','confirmed','completed','no_show') "
                     "  AND (:df IS NULL OR b.starts_at >= CAST(:df AS timestamptz)) "
                     "  AND (:dt IS NULL OR b.starts_at <= CAST(:dt AS timestamptz)) "
                     "ORDER BY b.starts_at"),
                {"c": p.club_id, "df": q.get("date_from"), "dt": q.get("date_to")},
            ).mappings().all()
            for r in rows:
                d = dict(r)
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
        return jsonify(error="master_failed", detail=("%s: %s" % (type(e).__name__, e))[:300]), 500
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

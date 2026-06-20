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
from diary import availability as availability_mod
from diary import bookings as bookings_mod
from diary import classes as classes_mod
from diary import crons as crons_mod

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
    with session_scope() as s:
        slots = availability_mod.compute_availability(
            s, club_id=p.club_id,
            resource_id=q.get("resource_id"), kind=q.get("kind"),
            coach_user_id=q.get("coach_id"), surface=q.get("surface"),
            date_from=q.get("date_from"), date_to=q.get("date_to"),
            duration_minutes=q.get("duration", type=int),
            audience=audience,
            any_resource=(q.get("any") in ("1", "true", "yes")),
        )
    return jsonify(slots=slots, count=len(slots)), 200


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
    with session_scope() as s:
        res = bookings_mod.create_booking(
            s, club_id=p.club_id, booked_by_user_id=p.user_id, role=p.role,
            booking_type=b.get("booking_type", "court"),
            resource_id=b.get("resource_id"),
            starts_at=b.get("starts_at"), ends_at=b.get("ends_at"),
            settlement_mode=b.get("settlement_mode", "at_court"),
            parties=b.get("parties") or [],
            coach_user_id=b.get("coach_user_id"),
            court_resource_id=b.get("court_resource_id"),
            audience=audience, notes=b.get("notes"),
            recurrence_id=b.get("recurrence_id"),
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
    with session_scope() as s:
        res = classes_mod.enrol(
            s, club_id=p.club_id, class_session_id=class_session_id, user_id=target_user,
            settlement_mode=b.get("settlement_mode", "at_court"),
            audience=b.get("audience", "member"))
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
    out = []
    for r in rows:
        d = dict(r)
        for k in ("id", "resource_id", "coach_user_id", "booked_by_user_id", "order_id"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        for k in ("starts_at", "ends_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        out.append(d)
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

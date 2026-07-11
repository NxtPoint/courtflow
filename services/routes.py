# services/routes.py — /api/services/* : the ONE API a service is created + edited through (owner + coach).
#
# The same endpoints serve both roles; the route enforces who may change what:
#   owner (club_admin/platform_admin) — everything, incl. commission; may CREATE a lesson FOR any
#                                        coach (POST "" with coach_user_id — 'create per coach').
#   coach                              — their OWN lesson/class service: create (for self), name,
#                                        variations, payment, packages. NEVER commission (owner-only).
# Writes delegate to the existing billing/admin repos so there's no duplicated logic — this lane just
# unifies the surface. Reads come from services.repositories.get_service (one composed payload).

import logging

from flask import Blueprint, jsonify, request

from auth import resolve_principal
from db import session_scope
from services import repositories as repo
from admin import repositories as admin_repo

log = logging.getLogger("services.routes")
services_bp = Blueprint("services", __name__, url_prefix="/api/services")


def _principal():
    p = resolve_principal(request)
    if p is None or not p.authenticated or p.club_id is None:
        return None
    return p


def _is_owner(p):
    return p.role in ("club_admin", "platform_admin")


def _can_manage(p, svc):
    """Owner manages any service; a coach manages only their OWN (lesson/class) service."""
    if _is_owner(p):
        return True
    if p.role == "coach":
        return svc.get("coach_user_id") is not None and str(svc["coach_user_id"]) == str(p.user_id)
    return False


def _body():
    return request.get_json(silent=True) or {}


@services_bp.get("")
def list_services():
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    with session_scope() as s:
        rows = repo.list_services(s, club_id=p.club_id, role=p.role, user_id=p.user_id)
    return jsonify(services=rows, count=len(rows)), 200


@services_bp.post("")
def create_service():
    """Create a NEW lesson service. The OWNER may create it FOR A CHOSEN COACH (body.coach_user_id,
    required — 'create per coach'); a COACH creates it for themselves. Delegates to
    coach.repositories.create_service so the product is owned by the target coach (coach_user_id) —
    ONE create path, no duplicated logic. Classes/courts have their own flows (class scheduler /
    court resources), so only 'lesson' is accepted here."""
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    b = _body()
    kind = (b.get("service_kind") or "lesson").strip()
    if kind != "lesson":
        return jsonify(error="ONLY_LESSON"), 400   # classes → scheduler, courts → resources
    name = (b.get("name") or "").strip()
    if not name:
        return jsonify(error="name required"), 400

    # Resolve the target coach: owner picks one; a coach is always themselves.
    if _is_owner(p):
        target = (str(b.get("coach_user_id") or "").strip()) or None
        if not target:
            return jsonify(error="coach_user_id required"), 400
    elif p.role == "coach":
        target = str(p.user_id)
    else:
        return jsonify(error="forbidden"), 403

    from coach import repositories as coach_repo
    with session_scope() as s:
        # Defence in depth: the target MUST be a coach in this club before we provision under them.
        if _is_owner(p) and not admin_repo.is_club_coach(s, club_id=p.club_id, user_id=target):
            return jsonify(error="NOT_A_COACH"), 400
        svc = coach_repo.create_service(
            s, club_id=p.club_id, user_id=target,
            name=name, duration_minutes=b.get("duration_minutes"),
            amount_minor=int(b.get("amount_minor") or 0),
        )
    return jsonify(service=svc), 201


@services_bp.get("/<product_id>")
def get_service(product_id):
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    with session_scope() as s:
        svc = repo.get_service(s, club_id=p.club_id, product_id=product_id)
        if not svc:
            return jsonify(error="NOT_FOUND"), 404
        if not _can_manage(p, svc):
            return jsonify(error="forbidden"), 403
        svc["can_edit_commission"] = _is_owner(p)   # the coach sees commission greyed
    return jsonify(service=svc), 200


def _load_manageable(s, p, product_id):
    """Load a service + check the caller may manage it. Returns (svc, error_response)."""
    svc = repo.get_service(s, club_id=p.club_id, product_id=product_id)
    if not svc:
        return None, (jsonify(error="NOT_FOUND"), 404)
    if not _can_manage(p, svc):
        return None, (jsonify(error="forbidden"), 403)
    return svc, None


@services_bp.patch("/<product_id>")
def patch_service(product_id):
    """Service-level edit: name, description, payment preference (any manager); commission % (OWNER
    only — silently ignored for a coach)."""
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    b = _body()
    with session_scope() as s:
        svc, err = _load_manageable(s, p, product_id)
        if err:
            return err
        if "name" in b or "description" in b:
            admin_repo.patch_product(s, club_id=p.club_id, product_id=product_id,
                                     name=b.get("name"), description=b.get("description"))
        if "active" in b:   # legacy: map active boolean to the lifecycle status
            repo.set_service_status(s, club_id=p.club_id, product_id=product_id,
                                    status="active" if b.get("active") else "deactivated")
        if "status" in b:   # lifecycle: active | deactivated | terminated
            repo.set_service_status(s, club_id=p.club_id, product_id=product_id, status=b.get("status"))
        if "payment_modes" in b:
            repo.set_payment_modes(s, club_id=p.club_id, product_id=product_id,
                                   modes=b.get("payment_modes"))
        # Court-SERVICE membership eligibility (owner only) — false = a PAYG-only court (e.g. clay), never
        # free for members. Only meaningful for a court service; harmless on others.
        if "members_covered" in b and _is_owner(p):
            admin_repo.set_members_covered(s, club_id=p.club_id, product_id=product_id,
                                           members_covered=bool(b.get("members_covered")))
        # Commission — OWNER ONLY. A coach's request to change it is ignored (defence in depth: the
        # UI greys it out, and the API refuses it here).
        if "commission_pct" in b and _is_owner(p):
            pct = b.get("commission_pct")
            try:
                pct = max(0, min(100, float(pct)))
            except (TypeError, ValueError):
                pct = None
            if pct is not None:
                admin_repo.set_commission_rule(
                    s, club_id=p.club_id, product_id=product_id,
                    coach_user_id=svc.get("coach_user_id"), commission_pct=pct)
        out = repo.get_service(s, club_id=p.club_id, product_id=product_id)
        out["can_edit_commission"] = _is_owner(p)
    return jsonify(service=out), 200


# ---- variations (per-duration prices) -------------------------------------
@services_bp.post("/<product_id>/variations")
def add_variation(product_id):
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    b = _body()
    dur = b.get("duration_minutes")
    if not dur or int(dur) < 1:
        return jsonify(error="duration_minutes required"), 400
    with session_scope() as s:
        svc, err = _load_manageable(s, p, product_id)
        if err:
            return err
        _peak = b.get("peak_amount_minor")
        admin_repo.create_price(s, club_id=p.club_id, product_id=product_id,
                                amount_minor=int(b.get("amount_minor") or 0),
                                duration_minutes=int(dur),
                                peak_amount_minor=(int(_peak) if _peak not in (None, "") else None))
        out = repo.get_service(s, club_id=p.club_id, product_id=product_id)
    return jsonify(service=out), 201


@services_bp.patch("/<product_id>/variations/<price_id>")
def patch_variation(product_id, price_id):
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    b = _body()
    with session_scope() as s:
        svc, err = _load_manageable(s, p, product_id)
        if err:
            return err
        # peak_amount_minor passed ONLY when present (None clears it, sentinel = leave unchanged).
        _peak_kw = {}
        if "peak_amount_minor" in b:
            _pv = b.get("peak_amount_minor")
            _peak_kw["peak_amount_minor"] = (int(_pv) if _pv not in (None, "") else None)
        admin_repo.patch_price(s, club_id=p.club_id, price_id=price_id,
                               amount_minor=b.get("amount_minor"),
                               duration_minutes=b.get("duration_minutes"), status=b.get("status"),
                               **_peak_kw)
        out = repo.get_service(s, club_id=p.club_id, product_id=product_id)
    return jsonify(service=out), 200


@services_bp.delete("/<product_id>/variations/<price_id>")
def delete_variation(product_id, price_id):
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    with session_scope() as s:
        svc, err = _load_manageable(s, p, product_id)
        if err:
            return err
        admin_repo.patch_price(s, club_id=p.club_id, price_id=price_id, status="retired")
        out = repo.get_service(s, club_id=p.club_id, product_id=product_id)
    return jsonify(service=out), 200


# ---- packages (bundle plans) ----------------------------------------------
@services_bp.post("/<product_id>/packages")
def add_package(product_id):
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    b = _body()
    with session_scope() as s:
        svc, err = _load_manageable(s, p, product_id)
        if err:
            return err
        from billing import bundles
        # PER-SERVICE: tie the pack to THIS exact service (product_id) so it only draws for it — the
        # product is authoritative and derives service_kind + coach (owner rule: lesson/class packs
        # belong to their coach; court packs are coachless).
        bundles.create_plan(s, club_id=p.club_id, product_id=svc["id"],
                            sessions_count=int(b.get("sessions_count") or 1),
                            price_minor=int(b.get("price_minor") or 0),
                            label=b.get("label"), duration_minutes=b.get("duration_minutes"),
                            validity_days=b.get("validity_days"))
        out = repo.get_service(s, club_id=p.club_id, product_id=product_id)
    return jsonify(service=out), 201


@services_bp.patch("/<product_id>/packages/<plan_id>")
def patch_package(product_id, plan_id):
    p = _principal()
    if not p:
        return jsonify(error="unauthorized"), 401
    b = _body()
    with session_scope() as s:
        svc, err = _load_manageable(s, p, product_id)
        if err:
            return err
        from billing import bundles
        # Explicit "assign this legacy pack to THIS service" — scopes an unscoped (product_id NULL)
        # pack to `svc` so it stops cross-showing under the coach's other same-kind services. Guarded
        # to product_id IS NULL, so it can never steal a pack that already belongs elsewhere.
        if b.get("adopt"):
            bundles.assign_plan_product(s, club_id=p.club_id, plan_id=plan_id, product_id=svc["id"])
        if "status" in b:
            bundles.set_plan_status(s, club_id=p.club_id, plan_id=plan_id, status=b.get("status"))
        elif any(k in b for k in ("label", "sessions_count", "duration_minutes", "price_minor", "validity_days")):
            bundles.update_plan(s, club_id=p.club_id, plan_id=plan_id, label=b.get("label"),
                                sessions_count=b.get("sessions_count"),
                                duration_minutes=b.get("duration_minutes"),
                                price_minor=b.get("price_minor"), validity_days=b.get("validity_days"))
        out = repo.get_service(s, club_id=p.club_id, product_id=product_id)
    return jsonify(service=out), 200

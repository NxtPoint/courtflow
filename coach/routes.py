# coach/routes.py — the /api/coach/* write surface (coach self-service: onboarding,
# profile, weekly hours, lesson services/rates). Blueprint coach_bp. Registered in app.py.
#
# Thin routes (admin/diary/billing style): resolve the principal (auth.resolve_principal),
# gate to roles coach + club_admin + platform_admin (reject others 403), and pull BOTH
# user_id AND club_id FROM THE PRINCIPAL (never the body — docs/02 §1). A coach only ever
# sees/edits THEIR OWN profile/resource/services — every repository call is scoped by
# principal.user_id, so a coach can never touch another coach's data. Plain SQL via the
# repositories; multi-tenant club_id-scoped throughout.
#
# Imports stay lazy where practical (boto3 in photo-presign is guarded so the lane never
# hard-depends on S3). The repositories import is module-level (pure SQL, no DB at import).

import logging
import os

from flask import Blueprint, jsonify, request

from auth import resolve_principal
from db import session_scope
from coach import repositories as repo

log = logging.getLogger("coach.routes")

coach_bp = Blueprint("coach", __name__, url_prefix="/api/coach")

# Roles allowed to use the coach console. A coach acts on their own data; club_admin /
# platform_admin are allowed in too (they may manage their own coach_profile / act as a
# coach), but the repositories scope every read/write to principal.user_id regardless.
_COACH_ROLES = ("coach", "club_admin", "platform_admin")


# ---------------------------------------------------------------------------
# auth helper
# ---------------------------------------------------------------------------

def _coach():
    """Resolve an authenticated coach principal with a resolved club + user, or None.
    Returns (principal, error_response) — error_response is a (json, status) tuple or None."""
    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return None, (jsonify(error="unauthorized"), 401)
    if p.role not in _COACH_ROLES:
        return None, (jsonify(error="forbidden"), 403)
    if p.club_id is None:
        return None, (jsonify(error="no_club_scope"), 400)
    if p.user_id is None:
        # An OPS principal carries no user_id — there is no "own" coach data to act on.
        return None, (jsonify(error="no_user_scope"), 400)
    return p, None


def _body():
    return request.get_json(silent=True) or {}


# ---------------------------------------------------------------------------
# onboarding
# ---------------------------------------------------------------------------

@coach_bp.get("/onboarding")
def get_onboarding():
    p, err = _coach()
    if err:
        return err
    with session_scope() as s:
        repo.ensure_profile(s, club_id=p.club_id, user_id=p.user_id)
        profile = repo.get_profile(s, club_id=p.club_id, user_id=p.user_id) or {}
        steps = repo.onboarding_steps(s, club_id=p.club_id, user_id=p.user_id)
        hours = repo.hours_week(s, club_id=p.club_id, user_id=p.user_id)
        services = repo.list_services(s, club_id=p.club_id, user_id=p.user_id)
        completed = bool(profile.get("onboarding_completed"))
    return jsonify(
        completed=completed,
        steps=steps,
        profile={
            "display_name": profile.get("display_name"),
            "headline": profile.get("headline"),
            "bio": profile.get("bio"),
            "photo_url": profile.get("photo_url"),
            "specialties": profile.get("specialties") or [],
            "phone": profile.get("phone"),
            "first_name": profile.get("first_name"),
            "surname": profile.get("surname"),
            "email": profile.get("email"),
        },
        hours=hours,
        services=services,
    ), 200


@coach_bp.post("/onboarding/complete")
def complete_onboarding():
    p, err = _coach()
    if err:
        return err
    with session_scope() as s:
        repo.set_onboarding_completed(s, club_id=p.club_id, user_id=p.user_id, completed=True)
    return jsonify(ok=True), 200


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------

@coach_bp.get("/profile")
def get_profile():
    p, err = _coach()
    if err:
        return err
    with session_scope() as s:
        repo.ensure_profile(s, club_id=p.club_id, user_id=p.user_id)
        profile = repo.get_profile(s, club_id=p.club_id, user_id=p.user_id)
    return jsonify(profile=profile), 200


@coach_bp.patch("/profile")
def patch_profile():
    p, err = _coach()
    if err:
        return err
    b = _body()
    specialties = b.get("specialties")
    if specialties is not None and not isinstance(specialties, list):
        return jsonify(error="specialties must be a list"), 400
    with session_scope() as s:
        profile = repo.patch_profile(
            s, club_id=p.club_id, user_id=p.user_id,
            display_name=b.get("display_name"), headline=b.get("headline"),
            bio=b.get("bio"), photo_url=b.get("photo_url"),
            specialties=specialties, phone=b.get("phone"),
            first_name=b.get("first_name"), surname=b.get("surname"),
        )
    return jsonify(profile=profile), 200


# ---------------------------------------------------------------------------
# weekly working hours (the coach's own diary.resource)
# ---------------------------------------------------------------------------

@coach_bp.put("/hours")
def put_hours():
    p, err = _coach()
    if err:
        return err
    b = _body()
    week = b.get("week") or []
    with session_scope() as s:
        profile = repo.get_profile(s, club_id=p.club_id, user_id=p.user_id) or {}
        resource_id, inserted = repo.replace_hours(
            s, club_id=p.club_id, user_id=p.user_id, week=week,
            display_name=profile.get("display_name"),
        )
        rules = repo.list_hours(s, club_id=p.club_id, resource_id=resource_id)
    return jsonify(ok=True, resource_id=resource_id, rules_written=inserted, rules=rules), 200


# ---------------------------------------------------------------------------
# lesson services / rates (billing.product kind='lesson' + price), per coach
# ---------------------------------------------------------------------------

@coach_bp.get("/services")
def get_services():
    p, err = _coach()
    if err:
        return err
    with session_scope() as s:
        rows = repo.list_services(s, club_id=p.club_id, user_id=p.user_id)
    return jsonify(services=rows, count=len(rows)), 200


@coach_bp.post("/services")
def post_service():
    p, err = _coach()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        svc = repo.create_service(
            s, club_id=p.club_id, user_id=p.user_id,
            name=b.get("name"), duration_minutes=b.get("duration_minutes"),
            amount_minor=b.get("amount_minor", 0), audience=b.get("audience", "any"),
            unit=b.get("unit", "per_hour"),
        )
    return jsonify(service=svc), 201


@coach_bp.patch("/services/<price_id>")
def patch_service(price_id):
    p, err = _coach()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        svc = repo.patch_service(
            s, club_id=p.club_id, user_id=p.user_id, price_id=price_id,
            name=b.get("name"), amount_minor=b.get("amount_minor"),
        )
    if svc is None:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(service=svc), 200


@coach_bp.delete("/services/<price_id>")
def delete_service(price_id):
    p, err = _coach()
    if err:
        return err
    with session_scope() as s:
        ok = repo.deactivate_service(s, club_id=p.club_id, user_id=p.user_id, price_id=price_id)
    if not ok:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(ok=True), 200


# ---------------------------------------------------------------------------
# photo upload presign (S3 if configured; else tell the frontend to fall back)
# ---------------------------------------------------------------------------

def _s3_configured():
    return bool(
        os.getenv("S3_BUCKET")
        and (os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_PROFILE")
             or os.getenv("AWS_ROLE_ARN") or os.getenv("AWS_WEB_IDENTITY_TOKEN_FILE"))
    )


@coach_bp.post("/photo-presign")
def photo_presign():
    """Return a presigned S3 PUT for the coach to upload a profile photo, IF S3 is configured
    (env S3_BUCKET + AWS creds present). Otherwise return {configured:false} so the frontend
    falls back to a photo-URL paste. boto3 import is guarded — the lane never hard-requires S3."""
    p, err = _coach()
    if err:
        return err
    if not _s3_configured():
        return jsonify(configured=False), 200
    b = _body()
    filename = (b.get("filename") or "").strip() or "photo"
    content_type = (b.get("content_type") or "application/octet-stream").strip()
    try:
        import boto3  # guarded: optional dependency
    except Exception:
        log.debug("boto3 unavailable — photo presign falls back to unconfigured")
        return jsonify(configured=False), 200

    bucket = os.getenv("S3_BUCKET")
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    # Namespace the object per club + coach so uploads can't collide / leak across coaches.
    safe_name = filename.replace("/", "_").replace("\\", "_")
    key = f"coach-photos/{p.club_id}/{p.user_id}/{safe_name}"
    try:
        s3 = boto3.client("s3", region_name=region) if region else boto3.client("s3")
        url = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=900,
        )
    except Exception:
        log.exception("photo presign failed")
        return jsonify(configured=False), 200

    public_base = os.getenv("S3_PUBLIC_BASE_URL")
    if public_base:
        public_url = f"{public_base.rstrip('/')}/{key}"
    elif region and region != "us-east-1":
        public_url = f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
    else:
        public_url = f"https://{bucket}.s3.amazonaws.com/{key}"
    return jsonify(configured=True, url=url, public_url=public_url, key=key), 200

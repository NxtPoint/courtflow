# admin/routes.py — the /api/admin/* write surface (owner self-service: onboarding +
# club settings). Blueprint admin_bp. Registered in app.py.
#
# Thin routes (diary/billing style): resolve the principal (auth.resolve_principal), gate
# to roles club_admin + platform_admin (reject others 403), pull club_id FROM THE PRINCIPAL
# (never the body — docs/02 §1), call admin.repositories, map dicts to JSON. Plain SQL via
# the repositories; every query is club_id-scoped (multi-tenant).
#
# Imports that touch the DB stay lazy where practical so the module imports clean with no
# DATABASE_URL (app.py boot discipline). The repositories import is module-level (pure SQL,
# no DB at import time).

import logging
import secrets

from flask import Blueprint, jsonify, request

from auth import resolve_principal
from db import session_scope
from admin import repositories as repo
from diary import classes as classes_mod

log = logging.getLogger("admin.routes")

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")

# Roles allowed to use the admin console.
_ADMIN_ROLES = ("club_admin", "platform_admin")


# ---------------------------------------------------------------------------
# auth helpers
# ---------------------------------------------------------------------------

def _admin():
    """Resolve an authenticated admin principal with a resolved club, or None.
    Returns (principal, error_response) — error_response is a (json, status) tuple or None."""
    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return None, (jsonify(error="unauthorized"), 401)
    if p.role not in _ADMIN_ROLES:
        return None, (jsonify(error="forbidden"), 403)
    if p.club_id is None:
        # A club-scoped admin action needs a resolved club (platform_admin passes one via
        # X-Club, already resolved into p.club_id by the principal resolver).
        return None, (jsonify(error="no_club_scope"), 400)
    return p, None


def _body():
    return request.get_json(silent=True) or {}


def _class_result(res):
    """Map a diary.classes logic dict {ok, status, error, ...} to (json, status)."""
    if res is None:
        return jsonify(error="NOT_FOUND"), 404
    if res.get("ok"):
        return jsonify({k: v for k, v in res.items() if k != "ok"}), 200
    return jsonify(error=res.get("error"),
                   **{k: v for k, v in res.items()
                      if k not in ("ok", "status", "error")}), res.get("status", 400)


# ---------------------------------------------------------------------------
# onboarding
# ---------------------------------------------------------------------------

@admin_bp.get("/onboarding")
def get_onboarding():
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        club = repo.get_club(s, club_id=p.club_id)
        location = repo.get_primary_location(s, club_id=p.club_id)
        branding = repo.get_branding(s, club_id=p.club_id)
        policy = repo.get_policy(s, club_id=p.club_id)
        steps, counts = repo.onboarding_counts_and_steps(s, club_id=p.club_id)
        hours = repo.hours_week(s, club_id=p.club_id)
    completed = bool(club and club.get("onboarding_completed"))
    return jsonify(
        completed=completed,
        steps=steps,
        club=club,
        location=location,
        branding=branding,
        policy=policy,
        counts=counts,
        hours=hours,
    ), 200


@admin_bp.post("/onboarding/complete")
def complete_onboarding():
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        repo.set_onboarding_completed(s, club_id=p.club_id, completed=True)
    return jsonify(ok=True), 200


# ---------------------------------------------------------------------------
# club / location / branding / policy
# ---------------------------------------------------------------------------

@admin_bp.get("/club")
def get_club():
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        return jsonify(
            club=repo.get_club(s, club_id=p.club_id),
            location=repo.get_primary_location(s, club_id=p.club_id),
            branding=repo.get_branding(s, club_id=p.club_id),
            policy=repo.get_policy(s, club_id=p.club_id),
        ), 200


@admin_bp.patch("/club")
def patch_club():
    p, err = _admin()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        club = repo.patch_club(
            s, club_id=p.club_id,
            name=b.get("name"), legal_name=b.get("legal_name"),
            currency_code=b.get("currency_code"), timezone=b.get("timezone"),
            locale=b.get("locale"),
        )
    return jsonify(club=club), 200


@admin_bp.put("/location")
def put_location():
    p, err = _admin()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        location = repo.upsert_primary_location(
            s, club_id=p.club_id,
            name=b.get("name"), address_line=b.get("address_line"), city=b.get("city"),
            postal_code=b.get("postal_code"), country=b.get("country"),
            phone=b.get("phone"), email=b.get("email"),
            lat=b.get("lat"), lng=b.get("lng"),
        )
    return jsonify(location=location), 200


@admin_bp.patch("/branding")
def patch_branding():
    p, err = _admin()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        branding = repo.patch_branding(
            s, club_id=p.club_id,
            primary_color=b.get("primary_color"), accent_color=b.get("accent_color"),
            logo_url=b.get("logo_url"), favicon_url=b.get("favicon_url"),
            og_image_url=b.get("og_image_url"),
        )
    return jsonify(branding=branding), 200


@admin_bp.patch("/policy")
def patch_policy():
    p, err = _admin()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        policy = repo.patch_policy(
            s, club_id=p.club_id,
            booking_window_days=b.get("booking_window_days"),
            min_booking_minutes=b.get("min_booking_minutes"),
            cancellation_cutoff_hours=b.get("cancellation_cutoff_hours"),
            no_show_fee_minor=b.get("no_show_fee_minor"),
            guest_requires_member=b.get("guest_requires_member"),
            allow_pay_at_court=b.get("allow_pay_at_court"),
            allow_monthly_account=b.get("allow_monthly_account"),
            allow_online_payment=b.get("allow_online_payment"),
        )
    return jsonify(policy=policy), 200


# ---------------------------------------------------------------------------
# resources (courts / coaches / classes)
# ---------------------------------------------------------------------------

@admin_bp.get("/resources")
def get_resources():
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        rows = repo.list_resources(s, club_id=p.club_id)
    return jsonify(resources=rows, count=len(rows)), 200


@admin_bp.post("/resources")
def post_resource():
    p, err = _admin()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        res = repo.create_resource(
            s, club_id=p.club_id,
            kind=b.get("kind", "court"), name=b.get("name"),
            surface=b.get("surface"), capacity=b.get("capacity"),
            coach_user_id=b.get("coach_user_id"), rank=b.get("rank"),
        )
    return jsonify(resource=res), 201


@admin_bp.patch("/resources/<resource_id>")
def patch_resource(resource_id):
    p, err = _admin()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        res = repo.patch_resource(
            s, club_id=p.club_id, resource_id=resource_id,
            name=b.get("name"), surface=b.get("surface"),
            is_active=b.get("is_active"), rank=b.get("rank"), capacity=b.get("capacity"),
        )
    if res is None:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(resource=res), 200


@admin_bp.delete("/resources/<resource_id>")
def delete_resource(resource_id):
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        ok = repo.soft_delete_resource(s, club_id=p.club_id, resource_id=resource_id)
    if not ok:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(ok=True), 200


# ---------------------------------------------------------------------------
# hours (availability_rule)
# ---------------------------------------------------------------------------

@admin_bp.get("/hours")
def get_hours():
    p, err = _admin()
    if err:
        return err
    resource_id = (request.args.get("resource_id") or "").strip() or None
    with session_scope() as s:
        rows = repo.list_hours(s, club_id=p.club_id, resource_id=resource_id)
    return jsonify(hours=rows, count=len(rows)), 200


@admin_bp.put("/hours")
def put_hours():
    p, err = _admin()
    if err:
        return err
    b = _body()
    scope = b.get("scope")
    week = b.get("week") or []
    with session_scope() as s:
        if scope == "all_courts" or scope is None:
            resource_ids = repo.court_resource_ids(s, club_id=p.club_id)
        else:
            # scope is a specific resource_id — validate it belongs to this club.
            res = repo.get_resource(s, club_id=p.club_id, resource_id=scope)
            if res is None:
                return jsonify(error="RESOURCE_NOT_FOUND"), 404
            resource_ids = [scope]
        inserted = repo.replace_hours(s, club_id=p.club_id, resource_ids=resource_ids, week=week)
    return jsonify(ok=True, resources=len(resource_ids), rules_written=inserted), 200


# ---------------------------------------------------------------------------
# products + prices
# ---------------------------------------------------------------------------

@admin_bp.get("/products")
def get_products():
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        rows = repo.list_products(s, club_id=p.club_id)
    return jsonify(products=rows, count=len(rows)), 200


@admin_bp.post("/products")
def post_product():
    p, err = _admin()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        product = repo.create_product(
            s, club_id=p.club_id,
            kind=b.get("kind"), name=b.get("name"),
            description=b.get("description"), prices=b.get("prices") or [],
        )
    return jsonify(product=product), 201


@admin_bp.patch("/products/<product_id>")
def patch_product(product_id):
    p, err = _admin()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        product = repo.patch_product(
            s, club_id=p.club_id, product_id=product_id,
            kind=b.get("kind"), name=b.get("name"),
            description=b.get("description"), active=b.get("active"),
        )
    if product is None:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(product=product), 200


@admin_bp.post("/prices")
def post_price():
    p, err = _admin()
    if err:
        return err
    b = _body()
    product_id = b.get("product_id")
    if not product_id:
        return jsonify(error="product_id required"), 400
    with session_scope() as s:
        price = repo.create_price(
            s, club_id=p.club_id, product_id=product_id,
            audience=b.get("audience", "any"), amount_minor=b.get("amount_minor", 0),
            unit=b.get("unit", "per_booking"), duration_minutes=b.get("duration_minutes"),
        )
    if price is None:
        return jsonify(error="PRODUCT_NOT_FOUND"), 404
    return jsonify(price=price), 201


@admin_bp.patch("/prices/<price_id>")
def patch_price(price_id):
    p, err = _admin()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        price = repo.patch_price(
            s, club_id=p.club_id, price_id=price_id,
            audience=b.get("audience"), amount_minor=b.get("amount_minor"),
            unit=b.get("unit"), duration_minutes=b.get("duration_minutes"),
            active=b.get("active"),
        )
    if price is None:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(price=price), 200


@admin_bp.delete("/prices/<price_id>")
def delete_price(price_id):
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        ok = repo.deactivate_price(s, club_id=p.club_id, price_id=price_id)
    if not ok:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(ok=True), 200


# ---------------------------------------------------------------------------
# classes (class type = resource(kind='class') + product(kind='class') + price)
# ---------------------------------------------------------------------------

@admin_bp.get("/classes")
def get_classes():
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        rows = classes_mod.list_class_types(s, club_id=p.club_id)
    return jsonify(classes=rows, count=len(rows)), 200


@admin_bp.post("/classes")
def post_class():
    p, err = _admin()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        res = classes_mod.create_class_type(
            s, club_id=p.club_id, name=b.get("name"),
            coach_user_id=b.get("coach_user_id"), capacity=b.get("capacity"),
            price_amount_minor=b.get("price_amount_minor"),
            duration_minutes=b.get("duration_minutes"), description=b.get("description"))
    return _class_result(res)


@admin_bp.post("/classes/<resource_id>/schedule")
def post_class_schedule(resource_id):
    p, err = _admin()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        res = classes_mod.schedule_sessions(
            s, club_id=p.club_id, resource_id=resource_id,
            weekdays=b.get("weekdays"), start_time=b.get("start_time"),
            date_from=b.get("date_from"), date_until=b.get("date_until"),
            dates=b.get("dates"), duration_minutes=b.get("duration_minutes"),
            capacity=b.get("capacity"), price_id=b.get("price_id"))
    return _class_result(res)


@admin_bp.get("/classes/<resource_id>/sessions")
def get_class_sessions(resource_id):
    p, err = _admin()
    if err:
        return err
    q = request.args
    with session_scope() as s:
        rows = classes_mod.list_type_sessions(
            s, club_id=p.club_id, resource_id=resource_id,
            date_from=q.get("date_from"), date_to=q.get("date_to"))
    return jsonify(sessions=rows, count=len(rows)), 200


@admin_bp.post("/classes/sessions/<session_id>/cancel")
def post_class_session_cancel(session_id):
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        res = classes_mod.cancel_session(s, club_id=p.club_id, session_id=session_id)
    return _class_result(res)


# ---------------------------------------------------------------------------
# coaches + invite
# ---------------------------------------------------------------------------

@admin_bp.get("/coaches")
def get_coaches():
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        rows = repo.list_coaches(s, club_id=p.club_id)
    return jsonify(coaches=rows, count=len(rows)), 200


@admin_bp.get("/payments")
def get_payments():
    """Recent successful online payments for the admin Billing view (with refund status)."""
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        rows = repo.list_payments(s, club_id=p.club_id)
    return jsonify(payments=rows, count=len(rows)), 200


@admin_bp.get("/people")
def get_people():
    """Everyone in the club (members/coaches/guests/admins) for the admin People tab."""
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        rows = repo.list_people(s, club_id=p.club_id)
    return jsonify(people=rows, count=len(rows)), 200


@admin_bp.post("/members/<user_id>/membership")
def grant_membership(user_id):
    """Grant (or extend) a member's membership → their courts become free until it expires."""
    p, err = _admin()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        res = repo.grant_membership(s, club_id=p.club_id, user_id=user_id,
                                    months=b.get("months") or 1)
    return jsonify(res), 200


@admin_bp.delete("/members/<user_id>/membership")
def revoke_membership(user_id):
    """Cancel a member's active membership (courts revert to pay-as-you-go)."""
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        res = repo.revoke_membership(s, club_id=p.club_id, user_id=user_id)
    return jsonify(res), 200


def _send_coach_invite_email(*, to_email, club_id, display_name):
    """Best-effort: emit a coach_invited event + send an SES invite email. Guarded imports
    so the admin lane never hard-depends on marketing_crm being present/configured."""
    try:
        from marketing_crm.tracking import emit
        emit("coach_invited", {"club_id": str(club_id), "email": to_email})
    except Exception:
        log.debug("coach_invited emit skipped (tracking unavailable)")
    try:
        from marketing_crm.email import ses
        ses.send_email(
            to_email,
            "You've been invited to coach",
            f"Hi {display_name or ''},\n\nYou've been added as a coach. "
            f"Sign in to get started: log in at /login.\n",
        )
    except Exception:
        log.debug("coach invite email skipped (ses unavailable)")


@admin_bp.post("/coaches/invite")
def invite_coach():
    p, err = _admin()
    if err:
        return err
    b = _body()
    email = (b.get("email") or "").strip()
    if not email:
        return jsonify(error="email required"), 400
    display_name = b.get("display_name") or (
        f"{(b.get('first_name') or '').strip()} {(b.get('surname') or '').strip()}".strip()
    ) or None
    token = secrets.token_urlsafe(32)
    with session_scope() as s:
        user_id = repo.upsert_user_by_email(
            s, email=email, first_name=b.get("first_name"),
            surname=b.get("surname"), phone=b.get("phone"),
        )
        repo.upsert_coach_membership(s, club_id=p.club_id, user_id=user_id)
        repo.upsert_coach_profile(s, club_id=p.club_id, user_id=user_id,
                                  display_name=display_name)
        repo.create_coach_invite(s, club_id=p.club_id, user_id=user_id, token=token)
        coach = repo.get_coach(s, club_id=p.club_id, user_id=user_id)
    _send_coach_invite_email(to_email=email, club_id=p.club_id, display_name=display_name)
    return jsonify(coach=coach, invite_link="/login"), 201


@admin_bp.post("/coaches/<user_id>/resend-invite")
def resend_coach_invite(user_id):
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        coach = repo.get_coach(s, club_id=p.club_id, user_id=user_id)
        if coach is None:
            return jsonify(error="NOT_FOUND"), 404
        token = secrets.token_urlsafe(32)
        repo.create_coach_invite(s, club_id=p.club_id, user_id=user_id, token=token)
        email = coach.get("email")
        display_name = coach.get("display_name")
    if email:
        _send_coach_invite_email(to_email=email, club_id=p.club_id, display_name=display_name)
    return jsonify(coach=coach, invite_link="/login"), 200


@admin_bp.delete("/coaches/<user_id>")
def revoke_coach(user_id):
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        ok = repo.revoke_coach(s, club_id=p.club_id, user_id=user_id)
    if not ok:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(ok=True), 200

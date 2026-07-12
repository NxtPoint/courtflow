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
from sqlalchemy import text

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
    # Peak-window fields are passed ONLY when present in the body (a partial patch that touches something
    # else must not clear peak). When present, None is honoured = clear the window (peak pricing off).
    peak_kwargs = {}
    if any(k in b for k in ("peak_days", "peak_start_min", "peak_end_min")):
        peak_kwargs = {"peak_days": b.get("peak_days"), "peak_start_min": b.get("peak_start_min"),
                       "peak_end_min": b.get("peak_end_min")}
    with session_scope() as s:
        policy = repo.patch_policy(
            s, club_id=p.club_id, **peak_kwargs,
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
            product_id=b.get("product_id"),   # court SERVICE allocation (Hardcourt vs Clay)
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
            product_id=b.get("product_id"),   # re-allocate the court to a court SERVICE
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
        outcome = repo.delete_resource(s, club_id=p.club_id, resource_id=resource_id)
    if outcome is None:
        return jsonify(error="NOT_FOUND"), 404
    # 'deleted' = removed outright; 'archived' = had history, kept + hidden (is_active=false).
    return jsonify(ok=True, outcome=outcome), 200


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
            active=b.get("active"), status=b.get("status"),
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
# membership term plans (configurable: label + amount + duration). Each plan is a
# billing.price row (with term_months) on the club's kind='membership' product.
# ---------------------------------------------------------------------------

@admin_bp.get("/membership-plans")
def get_membership_plans():
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        plans = repo.list_membership_plans(s, club_id=p.club_id)
    return jsonify(plans=plans, count=len(plans)), 200


@admin_bp.post("/membership-plans")
def post_membership_plan():
    p, err = _admin()
    if err:
        return err
    b = _body()
    term_months = b.get("term_months")
    amount_minor = b.get("amount_minor")
    if term_months is None or int(term_months) < 1:
        return jsonify(error="term_months must be >= 1"), 400
    if amount_minor is None or int(amount_minor) < 0:
        return jsonify(error="amount_minor required"), 400
    with session_scope() as s:
        plan = repo.create_membership_plan(
            s, club_id=p.club_id, label=b.get("label"), tier=b.get("tier"),
            amount_minor=int(amount_minor), term_months=int(term_months),
            access_days=b.get("access_days"), access_start_min=b.get("access_start_min"),
            access_end_min=b.get("access_end_min"), payment_modes=b.get("payment_modes"),
            max_covered_minutes=b.get("max_covered_minutes"),
            max_covered_per_day=b.get("max_covered_per_day"),
            max_courts_per_day=b.get("max_courts_per_day"),
            is_trial=b.get("is_trial"), trial_days=b.get("trial_days"))
    return jsonify(plan=plan), 201


@admin_bp.patch("/membership-plans/<price_id>")
def patch_membership_plan(price_id):
    p, err = _admin()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        plan = repo.patch_membership_plan(
            s, club_id=p.club_id, price_id=price_id,
            label=b.get("label"), amount_minor=b.get("amount_minor"), tier=b.get("tier"),
            term_months=b.get("term_months"), active=b.get("active"), status=b.get("status"),
            set_window=bool(b.get("set_window")), access_days=b.get("access_days"),
            access_start_min=b.get("access_start_min"), access_end_min=b.get("access_end_min"),
            set_modes=bool(b.get("set_modes")), payment_modes=b.get("payment_modes"),
            set_limits=bool(b.get("set_limits")),
            max_covered_minutes=b.get("max_covered_minutes"),
            max_covered_per_day=b.get("max_covered_per_day"),
            max_courts_per_day=b.get("max_courts_per_day"),
            set_trial=bool(b.get("set_trial")), is_trial=b.get("is_trial"),
            trial_days=b.get("trial_days"))
    if plan is None:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(plan=plan), 200


@admin_bp.delete("/membership-plans/<price_id>")
def delete_membership_plan(price_id):
    """Deactivate (soft-delete) a term plan — it stops being offered but past purchases stand."""
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        ok = repo.deactivate_membership_plan(s, club_id=p.club_id, price_id=price_id)
    if not ok:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(ok=True), 200


# ---- equipment hire (ball machine / racquets / balls) — a flat-fee booking add-on ----
@admin_bp.get("/equipment")
def get_equipment():
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        items = repo.list_equipment(s, club_id=p.club_id)
    return jsonify(equipment=items, count=len(items)), 200


@admin_bp.post("/equipment")
def post_equipment():
    p, err = _admin()
    if err:
        return err
    b = _body()
    name = (b.get("name") or "").strip()
    if not name:
        return jsonify(error="name required"), 400
    with session_scope() as s:
        item = repo.create_equipment(
            s, club_id=p.club_id, name=name, amount_minor=int(b.get("amount_minor") or 0),
            quantity=int(b.get("quantity") or 1), feature_on_home=bool(b.get("feature_on_home")))
    return jsonify(equipment=item), 201


@admin_bp.patch("/equipment/<resource_id>")
def patch_equipment_route(resource_id):
    p, err = _admin()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        item = repo.patch_equipment(
            s, club_id=p.club_id, resource_id=resource_id, name=b.get("name"),
            amount_minor=b.get("amount_minor"), quantity=b.get("quantity"),
            feature_on_home=b.get("feature_on_home"), is_active=b.get("is_active"))
    if item is None:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(equipment=item), 200


@admin_bp.delete("/equipment/<resource_id>")
def delete_equipment(resource_id):
    """Soft-deactivate an equipment item (hidden from booking; kept for history/existing hires)."""
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        item = repo.patch_equipment(s, club_id=p.club_id, resource_id=resource_id, is_active=False)
    if item is None:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(ok=True), 200


@admin_bp.get("/membership-config")
def get_membership_config():
    """The membership product's payment preference + the club's enabled methods, for the editor.
    {payment_modes: [...]|null (null = inherit), club_payment_methods: [...]}."""
    p, err = _admin()
    if err:
        return err
    from billing import membership as membership_repo
    from services.repositories import club_payment_methods
    with session_scope() as s:
        modes = membership_repo.membership_payment_modes(s, club_id=p.club_id)
        enabled = club_payment_methods(s, club_id=p.club_id)
    return jsonify(payment_modes=modes, club_payment_methods=enabled), 200


@admin_bp.patch("/membership-config")
def patch_membership_config():
    """Set the membership product's payment preference. Body {payment_modes: [...]|null} — null/empty
    inherits the club's global methods."""
    p, err = _admin()
    if err:
        return err
    b = _body()
    modes = b.get("payment_modes", None)
    if modes is not None and not isinstance(modes, list):
        return jsonify(error="payment_modes must be a list or null"), 400
    from billing import membership as membership_repo
    with session_scope() as s:
        membership_repo.set_membership_payment_modes(s, club_id=p.club_id, modes=modes)
    return jsonify(ok=True), 200


# ---------------------------------------------------------------------------
# session-pack (token bundle) plans — the owner-configured prepaid packs (docs/specs/02).
# Each plan = a billing.bundle_plan row (service_kind, optional coach, label, #sessions, optional
# duration, price, optional validity). Generic across court/lesson/class; nothing hardcoded.
# Delegates to billing.bundles (the engine owns the CRUD); routes stay thin.
# ---------------------------------------------------------------------------

@admin_bp.get("/bundle-plans")
def get_bundle_plans():
    p, err = _admin()
    if err:
        return err
    from billing import bundles as bundles_repo
    service_kind = (request.args.get("service_kind") or "").strip() or None
    with session_scope() as s:
        plans = bundles_repo.list_plans(s, club_id=p.club_id, service_kind=service_kind,
                                        active_only=False)
    return jsonify(plans=plans, count=len(plans)), 200


# (POST/PATCH/DELETE /api/admin/bundle-plans REMOVED 2026-07-09 — a pack belongs to ONE specific
#  service and is created/edited ONLY under it via the services lane, POST/PATCH /api/services/
#  <product_id>/packages. GET stays above for the offline "issue a pack" picker. bundles.create_plan/
#  update_plan/deactivate_plan remain and are reached through the services lane.)


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
    # A class must belong to a coach (its enrolments + commission attribute to them).
    if not (b.get("coach_user_id") or "").strip():
        return jsonify(error="COACH_REQUIRED", message="A class must be assigned to a coach."), 400
    try:
        with session_scope() as s:
            res = classes_mod.create_class_type(
                s, club_id=p.club_id, name=b.get("name"),
                coach_user_id=b.get("coach_user_id"), capacity=b.get("capacity"),
                price_amount_minor=b.get("price_amount_minor"),
                duration_minutes=b.get("duration_minutes"), description=b.get("description"))
    except ValueError as e:
        return jsonify(error=str(e)), 400
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
            capacity=b.get("capacity"), price_id=b.get("price_id"),
            court_resource_id=b.get("court_resource_id"),
            court_resource_ids=b.get("court_resource_ids"))
    return _class_result(res)


@admin_bp.patch("/classes/<resource_id>")
def patch_class(resource_id):
    """Edit a class type (owner may set ANY coach). coach_user_id is required + validated as a real club
    coach. Changing coach/courts cascades to future sessions (see classes.update_class_type)."""
    p, err = _admin()
    if err:
        return err
    b = _body()
    coach_user_id = (b.get("coach_user_id") or "").strip()
    if not coach_user_id:
        return jsonify(error="COACH_REQUIRED", message="A class must be assigned to a coach."), 400
    with session_scope() as s:
        if not repo.is_club_coach(s, club_id=p.club_id, user_id=coach_user_id):
            return jsonify(error="COACH_NOT_IN_CLUB"), 400
        try:
            res = classes_mod.update_class_type(
                s, club_id=p.club_id, resource_id=resource_id, coach_user_id=coach_user_id,
                name=b.get("name"), capacity=b.get("capacity"), description=b.get("description"),
                court_resource_ids=b.get("court_resource_ids"),
                court_resource_id=b.get("court_resource_id"))
        except ValueError as e:
            return jsonify(error=str(e)), 400
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


@admin_bp.get("/refund-requests")
def get_refund_requests():
    """Read-only queue of client-raised refund requests for the admin Billing view (join order
    amount + requester email). Optional ?status= filter. Executing the actual refund stays on
    the existing Admin → Recent online payments → Refund (Yoco) path — this is just the queue."""
    p, err = _admin()
    if err:
        return err
    status = (request.args.get("status") or "").strip() or None
    from billing import refunds
    with session_scope() as s:
        rows = refunds.list_refund_requests_admin(s, club_id=p.club_id, status=status)
    return jsonify(requests=rows, count=len(rows)), 200


# Refund-request DECISION error code -> (HTTP status, admin-facing message). NOT_FOUND scopes
# to THIS club (a cross-club request is invisible → 404); NOT_PENDING is the double-action guard
# (already approved/declined/cancelled → 409); the rest are gateway-refund failures (the request
# is left 'pending' so the admin can retry).
_REFUND_DECIDE_ERR = {
    "NOT_FOUND": (404, "Refund request not found."),
    "NOT_PENDING": (409, "This request has already been decided."),
    "yoco_unavailable": (503, "Online payments are not available right now."),
    "no_yoco_checkout_for_order": (404, "No Yoco checkout found for this order."),
    "refund_failed": (502, "The gateway refund failed — the request is still pending."),
}


def _emit_refund_decided(*, club_id, request_row, decision, note):
    """Best-effort emit → drives the (already-built) refund_decided notification (approved/
    declined). Guarded — the decision already committed; a CRM hiccup must never surface as an
    error (mirrors the membership_activated / coach_invited emits in this module)."""
    try:
        from marketing_crm.tracking import emit
        emit("refund_decided", {
            "club_id": str(club_id),
            "user_id": request_row.get("user_id"),
            "ref_type": "order", "ref_id": str(request_row.get("order_id")),
            "order_id": str(request_row.get("order_id")),
            "decision": decision,
            "amount_minor": request_row.get("amount_minor"),
            "note": note,
        })
    except Exception:
        log.debug("refund_decided emit skipped (tracking unavailable)")


@admin_bp.post("/refund-requests/<request_id>/approve")
def approve_refund_request(request_id):
    """Approve a pending client refund request: execute the Yoco refund for its order (reusing
    the existing gateway path), mark the request 'refunded', then emit refund_decided/approved.
    Body {amount_minor?, cancel_booking?, note?}. 404 if not this club's request; 409 if not
    pending; 502/503 if the gateway refund failed (request LEFT pending)."""
    p, err = _admin()
    if err:
        return err
    b = _body()
    amount_minor = b.get("amount_minor")
    note = b.get("note")
    cancel_flag = bool(b.get("cancel_booking"))
    from billing import refunds
    with session_scope() as s:
        req, ecode, emsg = refunds.approve_refund_request(
            s, club_id=p.club_id, request_id=request_id, decided_by=p.user_id,
            amount_minor=amount_minor, note=note)
    if ecode:
        status, msg = _REFUND_DECIDE_ERR.get(ecode, (502, "The refund could not be completed."))
        return jsonify(error=ecode, message=(emsg or msg)), status

    # Optional: also cancel the order's booking(s) + free the slot — the same "Refund & cancel"
    # choice as the Recent-online-payments path (record-only refund otherwise, docs/05 §8). Reuse
    # diary.cancel_booking (role=club_admin waives the fee); guarded so the decision stands even
    # if the cancel fails. Separate session: the approve transaction already committed.
    cancelled = None
    if cancel_flag:
        cancelled = False
        try:
            from sqlalchemy import text as _text
            from diary.bookings import cancel_booking as _diary_cancel
            with session_scope() as s2:
                bid = s2.execute(
                    _text("SELECT booking_id FROM billing.order_line "
                          "WHERE order_id = :oid AND booking_id IS NOT NULL LIMIT 1"),
                    {"oid": str(req.get("order_id"))},
                ).scalar()
                if bid:
                    cres = _diary_cancel(s2, club_id=p.club_id, booking_id=str(bid),
                                         actor_user_id=p.user_id, role="club_admin",
                                         reason="admin refund (request approved)")
                    cancelled = bool(cres and cres.get("ok"))
        except Exception:
            log.warning("approve refund: booking cancel failed for order=%s (refund stands)",
                        req.get("order_id"))

    _emit_refund_decided(club_id=p.club_id, request_row=req, decision="approved", note=note)
    return jsonify(refund_request=req, cancelled=cancelled), 200


@admin_bp.post("/refund-requests/<request_id>/decline")
def decline_refund_request(request_id):
    """Decline a pending client refund request → 'declined' (no money moves), then emit
    refund_decided/declined. Body {note?}. 404 if not this club's request; 409 if not pending."""
    p, err = _admin()
    if err:
        return err
    b = _body()
    note = b.get("note")
    from billing import refunds
    with session_scope() as s:
        req, ecode = refunds.decline_refund_request(
            s, club_id=p.club_id, request_id=request_id, decided_by=p.user_id, note=note)
    if ecode:
        status, msg = _REFUND_DECIDE_ERR.get(ecode, (400, "Could not decline the request."))
        return jsonify(error=ecode, message=msg), status
    _emit_refund_decided(club_id=p.club_id, request_row=req, decision="declined", note=note)
    return jsonify(refund_request=req), 200


@admin_bp.get("/people")
def get_people():
    """Everyone in the club (members/coaches/guests/admins) for the admin People tab."""
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        rows = repo.list_people(s, club_id=p.club_id)
    return jsonify(people=rows, count=len(rows)), 200


@admin_bp.get("/people/<user_id>")
def get_person(user_id):
    """The unified person 360 (profile + all roles + active membership + owed statement + online
    payments + bookings; if the person is a coach, also a settlement summary). Mirrors the coach
    client 360 from the club's god-view. Every booking row drills to the admin event story."""
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        person = repo.get_person(s, club_id=p.club_id, user_id=user_id)
    if person is None:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(person=person), 200


@admin_bp.post("/clients")
def create_client():
    """Create a client on the system now (a walk-up / off-system customer). Returns their user_id so
    the caller can then issue a membership/pack. Idempotent on email (links to their login later)."""
    p, err = _admin()
    if err:
        return err
    b = _body()
    email = (b.get("email") or "").strip()
    if not email:
        return jsonify(error="email required"), 400
    with session_scope() as s:
        res = repo.create_client(s, club_id=p.club_id, name=b.get("name"),
                                 email=email, phone=b.get("phone"))
    return jsonify(res), 201


@admin_bp.get("/clients/<client_user_id>/packages")
def admin_client_packages(client_user_id):
    """A client's ACTIVE lesson packs — for admin on-behalf booking to auto-route to a prepaid pack.
    ?coach_id filters to packs drawable by that coach (coach-specific to them, or coach-agnostic)."""
    p, err = _admin()
    if err:
        return err
    coach_id = (request.args.get("coach_id") or "").strip() or None
    try:
        from billing import bundles
        with session_scope() as s:
            ws = bundles.wallets_for(s, club_id=p.club_id, user_id=client_user_id, active_only=True)
        # Lesson packs: only the chosen coach's (or coach-agnostic). Class/court packs are coach-
        # agnostic → always included (so a class booking can auto-draw the client's class pack).
        ws = [w for w in ws if w.get("service_kind") != "lesson"
              or w.get("coach_user_id") in (None, coach_id)]
    except Exception:
        ws = []
    return jsonify(packages=ws), 200


@admin_bp.post("/members/<user_id>/issue")
def issue_package(user_id):
    """Issue a membership OR a token pack to a client (walk-up / off-system). Reuses the offline-
    purchase engine: owed order + activated now; mark_paid settles it immediately. Body:
    {kind:'membership'|'pack', price_id?|bundle_plan_id?, start_date?, mark_paid?, pay_provider?}."""
    p, err = _admin()
    if err:
        return err
    b = _body()
    kind = (b.get("kind") or "").strip().lower()
    with session_scope() as s:
        try:
            res = repo.issue_package(
                s, club_id=p.club_id, user_id=user_id, kind=kind,
                price_id=b.get("price_id"), bundle_plan_id=b.get("bundle_plan_id"),
                start_date=(b.get("start_date") or None),
                mark_paid=bool(b.get("mark_paid")), pay_provider=(b.get("pay_provider") or "cash"))
        except ValueError as e:
            return jsonify(error=str(e)), 400
    try:
        from marketing_crm.tracking import emit
        payload = {"club_id": str(p.club_id), "user_id": str(user_id), "ref_type": "order",
                   "ref_id": str(res.get("order_id"))}
        # For a PACK, carry the pack name + session count so the "Pack activated" email body reads
        # "Your <pack> is ready. N sessions added" instead of the generic "Your session pack is ready".
        if kind != "membership":
            plan = res.get("plan") or {}
            payload["label"] = plan.get("label")
            payload["tokens_total"] = plan.get("sessions_count")
        emit("membership_activated" if kind == "membership" else "bundle_activated", payload)
    except Exception:
        log.debug("issue_package activation emit skipped")
    return jsonify(res), 201


@admin_bp.post("/members/<user_id>/membership")
def grant_membership(user_id):
    """Grant (or extend) a member's membership → their courts become free until it expires."""
    p, err = _admin()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        res = repo.grant_membership(s, club_id=p.club_id, user_id=user_id,
                                    months=b.get("months"), price_id=b.get("price_id"),
                                    start_date=(b.get("start_date") or None))
    # NEW emit: a manual membership grant → "Membership active" notification to the member
    # (child→guardian resolved by the notifications engine). Best-effort + guarded — the admin
    # action already committed; a CRM/notification hiccup must not surface as an error.
    try:
        from marketing_crm.tracking import emit
        emit("membership_activated", {"club_id": str(p.club_id), "user_id": str(user_id),
                                      "ref_type": "membership_subscription"})
    except Exception:
        log.debug("membership_activated emit skipped (tracking unavailable)")
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
        outcome = repo.delete_coach(s, club_id=p.club_id, user_id=user_id)
    if outcome is None:
        return jsonify(error="NOT_FOUND"), 404
    # 'deleted' = removed outright; 'archived' = had history, kept + marked lapsed instead.
    return jsonify(ok=True, outcome=outcome), 200


@admin_bp.patch("/coaches/<user_id>")
def patch_coach(user_id):
    """Admin edit of a coach — currently Hide/Unhide (is_bookable)."""
    p, err = _admin()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        if "status" in b:   # lifecycle: active | deactivated | terminated
            repo.set_coach_status(s, club_id=p.club_id, user_id=user_id, status=b.get("status"))
        elif "is_bookable" in b:
            ok = repo.set_coach_bookable(s, club_id=p.club_id, user_id=user_id,
                                         is_bookable=bool(b.get("is_bookable")))
            if not ok:
                return jsonify(error="NOT_FOUND"), 404
    return jsonify(ok=True), 200


# ---------------------------------------------------------------------------
# commission engine — coach agreements + commission rules (owner config)
# The owner monetises coaches via rent AND/OR commission % (additive, per coach). Commission
# resolves coach+product > product > coach > club (billing.commission.resolve_commission_pct).
# ---------------------------------------------------------------------------

@admin_bp.get("/coach-agreements")
def get_coach_agreements():
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        data = repo.coach_agreements_overview(s, club_id=p.club_id)
    return jsonify(data), 200


@admin_bp.put("/coach-agreements/<coach_user_id>")
def put_coach_agreement(coach_user_id):
    """Set a coach's rent posture (rent_minor / rent_day). Commission % is set via the
    commission-rules endpoint (so rent and % are independent — additive per docs/specs/01)."""
    p, err = _admin()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        agr = repo.upsert_agreement(
            s, club_id=p.club_id, coach_user_id=coach_user_id,
            rent_minor=b.get("rent_minor"), rent_day=b.get("rent_day"),
            status=b.get("status"), notes=b.get("notes"))
    return jsonify(agreement=agr), 200


@admin_bp.get("/commission-rules")
def get_commission_rules():
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        rows = repo.list_commission_rules(s, club_id=p.club_id)
    return jsonify(rules=rows, count=len(rows)), 200


@admin_bp.post("/commission-rules")
def post_commission_rule():
    """Set a rate for a scope (derived from which of product_id/coach_user_id are sent):
    club | product | coach | coach_product. SUPERSEDES the matching active rule."""
    p, err = _admin()
    if err:
        return err
    b = _body()
    pct = b.get("commission_pct")
    try:
        pct = float(pct)
    except (TypeError, ValueError):
        return jsonify(error="commission_pct must be a number 0..100"), 400
    if pct < 0 or pct > 100:
        return jsonify(error="commission_pct must be between 0 and 100"), 400
    with session_scope() as s:
        rule = repo.set_commission_rule(
            s, club_id=p.club_id,
            product_id=b.get("product_id") or None,
            coach_user_id=b.get("coach_user_id") or None,
            commission_pct=pct)
    return jsonify(rule=rule), 201


@admin_bp.delete("/commission-rules/<rule_id>")
def delete_commission_rule(rule_id):
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        ok = repo.deactivate_commission_rule(s, club_id=p.club_id, rule_id=rule_id)
    if not ok:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(ok=True), 200


@admin_bp.get("/commission-rules/preview")
def preview_commission_rule():
    """The 'effective rate' UI preview: the resolved % for a (coach, product) pair."""
    p, err = _admin()
    if err:
        return err
    from billing.commission import resolve_commission_pct
    coach = (request.args.get("coach_user_id") or "").strip() or None
    product = (request.args.get("product_id") or "").strip() or None
    with session_scope() as s:
        pct = resolve_commission_pct(s, club_id=p.club_id, product_id=product,
                                     coach_user_id=coach)
    return jsonify(effective_pct=float(pct)), 200


# ---------------------------------------------------------------------------
# owner cockpit — financial numbers (views-style thin passthroughs)
# ---------------------------------------------------------------------------

def _range():
    return ((request.args.get("from") or "").strip() or None,
            (request.args.get("to") or "").strip() or None)


@admin_bp.get("/home")
def get_admin_home():
    """Owner Home command-center: money · people-attention · approvals (each guarded → zeros)."""
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        data = repo.admin_home(s, club_id=p.club_id)
    return jsonify(data), 200


@admin_bp.get("/financials/summary")
def get_cockpit_summary():
    p, err = _admin()
    if err:
        return err
    dt_from, dt_to = _range()
    with session_scope() as s:
        data = repo.cockpit_summary(s, club_id=p.club_id, dt_from=dt_from, dt_to=dt_to)
    return jsonify(data), 200


@admin_bp.get("/financials/revenue")
def get_cockpit_revenue():
    p, err = _admin()
    if err:
        return err
    dt_from, dt_to = _range()
    with session_scope() as s:
        rows = repo.cockpit_revenue(s, club_id=p.club_id, dt_from=dt_from, dt_to=dt_to)
    return jsonify(revenue=rows, count=len(rows)), 200


@admin_bp.get("/financials/coach-earnings")
def get_cockpit_coach_earnings():
    p, err = _admin()
    if err:
        return err
    dt_from, dt_to = _range()
    with session_scope() as s:
        rows = repo.cockpit_coach_earnings(s, club_id=p.club_id, dt_from=dt_from, dt_to=dt_to)
    return jsonify(coaches=rows, count=len(rows)), 200


@admin_bp.get("/financials/memberships")
def get_cockpit_memberships():
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        data = repo.cockpit_memberships(s, club_id=p.club_id)
    return jsonify(data), 200


# --- club <-> coach SETTLEMENT (payouts) — the other half of the loop ---------------------
# The cockpit REPORTS each coach's running coach_ledger balance (financials/coach-earnings →
# lifetime_balance_minor). These endpoints RECORD the settlement that pays it down, netting the
# ledger (append-only). Admin-only.

@admin_bp.get("/financials/settlement")
def get_settlement_overview():
    """The 'who owes what' aging view: clients bucketed by age + coaches with a non-zero ledger
    balance (the club<->coach settlement worklist). Read-only, guarded."""
    p, err = _admin()
    if err:
        return err
    from billing import commission as comm
    with session_scope() as s:
        data = comm.settlement_overview(s, club_id=p.club_id)
    return jsonify(data), 200


@admin_bp.get("/coach-payouts")
def list_coach_payouts_route():
    p, err = _admin()
    if err:
        return err
    coach_uid = (request.args.get("coach_user_id") or "").strip() or None
    from billing import commission as comm
    with session_scope() as s:
        rows = comm.list_coach_payouts(s, club_id=p.club_id, coach_user_id=coach_uid)
    return jsonify(payouts=rows, count=len(rows)), 200


@admin_bp.post("/coach-payouts")
def record_coach_payout_route():
    """Record a club<->coach settlement. Body: {coach_user_id, amount_minor, direction
    (club_to_coach|coach_to_club|offset), method?(eft|cash|offset), reference?, period_label?,
    note?, status?(paid|draft)}. A 'paid' payout nets the coach_ledger balance (append-only)."""
    p, err = _admin()
    if err:
        return err
    b = request.get_json(silent=True) or {}
    coach_uid = (b.get("coach_user_id") or "").strip()
    if not coach_uid:
        return jsonify(error="coach_user_id required"), 400
    from billing import commission as comm
    with session_scope() as s:
        res = comm.record_coach_payout(
            s, club_id=p.club_id, coach_user_id=coach_uid,
            amount_minor=b.get("amount_minor"), direction=(b.get("direction") or ""),
            method=(b.get("method") or "eft"), reference=b.get("reference"),
            period_label=b.get("period_label"), note=b.get("note"),
            created_by=p.user_id, status=(b.get("status") or "paid"))
    return (jsonify(res), 200) if res.get("ok") else (jsonify(res), 422)


@admin_bp.patch("/coach-payouts/<payout_id>")
def patch_coach_payout_route(payout_id):
    """Flip a payout status: draft->paid (posts the ledger entry) or void (a draft only)."""
    p, err = _admin()
    if err:
        return err
    b = request.get_json(silent=True) or {}
    from billing import commission as comm
    with session_scope() as s:
        res = comm.set_payout_status(s, club_id=p.club_id, payout_id=payout_id,
                                     status=(b.get("status") or ""))
    if not res.get("ok"):
        return jsonify(res), (404 if res.get("error") == "NOT_FOUND" else 422)
    return jsonify(res), 200


# ---------------------------------------------------------------------------
# coach month-end statement (coach self-service surface — NOT admin-only).
# Per docs/specs/01 the coach's most-wanted surface. Accessible to the logged-in coach
# (their own statement) OR an admin (any coach via ?coach_user_id=). Lives in the admin lane
# (this module) rather than coach.py so the Coach agent's files are untouched.
# ---------------------------------------------------------------------------

def _coach_or_admin():
    """Resolve a principal who is a coach (own statement) or club/platform admin. Returns
    (principal, error). Admins may target ?coach_user_id=; a coach is locked to themselves."""
    pr = resolve_principal(request)
    if pr is None or not pr.authenticated:
        return None, (jsonify(error="unauthorized"), 401)
    if pr.role not in ("coach", "club_admin", "platform_admin"):
        return None, (jsonify(error="forbidden"), 403)
    if pr.club_id is None:
        return None, (jsonify(error="no_club_scope"), 400)
    return pr, None


@admin_bp.get("/coach-statement")
def coach_statement():
    """GET /api/admin/coach-statement?month=YYYY-MM[&coach_user_id=]
    Per-client: lessons, paid-via-Yoco, owed (arrears), net balance + the coach's running
    ledger balance. A coach sees only their own; an admin may pass coach_user_id."""
    pr, err = _coach_or_admin()
    if err:
        return err
    from billing import commission as comm
    month = (request.args.get("month") or "").strip() or None
    target = (request.args.get("coach_user_id") or "").strip() or None
    is_admin = pr.role in ("club_admin", "platform_admin")
    coach_id = target if (is_admin and target) else pr.user_id
    if coach_id is None:
        return jsonify(error="no_coach"), 400
    with session_scope() as s:
        data = comm.coach_statement(s, club_id=pr.club_id, coach_user_id=coach_id, month=month)
    return jsonify(data), 200


@admin_bp.post("/coach-statement/arrears/<arrears_id>/collected")
def post_arrears_collected(arrears_id):
    """The coach (or admin) marks an arrears invoice collected (off-platform EFT received) →
    accrues its commission. A coach may only mark their OWN arrears."""
    pr, err = _coach_or_admin()
    if err:
        return err
    from billing import commission as comm
    is_admin = pr.role in ("club_admin", "platform_admin")
    with session_scope() as s:
        res = comm.mark_arrears_collected(
            s, club_id=pr.club_id, arrears_id=arrears_id,
            coach_user_id=None if is_admin else pr.user_id,
            collected_by=pr.user_id)
    if not res.get("ok"):
        code = 404 if res.get("error") == "NOT_FOUND" else 403
        return jsonify(error=res.get("error")), code
    return jsonify(res), 200


@admin_bp.get("/activity")
def admin_activity():
    """The club-wide transaction log — every payment, refund, order raised/void/written-off,
    commission earned/clawed back, arrears, and membership event, newest first. Owner oversight."""
    pr, err = _admin()
    if err:
        return err
    try:
        limit = max(1, min(300, int(request.args.get("limit") or 150)))
    except (TypeError, ValueError):
        limit = 150
    from billing import activity as act
    with session_scope() as s:
        rows = act.transaction_log(s, club_id=pr.club_id, scope="owner", limit=limit)
    return jsonify(activity=rows, count=len(rows)), 200


@admin_bp.patch("/coach-statement/arrears/<arrears_id>")
def patch_arrears(arrears_id):
    """The coach (or admin) edits an OWED arrears line before collection: DISCOUNT (body
    {gross_minor}) and/or WRITE OFF (body {status:'written_off'} — waive the lesson, no
    commission). A coach may only edit their OWN arrears."""
    pr, err = _coach_or_admin()
    if err:
        return err
    from billing import commission as comm
    is_admin = pr.role in ("club_admin", "platform_admin")
    body = request.get_json(silent=True) or {}
    with session_scope() as s:
        res = comm.adjust_arrears(
            s, club_id=pr.club_id, arrears_id=arrears_id,
            coach_user_id=None if is_admin else pr.user_id,
            gross_minor=body.get("gross_minor"), status=body.get("status"),
            reason=body.get("reason"), actor_user_id=pr.user_id)
    if not res.get("ok"):
        err_code = res.get("error")
        code = 404 if err_code == "NOT_FOUND" else (403 if err_code == "FORBIDDEN" else 422)
        return jsonify(error=err_code, status=res.get("status")), code
    return jsonify(res), 200


# ---------------------------------------------------------------------------
# unified statement (admin view) — see + clear a member's owed orders
# (docs/specs/UNIFIED-STATEMENT.md). Voids a mistake / writes off a forgiven debt.
# ---------------------------------------------------------------------------

@admin_bp.get("/members/<user_id>/statement")
def member_statement(user_id):
    """A member's UNIFIED statement (their unpaid orders + total), for the People 360 drawer."""
    p, err = _admin()
    if err:
        return err
    from billing import statement as statement_repo
    with session_scope() as s:
        data = statement_repo.statement(s, club_id=p.club_id, user_id=user_id)
    return jsonify(data), 200


# ---------------------------------------------------------------------------
# admin event story (the ONE shared drill target — docs/specs/ADMIN-REDESIGN.md, golden rule).
# Read = the god-view of any booking; the write actions REUSE the existing diary/billing routes
# (accept/propose/decline/reschedule/cancel/status via /api/diary/*, settle via /api/billing/
# desk-payment, refund via /api/billing/yoco/refund, void/write-off via /orders/<id>/void, coaching
# via /coach-statement/arrears/*). The only NEW write is reassign-coach (admin-only).
# ---------------------------------------------------------------------------

@admin_bp.get("/bookings/<booking_id>")
def get_admin_booking(booking_id):
    """The admin god-view of one booking: client + coach + charge + coaching arrears + full action
    eligibility. Every People/Money/Diary/Home booking row drills here (#/event/:id)."""
    p, err = _admin()
    if err:
        return err
    from diary import bookings as bookings_mod
    with session_scope() as s:
        story = bookings_mod.admin_booking_story(s, club_id=p.club_id, booking_id=booking_id)
    if story is None:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(booking=story), 200


@admin_bp.get("/classes/<enrolment_id>")
def get_admin_class(enrolment_id):
    """The admin god-view record of one CLASS enrolment — same shape as the booking story so the one
    widget renders it (every People/Diary class row drills here)."""
    p, err = _admin()
    if err:
        return err
    from diary import classes as classes_mod
    with session_scope() as s:
        story = classes_mod.enrolment_story(s, club_id=p.club_id, enrolment_id=enrolment_id, scope="owner")
    if story is None:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(booking=story), 200


@admin_bp.post("/bookings/<booking_id>/reassign-coach")
def reassign_coach(booking_id):
    """Move a future, not-yet-paid lesson to a different bookable coach. Body {coach_user_id}.
    The GiST exclusion constraint guarantees no double-book (busy -> 409, nothing changes)."""
    p, err = _admin()
    if err:
        return err
    b = _body()
    coach = (b.get("coach_user_id") or "").strip()
    if not coach:
        return jsonify(error="coach_user_id required"), 400
    from diary import bookings as bookings_mod
    with session_scope() as s:
        res = bookings_mod.admin_reassign_coach(
            s, club_id=p.club_id, booking_id=booking_id, new_coach_user_id=coach)
    if not res.get("ok"):
        return jsonify(res), res.get("status", 400)
    return jsonify(res), 200


@admin_bp.post("/orders/<order_id>/void")
def void_order(order_id):
    """Clear an UNPAID order: void (a mistake) or write-off (body {write_off:true} — forgive the debt).
    A paid order must be refunded, not voided. Drops the line off the member's statement + balance."""
    p, err = _admin()
    if err:
        return err
    b = _body()
    from billing import statement as statement_repo
    with session_scope() as s:
        res = statement_repo.void_order(s, club_id=p.club_id, order_id=order_id,
                                        write_off=bool(b.get("write_off")))
    if not res.get("ok"):
        return jsonify(res), 409
    return jsonify(res), 200


@admin_bp.post("/orders/<order_id>/discount")
def discount_order_route(order_id):
    """Apply a discount to any OPEN order (court/lesson/class/pack/membership). Body:
    {discount_minor | new_amount_minor, reason}. Reprices the order line(s) preserving the pre-discount
    price in original_amount_minor, and keeps a linked coach_arrears line in LOCKSTEP. A paid order must
    be refunded, not discounted (NOT_OPEN)."""
    p, err = _admin()
    if err:
        return err
    b = _body()
    reason = (b.get("reason") or "").strip()
    if not reason:
        return jsonify(error="reason_required"), 400
    from billing.statement import discount_order
    try:
        with session_scope() as s:
            res = discount_order(
                s, club_id=p.club_id, order_id=order_id,
                discount_minor=b.get("discount_minor"), new_amount_minor=b.get("new_amount_minor"),
                reason=reason, actor_user_id=p.user_id)
    except ValueError as e:            # BAD_ARGS: not exactly one of discount / new-amount
        return jsonify(error=str(e)), 400
    if res.get("ok") is False:
        code = res.get("error", "ERROR")
        status = 404 if code == "ORDER_NOT_FOUND" else 400
        return jsonify(error=code, **{k: v for k, v in res.items() if k not in ("ok", "error")}), status
    return jsonify(res), 200


@admin_bp.post("/clients/<client_user_id>/invoice")
def create_client_invoice(client_user_id):
    """Generate an ad-hoc OWED invoice for a client and email them a pay link. Body:
    {lines:[{price_id?|description, qty, amount_minor}], discount_minor?, reason?}. Creates an 'open'
    billing.order (shows on the client's unified statement, settleable online via /portal); NOT a
    calendar booking. Emits `statement_ready` → the 'Your invoice is ready' email with a /portal pay link."""
    p, err = _admin()
    if err:
        return err
    b = _body()
    lines = b.get("lines")
    if not isinstance(lines, list) or not lines:
        return jsonify(error="lines_required"), 400
    with session_scope() as s:
        inv = repo.create_invoice(
            s, club_id=p.club_id, user_id=client_user_id, lines=lines,
            discount_minor=int(b.get("discount_minor") or 0),
            reason=((b.get("reason") or "").strip() or None), actor_user_id=p.user_id)
        if not inv:
            return jsonify(error="no_valid_lines"), 400
        # Whether the client can even BE emailed a pay link (surface it honestly to the admin).
        client_email = s.execute(
            text('SELECT email FROM iam."user" WHERE id = :u'), {"u": str(client_user_id)}).scalar()
    # Email is best-effort and fires AFTER the invoice commits — a delivery hiccup (or a client with no
    # email) must NEVER fail or roll back the invoice. emit is itself fire-and-forget, but keep it out of
    # the tx + guarded so the invoice is unconditionally saved.
    try:
        from marketing_crm.tracking import emit
        emit("statement_ready", {"club_id": str(p.club_id), "user_id": str(client_user_id),
                                 "email": (client_email or None),
                                 "amount_minor": inv.get("amount_minor"), "currency": inv.get("currency")})
    except Exception:
        pass
    inv["emailed"] = bool(client_email)
    return jsonify(inv), 201


@admin_bp.post("/clients/<client_user_id>/wallets/<wallet_id>/adjust")
def admin_wallet_adjust(client_user_id, wallet_id):
    """Manually adjust a client's prepaid pack balance (money-adjacent, audited). Body:
    {delta_sessions | delta_minutes, reason}. delta_sessions is converted to minutes via the wallet's
    base length. Writes a token_ledger 'adjust' row stamped with the acting admin."""
    p, err = _admin()
    if err:
        return err
    b = _body()
    reason = (b.get("reason") or "").strip() or None
    from billing import bundles
    with session_scope() as s:
        # Resolve the delta in MINUTES: an explicit delta_minutes wins, else delta_sessions × base length.
        delta_minutes = b.get("delta_minutes")
        if delta_minutes is None and b.get("delta_sessions") is not None:
            base = s.execute(
                text("SELECT COALESCE(base_minutes, duration_minutes, 60) "
                     "FROM billing.token_wallet WHERE club_id = :c AND id = :w"),
                {"c": p.club_id, "w": wallet_id}).scalar()
            if base is None:
                return jsonify(error="WALLET_NOT_FOUND"), 404
            delta_minutes = int(round(float(b["delta_sessions"]) * int(base)))
        try:
            res = bundles.adjust_wallet(
                s, club_id=p.club_id, wallet_id=wallet_id,
                delta_minutes=int(delta_minutes or 0), reason=reason, actor_user_id=p.user_id)
        except ValueError as e:
            code = str(e)
            return jsonify(error=code), (404 if code == "WALLET_NOT_FOUND" else 400)
    return jsonify(res), 200


@admin_bp.post("/clients/<client_user_id>/wallets/<wallet_id>/expire")
def admin_wallet_expire(client_user_id, wallet_id):
    """Soft-expire a client's prepaid pack (status→expired, balance zeroed; the row + ledger are kept
    for audit — never hard-deleted). Body: {reason}."""
    p, err = _admin()
    if err:
        return err
    reason = (_body().get("reason") or "").strip() or None
    from billing import bundles
    with session_scope() as s:
        try:
            res = bundles.expire_wallet(
                s, club_id=p.club_id, wallet_id=wallet_id, reason=reason, actor_user_id=p.user_id)
        except ValueError as e:
            code = str(e)
            return jsonify(error=code), (404 if code == "WALLET_NOT_FOUND" else 400)
    return jsonify(res), 200

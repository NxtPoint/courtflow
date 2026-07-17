# coach/routes.py — the /api/coach/* surface (coach self-service: onboarding, profile,
# weekly hours, lesson services/rates, time-off view/remove, and a read-only "My Clients"
# view). Blueprint coach_bp. Registered in app.py.
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
from diary import classes as classes_mod

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
            "languages": profile.get("languages") or [],
            "qualifications": profile.get("qualifications") or [],
            "years_experience": profile.get("years_experience"),
            "is_bookable": profile.get("is_bookable"),
            "public_visibility": profile.get("public_visibility"),
            "review_bookings": profile.get("review_bookings"),
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
    # text[] fields must be lists when supplied (same guard as specialties).
    for arr in ("specialties", "languages", "qualifications"):
        v = b.get(arr)
        if v is not None and not isinstance(v, list):
            return jsonify(error=arr + " must be a list"), 400
    # rank is admin-only — the repo doesn't accept it, so it's ignored even if sent.
    with session_scope() as s:
        profile = repo.patch_profile(
            s, club_id=p.club_id, user_id=p.user_id,
            display_name=b.get("display_name"), headline=b.get("headline"),
            bio=b.get("bio"), photo_url=b.get("photo_url"),
            specialties=b.get("specialties"), languages=b.get("languages"),
            qualifications=b.get("qualifications"),
            years_experience=b.get("years_experience"),
            is_bookable=b.get("is_bookable"), public_visibility=b.get("public_visibility"),
            review_bookings=b.get("review_bookings"),
            phone=b.get("phone"),
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
    # Per-duration per_booking is the platform pricing model (diary/pricing.py). audience
    # defaults to 'any' so the price resolves for every booker (not just 'member').
    with session_scope() as s:
        svc = repo.create_service(
            s, club_id=p.club_id, user_id=p.user_id,
            name=b.get("name"), duration_minutes=b.get("duration_minutes"),
            amount_minor=b.get("amount_minor", 0), audience=b.get("audience", "any"),
            unit=b.get("unit", "per_booking"),
        )
    return jsonify(service=svc), 201


@coach_bp.post("/services/<product_id>/rate")
def post_service_rate(product_id):
    """Add another per-duration rate to an existing lesson product the coach owns (so one
    'Private lesson' product can carry 30/60/90 rates). Body: {duration_minutes, amount_minor}."""
    p, err = _coach()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        svc = repo.add_service_rate(
            s, club_id=p.club_id, user_id=p.user_id, product_id=product_id,
            duration_minutes=b.get("duration_minutes"),
            amount_minor=b.get("amount_minor", 0), audience=b.get("audience", "any"),
            unit=b.get("unit", "per_booking"),
        )
    if svc is None:
        return jsonify(error="NOT_FOUND"), 404
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
            duration_minutes=b.get("duration_minutes"),
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


# (coach /bundle-plans routes REMOVED 2026-07-09 — a coach's packs live under each of their
#  lessons/classes and are edited via the services lane /api/services/<id>/packages, not a
#  standalone coach packs surface.)


# ---------------------------------------------------------------------------
# classes (a coach's OWN classes: resource(kind='class', coach_user_id=the coach)
# + product(kind='class') + price). Every route scopes to the coach's user_id.
# ---------------------------------------------------------------------------

def _class_result(res):
    if res is None:
        return jsonify(error="NOT_FOUND"), 404
    if res.get("ok"):
        return jsonify({k: v for k, v in res.items() if k != "ok"}), 200
    return jsonify(error=res.get("error"),
                   **{k: v for k, v in res.items()
                      if k not in ("ok", "status", "error")}), res.get("status", 400)


@coach_bp.get("/classes")
def get_classes():
    p, err = _coach()
    if err:
        return err
    with session_scope() as s:
        rows = classes_mod.list_class_types(s, club_id=p.club_id, coach_user_id=p.user_id)
    return jsonify(classes=rows, count=len(rows)), 200


@coach_bp.post("/classes")
def post_class():
    """Create a class owned by THIS coach (coach_user_id forced to the principal)."""
    p, err = _coach()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        res = classes_mod.create_class_type(
            s, club_id=p.club_id, name=b.get("name"), coach_user_id=p.user_id,
            capacity=b.get("capacity"), price_amount_minor=b.get("price_amount_minor"),
            duration_minutes=b.get("duration_minutes"), description=b.get("description"))
    return _class_result(res)


@coach_bp.post("/classes/<resource_id>/schedule")
def post_class_schedule(resource_id):
    p, err = _coach()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        if not repo.owns_class_resource(s, club_id=p.club_id, user_id=p.user_id,
                                        resource_id=resource_id):
            return jsonify(error="forbidden"), 403
        res = classes_mod.schedule_sessions(
            s, club_id=p.club_id, resource_id=resource_id,
            weekdays=b.get("weekdays"), start_time=b.get("start_time"),
            date_from=b.get("date_from"), date_until=b.get("date_until"),
            dates=b.get("dates"), duration_minutes=b.get("duration_minutes"),
            capacity=b.get("capacity"), price_id=b.get("price_id"),
            court_resource_id=b.get("court_resource_id"),
            court_resource_ids=b.get("court_resource_ids"))
    return _class_result(res)


@coach_bp.patch("/classes/<resource_id>")
def patch_class(resource_id):
    """Edit a class this coach OWNS. The coach is forced to themselves (a coach can't reassign their
    class to another coach). Changing courts/capacity cascades to future sessions."""
    p, err = _coach()
    if err:
        return err
    b = _body()
    with session_scope() as s:
        if not repo.owns_class_resource(s, club_id=p.club_id, user_id=p.user_id,
                                        resource_id=resource_id):
            return jsonify(error="forbidden"), 403
        try:
            res = classes_mod.update_class_type(
                s, club_id=p.club_id, resource_id=resource_id, coach_user_id=p.user_id,
                name=b.get("name"), capacity=b.get("capacity"), description=b.get("description"),
                court_resource_ids=b.get("court_resource_ids"),
                court_resource_id=b.get("court_resource_id"))
        except ValueError as e:
            return jsonify(error=str(e)), 400
    return _class_result(res)


@coach_bp.get("/classes/<resource_id>/sessions")
def get_class_sessions(resource_id):
    p, err = _coach()
    if err:
        return err
    q = request.args
    with session_scope() as s:
        if not repo.owns_class_resource(s, club_id=p.club_id, user_id=p.user_id,
                                        resource_id=resource_id):
            return jsonify(error="forbidden"), 403
        rows = classes_mod.list_type_sessions(
            s, club_id=p.club_id, resource_id=resource_id,
            date_from=q.get("date_from"), date_to=q.get("date_to"))
    return jsonify(sessions=rows, count=len(rows)), 200


@coach_bp.post("/classes/sessions/<session_id>/cancel")
def post_class_session_cancel(session_id):
    p, err = _coach()
    if err:
        return err
    with session_scope() as s:
        if not repo.owns_class_session(s, club_id=p.club_id, user_id=p.user_id,
                                       session_id=session_id):
            return jsonify(error="forbidden"), 403
        res = classes_mod.cancel_session(s, club_id=p.club_id, session_id=session_id)
    return _class_result(res)


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


# ---------------------------------------------------------------------------
# time-off (view + remove) — the coach's OWN coach resource. POST stays in the
# diary lane (/api/diary/time-off); the coach lane owns the GET/DELETE so a coach
# can list upcoming blocks and remove a holiday. Every repo call is scoped to the
# coach's user_id, so a coach can only see/remove blocks on their own resource.
# ---------------------------------------------------------------------------

@coach_bp.get("/time-off")
def get_time_off():
    p, err = _coach()
    if err:
        return err
    upcoming = (request.args.get("all") or "").lower() not in ("1", "true", "yes")
    with session_scope() as s:
        rows = repo.list_time_off(s, club_id=p.club_id, user_id=p.user_id,
                                  upcoming_only=upcoming)
    return jsonify(time_off=rows, count=len(rows)), 200


@coach_bp.delete("/time-off/<time_off_id>")
def delete_time_off(time_off_id):
    p, err = _coach()
    if err:
        return err
    with session_scope() as s:
        ok = repo.delete_time_off(s, club_id=p.club_id, user_id=p.user_id,
                                  time_off_id=time_off_id)
    if not ok:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(ok=True), 200


# ---------------------------------------------------------------------------
# "My Clients" — read-only derivation from diary.booking + diary.enrolment.
# A coach sees ONLY their own clients (scoped by coach_user_id = principal.user_id),
# and only that client's history WITH THIS COACH. Spend is GROSS (commission-engine
# agent owns net-of-commission). No new tables; pure SQL aggregation in the repo.
# ---------------------------------------------------------------------------

@coach_bp.get("/clients")
def get_clients():
    p, err = _coach()
    if err:
        return err
    q = request.args
    try:
        limit = min(max(int(q.get("limit") or 200), 1), 500)
    except (TypeError, ValueError):
        limit = 200
    with session_scope() as s:
        rows = repo.list_clients(s, club_id=p.club_id, user_id=p.user_id,
                                 search=(q.get("search") or "").strip() or None, limit=limit)
    return jsonify(clients=rows, count=len(rows)), 200


@coach_bp.post("/clients")
def coach_create_client():
    """A coach creates a new client (walk-up / off-system) — the SAME as the admin People 'New client'.
    First name + surname + email required, cell optional. Reuses admin.repositories.create_client
    (idempotent on email; links to their Clerk login by email on first sign-in)."""
    p, err = _coach()
    if err:
        return err
    b = _body()
    first = (b.get("first_name") or "").strip()
    surname = (b.get("surname") or "").strip()
    email = (b.get("email") or "").strip()
    if not first or not surname:
        return jsonify(error="first name and surname required"), 400
    from admin import repositories as admin_repo
    if not admin_repo._EMAIL_RE.match(email):
        return jsonify(error="valid email required"), 400
    try:
        with session_scope() as s:
            res = admin_repo.create_client(s, club_id=p.club_id, first_name=first, surname=surname,
                                           email=email, phone=(b.get("phone") or "").strip() or None)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(res), 201


@coach_bp.get("/members/search")
def coach_member_search():
    """Type-ahead lookup of club members (name/email) for 'book a client' — returns email + phone so
    the coach picks a real member, never free-types. Min 2 chars."""
    p, err = _coach()
    if err:
        return err
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify(members=[]), 200
    with session_scope() as s:
        rows = repo.search_members(s, club_id=p.club_id, q=q)
    return jsonify(members=rows), 200


@coach_bp.get("/packages")
def coach_packages():
    """The coach's 'clients with packages' view — every client holding an active lesson pack with
    this coach + their remaining balance."""
    p, err = _coach()
    if err:
        return err
    with session_scope() as s:
        rows = repo.coach_package_holders(s, club_id=p.club_id, coach_user_id=p.user_id)
    return jsonify(packages=rows), 200


@coach_bp.get("/members/<client_user_id>/packages")
def coach_client_packages(client_user_id):
    """A client's ACTIVE lesson packs that THIS coach can draw (coach-specific to them, or
    coach-agnostic) — so 'book a client' can auto-route to a prepaid pack the client already funded
    to the coach, instead of raising a NEW charge. Returns [] if the token engine isn't present."""
    p, err = _coach()
    if err:
        return err
    try:
        from billing import bundles
        with session_scope() as s:
            ws = bundles.wallets_for(s, club_id=p.club_id, user_id=client_user_id, active_only=True)
        # Lesson packs are coach-specific — only THIS coach's (or coach-agnostic) ones are drawable by
        # them; class/court packs are coach-agnostic, so they always come through.
        mine = [w for w in ws if w.get("service_kind") != "lesson"
                or w.get("coach_user_id") in (None, str(p.user_id))]
    except Exception:
        mine = []
    return jsonify(packages=mine), 200


@coach_bp.get("/clients/<client_user_id>/360")
def coach_client_360(client_user_id):
    """The unified Client 360 record, coach scope — the SAME cross-lane composer the admin person-360
    and the client's own account derive from (golden rule: one data layer, views off it). Coaching +
    packages are filtered to THIS coach's relevance; `?month=YYYY-MM` scopes the coaching figures and
    adds the per-SERVICE breakdown (the month → client → service → transaction drill)."""
    p, err = _coach()
    if err:
        return err
    month = (request.args.get("month") or "").strip() or None
    from client360 import get_client_360
    with session_scope() as s:
        data = get_client_360(s, club_id=p.club_id, user_id=client_user_id,
                              scope="coach", coach_user_id=p.user_id, month=month)
    if data is None:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(person=data), 200


@coach_bp.get("/bookings/<booking_id>")
def get_booking_story(booking_id):
    """The coach's full 'event story' for a lesson/class they run — client + contact, when, court,
    charge + payment status, players + attendance, and the actions the coach can take."""
    p, err = _coach()
    if err:
        return err
    from diary import bookings as diary_bookings
    with session_scope() as s:
        story = diary_bookings.coach_booking_story(
            s, club_id=p.club_id, coach_user_id=p.user_id, booking_id=booking_id)
    if story is None:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(booking=story), 200


@coach_bp.get("/classes/<enrolment_id>")
def get_class_story(enrolment_id):
    """The coach's record of one CLASS enrolment in a class they run — same shape as the booking story."""
    p, err = _coach()
    if err:
        return err
    from diary import classes as diary_classes
    with session_scope() as s:
        story = diary_classes.enrolment_story(
            s, club_id=p.club_id, enrolment_id=enrolment_id, scope="coach", user_id=p.user_id)
    if story is None:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(booking=story), 200


@coach_bp.get("/orders/<order_id>/record")
def get_coach_order_record(order_id):
    """The coach's transaction record for a STANDALONE order they earned (a pack they SOLD) — the SAME
    Widgets.TransactionDetail shape a lesson/class drills to, so the earnings widget's order-only rows
    open a real record. Coach-scoped + read-only (fold + audit log + receipt; NO club money actions)."""
    p, err = _coach()
    if err:
        return err
    from diary import bookings as diary_bookings
    with session_scope() as s:
        story = diary_bookings.order_story(
            s, club_id=p.club_id, order_id=order_id, scope="coach", user_id=p.user_id)
    if story is None:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(booking=story), 200


@coach_bp.get("/money")
def get_coach_money():
    """The coach Money tab as an OUTCOME of bookings: the folded statement (Billed − Discount −
    Written-off = Invoiced ; Invoiced = Paid + Outstanding) for this coach's sessions in `?month=`
    (default current), per client + grand total + the coach's cut on paid + ledger balance."""
    p, err = _coach()
    if err:
        return err
    month = (request.args.get("month") or "").strip() or None
    with session_scope() as s:
        data = repo.coach_month_money(s, club_id=p.club_id, coach_user_id=p.user_id, month=month)
    return jsonify(data), 200


@coach_bp.get("/financials/earnings-by-service")
def get_coach_earnings_by_service():
    """The coach's OWN 'how am I earning — by service, by month' — the SAME reader + payload shape as the
    admin surface (admin.repositories.earnings_by_service), scoped to this coach's services (lessons +
    classes they run + packs they sold). So ONE Widgets.Earnings renders both; the coach is just the slice."""
    p, err = _coach()
    if err:
        return err
    from admin import repositories as admin_repo
    month = (request.args.get("month") or "").strip() or None
    with session_scope() as s:
        data = admin_repo.earnings_by_service(s, club_id=p.club_id, month=month, coach_user_id=p.user_id)
    return jsonify(data), 200


@coach_bp.get("/financials/revenue-clients")
def get_coach_earnings_clients():
    """The coach revenue drill's CLIENT level — within a service, this coach's clients (coach-scoped).
    Same admin reader, coach-scoped, so the coach skips the by-coach level (they ARE the coach)."""
    p, err = _coach()
    if err:
        return err
    from admin import repositories as admin_repo
    category = (request.args.get("category") or "").strip() or None
    month = (request.args.get("month") or "").strip() or None
    with session_scope() as s:
        data = admin_repo.earnings_clients(s, club_id=p.club_id, category=category,
                                           month=month, coach_user_id=p.user_id)
    return jsonify(data), 200


@coach_bp.get("/financials/transactions")
def get_coach_earnings_transactions():
    """The coach's month → (SERVICE and/or CLIENT) → TRANSACTIONS drill — the same reader as admin, coach-
    scoped. `?category=…` and/or `?user_id=…`, `&month=…`. Each row drills to the shared transaction record."""
    p, err = _coach()
    if err:
        return err
    from admin import repositories as admin_repo
    category = (request.args.get("category") or "").strip() or None
    user_id = (request.args.get("user_id") or "").strip() or None
    month = (request.args.get("month") or "").strip() or None
    with session_scope() as s:
        data = admin_repo.earnings_transactions(s, club_id=p.club_id, category=category,
                                                user_id=user_id, month=month, coach_user_id=p.user_id)
    return jsonify(data), 200


@coach_bp.get("/clients/<client_user_id>/invoice")
def get_client_invoice(client_user_id):
    """The printable coaching invoice for one client + month (paid/owed/written-off lines + totals).
    Coach-scoped — only this coach's coaching with this client."""
    p, err = _coach()
    if err:
        return err
    month = (request.args.get("month") or "").strip() or None
    from billing import commission as comm
    with session_scope() as s:
        inv = comm.client_invoice_data(s, club_id=p.club_id, coach_user_id=p.user_id,
                                       client_user_id=client_user_id, month=month)
    return jsonify(invoice=inv), 200


@coach_bp.post("/clients/<client_user_id>/issue-invoice")
def issue_client_invoice(client_user_id):
    """Month-end: send this client their coaching statement (notification + pay link to their unified
    statement). Returns {invoice, owed_minor, notified}."""
    p, err = _coach()
    if err:
        return err
    month = (request.args.get("month") or (_body().get("month")) or "").strip() or None
    from billing import commission as comm
    with session_scope() as s:
        res = comm.issue_client_invoice(s, club_id=p.club_id, coach_user_id=p.user_id,
                                        client_user_id=client_user_id, month=month)
    return jsonify(res), 200


# ---------------------------------------------------------------------------
# business cockpit — the coach's read-only "how is my business doing?" overview.
# One payload: KPIs (activity + earnings net-of-commission + fill rate + clients),
# a last-6-months trend, top clients, upcoming sessions. Coach-scoped (club_id +
# user_id from the principal — never the body), so a coach only ever sees THEIR
# OWN numbers. Pure SQL aggregation; billing/commission reads degrade gracefully.
# ---------------------------------------------------------------------------

@coach_bp.get("/cockpit")
def get_cockpit():
    p, err = _coach()
    if err:
        return err
    month = (request.args.get("month") or "").strip() or None
    with session_scope() as s:
        data = repo.cockpit(s, club_id=p.club_id, user_id=p.user_id, month=month)
    return jsonify(data), 200


@coach_bp.get("/commission")
def get_commission():
    """The club's commission on this coach's lessons — READ-ONLY (the owner sets it in admin;
    the coach only sees it, greyed)."""
    p, err = _coach()
    if err:
        return err
    with session_scope() as s:
        data = repo.coach_commission_overview(s, club_id=p.club_id, user_id=p.user_id)
    return jsonify(data), 200


@coach_bp.get("/activity")
def get_activity():
    """The coach's chronological transaction log — lessons earned on, refund clawbacks, and each
    client's arrears (accrued/paid/written-off). Scoped to THIS coach."""
    p, err = _coach()
    if err:
        return err
    try:
        limit = max(1, min(200, int(request.args.get("limit") or 120)))
    except (TypeError, ValueError):
        limit = 120
    from billing import activity as act
    with session_scope() as s:
        rows = act.transaction_log(s, club_id=p.club_id, scope="coach",
                                   user_id=p.user_id, limit=limit)
    return jsonify(activity=rows, count=len(rows)), 200


# ---------------------------------------------------------------------------
# disputes — refund requests on THIS coach's coaching services (coach decides; club oversees)
#   GET  /api/coach/refund-requests?status=      -> {requests:[…]}
#   POST /api/coach/refund-requests/<id>/approve -> execute the refund + clawback, mark refunded
#   POST /api/coach/refund-requests/<id>/decline -> decline (no money moves)
# A coach may ONLY act on a request routed to them (coach_user_id = them); anything else is 403.
# ---------------------------------------------------------------------------

_REFUND_ERR = {
    "NOT_FOUND": (404, "That request no longer exists."),
    "FORBIDDEN": (403, "That dispute isn’t yours to decide."),
    "NOT_PENDING": (409, "That request has already been decided."),
    "NOT_REFUNDABLE": (409, "That order can’t be refunded."),
    "refund_failed": (502, "The refund could not be processed — the request is still pending."),
}


@coach_bp.get("/refund-requests")
def coach_refund_requests():
    p, err = _coach()
    if err:
        return err
    status = (request.args.get("status") or "").strip() or None
    from billing import refunds
    with session_scope() as s:
        rows = refunds.list_refund_requests_coach(
            s, club_id=p.club_id, coach_user_id=p.user_id, status=status)
    return jsonify(requests=rows, count=len(rows)), 200


@coach_bp.post("/refund-requests/<request_id>/approve")
def coach_approve_refund(request_id):
    """The coach approves a dispute on their coaching service → the refund (and the proportional
    commission clawback) executes. Scoped to THEIR own disputes."""
    p, err = _coach()
    if err:
        return err
    b = _body()
    from billing import refunds
    with session_scope() as s:
        req, ecode, emsg = refunds.approve_refund_request(
            s, club_id=p.club_id, request_id=request_id, decided_by=p.user_id,
            amount_minor=b.get("amount_minor"), note=b.get("note"),
            require_coach_user_id=p.user_id)
    if ecode:
        code, msg = _REFUND_ERR.get(ecode, (502, "The refund could not be completed."))
        return jsonify(error=ecode, message=(emsg or msg)), code
    return jsonify(refund_request=req), 200


@coach_bp.post("/refund-requests/<request_id>/decline")
def coach_decline_refund(request_id):
    """The coach declines a dispute on their coaching service (no money moves). Own disputes only."""
    p, err = _coach()
    if err:
        return err
    b = _body()
    from billing import refunds
    with session_scope() as s:
        req, ecode = refunds.decline_refund_request(
            s, club_id=p.club_id, request_id=request_id, decided_by=p.user_id,
            note=b.get("note"), require_coach_user_id=p.user_id)
    if ecode:
        code, msg = _REFUND_ERR.get(ecode, (400, "Could not decline the request."))
        return jsonify(error=ecode, message=msg), code
    return jsonify(refund_request=req), 200

# api_v1/routes.py — /api/v1/clubs/<club_slug>/…  (docs/specs/PUBLIC-API.md). Court hire.
#
# Every endpoint: the club comes from the URL; the caller must be allowed to act in it (auth.principal
# — memberships, and an outside login's allowed clubs); errors are
#   HTTP status + {"error": {"code": "SLOT_TAKEN", "message": "...", "details": {...}}}
# and money is always {"amount_minor": int, "currency": "ZAR"}.
#
# NO RULES HERE. Booking/pricing/payment rules live in the lanes (diary.bookings, diary.pricing,
# diary.booking_request, billing.checkout); this file maps the contract onto them. If a rule seems to
# be needed here, it belongs in the lane — the member app's route must obey it too.

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, jsonify, request
from sqlalchemy import text

from db import session_scope

log = logging.getLogger("api_v1")

api_v1_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1/clubs/<club_slug>")

# The public names for how a booking is paid, mapped onto the settlement modes the lanes use.
PAYMENT_METHODS = {
    "card": "online",
    "at_club": "at_court",
    "account": "monthly_account",
    "pack": "token",
    "membership": "membership_covered",
}
_METHOD_FOR_MODE = {v: k for k, v in PAYMENT_METHODS.items()}

# Default messages for lane error codes that arrive without one.
_MESSAGES = {
    "SLOT_TAKEN": "that court was just taken — pick another time or court",
    "NOT_FOUND": "not found",
}


# ---------------------------------------------------------------------------
# contract helpers
# ---------------------------------------------------------------------------

def error(code, message, status, **details):
    return jsonify(error={"code": code, "message": message, "details": details}), status


def _from_lane(res):
    """A lane's {ok: False, error, status, message, ...} → the v1 error shape."""
    code = str(res.get("error") or "ERROR").upper()
    details = {k: v for k, v in res.items() if k not in ("ok", "error", "status", "message")}
    return error(code, res.get("message") or _MESSAGES.get(code, code.replace("_", " ").lower()),
                 int(res.get("status") or 400), **details)


def money(amount_minor, currency):
    return None if amount_minor is None else {"amount_minor": int(amount_minor), "currency": currency}


def _club(s, slug):
    row = s.execute(
        text("SELECT c.id, c.slug, c.name, c.currency_code, c.timezone, "
             "       p.booking_window_days, p.cancellation_cutoff_hours, "
             "       COALESCE(p.allow_online_payment, false) AS allow_online, "
             "       COALESCE(p.allow_pay_at_court, true) AS allow_at_club, "
             "       COALESCE(p.allow_monthly_account, true) AS allow_account, "
             "       COALESCE(p.allowed_return_origins, '{}') AS return_origins, "
             "       b.primary_color, b.accent_color, b.logo_url, b.domain "
             "FROM club.club c LEFT JOIN club.policy p ON p.club_id = c.id "
             "LEFT JOIN club.branding b ON b.club_id = c.id "
             "WHERE c.slug = :s AND COALESCE(c.is_template, false) = false"),
        {"s": (slug or "").strip().lower()},
    ).mappings().first()
    return dict(row) if row else None


def endpoint(fn):
    """Resolve the club from the URL and the caller from the token, and refuse a caller who may not
    act in that club. Hands `club` (dict) and `p` (Principal) to the endpoint."""
    @wraps(fn)
    def wrapper(club_slug, **kw):
        from auth import resolve_principal
        with session_scope() as s:
            club = _club(s, club_slug)
        if not club:
            return error("CLUB_NOT_FOUND", "no such club", 404)
        p = resolve_principal(request, club_hint=str(club["id"]))
        if p is None or not p.authenticated or p.method != "jwt":
            return error("UNAUTHENTICATED", "sign in to use this club", 401)
        if str(p.club_id or "") != str(club["id"]) or not p.role:
            return error("NOT_ALLOWED_AT_THIS_CLUB", "you can't book at this club", 403)
        return fn(club, p, **kw)
    return wrapper


def _body():
    return request.get_json(silent=True) or {}


# ---- idempotent writes ------------------------------------------------------

def _idem_begin(s, club, p, name):
    """Reserve the caller's Idempotency-Key for this write. Returns (key, None) to proceed, or
    (None, response) to return straight away — a replay of the stored first response, or a refusal."""
    key = (request.headers.get("Idempotency-Key") or "").strip()
    if not key or len(key) > 200:
        return None, error("IDEMPOTENCY_KEY_REQUIRED",
                           "send an Idempotency-Key header (any unique string, max 200 chars)", 400)
    reserved = s.execute(
        text("INSERT INTO api.idempotency (club_id, user_id, idem_key, endpoint) "
             "VALUES (:c, :u, :k, :e) ON CONFLICT DO NOTHING RETURNING 1"),
        {"c": str(club["id"]), "u": str(p.user_id), "k": key, "e": name},
    ).first()
    if reserved:
        return key, None
    prev = s.execute(
        text("SELECT endpoint, status_code, body FROM api.idempotency "
             "WHERE club_id = :c AND user_id = :u AND idem_key = :k"),
        {"c": str(club["id"]), "u": str(p.user_id), "k": key},
    ).mappings().first()
    if prev and prev["endpoint"] != name:
        return None, error("IDEMPOTENCY_KEY_REUSED", "that Idempotency-Key was used for a different request", 422)
    if not prev or prev["status_code"] is None:
        return None, error("REQUEST_IN_PROGRESS", "the first request with this key hasn't finished", 409)
    body = prev["body"] if isinstance(prev["body"], dict) else json.loads(prev["body"] or "{}")
    return None, (jsonify(body), prev["status_code"])


def _idem_finish(s, club, p, key, status, body):
    s.execute(text("UPDATE api.idempotency SET status_code = :sc, body = CAST(:b AS jsonb) "
                   "WHERE club_id = :c AND user_id = :u AND idem_key = :k"),
              {"sc": status, "b": json.dumps(body, default=str), "c": str(club["id"]),
               "u": str(p.user_id), "k": key})


def _idem_release(s, club, p, key):
    """A refused write stores nothing, so the same key can be retried for real once fixed."""
    s.execute(text("DELETE FROM api.idempotency WHERE club_id = :c AND user_id = :u AND idem_key = :k"),
              {"c": str(club["id"]), "u": str(p.user_id), "k": key})


# ---- the booking as the API shows it ------------------------------------------

def _booking_view(s, club, booking_id):
    row = s.execute(
        text("SELECT b.id, b.status, b.booking_type, b.resource_id, r.name AS court_name, "
             "       b.product_id, b.starts_at, b.ends_at, b.held_until, b.settlement_mode, "
             "       b.booked_by_user_id, b.order_id, o.amount_minor, o.status AS order_status, "
             "       o.currency_code "
             "FROM diary.booking b JOIN diary.resource r ON r.id = b.resource_id "
             "LEFT JOIN billing.\"order\" o ON o.id = b.order_id "
             "WHERE b.club_id = :c AND b.id = CAST(:b AS uuid)"),
        {"c": str(club["id"]), "b": str(booking_id)},
    ).mappings().first()
    if not row:
        return None
    method = _METHOD_FOR_MODE.get(row["settlement_mode"] or "", row["settlement_mode"])
    due = (row["order_status"] in ("awaiting_payment", "open") and (row["amount_minor"] or 0) > 0)
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "court": {"id": str(row["resource_id"]), "name": row["court_name"]},
        "service_id": str(row["product_id"]) if row["product_id"] else None,
        "starts_at": row["starts_at"].isoformat(),
        "ends_at": row["ends_at"].isoformat(),
        "duration_minutes": int((row["ends_at"] - row["starts_at"]).total_seconds() // 60),
        "payment": {
            "method": method,
            "amount": money(row["amount_minor"] or 0, row["currency_code"] or club["currency_code"]),
            "status": ("due" if due else ("paid" if row["order_status"] == "paid" else
                                          (row["order_status"] or "none"))),
            "card_payment_due": bool(due and row["settlement_mode"] == "online"),
        },
        "hold_expires_at": (row["held_until"].isoformat()
                            if row["status"] == "held" and row["held_until"] else None),
        "_owner": str(row["booked_by_user_id"]) if row["booked_by_user_id"] else None,
    }


def _public(view):
    return {k: v for k, v in view.items() if not k.startswith("_")}


def _own_booking(s, club, p, booking_id):
    """The caller's OWN court booking, or None. Someone else's reads as not found, never forbidden —
    the API does not confirm that a booking id exists."""
    try:
        v = _booking_view(s, club, booking_id)
    except Exception:
        return None
    if not v or v["_owner"] != str(p.user_id):
        return None
    return v


# ---------------------------------------------------------------------------
# club + services + availability (read)
# ---------------------------------------------------------------------------

@api_v1_bp.get("")
@endpoint
def club_info(club, p):
    from billing.checkout import payments_enabled
    methods = []
    if club["allow_online"] and payments_enabled():
        methods.append("card")
    if club["allow_at_club"]:
        methods.append("at_club")
    if club["allow_account"]:
        methods.append("account")
    return jsonify(
        slug=club["slug"], name=club["name"], timezone=club["timezone"], currency=club["currency_code"],
        booking_window_days=club["booking_window_days"],
        cancellation_cutoff_hours=club["cancellation_cutoff_hours"],
        payment_methods=methods,
        branding={"primary_color": club["primary_color"], "accent_color": club["accent_color"],
                  "logo_url": club["logo_url"]},
    ), 200


@api_v1_bp.get("/court-services")
@endpoint
def court_services(club, p):
    from diary import pricing
    from diary import equipment as eq
    with session_scope() as s:
        svcs = pricing.services_for(s, club_id=str(club["id"]), kind="court_booking", audience="member")
        peak = {str(r["id"]): r["peak_amount_minor"] for r in s.execute(
            text("SELECT id, peak_amount_minor FROM billing.price WHERE club_id = :c "
                 "AND peak_amount_minor IS NOT NULL"), {"c": str(club["id"])}).mappings().all()}
        kit = {sv["product_id"]: eq.list_equipment(s, club_id=str(club["id"]),
                                                   court_product_id=sv["product_id"]) for sv in svcs}
    out = []
    for sv in svcs:
        cur = sv.get("currency_code") or club["currency_code"]
        modes = sv.get("payment_modes")
        out.append({
            "id": sv["product_id"], "name": sv["name"],
            # peak_price: charged instead of price when a slot falls in that court's peak hours —
            # GET /availability prices each slot, so a front end need not work this out itself.
            "durations": [{"minutes": d["duration_minutes"], "price": money(d["amount_minor"], cur),
                           "peak_price": money(peak.get(str(d.get("price_id"))), cur)}
                          for d in sv.get("durations") or []],
            # None = every method the club allows (see GET /)
            "payment_methods": ([_METHOD_FOR_MODE.get(m, m) for m in modes] if modes else None),
            "equipment": [{"id": str(e["id"]), "name": e.get("name"),
                           "price": money(e.get("amount_minor"), e.get("currency_code") or cur)}
                          for e in kit.get(sv["product_id"]) or []],
        })
    return jsonify(services=out), 200


@api_v1_bp.get("/availability")
@endpoint
def availability(club, p):
    """?service=<id>&date=YYYY-MM-DD (or from=&to=)&duration=<minutes>. Every slot carries the price
    THIS caller would pay — 0 when their membership covers it, peak applied."""
    from diary import availability as av, pricing
    q = request.args
    try:
        duration = int(q.get("duration") or 0)
    except ValueError:
        return error("BAD_REQUEST", "duration must be whole minutes", 400)
    if duration <= 0:
        return error("BAD_REQUEST", "duration is required (minutes)", 400)
    d_from = q.get("from") or q.get("date")
    d_to = q.get("to") or q.get("date")
    if not d_from:
        return error("BAD_REQUEST", "date (or from/to) is required", 400)
    with session_scope() as s:
        windows = pricing.active_membership_windows(s, club_id=str(club["id"]), user_id=p.user_id)
        slots = av.compute_availability(
            s, club_id=str(club["id"]), kind="court", date_from=d_from, date_to=d_to,
            duration_minutes=duration, audience="member", any_resource=False,
            membership_covered=bool(windows), membership_windows=windows,
            product_id=q.get("service"), member_user_id=p.user_id)
    return jsonify(slots=[{
        "court": {"id": sl["resource_id"], "name": sl.get("resource_name")},
        "starts_at": sl["start"], "ends_at": sl["end"],
        "price": money(sl.get("price"), club["currency_code"]),
    } for sl in slots]), 200


# ---------------------------------------------------------------------------
# bookings
# ---------------------------------------------------------------------------

def _parse_start(v):
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else None


def _booking_request(b):
    """Parse a book/quote body. Returns (parsed, None) or (None, error response)."""
    starts = _parse_start(b.get("starts_at"))
    if starts is None:
        return None, error("BAD_REQUEST", "starts_at must be an ISO time with a UTC offset", 400)
    try:
        minutes = int(b.get("duration_minutes") or 0)
    except (TypeError, ValueError):
        minutes = 0
    if minutes <= 0:
        return None, error("BAD_REQUEST", "duration_minutes is required", 400)
    method = (b.get("payment_method") or "").strip()
    if method not in PAYMENT_METHODS:
        return None, error("BAD_REQUEST", "payment_method must be one of " + ", ".join(PAYMENT_METHODS), 400)
    return {"starts": starts, "minutes": minutes, "method": method,
            "players": b.get("players") or []}, None


def _lane_create(s, club, p, b, req):
    """ONE call into diary.bookings.create_booking for both booking and quoting, so a quote is
    exactly what a booking would do."""
    from diary import bookings as bookings_mod
    from diary.booking_request import extra_players
    players = req["players"]
    extras = extra_players(s, p, {"booking_type": "court", "extra_clients": players,
                                  "seats": len(players) + 1}, owner_uid=p.user_id, is_staff=False)
    return bookings_mod.create_booking(
        s, club_id=str(club["id"]), booked_by_user_id=p.user_id, role=p.role,
        booking_type="court", resource_id=(b.get("court_id") or "any"),
        starts_at=req["starts"].isoformat(),
        ends_at=(req["starts"] + timedelta(minutes=req["minutes"])).isoformat(),
        settlement_mode=PAYMENT_METHODS[req["method"]], audience="member",
        product_id=b.get("service_id"),
        addons=[{"resource_id": a.get("equipment_id"), "qty": a.get("quantity", 1)}
                for a in (b.get("addons") or []) if isinstance(a, dict)],
        extra_clients=extras or None, seats=(len(players) + 1 if players else None),
        visibility="private")


@api_v1_bp.post("/bookings")
@endpoint
def create_booking(club, p):
    """{service_id, court_id | "any", starts_at (ISO, with offset), duration_minutes,
        payment_method: card|at_club|account|pack|membership, addons?: [{equipment_id, quantity}],
        players?: [{email} | {user_id}]}  +  Idempotency-Key header."""
    from diary.booking_request import apply_min_profile
    b = _body()
    # The key is checked BEFORE anything is written (the first-booking details step below saves).
    if not (request.headers.get("Idempotency-Key") or "").strip():
        return error("IDEMPOTENCY_KEY_REQUIRED",
                     "send an Idempotency-Key header (any unique string, max 200 chars)", 400)
    req, bad = _booking_request(b)
    if bad:
        return bad
    # First booking: the club needs the member's name + phone (the same rule the app applies).
    missing = apply_min_profile(p, b)
    if missing:
        return error("PROFILE_INCOMPLETE", "complete your details first (PATCH /me or send them here)",
                     422, needs_profile=missing)
    with session_scope() as s:
        key, early = _idem_begin(s, club, p, "POST /bookings")
        if early:
            return early
        res = _lane_create(s, club, p, b, req)
        if not res.get("ok"):
            _idem_release(s, club, p, key)
            return _from_lane(res)
        view = _public(_booking_view(s, club, res["booking"]["id"]))
        body = {"booking": view}
        _idem_finish(s, club, p, key, 201, body)
    return jsonify(body), 201


@api_v1_bp.post("/quotes")
@endpoint
def quote(club, p):
    """The same body as POST /bookings → what that booking WOULD be: its court, total, line items and
    how it would be paid (a membership that doesn't cover this slot shows as card/at_club), or the
    exact refusal a booking would get. Nothing is kept and nothing is announced: it runs the real
    booking inside a savepoint that is always rolled back, with emits silenced."""
    from marketing_crm.tracking.client import suppressed
    b = _body()
    req, bad = _booking_request(b)
    if bad:
        return bad
    with session_scope() as s:
        sp = s.begin_nested()
        try:
            with suppressed():
                res = _lane_create(s, club, p, b, req)
                if not res.get("ok"):
                    return _from_lane(res)
                v = _booking_view(s, club, res["booking"]["id"])
                order_id = res["booking"].get("order_id")
                lines = [] if not order_id else [
                    {"description": r["description"], "quantity": int(r["qty"] or 1),
                     "amount": money(r["amount_minor"], v["payment"]["amount"]["currency"])}
                    for r in s.execute(text("SELECT description, qty, amount_minor FROM billing.order_line "
                                            "WHERE order_id = :o ORDER BY created_at, id"),
                                       {"o": str(order_id)}).mappings().all()]
        finally:
            if sp.is_active:
                sp.rollback()
    return jsonify(quote={
        "court": v["court"], "service_id": v["service_id"], "starts_at": v["starts_at"],
        "ends_at": v["ends_at"], "duration_minutes": v["duration_minutes"],
        "payment_method": v["payment"]["method"], "total": v["payment"]["amount"],
        "card_payment_due": v["payment"]["card_payment_due"], "lines": lines,
    }), 200


@api_v1_bp.get("/bookings")
@endpoint
def list_my_bookings(club, p):
    """The caller's OWN court bookings, soonest first. ?from=&to= (ISO dates) optional."""
    q = request.args
    where = ["b.club_id = :c", "b.booked_by_user_id = :u", "b.booking_type = 'court'"]
    params = {"c": str(club["id"]), "u": str(p.user_id)}
    if q.get("from"):
        where.append("b.starts_at >= CAST(:f AS timestamptz)"); params["f"] = q.get("from")
    if q.get("to"):
        where.append("b.starts_at < CAST(:t AS timestamptz) + interval '1 day'"); params["t"] = q.get("to")
    with session_scope() as s:
        ids = s.execute(text("SELECT b.id FROM diary.booking b WHERE " + " AND ".join(where) +
                             " ORDER BY b.starts_at LIMIT 200"), params).scalars().all()
        out = [_public(_booking_view(s, club, i)) for i in ids]
    return jsonify(bookings=out), 200


@api_v1_bp.get("/bookings/<booking_id>")
@endpoint
def get_booking(club, p, booking_id):
    with session_scope() as s:
        v = _own_booking(s, club, p, booking_id)
    if not v:
        return error("NOT_FOUND", "no such booking", 404)
    return jsonify(booking=_public(v)), 200


@api_v1_bp.post("/bookings/<booking_id>/cancel")
@endpoint
def cancel_booking(club, p, booking_id):
    from diary import bookings as bookings_mod
    from iam.permissions import can
    with session_scope() as s:
        v = _own_booking(s, club, p, booking_id)
        if not v:
            return error("NOT_FOUND", "no such booking", 404)
        bk = bookings_mod.get_booking(s, club_id=str(club["id"]), booking_id=booking_id)
        if not bk or not can(p, "cancel_booking", bk):
            return error("NOT_FOUND", "no such booking", 404)
        res = bookings_mod.cancel_booking(s, club_id=str(club["id"]), booking_id=booking_id,
                                          actor_user_id=p.user_id, role=p.role,
                                          reason=_body().get("reason"))
        if not res.get("ok"):
            return _from_lane(res)
        view = _public(_booking_view(s, club, booking_id))
    return jsonify(booking=view, fee=money(res.get("fee_minor") or 0, club["currency_code"])), 200


@api_v1_bp.post("/bookings/<booking_id>/reschedule")
@endpoint
def reschedule(club, p, booking_id):
    """{starts_at?, duration_minutes?, court_id?} — move time and/or court. The same money guards the
    app's reschedule runs (a court move can't cross court services; a covered booking is re-checked
    against the new slot)."""
    from diary import bookings as bookings_mod
    from iam.permissions import can
    b = _body()
    with session_scope() as s:
        v = _own_booking(s, club, p, booking_id)
        if not v:
            return error("NOT_FOUND", "no such booking", 404)
        bk = bookings_mod.get_booking(s, club_id=str(club["id"]), booking_id=booking_id)
        if not bk or not can(p, "reschedule_booking", bk):
            return error("NOT_FOUND", "no such booking", 404)
        starts = _parse_start(b["starts_at"]) if b.get("starts_at") else _parse_start(v["starts_at"])
        if starts is None:
            return error("BAD_REQUEST", "starts_at must be an ISO time with a UTC offset", 400)
        try:
            minutes = int(b.get("duration_minutes") or v["duration_minutes"])
        except (TypeError, ValueError):
            return error("BAD_REQUEST", "duration_minutes must be whole minutes", 400)
        res = bookings_mod.reschedule_booking(
            s, club_id=str(club["id"]), booking_id=booking_id,
            new_starts_at=starts.isoformat(), new_ends_at=(starts + timedelta(minutes=minutes)).isoformat(),
            actor_user_id=p.user_id, role=p.role, scope="this",
            new_court_resource_id=(b.get("court_id") or None))
        if not res.get("ok"):
            return _from_lane(res)
        view = _public(_booking_view(s, club, booking_id))
    return jsonify(booking=view), 200


@api_v1_bp.post("/bookings/<booking_id>/promo")
@endpoint
def apply_promo(club, p, booking_id):
    """{code} → the booking's order, discounted. A refusal says why (expired, not for this service,
    already used…)."""
    from billing import promotions
    code = (_body().get("code") or "").strip()
    if not code:
        return error("BAD_REQUEST", "code is required", 400)
    with session_scope() as s:
        v = _own_booking(s, club, p, booking_id)
        if not v:
            return error("NOT_FOUND", "no such booking", 404)
        order_id = s.execute(text("SELECT order_id FROM diary.booking WHERE id = CAST(:b AS uuid)"),
                             {"b": booking_id}).scalar()
        if not order_id:
            return error("NOTHING_TO_DISCOUNT", "this booking has no charge", 409)
        payer = s.execute(text('SELECT user_id FROM billing."order" WHERE id = :o'),
                          {"o": str(order_id)}).scalar()
        res = promotions.apply_to_order(s, club_id=str(club["id"]), code=code, order_id=str(order_id),
                                        user_id=payer, actor_user_id=p.user_id)
        if not res.get("ok"):
            return error(str(res.get("error") or "PROMO_REFUSED").upper(),
                         res.get("reason") or res.get("message") or "that code can't be used here", 422)
        view = _public(_booking_view(s, club, booking_id))
    return jsonify(booking=view, discount=money(res.get("discount_minor") or 0, club["currency_code"]),
                   label=res.get("label")), 200


def _me(s, club, p):
    from iam import repositories as iam_repo
    from iam.validation import missing_min_fields
    from billing.me import member_plan
    from billing.bundles import wallets_for
    prof = iam_repo.get_profile(s, user_id=p.user_id) or {}
    plan = member_plan(s, club_id=str(club["id"]), user_id=p.user_id) or {}
    packs = wallets_for(s, club_id=str(club["id"]), user_id=p.user_id, active_only=True)
    return {
        "email": prof.get("email") or p.email,
        "first_name": prof.get("first_name"), "surname": prof.get("surname"), "phone": prof.get("phone"),
        "missing_details": [f["field"] for f in missing_min_fields(prof)],
        "membership": {
            "active": bool(plan.get("active")), "name": plan.get("name"),
            "is_trial": bool(plan.get("is_trial")), "trial_days_left": plan.get("trial_days_left"),
            "ends_at": (plan["current_period_end"].isoformat()
                        if hasattr(plan.get("current_period_end"), "isoformat")
                        else plan.get("current_period_end")),
            "courts_free_when": plan.get("membership_window_summary"),
        },
        "packs": [{"id": str(w["id"]), "label": w.get("label"), "service_kind": w.get("service_kind"),
                   "sessions_left": w.get("tokens_remaining"), "minutes_left": w.get("minutes_remaining"),
                   "expires_at": (w["expires_at"].isoformat() if w.get("expires_at") else None)}
                  for w in packs],
    }


@api_v1_bp.get("/me")
@endpoint
def me(club, p):
    with session_scope() as s:
        out = _me(s, club, p)
    return jsonify(me=out), 200


@api_v1_bp.patch("/me")
@endpoint
def update_me(club, p):
    """{first_name?, surname?, phone?, marketing_opt_in?} — the details a first booking needs."""
    from diary.booking_request import apply_min_profile
    b = _body()
    body = {k: b.get(k) for k in ("first_name", "surname", "phone", "marketing_opt_in") if k in b}
    if p.role == "member":
        apply_min_profile(p, body)
    else:   # staff aren't asked at booking time, but may still keep their own details current
        from iam import repositories as iam_repo
        fields = {k: (body.get(k) or "").strip() for k in ("first_name", "surname", "phone")
                  if (body.get(k) or "").strip()}
        if fields:
            with session_scope() as s:
                iam_repo.patch_profile(s, user_id=p.user_id, fields=fields)
    with session_scope() as s:
        out = _me(s, club, p)
    return jsonify(me=out), 200


def _allowed_return(club, url):
    """A partner's return_url must be on the club's allow-list (scheme://host), else the checkout
    would bounce a player — mid-payment — to any site the caller names."""
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    origin = f"{parts.scheme}://{parts.netloc}".lower()
    allowed = [o.strip().rstrip("/").lower() for o in (club["return_origins"] or [])]
    if club.get("domain"):
        allowed += [f"https://{club['domain'].lower()}", f"https://www.{club['domain'].lower()}"]
    return parts.scheme in ("https", "http") and origin in allowed


@api_v1_bp.post("/bookings/<booking_id>/checkout")
@endpoint
def checkout(club, p, booking_id):
    """{return_url?} + Idempotency-Key → {provider, redirect_url}. Send the player to redirect_url;
    the booking confirms when the provider tells CourtFlow it was paid (not when they come back)."""
    from billing.checkout import start_checkout
    b = _body()
    base = (os.getenv("APP_BASE_URL") or "https://courtflow-web.onrender.com").rstrip("/")
    ret = (b.get("return_url") or "").strip()
    if ret and not _allowed_return(club, ret):
        return error("RETURN_URL_NOT_ALLOWED", "that return_url isn't on this club's allow-list", 422)
    with session_scope() as s:
        v = _own_booking(s, club, p, booking_id)
        if not v:
            return error("NOT_FOUND", "no such booking", 404)
        order_id = s.execute(text("SELECT order_id FROM diary.booking WHERE id = CAST(:b AS uuid)"),
                             {"b": booking_id}).scalar()
        if not order_id or not v["payment"]["card_payment_due"]:
            return error("NOTHING_TO_PAY", "this booking has no card payment due", 409)
        key, early = _idem_begin(s, club, p, "POST /bookings/checkout")
        if early:
            return early
        sep = "&" if "?" in ret else "?"
        success = f"{ret}{sep}booking={booking_id}&result=success" if ret else \
            f"{base}/pay-return.html?order={order_id}&r=success"
        cancel = f"{ret}{sep}booking={booking_id}&result=cancel" if ret else \
            f"{base}/pay-return.html?order={order_id}&r=cancel"
        r = start_checkout(s, principal=p, order_id=str(order_id), success_url=success, cancel_url=cancel)
        if not r.get("ok"):
            _idem_release(s, club, p, key)
            return _from_lane({k: v for k, v in r.items() if k != "provider_body"})
        body = {"provider": r["provider"], "redirect_url": r["redirect_url"],
                "expires_at": v["hold_expires_at"]}
        _idem_finish(s, club, p, key, 200, body)
    return jsonify(body), 200

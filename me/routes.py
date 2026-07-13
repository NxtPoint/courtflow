# me/routes.py — the /api/me/* surface: the client "My Account" self-service lane.
# Blueprint me_bp. Registered in app.py (one _try_register line).
#
# Thin routes (admin/diary style): resolve the principal (auth.resolve_principal), gate with
# iam.permissions.can(), pull club_id + user_id FROM THE PRINCIPAL (never the body — docs/02 §1),
# call iam.repositories, map dicts to JSON. Every action is scoped to the CALLER's own row:
# a member only ever reads/writes their OWN profile and their OWN dependents.
#
# Endpoints (spec §2.3, §3.3):
#   GET   /api/me/profile               -> the caller's demographics (email read-only)
#   PATCH /api/me/profile               -> update only editable fields (email immutable)
#   GET   /api/me/dependents            -> the caller's active children/dependents
#   POST  /api/me/dependents            -> add a child (login-less iam.user + iam.dependent)
#   PATCH /api/me/dependents/<id>       -> edit one the caller owns
#   DELETE /api/me/dependents/<id>      -> soft-remove one the caller owns
#
# Financials + refund requests (client-financials lane — spec §4, §6):
#   GET   /api/me/financials            -> plan + usage-this-month + spend + account + next_charge
#   GET   /api/me/orders                -> recent paid/refunded orders (receipts; refund eligibility)
#   GET   /api/me/refund-requests       -> the caller's own refund requests
#   POST  /api/me/refund-requests       -> raise a refund request on a paid order the caller owns
#   POST  /api/me/refund-requests/<id>/cancel -> withdraw a still-pending request

import logging
from datetime import date, datetime

from flask import Blueprint, jsonify, request

from auth import resolve_principal
from db import session_scope
from iam.permissions import can
from iam import repositories as iam_repo

log = logging.getLogger("me.routes")

me_bp = Blueprint("me", __name__, url_prefix="/api/me")


# ---------------------------------------------------------------------------
# auth helpers
# ---------------------------------------------------------------------------

def _principal():
    """Resolve an authenticated, club-scoped principal with a real user_id, or
    (None, error_response). OPS principals carry no user_id → not a client self-service path."""
    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return None, (jsonify(error="unauthorized"), 401)
    if not p.user_id:
        return None, (jsonify(error="unauthorized"), 401)
    if p.club_id is None:
        return None, (jsonify(error="no_club_scope"), 400)
    return p, None


def _body():
    return request.get_json(silent=True) or {}


# ---------------------------------------------------------------------------
# validation (spec §2.1)
# ---------------------------------------------------------------------------

def _validate_name(v):
    v = (v or "").strip()
    if not (1 <= len(v) <= 80):
        return None, "1–80 characters"
    return v, None


def _validate_phone(v):
    v = (v or "").strip()
    if v == "":
        return "", None
    # E.164-ish: +, digits, spaces, dashes, parens; 7–20 chars.
    import re
    if not re.fullmatch(r"[+0-9 ()\-]{7,20}", v):
        return None, "7–20 digits (may include + and spaces)"
    return v, None


def _validate_dob(v):
    """Optional ISO date; not in the future; sanity floor 1900. Returns (value, err)."""
    if v in (None, ""):
        return None, None
    try:
        d = date.fromisoformat(str(v)[:10])
    except Exception:
        return None, "must be a date (YYYY-MM-DD)"
    today = datetime.utcnow().date()
    if d > today:
        return None, "cannot be in the future"
    if d.year < 1900:
        return None, "too far in the past"
    return d.isoformat(), None


def _validate_profile_patch(b):
    """Validate the editable demographics; return (clean_fields, errors). Only present keys are
    considered. email is silently dropped (never written — it is the identity key)."""
    fields, errors = {}, {}

    if "first_name" in b:
        v, e = _validate_name(b.get("first_name"))
        if e:
            errors["first_name"] = e
        else:
            fields["first_name"] = v
    if "surname" in b:
        # surname allowed empty (≤80 when present)
        sv = (b.get("surname") or "").strip()
        if len(sv) > 80:
            errors["surname"] = "≤ 80 characters"
        else:
            fields["surname"] = sv
    if "phone" in b:
        v, e = _validate_phone(b.get("phone"))
        if e:
            errors["phone"] = e
        else:
            fields["phone"] = v
    if "dob" in b:
        v, e = _validate_dob(b.get("dob"))
        if e:
            errors["dob"] = e
        else:
            fields["dob"] = v
    if "emergency_contact_phone" in b:
        v, e = _validate_phone(b.get("emergency_contact_phone"))
        if e:
            errors["emergency_contact_phone"] = e
        else:
            fields["emergency_contact_phone"] = v

    # free-text demographics: length-capped, trimmed.
    _caps = {"address_line1": 120, "address_line2": 120, "city": 80, "postal_code": 16,
             "country": 64, "emergency_contact_name": 80}
    for k, cap in _caps.items():
        if k in b:
            sv = (b.get(k) or "").strip()
            if len(sv) > cap:
                errors[k] = f"≤ {cap} characters"
            else:
                fields[k] = sv

    if "marketing_opt_in" in b:
        fields["marketing_opt_in"] = bool(b.get("marketing_opt_in"))

    return fields, errors


# ---------------------------------------------------------------------------
# profile  (spec §2.3)
# ---------------------------------------------------------------------------

@me_bp.get("/profile")
def get_profile():
    p, err = _principal()
    if err:
        return err
    if not can(p, "manage_own_profile", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    with session_scope() as s:
        prof = iam_repo.get_profile(s, user_id=p.user_id)
    if prof is None:
        return jsonify(error="NOT_FOUND"), 404
    prof["role"] = p.role  # for the UI; not persisted, not editable
    return jsonify(prof), 200


@me_bp.patch("/profile")
def patch_profile():
    p, err = _principal()
    if err:
        return err
    if not can(p, "manage_own_profile", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    b = _body()
    fields, errors = _validate_profile_patch(b)
    if errors:
        return jsonify(error="VALIDATION", fields=errors), 422
    # email / role / club / clerk_user_id are never accepted — patch_profile whitelists strictly.
    with session_scope() as s:
        prof = iam_repo.patch_profile(s, user_id=p.user_id, fields=fields)
    if prof is None:
        return jsonify(error="NOT_FOUND"), 404
    # On a marketing-consent change, best-effort emit consent_updated (guarded — same pattern as
    # admin._send_coach_invite_email; never hard-depends on marketing_crm).
    if "marketing_opt_in" in fields:
        try:
            from marketing_crm.tracking import emit
            emit("consent_updated", {"club_id": str(p.club_id), "email": prof.get("email"),
                                     "marketing_opt_in": bool(fields["marketing_opt_in"])})
        except Exception:
            log.debug("consent_updated emit skipped (tracking unavailable)")
    prof["role"] = p.role
    return jsonify(prof), 200


# ---------------------------------------------------------------------------
# acquisition  (Google Ads / Client-360 first-touch attribution — gclid capture)
# ---------------------------------------------------------------------------

@me_bp.post("/acquisition")
def record_my_acquisition():
    """Persist the caller's FIRST-TOUCH ad/UTM attribution (gclid, utm_*) onto core.acquisition.
    Fired once by frontend/js/attribution.js after the visitor signs in. Best-effort + idempotent
    (first-touch wins in the repo) — always returns 200 so the client can clear its buffer even if
    the CRM core is momentarily unavailable. No permission gate: a user records only their OWN
    attribution (keyed off the JWT principal; the client never asserts a user_id)."""
    p, err = _principal()
    if err:
        return err
    b = _body()
    attr = {
        "gclid": b.get("gclid"),
        "fbclid": b.get("fbclid"),
        "source": b.get("utm_source") or b.get("source"),
        "medium": b.get("utm_medium") or b.get("medium"),
        "campaign": b.get("utm_campaign") or b.get("campaign"),
        "term": b.get("utm_term") or b.get("term"),
        "content": b.get("utm_content") or b.get("content"),
        "referrer": b.get("referrer"),
        "landing_page": b.get("landing_page"),
    }
    stored = False
    try:
        from core.repositories.acquisition import record_acquisition
        with session_scope() as s:
            row = record_acquisition(s, iam_user_id=p.user_id, email=p.email,
                                     club_id=p.club_id, attr=attr)
            stored = row is not None
    except Exception:
        log.debug("acquisition capture skipped (core unavailable/benign)", exc_info=False)
    return jsonify(ok=True, stored=stored), 200


# ---------------------------------------------------------------------------
# dependents / children  (spec §3.3)
# ---------------------------------------------------------------------------

@me_bp.get("/dependents")
def list_dependents():
    p, err = _principal()
    if err:
        return err
    if not can(p, "add_junior", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    with session_scope() as s:
        rows = iam_repo.list_dependents(s, club_id=p.club_id, guardian_user_id=p.user_id)
    return jsonify(dependents=rows, count=len(rows)), 200


@me_bp.post("/dependents")
def create_dependent():
    p, err = _principal()
    if err:
        return err
    if not can(p, "add_junior", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    b = _body()
    first_name, e = _validate_name(b.get("first_name"))
    if e:
        return jsonify(error="VALIDATION", fields={"first_name": e}), 422
    dob, e = _validate_dob(b.get("dob"))
    if e:
        return jsonify(error="VALIDATION", fields={"dob": e}), 422
    relationship = (b.get("relationship") or "child").strip()
    if relationship not in ("child", "spouse", "partner", "other"):
        relationship = "child"
    with session_scope() as s:
        dep = iam_repo.create_dependent(
            s, club_id=p.club_id, guardian_user_id=p.user_id,
            first_name=first_name, surname=(b.get("surname") or "").strip() or None,
            dob=dob, relationship=relationship,
            is_minor=bool(b.get("is_minor", True)),
            notes=(b.get("notes") or "").strip() or None,
        )
    # Best-effort: dependent_added (marketing; guardian email only — NEVER child PII, per the contract).
    try:
        from marketing_crm.tracking import emit
        emit("dependent_added", {"club_id": str(p.club_id), "email": p.email,
                                 "relationship": relationship, "is_minor": bool(b.get("is_minor", True))})
    except Exception:
        log.debug("dependent_added emit skipped (tracking unavailable)")
    return jsonify(dependent=dep), 201


@me_bp.patch("/dependents/<dependent_id>")
def update_dependent(dependent_id):
    p, err = _principal()
    if err:
        return err
    if not can(p, "add_junior", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    b = _body()
    # Validate the present, editable fields.
    errors = {}
    fields = {}
    if "first_name" in b:
        v, e = _validate_name(b.get("first_name"))
        if e:
            errors["first_name"] = e
        else:
            fields["first_name"] = v
    if "surname" in b:
        fields["surname"] = (b.get("surname") or "").strip() or None
    if "dob" in b:
        v, e = _validate_dob(b.get("dob"))
        if e:
            errors["dob"] = e
        else:
            fields["dob"] = v
    if "relationship" in b:
        rel = (b.get("relationship") or "child").strip()
        fields["relationship"] = rel if rel in ("child", "spouse", "partner", "other") else "child"
    if "is_minor" in b:
        fields["is_minor"] = bool(b.get("is_minor"))
    if "notes" in b:
        fields["notes"] = (b.get("notes") or "").strip() or None
    if errors:
        return jsonify(error="VALIDATION", fields=errors), 422
    with session_scope() as s:
        dep = iam_repo.update_dependent(s, club_id=p.club_id, guardian_user_id=p.user_id,
                                        dependent_id=dependent_id, fields=fields)
    if dep is None:
        return jsonify(error="NOT_FOUND"), 404  # not the caller's dependent (or doesn't exist)
    return jsonify(dependent=dep), 200


@me_bp.delete("/dependents/<dependent_id>")
def delete_dependent(dependent_id):
    p, err = _principal()
    if err:
        return err
    if not can(p, "add_junior", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    with session_scope() as s:
        ok = iam_repo.deactivate_dependent(s, club_id=p.club_id, guardian_user_id=p.user_id,
                                           dependent_id=dependent_id)
    if not ok:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(ok=True), 200


# ---------------------------------------------------------------------------
# financials  (spec §4) — gate: view_own_ledger (member+, already defined)
# ---------------------------------------------------------------------------

@me_bp.get("/financials")
def get_financials():
    """Current plan + usage-this-month + spend (this month + N-month history) + account
    balance + next charge. STRICTLY member-scoped (the caller's club_id + user_id from the
    principal). Reads live billing.*/diary.* via billing.me (each sub-query guarded)."""
    p, err = _principal()
    if err:
        return err
    if not can(p, "view_own_ledger", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    from billing import me as billing_me
    with session_scope() as s:
        data = billing_me.member_financials(s, club_id=p.club_id, user_id=p.user_id)
    return jsonify(data), 200


@me_bp.get("/activity-summary")
def get_activity_summary():
    """The month-at-a-glance headline for the home + Client 360 rollup: sessions played
    (lessons/court/classes) + minutes + spend-by-service + billed/paid/outstanding + the weekly
    chart buckets. `?month=YYYY-MM` (default this month). Member-scoped, guarded."""
    p, err = _principal()
    if err:
        return err
    if not can(p, "view_own_ledger", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    month = (request.args.get("month") or "").strip() or None
    from billing import me as billing_me
    with session_scope() as s:
        data = billing_me.activity_summary(s, club_id=p.club_id, user_id=p.user_id, month=month)
    return jsonify(data), 200


@me_bp.get("/360")
def my_360():
    """The caller's own unified Client 360 record — the SAME cross-lane composer the admin person-360
    and the coach client record derive from (golden rule: one data layer, views off it). Client scope:
    read-only + pay / request-refund, no staff edit actions."""
    p, err = _principal()
    if err:
        return err
    month = (request.args.get("month") or "").strip() or None
    from client360 import get_client_360
    with session_scope() as s:
        data = get_client_360(s, club_id=p.club_id, user_id=p.user_id, scope="client", month=month)
    if data is None:
        return jsonify(error="not_a_member"), 404
    return jsonify(person=data), 200


@me_bp.get("/plan")
def get_plan():
    """The caller's current plan only (type, is_trial, trial_days_left, current_period_end) — a
    cheap read for the free-week banner + the booking flow's covered-court label."""
    p, err = _principal()
    if err:
        return err
    if not can(p, "view_own_ledger", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    from billing import me as billing_me
    with session_scope() as s:
        data = billing_me.member_plan(s, club_id=p.club_id, user_id=p.user_id)
    return jsonify(data), 200


@me_bp.post("/membership/cancel")
def cancel_my_membership():
    """The caller cancels their OWN active membership → courts revert to PAYG. The paid term isn't
    refunded here (that's a separate refund request). Idempotent."""
    p, err = _principal()
    if err:
        return err
    if not can(p, "view_own_ledger", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    from billing import membership as membership_repo
    with session_scope() as s:
        res = membership_repo.cancel_membership(s, club_id=p.club_id, user_id=p.user_id)
    return jsonify(ok=True, **res), 200


@me_bp.get("/statement")
def get_statement():
    """The caller's UNIFIED statement (docs/specs/UNIFIED-STATEMENT.md): every owed service as ONE line
    (the unpaid orders), with the single reconciled total they owe. One source of truth — no double
    count. {items:[{order_id, description, kind, amount_minor, settlement_mode, pay_label, created_at}],
    count, total_owed_minor, currency}. STRICTLY member-scoped."""
    p, err = _principal()
    if err:
        return err
    if not can(p, "view_own_ledger", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    from billing import statement as statement_repo
    with session_scope() as s:
        data = statement_repo.statement(s, club_id=p.club_id, user_id=p.user_id)
    return jsonify(data), 200


@me_bp.get("/activity")
def get_activity():
    """The client's monthly ACTIVITY — answers 'what did I book', 'what did I spend on what', and
    'what's outstanding'. ?month=YYYY-MM (default this month). Bookings are the sessions dated in the
    month; spend is money PAID that month by category; outstanding is the current (running) balance."""
    p, err = _principal()
    if err:
        return err
    if not can(p, "view_own_ledger", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    month = (request.args.get("month") or "").strip() or None
    from billing import me as billing_me
    from billing import statement as statement_repo
    from diary import bookings as bookings_mod, classes as classes_mod
    from datetime import date, timedelta
    start, nxt = billing_me._month_bounds(month)
    last = (date.fromisoformat(nxt) - timedelta(days=1)).isoformat()
    with session_scope() as s:
        spend = billing_me.spend_by_category(s, club_id=p.club_id, user_id=p.user_id, month=month)
        st = statement_repo.statement(s, club_id=p.club_id, user_id=p.user_id)
        bk = bookings_mod.list_bookings(s, club_id=p.club_id, role="member", user_id=p.user_id,
                                        date_from=start, date_to=last, limit=200)
        enr = classes_mod.list_my_enrolments(s, club_id=p.club_id, user_id=p.user_id)
    items = []
    for b in bk:
        items.append({"kind": (b.get("booking_type") or "court"), "when": b.get("starts_at"),
                      "resource_name": b.get("resource_name"), "coach_name": b.get("coach_name"),
                      "status": b.get("status"),
                      "booking_id": str(b["id"]) if b.get("id") else None})
    for e in enr:
        when = e.get("starts_at") or ""
        if not (start <= when[:10] <= last):
            continue
        items.append({"kind": "class", "when": when, "resource_name": e.get("class_name"),
                      "coach_name": e.get("coach_display"), "status": e.get("status"),
                      "enrolment_id": str(e["enrolment_id"]) if e.get("enrolment_id") else None})
    items.sort(key=lambda x: x.get("when") or "", reverse=True)
    return jsonify(month=start[:7], bookings=items, spend=spend,
                   outstanding={"total_owed_minor": st.get("total_owed_minor", 0),
                                "count": st.get("count", 0), "currency": st.get("currency")}), 200


@me_bp.post("/statement/pay")
def pay_statement():
    """Settle the caller's statement ONLINE. Body {order_ids?} settles a SUBSET (default = everything
    owed) via ONE Yoco checkout: a settlement order covering the chosen owed orders. On payment each
    covered order is marked paid once + its commission accrues (the webhook fan-out). Returns
    {order_id, amount_minor, currency, items} → the page calls Pay.startYocoCheckout(order_id). 409 if
    nothing is owed. STRICTLY member-scoped (the client pays only their own orders)."""
    p, err = _principal()
    if err:
        return err
    if not can(p, "view_own_ledger", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    body = request.get_json(silent=True) or {}
    order_ids = body.get("order_ids") or None
    from billing import statement as statement_repo
    with session_scope() as s:
        res = statement_repo.create_settlement_order(
            s, club_id=p.club_id, user_id=p.user_id, order_ids=order_ids)
    if not res:
        return jsonify(error="NOTHING_OWED", message="You have no outstanding balance to settle."), 409
    return jsonify(res), 201


@me_bp.get("/orders")
def get_orders():
    """The caller's recent paid/refunded orders (receipts) — each row flags whether it is
    refundable (paid + no open request) so the UI can offer 'Request a refund'."""
    p, err = _principal()
    if err:
        return err
    if not can(p, "view_own_ledger", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    from billing import me as billing_me
    with session_scope() as s:
        rows = billing_me.member_orders(s, club_id=p.club_id, user_id=p.user_id)
    return jsonify(orders=rows, count=len(rows)), 200


# ---------------------------------------------------------------------------
# refund requests  (spec §6) — gate: request_refund (member+, NEW verb)
# ---------------------------------------------------------------------------

# error_code -> (http_status, message)
_REFUND_ERR = {
    "NOT_FOUND": (404, "That order was not found on your account."),
    "NOT_REFUNDABLE": (409, "Only paid orders can be refunded."),
    "DUPLICATE": (409, "You already have an open refund request for this order."),
    "NOT_PENDING": (409, "This request can no longer be cancelled."),
}


@me_bp.get("/refund-requests")
def list_refund_requests():
    p, err = _principal()
    if err:
        return err
    if not can(p, "request_refund", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    from billing import refunds
    with session_scope() as s:
        rows = refunds.list_refund_requests(s, club_id=p.club_id, user_id=p.user_id)
    return jsonify(requests=rows, count=len(rows)), 200


@me_bp.post("/refund-requests")
def create_refund_request():
    """Raise a refund REQUEST against one of the caller's PAID orders. The order is validated
    server-side as belonging to the caller (club_id + user_id) and being refundable; at most
    one open request per order. The member never moves money — an admin approves later."""
    p, err = _principal()
    if err:
        return err
    if not can(p, "request_refund", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    b = _body()
    order_id = (b.get("order_id") or "").strip()
    if not order_id:
        return jsonify(error="VALIDATION", fields={"order_id": "required"}), 422
    reason = (b.get("reason") or "").strip() or None
    amount_minor = b.get("amount_minor")
    from billing import refunds
    with session_scope() as s:
        req, ecode = refunds.create_refund_request(
            s, club_id=p.club_id, user_id=p.user_id, order_id=order_id,
            amount_minor=amount_minor, reason=reason)
    if ecode:
        status, msg = _REFUND_ERR.get(ecode, (400, "Could not create the request."))
        return jsonify(error=ecode, message=msg), status
    # Best-effort: refund_requested (transactional → admin). Guarded — never hard-depends on CRM.
    try:
        from marketing_crm.tracking import emit
        emit("refund_requested", {"club_id": str(p.club_id), "email": p.email,
                                  "ref_type": "order", "ref_id": str(order_id),
                                  "amount_minor": req.get("amount_minor"), "reason": reason})
    except Exception:
        log.debug("refund_requested emit skipped (tracking unavailable)")
    return jsonify(refund_request=req), 201


@me_bp.post("/refund-requests/<request_id>/cancel")
def cancel_refund_request(request_id):
    """Member withdraws a still-pending refund request (their own)."""
    p, err = _principal()
    if err:
        return err
    if not can(p, "request_refund", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    from billing import refunds
    with session_scope() as s:
        req, ecode = refunds.cancel_refund_request(
            s, club_id=p.club_id, user_id=p.user_id, request_id=request_id)
    if ecode:
        status, msg = _REFUND_ERR.get(ecode, (400, "Could not cancel the request."))
        return jsonify(error=ecode, message=msg), status
    return jsonify(ok=True, refund_request=req), 200


# ---------------------------------------------------------------------------
# notifications / in-app inbox  (the notifications engine — core.notification)
#   GET  /api/me/notifications?unread=  -> {notifications:[…], unread_count}
#   POST /api/me/notifications/read  body {id?|all:true} -> mark read
# Always scoped to the caller's own (club_id, user_id) — a member only ever sees + marks
# their OWN notifications. No new permission verb: any authenticated club member has an inbox.
# ---------------------------------------------------------------------------

@me_bp.get("/notifications")
def list_notifications_route():
    p, err = _principal()
    if err:
        return err
    unread_only = (request.args.get("unread") or "").strip() in ("1", "true", "yes")
    try:
        limit = max(1, min(100, int(request.args.get("limit") or 30)))
    except (TypeError, ValueError):
        limit = 30
    from core.repositories import notifications as notif_repo
    with session_scope() as s:
        rows = notif_repo.list_notifications(
            s, club_id=p.club_id, user_id=p.user_id, unread_only=unread_only, limit=limit)
        count = notif_repo.unread_count(s, club_id=p.club_id, user_id=p.user_id)
    return jsonify(notifications=rows, unread_count=count, count=len(rows)), 200


@me_bp.post("/notifications/read")
def mark_notifications_read():
    p, err = _principal()
    if err:
        return err
    b = _body()
    notif_id = (b.get("id") or "").strip() or None
    mark_all = bool(b.get("all"))
    if not notif_id and not mark_all:
        return jsonify(error="VALIDATION", fields={"id": "id or all:true required"}), 422
    from core.repositories import notifications as notif_repo
    with session_scope() as s:
        updated = notif_repo.mark_read(
            s, club_id=p.club_id, user_id=p.user_id,
            notification_id=notif_id, all_unread=mark_all)
        count = notif_repo.unread_count(s, club_id=p.club_id, user_id=p.user_id)
    return jsonify(ok=True, updated=updated, unread_count=count), 200


# ---------------------------------------------------------------------------
# transaction log  (the client's chronological "what happened to my money" feed)
#   GET /api/me/activity  -> {activity:[…]}   — always scoped to the caller's own rows
# ---------------------------------------------------------------------------

@me_bp.get("/billing/summary")
def my_billing_summary():
    """This month's billing grouped by category (Court hire / Lessons / Classes) — count + total +
    the individual sessions (each drills into its booking story). ?month=YYYY-MM (default: current)."""
    p, err = _principal()
    if err:
        return err
    month = (request.args.get("month") or "").strip() or None
    from billing import me as billing_me
    with session_scope() as s:
        data = billing_me.billing_summary(s, club_id=p.club_id, user_id=p.user_id, month=month)
    return jsonify(data), 200


@me_bp.get("/bookings/<booking_id>")
def my_booking_story(booking_id):
    """The full 'story' of one of the caller's bookings — what/when/where (club+court)/who (players)/
    the charge + payment status + action eligibility — assembled in one payload for the detail view."""
    p, err = _principal()
    if err:
        return err
    from diary import bookings as diary_bookings
    with session_scope() as s:
        story = diary_bookings.booking_story(
            s, club_id=p.club_id, user_id=p.user_id, booking_id=booking_id)
    if story is None:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(booking=story), 200


@me_bp.get("/classes/<enrolment_id>")
def my_class_story(enrolment_id):
    """The full record of one of the caller's CLASS enrolments — the class sibling of the booking story,
    same shape so the one transaction-record widget renders it."""
    p, err = _principal()
    if err:
        return err
    from diary import classes as diary_classes
    with session_scope() as s:
        story = diary_classes.enrolment_story(
            s, club_id=p.club_id, enrolment_id=enrolment_id, scope="client", user_id=p.user_id)
    if story is None:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(booking=story), 200


# NOTE: the client transaction-log ('my_activity', scope='client') route was removed 2026-07-05 — it
# collided with GET /api/me/activity (the monthly Activity view, get_activity above) and its only
# consumer (the retired account.html shell) is gone. billing.activity.transaction_log stays for the
# coach/admin scopes.

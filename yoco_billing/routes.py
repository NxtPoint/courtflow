# yoco_billing/routes.py — the Yoco blueprint (yoco_bp). Registered in app.py.
#
# Endpoints (all under /api/billing/yoco/*):
#   POST /checkout        AUTH'd. Body {order_id}. Server-side creates a Yoco hosted checkout
#                         for an 'online' order and returns {redirect_url}. Gated by
#                         PAYMENTS_ENABLED (global) + club.policy.allow_online_payment (per-club
#                         rollback). Persists the Yoco checkout id so a later refund can find it.
#   POST /webhook         PUBLIC, signature-verified. verify -> parse_event -> apply_payment_event
#                         (the same idempotent core as a desk payment). 401 only on bad signature;
#                         200 for accepted/duplicate/unknown so Yoco stops retrying; 500 only on a
#                         transient internal error (so Yoco DOES retry).
#   POST /refund          ADMIN (role-gated take_pay_at_court). Body {order_id, amount_minor?}.
#                         Asks Yoco to refund; the ledger row is written when the refund.succeeded
#                         webhook arrives (record-only — the booking is NEVER auto-reversed, docs/05 §8).
#   GET  /order/<id>      AUTH'd. Order status probe for the pay-return page (UX only; the booking
#                         is confirmed by the webhook, not by this read).
#
# Self-serve membership (v1: one month per purchase via the one-off checkout):
#   POST /api/billing/membership/checkout  AUTH'd member. Creates an online membership order +
#                         a pending linked subscription; returns {order_id} for Pay.startYocoCheckout.
#   GET  /api/billing/membership/status    AUTH'd member. {active, current_period_end, price_minor,
#                         currency}. The webhook handler activates the membership AFTER
#                         apply_payment_event marks the order paid (membership.activate_membership_for_order).
#
# Importing yoco_billing.adapter (below) registers the gateway as a side-effect. DB-touching
# imports stay lazy so the module imports clean with no DATABASE_URL (app.py boot discipline).

from __future__ import annotations

import json
import logging
import os

from flask import Blueprint, jsonify, request

# Side-effect import: registers the "yoco" gateway (register_gateway) at blueprint load.
from yoco_billing import adapter  # noqa: F401

log = logging.getLogger("yoco_billing.routes")

yoco_bp = Blueprint("yoco", __name__)


def _truthy(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip() in ("1", "true", "True")


def _app_base_url() -> str:
    """Where the pay-return page is served (the courtflow-web portal). Prefer APP_BASE_URL;
    fall back to the caller's Origin, then the onrender web host."""
    base = (os.getenv("APP_BASE_URL") or "").strip().rstrip("/")
    if base:
        return base
    origin = (request.headers.get("Origin") or "").strip().rstrip("/")
    if origin:
        return origin
    return "https://courtflow-web.onrender.com"


def _club_allows_online(session, club_id) -> bool:
    from sqlalchemy import text
    try:
        return bool(session.execute(
            text("SELECT COALESCE(allow_online_payment, false) FROM club.policy WHERE club_id = :c"),
            {"c": str(club_id)},
        ).scalar())
    except Exception:
        return False


def _in_callers_club(principal, club_id) -> bool:
    return principal.is_platform_admin or str(club_id) == str(principal.club_id or "")


# ---------------------------------------------------------------------------
# POST /api/billing/yoco/checkout — create a hosted checkout for an online order
# ---------------------------------------------------------------------------

@yoco_bp.post("/api/billing/yoco/checkout")
def yoco_checkout():
    from auth import resolve_principal
    from iam.permissions import can
    from billing.gateway import get_gateway

    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return jsonify(error="unauthorized"), 401

    body = request.get_json(silent=True) or {}
    order_id = (body.get("order_id") or "").strip()
    if not order_id:
        return jsonify(error="order_id required"), 400

    if not _truthy("PAYMENTS_ENABLED"):
        return jsonify(error="online_payments_disabled"), 403

    gw = get_gateway("yoco")
    if gw is None:
        return jsonify(error="yoco_unavailable"), 503

    from db import session_scope
    from sqlalchemy import text
    from billing import orders as orders_repo

    with session_scope() as s:
        order = orders_repo.get_order(s, order_id=order_id)
        if not order:
            return jsonify(error="order not found"), 404

        # Tenancy first, then ownership: the payer, or a club admin, may start checkout.
        if not _in_callers_club(p, order["club_id"]):
            return jsonify(error="forbidden"), 403
        owns = bool(p.user_id and order.get("user_id") and str(order["user_id"]) == str(p.user_id))
        if not (owns or can(p, "take_pay_at_court", {"club_id": order["club_id"]})):
            return jsonify(error="forbidden"), 403

        if (order.get("settlement_mode") or "") != "online":
            return jsonify(error="order is not an online-payment order"), 400
        if order.get("status") not in ("awaiting_payment", "open"):
            return jsonify(error="order already settled", status=order.get("status")), 409
        if int(order.get("amount_minor") or 0) <= 0:
            return jsonify(error="order has no amount to pay"), 400
        if not _club_allows_online(s, order["club_id"]):
            return jsonify(error="online_payments_not_enabled_for_club"), 403

        base = _app_base_url()
        success = f"{base}/pay-return.html?order={order_id}&r=success"
        cancel = f"{base}/pay-return.html?order={order_id}&r=cancel"

        try:
            intent = gw.create_checkout(order=order, success_url=success, cancel_url=cancel)
        except Exception as e:
            log.warning("yoco create_checkout failed: %s", e)
            return jsonify(error="checkout_failed", detail=str(e)), 502

        # Persist the Yoco checkout id (event_hash NULL) so /refund can reference it later.
        if intent.intent_id:
            try:
                s.execute(
                    text("""
                        INSERT INTO billing.payment_attempt
                            (club_id, order_id, provider, intent_id, status, raw_event)
                        VALUES (:club_id, :order_id, 'yoco', :intent_id, 'created',
                                CAST(:raw AS jsonb))
                    """),
                    {"club_id": str(order["club_id"]), "order_id": order_id,
                     "intent_id": intent.intent_id, "raw": json.dumps(intent.extra or {})},
                )
            except Exception:
                log.info("could not persist checkout intent for order=%s (continuing)", order_id)

    return jsonify(redirect_url=intent.redirect_url, intent_id=intent.intent_id,
                   provider="yoco"), 200


# ---------------------------------------------------------------------------
# POST /api/billing/yoco/webhook — verify -> normalize -> apply_payment_event
# ---------------------------------------------------------------------------

@yoco_bp.post("/api/billing/yoco/webhook")
def yoco_webhook():
    from billing.gateway import get_gateway
    from billing.events import apply_payment_event

    gw = get_gateway("yoco")
    if gw is None:
        # No adapter registered — acknowledge so Yoco doesn't hammer us; nothing to do.
        return jsonify(ok=True, ignored="no_gateway"), 200

    # verify_webhook reads the RAW body (request.get_data()) before any JSON parsing.
    if not gw.verify_webhook(request):
        return jsonify(error="invalid_signature"), 401

    payload = request.get_json(silent=True) or {}
    try:
        event = gw.parse_event(payload)
    except Exception as e:
        log.warning("yoco parse_event failed: %s", e)
        return jsonify(error="parse_failed"), 400

    from db import session_scope
    try:
        # Settlement + membership activation in ONE transaction. apply_payment_event keeps its
        # OWN idempotency intact: passing `session` joins (doesn't change) its logic, and on a
        # replay it returns {ignored:True} WITHOUT re-marking the order — so we only activate a
        # membership on a genuinely NEW charge_succeeded. The activation helper is itself
        # idempotent (already_active guard), giving belt-and-braces.
        with session_scope() as s:
            result = apply_payment_event(event, session=s)
            if (not result.get("ignored")
                    and (event.kind or "").strip().lower() == "charge_succeeded"
                    and result.get("order_id")):
                from billing import membership as membership_repo
                if membership_repo.is_membership_order(s, order_id=result["order_id"]):
                    act = membership_repo.activate_membership_for_order(
                        s, order_id=result["order_id"], provider="yoco", months=1)
                    result["membership"] = act
    except Exception:
        # Transient (e.g. DB) error — 500 so Yoco retries the delivery.
        log.exception("apply_payment_event failed for yoco webhook")
        return jsonify(error="internal"), 500

    # 200 for accepted / duplicate / unknown so Yoco stops retrying.
    return jsonify(result), 200


# ---------------------------------------------------------------------------
# Self-serve membership purchase (v1: ONE MONTH per purchase via the one-off checkout).
# Auto-renewing Yoco subscription is the NEXT iteration — this buys a single month and the
# member re-buys when it lapses. Reuses the SAME hosted-checkout + webhook seam as bookings.
# ---------------------------------------------------------------------------

@yoco_bp.post("/api/billing/membership/checkout")
def membership_checkout():
    """AUTH'd member. Create an online order for THIS member's club membership + a pending
    subscription row linked to it, then return {order_id} so the page calls
    Pay.startYocoCheckout(order_id). Same gates as a booking online payment."""
    from auth import resolve_principal

    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return jsonify(error="unauthorized"), 401
    if not p.club_id:
        return jsonify(error="no_club"), 403
    if not p.user_id:
        return jsonify(error="no_user"), 403

    if not _truthy("PAYMENTS_ENABLED"):
        return jsonify(error="online_payments_disabled"), 403

    from db import session_scope
    from billing import membership as membership_repo

    with session_scope() as s:
        if not _club_allows_online(s, p.club_id):
            return jsonify(error="online_payments_not_enabled_for_club"), 403
        try:
            res = membership_repo.create_membership_order(s, club_id=p.club_id, user_id=p.user_id)
        except Exception as e:
            log.warning("create_membership_order failed club=%s: %s", p.club_id, e)
            return jsonify(error="membership_order_failed"), 500
        if not res:
            return jsonify(error="no_membership_offered"), 404
        if int(res.get("amount_minor") or 0) <= 0:
            return jsonify(error="membership_has_no_price"), 400

    return jsonify(order_id=res["order_id"], amount_minor=res["amount_minor"],
                   currency=res["currency"], provider="yoco"), 200


@yoco_bp.get("/api/billing/membership/status")
def membership_status():
    """AUTH'd member. Their membership status for the Membership page:
    {active, current_period_end, price_minor, currency, sold}."""
    from auth import resolve_principal

    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return jsonify(error="unauthorized"), 401
    if not p.club_id:
        return jsonify(error="no_club"), 403

    from db import session_scope
    from billing import membership as membership_repo

    with session_scope() as s:
        st = membership_repo.membership_status(s, club_id=p.club_id, user_id=p.user_id)
        # Surface whether the club has online pay on, so the page can disable the Buy button.
        st["online_enabled"] = bool(_truthy("PAYMENTS_ENABLED") and _club_allows_online(s, p.club_id))
    return jsonify(st), 200


# ---------------------------------------------------------------------------
# POST /api/billing/yoco/refund — admin-initiated refund (record-only settlement)
# ---------------------------------------------------------------------------

@yoco_bp.post("/api/billing/yoco/refund")
def yoco_refund():
    from auth import resolve_principal
    from iam.permissions import can
    from billing.gateway import get_gateway

    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return jsonify(error="unauthorized"), 401

    body = request.get_json(silent=True) or {}
    order_id = (body.get("order_id") or "").strip()
    if not order_id:
        return jsonify(error="order_id required"), 400

    gw = get_gateway("yoco")
    if gw is None:
        return jsonify(error="yoco_unavailable"), 503

    from db import session_scope
    from sqlalchemy import text
    from billing import orders as orders_repo

    amount_in = body.get("amount_minor")
    with session_scope() as s:
        order = orders_repo.get_order(s, order_id=order_id)
        if not order:
            return jsonify(error="order not found"), 404
        if not can(p, "take_pay_at_court", {"club_id": order["club_id"]}):
            return jsonify(error="forbidden"), 403

        checkout_id = s.execute(
            text("""
                SELECT intent_id FROM billing.payment_attempt
                WHERE order_id = :oid AND provider = 'yoco' AND intent_id IS NOT NULL
                ORDER BY created_at DESC LIMIT 1
            """),
            {"oid": order_id},
        ).scalar()
        if not checkout_id:
            return jsonify(error="no_yoco_checkout_for_order"), 404

        amount = int(amount_in) if amount_in is not None else int(order.get("amount_minor") or 0)

    try:
        res = gw.refund(payment={"checkout_id": checkout_id}, amount_minor=amount)
    except Exception as e:
        log.warning("yoco refund failed for order=%s: %s", order_id, e)
        return jsonify(error="refund_failed", detail=str(e)), 502

    # The authoritative ledger write happens on the refund.succeeded webhook ->
    # apply_payment_event(kind='refunded'). This is the gateway acknowledgement.
    return jsonify(ok=True, provider="yoco", refund_id=res.provider_refund_id,
                   amount_minor=res.amount_minor, status=res.status,
                   note="refund requested; ledger updates on refund.succeeded webhook"), 200


# ---------------------------------------------------------------------------
# GET /api/billing/yoco/order/<order_id> — status probe for the pay-return page
# ---------------------------------------------------------------------------

@yoco_bp.get("/api/billing/yoco/order/<order_id>")
def yoco_order_status(order_id):
    from auth import resolve_principal
    from iam.permissions import can

    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return jsonify(error="unauthorized"), 401

    from db import session_scope
    from billing import orders as orders_repo

    with session_scope() as s:
        order = orders_repo.get_order(s, order_id=order_id)
        if not order:
            return jsonify(error="order not found"), 404
        if not _in_callers_club(p, order["club_id"]):
            return jsonify(error="forbidden"), 403
        owns = bool(p.user_id and order.get("user_id") and str(order["user_id"]) == str(p.user_id))
        if not (owns or can(p, "view_finances", {"club_id": order["club_id"]})):
            return jsonify(error="forbidden"), 403
        return jsonify(
            order_id=str(order["id"]),
            status=order.get("status"),
            settlement_mode=order.get("settlement_mode"),
            amount_minor=order.get("amount_minor"),
            currency_code=order.get("currency_code"),
        ), 200

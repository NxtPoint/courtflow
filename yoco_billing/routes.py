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
#   POST /refund          ADMIN (role-gated take_pay_at_court). Body {order_id, amount_minor?,
#                         cancel_booking?}. Asks Yoco to refund; ledger row written when the
#                         refund.succeeded webhook arrives (record-only — booking NOT auto-reversed,
#                         docs/05 §8). cancel_booking=true ALSO cancels the order's booking(s) +
#                         frees the slot (diary.cancel_booking) — the "Refund & cancel" option.
#   GET  /order/<id>      AUTH'd. Order status probe for the pay-return page (UX only; the booking
#                         is confirmed by the webhook, not by this read).
#   POST /reconcile/<id>  AUTH'd (payer/admin). Recover a MISSED payment: ask Yoco whether the
#                         checkout completed and, if so, confirm via apply_payment_event
#                         (idempotent). The pay-return page calls this when polling stays pending.
#   GET  /receipt/<id>    (path: /api/billing/receipt/<id>) AUTH'd (payer/admin). Receipt JSON for
#                         the printable receipt page; works for online AND desk payments.
#   POST /api/cron/reconcile-payments   OPS-only bulk sweep of pending online orders.
#
# Self-serve membership (configurable TERM PLANS via the one-off checkout):
#   POST /api/billing/membership/checkout  AUTH'd member. Body {price_id?} picks a term plan
#                         (omitted → cheapest). Creates an online membership order for THAT plan +
#                         a pending linked subscription; returns {order_id} for Pay.startYocoCheckout.
#   GET  /api/billing/membership/status    AUTH'd member. {active, current_period_end, price_minor,
#                         currency, plans}. The webhook handler activates the membership AFTER
#                         apply_payment_event marks the order paid, granting the plan's term_months
#                         (membership.activate_membership_for_order).
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
                    # months omitted → the granted duration is the LINKED PLAN's term_months
                    # (read off the order's price_id), so each term plan grants its own length.
                    act = membership_repo.activate_membership_for_order(
                        s, order_id=result["order_id"], provider="yoco")
                    result["membership"] = act
                # Bundle/token pack purchase (docs/specs/02): activate the linked PENDING wallet —
                # grant sessions_count tokens + set expires_at. Idempotent keyed off order_id (a
                # replay finds it already active → no second grant), mirroring the membership hook.
                from billing import bundles as bundles_repo
                if bundles_repo.is_bundle_order(s, order_id=result["order_id"]):
                    result["bundle"] = bundles_repo.activate_wallet_for_order(
                        s, order_id=result["order_id"], provider="yoco")
                    # NEW emit: a pack just activated → drive a "Pack activated" notification.
                    # (The receipt notification from payment_succeeded covers the payment; this
                    # covers the grant.) Best-effort + guarded — never affects settlement.
                    _b = result["bundle"]
                    if _b and _b.get("status") == "granted":
                        try:
                            from marketing_crm.tracking import emit
                            emit("bundle_activated", {
                                "club_id": str(event.club_id) if event.club_id else None,
                                "user_id": _b.get("user_id"),
                                "ref_type": "order", "ref_id": str(result["order_id"]),
                                "label": _b.get("label"),
                                "tokens_total": _b.get("tokens_total"),
                            })
                        except Exception:
                            log.debug("bundle_activated emit skipped (tracking unavailable)")
    except Exception:
        # Transient (e.g. DB) error — 500 so Yoco retries the delivery.
        log.exception("apply_payment_event failed for yoco webhook")
        return jsonify(error="internal"), 500

    # 200 for accepted / duplicate / unknown so Yoco stops retrying.
    return jsonify(result), 200


# ---------------------------------------------------------------------------
# Self-serve membership purchase (configurable TERM PLANS via the one-off checkout).
# The member picks a configured term plan (label · price · duration); checkout creates an order
# for THAT plan's amount; activation (on the paid webhook) grants the plan's term_months. There's
# no auto-renewing Yoco subscription yet — the member re-buys when the term lapses. Reuses the
# SAME hosted-checkout + webhook seam as bookings.
# ---------------------------------------------------------------------------

@yoco_bp.post("/api/billing/membership/checkout")
def membership_checkout():
    """AUTH'd member. Create an online order for the CHOSEN membership term plan + a pending
    subscription row linked to it, then return {order_id} so the page calls
    Pay.startYocoCheckout(order_id). Body {price_id?} selects the term plan; omitted → the
    cheapest active plan. Same gates as a booking online payment."""
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

    body = request.get_json(silent=True) or {}
    price_id = (body.get("price_id") or "").strip() or None

    from db import session_scope
    from billing import membership as membership_repo

    with session_scope() as s:
        if not _club_allows_online(s, p.club_id):
            return jsonify(error="online_payments_not_enabled_for_club"), 403
        try:
            res = membership_repo.create_membership_order(
                s, club_id=p.club_id, user_id=p.user_id, price_id=price_id)
        except Exception as e:
            log.warning("create_membership_order failed club=%s: %s", p.club_id, e)
            return jsonify(error="membership_order_failed"), 500
        if not res:
            return jsonify(error="no_membership_offered"), 404
        if int(res.get("amount_minor") or 0) <= 0:
            return jsonify(error="membership_has_no_price"), 400

    return jsonify(order_id=res["order_id"], amount_minor=res["amount_minor"],
                   currency=res["currency"], price_id=res["price_id"],
                   term_months=res.get("term_months"), label=res.get("label"),
                   provider="yoco"), 200


@yoco_bp.get("/api/billing/membership/status")
def membership_status():
    """AUTH'd member. Their membership status for the Membership page:
    {active, current_period_end, price_minor, currency, sold, plans, online_enabled}.
    `plans` are the configured term plans the member can pick + buy."""
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
# Session packs (token bundles) — buy a prepaid pack of N sessions via the SAME hosted checkout.
# Mirrors the membership purchase seam: checkout creates an awaiting_payment order + a pending
# token_wallet linked by order_id; the webhook (above) activates the wallet (grants tokens) on the
# paid charge — idempotent. docs/specs/02.
# ---------------------------------------------------------------------------

@yoco_bp.get("/api/billing/bundles")
def bundles_list():
    """AUTH'd member. The club's active bundle plans the member can buy (optionally one kind):
    GET /api/billing/bundles?service_kind=court|lesson|class
    -> {plans:[{id,service_kind,coach_user_id,label,sessions_count,duration_minutes,price_minor,
                currency,validity_days}], online_enabled}."""
    from auth import resolve_principal

    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return jsonify(error="unauthorized"), 401
    if not p.club_id:
        return jsonify(error="no_club"), 403

    service_kind = (request.args.get("service_kind") or "").strip() or None

    from db import session_scope
    from billing import bundles as bundles_repo
    with session_scope() as s:
        plans = bundles_repo.list_plans(s, club_id=p.club_id, service_kind=service_kind)
        online = bool(_truthy("PAYMENTS_ENABLED") and _club_allows_online(s, p.club_id))
    return jsonify(plans=plans, count=len(plans), online_enabled=online), 200


@yoco_bp.get("/api/billing/bundles/wallets")
def bundles_wallets():
    """AUTH'd member. Their token wallets (remaining + expiry), optionally for one kind:
    GET /api/billing/bundles/wallets?service_kind=&active=1
    -> {wallets:[{id,service_kind,coach_user_id,duration_minutes,tokens_total,tokens_remaining,
                  status,expires_at,label}]}."""
    from auth import resolve_principal

    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return jsonify(error="unauthorized"), 401
    if not p.club_id:
        return jsonify(error="no_club"), 403

    service_kind = (request.args.get("service_kind") or "").strip() or None
    active_only = (request.args.get("active") or "").strip() in ("1", "true", "yes")

    from db import session_scope
    from billing import bundles as bundles_repo
    with session_scope() as s:
        wallets = bundles_repo.wallets_for(s, club_id=p.club_id, user_id=p.user_id,
                                           service_kind=service_kind, active_only=active_only)
    return jsonify(wallets=wallets, count=len(wallets)), 200


@yoco_bp.post("/api/billing/bundles/checkout")
def bundles_checkout():
    """AUTH'd member. Create an online order for a chosen bundle plan + a pending token_wallet
    linked by order_id, then return {order_id} for Pay.startYocoCheckout. Same gates as a booking
    online payment. Body {bundle_plan_id}."""
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

    body = request.get_json(silent=True) or {}
    plan_id = (body.get("bundle_plan_id") or "").strip()
    if not plan_id:
        return jsonify(error="bundle_plan_id required"), 400

    from db import session_scope
    from billing import bundles as bundles_repo

    with session_scope() as s:
        if not _club_allows_online(s, p.club_id):
            return jsonify(error="online_payments_not_enabled_for_club"), 403
        try:
            res = bundles_repo.create_bundle_order(
                s, club_id=p.club_id, user_id=p.user_id, bundle_plan_id=plan_id)
        except Exception as e:
            log.warning("create_bundle_order failed club=%s: %s", p.club_id, e)
            return jsonify(error="bundle_order_failed"), 500
        if not res:
            return jsonify(error="bundle_plan_not_found"), 404
        if int(res.get("amount_minor") or 0) <= 0:
            return jsonify(error="bundle_has_no_price"), 400

    return jsonify(order_id=res["order_id"], amount_minor=res["amount_minor"],
                   currency=res["currency"], plan=res["plan"], provider="yoco"), 200


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
    cancel_flag = bool(body.get("cancel_booking"))
    with session_scope() as s:
        order = orders_repo.get_order(s, order_id=order_id)
        if not order:
            return jsonify(error="order not found"), 404
        if not can(p, "take_pay_at_court", {"club_id": order["club_id"]}):
            return jsonify(error="forbidden"), 403
        order_club_id = order["club_id"]

        # The refund endpoint is /api/checkouts/{CHECKOUT_id}/refund — it needs the Yoco
        # CHECKOUT id (ch_…) we stored at checkout-create (status='created'), NOT the most
        # recent attempt. apply_payment_event also writes an attempt row from the webhook
        # carrying the PAYMENT id (p_…); refunding that 404s ("Checkout with id p_… not
        # found"). Filter to the checkout-create row (status='created' / ch_ prefix).
        checkout_id = s.execute(
            text("""
                SELECT intent_id FROM billing.payment_attempt
                WHERE order_id = :oid AND provider = 'yoco' AND intent_id IS NOT NULL
                  AND (status = 'created' OR intent_id LIKE 'ch_%')
                ORDER BY created_at ASC LIMIT 1
            """),
            {"oid": order_id},
        ).scalar()
        if not checkout_id:
            return jsonify(error="no_yoco_checkout_for_order"), 404

        # Full refund (the admin button passes no amount) -> send NO amount so Yoco refunds
        # the full remaining balance (their `amount` field is nullable; null = full refund).
        # Sending an explicit full amount that doesn't EXACTLY match Yoco's refundable balance
        # is a common 400 — so only pass an amount for an explicit partial refund.
        amount = int(amount_in) if amount_in is not None else None

    try:
        res = gw.refund(payment={"checkout_id": checkout_id}, amount_minor=amount)
    except Exception as e:
        # Surface Yoco's actual reason to the admin UI (str(YocoError) = "yoco <status>: <desc>"),
        # not just a generic code, so failures are diagnosable from the toast.
        log.warning("yoco refund failed order=%s checkout=%s: %s", order_id, checkout_id, e)
        return jsonify(error="refund_failed", message=f"Yoco refund failed — {e}",
                       detail=str(e)), 502

    # Optional: also cancel the booking(s) and free the slot. The refund itself is record-only
    # (booking NOT auto-reversed, docs/05 §8); "Refund & cancel" is an explicit admin choice.
    # Reuse diary.cancel_booking (lazy + guarded): it cancels every booking sharing this order_id
    # (lesson + its court) in one call, frees the slot, and promotes the waitlist. role=club_admin
    # bypasses the cancellation fee (this is an admin override paired with a money refund).
    cancelled = None
    if cancel_flag:
        cancelled = False
        try:
            from db import session_scope as _scope
            from sqlalchemy import text as _text
            from diary.bookings import cancel_booking as _diary_cancel
            with _scope() as s2:
                bid = s2.execute(
                    _text("SELECT booking_id FROM billing.order_line "
                          "WHERE order_id = :oid AND booking_id IS NOT NULL LIMIT 1"),
                    {"oid": order_id},
                ).scalar()
                if bid:
                    cres = _diary_cancel(s2, club_id=order_club_id, booking_id=str(bid),
                                         actor_user_id=p.user_id, role="club_admin",
                                         reason="admin refund")
                    cancelled = bool(cres and cres.get("ok"))
        except Exception:
            log.warning("refund+cancel: booking cancel failed for order=%s (refund stands)", order_id)

    # The authoritative ledger write happens on the refund.succeeded webhook ->
    # apply_payment_event(kind='refunded'). This is the gateway acknowledgement.
    return jsonify(ok=True, provider="yoco", refund_id=res.provider_refund_id,
                   amount_minor=res.amount_minor, status=res.status, cancelled=cancelled,
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


def _owns_or_can_view(p, order, can_fn) -> bool:
    """The payer, or someone who can view the club's finances, may read this order."""
    owns = bool(p.user_id and order.get("user_id") and str(order["user_id"]) == str(p.user_id))
    return owns or can_fn(p, "view_finances", {"club_id": order["club_id"]})


# ---------------------------------------------------------------------------
# POST /api/billing/yoco/reconcile/<order_id> — recover a missed payment (auth'd)
# The pay-return page calls this when polling stays 'awaiting_payment' (webhook slow/missed):
# it asks Yoco whether the checkout actually completed and, if so, confirms the booking.
# ---------------------------------------------------------------------------

@yoco_bp.post("/api/billing/yoco/reconcile/<order_id>")
def yoco_reconcile_order(order_id):
    from auth import resolve_principal
    from iam.permissions import can

    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return jsonify(error="unauthorized"), 401

    from db import session_scope
    from billing import orders as orders_repo
    from yoco_billing import reconcile

    with session_scope() as s:
        order = orders_repo.get_order(s, order_id=order_id)
        if not order:
            return jsonify(error="order not found"), 404
        if not _in_callers_club(p, order["club_id"]):
            return jsonify(error="forbidden"), 403
        if not _owns_or_can_view(p, order, can):
            return jsonify(error="forbidden"), 403
        result = reconcile.reconcile_order(s, order_id=str(order_id))
    return jsonify(result), 200


# ---------------------------------------------------------------------------
# POST /api/cron/reconcile-payments — OPS-only bulk sweep of pending online orders
# (callable by a future cron or by hand with X-Ops-Key). Body/query: {club_id?, hours?}.
# ---------------------------------------------------------------------------

@yoco_bp.post("/api/cron/reconcile-payments")
def cron_reconcile_payments():
    from auth import resolve_principal
    p = resolve_principal(request)
    if p is None or not p.is_platform_admin:
        return jsonify(error="unauthorized"), 401

    body = request.get_json(silent=True) or {}
    club_id = (body.get("club_id") or request.args.get("club_id") or "").strip() or None
    try:
        hours = int(body.get("hours") or request.args.get("hours") or 72)
    except (TypeError, ValueError):
        hours = 72

    from db import session_scope
    from yoco_billing import reconcile
    with session_scope() as s:
        result = reconcile.reconcile_pending(s, club_id=club_id, hours=hours)
    return jsonify(result), 200


# ---------------------------------------------------------------------------
# GET /api/billing/receipt/<order_id> — receipt data for the printable receipt page (auth'd).
# Returns JSON the receipt.html page renders; works for online AND desk payments.
# ---------------------------------------------------------------------------

@yoco_bp.get("/api/billing/receipt/<order_id>")
def billing_receipt(order_id):
    from auth import resolve_principal
    from iam.permissions import can

    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return jsonify(error="unauthorized"), 401

    from db import session_scope
    from billing import orders as orders_repo
    from yoco_billing import receipt as receipt_mod

    with session_scope() as s:
        order = orders_repo.get_order(s, order_id=order_id)
        if not order:
            return jsonify(error="order not found"), 404
        if not _in_callers_club(p, order["club_id"]):
            return jsonify(error="forbidden"), 403
        if not _owns_or_can_view(p, order, can):
            return jsonify(error="forbidden"), 403
        data = receipt_mod.build_receipt(s, order_id=str(order_id))
    if not data:
        return jsonify(error="order not found"), 404
    return jsonify(receipt=data), 200

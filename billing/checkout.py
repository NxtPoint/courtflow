# billing/checkout.py — start a hosted card payment for an order, whichever provider the club uses.
#
# ONE implementation behind both front doors: the member app's POST /api/billing/yoco/checkout and the
# public API's POST /api/v1/clubs/<club>/bookings/<id>/checkout. The checks were inline in the Yoco
# route, so a second caller would have had to copy them — and the ownership check is the one that
# matters: without it anyone could start a checkout for someone else's order.
#
# Provider-agnostic: it asks the gateway registry (billing.gateway) for the provider, so PayPal plugs
# in beside Yoco without touching this. billing/ never imports an adapter package.

import json
import logging
import os

from sqlalchemy import text

log = logging.getLogger("billing.checkout")


def payments_enabled() -> bool:
    return os.getenv("PAYMENTS_ENABLED", "0").strip() in ("1", "true", "True")


def club_provider(session, club_id) -> str:
    """Which payment provider takes THIS club's card payments. One platform-wide setting today
    (PAYMENTS_PROVIDER, default yoco); becomes per club with the payment-account setup screen."""
    return (os.getenv("PAYMENTS_PROVIDER") or "yoco").strip().lower()


def club_allows_online(session, club_id) -> bool:
    try:
        return bool(session.execute(
            text("SELECT COALESCE(allow_online_payment, false) FROM club.policy WHERE club_id = :c"),
            {"c": str(club_id)},
        ).scalar())
    except Exception:
        return False


def _err(code, status, message):
    return {"ok": False, "error": code, "status": status, "message": message}


def start_checkout(session, *, principal, order_id, success_url, cancel_url):
    """Create a hosted checkout for an ONLINE order the caller may pay. Returns
    {ok, redirect_url, intent_id, provider} or {ok: False, error, status, message}.

    Error codes: ORDER_NOT_FOUND 404 · FORBIDDEN 403 · NOT_AN_ONLINE_ORDER 400 · ALREADY_SETTLED 409 ·
    NOTHING_TO_PAY 400 · ONLINE_PAYMENTS_DISABLED 403 · ONLINE_PAYMENTS_OFF_FOR_CLUB 403 ·
    PROVIDER_UNAVAILABLE 503 · CHECKOUT_FAILED 502 (with `detail`, `provider_body`)."""
    from billing import orders as orders_repo
    from billing.gateway import get_gateway
    from iam.permissions import can

    if not payments_enabled():
        return _err("ONLINE_PAYMENTS_DISABLED", 403, "online payments are switched off")
    order = orders_repo.get_order(session, order_id=order_id)
    if not order:
        return _err("ORDER_NOT_FOUND", 404, "order not found")
    # Tenancy first, then ownership: the payer, or a club admin, may start checkout.
    in_club = principal.is_platform_admin or str(order["club_id"]) == str(principal.club_id or "")
    owns = bool(principal.user_id and order.get("user_id")
                and str(order["user_id"]) == str(principal.user_id))
    if not in_club or not (owns or can(principal, "take_pay_at_court", {"club_id": order["club_id"]})):
        return _err("FORBIDDEN", 403, "that order isn't yours to pay")
    if (order.get("settlement_mode") or "") != "online":
        return _err("NOT_AN_ONLINE_ORDER", 400, "order is not an online-payment order")
    if order.get("status") not in ("awaiting_payment", "open"):
        d = _err("ALREADY_SETTLED", 409, "order already settled")
        d["order_status"] = order.get("status")
        return d
    if int(order.get("amount_minor") or 0) <= 0:
        return _err("NOTHING_TO_PAY", 400, "order has no amount to pay")
    if not club_allows_online(session, order["club_id"]):
        return _err("ONLINE_PAYMENTS_OFF_FOR_CLUB", 403, "this club doesn't take online payments")
    provider = club_provider(session, order["club_id"])
    gw = get_gateway(provider)
    if gw is None:
        return _err("PROVIDER_UNAVAILABLE", 503, f"{provider} is not available")

    try:
        intent = gw.create_checkout(order=order, success_url=success_url, cancel_url=cancel_url)
    except Exception as e:
        # Surface the provider's FULL error body (which field it rejected) — a bare
        # "yoco 400: For input string" hides which field is at fault.
        pb = getattr(e, "body", None)
        log.warning("%s create_checkout failed for order=%s amount=%s: %s | body=%s",
                    provider, order_id, order.get("amount_minor"), e, pb)
        d = _err("CHECKOUT_FAILED", 502, "the payment provider refused the checkout")
        d.update(detail=str(e), provider_body=pb)
        return d

    # Persist the checkout id (event_hash NULL) so refund / reconcile can reference it later.
    if intent.intent_id:
        try:
            session.execute(
                text("""
                    INSERT INTO billing.payment_attempt
                        (club_id, order_id, provider, intent_id, status, raw_event)
                    VALUES (:club_id, :order_id, :provider, :intent_id, 'created',
                            CAST(:raw AS jsonb))
                """),
                {"club_id": str(order["club_id"]), "order_id": order_id, "provider": provider,
                 "intent_id": intent.intent_id, "raw": json.dumps(intent.extra or {})},
            )
        except Exception:
            log.info("could not persist checkout intent for order=%s (continuing)", order_id)
    return {"ok": True, "redirect_url": intent.redirect_url, "intent_id": intent.intent_id,
            "provider": provider}

# yoco_billing/ — the Yoco payment gateway adapter (docs/05 §6).
#
# Implements billing.gateway.PaymentGateway over Yoco's hosted Checkout API + Standard-
# Webhooks. The core settlement path (billing.events.apply_payment_event) is untouched; this
# package only translates Yoco's REST/webhooks into the normalized wire shapes and exposes
# /api/billing/yoco/* routes.
#
# Wiring: app.py does _try_register(app, "yoco_billing.routes", "yoco_bp"), which imports
# yoco_billing.routes -> yoco_billing.adapter, and the adapter calls register_gateway("yoco").
#
# Provider-agnostic by design: a future provider (FastPay, PayPal) is another package
# implementing the same Protocol — the core never changes.

from __future__ import annotations

import logging

log = logging.getLogger("yoco_billing")


class RefundError(Exception):
    """A Yoco refund could not be executed. `message` is admin-facing (carries Yoco's reason);
    `status` is a suggested HTTP status (404 = no checkout to refund, 503 = adapter absent,
    502 = the gateway call failed)."""

    def __init__(self, code: str, message: str, status: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def execute_order_refund(session, *, order_id, amount_minor=None):
    """Execute the actual Yoco refund for one order — the SINGLE source of truth reused by both
    the admin 'Recent online payments → Refund' button (yoco_billing.routes) AND the approve path
    of a client refund-request (billing.refunds.approve_refund_request).

    Record-only (the authoritative ledger row is written when the refund.succeeded webhook arrives
    via apply_payment_event — the booking is NOT auto-reversed, docs/05 §8). This only asks Yoco.

    Looks up the Yoco CHECKOUT id (ch_…, payment_attempt.status='created') we stored at
    checkout-create — NOT the webhook's PAYMENT id (p_…), which 404s on the refund endpoint.
    amount_minor=None → a FULL refund (Yoco's `amount` field is nullable = full balance).

    Returns the gateway RefundResult on success; raises RefundError on any failure (so a caller
    inside a transaction can roll back / leave its own state untouched). Does NOT commit."""
    from sqlalchemy import text
    from billing.gateway import get_gateway

    gw = get_gateway("yoco")
    if gw is None:
        raise RefundError("yoco_unavailable", "Online payments are not available.", status=503)

    checkout_id = session.execute(
        text("""
            SELECT intent_id FROM billing.payment_attempt
            WHERE order_id = :oid AND provider = 'yoco' AND intent_id IS NOT NULL
              AND (status = 'created' OR intent_id LIKE 'ch_%')
            ORDER BY created_at ASC LIMIT 1
        """),
        {"oid": str(order_id)},
    ).scalar()
    if not checkout_id:
        raise RefundError("no_yoco_checkout_for_order",
                          "No Yoco checkout found for this order.", status=404)

    amount = int(amount_minor) if amount_minor is not None else None
    try:
        return gw.refund(payment={"checkout_id": checkout_id}, amount_minor=amount)
    except Exception as e:
        # Surface Yoco's actual reason (str(YocoError) = "yoco <status>: <desc>").
        log.warning("yoco refund failed order=%s checkout=%s: %s", order_id, checkout_id, e)
        raise RefundError("refund_failed", f"Yoco refund failed — {e}", status=502) from e

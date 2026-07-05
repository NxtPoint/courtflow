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
        result = gw.refund(payment={"checkout_id": checkout_id}, amount_minor=amount)
    except Exception as e:
        # Surface Yoco's actual reason (str(YocoError) = "yoco <status>: <desc>").
        log.warning("yoco refund failed order=%s checkout=%s: %s", order_id, checkout_id, e)
        raise RefundError("refund_failed", f"Yoco refund failed — {e}", status=502) from e

    # Yoco's checkout-refund does NOT reliably deliver a refund.succeeded webhook, so the old
    # record-only design left refunds INVISIBLE (button said "Refunded" but nothing recorded, the
    # order stayed 'paid', no ledger row). Record the refund NOW from the gateway response instead.
    # Idempotent: a later refund webhook with the same refund id dedupes on the (provider,
    # provider_payment_id) unique index in apply_payment_event, so this never double-counts.
    try:
        from billing.events import apply_payment_event
        from billing.gateway import NormalizedPaymentEvent
        o = session.execute(
            text('SELECT club_id, amount_minor, currency_code FROM billing."order" WHERE id = :o'),
            {"o": str(order_id)},
        ).mappings().first()
        # A FULL refund passes amount=None (Yoco refunds the balance) and the adapter reports 0 —
        # fall back to the order's amount so the ledger row is the real figure.
        amt = int(result.amount_minor or (amount if amount is not None else (int(o["amount_minor"]) if o else 0)))
        apply_payment_event(
            NormalizedPaymentEvent(
                provider="yoco", kind="refunded", order_ref=str(order_id),
                provider_payment_id=(result.provider_refund_id or ("refund:" + str(checkout_id))),
                amount_minor=amt, currency=(o["currency_code"] if o else "ZAR"),
                status="refunded", direction="refund",
                club_id=(str(o["club_id"]) if o else None), raw={"source": "sync_refund"}),
            session=session)
        # A direct refund FULFILS any pending client refund-request for this order — resolve it so it
        # doesn't linger under Approvals (approving again would 400 "already refunded").
        session.execute(
            text("UPDATE billing.refund_request SET status = 'refunded', updated_at = now() "
                 "WHERE order_id = :o AND status = 'pending'"),
            {"o": str(order_id)})
    except Exception:
        log.warning("refund taken at Yoco but the sync ledger write failed order=%s "
                    "(a refund webhook, if any, will reconcile)", order_id, exc_info=False)
    return result

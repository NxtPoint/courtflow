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

    # The ORDER's own facts come first, before we reach for any infrastructure: "this sale was never a
    # card sale" is true whether or not Yoco is reachable, and it is the answer that actually helps.
    # Checking the gateway first would answer "online payments are not available" to a question that
    # was never about availability.
    #
    # WAS THIS ORDER ACTUALLY PAID BY CARD? A billing.payment_attempt row is written the moment a
    # member taps "Pay online" — BEFORE any money moves — so its existence proves an INTENT, never a
    # payment. An order whose member started an online checkout, abandoned it, and then settled at the
    # desk (or had a coach collect it, or an admin mark it paid) still carries that `ch_` id while the
    # money arrived somewhere else entirely.
    #
    # Asking Yoco to refund that checkout is asking it to return money it never took, and Yoco answers
    # "insufficient funds" — about the CHECKOUT's balance, not the merchant's. That reads like the
    # club's Yoco account being empty, so it gets diagnosed as a banking problem while the real cause
    # is that this sale was never a card sale. Check the payments, not the intents.
    paid_via = session.execute(
        text("SELECT provider, COALESCE(SUM(amount_minor),0) AS amt FROM billing.payment "
             "WHERE order_id = :o AND direction = 'charge' AND status = 'succeeded' "
             "GROUP BY provider"),
        {"o": str(order_id)},
    ).mappings().all()
    by_provider = {r["provider"]: int(r["amt"] or 0) for r in paid_via}
    # NARROW ON PURPOSE. Refuse only when we can POSITIVELY see the money came another way — some
    # other provider succeeded on this order, so a card refund is simply the wrong instrument and
    # saying so beats a baffling gateway error.
    #
    # When there are NO payment rows at all we do NOT refuse. That is the AMBIGUOUS case, not the
    # obvious one: the charge may sit on a 'Pay all' wrapper, or a webhook may never have been
    # recorded, while the money is genuinely at Yoco. Refusing there would be asserting knowledge we
    # don't have and blocking a legitimate refund on the strength of a gap in our own records. Let
    # Yoco answer — it is the authority on whether that checkout holds funds.
    if by_provider and not by_provider.get("yoco"):
        how = ", ".join(sorted(by_provider))
        raise RefundError(
            "not_paid_by_card",
            f"This order wasn't paid by card — it was settled by {how}. "
            "Refund it the same way it was paid and record that against the order.",
            status=409)

    # ALREADY REFUNDED? This is the REAL double-refund guard, and it lives here rather than in the
    # gateway's idempotency key. That key used to be fixed per checkout, which looked like protection
    # but was really just a permanent replay of whatever happened first — it blocked legitimate
    # retries while never once consulting whether the money had actually gone back. Our own ledger
    # knows, so ask it.
    charged = int(by_provider.get("yoco") or 0)
    refunded = int(session.execute(
        text("SELECT COALESCE(SUM(amount_minor),0) FROM billing.payment "
             "WHERE order_id = :o AND provider = 'yoco' AND direction = 'refund' "
             "  AND status IN ('succeeded','refunded')"),
        {"o": str(order_id)},
    ).scalar() or 0)
    # Only meaningful when we can SEE the charge. With no recorded card payment (charged == 0) these
    # comparisons are vacuously true — `refunded >= charged` is `0 >= 0` — and would refuse "already
    # refunded in full (0.00 of 0.00)" on precisely the ambiguous case decided above to let through.
    # No knowledge of the charge means no opinion about the refund; Yoco decides.
    if charged > 0:
        wanted = int(amount_minor) if amount_minor is not None else (charged - refunded)
        if refunded >= charged:
            raise RefundError(
                "already_refunded",
                f"This order's card payment has already been refunded in full "
                f"({refunded / 100:,.2f} of {charged / 100:,.2f}).", status=409)
        if wanted <= 0 or refunded + wanted > charged:
            raise RefundError(
                "refund_exceeds_payment",
                f"That would refund more than was charged — {charged / 100:,.2f} taken, "
                f"{refunded / 100:,.2f} already returned.", status=409)

    gw = get_gateway("yoco")
    if gw is None:
        raise RefundError("yoco_unavailable", "Online payments are not available.", status=503)

    # WHICH checkout holds the money. POST /checkout mints a fresh Yoco checkout on every call, so an
    # order the member abandoned once and paid on the retry has two `ch_` ids — and this used to take
    # the OLDEST, which is likewise a checkout Yoco never collected against. Resolved against Yoco
    # itself (lazy import: reconcile imports this package, so a top-level import would be circular).
    from yoco_billing.reconcile import paid_checkout_id_for_order
    checkout_id = paid_checkout_id_for_order(session, order_id)
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

# yoco_billing/adapter.py — the Yoco PaymentGateway (docs/05 §6).
#
# Implements billing.gateway.PaymentGateway by translating Yoco's REST + webhooks into the
# NORMALIZED shapes the core understands (CheckoutIntent / NormalizedPaymentEvent /
# RefundResult). The core grant path (billing.events.apply_payment_event) never changes —
# it sees only the normalized event; nothing Yoco-specific leaks past this file.
#
# Registers itself on import (register_gateway("yoco", ...)). yoco_billing.routes imports this
# module, and app.py registers that blueprint at boot, so the gateway is live whenever the
# package is wired in. Real charges stay gated by PAYMENTS_ENABLED + the club policy (routes.py)
# and by valid YOCO_* keys (client.py raises a clear error otherwise).

from __future__ import annotations

import logging
from typing import Any, Optional

from billing.gateway import (
    CheckoutIntent,
    NormalizedPaymentEvent,
    RefundResult,
    register_gateway,
)
from yoco_billing import client

log = logging.getLogger("yoco_billing.adapter")

# Yoco event type -> (normalized kind, money direction). Unknown types map to ("unknown",…)
# which apply_payment_event records as an attempt-only no-op (never crashes).
_EVENT_MAP = {
    "payment.succeeded": ("charge_succeeded", "charge"),
    "payment.failed":    ("charge_failed", "charge"),
    "refund.succeeded":  ("refunded", "refund"),
}


def _get(obj: Any, key: str) -> Any:
    """Read a key from a dict-like or attr-bearing object (orders/payments arrive as dicts)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


class YocoGateway:
    name = "yoco"

    def create_checkout(self, *, order, success_url: str, cancel_url: str) -> CheckoutIntent:
        """Server-side create a Yoco hosted checkout for an 'online' order. Wallets
        (Apple/Google/Samsung Pay) render automatically on the hosted page. We stash
        {order_id, club_id} in metadata so the webhook resolves the order server-side."""
        order_id = str(_get(order, "id") or _get(order, "order_id") or "")
        club_id = _get(order, "club_id")
        amount = int(_get(order, "amount_minor") or 0)
        currency = _get(order, "currency_code") or "ZAR"

        resp = client.create_checkout(
            amount_minor=amount,
            currency=currency,
            metadata={"order_id": order_id, "club_id": str(club_id) if club_id else None},
            success_url=success_url,
            cancel_url=cancel_url,
            idempotency_key=f"checkout:{order_id}",
        )
        return CheckoutIntent(
            provider="yoco",
            intent_id=str(resp.get("id") or ""),
            redirect_url=resp.get("redirectUrl"),
            extra={"status": resp.get("status"), "order_id": order_id},
        )

    def verify_webhook(self, request) -> bool:
        """Standard-Webhooks signature check. Reads the RAW body (the exact bytes Yoco
        signed) before any JSON parsing."""
        raw = request.get_data()
        return client.verify_signature(headers=request.headers, raw_body=raw)

    def parse_event(self, payload) -> NormalizedPaymentEvent:
        """Map a Yoco webhook envelope to the normalized event. Yoco wraps the resource in
        a `payload` object and echoes our metadata; charges carry the payment id, refunds
        carry the refund id (distinct ids => the (provider, provider_payment_id) unique index
        dedupes charge vs refund independently)."""
        payload = payload or {}
        etype = (payload.get("type") or "").strip().lower()
        inner = payload.get("payload") or {}
        meta = inner.get("metadata") or payload.get("metadata") or {}

        kind, direction = _EVENT_MAP.get(etype, ("unknown", "charge"))
        return NormalizedPaymentEvent(
            provider="yoco",
            kind=kind,
            order_ref=(meta.get("order_id") or None),
            provider_payment_id=(str(inner.get("id")) if inner.get("id") else None),
            amount_minor=int(inner.get("amount") or 0),
            currency=(inner.get("currency") or None),
            status=(inner.get("status") or "succeeded"),
            direction=direction,
            club_id=(meta.get("club_id") or None),
            raw=payload,
        )

    def refund(self, *, payment, amount_minor: int) -> RefundResult:
        """Refund via POST /api/checkouts/{checkout_id}/refund. The caller passes the Yoco
        checkout id (captured at checkout-create) as payment['checkout_id']. The ledger row
        is written when the refund.succeeded webhook arrives (record-only) — this just asks
        Yoco to do it."""
        checkout_id = _get(payment, "checkout_id") or _get(payment, "intent_id")
        if not checkout_id:
            raise client.YocoError(0, "no yoco checkout id for this payment")
        resp = client.refund_checkout(
            checkout_id=str(checkout_id),
            amount_minor=amount_minor,
            idempotency_key=f"refund:{checkout_id}:{int(amount_minor or 0)}",
        )
        return RefundResult(
            provider="yoco",
            provider_refund_id=str(resp.get("id")) if resp.get("id") else None,
            amount_minor=int(amount_minor or 0),
            status=(resp.get("status") or "succeeded"),
            raw=resp,
        )


# Register on import — the package being wired into app.py is the enable switch.
try:
    register_gateway("yoco", YocoGateway())
    log.info("yoco gateway registered")
except Exception:
    log.exception("yoco gateway registration failed")

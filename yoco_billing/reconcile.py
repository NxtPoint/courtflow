# yoco_billing/reconcile.py — recover payments whose webhook we never received.
#
# WHY: courtflow-api runs on Render Free (sleeps when idle). Yoco retries missed webhooks,
# which heals most gaps — but a longer outage (or a window where the held booking is about to
# expire) can leave an order stuck `awaiting_payment` while the customer has actually PAID.
# That's the worst failure mode: money taken, booking unconfirmed. Reconciliation closes it.
#
# HOW: for an `awaiting_payment` online order, fetch its Yoco checkout (GET /api/checkouts/{id})
# and, if Yoco reports the checkout COMPLETED with a paymentId, synthesise the SAME
# charge_succeeded event the webhook would have produced and run it through apply_payment_event.
# That path is idempotent (event_hash + unique(provider, provider_payment_id)), so if the real
# webhook also lands there is exactly one payment and one confirmation — never a double.
#
# SAFE-BY-DESIGN: if the GET-checkout surface is unavailable (Yoco leans webhook-first and may
# 404/405 the retrieve), we report {verifiable: False} rather than failing — the order is simply
# flagged as still-pending for manual follow-up. Nothing here can over-charge or double-confirm.

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from billing.gateway import NormalizedPaymentEvent
from billing.events import apply_payment_event
from billing import orders as orders_repo
from yoco_billing import client

log = logging.getLogger("yoco_billing.reconcile")

# Yoco checkout.status values that mean "paid".
_PAID_STATUSES = ("completed", "succeeded", "paid")


def _checkout_id_for_order(session, order_id: str) -> Optional[str]:
    """The Yoco CHECKOUT id (ch_…) stored at checkout-create — same lookup the refund uses."""
    return session.execute(
        text("""
            SELECT intent_id FROM billing.payment_attempt
            WHERE order_id = :oid AND provider = 'yoco' AND intent_id IS NOT NULL
              AND (status = 'created' OR intent_id LIKE 'ch_%')
            ORDER BY created_at ASC LIMIT 1
        """),
        {"oid": str(order_id)},
    ).scalar()


def _checkout_is_paid(co: Dict[str, Any]) -> bool:
    status = str(co.get("status") or "").strip().lower()
    # Require BOTH a payment id AND a paid status. (The old `or bool(paymentId)` made the status
    # check dead — any checkout carrying a paymentId, even a non-completed one, read as paid.)
    return bool(co.get("paymentId")) and status in _PAID_STATUSES


def reconcile_order(session, *, order_id: str) -> Dict[str, Any]:
    """Reconcile a single order against Yoco. Returns a result dict:
      {ok, changed, status, reason, verifiable?}.
    `changed=True` means we recovered a missed payment (order now paid / booking confirmed)."""
    order = orders_repo.get_order(session, order_id=order_id)
    if not order:
        return {"ok": False, "changed": False, "reason": "order_not_found"}
    if (order.get("settlement_mode") or "") != "online":
        return {"ok": True, "changed": False, "reason": "not_online", "status": order.get("status")}
    if order.get("status") != "awaiting_payment":
        # Already settled/void/refunded — nothing to recover.
        return {"ok": True, "changed": False, "reason": "not_pending", "status": order.get("status")}

    checkout_id = _checkout_id_for_order(session, order_id)
    if not checkout_id:
        return {"ok": True, "changed": False, "reason": "no_checkout", "status": "awaiting_payment"}

    # Ask Yoco for the truth. A missing GET surface (404/405) => unverifiable, not an error.
    try:
        co = client.get_checkout(checkout_id=str(checkout_id))
    except client.YocoError as e:
        if e.status in (404, 405, 501):
            log.info("reconcile: GET checkout unavailable (status=%s) order=%s", e.status, order_id)
            return {"ok": True, "changed": False, "reason": "unverifiable",
                    "verifiable": False, "status": "awaiting_payment"}
        log.warning("reconcile: get_checkout failed order=%s: %s", order_id, e)
        return {"ok": False, "changed": False, "reason": "yoco_error", "detail": str(e)}

    if not _checkout_is_paid(co):
        return {"ok": True, "changed": False, "reason": "not_paid_yet",
                "yoco_status": co.get("status"), "status": "awaiting_payment"}

    meta = co.get("metadata") or {}
    event = NormalizedPaymentEvent(
        provider="yoco",
        kind="charge_succeeded",
        order_ref=str(order_id),
        provider_payment_id=(str(co.get("paymentId")) if co.get("paymentId") else None),
        amount_minor=int(co.get("amount") or order.get("amount_minor") or 0),
        currency=(co.get("currency") or order.get("currency_code")),
        status="succeeded",
        direction="charge",
        club_id=str(order.get("club_id")) or (meta.get("club_id") or None),
        raw={"source": "reconcile", "checkout_id": str(checkout_id), "checkout": co},
    )
    res = apply_payment_event(event, session=session)
    # CRITICAL PARITY WITH THE WEBHOOK: recovering the payment is not enough — a pack/membership must
    # also be ACTIVATED (and the pack-activated email emitted). The reconcile path historically did
    # NEITHER, so an online pack recovered here showed PAID but the wallet stayed PENDING (unusable)
    # and no email was ever sent. activate_purchase is idempotent (grants only a pending wallet), so
    # it's safe on an already-processed order too. Guarded so one order can't break the sweep.
    try:
        from yoco_billing.activation import activate_purchase
        activate_purchase(session, order_id=str(order_id), club_id=str(order.get("club_id")))
    except Exception:
        log.warning("reconcile: activation failed order=%s (payment recovered, activation deferred)",
                    order_id, exc_info=False)
    changed = not res.get("ignored")
    log.info("reconcile: order=%s recovered=%s (%s)", order_id, changed,
             "applied" if changed else "already-processed")
    return {"ok": True, "changed": changed, "reason": "recovered" if changed else "already_processed",
            "status": "paid", "applied": res}


def reconcile_pending(session, *, club_id: Optional[str] = None, hours: int = 72,
                      limit: int = 200) -> Dict[str, Any]:
    """Sweep recent `awaiting_payment` online orders (default last 72h) and reconcile each.
    Returns {scanned, recovered, results:[...]}. Bounded by `limit` (logs if it caps)."""
    params: Dict[str, Any] = {"hours": int(hours), "lim": int(limit) + 1}
    clause = ""
    if club_id:
        clause = "AND o.club_id = :club_id"
        params["club_id"] = str(club_id)

    rows = session.execute(
        text(f"""
            SELECT o.id
            FROM billing."order" o
            WHERE o.settlement_mode = 'online'
              AND o.status = 'awaiting_payment'
              AND o.created_at >= now() - (:hours || ' hours')::interval
              {clause}
            ORDER BY o.created_at DESC
            LIMIT :lim
        """),
        params,
    ).scalars().all()

    capped = len(rows) > limit
    rows = rows[:limit]
    results: List[Dict[str, Any]] = []
    recovered = 0
    for oid in rows:
        r = reconcile_order(session, order_id=str(oid))
        if r.get("changed"):
            recovered += 1
        results.append({"order_id": str(oid), **{k: r.get(k) for k in ("changed", "reason", "status")}})

    if capped:
        log.warning("reconcile_pending hit the %s-order cap (more pending remain)", limit)
    return {"ok": True, "scanned": len(rows), "recovered": recovered,
            "capped": capped, "results": results}

# yoco_billing/receipt.py — build a payment receipt for an order (any settlement mode).
#
# Pure billing READ: assembles the order + its lines + its payments + the club name + the
# payer's email into a JSON-serialisable dict the receipt page (frontend/app/receipt.html)
# renders and prints. Works for Yoco online payments AND desk payments — a receipt is just a
# view over billing.*; it lives in the payments lane to stay independent of billing/ core.

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import text


def _iso(v) -> Optional[str]:
    if v is None:
        return None
    try:
        return v.isoformat()
    except AttributeError:
        return str(v)


def build_receipt(session, *, order_id: str) -> Optional[Dict[str, Any]]:
    """Return a receipt dict for the order, or None if the order doesn't exist.
    {receipt_no, order_id, club_name, issued_at, payer_email, currency, settlement_mode,
     status, lines:[{description,qty,amount_minor}], amount_minor, payments:[...],
     paid_minor, refunded_minor, net_minor}."""
    order = session.execute(
        text('SELECT id, club_id, user_id, amount_minor, currency_code, settlement_mode, '
             'status, created_at FROM billing."order" WHERE id = :id'),
        {"id": str(order_id)},
    ).mappings().first()
    if not order:
        return None

    club_name = session.execute(
        text("SELECT name FROM club.club WHERE id = :id"), {"id": str(order["club_id"])},
    ).scalar()

    payer_email = None
    if order["user_id"]:
        payer_email = session.execute(
            text("SELECT email FROM iam.user WHERE id = :id"), {"id": str(order["user_id"])},
        ).scalar()

    lines = [
        {"description": r["description"], "qty": int(r["qty"] or 1),
         "amount_minor": int(r["amount_minor"] or 0)}
        for r in session.execute(
            text("SELECT description, qty, amount_minor FROM billing.order_line "
                 "WHERE order_id = :oid ORDER BY created_at"),
            {"oid": str(order_id)},
        ).mappings().all()
    ]

    payments: List[Dict[str, Any]] = []
    paid_minor = 0
    refunded_minor = 0
    for r in session.execute(
        text("SELECT provider, provider_payment_id, amount_minor, currency_code, direction, "
             "status, created_at FROM billing.payment WHERE order_id = :oid ORDER BY created_at"),
        {"oid": str(order_id)},
    ).mappings().all():
        amt = int(r["amount_minor"] or 0)
        if r["direction"] == "refund":
            refunded_minor += amt
        elif r["status"] == "succeeded":
            paid_minor += amt
        payments.append({
            "provider": r["provider"],
            "reference": r["provider_payment_id"],
            "amount_minor": amt,
            "currency": r["currency_code"],
            "direction": r["direction"],
            "status": r["status"],
            "created_at": _iso(r["created_at"]),
        })

    return {
        "receipt_no": f"NP-{str(order['id']).replace('-', '')[:8].upper()}",
        "order_id": str(order["id"]),
        "club_name": club_name or "NextPoint Tennis",
        "issued_at": _iso(order["created_at"]),
        "payer_email": payer_email,
        "currency": order["currency_code"],
        "settlement_mode": order["settlement_mode"],
        "status": order["status"],
        "lines": lines,
        "amount_minor": int(order["amount_minor"] or 0),
        "payments": payments,
        "paid_minor": paid_minor,
        "refunded_minor": refunded_minor,
        "net_minor": paid_minor - refunded_minor,
    }

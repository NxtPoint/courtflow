# billing/statement.py — the UNIFIED client statement (one source of truth: unpaid orders).
#
# THE INVARIANT (docs/specs/UNIFIED-STATEMENT.md): one debt = one billing.order, settled exactly once.
# A client owes the SUM of their unpaid orders — nothing more. Commission splits and the monthly tab are
# internal CONSEQUENCES of a paid order, never a second debt. This module is the single place that reads
# "what a client owes" and settles it, so account_ledger / coach_arrears can never double-count it.
#
# Owed = billing.order.status='open' (the at_court / monthly_account close-out set; a pack bought
# pay-at-club is also an 'open' order). Online orders mid-checkout (awaiting_payment) and settlement
# orders (the pay-all vehicle) are NOT owed lines. Paid/void/written_off/refunded never appear as owed.
#
# Seams (each takes an explicit session, never commits — caller composes via db.session_scope()):
#   statement(session, club_id, user_id)        -> {items, total_owed_minor, currency, count}
#   unpaid_orders(session, club_id, user_id)     -> [line, ...]
#   create_settlement_order(session, club_id, user_id, order_ids=None)
#                                                -> {order_id, amount_minor, currency, items} | None
#   settle_settlement_order(session, settlement_order_id)   -> {settled, splits}
#   void_order(session, club_id, order_id, write_off=False) -> {ok, status} | {ok:False, error}

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

log = logging.getLogger(__name__)

OWED_STATUSES = ("open",)   # the client-owed set; online/awaiting + settlement orders are excluded

_PAY_LABEL = {
    "at_court": "Pay at the club",
    "monthly_account": "Monthly account (end of month)",
    "online": "Pay online",
    "membership_covered": "Covered by membership",
    "free": "Complimentary",
    "token": "Covered by your pack",
}


def _club_currency(session, club_id) -> str:
    return session.execute(
        text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": str(club_id)}).scalar() or "ZAR"


def unpaid_orders(session, *, club_id, user_id) -> List[Dict[str, Any]]:
    """The client's OWED orders (status='open', not already being settled by an in-flight settlement
    order), one line each, oldest-first. kind is derived from the order's first line (its booking
    type, else the product kind, else 'other'). Guarded -> []."""
    try:
        rows = session.execute(
            text("""
                SELECT o.id, o.created_at, o.amount_minor, o.currency_code, o.settlement_mode, o.status,
                       (SELECT ol.description FROM billing.order_line ol
                         WHERE ol.order_id = o.id ORDER BY ol.created_at LIMIT 1) AS description,
                       (SELECT COALESCE(b.booking_type, pr.kind)
                          FROM billing.order_line ol
                          LEFT JOIN diary.booking b  ON b.id = ol.booking_id
                          LEFT JOIN billing.price   p  ON p.id = ol.price_id
                          LEFT JOIN billing.product pr ON pr.id = p.product_id
                         WHERE ol.order_id = o.id ORDER BY ol.created_at LIMIT 1) AS kind
                FROM billing."order" o
                WHERE o.club_id = :c AND o.user_id = :u
                  AND o.status IN ('open')
                  AND o.settled_by_order_id IS NULL
                ORDER BY o.created_at ASC
            """),
            {"c": str(club_id), "u": str(user_id) if user_id else None},
        ).mappings().all()
    except Exception:
        log.debug("unpaid_orders suppressed (billing not ready)", exc_info=False)
        return []
    out = []
    for r in rows:
        mode = r["settlement_mode"]
        out.append({
            "order_id": str(r["id"]),
            "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else r["created_at"],
            "description": r["description"] or "Booking",
            "kind": (r["kind"] or "other"),
            "amount_minor": int(r["amount_minor"] or 0),
            "currency": r["currency_code"],
            "settlement_mode": mode,
            "pay_label": _PAY_LABEL.get(mode, mode),
        })
    return out


def statement(session, *, club_id, user_id) -> Dict[str, Any]:
    """The whole unified statement: every owed service as a line + ONE reconciled total. The total is
    exactly SUM(unpaid order amounts) — the only number the client owes."""
    items = unpaid_orders(session, club_id=club_id, user_id=user_id)
    total = sum(int(i["amount_minor"] or 0) for i in items)
    return {
        "items": items,
        "count": len(items),
        "total_owed_minor": total,
        "currency": (items[0]["currency"] if items else _club_currency(session, club_id)),
    }


def create_settlement_order(session, *, club_id, user_id, order_ids=None) -> Optional[Dict[str, Any]]:
    """Create ONE online (awaiting_payment) settlement order that pays the client's owed orders by card.
    `order_ids` selects which to settle (default = ALL owed). Links each covered child via
    order.settled_by_order_id; on the settlement order's charge_succeeded, settle_settlement_order marks
    each child paid + fans out its commission. Re-callable: children tied to an UNPAID prior settlement
    order are reclaimed (an abandoned checkout never locks them). Returns {order_id, amount_minor,
    currency, items} or None when nothing is owed."""
    # Reclaim children stuck on an abandoned (unpaid) settlement order so they're owed again.
    session.execute(
        text("""
            UPDATE billing."order" child
            SET settled_by_order_id = NULL, updated_at = now()
            WHERE child.club_id = :c AND child.user_id = :u AND child.status = 'open'
              AND child.settled_by_order_id IS NOT NULL
              AND child.settled_by_order_id IN (
                    SELECT id FROM billing."order" WHERE status <> 'paid')
        """),
        {"c": str(club_id), "u": str(user_id) if user_id else None},
    )

    items = unpaid_orders(session, club_id=club_id, user_id=user_id)
    if order_ids:
        want = {str(o) for o in order_ids}
        items = [i for i in items if i["order_id"] in want]
    if not items:
        return None
    total = sum(int(i["amount_minor"] or 0) for i in items)
    if total <= 0:
        return None
    currency = items[0]["currency"] or _club_currency(session, club_id)

    settle_id = session.execute(
        text('INSERT INTO billing."order" (club_id, user_id, amount_minor, currency_code, '
             "settlement_mode, status) VALUES (:c, :u, :amt, :cur, 'online', 'awaiting_payment') "
             "RETURNING id"),
        {"c": str(club_id), "u": str(user_id) if user_id else None, "amt": total, "cur": currency},
    ).scalar_one()
    settle_id = str(settle_id)
    # A summary line with NO price_id/booking so the generic commission fan-out skips it — each child
    # order's OWN lines drive its split when settle_settlement_order runs (never double-counted).
    session.execute(
        text("INSERT INTO billing.order_line (order_id, club_id, description, qty, amount_minor) "
             "VALUES (:o, :c, :desc, 1, :amt)"),
        {"o": settle_id, "c": str(club_id),
         "desc": f"Statement settlement — {len(items)} item" + ("" if len(items) == 1 else "s"),
         "amt": total},
    )
    session.execute(
        text('UPDATE billing."order" SET settled_by_order_id = :s, updated_at = now() '
             "WHERE club_id = :c AND id = ANY(:ids)"),
        {"s": settle_id, "c": str(club_id), "ids": [i["order_id"] for i in items]},
    )
    return {"order_id": settle_id, "amount_minor": total, "currency": currency, "items": len(items)}


def settle_settlement_order(session, *, settlement_order_id) -> Dict[str, Any]:
    """A settlement order was paid — mark each of its child orders 'paid' and fan out the child's
    commission split (lessons/classes). Idempotent: only acts on children still 'open'; the split insert
    is itself guarded. Called from the payment fan-out (online webhook + desk payment). Returns
    {settled, splits}."""
    pay_id = session.execute(
        text("SELECT id FROM billing.payment WHERE order_id = :o AND direction = 'charge' "
             "AND status = 'succeeded' ORDER BY created_at LIMIT 1"),
        {"o": str(settlement_order_id)},
    ).scalar()
    children = session.execute(
        text('SELECT id, club_id FROM billing."order" '
             "WHERE settled_by_order_id = :s AND status = 'open'"),
        {"s": str(settlement_order_id)},
    ).mappings().all()
    settled = 0
    splits = 0
    for ch in children:
        session.execute(
            text('UPDATE billing."order" SET status = \'paid\', updated_at = now() WHERE id = :id'),
            {"id": ch["id"]},
        )
        settled += 1
        try:
            from billing.commission import record_split_for_order
            res = record_split_for_order(session, club_id=ch["club_id"], order_id=ch["id"],
                                         payment_id=str(pay_id) if pay_id else None)
            splits += int(res.get("splits") or 0)
        except Exception:
            log.info("settle_settlement_order: split skipped for child=%s", ch["id"], exc_info=False)
        # Keep the coach's arrears view in lockstep: a lesson paid here drops off the coach's "owed"
        # tab. Status-only (commission already accrued via the split above — never double-counted).
        try:
            session.execute(
                text("UPDATE billing.coach_arrears SET status = 'collected', collected_at = now(), "
                     "updated_at = now() WHERE club_id = :c AND status = 'owed' "
                     "AND order_line_id IN (SELECT id FROM billing.order_line WHERE order_id = :o)"),
                {"c": ch["club_id"], "o": ch["id"]},
            )
        except Exception:
            pass
    return {"settled": settled, "splits": splits}


def is_settlement_order(session, *, order_id) -> bool:
    """True if this order is a 'pay all' settlement vehicle (has child orders pointing at it)."""
    return session.execute(
        text('SELECT 1 FROM billing."order" WHERE settled_by_order_id = :o LIMIT 1'),
        {"o": str(order_id)},
    ).first() is not None


def void_order(session, *, club_id, order_id, write_off=False, reason=None) -> Dict[str, Any]:
    """Clear an UNPAID order: 'void' (a mistake — never owed) or 'written_off' (a real debt forgiven).
    Only acts on an owed/in-flight order (open / awaiting_payment); a paid order must be refunded, not
    voided. Drops the line off the statement + the balance. Returns {ok, status} or {ok:False, error}."""
    new_status = "written_off" if write_off else "void"
    row = session.execute(
        text('UPDATE billing."order" SET status = :ns, updated_at = now() '
             "WHERE club_id = :c AND id = :o AND status IN ('open','awaiting_payment') "
             "RETURNING id"),
        {"ns": new_status, "c": str(club_id), "o": str(order_id)},
    ).first()
    if not row:
        return {"ok": False, "error": "NOT_OPEN"}
    return {"ok": True, "status": new_status}

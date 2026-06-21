# billing/refunds.py — refund-REQUEST CRUD (client-initiated, admin-approved).
#
# A client raises a refund REQUEST against one of THEIR paid orders; an admin later
# approves/declines it (crm-and-foundations-spec §5). This is DISTINCT from the admin's
# direct Yoco refund (yoco_billing/routes.py — the record-only money movement). The member
# never moves money here; they create a billing.refund_request row an admin actions.
#
# State machine (table CHECK enforces the value set; these functions enforce the TRANSITIONS):
#   pending --approve--> approved --(admin runs the real refund)--> refunded (terminal)
#      |                    |
#      |--decline--> declined (terminal, with note)
#      |--cancel---> cancelled (member withdrew before a decision; terminal)
#
# All functions take an explicit `session`, NEVER commit (1050 discipline — the route's
# session_scope owns the transaction). Every query is club_id-scoped; member-facing reads/
# writes are additionally user_id-scoped (a member only ever touches their OWN requests, on
# THEIR OWN orders).
#
# Public surface:
#   create_refund_request(session, *, club_id, user_id, order_id, amount_minor=None, reason=None)
#       -> (request_dict, None) | (None, error_code)   ERR: NOT_FOUND | NOT_REFUNDABLE | DUPLICATE
#   list_refund_requests(session, *, club_id, user_id) -> [request dicts]            (member: own)
#   cancel_refund_request(session, *, club_id, user_id, request_id) -> (dict|None, err)
#   list_refund_requests_admin(session, *, club_id, status=None) -> [request dicts] (admin queue)

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

log = logging.getLogger("billing.refunds")

# The only order status a client may request a refund against (only settled money is refundable).
_REFUNDABLE_ORDER_STATUSES = ("paid",)


def _iso(v) -> Optional[str]:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)


def _row_to_dict(r) -> Dict[str, Any]:
    return {
        "id": str(r["id"]),
        "order_id": str(r["order_id"]),
        "user_id": str(r["user_id"]) if r["user_id"] is not None else None,
        "amount_minor": int(r["amount_minor"]) if r["amount_minor"] is not None else None,
        "reason": r["reason"],
        "status": r["status"],
        "decided_by": str(r["decided_by"]) if r["decided_by"] is not None else None,
        "decided_at": _iso(r["decided_at"]),
        "note": r["note"],
        "created_at": _iso(r["created_at"]),
        "updated_at": _iso(r["updated_at"]),
    }


_SELECT_COLS = ("id, order_id, user_id, amount_minor, reason, status, decided_by, "
                "decided_at, note, created_at, updated_at")


# ---------------------------------------------------------------------------
# member: create / list / cancel
# ---------------------------------------------------------------------------

def create_refund_request(session, *, club_id, user_id, order_id,
                          amount_minor=None, reason=None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Create a 'pending' refund request for one of the CALLER's paid orders.

    Validation guards (the order is looked up scoped to club_id + user_id so a member can
    NEVER request a refund on someone else's / a non-existent order):
      - NOT_FOUND        : order not found OR not owned by this user (in this club)
      - NOT_REFUNDABLE   : order.status not in ('paid',)
      - DUPLICATE        : an open ('pending') request already exists for this order

    On success the requested amount defaults to the FULL paid order amount; a supplied
    amount is clamped to (0, order.amount_minor]. Returns (request_dict, None)."""
    order = session.execute(
        text('SELECT id, status, amount_minor, currency_code FROM billing."order" '
             "WHERE id = :oid AND club_id = :c AND user_id = :u"),
        {"oid": str(order_id), "c": str(club_id), "u": str(user_id)},
    ).mappings().first()
    if not order:
        return None, "NOT_FOUND"
    if order["status"] not in _REFUNDABLE_ORDER_STATUSES:
        return None, "NOT_REFUNDABLE"

    # One open request per order (defence in depth on top of the partial unique index).
    existing = session.execute(
        text("SELECT 1 FROM billing.refund_request "
             "WHERE order_id = :oid AND status = 'pending' LIMIT 1"),
        {"oid": str(order_id)},
    ).first()
    if existing:
        return None, "DUPLICATE"

    order_amt = int(order["amount_minor"] or 0)
    amt = order_amt
    if amount_minor is not None:
        try:
            amt = int(amount_minor)
        except (TypeError, ValueError):
            amt = order_amt
        if amt <= 0 or amt > order_amt:
            amt = order_amt

    reason = (reason or "").strip() or None

    try:
        row = session.execute(
            text("""
                INSERT INTO billing.refund_request
                    (club_id, order_id, user_id, amount_minor, reason, status)
                VALUES (:c, :oid, :u, :amt, :reason, 'pending')
                RETURNING """ + _SELECT_COLS),
            {"c": str(club_id), "oid": str(order_id), "u": str(user_id),
             "amt": amt, "reason": reason},
        ).mappings().first()
    except Exception:
        # The partial unique index can still race a concurrent insert -> treat as a duplicate.
        log.debug("refund_request insert conflict -> DUPLICATE", exc_info=False)
        return None, "DUPLICATE"
    return _row_to_dict(row), None


def list_refund_requests(session, *, club_id, user_id) -> List[Dict[str, Any]]:
    """The CALLER's own refund requests, most-recent first."""
    rows = session.execute(
        text("SELECT " + _SELECT_COLS + " FROM billing.refund_request "
             "WHERE club_id = :c AND user_id = :u ORDER BY created_at DESC"),
        {"c": str(club_id), "u": str(user_id)},
    ).mappings().all()
    return [_row_to_dict(r) for r in rows]


def cancel_refund_request(session, *, club_id, user_id, request_id
                          ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Member withdraws a still-'pending' request. Scoped to club_id + user_id so a member
    can only cancel their OWN. ERR: NOT_FOUND (not theirs / doesn't exist) | NOT_PENDING."""
    row = session.execute(
        text("SELECT " + _SELECT_COLS + " FROM billing.refund_request "
             "WHERE id = :id AND club_id = :c AND user_id = :u"),
        {"id": str(request_id), "c": str(club_id), "u": str(user_id)},
    ).mappings().first()
    if not row:
        return None, "NOT_FOUND"
    if row["status"] != "pending":
        return None, "NOT_PENDING"
    upd = session.execute(
        text("UPDATE billing.refund_request SET status = 'cancelled', updated_at = now() "
             "WHERE id = :id RETURNING " + _SELECT_COLS),
        {"id": str(request_id)},
    ).mappings().first()
    return _row_to_dict(upd), None


# ---------------------------------------------------------------------------
# admin: read-only queue (the thin admin follow-up; approve/decline is another lane)
# ---------------------------------------------------------------------------

def list_refund_requests_admin(session, *, club_id, status=None) -> List[Dict[str, Any]]:
    """The club's refund-request queue for the admin view, most-recent first. Joins the
    order amount/currency + the requester email (the payer). Optionally filtered by status.
    Read-only — executing the refund stays on the existing admin Yoco-refund path."""
    where = "rr.club_id = :c"
    params: Dict[str, Any] = {"c": str(club_id)}
    if status:
        where += " AND rr.status = :st"
        params["st"] = status
    rows = session.execute(
        text("""
            SELECT rr.id, rr.order_id, rr.user_id, rr.amount_minor, rr.reason, rr.status,
                   rr.decided_by, rr.decided_at, rr.note, rr.created_at, rr.updated_at,
                   o.amount_minor AS order_amount_minor, o.currency_code, o.status AS order_status,
                   u.email AS requester_email,
                   trim(coalesce(u.first_name,'') || ' ' || coalesce(u.surname,'')) AS requester_name
            FROM billing.refund_request rr
            JOIN billing."order" o ON o.id = rr.order_id
            LEFT JOIN iam.user u ON u.id = rr.user_id
            WHERE """ + where + """
            ORDER BY rr.created_at DESC
        """),
        params,
    ).mappings().all()
    out = []
    for r in rows:
        d = _row_to_dict(r)
        d.update({
            "order_amount_minor": int(r["order_amount_minor"] or 0),
            "currency_code": r["currency_code"],
            "order_status": r["order_status"],
            "requester_email": r["requester_email"],
            "requester_name": (r["requester_name"] or "").strip() or None,
        })
        out.append(d)
    return out

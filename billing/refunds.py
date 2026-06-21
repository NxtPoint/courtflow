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
# admin: decide (approve → execute the Yoco refund + mark refunded | decline → declined)
#
# The approve/decline LOGIC lives here (the admin route stays thin). Both functions:
#   - look the request up SCOPED TO club_id (a cross-club request → NOT_FOUND / 404),
#   - enforce the pending→ transition (anything not 'pending' → NOT_PENDING / 409 — this is the
#     double-action guard: approving twice can NEVER double-refund),
#   - stamp decided_by / decided_at / note,
#   - take an explicit `session`, NEVER commit (the route's session_scope owns the transaction).
#
# APPROVE executes the REAL money movement by REUSING the existing Yoco refund path
# (yoco_billing.execute_order_refund — the same checkout-id lookup + gateway call the admin
# "Recent online payments → Refund" button uses). It runs the gateway refund FIRST and only
# marks the request 'refunded' if that SUCCEEDS — a failed gateway refund raises, we return the
# error, and the request is LEFT 'pending' (the UPDATE never runs, so nothing committed). The
# route emits refund_decided after a successful decision.
# ---------------------------------------------------------------------------

def _load_pending_admin(session, *, club_id, request_id):
    """Load a request scoped to club_id; return (row_dict, error). error is NOT_FOUND (wrong
    club / missing → 404) or NOT_PENDING (already decided/cancelled → 409)."""
    row = session.execute(
        text("SELECT " + _SELECT_COLS + " FROM billing.refund_request "
             "WHERE id = :id AND club_id = :c"),
        {"id": str(request_id), "c": str(club_id)},
    ).mappings().first()
    if not row:
        return None, "NOT_FOUND"
    if row["status"] != "pending":
        return None, "NOT_PENDING"
    return _row_to_dict(row), None


def approve_refund_request(session, *, club_id, request_id, decided_by,
                           amount_minor=None, note=None
                           ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Approve a 'pending' request: execute the Yoco refund for the request's order, then mark
    it 'refunded'. Returns (request_dict, None) | (None, error_code).

    Errors: NOT_FOUND (wrong club / missing) | NOT_PENDING (already decided — the double-action
    guard) | <refund error code> (the gateway refund failed: the request is LEFT 'pending').

    The refunded amount defaults to the request's amount_minor (the member's requested figure);
    an explicit amount_minor overrides it; None throughout → a full refund (the helper sends no
    amount → Yoco's full balance)."""
    req, err = _load_pending_admin(session, club_id=club_id, request_id=request_id)
    if err:
        return None, err

    # The money FIRST — reuse the existing admin Yoco-refund path. On failure this RAISES; we
    # return the error and DO NOT mark the request refunded (it stays 'pending' — no UPDATE ran).
    from yoco_billing import execute_order_refund, RefundError
    amt = amount_minor if amount_minor is not None else req.get("amount_minor")
    try:
        execute_order_refund(session, order_id=req["order_id"], amount_minor=amt)
    except RefundError as e:
        log.warning("approve_refund_request: gateway refund failed req=%s: %s", request_id, e.message)
        return None, e.code

    note = (note or "").strip() or None
    upd = session.execute(
        text("UPDATE billing.refund_request "
             "SET status = 'refunded', decided_by = :by, decided_at = now(), "
             "    note = :note, updated_at = now() "
             "WHERE id = :id AND status = 'pending' RETURNING " + _SELECT_COLS),
        {"id": str(request_id), "by": str(decided_by) if decided_by else None, "note": note},
    ).mappings().first()
    if not upd:
        # Lost a race (another admin decided it between our load and update) — treat as already
        # actioned. The money refund above is idempotent on Yoco's side (keyed on checkout+amount).
        return None, "NOT_PENDING"
    return _row_to_dict(upd), None


def decline_refund_request(session, *, club_id, request_id, decided_by, note=None
                           ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Decline a 'pending' request → 'declined' (terminal), stamping the decider + optional note.
    No money moves. Errors: NOT_FOUND (wrong club / missing) | NOT_PENDING (already decided)."""
    _, err = _load_pending_admin(session, club_id=club_id, request_id=request_id)
    if err:
        return None, err
    note = (note or "").strip() or None
    upd = session.execute(
        text("UPDATE billing.refund_request "
             "SET status = 'declined', decided_by = :by, decided_at = now(), "
             "    note = :note, updated_at = now() "
             "WHERE id = :id AND status = 'pending' RETURNING " + _SELECT_COLS),
        {"id": str(request_id), "by": str(decided_by) if decided_by else None, "note": note},
    ).mappings().first()
    if not upd:
        return None, "NOT_PENDING"
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

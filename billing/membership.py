# billing/membership.py — self-serve membership purchase helpers (v1: one month per purchase).
#
# v1 SCOPE (label clearly): a member buys ONE MONTH of the club's membership via the existing
# Yoco ONE-OFF hosted checkout. There is NO auto-renewing Yoco subscription yet — when the
# month lapses the member re-buys. A real recurring Yoco subscription is the NEXT iteration.
#
# Two seams, both plain-SQL (every fn takes an explicit `session`, never commits — 1050 discipline):
#
#   create_membership_order(session, club_id, user_id)
#       -> {order_id, amount_minor, currency, price_id}
#     Creates a billing.order for the club's kind='membership' product (settlement 'online',
#     status 'awaiting_payment') and a PENDING billing.membership_subscription row carrying
#     order_id (the link the webhook keys off). Returns the order so the route hands order_id
#     to Pay.startYocoCheckout.
#
#   activate_membership_for_order(session, order_id, *, provider='yoco', months=1)
#       -> {ok, status: 'activated'|'extended'|'already_active'|'no_membership_order'|'order_not_paid'}
#     The webhook calls this AFTER apply_payment_event has marked the order 'paid'. It mirrors
#     admin.repositories.grant_membership (find the linked subscription row, set status='active',
#     current_period_end = today + months). IDEMPOTENT on a replay: keyed off the order's linked
#     subscription row + a guard that an already-active row for THIS order is not double-extended.
#
# Activation logic is mirrored from admin/repositories.py::grant_membership so the paid path
# and the admin-grant path converge on the same membership_subscription shape.

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import text


def _membership_price(session, *, club_id) -> Optional[Dict[str, Any]]:
    """The club's active membership price (kind='membership' product). Mirrors the lookup in
    grant_membership; also returns the amount + currency so the order carries the right total."""
    row = session.execute(
        text("SELECT p.id AS price_id, p.amount_minor, p.currency_code "
             "FROM billing.product pr "
             "JOIN billing.price p ON p.product_id = pr.id AND p.active = true "
             "WHERE pr.club_id = :c AND pr.kind = 'membership' AND pr.active = true "
             "ORDER BY p.created_at LIMIT 1"),
        {"c": str(club_id)},
    ).mappings().first()
    return dict(row) if row else None


def membership_offer(session, *, club_id) -> Optional[Dict[str, Any]]:
    """Read-only: the club's membership offer for the Buy button + status endpoint.
    {price_id, amount_minor, currency} or None if the club sells no membership."""
    p = _membership_price(session, club_id=club_id)
    if not p:
        return None
    return {"price_id": str(p["price_id"]),
            "amount_minor": int(p["amount_minor"] or 0),
            "currency": p["currency_code"]}


def create_membership_order(session, *, club_id, user_id) -> Optional[Dict[str, Any]]:
    """Create an online (awaiting_payment) order for the club's membership + a pending
    subscription row linked by order_id (the webhook's recognition key). Returns
    {order_id, amount_minor, currency, price_id} or None if the club sells no membership."""
    offer = _membership_price(session, club_id=club_id)
    if not offer:
        return None
    price_id = str(offer["price_id"])
    amount = int(offer["amount_minor"] or 0)
    currency = offer["currency_code"]

    order_id = session.execute(
        text("""
            INSERT INTO billing."order"
                (club_id, user_id, amount_minor, currency_code, settlement_mode, status)
            VALUES (:c, :u, :amt, :cur, 'online', 'awaiting_payment')
            RETURNING id
        """),
        {"c": str(club_id), "u": str(user_id) if user_id else None,
         "amt": amount, "cur": currency},
    ).scalar_one()
    order_id = str(order_id)

    # An order_line documents what was bought (powers the admin payments view + receipts).
    session.execute(
        text("""
            INSERT INTO billing.order_line
                (order_id, club_id, description, price_id, qty, amount_minor)
            VALUES (:oid, :c, 'Membership — Unlimited Courts (1 month)', :pid, 1, :amt)
        """),
        {"oid": order_id, "c": str(club_id), "pid": price_id, "amt": amount},
    )

    # PENDING subscription row, linked by order_id — the webhook activates THIS row on paid.
    # status='expired' is the placeholder pending-state (an unpaid intent must NOT count as
    # active in has_active_membership); activation flips it to 'active'.
    session.execute(
        text("""
            INSERT INTO billing.membership_subscription
                (club_id, user_id, price_id, status, provider, order_id)
            VALUES (:c, :u, :pid, 'expired', 'yoco', :oid)
        """),
        {"c": str(club_id), "u": str(user_id) if user_id else None,
         "pid": price_id, "oid": order_id},
    )

    return {"order_id": order_id, "amount_minor": amount,
            "currency": currency, "price_id": price_id}


def is_membership_order(session, *, order_id) -> bool:
    """True if this order is a self-serve membership purchase (has a linked subscription row)."""
    row = session.execute(
        text("SELECT 1 FROM billing.membership_subscription WHERE order_id = :oid LIMIT 1"),
        {"oid": str(order_id)},
    ).first()
    return row is not None


def activate_membership_for_order(session, *, order_id, provider="yoco",
                                  months=1) -> Dict[str, Any]:
    """Activate/extend the membership linked to a PAID membership order. Mirrors
    grant_membership's period maths. IDEMPOTENT: a replayed paid webhook for the same order
    will NOT double-extend (the linked row is already 'active' with a future period_end for
    this order, so we report 'already_active' and do nothing).

    Returns {ok, status, ...}. Called by the Yoco webhook AFTER apply_payment_event marks
    the order 'paid' — apply_payment_event itself is untouched (its idempotency is intact)."""
    months = max(1, int(months or 1))

    link = session.execute(
        text("SELECT id, club_id, user_id, price_id, status, current_period_end "
             "FROM billing.membership_subscription WHERE order_id = :oid "
             "ORDER BY created_at LIMIT 1"),
        {"oid": str(order_id)},
    ).mappings().first()
    if not link:
        return {"ok": True, "status": "no_membership_order"}

    # Only activate once the order is genuinely paid (defence-in-depth; the webhook already
    # gates on apply_payment_event success, but a direct call must respect order status too).
    paid = session.execute(
        text('SELECT 1 FROM billing."order" WHERE id = :oid AND status = :s'),
        {"oid": str(order_id), "s": "paid"},
    ).first()
    if not paid:
        return {"ok": True, "status": "order_not_paid"}

    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)

    # Idempotency guard: if THIS order's row is already active with a future period_end, a
    # replay must be a no-op (no second month). The order_id link is the dedupe key.
    if link["status"] == "active" and link["current_period_end"] is not None:
        return {"ok": True, "status": "already_active",
                "membership_subscription_id": str(link["id"]),
                "current_period_end": _iso(link["current_period_end"])}

    club_id = link["club_id"]
    user_id = link["user_id"]

    # If the member already holds a DIFFERENT active membership (e.g. an admin grant), extend
    # that one by `months` (stacking the paid month) and mark this purchase's row active too
    # so the order_id link stays idempotent.
    existing = session.execute(
        text("SELECT id, current_period_end FROM billing.membership_subscription "
             "WHERE club_id = :c AND user_id = :u AND status = 'active' "
             "  AND id <> :self LIMIT 1"),
        {"c": str(club_id), "u": str(user_id) if user_id else None, "self": str(link["id"])},
    ).mappings().first()

    if existing:
        new_row = session.execute(
            text("""
                UPDATE billing.membership_subscription
                SET current_period_end =
                        (GREATEST(COALESCE(current_period_end, CURRENT_DATE), CURRENT_DATE)
                         + make_interval(months => :m))::date,
                    updated_at = now()
                WHERE id = :id
                RETURNING current_period_end
            """),
            {"m": months, "id": existing["id"]},
        ).mappings().first()
        # The purchase row records the order; mark it active w/ the same end so it's not re-run.
        session.execute(
            text("UPDATE billing.membership_subscription "
                 "SET status = 'active', provider = :prov, "
                 "    current_period_end = :pe, updated_at = now() WHERE id = :id"),
            {"prov": provider, "pe": new_row["current_period_end"], "id": link["id"]},
        )
        return {"ok": True, "status": "extended",
                "membership_subscription_id": str(existing["id"]),
                "current_period_end": _iso(new_row["current_period_end"])}

    # No other active membership — activate THIS linked row for one period from today.
    row = session.execute(
        text("""
            UPDATE billing.membership_subscription
            SET status = 'active', provider = :prov,
                current_period_end = (CURRENT_DATE + make_interval(months => :m))::date,
                updated_at = now()
            WHERE id = :id
            RETURNING current_period_end
        """),
        {"prov": provider, "m": months, "id": link["id"]},
    ).mappings().first()
    return {"ok": True, "status": "activated",
            "membership_subscription_id": str(link["id"]),
            "current_period_end": _iso(row["current_period_end"])}


def membership_status(session, *, club_id, user_id) -> Dict[str, Any]:
    """Member-facing status for the Membership page. {active, current_period_end,
    price_minor, currency}. active = any active sub with NULL or future period_end (same
    predicate as diary.pricing.has_active_membership)."""
    offer = membership_offer(session, club_id=club_id)
    row = session.execute(
        text("SELECT current_period_end FROM billing.membership_subscription "
             "WHERE club_id = :c AND user_id = :u AND status = 'active' "
             "  AND (current_period_end IS NULL OR current_period_end >= CURRENT_DATE) "
             "ORDER BY current_period_end DESC NULLS FIRST LIMIT 1"),
        {"c": str(club_id), "u": str(user_id) if user_id else None},
    ).mappings().first()
    end = row["current_period_end"] if row else None
    return {
        "active": row is not None,
        "current_period_end": (end.isoformat() if hasattr(end, "isoformat") else end)
        if end is not None else None,
        "price_minor": offer["amount_minor"] if offer else None,
        "currency": offer["currency"] if offer else None,
        "sold": offer is not None,
    }

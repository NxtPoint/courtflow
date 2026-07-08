# billing/orders.py — order creation per settlement mode + desk-payment recording.
#
# THE interface Agent B (diary) codes against:
#     create_order_for_booking(session, *, club_id, user_id, lines, settlement_mode,
#                              currency_code=None, due_date=None, booking_status_hint=None)
#         -> order_id (str)
#
# B calls this when a booking is made; the settlement_mode (chosen per club.policy + role,
# docs/05 §5) decides what billing does — and, critically, what booking status B should set:
#
#   at_court            order.status='open'             booking CONFIRMED (settle at desk later)
#   monthly_account     order.status='open' + ledger    booking CONFIRMED (charge accrues on tab)
#   membership_covered  order.status='paid', amount 0   booking CONFIRMED (covered by membership)
#   free                order.status='paid', amount 0   booking CONFIRMED (complimentary)
#   online              order.status='awaiting_payment' booking HELD -> pay -> apply_payment_event
#                                                        -> confirmed (the held-path hook)
#
# create_order_for_booking returns the order_id AND (via booking_status_for_mode) tells B
# the booking status to set. B owns diary.booking; billing owns billing.*; the link is
# order_line.booking_id, which apply_payment_event uses to confirm held bookings on payment.
#
# Plain-SQL repositories; every fn takes an explicit `session`, never commits (1050 discipline).

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from billing.gateway import NormalizedPaymentEvent, MANUAL_PROVIDERS
from billing.events import apply_payment_event

log = logging.getLogger("billing.orders")

# settlement_mode -> the diary.booking status B should set when the order is created.
# 'online' is the only HELD path (await payment); everything else confirms immediately.
_BOOKING_STATUS_BY_MODE = {
    "at_court":           "confirmed",
    "monthly_account":    "confirmed",
    "membership_covered": "confirmed",
    "free":               "confirmed",
    "token":              "confirmed",  # prepaid token drawn -> confirmed (docs/specs/02)
    "online":             "held",
}

# settlement_mode -> initial billing.order status.
_ORDER_STATUS_BY_MODE = {
    "at_court":           "open",
    "monthly_account":    "open",
    "membership_covered": "paid",
    "free":               "paid",
    "token":              "paid",       # token is paid in kind (R0 order, no money moves)
    "online":             "awaiting_payment",
}


def booking_status_for_mode(settlement_mode: str) -> str:
    """Tell the diary (Agent B) which booking status to set for a settlement mode.
    Exposed so B never hard-codes the held-vs-confirmed decision."""
    return _BOOKING_STATUS_BY_MODE.get((settlement_mode or "").strip().lower(), "confirmed")


def create_order_for_booking(session, *, club_id, user_id, lines: List[Dict[str, Any]],
                             settlement_mode: str, currency_code: Optional[str] = None,
                             due_date=None, booking_status_hint: Optional[str] = None) -> str:
    """Create a billing.order (+ order_lines) for a booking and apply the settlement mode.
    THE interface Agent B calls. Returns the new order_id (str).

    Args:
      session         caller's open transaction (B composes booking+order atomically).
      club_id         tenant (NOT NULL; every billing row carries it).
      user_id         iam.user.id of the payer (nullable for ad-hoc guests).
      lines           list of dicts: {description, price_id?, qty?, amount_minor,
                      booking_id?, enrolment_id?}. amount_minor per line in cents.
      settlement_mode one of docs/05 §5: at_court|monthly_account|membership_covered|free|online.
      currency_code   defaults to the club's currency (resolved if omitted).
      due_date        for monthly_account statements (optional).

    Side effects by mode:
      membership_covered / free  -> order.amount forced to 0, status='paid' (no money).
      monthly_account            -> a ledger CHARGE for the order total accrues on the tab.
      online                     -> status='awaiting_payment'; booking stays HELD until the
                                    gateway webhook drives apply_payment_event -> confirmed.
      at_court                   -> status='open'; settled at the desk via record_desk_payment.
    """
    mode = (settlement_mode or "").strip().lower()
    if mode not in _ORDER_STATUS_BY_MODE:
        raise ValueError(f"unknown settlement_mode '{settlement_mode}'")

    currency = currency_code or _club_currency(session, club_id)
    lines = lines or []

    # membership_covered / free / token => zero amount regardless of line inputs (no money moves;
    # a token is paid in kind from a prepaid wallet — docs/specs/02).
    zero_amount = mode in ("membership_covered", "free", "token")
    total = 0 if zero_amount else sum(int(l.get("amount_minor") or 0) * int(l.get("qty") or 1)
                                      for l in lines)
    order_status = _ORDER_STATUS_BY_MODE[mode]

    order_id = session.execute(
        text("""
            INSERT INTO billing."order"
                (club_id, user_id, amount_minor, currency_code, settlement_mode, status, due_date)
            VALUES (:club_id, :user_id, :amount, :currency, :mode, :status, :due_date)
            RETURNING id
        """),
        {"club_id": str(club_id), "user_id": str(user_id) if user_id else None,
         "amount": int(total), "currency": currency, "mode": mode,
         "status": order_status, "due_date": due_date},
    ).scalar_one()
    order_id = str(order_id)

    for l in lines:
        qty = int(l.get("qty") or 1)
        unit = 0 if zero_amount else int(l.get("amount_minor") or 0)
        session.execute(
            text("""
                INSERT INTO billing.order_line
                    (order_id, club_id, description, price_id, qty, amount_minor,
                     booking_id, enrolment_id)
                VALUES (:order_id, :club_id, :description, :price_id, :qty, :amount,
                        :booking_id, :enrolment_id)
            """),
            {"order_id": order_id, "club_id": str(club_id),
             "description": l.get("description"),
             "price_id": l.get("price_id"),
             "qty": qty, "amount": unit * qty,
             "booking_id": l.get("booking_id"),
             "enrolment_id": l.get("enrolment_id")},
        )

    # monthly_account: the order itself IS the debt (status='open' → the unified statement counts
    # it as owed, settleable online any time). No separate ledger tab — that parallel store was
    # retired (the order is the single source of truth for what a client owes).

    return order_id


def record_desk_payment(session, *, club_id, order_id, amount_minor, provider="cash",
                        currency_code=None, provider_payment_id=None,
                        user_id=None) -> Dict[str, Any]:
    """Admin records money taken at the desk (cash / card_at_desk / eft) — the at_court
    settlement close-out. Routes through the SAME apply_payment_event core (via a manual
    charge_succeeded event) so the order flips to 'paid' and any held booking confirms,
    exactly like a gateway charge. Writes billing.payment with provider=cash/card_at_desk/eft.

    Idempotent: pass a provider_payment_id (e.g. a receipt number) to dedupe re-submits;
    the apply_payment_event hash + the payment unique index both guard double-recording."""
    provider = (provider or "cash").strip().lower()
    if provider not in MANUAL_PROVIDERS:
        provider = "cash"
    currency = currency_code or _club_currency(session, club_id)

    event = NormalizedPaymentEvent(
        provider=provider,
        kind="charge_succeeded",
        order_ref=str(order_id),
        provider_payment_id=provider_payment_id,
        amount_minor=int(amount_minor or 0),
        currency=currency,
        status="succeeded",
        direction="charge",
        club_id=str(club_id),
        user_id=str(user_id) if user_id else None,
        raw={"source": "desk", "provider": provider},
    )
    # Join the caller's transaction (do not open a second one).
    return apply_payment_event(event, session=session)


def reprice_booking_order(session, *, club_id, booking_id, duration_minutes) -> Dict[str, Any]:
    """Re-price a booking's UNPAID order (+ its owed coaching arrears) to a NEW duration's price —
    e.g. after a reschedule changed the lesson/court LENGTH, so the charge always matches the booked
    time (a 30-min lesson costs the 30-min price, not the 45-min price it was first booked at). The
    new price is resolved from the SAME product as the current line, so it is the coach's own service
    price for that duration — never another coach's rate.

    Safe no-op (nothing to re-price) when: there is no order, the order is already settled
    (paid/void/written_off) or a real charge has succeeded (money moved — re-pricing would need a
    refund, out of scope here), the order is a R0 mode (membership/token/free), or the new duration
    has NO configured price on that product (we never guess a price). Guarded — never raises; on any
    failure the order is left unchanged. Returns a summary dict."""
    try:
        head = session.execute(
            text('SELECT o.id AS order_id, o.status, o.settlement_mode '
                 'FROM billing.order_line ol JOIN billing."order" o ON o.id = ol.order_id '
                 'WHERE ol.booking_id = :b AND ol.club_id = :c LIMIT 1'),
            {"b": str(booking_id), "c": str(club_id)},
        ).mappings().first()
        if not head:
            return {"repriced": False, "reason": "no_order"}
        order_id = str(head["order_id"])
        if head["status"] not in ("open", "awaiting_payment"):
            return {"repriced": False, "reason": "settled", "order_id": order_id}
        if (head["settlement_mode"] or "").lower() in ("membership_covered", "free", "token"):
            return {"repriced": False, "reason": "zero_mode", "order_id": order_id}
        if session.execute(
                text("SELECT 1 FROM billing.payment WHERE order_id = :o AND direction='charge' "
                     "AND status='succeeded' LIMIT 1"), {"o": order_id}).first():
            return {"repriced": False, "reason": "charged", "order_id": order_id}

        # Re-price each line of THIS booking to the new duration's price on the SAME product.
        lines = session.execute(
            text("SELECT id, price_id, qty FROM billing.order_line "
                 "WHERE order_id = :o AND booking_id = :b"),
            {"o": order_id, "b": str(booking_id)},
        ).mappings().all()
        changed = 0
        for ln in lines:
            if not ln["price_id"]:
                continue
            newp = session.execute(
                text("SELECT p2.id AS price_id, p2.amount_minor "
                     "FROM billing.price p1 "
                     "JOIN billing.price p2 ON p2.product_id = p1.product_id AND p2.active = true "
                     "WHERE p1.id = :pid AND p2.duration_minutes = :dur "
                     "ORDER BY (p2.audience = 'any') DESC, p2.amount_minor ASC LIMIT 1"),
                {"pid": str(ln["price_id"]), "dur": int(duration_minutes)},
            ).mappings().first()
            if not newp:
                continue  # the new duration is not priced on this product — never guess a price
            qty = int(ln["qty"] or 1)
            session.execute(
                text("UPDATE billing.order_line SET price_id = :np, amount_minor = :amt "
                     "WHERE id = :id"),
                {"np": str(newp["price_id"]), "amt": int(newp["amount_minor"] or 0) * qty,
                 "id": str(ln["id"])},
            )
            changed += 1
        if not changed:
            return {"repriced": False, "reason": "no_new_price", "order_id": order_id}

        # Order total = sum of its line totals (order_line.amount_minor is already qty-weighted).
        session.execute(
            text('UPDATE billing."order" SET amount_minor = '
                 "COALESCE((SELECT SUM(amount_minor) FROM billing.order_line WHERE order_id = :o), 0) "
                 "WHERE id = :o"),
            {"o": order_id},
        )
        # Keep the owed coaching charge (arrears) in lockstep with the new line amount.
        session.execute(
            text("UPDATE billing.coach_arrears ca SET gross_minor = ol.amount_minor, updated_at = now() "
                 "FROM billing.order_line ol "
                 "WHERE ca.order_line_id = ol.id AND ca.club_id = :c AND ca.booking_id = :b "
                 "  AND ca.status = 'owed'"),
            {"c": str(club_id), "b": str(booking_id)},
        )
        total = session.execute(
            text('SELECT amount_minor FROM billing."order" WHERE id = :o'), {"o": order_id}).scalar()
        return {"repriced": True, "order_id": order_id, "lines": changed,
                "amount_minor": int(total or 0)}
    except Exception:
        log.warning("reprice_booking_order failed (order left unchanged) booking=%s", booking_id,
                    exc_info=False)
        return {"repriced": False, "reason": "error"}


def get_order(session, *, order_id) -> Optional[Dict[str, Any]]:
    row = session.execute(
        text('SELECT id, club_id, user_id, amount_minor, currency_code, settlement_mode, '
             'status, due_date, created_at FROM billing."order" WHERE id = :id'),
        {"id": str(order_id)},
    ).mappings().first()
    return dict(row) if row else None


def _club_currency(session, club_id) -> str:
    """The club's currency_code (ZAR for NextPoint). Falls back to ZAR if club row absent
    (keeps billing usable in isolation tests)."""
    try:
        cur = session.execute(
            text("SELECT currency_code FROM club.club WHERE id = :id"),
            {"id": str(club_id)},
        ).scalar_one_or_none()
        return cur or "ZAR"
    except Exception:
        return "ZAR"

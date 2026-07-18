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


def settlement_status_label(state, settlement_mode=None):
    """THE canonical human 'payment status' vocabulary — the SAME words a client sees on a receipt,
    a confirmation email, and their client-record booking rows, so wording never drifts between
    surfaces. `state` is the settled state (as produced by diary.bookings._booking_charge: covered /
    owed / pending / paid / refunded / part_refunded / written_off / void / none); `settlement_mode`
    is how they chose to pay. Pure (no DB). Both marketing_crm email and client360 delegate here."""
    sm = settlement_mode or ""
    st = state or ""
    if st == "covered":
        if sm == "token":
            return "Covered by session pack"
        if sm == "free":
            return "Free"
        return "Covered by membership"
    if st == "refunded":
        return "Refunded"
    if st == "part_refunded":
        return "Partially refunded"
    if st == "written_off":
        return "Written off"
    if st in ("void", "cancelled"):
        return "Cancelled"
    if st == "paid":
        return {"online": "Paid online", "at_court": "Paid at court",
                "monthly_account": "Paid"}.get(sm, "Paid")
    if st in ("owed", "pending", "none", "unknown", ""):
        return {"online": "Awaiting online payment", "at_court": "Pay at court",
                "monthly_account": "On monthly account (settled month-end)"}.get(sm, "Unpaid")
    return str(st).replace("_", " ").title()


def _club_currency(session, club_id) -> str:
    return session.execute(
        text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": str(club_id)}).scalar() or "ZAR"


def _reclaim_abandoned_settlements(session, *, club_id, user_id, grace_minutes=30) -> None:
    """Free any owed order still linked to an ABANDONED (unpaid) settlement order, so its debt shows
    again. An abandoned 'Pay All' checkout would otherwise hide the debt (the child is filtered out by
    settled_by_order_id) until the client retried. `grace_minutes` distinguishes an IN-FLIGHT checkout
    (recent — leave it, the client is paying right now) from an ABANDONED one (older — reclaim):
      - read path (statement) uses the grace so it never disturbs a live checkout but self-heals a
        stale one (like the lazy hold-expiry);
      - an explicit retry (create_settlement_order) passes grace_minutes=0 to free everything unpaid.
    Guarded — a failure never blanks the read."""
    try:
        session.execute(
            text("""
                UPDATE billing."order" child
                SET settled_by_order_id = NULL, updated_at = now()
                WHERE child.club_id = :c AND child.user_id = :u AND child.status = 'open'
                  AND child.settled_by_order_id IS NOT NULL
                  AND child.settled_by_order_id IN (
                        SELECT id FROM billing."order"
                        WHERE status <> 'paid'
                          -- clock_timestamp() (real wall-clock, not the fixed transaction now())
                          -- so the age test is correct both in prod and within one test transaction.
                          AND created_at < clock_timestamp() - make_interval(mins => :g))
            """),
            {"c": str(club_id), "u": str(user_id) if user_id else None, "g": int(grace_minutes)},
        )
    except Exception:
        log.debug("reclaim skipped (billing not ready)", exc_info=False)


def _void_phantom_cancelled_orders(session, *, club_id, user_id) -> None:
    """Self-heal PHANTOM owed orders: an 'open' order whose EVENT(s) — bookings AND/OR class
    enrolments — were ALL cancelled but the order was never voided (a cancel BEFORE the cancel path
    learned to void — you shouldn't owe for a cancelled booking/class). Voids each via void_order
    (which also clears any coach_arrears). SAFE + narrow: only touches an order that (a) has ≥1 event
    line (booking or enrolment) and (b) has NO active (non-cancelled) event — so membership/pack orders
    (no event line) and any order with a live event are never touched. Guarded — never blanks the read."""
    try:
        ids = session.execute(
            text("""
                SELECT o.id FROM billing."order" o
                WHERE o.club_id = :c AND o.user_id = :u AND o.status = 'open'
                  AND o.settled_by_order_id IS NULL
                  -- has at least one EVENT line (a booking OR a class enrolment)
                  AND EXISTS (SELECT 1 FROM billing.order_line ol
                               WHERE ol.order_id = o.id
                                 AND (ol.booking_id IS NOT NULL OR ol.enrolment_id IS NOT NULL))
                  -- and NO event line is still active (a non-cancelled booking OR non-cancelled enrolment)
                  AND NOT EXISTS (
                        SELECT 1 FROM billing.order_line ol
                        LEFT JOIN diary.booking   b ON b.id = ol.booking_id
                        LEFT JOIN diary.enrolment e ON e.id = ol.enrolment_id
                        WHERE ol.order_id = o.id
                          AND ((ol.booking_id   IS NOT NULL AND b.status <> 'cancelled')
                            OR (ol.enrolment_id IS NOT NULL AND e.status <> 'cancelled')))
            """),
            {"c": str(club_id), "u": str(user_id) if user_id else None},
        ).scalars().all()
        for oid in ids:
            void_order(session, club_id=club_id, order_id=oid, reason="booking cancelled (cleanup)")
    except Exception:
        log.debug("phantom-void skipped (billing not ready)", exc_info=False)


def _void_written_off_arrears_orders(session, *, club_id, user_id) -> None:
    """Self-heal: an 'open' order whose COACHING arrears was WRITTEN OFF (by the coach/admin) should
    itself be written off — lockstep. Heals lessons written off BEFORE adjust_arrears learned to void
    the client order (you shouldn't owe for a lesson the coach waived). Narrow: only an open order with
    a written_off arrears line. Guarded — never blanks the read."""
    try:
        ids = session.execute(
            text("""
                SELECT DISTINCT o.id FROM billing."order" o
                JOIN billing.order_line ol ON ol.order_id = o.id
                JOIN billing.coach_arrears a ON a.order_line_id = ol.id
                WHERE o.club_id = :c AND o.user_id = :u AND o.status = 'open'
                  AND o.settled_by_order_id IS NULL AND a.status = 'written_off'
            """),
            {"c": str(club_id), "u": str(user_id) if user_id else None},
        ).scalars().all()
        for oid in ids:
            void_order(session, club_id=club_id, order_id=oid, write_off=True,
                       reason="coaching written off (cleanup)")
    except Exception:
        log.debug("written-off-arrears self-heal skipped", exc_info=False)


def unpaid_orders(session, *, club_id, user_id) -> List[Dict[str, Any]]:
    """The client's OWED orders (status='open', not already being settled by an in-flight settlement
    order), one line each, oldest-first. kind is derived from the order's first line (its booking
    type, else the product kind, else 'other'). Guarded -> []."""
    _reclaim_abandoned_settlements(session, club_id=club_id, user_id=user_id)  # stale-only (grace)
    _void_phantom_cancelled_orders(session, club_id=club_id, user_id=user_id)  # clear cancelled-booking debt
    _void_written_off_arrears_orders(session, club_id=club_id, user_id=user_id)  # clear written-off coaching debt
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
                         WHERE ol.order_id = o.id ORDER BY ol.created_at LIMIT 1) AS kind,
                       (SELECT COALESCE(cp.display_name,
                                        NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.surname)), ''), u.email)
                          FROM billing.order_line ol
                          LEFT JOIN diary.booking b  ON b.id = ol.booking_id
                          LEFT JOIN billing.price   p  ON p.id = ol.price_id
                          LEFT JOIN billing.product pr ON pr.id = p.product_id
                          LEFT JOIN iam."user" u ON u.id = COALESCE(pr.coach_user_id, b.coach_user_id)
                          LEFT JOIN iam.coach_profile cp ON cp.club_id = o.club_id
                               AND cp.user_id = COALESCE(pr.coach_user_id, b.coach_user_id)
                         WHERE ol.order_id = o.id AND COALESCE(pr.coach_user_id, b.coach_user_id) IS NOT NULL
                         ORDER BY ol.created_at LIMIT 1) AS coach_name,
                       (SELECT b.starts_at FROM billing.order_line ol
                          JOIN diary.booking b ON b.id = ol.booking_id
                         WHERE ol.order_id = o.id ORDER BY ol.created_at LIMIT 1) AS starts_at,
                       EXISTS (SELECT 1 FROM billing.token_wallet w WHERE w.order_id = o.id) AS is_pack,
                       EXISTS (SELECT 1 FROM billing.membership_subscription ms WHERE ms.order_id = o.id) AS is_membership
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
        kind = (r["kind"] or "other")
        # Category drives the statement's grouping headings.
        if r["is_pack"]:
            category = "Session packs"
        elif r["is_membership"]:
            category = "Membership"
        elif kind == "lesson":
            category = "Coaching"
        elif kind == "court":
            category = "Court hire"
        elif kind == "class":
            category = "Classes"
        else:
            category = "Other"
        # The line's own date: a booking's start time if present, else when the order was raised.
        when = r["starts_at"] or r["created_at"]
        out.append({
            "order_id": str(r["id"]),
            "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else r["created_at"],
            "date": when.isoformat() if hasattr(when, "isoformat") else when,
            "description": r["description"] or "Booking",
            "kind": kind,
            "category": category,
            "coach_name": r["coach_name"],
            "amount_minor": int(r["amount_minor"] or 0),
            "currency": r["currency_code"],
            "settlement_mode": mode,
            "pay_label": _PAY_LABEL.get(mode, mode),
            "status": "Owed",
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
    # An explicit retry frees EVERY still-unpaid prior settlement (grace 0), so a client who abandons
    # and immediately clicks Pay again isn't blocked by the in-flight window unpaid_orders honours.
    _reclaim_abandoned_settlements(session, club_id=club_id, user_id=user_id, grace_minutes=0)
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
            # record_split_for_order ALSO clears the child's owed coach_arrears (the lockstep now
            # lives in one place — every settle path drops the lesson off the coach's owed tab).
            res = record_split_for_order(session, club_id=ch["club_id"], order_id=ch["id"],
                                         payment_id=str(pay_id) if pay_id else None)
            splits += int(res.get("splits") or 0)
        except Exception:
            log.info("settle_settlement_order: split skipped for child=%s", ch["id"], exc_info=False)
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
    # LOCKSTEP: a voided/written-off lesson must NOT stay 'owed' on the coach's tab — otherwise the
    # coach could 'mark collected' a debt the club just forgave and earn commission on it. Drop it.
    session.execute(
        text("UPDATE billing.coach_arrears SET status = 'written_off', updated_at = now() "
             "WHERE club_id = :c AND status = 'owed' AND order_line_id IN "
             "(SELECT id FROM billing.order_line WHERE order_id = :o)"),
        {"c": str(club_id), "o": str(order_id)},
    )
    # PURCHASE cleanup: voiding an unpaid MEMBERSHIP order cancels its subscription; an unpaid/pending
    # PACK order expires its wallet — so nothing it granted is left dangling (a no-op for booking orders,
    # whose order_id links no subscription/wallet). This is what lets "cancel the unpaid membership/pack"
    # from its transaction record fully unwind it.
    session.execute(
        text("UPDATE billing.membership_subscription SET status = 'cancelled', updated_at = now() "
             "WHERE club_id = :c AND order_id = :o AND status <> 'cancelled'"),
        {"c": str(club_id), "o": str(order_id)},
    )
    session.execute(
        text("UPDATE billing.token_wallet "
             "SET status = 'expired', minutes_remaining = 0, tokens_remaining = 0, updated_at = now() "
             "WHERE club_id = :c AND order_id = :o AND status IN ('pending','active')"),
        {"c": str(club_id), "o": str(order_id)},
    )
    # Free any promo-redemption slot this order held — a voided order must not burn a promo use.
    try:
        from billing import promotions
        promotions.reverse_for_order(session, order_id)
    except Exception:
        pass
    return {"ok": True, "status": new_status}


def discount_order(session, *, club_id, order_id, discount_minor=None, new_amount_minor=None,
                   reason, actor_user_id=None) -> Dict[str, Any]:
    """Apply a discount to ANY open order (court / lesson / class / pack / membership) — reduce what the
    client owes WITHOUT inventing a second debt or a settlement path. Acts on the ONE debt store
    (billing.order + billing.order_line); the order total is the sum of its lines.

    Provide EXACTLY ONE of:
      * discount_minor     — subtract this many minor units from the order total, OR
      * new_amount_minor    — set the order total to this figure (the discount is the difference).

    Guards (mirror void_order): only an owed/in-flight order (open / awaiting_payment) can be discounted
    — a PAID order must be REFUNDED, not discounted. A new total < 0 is rejected (DISCOUNT_EXCEEDS_TOTAL);
    a no-op (new total == current) is rejected (NO_CHANGE).

    MULTI-LINE RULE: the discount applies to the WHOLE order total and each line is scaled PRO-RATA by
    its current amount (remainder lands on the last line so the lines always re-sum to the new total
    exactly). A single-line order therefore just takes the whole discount.

    LOCKSTEP (coaching): a line that funds an OWED coach_arrears row is re-priced by DELEGATING to
    commission.adjust_arrears(gross_minor=new_line) — the SAME re-price mechanism used on a duration
    change / a coach discount — so the coach's owed/commission view drops by exactly the same amount and
    the lockstep lives in ONE place. A non-coaching line is re-priced directly here. Both paths preserve
    the pre-discount price in order_line.original_amount_minor (set once — the audit of "was → now").

    Returns {order_id, old_total_minor, new_total_minor, discount_minor, status, reason}
    or {ok: False, error} on a guard failure."""
    # --- validate the ask: exactly one of discount_minor / new_amount_minor ---
    has_disc = discount_minor is not None
    has_new = new_amount_minor is not None
    if has_disc == has_new:  # both or neither
        raise ValueError("BAD_ARGS")

    # --- load the order, scoped, and guard its status (owed/in-flight only) ---
    head = session.execute(
        text('SELECT id, amount_minor, status FROM billing."order" '
             "WHERE club_id = :c AND id = :o"),
        {"c": str(club_id), "o": str(order_id)},
    ).mappings().first()
    if not head:
        return {"ok": False, "error": "ORDER_NOT_FOUND"}
    if head["status"] not in ("open", "awaiting_payment"):
        return {"ok": False, "error": "NOT_OPEN", "status": head["status"]}

    # The lines are the authoritative basis (the order total is their sum). Use line_sum as the current
    # total so the pro-rata split always re-sums exactly to the new total (no drift vs the stored value).
    lines = session.execute(
        text("SELECT id, amount_minor FROM billing.order_line "
             "WHERE order_id = :o ORDER BY created_at"),
        {"o": str(order_id)},
    ).mappings().all()
    current_total = sum(int(l["amount_minor"] or 0) for l in lines)

    # --- resolve the target new total + the effective discount ---
    if has_new:
        new_total = int(new_amount_minor)
    else:
        new_total = current_total - int(discount_minor)
    discount = current_total - new_total

    if new_total < 0:
        return {"ok": False, "error": "DISCOUNT_EXCEEDS_TOTAL"}
    if discount == 0:
        return {"ok": False, "error": "NO_CHANGE"}
    if not lines or current_total <= 0:
        # nothing priced to discount (a R0 / empty order)
        return {"ok": False, "error": "NO_CHANGE"}

    # --- distribute the new total across the lines, pro-rata by current amount ---
    allocated = 0
    new_line_amounts: List[int] = []
    last = len(lines) - 1
    for i, l in enumerate(lines):
        if i == last:
            nl = new_total - allocated          # remainder — keeps the lines summing to new_total exactly
        else:
            amt = int(l["amount_minor"] or 0)
            nl = round(amt * new_total / current_total)
            allocated += nl
        new_line_amounts.append(int(nl))

    # --- apply per line: coaching lines DELEGATE to adjust_arrears (lockstep in one place) ---
    for l, nl in zip(lines, new_line_amounts):
        arrears_id = session.execute(
            text("SELECT id FROM billing.coach_arrears "
                 "WHERE club_id = :c AND order_line_id = :ol AND status = 'owed' LIMIT 1"),
            {"c": str(club_id), "ol": str(l["id"])},
        ).scalar()
        if arrears_id:
            # adjust_arrears sets original_amount_minor (once), drops the line + the coach's gross to
            # `nl`, and recomputes the order total — the SAME re-price the duration-change path uses.
            from billing.commission import adjust_arrears
            adjust_arrears(session, club_id=club_id, arrears_id=arrears_id,
                           gross_minor=int(nl), actor_user_id=actor_user_id, reason=reason)
        else:
            # Non-coaching line: preserve the pre-discount price ONCE, then drop to the new amount.
            session.execute(
                text("UPDATE billing.order_line SET "
                     "  original_amount_minor = COALESCE(original_amount_minor, amount_minor), "
                     "  amount_minor = :nl "
                     "WHERE id = :id"),
                {"nl": int(nl), "id": str(l["id"])},
            )

    # --- recompute the order total from its lines (covers non-coaching lines; idempotent for coaching) ---
    session.execute(
        text('UPDATE billing."order" SET amount_minor = '
             "COALESCE((SELECT SUM(amount_minor) FROM billing.order_line WHERE order_id = :o), 0), "
             "updated_at = now() WHERE club_id = :c AND id = :o"),
        {"c": str(club_id), "o": str(order_id)},
    )
    return {"order_id": str(order_id), "old_total_minor": current_total,
            "new_total_minor": new_total, "discount_minor": discount,
            "status": head["status"], "reason": reason}

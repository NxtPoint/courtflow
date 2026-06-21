# billing/membership.py — self-serve membership purchase helpers (configurable TERM PLANS).
#
# SCOPE: a member buys a chosen membership TERM PLAN (label · amount · duration) via the existing
# Yoco ONE-OFF hosted checkout, and gets membership for THAT plan's `term_months`. There is NO
# auto-renewing Yoco subscription yet — when the term lapses the member re-buys. A real recurring
# Yoco subscription is a later iteration.
#
# NOTHING IS HARDCODED: a term plan = one billing.price row on the club's kind='membership' product
# carrying {term_months, label, amount_minor}. The owner CRUDs the plans in Settings; the member
# picks one; activation grants exactly that plan's term_months (derived from the order's linked
# price — never a literal). 1-month / 3-month / N-month are all just data.
#
# Seams, all plain-SQL (each fn takes an explicit `session`, never commits — 1050 discipline):
#
#   membership_plans(session, club_id)
#       -> [{price_id, label, amount_minor, term_months, currency, active}]  (active plans, cheapest-first)
#
#   create_membership_order(session, club_id, user_id, price_id=None)
#       -> {order_id, amount_minor, currency, price_id, term_months, label}
#     Picks the chosen plan (price_id) or the CHEAPEST active plan when omitted. Creates an
#     'online'/'awaiting_payment' billing.order for THAT plan's amount + a PENDING
#     membership_subscription row carrying order_id (the link the webhook keys off).
#
#   activate_membership_for_order(session, order_id, *, provider='yoco', months=None)
#       -> {ok, status: 'activated'|'extended'|'already_active'|'no_membership_order'|'order_not_paid'}
#     The webhook calls this AFTER apply_payment_event has marked the order 'paid'. The granted
#     duration is the LINKED PLAN's term_months (read off the subscription's price_id), so each
#     term grants its own length. IDEMPOTENT on a replay: keyed off the order's linked subscription
#     row + a guard that an already-active row for THIS order is not double-extended.
#
# Activation logic mirrors admin/repositories.py::grant_membership so the paid path and the
# admin-grant path converge on the same membership_subscription shape.

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import text


def _club_currency(session, *, club_id) -> str:
    cur = session.execute(
        text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": str(club_id)},
    ).scalar()
    return cur or "ZAR"


def membership_product_id(session, *, club_id, create_if_missing=False) -> Optional[str]:
    """The club's kind='membership' product id (the catalogue node term plans hang off).
    Optionally create it (used by the admin add-plan path so a club with no membership product
    yet can start selling). Idempotent: returns the existing product if present."""
    pid = session.execute(
        text("SELECT id FROM billing.product "
             "WHERE club_id = :c AND kind = 'membership' ORDER BY created_at LIMIT 1"),
        {"c": str(club_id)},
    ).scalar()
    if pid:
        return str(pid)
    if not create_if_missing:
        return None
    pid = session.execute(
        text("INSERT INTO billing.product (club_id, kind, name, active) "
             "VALUES (:c, 'membership', 'Unlimited Courts Membership', true) RETURNING id"),
        {"c": str(club_id)},
    ).scalar_one()
    return str(pid)


def _plan_label(label, term_months) -> str:
    """Display label for a plan: the explicit label, else derive from term_months
    ('1 month' / 'N months'), else a generic fallback."""
    if label:
        return label
    m = int(term_months or 0)
    if m == 1:
        return "1 month"
    if m > 1:
        return f"{m} months"
    return "Membership"


def membership_plans(session, *, club_id, active_only=True) -> List[Dict[str, Any]]:
    """The club's configured membership TERM PLANS (active by default), cheapest-first.
    Each plan = {price_id, label, amount_minor, term_months, currency, active}. A term plan is a
    billing.price row on the membership product with term_months SET (so we never pick up a stray
    non-term price)."""
    where = "AND p.active = true" if active_only else ""
    rows = session.execute(
        text("SELECT p.id AS price_id, p.label, p.amount_minor, p.term_months, "
             "       p.currency_code, p.active "
             "FROM billing.product pr "
             "JOIN billing.price p ON p.product_id = pr.id "
             "WHERE pr.club_id = :c AND pr.kind = 'membership' "
             "  AND p.term_months IS NOT NULL " + where + " "
             "ORDER BY p.amount_minor ASC, p.term_months ASC, p.created_at ASC"),
        {"c": str(club_id)},
    ).mappings().all()
    out = []
    for r in rows:
        tm = int(r["term_months"]) if r["term_months"] is not None else None
        out.append({
            "price_id": str(r["price_id"]),
            "label": _plan_label(r["label"], tm),
            "amount_minor": int(r["amount_minor"] or 0),
            "term_months": tm,
            "currency": r["currency_code"],
            "active": bool(r["active"]),
        })
    return out


def _plan_for_price(session, *, club_id, price_id) -> Optional[Dict[str, Any]]:
    """A single active term plan by price_id, scoped to the club + the membership product."""
    r = session.execute(
        text("SELECT p.id AS price_id, p.label, p.amount_minor, p.term_months, p.currency_code "
             "FROM billing.product pr "
             "JOIN billing.price p ON p.product_id = pr.id "
             "WHERE pr.club_id = :c AND pr.kind = 'membership' "
             "  AND p.id = :pid AND p.active = true AND p.term_months IS NOT NULL"),
        {"c": str(club_id), "pid": str(price_id)},
    ).mappings().first()
    if not r:
        return None
    tm = int(r["term_months"]) if r["term_months"] is not None else None
    return {"price_id": str(r["price_id"]), "label": _plan_label(r["label"], tm),
            "amount_minor": int(r["amount_minor"] or 0), "term_months": tm,
            "currency": r["currency_code"]}


def _term_months_for_order(session, *, order_id) -> int:
    """The term_months granted by the plan linked to a membership order (via the subscription
    row's price_id). Falls back to 1 if a plan somehow lacks term_months (defensive — a term
    plan always has it). NEVER a hardcoded literal in the happy path."""
    tm = session.execute(
        text("SELECT pr.term_months "
             "FROM billing.membership_subscription ms "
             "JOIN billing.price pr ON pr.id = ms.price_id "
             "WHERE ms.order_id = :oid ORDER BY ms.created_at LIMIT 1"),
        {"oid": str(order_id)},
    ).scalar()
    return max(1, int(tm or 1))


def membership_offer(session, *, club_id) -> Optional[Dict[str, Any]]:
    """Read-only: the club's DEFAULT (cheapest) membership term plan, for the status endpoint's
    headline price. {price_id, amount_minor, currency, term_months, label} or None if no plans."""
    plans = membership_plans(session, club_id=club_id)
    if not plans:
        return None
    p = plans[0]
    return {"price_id": p["price_id"], "amount_minor": p["amount_minor"],
            "currency": p["currency"], "term_months": p["term_months"], "label": p["label"]}


def create_membership_order(session, *, club_id, user_id, price_id=None) -> Optional[Dict[str, Any]]:
    """Create an online (awaiting_payment) order for the chosen membership term plan + a pending
    subscription row linked by order_id (the webhook's recognition key). `price_id` selects the
    plan; if omitted, the CHEAPEST active plan is used. Returns
    {order_id, amount_minor, currency, price_id, term_months, label} or None if the club sells no
    membership / the chosen plan is invalid."""
    plan = None
    if price_id:
        plan = _plan_for_price(session, club_id=club_id, price_id=price_id)
    if plan is None:
        # default to the cheapest active plan (membership_plans is cheapest-first)
        plans = membership_plans(session, club_id=club_id)
        if not plans:
            return None
        plan = plans[0]

    price_id = plan["price_id"]
    amount = int(plan["amount_minor"] or 0)
    currency = plan["currency"]
    term_months = plan["term_months"]
    label = plan["label"]

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
            VALUES (:oid, :c, :desc, :pid, 1, :amt)
        """),
        {"oid": order_id, "c": str(club_id),
         "desc": f"Membership — Unlimited Courts ({label})", "pid": price_id, "amt": amount},
    )

    # PENDING subscription row, linked by order_id + carrying the chosen plan's price_id — the
    # webhook activates THIS row on paid and reads term_months off the linked price. status
    # 'expired' is the placeholder pending-state (an unpaid intent must NOT count as active in
    # has_active_membership); activation flips it to 'active'.
    session.execute(
        text("""
            INSERT INTO billing.membership_subscription
                (club_id, user_id, price_id, status, provider, order_id)
            VALUES (:c, :u, :pid, 'expired', 'yoco', :oid)
        """),
        {"c": str(club_id), "u": str(user_id) if user_id else None,
         "pid": price_id, "oid": order_id},
    )

    return {"order_id": order_id, "amount_minor": amount, "currency": currency,
            "price_id": price_id, "term_months": term_months, "label": label}


def is_membership_order(session, *, order_id) -> bool:
    """True if this order is a self-serve membership purchase (has a linked subscription row)."""
    row = session.execute(
        text("SELECT 1 FROM billing.membership_subscription WHERE order_id = :oid LIMIT 1"),
        {"oid": str(order_id)},
    ).first()
    return row is not None


def activate_membership_for_order(session, *, order_id, provider="yoco",
                                  months=None) -> Dict[str, Any]:
    """Activate/extend the membership linked to a PAID membership order. The granted duration is
    the LINKED PLAN's term_months (read off the subscription row's price_id) unless an explicit
    `months` override is passed. Mirrors grant_membership's period maths. IDEMPOTENT: a replayed
    paid webhook for the same order will NOT double-extend (the linked row is already 'active'
    with a future period_end for this order, so we report 'already_active' and do nothing).

    Returns {ok, status, ...}. Called by the Yoco webhook AFTER apply_payment_event marks the
    order 'paid' — apply_payment_event itself is untouched (its idempotency is intact)."""
    # The term comes from the plan, NOT a hardcoded literal. An explicit `months` (e.g. a test or
    # an admin override) wins if supplied.
    if months is None:
        months = _term_months_for_order(session, order_id=order_id)
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
    # replay must be a no-op (no second term). The order_id link is the dedupe key.
    if link["status"] == "active" and link["current_period_end"] is not None:
        return {"ok": True, "status": "already_active",
                "membership_subscription_id": str(link["id"]),
                "term_months": months,
                "current_period_end": _iso(link["current_period_end"])}

    club_id = link["club_id"]
    user_id = link["user_id"]

    # If the member already holds a DIFFERENT active membership (e.g. an admin grant), extend
    # that one by `months` (stacking the paid term) and mark this purchase's row active too so
    # the order_id link stays idempotent.
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
                "term_months": months,
                "current_period_end": _iso(new_row["current_period_end"])}

    # No other active membership — activate THIS linked row for one term from today.
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
            "term_months": months,
            "current_period_end": _iso(row["current_period_end"])}


def membership_status(session, *, club_id, user_id) -> Dict[str, Any]:
    """Member-facing status for the Membership page. {active, current_period_end, price_minor,
    currency, sold, plans}. `plans` are the configured term plans the member can buy. active =
    any active sub with NULL or future period_end (same predicate as
    diary.pricing.has_active_membership)."""
    plans = membership_plans(session, club_id=club_id)
    offer = plans[0] if plans else None
    row = session.execute(
        text("SELECT current_period_end, provider, "
             "       (current_period_end - CURRENT_DATE) AS days_left "
             "FROM billing.membership_subscription "
             "WHERE club_id = :c AND user_id = :u AND status = 'active' "
             "  AND (current_period_end IS NULL OR current_period_end >= CURRENT_DATE) "
             "ORDER BY current_period_end DESC NULLS FIRST LIMIT 1"),
        {"c": str(club_id), "u": str(user_id) if user_id else None},
    ).mappings().first()
    end = row["current_period_end"] if row else None
    is_trial = bool(row and row["provider"] == "trial")
    days_left = int(row["days_left"]) if (row and row["days_left"] is not None) else None
    return {
        "active": row is not None,
        "current_period_end": (end.isoformat() if hasattr(end, "isoformat") else end)
        if end is not None else None,
        "is_trial": is_trial,                       # the signup free-week (provider='trial')
        "trial_days_left": days_left if is_trial else None,
        "price_minor": offer["amount_minor"] if offer else None,
        "currency": offer["currency"] if offer else None,
        "sold": bool(plans),
        "plans": plans,
    }


def grant_signup_trial(session, *, club_id, user_id, days=7) -> Dict[str, Any]:
    """Grant a new member a time-boxed FREE-WEEK: an active membership_subscription (provider='trial')
    whose current_period_end = today + `days`. Reuses the membership engine wholesale — this makes
    COURT bookings free (has_active_membership -> settlement_mode='membership_covered', courts-only
    guard in diary.bookings), and lapses automatically with NO cron (the active-check is date-bounded).

    IDEMPOTENT + one-shot: grants ONLY if the member has NEVER held any subscription (no paid plan, no
    prior trial) — so it can't double-grant on repeated logins and an expired trial is never reissued.
    Returns {granted: bool, current_period_end?, reason?}. Never raises on a benign skip."""
    if not user_id or int(days) <= 0:
        return {"granted": False, "reason": "disabled"}
    existing = session.execute(
        text("SELECT 1 FROM billing.membership_subscription "
             "WHERE club_id = :c AND user_id = :u LIMIT 1"),
        {"c": str(club_id), "u": str(user_id)},
    ).first()
    if existing:
        return {"granted": False, "reason": "already_has_subscription"}
    row = session.execute(
        text("""
            INSERT INTO billing.membership_subscription
                (club_id, user_id, price_id, status, provider, order_id, current_period_end)
            VALUES (:c, :u, NULL, 'active', 'trial', NULL,
                    (CURRENT_DATE + make_interval(days => :d))::date)
            RETURNING current_period_end
        """),
        {"c": str(club_id), "u": str(user_id), "d": int(days)},
    ).mappings().first()
    end = row["current_period_end"] if row else None
    return {"granted": True,
            "current_period_end": end.isoformat() if hasattr(end, "isoformat") else end}

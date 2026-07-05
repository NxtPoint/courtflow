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
        text("SELECT p.id AS price_id, p.label, p.amount_minor, p.term_months, p.membership_tier, "
             "       p.currency_code, p.active, p.access_days, p.access_start_min, p.access_end_min, "
             "       p.payment_modes "
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
            "tier": (r["membership_tier"] or None),
            "currency": r["currency_code"],
            "active": bool(r["active"]),
            # Per-tier payment preference (None = inherit the membership default / global).
            "payment_modes": _csv_modes(r["payment_modes"]),
            # Access window (Phase 5): a human summary for the purchase page (None = covers any time).
            "access_summary": _window_summary(r["access_days"], r["access_start_min"], r["access_end_min"]),
        })
    return out


def _window_summary(days_csv, start_min, end_min):
    """Concise human label for a membership access window, e.g. 'Courts free weekdays 06:00–17:00'.
    None when there's no constraint (covers any time)."""
    if not days_csv and start_min is None and end_min is None:
        return None
    nums = sorted(int(x) for x in days_csv.split(",")) if days_csv else list(range(1, 8))
    names = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
    if nums == [1, 2, 3, 4, 5]:
        day_txt = "weekdays"
    elif nums == [6, 7]:
        day_txt = "weekends"
    elif len(nums) == 7:
        day_txt = "any day"
    else:
        day_txt = "/".join(names[n] for n in nums)

    def hhmm(m):
        return f"{m // 60:02d}:{m % 60:02d}"
    if start_min is not None and end_min is not None:
        time_txt = f"{hhmm(start_min)}–{hhmm(end_min)}"
    elif end_min is not None:
        time_txt = f"before {hhmm(end_min)}"
    elif start_min is not None:
        time_txt = f"after {hhmm(start_min)}"
    else:
        time_txt = ""
    return ("Courts free " + day_txt + (" " + time_txt if time_txt else "")).strip()


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


# A membership may be paid online (Yoco) OR settled offline (at the desk / on the monthly tab) —
# exactly the same settlement vocabulary bookings use. Offline modes activate the membership
# immediately (the member can play now) and leave an 'open' order that the desk collects later.
MEMBERSHIP_PAY_MODES = ("online", "at_court", "monthly_account")


def _csv_modes(csv) -> Optional[List[str]]:
    if not csv:
        return None
    modes = [m.strip() for m in str(csv).split(",") if m.strip() in MEMBERSHIP_PAY_MODES]
    return modes or None


def membership_payment_modes(session, *, club_id) -> Optional[List[str]]:
    """The membership PRODUCT's payment preference (the default for every membership), or None =
    inherit the club's global enabled methods. Mirrors product.payment_modes for other services."""
    pid = membership_product_id(session, club_id=club_id)
    if not pid:
        return None
    return _csv_modes(session.execute(
        text("SELECT payment_modes FROM billing.product WHERE id = :p"), {"p": pid}).scalar())


def membership_modes_pref(session, *, club_id, price_id=None) -> Optional[List[str]]:
    """The LAYERED payment preference for buying a membership: the chosen tier's price-level
    payment_modes if set, else the membership product's default, else None (= inherit the club's
    global enabled methods). This is what makes payment options per-membership configurable."""
    if price_id:
        per_price = _csv_modes(session.execute(
            text("SELECT payment_modes FROM billing.price WHERE club_id = :c AND id = :p"),
            {"c": str(club_id), "p": str(price_id)}).scalar())
        if per_price:
            return per_price
    return membership_payment_modes(session, club_id=club_id)


def set_membership_payment_modes(session, *, club_id, modes) -> bool:
    """Persist the membership product's payment preference (a list, or None = inherit global).
    Creates the membership product if the club doesn't have one yet."""
    pid = membership_product_id(session, club_id=club_id, create_if_missing=True)
    if modes is None:
        csv = None
    else:
        clean = [m for m in modes if m in MEMBERSHIP_PAY_MODES]
        csv = ",".join(clean) if clean else None
    session.execute(
        text("UPDATE billing.product SET payment_modes = :m, updated_at = now() WHERE id = :p"),
        {"m": csv, "p": pid})
    return True


def create_membership_order(session, *, club_id, user_id, price_id=None,
                            settlement_mode="online") -> Optional[Dict[str, Any]]:
    """Create an order for the chosen membership term plan + a subscription row linked by order_id.
    `price_id` selects the plan; if omitted, the CHEAPEST active plan is used. `settlement_mode` is
    one of MEMBERSHIP_PAY_MODES:
      online           -> 'awaiting_payment' order; member pays via Yoco; the webhook activates.
      at_court/monthly -> 'open' order (owed, settled at the desk / on the tab); the membership is
                          activated IMMEDIATELY so the member can play now.
    Returns {order_id, amount_minor, currency, price_id, term_months, label, settlement_mode,
    needs_checkout, activated} or None if the club sells no membership / the chosen plan is invalid."""
    mode = (settlement_mode or "online").strip().lower()
    if mode not in MEMBERSHIP_PAY_MODES:
        mode = "online"
    online = (mode == "online")

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

    order_status = "awaiting_payment" if online else "open"
    provider = "yoco" if online else "manual"

    order_id = session.execute(
        text("""
            INSERT INTO billing."order"
                (club_id, user_id, amount_minor, currency_code, settlement_mode, status)
            VALUES (:c, :u, :amt, :cur, :mode, :st)
            RETURNING id
        """),
        {"c": str(club_id), "u": str(user_id) if user_id else None,
         "amt": amount, "cur": currency, "mode": mode, "st": order_status},
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

    # Subscription row linked by order_id + carrying the chosen plan's price_id. status 'expired'
    # is the placeholder pending-state (an unpaid intent must NOT count as active); activation flips
    # it to 'active'. Online → the webhook activates on paid; offline → we activate right below.
    session.execute(
        text("""
            INSERT INTO billing.membership_subscription
                (club_id, user_id, price_id, status, provider, order_id)
            VALUES (:c, :u, :pid, 'expired', :prov, :oid)
        """),
        {"c": str(club_id), "u": str(user_id) if user_id else None,
         "pid": price_id, "prov": provider, "oid": order_id},
    )

    out = {"order_id": order_id, "amount_minor": amount, "currency": currency,
           "price_id": price_id, "term_months": term_months, "label": label,
           "settlement_mode": mode, "needs_checkout": online, "activated": False}

    if not online:
        # Offline (at desk / monthly tab): activate the membership now — the member plays immediately;
        # the 'open' order is collected later. No Yoco round-trip.
        link = session.execute(
            text("SELECT id, club_id, user_id, price_id, status, current_period_end "
                 "FROM billing.membership_subscription WHERE order_id = :oid ORDER BY created_at LIMIT 1"),
            {"oid": order_id},
        ).mappings().first()
        if link:
            out["activation"] = _apply_term_grant(
                session, link=link, months=max(1, int(term_months or 1)), provider=provider)
            out["activated"] = True

    return out


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

    return _apply_term_grant(session, link=link, months=months, provider=provider)


def _apply_term_grant(session, *, link, months, provider) -> Dict[str, Any]:
    """Core period grant for a subscription `link` row — NO paid-gate (callers decide when it's due).
    Idempotent on an already-active row for this order; stacks onto an existing active membership if
    the member already holds one. Used by both the paid-webhook path and the offline immediate path."""
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
    # A real paid plan SUPERSEDES a free-week trial (below), never stacks onto it — so exclude trial subs.
    existing = session.execute(
        text("SELECT id, current_period_end FROM billing.membership_subscription "
             "WHERE club_id = :c AND user_id = :u AND status = 'active' "
             "  AND COALESCE(provider,'') <> 'trial' "
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

    # SUPERSEDE the signup free-week: a real paid plan REPLACES the trial (else the trial lingers active
    # and membership_status can mislabel the paid plan as "Free week").
    session.execute(
        text("UPDATE billing.membership_subscription "
             "SET status = 'cancelled', cancelled_at = COALESCE(cancelled_at, now()), updated_at = now() "
             "WHERE club_id = CAST(:c AS uuid) AND user_id = CAST(:u AS uuid) "
             "  AND provider = 'trial' AND status = 'active' AND id <> CAST(:self AS uuid)"),
        {"c": str(club_id), "u": str(user_id) if user_id else None, "self": str(link["id"])})

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
        text("SELECT ms.id AS sub_id, ms.current_period_end, ms.provider, "
             "       (ms.current_period_end - CURRENT_DATE) AS days_left, "
             "       p.access_days, p.access_start_min, p.access_end_min, "
             "       p.membership_tier, p.label, p.term_months "
             "FROM billing.membership_subscription ms "
             "LEFT JOIN billing.price p ON p.id = ms.price_id "
             "WHERE ms.club_id = :c AND ms.user_id = :u AND ms.status = 'active' "
             "  AND (ms.current_period_end IS NULL OR ms.current_period_end >= CURRENT_DATE) "
             "ORDER BY ms.current_period_end DESC NULLS FIRST LIMIT 1"),
        {"c": str(club_id), "u": str(user_id) if user_id else None},
    ).mappings().first()
    end = row["current_period_end"] if row else None
    is_trial = bool(row and row["provider"] == "trial")
    days_left = int(row["days_left"]) if (row and row["days_left"] is not None) else None
    # Access window of the active membership (Phase 5): None = unconstrained (covers any time).
    window = None
    if row and (row["access_days"] or row["access_start_min"] is not None
                or row["access_end_min"] is not None):
        window = {
            "days": [int(x) for x in row["access_days"].split(",") if x.strip()]
            if row["access_days"] else None,
            "start_min": int(row["access_start_min"]) if row["access_start_min"] is not None else None,
            "end_min": int(row["access_end_min"]) if row["access_end_min"] is not None else None,
        }
    # The active plan's display name: tier (Adult Off-Peak) → label → term length → generic.
    plan_name = None
    if row:
        plan_name = (row["membership_tier"] or row["label"]
                     or (_plan_label(None, row["term_months"]) if row["term_months"] else None))
    return {
        "active": row is not None,
        "subscription_id": str(row["sub_id"]) if row else None,
        "plan_name": ("Free week" if is_trial else (plan_name or "Membership")) if row else None,
        "current_period_end": (end.isoformat() if hasattr(end, "isoformat") else end)
        if end is not None else None,
        "is_trial": is_trial,                       # the signup free-week (provider='trial')
        "trial_days_left": days_left if is_trial else None,
        "membership_window": window,                # Phase 5 access window (None = any time)
        "membership_window_summary": (_window_summary(row["access_days"], row["access_start_min"],
                                                       row["access_end_min"]) if row else None),
        "price_minor": offer["amount_minor"] if offer else None,
        "currency": offer["currency"] if offer else None,
        "sold": bool(plans),
        "plans": plans,
    }


def cancel_membership(session, *, club_id, user_id) -> Dict[str, Any]:
    """Member self-cancel: end the caller's ACTIVE membership(s) now → courts revert to PAYG. Mirrors
    the admin cancel (status='cancelled'). Idempotent: no active sub → {cancelled: 0}. The paid term
    isn't refunded here (a refund is a separate request); cancelling just stops coverage.

    An UNPAID offline plan (order still open/awaiting_payment) is voided so it drops off the client's
    statement — a cancelled plan you never paid for is not a debt. A PAID plan's order is left intact
    (void_order refuses anything already paid); refunding a paid term is a separate refund request."""
    # Cancel an ACTIVE membership OR an OWED-but-inactive one (offline plan bought, never paid — the sub
    # sits in a non-'active' placeholder while its order is still open). Without the second branch an
    # unpaid offline membership was uncancellable and its owed order stuck forever.
    rows = session.execute(
        text("UPDATE billing.membership_subscription "
             "SET status = 'cancelled', cancelled_at = COALESCE(cancelled_at, now()), updated_at = now() "
             "WHERE club_id = :c AND user_id = :u AND status <> 'cancelled' "
             "  AND ( (status = 'active' AND (current_period_end IS NULL OR current_period_end >= CURRENT_DATE)) "
             "        OR EXISTS (SELECT 1 FROM billing.\"order\" o "
             "                    WHERE o.id = membership_subscription.order_id "
             "                      AND o.status IN ('open','awaiting_payment')) ) "
             "RETURNING order_id"),
        {"c": str(club_id), "u": str(user_id) if user_id else None},
    ).mappings().all()
    voided = _void_unpaid_orders(session, club_id, [r["order_id"] for r in rows])
    return {"cancelled": len(rows), "voided_orders": voided}


def _void_unpaid_orders(session, club_id, order_ids) -> int:
    """Void each still-unpaid order behind a cancelled/revoked membership (paid orders untouched).
    Reuses statement.void_order — the single sanctioned place to clear an unpaid order."""
    from billing.statement import void_order
    voided = 0
    for oid in order_ids:
        if not oid:
            continue                                    # trial subs carry no order
        res = void_order(session, club_id=club_id, order_id=oid, reason="membership cancelled")
        if res.get("ok"):
            voided += 1
    return voided


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

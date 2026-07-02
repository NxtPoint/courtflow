# billing/me.py — member-facing FINANCIAL READS for the client "My Account → Financials" tab.
#
# Lane note (client-self-service-spec §4.2): these read billing.* (+ a tiny diary count)
# DIRECTLY, but the SQL lives HERE in the billing lane (billing owns its tables); the me/
# route layer just composes the JSON. Every query is STRICTLY principal-scoped — club_id +
# user_id are passed in from the route's principal, NEVER from a request body.
#
# Every sub-query is GUARDED (try/except -> safe default) exactly like diary/pricing.py, so
# the Financials tab degrades gracefully if a table is mid-migration / not yet present in an
# isolated boot. A missing financials read must never 500 the My Account page.
#
# Public surface:
#   member_financials(session, *, club_id, user_id) -> dict  (the whole tab payload)
#   member_orders(session, *, club_id, user_id, limit=50)    -> [order dicts] (spend detail / receipts)
#
# Reuses billing.membership.membership_status for the plan block (single source of truth for
# active/period-end/price), so plan logic is not duplicated here.

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

log = logging.getLogger("billing.me")


def _iso(v) -> Optional[str]:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)


# ---------------------------------------------------------------------------
# plan (REUSE membership_status — single source of truth)
# ---------------------------------------------------------------------------

def member_plan(session, *, club_id, user_id) -> Dict[str, Any]:
    """Public, lightweight read of the caller's plan (for the free-week banner / covered label).
    Single source of truth: _plan -> membership_status."""
    return _plan(session, club_id=club_id, user_id=user_id)


def _plan(session, *, club_id, user_id) -> Dict[str, Any]:
    """Current plan: 'membership' when an active membership exists, else 'payg'. Renewal date
    + the club's headline membership offer (for upsell) come straight from membership_status."""
    try:
        from billing.membership import membership_status
        st = membership_status(session, club_id=club_id, user_id=user_id)
    except Exception:
        log.debug("plan: membership_status unavailable", exc_info=False)
        st = {"active": False, "current_period_end": None, "price_minor": None,
              "currency": None, "sold": False}
    active = bool(st.get("active"))
    is_trial = bool(st.get("is_trial"))
    return {
        "type": "membership" if active else "payg",
        "active": active,
        # The member's ACTUAL plan name (tier, e.g. "Adult Off-Peak"), not a generic label.
        "name": st.get("plan_name") if active else "Pay as you go",
        "subscription_id": st.get("subscription_id"),
        "current_period_end": st.get("current_period_end"),
        "price_minor": st.get("price_minor"),
        "sold": bool(st.get("sold")),
        "is_trial": is_trial,                       # signup free-week (courts free, time-boxed)
        "trial_days_left": st.get("trial_days_left"),
        "membership_window": st.get("membership_window"),  # Phase 5 (None = covers any time)
        "membership_window_summary": st.get("membership_window_summary"),  # e.g. "Courts free weekdays 06:00–16:00"
    }


# ---------------------------------------------------------------------------
# usage this month (diary bookings owned by the member, this calendar month)
# ---------------------------------------------------------------------------

def _usage_this_month(session, *, club_id, user_id) -> Dict[str, int]:
    """Count of the member's OWN confirmed/completed bookings in the current calendar month,
    grouped by booking_type. Keyed on booked_by_user_id (the account holder)."""
    out = {"court": 0, "lesson": 0, "class": 0, "total": 0}
    try:
        rows = session.execute(
            text("""
                SELECT booking_type, count(*) AS n
                FROM diary.booking
                WHERE club_id = :c AND booked_by_user_id = :u
                  AND status IN ('confirmed','completed')
                  AND starts_at >= date_trunc('month', now())
                  AND starts_at <  date_trunc('month', now()) + interval '1 month'
                GROUP BY booking_type
            """),
            {"c": str(club_id), "u": str(user_id)},
        ).mappings().all()
    except Exception:
        log.debug("usage_this_month suppressed (diary not ready)", exc_info=False)
        return out
    for r in rows:
        bt = r["booking_type"]
        n = int(r["n"] or 0)
        if bt in out:
            out[bt] = n
        out["total"] += n
    return out


# ---------------------------------------------------------------------------
# spend (paid orders this month + a short N-month history)
# ---------------------------------------------------------------------------

def _spend(session, *, club_id, user_id, months=6) -> Dict[str, Any]:
    """Sum of the member's PAID orders by month for the last N months, most-recent first.
    spend = settled money (status='paid' covers online + desk-settled; membership_covered/free
    orders sum to 0 but still count). this_month_minor is the current calendar month's total."""
    history: List[Dict[str, Any]] = []
    try:
        rows = session.execute(
            text("""
                SELECT to_char(date_trunc('month', o.created_at), 'YYYY-MM') AS period,
                       COALESCE(SUM(o.amount_minor), 0)                       AS paid_minor,
                       count(*)                                               AS orders
                FROM billing."order" o
                WHERE o.club_id = :c AND o.user_id = :u AND o.status = 'paid'
                  AND o.created_at >= date_trunc('month', now()) - make_interval(months => :m)
                GROUP BY 1
                ORDER BY 1 DESC
            """),
            {"c": str(club_id), "u": str(user_id), "m": int(months)},
        ).mappings().all()
    except Exception:
        log.debug("spend suppressed (billing.order not ready)", exc_info=False)
        rows = []
    cur_period = None
    try:
        cur_period = session.execute(text("SELECT to_char(now(), 'YYYY-MM')")).scalar()
    except Exception:
        cur_period = None
    this_month = 0
    for r in rows:
        period = r["period"]
        paid = int(r["paid_minor"] or 0)
        history.append({"period": period, "paid_minor": paid, "orders": int(r["orders"] or 0)})
        if period == cur_period:
            this_month = paid
    return {"this_month_minor": this_month, "history": history}


# ---------------------------------------------------------------------------
# account balance (running tab, if monthly_account used)
# ---------------------------------------------------------------------------

def _account(session, *, club_id, user_id) -> Dict[str, Any]:
    """What the member owes — derived from the UNIFIED STATEMENT (the single source of truth: unpaid
    orders), not a separate ledger tab (that parallel store was retired). balance_minor = total owed;
    open_charges = number of owed lines. Guarded → 0/absent."""
    try:
        from billing import statement as _statement
        st = _statement.statement(session, club_id=club_id, user_id=user_id)
        return {"balance_minor": int(st.get("total_owed_minor") or 0),
                "open_charges": int(st.get("count") or 0)}
    except Exception:
        log.debug("account balance suppressed", exc_info=False)
        return {"balance_minor": 0, "open_charges": 0}


# ---------------------------------------------------------------------------
# orders (spend detail / receipts) — powers the "recent payments" + refund buttons
# ---------------------------------------------------------------------------

def member_orders(session, *, club_id, user_id, limit=50) -> List[Dict[str, Any]]:
    """The member's recent PAID/REFUNDED orders (self-scoped), most-recent first. Each row
    carries the first order-line description and whether it already has an OPEN refund request
    (so the UI can disable the 'Request refund' button). Guarded -> []."""
    try:
        rows = session.execute(
            text("""
                SELECT o.id, o.created_at, o.amount_minor, o.currency_code,
                       o.status, o.settlement_mode,
                       (SELECT description FROM billing.order_line
                         WHERE order_id = o.id ORDER BY created_at LIMIT 1) AS description,
                       EXISTS (SELECT 1 FROM billing.refund_request rr
                               WHERE rr.order_id = o.id
                                 AND rr.status IN ('pending','approved')) AS has_open_refund,
                       (SELECT status FROM billing.refund_request rr2
                         WHERE rr2.order_id = o.id
                         ORDER BY created_at DESC LIMIT 1) AS refund_status
                FROM billing."order" o
                WHERE o.club_id = :c AND o.user_id = :u
                  AND o.status IN ('paid','refunded')
                ORDER BY o.created_at DESC
                LIMIT :lim
            """),
            {"c": str(club_id), "u": str(user_id), "lim": int(limit)},
        ).mappings().all()
    except Exception:
        log.debug("member_orders suppressed (billing not ready)", exc_info=False)
        return []
    out = []
    for r in rows:
        out.append({
            "id": str(r["id"]),
            "created_at": _iso(r["created_at"]),
            "amount_minor": int(r["amount_minor"] or 0),
            "currency_code": r["currency_code"],
            "status": r["status"],
            "settlement_mode": r["settlement_mode"],
            "description": r["description"],
            "has_open_refund": bool(r["has_open_refund"]),
            "refund_status": r["refund_status"],
            # a paid order with no open request is refundable from the client side (§6)
            "refundable": bool(r["status"] == "paid" and not r["has_open_refund"]),
        })
    return out


# ---------------------------------------------------------------------------
# the composed financials payload
# ---------------------------------------------------------------------------

def member_financials(session, *, club_id, user_id) -> Dict[str, Any]:
    """The whole Financials-tab payload (spec §4.1): plan + usage_this_month + spend +
    account + next_charge + currency. Each sub-block is independently guarded."""
    plan = _plan(session, club_id=club_id, user_id=user_id)
    usage = _usage_this_month(session, club_id=club_id, user_id=user_id)
    spend = _spend(session, club_id=club_id, user_id=user_id)
    account = _account(session, club_id=club_id, user_id=user_id)

    try:
        currency = session.execute(
            text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": str(club_id)},
        ).scalar() or "ZAR"
    except Exception:
        currency = "ZAR"

    # next_charge: if a membership is active with a known renewal date, that date + price.
    next_charge = {"kind": None, "amount_minor": None, "due_date": None}
    if plan.get("active") and plan.get("current_period_end"):
        next_charge = {
            "kind": "membership_renewal",
            "amount_minor": plan.get("price_minor"),
            "due_date": plan.get("current_period_end"),
        }

    return {
        "currency": currency,
        "plan": plan,
        "usage_this_month": usage,
        "spend": spend,
        "account": account,
        "next_charge": next_charge,
    }

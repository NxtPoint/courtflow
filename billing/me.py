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


def billing_summary(session, *, club_id, user_id, month=None) -> Dict[str, Any]:
    """The client's MONTHLY billing, grouped BY CATEGORY (Lessons / Court hire / Classes / Session
    packs / Membership) — each with a count, a total, and the individual items (drill-through). Built
    from the client's ORDERS (the same source of truth as the statement), so it always matches what
    they owe/paid: 'Lessons · 2 · R1500' → the 2 lessons → each lesson's full detail (booking story;
    membership/packs drill to their receipt). Month = the booking date, else the order date. Guarded."""
    ym = month or session.execute(text("SELECT to_char(now(),'YYYY-MM')")).scalar()
    currency = session.execute(
        text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": str(club_id)}).scalar() or "ZAR"
    try:
        rows = session.execute(
            text("""
                SELECT o.id AS order_id, o.amount_minor, o.status AS ostatus, o.settlement_mode,
                       o.created_at,
                       ol.booking_id,
                       b.starts_at AS booking_at, b.booking_type AS b_kind,
                       pr.kind AS p_kind,
                       r.name AS resource_name,
                       (SELECT cr.name FROM diary.booking cb JOIN diary.resource cr ON cr.id = cb.resource_id
                         WHERE cb.club_id = o.club_id AND cb.order_id = o.id
                           AND cb.booking_type = 'court' LIMIT 1) AS held_court,
                       COALESCE(cp.display_name,
                                NULLIF(TRIM(COALESCE(cu.first_name,'') || ' ' || COALESCE(cu.surname,'')),''))
                         AS coach_name,
                       EXISTS (SELECT 1 FROM billing.token_wallet w WHERE w.order_id = o.id) AS is_pack,
                       EXISTS (SELECT 1 FROM billing.membership_subscription ms WHERE ms.order_id = o.id) AS is_membership
                FROM billing."order" o
                LEFT JOIN LATERAL (SELECT id, booking_id, price_id FROM billing.order_line
                                    WHERE order_id = o.id ORDER BY created_at LIMIT 1) ol ON true
                LEFT JOIN diary.booking b ON b.id = ol.booking_id
                LEFT JOIN diary.resource r ON r.id = b.resource_id
                LEFT JOIN billing.price p ON p.id = ol.price_id
                LEFT JOIN billing.product pr ON pr.id = p.product_id
                LEFT JOIN iam."user" cu ON cu.id = b.coach_user_id
                LEFT JOIN iam.coach_profile cp ON cp.user_id = b.coach_user_id AND cp.club_id = o.club_id
                WHERE o.club_id = :c AND o.user_id = :u
                  AND o.status = 'open'          -- OWED only: the card sits under "YOU OWE" and must
                                                 -- reconcile to it (paid/refunded/written-off are not
                                                 -- owed — they live in the session records + history)
                  AND o.settled_by_order_id IS NULL
                  AND to_char(COALESCE(b.starts_at, o.created_at),'YYYY-MM') = :ym
                ORDER BY COALESCE(b.starts_at, o.created_at) DESC
            """),
            {"c": str(club_id), "u": str(user_id), "ym": ym},
        ).mappings().all()
    except Exception:
        log.debug("billing_summary suppressed (billing/diary not ready)", exc_info=False)
        rows = []

    LABEL = {"lesson": "Lessons", "court": "Court hire", "class": "Classes",
             "pack": "Session packs", "membership": "Membership", "other": "Other"}
    ORDER = ["lesson", "court", "class", "pack", "membership", "other"]
    _ST = {"paid": "paid", "open": "owed", "awaiting_payment": "pending", "refunded": "refunded",
           "void": "cancelled", "written_off": "written_off"}
    cats: Dict[str, Any] = {}
    total = 0
    for r in rows:
        # Category: pack / membership first (order-level), else the booking/product kind.
        if r["is_pack"]:
            k = "pack"
        elif r["is_membership"]:
            k = "membership"
        else:
            k = (r["b_kind"] or r["p_kind"] or "other")
            if k not in ("court", "lesson", "class"):
                k = "other"
        amt = int(r["amount_minor"] or 0)
        covered = r["settlement_mode"] in ("membership_covered", "free", "token")
        st = "covered" if (covered and amt == 0) else _ST.get(r["ostatus"], r["ostatus"] or "—")
        court = r["held_court"] if r["b_kind"] == "lesson" else r["resource_name"]
        c = cats.setdefault(k, {"key": k, "label": LABEL.get(k, k), "count": 0, "total_minor": 0, "items": []})
        c["count"] += 1                     # every line here is an OWED (open) order
        c["total_minor"] += amt
        total += amt
        c["items"].append({
            "order_id": str(r["order_id"]),
            "booking_id": str(r["booking_id"]) if r["booking_id"] else None,
            "starts_at": _iso(r["booking_at"] or r["created_at"]),
            "amount_minor": amt, "status": st, "coach_name": r["coach_name"], "court_name": court,
        })
    categories = [cats[k] for k in ORDER if k in cats]
    return {"month": ym, "currency": currency, "total_minor": total, "categories": categories}


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
    # An OWED-but-inactive membership (offline plan bought, not yet paid) — so the UI can show a
    # "Cancel membership" affordance even when the sub isn't 'active' (else it's uncancellable).
    owed_membership = False
    try:
        owed_membership = bool(session.execute(
            text('SELECT 1 FROM billing."order" o '
                 "WHERE o.club_id = :c AND o.user_id = :u AND o.status IN ('open','awaiting_payment') "
                 "  AND o.settled_by_order_id IS NULL "
                 "  AND EXISTS (SELECT 1 FROM billing.membership_subscription ms WHERE ms.order_id = o.id) "
                 "LIMIT 1"),
            {"c": str(club_id), "u": str(user_id)}).scalar())
    except Exception:
        pass
    return {
        "type": "membership" if active else "payg",
        "active": active,
        "owed_membership": owed_membership,
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
# monthly activity — spend BY CATEGORY for one calendar month
# ---------------------------------------------------------------------------

def activity_summary(session, *, club_id, user_id, month=None) -> Dict[str, Any]:
    """A client's MONTH at a glance — how many times they played (lessons / court / classes) and the
    money: billed (orders raised, ex-cancelled), paid (net of refunds), and still outstanding.
    Complements billing_summary (which is OWED-only): this counts EVERYTHING that happened, so a
    fully-settled month still tells its story ('you played 5, billed R2000, paid R2000, owe R0')
    instead of just looking empty. Month = booking date, else order/payment date. Guarded → zeros."""
    ym = month or session.execute(text("SELECT to_char(now(),'YYYY-MM')")).scalar()
    currency = session.execute(
        text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": str(club_id)}).scalar() or "ZAR"
    LBL = {"lesson": "Lessons", "court": "Court hire", "class": "Classes", "other": "Other"}
    out = {"month": ym, "currency": currency,
           "counts": {"lesson": 0, "court": 0, "class": 0, "total": 0},
           "minutes": 0, "billed_minor": 0, "paid_minor": 0, "outstanding_minor": 0,
           "by_service": [], "by_week": []}
    try:
        # Sessions PLAYED this month (confirmed/completed): counts + total minutes + weekly buckets by
        # type (the stacked chart). A lesson is ONE session: its auto-held court row (booking_type
        # ='court' WITH the coach set) is excluded so it isn't double-counted — a STANDALONE court has
        # no coach (coach_user_id IS NULL). Week = which week of the month the session falls in.
        weeks = {}   # wk(int) -> {"lesson":n,"court":n,"class":n}

        def _wk(w):
            return weeks.setdefault(int(w or 1), {"lesson": 0, "court": 0, "class": 0})
        for r in session.execute(
            text("SELECT booking_type AS k, "
                 "  ((EXTRACT(DAY FROM starts_at)::int - 1) / 7 + 1) AS wk, "
                 "  COALESCE(EXTRACT(EPOCH FROM (ends_at - starts_at)) / 60, 0) AS mins "
                 "FROM diary.booking WHERE club_id = :c AND booked_by_user_id = :u "
                 "  AND status IN ('confirmed','completed') "
                 "  AND ((booking_type = 'lesson') OR (booking_type = 'court' AND coach_user_id IS NULL)) "
                 "  AND to_char(starts_at,'YYYY-MM') = :ym"),
            {"c": str(club_id), "u": str(user_id), "ym": ym},
        ).mappings().all():
            k = r["k"]
            out["counts"][k] = out["counts"].get(k, 0) + 1
            out["minutes"] += int(r["mins"] or 0)
            _wk(r["wk"])[k] += 1
        for r in session.execute(
            text("SELECT ((EXTRACT(DAY FROM cs.starts_at)::int - 1) / 7 + 1) AS wk, "
                 "  COALESCE(EXTRACT(EPOCH FROM (cs.ends_at - cs.starts_at)) / 60, 0) AS mins "
                 "FROM diary.enrolment e JOIN diary.class_session cs ON cs.id = e.class_session_id "
                 "WHERE e.club_id = :c AND e.user_id = :u AND e.status = 'enrolled' "
                 "  AND to_char(cs.starts_at,'YYYY-MM') = :ym"),
            {"c": str(club_id), "u": str(user_id), "ym": ym},
        ).mappings().all():
            out["counts"]["class"] += 1
            out["minutes"] += int(r["mins"] or 0)
            _wk(r["wk"])["class"] += 1
        out["counts"]["total"] = out["counts"]["lesson"] + out["counts"]["court"] + out["counts"]["class"]
        out["by_week"] = [dict(week=w, **weeks[w]) for w in sorted(weeks)]
        # Spend BY SERVICE (billed, ex-cancelled) — one row per order, categorised by its booking/product.
        svc = {}
        for r in session.execute(
            text("SELECT COALESCE(b.booking_type, "
                 "  CASE WHEN pr.kind = 'court_booking' THEN 'court' ELSE pr.kind END) AS k, "
                 "  o.amount_minor AS amt "
                 'FROM billing."order" o '
                 "LEFT JOIN LATERAL (SELECT booking_id, price_id FROM billing.order_line "
                 "                   WHERE order_id = o.id ORDER BY created_at LIMIT 1) ol ON true "
                 "LEFT JOIN diary.booking b ON b.id = ol.booking_id "
                 "LEFT JOIN billing.price p ON p.id = ol.price_id "
                 "LEFT JOIN billing.product pr ON pr.id = p.product_id "
                 "WHERE o.club_id = :c AND o.user_id = :u AND o.settled_by_order_id IS NULL "
                 "  AND o.status IN ('open','paid','refunded','written_off') "
                 "  AND to_char(COALESCE(b.starts_at, o.created_at),'YYYY-MM') = :ym"),
            {"c": str(club_id), "u": str(user_id), "ym": ym},
        ).mappings().all():
            k = r["k"] if r["k"] in ("lesson", "court", "class") else "other"
            e = svc.setdefault(k, {"count": 0, "billed_minor": 0})
            e["count"] += 1
            e["billed_minor"] += int(r["amt"] or 0)
        out["by_service"] = [{"key": k, "label": LBL[k], "count": svc[k]["count"],
                              "billed_minor": svc[k]["billed_minor"]}
                             for k in ("lesson", "court", "class", "other") if k in svc]
        # Billed (orders raised this month, excluding cancelled/void) + still outstanding (open).
        m = session.execute(
            text("SELECT "
                 "COALESCE(SUM(CASE WHEN o.status IN ('open','paid','refunded','written_off') "
                 "               THEN o.amount_minor END),0) AS billed, "
                 "COALESCE(SUM(CASE WHEN o.status = 'open' THEN o.amount_minor END),0) AS outstanding "
                 'FROM billing."order" o '
                 "LEFT JOIN LATERAL (SELECT booking_id FROM billing.order_line "
                 "                   WHERE order_id = o.id ORDER BY created_at LIMIT 1) ol ON true "
                 "LEFT JOIN diary.booking b ON b.id = ol.booking_id "
                 "WHERE o.club_id = :c AND o.user_id = :u AND o.settled_by_order_id IS NULL "
                 "  AND to_char(COALESCE(b.starts_at, o.created_at),'YYYY-MM') = :ym"),
            {"c": str(club_id), "u": str(user_id), "ym": ym},
        ).mappings().first()
        out["billed_minor"] = int(m["billed"] or 0)
        out["outstanding_minor"] = int(m["outstanding"] or 0)
        # Paid = net money movement (charges − refunds) recorded this month, over THIS client's orders.
        out["paid_minor"] = int(session.execute(
            text("SELECT COALESCE(SUM(CASE "
                 "  WHEN direction='charge' AND status='succeeded' THEN amount_minor "
                 "  WHEN direction='refund' AND status IN ('refunded','succeeded') THEN -amount_minor "
                 "  END),0) FROM billing.payment "
                 'WHERE club_id = :c AND to_char(created_at,\'YYYY-MM\') = :ym AND order_id IN '
                 '  (SELECT id FROM billing."order" WHERE club_id = :c AND user_id = :u)'),
            {"c": str(club_id), "u": str(user_id), "ym": ym},
        ).scalar() or 0)
    except Exception:
        log.debug("activity_summary suppressed (billing/diary not ready)", exc_info=False)
    return out


def _month_bounds(month=None):
    """(start, next_start) ISO date strings for a 'YYYY-MM' month (default the current month)."""
    import re
    from datetime import date
    if month and re.match(r"^\d{4}-\d{2}$", month):
        y, mo = int(month[:4]), int(month[5:7])
    else:
        t = date.today(); y, mo = t.year, t.month
    start = date(y, mo, 1)
    ny, nmo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return start.isoformat(), date(ny, nmo, 1).isoformat()


def spend_by_category(session, *, club_id, user_id, month=None) -> Dict[str, Any]:
    """PAID spend in one calendar month, grouped by category — 'how much did I spend on what'. category
    from the order's first line (pack/membership flags, else booking_type/product kind). Guarded → 0/[]."""
    start, nxt = _month_bounds(month)
    try:
        rows = session.execute(
            text("""
                SELECT o.amount_minor,
                       (SELECT COALESCE(b.booking_type, pr.kind)
                          FROM billing.order_line ol
                          LEFT JOIN diary.booking b  ON b.id = ol.booking_id
                          LEFT JOIN billing.price   p  ON p.id = ol.price_id
                          LEFT JOIN billing.product pr ON pr.id = p.product_id
                         WHERE ol.order_id = o.id ORDER BY ol.created_at LIMIT 1) AS kind,
                       EXISTS (SELECT 1 FROM billing.token_wallet w WHERE w.order_id = o.id) AS is_pack,
                       EXISTS (SELECT 1 FROM billing.membership_subscription ms WHERE ms.order_id = o.id) AS is_membership
                FROM billing."order" o
                WHERE o.club_id = :c AND o.user_id = :u AND o.status = 'paid'
                  AND o.created_at >= CAST(:s AS date) AND o.created_at < CAST(:n AS date)
            """),
            {"c": str(club_id), "u": str(user_id), "s": start, "n": nxt},
        ).mappings().all()
    except Exception:
        log.debug("spend_by_category suppressed", exc_info=False)
        rows = []
    LABEL = {"court": "Court hire", "court_booking": "Court hire", "lesson": "Lessons",
             "class": "Classes", "membership": "Membership"}
    buckets: Dict[str, Dict[str, Any]] = {}
    total = 0
    for r in rows:
        amt = int(r["amount_minor"] or 0)
        if r["is_pack"]:
            cat = "Session packs"
        elif r["is_membership"]:
            cat = "Membership"
        else:
            cat = LABEL.get((r["kind"] or "").lower(), "Other")
        b = buckets.setdefault(cat, {"category": cat, "paid_minor": 0, "count": 0})
        b["paid_minor"] += amt
        b["count"] += 1
        total += amt
    by_cat = sorted(buckets.values(), key=lambda x: -x["paid_minor"])
    currency = session.execute(
        text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": str(club_id)}).scalar() or "ZAR"
    return {"month": start[:7], "total_paid_minor": total, "by_category": by_cat, "currency": currency}


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

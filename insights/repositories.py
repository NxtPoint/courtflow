# insights/repositories.py — Phase 2 P1 insight read-layer (docs/specs/ADMIN-PHASE2.md).
#
# Guarded, club_id-scoped, read-only aggregations over the EXISTING diary/billing/core data — NO
# new tables. Mirrors the analytics/repositories.py discipline: every read is wrapped so a missing
# table / empty data degrades to an empty payload, never a 500. This is the ONE place a given metric
# is computed, so a dashboard tile, an alert threshold and a benchmark ribbon can never disagree.
#
# First metric: court_utilisation (the "find dead slots" heatmap — a Phase-2 Must-have and the top
# benchmark gap vs world-class club software). More metrics slot in beside it as guarded functions.

from sqlalchemy import text


def _guard(fn, default):
    try:
        return fn()
    except Exception:
        return default


def _club_currency(session, club_id):
    try:
        return session.execute(
            text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": str(club_id)}
        ).scalar() or "ZAR"
    except Exception:
        return "ZAR"


def sales_by_day(session, *, club_id, month=None):
    """Sales (successful CHARGE payments) for ONE month, grouped by day — the owner's daily takings.
    Each sale carries the client, the service type (court / lesson / class / membership / pack) and
    the amount, and a link target for the standard transaction-detail widget: a booking-backed sale
    -> the event story (booking_id); everything else -> its receipt (order_id). `month` = 'YYYY-MM'
    (default = current month). Guarded -> empty. club_id-scoped."""
    b = session.execute(
        text("""
            SELECT to_char(COALESCE(to_date(:m, 'YYYY-MM'), date_trunc('month', now())), 'YYYY-MM') AS ym,
                   date_trunc('month', COALESCE(to_date(:m, 'YYYY-MM'), now()))::timestamptz AS start_d,
                   (date_trunc('month', COALESCE(to_date(:m, 'YYYY-MM'), now()))
                    + interval '1 month')::timestamptz AS end_d
        """),
        {"m": month},
    ).mappings().first()
    ym, start_d, end_d = b["ym"], b["start_d"], b["end_d"]

    def _rows():
        return session.execute(
            text("""
                SELECT p.id AS payment_id, p.order_id, p.amount_minor, p.currency_code, p.created_at,
                       COALESCE(NULLIF(TRIM(COALESCE(u.first_name,'') || ' ' || COALESCE(u.surname,'')), ''),
                                u.email, 'Walk-in') AS client_name,
                       (SELECT COALESCE(bk.booking_type, pr.kind, 'other')
                          FROM billing.order_line ol
                          LEFT JOIN diary.booking bk ON bk.id = ol.booking_id
                          LEFT JOIN billing.price prc ON prc.id = ol.price_id
                          LEFT JOIN billing.product pr ON pr.id = prc.product_id
                         WHERE ol.order_id = p.order_id ORDER BY ol.created_at LIMIT 1) AS service_type,
                       (SELECT ol.booking_id FROM billing.order_line ol
                         WHERE ol.order_id = p.order_id AND ol.booking_id IS NOT NULL LIMIT 1) AS booking_id,
                       (SELECT ol.description FROM billing.order_line ol
                         WHERE ol.order_id = p.order_id ORDER BY ol.created_at LIMIT 1) AS description
                FROM billing.payment p
                JOIN billing."order" o ON o.id = p.order_id
                LEFT JOIN iam."user" u ON u.id = o.user_id
                WHERE p.club_id = :c AND p.direction = 'charge' AND p.status = 'succeeded'
                  AND p.created_at >= :s AND p.created_at < :e
                ORDER BY p.created_at DESC
            """),
            {"c": str(club_id), "s": start_d, "e": end_d},
        ).mappings().all()

    rows = _guard(_rows, [])
    days = {}
    total = 0
    currency = None
    for r in rows:
        currency = currency or r["currency_code"]
        amt = int(r["amount_minor"] or 0)
        total += amt
        dkey = r["created_at"].date().isoformat()
        d = days.setdefault(dkey, {"date": dkey, "total_minor": 0, "sales": []})
        d["total_minor"] += amt
        d["sales"].append({
            "payment_id": str(r["payment_id"]),
            "order_id": str(r["order_id"]) if r["order_id"] else None,
            "booking_id": str(r["booking_id"]) if r["booking_id"] else None,
            "client_name": r["client_name"],
            "service_type": r["service_type"],
            "description": r["description"],
            "amount_minor": amt,
            "at": r["created_at"].isoformat(),
        })
    day_list = sorted(days.values(), key=lambda x: x["date"], reverse=True)
    return {
        "month": ym,
        "currency": currency or _club_currency(session, club_id),
        "total_minor": total,
        "count": len(rows),
        "days": day_list,
    }


def bookings_by_day(session, *, club_id, month=None):
    """Bookings MADE (not money received) for ONE month, grouped by the day they're played
    (starts_at) — the owner's daily diary at a glance. The sibling of sales_by_day, but over
    diary.booking rather than billing.payment, so it also carries the COACH (which a payment
    doesn't) and surfaces bookings that carry no charge (membership-covered / R0). Each row
    links to the standard event-story widget via booking_id (-> #/event/<id> = admin
    booking_story). `month` = 'YYYY-MM' (default = current month). Guarded -> empty. club_id-scoped.

    Counts real bookings only: status IN ('confirmed','completed','no_show') — 'held'/'requested'/
    'proposed'/'cancelled' hold no confirmed slot and are excluded (keeps this reconciling with the
    Overview 'bookings per day' series, which reads the same function)."""
    b = session.execute(
        text("""
            SELECT to_char(COALESCE(to_date(:m, 'YYYY-MM'), date_trunc('month', now())), 'YYYY-MM') AS ym,
                   date_trunc('month', COALESCE(to_date(:m, 'YYYY-MM'), now()))::timestamptz AS start_d,
                   (date_trunc('month', COALESCE(to_date(:m, 'YYYY-MM'), now()))
                    + interval '1 month')::timestamptz AS end_d
        """),
        {"m": month},
    ).mappings().first()
    ym, start_d, end_d = b["ym"], b["start_d"], b["end_d"]

    def _rows():
        return session.execute(
            text("""
                SELECT bk.id AS booking_id, bk.booking_type, bk.status, bk.starts_at, bk.ends_at,
                       COALESCE(NULLIF(TRIM(COALESCE(cl.first_name,'') || ' ' || COALESCE(cl.surname,'')), ''),
                                cl.email, 'Walk-in') AS client_name,
                       NULLIF(TRIM(COALESCE(co.first_name,'') || ' ' || COALESCE(co.surname,'')), '') AS coach_name,
                       res.name AS court_name,
                       (SELECT ol.description FROM billing.order_line ol
                         WHERE ol.order_id = bk.order_id ORDER BY ol.created_at LIMIT 1) AS description
                FROM diary.booking bk
                LEFT JOIN iam."user" cl ON cl.id = bk.booked_by_user_id
                LEFT JOIN iam."user" co ON co.id = bk.coach_user_id
                LEFT JOIN diary.resource res ON res.id = bk.resource_id
                WHERE bk.club_id = :c
                  AND bk.status IN ('confirmed', 'completed', 'no_show')
                  AND bk.starts_at >= :s AND bk.starts_at < :e
                ORDER BY bk.starts_at DESC
            """),
            {"c": str(club_id), "s": start_d, "e": end_d},
        ).mappings().all()

    rows = _guard(_rows, [])
    days = {}
    counts = {"court": 0, "lesson": 0, "class": 0}
    for r in rows:
        bt = r["booking_type"] or "court"
        if bt in counts:
            counts[bt] += 1
        dkey = r["starts_at"].date().isoformat()
        d = days.setdefault(dkey, {"date": dkey, "count": 0, "bookings": []})
        d["count"] += 1
        d["bookings"].append({
            "booking_id": str(r["booking_id"]),
            "booking_type": bt,
            "status": r["status"],
            "client_name": r["client_name"],
            "coach_name": r["coach_name"],
            "court_name": r["court_name"],
            "description": r["description"],
            "starts_at": r["starts_at"].isoformat(),
        })
    day_list = sorted(days.values(), key=lambda x: x["date"], reverse=True)
    return {
        "month": ym,
        "count": len(rows),
        "by_type": counts,
        "days": day_list,
    }


def court_utilisation(session, *, club_id, days=30):
    """Court occupancy over the trailing `days`, bucketed by weekday (0=Mon..6=Sun) x hour-of-day:
    booked court-hours vs available (open) court-hours, plus an overall utilisation %. Reads
    diary.booking (court) + diary.availability_rule (open hours) — nothing new. Guarded per
    sub-query so a partial DB yields an empty grid rather than erroring. Bucketing is by the
    booking's START hour (a 90-min booking counts in its start bucket) — fine for a heatmap.

    Returns {days, overall_pct, booked_hours, available_hours,
             cells:[{weekday,hour,booked_hours,available_hours,pct}]}."""
    days = max(1, min(int(days or 30), 365))

    # Booked court-hours per (weekday, hour) over the window.
    def _booked():
        rows = session.execute(
            text("""
                SELECT (EXTRACT(ISODOW FROM b.starts_at)::int - 1) AS weekday,
                       EXTRACT(HOUR FROM b.starts_at)::int AS hour,
                       COALESCE(SUM(EXTRACT(EPOCH FROM (b.ends_at - b.starts_at)) / 3600.0), 0) AS booked
                FROM diary.booking b
                JOIN diary.resource r ON r.id = b.resource_id AND r.kind = 'court'
                WHERE b.club_id = :c AND b.booking_type = 'court'
                  AND b.status IN ('confirmed', 'held', 'completed', 'no_show')
                  AND b.starts_at >= now() - make_interval(days => :d) AND b.starts_at < now()
                GROUP BY 1, 2
            """),
            {"c": str(club_id), "d": days},
        ).mappings().all()
        return {(int(r["weekday"]), int(r["hour"])): float(r["booked"] or 0) for r in rows}

    # Open court-hours per (weekday, hour) for ONE occurrence of that weekday (court rules expanded
    # into hour buckets). count(*) = number of courts open in that bucket.
    def _avail_per_occ():
        rows = session.execute(
            text("""
                SELECT ar.weekday AS weekday, gs.hour AS hour, COUNT(*) AS courts
                FROM diary.availability_rule ar
                JOIN diary.resource r ON r.id = ar.resource_id AND r.kind = 'court'
                CROSS JOIN LATERAL generate_series(EXTRACT(HOUR FROM ar.start_time)::int,
                                                   EXTRACT(HOUR FROM ar.end_time)::int - 1) AS gs(hour)
                WHERE ar.club_id = :c
                GROUP BY 1, 2
            """),
            {"c": str(club_id)},
        ).mappings().all()
        return {(int(r["weekday"]), int(r["hour"])): int(r["courts"]) for r in rows}

    # How many times each weekday occurs in the window (so per-occurrence availability scales to it).
    def _occ():
        rows = session.execute(
            text("""
                SELECT (EXTRACT(ISODOW FROM d)::int - 1) AS weekday, COUNT(*) AS occ
                FROM generate_series((now() - make_interval(days => :d))::date,
                                     (now())::date - 1, interval '1 day') d
                GROUP BY 1
            """),
            {"d": days},
        ).mappings().all()
        return {int(r["weekday"]): int(r["occ"]) for r in rows}

    booked = _guard(_booked, {})
    avail_per_occ = _guard(_avail_per_occ, {})
    occ = _guard(_occ, {})

    cells = []
    total_booked = 0.0
    total_avail = 0.0
    for key in set(booked.keys()) | set(avail_per_occ.keys()):
        wd, hr = key
        b = booked.get(key, 0.0)
        a = avail_per_occ.get(key, 0) * occ.get(wd, 0)
        total_booked += b
        total_avail += a
        pct = round(min(100.0, b / a * 100.0), 1) if a > 0 else None
        cells.append({"weekday": wd, "hour": hr, "booked_hours": round(b, 2),
                      "available_hours": round(a, 2), "pct": pct})
    cells.sort(key=lambda x: (x["weekday"], x["hour"]))
    overall = round(min(100.0, total_booked / total_avail * 100.0), 1) if total_avail > 0 else None
    return {
        "days": days,
        "overall_pct": overall,
        "booked_hours": round(total_booked, 1),
        "available_hours": round(total_avail, 1),
        "cells": cells,
    }

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

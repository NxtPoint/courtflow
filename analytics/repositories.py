# analytics/repositories.py — guarded aggregation queries for the Business Overview.
#
# Every function is read-only and defensive: if a table/column isn't there yet or a query fails,
# it returns a safe empty default and logs, so one missing panel never breaks the dashboard.
# club_id is optional: None = platform-wide (all clubs); a uuid = that club only.

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

log = logging.getLogger("analytics.repositories")

# core.usage_event stores the page-view beacon payload in its JSONB column named "metadata".
_PV = "event_type = 'page_view'"
_PL = "event_type = 'page_leave'"   # carries duration_ms for time-on-site


def _window(days: int) -> str:
    return f"occurred_at >= now() - interval '{int(days)} days'"


def _guard(fn, default):
    try:
        return fn()
    except Exception as e:
        log.info("analytics query skipped (%s)", e.__class__.__name__)
        return default


def _club_clause(col: str, club_id: Optional[str]) -> str:
    return "" if not club_id else f" AND {col} = :club"


def _p(club_id: Optional[str], **extra) -> Dict[str, Any]:
    d: Dict[str, Any] = dict(extra)
    if club_id:
        d["club"] = str(club_id)
    return d


# ---------------------------------------------------------------------------
# Website traffic (core.usage_event, event_type='page_view')
# ---------------------------------------------------------------------------

def traffic_summary(session, *, club_id, days) -> Dict[str, Any]:
    def q():
        row = session.execute(text(f"""
            SELECT count(*) AS visits,
                   count(DISTINCT metadata->>'anon_id')
                     FILTER (WHERE metadata->>'anon_id' IS NOT NULL) AS unique_visitors
            FROM core.usage_event
            WHERE {_PV} AND {_window(days)}{_club_clause('club_id', club_id)}
        """), _p(club_id)).mappings().first()
        return {"visits": int(row["visits"] or 0),
                "unique_visitors": int(row["unique_visitors"] or 0)}
    return _guard(q, {"visits": 0, "unique_visitors": 0})


def new_vs_returning(session, *, club_id, days) -> Dict[str, Any]:
    """Among visitors active in the window: new = first-ever page view in the window;
    returning = first seen before the window."""
    def q():
        row = session.execute(text(f"""
            WITH active AS (
                SELECT DISTINCT metadata->>'anon_id' AS aid
                FROM core.usage_event
                WHERE {_PV} AND {_window(days)} AND metadata->>'anon_id' IS NOT NULL
                  {_club_clause('club_id', club_id)}
            ),
            firsts AS (
                SELECT metadata->>'anon_id' AS aid, min(occurred_at) AS first_seen
                FROM core.usage_event
                WHERE {_PV} AND metadata->>'anon_id' IS NOT NULL
                  {_club_clause('club_id', club_id)}
                GROUP BY 1
            )
            SELECT
              count(*) FILTER (WHERE f.first_seen >= now() - interval '{int(days)} days') AS new_visitors,
              count(*) FILTER (WHERE f.first_seen <  now() - interval '{int(days)} days') AS returning_visitors
            FROM active a JOIN firsts f ON f.aid = a.aid
        """), _p(club_id)).mappings().first()
        return {"new_visitors": int(row["new_visitors"] or 0),
                "returning_visitors": int(row["returning_visitors"] or 0)}
    return _guard(q, {"new_visitors": 0, "returning_visitors": 0})


def visits_daily(session, *, club_id, days) -> List[Dict[str, Any]]:
    def q():
        rows = session.execute(text(f"""
            SELECT date_trunc('day', occurred_at)::date AS day,
                   count(*) AS visits,
                   count(DISTINCT metadata->>'anon_id') AS unique_visitors
            FROM core.usage_event
            WHERE {_PV} AND {_window(days)}{_club_clause('club_id', club_id)}
            GROUP BY 1 ORDER BY 1
        """), _p(club_id)).mappings().all()
        return [{"day": str(r["day"]), "visits": int(r["visits"] or 0),
                 "unique_visitors": int(r["unique_visitors"] or 0)} for r in rows]
    return _guard(q, [])


def traffic_sources(session, *, club_id, days) -> List[Dict[str, Any]]:
    """utm_source if present, else the referrer host, else 'direct'."""
    def q():
        rows = session.execute(text(f"""
            SELECT CASE
                     WHEN COALESCE(metadata->>'utm_source','') <> '' THEN metadata->>'utm_source'
                     WHEN COALESCE(metadata->>'referrer','') <> ''
                       THEN split_part(split_part(metadata->>'referrer','//',2),'/',1)
                     ELSE 'direct' END AS source,
                   count(*) AS visits
            FROM core.usage_event
            WHERE {_PV} AND {_window(days)}{_club_clause('club_id', club_id)}
            GROUP BY 1 ORDER BY 2 DESC LIMIT 10
        """), _p(club_id)).mappings().all()
        return [{"source": r["source"] or "direct", "visits": int(r["visits"] or 0)} for r in rows]
    return _guard(q, [])


def top_pages(session, *, club_id, days) -> List[Dict[str, Any]]:
    def q():
        rows = session.execute(text(f"""
            SELECT metadata->>'path' AS path, count(*) AS visits
            FROM core.usage_event
            WHERE {_PV} AND {_window(days)} AND metadata->>'path' IS NOT NULL
              {_club_clause('club_id', club_id)}
            GROUP BY 1 ORDER BY 2 DESC LIMIT 10
        """), _p(club_id)).mappings().all()
        return [{"path": r["path"], "visits": int(r["visits"] or 0)} for r in rows]
    return _guard(q, [])


def by_country(session, *, club_id, days) -> List[Dict[str, Any]]:
    def q():
        rows = session.execute(text(f"""
            SELECT COALESCE(NULLIF(metadata->>'country',''), 'unknown') AS country,
                   count(*) AS visits
            FROM core.usage_event
            WHERE {_PV} AND {_window(days)}{_club_clause('club_id', club_id)}
            GROUP BY 1 ORDER BY 2 DESC LIMIT 15
        """), _p(club_id)).mappings().all()
        return [{"country": r["country"], "visits": int(r["visits"] or 0)} for r in rows]
    return _guard(q, [])


def by_device(session, *, club_id, days) -> List[Dict[str, Any]]:
    def q():
        rows = session.execute(text(f"""
            SELECT COALESCE(NULLIF(metadata->>'device',''), 'unknown') AS device,
                   count(*) AS visits
            FROM core.usage_event
            WHERE {_PV} AND {_window(days)}{_club_clause('club_id', club_id)}
            GROUP BY 1 ORDER BY 2 DESC
        """), _p(club_id)).mappings().all()
        return [{"device": r["device"], "visits": int(r["visits"] or 0)} for r in rows]
    return _guard(q, [])


def by_browser(session, *, club_id, days) -> List[Dict[str, Any]]:
    def q():
        rows = session.execute(text(f"""
            SELECT COALESCE(NULLIF(metadata->>'browser',''), 'unknown') AS browser,
                   count(*) AS visits
            FROM core.usage_event
            WHERE {_PV} AND {_window(days)}{_club_clause('club_id', club_id)}
            GROUP BY 1 ORDER BY 2 DESC LIMIT 10
        """), _p(club_id)).mappings().all()
        return [{"browser": r["browser"], "visits": int(r["visits"] or 0)} for r in rows]
    return _guard(q, [])


def time_on_site(session, *, club_id, days) -> Dict[str, Any]:
    """Average + median seconds per page from the page_leave duration events."""
    def q():
        row = session.execute(text(f"""
            SELECT round(avg((metadata->>'duration_ms')::numeric) / 1000.0, 1) AS avg_seconds,
                   round((percentile_cont(0.5) WITHIN GROUP (
                         ORDER BY (metadata->>'duration_ms')::numeric) / 1000.0)::numeric, 1) AS median_seconds,
                   count(*) AS samples
            FROM core.usage_event
            WHERE {_PL} AND {_window(days)}
              AND (metadata->>'duration_ms') ~ '^[0-9]+$'{_club_clause('club_id', club_id)}
        """), _p(club_id)).mappings().first()
        return {"avg_seconds": float(row["avg_seconds"] or 0),
                "median_seconds": float(row["median_seconds"] or 0),
                "samples": int(row["samples"] or 0)}
    return _guard(q, {"avg_seconds": 0, "median_seconds": 0, "samples": 0})


# ---------------------------------------------------------------------------
# Customers + signups (core.account)
# ---------------------------------------------------------------------------

def customers(session, *, club_id, days) -> Dict[str, Any]:
    def q():
        row = session.execute(text(f"""
            SELECT count(*) FILTER (WHERE deleted_at IS NULL) AS total,
                   count(*) FILTER (WHERE deleted_at IS NULL
                     AND created_at >= now() - interval '{int(days)} days') AS new_in_window
            FROM core.account
            WHERE true{_club_clause('club_id', club_id)}
        """), _p(club_id)).mappings().first()
        return {"total": int(row["total"] or 0), "new_in_window": int(row["new_in_window"] or 0)}
    return _guard(q, {"total": 0, "new_in_window": 0})


def signups_daily(session, *, club_id, days) -> List[Dict[str, Any]]:
    def q():
        rows = session.execute(text(f"""
            SELECT date_trunc('day', created_at)::date AS day, count(*) AS signups
            FROM core.account
            WHERE deleted_at IS NULL AND created_at >= now() - interval '{int(days)} days'
              {_club_clause('club_id', club_id)}
            GROUP BY 1 ORDER BY 1
        """), _p(club_id)).mappings().all()
        return [{"day": str(r["day"]), "signups": int(r["signups"] or 0)} for r in rows]
    return _guard(q, [])


# ---------------------------------------------------------------------------
# Bookings + revenue (diary.* / billing.*)
# ---------------------------------------------------------------------------

def bookings_and_revenue(session, *, club_id, days) -> Dict[str, Any]:
    bookings = _guard(lambda: int(session.execute(text(f"""
        SELECT count(*) FROM diary.booking
        WHERE status IN ('confirmed','completed')
          AND created_at >= now() - interval '{int(days)} days'{_club_clause('club_id', club_id)}
    """), _p(club_id)).scalar() or 0), 0)

    rev = _guard(lambda: session.execute(text(f"""
        SELECT
          COALESCE(sum(amount_minor) FILTER (WHERE direction='charge' AND status='succeeded'),0) AS gross,
          COALESCE(sum(amount_minor) FILTER (WHERE direction='refund'),0) AS refunded
        FROM billing.payment
        WHERE created_at >= now() - interval '{int(days)} days'{_club_clause('club_id', club_id)}
    """), _p(club_id)).mappings().first(), {"gross": 0, "refunded": 0})

    return {"bookings": bookings,
            "revenue_minor": int((rev or {}).get("gross") or 0),
            "refunded_minor": int((rev or {}).get("refunded") or 0),
            "net_minor": int((rev or {}).get("gross") or 0) - int((rev or {}).get("refunded") or 0)}


def settlement_mix(session, *, club_id, days) -> List[Dict[str, Any]]:
    def q():
        rows = session.execute(text(f"""
            SELECT settlement_mode AS mode, count(*) AS count,
                   COALESCE(sum(amount_minor),0) AS amount_minor
            FROM billing."order"
            WHERE created_at >= now() - interval '{int(days)} days'
              AND status IN ('paid','open','awaiting_payment'){_club_clause('club_id', club_id)}
            GROUP BY 1 ORDER BY 2 DESC
        """), _p(club_id)).mappings().all()
        return [{"mode": r["mode"], "count": int(r["count"] or 0),
                 "amount_minor": int(r["amount_minor"] or 0)} for r in rows]
    return _guard(q, [])


# ---------------------------------------------------------------------------
# NPS (core.nps_response)
# ---------------------------------------------------------------------------

def nps(session, *, club_id, days) -> Dict[str, Any]:
    def q():
        row = session.execute(text(f"""
            SELECT count(*) FILTER (WHERE score >= 9) AS promoters,
                   count(*) FILTER (WHERE score BETWEEN 7 AND 8) AS passives,
                   count(*) FILTER (WHERE score <= 6) AS detractors,
                   count(*) AS total
            FROM core.nps_response
            WHERE submitted_at >= now() - interval '{int(days)} days'{_club_clause('club_id', club_id)}
        """), _p(club_id)).mappings().first()
        total = int(row["total"] or 0)
        promoters = int(row["promoters"] or 0)
        detractors = int(row["detractors"] or 0)
        score = round(((promoters - detractors) / total) * 100) if total else None
        return {"promoters": promoters, "passives": int(row["passives"] or 0),
                "detractors": detractors, "total": total, "score": score}
    return _guard(q, {"promoters": 0, "passives": 0, "detractors": 0, "total": 0, "score": None})


# ---------------------------------------------------------------------------
# Compose the whole overview
# ---------------------------------------------------------------------------

def overview(session, *, club_id: Optional[str] = None, days: int = 30) -> Dict[str, Any]:
    traffic = traffic_summary(session, club_id=club_id, days=days)
    nvr = new_vs_returning(session, club_id=club_id, days=days)
    tos = time_on_site(session, club_id=club_id, days=days)
    cust = customers(session, club_id=club_id, days=days)
    rev = bookings_and_revenue(session, club_id=club_id, days=days)
    return {
        "scope": {"club_id": str(club_id) if club_id else None, "days": int(days)},
        "kpis": {
            "visits": traffic["visits"],
            "unique_visitors": traffic["unique_visitors"],
            "new_visitors": nvr["new_visitors"],
            "returning_visitors": nvr["returning_visitors"],
            "avg_seconds": tos["avg_seconds"],
            "median_seconds": tos["median_seconds"],
            "total_customers": cust["total"],
            "new_customers": cust["new_in_window"],
            "bookings": rev["bookings"],
            "revenue_minor": rev["revenue_minor"],
            "net_minor": rev["net_minor"],
        },
        "visits_daily": visits_daily(session, club_id=club_id, days=days),
        "signups_daily": signups_daily(session, club_id=club_id, days=days),
        "traffic_sources": traffic_sources(session, club_id=club_id, days=days),
        "top_pages": top_pages(session, club_id=club_id, days=days),
        "by_country": by_country(session, club_id=club_id, days=days),
        "by_device": by_device(session, club_id=club_id, days=days),
        "by_browser": by_browser(session, club_id=club_id, days=days),
        "settlement_mix": settlement_mix(session, club_id=club_id, days=days),
        "nps": nps(session, club_id=club_id, days=days),
        "revenue": rev,
    }

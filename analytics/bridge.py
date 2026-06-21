# analytics/bridge.py — pull a SEPARATE business's overview metrics into the dashboard.
#
# Ten-Fifty5 (1050) is its own app + DB. This bridge fetches its already-aggregated cockpit
# metrics over HTTPS, normalises them into the same overview shape the CourtFlow dashboard uses,
# and caches briefly so the dashboard never hammers 1050. It is READ-ONLY and GUARDED: if 1050 is
# unreachable or unconfigured, fetch_property returns {available: False} and the CourtFlow panels
# still render — the Ten-Fifty5 panel just shows "unavailable".
#
# CURRENCY: 1050 revenue is USD, CourtFlow is ZAR — they are NEVER summed. Each property carries
# its own `currency`; the "all" roll-up sums COUNT metrics only (see routes.py).
#
# Auth (Option A, no 1050 change): 1050's existing cockpit endpoints accept a shared key —
# header `X-Client-Key: <CLIENT_API_KEY>` + `?email=<admin>`. Config via env:
#   BRIDGE_TENFIFTY5_URL, BRIDGE_TENFIFTY5_CLIENT_KEY, BRIDGE_TENFIFTY5_ADMIN_EMAIL
# Option B (later, least-privilege): point BRIDGE_TENFIFTY5_URL at a dedicated X-Ops-Key metrics
# endpoint and set BRIDGE_TENFIFTY5_OPS_KEY — only the fetch auth changes, not the mapping.

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger("analytics.bridge")

_TIMEOUT = 8
_CACHE: Dict[str, Any] = {}      # key -> (expires_at, payload)
_CACHE_TTL = 300                 # 5 min — fast enough for a dashboard, gentle on 1050


# ---------------------------------------------------------------------------
# Property registry (which external businesses can be bridged)
# ---------------------------------------------------------------------------

def _tenfifty5_config() -> Optional[Dict[str, str]]:
    url = (os.getenv("BRIDGE_TENFIFTY5_URL") or "").strip().rstrip("/")
    if not url:
        return None
    return {
        "url": url,
        "client_key": (os.getenv("BRIDGE_TENFIFTY5_CLIENT_KEY") or "").strip(),
        "admin_email": (os.getenv("BRIDGE_TENFIFTY5_ADMIN_EMAIL") or "").strip(),
        "ops_key": (os.getenv("BRIDGE_TENFIFTY5_OPS_KEY") or "").strip(),
    }


def list_properties() -> List[Dict[str, Any]]:
    """The properties the dashboard can show. courtflow is always present (local data);
    ten-fifty5 appears as available only when its bridge env is configured."""
    props = [{"id": "courtflow", "label": "NextPoint / CourtFlow", "currency": "ZAR", "available": True}]
    props.append({"id": "ten-fifty5", "label": "Ten-Fifty5", "currency": "USD",
                  "available": _tenfifty5_config() is not None})
    return props


# ---------------------------------------------------------------------------
# Fetch + normalise
# ---------------------------------------------------------------------------

def _get(cfg: Dict[str, str], path: str) -> Optional[Dict[str, Any]]:
    headers = {}
    params = {}
    if cfg.get("ops_key"):
        headers["X-Ops-Key"] = cfg["ops_key"]            # Option B
    elif cfg.get("client_key"):
        headers["X-Client-Key"] = cfg["client_key"]      # Option A
        if cfg.get("admin_email"):
            params["email"] = cfg["admin_email"]
    try:
        r = requests.get(cfg["url"] + path, headers=headers, params=params, timeout=_TIMEOUT)
        if r.status_code // 100 != 2:
            log.info("bridge: 1050 %s -> HTTP %s", path, r.status_code)
            return None
        return r.json() or {}
    except requests.RequestException as e:
        log.info("bridge: 1050 %s unreachable (%s)", path, e.__class__.__name__)
        return None


def _within(day_or_month: str, days: int) -> bool:
    # Keep series points within roughly the window (string compare on ISO dates is monotonic).
    try:
        cutoff = time.gmtime(time.time() - days * 86400)
        cut = time.strftime("%Y-%m-%d", cutoff)
        return str(day_or_month)[:10] >= cut
    except Exception:
        return True


def _sum(rows, key) -> int:
    return int(sum(int((r or {}).get(key) or 0) for r in (rows or [])))


def fetch_tenfifty5(days: int) -> Dict[str, Any]:
    """Fetch + normalise 1050's metrics into the overview shape. Always returns a dict;
    {available: False} if unconfigured/unreachable. Cached for _CACHE_TTL seconds."""
    cfg = _tenfifty5_config()
    if not cfg:
        return {"property": "ten-fifty5", "label": "Ten-Fifty5", "currency": "USD",
                "available": False, "reason": "not_configured"}

    ckey = f"ten-fifty5:{days}"
    hit = _CACHE.get(ckey)
    if hit and hit[0] > time.time():
        return hit[1]

    if cfg.get("ops_key"):
        # Option B: ONE least-privilege endpoint returning {health, performance, feedback}.
        blob = _get(cfg, f"/api/ops/metrics/overview?days={int(days)}") or {}
        health = blob.get("health") or {}
        perf = blob.get("performance") or {}
        feedback = blob.get("feedback") or {}
    else:
        # Option A: reuse 1050's EXISTING admin cockpit endpoints (shared CLIENT_API_KEY + email).
        health = (_get(cfg, "/api/client/backoffice/cockpit/business-health") or {}).get("health") or {}
        perf = _get(cfg, "/api/client/backoffice/cockpit/performance") or {}
        feedback = (_get(cfg, "/api/client/backoffice/cockpit/feedback") or {}).get("summary") or {}

    if not health and not perf:
        payload = {"property": "ten-fifty5", "label": "Ten-Fifty5", "currency": "USD",
                   "available": False, "reason": "unreachable"}
        _CACHE[ckey] = (time.time() + 60, payload)   # short cache on failure
        return payload

    visitors = [v for v in (perf.get("visitors_daily") or []) if _within(v.get("day", ""), days)]
    revenue_months = [m for m in (perf.get("revenue_monthly") or []) if _within(m.get("month", ""), days)]
    new_accounts = [m for m in (perf.get("new_accounts") or []) if _within(m.get("month", ""), days)]

    visits = _sum(visitors, "page_views")
    unique = _sum(visitors, "unique_visitors")          # daily-summed (1050 exposes daily only)
    revenue_cents = _sum(revenue_months, "net_cents") if revenue_months \
        else int(health.get("payg_revenue_cents") or 0)

    total = int(feedback.get("responses") or 0)
    promoters = int(feedback.get("promoters") or 0)
    detractors = int(feedback.get("detractors") or 0)
    nps_score = feedback.get("nps")

    payload = {
        "property": "ten-fifty5",
        "label": "Ten-Fifty5",
        "currency": "USD",
        "available": True,
        "scope": {"days": int(days)},
        "kpis": {
            "visits": visits,
            "unique_visitors": unique,
            "new_visitors": None,                       # 1050 doesn't expose the split
            "returning_visitors": None,
            "total_customers": int(health.get("total_accounts") or 0),
            "new_customers": int(health.get("new_accounts_this_month") or 0),
            "bookings": None,                           # not a booking business
            "revenue_minor": revenue_cents,
            "net_minor": revenue_cents,
        },
        # Business-specific KPIs rendered as labelled extras (uniform two-business view).
        "extra_kpis": [
            {"label": "MRR", "value_minor": int(health.get("mrr_cents") or 0), "currency": "USD"},
            {"label": "Active subscriptions", "value": int(health.get("active_subscriptions") or 0)},
            {"label": "Churned (month)", "value": int(health.get("churned_this_month") or 0)},
        ],
        "visits_daily": [{"day": v.get("day"), "visits": int(v.get("page_views") or 0),
                          "unique_visitors": int(v.get("unique_visitors") or 0)} for v in visitors],
        "signups_daily": [{"day": m.get("month"), "signups": int(m.get("new_accounts") or 0)}
                          for m in new_accounts],
        # 1050's cockpit doesn't break out source/page/geo — empty until its Option B endpoint adds them.
        "traffic_sources": [],
        "top_pages": [],
        "by_country": [],
        "settlement_mix": [],
        "nps": {"promoters": promoters, "passives": int(feedback.get("passives") or 0),
                "detractors": detractors, "total": total,
                "score": round(nps_score) if isinstance(nps_score, (int, float)) else None},
        "revenue": {"revenue_minor": revenue_cents, "refunded_minor": 0, "net_minor": revenue_cents},
        "notes": "Revenue & MRR in USD. Unique = sum of daily unique (1050 exposes daily granularity). "
                 "Source/page/country breakdowns need 1050's Option-B endpoint.",
    }
    _CACHE[ckey] = (time.time() + _CACHE_TTL, payload)
    return payload


def fetch_property(name: str, *, days: int) -> Optional[Dict[str, Any]]:
    if (name or "").strip().lower() == "ten-fifty5":
        return fetch_tenfifty5(days)
    return None

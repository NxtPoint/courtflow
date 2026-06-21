# 12 · Ten-Fifty5 (1050) bridge — Business Overview

The CourtFlow **Business Overview** dashboard (`analytics/`) can show **Ten-Fifty5** alongside
**NextPoint/CourtFlow**. 1050 is a *separate app + database*; the bridge fetches its already-aggregated
cockpit metrics over HTTPS, normalises them into the dashboard's shape, and caches for ~5 min. Read-only,
guarded (1050 unreachable → the panel shows "unavailable", CourtFlow still renders).

**Currency:** 1050 = USD, CourtFlow = ZAR. They are **never summed**. Each property keeps its own currency;
the **All** view sums *count* metrics only and lists revenue per business.

## Where it lives (CourtFlow side — built)
- `analytics/bridge.py` — `fetch_tenfifty5(days)`; auth + mapping; in-memory cache.
- `analytics/routes.py` — `GET /api/analytics/overview?property=courtflow|ten-fifty5|all`, `…/properties`.
- `frontend/js/overview.js` + `overview.html` — a **business switcher** (platform-admin only), shown only
  when the bridge is configured.
- `render.yaml` — `BRIDGE_TENFIFTY5_*` env (all `sync:false`).

## Option A — live now, NO 1050 change (current default)
The bridge reuses 1050's existing admin cockpit endpoints with the shared key. Set on **courtflow-api**:

```
BRIDGE_TENFIFTY5_URL          = https://<1050-api-host>
BRIDGE_TENFIFTY5_CLIENT_KEY   = <1050 CLIENT_API_KEY>
BRIDGE_TENFIFTY5_ADMIN_EMAIL  = info@ten-fifty5.com     # an ADMIN_EMAILS address on 1050
```
The bridge calls `…/cockpit/business-health`, `…/cockpit/performance`, `…/cockpit/feedback` with
`X-Client-Key` + `?email=`. Caveat: that key can hit *all* 1050 admin endpoints — fine to start, but
prefer Option B for least-privilege.

## Option B — least-privilege (recommended target; needs a tiny 1050 deploy)
Add ONE read-only endpoint to the **1050** repo, then on CourtFlow set `BRIDGE_TENFIFTY5_URL=<1050 host>`
+ `BRIDGE_TENFIFTY5_OPS_KEY=<key>` (leave CLIENT_KEY/EMAIL unset). The bridge auto-switches to the single
ops endpoint — **no CourtFlow code change**, just env.

Drop this into 1050 (e.g. `ops_metrics.py`) and `app.register_blueprint(ops_metrics_bp)`:

```python
# ops_metrics.py — read-only Business-Overview metrics for the CourtFlow dashboard bridge.
# OPS_KEY (header) auth only. Reads existing core.vw_* views — no schema changes.
import hmac, os
from flask import Blueprint, jsonify, request
from sqlalchemy import text
from db import engine            # 1050's SQLAlchemy engine

ops_metrics_bp = Blueprint("ops_metrics", __name__)

def _ops_ok():
    key = (os.getenv("OPS_KEY") or "").strip()
    sup = (request.headers.get("X-Ops-Key") or "").strip()
    return bool(key) and hmac.compare_digest(sup, key)

def _rows(c, sql): return [dict(r) for r in c.execute(text(sql)).mappings().all()]
def _one(c, sql):
    r = c.execute(text(sql)).mappings().first(); return dict(r) if r else {}

@ops_metrics_bp.get("/api/ops/metrics/overview")
def ops_metrics_overview():
    if not _ops_ok():
        return jsonify(ok=False, error="forbidden"), 403
    try:
        days = max(1, min(int(request.args.get("days") or 30), 365))
    except (TypeError, ValueError):
        days = 30
    with engine.connect() as c:
        return jsonify(
            health=_one(c, "SELECT * FROM core.vw_business_health"),
            performance={
                "visitors_daily":  _rows(c, f"SELECT * FROM core.vw_visitors_daily  WHERE day >= current_date - {days} ORDER BY day"),
                "revenue_monthly": _rows(c, "SELECT * FROM core.vw_revenue_monthly ORDER BY month DESC LIMIT 12"),
                "new_accounts":    _rows(c, "SELECT * FROM core.vw_new_accounts_monthly ORDER BY month DESC LIMIT 12"),
            },
            feedback=_one(c, "SELECT * FROM core.vw_nps_summary"),
        )
```

Set `OPS_KEY` on 1050 (reuse the existing one or a dedicated value) and the matching
`BRIDGE_TENFIFTY5_OPS_KEY` on CourtFlow. Done — least-privilege, one route, no PII (aggregates only).

## What the 1050 column shows
Populated from 1050's data: **website visits + unique visitors** (its `vw_visitors_daily`), **customers**
(`total_accounts`, `new_accounts_this_month`), **revenue (USD)** + **MRR** + **active subscriptions**
(`vw_business_health` / `vw_revenue_monthly`), **NPS** (`vw_nps_summary`). Not shown (1050's cockpit doesn't
break them out): traffic **source / top-page / country** — add them to the Option-B endpoint later if wanted.
`new vs returning` is CourtFlow-only (1050 exposes daily uniques, not the split).

# analytics/routes.py — the Business Overview blueprint (analytics_bp). Registered in app.py.
#
# Endpoints (read-only):
#   GET /api/analytics/overview?days=30&club_id=<uuid?>&property=courtflow|ten-fifty5|all
#       property defaults to 'courtflow' (local data). Non-courtflow properties (the 1050 bridge,
#       and 'all') are PLATFORM_ADMIN-only — that's cross-business owner data.
#       platform_admin: all clubs or one via ?club_id. club_admin: forced to their own club, courtflow.
#   GET /api/analytics/properties  — which businesses the dashboard can show (courtflow + bridged).
#   GET /api/analytics/clubs  — platform_admin only: clubs for the filter dropdown.
#
# Auth: auth.principal.resolve_principal; gate via iam.permissions.can('view_club_analytics').
# Aggregation is in analytics.repositories (guarded SELECTs); the 1050 column comes from
# analytics.bridge (guarded HTTPS fetch). CURRENCY: ZAR vs USD are never summed — 'all' sums COUNTS
# only and lists revenue per property.

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

log = logging.getLogger("analytics.routes")

analytics_bp = Blueprint("analytics", __name__)


def _clamp_days(raw) -> int:
    try:
        d = int(raw)
    except (TypeError, ValueError):
        return 30
    return max(1, min(d, 365))


def _courtflow_overview(days, club_id):
    from db import session_scope
    from analytics import repositories as repo
    with session_scope() as s:
        data = repo.overview(s, club_id=club_id, days=days)
    data.update({"property": "courtflow", "label": "NextPoint / CourtFlow",
                 "currency": "ZAR", "available": True})
    return data


def _combine_counts(props):
    """Sum COUNT metrics across available properties; revenue stays per-property (mixed currency)."""
    keys = ["visits", "unique_visitors", "total_customers", "new_customers", "bookings"]
    out = {k: 0 for k in keys}
    revenue_by_property = []
    for pr in props:
        if not pr.get("available"):
            continue
        kp = pr.get("kpis") or {}
        for k in keys:
            v = kp.get(k)
            if isinstance(v, (int, float)):
                out[k] += v
        revenue_by_property.append({
            "property": pr.get("property"), "label": pr.get("label"),
            "currency": pr.get("currency"), "revenue_minor": kp.get("revenue_minor") or 0,
        })
    out["revenue_by_property"] = revenue_by_property
    return out


@analytics_bp.get("/api/analytics/overview")
def analytics_overview():
    from auth import resolve_principal
    from iam.permissions import can

    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return jsonify(error="unauthorized"), 401

    prop = (request.args.get("property") or "courtflow").strip().lower()
    days = _clamp_days(request.args.get("days") or 30)

    # Cross-business properties (the 1050 bridge / 'all') are platform-owner only.
    if prop != "courtflow" and not p.is_platform_admin:
        return jsonify(error="forbidden"), 403

    # Resolve the courtflow club scope (platform_admin = all/filter; club_admin = own club).
    if p.is_platform_admin:
        club_id = (request.args.get("club_id") or "").strip() or None
    elif can(p, "view_club_analytics", {"club_id": p.club_id}):
        club_id = p.club_id
    else:
        return jsonify(error="forbidden"), 403

    if prop == "ten-fifty5":
        from analytics import bridge
        return jsonify(bridge.fetch_property("ten-fifty5", days=days)), 200

    if prop == "all":
        from analytics import bridge
        cf = _courtflow_overview(days, None)            # platform-wide courtflow
        tf = bridge.fetch_property("ten-fifty5", days=days)
        props = [cf] + ([tf] if tf else [])
        return jsonify({"property": "all", "scope": {"days": days},
                        "properties": props, "combined": _combine_counts(props)}), 200

    # Default: courtflow.
    return jsonify(_courtflow_overview(days, club_id)), 200


@analytics_bp.get("/api/analytics/properties")
def analytics_properties():
    from auth import resolve_principal
    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return jsonify(error="unauthorized"), 401
    # club_admin only ever sees courtflow; platform_admin sees the bridged businesses too.
    from analytics import bridge
    if p.is_platform_admin:
        return jsonify(properties=bridge.list_properties()), 200
    return jsonify(properties=[{"id": "courtflow", "label": "NextPoint / CourtFlow",
                                "currency": "ZAR", "available": True}]), 200


@analytics_bp.get("/api/analytics/clubs")
def analytics_clubs():
    """Platform_admin only — the club list for the dashboard's filter dropdown."""
    from auth import resolve_principal

    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return jsonify(error="unauthorized"), 401
    if not p.is_platform_admin:
        return jsonify(error="forbidden"), 403

    from db import session_scope
    from sqlalchemy import text
    with session_scope() as s:
        rows = s.execute(text(
            "SELECT id, name FROM club.club WHERE status = 'active' ORDER BY name"
        )).mappings().all()
    return jsonify(clubs=[{"id": str(r["id"]), "name": r["name"]} for r in rows]), 200

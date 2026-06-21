# analytics/routes.py — the Business Overview blueprint (analytics_bp). Registered in app.py.
#
# Endpoints (read-only):
#   GET /api/analytics/overview?days=30&club_id=<uuid?>  — the whole dashboard payload.
#       platform_admin: all clubs, or one club via ?club_id. club_admin: forced to their own club.
#   GET /api/analytics/clubs  — platform_admin only: clubs for the filter dropdown.
#
# Auth: auth.principal.resolve_principal; gate via iam.permissions.can('view_club_analytics').
# DB-touching imports stay lazy (app.py boot discipline). All aggregation is in
# analytics.repositories (guarded SELECTs — a missing/empty table yields empty panels, not 500s).

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


@analytics_bp.get("/api/analytics/overview")
def analytics_overview():
    from auth import resolve_principal
    from iam.permissions import can

    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return jsonify(error="unauthorized"), 401

    # Scope: platform_admin sees all (or ?club_id=); club_admin is forced to their own club.
    if p.is_platform_admin:
        club_id = (request.args.get("club_id") or "").strip() or None
    elif can(p, "view_club_analytics", {"club_id": p.club_id}):
        club_id = p.club_id
    else:
        return jsonify(error="forbidden"), 403

    days = _clamp_days(request.args.get("days") or 30)

    from db import session_scope
    from analytics import repositories as repo
    with session_scope() as s:
        data = repo.overview(s, club_id=club_id, days=days)
    return jsonify(data), 200


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

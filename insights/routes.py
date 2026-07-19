# insights/routes.py — Phase 2 P1 insight read-layer HTTP surface (docs/specs/ADMIN-PHASE2.md).
#
# Thin, like the other lanes: resolve the principal, gate to admins, pull club_id FROM THE PRINCIPAL
# (never the body), call insights.repositories, return JSON. Every read is guarded in the repo, so
# these routes never 500 on missing/empty data.

from flask import Blueprint, jsonify, request

from auth import resolve_principal
from db import session_scope
from insights import repositories as repo

insights_bp = Blueprint("insights", __name__, url_prefix="/api/insights")

_ADMIN_ROLES = ("club_admin", "platform_admin")


def _admin():
    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return None, (jsonify(error="unauthorized"), 401)
    if p.role not in _ADMIN_ROLES:
        return None, (jsonify(error="forbidden"), 403)
    if p.club_id is None:
        return None, (jsonify(error="no_club_scope"), 400)
    return p, None


@insights_bp.get("/overview")
def overview():
    """Month-scoped daily business overview for the admin 'Overview' tab — dense per-day series
    (traffic, bookings, revenue, clients, members, NPS) + KPI totals + traffic breakdowns, all
    reconciling with Money → Sales/Bookings by day. ?month=YYYY-MM (default current)."""
    p, err = _admin()
    if err:
        return err
    month = (request.args.get("month") or "").strip() or None
    with session_scope() as s:
        data = repo.overview(s, club_id=p.club_id, month=month)
    return jsonify(data), 200


@insights_bp.get("/bookings-by-day")
def bookings_by_day():
    """Daily bookings for one ?month=YYYY-MM (default current) — grouped by the day played, each
    booking with client + service type + coach + status + a detail link (booking_id -> event
    story). Powers the Money -> 'Bookings by day' section (sibling of Sales by day)."""
    p, err = _admin()
    if err:
        return err
    month = (request.args.get("month") or "").strip() or None
    with session_scope() as s:
        data = repo.bookings_by_day(s, club_id=p.club_id, month=month)
    return jsonify(data), 200


@insights_bp.get("/trial-cohorts")
def trial_cohorts():
    """Trial→paid conversion by start-month cohort over the last ?months (default 6) — started +
    converted-within-14d/30d/ever + rates. Powers the Overview → Members cohort panel."""
    p, err = _admin()
    if err:
        return err
    try:
        months = int(request.args.get("months") or 6)
    except (TypeError, ValueError):
        months = 6
    with session_scope() as s:
        data = repo.trial_cohorts(s, club_id=p.club_id, months=months)
    return jsonify(data), 200


@insights_bp.get("/web-metrics")
def web_metrics():
    """Latest Google (GA4 + Search Console) snapshot for the Overview → Acquisition panel — GA4
    totals/channels/top-pages/geo + GSC totals/top-queries/striking-distance, with the snapshot
    date. Reads core.web_daily (fed by the marketing-digest ingest). {connected:false} until the
    first ingest runs. Guarded → never 500s."""
    p, err = _admin()
    if err:
        return err
    with session_scope() as s:
        data = repo.web_metrics(s, club_id=p.club_id)
    return jsonify(data), 200


@insights_bp.get("/court-utilisation")
def court_utilisation():
    """Court occupancy heatmap (weekday x hour) + overall utilisation % over the last ?days (default
    30). Powers the Insights 'Court utilisation' panel — the first Phase-2 metric."""
    p, err = _admin()
    if err:
        return err
    try:
        days = int(request.args.get("days") or 30)
    except (TypeError, ValueError):
        days = 30
    with session_scope() as s:
        data = repo.court_utilisation(s, club_id=p.club_id, days=days)
    return jsonify(data), 200


@insights_bp.get("/sales-by-day")
def sales_by_day():
    """Daily sales for one ?month=YYYY-MM (default current) — grouped by day, each sale with client +
    service type + amount + a detail link. Powers the Money → 'Sales by day' section."""
    p, err = _admin()
    if err:
        return err
    month = (request.args.get("month") or "").strip() or None
    with session_scope() as s:
        data = repo.sales_by_day(s, club_id=p.club_id, month=month)
    return jsonify(data), 200

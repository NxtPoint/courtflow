# marketing_crm/backoffice/blueprint.py — the club-admin cockpit (thin views over the SoR).
#
# Routes under /api/admin/cockpit/*. Admin-gated: a verified Clerk JWT principal whose resolved role
# is club_admin or platform_admin, OR an OPS_KEY principal (server-to-server) — via the foundation's
# auth.resolve_principal (decision D6: no shared CLIENT_API_KEY, role from iam.membership). Every
# query is club_id-scoped to the caller's active club (platform_admin may pass ?club_id=).
#
# Endpoints split into:
#   • LIVE now — read core.* (the data Agent D owns): signups, usage/engagement, consent, NPS.
#   • STUBBED pending B/C — occupancy, revenue, coach-utilisation, attendance need diary.* / billing.*
#     views that don't exist yet. Each returns 501 with a guarded TODO naming the exact tables so it
#     lights up once B/C land (the table probe makes it self-activating).
#
# Aggregation stays in SQL (rule #2). Read-only (no writes from the cockpit beyond the CRM-sync
# trigger). Ported in shape from 1050 marketing_crm/backoffice/blueprint.py.

import logging

from flask import Blueprint, jsonify, request
from sqlalchemy import text

from db import get_engine

log = logging.getLogger("marketing_crm.backoffice")
cockpit_bp = Blueprint("cockpit", __name__)
_PREFIX = "/api/admin/cockpit"


def _principal():
    try:
        from auth import resolve_principal
        return resolve_principal(request)
    except Exception:
        log.exception("cockpit: principal resolution failed")
        return None


def _admin_ctx():
    """Return (ok, club_id, principal). ok is True only for club_admin / platform_admin / ops.
    club_id is the caller's active club; a platform_admin may target another via ?club_id=."""
    p = _principal()
    if p is None or not p.authenticated:
        return (False, None, p)
    role = getattr(p, "role", None)
    if role not in ("club_admin", "platform_admin"):
        return (False, None, p)
    club_id = getattr(p, "club_id", None)
    if getattr(p, "is_platform_admin", False):
        club_id = (request.args.get("club_id") or club_id)  # may scope across clubs
    return (True, club_id, p)


def _rows(sql, params=None):
    with get_engine().connect() as c:
        return [dict(r) for r in c.execute(text(sql), params or {}).mappings()]


def _one(sql, params=None):
    rows = _rows(sql, params)
    return rows[0] if rows else {}


def _table_exists(schema, table):
    """True if a relation (table OR view) exists — lets a stubbed endpoint self-activate once the
    owning lane (B/C) creates its view. Never raises."""
    try:
        r = _one("SELECT to_regclass(:q) AS reg", {"q": f"{schema}.{table}"})
        return bool(r.get("reg"))
    except Exception:
        return False


def _forbidden():
    return jsonify({"ok": False, "error": "forbidden"}), 403


def _stub(feature, needs):
    """Uniform 501 for an endpoint awaiting another lane's tables. `needs` names them so the
    dependency is explicit in the response and the code."""
    return jsonify({
        "ok": False, "status": "pending_lane",
        "feature": feature,
        "needs": needs,
        "note": f"{feature} lights up once {', '.join(needs)} exist (Agent B/C). "
                f"This endpoint self-activates when the table/view is present.",
    }), 501


# ── Health ───────────────────────────────────────────────────────────────────
@cockpit_bp.route(f"{_PREFIX}/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "cockpit"})


# ════════════════════════════════════════════════════════════════════════════
# LIVE — backed by core.* (Agent D's schema), available today.
# ════════════════════════════════════════════════════════════════════════════

@cockpit_bp.route(f"{_PREFIX}/signups", methods=["GET", "OPTIONS"])
def signups():
    """New accounts/users for the club + a monthly trend. core.account is the spine."""
    if request.method == "OPTIONS":
        return ("", 204)
    ok, club_id, _ = _admin_ctx()
    if not ok:
        return _forbidden()
    where = "deleted_at IS NULL" + (" AND club_id = :club" if club_id else "")
    params = {"club": club_id} if club_id else {}
    totals = _one(
        f"SELECT count(*) AS total_accounts, "
        f"       count(*) FILTER (WHERE created_at >= date_trunc('month', now())) AS new_this_month "
        f"FROM core.account WHERE {where}", params)
    monthly = _rows(
        f"SELECT to_char(date_trunc('month', created_at), 'YYYY-MM') AS month, count(*) AS signups "
        f"FROM core.account WHERE {where} "
        f"GROUP BY 1 ORDER BY 1 DESC LIMIT 24", params)
    opted_in = _one(
        "SELECT count(*) AS marketing_opt_in FROM core.app_user "
        "WHERE deleted_at IS NULL AND marketing_opt_in" + (" AND club_id = :club" if club_id else ""),
        params)
    return jsonify({"ok": True, "totals": {**totals, **opted_in}, "monthly": monthly})


@cockpit_bp.route(f"{_PREFIX}/usage", methods=["GET", "OPTIONS"])
def usage():
    """Engagement from core.usage_event: events/day, distinct accounts, and a breakdown by type."""
    if request.method == "OPTIONS":
        return ("", 204)
    ok, club_id, _ = _admin_ctx()
    if not ok:
        return _forbidden()
    where = "occurred_at >= now() - interval '90 days'" + (" AND club_id = :club" if club_id else "")
    params = {"club": club_id} if club_id else {}
    daily = _rows(
        f"SELECT date_trunc('day', occurred_at)::date AS day, count(*) AS events, "
        f"       count(DISTINCT account_id) AS distinct_accounts "
        f"FROM core.usage_event WHERE {where} GROUP BY 1 ORDER BY 1 DESC", params)
    by_type = _rows(
        f"SELECT event_type, count(*) AS events FROM core.usage_event WHERE {where} "
        f"GROUP BY 1 ORDER BY 2 DESC LIMIT 30", params)
    return jsonify({"ok": True, "daily": daily, "by_type": by_type})


@cockpit_bp.route(f"{_PREFIX}/consent", methods=["GET", "OPTIONS"])
def consent_overview():
    """Consent posture: latest status per type across the club (compliance at a glance)."""
    if request.method == "OPTIONS":
        return ("", 204)
    ok, club_id, _ = _admin_ctx()
    if not ok:
        return _forbidden()
    where = "1=1" + (" AND c.club_id = :club" if club_id else "")
    params = {"club": club_id} if club_id else {}
    # Latest record per (subject, type) → current state; count grants vs withdrawals.
    by_type = _rows(
        f"""
        SELECT consent_type,
               count(*) FILTER (WHERE status = 'granted')   AS granted,
               count(*) FILTER (WHERE status = 'withdrawn') AS withdrawn
        FROM (
            SELECT DISTINCT ON (c.subject_person_id, c.consent_type)
                   c.subject_person_id, c.consent_type, c.status
            FROM core.consent c WHERE {where}
            ORDER BY c.subject_person_id, c.consent_type, c.created_at DESC
        ) latest
        GROUP BY consent_type ORDER BY consent_type
        """, params)
    return jsonify({"ok": True, "by_type": by_type})


@cockpit_bp.route(f"{_PREFIX}/nps", methods=["GET", "OPTIONS"])
def nps():
    """NPS summary + recent verbatims from core.nps_response."""
    if request.method == "OPTIONS":
        return ("", 204)
    ok, club_id, _ = _admin_ctx()
    if not ok:
        return _forbidden()
    where = "1=1" + (" AND club_id = :club" if club_id else "")
    params = {"club": club_id} if club_id else {}
    summary = _one(
        f"""
        SELECT count(*) AS responses,
               count(*) FILTER (WHERE bucket='promoter')  AS promoters,
               count(*) FILTER (WHERE bucket='passive')   AS passives,
               count(*) FILTER (WHERE bucket='detractor') AS detractors,
               CASE WHEN count(*) = 0 THEN NULL
                    ELSE ROUND((count(*) FILTER (WHERE bucket='promoter')
                                - count(*) FILTER (WHERE bucket='detractor')) * 100.0 / count(*), 1)
               END AS nps
        FROM core.nps_response WHERE {where}
        """, params)
    verbatims = _rows(
        f"SELECT score, bucket, comment, submitted_at FROM core.nps_response "
        f"WHERE {where} AND comment IS NOT NULL AND comment <> '' "
        f"ORDER BY submitted_at DESC LIMIT 25", params)
    return jsonify({"ok": True, "summary": summary, "verbatims": verbatims})


@cockpit_bp.route(f"{_PREFIX}/sync-crm", methods=["POST", "OPTIONS"])
def sync_crm():
    """Trigger a full core.* → Klaviyo profile sync (admin; for a nightly cron or manual run).
    No-op unless KLAVIYO_API_KEY is set."""
    if request.method == "OPTIONS":
        return ("", 204)
    ok, _, _ = _admin_ctx()
    if not ok:
        return _forbidden()
    try:
        from marketing_crm.crm_sync import enabled, sync_all
        if not enabled():
            return jsonify({"ok": True, "synced": 0, "note": "Klaviyo not configured (no KLAVIYO_API_KEY)"})
        return jsonify({"ok": True, "synced": sync_all()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════════════════════
# STUBBED — need diary.* (Agent B) / billing.* (Agent C). Each self-activates when
# its backing relation exists (the to_regclass probe). Until then: 501 + the TODO.
# ════════════════════════════════════════════════════════════════════════════

@cockpit_bp.route(f"{_PREFIX}/occupancy", methods=["GET", "OPTIONS"])
def occupancy():
    """Court/resource occupancy & utilisation. TODO(Agent B): reads diary.booking + diary.resource
    (+ diary.availability_rule for the denominator). Compute booked-minutes / available-minutes per
    resource per day, club_id-scoped, status IN ('confirmed','completed')."""
    if request.method == "OPTIONS":
        return ("", 204)
    ok, _, _ = _admin_ctx()
    if not ok:
        return _forbidden()
    if not (_table_exists("diary", "booking") and _table_exists("diary", "resource")):
        return _stub("occupancy", ["diary.booking", "diary.resource", "diary.availability_rule"])
    # Self-activated path (lights up once Agent B's diary.* lands).
    return jsonify({"ok": True, "feature": "occupancy",
                    "note": "diary.* present — implement the occupancy aggregation here."})


@cockpit_bp.route(f"{_PREFIX}/revenue", methods=["GET", "OPTIONS"])
def revenue():
    """Revenue & settlement mix. TODO(Agent C): reads billing.payment (actual money in, by month)
    + billing.order (settlement_mode split) + billing.account_ledger (monthly-account balances),
    all club_id-scoped. Money in minor units."""
    if request.method == "OPTIONS":
        return ("", 204)
    ok, _, _ = _admin_ctx()
    if not ok:
        return _forbidden()
    if not _table_exists("billing", "payment"):
        return _stub("revenue", ["billing.payment", "billing.order", "billing.account_ledger"])
    return jsonify({"ok": True, "feature": "revenue",
                    "note": "billing.* present — implement the revenue aggregation here."})


@cockpit_bp.route(f"{_PREFIX}/coach-utilisation", methods=["GET", "OPTIONS"])
def coach_utilisation():
    """Coach utilisation. TODO(Agent B): reads diary.booking WHERE booking_type='lesson'
    (+ coach_user_id) joined to iam.coach_profile for the roster; booked lesson-hours per coach
    vs their availability_rule hours. club_id-scoped."""
    if request.method == "OPTIONS":
        return ("", 204)
    ok, _, _ = _admin_ctx()
    if not ok:
        return _forbidden()
    if not _table_exists("diary", "booking"):
        return _stub("coach_utilisation", ["diary.booking", "iam.coach_profile", "diary.availability_rule"])
    return jsonify({"ok": True, "feature": "coach_utilisation",
                    "note": "diary.* present — implement the coach-utilisation aggregation here."})


@cockpit_bp.route(f"{_PREFIX}/attendance", methods=["GET", "OPTIONS"])
def attendance():
    """Class attendance & no-shows. TODO(Agent B): reads diary.enrolment (status counts:
    attended/no_show/cancelled) + diary.class_session for the schedule, club_id-scoped.
    Also surfaces diary.booking.status='no_show' for court no-shows."""
    if request.method == "OPTIONS":
        return ("", 204)
    ok, _, _ = _admin_ctx()
    if not ok:
        return _forbidden()
    if not _table_exists("diary", "enrolment"):
        return _stub("attendance", ["diary.enrolment", "diary.class_session", "diary.booking"])
    return jsonify({"ok": True, "feature": "attendance",
                    "note": "diary.* present — implement the attendance aggregation here."})


def register(app):
    """Register the cockpit blueprint. Always on (every route is admin-gated via _admin_ctx)."""
    app.register_blueprint(cockpit_bp)
    return True

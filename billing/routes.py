# billing/routes.py — the billing blueprint (billing_bp). Registered in app.py.
#
# Endpoints:
#   GET  /api/billing/config            PUBLIC probe (mirrors 1050's paypal/config). Returns
#                                       {online_enabled, provider, currency, public_key} so the
#                                       frontend renders the right checkout — or hides it and
#                                       shows pay-at-court. Flipping club.policy.allow_online_payment
#                                       / PAYMENTS_ENABLED is the instant rollback switch.
#   POST /api/billing/desk-payment      ADMIN (role-gated: take_pay_at_court). Records a desk
#                                       payment (cash/card_at_desk/eft) via the manual gateway ->
#                                       apply_payment_event. Closes out an at_court order.
#   POST /api/cron/monthly-invoice      OPS-only (cron). Builds monthly statements from the
#                                       account_ledger (matches crons/trigger.py JOB_ROUTES).
#
# Auth: auth.principal.resolve_principal (club-scoped); role gate via iam.permissions.can.
# All imports inside handlers stay lazy where they touch the DB so the module imports clean
# with no DATABASE_URL (app.py boot discipline).

from __future__ import annotations

import os

from flask import Blueprint, jsonify, request

billing_bp = Blueprint("billing", __name__)


def _truthy(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip() in ("1", "true", "True")


# ---------------------------------------------------------------------------
# GET /api/billing/config — public probe (drives the frontend + instant rollback)
# ---------------------------------------------------------------------------

@billing_bp.get("/api/billing/config")
def billing_config():
    """Public. online_enabled is true ONLY when BOTH the global flag (PAYMENTS_ENABLED) and
    the club's policy (allow_online_payment) are on — either being off hides online checkout
    (instant rollback). Never returns secret keys: only the publishable key."""
    provider = (os.getenv("PAYMENTS_PROVIDER") or "manual").strip().lower()
    global_on = _truthy("PAYMENTS_ENABLED")

    club_id = (request.args.get("club_id") or "").strip() or None
    currency = "ZAR"
    club_allows = False
    # Desk + monthly-account settlement default ON (matches the backend guard's defaults); the
    # booking picker reads these so it only ever offers a mode the club actually allows.
    allow_at_court = True
    allow_monthly = True

    # Resolve the club's policy/currency if we have a DB + a club hint. Best-effort.
    if club_id and (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or os.getenv("DB_URL")):
        try:
            from db import session_scope
            from sqlalchemy import text
            with session_scope() as s:
                row = s.execute(
                    text("""
                        SELECT c.currency_code,
                               COALESCE(p.allow_online_payment, false) AS allow_online,
                               COALESCE(p.allow_pay_at_court, true)    AS allow_at_court,
                               COALESCE(p.allow_monthly_account, true) AS allow_monthly
                        FROM club.club c
                        LEFT JOIN club.policy p ON p.club_id = c.id
                        WHERE c.id = :id
                    """),
                    {"id": club_id},
                ).mappings().first()
                if row:
                    currency = row["currency_code"] or "ZAR"
                    club_allows = bool(row["allow_online"])
                    allow_at_court = bool(row["allow_at_court"])
                    allow_monthly = bool(row["allow_monthly"])
        except Exception:
            pass

    public_key = ""
    if provider == "yoco":
        public_key = os.getenv("YOCO_PUBLIC_KEY", "")
    elif provider == "paypal":
        public_key = os.getenv("PAYPAL_CLIENT_ID", "")

    online_enabled = bool(global_on and club_allows)
    return jsonify({
        "online_enabled": online_enabled,
        "provider": provider if online_enabled else "manual",
        "currency": currency,
        "public_key": public_key if online_enabled else "",
        "allow_at_court": allow_at_court,
        "allow_monthly": allow_monthly,
    }), 200


# ---------------------------------------------------------------------------
# POST /api/billing/desk-payment — admin records a pay-at-court settlement
# ---------------------------------------------------------------------------

@billing_bp.post("/api/billing/desk-payment")
def desk_payment():
    """Admin-only. Body: {order_id, amount_minor, provider?(cash|card_at_desk|eft),
    currency_code?, provider_payment_id?(receipt no, for idempotency)}. Records the payment
    and closes the order (-> 'paid'); confirms any held booking on the order."""
    from auth import resolve_principal
    from iam.permissions import can

    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return jsonify(error="unauthorized"), 401

    body = request.get_json(silent=True) or {}
    order_id = (body.get("order_id") or "").strip()
    if not order_id:
        return jsonify(error="order_id required"), 400

    from db import session_scope
    from billing import orders as orders_repo

    with session_scope() as s:
        order = orders_repo.get_order(s, order_id=order_id)
        if not order:
            return jsonify(error="order not found"), 404
        # Role gate, scoped to the order's club (take_pay_at_court -> club_admin).
        if not can(p, "take_pay_at_court", {"club_id": order["club_id"]}):
            return jsonify(error="forbidden"), 403

        amount = body.get("amount_minor")
        amount = int(amount) if amount is not None else int(order["amount_minor"] or 0)
        result = orders_repo.record_desk_payment(
            s,
            club_id=order["club_id"],
            order_id=order_id,
            amount_minor=amount,
            provider=(body.get("provider") or "cash"),
            currency_code=body.get("currency_code") or order["currency_code"],
            provider_payment_id=body.get("provider_payment_id"),
            user_id=order["user_id"],
            recorded_by=p.user_id,                       # cash-audit: WHO took the money (not the payer)
            allow_partial=bool(body.get("allow_partial")),
        )
        if isinstance(result, dict) and result.get("error"):
            return jsonify(result), 422               # amount mismatch / order not owed (A2 guard)
    return jsonify(result), 200


# The monthly-invoice cron route was RETIRED with the account_ledger monthly tab — the unified
# statement (billing/statement.py) is the single debt of record, settleable online any time, and a
# coach issues a client's month-end statement per-client via POST /api/coach/clients/<id>/issue-invoice.


# ---------------------------------------------------------------------------
# POST /api/cron/month-end — OPS-only month-end sweep (fired by the keep-warm GitHub Action, NOT an
# always-on Render cron). Accrues coach arrears + rent for the period, then notifies every client with
# an open statement balance (statement_ready). Idempotent per (club,user,period). Body/query:
# {club_id?, period?(YYYY-MM)}. No club_id → sweep all clubs.
# ---------------------------------------------------------------------------

@billing_bp.post("/api/cron/month-end")
def cron_month_end():
    import logging
    from sqlalchemy import text
    from auth import resolve_principal
    from db import session_scope
    from billing import commission as comm

    log = logging.getLogger("billing.routes")
    p = resolve_principal(request)
    if p is None or not p.is_platform_admin:
        return jsonify(error="unauthorized"), 401

    body = request.get_json(silent=True) or {}
    club_id = (body.get("club_id") or request.args.get("club_id") or "").strip() or None
    period = (body.get("period") or request.args.get("period") or "").strip() or None

    results = []
    with session_scope() as s:
        clubs = [club_id] if club_id else [str(r[0]) for r in s.execute(text("SELECT id FROM club.club")).all()]
        for cid in clubs:
            try:
                results.append(comm.run_month_end(s, club_id=cid, period_label=period))
            except Exception:
                log.warning("month-end sweep failed for club=%s", cid, exc_info=False)
    return jsonify(clubs=len(results), results=results), 200

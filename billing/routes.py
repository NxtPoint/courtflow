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

    # Resolve the club's policy/currency if we have a DB + a club hint. Best-effort.
    if club_id and (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or os.getenv("DB_URL")):
        try:
            from db import session_scope
            from sqlalchemy import text
            with session_scope() as s:
                row = s.execute(
                    text("""
                        SELECT c.currency_code,
                               COALESCE(p.allow_online_payment, false) AS allow_online
                        FROM club.club c
                        LEFT JOIN club.policy p ON p.club_id = c.id
                        WHERE c.id = :id
                    """),
                    {"id": club_id},
                ).mappings().first()
                if row:
                    currency = row["currency_code"] or "ZAR"
                    club_allows = bool(row["allow_online"])
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
        )
    return jsonify(result), 200


# ---------------------------------------------------------------------------
# POST /api/cron/monthly-invoice — OPS-only statement builder (cron)
# ---------------------------------------------------------------------------

@billing_bp.post("/api/cron/monthly-invoice")
def cron_monthly_invoice():
    """OPS-only (crons/trigger.py posts here with X-Ops-Key). Builds per-member statements
    from billing.account_ledger for each club (or one club via ?club_id / body.club_id).
    Returns the statements; emailing them is wired at Phase 4 (Klaviyo/SES). Idempotent —
    a statement build is a pure read, safe to re-run."""
    from auth import resolve_principal
    p = resolve_principal(request)
    if p is None or not p.is_platform_admin:
        return jsonify(error="unauthorized"), 401

    body = request.get_json(silent=True) or {}
    club_id = (body.get("club_id") or request.args.get("club_id") or "").strip() or None
    period_start = body.get("period_start")
    period_end = body.get("period_end")

    from db import session_scope
    from sqlalchemy import text
    from billing import ledger

    out = []
    with session_scope() as s:
        if club_id:
            club_ids = [club_id]
        else:
            rows = s.execute(text("SELECT id FROM club.club WHERE status = 'active'")).mappings().all()
            club_ids = [str(r["id"]) for r in rows]

        for cid in club_ids:
            statements = ledger.build_statements(
                s, club_id=cid, period_start=period_start, period_end=period_end)
            if statements:
                out.append({"club_id": cid, "statement_count": len(statements),
                            "statements": statements})

    return jsonify({"ok": True, "clubs": len(out), "results": out}), 200

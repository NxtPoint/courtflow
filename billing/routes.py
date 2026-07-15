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
# Invoice DOCUMENTS — serve JSON + a professional PDF (owner or view_finances; else the payer).
# An invoice RENDERS over live orders (its paid status is derived live) — see billing/invoicing.py.
# ---------------------------------------------------------------------------

def _invoice_guard(order_user_id, invoice_club_id):
    """Return (principal, error_tuple). Allows the bill-to payer OR a club-finance viewer."""
    from auth import resolve_principal
    from iam.permissions import can
    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return None, (jsonify(error="unauthorized"), 401)
    same_club = bool(p.club_id and str(p.club_id) == str(invoice_club_id))
    owns = bool(p.user_id and order_user_id and str(order_user_id) == str(p.user_id))
    if not (owns or (same_club and can(p, "view_finances", {"club_id": invoice_club_id}))):
        return None, (jsonify(error="forbidden"), 403)
    return p, None


@billing_bp.get("/api/billing/invoice/<invoice_id>")
def billing_invoice(invoice_id):
    """JSON of an issued invoice document (frozen lines + live paid/outstanding)."""
    from db import session_scope
    from billing import invoicing
    from sqlalchemy import text
    with session_scope() as s:
        head = s.execute(text("SELECT club_id, user_id FROM billing.invoice WHERE id = :i"),
                         {"i": str(invoice_id)}).mappings().first()
        if not head:
            return jsonify(error="not_found"), 404
        _, err = _invoice_guard(head["user_id"], head["club_id"])
        if err:
            return err
        doc = invoicing.build_invoice_document(s, invoice_id=invoice_id)
    return jsonify(invoice=doc), 200


@billing_bp.get("/api/billing/invoice/<invoice_id>/pdf")
def billing_invoice_pdf(invoice_id):
    """The invoice as a downloadable PDF (professional letterhead + bank details)."""
    from flask import Response
    from db import session_scope
    from billing import invoicing, invoice_pdf
    from sqlalchemy import text
    with session_scope() as s:
        head = s.execute(text("SELECT club_id, user_id FROM billing.invoice WHERE id = :i"),
                         {"i": str(invoice_id)}).mappings().first()
        if not head:
            return jsonify(error="not_found"), 404
        _, err = _invoice_guard(head["user_id"], head["club_id"])
        if err:
            return err
        doc = invoicing.build_invoice_document(s, invoice_id=invoice_id)
        pay_url = invoicing.portal_url(s, head["club_id"])
        pdf = invoice_pdf.render_pdf(doc, pay_online_url=pay_url)
        fname = (doc.get("number") or "invoice").replace("/", "-") + ".pdf"
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{fname}"'})


@billing_bp.post("/api/billing/invoice/<invoice_id>/mark-paid")
def billing_invoice_mark_paid(invoice_id):
    """Admin marks an invoice PAID by EFT/cash/card-at-desk (take_pay_at_court). Settles every
    open order it covers via the desk-payment core → receipts fire → the invoice shows Paid.
    Body: {provider?(eft|cash|card_at_desk), reference?}."""
    from auth import resolve_principal
    from iam.permissions import can
    from db import session_scope
    from billing import invoicing
    from sqlalchemy import text

    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return jsonify(error="unauthorized"), 401
    body = request.get_json(silent=True) or {}
    with session_scope() as s:
        head = s.execute(text("SELECT club_id FROM billing.invoice WHERE id = :i"),
                         {"i": str(invoice_id)}).mappings().first()
        if not head:
            return jsonify(error="not_found"), 404
        if not can(p, "take_pay_at_court", {"club_id": head["club_id"]}):
            return jsonify(error="forbidden"), 403
        res = invoicing.mark_invoice_paid(
            s, club_id=head["club_id"], invoice_id=invoice_id,
            provider=(body.get("provider") or "eft"),
            reference=((body.get("reference") or "").strip() or None),
            recorded_by=p.user_id)
        if not res.get("ok"):
            return jsonify(res), 422
        doc = invoicing.build_invoice_document(s, invoice_id=invoice_id)
        res["invoice"] = doc
    return jsonify(res), 200


@billing_bp.post("/api/billing/invoice/<invoice_id>/void")
def billing_invoice_void(invoice_id):
    """Admin voids an invoice DOCUMENT (does not touch the debt; its orders can be re-invoiced).
    Gated to a club-finance manager (view_finances = club_admin+)."""
    from auth import resolve_principal
    from iam.permissions import can
    from db import session_scope
    from billing import invoicing
    from sqlalchemy import text

    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return jsonify(error="unauthorized"), 401
    with session_scope() as s:
        head = s.execute(text("SELECT club_id FROM billing.invoice WHERE id = :i"),
                         {"i": str(invoice_id)}).mappings().first()
        if not head:
            return jsonify(error="not_found"), 404
        if not can(p, "view_finances", {"club_id": head["club_id"]}):
            return jsonify(error="forbidden"), 403
        res = invoicing.void_invoice(s, club_id=head["club_id"], invoice_id=invoice_id)
        if not res.get("ok"):
            return jsonify(res), 422
    return jsonify(res), 200


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

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

@billing_bp.post("/api/billing/promo/validate")
def promo_validate():
    """Preview a promo code against an intended purchase (no write). Body: {code, applies_to?,
    amount_minor?, product_id?}. Returns {ok, discount_minor, label} or {ok:false, error, reason}.
    Auth required (a member checking their own basket); scoped to the caller's club."""
    from auth import resolve_principal
    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return jsonify(ok=False, error="unauthorized"), 401
    body = request.get_json(silent=True) or {}
    code = (body.get("code") or "").strip()
    if not code:
        return jsonify(ok=False, error="code required"), 400
    from db import session_scope
    from billing import promotions
    with session_scope() as s:
        res = promotions.validate(
            s, club_id=p.club_id, code=code,
            applies_to=(body.get("applies_to") or "all"),
            amount_minor=int(body.get("amount_minor") or 0),
            product_id=body.get("product_id"), user_id=p.user_id)
    return jsonify(res), (200 if res.get("ok") else 200)  # 200 either way; UI reads ok/reason


@billing_bp.post("/api/billing/promo/apply")
def promo_apply():
    """Apply a promo code to a REAL open order (before payment). Body: {order_id, code}. Returns
    {ok, discount_minor, new_total_minor, label} or {ok:false, error, reason}. The caller must own
    the order OR be club staff."""
    from auth import resolve_principal
    from iam.permissions import can
    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return jsonify(ok=False, error="unauthorized"), 401
    body = request.get_json(silent=True) or {}
    order_id = (body.get("order_id") or "").strip()
    code = (body.get("code") or "").strip()
    if not order_id or not code:
        return jsonify(ok=False, error="order_id and code required"), 400

    from db import session_scope
    from billing import orders as orders_repo, promotions
    with session_scope() as s:
        order = orders_repo.get_order(s, order_id=order_id)
        if not order:
            return jsonify(ok=False, error="order not found"), 404
        # Owner of the order, or club staff (take_pay_at_court gate = club_admin/coach at desk).
        is_owner = (order.get("user_id") and str(order["user_id"]) == str(p.user_id))
        if not is_owner and not can(p, "take_pay_at_court", {"club_id": order["club_id"]}):
            return jsonify(ok=False, error="forbidden"), 403
        res = promotions.apply_to_order(
            s, club_id=order["club_id"], code=code, order_id=order_id,
            user_id=order.get("user_id"), actor_user_id=p.user_id)
    return jsonify(res), 200


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

    # PER-CLIENT TRANSACTIONS. This used to run every club and every client inside ONE session_scope.
    # The sweep allocates GAPLESS invoice numbers and emails them — and emit() dispatches on a
    # background thread with its own session, so the email leaves immediately and does NOT roll back.
    # With gunicorn's 120s timeout and ~900 members, a worker killed at client #400 rolled back 400
    # invoices whose numbered emails had already been delivered, and a re-run allocated different
    # numbers. Committing per client means a failure costs exactly one client; billing.month_end_notice
    # makes the re-run skip everyone already done. Partial progress is now a feature, not corruption.
    #
    # RESUMABLE, TIME-BOXED. Per-client commits make a kill survivable, but gunicorn still reaps the
    # worker at --timeout 120s (render.yaml) — so with a few hundred owing clients the sweep would be
    # cut off mid-way EVERY month and never finish. Rather than bet on one long request, stop
    # cleanly under our own budget and report `remaining`; the caller loops until it hits zero.
    # Because every completed client is committed and claimed in month_end_notice, each pass simply
    # continues where the last stopped. Default 90s keeps a margin under the 120s reaper.
    import time
    t0 = time.time()
    try:
        budget = float(body.get("max_seconds") or request.args.get("max_seconds") or 90)
    except (TypeError, ValueError):
        budget = 90.0
    budget = max(5.0, min(budget, 600.0))
    remaining_clients = 0
    timed_out = False

    results = []
    with session_scope() as s:
        clubs = [club_id] if club_id else [str(r[0]) for r in s.execute(text("SELECT id FROM club.club")).all()]

    for cid in clubs:
        if timed_out:
            break
        stats = {"club_id": cid, "notified": 0, "already": 0, "failed": 0,
                 "rent_charges": 0, "clients_owing": 0, "period": period}
        try:
            with session_scope() as s:          # phase 1+2: accruals + the worklist, one commit
                stats["period"] = comm.month_end_period(s, period)
                stats["rent_charges"] = comm.month_end_accrue(
                    s, club_id=cid, period=stats["period"])
                targets = comm.month_end_targets(s, club_id=cid)
            stats["clients_owing"] = len(targets)
        except Exception:
            log.warning("month-end setup failed for club=%s", cid, exc_info=True)
            results.append(dict(stats, error="setup_failed"))
            continue

        for idx, tgt in enumerate(targets):
            if time.time() - t0 >= budget:
                # Out of budget — stop cleanly rather than being reaped mid-client. Everyone
                # already swept is committed; `remaining` tells the caller to come back.
                timed_out = True
                remaining_clients += len(targets) - idx
                break
            try:
                with session_scope() as s:      # phase 3: ONE client, ONE commit
                    outcome = comm.month_end_client(
                        s, club_id=cid, period=stats["period"], user_id=tgt["user_id"],
                        owed=tgt["owed"], cur=tgt["cur"])
                stats["already" if outcome == "already" else "notified"] += 1
            except Exception:
                # One client's failure must never stop the sweep — and because it committed
                # nothing, the next run picks them up (no month_end_notice row was claimed).
                stats["failed"] += 1
                log.warning("month-end failed for club=%s user=%s", cid, tgt["user_id"],
                            exc_info=True)
        results.append(stats)

    if timed_out:
        # Clubs we never reached at all still owe a pass.
        remaining_clients += sum(1 for c in clubs if not any(r["club_id"] == c for r in results))

    elapsed = round(time.time() - t0, 1)
    total_failed = sum(r.get("failed", 0) for r in results)
    log.info("month-end sweep: %ss failed=%s remaining=%s %s",
             elapsed, total_failed, remaining_clients, results)
    # `ok:false` when anything failed, so the caller (and the Action) can go RED instead of silently
    # reporting success — the sweep runs once a month and a silent no-op is invisible for 30 days.
    # `remaining > 0` is NOT a failure: it means "call me again", and the caller loops.
    return jsonify(ok=(total_failed == 0), clubs=len(results), results=results,
                   failed=total_failed, remaining=remaining_clients,
                   complete=(not timed_out), elapsed_seconds=elapsed), 200

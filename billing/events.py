# billing/events.py — the SINGLE, provider-independent grant/settlement path (docs/05 §3).
#
# This is the heart of the payment abstraction, ported in SHAPE from 1050's
# subscriptions_api.apply_subscription_event: normalize -> sha256 event-hash dedupe ->
# record -> grant. Every provider (Yoco, PayPal, the ManualGateway desk) funnels its
# NormalizedPaymentEvent through apply_payment_event(); adding a gateway never touches this.
#
# Steps (docs/05 §3):
#   1. idempotency: INSERT billing.payment_attempt(event_hash) — if the hash already exists,
#      this is a replay -> return {ignored:True} and do NOTHING (no double payment/grant).
#   2. find the order by event.order_ref (we set it as gateway metadata at checkout).
#   3. record billing.payment (record-only; unique(provider, provider_payment_id) dedupes
#      the money movement a second time, belt-and-braces with the attempt hash).
#   4. dispatch by event.kind:
#        charge_succeeded     -> order.status='paid'; CONFIRM held booking(s); ledger note
#        charge_failed        -> order.status='awaiting_payment' (record-only)
#        refunded             -> record refund payment ONLY (NEVER auto-reverse a booking)
#        subscription_active  -> membership_subscription.status='active' (+ period_end)
#        subscription_cancelled -> membership_subscription.status='cancelled'
#   5. emit payment_succeeded / membership_started (guarded; Agent D's tracking).
#
# THE BILLING <-> DIARY CONTRACT (so Agent B matches):
#   On charge_succeeded, apply_payment_event finds every diary.booking whose id appears in
#   this order's billing.order_line.booking_id and, IF that booking is currently 'held',
#   sets it to 'confirmed' (status-only UPDATE, guarded by club_id). billing does NOT import
#   diary code — it UPDATEs diary.booking by id+club_id (the 'online' held-path: booking
#   held -> pay -> webhook -> apply_payment_event -> confirmed). If diary.booking does not
#   exist yet (B not landed), the UPDATE is wrapped so the payment still records.

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from sqlalchemy import text

from billing.gateway import NormalizedPaymentEvent

log = logging.getLogger("billing.events")


# ---------------------------------------------------------------------------
# Cross-lane tracking (Agent D) — lazy + guarded so billing self-verifies alone.
# The brief names `from marketing_crm.tracking import emit`; the ported module exposes
# `track`. We try emit, then track, then no-op — billing NEVER fails because CRM is absent.
# ---------------------------------------------------------------------------

def _emit(event: str, **payload) -> None:
    try:
        from marketing_crm import tracking as _t
        fn = getattr(_t, "emit", None) or getattr(_t, "track", None)
        if fn is not None:
            fn(event, **payload) if _accepts_kwargs(fn) else fn(event, payload)
    except Exception:
        # Fire-and-forget: tracking must never break settlement.
        pass


def _accepts_kwargs(fn) -> bool:
    try:
        import inspect
        sig = inspect.signature(fn)
        return any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
    except Exception:
        return True


# ---------------------------------------------------------------------------
# The single grant/settlement path
# ---------------------------------------------------------------------------

def apply_payment_event(event: NormalizedPaymentEvent, *, session=None) -> Dict[str, Any]:
    """Provider-independent settlement. Idempotent on event.event_hash().

    `session` optional: if given, the work joins the caller's transaction (the caller
    commits). If None, we open our own db.session_scope() (commits on success). Returns a
    result dict: {ok, ignored?, order_id?, payment_id?, kind, ...}.
    """
    if session is not None:
        return _apply(session, event)

    from db import session_scope
    with session_scope() as s:
        return _apply(s, event)


def _apply(session, event: NormalizedPaymentEvent) -> Dict[str, Any]:
    kind = (event.kind or "").strip().lower()
    ev_hash = event.event_hash()

    # --- 1. idempotency: claim the event_hash; a duplicate = no-op replay ----
    exists = session.execute(
        text("SELECT 1 FROM billing.payment_attempt WHERE event_hash = :h"),
        {"h": ev_hash},
    ).first()
    if exists:
        return {"ok": True, "ignored": True, "reason": "duplicate_event", "event_hash": ev_hash}

    # --- 2. find the order by order_ref --------------------------------------
    order = None
    if event.order_ref:
        order = session.execute(
            text("SELECT id, club_id, user_id, amount_minor, currency_code, settlement_mode, "
                 "status FROM billing.\"order\" WHERE id = :id"),
            {"id": str(event.order_ref)},
        ).mappings().first()
        order = dict(order) if order else None

    club_id = event.club_id or (order["club_id"] if order else None)
    order_id = order["id"] if order else None

    # Record the attempt (claims the hash). Belt: a UNIQUE-violation racing replay is caught.
    session.execute(
        text("""
            INSERT INTO billing.payment_attempt
                (club_id, order_id, provider, intent_id, status, raw_event, event_hash)
            VALUES (:club_id, :order_id, :provider, :intent_id, :status,
                    CAST(:raw AS jsonb), :event_hash)
        """),
        {
            "club_id": str(club_id) if club_id else None,
            "order_id": str(order_id) if order_id else None,
            "provider": event.provider,
            "intent_id": event.provider_payment_id or event.provider_subscription_id,
            "status": event.status,
            "raw": json.dumps(event.raw or {}),
            "event_hash": ev_hash,
        },
    )

    result: Dict[str, Any] = {"ok": True, "kind": kind, "event_hash": ev_hash,
                              "order_id": str(order_id) if order_id else None}

    # --- 4. dispatch by kind -------------------------------------------------
    if kind == "charge_succeeded":
        payment_id = _record_payment(session, event, order, club_id,
                                     direction="charge", status="succeeded")
        if order_id:
            _mark_order(session, order_id, "paid")
            confirmed = _confirm_held_bookings(session, order_id, club_id)
            result["bookings_confirmed"] = confirmed
            # --- commission fan-out (Phase D, owner lane) ----------------------
            # Accrue commission ON COLLECTION for each lesson/class line of the paid order.
            # Savepoint-guarded (like _confirm_held_bookings) so a split failure NEVER blocks
            # settlement, and idempotent on (payment_id, order_line_id, party_type) so a
            # replayed webhook adds NO second split. apply_payment_event's existing
            # record/confirm semantics are untouched — this is a pure fan-out after them.
            split = _accrue_commission(session, club_id, order_id, payment_id)
            if split:
                result["commission"] = split
            # Unified statement: if this order is a 'pay all' settlement vehicle, mark each child
            # order paid + fan out its commission (docs/specs/UNIFIED-STATEMENT.md). Savepoint-guarded
            # + idempotent (only acts on still-'open' children). One debt settled exactly once.
            unified = _settle_unified_statement(session, order_id)
            if unified:
                result["statement_settled"] = unified
        result["payment_recorded"] = True
        # ref_type/ref_id + the order's user_id let the notifications engine resolve the payer
        # (iam.user; child→guardian) and link the receipt notification to /receipt.html?order=<id>.
        _emit("payment_succeeded",
              club_id=str(club_id) if club_id else None,
              order_id=str(order_id) if order_id else None,
              ref_type="order", ref_id=str(order_id) if order_id else None,
              user_id=str(order["user_id"]) if (order and order.get("user_id")) else None,
              amount_minor=event.amount_minor, currency=event.currency,
              provider=event.provider)

    elif kind == "charge_failed":
        _record_payment(session, event, order, club_id, direction="charge", status="failed")
        if order_id and order and order["status"] in ("open", "awaiting_payment"):
            _mark_order(session, order_id, "awaiting_payment")

    elif kind == "refunded":
        # Record-only for the booking. NEVER auto-reverse the booking (docs/05 §8, the 1050 decision).
        refund_pay_id = _record_payment(session, event, order, club_id,
                                        direction="refund", status="refunded")
        if order_id:
            _mark_order(session, order_id, "refunded")
            # Reverse the coach's commission PROPORTIONALLY so the club doesn't eat the coach's
            # share of a refunded lesson (and the coach sees the refund). Savepoint-guarded +
            # idempotent, exactly like the charge fan-out — never blocks the refund record.
            clawback = _accrue_refund_clawback(session, event, club_id, order_id, refund_pay_id)
            if clawback:
                result["commission_clawback"] = clawback
        result["payment_recorded"] = True
        result["note"] = "refund recorded; booking NOT auto-reversed"
        _emit("payment_refunded",
              club_id=str(club_id) if club_id else None,
              order_id=str(order_id) if order_id else None,
              ref_type="order", ref_id=str(order_id) if order_id else None,
              user_id=str(order["user_id"]) if (order and order.get("user_id")) else None,
              amount_minor=event.amount_minor, currency=event.currency,
              provider=event.provider)

    elif kind == "subscription_active":
        sub_id = _upsert_membership(session, event, club_id, status="active")
        result["membership_subscription_id"] = sub_id
        _emit("membership_started",
              club_id=str(club_id) if club_id else None,
              user_id=event.user_id, provider=event.provider,
              provider_subscription_id=event.provider_subscription_id)

    elif kind == "subscription_cancelled":
        sub_id = _upsert_membership(session, event, club_id, status="cancelled")
        result["membership_subscription_id"] = sub_id

    else:
        result["note"] = f"unhandled kind '{kind}' (recorded as attempt only)"

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record_payment(session, event: NormalizedPaymentEvent, order, club_id, *,
                    direction: str, status: str) -> Optional[str]:
    """Insert billing.payment (record-only). Idempotent on (provider, provider_payment_id):
    if the money movement was already recorded (a second webhook for the same charge),
    ON CONFLICT DO NOTHING keeps it single. Returns the payment id or None."""
    currency = event.currency or (order["currency_code"] if order else None) or "ZAR"
    row = session.execute(
        text("""
            INSERT INTO billing.payment
                (club_id, order_id, provider, provider_payment_id, amount_minor,
                 currency_code, direction, status)
            VALUES (:club_id, :order_id, :provider, :ppid, :amount, :currency, :direction, :status)
            ON CONFLICT (provider, provider_payment_id)
                WHERE provider_payment_id IS NOT NULL
            DO NOTHING
            RETURNING id
        """),
        {
            "club_id": str(club_id) if club_id else None,
            "order_id": str(order["id"]) if order else None,
            "provider": event.provider,
            "ppid": event.provider_payment_id,
            "amount": int(event.amount_minor or 0),
            "currency": currency,
            "direction": direction,
            "status": status,
        },
    ).mappings().first()
    return str(row["id"]) if row else None


def _accrue_commission(session, club_id, order_id, payment_id):
    """Commission fan-out after a successful charge (Phase D owner lane). Resolves the
    payment row if `_record_payment` returned None (a second delivery dedup'd on
    (provider, provider_payment_id) — we still want the split keyed to the real payment),
    then calls billing.commission.record_split_for_order. SAVEPOINT-guarded so a failure
    (e.g. commission tables not present in an isolated billing self-test) NEVER blocks the
    payment/order commit, and idempotent on the split's unique key so a replay is a no-op.
    Returns the engine result dict or None."""
    try:
        with session.begin_nested():
            pid = payment_id
            if pid is None and order_id:
                pid = session.execute(
                    text("SELECT id FROM billing.payment WHERE order_id = :o "
                         "AND direction = 'charge' AND status = 'succeeded' "
                         "ORDER BY created_at DESC LIMIT 1"),
                    {"o": str(order_id)},
                ).scalar()
            from billing import commission as _commission
            return _commission.record_split_for_order(
                session, club_id=club_id, order_id=order_id, payment_id=pid)
    except Exception:
        log.info("commission fan-out skipped (engine/tables unavailable) order=%s", order_id)
        return None


def _accrue_refund_clawback(session, event, club_id, order_id, refund_payment_id):
    """Proportional coach-commission clawback on a refund (the mirror of _accrue_commission).
    Resolves the refund payment id if `_record_payment` dedup'd it (a replayed refund webhook),
    keying idempotently by the event's provider_payment_id. SAVEPOINT-guarded so a failure never
    blocks the refund record, and idempotent on the refund payment. Returns the engine result or
    None."""
    try:
        with session.begin_nested():
            pid = refund_payment_id
            if pid is None and order_id:
                pid = session.execute(
                    text("SELECT id FROM billing.payment WHERE order_id = :o "
                         "AND direction = 'refund' AND status = 'refunded' "
                         "AND (CAST(:ppid AS text) IS NULL OR provider_payment_id = :ppid) "
                         "ORDER BY created_at DESC LIMIT 1"),
                    {"o": str(order_id), "ppid": event.provider_payment_id},
                ).scalar()
            from billing import commission as _commission
            return _commission.record_refund_clawback(
                session, club_id=club_id, order_id=order_id,
                refund_payment_id=pid, refund_minor=int(event.amount_minor or 0))
    except Exception:
        log.info("refund clawback skipped (engine/tables unavailable) order=%s", order_id)
        return None


def _settle_unified_statement(session, order_id):
    """If `order_id` is a 'pay all' settlement order, mark its child orders paid + fan out each child's
    commission. SAVEPOINT-guarded + idempotent (only still-'open' children). Returns the result dict or
    None when this isn't a settlement order / the engine isn't present."""
    try:
        with session.begin_nested():
            from billing import statement as _statement
            if not _statement.is_settlement_order(session, order_id=order_id):
                return None
            return _statement.settle_settlement_order(session, settlement_order_id=order_id)
    except Exception:
        log.info("unified statement settlement skipped order=%s", order_id)
        return None


def _mark_order(session, order_id, status: str) -> None:
    session.execute(
        text('UPDATE billing."order" SET status = :s, updated_at = now() WHERE id = :id'),
        {"s": status, "id": str(order_id)},
    )


def _confirm_held_bookings(session, order_id, club_id) -> int:
    """THE billing->diary contract. Confirm every diary.booking linked to this order's
    lines that is currently 'held' (the online held-path: held -> paid -> confirmed).
    Status-only UPDATE, scoped by club_id. Returns count confirmed.

    Wrapped in a SAVEPOINT (session.begin_nested) so that if diary.* does not exist yet
    (Agent B not landed) the failed UPDATE rolls back ONLY this savepoint — it must NOT
    poison the outer transaction (which still has to commit the payment + order). This is
    what lets billing self-verify in isolation."""
    try:
        with session.begin_nested():
            res = session.execute(
                text("""
                    UPDATE diary.booking b
                    SET status = 'confirmed', updated_at = now()
                    FROM billing.order_line ol
                    WHERE ol.order_id = :order_id
                      AND ol.booking_id = b.id
                      AND b.club_id = :club_id
                      AND b.status = 'held'
                """),
                {"order_id": str(order_id), "club_id": str(club_id) if club_id else None},
            )
            return res.rowcount or 0
    except Exception:
        # diary.* not present yet (B not landed) — savepoint rolled back; payment still
        # records on the outer txn. Log + continue.
        log.info("confirm_held_bookings skipped (diary.booking unavailable) order=%s", order_id)
        return 0


def _upsert_membership(session, event: NormalizedPaymentEvent, club_id, *, status: str) -> Optional[str]:
    """Activate / cancel a recurring membership from a subscription event. Idempotent on
    (provider, provider_subscription_id) — the unique index in schema.py."""
    if not event.provider_subscription_id:
        # No provider sub id (e.g. manual membership) — insert a fresh active row.
        row = session.execute(
            text("""
                INSERT INTO billing.membership_subscription
                    (club_id, user_id, price_id, status, provider, current_period_end)
                VALUES (:club_id, :user_id, :price_id, :status, :provider, :period_end)
                RETURNING id
            """),
            {"club_id": str(club_id) if club_id else None,
             "user_id": event.user_id, "price_id": event.price_id,
             "status": status, "provider": event.provider,
             "period_end": event.current_period_end},
        ).mappings().first()
        return str(row["id"]) if row else None

    row = session.execute(
        text("""
            INSERT INTO billing.membership_subscription
                (club_id, user_id, price_id, status, provider,
                 provider_subscription_id, current_period_end)
            VALUES (:club_id, :user_id, :price_id, :status, :provider, :psid, :period_end)
            ON CONFLICT (provider, provider_subscription_id)
                WHERE provider_subscription_id IS NOT NULL
            DO UPDATE SET status = EXCLUDED.status,
                          current_period_end = COALESCE(EXCLUDED.current_period_end,
                                               billing.membership_subscription.current_period_end),
                          updated_at = now()
            RETURNING id
        """),
        {"club_id": str(club_id) if club_id else None,
         "user_id": event.user_id, "price_id": event.price_id,
         "status": status, "provider": event.provider,
         "psid": event.provider_subscription_id,
         "period_end": event.current_period_end},
    ).mappings().first()
    return str(row["id"]) if row else None

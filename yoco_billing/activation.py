# yoco_billing/activation.py — idempotent post-payment activation shared by the webhook + reconcile.
#
# WHY: an online purchase can settle via TWO paths — the Yoco WEBHOOK or RECONCILE (missed-webhook
# recovery, common on Render Free where the app sleeps). BOTH must also activate the linked
# membership / pack and emit the pack-activated notification. This helper is the ONE place that does
# it, so the two paths can't drift. (The reconcile path historically did NEITHER → a paid order with
# a dead PENDING pack and no confirmation email — the "shows paid, no email, pack unusable" bug.)
#
# Idempotent: activation only grants a PENDING wallet / inactive membership, so calling it twice — or
# the real webhook landing AFTER reconcile already recovered the payment — is a safe no-op that still
# REPAIRS a previously-un-activated purchase (it no longer depends on the event being "fresh"; the
# earlier code gated activation on `not ignored`, which the idempotency hash made permanent). The
# bundle_activated email fires only on the FIRST grant (status == 'granted').

from __future__ import annotations

import logging

from sqlalchemy import text

log = logging.getLogger("yoco_billing.activation")


def activate_purchase(session, *, order_id, club_id=None) -> dict:
    """Activate the membership/pack linked to a PAID order + emit bundle_activated on first grant.
    Safe to call from the webhook AND reconcile, and more than once. Returns {membership?, bundle?}.

    Activation calls may raise (a real DB error) so the caller can react — the webhook wraps this and
    returns 500 so Yoco RETRIES; reconcile guards it so one bad order doesn't stop the sweep. Only the
    emit is best-effort/guarded (a notification hiccup must never break settlement)."""
    out: dict = {}
    if not order_id:
        return out
    row = session.execute(
        text('SELECT status, club_id FROM billing."order" WHERE id = :o'),
        {"o": str(order_id)},
    ).mappings().first()
    if not row or row["status"] != "paid":
        return out                       # only activate against a genuinely settled order
    club_id = club_id or row["club_id"]

    from billing import membership as membership_repo, bundles as bundles_repo

    if membership_repo.is_membership_order(session, order_id=order_id):
        out["membership"] = membership_repo.activate_membership_for_order(
            session, order_id=order_id, provider="yoco")

    if bundles_repo.is_bundle_order(session, order_id=order_id):
        b = bundles_repo.activate_wallet_for_order(session, order_id=order_id, provider="yoco")
        out["bundle"] = b
        if b and b.get("status") == "granted":
            try:
                from marketing_crm.tracking import emit
                emit("bundle_activated", {
                    "club_id": str(club_id) if club_id else None,
                    "user_id": b.get("user_id"),
                    "ref_type": "order", "ref_id": str(order_id),
                    "label": b.get("label"),
                    "tokens_total": b.get("tokens_total"),
                })
            except Exception:
                log.debug("bundle_activated emit skipped (tracking unavailable)")
    return out

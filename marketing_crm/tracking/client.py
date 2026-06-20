# marketing_crm/tracking/client.py — the emit() implementation.
#
# Ported from 1050 marketing_crm/tracking/client.py (track()), reshaped to the LOCKED booking-domain
# signature emit(event, payload) that Agents B (diary) and C (billing) code against.
#
# Guarantees (the contract — do not weaken):
#   - NEVER raises. A telemetry failure must never break a booking/payment.
#   - NEVER blocks the caller — the work runs on a daemon thread.
#   - Always writes core.usage_event (our SoR). Then best-effort forwards to Klaviyo (gated).
#   - Off-key Klaviyo / no DB is a clean no-op, not an exception.
#
# payload carries club_id + the adult-contact email + non-PII booking details (contracts/events.md).
# We resolve account/user from the email best-effort; if unresolved we keep the email in metadata so
# the event can be linked later. We NEVER duplicate the email into metadata once it IS linked.

import logging
import threading

log = logging.getLogger("marketing_crm.tracking")

# Payload keys that are control/linkage, not free-form metadata. Stripped out of the JSONB blob.
_RESERVED = {"club_id", "email", "account_id", "user_id", "person_id", "ref_type", "ref_id"}


def emit(event, payload=None):
    """Record a booking-domain product event. Fire-and-forget — safe from any request handler,
    cron, or service path. Writes core.usage_event then forwards to Klaviyo (transactional always,
    marketing gated on opt-in). Never raises, never blocks.

    Args:
        event:   the canonical event name (use marketing_crm.tracking.events constants).
        payload: dict carrying `club_id`, adult-contact `email`, and non-PII details
                 (see contracts/events.md). May also carry `ref_type`/`ref_id` linkage and
                 pre-resolved `account_id`/`user_id`/`person_id`.
    """
    payload = dict(payload or {})
    try:
        threading.Thread(target=_emit, args=(event, payload), daemon=True).start()
    except Exception:
        log.exception("emit: failed to spawn thread for %s", event)


def _emit(event, payload):
    club_id = payload.get("club_id")
    email = (payload.get("email") or "").strip().lower() or None
    account_id = payload.get("account_id")
    user_id = payload.get("user_id")
    person_id = payload.get("person_id")
    ref_type = payload.get("ref_type")
    ref_id = payload.get("ref_id")
    # Everything not reserved becomes non-PII event metadata.
    meta = {k: v for k, v in payload.items() if k not in _RESERVED}

    # 1) Durable: core.usage_event (the SoR). Resolve account/user by email best-effort.
    try:
        from db import session_scope
        from core.repositories import accounts, usage_events
        with session_scope() as s:
            if email:
                if account_id is None:
                    a = accounts.get_account_by_email(s, email)
                    if a:
                        account_id = a.id
                if user_id is None:
                    u = accounts.get_user_by_email(s, email)
                    if u:
                        user_id = u.id
            # Keep the email so a pre-backfill event can be linked later; once linked, don't
            # duplicate PII into the blob.
            if account_id is None and email:
                meta["email_unmatched"] = email
            usage_events.record_usage(
                s, event_type=event, club_id=club_id, account_id=account_id, user_id=user_id,
                person_id=person_id, ref_type=ref_type, ref_id=ref_id, metadata=(meta or None),
            )
    except Exception:
        log.exception("emit: usage_event write failed for %s", event)

    # 2) Best-effort: forward to Klaviyo so flows can trigger. Self-gates on KLAVIYO_API_KEY,
    #    enforces the transactional-vs-marketing rule, and tags the profile with the club trait.
    try:
        from marketing_crm.crm_sync import forward_event
        forward_event(event, email, club_id=club_id, properties=meta)
    except Exception:
        log.exception("emit: crm forward failed for %s", event)

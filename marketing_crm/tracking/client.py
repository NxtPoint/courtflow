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


def _core_id(v):
    """core.* identity (account/app_user/person) is BIGINT. Producers (diary/billing) speak the
    platform's iam.user UUID — a DIFFERENT identity space, bridged by email. Accept a value as a
    core id only if it's int-like; a UUID is NOT a core id (it's handled via email + metadata)."""
    if v is None:
        return None
    if isinstance(v, int):
        return v
    s = str(v)
    return int(s) if s.isdigit() else None


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
    club_id = payload.get("club_id")               # UUID — usage_event.club_id is UUID (matches)
    email = (payload.get("email") or "").strip().lower() or None
    # core ids are bigints; producers pass iam UUIDs. Only int-like values are real core ids.
    account_id = _core_id(payload.get("account_id"))
    user_id = _core_id(payload.get("user_id"))
    person_id = _core_id(payload.get("person_id"))
    ref_type = payload.get("ref_type")
    ref_id = payload.get("ref_id")
    # Everything not reserved becomes non-PII event metadata.
    meta = {k: v for k, v in payload.items() if k not in _RESERVED}
    # Preserve the platform (iam.user) UUID for later linkage — it's not a core bigint id.
    iam_user_id = payload.get("user_id")
    if iam_user_id is not None and user_id is None:
        meta["iam_user_id"] = str(iam_user_id)

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

    # 1b) Best-effort: notifications / communications engine (the in-app inbox + a transactional
    #     email fallback). Driven off THIS event stream — for a mapped set of transactional kinds
    #     (notifications.KIND_MAP) we resolve the recipient (iam.user; child→guardian), render a
    #     template, ALWAYS write a core.notification row, and attempt an email (SES; 'skipped' with
    #     no keys). Wrapped + on its OWN session so a delivery failure NEVER affects the producer's
    #     booking/payment path (it already committed its usage_event above; the caller is on a
    #     different thread). Non-fatal above all.
    try:
        from marketing_crm import notifications
        if event in notifications.KIND_MAP:
            from db import session_scope
            with session_scope() as s2:
                notifications.deliver_for_event(s2, event, payload)
    except Exception:
        log.exception("emit: notification delivery failed for %s (non-fatal)", event)

    # 2) Best-effort: forward to Klaviyo so flows can trigger. Self-gates on KLAVIYO_API_KEY,
    #    enforces the transactional-vs-marketing rule, and tags the profile with the club trait.
    try:
        from marketing_crm.crm_sync import forward_event
        forward_event(event, email, club_id=club_id, properties=meta)
    except Exception:
        log.exception("emit: crm forward failed for %s", event)

    # 3) Best-effort: ledger a Google Ads offline conversion if THIS money-event's buyer arrived via a
    #    gclid'd ad click (closes the loop on the core.acquisition capture). Own session; a no-op for
    #    non-conversion events and for organic buyers. NEVER affects the payment path (separate thread,
    #    already-committed usage_event above). Shared, portable — ten-fifty5 wires the identical hook.
    try:
        from offline_conversions.recorder import record_from_emit
        from db import session_scope
        with session_scope() as s3:
            record_from_emit(s3, event, payload)
    except Exception:
        log.exception("emit: offline-conversion ledger failed for %s (non-fatal)", event)

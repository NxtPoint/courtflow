# marketing_crm.tracking — booking-domain event instrumentation (Agent D lane).
#
# emit(event, payload) is the SINGLE, LOCKED entry point that Agents B (diary) and C (billing) call.
# Fire-and-forget: it NEVER raises and NEVER blocks the request (work happens on a daemon thread).
# It writes core.usage_event (always, our SoR) then forwards to Klaviyo (transactional always,
# marketing gated on opt-in; off-key Klaviyo is a clean no-op). Event names come from
# contracts/events.md (see events.py constants).
#
# LOCKED SIGNATURE — do not change without updating contracts/events.md and notifying B & C:
#     emit(event: str, payload: dict) -> None
#
# Usage:
#     from marketing_crm.tracking import emit
#     from marketing_crm.tracking.events import BOOKING_CONFIRMED
#     emit(BOOKING_CONFIRMED, {"club_id": club_id, "email": adult_email, "resource_name": "Court 1",
#                              "starts_at": iso, "ends_at": iso, "ref_type": "booking", "ref_id": bid})

from marketing_crm.tracking.client import emit  # noqa: F401
from marketing_crm.tracking.events import EVENTS, TRANSACTIONAL_EVENTS, is_transactional  # noqa: F401
from marketing_crm.tracking.beacon import page_bp, register as register_beacon  # noqa: F401

__all__ = ["emit", "EVENTS", "TRANSACTIONAL_EVENTS", "is_transactional",
           "page_bp", "register_beacon"]

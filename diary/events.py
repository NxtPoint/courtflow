# diary/events.py — guarded emit to the CRM/event bus (Agent D's lane).
#
# The diary must never hard-depend on marketing_crm being present (it ships in its own
# lane, and this lane self-verifies in isolation). So every emit is lazy + try/except:
# if marketing_crm.tracking isn't importable, or emit() raises, we swallow it (events are
# fire-and-forget; a booking must never fail because Klaviyo/CRM is down — exactly 1050's
# discipline).
#
# Cross-lane contract we ASSUME (Agent D matches this signature):
#     marketing_crm.tracking.emit(event: str, payload: dict) -> None
#
# Canonical diary events (docs/06 §2): booking_confirmed, booking_cancelled,
# booking_rescheduled, booking_reminder, class_enrolled, class_waitlisted,
# waitlist_slot_open, lesson_completed.
#
# Every payload carries club_id + user_id/email (the ADULT contact) and non-PII booking
# detail (resource name, time, price). Callers are responsible for not putting minor PII
# in the payload (docs/06 §5).

import logging

log = logging.getLogger("diary.events")


def emit(event, payload):
    """Fire-and-forget emit to the CRM event bus. Never raises. No-op (logged at debug)
    if Agent D's marketing_crm.tracking isn't present yet."""
    try:
        from marketing_crm.tracking import emit as _emit  # lazy: Agent D's lane
    except Exception:
        log.debug("diary event %s suppressed — marketing_crm.tracking not present", event)
        return False
    try:
        _emit(event, payload)
        return True
    except Exception:
        log.warning("diary event %s emit failed (ignored)", event, exc_info=False)
        return False

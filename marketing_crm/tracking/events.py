# marketing_crm/tracking/events.py — canonical event-name constants (mirror contracts/events.md).
#
# Use these constants, never string literals, so the booking-domain taxonomy stays single-sourced.
# B (diary) and C (billing) import EVENTS / the constants from here so producer & consumer match.

# Lifecycle / identity
ACCOUNT_CREATED = "account_created"
CONSENT_RECORDED = "consent_recorded"

# Bookings (court / lesson)
BOOKING_CONFIRMED = "booking_confirmed"
BOOKING_CANCELLED = "booking_cancelled"
BOOKING_RESCHEDULED = "booking_rescheduled"
BOOKING_REMINDER = "booking_reminder"

# Classes / waitlist
CLASS_ENROLLED = "class_enrolled"
CLASS_WAITLISTED = "class_waitlisted"
WAITLIST_SLOT_OPEN = "waitlist_slot_open"

# Lessons / coaching
LESSON_COMPLETED = "lesson_completed"

# Billing
PAYMENT_SUCCEEDED = "payment_succeeded"
MONTHLY_STATEMENT_READY = "monthly_statement_ready"
MEMBERSHIP_STARTED = "membership_started"
MEMBERSHIP_LAPSED = "membership_lapsed"

# Funnel / feedback
FREE_LESSON_REQUESTED = "free_lesson_requested"
NPS_SUBMITTED = "nps_submitted"
FEEDBACK_SUBMITTED = "feedback_submitted"

# Anonymous (beacon only — never forwarded to Klaviyo)
PAGE_VIEW = "page_view"

# The canonical set of named booking-domain events (page_view excluded — it is infra, not a
# lifecycle event). emit() does NOT reject unknown names (forward-compat: a new producer can add
# one), but EVENTS is the authoritative list and contracts/events.md must be updated in lockstep.
EVENTS = {
    ACCOUNT_CREATED, CONSENT_RECORDED,
    BOOKING_CONFIRMED, BOOKING_CANCELLED, BOOKING_RESCHEDULED, BOOKING_REMINDER,
    CLASS_ENROLLED, CLASS_WAITLISTED, WAITLIST_SLOT_OPEN,
    LESSON_COMPLETED,
    PAYMENT_SUCCEEDED, MONTHLY_STATEMENT_READY, MEMBERSHIP_STARTED, MEMBERSHIP_LAPSED,
    FREE_LESSON_REQUESTED, NPS_SUBMITTED, FEEDBACK_SUBMITTED,
}

# Transactional events ALWAYS forward to Klaviyo (regardless of marketing_opt_in) — legitimate
# booking comms (docs/06 §4). Everything else is marketing-gated (consent_recorded is a state
# update, never a send). This set is the gate crm_sync.forward_event consults.
TRANSACTIONAL_EVENTS = {
    BOOKING_CONFIRMED, BOOKING_CANCELLED, BOOKING_RESCHEDULED, BOOKING_REMINDER,
    CLASS_ENROLLED, CLASS_WAITLISTED, WAITLIST_SLOT_OPEN,
    PAYMENT_SUCCEEDED, MONTHLY_STATEMENT_READY,
}


def is_transactional(event_type):
    """True if the event must send regardless of marketing consent (docs/06 §4)."""
    return event_type in TRANSACTIONAL_EVENTS

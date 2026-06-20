# marketing_crm — CRM / Klaviyo / events lane (Agent D).
#
# We are our own CRM (core.* + the cockpit); Klaviyo is the marketing/lifecycle + confirmation engine.
# Subpackages:
#   tracking/  — emit(event, payload): the LOCKED producer interface B & C call (→ core.usage_event
#                → Klaviyo); the page-view beacon.
#   crm_sync/  — Klaviyo profile upsert + event forward (self-gates on KLAVIYO_API_KEY; transactional
#                always, marketing gated on opt-in; `club` trait for per-club segmentation). HubSpot dormant.
#   consent/   — HTTP capture/withdraw/state over core.consent (marketing opt-in + parental/minor path).
#   backoffice/— the club-admin cockpit (live core.* views; diary/billing views stubbed pending B/C).
#   email/     — SES fallback for the booking-confirmation send (self-gates on AWS creds).
#
# The canonical event taxonomy is contracts/events.md.

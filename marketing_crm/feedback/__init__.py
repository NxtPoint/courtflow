# marketing_crm/feedback — the gated review / feedback engine.
#
# A tokened, no-login feedback capture: an email (post-lesson, or a review-ask campaign) links to
# /feedback?t=<signed-token>&score=<1-5>. Happy raters (4-5★) are routed to the club's Google review
# page (grows the Google Business Profile → local map-pack reach — MARKETING-ENGINE.md §6); unhappy
# raters (1-3★) land in a private "how can we improve?" form. Every rating writes core.nps_response
# (→ admin NPS panel + Client-360) and emits nps_submitted / feedback_submitted (→ Klaviyo, gated).
#
# Public API of this package:
#   feedback_url_for(iam_user_id, club_id, context)  -> an absolute, signed /feedback URL (or None)
#   REVIEW_URL                                        -> the club's Google review link (env-overridable)
# The blueprint (feedback_bp) + token signer + record service live in the sibling modules.

from marketing_crm.feedback.tokens import feedback_url_for, review_url, REVIEW_URL  # noqa: F401

# marketing_crm/feedback/tokens.py — feedback-link helpers over the shared signer.
#
# The generic signed-token primitives (mint/verify) now live in marketing_crm.signing (shared with the
# re-permission opt-in). This module keeps the feedback-specific bits: the review URL + the /feedback
# link builder. Public API unchanged (feedback_url_for, review_url, REVIEW_URL, mint, verify).

import logging
import os

from marketing_crm.signing import mint, verify, base_url  # noqa: F401  (re-exported for callers)

log = logging.getLogger("marketing_crm.feedback.tokens")

_DEFAULT_REVIEW_URL = "https://g.page/r/Ce9nBEAMXHTpEBM/review"  # NextPoint Tennis Google review shortlink


def review_url() -> str:
    """The club's Google review link (env-overridable so a new club just sets its own)."""
    return (os.getenv("GOOGLE_REVIEW_URL") or _DEFAULT_REVIEW_URL).strip()


# Module-level convenience (read at import; env is stable within a process).
REVIEW_URL = review_url()


def feedback_url_for(iam_user_id, club_id, context="feedback", *, ttl_days=30):
    """Absolute, signed /feedback URL for a recipient — put this in the email CTA (Klaviyo reads it as
    an event property). Returns None if there's no user to key the token to. Never raises."""
    try:
        if not iam_user_id:
            return None
        tok = mint(iam_user_id, club_id, context=context, ttl_days=ttl_days)
        return f"{base_url()}/feedback?t={tok}"
    except Exception:
        log.exception("feedback_url_for failed")
        return None

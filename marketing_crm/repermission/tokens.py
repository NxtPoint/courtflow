# marketing_crm/repermission/tokens.py — the /subscribe opt-in link builder (over the shared signer).

import logging

from marketing_crm.signing import mint, base_url

log = logging.getLogger("marketing_crm.repermission.tokens")

CONTEXT = "optin"


def optin_url_for(iam_user_id, club_id, *, ttl_days=60):
    """Absolute, signed /subscribe URL for a recipient — the re-permission email CTA links here.
    60-day TTL (a one-off notice may sit unread a while). Returns None without a user. Never raises."""
    try:
        if not iam_user_id:
            return None
        tok = mint(iam_user_id, club_id, context=CONTEXT, ttl_days=ttl_days)
        return f"{base_url()}/subscribe?t={tok}"
    except Exception:
        log.exception("optin_url_for failed")
        return None

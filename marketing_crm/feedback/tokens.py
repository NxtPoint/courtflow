# marketing_crm/feedback/tokens.py — stateless, HMAC-signed feedback tokens (no DB row, no PII in URL).
#
# A token carries only the linkage needed to record feedback for one recipient: the iam.user id, the
# club id, a context tag, a per-request nonce (so one email = one upsertable response), and an expiry.
# It is signed with HMAC-SHA256 so it can't be forged. The email/name are NEVER in the URL — they are
# resolved server-side from the iam.user id at redemption.
#
# Secret resolution (no NEW required config): FEEDBACK_SECRET -> OPS_KEY -> a dev fallback (warned once).
# In production OPS_KEY is set, so tokens are signed with a real secret without any extra env work.

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time

log = logging.getLogger("marketing_crm.feedback.tokens")

_DEFAULT_REVIEW_URL = "https://g.page/r/Ce9nBEAMXHTpEBM/review"  # NextPoint Tennis Google review shortlink
_DEV_SECRET_WARNED = False


def _secret() -> bytes:
    global _DEV_SECRET_WARNED
    s = (os.getenv("FEEDBACK_SECRET") or os.getenv("OPS_KEY") or "").strip()
    if not s:
        if not _DEV_SECRET_WARNED:
            log.warning("feedback tokens: no FEEDBACK_SECRET/OPS_KEY set — using an INSECURE dev secret")
            _DEV_SECRET_WARNED = True
        s = "cf-feedback-dev-secret"
    return s.encode("utf-8")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(txt: str) -> bytes:
    pad = "=" * (-len(txt) % 4)
    return base64.urlsafe_b64decode(txt + pad)


def _sign(body_b64: str) -> str:
    return _b64e(hmac.new(_secret(), body_b64.encode("ascii"), hashlib.sha256).digest())


def mint(iam_user_id, club_id, *, context="feedback", ttl_days=30) -> str:
    """Return a signed token for a recipient. `iam_user_id` + `club_id` are UUID strings."""
    payload = {
        "u": str(iam_user_id),
        "c": str(club_id) if club_id else None,
        "x": context,
        "j": secrets.token_urlsafe(6),          # per-request nonce → one upsertable nps_response
        "e": int(time.time()) + ttl_days * 86400,
    }
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_sign(body)}"


def verify(token: str):
    """Return the token payload dict if the signature is valid and it hasn't expired, else None."""
    try:
        body, sig = str(token).split(".", 1)
    except (ValueError, AttributeError):
        return None
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        payload = json.loads(_b64d(body).decode("utf-8"))
    except Exception:
        return None
    if not payload.get("u") or int(payload.get("e") or 0) < int(time.time()):
        return None
    return payload


def review_url() -> str:
    """The club's Google review link (env-overridable so a new club just sets its own)."""
    return (os.getenv("GOOGLE_REVIEW_URL") or _DEFAULT_REVIEW_URL).strip()


# Module-level convenience (read at import; env is stable within a process).
REVIEW_URL = review_url()


def _base_url() -> str:
    """Where the /feedback page is served — the public portal (never-sleeps courtflow-web)."""
    return (os.getenv("FEEDBACK_BASE_URL")
            or os.getenv("PUBLIC_APP_URL")
            or os.getenv("APP_BASE_URL")
            or "https://nextpointtennis.com").strip().rstrip("/")


def feedback_url_for(iam_user_id, club_id, context="feedback", *, ttl_days=30):
    """Absolute, signed /feedback URL for a recipient — put this in the email CTA (Klaviyo reads it as
    an event property). Returns None if there's no user to key the token to. Never raises."""
    try:
        if not iam_user_id:
            return None
        tok = mint(iam_user_id, club_id, context=context, ttl_days=ttl_days)
        return f"{_base_url()}/feedback?t={tok}"
    except Exception:
        log.exception("feedback_url_for failed")
        return None

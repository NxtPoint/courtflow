# marketing_crm/signing.py — the ONE signer for tokened, no-login email links.
#
# A signed link carries only linkage (iam.user id + club id + a context tag + a per-request nonce +
# an expiry) — never PII. HMAC-SHA256 makes it unforgeable; the recipient's email/name are resolved
# server-side from the iam.user id at redemption. Used by BOTH the feedback engine (/feedback) and the
# re-permission opt-in (/subscribe) — same format, disambiguated by the `context` field.
#
# Secret (no NEW required config): FEEDBACK_SECRET -> OPS_KEY -> a dev fallback (warned once). In prod
# OPS_KEY is set, so links sign with a real secret without any extra env work.

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time

log = logging.getLogger("marketing_crm.signing")

_DEV_SECRET_WARNED = False


def _secret() -> bytes:
    global _DEV_SECRET_WARNED
    s = (os.getenv("FEEDBACK_SECRET") or os.getenv("OPS_KEY") or "").strip()
    if not s:
        if not _DEV_SECRET_WARNED:
            log.warning("signing: no FEEDBACK_SECRET/OPS_KEY set — using an INSECURE dev secret")
            _DEV_SECRET_WARNED = True
        s = "cf-link-dev-secret"
    return s.encode("utf-8")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(txt: str) -> bytes:
    pad = "=" * (-len(txt) % 4)
    return base64.urlsafe_b64decode(txt + pad)


def _sign(body_b64: str) -> str:
    return _b64e(hmac.new(_secret(), body_b64.encode("ascii"), hashlib.sha256).digest())


def mint(iam_user_id, club_id, *, context="link", ttl_days=30) -> str:
    """Return a signed token for a recipient. `iam_user_id` + `club_id` are UUID strings."""
    payload = {
        "u": str(iam_user_id),
        "c": str(club_id) if club_id else None,
        "x": context,
        "j": secrets.token_urlsafe(6),          # per-request nonce (dedupe / one-response-per-link)
        "e": int(time.time()) + ttl_days * 86400,
    }
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_sign(body)}"


def verify(token, *, context=None):
    """Return the token payload if the signature is valid and unexpired, else None. If `context` is
    given, the token's context MUST match (so a /feedback token can't be replayed at /subscribe)."""
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
    if context is not None and payload.get("x") != context:
        return None
    return payload


def base_url() -> str:
    """Public base for the no-login pages (the never-sleeps courtflow-web portal)."""
    return (os.getenv("FEEDBACK_BASE_URL")
            or os.getenv("PUBLIC_APP_URL")
            or os.getenv("APP_BASE_URL")
            or "https://nextpointtennis.com").strip().rstrip("/")

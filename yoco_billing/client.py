# yoco_billing/client.py — thin Yoco REST client + webhook signature verification.
#
# NO business logic here (that's adapter.py); this is just HTTP + crypto against Yoco's
# live API (developer.yoco.com, confirmed June 2026):
#   - create checkout : POST https://payments.yoco.com/api/checkouts          (Bearer secret key)
#   - refund          : POST https://payments.yoco.com/api/checkouts/{id}/refund
#   - webhooks        : Standard Webhooks (svix) signing — headers webhook-id /
#                       webhook-timestamp / webhook-signature; signed content
#                       "{id}.{timestamp}.{raw_body}"; key = base64-decode(secret minus the
#                       "whsec_" prefix); expected = base64(HMAC_SHA256(key, signed_content));
#                       the header is space-separated "v1,<b64sig>" items, compared constant-time.
#
# Amounts are integer MINOR units (ZAR cents), matching billing.*. All env is read lazily so
# the module imports clean with no keys set (app.py boot discipline).

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Any, Dict, Optional

import requests

YOCO_API_BASE = "https://payments.yoco.com"
_TIMEOUT_SECONDS = 20
# Replay window for the webhook-timestamp (Yoco recommends ~3 min; svix default is 5 min).
WEBHOOK_TOLERANCE_SECONDS = 300


class YocoError(Exception):
    """Raised on any non-2xx / network failure talking to Yoco."""

    def __init__(self, status: int, message: str, body: Any = None):
        super().__init__(f"yoco {status}: {message}")
        self.status = status
        self.message = message
        self.body = body


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------

def _secret_key() -> str:
    k = (os.getenv("YOCO_SECRET_KEY") or "").strip()
    if not k:
        raise YocoError(0, "YOCO_SECRET_KEY not configured")
    return k


def _headers(idempotency_key: Optional[str] = None) -> Dict[str, str]:
    h = {
        "Authorization": f"Bearer {_secret_key()}",
        "Content-Type": "application/json",
    }
    if idempotency_key:
        # Yoco honours an Idempotency-Key header so a retried create/refund is safe.
        h["Idempotency-Key"] = str(idempotency_key)
    return h


def _post(path: str, json_body: Dict[str, Any], idempotency_key: Optional[str] = None) -> Dict[str, Any]:
    url = YOCO_API_BASE + path
    try:
        r = requests.post(url, json=json_body, headers=_headers(idempotency_key),
                          timeout=_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        raise YocoError(0, f"network error: {e.__class__.__name__}")
    if r.status_code // 100 != 2:
        body: Any
        try:
            body = r.json()
        except Exception:
            body = {"text": (r.text or "")[:500]}
        msg = ""
        if isinstance(body, dict):
            msg = body.get("description") or body.get("message") or body.get("error") or ""
        raise YocoError(r.status_code, msg or "request failed", body)
    try:
        return r.json() or {}
    except Exception:
        return {}


def create_checkout(*, amount_minor: int, currency: str, metadata: Dict[str, Any],
                    success_url: str, cancel_url: str, failure_url: Optional[str] = None,
                    idempotency_key: Optional[str] = None) -> Dict[str, Any]:
    """POST /api/checkouts. amount_minor is ZAR cents. Returns Yoco's checkout object
    (notably {id, redirectUrl, status})."""
    body = {
        "amount": int(amount_minor),
        "currency": (currency or "ZAR"),
        "successUrl": success_url,
        "cancelUrl": cancel_url,
        "failureUrl": failure_url or cancel_url,
        "metadata": {k: v for k, v in (metadata or {}).items() if v is not None},
    }
    return _post("/api/checkouts", body, idempotency_key=idempotency_key)


def refund_checkout(*, checkout_id: str, amount_minor: Optional[int] = None,
                    idempotency_key: Optional[str] = None) -> Dict[str, Any]:
    """POST /api/checkouts/{id}/refund. Omitting amount refunds the full checkout; passing
    amount_minor (ZAR cents) does a partial refund. Live keys only (Yoco rejects test-mode
    refunds). Returns the refund object."""
    body: Dict[str, Any] = {}
    if amount_minor is not None:
        body["amount"] = int(amount_minor)
    return _post(f"/api/checkouts/{checkout_id}/refund", body, idempotency_key=idempotency_key)


# ---------------------------------------------------------------------------
# Webhook signature verification (Standard Webhooks / svix scheme)
# ---------------------------------------------------------------------------

def _header(headers: Any, name: str) -> str:
    """Case-insensitive header read. Works for Flask's EnvironHeaders and plain dicts."""
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    if getter is not None:
        v = getter(name)
        if v is None and hasattr(headers, "items"):
            low = name.lower()
            for k, val in headers.items():
                if str(k).lower() == low:
                    v = val
                    break
        return (v or "").strip()
    return ""


def _signing_key(secret: str) -> bytes:
    """Standard Webhooks secret is 'whsec_<base64>'. Strip the prefix and base64-decode to
    the raw HMAC key. Falls back to raw UTF-8 bytes if it isn't valid base64 (defensive)."""
    s = (secret or "").strip()
    if s.startswith("whsec_"):
        s = s[len("whsec_"):]
    try:
        return base64.b64decode(s)
    except Exception:
        return s.encode("utf-8")


def verify_signature(*, headers: Any, raw_body: Any,
                     tolerance_seconds: int = WEBHOOK_TOLERANCE_SECONDS,
                     now: Optional[int] = None) -> bool:
    """Verify a Yoco webhook. Returns True only if the signature matches and the timestamp
    is within tolerance. Fails closed on any missing piece. `raw_body` MUST be the exact
    bytes Yoco signed (read request.get_data() BEFORE parsing JSON)."""
    secret = (os.getenv("YOCO_WEBHOOK_SECRET") or "").strip()
    if not secret:
        return False

    wid = _header(headers, "webhook-id")
    wts = _header(headers, "webhook-timestamp")
    wsig = _header(headers, "webhook-signature")
    if not (wid and wts and wsig):
        return False

    # Replay guard — the signed timestamp must be recent.
    try:
        ts = int(wts)
    except (TypeError, ValueError):
        return False
    current = int(now if now is not None else time.time())
    if abs(current - ts) > int(tolerance_seconds):
        return False

    body = raw_body.decode("utf-8") if isinstance(raw_body, (bytes, bytearray)) else str(raw_body or "")
    signed_content = f"{wid}.{ts}.{body}"
    key = _signing_key(secret)
    expected = base64.b64encode(
        hmac.new(key, signed_content.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")

    # webhook-signature is a space-separated list of "v1,<b64sig>" items (≥1).
    for part in wsig.split():
        sig = part.split(",", 1)[1] if "," in part else part
        if hmac.compare_digest(sig, expected):
            return True
    return False

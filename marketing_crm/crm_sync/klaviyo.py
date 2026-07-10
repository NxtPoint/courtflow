# marketing_crm/crm_sync/klaviyo.py — Klaviyo profile upsert + event tracking.
#
# Auth: KLAVIYO_API_KEY (private key). SELF-GATES: no key → every function is a silent no-op
# (returns False), NEVER raises. Profiles carry the `club` trait (per-club segmentation, D3 —
# one Klaviyo, many clubs) + marketing-consent state so flows can gate on it. Events are forwarded
# so Klaviyo flows trigger. API revision pinned. Ported from 1050 marketing_crm/crm_sync/klaviyo.py.

import logging
import os

log = logging.getLogger("marketing_crm.crm_sync.klaviyo")
_BASE = "https://a.klaviyo.com/api"
_REVISION = "2024-10-15"


def _key():
    return os.getenv("KLAVIYO_API_KEY")


def enabled():
    return bool(_key())


def _headers():
    return {
        "Authorization": f"Klaviyo-API-Key {_key()}",
        "revision": _REVISION,
        "accept": "application/json",
        "content-type": "application/json",
    }


def upsert_profile(traits):
    """Create-or-update a Klaviyo profile by email (profile-import upsert endpoint).
    No-op (False) without a key or an email. `traits['club']` is the per-club segmentation trait."""
    if not _key():
        return False
    email = (traits or {}).get("email")
    if not email:
        return False
    import requests
    props = {
        # `club` is the per-club segmentation trait (decision D3 — one Klaviyo, many clubs).
        "club": traits.get("club"),
        "marketing_opt_in": bool(traits.get("marketing_opt_in")),
        "role": traits.get("role"),
        "signup_source": traits.get("source"),
        # Segmentation extras (for reactivation + lifecycle): member state + dormancy signal.
        "member_status": traits.get("member_status"),
        "never_logged_in": traits.get("never_logged_in"),
    }
    props = {k: v for k, v in props.items() if v is not None}
    attrs = {"email": email, "properties": props}
    if traits.get("first_name"):
        attrs["first_name"] = str(traits["first_name"]).split(" ")[0]
    elif traits.get("display_name"):
        attrs["first_name"] = str(traits["display_name"]).split(" ")[0]
    if traits.get("last_name"):
        attrs["last_name"] = str(traits["last_name"])
    body = {"data": {"type": "profile", "attributes": attrs}}
    try:
        # POST creates; on duplicate (409) Klaviyo returns the existing id → PATCH it.
        r = requests.post(f"{_BASE}/profiles/", headers=_headers(), json=body, timeout=8)
        if r.status_code == 409:
            dup_id = (((r.json() or {}).get("errors") or [{}])[0].get("meta") or {}).get("duplicate_profile_id")
            if dup_id:
                body["data"]["id"] = dup_id
                r = requests.patch(f"{_BASE}/profiles/{dup_id}/", headers=_headers(), json=body, timeout=8)
        ok = r.status_code < 300
        if not ok:
            log.warning("klaviyo upsert %s -> %s %s", email, r.status_code, r.text[:200])
        return ok
    except Exception:
        log.exception("klaviyo profile upsert failed for %s", email)
        return False


def track_event(email, metric, properties=None):
    """Forward a product event to Klaviyo (drives flow triggers). No-op (False) without key/email."""
    if not _key() or not email:
        return False
    import requests
    body = {"data": {"type": "event", "attributes": {
        "metric": {"data": {"type": "metric", "attributes": {"name": metric}}},
        "profile": {"data": {"type": "profile", "attributes": {"email": email}}},
        "properties": properties or {},
    }}}
    try:
        r = requests.post(f"{_BASE}/events/", headers=_headers(), json=body, timeout=8)
        ok = r.status_code < 300
        if not ok:
            log.warning("klaviyo event %s/%s -> %s %s", metric, email, r.status_code, r.text[:200])
        return ok
    except Exception:
        log.exception("klaviyo track_event failed for %s/%s", metric, email)
        return False

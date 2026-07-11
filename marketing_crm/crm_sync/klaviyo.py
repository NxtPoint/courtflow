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
        # Trial cohort (drives the trial-conversion flow / segment).
        "on_trial": traits.get("on_trial"),
        "trial_ends_at": traits.get("trial_ends_at"),
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


def get_or_create_list(name):
    """Return the id of a Klaviyo list by name, creating it if absent. None on failure / no key.
    Used to hold a consented import cohort (e.g. the reactivation list)."""
    if not _key():
        return None
    import requests
    try:
        r = requests.get(f"{_BASE}/lists/", headers=_headers(),
                         params={"filter": f'equals(name,"{name}")'}, timeout=8)
        if r.status_code < 300:
            data = (r.json() or {}).get("data") or []
            if data:
                return data[0]["id"]
        body = {"data": {"type": "list", "attributes": {"name": name}}}
        r = requests.post(f"{_BASE}/lists/", headers=_headers(), json=body, timeout=8)
        if r.status_code < 300:
            return ((r.json() or {}).get("data") or {}).get("id")
        log.warning("klaviyo list create %s -> %s %s", name, r.status_code, r.text[:200])
    except Exception:
        log.exception("klaviyo get_or_create_list failed for %s", name)
    return None


def subscribe_emails(list_id, emails):
    """Subscribe a batch of emails to a list with email-marketing consent = SUBSCRIBED. We hold the
    opt-in in our OWN DB (imported Wix consent), so recording it here is legitimate — this is what
    makes API-imported profiles (which land as 'Never subscribed') actually marketable. Batches of
    100. Returns True if all batches were accepted. No-op (False) without key/list/emails."""
    if not _key() or not list_id or not emails:
        return False
    import requests
    ok_all = True
    for i in range(0, len(emails), 100):
        chunk = [e for e in emails[i:i + 100] if e]
        profiles = [{"type": "profile", "attributes": {
            "email": e,
            "subscriptions": {"email": {"marketing": {"consent": "SUBSCRIBED"}}},
        }} for e in chunk]
        body = {"data": {
            "type": "profile-subscription-bulk-create-job",
            "attributes": {"profiles": {"data": profiles}},
            "relationships": {"list": {"data": {"type": "list", "id": str(list_id)}}},
        }}
        try:
            r = requests.post(f"{_BASE}/profile-subscription-bulk-create-jobs/",
                              headers=_headers(), json=body, timeout=15)
            if r.status_code >= 300:
                ok_all = False
                log.warning("klaviyo subscribe -> %s %s", r.status_code, r.text[:200])
        except Exception:
            ok_all = False
            log.exception("klaviyo subscribe batch failed")
    return ok_all


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

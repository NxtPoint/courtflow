# marketing_crm/crm_sync/hubspot.py — HubSpot contact upsert (one-way mirror of core.*).
#
# ⚠️ DORMANT / RETAINED (faithful to 1050's 2026-06-18 decision): we do NOT use a separate CRM tool.
# Our own core.*/billing.* + the cockpit ARE the CRM (single source of truth, no sync drift, no
# per-seat cost). Klaviyo is the only active marketing destination. This module is kept dormant as a
# zero-cost escape hatch IF a sales-led motion ever needs HubSpot — it self-gates (no-op without a
# token), so leaving it costs nothing. Don't invest further here; don't set a HubSpot key.
#
# Auth: HUBSPOT_PRIVATE_APP_TOKEN (preferred) or HUBSPOT_API_KEY. No-op if unset.

import logging
import os

log = logging.getLogger("marketing_crm.crm_sync.hubspot")
_BASE = "https://api.hubapi.com/crm/v3/objects/contacts"


def _token():
    return os.getenv("HUBSPOT_PRIVATE_APP_TOKEN") or os.getenv("HUBSPOT_API_KEY")


def enabled():
    return bool(_token())


def upsert_contact(traits):
    """Upsert a HubSpot contact by email. DORMANT: no-op (False) without a token. Never raises."""
    if not _token():
        return False
    email = (traits or {}).get("email")
    if not email:
        return False
    import requests
    props = {
        "email": email,
        "club": traits.get("club"),
        "role": traits.get("role"),
    }
    props = {k: v for k, v in props.items() if v is not None}
    try:
        r = requests.post(
            f"{_BASE}?idProperty=email",
            headers={"Authorization": f"Bearer {_token()}", "content-type": "application/json"},
            json={"properties": props}, timeout=8,
        )
        ok = r.status_code < 300
        if not ok:
            log.warning("hubspot upsert %s -> %s %s", email, r.status_code, r.text[:200])
        return ok
    except Exception:
        log.exception("hubspot contact upsert failed for %s", email)
        return False

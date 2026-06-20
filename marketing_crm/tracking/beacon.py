# marketing_crm/tracking/beacon.py — public page-view beacon (navigation analytics).
#
# POST /api/track/page records a page_view into core.usage_event (account resolved by email when the
# page is authed; anonymous otherwise). Designed for navigator.sendBeacon: the body is parsed from
# the raw request (text/plain), so there is NO CORS preflight — works from the public marketing pages
# and the member SPAs alike. Never blocks; always returns {ok:true}.
#
# page_view is intentionally NOT forwarded to Klaviyo (would be noisy/expensive) — DB only.
# Ported from 1050 marketing_crm/tracking/beacon.py, multi-tenant (club_id carried into the event).

import json
import logging
import threading

from flask import Blueprint, jsonify, request

log = logging.getLogger("marketing_crm.tracking.beacon")
page_bp = Blueprint("mc_page_beacon", __name__)


@page_bp.route("/api/track/page", methods=["POST", "OPTIONS"])
def page():
    if request.method == "OPTIONS":
        return ("", 204)
    try:
        body = json.loads(request.get_data() or b"{}")
    except Exception:
        body = request.get_json(silent=True) or {}
    path = (body.get("path") or "")[:300]
    if not path:
        return jsonify({"ok": False, "error": "path required"}), 400
    club_id = (body.get("club_id") or "").strip() or None
    email = (body.get("email") or "").strip().lower() or None
    referrer = (body.get("referrer") or "")[:300]
    # First-party anonymous visitor id (client-generated, localStorage) — lets us count UNIQUE
    # VISITORS for logged-out marketing traffic (account_id is NULL there). UTM params power
    # acquisition-source analytics.
    anon_id = (str(body.get("anon_id") or "")[:64]) or None
    utm = body.get("utm") if isinstance(body.get("utm"), dict) else {}
    props = body.get("props") if isinstance(body.get("props"), dict) else {}
    try:
        threading.Thread(target=_record, args=(path, club_id, email, referrer, props, anon_id, utm),
                         daemon=True).start()
    except Exception:
        log.exception("page beacon: thread spawn failed")
    return jsonify({"ok": True})


def _record(path, club_id, email, referrer, props, anon_id=None, utm=None):
    try:
        from db import session_scope
        from core.repositories import accounts, usage_events
        with session_scope() as s:
            account_id = None
            if email:
                a = accounts.get_account_by_email(s, email)
                if a:
                    account_id = a.id
            meta = {"path": path, "referrer": referrer}
            if anon_id:
                meta["anon_id"] = anon_id
            for k in ("source", "medium", "campaign", "term", "content"):
                v = (utm or {}).get(k)
                if v:
                    meta["utm_" + k] = str(v)[:120]
            for k in list(props)[:10]:
                meta[str(k)[:40]] = str(props[k])[:200]
            if account_id is None and email:
                meta["email_unmatched"] = email
            usage_events.record_usage(
                s, event_type="page_view", club_id=club_id, account_id=account_id,
                ref_type="page", ref_id=path, metadata=meta,
            )
    except Exception:
        log.exception("page beacon: usage_event write failed")


def register(app):
    """Register the page beacon blueprint. Always on (it self-degrades with no DB)."""
    app.register_blueprint(page_bp)
    return True

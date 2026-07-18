# marketing_crm/repermission/blueprint.py — the public, token-guarded opt-in API (repermission_bp).
#
# No login: the signed opt-in token (context="optin") IS the authorization. The /subscribe page calls:
#   GET  /api/subscribe?t=<token>              -> validate + greet   {ok, first_name}
#   POST /api/subscribe {token, opt?}          -> grant (opt!=false) / undo (opt=false)  {ok, first_name, opt}
# A grant records marketing consent in OUR DB and (after commit, fire-and-forget) subscribes the member
# to the Klaviyo list → the Welcome flow triggers. See docs/specs/KLAVIYO-MASTER-PLAN.md §5.

import logging

from flask import Blueprint, jsonify, request

from marketing_crm import signing
from marketing_crm.repermission.service import apply_optin
from marketing_crm.repermission.tokens import CONTEXT

log = logging.getLogger("marketing_crm.repermission")

repermission_bp = Blueprint("repermission", __name__)


def _evidence():
    return {"ip": request.headers.get("X-Forwarded-For", request.remote_addr),
            "user_agent": (request.headers.get("User-Agent") or "")[:300],
            "source": "repermission_email"}


@repermission_bp.get("/api/subscribe")
def subscribe_validate():
    payload = signing.verify(request.args.get("t", ""), context=CONTEXT)
    if not payload:
        return jsonify(ok=False, error="invalid_or_expired"), 400
    first_name = None
    try:
        from db import session_scope
        from sqlalchemy import text
        with session_scope() as s:
            row = s.execute(
                text("SELECT first_name FROM iam.user WHERE id = CAST(:u AS uuid)"),
                {"u": payload.get("u")},
            ).mappings().first()
            if row and row["first_name"]:
                first_name = str(row["first_name"]).split(" ")[0]
    except Exception:
        log.exception("subscribe_validate: name lookup failed (non-fatal)")
    return jsonify(ok=True, first_name=first_name)


@repermission_bp.post("/api/subscribe")
def subscribe_apply():
    data = request.get_json(silent=True) or {}
    payload = signing.verify(data.get("token") or request.args.get("t", ""), context=CONTEXT)
    if not payload:
        return jsonify(ok=False, error="invalid_or_expired"), 400
    opt = data.get("opt")
    opt = True if opt is None else bool(opt)     # default action = grant
    try:
        from db import session_scope
        with session_scope() as s:
            out = apply_optin(s, payload, opt=opt, evidence=_evidence())
    except Exception:
        log.exception("subscribe_apply failed")
        return jsonify(ok=False, error="server_error"), 500
    if not out.get("ok"):
        return jsonify(ok=False, error="no_account"), 404

    # AFTER commit — reflect the change in Klaviyo (fire-and-forget; never affects the response).
    email, club_id = out.get("email"), out.get("club_id")
    try:
        from marketing_crm.crm_sync import sync as _crm
        if opt:
            _crm.subscribe_member(email, club_id=club_id)     # list + consent → Welcome flow
        else:
            _crm.sync_profile(email, club_id=club_id)         # push marketing_opt_in=false trait
    except Exception:
        log.exception("subscribe_apply: klaviyo sync failed (non-fatal)")
    # Emit consent_recorded so the funnel/usage_event reflects the opt-in.
    try:
        from marketing_crm.tracking import emit
        emit("consent_recorded", {"club_id": club_id, "email": email,
                                  "consent_type": "marketing_email", "marketing_opt_in": opt,
                                  "source": "repermission"})
    except Exception:
        log.exception("subscribe_apply: emit failed (non-fatal)")

    return jsonify(ok=True, first_name=out.get("first_name"), opt=opt)

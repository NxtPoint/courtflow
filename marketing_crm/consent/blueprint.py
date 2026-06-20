# marketing_crm/consent/blueprint.py — consent capture / withdrawal / state API.
#
# Records over the EXISTING core.consent (built by Agent A). Two paths:
#   - marketing opt-in flip (consent_type=marketing_email → flips core.app_user.marketing_opt_in,
#     the Klaviyo marketing gate);
#   - the parental/minor path (consent_type=minor_processing_parental → subject = the junior,
#     granted_by = the guardian's login). NO minor PII is ever sent to Klaviyo — the only event
#     emitted is consent_recorded keyed by the ADULT's email.
#
# Recording consent ensures the core identity (account/user/person) exists — the forward write-path
# into core.* (1050 pattern). Auth: a verified Clerk JWT (email derived server-side) OR OPS_KEY for
# server-to-server (decision D6 — no shared CLIENT_API_KEY client path). The authed adult's email is
# trusted server-side; a body `email` is only honoured for OPS/admin callers.

import logging

from flask import Blueprint, jsonify, request

from db import session_scope, norm_email
from core.repositories import accounts, consent as cons

log = logging.getLogger("marketing_crm.consent")
consent_bp = Blueprint("mc_consent", __name__)
_P = "/api/consent"

CONSENT_TYPES = cons.CONSENT_TYPES  # terms_of_service, privacy_policy, marketing_email,
#                                     minor_processing_parental


def _principal():
    """Resolve the caller (Clerk JWT or OPS_KEY). None → unauthorized. Never raises."""
    try:
        from auth import resolve_principal
        return resolve_principal(request)
    except Exception:
        log.exception("consent: principal resolution failed")
        return None


def _caller_email(p, body):
    """The email the consent applies to. For a normal authed user it is THEIR verified email
    (the client can't assert a different one). OPS/platform_admin callers may target an email
    via the body (server-to-server / admin capture)."""
    if p is not None and getattr(p, "is_platform_admin", False):
        return norm_email(body.get("email")) or norm_email(getattr(p, "email", None))
    return norm_email(getattr(p, "email", None)) if p is not None else None


def _club_id(p, body):
    return (getattr(p, "club_id", None) or (body.get("club_id") if p and p.is_platform_admin else None))


def _evidence():
    return {"ip": request.headers.get("X-Forwarded-For", request.remote_addr),
            "user_agent": request.headers.get("User-Agent", "")[:300]}


@consent_bp.route(f"{_P}/record", methods=["POST", "OPTIONS"])
def record():
    if request.method == "OPTIONS":
        return ("", 204)
    p = _principal()
    if p is None or not p.authenticated:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    email = _caller_email(p, body)
    ctype = (body.get("consent_type") or "").strip()
    if not email:
        return jsonify({"ok": False, "error": "email required"}), 400
    if ctype not in CONSENT_TYPES:
        return jsonify({"ok": False, "error": f"unknown consent_type (allowed: {sorted(CONSENT_TYPES)})"}), 400

    club_id = _club_id(p, body)
    policy_version = body.get("policy_version") or cons.CURRENT_POLICY_VERSION
    full_name = body.get("full_name")
    marketing_flag = None
    with session_scope() as s:
        acct, owner, primary = accounts.ensure_identity(s, email=email, full_name=full_name,
                                                         club_id=club_id)
        # subject: self by default; for a minor's parental consent the subject is the junior.
        subject_person_id = primary.id
        if ctype == "minor_processing_parental":
            jr_name = (body.get("subject_name") or "").strip()
            if not jr_name:
                return jsonify({"ok": False, "error": "subject_name required for parental consent"}), 400
            junior = accounts.create_person(s, account_id=acct.id, full_name=jr_name,
                                            role="player", dob=_parse_date(body.get("subject_dob")),
                                            club_id=club_id)
            subject_person_id = junior.id

        cons.record_consent(
            s, subject_person_id=subject_person_id, consent_type=ctype,
            granted_by_user_id=owner.id, status="granted",
            policy_version=policy_version, source=body.get("source") or "portal",
            evidence=_evidence(), club_id=club_id,
        )
        # marketing consent flips the opt-in flag (the Klaviyo marketing gate).
        if ctype == "marketing_email":
            accounts.set_marketing_opt_in(s, owner.id, True)
            marketing_flag = True

    # Emit consent_recorded (keyed by the ADULT email — never minor PII). System event: sets state.
    _emit_consent(ctype, email, club_id, marketing_flag)
    return jsonify({"ok": True, "consent_type": ctype})


@consent_bp.route(f"{_P}/withdraw", methods=["POST", "OPTIONS"])
def withdraw():
    if request.method == "OPTIONS":
        return ("", 204)
    p = _principal()
    if p is None or not p.authenticated:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    email = _caller_email(p, body)
    ctype = (body.get("consent_type") or "").strip()
    if not email or ctype not in CONSENT_TYPES:
        return jsonify({"ok": False, "error": "email + valid consent_type required"}), 400
    club_id = _club_id(p, body)
    with session_scope() as s:
        acct = accounts.get_account_by_email(s, email)
        if acct is None:
            return jsonify({"ok": False, "error": "account not found"}), 404
        owner = accounts.get_user_by_email(s, email)
        primary = accounts.get_primary_person(s, acct.id)
        subject_id = primary.id if primary else None
        if subject_id:
            cons.withdraw_consent(s, subject_person_id=subject_id, consent_type=ctype,
                                  granted_by_user_id=(owner.id if owner else None), club_id=club_id)
        if ctype == "marketing_email" and owner:
            accounts.set_marketing_opt_in(s, owner.id, False)
    return jsonify({"ok": True, "consent_type": ctype, "status": "withdrawn"})


@consent_bp.route(f"{_P}/state", methods=["GET", "OPTIONS"])
def state():
    if request.method == "OPTIONS":
        return ("", 204)
    p = _principal()
    if p is None or not p.authenticated:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    # Self-service: a user reads their own state; an admin may pass ?email=.
    email = norm_email(request.args.get("email")) if getattr(p, "is_platform_admin", False) else None
    email = email or norm_email(getattr(p, "email", None))
    out = {"ok": True, "marketing_opt_in": False, "consents": {}}
    if not email:
        return jsonify(out)
    with session_scope() as s:
        acct = accounts.get_account_by_email(s, email)
        if acct is None:
            return jsonify(out)
        owner = accounts.get_user_by_email(s, email)
        out["marketing_opt_in"] = bool(owner.marketing_opt_in) if owner else False
        primary = accounts.get_primary_person(s, acct.id)
        if primary:
            for ct in CONSENT_TYPES:
                row = cons.latest_consent(s, primary.id, ct)
                out["consents"][ct] = (row.status if row else None)
    return jsonify(out)


def _parse_date(v):
    if not v:
        return None
    try:
        from datetime import date
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _emit_consent(ctype, email, club_id, marketing_flag):
    try:
        from marketing_crm.tracking import emit
        from marketing_crm.tracking.events import CONSENT_RECORDED
        payload = {"club_id": club_id, "email": email, "consent_type": ctype}
        if marketing_flag is not None:
            payload["marketing_opt_in"] = marketing_flag
        emit(CONSENT_RECORDED, payload)
    except Exception:
        log.exception("consent: emit failed")


def register(app):
    """Register the consent endpoints. Always on (every route is auth-gated)."""
    app.register_blueprint(consent_bp)
    return True

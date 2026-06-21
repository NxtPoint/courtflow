# me/routes.py — the /api/me/* surface: the client "My Account" self-service lane.
# Blueprint me_bp. Registered in app.py (one _try_register line).
#
# Thin routes (admin/diary style): resolve the principal (auth.resolve_principal), gate with
# iam.permissions.can(), pull club_id + user_id FROM THE PRINCIPAL (never the body — docs/02 §1),
# call iam.repositories, map dicts to JSON. Every action is scoped to the CALLER's own row:
# a member only ever reads/writes their OWN profile and their OWN dependents.
#
# Endpoints (spec §2.3, §3.3):
#   GET   /api/me/profile               -> the caller's demographics (email read-only)
#   PATCH /api/me/profile               -> update only editable fields (email immutable)
#   GET   /api/me/dependents            -> the caller's active children/dependents
#   POST  /api/me/dependents            -> add a child (login-less iam.user + iam.dependent)
#   PATCH /api/me/dependents/<id>       -> edit one the caller owns
#   DELETE /api/me/dependents/<id>      -> soft-remove one the caller owns

import logging
from datetime import date, datetime

from flask import Blueprint, jsonify, request

from auth import resolve_principal
from db import session_scope
from iam.permissions import can
from iam import repositories as iam_repo

log = logging.getLogger("me.routes")

me_bp = Blueprint("me", __name__, url_prefix="/api/me")


# ---------------------------------------------------------------------------
# auth helpers
# ---------------------------------------------------------------------------

def _principal():
    """Resolve an authenticated, club-scoped principal with a real user_id, or
    (None, error_response). OPS principals carry no user_id → not a client self-service path."""
    p = resolve_principal(request)
    if p is None or not p.authenticated:
        return None, (jsonify(error="unauthorized"), 401)
    if not p.user_id:
        return None, (jsonify(error="unauthorized"), 401)
    if p.club_id is None:
        return None, (jsonify(error="no_club_scope"), 400)
    return p, None


def _body():
    return request.get_json(silent=True) or {}


# ---------------------------------------------------------------------------
# validation (spec §2.1)
# ---------------------------------------------------------------------------

def _validate_name(v):
    v = (v or "").strip()
    if not (1 <= len(v) <= 80):
        return None, "1–80 characters"
    return v, None


def _validate_phone(v):
    v = (v or "").strip()
    if v == "":
        return "", None
    # E.164-ish: +, digits, spaces, dashes, parens; 7–20 chars.
    import re
    if not re.fullmatch(r"[+0-9 ()\-]{7,20}", v):
        return None, "7–20 digits (may include + and spaces)"
    return v, None


def _validate_dob(v):
    """Optional ISO date; not in the future; sanity floor 1900. Returns (value, err)."""
    if v in (None, ""):
        return None, None
    try:
        d = date.fromisoformat(str(v)[:10])
    except Exception:
        return None, "must be a date (YYYY-MM-DD)"
    today = datetime.utcnow().date()
    if d > today:
        return None, "cannot be in the future"
    if d.year < 1900:
        return None, "too far in the past"
    return d.isoformat(), None


def _validate_profile_patch(b):
    """Validate the editable demographics; return (clean_fields, errors). Only present keys are
    considered. email is silently dropped (never written — it is the identity key)."""
    fields, errors = {}, {}

    if "first_name" in b:
        v, e = _validate_name(b.get("first_name"))
        if e:
            errors["first_name"] = e
        else:
            fields["first_name"] = v
    if "surname" in b:
        # surname allowed empty (≤80 when present)
        sv = (b.get("surname") or "").strip()
        if len(sv) > 80:
            errors["surname"] = "≤ 80 characters"
        else:
            fields["surname"] = sv
    if "phone" in b:
        v, e = _validate_phone(b.get("phone"))
        if e:
            errors["phone"] = e
        else:
            fields["phone"] = v
    if "dob" in b:
        v, e = _validate_dob(b.get("dob"))
        if e:
            errors["dob"] = e
        else:
            fields["dob"] = v
    if "emergency_contact_phone" in b:
        v, e = _validate_phone(b.get("emergency_contact_phone"))
        if e:
            errors["emergency_contact_phone"] = e
        else:
            fields["emergency_contact_phone"] = v

    # free-text demographics: length-capped, trimmed.
    _caps = {"address_line1": 120, "address_line2": 120, "city": 80, "postal_code": 16,
             "country": 64, "emergency_contact_name": 80}
    for k, cap in _caps.items():
        if k in b:
            sv = (b.get(k) or "").strip()
            if len(sv) > cap:
                errors[k] = f"≤ {cap} characters"
            else:
                fields[k] = sv

    if "marketing_opt_in" in b:
        fields["marketing_opt_in"] = bool(b.get("marketing_opt_in"))

    return fields, errors


# ---------------------------------------------------------------------------
# profile  (spec §2.3)
# ---------------------------------------------------------------------------

@me_bp.get("/profile")
def get_profile():
    p, err = _principal()
    if err:
        return err
    if not can(p, "manage_own_profile", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    with session_scope() as s:
        prof = iam_repo.get_profile(s, user_id=p.user_id)
    if prof is None:
        return jsonify(error="NOT_FOUND"), 404
    prof["role"] = p.role  # for the UI; not persisted, not editable
    return jsonify(prof), 200


@me_bp.patch("/profile")
def patch_profile():
    p, err = _principal()
    if err:
        return err
    if not can(p, "manage_own_profile", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    b = _body()
    fields, errors = _validate_profile_patch(b)
    if errors:
        return jsonify(error="VALIDATION", fields=errors), 422
    # email / role / club / clerk_user_id are never accepted — patch_profile whitelists strictly.
    with session_scope() as s:
        prof = iam_repo.patch_profile(s, user_id=p.user_id, fields=fields)
    if prof is None:
        return jsonify(error="NOT_FOUND"), 404
    # On a marketing-consent change, best-effort emit consent_updated (guarded — same pattern as
    # admin._send_coach_invite_email; never hard-depends on marketing_crm).
    if "marketing_opt_in" in fields:
        try:
            from marketing_crm.tracking import emit
            emit("consent_updated", {"club_id": str(p.club_id), "email": prof.get("email"),
                                     "marketing_opt_in": bool(fields["marketing_opt_in"])})
        except Exception:
            log.debug("consent_updated emit skipped (tracking unavailable)")
    prof["role"] = p.role
    return jsonify(prof), 200


# ---------------------------------------------------------------------------
# dependents / children  (spec §3.3)
# ---------------------------------------------------------------------------

@me_bp.get("/dependents")
def list_dependents():
    p, err = _principal()
    if err:
        return err
    if not can(p, "add_junior", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    with session_scope() as s:
        rows = iam_repo.list_dependents(s, club_id=p.club_id, guardian_user_id=p.user_id)
    return jsonify(dependents=rows, count=len(rows)), 200


@me_bp.post("/dependents")
def create_dependent():
    p, err = _principal()
    if err:
        return err
    if not can(p, "add_junior", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    b = _body()
    first_name, e = _validate_name(b.get("first_name"))
    if e:
        return jsonify(error="VALIDATION", fields={"first_name": e}), 422
    dob, e = _validate_dob(b.get("dob"))
    if e:
        return jsonify(error="VALIDATION", fields={"dob": e}), 422
    relationship = (b.get("relationship") or "child").strip()
    if relationship not in ("child", "spouse", "partner", "other"):
        relationship = "child"
    with session_scope() as s:
        dep = iam_repo.create_dependent(
            s, club_id=p.club_id, guardian_user_id=p.user_id,
            first_name=first_name, surname=(b.get("surname") or "").strip() or None,
            dob=dob, relationship=relationship,
            is_minor=bool(b.get("is_minor", True)),
            notes=(b.get("notes") or "").strip() or None,
        )
    # Best-effort: dependent_added (marketing; guardian email only — NEVER child PII, per the contract).
    try:
        from marketing_crm.tracking import emit
        emit("dependent_added", {"club_id": str(p.club_id), "email": p.email,
                                 "relationship": relationship, "is_minor": bool(b.get("is_minor", True))})
    except Exception:
        log.debug("dependent_added emit skipped (tracking unavailable)")
    return jsonify(dependent=dep), 201


@me_bp.patch("/dependents/<dependent_id>")
def update_dependent(dependent_id):
    p, err = _principal()
    if err:
        return err
    if not can(p, "add_junior", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    b = _body()
    # Validate the present, editable fields.
    errors = {}
    fields = {}
    if "first_name" in b:
        v, e = _validate_name(b.get("first_name"))
        if e:
            errors["first_name"] = e
        else:
            fields["first_name"] = v
    if "surname" in b:
        fields["surname"] = (b.get("surname") or "").strip() or None
    if "dob" in b:
        v, e = _validate_dob(b.get("dob"))
        if e:
            errors["dob"] = e
        else:
            fields["dob"] = v
    if "relationship" in b:
        rel = (b.get("relationship") or "child").strip()
        fields["relationship"] = rel if rel in ("child", "spouse", "partner", "other") else "child"
    if "is_minor" in b:
        fields["is_minor"] = bool(b.get("is_minor"))
    if "notes" in b:
        fields["notes"] = (b.get("notes") or "").strip() or None
    if errors:
        return jsonify(error="VALIDATION", fields=errors), 422
    with session_scope() as s:
        dep = iam_repo.update_dependent(s, club_id=p.club_id, guardian_user_id=p.user_id,
                                        dependent_id=dependent_id, fields=fields)
    if dep is None:
        return jsonify(error="NOT_FOUND"), 404  # not the caller's dependent (or doesn't exist)
    return jsonify(dependent=dep), 200


@me_bp.delete("/dependents/<dependent_id>")
def delete_dependent(dependent_id):
    p, err = _principal()
    if err:
        return err
    if not can(p, "add_junior", {"club_id": p.club_id}):
        return jsonify(error="forbidden"), 403
    with session_scope() as s:
        ok = iam_repo.deactivate_dependent(s, club_id=p.club_id, guardian_user_id=p.user_id,
                                           dependent_id=dependent_id)
    if not ok:
        return jsonify(error="NOT_FOUND"), 404
    return jsonify(ok=True), 200

# marketing_crm/repermission/service.py — apply (or undo) marketing opt-in for a verified opt-in token.
#
# Reuse-first: grant reuses consent.grant_marketing_consent (ensures identity + writes core.consent +
# flips core.app_user.marketing_opt_in); undo mirrors the consent-withdraw path. Repos never commit —
# the caller composes via db.session_scope(); the Klaviyo subscribe/sync + emit happen AFTER commit.

import logging

from sqlalchemy import text

log = logging.getLogger("marketing_crm.repermission.service")


def _resolve(session, iam_user_id):
    row = session.execute(
        text("SELECT email, first_name FROM iam.user WHERE id = CAST(:u AS uuid)"),
        {"u": iam_user_id},
    ).mappings().first()
    email = (row["email"].strip().lower() if row and row["email"] else None) if row else None
    first_name = (row["first_name"].strip() if row and row["first_name"] else None) if row else None
    return email, first_name


def apply_optin(session, payload, *, opt=True, evidence=None):
    """Grant (opt=True) or withdraw (opt=False) marketing consent for the token's recipient. Returns
    {ok, email, club_id, first_name, opt}. The caller subscribes/syncs Klaviyo + emits after commit."""
    iam_uid = payload.get("u")
    club_id = payload.get("c")
    email, first_name = _resolve(session, iam_uid)
    out = {"ok": False, "email": email, "club_id": club_id,
           "first_name": (first_name.split(" ")[0] if first_name else None), "opt": opt}
    if not email:
        return out

    from core.repositories import accounts, consent as cons
    if opt:
        # Grant: ensure identity + record core.consent(marketing_email, granted) + flip the opt-in flag.
        from marketing_crm.consent.blueprint import grant_marketing_consent
        grant_marketing_consent(session, email=email, club_id=club_id,
                                source="repermission", evidence=evidence or {})
    else:
        # Undo: flip the flag off + record the withdrawal (mirrors /api/consent/withdraw).
        owner = accounts.get_user_by_email(session, email)
        if owner:
            accounts.set_marketing_opt_in(session, owner.id, False)
        acct = accounts.get_account_by_email(session, email)
        primary = accounts.get_primary_person(session, acct.id) if acct else None
        if primary:
            cons.withdraw_consent(session, subject_person_id=primary.id,
                                  consent_type="marketing_email",
                                  granted_by_user_id=(owner.id if owner else None), club_id=club_id)
    out["ok"] = True
    return out

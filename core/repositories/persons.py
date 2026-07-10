# core/repositories/persons.py — the iam.user <-> core.person bridge (Client-360 Slice-0, Step 2).
#
# link_person_for_user() ensures the 1:1 core.person CRM satellite for a CANONICAL iam.user
# exists and is linked (core.person.iam_user_id). Idempotent + safe to call on every login AND
# from the one-off backfill. Repo functions take an explicit `session` and never commit — callers
# compose via db.session_scope(). See docs/specs/CLIENT-360-CRM-PLAN.md §10.
#
# Match order (why this is safe even if core.* already holds consent-created rows):
#   1. already linked (core.person.iam_user_id set) -> return it;
#   2. else ensure_identity(email) ADOPTS an email-matched existing person or CREATES one
#      (create_account / create_user / get_primary_person are all idempotent-by-email), so a
#      consent-first human is claimed, not duplicated;
#   3. stamp iam_user_id + carry surname / phone / marketing_opt_in from the canonical record.
#
# Dependents (login-less iam.user rows with NULL email) are NOT handled here — they attach to the
# GUARDIAN's core.account via a separate path; this helper returns None for an emailless user.

from sqlalchemy import select

from core.models import Person
from core.repositories.accounts import ensure_identity


def _full_name(first_name, surname):
    parts = [p.strip() for p in (first_name, surname) if p and p.strip()]
    return " ".join(parts) or None


def _apply_profile(person, *, surname=None, phone=None):
    """Carry canonical fields onto the satellite WITHOUT overwriting data it already has."""
    if surname and not (person.surname and person.surname.strip()):
        person.surname = surname.strip()
    if phone and not (person.phone and person.phone.strip()):
        person.phone = phone.strip()


def get_linked_person(session, iam_user_id):
    """Return the core.person linked to this iam.user, or None."""
    if not iam_user_id:
        return None
    return session.execute(
        select(Person).where(Person.iam_user_id == iam_user_id)
    ).scalar_one_or_none()


def link_person_for_user(session, *, iam_user_id, club_id, email,
                         first_name=None, surname=None, phone=None,
                         marketing_opt_in=None, role="player"):
    """Ensure + link the core.person satellite for an iam.user. Returns the Person, or None when
    the user has no email (dependents — handled by the guardian-attach path, not here).

    Idempotent: re-calling on every login is a cheap no-op once linked (it only fills any
    still-missing profile fields; it never overwrites)."""
    # 1. Already linked?
    person = get_linked_person(session, iam_user_id)
    if person is not None:
        _apply_profile(person, surname=surname, phone=phone)
        session.flush()
        return person

    # No email -> cannot key a core.account (login-less dependents). Caller handles those.
    if not (email and email.strip()):
        return None

    # 2. Adopt-or-create by email (ensure_identity is idempotent on the email).
    full_name = _full_name(first_name, surname) or email
    _acct, user, person = ensure_identity(session, email=email, full_name=full_name,
                                          role=role, club_id=club_id)

    # 3. Stamp the bridge + carry the canonical profile onto the satellite.
    person.iam_user_id = iam_user_id
    _apply_profile(person, surname=surname, phone=phone)
    if marketing_opt_in is not None:
        user.marketing_opt_in = bool(marketing_opt_in)
    session.flush()
    return person

# core/repositories/accounts.py — accounts, users, persons, relationships.
#
# Ported from 1050 core_db/repositories/accounts.py. Change: club_id threaded through
# the create_* paths (multi-tenancy). Every function takes an explicit `session` and
# never commits; callers compose via core.db.session_scope().

from datetime import date, datetime, timezone

from sqlalchemy import func, select

from core.db import norm_email
from core.models import Account, AppUser, Acquisition, Person, Relationship


def _now():
    return datetime.now(timezone.utc)


def _is_minor(dob):
    if not dob:
        return None
    today = date.today()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return age < 18


# ---- Account -------------------------------------------------------------

def get_account_by_email(session, email):
    email = norm_email(email)
    if not email:
        return None
    return session.execute(
        select(Account).where(func.lower(Account.email) == email, Account.deleted_at.is_(None))
    ).scalar_one_or_none()


def create_account(session, *, email, display_name=None, currency_code="ZAR",
                   club_id=None):
    """Create an account (idempotent on email — returns existing if present)."""
    existing = get_account_by_email(session, email)
    if existing:
        return existing
    acct = Account(
        email=norm_email(email),
        display_name=display_name,
        currency_code=currency_code,
        club_id=club_id,
    )
    session.add(acct)
    session.flush()
    return acct


# ---- User ----------------------------------------------------------------

def get_user_by_email(session, email):
    email = norm_email(email)
    if not email:
        return None
    return session.execute(
        select(AppUser).where(func.lower(AppUser.email) == email, AppUser.deleted_at.is_(None))
    ).scalar_one_or_none()


def get_user_by_auth_provider_uid(session, auth_provider, auth_provider_uid):
    """Resolve a login identity by its external IdP id (e.g. Clerk `sub`)."""
    if not auth_provider or not auth_provider_uid:
        return None
    return session.execute(
        select(AppUser).where(
            AppUser.auth_provider == auth_provider,
            AppUser.auth_provider_uid == auth_provider_uid,
            AppUser.deleted_at.is_(None),
        )
    ).scalar_one_or_none()


def set_auth_provider(session, user_id, *, auth_provider, auth_provider_uid, email_verified=None):
    user = session.get(AppUser, user_id)
    if user is None:
        return None
    user.auth_provider = auth_provider
    user.auth_provider_uid = auth_provider_uid
    if email_verified is not None:
        user.email_verified = bool(email_verified)
    user.updated_at = _now()
    session.flush()
    return user


def create_user(session, *, account_id, email, auth_provider="clerk", auth_provider_uid=None,
                is_account_owner=False, marketing_opt_in=False, email_verified=False, club_id=None):
    existing = get_user_by_email(session, email)
    if existing:
        return existing
    user = AppUser(
        account_id=account_id,
        email=norm_email(email),
        auth_provider=auth_provider,
        auth_provider_uid=auth_provider_uid,
        is_account_owner=is_account_owner,
        marketing_opt_in=marketing_opt_in,
        email_verified=email_verified,
        club_id=club_id,
    )
    session.add(user)
    session.flush()
    return user


def touch_login(session, user_id):
    user = session.get(AppUser, user_id)
    if user:
        user.last_login_at = _now()
        session.flush()
    return user


def set_marketing_opt_in(session, user_id, value):
    user = session.get(AppUser, user_id)
    if user:
        user.marketing_opt_in = bool(value)
        user.updated_at = _now()
        session.flush()
    return user


# ---- Person --------------------------------------------------------------

def create_person(session, *, account_id, full_name, role="player", user_id=None,
                  is_primary=False, dob=None, club_id=None, **profile):
    person = Person(
        account_id=account_id,
        user_id=user_id,
        club_id=club_id,
        full_name=full_name,
        role=role,
        is_primary=is_primary,
        dob=dob,
        is_minor=_is_minor(dob),
    )
    for k, v in profile.items():
        if hasattr(person, k):
            setattr(person, k, v)
    session.add(person)
    session.flush()
    return person


def get_primary_person(session, account_id):
    return session.execute(
        select(Person).where(Person.account_id == account_id, Person.is_primary.is_(True),
                             Person.deleted_at.is_(None)).order_by(Person.id).limit(1)
    ).scalar_one_or_none()


def ensure_identity(session, *, email, full_name=None, role="player", club_id=None):
    """Idempotently ensure a core account + owner user + primary person exist for an email.
    The forward write-path into core.* (driven by consent capture / signup).
    Returns (account, owner_user, primary_person)."""
    acct = create_account(session, email=email, display_name=full_name, club_id=club_id)
    user = create_user(session, account_id=acct.id, email=email, is_account_owner=True,
                       club_id=club_id)
    person = get_primary_person(session, acct.id)
    if person is None:
        person = create_person(session, account_id=acct.id, full_name=(full_name or email),
                               role=role, user_id=user.id, is_primary=True, club_id=club_id)
    return acct, user, person


# ---- Relationship (coach<->player, parent<->junior) ----------------------

def link_persons(session, *, from_person_id, to_person_id, type_, status="active",
                 club_id=None, invite_token=None, invited_email=None):
    """Idempotent on (from, to, type): re-linking updates status/token."""
    existing = session.execute(
        select(Relationship).where(
            Relationship.from_person_id == from_person_id,
            Relationship.to_person_id == to_person_id,
            Relationship.type == type_,
        )
    ).scalar_one_or_none()
    if existing:
        existing.status = status
        existing.invite_token = invite_token
        existing.invited_email = invited_email
        existing.updated_at = _now()
        if status == "revoked":
            existing.revoked_at = _now()
        session.flush()
        return existing
    rel = Relationship(
        from_person_id=from_person_id, to_person_id=to_person_id, type=type_,
        status=status, club_id=club_id, invite_token=invite_token, invited_email=invited_email,
    )
    session.add(rel)
    session.flush()
    return rel

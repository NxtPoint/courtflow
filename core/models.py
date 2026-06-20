# core/models.py — SQLAlchemy ORM models for the canonical `core.*` CRM schema.
#
# Ported from 1050 core_db/models.py, with two deliberate changes:
#   1. MULTI-TENANCY: club_id added where relevant (docs/02 §6). Domain rows carry
#      club_id NOT NULL so RLS is a drop-in later (decision D7).
#   2. SCOPE: 1050's ML/billing nouns (Match, Plan, Subscription, CreditLedger,
#      SubscriptionEvent) are NOT ported here — bookings billing is its own billing.*
#      schema owned by Agent C (decision D1). core.* keeps the CRM/compliance core:
#      account / app_user / person / usage_event / consent / nps + DSAR/retention.
#
# Conventions (unchanged from 1050):
#   - bigint identity PK `id` + `public_id uuid` on externally-exposed entities.
#   - email lowercased by the DAL; case-insensitive uniqueness via functional index.
#   - created_at server-default now(); updated_at maintained by the DAL.
#   - soft-delete via deleted_at; retention via retention_until / anonymized_at.
#   - physical table for users is core.app_user (`user` is reserved in Postgres).

from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()
SCHEMA = "core"

_UUID_DEFAULT = text("gen_random_uuid()")
_NOW = text("now()")


def _ts(**kw):
    return Column(DateTime(timezone=True), **kw)


# club_id is a tenant discriminator. It references club.club(id) (a UUID PK in another
# schema); we declare the column as a plain UUID + add the FK in schema.py supplemental
# DDL to avoid a hard ORM cross-schema metadata dependency on the club package.
def _club_id(nullable=False):
    return Column(UUID(as_uuid=True), nullable=nullable)


# ---------------------------------------------------------------------------
# Identity & ownership
# ---------------------------------------------------------------------------

class Account(Base):
    """Billing / ownership container — one per paying customer (the guardian/adult)."""
    __tablename__ = "account"
    __table_args__ = {"schema": SCHEMA}

    id = Column(BigInteger, primary_key=True)
    public_id = Column(UUID(as_uuid=True), nullable=False, server_default=_UUID_DEFAULT)
    club_id = _club_id(nullable=True)   # nullable: a human can predate club assignment
    email = Column(Text, nullable=False)
    display_name = Column(Text, nullable=True)
    currency_code = Column(CHAR(3), nullable=False, server_default=text("'ZAR'"))
    status = Column(Text, nullable=False, server_default=text("'active'"))  # active|suspended|closed

    created_at = _ts(nullable=False, server_default=_NOW)
    updated_at = _ts(nullable=False, server_default=_NOW)
    deleted_at = _ts(nullable=True)

    users = relationship("AppUser", back_populates="account", cascade="all, delete-orphan")
    persons = relationship("Person", back_populates="account", cascade="all, delete-orphan")


class AppUser(Base):
    """Authenticatable login identity. Physical table core.app_user."""
    __tablename__ = "app_user"
    __table_args__ = {"schema": SCHEMA}

    id = Column(BigInteger, primary_key=True)
    public_id = Column(UUID(as_uuid=True), nullable=False, server_default=_UUID_DEFAULT)
    account_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.account.id", ondelete="CASCADE"), nullable=False)
    club_id = _club_id(nullable=True)

    email = Column(Text, nullable=False)
    auth_provider = Column(Text, nullable=False, server_default=text("'clerk'"))  # clerk|password|google|...
    auth_provider_uid = Column(Text, nullable=True)   # Clerk `sub`
    email_verified = Column(Boolean, nullable=False, server_default=text("false"))
    is_account_owner = Column(Boolean, nullable=False, server_default=text("false"))
    marketing_opt_in = Column(Boolean, nullable=False, server_default=text("false"))
    status = Column(Text, nullable=False, server_default=text("'active'"))  # active|disabled

    last_login_at = _ts(nullable=True)
    created_at = _ts(nullable=False, server_default=_NOW)
    updated_at = _ts(nullable=False, server_default=_NOW)
    deleted_at = _ts(nullable=True)

    account = relationship("Account", back_populates="users")
    acquisition = relationship("Acquisition", back_populates="user", uselist=False,
                               cascade="all, delete-orphan")


class Acquisition(Base):
    """Signup attribution (UTM / source), 1:1 with a user."""
    __tablename__ = "acquisition"
    __table_args__ = {"schema": SCHEMA}

    user_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.app_user.id", ondelete="CASCADE"),
                     primary_key=True)
    source = Column(Text, nullable=True)
    medium = Column(Text, nullable=True)
    campaign = Column(Text, nullable=True)
    term = Column(Text, nullable=True)
    content = Column(Text, nullable=True)
    referrer = Column(Text, nullable=True)
    landing_page = Column(Text, nullable=True)
    gclid = Column(Text, nullable=True)
    fbclid = Column(Text, nullable=True)
    first_seen_at = _ts(nullable=True)
    signed_up_at = _ts(nullable=True)
    created_at = _ts(nullable=False, server_default=_NOW)

    user = relationship("AppUser", back_populates="acquisition")


class Person(Base):
    """Tennis profile (player/parent/coach). A minor is a player with is_minor=true."""
    __tablename__ = "person"
    __table_args__ = {"schema": SCHEMA}

    id = Column(BigInteger, primary_key=True)
    public_id = Column(UUID(as_uuid=True), nullable=False, server_default=_UUID_DEFAULT)
    account_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.account.id", ondelete="CASCADE"), nullable=False)
    club_id = _club_id(nullable=True)
    user_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.app_user.id", ondelete="SET NULL"), nullable=True)

    role = Column(Text, nullable=False, server_default=text("'player'"))  # player|parent|coach
    is_primary = Column(Boolean, nullable=False, server_default=text("false"))
    full_name = Column(Text, nullable=False)
    surname = Column(Text, nullable=True)

    dob = Column(Date, nullable=True)
    is_minor = Column(Boolean, nullable=True)   # derived (age isn't immutable), refreshed on write

    utr = Column(Text, nullable=True)
    dominant_hand = Column(Text, nullable=True)
    country = Column(Text, nullable=True)
    area = Column(Text, nullable=True)
    phone = Column(Text, nullable=True)
    skill_level = Column(Text, nullable=True)
    club_school = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    profile_photo_url = Column(Text, nullable=True)

    status = Column(Text, nullable=False, server_default=text("'active'"))
    created_at = _ts(nullable=False, server_default=_NOW)
    updated_at = _ts(nullable=False, server_default=_NOW)
    deleted_at = _ts(nullable=True)
    retention_until = _ts(nullable=True)
    anonymized_at = _ts(nullable=True)

    account = relationship("Account", back_populates="persons")


class Relationship(Base):
    """coach<->player, parent<->junior."""
    __tablename__ = "relationship"
    __table_args__ = (
        UniqueConstraint("from_person_id", "to_person_id", "type",
                         name="uq_relationship_from_to_type"),
        {"schema": SCHEMA},
    )

    id = Column(BigInteger, primary_key=True)
    club_id = _club_id(nullable=True)
    from_person_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.person.id", ondelete="CASCADE"), nullable=False)
    to_person_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.person.id", ondelete="CASCADE"), nullable=False)
    type = Column(Text, nullable=False)               # coach_player | parent_junior
    status = Column(Text, nullable=False, server_default=text("'pending'"))  # pending|active|revoked
    invite_token = Column(Text, nullable=True)
    invited_email = Column(Text, nullable=True)
    created_at = _ts(nullable=False, server_default=_NOW)
    updated_at = _ts(nullable=False, server_default=_NOW)
    revoked_at = _ts(nullable=True)


# ---------------------------------------------------------------------------
# Usage events (the canonical stream crm_sync forwards to Klaviyo, docs/06)
# ---------------------------------------------------------------------------

class UsageEvent(Base):
    __tablename__ = "usage_event"
    __table_args__ = {"schema": SCHEMA}

    id = Column(BigInteger, primary_key=True)
    club_id = _club_id(nullable=True)
    account_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.account.id", ondelete="SET NULL"), nullable=True)
    user_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.app_user.id", ondelete="SET NULL"), nullable=True)
    person_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.person.id", ondelete="SET NULL"), nullable=True)
    event_type = Column(Text, nullable=False)         # booking_confirmed|account_created|login|...
    ref_type = Column(Text, nullable=True)
    ref_id = Column(Text, nullable=True)
    event_metadata = Column("metadata", JSONB, nullable=True)  # physical column name `metadata`
    occurred_at = _ts(nullable=False, server_default=_NOW)
    created_at = _ts(nullable=False, server_default=_NOW)


# ---------------------------------------------------------------------------
# Feedback / NPS
# ---------------------------------------------------------------------------

class NpsResponse(Base):
    __tablename__ = "nps_response"
    __table_args__ = {"schema": SCHEMA}

    id = Column(BigInteger, primary_key=True)
    club_id = _club_id(nullable=True)
    account_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.account.id", ondelete="SET NULL"), nullable=True)
    user_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.app_user.id", ondelete="SET NULL"), nullable=True)
    score = Column(Integer, nullable=False)           # 0-10
    bucket = Column(Text, nullable=True)              # detractor|passive|promoter (derived)
    comment = Column(Text, nullable=True)
    survey_id = Column(Text, nullable=True)
    submitted_at = _ts(nullable=False, server_default=_NOW)


# ---------------------------------------------------------------------------
# Consent, privacy & retention (compliance core — parental/minor model, docs/04 §5)
# ---------------------------------------------------------------------------

class Consent(Base):
    """Versioned, per-type consent. For a minor: subject=junior, granted_by=parent's user."""
    __tablename__ = "consent"
    __table_args__ = {"schema": SCHEMA}

    id = Column(BigInteger, primary_key=True)
    club_id = _club_id(nullable=True)
    subject_person_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.person.id", ondelete="CASCADE"), nullable=False)
    granted_by_user_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.app_user.id", ondelete="SET NULL"), nullable=True)
    consent_type = Column(Text, nullable=False)
    # terms_of_service|privacy_policy|marketing_email|minor_processing_parental
    policy_version = Column(Text, nullable=True)
    status = Column(Text, nullable=False, server_default=text("'granted'"))  # granted|withdrawn
    granted_at = _ts(nullable=True)
    withdrawn_at = _ts(nullable=True)
    source = Column(Text, nullable=True)              # signup|portal|import
    evidence = Column(JSONB, nullable=True)           # ip, user-agent, exact checkbox text
    created_at = _ts(nullable=False, server_default=_NOW)


class DataSubjectRequest(Base):
    """POPIA/GDPR rights handling (access/erasure/rectification/portability)."""
    __tablename__ = "data_subject_request"
    __table_args__ = {"schema": SCHEMA}

    id = Column(BigInteger, primary_key=True)
    club_id = _club_id(nullable=True)
    subject_person_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.person.id", ondelete="SET NULL"), nullable=True)
    requested_by_user_id = Column(BigInteger, ForeignKey(f"{SCHEMA}.app_user.id", ondelete="SET NULL"), nullable=True)
    request_type = Column(Text, nullable=False)       # access|erasure|rectification|portability
    status = Column(Text, nullable=False, server_default=text("'received'"))
    requested_at = _ts(nullable=False, server_default=_NOW)
    completed_at = _ts(nullable=True)
    notes = Column(Text, nullable=True)


class RetentionRule(Base):
    """Configurable retention windows; a sweep job applies them."""
    __tablename__ = "retention_rule"
    __table_args__ = (UniqueConstraint("data_class", "applies_after", name="uq_retention_class_after"),
                      {"schema": SCHEMA})

    id = Column(BigInteger, primary_key=True)
    data_class = Column(Text, nullable=False)         # account_pii|marketing|financial|...
    retention_days = Column(Integer, nullable=False)
    applies_after = Column(Text, nullable=False)      # account_closure|consent_withdrawal
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    notes = Column(Text, nullable=True)
    created_at = _ts(nullable=False, server_default=_NOW)
    updated_at = _ts(nullable=False, server_default=_NOW)

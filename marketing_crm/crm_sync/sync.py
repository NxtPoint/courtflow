# marketing_crm/crm_sync/sync.py — orchestration: core.* → Klaviyo (the marketing feed).
#
# core.* is the source of truth; Klaviyo is a one-way downstream mirror that drives lifecycle flows.
#
# enabled()        : self-gates on a destination key (KLAVIYO_API_KEY) — no key → silent no-op.
# build_traits()   : core.app_user → flat trait dict (owner/adult-level only; NO minor/child PII).
# sync_profile()   : fire-and-forget upsert of one account to Klaviyo.
# forward_event()  : forward a product event to Klaviyo, ENFORCING the transactional-vs-marketing
#                    gate (docs/06 §4). Called from the emit() thread — synchronous here.
# sync_all()       : batch upsert every account (nightly/manual via the cockpit ops endpoint).
#
# Ported from 1050 marketing_crm/crm_sync/sync.py, simplified to core.* (no billing view) +
# multi-tenant: every profile carries the `club` trait (decision D3 — one Klaviyo, many clubs).

import logging
import os
import threading

from sqlalchemy import select, text

from db import norm_email, session_scope
from core.models import AppUser
from marketing_crm.crm_sync import hubspot, klaviyo
from marketing_crm.tracking.events import is_transactional

log = logging.getLogger("marketing_crm.crm_sync")


def enabled():
    """Active only when a destination key is configured (self-gates; de-gated from a separate
    CRM_SYNC_ENABLED flag — faithful to 1050's 2026-06-17 decision). No key → clean no-op."""
    return bool(os.getenv("KLAVIYO_API_KEY")
                or os.getenv("HUBSPOT_PRIVATE_APP_TOKEN") or os.getenv("HUBSPOT_API_KEY"))


def build_traits(session, email, club_id=None):
    """core.app_user (owner/adult) → flat Klaviyo trait dict. Owner-level only — no child PII.
    `club` is the per-club segmentation trait. Returns None if the email is unknown to core.*
    (we still send a minimal profile on forward, so an unknown email is not fatal)."""
    email = norm_email(email)
    if not email:
        return None
    user = session.execute(
        select(AppUser).where(AppUser.email == email, AppUser.deleted_at.is_(None))
    ).scalar_one_or_none()
    traits = {"email": email, "club": (str(club_id) if club_id else None)}
    if user is not None:
        traits["marketing_opt_in"] = bool(user.marketing_opt_in)
        traits["club"] = traits["club"] or (str(user.club_id) if user.club_id else None)
    # Enrich from the People record (iam.user) for segmentation: name + the dormancy signal
    # (never_logged_in = clerk_user_id NULL → the imported-but-not-yet-activated cohort) + member state.
    try:
        iam = session.execute(text("""
            SELECT u.first_name, u.surname, u.clerk_user_id,
                   EXISTS (SELECT 1 FROM iam.membership m
                           WHERE m.user_id = u.id AND m.member_status = 'active') AS active_member
            FROM iam.user u WHERE lower(u.email) = :e ORDER BY u.created_at LIMIT 1
        """), {"e": email}).mappings().first()
        if iam:
            if iam["first_name"]:
                traits["first_name"] = iam["first_name"]
            if iam["surname"]:
                traits["last_name"] = iam["surname"]
            traits["never_logged_in"] = (iam["clerk_user_id"] is None)
            traits["member_status"] = "active" if iam["active_member"] else "inactive"
    except Exception:
        session.rollback()
        log.debug("build_traits: iam enrichment skipped for %s", email)
    return traits


def _marketing_opt_in(email):
    """Best-effort read of the adult contact's marketing_opt_in from core.app_user. Defaults False
    (fail-closed for marketing). Never raises."""
    try:
        email = norm_email(email)
        if not email:
            return False
        with session_scope() as s:
            user = s.execute(
                select(AppUser).where(AppUser.email == email, AppUser.deleted_at.is_(None))
            ).scalar_one_or_none()
            return bool(user and user.marketing_opt_in)
    except Exception:
        log.exception("crm_sync: opt-in lookup failed for %s", email)
        return False


def _push(traits):
    if not traits:
        return
    # Klaviyo is the ONLY active destination (we are our own CRM; Klaviyo is the marketing engine).
    # HubSpot stays dormant — upsert_contact() no-ops without a token (zero-cost escape hatch).
    try:
        klaviyo.upsert_profile(traits)
    except Exception:
        log.exception("klaviyo push failed")
    try:
        hubspot.upsert_contact(traits)  # dormant (no-op without a HUBSPOT token)
    except Exception:
        log.exception("hubspot push failed")


def sync_profile(email, club_id=None):
    """Fire-and-forget: upsert one account's profile to Klaviyo. Safe from request handlers."""
    if not enabled():
        return

    def _run():
        try:
            with session_scope() as s:
                traits = build_traits(s, email, club_id=club_id)
            _push(traits)
        except Exception:
            log.exception("sync_profile failed for %s", email)

    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        log.exception("sync_profile: thread spawn failed")


def forward_event(event_type, email, club_id=None, properties=None):
    """Forward a product event to Klaviyo (flow trigger). Enforces the send rule (docs/06 §4):
      - transactional events ALWAYS send (legitimate booking comms);
      - all other (marketing) events send ONLY when the adult contact's marketing_opt_in is true.
    Synchronous — call from a background context (emit() already runs on its own thread).
    Self-gates on KLAVIYO_API_KEY via enabled(); off-key is a clean no-op. Never raises."""
    if not enabled() or not email:
        return False
    try:
        if not is_transactional(event_type) and not _marketing_opt_in(email):
            # Marketing event without consent — suppress the Klaviyo send (the core.usage_event row
            # was already written by emit(); only the marketing forward is gated).
            log.debug("crm_sync: suppressed marketing event %s for %s (no opt-in)", event_type, email)
            return False
        props = dict(properties or {})
        if club_id is not None:
            props.setdefault("club", str(club_id))
        # Keep the profile's club trait fresh so segmentation works on first touch.
        try:
            klaviyo.upsert_profile({"email": email, "club": (str(club_id) if club_id else None)})
        except Exception:
            log.exception("forward_event: profile upsert failed for %s", email)
        return klaviyo.track_event(email, event_type, props)
    except Exception:
        log.exception("forward_event failed for %s/%s", event_type, email)
        return False


def sync_all(limit=5000):
    """Batch upsert every (non-deleted) core.app_user to Klaviyo. Returns count synced. For the
    cockpit's manual/nightly sync. No-op (0) when no destination key is set."""
    if not enabled():
        return 0
    n = 0
    with session_scope() as s:
        users = s.execute(
            select(AppUser.email, AppUser.club_id).where(AppUser.deleted_at.is_(None))
            .order_by(AppUser.id).limit(limit)
        ).all()
    for email, club_id in users:
        try:
            with session_scope() as s:
                traits = build_traits(s, email, club_id=club_id)
            _push(traits)
            n += 1
        except Exception:
            log.exception("sync_all: failed for %s", email)
    log.info("crm_sync.sync_all synced %d profiles", n)
    return n

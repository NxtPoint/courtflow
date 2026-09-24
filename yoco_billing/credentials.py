# yoco_billing/credentials.py — WHICH Yoco account a club's money goes into.
#
# Every club takes card payments into ITS OWN Yoco merchant account. The keys are Render env vars,
# like every other secret here (never in the DB, never in a backup):
#
#   the DEFAULT club (slug in YOCO_DEFAULT_CLUB, default "nextpoint")
#       YOCO_SECRET_KEY / YOCO_WEBHOOK_SECRET                      <- unchanged, NextPoint's
#   any other club, e.g. slug "acme-academy"
#       YOCO_SECRET_KEY__ACME_ACADEMY / YOCO_WEBHOOK_SECRET__ACME_ACADEMY
#
# THE RULE THIS FILE EXISTS FOR: a club with no keys of its own FAILS CLOSED. It never falls back
# to the default club's keys — that fallback would silently put one club's customers' money into
# another club's bank account, and nothing on any screen would show it.
#
# The club is a REQUIRED argument everywhere below, not a default: a money gate the caller has to
# remember is a gate in the wrong place (CLAUDE.md, the seat-rule gotchas).

from __future__ import annotations

import logging
import os
import re
from typing import Dict, Optional

log = logging.getLogger("yoco_billing.credentials")

_SLUG_BY_CLUB: Dict[str, str] = {}   # club_id -> slug; slugs never change, so cache per process


class YocoNotConfigured(Exception):
    """This club has no Yoco account configured — online payment must be refused, not rerouted."""


def default_club_slug() -> str:
    return (os.getenv("YOCO_DEFAULT_CLUB") or "nextpoint").strip().lower()


def _env_suffix(slug: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", (slug or "").strip().upper())


def env_names(slug: str):
    """(secret-key var, webhook-secret var) for a club slug — also what the error names, so the
    person setting it up on Render sees exactly what to add."""
    if (slug or "").strip().lower() == default_club_slug():
        return ("YOCO_SECRET_KEY", "YOCO_WEBHOOK_SECRET")
    sfx = _env_suffix(slug)
    return (f"YOCO_SECRET_KEY__{sfx}", f"YOCO_WEBHOOK_SECRET__{sfx}")


def slug_for_club(club_id) -> str:
    """club_id -> slug (cached). Raises YocoNotConfigured for an unknown club rather than guessing."""
    if not club_id:
        raise YocoNotConfigured("no club given for a Yoco call")
    cid = str(club_id)
    if cid in _SLUG_BY_CLUB:
        return _SLUG_BY_CLUB[cid]
    from sqlalchemy import text
    from db import session_scope
    with session_scope() as s:
        slug = s.execute(text("SELECT slug FROM club.club WHERE id = CAST(:c AS uuid)"),
                         {"c": cid}).scalar()
    if not slug:
        raise YocoNotConfigured(f"unknown club {cid}")
    _SLUG_BY_CLUB[cid] = str(slug)
    return _SLUG_BY_CLUB[cid]


def club_id_for_slug(session, slug: str) -> Optional[str]:
    from sqlalchemy import text
    cid = session.execute(text("SELECT id FROM club.club WHERE slug = :s"),
                          {"s": (slug or "").strip().lower()}).scalar()
    return str(cid) if cid else None


def secret_key_for_slug(slug: str) -> str:
    var = env_names(slug)[0]
    k = (os.getenv(var) or "").strip()
    if not k:
        raise YocoNotConfigured(f"{var} not configured")
    return k


def webhook_secret_for_slug(slug: str) -> str:
    """Empty string when unset — the signature check then fails closed."""
    return (os.getenv(env_names(slug)[1]) or "").strip()


def public_key_for_club(club_id) -> str:
    """The club's own public key, or "" — the same naming as the secret (YOCO_PUBLIC_KEY[__SLUG])."""
    slug = slug_for_club(club_id)
    var = "YOCO_PUBLIC_KEY" if slug == default_club_slug() else f"YOCO_PUBLIC_KEY__{_env_suffix(slug)}"
    return (os.getenv(var) or "").strip()


def secret_key_for_club(club_id) -> str:
    return secret_key_for_slug(slug_for_club(club_id))

# core — own-CRM (ported from 1050 core_db), the canonical event/identity/consent store.
# account/user/person/usage_event/consent/nps, multi-tenant (club_id added per docs/02 §6).
from core.schema import init  # noqa: F401

__all__ = ["init"]

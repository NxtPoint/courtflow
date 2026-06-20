# core/db.py — session + serialization helpers for the core.* data-access layer.
#
# Ported from 1050 core_db/db.py. Repository functions take an explicit `session` and
# never commit, so callers compose transactions via session_scope() (re-exported from
# the platform-wide db module so there is ONE engine/session_scope for the whole app).

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import inspect

# Single source of truth for engine + transactional scope (platform-wide).
from db import get_engine, norm_email, session_scope  # noqa: F401

__all__ = ["get_engine", "session_scope", "norm_email", "as_dict"]


def as_dict(obj):
    """Serialize an ORM row to a JSON-friendly dict (uuid/datetime/date/Decimal -> str/float)."""
    if obj is None:
        return None
    out = {}
    for col in inspect(obj).mapper.column_attrs:
        key = col.key
        val = getattr(obj, key)
        if isinstance(val, UUID):
            val = str(val)
        elif isinstance(val, (datetime, date)):
            val = val.isoformat()
        elif isinstance(val, Decimal):
            val = float(val)
        out[key] = val
    return out

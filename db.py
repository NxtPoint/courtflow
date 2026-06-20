# db.py — Database engine, session scope, and the idempotent boot-schema runner.
#
# Ported from 1050's `db_init.py` (engine) + `core_db/db.py` (session_scope), stripped
# of all bronze/silver/gold/ML content. This is the single connection point for the
# whole platform and the orchestrator that runs every schema module's init() on boot.
#
# Design (matches 1050):
#   - DATABASE_URL is normalized to the psycopg v3 driver (postgres:// -> postgresql+psycopg://).
#   - The engine is built LAZILY (get_engine()) so importing this module never forces a
#     DB connection — important because much of the app must import cleanly with no DB
#     (CI, py_compile, the Phase-0 selftest).
#   - pool_pre_ping + pool_recycle=1800 for Render's connection lifecycle.
#   - run_boot_init() enables the pgcrypto + btree_gist extensions, then calls each
#     registered module's init(engine). Every init() is idempotent (CREATE ... IF NOT
#     EXISTS / ADD COLUMN IF NOT EXISTS), so running it twice is a no-op. NO migrations.
#
# Each schema module (club, iam, core, and later diary/billing) exposes:
#     def init(engine=None): ...
# and is registered in BOOT_MODULES below. Order matters: club before iam/core (FKs).

import logging
import os
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

log = logging.getLogger("db")

# Lazily-built shared engine.
_engine = None


def _database_url():
    """Read + normalize DATABASE_URL to the psycopg v3 driver. Raises only when an
    engine is actually requested (not at import time)."""
    url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or os.getenv("DB_URL")
    if not url:
        raise RuntimeError("DATABASE_URL (or POSTGRES_URL / DB_URL) env var is required.")
    # Render/Heroku give postgres:// — SQLAlchemy 2 wants postgresql://, and we force psycopg v3.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://") and "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def get_engine():
    """Return the shared SQLAlchemy engine, building it on first use. Lazy so importing
    this module is side-effect free (no connection, no DATABASE_URL requirement)."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            _database_url(),
            pool_pre_ping=True,
            pool_recycle=1800,   # keep connections fresh on Render
            future=True,
        )
    return _engine


@contextmanager
def session_scope():
    """Transactional scope: commits on success, rolls back on error, always closes.
    Repository functions take an explicit `session` and never commit themselves, so
    callers compose multi-step writes atomically (ported from 1050 core_db.db)."""
    s = Session(get_engine())
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def norm_email(email):
    """Canonicalize an email for storage / comparison (lowercased, stripped)."""
    return (email or "").strip().lower() or None


# ---------------------------------------------------------------------------
# Boot schema runner
# ---------------------------------------------------------------------------
# Each entry is the dotted import path of a module exposing init(engine=None).
# ORDER MATTERS: club first (other schemas FK to club.club), then iam + core.
# Agents B (diary) and C (billing) append their modules here when their schemas land.
BOOT_MODULES = [
    "club.schema",
    "iam.schema",
    "core.schema",
    "billing.schema",  # Agent C — products/prices/orders/payments/ledger
    "diary.schema",    # Agent B — resources/bookings (GiST exclusion)/classes; reads billing.price
]

# Extensions every schema depends on. pgcrypto -> gen_random_uuid(); btree_gist ->
# the diary's no-double-booking exclusion constraint (Agent B). Enabling now is
# harmless and idempotent.
_EXTENSIONS = (
    "CREATE EXTENSION IF NOT EXISTS pgcrypto;",
    "CREATE EXTENSION IF NOT EXISTS btree_gist;",
)


def enable_extensions(engine=None):
    """Enable required Postgres extensions. Idempotent."""
    engine = engine or get_engine()
    with engine.begin() as conn:
        for stmt in _EXTENSIONS:
            conn.execute(text(stmt))


def run_boot_init(engine=None, modules=None):
    """Enable extensions, then run every registered module's init() in order.
    Idempotent end-to-end (safe to call on every boot, twice in a row = no error).

    Each module is imported lazily and init()'d inside its own try/except so a single
    failing module can't stop the others from booting (1050 boot discipline). Returns
    a list of (module, "ok"|"error") tuples for logging/verification."""
    engine = engine or get_engine()
    results = []

    try:
        enable_extensions(engine)
        results.append(("_extensions", "ok"))
    except Exception:
        log.exception("enable_extensions() failed on boot")
        results.append(("_extensions", "error"))

    for mod_path in (modules if modules is not None else BOOT_MODULES):
        try:
            mod = __import__(mod_path, fromlist=["init"])
            mod.init(engine)
            results.append((mod_path, "ok"))
            log.info("boot init ok: %s", mod_path)
        except Exception:
            log.exception("boot init FAILED: %s", mod_path)
            results.append((mod_path, "error"))

    return results


if __name__ == "__main__":
    # Manual run: `python -m db`  (requires DATABASE_URL). Additive + safe.
    logging.basicConfig(level=logging.INFO)
    res = run_boot_init()
    eng = get_engine()
    print(f"boot init on {eng.url.render_as_string(hide_password=True)}")
    for name, status in res:
        print(f"  {status:6} {name}")

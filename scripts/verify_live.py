# scripts/verify_live.py — one-time live-DB verification. Reads DATABASE_URL from a
# gitignored .env.local (never printed) and proves the platform boots + seeds against
# the real Postgres. Safe to re-run (everything is idempotent). Prints status only.
#
#   .venv/Scripts/python -m scripts.verify_live
#
import os
import sys
from pathlib import Path


def _load_env_local():
    """Load DATABASE_URL (+ any other keys) from .env.local without echoing the value."""
    f = Path(__file__).resolve().parent.parent / ".env.local"
    if not f.exists():
        print("!! .env.local not found — create it with a single line:\n"
              "   DATABASE_URL=postgresql://courtflow:...@...render.com/courtflow")
        sys.exit(2)
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    if not os.environ.get("DATABASE_URL"):
        print("!! .env.local has no DATABASE_URL line")
        sys.exit(2)


def main():
    _load_env_local()
    import db
    from sqlalchemy import text

    ok = True
    # 1) Boot all schemas twice — the idempotency gate.
    print("== boot init (pass 1) ==")
    r1 = db.run_boot_init()
    print("   " + ", ".join(f"{m}={s}" for m, s in r1))
    print("== boot init (pass 2 — must be a clean no-op) ==")
    r2 = db.run_boot_init()
    print("   " + ", ".join(f"{m}={s}" for m, s in r2))
    if any(s != "ok" for _, s in r1 + r2):
        print("   FAIL: a schema module errored"); ok = False
    else:
        print("   ok: all six schemas boot idempotently")

    # Connection target (password masked) — confirms WHICH db without leaking secrets.
    eng = db.get_engine()
    print(f"== connected: {eng.url.render_as_string(hide_password=True)} ==")

    # 2) Seed NextPoint (idempotent) + count what landed.
    print("== seed NextPoint (idempotent) ==")
    try:
        from scripts import seed_nextpoint
        seed_nextpoint.main() if hasattr(seed_nextpoint, "main") else seed_nextpoint.seed()
    except SystemExit:
        pass
    except Exception as e:
        print(f"   seed raised: {e.__class__.__name__}: {e}"); ok = False

    with db.session_scope() as s:
        def count(q):
            try:
                return s.execute(text(q)).scalar_one()
            except Exception:
                return "n/a"
        print(f"   clubs={count('select count(*) from club.club')} "
              f"locations={count('select count(*) from club.location')} "
              f"resources={count('select count(*) from diary.resource')} "
              f"coach_profiles={count('select count(*) from iam.coach_profile')} "
              f"products={count('select count(*) from billing.product')} "
              f"prices={count('select count(*) from billing.price')}")

    # 3) Extension + constraint sanity (the diary's safety net).
    with db.session_scope() as s:
        exts = [r[0] for r in s.execute(text(
            "select extname from pg_extension where extname in ('pgcrypto','btree_gist')"))]
        print(f"== extensions: {sorted(exts)} ==")
        if sorted(exts) != ["btree_gist", "pgcrypto"]:
            print("   FAIL: required extensions missing"); ok = False
        excl = s.execute(text(
            "select conname from pg_constraint where contype='x' and conrelid='diary.booking'::regclass"
        )).first()
        print(f"== no-double-book exclusion constraint present: {bool(excl)} ==")
        if not excl:
            print("   FAIL: diary.booking exclusion constraint missing"); ok = False

    print("\n=== LIVE VERIFY: " + ("PASS — DB ready" if ok else "FAIL") + " ===")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

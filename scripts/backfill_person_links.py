# scripts/backfill_person_links.py — Client-360 Slice-0, Step 3: forward-create the core.person
# CRM satellites for every iam.user member and link them (core.person.iam_user_id).
#
# DRY-RUN BY DEFAULT — prints exactly what it WOULD do and persists nothing. Pass --commit to write.
# Idempotent + resumable: an already-linked user is a cheap no-op (link_person_for_user returns the
# existing satellite). See docs/specs/CLIENT-360-CRM-PLAN.md §10 Step 3.
#
#   .venv/Scripts/python -m scripts.backfill_person_links                 # DRY-RUN, no name split
#   .venv/Scripts/python -m scripts.backfill_person_links --split-names   # DRY-RUN + preview name split
#   .venv/Scripts/python -m scripts.backfill_person_links --split-names --commit   # WRITE
#   .venv/Scripts/python -m scripts.backfill_person_links --limit 20 --commit      # first small batch
#
# Reads DATABASE_URL from the environment (Render shell) or a gitignored .env.local. Never prints it.
#
# Scope: members (iam.user with a membership + an email). The 9 login-less dependents (NULL email)
# are DEFERRED — they attach to the guardian's core.account via a separate follow-up, not here.

import argparse
import os
import sys
from pathlib import Path


def _load_env_local():
    if os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("DB_URL"):
        return
    f = Path(__file__).resolve().parent.parent / ".env.local"
    if not f.exists():
        print("!! No DATABASE_URL in env and no .env.local found. Run on the Render shell, or\n"
              "   create .env.local with: DATABASE_URL=postgresql://courtflow:...@...render.com/courtflow")
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


def _hdr(t):
    print(f"\n== {t} " + "=" * max(0, 60 - len(t)))


def _row(label, value, width=44):
    print(f"   {label:<{width}} {value}")


def _pct(n, d):
    return f"{(100.0 * n / d):5.1f}%" if d else "  n/a"


# Members = iam.user with a membership + a usable email. club_id = their earliest membership's club.
_MEMBERS_SQL = """
SELECT u.id, u.email, u.first_name, u.surname, u.phone, u.marketing_opt_in,
       (SELECT m.club_id FROM iam.membership m WHERE m.user_id = u.id
          ORDER BY m.joined_at LIMIT 1) AS club_id
FROM iam.user u
WHERE EXISTS (SELECT 1 FROM iam.membership m WHERE m.user_id = u.id)
  AND u.email IS NOT NULL AND btrim(u.email) <> ''
ORDER BY u.created_at
{limit}
"""

# Surname split: rows with a blank surname whose first_name is "<first...> <surname>".
_SPLIT_WHERE = (
    "WHERE (u.surname IS NULL OR btrim(u.surname) = '') "
    "AND u.first_name ~ '\\S\\s+\\S' "
    "AND EXISTS (SELECT 1 FROM iam.membership m WHERE m.user_id = u.id)"
)
_SPLIT_COUNT = f"SELECT count(*) FROM iam.user u {_SPLIT_WHERE}"
_SPLIT_SAMPLES = f"""
SELECT u.first_name AS before_first,
       btrim(substring(u.first_name from '^(.*\\S)\\s+\\S+$')) AS after_first,
       btrim(substring(u.first_name from '\\S+$'))            AS after_surname
FROM iam.user u {_SPLIT_WHERE}
ORDER BY u.first_name LIMIT 8
"""
# The RHS of every SET is evaluated against the OLD row, so first_name/surname derive cleanly.
_SPLIT_UPDATE = f"""
UPDATE iam.user AS u SET
    surname    = btrim(substring(u.first_name from '\\S+$')),
    first_name = btrim(substring(u.first_name from '^(.*\\S)\\s+\\S+$')),
    updated_at = now()
{_SPLIT_WHERE}
"""

_COMPLETE_SQL = """
SELECT
  count(*) AS pop,
  count(*) FILTER (WHERE u.first_name IS NOT NULL AND btrim(u.first_name) <> ''
                    AND u.surname   IS NOT NULL AND btrim(u.surname)   <> ''
                    AND u.phone     IS NOT NULL AND btrim(u.phone)     <> ''
                    AND u.email     IS NOT NULL AND btrim(u.email)     <> '') AS complete
FROM iam.user u
WHERE EXISTS (SELECT 1 FROM iam.membership m WHERE m.user_id = u.id)
"""


def main():
    ap = argparse.ArgumentParser(description="Backfill core.person satellites for iam.user members.")
    ap.add_argument("--commit", action="store_true", help="persist changes (default: dry-run)")
    ap.add_argument("--split-names", action="store_true",
                    help="also split '<first> <surname>' from first_name where surname is blank")
    ap.add_argument("--limit", type=int, default=None, help="process only the first N members (testing)")
    args = ap.parse_args()

    _load_env_local()
    import db
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from core.repositories.persons import link_person_for_user

    commit = args.commit
    s = Session(db.get_engine())
    try:
        eng = db.get_engine()
        _hdr(f"BACKFILL person links — {'COMMIT (writes)' if commit else 'DRY-RUN (no writes persisted)'}")
        _row("connected", eng.url.render_as_string(hide_password=True))

        before = s.execute(text(_COMPLETE_SQL)).mappings().one()
        persons_before = s.execute(text("SELECT count(*) FROM core.person")).scalar_one()
        _row("members", before["pop"])
        _row("completeness (before)", f"{before['complete']}/{before['pop']}  ({_pct(before['complete'], before['pop'])})")
        _row("core.person rows (before)", persons_before)

        # --- Pass 0: surname split -------------------------------------------
        if args.split_names:
            _hdr("Pass 0 — surname split")
            cand = s.execute(text(_SPLIT_COUNT)).scalar_one()
            _row("candidates (surname blank + first_name has a space)", cand)
            for r in s.execute(text(_SPLIT_SAMPLES)).mappings().all():
                _row(f"  '{r['before_first']}'", f"-> first='{r['after_first']}'  surname='{r['after_surname']}'")
            res = s.execute(text(_SPLIT_UPDATE))
            _row("rows the split would update" if not commit else "rows updated", res.rowcount)
        else:
            _row("surname split", "skipped (pass --split-names to enable)")

        # --- Pass 1: forward-create + link satellites ------------------------
        _hdr("Pass 1 — forward-create core.person satellites")
        limit_clause = f"LIMIT {int(args.limit)}" if args.limit else ""
        rows = s.execute(text(_MEMBERS_SQL.format(limit=limit_clause))).mappings().all()
        processed = 0
        for r in rows:
            link_person_for_user(
                s,
                iam_user_id=r["id"], club_id=r["club_id"], email=r["email"],
                first_name=r["first_name"], surname=r["surname"], phone=r["phone"],
                marketing_opt_in=r["marketing_opt_in"],
            )
            processed += 1

        persons_after = s.execute(text("SELECT count(*) FROM core.person")).scalar_one()
        created = persons_after - persons_before
        _row("members processed", processed)
        _row("satellites created (new)", created)
        _row("already-linked (resume no-op)", max(0, processed - created))
        _row("core.person rows (after)", persons_after)

        # --- Pass 2: dependents (deferred) -----------------------------------
        _hdr("Pass 2 — dependents (DEFERRED to guardian-attach follow-up)")
        deps = s.execute(text("SELECT count(*) FROM iam.dependent")).scalar_one()
        _row("login-less dependents skipped", deps)

        # --- Result snapshot -------------------------------------------------
        after = s.execute(text(_COMPLETE_SQL)).mappings().one()
        _hdr("RESULT")
        _row("completeness (after)",
             f"{after['complete']}/{after['pop']}  ({_pct(after['complete'], after['pop'])})")
        _row("completeness lift",
             f"+{after['complete'] - before['complete']} members "
             f"({_pct(after['complete'], after['pop'])} vs {_pct(before['complete'], before['pop'])})")

        if commit:
            s.commit()
            print("\n>>> COMMITTED — changes persisted.\n")
        else:
            s.rollback()
            print("\n>>> DRY-RUN — rolled back. Nothing was written. Re-run with --commit to apply.\n")
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()

    sys.exit(0)


if __name__ == "__main__":
    main()

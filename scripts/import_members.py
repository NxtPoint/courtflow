# scripts/import_members.py — friendly one-shot: import the cleaned Wix clients as ACTIVE members.
#
# For Tomo to run straight from PowerShell, no env-var juggling:
#     python scripts/import_members.py
#
# What it does:
#   1) loads the target DATABASE_URL (from .env.local, or the DATABASE_URL env var),
#   2) DRY-RUNs the import and shows you the counts (nothing written),
#   3) asks you to type YES,
#   4) COMMITS to that database,
#   5) verifies the member count + confirms NO 7-day trials were granted.
#
# Idempotent — safe to run again later (e.g. when your memberships/lessons files land).
# Grants NO signup trial: the importer never does, and the pre-created member row also
# suppresses the first-login trial (auth/principal.py only trials users with no membership).
#
# THE DATABASE URL — nothing to store on disk. When you run it and no URL is configured, it
# asks you to PASTE the connection string into a HIDDEN prompt (getpass): held only in memory
# for this run, never written to disk, never echoed, never in shell history. (Get it from
# Render -> your Postgres -> 'External Connection String'.)
#   Optional conveniences if you PREFER them (not required): a gitignored .env.local with a
#   DATABASE_URL= line, or a DATABASE_URL env var. The secure paste-prompt is the default.

import argparse
import io
import os
import sys
import urllib.parse

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Allow running as a loose file (python scripts/import_members.py) — put the repo root on the
# import path so `db` and `scripts.import_wix` resolve.
if REPO not in sys.path:
    sys.path.insert(0, REPO)
DEFAULT_CSV = r"C:\Users\tomos\OneDrive\Documentos\Next Point Tennis\Migration\clients.csv"


def _load_database_url():
    """Return (url, source). Prefers .env.local (the explicit prod pointer) over a stray env var,
    so this script targets PROD by default and doesn't accidentally hit a dev shell's DATABASE_URL."""
    envf = os.path.join(REPO, ".env.local")
    if os.path.isfile(envf):
        for line in io.open(envf, encoding="utf-8"):
            line = line.strip()
            if line.startswith("DATABASE_URL=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'"), ".env.local"
    if os.getenv("DATABASE_URL"):
        return os.getenv("DATABASE_URL"), "DATABASE_URL env var"
    return None, None


def _mask(url):
    """Host + db name only — never the password."""
    try:
        p = urllib.parse.urlparse(url)
        return "%s / %s" % (p.hostname or "?", (p.path or "").lstrip("/") or "?")
    except Exception:
        return "(unparseable url)"


def main():
    ap = argparse.ArgumentParser(description="Import cleaned Wix clients as active members.")
    ap.add_argument("csv", nargs="?", default=DEFAULT_CSV, help="path to clients.csv")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--dry-run", action="store_true", help="show counts only, never write")
    args = ap.parse_args()

    url, src = _load_database_url()
    if not url:
        # Nothing on disk / in the env — ask for it securely. getpass hides what you paste; the
        # string stays ONLY in this process's memory for this run: never written to disk, never
        # echoed to the screen, never saved in shell history. This is the safest way.
        import getpass
        print("No .env.local or DATABASE_URL set — that's fine, paste it securely below.")
        print("(Hidden as you paste. Used only for this run. NOT saved to disk or shell history.)")
        print("Get it from Render -> your Postgres -> 'External Connection String'.\n")
        try:
            url = getpass.getpass("Paste prod DATABASE_URL, then press Enter: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled."); return 1
        src = "entered now (not stored anywhere)"
    if not url:
        print("No URL provided — aborting."); return 2
    if not os.path.isfile(args.csv):
        print("ERROR: clients CSV not found: %s" % args.csv)
        return 2

    # Set the engine's URL BEFORE importing db/import_wix (the engine is built lazily on first use).
    os.environ["DATABASE_URL"] = url
    from scripts import import_wix

    print("=" * 64)
    print("  Import Wix clients -> active members")
    print("=" * 64)
    print("  Target DB : %s   (from %s)" % (_mask(url), src))
    print("  Clients   : %s" % args.csv)
    print("=" * 64)

    print("\nSTEP 1 - DRY RUN (nothing written):")
    import_wix.run(directory="migration/wix", clients_path=args.csv, members_path=None,
                   plans_path=None, club_slug="nextpoint", commit=False)

    if args.dry_run:
        print("Dry-run only (--dry-run). Nothing written.")
        return 0

    if not args.yes:
        print("This will WRITE the above members to:  %s" % _mask(url))
        ans = input("Type YES to proceed (anything else cancels): ").strip()
        if ans != "YES":
            print("Cancelled - nothing written.")
            return 1

    print("\nSTEP 2 - COMMITTING...")
    import_wix.run(directory="migration/wix", clients_path=args.csv, members_path=None,
                   plans_path=None, club_slug="nextpoint", commit=True)

    print("\nSTEP 3 - verifying...")
    from db import session_scope
    from sqlalchemy import text
    with session_scope() as s:
        members = s.execute(text("SELECT count(*) FROM iam.membership "
                                 "WHERE role='member' AND member_status='active'")).scalar()
        trials = s.execute(text("SELECT count(*) FROM billing.membership_subscription "
                                "WHERE provider='trial'")).scalar()
    print("  active members now  : %s" % members)
    print("  trial subscriptions : %s  (import grants none)" % trials)
    print("\nDone. Safe to re-run anytime (idempotent).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

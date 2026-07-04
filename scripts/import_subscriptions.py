# scripts/import_subscriptions.py — import the paid MEMBERSHIPS (members.csv) as active subscriptions.
#
# Run:  python scripts/import_subscriptions.py
#
# Reads members.csv (email, plan, expiry) and grants each member their membership by MATCHING the
# Wix plan name to the club plan you created in the frontend (by label). It:
#   1) loads the target DATABASE_URL (secure hidden prompt if none is set — nothing stored on disk),
#   2) DRY-RUNs and prints the "Wix plan -> matched club plan" map (verify names line up; NO MATCH is
#      flagged with <<< so a mismatch is obvious),
#   3) asks you to type YES,
#   4) COMMITS, then verifies the active-membership count.
#
# Idempotent. Grants provider='manual' subscriptions with each member's exact Wix expiry. Emails must
# already exist as members (run import_members.py first). MUST be run AFTER you've created the plans
# in the frontend with names identical to the Wix plan names.

import argparse
import io
import os
import sys
import urllib.parse

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
DEFAULT_CSV = r"C:\Users\tomos\OneDrive\Documentos\Next Point Tennis\Migration\members.csv"


def _load_database_url():
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
    try:
        p = urllib.parse.urlparse(url)
        return "%s / %s" % (p.hostname or "?", (p.path or "").lstrip("/") or "?")
    except Exception:
        return "(unparseable url)"


def main():
    ap = argparse.ArgumentParser(description="Import Wix memberships as active subscriptions.")
    ap.add_argument("csv", nargs="?", default=DEFAULT_CSV, help="path to members.csv")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--dry-run", action="store_true", help="show the plan-match map only, never write")
    args = ap.parse_args()

    url, src = _load_database_url()
    if not url:
        import getpass
        print("No .env.local or DATABASE_URL set — paste it securely below.")
        print("(Hidden as you paste. Used only for this run. NOT saved to disk or shell history.)")
        print("Get it from Render -> your Postgres -> 'External Connection String'.\n")
        try:
            url = getpass.getpass("Paste prod DATABASE_URL, then press Enter: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled."); return 1
        src = "entered now (not stored anywhere)"
    if not url:
        print("No URL provided - aborting."); return 2
    if not os.path.isfile(args.csv):
        print("ERROR: members CSV not found: %s" % args.csv); return 2

    os.environ["DATABASE_URL"] = url
    from scripts import import_wix

    print("=" * 64)
    print("  Import Wix memberships -> active subscriptions")
    print("=" * 64)
    print("  Target DB : %s   (from %s)" % (_mask(url), src))
    print("  Members   : %s" % args.csv)
    print("=" * 64)

    print("\nSTEP 1 - DRY RUN (nothing written). CHECK the plan-match map below:")
    import_wix.run(directory="migration/wix", clients_path=None, members_path=args.csv,
                   plans_path=None, club_slug="nextpoint", commit=False)

    if args.dry_run:
        print("Dry-run only (--dry-run). Nothing written.")
        return 0

    print("If any plan shows 'NO MATCH <<<', STOP: fix the name in the frontend or the CSV, then re-run.")
    if not args.yes:
        ans = input("Type YES to write these memberships to %s: " % _mask(url)).strip()
        if ans != "YES":
            print("Cancelled - nothing written."); return 1

    print("\nSTEP 2 - COMMITTING...")
    import_wix.run(directory="migration/wix", clients_path=None, members_path=args.csv,
                   plans_path=None, club_slug="nextpoint", commit=True)

    print("\nSTEP 3 - verifying...")
    from db import session_scope
    from sqlalchemy import text
    with session_scope() as s:
        active = s.execute(text("SELECT count(*) FROM billing.membership_subscription "
                                "WHERE status='active'")).scalar()
        manual = s.execute(text("SELECT count(*) FROM billing.membership_subscription "
                                "WHERE status='active' AND provider='manual'")).scalar()
    print("  active memberships now : %s  (of which manual/imported: %s)" % (active, manual))
    print("\nDone. Safe to re-run anytime (idempotent).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

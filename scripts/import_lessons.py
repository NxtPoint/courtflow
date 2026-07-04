# scripts/import_lessons.py — import Wix lesson packs as coach-specific token wallets (take-on).
#
# Run:  python scripts/import_lessons.py
#
# Reads lessons.csv (email, lesson_plan, sessions_total, sessions_remaining, duration_minutes,
# coach_email, expiry) and creates one MINUTE-based token_wallet per pack, with the coach set so the
# balance only draws against THAT coach's lessons. A member may hold several packs. It:
#   1) loads the target DATABASE_URL (secure hidden prompt; refuses a LOCAL db for a prod import),
#   2) DRY-RUNs and lists each wallet (member remaining/total x length coach),
#   3) asks you to type YES,
#   4) COMMITS, then verifies the lesson-wallet count.
#
# Idempotent (an existing matching wallet is left untouched). Emails must already exist as members
# and the coaches must already be invited (run import_members.py + invite coaches first).

import argparse
import io
import os
import sys
import urllib.parse

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
DEFAULT_CSV = r"C:\Users\tomos\OneDrive\Documentos\Next Point Tennis\Migration\lessons.csv"


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


def _is_local_host(url):
    try:
        h = (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        return True
    return h in ("localhost", "127.0.0.1", "::1", "")


def main():
    ap = argparse.ArgumentParser(description="Import Wix lesson packs as coach-specific token wallets.")
    ap.add_argument("csv", nargs="?", default=DEFAULT_CSV, help="path to lessons.csv")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--dry-run", action="store_true", help="list the wallets only, never write")
    args = ap.parse_args()

    url, src = _load_database_url()
    if url and _is_local_host(url):
        print("!! DATABASE_URL from %s points at a LOCAL database (%s)." % (src, _mask(url)))
        print("!! Ignoring it — a PRODUCTION import must not hit your local dev DB.")
        print("!! You'll be asked to paste the real prod (Render External) URL instead.\n")
        url, src = None, None
    if not url:
        import getpass
        print("Paste the prod DATABASE_URL securely below (hidden; not saved to disk or history).")
        print("Get it from Render -> your Postgres -> 'External Connection String'.\n")
        try:
            url = getpass.getpass("Paste prod DATABASE_URL, then press Enter: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled."); return 1
        src = "entered now (not stored anywhere)"
    if not url:
        print("No URL provided - aborting."); return 2
    if not os.path.isfile(args.csv):
        print("ERROR: lessons CSV not found: %s" % args.csv); return 2

    os.environ["DATABASE_URL"] = url
    from scripts import import_wix

    print("=" * 64)
    print("  Import Wix lesson packs -> coach token wallets")
    print("=" * 64)
    print("  Target DB : %s   (from %s)" % (_mask(url), src))
    print("  Lessons   : %s" % args.csv)
    print("=" * 64)

    print("\nSTEP 1 - DRY RUN (nothing written). Check each wallet below:")
    import_wix.run(directory="migration/wix", clients_path=None, members_path=None,
                   plans_path=None, lessons_path=args.csv, club_slug="nextpoint", commit=False)

    if args.dry_run:
        print("Dry-run only (--dry-run). Nothing written.")
        return 0

    print("If any row shows SKIP or a coach 'not found', STOP and tell me.")
    if not args.yes:
        ans = input("Type YES to write these lesson wallets to %s: " % _mask(url)).strip()
        if ans != "YES":
            print("Cancelled - nothing written."); return 1

    print("\nSTEP 2 - COMMITTING...")
    import_wix.run(directory="migration/wix", clients_path=None, members_path=None,
                   plans_path=None, lessons_path=args.csv, club_slug="nextpoint", commit=True)

    print("\nSTEP 3 - verifying...")
    from db import session_scope
    from sqlalchemy import text
    with session_scope() as s:
        n = s.execute(text("SELECT count(*) FROM billing.token_wallet "
                           "WHERE service_kind='lesson' AND status='active'")).scalar()
        mins = s.execute(text("SELECT COALESCE(sum(minutes_remaining),0) FROM billing.token_wallet "
                              "WHERE service_kind='lesson' AND status='active'")).scalar()
    print("  active lesson wallets now : %s  (%s minutes remaining total)" % (n, mins))
    print("\nDone. Safe to re-run anytime (idempotent).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

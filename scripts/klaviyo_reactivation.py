# scripts/klaviyo_reactivation.py — sync the DORMANT, opted-in member cohort to Klaviyo so a
# win-back flow/campaign can target them (Mission 2.0). Measure-first + cost-conscious: it reports
# the cohort sizes and only pushes the safely-reachable (opted-in) dormant members, not all 911.
#
# DRY-RUN BY DEFAULT — reports counts, pushes NOTHING. Pass --commit to upsert profiles to Klaviyo.
#
#   .venv/Scripts/python -m scripts.klaviyo_reactivation            # report cohort, no push
#   .venv/Scripts/python -m scripts.klaviyo_reactivation --commit   # upsert the opted-in dormant
#   .venv/Scripts/python -m scripts.klaviyo_reactivation --limit 25 --commit   # first small batch
#
# "Dormant" = a member who has NEVER logged into the new platform (iam.user.clerk_user_id IS NULL —
# the imported-but-not-activated cohort). "Reachable" = dormant AND marketing_opt_in (POPIA: only
# opted-in contacts get a marketing win-back). Requires KLAVIYO_API_KEY set (crm_sync self-gates);
# without it the report still runs (DB only) and --commit is a no-op with a clear message.
#
# Reads DATABASE_URL from the environment (Render shell) or a gitignored .env.local.

import argparse
import os
import sys
from pathlib import Path


def _load_env_local():
    if os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("DB_URL"):
        return
    f = Path(__file__).resolve().parent.parent / ".env.local"
    if not f.exists():
        print("!! No DATABASE_URL in env and no .env.local found. Run on the Render shell, or create\n"
              "   .env.local with: DATABASE_URL=postgresql://courtflow:...@...render.com/courtflow")
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


# Members who have never logged into the new platform (clerk_user_id NULL) — the dormant cohort.
_MEMBER = ("FROM iam.user u WHERE u.clerk_user_id IS NULL "
           "AND u.email IS NOT NULL AND btrim(u.email) <> '' "
           "AND EXISTS (SELECT 1 FROM iam.membership m WHERE m.user_id = u.id)")

_COHORT_SQL = f"""
SELECT u.email,
       (SELECT m.club_id FROM iam.membership m WHERE m.user_id = u.id
          ORDER BY m.joined_at LIMIT 1) AS club_id
{_MEMBER} AND u.marketing_opt_in = true
ORDER BY u.created_at
{{limit}}
"""


def main():
    ap = argparse.ArgumentParser(description="Sync the dormant opted-in member cohort to Klaviyo.")
    ap.add_argument("--commit", action="store_true", help="push profiles to Klaviyo (default: report only)")
    ap.add_argument("--limit", type=int, default=None, help="push only the first N (testing)")
    args = ap.parse_args()

    _load_env_local()
    import db
    from sqlalchemy import text
    from marketing_crm.crm_sync import sync as SYNC

    with db.session_scope() as s:
        def scalar(q):
            return s.execute(text(q)).scalar_one()
        total_members = scalar("SELECT count(DISTINCT m.user_id) FROM iam.membership m")
        dormant = scalar(f"SELECT count(*) {_MEMBER}")
        dormant_optin = scalar(f"SELECT count(*) {_MEMBER} AND u.marketing_opt_in = true")
        dormant_nooptin = dormant - dormant_optin

    _hdr("KLAVIYO REACTIVATION — cohort")
    _row("members (total)", total_members)
    _row("dormant (never logged into new platform)", f"{dormant}  ({_pct(dormant, total_members)})")
    _row("  reachable NOW (dormant + opted-in)", f"{dormant_optin}  <- marketing win-back cohort")
    _row("  dormant but NOT opted-in", f"{dormant_nooptin}  (needs a service/migration notice, not marketing)")

    key_set = SYNC.enabled()
    _hdr("Klaviyo")
    _row("KLAVIYO_API_KEY present", "yes" if key_set else "NO — set it in Render to enable pushing")
    _row("mode", "COMMIT (push)" if args.commit else "DRY-RUN (report only)")

    if not args.commit:
        print("\n>>> DRY-RUN — nothing pushed. Re-run with --commit (and KLAVIYO_API_KEY set) to sync.\n")
        sys.exit(0)
    if not key_set:
        print("\n!! --commit given but KLAVIYO_API_KEY is not set — nothing pushed. Set the key first.\n")
        sys.exit(1)

    # Push the opted-in dormant cohort (enriched traits via build_traits: name + club +
    # never_logged_in + member_status → segmentable in Klaviyo).
    limit_clause = f"LIMIT {int(args.limit)}" if args.limit else ""
    with db.session_scope() as s:
        rows = s.execute(text(_COHORT_SQL.format(limit=limit_clause))).mappings().all()
    pushed, emails = 0, []
    for r in rows:
        try:
            with db.session_scope() as s:
                traits = SYNC.build_traits(s, r["email"], club_id=r["club_id"])
            SYNC._push(traits)
            emails.append(r["email"])
            pushed += 1
        except Exception as e:
            print(f"   !! {r['email']}: {e.__class__.__name__}")

    # Subscribe the cohort to a list WITH consent. API-imported profiles land as 'Never subscribed'
    # and can't receive campaigns; we hold the opt-in in our own DB, so recording it here is
    # legitimate and makes them marketable. The campaign then targets this list.
    from marketing_crm.crm_sync import klaviyo
    list_name = os.getenv("KLAVIYO_REACTIVATION_LIST", "NextPoint Reactivation")
    list_id = klaviyo.get_or_create_list(list_name)
    subscribed = klaviyo.subscribe_emails(list_id, emails) if list_id else False

    _hdr("RESULT")
    _row("profiles upserted to Klaviyo", pushed)
    _row(f"subscribed to list '{list_name}'",
         f"{len(emails)} (list id {list_id})" if subscribed else f"FAILED (list id {list_id})")
    print(f"\n>>> DONE. In Klaviyo → Campaigns, send a campaign to the list '{list_name}'.\n")
    sys.exit(0)


if __name__ == "__main__":
    main()

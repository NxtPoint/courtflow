# scripts/backfill_klaviyo_subscribers.py — make the people who already said yes actually mailable.
#
# WHY: consent and subscription are two different things and only one of them was ever wired.
# `subscribe_member()` is the ONLY call that marks a profile as subscribed in Klaviyo, and it fires
# from exactly one place — marketing_crm/consent/blueprint.py, i.e. someone actively granting
# consent through the re-permission page. Every other route to marketing_opt_in=true (the Wix
# import, an admin edit, and from 2026-08-23 the signup default) sets the flag in OUR database and
# tells Klaviyo nothing. `sync_all()` does not close the gap: it upserts a PROFILE but never
# subscribes, and it ignores consent entirely, so running it would push every user onto the Klaviyo
# bill while leaving all of them unmailable.
#
# Net effect on live data (2026-08-23): 455 people opted in, ~40 reachable. This backfills the
# difference — and ONLY the difference. It subscribes people who have already said yes.
#
# IT WILL NOT SUBSCRIBE ANYONE WHO DID NOT CONSENT. The audience is exactly
# marketing_opt_in = true, and that is not a flag this script can set.
#
# DRY RUN BY DEFAULT — prints who WOULD be subscribed and writes nothing:
#     python -m scripts.backfill_klaviyo_subscribers
# To actually send them to Klaviyo (type YES at the prompt):
#     python -m scripts.backfill_klaviyo_subscribers --commit
#
# ⚠ CHECK THE LIST'S OPT-IN PROCESS FIRST. If the target Klaviyo list is DOUBLE opt-in, subscribing
# via the API only sends a confirmation request — the person stays unmailable until they click it,
# and you will have spent the one re-engagement email you had on a confirmation nobody expected.
# "NextPoint Members" was double opt-in as of 2026-08-23. Switch it to single opt-in in the Klaviyo
# UI (Lists → the list → Settings) BEFORE running with --commit.

import argparse
import io
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


def _load_env():
    envf = os.path.join(REPO, ".env.local")
    if os.path.isfile(envf) and not os.getenv("DATABASE_URL"):
        for line in io.open(envf, encoding="utf-8"):
            line = line.strip()
            if line.startswith("DATABASE_URL=") and not line.startswith("#"):
                os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not os.getenv("DATABASE_URL"):
        print("DATABASE_URL is not set. On Render it already is; locally use .env.local.")
        sys.exit(2)


def main():
    ap = argparse.ArgumentParser(description="Subscribe already-consented members to Klaviyo.")
    ap.add_argument("--club", default="NextPoint Tennis")
    ap.add_argument("--commit", action="store_true", help="actually subscribe (default: dry run)")
    ap.add_argument("--limit", type=int, default=0, help="cap the number processed (0 = no cap)")
    args = ap.parse_args()

    _load_env()
    import db
    from sqlalchemy import text

    from marketing_crm.crm_sync import enabled
    if not enabled():
        print("KLAVIYO_API_KEY is not set in this environment — nothing to do.")
        print("(That is the switch; without it every Klaviyo call is a silent no-op.)")
        sys.exit(2)

    with db.session_scope() as s:
        club = s.execute(text("SELECT id, name FROM club.club WHERE name = :n"), {"n": args.club}).first()
        if not club:
            print(f"No club named {args.club!r}.")
            sys.exit(2)
        cid, cname = club[0], club[1]

        # The audience: consented, emailable, still an active member of THIS club.
        # Both opt-in flags are honoured — iam.user is what Client-360 shows and core.app_user is
        # the Klaviyo gate; they disagree on live data (455 vs 505), and for a SEND the safe read
        # is the union of "has said yes somewhere" minus anyone who has said no anywhere.
        rows = s.execute(text("""
            SELECT DISTINCT lower(u.email) AS email
            FROM iam.membership m
            JOIN iam.user u ON u.id = m.user_id
            LEFT JOIN core.app_user au ON lower(au.email) = lower(u.email)
            WHERE m.club_id = :c
              AND COALESCE(u.email, '') <> ''
              AND COALESCE(m.member_status, 'active') = 'active'
              AND (u.marketing_opt_in IS TRUE OR au.marketing_opt_in IS TRUE)
              AND COALESCE(au.marketing_opt_in, TRUE) IS TRUE
              AND COALESCE(au.status, 'active') = 'active'
              AND au.deleted_at IS NULL
            ORDER BY 1
        """), {"c": cid}).fetchall()

    emails = [r[0] for r in rows if r[0]]
    if args.limit:
        emails = emails[:args.limit]

    print(f"\n{cname} — Klaviyo subscriber backfill")
    print("=" * 66)
    print(f"  consented + emailable + active : {len(emails)}")
    print(f"  mode                           : {'COMMIT' if args.commit else 'DRY RUN (nothing written)'}")
    print("=" * 66)
    for e in emails[:20]:
        print(f"   {e}")
    if len(emails) > 20:
        print(f"   ... and {len(emails) - 20} more")

    if not args.commit:
        print("\nDry run. Re-run with --commit to subscribe these people.")
        print("Check the list is SINGLE opt-in first, or they all get a confirmation email instead.\n")
        return

    if not emails:
        print("\nNothing to do.\n")
        return

    print(f"\nThis will subscribe {len(emails)} real people to marketing email in Klaviyo.")
    if input("Type YES to proceed: ").strip() != "YES":
        print("Aborted. Nothing was written.\n")
        return

    from marketing_crm.crm_sync.sync import subscribe_member
    ok = bad = 0
    for e in emails:
        try:
            subscribe_member(e, club_id=cid)
            ok += 1
        except Exception as exc:                      # never let one address stop the batch
            bad += 1
            print(f"   !! {e}: {exc}")
        if (ok + bad) % 50 == 0:
            print(f"   ...{ok + bad}/{len(emails)}")
    print(f"\nSubscribed {ok}, failed {bad}.")
    print("Verify in Klaviyo: the list's profile_count should have moved by roughly that much.\n")


if __name__ == "__main__":
    main()

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
# "NextPoint Members" WAS double opt-in and was switched to SINGLE opt-in on 2026-08-23 (verified
# via the API: opt_in_process = single_opt_in). If you point this at a different list, check that
# list first — Klaviyo creates new lists as double opt-in by default.

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

    # NOT subscribe_member(): that is fire-and-forget and spawns a THREAD PER CALL. It is built for
    # one invocation from a request handler, and looping it over hundreds of people would start
    # hundreds of daemon threads, trip Klaviyo's rate limit, and let this process exit before they
    # finished — subscribing an unpredictable subset while printing success. Use the synchronous
    # bulk API underneath it instead: batches of 100, consent recorded as SUBSCRIBED, real return value.
    from marketing_crm.crm_sync import klaviyo
    from db import norm_email          # lives in db.py, not crm_sync (sync.py imports it from there too)

    list_name = os.getenv("KLAVIYO_MARKETING_LIST", "NextPoint Members")
    list_id = klaviyo.get_or_create_list(list_name)
    if not list_id:
        print(f"Could not resolve the Klaviyo list {list_name!r}. Nothing was written.")
        sys.exit(1)
    # get_or_create_list CREATES on a name miss, and a fresh list is DOUBLE opt-in — which would
    # silently undo the whole point. Make the caller confirm the id it is about to write to.
    print(f"\n  target list : {list_name!r}  (id {list_id})")
    lst = klaviyo.get_list_meta(list_id) if hasattr(klaviyo, "get_list_meta") else None
    if lst and lst.get("opt_in_process") == "double_opt_in":
        print("  !! that list is DOUBLE opt-in — subscribing would only send confirmation requests.")
        print("     Switch it to single opt-in first. Nothing was written.")
        sys.exit(1)
    if input(f"Type the list id ({list_id}) to confirm: ").strip() != str(list_id):
        print("Aborted. Nothing was written.\n")
        return

    addrs = [norm_email(e) for e in emails]
    ok = klaviyo.subscribe_emails(list_id, addrs)
    print(f"\nSubmitted {len(addrs)} addresses in batches of 100 → {'ALL ACCEPTED' if ok else 'SOME BATCHES FAILED (see log)'}.")
    print("Klaviyo processes these as a job, so the count moves over the next minute or two.")
    print(f"Verify: the {list_name!r} profile_count should climb from 18 toward ~{len(addrs)}.\n")


if __name__ == "__main__":
    main()

# scripts/reconcile_marketing_optin.py — make the admin screen tell the truth about consent.
#
# WHY: marketing_opt_in exists on BOTH iam.user (what Client-360 and the admin screen show) and
# core.app_user (the gate marketing_crm/crm_sync actually reads before it will mail anyone). Only
# some write paths touch both. The re-permission page (marketing_crm/consent) writes core.app_user
# and Klaviyo and NEVER writes back to iam.user, so on 2026-08-25 the live numbers were 459 vs 506
# and 82 people were being mailed while the admin screen showed them opted OUT.
#
# The audit settled which side was right (`python -m scripts.audit_marketing_reach`, section B):
#
#     app-only: consent rows -> granted 111 · withdrawn 0 · NONE 0
#
# NONE = 0. Every one of those 82 has a dated, granted core.consent row — they opted in on the
# re-permission page deliberately. The SEND was correct all along; the SCREEN was stale. So this
# script moves iam.user to match, and only ever where core.consent proves the person agreed.
#
# WHAT IT DOES — two writes, both to iam.user only:
#   1. iam.user.marketing_opt_in := TRUE   where core.app_user says true AND a GRANTED, un-withdrawn
#      core.consent 'marketing_email' row exists. (Consent is the authority, not the flag.)
#   2. iam.user.marketing_opt_in := FALSE  where that consent has been WITHDRAWN. Honouring a
#      withdrawal on both columns can never be the wrong call, so it is not gated on anything else.
#
# WHAT IT DELIBERATELY WILL NOT DO — and do not "improve" it into doing:
#   It never writes core.app_user, and it never promotes anyone INTO the mailable set. The other
#   direction of the disagreement (35 people: iam.user says yes, app_user says no) reads
#   `granted 0 · withdrawn 1 · NONE 34` — i.e. 34 of them have NO consent record at all. Their
#   iam.user flag is Wix-era import data, and core.app_user has simply never been anything but its
#   `false` default. Flipping app_user for them would start mailing 34 people on the strength of a
#   flag copied out of a system we no longer run, with no dated record to show anyone who asks.
#   They are re-permission candidates (marketing_crm/repermission → /api/subscribe), not a backfill.
#   The 1 who withdrew is handled by rule 2 above and must stay off.
#
# DRY RUN BY DEFAULT — prints the counts and a sample, writes nothing:
#     python -m scripts.reconcile_marketing_optin
# To write (type YES at the prompt):
#     python -m scripts.reconcile_marketing_optin --commit
#
# Safe to run repeatedly: both statements are idempotent (they only touch rows already disagreeing).

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


# The people whose iam.user flag is stale in the SAFE direction: core.app_user already mails them
# and core.consent proves they said yes. Matching is on lower(email) because that is the only key
# the two schemas reliably share (core.person.iam_user_id is the proper bridge but is nullable).
_GRANTED = """
    SELECT DISTINCT lower(u.email) AS email
    FROM iam.membership m
    JOIN iam.user u        ON u.id = m.user_id
    JOIN core.app_user au  ON lower(au.email) = lower(u.email)
    JOIN core.person p     ON p.user_id = au.id
    JOIN core.consent c    ON c.subject_person_id = p.id
                          AND c.consent_type = 'marketing_email'
                          AND c.status = 'granted' AND c.withdrawn_at IS NULL
    WHERE m.club_id = :c
      AND au.marketing_opt_in IS TRUE
      AND COALESCE(au.status, 'active') = 'active' AND au.deleted_at IS NULL
      AND u.marketing_opt_in IS NOT TRUE
    ORDER BY 1
"""

# Withdrawals, in whichever direction the flags currently sit. A withdrawal outranks every flag.
_WITHDRAWN = """
    SELECT DISTINCT lower(u.email) AS email
    FROM iam.membership m
    JOIN iam.user u        ON u.id = m.user_id
    JOIN core.app_user au  ON lower(au.email) = lower(u.email)
    JOIN core.person p     ON p.user_id = au.id
    JOIN core.consent c    ON c.subject_person_id = p.id
                          AND c.consent_type = 'marketing_email'
    WHERE m.club_id = :c
      AND (c.status = 'withdrawn' OR c.withdrawn_at IS NOT NULL)
      AND u.marketing_opt_in IS TRUE
    ORDER BY 1
"""


def main():
    ap = argparse.ArgumentParser(
        description="Align iam.user.marketing_opt_in with core.consent (dry run by default).")
    ap.add_argument("--club", default="NextPoint Tennis")
    ap.add_argument("--commit", action="store_true", help="actually write (default: dry run)")
    args = ap.parse_args()

    _load_env()
    import db
    from sqlalchemy import text

    with db.session_scope() as s:
        club = s.execute(text("SELECT id, name FROM club.club WHERE name = :n"),
                         {"n": args.club}).first()
        if not club:
            print(f"No club named {args.club!r}.")
            sys.exit(2)
        cid, cname = club[0], club[1]

        grant = [r[0] for r in s.execute(text(_GRANTED), {"c": cid}).fetchall()]
        withdraw = [r[0] for r in s.execute(text(_WITHDRAWN), {"c": cid}).fetchall()]

        print(f"\n{cname} — reconcile iam.user.marketing_opt_in against core.consent")
        print("=" * 72)
        print(f"  turn ON  (consent GRANTED, screen says off) : {len(grant)}")
        for e in grant[:10]:
            print(f"      + {e}")
        if len(grant) > 10:
            print(f"      ... and {len(grant) - 10} more")
        print(f"  turn OFF (consent WITHDRAWN, screen says on): {len(withdraw)}")
        for e in withdraw[:10]:
            print(f"      - {e}")
        print("=" * 72)
        print("  core.app_user is NOT touched. Nobody is added to the mailable set by this script;")
        print("  it only makes the admin screen agree with the consent record that already exists.")

        if not args.commit:
            print("\nDry run. Nothing was written. Re-run with --commit to apply.\n")
            return
        if not grant and not withdraw:
            print("\nAlready reconciled. Nothing to do.\n")
            return

        print(f"\nThis will UPDATE {len(grant) + len(withdraw)} iam.user rows on LIVE data.")
        if input("Type YES to proceed: ").strip() != "YES":
            print("Aborted. Nothing was written.\n")
            return

        n_on = n_off = 0
        if grant:
            n_on = s.execute(text(
                "UPDATE iam.user SET marketing_opt_in = TRUE "
                "WHERE lower(email) = ANY(:e) AND marketing_opt_in IS NOT TRUE"
            ), {"e": grant}).rowcount
        if withdraw:
            n_off = s.execute(text(
                "UPDATE iam.user SET marketing_opt_in = FALSE "
                "WHERE lower(email) = ANY(:e) AND marketing_opt_in IS TRUE"
            ), {"e": withdraw}).rowcount

        print(f"\n  turned ON  : {n_on}")
        print(f"  turned OFF : {n_off}")
        print("  Re-run `python -m scripts.audit_marketing_reach` — section B should now agree.\n")


if __name__ == "__main__":
    main()

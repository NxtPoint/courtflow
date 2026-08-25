# scripts/diagnose_signup_optin.py — why is a new signup still landing opted OUT?
#
# WHY THIS EXISTS: the code that reads club.policy.marketing_opt_in_default shipped on
# 2026-08-23, the policy now reads TRUE, and yet the daily rate in `audit_marketing_reach`
# section G looks flat — 24 Aug: 8 signups, 0 opted in.
#
# THE FIRST QUESTION IS NOT "WHAT BROKE" — IT IS "WHEN WAS THE TOGGLE ACTUALLY FLIPPED". Code
# shipping on the 23rd says nothing about when a human ticked the box, and every signup BEFORE
# that tick was SUPPOSED to land opted out. Judging the fix against them makes a working fix look
# broken, which is how you end up rewriting a signup path that was fine. So this reads
# club.policy.updated_at and reports the rate only for signups after it.
#
# If the rate is still flat AFTER that timestamp, then the failure is on the live path and it is
# being HIDDEN: the whole block in auth/principal.py sits inside
#
#     try: ... except Exception: log.debug("core.person satellite link skipped (benign)")
#
# and the statements run in this order:
#
#     1. from core.repositories.persons import link_person_for_user
#     2. sat_club = str(memberships[0]["club_id"]) ...
#     3. _mkt = _marketing_opt_in_default(s, sat_club)
#     4. if _mkt: iam_repo.set_marketing_opt_in(s, user["id"], True)
#     5. link_person_for_user(..., marketing_opt_in=True)
#
# An exception at 1–3 skips BOTH the flag and the person satellite. An exception at 5 leaves the
# flag set but no satellite. So the presence or absence of core.person for a recent signup tells
# you WHERE it broke, and that is what this prints. It also checks the trial, because the trial is
# granted on the SAME `_created` gate a few lines earlier — a signup with a trial but no opt-in
# proves the gate fired and the fault is inside the block, not the gate.
#
# READ THE OUTPUT LIKE THIS:
#   trial YES · person NO  · optin NO   -> blew up at step 1-3 (import / sat_club / policy read)
#   trial YES · person YES · optin NO   -> reached step 5 but _mkt was FALSE (policy read wrong club)
#   trial NO  · person NO  · optin NO   -> `_created` was False; these are not new humans at all
#   trial YES · person YES · optin YES  -> working; the leak is elsewhere
#
# READ-ONLY. Every statement is a SELECT.
#
#     python -m scripts.diagnose_signup_optin            # last 21 days
#     python -m scripts.diagnose_signup_optin --days 7

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


def _pct(n, d):
    return f"{(100.0 * n / d):5.1f}%" if d else "    -"


def main():
    ap = argparse.ArgumentParser(description="Why do new signups land opted out? (read-only)")
    ap.add_argument("--club", default="NextPoint Tennis")
    ap.add_argument("--days", type=int, default=21)
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

        print(f"\n{cname} — why are new signups landing opted OUT?   (READ-ONLY)")
        print("=" * 78)

        prow = s.execute(text(
            "SELECT COALESCE(marketing_opt_in_default,false), updated_at "
            "FROM club.policy WHERE club_id=:c"
        ), {"c": cid}).first()
        pol, pol_when = (prow[0], prow[1]) if prow else (False, None)
        print(f"  club.policy.marketing_opt_in_default : {bool(pol)}"
              + ("" if pol else "   <-- OFF; nothing below can work"))
        print(f"  club_id                              : {cid}")
        # THE ONLY HONEST YARDSTICK. A signup that happened BEFORE the toggle was turned on was
        # supposed to land opted out — counting it against the fix makes a working fix look broken.
        # updated_at is an UPPER BOUND on when the flag was set (any later policy edit bumps it
        # too), so treat it as "no earlier than", and if it moved for an unrelated reason the
        # window below is simply too small, never too generous.
        print(f"  club.policy last written             : {pol_when}"
              "   <-- signups before this were SUPPOSED to opt out")

        # Every club.policy row, because the code reads the policy for the club it resolved from
        # the MEMBERSHIP — if this deployment somehow has more than one club row, or a policy row
        # under a different club_id, the read returns nothing and quietly means "opt out".
        pols = s.execute(text(
            "SELECT p.club_id, c.name, COALESCE(p.marketing_opt_in_default,false) "
            "FROM club.policy p LEFT JOIN club.club c ON c.id = p.club_id ORDER BY 2"
        )).fetchall()
        print(f"  club.policy rows in this database    : {len(pols)}")
        for pc, pn, pv in pols:
            print(f"      {str(pn or '(orphan)'):28} {str(pc)}  default={bool(pv)}")

        rows = s.execute(text("""
            SELECT
              to_char(date_trunc('day', u.created_at), 'YYYY-MM-DD')          AS d,
              count(*)                                                        AS signups,
              count(*) FILTER (WHERE u.marketing_opt_in IS TRUE)              AS optin_iam,
              count(*) FILTER (WHERE au.marketing_opt_in IS TRUE)             AS optin_gate,
              count(*) FILTER (WHERE au.id IS NOT NULL)                       AS has_appuser,
              count(*) FILTER (WHERE pr.id IS NOT NULL)                       AS has_person,
              count(*) FILTER (WHERE ms.user_id IS NOT NULL)                  AS got_trial
            FROM iam.user u
            JOIN iam.membership m ON m.user_id = u.id AND m.club_id = :c
            LEFT JOIN core.app_user au ON lower(au.email) = lower(u.email) AND au.deleted_at IS NULL
            LEFT JOIN core.person   pr ON pr.iam_user_id = u.id
            LEFT JOIN LATERAL (
                SELECT user_id FROM billing.membership_subscription
                 WHERE club_id = :c AND user_id = u.id AND provider = 'trial' LIMIT 1
            ) ms ON true
            WHERE u.created_at > now() - make_interval(days => :d)
            GROUP BY 1 ORDER BY 1
        """), {"c": cid, "d": args.days}).fetchall()

        print("\n  Per signup day — what actually got written:")
        print(f"      {'day':12}{'signups':>8}{'optin(iam)':>12}{'optin(gate)':>13}"
              f"{'app_user':>10}{'person':>8}{'trial':>7}")
        tot = [0] * 6
        for d, n, oi, og, ha, hp, gt in rows:
            print(f"      {d:12}{n:>8}{oi:>12}{og:>13}{ha:>10}{hp:>8}{gt:>7}")
            for i, v in enumerate((n, oi, og, ha, hp, gt)):
                tot[i] += v
        if not rows:
            print("      (no signups in this window)")
            return
        print(f"      {'TOTAL':12}{tot[0]:>8}{tot[1]:>12}{tot[2]:>13}"
              f"{tot[3]:>10}{tot[4]:>8}{tot[5]:>7}")

        # --- the verdict ------------------------------------------------
        # Judge ONLY the signups that came after the toggle. The earlier version of this compared
        # totals and printed "Working" off two non-zero columns, which was worse than printing
        # nothing: 30 opt-ins out of 157 is not working, and the number that mattered (5 out of 29
        # AFTER the flip) was buried inside it.
        n, oi, og, ha, hp, gt = tot
        print("\n" + "-" * 78)
        if not pol:
            print("  The default is OFF. Nothing here can work until it is on.")
        elif pol_when is None:
            print("  No club.policy row — cannot tell when the toggle was set.")
        else:
            aft = s.execute(text("""
                SELECT count(*)                                            AS signups,
                       count(*) FILTER (WHERE u.marketing_opt_in IS TRUE)  AS optin_iam,
                       count(*) FILTER (WHERE au.marketing_opt_in IS TRUE) AS optin_gate,
                       count(*) FILTER (WHERE pr.id IS NOT NULL)           AS has_person
                FROM iam.user u
                JOIN iam.membership m ON m.user_id = u.id AND m.club_id = :c
                LEFT JOIN core.app_user au ON lower(au.email) = lower(u.email)
                                          AND au.deleted_at IS NULL
                LEFT JOIN core.person   pr ON pr.iam_user_id = u.id
                WHERE u.created_at >= :since
            """), {"c": cid, "since": pol_when}).first()
            a_n, a_oi, a_og, a_hp = (aft[0] or 0, aft[1] or 0, aft[2] or 0, aft[3] or 0)
            print(f"  SINCE THE TOGGLE WAS SET ({pol_when:%Y-%m-%d %H:%M} UTC):")
            print(f"      signups                     : {a_n}")
            print(f"      opted in on iam.user        : {a_oi}   {_pct(a_oi, a_n)}")
            print(f"      opted in on the Klaviyo gate: {a_og}   {_pct(a_og, a_n)}")
            print(f"      core.person satellite made  : {a_hp}   {_pct(a_hp, a_n)}")
            print()
            if a_n == 0:
                print("  NOTHING TO JUDGE YET. No one has signed up since the toggle was set, so the")
                print("  earlier rows say nothing about whether the fix works — they predate it.")
                print("  Re-run tomorrow; every row after this timestamp should read near 100%.")
            elif a_n < 10:
                print(f"  TOO EARLY TO CALL. Only {a_n} signup(s) since the toggle. {a_oi} opted in.")
                print("  Re-run once a full day has passed rather than reading a handful of rows.")
            elif a_oi >= 0.9 * a_n:
                print("  WORKING. Signups since the toggle are landing opted IN. The leak is closed;")
                print("  the low lifetime rate is history, not an ongoing bleed.")
            elif a_hp >= 0.9 * a_n:
                print("  BLOCK RAN, FLAG DID NOT. The core.person satellite is being created, so the")
                print("  try-block in auth/principal.py reached its last statement — which means")
                print("  _mkt came back FALSE even though the policy says true. Check that the club")
                print("  resolved from memberships[0] is the one holding the policy row above.")
            else:
                print("  BROKE BEFORE THE FLAG. Signups since the toggle have no person satellite,")
                print("  so auth/principal.py is throwing early in that try-block and log.debug is")
                print("  swallowing it. Raise the log level and sign up a throwaway account.")
        print("-" * 78 + "\n")


if __name__ == "__main__":
    main()

# scripts/audit_marketing_reach.py — who can we actually email, and did the trials ever convert?
#
# WHY: two questions get asked together and answered by guesswork. "We have ~1200 clients" is a
# count of PEOPLE; "who can we email" is a count of CONSENT, and the two are wildly different
# numbers. Separately, ~300 trials were granted and the belief is that none converted to a
# membership and that they drift back to PAYG instead. That belief decides the whole campaign:
# an ex-trialist who still plays every week is a warm upsell, and one who vanished is a
# reactivation problem. They need completely different emails, so the split has to be measured
# rather than assumed.
#
# READ-ONLY. Every statement here is a SELECT — there is no --commit, because there is nothing
# to commit. Safe to run against production.
#
# RUN IT (per docs/specs/DATA-ACCESS.md): Render -> courtflow-api -> Shell:
#     python -m scripts.audit_marketing_reach
# Locally it reads a gitignored .env.local, else DATABASE_URL. The URL is never printed.
#
# A NOTE ON THE TWO OPT-IN FLAGS: marketing_opt_in exists on BOTH iam.user and core.app_user.
# marketing_crm/consent flips core.app_user (it calls that "the Klaviyo marketing gate") while
# client360 reads iam.user. If those two disagree, the gate and the screen are telling different
# stories and one of them is wrong — so this prints both rather than picking a winner.

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


def _one(s, sql, **p):
    from sqlalchemy import text
    return s.execute(text(sql), p).scalar() or 0


def _pct(n, d):
    return f"{(100.0 * n / d):5.1f}%" if d else "    -"


def _row(label, n, total=None, note=""):
    bar = f"  {_pct(n, total)}" if total else "       "
    print(f"   {label:<52}{n:>7}{bar}  {note}")


def main():
    ap = argparse.ArgumentParser(description="Marketing reach + trial conversion (read-only).")
    ap.add_argument("--club", default="NextPoint Tennis", help="club name (default: NextPoint Tennis)")
    args = ap.parse_args()

    _load_env()
    import db
    from sqlalchemy import text

    with db.session_scope() as s:
        club = s.execute(text("SELECT id, name FROM club.club WHERE name = :n"), {"n": args.club}).first()
        if not club:
            names = [r[0] for r in s.execute(text("SELECT name FROM club.club ORDER BY 1")).fetchall()]
            print(f"No club named {args.club!r}. Known: {names}")
            sys.exit(2)
        cid, cname = club[0], club[1]
        print(f"\n{cname} — marketing reach & trial conversion   (READ-ONLY)")
        print("=" * 78)

        # ---- A. who we have ------------------------------------------------
        print("\nA. WHO WE HAVE")
        people = _one(s, "SELECT count(DISTINCT user_id) FROM iam.membership WHERE club_id=:c", c=cid)
        members = _one(s, "SELECT count(DISTINCT user_id) FROM iam.membership "
                          "WHERE club_id=:c AND role='member'", c=cid)
        emailed = _one(s, "SELECT count(DISTINCT m.user_id) FROM iam.membership m "
                          "JOIN iam.user u ON u.id=m.user_id "
                          "WHERE m.club_id=:c AND COALESCE(u.email,'') <> ''", c=cid)
        _row("people linked to the club (any role)", people)
        _row("...with role 'member'", members, people)
        _row("...with a usable email address", emailed, people)

        # ---- B. who we may email -------------------------------------------
        print("\nB. WHO WE MAY EMAIL   (consent, not headcount)")
        iam_opt = _one(s, "SELECT count(DISTINCT m.user_id) FROM iam.membership m "
                          "JOIN iam.user u ON u.id=m.user_id "
                          "WHERE m.club_id=:c AND u.marketing_opt_in IS TRUE", c=cid)
        app_opt = _one(s, "SELECT count(*) FROM core.app_user "
                          "WHERE marketing_opt_in IS TRUE AND COALESCE(status,'active')='active' "
                          "AND (club_id = :c OR club_id IS NULL)", c=cid)
        consent = _one(s, "SELECT count(DISTINCT subject_person_id) FROM core.consent "
                          "WHERE club_id=:c AND consent_type='marketing_email' "
                          "AND COALESCE(status,'granted')='granted' AND withdrawn_at IS NULL", c=cid)
        reach = _one(s, "SELECT count(DISTINCT m.user_id) FROM iam.membership m "
                        "JOIN iam.user u ON u.id=m.user_id "
                        "WHERE m.club_id=:c AND u.marketing_opt_in IS TRUE "
                        "AND COALESCE(u.email,'') <> ''", c=cid)
        _row("iam.user.marketing_opt_in = true", iam_opt, people)
        _row("core.app_user.marketing_opt_in = true", app_opt, people, "(the Klaviyo gate)")
        _row("core.consent 'marketing_email' still granted", consent, people)
        _row("REACHABLE  (opted in AND has an email)", reach, people, "<-- the real audience")
        if iam_opt != app_opt:
            print(f"   !! the two opt-in flags DISAGREE ({iam_opt} vs {app_opt}) — the gate and the")
            print( "      screen are reading different columns. Worth reconciling before a send.")

        # ---- C. trials -----------------------------------------------------
        print("\nC. TRIALS EVER GRANTED")
        tr_all = _one(s, "SELECT count(*) FROM billing.membership_subscription "
                         "WHERE club_id=:c AND provider='trial'", c=cid)
        tr_ppl = _one(s, "SELECT count(DISTINCT user_id) FROM billing.membership_subscription "
                         "WHERE club_id=:c AND provider='trial'", c=cid)
        tr_live = _one(s, "SELECT count(*) FROM billing.membership_subscription "
                          "WHERE club_id=:c AND provider='trial' AND status='active'", c=cid)
        _row("trial subscriptions created", tr_all)
        _row("...distinct people trialled", tr_ppl)
        _row("...still active right now", tr_live, tr_all)

        # ---- D. did they convert? -----------------------------------------
        print("\nD. DID THE TRIALS CONVERT?   (per person, vs trial end)")
        # one row per trialist with the date their trial ended
        TRIALISTS = ("SELECT user_id, MAX(COALESCE(current_period_end, (created_at + interval '7 days')::date)) AS ended "
                     "FROM billing.membership_subscription "
                     "WHERE club_id=:c AND provider='trial' AND user_id IS NOT NULL GROUP BY user_id")
        conv = _one(s, f"WITH t AS ({TRIALISTS}) SELECT count(*) FROM t WHERE EXISTS ("
                       " SELECT 1 FROM billing.membership_subscription ms WHERE ms.club_id=:c "
                       " AND ms.user_id=t.user_id AND COALESCE(ms.provider,'') <> 'trial')", c=cid)
        paid_after = _one(s, f"WITH t AS ({TRIALISTS}) SELECT count(*) FROM t WHERE EXISTS ("
                             " SELECT 1 FROM billing.\"order\" o WHERE o.club_id=:c AND o.user_id=t.user_id "
                             " AND o.status='paid' AND o.created_at::date > t.ended)", c=cid)
        booked_after = _one(s, f"WITH t AS ({TRIALISTS}) SELECT count(*) FROM t WHERE EXISTS ("
                               " SELECT 1 FROM diary.booking b WHERE b.club_id=:c "
                               " AND b.booked_by_user_id=t.user_id AND b.starts_at::date > t.ended "
                               " AND b.status <> 'cancelled')", c=cid)
        silent = _one(s, f"WITH t AS ({TRIALISTS}) SELECT count(*) FROM t WHERE NOT EXISTS ("
                         " SELECT 1 FROM diary.booking b WHERE b.club_id=:c "
                         " AND b.booked_by_user_id=t.user_id AND b.starts_at::date > t.ended "
                         " AND b.status <> 'cancelled')", c=cid)
        _row("bought a PAID membership (ever)", conv, tr_ppl, "<-- the conversion rate")
        _row("paid for something AFTER the trial ended", paid_after, tr_ppl, "(the PAYG theory)")
        _row("booked a court AFTER the trial ended", booked_after, tr_ppl)
        _row("did NOTHING after the trial ended", silent, tr_ppl, "(reactivation, not upsell)")

        # ---- E. memberships now -------------------------------------------
        print("\nE. MEMBERSHIPS RIGHT NOW")
        paid_now = _one(s, "SELECT count(DISTINCT user_id) FROM billing.membership_subscription "
                           "WHERE club_id=:c AND status='active' AND COALESCE(provider,'') <> 'trial'", c=cid)
        _row("active PAID memberships", paid_now, people)
        _row("active trials", tr_live)

        # ---- F. campaign segments -----------------------------------------
        print("\nF. CAMPAIGN SEGMENTS   (all filtered to opted-in + emailable)")
        OPTED = ("JOIN iam.membership m ON m.user_id=t.user_id AND m.club_id=:c "
                 "JOIN iam.user u ON u.id=t.user_id "
                 "WHERE u.marketing_opt_in IS TRUE AND COALESCE(u.email,'') <> ''")
        NO_PAID = ("NOT EXISTS (SELECT 1 FROM billing.membership_subscription ms WHERE ms.club_id=:c "
                   "AND ms.user_id=t.user_id AND COALESCE(ms.provider,'') <> 'trial')")
        PLAYED = ("EXISTS (SELECT 1 FROM diary.booking b WHERE b.club_id=:c "
                  "AND b.booked_by_user_id=t.user_id AND b.starts_at > now() - interval '90 days' "
                  "AND b.status <> 'cancelled')")
        warm = _one(s, f"WITH t AS ({TRIALISTS}) SELECT count(DISTINCT t.user_id) FROM t {OPTED} "
                       f"AND {NO_PAID} AND {PLAYED}", c=cid)
        cold = _one(s, f"WITH t AS ({TRIALISTS}) SELECT count(DISTINCT t.user_id) FROM t {OPTED} "
                       f"AND {NO_PAID} AND NOT {PLAYED}", c=cid)
        never = _one(s,
                     "SELECT count(DISTINCT m.user_id) FROM iam.membership m "
                     "JOIN iam.user u ON u.id=m.user_id "
                     "WHERE m.club_id=:c AND u.marketing_opt_in IS TRUE AND COALESCE(u.email,'') <> '' "
                     "AND NOT EXISTS (SELECT 1 FROM billing.membership_subscription ms "
                     "  WHERE ms.club_id=:c AND ms.user_id=m.user_id) "
                     "AND EXISTS (SELECT 1 FROM diary.booking b WHERE b.club_id=:c "
                     "  AND b.booked_by_user_id=m.user_id AND b.starts_at > now() - interval '90 days' "
                     "  AND b.status <> 'cancelled')", c=cid)
        _row("ex-trialist, no membership, PLAYED in 90d", warm, reach, "<-- warmest: send first")
        _row("ex-trialist, no membership, quiet 90d", cold, reach, "(win-back tone)")
        _row("never trialled, no membership, played 90d", never, reach, "(pure PAYG upsell)")

        print("\n" + "=" * 78)
        print("Nothing was written. Numbers are per-person unless the label says otherwise.\n")


if __name__ == "__main__":
    main()

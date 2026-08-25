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
        # THE COLUMN THIS SECTION READS IS THE WHOLE POINT OF IT. marketing_crm/crm_sync will
        # not mail anyone whose core.app_user.marketing_opt_in is false, so THAT is the gate and
        # that is what "may email" has to mean. iam.user.marketing_opt_in is what the admin screen
        # and Client-360 show, and it is a different number: on 2026-08-25 it read 82 LOWER than
        # the gate (before the consent reconcile) and then 34 HIGHER (after it). Either way,
        # sizing an audience off it is a read that lies — first understating the reach, then
        # promising 34 recipients who cannot receive anything. Both flags are still PRINTED,
        # because the gap between them is the diagnostic; but every count below that claims
        # mailability joins through MAILABLE.
        MAILABLE = ("JOIN core.app_user au ON lower(au.email) = lower(u.email) "
                    "AND au.deleted_at IS NULL AND au.marketing_opt_in IS TRUE "
                    "AND COALESCE(au.status, 'active') = 'active'")
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
                        f"{MAILABLE} "
                        "WHERE m.club_id=:c AND COALESCE(u.email,'') <> ''", c=cid)
        _row("iam.user.marketing_opt_in = true", iam_opt, people)
        _row("core.app_user.marketing_opt_in = true", app_opt, people, "(the Klaviyo gate)")
        _row("core.consent 'marketing_email' still granted", consent, people)
        _row("REACHABLE  (past the Klaviyo gate, has an email)", reach, people,
             "<-- the real audience")
        if iam_opt != app_opt:
            print(f"   .. the two opt-in flags differ ({iam_opt} screen vs {app_opt} gate).")
            # Saying "they disagree" is not actionable; saying WHICH WAY it leans is. The two
            # directions are different problems and only one of them is a consent risk:
            #   app-only  = we are MAILING someone the admin screen shows as opted OUT
            #   iam-only  = someone said yes on the screen and Klaviyo never heard about it
            # (the second is the gap the backfill closes; the first is the one to look at hard).
            app_only = s.execute(text("""
                SELECT lower(au.email)
                FROM core.app_user au
                WHERE au.marketing_opt_in IS TRUE
                  AND COALESCE(au.status,'active') = 'active' AND au.deleted_at IS NULL
                  AND (au.club_id = :c OR au.club_id IS NULL)
                  AND EXISTS (SELECT 1 FROM iam.membership m JOIN iam.user u ON u.id = m.user_id
                               WHERE m.club_id = :c AND lower(u.email) = lower(au.email)
                                 AND u.marketing_opt_in IS NOT TRUE)
                ORDER BY 1
            """), {"c": cid}).fetchall()
            iam_only = s.execute(text("""
                SELECT DISTINCT lower(u.email)
                FROM iam.membership m
                JOIN iam.user u ON u.id = m.user_id
                LEFT JOIN core.app_user au ON lower(au.email) = lower(u.email)
                WHERE m.club_id = :c AND u.marketing_opt_in IS TRUE
                  AND (au.id IS NULL OR au.marketing_opt_in IS NOT TRUE)
                ORDER BY 1
            """), {"c": cid}).fetchall()
            print(f"      app_user says YES, iam.user says NO : {len(app_only)}"
                  "   <-- being mailed while the screen says opted out")
            for r in app_only[:8]:
                print(f"         - {r[0]}")
            print(f"      iam.user says YES, app_user says NO : {len(iam_only)}"
                  "   <-- said yes, Klaviyo never told")
            for r in iam_only[:8]:
                print(f"         - {r[0]}")

            # WHICH SIDE IS WRONG. The counts above say the two columns differ; they do not say
            # whether we are mailing people who never agreed. core.consent is the only place an
            # ACTIVE, dated, per-person decision is recorded (the re-permission page writes it,
            # and a withdrawal stamps withdrawn_at) — so it is the tie-breaker, and it is the
            # thing a complaint would be answered with. Read it like this:
            #   app-only WITH a granted consent row  -> they opted in on the re-permission page and
            #                                           iam.user is simply a stale copy. Mailing is
            #                                           correct; the ADMIN SCREEN is what is wrong.
            #   app-only with NO consent row at all  -> we cannot show they agreed. THIS is the
            #                                           number that matters. Anything above zero
            #                                           needs a look before the next send.
            def _consent_split(emails_):
                if not emails_:
                    return (0, 0, 0)
                addrs = [e[0] for e in emails_]
                row = s.execute(text("""
                    SELECT
                      count(*) FILTER (WHERE c.status = 'granted' AND c.withdrawn_at IS NULL)  AS granted,
                      count(*) FILTER (WHERE c.status = 'withdrawn' OR c.withdrawn_at IS NOT NULL) AS withdrawn,
                      count(*) FILTER (WHERE c.id IS NULL)                                     AS none_
                    FROM core.app_user au
                    LEFT JOIN core.person p ON p.user_id = au.id
                    LEFT JOIN core.consent c
                           ON c.subject_person_id = p.id
                          AND c.consent_type = 'marketing_email'
                    WHERE lower(au.email) = ANY(:addrs)
                """), {"addrs": addrs}).first()
                return (row[0] or 0, row[1] or 0, row[2] or 0)

            for label, bucket in (("app-only", app_only), ("iam-only", iam_only)):
                g, w, n = _consent_split(bucket)
                print(f"      {label}: consent rows -> granted {g} · withdrawn {w} · NONE {n}"
                      + ("   <-- no evidence they agreed" if (label == "app-only" and n) else ""))

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
                 f"{MAILABLE} "
                 "WHERE COALESCE(u.email,'') <> ''")
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
                     f"{MAILABLE} "
                     "WHERE m.club_id=:c AND COALESCE(u.email,'') <> '' "
                     "AND NOT EXISTS (SELECT 1 FROM billing.membership_subscription ms "
                     "  WHERE ms.club_id=:c AND ms.user_id=m.user_id) "
                     "AND EXISTS (SELECT 1 FROM diary.booking b WHERE b.club_id=:c "
                     "  AND b.booked_by_user_id=m.user_id AND b.starts_at > now() - interval '90 days' "
                     "  AND b.status <> 'cancelled')", c=cid)
        _row("ex-trialist, no membership, PLAYED in 90d", warm, reach, "<-- warmest: send first")
        _row("ex-trialist, no membership, quiet 90d", cold, reach, "(win-back tone)")
        _row("never trialled, no membership, played 90d", never, reach, "(pure PAYG upsell)")

        # ---- G. the leak -----------------------------------------------
        # Growth is coming from free trials, so the question that decides the whole marketing
        # programme is whether a NEW member arrives marketable. Two things must be true: the club
        # policy must say opt-IN by default, and signup must honour it. A per-month opt-in rate
        # shows both at once - the rate should step UP sharply from the day the default was
        # switched on, and a flat line means the switch was never flipped.
        print("\nG. IS THE TAP OPEN?   (do NEW signups arrive marketable?)")
        dflt = s.execute(text(
            "SELECT COALESCE(marketing_opt_in_default, false) FROM club.policy WHERE club_id = :c"
        ), {"c": cid}).scalar()
        print(f"   club.policy.marketing_opt_in_default          : {bool(dflt)}")
        if not dflt:
            print("   !! OFF - every self-signup lands opted OUT, having never been asked.")
            print("      Admin -> Setup -> Club profile & payments -> 'New members start opted in'.")
        else:
            print("   -> ON - new signups start opted in and can unsubscribe.")

        print("\n   Signups per month, and how many arrived marketable:")
        rows = s.execute(text("""
            SELECT to_char(date_trunc('month', u.created_at), 'YYYY-MM') AS mon,
                   count(*)                                              AS signups,
                   count(*) FILTER (WHERE au.id IS NOT NULL)              AS opted_in
            FROM iam.user u
            JOIN iam.membership m ON m.user_id = u.id AND m.club_id = :c
            LEFT JOIN core.app_user au ON lower(au.email) = lower(u.email)
                  AND au.deleted_at IS NULL AND au.marketing_opt_in IS TRUE
                  AND COALESCE(au.status, 'active') = 'active'
            WHERE u.created_at > now() - interval '12 months'
            GROUP BY 1 ORDER BY 1
        """), {"c": cid}).fetchall()
        print(f"      {'month':9}{'signups':>9}{'opted in':>10}{'rate':>8}")
        for mon, n_, o_ in rows:
            print(f"      {mon:9}{n_:>9}{o_:>10}{_pct(o_, n_):>8}")

        # A MONTHLY RATE CANNOT TELL YOU WHETHER THE FIX IS WORKING. The default was switched on
        # part-way through August, so that month is a blend of a broken week and a fixed one and
        # reads ~10% either way — which is indistinguishable from "the switch did nothing". Waiting
        # for a clean September means finding out in October, and the whole point of the leak is
        # that it costs a marketable person per signup while you wait. So look at it daily instead:
        # after the switch every row should read at or near 100%, and any zero-rate day AFTER the
        # switch means signup is not honouring club.policy.marketing_opt_in_default and the leak
        # is still open (start at auth/principal.py, the _created gate).
        print("\n   The last 21 days, daily — is the fix actually holding?")
        drows = s.execute(text("""
            SELECT to_char(date_trunc('day', u.created_at), 'YYYY-MM-DD')  AS d,
                   count(*)                                                AS signups,
                   count(*) FILTER (WHERE au.id IS NOT NULL)               AS opted_in
            FROM iam.user u
            JOIN iam.membership m ON m.user_id = u.id AND m.club_id = :c
            LEFT JOIN core.app_user au ON lower(au.email) = lower(u.email)
                  AND au.deleted_at IS NULL AND au.marketing_opt_in IS TRUE
                  AND COALESCE(au.status, 'active') = 'active'
            WHERE u.created_at > now() - interval '21 days'
            GROUP BY 1 ORDER BY 1
        """), {"c": cid}).fetchall()
        if not drows:
            print("      (no signups in the last 21 days — nothing to judge the switch on yet)")
        else:
            print(f"      {'day':12}{'signups':>9}{'opted in':>10}{'rate':>8}")
            for d_, n_, o_ in drows:
                flag = "  <-- opted OUT, switch not honoured" if (n_ and not o_) else ""
                print(f"      {d_:12}{n_:>9}{o_:>10}{_pct(o_, n_):>8}{flag}")

        print("\n   Of everyone who has EVER held a trial:")
        tr_opt = _one(s, "SELECT count(DISTINCT ms.user_id) FROM billing.membership_subscription ms "
                         "JOIN iam.user u ON u.id = ms.user_id "
                         "JOIN core.app_user au ON lower(au.email) = lower(u.email) "
                         "AND au.deleted_at IS NULL AND au.marketing_opt_in IS TRUE "
                         "AND COALESCE(au.status, 'active') = 'active' "
                         "WHERE ms.club_id=:c AND ms.provider='trial'", c=cid)
        _row("trialists we may email", tr_opt, tr_ppl, "<-- the trial-grown audience")

        print("\n" + "=" * 78)
        print("Nothing was written. Numbers are per-person unless the label says otherwise.\n")


if __name__ == "__main__":
    main()

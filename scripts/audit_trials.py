# scripts/audit_trials.py — review who currently holds a 7-day trial, and flag wrongly-granted ones.
#
# WHY: the "7 Day Trial Period" (provider='trial') must ONLY ever be granted to a genuinely NEW
# member — an email NOT already in history. Before the 2026-07-09 fix, a returning/imported Wix
# user (or a coach) whose Clerk/Google login email did NOT match their imported record could log in,
# land with no membership row, and be auto-enrolled + TRIALED. This script surfaces every active
# trial with the signals that reveal such a mis-grant, so you can eyeball the ~28 and (optionally)
# cancel the wrong ones back to PAYG. Trials auto-lapse after 7 days anyway, so this is cleanup.
#
# READ-ONLY by default (prints a table, writes nothing). To cancel the flagged ones:
#     python scripts/audit_trials.py --cancel-flagged      (then type YES at the prompt)
#
# DATABASE URL: prefers a gitignored .env.local (DATABASE_URL=...), else the DATABASE_URL env var.
# Get the string from Render -> your Postgres -> 'External Connection String'. Never printed.
#
# A "flag" = the trial-holder is NOT a genuinely-new member:
#   - coach            : holds a coach role (a coach must never be on a member trial)
#   - pre-existing user: the iam.user row was created >1 day BEFORE the trial (imported/seeded)
#   - prior activity   : had a booking or order BEFORE the trial started (was active before)
# Any flag → the trial should not exist → cancelling it reverts the person to PAYG immediately.

import argparse
import io
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


def _load_env():
    """Load DATABASE_URL from .env.local into the environment if present (so db.get_engine picks it
    up). Returns the host/db for a one-line confirmation. Never prints the password."""
    envf = os.path.join(REPO, ".env.local")
    if os.path.isfile(envf) and not os.getenv("DATABASE_URL"):
        for line in io.open(envf, encoding="utf-8"):
            line = line.strip()
            if line.startswith("DATABASE_URL=") and not line.startswith("#"):
                os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    url = os.getenv("DATABASE_URL") or ""
    import urllib.parse
    try:
        p = urllib.parse.urlparse(url)
        return "%s / %s" % (p.hostname or "?", (p.path or "").lstrip("/") or "?")
    except Exception:
        return "(unparseable url)"


AUDIT_SQL = """
    SELECT ms.id                AS sub_id,
           ms.club_id,
           ms.user_id,
           ms.created_at        AS trial_created,
           ms.period_start,
           ms.current_period_end,
           (ms.current_period_end - CURRENT_DATE) AS days_left,
           u.email, u.first_name, u.surname,
           u.created_at         AS user_created,
           (u.clerk_user_id IS NOT NULL) AS has_login,
           EXISTS(SELECT 1 FROM iam.membership m
                  WHERE m.user_id = ms.user_id AND m.club_id = ms.club_id AND m.role = 'coach') AS is_coach,
           (SELECT count(*) FROM diary.booking b WHERE b.booked_by_user_id = ms.user_id) AS bookings,
           (SELECT count(*) FROM billing."order" o WHERE o.user_id = ms.user_id) AS orders,
           (SELECT min(b.created_at) FROM diary.booking b WHERE b.booked_by_user_id = ms.user_id) AS first_booking,
           (SELECT min(o.created_at) FROM billing."order" o WHERE o.user_id = ms.user_id) AS first_order
    FROM billing.membership_subscription ms
    JOIN iam."user" u ON u.id = ms.user_id
    WHERE ms.provider = 'trial'
      AND ms.status = 'active'
      AND (ms.current_period_end IS NULL OR ms.current_period_end >= CURRENT_DATE)
    ORDER BY ms.created_at DESC
"""


def _flags(r):
    """Return the list of reasons this trial looks wrongly-granted (empty = a clean, genuine trial)."""
    out = []
    if r["is_coach"]:
        out.append("coach")
    # The user row pre-dates the trial by >1 day → they were already in the system (imported/seeded).
    try:
        if r["user_created"] and r["trial_created"] and \
                (r["trial_created"] - r["user_created"]).total_seconds() > 86400:
            out.append("pre-existing-user")
    except Exception:
        pass
    # Any booking/order BEFORE the trial started → they were active before it.
    ref = r["trial_created"] or r["period_start"]
    for key, lbl in (("first_booking", "prior-booking"), ("first_order", "prior-order")):
        try:
            if r[key] and ref and r[key] < ref:
                out.append(lbl)
        except Exception:
            pass
    return out


def main():
    ap = argparse.ArgumentParser(description="Audit active 7 Day Trial Period grants.")
    ap.add_argument("--cancel-flagged", action="store_true",
                    help="Cancel the flagged trials (revert to PAYG). Prompts for a typed YES.")
    args = ap.parse_args()

    where = _load_env()
    if not os.getenv("DATABASE_URL"):
        print("No DATABASE_URL found (set it, or add a .env.local with DATABASE_URL=...).")
        return 2
    print("DB: %s\n" % where)

    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from db import get_engine

    s = Session(get_engine())
    try:
        rows = s.execute(text(AUDIT_SQL)).mappings().all()
        clean, flagged = [], []
        for r in rows:
            (flagged if _flags(r) else clean).append(r)

        print("Active '7 Day Trial Period' holders: %d  (%d clean · %d FLAGGED)\n"
              % (len(rows), len(clean), len(flagged)))
        hdr = "  %-30s %-22s %5s %6s %6s  %s"
        print(hdr % ("email", "name", "days", "bkgs", "ords", "flags"))
        print("  " + "-" * 86)
        for r in rows:
            fl = _flags(r)
            name = ((r["first_name"] or "") + " " + (r["surname"] or "")).strip() or "—"
            print(hdr % ((r["email"] or "—")[:30], name[:22],
                         str(r["days_left"]) if r["days_left"] is not None else "—",
                         r["bookings"], r["orders"], (", ".join(fl) if fl else "clean")))
        print()

        if not flagged:
            print("Nothing flagged — every active trial looks like a genuine new signup. ✅")
            return 0
        print("FLAGGED (should not hold a trial → cancel reverts them to PAYG):")
        for r in flagged:
            print("  - %s  [%s]" % (r["email"] or r["user_id"], ", ".join(_flags(r))))
        print()

        if not args.cancel_flagged:
            print("Read-only. Re-run with --cancel-flagged to cancel the %d flagged trial(s)." % len(flagged))
            return 0

        print("About to CANCEL %d flagged trial(s) (status='cancelled', cancelled_at=now())." % len(flagged))
        try:
            confirm = input("Type YES to proceed: ").strip()
        except EOFError:
            confirm = ""
        if confirm != "YES":
            print("Aborted — nothing changed.")
            return 0
        ids = [str(r["sub_id"]) for r in flagged]
        s.execute(
            text("UPDATE billing.membership_subscription "
                 "SET status='cancelled', cancelled_at=COALESCE(cancelled_at, now()) "
                 "WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        s.commit()
        print("Cancelled %d trial(s). They are now PAYG." % len(ids))
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())

"""preview_month_end — READ-ONLY dry run of the month-end statement sweep.

Run this BEFORE the 25th to see exactly who will be invoiced and for how much. It answers the
question the sweep itself can't: is a R0 result "everybody is settled" or "the debt is somewhere
the sweep doesn't look?" — because `run_month_end` only ever considers orders that are
`status='open'`, unwrapped and user-attributed, so real money in any other state is invisible to
it AND to a naive check of it.

    python -m scripts.preview_month_end            # all clubs
    python -m scripts.preview_month_end --club <id>

Opens a plain `connect()` and never commits, so it cannot write anything.
"""
import sys

from sqlalchemy import text

from db import get_engine

# The sweep's OWN target query (billing.commission.month_end_targets). Keep these in step — the
# whole point of this script is to preview what that function will do, not something adjacent.
TARGETS = """
SELECT o.user_id,
       u.email,
       COUNT(*) AS orders,
       SUM(o.amount_minor) AS owed_minor,
       MIN(o.created_at)::date AS oldest
FROM billing."order" o
LEFT JOIN iam."user" u ON u.id = o.user_id
WHERE o.club_id = :c
  AND o.status = 'open'
  AND o.settled_by_order_id IS NULL
  AND o.user_id IS NOT NULL
GROUP BY o.user_id, u.email
HAVING COALESCE(SUM(o.amount_minor), 0) > 0
ORDER BY 4 DESC
"""

# Money the sweep will NOT touch, and why. Each bucket is a different story:
#   awaiting_payment -> an abandoned online checkout (correctly skipped; nobody should be invoiced
#                       for a page they closed), but a growing pile means orphaned orders are back.
#   open + wrapped   -> live debt hidden behind a 'Pay all' wrapper. _reclaim_abandoned_settlements
#                       releases those after 30 min; anything older is a bug, and it is REAL money
#                       that will silently miss the invoice run.
#   open + no user   -> unattributable debt; the sweep groups by user_id, so it can never bill it.
EXCLUDED = """
SELECT o.status,
       COUNT(*) AS n,
       SUM(o.amount_minor) AS minor,
       COUNT(*) FILTER (WHERE o.settled_by_order_id IS NOT NULL) AS wrapped,
       COUNT(*) FILTER (WHERE o.user_id IS NULL) AS no_user,
       MIN(o.created_at)::date AS oldest
FROM billing."order" o
WHERE o.club_id = :c
  AND o.amount_minor > 0
  AND NOT (o.status = 'open'
           AND o.settled_by_order_id IS NULL
           AND o.user_id IS NOT NULL)
GROUP BY o.status
ORDER BY 3 DESC NULLS LAST
"""

STALE_WRAPPED = """
SELECT COUNT(*) AS n, SUM(amount_minor) AS minor
FROM billing."order"
WHERE club_id = :c
  AND status = 'open'
  AND settled_by_order_id IS NOT NULL
  AND created_at < now() - interval '2 hours'
"""

ALREADY = """
SELECT COUNT(*) FROM billing.month_end_notice
WHERE club_id = :c AND period_label = :p
"""


def rand(minor):
    return "R{:,.2f}".format((minor or 0) / 100.0)


def main(argv):
    club = None
    if "--club" in argv:
        club = argv[argv.index("--club") + 1]

    eng = get_engine()
    with eng.connect() as c:
        period = c.execute(text("SELECT to_char(now(),'YYYY-MM')")).scalar()
        if club:
            clubs = [(club, "(specified)")]
        else:
            rows = c.execute(
                text("SELECT id, name FROM club.club ORDER BY created_at")
            ).all()
            clubs = [(str(r[0]), r[1]) for r in rows]

        print("Period {} - {} club(s)".format(period, len(clubs)))

        for cid, name in clubs:
            print("")
            print("=" * 70)
            print("{}  ({})".format(name, cid))
            print("=" * 70)

            done = c.execute(text(ALREADY), {"c": cid, "p": period}).scalar()
            if done:
                print("NOTE: {} client(s) already swept this period —".format(done))
                print("      they will be skipped (month_end_notice).")

            rows = c.execute(text(TARGETS), {"c": cid}).mappings().all()
            total = sum(int(r["owed_minor"]) for r in rows)
            print("")
            print("WILL INVOICE: {} client(s), {}".format(len(rows), rand(total)))
            if rows:
                print("")
                print("  {:<40}{:>7}{:>13}  {}".format(
                    "email", "orders", "owed", "oldest"))
                for r in rows:
                    print("  {:<40}{:>7}{:>13}  {}".format(
                        (r["email"] or "?")[:40],
                        r["orders"],
                        rand(int(r["owed_minor"])),
                        r["oldest"]))

            ex = c.execute(text(EXCLUDED), {"c": cid}).mappings().all()
            print("")
            print("NOT INVOICED (and why):")
            if not ex:
                print("  nothing excluded")
            for r in ex:
                bits = []
                if r["wrapped"]:
                    bits.append("{} behind a 'Pay all'".format(r["wrapped"]))
                if r["no_user"]:
                    bits.append("{} unattributed".format(r["no_user"]))
                why = ("  [" + ", ".join(bits) + "]") if bits else ""
                print("  {:<18} n={:<5} {:>13}  since {}{}".format(
                    r["status"], r["n"], rand(int(r["minor"] or 0)),
                    r["oldest"], why))

            st = c.execute(text(STALE_WRAPPED), {"c": cid}).mappings().first()
            if st and st["n"]:
                print("")
                print("WARNING: {} open order(s) worth {} have been hidden".format(
                    st["n"], rand(int(st["minor"] or 0))))
                print("  behind a settlement wrapper for >2h. The 30-minute")
                print("  reclaim should have freed them. This is real debt")
                print("  that will MISS the invoice run — investigate.")

    print("")
    print("Read-only - nothing was written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

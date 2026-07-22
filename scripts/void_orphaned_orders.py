# scripts/void_orphaned_orders.py — one-off: void the unpaid orders left behind by abandoned
# online checkouts, whose bookings were already cancelled by lazy expiry.
#
# WHY THESE EXIST. diary.bookings.release_expired_holds used to cancel a lapsed 'held' booking but
# leave its order untouched, so an abandoned Yoco checkout left an 'awaiting_payment' order pointing
# at a cancelled booking — 37 of them in production. They bill nobody (awaiting_payment is excluded
# from the statement, "pay all", month-end and invoicing) but they pollute every money read and make
# an abandoned checkout look like a permanent unpaid debt. The statement self-heal
# (billing.statement._void_phantom_cancelled_orders) only rescues 'open' orders, so these never got
# cleared. The root cause is fixed; this clears the backlog.
#
# SAFETY. An order is voided ONLY when EVERY booking on it is cancelled/expired AND it has taken no
# money (no succeeded payment). A PAID or part-paid order is never touched — that is the refund
# path's business. One order can carry several bookings (a lesson plus its auto-held court, a squad's
# per-head partners), so the all-cancelled test is what stops a live debt being erased.
#
# DRY-RUN BY DEFAULT — lists what it WOULD void and changes nothing. Pass --commit to write.
#
#   python -m scripts.void_orphaned_orders             # report
#   python -m scripts.void_orphaned_orders --commit    # void them

import argparse
import os
import sys
from pathlib import Path


def _load_env_local():
    if os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("DB_URL"):
        return
    f = Path(__file__).resolve().parent.parent / ".env.local"
    if not f.exists():
        print("!! No DATABASE_URL in env and no .env.local found. Run this on the Render shell.")
        sys.exit(2)
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# Orders whose EVERY linked booking is dead, which hold no money, and which are still open-ish.
# NOT EXISTS (a live booking) is the guard that protects a lesson+court or squad order where one row
# is still alive. NOT EXISTS (a succeeded/refunded payment) protects anything that took money.
_ORPHANS_SQL = """
SELECT DISTINCT o.id, o.status, o.settlement_mode, o.amount_minor, o.created_at,
       u.email AS client
FROM billing."order" o
JOIN billing.order_line ol ON ol.order_id = o.id AND ol.booking_id IS NOT NULL
LEFT JOIN iam."user" u ON u.id = o.user_id
WHERE o.club_id = :c
  AND o.status IN ('open', 'awaiting_payment')
  AND NOT EXISTS (SELECT 1 FROM billing.order_line l2
                   JOIN diary.booking b2 ON b2.id = l2.booking_id
                  WHERE l2.order_id = o.id
                    AND b2.status NOT IN ('cancelled', 'expired'))
  AND NOT EXISTS (SELECT 1 FROM billing.payment p
                  WHERE p.order_id = o.id AND p.direction = 'charge'
                    AND p.status IN ('succeeded', 'refunded'))
ORDER BY o.created_at DESC
"""


def main():
    ap = argparse.ArgumentParser(description="Void unpaid orders whose bookings are all cancelled.")
    ap.add_argument("--commit", action="store_true", help="actually void (default: report only)")
    args = ap.parse_args()
    _load_env_local()
    import db
    from sqlalchemy import text

    with db.session_scope() as s:
        clubs = s.execute(text("SELECT id, name FROM club.club ORDER BY created_at")).mappings().all()
        total = 0
        for c in clubs:
            rows = s.execute(text(_ORPHANS_SQL), {"c": str(c["id"])}).mappings().all()
            if not rows:
                continue
            print("\n== %s — %d orphaned order(s)" % (c["name"], len(rows)))
            print("   %-38s %-17s %10s  %s" % ("order_id", "status", "amount", "client"))
            for r in rows:
                print("   %-38s %-17s %10.2f  %s" % (
                    r["id"], r["status"], (r["amount_minor"] or 0) / 100.0, r["client"] or "—"))
            total += len(rows)
            if args.commit:
                from billing.statement import void_order
                done = 0
                for r in rows:
                    try:
                        void_order(s, club_id=str(c["id"]), order_id=str(r["id"]),
                                   reason="abandoned checkout — booking already cancelled")
                        done += 1
                    except Exception as e:
                        print("   !! %s: %s" % (r["id"], e.__class__.__name__))
                print("   -> voided %d/%d" % (done, len(rows)))

    print("\n%d orphaned order(s) found." % total)
    if total and not args.commit:
        print(">>> DRY-RUN — nothing changed. Re-run with --commit to void them.\n")
    elif total:
        print(">>> Done. The root cause is fixed too (release_expired_holds now voids on expiry),\n"
              "    so this should not need running again.\n")
    else:
        print(">>> Nothing to clean up.\n")


if __name__ == "__main__":
    main()

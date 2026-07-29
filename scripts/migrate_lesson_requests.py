# scripts/migrate_lesson_requests.py — clear the lesson requests left over from the approval gate.
#
#   python -m scripts.migrate_lesson_requests            # DRY RUN — report only (default)
#   python -m scripts.migrate_lesson_requests --commit   # act
#   python -m scripts.migrate_lesson_requests --club <uuid>
#
# WHY: the approval gate was removed on 2026-07-29 (one lesson flow — a lesson holds its slot and the
# settlement alone decides held vs confirmed; the coach is notified rather than asked). No NEW
# 'requested'/'proposed' lesson can be created, but the ones made while it did gate are still sitting
# in production, and accept/propose/decline stay alive only to finish them. This clears the queue so
# that path can be deleted.
#
# WHAT IT DOES, per pending request:
#   PAID          -> ACCEPT it. The client paid for a lesson; honour it (assigns a court, confirms,
#                    reuses the existing order — never a second charge).
#   IN THE PAST   -> CANCEL. Nobody is turning up to a lesson that was never confirmed, and leaving
#                    it pending keeps it on the coach's queue forever.
#   UNPAID FUTURE -> LEAVE IT. A human decision: the coach may still want it, or the client may have
#                    moved on. Reported so it can be worked through, never guessed at.
#
# Read-only by default. Every action is per-request and independently guarded, so one failure never
# blocks the rest, and re-running is safe (an already-accepted request is simply no longer pending).

import argparse
import sys
from datetime import datetime, timezone

from sqlalchemy import text

from db import get_engine, session_scope


def _money(minor):
    return f"R{(minor or 0) / 100:,.2f}"


def _pending(session, club_id=None):
    where = ["b.booking_type = 'lesson'", "b.status IN ('requested','proposed')"]
    params = {}
    if club_id:
        where.append("b.club_id = CAST(:club AS uuid)")
        params["club"] = club_id
    return session.execute(
        text("""
            SELECT b.id, b.club_id, b.status, b.starts_at, b.order_id, b.coach_user_id,
                   o.status AS order_status, o.amount_minor,
                   COALESCE(NULLIF(TRIM(CONCAT_WS(' ', cu.first_name, cu.surname)),''), 'the client')
                     AS client_name,
                   COALESCE(cp.display_name,
                            NULLIF(TRIM(CONCAT_WS(' ', co.first_name, co.surname)),'')) AS coach_name
            FROM diary.booking b
            LEFT JOIN billing."order" o ON o.id = b.order_id
            LEFT JOIN iam."user" cu ON cu.id = b.booked_by_user_id
            LEFT JOIN iam."user" co ON co.id = b.coach_user_id
            LEFT JOIN iam.coach_profile cp
                   ON cp.user_id = b.coach_user_id AND cp.club_id = b.club_id
            WHERE """ + " AND ".join(where) + """
            ORDER BY b.starts_at
        """),
        params,
    ).mappings().all()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true", help="actually accept/cancel")
    ap.add_argument("--club", help="limit to one club_id")
    args = ap.parse_args()

    get_engine()
    now = datetime.now(timezone.utc)
    accepted = cancelled = left = failed = 0

    with session_scope() as s:
        rows = _pending(s, club_id=args.club)
        if not rows:
            print("No pending lesson requests — the approval path can be deleted.")
            return 0
        print(f"{'COMMIT' if args.commit else 'DRY RUN'} — {len(rows)} pending lesson request(s)\n")

        for r in rows:
            bid = str(r["id"])
            when = r["starts_at"]
            paid = (r["order_status"] == "paid")
            past = when is not None and when < now
            who = f"{r['client_name']} with {r['coach_name'] or 'a coach'} · {when:%Y-%m-%d %H:%M}"

            if paid:
                print(f"  ACCEPT   {who}  ({_money(r['amount_minor'])} already paid)")
                if args.commit:
                    try:
                        from diary.bookings import accept_booking
                        res = accept_booking(s, club_id=str(r["club_id"]), booking_id=bid,
                                             actor_user_id=str(r["coach_user_id"]) if r["coach_user_id"] else None,
                                             role="club_admin")
                        if res.get("ok"):
                            accepted += 1
                        else:
                            failed += 1
                            print(f"           ! could not accept: {res.get('error')} — needs a human")
                    except Exception as e:
                        failed += 1
                        print(f"           ! {e.__class__.__name__} — needs a human")
                else:
                    accepted += 1

            elif past:
                print(f"  CANCEL   {who}  (never confirmed, already in the past)")
                if args.commit:
                    try:
                        from diary.bookings import decline_booking
                        res = decline_booking(s, club_id=str(r["club_id"]), booking_id=bid,
                                              actor_user_id=None, role="club_admin",
                                              reason="lesson request expired — approval retired")
                        cancelled += 1 if res.get("ok") else 0
                        failed += 0 if res.get("ok") else 1
                    except Exception as e:
                        failed += 1
                        print(f"           ! {e.__class__.__name__} — needs a human")
                else:
                    cancelled += 1

            else:
                left += 1
                print(f"  DECIDE   {who}  (unpaid, still in the future — left for a human)")

        print(f"\n  accept {accepted} · cancel {cancelled} · leave {left}"
              + (f" · FAILED {failed}" if failed else ""))
        if left:
            print("\n  The DECIDE rows are unpaid future requests. Accept them in the coach console")
            print("  (they still work), or cancel them and ask the client to rebook — the booking")
            print("  flow no longer asks a coach to approve anything.")
        if not args.commit:
            print("\nDRY RUN — nothing was written. Re-run with --commit to apply.")
            s.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main())

# scripts/audit_duplicate_heads.py — find lessons billed to MORE heads than played, and clear the
# duplicates by explicit id.
#
# WHY. A lesson can legitimately carry several orders: SEMI-PRIVATE bills PER HEAD, so a squad of
# three raises three owed orders at the service price and every one of them is a real debt. The same
# machinery produces a defect when a lesson is booked with a PAYER named as the client and the
# PLAYER added as an extra: two heads are billed for one person on court. Live example, found
# 2026-09-09 — a parent and a child, orders created in the SAME SECOND on each of three lessons, the
# parent's paid by a R550 'Pay all' and the child's left open for ever. The money was banked once,
# the coach accrued once, and the club's receivables carried a duplicate nobody could act on: opening
# it from the client's record lands on the PAID order, which offers nothing to do.
#
# THIS IS WHY IT DOES NOT VOID ANYTHING ON ITS OWN JUDGEMENT. The signal that separates a phantom
# head from a real squad member is WHO WAS ON COURT, and that is a fact about the club's day, not
# about the rows: two orders at one price on one lesson is a duplicate when one person played and a
# correct squad bill when two did. Guessing wrong ERASES A REAL DEBT — silently, in the direction
# that costs the club money and never reappears. So the audit reports, a human decides, and
# `--void-ids` acts only on ids typed out in full.
#
# WHAT --void-ids WILL REFUSE, whatever you type:
#   * an order that has taken ANY money (a payment row) — that is the refund path's business;
#   * an order that is not 'open'/'awaiting_payment' — nothing else is a live debt;
#   * an order with no PAID sibling at the same amount on the same booking — i.e. no evidence the
#     lesson was already paid for, which is the whole basis for calling it a duplicate.
# Voiding an unpaid order moves no money and touches no commission: the split accrued on the PAID
# sibling and is untouched.
#
# RUN IT (per docs/specs/DATA-ACCESS.md): Render -> courtflow-api -> Shell:
#     python -m scripts.audit_duplicate_heads
#     python -m scripts.audit_duplicate_heads --void-ids 787fa9e1-...,b777e61e-...   (+ typed YES)

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


def _r(minor):
    try:
        return "R%.2f" % (int(minor or 0) / 100.0)
    except Exception:
        return "R0.00"


def _candidates(session):
    """Bookings carrying an OPEN order and a PAID order at the SAME amount.

    Same amount matters: two heads at one price is the shape per-head billing produces, and it is
    also the shape a mis-keyed payer produces. A booking whose orders differ in amount is somebody
    genuinely buying different things and is not a candidate at all."""
    from sqlalchemy import text
    return session.execute(text("""
        WITH heads AS (
            SELECT DISTINCT ol.booking_id, o.id AS order_id, o.status, o.amount_minor,
                   o.user_id, o.created_at
            FROM billing.order_line ol
            JOIN billing."order" o ON o.id = ol.order_id
            WHERE ol.booking_id IS NOT NULL
        )
        SELECT h.booking_id,
               (b.starts_at AT TIME ZONE 'Africa/Johannesburg') AS starts,
               b.booking_type, b.status AS booking_status,
               h.order_id, h.status, h.amount_minor, h.user_id,
               (h.created_at AT TIME ZONE 'Africa/Johannesburg') AS created,
               NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.surname)), '') AS owner_name,
               u.email,
               (SELECT count(*) FROM diary.booking_party bp
                 WHERE bp.booking_id = h.booking_id) AS party_count
        FROM heads h
        JOIN diary.booking b ON b.id = h.booking_id
        LEFT JOIN iam.user u ON u.id = h.user_id
        WHERE h.booking_id IN (
            SELECT x.booking_id FROM heads x
            JOIN heads y ON y.booking_id = x.booking_id AND y.amount_minor = x.amount_minor
                        AND y.order_id <> x.order_id
            WHERE x.status = 'paid' AND y.status IN ('open','awaiting_payment')
        )
        ORDER BY b.starts_at, h.created_at
    """)).mappings().fetchall()


def _report(session):
    rows = _candidates(session)
    if not rows:
        print("\n   No lesson carries a paid order and an unpaid order at the same amount.")
        print("   Nothing to look at.")
        return []
    by_booking = {}
    for r in rows:
        by_booking.setdefault(r["booking_id"], []).append(r)

    print("\n   %d booking(s) carry a PAID and an UNPAID order at the same amount." % len(by_booking))
    print("   Read each one and decide: did that many people actually play?\n")
    suspects = []
    for bid, orders in by_booking.items():
        head = orders[0]
        print("   %s  %-7s %-10s  players recorded: %d"
              % (str(head["starts"])[:16], head["booking_type"], head["booking_status"],
                 head["party_count"]))
        same_second = len({str(o["created"])[:19] for o in orders}) == 1
        for o in orders:
            mark = ""
            if o["status"] in ("open", "awaiting_payment"):
                mark = "  <-- unpaid"
                suspects.append(o)
            print("        %s  %-9s %9s  %-26s%s"
                  % (o["order_id"], o["status"], _r(o["amount_minor"]),
                     (o["owner_name"] or o["email"] or "?")[:26], mark))
        if same_second:
            print("        ^ all created in the SAME SECOND - one booking action raised every one of")
            print("          these, so this is the per-head path, not somebody buying twice.")
        print()

    total = sum(int(o["amount_minor"] or 0) for o in suspects)
    print("   %s of unpaid debt sits beside an already-paid order for the same lesson." % _r(total))
    print("   Where only ONE person actually played, that unpaid row is a duplicate and should be")
    print("   voided. Where the lesson really was a squad, it is a REAL debt - leave it and chase it.")
    print("\n   To clear the ones you have judged duplicates, pass their FULL ids:")
    print("     python -m scripts.audit_duplicate_heads --void-ids <id>,<id>")
    return suspects


def _void(session, ids):
    """Void the named orders, re-checking every safety condition against the database first."""
    from sqlalchemy import text
    done = refused = 0
    for oid in ids:
        row = session.execute(text(
            'SELECT o.id, o.club_id, o.status, o.amount_minor, o.user_id, '
            '       (SELECT count(*) FROM billing.payment p WHERE p.order_id = o.id) AS pays, '
            '       (SELECT count(*) FROM billing.order_line ol '
            '          JOIN billing."order" o2 ON o2.id = ol.order_id '
            '         WHERE ol.booking_id IN (SELECT ol2.booking_id FROM billing.order_line ol2 '
            '                                  WHERE ol2.order_id = o.id) '
            '           AND o2.status = \'paid\' AND o2.amount_minor = o.amount_minor '
            '           AND o2.id <> o.id) AS paid_siblings '
            'FROM billing."order" o WHERE o.id = CAST(:i AS uuid)'), {"i": oid}).mappings().first()
        if not row:
            print("   REFUSED %s - no such order" % oid)
            refused += 1
            continue
        if row["status"] not in ("open", "awaiting_payment"):
            print("   REFUSED %s - status is '%s', not a live debt" % (oid, row["status"]))
            refused += 1
            continue
        if int(row["pays"] or 0):
            print("   REFUSED %s - it has taken money; that is the refund path, not this" % oid)
            refused += 1
            continue
        if not int(row["paid_siblings"] or 0):
            print("   REFUSED %s - no PAID order at the same amount on the same lesson, so there is"
                  % oid)
            print("            no evidence this is a duplicate. It looks like a real debt.")
            refused += 1
            continue
        session.execute(text(
            'UPDATE billing."order" SET status = \'void\', '
            "       void_reason = 'duplicate head order - the lesson was paid on another order', "
            "       updated_at = now() "
            'WHERE id = :i AND club_id = :c AND status IN (\'open\',\'awaiting_payment\')'),
            {"i": row["id"], "c": row["club_id"]})
        print("   voided  %s  %s" % (oid, _r(row["amount_minor"])))
        done += 1
    print("\n   voided %d, refused %d" % (done, refused))
    return done


def main():
    ap = argparse.ArgumentParser(
        description="Find lessons billed to more heads than played; void named duplicates.")
    ap.add_argument("--void-ids", help="comma-separated FULL order ids to void (needs a typed YES)")
    args = ap.parse_args()

    _load_env()
    import db

    print("\nDuplicate head orders            (%s)"
          % ("VOIDING the ids you named" if args.void_ids else "READ-ONLY"))
    print("=" * 78)
    with db.session_scope() as s:
        _report(s)
        if not args.void_ids:
            print("\n" + "=" * 78)
            print("Nothing was written.\n")
            return 0
        ids = [x.strip() for x in args.void_ids.split(",") if x.strip()]
        print("\n   About to VOID %d order(s). This cancels a debt; it moves no money and does not"
              % len(ids))
        print("   touch commission, which accrued on the PAID order.")
        try:
            if input("   Type YES to proceed: ").strip() != "YES":
                print("   Aborted. Nothing written.")
                return 1
        except EOFError:
            print("   No terminal to confirm on. Aborted. Nothing written.")
            return 1
        print()
        _void(s, ids)

    print("\n" + "=" * 78 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

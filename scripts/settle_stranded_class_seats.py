"""settle_stranded_class_seats — remediate class seats stuck in 'awaiting_payment' forever.

A seat taken with the ONLINE mode holds its place against a pending Yoco charge, and its order sits
in 'awaiting_payment' — a PENDING-CHECKOUT state that is deliberately invisible to the statement,
month-end and invoicing so an in-flight charge is never double-collected. If the player then turns
up and plays, that assumption stops being true: the class was delivered and the money is genuinely
owed, but no invoice will ever ask for it. Check-in now converts the seat automatically
(diary.classes._settle_held_seat_for_attendance) — this script is for the seats stranded BEFORE
that fix, which the sweep would otherwise skip for good.

    python -m scripts.settle_stranded_class_seats                 # dry run (default)
    python -m scripts.settle_stranded_class_seats --settle        # -> owed, so it gets invoiced
    python -m scripts.settle_stranded_class_seats --void          # -> seat cancelled, order voided

--settle is the right call when the player took the seat: the order becomes a normal at-court debt
on the same rails as everyone else. --void is for seats nobody ever used. Neither runs without the
explicit flag, and a seat whose order has since been PAID is never touched.
"""
import sys

from sqlalchemy import text

from db import session_scope

STRANDED = """
SELECT e.id            AS enrolment_id,
       e.user_id,
       u.email,
       e.status        AS seat,
       e.order_id,
       o.amount_minor,
       o.status        AS order_status,
       cs.starts_at,
       r.name          AS class_name
FROM diary.enrolment e
JOIN billing."order" o    ON o.id = e.order_id
JOIN diary.class_session cs ON cs.id = e.class_session_id
LEFT JOIN diary.resource r  ON r.id = cs.resource_id
LEFT JOIN iam."user" u      ON u.id = e.user_id
WHERE e.club_id = :c
  AND e.status IN ('enrolled', 'attended')
  AND o.status = 'awaiting_payment'
ORDER BY cs.starts_at
"""


def rand(minor):
    return "R{:,.2f}".format((minor or 0) / 100.0)


def main(argv):
    settle = "--settle" in argv
    void = "--void" in argv
    if settle and void:
        print("Pick one: --settle or --void, not both.")
        return 2
    club = argv[argv.index("--club") + 1] if "--club" in argv else None

    with session_scope() as s:
        if club:
            clubs = [club]
        else:
            clubs = [str(r[0]) for r in s.execute(
                text("SELECT id FROM club.club ORDER BY created_at")).all()]

        total = 0
        touched = 0
        for cid in clubs:
            rows = s.execute(text(STRANDED), {"c": cid}).mappings().all()
            if not rows:
                continue
            print("")
            print("club {} - {} stranded seat(s)".format(cid, len(rows)))
            print("")
            for r in rows:
                total += int(r["amount_minor"] or 0)
                print("  {}  {:<32} {:<10} {:>10}  {}".format(
                    r["starts_at"].strftime("%Y-%m-%d %H:%M"),
                    (r["email"] or "?")[:32],
                    r["seat"],
                    rand(int(r["amount_minor"] or 0)),
                    (r["class_name"] or "?")[:24]))

                if settle:
                    # Exactly what check-in does: the debt joins the normal at-court rails, so the
                    # month-end sweep will consolidate and invoice it like any other balance.
                    s.execute(
                        text('UPDATE billing."order" SET status = \'open\', '
                             "settlement_mode = 'at_court', updated_at = now() "
                             "WHERE id = :o AND status = 'awaiting_payment'"),
                        {"o": str(r["order_id"])})
                    s.execute(
                        text("UPDATE diary.enrolment SET held_until = NULL, "
                             "settlement_mode = 'at_court', updated_at = now() "
                             "WHERE id = :id"),
                        {"id": r["enrolment_id"]})
                    touched += 1
                elif void:
                    s.execute(
                        text('UPDATE billing."order" SET status = \'void\', '
                             "updated_at = now() "
                             "WHERE id = :o AND status = 'awaiting_payment'"),
                        {"o": str(r["order_id"])})
                    s.execute(
                        text("UPDATE diary.enrolment SET status = 'cancelled', "
                             "held_until = NULL, updated_at = now() WHERE id = :id"),
                        {"id": r["enrolment_id"]})
                    touched += 1

        print("")
        print("total stranded: {}".format(rand(total)))
        if settle:
            print("SETTLED {} seat(s) -> owed. They will be invoiced by the next".format(touched))
            print("month-end sweep (preview it: python -m scripts.preview_month_end).")
        elif void:
            print("VOIDED {} seat(s). Nobody will be billed for them.".format(touched))
        else:
            print("DRY RUN - nothing changed. Re-run with --settle or --void.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

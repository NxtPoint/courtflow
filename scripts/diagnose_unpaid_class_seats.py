#!/usr/bin/env python
"""READ-ONLY: unpaid class-seat charges that the coach's work log does not count.

Why this exists (2026-11-15): Allon's card said R14,360 of invoices outstanding while his clients'
unpaid lines added up to R14,900. The R540 gap was three R180 class seats that are still OPEN debts
on the client's account but are missing from the work log, which counts a seat only while the
enrolment is 'enrolled'/'attended' and the session is not cancelled. A seat that was cancelled or
marked no-show while its charge stayed open is either a legitimate no-show fee or a ghost debt the
client should never have been billed. This prints every such seat so a human can tell which.

For every OPEN order that bills a class seat, it shows the enrolment and session behind it, and
flags it when the work log would skip it.

Nothing is written. Safe to run against production (Render -> courtflow-api -> Shell):

    python -m scripts.diagnose_unpaid_class_seats                 # every coach
    python -m scripts.diagnose_unpaid_class_seats --coach Allon   # name / email match
    python -m scripts.diagnose_unpaid_class_seats --all           # also list seats the log DOES count
"""
import sys

from sqlalchemy import text

from db import session_scope


def _money(minor):
    return f"R{(minor or 0) / 100:,.2f}"


def _arg(argv, flag, default=None):
    return argv[argv.index(flag) + 1] if flag in argv and len(argv) > argv.index(flag) + 1 else default


def main(argv):
    who = _arg(argv, "--coach")
    show_all = "--all" in argv

    with session_scope() as s:
        rows = s.execute(
            text("""
                SELECT o.id AS order_id, o.status AS order_status, o.created_at AS order_created,
                       ol.amount_minor,
                       e.id AS enrolment_id, e.status AS enrol_status, e.updated_at AS enrol_updated,
                       e.order_id AS enrol_order_id,
                       cs.id AS session_id, cs.starts_at, cs.status AS session_status,
                       COALESCE(pr.name, ol.description) AS service,
                       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', cu.first_name, cu.surname)),''), cu.email) AS coach,
                       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', pu.first_name, pu.surname)),''), pu.email) AS payer,
                       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', su.first_name, su.surname)),''), su.email) AS player,
                       (SELECT a.status FROM billing.coach_arrears a
                         WHERE a.club_id = o.club_id
                           AND (a.order_line_id = ol.id OR a.enrolment_id = e.id)
                         ORDER BY a.created_at DESC LIMIT 1) AS arrears_status,
                       -- Did the same player end up with ANOTHER seat in the same session?
                       -- (a cancel + re-book leaves the old charge behind if it was never voided)
                       (SELECT count(*) FROM diary.enrolment e2
                         WHERE e2.class_session_id = cs.id AND e2.id <> e.id
                           AND e2.user_id = e.user_id) AS other_seats_same_session
                FROM billing."order" o
                JOIN billing.order_line ol ON ol.order_id = o.id AND ol.enrolment_id IS NOT NULL
                LEFT JOIN diary.enrolment e     ON e.id = ol.enrolment_id
                LEFT JOIN diary.class_session cs ON cs.id = e.class_session_id
                LEFT JOIN billing.price   p2 ON p2.id = ol.price_id
                LEFT JOIN billing.product pr ON pr.id = p2.product_id
                LEFT JOIN iam."user" cu ON cu.id = cs.coach_user_id
                LEFT JOIN iam."user" pu ON pu.id = o.user_id
                LEFT JOIN iam."user" su ON su.id = e.user_id
                WHERE o.status IN ('open', 'awaiting_payment')
                  AND (CAST(:who AS text) IS NULL
                       OR COALESCE(cu.first_name,'') ILIKE '%' || CAST(:who AS text) || '%'
                       OR COALESCE(cu.surname,'')    ILIKE '%' || CAST(:who AS text) || '%'
                       OR COALESCE(cu.email,'')      ILIKE '%' || CAST(:who AS text) || '%')
                ORDER BY coach, cs.starts_at NULLS FIRST
            """),
            {"who": who},
        ).mappings().all()

        print("UNPAID CLASS SEATS  (read-only)\n")
        skipped_total = 0
        skipped = 0
        for r in rows:
            counted = (r["enrol_status"] in ("enrolled", "attended")
                       and r["session_status"] not in (None, "cancelled"))
            if counted and not show_all:
                continue
            if not counted:
                skipped += 1
                skipped_total += int(r["amount_minor"] or 0)
            why = []
            if r["enrolment_id"] is None:
                why.append("enrolment row is GONE")
            elif r["enrol_status"] not in ("enrolled", "attended"):
                why.append(f"seat is '{r['enrol_status']}'")
            if r["session_status"] == "cancelled":
                why.append("session CANCELLED")
            if r["enrol_order_id"] and str(r["enrol_order_id"]) != str(r["order_id"]):
                why.append("seat now points at a DIFFERENT order")
            if r["other_seats_same_session"]:
                why.append("player has another seat in this session")
            when = r["starts_at"].strftime("%Y-%m-%d %H:%M") if r["starts_at"] else "?"
            print(f"{'NOT IN WORK LOG' if not counted else 'counted       '}  {when}  "
                  f"{_money(r['amount_minor']):>10}  {r['coach'] or '?'}  ·  {r['service'] or 'class'}")
            print(f"    player {r['player'] or '?'}   billed to {r['payer'] or '?'}")
            print(f"    order {r['order_id']} ({r['order_status']}, created "
                  f"{r['order_created']:%Y-%m-%d})   coach tab: {r['arrears_status'] or '-'}")
            if why:
                print(f"    why: {'; '.join(why)}"
                      + (f"  (seat last changed {r['enrol_updated']:%Y-%m-%d})" if r["enrol_updated"] else ""))
            print()

        print("-" * 78)
        print(f"{skipped} unpaid seat charge(s) the work log does not count, {_money(skipped_total)} in all.")
        print("A 'no_show' seat may be a fair charge. A 'cancelled' seat or a cancelled session")
        print("usually means the client is billed for a class they did not take.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

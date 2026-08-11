"""fix_unbilled_seats — void seat charges raised while the club had charging switched OFF.

WHY THIS EXISTS
---------------
`community.seats.apply_seat_orders` used to trust its CALLER to check
`club.policy.seat_rule_enforced`. Of its four callers exactly one did, so `join_game`,
`set_visibility` and invite-accept all billed a seat share in clubs that had "Charge for every
seat" switched off. Found live 2026-08-11: NextPoint had Community features ON and charging OFF,
Tshepo took a seat in a game, and the club dashboard read "Seats unpaid R110.00".

The code fix moves the gate inside `apply_seat_orders` (guarded by
`sc_joining_a_game_bills_nobody_while_the_money_switch_is_off`). This script cleans up the debts
the old behaviour already raised — a member must not be left owing money the club never meant to
charge, and an invoice or a month-end sweep would otherwise pick it up as real receivable.

WHAT IT TOUCHES
---------------
ONLY orders that are ALL of:
  · linked to a seat (`diary.booking_party.order_id`) — never a booking's own court order,
  · in a club whose `seat_rule_enforced` is currently FALSE,
  · not yet paid (status NOT IN paid/void/refunded) — a settled charge is a refund decision for a
    human, never a script's,
so it cannot touch a club that is legitimately charging, and it cannot take money off anyone.

It voids through `billing.statement.void_order` rather than deleting, so the audit trail says what
happened and the client statement, Client-360 and Club earnings all stay reconciled. It then clears
the seat's money fields, leaving the seat itself intact — the player keeps their place in the game.

HOW TO RUN (see docs/specs/DATA-ACCESS.md)
------------------------------------------
In the Render `courtflow-api` -> Shell tab, where DATABASE_URL is already in the environment:

    python -m scripts.fix_unbilled_seats            # DRY RUN — prints, changes nothing
    python -m scripts.fix_unbilled_seats --commit   # writes

Idempotent: a second run finds nothing.
"""
from __future__ import annotations

import sys

from sqlalchemy import text

import db

_FIND = text(
    """
    SELECT bp.id            AS seat_id,
           bp.club_id,
           bp.user_id,
           bp.order_id,
           o.amount_minor,
           o.status         AS order_status,
           b.id             AS booking_id,
           b.starts_at,
           u.email
      FROM diary.booking_party bp
      JOIN billing."order" o ON o.id = bp.order_id
      JOIN diary.booking   b ON b.id = bp.booking_id
      JOIN club.policy     p ON p.club_id = bp.club_id
      LEFT JOIN iam."user" u ON u.id = bp.user_id
     WHERE COALESCE(p.seat_rule_enforced, false) = false
       AND o.status NOT IN ('paid', 'void', 'refunded')
     ORDER BY b.starts_at
    """
)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    commit = "--commit" in argv

    with db.session_scope() as session:
        rows = [dict(r) for r in session.execute(_FIND).mappings().all()]

        if not rows:
            print("Nothing to do — no unpaid seat charges in clubs with charging switched off.")
            return 0

        total = sum(int(r["amount_minor"] or 0) for r in rows)
        print("%d seat charge(s) raised while charging was OFF, totalling R%.2f\n"
              % (len(rows), total / 100.0))
        for r in rows:
            print("  seat %s  %s  R%.2f  %s  (booking %s, %s)"
                  % (str(r["seat_id"])[:8], (r["email"] or "?")[:34].ljust(34),
                     int(r["amount_minor"] or 0) / 100.0, r["order_status"],
                     str(r["booking_id"])[:8], r["starts_at"]))

        if not commit:
            print("\nDRY RUN — nothing written. Re-run with --commit to void these.")
            session.rollback()
            return 0

        from billing.statement import void_order

        done = 0
        for r in rows:
            void_order(session, club_id=r["club_id"], order_id=r["order_id"],
                       reason="seat charged while the club's seat rule was switched off")
            # The seat KEEPS its place — only the money is undone. covered stays NULL because these
            # seats were never free by entitlement; they are free because the club is not charging.
            session.execute(
                text("UPDATE diary.booking_party "
                     "   SET order_id = NULL, share_minor = NULL, "
                     "       seat_status = CASE WHEN seat_status = 'held' THEN 'confirmed' "
                     "                          ELSE seat_status END "
                     " WHERE id = :id"),
                {"id": str(r["seat_id"])})
            done += 1

        # The frozen quote goes too: nothing was sold, so nothing should pin a price. If the club
        # later switches charging on, the game prices from the policy in force at that time.
        session.execute(
            text("UPDATE diary.booking b SET seat_share_minor = NULL "
                 "  FROM club.policy p "
                 " WHERE p.club_id = b.club_id "
                 "   AND COALESCE(p.seat_rule_enforced, false) = false "
                 "   AND b.seat_share_minor IS NOT NULL"))

        print("\nVoided %d seat charge(s), R%.2f. Seats kept their places."
              % (done, total / 100.0))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

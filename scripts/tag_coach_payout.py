"""Say which MONTH a recorded club<->coach payout settles.

WHY THIS EXISTS. A payout is credited to the month it SETTLES, not the day the cash moved
(`billing.coach_payout.period_label`; see `billing.commission.coach_pnl`). July's commission is
routinely paid in the first days of August — and an UNLABELLED payout falls back to `occurred_at`,
so it credits August: July keeps showing the full amount as still due, the owner pays it a second
time, and August carries a credit belonging to another month. No month can ever be closed.

The Record-payout modal now asks for the month and defaults it to the card on screen, so new
payouts are labelled. This script is for the ones recorded BEFORE that — list them, then tag.

    python -m scripts.tag_coach_payout --coach "Allon"                     # list, changes nothing
    python -m scripts.tag_coach_payout --coach "Allon" --id <uuid> --period 2026-07
    python -m scripts.tag_coach_payout --coach "Allon" --id <uuid> --period 2026-07 --commit

DRY-RUN BY DEFAULT — without --commit it prints the before/after and ROLLS BACK. Tagging moves
money between months on a live statement, so it is never the default.

Labelling changes NO amount and posts NO ledger entry: it only says which month the existing
payout belongs to. To undo, tag it back (or `--period ""` to clear the label, restoring the old
occurred_at behaviour exactly).
"""
from __future__ import annotations

import argparse
import re
import sys

from sqlalchemy import text

import db

_PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _resolve_coach(session, needle):
    """Find ONE coach by name or email, and their club.

    The club is taken FROM THE COACH, never "the first club" — in a multi-club database that is a
    coin flip, and this writes to a money record.
    """
    rows = session.execute(
        text("SELECT u.id, u.email, "
             "       NULLIF(TRIM(COALESCE(u.first_name,'') || ' ' || COALESCE(u.surname,'')),'') AS full_name, "
             "       m.club_id, c.name AS club_name "
             "FROM iam.\"user\" u "
             "JOIN iam.membership m ON m.user_id = u.id "
             "JOIN club.club c ON c.id = m.club_id "
             "WHERE m.role IN ('coach','club_admin') "
             "  AND (u.email ILIKE :q OR COALESCE(u.first_name,'') ILIKE :q "
             "       OR COALESCE(u.surname,'') ILIKE :q)"),
        {"q": f"%{needle}%"},
    ).mappings().all()
    if not rows:
        sys.exit(f"No coach matches {needle!r}.")
    if len(rows) > 1:
        print(f"{needle!r} matches {len(rows)} people — be more specific:")
        for r in rows:
            print(f"  {r['full_name'] or '(no name)'} <{r['email']}>  {r['id']}")
        sys.exit(2)
    return rows[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--coach", required=True, help="name or email fragment")
    ap.add_argument("--id", help="the payout to tag (from the listing)")
    ap.add_argument("--period", help="'YYYY-MM' it settles, or '' to clear the label")
    ap.add_argument("--commit", action="store_true", help="actually write (default: roll back)")
    a = ap.parse_args()

    with db.session_scope() as s:
        coach = _resolve_coach(s, a.coach)
        print(f"{coach['full_name'] or coach['email']}  |  {coach['club_name']}")

        rows = s.execute(
            text("SELECT id, direction, amount_minor, currency, method, reference, period_label, "
                 "       status, created_at, paid_at "
                 "FROM billing.coach_payout WHERE club_id = :c AND coach_user_id = :u "
                 "ORDER BY created_at DESC LIMIT 50"),
            {"c": str(coach["club_id"]), "u": str(coach["id"])},
        ).mappings().all()
        if not rows:
            print("  No payouts recorded.")
            return 0

        print(f"\n  {'payout':38} {'amount':>12}  {'settles':8} {'fallback':8} direction")
        for r in rows:
            moved = (r["paid_at"] or r["created_at"])
            fallback = moved.strftime("%Y-%m") if moved else "?"
            settles = r["period_label"] or f"({fallback})"
            amt = f"{r['currency'] or 'ZAR'} {(r['amount_minor'] or 0) / 100:,.2f}"
            flag = "" if r["period_label"] else "   <- unlabelled, credits the month it moved"
            print(f"  {str(r['id']):38} {amt:>12}  {settles:8} {fallback:8} "
                  f"{r['direction']}{flag}")

        if not a.id:
            print("\n  Nothing changed. Pass --id <payout> --period YYYY-MM to tag one.")
            return 0

        if a.period is None:
            sys.exit("--id needs --period (use --period '' to clear the label).")
        period = (a.period or "").strip()
        if period and not _PERIOD.match(period):
            sys.exit(f"--period must be 'YYYY-MM' (got {a.period!r}).")

        # Scope the UPDATE by club AND coach as well as id — the id came off a console, and a
        # payout is the one record where a mis-typed uuid moves somebody else's money.
        before = s.execute(
            text("SELECT period_label, amount_minor, currency FROM billing.coach_payout "
                 "WHERE id = :i AND club_id = :c AND coach_user_id = :u"),
            {"i": a.id, "c": str(coach["club_id"]), "u": str(coach["id"])},
        ).mappings().first()
        if not before:
            sys.exit(f"Payout {a.id} is not one of {coach['full_name'] or coach['email']}'s.")

        s.execute(
            text("UPDATE billing.coach_payout SET period_label = NULLIF(:p,'') "
                 "WHERE id = :i AND club_id = :c AND coach_user_id = :u"),
            {"p": period, "i": a.id, "c": str(coach["club_id"]), "u": str(coach["id"])},
        )
        amt = f"{before['currency'] or 'ZAR'} {(before['amount_minor'] or 0) / 100:,.2f}"
        was = before["period_label"] or "(unlabelled)"
        print(f"\n  {amt}: settles {was} -> {period or '(unlabelled)'}")

        if not a.commit:
            s.rollback()
            print("  DRY RUN - rolled back. Re-run with --commit to keep it.")
        else:
            print("  Committed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

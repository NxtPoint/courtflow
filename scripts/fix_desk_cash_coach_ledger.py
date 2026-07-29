#!/usr/bin/env python
"""Correct historical coach_ledger entries for coaching settled in CASH or CARD-AT-DESK.

THE RULE (owner, 2026-07-29): the club can only actually RECEIVE money two ways — a Yoco charge or an
EFT into its account. Anything else recorded against a COACHING order is cash the coach took from the
client directly; the club has no facility for collecting on a coach's behalf.

THE BUG THIS CORRECTS: `record_split_for_order` hard-coded `cash_held_by='club'` for EVERY payment
path, so a lesson settled in cash at the court posted `+coach_net` — the club owes the coach his
share — when the coach was standing there holding the money. The club was in fact owed its
commission, so the ledger was wrong by the WHOLE GROSS on each one, and it surfaced as "Coach payouts
due" telling the owner to pay a coach who was holding the club's money. Fixed forward in
`billing/commission.py` (`cash_custody_for`); this repairs the rows already written.

Exactly the same shape and the same safety as `fix_inverted_coach_ledger.py`:
  · DRY RUN by default — prints what it would do and rolls back. `--commit` to apply.
  · APPENDS one correcting `adjustment` per coach. It never rewrites history: the wrong entries and
    the correction sit side by side so the trail stays auditable.
  · IDEMPOTENT on a fixed ref_id — a second `--commit` writes nothing.
  · commission_split rows are NOT touched. The sale divided the same way whoever held the cash, so
    commission REPORTING was always right; only the running balance was lying.

    python -m scripts.fix_desk_cash_coach_ledger            # dry run
    python -m scripts.fix_desk_cash_coach_ledger --commit   # apply
"""
import sys

from sqlalchemy import bindparam, text

from db import session_scope

REF = "fix-desk-cash-coach-ledger"      # deterministic → idempotent

# The two providers that genuinely reach the club. Kept in step with
# billing.commission.CLUB_BANKED_PROVIDERS.
CLUB_BANKED = ("yoco", "eft")


def _money(minor):
    return f"R{(minor or 0) / 100:,.2f}"


def _already_fixed(session, club_id, coach_user_id):
    return session.execute(
        text("SELECT 1 FROM billing.coach_ledger WHERE club_id=:c AND coach_user_id=:u "
             "AND entry_type='adjustment' AND ref_id=:r LIMIT 1"),
        {"c": club_id, "u": coach_user_id, "r": REF},
    ).first() is not None


def main(argv):
    commit = "--commit" in argv
    print("DESK-CASH coaching collections booked as if the CLUB held the money"
          if commit else "DRY RUN — desk-cash coaching collections booked the wrong way")
    print()

    with session_scope() as s:
        # Every commission_earning whose split was paid by a NON-club provider. Those are the rows
        # that should have been `commission_due` (−owner_cut) instead of `+coach_net`.
        rows = s.execute(
            text("""
                SELECT l.club_id, l.coach_user_id,
                       COALESCE(cl.name, '(club)') AS club_name,
                       COALESCE(cp.display_name,
                                NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.surname)), ''),
                                u.email, '(coach)') AS coach_name,
                       COUNT(*)                              AS n,
                       COALESCE(SUM(cs.gross_minor), 0)      AS gross,
                       COALESCE(SUM(l.amount_minor), 0)      AS posted,
                       COALESCE(SUM(-owner.amount_minor), 0) AS correct
                FROM billing.coach_ledger l
                JOIN billing.commission_split cs ON CAST(cs.id AS text) = l.ref_id
                                                AND cs.party_type = 'coach'
                JOIN billing.payment pm ON pm.id = cs.payment_id
                -- the OWNER side of the same payment+line is the club's cut
                JOIN billing.commission_split owner
                     ON owner.payment_id = cs.payment_id
                    AND owner.order_line_id = cs.order_line_id
                    AND owner.party_type = 'owner'
                LEFT JOIN club.club cl ON cl.id = l.club_id
                LEFT JOIN iam."user" u ON u.id = l.coach_user_id
                LEFT JOIN iam.coach_profile cp
                       ON cp.user_id = l.coach_user_id AND cp.club_id = l.club_id
                WHERE l.entry_type = 'commission_earning'
                  AND LOWER(pm.provider) NOT IN :banked
                GROUP BY l.club_id, l.coach_user_id, cl.name, coach_name
                ORDER BY cl.name, coach_name
            """).bindparams(bindparam("banked", value=CLUB_BANKED, expanding=True)),
        ).mappings().all()

        if not rows:
            print("  Nothing to correct — no coaching was settled in cash or card-at-desk.\n")
            return 0

        total = 0
        for r in rows:
            posted, correct = int(r["posted"]), int(r["correct"])
            delta = correct - posted
            done = _already_fixed(s, r["club_id"], r["coach_user_id"])
            print(f"  {r['coach_name']}  ({r['club_name']})")
            print(f"    {r['n']} cash/desk collection(s), {_money(r['gross'])} gross")
            print(f"    posted    {_money(posted):>14}  (credited to the coach — wrong)")
            print(f"    correct   {_money(correct):>14}  (the club's commission, owed BY the coach)")
            if done:
                print("    ALREADY CORRECTED — skipping\n")
                continue
            bal = int(s.execute(
                text("SELECT COALESCE(SUM(amount_minor),0) FROM billing.coach_ledger "
                     "WHERE club_id=:c AND coach_user_id=:u"),
                {"c": r["club_id"], "u": r["coach_user_id"]}).scalar() or 0)
            print(f"    balance  {_money(bal)}  →  {_money(bal + delta)}")
            total += delta
            if commit:
                s.execute(
                    text("INSERT INTO billing.coach_ledger (club_id, coach_user_id, entry_type, "
                         "amount_minor, currency, ref_type, ref_id, note) "
                         "VALUES (:c,:u,'adjustment',:a,'ZAR','correction',:r,:n)"),
                    {"c": r["club_id"], "u": r["coach_user_id"], "a": delta, "r": REF,
                     "n": "Correction: coaching settled in cash/card at the court was booked as if "
                          "the club held it. The club only receives Yoco and EFT."},
                )
                print("    → correcting adjustment WRITTEN")
            else:
                print("    → would write a correcting adjustment")
            print()

        print(f"{len(rows)} coach(es), net correction {_money(total)}\n")
        if not commit:
            print("DRY RUN — nothing was written. Re-run with --commit to apply.")
            raise SystemExit(0)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception as e:                                        # noqa: BLE001
        print(f"FAILED: {e}")
        sys.exit(1)

# scripts/fix_inverted_coach_ledger.py — correct the club↔coach balances that the inverted
# arrears ledger entry left behind.
#
#   python -m scripts.fix_inverted_coach_ledger                  # DRY RUN — report only (default)
#   python -m scripts.fix_inverted_coach_ledger --commit         # write the correcting entries
#   python -m scripts.fix_inverted_coach_ledger --club <uuid>    # one club (default: all)
#
# THE BUG (fixed forward in billing/commission.py::_write_split_pair):
#   billing.coach_ledger is signed — (+) the club owes the coach, (−) the coach owes the club. A
#   'commission_earning' of +coach_net is correct only when the CLUB took the money and is holding
#   it. But `mark_arrears_collected` — off-platform by definition, the coach chased the payment into
#   their OWN account — posted the same +coach_net. The coach already held the full gross, so the
#   club was owed its commission and the ledger said the exact opposite.
#
#   Per collection the balance is wrong by  coach_net + owner_cut  ==  the FULL GROSS.
#
# WHAT THIS FIXES: every ledger row of entry_type='commission_earning' whose commission_split has
# basis='arrears_commission' (the off-platform marker — a club-collected split never carries it).
# It does NOT rewrite history: it appends ONE correcting 'adjustment' per affected coach, so the
# audit trail keeps both the original entry and the correction. Idempotent — the adjustment carries
# a deterministic ref_id, so a second --commit run writes nothing.
#
# The money itself is unchanged: what the client paid, and to whom, is not in question. Only the
# running balance between the club and the coach moves.

import argparse
import sys

from sqlalchemy import text

from db import get_engine, session_scope

REF = "fix-inverted-arrears-ledger"      # deterministic → idempotent


def _rows(session, club_id=None):
    """Every ledger entry that credited a coach for money they collected themselves."""
    where = ["l.entry_type = 'commission_earning'", "cs.basis = 'arrears_commission'"]
    params = {}
    if club_id:
        where.append("l.club_id = CAST(:club AS uuid)")
        params["club"] = club_id
    return session.execute(
        text("""
            SELECT l.club_id, l.coach_user_id,
                   COALESCE(u.first_name,'') || ' ' || COALESCE(u.surname,'') AS coach_name,
                   COUNT(*)                            AS n,
                   COALESCE(SUM(l.amount_minor),0)     AS credited,   -- what was posted (+coach_net)
                   COALESCE(SUM(cs.gross_minor),0)     AS gross,      -- the full sale
                   COALESCE(SUM(cs2.amount_minor),0)   AS owner_cut   -- what the club should be owed
            FROM billing.coach_ledger l
            JOIN billing.commission_split cs
              ON CAST(cs.id AS text) = l.ref_id AND cs.party_type = 'coach'
            -- the OWNER half of the same split pair carries the club's cut
            LEFT JOIN billing.commission_split cs2
              ON cs2.order_line_id IS NOT DISTINCT FROM cs.order_line_id
             AND cs2.party_type = 'owner' AND cs2.club_id = cs.club_id
            LEFT JOIN iam."user" u ON u.id = l.coach_user_id
            WHERE """ + " AND ".join(where) + """
            GROUP BY l.club_id, l.coach_user_id, u.first_name, u.surname
            ORDER BY 1, 3
        """),
        params,
    ).mappings().all()


def _already_fixed(session, club_id, coach_user_id):
    return session.execute(
        text("SELECT 1 FROM billing.coach_ledger WHERE club_id = :c AND coach_user_id = :u "
             "AND entry_type = 'adjustment' AND ref_id = :r LIMIT 1"),
        {"c": club_id, "u": coach_user_id, "r": REF},
    ).first() is not None


def _balance(session, club_id, coach_user_id):
    return int(session.execute(
        text("SELECT COALESCE(SUM(amount_minor),0) FROM billing.coach_ledger "
             "WHERE club_id = :c AND coach_user_id = :u"),
        {"c": club_id, "u": coach_user_id}).scalar() or 0)


def _money(minor):
    return f"R{minor / 100:,.2f}"


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true", help="write the correcting entries")
    ap.add_argument("--club", help="limit to one club_id")
    args = ap.parse_args()

    get_engine()
    total_correction = 0
    affected = 0
    with session_scope() as s:
        rows = _rows(s, club_id=args.club)
        if not rows:
            print("No inverted arrears entries found — nothing to correct.")
            return 0
        print(f"{'COMMIT' if args.commit else 'DRY RUN'} — inverted arrears ledger entries\n")
        for r in rows:
            club_id, coach = str(r["club_id"]), str(r["coach_user_id"])
            name = (r["coach_name"] or "").strip() or coach[:8]
            credited = int(r["credited"] or 0)          # what WAS posted  (+coach_net)
            owner_cut = int(r["owner_cut"] or 0)        # what SHOULD be   (−owner_cut)
            # The correction removes the wrong credit and applies the right debit.
            correction = -(credited + owner_cut)
            before = _balance(s, club_id, coach)
            done = _already_fixed(s, club_id, coach)
            print(f"  {name}")
            print(f"    {r['n']} off-platform collection(s), {_money(int(r['gross'] or 0))} gross")
            print(f"    posted   {_money(credited):>14}  (credited to the coach — wrong)")
            print(f"    correct  {_money(-owner_cut):>14}  (the club's commission, owed BY the coach)")
            print(f"    balance  {_money(before)}  →  {_money(before + correction)}")
            if done:
                print("    ALREADY CORRECTED — skipping\n")
                continue
            affected += 1
            total_correction += correction
            if args.commit:
                s.execute(
                    text("""
                        INSERT INTO billing.coach_ledger
                            (club_id, coach_user_id, entry_type, amount_minor, currency,
                             ref_type, ref_id, note)
                        VALUES (:c, :u, 'adjustment', :amt, 'ZAR', 'correction', :r, :note)
                    """),
                    {"c": club_id, "u": coach, "amt": correction, "r": REF,
                     "note": "correcting the inverted off-platform arrears credit "
                             f"({r['n']} collection(s)): the coach held the cash, so the club is "
                             "owed its commission"},
                )
                print("    → correcting adjustment WRITTEN\n")
            else:
                print("    → would write a correcting adjustment\n")
        print(f"{affected} coach(es), net correction {_money(total_correction)}")
        if not args.commit:
            print("\nDRY RUN — nothing was written. Re-run with --commit to apply.")
            s.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main())

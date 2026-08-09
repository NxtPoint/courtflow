#!/usr/bin/env python
"""READ-ONLY: tie a coach's commission splits back to the CASH that produced them.

    python -m scripts.reconcile_coach_cash --coach Allon --month 2026-07

WHY. `diagnose_coach_statement` shows two figures that are supposed to describe the same month:
"money that actually moved" (billing.payment by landing date, filtered to the coach's own orders)
and "paid to the club" (the gross behind the commission splits, which is what the settlement charges
commission on). For Allon in 2026-07 those read R9,610 and R20,700 — a gap of R11,090, on a figure
that decides what the club pays a coach. That is too big to wave through as a "different basis".

Every `commission_split` carries the `payment_id` that produced it, so the question is answerable
exactly rather than by inference: follow each split to its payment, and see which ORDER that payment
was recorded against.

  * SAME order  → ordinary: the client paid that charge directly.
  * OTHER order → a 'Pay all' SETTLEMENT WRAPPER. The money is real and did arrive, but the payment
    row hangs on the wrapper, and the wrapper carries no coach — so a per-coach read of
    billing.payment cannot see it while the splits (one per child) can. This is the documented
    reason payments_received "sees money the order CTE excludes, e.g. both sides of a settled
    'Pay all' wrapper" — and it INFLATES the split side relative to the bank side, exactly the
    shape of the gap.
  * NO payment  → an arrears collection (`mark_arrears_collected` writes no payment row at all;
    the coach took the cash off-platform). Correct, but it is NOT bank money.

Anything left after those three is a real discrepancy and is printed as such.

READ-ONLY: SELECTs only. DATABASE_URL from the env (Render Shell) or a gitignored .env.local.
"""
import argparse
import os
import sys
from pathlib import Path

RAND = "R{:,.2f}".format


def _load_env():
    if os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("DB_URL"):
        return
    f = Path(__file__).resolve().parent.parent / ".env.local"
    if not f.exists():
        sys.exit("!! No DATABASE_URL in env and no .env.local. Run this on the Render shell.")
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# Each OWNER split, with the payment behind it and the order that payment was recorded against.
_SPLITS = """
SELECT cs.id, cs.basis, cs.gross_minor, cs.amount_minor, cs.occurred_at::date AS d,
       cs.payment_id,
       p.provider, p.amount_minor AS pay_minor, p.order_id AS paid_order_id, p.status AS pay_status,
       ol.order_id AS split_order_id,
       o.covered_order_ids IS NOT NULL AS payment_is_wrapper,
       COALESCE(NULLIF(trim(coalesce(u.first_name,'')||' '||coalesce(u.surname,'')),''),'—') AS client
  FROM billing.commission_split cs
  LEFT JOIN billing.payment p     ON p.id = cs.payment_id
  LEFT JOIN billing.order_line ol ON ol.id = cs.order_line_id
  LEFT JOIN billing."order" o     ON o.id = p.order_id
  LEFT JOIN iam."user" u          ON u.id = o.user_id
 WHERE cs.club_id = :c AND cs.coach_user_id = :u AND cs.party_type = 'owner'
   AND to_char((cs.occurred_at AT TIME ZONE :tz), 'YYYY-MM') = :ym
 ORDER BY cs.occurred_at
"""

# Every payment that TOUCHED this coach's month, whether or not the order is attributed to him.
_PAYMENTS = """
SELECT DISTINCT p.id, p.provider, p.amount_minor, p.direction, p.status,
       p.created_at::date AS d, o.covered_order_ids IS NOT NULL AS is_wrapper
  FROM billing.payment p
  JOIN billing.commission_split cs ON cs.payment_id = p.id
  LEFT JOIN billing."order" o ON o.id = p.order_id
 WHERE cs.club_id = :c AND cs.coach_user_id = :u AND cs.party_type = 'owner'
   AND to_char((cs.occurred_at AT TIME ZONE :tz), 'YYYY-MM') = :ym
"""


def main():
    ap = argparse.ArgumentParser(description="Tie a coach's commission splits back to real cash.")
    ap.add_argument("--coach", required=True, help="part of the coach's name or their email")
    ap.add_argument("--month", help="YYYY-MM (default: this month)")
    args = ap.parse_args()
    _load_env()

    import db
    from sqlalchemy import text

    with db.session_scope() as s:
        # FIND THE COACH FIRST, THEN USE *THEIR* CLUB. Taking "the first club" would be a coin flip
        # in a two-club database and would report a confident R0 for the wrong tenant — the failure
        # this codebase warns about, where a bare zero cannot be told apart from "all settled".
        who = s.execute(
            text('SELECT u.id, u.email, cp.club_id, cl.name AS club_name, '
                 "       COALESCE(cl.timezone,'Africa/Johannesburg') AS tz, "
                 "       COALESCE(NULLIF(trim(coalesce(u.first_name,'')||' '||coalesce(u.surname,'')),''),"
                 "                u.email) AS name "
                 'FROM iam.coach_profile cp '
                 ' JOIN iam."user" u ON u.id = cp.user_id '
                 " JOIN club.club cl ON cl.id = cp.club_id "
                 " WHERE u.email ILIKE :q OR "
                 "       coalesce(u.first_name,'')||' '||coalesce(u.surname,'') ILIKE :q"),
            {"q": f"%{args.coach}%"}).mappings().all()
        if len(who) != 1:
            for w in who:
                print(f"     {w['name']:<26} {w['email']:<32} {w['club_name']}")
            sys.exit(f"!! {len(who)} coaches match {args.coach!r} — be more specific.")
        who = who[0]
        club = {"id": who["club_id"], "name": who["club_name"], "tz": who["tz"]}
        ym = args.month or s.execute(text("SELECT to_char(now(),'YYYY-MM')")).scalar()
        prm = {"c": str(club["id"]), "u": str(who["id"]), "ym": ym, "tz": club["tz"]}

        rows = [dict(r) for r in s.execute(text(_SPLITS), prm).mappings()]
        print("=" * 78)
        print(f"{who['name']} ({club['name']}) — {ym}   splits vs the cash behind them")
        print("=" * 78)
        if not rows:
            print("\n  No commission splits for that month.\n")
            return 0

        direct, wrapper, no_pay, odd = [], [], [], []
        for r in rows:
            if not r["payment_id"]:
                no_pay.append(r)
            elif r["payment_is_wrapper"] or (r["paid_order_id"] and r["split_order_id"]
                                             and str(r["paid_order_id"]) != str(r["split_order_id"])):
                wrapper.append(r)
            elif r["paid_order_id"] and r["split_order_id"]:
                direct.append(r)
            else:
                odd.append(r)

        def tot(rs):
            return sum(int(x["gross_minor"] or 0) for x in rs)

        print(f"\n  {len(rows)} owner splits, gross {RAND(tot(rows) / 100)}\n")
        print(f"    paid DIRECTLY on the charge   {len(direct):>4}   {RAND(tot(direct) / 100):>13}")
        print(f"    paid via a 'Pay all' WRAPPER  {len(wrapper):>4}   {RAND(tot(wrapper) / 100):>13}"
              "   <- real money; the payment row hangs on the wrapper,")
        print(f"    {'':>4}{'':>28}   so a per-coach read of billing.payment cannot see it")
        print(f"    NO payment row (off-platform) {len(no_pay):>4}   {RAND(tot(no_pay) / 100):>13}"
              "   <- coach took the cash; never bank money")
        if odd:
            print(f"    UNEXPLAINED                   {len(odd):>4}   {RAND(tot(odd) / 100):>13}"
                  "   <<< investigate")

        # The bank side, counted the way the diagnostic's section 1 does — but including wrappers.
        pays = [dict(r) for r in s.execute(text(_PAYMENTS), prm).mappings()]
        charged = sum(int(p["amount_minor"] or 0) for p in pays
                      if p["direction"] == "charge" and p["status"] == "succeeded")
        refunded = sum(int(p["amount_minor"] or 0) for p in pays if p["direction"] == "refund")
        wrapped = sum(int(p["amount_minor"] or 0) for p in pays
                      if p["is_wrapper"] and p["direction"] == "charge")
        print(f"\n  THE CASH BEHIND THOSE SPLITS (following payment_id, wrappers included)")
        print(f"    charges                       {RAND(charged / 100):>13}")
        print(f"    less refunds                  {RAND(refunded / 100):>13}")
        print(f"    = received                    {RAND((charged - refunded) / 100):>13}")
        print(f"    of which via a wrapper        {RAND(wrapped / 100):>13}")

        gap = tot(rows) - (charged - refunded)
        print(f"\n  SPLIT GROSS minus CASH RECEIVED: {RAND(gap / 100)}")
        # DIRECTION MATTERS MORE THAN SIZE, and the two directions are not symmetrical.
        #
        # gap < 0 (cash EXCEEDS the splits) is ordinary and not a loss: one payment can settle an
        # order carrying non-coaching value too — equipment on a lesson, a court on the same tab —
        # and only the coaching line raises a split. More money arrived than this coach's commission
        # is computed on, which is the safe direction.
        #
        # gap > 0 (splits EXCEED the cash) is the one that matters: commission would be charged on
        # money the club cannot show arriving. And only `odd` rows — splits with no traceable
        # payment at all — can mean money is genuinely missing; a wrapper-paid split is real money
        # by definition, so listing those as suspects teaches people to ignore this report.
        if not odd and gap <= 0:
            print("  -> RECONCILED. Every split traces to a real payment, and the cash received is")
            print(f"     {RAND(abs(gap) / 100)} MORE than the coaching splits — a payment that also")
            print("     covered non-coaching value on the same order. Nothing is missing.")
            print("     The gap in diagnose_coach_statement is its per-coach FILTER on")
            print("     billing.payment: a wrapper's payment carries no coach, so that read cannot")
            print("     see it while the splits can.")
        elif not odd:
            print(f"  -> Every split traces to a payment, but the splits EXCEED the cash by "
                  f"{RAND(gap / 100)}.")
            print("     Commission would be charged on money the bank cannot show arriving. Check")
            print("     whether a wrapper settled children in a DIFFERENT month than these splits.")
        else:
            print("  -> UNEXPLAINED — these splits carry no traceable payment at all:")
            for r in odd[:20]:
                print(f"     {str(r['d'])}  {RAND(int(r['gross_minor'] or 0) / 100):>10}  "
                      f"{r['basis']:<18} {(r['client'] or '')[:22]:<22} pay={r['provider'] or '—'}")
        print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:                                            # noqa: BLE001
        print(f"FAILED: {type(e).__name__}: {e}")
        sys.exit(1)

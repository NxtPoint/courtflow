#!/usr/bin/env python
"""Undo a DESK payment recorded in error — the money never arrived, so the debt comes back.
DRY-RUN BY DEFAULT.

    python -m scripts.reverse_payment --order <order_id>
    python -m scripts.reverse_payment --order <order_id> --reason "ticked paid by mistake" --commit

NOT A REFUND. A refund means money went BACK to the client; this means it never arrived and somebody
marked the charge paid. Recording one as the other would put a refund the club never issued into its
books, and would leave the client's balance right for the wrong reason.

ONLY a desk payment (cash / card_at_desk / eft). A Yoco charge means real money is sitting in the
merchant account — reversing it here would flip the order back to owed while the club still holds
the cash, and nothing would put them back in step. That case is refused by name.

IT ALSO CLAWS BACK THE COMMISSION, which is the half that gets forgotten. The coach was credited the
moment the payment was recorded; leaving that in place pays him on a collection that never happened.

REFUSED when the payment granted a pack or a membership — revoking a wallet somebody may already
have drawn from is a person's decision, not a script's.
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


def main():
    ap = argparse.ArgumentParser(description="Undo a desk payment recorded in error (dry-run default).")
    ap.add_argument("--order", required=True, help="the order id")
    ap.add_argument("--reason", default="recorded in error")
    ap.add_argument("--commit", action="store_true", help="actually reverse (default: report only)")
    args = ap.parse_args()
    _load_env()

    import db
    from sqlalchemy import text
    from billing import orders as O

    with db.session_scope() as s:
        o = s.execute(
            text('SELECT o.id, o.club_id, o.status, o.amount_minor, o.currency_code, '
                 "       COALESCE(NULLIF(trim(coalesce(u.first_name,'')||' '||coalesce(u.surname,'')),''),"
                 "                u.email) AS who, "
                 "       COALESCE((SELECT string_agg(DISTINCT ol.description, ', ') "
                 "                   FROM billing.order_line ol WHERE ol.order_id = o.id),'charge') AS what "
                 'FROM billing."order" o LEFT JOIN iam."user" u ON u.id = o.user_id '
                 " WHERE o.id = CAST(:o AS uuid)"),
            {"o": args.order}).mappings().first()
        if not o:
            sys.exit(f"!! no order {args.order}")

        pays = s.execute(
            text("SELECT provider, direction, status, amount_minor, created_at::date AS d "
                 " FROM billing.payment WHERE order_id = CAST(:o AS uuid) ORDER BY created_at"),
            {"o": args.order}).mappings().all()

        print("=" * 74)
        print(f"  {o['who']}   {RAND(int(o['amount_minor'] or 0) / 100)}   {o['what'][:40]}")
        print(f"  order {o['id']}   status={o['status']}")
        print("=" * 74)
        print("\n  Payments on this order:")
        for p in pays:
            print(f"    {str(p['d'])}  {p['provider']:<14} {p['direction']:<8} {p['status']:<10} "
                  f"{RAND(int(p['amount_minor'] or 0) / 100)}")
        if not pays:
            print("    (none)")

        if not args.commit:
            print("\n  >>> DRY-RUN. With --commit this would:")
            print("      · mark the desk payment 'reversed' (kept, not deleted)")
            print("      · put the order back to 'open' — owed again, back on the statement")
            print("      · claw back the coach's commission on it")
            print("      · un-settle any 'Pay all' wrapper it paid")
            print("      Nothing has changed.\n")
            return 0

        res = O.reverse_desk_payment(s, club_id=str(o["club_id"]), order_id=str(o["id"]),
                                     reason=args.reason)
        if not res.get("ok"):
            print(f"\n  REFUSED: {res.get('error')}")
            if res.get("message"):
                print(f"  {res['message']}")
            print()
            return 1
        cb = res.get("clawback") or {}
        print(f"\n  Reversed {RAND(res['reversed_minor'] / 100)} ({res.get('provider')}).")
        print(f"  Commission clawed back: {cb.get('clawbacks', 0)} split(s).")
        print("  The order is owed again.\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:                                            # noqa: BLE001
        print(f"FAILED: {type(e).__name__}: {e}")
        sys.exit(1)

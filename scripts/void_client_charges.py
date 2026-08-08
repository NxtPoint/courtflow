#!/usr/bin/env python
"""Cancel every still-owed charge for ONE client, in one action. DRY-RUN BY DEFAULT.

    python -m scripts.void_client_charges --who terkaanambet@gmail.com
    python -m scripts.void_client_charges --who "Ross Nemeth" --period 2026-07
    python -m scripts.void_client_charges --who ... --reason "coach test account" --commit
    python -m scripts.void_client_charges --who ... --write-off --commit

WHY A SCRIPT AND NOT A BUTTON. Voiding an INVOICE cancels the document, not the debt — an invoice
renders over live orders and is never a second debt store. So cleaning up a wrongly-billed client
means voiding the CHARGES, and there can be dozens: one live account carried 73 between two months.
Clicking through 73 confirmations is not a plan. But "wipe this client's balance" is also one
mis-click away from erasing a real member's real debt, which is why it lives here behind a dry run
and an explicit --commit rather than in the admin console.

WHAT IT DOES NOT TOUCH. A PAID order — money that has moved is the refund path's business, and
void_order no-ops on one anyway. It loops void_order rather than issuing a bulk UPDATE, so every
charge keeps the consequences that path owns: any live 'Pay all' wrapper covering it is detached and
killed, the matching coach_arrears is dropped (no commission survives a cancelled sale), and the
reason is recorded on void_reason.

VOID vs WRITE-OFF. Default is `void` = "never owed, this charge was a mistake" (excluded from the
month entirely). `--write-off` = "a real debt we have forgiven", which SHOWS in the month's numbers
as written off. They are not interchangeable: one is a correction, the other is a decision.
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


def _resolve(s, needle):
    """Find exactly ONE client by email or name. Ambiguity is refused, never guessed — this
    cancels money, and 'Thando' matches two different people on this club alone."""
    from sqlalchemy import text
    rows = s.execute(
        text('SELECT u.id, u.email, '
             "       COALESCE(NULLIF(trim(coalesce(u.first_name,'')||' '||coalesce(u.surname,'')),''),"
             "                '(unnamed)') AS name "
             'FROM iam."user" u '
             " WHERE lower(u.email) = lower(:n) "
             "    OR lower(coalesce(u.first_name,'')||' '||coalesce(u.surname,'')) LIKE lower(:like)"),
        {"n": needle, "like": f"%{needle}%"},
    ).mappings().all()
    if not rows:
        sys.exit(f"!! no client matches {needle!r}")
    if len(rows) > 1:
        print(f"!! {len(rows)} clients match {needle!r} — be more specific:")
        for r in rows[:10]:
            print(f"     {r['name']:<28} {r['email']}")
        if len(rows) > 10:
            print(f"     ... +{len(rows) - 10} more (an email address always matches exactly one)")
        sys.exit(2)
    return rows[0]


def main():
    ap = argparse.ArgumentParser(description="Void every owed charge for one client (dry-run default).")
    ap.add_argument("--who", required=True, help="client email, or part of their name")
    ap.add_argument("--period", help="limit to a delivery month, YYYY-MM (default: everything owed)")
    ap.add_argument("--reason", default="bulk cancel", help="recorded on each order's void_reason")
    ap.add_argument("--write-off", action="store_true",
                    help="record as FORGIVEN debt (shows in the month) rather than never-owed")
    ap.add_argument("--commit", action="store_true", help="actually void (default: report only)")
    args = ap.parse_args()
    _load_env()

    import db
    from sqlalchemy import text
    from billing import statement as ST

    with db.session_scope() as s:
        who = _resolve(s, args.who)
        clubs = s.execute(text("SELECT id, name FROM club.club ORDER BY created_at")).mappings().all()
        grand = 0
        for club in clubs:
            rows = ST.client_open_charges(s, club_id=str(club["id"]), user_id=str(who["id"]),
                                          period_label=args.period)
            if not rows:
                continue
            total = sum(int(r["amount_minor"] or 0) for r in rows)
            grand += total
            verb = "WRITE OFF" if args.write_off else "VOID"
            scope = f" delivered in {args.period}" if args.period else ""
            print(f"\n== {club['name']} — {verb} {len(rows)} charge(s){scope} for "
                  f"{who['name']} <{who['email']}>   {RAND(total / 100)}")
            for r in rows:
                print(f"   {str(r['delivered_at'])[:10]}  {RAND(int(r['amount_minor'] or 0) / 100):>10}  "
                      f"{r['status']:<17} {(r['what'] or '')[:34]}")
            if args.commit:
                res = ST.void_client_charges(
                    s, club_id=str(club["id"]), user_id=str(who["id"]),
                    period_label=args.period, reason=args.reason, write_off=args.write_off)
                print(f"   -> {verb.lower()}ed {res['voided']}, skipped {res['skipped']}, "
                      f"{RAND(res['amount_minor'] / 100)}")

        if not grand:
            print(f"\n{who['name']} <{who['email']}> owes nothing" +
                  (f" for {args.period}" if args.period else "") + ".")
        elif not args.commit:
            print(f"\n>>> DRY-RUN — nothing changed. {RAND(grand / 100)} would be cancelled.")
            print(">>> Re-run with --commit once the list above is right.\n")
        else:
            print(f"\n>>> Done. Their statement should now show {RAND(0)} for that scope.\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:                                            # noqa: BLE001
        print(f"FAILED: {type(e).__name__}: {e}")
        sys.exit(1)

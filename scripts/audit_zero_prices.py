#!/usr/bin/env python
"""READ-ONLY: find prices that silently bill NOTHING, and the R0 orders they produced.

    python -m scripts.audit_zero_prices
    python -m scripts.audit_zero_prices --detail    # + the exact R0 orders worth investigating

WHY. `diary.pricing.price_for` resolves the exact duration and then tie-breaks on
**`amount_minor ASC`** — so if one service carries two active price rows for the SAME duration, the
CHEAPER one always wins, in silence. Production hit exactly that on 2026-07-31: a coach with
`60 min R0.00` next to `60 min R600.00`, so every 60-minute lesson with him billed nothing
(R12,680 of coaching delivered, R0.00 collected), plus a second coach on R550/R700 where the R550
quietly won. Nothing in the CODE is wrong there, which is why no gate catches it and only looking
at the screen did — the DATA is wrong, and it keeps billing wrongly until someone fixes the row.

This is the check for it. Three things, cheapest first:

  1. DUPLICATE DURATIONS — two active price rows, one product, one duration. The tie-break makes
     the cheaper row authoritative, so the expensive one is decorative.
  2. R0 ROWS ON A PAID SERVICE — an active zero price alongside a real one. Same trap, stated the
     other way round, and the version that costs the whole fee rather than the difference.
  3. THE R0 ORDERS THEMSELVES, split by settlement_mode — because a R0 order is often CORRECT.
     A pack draw ('token') and a membership-covered court are R0 by design. An `at_court` or
     `monthly_account` order at R0 is a lesson somebody delivered for free by accident.

Read-only: SELECTs only, safe on production. Takes DATABASE_URL from the environment (Render
Shell) or a gitignored .env.local, like the other diagnostics.
"""
import os
import pathlib
import sys

RAND = "R{:,.2f}".format


def _load_env():
    if os.environ.get("DATABASE_URL"):
        return
    f = pathlib.Path(__file__).resolve().parent.parent / ".env.local"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    if not os.environ.get("DATABASE_URL"):
        sys.exit("!! no DATABASE_URL (env or .env.local)")


# Two ACTIVE price rows for one product+duration. price_for takes the cheapest, so the dearer row
# never bills anything — the club thinks it charges the higher figure.
_DUPES = """
SELECT p.name, p.kind, pr.duration_minutes AS mins, count(*) AS n,
       min(pr.amount_minor) AS lo, max(pr.amount_minor) AS hi,
       COALESCE(NULLIF(trim(coalesce(u.first_name,'')||' '||coalesce(u.surname,'')),''),'—') AS coach
  FROM billing.price pr
  JOIN billing.product p ON p.id = pr.product_id
  LEFT JOIN iam."user" u ON u.id = p.coach_user_id
 WHERE pr.active AND p.club_id = :c AND pr.duration_minutes IS NOT NULL
 GROUP BY p.name, p.kind, pr.duration_minutes, coach
HAVING count(*) > 1
 ORDER BY (max(pr.amount_minor) - min(pr.amount_minor)) DESC
"""

# An ACTIVE zero price on a product that also sells at a real price.
_ZERO_ROWS = """
SELECT p.name, p.kind, pr.duration_minutes AS mins, pr.label,
       COALESCE(NULLIF(trim(coalesce(u.first_name,'')||' '||coalesce(u.surname,'')),''),'—') AS coach
  FROM billing.price pr
  JOIN billing.product p ON p.id = pr.product_id
  LEFT JOIN iam."user" u ON u.id = p.coach_user_id
 WHERE pr.active AND p.club_id = :c AND COALESCE(pr.amount_minor,0) = 0
   AND EXISTS (SELECT 1 FROM billing.price p2
                WHERE p2.product_id = pr.product_id AND p2.active AND p2.amount_minor > 0)
 ORDER BY p.name
"""

# R0 orders, split by HOW they were settled. token / membership_covered / free are correct by
# design; at_court and monthly_account at R0 are work delivered for nothing.
_ZERO_ORDERS = """
SELECT o.settlement_mode, o.status, count(*) AS n,
       COALESCE(string_agg(DISTINCT ol.description, ', '), '') AS what
  FROM billing."order" o
  LEFT JOIN billing.order_line ol ON ol.order_id = o.id
 WHERE o.club_id = :c AND COALESCE(o.amount_minor,0) = 0
   AND o.status IN ('open','awaiting_payment','paid')
 GROUP BY o.settlement_mode, o.status
 ORDER BY count(*) DESC
"""

_BY_DESIGN = ("token", "membership_covered", "free")


_ZERO_DETAIL = """
SELECT o.id, o.settlement_mode, o.created_at::date AS d,
       COALESCE(NULLIF(trim(coalesce(u.first_name,'')||' '||coalesce(u.surname,'')),''),'(none)') AS who,
       COALESCE((SELECT string_agg(DISTINCT ol.description, ', ') FROM billing.order_line ol
                  WHERE ol.order_id = o.id), 'charge') AS what
  FROM billing."order" o
  LEFT JOIN iam."user" u ON u.id = o.user_id
 WHERE o.club_id = :c AND COALESCE(o.amount_minor,0) = 0
   AND o.status IN ('open','awaiting_payment')
   AND o.settlement_mode NOT IN ('token','membership_covered','free')
 ORDER BY o.created_at
"""


def main():
    detail = "--detail" in sys.argv
    _load_env()
    from sqlalchemy import create_engine, text

    eng = create_engine(os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1),
                        pool_pre_ping=True)
    findings = 0
    with eng.connect() as c:
        for club in c.execute(text("SELECT id, name FROM club.club ORDER BY created_at")).mappings():
            cid = {"c": str(club["id"])}
            dupes = c.execute(text(_DUPES), cid).mappings().all()
            zeros = c.execute(text(_ZERO_ROWS), cid).mappings().all()
            orders = c.execute(text(_ZERO_ORDERS), cid).mappings().all()
            if not (dupes or zeros or orders):
                continue
            print("=" * 74)
            print(club["name"])
            print("=" * 74)

            if dupes:
                findings += len(dupes)
                print("\n  DUPLICATE DURATIONS — the CHEAPER row always wins, silently")
                for d in dupes:
                    print(f"    {d['name'][:28]:<28} {d['kind']:<14} {d['mins']}min  x{d['n']}   "
                          f"{RAND(d['lo']/100)} vs {RAND(d['hi']/100)}   coach: {d['coach'][:18]}")
                print("    -> every booking of that duration bills the LOWER figure. Retire the wrong row")
                print("       in the service editor (Remove sets status='retired'), do not delete it.")

            if zeros:
                findings += len(zeros)
                print("\n  R0 PRICE ROWS on a service that also sells at a real price")
                for z in zeros:
                    print(f"    {z['name'][:28]:<28} {z['kind']:<14} "
                          f"{str(z['mins'] or '—'):>4}min  {(z['label'] or '')[:18]:<18} "
                          f"coach: {z['coach'][:18]}")
                print("    -> this is the expensive version of the same trap: not a smaller fee, NO fee.")

            if orders:
                print("\n  R0 ORDERS, by how they were settled")
                for o in orders:
                    ok = (o["settlement_mode"] in _BY_DESIGN)
                    flag = "" if ok else "   << WORK DELIVERED FOR NOTHING — investigate"
                    print(f"    {o['settlement_mode']:<20} {o['status']:<16} {o['n']:>4}  "
                          f"{(o['what'] or '')[:26]:<26}{flag}")
                    if not ok:
                        findings += 1
                print("    (token / membership_covered / free are R0 BY DESIGN — a pack draw and a")
                print("     covered court cost nothing. at_court and monthly_account at R0 are not.)")
                if detail:
                    rows = c.execute(text(_ZERO_DETAIL), cid).mappings().all()
                    if rows:
                        print("\n    The ones worth investigating:")
                        for r in rows:
                            print(f"      {str(r['d'])}  {r['settlement_mode']:<17} "
                                  f"{r['who'][:22]:<22} {(r['what'] or '')[:30]}")
                            print(f"        {r['id']}")
                elif any(o["settlement_mode"] not in _BY_DESIGN for o in orders):
                    print("    -> re-run with --detail to see exactly which orders those are.")
            print()

    print(f"{findings} thing(s) worth a look.")
    if not findings:
        print("Nothing priced at zero that shouldn't be.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:                                            # noqa: BLE001
        print(f"FAILED: {type(e).__name__}: {e}")
        sys.exit(1)

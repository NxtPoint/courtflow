#!/usr/bin/env python
"""READ-ONLY: where a MONTH actually stands — what was billed, what came in, what is still owed,
and which payments are stuck half-finished. Run it on the Render Shell to close a month.

    python -m scripts.month_position              # the month just ended
    python -m scripts.month_position 2026-07      # a specific month
    python -m scripts.month_position 2026-07 --chase   # + a per-client chase list with contacts
    python -m scripts.month_position 2026-07 --dupes   # + repeat "Buy" clusters (failed sales)

Why this exists: `month_end_targets` and `invoicing.open_order_ids` select every `status='open'`
order with **no date filter at all**, so a "month-end" invoice is a photograph of everything owed
at the instant it runs, not a month's bill. That makes "what is still outstanding for July" a
question the product cannot currently answer — every view mixes the months together. This script
answers it by resolving each order to the month the SERVICE WAS DELIVERED.

**A charge belongs to the month it was DELIVERED**, not the month it was raised or paid. That is
already the rule elsewhere (`billing.me.activity_summary` buckets by the SESSION's month, on
purpose — CLAUDE.md records the fold that reconciles in NEITHER month when it didn't), so anything
new has to agree with it or the invoice and the client's own summary will disagree.

The delivery date is resolved through the SAME joins `invoicing._enriched_line_descriptions` uses
to print a line — `order_line.booking_id -> diary.booking.starts_at` for court/lesson (booking_id
on the LINE, so a squad partner's own head resolves too) and `diary.enrolment.order_id ->
class_session.starts_at` for a class. Do not write a second resolver: a parallel one that drifts is
exactly how a class ended up billed at another class's rate.

READ-ONLY. Every statement is a SELECT; there is no INSERT/UPDATE/DELETE anywhere in this file, so
it is safe against production. Reads DATABASE_URL from the environment (Render Shell) or a
gitignored .env.local, the same as the other diagnostics.
"""
import os
import pathlib
import sys
from collections import defaultdict

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


# Every order, with the month it was DELIVERED in — using billing.invoicing.DELIVERED_AT_SQL, the
# SAME expression the invoice and the month-end sweep bill on. This script exists to answer "is the
# month right?", so a private copy of that rule could agree with itself while disagreeing with the
# thing it is auditing.
from billing.invoicing import DELIVERED_AT_SQL                       # noqa: E402

_DELIVERED = """
WITH delivered AS (
  SELECT o.id,
         o.user_id,
         o.status,
         o.amount_minor,
         o.settlement_mode,
         o.created_at,
         """ + DELIVERED_AT_SQL + """ AS delivered_at,
         COALESCE(
           (SELECT string_agg(DISTINCT ol.description, ', ') FROM billing.order_line ol
             WHERE ol.order_id = o.id), 'charge') AS what,
         (SELECT COALESCE(sum(ol.original_amount_minor - ol.amount_minor), 0)
            FROM billing.order_line ol
           WHERE ol.order_id = o.id AND ol.original_amount_minor IS NOT NULL) AS discount_minor
    FROM billing."order" o
   WHERE o.club_id = :c
     AND o.settled_by_order_id IS NULL          -- a child hidden behind a live 'Pay all' wrapper
     AND o.covered_order_ids IS NULL            -- the wrapper itself is not a debt, its children are
)
"""

# One month.
_FOR_PERIOD = _DELIVERED + """
SELECT * FROM delivered
 WHERE to_char((delivered_at AT TIME ZONE :tz), 'YYYY-MM') = :period
"""

# Every month that still has money owed on it — the cutover picture.
_SPREAD = _DELIVERED + """
SELECT to_char((delivered_at AT TIME ZONE :tz),'YYYY-MM') AS m,
       count(*) AS n, sum(amount_minor) AS minor
  FROM delivered
 WHERE status IN ('open','awaiting_payment')
 GROUP BY 1 ORDER BY 1
"""


def main(argv):
    _load_env()
    from sqlalchemy import create_engine, text

    period = next((a for a in argv if not a.startswith("-")), None)
    chase = "--chase" in argv
    dupes = "--dupes" in argv
    url = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
    eng = create_engine(url, pool_pre_ping=True)

    with eng.connect() as c:
        clubs = c.execute(text("SELECT id, name, COALESCE(timezone,'Africa/Johannesburg') tz "
                               "FROM club.club ORDER BY created_at")).mappings().all()
        if not period:
            period = c.execute(text("SELECT to_char(now() - interval '1 month','YYYY-MM')")).scalar()

        for club in clubs:
            tz = club["tz"]
            rows = [dict(r) for r in c.execute(
                text(_FOR_PERIOD), {"c": str(club["id"]), "tz": tz, "period": period}).mappings()]
            if not rows:
                continue

            print("=" * 78)
            print(f"{club['name']} — {period}   (delivery month, {tz})")
            print("=" * 78)

            # ---- the fold, in the shape every other money surface uses -------------------
            by = defaultdict(int)
            for r in rows:
                by[r["status"]] += int(r["amount_minor"] or 0)
            discount = sum(int(r["discount_minor"] or 0) for r in rows)
            billed = sum(int(r["amount_minor"] or 0) for r in rows if r["status"] != "void")
            paid = by["paid"] + by["refunded"]
            outstanding = by["open"] + by["awaiting_payment"]
            print("\n  THE MONTH")
            print(f"    billed            {RAND(billed/100):>14}   ({len(rows)} orders)")
            print(f"    less discount     {RAND(discount/100):>14}")
            print(f"    less written off  {RAND(by['written_off']/100):>14}")
            print(f"    ---------------------------------")
            print(f"    collected         {RAND(paid/100):>14}")
            print(f"      of which refunded {RAND(by['refunded']/100):>12}")
            print(f"    STILL OUTSTANDING {RAND(outstanding/100):>14}"
                  f"   <-- open {RAND(by['open']/100)} + awaiting payment {RAND(by['awaiting_payment']/100)}")
            if by["void"]:
                print(f"    (void, excluded)  {RAND(by['void']/100):>14}")

            # ---- payments that never finished -------------------------------------------
            # 'awaiting_payment' = the member was sent to a Yoco checkout and never came back (or the
            # webhook was missed). These are the ones to chase or reconcile, and a >1 attempt count
            # is the known trap: refund/reconcile used to ask about the OLDEST checkout, which is the
            # abandoned one, and Yoco answers "insufficient funds" about THAT checkout's balance.
            stuck = [r for r in rows if r["status"] == "awaiting_payment"]
            if stuck:
                print(f"\n  PAYMENTS THAT NEVER COMPLETED ({len(stuck)}) — chase or reconcile")
                for r in sorted(stuck, key=lambda x: x["delivered_at"]):
                    who = c.execute(
                        text('SELECT COALESCE(first_name,\'\')||\' \'||COALESCE(surname,\'\') n, email '
                             'FROM iam."user" WHERE id = :u'), {"u": r["user_id"]}).mappings().first()
                    att = c.execute(text("SELECT count(*) FROM billing.payment_attempt WHERE order_id = :o"),
                                    {"o": r["id"]}).scalar() or 0
                    flag = "  << >1 checkout — reconcile must use the NEWEST" if att > 1 else ""
                    print(f"    {str(r['delivered_at'])[:10]}  {RAND(r['amount_minor']/100):>10}  "
                          f"{(who['n'].strip() if who else '?'):<24} {r['what'][:26]:<26} "
                          f"attempts={att}{flag}")
                print("    -> python -m scripts.diagnose_refund <order_id>   (per-order detail)")
                print("    -> POST /api/billing/yoco/reconcile/<order_id>    (recover a missed webhook)")

            # ---- repeat attempts at the SAME purchase ------------------------------------
            # `create_bundle_order` / `create_membership_order` INSERT unconditionally — there is no
            # reuse guard — so every tap of "Buy" leaves ANOTHER awaiting_payment order. A cluster
            # here is one member trying repeatedly, not N purchases.
            #
            # READ IT AS A FAILED SALE, NOT A DEBT. An online pack/membership is granted ON PAYMENT,
            # so an unpaid cluster means the member has no pack, owes nothing, and WANTED TO BUY.
            # The money to chase is in `open`; this is a conversion problem, and the bigger the
            # cluster the harder they tried.
            if dupes:
                clusters = defaultdict(list)
                for r in rows:
                    if r["status"] == "awaiting_payment":
                        clusters[(str(r["user_id"]), int(r["amount_minor"] or 0),
                                  (r["what"] or "")[:40])].append(r)
                repeated = {k: v for k, v in clusters.items() if len(v) > 1}
                if repeated:
                    lost = sum(k[1] for k in repeated)          # ONE of each, not the pile
                    print(f"\n  REPEATED ATTEMPTS AT THE SAME PURCHASE ({len(repeated)} cluster(s))")
                    print(f"  Phantom debt: {RAND((sum(k[1]*len(v) for k, v in repeated.items()) - lost)/100)}"
                          f"   |   Genuinely LOST SALES: {RAND(lost/100)}")
                    for (uid, amt, what), grp in sorted(repeated.items(), key=lambda kv: -kv[0][1]*len(kv[1])):
                        u = c.execute(
                            text('SELECT COALESCE(first_name,\'\')||\' \'||COALESCE(surname,\'\') n, email '
                                 'FROM iam."user" WHERE id = :u'), {"u": uid}).mappings().first()
                        print(f"\n    {(u['n'].strip() if u and u['n'] else '(unknown)')} — "
                              f"{len(grp)} x {RAND(amt/100)} — {what}")
                        print(f"      {u['email'] if u else ''}")
                        for r in sorted(grp, key=lambda x: x["created_at"]):
                            att = c.execute(
                                text("SELECT count(*) FROM billing.payment_attempt WHERE order_id = :o"),
                                {"o": r["id"]}).scalar() or 0
                            paid = c.execute(
                                text("SELECT count(*) FROM billing.payment WHERE order_id = :o "
                                     "AND direction='charge' AND status IN ('succeeded','refunded')"),
                                {"o": r["id"]}).scalar() or 0
                            note = ("  << TOOK MONEY — do NOT void" if paid else
                                    ("  (no checkout ever created)" if att == 0 else ""))
                            print(f"      {r['id']}  {str(r['created_at'])[:16]}  "
                                  f"attempts={att}{note}")
                    print("\n    None of the above is collectable debt. Clean up with:")
                    print("      python -m scripts.void_orphaned_orders            # dry run")
                    print("      python -m scripts.void_orphaned_orders --commit")

            # ---- who owes ----------------------------------------------------------------
            owing = defaultdict(lambda: {"minor": 0, "n": 0, "oldest": None, "what": set()})
            for r in rows:
                if r["status"] not in ("open", "awaiting_payment"):
                    continue
                k = str(r["user_id"])
                a = owing[k]
                a["minor"] += int(r["amount_minor"] or 0)
                a["n"] += 1
                a["what"].add((r["what"] or "").split(",")[0].strip()[:18])
                if a["oldest"] is None or r["delivered_at"] < a["oldest"]:
                    a["oldest"] = r["delivered_at"]
            if owing:
                print(f"\n  WHO STILL OWES FOR {period} ({len(owing)} clients)")
                for uid, a in sorted(owing.items(), key=lambda kv: -kv[1]["minor"]):
                    u = c.execute(
                        text('SELECT COALESCE(first_name,\'\')||\' \'||COALESCE(surname,\'\') n, '
                             'email, phone FROM iam."user" WHERE id = :u'), {"u": uid}).mappings().first()
                    name = (u["n"].strip() if u and u["n"] else "(unknown)")
                    line = (f"    {RAND(a['minor']/100):>11}  {name:<26} {a['n']:>2} item(s)  "
                            f"since {str(a['oldest'])[:10]}  {', '.join(sorted(a['what']))[:30]}")
                    if chase:
                        line += f"\n{'':>18}{u['email'] if u else ''}  {u['phone'] if u and u['phone'] else ''}"
                    print(line)

            # ---- is that debt even on an invoice? ----------------------------------------
            open_ids = [r["id"] for r in rows if r["status"] in ("open", "awaiting_payment")]
            if open_ids:
                inv = c.execute(
                    text("SELECT count(DISTINCT il.order_id) FROM billing.invoice_line il "
                         "JOIN billing.invoice i ON i.id = il.invoice_id "
                         "WHERE il.order_id = ANY(:ids) AND i.status = 'issued'"),
                    {"ids": open_ids}).scalar() or 0
                print(f"\n  INVOICE COVERAGE: {inv} of {len(open_ids)} outstanding orders sit on an "
                      f"issued invoice; {len(open_ids) - inv} have never been invoiced at all.")
                # NAME them. A bare count cannot distinguish "these are R0 rows we deliberately
                # never bill" from "real money nobody has been asked for", and those need opposite
                # actions. Each is tagged with WHY it was skipped, so only the last tag is work.
                gaps = [dict(r) for r in c.execute(
                    text('SELECT o.id, o.user_id, o.amount_minor, '
                         '       COALESCE((SELECT string_agg(DISTINCT ol.description, \', \') '
                         '                   FROM billing.order_line ol '
                         '                  WHERE ol.order_id = o.id), \'charge\') AS what '
                         'FROM billing."order" o WHERE o.id = ANY(:ids) '
                         '  AND NOT EXISTS (SELECT 1 FROM billing.invoice_line il '
                         '                    JOIN billing.invoice i ON i.id = il.invoice_id '
                         '                   WHERE il.order_id = o.id AND i.status = \'issued\') '
                         'ORDER BY o.amount_minor DESC'),
                    {"ids": open_ids}).mappings()]
                if gaps:
                    print("    Not on any invoice:")
                    for g in gaps:
                        if not int(g["amount_minor"] or 0):
                            why = "R0 — not a debt, never invoiced (by design)"
                        elif not g["user_id"]:
                            why = "NO CLIENT on the order — cannot be invoiced to anyone"
                        else:
                            why = "<< REAL DEBT, NOT INVOICED — investigate"
                        who = ""
                        if g["user_id"]:
                            u = c.execute(
                                text('SELECT COALESCE(first_name,\'\')||\' \'||COALESCE(surname,\'\')'
                                     ' FROM iam."user" WHERE id = :u'), {"u": g["user_id"]}).scalar()
                            who = (u or "").strip() or "(unnamed)"
                        print(f"      {RAND(g['amount_minor']/100):>10}  {who[:22]:<22} "
                              f"{(g['what'] or '')[:22]:<22} {why}")

            # ---- how mixed-up is the CURRENT open balance? -------------------------------
            # The cutover question: today's "outstanding" view spans every month at once.
            spread = c.execute(text(_SPREAD),
                                   {"c": str(club["id"]), "tz": tz, "period": period}).mappings().all()
            if spread:
                print("\n  ALL CURRENTLY-OUTSTANDING DEBT, BY DELIVERY MONTH (the cutover picture)")
                for s in spread:
                    mark = "  <-- this month" if s["m"] == period else ""
                    print(f"    {s['m']}   {RAND((s['minor'] or 0)/100):>12}   {s['n']:>4} orders{mark}")
            print()


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except SystemExit:
        raise
    except Exception as e:                                            # noqa: BLE001
        print(f"FAILED: {type(e).__name__}: {e}")
        sys.exit(1)

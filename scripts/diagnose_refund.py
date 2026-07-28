# scripts/diagnose_refund.py — READ-ONLY: why is a Yoco refund failing?
#
#   python -m scripts.diagnose_refund                 # every order with >1 checkout (the risky ones)
#   python -m scripts.diagnose_refund <order_id>      # the full picture for one order
#   python -m scripts.diagnose_refund --recent 20     # the last 20 online orders + their checkouts
#
# Reads DATABASE_URL from a gitignored .env.local (never printed) — same pattern as verify_live, but
# this one runs NO boot DDL and writes NOTHING. Pure SELECTs.
#
# WHAT IT ANSWERS. "Insufficient funds" on a refund has two completely different causes and they look
# identical from the admin console:
#
#   (a) THE CHECKOUT IS WRONG. POST /checkout mints a fresh Yoco checkout on every call, so an order
#       the member abandoned once and paid on the retry carries several ch_ ids. Refund used to aim at
#       the OLDEST — a checkout Yoco never collected against — and Yoco calls that insufficient funds.
#       Fixed 2026-07-28 (reconcile.paid_checkout_id_for_order); an order listed here with >1 checkout
#       was exposed to it.
#
#   (b) THE YOCO BALANCE IS EMPTY. Yoco funds refunds from your Yoco BALANCE, not your bank. If the
#       money already settled out and you've taken little since, Yoco declines — and no code change
#       helps. Refund by EFT and record it against the order instead.
#
# One checkout on the order → it's (b). Several → it was (a), and the fix now targets the right one.

import argparse
import os
import sys
from pathlib import Path


def _load_env_local():
    # ALREADY CONFIGURED? Then use it. Inside a Render shell on courtflow-api, DATABASE_URL is
    # already in the environment (the INTERNAL Frankfurt URL, which only resolves from inside
    # Render) — so this runs there with nothing to set up. .env.local is the LOCAL fallback.
    if os.environ.get("DATABASE_URL"):
        return
    f = Path(__file__).resolve().parent.parent / ".env.local"
    if not f.exists():
        print("!! No DATABASE_URL in the environment and no .env.local.\n"
              "\n"
              "   EASIEST — run this in a Render shell on the courtflow-api service:\n"
              "     DATABASE_URL is already set there, so just run the command as-is.\n"
              "\n"
              "   FROM YOUR LAPTOP — create .env.local in the repo root with ONE line:\n"
              "     DATABASE_URL=postgresql://<user>:<pass>@<host>.frankfurt-postgres.render.com/<db>\n"
              "   Use the EXTERNAL connection string from the Render dashboard (Postgres → Connect →\n"
              "   External). The DATABASE_URL on the SERVICE is the internal Frankfurt address and\n"
              "   will not resolve from outside Render.\n"
              "   .env.local is gitignored and this script never prints its contents.")
        sys.exit(2)
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    if not os.environ.get("DATABASE_URL"):
        print("!! .env.local has no DATABASE_URL line")
        sys.exit(2)


def _money(minor, cur="ZAR"):
    return f"{cur} {(minor or 0) / 100:,.2f}"


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Read-only Yoco refund diagnosis.")
    ap.add_argument("order_id", nargs="?", help="the order to inspect")
    ap.add_argument("--client", help="find a client's orders by name or email (no uuid needed)")
    ap.add_argument("--recent", type=int, default=0, help="list the N most recent online orders")
    args = ap.parse_args()

    _load_env_local()
    from sqlalchemy import text
    from db import get_engine
    from sqlalchemy.orm import Session

    s = Session(get_engine())
    try:
        who = s.execute(text("SELECT current_database()")).scalar()
        print(f"connected to: {who}\n")

        # Find the order by CLIENT rather than uuid — you know the name and the amount, not the id.
        if args.client:
            rows = s.execute(
                text('SELECT o.id, o.status, o.amount_minor, o.currency_code, o.settlement_mode, '
                     '       o.created_at, '
                     '       COALESCE(NULLIF(TRIM(CONCAT_WS(\' \', u.first_name, u.surname)),\'\'), '
                     '                u.email) AS who, '
                     '       (SELECT COUNT(*) FROM billing.payment_attempt pa '
                     '         WHERE pa.order_id = o.id AND pa.provider = \'yoco\' '
                     '           AND pa.intent_id IS NOT NULL) AS n_checkouts '
                     'FROM billing."order" o JOIN iam."user" u ON u.id = o.user_id '
                     'WHERE u.first_name ILIKE :q OR u.surname ILIKE :q OR u.email ILIKE :q '
                     'ORDER BY o.created_at DESC LIMIT 40'),
                {"q": f"%{args.client}%"},
            ).mappings().all()
            if not rows:
                print(f"No client matching '{args.client}'.")
                return 1
            print(f"Orders for '{args.client}':\n")
            for r in rows:
                print(f"  {r['created_at']:%Y-%m-%d}  {_money(r['amount_minor'], r['currency_code']):>13}  "
                      f"{r['status']:<10} {(r['settlement_mode'] or ''):<16} "
                      f"{r['n_checkouts']} checkout(s)  {r['who']}")
                print(f"      {r['id']}")
            print("\nRe-run with the order id above for the full picture + verdict:")
            print(f"  python -m scripts.diagnose_refund {rows[0]['id']}")
            return 0

        if args.order_id:
            o = s.execute(
                text('SELECT id, status, amount_minor, currency_code, settlement_mode, created_at '
                     'FROM billing."order" WHERE id = CAST(:o AS uuid)'),
                {"o": args.order_id},
            ).mappings().first()
            if not o:
                print("No such order.")
                return 1
            print(f"ORDER {o['id']}")
            print(f"  {o['status']}  {_money(o['amount_minor'], o['currency_code'])}  "
                  f"{o['settlement_mode']}  created {o['created_at']:%Y-%m-%d %H:%M}")

            # billing.payment_attempt holds TWO different things and conflating them is misleading:
            #   · CHECKOUT intents  — ch_…, status 'created', written when the member taps Pay online
            #   · WEBHOOK records   — p_…, status 'succeeded', written by apply_payment_event to claim
            #                         the event hash for idempotency
            # Only the first kind is refundable (Yoco's endpoint is /checkouts/{id}/refund; a p_ id
            # 404s there). The resolver filters to exactly that, so count it the same way here — an
            # earlier version of this script counted BOTH and reported "2 checkouts" for an order that
            # has one, which points the diagnosis straight at the wrong bug.
            rows_all = s.execute(
                text("SELECT intent_id, status, created_at FROM billing.payment_attempt "
                     "WHERE order_id = CAST(:o AS uuid) AND provider = 'yoco' "
                     "  AND intent_id IS NOT NULL "
                     "ORDER BY created_at DESC, id DESC"),
                {"o": args.order_id},
            ).mappings().all()
            att = [a for a in rows_all
                   if a["status"] == "created" or str(a["intent_id"]).startswith("ch_")]
            hooks = [a for a in rows_all if a not in att]
            print(f"\n  Yoco CHECKOUTS (refundable): {len(att)}")
            for i, a in enumerate(att):
                tag = "  <- the one a refund targets" if i == 0 else ""
                print(f"    {a['created_at']:%Y-%m-%d %H:%M}  {a['intent_id']}  [{a['status']}]{tag}")
            if hooks:
                print(f"  webhook records (NOT checkouts, not refundable): {len(hooks)}")
                for a in hooks:
                    print(f"    {a['created_at']:%Y-%m-%d %H:%M}  {a['intent_id']}  [{a['status']}]")

            pays = s.execute(
                text("SELECT provider, provider_payment_id, direction, status, amount_minor, "
                     "       currency_code, created_at "
                     "FROM billing.payment WHERE order_id = CAST(:o AS uuid) "
                     "ORDER BY created_at"),
                {"o": args.order_id},
            ).mappings().all()
            print(f"\n  Payments recorded: {len(pays)}")
            for p in pays:
                print(f"    {p['created_at']:%Y-%m-%d %H:%M}  {p['provider']:<12} {p['direction']:<7} "
                      f"{p['status']:<10} {_money(p['amount_minor'], p['currency_code'])}  "
                      f"{p['provider_payment_id'] or ''}")

            # WAS IT PAID VIA A 'PAY ALL' WRAPPER? Then the Yoco payment lives on the WRAPPER order and
            # this child was marked paid without a payment row of its own — so the money is genuinely
            # at Yoco while this order looks card-less. Chased both ways: the child's own back-reference
            # (mutable — _reclaim_abandoned_settlements NULLs it) and the wrapper's immutable snapshot.
            wrapper = s.execute(
                text('SELECT w.id, w.status, w.amount_minor, w.currency_code '
                     'FROM billing."order" w '
                     'WHERE w.id = (SELECT settled_by_order_id FROM billing."order" '
                     '               WHERE id = CAST(:o AS uuid)) '
                     '   OR CAST(:o AS uuid) = ANY(COALESCE(w.covered_order_ids, ARRAY[]::uuid[]))'),
                {"o": args.order_id},
            ).mappings().first()
            if wrapper:
                print(f"\n  SETTLED BY A 'PAY ALL' WRAPPER: {wrapper['id']}")
                print(f"    {wrapper['status']}  {_money(wrapper['amount_minor'], wrapper['currency_code'])}")
                wp = s.execute(
                    text("SELECT provider, direction, status, amount_minor, currency_code "
                         "FROM billing.payment WHERE order_id = :o ORDER BY created_at"),
                    {"o": str(wrapper["id"])},
                ).mappings().all()
                for x in wp:
                    print(f"      payment: {x['provider']} {x['direction']} {x['status']} "
                          f"{_money(x['amount_minor'], x['currency_code'])}")
                wa = s.execute(
                    text("SELECT COUNT(*) FROM billing.payment_attempt WHERE order_id = :o "
                         "AND provider = 'yoco' AND intent_id IS NOT NULL"),
                    {"o": str(wrapper["id"])},
                ).scalar()
                print(f"      yoco checkouts on the wrapper: {wa}")
                print("    → the card payment is on the WRAPPER, not this order. Refund the wrapper")
                print("      (it covers several debts, so a part-refund can't be split per child —")
                print("      see 'Pay all' in CLAUDE.md), or refund in the Yoco dashboard and record it.")

            # The decisive question is not "is there a checkout" but "did a CARD payment succeed".
            yoco_ok = sum(int(p["amount_minor"] or 0) for p in pays
                          if p["provider"] == "yoco" and p["direction"] == "charge"
                          and p["status"] == "succeeded")
            other = sorted({p["provider"] for p in pays
                            if p["direction"] == "charge" and p["status"] == "succeeded"
                            and p["provider"] != "yoco"})

            print("\n  VERDICT:")
            if not yoco_ok and wrapper:
                print("    The card payment for this lesson is on the 'Pay all' WRAPPER above, not on")
                print("    this order — which is why a refund aimed here finds an empty checkout and")
                print("    Yoco answers 'insufficient funds' (about THAT checkout, not your balance).")
                print("    The money IS at Yoco; it is just filed against the wrapper.")
            elif not yoco_ok:
                if other:
                    print(f"    NO successful CARD payment — this order was settled by {', '.join(other)}.")
                    print("    A payment_attempt row is written the moment someone taps 'Pay online',")
                    print("    BEFORE any money moves, so the ch_ id above is an INTENT, not a payment.")
                    print("    Yoco was being asked to return money it never took, and answers")
                    print("    'insufficient funds' about THAT CHECKOUT's balance — nothing to do with")
                    print("    your merchant balance. Refund it the way it was paid and record that.")
                else:
                    print("    NO successful payment of ANY kind is recorded against this order, so")
                    print("    there is nothing to refund. Check how the money actually arrived.")
                print("    (Refused up-front since 2026-07-28 with a message naming the real method.)")
            elif len(att) > 1:
                print("    This order has MORE THAN ONE Yoco checkout, so the refund was aiming at the")
                print("    wrong one (the oldest). That is the bug fixed on 2026-07-28 — retry the")
                print("    refund now; it will target the checkout that actually holds the money.")
            elif len(att) == 1:
                print("    A card payment DID succeed and there is exactly ONE checkout, so the refund")
                print("    was already aiming at the right place — nothing in our request is wrong.")
                print("    If Yoco still says 'insufficient funds', that is Yoco's own answer and no")
                print("    code change here will move it. Refunds draw on the AVAILABLE Yoco balance:")
                print("    today's takings may still be pending settlement, and this charge is from")
                age = ""
                if pays:
                    age = f"{(max(p['created_at'] for p in pays)):%Y-%m-%d}"
                print(f"    {age or 'an earlier date'}, so its funds have long since settled out.")
                print("    → Refund it directly in the Yoco dashboard against the payment id above,")
                print("      then record it here so the books match.")
            else:
                print("    A card payment succeeded but NO checkout id was recorded, so the refund has")
                print("    nothing to reference. Refund from the Yoco dashboard and record it here.")
            return 0

        # No order given: surface the orders that were exposed to the wrong-checkout bug.
        rows = s.execute(
            text('SELECT o.id, o.status, o.amount_minor, o.currency_code, o.created_at, '
                 '       COUNT(pa.id) AS n '
                 'FROM billing."order" o '
                 'JOIN billing.payment_attempt pa ON pa.order_id = o.id AND pa.provider = \'yoco\' '
                 '  AND pa.intent_id IS NOT NULL '
                 'GROUP BY o.id, o.status, o.amount_minor, o.currency_code, o.created_at '
                 'HAVING COUNT(pa.id) > 1 '
                 'ORDER BY o.created_at DESC LIMIT 50'),
        ).mappings().all()
        print(f"Orders with MORE THAN ONE Yoco checkout (exposed to the wrong-checkout bug): {len(rows)}")
        for r in rows:
            print(f"  {r['created_at']:%Y-%m-%d}  {r['id']}  {r['status']:<10} "
                  f"{_money(r['amount_minor'], r['currency_code']):>14}  {r['n']} checkouts")
        if not rows:
            print("  none — every online order has exactly one checkout.")

        if args.recent:
            print(f"\nMost recent {args.recent} online orders:")
            recent = s.execute(
                text('SELECT o.id, o.status, o.amount_minor, o.currency_code, o.created_at, '
                     '       (SELECT COUNT(*) FROM billing.payment_attempt pa '
                     '         WHERE pa.order_id = o.id AND pa.provider = \'yoco\' '
                     '           AND pa.intent_id IS NOT NULL) AS n '
                     'FROM billing."order" o WHERE o.settlement_mode = \'online\' '
                     'ORDER BY o.created_at DESC LIMIT :n'),
                {"n": args.recent},
            ).mappings().all()
            for r in recent:
                print(f"  {r['created_at']:%Y-%m-%d}  {r['id']}  {r['status']:<10} "
                      f"{_money(r['amount_minor'], r['currency_code']):>14}  {r['n']} checkout(s)")
        return 0
    finally:
        s.rollback()      # nothing was written; this just closes cleanly
        s.close()


if __name__ == "__main__":
    sys.exit(main())

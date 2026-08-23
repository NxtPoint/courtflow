# scripts/verify_promotion.py — read back a promotion EXACTLY as configured, before you market it.
#
# WHY: a promo is written once in an admin form and then quoted to hundreds of people in an email.
# Every field on it is a way for the campaign to fail in public — a window that opens tomorrow, a
# scope pointing at packs instead of memberships, a per-customer cap of 0, a bonus_qty left blank so
# "get a month free" grants nothing. The customer meets that mistake at checkout, with their card
# out, and it reads as the club being broken.
#
# So this prints the row, in plain language, and flags the combinations that are inert.
#
# READ-ONLY. Every statement is a SELECT.
#
#     python -m scripts.verify_promotion "Member_30"
#
# Codes are matched the way the engine matches them: case-insensitively, whitespace stripped
# (billing/promotions.py uses lower(code) = lower(:code) on a .strip()'d input).

import argparse
import io
import os
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


def _load_env():
    envf = os.path.join(REPO, ".env.local")
    if os.path.isfile(envf) and not os.getenv("DATABASE_URL"):
        for line in io.open(envf, encoding="utf-8"):
            line = line.strip()
            if line.startswith("DATABASE_URL=") and not line.startswith("#"):
                os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not os.getenv("DATABASE_URL"):
        print("DATABASE_URL is not set. On Render it already is; locally use .env.local.")
        sys.exit(2)


def main():
    ap = argparse.ArgumentParser(description="Print a promotion exactly as configured (read-only).")
    ap.add_argument("code", help='the promo code, e.g. "Member_30"')
    ap.add_argument("--club", default="NextPoint Tennis")
    args = ap.parse_args()

    _load_env()
    import db
    from sqlalchemy import text

    with db.session_scope() as s:
        club = s.execute(text("SELECT id, name FROM club.club WHERE name = :n"), {"n": args.club}).first()
        if not club:
            print(f"No club named {args.club!r}.")
            sys.exit(2)
        cid = club[0]

        p = s.execute(text("""
            SELECT code, name, kind, status, applies_to, product_id,
                   percent_bps, value_minor, bonus_qty,
                   min_spend_minor, first_time_only, max_redemptions, per_customer_cap, stackable,
                   starts_at, ends_at, created_at
            FROM billing.promotion
            WHERE club_id = :c AND lower(code) = lower(:code)
        """), {"c": cid, "code": args.code.strip()}).mappings().first()

        if not p:
            print(f"\n  NO PROMOTION with code {args.code!r} exists for {club[1]}.")
            others = s.execute(text(
                "SELECT code, kind, status FROM billing.promotion WHERE club_id = :c ORDER BY created_at DESC LIMIT 10"
            ), {"c": cid}).fetchall()
            if others:
                print("  Most recent codes on this club:")
                for c_, k_, st_ in others:
                    print(f"    {c_!r:24} {k_:14} {st_}")
            sys.exit(1)

        used = s.execute(text(
            "SELECT count(*) FROM billing.promotion_redemption r "
            "JOIN billing.promotion pr ON pr.id = r.promotion_id "
            "WHERE pr.club_id = :c AND lower(pr.code) = lower(:code)"
        ), {"c": cid, "code": args.code.strip()}).scalar() or 0

        print(f"\n{club[1]} — promotion {p['code']!r}")
        print("=" * 70)
        print(f"  name             : {p['name']}")
        print(f"  kind             : {p['kind']}")
        print(f"  status           : {p['status']}")
        print(f"  applies to       : {p['applies_to']}" + (f"  (product {p['product_id']})" if p['product_id'] else ""))
        if p["kind"] == "percent_off":
            print(f"  discount         : {(p['percent_bps'] or 0) / 100:.2f}%")
        elif p["kind"] == "amount_off":
            print(f"  discount         : R{(p['value_minor'] or 0) / 100:.2f}")
        else:
            print(f"  bonus_qty        : {p['bonus_qty']}   ({'months' if p['kind'] == 'bonus_period' else 'units'})")
        print(f"  window           : {p['starts_at']}  ->  {p['ends_at']}")
        print(f"  per-customer cap : {p['per_customer_cap']}")
        print(f"  max redemptions  : {p['max_redemptions']}")
        print(f"  first-time only  : {p['first_time_only']}")
        print(f"  stackable        : {p['stackable']}")
        print(f"  min spend        : " + (f"R{p['min_spend_minor'] / 100:.2f}" if p["min_spend_minor"] else "none"))
        print(f"  redeemed so far  : {used}")

        # --- the ways this is inert -------------------------------------
        now = datetime.now(timezone.utc)
        problems = []
        if (p["status"] or "") != "active":
            problems.append(f"status is {p['status']!r}, not 'active' — it will not redeem")
        if p["starts_at"] and p["starts_at"] > now:
            problems.append(f"has NOT STARTED yet (opens {p['starts_at']})")
        if p["ends_at"] and p["ends_at"] < now:
            problems.append(f"has already EXPIRED ({p['ends_at']})")
        if not p["ends_at"]:
            # The one a passing check used to hide. An empty window is legitimate for an evergreen
            # offer and CATASTROPHIC for a dated campaign: the email promises a deadline the billing
            # engine will not enforce, so the code stays live for anyone who forwards or screenshots
            # it, for ever. With max_redemptions also empty the exposure has no ceiling at all.
            problems.append("NO END DATE — a deadline in your copy would be fiction; this code "
                            "redeems for ever (set ends_at, and remember SAST is UTC+2: to close at "
                            "23:59 on 1 Sept, store 2026-09-01T21:59:00Z, not T00:00)")
        if not p["ends_at"] and p["max_redemptions"] is None:
            problems.append("no end date AND no max_redemptions — the exposure is unbounded")
        if not p["starts_at"]:
            problems.append("no start date — it is redeemable RIGHT NOW, before any announcement")
        if p["kind"] in ("bonus_period", "bonus_units") and not p["bonus_qty"]:
            problems.append("bonus_qty is empty — the bonus grants NOTHING")
        if p["kind"] == "percent_off" and not p["percent_bps"]:
            problems.append("percent_bps is empty — the discount is 0%")
        if p["kind"] == "amount_off" and not p["value_minor"]:
            problems.append("value_minor is empty — the discount is R0")
        if p["kind"] == "bonus_period" and (p["applies_to"] or "") != "membership":
            problems.append(f"applies_to is {p['applies_to']!r}; a bonus_period only grants on a MEMBERSHIP")
        if p["max_redemptions"] is not None and used >= p["max_redemptions"]:
            problems.append(f"max_redemptions ({p['max_redemptions']}) already reached")
        if p["per_customer_cap"] is not None and p["per_customer_cap"] < 1:
            problems.append(f"per_customer_cap is {p['per_customer_cap']} — nobody can redeem it")

        print("-" * 70)
        if problems:
            print("  NOT SAFE TO MARKET YET:")
            for x in problems:
                print(f"    !! {x}")
        else:
            print("  OK — active, in date, and configured to actually grant something.")
        print()


if __name__ == "__main__":
    main()

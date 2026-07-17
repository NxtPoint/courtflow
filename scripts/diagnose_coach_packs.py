# scripts/diagnose_coach_packs.py — READ-ONLY. Pinpoint where each session PACK lands in the coach-
# earnings roll-up: who it's attributed to (its wallet's coach, else the CLUB), the sale ORDER's amount +
# status + month, and whether that month/attribution matches what you're looking at. Answers "why isn't
# coach X's R900 pack showing on his earnings?" — it's a SALE-based model (revenue at sale, draws are R0),
# so a pack shows under its SELLING coach in its SALE month, or under the club if the wallet has no coach.
#
# Run on Render Shell (DATABASE_URL already in env), or locally with a gitignored .env.local:
#   python -m scripts.diagnose_coach_packs                 # all packs, all months
#   python -m scripts.diagnose_coach_packs Tshepo          # only packs whose coach OR buyer matches "Tshepo"
#   python -m scripts.diagnose_coach_packs Tshepo 2026-07  # + restrict the sale month
#
# It writes NOTHING (rolls back). Amounts are in major units (rands).
import os
import sys
from pathlib import Path


def _load_env_local():
    if os.environ.get("DATABASE_URL"):
        return
    f = Path(__file__).resolve().parent.parent / ".env.local"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    if not os.environ.get("DATABASE_URL"):
        print("!! no DATABASE_URL (set it in env, or add a .env.local line)")
        sys.exit(2)


def _r(m):
    return "R{:,.2f}".format((m or 0) / 100.0)


def main():
    _load_env_local()
    import db
    from sqlalchemy import text

    needle = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    month = (sys.argv[2] if len(sys.argv) > 2 else "").strip() or None

    with db.session_scope() as s:
        rows = s.execute(
            text("""
                SELECT tw.id AS wallet_id, tw.status AS wallet_status, tw.service_kind,
                       tw.coach_user_id,
                       COALESCE(cp.display_name, NULLIF(TRIM(CONCAT_WS(' ', cu.first_name, cu.surname)),''),
                                cu.email) AS coach_name,
                       bp.label AS plan_label,
                       o.id AS order_id, o.amount_minor, o.status AS order_status, o.settlement_mode,
                       to_char(o.created_at, 'YYYY-MM') AS sale_month, o.settled_by_order_id,
                       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', bu.first_name, bu.surname)),''), bu.email) AS buyer_name,
                       EXISTS (SELECT 1 FROM billing."order" ch WHERE ch.settled_by_order_id = o.id) AS settled_by_wrapper
                FROM billing.token_wallet tw
                LEFT JOIN billing.bundle_plan bp ON bp.id = tw.bundle_plan_id
                LEFT JOIN billing."order" o ON o.id = tw.order_id
                LEFT JOIN iam."user" cu ON cu.id = tw.coach_user_id
                LEFT JOIN iam.coach_profile cp ON cp.user_id = tw.coach_user_id AND cp.club_id = tw.club_id
                LEFT JOIN iam."user" bu ON bu.id = tw.user_id
                ORDER BY o.created_at DESC NULLS LAST
            """),
        ).mappings().all()

        # Statuses the earnings roll-up counts (matches admin.repositories._earnings_cte).
        COUNTED = {"open", "paid", "written_off", "refunded"}
        shown = 0
        print("\n=== SESSION PACKS — where each lands in coach earnings ===")
        print("(sale-based: a pack shows under its SELLING coach in its SALE month; draws are R0)\n")
        for r in rows:
            if needle and needle not in ((r["coach_name"] or "").lower() + " " + (r["buyer_name"] or "").lower()):
                continue
            if month and r["sale_month"] != month:
                continue
            shown += 1
            attributed = r["coach_name"] if r["coach_user_id"] else "— CLUB (no coach on wallet) —"
            counts = (r["order_status"] in COUNTED) and (r["settled_by_order_id"] is None) and (not r["settled_by_wrapper"])
            why = []
            if r["order_status"] not in COUNTED:
                why.append(f"order status '{r['order_status']}' not counted")
            if r["settled_by_wrapper"]:
                why.append("settled by a 'pay-all' wrapper -> EXCLUDED from earnings")
            if not r["coach_user_id"]:
                why.append("wallet has NO coach -> shows under CLUB direct services (Session packs)")
            print(f"[{r['service_kind']:5}] {_r(r['amount_minor']):>11}  sale {r['sale_month'] or '?'}  "
                  f"{r['order_status'] or '?':14} -> {attributed}")
            print(f"         plan: {r['plan_label'] or '?'}  |  buyer: {r['buyer_name'] or '?'}  |  "
                  f"wallet: {r['wallet_status']}  |  counts in earnings: {'YES' if counts else 'NO'}")
            if why:
                print("         note: " + " ; ".join(why))
        if not shown:
            print("(no packs matched" + (f" '{needle}'" if needle else "") + (f" in {month}" if month else "") + ")")
        print(f"\n{shown} pack(s) shown.  Nothing was written.\n")
        s.rollback()


if __name__ == "__main__":
    main()

# scripts/reconcile_coach_commission.py — READ-ONLY. Proves no coach is short-changed: every PAID
# lesson/class line (real money collected — Yoco, cash, EFT, invoice, or a 'pay-all' statement) must
# carry a coach commission_split. Commission accrues on COLLECTION through the ONE payment core
# (apply_payment_event -> record_split_for_order / settle_settlement_order), so a paid coaching line
# WITHOUT a coach split means a collection whose split silently failed — the one place a coach could be
# under-paid. This lists those (should be NONE) + a covered/uncovered rand tie-out.
#
# Run on Render Shell (DATABASE_URL in env), or locally with a gitignored .env.local:
#   python -m scripts.reconcile_coach_commission            # all-time
#   python -m scripts.reconcile_coach_commission 2026-07    # one month (by order date)
#
# Writes NOTHING (rolls back). Amounts in rands.
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

    month = (sys.argv[1] if len(sys.argv) > 1 else "").strip() or None

    # Every PAID lesson/class order line (gross>0), its coach (product, else booking), and whether a
    # coach commission_split exists for it. status='paid' covers desk, online, AND settled-by-'pay-all'
    # children (settle_settlement_order sets them 'paid' + accrues). Refunded lines carry a clawback so
    # they still have a coach split (not flagged). Month filter is on the ORDER date.
    sql = """
        SELECT o.club_id, to_char(o.created_at,'YYYY-MM') AS order_month,
               ol.id AS order_line_id, o.id AS order_id, ol.amount_minor,
               COALESCE(pr.kind, b.booking_type) AS kind,
               COALESCE(pr.coach_user_id, b.coach_user_id) AS coach_user_id,
               COALESCE(cp.display_name, NULLIF(TRIM(CONCAT_WS(' ', cu.first_name, cu.surname)),''),
                        cu.email) AS coach_name,
               COALESCE(NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.surname)),''), u.email, 'Walk-in') AS client_name,
               EXISTS (SELECT 1 FROM billing.commission_split cs
                        WHERE cs.order_line_id = ol.id AND cs.party_type = 'coach') AS has_split
        FROM billing.order_line ol
        JOIN billing."order" o ON o.id = ol.order_id
        LEFT JOIN billing.price   p  ON p.id  = ol.price_id
        LEFT JOIN billing.product pr ON pr.id = p.product_id
        LEFT JOIN diary.booking   b  ON b.id  = ol.booking_id
        LEFT JOIN iam."user" cu ON cu.id = COALESCE(pr.coach_user_id, b.coach_user_id)
        LEFT JOIN iam.coach_profile cp ON cp.user_id = COALESCE(pr.coach_user_id, b.coach_user_id) AND cp.club_id = o.club_id
        LEFT JOIN iam."user" u ON u.id = o.user_id
        WHERE o.status = 'paid'
          AND ol.amount_minor > 0
          AND COALESCE(pr.kind, b.booking_type) IN ('lesson','class')
          AND (CAST(:m AS text) IS NULL OR to_char(o.created_at,'YYYY-MM') = :m)
        ORDER BY o.created_at
    """
    with db.session_scope() as s:
        rows = s.execute(text(sql), {"m": month}).mappings().all()

        covered = uncovered = coachless = 0
        cov_gross = unc_gross = noco_gross = 0
        bad = []
        noco = []
        for r in rows:
            g = int(r["amount_minor"] or 0)
            if r["coach_user_id"] is None:
                coachless += 1; noco_gross += g; noco.append(r); continue
            if r["has_split"]:
                covered += 1; cov_gross += g
            else:
                uncovered += 1; unc_gross += g; bad.append(r)

        scope = f"month {month}" if month else "all time"
        print(f"\n=== COACH COMMISSION RECONCILIATION — {scope} ===")
        print(f"PAID lesson/class lines: {covered + uncovered + coachless}\n")
        print(f"  covered by a coach split : {covered:4}  {_r(cov_gross):>13}")
        print(f"  MISSING a coach split    : {uncovered:4}  {_r(unc_gross):>13}   <-- short-change risk")
        print(f"  paid but NO coach set    : {coachless:4}  {_r(noco_gross):>13}   <-- can't credit anyone")

        if bad:
            print("\n-- PAID coaching with NO commission split (a coach may be under-paid) --")
            for r in bad:
                print(f"   {r['order_month']}  {r['kind']:6} {_r(r['amount_minor']):>11}  "
                      f"coach: {r['coach_name'] or '?':20}  client: {r['client_name']}  order {r['order_id']}")
        if noco:
            print("\n-- PAID lesson/class with NO coach attributed (data issue) --")
            for r in noco:
                print(f"   {r['order_month']}  {r['kind']:6} {_r(r['amount_minor']):>11}  "
                      f"client: {r['client_name']}  order {r['order_id']}")

        if not bad and not coachless:
            print("\n✓ CLEAN — every collected coaching rand is credited to a coach. No short-change.")
        print("\nNothing was written.\n")
        s.rollback()


if __name__ == "__main__":
    main()

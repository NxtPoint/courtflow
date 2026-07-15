# scripts/fix_bypassed_packs.py — remediate the reconcile / pack-bypass billing bugs (SUPERVISED, dry-run first).
#
# Three idempotent, club-scoped repairs for data created before the fixes (commits a244e19 etc.):
#
#   (A) ACTIVATE pending PACK wallets on PAID orders. The reconcile (missed-webhook) path used to mark an
#       online pack paid WITHOUT activating the wallet, leaving it 'pending' (unusable). Activate them.
#
#   (B) UNWIND duplicate owed lessons. A member self-booking a lesson never drew their pack -> the lesson
#       became a full-price OWED order (a double-charge on top of the paid pack). For each OPEN lesson
#       order whose owner holds an ACTIVE matching pack (same coach/duration/service), DRAW a token for
#       the delivered lesson and VOID the owed order -> the client owes R0 and the wallet reflects the
#       lesson taken.
#
#   (C) ACTIVATE stuck MEMBERSHIPS. The SAME reconcile gap left online memberships PAID but with the
#       subscription stuck at its 'expired' pending-placeholder (current_period_end NULL) — the member
#       paid but isn't actually covered. Activate the term. (No double-charge to unwind; membership's
#       receipt email rode payment_succeeded, so unlike packs the buyer did get an email.)
#
# DRY-RUN BY DEFAULT: prints exactly what it WOULD do and rolls back. Pass --commit to write.
# Optional: --club <uuid>  --user <email>  to scope. Safe to re-run (idempotent throughout).
#
#   python -m scripts.fix_bypassed_packs                      # dry-run, all clubs
#   python -m scripts.fix_bypassed_packs --user ryan@x.com    # dry-run, one client
#   python -m scripts.fix_bypassed_packs --user ryan@x.com --commit

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text
from sqlalchemy.orm import Session

from db import get_engine
from billing import bundles as BN
from billing import membership as MB
from billing import statement as ST


def _scope_clause(args, alias="o"):
    parts, params = [], {}
    if args.club:
        parts.append(f"{alias}.club_id = :club"); params["club"] = args.club
    if args.user:
        parts.append(f"{alias}.user_id = (SELECT id FROM iam.\"user\" WHERE lower(email)=lower(:uemail))")
        params["uemail"] = args.user
    return (" AND " + " AND ".join(parts) if parts else ""), params


def repair_pending_wallets(s, args):
    clause, params = _scope_clause(args, "o")
    rows = s.execute(text(
        'SELECT w.id AS wallet_id, w.order_id, w.user_id, w.tokens_total, w.status '
        'FROM billing.token_wallet w JOIN billing."order" o ON o.id = w.order_id '
        "WHERE w.status = 'pending' AND o.status = 'paid'" + clause), params).mappings().all()
    print(f"\n(A) PENDING wallets on PAID orders: {len(rows)}")
    activated = 0
    for r in rows:
        who = s.execute(text('SELECT email FROM iam."user" WHERE id=:u'), {"u": r["user_id"]}).scalar()
        res = BN.activate_wallet_for_order(s, order_id=str(r["order_id"]), provider="yoco")
        ok = bool(res and res.get("status") == "granted")
        activated += 1 if ok else 0
        sessions = (res.get("tokens_total") if res else None) or r["tokens_total"]
        print(f"   - {who or r['user_id']}  pack order={str(r['order_id'])[:8]}  -> "
              f"{'ACTIVATED (' + str(sessions) + ' sessions)' if ok else 'no-op ('+str(res)+')'}")
    return activated


def repair_owed_lessons(s, args):
    clause, params = _scope_clause(args, "o")
    rows = s.execute(text(
        'SELECT o.id AS order_id, o.club_id, o.user_id, o.amount_minor, o.currency_code, '
        '       ol.booking_id, ol.price_id, b.coach_user_id, b.starts_at, b.ends_at, '
        '       EXTRACT(EPOCH FROM (b.ends_at - b.starts_at))/60 AS dur_min '
        'FROM billing."order" o '
        'JOIN billing.order_line ol ON ol.order_id = o.id '
        'JOIN diary.booking b ON b.id = ol.booking_id '
        "WHERE o.status = 'open' AND o.settlement_mode IN ('at_court','monthly_account') "
        "  AND b.booking_type = 'lesson' AND b.status <> 'cancelled'" + clause +
        ' ORDER BY o.created_at'), params).mappings().all()
    print(f"\n(B) OPEN lesson orders that may duplicate a held pack: {len(rows)} candidate line(s)")
    unwound = 0
    for r in rows:
        # Resolve the booking's product (per-service pack scoping) from its price row.
        product_id = None
        if r["price_id"]:
            product_id = s.execute(text("SELECT product_id FROM billing.price WHERE id=:p"),
                                   {"p": r["price_id"]}).scalar()
        dur = int(r["dur_min"] or 0) or None
        wallet = BN.match_wallet(s, club_id=str(r["club_id"]), user_id=str(r["user_id"]),
                                 service_kind="lesson", duration_minutes=dur,
                                 coach_user_id=str(r["coach_user_id"]) if r["coach_user_id"] else None,
                                 product_id=str(product_id) if product_id else None)
        who = s.execute(text('SELECT email FROM iam."user" WHERE id=:u'), {"u": r["user_id"]}).scalar()
        amt = f"R{int(r['amount_minor'] or 0)/100:.2f}"
        if not wallet:
            print(f"   - {who}  owed lesson {amt} (order={str(r['order_id'])[:8]}) -> NO matching pack, LEFT as owed")
            continue
        # This owed lesson duplicates a paid pack -> draw the token for it + void the owed order.
        print(f"   - {who}  owed lesson {amt} (order={str(r['order_id'])[:8]}) -> DRAW pack token + VOID owed order")
        BN.draw_token(s, club_id=str(r["club_id"]), wallet=wallet, booking_id=str(r["booking_id"]),
                      reason="remediation: bypassed pack", duration_minutes=dur)
        ST.void_order(s, club_id=str(r["club_id"]), order_id=str(r["order_id"]), write_off=False)
        # Reflect the pack draw on the booking's settlement (display).
        s.execute(text("UPDATE diary.booking SET settlement_mode='token', updated_at=now() WHERE id=:b"),
                  {"b": r["booking_id"]})
        unwound += 1
    return unwound


def repair_stuck_memberships(s, args):
    # A stuck reconciled membership: the order is PAID but the subscription is still at its 'expired'
    # pending-placeholder with no term (current_period_end IS NULL) — never activated. (A genuinely
    # lapsed membership that HAD a term carries a non-NULL current_period_end, so this can't touch it;
    # a cancelled one is status='cancelled'.) activate_membership_for_order is idempotent + paid-gated.
    clause, params = _scope_clause(args, "o")
    rows = s.execute(text(
        'SELECT ms.order_id, ms.user_id '
        'FROM billing.membership_subscription ms JOIN billing."order" o ON o.id = ms.order_id '
        "WHERE o.status = 'paid' AND ms.status = 'expired' AND ms.current_period_end IS NULL" + clause),
        params).mappings().all()
    print(f"\n(C) PAID memberships never activated (reconcile gap): {len(rows)}")
    activated = 0
    for r in rows:
        who = s.execute(text('SELECT email FROM iam."user" WHERE id=:u'), {"u": r["user_id"]}).scalar()
        res = MB.activate_membership_for_order(s, order_id=str(r["order_id"]), provider="yoco")
        ok = bool(res and res.get("status") in ("activated", "extended"))
        activated += 1 if ok else 0
        print(f"   - {who or r['user_id']}  membership order={str(r['order_id'])[:8]}  -> "
              f"{'ACTIVATED (term ends ' + str(res.get('current_period_end')) + ')' if ok else 'no-op ('+str(res.get('status'))+')'}")
    return activated


def main():
    ap = argparse.ArgumentParser(description="Remediate bypassed prepaid packs (dry-run by default).")
    ap.add_argument("--commit", action="store_true", help="write changes (default: dry-run + rollback)")
    ap.add_argument("--club", help="scope to one club id")
    ap.add_argument("--user", help="scope to one client email")
    args = ap.parse_args()

    s = Session(get_engine())
    try:
        activated = repair_pending_wallets(s, args)
        unwound = repair_owed_lessons(s, args)
        memberships = repair_stuck_memberships(s, args)
        print(f"\nSUMMARY: {activated} pack wallet(s) activated, {unwound} duplicate owed lesson(s) "
              f"unwound, {memberships} membership(s) activated.")
        if args.commit:
            s.commit()
            print("COMMITTED.")
        else:
            s.rollback()
            print("DRY-RUN — rolled back. Re-run with --commit to apply.")
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())

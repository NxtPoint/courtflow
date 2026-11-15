#!/usr/bin/env python
"""READ-ONLY: do the money screens agree on what each coach's clients still owe?

Three screens answer "what is still unpaid on Allon's clients?", each from its own reader:

  CARD      Money -> Club earnings -> <coach>: "Invoices outstanding" (billing.commission.coach_settlement,
            built from the work log + unpaid packs, every month up to the one on screen)
  LIST      the same drill's By-client list, one month at a time (admin.repositories.earnings_clients),
            summed here over every month
  INVOICES  Money -> Invoices & receipting, filtered to the coach (billing.invoicing.list_invoices)

and underneath all three, the TRUTH: the open orders themselves, attributed to the coach by the
lesson's coach, the class's coach or the pack wallet's coach. This prints each screen against the
truth, per coach and per client, so a gap names the client and the screen that is wrong.

It also checks, club-wide:
  * an open charge sitting on MORE THAN ONE live invoice (the invoices page would count it twice)
  * invoices "with <coach>" that also carry charges that are NOT that coach's (court hire, another
    coach, a membership) -- the invoices page shows the whole invoice under the coach's filter

Found 2026-11-15 chasing Allon's R14,900 (card) vs R16,220 (invoices page).
Nothing is written. Safe on production (Render -> courtflow-api -> Shell):

    python -m scripts.reconcile_coach_screens                     # every coach, up to this month
    python -m scripts.reconcile_coach_screens --coach Allon
    python -m scripts.reconcile_coach_screens --month 2026-10
"""
import sys
from collections import defaultdict

from sqlalchemy import text

from db import session_scope


def _money(minor):
    return f"R{(minor or 0) / 100:,.2f}"


def _arg(argv, flag, default=None):
    return argv[argv.index(flag) + 1] if flag in argv and len(argv) > argv.index(flag) + 1 else default


def _months(first, last):
    y, m = int(first[:4]), int(first[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


# Every OPEN order line, with the coach it belongs to and the month the work was done in.
_OPEN_LINES = """
    SELECT o.id AS order_id, o.user_id AS client_id, ol.amount_minor AS line_minor,
           o.amount_minor AS order_minor,
           COALESCE(bk.coach_user_id, cls.coach_user_id,
                    (SELECT w.coach_user_id FROM billing.token_wallet w
                      WHERE w.order_id = o.id LIMIT 1)) AS coach_id,
           CASE WHEN bk.id IS NOT NULL THEN 'lesson'
                WHEN en.id IS NOT NULL THEN 'class'
                WHEN EXISTS (SELECT 1 FROM billing.token_wallet w WHERE w.order_id = o.id) THEN 'pack'
                ELSE 'other' END AS kind,
           to_char(COALESCE(bk.starts_at, cls.starts_at, o.service_date::timestamptz, o.created_at)
                   AT TIME ZONE 'Africa/Johannesburg', 'YYYY-MM') AS work_month,
           COALESCE(NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.surname)), ''), u.email) AS client
    FROM billing."order" o
    JOIN billing.order_line ol ON ol.order_id = o.id
    LEFT JOIN diary.booking bk       ON bk.id = ol.booking_id
    LEFT JOIN diary.enrolment en     ON en.id = ol.enrolment_id
    LEFT JOIN diary.class_session cls ON cls.id = en.class_session_id
    LEFT JOIN iam."user" u ON u.id = o.user_id
    WHERE o.club_id = :club AND o.status = 'open'
      AND o.covered_order_ids IS NULL          -- a 'Pay all' wrapper is not a debt of its own
"""


def main(argv):
    who = _arg(argv, "--coach")
    with session_scope() as s:
        ym = _arg(argv, "--month") or s.execute(
            text("SELECT to_char(now() AT TIME ZONE 'Africa/Johannesburg','YYYY-MM')")).scalar()
        print(f"COACH SCREENS RECONCILIATION - everything unpaid up to {ym}   (read-only)\n")

        from billing.commission import coach_settlement, coach_sessions_by_day
        from billing.invoicing import list_invoices
        from admin.repositories import earnings_clients

        clubs = s.execute(text("SELECT id, name FROM club.club ORDER BY created_at")).mappings().all()
        problems = 0
        for club in clubs:
            club_id = club["id"]
            coaches = s.execute(text("""
                SELECT cp.user_id,
                       COALESCE(NULLIF(cp.display_name,''),
                                NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.surname)),''), u.email) AS name
                FROM iam.coach_profile cp JOIN iam."user" u ON u.id = cp.user_id
                WHERE cp.club_id = :c
                  AND (CAST(:who AS text) IS NULL
                       OR CONCAT_WS(' ', cp.display_name, u.first_name, u.surname, u.email)
                          ILIKE '%' || CAST(:who AS text) || '%')
                ORDER BY 2"""), {"c": club_id, "who": who}).mappings().all()
            if not coaches:
                continue
            print("=" * 78)
            print(f"CLUB: {club['name']}")

            lines = s.execute(text(_OPEN_LINES), {"club": club_id}).mappings().all()
            first_month = min([l["work_month"] for l in lines] + [ym])

            invoices = list_invoices(s, club_id=club_id, unpaid_only=True, limit=5000)
            # Which live invoices is each open order on?
            on_invoices = defaultdict(list)
            for inv in invoices:
                for oid in inv.get("open_order_ids") or []:
                    on_invoices[str(oid)].append(inv.get("number"))

            # ---- club-wide: a charge on two live invoices --------------------------------------
            dupes = {o: nums for o, nums in on_invoices.items() if len(nums) > 1}
            if dupes:
                problems += len(dupes)
                print(f"\n  !! {len(dupes)} open charge(s) sit on MORE THAN ONE live invoice "
                      f"(the invoices page counts each one twice):")
                amt = {str(l["order_id"]): int(l["order_minor"] or 0) for l in lines}
                for o, nums in sorted(dupes.items(), key=lambda kv: kv[1]):
                    print(f"     order {o}  {_money(amt.get(o))}  on {', '.join(sorted(nums))}")

            for c in coaches:
                cid = str(c["user_id"])
                name = c["name"]

                # TRUTH: open orders that are this coach's, worked up to the month
                truth = defaultdict(int)
                truth_orders = set()
                for l in lines:
                    if str(l["coach_id"] or "") == cid and l["work_month"] <= ym:
                        truth[l["client"] or "?"] += int(l["line_minor"] or 0)
                        truth_orders.add(str(l["order_id"]))
                truth_total = sum(truth.values())

                # CARD
                st = coach_settlement(s, club_id=club_id, coach_user_id=cid, month=ym)["settlement"]
                card_total = st.get("outstanding_total_minor")
                thru = coach_sessions_by_day(s, club_id=club_id, coach_user_id=cid, month=ym, through=True)
                card = defaultdict(int)
                for cl in thru.get("clients") or []:
                    for r in cl.get("rows") or []:
                        if r.get("custody") == "outstanding":
                            card[cl.get("client_name") or "?"] += int(r.get("amount_minor") or 0)
                packs = int((st.get("outstanding_breakdown") or {}).get("packs_minor") or 0)

                # LIST, summed over every month
                lst = defaultdict(int)
                for m in _months(first_month, ym):
                    for r in (earnings_clients(s, club_id=club_id, month=m, earned_by=cid) or {}).get("clients") or []:
                        if int(r.get("outstanding_minor") or 0):
                            lst[r.get("name") or "?"] += int(r["outstanding_minor"])
                list_total = sum(lst.values())

                # INVOICES page, filtered to this coach
                mine = [i for i in invoices if name in (i.get("coaches") or [])]
                inv_total = sum(int(i.get("outstanding_minor") or 0) for i in mine)
                inv_other = 0          # charges on "his" invoices that are not his
                inv_other_rows = []
                for i in mine:
                    for oid in i.get("open_order_ids") or []:
                        if str(oid) not in truth_orders:
                            amt = sum(int(l["line_minor"] or 0) for l in lines if str(l["order_id"]) == str(oid))
                            kinds = {l["kind"] for l in lines if str(l["order_id"]) == str(oid)}
                            owner = {str(l["coach_id"]) for l in lines if str(l["order_id"]) == str(oid)} - {"None"}
                            inv_other += amt
                            inv_other_rows.append((i.get("number"), i.get("client_name"), amt,
                                                   "/".join(sorted(kinds)),
                                                   "another coach" if owner else "club"))
                uninvoiced = sum(int(l["line_minor"] or 0) for l in lines
                                 if str(l["order_id"]) in truth_orders and str(l["order_id"]) not in on_invoices)

                ok = (card_total == truth_total and list_total == truth_total
                      and inv_total - inv_other + uninvoiced == truth_total)
                if not truth_total and not inv_total and not card_total and not list_total:
                    continue
                print(f"\n  {name}   {'OK' if ok else 'MISMATCH'}")
                print(f"    open charges (the truth)      {_money(truth_total):>12}")
                print(f"    coach card                    {_money(card_total):>12}"
                      + (f"   (incl. unpaid packs {_money(packs)})" if packs else ""))
                print(f"    client list, all months       {_money(list_total):>12}")
                print(f"    invoices page (his filter)    {_money(inv_total):>12}"
                      f"   = his {_money(inv_total - inv_other)} + not his {_money(inv_other)}"
                      f";  his charges not yet invoiced {_money(uninvoiced)}")
                if not ok:
                    problems += 1
                    names = sorted(set(truth) | set(card) | set(lst))
                    print(f"    {'client':<28}{'truth':>11}{'card':>11}{'list':>11}")
                    for n in names:
                        t, a, b = truth.get(n, 0), card.get(n, 0), lst.get(n, 0)
                        if not (t == a == b):
                            print(f"    {n[:27]:<28}{_money(t):>11}{_money(a):>11}{_money(b):>11}")
                    if packs:
                        print(f"    (the card's unpaid packs, {_money(packs)}, are not per client above)")
                for inv_no, cname, amt, kinds, owner in inv_other_rows:
                    print(f"    on {inv_no} ({cname}): {_money(amt)} of {kinds} that is {owner}'s, not {name}'s")

        print("\n" + "-" * 78)
        print("All screens agree." if not problems else f"{problems} thing(s) to look at, listed above.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

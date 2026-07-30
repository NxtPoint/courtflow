#!/usr/bin/env python
"""READ-ONLY: explain a coach's statement figures, line by line.

Answers "why does 'Paid to the club' say R17k when I count R6k of lessons?" by showing every row
behind the number and totalling it four ways, so a disagreement points at WHICH view is wrong instead
of leaving two numbers staring at each other:

  1. WHAT THE CLUB ACTUALLY BANKED  — straight off `billing.payment` (yoco + eft charges, less
     refunds), by the date the money LANDED. This is the bank-reconciliation figure and it is derived
     from nothing else.
  2. THE COMMISSION SPLITS          — every row the settlement math is built from: basis, provider,
     gross, the club's cut. Grouped by basis (lesson / class / arrears / clawback) and by provider,
     because a coach who also runs CLASSES has class seats in here that they are not thinking of when
     they add up "lessons".
  3. THE SETTLEMENT                 — what the statement shows, recomputed here from the same reader.
  4. THE LEDGER                     — what `coach_ledger` accumulated. (3) and (4) must agree.

Nothing is written. Safe to run against production.

    python -m scripts.diagnose_coach_statement                      # this month, every coach
    python -m scripts.diagnose_coach_statement --month 2026-07
    python -m scripts.diagnose_coach_statement --coach "Allon"      # name / email match
    python -m scripts.diagnose_coach_statement --coach Allon --detail
"""
import sys

from sqlalchemy import text

from db import session_scope


def _money(minor):
    return f"R{(minor or 0) / 100:,.2f}"


def _arg(argv, flag, default=None):
    return argv[argv.index(flag) + 1] if flag in argv and len(argv) > argv.index(flag) + 1 else default


def main(argv):
    month = _arg(argv, "--month")
    who = _arg(argv, "--coach")
    detail = "--detail" in argv

    with session_scope() as s:
        ym = month or s.execute(text("SELECT to_char(now(),'YYYY-MM')")).scalar()
        print(f"COACH STATEMENT DIAGNOSTIC - {ym}   (read-only)\n")

        coaches = s.execute(
            text("""
                SELECT DISTINCT cp.club_id, cp.user_id,
                       COALESCE(cp.display_name,
                                NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.surname)), ''),
                                u.email) AS name,
                       cl.name AS club_name
                FROM iam.coach_profile cp
                JOIN iam."user" u ON u.id = cp.user_id
                LEFT JOIN club.club cl ON cl.id = cp.club_id
                -- CAST is load-bearing: a bare `:who IS NULL` raises psycopg AmbiguousParameter
                -- (see CLAUDE.md gotchas).
                WHERE (CAST(:who AS text) IS NULL
                       OR COALESCE(cp.display_name,'') ILIKE '%' || CAST(:who AS text) || '%'
                       OR COALESCE(u.first_name,'')    ILIKE '%' || CAST(:who AS text) || '%'
                       OR COALESCE(u.surname,'')       ILIKE '%' || CAST(:who AS text) || '%'
                       OR COALESCE(u.email,'')         ILIKE '%' || CAST(:who AS text) || '%')
                ORDER BY name
            """),
            {"who": who},
        ).mappings().all()

        if not coaches:
            print(f"  No coach matched {who!r}.")
            return 0

        from billing.commission import coach_settlement, coach_sessions_by_day

        for c in coaches:
            club_id, coach_id = c["club_id"], str(c["user_id"])
            print("=" * 78)
            print(f"{c['name']}   ({c['club_name']})")
            print("=" * 78)

            # ---- 1. WHAT THE CLUB ACTUALLY BANKED, for THIS coach's orders -------------------
            bank = s.execute(
                text("""
                    SELECT pm.provider, pm.direction,
                           COUNT(*) AS n, COALESCE(SUM(pm.amount_minor), 0) AS total
                    FROM billing.payment pm
                    WHERE pm.club_id = :club
                      AND to_char(pm.created_at, 'YYYY-MM') = :ym
                      AND pm.status IN ('succeeded', 'refunded')
                      AND EXISTS (
                        SELECT 1 FROM billing.order_line ol
                        LEFT JOIN diary.booking bk ON bk.id = ol.booking_id
                        LEFT JOIN diary.enrolment e ON e.id = ol.enrolment_id
                        LEFT JOIN diary.class_session cls ON cls.id = e.class_session_id
                        WHERE ol.order_id = pm.order_id
                          AND (bk.coach_user_id = CAST(:coach AS uuid)
                               OR cls.coach_user_id = CAST(:coach AS uuid)))
                    GROUP BY 1, 2 ORDER BY 1, 2
                """),
                {"club": club_id, "coach": coach_id, "ym": ym},
            ).mappings().all()
            print("\n  1. MONEY THAT ACTUALLY MOVED (billing.payment, by landing date)")
            if not bank:
                print("     (none)")
            banked = 0
            for r in bank:
                sign = -1 if r["direction"] == "refund" else 1
                if (r["provider"] or "").lower() in ("yoco", "eft"):
                    banked += sign * int(r["total"])
                print(f"     {r['provider']:<14} {r['direction']:<8} {r['n']:>3} x   "
                      f"{_money(sign * int(r['total'])):>14}")
            print(f"     {'IN THE CLUB BANK':<24}      {_money(banked):>14}   (yoco + eft - refunds)")

            # ---- 2. THE SPLITS the settlement is built from ---------------------------------
            rows = s.execute(
                text("""
                    SELECT cs.basis, COALESCE(pm.provider, '(none)') AS provider,
                           COUNT(*) AS n,
                           COALESCE(SUM(cs.gross_minor), 0)  AS gross,
                           COALESCE(SUM(cs.amount_minor), 0) AS club_cut
                    FROM billing.commission_split cs
                    LEFT JOIN billing.payment pm ON pm.id = cs.payment_id
                    WHERE cs.club_id = :club AND cs.coach_user_id = CAST(:coach AS uuid)
                      AND cs.party_type = 'owner'
                      AND to_char(cs.occurred_at, 'YYYY-MM') = :ym
                    GROUP BY 1, 2 ORDER BY 1, 2
                """),
                {"club": club_id, "coach": coach_id, "ym": ym},
            ).mappings().all()
            print("\n  2. COMMISSION SPLITS  (what the settlement math reads)")
            if not rows:
                print("     (none)")
            print(f"     {'basis':<20} {'provider':<14} {'n':>4} {'gross':>14} {'club cut':>13}")
            for r in rows:
                print(f"     {r['basis']:<20} {r['provider']:<14} {r['n']:>4} "
                      f"{_money(r['gross']):>14} {_money(r['club_cut']):>13}")
            # The split that matters: is this money with the club, or with the coach?
            club_side = sum(int(r["gross"]) for r in rows
                            if (r["provider"] or "").lower() in ("yoco", "eft"))
            coach_side = sum(int(r["gross"]) for r in rows
                             if (r["provider"] or "").lower() not in ("yoco", "eft"))
            print(f"     -> gross via yoco/eft (CLUB holds):  {_money(club_side)}")
            print(f"     -> gross any other way (COACH holds): {_money(coach_side)}")

            if detail:
                lines = s.execute(
                    text("""
                        SELECT cs.occurred_at::date AS d, cs.basis,
                               COALESCE(pm.provider, '(none)') AS provider,
                               cs.gross_minor, cs.amount_minor,
                               COALESCE(pr.name, ol.description) AS service,
                               COALESCE(NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.surname)), ''),
                                        u.email, '?') AS client
                        FROM billing.commission_split cs
                        LEFT JOIN billing.payment pm ON pm.id = cs.payment_id
                        LEFT JOIN billing.order_line ol ON ol.id = cs.order_line_id
                        LEFT JOIN billing."order" o ON o.id = ol.order_id
                        LEFT JOIN iam."user" u ON u.id = o.user_id
                        LEFT JOIN billing.price p2 ON p2.id = ol.price_id
                        LEFT JOIN billing.product pr ON pr.id = p2.product_id
                        WHERE cs.club_id = :club AND cs.coach_user_id = CAST(:coach AS uuid)
                          AND cs.party_type = 'owner'
                          AND to_char(cs.occurred_at, 'YYYY-MM') = :ym
                        ORDER BY cs.occurred_at
                    """),
                    {"club": club_id, "coach": coach_id, "ym": ym},
                ).mappings().all()
                print("\n     EVERY SPLIT ROW")
                for r in lines:
                    print(f"       {r['d']}  {r['basis']:<19} {r['provider']:<13} "
                          f"{_money(r['gross_minor']):>12}  cut {_money(r['amount_minor']):>10}  "
                          f"{(r['service'] or '?')[:26]:<26} {(r['client'] or '?')[:22]}")

            # ---- 3 + 4. the statement, and does it tie to the ledger ------------------------
            st = coach_settlement(s, club_id=club_id, coach_user_id=coach_id, month=ym)["settlement"]
            print("\n  3. THE SETTLEMENT (what the statement shows)")
            print(f"     Paid to the club        {_money(st['club_held_minor']):>14}")
            print(f"     Collected by the coach  {_money(st['coach_held_minor']):>14}")
            print(f"     Total collected         {_money(st['total_collected_minor']):>14}")
            print(f"     Club commission         {_money(-st['commission_minor']):>14}"
                  f"   ({st['effective_pct']}%)")
            print(f"     NET                     {_money(st['net_minor']):>14}"
                  f"   ({'club owes coach' if st['net_minor'] >= 0 else 'coach owes club'})")
            print(f"\n  4. THE LEDGER   this month {_money(st['ledger_commission_minor'])}"
                  f"   ->  {'TIES' if st['reconciles'] else '*** DOES NOT TIE ***'}")

            # ---- the work log, for the "I count R6k of lessons" comparison ------------------
            log = coach_sessions_by_day(s, club_id=club_id, coach_user_id=coach_id, month=ym)
            tot = log["totals"]
            by_kind = {}
            for cl in log["clients"]:
                for r in cl["rows"]:
                    k = by_kind.setdefault(r["kind"], {"n": 0, "amt": 0})
                    k["n"] += 1
                    k["amt"] += int(r["amount_minor"] or 0)
            print(f"\n  5. SESSIONS DELIVERED in {ym} (by the day they ran)")
            for k, v in sorted(by_kind.items()):
                print(f"     {k:<10} {v['n']:>4} x   {_money(v['amt']):>14}")
            print(f"     {'TOTAL':<10} {tot['sessions']:>4} x   {_money(tot['billed_minor']):>14}")
            print(f"       of which  paid to club {_money(tot['to_club_minor'])}"
                  f" | with coach {_money(tot['with_coach_minor'])}"
                  f" | outstanding {_money(tot['outstanding_minor'])}")
            print("\n  READ IT LIKE THIS: (5) is the WORK, dated when it happened. (1)-(3) is the")
            print("  MONEY, dated when it arrived. A lesson taught last month and paid this month is")
            print("  in this month's money and last month's work - that is the rule, not a mismatch.")
            print("  If (5)'s total looks bigger than you expect, check whether CLASS seats are in it.\n")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:                                          # noqa: BLE001
        print(f"FAILED: {e}")
        sys.exit(1)

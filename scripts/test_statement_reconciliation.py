# scripts/test_statement_reconciliation.py — the GATE for the unified statement (docs/specs/UNIFIED-STATEMENT.md).
#
# Proves the money invariant on a scratch DB (rollback-only): a client owes the SUM of their unpaid
# orders, NOTHING is double-counted, and every debt settles EXACTLY once. Run via scripts.test_all.
#
#   python -m scripts.test_statement_reconciliation
#
# Rollback-only: one transaction, rolled back at the end, so the scratch club never persists. Stubs the
# CRM emit (its own tx can't see the uncommitted scratch club).

import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from db import get_engine
from diary import bookings as B
from billing import statement as S
from billing import orders as O
from billing import commission as CM

JHB = ZoneInfo("Africa/Johannesburg")
_RESULTS = []


def check(name, cond, detail=""):
    _RESULTS.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  {detail}"))


class Fx:
    pass


def _mk_user(s, email, first):
    return s.execute(text('INSERT INTO iam."user" (email, first_name) VALUES (:e,:f) RETURNING id'),
                     {"e": email, "f": first}).scalar_one()


def setup(s):
    fx = Fx()
    fx.courts = []
    fx.club_id = s.execute(
        text("INSERT INTO club.club (slug, name) VALUES (:s,'Scratch Statement') RETURNING id"),
        {"s": "scratchstmt-" + datetime.now(timezone.utc).strftime("%H%M%S%f")}).scalar_one()
    s.execute(text("INSERT INTO club.policy (club_id, booking_window_days, min_booking_minutes, "
                   "cancellation_cutoff_hours, allow_pay_at_court, allow_monthly_account, "
                   "allow_online_payment) VALUES (:c,60,60,0,true,true,true)"), {"c": fx.club_id})
    fx.member = _mk_user(s, "stmt-m@x.test", "Mem")
    s.execute(text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
                   "VALUES (:c,:u,'member','active')"), {"c": fx.club_id, "u": fx.member})
    fx.coach_uid = _mk_user(s, "stmt-c@x.test", "Coachy")
    s.execute(text("INSERT INTO iam.coach_profile (club_id, user_id, display_name, is_bookable) "
                   "VALUES (:c,:u,'Coachy',true)"), {"c": fx.club_id, "u": fx.coach_uid})
    fx.coach_res = s.execute(
        text("INSERT INTO diary.resource (club_id, kind, name, coach_user_id) "
             "VALUES (:c,'coach','Coachy',:u) RETURNING id"), {"c": fx.club_id, "u": fx.coach_uid}).scalar_one()
    for i in (1, 2):
        fx.courts.append(s.execute(
            text("INSERT INTO diary.resource (club_id, kind, name, surface, rank) "
                 "VALUES (:c,'court',:n,'hard',:r) RETURNING id"),
            {"c": fx.club_id, "n": f"Court {i}", "r": i}).scalar_one())
    fx.target = (datetime.now(JHB) + timedelta(days=3)).date()
    for rid in [fx.coach_res] + fx.courts:
        s.execute(text("INSERT INTO diary.availability_rule (club_id, resource_id, weekday, "
                       "start_time, end_time, slot_minutes) VALUES (:c,:r,:wd,'08:00','18:00',30)"),
                  {"c": fx.club_id, "r": rid, "wd": fx.target.weekday()})
    court_prod = s.execute(text("INSERT INTO billing.product (club_id, kind, name) "
                                "VALUES (:c,'court_booking','Court Hire') RETURNING id"), {"c": fx.club_id}).scalar_one()
    s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, currency_code, "
                   "unit, duration_minutes, active) VALUES (:c,:p,'any',15000,'ZAR','per_booking',60,true)"),
              {"c": fx.club_id, "p": court_prod})
    lesson_prod = s.execute(text("INSERT INTO billing.product (club_id, kind, name, coach_user_id) "
                                 "VALUES (:c,'lesson','Private Lesson',:u) RETURNING id"),
                            {"c": fx.club_id, "u": fx.coach_uid}).scalar_one()
    s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, currency_code, "
                   "unit, duration_minutes, active) VALUES (:c,:p,'any',40000,'ZAR','per_booking',60,true)"),
              {"c": fx.club_id, "p": lesson_prod})
    return fx


def at(fx, h):
    return datetime(fx.target.year, fx.target.month, fx.target.day, h, 0, tzinfo=JHB)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def book_lesson(s, fx, hour, mode):
    return B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                            booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                            starts_at=iso(at(fx, hour)), ends_at=iso(at(fx, hour + 1)), settlement_mode=mode)


def book_court(s, fx, hour, mode, court=None):
    return B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                            booking_type="court", resource_id=court if court is not None else fx.courts[0],
                            starts_at=iso(at(fx, hour)), ends_at=iso(at(fx, hour + 1)), settlement_mode=mode)


def owed(s, fx):
    return S.statement(s, club_id=fx.club_id, user_id=fx.member)


def coach_earnings(s, fx):
    return int(s.execute(text("SELECT COALESCE(SUM(amount_minor),0) FROM billing.coach_ledger "
                              "WHERE club_id=:c AND coach_user_id=:u AND entry_type='commission_earning'"),
                         {"c": fx.club_id, "u": fx.coach_uid}).scalar() or 0)


def sc_no_double_count(s, fx):
    print("\n# Statement = SUM(unpaid orders); no double-count across services/modes")
    book_lesson(s, fx, 9, "monthly_account")    # R400 owed (lesson on the monthly tab)
    book_court(s, fx, 11, "at_court")           # R150 owed (court, pay at club)
    st = owed(s, fx)
    check("statement lists exactly 2 owed lines", st["count"] == 2, str(st["count"]))
    check("total owed == R400 + R150 == R550 (NOT doubled)", st["total_owed_minor"] == 55000, str(st["total_owed_minor"]))
    check("lines carry their kind (court + lesson)", sorted(i["kind"] for i in st["items"]) == ["court", "lesson"])
    check("each line shows a pay type", all(i["pay_label"] for i in st["items"]))
    check("lines carry a grouping category", sorted(i["category"] for i in st["items"]) == ["Coaching", "Court hire"],
          str(sorted(i["category"] for i in st["items"])))
    check("the coaching line names the coach", any(i["category"] == "Coaching" and i["coach_name"] for i in st["items"]))


def sc_membership_r0(s, fx):
    print("\n# Membership-covered court is R0 → never an owed line")
    s.execute(text("INSERT INTO billing.membership_subscription (club_id, user_id, status, provider, "
                   "current_period_end) VALUES (:c,:u,'active','manual',CURRENT_DATE+30)"),
              {"c": fx.club_id, "u": fx.member})
    book_court(s, fx, 13, "membership_covered", court=fx.courts[1])
    check("covered court adds NO owed line (still 2)", owed(s, fx)["count"] == 2, str(owed(s, fx)["count"]))
    s.execute(text("UPDATE billing.membership_subscription SET status='cancelled' WHERE club_id=:c AND user_id=:u"),
              {"c": fx.club_id, "u": fx.member})


def sc_pay_all(s, fx):
    print("\n# Pay-all online → one settlement order → all children paid ONCE; coach accrues; idempotent")
    so = S.create_settlement_order(s, club_id=fx.club_id, user_id=fx.member)
    check("settlement order totals the owed R550", so and so["amount_minor"] == 55000, str(so))
    check("settling covers both items", so and so["items"] == 2, str(so))
    check("owed lines hidden while settling", owed(s, fx)["count"] == 0)
    O.record_desk_payment(s, club_id=fx.club_id, order_id=so["order_id"], amount_minor=so["amount_minor"],
                          provider="card_at_desk", provider_payment_id="SETTLE-1", user_id=fx.member)
    after = owed(s, fx)
    check("after paying, balance is ZERO", after["total_owed_minor"] == 0 and after["count"] == 0, str(after))
    earn1 = coach_earnings(s, fx)
    check("coach earned commission on the settled lesson (>0)", earn1 > 0, str(earn1))
    # Replay the SAME settlement payment → no re-charge, no double split.
    O.record_desk_payment(s, club_id=fx.club_id, order_id=so["order_id"], amount_minor=so["amount_minor"],
                          provider="card_at_desk", provider_payment_id="SETTLE-1", user_id=fx.member)
    check("balance still ZERO after replay", owed(s, fx)["total_owed_minor"] == 0)
    check("coach earnings unchanged after replay", coach_earnings(s, fx) == earn1, str(coach_earnings(s, fx)))


def sc_partial_and_reclaim(s, fx):
    print("\n# Partial settle pays one line; abandoned settlement never locks the rest")
    book_court(s, fx, 14, "at_court")            # R150
    book_court(s, fx, 15, "at_court")            # R150
    st = owed(s, fx)
    check("two fresh owed lines (R300)", st["count"] == 2 and st["total_owed_minor"] == 30000, str(st))
    one = st["items"][0]["order_id"]
    so2 = S.create_settlement_order(s, club_id=fx.club_id, user_id=fx.member, order_ids=[one])
    check("partial settlement totals just R150", so2 and so2["amount_minor"] == 15000, str(so2))
    O.record_desk_payment(s, club_id=fx.club_id, order_id=so2["order_id"], amount_minor=15000,
                          provider="card_at_desk", provider_payment_id="SETTLE-2", user_id=fx.member)
    st2 = owed(s, fx)
    check("after partial pay, ONE line (R150) remains", st2["count"] == 1 and st2["total_owed_minor"] == 15000, str(st2))
    # Abandon a settlement order, then re-create → the line is reclaimed (never locked).
    S.create_settlement_order(s, club_id=fx.club_id, user_id=fx.member)              # left UNPAID
    check("while a settlement is in flight, nothing shows owed", owed(s, fx)["count"] == 0)
    so4 = S.create_settlement_order(s, club_id=fx.club_id, user_id=fx.member)        # reclaim
    check("re-creating reclaims the abandoned line (R150 again)", so4 and so4["amount_minor"] == 15000, str(so4))


def sc_void(s, fx):
    print("\n# Void / write-off clears an owed line off the statement + balance")
    book_court(s, fx, 16, "at_court")            # R150 standalone owed
    line = s.execute(text('SELECT id FROM billing."order" WHERE club_id=:c AND user_id=:u '
                          "AND status='open' AND settled_by_order_id IS NULL ORDER BY created_at DESC LIMIT 1"),
                     {"c": fx.club_id, "u": fx.member}).scalar()
    before = owed(s, fx)["total_owed_minor"]
    v = S.void_order(s, club_id=fx.club_id, order_id=str(line), write_off=True)
    check("write-off marks the order written_off", v.get("ok") and v.get("status") == "written_off", str(v))
    after = owed(s, fx)
    check("written-off order is NOT owed", not any(i["order_id"] == str(line) for i in after["items"]))
    check("balance dropped by exactly that line (R150)", before - after["total_owed_minor"] == 15000,
          f"before={before} after={after['total_owed_minor']}")
    check("a paid order cannot be voided", S.void_order(s, club_id=fx.club_id, order_id=str(line)).get("ok") is False)


def arrears_owed(s, fx):
    return int(s.execute(text("SELECT COALESCE(SUM(gross_minor),0) FROM billing.coach_arrears "
                              "WHERE club_id=:c AND client_user_id=:u AND status='owed'"),
                         {"c": fx.club_id, "u": fx.member}).scalar() or 0)


def sc_arrears_lockstep(s, fx):
    print("\n# Arrears (coach view) ↔ orders (client view) stay in lockstep; commission once, no double")
    e0 = coach_earnings(s, fx)
    book_lesson(s, fx, 8, "at_court")                # R400 owed lesson
    CM.accrue_arrears_for_club(s, club_id=fx.club_id)   # the coach viewing their statement accrues this
    check("coach arrears shows the unpaid lesson (R400 owed)", arrears_owed(s, fx) == 40000, str(arrears_owed(s, fx)))
    check("client statement also shows R400 owed", owed(s, fx)["total_owed_minor"] == 40000, str(owed(s, fx)))
    # Client settles the lesson via the UNIFIED statement (one settlement order, paid by card).
    so = S.create_settlement_order(s, club_id=fx.club_id, user_id=fx.member)
    O.record_desk_payment(s, club_id=fx.club_id, order_id=so["order_id"], amount_minor=so["amount_minor"],
                          provider="card_at_desk", provider_payment_id="SETTLE-LS", user_id=fx.member)
    check("client statement zero after settling", owed(s, fx)["total_owed_minor"] == 0, str(owed(s, fx)))
    check("coach 'owed' arrears cleared (lockstep)", arrears_owed(s, fx) == 0, str(arrears_owed(s, fx)))
    earn1 = coach_earnings(s, fx)
    check("coach earned commission on the settled lesson (>0)", earn1 > e0, f"{e0}->{earn1}")
    # Re-running the lazy accrual must NOT resurrect the now-paid lesson, nor double the commission.
    CM.accrue_arrears_for_club(s, club_id=fx.club_id)
    check("re-accrual does NOT resurrect the paid lesson", arrears_owed(s, fx) == 0)
    check("commission accrued exactly once (no double)", coach_earnings(s, fx) == earn1, str(coach_earnings(s, fx)))

    print("\n# Reverse lockstep: coach marks an off-platform lesson collected → client's order clears")
    book_lesson(s, fx, 16, "at_court")               # R400 owed lesson (16:00)
    CM.accrue_arrears_for_club(s, club_id=fx.club_id)
    aid = s.execute(text("SELECT id FROM billing.coach_arrears WHERE club_id=:c AND client_user_id=:u "
                         "AND status='owed' ORDER BY created_at DESC LIMIT 1"),
                    {"c": fx.club_id, "u": fx.member}).scalar()
    check("a fresh owed lesson is on the client statement", owed(s, fx)["total_owed_minor"] == 40000, str(owed(s, fx)))
    CM.mark_arrears_collected(s, club_id=fx.club_id, arrears_id=str(aid))
    check("coach collected off-platform → client statement clears too", owed(s, fx)["total_owed_minor"] == 0,
          str(owed(s, fx)))


def sc_pack_offline(s, fx):
    print("\n# A pack bought 'pay at club' is usable now + shows as an owed statement line")
    from billing import bundles as BD
    plan_id = s.execute(text("INSERT INTO billing.bundle_plan (club_id, service_kind, label, "
                             "sessions_count, duration_minutes, price_minor, currency_code, active, status) "
                             "VALUES (:c,'court','5 court sessions',5,60,60000,'ZAR',true,'active') RETURNING id"),
                        {"c": fx.club_id}).scalar()
    res = BD.create_bundle_order(s, club_id=fx.club_id, user_id=fx.member,
                                 bundle_plan_id=str(plan_id), settlement_mode="at_court")
    check("offline pack: no checkout, granted immediately", (not res["needs_checkout"]) and res["activated"], str(res))
    w = s.execute(text("SELECT status, tokens_remaining FROM billing.token_wallet WHERE order_id=:o"),
                  {"o": res["order_id"]}).mappings().first()
    check("pack wallet is active + granted (5 sessions usable now)", w and w["status"] == "active" and w["tokens_remaining"] == 5, str(dict(w) if w else None))
    st = owed(s, fx)
    check("pack shows as an owed statement line (pay at club)",
          any("pack" in (i["description"] or "").lower() for i in st["items"]),
          str([i["description"] for i in st["items"]]))


def sc_cancelled_class_not_owed(s, fx):
    print("\n# A cancelled CLASS must not stay owed (void on cancel + self-heal a stuck order) + the invariant")
    from diary import classes as C
    class_res = s.execute(text("INSERT INTO diary.resource (club_id, kind, name) VALUES (:c,'class','Cardio') RETURNING id"),
                          {"c": fx.club_id}).scalar_one()
    class_prod = s.execute(text("INSERT INTO billing.product (club_id, kind, name) VALUES (:c,'class','Cardio') RETURNING id"),
                           {"c": fx.club_id}).scalar_one()
    class_price = s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, currency_code, "
                                 "unit, active) VALUES (:c,:p,'any',12000,'ZAR','per_session',true) RETURNING id"),
                            {"c": fx.club_id, "p": class_prod}).scalar_one()

    def mk_session(hour):
        return s.execute(text("INSERT INTO diary.class_session (club_id, resource_id, coach_user_id, starts_at, "
                              "ends_at, capacity, price_id, status) VALUES (:c,:r,:u,:sa,:ea,10,:pr,'scheduled') RETURNING id"),
                         {"c": fx.club_id, "r": class_res, "u": fx.coach_uid,
                          "sa": iso(at(fx, hour)), "ea": iso(at(fx, hour + 1)), "pr": class_price}).scalar_one()

    # Part A — cancel_enrolment VOIDS the order (the headline bug).
    cs1 = mk_session(14)
    before = owed(s, fx)["total_owed_minor"]
    C.enrol(s, club_id=fx.club_id, class_session_id=str(cs1), user_id=fx.member, settlement_mode="at_court")
    st = owed(s, fx)
    check("class enrol adds R120 owed under Classes",
          st["total_owed_minor"] == before + 12000 and any(i["category"] == "Classes" for i in st["items"]),
          str(st["total_owed_minor"]))
    C.cancel_enrolment(s, club_id=fx.club_id, class_session_id=str(cs1), user_id=fx.member)
    st2 = owed(s, fx)
    check("cancelled class is NOT owed (order voided on cancel)",
          st2["total_owed_minor"] == before and not any(i["category"] == "Classes" for i in st2["items"]),
          str(st2["total_owed_minor"]))

    # Part B — SELF-HEAL a stuck order (enrolment cancelled but order left 'open' — pre-fix rows).
    cs2 = mk_session(15)
    C.enrol(s, club_id=fx.club_id, class_session_id=str(cs2), user_id=fx.member, settlement_mode="at_court")
    s.execute(text("UPDATE diary.enrolment SET status='cancelled' WHERE class_session_id=:cs AND user_id=:u"),
              {"cs": str(cs2), "u": fx.member})
    check("self-heal voids a stuck cancelled-class order on statement read",
          owed(s, fx)["total_owed_minor"] == before, str(owed(s, fx)["total_owed_minor"]))

    # THE INVARIANT: the statement total == Σ open-order amounts (no drift, nothing uncounted).
    sigma = s.execute(text('SELECT COALESCE(SUM(amount_minor),0) FROM billing."order" '
                           "WHERE club_id=:c AND user_id=:u AND status='open' AND settled_by_order_id IS NULL"),
                      {"c": fx.club_id, "u": fx.member}).scalar()
    check("INVARIANT: statement total == Σ open-order amounts",
          owed(s, fx)["total_owed_minor"] == int(sigma), f"stmt={owed(s, fx)['total_owed_minor']} sigma={sigma}")


SCENARIOS = [sc_no_double_count, sc_membership_r0, sc_pay_all, sc_partial_and_reclaim, sc_void,
             sc_arrears_lockstep, sc_pack_offline, sc_cancelled_class_not_owed]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import diary.events
    diary.events.emit = lambda *a, **k: False
    try:
        import marketing_crm.tracking as _mt
        for _n in ("emit", "track"):
            if hasattr(_mt, _n):
                setattr(_mt, _n, lambda *a, **k: None)
    except Exception:
        pass

    s = Session(get_engine())
    try:
        fx = setup(s)
        print(f"Scratch statement club {fx.club_id} · test day {fx.target}")
        for scenario in SCENARIOS:
            try:
                scenario(s, fx)
            except Exception as e:
                check(f"{scenario.__name__} raised", False, repr(e))
    finally:
        s.rollback()
        s.close()

    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"\n{'='*60}\n{passed}/{total} checks passed")
    for n, ok, d in _RESULTS:
        if not ok:
            print(f"  - FAIL: {n}  {d}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

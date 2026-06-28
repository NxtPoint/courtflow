# scripts/test_billing_scenarios.py — commercial-engine scenario harness.
#
# The money-path companion to test_booking_scenarios.py. Drives the REAL billing engines
# (orders / apply_payment_event / commission / bundles / membership / refunds) against a
# self-contained scratch club inside ONE transaction that is ALWAYS rolled back — so it never
# persists. Asserts the INVARIANTS that matter for a white-label platform: settlement per mode,
# idempotent payment replay, commission accrual + scoping, token unit/minute draw-down +
# credit-back, membership coverage + access windows, and the refund-request lifecycle.
#
#   Run:  python -m scripts.test_billing_scenarios     (needs DATABASE_URL = the sandbox)
#
# NOTE: the live Yoco webhook signature-verify + the actual gateway refund call are network/HTTP
# and are covered by the offline Yoco tests in CLAUDE.md ("Verifying"); here we drive the
# provider-independent core (apply_payment_event) and the desk path, which every gateway funnels
# through.

import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo

from db import get_engine
from diary import bookings as B
from billing import orders as O
from billing import commission as CM
from billing import bundles as BN
from billing import membership as MB
from billing import refunds as RF
from billing import ledger as LG
from billing.events import apply_payment_event
from billing.gateway import NormalizedPaymentEvent
from diary import pricing as PR

JHB = ZoneInfo("Africa/Johannesburg")
_RESULTS = []


def check(name, cond, detail=""):
    _RESULTS.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if (detail and not cond) else ""))
    return bool(cond)


class Fx:
    club_id = None
    coach_uid = None
    coach_res = None
    courts = []
    member = None
    lesson_product = None
    court_product = None
    membership_price = None
    target = None


def _mk_user(s, email, first):
    return s.execute(text('INSERT INTO iam."user" (email, first_name) VALUES (:e, :f) RETURNING id'),
                     {"e": email, "f": first}).scalar_one()


def _price(s, club_id, product_id, amount, *, dur=None, unit="per_booking", term=None,
           label=None, access_days=None, start_min=None, end_min=None):
    return s.execute(
        text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, currency_code, "
             "unit, duration_minutes, term_months, label, access_days, access_start_min, "
             "access_end_min, active, status) "
             "VALUES (:c,:p,'any',:a,'ZAR',:u,:d,:t,:l,:ad,:sm,:em,true,'active') RETURNING id"),
        {"c": club_id, "p": product_id, "a": amount, "u": unit, "d": dur, "t": term, "l": label,
         "ad": access_days, "sm": start_min, "em": end_min},
    ).scalar_one()


def setup(s):
    fx = Fx()
    fx.club_id = s.execute(
        text("INSERT INTO club.club (slug, name) VALUES (:s,'Scratch Billing') RETURNING id"),
        {"s": "scratchbill-" + datetime.now(timezone.utc).strftime("%H%M%S%f")},
    ).scalar_one()
    s.execute(
        text("INSERT INTO club.policy (club_id, booking_window_days, min_booking_minutes, "
             "cancellation_cutoff_hours, allow_pay_at_court, allow_monthly_account, "
             "allow_online_payment) VALUES (:c, 60, 60, 0, true, true, true)"),
        {"c": fx.club_id})
    fx.member = _mk_user(s, "m@bill.test", "Mem")
    s.execute(text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
                   "VALUES (:c,:u,'member','active')"), {"c": fx.club_id, "u": fx.member})
    fx.coach_uid = _mk_user(s, "c@bill.test", "Coachy")
    s.execute(text("INSERT INTO iam.coach_profile (club_id, user_id, display_name, is_bookable) "
                   "VALUES (:c,:u,'Coachy',true)"), {"c": fx.club_id, "u": fx.coach_uid})
    fx.coach_res = s.execute(
        text("INSERT INTO diary.resource (club_id, kind, name, coach_user_id) "
             "VALUES (:c,'coach','Coachy',:u) RETURNING id"),
        {"c": fx.club_id, "u": fx.coach_uid}).scalar_one()
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

    # Billing catalogue.
    fx.court_product = s.execute(
        text("INSERT INTO billing.product (club_id, kind, name) VALUES (:c,'court_booking','Court Hire') "
             "RETURNING id"), {"c": fx.club_id}).scalar_one()
    for dur, amt in [(30, 9000), (60, 15000), (90, 21000), (120, 28000)]:
        _price(s, fx.club_id, fx.court_product, amt, dur=dur)
    fx.lesson_product = s.execute(
        text("INSERT INTO billing.product (club_id, kind, name) VALUES (:c,'lesson','Private Lesson') "
             "RETURNING id"), {"c": fx.club_id}).scalar_one()
    _price(s, fx.club_id, fx.lesson_product, 40000, dur=60)
    mem_product = s.execute(
        text("INSERT INTO billing.product (club_id, kind, name) VALUES (:c,'membership','Membership') "
             "RETURNING id"), {"c": fx.club_id}).scalar_one()
    fx.membership_price = _price(s, fx.club_id, mem_product, 22000, unit="per_month", term=1,
                                 label="Monthly")
    return fx


def at(fx, h, m=0):
    return datetime(fx.target.year, fx.target.month, fx.target.day, h, m, tzinfo=JHB)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _order(s, oid):
    return O.get_order(s, order_id=oid)


def _payments(s, oid):
    return s.execute(text("SELECT direction, status, amount_minor FROM billing.payment "
                          "WHERE order_id=:o"), {"o": str(oid)}).mappings().all()


# ---------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------

def sc_settlement_at_court(s, fx):
    print("\n# Settlement: at-court → order 'open' → desk payment → 'paid' (idempotent)")
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         starts_at=iso(at(fx, 9)), ends_at=iso(at(fx, 10)),
                         settlement_mode="at_court")
    oid = r["booking"]["order_id"]
    o = _order(s, oid)
    check("court order created 'open'", o and o["status"] == "open", str(o))
    check("court order priced at R150 (60 min)", o and o["amount_minor"] == 15000,
          str(o and o["amount_minor"]))
    O.record_desk_payment(s, club_id=fx.club_id, order_id=oid, amount_minor=15000,
                          provider="cash", provider_payment_id="RCPT-1", user_id=fx.member)
    check("order 'paid' after desk payment", _order(s, oid)["status"] == "paid")
    check("exactly one payment recorded", len(_payments(s, oid)) == 1)
    # Replay the SAME desk receipt → idempotent (no second payment).
    O.record_desk_payment(s, club_id=fx.club_id, order_id=oid, amount_minor=15000,
                          provider="cash", provider_payment_id="RCPT-1", user_id=fx.member)
    check("replayed desk payment is idempotent (still one)", len(_payments(s, oid)) == 1)


def sc_settlement_online(s, fx):
    print("\n# Settlement: online → booking 'held' → charge_succeeded → 'paid' + 'confirmed' (idempotent)")
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         starts_at=iso(at(fx, 11)), ends_at=iso(at(fx, 12)),
                         settlement_mode="online")
    bid = r["booking"]["id"]; oid = r["booking"]["order_id"]
    check("online booking starts 'held'", r["booking"]["status"] == "held", str(r["booking"]))
    check("online order 'awaiting_payment'", _order(s, oid)["status"] == "awaiting_payment")
    ev = NormalizedPaymentEvent(provider="yoco", kind="charge_succeeded", order_ref=oid,
                                provider_payment_id="p_online_1", amount_minor=15000,
                                currency="ZAR", status="succeeded", direction="charge",
                                club_id=str(fx.club_id), user_id=str(fx.member), raw={"t": 1})
    res = apply_payment_event(ev, session=s)
    check("charge applied", res.get("ok") and not res.get("ignored"), str(res))
    check("order 'paid' after charge", _order(s, oid)["status"] == "paid")
    bk = B.get_booking(s, club_id=fx.club_id, booking_id=bid)
    check("held booking 'confirmed' after charge", bk["status"] == "confirmed", bk["status"])
    # Replay the identical event → ignored (event_hash dedupe); still one payment.
    res2 = apply_payment_event(ev, session=s)
    check("replayed charge ignored (idempotent)", res2.get("ignored") is True, str(res2))
    check("still exactly one charge payment", len(_payments(s, oid)) == 1)


def sc_settlement_monthly(s, fx):
    print("\n# Settlement: monthly_account → the order total accrues on the member's ledger")
    before = LG.current_balance_minor(s, club_id=fx.club_id, user_id=fx.member)
    B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                     booking_type="court", resource_id=fx.courts[0],
                     starts_at=iso(at(fx, 13)), ends_at=iso(at(fx, 14)),
                     settlement_mode="monthly_account")
    after = LG.current_balance_minor(s, club_id=fx.club_id, user_id=fx.member)
    check("ledger charged R150 on the tab", after - before == 15000, f"delta={after-before}")


def sc_commission(s, fx):
    print("\n# Commission: 30% club rule → split owner/coach on collection; scoping; idempotent replay")
    s.execute(text("INSERT INTO billing.commission_rule (club_id, scope, commission_pct, "
                   "effective_from, active) VALUES (:c,'club',30,:ef,true)"),
              {"c": fx.club_id, "ef": datetime.now(timezone.utc) - timedelta(days=1)})
    check("club rule resolves to 30%",
          CM.resolve_commission_pct(s, club_id=fx.club_id) == 30)
    # A more specific coach+product rule (40%) must win over the club default.
    s.execute(text("INSERT INTO billing.commission_rule (club_id, scope, product_id, coach_user_id, "
                   "commission_pct, effective_from, active) VALUES (:c,'coach_product',:p,:u,40,:ef,true)"),
              {"c": fx.club_id, "p": fx.lesson_product, "u": fx.coach_uid,
               "ef": datetime.now(timezone.utc) - timedelta(days=1)})
    check("coach+product rule (40%) beats club (30%)",
          CM.resolve_commission_pct(s, club_id=fx.club_id, product_id=fx.lesson_product,
                                    coach_user_id=fx.coach_uid) == 40)
    # Book a lesson online (R400), pay → commission accrues. The coach+product 40% rule applies.
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="lesson", resource_id=fx.coach_res,
                         coach_user_id=fx.coach_uid, starts_at=iso(at(fx, 9)), ends_at=iso(at(fx, 10)),
                         settlement_mode="online")
    oid = r["booking"]["order_id"]
    check("lesson order priced at R400", _order(s, oid)["amount_minor"] == 40000,
          str(_order(s, oid)["amount_minor"]))
    ev = NormalizedPaymentEvent(provider="yoco", kind="charge_succeeded", order_ref=oid,
                                provider_payment_id="p_lesson_1", amount_minor=40000,
                                currency="ZAR", status="succeeded", direction="charge",
                                club_id=str(fx.club_id), user_id=str(fx.member), raw={"t": 2})
    apply_payment_event(ev, session=s)
    bal = CM.coach_balance(s, club_id=fx.club_id, coach_user_id=fx.coach_uid)
    check("coach earns R240 (60% of R400 @ 40% club cut)", bal == 24000, f"coach_balance={bal}")
    # Replay → no second split.
    apply_payment_event(ev, session=s)
    bal2 = CM.coach_balance(s, club_id=fx.club_id, coach_user_id=fx.coach_uid)
    check("commission split is idempotent on replay", bal2 == 24000, f"coach_balance={bal2}")


def sc_tokens(s, fx):
    print("\n# Tokens/bundles: buy pack → activate → unit/minute draw-down → credit-back; NO_TOKEN")
    plan = BN.create_plan(s, club_id=fx.club_id, service_kind="lesson", sessions_count=10,
                          price_minor=300000, duration_minutes=60, coach_user_id=fx.coach_uid,
                          label="10 lessons")
    order = BN.create_bundle_order(s, club_id=fx.club_id, user_id=fx.member,
                                   bundle_plan_id=plan["id"])
    oid = order["order_id"]
    ev = NormalizedPaymentEvent(provider="yoco", kind="charge_succeeded", order_ref=oid,
                                provider_payment_id="p_pack_1", amount_minor=300000,
                                currency="ZAR", status="succeeded", direction="charge",
                                club_id=str(fx.club_id), user_id=str(fx.member), raw={"t": 3})
    apply_payment_event(ev, session=s)
    BN.activate_wallet_for_order(s, order_id=oid)
    w = s.execute(text("SELECT status, minutes_total, minutes_remaining, tokens_remaining "
                       "FROM billing.token_wallet WHERE order_id=:o"), {"o": str(oid)}).mappings().first()
    check("wallet active with 600 minutes (10×60)",
          w and w["status"] == "active" and w["minutes_remaining"] == 600, str(dict(w) if w else None))
    # Book a 90-min lesson on the pack → draws 90 minutes (= 1.5 sessions off a 60-unit).
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                         starts_at=iso(at(fx, 9)), ends_at=iso(at(fx, 10, 30)),
                         settlement_mode="token")
    check("token lesson booked", r.get("ok"), str(r))
    w2 = s.execute(text("SELECT minutes_remaining, tokens_remaining FROM billing.token_wallet "
                        "WHERE order_id=:o"), {"o": str(oid)}).mappings().first()
    check("90 min drawn → 510 remaining", w2["minutes_remaining"] == 510, str(dict(w2)))
    check("tokens shown as CEIL(510/60)=9", w2["tokens_remaining"] == 9, str(dict(w2)))
    # Cancel → credit back the exact 90 minutes.
    B.cancel_booking(s, club_id=fx.club_id, booking_id=r["booking"]["id"],
                     actor_user_id=fx.member, role="member")
    w3 = s.execute(text("SELECT minutes_remaining FROM billing.token_wallet WHERE order_id=:o"),
                   {"o": str(oid)}).mappings().first()
    check("cancel credits the 90 min back → 600", w3["minutes_remaining"] == 600, str(dict(w3)))
    # No COURT wallet → a court token booking cleanly rejects with NO_TOKEN.
    rc = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                          booking_type="court", resource_id=fx.courts[0],
                          starts_at=iso(at(fx, 14)), ends_at=iso(at(fx, 15)),
                          settlement_mode="token")
    check("court token with no wallet → NO_TOKEN", rc.get("error") == "NO_TOKEN", str(rc))


def sc_membership(s, fx):
    print("\n# Membership: active sub covers courts (R0); access window enforced; trial idempotent")
    # An active (manual) membership with no access window → covers any time.
    s.execute(text("INSERT INTO billing.membership_subscription (club_id, user_id, status, provider, "
                   "current_period_end) VALUES (:c,:u,'active','manual', CURRENT_DATE + 30)"),
              {"c": fx.club_id, "u": fx.member})
    check("has_active_membership true", PR.has_active_membership(s, club_id=fx.club_id, user_id=fx.member))
    check("membership covers a court (no window)",
          PR.membership_covers(s, club_id=fx.club_id, user_id=fx.member, starts_at=at(fx, 9)))
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         starts_at=iso(at(fx, 9)), ends_at=iso(at(fx, 10)),
                         settlement_mode="membership_covered")
    o = _order(s, r["booking"]["order_id"])
    check("covered court order is R0 + paid", o["amount_minor"] == 0 and o["status"] == "paid", str(o))

    # A second member on a WINDOWED tier (weekdays 06:00–17:00) — covered inside, PAYG outside.
    m2 = _mk_user(s, "m2@bill.test", "Mem2")
    s.execute(text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
                   "VALUES (:c,:u,'member','active')"), {"c": fx.club_id, "u": m2})
    win_price = _price(s, fx.club_id, s.execute(
        text("SELECT id FROM billing.product WHERE club_id=:c AND kind='membership' LIMIT 1"),
        {"c": fx.club_id}).scalar(), 18000, unit="per_month", term=1, label="Student",
        access_days="1,2,3,4,5", start_min=360, end_min=1020)
    s.execute(text("INSERT INTO billing.membership_subscription (club_id, user_id, price_id, status, "
                   "provider, current_period_end) VALUES (:c,:u,:p,'active','manual', CURRENT_DATE + 30)"),
              {"c": fx.club_id, "u": m2, "p": win_price})
    # target day is a weekday (we pick +3d; ensure a weekday slot). 10:00 inside, 18:00 outside.
    inside = PR.membership_covers(s, club_id=fx.club_id, user_id=m2, starts_at=at(fx, 10))
    outside = PR.membership_covers(s, club_id=fx.club_id, user_id=m2, starts_at=at(fx, 18))
    is_weekday = fx.target.weekday() < 5
    check("windowed tier covers 10:00 inside hours (weekday)", inside or not is_weekday,
          f"inside={inside} weekday={is_weekday}")
    check("windowed tier does NOT cover 18:00 (after 17:00)", not outside, f"outside={outside}")

    # grant_signup_trial is one-shot / idempotent.
    m3 = _mk_user(s, "m3@bill.test", "Mem3")
    g1 = MB.grant_signup_trial(s, club_id=fx.club_id, user_id=m3, days=7)
    g2 = MB.grant_signup_trial(s, club_id=fx.club_id, user_id=m3, days=7)
    n = s.execute(text("SELECT count(*) FROM billing.membership_subscription "
                       "WHERE club_id=:c AND user_id=:u AND provider='trial'"),
                  {"c": fx.club_id, "u": m3}).scalar()
    check("signup trial granted once, not double", n == 1, f"trial rows={n} g1={g1} g2={g2}")


def sc_refund_request(s, fx):
    print("\n# Refund request lifecycle: create → list → decline (terminal) → withdraw")
    # A paid order to refund.
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         starts_at=iso(at(fx, 9)), ends_at=iso(at(fx, 10)), settlement_mode="at_court")
    oid = r["booking"]["order_id"]
    O.record_desk_payment(s, club_id=fx.club_id, order_id=oid, amount_minor=15000,
                          provider="cash", provider_payment_id="RCPT-RF", user_id=fx.member)
    req, err = RF.create_refund_request(s, club_id=fx.club_id, user_id=fx.member, order_id=oid,
                                        reason="rain")
    check("refund request created", err is None and req, str(err))
    # Duplicate open request is refused.
    _, err2 = RF.create_refund_request(s, club_id=fx.club_id, user_id=fx.member, order_id=oid)
    check("duplicate open request refused", err2 == "DUPLICATE", str(err2))
    lst = RF.list_refund_requests_admin(s, club_id=fx.club_id, status="pending")
    check("admin sees the pending request", len(lst) == 1, f"count={len(lst)}")
    dec, derr = RF.decline_refund_request(s, club_id=fx.club_id, request_id=req["id"],
                                          decided_by=fx.member, note="no")
    check("decline → 'declined'", derr is None and dec["status"] == "declined", str(derr))
    _, derr2 = RF.decline_refund_request(s, club_id=fx.club_id, request_id=req["id"],
                                         decided_by=fx.member)
    check("re-deciding a closed request → NOT_PENDING", derr2 == "NOT_PENDING", str(derr2))


def sc_statement_pay(s, fx):
    print("\n# Month-end: client pays their owed coaching statement online → arrears settle")
    # Seed an OWED arrears row for the member (an off-platform lesson on the coach's tab).
    s.execute(text("INSERT INTO billing.coach_arrears (club_id, coach_user_id, client_user_id, "
                   "gross_minor, currency, status) VALUES (:c,:coach,:u,40000,'ZAR','owed')"),
              {"c": fx.club_id, "coach": fx.coach_uid, "u": fx.member})
    pay = CM.create_statement_payment(s, club_id=fx.club_id, client_user_id=fx.member)
    check("statement-payment order created for R400", pay and pay["amount_minor"] == 40000, str(pay))
    oid = pay["order_id"]
    check("order is awaiting_payment online", _order(s, oid)["status"] == "awaiting_payment")
    ev = NormalizedPaymentEvent(provider="yoco", kind="charge_succeeded", order_ref=oid,
                                provider_payment_id="p_stmt_1", amount_minor=40000, currency="ZAR",
                                status="succeeded", direction="charge", club_id=str(fx.club_id),
                                user_id=str(fx.member), raw={"t": 9})
    apply_payment_event(ev, session=s)
    owed = s.execute(text("SELECT count(*) FROM billing.coach_arrears WHERE client_user_id=:u AND status='owed'"),
                     {"u": fx.member}).scalar()
    check("arrears marked collected after payment", owed == 0, f"still owed={owed}")
    # Commission accrued for the coach on the settled arrears.
    bal = CM.coach_balance(s, club_id=fx.club_id, coach_user_id=fx.coach_uid)
    check("coach earned commission on the settled statement", bal > 0, f"coach_balance={bal}")
    # Replay → idempotent (no second settle / double commission).
    apply_payment_event(ev, session=s)
    check("replay is idempotent (coach balance unchanged)",
          CM.coach_balance(s, club_id=fx.club_id, coach_user_id=fx.coach_uid) == bal, "balance changed on replay")


SCENARIOS = [
    sc_statement_pay,
    sc_settlement_at_court,
    sc_settlement_online,
    sc_settlement_monthly,
    sc_commission,
    sc_tokens,
    sc_membership,
    sc_refund_request,
]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    # The CRM event feed writes core.usage_event in its own tx (can't see our uncommitted scratch
    # club). We test the money engines here, not the feed — stub diary.events.emit to a no-op.
    import diary.events
    diary.events.emit = lambda *a, **k: False
    # apply_payment_event emits payment_succeeded via marketing_crm.tracking (its OWN tx → FK on
    # the uncommitted scratch club). Guarded already, but stub it to keep the output clean.
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
        print(f"Scratch billing club {fx.club_id} · test day {fx.target}")
        for scenario in SCENARIOS:
            sp = s.begin_nested()
            try:
                scenario(s, fx)
            except Exception as e:
                check(f"{scenario.__name__} raised", False, repr(e))
            finally:
                if sp.is_active:
                    sp.rollback()
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

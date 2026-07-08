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
from billing import statement as ST
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
    print("\n# Settlement: monthly_account → the order itself is the debt (unified statement)")
    before = ST.statement(s, club_id=fx.club_id, user_id=fx.member)["total_owed_minor"]
    B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                     booking_type="court", resource_id=fx.courts[0],
                     starts_at=iso(at(fx, 13)), ends_at=iso(at(fx, 14)),
                     settlement_mode="monthly_account")
    after = ST.statement(s, club_id=fx.club_id, user_id=fx.member)["total_owed_minor"]
    check("monthly_account booking is owed on the unified statement (R150)",
          after - before == 15000, f"delta={after-before}")


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
    # H2 regression: the auto-held court MUST confirm together with its online-paid lesson (it shares
    # the order but has NO order_line; the old confirm joined only order_line -> court left 'held' ->
    # lazy-expiry later cancelled it = a paid lesson with no court).
    court_status = s.execute(text(
        "SELECT status FROM diary.booking WHERE club_id=:c AND order_id=:o "
        "AND booking_type='court' AND notes='(court held for lesson)'"),
        {"c": fx.club_id, "o": oid}).scalar()
    check("H2: auto-held court confirms with the online-paid lesson", court_status == "confirmed",
          f"court_status={court_status}")
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


def sc_membership_purchase(s, fx):
    print("\n# Membership purchase: payment modes (per-service) + online vs offline settlement")
    # Per-service payment preference round-trips on the membership product (the default).
    MB.set_membership_payment_modes(s, club_id=fx.club_id, modes=["at_court"])
    check("membership pay modes set to [at_court]",
          MB.membership_payment_modes(s, club_id=fx.club_id) == ["at_court"])
    MB.set_membership_payment_modes(s, club_id=fx.club_id, modes=None)
    check("membership pay modes cleared → inherit (None)",
          MB.membership_payment_modes(s, club_id=fx.club_id) is None)

    # PER-TIER override: a single membership tier carries its OWN payment options, layered over the
    # product default → global. The fixture price gets [at_court] only; clearing inherits again.
    s.execute(text("UPDATE billing.price SET payment_modes = 'at_court' WHERE id = :p"),
              {"p": fx.membership_price})
    check("per-tier price pref resolves to [at_court]",
          MB.membership_modes_pref(s, club_id=fx.club_id, price_id=fx.membership_price) == ["at_court"])
    # product default takes over once the tier inherits
    MB.set_membership_payment_modes(s, club_id=fx.club_id, modes=["online", "at_court"])
    s.execute(text("UPDATE billing.price SET payment_modes = NULL WHERE id = :p"),
              {"p": fx.membership_price})
    check("cleared tier falls back to product default",
          MB.membership_modes_pref(s, club_id=fx.club_id, price_id=fx.membership_price) == ["online", "at_court"])
    MB.set_membership_payment_modes(s, club_id=fx.club_id, modes=None)  # reset for the rest of the scenario

    # ONLINE purchase → awaiting_payment order, NOT active until the webhook (needs_checkout).
    bu = _mk_user(s, "buy_online@bill.test", "BuyOnline")
    s.execute(text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
                   "VALUES (:c,:u,'member','active')"), {"c": fx.club_id, "u": bu})
    onl = MB.create_membership_order(s, club_id=fx.club_id, user_id=bu,
                                     price_id=fx.membership_price, settlement_mode="online")
    check("online order needs_checkout, not yet active",
          onl["needs_checkout"] and not onl["activated"], str(onl))
    check("online order is awaiting_payment", _order(s, onl["order_id"])["status"] == "awaiting_payment")
    check("online buyer NOT a member until paid",
          not PR.has_active_membership(s, club_id=fx.club_id, user_id=bu))

    # OFFLINE (at the desk) → 'open' order + membership ACTIVE immediately (no checkout).
    bu2 = _mk_user(s, "buy_desk@bill.test", "BuyDesk")
    s.execute(text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
                   "VALUES (:c,:u,'member','active')"), {"c": fx.club_id, "u": bu2})
    off = MB.create_membership_order(s, club_id=fx.club_id, user_id=bu2,
                                     price_id=fx.membership_price, settlement_mode="at_court")
    check("offline order: no checkout, activated immediately",
          (not off["needs_checkout"]) and off["activated"], str(off))
    check("offline order is 'open' (owed at desk)", _order(s, off["order_id"])["status"] == "open")
    check("offline buyer IS a member straight away",
          PR.has_active_membership(s, club_id=fx.club_id, user_id=bu2))

    # Status surfaces the actual plan name; self-cancel reverts to PAYG.
    stat = MB.membership_status(s, club_id=fx.club_id, user_id=bu2)
    check("status carries a plan name + subscription id",
          bool(stat.get("plan_name")) and bool(stat.get("subscription_id")), str(stat.get("plan_name")))
    canc = MB.cancel_membership(s, club_id=fx.club_id, user_id=bu2)
    check("self-cancel ends the membership (1 row)", canc["cancelled"] == 1, str(canc))
    check("cancelled buyer is no longer a member",
          not PR.has_active_membership(s, club_id=fx.club_id, user_id=bu2))


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


def sc_refund_clawback(s, fx):
    print("\n# Refund clawback: a refunded lesson reverses the coach's commission PROPORTIONALLY")
    s.execute(text("INSERT INTO billing.commission_rule (club_id, scope, commission_pct, "
                   "effective_from, active) VALUES (:c,'club',30,:ef,true)"),
              {"c": fx.club_id, "ef": datetime.now(timezone.utc) - timedelta(days=1)})
    # Lesson online R400, paid → coach earns R280 (70% of R400 @ 30% club cut).
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                         starts_at=iso(at(fx, 15)), ends_at=iso(at(fx, 16)), settlement_mode="online")
    oid = r["booking"]["order_id"]
    ev = NormalizedPaymentEvent(provider="yoco", kind="charge_succeeded", order_ref=oid,
                                provider_payment_id="p_claw_1", amount_minor=40000, currency="ZAR",
                                status="succeeded", direction="charge", club_id=str(fx.club_id),
                                user_id=str(fx.member), raw={"t": 20})
    apply_payment_event(ev, session=s)
    check("coach earns R280 on the paid lesson",
          CM.coach_balance(s, club_id=fx.club_id, coach_user_id=fx.coach_uid) == 28000,
          str(CM.coach_balance(s, club_id=fx.club_id, coach_user_id=fx.coach_uid)))
    # FULL refund (Yoco sends NO amount) → full clawback → coach back to R0, club eats only its own cut.
    rev = NormalizedPaymentEvent(provider="yoco", kind="refunded", order_ref=oid,
                                 provider_payment_id="rf_claw_1", amount_minor=0, currency="ZAR",
                                 status="refunded", direction="refund", club_id=str(fx.club_id),
                                 user_id=str(fx.member), raw={"t": 21})
    res = apply_payment_event(rev, session=s)
    check("full refund claws back the coach's commission (→ R0)",
          CM.coach_balance(s, club_id=fx.club_id, coach_user_id=fx.coach_uid) == 0,
          f"bal={CM.coach_balance(s, club_id=fx.club_id, coach_user_id=fx.coach_uid)} "
          f"claw={res.get('commission_clawback')}")
    check("order marked refunded", _order(s, oid)["status"] == "refunded")
    apply_payment_event(rev, session=s)   # replay
    check("refund replay is idempotent (still R0, no double clawback)",
          CM.coach_balance(s, club_id=fx.club_id, coach_user_id=fx.coach_uid) == 0)

    # PARTIAL refund on a second lesson: half back → half the commission clawed (R140 of R280).
    r2 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                          booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                          starts_at=iso(at(fx, 17)), ends_at=iso(at(fx, 18)), settlement_mode="online")
    oid2 = r2["booking"]["order_id"]
    apply_payment_event(NormalizedPaymentEvent(provider="yoco", kind="charge_succeeded", order_ref=oid2,
                        provider_payment_id="p_claw_2", amount_minor=40000, currency="ZAR",
                        status="succeeded", direction="charge", club_id=str(fx.club_id),
                        user_id=str(fx.member), raw={"t": 22}), session=s)
    bal_before = CM.coach_balance(s, club_id=fx.club_id, coach_user_id=fx.coach_uid)
    apply_payment_event(NormalizedPaymentEvent(provider="yoco", kind="refunded", order_ref=oid2,
                        provider_payment_id="rf_claw_2", amount_minor=20000, currency="ZAR",
                        status="refunded", direction="refund", club_id=str(fx.club_id),
                        user_id=str(fx.member), raw={"t": 23}), session=s)
    bal_after = CM.coach_balance(s, club_id=fx.club_id, coach_user_id=fx.coach_uid)
    check("half refund claws back half the commission (R140 of R280)",
          bal_before - bal_after == 14000, f"before={bal_before} after={bal_after}")


def sc_membership_cancel_voids_order(s, fx):
    print("\n# Cancel an UNPAID plan → its owed order is voided (drops off the statement); PAID untouched")
    from billing import statement as ST
    bu = _mk_user(s, "cancel_void@bill.test", "CancelVoid")
    s.execute(text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
                   "VALUES (:c,:u,'member','active')"), {"c": fx.club_id, "u": bu})
    off = MB.create_membership_order(s, club_id=fx.club_id, user_id=bu,
                                     price_id=fx.membership_price, settlement_mode="at_court")
    oid = off["order_id"]
    check("offline plan is an owed 'open' order", _order(s, oid)["status"] == "open")
    check("statement shows the owed plan before cancel",
          ST.statement(s, club_id=fx.club_id, user_id=bu)["count"] >= 1)
    canc = MB.cancel_membership(s, club_id=fx.club_id, user_id=bu)
    check("cancel voided exactly one unpaid order", canc.get("voided_orders") == 1, str(canc))
    check("cancelled plan's order is now 'void'", _order(s, oid)["status"] == "void")
    check("statement is clear after cancel (unpaid plan gone)",
          ST.statement(s, club_id=fx.club_id, user_id=bu)["count"] == 0)

    # A PAID plan is NOT voided on cancel (a refund is a separate flow).
    bu2 = _mk_user(s, "cancel_paid@bill.test", "CancelPaid")
    s.execute(text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
                   "VALUES (:c,:u,'member','active')"), {"c": fx.club_id, "u": bu2})
    off2 = MB.create_membership_order(s, club_id=fx.club_id, user_id=bu2,
                                      price_id=fx.membership_price, settlement_mode="at_court")
    oid2 = off2["order_id"]
    O.record_desk_payment(s, club_id=fx.club_id, order_id=oid2,
                          amount_minor=_order(s, oid2)["amount_minor"], provider="cash",
                          provider_payment_id="RCPT-PAIDPLAN", user_id=bu2)
    check("paid plan order is 'paid'", _order(s, oid2)["status"] == "paid")
    canc2 = MB.cancel_membership(s, club_id=fx.club_id, user_id=bu2)
    check("cancel does NOT void a paid plan", canc2.get("voided_orders") == 0, str(canc2))
    check("paid plan order stays 'paid' after cancel", _order(s, oid2)["status"] == "paid")


def sc_client_month_end(s, fx):
    print("\n# Coach month-end: single-client invoice (paid + owed) + issue + 360 money merge")
    from coach import repositories as CR
    s.execute(text("INSERT INTO billing.commission_rule (club_id, scope, commission_pct, "
                   "effective_from, active) VALUES (:c,'club',30,:ef,true)"),
              {"c": fx.club_id, "ef": datetime.now(timezone.utc) - timedelta(days=1)})
    ym = s.execute(text("SELECT to_char(now(),'YYYY-MM')")).scalar()
    # A PAID online lesson → the client is a real coaching relationship + a 'paid' invoice line.
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                         starts_at=iso(at(fx, 9)), ends_at=iso(at(fx, 10)), settlement_mode="online")
    apply_payment_event(NormalizedPaymentEvent(provider="yoco", kind="charge_succeeded",
                        order_ref=r["booking"]["order_id"], provider_payment_id="p_inv_1",
                        amount_minor=40000, currency="ZAR", status="succeeded", direction="charge",
                        club_id=str(fx.club_id), user_id=str(fx.member), raw={"t": 40}), session=s)
    # An OWED off-platform lesson on the tab.
    s.execute(text("INSERT INTO billing.coach_arrears (club_id, coach_user_id, client_user_id, "
                   "gross_minor, currency, status) VALUES (:c,:coach,:u,40000,'ZAR','owed')"),
              {"c": fx.club_id, "coach": fx.coach_uid, "u": fx.member})

    inv = CM.client_invoice_data(s, club_id=fx.club_id, coach_user_id=fx.coach_uid,
                                 client_user_id=fx.member, month=ym)
    check("invoice shows a PAID line + an OWED line",
          inv["totals"]["paid_minor"] > 0 and inv["totals"]["owed_minor"] == 40000, str(inv["totals"]))
    res = CM.issue_client_invoice(s, club_id=fx.club_id, coach_user_id=fx.coach_uid,
                                  client_user_id=fx.member, month=ym)
    check("issue-invoice reports owed + notifies", res["owed_minor"] == 40000 and res["notified"], str(res))

    c = CR.get_client(s, club_id=fx.club_id, user_id=fx.coach_uid, client_user_id=fx.member, month=ym)
    check("coach 360 merges month money (paid + owed)",
          c and c.get("money") and c["money"]["owed_minor"] == 40000 and c["money"]["paid_minor"] > 0,
          str(c and c.get("money")))
    check("coach 360 lists this client's owed arrears line",
          any(a.get("status") == "owed" for a in (c.get("arrears") or [])), str(len(c.get("arrears") or [])))
    check("coach 360 history carries booking_id (for reschedule/cancel)",
          any(h.get("booking_id") for h in (c.get("history") or [])), "no booking_id surfaced")
    # A coach can only invoice THEIR OWN client — an unrelated user yields an empty invoice.
    other = _mk_user(s, "stranger@bill.test", "Stranger")
    empty = CM.client_invoice_data(s, club_id=fx.club_id, coach_user_id=fx.coach_uid,
                                   client_user_id=other, month=ym)
    check("no coaching relationship → empty invoice",
          empty["totals"]["paid_minor"] == 0 and empty["totals"]["owed_minor"] == 0, str(empty["totals"]))


def _line_of(s, oid):
    return s.execute(text("SELECT id FROM billing.order_line WHERE order_id=:o ORDER BY created_at LIMIT 1"),
                     {"o": str(oid)}).scalar()


def _seed_owed_arrears(s, fx, order_line_id, gross=40000):
    s.execute(text("INSERT INTO billing.coach_arrears (club_id, coach_user_id, client_user_id, "
                   "order_line_id, gross_minor, currency, status) "
                   "VALUES (:c,:coach,:u,:ol,:g,'ZAR','owed')"),
              {"c": fx.club_id, "coach": fx.coach_uid, "u": fx.member, "ol": order_line_id, "g": gross})


def _arrears_status(s, order_line_id):
    return s.execute(text("SELECT status FROM billing.coach_arrears WHERE order_line_id=:ol"),
                     {"ol": order_line_id}).scalar()


def sc_lockstep_desk_pay(s, fx):
    print("\n# Lockstep: desk-paying an at-court lesson clears arrears + no double-commission")
    s.execute(text("INSERT INTO billing.commission_rule (club_id, scope, commission_pct, "
                   "effective_from, active) VALUES (:c,'club',30,:ef,true)"),
              {"c": fx.club_id, "ef": datetime.now(timezone.utc) - timedelta(days=1)})
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                         starts_at=iso(at(fx, 9)), ends_at=iso(at(fx, 10)), settlement_mode="at_court")
    oid = r["booking"]["order_id"]; ol = _line_of(s, oid)
    _seed_owed_arrears(s, fx, ol)                                # an owed lesson on the coach tab
    O.record_desk_payment(s, club_id=fx.club_id, order_id=oid, amount_minor=40000, provider="cash",
                          provider_payment_id="RCPT-DESK", user_id=fx.member)
    check("desk pay cleared the coach's owed tab (lockstep)", _arrears_status(s, ol) == "collected",
          str(_arrears_status(s, ol)))
    bal = CM.coach_balance(s, club_id=fx.club_id, coach_user_id=fx.coach_uid)
    check("coach earned commission exactly once on desk pay", bal == 28000, f"bal={bal}")
    # Simulate drift (arrears back to 'owed') then a stray 'mark collected' → guard = no double.
    s.execute(text("UPDATE billing.coach_arrears SET status='owed' WHERE order_line_id=:ol"), {"ol": ol})
    aid = s.execute(text("SELECT id FROM billing.coach_arrears WHERE order_line_id=:ol"), {"ol": ol}).scalar()
    res = CM.mark_arrears_collected(s, club_id=fx.club_id, arrears_id=aid)
    check("re-collect on an already-paid order is a no-op (guard)",
          res.get("status") == "reconciled" and res.get("splits") == 0, str(res))
    check("coach commission NOT doubled after stray re-collect",
          CM.coach_balance(s, club_id=fx.club_id, coach_user_id=fx.coach_uid) == 28000, "doubled!")


def sc_void_clears_arrears(s, fx):
    print("\n# Void/write-off an order also drops it off the coach tab (no commission on a forgiven lesson)")
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                         starts_at=iso(at(fx, 11)), ends_at=iso(at(fx, 12)), settlement_mode="at_court")
    oid = r["booking"]["order_id"]; ol = _line_of(s, oid)
    _seed_owed_arrears(s, fx, ol)
    ST.void_order(s, club_id=fx.club_id, order_id=oid, write_off=True, reason="goodwill")
    check("write-off dropped the lesson off the coach tab", _arrears_status(s, ol) == "written_off",
          str(_arrears_status(s, ol)))
    aid = s.execute(text("SELECT id FROM billing.coach_arrears WHERE order_line_id=:ol"), {"ol": ol}).scalar()
    res = CM.mark_arrears_collected(s, club_id=fx.club_id, arrears_id=aid)
    check("can't collect commission on a written-off lesson",
          res.get("splits") == 0 and CM.coach_balance(s, club_id=fx.club_id, coach_user_id=fx.coach_uid) == 0,
          str(res))


def sc_settlement_refund_clawback(s, fx):
    print("\n# Refund of a 'pay all' settlement order claws back the child lesson's commission")
    s.execute(text("INSERT INTO billing.commission_rule (club_id, scope, commission_pct, "
                   "effective_from, active) VALUES (:c,'club',30,:ef,true)"),
              {"c": fx.club_id, "ef": datetime.now(timezone.utc) - timedelta(days=1)})
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                         starts_at=iso(at(fx, 9)), ends_at=iso(at(fx, 10)), settlement_mode="at_court")
    settle = ST.create_settlement_order(s, club_id=fx.club_id, user_id=fx.member)
    check("settlement order covers the owed lesson", settle and settle["amount_minor"] == 40000, str(settle))
    sid = settle["order_id"]
    apply_payment_event(NormalizedPaymentEvent(provider="yoco", kind="charge_succeeded", order_ref=sid,
                        provider_payment_id="p_settle_cb", amount_minor=40000, currency="ZAR",
                        status="succeeded", direction="charge", club_id=str(fx.club_id),
                        user_id=str(fx.member), raw={"t": 50}), session=s)
    check("coach earned R280 via the settlement", CM.coach_balance(s, club_id=fx.club_id, coach_user_id=fx.coach_uid) == 28000,
          str(CM.coach_balance(s, club_id=fx.club_id, coach_user_id=fx.coach_uid)))
    apply_payment_event(NormalizedPaymentEvent(provider="yoco", kind="refunded", order_ref=sid,
                        provider_payment_id="rf_settle_cb", amount_minor=0, currency="ZAR",
                        status="refunded", direction="refund", club_id=str(fx.club_id),
                        user_id=str(fx.member), raw={"t": 51}), session=s)
    check("refunding the settlement claws back the child lesson's commission (→ R0)",
          CM.coach_balance(s, club_id=fx.club_id, coach_user_id=fx.coach_uid) == 0,
          str(CM.coach_balance(s, club_id=fx.club_id, coach_user_id=fx.coach_uid)))


def sc_abandoned_reclaim_on_read(s, fx):
    print("\n# Abandoned 'pay all' checkout re-surfaces the debt on READ (not just on retry)")
    for hh in (9, 11):
        B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         starts_at=iso(at(fx, hh)), ends_at=iso(at(fx, hh + 1)), settlement_mode="at_court")
    settle = ST.create_settlement_order(s, club_id=fx.club_id, user_id=fx.member)
    sid = settle["order_id"]
    check("while settling (in-flight), statement shows nothing owed",
          ST.statement(s, club_id=fx.club_id, user_id=fx.member)["count"] == 0)
    # Client abandons; the checkout ages past the in-flight grace window.
    s.execute(text("UPDATE billing.\"order\" SET created_at = created_at - interval '40 minutes' WHERE id=:o"),
              {"o": sid})
    st = ST.statement(s, club_id=fx.club_id, user_id=fx.member)
    check("an abandoned checkout re-surfaces the debt on read", st["count"] == 2 and st["total_owed_minor"] == 30000,
          str(st.get("count")) + "/" + str(st.get("total_owed_minor")))


def sc_client_by_service(s, fx):
    print("\n# Coach client record: sessions grouped BY SERVICE, drillable to the event")
    for hh in (9, 11):
        B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                         starts_at=iso(at(fx, hh)), ends_at=iso(at(fx, hh + 1)), settlement_mode="at_court")
    bd = CM.client_service_breakdown(s, club_id=fx.club_id, coach_user_id=fx.coach_uid,
                                     client_user_id=fx.member, month=None)
    check("by-service groups the 2 lessons into one service",
          len(bd["services"]) == 1 and bd["services"][0]["count"] == 2, str(bd.get("services")))
    check("service billed = both lessons (gross)", bd["billed_minor"] == bd["services"][0]["billed_minor"] and bd["billed_minor"] > 0, str(bd["billed_minor"]))
    check("each session carries booking_id for the event drill",
          all(it.get("booking_id") for it in bd["services"][0]["items"]), str(bd["services"][0]["items"]))
    # Write one lesson off + discount the other → the breakdown reflects the REAL state (not just 'owed').
    CM.accrue_arrears_for_club(s, club_id=fx.club_id)
    aids = s.execute(text("SELECT id, gross_minor FROM billing.coach_arrears WHERE club_id=:c AND coach_user_id=:co "
                          "AND client_user_id=:cl ORDER BY created_at"),
                     {"c": fx.club_id, "co": str(fx.coach_uid), "cl": str(fx.member)}).mappings().all()
    check("both lessons accrued an arrears line", len(aids) == 2, str(len(aids)))
    orig = int(aids[0]["gross_minor"] or 0)
    CM.adjust_arrears(s, club_id=fx.club_id, arrears_id=aids[0]["id"], status="written_off", reason="waived")
    CM.adjust_arrears(s, club_id=fx.club_id, arrears_id=aids[1]["id"], gross_minor=orig - 5000)  # discount R50
    bd2 = CM.client_service_breakdown(s, club_id=fx.club_id, coach_user_id=fx.coach_uid,
                                      client_user_id=fx.member, month=None)
    sts = sorted(it["status"] for it in bd2["services"][0]["items"])
    check("statuses now reflect written_off + discounted", sts == ["discounted", "written_off"], str(sts))
    disc = [it for it in bd2["services"][0]["items"] if it["status"] == "discounted"][0]
    check("discounted session shows reduced effective + original billed",
          disc["amount_minor"] == orig - 5000 and disc["billed_minor"] == orig, str(disc))
    check("total billed unchanged by write-off/discount (still gross)", bd2["billed_minor"] == bd["billed_minor"], str(bd2["billed_minor"]))


def sc_dispute_routing(s, fx):
    print("\n# Dispute routing: a coaching refund → the coach decides; a court refund → the club")
    # A paid LESSON (coaching service) → routes to the coach.
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                         starts_at=iso(at(fx, 9)), ends_at=iso(at(fx, 10)), settlement_mode="at_court")
    loid = r["booking"]["order_id"]
    O.record_desk_payment(s, club_id=fx.club_id, order_id=loid,
                          amount_minor=_order(s, loid)["amount_minor"], provider="cash",
                          provider_payment_id="RCPT-LSN", user_id=fx.member)
    lreq, lerr = RF.create_refund_request(s, club_id=fx.club_id, user_id=fx.member, order_id=loid,
                                          reason="coach late")
    check("coaching dispute routes to the coach",
          lerr is None and lreq["routed_to"] == "coach" and lreq["coach_user_id"] == str(fx.coach_uid),
          str(lreq))

    # A paid COURT (non-coaching) → routes to the club (no coach).
    rc = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                          booking_type="court", resource_id=fx.courts[0],
                          starts_at=iso(at(fx, 11)), ends_at=iso(at(fx, 12)), settlement_mode="at_court")
    coid = rc["booking"]["order_id"]
    O.record_desk_payment(s, club_id=fx.club_id, order_id=coid,
                          amount_minor=_order(s, coid)["amount_minor"], provider="cash",
                          provider_payment_id="RCPT-CRT", user_id=fx.member)
    creq, cerr = RF.create_refund_request(s, club_id=fx.club_id, user_id=fx.member, order_id=coid)
    check("court dispute routes to the club",
          cerr is None and creq["routed_to"] == "club" and creq["coach_user_id"] is None, str(creq))

    # The coach sees only THEIR dispute; the club oversees BOTH.
    coach_q = RF.list_refund_requests_coach(s, club_id=fx.club_id, coach_user_id=fx.coach_uid)
    check("coach queue = only their coaching dispute",
          len(coach_q) == 1 and coach_q[0]["id"] == lreq["id"], f"n={len(coach_q)}")
    admin_q = RF.list_refund_requests_admin(s, club_id=fx.club_id, status="pending")
    check("club queue oversees both disputes", len(admin_q) == 2, f"n={len(admin_q)}")

    # A DIFFERENT coach cannot decide this coach's dispute.
    other = _mk_user(s, "othercoach2@bill.test", "OtherCoach2")
    _, ferr = RF.decline_refund_request(s, club_id=fx.club_id, request_id=lreq["id"],
                                        decided_by=other, require_coach_user_id=other)
    check("another coach is forbidden from deciding it", ferr == "FORBIDDEN", str(ferr))

    # The owning coach declines their own dispute (no money moves).
    dec, derr = RF.decline_refund_request(s, club_id=fx.club_id, request_id=lreq["id"],
                                          decided_by=fx.coach_uid, require_coach_user_id=fx.coach_uid,
                                          note="offered a make-up lesson")
    check("the owning coach can decline their dispute",
          derr is None and dec["status"] == "declined", str(derr))
    # The club can still decide the court dispute (oversight path, no coach guard).
    cdec, cderr = RF.decline_refund_request(s, club_id=fx.club_id, request_id=creq["id"],
                                            decided_by=fx.member, note="not eligible")
    check("the club decides the non-coaching dispute", cderr is None and cdec["status"] == "declined",
          str(cderr))


def sc_booking_story(s, fx):
    print("\n# Booking story: one payload assembles court + charge + players + eligibility")
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         starts_at=iso(at(fx, 9)), ends_at=iso(at(fx, 10)), settlement_mode="at_court")
    bid = r["booking"]["id"]
    story = B.booking_story(s, club_id=fx.club_id, user_id=fx.member, booking_id=bid)
    check("story assembles court name + owed charge + pay action",
          story and story["court_name"] and story["charge"]["status"] == "owed"
          and story["can"]["pay"] and story["can"]["receipt"] is False, str(story and story.get("charge")))
    check("story lists the player(s)", story and len(story["players"]) >= 1, str(story and story.get("players")))
    # Scoped: a different user cannot read someone else's booking story.
    other = _mk_user(s, "peeker@bill.test", "Peeker")
    check("another user can't read the booking story",
          B.booking_story(s, club_id=fx.club_id, user_id=other, booking_id=bid) is None)


def sc_cancel_voids_order(s, fx):
    print("\n# Cancelling a booking voids its unpaid order (no phantom 'owed' after cancel)")
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         starts_at=iso(at(fx, 15)), ends_at=iso(at(fx, 16)), settlement_mode="at_court")
    bid = r["booking"]["id"]; oid = r["booking"]["order_id"]
    check("court is owed on the statement before cancel", _order(s, oid)["status"] == "open")
    st0 = ST.statement(s, club_id=fx.club_id, user_id=fx.member)
    check("statement lists the owed court", any(i["order_id"] == str(oid) for i in st0["items"]), str(st0["count"]))
    B.cancel_booking(s, club_id=fx.club_id, booking_id=bid, actor_user_id=fx.member, role="member")
    check("cancelled booking's order is now void", _order(s, oid)["status"] == "void")
    st1 = ST.statement(s, club_id=fx.club_id, user_id=fx.member)
    check("cancelled booking no longer owed", not any(i["order_id"] == str(oid) for i in st1["items"]),
          f"still owed count={st1['count']}")


def sc_phantom_cleanup(s, fx):
    print("\n# Self-heal: a cancelled-booking order stuck 'open' is voided on statement read")
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         starts_at=iso(at(fx, 17)), ends_at=iso(at(fx, 18)), settlement_mode="at_court")
    bid = r["booking"]["id"]; oid = r["booking"]["order_id"]
    # Simulate a PRE-FIX phantom: cancel the booking directly, leaving the order 'open'.
    s.execute(text("UPDATE diary.booking SET status='cancelled' WHERE id=:b"), {"b": bid})
    check("phantom order is 'open' before heal", _order(s, oid)["status"] == "open")
    st = ST.statement(s, club_id=fx.club_id, user_id=fx.member)     # read → self-heal
    check("phantom order voided on statement read", _order(s, oid)["status"] == "void")
    check("phantom no longer owed", not any(i["order_id"] == str(oid) for i in st["items"]))
    # A non-booking owed order (membership bought offline) is NEVER touched.
    bu = _mk_user(s, "phantomsafe@bill.test", "PhantomSafe")
    s.execute(text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
                   "VALUES (:c,:u,'member','active')"), {"c": fx.club_id, "u": bu})
    mo = MB.create_membership_order(s, club_id=fx.club_id, user_id=bu,
                                    price_id=fx.membership_price, settlement_mode="at_court")
    ST.statement(s, club_id=fx.club_id, user_id=bu)                 # read → heal runs
    check("owed membership order is left intact (not a phantom)",
          _order(s, mo["order_id"])["status"] == "open", str(_order(s, mo["order_id"])["status"]))


def sc_coach_event_story(s, fx):
    print("\n# Coach event story: a lesson the coach runs → client + charge + coach actions")
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                         starts_at=iso(at(fx, 9)), ends_at=iso(at(fx, 10)), settlement_mode="at_court")
    bid = r["booking"]["id"]
    story = B.coach_booking_story(s, club_id=fx.club_id, coach_user_id=fx.coach_uid, booking_id=bid)
    check("coach story carries the client + owed charge + coach actions",
          story and story["client"]["name"] and story["charge"]["status"] == "owed"
          and story["can"]["cancel"] and story["can"]["reschedule"], str(story and story.get("charge")))
    check("coach story exposes client contact (email)",
          story and ("email" in story["client"]), str(story and story.get("client")))
    other = _mk_user(s, "othercoach3@bill.test", "OtherCoach3")
    check("another coach can't read this coach's event story",
          B.coach_booking_story(s, club_id=fx.club_id, coach_user_id=other, booking_id=bid) is None)


def sc_transaction_log(s, fx):
    print("\n# Transaction log: one chronological feed, role-scoped (client / coach / owner)")
    from billing import activity as ACT
    s.execute(text("INSERT INTO billing.commission_rule (club_id, scope, commission_pct, "
                   "effective_from, active) VALUES (:c,'club',30,:ef,true)"),
              {"c": fx.club_id, "ef": datetime.now(timezone.utc) - timedelta(days=1)})
    # Pay a lesson online, then refund it → the feed should carry payment, commission, refund, clawback.
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                         starts_at=iso(at(fx, 15)), ends_at=iso(at(fx, 16)), settlement_mode="online")
    oid = r["booking"]["order_id"]
    apply_payment_event(NormalizedPaymentEvent(provider="yoco", kind="charge_succeeded", order_ref=oid,
                        provider_payment_id="p_log_1", amount_minor=40000, currency="ZAR",
                        status="succeeded", direction="charge", club_id=str(fx.club_id),
                        user_id=str(fx.member), raw={"t": 30}), session=s)
    apply_payment_event(NormalizedPaymentEvent(provider="yoco", kind="refunded", order_ref=oid,
                        provider_payment_id="rf_log_1", amount_minor=0, currency="ZAR",
                        status="refunded", direction="refund", club_id=str(fx.club_id),
                        user_id=str(fx.member), raw={"t": 31}), session=s)

    owner = ACT.transaction_log(s, club_id=fx.club_id, scope="owner")
    kinds = {e["kind"] for e in owner}
    check("owner feed has the payment", "payment" in kinds, str(sorted(kinds)))
    check("owner feed has the refund", "refund" in kinds, str(sorted(kinds)))
    check("owner feed has commission earned", "commission_earned" in kinds, str(sorted(kinds)))
    check("owner feed has the refund clawback", "refund_clawback" in kinds, str(sorted(kinds)))
    check("owner feed is newest-first",
          all((owner[i]["at"] or "") >= (owner[i + 1]["at"] or "") for i in range(len(owner) - 1)))

    client = ACT.transaction_log(s, club_id=fx.club_id, scope="client", user_id=fx.member)
    ckinds = {e["kind"] for e in client}
    check("client sees their payment + refund", {"payment", "refund"} <= ckinds, str(sorted(ckinds)))
    check("client does NOT see commission internals",
          "commission_earned" not in ckinds and "refund_clawback" not in ckinds, str(sorted(ckinds)))

    coach = ACT.transaction_log(s, club_id=fx.club_id, scope="coach", user_id=fx.coach_uid)
    kkinds = {e["kind"] for e in coach}
    check("coach sees commission earned + clawback",
          {"commission_earned", "refund_clawback"} <= kkinds, str(sorted(kkinds)))
    # A coach must never see another coach's money: scope by coach_user_id.
    other = _mk_user(s, "othercoach@bill.test", "OtherCoach")
    empty = ACT.transaction_log(s, club_id=fx.club_id, scope="coach", user_id=other)
    check("a different coach sees none of this coach's commission",
          not any(e["kind"] in ("commission_earned", "refund_clawback") for e in empty), str(len(empty)))


def sc_payment_preference(s, fx):
    print("\n# Per-service payment preference: a service offering only 'at_court' refuses online")
    s.execute(text("UPDATE billing.product SET payment_modes='at_court' WHERE id=:p"), {"p": fx.court_product})
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         starts_at=iso(at(fx, 9)), ends_at=iso(at(fx, 10)), settlement_mode="online")
    check("online refused when service is at-court-only", r.get("error") == "SETTLEMENT_NOT_ALLOWED", str(r))
    r2 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                          booking_type="court", resource_id=fx.courts[1],
                          starts_at=iso(at(fx, 9)), ends_at=iso(at(fx, 10)), settlement_mode="at_court")
    check("at-court accepted (the allowed method)", r2.get("ok"), str(r2))
    s.execute(text("UPDATE billing.product SET payment_modes=NULL WHERE id=:p"), {"p": fx.court_product})
    r3 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                          booking_type="court", resource_id=fx.courts[0],
                          starts_at=iso(at(fx, 11)), ends_at=iso(at(fx, 12)), settlement_mode="online")
    check("online allowed again with no restriction", r3.get("ok"), str(r3))


def sc_person_360(s, fx):
    """admin.get_person — the unified person 360 (ADMIN-REDESIGN Step 2). A member with an owed
    (monthly) order surfaces with a statement + that booking on the record; a coach surfaces with
    a settlement block; a non-member of the club resolves to None."""
    from admin import repositories as AR
    # Member: a monthly-account (owed) court booking → statement + bookings.
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         starts_at=iso(at(fx, 8)), ends_at=iso(at(fx, 9)),
                         settlement_mode="monthly_account")
    check("person: setup booking created", r.get("ok"), str(r))
    person = AR.get_person(s, club_id=fx.club_id, user_id=fx.member)
    check("person: member resolves with role",
          person is not None and "member" in (person.get("roles") or []),
          str(person and person.get("roles")))
    check("person: owed reflects the monthly order", person and person.get("owed_minor", 0) > 0,
          str(person and person.get("owed_minor")))
    check("person: statement items present",
          person and len((person.get("statement") or {}).get("items") or []) >= 1, "")
    check("person: booking on the record", person and person.get("bookings_count", 0) >= 1,
          str(person and person.get("bookings_count")))
    # Coach: give them the coach membership (invite normally does) then resolve → settlement block.
    s.execute(text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
                   "VALUES (:c,:u,'coach','active') "
                   "ON CONFLICT (club_id, user_id, role) DO NOTHING"),
              {"c": fx.club_id, "u": fx.coach_uid})
    coach = AR.get_person(s, club_id=fx.club_id, user_id=fx.coach_uid)
    check("person: coach resolves as coach",
          coach is not None and coach.get("is_coach") is True, str(coach and coach.get("roles")))
    check("person: coach has a settlement block",
          coach is not None and isinstance(coach.get("settlement"), dict), "")
    # A user with no membership in this club → None (can't probe arbitrary users).
    stranger = _mk_user(s, "stranger360@bill.test", "Stranger")
    check("person: non-member → None",
          AR.get_person(s, club_id=fx.club_id, user_id=stranger) is None, "")


def sc_admin_event_story(s, fx):
    """admin_booking_story (the ONE god-view drill target) + admin_reassign_coach (ADMIN-REDESIGN
    Step 3): a lesson resolves with client + coach + charge + full action eligibility; a future,
    unpaid lesson reassigns to another bookable coach; reassigning to the same coach is rejected."""
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="club_admin",
                         booking_type="lesson", resource_id=fx.coach_res,
                         starts_at=iso(at(fx, 10)), ends_at=iso(at(fx, 11)),
                         coach_user_id=fx.coach_uid, settlement_mode="at_court",
                         booked_for_user_id=fx.member)
    check("event: lesson created on-behalf", r.get("ok"), str(r))
    bid = r["booking"]["id"]
    story = B.admin_booking_story(s, club_id=fx.club_id, booking_id=bid)
    check("event: story resolves", story is not None, "")
    check("event: client + coach both present",
          story and (story.get("client") or {}).get("user_id") and (story.get("coach") or {}).get("user_id"),
          str(story and (story.get("client"), story.get("coach"))))
    check("event: charge block present", story and isinstance(story.get("charge"), dict), "")
    check("event: reassign offered on a future unpaid lesson",
          story and (story.get("can") or {}).get("reassign_coach") is True, str(story and story.get("can")))
    # Second bookable coach + resource + hours.
    coach2 = _mk_user(s, "coach2-evt@bill.test", "Coachy2")
    s.execute(text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
                   "VALUES (:c,:u,'coach','active') ON CONFLICT (club_id,user_id,role) DO NOTHING"),
              {"c": fx.club_id, "u": coach2})
    s.execute(text("INSERT INTO iam.coach_profile (club_id, user_id, display_name, is_bookable) "
                   "VALUES (:c,:u,'Coachy2',true)"), {"c": fx.club_id, "u": coach2})
    res2 = s.execute(text("INSERT INTO diary.resource (club_id, kind, name, coach_user_id) "
                          "VALUES (:c,'coach','Coachy2',:u) RETURNING id"),
                     {"c": fx.club_id, "u": coach2}).scalar_one()
    s.execute(text("INSERT INTO diary.availability_rule (club_id, resource_id, weekday, start_time, "
                   "end_time, slot_minutes) VALUES (:c,:r,:wd,'08:00','18:00',30)"),
              {"c": fx.club_id, "r": res2, "wd": fx.target.weekday()})
    res = B.admin_reassign_coach(s, club_id=fx.club_id, booking_id=bid, new_coach_user_id=coach2)
    check("event: reassign ok", res.get("ok"), str(res))
    story2 = B.admin_booking_story(s, club_id=fx.club_id, booking_id=bid)
    check("event: coach changed after reassign",
          story2 and str((story2.get("coach") or {}).get("user_id")) == str(coach2),
          str(story2 and story2.get("coach")))
    again = B.admin_reassign_coach(s, club_id=fx.club_id, booking_id=bid, new_coach_user_id=coach2)
    check("event: reassign to the same coach rejected",
          not again.get("ok") and again.get("error") == "SAME_COACH", str(again))
    # A non-existent booking → None.
    check("event: unknown booking → None",
          B.admin_booking_story(s, club_id=fx.club_id,
                                booking_id="00000000-0000-0000-0000-000000000000") is None, "")


def sc_court_utilisation(s, fx):
    """insights.court_utilisation (Phase 2 P1 read-layer): a well-formed heatmap payload; a past
    court booking lifts booked_hours + adds a cell. Guarded — empty club → zeros, never raises."""
    from insights import repositories as INS
    empty = INS.court_utilisation(s, club_id=fx.club_id, days=7)
    check("util: payload shape",
          isinstance(empty.get("cells"), list) and "overall_pct" in empty and "booked_hours" in empty,
          str(empty)[:120])
    # A past completed court booking (inserted directly — create_booking refuses past slots).
    s.execute(text("INSERT INTO diary.booking (club_id, booking_type, resource_id, starts_at, ends_at, "
                   "status, booked_by_user_id, settlement_mode) "
                   "VALUES (:c,'court',:r, now() - interval '2 days', "
                   "        now() - interval '2 days' + interval '1 hour', 'completed', :u, 'at_court')"),
              {"c": fx.club_id, "r": fx.courts[0], "u": fx.member})
    u = INS.court_utilisation(s, club_id=fx.club_id, days=7)
    check("util: booked hours reflected", (u.get("booked_hours") or 0) > 0, str(u.get("booked_hours")))
    check("util: a heatmap cell exists", len(u.get("cells") or []) >= 1, str(len(u.get("cells") or [])))


def sc_sales_by_day(s, fx):
    """insights.sales_by_day (Money → daily takings): a desk-settled court sale shows in the current
    month grouped by day, with client + service type + amount + a booking_id detail link."""
    from insights import repositories as INS
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         starts_at=iso(at(fx, 9)), ends_at=iso(at(fx, 10)), settlement_mode="at_court")
    oid = r["booking"]["order_id"]
    O.record_desk_payment(s, club_id=fx.club_id, order_id=oid, amount_minor=15000,
                          provider="cash", user_id=fx.member)
    data = INS.sales_by_day(s, club_id=fx.club_id, month=None)  # current month (payment is now())
    check("sales: total reflects the sale", (data.get("total_minor") or 0) >= 15000, str(data.get("total_minor")))
    check("sales: at least one day bucket", len(data.get("days") or []) >= 1, str(len(data.get("days") or [])))
    sale = None
    for d in (data.get("days") or []):
        for x in (d.get("sales") or []):
            sale = x
    check("sales: row has client + service type + amount",
          sale is not None and sale.get("client_name") and sale.get("service_type") and (sale.get("amount_minor") or 0) > 0,
          str(sale))
    check("sales: court sale carries a booking_id (event-story link)",
          sale is not None and sale.get("booking_id"), str(sale and sale.get("booking_id")))


def sc_coach_scoped_pricing(s, fx):
    """H1 regression: a lesson is priced on the SELECTED coach's OWN rate card, never the cheapest
    coach's. Two coaches at different rates; booking with each must charge that coach's own price."""
    print("\n# Coach rate cards: a lesson is priced on the SELECTED coach's own rate (not the cheapest)")
    # Coach A (fx.coach_uid) gets an OWN lesson product at R400/60; a 2nd coach B at a CHEAPER R300/60.
    prodA = s.execute(text("INSERT INTO billing.product (club_id, kind, name, coach_user_id) "
                           "VALUES (:c,'lesson','A Lesson',:u) RETURNING id"),
                      {"c": fx.club_id, "u": fx.coach_uid}).scalar_one()
    _price(s, fx.club_id, prodA, 40000, dur=60)
    coachB = _mk_user(s, "coachb@bill.test", "CoachB")
    s.execute(text("INSERT INTO iam.coach_profile (club_id, user_id, display_name, is_bookable) "
                   "VALUES (:c,:u,'CoachB',true)"), {"c": fx.club_id, "u": coachB})
    resB = s.execute(text("INSERT INTO diary.resource (club_id, kind, name, coach_user_id) "
                          "VALUES (:c,'coach','CoachB',:u) RETURNING id"),
                     {"c": fx.club_id, "u": coachB}).scalar_one()
    s.execute(text("INSERT INTO diary.availability_rule (club_id, resource_id, weekday, "
                   "start_time, end_time, slot_minutes) VALUES (:c,:r,:wd,'08:00','18:00',30)"),
              {"c": fx.club_id, "r": resB, "wd": fx.target.weekday()})
    prodB = s.execute(text("INSERT INTO billing.product (club_id, kind, name, coach_user_id) "
                           "VALUES (:c,'lesson','B Lesson',:u) RETURNING id"),
                      {"c": fx.club_id, "u": coachB}).scalar_one()
    _price(s, fx.club_id, prodB, 30000, dur=60)   # cheaper — the OLD unscoped code picked this for ANY coach
    rA = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                          booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                          starts_at=iso(at(fx, 8)), ends_at=iso(at(fx, 9)), settlement_mode="at_court")
    check("coach A lesson priced at A's R400 (NOT the cheaper coach's R300)",
          _order(s, rA["booking"]["order_id"])["amount_minor"] == 40000,
          str(_order(s, rA["booking"]["order_id"])["amount_minor"]))
    rB = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                          booking_type="lesson", resource_id=resB, coach_user_id=coachB,
                          starts_at=iso(at(fx, 10)), ends_at=iso(at(fx, 11)), settlement_mode="at_court")
    check("coach B lesson priced at B's own R300",
          _order(s, rB["booking"]["order_id"])["amount_minor"] == 30000,
          str(_order(s, rB["booking"]["order_id"])["amount_minor"]))
    # A club-shared product with a phantom 45-min + a ZERO-rated 60 must NOT leak into a coach who has
    # their OWN rate card (the reported "45 appears / 60 comes through free" bug).
    shared = s.execute(text("INSERT INTO billing.product (club_id, kind, name) "
                            "VALUES (:c,'lesson','Shared Lesson') RETURNING id"),
                       {"c": fx.club_id}).scalar_one()
    _price(s, fx.club_id, shared, 55000, dur=45)   # phantom 45-min
    _price(s, fx.club_id, shared, 0, dur=60)         # zero-rated 60 (the leak source)
    dmins = [d["duration_minutes"] for d in PR.durations_for(s, club_id=fx.club_id, kind="lesson", coach_user_id=fx.coach_uid)]
    check("coach A durations show ONLY their own (no leaked shared 45-min)", 45 not in dmins, str(dmins))
    prA60 = PR.price_for(s, club_id=fx.club_id, kind="lesson", duration_minutes=60, coach_user_id=fx.coach_uid)
    check("coach A 60-min is A's own R400, NOT the shared R0", prA60 and prA60["amount_minor"] == 40000,
          str(prA60 and prA60.get("amount_minor")))


def sc_settlement_guards(s, fx):
    """H3/H4: crafted settlement modes can't mint a free/R0 booking. 'free' is refused for a member;
    'membership_covered' on a gated (review_bookings) lesson is coerced to at_court and CHARGED on
    accept, never an R0 'paid' lesson."""
    print("\n# Settlement guards: crafted 'free' refused; membership_covered lesson can't be R0")
    rfree = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                             booking_type="court", resource_id=fx.courts[0],
                             starts_at=iso(at(fx, 8)), ends_at=iso(at(fx, 9)), settlement_mode="free")
    check("H3: member 'free' court booking refused (SETTLEMENT_NOT_ALLOWED)",
          (not rfree.get("ok")) and rfree.get("error") == "SETTLEMENT_NOT_ALLOWED", str(rfree))
    # Gated coach + a crafted membership_covered lesson.
    s.execute(text("UPDATE iam.coach_profile SET review_bookings=true WHERE club_id=:c AND user_id=:u"),
              {"c": fx.club_id, "u": fx.coach_uid})
    rgate = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                             booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                             starts_at=iso(at(fx, 9)), ends_at=iso(at(fx, 10)),
                             settlement_mode="membership_covered")
    check("H4: gated lesson created as 'requested'",
          rgate.get("ok") and rgate["booking"]["status"] == "requested",
          str(rgate.get("booking", {}).get("status")))
    bid = rgate["booking"]["id"]
    stored = s.execute(text("SELECT settlement_mode FROM diary.booking WHERE id=:b"), {"b": bid}).scalar()
    check("H4: crafted membership_covered coerced to at_court on the requested row",
          stored == "at_court", str(stored))
    acc = B.accept_booking(s, club_id=fx.club_id, booking_id=bid, actor_user_id=fx.coach_uid, role="coach")
    check("H4: coach accept confirms the lesson",
          acc.get("ok") and acc["booking"]["status"] == "confirmed", str(acc))
    oid = B._booking_dict(s, bid)["order_id"]
    check("H4: accepted lesson is CHARGED (R400), not R0",
          _order(s, oid) and _order(s, oid)["amount_minor"] == 40000,
          str(_order(s, oid) and _order(s, oid)["amount_minor"]))


def sc_online_only(s, fx):
    """M1: an ONLINE-ONLY coach can't be booked owed by a client. A crafted at_court is refused; an
    online request to a gated (review) coach stays 'online', and the coach's accept keeps it HELD
    (order awaiting_payment — client prepays), never confirmed+owed."""
    print("\n# Online-only coach: client must prepay; gated accept stays held; at_court refused")
    s.execute(text("UPDATE billing.product SET payment_modes='online' WHERE id=:p"),
              {"p": fx.lesson_product})
    s.execute(text("UPDATE iam.coach_profile SET review_bookings=true WHERE club_id=:c AND user_id=:u"),
              {"c": fx.club_id, "u": fx.coach_uid})
    rbad = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                            booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                            starts_at=iso(at(fx, 8)), ends_at=iso(at(fx, 9)), settlement_mode="at_court")
    check("M1: at_court refused for an online-only coach (gated path)",
          (not rbad.get("ok")) and rbad.get("error") == "SETTLEMENT_NOT_ALLOWED", str(rbad))
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                         starts_at=iso(at(fx, 9)), ends_at=iso(at(fx, 10)), settlement_mode="online")
    stored = r.get("ok") and s.execute(text("SELECT settlement_mode FROM diary.booking WHERE id=:b"),
                                       {"b": r["booking"]["id"]}).scalar()
    check("M1: online request preserved (not coerced to at_court)", stored == "online", str(stored))
    bid = r["booking"]["id"]
    acc = B.accept_booking(s, club_id=fx.club_id, booking_id=bid, actor_user_id=fx.coach_uid, role="coach")
    check("M1: accept keeps the online lesson HELD (awaiting prepayment), not confirmed",
          acc.get("ok") and acc["booking"]["status"] == "held",
          str(acc.get("booking", {}).get("status")))
    oid = B._booking_dict(s, bid)["order_id"]
    check("M1: order is awaiting_payment (client must pay), not open/owed",
          _order(s, oid) and _order(s, oid)["status"] == "awaiting_payment",
          str(_order(s, oid) and _order(s, oid)["status"]))


def sc_offplatform_reconcile(s, fx):
    """H5: a lesson the coach collects OFF-platform (arrears_commission) must show in the coach's OWN
    statement 'paid' — not vanish while the owner/client still see it. Book at-court (owed), accrue,
    mark collected off-platform, and assert the coach statement flips owed -> paid."""
    print("\n# Off-platform collection reconciles: coach statement includes arrears_commission (H5)")
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                         starts_at=iso(at(fx, 8)), ends_at=iso(at(fx, 9)), settlement_mode="at_court")
    bid = r["booking"]["id"]
    CM.accrue_arrears_for_club(s, club_id=fx.club_id)
    st0 = CM.coach_statement(s, club_id=fx.club_id, coach_user_id=fx.coach_uid)
    check("off-platform: owed R400 before collection",
          st0["totals"]["owed_minor"] == 40000 and st0["totals"]["paid_minor"] == 0, str(st0["totals"]))
    arr = s.execute(text("SELECT id FROM billing.coach_arrears WHERE booking_id=:b AND status='owed'"),
                    {"b": bid}).scalar()
    CM.mark_arrears_collected(s, club_id=fx.club_id, arrears_id=arr, coach_user_id=fx.coach_uid)
    st1 = CM.coach_statement(s, club_id=fx.club_id, coach_user_id=fx.coach_uid)
    check("H5: off-platform-collected lesson shows in the coach's PAID (didn't vanish)",
          st1["totals"]["paid_minor"] > 0, str(st1["totals"]))
    check("H5: no longer owed after off-platform collection",
          st1["totals"]["owed_minor"] == 0, str(st1["totals"]))


def sc_onbehalf_token(s, fx):
    """On-behalf auto-draw: when a coach books their client and the client has a prepaid pack WITH
    that coach, it DRAWS the client's pack (R0 token order) — never a new owed charge/financial entry.
    (Robert's pack with Allon is drawn when Allon books Robert.)"""
    print("\n# On-behalf token: coach books their client on the client's OWN pack (draw, not a new charge)")
    plan = BN.create_plan(s, club_id=fx.club_id, service_kind="lesson", sessions_count=10,
                          price_minor=300000, duration_minutes=60, coach_user_id=fx.coach_uid, label="10 lessons")
    order = BN.create_bundle_order(s, club_id=fx.club_id, user_id=fx.member, bundle_plan_id=plan["id"])
    oid = order["order_id"]
    apply_payment_event(NormalizedPaymentEvent(provider="yoco", kind="charge_succeeded", order_ref=oid,
        provider_payment_id="p_pack_ob", amount_minor=300000, currency="ZAR", status="succeeded",
        direction="charge", club_id=str(fx.club_id), user_id=str(fx.member), raw={"t": 9}), session=s)
    BN.activate_wallet_for_order(s, order_id=oid)
    before = s.execute(text("SELECT minutes_remaining FROM billing.token_wallet WHERE order_id=:o"), {"o": str(oid)}).scalar()
    # The COACH books the CLIENT on-behalf (settlement_mode=token, booked_for=client) → draws THEIR pack.
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.coach_uid, role="coach",
                         booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                         starts_at=iso(at(fx, 9)), ends_at=iso(at(fx, 10)),
                         settlement_mode="token", booked_for_user_id=fx.member)
    check("on-behalf token lesson booked", r.get("ok"), str(r))
    after = s.execute(text("SELECT minutes_remaining FROM billing.token_wallet WHERE order_id=:o"), {"o": str(oid)}).scalar()
    check("client's own pack drawn 60 min (not a new charge)", (before - after) == 60, f"{before}->{after}")
    bord = _order(s, r["booking"]["order_id"])
    check("on-behalf token booking is R0/paid — NO new owed financial entry",
          bord and bord["amount_minor"] == 0 and bord["status"] == "paid", str(bord))
    # The coach's 'clients with packages' view lists the client + remaining balance.
    from coach import repositories as CR
    holders = CR.coach_package_holders(s, club_id=fx.club_id, coach_user_id=fx.coach_uid)
    check("coach 'clients with packages' lists the pack holder",
          any(str(h.get("client_user_id")) == str(fx.member) for h in holders), str(holders))


def sc_service_selection(s, fx):
    """A coach with MULTIPLE lesson services (Private / Semi-private) prices the CHOSEN one — not a
    merge of both. services_for lists each product separately; booking with product_id charges it."""
    print("\n# Service selection: a coach's multiple lesson services price independently (product_id)")
    priv = s.execute(text("INSERT INTO billing.product (club_id, kind, name, coach_user_id) "
                          "VALUES (:c,'lesson','Private',:u) RETURNING id"),
                     {"c": fx.club_id, "u": fx.coach_uid}).scalar_one()
    _price(s, fx.club_id, priv, 40000, dur=60)
    semi = s.execute(text("INSERT INTO billing.product (club_id, kind, name, coach_user_id) "
                          "VALUES (:c,'lesson','Semi-private',:u) RETURNING id"),
                     {"c": fx.club_id, "u": fx.coach_uid}).scalar_one()
    _price(s, fx.club_id, semi, 25000, dur=60)
    names = sorted([sv["name"] for sv in PR.services_for(s, club_id=fx.club_id, kind="lesson", coach_user_id=fx.coach_uid)])
    check("services_for lists BOTH of the coach's services", names == ["Private", "Semi-private"], str(names))
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                         starts_at=iso(at(fx, 8)), ends_at=iso(at(fx, 9)),
                         settlement_mode="at_court", product_id=semi)
    check("booking the Semi-private service charges ITS R250 (not the merge/other service)",
          _order(s, r["booking"]["order_id"])["amount_minor"] == 25000,
          str(_order(s, r["booking"]["order_id"])["amount_minor"]))
    r2 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                          booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                          starts_at=iso(at(fx, 10)), ends_at=iso(at(fx, 11)),
                          settlement_mode="at_court", product_id=priv)
    check("booking the Private service charges ITS R400",
          _order(s, r2["booking"]["order_id"])["amount_minor"] == 40000,
          str(_order(s, r2["booking"]["order_id"])["amount_minor"]))


def sc_cancel_fee_and_paid_resize(s, fx):
    """M6: a late cancel raises a REAL fee order (not just an email). M7: a PAID booking can't be
    stretched into a longer/pricier slot (cancel & rebook)."""
    print("\n# Late-cancel fee billed (M6) + paid booking can't be extended (M7)")
    s.execute(text("UPDATE club.policy SET no_show_fee_minor=5000, cancellation_cutoff_hours=100 WHERE club_id=:c"),
              {"c": fx.club_id})
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         starts_at=iso(at(fx, 9)), ends_at=iso(at(fx, 10)), settlement_mode="at_court")
    cres = B.cancel_booking(s, club_id=fx.club_id, booking_id=r["booking"]["id"], actor_user_id=fx.member, role="member")
    check("cancel inside cutoff flags a R50 fee", cres.get("fee_applied") and cres.get("fee_minor") == 5000, str(cres))
    fee = s.execute(text("SELECT o.amount_minor FROM billing.\"order\" o JOIN billing.order_line ol ON ol.order_id=o.id "
                         "WHERE o.club_id=:c AND o.user_id=:u AND ol.description='Late cancellation fee'"),
                    {"c": fx.club_id, "u": fx.member}).scalar()
    check("M6: a R50 late-cancel fee ORDER is raised (billed, not just emailed)", fee == 5000, str(fee))
    # M7: pay a 60-min court online, then (as admin, bypassing cutoff) try to extend to 90 min → refused.
    r2 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.member, role="member",
                          booking_type="court", resource_id=fx.courts[1],
                          starts_at=iso(at(fx, 12)), ends_at=iso(at(fx, 13)), settlement_mode="online")
    oid = r2["booking"]["order_id"]
    apply_payment_event(NormalizedPaymentEvent(provider="yoco", kind="charge_succeeded", order_ref=oid,
        provider_payment_id="p_m7", amount_minor=15000, currency="ZAR", status="succeeded", direction="charge",
        club_id=str(fx.club_id), user_id=str(fx.member), raw={"t": 11}), session=s)
    rr = B.reschedule_booking(s, club_id=fx.club_id, booking_id=r2["booking"]["id"],
                              new_starts_at=iso(at(fx, 14)), new_ends_at=iso(at(fx, 15, 30)),
                              actor_user_id=fx.member, role="club_admin")
    check("M7: a PAID booking can't be extended to a longer slot", (not rr.get("ok")) and rr.get("error") == "PAID_CANNOT_EXTEND", str(rr))


SCENARIOS = [
    sc_coach_scoped_pricing,
    sc_service_selection,
    sc_cancel_fee_and_paid_resize,
    sc_settlement_guards,
    sc_online_only,
    sc_offplatform_reconcile,
    sc_onbehalf_token,
    sc_payment_preference,
    sc_person_360,
    sc_admin_event_story,
    sc_court_utilisation,
    sc_sales_by_day,
    sc_settlement_at_court,
    sc_settlement_online,
    sc_settlement_monthly,
    sc_commission,
    sc_tokens,
    sc_membership,
    sc_membership_purchase,
    sc_refund_request,
    sc_refund_clawback,
    sc_membership_cancel_voids_order,
    sc_dispute_routing,
    sc_client_by_service,
    sc_client_month_end,
    sc_lockstep_desk_pay,
    sc_void_clears_arrears,
    sc_settlement_refund_clawback,
    sc_abandoned_reclaim_on_read,
    sc_booking_story,
    sc_cancel_voids_order,
    sc_phantom_cleanup,
    sc_coach_event_story,
    sc_transaction_log,
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

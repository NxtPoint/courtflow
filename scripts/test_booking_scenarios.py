# scripts/test_booking_scenarios.py — end-to-end booking-engine scenario harness.
#
# Drives the REAL diary logic (diary.bookings / diary.classes / diary.availability) against a
# throwaway, self-contained scratch club built inside ONE transaction that is ALWAYS rolled back
# at the end — so it never persists and never pollutes the sandbox seed. It is the bulk way to
# validate booking behaviour (court / lesson coach∩court / class / lesson-approval lifecycle) and
# the cancel/amend RELEASE invariants, instead of clicking each path by hand.
#
#   Run:  python -m scripts.test_booking_scenarios          (needs DATABASE_URL = the sandbox)
#   Gate: exits non-zero if any scenario fails.
#
# Each scenario asserts an INVARIANT (not just "no error"): a cancel frees BOTH the coach and the
# court, a coach running a class can't be booked for a lesson, a double-book loses, etc. Add a new
# scenario by appending a function and listing it in SCENARIOS.

import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from zoneinfo import ZoneInfo

from db import get_engine
from diary import bookings as B
from diary import classes as C
from diary import availability as A
from diary import pricing as P

JHB = ZoneInfo("Africa/Johannesburg")

# ---------------------------------------------------------------------------
# tiny assert framework
# ---------------------------------------------------------------------------
_RESULTS = []


def check(name, cond, detail=""):
    _RESULTS.append((name, bool(cond), detail))
    mark = "PASS" if cond else "FAIL"
    line = f"  [{mark}] {name}"
    if detail and not cond:
        line += f"  — {detail}"
    print(line)
    return bool(cond)


# ---------------------------------------------------------------------------
# scratch fixtures (all inside the caller's open transaction)
# ---------------------------------------------------------------------------

class Fx:
    """Holds the ids the scenarios reference."""
    club_id = None
    courts = []          # [court_resource_id, ...]
    coach_res = None     # diary.resource(kind=coach) id
    coach_uid = None     # iam.user id of the coach
    members = []         # [iam.user id, ...]
    class_res = None     # diary.resource(kind=class) id
    target = None        # date of the test day (a few days out, within the window)


def _mk_user(s, email, first):
    return s.execute(
        text('INSERT INTO iam."user" (email, first_name) VALUES (:e, :f) RETURNING id'),
        {"e": email, "f": first},
    ).scalar_one()


def _mk_dependent(s, club_id, guardian_user_id, first):
    """A login-less child (iam.user with NULL email) + the guardian link — so a parent's kids can be
    booked/billed. Mirrors iam.create_dependent's shape without the login machinery."""
    du = s.execute(
        text('INSERT INTO iam."user" (first_name) VALUES (:f) RETURNING id'), {"f": first},
    ).scalar_one()
    s.execute(
        text("INSERT INTO iam.dependent (club_id, guardian_user_id, dependent_user_id, first_name, is_active) "
             "VALUES (:c, :g, :d, :f, true)"),
        {"c": club_id, "g": guardian_user_id, "d": du, "f": first},
    )
    return str(du)


def setup(s):
    fx = Fx()
    fx.club_id = s.execute(
        text("INSERT INTO club.club (slug, name) VALUES (:s, :n) RETURNING id"),
        {"s": "scratch-" + datetime.now(timezone.utc).strftime("%H%M%S%f"),
         "n": "Scratch Tennis"},
    ).scalar_one()
    # Generous window so a few-days-out test day is always bookable.
    s.execute(
        text("INSERT INTO club.policy (club_id, booking_window_days, min_booking_minutes, "
             "cancellation_cutoff_hours, allow_pay_at_court, allow_online_payment) "
             "VALUES (:c, 60, 60, 0, true, true)"),
        {"c": fx.club_id},
    )
    # The coach (user + resource + profile, review OFF by default).
    fx.coach_uid = _mk_user(s, "coach@scratch.test", "Coach")
    s.execute(
        text("INSERT INTO iam.coach_profile (club_id, user_id, display_name, is_bookable) "
             "VALUES (:c, :u, 'Coach Scratch', true)"),
        {"c": fx.club_id, "u": fx.coach_uid},
    )
    fx.coach_res = s.execute(
        text("INSERT INTO diary.resource (club_id, kind, name, coach_user_id) "
             "VALUES (:c, 'coach', 'Coach Scratch', :u) RETURNING id"),
        {"c": fx.club_id, "u": fx.coach_uid},
    ).scalar_one()
    # Two courts.
    for i in (1, 2):
        cid = s.execute(
            text("INSERT INTO diary.resource (club_id, kind, name, surface, rank) "
                 "VALUES (:c, 'court', :n, 'hard', :r) RETURNING id"),
            {"c": fx.club_id, "n": f"Court {i}", "r": i},
        ).scalar_one()
        fx.courts.append(cid)
    # Default PAYG prices so the fixture's court + lesson services are BILLABLE (a realistic club
    # prices its services; A5 refuses an unpriced billable booking). Court R150/60min on the club's
    # default court product (courts carry product_id=NULL → resolve to this); lesson R400/60min on a
    # shared (coach-agnostic) lesson product. Duration ranking makes any booked length resolve here.
    court_prod = s.execute(
        text("INSERT INTO billing.product (club_id, kind, name, active) "
             "VALUES (:c, 'court_booking', 'Court Hire', true) RETURNING id"),
        {"c": fx.club_id},
    ).scalar_one()
    fx.court_product = court_prod      # the DEFAULT court service (courts resolve here)
    s.execute(
        text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
             "currency_code, duration_minutes, active) "
             "VALUES (:c, :p, 'any', 15000, 'ZAR', 60, true)"),
        {"c": fx.club_id, "p": court_prod},
    )
    lesson_prod = s.execute(
        text("INSERT INTO billing.product (club_id, kind, name, active) "
             "VALUES (:c, 'lesson', 'Private lesson', true) RETURNING id"),
        {"c": fx.club_id},
    ).scalar_one()
    s.execute(
        text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
             "currency_code, duration_minutes, active) "
             "VALUES (:c, :p, 'any', 40000, 'ZAR', 60, true)"),
        {"c": fx.club_id, "p": lesson_prod},
    )
    # Members.
    for i in (1, 2, 3):
        fx.members.append(_mk_user(s, f"member{i}@scratch.test", f"Member{i}"))
        s.execute(
            text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
                 "VALUES (:c, :u, 'member', 'active')"),
            {"c": fx.club_id, "u": fx.members[-1]},
        )

    # The test day: 3 days out. Identical 08:00–18:00 / 60-min grid on coach + both courts so the
    # coach∩court slot keys align.
    fx.target = (datetime.now(JHB) + timedelta(days=3)).date()
    wd = fx.target.weekday()
    for rid in [fx.coach_res] + fx.courts:
        s.execute(
            text("INSERT INTO diary.availability_rule "
                 "(club_id, resource_id, weekday, start_time, end_time, slot_minutes) "
                 "VALUES (:c, :r, :wd, '08:00', '18:00', 60)"),
            {"c": fx.club_id, "r": rid, "wd": wd},
        )
    # A class type taught by the coach (capacity 2 for the waitlist test).
    res = C.create_class_type(s, club_id=fx.club_id, name="Cardio Tennis", capacity=2,
                              price_amount_minor=12000, duration_minutes=90,
                              coach_user_id=fx.coach_uid)
    fx.class_res = res["class"]["resource_id"]
    return fx


# ---------------------------------------------------------------------------
# time helpers (wall-clock JHB → the values the engine speaks)
# ---------------------------------------------------------------------------

def at(fx, hour, minute=0):
    """A tz-aware JHB datetime on the test day."""
    return datetime(fx.target.year, fx.target.month, fx.target.day, hour, minute, tzinfo=JHB)


def utc_iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def lesson_slots(s, fx, hour_from=8, hour_to=18):
    return A.compute_availability(
        s, club_id=fx.club_id, kind="coach", coach_user_id=fx.coach_uid,
        date_from=utc_iso(at(fx, hour_from)), date_to=utc_iso(at(fx, hour_to)),
        duration_minutes=60, audience="member")


def court_slots(s, fx, resource_id, hour_from=8, hour_to=18):
    return A.compute_availability(
        s, club_id=fx.club_id, resource_id=resource_id, kind="court",
        date_from=utc_iso(at(fx, hour_from)), date_to=utc_iso(at(fx, hour_to)),
        duration_minutes=60, audience="member")


def has_slot(slots, dt):
    target = utc_iso(dt)
    return any(sl["start"] == target for sl in slots)


def _rows_for_order(s, order_id):
    return s.execute(
        text("SELECT resource_id, status FROM diary.booking WHERE order_id = :o"),
        {"o": order_id},
    ).mappings().all()


# ---------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------

def sc_court_book_cancel(s, fx):
    print("\n# Court: book → busy → cancel → free")
    m = fx.members[0]; court = fx.courts[0]
    start, end = at(fx, 9), at(fx, 10)
    check("court slot free before booking", has_slot(court_slots(s, fx, court), start))
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                         booking_type="court", resource_id=court,
                         starts_at=utc_iso(start), ends_at=utc_iso(end))
    ok = r.get("ok") and r["booking"]["status"] == "confirmed"
    check("court booking confirmed", ok, str(r))
    check("court slot gone after booking", not has_slot(court_slots(s, fx, court), start))
    # double-book the same slot → SLOT_TAKEN
    r2 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[1], role="member",
                          booking_type="court", resource_id=court,
                          starts_at=utc_iso(start), ends_at=utc_iso(end))
    check("double-book refused (SLOT_TAKEN)", r2.get("error") == "SLOT_TAKEN", str(r2))
    # cancel → slot free again
    B.cancel_booking(s, club_id=fx.club_id, booking_id=r["booking"]["id"],
                     actor_user_id=m, role="member")
    check("court slot free after cancel", has_slot(court_slots(s, fx, court), start))


def sc_court_reschedule(s, fx):
    print("\n# Court: reschedule frees old slot, takes new; conflict preserves original")
    m = fx.members[0]; court = fx.courts[0]
    s1, e1 = at(fx, 11), at(fx, 12)
    s2, e2 = at(fx, 13), at(fx, 14)
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                         booking_type="court", resource_id=court,
                         starts_at=utc_iso(s1), ends_at=utc_iso(e1))
    bid = r["booking"]["id"]
    rr = B.reschedule_booking(s, club_id=fx.club_id, booking_id=bid,
                              new_starts_at=utc_iso(s2), new_ends_at=utc_iso(e2),
                              actor_user_id=m, role="member")
    check("reschedule ok", rr.get("ok"), str(rr))
    check("old slot free after move", has_slot(court_slots(s, fx, court), s1))
    check("new slot busy after move", not has_slot(court_slots(s, fx, court), s2))
    # Block 15:00, then try to reschedule onto it → conflict, original (13:00) preserved.
    s3, e3 = at(fx, 15), at(fx, 16)
    B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[1], role="member",
                     booking_type="court", resource_id=court,
                     starts_at=utc_iso(s3), ends_at=utc_iso(e3))
    rc = B.reschedule_booking(s, club_id=fx.club_id, booking_id=bid,
                              new_starts_at=utc_iso(s3), new_ends_at=utc_iso(e3),
                              actor_user_id=m, role="member")
    check("reschedule into taken slot refused", rc.get("error") == "SLOT_TAKEN", str(rc))
    still = B.get_booking(s, club_id=fx.club_id, booking_id=bid)
    check("original time preserved after failed reschedule",
          still["starts_at"] == utc_iso(s2), still["starts_at"])


def sc_reschedule_court_move(s, fx):
    """Clients + coaches kept asking to MOVE COURTS without cancelling. A reschedule can now carry a
    court: a court booking's own resource changes; a lesson stays on the coach and its auto-held court
    row moves instead. A busy target is refused up front with a precise error, not a bare SLOT_TAKEN."""
    print("\n# Reschedule can also change the COURT (court booking + lesson's held court)")
    m = fx.members[0]
    c0, c1 = fx.courts[0], fx.courts[1]
    s1, e1 = at(fx, 9), at(fx, 10)
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                         booking_type="court", resource_id=c0,
                         starts_at=utc_iso(s1), ends_at=utc_iso(e1))
    bid = r["booking"]["id"]

    # Same time, different court — a pure court swap.
    rr = B.reschedule_booking(s, club_id=fx.club_id, booking_id=bid,
                              new_starts_at=utc_iso(s1), new_ends_at=utc_iso(e1),
                              actor_user_id=m, role="member", new_court_resource_id=c1)
    check("court swap at the same time is accepted", rr.get("ok"), str(rr))
    moved = B.get_booking(s, club_id=fx.club_id, booking_id=bid)
    check("the booking now sits on the NEW court", str(moved["resource_id"]) == str(c1),
          str(moved["resource_id"]))
    check("the old court is free again", has_slot(court_slots(s, fx, c0), s1))

    # A court that's already taken at that time is refused with a precise error.
    B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[1], role="member",
                     booking_type="court", resource_id=c0,
                     starts_at=utc_iso(s1), ends_at=utc_iso(e1))
    busy = B.reschedule_booking(s, club_id=fx.club_id, booking_id=bid,
                                new_starts_at=utc_iso(s1), new_ends_at=utc_iso(e1),
                                actor_user_id=m, role="member", new_court_resource_id=c0)
    check("moving onto a BUSY court is refused (COURT_NOT_AVAILABLE)",
          busy.get("error") == "COURT_NOT_AVAILABLE", str(busy))
    check("the refused move left it on its court",
          str(B.get_booking(s, club_id=fx.club_id, booking_id=bid)["resource_id"]) == str(c1))

    # A no-op "move" to the court it's already on must not trip the busy-check against ITSELF.
    same = B.reschedule_booking(s, club_id=fx.club_id, booking_id=bid,
                                new_starts_at=utc_iso(at(fx, 16)), new_ends_at=utc_iso(at(fx, 17)),
                                actor_user_id=m, role="member", new_court_resource_id=c1)
    check("re-selecting the SAME court doesn't block itself", same.get("ok"), str(same))

    # A LESSON sits on the coach; its auto-held COURT row is what moves.
    lr = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                          booking_type="lesson", resource_id=fx.coach_res,
                          coach_user_id=fx.coach_uid,
                          starts_at=utc_iso(at(fx, 11)), ends_at=utc_iso(at(fx, 12)))
    lid = lr["booking"]["id"]
    lrr = B.reschedule_booking(s, club_id=fx.club_id, booking_id=lid,
                               new_starts_at=utc_iso(at(fx, 11)), new_ends_at=utc_iso(at(fx, 12)),
                               actor_user_id=m, role="member", new_court_resource_id=c1)
    check("a lesson accepts a court move", lrr.get("ok"), str(lrr))
    lesson = B.get_booking(s, club_id=fx.club_id, booking_id=lid)
    check("the lesson itself still sits on the COACH (not the court)",
          str(lesson["resource_id"]) == str(fx.coach_res), str(lesson["resource_id"]))
    held = s.execute(text("SELECT resource_id FROM diary.booking WHERE club_id=:c AND order_id=:o "
                          "AND booking_type='court' AND id<>:id"),
                     {"c": fx.club_id, "o": lesson["order_id"], "id": lid}).scalar()
    check("the lesson's HELD COURT moved to the chosen court", str(held) == str(c1), str(held))


def sc_expired_hold_voids_order(s, fx):
    """An abandoned online checkout left its order behind: lazy expiry cancelled the booking but
    never touched the order, leaving an 'awaiting_payment' row pointing at a cancelled booking (37 in
    production). It bills nobody, but it pollutes every money read — and the statement self-heal only
    rescues 'open' orders, so these never cleared."""
    print("\n# Expired hold VOIDS its abandoned order (no phantom awaiting_payment left behind)")
    m = fx.members[0]
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         starts_at=utc_iso(at(fx, 9)), ends_at=utc_iso(at(fx, 10)),
                         settlement_mode="online")
    bid = r["booking"]["id"]
    oid = r["booking"]["order_id"]
    check("an online booking starts held + awaiting_payment",
          r["booking"]["status"] == "held" and bool(oid), str(r["booking"]["status"]))

    # Abandon it: force the hold to lapse, then let any read trigger lazy expiry.
    s.execute(text("UPDATE diary.booking SET held_until = now() - interval '1 minute' WHERE id=:b"),
              {"b": bid})
    B.release_expired_holds(s, fx.club_id)
    bk = B.get_booking(s, club_id=fx.club_id, booking_id=bid)
    check("the lapsed booking is cancelled", bk["status"] == "cancelled", bk["status"])
    ost = s.execute(text('SELECT status FROM billing."order" WHERE id=:o'), {"o": oid}).scalar()
    check("...and its abandoned order is VOIDED, not left awaiting_payment",
          ost in ("void", "written_off"), str(ost))

    # A LESSON carries two rows on one order (coach + auto-held court). The order must only be voided
    # once BOTH are dead — never while one is still live, or a real debt would be erased.
    r2 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                          booking_type="lesson", resource_id=fx.coach_res,
                          coach_user_id=fx.coach_uid,
                          starts_at=utc_iso(at(fx, 11)), ends_at=utc_iso(at(fx, 12)),
                          settlement_mode="at_court")
    oid2 = r2["booking"]["order_id"]
    n_rows = s.execute(text("SELECT count(*) FROM diary.booking WHERE club_id=:c AND order_id=:o"),
                       {"c": fx.club_id, "o": oid2}).scalar()
    check("the lesson holds 2 rows on one order (coach + court)", n_rows == 2, str(n_rows))
    B.release_expired_holds(s, fx.club_id)     # nothing lapsed — must not touch a live order
    ost2 = s.execute(text('SELECT status FROM billing."order" WHERE id=:o'), {"o": oid2}).scalar()
    check("a LIVE order is never voided by the sweep", ost2 not in ("void", "written_off"), str(ost2))


def sc_booking_type_must_match_resource(s, fx):
    """`booking_type` came off the request body, and the resource-kind check lived only inside the
    LESSON branch while the court-service guard lived only inside the COURT branch. 'class' is legal
    in the schema CHECK (that is how a class GiST-reserves its court), so POSTing a COURT resource as
    a 'class' skipped the court block entirely — cheapest class rate, class payment rules (usually
    none), a class pack drawn for a court, and worst of all a court genuinely GiST-blocked but
    INVISIBLE to staff, because the master feed excludes booking_type='class' and there is no
    class_session behind a crafted row."""
    print("\n# booking_type must match the resource, and 'class' is not bookable via this route")
    m = fx.members[0]

    ghost = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                             booking_type="class", resource_id=fx.courts[0],
                             starts_at=utc_iso(at(fx, 9)), ends_at=utc_iso(at(fx, 10)),
                             settlement_mode="at_court")
    check("a COURT posted as a 'class' is refused",
          ghost.get("error") == "BOOKING_TYPE_NOT_ALLOWED", str(ghost))
    blocked = s.execute(text("SELECT count(*) FROM diary.booking WHERE club_id=:c "
                             "AND resource_id=:r AND booking_type='class'"),
                        {"c": fx.club_id, "r": fx.courts[0]}).scalar()
    check("...and no invisible court hold was created", blocked == 0, str(blocked))

    # A COACH resource booked as a 'court', and a COURT booked as a 'lesson' — both nonsense.
    check("a COACH resource booked as a 'court' is refused",
          B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                           booking_type="court", resource_id=fx.coach_res,
                           starts_at=utc_iso(at(fx, 9)), ends_at=utc_iso(at(fx, 10)),
                           settlement_mode="at_court").get("error") == "RESOURCE_KIND_MISMATCH")
    check("a COURT resource booked as a 'lesson' is refused",
          B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                           booking_type="lesson", resource_id=fx.courts[0],
                           coach_user_id=fx.coach_uid,
                           starts_at=utc_iso(at(fx, 9)), ends_at=utc_iso(at(fx, 10)),
                           settlement_mode="at_court").get("error") == "RESOURCE_KIND_MISMATCH")

    # The legitimate paths are untouched.
    check("a real COURT booking still works",
          B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                           booking_type="court", resource_id=fx.courts[0],
                           starts_at=utc_iso(at(fx, 11)), ends_at=utc_iso(at(fx, 12)),
                           settlement_mode="at_court").get("ok") is True)
    check("a real LESSON booking still works",
          B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                           booking_type="lesson", resource_id=fx.coach_res,
                           coach_user_id=fx.coach_uid,
                           starts_at=utc_iso(at(fx, 13)), ends_at=utc_iso(at(fx, 14)),
                           settlement_mode="at_court").get("ok") is True)
    # And a real CLASS still reserves its court — that path inserts directly, never via this route.
    C.schedule_sessions(s, club_id=fx.club_id, resource_id=fx.class_res,
                        dates=[fx.target.isoformat()], start_time="16:00",
                        duration_minutes=60, capacity=4)
    sess = s.execute(text("SELECT count(*) FROM diary.class_session WHERE club_id=:c "
                          "AND resource_id=:r AND starts_at=:sa"),
                     {"c": fx.club_id, "r": fx.class_res, "sa": at(fx, 16)}).scalar()
    check("REAL class scheduling is unaffected (it never used this route)", sess == 1, str(sess))


def sc_posted_service_must_be_real(s, fx):
    """`product_id` arrives straight off the request body and was validated ONLY on the court branch.
    For a LESSON it then drove the payment gate, the price guard AND the order price — and
    price_for's product branch has no kind/coach/active predicate, falling through to
    `amount_minor ASC LIMIT 1`. Posting another service's id therefore billed the cheapest price in
    the club, evaluated the card-only rule against the substituted service, and (if the id named a
    COURT product) made commission classify a delivered lesson as court, so the coach earned nothing.
    Service ids are public to any authenticated member via GET /api/diary/services."""
    print("\n# A posted product_id must be a REAL service of this kind, for THIS coach")
    m = fx.members[0]

    # A cheap CLASS service — the classic substitution target for a lesson booking.
    cheap_class = s.execute(text("INSERT INTO billing.product (club_id, kind, name) "
                                 "VALUES (:c,'class','Cheap Class') RETURNING id"),
                            {"c": fx.club_id}).scalar()
    s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                   "currency_code, unit, duration_minutes, active, status) "
                   "VALUES (:c,:p,'any',1200,'ZAR','per_booking',60,true,'active')"),
              {"c": fx.club_id, "p": cheap_class})

    def _book(pid):
        return B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                                booking_type="lesson", resource_id=fx.coach_res,
                                coach_user_id=fx.coach_uid, product_id=pid,
                                starts_at=utc_iso(at(fx, 9)), ends_at=utc_iso(at(fx, 10)),
                                settlement_mode="at_court")

    r1 = _book(str(cheap_class))
    check("a CLASS product posted on a LESSON booking is refused",
          r1.get("error") == "SERVICE_NOT_VALID", str(r1))

    # A COURT product — the worst case: commission would classify the lesson as court and pay the
    # coach nothing on a lesson they actually delivered.
    court_prod = s.execute(text("SELECT id FROM billing.product WHERE club_id=:c "
                                "AND kind='court_booking' AND active=true LIMIT 1"),
                           {"c": fx.club_id}).scalar()
    check("a COURT product posted on a LESSON booking is refused",
          _book(str(court_prod)).get("error") == "SERVICE_NOT_VALID", "commission would be lost")

    # ANOTHER coach's lesson service — right kind, wrong rate card.
    other = _mk_user(s, "svc_thief_coach@scratch.test", "Thief")
    s.execute(text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
                   "VALUES (:c,:u,'coach','active')"), {"c": fx.club_id, "u": other})
    s.execute(text("INSERT INTO iam.coach_profile (club_id, user_id, display_name, is_bookable) "
                   "VALUES (:c,:u,'Thief',true)"), {"c": fx.club_id, "u": other})
    other_svc = s.execute(text("INSERT INTO billing.product (club_id, kind, name, coach_user_id) "
                               "VALUES (:c,'lesson','Other Rate Card',:u) RETURNING id"),
                          {"c": fx.club_id, "u": other}).scalar()
    s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                   "currency_code, unit, duration_minutes, active, status) "
                   "VALUES (:c,:p,'any',500,'ZAR','per_booking',60,true,'active')"),
              {"c": fx.club_id, "p": other_svc})
    check("ANOTHER coach's lesson service is refused",
          _book(str(other_svc)).get("error") == "SERVICE_NOT_VALID", "would price off their rate card")

    # A DEACTIVATED service must not be bookable either (its payment rule silently disappears —
    # payment_modes_for requires active=true while price_for did not).
    dead = s.execute(text("INSERT INTO billing.product (club_id, kind, name, coach_user_id, active) "
                          "VALUES (:c,'lesson','Retired Svc',:u,false) RETURNING id"),
                     {"c": fx.club_id, "u": fx.coach_uid}).scalar()
    s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                   "currency_code, unit, duration_minutes, active, status) "
                   "VALUES (:c,:p,'any',900,'ZAR','per_booking',60,true,'active')"),
              {"c": fx.club_id, "p": dead})
    check("a DEACTIVATED service is refused", _book(str(dead)).get("error") == "SERVICE_NOT_VALID",
          str(_book(str(dead))))

    # And the legitimate path is untouched: the coach's OWN active lesson service still books.
    good = s.execute(text("SELECT id FROM billing.product WHERE club_id=:c AND kind='lesson' "
                          "AND active=true AND (coach_user_id IS NULL OR coach_user_id=:u) LIMIT 1"),
                     {"c": fx.club_id, "u": fx.coach_uid}).scalar()
    ok = _book(str(good))
    check("the coach's OWN service still books normally", ok.get("ok") is True, str(ok))


def sc_gated_lesson_bills_the_booked_service(s, fx):
    """A review-gated lesson used to be billed the coach's CHEAPEST service. A 'requested' booking
    creates NO order, and diary.booking had nowhere to remember which service was chosen — so on
    accept, pricing fell back to price_for(kind='lesson', coach), whose tie-break is
    `amount_minor ASC LIMIT 1`. A R400 Private billed R250 if that coach also sold a Semi-private,
    with commission accruing on the wrong base and the sale attributed to the wrong service."""
    print("\n# A gated lesson is billed the service the client BOOKED, not the coach's cheapest")
    m = fx.members[0]
    # The coach's EXPENSIVE service (the one actually booked)…
    dear = s.execute(text("INSERT INTO billing.product (club_id, kind, name, coach_user_id) "
                          "VALUES (:c,'lesson','Private (dear)',:u) RETURNING id"),
                     {"c": fx.club_id, "u": fx.coach_uid}).scalar()
    s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                   "currency_code, unit, duration_minutes, active, status) "
                   "VALUES (:c,:p,'any',90000,'ZAR','per_booking',60,true,'active')"),
              {"c": fx.club_id, "p": dear})
    # …and a CHEAPER sibling on the same coach + duration, which the fallback would grab.
    cheap = s.execute(text("INSERT INTO billing.product (club_id, kind, name, coach_user_id) "
                           "VALUES (:c,'lesson','Semi-private (cheap)',:u) RETURNING id"),
                      {"c": fx.club_id, "u": fx.coach_uid}).scalar()
    s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                   "currency_code, unit, duration_minutes, active, status) "
                   "VALUES (:c,:p,'any',10000,'ZAR','per_booking',60,true,'active')"),
              {"c": fx.club_id, "p": cheap})
    # Turn the coach's review gate ON — this is the path that loses the service.
    s.execute(text("UPDATE iam.coach_profile SET review_bookings = true "
                   "WHERE club_id=:c AND user_id=:u"), {"c": fx.club_id, "u": fx.coach_uid})

    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                         booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                         product_id=dear,        # the client chose the R900 service
                         starts_at=utc_iso(at(fx, 9)), ends_at=utc_iso(at(fx, 10)),
                         settlement_mode="at_court")
    bid = r["booking"]["id"]
    check("the lesson is booked and priced immediately (one flow, no gate)",
          r["booking"]["status"] == "confirmed" and bool(r["booking"].get("order_id")),
          str(r["booking"]["status"]))
    remembered = s.execute(text("SELECT product_id FROM diary.booking WHERE id=:b"),
                           {"b": bid}).scalar()
    check("...the chosen SERVICE is remembered on the booking",
          str(remembered) == str(dear), str(remembered))
    amt = s.execute(text('SELECT amount_minor FROM billing."order" WHERE id=:o'),
                    {"o": r["booking"]["order_id"]}).scalar()
    check("it is billed the BOOKED service (R900), not the cheapest (R100)", amt == 90000,
          "billed %s" % amt)
    billed_prod = s.execute(text("SELECT pr.product_id FROM billing.order_line ol "
                                 "JOIN billing.price pr ON pr.id = ol.price_id "
                                 "WHERE ol.booking_id = :b LIMIT 1"), {"b": bid}).scalar()
    check("...and attributed to the right service in earnings", str(billed_prod) == str(dear),
          str(billed_prod))

    s.execute(text("UPDATE iam.coach_profile SET review_bookings = false "
                   "WHERE club_id=:c AND user_id=:u"), {"c": fx.club_id, "u": fx.coach_uid})


def sc_member_cannot_bypass_online_only(s, fx):
    """PRODUCTION REPLICA. Three plain members (no coach/admin role) booked an online-only court as
    pay-at-court: lucaaclark, prjshamma262, kbsolr. This mirrors that exact config — a court whose
    resource.product_id points at a court service restricted to 'online' — and drives every route a
    member can reach. If the gate holds here, the live path must have differed (config drift or a
    stale client); if it doesn't, this is the bug."""
    print("\n# A plain MEMBER cannot take an online-only court pay-at-court (prod replica)")
    m = fx.members[0]
    online_only = s.execute(
        text("INSERT INTO billing.product (club_id, kind, name, payment_modes, active) "
             "VALUES (:c,'court_booking','Court Hire - Hard Court','online',true) RETURNING id"),
        {"c": fx.club_id}).scalar()
    s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                   "currency_code, unit, duration_minutes, active, status) "
                   "VALUES (:c,:p,'any',15000,'ZAR','per_booking',60,true,'active')"),
              {"c": fx.club_id, "p": online_only})
    s.execute(text("UPDATE diary.resource SET product_id = :p WHERE id = :r"),
              {"p": online_only, "r": fx.courts[0]})

    def _try(mode, product_id=None, label=""):
        return B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                                booking_type="court", resource_id=fx.courts[0],
                                starts_at=utc_iso(at(fx, 9)), ends_at=utc_iso(at(fx, 10)),
                                settlement_mode=mode, product_id=product_id)

    # (a) EXACTLY what the UI posts: the resolved product_id + the member's chosen mode.
    r1 = _try("at_court", product_id=online_only)
    check("at_court WITH the correct product_id is refused",
          r1.get("error") == "SETTLEMENT_NOT_ALLOWED", str(r1))

    # (b) The client omits product_id entirely (single-service club shape, or an older bundle) —
    #     the server must still resolve the court's own service and enforce it.
    r2 = _try("at_court")
    check("at_court with NO product_id posted is still refused (server resolves the court's service)",
          r2.get("error") == "SETTLEMENT_NOT_ALLOWED", str(r2))

    # (c) month-end is the other money mode those bookings used.
    r3 = _try("monthly_account")
    check("monthly_account is refused too", r3.get("error") == "SETTLEMENT_NOT_ALLOWED", str(r3))

    # (d) The allowed mode must still work — the gate must not block the legitimate path.
    r4 = _try("online", product_id=online_only)
    check("online (the one allowed mode) IS accepted", r4.get("ok") is True, str(r4))

    # (e) STAFF override remains intentional and documented — staff may take a card-only service at
    # the desk. This used to be asserted with a COACH self-booking the court; that path is now
    # refused outright (owner decision 2026-08-08 — a coach books lessons, not club courts in his own
    # name), so the OVERRIDE is asserted through an admin, which is what it was always about. The
    # coach's own refusal is asserted below, by ITS error, so neither rule can silently lapse.
    r5 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="club_admin",
                          booked_for_user_id=m,
                          booking_type="court", resource_id=fx.courts[0],
                          starts_at=utc_iso(at(fx, 13)), ends_at=utc_iso(at(fx, 14)),
                          settlement_mode="at_court")
    check("STAFF may still override a card-only service (by design, BUSINESS-RULES.md)",
          r5.get("ok") is True, str(r5))
    r6 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.coach_uid, role="coach",
                          booking_type="court", resource_id=fx.courts[0],
                          starts_at=utc_iso(at(fx, 15)), ends_at=utc_iso(at(fx, 16)),
                          settlement_mode="at_court")
    check("...but a coach may NOT put a club court in his own name",
          r6.get("error") == "COACH_CANNOT_BOOK_COURT", str(r6))


def sc_expired_void_is_recoverable(s, fx):
    """REGRESSION GUARD for the fix that voids an abandoned order on hold expiry. Voiding was right,
    but reconcile refused ANY order that wasn't awaiting_payment — so a member who paid AFTER their
    hold lapsed, with the webhook missed (Render Free sleeps), had money taken with no booking, no
    receipt and no trace. reconcile must now reach an expired-hold void — and ONLY that: an order an
    admin deliberately voided must stay untouchable, or a cancelled sale could be resurrected."""
    print("\n# An expired-hold VOID stays recoverable by reconcile; a deliberate void does NOT")
    from yoco_billing.reconcile import _is_expired_hold_void
    m = fx.members[0]

    # (1) Abandoned online booking → hold lapses → booking cancelled 'hold_expired', order voided.
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         starts_at=utc_iso(at(fx, 9)), ends_at=utc_iso(at(fx, 10)),
                         settlement_mode="online")
    oid = r["booking"]["order_id"]
    s.execute(text("UPDATE diary.booking SET held_until = now() - interval '1 minute' WHERE id=:b"),
              {"b": r["booking"]["id"]})
    B.release_expired_holds(s, fx.club_id)
    st = s.execute(text('SELECT status FROM billing."order" WHERE id=:o'), {"o": oid}).scalar()
    check("the abandoned order is voided", st in ("void", "written_off"), str(st))
    check("...and reconcile can still REACH it (money may already be with Yoco)",
          _is_expired_hold_void(s, oid) is True, "reconcile would refuse it — the regression")

    # (2) A DELIBERATE void (admin cancels an unpaid booking) must NOT be reconcilable — recovering
    #     it would resurrect a sale the club deliberately cancelled.
    r2 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                          booking_type="court", resource_id=fx.courts[0],
                          starts_at=utc_iso(at(fx, 11)), ends_at=utc_iso(at(fx, 12)),
                          settlement_mode="online")
    oid2 = r2["booking"]["order_id"]
    B.cancel_booking(s, club_id=fx.club_id, booking_id=r2["booking"]["id"],
                     actor_user_id=m, role="member")
    st2 = s.execute(text('SELECT status FROM billing."order" WHERE id=:o'), {"o": oid2}).scalar()
    check("a cancelled booking's order is voided too", st2 in ("void", "written_off"), str(st2))
    check("...but a DELIBERATE void is NOT reconcilable (no hold_expired behind it)",
          _is_expired_hold_void(s, oid2) is False, "reconcile would resurrect a cancelled sale")


def sc_court_move_guards(s, fx):
    """A court move must re-run the guards a TIME move already runs. It originally checked only that
    the target was free, so a member could move a booking onto a court from a DIFFERENT court service
    (keeping the old cheap price AND the old settlement mode — reprice_booking_order re-prices on the
    same product, so it could never correct that), or move a membership-covered R0 booking onto a
    court their membership never covers."""
    print("\n# Court move re-runs the money guards (service change refused, coverage re-checked)")
    m = fx.members[0]
    # Put courts[1] on its OWN court service (a second, pricier one) — the Hardcourt/Clay shape.
    clay = s.execute(text("INSERT INTO billing.product (club_id, kind, name) "
                          "VALUES (:c,'court_booking','Clay Hire') RETURNING id"),
                     {"c": fx.club_id}).scalar()
    s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                   "currency_code, unit, duration_minutes, active, status) "
                   "VALUES (:c,:p,'any',99000,'ZAR','per_booking',60,true,'active')"),
              {"c": fx.club_id, "p": clay})
    s.execute(text("UPDATE diary.resource SET product_id = :p WHERE id = :r"),
              {"p": clay, "r": fx.courts[1]})
    # Allocate the OTHER courts to the default service explicitly — mirroring production, where every
    # court carries an explicit product_id. (With two court products an unallocated court resolves to
    # an AMBIGUOUS service, which the guard now also refuses to move across; covered at the end.)
    for _c in [fx.courts[0]] + (fx.courts[2:3] if len(fx.courts) > 2 else []):
        s.execute(text("UPDATE diary.resource SET product_id = :p WHERE id = :r"),
                  {"p": fx.court_product, "r": _c})

    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         starts_at=utc_iso(at(fx, 9)), ends_at=utc_iso(at(fx, 10)))
    bid = r["booking"]["id"]
    check("a booking on the default court service was created", r.get("ok"), str(r))

    cross = B.reschedule_booking(s, club_id=fx.club_id, booking_id=bid,
                                 new_starts_at=utc_iso(at(fx, 9)), new_ends_at=utc_iso(at(fx, 10)),
                                 actor_user_id=m, role="member", new_court_resource_id=fx.courts[1])
    check("moving onto a DIFFERENT court service is refused",
          cross.get("error") == "COURT_SERVICE_CHANGED", str(cross))
    still = B.get_booking(s, club_id=fx.club_id, booking_id=bid)
    check("...and the booking kept its original court",
          str(still["resource_id"]) == str(fx.courts[0]), str(still["resource_id"]))

    # THE GUARD MUST NOT OVER-BLOCK: a move WITHIN the same service is the common case and must
    # still work. Add a third court on the DEFAULT service to prove it.
    sibling = s.execute(
        text("INSERT INTO diary.resource (club_id, kind, name, surface, rank, product_id) "
             "VALUES (:c,'court','Court Sibling','hard',9,:p) RETURNING id"),
        {"c": fx.club_id, "p": fx.court_product}).scalar()
    s.execute(text("INSERT INTO diary.availability_rule (club_id, resource_id, weekday, "
                   "start_time, end_time, slot_minutes) VALUES (:c,:r,:wd,'08:00','18:00',30)"),
              {"c": fx.club_id, "r": sibling, "wd": fx.target.weekday()})
    same = B.reschedule_booking(s, club_id=fx.club_id, booking_id=bid,
                                new_starts_at=utc_iso(at(fx, 9)), new_ends_at=utc_iso(at(fx, 10)),
                                actor_user_id=m, role="member", new_court_resource_id=sibling)
    check("a move WITHIN the same court service is still allowed", same.get("ok"), str(same))
    check("...and it landed on the sibling court",
          str(B.get_booking(s, club_id=fx.club_id, booking_id=bid)["resource_id"]) == str(sibling))


def sc_coach_preferred_court(s, fx):
    """A coach's preferred court: when a lesson doesn't name a court, hold the coach's usual one if
    it's free (their lessons were scattering across the club), else fall back to any free court so a
    busy favourite can never make a lesson unbookable."""
    print("\n# Coach preferred court: honoured when free, falls back when busy, never blocks a lesson")
    m = fx.members[0]
    pref = fx.courts[1]                      # deliberately NOT courts[0] (the first-free default)
    s.execute(text("UPDATE iam.coach_profile SET preferred_court_resource_id = :p "
                   "WHERE club_id = :c AND user_id = :u"),
              {"p": pref, "c": fx.club_id, "u": fx.coach_uid})

    def held_court_of(bid):
        bk = B.get_booking(s, club_id=fx.club_id, booking_id=bid)
        return s.execute(text("SELECT resource_id FROM diary.booking WHERE club_id=:c AND order_id=:o "
                              "AND booking_type='court' AND id<>:id"),
                         {"c": fx.club_id, "o": bk["order_id"], "id": bid}).scalar()

    r1 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                          booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                          starts_at=utc_iso(at(fx, 9)), ends_at=utc_iso(at(fx, 10)))
    check("lesson booked without naming a court", r1.get("ok"), str(r1))
    check("it landed on the coach's PREFERRED court (not merely the first free one)",
          str(held_court_of(r1["booking"]["id"])) == str(pref), str(held_court_of(r1["booking"]["id"])))

    # Preferred court busy at the new time → fall back rather than refuse the lesson.
    B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[1], role="member",
                     booking_type="court", resource_id=pref,
                     starts_at=utc_iso(at(fx, 13)), ends_at=utc_iso(at(fx, 14)))
    r2 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                          booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                          starts_at=utc_iso(at(fx, 13)), ends_at=utc_iso(at(fx, 14)))
    check("a busy preference never blocks the lesson", r2.get("ok"), str(r2))
    check("it fell back to a different, free court",
          str(held_court_of(r2["booking"]["id"])) != str(pref), str(held_court_of(r2["booking"]["id"])))

    # An EXPLICIT court still wins over the preference (staff override).
    r3 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                          booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                          court_resource_id=fx.courts[0],
                          starts_at=utc_iso(at(fx, 15)), ends_at=utc_iso(at(fx, 16)))
    check("an explicitly chosen court overrides the preference",
          str(held_court_of(r3["booking"]["id"])) == str(fx.courts[0]),
          str(held_court_of(r3["booking"]["id"])))


def sc_lesson_two_rows(s, fx):
    print("\n# Lesson: one booking → coach + court rows; cancel frees BOTH")
    m = fx.members[0]
    start, end = at(fx, 9), at(fx, 10)
    check("coach slot free before lesson", has_slot(lesson_slots(s, fx), start))
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                         booking_type="lesson", resource_id=fx.coach_res,
                         coach_user_id=fx.coach_uid,
                         starts_at=utc_iso(start), ends_at=utc_iso(end))
    ok = r.get("ok")
    check("lesson booked", ok, str(r))
    oid = r["booking"]["order_id"] if ok else None
    rows = _rows_for_order(s, oid) if oid else []
    kinds = set()
    for row in rows:
        rk = s.execute(text("SELECT kind FROM diary.resource WHERE id=:r"),
                       {"r": row["resource_id"]}).scalar()
        kinds.add(rk)
    check("lesson created a coach row AND a court row", kinds == {"coach", "court"},
          f"rows={len(rows)} kinds={kinds}")
    check("coach slot gone after lesson", not has_slot(lesson_slots(s, fx), start))
    # Both courts? court1 taken by the lesson, court2 should still be free for the coach grid.
    # Cancel → coach AND court both free again.
    B.cancel_booking(s, club_id=fx.club_id, booking_id=r["booking"]["id"],
                     actor_user_id=m, role="member")
    check("coach slot free after lesson cancel", has_slot(lesson_slots(s, fx), start))
    free_courts = [c for c in fx.courts if has_slot(court_slots(s, fx, c), start)]
    check("both courts free after lesson cancel", len(free_courts) == len(fx.courts),
          f"free={len(free_courts)}/{len(fx.courts)}")


def sc_lesson_list_collapse(s, fx):
    print("\n# Lesson list: ONE line per lesson (court collapsed) with the court name attached")
    m = fx.members[0]
    start, end = at(fx, 9), at(fx, 10)
    B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                     booking_type="lesson", resource_id=fx.coach_res,
                     coach_user_id=fx.coach_uid,
                     starts_at=utc_iso(start), ends_at=utc_iso(end))
    mine = B.list_bookings(s, club_id=fx.club_id, role="member", user_id=m,
                           date_from=utc_iso(at(fx, 0)), date_to=utc_iso(at(fx, 23)))
    lessons = [b for b in mine if b["booking_type"] == "lesson"]
    courts = [b for b in mine if b["booking_type"] == "court"]
    check("member sees exactly ONE lesson line", len(lessons) == 1, f"lessons={len(lessons)}")
    check("the auto-held court row is hidden", len(courts) == 0, f"court rows={len(courts)}")
    check("lesson line carries the court name", bool(lessons and lessons[0].get("court_name")),
          str(lessons[0]) if lessons else "no lesson")
    # The coach (as_coach) sees the same single collapsed line.
    coach_view = B.list_bookings(s, club_id=fx.club_id, role="coach", user_id=fx.coach_uid,
                                 as_coach=True, date_from=utc_iso(at(fx, 0)),
                                 date_to=utc_iso(at(fx, 23)))
    check("coach sees one row for the lesson (no separate court)",
          len([b for b in coach_view if b["booking_type"] == "court"]) == 0,
          f"coach court rows={len([b for b in coach_view if b['booking_type']=='court'])}")


def sc_lesson_needs_court(s, fx):
    print("\n# Lesson: no free court at the time → not offered / refused")
    m = fx.members[0]
    start, end = at(fx, 9), at(fx, 10)
    # Occupy BOTH courts at 09:00 with plain court bookings.
    held = []
    for c in fx.courts:
        r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                             booking_type="court", resource_id=c,
                             starts_at=utc_iso(start), ends_at=utc_iso(end))
        held.append(r["booking"]["id"])
    check("lesson slot hidden when no court free", not has_slot(lesson_slots(s, fx), start))
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[1], role="member",
                         booking_type="lesson", resource_id=fx.coach_res,
                         coach_user_id=fx.coach_uid,
                         starts_at=utc_iso(start), ends_at=utc_iso(end))
    check("lesson refused when no court free", r.get("error") == "NO_COURT_AVAILABLE", str(r))
    for bid in held:
        B.cancel_booking(s, club_id=fx.club_id, booking_id=bid, actor_user_id=m, role="member")


def sc_coach_class_conflict(s, fx):
    print("\n# Coach∩class (the reported bug): a class blocks the coach's lessons")
    m = fx.members[0]
    # Schedule the class 08:00–09:30 on the test day.
    C.schedule_sessions(s, club_id=fx.club_id, resource_id=fx.class_res,
                        dates=[fx.target.isoformat()], start_time="08:00",
                        duration_minutes=90, capacity=2)
    # 08:00 and 09:00 lesson slots overlap the class → must be HIDDEN.
    check("coach 08:00 lesson hidden during class", not has_slot(lesson_slots(s, fx), at(fx, 8)))
    check("coach 09:00 lesson hidden during class", not has_slot(lesson_slots(s, fx), at(fx, 9)))
    check("coach 10:00 lesson visible after class", has_slot(lesson_slots(s, fx), at(fx, 10)))
    # Write-path guard: booking a lesson over the class → COACH_BUSY.
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                         booking_type="lesson", resource_id=fx.coach_res,
                         coach_user_id=fx.coach_uid,
                         starts_at=utc_iso(at(fx, 8)), ends_at=utc_iso(at(fx, 9)))
    check("lesson over class refused (COACH_BUSY)", r.get("error") == "COACH_BUSY", str(r))
    # A COURT booking at the class time is still fine (a class reserves no court).
    rc = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                          booking_type="court", resource_id=fx.courts[0],
                          starts_at=utc_iso(at(fx, 8)), ends_at=utc_iso(at(fx, 9)))
    check("court booking at class time still allowed", rc.get("ok"), str(rc))


def sc_slot_granularity(s, fx):
    print("\n# Slot grid: 30-min cadence — a 30-min booking leaves the next half-hour bookable")
    m = fx.members[0]; court = fx.courts[0]
    # Grid should offer :00 AND :30 starts.
    slots = court_slots(s, fx, court)
    check("08:30 start is offered (30-min grid)", has_slot(slots, at(fx, 8, 30)),
          "no 08:30 candidate")
    # Book 09:00–09:30 (a 30-min booking). The 09:30 start must remain bookable (the bug: an
    # hourly grid would jump straight to 10:00).
    B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                     booking_type="court", resource_id=court,
                     starts_at=utc_iso(at(fx, 9)), ends_at=utc_iso(at(fx, 9, 30)))
    after = court_slots(s, fx, court)
    check("09:30 start still bookable after a 30-min booking", has_slot(after, at(fx, 9, 30)),
          "09:30 gap not offered")
    check("09:00 start gone (just booked)", not has_slot(after, at(fx, 9)))


def sc_class_waitlist(s, fx):
    print("\n# Class: enrol to capacity → waitlist → cancel promotes the waitlister")
    # A fresh one-off class at 14:00 so it doesn't collide with the 08:00 session.
    C.schedule_sessions(s, club_id=fx.club_id, resource_id=fx.class_res,
                        dates=[fx.target.isoformat()], start_time="14:00",
                        duration_minutes=90, capacity=2)
    sid = s.execute(
        text("SELECT id FROM diary.class_session WHERE club_id=:c AND resource_id=:r "
             "AND starts_at = :sa"),
        {"c": fx.club_id, "r": fx.class_res, "sa": at(fx, 14)},
    ).scalar()
    r1 = C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=fx.members[0])
    r2 = C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=fx.members[1])
    r3 = C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=fx.members[2])
    check("1st enrol seated", r1.get("status_value") == "enrolled", str(r1))
    check("2nd enrol seated", r2.get("status_value") == "enrolled", str(r2))
    check("3rd enrol waitlisted (capacity 2)", r3.get("status_value") == "waitlisted", str(r3))
    cr = C.cancel_enrolment(s, club_id=fx.club_id, class_session_id=sid, user_id=fx.members[0])
    check("cancel promotes the waitlister", cr.get("promoted") is not None, str(cr))


def _class_at(s, fx, hour, capacity=2, mins=90):
    """Schedule a fresh one-off class at `hour` and return its session id. Raises if the session
    wasn't created — two classes on the SAME class resource must not overlap (a 90-min class at 12
    runs to 13:30, so the next one can't start at 13), and a silent None here surfaces much later as
    an unrelated TypeError."""
    C.schedule_sessions(s, club_id=fx.club_id, resource_id=fx.class_res,
                        dates=[fx.target.isoformat()], start_time="%02d:00" % hour,
                        duration_minutes=mins, capacity=capacity)
    sid = s.execute(
        text("SELECT id FROM diary.class_session WHERE club_id=:c AND resource_id=:r "
             "AND starts_at = :sa"),
        {"c": fx.club_id, "r": fx.class_res, "sa": at(fx, hour)},
    ).scalar()
    if not sid:
        raise AssertionError(
            "no class session created at %02d:00 — does it overlap another class on this "
            "resource, or fall outside its availability?" % hour)
    return sid


def _order_of_enrolment(s, fx, sid, uid):
    return s.execute(
        text('SELECT o.id, o.status, o.settlement_mode FROM diary.enrolment e '
             'JOIN billing."order" o ON o.id = e.order_id '
             'WHERE e.class_session_id=:cs AND e.user_id=:u'),
        {"cs": sid, "u": uid}).mappings().first()


def sc_class_roster_shows_payment(s, fx):
    """The club roster must never paint an unpaid seat as a plain 'Enrolled'. It used not to join
    billing."order" at all, so an awaiting_payment seat looked identical to a settled one — which is
    how five real seats were delivered unpaid without anyone seeing it."""
    print("\n# Class roster: an UNPAID seat is visibly unpaid to the club (not a bare 'Enrolled')")
    sid = _class_at(s, fx, 9, capacity=3)
    C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=fx.members[0],
            settlement_mode="online")
    C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=fx.members[1],
            settlement_mode="at_court")
    rr = C.roster(s, club_id=fx.club_id, session_id=sid)
    seats = {e["user_id"]: e for e in (rr.get("enrolled") or [])}
    online = seats.get(str(fx.members[0]))
    owed = seats.get(str(fx.members[1]))
    check("roster returns the online seat", bool(online), str(rr))
    check("the unpaid online seat is FLAGGED unpaid", online and online.get("unpaid") is True,
          str(online))
    check("...with its order status surfaced", online and online.get("order_status") == "awaiting_payment",
          str(online and online.get("order_status")))
    check("...and a human label the UI can print", online and "waiting payment" in
          (online.get("payment_label") or "").lower(), str(online and online.get("payment_label")))
    check("an at-court seat is NOT flagged unpaid (it's a normal owed debt)",
          owed and owed.get("unpaid") is False, str(owed))
    check("...and reads as Owed", owed and owed.get("payment_label") == "Owed",
          str(owed and owed.get("payment_label")))


def sc_class_checkin_settles_debt(s, fx):
    """Checking a player in asserts the class WAS delivered. An awaiting_payment order is excluded
    from the statement, month-end and invoicing, and the expiry sweep only matches 'enrolled' — so
    marking attendance used to strand the debt where nothing could ever collect or clear it."""
    print("\n# Class check-in: an unpaid held seat becomes a REAL owed debt (never stranded)")
    sid = _class_at(s, fx, 10, capacity=2)
    C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=fx.members[0],
            settlement_mode="online")
    before = _order_of_enrolment(s, fx, sid, fx.members[0])
    check("the online seat starts awaiting_payment", before and before["status"] == "awaiting_payment",
          str(dict(before) if before else None))

    C.mark_attendance(s, club_id=fx.club_id, session_id=sid, user_id=fx.members[0], attended=True)
    after = _order_of_enrolment(s, fx, sid, fx.members[0])
    check("check-in converts it to an OPEN (collectable) debt", after and after["status"] == "open",
          str(dict(after) if after else None))
    check("...settled at the desk, so it lands on the statement",
          after and after["settlement_mode"] == "at_court", str(after and after["settlement_mode"]))
    held = s.execute(text("SELECT held_until FROM diary.enrolment "
                          "WHERE class_session_id=:cs AND user_id=:u"),
                     {"cs": sid, "u": fx.members[0]}).scalar()
    check("...and the stale hold is cleared", held is None, str(held))
    st = s.execute(text("SELECT status FROM diary.enrolment WHERE class_session_id=:cs AND user_id=:u"),
                   {"cs": sid, "u": fx.members[0]}).scalar()
    check("the seat is marked attended", st == "attended", str(st))
    # A PAID seat must be left completely alone by the same path.
    C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=fx.members[1],
            settlement_mode="at_court")
    paid_before = _order_of_enrolment(s, fx, sid, fx.members[1])
    C.mark_attendance(s, club_id=fx.club_id, session_id=sid, user_id=fx.members[1], attended=True)
    paid_after = _order_of_enrolment(s, fx, sid, fx.members[1])
    check("an already-owed seat is untouched by check-in",
          paid_after and paid_after["status"] == paid_before["status"], str(dict(paid_after)))


def sc_class_promotion_never_free(s, fx):
    """THE SILENT ONE: cancelling voids the order but leaves enrolment.order_id pointing at the dead
    row. Re-enrolling into a full class reactivates that row as waitlisted WITH the stale id, and the
    old 'already billed?' guard only tested for a non-NULL id — so promotion skipped billing and
    handed out a free class with a confirmation email and no commission."""
    print("\n# Class promotion: a stale VOIDED order_id must NOT be mistaken for 'already billed'")
    sid = _class_at(s, fx, 11, capacity=1)
    # members[0] takes the only seat; members[1] enrols, cancels (voiding their order), then re-enrols
    # into the now-full class -> waitlisted, carrying the DEAD order_id.
    C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=fx.members[1])
    dead = _order_of_enrolment(s, fx, sid, fx.members[1])
    C.cancel_enrolment(s, club_id=fx.club_id, class_session_id=sid, user_id=fx.members[1])
    voided = s.execute(text('SELECT status FROM billing."order" WHERE id=:o'),
                       {"o": dead["id"]}).scalar()
    check("cancelling voided the original order", voided in ("void", "written_off"), str(voided))
    C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=fx.members[0])
    again = C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=fx.members[1])
    check("re-enrolling into the full class waitlists them", again.get("status_value") == "waitlisted",
          str(again))
    stale = s.execute(text("SELECT order_id FROM diary.enrolment "
                           "WHERE class_session_id=:cs AND user_id=:u"),
                      {"cs": sid, "u": fx.members[1]}).scalar()
    check("...still carrying the DEAD order_id (the trap)", str(stale) == str(dead["id"]), str(stale))

    # Free the seat -> promotion must BILL them on a fresh live order, not skip on the dead one.
    C.cancel_enrolment(s, club_id=fx.club_id, class_session_id=sid, user_id=fx.members[0])
    promoted = _order_of_enrolment(s, fx, sid, fx.members[1])
    check("the promoted seat IS billed (no free class)", bool(promoted), "no order at all")
    check("...on a NEW live order, not the voided one",
          promoted and str(promoted["id"]) != str(dead["id"]), str(promoted and promoted["id"]))
    check("...which is a real collectable debt",
          promoted and promoted["status"] in ("open", "awaiting_payment"), str(promoted))


def sc_class_late_payment_reinstates(s, fx):
    """A Yoco webhook arriving after the 30-minute hold lapsed used to take the money and give
    nothing: lazy expiry had cancelled the seat, and confirm_paid_enrolments only matched still-held
    seats. Bookings already re-instate in this case; classes now do too."""
    print("\n# Class late payment: a lapsed-then-paid seat is RE-INSTATED (never money-for-nothing)")
    sid = _class_at(s, fx, 12, capacity=2)
    C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=fx.members[0],
            settlement_mode="online")
    o = _order_of_enrolment(s, fx, sid, fx.members[0])
    # Force the hold to lapse, then sweep — exactly what an abandoned checkout does.
    s.execute(text("UPDATE diary.enrolment SET held_until = now() - interval '1 minute' "
                   "WHERE class_session_id=:cs AND user_id=:u"), {"cs": sid, "u": fx.members[0]})
    C.release_expired_enrolments(s, club_id=fx.club_id, class_session_id=sid)
    gone = s.execute(text("SELECT status FROM diary.enrolment WHERE class_session_id=:cs AND user_id=:u"),
                     {"cs": sid, "u": fx.members[0]}).scalar()
    check("the abandoned seat was swept to cancelled", gone == "cancelled", str(gone))

    # The webhook finally lands: mark the order paid, then run the payment-side confirm.
    s.execute(text('UPDATE billing."order" SET status = \'paid\' WHERE id = :o'), {"o": o["id"]})
    C.confirm_paid_enrolments(s, club_id=fx.club_id, order_id=o["id"])
    back = s.execute(text("SELECT status, held_until FROM diary.enrolment "
                          "WHERE class_session_id=:cs AND user_id=:u"),
                     {"cs": sid, "u": fx.members[0]}).mappings().first()
    check("the paid seat is RE-INSTATED, not left cancelled", back["status"] == "enrolled",
          str(dict(back)))
    check("...with no lingering hold", back["held_until"] is None, str(back["held_until"]))

    # And when the class filled up in the meantime, we must NOT bump the waitlister who took it —
    # the seat stays gone and it becomes a refund case (logged), never a silent overbooking.
    sid2 = _class_at(s, fx, 15, capacity=1)
    C.enrol(s, club_id=fx.club_id, class_session_id=sid2, user_id=fx.members[0],
            settlement_mode="online")
    o2 = _order_of_enrolment(s, fx, sid2, fx.members[0])
    s.execute(text("UPDATE diary.enrolment SET held_until = now() - interval '1 minute' "
                   "WHERE class_session_id=:cs AND user_id=:u"), {"cs": sid2, "u": fx.members[0]})
    C.release_expired_enrolments(s, club_id=fx.club_id, class_session_id=sid2)
    C.enrol(s, club_id=fx.club_id, class_session_id=sid2, user_id=fx.members[1])   # takes the seat
    s.execute(text('UPDATE billing."order" SET status = \'paid\' WHERE id = :o'), {"o": o2["id"]})
    C.confirm_paid_enrolments(s, club_id=fx.club_id, order_id=o2["id"])
    still = s.execute(text("SELECT status FROM diary.enrolment WHERE class_session_id=:cs AND user_id=:u"),
                      {"cs": sid2, "u": fx.members[0]}).scalar()
    check("a full class does NOT overbook on a late payment (refund case instead)",
          still == "cancelled", str(still))
    seated = _enrolled_n(s, sid2)
    check("...and capacity is still respected", seated == 1, str(seated))


def _enrolled_n(s, sid):
    return s.execute(text("SELECT count(*) FROM diary.enrolment "
                          "WHERE class_session_id=:cs AND status IN ('enrolled','attended')"),
                     {"cs": sid}).scalar()


def sc_class_session_lifecycle(s, fx):
    """A class had only TWO verbs — schedule and cancel — and cancel didn't give the money back.

    Three defects, one lifecycle:
      (1) NO WAY TO MOVE A SESSION. A coach who scheduled a term forward and then needed one shifted
          an hour had to CANCEL it (which releases every player and refunds) and re-schedule, losing
          the roster. In practice the session just ran at the wrong time.
      (2) THE COACH WAS NEVER TOLD a seat was taken. The only email that reached him was a blind copy
          of the player's own "you're enrolled" receipt — the same gap the lesson path had.
      (3) CANCELLING A CLASS KEPT THE MONEY. cancel_session called void_order, and void_order
          deliberately no-ops on a paid order ("a paid order must be refunded, not voided"), so every
          online payer lost their seat AND their payment — under an email that promised a refund."""
    print("\n# Class session lifecycle: MOVE it, tell the coach, and refund when the club cancels")

    # ---- (2) the coach hears about a seat, once, addressed to him ---------------------------
    sid = _class_at(s, fx, 8, capacity=3)
    with _Emits() as rec:
        C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=fx.members[0])
    check("an enrolment notifies the coach", rec.seen.count("class_booked") == 1, str(rec.seen))
    cb = rec.payloads("class_booked")[0]
    check("...addressed to the COACH, not the player",
          str(cb.get("user_id")) == str(fx.coach_uid), str(cb.get("user_id")))
    check("...and it names the player + the class",
          cb.get("player_name") and cb.get("class_name"), str(cb))
    check("...exactly once alongside the player's own confirmation",
          rec.seen.count("class_enrolled") == 1, str(rec.seen))

    # An ONLINE seat is not a seat until it's paid — the coach must not be told about a hold that
    # may lapse. He is told when the charge lands, which is the same rule the lesson path follows.
    sid_on = _class_at(s, fx, 10, capacity=2)
    with _Emits() as rec2:
        C.enrol(s, club_id=fx.club_id, class_session_id=sid_on, user_id=fx.members[1],
                settlement_mode="online")
    check("an UNPAID online seat does NOT notify the coach", "class_booked" not in rec2.seen,
          str(rec2.seen))
    o_on = _order_of_enrolment(s, fx, sid_on, fx.members[1])
    s.execute(text("UPDATE billing.\"order\" SET status='paid' WHERE id=:o"), {"o": o_on["id"]})
    with _Emits() as rec3:
        C.confirm_paid_enrolments(s, club_id=fx.club_id, order_id=o_on["id"])
    check("...he IS told when the payment lands", rec3.seen.count("class_booked") == 1, str(rec3.seen))

    # ---- (1) MOVE a session: it keeps its roster and takes its court with it -----------------
    court_a = fx.courts[0]
    C.schedule_sessions(s, club_id=fx.club_id, resource_id=fx.class_res,
                        dates=[fx.target.isoformat()], start_time="14:00", duration_minutes=60,
                        capacity=3, court_resource_ids=[court_a])
    mv = s.execute(text("SELECT id FROM diary.class_session WHERE club_id=:c AND resource_id=:r "
                        "AND starts_at=:sa"),
                   {"c": fx.club_id, "r": fx.class_res, "sa": at(fx, 14)}).scalar()
    check("a class is scheduled at 14:00 on court A", mv is not None)
    C.enrol(s, club_id=fx.club_id, class_session_id=mv, user_id=fx.members[0])
    C.enrol(s, club_id=fx.club_id, class_session_id=mv, user_id=fx.members[2])

    def held_courts(sid_):
        return {str(x) for x in s.execute(
            text("SELECT csc.court_resource_id FROM diary.class_session_court csc "
                 "JOIN diary.booking b ON b.id = csc.court_booking_id AND b.status='confirmed' "
                 "WHERE csc.class_session_id=:cs"), {"cs": sid_}).scalars().all()}

    check("it holds court A at 14:00", held_courts(mv) == {str(court_a)}, str(held_courts(mv)))
    with _Emits() as rec4:
        moved = C.reschedule_session(s, club_id=fx.club_id, session_id=mv,
                                     starts_at=utc_iso(at(fx, 16)), duration_minutes=60)
    check("the session MOVES (the verb that didn't exist)", moved.get("ok"), str(moved))
    row = s.execute(text("SELECT starts_at, ends_at FROM diary.class_session WHERE id=:i"),
                    {"i": mv}).mappings().first()
    check("...to the new time", row["starts_at"] == at(fx, 16), str(row["starts_at"]))
    check("...for the new duration", row["ends_at"] == at(fx, 17), str(row["ends_at"]))
    check("...keeping BOTH enrolments (this is what cancel-and-reschedule destroyed)",
          _enrolled_n(s, mv) == 2, str(_enrolled_n(s, mv)))
    check("...and every player is told where it went",
          rec4.seen.count("class_rescheduled") == 2, str(rec4.seen))
    ev = rec4.payloads("class_rescheduled")[0]
    check("...carrying the OLD time as well as the new (which diary entry to change)",
          ev.get("old_starts_at") and ev.get("starts_at") != ev.get("old_starts_at"), str(ev))

    # The court moved WITH it: free at the old time, blocked at the new one.
    check("the court hold followed the session", held_courts(mv) == {str(court_a)},
          str(held_courts(mv)))
    free_old = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[1], role="member",
                                booking_type="court", resource_id=court_a,
                                starts_at=utc_iso(at(fx, 14)), ends_at=utc_iso(at(fx, 14, 30)))
    check("the OLD slot is released (a stale hold would block a court nobody is using)",
          free_old.get("ok"), str(free_old))
    busy_new = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[1], role="member",
                                booking_type="court", resource_id=court_a,
                                starts_at=utc_iso(at(fx, 16)), ends_at=utc_iso(at(fx, 16, 30)))
    check("the NEW slot is blocked (the class really is on that court now)",
          busy_new.get("error") == "SLOT_TAKEN", str(busy_new))

    # A move onto a time the coach is already teaching is refused — the same guard scheduling uses.
    les = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[0], role="member",
                           booking_type="lesson", resource_id=fx.coach_res,
                           coach_user_id=fx.coach_uid,
                           starts_at=utc_iso(at(fx, 19)), ends_at=utc_iso(at(fx, 20)))
    check("the coach has a lesson at 19:00", les.get("ok"), str(les))
    clash = C.reschedule_session(s, club_id=fx.club_id, session_id=mv,
                                 starts_at=utc_iso(at(fx, 19)), duration_minutes=60)
    check("a move onto the coach's own lesson is REFUSED",
          clash.get("error") == "COACH_NOT_AVAILABLE", str(clash))
    check("...and the session did not move", s.execute(
        text("SELECT starts_at FROM diary.class_session WHERE id=:i"), {"i": mv}).scalar()
        == at(fx, 16))

    # No court free at the target -> REFUSE, and leave the class exactly as it was. A half-moved
    # class holds no court and cannot physically run.
    all_courts = [str(x) for x in s.execute(
        text("SELECT id FROM diary.resource WHERE club_id=:c AND kind='court' AND is_active=true"),
        {"c": fx.club_id}).scalars().all()]
    for c in all_courts:
        s.execute(text("INSERT INTO diary.booking (club_id, booking_type, resource_id, "
                       "starts_at, ends_at, status) VALUES (:c,'court',:r,:sa,:ea,'confirmed')"),
                  {"c": fx.club_id, "r": c, "sa": at(fx, 21), "ea": at(fx, 22)})
    nocourt = C.reschedule_session(s, club_id=fx.club_id, session_id=mv,
                                   starts_at=utc_iso(at(fx, 21)), duration_minutes=60)
    check("a move with NO court free anywhere is refused",
          nocourt.get("error") == "NO_COURT_AVAILABLE", str(nocourt))
    check("...and the class keeps its original court hold (never half-moved)",
          held_courts(mv) == {str(court_a)}, str(held_courts(mv)))
    check("...at its original time", s.execute(
        text("SELECT starts_at FROM diary.class_session WHERE id=:i"), {"i": mv}).scalar()
        == at(fx, 16))
    check("...and its roster", _enrolled_n(s, mv) == 2, str(_enrolled_n(s, mv)))

    # ---- (3) the club cancels: a PAID seat gets its money back, an unpaid one is voided -------
    sid_c = _class_at(s, fx, 12, capacity=3)
    C.enrol(s, club_id=fx.club_id, class_session_id=sid_c, user_id=fx.members[0],
            settlement_mode="online")
    C.enrol(s, club_id=fx.club_id, class_session_id=sid_c, user_id=fx.members[1],
            settlement_mode="at_court")
    paid_o = _order_of_enrolment(s, fx, sid_c, fx.members[0])
    owed_o = _order_of_enrolment(s, fx, sid_c, fx.members[1])
    s.execute(text("UPDATE billing.\"order\" SET status='paid' WHERE id=:o"), {"o": paid_o["id"]})

    # Stand in for the gateway: moving the money is the billing lane's job, but WHICH orders this
    # asks to refund is exactly the defect, so record the calls.
    import yoco_billing
    asked = []
    _orig_refund = yoco_billing.execute_order_refund
    yoco_billing.execute_order_refund = (
        lambda session, order_id, **kw: asked.append(str(order_id)))
    try:
        with _Emits() as rec5:
            res = C.cancel_session(s, club_id=fx.club_id, session_id=sid_c)
    finally:
        yoco_billing.execute_order_refund = _orig_refund
    check("the session cancels", res.get("ok"), str(res))
    check("the PAID seat is refunded (this is the money that used to just vanish)",
          asked == [str(paid_o["id"])], str(asked))
    owed_after = s.execute(text("SELECT status FROM billing.\"order\" WHERE id=:o"),
                           {"o": owed_o["id"]}).scalar()
    check("the UNPAID seat is voided, not refunded", owed_after == "void", str(owed_after))
    refunded_flags = [bool(p.get("refunded")) for p in rec5.payloads("class_cancelled")]
    check("the email is told the outcome (it promised a refund it never made)",
          sorted(refunded_flags) == [False, True], str(refunded_flags))


def sc_class_price_survives_rename(s, fx):
    """A class's service was resolved by JOINING ON NAMES. Renaming the service updates
    billing.product.name and nothing syncs diary.resource.name, so every session scheduled afterwards
    resolved to NO product: price_id NULL, then a kind-level fallback billed it at some OTHER class's
    rate under that class's payment rules. Two live enrolments were billed 'Adult beginner group'
    against 'Social Tennis' and 'Cardio Tennis'. A name is not an identifier."""
    print("\n# Class pricing survives a SERVICE RENAME (durable product link, not a name join)")
    # The class resource's own service, resolved the way the fixture names it.
    rname = s.execute(text("SELECT name FROM diary.resource WHERE id = :r"),
                      {"r": fx.class_res}).scalar()
    prod = s.execute(text("SELECT id FROM billing.product WHERE club_id=:c AND kind='class' "
                          "AND active=true AND lower(name)=lower(:n)"),
                     {"c": fx.club_id, "n": rname}).scalar()
    check("the class fixture has a product (fixture sanity)", bool(prod), "resource=%s" % rname)

    # A DECOY class product — cheaper, and the kind-level fallback's ORDER BY created_at makes it a
    # plausible wrong winner. If pricing ever reaches for "some class product", it lands here.
    decoy = s.execute(text("INSERT INTO billing.product (club_id, kind, name) "
                           "VALUES (:c,'class','Zzz Decoy Class') RETURNING id"),
                      {"c": fx.club_id}).scalar()
    s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                   "currency_code, unit, duration_minutes, active, status) "
                   "VALUES (:c,:p,'any',1000,'ZAR','per_booking',90,true,'active')"),
              {"c": fx.club_id, "p": decoy})      # R10 — obviously wrong if it ever wins

    # (1) A LEGACY row with no link yet, names still agreeing: the lookup heals it and PINS the link.
    s.execute(text("UPDATE diary.resource SET product_id = NULL WHERE id = :r"), {"r": fx.class_res})
    sid0 = _class_at(s, fx, 9, capacity=3)
    linked = s.execute(text("SELECT product_id FROM diary.resource WHERE id = :r"),
                       {"r": fx.class_res}).scalar()
    check("a legacy unlinked class is self-healed and PINNED to its product",
          str(linked) == str(prod), str(linked))
    check("...and that session prices off its own service",
          str(s.execute(text("SELECT product_id FROM billing.price WHERE id = "
                             "(SELECT price_id FROM diary.class_session WHERE id=:s)"),
                        {"s": sid0}).scalar()) == str(prod), "priced off the wrong product")

    # (2) NOW rename the service exactly as the editor does (product only — the resource keeps its
    # old name). With the link pinned, the rename is a non-event.
    s.execute(text("UPDATE billing.product SET name = 'Renamed Adult Group', updated_at = now() "
                   "WHERE id = :p"), {"p": prod})
    sid = _class_at(s, fx, 11, capacity=3)
    resolved = s.execute(text("SELECT price_id FROM diary.class_session WHERE id = :s"),
                         {"s": sid}).scalar()
    check("a session scheduled AFTER the rename STILL resolves a price",
          resolved is not None, "price_id is NULL — this is the bug")
    got_prod = s.execute(text("SELECT product_id FROM billing.price WHERE id = :p"),
                         {"p": resolved}).scalar() if resolved else None
    check("...and it is THIS class's own product, never the decoy",
          str(got_prod) == str(prod), "got %s, wanted %s" % (got_prod, prod))

    # (3) THE UNRECOVERABLE CASE, stated honestly: an OLD row that was never linked AND whose name
    # has already drifted cannot be resolved by any means — there is nothing left to match on. What
    # matters is that it now REFUSES rather than silently billing the decoy's R10. Relinking such a
    # class is a human job (the boot backfill deliberately skips ambiguous/drifted rows). We drift the
    # RESOURCE name directly here: the product→resource name trigger only prevents drift caused by a
    # PRODUCT rename, so a legacy resource whose own name was changed is still a genuine orphan.
    s.execute(text("UPDATE diary.resource SET product_id = NULL, name = 'Legacy Orphan No Match' "
                   "WHERE id = :r"), {"r": fx.class_res})
    sid2 = _class_at(s, fx, 15, capacity=3)
    orphan_price = s.execute(text("SELECT price_id FROM diary.class_session WHERE id = :s"),
                             {"s": sid2}).scalar()
    check("an orphaned class (no link + drifted name) resolves NO price", orphan_price is None,
          str(orphan_price))
    r = C.enrol(s, club_id=fx.club_id, class_session_id=sid2, user_id=fx.members[0],
                settlement_mode="at_court", role="member")
    check("...and enrolling is REFUSED, not billed at another class's rate",
          r.get("ok") is not True and r.get("error") == "PRICE_NOT_CONFIGURED", str(r))


def sc_class_list_shows_renamed_service(s, fx):
    """The DIARY 'Classes' list (list_class_types) is what a coach/owner sees to schedule. It joined
    the product to the resource BY NAME, so a class renamed in the service editor showed the STALE
    resource name and a blank price/length ("—") in the diary while Services showed the new name —
    Allon's 'Cardio Tennis' vs 'Cardio Bootcamp Tennis'. The list must resolve via the durable
    product_id link and show the CURRENT service name + price. And the rename itself must sync the
    resource name so the two never split."""
    print("\n# Diary class list follows a service-editor rename (durable link, name synced)")
    from admin import repositories as AR

    # A freshly-created class type: resource + product, linked at birth. Give it a price so the list
    # can show one.
    ct = C.create_class_type(s, club_id=fx.club_id, name="Cardio Tennis Ext", capacity=8,
                             price_amount_minor=18000, duration_minutes=60,
                             coach_user_id=fx.coach_uid)
    rid = ct["class"]["resource_id"]
    prod = s.execute(text("SELECT product_id FROM diary.resource WHERE id = :r"), {"r": rid}).scalar()
    check("new class is linked to its product at birth", bool(prod), str(ct))

    def _row():
        return next((x for x in C.list_class_types(s, club_id=fx.club_id)
                     if str(x["resource_id"]) == str(rid)), None)

    before = _row()
    check("list shows the class with its price BEFORE the rename",
          before and before["name"] == "Cardio Tennis Ext" and before["price_amount_minor"] == 18000,
          str(before))

    # Rename via the SERVICE EDITOR path (product only) — the exact action Allon took.
    AR.patch_product(s, club_id=fx.club_id, product_id=str(prod), name="Cardio Bootcamp Tennis")

    after = _row()
    check("the diary list now shows the NEW service name", after and after["name"] == "Cardio Bootcamp Tennis",
          str(after))
    check("...and STILL shows the price + length (no '—' blank)",
          after and after["price_amount_minor"] == 18000 and after["duration_minutes"] == 60, str(after))
    synced = s.execute(text("SELECT name FROM diary.resource WHERE id = :r"), {"r": rid}).scalar()
    check("the rename SYNCED the diary resource name (no future drift)",
          synced == "Cardio Bootcamp Tennis", synced)


def sc_class_name_cannot_break_the_class(s, fx):
    """THE PERMANENT GUARANTEE. A class is two linked rows (billing.product + diary.resource) and its
    name lived in both, so any writer touching one could split them. Renaming a class must NEVER break
    resolution, pricing or display — and the two stored names must be unable to drift, no matter WHO
    writes. Three independent proofs:
      (1) a DB trigger mirrors billing.product.name -> diary.resource.name even on a RAW SQL update
          (no application code involved) — so a future code path / script / manual query can't split them;
      (2) after a service-editor rename, both the list AND the single-class reader show the new name
          WITH the price (resolution is by product_id, never name);
      (3) a session scheduled AFTER the rename still prices correctly."""
    print("\n# A class name is not an identifier: renaming can never break the class (trigger + link)")
    from admin import repositories as AR

    ct = C.create_class_type(s, club_id=fx.club_id, name="Bootcamp Orig", capacity=8,
                             price_amount_minor=20000, duration_minutes=60, coach_user_id=fx.coach_uid)
    rid = ct["class"]["resource_id"]
    prod = s.execute(text("SELECT product_id FROM diary.resource WHERE id=:r"), {"r": rid}).scalar()

    # (1) THE DB TRIGGER — rename the product with RAW SQL, bypassing every code path.
    s.execute(text("UPDATE billing.product SET name='Bootcamp Raw' WHERE id=:p"), {"p": prod})
    check("DB trigger mirrors a RAW product rename onto the resource (drift is impossible)",
          s.execute(text("SELECT name FROM diary.resource WHERE id=:r"), {"r": rid}).scalar() == "Bootcamp Raw")

    # (2) THE SERVICE-EDITOR PATH — the real UI action.
    AR.patch_product(s, club_id=fx.club_id, product_id=str(prod), name="Bootcamp Final")
    row = C.class_type_dict(s, club_id=fx.club_id, resource_id=rid)
    listed = next((x for x in C.list_class_types(s, club_id=fx.club_id)
                   if str(x["resource_id"]) == str(rid)), None)
    check("single-class reader shows the new name + price (by product_id, not name)",
          row and row["name"] == "Bootcamp Final" and row["price_amount_minor"] == 20000, str(row))
    check("class list shows the new name + price too",
          listed and listed["name"] == "Bootcamp Final" and listed["price_amount_minor"] == 20000, str(listed))
    check("the resource name stayed in lockstep through the editor path",
          s.execute(text("SELECT name FROM diary.resource WHERE id=:r"), {"r": rid}).scalar() == "Bootcamp Final")

    # (3) A session scheduled AFTER the rename still prices off THIS class's own product.
    C.schedule_sessions(s, club_id=fx.club_id, resource_id=rid,
                        dates=[fx.target.isoformat()], start_time="13:00",
                        duration_minutes=60, capacity=8)
    pid = s.execute(text("SELECT price_id FROM diary.class_session WHERE resource_id=:r "
                         "ORDER BY created_at DESC LIMIT 1"), {"r": rid}).scalar()
    got = s.execute(text("SELECT product_id FROM billing.price WHERE id=:p"), {"p": pid}).scalar() if pid else None
    check("a session scheduled AFTER the rename still prices off its own product",
          pid is not None and str(got) == str(prod), f"price_id={pid} product={got} want {prod}")


def sc_class_retired_price_never_free(s, fx):
    """Removing a price variation deactivates the price row; billing's price read requires
    active=true, so the order was written at R0 while the class list still showed the old amount.
    Shown != charged, and silently across every already-scheduled session."""
    print("\n# Class with a RETIRED price variation: refuses / re-resolves — never enrols at R0")
    sid = _class_at(s, fx, 11, capacity=3)
    pid = s.execute(text("SELECT price_id FROM diary.class_session WHERE id = :s"),
                    {"s": sid}).scalar()
    check("the session froze a price (fixture sanity)", bool(pid), str(pid))

    # Retire THAT variation, as the service editor's "Remove" does.
    s.execute(text("UPDATE billing.price SET active = false WHERE id = :p"), {"p": pid})
    r = C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=fx.members[0],
                settlement_mode="at_court", role="member")
    if r.get("ok"):
        # Acceptable ONLY if it re-resolved to another ACTIVE price of the same service and charged it.
        amt = s.execute(text('SELECT o.amount_minor FROM diary.enrolment e '
                             'JOIN billing."order" o ON o.id = e.order_id '
                             'WHERE e.class_session_id=:cs AND e.user_id=:u'),
                        {"cs": sid, "u": fx.members[0]}).scalar()
        check("if it enrolled, it was CHARGED (never a silent R0)", (amt or 0) > 0,
              "enrolled at amount_minor=%s" % amt)
    else:
        check("otherwise it is refused up-front with PRICE_NOT_CONFIGURED",
              r.get("error") == "PRICE_NOT_CONFIGURED", str(r))

    # A token seat is legitimately R0 and must NOT be blocked by the price guard.
    sid2 = _class_at(s, fx, 15, capacity=3)
    s.execute(text("UPDATE billing.price SET active = false WHERE id = "
                   "(SELECT price_id FROM diary.class_session WHERE id = :s)"), {"s": sid2})
    rt = C.enrol(s, club_id=fx.club_id, class_session_id=sid2, user_id=fx.members[1],
                 settlement_mode="free", role="club_admin")
    check("a legitimately-R0 (free/admin) seat is NOT blocked by the price guard",
          rt.get("ok") is True, str(rt))


def sc_class_online_hold_expiry(s, fx):
    print("\n# Class: unpaid ONLINE seat is HELD, lazily released on abandonment, waitlister promoted")
    C.schedule_sessions(s, club_id=fx.club_id, resource_id=fx.class_res,
                        dates=[fx.target.isoformat()], start_time="16:00",
                        duration_minutes=90, capacity=1)
    sid = s.execute(
        text("SELECT id FROM diary.class_session WHERE club_id=:c AND resource_id=:r "
             "AND starts_at = :sa"),
        {"c": fx.club_id, "r": fx.class_res, "sa": at(fx, 16)},
    ).scalar()
    # Online enrol HOLDS the seat pending the Yoco payment: awaiting_payment order + held_until stamp,
    # and the response carries the order to pay (the paywall seam the frontend drives).
    r1 = C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=fx.members[0],
                 settlement_mode="online")
    check("online enrol seated (held)", r1.get("status_value") == "enrolled", str(r1))
    check("online enrol returns an order to pay", bool(r1.get("order_id")), str(r1))
    held, ostatus = s.execute(
        text('SELECT e.held_until, o.status FROM diary.enrolment e '
             'JOIN billing."order" o ON o.id = e.order_id '
             'WHERE e.class_session_id=:cs AND e.user_id=:u'),
        {"cs": sid, "u": fx.members[0]}).first()
    check("held_until stamped on the online seat", held is not None)
    check("order awaiting_payment (paywall pending)", ostatus == "awaiting_payment", str(ostatus))
    # Bug (c): the client's OWN view flags the unpaid seat as awaiting_payment (not a confirmed session).
    mine = C.list_my_enrolments(s, club_id=fx.club_id, user_id=fx.members[0])
    me_row = [e for e in mine if e["class_session_id"] == str(sid)]
    check("client's own view flags the unpaid seat 'awaiting_payment' (not confirmed)",
          len(me_row) == 1 and me_row[0].get("awaiting_payment") is True,
          str(me_row and me_row[0].get("awaiting_payment")))
    # Bug (b): the class SERVICE's payment preference is surfaced so the checkout can honour it.
    cprod2 = s.execute(text("SELECT pr.product_id FROM diary.class_session cs "
                            "JOIN billing.price pr ON pr.id = cs.price_id WHERE cs.id=:s"), {"s": sid}).scalar()
    if cprod2:
        s.execute(text("UPDATE billing.product SET payment_modes='online' WHERE id=:p"), {"p": cprod2})
        sess = C.list_sessions(s, club_id=fx.club_id,
                               date_from=fx.target.isoformat(), date_to=fx.target.isoformat())
        srow = [x for x in sess if x["id"] == str(sid)]
        check("list_sessions surfaces the class payment preference (online-only)",
              len(srow) == 1 and srow[0].get("payment_modes") == "online",
              str(srow and srow[0].get("payment_modes")))
    # A second member is waitlisted behind the held seat (capacity 1).
    r2 = C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=fx.members[1],
                 settlement_mode="online")
    check("2nd online enrol waitlisted behind the held seat",
          r2.get("status_value") == "waitlisted", str(r2))
    # Simulate an abandoned checkout: backdate the hold, then run lazy expiry (as a class read would).
    s.execute(text("UPDATE diary.enrolment SET held_until = now() - interval '1 minute' "
                   "WHERE class_session_id=:cs AND user_id=:u"),
              {"cs": sid, "u": fx.members[0]})
    released = C.release_expired_enrolments(s, club_id=fx.club_id, class_session_id=sid)
    check("lapsed unpaid seat released", released == 1, f"released={released}")
    st0, ost0 = s.execute(
        text('SELECT e.status, o.status FROM diary.enrolment e '
             'JOIN billing."order" o ON o.id = e.order_id '
             'WHERE e.class_session_id=:cs AND e.user_id=:u'),
        {"cs": sid, "u": fx.members[0]}).first()
    check("abandoned seat is now cancelled", st0 == "cancelled", str(st0))
    check("its unpaid order was voided", ost0 == "void", str(ost0))
    st1 = s.execute(text("SELECT status FROM diary.enrolment WHERE class_session_id=:cs AND user_id=:u"),
                    {"cs": sid, "u": fx.members[1]}).scalar()
    check("waitlister promoted into the freed seat", st1 == "enrolled", str(st1))
    # A PAID online seat must NEVER be expired even once its hold lapses.
    C.schedule_sessions(s, club_id=fx.club_id, resource_id=fx.class_res,
                        dates=[fx.target.isoformat()], start_time="15:00",
                        duration_minutes=60, capacity=1)
    sid2 = s.execute(
        text("SELECT id FROM diary.class_session WHERE club_id=:c AND resource_id=:r AND starts_at=:sa"),
        {"c": fx.club_id, "r": fx.class_res, "sa": at(fx, 15)}).scalar()
    check("paid-guard session created", sid2 is not None)
    rp = C.enrol(s, club_id=fx.club_id, class_session_id=sid2, user_id=fx.members[2],
                 settlement_mode="online")
    s.execute(text("UPDATE billing.\"order\" SET status='paid' WHERE id=:o"), {"o": rp.get("order_id")})
    s.execute(text("UPDATE diary.enrolment SET held_until = now() - interval '1 minute' "
                   "WHERE class_session_id=:cs AND user_id=:u"), {"cs": sid2, "u": fx.members[2]})
    rel2 = C.release_expired_enrolments(s, club_id=fx.club_id, class_session_id=sid2)
    check("a PAID seat is never released", rel2 == 0, f"released={rel2}")
    stp = s.execute(text("SELECT status FROM diary.enrolment WHERE class_session_id=:cs AND user_id=:u"),
                    {"cs": sid2, "u": fx.members[2]}).scalar()
    check("paid seat stays enrolled", stp == "enrolled", str(stp))


def sc_offpeak_slot_pricing(s, fx):
    print("\n# Off-peak membership: court slots priced PER-SLOT (free inside window, PAYG at peak)")
    from billing.membership import membership_product_id
    member, court = fx.members[0], fx.courts[0]
    # A PAYG court price (60 min = R150) so peak slots have an amount to fall back to.
    cprod = s.execute(text("INSERT INTO billing.product (club_id, kind, name, active) "
                           "VALUES (:c,'court_booking','Court Hire',true) RETURNING id"),
                      {"c": fx.club_id}).scalar()
    s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                   "currency_code, unit, duration_minutes, active) "
                   "VALUES (:c,:p,'any',15000,'ZAR','per_booking',60,true)"),
              {"c": fx.club_id, "p": cprod})
    # An OFF-PEAK membership: weekdays 06:00–16:00 (start_min 360, end_min 960).
    mprod = membership_product_id(s, club_id=fx.club_id, create_if_missing=True)
    mprice = s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                            "currency_code, unit, term_months, membership_tier, active, "
                            "access_days, access_start_min, access_end_min) "
                            "VALUES (:c,:p,'member',18000,'ZAR','per_month',1,'Off-Peak',true,"
                            "'1,2,3,4,5',360,960) RETURNING id"),
                       {"c": fx.club_id, "p": mprod}).scalar()
    s.execute(text("INSERT INTO billing.membership_subscription (club_id, user_id, price_id, status, "
                   "provider, current_period_end) VALUES (:c,:u,:pr,'active','manual',CURRENT_DATE+30)"),
              {"c": fx.club_id, "u": member, "pr": mprice})

    windows = P.active_membership_windows(s, club_id=fx.club_id, user_id=member)
    slots = A.compute_availability(
        s, club_id=fx.club_id, resource_id=court, kind="court",
        date_from=utc_iso(at(fx, 8)), date_to=utc_iso(at(fx, 18)),
        duration_minutes=60, audience="member",
        membership_covered=bool(windows), membership_windows=windows)
    by_start = {sl["start"]: sl for sl in slots}
    s10 = by_start.get(utc_iso(at(fx, 10)))   # inside window
    s17 = by_start.get(utc_iso(at(fx, 17)))   # peak (after 16:00)
    is_weekday = fx.target.weekday() < 5
    check("peak 17:00 slot keeps its PAYG price (R150)", bool(s17) and s17["price"] == 15000,
          str(s17 and s17.get("price")))
    check("off-peak 10:00 slot is free on a weekday", (not is_weekday) or (bool(s10) and s10["price"] == 0),
          f"weekday={is_weekday} price={s10 and s10.get('price')}")


def sc_peak_court_pricing(s, fx):
    print("\n# PEAK court pricing: a booking inside the club peak window is charged its peak price (shown == charged)")
    member, court = fx.members[0], fx.courts[0]
    # A court PAYG price: 60 min = R150 base, R250 peak.
    cprod = s.execute(text("INSERT INTO billing.product (club_id, kind, name, active) "
                           "VALUES (:c,'court_booking','Court Hire',true) RETURNING id"),
                      {"c": fx.club_id}).scalar()
    s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                   "currency_code, unit, duration_minutes, peak_amount_minor, active) "
                   "VALUES (:c,:p,'any',15000,'ZAR','per_booking',60,25000,true)"),
              {"c": fx.club_id, "p": cprod})
    # Allocate the test court to THIS court service (the fixture already has a court product, so we scope
    # every read to cprod — otherwise price resolution blends the cheapest across court products).
    s.execute(text("UPDATE diary.resource SET product_id=:p WHERE id=:r"), {"p": cprod, "r": court})
    # Club peak window: 17:00–19:00 EVERY day (peak_days NULL = all days) so the test is weekday-agnostic.
    s.execute(text("INSERT INTO club.policy (club_id, peak_start_min, peak_end_min, peak_days) "
                   "VALUES (:c,1020,1140,NULL) ON CONFLICT (club_id) DO UPDATE SET "
                   "peak_start_min=1020, peak_end_min=1140, peak_days=NULL"),
              {"c": fx.club_id})

    def _drop_peak_cache():
        # The peak window is cached on the SHARED session; clear it so reads see the just-set window,
        # and again at the end so the savepoint-rolled-back policy doesn't leak "peak on" into later
        # scenarios. Through pricing.clear_peak_cache, NOT by delattr on a private name: this used to
        # name the attribute itself, and when the cache went plural the clear silently became a no-op.
        P.clear_peak_cache(s)

    _drop_peak_cache()
    try:
        # 1) The resolver: peak in-window, base off-window, base when no time given (backward compat).
        pk = P.price_for(s, club_id=fx.club_id, product_id=cprod, duration_minutes=60, at_local=at(fx, 17))
        op = P.price_for(s, club_id=fx.club_id, product_id=cprod, duration_minutes=60, at_local=at(fx, 10))
        nt = P.price_for(s, club_id=fx.club_id, product_id=cprod, duration_minutes=60)
        check("price_for peak (17:00) = R250 + is_peak", bool(pk) and pk["amount_minor"] == 25000 and pk.get("is_peak"), str(pk))
        check("price_for off-peak (10:00) = R150", bool(op) and op["amount_minor"] == 15000 and not op.get("is_peak"), str(op))
        check("price_for no time = base R150 (backward compat)", bool(nt) and nt["amount_minor"] == 15000, str(nt))

        # 2) Availability shows peak at 17:00, base at 10:00 (no membership → straight PAYG).
        slots = A.compute_availability(s, club_id=fx.club_id, resource_id=court, kind="court",
                                       date_from=utc_iso(at(fx, 8)), date_to=utc_iso(at(fx, 18)),
                                       duration_minutes=60, audience="member", product_id=cprod)
        by_start = {sl["start"]: sl for sl in slots}
        s17 = by_start.get(utc_iso(at(fx, 17)))
        s10 = by_start.get(utc_iso(at(fx, 10)))
        check("availability 17:00 slot shows R250 (peak)", bool(s17) and s17["price"] == 25000, str(s17 and s17.get("price")))
        check("availability 10:00 slot shows R150 (off-peak)", bool(s10) and s10["price"] == 15000, str(s10 and s10.get("price")))
        # 15-min start grid (BOOKING_GRANULARITY_MIN=15): starts are offered on the quarter-hour, not only :00/:30.
        _mins = set(sl["start"][14:16] for sl in slots)
        check("15-min start grid live (:15/:45 starts offered)", ("15" in _mins or "45" in _mins), str(sorted(_mins)))

        # 3) create_booking CHARGES what was shown — peak at 17:00, base at 10:00.
        def _order_amt(booking_id):
            return s.execute(text('SELECT o.amount_minor FROM billing."order" o '
                                  'JOIN billing.order_line ol ON ol.order_id = o.id '
                                  'WHERE ol.booking_id = :b LIMIT 1'), {"b": booking_id}).scalar()
        rp = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=member, role="member",
                              booking_type="court", resource_id=court, settlement_mode="at_court", product_id=cprod,
                              starts_at=utc_iso(at(fx, 17)), ends_at=utc_iso(at(fx, 18)))
        check("peak court booking charges R250 (shown == charged)",
              rp.get("ok") and _order_amt(rp["booking"]["id"]) == 25000,
              str(rp.get("error") or (rp.get("booking") and _order_amt(rp["booking"]["id"]))))
        ro = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=member, role="member",
                              booking_type="court", resource_id=court, settlement_mode="at_court", product_id=cprod,
                              starts_at=utc_iso(at(fx, 10)), ends_at=utc_iso(at(fx, 11)))
        check("off-peak court booking charges R150",
              ro.get("ok") and _order_amt(ro["booking"]["id"]) == 15000,
              str(ro.get("error") or (ro.get("booking") and _order_amt(ro["booking"]["id"]))))
    finally:
        _drop_peak_cache()


def sc_membership_entitlement(s, fx):
    print("\n# Membership entitlement (SILENT caps): duration cap, courts/day cap, clay exclusion -> PAYG")
    from billing.membership import membership_product_id
    from diary import entitlement as E
    member, court1, court2 = fx.members[0], fx.courts[0], fx.courts[1]
    # A members-covered court service (Hardcourt) + a PAYG-only one (Clay, members_covered=false).
    hard = s.execute(text("INSERT INTO billing.product (club_id, kind, name, active, members_covered) "
                          "VALUES (:c,'court_booking','Hardcourt',true,true) RETURNING id"),
                     {"c": fx.club_id}).scalar()
    clay = s.execute(text("INSERT INTO billing.product (club_id, kind, name, active, members_covered) "
                          "VALUES (:c,'court_booking','Clay',true,false) RETURNING id"),
                     {"c": fx.club_id}).scalar()
    for pid, amt in ((hard, 15000), (clay, 28000)):
        s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                       "currency_code, unit, duration_minutes, active) "
                       "VALUES (:c,:p,'any',:a,'ZAR','per_booking',60,true)"),
                  {"c": fx.club_id, "p": pid, "a": amt})
        # a 120-min price too so an over-cap booking has a PAYG rate to fall to.
        s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                       "currency_code, unit, duration_minutes, active) "
                       "VALUES (:c,:p,'any',:a,'ZAR','per_booking',120,true)"),
                  {"c": fx.club_id, "p": pid, "a": amt * 2})
    s.execute(text("UPDATE diary.resource SET product_id=:p WHERE id IN (:a,:b)"),
              {"p": hard, "a": court1, "b": court2})
    clay_court = s.execute(
        text("INSERT INTO diary.resource (club_id, kind, name, surface, rank, product_id) "
             "VALUES (:c,'court','Clay Court','clay',9,:p) RETURNING id"),
        {"c": fx.club_id, "p": clay}).scalar()
    # A membership tier: any-time coverage, max 90 covered minutes, max 1 court/day.
    mprod = membership_product_id(s, club_id=fx.club_id, create_if_missing=True)
    mprice = s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                            "currency_code, unit, term_months, membership_tier, active, "
                            "max_covered_minutes, max_courts_per_day) "
                            "VALUES (:c,:p,'member',18000,'ZAR','per_month',1,'Adult',true,90,1) RETURNING id"),
                       {"c": fx.club_id, "p": mprod}).scalar()
    s.execute(text("INSERT INTO billing.membership_subscription (club_id, user_id, price_id, status, "
                   "provider, current_period_end) VALUES (:c,:u,:pr,'active','manual',CURRENT_DATE+30)"),
              {"c": fx.club_id, "u": member, "pr": mprice})

    def ent(res, h0, h1):
        return E.court_covered(s, club_id=fx.club_id, user_id=member,
                               starts_at=at(fx, h0), ends_at=at(fx, h1), resource_id=res)
    check("60-min court booking is covered (within caps)", ent(court1, 10, 11) is True)
    check("120-min court booking NOT covered (over the 90-min cap)", ent(court1, 10, 12) is False)
    check("clay court NEVER covered for a member (members_covered=false)", ent(clay_court, 10, 11) is False)

    def _order_amt(bid):
        return s.execute(text('SELECT o.amount_minor, o.settlement_mode FROM billing."order" o '
                              'JOIN billing.order_line ol ON ol.order_id=o.id WHERE ol.booking_id=:b LIMIT 1'),
                         {"b": bid}).mappings().first()
    # A covered 60-min booking settles R0 (membership pays).
    r1 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=member, role="member",
                          booking_type="court", resource_id=court1, settlement_mode="membership_covered",
                          starts_at=utc_iso(at(fx, 10)), ends_at=utc_iso(at(fx, 11)), product_id=hard)
    o1 = _order_amt(r1["booking"]["id"]) if r1.get("ok") else None
    check("covered court booking is R0 + membership_covered", bool(o1) and o1["amount_minor"] == 0 and o1["settlement_mode"] == "membership_covered", str(o1))
    # A 2nd DISTINCT court the same day exceeds max_courts_per_day=1 -> silently PAYG (R150).
    check("2nd distinct court same day is NOT covered (courts/day cap)", ent(court2, 12, 13) is False)
    r2 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=member, role="member",
                          booking_type="court", resource_id=court2, settlement_mode="membership_covered",
                          starts_at=utc_iso(at(fx, 12)), ends_at=utc_iso(at(fx, 13)), product_id=hard)
    o2 = _order_amt(r2["booking"]["id"]) if r2.get("ok") else None
    check("over-cap 2nd court silently downgrades to PAYG R150 (never blocked)", bool(o2) and o2["amount_minor"] == 15000 and o2["settlement_mode"] == "at_court", str(o2))


def sc_configurable_trial(s, fx):
    print("\n# Configurable trial: the signup trial is a real membership tier + inherits its caps")
    from billing.membership import membership_product_id, grant_signup_trial, membership_status
    from diary import entitlement as E
    # A trial TIER: is_trial, 5 days, max 1 court/day (an entitlement cap the trial must inherit).
    mprod = membership_product_id(s, club_id=fx.club_id, create_if_missing=True)
    tprice = s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                            "currency_code, unit, term_months, membership_tier, active, is_trial, "
                            "trial_days, max_courts_per_day) "
                            "VALUES (:c,:p,'member',0,'ZAR','per_month',1,'Trial',true,true,5,1) RETURNING id"),
                       {"c": fx.club_id, "p": mprod}).scalar()
    # A brand-new member (no prior subscription) gets the trial.
    newu = s.execute(text("INSERT INTO iam.user (email, first_name) VALUES (:e,'New') RETURNING id"),
                     {"e": "trialtest+%s@example.com" % str(fx.club_id)[:8]}).scalar()
    g = grant_signup_trial(s, club_id=fx.club_id, user_id=newu, days=7)
    check("trial granted to a brand-new member", g.get("granted") is True, str(g))
    sub = s.execute(text("SELECT price_id, provider, (current_period_end - CURRENT_DATE) AS days_left "
                         "FROM billing.membership_subscription WHERE club_id=:c AND user_id=:u"),
                    {"c": fx.club_id, "u": newu}).mappings().first()
    check("trial LINKS the configured trial tier (not a NULL-price special case)", sub and str(sub["price_id"]) == str(tprice), str(sub and sub["price_id"]))
    check("trial length comes from the tier (5 days, not the env 7)", sub and int(sub["days_left"]) == 5, str(sub and sub["days_left"]))
    caps = E.active_caps(s, club_id=fx.club_id, user_id=newu)
    check("trial INHERITS the tier's caps (max 1 court/day)", caps["max_courts_per_day"] == 1, str(caps))
    stt = membership_status(s, club_id=fx.club_id, user_id=newu)
    check("membership_status still flags it as the trial", stt.get("is_trial") is True and stt.get("active") is True, str({"is_trial": stt.get("is_trial")}))
    # The trial tier is NOT offered for sale.
    from billing.membership import membership_plans
    plans = membership_plans(s, club_id=fx.club_id)
    check("the trial tier is NOT in the buyable plans list", all(str(pl["price_id"]) != str(tprice) for pl in plans), str(len(plans)))


def sc_equipment_hire(s, fx):
    print("\n# Equipment hire: flat-fee add-on on the court order (no double-bill, time-based availability, no double-book)")
    from admin import repositories as AR
    from diary import equipment as EQ
    member, court, court2 = fx.members[0], fx.courts[0], fx.courts[1]
    cprod = s.execute(text("INSERT INTO billing.product (club_id, kind, name, active) "
                           "VALUES (:c,'court_booking','Court Hire',true) RETURNING id"),
                      {"c": fx.club_id}).scalar()
    s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                   "currency_code, unit, duration_minutes, active) "
                   "VALUES (:c,:p,'any',15000,'ZAR','per_booking',60,true)"), {"c": fx.club_id, "p": cprod})
    s.execute(text("UPDATE diary.resource SET product_id=:p WHERE id IN (:a,:b)"),
              {"p": cprod, "a": court, "b": court2})
    ball = AR.create_equipment(s, club_id=fx.club_id, name="Ball machine", amount_minor=8000, quantity=1)
    racquet = AR.create_equipment(s, club_id=fx.club_id, name="Racquet", amount_minor=2000, quantity=10)
    check("ball machine starts with 1 unit free",
          EQ.available_units(s, club_id=fx.club_id, resource_id=ball["id"], starts=at(fx, 10), ends=at(fx, 11)) == 1)

    def order_of(bid):
        return s.execute(text('SELECT o.id, o.amount_minor, o.settlement_mode, '
                              '(SELECT count(*) FROM billing.order_line ol WHERE ol.order_id=o.id) AS lines '
                              'FROM billing."order" o JOIN billing.order_line ol2 ON ol2.order_id=o.id '
                              'WHERE ol2.booking_id=:b LIMIT 1'), {"b": bid}).mappings().first()

    # 1) PAYG court + ball machine + 2 racquets -> ONE order, R150 + R80 + R40 = R270, 3 lines.
    r1 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=member, role="member",
                          booking_type="court", resource_id=court, settlement_mode="at_court",
                          starts_at=utc_iso(at(fx, 10)), ends_at=utc_iso(at(fx, 11)), product_id=cprod,
                          addons=[{"resource_id": ball["id"], "qty": 1}, {"resource_id": racquet["id"], "qty": 2}])
    o1 = order_of(r1["booking"]["id"]) if r1.get("ok") else None
    check("court + equipment on ONE order, total R270", bool(o1) and o1["amount_minor"] == 27000, str(o1 and dict(o1)))
    check("ONE order, 3 lines (court + machine + racquets) — no double bill", bool(o1) and o1["lines"] == 3, str(o1 and o1["lines"]))

    # 2) the single ball machine is now unavailable for an OVERLAPPING time on ANOTHER court (time-based).
    check("ball machine 0 free during an overlapping time (court-agnostic)",
          EQ.available_units(s, club_id=fx.club_id, resource_id=ball["id"], starts=at(fx, 10, 30), ends=at(fx, 11, 30)) == 0)
    r2 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[1], role="member",
                          booking_type="court", resource_id=court2, settlement_mode="at_court",
                          starts_at=utc_iso(at(fx, 10)), ends_at=utc_iso(at(fx, 11)), product_id=cprod,
                          addons=[{"resource_id": ball["id"], "qty": 1}])
    check("2nd overlapping ball-machine hire refused (the 1 unit can't double-book)",
          r2.get("ok") is False and r2.get("error") == "EQUIPMENT_UNAVAILABLE", str(r2.get("error")))
    check("racquets still available (qty 10)",
          EQ.available_units(s, club_id=fx.club_id, resource_id=racquet["id"], starts=at(fx, 10), ends=at(fx, 11)) >= 8)
    check("ball machine free again at a non-overlapping time",
          EQ.available_units(s, club_id=fx.club_id, resource_id=ball["id"], starts=at(fx, 14), ends=at(fx, 15)) == 1)

    # 3) cancel voids the WHOLE order (equipment line goes with it) + frees the machine.
    B.cancel_booking(s, club_id=fx.club_id, booking_id=r1["booking"]["id"], actor_user_id=member, role="member")
    ost = s.execute(text('SELECT status FROM billing."order" WHERE id=:o'), {"o": o1["id"]}).scalar()
    check("cancel voids the whole order incl. equipment (no orphan charge)", ost in ("void", "written_off"), str(ost))
    check("cancelled booking frees the ball machine",
          EQ.available_units(s, club_id=fx.club_id, resource_id=ball["id"], starts=at(fx, 10), ends=at(fx, 11)) == 1)


def sc_court_service_allocation(s, fx):
    print("\n# Court services: distinct products over allocated courts (price + availability isolation)")
    member = fx.members[0]
    # Two court SERVICES at DIFFERENT prices (Hardcourt R150/60, Clay R280/60).
    hard = s.execute(text("INSERT INTO billing.product (club_id, kind, name, active) "
                          "VALUES (:c,'court_booking','Hardcourt Hire',true) RETURNING id"),
                     {"c": fx.club_id}).scalar()
    clay = s.execute(text("INSERT INTO billing.product (club_id, kind, name, active) "
                          "VALUES (:c,'court_booking','Clay Hire',true) RETURNING id"),
                     {"c": fx.club_id}).scalar()
    for pid, amt in ((hard, 15000), (clay, 28000)):
        s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                       "currency_code, unit, duration_minutes, active) "
                       "VALUES (:c,:p,'any',:a,'ZAR','per_booking',60,true)"),
                  {"c": fx.club_id, "p": pid, "a": amt})
    # Allocate: the two existing (hard) courts → Hardcourt; a NEW clay court → Clay.
    for cid in fx.courts:
        s.execute(text("UPDATE diary.resource SET product_id=:p WHERE id=:r"), {"p": hard, "r": cid})
    clay_court = s.execute(
        text("INSERT INTO diary.resource (club_id, kind, name, surface, rank, product_id) "
             "VALUES (:c,'court','Clay Court','clay',3,:p) RETURNING id"),
        {"c": fx.club_id, "p": clay}).scalar()
    s.execute(text("INSERT INTO diary.availability_rule (club_id, resource_id, weekday, start_time, "
                   "end_time, slot_minutes) VALUES (:c,:r,:wd,'08:00','18:00',60)"),
              {"c": fx.club_id, "r": clay_court, "wd": fx.target.weekday()})

    # --- pricing scoped per product (NO cheapest-across leak) ---
    hp = P.price_for(s, club_id=fx.club_id, kind="court_booking", duration_minutes=60, product_id=hard)
    cp = P.price_for(s, club_id=fx.club_id, kind="court_booking", duration_minutes=60, product_id=clay)
    check("hardcourt price scoped to R150", bool(hp) and hp["amount_minor"] == 15000, str(hp))
    check("clay price scoped to R280 (not the cheaper hard rate)", bool(cp) and cp["amount_minor"] == 28000, str(cp))
    hd = P.durations_for(s, club_id=fx.club_id, kind="court_booking", product_id=hard)
    cd = P.durations_for(s, club_id=fx.club_id, kind="court_booking", product_id=clay)
    check("hardcourt durations = its price only", len(hd) == 1 and hd[0]["amount_minor"] == 15000, str(hd))
    check("clay durations = its price only", len(cd) == 1 and cd[0]["amount_minor"] == 28000, str(cd))

    # court_service_for_resource resolves each court's own service.
    check("hard court resolves → Hardcourt product",
          str(P.court_service_for_resource(s, club_id=fx.club_id, resource_id=fx.courts[0])) == str(hard))
    check("clay court resolves → Clay product",
          str(P.court_service_for_resource(s, club_id=fx.club_id, resource_id=clay_court)) == str(clay))

    # --- availability scoped to the service's courts + priced by the service ---
    clay_slots = A.compute_availability(
        s, club_id=fx.club_id, kind="court", product_id=clay,
        date_from=utc_iso(at(fx, 8)), date_to=utc_iso(at(fx, 18)),
        duration_minutes=60, audience="member")
    clay_rids = {sl["resource_id"] for sl in clay_slots}
    check("clay availability returns ONLY the clay court", clay_rids == {str(clay_court)}, str(clay_rids))
    check("clay slots priced at the clay rate (R280)",
          bool(clay_slots) and all(sl["price"] == 28000 for sl in clay_slots),
          str(clay_slots[:1]))
    hard_slots = A.compute_availability(
        s, club_id=fx.club_id, kind="court", product_id=hard,
        date_from=utc_iso(at(fx, 8)), date_to=utc_iso(at(fx, 18)),
        duration_minutes=60, audience="member")
    hard_rids = {sl["resource_id"] for sl in hard_slots}
    # (The 'any court' union collapses identical times to the first free court, so hard_rids is a
    # SUBSET of the hard courts — the invariant is that only hard courts appear, never the clay one.)
    check("hardcourt availability excludes the clay court", str(clay_court) not in hard_rids, str(hard_rids))
    check("hardcourt availability returns only hard courts",
          bool(hard_rids) and hard_rids <= {str(c) for c in fx.courts}, str(hard_rids))
    check("hardcourt slots priced at the hard rate (R150)",
          bool(hard_slots) and all(sl["price"] == 15000 for sl in hard_slots), str(hard_slots[:1]))

    # --- booking charges the SERVICE's rate ---
    start = at(fx, 9)
    rc = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=member, role="member",
                          booking_type="court", resource_id=clay_court, product_id=clay,
                          starts_at=utc_iso(start), ends_at=utc_iso(at(fx, 10)))
    check("clay court booked", rc.get("ok"), str(rc))
    clay_amt = s.execute(text('SELECT amount_minor FROM billing."order" WHERE id=:o'),
                         {"o": rc["booking"]["order_id"]}).scalar() if rc.get("ok") else None
    check("clay booking charged R280", clay_amt == 28000, f"amount={clay_amt}")
    rh = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=member, role="member",
                          booking_type="court", resource_id=fx.courts[0], product_id=hard,
                          starts_at=utc_iso(start), ends_at=utc_iso(at(fx, 10)))
    check("hard court booked", rh.get("ok"), str(rh))
    hard_amt = s.execute(text('SELECT amount_minor FROM billing."order" WHERE id=:o'),
                         {"o": rh["booking"]["order_id"]}).scalar() if rh.get("ok") else None
    check("hard booking charged R150 (not blended)", hard_amt == 15000, f"amount={hard_amt}")

    # --- wrong-service guard: a Hardcourt court booked under the Clay service → rejected ---
    rw = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[1], role="member",
                          booking_type="court", resource_id=fx.courts[1], product_id=clay,
                          starts_at=utc_iso(at(fx, 11)), ends_at=utc_iso(at(fx, 12)))
    check("hard court booked under Clay service → COURT_NOT_IN_SERVICE",
          rw.get("error") == "COURT_NOT_IN_SERVICE", str(rw))

    # --- NULL-fallback: no product_id posted → prices via the court's own service (R150) ---
    rn = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[2], role="member",
                          booking_type="court", resource_id=fx.courts[0],
                          starts_at=utc_iso(at(fx, 13)), ends_at=utc_iso(at(fx, 14)))
    check("hard court booked with NO posted service", rn.get("ok"), str(rn))
    namt = s.execute(text('SELECT amount_minor FROM billing."order" WHERE id=:o'),
                     {"o": rn["booking"]["order_id"]}).scalar() if rn.get("ok") else None
    check("unscoped booking still charged the court's own (hard) rate R150", namt == 15000, f"amount={namt}")


def sc_class_courts(s, fx):
    print("\n# Class courts: reserve MULTIPLE courts + auto-repick + coach-guard + edit + cancel")
    # A 3rd court so a busy desired court has somewhere to be repicked to.
    court3 = s.execute(
        text("INSERT INTO diary.resource (club_id, kind, name, surface, rank) "
             "VALUES (:c,'court','Court 3','hard',3) RETURNING id"),
        {"c": fx.club_id}).scalar()
    s.execute(text("INSERT INTO diary.availability_rule (club_id, resource_id, weekday, start_time, "
                   "end_time, slot_minutes) VALUES (:c,:r,:wd,'08:00','18:00',60)"),
              {"c": fx.club_id, "r": court3, "wd": fx.target.weekday()})

    def session_courts(sid):
        return {str(x) for x in s.execute(
            text("SELECT court_resource_id FROM diary.class_session_court WHERE class_session_id=:cs"),
            {"cs": sid}).scalars().all()}

    def sid_at(hour):
        return s.execute(text("SELECT id FROM diary.class_session WHERE club_id=:c AND resource_id=:r "
                              "AND starts_at=:sa"),
                         {"c": fx.club_id, "r": fx.class_res, "sa": at(fx, hour)}).scalar()

    # --- (1) schedule a class on TWO courts at 10:00 → 2 link rows + 2 shadow holds; both blocked ---
    r = C.schedule_sessions(s, club_id=fx.club_id, resource_id=fx.class_res,
                            dates=[fx.target.isoformat()], start_time="10:00",
                            duration_minutes=90, capacity=2,
                            court_resource_ids=[fx.courts[0], fx.courts[1]])
    check("two-court class scheduled (created=1)", r.get("created") == 1, str(r))
    sid = sid_at(10)
    courts1 = session_courts(sid)
    check("2 class_session_court rows on the session", len(courts1) == 2, str(courts1))
    check("both desired courts linked", courts1 == {str(fx.courts[0]), str(fx.courts[1])}, str(courts1))
    shadows = {str(x) for x in s.execute(
        text("SELECT resource_id FROM diary.booking WHERE club_id=:c AND booking_type='class' "
             "AND status='confirmed' AND starts_at=:sa"),
        {"c": fx.club_id, "sa": at(fx, 10)}).scalars().all()}
    check("2 shadow court holds block both courts", shadows == {str(fx.courts[0]), str(fx.courts[1])},
          str(shadows))
    # A court booking on EITHER reserved court at the class time → SLOT_TAKEN.
    rc = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[0], role="member",
                          booking_type="court", resource_id=fx.courts[0],
                          starts_at=utc_iso(at(fx, 10)), ends_at=utc_iso(at(fx, 10, 30)))
    check("court1 blocked by the class (SLOT_TAKEN)", rc.get("error") == "SLOT_TAKEN", str(rc))
    rc2 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[0], role="member",
                           booking_type="court", resource_id=fx.courts[1],
                           starts_at=utc_iso(at(fx, 10)), ends_at=utc_iso(at(fx, 10, 30)))
    check("court2 blocked by the class (SLOT_TAKEN)", rc2.get("error") == "SLOT_TAKEN", str(rc2))

    # --- (2) busy desired court → auto-substitutes a free court (still 2 courts) ---
    occ = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[0], role="member",
                           booking_type="court", resource_id=fx.courts[0],
                           starts_at=utc_iso(at(fx, 13)), ends_at=utc_iso(at(fx, 14, 30)))
    check("court1 pre-occupied at 13:00", occ.get("ok"), str(occ))
    r2 = C.schedule_sessions(s, club_id=fx.club_id, resource_id=fx.class_res,
                             dates=[fx.target.isoformat()], start_time="13:00",
                             duration_minutes=90, capacity=2,
                             court_resource_ids=[fx.courts[0], fx.courts[1]])
    check("class scheduled despite a busy desired court", r2.get("created") == 1, str(r2))
    courts2 = session_courts(sid_at(13))
    check("busy court1 substituted out, court3 in", str(fx.courts[0]) not in courts2
          and str(court3) in courts2, str(courts2))
    check("substitution kept 2 courts (court2 not cannibalised)",
          courts2 == {str(fx.courts[1]), str(court3)}, str(courts2))

    # --- (3) coach busy (held/confirmed lesson) → the class occurrence is SKIPPED ---
    les = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[0], role="member",
                           booking_type="lesson", resource_id=fx.coach_res,
                           coach_user_id=fx.coach_uid,
                           starts_at=utc_iso(at(fx, 16)), ends_at=utc_iso(at(fx, 17)))
    check("coach has a confirmed lesson at 16:00", les.get("ok"), str(les))
    r3 = C.schedule_sessions(s, club_id=fx.club_id, resource_id=fx.class_res,
                             dates=[fx.target.isoformat()], start_time="16:00",
                             duration_minutes=90, capacity=2,
                             court_resource_ids=[court3])
    check("class over the coach's lesson skipped (coach_busy)",
          r3.get("created") == 0 and r3.get("coach_busy") == 1, str(r3))
    check("no class session created at 16:00", sid_at(16) is None)

    # --- (4) update_class_type: change coach + courts; cascades to FUTURE sessions ---
    coach2_uid = _mk_user(s, "coach2@scratch.test", "Coach2")
    s.execute(text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
                   "VALUES (:c,:u,'coach','active')"), {"c": fx.club_id, "u": coach2_uid})
    s.execute(text("INSERT INTO iam.coach_profile (club_id, user_id, display_name, is_bookable) "
                   "VALUES (:c,:u,'Coach Two',true)"), {"c": fx.club_id, "u": coach2_uid})
    prod_before = s.execute(
        text("SELECT id FROM billing.product WHERE club_id=:c AND kind='class' AND active=true "
             "AND lower(name)='cardio tennis'"), {"c": fx.club_id}).scalar()
    up = C.update_class_type(s, club_id=fx.club_id, resource_id=fx.class_res,
                             coach_user_id=coach2_uid, court_resource_ids=[court3])
    check("update ok", up.get("ok"), str(up))
    check("no new-coach conflicts reported", up.get("coach_conflicts") == [], str(up.get("coach_conflicts")))
    res_coach = s.execute(text("SELECT coach_user_id FROM diary.resource WHERE id=:r"),
                          {"r": fx.class_res}).scalar()
    check("class resource now coach2", str(res_coach) == str(coach2_uid), str(res_coach))
    prod_coach = s.execute(text("SELECT coach_user_id FROM billing.product WHERE id=:p"),
                           {"p": prod_before}).scalar()
    check("billing.product coach2 (commission attribution follows)",
          str(prod_coach) == str(coach2_uid), str(prod_coach))
    fut_coaches = {str(x) for x in s.execute(
        text("SELECT DISTINCT coach_user_id FROM diary.class_session WHERE club_id=:c AND resource_id=:r "
             "AND status='scheduled' AND starts_at >= now()"),
        {"c": fx.club_id, "r": fx.class_res}).scalars().all()}
    check("all future sessions carry coach2", fut_coaches == {str(coach2_uid)}, str(fut_coaches))
    # Courts reassigned to [court3] on every future session; shadow holds carry coach2.
    check("10:00 session re-reserved onto court3", session_courts(sid_at(10)) == {str(court3)},
          str(session_courts(sid_at(10))))
    check("13:00 session re-reserved onto court3", session_courts(sid_at(13)) == {str(court3)},
          str(session_courts(sid_at(13))))
    shadow_coaches = {str(x) for x in s.execute(
        text("SELECT DISTINCT b.coach_user_id FROM diary.booking b "
             "JOIN diary.class_session_court csc ON csc.court_booking_id=b.id "
             "JOIN diary.class_session cs ON cs.id=csc.class_session_id "
             "WHERE cs.club_id=:c AND cs.resource_id=:r AND b.status='confirmed'"),
        {"c": fx.club_id, "r": fx.class_res}).scalars().all()}
    check("shadow court holds carry coach2", shadow_coaches == {str(coach2_uid)}, str(shadow_coaches))
    # The old court1/court2 holds at 10:00 were cancelled → those courts free again.
    free1 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[1], role="member",
                             booking_type="court", resource_id=fx.courts[0],
                             starts_at=utc_iso(at(fx, 10)), ends_at=utc_iso(at(fx, 10, 30)))
    check("court1 free again after courts reassigned off it", free1.get("ok"), str(free1))

    # --- (5) cancel_session frees ALL the class's courts ---
    cancel = C.cancel_session(s, club_id=fx.club_id, session_id=sid_at(10))
    check("cancel_session ok", cancel.get("ok"), str(cancel))
    still_held = s.execute(
        text("SELECT count(*) FROM diary.booking b JOIN diary.class_session_court csc "
             "ON csc.court_booking_id=b.id WHERE csc.class_session_id=:cs AND b.status='confirmed'"),
        {"cs": sid_at(10)}).scalar()
    check("no court hold left for the cancelled session", still_held == 0, str(still_held))
    reuse = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[2], role="member",
                             booking_type="court", resource_id=court3,
                             starts_at=utc_iso(at(fx, 10)), ends_at=utc_iso(at(fx, 10, 30)))
    check("court3 bookable after the class session is cancelled", reuse.get("ok"), str(reuse))


def sc_cancel_after_start_guard(s, fx):
    """A1: a member/guest may NOT cancel a booking that has already STARTED — otherwise a
    delivered-but-owed booking could be cancelled after the fact, voiding its order and erasing the
    debt. Admins/coaches still may."""
    print("\n# A started booking can't be cancelled by the member (debt can't vanish after delivery)")
    m = fx.members[0]; court = fx.courts[0]
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                         booking_type="court", resource_id=court,
                         starts_at=utc_iso(at(fx, 9)), ends_at=utc_iso(at(fx, 10)),
                         settlement_mode="at_court")
    bid = r["booking"]["id"]
    # Force the booking into the PAST (it has been delivered) — create_booking refuses a past start.
    s.execute(text("UPDATE diary.booking SET starts_at=:s, ends_at=:e WHERE id=:id"),
              {"s": datetime.now(timezone.utc) - timedelta(hours=2),
               "e": datetime.now(timezone.utc) - timedelta(hours=1), "id": bid})
    rc = B.cancel_booking(s, club_id=fx.club_id, booking_id=bid, actor_user_id=m, role="member")
    check("member cancel of a started booking is refused (CANNOT_CANCEL_STARTED)",
          rc.get("error") == "CANNOT_CANCEL_STARTED", str(rc))
    check("the started booking is still confirmed (debt intact)",
          B.get_booking(s, club_id=fx.club_id, booking_id=bid)["status"] == "confirmed")
    ra = B.cancel_booking(s, club_id=fx.club_id, booking_id=bid, actor_user_id=fx.coach_uid,
                          role="club_admin")
    check("an admin may still cancel a started booking", ra.get("ok"), str(ra))


def sc_unpriced_booking_refused(s, fx):
    """A5: a BILLABLE booking with no configured price is refused up-front (would otherwise be a
    silent R0 order — a delivered service that's never owed). Nothing persists."""
    print("\n# A billable booking with no configured price is refused (no silent R0 order)")
    m = fx.members[0]; court = fx.courts[0]
    # Deactivate the court price for THIS savepoint only → the court service is now unpriced.
    s.execute(text("UPDATE billing.price p SET active=false FROM billing.product pr "
                   "WHERE p.product_id=pr.id AND pr.club_id=:c AND pr.kind='court_booking'"),
              {"c": fx.club_id})
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                         booking_type="court", resource_id=court,
                         starts_at=utc_iso(at(fx, 9)), ends_at=utc_iso(at(fx, 10)),
                         settlement_mode="at_court")
    check("an unpriced court booking is refused (PRICE_NOT_CONFIGURED)",
          r.get("error") == "PRICE_NOT_CONFIGURED", str(r))
    check("nothing persisted — the slot is still free", has_slot(court_slots(s, fx, court), at(fx, 9)))


def sc_backcapture_past_lesson(s, fx):
    print("\n# Back-capture: a coach logs a PAST lesson on-behalf → bills the client, no past-guard, "
          "resource resolved from coach_user_id")
    m = fx.members[0]
    past = datetime.now(JHB) - timedelta(days=2)
    start = past.replace(hour=15, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=1)
    # A MEMBER may NEVER backdate — the past guard holds even when allow_past is passed.
    blocked = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                               booking_type="lesson", resource_id=fx.coach_res,
                               coach_user_id=fx.coach_uid,
                               starts_at=utc_iso(start), ends_at=utc_iso(end), allow_past=True)
    check("member self-book cannot backdate (IN_THE_PAST)",
          blocked.get("error") == "IN_THE_PAST", str(blocked))
    # Coach ON-BEHALF, resource_id OMITTED → the server resolves the coach's diary resource from
    # coach_user_id (a past lesson has no availability slot to carry it).
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.coach_uid, role="coach",
                         booking_type="lesson", resource_id=None, coach_user_id=fx.coach_uid,
                         starts_at=utc_iso(start), ends_at=utc_iso(end),
                         settlement_mode="monthly_account", booked_for_user_id=m, allow_past=True)
    ok = r.get("ok")
    check("coach on-behalf logs a past lesson", ok, str(r))
    b = r.get("booking") or {}
    check("coach resource resolved from coach_user_id when omitted",
          str(b.get("resource_id")) == str(fx.coach_res),
          f"resource_id={b.get('resource_id')} coach_res={fx.coach_res}")
    # It BILLS the client — an owed order raised on THEIR account (not the coach's).
    oid = b.get("order_id")
    owner = s.execute(text('SELECT user_id FROM billing."order" WHERE id=:o'),
                      {"o": oid}).scalar() if oid else None
    check("past lesson raised an order billed to the client", str(owner) == str(m),
          f"order owner={owner} client={m}")
    # The lesson also holds a court in the past (harmless — nothing competes for a past slot).
    rows = _rows_for_order(s, oid) if oid else []
    kinds = set()
    for row in rows:
        kinds.add(s.execute(text("SELECT kind FROM diary.resource WHERE id=:r"),
                            {"r": row["resource_id"]}).scalar())
    check("back-captured lesson still made a coach + court row", kinds == {"coach", "court"},
          f"kinds={kinds}")


def sc_semi_private_perhead(s, fx):
    """SEMI-PRIVATE (squad) lesson: one slot, TWO clients, each billed their OWN order at the service
    price (PER HEAD — never summed onto one payer). Both see the lesson once in their person-360 at
    their own head; a cancel voids BOTH debts (no partner left stranded owing)."""
    print("\n# Semi-private lesson: 2 clients, one slot → 1 order EACH (per-head), both billed + "
          "visible; cancel voids both")
    from client360 import repositories as CL
    m0, m1 = fx.members[0], fx.members[1]
    start, end = at(fx, 11), at(fx, 12)
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m0, role="member",
                         booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                         starts_at=utc_iso(start), ends_at=utc_iso(end), extra_clients=[m1])
    ok = r.get("ok")
    check("semi-private lesson booked", ok, str(r))
    b = r.get("booking") or {}
    bid = b.get("id"); prim_oid = b.get("order_id")
    extra = b.get("extra_order_ids") or []
    check("one EXTRA order raised for the partner", len(extra) == 1, f"extra={extra}")

    def _owner_amt(oid):
        row = s.execute(text('SELECT o.user_id AS uid, '
                             ' (SELECT COALESCE(SUM(ol.amount_minor),0) FROM billing.order_line ol '
                             '    WHERE ol.order_id = o.id) AS amt '
                             'FROM billing."order" o WHERE o.id = :o'), {"o": oid}).mappings().first()
        return (str(row["uid"]), int(row["amt"])) if row else (None, None)

    p_owner, p_amt = _owner_amt(prim_oid)
    e_owner, e_amt = _owner_amt(extra[0]) if extra else (None, None)
    check("primary billed to client 1 @ R400", p_owner == str(m0) and p_amt == 40000, f"{p_owner}/{p_amt}")
    check("partner billed to client 2 @ R400 (per-head, not doubled)",
          e_owner == str(m1) and e_amt == 40000, f"{e_owner}/{e_amt}")
    # Both orders reference the ONE lesson booking (linked via order_line.booking_id, not order_id).
    linked = {str(x) for x in s.execute(
        text("SELECT DISTINCT order_id FROM billing.order_line WHERE booking_id = :b"),
        {"b": bid}).scalars()}
    check("both orders link to the one lesson booking",
          {str(prim_oid), str(extra[0])} <= linked if extra else False, str(linked))
    # person-360: EACH client sees the lesson ONCE, at THEIR OWN R400 (never the R800 table total).
    for idx, who in enumerate((m0, m1), start=1):
        c = CL.get_client_360(s, club_id=fx.club_id, user_id=who, scope="admin")
        les = [x for x in ((c.get("upcoming") or []) + (c.get("history") or []))
               if x.get("kind") == "lesson"]
        check(f"client {idx} sees the semi-private lesson in their 360 exactly once", len(les) == 1,
              f"lessons={len(les)}")
        check(f"client {idx}'s 360 lesson shows their OWN head R400",
              bool(les) and int(les[0].get("amount_minor") or 0) == 40000,
              str(les[0]) if les else "none")
    # Cancel the lesson → BOTH clients' owed orders void (no phantom debt on the partner).
    B.cancel_booking(s, club_id=fx.club_id, booking_id=bid, actor_user_id=m0, role="member")
    for idx, oid in enumerate((prim_oid, extra[0] if extra else None), start=1):
        st = s.execute(text('SELECT status FROM billing."order" WHERE id = :o'), {"o": oid}).scalar() if oid else None
        check(f"client {idx}'s order voided by the cancel", st == "void", f"status={st}")


def sc_semi_private_add_later(s, fx):
    """SEMI-PRIVATE add-a-player-LATER: a lesson booked solo can gain a second client after the fact
    (squad confirmations land late). The added client is billed their OWN order (per-head) + becomes
    visible in their 360; the cap + duplicate + non-lesson guards hold."""
    print("\n# Semi-private: add a 2nd client to an ALREADY-booked lesson (late confirmation) → their own bill")
    from client360 import repositories as CL
    m0, m1, m2 = fx.members[0], fx.members[1], fx.members[2]
    start, end = at(fx, 13), at(fx, 14)
    # Book a normal lesson SOLO first (no extra_clients).
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m0, role="member",
                         booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                         starts_at=utc_iso(start), ends_at=utc_iso(end))
    check("solo lesson booked", r.get("ok"), str(r))
    bid = r["booking"]["id"]
    # The service allows 2 clients (set max_clients=2 on the lesson product for this scratch club).
    s.execute(text("UPDATE billing.product SET max_clients = 2 WHERE club_id = :c AND kind = 'lesson'"),
              {"c": fx.club_id})
    # Coach adds the partner AFTER the fact.
    a = B.add_lesson_partner(s, club_id=fx.club_id, booking_id=bid, new_user_id=m1,
                             actor_user_id=fx.coach_uid, role="coach")
    check("coach adds a 2nd client to the existing lesson", a.get("ok"), str(a))
    add_oid = a.get("order_id")
    row = s.execute(text('SELECT o.user_id AS uid, '
                         ' (SELECT COALESCE(SUM(ol.amount_minor),0) FROM billing.order_line ol '
                         '    WHERE ol.order_id = o.id) AS amt '
                         'FROM billing."order" o WHERE o.id = :o'), {"o": add_oid}).mappings().first()
    check("added client billed their OWN order @ R400",
          row and str(row["uid"]) == str(m1) and int(row["amt"]) == 40000, str(dict(row) if row else None))
    # The added client now SEES the lesson in their 360 at their own head.
    c = CL.get_client_360(s, club_id=fx.club_id, user_id=m1, scope="admin")
    les = [x for x in ((c.get("upcoming") or []) + (c.get("history") or [])) if x.get("kind") == "lesson"]
    check("added client sees the lesson in their 360", len(les) == 1 and int(les[0].get("amount_minor") or 0) == 40000,
          f"lessons={len(les)}")
    # Guards: duplicate add is refused, and a 3rd client exceeds max_clients=2.
    dup = B.add_lesson_partner(s, club_id=fx.club_id, booking_id=bid, new_user_id=m1,
                               actor_user_id=fx.coach_uid, role="coach")
    check("adding the same client twice is refused", dup.get("error") == "ALREADY_ON_LESSON", str(dup))
    full = B.add_lesson_partner(s, club_id=fx.club_id, booking_id=bid, new_user_id=m2,
                                actor_user_id=fx.coach_uid, role="coach")
    check("a 3rd client past max_clients=2 is refused (LESSON_FULL)", full.get("error") == "LESSON_FULL", str(full))
    # Cancelling the lesson voids BOTH the primary and the late-added partner's order.
    B.cancel_booking(s, club_id=fx.club_id, booking_id=bid, actor_user_id=m0, role="member")
    st = s.execute(text('SELECT status FROM billing."order" WHERE id = :o'), {"o": add_oid}).scalar()
    check("the late-added partner's order voids on cancel too", st == "void", f"status={st}")


def sc_semi_private_dependents(s, fx):
    """SEMI-PRIVATE with a parent's TWO KIDS on ONE account: each child is a login-less dependent, so
    BOTH heads bill to the GUARDIAN (spend rolls up to the payer). The parent sees ONE lesson at R800
    (both kids); each kid is recorded as a player; a cancel voids both the parent's orders."""
    print("\n# Semi-private: a parent's 2 kids (dependents) → both billed to the PARENT, parent sees R800")
    from client360 import repositories as CL
    g = fx.members[0]
    k1 = _mk_dependent(s, fx.club_id, g, "Kid1")
    k2 = _mk_dependent(s, fx.club_id, g, "Kid2")
    s.execute(text("UPDATE billing.product SET max_clients = 2 WHERE club_id = :c AND kind = 'lesson'"),
              {"c": fx.club_id})
    start, end = at(fx, 15), at(fx, 16)
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=g, role="member",
                         booking_type="lesson", resource_id=fx.coach_res, coach_user_id=fx.coach_uid,
                         starts_at=utc_iso(start), ends_at=utc_iso(end),
                         parties=[{"party_role": "player", "user_id": k1}], extra_clients=[k2])
    check("semi-private for 2 kids booked", r.get("ok"), str(r))
    bid = r["booking"]["id"]
    owners = [str(x) for x in s.execute(
        text('SELECT DISTINCT o.user_id FROM billing."order" o JOIN billing.order_line ol ON ol.order_id = o.id '
             'WHERE ol.booking_id = :b'), {"b": bid}).scalars()]
    check("BOTH kids' orders bill the parent", owners == [str(g)], f"owners={owners}")
    c = CL.get_client_360(s, club_id=fx.club_id, user_id=g, scope="admin")
    les = [x for x in ((c.get("upcoming") or []) + (c.get("history") or [])) if x.get("kind") == "lesson"]
    check("parent sees ONE lesson for the squad", len(les) == 1, f"lessons={len(les)}")
    check("…billed the parent R800 (both kids' heads)",
          bool(les) and int(les[0].get("amount_minor") or 0) == 80000, str(les[0]) if les else "none")
    pn = s.execute(text("SELECT count(*) FROM diary.booking_party WHERE booking_id = :b AND party_role <> 'guest'"),
                   {"b": bid}).scalar()
    check("both kids recorded as players", pn == 2, f"parties={pn}")
    # A 3rd head is refused (max_clients=2, two heads already).
    k3 = _mk_dependent(s, fx.club_id, g, "Kid3")
    full = B.add_lesson_partner(s, club_id=fx.club_id, booking_id=bid, new_user_id=k3,
                                actor_user_id=fx.coach_uid, role="coach")
    check("a 3rd kid past max_clients=2 is refused", full.get("error") == "LESSON_FULL", str(full))
    B.cancel_booking(s, club_id=fx.club_id, booking_id=bid, actor_user_id=g, role="member")
    voids = [str(x) for x in s.execute(
        text('SELECT DISTINCT o.status FROM billing."order" o JOIN billing.order_line ol ON ol.order_id = o.id '
             'WHERE ol.booking_id = :b'), {"b": bid}).scalars()]
    check("cancel voids both kids' orders", voids == ["void"], f"statuses={voids}")


def sc_semi_private_addable_guard(s, fx):
    """Route guard (_addable_player_uid): a MEMBER may add club members + their OWN kids as squad
    players, but NEVER an arbitrary account or another family's child (no billing a stranger). Staff
    may add any in-club member/child. This is the security boundary behind the upfront + add-later flows."""
    print("\n# Semi-private guard: a member can't add someone else's account or child as a squad player")
    from diary.routes import _addable_player_uid
    g1, g2 = fx.members[0], fx.members[1]
    mine = _mk_dependent(s, fx.club_id, g1, "MyKid")
    theirs = _mk_dependent(s, fx.club_id, g2, "TheirKid")
    stranger = _mk_user(s, "stranger@nope.test", "Stray")   # a real user with NO membership in this club
    check("a club member is addable by a member",
          _addable_player_uid(s, fx.club_id, str(g2), owner_uid=str(g1), is_staff=False) == str(g2))
    check("my OWN child is addable (non-staff)",
          _addable_player_uid(s, fx.club_id, mine, owner_uid=str(g1), is_staff=False) == mine)
    check("another family's child is NOT addable by a member",
          _addable_player_uid(s, fx.club_id, theirs, owner_uid=str(g1), is_staff=False) is None)
    check("…but STAFF can add any in-club child",
          _addable_player_uid(s, fx.club_id, theirs, owner_uid=str(g1), is_staff=True) == theirs)
    check("a non-member account is never addable (even by staff)",
          _addable_player_uid(s, fx.club_id, str(stranger), owner_uid=str(g1), is_staff=True) is None)


def sc_class_payment_gate(s, fx):
    """A class is a SERVICE — enrolment must respect the payment rules like a court/lesson booking. A
    member CANNOT post 'membership_covered'/'free' to conjure an R0 seat (a membership covers COURTS
    only), and a CARD-ONLY class refuses pay-at-court. (Before the fix, enrol took any settlement_mode
    verbatim — a member could self-enrol for free or on an owed order against a card-only class.)"""
    print("\n# Class payment gate: no free seat via membership_covered; a card-only class refuses at-court")
    m = fx.members[0]
    C.schedule_sessions(s, club_id=fx.club_id, resource_id=fx.class_res,
                        dates=[fx.target.isoformat()], start_time="15:00",
                        duration_minutes=90, capacity=10)
    sid = s.execute(text("SELECT id FROM diary.class_session WHERE club_id=:c AND resource_id=:r AND starts_at=:sa"),
                    {"c": fx.club_id, "r": fx.class_res, "sa": at(fx, 15)}).scalar()
    # 1) 'membership_covered' is DOWNGRADED → the seat is OWED at the class price (R120), never free.
    r = C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=m,
                settlement_mode="membership_covered", role="member")
    check("class enrol accepted", r.get("ok"), str(r))
    oid = r.get("order_id")
    amt = s.execute(text('SELECT amount_minor FROM billing."order" WHERE id=:o'), {"o": oid}).scalar() if oid else None
    sm = s.execute(text('SELECT settlement_mode FROM billing."order" WHERE id=:o'), {"o": oid}).scalar() if oid else None
    check("membership_covered on a CLASS is NOT free — owed at the class price R120", amt == 12000, f"amount={amt}")
    check("…and it settled at-court, not membership_covered", sm != "membership_covered", f"mode={sm}")
    C.cancel_enrolment(s, club_id=fx.club_id, class_session_id=sid, user_id=m)
    # 2) CARD-ONLY class: at-court refused; online accepted.
    s.execute(text("UPDATE billing.product SET payment_modes='online' WHERE club_id=:c AND kind='class'"),
              {"c": fx.club_id})
    bad = C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=m, settlement_mode="at_court", role="member")
    check("card-only class refuses pay-at-court", bad.get("error") == "SETTLEMENT_NOT_ALLOWED", str(bad))
    ok = C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=m, settlement_mode="online", role="member")
    check("card-only class accepts online (card)", ok.get("ok"), str(ok))
    C.cancel_enrolment(s, club_id=fx.club_id, class_session_id=sid, user_id=m)
    # 3) 'free' is admin-only — a member can't self-enrol free.
    freebad = C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=m, settlement_mode="free", role="member")
    check("a member cannot self-enrol 'free'", freebad.get("error") == "SETTLEMENT_NOT_ALLOWED", str(freebad))
    # 4) STAFF override still works (admin enrols at-court on the card-only class).
    st = C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=fx.members[1],
                 settlement_mode="at_court", role="club_admin")
    check("staff may still enrol at-court on a card-only class", st.get("ok"), str(st))


def sc_card_only_service_gate(s, fx):
    """A CARD-ONLY service (payment_modes='online') refuses pay-at-court / month-end on the BOOKING
    path server-side — the guard scopes to the EXACT service product, so a clay-style card-only court
    can't be taken on an owed order (the leak behind the unpaid clay pack's sibling on the diary side).
    Staff keep their override."""
    print("\n# Card-only service: a member can't book it pay-at-court (server enforces the service's payment rule)")
    m = fx.members[0]
    s.execute(text("UPDATE billing.product SET payment_modes = 'online' WHERE club_id = :c AND kind = 'court_booking'"),
              {"c": fx.club_id})
    start, end = at(fx, 9), at(fx, 10)
    bad = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                           booking_type="court", resource_id=fx.courts[0],
                           starts_at=utc_iso(start), ends_at=utc_iso(end), settlement_mode="at_court")
    check("pay-at-court REFUSED on a card-only court (SETTLEMENT_NOT_ALLOWED)",
          bad.get("error") == "SETTLEMENT_NOT_ALLOWED", str(bad))
    check("nothing persisted — the slot is still free", has_slot(court_slots(s, fx, fx.courts[0]), start))
    ok = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                          booking_type="court", resource_id=fx.courts[0],
                          starts_at=utc_iso(start), ends_at=utc_iso(end), settlement_mode="online")
    check("online IS accepted on a card-only court", ok.get("ok"), str(ok))
    staff = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.coach_uid, role="club_admin",
                             booking_type="court", resource_id=fx.courts[1],
                             starts_at=utc_iso(start), ends_at=utc_iso(end), settlement_mode="at_court")
    check("staff may still force pay-at-court (admin override)", staff.get("ok"), str(staff))


class _Emits:
    """Record the events a block emits. main() stubs diary.events.emit to a no-op for the whole run,
    so a scenario that cares WHICH event fired swaps in a recorder for its own duration. Patching the
    module attribute (not the function) is what makes it visible to classes.py's `events.emit(...)`."""

    def __init__(self):
        self.seen = []
        self.events = []          # (name, payload) — for asserting WHO an event was addressed to

    def __enter__(self):
        self._orig = C.events.emit
        C.events.emit = self._rec
        return self

    def _rec(self, event, payload=None):
        self.seen.append(event)
        self.events.append((event, payload or {}))

    def payloads(self, name):
        return [p for (e, p) in self.events if e == name]

    def __exit__(self, *exc):
        C.events.emit = self._orig
        return False


def _membership_for(s, fx, user_id, *, days=30, **caps):
    """Give a user an active membership ending `days` out. caps → the tier's own entitlement caps."""
    from billing.membership import membership_product_id
    mprod = membership_product_id(s, club_id=fx.club_id, create_if_missing=True)
    cols = "".join(f", {k}" for k in caps)
    vals = "".join(f", :{k}" for k in caps)
    price = s.execute(
        text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, currency_code, "
             "unit, term_months, membership_tier, active" + cols + ") "
             "VALUES (:c,:p,'member',18000,'ZAR','per_month',1,'Adult',true" + vals + ") RETURNING id"),
        dict(caps, c=fx.club_id, p=mprod)).scalar()
    s.execute(
        text("INSERT INTO billing.membership_subscription (club_id, user_id, price_id, status, "
             "provider, current_period_end) "
             "VALUES (:c,:u,:pr,'active','manual',CURRENT_DATE + :d)"),
        {"c": fx.club_id, "u": user_id, "pr": price, "d": days})
    return price


def _order_for_booking(s, booking_id):
    return s.execute(
        text('SELECT o.amount_minor, o.settlement_mode, o.status FROM billing."order" o '
             'JOIN billing.order_line ol ON ol.order_id = o.id WHERE ol.booking_id = :b LIMIT 1'),
        {"b": booking_id}).mappings().first()


def sc_membership_cannot_book_past_its_own_expiry(s, fx):
    """A membership must be judged on the day the booking FALLS ON, not the day it is made.

    membership_covers tested `current_period_end >= CURRENT_DATE` — "is this plan alive right now" —
    while starts_at was used only for the access window. So a member could book FORWARD past their
    own expiry and the booking was written membership_covered at R0 permanently; the term lapsing
    afterwards changed nothing because the price was already fixed. Reported as trial members booking
    beyond their 7 days, but it was never trial-specific: a monthly member could book the whole of
    next month on the last day of this one and then not renew."""
    print("\n# A membership can't book past its own expiry (the trial/renewal R0 leak)")
    from diary import entitlement as E
    m = fx.members[0]
    _membership_for(s, fx, m, days=2)          # expires in 2 days; the test day is 3 days out
    soon = datetime.now(JHB) + timedelta(days=1)
    inside = datetime(soon.year, soon.month, soon.day, 10, tzinfo=JHB)
    check("a booking INSIDE the term is covered",
          E.court_covered(s, club_id=fx.club_id, user_id=m, starts_at=inside,
                          ends_at=inside + timedelta(hours=1), resource_id=fx.courts[0]) is True)
    check("a booking BEYOND the expiry is NOT covered (the leak)",
          E.court_covered(s, club_id=fx.club_id, user_id=m, starts_at=at(fx, 10),
                          ends_at=at(fx, 11), resource_id=fx.courts[0]) is False)
    # …and the money follows: it silently becomes a normal paid booking, never blocked.
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         settlement_mode="membership_covered",
                         starts_at=utc_iso(at(fx, 10)), ends_at=utc_iso(at(fx, 11)))
    check("the post-expiry booking still SUCCEEDS (a cap downgrades, never blocks)", r.get("ok"), str(r))
    o = _order_for_booking(s, r["booking"]["id"]) if r.get("ok") else None
    check("…billed PAYG R150, not R0", bool(o) and o["amount_minor"] == 15000, str(o))
    check("…and settled at-court, not membership_covered",
          bool(o) and o["settlement_mode"] == "at_court", str(o))


def sc_one_coach_one_place_at_a_time(s, fx):
    """The GiST exclusion constraint is keyed on resource_id, so it stops one COURT being taken
    twice — it says nothing about one PERSON. A class books no row on the coach's resource, and a
    court the coach books for themselves sits on the COURT's resource, so a coach could hold a court
    at 09:00 while ALSO delivering a lesson and running a class: three commitments, one human, no
    constraint violated. Only the lesson→class direction was ever guarded."""
    print("\n# One coach, one place at a time (court ↔ lesson ↔ class, both directions)")
    m = fx.members[0]
    # The coach is a club member too, so they can book a court like anyone else.
    s.execute(text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
                   "VALUES (:c,:u,'member','active')"), {"c": fx.club_id, "u": fx.coach_uid})
    # 1) LESSON at 10:00 → the coach may not also take a court then.
    les = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                           booking_type="lesson", resource_id=fx.coach_res,
                           coach_user_id=fx.coach_uid,
                           starts_at=utc_iso(at(fx, 10)), ends_at=utc_iso(at(fx, 11)))
    check("the coach's 10:00 lesson is booked", les.get("ok"), str(les))
    clash = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.coach_uid, role="member",
                             booking_type="court", resource_id=fx.courts[1],
                             starts_at=utc_iso(at(fx, 10)), ends_at=utc_iso(at(fx, 11)))
    check("coach can't book a court while delivering a lesson", clash.get("error") == "COACH_BUSY", str(clash))
    # A MEMBER booking that same court is of course fine — the guard is about the coach, not the court.
    ok_other = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[1], role="member",
                                booking_type="court", resource_id=fx.courts[1],
                                starts_at=utc_iso(at(fx, 10)), ends_at=utc_iso(at(fx, 11)))
    check("…but a member may still book that court at the same time", ok_other.get("ok"), str(ok_other))
    # 2) The reverse: a coach holding a COURT can't then be booked for a lesson.
    own = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.coach_uid, role="member",
                           booking_type="court", resource_id=fx.courts[0],
                           starts_at=utc_iso(at(fx, 13)), ends_at=utc_iso(at(fx, 14)))
    check("the coach books their own court at 13:00", own.get("ok"), str(own))
    les2 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                            booking_type="lesson", resource_id=fx.coach_res,
                            coach_user_id=fx.coach_uid,
                            starts_at=utc_iso(at(fx, 13)), ends_at=utc_iso(at(fx, 14)))
    check("a lesson over the coach's own court is refused", les2.get("error") == "COACH_BUSY", str(les2))
    # 3) A class the coach RUNS legitimately holds several courts — that must never read as a clash
    #    with itself (its rows are booking_type='class').
    C.schedule_sessions(s, club_id=fx.club_id, resource_id=fx.class_res,
                        dates=[fx.target.isoformat()], start_time="16:00",
                        duration_minutes=60, capacity=4,
                        court_resource_ids=[fx.courts[0], fx.courts[1]])
    held = s.execute(text("SELECT count(*) FROM diary.booking WHERE club_id=:c "
                          "AND booking_type='class' AND starts_at=:sa AND status IN ('held','confirmed')"),
                     {"c": fx.club_id, "sa": at(fx, 16)}).scalar()
    check("the coach's class holds BOTH courts (many courts, one commitment)", held == 2, f"held={held}")
    les3 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                            booking_type="lesson", resource_id=fx.coach_res,
                            coach_user_id=fx.coach_uid,
                            starts_at=utc_iso(at(fx, 16)), ends_at=utc_iso(at(fx, 17)))
    check("…but the coach still can't take a lesson during it", les3.get("error") == "COACH_BUSY", str(les3))


def sc_member_second_concurrent_court_is_payg(s, fx):
    """A member holding two courts at the same moment is legitimate (a doubles group, a family) —
    getting BOTH free is not. The membership covers the member, not every court they can reach at
    once, and nothing enforced that: the exclusion constraint is per-court, and the daily caps only
    count bookings, not concurrency."""
    print("\n# One member, one COVERED court at a time (the 2nd concurrent court is PAYG)")
    from diary import entitlement as E
    m = fx.members[0]
    _membership_for(s, fx, m, days=30)          # no caps at all — concurrency stands on its own
    r1 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                          booking_type="court", resource_id=fx.courts[0],
                          settlement_mode="membership_covered",
                          starts_at=utc_iso(at(fx, 10)), ends_at=utc_iso(at(fx, 11)))
    o1 = _order_for_booking(s, r1["booking"]["id"]) if r1.get("ok") else None
    check("the first covered court is free", bool(o1) and o1["amount_minor"] == 0, str(o1))
    check("a second OVERLAPPING court is no longer covered",
          E.court_covered(s, club_id=fx.club_id, user_id=m, starts_at=at(fx, 10),
                          ends_at=at(fx, 11), resource_id=fx.courts[1]) is False)
    r2 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                          booking_type="court", resource_id=fx.courts[1],
                          settlement_mode="membership_covered",
                          starts_at=utc_iso(at(fx, 10)), ends_at=utc_iso(at(fx, 11)))
    check("…the booking still SUCCEEDS (a doubles group isn't blocked)", r2.get("ok"), str(r2))
    o2 = _order_for_booking(s, r2["booking"]["id"]) if r2.get("ok") else None
    check("…it is simply charged PAYG R150", bool(o2) and o2["amount_minor"] == 15000, str(o2))
    # A NON-overlapping second booking later the same day is still covered (no cap configured).
    check("a later, non-overlapping court that day is still covered",
          E.court_covered(s, club_id=fx.club_id, user_id=m, starts_at=at(fx, 14),
                          ends_at=at(fx, 15), resource_id=fx.courts[0]) is True)


def sc_equipment_follows_its_own_payment_rule(s, fx):
    """Equipment rides the court's order, so where the court was FREE (membership_covered) or prepaid
    (token) the order was hard-coded to 'at_court' to collect the fee — assumed, never checked. A
    card-only club still got an owed pay-at-court debt for the ball machine, and because the booking
    status was decided from the COURT's free mode it confirmed instantly against an order nobody
    could collect. That is the "equipment always comes as pay at court" report."""
    print("\n# Equipment is a service: it obeys its own payment rule and can hold the booking")
    from admin import repositories as AR
    m = fx.members[0]
    _membership_for(s, fx, m, days=30)
    # A card-only ball machine.
    kit = AR.create_equipment(s, club_id=fx.club_id, name="Ball machine", amount_minor=8000,
                              quantity=1, payment_modes=["online"])
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         settlement_mode="membership_covered",
                         starts_at=utc_iso(at(fx, 10)), ends_at=utc_iso(at(fx, 11)),
                         addons=[{"resource_id": kit["id"], "qty": 1}])
    check("the booking succeeds", r.get("ok"), str(r))
    check("the server FLAGS that payment is still required", r.get("requires_payment") is True, str(r))
    check("the booking is HELD, not confirmed (it isn't paid for yet)",
          r.get("ok") and r["booking"]["status"] == "held", str(r.get("booking")))
    o = _order_for_booking(s, r["booking"]["id"]) if r.get("ok") else None
    check("the order takes the equipment's own method (online), not a assumed at-court",
          bool(o) and o["settlement_mode"] == "online", str(o))
    check("…and awaits payment rather than sitting owed", bool(o) and o["status"] == "awaiting_payment", str(o))
    check("…for the equipment fee only — the court stays free (R80)",
          bool(o) and o["amount_minor"] == 8000, str(o))
    # An item the club has no way to collect for is REFUSED, never granted unpaid (the pack rule).
    s.execute(text("UPDATE club.policy SET allow_monthly_account = false WHERE club_id = :c"),
              {"c": fx.club_id})
    kit2 = AR.create_equipment(s, club_id=fx.club_id, name="Court-side kit", amount_minor=5000,
                               quantity=1, payment_modes=["monthly_account"])
    bad = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                           booking_type="court", resource_id=fx.courts[1],
                           settlement_mode="membership_covered",
                           starts_at=utc_iso(at(fx, 12)), ends_at=utc_iso(at(fx, 13)),
                           addons=[{"resource_id": kit2["id"], "qty": 1}])
    check("equipment the club can't collect for is REFUSED, not handed over unpaid",
          bad.get("error") == "EQUIPMENT_NOT_PAYABLE", str(bad))


def sc_club_default_caps_cover_every_membership(s, fx):
    """The caps lived only on billing.price, so they applied only to a tier that HAD a price row. The
    signup trial usually doesn't, so trial members were entirely uncapped — and _best treated a NULL
    cap as "an unconstrained tier wins", so merely HOLDING the price-less trial alongside a capped
    paid tier wiped that tier's caps out too. Club defaults make "every membership is capped" one
    setting. Owner's rule: ONE covered booking a day, 90 minutes max; anything else is paid."""
    print("\n# Club default caps apply to EVERY membership — including the price-less trial")
    from billing.membership import grant_signup_trial
    from diary import entitlement as E
    s.execute(text("UPDATE club.policy SET default_max_covered_per_day = 1, "
                   "default_max_covered_minutes = 90 WHERE club_id = :c"), {"c": fx.club_id})
    # A brand-new member on the LEGACY trial (no trial tier configured → NULL price_id, no caps).
    newu = _mk_user(s, f"trialcap+{str(fx.club_id)[:8]}@scratch.test", "Trialist")
    g = grant_signup_trial(s, club_id=fx.club_id, user_id=newu, days=7)
    check("the legacy trial is granted", g.get("granted") is True, str(g))
    linked = s.execute(text("SELECT price_id FROM billing.membership_subscription "
                            "WHERE club_id=:c AND user_id=:u"), {"c": fx.club_id, "u": newu}).scalar()
    check("…with NO price row of its own (this is why per-tier caps missed it)", linked is None, str(linked))
    caps = E.active_caps(s, club_id=fx.club_id, user_id=newu)
    check("the trial INHERITS the club default: 1 booking/day", caps["max_covered_per_day"] == 1, str(caps))
    check("…and 90 covered minutes", caps["max_covered_minutes"] == 90, str(caps))
    # The rule, end to end: 90 min is covered, 120 is not, and the 2nd booking that day is not.
    check("a 90-min booking is covered",
          E.court_covered(s, club_id=fx.club_id, user_id=newu, starts_at=at(fx, 10),
                          ends_at=at(fx, 11, 30), resource_id=fx.courts[0]) is True)
    check("a 120-min booking is NOT (over the 90-min max)",
          E.court_covered(s, club_id=fx.club_id, user_id=newu, starts_at=at(fx, 10),
                          ends_at=at(fx, 12), resource_id=fx.courts[0]) is False)
    B.create_booking(s, club_id=fx.club_id, booked_by_user_id=newu, role="member",
                     booking_type="court", resource_id=fx.courts[0],
                     settlement_mode="membership_covered",
                     starts_at=utc_iso(at(fx, 10)), ends_at=utc_iso(at(fx, 11)))
    check("after ONE covered booking, a later one the same day is charged",
          E.court_covered(s, club_id=fx.club_id, user_id=newu, starts_at=at(fx, 14),
                          ends_at=at(fx, 15), resource_id=fx.courts[0]) is False)
    # THE TRAP: holding the price-less trial must not cancel a paid tier's caps.
    both = fx.members[2]
    _membership_for(s, fx, both, days=30, max_covered_per_day=1, max_covered_minutes=90)
    s.execute(text("INSERT INTO billing.membership_subscription (club_id, user_id, price_id, status, "
                   "provider, current_period_end) "
                   "VALUES (:c,:u,NULL,'active','trial',CURRENT_DATE + 7)"),
              {"c": fx.club_id, "u": both})
    caps2 = E.active_caps(s, club_id=fx.club_id, user_id=both)
    check("a price-less trial alongside a capped tier no longer wipes the caps",
          caps2["max_covered_per_day"] == 1 and caps2["max_covered_minutes"] == 90, str(caps2))


def sc_waitlist_promotion_into_a_cardonly_class_is_held(s, fx):
    """_bill_promoted_enrolment rewrote an 'online' intent to 'at_court' because an async promotion
    can't drive a checkout — but on a class the club sells CARD-ONLY that produced a confirmed seat
    and a "you're enrolled" email against an owed order nobody could collect, straight past the
    payment gate `enrol` enforces two functions up. The seat is now RESERVED pending payment, on the
    same lazy-expiry rails as an online self-enrolment."""
    print("\n# Waitlist promotion into a CARD-ONLY class holds the seat (never a free confirmed one)")
    C.schedule_sessions(s, club_id=fx.club_id, resource_id=fx.class_res,
                        dates=[fx.target.isoformat()], start_time="17:00",
                        duration_minutes=60, capacity=1)
    sid = s.execute(text("SELECT id FROM diary.class_session WHERE club_id=:c AND resource_id=:r "
                         "AND starts_at=:sa"),
                    {"c": fx.club_id, "r": fx.class_res, "sa": at(fx, 17)}).scalar()
    s.execute(text("UPDATE billing.product SET payment_modes='online' WHERE club_id=:c AND kind='class'"),
              {"c": fx.club_id})
    first, second = fx.members[0], fx.members[1]
    a = C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=first,
                settlement_mode="online", role="member")
    check("the only seat is taken", a.get("ok") and a.get("status_value") == "enrolled", str(a))
    b = C.enrol(s, club_id=fx.club_id, class_session_id=sid, user_id=second,
                settlement_mode="online", role="member")
    check("the next player is waitlisted", b.get("status_value") == "waitlisted", str(b))
    # Free the seat → the waitlister is promoted.
    with _Emits() as rec:
        C.cancel_enrolment(s, club_id=fx.club_id, class_session_id=sid, user_id=first)
    row = s.execute(text("SELECT e.status, e.settlement_mode, e.held_until, o.status AS ostatus, "
                         "       o.amount_minor "
                         'FROM diary.enrolment e LEFT JOIN billing."order" o ON o.id = e.order_id '
                         "WHERE e.class_session_id=:cs AND e.user_id=:u"),
                    {"cs": sid, "u": second}).mappings().first()
    check("the waitlister IS promoted into the seat", row and row["status"] == "enrolled", str(row))
    check("…but the seat is HELD pending payment, not confirmed", row and row["held_until"] is not None, str(row))
    check("…it kept the online method (never silently rewritten to at-court)",
          row and row["settlement_mode"] == "online", str(row))
    check("…its order awaits payment rather than sitting owed",
          row and row["ostatus"] == "awaiting_payment", str(row))
    check("…and it IS billed at the class rate R120", row and row["amount_minor"] == 12000, str(row))
    check("no 'you're enrolled' confirmation went out for an unpaid seat",
          "class_enrolled" not in rec.seen, str(rec.seen))
    check("…the player is told to pay instead", "class_seat_awaiting_payment" in rec.seen, str(rec.seen))
    # Paying it confirms the seat and releases the hold — the deferred confirmation fires HERE.
    oid = s.execute(text("SELECT order_id FROM diary.enrolment WHERE class_session_id=:cs AND user_id=:u"),
                    {"cs": sid, "u": second}).scalar()
    with _Emits() as rec2:
        C.confirm_paid_enrolments(s, club_id=fx.club_id, order_id=oid)
    after = s.execute(text("SELECT held_until FROM diary.enrolment WHERE class_session_id=:cs AND user_id=:u"),
                      {"cs": sid, "u": second}).mappings().first()
    check("payment clears the hold", after and after["held_until"] is None, str(after))
    check("…and NOW the enrolment confirmation is sent", "class_enrolled" in rec2.seen, str(rec2.seen))


def sc_one_lesson_flow(s, fx):
    """THE lesson flow — one shape, whoever books and however they pay.

    A lesson used to take four shapes depending on the settlement mode and the coach's review flag,
    and WHO GOT TOLD depended on the same two flags: only a gated booking emailed the coach directly,
    everywhere else he was a blind copy or nothing at all, and a client who made a request got no
    email whatsoever. Same act, four answers.

    Now: every lesson holds the coach AND a court, the settlement alone decides held vs confirmed,
    and the coach is told EXACTLY ONCE per lesson — including for the online one, which confirms
    later in the payment path and would otherwise be the one he never heard about."""
    print("\n# ONE lesson flow: same shape every mode; the coach is told exactly once")
    from billing.events import apply_payment_event
    from billing.gateway import NormalizedPaymentEvent
    m = fx.members[0]
    # Review ON throughout — it must make no difference to the shape any more.
    s.execute(text("UPDATE iam.coach_profile SET review_bookings = true "
                   "WHERE club_id=:c AND user_id=:u"), {"c": fx.club_id, "u": fx.coach_uid})

    def _book(hour, mode):
        return B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                                booking_type="lesson", resource_id=fx.coach_res,
                                coach_user_id=fx.coach_uid, settlement_mode=mode,
                                starts_at=utc_iso(at(fx, hour)), ends_at=utc_iso(at(fx, hour + 1)))

    def _has_court(order_id):
        return bool(s.execute(text("SELECT 1 FROM diary.booking WHERE order_id=:o "
                                   "AND booking_type='court'"), {"o": order_id}).first())

    # ---- shape is identical across settlement modes -----------------------------------------
    owed = _book(9, "at_court")
    check("at-court → confirmed, court held, order raised",
          owed["booking"]["status"] == "confirmed" and _has_court(owed["booking"]["order_id"]),
          str(owed["booking"]["status"]))
    online = _book(11, "online")
    check("online → HELD pending payment, court held, order raised",
          online["booking"]["status"] == "held" and _has_court(online["booking"]["order_id"]),
          str(online["booking"]["status"]))
    check("…and BOTH reserve the coach (no unreserved lesson state exists)",
          not has_slot(lesson_slots(s, fx), at(fx, 9)) and not has_slot(lesson_slots(s, fx), at(fx, 11)))

    # ---- the coach is told ONCE per lesson, on both paths ------------------------------------
    with _Emits() as rec:
        paid_owed = _book(13, "at_court")
    check("an OWED lesson notifies the coach at booking",
          rec.seen.count("lesson_booked") == 1, str(rec.seen))
    check("…exactly once (not once per linked row — a lesson is coach + court)",
          rec.seen.count("lesson_booked") == 1, str(rec.seen))

    with _Emits() as rec2:
        pend = _book(15, "online")
    check("an ONLINE lesson does NOT notify the coach before it's paid",
          "lesson_booked" not in rec2.seen, str(rec2.seen))
    with _Emits() as rec3:
        apply_payment_event(NormalizedPaymentEvent(
            provider="yoco", kind="charge_succeeded", order_ref=str(pend["booking"]["order_id"]),
            provider_payment_id="p_flow_1", amount_minor=40000, currency="ZAR", status="succeeded",
            direction="charge", club_id=str(fx.club_id), user_id=str(m)), session=s)
    check("…it notifies him WHEN THE PAYMENT LANDS (the one he used to never hear about)",
          rec3.seen.count("lesson_booked") == 1, str(rec3.seen))
    check("…and the lesson is now confirmed",
          _booking_row(s, pend["booking"]["id"])["status"] == "confirmed")

    # ---- a coach cancelling a PAID lesson gives the money back --------------------------------
    paid = _book(17, "online")
    apply_payment_event(NormalizedPaymentEvent(
        provider="yoco", kind="charge_succeeded", order_ref=str(paid["booking"]["order_id"]),
        provider_payment_id="p_flow_2", amount_minor=40000, currency="ZAR", status="succeeded",
        direction="charge", club_id=str(fx.club_id), user_id=str(m)), session=s)
    cancelled = B.cancel_booking(s, club_id=fx.club_id, booking_id=paid["booking"]["id"],
                                 actor_user_id=fx.coach_uid, role="coach", reason="coach unavailable")
    check("the coach can cancel it", cancelled.get("ok"), str(cancelled))
    check("…and a paid lesson REFUNDS itself (decline used to do this; cancel is now the way)",
          cancelled.get("refunds") is not None, str(cancelled.get("refunds")))

    # A CLIENT cancelling their own paid lesson is NOT auto-refunded — that is a request decided
    # under the cancellation policy, not money handed back automatically.
    mine = _book(19, "online")
    apply_payment_event(NormalizedPaymentEvent(
        provider="yoco", kind="charge_succeeded", order_ref=str(mine["booking"]["order_id"]),
        provider_payment_id="p_flow_3", amount_minor=40000, currency="ZAR", status="succeeded",
        direction="charge", club_id=str(fx.club_id), user_id=str(m)), session=s)
    own = B.cancel_booking(s, club_id=fx.club_id, booking_id=mine["booking"]["id"],
                           actor_user_id=m, role="member")
    check("a CLIENT's own cancellation is not auto-refunded", own.get("refunds") is None,
          str(own.get("refunds")))
    check("…but it is flagged as paid so the club is prompted", own.get("was_paid") is True)
    s.execute(text("UPDATE iam.coach_profile SET review_bookings = false "
                   "WHERE club_id=:c AND user_id=:u"), {"c": fx.club_id, "u": fx.coach_uid})


def sc_paying_is_the_acceptance(s, fx):
    """PAYING FOR IT IS THE ACCEPTANCE. An online lesson is never gated, whatever the coach's review
    setting — which fixes two things at once.

    A gated ('requested') lesson created no order, so a client booking a CARD-ONLY coach was never
    sent to Yoco (the client needs an order_id to reach checkout). She got a success screen, paid
    nothing, and had no way to pay at all. Worse, a request reserves NOTHING, so two clients could
    each request — and each pay for — the same 18:00 slot, leaving the coach to take one and the club
    to refund the other.

    A confirmed booking holds the coach AND the court through the exclusion constraint, so the second
    client is simply told the slot is taken. And the gate was never buying control anyway: a coach can
    RESCHEDULE, so a time that doesn't suit is moved, not refused. An OWED request (at-court/monthly)
    is still gated — that is the case the review setting was actually for."""
    print("\n# Paying IS the acceptance: an online lesson is never gated, and holds its slot")
    from billing.events import apply_payment_event
    from billing.gateway import NormalizedPaymentEvent
    m, m2 = fx.members[0], fx.members[1]
    s.execute(text("UPDATE iam.coach_profile SET review_bookings = true "
                   "WHERE club_id = :c AND user_id = :u"), {"c": fx.club_id, "u": fx.coach_uid})
    prod = s.execute(text("INSERT INTO billing.product (club_id, kind, name, active, coach_user_id, "
                          "payment_modes) VALUES (:c,'lesson','Private (card only)',true,:u,'online') "
                          "RETURNING id"), {"c": fx.club_id, "u": fx.coach_uid}).scalar()
    s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                   "currency_code, duration_minutes, active) "
                   "VALUES (:c,:p,'any',55000,'ZAR',60,true)"), {"c": fx.club_id, "p": prod})

    def _book(who, hour, mode="online"):
        return B.create_booking(s, club_id=fx.club_id, booked_by_user_id=who, role="member",
                                booking_type="lesson", resource_id=fx.coach_res,
                                coach_user_id=fx.coach_uid, product_id=str(prod),
                                settlement_mode=mode,
                                starts_at=utc_iso(at(fx, hour)), ends_at=utc_iso(at(fx, hour + 1)))

    # (1) The coach REVIEWS bookings — and an online booking is still not gated.
    r = _book(m, 9)
    bk = r.get("booking") or {}
    check("an online lesson with a reviewing coach is NOT 'requested'",
          r.get("ok") and bk.get("status") == "held", str(r))
    check("…it carries an order to pay immediately", bool(bk.get("order_id")), str(bk))
    check("…and a COURT is held with it", bool(s.execute(
        text("SELECT 1 FROM diary.booking WHERE order_id=:o AND booking_type='court'"),
        {"o": bk["order_id"]}).first()))

    # (2) THE SLOT IS TAKEN. This is what a 'requested' booking could never do.
    clash = _book(m2, 9)
    # Either refusal is correct and both mean "that slot is gone": the one-person-one-place guard
    # (COACH_BUSY) usually fires a step before the exclusion constraint (SLOT_TAKEN) would. Asserting
    # the exact code would pin an implementation detail rather than the invariant that matters.
    check("a SECOND client cannot take the same slot",
          not clash.get("ok") and clash.get("error") in ("SLOT_TAKEN", "COACH_BUSY"), str(clash))
    check("…and nothing of theirs persisted",
          s.execute(text("SELECT COUNT(*) FROM diary.booking WHERE club_id=:c AND booked_by_user_id=:u"),
                    {"c": fx.club_id, "u": m2}).scalar() == 0)

    # (3) Paying confirms it outright — no coach step, nothing left to lapse.
    apply_payment_event(NormalizedPaymentEvent(
        provider="yoco", kind="charge_succeeded", order_ref=str(bk["order_id"]),
        provider_payment_id="p_paid_1", amount_minor=55000, currency="ZAR", status="succeeded",
        direction="charge", club_id=str(fx.club_id), user_id=str(m)), session=s)
    row = _booking_row(s, bk["id"])
    check("paying CONFIRMS the lesson", row["status"] == "confirmed", str(dict(row)))
    check("…and the order is settled",
          s.execute(text('SELECT status FROM billing."order" WHERE id=:o'),
                    {"o": bk["order_id"]}).scalar() == "paid")

    # (4) ONE FLOW — an OWED booking with the same reviewing coach behaves identically in shape:
    # confirmed, slot held, order raised. Only the settlement differs, which is the whole point.
    s.execute(text("UPDATE billing.product SET payment_modes = NULL WHERE id = :p"), {"p": prod})
    owed = _book(m, 12, mode="at_court")
    check("an at-court booking with the same reviewing coach is ALSO confirmed",
          owed.get("ok") and owed["booking"]["status"] == "confirmed", str(owed))
    check("…and raises an OWED order (settlement differs, the shape does not)",
          bool(owed["booking"].get("order_id"))
          and s.execute(text('SELECT status FROM billing."order" WHERE id=:o'),
                        {"o": owed["booking"]["order_id"]}).scalar() == "open",
          str(owed["booking"]))


def _booking_row(s, booking_id):
    return s.execute(text("SELECT status, order_id FROM diary.booking WHERE id=:b"),
                     {"b": booking_id}).mappings().first()


def sc_peak_hours_can_differ_per_court(s, fx):
    """Peak was ONE club-wide window, so "peak on the show courts only" was unexpressible — every
    court shared it. The peak AMOUNT was already per service+duration; only the WINDOW was global.

    Three states, and the third is why `peak_override` exists at all: a nullable window can only ever
    ADD peak to a court, never REMOVE it, which is half the requirement. And because the availability
    grid and create_booking price independently, both must consult the SAME window — or the grid
    quotes one number and the order charges another."""
    print("\n# Peak hours per COURT: inherit the club · own window · never peak")
    from diary import pricing as P
    m = fx.members[0]
    # Club-wide peak 17:00–19:00 Mon–Sun, and a court service with a peak amount.
    s.execute(text("UPDATE club.policy SET peak_days = NULL, peak_start_min = 1020, "
                   "peak_end_min = 1140 WHERE club_id = :c"), {"c": fx.club_id})
    s.execute(text("UPDATE billing.price SET peak_amount_minor = 25000 "
                   "WHERE club_id = :c AND product_id = :p AND duration_minutes = 60"),
              {"c": fx.club_id, "p": fx.court_product})
    inherit, own, never = fx.courts[0], fx.courts[1], s.execute(
        text("INSERT INTO diary.resource (club_id, kind, name, surface, rank) "
             "VALUES (:c,'court','Back Court','hard',9) RETURNING id"), {"c": fx.club_id}).scalar()
    # A court with no availability_rule emits no slots at all, so the grid half of shown==charged
    # would read None for a reason unrelated to peak. Give the new court the fixture's own hours.
    s.execute(text("INSERT INTO diary.availability_rule (club_id, resource_id, weekday, start_time, "
                   "end_time, slot_minutes) VALUES (:c,:r,:wd,'08:00','20:00',60)"),
              {"c": fx.club_id, "r": never, "wd": fx.target.weekday()})
    # `own` peaks EARLIER (07:00–09:00) and not at the club's time; `never` opts out entirely.
    s.execute(text("UPDATE diary.resource SET peak_override = true, peak_days = NULL, "
                   "peak_start_min = 420, peak_end_min = 540 WHERE id = :r"), {"r": own})
    s.execute(text("UPDATE diary.resource SET peak_override = true, peak_days = NULL, "
                   "peak_start_min = NULL, peak_end_min = NULL WHERE id = :r"), {"r": never})
    # Peak is cached per session, so a scenario that CHANGES peak config has to drop it or it reads
    # whatever an earlier scenario resolved. This used to pass only because nothing had cached this
    # club yet — an ordering dependency, not a guarantee.
    P.clear_peak_cache(s)

    def peak_at(court, hour):
        return P.in_peak_window(s, club_id=fx.club_id, local_dt=at(fx, hour), resource_id=court)

    check("inheriting court: peak at 17:00 (the club window)", peak_at(inherit, 17) is True)
    check("inheriting court: NOT peak at 08:00", peak_at(inherit, 8) is False)
    check("own-window court: peak at 08:00 (its own)", peak_at(own, 8) is True)
    check("own-window court: NOT peak at 17:00 (the club's time doesn't apply)",
          peak_at(own, 17) is False)
    check("never-peak court: NOT peak at 17:00 even though the CLUB is",
          peak_at(never, 17) is False)
    check("never-peak court: NOT peak at 08:00 either", peak_at(never, 8) is False)

    # SHOWN == CHARGED. The grid prices per slot; the order prices at booking. Same window, or the
    # member is quoted one number and billed another.
    def shown(court, hour):
        # The slot key is "start" (not starts_at) and the figure is "price" — see
        # diary.availability's slot dict. Reuses the harness's own court_slots helper.
        for sl in court_slots(s, fx, court, hour_from=hour, hour_to=hour + 1):
            if sl["start"] == utc_iso(at(fx, hour)):
                return sl.get("price")
        return None

    def charged(court, hour):
        r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                             booking_type="court", resource_id=court,
                             starts_at=utc_iso(at(fx, hour)), ends_at=utc_iso(at(fx, hour + 1)),
                             settlement_mode="at_court")
        if not r.get("ok"):
            return None
        return s.execute(text('SELECT o.amount_minor FROM billing."order" o '
                              'JOIN billing.order_line ol ON ol.order_id=o.id '
                              'WHERE ol.booking_id=:b'), {"b": r["booking"]["id"]}).scalar()

    # Read the grid BEFORE booking — the booking occupies the slot, so afterwards there is nothing
    # left to price and `shown` would report None for reasons that have nothing to do with peak.
    shown_own_8, shown_never_17 = shown(own, 8), shown(never, 17)
    check("own-window court at 08:00 is CHARGED the peak R250", charged(own, 8) == 25000)
    check("…and the grid SHOWED peak there too", shown_own_8 == 25000, str(shown_own_8))
    check("never-peak court at 17:00 is CHARGED the base R150", charged(never, 17) == 15000)
    check("…and the grid SHOWED base there too", shown_never_17 == 15000, str(shown_never_17))
    check("inheriting court at 17:00 is CHARGED peak R250", charged(inherit, 17) == 25000)


def sc_a_tier_can_be_free_except_at_peak(s, fx):
    """The free-week problem: trialists were booking prime-time courts for nothing.

    The club does NOT want to blank out prime time for them — just to charge for it. Expressing that
    with the ACCESS WINDOW would mean hand-maintaining the INVERSE of peak ("everything except Mon–Thu
    17:00–19:00 and Sat 08:00–10:00") in a second place, per tier. It would be wrong the first time
    peak moved, and the failure is SILENT: trialists simply start playing free at peak again.

    So the tier states what it means — `covers_peak = false` — and coverage consults whatever peak is
    configured, for the COURT being booked, at the moment of the booking. Peak moves, this follows.

    The court matters: peak is per court here, so a tier excluded at peak is still free on a court
    that has no peak at all (clay, in this club)."""
    print("\n# A membership tier can cover everything EXCEPT peak (the trial's prime-time rule)")
    from diary import pricing as P
    from diary import entitlement as E
    from billing.membership import membership_product_id
    peaky, never_peak = fx.courts[0], fx.courts[1]

    # Club peak 17:00–19:00 every day; `never_peak` opts out entirely (this club's clay court).
    s.execute(text("UPDATE club.policy SET peak_days = NULL, peak_start_min = 1020, "
                   "peak_end_min = 1140 WHERE club_id = :c"), {"c": fx.club_id})
    s.execute(text("UPDATE diary.resource SET peak_override = true, peak_days = NULL, "
                   "peak_start_min = NULL, peak_end_min = NULL WHERE id = :r"), {"r": never_peak})
    P.clear_peak_cache(s)

    mprod = membership_product_id(s, club_id=fx.club_id, create_if_missing=True)
    trial_user, anytime_user = fx.members[1], fx.members[2]

    def _tier(name, covers_peak):
        return s.execute(
            text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                 "currency_code, unit, term_months, membership_tier, active, covers_peak) "
                 "VALUES (:c,:p,'member',0,'ZAR','per_month',1,:t,true,:cp) RETURNING id"),
            {"c": fx.club_id, "p": mprod, "t": name, "cp": covers_peak}).scalar()

    def _subscribe(uid, price_id):
        s.execute(text("INSERT INTO billing.membership_subscription (club_id, user_id, price_id, "
                       "status, provider, current_period_end) "
                       "VALUES (:c,:u,:pr,'active','trial',CURRENT_DATE+30)"),
                  {"c": fx.club_id, "u": uid, "pr": price_id})

    _subscribe(trial_user, _tier("Free Trial", False))
    _subscribe(anytime_user, _tier("Anytime", True))

    def covered(uid, court, hour):
        # DATETIMES, not ISO strings: membership_covers calls .astimezone on starts_at, and the
        # guard turns the resulting AttributeError into a bare False — so a string argument makes
        # every assertion here "not covered" for a reason that has nothing to do with peak.
        return E.court_covered(s, club_id=fx.club_id, user_id=uid, resource_id=court,
                               starts_at=at(fx, hour), ends_at=at(fx, hour + 1))

    check("the trial covers an OFF-peak court — the free week still works",
          covered(trial_user, peaky, 10) is True)
    check("…but NOT a peak one (the whole point)", covered(trial_user, peaky, 17) is False)
    check("…so a peak court is simply PAYG for them, never blocked",
          covered(trial_user, peaky, 18) is False)
    check("the boundary is the window's: 19:00 is out of peak and covered again",
          covered(trial_user, peaky, 19) is True)

    # PER COURT. A tier excluded at peak is still free on a court with no peak window at all.
    check("the trial IS still free at 17:00 on a court that has no peak (clay here)",
          covered(trial_user, never_peak, 17) is True)

    # And it is per SUBSCRIPTION, not a club-wide switch — a paying anytime member is unaffected.
    check("a normal anytime membership still covers peak", covered(anytime_user, peaky, 17) is True)
    check("…and off-peak too, obviously", covered(anytime_user, peaky, 10) is True)

    # The default has to be "covers peak", or adding this column would silently have started
    # charging every existing member at peak.
    dflt = s.execute(text("SELECT covers_peak FROM billing.price WHERE club_id = :c "
                          "  AND membership_tier = 'Anytime' LIMIT 1"), {"c": fx.club_id}).scalar()
    check("covers_peak defaults TRUE, so no existing tier changed behaviour", dflt is True)


def sc_peak_can_have_more_than_one_window(s, fx):
    """A real club's peak is not one window. NextPoint's is weekday EVENINGS (Mon–Thu 17:00–19:00)
    AND Saturday MORNING (08:00–10:00) — two different time ranges on two different day sets.

    The single peak_days/peak_start_min/peak_end_min triple could hold only one of them, so the owner
    had to choose which half of their peak to charge for. The screen looked correctly configured the
    whole time: it showed "peak 17:00–19:00 · Mon…Sun", which under-charged nothing visibly and
    over-charged Fri/Sat/Sun evenings while Saturday morning — their busiest — sold at off-peak.

    The legacy columns are NOT migrated, so this also pins the fallback: a scope with no
    diary.peak_window rows still resolves its old single window exactly as before."""
    print("\n# Peak is a LIST of windows: Mon–Thu evenings AND Sat mornings, on one court")
    from diary import pricing as P
    court = fx.courts[0]

    # LEGACY FIRST: the club's old single window still resolves while it has no rows of its own.
    s.execute(text("UPDATE club.policy SET peak_days = NULL, peak_start_min = 1020, "
                   "peak_end_min = 1140 WHERE club_id = :c"), {"c": fx.club_id})
    P.clear_peak_cache(s)
    check("a club with no peak_window rows still uses its legacy single window",
          P.in_peak_window(s, club_id=fx.club_id, local_dt=at(fx, 17), resource_id=court) is True)

    # NOW the real shape: Mon–Thu 17:00–19:00 AND Sat 08:00–10:00, as club-level defaults.
    for days, a, b in (("1,2,3,4", 1020, 1140), ("6", 480, 600)):
        s.execute(text("INSERT INTO diary.peak_window (club_id, resource_id, days, start_min, end_min) "
                       "VALUES (:c, NULL, :d, :a, :b)"),
                  {"c": fx.club_id, "d": days, "a": a, "b": b})
    P.clear_peak_cache(s)

    def peak_on(iso_dow, hour, minute=0):
        """A club-local datetime on a KNOWN weekday — the fixture's test day can be any day, so a
        day-of-week rule asserted against it would pass or fail on when the suite happens to run."""
        base = at(fx, hour, minute)
        return P.in_peak_window(s, club_id=fx.club_id, resource_id=court,
                                local_dt=base + timedelta(days=(iso_dow - base.isoweekday()) % 7))

    check("Tuesday 18:00 is peak (the weekday-evening window)", peak_on(2, 18) is True)
    check("Thursday 17:00 is peak — the window is inclusive at the start", peak_on(4, 17) is True)
    check("Saturday 09:00 is peak (the SECOND window — this is what could not be expressed)",
          peak_on(6, 9) is True)
    check("…and BOTH windows are live at once, not just the first one found",
          peak_on(2, 18) is True and peak_on(6, 9) is True)
    # The gaps matter as much as the hits — an over-charge is as wrong as an under-charge.
    check("Friday 18:00 is NOT peak (it was, wrongly, when one window covered all seven days)",
          peak_on(5, 18) is False)
    check("Saturday 18:00 is NOT peak — Saturday's window is the MORNING one", peak_on(6, 18) is False)
    check("Tuesday 09:00 is NOT peak — the morning window is Saturdays only", peak_on(2, 9) is False)
    check("Thursday 19:00 is NOT peak — end is exclusive", peak_on(4, 19) is False)

    # A COURT that overrides still wins, and its own rows beat the club's.
    s.execute(text("UPDATE diary.resource SET peak_override = true, peak_days = NULL, "
                   "peak_start_min = NULL, peak_end_min = NULL WHERE id = :r"), {"r": court})
    P.clear_peak_cache(s)
    check("a court that overrides with NO windows of its own is never peak — the club's two "
          "windows do not leak back in", peak_on(2, 18) is False and peak_on(6, 9) is False)


def sc_peak_survives_a_reschedule(s, fx):
    """A RESCHEDULE re-priced at the BASE amount, whatever the new time — so moving a booking INTO a
    peak window under-charged it, permanently and silently.

    Two separate defects, and the second is the one that made it unreachable by testing the first:
      (1) `reprice_booking_order` took `duration_minutes` only and selected `p2.amount_minor` — the
          off-peak column. It never read `peak_amount_minor` and never asked whether the new time was
          peak. It did not "keep the original band"; peak was dropped entirely.
      (2) The CALL only fired when the DURATION changed. Moving a 60-min court from 10:00 to 18:00 is
          the same length, so nothing re-priced at all — the commonest move of the lot.
    And now that the peak WINDOW is per court, a court SWAP changes the price at an unchanged time
    too, so that must trigger it as well."""
    print("\n# Peak survives a reschedule: into peak, out of peak, and across courts")
    from billing.orders import reprice_booking_order          # noqa: F401  (import proves it loads)
    m = fx.members[1]
    BASE, PEAK = 15000, 25000

    # Club peak 17:00-19:00. `plain` inherits it; `early` peaks 07:00-09:00 instead (per-court).
    s.execute(text("UPDATE club.policy SET peak_days = NULL, peak_start_min = 1020, "
                   "peak_end_min = 1140 WHERE club_id = :c"), {"c": fx.club_id})
    s.execute(text("UPDATE billing.price SET peak_amount_minor = :pk "
                   "WHERE club_id = :c AND product_id = :p AND duration_minutes = 60"),
              {"pk": PEAK, "c": fx.club_id, "p": fx.court_product})
    plain = fx.courts[0]
    early = s.execute(
        text("INSERT INTO diary.resource (club_id, kind, name, surface, rank) "
             "VALUES (:c,'court','Early Court','hard',11) RETURNING id"), {"c": fx.club_id}).scalar()
    s.execute(text("INSERT INTO diary.availability_rule (club_id, resource_id, weekday, start_time, "
                   "end_time, slot_minutes) VALUES (:c,:r,:wd,'06:00','21:00',60)"),
              {"c": fx.club_id, "r": early, "wd": fx.target.weekday()})
    s.execute(text("UPDATE diary.resource SET peak_override = true, peak_days = NULL, "
                   "peak_start_min = 420, peak_end_min = 540 WHERE id = :r"), {"r": early})
    # Same reason as the per-court scenario: peak is cached per session, so changing it here without
    # dropping the cache reads whatever an earlier scenario resolved for this club.
    from diary import pricing as _P
    _P.clear_peak_cache(s)

    def book(court, hour, mins=60):
        st = at(fx, hour)
        r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                             booking_type="court", resource_id=court,
                             starts_at=utc_iso(st), ends_at=utc_iso(st + timedelta(minutes=mins)),
                             settlement_mode="at_court")
        assert r.get("ok"), str(r)
        return r["booking"]["id"]

    def amount(bid):
        return s.execute(text('SELECT o.amount_minor FROM billing."order" o '
                              'JOIN billing.order_line ol ON ol.order_id=o.id '
                              'WHERE ol.booking_id=:b'), {"b": bid}).scalar()

    def move(bid, hour, mins=60, court=None):
        st = at(fx, hour)
        return B.reschedule_booking(
            s, club_id=fx.club_id, booking_id=bid,
            new_starts_at=utc_iso(st), new_ends_at=utc_iso(st + timedelta(minutes=mins)),
            actor_user_id=m, role="club_admin", new_court_resource_id=court)

    # --- (1) THE BUG: move OFF-PEAK -> PEAK at the SAME duration ---------------------------
    b1 = book(plain, 10)
    check("an off-peak 10:00 court is billed the base R150", amount(b1) == BASE, str(amount(b1)))
    r1 = move(b1, 17)
    check("it reschedules into the 17:00 peak window", r1.get("ok"), str(r1))
    check("...and is NOW CHARGED PEAK R250 (it silently stayed R150 — the leak)",
          amount(b1) == PEAK, str(amount(b1)))

    # --- (2) and the reverse: PEAK -> OFF-PEAK must come back DOWN --------------------------
    b2 = book(plain, 18)
    check("a 18:00 court is billed peak R250", amount(b2) == PEAK, str(amount(b2)))
    check("it moves to 11:00", move(b2, 11).get("ok"))
    check("...and drops back to the base R150 (a stuck peak over-charges just as badly)",
          amount(b2) == BASE, str(amount(b2)))

    # --- (3) THE COURT decides the window, so a court SWAP re-prices at an unchanged time ----
    b3 = book(plain, 8)                                   # 08:00: off-peak on `plain`...
    check("08:00 on the club-window court is base R150", amount(b3) == BASE, str(amount(b3)))
    r3 = move(b3, 8, court=early)                         # ...but PEAK on `early` (07:00-09:00)
    check("it moves to the early-peak court at the SAME time", r3.get("ok"), str(r3))
    check("...and is charged that COURT's peak R250 (the window follows the court)",
          amount(b3) == PEAK, str(amount(b3)))

    # --- (4) REGRESSION: a duration change still re-prices (the original purpose) -----------
    # The scratch fixture prices ONLY 60 min, and reprice deliberately no-ops on an unpriced
    # duration ("never guess a price") - so give the product a real 30-min row to move onto.
    s.execute(text("UPDATE billing.price SET peak_amount_minor = NULL "
                   "WHERE club_id = :c AND product_id = :p"), {"c": fx.club_id, "p": fx.court_product})
    s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                   "currency_code, duration_minutes, active) "
                   "VALUES (:c, :p, 'any', 9000, 'ZAR', 30, true)"),
              {"c": fx.club_id, "p": fx.court_product})
    b4 = book(plain, 13, mins=60)
    check("a 60-min booking is priced at R150", amount(b4) == BASE, str(amount(b4)))
    check("it shortens to 30 min", move(b4, 13, mins=30).get("ok"))
    check("...and re-prices to the 30-min rate R90 (the original purpose, unchanged)",
          amount(b4) == 9000, str(amount(b4)))

    # --- (5) REGRESSION: a PAID order is never re-priced by a move --------------------------
    s.execute(text("UPDATE billing.price SET peak_amount_minor = :pk "
                   "WHERE club_id = :c AND product_id = :p AND duration_minutes = 60"),
              {"pk": PEAK, "c": fx.club_id, "p": fx.court_product})
    b5 = book(plain, 15)
    oid = s.execute(text("SELECT order_id FROM billing.order_line WHERE booking_id=:b"),
                    {"b": b5}).scalar()
    # BOTH the order status AND a real succeeded charge. `_order_has_succeeded_charge` (the
    # extend guard) and reprice's own guard each read billing.payment, so a status-only flip is
    # not a paid booking to either of them.
    s.execute(text('UPDATE billing."order" SET status = \'paid\' WHERE id = :o'), {"o": oid})
    paid_before = amount(b5)
    s.execute(text("INSERT INTO billing.payment (club_id, order_id, provider, provider_payment_id, "
                   "amount_minor, currency_code, direction, status) "
                   "VALUES (:c, :o, 'yoco', 'p_peak_regression', :a, 'ZAR', 'charge', 'succeeded')"),
              {"c": fx.club_id, "o": oid, "a": paid_before})
    # 18:00, not 17:00 — step (1) already parked a booking on this court at 17:00, so that move
    # would refuse SLOT_TAKEN and prove nothing about pricing. 18:00 is inside the same peak window.
    check("the paid booking moves into peak (same length, so not an extend)",
          move(b5, 18).get("ok"))
    check("...but a SETTLED order is NOT re-priced (that needs a refund, not a silent re-charge)",
          amount(b5) == paid_before, str(amount(b5)))


def sc_trial_obeys_the_same_court_rules_as_a_membership(s, fx):
    """"Is clay included in the free trial?" — the trial is not a special case in the pricing engine.
    It IS a membership (provider='trial'), so it goes through the SAME resolver as a paid one: the
    court service's `members_covered` flag, the duration/day caps, the access window. Turning clay
    off for members turns it off for trialists too, with no separate setting and no code change.

    Worth pinning explicitly because it's the one people assume must be special-cased — and if it
    ever were, a club would be giving its most expensive courts away to every new signup."""
    print("\n# The trial is a MEMBERSHIP: clay exclusion + caps apply to it identically")
    from billing.membership import grant_signup_trial
    from diary import entitlement as E
    # Two court services: hardcourt covered for members, clay explicitly PAYG-only.
    hard = s.execute(text("INSERT INTO billing.product (club_id, kind, name, active, members_covered) "
                          "VALUES (:c,'court_booking','Hard Hire',true,true) RETURNING id"),
                     {"c": fx.club_id}).scalar()
    clay = s.execute(text("INSERT INTO billing.product (club_id, kind, name, active, members_covered) "
                          "VALUES (:c,'court_booking','Clay Hire',true,false) RETURNING id"),
                     {"c": fx.club_id}).scalar()
    for pid, amt in ((hard, 15000), (clay, 28000)):
        s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                       "currency_code, duration_minutes, active) "
                       "VALUES (:c,:p,'any',:a,'ZAR',60,true)"), {"c": fx.club_id, "p": pid, "a": amt})
    s.execute(text("UPDATE diary.resource SET product_id=:p WHERE id=:r"),
              {"p": hard, "r": fx.courts[0]})
    clay_court = s.execute(text("INSERT INTO diary.resource (club_id, kind, name, surface, rank, "
                                "product_id) VALUES (:c,'court','Clay 1','clay',9,:p) RETURNING id"),
                           {"c": fx.club_id, "p": clay}).scalar()

    newu = _mk_user(s, f"trialclay+{str(fx.club_id)[:8]}@scratch.test", "Trialist")
    g = grant_signup_trial(s, club_id=fx.club_id, user_id=newu, days=7)
    check("a brand-new member gets the trial", g.get("granted") is True, str(g))

    check("the trial DOES cover a members-covered hard court",
          E.court_covered(s, club_id=fx.club_id, user_id=newu, starts_at=at(fx, 10),
                          ends_at=at(fx, 11), resource_id=fx.courts[0]) is True)
    check("…and does NOT cover a PAYG-only clay court (members_covered=false)",
          E.court_covered(s, club_id=fx.club_id, user_id=newu, starts_at=at(fx, 10),
                          ends_at=at(fx, 11), resource_id=clay_court) is False)

    # End to end: the trialist's clay booking is CHARGED, not blocked.
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=newu, role="member",
                         booking_type="court", resource_id=clay_court, product_id=str(clay),
                         settlement_mode="membership_covered",
                         starts_at=utc_iso(at(fx, 10)), ends_at=utc_iso(at(fx, 11)))
    check("the clay booking still SUCCEEDS (never blocked, just billed)", r.get("ok"), str(r))
    o = s.execute(text('SELECT o.amount_minor, o.settlement_mode FROM billing."order" o '
                       'JOIN billing.order_line ol ON ol.order_id=o.id WHERE ol.booking_id=:b'),
                  {"b": r["booking"]["id"]}).mappings().first() if r.get("ok") else None
    check("…and is billed at the clay rate R280, at-court", bool(o) and o["amount_minor"] == 28000
          and o["settlement_mode"] == "at_court", str(o))

    # The club-wide cap reaches the trial exactly as it reaches a paid tier.
    s.execute(text("UPDATE club.policy SET default_max_covered_minutes = 90 WHERE club_id = :c"),
              {"c": fx.club_id})
    check("a 2-hour hard court is over the club cap for a TRIALIST too",
          E.court_covered(s, club_id=fx.club_id, user_id=newu, starts_at=at(fx, 12),
                          ends_at=at(fx, 14), resource_id=fx.courts[0]) is False)


def sc_equipment_court_is_charged_and_both_are_booked_out(s, fx):
    """THE equipment invariant, end to end: the COURT is charged unless a membership genuinely covers
    it, and BOTH the court and the kit are reserved so neither can be taken twice.

    Reported as "she hired 2 racquets for R100 and got a 2-hour court free". The court was free
    because she held a MEMBERSHIP — not an equipment fault — but the shapes are indistinguishable
    from the order alone, so all three are pinned here: PAYG pays for the court, a covered member
    doesn't, and a covered member OVER THE CAP does."""
    print("\n# Equipment: the court is still charged (unless covered), and both are booked out")
    from admin import repositories as AR
    from diary.equipment import available_units
    m = fx.members[0]
    court = fx.courts[0]
    kit = AR.create_equipment(s, club_id=fx.club_id, name="Racquet", amount_minor=5000, quantity=4)

    def _order(bid):
        return s.execute(text('SELECT o.amount_minor, o.settlement_mode FROM billing."order" o '
                              'JOIN billing.order_line ol ON ol.order_id=o.id '
                              'WHERE ol.booking_id=:b LIMIT 1'), {"b": bid}).mappings().first()

    # (1) PAYG: the court is charged IN FULL alongside the kit. This is the leak that was feared.
    r1 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                          booking_type="court", resource_id=court,
                          starts_at=utc_iso(at(fx, 9)), ends_at=utc_iso(at(fx, 10)),
                          settlement_mode="at_court",
                          addons=[{"resource_id": kit["id"], "qty": 2}])
    check("PAYG court + kit is booked", r1.get("ok"), str(r1))
    o1 = _order(r1["booking"]["id"]) if r1.get("ok") else None
    check("…and the ORDER carries the court R150 AND the kit R100 = R250",
          bool(o1) and o1["amount_minor"] == 25000, str(o1))

    # (2) BOTH are booked out. The court by the GiST constraint, the kit by time-overlap count.
    left = available_units(s, club_id=fx.club_id, resource_id=kit["id"],
                           starts=at(fx, 9), ends=at(fx, 10))
    check("2 of the 4 racquets are now out for that hour", left == 2, f"left={left}")
    clash = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[1], role="member",
                             booking_type="court", resource_id=court,
                             starts_at=utc_iso(at(fx, 9)), ends_at=utc_iso(at(fx, 10)))
    check("the COURT can't be double-booked", clash.get("error") == "SLOT_TAKEN", str(clash))
    over = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[1], role="member",
                            booking_type="court", resource_id=fx.courts[1],
                            starts_at=utc_iso(at(fx, 9)), ends_at=utc_iso(at(fx, 10)),
                            settlement_mode="at_court",
                            addons=[{"resource_id": kit["id"], "qty": 3}])
    check("the KIT can't be over-hired (only 2 left, 3 asked)",
          over.get("error") == "EQUIPMENT_UNAVAILABLE", str(over))
    ok2 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[1], role="member",
                           booking_type="court", resource_id=fx.courts[1],
                           starts_at=utc_iso(at(fx, 9)), ends_at=utc_iso(at(fx, 10)),
                           settlement_mode="at_court",
                           addons=[{"resource_id": kit["id"], "qty": 2}])
    check("…but the remaining 2 CAN be hired on another court", ok2.get("ok"), str(ok2))
    check("all 4 racquets are now out",
          available_units(s, club_id=fx.club_id, resource_id=kit["id"],
                          starts=at(fx, 9), ends=at(fx, 10)) == 0)

    # (3) A MEMBER whose entitlement covers the court: court free, kit still charged. This is the
    # reported shape — correct, and only correct because the membership really does cover it.
    _membership_for_court(s, fx, m)
    r3 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                          booking_type="court", resource_id=court,
                          starts_at=utc_iso(at(fx, 11)), ends_at=utc_iso(at(fx, 12)),
                          settlement_mode="membership_covered",
                          addons=[{"resource_id": kit["id"], "qty": 1}])
    o3 = _order(r3["booking"]["id"]) if r3.get("ok") else None
    check("a covered member pays for the KIT ONLY (R50)",
          bool(o3) and o3["amount_minor"] == 5000, str(o3))
    check("…the booking itself stays membership_covered",
          r3.get("ok") and r3["booking"]["settlement_mode"] == "membership_covered", str(r3.get("booking")))

    # (4) …and the moment the club's cap bites, that same member PAYS for the court again.
    s.execute(text("UPDATE club.policy SET default_max_covered_minutes = 60 WHERE club_id = :c"),
              {"c": fx.club_id})
    r4 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                          booking_type="court", resource_id=court,
                          starts_at=utc_iso(at(fx, 13)), ends_at=utc_iso(at(fx, 15)),   # 2 HOURS
                          settlement_mode="membership_covered",
                          addons=[{"resource_id": kit["id"], "qty": 1}])
    o4 = _order(r4["booking"]["id"]) if r4.get("ok") else None
    check("a 2-hour booking over the 60-min cap is charged for the court again",
          bool(o4) and o4["settlement_mode"] == "at_court" and o4["amount_minor"] > 5000, str(o4))


def _membership_for_court(s, fx, user_id):
    """An active, uncapped membership so COURT bookings resolve as covered."""
    from billing.membership import membership_product_id
    mp = membership_product_id(s, club_id=fx.club_id, create_if_missing=True)
    pr = s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                        "currency_code, unit, term_months, membership_tier, active) "
                        "VALUES (:c,:p,'member',18000,'ZAR','per_month',1,'Adult',true) RETURNING id"),
                   {"c": fx.club_id, "p": mp}).scalar()
    s.execute(text("INSERT INTO billing.membership_subscription (club_id, user_id, price_id, status, "
                   "provider, current_period_end) "
                   "VALUES (:c,:u,:pr,'active','manual',CURRENT_DATE + 30)"),
              {"c": fx.club_id, "u": user_id, "pr": pr})


def sc_equipment_is_scoped_to_its_court_service(s, fx):
    """Equipment used to be club-wide: every item showed on every court booking whatever service it
    belonged to. Clay-only kit could be hired on a hard court. NO service links still means "offered
    everywhere" — the default, so nothing pre-existing changes — and the guard is SERVER-side because
    `addons` arrives off the request body, exactly like the posted product_id."""
    print("\n# Equipment is scoped to its court service (and the guard is server-side)")
    from admin import repositories as AR
    from diary.equipment import list_equipment
    m = fx.members[0]
    # Two court services; court[0] is Hardcourt, the clay court is its own service.
    hard = fx.court_product
    clay_prod = s.execute(text("INSERT INTO billing.product (club_id, kind, name, active) "
                               "VALUES (:c,'court_booking','Clay Hire',true) RETURNING id"),
                          {"c": fx.club_id}).scalar()
    s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                   "currency_code, duration_minutes, active) "
                   "VALUES (:c,:p,'any',20000,'ZAR',60,true)"), {"c": fx.club_id, "p": clay_prod})
    clay_court = s.execute(text("INSERT INTO diary.resource (club_id, kind, name, surface, rank, "
                                "product_id) VALUES (:c,'court','Clay 1','clay',9,:p) RETURNING id"),
                           {"c": fx.club_id, "p": clay_prod}).scalar()
    s.execute(text("UPDATE diary.resource SET product_id = :p WHERE id = :r"),
              {"p": hard, "r": fx.courts[0]})

    anywhere = AR.create_equipment(s, club_id=fx.club_id, name="Racquet", amount_minor=5000, quantity=4)
    clay_only = AR.create_equipment(s, club_id=fx.club_id, name="Clay shoes", amount_minor=3000,
                                    quantity=2, service_product_ids=[str(clay_prod)])

    hard_items = [i["id"] for i in list_equipment(s, club_id=fx.club_id, court_product_id=str(hard))]
    clay_items = [i["id"] for i in list_equipment(s, club_id=fx.club_id, court_product_id=str(clay_prod))]
    check("an UNLINKED item is offered on every service (the default)",
          anywhere["id"] in hard_items and anywhere["id"] in clay_items)
    check("a clay-only item is NOT offered on hardcourt", clay_only["id"] not in hard_items,
          str(hard_items))
    check("…and IS offered on clay", clay_only["id"] in clay_items, str(clay_items))

    # The picker filters, but the picker is not the authority.
    bad = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                           booking_type="court", resource_id=fx.courts[0], product_id=str(hard),
                           starts_at=utc_iso(at(fx, 9)), ends_at=utc_iso(at(fx, 10)),
                           settlement_mode="at_court",
                           addons=[{"resource_id": clay_only["id"], "qty": 1}])
    check("a crafted request for clay-only kit on a hard court is REFUSED",
          bad.get("error") == "EQUIPMENT_NOT_FOR_SERVICE", str(bad))
    check("…and nothing persisted — the slot is still free",
          not s.execute(text("SELECT 1 FROM diary.booking WHERE club_id=:c AND resource_id=:r "
                             "AND starts_at=:sa AND status IN ('held','confirmed')"),
                        {"c": fx.club_id, "r": fx.courts[0], "sa": at(fx, 9)}).first())
    good = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                            booking_type="court", resource_id=clay_court, product_id=str(clay_prod),
                            starts_at=utc_iso(at(fx, 9)), ends_at=utc_iso(at(fx, 10)),
                            settlement_mode="at_court",
                            addons=[{"resource_id": clay_only["id"], "qty": 1}])
    check("…while the SAME kit books fine on a clay court", good.get("ok"), str(good))


# ---------------------------------------------------------------------------
# THE SEAT RULE (community/) — a court fee is split by the seats nobody covers
# ---------------------------------------------------------------------------
# The leak these guard: a membership makes court bookings free, but nothing knew WHO ELSE was on the
# court, so one membership could cover a second, third or fourth player who never paid. The
# entitlement caps limit how MUCH a member books; they cannot express who it was for.
#
# These exercise community.seats DIRECTLY, against bookings made the ordinary way with seats added by
# hand. create_booking is not seat-aware yet — the money core is built and pinned FIRST, because it is
# the part that decides what people are charged.

def _seat(s, fx, booking_id, user_id=None, *, seat_status="confirmed", role="player"):
    """Add a seat (a diary.booking_party row) to a booking."""
    return s.execute(
        text("INSERT INTO diary.booking_party (booking_id, club_id, user_id, party_role, seat_status) "
             "VALUES (:b, :c, :u, :r, :st) RETURNING id"),
        {"b": booking_id, "c": fx.club_id, "u": user_id, "r": role, "st": seat_status},
    ).scalar_one()


def _charging_on(s, fx):
    """Switch the club's seat rule ON, immediately before a money call.

    Two things this encodes. First, a scenario that asserts seat MONEY has to run in a club that
    charges for seats — the gate now lives inside `apply_seat_orders` (it was the caller's job until
    2026-08-11, and three of the four callers forgot, which is how a member got billed with charging
    switched off). Second, it is called just before the money, never at the top of the scenario:
    `community_enabled` set before `create_booking` makes the booking seat ITSELF, which then doubles
    up with the explicit `_seat()` rows these scenarios build by hand."""
    from community import repositories as crepo
    crepo.save_settings(s, club_id=fx.club_id,
                        fields={"community_enabled": True, "seat_rule_enforced": True})


def _as_game(s, booking_id, *, seats=2, play_format="singles", visibility="private"):
    s.execute(
        text("UPDATE diary.booking SET seats = :n, play_format = :f, visibility = :v WHERE id = :b"),
        {"n": seats, "f": play_format, "v": visibility, "b": booking_id})


def _seat_rows(s, booking_id):
    """Every seat that carries an order, with what it owes. Read through booking_party.order_id (NOT
    order_line.booking_id) so the booking's OWN order isn't mistaken for a seat's."""
    return [dict(r) for r in s.execute(
        text('SELECT bp.user_id, bp.order_id, bp.share_minor, bp.covered, bp.seat_status, '
             '       o.amount_minor, o.settlement_mode, o.status '
             'FROM diary.booking_party bp JOIN billing."order" o ON o.id = bp.order_id '
             'WHERE bp.booking_id = :b ORDER BY bp.created_at, bp.id'),
        {"b": booking_id}).mappings().all()]


def _all_seats(s, booking_id):
    """EVERY seat, whether or not it owes anything — the shape needed since the Book-a-court rule
    (2026-08-15). `_seat_rows` inner-joins the order, so a seat with no debt of its own is invisible
    to it: a membership-covered holder, or a guest whose booker already paid the whole court. Those
    seats are exactly the ones the new rule is about, so assertions on them need this instead."""
    return [dict(r) for r in s.execute(
        text("SELECT user_id, order_id, share_minor, covered, seat_status, party_role "
             "FROM diary.booking_party WHERE booking_id = :b ORDER BY created_at, id"),
        {"b": booking_id}).mappings().all()]


def _covered_court(s, fx, user_id, hour=10, court=0):
    """A membership-covered court booking, the shape the seat rule has to reason about."""
    return B.create_booking(s, club_id=fx.club_id, booked_by_user_id=user_id, role="member",
                            booking_type="court", resource_id=fx.courts[court],
                            settlement_mode="membership_covered",
                            starts_at=utc_iso(at(fx, hour)), ends_at=utc_iso(at(fx, hour + 1)))


def sc_a_seat_share_is_a_fixed_fraction_of_the_court(s, fx):
    """A SHARE IS NOT A DIVISION OF THE COURT FEE — it is a fixed fraction of it, so the price a
    player is quoted never moves when someone else joins, leaves, or turns out to be a member.

    That stability is the whole design (owner decision 2026-08-10). The earlier model divided one
    court fee among the un-covered seats, which meant your share changed under you and needed a lock,
    a re-price and a refusal to stay honest. All three are gone.

    Consequence to keep in view: at 50% singles with two payers collects the court price, but with
    MORE than two payers the club collects MORE than one court fee. That is deliberate."""
    print("\n# a seat share is a fixed % of the court, rounded — not a division of the fee")
    from community.seats import share_minor
    # The club's real price list: R90 / R150 / R210 / R280 for 30/60/90/120.
    for price, raw, up10 in ((9000, 4500, 5000), (15000, 7500, 8000),
                             (21000, 10500, 11000), (28000, 14000, 14000)):
        check(f"50% of {price} is {raw} un-rounded",
              share_minor(price, pct=50, rounding="none") == raw,
              str(share_minor(price, pct=50, rounding="none")))
        check(f"…and {up10} rounded UP to the nearest ten",
              share_minor(price, pct=50, rounding="up_10") == up10,
              str(share_minor(price, pct=50, rounding="up_10")))
    check("the share does NOT depend on how many are playing — there is no seat count in the call",
          share_minor(15000, pct=50, rounding="up_10") == 8000)
    check("a different percentage is honoured", share_minor(21000, pct=40, rounding="none") == 8400)
    check("rounding to the nearest five leaves a clean price alone",
          share_minor(15000, pct=50, rounding="nearest_5") == 7500)
    check("an impossible percentage is refused, not silently clamped",
          _raises(__import__("community.seats", fromlist=["SeatError"]).SeatError,
                  share_minor, 15000, pct=140) == "BAD_SHARE")
    check("a free court yields a free seat, not a crash", share_minor(0, pct=50) == 0)


def sc_member_plus_guest_bills_the_guest_in_full(s, fx):
    """THE LEAK, CLOSED. A member brings a friend who is not a member: the member's own seat is
    covered, and the WHOLE court fee lands on the friend. Before this, the friend played free on the
    member's membership — which is how two friends shared one membership and halved their court cost
    indefinitely."""
    print("\n# member + non-member: the non-member pays ONE SHARE, the member nothing")
    from community import seats as S
    m, guest = fx.members[0], fx.members[1]
    _membership_for_court(s, fx, m)                 # only the member is covered
    r = _covered_court(s, fx, m, hour=10)
    check("the member's own court booking is covered (R0)",
          r.get("ok") and r["booking"]["settlement_mode"] == "membership_covered", str(r))
    bid = r["booking"]["id"]
    _as_game(s, bid, seats=2)
    _seat(s, fx, bid, m, role="host")
    _seat(s, fx, bid, guest)

    plan = S.seat_plan(s, club_id=fx.club_id, booking_id=bid)
    check("the court is priced at R150", plan["court_price_minor"] == 15000,
          str(plan["court_price_minor"]))
    check("both seats are occupied — nothing left open", plan["open_count"] == 0, str(plan))
    covered = [row for row in plan["rows"] if row["covered"]]
    check("exactly ONE seat is covered (the member's)", len(covered) == 1, str(plan["rows"]))

    _charging_on(s, fx)      # see the helper: set HERE, never before the booking
    res = S.apply_seat_orders(s, club_id=fx.club_id, booking_id=bid)
    check("exactly one seat is charged", res["charged"] == 1, str(res))
    check("…and it carries ONE SHARE — R80 (50% of R150, rounded up)", res["total_minor"] == 8000, str(res))
    rows = _seat_rows(s, bid)
    check("the member is billed nothing at all (no seat order)",
          all(str(x["user_id"]) != str(m) for x in rows), str(rows))
    check("the guest owes R80", len(rows) == 1 and rows[0]["amount_minor"] == 8000, str(rows))
    check("…recorded as un-covered, so the money is auditable later",
          rows and rows[0]["covered"] is False, str(rows))

    # Re-running must not mint a second debt for the same seat — the sweep re-runs hourly.
    _charging_on(s, fx)      # see the helper: set HERE, never before the booking
    again = S.apply_seat_orders(s, club_id=fx.club_id, booking_id=bid)
    check("re-applying is idempotent (no second debt for the same seat)",
          again["charged"] == 1 and len(_seat_rows(s, bid)) == 1, str(_seat_rows(s, bid)))


def sc_two_payg_split_and_both_must_settle(s, fx):
    """Two non-members split the court fee, and the court confirms only when BOTH have paid. A single
    Yoco checkout cannot collect from two payers, so this is N orders against one booking — the same
    shape semi-private lessons already use, with the confirmation gate widened from one order to N."""
    print("\n# two PAYG players: R80 + R80, and the court is held until BOTH settle")
    from community import seats as S
    p1, p2 = fx.members[1], fx.members[2]           # neither holds a membership
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=p1, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         settlement_mode="at_court",
                         starts_at=utc_iso(at(fx, 12)), ends_at=utc_iso(at(fx, 13)))
    check("the booking is made", r.get("ok"), str(r))
    bid = r["booking"]["id"]
    # OPEN, because splitting a court between two PAYG players IS Find a Game. On a private
    # Book-a-court the booker pays the whole fee instead and their guest owes nothing — the owner's
    # rule of 2026-08-15, pinned by sc_a_payg_booker_pays_the_whole_court_not_a_share.
    _as_game(s, bid, seats=2, visibility="open")
    _seat(s, fx, bid, p1, role="host")
    _seat(s, fx, bid, p2)

    _charging_on(s, fx)      # see the helper: set HERE, never before the booking
    S.apply_seat_orders(s, club_id=fx.club_id, booking_id=bid)
    rows = _seat_rows(s, bid)
    check("both seats are charged", len(rows) == 2, str(rows))
    check("…R80 each — the SAME share, not a division", sorted(x["amount_minor"] for x in rows) == [8000, 8000],
          str(rows))
    check("…so two payers settle R160 on a R150 court — rounding up costs the pair R10",
          sum(x["amount_minor"] for x in rows) == 16000, str(rows))
    check("the club offers online, so each seat must PREPAY",
          all(x["settlement_mode"] == "online" for x in rows), str(rows))

    check("nothing is settled yet, so the court must NOT confirm",
          S.all_prepaid_seats_settled(s, club_id=fx.club_id, booking_id=bid) is False)
    first = s.execute(
        text('SELECT bp.order_id FROM diary.booking_party bp WHERE bp.booking_id = :b '
             '  AND bp.order_id IS NOT NULL ORDER BY bp.created_at LIMIT 1'), {"b": bid}).scalar()
    s.execute(text('UPDATE billing."order" SET status = \'paid\' WHERE id = :o'), {"o": first})
    check("ONE of the two has paid — still not confirmable (the trap)",
          S.all_prepaid_seats_settled(s, club_id=fx.club_id, booking_id=bid) is False)
    s.execute(text('UPDATE billing."order" SET status = \'paid\' WHERE id IN '
                   '(SELECT order_id FROM diary.booking_party WHERE booking_id = :b '
                   ' AND order_id IS NOT NULL)'), {"b": bid})
    check("…once BOTH have settled, the court may confirm",
          S.all_prepaid_seats_settled(s, club_id=fx.club_id, booking_id=bid) is True)


def sc_open_seat_collapses_onto_the_holder_at_cutoff(s, fx):
    """A member may book a court and go looking for a partner — they simply cannot end up having held
    a second seat, free, that nobody ever filled. At the cutoff the empty seat's share becomes theirs.

    The idempotency half of this is the one that bites: the sweep runs hourly, and a collapsed seat
    that did not count as OCCUPYING its seat was collapsed again on every single run."""
    print("\n# an unfilled OPEN seat collapses onto the holder — and only once, however often we sweep")
    from community import seats as S
    m = fx.members[0]
    _membership_for_court(s, fx, m)
    r = _covered_court(s, fx, m, hour=14)
    bid = r["booking"]["id"]
    _as_game(s, bid, seats=2, visibility="open")
    _seat(s, fx, bid, m, role="host")

    plan = S.seat_plan(s, club_id=fx.club_id, booking_id=bid)
    check("one seat is open", plan["open_count"] == 1, str(plan))
    check("the member's own seat is covered, so nothing is owed yet",
          all(row["covered"] for row in plan["rows"]), str(plan["rows"]))

    out = S.collapse_open_seats(s, club_id=fx.club_id, booking_id=bid)
    check("the open seat collapses", out["collapsed"] == 1, str(out))
    check("…and the holder is billed ONE SHARE — R80, what the taker would have paid",
          out["amount_minor"] == 8000, str(out))
    rows = _seat_rows(s, bid)
    check("the charge belongs to the holder", len(rows) == 1 and str(rows[0]["user_id"]) == str(m),
          str(rows))
    check("…recorded as a collapsed seat, not a mystery charge",
          rows and rows[0]["seat_status"] == "collapsed", str(rows))
    check("the game is closed to new joiners once it has collapsed",
          s.execute(text("SELECT visibility FROM diary.booking WHERE id = :b"),
                    {"b": bid}).scalar() == "private")

    again = S.collapse_open_seats(s, club_id=fx.club_id, booking_id=bid)
    check("sweeping again collapses NOTHING (idempotent — the hourly job re-runs all day)",
          again["collapsed"] == 0, str(again))
    check("…and the holder is not billed twice", len(_seat_rows(s, bid)) == 1,
          str(_seat_rows(s, bid)))


def sc_the_quoted_share_is_frozen_for_the_life_of_the_game(s, fx):
    """A game keeps the share it was QUOTED, whatever the club changes afterwards — and a late joiner
    pays exactly what the people already in it paid.

    This replaced a lock, a re-price and a refusal. When the share was a DIVISION of the court fee it
    moved every time somebody joined, left or turned out to be a member, so the earlier design had to
    freeze it on first payment and then refuse anyone who arrived after (and an `int(None or 0)` in
    that refusal path once handed a late joiner a FREE court). A fixed fraction needs none of it."""
    print("\n# the quoted share is frozen — a later joiner pays the same, a price change can't reach it")
    from community import seats as S
    _enable_seat_rule(s, fx)
    p1, p2, p3 = fx.members[0], fx.members[1], fx.members[2]
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=p1, role="member",
                         booking_type="court", resource_id=fx.courts[1],
                         settlement_mode="at_court",
                         starts_at=utc_iso(at(fx, 16)), ends_at=utc_iso(at(fx, 17)),
                         extra_clients=[{"user_id": str(p2)}],
                         # OPEN: freezing a quoted share is a Find-a-Game property. A private
                         # Book-a-court has no share to freeze — the booker pays the court's price.
                         play_format="doubles", seats=4, visibility="open")
    bid = r["booking"]["id"]
    check("two un-covered seats each owe ONE SHARE — R80, not half of R150",
          sorted(x["amount_minor"] for x in _seat_rows(s, bid)) == [8000, 8000],
          str(_seat_rows(s, bid)))
    check("the quote is frozen on the game",
          s.execute(text("SELECT seat_share_minor FROM diary.booking WHERE id=:b"),
                    {"b": bid}).scalar() == 8000)

    paid = s.execute(text("SELECT order_id FROM diary.booking_party WHERE booking_id = :b "
                          "  AND user_id = :u"), {"b": bid, "u": p1}).scalar()
    s.execute(text('UPDATE billing."order" SET status=\'paid\' WHERE id=:o'), {"o": paid})

    # THE CLUB CHANGES ITS PRICING — mid-flight, after money has moved.
    s.execute(text("UPDATE club.policy SET seat_share_pct = 80 WHERE club_id = :c"),
              {"c": fx.club_id})
    _seat(s, fx, bid, p3)                       # …and a third player joins
    _charging_on(s, fx)      # see the helper: set HERE, never before the booking
    S.apply_seat_orders(s, club_id=fx.club_id, booking_id=bid)
    rows = _seat_rows(s, bid)
    check("the PAID seat is untouched",
          any(x["amount_minor"] == 8000 and x["status"] == "paid" for x in rows), str(rows))
    check("the other original seat is not re-priced either",
          len([x for x in rows if x["amount_minor"] == 8000]) == 3, str(rows))
    check("…and the LATE joiner pays the SAME share, not the club's new rate and not zero",
          any(str(x["user_id"]) == str(p3) and x["amount_minor"] == 8000 for x in rows), str(rows))
    check("a doubles game with three payers collects MORE than one court fee — deliberately",
          sum(x["amount_minor"] for x in rows) == 24000, str(rows))

    # A NEW game, made after the change, DOES get the new rate. OPEN, like the one above — a share is
    # only ever quoted on a game the club is being invited into.
    r2 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=p2, role="member",
                          booking_type="court", resource_id=fx.courts[0],
                          settlement_mode="at_court",
                          starts_at=utc_iso(at(fx, 19)), ends_at=utc_iso(at(fx, 20)),
                          play_format="singles", visibility="open")
    check("…while a game booked AFTER the change is priced at the new 80% (R120)",
          any(x["amount_minor"] == 12000 for x in _seat_rows(s, r2["booking"]["id"])),
          str(_seat_rows(s, r2["booking"]["id"])))


def sc_seat_rule_off_changes_nothing(s, fx):
    """The regression contract. Both switches default false, so a club that has not opted in books
    exactly as it did before — no seats, no seat orders, no behaviour change anywhere."""
    print("\n# with the seat rule OFF, a court booking behaves exactly as it always has")
    from community import seats as S
    pol = S.policy(s, fx.club_id)
    check("community_enabled defaults OFF", pol["community_enabled"] is False, str(pol))
    check("seat_rule_enforced defaults OFF", pol["seat_rule_enforced"] is False, str(pol))
    check("a club with no policy row is OFF too, not accidentally ON",
          S.policy(s, "00000000-0000-0000-0000-000000000000")["seat_rule_enforced"] is False)

    m = fx.members[0]
    _membership_for_court(s, fx, m)
    r = _covered_court(s, fx, m, hour=8)
    check("the covered booking still confirms immediately", r.get("ok")
          and r["booking"]["status"] == "confirmed", str(r))
    check("…still at R0", (_order_for_booking(s, r["booking"]["id"]) or {}).get("amount_minor") == 0)
    check("…and NO seat order was raised behind it", _seat_rows(s, r["booking"]["id"]) == [])


def _enable_seat_rule(s, fx, **over):
    s.execute(
        text("UPDATE club.policy SET seat_rule_enforced = true, community_enabled = true, "
             "       seat_pay_hours = :ph WHERE club_id = :c"),
        {"ph": over.get("seat_pay_hours", 24), "c": fx.club_id})


def sc_seat_rule_bills_through_create_booking(s, fx):
    """The rule reaching the LIVE booking path. A member books a court and names a non-member: the
    member's own order is re-priced to their (covered, R0) share and the friend gets their own debt
    for the whole fee — on ONE create_booking call, the way the booking flow will do it.

    The holder's seat rides the booking's OWN order rather than raising a second one beside it.
    Billing the member the full fee AND the guest a share would double-charge the club's own member —
    the same leak pointing the other way."""
    print("\n# create_booking seats the court and bills the guest — end to end")
    _enable_seat_rule(s, fx)
    m, guest = fx.members[0], fx.members[1]
    _membership_for_court(s, fx, m)
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         settlement_mode="membership_covered",
                         starts_at=utc_iso(at(fx, 10)), ends_at=utc_iso(at(fx, 11)),
                         extra_clients=[{"user_id": str(guest)}], play_format="singles")
    check("the booking is made", r.get("ok"), str(r))
    bid = r["booking"]["id"]
    check("it is a 2-seat singles game",
          s.execute(text("SELECT seats FROM diary.booking WHERE id=:b"), {"b": bid}).scalar() == 2)
    rows = _seat_rows(s, bid)
    guest_rows = [x for x in rows if str(x["user_id"]) == str(guest)]
    check("the guest owes ONE SHARE — R80",
          len(guest_rows) == 1 and guest_rows[0]["amount_minor"] == 8000, str(rows))
    # Read through _all_seats: since the Book-a-court rule the holder's seat no longer rides the
    # booking's own order on a private court, so it carries NO order id and _seat_rows (which
    # inner-joins one) cannot see it. That is the correct shape — a covered seat has no debt — and it
    # is stronger than before, because previously the member's seat pointed at a R0 order that existed
    # only so it could be re-priced.
    holder = [x for x in _all_seats(s, bid) if str(x["user_id"]) == str(m)]
    check("the member owes nothing — no seat debt of their own",
          len(holder) == 1 and not holder[0]["order_id"], str(holder))
    check("…and the member's seat is recorded as covered", holder and holder[0]["covered"] is True,
          str(holder))
    # AN UNPAID GUEST MUST NOT COST THE MEMBER THEIR COURT (owner's decision, 2026-08-15). This used
    # to assert the opposite — that the court stayed HELD until the guest paid — and `held` is not a
    # gentle state: release_expired_holds CANCELS the booking once seat_pay_hours passes. A guest who
    # hadn't got round to paying by tomorrow lost the member the court they had booked. The club takes
    # that money at the desk instead. The guest's debt is untouched by this; it is still a real order.
    check("the court is CONFIRMED even though the guest hasn't paid",
          r["booking"]["status"] == "confirmed", str(r))
    check("…and the guest still owes it — the debt is not forgiven, just not blocking",
          guest_rows and guest_rows[0]["amount_minor"] == 8000, str(rows))
    check("…and the member is NOT sent to a checkout for someone else's debt",
          r.get("requires_payment") is not True, str(r))


def sc_a_payg_booker_pays_the_whole_court_not_a_share(s, fx):
    """THE BOOK-A-COURT RULE (owner's decision, 2026-08-15). A share is what you pay to SHARE a court
    you found somebody else for. It is not what a court costs.

    Before this, `seat_a_new_booking` linked the holder's seat to the booking's own order so
    apply_seat_orders could re-price it DOWN to a share — which meant a PAYG member booking a 60-min
    court dropped from R150 to R80 the moment the money switch went on. That hands every PAYG booker
    a reason to book "singles", say a friend is coming, and take the court at half price, leaving the
    club to police whether the second person ever arrived. A court fee is not negotiable by who you
    name."""
    print("\n# Book a court: a PAYG booker pays the WHOLE court fee, never a share")
    _enable_seat_rule(s, fx)
    payg = fx.members[1]                       # deliberately on no plan
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=payg, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         settlement_mode="at_court", play_format="singles",
                         starts_at=utc_iso(at(fx, 8)), ends_at=utc_iso(at(fx, 9)))
    check("the booking is made", r.get("ok"), str(r))
    bid = r["booking"]["id"]
    total = s.execute(text('SELECT amount_minor FROM billing."order" WHERE id = :o'),
                      {"o": r["booking"]["order_id"]}).scalar()
    check("the booker is charged the FULL 60-min court fee (R150), not a R80 share",
          total == 15000, str(total))
    rows = _seat_rows(s, bid)
    check("…and no SECOND debt was raised beside it",
          [x for x in rows if x["amount_minor"] and x["order_id"] != r["booking"]["order_id"]] == [],
          str(rows))

    # The same booker, same court, but PUBLISHED to the club — that IS Find a Game, and there a share
    # is the product. Publishing must never be the cheaper option, which is what makes the rule safe:
    # their own seat is a share, and an unfilled seat collapses back onto them for a second one.
    r2 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=payg, role="member",
                          booking_type="court", resource_id=fx.courts[1],
                          settlement_mode="at_court", play_format="singles", visibility="open",
                          starts_at=utc_iso(at(fx, 8)), ends_at=utc_iso(at(fx, 9)))
    open_total = s.execute(text('SELECT amount_minor FROM billing."order" WHERE id = :o'),
                           {"o": r2["booking"]["order_id"]}).scalar()
    check("publishing the spare seat DOES price the booker at a share (R80) — Find a Game is a "
          "different product", open_total == 8000, str(open_total))


def sc_a_guest_is_free_once_the_court_itself_is_paid_for(s, fx):
    """The seat rule exists to make sure a court gets paid for ONCE.

    A membership-covered booker pays nothing, so their guest is the only person who can pay for that
    court — bill them, or the original leak is open again (two friends, one membership, half price,
    indefinitely). A PAYG booker has ALREADY paid the whole court, so billing their guest as well
    would collect R230 for a R150 court and be impossible to explain at the desk.

    Same flow, same widget, same seat rows. The only thing that decides it is whether the court has
    been paid for yet."""
    print("\n# Book a court: the guest pays only when the court isn't already paid for")
    _enable_seat_rule(s, fx)
    member, payg, guest1, guest2 = fx.members[0], fx.members[1], fx.members[2], fx.members[2]
    _membership_for_court(s, fx, member)

    # 1) MEMBER + guest -> the court is paid for by NOBODY, so the guest owes a share. The leak.
    rm = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=member, role="member",
                          booking_type="court", resource_id=fx.courts[0],
                          settlement_mode="membership_covered", play_format="singles",
                          starts_at=utc_iso(at(fx, 16)), ends_at=utc_iso(at(fx, 17)),
                          extra_clients=[{"user_id": str(guest1)}])
    rows_m = _seat_rows(s, rm["booking"]["id"])
    g = [x for x in rows_m if str(x["user_id"]) == str(guest1)]
    h = [x for x in _all_seats(s, rm["booking"]["id"]) if str(x["user_id"]) == str(member)]
    check("a MEMBER's guest owes one share — the leak stays shut",
          len(g) == 1 and g[0]["amount_minor"] == 8000, str(rows_m))
    check("…and the member themselves owes nothing", h and not h[0]["order_id"], str(h))
    check("…and their seat says WHY it was free (covered by the membership)",
          h and h[0]["covered"] is True, str(h))

    # 2) PAYG + guest -> the booker already paid the whole court, so the guest owes nothing.
    rp = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=payg, role="member",
                          booking_type="court", resource_id=fx.courts[1],
                          settlement_mode="at_court", play_format="singles",
                          starts_at=utc_iso(at(fx, 16)), ends_at=utc_iso(at(fx, 17)),
                          extra_clients=[{"user_id": str(guest2)}])
    booker_total = s.execute(text('SELECT amount_minor FROM billing."order" WHERE id = :o'),
                             {"o": rp["booking"]["order_id"]}).scalar()
    rows_p = _seat_rows(s, rp["booking"]["id"])
    check("the PAYG booker still pays the whole court (R150)", booker_total == 15000, str(booker_total))
    check("…and their guest is charged NOTHING — the court is already paid for",
          all((x["amount_minor"] or 0) == 0 for x in rows_p if str(x["user_id"]) == str(guest2)),
          str(rows_p))
    check("…so the club collects R150 for the court, not R230",
          booker_total + sum(x["amount_minor"] or 0 for x in rows_p) == 15000, str(rows_p))
    all_p = _all_seats(s, rp["booking"]["id"])
    guest_seat = [x for x in all_p if str(x["user_id"]) == str(guest2)]
    check("…and the free guest seat does NOT claim a membership covered it (the audit trail)",
          guest_seat and guest_seat[0]["covered"] is False, str(all_p))
    check("…nor is it left holding a phantom order id",
          guest_seat and not guest_seat[0]["order_id"], str(all_p))


def sc_a_private_court_never_lapses_because_a_guest_did_not_pay(s, fx):
    """`held` is not a gentle state. release_expired_holds CANCELS a held booking once held_until
    passes, so holding a court until an invited guest pays means a guest who is merely slow costs the
    member the court they booked — and the club would far rather take that money at the desk.

    Find a Game keeps the hold (see `sc_seat_rule_holds_the_court_until_every_seat_settles`): there,
    strangers should not get a court on somebody else's payment. This is the private counterpart."""
    print("\n# Book a court: an unpaid guest never costs the member their court")
    _enable_seat_rule(s, fx)
    member, guest = fx.members[0], fx.members[1]
    _membership_for_court(s, fx, member)
    # membership_covered, so the BOOKER's own order is R0 and settled. The only thing outstanding is
    # the guest's seat — which is the whole point. (Booking this `online` would hold the court for the
    # ordinary checkout reason and prove nothing about seats.)
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=member, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         settlement_mode="membership_covered", play_format="singles",
                         starts_at=utc_iso(at(fx, 12)), ends_at=utc_iso(at(fx, 13)),
                         extra_clients=[{"user_id": str(guest)}])
    bid = r["booking"]["id"]
    check("the court is CONFIRMED, not held, even with the guest's seat unpaid",
          r["booking"]["status"] == "confirmed", str(r))
    check("…and carries no expiry that could cancel it",
          s.execute(text("SELECT held_until IS NULL FROM diary.booking WHERE id=:b"),
                    {"b": bid}).scalar() is True)

    # THE TRAP: the debt must survive. "Don't block the court" is not "don't charge for the seat" —
    # the club is collecting at the desk, so the order has to still be there to collect.
    rows = _seat_rows(s, bid)
    g = [x for x in rows if str(x["user_id"]) == str(guest)]
    check("the guest's debt is STILL a real order (collected at the desk, not forgiven)",
          len(g) == 1 and g[0]["amount_minor"] == 8000 and bool(g[0]["order_id"]), str(rows))
    st = s.execute(text('SELECT status FROM billing."order" WHERE id = :o'),
                   {"o": g[0]["order_id"]}).scalar()
    check("…and it is open/awaiting, not voided away with the hold",
          st in ("open", "awaiting_payment"), str(st))

    # And the lazy sweep that cancels abandoned holds must leave this booking entirely alone.
    B.release_expired_holds(s, fx.club_id, now=at(fx, 12) + timedelta(hours=48))
    check("the hold sweep 48h later does NOT cancel it (the whole point)",
          s.execute(text("SELECT status FROM diary.booking WHERE id=:b"),
                    {"b": bid}).scalar() == "confirmed")


def sc_seat_rule_holds_the_court_until_every_seat_settles(s, fx):
    """The billing→diary contract, widened from one order to N. A paid seat must not confirm the
    court out from under a seat that has not paid.

    THIS IS A FIND-A-GAME RULE, AND ONLY A FIND-A-GAME RULE (owner's decision, 2026-08-15). It is
    right when strangers found each other through the feed: nobody should get a court on somebody
    else's payment. It is wrong on a private Book-a-court, where holding the court means CANCELLING a
    member's own booking because the guest they invited was slow to pay — so this scenario books an
    OPEN game, which is the shape the rule now applies to. The private counterpart is
    `sc_a_private_court_never_lapses_because_a_guest_did_not_pay`."""
    print("\n# a paid seat does NOT confirm an OPEN game whose other seat is still unpaid")
    from billing.events import _confirm_held_bookings
    _enable_seat_rule(s, fx)
    p1, p2 = fx.members[1], fx.members[2]        # neither is a member of any plan
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=p1, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         settlement_mode="online",
                         starts_at=utc_iso(at(fx, 13)), ends_at=utc_iso(at(fx, 14)),
                         extra_clients=[{"user_id": str(p2)}], play_format="singles",
                         visibility="open")
    check("the booking is made and HELD (both seats owe online)",
          r.get("ok") and r["booking"]["status"] == "held", str(r))
    bid = r["booking"]["id"]
    rows = _seat_rows(s, bid)
    check("two seats, R80 each", sorted(x["amount_minor"] for x in rows) == [8000, 8000], str(rows))
    check("…and BOTH must prepay", all(x["settlement_mode"] == "online" for x in rows), str(rows))

    # Pay a NAMED seat, never "the first one". `now()` is transaction-stable in Postgres, so every
    # seat inserted by one create_booking shares a created_at to the microsecond — ordering by it
    # picks an arbitrary row, and this assertion would pass or fail on a coin toss.
    def _order_of(uid):
        return s.execute(text("SELECT order_id FROM diary.booking_party "
                              "WHERE booking_id = :b AND user_id = :u"),
                         {"b": bid, "u": uid}).scalar()

    s.execute(text('UPDATE billing."order" SET status=\'paid\' WHERE id=:o'), {"o": _order_of(p2)})
    _confirm_held_bookings(s, _order_of(p2), fx.club_id)
    st = s.execute(text("SELECT status FROM diary.booking WHERE id=:b"), {"b": bid}).scalar()
    check("ONE seat paid → the court is STILL held (the trap)", st == "held", st)
    check("…but the split is now LOCKED by that payment",
          s.execute(text("SELECT split_locked_at IS NOT NULL FROM diary.booking WHERE id=:b"),
                    {"b": bid}).scalar() is True)

    oid = _order_of(p1)
    s.execute(text('UPDATE billing."order" SET status=\'paid\' WHERE id=:o'), {"o": oid})
    _confirm_held_bookings(s, oid, fx.club_id)
    st = s.execute(text("SELECT status FROM diary.booking WHERE id=:b"), {"b": bid}).scalar()
    check("…both paid → the court CONFIRMS", st == "confirmed", st)
    check("…and the hold is cleared",
          s.execute(text("SELECT held_until IS NULL FROM diary.booking WHERE id=:b"),
                    {"b": bid}).scalar() is True)


def sc_cancelling_a_game_voids_every_seat_debt(s, fx):
    """Cancel must leave nobody owing. A squad lesson already voids every head's order via
    order_line.booking_id; a seat raises its order the same way, so this asserts the seats inherited
    that rather than assuming it — an un-voided seat debt is a bill for a game that never happened."""
    print("\n# cancelling the game voids EVERY seat's debt — nobody is left owing")
    _enable_seat_rule(s, fx)
    p1, p2 = fx.members[0], fx.members[1]
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=p1, role="member",
                         booking_type="court", resource_id=fx.courts[1],
                         settlement_mode="at_court",
                         starts_at=utc_iso(at(fx, 15)), ends_at=utc_iso(at(fx, 16)),
                         # OPEN so there ARE two seat debts to void — on a private court with a PAYG
                         # booker the only debt is the booking's own order.
                         extra_clients=[{"user_id": str(p2)}], play_format="singles",
                         visibility="open")
    bid = r["booking"]["id"]
    check("two seat debts exist before the cancel", len(_seat_rows(s, bid)) == 2)
    B.cancel_booking(s, club_id=fx.club_id, booking_id=bid, actor_user_id=p1, role="member")
    live = [x for x in _seat_rows(s, bid) if x["status"] not in ("void", "refunded")]
    check("…and NONE survives the cancel", live == [], str(_seat_rows(s, bid)))


def sc_an_expired_membership_is_an_uncovered_seat(s, fx):
    """Coverage is not a property of the person, it is a property of the seat AT THAT MOMENT — and
    seats delegate the whole question to diary.entitlement.court_covered. A member whose term has
    lapsed by the day of the game is simply an un-covered seat and pays like anyone else, which is
    the same rule sc_membership_cannot_book_past_its_own_expiry pins for bookings."""
    print("\n# a lapsed member is an UN-COVERED seat, and is billed like anyone else")
    _enable_seat_rule(s, fx)
    lapsed, other = fx.members[0], fx.members[1]
    _membership_for(s, fx, lapsed, days=1)       # expires long before the test day
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=lapsed, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         settlement_mode="membership_covered",
                         starts_at=utc_iso(at(fx, 17)), ends_at=utc_iso(at(fx, 18)),
                         # OPEN so the shares are actually quoted: this scenario is about COVERAGE
                         # resolution (an expired membership covers nobody), and shares only exist on
                         # a game the club was invited into. The private equivalent still charges the
                         # lapsed member — via the booking's own order, at the FULL court fee, which
                         # sc_a_payg_booker_pays_the_whole_court_not_a_share pins.
                         extra_clients=[{"user_id": str(other)}], play_format="singles",
                         visibility="open")
    check("the booking still succeeds — entitlement downgrades, it never blocks", r.get("ok"), str(r))
    rows = _seat_rows(s, r["booking"]["id"])
    check("BOTH seats are un-covered", all(x["covered"] is False for x in rows), str(rows))
    check("…so they each owe a share — the lapsed member is not free",
          sorted(x["amount_minor"] for x in rows) == [8000, 8000], str(rows))


def _open_game(s, fx, host, hour=11, seats=2, court=0):
    """A published game with an open seat, made the way create_booking makes one."""
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=host, role="member",
                         booking_type="court", resource_id=fx.courts[court],
                         settlement_mode="membership_covered",
                         starts_at=utc_iso(at(fx, hour)), ends_at=utc_iso(at(fx, hour + 1)),
                         play_format="singles" if seats == 2 else "doubles",
                         seats=seats, visibility="open",
                         open_until=at(fx, hour) - timedelta(hours=2))
    return r


def sc_invited_friend_is_trialed_once_only(s, fx):
    """"First seven days free, then they pay" — with NO new free-play mechanism to police.

    The free week IS the existing 7-day trial membership, so while it runs the friend's seats resolve
    covered through the ORDINARY entitlement path, and when it lapses the seat rule bills them like
    anybody else. grant_signup_trial refuses anyone who has EVER held a subscription, which is what
    makes a second invite worthless and what protects the ~880 imported Wix members."""
    print("\n# an invited friend gets the free week ONCE — never twice, never a Wix import")
    from community import invites
    host = fx.members[0]
    inv = invites.invite_player(s, club_id=fx.club_id, inviter_user_id=host,
                                email="newfriend@scratch.test")
    check("the invite is created with a signed link", inv.get("ok") and "/join.html?t=" in inv["url"],
          str(inv))
    check("…and it knows they're not a member yet", inv.get("already_a_member") is False)

    dup = invites.invite_player(s, club_id=fx.club_id, inviter_user_id=host,
                                email="NewFriend@scratch.test")   # same person, different case
    check("re-inviting RE-SENDS rather than minting a second free week", dup.get("resent") is True,
          str(dup))

    friend = _mk_user(s, "newfriend@scratch.test", "Friend")
    acc = invites.accept_invite(s, token=inv["token"], user_id=friend, club_id=fx.club_id)
    check("accepting grants the free week", acc["trial"].get("granted") is True, str(acc["trial"]))
    check("…and they're now a member of the club",
          s.execute(text("SELECT 1 FROM iam.membership WHERE club_id=:c AND user_id=:u"),
                    {"c": fx.club_id, "u": friend}).first() is not None)

    inv2 = invites.invite_player(s, club_id=fx.club_id, inviter_user_id=fx.members[1],
                                 email="newfriend@scratch.test")
    acc2 = invites.accept_invite(s, token=inv2["token"], user_id=friend, club_id=fx.club_id)
    check("a SECOND invite grants NO second free week (the trap)",
          acc2["trial"].get("granted") is False, str(acc2["trial"]))
    check("…and says why", acc2["trial"].get("reason") == "already_has_subscription",
          str(acc2["trial"]))

    # A member who already has subscription history — the shape every imported Wix user has.
    wix = _mk_user(s, "wiximport@scratch.test", "Imported")
    _membership_for(s, fx, wix, days=30)
    inv3 = invites.invite_player(s, club_id=fx.club_id, inviter_user_id=host,
                                 email="wiximport@scratch.test")
    acc3 = invites.accept_invite(s, token=inv3["token"], user_id=wix, club_id=fx.club_id)
    check("an EXISTING member is never trialed by an invite", acc3["trial"].get("granted") is False,
          str(acc3["trial"]))


def sc_joining_an_open_game_bills_the_new_seat(s, fx):
    """The Open Match loop, end to end: a member posts a game with a seat free, someone takes it, and
    the money follows the moment they do."""
    print("\n# joining an open game takes the seat AND raises that player's own debt")
    from community import games
    _enable_seat_rule(s, fx)
    host, joiner = fx.members[0], fx.members[1]
    _membership_for_court(s, fx, host)
    r = _open_game(s, fx, host, hour=11)
    check("the game is published with a seat open", r.get("ok"), str(r))
    bid = r["booking"]["id"]

    feed = games.list_open_games(s, club_id=fx.club_id, user_id=joiner)
    mine = [g for g in feed if g["booking_id"] == str(bid)]
    check("it appears in the Find-a-Game feed", len(mine) == 1, str(feed))
    check("…advertising exactly one open seat", mine and mine[0]["open_seats"] == 1, str(mine))
    check("…and does NOT leak the host's email",
          all("email" not in g for g in feed), str(feed))

    out = games.join_game(s, club_id=fx.club_id, booking_id=bid, user_id=joiner)
    check("the join succeeds", out.get("ok"), str(out))
    rows = _seat_rows(s, bid)
    joined = [x for x in rows if str(x["user_id"]) == str(joiner)]
    check("the joiner owes ONE SHARE — R80 (the host's seat is covered)",
          len(joined) == 1 and joined[0]["amount_minor"] == 8000, str(rows))
    check("the game is now full", games.game_detail(
        s, club_id=fx.club_id, booking_id=bid, viewer_user_id=host)["open_seats"] == 0)
    try:
        games.join_game(s, club_id=fx.club_id, booking_id=bid, user_id=fx.members[2])
        check("a third player is refused", False, "no GameError")
    except games.GameError as e:
        check("a third player is refused (GAME_FULL)", e.code == "GAME_FULL", e.code)


def sc_leaving_a_game_frees_the_seat_and_the_debt(s, fx):
    """Leaving must not leave a bill behind — and must not let someone walk away from money already
    taken, which is a refund decision rather than a self-service one."""
    print("\n# leaving gives the seat back and voids the unpaid share; the host can't leave")
    from community import games
    _enable_seat_rule(s, fx)
    host, joiner = fx.members[0], fx.members[1]
    _membership_for_court(s, fx, host)
    bid = _open_game(s, fx, host, hour=12)["booking"]["id"]
    games.join_game(s, club_id=fx.club_id, booking_id=bid, user_id=joiner)
    check("the joiner has a debt",
          any(str(x["user_id"]) == str(joiner) and x["amount_minor"] > 0
              for x in _seat_rows(s, bid)), str(_seat_rows(s, bid)))

    games.leave_game(s, club_id=fx.club_id, booking_id=bid, user_id=joiner)
    # The HOST's own R0 membership_covered order legitimately survives and reads 'paid' — a covered
    # seat is settled, not owed. Only the LEAVER's debt must be gone, and their seat detached from it.
    after = _seat_rows(s, bid)
    check("…which is VOIDED and detached when they leave",
          all(str(x["user_id"]) != str(joiner) for x in after), str(after))
    check("…while the host's own covered seat is untouched",
          any(str(x["user_id"]) == str(host) and x["amount_minor"] == 0 for x in after), str(after))
    check("the seat is open again",
          games.game_detail(s, club_id=fx.club_id, booking_id=bid)["open_seats"] == 1)

    try:
        games.leave_game(s, club_id=fx.club_id, booking_id=bid, user_id=host)
        check("the HOST cannot leave their own booking", False, "no GameError")
    except games.GameError as e:
        check("the HOST cannot leave their own booking (that's a cancel)",
              e.code == "HOST_CANNOT_LEAVE", e.code)


def sc_open_game_sweep_collapses_and_is_idempotent(s, fx):
    """The hourly job. It must be safe to run all day: the collapse charges once, and a second sweep
    finds nothing to do."""
    print("\n# the open-game sweep collapses an unfilled seat exactly once")
    from community.crons import sweep_open_games
    _enable_seat_rule(s, fx)
    host = fx.members[0]
    _membership_for_court(s, fx, host)
    bid = _open_game(s, fx, host, hour=9)["booking"]["id"]
    # Push the fill deadline into the past — the sweep's trigger condition.
    s.execute(text("UPDATE diary.booking SET open_until = now() - interval '1 hour' WHERE id=:b"),
              {"b": bid})

    out = sweep_open_games(s, club_id=fx.club_id)
    check("the sweep collapses the unfilled seat", out.get("collapsed") == 1, str(out))
    rows = _seat_rows(s, bid)
    check("…and the holder is billed ONE SHARE (R80) for it",
          any(x["amount_minor"] == 8000 and str(x["user_id"]) == str(host) for x in rows), str(rows))

    again = sweep_open_games(s, club_id=fx.club_id)
    check("a second sweep collapses NOTHING (idempotent)", again.get("collapsed") == 0, str(again))
    check("…and raises no second charge", len(_seat_rows(s, bid)) == len(rows), str(_seat_rows(s, bid)))


def sc_community_reads_never_leak_contact_details(s, fx):
    """The privacy promise the whole feature rests on: match chat exists precisely so nobody has to
    swap a phone number, so no community read may return one. Discovery is also OPT-IN — being
    findable by 1,100 strangers is a choice, not something a member acquires because we shipped."""
    print("\n# community reads carry names and levels — never an email or a phone")
    from community import games, matching, repositories as repo
    _enable_seat_rule(s, fx)
    host, other = fx.members[0], fx.members[1]
    _membership_for_court(s, fx, host)
    bid = _open_game(s, fx, host, hour=16)["booking"]["id"]

    detail = games.game_detail(s, club_id=fx.club_id, booking_id=bid, viewer_user_id=other)
    blob = str(detail)
    check("the game detail has no email address", "@" not in blob, blob[:300])
    check("…and shows the VIEWER no other player's amount",
          all(x["amount_minor"] is None for x in detail["seats"] if not x["is_me"]), str(detail["seats"]))

    check("a member is NOT discoverable until they opt in",
          all(p["user_id"] != str(other) for p in
              matching.suggest_players(s, club_id=fx.club_id, user_id=host)))
    repo.upsert_player_profile(s, club_id=fx.club_id, user_id=other,
                               fields={"level_num": 5.0, "visible_in_community": True})
    sugg = matching.suggest_players(s, club_id=fx.club_id, user_id=host)
    check("…and IS once they do", any(p["user_id"] == str(other) for p in sugg), str(sugg))
    check("suggestions carry no email either", "@" not in str(sugg), str(sugg)[:300])


def sc_a_crafted_game_cannot_cheapen_or_outlive_its_own_bill(s, fx):
    """`seats`, `play_format` and `visibility` arrive off the REQUEST BODY, so they get the same
    treatment as the posted product_id: read, then bounded server-side.

    Two things a crafted request would otherwise buy. A huge `seats` divides one court fee into
    unpayable slivers, so it is clamped. And `open_until` — the instant an unfilled seat becomes the
    holder's to pay for — is NEVER taken from the body at all: a client that could set it could push
    its own charge past the game and never be billed for the court it held."""
    print("\n# a crafted game can't cheapen its share or push its own deadline past the game")
    from community import seats as S
    _enable_seat_rule(s, fx)
    m = fx.members[0]
    _membership_for_court(s, fx, m)
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         settlement_mode="membership_covered",
                         starts_at=utc_iso(at(fx, 18)), ends_at=utc_iso(at(fx, 19)),
                         play_format="singles", seats=99, visibility="open",
                         # A caller trying to outlive its own bill: a deadline AFTER the game starts.
                         open_until=at(fx, 23))
    check("the booking still succeeds", r.get("ok"), str(r))
    bid = r["booking"]["id"]
    row = s.execute(text("SELECT seats, open_until, starts_at FROM diary.booking WHERE id=:b"),
                    {"b": bid}).mappings().first()
    check("a 99-seat singles court is clamped, not honoured", int(row["seats"]) <= 8, str(row["seats"]))
    check("the fill deadline is the CLUB's, and falls BEFORE the game starts",
          row["open_until"] is not None and row["open_until"] < row["starts_at"], str(dict(row)))
    check("…so the sweep can still collapse it and bill the holder",
          S.seat_plan(s, club_id=fx.club_id, booking_id=bid)["open_count"] > 0)


def sc_the_seat_rule_can_be_switched_on_without_sql(s, fx):
    """The owner has to be able to turn this on, see what it is doing, and correct a wrong level —
    without a database client.

    This is not a nicety. The entitlement caps (max_covered_per_day / max_covered_minutes) shipped
    correct and then sat INERT for weeks because the only way to set them was SQL, and everyone
    assumed a shipped feature was a working one. A money rule with no switch is a money rule nobody
    turns on."""
    print("\n# the owner can switch the seat rule on, see it working, and fix a level — no SQL")
    from community import repositories as repo, seats as S
    m, other = fx.members[0], fx.members[1]

    cfg = repo.settings(s, club_id=fx.club_id)
    check("it reads OFF to begin with", cfg["seat_rule_enforced"] is False, str(cfg))

    cfg = repo.save_settings(s, club_id=fx.club_id,
                             fields={"community_enabled": True, "seat_rule_enforced": True,
                                     "open_game_cutoff_hours": 6, "seat_pay_hours": 48})
    check("switching it on sticks", cfg["seat_rule_enforced"] is True, str(cfg))
    check("…and the money core sees the SAME switch",
          S.policy(s, fx.club_id)["seat_rule_enforced"] is True)
    check("the timings save too", cfg["open_game_cutoff_hours"] == 6 and cfg["seat_pay_hours"] == 48,
          str(cfg))
    check("an absurd timing is clamped, not stored",
          repo.save_settings(s, club_id=fx.club_id,
                             fields={"seat_pay_hours": 99999})["seat_pay_hours"] == 720)

    # The operational read the owner actually scans: is anyone about to play on an unpaid court?
    _membership_for_court(s, fx, m)
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         settlement_mode="membership_covered",
                         starts_at=utc_iso(at(fx, 15)), ends_at=utc_iso(at(fx, 16)),
                         extra_clients=[{"user_id": str(other)}], play_format="singles")
    check("the game was made", r.get("ok"), str(r))
    games = repo.admin_games(s, club_id=fx.club_id)
    mine = [g for g in games if g["booking_id"] == str(r["booking"]["id"])]
    check("the owner's games list shows it", len(mine) == 1, str(games))
    check("…with the money still owed on it, which is the number they scan for",
          mine and mine[0]["owed_minor"] == 8000, str(mine))

    cfg2 = repo.settings(s, club_id=fx.club_id)
    check("the dashboard counts the unpaid seat too", cfg2["unpaid_seats_minor"] == 8000, str(cfg2))

    # A coach correcting a self-rating — the fix for "everybody is advanced".
    repo.upsert_player_profile(s, club_id=fx.club_id, user_id=other,
                               fields={"level_num": 9.0, "visible_in_community": True})
    check("a self-declared level is recorded as SELF",
          repo.player_profile(s, club_id=fx.club_id, user_id=other)["level_source"] == "self")
    repo.set_level_as_staff(s, club_id=fx.club_id, user_id=other, level_num=5.0,
                            set_by_user_id=fx.coach_uid)
    prof = repo.player_profile(s, club_id=fx.club_id, user_id=other)
    check("a coach's correction overwrites it", prof["level_num"] == 5.0, str(prof))
    check("…and is recorded as an ASSESSMENT, not a self-rating",
          prof["level_source"] == "coach", str(prof))
    check("the players list shows both the level and who set it",
          any(p["user_id"] == str(other) and p["level"] == 5.0 and p["level_source"] == "coach"
              for p in repo.admin_players(s, club_id=fx.club_id)))


def sc_match_chat_is_private_to_the_players(s, fx):
    """Match chat exists so nobody has to swap a phone number — which is the whole reason every
    community read can stay free of contact details. That promise is only worth anything if the chat
    itself is closed: a stranger who can read it gets the details the API refused to give them."""
    print("\n# match chat is readable and postable ONLY by the players in the game")
    from community import chat, games
    _enable_seat_rule(s, fx)
    host, joiner, stranger = fx.members[0], fx.members[1], fx.members[2]
    _membership_for_court(s, fx, host)
    bid = _open_game(s, fx, host, hour=11)["booking"]["id"]

    check("the host is in their own game", chat.is_in_game(s, booking_id=bid, user_id=host) is True)
    check("a stranger is not", chat.is_in_game(s, booking_id=bid, user_id=stranger) is False)
    try:
        chat.post_message(s, club_id=fx.club_id, booking_id=bid, user_id=stranger, body="hello")
        check("a stranger cannot post", False, "no ChatError")
    except chat.ChatError as e:
        check("a stranger cannot post (NOT_IN_GAME)", e.code == "NOT_IN_GAME", e.code)
    try:
        chat.list_messages(s, club_id=fx.club_id, booking_id=bid, user_id=stranger)
        check("…nor read", False, "no ChatError")
    except chat.ChatError as e:
        check("…nor read it (NOT_IN_GAME)", e.code == "NOT_IN_GAME", e.code)

    games.join_game(s, club_id=fx.club_id, booking_id=bid, user_id=joiner)
    check("joining the game earns the right to post",
          chat.post_message(s, club_id=fx.club_id, booking_id=bid, user_id=joiner,
                            body="on my way").get("ok") is True)
    msgs = chat.list_messages(s, club_id=fx.club_id, booking_id=bid, user_id=host)
    check("the host can read it", any(m["body"] == "on my way" for m in msgs), str(msgs))
    check("…and the join is on the timeline as a system line",
          any(m["system"] and "joined" in m["body"] for m in msgs), str(msgs))
    check("an empty message is refused, not stored",
          _raises(chat.ChatError, chat.post_message, s, club_id=fx.club_id, booking_id=bid,
                  user_id=host, body="   ") == "EMPTY_MESSAGE")
    check("staff can read it without being in the game (support)",
          isinstance(chat.list_messages(s, club_id=fx.club_id, booking_id=bid,
                                        user_id=stranger, staff=True), list))

    # WHO COUNTS AS STAFF IS THE WHOLE QUESTION (owner's call, 2026-08-12). `staff=True` is a support
    # power — reading a conversation between members who use chat precisely so they never have to
    # swap phone numbers — and support is an OWNER mandate. A coach has no role in a members' court
    # booking, earns nothing from it and never sees it on their P&L, yet `_staff` used to include
    # them: every coach could read any game's chat AND invite a stranger into any game's seat.
    # Nothing surfaced it (the coach app does not mount Widgets.Game, deliberately), so it sat unused
    # — which is exactly how it would have shipped as a real exposure the day someone mounted it.
    from community import routes as _croutes

    class _P:
        def __init__(self, role):
            self.role = role
    check("a COACH is not staff here — no reading members' private chat",
          _croutes._staff(_P("coach")) is False)
    check("…and therefore cannot invite a stranger into someone else's game either",
          _croutes._staff(_P("coach")) is False)
    check("the owner still can (support is an owner mandate)",
          _croutes._staff(_P("club_admin")) is True)
    check("so can the platform admin", _croutes._staff(_P("platform_admin")) is True)
    check("a plain member is not staff", _croutes._staff(_P("member")) is False)
    # A coach who is genuinely PLAYING is a player in that moment, and every caller falls through to
    # is_in_game — so this rule takes nothing away from a coach who actually booked a seat.
    check("a coach who is actually in the game still qualifies as a player",
          chat.is_in_game(s, booking_id=bid, user_id=joiner) is True)

    # The lane's OTHER dormant coach grant, removed the same day: listing every player in the club and
    # overwriting their level. The reasoning for it was sound (a coach who has watched someone play
    # beats a five-question self-quiz) but no coach UI ever called it, so it was a standing permission
    # over a members-only feature with nothing exercising it. Pinned as owner-only: if a coach
    # assessment returns it should return as a real screen, not as a grant nobody remembers.
    # Those two routes now gate on `_admin(p, "view_master_diary")` ALONE, so the permission table is
    # what refuses a coach — assert on the real decision rather than on the route text.
    from iam.permissions import can

    class _CP:
        def __init__(self, role):
            self.role, self.club_id = role, fx.club_id
    check("the permission a coach lost is genuinely denied to them",
          can(_CP("coach"), "view_master_diary", {"club_id": fx.club_id}) is False)
    check("…and genuinely granted to the owner",
          can(_CP("club_admin"), "view_master_diary", {"club_id": fx.club_id}) is True)


def _raises(exc_type, fn, *a, **kw):
    """Call fn and return the .code of the expected error, or a marker. Keeps the assertions above
    readable when the point is WHICH refusal happened."""
    try:
        fn(*a, **kw)
        return "NO_ERROR"
    except exc_type as e:
        return getattr(e, "code", "ERROR")


def sc_a_result_needs_someone_else_to_confirm_it(s, fx):
    """A result reported by one player is a CLAIM. Confirmation has to come from somebody else, or
    it is the same claim twice — and an unconfirmed claim must never be treated as evidence, because
    the whole point of recording results is to eventually calibrate people's levels from them."""
    print("\n# a result is a claim until the OTHER player confirms it")
    from community import games, results
    _enable_seat_rule(s, fx)
    host, joiner, stranger = fx.members[0], fx.members[1], fx.members[2]
    _membership_for_court(s, fx, host)
    bid = _open_game(s, fx, host, hour=13)["booking"]["id"]
    games.join_game(s, club_id=fx.club_id, booking_id=bid, user_id=joiner)

    check("someone who didn't play can't record a result",
          _raises(results.ResultError, results.record_result, s, club_id=fx.club_id,
                  booking_id=bid, user_id=stranger, outcome="played") == "NOT_IN_GAME")
    check("a made-up outcome is refused",
          _raises(results.ResultError, results.record_result, s, club_id=fx.club_id,
                  booking_id=bid, user_id=host, outcome="thrashed") == "BAD_OUTCOME")
    out = results.record_result(s, club_id=fx.club_id, booking_id=bid, user_id=host,
                                outcome="played", winner_user_id=host, score_text="6-4 6-3")
    check("the player who was there can record it", out.get("ok") and out["confirmed"] is False, str(out))
    check("…and cannot confirm their OWN claim (the trap)",
          _raises(results.ResultError, results.confirm_result, s, club_id=fx.club_id,
                  booking_id=bid, user_id=host) == "CANNOT_SELF_CONFIRM")
    check("the other player can", results.confirm_result(
        s, club_id=fx.club_id, booking_id=bid, user_id=joiner)["confirmed"] is True)

    # A re-report is a NEW claim, so the old confirmation must fall away — otherwise one player
    # confirms a scoreline and the other quietly rewrites it afterwards.
    results.record_result(s, club_id=fx.club_id, booking_id=bid, user_id=joiner,
                          outcome="played", winner_user_id=joiner, score_text="7-5 6-4")
    still = s.execute(text("SELECT confirmed_at, score_text FROM community.match_result "
                           " WHERE booking_id = :b"), {"b": bid}).mappings().first()
    check("re-reporting WITHDRAWS the previous confirmation", still["confirmed_at"] is None, str(dict(still)))
    check("…and there is still exactly ONE result row, not a pile of claims",
          s.execute(text("SELECT count(*) FROM community.match_result WHERE booking_id=:b"),
                    {"b": bid}).scalar() == 1)

    # The private signal. It is never rendered anywhere — it only weights matching.
    results.play_again(s, club_id=fx.club_id, booking_id=bid, rater_user_id=host,
                       subject_user_id=joiner, again=False)
    results.play_again(s, club_id=fx.club_id, booking_id=bid, rater_user_id=host,
                       subject_user_id=joiner, again=True)     # changed their mind
    check("would-play-again is one row per pair, not a stack",
          s.execute(text("SELECT count(*) FROM community.play_again WHERE booking_id=:b"),
                    {"b": bid}).scalar() == 1)
    check("you can't rate yourself",
          _raises(results.ResultError, results.play_again, s, club_id=fx.club_id, booking_id=bid,
                  rater_user_id=host, subject_user_id=host, again=True) == "CANNOT_RATE_SELF")


def sc_the_result_screen_offers_only_what_the_server_allows(s, fx):
    """The result UI is driven ENTIRELY by game_detail's can{} and the viewer's own rate[] — never by
    the browser working it out.

    Two things it would be easy to get wrong, and both are money-free but trust-critical:

    1. WHETHER THE GAME IS OVER IS THE CLUB'S CLOCK, NOT THE PHONE'S. The old widget compared
       `new Date(game.ends_at) < new Date()`, so a member whose phone clock was wrong (or who simply
       changed their time zone while travelling) could be offered "Enter the result" mid-match, or
       denied it afterwards. The gate moved server-side.

    2. THE WOULD-PLAY-AGAIN SIGNAL IS PRIVATE, AND A READ KEYED ON THE BOOKING ALONE LEAKS IT. The
       rate[] block has to be filtered by rater_user_id = the viewer. Get that wrong and the payload
       cheerfully tells a player that the person they just played would rather not play them again —
       which is exactly the reputation system results.py refuses to build, delivered by accident."""
    print("\n# the result screen is driven by the server's can{}, and rate[] is PRIVATE")
    from community import games, results
    _enable_seat_rule(s, fx)
    host, joiner = fx.members[0], fx.members[1]
    _membership_for_court(s, fx, host)
    bid = _open_game(s, fx, host, hour=15)["booking"]["id"]
    games.join_game(s, club_id=fx.club_id, booking_id=bid, user_id=joiner)

    # --- while the game is still in the future -------------------------------
    d = games.game_detail(s, club_id=fx.club_id, booking_id=bid, viewer_user_id=host)
    check("a game that hasn't happened yet offers NO result button", d["can"]["record_result"] is False)
    check("…and carries no result", d["result"] is None)
    check("…and asks nobody to rate anybody", d["rate"] == [], str(d["rate"]))

    # --- move it into the past (the club's clock, not the caller's) ----------
    s.execute(text("UPDATE diary.booking SET starts_at = now() - interval '2 hours', "
                   "       ends_at = now() - interval '1 hour' WHERE id = :b"), {"b": bid})

    d = games.game_detail(s, club_id=fx.club_id, booking_id=bid, viewer_user_id=host)
    check("once it's over, a player who was there may record it", d["can"]["record_result"] is True)
    check("…but there is nothing to confirm yet", d["can"]["confirm_result"] is False)
    check("…and the rate list now names the OTHER player, unanswered",
          [(r["user_id"], r["again"]) for r in d["rate"]] == [(str(joiner), None)], str(d["rate"]))

    results.record_result(s, club_id=fx.club_id, booking_id=bid, user_id=host,
                          outcome="played", winner_user_id=host, score_text="6-2 6-1")

    mine = games.game_detail(s, club_id=fx.club_id, booking_id=bid, viewer_user_id=host)
    check("the reporter sees their own claim", mine["result"]["reported_by_me"] is True)
    check("…marked unconfirmed", mine["result"]["confirmed"] is False)
    check("…and is NOT offered a Confirm button on it (the trap)",
          mine["can"]["confirm_result"] is False)

    theirs = games.game_detail(s, club_id=fx.club_id, booking_id=bid, viewer_user_id=joiner)
    check("the other player IS offered one", theirs["can"]["confirm_result"] is True)
    check("…and is told it wasn't their claim", theirs["result"]["reported_by_me"] is False)

    results.confirm_result(s, club_id=fx.club_id, booking_id=bid, user_id=joiner)
    after = games.game_detail(s, club_id=fx.club_id, booking_id=bid, viewer_user_id=joiner)
    check("once agreed it reads as confirmed", after["result"]["confirmed"] is True)
    check("…and nobody is asked to confirm it twice", after["can"]["confirm_result"] is False)

    # --- THE PRIVACY TRAP ----------------------------------------------------
    # This has to be a DOUBLES game, and that is the whole point. In a two-player game the subject
    # uniquely identifies the rater, so dropping the `rater_user_id = viewer` filter is completely
    # unobservable — a two-player assertion here passes for the wrong reason and guards nothing.
    # With three players there is a pair the viewer is NOT part of, and that is where the leak shows.
    results.play_again(s, club_id=fx.club_id, booking_id=bid, rater_user_id=host,
                       subject_user_id=joiner, again=False)
    h = games.game_detail(s, club_id=fx.club_id, booking_id=bid, viewer_user_id=host)
    check("the rater sees their OWN answer back",
          [(r["user_id"], r["again"]) for r in h["rate"]] == [(str(joiner), False)], str(h["rate"]))

    third = fx.members[2]
    # A DIFFERENT court and a different past window — the first game is already parked in
    # now()-2h..now()-1h, and the GiST no-overlap constraint applies to backdated rows too.
    dbl = _open_game(s, fx, host, hour=17, seats=4, court=1)["booking"]["id"]
    games.join_game(s, club_id=fx.club_id, booking_id=dbl, user_id=joiner)
    games.join_game(s, club_id=fx.club_id, booking_id=dbl, user_id=third)
    s.execute(text("UPDATE diary.booking SET starts_at = now() - interval '5 hours', "
                   "       ends_at = now() - interval '4 hours' WHERE id = :b"), {"b": dbl})
    # The host privately says they'd rather not play `third` again. `joiner` was in the same game
    # but is no party to that opinion.
    results.play_again(s, club_id=fx.club_id, booking_id=dbl, rater_user_id=host,
                       subject_user_id=third, again=False)

    seen = games.game_detail(s, club_id=fx.club_id, booking_id=dbl, viewer_user_id=joiner)
    answers = dict((r["user_id"], r["again"]) for r in seen["rate"])
    check("a third player is asked about BOTH of the others", sorted(answers) == sorted([str(host), str(third)]),
          str(answers))
    check("THE HOST'S PRIVATE VERDICT ON A THIRD PLAYER DOES NOT LEAK (the trap)",
          answers.get(str(third)) is None, str(answers))
    check("…nor does anything else the viewer didn't say themselves",
          all(v is None for v in answers.values()), str(answers))
    # And the rater still sees their own, in the same game, so the filter isn't just blanking it.
    own = dict((r["user_id"], r["again"]) for r in
               games.game_detail(s, club_id=fx.club_id, booking_id=dbl,
                                 viewer_user_id=host)["rate"])
    check("…while the host still sees the verdict they actually gave",
          own.get(str(third)) is False, str(own))


def sc_the_community_emails_say_the_thing_that_matters(s, fx):
    """The five community notifications, rendered against the payloads the lane ACTUALLY emits.

    These templates are pure functions with no DB, so nothing else exercises them — which is exactly
    the shape CLAUDE.md warns about for the confirmation-email block: assembled by hand, only ever
    run in production, and a renamed payload key blanks them SILENTLY rather than raising.

    The one that must never regress is `game_seat_collapsed`. It is the email that explains a charge
    the member did not choose — their spare seat went unfilled, so the court time became theirs. A
    charge that arrives with no explanation is a support ticket and a trust problem, which is the
    whole reason `_emit_collapsed` exists. If the amount ever stops rendering, the email still sends,
    still reads as a complete sentence, and quietly stops doing its job.

    Also pinned: the two events deliberately NOT wired to email. `game_opened` is promotion, not a
    receipt, and `game_result_recorded` would email everyone every time a scoreline is typed."""
    print("\n# the community emails render, and the collapsed-seat one STATES THE AMOUNT")
    from marketing_crm import notifications as N

    for ev in ("player_invited", "game_seat_taken", "game_seat_unpaid_reminder",
               "game_seat_collapsed", "game_full"):
        check(f"{ev} is wired to a notification", ev in N.KIND_MAP)
    for ev in ("game_opened", "game_result_recorded"):
        check(f"{ev} is deliberately NOT emailed (noise, not a receipt)", ev not in N.KIND_MAP)

    # The EXACT payload community.crons._emit_collapsed sends — note it carries no currency_code,
    # so this also pins that the money helper still defaults to ZAR rather than dropping the symbol.
    collapsed = {"club_id": str(fx.club_id), "email": "someone@scratch.test",
                 "user_id": str(fx.members[0]), "ref_type": "booking", "ref_id": "x",
                 "amount_minor": 15000, "starts_at": "2026-08-12T10:00:00+00:00"}
    title, body, link = N.KIND_MAP["game_seat_collapsed"](collapsed)
    check("the collapsed-seat email has a subject", bool(title), repr(title))
    check("…NAMES THE AMOUNT the member is about to be charged (the trap)",
          "150.00" in (body or ""), repr(body))
    check("…with the currency symbol, even though the emit sends no currency_code",
          "R150.00" in (body or ""), repr(body))
    check("…says the court is still theirs, so it doesn't read as a cancellation",
          "still booked" in (body or "").lower(), repr(body))
    check("…and links into the app", bool(link), repr(link))

    # A missing amount must not silently render a sentence that looks fine.
    _t, body_noamt, _l = N.KIND_MAP["game_seat_collapsed"](dict(collapsed, amount_minor=None))
    check("with no amount it degrades to a readable sentence rather than 'RNone'",
          "None" not in (body_noamt or ""), repr(body_noamt))

    # The rest just have to render — a template raising would lose the notification entirely
    # (deliver() catches it and falls back to a title-cased event name, which helps nobody).
    for ev in ("player_invited", "game_seat_taken", "game_seat_unpaid_reminder", "game_full"):
        t, b, _ = N.KIND_MAP[ev]({"club_id": str(fx.club_id), "email": "a@b.test",
                                  "user_id": str(fx.members[0]), "ref_type": "booking",
                                  "ref_id": "x", "url": "https://example.test/join"})
        check(f"{ev} renders a subject and a body", bool(t) and bool(b), f"{t!r} / {b!r}")

    check("the unpaid-seat nudge explains WHY the court isn't confirmed yet",
          "every player has paid" in N.KIND_MAP["game_seat_unpaid_reminder"]({})[1].lower(),
          N.KIND_MAP["game_seat_unpaid_reminder"]({})[1])


def sc_matching_puts_the_right_level_first(s, fx):
    """The single biggest determinant of whether this feature works. People stop using these products
    when they are repeatedly matched far above or below their standard, so level dominates the score —
    and a player they have said they would rather not play again is DROPPED, not merely ranked down."""
    print("\n# matching is deterministic and level-led, and honours the private signal")
    from community import matching, repositories as repo
    me = fx.members[0]
    close, far = fx.members[1], fx.members[2]
    repo.upsert_player_profile(s, club_id=fx.club_id, user_id=me,
                               fields={"level_num": 5.0, "prefers_times": ["mon_pm"],
                                       "visible_in_community": True})
    repo.upsert_player_profile(s, club_id=fx.club_id, user_id=close,
                               fields={"level_num": 5.2, "prefers_times": ["mon_pm"],
                                       "visible_in_community": True})
    repo.upsert_player_profile(s, club_id=fx.club_id, user_id=far,
                               fields={"level_num": 9.0, "prefers_times": ["sat_am"],
                                       "visible_in_community": True})

    out = matching.suggest_players(s, club_id=fx.club_id, user_id=me)
    ids = [p["user_id"] for p in out]
    check("both opted-in players are offered", str(close) in ids and str(far) in ids, str(out))
    check("the CLOSE level ranks above the far one", ids.index(str(close)) < ids.index(str(far)),
          str(out))
    check("…and scores higher", out[ids.index(str(close))]["match_pct"]
          > out[ids.index(str(far))]["match_pct"], str(out))
    check("the score is deterministic — the same call twice gives the same answer",
          [p["match_pct"] for p in matching.suggest_players(s, club_id=fx.club_id, user_id=me)]
          == [p["match_pct"] for p in out], str(out))

    # The five-question quiz: answers about what you DO, mapped onto the 1–10 scale.
    lo = matching.level_from_answers({q["key"]: 0 for q in matching.ONBOARDING_QUESTIONS})
    hi = matching.level_from_answers({q["key"]: 3 for q in matching.ONBOARDING_QUESTIONS})
    check("the quiz floors at 1.0 and tops out below Elite", lo == 1.0 and 8.5 <= hi <= 9.5,
          f"{lo} .. {hi}")
    check("no answers means no level, not a guess of zero",
          matching.level_from_answers({}) is None)


def sc_the_sweep_reminds_then_releases_an_unpaid_seat(s, fx):
    """The other two thirds of the hourly job. Collapse is already pinned; this is the REMIND and
    RELEASE half — a seat somebody accepted and never paid for must not sit on a court all week, and
    the person must be nudged while they can still act."""
    print("\n# the sweep nudges an unpaid seat, then releases it when the window passes")
    from community.crons import sweep_open_games
    _enable_seat_rule(s, fx)
    p1, p2 = fx.members[0], fx.members[1]
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=p1, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         settlement_mode="online",
                         starts_at=utc_iso(at(fx, 17)), ends_at=utc_iso(at(fx, 18)),
                         # OPEN — and it always had to be: the sweep selects `visibility = 'open'`,
                         # so a private booking was never reachable by it in the first place.
                         extra_clients=[{"user_id": str(p2)}], play_format="singles",
                         visibility="open")
    bid = r["booking"]["id"]
    check("two unpaid online seats", len(_seat_rows(s, bid)) == 2, str(_seat_rows(s, bid)))

    out = sweep_open_games(s, club_id=fx.club_id)
    check("the sweep nudges the unpaid seats", int(out.get("reminded") or 0) >= 1, str(out))
    again = sweep_open_games(s, club_id=fx.club_id)
    check("…once each, however often it runs (deduped on reminder_log)",
          int(again.get("reminded") or 0) == 0, str(again))
    check("nothing is released while the window is still open",
          int(out.get("released") or 0) == 0 and int(again.get("released") or 0) == 0, str(again))

    # The pay-by window lapses.
    s.execute(text("UPDATE diary.booking SET held_until = now() - interval '1 minute' WHERE id=:b"),
              {"b": bid})
    third = sweep_open_games(s, club_id=fx.club_id)
    check("the unpaid GUEST seat is released", int(third.get("released") or 0) >= 1, str(third))
    left = [x for x in _seat_rows(s, bid) if str(x["user_id"]) == str(p2)]
    check("…and its debt no longer stands against them", left == [], str(_seat_rows(s, bid)))
    check("the HOST's seat is never released by the sweep — that would cancel their own booking",
          any(str(x["user_id"]) == str(p1) for x in _seat_rows(s, bid)), str(_seat_rows(s, bid)))


def sc_a_game_says_what_kind_of_tennis_it_is(s, fx):
    """INTENT IS A SEPARATE AXIS FROM SEAT COUNT, and it has to be, because they answer different
    questions. `play_format` (singles/doubles) decides how many seats share the court fee — it is a
    MONEY field. `play_intent` (social/practice/competitive) is what the players actually want out of
    the session.

    Conflating them would mean "I just want a relaxed hit" could only be said by changing how many
    people pay, which is nonsense — and mismatched intent spoils a session as reliably as a mismatched
    level does. Turning up for a friendly hit against someone grinding out a practice match is the
    fastest way to stop using a feature like this."""
    print("\n# a game carries its INTENT, filterable, and separate from the seat count")
    from community import games
    _enable_seat_rule(s, fx)
    host = fx.members[0]
    _membership_for_court(s, fx, host)

    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=host, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         settlement_mode="membership_covered",
                         starts_at=utc_iso(at(fx, 13)), ends_at=utc_iso(at(fx, 14)),
                         play_format="singles", visibility="open", play_intent="social")
    check("a social singles game is created", r.get("ok"), str(r))
    bid = r["booking"]["id"]
    check("the intent is stored on the booking",
          s.execute(text("SELECT play_intent FROM diary.booking WHERE id=:b"),
                    {"b": bid}).scalar() == "social")
    check("…and the SEAT COUNT is untouched by it — they are different questions",
          s.execute(text("SELECT seats FROM diary.booking WHERE id=:b"), {"b": bid}).scalar() == 2)

    r2 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=host, role="member",
                          booking_type="court", resource_id=fx.courts[1],
                          settlement_mode="membership_covered",
                          starts_at=utc_iso(at(fx, 15)), ends_at=utc_iso(at(fx, 16)),
                          play_format="singles", visibility="open", play_intent="competitive")
    other = fx.members[1]
    feed = games.list_open_games(s, club_id=fx.club_id, user_id=other)
    check("both games are in the unfiltered feed", len(
        [g for g in feed if g["booking_id"] in (str(bid), str(r2["booking"]["id"]))]) == 2, str(feed))
    check("the feed carries the intent so a card can show it",
          all(g.get("play_intent") for g in feed if g["booking_id"] == str(bid)), str(feed))

    social = games.list_open_games(s, club_id=fx.club_id, user_id=other, play_intent="social")
    check("filtering to SOCIAL returns the social game",
          any(g["booking_id"] == str(bid) for g in social), str(social))
    check("…and NOT the competitive one — 'just a hit' is now sayable",
          not any(g["booking_id"] == str(r2["booking"]["id"]) for g in social), str(social))

    check("an unknown intent is discarded, never stored",
          B.create_booking(s, club_id=fx.club_id, booked_by_user_id=host, role="member",
                           booking_type="court", resource_id=fx.courts[0],
                           settlement_mode="membership_covered",
                           starts_at=utc_iso(at(fx, 19)), ends_at=utc_iso(at(fx, 20)),
                           play_format="singles", play_intent="ANNIHILATION").get("ok"))


def sc_the_feed_defaults_to_games_around_my_level(s, fx):
    """Being repeatedly shown games far above or below your standard is the single most reliable way
    to make someone stop opening this screen. So the feed's DEFAULT is a band around the caller's own
    level — but it must degrade to EVERYTHING for a member who hasn't set one, because an empty feed
    reads as "no games here" rather than "tell us your level", and that is the wrong lesson to teach
    on a first visit."""
    print("\n# the feed defaults to games around MY level — and shows everything if I have none")
    from community import games, repositories as repo
    _enable_seat_rule(s, fx)
    me, peer, faraway = fx.members[0], fx.members[1], fx.members[2]
    _membership_for_court(s, fx, peer)
    _membership_for_court(s, fx, faraway)
    repo.upsert_player_profile(s, club_id=fx.club_id, user_id=peer,
                               fields={"level_num": 5.0, "visible_in_community": True})
    repo.upsert_player_profile(s, club_id=fx.club_id, user_id=faraway,
                               fields={"level_num": 9.0, "visible_in_community": True})
    near = _open_game(s, fx, peer, hour=9)["booking"]["id"]
    far = _open_game(s, fx, faraway, hour=10, court=1)["booking"]["id"]

    # I have NO level yet.
    all_games = games.list_open_games(s, club_id=fx.club_id, user_id=me, near_my_level=1.5)
    ids = [g["booking_id"] for g in all_games]
    check("with no level of my own I see EVERYTHING, not an empty screen",
          str(near) in ids and str(far) in ids, str(ids))

    repo.upsert_player_profile(s, club_id=fx.club_id, user_id=me, fields={"level_num": 5.2})
    banded = games.list_open_games(s, club_id=fx.club_id, user_id=me, near_my_level=1.5)
    bids = [g["booking_id"] for g in banded]
    check("once I have a level, a game near it is shown", str(near) in bids, str(bids))
    check("…and one three levels away is not", str(far) not in bids, str(bids))
    wide = games.list_open_games(s, club_id=fx.club_id, user_id=me)
    check("'show everything' is still one call away — the band is a default, not a cage",
          str(far) in [g["booking_id"] for g in wide], str(wide))


def sc_dark_means_dark_for_the_whole_lane(s, fx):
    """A club that has not switched the community on must get NOTHING — not an empty version of it.

    The flag used to be checked one action at a time: join and set_visibility asked, the feed, the
    profile, chat, results and matching did not. So a member could reach the screens by typing the URL
    and find a working-but-empty feature, which is worse than an absent one because it looks BROKEN
    rather than unbuilt. The gate now lives in one `before_request`, so a new endpoint is covered by
    default and has to opt out on purpose."""
    print("\n# with the community OFF the lane refuses everything — except the switch itself")
    from community import games, seats
    # The scratch club's policy row exists with both switches at their defaults.
    pol = seats.policy(s, fx.club_id)
    check("the club starts with the community OFF", pol["community_enabled"] is False, str(pol))

    host = fx.members[0]
    _membership_for_court(s, fx, host)
    try:
        games.join_game(s, club_id=fx.club_id, booking_id=fx.courts[0], user_id=host)
        check("joining is refused while the lane is dark", False, "no GameError")
    except games.GameError as e:
        check("joining is refused while the lane is dark (COMMUNITY_DISABLED)",
              e.code == "COMMUNITY_DISABLED", e.code)

    # …and the OWNER can still reach the switch, which is the one thing that must never be gated —
    # gating it would make the feature unreachable by design.
    from community import repositories as repo
    cfg = repo.settings(s, club_id=fx.club_id)
    check("the owner can still READ the settings with it off", cfg is not None and
          cfg["community_enabled"] is False, str(cfg))
    cfg = repo.save_settings(s, club_id=fx.club_id, fields={"community_enabled": True})
    check("…and still WRITE them — the switch is never behind its own flag",
          cfg["community_enabled"] is True, str(cfg))
    check("once on, the lane answers", seats.policy(s, fx.club_id)["community_enabled"] is True)


def sc_joining_a_game_bills_nobody_while_the_money_switch_is_off(s, fx):
    """THE MONEY SWITCH GATES EVERY SEAT PATH, not just the one that creates a booking.

    Found live 2026-08-11, and it is the exact failure the two-switch design promises cannot happen.
    Tomo had "Community features" ON and "Charge for every seat" OFF — the state the admin screen
    describes as "you can give members the feature before you change what anyone pays". Tshepo took
    a seat in his game, and the club dashboard then read **Seats unpaid R110.00**: a real
    billing."order" row, for a 90-minute share, in a club that had not switched charging on.

    The gate lived in the CALLERS. `seat_a_new_booking` had it and the cron had it; `join_game`,
    `set_visibility` and invite-accept did not — three of four callers forgot, which is what a rule
    in the wrong place looks like. It is now inside `apply_seat_orders`, so no caller can forget it
    and no NEW caller can reintroduce it.

    The complement of sc_community_alone_makes_games_without_charging_anyone: that one proved the
    community switch alone still MAKES a game; this proves the money switch alone decides whether
    anyone PAYS for a seat in it — including a seat taken later, by someone else, through a
    different code path."""
    from community import games, repositories as repo
    repo.save_settings(s, club_id=fx.club_id,
                       fields={"community_enabled": True, "seat_rule_enforced": False})
    host, joiner = fx.members[0], fx.members[1]

    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=host, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         settlement_mode="at_court",
                         starts_at=utc_iso(at(fx, 18)), ends_at=utc_iso(at(fx, 19)),
                         play_format="singles", visibility="open", play_intent="social")
    check("the game is created", r.get("ok"), str(r))
    bid = str(r["booking"]["id"])
    check("no seat is billed at creation", _seat_rows(s, bid) == [], str(_seat_rows(s, bid)))

    j = games.join_game(s, club_id=fx.club_id, booking_id=bid, user_id=joiner)
    check("the join succeeds", j.get("ok"), str(j))

    # THE ASSERTION THE LIVE BUG WOULD HAVE FAILED.
    check("joining bills NOBODY while charging is off", _seat_rows(s, bid) == [],
          "seat orders raised: %s" % (_seat_rows(s, bid),))
    owed = s.execute(text('SELECT COALESCE(SUM(o.amount_minor),0) FROM diary.booking_party bp '
                          '  JOIN billing."order" o ON o.id = bp.order_id '
                          " WHERE bp.booking_id = :b"), {"b": bid}).scalar()
    check("…and no seat carries an order at all", int(owed or 0) == 0, "owed=%s" % owed)

    seats = _seats_of(s, bid)
    check("both seats are taken", len(seats) == 2, str(seats))
    # 'held' means awaiting payment. There is no payment to await, so a seat left held would show a
    # member a court that never looks confirmed.
    check("the joiner's seat is CONFIRMED, not left awaiting a payment that will never come",
          all(x["seat_status"] == "confirmed" for x in seats), str(seats))
    # `covered` means "a membership paid for this". These are free because charging is OFF — a
    # different reason, and recording the wrong one makes the audit trail lie.
    check("no seat claims to have been covered by a membership",
          all(x["covered"] in (None, False) for x in seats), str(seats))
    row = s.execute(text("SELECT seat_share_minor FROM diary.booking WHERE id = :b"),
                    {"b": bid}).mappings().first()
    check("and no quote is frozen — nothing was sold", row["seat_share_minor"] is None, str(dict(row)))


def sc_a_game_appears_once_however_often_you_save_your_profile(s, fx):
    """ONE PLAYER, ONE PROFILE — and therefore one row per game in the feed.

    Tomo, looking at the live feed: "when I click find a match it shows 5 versions, it repeats allot
    man." Five copies of every game, one per time he had saved his player profile.

    `upsert_player_profile` wrote `INSERT ... ON CONFLICT DO NOTHING` with NO conflict target, and
    `iam.player_profile` had no unique constraint on (club_id, user_id) — only plain indexes. A bare
    ON CONFLICT DO NOTHING looks idempotent, which is exactly why it survived review: it is the shape
    you write when you mean upsert. But the only unique thing on the table was the primary key, and
    that is a fresh gen_random_uuid() every time, so it could never conflict. Every save appended a
    row, and `list_open_games`'s LEFT JOIN onto player_profile then multiplied every game by the row
    count.

    Worth stating why no gate caught it: nothing here is an ERROR. The INSERT succeeds, the UPDATE
    (WHERE club_id AND user_id) faithfully updates every duplicate so they never disagree, the JOIN
    is valid SQL, and the harness only ever saved a profile ONCE per scenario — so the fan-out needed
    a second save to appear at all. It was visible only to somebody using the thing twice.

    This asserts the invariant on both sides: the row count after repeated saves, and the feed."""
    from community import games, matching, repositories as repo
    repo.save_settings(s, club_id=fx.club_id,
                       fields={"community_enabled": True, "seat_rule_enforced": False})
    host, other = fx.members[0], fx.members[1]

    # Save the host's profile several times over, exactly as a member fiddling with their level does.
    for lvl in (3.0, 4.0, 4.5):
        repo.upsert_player_profile(s, club_id=fx.club_id, user_id=host,
                                   fields={"level_num": lvl}, source="self")
    n = s.execute(text("SELECT count(*) FROM iam.player_profile "
                       " WHERE club_id = :c AND user_id = :u"),
                  {"c": str(fx.club_id), "u": str(host)}).scalar()
    check("three saves leave ONE profile row, not three", int(n) == 1, "rows=%s" % n)
    check("and it holds the LAST level saved",
          float(repo.player_profile(s, club_id=fx.club_id, user_id=host)["level_num"]) == 4.5)

    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=host, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         settlement_mode="at_court",
                         starts_at=utc_iso(at(fx, 16)), ends_at=utc_iso(at(fx, 17)),
                         play_format="singles", visibility="open", play_intent="social")
    check("the game is created", r.get("ok"), str(r))
    bid = str(r["booking"]["id"])

    feed = games.list_open_games(s, club_id=fx.club_id, user_id=other)
    hits = [g for g in feed if g["booking_id"] == bid]
    check("the game appears EXACTLY ONCE in the feed", len(hits) == 1,
          "%d copies: %s" % (len(hits), [g["booking_id"] for g in feed]))

    # The same fan-out hit "Players for you" and admin -> Players & levels, off the same join.
    seen = [p for p in matching.suggest_players(s, club_id=fx.club_id, user_id=other)
            if str(p.get("user_id")) == str(host)]
    check("and the host appears at most once in the player suggestions", len(seen) <= 1,
          str(seen))


def sc_community_alone_makes_games_without_charging_anyone(s, fx):
    """THE TWO SWITCHES DO TWO JOBS, and the whole design rests on being able to run the first without
    the second: give members Find a Game as a BENEFIT before changing what anyone pays.

    The first cut gated the seating write on seat_rule_enforced, so a club with only the community
    half switched on got no seats, no visibility and therefore NO GAMES AT ALL — the feed was empty by
    construction and there was no way to post one. A real booking exposed it on the first try: the
    member paid for a court exactly as before and nothing about it was a game.

    So: community ON alone must produce a REAL, findable game — and must not raise a single order."""
    print("\n# community ON + money OFF = a real, findable game that charges nobody")
    from community import games, repositories as repo
    repo.save_settings(s, club_id=fx.club_id,
                       fields={"community_enabled": True, "seat_rule_enforced": False})
    host, other = fx.members[0], fx.members[1]
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=host, role="member",
                         booking_type="court", resource_id=fx.courts[0],
                         settlement_mode="at_court",
                         starts_at=utc_iso(at(fx, 14)), ends_at=utc_iso(at(fx, 15)),
                         play_format="singles", visibility="open", play_intent="social")
    check("the booking succeeds", r.get("ok"), str(r))
    bid = r["booking"]["id"]

    row = s.execute(text("SELECT visibility, seats, play_intent, open_until FROM diary.booking "
                         " WHERE id = :b"), {"b": bid}).mappings().first()
    check("it IS a game — seated, published, with an intent",
          row["visibility"] == "open" and row["seats"] == 2 and row["play_intent"] == "social",
          str(dict(row)))
    check("…and it has a fill deadline", row["open_until"] is not None)
    check("the host has a seat", len(_seats_of(s, bid)) >= 1, str(_seats_of(s, bid)))

    check("it is FINDABLE by another member",
          any(g["booking_id"] == str(bid)
              for g in games.list_open_games(s, club_id=fx.club_id, user_id=other)))

    # …and NOBODY is billed a share. The court itself is charged exactly as it always was.
    check("NO seat order exists — nobody is charged a share", _seat_rows(s, bid) == [],
          str(_seat_rows(s, bid)))
    check("the booking is CONFIRMED, not held pending a payment nobody owes",
          s.execute(text("SELECT status FROM diary.booking WHERE id=:b"),
                    {"b": bid}).scalar() == "confirmed")

    # The sweep must not quietly bill the holder for the unfilled seat either.
    from community.crons import sweep_open_games
    s.execute(text("UPDATE diary.booking SET open_until = now() - interval '1 hour' WHERE id=:b"),
              {"b": bid})
    out = sweep_open_games(s, club_id=fx.club_id)
    check("the sweep collapses NOTHING while the money rule is off",
          int(out.get("collapsed") or 0) == 0, str(out))
    check("…so the holder is still charged nothing", _seat_rows(s, bid) == [], str(_seat_rows(s, bid)))


def sc_you_cannot_take_a_seat_in_two_places_at_once(s, fx):
    """ONE PERSON, ONE PLACE — the diary's oldest rule, applied to seats.

    A seat is a booking_party row, not a resource booking, so the GiST exclusion constraint has
    nothing to say about it. Joining therefore bypassed the commitment check entirely: a player could
    hold seats in two games at the same hour, and a COACH could take a social game at 13:00 while the
    club's diary had him teaching a lesson at 13:00. That is the exact hole `_coach_commitment_at` was
    written to close for bookings (sc_one_coach_one_place_at_a_time), reopened by a different door.

    Both are REFUSALS, not downgrades. Elsewhere this platform prefers "don't block, just don't cover"
    — a member's second concurrent court is PAYG rather than refused — but that is about what a thing
    COSTS. This is about being in two places at once, which no price makes possible."""
    print("\n# a player can't hold two seats at one time, and a coach can't join while teaching")
    from community import games, repositories as repo
    repo.save_settings(s, club_id=fx.club_id, fields={"community_enabled": True})
    host_a, host_b, player = fx.members[0], fx.members[1], fx.members[2]

    a = _open_game(s, fx, host_a, hour=10, court=0)["booking"]["id"]
    b = _open_game(s, fx, host_b, hour=10, court=1)["booking"]["id"]   # SAME hour, other court
    check("two games exist at the same time", a and b)

    check("the player joins the first", games.join_game(
        s, club_id=fx.club_id, booking_id=a, user_id=player).get("ok") is True)
    try:
        games.join_game(s, club_id=fx.club_id, booking_id=b, user_id=player)
        check("…and is REFUSED the overlapping one", False, "no GameError")
    except games.GameError as e:
        check("…and is REFUSED the overlapping one (ALREADY_PLAYING_THEN)",
              e.code == "ALREADY_PLAYING_THEN", e.code)

    # A COACH with a lesson on the books cannot take a seat at that hour either.
    lesson = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=fx.members[0], role="member",
                              booking_type="lesson", resource_id=fx.coach_res,
                              coach_user_id=fx.coach_uid, settlement_mode="at_court",
                              starts_at=utc_iso(at(fx, 12)), ends_at=utc_iso(at(fx, 13)))
    check("the coach has a lesson at 12:00", lesson.get("ok"), str(lesson))
    c = _open_game(s, fx, host_b, hour=12, court=1)["booking"]["id"]
    try:
        games.join_game(s, club_id=fx.club_id, booking_id=c, user_id=fx.coach_uid)
        check("the coach is REFUSED a game while teaching", False, "no GameError")
    except games.GameError as e:
        check("the coach is REFUSED a game while teaching (COACH_IS_WORKING)",
              e.code == "COACH_IS_WORKING", e.code)

    # …but he is perfectly welcome at an hour he is free.
    d = _open_game(s, fx, host_b, hour=16, court=1)["booking"]["id"]
    check("…and CAN join at an hour he isn't working", games.join_game(
        s, club_id=fx.club_id, booking_id=d, user_id=fx.coach_uid).get("ok") is True)


def _seats_of(s, booking_id):
    return [dict(r) for r in s.execute(
        text("SELECT id, user_id, seat_status, covered, share_minor, order_id "
             "  FROM diary.booking_party WHERE booking_id = :b"),
        {"b": booking_id}).mappings().all()]


SCENARIOS = [
    # THE SEAT RULE (community/) — the money core, pinned before create_booking learns about seats.
    sc_a_seat_share_is_a_fixed_fraction_of_the_court,
    sc_a_game_says_what_kind_of_tennis_it_is,
    sc_the_feed_defaults_to_games_around_my_level,
    sc_dark_means_dark_for_the_whole_lane,
    sc_community_alone_makes_games_without_charging_anyone,
    sc_a_game_appears_once_however_often_you_save_your_profile,
    sc_joining_a_game_bills_nobody_while_the_money_switch_is_off,
    sc_you_cannot_take_a_seat_in_two_places_at_once,
    sc_match_chat_is_private_to_the_players,
    sc_a_result_needs_someone_else_to_confirm_it,
    sc_the_result_screen_offers_only_what_the_server_allows,
    sc_the_community_emails_say_the_thing_that_matters,
    sc_matching_puts_the_right_level_first,
    sc_the_sweep_reminds_then_releases_an_unpaid_seat,
    sc_the_seat_rule_can_be_switched_on_without_sql,
    sc_a_crafted_game_cannot_cheapen_or_outlive_its_own_bill,
    sc_invited_friend_is_trialed_once_only,
    sc_joining_an_open_game_bills_the_new_seat,
    sc_leaving_a_game_frees_the_seat_and_the_debt,
    sc_open_game_sweep_collapses_and_is_idempotent,
    sc_community_reads_never_leak_contact_details,
    sc_seat_rule_bills_through_create_booking,
    sc_a_payg_booker_pays_the_whole_court_not_a_share,
    sc_a_guest_is_free_once_the_court_itself_is_paid_for,
    sc_a_private_court_never_lapses_because_a_guest_did_not_pay,
    sc_seat_rule_holds_the_court_until_every_seat_settles,
    sc_cancelling_a_game_voids_every_seat_debt,
    sc_an_expired_membership_is_an_uncovered_seat,
    sc_member_plus_guest_bills_the_guest_in_full,
    sc_two_payg_split_and_both_must_settle,
    sc_open_seat_collapses_onto_the_holder_at_cutoff,
    sc_the_quoted_share_is_frozen_for_the_life_of_the_game,
    sc_seat_rule_off_changes_nothing,
    sc_cancel_after_start_guard,
    sc_unpriced_booking_refused,
    sc_court_book_cancel,
    sc_court_reschedule,
    sc_reschedule_court_move,
    sc_expired_hold_voids_order,
    sc_booking_type_must_match_resource,
    sc_posted_service_must_be_real,
    sc_gated_lesson_bills_the_booked_service,
    sc_member_cannot_bypass_online_only,
    sc_expired_void_is_recoverable,
    sc_court_move_guards,
    sc_coach_preferred_court,
    sc_lesson_two_rows,
    sc_lesson_list_collapse,
    sc_lesson_needs_court,
    sc_coach_class_conflict,
    sc_slot_granularity,
    sc_class_waitlist,
    sc_class_session_lifecycle,
    sc_class_price_survives_rename,
    sc_class_list_shows_renamed_service,
    sc_class_name_cannot_break_the_class,
    sc_class_retired_price_never_free,
    sc_class_roster_shows_payment,
    sc_class_checkin_settles_debt,
    sc_class_promotion_never_free,
    sc_class_late_payment_reinstates,
    sc_class_online_hold_expiry,
    sc_offpeak_slot_pricing,
    sc_peak_court_pricing,
    sc_membership_entitlement,
    sc_configurable_trial,
    sc_equipment_hire,
    sc_court_service_allocation,
    sc_class_courts,
    sc_backcapture_past_lesson,
    sc_semi_private_perhead,
    sc_semi_private_add_later,
    sc_semi_private_dependents,
    sc_semi_private_addable_guard,
    sc_card_only_service_gate,
    sc_class_payment_gate,
    # Revenue-leak hardening (2026-07-27) — see each docstring for the leak it closes.
    sc_membership_cannot_book_past_its_own_expiry,
    sc_one_coach_one_place_at_a_time,
    sc_member_second_concurrent_court_is_payg,
    sc_equipment_follows_its_own_payment_rule,
    sc_club_default_caps_cover_every_membership,
    sc_waitlist_promotion_into_a_cardonly_class_is_held,
    sc_one_lesson_flow,
    sc_paying_is_the_acceptance,
    sc_peak_hours_can_differ_per_court,
    sc_peak_can_have_more_than_one_window,
    sc_a_tier_can_be_free_except_at_peak,
    sc_peak_survives_a_reschedule,
    sc_trial_obeys_the_same_court_rules_as_a_membership,
    sc_equipment_court_is_charged_and_both_are_booked_out,
    sc_equipment_is_scoped_to_its_court_service,
]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows console is cp1252 by default
    except Exception:
        pass
    # The CRM event feed writes core.usage_event in its OWN transaction, which can't see our
    # uncommitted scratch club (FK). We test booking INTEGRITY here, not the event feed (it has
    # its own tests), so stub emit to a no-op for the run. bookings/classes both call the module
    # attribute diary.events.emit, so this one patch covers both lanes.
    import diary.events
    diary.events.emit = lambda *a, **k: False
    engine = get_engine()
    s = Session(engine)
    try:
        fx = setup(s)
        print(f"Scratch club {fx.club_id} · test day {fx.target} (weekday {fx.target.weekday()})")
        for scenario in SCENARIOS:
            # Each scenario runs in a SAVEPOINT so a fixture left behind by one (e.g. an
            # uncancelled booking) can't bleed into the next — we roll the savepoint back after.
            sp = s.begin_nested()
            try:
                scenario(s, fx)
            except Exception as e:  # a crash in one scenario shouldn't abort the rest
                check(f"{scenario.__name__} raised", False, repr(e))
            finally:
                if sp.is_active:
                    sp.rollback()
    finally:
        s.rollback()   # never persist the scratch club
        s.close()

    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"\n{'='*60}\n{passed}/{total} checks passed")
    fails = [(n, d) for n, ok, d in _RESULTS if not ok]
    if fails:
        print("FAILURES:")
        for n, d in fails:
            print(f"  - {n}  {d}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

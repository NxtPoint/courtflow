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


def sc_lesson_lifecycle(s, fx):
    print("\n# Lesson approval lifecycle (coach review ON): request → accept / decline / propose")
    s.execute(text("UPDATE iam.coach_profile SET review_bookings = true "
                   "WHERE club_id=:c AND user_id=:u"),
              {"c": fx.club_id, "u": fx.coach_uid})
    m = fx.members[0]
    start, end = at(fx, 9), at(fx, 10)
    # Client self-books → 'requested', reserves NOTHING (coach still free, no court row).
    r = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                         booking_type="lesson", resource_id=fx.coach_res,
                         coach_user_id=fx.coach_uid,
                         starts_at=utc_iso(start), ends_at=utc_iso(end))
    req_id = r["booking"]["id"]
    check("gated self-book → requested", r["booking"]["status"] == "requested", str(r.get("booking")))
    check("requested lesson reserves no court (coach slot still free)",
          has_slot(lesson_slots(s, fx), start))
    # Coach accepts → court assigned, confirmed.
    acc = B.accept_booking(s, club_id=fx.club_id, booking_id=req_id,
                           actor_user_id=fx.coach_uid, role="coach")
    check("coach accept → confirmed", acc.get("ok") and acc["booking"]["status"] == "confirmed",
          str(acc))
    check("coach slot gone after accept", not has_slot(lesson_slots(s, fx), start))
    B.cancel_booking(s, club_id=fx.club_id, booking_id=req_id, actor_user_id=m, role="member")

    # A second request the coach DECLINES → cancelled, nothing reserved.
    r2 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                          booking_type="lesson", resource_id=fx.coach_res,
                          coach_user_id=fx.coach_uid,
                          starts_at=utc_iso(at(fx, 11)), ends_at=utc_iso(at(fx, 12)))
    dec = B.decline_booking(s, club_id=fx.club_id, booking_id=r2["booking"]["id"],
                            actor_user_id=fx.coach_uid, role="coach", reason="busy")
    check("coach decline → cancelled", dec["booking"]["status"] == "cancelled", str(dec))

    # A third request the coach PROPOSES a new time → proposed; client accepts → confirmed.
    r3 = B.create_booking(s, club_id=fx.club_id, booked_by_user_id=m, role="member",
                          booking_type="lesson", resource_id=fx.coach_res,
                          coach_user_id=fx.coach_uid,
                          starts_at=utc_iso(at(fx, 13)), ends_at=utc_iso(at(fx, 14)))
    prop = B.propose_time(s, club_id=fx.club_id, booking_id=r3["booking"]["id"],
                          actor_user_id=fx.coach_uid, role="coach",
                          starts_at=utc_iso(at(fx, 15)), ends_at=utc_iso(at(fx, 16)))
    check("coach propose → proposed", prop["booking"]["status"] == "proposed", str(prop))
    acc3 = B.accept_booking(s, club_id=fx.club_id, booking_id=r3["booking"]["id"],
                            actor_user_id=m, role="member")
    check("client accept proposed → confirmed",
          acc3.get("ok") and acc3["booking"]["status"] == "confirmed", str(acc3))
    s.execute(text("UPDATE iam.coach_profile SET review_bookings = false "
                   "WHERE club_id=:c AND user_id=:u"),
              {"c": fx.club_id, "u": fx.coach_uid})


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
        # and again at the end so the savepoint-rolled-back policy doesn't leak "peak on" into later scenarios.
        try:
            delattr(s, "_cf_peak_window")
        except Exception:
            pass

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


SCENARIOS = [
    sc_cancel_after_start_guard,
    sc_unpriced_booking_refused,
    sc_court_book_cancel,
    sc_court_reschedule,
    sc_lesson_two_rows,
    sc_lesson_list_collapse,
    sc_lesson_needs_court,
    sc_coach_class_conflict,
    sc_slot_granularity,
    sc_class_waitlist,
    sc_class_online_hold_expiry,
    sc_lesson_lifecycle,
    sc_offpeak_slot_pricing,
    sc_peak_court_pricing,
    sc_membership_entitlement,
    sc_configurable_trial,
    sc_court_service_allocation,
    sc_class_courts,
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

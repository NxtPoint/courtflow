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


SCENARIOS = [
    sc_court_book_cancel,
    sc_court_reschedule,
    sc_lesson_two_rows,
    sc_lesson_list_collapse,
    sc_lesson_needs_court,
    sc_coach_class_conflict,
    sc_class_waitlist,
    sc_lesson_lifecycle,
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

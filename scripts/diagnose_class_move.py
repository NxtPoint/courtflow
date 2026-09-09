# scripts/diagnose_class_move.py — why can't THIS class move to THAT time, week by week?
#
# WHY. `reschedule_series` refuses an occurrence it cannot legally move and reports a count and a
# reason code — "1 could not move (court or coach busy)". That is honest and completely unusable: it
# names neither the date nor the thing in the way, so the operator's only recourse is to open the
# diary and hunt. Worse, a run that refuses SEVERAL weeks reads as "the move did nothing from the
# 8th onwards", which is what was reported and is a very different complaint from the truth.
#
# So this answers the actual question, per occurrence, WITHOUT MOVING ANYTHING: for each upcoming
# session of a class, would it move to the proposed time, and if not, WHAT is in the way — which
# class, whose lesson, which court, at what time. It calls the SAME guards the mover calls
# (`_coach_busy_at`, and a court-by-court availability check mirroring `_reserve_courts_for_class`),
# so a green line here means the move will actually take. A second implementation of the guards
# would only tell you what a different piece of code thinks.
#
# READ-ONLY. Every statement is a SELECT and nothing is reserved, moved or written.
#
# RUN IT (per docs/specs/DATA-ACCESS.md): Render -> courtflow-api -> Shell:
#     python -m scripts.diagnose_class_move "cardio"                # what is scheduled now
#     python -m scripts.diagnose_class_move "cardio" --to 17:15     # ...and what blocks that time

import argparse
import io
import os
import sys
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


def _load_env():
    envf = os.path.join(REPO, ".env.local")
    if os.path.isfile(envf) and not os.getenv("DATABASE_URL"):
        for line in io.open(envf, encoding="utf-8"):
            line = line.strip()
            if line.startswith("DATABASE_URL=") and not line.startswith("#"):
                os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not os.getenv("DATABASE_URL"):
        print("DATABASE_URL is not set. On Render it already is; locally use .env.local.")
        sys.exit(2)


def _find_class(s, needle):
    from sqlalchemy import text
    return s.execute(text(
        "SELECT r.id, r.club_id, r.name, r.coach_user_id, "
        "       COALESCE(cp.display_name, NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.surname)), ''), "
        "                u.email) AS coach_name "
        "FROM diary.resource r "
        "LEFT JOIN iam.user u ON u.id = r.coach_user_id "
        "LEFT JOIN iam.coach_profile cp ON cp.user_id = r.coach_user_id AND cp.club_id = r.club_id "
        "WHERE r.kind = 'class' AND lower(r.name) LIKE :n ORDER BY r.name"),
        {"n": "%%%s%%" % needle.strip().lower()}).mappings().fetchall()


def _sessions(s, club_id, resource_id):
    from sqlalchemy import text
    return s.execute(text(
        "SELECT cs.id, cs.starts_at, cs.ends_at, cs.status, "
        "       (cs.starts_at AT TIME ZONE 'Africa/Johannesburg') AS local_start, "
        "       (SELECT array_agg(csc.court_resource_id::text) FROM diary.class_session_court csc "
        "         WHERE csc.class_session_id = cs.id) AS courts, "
        "       (SELECT count(*) FROM diary.enrolment e "
        "         WHERE e.class_session_id = cs.id AND e.status = 'enrolled') AS enrolled "
        "FROM diary.class_session cs "
        "WHERE cs.club_id = :c AND cs.resource_id = :r AND cs.starts_at > now() "
        "ORDER BY cs.starts_at"), {"c": club_id, "r": resource_id}).mappings().fetchall()


def _what_blocks_coach(s, club_id, coach_user_id, starts, ends, exclude_session_id):
    """NAME the thing in the way, not just that there is one. `_coach_busy_at` returns a boolean,
    which is the right shape for a guard and the wrong shape for a person trying to fix it."""
    from sqlalchemy import text
    if not coach_user_id:
        return None
    cl = s.execute(text(
        "SELECT r.name, (cs.starts_at AT TIME ZONE 'Africa/Johannesburg') AS st "
        "FROM diary.class_session cs JOIN diary.resource r ON r.id = cs.resource_id "
        "WHERE cs.club_id = :c AND cs.coach_user_id = :u AND cs.status = 'scheduled' "
        "  AND cs.ends_at > :s AND cs.starts_at < :e AND cs.id <> :x LIMIT 1"),
        {"c": club_id, "u": coach_user_id, "s": starts, "e": ends,
         "x": exclude_session_id}).mappings().first()
    if cl:
        return "their own class '%s' at %s" % (cl["name"], str(cl["st"])[11:16])
    bk = s.execute(text(
        "SELECT b.booking_type, (b.starts_at AT TIME ZONE 'Africa/Johannesburg') AS st "
        "FROM diary.booking b "
        "WHERE b.club_id = :c AND b.status IN ('held','confirmed') "
        "  AND b.ends_at > :s AND b.starts_at < :e "
        "  AND ((b.booking_type = 'lesson' AND b.coach_user_id = CAST(:u AS uuid)) "
        "    OR (b.booking_type = 'court'  AND b.booked_by_user_id = CAST(:u AS uuid))) LIMIT 1"),
        {"c": club_id, "u": str(coach_user_id), "s": starts, "e": ends}).mappings().first()
    if bk:
        return "their own %s at %s" % (bk["booking_type"], str(bk["st"])[11:16])
    return None


def _free_courts(s, club_id, starts, ends, exclude_booking_ids):
    """How many courts are free at the target — the same question _reserve_courts_for_class asks.
    The session's OWN holds are excluded: they are released before the re-take, so a class must
    never be reported as blocked by itself."""
    from sqlalchemy import text
    return s.execute(text(
        "SELECT count(*) FROM diary.resource r "
        "WHERE r.club_id = :c AND r.kind = 'court' AND COALESCE(r.is_active, true) "
        "  AND NOT EXISTS (SELECT 1 FROM diary.booking b "
        "                   WHERE b.resource_id = r.id AND b.status IN ('held','confirmed') "
        "                     AND b.ends_at > :s AND b.starts_at < :e "
        "                     AND NOT (b.id = ANY(CAST(:excl AS uuid[]))))"),
        {"c": club_id, "s": starts, "e": ends,
         "excl": list(exclude_booking_ids or [])}).scalar() or 0


def _own_holds(s, session_id):
    from sqlalchemy import text
    ids = s.execute(text("SELECT court_booking_id FROM diary.class_session_court "
                         "WHERE class_session_id = :cs AND court_booking_id IS NOT NULL"),
                    {"cs": session_id}).scalars().all()
    return [str(x) for x in ids]


def main():
    ap = argparse.ArgumentParser(
        description="Why can't this class move to that time, week by week? (read-only)")
    ap.add_argument("needle", help="part of the class name")
    ap.add_argument("--to", help="proposed new start time, HH:MM in club time")
    args = ap.parse_args()

    _load_env()
    import db
    from sqlalchemy import text

    print("\nClass move - what would block it            (READ-ONLY)")
    print("=" * 78)
    with db.session_scope() as s:
        rows = _find_class(s, args.needle)
        if not rows:
            print("   No class matches %r." % args.needle)
            return 2
        if len(rows) > 1:
            print("   %d classes match - narrow it:" % len(rows))
            for r in rows:
                print("     %s  (coach %s)" % (r["name"], r["coach_name"] or "-"))
            return 2
        cls = rows[0]
        print("   %s   coach: %s" % (cls["name"], cls["coach_name"] or "(none)"))

        sess = _sessions(s, cls["club_id"], cls["id"])
        if not sess:
            print("\n   No upcoming sessions.")
            return 0

        new_t = None
        if args.to:
            try:
                hh, mm = args.to.strip().split(":")
                new_t = (int(hh), int(mm))
            except Exception:
                print("   --to must look like 17:15")
                return 2

        print("\n   %-18s%-9s%-9s%s"
              % ("when (SAST)", "status", "seats", "would it move?" if new_t else "courts held"))
        print("   " + "-" * 74)
        blocked = 0
        for r in sess:
            local = r["local_start"]
            when = str(local)[:16]
            if not new_t:
                print("   %-18s%-9s%-9s%s"
                      % (when, r["status"], r["enrolled"], len(r["courts"] or [])))
                continue
            mins = int(round((r["ends_at"] - r["starts_at"]).total_seconds() / 60.0))
            tzinfo = r["starts_at"].tzinfo
            # Build the target in CLUB time, exactly as reschedule_series does.
            import zoneinfo
            jhb = zoneinfo.ZoneInfo("Africa/Johannesburg")
            tgt = datetime(local.year, local.month, local.day, new_t[0], new_t[1], tzinfo=jhb)
            tgt_end = tgt + (r["ends_at"] - r["starts_at"])
            if r["status"] != "scheduled":
                print("   %-18s%-9s%-9s%s" % (when, r["status"], r["enrolled"], "skipped (not scheduled)"))
                continue
            if tgt == r["starts_at"]:
                print("   %-18s%-9s%-9s%s" % (when, r["status"], r["enrolled"], "already at that time"))
                continue
            why = _what_blocks_coach(s, cls["club_id"], cls["coach_user_id"], tgt, tgt_end, r["id"])
            if why:
                blocked += 1
                print("   %-18s%-9s%-9sNO - coach busy: %s" % (when, r["status"], r["enrolled"], why))
                continue
            want = len(r["courts"] or [])
            if want:
                free = _free_courts(s, cls["club_id"], tgt, tgt_end, _own_holds(s, r["id"]))
                if free < 1:
                    blocked += 1
                    print("   %-18s%-9s%-9sNO - no court free at %s"
                          % (when, r["status"], r["enrolled"], args.to))
                    continue
                if free < want:
                    print("   %-18s%-9s%-9syes, but only %d of %d courts (it will hold fewer)"
                          % (when, r["status"], r["enrolled"], free, want))
                    continue
            print("   %-18s%-9s%-9syes" % (when, r["status"], r["enrolled"], ))

        if new_t:
            print("\n   %d of %d upcoming sessions cannot move to %s."
                  % (blocked, len(sess), args.to))
            if blocked:
                print("   Each 'NO' names what is in the way. Clear that, or move those weeks to a")
                print("   different time by hand - the series move skips them and carries on, so the")
                print("   rest of the term is already correct.")

    print("\n" + "=" * 78)
    print("Nothing was written.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

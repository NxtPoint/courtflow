#!/usr/bin/env python
"""READ-ONLY: how many HOURS did a coach spend on court in a month?

Answers "how many hours did Allon and Colbert put in for September?" straight off the diary - the
time a coach was actually booked to teach, not the money it raised (that is
`diagnose_coach_statement`).

  LESSONS  every lesson slot the coach held (confirmed / completed). Counted by DISTINCT TIME SLOT,
           not by booking row: a semi-private lesson has one row per player on the same slot, and
           counting rows would turn one hour on court into two or three.
  CLASSES  every class session the coach ran that was not cancelled, counted once however many
           players were enrolled. Sessions with NOBODY enrolled are shown separately - the diary
           cannot say whether an empty class actually ran.

The month is the club's LOCAL month (`club.club.timezone`), so a lesson at 06:00 on the 1st is not
pushed into the previous month by UTC.

Nothing is written. Safe to run against production.

    python -m scripts.coach_hours --coach Allon,Colbert --month 2026-09
    python -m scripts.coach_hours --month 2026-09                  # every coach
    python -m scripts.coach_hours --coach Allon --month 2026-09 --detail
"""
import sys

from sqlalchemy import text

from db import session_scope


def _arg(argv, flag, default=None):
    return argv[argv.index(flag) + 1] if flag in argv and len(argv) > argv.index(flag) + 1 else default


def _h(minutes):
    return f"{(minutes or 0) / 60:,.2f} h"


def _coaches(s, who):
    # Resolve the coach FIRST and take THEIR club (DATA-ACCESS.md) - never "the first club".
    return s.execute(
        text("""
            SELECT DISTINCT cp.club_id, cp.user_id,
                   COALESCE(cp.display_name,
                            NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.surname)), ''),
                            u.email) AS name,
                   cl.name AS club_name, COALESCE(cl.timezone, 'UTC') AS tz
            FROM iam.coach_profile cp
            JOIN iam."user" u ON u.id = cp.user_id
            JOIN club.club cl ON cl.id = cp.club_id
            -- CAST is load-bearing: a bare `:who IS NULL` raises psycopg AmbiguousParameter.
            WHERE (CAST(:who AS text) IS NULL
                   OR COALESCE(cp.display_name,'') ILIKE '%' || CAST(:who AS text) || '%'
                   OR COALESCE(u.first_name,'')    ILIKE '%' || CAST(:who AS text) || '%'
                   OR COALESCE(u.surname,'')       ILIKE '%' || CAST(:who AS text) || '%'
                   OR COALESCE(u.email,'')         ILIKE '%' || CAST(:who AS text) || '%')
            ORDER BY name
        """),
        {"who": who},
    ).mappings().all()


def _slots(s, c, ym):
    p = {"club": c["club_id"], "coach": str(c["user_id"]), "ym": ym, "tz": c["tz"]}
    lessons = s.execute(
        text("""
            SELECT b.starts_at AT TIME ZONE :tz AS starts_local,
                   EXTRACT(EPOCH FROM (b.ends_at - b.starts_at)) / 60 AS minutes,
                   COUNT(*) AS players,
                   MIN(COALESCE(pr.name, 'Lesson')) AS service
            FROM diary.booking b
            LEFT JOIN billing.product pr ON pr.id = b.product_id
            WHERE b.club_id = :club AND b.coach_user_id = CAST(:coach AS uuid)
              AND b.booking_type = 'lesson'
              AND b.status IN ('confirmed', 'completed')
              AND to_char(b.starts_at AT TIME ZONE :tz, 'YYYY-MM') = :ym
            GROUP BY b.starts_at, b.ends_at
            ORDER BY b.starts_at
        """), p).mappings().all()
    classes = s.execute(
        text("""
            SELECT cls.starts_at AT TIME ZONE :tz AS starts_local,
                   EXTRACT(EPOCH FROM (cls.ends_at - cls.starts_at)) / 60 AS minutes,
                   (SELECT COUNT(*) FROM diary.enrolment e
                     WHERE e.class_session_id = cls.id
                       AND e.status IN ('enrolled', 'attended')) AS players,
                   COALESCE(r.name, 'Class') AS service
            FROM diary.class_session cls
            LEFT JOIN diary.resource r ON r.id = cls.resource_id
            WHERE cls.club_id = :club AND cls.coach_user_id = CAST(:coach AS uuid)
              AND cls.status <> 'cancelled'
              AND to_char(cls.starts_at AT TIME ZONE :tz, 'YYYY-MM') = :ym
            ORDER BY cls.starts_at
        """), p).mappings().all()
    left_out = s.execute(
        text("""
            SELECT b.status, COUNT(DISTINCT (b.starts_at, b.ends_at)) AS n
            FROM diary.booking b
            WHERE b.club_id = :club AND b.coach_user_id = CAST(:coach AS uuid)
              AND b.booking_type = 'lesson'
              AND b.status NOT IN ('confirmed', 'completed')
              AND to_char(b.starts_at AT TIME ZONE :tz, 'YYYY-MM') = :ym
            GROUP BY 1 ORDER BY 1
        """), p).mappings().all()
    return lessons, classes, left_out


def _by_service(rows):
    out = {}
    for r in rows:
        k = out.setdefault(r["service"], {"n": 0, "min": 0})
        k["n"] += 1
        k["min"] += int(r["minutes"] or 0)
    return out


def main(argv):
    who = _arg(argv, "--coach")
    detail = "--detail" in argv
    needles = [w.strip() for w in who.split(",") if w.strip()] if who else [None]

    with session_scope() as s:
        ym = _arg(argv, "--month") or s.execute(text("SELECT to_char(now(),'YYYY-MM')")).scalar()
        print(f"COACH HOURS ON COURT - {ym}   (read-only)\n")

        seen, summary = set(), []
        for needle in needles:
            coaches = _coaches(s, needle)
            if not coaches:
                print(f"  No coach matched {needle!r}.\n")
                continue
            for c in coaches:
                key = (str(c["club_id"]), str(c["user_id"]))
                if key in seen:
                    continue
                seen.add(key)
                lessons, classes, left_out = _slots(s, c, ym)
                ran = [r for r in classes if int(r["players"] or 0) > 0]
                empty = [r for r in classes if int(r["players"] or 0) == 0]
                l_min = sum(int(r["minutes"] or 0) for r in lessons)
                c_min = sum(int(r["minutes"] or 0) for r in ran)
                e_min = sum(int(r["minutes"] or 0) for r in empty)

                print("=" * 70)
                print(f"{c['name']}   ({c['club_name']}, {c['tz']})")
                print("=" * 70)
                print(f"  LESSONS  {len(lessons):>4} slots   {_h(l_min):>10}")
                for svc, v in sorted(_by_service(lessons).items()):
                    print(f"     {svc[:34]:<34} {v['n']:>4} x  {_h(v['min']):>10}")
                print(f"  CLASSES  {len(ran):>4} sessions {_h(c_min):>9}")
                for svc, v in sorted(_by_service(ran).items()):
                    print(f"     {svc[:34]:<34} {v['n']:>4} x  {_h(v['min']):>10}")
                print(f"  {'TOTAL':<22} {_h(l_min + c_min):>10}")
                if empty:
                    print(f"  NOT counted: {len(empty)} class session(s) with nobody enrolled"
                          f" ({_h(e_min)}) - add them if they ran.")
                for r in left_out:
                    print(f"  NOT counted: {r['n']} lesson slot(s) with status '{r['status']}'.")
                if detail:
                    print("\n  EVERY SLOT")
                    rows = [("lesson", r) for r in lessons] + [("class", r) for r in classes]
                    for kind, r in sorted(rows, key=lambda x: x[1]["starts_local"]):
                        print(f"    {r['starts_local']:%Y-%m-%d %a %H:%M}  {kind:<6}"
                              f" {int(r['minutes'] or 0):>4} min  players {int(r['players'] or 0)}"
                              f"  {(r['service'] or '')[:30]}")
                print()
                summary.append((c["name"], l_min, c_min))

        if len(summary) > 1:
            print("-" * 70)
            print(f"  {'SUMMARY ' + ym:<26} {'lessons':>11} {'classes':>11} {'total':>11}")
            for name, l_min, c_min in summary:
                print(f"  {name[:26]:<26} {_h(l_min):>11} {_h(c_min):>11} {_h(l_min + c_min):>11}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:                                          # noqa: BLE001
        print(f"FAILED: {e}")
        sys.exit(1)

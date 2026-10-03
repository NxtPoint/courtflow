# admin/timesheet.py — THE COURT AUDIT: what was BOOKED versus what was SEEN on court.
#
# A coach who teaches a lesson without entering it costs the club twice: the client is never
# billed through the platform (so no commission), and the court was used for nothing. The diary
# cannot catch that by itself — an unbooked lesson is exactly the thing it has no row for. So a
# reviewer (the manager, working from court footage) records what actually happened:
#
#   * a BOOKED session he saw            -> verdict 'verified'
#   * a BOOKED session that did not run  -> verdict 'not_seen'
#   * a court in use with NO booking     -> an 'unbooked' entry (coach, time, court)
#
# and the month-end RECON puts the two side by side per coach. An unbooked entry can then be
# charged the club's admin penalty — by an ADMIN only, never by the reviewer, so a capture mistake
# cannot fine a coach by itself. The penalty is a `coach_ledger` ADJUSTMENT dated on the day of the
# session, which is how it reaches the coach's statement with no second money store.
#
# ACCESS is its own short list (`diary.timesheet_auditor`), not a role: the reviewer is an ordinary
# coach account and must not gain the rest of the admin console to do this one job.
#
# Repos never commit — callers compose via db.session_scope().

import logging

from sqlalchemy import text

log = logging.getLogger("admin.timesheet")

DEFAULT_PENALTY_MINOR = 50000          # R500 — the club's admin penalty for an unbooked court
_ADMIN_ROLES = ("club_admin", "platform_admin")

DDL = [
    """
    CREATE TABLE IF NOT EXISTS diary.timesheet_auditor (
        club_id    uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        user_id    uuid NOT NULL REFERENCES iam.user(id) ON DELETE CASCADE,
        granted_by uuid,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (club_id, user_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS diary.timesheet_entry (
        id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id           uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        coach_user_id     uuid NOT NULL REFERENCES iam.user(id),
        verdict           text NOT NULL CHECK (verdict IN ('verified','not_seen','unbooked')),
        starts_at         timestamptz NOT NULL,
        ends_at           timestamptz NOT NULL,
        booking_id        uuid,
        class_session_id  uuid,
        court_resource_id uuid,
        note              text,
        captured_by       uuid,
        created_at        timestamptz NOT NULL DEFAULT now(),
        penalty_minor     integer,
        penalty_at        timestamptz,
        penalty_by        uuid
    );
    """,
    # ONE verdict per booked session — re-marking it replaces the verdict, never stacks a second.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_timesheet_booking ON diary.timesheet_entry (booking_id) "
    "WHERE booking_id IS NOT NULL;",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_timesheet_class ON diary.timesheet_entry (class_session_id) "
    "WHERE class_session_id IS NOT NULL;",
    "CREATE INDEX IF NOT EXISTS ix_timesheet_coach ON diary.timesheet_entry (club_id, coach_user_id, starts_at);",
]

_NAME = ("COALESCE(NULLIF(cp.display_name, ''), "
         "NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.surname)), ''), u.email)")


def _tz(session, club_id):
    return session.execute(text("SELECT COALESCE(timezone, 'UTC') FROM club.club WHERE id = :c"),
                           {"c": str(club_id)}).scalar() or "UTC"


def can_audit(session, *, club_id, user_id, role):
    """Admins always; anyone else only if an admin put them on the auditor list."""
    if role in _ADMIN_ROLES:
        return True
    return bool(session.execute(
        text("SELECT 1 FROM diary.timesheet_auditor WHERE club_id = :c AND user_id = :u"),
        {"c": str(club_id), "u": str(user_id)}).first())


def auditors(session, *, club_id):
    rows = session.execute(
        text("SELECT a.user_id, NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.surname)), '') AS name, "
             '       u.email FROM diary.timesheet_auditor a JOIN iam."user" u ON u.id = a.user_id '
             "WHERE a.club_id = :c ORDER BY name"), {"c": str(club_id)}).mappings().all()
    return [{"user_id": str(r["user_id"]), "name": r["name"] or r["email"] or "Member"} for r in rows]


def set_auditor(session, *, club_id, user_id, allowed, granted_by=None):
    """Grant or revoke capture access. The user must belong to THIS club."""
    if not session.execute(text("SELECT 1 FROM iam.membership WHERE club_id = :c AND user_id = :u"),
                           {"c": str(club_id), "u": str(user_id)}).first():
        return {"ok": False, "error": "NOT_A_MEMBER"}
    if allowed:
        session.execute(
            text("INSERT INTO diary.timesheet_auditor (club_id, user_id, granted_by) "
                 "VALUES (:c, :u, :g) ON CONFLICT (club_id, user_id) DO NOTHING"),
            {"c": str(club_id), "u": str(user_id), "g": str(granted_by) if granted_by else None})
    else:
        session.execute(text("DELETE FROM diary.timesheet_auditor WHERE club_id = :c AND user_id = :u"),
                        {"c": str(club_id), "u": str(user_id)})
    return {"ok": True, "auditors": auditors(session, club_id=club_id)}


def coaches(session, *, club_id):
    rows = session.execute(
        text(f'SELECT cp.user_id, {_NAME} AS name FROM iam.coach_profile cp '
             'JOIN iam."user" u ON u.id = cp.user_id WHERE cp.club_id = :c ORDER BY name'),
        {"c": str(club_id)}).mappings().all()
    return [{"coach_user_id": str(r["user_id"]), "name": r["name"] or "Coach"} for r in rows]


# Every session the diary holds for a coach, ONE ROW PER TIME SLOT. A semi-private lesson has a row
# per player on the same slot — counting rows would turn one hour on court into two or three.
_BOOKED_SQL = """
    SELECT 'lesson' AS kind, MIN(b.id::text) AS ref_id, b.coach_user_id, b.starts_at, b.ends_at,
           string_agg(DISTINCT COALESCE(NULLIF(TRIM(CONCAT_WS(' ', cu.first_name, cu.surname)), ''),
                                        cu.email, 'Client'), ', ') AS label,
           (SELECT r.name FROM diary.booking cb JOIN diary.resource r ON r.id = cb.resource_id
             WHERE cb.order_id = MIN(b.order_id::text)::uuid AND cb.booking_type = 'court'
             LIMIT 1) AS court
    FROM diary.booking b
    LEFT JOIN iam."user" cu ON cu.id = b.booked_by_user_id
    WHERE b.club_id = :c AND b.booking_type = 'lesson' AND b.coach_user_id IS NOT NULL
      AND b.status IN ('confirmed', 'completed')
      AND b.starts_at >= :s AND b.starts_at < :e
      AND (CAST(:coach AS uuid) IS NULL OR b.coach_user_id = CAST(:coach AS uuid))
    GROUP BY b.coach_user_id, b.starts_at, b.ends_at
    UNION ALL
    SELECT 'class' AS kind, cls.id::text AS ref_id, cls.coach_user_id, cls.starts_at, cls.ends_at,
           COALESCE(cr.name, 'Class') || ' · ' ||
             (SELECT COUNT(*) FROM diary.enrolment en WHERE en.class_session_id = cls.id
                AND en.status IN ('enrolled', 'attended')) || ' enrolled' AS label,
           (SELECT r.name FROM diary.resource r WHERE r.id = cls.court_resource_id) AS court
    FROM diary.class_session cls
    LEFT JOIN diary.resource cr ON cr.id = cls.resource_id
    WHERE cls.club_id = :c AND cls.coach_user_id IS NOT NULL AND cls.status <> 'cancelled'
      AND cls.starts_at >= :s AND cls.starts_at < :e
      AND (CAST(:coach AS uuid) IS NULL OR cls.coach_user_id = CAST(:coach AS uuid))
"""


def _bounds(session, club_id, *, date=None, month=None):
    """[start, end) as timestamptz for a LOCAL day or month in the club's timezone."""
    tz = _tz(session, club_id)
    if date:
        q = ("SELECT (CAST(:d AS date)::timestamp AT TIME ZONE :tz), "
             "((CAST(:d AS date) + 1)::timestamp AT TIME ZONE :tz)")
        row = session.execute(text(q), {"d": date, "tz": tz}).first()
    else:
        q = ("SELECT (to_date(:m, 'YYYY-MM')::timestamp AT TIME ZONE :tz), "
             "((to_date(:m, 'YYYY-MM') + interval '1 month')::timestamp AT TIME ZONE :tz)")
        row = session.execute(text(q), {"m": month, "tz": tz}).first()
    return row[0], row[1], tz


def _booked(session, club_id, start, end, coach_user_id=None):
    return session.execute(
        text(_BOOKED_SQL), {"c": str(club_id), "s": start, "e": end,
                            "coach": str(coach_user_id) if coach_user_id else None}).mappings().all()


def _entries(session, club_id, start, end, coach_user_id=None):
    return session.execute(
        text("SELECT t.*, r.name AS court, "
             "       NULLIF(TRIM(CONCAT_WS(' ', cb.first_name, cb.surname)), '') AS captured_by_name "
             "FROM diary.timesheet_entry t "
             "LEFT JOIN diary.resource r ON r.id = t.court_resource_id "
             'LEFT JOIN iam."user" cb ON cb.id = t.captured_by '
             "WHERE t.club_id = :c AND t.starts_at >= :s AND t.starts_at < :e "
             "  AND (CAST(:coach AS uuid) IS NULL OR t.coach_user_id = CAST(:coach AS uuid)) "
             "ORDER BY t.starts_at"),
        {"c": str(club_id), "s": start, "e": end,
         "coach": str(coach_user_id) if coach_user_id else None}).mappings().all()


def _minutes(a, b):
    return int((b - a).total_seconds() // 60)


def _unbooked_dict(t):
    return {"id": str(t["id"]), "coach_user_id": str(t["coach_user_id"]),
            "starts_at": t["starts_at"].isoformat(), "ends_at": t["ends_at"].isoformat(),
            "minutes": _minutes(t["starts_at"], t["ends_at"]), "court": t["court"],
            "note": t["note"], "captured_by": t["captured_by_name"],
            "penalty_minor": t["penalty_minor"],
            "penalty_at": t["penalty_at"].isoformat() if t["penalty_at"] else None}


def day(session, *, club_id, coach_user_id, date):
    """One coach, one day: every booked session with the reviewer's verdict (or none yet), plus the
    court use he recorded that had no booking."""
    start, end, _ = _bounds(session, club_id, date=date)
    ents = _entries(session, club_id, start, end, coach_user_id)
    by_ref = {}
    for t in ents:
        ref = t["booking_id"] or t["class_session_id"]
        if ref:
            by_ref[str(ref)] = t["verdict"]
    sessions = [{"kind": b["kind"], "ref_id": b["ref_id"],
                 "starts_at": b["starts_at"].isoformat(), "ends_at": b["ends_at"].isoformat(),
                 "minutes": _minutes(b["starts_at"], b["ends_at"]), "label": b["label"],
                 "court": b["court"], "verdict": by_ref.get(str(b["ref_id"]))}
                for b in sorted(_booked(session, club_id, start, end, coach_user_id),
                                key=lambda x: x["starts_at"])]
    return {"date": date, "coach_user_id": str(coach_user_id), "sessions": sessions,
            "unbooked": [_unbooked_dict(t) for t in ents if t["verdict"] == "unbooked"]}


def set_verdict(session, *, club_id, kind, ref_id, verdict, captured_by=None):
    """Mark a BOOKED session 'verified' / 'not_seen', or clear the mark (verdict=None). The session
    is re-read from the diary, scoped by club, so a posted id can only ever mark a real session of
    this club — and the coach + times on the entry are the diary's, never the caller's."""
    if kind == "lesson":
        row = session.execute(
            text("SELECT coach_user_id, starts_at, ends_at FROM diary.booking "
                 "WHERE id = :r AND club_id = :c AND booking_type = 'lesson'"),
            {"r": str(ref_id), "c": str(club_id)}).mappings().first()
        col = "booking_id"
    elif kind == "class":
        row = session.execute(
            text("SELECT coach_user_id, starts_at, ends_at FROM diary.class_session "
                 "WHERE id = :r AND club_id = :c"), {"r": str(ref_id), "c": str(club_id)}).mappings().first()
        col = "class_session_id"
    else:
        return {"ok": False, "error": "BAD_KIND"}
    if not row or not row["coach_user_id"]:
        return {"ok": False, "error": "NOT_FOUND"}
    session.execute(text(f"DELETE FROM diary.timesheet_entry WHERE club_id = :c AND {col} = :r"),
                    {"c": str(club_id), "r": str(ref_id)})
    if verdict in ("verified", "not_seen"):
        session.execute(
            text(f"INSERT INTO diary.timesheet_entry (club_id, coach_user_id, verdict, starts_at, "
                 f"ends_at, {col}, captured_by) VALUES (:c, :u, :v, :s, :e, :r, :by)"),
            {"c": str(club_id), "u": str(row["coach_user_id"]), "v": verdict, "s": row["starts_at"],
             "e": row["ends_at"], "r": str(ref_id), "by": str(captured_by) if captured_by else None})
    elif verdict is not None:
        return {"ok": False, "error": "BAD_VERDICT"}
    return {"ok": True, "verdict": verdict}


def add_unbooked(session, *, club_id, coach_user_id, date, start_time, duration_minutes,
                 court_resource_id=None, note=None, captured_by=None):
    """Record a court a coach used with NO booking. Refused when a booked session of his already
    covers that time — that is a session to VERIFY, not a missing one, and recording it here would
    put a penalty in front of the admin for a lesson that was properly entered."""
    try:
        mins = int(duration_minutes)
    except (TypeError, ValueError):
        return {"ok": False, "error": "BAD_DURATION"}
    if not (15 <= mins <= 480):
        return {"ok": False, "error": "BAD_DURATION"}
    if not session.execute(text("SELECT 1 FROM iam.coach_profile WHERE club_id = :c AND user_id = :u"),
                           {"c": str(club_id), "u": str(coach_user_id)}).first():
        return {"ok": False, "error": "NOT_A_COACH"}
    tz = _tz(session, club_id)
    try:
        start = session.execute(
            text("SELECT (CAST(:d AS date) + CAST(:t AS time)) AT TIME ZONE :tz"),
            {"d": date, "t": start_time, "tz": tz}).scalar()
    except Exception:
        return {"ok": False, "error": "BAD_TIME"}
    end = session.execute(text("SELECT CAST(:s AS timestamptz) + make_interval(mins => :m)"),
                          {"s": start, "m": mins}).scalar()
    if end > session.execute(text("SELECT now()")).scalar():
        return {"ok": False, "error": "IN_THE_FUTURE",
                "message": "Record court use after it has happened."}
    clash = [b for b in _booked(session, club_id, start, end, coach_user_id)
             if b["starts_at"] < end and b["ends_at"] > start]
    # (_booked bounds on starts_at >= start, so also look for one that began earlier and runs in.)
    earlier = session.execute(
        text("SELECT 1 FROM diary.booking WHERE club_id = :c AND coach_user_id = CAST(:u AS uuid) "
             "AND booking_type = 'lesson' AND status IN ('confirmed','completed') "
             "AND starts_at < :e AND ends_at > :s "
             "UNION ALL SELECT 1 FROM diary.class_session WHERE club_id = :c "
             "AND coach_user_id = CAST(:u AS uuid) AND status <> 'cancelled' "
             "AND starts_at < :e AND ends_at > :s LIMIT 1"),
        {"c": str(club_id), "u": str(coach_user_id), "s": start, "e": end}).first()
    if clash or earlier:
        return {"ok": False, "error": "ALREADY_BOOKED",
                "message": "This coach has a booked session at that time — mark it as seen instead."}
    if court_resource_id and not session.execute(
            text("SELECT 1 FROM diary.resource WHERE id = :r AND club_id = :c AND kind = 'court'"),
            {"r": str(court_resource_id), "c": str(club_id)}).first():
        return {"ok": False, "error": "BAD_COURT"}
    new_id = session.execute(
        text("INSERT INTO diary.timesheet_entry (club_id, coach_user_id, verdict, starts_at, ends_at, "
             "court_resource_id, note, captured_by) "
             "VALUES (:c, :u, 'unbooked', :s, :e, :r, :n, :by) RETURNING id"),
        {"c": str(club_id), "u": str(coach_user_id), "s": start, "e": end,
         "r": str(court_resource_id) if court_resource_id else None,
         "n": (note or "").strip() or None,
         "by": str(captured_by) if captured_by else None}).scalar_one()
    return {"ok": True, "id": str(new_id)}


def remove_unbooked(session, *, club_id, entry_id):
    """Delete an unbooked entry captured in error — but never one already charged: the penalty is
    on the coach's ledger and must be reversed there first, not orphaned."""
    row = session.execute(
        text("SELECT penalty_minor FROM diary.timesheet_entry "
             "WHERE id = :i AND club_id = :c AND verdict = 'unbooked' FOR UPDATE"),
        {"i": str(entry_id), "c": str(club_id)}).mappings().first()
    if not row:
        return {"ok": False, "error": "NOT_FOUND"}
    if row["penalty_minor"]:
        return {"ok": False, "error": "PENALTY_CHARGED",
                "message": "A penalty was already charged for this — it can't be removed."}
    session.execute(text("DELETE FROM diary.timesheet_entry WHERE id = :i AND club_id = :c"),
                    {"i": str(entry_id), "c": str(club_id)})
    return {"ok": True}


def charge_penalty(session, *, club_id, entry_id, amount_minor=None, charged_by=None):
    """Charge the club's admin penalty for an unbooked court — ONCE. It is a coach_ledger ADJUSTMENT
    (a negative one: the coach owes it), dated on the day of the session so it lands on the statement
    of the month it happened in. The row lock + the penalty_minor check make a double-click a no-op."""
    row = session.execute(
        text("SELECT t.coach_user_id, t.starts_at, t.penalty_minor, r.name AS court "
             "FROM diary.timesheet_entry t LEFT JOIN diary.resource r ON r.id = t.court_resource_id "
             "WHERE t.id = :i AND t.club_id = :c AND t.verdict = 'unbooked' FOR UPDATE OF t"),
        {"i": str(entry_id), "c": str(club_id)}).mappings().first()
    if not row:
        return {"ok": False, "error": "NOT_FOUND"}
    if row["penalty_minor"]:
        return {"ok": True, "already": True, "penalty_minor": int(row["penalty_minor"])}
    amount = int(amount_minor) if amount_minor else DEFAULT_PENALTY_MINOR
    if amount <= 0:
        return {"ok": False, "error": "BAD_AMOUNT"}
    tz = _tz(session, club_id)
    when = session.execute(text("SELECT to_char(CAST(:s AS timestamptz) AT TIME ZONE :tz, "
                                "'DD Mon HH24:MI')"), {"s": row["starts_at"], "tz": tz}).scalar()
    cur = session.execute(text("SELECT COALESCE(currency_code, 'ZAR') FROM club.club WHERE id = :c"),
                          {"c": str(club_id)}).scalar() or "ZAR"
    session.execute(
        text("INSERT INTO billing.coach_ledger (club_id, coach_user_id, entry_type, amount_minor, "
             "currency, ref_type, ref_id, note, occurred_at) "
             "VALUES (:c, :u, 'adjustment', :amt, :cur, 'timesheet', :ref, :note, :at)"),
        {"c": str(club_id), "u": str(row["coach_user_id"]), "amt": -amount, "cur": cur,
         "ref": str(entry_id), "at": row["starts_at"],
         "note": f"Admin penalty — court used without a booking, {when}"
                 + (f" ({row['court']})" if row["court"] else "")})
    session.execute(
        text("UPDATE diary.timesheet_entry SET penalty_minor = :a, penalty_at = now(), penalty_by = :by "
             "WHERE id = :i"),
        {"a": amount, "by": str(charged_by) if charged_by else None, "i": str(entry_id)})
    return {"ok": True, "penalty_minor": amount}


def recon(session, *, club_id, month):
    """The month, coach by coach: what the diary says was booked, what the reviewer made of it, and
    the court use that was never booked at all."""
    start, end, _ = _bounds(session, club_id, month=month)
    names = {c["coach_user_id"]: c["name"] for c in coaches(session, club_id=club_id)}
    per = {}

    def slot(uid):
        uid = str(uid)
        return per.setdefault(uid, {
            "coach_user_id": uid, "name": names.get(uid, "Coach"),
            "booked": 0, "booked_minutes": 0, "verified": 0, "not_seen": 0, "unreviewed": 0,
            "unbooked": 0, "unbooked_minutes": 0, "penalties_minor": 0, "penalties_pending": 0})

    ents = _entries(session, club_id, start, end)
    verdicts = {str(t["booking_id"] or t["class_session_id"]): t["verdict"]
                for t in ents if (t["booking_id"] or t["class_session_id"])}
    for b in _booked(session, club_id, start, end):
        s = slot(b["coach_user_id"])
        s["booked"] += 1
        s["booked_minutes"] += _minutes(b["starts_at"], b["ends_at"])
        v = verdicts.get(str(b["ref_id"]))
        s["verified" if v == "verified" else ("not_seen" if v == "not_seen" else "unreviewed")] += 1
    unbooked = []
    for t in ents:
        if t["verdict"] != "unbooked":
            continue
        s = slot(t["coach_user_id"])
        s["unbooked"] += 1
        s["unbooked_minutes"] += _minutes(t["starts_at"], t["ends_at"])
        if t["penalty_minor"]:
            s["penalties_minor"] += int(t["penalty_minor"])
        else:
            s["penalties_pending"] += 1
        d = _unbooked_dict(t)
        d["coach_name"] = names.get(str(t["coach_user_id"]), "Coach")
        unbooked.append(d)
    return {"month": month, "default_penalty_minor": DEFAULT_PENALTY_MINOR,
            "coaches": sorted(per.values(), key=lambda x: x["name"] or ""),
            "unbooked": unbooked}

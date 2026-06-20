# diary/crons.py — cron LOGIC (docs/03 §7). The thin trigger (crons/trigger.py) POSTs to
# the endpoints in diary/routes.py; those endpoints call these functions. No business
# logic lives in the trigger — it owns nothing but the HTTP call (1050 pattern).
#
# Jobs owned by this lane:
#   reminders         (hourly)        — bookings/sessions at T-24h and T-2h without a
#                                        reminder sent -> emit booking_reminder.
#   capacity-sweep    (every few min) — release expired held bookings; promote court
#                                        waitlists; flag past confirmed lessons for attendance.
#   membership-refill (per period)    — roll membership periods / mark lapsed (best-effort;
#                                        billing.* owns the heavy lifting — guarded).
#
# Reminders dedupe via diary.reminder_log (created here, idempotently, so we don't double-
# send). Everything is club-agnostic at the cron layer (sweeps all clubs) but every row
# carries club_id so downstream emits stay tenant-scoped.

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from diary import events

log = logging.getLogger("diary.crons")

# Reminder offsets (label -> hours-before-start) and the tolerance window per run.
_REMINDER_OFFSETS = {"T-24h": 24, "T-2h": 2}
_WINDOW_MINUTES = 70  # an hourly cron with slack covers each offset exactly once


def _ensure_reminder_log(conn):
    """Idempotent helper table so reminders aren't sent twice. Created lazily here (this
    lane owns it; it's not in the core schema list because it's a pure operational log)."""
    # subject_kind is 'booking' | 'class_session'.
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS diary.reminder_log ("
        " id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
        " club_id uuid NOT NULL,"
        " subject_kind text NOT NULL,"
        " subject_id uuid NOT NULL,"
        " offset_label text NOT NULL,"
        " sent_at timestamptz NOT NULL DEFAULT now(),"
        " UNIQUE (subject_kind, subject_id, offset_label))"
    ))


def run_reminders(engine, *, now=None):
    """Find bookings/sessions starting in each reminder window without a reminder already
    logged -> emit booking_reminder + log it. Returns a summary dict."""
    now = now or datetime.now(timezone.utc)
    sent = 0
    with engine.begin() as conn:
        _ensure_reminder_log(conn)
        for label, hours in _REMINDER_OFFSETS.items():
            lo = now + timedelta(hours=hours) - timedelta(minutes=_WINDOW_MINUTES)
            hi = now + timedelta(hours=hours)
            # Bookings.
            rows = conn.execute(text(
                "SELECT b.id, b.club_id, b.booking_type, b.starts_at, b.ends_at, "
                "       b.booked_by_user_id, r.name AS resource_name "
                "FROM diary.booking b LEFT JOIN diary.resource r ON r.id = b.resource_id "
                "WHERE b.status='confirmed' AND b.starts_at > :lo AND b.starts_at <= :hi "
                "  AND NOT EXISTS (SELECT 1 FROM diary.reminder_log l "
                "       WHERE l.subject_kind='booking' AND l.subject_id=b.id "
                "         AND l.offset_label=:lbl)"),
                {"lo": lo, "hi": hi, "lbl": label},
            ).mappings().all()
            for b in rows:
                if _log_reminder(conn, b["club_id"], "booking", b["id"], label):
                    events.emit("booking_reminder", {
                        "club_id": str(b["club_id"]),
                        "user_id": str(b["booked_by_user_id"]) if b["booked_by_user_id"] else None,
                        "booking_id": str(b["id"]), "booking_type": b["booking_type"],
                        "resource_name": b["resource_name"],
                        "starts_at": b["starts_at"].isoformat(), "offset": label,
                    })
                    sent += 1
            # Class sessions -> remind each enrolled player.
            srows = conn.execute(text(
                "SELECT cs.id, cs.club_id, cs.starts_at, r.name AS class_name "
                "FROM diary.class_session cs LEFT JOIN diary.resource r ON r.id=cs.resource_id "
                "WHERE cs.status='scheduled' AND cs.starts_at > :lo AND cs.starts_at <= :hi "
                "  AND NOT EXISTS (SELECT 1 FROM diary.reminder_log l "
                "       WHERE l.subject_kind='class_session' AND l.subject_id=cs.id "
                "         AND l.offset_label=:lbl)"),
                {"lo": lo, "hi": hi, "lbl": label},
            ).mappings().all()
            for cs in srows:
                if not _log_reminder(conn, cs["club_id"], "class_session", cs["id"], label):
                    continue
                enrolled = conn.execute(text(
                    "SELECT user_id FROM diary.enrolment "
                    "WHERE class_session_id=:cs AND status='enrolled'"),
                    {"cs": cs["id"]},
                ).mappings().all()
                for e in enrolled:
                    events.emit("booking_reminder", {
                        "club_id": str(cs["club_id"]),
                        "user_id": str(e["user_id"]) if e["user_id"] else None,
                        "class_session_id": str(cs["id"]), "booking_type": "class",
                        "resource_name": cs["class_name"],
                        "starts_at": cs["starts_at"].isoformat(), "offset": label,
                    })
                    sent += 1
    return {"job": "reminders", "emitted": sent}


def _log_reminder(conn, club_id, kind, subject_id, label):
    """Insert the reminder-log row; returns True if it was new (we should send)."""
    res = conn.execute(text(
        "INSERT INTO diary.reminder_log (club_id, subject_kind, subject_id, offset_label) "
        "VALUES (:c, :k, :s, :l) "
        "ON CONFLICT (subject_kind, subject_id, offset_label) DO NOTHING RETURNING id"),
        {"c": club_id, "k": kind, "s": subject_id, "l": label},
    ).first()
    return res is not None


def run_capacity_sweep(engine, *, now=None):
    """Release expired held bookings; promote court waitlists for the freed slots; flag
    past confirmed lessons needing attendance (we leave them 'confirmed' but they surface
    in the coach's attendance view via a query — no status change needed here)."""
    now = now or datetime.now(timezone.utc)
    released = 0
    promoted = 0
    with engine.begin() as conn:
        # Expired holds -> cancelled (frees the slot via the exclusion-constraint WHERE).
        rows = conn.execute(text(
            "UPDATE diary.booking SET status='cancelled', cancelled_at=now(), "
            "       cancellation_reason='hold expired', held_until=NULL, updated_at=now() "
            "WHERE status='held' AND held_until IS NOT NULL AND held_until < :now "
            "RETURNING id, club_id, resource_id, starts_at"),
            {"now": now},
        ).mappings().all()
        released = len(rows)
        for r in rows:
            wl = conn.execute(text(
                "SELECT id, user_id FROM diary.waitlist "
                "WHERE club_id=:c AND resource_id=:rid AND notified_at IS NULL "
                "  AND (desired_start IS NULL OR desired_start=:ds) "
                "ORDER BY created_at LIMIT 1"),
                {"c": r["club_id"], "rid": r["resource_id"], "ds": r["starts_at"]},
            ).mappings().first()
            if wl:
                conn.execute(text("UPDATE diary.waitlist SET notified_at=now() WHERE id=:id"),
                             {"id": wl["id"]})
                events.emit("waitlist_slot_open", {
                    "club_id": str(r["club_id"]),
                    "user_id": str(wl["user_id"]) if wl["user_id"] else None,
                    "resource_id": str(r["resource_id"]),
                    "desired_start": r["starts_at"].isoformat() if r["starts_at"] else None,
                })
                promoted += 1
    return {"job": "capacity-sweep", "holds_released": released, "waitlists_notified": promoted}


def run_membership_refill(engine, *, now=None):
    """Best-effort membership period roll / lapse marking. billing.* owns the canonical
    membership lifecycle (Agent C); this is a guarded sweep so the cron route is live now.
    No-op (logged) if billing.membership_subscription isn't present yet."""
    now = now or datetime.now(timezone.utc)
    with engine.begin() as conn:
        exists = conn.execute(text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='billing' AND table_name='membership_subscription'"
        )).first()
        if not exists:
            log.info("membership-refill: billing.membership_subscription absent — no-op")
            return {"job": "membership-refill", "lapsed": 0, "note": "billing absent"}
        rows = conn.execute(text(
            "UPDATE billing.membership_subscription SET status='expired', updated_at=now() "
            "WHERE status='active' AND current_period_end IS NOT NULL "
            "  AND current_period_end < :today "
            "RETURNING id, club_id, user_id"),
            {"today": now.date()},
        ).mappings().all()
        for r in rows:
            events.emit("membership_lapsed", {
                "club_id": str(r["club_id"]),
                "user_id": str(r["user_id"]) if r["user_id"] else None,
            })
    return {"job": "membership-refill", "lapsed": len(rows)}

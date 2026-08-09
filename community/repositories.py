# community/repositories.py — the lane's reads + small writes.
#
# Display reads are _guard-wrapped (analytics/insights discipline): a missing profile row or an
# absent column must render an empty panel, never a 500 in the middle of a member's home page.
#
# NOTE the split of responsibility with community/seats.py, which does the OPPOSITE and raises: a
# dashboard that degrades is correct, a MONEY read that degrades is a silent zero. Nothing in this
# file touches money.

import logging

from sqlalchemy import text

log = logging.getLogger("community.repositories")


def _guard(fn, default):
    try:
        return fn()
    except Exception:
        log.debug("community read suppressed", exc_info=False)
        return default


def player_profile(session, *, club_id, user_id):
    """The caller's own player profile. Returns the shape the editor expects even when no row exists
    yet, so the UI has nothing to special-case on first visit."""
    empty = {"level_num": None, "level_source": None, "prefers_format": None,
             "prefers_play": None, "prefers_times": [], "photo_url": None,
             "visible_in_community": False, "has_profile": False}

    def _read():
        row = session.execute(
            text("SELECT level_num, level_source, prefers_format, prefers_play, prefers_times, "
                 "       photo_url, visible_in_community "
                 "FROM iam.player_profile WHERE club_id = :c AND user_id = :u"),
            {"c": str(club_id), "u": str(user_id)},
        ).mappings().first()
        if not row:
            return empty
        return {"level_num": float(row["level_num"]) if row["level_num"] is not None else None,
                "level_source": row["level_source"],
                "prefers_format": row["prefers_format"],
                "prefers_play": row["prefers_play"],
                "prefers_times": list(row["prefers_times"] or []),
                "photo_url": row["photo_url"],
                "visible_in_community": bool(row["visible_in_community"]),
                "has_profile": True}

    return _guard(_read, empty)


_ALLOWED = {"level_num", "prefers_format", "prefers_play", "prefers_times", "photo_url",
            "visible_in_community"}


def upsert_player_profile(session, *, club_id, user_id, fields, source="self",
                          set_by_user_id=None):
    """Create-or-update the caller's profile. NOT guarded — a save that silently does nothing is
    worse than an error the member can see and retry.

    `source` records HOW the level was arrived at (self / onboarding / coach / calculated). It exists
    so a coach can tell an assessed level from a self-declared one: 'how would you rate yourself?' is
    how everybody becomes advanced, and that is the failure mode that kills the matching."""
    fields = {k: v for k, v in (fields or {}).items() if k in _ALLOWED}
    session.execute(
        text("INSERT INTO iam.player_profile (club_id, user_id) VALUES (:c, :u) "
             "ON CONFLICT DO NOTHING"),
        {"c": str(club_id), "u": str(user_id)})
    if fields:
        sets, params = [], {"c": str(club_id), "u": str(user_id)}
        for k, v in fields.items():
            sets.append(f"{k} = :{k}")
            params[k] = v
        if "level_num" in fields:
            sets.append("level_source = :src")
            params["src"] = source
            sets.append("level_set_by_user_id = :sb")
            params["sb"] = str(set_by_user_id) if set_by_user_id else None
        sets.append("updated_at = now()")
        session.execute(
            text(f"UPDATE iam.player_profile SET {', '.join(sets)} "
                 " WHERE club_id = :c AND user_id = :u"),
            params)
    return player_profile(session, club_id=club_id, user_id=user_id)


def set_level_as_staff(session, *, club_id, user_id, level_num, set_by_user_id):
    """A coach/admin override. Recorded with source='coach' so it is visibly an assessment rather
    than a self-rating, and so a later automatic rating knows not to quietly overwrite a human."""
    return upsert_player_profile(session, club_id=club_id, user_id=user_id,
                                 fields={"level_num": level_num}, source="coach",
                                 set_by_user_id=set_by_user_id)


def my_games(session, *, club_id, user_id, limit=20):
    """Games the member is in — upcoming first. Used by the client Home card."""
    def _read():
        rows = session.execute(
            text("""
                SELECT b.id, b.starts_at, b.ends_at, b.play_format, b.status, b.visibility,
                       r.name AS court_name,
                       bp.seat_status, bp.share_minor, bp.covered,
                       o.status AS order_status
                  FROM diary.booking_party bp
                  JOIN diary.booking b ON b.id = bp.booking_id
                  LEFT JOIN diary.resource r ON r.id = b.resource_id
                  LEFT JOIN billing."order" o ON o.id = bp.order_id
                 WHERE bp.club_id = :c AND bp.user_id = :u
                   AND bp.seat_status IN ('held','confirmed','collapsed')
                   AND b.status IN ('held','confirmed')
                   AND b.starts_at > now()
                 ORDER BY b.starts_at LIMIT :n
            """),
            {"c": str(club_id), "u": str(user_id), "n": int(limit or 20)},
        ).mappings().all()
        return [{"booking_id": str(r["id"]),
                 "starts_at": r["starts_at"].isoformat() if r["starts_at"] else None,
                 "ends_at": r["ends_at"].isoformat() if r["ends_at"] else None,
                 "court_name": r["court_name"], "play_format": r["play_format"],
                 "status": r["status"], "visibility": r["visibility"],
                 "seat_status": r["seat_status"],
                 "covered": r["covered"],
                 "amount_minor": r["share_minor"],
                 "unpaid": bool(r["order_status"] and r["order_status"] != "paid")}
                for r in rows]

    return _guard(_read, [])

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
        # NAME THE CONFLICT TARGET. A bare ON CONFLICT DO NOTHING has nothing to conflict on but the
        # primary key — a fresh uuid every time — so it inserted a new row on every save and the feed
        # showed one copy of every game per row (ux_player_profile_club_user in iam/schema.py).
        text("INSERT INTO iam.player_profile (club_id, user_id) VALUES (:c, :u) "
             "ON CONFLICT (club_id, user_id) DO NOTHING"),
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


# ---------------------------------------------------------------------------
# ADMIN reads (club_admin / owner)
# ---------------------------------------------------------------------------

def settings(session, *, club_id):
    """The club's community configuration, for the Setup screen.

    Includes `courts_without_seats` — the courts whose SERVICE has no doubles seat count configured.
    That is the "code is inert until configured" trap this platform has fallen into before (the
    entitlement caps shipped and sat unused for weeks because nobody set them), so the setting screen
    has to SHOW the unconfigured state rather than let the owner assume it works."""
    from community import seats as _seats

    def _read():
        pol = _seats.policy(session, club_id)
        pol["seats_by_format"] = dict(_seats.SEATS_BY_FORMAT)
        # WHAT ONE PLAYER ACTUALLY PAYS, in rands, for each court duration the club sells. The share
        # is the single most consequential number on the settings screen, and "50%" is not an amount —
        # an owner should not have to do percentages in their head to find out they just set R110.
        try:
            rows = session.execute(
                text("SELECT DISTINCT p.duration_minutes, p.amount_minor "
                     "FROM billing.price p JOIN billing.product pr ON pr.id = p.product_id "
                     " WHERE pr.club_id = :c AND pr.kind = 'court_booking' AND p.active = true "
                     "   AND p.duration_minutes IS NOT NULL "
                     " ORDER BY p.duration_minutes"),
                {"c": str(club_id)},
            ).mappings().all()
            pol["share_examples"] = [
                {"duration_minutes": int(r["duration_minutes"]),
                 "court_minor": int(r["amount_minor"]),
                 "share_minor": _seats.share_minor(int(r["amount_minor"]),
                                                   pct=pol["seat_share_pct"],
                                                   rounding=pol["seat_rounding"])}
                for r in rows]
        except Exception:
            pol["share_examples"] = []
        pol["open_games"] = int(session.execute(
            text("SELECT count(*) FROM diary.booking WHERE club_id = :c AND visibility = 'open' "
                 "  AND status IN ('held','confirmed') AND starts_at > now()"),
            {"c": str(club_id)}).scalar() or 0)
        pol["live_invites"] = int(session.execute(
            text("SELECT count(*) FROM community.player_invite "
                 " WHERE club_id = :c AND status = 'sent'"),
            {"c": str(club_id)}).scalar() or 0)
        pol["discoverable_players"] = int(session.execute(
            text("SELECT count(*) FROM iam.player_profile "
                 " WHERE club_id = :c AND visible_in_community = true"),
            {"c": str(club_id)}).scalar() or 0)
        # Seats owed for money: an unpaid seat on a game that has not started is real receivable.
        pol["unpaid_seats_minor"] = int(session.execute(
            text('SELECT COALESCE(SUM(o.amount_minor), 0) FROM diary.booking_party bp '
                 '  JOIN diary.booking b ON b.id = bp.booking_id '
                 '  JOIN billing."order" o ON o.id = bp.order_id '
                 " WHERE bp.club_id = :c AND bp.seat_status IN ('invited','held','confirmed') "
                 "   AND o.status NOT IN ('paid','void','refunded') AND b.starts_at > now()"),
            {"c": str(club_id)}).scalar() or 0)
        return pol

    return _guard(_read, {"community_enabled": False, "seat_rule_enforced": False})


def save_settings(session, *, club_id, fields):
    """Write the switches. NOT guarded — a save that silently does nothing is the worst possible
    outcome on a screen whose whole job is turning a money rule on."""
    from community import seats as _seats

    allowed = {"community_enabled": bool, "seat_rule_enforced": bool,
               "open_game_cutoff_hours": int, "seat_pay_hours": int, "guest_trial_days": int,
               "seat_share_pct": int, "seat_rounding": str}
    sets, params = [], {"c": str(club_id)}
    for k, cast in allowed.items():
        if k not in (fields or {}):
            continue
        v = fields[k]
        if cast is int:
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
            # The share is a PERCENTAGE and clamps 0..100; the timings are hours/days and clamp 1..720.
            # One shared clamp would have let a 720% seat share through, which on a R210 court is
            # R1,512 a head.
            v = max(0, min(v, 100)) if k == "seat_share_pct" else max(1, min(v, 720))
        elif cast is str:
            if k == "seat_rounding" and v not in _seats._ROUNDERS:
                continue
        else:
            v = bool(v)
        sets.append(f"{k} = :{k}")
        params[k] = v
    if not sets:
        return settings(session, club_id=club_id)
    # INSERT-ONLY upsert, the same shape allow_online_payment uses: the boot re-seed must never be
    # able to reset a switch the owner deliberately flipped.
    session.execute(
        text("INSERT INTO club.policy (club_id) VALUES (:c) ON CONFLICT (club_id) DO NOTHING"),
        {"c": str(club_id)})
    session.execute(
        text(f"UPDATE club.policy SET {', '.join(sets)}, updated_at = now() WHERE club_id = :c"),
        params)
    return settings(session, club_id=club_id)


def admin_games(session, *, club_id, days=14, limit=100):
    """EVERY seated game, not just the ones with a seat free — the owner's view is different from a
    player's. Carries the seat counts and what is still owed, because the question an owner actually
    asks is "is anyone about to play on a court nobody paid for?"."""
    def _read():
        rows = session.execute(
            text("""
                SELECT b.id, b.starts_at, b.ends_at, b.play_format, b.play_intent, b.seats, b.visibility,
                       b.status, b.open_until, b.split_locked_at,
                       r.name AS court_name, u.first_name AS host_name, u.surname AS host_surname,
                       (SELECT count(*) FROM diary.booking_party bp
                         WHERE bp.booking_id = b.id
                           AND bp.seat_status IN ('invited','held','confirmed','collapsed')) AS taken,
                       (SELECT COALESCE(SUM(o.amount_minor), 0)
                          FROM diary.booking_party bp
                          JOIN billing."order" o ON o.id = bp.order_id
                         WHERE bp.booking_id = b.id
                           AND o.status NOT IN ('paid','void','refunded')) AS owed_minor
                  FROM diary.booking b
                  LEFT JOIN diary.resource r ON r.id = b.resource_id
                  LEFT JOIN iam."user" u ON u.id = b.booked_by_user_id
                 WHERE b.club_id = :c AND b.booking_type = 'court'
                   AND b.status IN ('held','confirmed')
                   AND b.starts_at > now() AND b.starts_at < now() + (:d || ' days')::interval
                   AND (b.visibility = 'open' OR b.seats IS NOT NULL)
                 ORDER BY b.starts_at LIMIT :n
            """),
            {"c": str(club_id), "d": str(int(days or 14)), "n": int(limit or 100)},
        ).mappings().all()
        out = []
        for r in rows:
            total = int(r["seats"] or 0)
            out.append({
                "booking_id": str(r["id"]),
                "starts_at": r["starts_at"].isoformat() if r["starts_at"] else None,
                "ends_at": r["ends_at"].isoformat() if r["ends_at"] else None,
                "court_name": r["court_name"],
                "play_format": r["play_format"], "play_intent": r["play_intent"],
                "visibility": r["visibility"],
                "status": r["status"],
                "seats_total": total,
                "seats_taken": int(r["taken"] or 0),
                "open_seats": max(0, total - int(r["taken"] or 0)),
                "owed_minor": int(r["owed_minor"] or 0),
                "locked": r["split_locked_at"] is not None,
                "open_until": r["open_until"].isoformat() if r["open_until"] else None,
                "host_name": " ".join(x for x in [r["host_name"], r["host_surname"]] if x) or "—",
            })
        return out

    return _guard(_read, [])


def admin_invites(session, *, club_id, limit=100):
    """The invite log. Shows whether the free week was actually granted — the answer to "my friend
    says they didn't get their free week", which is otherwise unanswerable without SQL."""
    def _read():
        rows = session.execute(
            text('SELECT i.id, i.email, i.status, i.created_at, i.expires_at, i.trial_granted_at, '
                 "       i.booking_id, u.first_name AS inviter_first, u.surname AS inviter_last "
                 "FROM community.player_invite i "
                 ' LEFT JOIN iam."user" u ON u.id = i.inviter_user_id '
                 " WHERE i.club_id = :c ORDER BY i.created_at DESC LIMIT :n"),
            {"c": str(club_id), "n": int(limit or 100)},
        ).mappings().all()
        return [{"id": str(r["id"]), "email": r["email"], "status": r["status"],
                 "invited_by": " ".join(x for x in [r["inviter_first"], r["inviter_last"]] if x) or "—",
                 "at": r["created_at"].isoformat() if r["created_at"] else None,
                 "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
                 "trial_granted": r["trial_granted_at"] is not None,
                 "booking_id": str(r["booking_id"]) if r["booking_id"] else None}
                for r in rows]

    return _guard(_read, [])


def admin_players(session, *, club_id, q=None, limit=50):
    """Members with a player profile, for the level-override screen. A five-question quiz is a
    starting point; a coach who has actually seen someone play is the correction."""
    def _read():
        params = {"c": str(club_id), "n": int(limit or 50)}
        where = "pp.club_id = :c"
        if q:
            where += (" AND (u.first_name ILIKE :q OR u.surname ILIKE :q "
                      "      OR u.email ILIKE :q)")
            params["q"] = f"%{q}%"
        rows = session.execute(
            text(f'SELECT pp.user_id, pp.level_num, pp.level_source, pp.visible_in_community, '
                 '       u.first_name, u.surname '
                 "FROM iam.player_profile pp "
                 ' JOIN iam."user" u ON u.id = pp.user_id '
                 f" WHERE {where} ORDER BY u.first_name, u.surname LIMIT :n"),
            params,
        ).mappings().all()
        return [{"user_id": str(r["user_id"]),
                 "name": " ".join(x for x in [r["first_name"], r["surname"]] if x) or "—",
                 "level": float(r["level_num"]) if r["level_num"] is not None else None,
                 "level_source": r["level_source"],
                 "visible": bool(r["visible_in_community"])} for r in rows]

    return _guard(_read, [])


def my_games(session, *, club_id, user_id, limit=20):
    """Games the member is in — upcoming first. Used by the client Home card."""
    def _read():
        rows = session.execute(
            text("""
                SELECT b.id, b.starts_at, b.ends_at, b.play_format, b.play_intent, b.status, b.visibility,
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
                 "play_intent": r["play_intent"],
                 "status": r["status"], "visibility": r["visibility"],
                 "seat_status": r["seat_status"],
                 "covered": r["covered"],
                 "amount_minor": r["share_minor"],
                 "unpaid": bool(r["order_status"] and r["order_status"] != "paid")}
                for r in rows]

    return _guard(_read, [])

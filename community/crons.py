# community/crons.py — the open-game sweep.
#
# THE ONE recurring job this lane needs, and the reason it needs one at all: everything else in the
# diary is released by lazy expiry (release_expired_holds runs whenever anyone looks at the diary), but
# a seat COLLAPSE moves money and sends email. That cannot wait for someone to happen to load a page,
# and it must not fire twice — so it is a real, idempotent, OPS_KEY-guarded job, fired by a GitHub
# Action exactly like reminders/month-end. NOT a render.yaml cron: all four of those stay commented
# out (CLAUDE.md).
#
# Everything here is idempotent by construction, because a doubled schedule or a manual re-run must be
# harmless:
#   · collapse_open_seats no-ops once the seat is collapsed (the 'collapsed' seat OCCUPIES its seat)
#   · the unpaid reminder is deduped by diary.reminder_log, the same table booking reminders use
#   · a released seat is only released once (the UPDATE is state-guarded)

import logging
from datetime import datetime, timezone

from sqlalchemy import text

from community import seats as _seats

log = logging.getLogger("community.crons")


def sweep_open_games(session, *, club_id, now=None, max_games=500):
    """Per club: remind, release, collapse. Returns a summary the route can log and the Action can
    assert on."""
    now = now or datetime.now(timezone.utc)
    pol = _seats.policy(session, club_id)
    if not pol["community_enabled"]:
        return {"ok": True, "skipped": "community_disabled"}

    out = {"ok": True, "reminded": 0, "released": 0, "collapsed": 0, "failed": 0}

    # 1) REMIND the seats that still owe. Before releasing anything — somebody who is about to lose
    # their seat should hear about it while they can still act.
    for row in session.execute(
        text("""
            SELECT bp.id AS party_id, bp.user_id, bp.booking_id, b.starts_at, b.held_until
              FROM diary.booking_party bp
              JOIN diary.booking b ON b.id = bp.booking_id
              JOIN billing."order" o ON o.id = bp.order_id
             WHERE bp.club_id = :c
               AND bp.seat_status IN ('invited','held')
               AND o.settlement_mode = 'online' AND o.status <> 'paid'
               AND b.status IN ('held','confirmed') AND b.starts_at > :now
             LIMIT :lim
        """),
        {"c": str(club_id), "now": now, "lim": int(max_games)},
    ).mappings().all():
        if _remind_once(session, club_id=club_id, booking_id=row["booking_id"],
                        user_id=row["user_id"], now=now):
            out["reminded"] += 1

    # 2) RELEASE seats whose pay-by window has passed. The seat goes back to open (and is then
    # collapsed by step 3 if nobody takes it) rather than sitting unpaid on a court all week.
    released = session.execute(
        text("""
            UPDATE diary.booking_party bp
               SET seat_status = 'released', order_id = NULL, share_minor = NULL
              FROM diary.booking b, billing."order" o
             WHERE bp.booking_id = b.id AND o.id = bp.order_id
               AND bp.club_id = :c
               AND bp.seat_status IN ('invited','held')
               AND bp.party_role <> 'host'
               AND o.settlement_mode = 'online' AND o.status <> 'paid'
               AND b.status IN ('held','confirmed')
               AND b.held_until IS NOT NULL AND b.held_until < :now
               AND b.starts_at > :now
         RETURNING bp.id, bp.booking_id, o.id AS order_id
        """),
        {"c": str(club_id), "now": now},
    ).mappings().all()
    for r in released:
        out["released"] += 1
        try:
            from billing.statement import void_order
            void_order(session, club_id=club_id, order_id=str(r["order_id"]),
                       reason="seat unpaid by the deadline")
        except Exception:
            log.debug("released-seat order void skipped", exc_info=False)

    # 3) COLLAPSE open seats at the cutoff — the rule that stops a free second seat being given away
    # to nobody. Each game in its OWN savepoint: one unpriced court must not abort the whole sweep for
    # the club (the same per-client-transactional discipline the month-end sweep learned the hard way).
    #
    # ONLY WHEN THE MONEY RULE IS ON. A collapse RAISES A CHARGE, so a club running the community half
    # alone — deliberately, while it tells its members what is coming — must never have a member
    # quietly billed for an unfilled seat by a background job. The seat simply stays open.
    if not pol["seat_rule_enforced"]:
        return out
    for row in session.execute(
        text("SELECT id FROM diary.booking "
             " WHERE club_id = :c AND visibility = 'open' AND booking_type = 'court' "
             "   AND status IN ('held','confirmed') "
             "   AND open_until IS NOT NULL AND open_until < :now AND starts_at > :now "
             " ORDER BY starts_at LIMIT :lim"),
        {"c": str(club_id), "now": now, "lim": int(max_games)},
    ).mappings().all():
        try:
            with session.begin_nested():
                res = _seats.collapse_open_seats(session, club_id=club_id,
                                                 booking_id=row["id"], now=now)
            if res.get("collapsed"):
                out["collapsed"] += int(res["collapsed"])
                _emit_collapsed(session, club_id=club_id, booking_id=row["id"],
                                amount_minor=res.get("amount_minor"))
        except Exception:
            out["failed"] += 1
            log.warning("open-game collapse failed booking=%s", row["id"], exc_info=False)

    return out


def _remind_once(session, *, club_id, booking_id, user_id, now):
    """Dedupe on diary.reminder_log — the same table the T-24h/T-2h booking reminders use, so one
    unpaid seat gets one nudge however often the sweep runs. Guarded: a reminder is a nicety, and it
    must never abort the release/collapse work that follows it."""
    if not user_id:
        return False
    try:
        # diary.reminder_log is created LAZILY by diary.crons._ensure_reminder_log (that lane owns it
        # and it is not in the boot schema list), so it may not exist yet on a club that has never run
        # a reminder. Its real shape is (club_id, subject_kind, subject_id, offset_label) with the
        # dedupe on the last three.
        session.execute(text(
            "CREATE TABLE IF NOT EXISTS diary.reminder_log ("
            " id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
            " club_id uuid NOT NULL,"
            " subject_kind text NOT NULL,"
            " subject_id uuid NOT NULL,"
            " offset_label text NOT NULL,"
            " sent_at timestamptz NOT NULL DEFAULT now(),"
            " UNIQUE (subject_kind, subject_id, offset_label))"))
        hit = session.execute(
            text("INSERT INTO diary.reminder_log (club_id, subject_kind, subject_id, offset_label) "
                 "VALUES (:c, 'booking', :b, :lbl) "
                 "ON CONFLICT (subject_kind, subject_id, offset_label) DO NOTHING RETURNING id"),
            {"c": str(club_id), "b": str(booking_id), "lbl": f"seat_unpaid:{user_id}"},
        ).first()
        if not hit:
            return False
        from marketing_crm.tracking import emit
        em = session.execute(text('SELECT email FROM iam."user" WHERE id = :u'),
                             {"u": str(user_id)}).scalar()
        emit("game_seat_unpaid_reminder",
             {"club_id": str(club_id), "email": em, "user_id": str(user_id),
              "ref_type": "booking", "ref_id": str(booking_id)})
        return True
    except Exception:
        log.debug("seat reminder skipped (benign)", exc_info=False)
        return False


def _emit_collapsed(session, *, club_id, booking_id, amount_minor):
    """TELL THEM. A charge that appears with no explanation is a support ticket and a trust problem —
    the member needs to know the seat they left open is the reason."""
    try:
        from marketing_crm.tracking import emit
        row = session.execute(
            text('SELECT b.booked_by_user_id, u.email, b.starts_at '
                 "FROM diary.booking b "
                 ' LEFT JOIN iam."user" u ON u.id = b.booked_by_user_id WHERE b.id = :b'),
            {"b": str(booking_id)},
        ).mappings().first()
        if not row:
            return
        emit("game_seat_collapsed",
             {"club_id": str(club_id), "email": row["email"],
              "user_id": str(row["booked_by_user_id"]) if row["booked_by_user_id"] else None,
              "ref_type": "booking", "ref_id": str(booking_id),
              "amount_minor": int(amount_minor or 0),
              "starts_at": row["starts_at"].isoformat() if row["starts_at"] else None})
    except Exception:
        log.debug("game_seat_collapsed emit skipped (benign)", exc_info=False)


def sweep_all_clubs(session, *, now=None, max_seconds=90):
    """Every club with the community switched on. TIME-BOXED and resumable in the same shape the
    month-end sweep uses — under gunicorn's 120s reaper, and the caller loops until complete."""
    import time
    started = time.monotonic()
    now = now or datetime.now(timezone.utc)
    clubs = [str(r[0]) for r in session.execute(
        text("SELECT club_id FROM club.policy WHERE community_enabled = true")).all()]
    done, totals = [], {"reminded": 0, "released": 0, "collapsed": 0, "failed": 0}
    for cid in clubs:
        if time.monotonic() - started > max_seconds:
            break
        res = sweep_open_games(session, club_id=cid, now=now)
        done.append(cid)
        for k in totals:
            totals[k] += int(res.get(k) or 0)
    return {"ok": True, "complete": len(done) == len(clubs), "clubs": len(done),
            "remaining": len(clubs) - len(done), **totals}

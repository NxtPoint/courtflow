# diary/entitlement.py — membership ENTITLEMENT resolver.
#
# The single source of truth for what an active MEMBER gets at a given time, read by BOTH
# diary.availability (to shape the shown options/prices) and diary.bookings.create_booking (to
# enforce) — so shown == charged == allowed. Guarded like diary/pricing.py: if billing is absent or
# anything is unexpected we treat the booking as UNCOVERED / unconstrained and NEVER block it.
#
# Entitlement = the existing membership coverage (active + inside the tier's access window, via
# diary.pricing.membership_covers) PLUS, silently:
#   - court-SERVICE eligibility: a court product flagged members_covered=false (e.g. a clay court sold
#     PAYG-only) is never free for a member.
#   - max_covered_minutes: a covered booking can't exceed the tier's cap (a longer one is PAYG; the
#     booking UI hides over-cap durations for members so it's never felt).
#   - max_covered_per_day / max_courts_per_day: once the member's daily covered bookings / distinct
#     covered courts hit the cap, further bookings that day are PAYG.
# Every cap DOWNGRADES to PAYG (never blocks) — the same behaviour off-peak already uses.

import logging
from datetime import timedelta, timezone

from sqlalchemy import text

from diary import pricing as _pricing

log = logging.getLogger("diary.entitlement")


def active_caps(session, *, club_id, user_id):
    """The member's covered-booking caps — the MOST GENEROUS across their active tiers (a member benefits
    from their best tier; a NULL/unconstrained tier wins). Returns
    {max_covered_minutes, max_covered_per_day, max_courts_per_day} where None = no cap. Guarded -> all None."""
    out = {"max_covered_minutes": None, "max_covered_per_day": None, "max_courts_per_day": None}
    try:
        if not user_id or not _pricing._membership_sub_exists(session):
            return out
        rows = session.execute(
            text("SELECT p.max_covered_minutes, p.max_covered_per_day, p.max_courts_per_day "
                 "FROM billing.membership_subscription ms "
                 "LEFT JOIN billing.price p ON p.id = ms.price_id "
                 "WHERE ms.club_id = :c AND ms.user_id = :u AND ms.status = 'active' "
                 "  AND (ms.current_period_end IS NULL OR ms.current_period_end >= CURRENT_DATE)"),
            {"c": str(club_id), "u": str(user_id)},
        ).mappings().all()
        if not rows:
            return out

        def _best(key):
            vals = [r[key] for r in rows]
            if any(v is None for v in vals):
                return None            # an unconstrained tier (or a NULL-price trial) wins
            return max(int(v) for v in vals)

        return {"max_covered_minutes": _best("max_covered_minutes"),
                "max_covered_per_day": _best("max_covered_per_day"),
                "max_courts_per_day": _best("max_courts_per_day")}
    except Exception:
        log.debug("active_caps suppressed", exc_info=False)
        return out


def service_members_covered(session, *, club_id, resource_id):
    """False if the court's court-SERVICE (billing.product) is flagged members_covered=false (never free
    for members, e.g. a clay court). Default True (covered) when unknown/unset. Guarded -> True (a missing
    flag must never make a covered court PAYG for everyone)."""
    try:
        pid = _pricing.court_service_for_resource(session, club_id=club_id, resource_id=resource_id)
        if not pid:
            return True
        row = session.execute(
            text("SELECT members_covered FROM billing.product WHERE club_id = :c AND id = :p"),
            {"c": str(club_id), "p": str(pid)},
        ).scalar()
        return row is None or bool(row)
    except Exception:
        return True


def local_day_bounds_utc(session, club_id, dt_utc):
    """The UTC [start, end) bounds of the club-LOCAL calendar day containing dt_utc."""
    tz = timezone.utc
    try:
        from diary.availability import _club_tz
        tz = _club_tz(session, club_id)
    except Exception:
        pass
    local = dt_utc.astimezone(tz)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return (start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc))


def _covered_usage(session, *, club_id, user_id, day_start_utc, day_end_utc, exclude_booking_id=None):
    """(count, {court resource_ids}) of the member's ACTIVE covered COURT bookings on a local day
    [day_start_utc, day_end_utc). Covered = settlement_mode='membership_covered', held/confirmed. The
    lesson's auto-held court row is settled at_court/online (not membership_covered), so it never counts.
    Guarded -> (0, set())."""
    try:
        params = {"c": str(club_id), "u": str(user_id), "ds": day_start_utc, "de": day_end_utc}
        ex = "AND id <> :ex " if exclude_booking_id else ""
        if exclude_booking_id:
            params["ex"] = str(exclude_booking_id)
        rows = session.execute(
            text("SELECT id, resource_id FROM diary.booking "
                 "WHERE club_id = :c AND booked_by_user_id = :u AND booking_type = 'court' "
                 "  AND settlement_mode = 'membership_covered' AND status IN ('held','confirmed') "
                 "  AND starts_at >= :ds AND starts_at < :de " + ex),
            params,
        ).mappings().all()
        courts = set(str(r["resource_id"]) for r in rows if r["resource_id"])
        return (len(rows), courts)
    except Exception:
        return (0, set())


def court_covered(session, *, club_id, user_id, starts_at, ends_at, resource_id, now=None):
    """AUTHORITATIVE (create_booking): is THIS court booking free under the member's entitlement? True only
    when the active membership covers the start time (access window) AND the court service is member-eligible
    AND the duration is within max_covered_minutes AND the daily covered-booking + distinct-court caps aren't
    exceeded. Otherwise False -> the caller charges PAYG. Guarded -> False (never blocks; a non-covered court
    is simply billed)."""
    try:
        if not user_id or starts_at is None:
            return False
        # 1) window coverage (active membership + inside its access window) — the existing rule.
        if not _pricing.membership_covers(session, club_id=club_id, user_id=user_id, starts_at=starts_at):
            return False
        # 2) court-service eligibility (a clay court is never covered).
        if not service_members_covered(session, club_id=club_id, resource_id=resource_id):
            return False
        caps = active_caps(session, club_id=club_id, user_id=user_id)
        # 3) duration cap.
        if caps["max_covered_minutes"] is not None and ends_at is not None:
            dur = int((ends_at - starts_at).total_seconds() // 60)
            if dur > int(caps["max_covered_minutes"]):
                return False
        # 4) daily caps on the booking's LOCAL day.
        if caps["max_covered_per_day"] is not None or caps["max_courts_per_day"] is not None:
            ds, de = local_day_bounds_utc(session, club_id, starts_at)
            count, courts = _covered_usage(session, club_id=club_id, user_id=user_id,
                                           day_start_utc=ds, day_end_utc=de)
            if caps["max_covered_per_day"] is not None and count >= int(caps["max_covered_per_day"]):
                return False
            if caps["max_courts_per_day"] is not None:
                rid = str(resource_id) if resource_id else None
                if rid not in courts and len(courts) >= int(caps["max_courts_per_day"]):
                    return False
        return True
    except Exception:
        log.debug("court_covered suppressed", exc_info=False)
        return False


# --- availability display helpers (precompute once per call, decide per slot) ---------------------

def availability_context(session, *, club_id, user_id, duration_min, range_start_utc, range_end_utc):
    """Precompute a member's entitlement for shaping COURT availability over a range in ONE pass (not
    per-slot): the caps, whether the requested duration is within the covered cap, and per-local-day covered
    usage {local_date -> (count, {court_ids})}. Returns None when there's no active membership (caller
    treats every slot as PAYG) or billing is absent. Guarded -> None."""
    try:
        if not user_id or not _pricing._membership_sub_exists(session):
            return None
        caps = active_caps(session, club_id=club_id, user_id=user_id)
        dur_ok = (caps["max_covered_minutes"] is None
                  or int(duration_min or 0) <= int(caps["max_covered_minutes"]))
        usage = {}
        if caps["max_covered_per_day"] is not None or caps["max_courts_per_day"] is not None:
            tz = timezone.utc
            try:
                from diary.availability import _club_tz
                tz = _club_tz(session, club_id)
            except Exception:
                pass
            rows = session.execute(
                text("SELECT resource_id, starts_at FROM diary.booking "
                     "WHERE club_id = :c AND booked_by_user_id = :u AND booking_type = 'court' "
                     "  AND settlement_mode = 'membership_covered' AND status IN ('held','confirmed') "
                     "  AND starts_at >= :ds AND starts_at < :de"),
                {"c": str(club_id), "u": str(user_id), "ds": range_start_utc, "de": range_end_utc},
            ).mappings().all()
            for r in rows:
                day = r["starts_at"].astimezone(tz).date()
                cnt, courts = usage.get(day, (0, set()))
                cnt += 1
                if r["resource_id"]:
                    courts.add(str(r["resource_id"]))
                usage[day] = (cnt, courts)
        return {"caps": caps, "dur_ok": dur_ok, "usage": usage}
    except Exception:
        log.debug("availability_context suppressed", exc_info=False)
        return None


def slot_covered(ctx, *, service_covered, slot_local, court_id):
    """Per-slot display decision (using the precomputed availability_context): would a court booking at this
    slot be FREE for the member? Combines duration cap, court-service eligibility and the daily caps against
    the slot's local day. ctx=None -> False (no membership → PAYG). Pure (no DB)."""
    if not ctx:
        return False
    if not service_covered or not ctx.get("dur_ok"):
        return False
    caps = ctx["caps"]
    if caps["max_covered_per_day"] is None and caps["max_courts_per_day"] is None:
        return True
    day = slot_local.date()
    count, courts = ctx["usage"].get(day, (0, set()))
    if caps["max_covered_per_day"] is not None and count >= int(caps["max_covered_per_day"]):
        return False
    if caps["max_courts_per_day"] is not None:
        rid = str(court_id) if court_id else None
        if rid not in courts and len(courts) >= int(caps["max_courts_per_day"]):
            return False
    return True

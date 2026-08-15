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


_CAP_KEYS = ("max_covered_minutes", "max_covered_per_day", "max_courts_per_day")


def club_default_caps(session, club_id):
    """The club's DEFAULT entitlement caps (club.policy) — the floor every membership inherits unless
    its own tier overrides them. None = no club default.

    Why this exists: the caps live on billing.price, so they only ever applied to a tier that HAD a
    price row. The signup trial usually does not (grant_signup_trial links a price only when the owner
    has configured a trial TIER), so trial members were entirely uncapped — and, worse, `_best` below
    treated a NULL cap as "an unconstrained tier wins", so simply HOLDING the price-less trial
    alongside a capped paid tier wiped that tier's caps out too. A club-level default means "all
    memberships are capped" is one setting, not a thing you must remember on every tier you ever
    create. Guarded -> all None."""
    out = {k: None for k in _CAP_KEYS}
    try:
        row = session.execute(
            text("SELECT default_max_covered_minutes, default_max_covered_per_day, "
                 "       default_max_courts_per_day FROM club.policy WHERE club_id = :c"),
            {"c": str(club_id)},
        ).mappings().first()
        if not row:
            return out
        return {"max_covered_minutes": row["default_max_covered_minutes"],
                "max_covered_per_day": row["default_max_covered_per_day"],
                "max_courts_per_day": row["default_max_courts_per_day"]}
    except Exception:
        log.debug("club_default_caps suppressed", exc_info=False)
        return out


def active_caps(session, *, club_id, user_id, on_date=None):
    """The member's covered-booking caps ON A GIVEN DAY — the MOST GENEROUS across the tiers active
    that day (a member benefits from their best tier). Returns
    {max_covered_minutes, max_covered_per_day, max_courts_per_day} where None = no cap.
    Guarded -> all None.

    TWO FIXES LIVE HERE.

    (1) `on_date` — which day's entitlement to read. This used to be hard-coded to CURRENT_DATE, the
    same bug as membership_covers: caps were read off whatever the member held TODAY, even when
    pricing a booking months out. Callers pass the booking's club-local date; None keeps the old
    today-based meaning for display callers that genuinely mean "right now".

    (2) A tier that specifies NO cap now INHERITS the club default instead of counting as
    "unconstrained". The old rule — any NULL wins — meant the price-less signup trial silently
    removed every cap the member's paid tier set. A tier still overrides the club default when it
    sets its own value, and most-generous-wins still applies ACROSS tiers, so a premium tier can be
    more generous than the club floor. A tier cannot express "unlimited" against a club default;
    NULL means inherit, exactly as payment_modes already does."""
    out = {k: None for k in _CAP_KEYS}
    try:
        if not user_id or not _pricing._membership_sub_exists(session):
            return out
        rows = session.execute(
            text("SELECT p.max_covered_minutes, p.max_covered_per_day, p.max_courts_per_day "
                 "FROM billing.membership_subscription ms "
                 "LEFT JOIN billing.price p ON p.id = ms.price_id "
                 "WHERE ms.club_id = :c AND ms.user_id = :u AND ms.status = 'active' "
                 "  AND (ms.current_period_end IS NULL "
                 "       OR ms.current_period_end >= COALESCE(CAST(:on_date AS date), CURRENT_DATE))"),
            {"c": str(club_id), "u": str(user_id), "on_date": on_date},
        ).mappings().all()
        if not rows:
            return out
        defaults = club_default_caps(session, club_id)

        def _best(key):
            fallback = defaults.get(key)
            # A tier that sets nothing inherits the club default; only a genuine absence of BOTH is
            # unlimited. Most generous across the tiers the member actually holds.
            vals = [(r[key] if r[key] is not None else fallback) for r in rows]
            if any(v is None for v in vals):
                return None
            return max(int(v) for v in vals)

        return {k: _best(k) for k in _CAP_KEYS}
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


def local_date(session, club_id, dt_utc):
    """The club-LOCAL calendar date of a UTC instant. This is the day a booking counts against for
    every entitlement question (term expiry, daily caps), so it must never be derived in UTC — a
    22:30 SAST booking is 20:30 UTC the same day, but a 01:00 SAST one is 23:00 UTC the day BEFORE,
    and reading that in UTC would bill it against the wrong day's allowance. Guarded -> UTC date."""
    try:
        from diary.availability import _club_tz
        return dt_utc.astimezone(_club_tz(session, club_id)).date()
    except Exception:
        return dt_utc.date()


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


def _has_overlapping_covered(session, *, club_id, user_id, starts_at, ends_at,
                             exclude_booking_id=None):
    """True if the member ALREADY holds a covered court booking overlapping [starts_at, ends_at).

    The GiST exclusion constraint is keyed on resource_id, so it stops one COURT being taken twice —
    it has nothing to say about one PERSON taking two different courts at the same moment. Booking
    two courts at once is legitimate (a doubles group, a family), so we don't refuse it; what isn't
    legitimate is both being FREE. The membership covers the member, not every court they can reach
    simultaneously. The second overlapping one downgrades to PAYG. Guarded -> False (never block)."""
    try:
        params = {"c": str(club_id), "u": str(user_id), "s": starts_at, "e": ends_at}
        ex = ""
        if exclude_booking_id:
            ex = "AND id <> CAST(:ex AS uuid) "
            params["ex"] = str(exclude_booking_id)
        return bool(session.execute(
            text("SELECT 1 FROM diary.booking "
                 "WHERE club_id = :c AND booked_by_user_id = :u AND booking_type = 'court' "
                 "  AND settlement_mode = 'membership_covered' AND status IN ('held','confirmed') "
                 "  AND ends_at > :s AND starts_at < :e " + ex + "LIMIT 1"),
            params,
        ).first())
    except Exception:
        return False


def court_covered(session, *, club_id, user_id, starts_at, ends_at, resource_id, now=None,
                  exclude_booking_id=None):
    """AUTHORITATIVE (create_booking): is THIS court booking free under the member's entitlement? True only
    when the active membership covers the start time (access window) AND the court service is member-eligible
    AND the duration is within max_covered_minutes AND the daily covered-booking + distinct-court caps aren't
    exceeded. Otherwise False -> the caller charges PAYG. Guarded -> False (never blocks; a non-covered court
    is simply billed)."""
    try:
        if not user_id or starts_at is None:
            return False
        # 1) window coverage (active membership + inside its access window) — the existing rule, plus
        # the tier's covers_peak. The COURT is passed because peak is per court: a tier that is not
        # covered at peak must be judged against the peak window of the court actually being booked,
        # not the club default, or clay (which has no peak here) would wrongly read as peak.
        if not _pricing.membership_covers(session, club_id=club_id, user_id=user_id,
                                          starts_at=starts_at, resource_id=resource_id):
            return False
        # 2) court-service eligibility (a clay court is never covered).
        if not service_members_covered(session, club_id=club_id, resource_id=resource_id):
            return False
        # Caps are read for the day the booking FALLS ON, not today — a booking months out must be
        # judged against the entitlement in force then (see active_caps).
        booking_day = local_date(session, club_id, starts_at)
        caps = active_caps(session, club_id=club_id, user_id=user_id, on_date=booking_day)
        # 3) duration cap.
        if caps["max_covered_minutes"] is not None and ends_at is not None:
            dur = int((ends_at - starts_at).total_seconds() // 60)
            if dur > int(caps["max_covered_minutes"]):
                return False
        # 4) daily caps on the booking's LOCAL day.
        if caps["max_covered_per_day"] is not None or caps["max_courts_per_day"] is not None:
            ds, de = local_day_bounds_utc(session, club_id, starts_at)
            count, courts = _covered_usage(session, club_id=club_id, user_id=user_id,
                                           day_start_utc=ds, day_end_utc=de,
                                           exclude_booking_id=exclude_booking_id)
            if caps["max_covered_per_day"] is not None and count >= int(caps["max_covered_per_day"]):
                return False
            if caps["max_courts_per_day"] is not None:
                rid = str(resource_id) if resource_id else None
                if rid not in courts and len(courts) >= int(caps["max_courts_per_day"]):
                    return False
        # 5) CONCURRENCY — one member, one covered court at a time. Independent of the daily caps
        # above (a club that allows several covered bookings a day still shouldn't hand out two at
        # the same instant), so it is checked even when no cap is configured.
        if _has_overlapping_covered(session, club_id=club_id, user_id=user_id, starts_at=starts_at,
                                    ends_at=ends_at, exclude_booking_id=exclude_booking_id):
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
        # The last day the member's term actually runs to. The picker shapes a whole range in one
        # pass, so without this it happily advertised "Covered by your membership" on days past the
        # member's own expiry — which the server then (correctly) charged PAYG for: shown != charged,
        # and the member finds out at the desk.
        covered_until = _pricing.membership_covered_until(session, club_id=club_id, user_id=user_id)
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
        return {"caps": caps, "dur_ok": dur_ok, "usage": usage, "covered_until": covered_until}
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
    day = slot_local.date()
    # Past the member's own term this slot is simply PAYG, however generous their caps are.
    until = ctx.get("covered_until")
    if until is not None and day > until:
        return False
    caps = ctx["caps"]
    if caps["max_covered_per_day"] is None and caps["max_courts_per_day"] is None:
        return True
    count, courts = ctx["usage"].get(day, (0, set()))
    if caps["max_covered_per_day"] is not None and count >= int(caps["max_covered_per_day"]):
        return False
    if caps["max_courts_per_day"] is not None:
        rid = str(court_id) if court_id else None
        if rid not in courts and len(courts) >= int(caps["max_courts_per_day"]):
            return False
    return True

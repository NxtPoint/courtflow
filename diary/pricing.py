# diary/pricing.py — guarded price lookup (billing.* is Agent C's lane).
#
# Per-duration PAYG pricing (the NextPoint model): a service (billing.product) carries ONE
# billing.price row per offered duration (duration_minutes set, unit='per_booking',
# audience='any', the fixed price). price_for() resolves the row for a kind/product + a chosen
# duration; durations_for() lists every priced duration for the duration picker. An active
# MEMBERSHIP makes COURT bookings free — that's resolved at booking time via
# has_active_membership(), NOT via an R0 price row.
#
# billing.price shape we read (docs/02 §5): (club_id, product_id, audience, amount_minor,
# currency_code, unit, duration_minutes, active). billing.product carries (kind, coach_user_id).
# This lane runs in isolation, so every read is GUARDED: if billing.* isn't present we return
# None / [] and callers carry price=None (the UI shows "price on request"). We never WRITE billing.*.

import logging

from sqlalchemy import text

log = logging.getLogger("diary.pricing")


def _billing_price_exists(session):
    row = session.execute(
        text("SELECT 1 FROM information_schema.tables "
             "WHERE table_schema = 'billing' AND table_name = 'price'")
    ).first()
    return row is not None


def _membership_sub_exists(session):
    row = session.execute(
        text("SELECT 1 FROM information_schema.tables "
             "WHERE table_schema = 'billing' AND table_name = 'membership_subscription'")
    ).first()
    return row is not None


def _coach_has_own_product(session, club_id, kind, coach_user_id):
    """True if this coach has their OWN active product of `kind`. When they do, their rate card is
    used EXACTLY (no shared / other-coach rows mixed in — that leaked phantom durations + zero-rated
    prices); when they don't, a club-shared (coach-agnostic) product is the fallback. Guarded → False."""
    try:
        row = session.execute(
            text("SELECT 1 FROM billing.product WHERE club_id = :c AND kind = :k "
                 "AND coach_user_id = :u AND active = true LIMIT 1"),
            {"c": club_id, "k": kind, "u": coach_user_id},
        ).first()
        return row is not None
    except Exception:
        return False


def _default_court_product_id(session, club_id):
    """The club's DEFAULT court-hire product (billing.product kind='court_booking'): the SINGLE
    active court product, or None when there are zero or MANY. Many → ambiguous, so callers keep
    the unscoped 'cheapest across court products' fallback (a NULL-product court in a multi-service
    club is a misconfiguration the owner resolves by allocating it). Guarded → None. Mirrors the
    coach two-tier fallback: a court with no explicit service falls back to the one shared product."""
    try:
        rows = session.execute(
            text("SELECT id FROM billing.product WHERE club_id = :c AND kind = 'court_booking' "
                 "AND active = true LIMIT 2"),
            {"c": club_id},
        ).scalars().all()
        return str(rows[0]) if len(rows) == 1 else None
    except Exception:
        return None


def _resource_has_product_col(session):
    """diary.resource.product_id is added by this lane's boot DDL; absent only pre-migration.
    Cached per session so we don't re-probe information_schema on every court read."""
    cache = getattr(session, "_cf_resource_product_col", None)
    if cache is not None:
        return cache
    try:
        row = session.execute(
            text("SELECT 1 FROM information_schema.columns "
                 "WHERE table_schema='diary' AND table_name='resource' "
                 "  AND column_name='product_id'")
        ).first()
        present = row is not None
    except Exception:
        present = False
    try:
        session._cf_resource_product_col = present
    except Exception:
        pass
    return present


def court_service_for_resource(session, *, club_id, resource_id):
    """The court SERVICE (billing.product id, as a str) a court resource belongs to: its OWN
    resource.product_id when set, else the club's DEFAULT court product (single court_booking
    product). None when neither resolves — an unconfigured multi-product club, or billing absent —
    in which case callers price unscoped (cheapest across court products). Guarded → default/None.

    This is the single resolution rule booking + availability use to price a court at ITS service's
    rate and to enumerate a service's own courts."""
    if resource_id and _resource_has_product_col(session):
        try:
            pid = session.execute(
                text("SELECT product_id FROM diary.resource WHERE club_id = :c AND id = :r"),
                {"c": club_id, "r": resource_id},
            ).scalar()
            if pid:
                return str(pid)
        except Exception:
            pass
    return _default_court_product_id(session, club_id)


def price_for(session, *, club_id, kind=None, duration_minutes=None, product_id=None,
              audience="any", coach_user_id=None, at_local=None):
    """Best matching billing.price for a service + a chosen duration. Returns a dict
    {price_id, amount_minor, base_amount_minor, is_peak, currency_code, unit, duration_minutes} or None
    (billing absent / no match). Never raises — pricing is best-effort here.

    PEAK pricing (court hire): pass `at_local` (the booking/slot's CLUB-LOCAL start). If it falls in the
    club peak window (club.policy.peak_*) AND this price row has a peak_amount_minor, `amount_minor` is the
    PEAK amount (and is_peak=True); `base_amount_minor` always carries the off-peak amount. Only court rows
    ever set peak_amount_minor, so this is naturally court-only. at_local=None -> always the base amount.

    Scope: by product_id if given, else by product kind (via billing.product). For a lesson,
    coach_user_id scopes to that coach's product when billing.product.coach_user_id exists.

    Duration resolution (when duration_minutes is given): EXACT match first, else the nearest
    priced duration <= requested, else any priced row. audience is honoured (exact over 'any')
    so legacy per-audience catalogues still resolve."""
    try:
        if not _billing_price_exists(session):
            return None
        # COURT services are product-scoped: with several court products (Hardcourt vs Clay) the old
        # 'cheapest across ALL court products' pick blended their rates. When no product_id is given
        # for a court, resolve the club's DEFAULT court product so a single-service club is unchanged
        # and a NULL-product court prices via the one shared product — never the cheapest of many.
        if product_id is None and kind == "court_booking":
            product_id = _default_court_product_id(session, club_id)
        params = {"c": club_id, "aud": audience}
        coach_scoped = False
        sql = ("SELECT p.id AS price_id, p.amount_minor, p.peak_amount_minor, p.currency_code, p.unit, "
               "       p.audience, p.duration_minutes "
               "FROM billing.price p ")
        where = ["p.club_id = :c", "p.active = true", "p.audience IN (:aud, 'any')"]
        if product_id is not None:
            where.append("p.product_id = :pid")
            params["pid"] = product_id
        elif kind is not None:
            sql += "JOIN billing.product pr ON pr.id = p.product_id AND pr.active = true "
            where.append("pr.kind = :kind")
            params["kind"] = kind
            if coach_user_id is not None and _product_has_coach_col(session):
                # TWO-TIER (never merge): if the coach has their OWN product use ONLY it (their rate
                # card exactly); else fall back to a club-shared (coach-agnostic) product. Merging
                # leaked other durations + zero-rated a lesson (the cheapest matching row won).
                where.append("pr.coach_user_id = :coach"
                             if _coach_has_own_product(session, club_id, kind, coach_user_id)
                             else "pr.coach_user_id IS NULL")
                params["coach"] = coach_user_id
        sql += "WHERE " + " AND ".join(where)
        # Ranking: exact duration first, then the nearest priced duration <= requested, then any;
        # tie-break to the exact audience, then cheapest. (Coach scope is a hard filter above now.)
        coach_rank = ""
        if duration_minutes is not None:
            params["dur"] = int(duration_minutes)
            sql += (" ORDER BY (p.duration_minutes = :dur) DESC, " + coach_rank +
                    "(p.duration_minutes IS NOT NULL AND p.duration_minutes <= :dur) DESC, "
                    "p.duration_minutes DESC NULLS LAST, "
                    "(p.audience = :aud) DESC, p.amount_minor ASC LIMIT 1")
        else:
            sql += " ORDER BY " + coach_rank + "(p.audience = :aud) DESC, p.amount_minor ASC LIMIT 1"
        row = session.execute(text(sql), params).mappings().first()
        if not row:
            return None
        base = row["amount_minor"]
        peak = row["peak_amount_minor"]
        is_peak = (at_local is not None and peak is not None
                   and in_peak_window(session, club_id=club_id, local_dt=at_local))
        return {
            "price_id": str(row["price_id"]),
            "amount_minor": (peak if is_peak else base),
            "base_amount_minor": base,
            "peak_amount_minor": peak,     # raw peak (or None) — lets callers price per-slot without re-query
            "is_peak": is_peak,
            "currency_code": row["currency_code"],
            "unit": row["unit"],
            "duration_minutes": row["duration_minutes"],
        }
    except Exception:
        log.debug("price_for() suppressed (billing not ready)", exc_info=False)
        return None


def durations_for(session, *, club_id, kind, coach_user_id=None, audience="any", product_id=None):
    """Every priced duration for a service (the frontend duration picker). Returns a list of
    {duration_minutes, amount_minor, price_id, currency_code} sorted by duration ascending.
    Only rows with a duration set are returned (per-duration pricing). Guarded -> [] if billing
    absent. For a lesson, coach_user_id scopes to that coach's product when available. For a COURT,
    product_id scopes to a specific court SERVICE (Hardcourt vs Clay); when omitted the DEFAULT
    court product is resolved so a single-service club is unchanged."""
    try:
        if not _billing_price_exists(session):
            return []
        # COURT: scope to the chosen (or default) court product so durations don't blend services.
        if product_id is None and kind == "court_booking":
            product_id = _default_court_product_id(session, club_id)
        params = {"c": club_id, "aud": audience}
        where = ["p.club_id = :c", "p.active = true", "pr.active = true",
                 "p.duration_minutes IS NOT NULL", "p.audience IN (:aud, 'any')"]
        if product_id is not None:
            # Hard product scope (a specific court/lesson service) — no kind/coach merge possible.
            where.append("p.product_id = :pid")
            params["pid"] = product_id
        else:
            where.append("pr.kind = :kind")
            params["kind"] = kind
        coach_rank = ""
        if product_id is None and coach_user_id is not None and _product_has_coach_col(session):
            # TWO-TIER (mirrors price_for so the picker shows EXACTLY what's charged): the coach's own
            # rate card if they have one, else a club-shared product — never a merge of both.
            where.append("pr.coach_user_id = :coach"
                         if _coach_has_own_product(session, club_id, kind, coach_user_id)
                         else "pr.coach_user_id IS NULL")
            params["coach"] = coach_user_id
        sql = ("SELECT DISTINCT ON (p.duration_minutes) p.duration_minutes, p.amount_minor, "
               "       p.id AS price_id, p.currency_code "
               "FROM billing.price p "
               "JOIN billing.product pr ON pr.id = p.product_id "
               "WHERE " + " AND ".join(where) +
               # DISTINCT ON keeps one row per duration: prefer the coach's OWN, then exact audience, then cheapest.
               " ORDER BY p.duration_minutes ASC, " + coach_rank + "(p.audience = :aud) DESC, p.amount_minor ASC")
        rows = session.execute(text(sql), params).mappings().all()
        return [{
            "duration_minutes": r["duration_minutes"],
            "amount_minor": r["amount_minor"],
            "price_id": str(r["price_id"]),
            "currency_code": r["currency_code"],
        } for r in rows]
    except Exception:
        log.debug("durations_for() suppressed (billing not ready)", exc_info=False)
        return []


_PAY_MODES = ("online", "at_court", "monthly_account")


def services_for(session, *, club_id, kind, coach_user_id=None, audience="any"):
    """Bookable SERVICES (products) of a kind for a coach — each with its OWN durations + payment
    modes + name — so the picker can offer e.g. 'Private lesson' vs 'Semi-private' separately (a coach
    can have several). Two-tier coach scope (own products, else shared), like price_for/durations_for.
    Returns [{product_id, name, payment_modes, currency_code, durations:[{duration_minutes,
    amount_minor, price_id}]}] ordered by name. Guarded → [].

    For kind='court_booking' this returns each court SERVICE (Hardcourt Hire, Clay Hire, …) with its
    OWN durations + price — exactly what the client court picker calls to offer a specific service."""
    _default_name = {"court_booking": "Court hire", "class": "Class"}.get(kind, "Lesson")
    try:
        if not _billing_price_exists(session):
            return []
        params = {"c": club_id, "kind": kind, "aud": audience}
        where = ["p.club_id = :c", "p.active = true", "pr.active = true", "pr.kind = :kind",
                 "p.duration_minutes IS NOT NULL", "p.audience IN (:aud, 'any')"]
        if coach_user_id is not None and _product_has_coach_col(session):
            where.append("pr.coach_user_id = :coach"
                         if _coach_has_own_product(session, club_id, kind, coach_user_id)
                         else "pr.coach_user_id IS NULL")
            params["coach"] = coach_user_id
        rows = session.execute(
            text("SELECT pr.id AS product_id, pr.name, pr.payment_modes, p.duration_minutes, "
                 "       p.amount_minor, p.id AS price_id, p.currency_code "
                 "FROM billing.price p JOIN billing.product pr ON pr.id = p.product_id "
                 "WHERE " + " AND ".join(where) +
                 " ORDER BY pr.name NULLS LAST, p.duration_minutes ASC, p.amount_minor ASC"),
            params,
        ).mappings().all()
        out, by_id = [], {}
        for r in rows:
            pid = str(r["product_id"])
            svc = by_id.get(pid)
            if svc is None:
                modes = [m.strip() for m in str(r["payment_modes"] or "").split(",")
                         if m.strip() in _PAY_MODES]
                svc = {"product_id": pid, "name": r["name"] or _default_name,
                       "payment_modes": (modes or None), "currency_code": r["currency_code"],
                       "durations": []}
                by_id[pid] = svc
                out.append(svc)
            # one row per duration per product (ORDER BY amount ASC → keep the cheapest).
            if not any(d["duration_minutes"] == r["duration_minutes"] for d in svc["durations"]):
                svc["durations"].append({"duration_minutes": r["duration_minutes"],
                                         "amount_minor": r["amount_minor"], "price_id": str(r["price_id"])})
        return out
    except Exception:
        log.debug("services_for() suppressed (billing not ready)", exc_info=False)
        return []


def payment_modes_for(session, *, club_id, kind, coach_user_id=None, product_id=None):
    """The per-service payment preference (allowed settlement modes) for this service's product —
    a subset of the club-enabled methods, or None (= no per-service restriction, all club-enabled).
    `kind` is the PRODUCT kind (court_booking|lesson|class); product_id scopes to a specific court/
    lesson SERVICE when given (a court service's own payment options). Guarded -> None."""
    try:
        if not _billing_price_exists(session):
            return None
        if product_id is not None:
            # Exact service — the one product's own preference.
            csv = session.execute(
                text("SELECT payment_modes FROM billing.product "
                     "WHERE club_id = :c AND id = :pid AND active = true"),
                {"c": club_id, "pid": product_id},
            ).scalar()
            if not csv:
                return None
            modes = [m.strip() for m in str(csv).split(",") if m.strip() in _PAY_MODES]
            return modes or None
        where = ["club_id = :c", "kind = :kind", "active = true"]
        params = {"c": club_id, "kind": kind}
        coach_rank = ""
        if coach_user_id is not None and _product_has_coach_col(session):
            # TWO-TIER (same resolution as price_for/durations_for): the coach's own product's
            # preference if they have one, else a club-shared product — never merged.
            where.append("coach_user_id = :coach"
                         if _coach_has_own_product(session, club_id, kind, coach_user_id)
                         else "coach_user_id IS NULL")
            params["coach"] = coach_user_id
        csv = session.execute(
            text("SELECT payment_modes FROM billing.product WHERE " + " AND ".join(where)
                 + " ORDER BY " + coach_rank + "created_at LIMIT 1"),
            params,
        ).scalar()
        if not csv:
            return None
        modes = [m.strip() for m in str(csv).split(",") if m.strip() in _PAY_MODES]
        return modes or None
    except Exception:
        log.debug("payment_modes_for() suppressed (billing not ready)", exc_info=False)
        return None


def has_active_membership(session, *, club_id, user_id):
    """True if the user holds an ACTIVE billing.membership_subscription in this club whose
    current_period_end is NULL (open-ended) or today-or-later. Guarded -> False if the table
    isn't present or anything goes wrong (a missing membership must never block a booking)."""
    try:
        if not user_id or not _membership_sub_exists(session):
            return False
        row = session.execute(
            text("SELECT 1 FROM billing.membership_subscription "
                 "WHERE club_id = :c AND user_id = :u AND status = 'active' "
                 "  AND (current_period_end IS NULL OR current_period_end >= CURRENT_DATE) "
                 "LIMIT 1"),
            {"c": club_id, "u": user_id},
        ).first()
        return row is not None
    except Exception:
        log.debug("has_active_membership() suppressed (billing not ready)", exc_info=False)
        return False


def active_membership_windows(session, *, club_id, user_id):
    """The access windows of the user's ACTIVE memberships, for pricing availability per-slot WITHOUT
    a DB round-trip per slot. Returns a list where each entry is either None (unconstrained — covers
    any time, e.g. a trial or a full membership) or {days, start_min, end_min}. Empty list = no active
    membership. Guarded -> [] (a missing membership never blocks a booking)."""
    try:
        if not user_id or not _membership_sub_exists(session):
            return []
        rows = session.execute(
            text("SELECT p.access_days, p.access_start_min, p.access_end_min "
                 "FROM billing.membership_subscription ms "
                 "LEFT JOIN billing.price p ON p.id = ms.price_id "
                 "WHERE ms.club_id = :c AND ms.user_id = :u AND ms.status = 'active' "
                 "  AND (ms.current_period_end IS NULL OR ms.current_period_end >= CURRENT_DATE)"),
            {"c": club_id, "u": user_id},
        ).mappings().all()
        out = []
        for r in rows:
            if not r["access_days"] and r["access_start_min"] is None and r["access_end_min"] is None:
                out.append(None)  # unconstrained — covers any time
            else:
                out.append({
                    "days": [int(x) for x in r["access_days"].split(",") if str(x).strip()] if r["access_days"] else None,
                    "start_min": r["access_start_min"], "end_min": r["access_end_min"],
                })
        return out
    except Exception:
        log.debug("active_membership_windows() suppressed (billing not ready)", exc_info=False)
        return []


def any_window_covers(windows, starts_local):
    """True if ANY window in `windows` covers the club-LOCAL start datetime (None = unconstrained).
    Mirrors membership_covers' day/time test (ISO weekday Mon=1; minutes-from-midnight, end exclusive)."""
    if not windows or starts_local is None:
        return False
    iso = starts_local.isoweekday()
    mod = starts_local.hour * 60 + starts_local.minute
    for w in windows:
        if w is None:
            return True
        if w.get("days") and iso not in w["days"]:
            continue
        if w.get("start_min") is not None and mod < w["start_min"]:
            continue
        if w.get("end_min") is not None and mod >= w["end_min"]:
            continue
        return True
    return False


def _peak_window(session, club_id):
    """The club's PEAK court-pricing window as {days:[int]|None, start_min, end_min}, or None when no peak
    window is configured. Cached per (session, club) so per-slot availability pricing never re-queries.
    Guarded -> None (a missing/empty policy just means no peak pricing)."""
    cache = getattr(session, "_cf_peak_window", None)
    if cache is None:
        cache = {}
        try:
            session._cf_peak_window = cache
        except Exception:
            pass
    key = str(club_id)
    if key in cache:
        return cache[key]
    win = None
    try:
        row = session.execute(
            text("SELECT peak_days, peak_start_min, peak_end_min FROM club.policy WHERE club_id = :c"),
            {"c": key},
        ).mappings().first()
        if row and (row["peak_start_min"] is not None or row["peak_end_min"] is not None or row["peak_days"]):
            win = {
                "days": [int(x) for x in row["peak_days"].split(",") if str(x).strip()] if row["peak_days"] else None,
                "start_min": row["peak_start_min"], "end_min": row["peak_end_min"],
            }
    except Exception:
        win = None
    cache[key] = win
    return win


def in_peak_window(session, *, club_id, local_dt):
    """True if local_dt (a CLUB-LOCAL datetime) falls inside the club peak window. Reuses any_window_covers'
    day/time semantics (ISO weekday, minutes-from-midnight, end exclusive). Guarded -> False."""
    win = _peak_window(session, club_id)
    if not win or local_dt is None:
        return False
    return any_window_covers([win], local_dt)


def membership_covers(session, *, club_id, user_id, starts_at):
    """True if an ACTIVE membership covers a COURT booking that STARTS at `starts_at` — i.e. the
    member is active AND the booking falls inside that plan's access window (Phase 5). A plan with
    NO window (trial/unconstrained) covers any time; a time-boxed tier (e.g. Student weekdays-only)
    covers only inside its days + hours, otherwise the booking is PAYG. ANY qualifying subscription
    wins. Guarded -> False (never blocks a booking; a non-covered booking just isn't free).

    Day/time are taken from `starts_at` (the booking's local start). access_days is CSV ISO weekdays
    (Mon=1..Sun=7); access_start_min/access_end_min are minutes-from-midnight (start inclusive, end
    exclusive)."""
    try:
        if not user_id or starts_at is None or not _membership_sub_exists(session):
            return False
        # Access windows (access_days / start_min / end_min) are CLUB-LOCAL — the same hours the owner
        # captures on the service. starts_at is tz-aware UTC internally, so convert to the club tz
        # before deriving weekday + minute-of-day; otherwise an off-peak tier is evaluated in UTC and
        # the covered/PAYG decision diverges from the calendar's local price (shown ≠ charged).
        local = starts_at
        try:
            from diary.availability import _club_tz
            local = starts_at.astimezone(_club_tz(session, club_id))
        except Exception:
            pass
        iso_dow = local.isoweekday()                     # 1=Mon..7=Sun (club-local)
        min_of_day = local.hour * 60 + local.minute
        row = session.execute(
            text("""
                SELECT 1
                FROM billing.membership_subscription ms
                LEFT JOIN billing.price p ON p.id = ms.price_id
                WHERE ms.club_id = :c AND ms.user_id = :u AND ms.status = 'active'
                  AND (ms.current_period_end IS NULL OR ms.current_period_end >= CURRENT_DATE)
                  AND (
                    p.id IS NULL  -- trial / no linked plan -> unconstrained, covers any time
                    OR (
                      (p.access_days IS NULL
                       OR CAST(:dow AS text) = ANY(string_to_array(p.access_days, ',')))
                      AND (p.access_start_min IS NULL OR :mod >= p.access_start_min)
                      AND (p.access_end_min   IS NULL OR :mod <  p.access_end_min)
                    )
                  )
                LIMIT 1
            """),
            {"c": club_id, "u": user_id, "dow": iso_dow, "mod": min_of_day},
        ).first()
        return row is not None
    except Exception:
        log.debug("membership_covers() suppressed (billing not ready)", exc_info=False)
        return False


def _product_has_coach_col(session):
    """billing.product.coach_user_id is added by the coach lane; absent in isolation. Cached
    per session so we don't re-probe information_schema on every price read."""
    cache = getattr(session, "_cf_product_coach_col", None)
    if cache is not None:
        return cache
    try:
        row = session.execute(
            text("SELECT 1 FROM information_schema.columns "
                 "WHERE table_schema='billing' AND table_name='product' "
                 "  AND column_name='coach_user_id'")
        ).first()
        present = row is not None
    except Exception:
        present = False
    try:
        session._cf_product_coach_col = present
    except Exception:
        pass
    return present

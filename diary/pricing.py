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


def price_for(session, *, club_id, kind=None, duration_minutes=None, product_id=None,
              audience="any", coach_user_id=None):
    """Best matching billing.price for a service + a chosen duration. Returns a dict
    {price_id, amount_minor, currency_code, unit, duration_minutes} or None (billing absent /
    no match). Never raises — pricing is best-effort here.

    Scope: by product_id if given, else by product kind (via billing.product). For a lesson,
    coach_user_id scopes to that coach's product when billing.product.coach_user_id exists.

    Duration resolution (when duration_minutes is given): EXACT match first, else the nearest
    priced duration <= requested, else any priced row. audience is honoured (exact over 'any')
    so legacy per-audience catalogues still resolve."""
    try:
        if not _billing_price_exists(session):
            return None
        params = {"c": club_id, "aud": audience}
        sql = ("SELECT p.id AS price_id, p.amount_minor, p.currency_code, p.unit, "
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
                where.append("pr.coach_user_id = :coach")
                params["coach"] = coach_user_id
        sql += "WHERE " + " AND ".join(where)
        # Ranking: exact duration first, then the nearest priced duration <= requested, then
        # any; tie-break to the exact audience, then cheapest. When no duration is requested we
        # fall back to the cheapest matching row (legacy callers).
        if duration_minutes is not None:
            params["dur"] = int(duration_minutes)
            sql += (" ORDER BY (p.duration_minutes = :dur) DESC, "
                    "(p.duration_minutes IS NOT NULL AND p.duration_minutes <= :dur) DESC, "
                    "p.duration_minutes DESC NULLS LAST, "
                    "(p.audience = :aud) DESC, p.amount_minor ASC LIMIT 1")
        else:
            sql += " ORDER BY (p.audience = :aud) DESC, p.amount_minor ASC LIMIT 1"
        row = session.execute(text(sql), params).mappings().first()
        if not row:
            return None
        return {
            "price_id": str(row["price_id"]),
            "amount_minor": row["amount_minor"],
            "currency_code": row["currency_code"],
            "unit": row["unit"],
            "duration_minutes": row["duration_minutes"],
        }
    except Exception:
        log.debug("price_for() suppressed (billing not ready)", exc_info=False)
        return None


def durations_for(session, *, club_id, kind, coach_user_id=None, audience="any"):
    """Every priced duration for a service (the frontend duration picker). Returns a list of
    {duration_minutes, amount_minor, price_id, currency_code} sorted by duration ascending.
    Only rows with a duration set are returned (per-duration pricing). Guarded -> [] if billing
    absent. For a lesson, coach_user_id scopes to that coach's product when available."""
    try:
        if not _billing_price_exists(session):
            return []
        params = {"c": club_id, "kind": kind, "aud": audience}
        where = ["p.club_id = :c", "p.active = true", "pr.active = true",
                 "pr.kind = :kind", "p.duration_minutes IS NOT NULL",
                 "p.audience IN (:aud, 'any')"]
        if coach_user_id is not None and _product_has_coach_col(session):
            where.append("pr.coach_user_id = :coach")
            params["coach"] = coach_user_id
        sql = ("SELECT DISTINCT ON (p.duration_minutes) p.duration_minutes, p.amount_minor, "
               "       p.id AS price_id, p.currency_code "
               "FROM billing.price p "
               "JOIN billing.product pr ON pr.id = p.product_id "
               "WHERE " + " AND ".join(where) +
               # DISTINCT ON keeps one row per duration: prefer exact audience, then cheapest.
               " ORDER BY p.duration_minutes ASC, (p.audience = :aud) DESC, p.amount_minor ASC")
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


def payment_modes_for(session, *, club_id, kind, coach_user_id=None):
    """The per-service payment preference (allowed settlement modes) for this service's product —
    a subset of the club-enabled methods, or None (= no per-service restriction, all club-enabled).
    `kind` is the PRODUCT kind (court_booking|lesson|class). Guarded -> None (never blocks booking)."""
    try:
        if not _billing_price_exists(session):
            return None
        where = ["club_id = :c", "kind = :kind", "active = true"]
        params = {"c": club_id, "kind": kind}
        if coach_user_id is not None and _product_has_coach_col(session):
            where.append("coach_user_id = :coach")
            params["coach"] = coach_user_id
        csv = session.execute(
            text("SELECT payment_modes FROM billing.product WHERE " + " AND ".join(where)
                 + " ORDER BY created_at LIMIT 1"),
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
        iso_dow = starts_at.isoweekday()                 # 1=Mon..7=Sun
        min_of_day = starts_at.hour * 60 + starts_at.minute
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

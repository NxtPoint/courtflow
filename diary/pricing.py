# diary/pricing.py — guarded audience-price lookup (billing.* is Agent C's lane).
#
# Availability and booking want to attach "the price for the caller's audience" to a
# slot (docs/03 §3 step 6). Prices live in billing.price (Agent C). This lane must run in
# isolation, so the lookup is GUARDED: if billing.price isn't present, we return None and
# callers carry price=None (the UI shows "price on request" / admin sets it later).
#
# billing.price shape we read (docs/02 §5): (club_id, product_id, audience, amount_minor,
# currency_code, unit, duration_minutes, active). audience ∈ member|visitor|guest|any.
# We never WRITE billing.* — only read, only if the table exists.

import logging

from sqlalchemy import text

log = logging.getLogger("diary.pricing")


def _billing_price_exists(session):
    row = session.execute(
        text("SELECT 1 FROM information_schema.tables "
             "WHERE table_schema = 'billing' AND table_name = 'price'")
    ).first()
    return row is not None


def price_for(session, *, club_id, audience, product_id=None, kind=None):
    """Best matching billing.price for (club, audience) — returns a dict
    {price_id, amount_minor, currency_code, unit} or None if billing absent / no match.

    Resolution: exact audience match first, then audience='any'. If product_id is given we
    scope to it; otherwise (availability preview) we match by the product's kind via
    billing.product when present. Never raises — pricing is best-effort here."""
    try:
        if not _billing_price_exists(session):
            return None
        params = {"c": club_id, "aud": audience}
        sql = ("SELECT p.id AS price_id, p.amount_minor, p.currency_code, p.unit, "
               "       p.audience "
               "FROM billing.price p ")
        where = ["p.club_id = :c", "p.active = true",
                 "p.audience IN (:aud, 'any')"]
        if product_id is not None:
            where.append("p.product_id = :pid")
            params["pid"] = product_id
        elif kind is not None:
            sql += "JOIN billing.product pr ON pr.id = p.product_id AND pr.active = true "
            where.append("pr.kind = :kind")
            params["kind"] = kind
        sql += "WHERE " + " AND ".join(where)
        # Prefer the exact audience over 'any', then cheapest.
        sql += " ORDER BY (p.audience = :aud) DESC, p.amount_minor ASC LIMIT 1"
        row = session.execute(text(sql), params).mappings().first()
        if not row:
            return None
        return {
            "price_id": str(row["price_id"]),
            "amount_minor": row["amount_minor"],
            "currency_code": row["currency_code"],
            "unit": row["unit"],
        }
    except Exception:
        log.debug("price_for() suppressed (billing not ready)", exc_info=False)
        return None

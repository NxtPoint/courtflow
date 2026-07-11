# diary/equipment.py — equipment hire (ball machine / racquets / balls) as a flat-fee add-on on a court
# booking. Equipment is a diary.resource(kind='equipment') with a `quantity`; it rides a court booking as
# order line(s) on the SAME order (no double bill) and is availability-checked by TIME (a single unit can't
# be hired twice for overlapping times), never holding a court of its own. Race-safe on the equipment row
# (the class-capacity FOR UPDATE pattern). Guarded reads; the reserve path RAISES so the booking rolls back.

import logging

from sqlalchemy import text

log = logging.getLogger("diary.equipment")


class EquipmentUnavailable(Exception):
    """Raised inside the booking savepoint when an equipment item can't fit its requested qty for the
    time — so the whole booking rolls back cleanly (mirrors the SLOT_TAKEN path)."""


def list_equipment(session, *, club_id, active_only=True, featured_only=False):
    """The club's equipment items (for the booking add-on picker + the Setup editor). Each =
    {id, name, quantity, feature_on_home, active, price_id, amount_minor, currency_code}. Guarded -> []."""
    try:
        where = ["r.club_id = :c", "r.kind = 'equipment'"]
        if active_only:
            where.append("r.is_active = true")
        if featured_only:
            where.append("r.feature_on_home = true")
        rows = session.execute(
            text("SELECT r.id, r.name, r.quantity, r.feature_on_home, r.is_active, r.product_id "
                 "FROM diary.resource r WHERE " + " AND ".join(where) + " ORDER BY r.rank, r.name"),
            {"c": str(club_id)},
        ).mappings().all()
        out = []
        for r in rows:
            price = _flat_price(session, club_id=club_id, product_id=r["product_id"])
            out.append({
                "id": str(r["id"]), "name": r["name"], "quantity": int(r["quantity"] or 1),
                "feature_on_home": bool(r["feature_on_home"]), "active": bool(r["is_active"]),
                "price_id": (price["price_id"] if price else None),
                "amount_minor": (price["amount_minor"] if price else None),
                "currency_code": (price["currency_code"] if price else None),
            })
        return out
    except Exception:
        log.debug("list_equipment suppressed", exc_info=False)
        return []


def _flat_price(session, *, club_id, product_id):
    """The equipment item's flat fee (its product's cheapest active price). Returns {price_id,
    amount_minor, currency_code} or None. Guarded -> None."""
    if not product_id:
        return None
    try:
        row = session.execute(
            text("SELECT id AS price_id, amount_minor, currency_code FROM billing.price "
                 "WHERE club_id = :c AND product_id = :p AND active = true "
                 "ORDER BY amount_minor ASC LIMIT 1"),
            {"c": str(club_id), "p": str(product_id)},
        ).mappings().first()
        return dict(row) if row else None
    except Exception:
        return None


def available_units(session, *, club_id, resource_id, starts, ends, exclude_booking_id=None):
    """How many units of an equipment item are free for [starts, ends): quantity − units already out across
    OVERLAPPING held/confirmed bookings (pure TIME overlap, court-agnostic). Guarded -> 0 (a bad read must
    never over-allocate). Returns 0 if the resource isn't an active equipment item."""
    try:
        qty = session.execute(
            text("SELECT quantity FROM diary.resource "
                 "WHERE club_id = :c AND id = :r AND kind = 'equipment' AND is_active = true"),
            {"c": str(club_id), "r": str(resource_id)},
        ).scalar()
        if qty is None:
            return 0
        params = {"c": str(club_id), "r": str(resource_id), "ds": starts, "de": ends}
        ex = ""
        if exclude_booking_id:
            ex = "AND be.booking_id <> :ex "
            params["ex"] = str(exclude_booking_id)
        out = session.execute(
            text("SELECT COALESCE(SUM(be.qty), 0) FROM diary.booking_equipment be "
                 "JOIN diary.booking b ON b.id = be.booking_id "
                 "WHERE be.club_id = :c AND be.resource_id = :r AND b.status IN ('held','confirmed') "
                 "  AND b.starts_at < :de AND b.ends_at > :ds " + ex),
            params,
        ).scalar()
        return int(qty) - int(out or 0)
    except Exception:
        log.debug("available_units suppressed", exc_info=False)
        return 0


def reserve_equipment(session, *, club_id, booking_id, addons, starts, ends):
    """Lock each requested equipment item (FOR UPDATE), re-check availability for the time, insert a
    diary.booking_equipment row, and RETURN the billing line dicts (to append to the booking's order).
    RAISES EquipmentUnavailable if any item can't fit its qty — called INSIDE the booking savepoint so a
    failure rolls the whole booking back (nothing persists). addons = [{resource_id, qty}]."""
    lines = []
    for a in (addons or []):
        rid = a.get("resource_id")
        qty = int(a.get("qty") or 1)
        if not rid or qty < 1:
            continue
        # Lock the item so two concurrent hires of the last unit can't both succeed (class-capacity pattern).
        row = session.execute(
            text("SELECT id, name, product_id FROM diary.resource "
                 "WHERE club_id = :c AND id = :r AND kind = 'equipment' AND is_active = true FOR UPDATE"),
            {"c": str(club_id), "r": str(rid)},
        ).mappings().first()
        if not row:
            raise EquipmentUnavailable("that equipment isn't available")
        avail = available_units(session, club_id=club_id, resource_id=rid, starts=starts, ends=ends)
        if avail < qty:
            raise EquipmentUnavailable(f"{row['name'] or 'equipment'} isn't available for that time")
        price = _flat_price(session, club_id=club_id, product_id=row["product_id"])
        price_id = price["price_id"] if price else None
        amt = int(price["amount_minor"]) if price and price["amount_minor"] is not None else 0
        session.execute(
            text("INSERT INTO diary.booking_equipment "
                 "(club_id, booking_id, resource_id, qty, price_id, amount_minor) "
                 "VALUES (:c, :b, :r, :q, :p, :a)"),
            {"c": str(club_id), "b": str(booking_id), "r": str(rid), "q": qty,
             "p": price_id, "a": amt},
        )
        # An equipment line carries NO booking_id (it's a standalone fee, not a booking line) so
        # reprice/commission readers that key off order_line.booking_id ignore it. It rides the booking's
        # order and is voided with it on cancel.
        lines.append({"description": (row["name"] or "Equipment"), "price_id": price_id,
                      "qty": qty, "amount_minor": amt})
    return lines

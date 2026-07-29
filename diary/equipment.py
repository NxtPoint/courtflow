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


def services_for(session, *, club_id, resource_id):
    """The court SERVICES an equipment item is offered on (billing.product ids, as str). EMPTY = every
    court service — the default, and what every pre-existing item has. Guarded -> []."""
    try:
        return [str(r) for r in session.execute(
            text("SELECT product_id FROM diary.equipment_service "
                 "WHERE club_id = :c AND resource_id = :r"),
            {"c": str(club_id), "r": str(resource_id)},
        ).scalars().all()]
    except Exception:
        return []


def set_services(session, *, club_id, resource_id, product_ids):
    """Replace the court services an item is offered on. product_ids empty/None -> offered on ALL
    (the row set is simply cleared). Caller composes the transaction."""
    session.execute(
        text("DELETE FROM diary.equipment_service WHERE club_id = :c AND resource_id = :r"),
        {"c": str(club_id), "r": str(resource_id)},
    )
    for pid in (product_ids or []):
        if not pid:
            continue
        session.execute(
            text("INSERT INTO diary.equipment_service (club_id, resource_id, product_id) "
                 "VALUES (:c, :r, CAST(:p AS uuid)) ON CONFLICT DO NOTHING"),
            {"c": str(club_id), "r": str(resource_id), "p": str(pid)},
        )


def offered_on_service(session, *, club_id, resource_id, court_product_id):
    """Is this item offered on that court service? True when the item has NO service links (offered
    everywhere — the default) or one of them matches. Guarded -> True (a bad read must never make an
    item that IS offered look unavailable)."""
    try:
        links = services_for(session, club_id=club_id, resource_id=resource_id)
        if not links:
            return True
        return court_product_id is not None and str(court_product_id) in links
    except Exception:
        return True


def list_equipment(session, *, club_id, active_only=True, featured_only=False,
                   court_product_id=None, starts=None, ends=None):
    """The club's equipment items (for the booking add-on picker + the Setup editor). Each =
    {id, name, quantity, feature_on_home, active, price_id, amount_minor, currency_code,
     payment_modes, services[], available?}.

    `court_product_id` filters to the items offered on THAT court service (an item with no links is
    offered on all — see equipment_service). `starts`/`ends` add `available` = the units actually free
    for that window, so the picker can clamp to what can really be hired rather than to what the club
    owns. Guarded -> []."""
    try:
        where = ["r.club_id = :c", "r.kind = 'equipment'"]
        if active_only:
            where.append("r.is_active = true")
        if featured_only:
            where.append("r.feature_on_home = true")
        rows = session.execute(
            text("SELECT r.id, r.name, r.quantity, r.feature_on_home, r.is_active, r.product_id, "
                 "       p.payment_modes "
                 "FROM diary.resource r "
                 "LEFT JOIN billing.product p ON p.id = r.product_id "
                 "WHERE " + " AND ".join(where) + " ORDER BY r.rank, r.name"),
            {"c": str(club_id)},
        ).mappings().all()
        out = []
        for r in rows:
            svc = services_for(session, club_id=club_id, resource_id=r["id"])
            # Offered on the chosen court service? No links = offered everywhere (the default).
            if court_product_id is not None and svc and str(court_product_id) not in svc:
                continue
            price = _flat_price(session, club_id=club_id, product_id=r["product_id"])
            modes = r["payment_modes"]
            item = {
                "id": str(r["id"]), "name": r["name"], "quantity": int(r["quantity"] or 1),
                "feature_on_home": bool(r["feature_on_home"]), "active": bool(r["is_active"]),
                "price_id": (price["price_id"] if price else None),
                "amount_minor": (price["amount_minor"] if price else None),
                "currency_code": (price["currency_code"] if price else None),
                # None = inherit every club-enabled method (the booking flow narrows by this).
                "payment_modes": ([m.strip() for m in str(modes).split(",") if m.strip()]
                                  if modes else None),
                "services": svc,          # [] = every court service
            }
            if starts is not None and ends is not None:
                # What can ACTUALLY be hired for this slot. The picker used to clamp to what the club
                # OWNS, so a client could select 4 racquets that were already out and only find out at
                # confirm (the server refuses, correctly — but at the worst possible moment).
                item["available"] = available_units(session, club_id=club_id, resource_id=r["id"],
                                                    starts=starts, ends=ends)
            out.append(item)
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


def check_offered(session, *, club_id, addons, court_product_id):
    """The NAME of the first requested item not offered on this court service, else None.

    The picker filters by service, but the picker is not the authority — `addons` arrives off the
    request body. Without this a crafted (or stale) request could hire clay-only kit on a hard court,
    which is the same class of hole the posted-product_id guard closes for services. Guarded -> None
    (a bad read must never block a legitimate hire)."""
    try:
        for a in (addons or []):
            rid = a.get("resource_id")
            if not rid or int(a.get("qty") or 1) < 1:
                continue
            if not offered_on_service(session, club_id=club_id, resource_id=rid,
                                      court_product_id=court_product_id):
                return session.execute(
                    text("SELECT name FROM diary.resource WHERE id = :r"), {"r": str(rid)},
                ).scalar() or "that equipment"
        return None
    except Exception:
        log.debug("check_offered suppressed", exc_info=False)
        return None


def quote(session, *, club_id, addons):
    """Price the requested equipment and resolve what it may be PAID with, BEFORE anything is
    inserted — returns {total_minor, modes} where `modes` is the intersection of every requested
    item's own `billing.product.payment_modes` (None = that item places no restriction, so it
    doesn't narrow the set) and an empty list means the items disagree irreconcilably.

    create_booking has to decide `held` vs `confirmed` before the order exists, so it needs the
    equipment's price and payment rules up front; reserve_equipment below stays the authority that
    actually locks stock and writes the lines. Guarded -> {0, None} (never blocks a booking)."""
    total = 0
    modes = None
    try:
        for a in (addons or []):
            rid = a.get("resource_id")
            qty = int(a.get("qty") or 1)
            if not rid or qty < 1:
                continue
            row = session.execute(
                text("SELECT product_id FROM diary.resource "
                     "WHERE club_id = :c AND id = :r AND kind = 'equipment' AND is_active = true"),
                {"c": str(club_id), "r": str(rid)},
            ).mappings().first()
            if not row:
                continue
            price = _flat_price(session, club_id=club_id, product_id=row["product_id"])
            if price and price["amount_minor"] is not None:
                total += int(price["amount_minor"]) * qty
            from diary.pricing import payment_modes_for
            pm = payment_modes_for(session, club_id=club_id, kind="equipment",
                                   product_id=row["product_id"])
            if pm is None:
                continue                      # unrestricted item — narrows nothing
            modes = list(pm) if modes is None else [m for m in modes if m in pm]
        return {"total_minor": total, "modes": modes}
    except Exception:
        log.debug("equipment quote suppressed", exc_info=False)
        return {"total_minor": 0, "modes": None}


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

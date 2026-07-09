# services/repositories.py — read the full config of a SERVICE in one place.
#
# get_service() composes EVERYTHING that makes a service work (variations, payment preference,
# packages, commission, and the club's enabled payment methods) so the unified editor renders + edits
# from a single payload. Writes live in the routes (delegating to the existing billing/admin repos),
# so this lane never duplicates the price/bundle/commission logic — it just brings it together.

from sqlalchemy import text

# billing.product.kind  <->  the service kind the rest of the system speaks.
_KIND_TO_SERVICE = {"court_booking": "court", "lesson": "lesson", "class": "class"}
_MANAGEABLE_KINDS = ("court_booking", "lesson", "class")
ALL_MODES = ("online", "at_court", "monthly_account")


def _modes_list(csv):
    if not csv:
        return None
    out = [m.strip() for m in str(csv).split(",") if m.strip() in ALL_MODES]
    return out or None


def club_payment_methods(session, *, club_id):
    """The methods the CLUB has enabled (the global set a service can offer a subset of)."""
    row = session.execute(
        text("SELECT allow_online_payment, allow_pay_at_court, allow_monthly_account "
             "FROM club.policy WHERE club_id = :c"), {"c": club_id}).mappings().first()
    row = row or {}
    out = []
    if row.get("allow_online_payment"):
        out.append("online")
    if row.get("allow_pay_at_court", True):
        out.append("at_court")
    if row.get("allow_monthly_account", True):
        out.append("monthly_account")
    return out


def list_services(session, *, club_id, role, user_id):
    """Services the caller may manage. Owner → all; coach → their OWN lesson/class services."""
    # Include INACTIVE (hidden) services too — admin shows them greyed with an Unhide.
    where = ["p.club_id = :c", "p.kind IN ('court_booking','lesson','class')"]
    params = {"c": club_id}
    if role not in ("club_admin", "platform_admin"):
        where.append("p.coach_user_id = :u")
        params["u"] = str(user_id)
    rows = session.execute(
        text("SELECT p.id, p.kind, p.name, p.coach_user_id, p.active, p.status, "
             "       COALESCE(cp.display_name, NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.surname)), ''), u.email) AS coach_name, "
             "       (SELECT count(*) FROM billing.price pr WHERE pr.product_id = p.id "
             "          AND pr.term_months IS NULL AND pr.active = true) AS variation_count, "
             "       (SELECT min(pr.amount_minor) FROM billing.price pr WHERE pr.product_id = p.id "
             "          AND pr.term_months IS NULL AND pr.active = true) AS from_amount_minor, "
             "       (SELECT json_agg(json_build_object('duration_minutes', pr.duration_minutes, "
             "                  'amount_minor', pr.amount_minor) ORDER BY pr.duration_minutes NULLS FIRST, pr.amount_minor) "
             "          FROM billing.price pr WHERE pr.product_id = p.id "
             "          AND pr.term_months IS NULL AND pr.active = true) AS variations "
             "FROM billing.product p "
             "LEFT JOIN iam.coach_profile cp ON cp.club_id = p.club_id AND cp.user_id = p.coach_user_id "
             "LEFT JOIN iam.user u ON u.id = p.coach_user_id "
             "WHERE " + " AND ".join(where) + " ORDER BY p.kind, p.name"),
        params,
    ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["status"] = d.get("status") or ("active" if d["active"] else "deactivated")
        d["active"] = bool(d["active"])
        d["service_kind"] = _KIND_TO_SERVICE.get(d["kind"], d["kind"])
        d["variations"] = d.get("variations") or []
        if d.get("coach_user_id") is not None:
            d["coach_user_id"] = str(d["coach_user_id"])
        out.append(d)
    return out


_STATUSES = ("active", "deactivated", "terminated")


def set_service_status(session, *, club_id, product_id, status):
    """Service lifecycle: active | deactivated | terminated. Keeps billing.product.active in sync
    (active only when status='active') so customer reads (durations/pricing) drop deactivated +
    terminated automatically. Returns True on update."""
    if status not in _STATUSES:
        return False
    res = session.execute(
        text("UPDATE billing.product SET status = :s, active = :a, updated_at = now() "
             "WHERE club_id = :c AND id = :p"),
        {"s": status, "a": (status == "active"), "c": club_id, "p": str(product_id)},
    )
    return (res.rowcount or 0) > 0


def get_service(session, *, club_id, product_id):
    """The full service config (or None). One payload: identity · variations · payment · packages ·
    commission · the club's enabled methods (for the payment picker)."""
    prod = session.execute(
        text("SELECT id, kind, name, description, coach_user_id, payment_modes, active, status "
             "FROM billing.product WHERE club_id = :c AND id = :id"),
        {"c": club_id, "id": str(product_id)},
    ).mappings().first()
    if not prod:
        return None
    kind = prod["kind"]
    service_kind = _KIND_TO_SERVICE.get(kind, kind)
    currency = session.execute(
        text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": club_id}).scalar() or "ZAR"

    # variations — per-duration prices (the membership term-plans are excluded).
    variations = [
        {"price_id": str(r["id"]), "duration_minutes": r["duration_minutes"],
         "amount_minor": int(r["amount_minor"] or 0), "status": r["status"]}
        for r in session.execute(
            text("SELECT id, duration_minutes, amount_minor, status FROM billing.price "
                 "WHERE club_id = :c AND product_id = :p AND term_months IS NULL "
                 "ORDER BY duration_minutes NULLS FIRST, amount_minor"),
            {"c": club_id, "p": str(product_id)},
        ).mappings().all()
    ]

    # packages — bundle_plans tied to THIS specific service (product_id), PLUS any legacy unscoped
    # pack (product_id NULL) matching by kind+coach until a backfill scopes it. So two services under
    # one coach no longer show each other's packs — each shows only its own (+ shared legacy ones).
    pkg_rows = session.execute(
        text("SELECT id, label, sessions_count, duration_minutes, price_minor, validity_days, status, "
             "       coach_user_id, product_id "
             "FROM billing.bundle_plan WHERE club_id = :c "
             "  AND (product_id = :pid "
             "       OR (product_id IS NULL AND service_kind = :sk "
             "           AND (coach_user_id IS NULL OR coach_user_id = :coach))) "
             "ORDER BY sessions_count"),
        {"c": club_id, "pid": str(product_id), "sk": service_kind, "coach": prod["coach_user_id"]},
    ).mappings().all()
    packages = [{"id": str(r["id"]), "label": r["label"], "sessions_count": r["sessions_count"],
                 "duration_minutes": r["duration_minutes"], "price_minor": int(r["price_minor"] or 0),
                 "validity_days": r["validity_days"], "status": r["status"]} for r in pkg_rows]

    # commission — meaningful for lessons/classes (court has none).
    commission = {"applies": kind in ("lesson", "class"), "club_default_pct": 0.0, "effective_pct": 0.0}
    if commission["applies"]:
        try:
            from billing.commission import resolve_commission_pct
            commission["club_default_pct"] = float(resolve_commission_pct(session, club_id=club_id))
            commission["effective_pct"] = float(resolve_commission_pct(
                session, club_id=club_id, product_id=prod["id"], coach_user_id=prod["coach_user_id"]))
        except Exception:
            pass

    return {
        "id": str(prod["id"]), "kind": kind, "service_kind": service_kind,
        "status": (prod["status"] or ("active" if prod["active"] else "deactivated")),
        "name": prod["name"], "description": prod["description"],
        "coach_user_id": str(prod["coach_user_id"]) if prod["coach_user_id"] else None,
        "currency": currency,
        "payment_modes": _modes_list(prod["payment_modes"]),          # None = all club-enabled
        "club_payment_methods": club_payment_methods(session, club_id=club_id),
        "variations": variations,
        "packages": packages,
        "commission": commission,
    }


def set_payment_modes(session, *, club_id, product_id, modes):
    """Persist the per-service payment preference (a subset of the enabled modes, or None = all)."""
    if modes is None:
        csv = None
    else:
        clean = [m for m in modes if m in ALL_MODES]
        csv = ",".join(clean) if clean else None
    session.execute(
        text("UPDATE billing.product SET payment_modes = :m, updated_at = now() "
             "WHERE club_id = :c AND id = :p"),
        {"m": csv, "c": club_id, "p": str(product_id)},
    )
    return True

# admin/repositories.py — plain-SQL helpers for the admin write APIs.
#
# Same discipline as iam/repositories.py + scripts/provision_club.py: SQLAlchemy Core
# text(), every fn takes an explicit `session` and NEVER commits (callers compose via
# db.session_scope()). club_id is ALWAYS passed in (resolved from the principal — never the
# request body) and scopes every query (multi-tenant, club_id NOT NULL).
#
# Helpers here back: onboarding read/complete, club/location/branding/policy read+write,
# resources CRUD, hours (availability_rule) replace, products+prices CRUD, coaches +
# coach_invite. Idempotent where the contract says so (location upsert, hours replace).

from sqlalchemy import text


# ---------------------------------------------------------------------------
# serialization helpers
# ---------------------------------------------------------------------------

def _row(row):
    """Map a Row -> dict, stringifying uuid/uuid-ish fields and isoformatting datetimes."""
    if row is None:
        return None
    d = dict(row)
    for k, v in list(d.items()):
        if v is None:
            continue
        # uuid columns -> str (any column ending in _id, or named id)
        if (k == "id" or k.endswith("_id")) and not isinstance(v, (str, int)):
            d[k] = str(v)
        elif hasattr(v, "isoformat") and not isinstance(v, str):
            # date / time / datetime
            d[k] = v.isoformat()
    return d


def _rows(rows):
    return [_row(r) for r in rows]


# ---------------------------------------------------------------------------
# club / location / branding / policy
# ---------------------------------------------------------------------------

def get_club(session, *, club_id):
    return _row(session.execute(
        text("SELECT id, slug, name, legal_name, status, currency_code, timezone, locale, "
             "       onboarding_completed, created_at, updated_at "
             "FROM club.club WHERE id = :c"),
        {"c": club_id},
    ).mappings().first())


def patch_club(session, *, club_id, name=None, legal_name=None, currency_code=None,
               timezone=None, locale=None):
    """COALESCE-style partial update — only the supplied fields change."""
    session.execute(
        text("""
            UPDATE club.club SET
                name          = COALESCE(:name, name),
                legal_name    = COALESCE(:legal_name, legal_name),
                currency_code = COALESCE(:currency_code, currency_code),
                timezone      = COALESCE(:timezone, timezone),
                locale        = COALESCE(:locale, locale),
                updated_at    = now()
            WHERE id = :c
        """),
        {"c": club_id, "name": name, "legal_name": legal_name,
         "currency_code": currency_code, "timezone": timezone, "locale": locale},
    )
    return get_club(session, club_id=club_id)


def get_primary_location(session, *, club_id):
    """The club's primary location = the oldest location row for the club."""
    return _row(session.execute(
        text("SELECT id, club_id, name, address_line, city, postal_code, country, "
             "       lat, lng, phone, email, created_at, updated_at "
             "FROM club.location WHERE club_id = :c ORDER BY created_at LIMIT 1"),
        {"c": club_id},
    ).mappings().first())


def upsert_primary_location(session, *, club_id, name=None, address_line=None, city=None,
                            postal_code=None, country=None, phone=None, email=None,
                            lat=None, lng=None):
    """Upsert the club's PRIMARY (oldest) location. If none exists, insert one. Idempotent."""
    existing = session.execute(
        text("SELECT id FROM club.location WHERE club_id = :c ORDER BY created_at LIMIT 1"),
        {"c": club_id},
    ).mappings().first()
    params = {"c": club_id, "name": name, "address_line": address_line, "city": city,
              "postal_code": postal_code, "country": country, "phone": phone,
              "email": email, "lat": lat, "lng": lng}
    if existing:
        session.execute(
            text("UPDATE club.location SET name=:name, address_line=:address_line, "
                 "city=:city, postal_code=:postal_code, country=:country, phone=:phone, "
                 "email=:email, lat=:lat, lng=:lng, updated_at=now() WHERE id=:id"),
            {**params, "id": existing["id"]},
        )
    else:
        session.execute(
            text("INSERT INTO club.location (club_id, name, address_line, city, postal_code, "
                 "country, phone, email, lat, lng) "
                 "VALUES (:c, :name, :address_line, :city, :postal_code, :country, :phone, "
                 ":email, :lat, :lng)"),
            params,
        )
    return get_primary_location(session, club_id=club_id)


def get_branding(session, *, club_id):
    return _row(session.execute(
        text("SELECT club_id, primary_color, accent_color, logo_url, favicon_url, domain, "
             "       marketing_hosts, og_image_url, klaviyo_list_id, created_at, updated_at "
             "FROM club.branding WHERE club_id = :c"),
        {"c": club_id},
    ).mappings().first())


def patch_branding(session, *, club_id, primary_color=None, accent_color=None, logo_url=None,
                   favicon_url=None, og_image_url=None):
    """Upsert-then-partial-update branding (1 row per club). Inserts a row if absent so a
    club with no branding row yet can still set colours during onboarding."""
    session.execute(
        text("INSERT INTO club.branding (club_id) VALUES (:c) ON CONFLICT (club_id) DO NOTHING"),
        {"c": club_id},
    )
    session.execute(
        text("""
            UPDATE club.branding SET
                primary_color = COALESCE(:primary_color, primary_color),
                accent_color  = COALESCE(:accent_color, accent_color),
                logo_url      = COALESCE(:logo_url, logo_url),
                favicon_url   = COALESCE(:favicon_url, favicon_url),
                og_image_url  = COALESCE(:og_image_url, og_image_url),
                updated_at    = now()
            WHERE club_id = :c
        """),
        {"c": club_id, "primary_color": primary_color, "accent_color": accent_color,
         "logo_url": logo_url, "favicon_url": favicon_url, "og_image_url": og_image_url},
    )
    return get_branding(session, club_id=club_id)


def get_policy(session, *, club_id):
    return _row(session.execute(
        text("SELECT club_id, booking_window_days, min_booking_minutes, "
             "       cancellation_cutoff_hours, no_show_fee_minor, guest_requires_member, "
             "       allow_pay_at_court, allow_monthly_account, allow_online_payment, "
             "       created_at, updated_at "
             "FROM club.policy WHERE club_id = :c"),
        {"c": club_id},
    ).mappings().first())


def patch_policy(session, *, club_id, booking_window_days=None, min_booking_minutes=None,
                 cancellation_cutoff_hours=None, no_show_fee_minor=None,
                 guest_requires_member=None, allow_pay_at_court=None,
                 allow_monthly_account=None, allow_online_payment=None):
    """Upsert-then-partial-update policy (1 row per club). Inserts a defaults row if absent."""
    session.execute(
        text("INSERT INTO club.policy (club_id) VALUES (:c) ON CONFLICT (club_id) DO NOTHING"),
        {"c": club_id},
    )
    session.execute(
        text("""
            UPDATE club.policy SET
                booking_window_days       = COALESCE(:booking_window_days, booking_window_days),
                min_booking_minutes       = COALESCE(:min_booking_minutes, min_booking_minutes),
                cancellation_cutoff_hours = COALESCE(:cancellation_cutoff_hours, cancellation_cutoff_hours),
                no_show_fee_minor         = COALESCE(:no_show_fee_minor, no_show_fee_minor),
                guest_requires_member     = COALESCE(:guest_requires_member, guest_requires_member),
                allow_pay_at_court        = COALESCE(:allow_pay_at_court, allow_pay_at_court),
                allow_monthly_account     = COALESCE(:allow_monthly_account, allow_monthly_account),
                allow_online_payment      = COALESCE(:allow_online_payment, allow_online_payment),
                updated_at                = now()
            WHERE club_id = :c
        """),
        {"c": club_id, "booking_window_days": booking_window_days,
         "min_booking_minutes": min_booking_minutes,
         "cancellation_cutoff_hours": cancellation_cutoff_hours,
         "no_show_fee_minor": no_show_fee_minor,
         "guest_requires_member": guest_requires_member,
         "allow_pay_at_court": allow_pay_at_court,
         "allow_monthly_account": allow_monthly_account,
         "allow_online_payment": allow_online_payment},
    )
    return get_policy(session, club_id=club_id)


def set_onboarding_completed(session, *, club_id, completed=True):
    session.execute(
        text("UPDATE club.club SET onboarding_completed = :v, updated_at = now() WHERE id = :c"),
        {"c": club_id, "v": completed},
    )


# ---------------------------------------------------------------------------
# resources (courts / coaches / classes)
# ---------------------------------------------------------------------------

def list_resources(session, *, club_id, include_inactive=True):
    where = "WHERE club_id = :c" if include_inactive else "WHERE club_id = :c AND is_active = true"
    return _rows(session.execute(
        text("SELECT id, club_id, location_id, kind, name, surface, coach_user_id, capacity, "
             "       is_active, rank, created_at, updated_at "
             f"FROM diary.resource {where} ORDER BY kind, rank, name"),
        {"c": club_id},
    ).mappings().all())


def get_resource(session, *, club_id, resource_id):
    return _row(session.execute(
        text("SELECT id, club_id, location_id, kind, name, surface, coach_user_id, capacity, "
             "       is_active, rank, created_at, updated_at "
             "FROM diary.resource WHERE club_id = :c AND id = :r"),
        {"c": club_id, "r": resource_id},
    ).mappings().first())


def create_resource(session, *, club_id, kind, name=None, surface=None, capacity=None,
                    coach_user_id=None, rank=None):
    row = session.execute(
        text("INSERT INTO diary.resource (club_id, kind, name, surface, capacity, "
             "coach_user_id, rank) "
             "VALUES (:c, :kind, :name, :surface, COALESCE(:capacity, 1), :coach, "
             "COALESCE(:rank, 0)) RETURNING id"),
        {"c": club_id, "kind": kind, "name": name, "surface": surface,
         "capacity": capacity, "coach": coach_user_id, "rank": rank},
    ).mappings().first()
    return get_resource(session, club_id=club_id, resource_id=row["id"])


def patch_resource(session, *, club_id, resource_id, name=None, surface=None, is_active=None,
                   rank=None, capacity=None):
    res = session.execute(
        text("""
            UPDATE diary.resource SET
                name      = COALESCE(:name, name),
                surface   = COALESCE(:surface, surface),
                is_active = COALESCE(:is_active, is_active),
                rank      = COALESCE(:rank, rank),
                capacity  = COALESCE(:capacity, capacity),
                updated_at = now()
            WHERE club_id = :c AND id = :r
            RETURNING id
        """),
        {"c": club_id, "r": resource_id, "name": name, "surface": surface,
         "is_active": is_active, "rank": rank, "capacity": capacity},
    ).mappings().first()
    if not res:
        return None
    return get_resource(session, club_id=club_id, resource_id=resource_id)


def soft_delete_resource(session, *, club_id, resource_id):
    res = session.execute(
        text("UPDATE diary.resource SET is_active = false, updated_at = now() "
             "WHERE club_id = :c AND id = :r RETURNING id"),
        {"c": club_id, "r": resource_id},
    ).mappings().first()
    return res is not None


def court_resource_ids(session, *, club_id, active_only=True):
    where = "AND is_active = true" if active_only else ""
    return [str(r) for r in session.execute(
        text(f"SELECT id FROM diary.resource WHERE club_id = :c AND kind = 'court' {where} "
             "ORDER BY rank, name"),
        {"c": club_id},
    ).scalars().all()]


# ---------------------------------------------------------------------------
# hours (availability_rule) — REPLACE semantics per (resource, weekday)
# ---------------------------------------------------------------------------

def list_hours(session, *, club_id, resource_id=None):
    """Return availability_rule rows for one resource, or (resource_id omitted) the rules of
    the courts. When omitted we return the courts' rules (the 'common' hours)."""
    if resource_id:
        rows = session.execute(
            text("SELECT id, club_id, resource_id, weekday, start_time, end_time, slot_minutes "
                 "FROM diary.availability_rule WHERE club_id = :c AND resource_id = :r "
                 "ORDER BY resource_id, weekday, start_time"),
            {"c": club_id, "r": resource_id},
        ).mappings().all()
    else:
        rows = session.execute(
            text("SELECT ar.id, ar.club_id, ar.resource_id, ar.weekday, ar.start_time, "
                 "       ar.end_time, ar.slot_minutes "
                 "FROM diary.availability_rule ar "
                 "JOIN diary.resource r ON r.id = ar.resource_id "
                 "WHERE ar.club_id = :c AND r.kind = 'court' "
                 "ORDER BY ar.resource_id, ar.weekday, ar.start_time"),
            {"c": club_id},
        ).mappings().all()
    return _rows(rows)


def replace_hours(session, *, club_id, resource_ids, week):
    """REPLACE availability_rule rows for the given resources: for each (resource, weekday)
    in `week`, delete existing rules then insert the OPEN ones. Idempotent — re-running with
    the same `week` yields the same rows. `week` is a list of dicts:
        {weekday:int(0-6), open:bool, start_time:'HH:MM', end_time:'HH:MM', slot_minutes:int}
    Returns the count of inserted rule rows."""
    inserted = 0
    weekdays = sorted({int(d["weekday"]) for d in week})
    for rid in resource_ids:
        # Clear every weekday we are replacing for this resource (so 'closed' days end up empty).
        for wd in weekdays:
            session.execute(
                text("DELETE FROM diary.availability_rule "
                     "WHERE club_id = :c AND resource_id = :r AND weekday = :w"),
                {"c": club_id, "r": rid, "w": wd},
            )
        for d in week:
            if not d.get("open"):
                continue
            session.execute(
                text("INSERT INTO diary.availability_rule "
                     "(club_id, resource_id, weekday, start_time, end_time, slot_minutes) "
                     "VALUES (:c, :r, :w, :st, :et, :sm)"),
                {"c": club_id, "r": rid, "w": int(d["weekday"]),
                 "st": d.get("start_time"), "et": d.get("end_time"),
                 "sm": int(d.get("slot_minutes") or 60)},
            )
            inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# products + prices
# ---------------------------------------------------------------------------

def list_products(session, *, club_id):
    products = session.execute(
        text("SELECT id, club_id, kind, name, description, active, created_at, updated_at "
             "FROM billing.product WHERE club_id = :c ORDER BY kind, name"),
        {"c": club_id},
    ).mappings().all()
    out = []
    for p in products:
        prices = session.execute(
            text("SELECT id, club_id, product_id, audience, amount_minor, currency_code, unit, "
                 "       duration_minutes, active, created_at, updated_at "
                 "FROM billing.price WHERE club_id = :c AND product_id = :p "
                 "ORDER BY audience, unit"),
            {"c": club_id, "p": p["id"]},
        ).mappings().all()
        d = _row(p)
        d["prices"] = _rows(prices)
        out.append(d)
    return out


def get_product(session, *, club_id, product_id):
    p = session.execute(
        text("SELECT id, club_id, kind, name, description, active, created_at, updated_at "
             "FROM billing.product WHERE club_id = :c AND id = :p"),
        {"c": club_id, "p": product_id},
    ).mappings().first()
    if not p:
        return None
    prices = session.execute(
        text("SELECT id, club_id, product_id, audience, amount_minor, currency_code, unit, "
             "       duration_minutes, active, created_at, updated_at "
             "FROM billing.price WHERE club_id = :c AND product_id = :p ORDER BY audience, unit"),
        {"c": club_id, "p": product_id},
    ).mappings().all()
    d = _row(p)
    d["prices"] = _rows(prices)
    return d


def _club_currency(session, *, club_id):
    cur = session.execute(
        text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": club_id},
    ).scalar()
    return cur or "ZAR"


def create_product(session, *, club_id, kind, name=None, description=None, prices=None):
    pid = session.execute(
        text("INSERT INTO billing.product (club_id, kind, name, description, active) "
             "VALUES (:c, :k, :n, :d, true) RETURNING id"),
        {"c": club_id, "k": kind, "n": name, "d": description},
    ).scalar_one()
    for pr in (prices or []):
        create_price(session, club_id=club_id, product_id=pid,
                     audience=pr.get("audience", "any"),
                     amount_minor=pr.get("amount_minor", 0),
                     unit=pr.get("unit", "per_booking"),
                     duration_minutes=pr.get("duration_minutes"))
    return get_product(session, club_id=club_id, product_id=pid)


def patch_product(session, *, club_id, product_id, kind=None, name=None, description=None,
                  active=None):
    res = session.execute(
        text("""
            UPDATE billing.product SET
                kind        = COALESCE(:kind, kind),
                name        = COALESCE(:name, name),
                description = COALESCE(:description, description),
                active      = COALESCE(:active, active),
                updated_at  = now()
            WHERE club_id = :c AND id = :p
            RETURNING id
        """),
        {"c": club_id, "p": product_id, "kind": kind, "name": name,
         "description": description, "active": active},
    ).mappings().first()
    if not res:
        return None
    return get_product(session, club_id=club_id, product_id=product_id)


def _get_price(session, *, club_id, price_id):
    return _row(session.execute(
        text("SELECT id, club_id, product_id, audience, amount_minor, currency_code, unit, "
             "       duration_minutes, active, status, created_at, updated_at "
             "FROM billing.price WHERE club_id = :c AND id = :p"),
        {"c": club_id, "p": price_id},
    ).mappings().first())


def create_price(session, *, club_id, product_id, audience="any", amount_minor=0,
                 unit="per_booking", duration_minutes=None):
    # Scope-check the product belongs to this club before attaching a price.
    owned = session.execute(
        text("SELECT 1 FROM billing.product WHERE club_id = :c AND id = :p"),
        {"c": club_id, "p": product_id},
    ).first()
    if not owned:
        return None
    pid = session.execute(
        text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
             "currency_code, unit, duration_minutes, active) "
             "VALUES (:c, :prod, :a, :amt, :cur, :u, :dur, true) RETURNING id"),
        {"c": club_id, "prod": product_id, "a": audience, "amt": amount_minor,
         "cur": _club_currency(session, club_id=club_id), "u": unit, "dur": duration_minutes},
    ).scalar_one()
    return _get_price(session, club_id=club_id, price_id=pid)


def patch_price(session, *, club_id, price_id, audience=None, amount_minor=None, unit=None,
                duration_minutes=None, active=None, status=None):
    """Partial update. `status` (active|dormant|retired) moves the price through its lifecycle and
    keeps the `active` boolean in sync (active = status='active') so customer reads Just Work."""
    if status is not None and status not in ("active", "dormant", "retired"):
        return None
    res = session.execute(
        text("""
            UPDATE billing.price SET
                audience         = COALESCE(:audience, audience),
                amount_minor     = COALESCE(:amount_minor, amount_minor),
                unit             = COALESCE(:unit, unit),
                duration_minutes = COALESCE(:duration_minutes, duration_minutes),
                status           = COALESCE(:status, status),
                active           = CASE WHEN :status IS NOT NULL THEN (:status = 'active')
                                        ELSE COALESCE(:active, active) END,
                updated_at       = now()
            WHERE club_id = :c AND id = :p
            RETURNING id
        """),
        {"c": club_id, "p": price_id, "audience": audience, "amount_minor": amount_minor,
         "unit": unit, "duration_minutes": duration_minutes, "active": active, "status": status},
    ).mappings().first()
    if not res:
        return None
    return _get_price(session, club_id=club_id, price_id=price_id)


def deactivate_price(session, *, club_id, price_id):
    """Retire a price (hidden from customers, kept for history)."""
    return patch_price(session, club_id=club_id, price_id=price_id, status="retired") is not None


# ---------------------------------------------------------------------------
# membership TERM PLANS (configurable: label + amount + duration). A term plan = one
# billing.price row (with term_months SET, unit='per_month', audience='member') on the
# club's kind='membership' product. Owner CRUDs them; the member picks one at checkout and
# activation grants the plan's term_months. NOTHING hardcoded — a plan is data.
# ---------------------------------------------------------------------------

def _plan_label(label, term_months):
    if label:
        return label
    m = int(term_months or 0)
    if m == 1:
        return "1 month"
    if m > 1:
        return f"{m} months"
    return "Membership"


def _plan_row(row):
    if row is None:
        return None
    tm = int(row["term_months"]) if row["term_months"] is not None else None
    days = row["access_days"] if "access_days" in row.keys() else None
    return {
        "price_id": str(row["price_id"]),
        "label": _plan_label(row["label"], tm),
        "amount_minor": int(row["amount_minor"] or 0),
        "term_months": tm,
        "currency": row["currency_code"],
        "active": bool(row["active"]),
        "status": row["status"] if "status" in row.keys() else ("active" if row["active"] else "retired"),
        "tier": (row["membership_tier"] if "membership_tier" in row.keys() else None) or None,
        # Access window (Phase 5): NULL = unconstrained (covers any time). access_days = ISO list.
        "access_days": [int(x) for x in days.split(",") if x.strip()] if days else None,
        "access_start_min": int(row["access_start_min"]) if row.get("access_start_min") is not None else None,
        "access_end_min": int(row["access_end_min"]) if row.get("access_end_min") is not None else None,
    }


_MEMBERSHIP_PLAN_COLS = (
    "p.id AS price_id, p.label, p.amount_minor, p.term_months, p.currency_code, p.active, p.status, "
    "p.membership_tier, p.access_days, p.access_start_min, p.access_end_min")


def list_membership_plans(session, *, club_id):
    """All membership term plans for the club (active + inactive), cheapest-first. A term plan is
    a price on the membership product with term_months set."""
    rows = session.execute(
        text("SELECT " + _MEMBERSHIP_PLAN_COLS + " "
             "FROM billing.product pr "
             "JOIN billing.price p ON p.product_id = pr.id "
             "WHERE pr.club_id = :c AND pr.kind = 'membership' AND p.term_months IS NOT NULL "
             "ORDER BY p.active DESC, p.amount_minor ASC, p.term_months ASC, p.created_at ASC"),
        {"c": club_id},
    ).mappings().all()
    return [_plan_row(r) for r in rows]


def _get_membership_plan(session, *, club_id, price_id):
    return _plan_row(session.execute(
        text("SELECT " + _MEMBERSHIP_PLAN_COLS + " "
             "FROM billing.product pr "
             "JOIN billing.price p ON p.product_id = pr.id "
             "WHERE pr.club_id = :c AND pr.kind = 'membership' AND p.id = :pid "
             "  AND p.term_months IS NOT NULL"),
        {"c": club_id, "pid": price_id},
    ).mappings().first())


def _membership_product_id(session, *, club_id, create_if_missing=False):
    pid = session.execute(
        text("SELECT id FROM billing.product "
             "WHERE club_id = :c AND kind = 'membership' ORDER BY created_at LIMIT 1"),
        {"c": club_id},
    ).scalar()
    if pid:
        return pid
    if not create_if_missing:
        return None
    return session.execute(
        text("INSERT INTO billing.product (club_id, kind, name, active) "
             "VALUES (:c, 'membership', 'Unlimited Courts Membership', true) RETURNING id"),
        {"c": club_id},
    ).scalar_one()


def _days_csv(days):
    """Normalize an access_days value (list[int], CSV str, or None) to a clean CSV of ISO weekdays
    ('1'..'7'), or None for 'all days'. An empty/full set -> None (unconstrained)."""
    if days is None:
        return None
    if isinstance(days, str):
        days = [d for d in days.split(",")]
    nums = sorted({int(d) for d in days if str(d).strip() and 1 <= int(d) <= 7})
    if not nums or len(nums) == 7:
        return None
    return ",".join(str(n) for n in nums)


def create_membership_plan(session, *, club_id, label, amount_minor, term_months, tier=None,
                           access_days=None, access_start_min=None, access_end_min=None):
    """Add a term plan = a billing.price (term_months, unit='per_month', audience='member') on the
    club's membership product (creating the product if missing). `tier` is the optional grouping name
    (Student/Family/…) the wizard drills (tier → term). Optional access window (Phase 5) time-boxes a
    tier: NULL = unconstrained (covers any time)."""
    prod_id = _membership_product_id(session, club_id=club_id, create_if_missing=True)
    pid = session.execute(
        text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
             "currency_code, unit, term_months, label, membership_tier, active, "
             "access_days, access_start_min, access_end_min) "
             "VALUES (:c, :prod, 'member', :amt, :cur, 'per_month', :tm, :lbl, :tier, true, "
             ":days, :smin, :emin) RETURNING id"),
        {"c": club_id, "prod": prod_id, "amt": int(amount_minor),
         "cur": _club_currency(session, club_id=club_id), "tm": int(term_months),
         "lbl": (label or "").strip() or None, "tier": (tier or "").strip() or None,
         "days": _days_csv(access_days), "smin": access_start_min, "emin": access_end_min},
    ).scalar_one()
    return _get_membership_plan(session, club_id=club_id, price_id=pid)


def patch_membership_plan(session, *, club_id, price_id, label=None, amount_minor=None,
                          term_months=None, active=None, status=None, tier=None,
                          access_days=None, access_start_min=None, access_end_min=None,
                          set_window=False):
    """COALESCE partial update of a term plan. Scoped to the club + the membership product so a
    booking price can't be reshaped into a plan here. `label`='' clears to NULL (derive default).
    `status` (active|dormant|retired) keeps the `active` boolean in sync. `set_window=True` writes
    the access window (any of the three may be NULL = unconstrained); else the window is untouched."""
    if status is not None and status not in ("active", "dormant", "retired"):
        return None
    lbl = label.strip() if isinstance(label, str) else None
    res = session.execute(
        text("""
            UPDATE billing.price p SET
                label        = CASE WHEN :lbl_set THEN :lbl ELSE p.label END,
                membership_tier = CASE WHEN :tier_set THEN :tier ELSE p.membership_tier END,
                amount_minor = COALESCE(:amount_minor, p.amount_minor),
                term_months  = COALESCE(:term_months, p.term_months),
                status       = COALESCE(:status, p.status),
                active       = CASE WHEN :status IS NOT NULL THEN (:status = 'active')
                                    ELSE COALESCE(:active, p.active) END,
                access_days      = CASE WHEN :set_win THEN :days ELSE p.access_days END,
                access_start_min = CASE WHEN :set_win THEN :smin ELSE p.access_start_min END,
                access_end_min   = CASE WHEN :set_win THEN :emin ELSE p.access_end_min END,
                updated_at   = now()
            FROM billing.product pr
            WHERE p.product_id = pr.id AND pr.club_id = :c AND pr.kind = 'membership'
              AND p.id = :pid AND p.term_months IS NOT NULL
            RETURNING p.id
        """),
        {"c": club_id, "pid": price_id,
         "lbl_set": label is not None, "lbl": (lbl or None),
         "tier_set": tier is not None, "tier": ((tier or "").strip() or None) if isinstance(tier, str) else None,
         "amount_minor": amount_minor, "term_months": term_months,
         "active": active, "status": status,
         "set_win": bool(set_window), "days": _days_csv(access_days),
         "smin": access_start_min, "emin": access_end_min},
    ).mappings().first()
    if not res:
        return None
    return _get_membership_plan(session, club_id=club_id, price_id=price_id)


def deactivate_membership_plan(session, *, club_id, price_id):
    """Retire a term plan (hidden from customers, kept for history)."""
    return patch_membership_plan(session, club_id=club_id, price_id=price_id,
                                 status="retired") is not None


# ---------------------------------------------------------------------------
# coaches + coach_invite
# ---------------------------------------------------------------------------

def list_coaches(session, *, club_id):
    """Coaches = iam.user JOIN membership(role=coach) JOIN coach_profile, LEFT JOIN the
    latest coach_invite for status. Scoped to the club."""
    rows = session.execute(
        text("""
            SELECT u.id AS user_id, u.email, u.first_name, u.surname, u.phone,
                   m.member_status,
                   cp.display_name, cp.headline, cp.is_bookable, cp.rank,
                   ci.status AS invite_status, ci.token AS invite_token, ci.created_at AS invited_at
            FROM iam.membership m
            JOIN iam.user u ON u.id = m.user_id
            LEFT JOIN iam.coach_profile cp ON cp.user_id = u.id AND cp.club_id = m.club_id
            LEFT JOIN LATERAL (
                SELECT status, token, created_at FROM iam.coach_invite
                WHERE club_id = m.club_id AND user_id = u.id
                ORDER BY created_at DESC LIMIT 1
            ) ci ON true
            WHERE m.club_id = :c AND m.role = 'coach'
            ORDER BY cp.rank, u.surname, u.first_name
        """),
        {"c": club_id},
    ).mappings().all()
    return _rows(rows)


def get_user_by_email(session, *, email):
    return _row(session.execute(
        text("SELECT id, clerk_user_id, email, first_name, surname, phone "
             "FROM iam.user WHERE lower(email) = lower(:e) ORDER BY created_at LIMIT 1"),
        {"e": email},
    ).mappings().first())


def upsert_user_by_email(session, *, email, first_name=None, surname=None, phone=None):
    """iam.user keyed by email (coaches link to Clerk on first login). Returns user_id (uuid).
    iam.user HAS a phone column (iam/schema.py) so phone is stored there."""
    existing = session.execute(
        text("SELECT id FROM iam.user WHERE lower(email) = lower(:e) ORDER BY created_at LIMIT 1"),
        {"e": email},
    ).mappings().first()
    if existing:
        session.execute(
            text("UPDATE iam.user SET "
                 "first_name = COALESCE(:fn, first_name), "
                 "surname    = COALESCE(:sn, surname), "
                 "phone      = COALESCE(:ph, phone), "
                 "updated_at = now() WHERE id = :id"),
            {"fn": first_name, "sn": surname, "ph": phone, "id": existing["id"]},
        )
        return existing["id"]
    row = session.execute(
        text("INSERT INTO iam.user (email, first_name, surname, phone) "
             "VALUES (:e, :fn, :sn, :ph) RETURNING id"),
        {"e": email, "fn": first_name, "sn": surname, "ph": phone},
    ).mappings().first()
    return row["id"]


def upsert_coach_membership(session, *, club_id, user_id):
    session.execute(
        text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
             "VALUES (:c, :u, 'coach', 'active') "
             "ON CONFLICT (club_id, user_id, role) "
             "DO UPDATE SET member_status = 'active', updated_at = now()"),
        {"c": club_id, "u": user_id},
    )


def upsert_coach_profile(session, *, club_id, user_id, display_name=None):
    existing = session.execute(
        text("SELECT id FROM iam.coach_profile WHERE user_id = :u"), {"u": user_id},
    ).mappings().first()
    if existing:
        session.execute(
            text("UPDATE iam.coach_profile SET club_id = :c, "
                 "display_name = COALESCE(:dn, display_name), updated_at = now() WHERE id = :id"),
            {"c": club_id, "dn": display_name, "id": existing["id"]},
        )
        return existing["id"]
    row = session.execute(
        text("INSERT INTO iam.coach_profile (club_id, user_id, display_name, is_bookable) "
             "VALUES (:c, :u, :dn, true) RETURNING id"),
        {"c": club_id, "u": user_id, "dn": display_name},
    ).mappings().first()
    return row["id"]


def set_coach_bookable(session, *, club_id, user_id, is_bookable):
    """Admin Hide/Unhide a coach: toggle iam.coach_profile.is_bookable (Hidden coaches aren't offered
    for booking, but are kept). Returns True if a row was updated."""
    res = session.execute(
        text("UPDATE iam.coach_profile SET is_bookable = :b, updated_at = now() "
             "WHERE club_id = :c AND user_id = :u"),
        {"b": bool(is_bookable), "c": club_id, "u": user_id},
    )
    return (res.rowcount or 0) > 0


def set_coach_status(session, *, club_id, user_id, status):
    """Coach lifecycle (the same 3-state model as services/memberships), mapped onto the existing
    fields: active = membership active + bookable; deactivated = bookable false (kept, hidden from
    booking); terminated = membership lapsed (kept for history). Returns True on success."""
    if status not in ("active", "deactivated", "terminated"):
        return False
    if status == "terminated":
        session.execute(
            text("UPDATE iam.membership SET member_status = 'lapsed', updated_at = now() "
                 "WHERE club_id = :c AND user_id = :u AND role = 'coach'"),
            {"c": club_id, "u": user_id})
    elif status == "deactivated":
        session.execute(
            text("UPDATE iam.coach_profile SET is_bookable = false, updated_at = now() "
                 "WHERE club_id = :c AND user_id = :u"),
            {"c": club_id, "u": user_id})
    else:  # active — reinstate fully
        session.execute(
            text("UPDATE iam.membership SET member_status = 'active', updated_at = now() "
                 "WHERE club_id = :c AND user_id = :u AND role = 'coach'"),
            {"c": club_id, "u": user_id})
        session.execute(
            text("UPDATE iam.coach_profile SET is_bookable = true, updated_at = now() "
                 "WHERE club_id = :c AND user_id = :u"),
            {"c": club_id, "u": user_id})
    return True


def create_coach_invite(session, *, club_id, user_id, token):
    row = session.execute(
        text("INSERT INTO iam.coach_invite (club_id, user_id, token, status) "
             "VALUES (:c, :u, :t, 'invited') RETURNING id, token, status, created_at"),
        {"c": club_id, "u": user_id, "t": token},
    ).mappings().first()
    return _row(row)


def latest_invite(session, *, club_id, user_id):
    return _row(session.execute(
        text("SELECT id, club_id, user_id, token, status, created_at, accepted_at "
             "FROM iam.coach_invite WHERE club_id = :c AND user_id = :u "
             "ORDER BY created_at DESC LIMIT 1"),
        {"c": club_id, "u": user_id},
    ).mappings().first())


def revoke_coach(session, *, club_id, user_id):
    """Revoke a coach: mark membership lapsed + every outstanding invite revoked. Scoped."""
    res = session.execute(
        text("UPDATE iam.membership SET member_status = 'lapsed', updated_at = now() "
             "WHERE club_id = :c AND user_id = :u AND role = 'coach' RETURNING id"),
        {"c": club_id, "u": user_id},
    ).mappings().first()
    session.execute(
        text("UPDATE iam.coach_invite SET status = 'revoked' "
             "WHERE club_id = :c AND user_id = :u AND status = 'invited'"),
        {"c": club_id, "u": user_id},
    )
    return res is not None


def delete_coach(session, *, club_id, user_id):
    """Hard-delete a coach when it's SAFE (no bookings, no financial history) — for coaches added by
    mistake or invites never accepted. If the coach has any history we keep the records and archive
    instead (membership lapsed), so reporting/settlement stays intact. Returns 'deleted', 'archived',
    or None when no such coach exists. Club-scoped throughout."""
    exists = session.execute(
        text("SELECT 1 FROM iam.membership WHERE club_id = :c AND user_id = :u AND role = 'coach'"),
        {"c": club_id, "u": user_id}).first()
    if exists is None:
        return None
    history = session.execute(
        text("""
            SELECT
              (SELECT count(*) FROM diary.booking WHERE club_id = :c AND coach_user_id = :u)
            + (SELECT count(*) FROM billing.commission_split WHERE club_id = :c AND coach_user_id = :u)
            + (SELECT count(*) FROM billing.coach_ledger WHERE club_id = :c AND coach_user_id = :u)
            + (SELECT count(*) FROM billing.coach_arrears WHERE club_id = :c AND coach_user_id = :u)
        """),
        {"c": club_id, "u": user_id}).scalar() or 0
    if history > 0:
        revoke_coach(session, club_id=club_id, user_id=user_id)
        return "archived"
    # No history → remove the coach's config rows and membership. (diary.resource children — hours,
    # availability — cascade; bookings would block the resource delete but we've ruled them out.)
    session.execute(text("DELETE FROM iam.coach_invite WHERE club_id = :c AND user_id = :u"), {"c": club_id, "u": user_id})
    session.execute(text("DELETE FROM billing.coach_agreement WHERE club_id = :c AND coach_user_id = :u"), {"c": club_id, "u": user_id})
    session.execute(text("DELETE FROM billing.commission_rule WHERE club_id = :c AND coach_user_id = :u"), {"c": club_id, "u": user_id})
    session.execute(text("DELETE FROM diary.resource WHERE club_id = :c AND kind = 'coach' AND coach_user_id = :u"), {"c": club_id, "u": user_id})
    session.execute(text("DELETE FROM iam.coach_profile WHERE club_id = :c AND user_id = :u"), {"c": club_id, "u": user_id})
    session.execute(text("DELETE FROM iam.membership WHERE club_id = :c AND user_id = :u AND role = 'coach'"), {"c": club_id, "u": user_id})
    return "deleted"


def get_coach(session, *, club_id, user_id):
    return _row(session.execute(
        text("""
            SELECT u.id AS user_id, u.email, u.first_name, u.surname, u.phone,
                   m.member_status, cp.display_name, cp.is_bookable, cp.rank
            FROM iam.membership m
            JOIN iam.user u ON u.id = m.user_id
            LEFT JOIN iam.coach_profile cp ON cp.user_id = u.id AND cp.club_id = m.club_id
            WHERE m.club_id = :c AND m.user_id = :u AND m.role = 'coach'
        """),
        {"c": club_id, "u": user_id},
    ).mappings().first())


# ---------------------------------------------------------------------------
# onboarding step derivation
# ---------------------------------------------------------------------------

def list_payments(session, *, club_id):
    """Recent successful CHARGE payments for the club, with the payer email + whether the
    order has since been refunded. Powers the admin Billing view's refund action."""
    rows = session.execute(
        text("""
            SELECT p.id, p.order_id, p.provider, p.amount_minor, p.currency_code, p.status,
                   p.created_at, o.settlement_mode, u.email AS payer_email,
                   EXISTS(SELECT 1 FROM billing.payment r
                          WHERE r.order_id = p.order_id AND r.direction = 'refund') AS refunded
            FROM billing.payment p
            JOIN billing."order" o ON o.id = p.order_id
            LEFT JOIN iam."user" u ON u.id = o.user_id
            WHERE p.club_id = :c AND p.direction = 'charge' AND p.status = 'succeeded'
            ORDER BY p.created_at DESC
            LIMIT 50
        """),
        {"c": club_id},
    ).mappings().all()
    return _rows(rows)


def list_people(session, *, club_id):
    """Everyone with a membership in the club (members, coaches, guests, admins): iam.user
    JOIN membership with role + status, coach display_name where applicable, and the latest
    coach-invite status. Scoped to the club. Ordered by role then name."""
    rows = session.execute(
        text("""
            SELECT u.id AS user_id, u.email, u.first_name, u.surname, u.phone,
                   m.role, m.member_status,
                   cp.display_name,
                   ci.status AS invite_status,
                   EXISTS(SELECT 1 FROM billing.membership_subscription ms
                          WHERE ms.club_id = m.club_id AND ms.user_id = u.id
                            AND ms.status = 'active'
                            AND (ms.current_period_end IS NULL
                                 OR ms.current_period_end >= CURRENT_DATE)) AS has_membership
            FROM iam.membership m
            JOIN iam.user u ON u.id = m.user_id
            LEFT JOIN iam.coach_profile cp ON cp.user_id = u.id AND cp.club_id = m.club_id
            LEFT JOIN LATERAL (
                SELECT status FROM iam.coach_invite
                WHERE club_id = m.club_id AND user_id = u.id
                ORDER BY created_at DESC LIMIT 1
            ) ci ON true
            WHERE m.club_id = :c
            ORDER BY m.role, u.surname NULLS LAST, u.first_name NULLS LAST
        """),
        {"c": club_id},
    ).mappings().all()
    return _rows(rows)


def grant_membership(session, *, club_id, user_id, months=1):
    """Admin grants a member an active membership (the club's 'membership' product). Makes
    their COURT bookings free (membership_covered) until current_period_end. Idempotent: an
    existing active subscription is extended rather than duplicated. provider='manual'."""
    price_id = session.execute(
        text("SELECT p.id FROM billing.product pr "
             "JOIN billing.price p ON p.product_id = pr.id AND p.active = true "
             "WHERE pr.club_id = :c AND pr.kind = 'membership' "
             "ORDER BY p.created_at LIMIT 1"),
        {"c": club_id},
    ).scalar()
    existing = session.execute(
        text("SELECT id FROM billing.membership_subscription "
             "WHERE club_id = :c AND user_id = :u AND status = 'active' LIMIT 1"),
        {"c": club_id, "u": user_id},
    ).scalar()
    months = max(1, int(months or 1))
    if existing:
        session.execute(
            text("UPDATE billing.membership_subscription "
                 "SET current_period_end = (CURRENT_DATE + make_interval(months => :m))::date, "
                 "    price_id = COALESCE(price_id, :pid), updated_at = now() WHERE id = :id"),
            {"m": months, "pid": price_id, "id": existing},
        )
        return {"ok": True, "status": "extended"}
    session.execute(
        text("INSERT INTO billing.membership_subscription "
             "(club_id, user_id, price_id, status, provider, current_period_end) "
             "VALUES (:c, :u, :pid, 'active', 'manual', "
             "        (CURRENT_DATE + make_interval(months => :m))::date)"),
        {"c": club_id, "u": user_id, "pid": price_id, "m": months},
    )
    return {"ok": True, "status": "granted"}


def revoke_membership(session, *, club_id, user_id):
    """Admin cancels a member's active membership (their courts revert to PAYG)."""
    session.execute(
        text("UPDATE billing.membership_subscription SET status = 'cancelled', updated_at = now() "
             "WHERE club_id = :c AND user_id = :u AND status = 'active'"),
        {"c": club_id, "u": user_id},
    )
    return {"ok": True}


def onboarding_counts_and_steps(session, *, club_id):
    """Derive the onboarding step booleans + counts in one pass (each guarded so a not-yet-
    present lane table degrades to False/0 rather than erroring)."""
    location = get_primary_location(session, club_id=club_id)
    profile = bool(location and location.get("address_line"))

    def _count(sql, default=0):
        try:
            return int(session.execute(text(sql), {"c": club_id}).scalar() or 0)
        except Exception:
            session.rollback()
            return default

    courts = _count("SELECT count(*) FROM diary.resource "
                    "WHERE club_id = :c AND kind = 'court' AND is_active = true")
    hours = _count("SELECT count(*) FROM diary.availability_rule WHERE club_id = :c")
    products = _count("SELECT count(*) FROM billing.product WHERE club_id = :c AND active = true")
    prices = _count("SELECT count(*) FROM billing.price WHERE club_id = :c AND active = true")
    coaches = _count("SELECT count(*) FROM iam.membership WHERE club_id = :c AND role = 'coach'")

    steps = {
        "profile":  profile,
        "hours":    hours >= 1,
        "courts":   courts >= 1,
        "services": prices >= 1,
        "coaches":  coaches >= 1,
    }
    counts = {"courts": courts, "products": products, "coaches": coaches}
    return steps, counts


def hours_week(session, *, club_id):
    """Collapse the courts' availability_rule into one representative week for UI pre-fill:
    {week:[{weekday,open,start_time'HH:MM',end_time'HH:MM',slot_minutes}]} for weekdays 0-6.
    A weekday with at least one court rule is 'open' (with that rule's times); others 'closed'
    with sensible defaults. Guarded: returns a default closed week if diary.* isn't present."""
    default = {"week": [
        {"weekday": wd, "open": False, "start_time": "06:00", "end_time": "22:00",
         "slot_minutes": 60} for wd in range(7)]}
    try:
        rows = session.execute(
            text("SELECT DISTINCT ON (ar.weekday) ar.weekday, "
                 "to_char(ar.start_time,'HH24:MI') AS start_time, "
                 "to_char(ar.end_time,'HH24:MI') AS end_time, ar.slot_minutes "
                 "FROM diary.availability_rule ar "
                 "JOIN diary.resource r ON r.id = ar.resource_id AND r.kind = 'court' "
                 "WHERE ar.club_id = :c "
                 "ORDER BY ar.weekday, ar.start_time"),
            {"c": club_id},
        ).mappings().all()
    except Exception:
        session.rollback()
        return default
    by_wd = {int(r["weekday"]): r for r in rows}
    week = []
    for wd in range(7):
        r = by_wd.get(wd)
        if r:
            week.append({"weekday": wd, "open": True, "start_time": r["start_time"],
                         "end_time": r["end_time"], "slot_minutes": int(r["slot_minutes"])})
        else:
            week.append({"weekday": wd, "open": False, "start_time": "06:00",
                         "end_time": "22:00", "slot_minutes": 60})
    return {"week": week}


# ===========================================================================
# commission engine — owner config + cockpit aggregation (Phase D)
#
# Plain SQL backing /api/admin/coach-agreements*, /commission-rules*, the coach statement,
# and the owner cockpit. All club_id-scoped. Rate writes SUPERSEDE (close the prior rule,
# insert a new one) so history is preserved for the cockpit. The resolution algorithm lives
# in billing/commission.py (resolve_commission_pct) — these helpers read/write config + the
# reporting aggregates. Money in *_minor cents, ex-VAT.
# ===========================================================================

def _derive_scope(product_id, coach_user_id):
    if product_id and coach_user_id:
        return "coach_product"
    if product_id:
        return "product"
    if coach_user_id:
        return "coach"
    return "club"


def get_agreement(session, *, club_id, coach_user_id):
    """The coach's current (active, open-ended) agreement, or None."""
    return _row(session.execute(
        text("SELECT id, club_id, coach_user_id, rent_minor, rent_currency, rent_day, status, "
             "       effective_from, effective_to, notes "
             "FROM billing.coach_agreement "
             "WHERE club_id = :c AND coach_user_id = :u "
             "  AND status = 'active' AND effective_to IS NULL "
             "ORDER BY effective_from DESC LIMIT 1"),
        {"c": club_id, "u": coach_user_id},
    ).mappings().first())


def upsert_agreement(session, *, club_id, coach_user_id, rent_minor=None, rent_day=None,
                     status=None, notes=None):
    """Upsert the coach's active agreement (one open-ended active row per coach). COALESCE
    partial update; inserts a fresh row if none exists. Currency = club currency."""
    existing = get_agreement(session, club_id=club_id, coach_user_id=coach_user_id)
    if existing:
        session.execute(
            text("""
                UPDATE billing.coach_agreement SET
                    rent_minor = COALESCE(:rent, rent_minor),
                    rent_day   = COALESCE(:day, rent_day),
                    status     = COALESCE(:status, status),
                    notes      = COALESCE(:notes, notes),
                    updated_at = now()
                WHERE id = :id
            """),
            {"rent": rent_minor, "day": rent_day, "status": status,
             "notes": notes, "id": existing["id"]},
        )
    else:
        session.execute(
            text("""
                INSERT INTO billing.coach_agreement
                    (club_id, coach_user_id, rent_minor, rent_currency, rent_day, status, notes)
                VALUES (:c, :u, COALESCE(:rent, 0), :cur, COALESCE(:day, 1), 'active', :notes)
            """),
            {"c": club_id, "u": coach_user_id, "rent": rent_minor,
             "cur": _club_currency(session, club_id=club_id), "day": rent_day, "notes": notes},
        )
    return get_agreement(session, club_id=club_id, coach_user_id=coach_user_id)


def list_commission_rules(session, *, club_id):
    """All commission rules for the club (active + history), most-specific/newest first."""
    return _rows(session.execute(
        text("SELECT id, club_id, scope, product_id, coach_user_id, commission_pct, "
             "       effective_from, effective_to, active, note "
             "FROM billing.commission_rule WHERE club_id = :c "
             "ORDER BY active DESC, effective_from DESC, id DESC"),
        {"c": club_id},
    ).mappings().all())


def set_commission_rule(session, *, club_id, product_id=None, coach_user_id=None,
                        commission_pct=0):
    """Set a rate for a scope: close any matching ACTIVE rule (effective_to=now, active=false)
    then insert a new one. SUPERSEDE preserves history. Scope is derived from which of
    product_id/coach_user_id are set. Returns the new rule row."""
    scope = _derive_scope(product_id, coach_user_id)
    # Close the prior active rule of the SAME scope (exact same product/coach keys).
    session.execute(
        text("""
            UPDATE billing.commission_rule
            SET active = false, effective_to = now()
            WHERE club_id = :c AND active AND scope = :scope
              AND product_id IS NOT DISTINCT FROM :product
              AND coach_user_id IS NOT DISTINCT FROM :coach
        """),
        {"c": club_id, "scope": scope, "product": product_id, "coach": coach_user_id},
    )
    row = session.execute(
        text("""
            INSERT INTO billing.commission_rule
                (club_id, scope, product_id, coach_user_id, commission_pct, active)
            VALUES (:c, :scope, :product, :coach, :pct, true)
            RETURNING id, club_id, scope, product_id, coach_user_id, commission_pct,
                      effective_from, effective_to, active, note
        """),
        {"c": club_id, "scope": scope, "product": product_id, "coach": coach_user_id,
         "pct": commission_pct},
    ).mappings().first()
    return _row(row)


def deactivate_commission_rule(session, *, club_id, rule_id):
    res = session.execute(
        text("UPDATE billing.commission_rule SET active = false, effective_to = now() "
             "WHERE club_id = :c AND id = :id AND active RETURNING id"),
        {"c": club_id, "id": rule_id},
    ).mappings().first()
    return res is not None


def coach_agreements_overview(session, *, club_id):
    """The Settings 'Coach agreements' payload: per coach -> rent + resolved coach-level % +
    their lesson types with club/coach/effective %, plus the club default %, plus the full
    rule list. The effective_pct per lesson type is computed by the resolution algorithm so
    the owner sees exactly what will apply."""
    from billing.commission import resolve_commission_pct
    currency = _club_currency(session, club_id=club_id)

    # Club default (scope='club') resolved %.
    club_default = float(resolve_commission_pct(session, club_id=club_id))

    coaches = session.execute(
        text("""
            SELECT u.id AS coach_user_id, u.first_name, u.surname, u.email, cp.display_name
            FROM iam.membership m
            JOIN iam.user u ON u.id = m.user_id
            LEFT JOIN iam.coach_profile cp ON cp.user_id = u.id AND cp.club_id = m.club_id
            WHERE m.club_id = :c AND m.role = 'coach'
            ORDER BY cp.rank NULLS LAST, u.surname NULLS LAST, u.first_name NULLS LAST
        """),
        {"c": club_id},
    ).mappings().all()

    out_coaches = []
    for c in coaches:
        coach_id = c["coach_user_id"]
        agreement = get_agreement(session, club_id=club_id, coach_user_id=coach_id)
        name = (c["display_name"]
                or " ".join(x for x in [c["first_name"], c["surname"]] if x).strip()
                or c["email"] or "Coach")
        # The coach's lesson products (lesson type = billing.product(kind='lesson', coach)).
        lesson_types = session.execute(
            text("SELECT id, name, kind FROM billing.product "
                 "WHERE club_id = :c AND kind IN ('lesson','class') "
                 "  AND (coach_user_id = :u OR coach_user_id IS NULL) AND active "
                 "ORDER BY kind, name"),
            {"c": club_id, "u": coach_id},
        ).mappings().all()
        lt_out = []
        for lt in lesson_types:
            club_pct = _scope_pct(session, club_id=club_id, scope="product",
                                  product_id=lt["id"], coach_user_id=None)
            coach_pct = _scope_pct(session, club_id=club_id, scope="coach_product",
                                   product_id=lt["id"], coach_user_id=coach_id)
            effective = float(resolve_commission_pct(
                session, club_id=club_id, product_id=lt["id"], coach_user_id=coach_id))
            lt_out.append({"product_id": str(lt["id"]), "name": lt["name"], "kind": lt["kind"],
                           "club_pct": club_pct, "coach_pct": coach_pct,
                           "effective_pct": effective})
        coach_level_pct = _scope_pct(session, club_id=club_id, scope="coach",
                                     product_id=None, coach_user_id=coach_id)
        out_coaches.append({
            "coach_user_id": str(coach_id),
            "name": name,
            "rent_minor": int(agreement["rent_minor"]) if agreement else 0,
            "rent_day": int(agreement["rent_day"]) if agreement else 1,
            "currency": currency,
            "coach_pct": coach_level_pct,
            "lesson_types": lt_out,
        })

    return {
        "club_default_pct": club_default,
        "currency": currency,
        "coaches": out_coaches,
        "rules": list_commission_rules(session, club_id=club_id),
    }


def _scope_pct(session, *, club_id, scope, product_id, coach_user_id):
    """The raw active rule % for an EXACT scope (not resolved/inherited) — None if no such
    rule. Drives the editable per-scope fields in the config UI."""
    v = session.execute(
        text("""
            SELECT commission_pct FROM billing.commission_rule
            WHERE club_id = :c AND active AND scope = :scope
              AND product_id IS NOT DISTINCT FROM :product
              AND coach_user_id IS NOT DISTINCT FROM :coach
            ORDER BY effective_from DESC, id DESC LIMIT 1
        """),
        {"c": club_id, "scope": scope, "product": product_id, "coach": coach_user_id},
    ).scalar()
    return float(v) if v is not None else None


# ---------------------------------------------------------------------------
# owner cockpit — financial aggregates (views-style, thin SQL passthroughs)
# ---------------------------------------------------------------------------

def cockpit_revenue(session, *, club_id, dt_from=None, dt_to=None):
    """Revenue by month + service kind, NET from the payment log (gross charges - refunds).
    Money in minor units. dt_from/dt_to are ISO date strings (inclusive from, exclusive to)."""
    rows = session.execute(
        text("""
            SELECT to_char(date_trunc('month', p.created_at), 'YYYY-MM') AS month,
                   COALESCE(prod.kind, 'other') AS service_kind,
                   SUM(ol.amount_minor) FILTER (WHERE p.direction='charge') AS gross_minor,
                   SUM(ol.amount_minor) FILTER (WHERE p.direction='refund') AS refund_minor,
                   SUM(CASE WHEN p.direction='charge' THEN ol.amount_minor
                            ELSE -ol.amount_minor END) AS net_minor
            FROM billing.payment p
            JOIN billing."order" o     ON o.id = p.order_id
            JOIN billing.order_line ol ON ol.order_id = o.id
            LEFT JOIN billing.price pr   ON pr.id = ol.price_id
            LEFT JOIN billing.product prod ON prod.id = pr.product_id
            WHERE p.club_id = :c
              AND ((p.direction = 'charge' AND p.status = 'succeeded')
                   OR (p.direction = 'refund' AND p.status IN ('succeeded', 'refunded')))
              AND (CAST(:dt_from AS text) IS NULL OR p.created_at >= CAST(:dt_from AS timestamptz))
              AND (CAST(:dt_to AS text) IS NULL OR p.created_at <  CAST(:dt_to   AS timestamptz))
            GROUP BY 1, 2 ORDER BY 1 DESC, 2
        """),
        {"c": club_id, "dt_from": dt_from, "dt_to": dt_to},
    ).mappings().all()
    return [{"month": r["month"], "service_kind": r["service_kind"],
             "gross_minor": int(r["gross_minor"] or 0),
             "refund_minor": int(r["refund_minor"] or 0),
             "net_minor": int(r["net_minor"] or 0)} for r in rows]


def cockpit_coach_earnings(session, *, club_id, dt_from=None, dt_to=None):
    """Per coach: gross lesson revenue, commission earned (owner keeps), coach earning, rent
    due (period), net to coach, and lifetime ledger balance. Straight off commission_split +
    coach_ledger (the heart of the cockpit). Signed minor units."""
    rows = session.execute(
        text("""
            WITH splits AS (
              SELECT coach_user_id,
                     SUM(amount_minor) FILTER (WHERE party_type='coach') AS coach_earn_minor,
                     SUM(amount_minor) FILTER (WHERE party_type='owner') AS owner_cut_minor,
                     SUM(gross_minor)  FILTER (WHERE party_type='owner') AS gross_lesson_minor,
                     count(*) FILTER (WHERE party_type='coach')          AS lesson_count
              FROM billing.commission_split
              WHERE club_id = :c AND basis <> 'refund_clawback'
                AND (CAST(:dt_from AS text) IS NULL OR occurred_at >= CAST(:dt_from AS timestamptz))
                AND (CAST(:dt_to AS text) IS NULL OR occurred_at <  CAST(:dt_to   AS timestamptz))
              GROUP BY coach_user_id),
            rent AS (
              SELECT coach_user_id, SUM(amount_minor) AS rent_minor   -- negative
              FROM billing.coach_ledger
              WHERE club_id = :c AND entry_type = 'rent_charge'
                AND (CAST(:dt_from AS text) IS NULL OR occurred_at >= CAST(:dt_from AS timestamptz))
                AND (CAST(:dt_to AS text) IS NULL OR occurred_at <  CAST(:dt_to   AS timestamptz))
              GROUP BY coach_user_id),
            bal AS (
              SELECT coach_user_id, SUM(amount_minor) AS balance_minor
              FROM billing.coach_ledger WHERE club_id = :c GROUP BY coach_user_id)
            SELECT u.id AS coach_user_id,
                   COALESCE(cp.display_name,
                            NULLIF(trim(concat_ws(' ', u.first_name, u.surname)), ''),
                            u.email) AS coach_name,
                   COALESCE(s.gross_lesson_minor, 0) AS gross_lesson_minor,
                   COALESCE(s.owner_cut_minor, 0)    AS commission_earned_minor,
                   COALESCE(s.coach_earn_minor, 0)   AS coach_earning_minor,
                   COALESCE(s.lesson_count, 0)       AS lesson_count,
                   COALESCE(-r.rent_minor, 0)        AS rent_due_minor,
                   COALESCE(s.coach_earn_minor, 0) + COALESCE(r.rent_minor, 0) AS net_to_coach_minor,
                   COALESCE(b.balance_minor, 0)      AS lifetime_balance_minor
            FROM iam.user u
            JOIN billing.coach_agreement ca
                 ON ca.coach_user_id = u.id AND ca.club_id = :c
                AND ca.status = 'active' AND ca.effective_to IS NULL
            LEFT JOIN iam.coach_profile cp ON cp.user_id = u.id AND cp.club_id = :c
            LEFT JOIN splits s ON s.coach_user_id = u.id
            LEFT JOIN rent   r ON r.coach_user_id = u.id
            LEFT JOIN bal    b ON b.coach_user_id = u.id
            ORDER BY commission_earned_minor DESC
        """),
        {"c": club_id, "dt_from": dt_from, "dt_to": dt_to},
    ).mappings().all()
    return [{
        "coach_user_id": str(r["coach_user_id"]),
        "coach_name": r["coach_name"],
        "lesson_count": int(r["lesson_count"] or 0),
        "gross_lesson_minor": int(r["gross_lesson_minor"] or 0),
        "commission_earned_minor": int(r["commission_earned_minor"] or 0),
        "coach_earning_minor": int(r["coach_earning_minor"] or 0),
        "rent_due_minor": int(r["rent_due_minor"] or 0),
        "net_to_coach_minor": int(r["net_to_coach_minor"] or 0),
        "lifetime_balance_minor": int(r["lifetime_balance_minor"] or 0),
    } for r in rows]


def cockpit_memberships(session, *, club_id):
    """Active membership count + MRR-ish (sum of active membership prices). Term plans mean
    'MRR' is the active monthly-equivalent; we report the active subscription value sum."""
    r = session.execute(
        text("""
            SELECT count(*) FILTER (WHERE ms.status='active'
                     AND (ms.current_period_end IS NULL OR ms.current_period_end >= CURRENT_DATE))
                     AS active_members,
                   COALESCE(SUM(pr.amount_minor) FILTER (WHERE ms.status='active'
                     AND (ms.current_period_end IS NULL OR ms.current_period_end >= CURRENT_DATE)
                     AND COALESCE(pr.term_months,1) > 0), 0) AS mrr_minor
            FROM billing.membership_subscription ms
            LEFT JOIN billing.price pr ON pr.id = ms.price_id
            WHERE ms.club_id = :c
        """),
        {"c": club_id},
    ).mappings().first()
    return {"active_members": int(r["active_members"] or 0),
            "mrr_minor": int(r["mrr_minor"] or 0)}


def cockpit_summary(session, *, club_id, dt_from=None, dt_to=None):
    """KPI header scalars: net revenue (period), commission earned (owner), rent due,
    active members + MRR, lessons booked (period)."""
    rev = cockpit_revenue(session, club_id=club_id, dt_from=dt_from, dt_to=dt_to)
    earnings = cockpit_coach_earnings(session, club_id=club_id, dt_from=dt_from, dt_to=dt_to)
    mem = cockpit_memberships(session, club_id=club_id)
    net_revenue = sum(r["net_minor"] for r in rev)
    commission = sum(e["commission_earned_minor"] for e in earnings)
    rent_due = sum(e["rent_due_minor"] for e in earnings)
    lessons = sum(e["lesson_count"] for e in earnings)
    return {
        "currency": _club_currency(session, club_id=club_id),
        "net_revenue_minor": net_revenue,
        "commission_earned_minor": commission,
        "rent_due_minor": rent_due,
        "lessons_paid": lessons,
        "active_members": mem["active_members"],
        "mrr_minor": mem["mrr_minor"],
    }


def coach_user_ids(session, *, club_id):
    """Coach user_ids for the club (membership role=coach). Used to scope the statement route."""
    return [str(r) for r in session.execute(
        text("SELECT user_id FROM iam.membership WHERE club_id = :c AND role = 'coach'"),
        {"c": club_id},
    ).scalars().all()]

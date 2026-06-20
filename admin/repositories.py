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
             "       duration_minutes, active, created_at, updated_at "
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
                duration_minutes=None, active=None):
    res = session.execute(
        text("""
            UPDATE billing.price SET
                audience         = COALESCE(:audience, audience),
                amount_minor     = COALESCE(:amount_minor, amount_minor),
                unit             = COALESCE(:unit, unit),
                duration_minutes = COALESCE(:duration_minutes, duration_minutes),
                active           = COALESCE(:active, active),
                updated_at       = now()
            WHERE club_id = :c AND id = :p
            RETURNING id
        """),
        {"c": club_id, "p": price_id, "audience": audience, "amount_minor": amount_minor,
         "unit": unit, "duration_minutes": duration_minutes, "active": active},
    ).mappings().first()
    if not res:
        return None
    return _get_price(session, club_id=club_id, price_id=price_id)


def deactivate_price(session, *, club_id, price_id):
    res = session.execute(
        text("UPDATE billing.price SET active = false, updated_at = now() "
             "WHERE club_id = :c AND id = :p RETURNING id"),
        {"c": club_id, "p": price_id},
    ).mappings().first()
    return res is not None


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

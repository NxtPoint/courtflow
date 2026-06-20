# scripts/provision_club.py — idempotent club provisioning + the "template club" clone.
#
# The cookie-cutter primitive (docs/08 §4): club #2 is a clone of a template club's
# branding + policy defaults, then customised. Everything here is idempotent — keyed on
# club.slug — so re-running is a no-op/upsert. It owns ONLY club.* scaffolding
# (club/location/branding/policy); iam/core/diary/billing content is layered by callers
# (e.g. seed_nextpoint.py) or the lane agents.
#
# Run standalone:
#   python -m scripts.provision_club --slug demo --name "Demo Tennis Club"
# or import provision_club(...) / clone_from_template(...) from another script.

import argparse
import logging

from sqlalchemy import text

from db import session_scope, run_boot_init

log = logging.getLogger("provision_club")

# Platform defaults for a brand-new South African club (overridable per call).
DEFAULT_POLICY = dict(
    booking_window_days=14,
    min_booking_minutes=60,
    cancellation_cutoff_hours=12,
    no_show_fee_minor=0,
    guest_requires_member=True,
    allow_pay_at_court=True,
    allow_monthly_account=True,
    allow_online_payment=False,
)


def get_club_by_slug(session, slug):
    row = session.execute(
        text("SELECT id, slug, name, is_template FROM club.club WHERE slug = :s"),
        {"s": slug},
    ).mappings().first()
    return dict(row) if row else None


def upsert_club(session, *, slug, name, legal_name=None, currency_code="ZAR",
                timezone="Africa/Johannesburg", locale="en-ZA", status="active",
                is_template=False):
    """Insert or update a club by slug. Returns club_id (uuid)."""
    row = session.execute(
        text("""
            INSERT INTO club.club (slug, name, legal_name, currency_code, timezone,
                                   locale, status, is_template)
            VALUES (:slug, :name, :legal, :cur, :tz, :loc, :status, :tmpl)
            ON CONFLICT (slug) DO UPDATE SET
                name = EXCLUDED.name,
                legal_name = COALESCE(EXCLUDED.legal_name, club.club.legal_name),
                currency_code = EXCLUDED.currency_code,
                timezone = EXCLUDED.timezone,
                locale = EXCLUDED.locale,
                status = EXCLUDED.status,
                is_template = EXCLUDED.is_template,
                updated_at = now()
            RETURNING id
        """),
        {"slug": slug, "name": name, "legal": legal_name, "cur": currency_code,
         "tz": timezone, "loc": locale, "status": status, "tmpl": is_template},
    ).mappings().first()
    return row["id"]


def upsert_location(session, *, club_id, name, **fields):
    """Idempotent on (club_id, name). Returns location_id."""
    existing = session.execute(
        text("SELECT id FROM club.location WHERE club_id = :c AND name = :n"),
        {"c": club_id, "n": name},
    ).mappings().first()
    cols = ("address_line", "city", "postal_code", "country", "lat", "lng", "phone", "email")
    vals = {k: fields.get(k) for k in cols}
    if existing:
        session.execute(
            text("UPDATE club.location SET address_line=:address_line, city=:city, "
                 "postal_code=:postal_code, country=:country, lat=:lat, lng=:lng, "
                 "phone=:phone, email=:email, updated_at=now() WHERE id=:id"),
            {**vals, "id": existing["id"]},
        )
        return existing["id"]
    row = session.execute(
        text("INSERT INTO club.location (club_id, name, address_line, city, postal_code, "
             "country, lat, lng, phone, email) "
             "VALUES (:club_id, :name, :address_line, :city, :postal_code, :country, "
             ":lat, :lng, :phone, :email) RETURNING id"),
        {"club_id": club_id, "name": name, **vals},
    ).mappings().first()
    return row["id"]


def upsert_branding(session, *, club_id, **fields):
    """One row per club (club_id PK). Idempotent upsert."""
    cols = ("primary_color", "accent_color", "logo_url", "favicon_url", "domain",
            "marketing_hosts", "og_image_url", "klaviyo_list_id")
    vals = {k: fields.get(k) for k in cols}
    session.execute(
        text("""
            INSERT INTO club.branding (club_id, primary_color, accent_color, logo_url,
                favicon_url, domain, marketing_hosts, og_image_url, klaviyo_list_id)
            VALUES (:club_id, :primary_color, :accent_color, :logo_url, :favicon_url,
                :domain, :marketing_hosts, :og_image_url, :klaviyo_list_id)
            ON CONFLICT (club_id) DO UPDATE SET
                primary_color = EXCLUDED.primary_color,
                accent_color = EXCLUDED.accent_color,
                logo_url = EXCLUDED.logo_url,
                favicon_url = EXCLUDED.favicon_url,
                domain = EXCLUDED.domain,
                marketing_hosts = EXCLUDED.marketing_hosts,
                og_image_url = EXCLUDED.og_image_url,
                klaviyo_list_id = EXCLUDED.klaviyo_list_id,
                updated_at = now()
        """),
        {"club_id": club_id, **vals},
    )


def upsert_policy(session, *, club_id, **overrides):
    """One row per club (club_id PK). Idempotent upsert; merges DEFAULT_POLICY + overrides."""
    p = {**DEFAULT_POLICY, **{k: v for k, v in overrides.items() if v is not None}}
    session.execute(
        text("""
            INSERT INTO club.policy (club_id, booking_window_days, min_booking_minutes,
                cancellation_cutoff_hours, no_show_fee_minor, guest_requires_member,
                allow_pay_at_court, allow_monthly_account, allow_online_payment)
            VALUES (:club_id, :booking_window_days, :min_booking_minutes,
                :cancellation_cutoff_hours, :no_show_fee_minor, :guest_requires_member,
                :allow_pay_at_court, :allow_monthly_account, :allow_online_payment)
            ON CONFLICT (club_id) DO UPDATE SET
                booking_window_days = EXCLUDED.booking_window_days,
                min_booking_minutes = EXCLUDED.min_booking_minutes,
                cancellation_cutoff_hours = EXCLUDED.cancellation_cutoff_hours,
                no_show_fee_minor = EXCLUDED.no_show_fee_minor,
                guest_requires_member = EXCLUDED.guest_requires_member,
                allow_pay_at_court = EXCLUDED.allow_pay_at_court,
                allow_monthly_account = EXCLUDED.allow_monthly_account,
                allow_online_payment = EXCLUDED.allow_online_payment,
                updated_at = now()
        """),
        {"club_id": club_id, **p},
    )


def provision_club(session, *, slug, name, legal_name=None, currency_code="ZAR",
                   timezone="Africa/Johannesburg", locale="en-ZA", is_template=False,
                   branding=None, policy=None, locations=None):
    """Idempotently provision a club's club.* scaffolding. Returns club_id.

    branding: dict of club.branding fields. policy: dict of overrides over DEFAULT_POLICY.
    locations: list of dicts (each needs 'name')."""
    club_id = upsert_club(session, slug=slug, name=name, legal_name=legal_name,
                          currency_code=currency_code, timezone=timezone, locale=locale,
                          is_template=is_template)
    upsert_branding(session, club_id=club_id, **(branding or {}))
    upsert_policy(session, club_id=club_id, **(policy or {}))
    for loc in (locations or []):
        upsert_location(session, club_id=club_id, **loc)
    log.info("provisioned club slug=%s id=%s", slug, club_id)
    return club_id


def get_template_club(session, template_slug="_template"):
    return get_club_by_slug(session, template_slug)


def ensure_template_club(session, template_slug="_template"):
    """Ensure a hidden template club exists (suspended, is_template=true) carrying the
    platform default branding + policy. Club #2+ clone from this (docs/08 §4)."""
    club_id = upsert_club(session, slug=template_slug, name="Template Club",
                          status="suspended", is_template=True)
    upsert_branding(session, club_id=club_id, primary_color="#0B5", accent_color="#053")
    upsert_policy(session, club_id=club_id)
    return club_id


def clone_from_template(session, *, slug, name, template_slug="_template", **overrides):
    """Create a new club by cloning the template club's branding + policy, then applying
    overrides. Idempotent (keyed on the new slug)."""
    tmpl = get_template_club(session, template_slug)
    if tmpl is None:
        ensure_template_club(session, template_slug)
        tmpl = get_template_club(session, template_slug)

    tmpl_branding = session.execute(
        text("SELECT primary_color, accent_color, logo_url, favicon_url, og_image_url "
             "FROM club.branding WHERE club_id = :c"),
        {"c": tmpl["id"]},
    ).mappings().first()
    tmpl_policy = session.execute(
        text("SELECT booking_window_days, min_booking_minutes, cancellation_cutoff_hours, "
             "no_show_fee_minor, guest_requires_member, allow_pay_at_court, "
             "allow_monthly_account, allow_online_payment FROM club.policy WHERE club_id = :c"),
        {"c": tmpl["id"]},
    ).mappings().first()

    branding = dict(tmpl_branding or {})
    branding.update(overrides.get("branding", {}))
    policy = dict(tmpl_policy or {})
    policy.update(overrides.get("policy", {}))

    return provision_club(
        session, slug=slug, name=name,
        legal_name=overrides.get("legal_name"),
        currency_code=overrides.get("currency_code", "ZAR"),
        timezone=overrides.get("timezone", "Africa/Johannesburg"),
        locale=overrides.get("locale", "en-ZA"),
        branding=branding, policy=policy,
        locations=overrides.get("locations"),
    )


def main():
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description="Idempotently provision a club (club.* scaffolding).")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--from-template", action="store_true",
                    help="clone branding+policy from the _template club")
    ap.add_argument("--domain", default=None)
    ap.add_argument("--skip-init", action="store_true", help="assume schema already booted")
    args = ap.parse_args()

    if not args.skip_init:
        run_boot_init()

    with session_scope() as s:
        if args.from_template:
            club_id = clone_from_template(s, slug=args.slug, name=args.name,
                                          branding={"domain": args.domain} if args.domain else {})
        else:
            club_id = provision_club(s, slug=args.slug, name=args.name,
                                     branding={"domain": args.domain} if args.domain else {})
    print(f"club provisioned: slug={args.slug} id={club_id}")


if __name__ == "__main__":
    main()

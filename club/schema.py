# club/schema.py — idempotent boot DDL for the `club.*` schema (the tenant root).
#
# Per docs/02 §2. Raw idempotent SQL (CREATE TABLE/INDEX IF NOT EXISTS, ADD COLUMN
# IF NOT EXISTS) — same discipline as 1050's db_init, no migration framework. init()
# is safe to run on every boot and twice in a row.
#
# Tables: club.club (tenant), club.location (>=1 venue per club), club.branding
# (white-label, host-switch), club.policy (booking/cancellation rules).
#
# UUID PKs via gen_random_uuid() (needs pgcrypto — enabled by db.run_boot_init()).

from sqlalchemy import text

SCHEMA = "club"

_DDL = [
    f"CREATE SCHEMA IF NOT EXISTS {SCHEMA};",

    # --- club.club : one row per tenant -----------------------------------
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.club (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        slug          text UNIQUE NOT NULL,
        name          text NOT NULL,
        legal_name    text,
        status        text NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active','trialing','suspended')),
        currency_code text NOT NULL DEFAULT 'ZAR',
        timezone      text NOT NULL DEFAULT 'Africa/Johannesburg',
        locale        text DEFAULT 'en-ZA',
        is_template   boolean NOT NULL DEFAULT false,  -- template-club clone source (docs/08 §4)
        created_at    timestamptz NOT NULL DEFAULT now(),
        updated_at    timestamptz NOT NULL DEFAULT now()
    );
    """,

    # --- club.location : venues -------------------------------------------
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.location (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id      uuid NOT NULL REFERENCES {SCHEMA}.club(id) ON DELETE CASCADE,
        name         text,
        address_line text,
        city         text,
        postal_code  text,
        country      text,
        lat          numeric,
        lng          numeric,
        phone        text,
        email        text,
        created_at   timestamptz NOT NULL DEFAULT now(),
        updated_at   timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_location_club ON {SCHEMA}.location (club_id);",

    # --- club.branding : white-label + host-switch (1 row per club) -------
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.branding (
        club_id         uuid PRIMARY KEY REFERENCES {SCHEMA}.club(id) ON DELETE CASCADE,
        primary_color   text,
        accent_color    text,
        logo_url        text,
        favicon_url     text,
        domain          text,            -- 'nextpointtennis.com' (primary host -> club resolution)
        marketing_hosts text[],          -- host-switch (mirrors 1050 MARKETING_HOSTS)
        og_image_url    text,
        klaviyo_list_id text,            -- per-club Klaviyo segmentation (optional, docs/06)
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now()
    );
    """,
    # Host -> club resolution lookups (docs/04 §3). Unique domain so a host maps to one club.
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_branding_domain "
    f"ON {SCHEMA}.branding (lower(domain)) WHERE domain IS NOT NULL;",

    # --- club.policy : booking & cancellation rules (1 row per club) ------
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.policy (
        club_id                  uuid PRIMARY KEY REFERENCES {SCHEMA}.club(id) ON DELETE CASCADE,
        booking_window_days      int  DEFAULT 14,
        min_booking_minutes      int  DEFAULT 60,
        cancellation_cutoff_hours int DEFAULT 12,
        no_show_fee_minor        numeric DEFAULT 0,
        guest_requires_member    boolean DEFAULT true,
        allow_pay_at_court       boolean DEFAULT true,
        allow_monthly_account    boolean DEFAULT true,
        allow_online_payment     boolean DEFAULT false,   -- flip on when Yoco goes live
        created_at               timestamptz NOT NULL DEFAULT now(),
        updated_at               timestamptz NOT NULL DEFAULT now()
    );
    """,
    # PEAK court-pricing window (one per club, court hire only). A court booking whose LOCAL start falls in
    # this window is charged its duration's peak price (billing.price.peak_amount_minor) instead of the base
    # amount; membership coverage still wins first. NULL = no peak pricing (unchanged behaviour). Same shape
    # as the membership access window (CSV ISO weekdays Mon=1..Sun=7; minutes-from-midnight, end exclusive).
    f"ALTER TABLE {SCHEMA}.policy ADD COLUMN IF NOT EXISTS peak_days text;",
    f"ALTER TABLE {SCHEMA}.policy ADD COLUMN IF NOT EXISTS peak_start_min int;",
    f"ALTER TABLE {SCHEMA}.policy ADD COLUMN IF NOT EXISTS peak_end_min int;",
]


def init(engine=None):
    """Create / update the club.* schema idempotently. Safe on every boot."""
    if engine is None:
        from db import get_engine
        engine = get_engine()
    with engine.begin() as conn:
        for stmt in _DDL:
            conn.execute(text(stmt))
    return engine


if __name__ == "__main__":
    init()
    print("club.* schema initialised")

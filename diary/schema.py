# diary/schema.py — idempotent boot DDL for the `diary.*` schema (the booking engine).
#
# Per docs/02 §4 (storage shape) + §8 (indexes + the exclusion constraint) and docs/03
# (behaviour). Raw idempotent SQL — same discipline as club.schema / iam.schema: CREATE
# TABLE/INDEX IF NOT EXISTS, ADD COLUMN IF NOT EXISTS. No migration framework. init() is
# safe on every boot and twice in a row.
#
# Tables: resource, availability_rule, time_off, booking, booking_party, class_session,
#         enrolment, waitlist, recurrence.
#
# THE crown jewel (docs/02 §8, docs/03 §4): a GiST EXCLUDE constraint on diary.booking
# makes Postgres physically refuse an overlapping held/confirmed row for the same
# resource — double-booking becomes impossible at the storage layer. ADD CONSTRAINT has
# no IF NOT EXISTS, so it is added inside a guarded DO $$ block that checks pg_constraint.
# Needs the btree_gist extension (enabled by db.run_boot_init()).
#
# club_id NOT NULL everywhere (multi-tenant from day one). UUID PKs via gen_random_uuid().

from sqlalchemy import text

SCHEMA = "diary"

# Name of the exclusion constraint (referenced by bookings.py to detect SLOT_TAKEN).
EXCLUSION_CONSTRAINT = "booking_no_overlap"

_DDL = [
    f"CREATE SCHEMA IF NOT EXISTS {SCHEMA};",

    # --- diary.resource : anything bookable that has a calendar -----------
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.resource (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id       uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        location_id   uuid REFERENCES club.location(id) ON DELETE SET NULL,
        kind          text NOT NULL CHECK (kind IN ('court','coach','class')),
        name          text,
        surface       text,                         -- 'hard' | 'clay' (courts)
        coach_user_id uuid,                          -- when kind='coach' (-> iam.coach_profile)
        capacity      int NOT NULL DEFAULT 1,        -- courts=1; classes=N
        is_active     boolean NOT NULL DEFAULT true,
        rank          int NOT NULL DEFAULT 0,
        created_at    timestamptz NOT NULL DEFAULT now(),
        updated_at    timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_resource_club_kind "
    f"ON {SCHEMA}.resource (club_id, kind, is_active);",
    f"CREATE INDEX IF NOT EXISTS ix_resource_coach "
    f"ON {SCHEMA}.resource (club_id, coach_user_id);",
    # A COURT belongs to a court SERVICE (billing.product kind='court_booking') — e.g. 'Hardcourt
    # Hire' vs 'Clay Hire' — so distinct court services can carry their OWN price + allocated courts.
    # Plain uuid (NOT a hard FK) to keep the diary decoupled from billing.* (same as coach_user_id).
    # NULL = unallocated → resolves to the club's DEFAULT court product (single-service clubs behave
    # exactly as before). Meaningful only for kind='court'.
    f"ALTER TABLE {SCHEMA}.resource ADD COLUMN IF NOT EXISTS product_id uuid;",
    f"CREATE INDEX IF NOT EXISTS ix_resource_product "
    f"ON {SCHEMA}.resource (club_id, product_id);",
    # EQUIPMENT (ball machine / racquets / balls) is a resource KIND with a `quantity` (how many exist,
    # 1 ball machine / 10 racquets) — it rides a court booking as a flat-fee add-on (see booking_equipment)
    # and is availability-checked by TIME (a single unit can't be hired twice for overlapping times),
    # never holding a court of its own. `feature_on_home` promotes an item to a hero tile on the client Home.
    f"ALTER TABLE {SCHEMA}.resource ADD COLUMN IF NOT EXISTS quantity int NOT NULL DEFAULT 1;",
    f"ALTER TABLE {SCHEMA}.resource ADD COLUMN IF NOT EXISTS feature_on_home boolean NOT NULL DEFAULT false;",
    # Widen resource.kind to accept 'equipment'. Idempotent drop+re-add of the auto-named CHECK.
    f"""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.constraint_column_usage
                   WHERE table_schema = '{SCHEMA}' AND table_name = 'resource'
                     AND constraint_name = 'resource_kind_check') THEN
            ALTER TABLE {SCHEMA}.resource DROP CONSTRAINT resource_kind_check;
        END IF;
        ALTER TABLE {SCHEMA}.resource ADD CONSTRAINT resource_kind_check
            CHECK (kind IN ('court','coach','class','equipment'));
    END $$;
    """,
    # --- diary.booking_equipment : equipment hired ON a booking (add-on lines + availability count) ---
    # Each row = N units of an equipment resource hired for a parent court booking's time. Drives BOTH
    # billing (one billing.order_line per row, on the booking's single order — no double bill) AND the
    # time-overlap availability count. price_id/amount_minor snapshot the flat fee at booking time.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.booking_equipment (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id       uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        booking_id    uuid NOT NULL REFERENCES {SCHEMA}.booking(id) ON DELETE CASCADE,
        resource_id   uuid NOT NULL REFERENCES {SCHEMA}.resource(id),
        qty           int NOT NULL DEFAULT 1,
        price_id      uuid,                            -- billing.price (cross-lane: unconstrained)
        amount_minor  int NOT NULL DEFAULT 0,          -- flat fee snapshot × qty is the order line
        created_at    timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_booking_equipment_booking "
    f"ON {SCHEMA}.booking_equipment (booking_id);",
    f"CREATE INDEX IF NOT EXISTS ix_booking_equipment_res "
    f"ON {SCHEMA}.booking_equipment (club_id, resource_id);",

    # --- diary.availability_rule : recurring open hours per resource ------
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.availability_rule (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id      uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        resource_id  uuid NOT NULL REFERENCES {SCHEMA}.resource(id) ON DELETE CASCADE,
        weekday      int NOT NULL,                   -- 0=Mon .. 6=Sun
        start_time   time NOT NULL,
        end_time     time NOT NULL,
        slot_minutes int NOT NULL DEFAULT 60,
        valid_from   date,                            -- seasonal overrides (nullable = always)
        valid_to     date,
        created_at   timestamptz NOT NULL DEFAULT now(),
        updated_at   timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_availability_rule_resource "
    f"ON {SCHEMA}.availability_rule (club_id, resource_id, weekday);",

    # --- diary.time_off : one-off blocks (holiday, maintenance, leave) ----
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.time_off (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id     uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        resource_id uuid NOT NULL REFERENCES {SCHEMA}.resource(id) ON DELETE CASCADE,
        starts_at   timestamptz NOT NULL,
        ends_at     timestamptz NOT NULL,
        reason      text,
        created_by  uuid,
        created_at  timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_time_off_resource "
    f"ON {SCHEMA}.time_off (club_id, resource_id, starts_at);",

    # --- diary.booking : THE core row (court hold OR lesson OR class) -----
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.booking (
        id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id             uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        booking_type        text NOT NULL CHECK (booking_type IN ('court','lesson','class')),
        resource_id         uuid NOT NULL REFERENCES {SCHEMA}.resource(id),
        coach_user_id       uuid,                    -- denormalised for lesson queries
        starts_at           timestamptz NOT NULL,
        ends_at             timestamptz NOT NULL,
        status              text NOT NULL DEFAULT 'confirmed' CHECK (status IN
                                ('held','confirmed','cancelled','completed','no_show',
                                 'requested','proposed')),
        held_until          timestamptz,             -- short-lived hold expiry (online flow)
        booked_by_user_id   uuid,                    -- the CLIENT/owner the booking is FOR (on-behalf sets this to the client, not the actor)
        recurrence_id       uuid,                    -- groups a recurring series (nullable)
        order_id            uuid,                    -- -> billing.order (settlement)
        settlement_mode     text,                    -- echoed from the order (audit/display)
        cancellation_reason text,
        cancelled_at        timestamptz,
        cancelled_by        uuid,
        notes               text,
        created_at          timestamptz NOT NULL DEFAULT now(),
        updated_at          timestamptz NOT NULL DEFAULT now(),
        CHECK (ends_at > starts_at)
    );
    """,
    # B-tree indexes (docs/02 §8).
    f"CREATE INDEX IF NOT EXISTS ix_booking_resource "
    f"ON {SCHEMA}.booking (club_id, resource_id, starts_at);",
    f"CREATE INDEX IF NOT EXISTS ix_booking_coach "
    f"ON {SCHEMA}.booking (club_id, coach_user_id, starts_at);",
    f"CREATE INDEX IF NOT EXISTS ix_booking_booker "
    f"ON {SCHEMA}.booking (club_id, booked_by_user_id, starts_at);",
    f"CREATE INDEX IF NOT EXISTS ix_booking_order "
    f"ON {SCHEMA}.booking (order_id);",
    f"CREATE INDEX IF NOT EXISTS ix_booking_recurrence "
    f"ON {SCHEMA}.booking (recurrence_id);",
    # Partial index to make the capacity-sweep's expired-hold scan cheap.
    f"CREATE INDEX IF NOT EXISTS ix_booking_held_until "
    f"ON {SCHEMA}.booking (held_until) WHERE status = 'held';",
    # The ACTOR who performed the booking (staff / parent / self), distinct from booked_by_user_id
    # (the CLIENT it's for). Populated going forward; NULL for pre-existing rows + self-books where
    # it equals the client. Drives the confirmation email's "Booked by" line (who did the action).
    f"ALTER TABLE {SCHEMA}.booking ADD COLUMN IF NOT EXISTS created_by_user_id uuid;",

    # THE exclusion constraint (docs/03 §4) — guarded ADD (no IF NOT EXISTS for it).
    # tstzrange(starts_at, ends_at) with '&&' overlap, only for held/confirmed rows, so
    # cancelled/completed/no_show rows free the slot. Needs btree_gist for the '=' on uuid.
    f"""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = '{EXCLUSION_CONSTRAINT}'
              AND conrelid = '{SCHEMA}.booking'::regclass
        ) THEN
            ALTER TABLE {SCHEMA}.booking
                ADD CONSTRAINT {EXCLUSION_CONSTRAINT}
                EXCLUDE USING gist (
                    resource_id WITH =,
                    tstzrange(starts_at, ends_at) WITH &&
                ) WHERE (status IN ('held','confirmed'));
        END IF;
    END $$;
    """,

    # Expand the booking status set for the lesson accept/propose/decline lifecycle
    # (requested = awaiting coach, proposed = awaiting client). Idempotent drop+re-add of the
    # column CHECK so existing DBs gain the new values; boot-twice safe.
    f"ALTER TABLE {SCHEMA}.booking DROP CONSTRAINT IF EXISTS booking_status_check;",
    f"ALTER TABLE {SCHEMA}.booking ADD CONSTRAINT booking_status_check "
    f"CHECK (status IN ('held','confirmed','cancelled','completed','no_show',"
    f"'requested','proposed'));",

    # --- diary.booking_party : participants (members / guests) -----------
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.booking_party (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        booking_id  uuid NOT NULL REFERENCES {SCHEMA}.booking(id) ON DELETE CASCADE,
        club_id     uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        user_id     uuid,                            -- nullable for ad-hoc guests
        party_role  text NOT NULL DEFAULT 'player' CHECK (party_role IN
                        ('host','partner','guest','player')),
        guest_name  text,
        guest_email text,
        price_id    uuid,                            -- which price applied to THIS party
        attended    boolean,
        created_at  timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_booking_party_booking "
    f"ON {SCHEMA}.booking_party (booking_id);",
    f"CREATE INDEX IF NOT EXISTS ix_booking_party_user "
    f"ON {SCHEMA}.booking_party (club_id, user_id);",

    # --- diary.class_session : a scheduled instance of a class -----------
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.class_session (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id       uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        resource_id   uuid NOT NULL REFERENCES {SCHEMA}.resource(id),  -- resource.kind='class'
        coach_user_id uuid,
        starts_at     timestamptz NOT NULL,
        ends_at       timestamptz NOT NULL,
        capacity      int NOT NULL DEFAULT 0,
        price_id      uuid,
        recurrence_id uuid,
        status        text NOT NULL DEFAULT 'scheduled' CHECK (status IN
                          ('scheduled','cancelled','completed')),
        created_at    timestamptz NOT NULL DEFAULT now(),
        updated_at    timestamptz NOT NULL DEFAULT now(),
        CHECK (ends_at > starts_at)
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_class_session_resource "
    f"ON {SCHEMA}.class_session (club_id, resource_id, starts_at);",
    f"CREATE INDEX IF NOT EXISTS ix_class_session_starts "
    f"ON {SCHEMA}.class_session (club_id, starts_at);",
    # A class can RESERVE A PHYSICAL COURT so it's booked out exactly like a member court booking:
    # court_booking_id -> the court-blocking diary.booking (reuses the GiST exclusion). Freed on cancel.
    # SCALAR cols kept for back-compat (email builder / legacy readers) — set to the FIRST reserved
    # court. The multi-court source of truth is diary.class_session_court below.
    f"ALTER TABLE {SCHEMA}.class_session ADD COLUMN IF NOT EXISTS court_resource_id uuid;",
    f"ALTER TABLE {SCHEMA}.class_session ADD COLUMN IF NOT EXISTS court_booking_id uuid;",

    # --- diary.class_session_court : a class can reserve MULTIPLE courts ---
    # One row per court a class occurrence holds (e.g. Cardio Tennis on courts 5–8). Each row's
    # court_booking_id -> the court-blocking diary.booking (booking_type='class') that GiST-reserves
    # that court so no lesson/court booking can take it. This is the SOURCE OF TRUTH for a session's
    # courts (the scalar class_session.court_* cols mirror the first one for legacy readers). Additive.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.class_session_court (
        id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id           uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        class_session_id  uuid NOT NULL REFERENCES {SCHEMA}.class_session(id) ON DELETE CASCADE,
        court_resource_id uuid NOT NULL REFERENCES {SCHEMA}.resource(id),
        court_booking_id  uuid,                        -- -> the court-blocking diary.booking
        created_at        timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_class_session_court_session "
    f"ON {SCHEMA}.class_session_court (class_session_id);",

    # --- diary.enrolment : a player joining a class_session --------------
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.enrolment (
        id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id          uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        class_session_id uuid NOT NULL REFERENCES {SCHEMA}.class_session(id) ON DELETE CASCADE,
        user_id          uuid,
        status           text NOT NULL DEFAULT 'enrolled' CHECK (status IN
                             ('enrolled','waitlisted','cancelled','attended','no_show')),
        order_id         uuid,
        waitlist_seq     bigserial,                  -- FIFO promotion ordering
        enrolled_at      timestamptz NOT NULL DEFAULT now(),
        updated_at       timestamptz NOT NULL DEFAULT now(),
        UNIQUE (class_session_id, user_id)
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_enrolment_session "
    f"ON {SCHEMA}.enrolment (class_session_id);",
    f"CREATE INDEX IF NOT EXISTS ix_enrolment_user "
    f"ON {SCHEMA}.enrolment (club_id, user_id);",
    # Billing intent captured at enrol time so a WAITLIST promotion can settle the seat exactly as an
    # enrol would (payer = guardian for a child; the chosen settlement mode; member/guest audience).
    f"ALTER TABLE {SCHEMA}.enrolment ADD COLUMN IF NOT EXISTS payer_user_id uuid;",
    f"ALTER TABLE {SCHEMA}.enrolment ADD COLUMN IF NOT EXISTS settlement_mode text;",
    f"ALTER TABLE {SCHEMA}.enrolment ADD COLUMN IF NOT EXISTS audience text;",
    # An ONLINE enrolment HOLDS its seat (status='enrolled') the instant it's created, awaiting the
    # Yoco payment — held_until stamps when that hold lapses. If the client abandons checkout the seat
    # is lazily released (release_expired_enrolments), the class analogue of the court hold. NULL for
    # firm seats (at-court/monthly/token/membership) — those are never auto-expired.
    f"ALTER TABLE {SCHEMA}.enrolment ADD COLUMN IF NOT EXISTS held_until timestamptz;",
    f"CREATE INDEX IF NOT EXISTS ix_enrolment_held "
    f"ON {SCHEMA}.enrolment (held_until) WHERE held_until IS NOT NULL;",

    # --- diary.waitlist : generic waitlist (court slot freeing up) -------
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.waitlist (
        id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id          uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        resource_id      uuid REFERENCES {SCHEMA}.resource(id) ON DELETE CASCADE,
        class_session_id uuid REFERENCES {SCHEMA}.class_session(id) ON DELETE CASCADE,
        user_id          uuid,
        desired_start    timestamptz,
        created_at       timestamptz NOT NULL DEFAULT now(),
        notified_at      timestamptz
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_waitlist_resource "
    f"ON {SCHEMA}.waitlist (club_id, resource_id, desired_start) "
    f"WHERE notified_at IS NULL;",

    # --- diary.recurrence : series definition (RRULE) --------------------
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.recurrence (
        id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id    uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        rrule      text NOT NULL,                    -- iCal RRULE string
        dtstart    timestamptz,                      -- series anchor (first occurrence)
        until      date,
        created_by uuid,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    """,

    # --- BACKFILL: link every CLASS resource to its billing.product ------------------------------
    # Class services used to be resolved by JOINING ON NAMES. Renaming a service updates only
    # billing.product.name, so the join silently broke and sessions scheduled afterwards got
    # price_id NULL -> billed at another class's rate under another class's payment rules.
    # create_class_type now sets diary.resource.product_id at birth; this heals the rows that
    # pre-date that. Deliberately CONSERVATIVE: it fills only NULL links, and only where exactly ONE
    # active class product matches on (name, coach) — an ambiguous or already-drifted name is left
    # alone for a human rather than guessed at. Idempotent: a second run matches nothing.
    """
    UPDATE diary.resource r
       SET product_id = m.pid, updated_at = now()
      FROM (
            SELECT r2.id AS rid, MIN(p.id::text)::uuid AS pid
              FROM diary.resource r2
              JOIN billing.product p
                ON p.club_id = r2.club_id AND p.kind = 'class' AND p.active = true
               AND lower(p.name) = lower(r2.name)
               AND p.coach_user_id IS NOT DISTINCT FROM r2.coach_user_id
             WHERE r2.kind = 'class' AND r2.product_id IS NULL
             GROUP BY r2.id
            HAVING count(*) = 1
           ) m
     WHERE r.id = m.rid AND r.product_id IS NULL;
    """,
]


def init(engine=None):
    """Create / update the diary.* schema idempotently. Requires club.* (FKs to
    club.club / club.location) and the btree_gist extension (both ensured by
    db.run_boot_init, which orders club before diary and enables extensions). Safe on
    every boot and twice in a row."""
    if engine is None:
        from db import get_engine
        engine = get_engine()
    with engine.begin() as conn:
        for stmt in _DDL:
            conn.execute(text(stmt))
    return engine


if __name__ == "__main__":
    init()
    print("diary.* schema initialised")

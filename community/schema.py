# community/schema.py — idempotent boot DDL OWNED by the community lane.
#
# Registered in db.BOOT_MODULES LAST: it creates the community.* schema and ALTERs diary.booking,
# diary.booking_party, iam.player_profile and club.policy, so club/iam/billing/diary must exist
# first. Same discipline as admin/schema.py + coach/schema.py — a lane never edits another lane's
# schema file, it adds what it needs here with ADD COLUMN IF NOT EXISTS.
#
# WHAT LIVES WHERE, AND WHY
#
# A GAME IS A BOOKING. The seat additions go on diary.booking / diary.booking_party rather than into
# a new `community.game` table, because a parallel game object would immediately fork the six things
# a booking already gets right: the GiST no-double-book constraint, the diary grid, reschedule and
# cancel, the unified statement (one debt = one order), Client-360 and month-end. diary.booking_party
# already IS a seat — it has user_id (nullable, for a guest), party_role, guest_name/guest_email,
# price_id and attended. This file gives it the money columns it was missing.
#
# Only the genuinely NEW domain gets community.* tables: invites, chat, results, the private
# would-play-again signal, and favourites. None of those has an existing home.
#
# init() is safe on every boot and twice in a row.

from sqlalchemy import text

SCHEMA = "community"

_DDL = [
    f"CREATE SCHEMA IF NOT EXISTS {SCHEMA};",

    # ------------------------------------------------------------------ #
    # 1) diary.booking — the game-level columns.
    # ------------------------------------------------------------------ #
    # visibility: 'private' (today's behaviour — only the named players) vs 'open' (the seat is
    # published to the community, Playtomic's Open Match). DEFAULT 'private' is what makes every
    # existing booking, and every existing scenario, behave exactly as before.
    "ALTER TABLE diary.booking ADD COLUMN IF NOT EXISTS visibility text "
    "NOT NULL DEFAULT 'private' CHECK (visibility IN ('private','open'));",

    # play_format decides the SEAT COUNT, which is the denominator of the money split. Getting this
    # wrong bills a doubles court as a singles one, so it is explicit rather than inferred.
    # 'practice' = one seat (a member hitting alone / against a ball machine), which is the one case
    # where there is no second seat to account for.
    "ALTER TABLE diary.booking ADD COLUMN IF NOT EXISTS play_format text "
    "CHECK (play_format IN ('singles','doubles','practice'));",
    "ALTER TABLE diary.booking ADD COLUMN IF NOT EXISTS seats int;",

    # The fill deadline. NULL = not an open game. After this instant an unfilled seat COLLAPSES onto
    # the booking holder (community.seats.collapse_open_seats) — the member still gets their court,
    # they just don't get a free second seat they never filled.
    "ALTER TABLE diary.booking ADD COLUMN IF NOT EXISTS open_until timestamptz;",

    # Set by the FIRST successful seat payment. Once money has moved the split can never be
    # recomputed — re-pricing a seat somebody already paid for is how you end up refunding cents and
    # breaking the statement fold. After the lock the game accepts only COVERED members into the
    # remaining seats (they owe nothing, so no split changes).
    "ALTER TABLE diary.booking ADD COLUMN IF NOT EXISTS split_locked_at timestamptz;",

    # Find-a-Game discovery reads open games by club + time; the partial index keeps that read off
    # the full booking table (which carries every booking the club has ever taken).
    "CREATE INDEX IF NOT EXISTS ix_booking_open_games ON diary.booking "
    "(club_id, starts_at) WHERE visibility = 'open' AND status IN ('held','confirmed');",

    # ------------------------------------------------------------------ #
    # 2) diary.booking_party — the party row BECOMES the seat.
    # ------------------------------------------------------------------ #
    # seat_status is the seat's own lifecycle, deliberately separate from the BOOKING's status:
    #   open      — published, nobody in it yet
    #   invited   — a named person has been asked (by email or user), not yet accepted
    #   held      — accepted, but their share is unpaid and must be prepaid
    #   confirmed — covered by membership, or paid, or owed on an accepted at-court/monthly debt
    #   released  — the invitee never paid by the deadline; the seat went back to open/collapsed
    #   collapsed — nobody took it, so its share was re-billed to the booking holder
    # DEFAULT 'confirmed' means every party row that exists today (squad lesson partners, guests)
    # keeps its current meaning without a backfill.
    "ALTER TABLE diary.booking_party ADD COLUMN IF NOT EXISTS seat_status text "
    "NOT NULL DEFAULT 'confirmed' CHECK (seat_status IN "
    "('open','invited','held','confirmed','released','collapsed'));",

    # THIS seat's own debt. NULL = the seat is covered (a member's membership paid for it) and there
    # is legitimately no order — the same shape as a membership_covered court today. Not a FK:
    # billing is a cross-lane schema (the convention billing.product.coach_user_id already follows).
    "ALTER TABLE diary.booking_party ADD COLUMN IF NOT EXISTS order_id uuid;",

    # The seat's share of the court fee, in minor units, frozen when the split locked. Stored rather
    # than recomputed so that what the player was TOLD is what the order says, forever — a recomputed
    # share drifts the moment anyone joins, cancels or lets a membership lapse.
    "ALTER TABLE diary.booking_party ADD COLUMN IF NOT EXISTS share_minor int;",

    # Resolved once, at seat time, by the EXISTING entitlement engine (diary.entitlement.court_covered
    # — access window, court-service eligibility, duration + daily caps, one-concurrent-covered-court).
    # Recorded so the money is auditable after the fact: "why was this seat free?" must be answerable
    # in six months, when the member's tier has changed.
    "ALTER TABLE diary.booking_party ADD COLUMN IF NOT EXISTS covered boolean;",

    "ALTER TABLE diary.booking_party ADD COLUMN IF NOT EXISTS invited_by_user_id uuid;",
    "ALTER TABLE diary.booking_party ADD COLUMN IF NOT EXISTS invited_at timestamptz;",
    "ALTER TABLE diary.booking_party ADD COLUMN IF NOT EXISTS joined_at timestamptz;",

    "CREATE INDEX IF NOT EXISTS ix_booking_party_seat_status "
    "ON diary.booking_party (club_id, seat_status);",
    # The sweep asks "which seats are unpaid past their deadline" across the club, and the money
    # reads ask "which order is this seat's" — both hit order_id.
    "CREATE INDEX IF NOT EXISTS ix_booking_party_order "
    "ON diary.booking_party (order_id) WHERE order_id IS NOT NULL;",

    # ------------------------------------------------------------------ #
    # 3) iam.player_profile — the dormant table becomes the player profile.
    # ------------------------------------------------------------------ #
    # The table already existed (dob, skill_level, dominant_hand, utr, guardian_user_id, notes) but
    # was only ever written by iam.repositories.create_dependent. Find a Game is what it was for.
    #
    # level_num is the NextPoint Level 1.0-10.0. Deliberately NOT UTR: replicating UTR needs a match
    # database we don't have yet, and the failure mode that kills these products is being repeatedly
    # matched against someone far too strong or too weak. A coach/admin override is recorded in
    # level_source + level_set_by_user_id so "everybody is advanced" is correctable and auditable.
    "ALTER TABLE iam.player_profile ADD COLUMN IF NOT EXISTS level_num numeric(3,1);",
    "ALTER TABLE iam.player_profile ADD COLUMN IF NOT EXISTS level_source text "
    "CHECK (level_source IN ('self','onboarding','coach','calculated'));",
    "ALTER TABLE iam.player_profile ADD COLUMN IF NOT EXISTS level_set_by_user_id uuid;",
    "ALTER TABLE iam.player_profile ADD COLUMN IF NOT EXISTS prefers_format text "
    "CHECK (prefers_format IN ('singles','doubles','both'));",
    "ALTER TABLE iam.player_profile ADD COLUMN IF NOT EXISTS prefers_play text "
    "CHECK (prefers_play IN ('social','practice','competitive'));",
    "ALTER TABLE iam.player_profile ADD COLUMN IF NOT EXISTS prefers_times text[];",
    "ALTER TABLE iam.player_profile ADD COLUMN IF NOT EXISTS photo_url text;",

    # EXPLICIT OPT-IN, defaulting FALSE. Being discoverable by 1,100 strangers is not something a
    # member should acquire by us shipping a feature — they choose it. A community read must filter
    # on this column, never on "has a profile row".
    "ALTER TABLE iam.player_profile ADD COLUMN IF NOT EXISTS visible_in_community boolean "
    "NOT NULL DEFAULT false;",

    "CREATE INDEX IF NOT EXISTS ix_player_profile_discoverable "
    "ON iam.player_profile (club_id, level_num) WHERE visible_in_community = true;",

    # ------------------------------------------------------------------ #
    # 4) club.policy — the module's switches. BOTH default false.
    # ------------------------------------------------------------------ #
    # Two switches, not one, and they are independent on purpose:
    #   community_enabled  — the social surface (Find a Game, open games, invites, chat)
    #   seat_rule_enforced — the MONEY rule (seats split the court fee)
    # The club can run the community feature before turning the money rule on (so members meet the
    # feature as a benefit, not a bill), and the money rule is the one that changes what members pay,
    # so it gets its own deliberate flip after the member communication goes out.
    #
    # seat_rule_enforced=false must mean the booking path behaves EXACTLY as it does today — that is
    # the regression contract, and sc_seat_rule_off_changes_nothing asserts it.
    "ALTER TABLE club.policy ADD COLUMN IF NOT EXISTS community_enabled boolean "
    "NOT NULL DEFAULT false;",
    "ALTER TABLE club.policy ADD COLUMN IF NOT EXISTS seat_rule_enforced boolean "
    "NOT NULL DEFAULT false;",
    # How long before the start an unfilled open seat collapses onto the holder. Long enough that
    # somebody can still realistically take it, short enough that the member can find a partner
    # elsewhere if it collapsed.
    "ALTER TABLE club.policy ADD COLUMN IF NOT EXISTS open_game_cutoff_hours int DEFAULT 12;",
    # How long an accepted-but-unpaid seat may hold the court. HOLD_MINUTES_DEFAULT (30) is sized for
    # one person standing at a Yoco checkout; a friend who has to read an email, sign up and pay needs
    # hours, not minutes.
    "ALTER TABLE club.policy ADD COLUMN IF NOT EXISTS seat_pay_hours int DEFAULT 24;",
    # The invited friend's free run. Reuses the EXISTING 7-day trial membership machinery rather than
    # inventing a second kind of free play (see community/invites.py).
    "ALTER TABLE club.policy ADD COLUMN IF NOT EXISTS guest_trial_days int DEFAULT 7;",

    # --- THE SHARE: what ONE player pays, as a % of the court's price ---------------------
    #
    # A SHARE IS A FIXED FRACTION OF THE COURT, NOT A DIVISION OF IT. That is the whole design
    # (owner decision, 2026-08-10), and it is what makes the price a player is quoted STABLE: it does
    # not move when someone else joins, leaves, or turns out to be a member. The earlier model divided
    # one court fee among the un-covered seats, which meant your share changed under you — and needed a
    # lock, a re-price and a refusal to keep it honest. None of that is necessary now.
    #
    # 50% is the default because two people is the normal case: singles with two payers then collects
    # exactly the court price. With MORE than two payers the club collects more than one court fee —
    # deliberately, since four people use a court more than two — and that is the consequence to
    # understand before switching the money rule on.
    "ALTER TABLE club.policy ADD COLUMN IF NOT EXISTS seat_share_pct int DEFAULT 50;",
    # Rounding is applied to the SHARE, once, after the percentage. 'up_10' keeps every amount a
    # member sees a whole, tidy number — and is a small price RISE wherever the raw share ends in 5
    # (R75 -> R80), which is intended rather than incidental.
    "ALTER TABLE club.policy ADD COLUMN IF NOT EXISTS seat_rounding text NOT NULL DEFAULT 'up_10' "
    "CHECK (seat_rounding IN ('none','up_5','up_10','nearest_5','nearest_10'));",

    # The share this game was QUOTED, frozen the first time its seats were priced.
    #
    # Why freeze it on the booking rather than recompute: the club can change seat_share_pct or the
    # court's price at any time, and a game already sold must not silently re-price under the people
    # who are in it. It also means a LATE joiner pays exactly what everyone else in that game paid,
    # which is the fair answer and removes the need to refuse them.
    "ALTER TABLE diary.booking ADD COLUMN IF NOT EXISTS seat_share_minor int;",

    # ------------------------------------------------------------------ #
    # 5) community.player_invite — "bring a friend".
    # ------------------------------------------------------------------ #
    # Mirrors iam.coach_invite. The token is the signed link the friend receives; it is minted by
    # marketing_crm.signing (HMAC, context-tagged, carries no PII) and only STORED here so an invite
    # can be revoked and so a replay can be recognised.
    #
    # trial_granted_at is the anti-abuse record: the free week is granted ONCE, to a genuinely new
    # account, and never to a returning member or one of the ~880 imported Wix users.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.player_invite (
        id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id           uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        inviter_user_id   uuid NOT NULL REFERENCES iam.user(id) ON DELETE CASCADE,
        email             text NOT NULL,
        token             text NOT NULL,
        status            text NOT NULL DEFAULT 'sent'
                              CHECK (status IN ('sent','accepted','expired','revoked')),
        booking_id        uuid,          -- the seat they were invited to (NULL = a plain club invite)
        party_id          uuid,          -- the diary.booking_party seat held for them
        accepted_user_id  uuid,
        trial_granted_at  timestamptz,
        expires_at        timestamptz,
        created_at        timestamptz NOT NULL DEFAULT now(),
        updated_at        timestamptz NOT NULL DEFAULT now()
    );
    """,
    # One LIVE invite per (club, email): re-inviting the same friend must re-send, never mint a
    # second free week. Partial unique index so accepted/expired/revoked rows accumulate as history.
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_player_invite_live "
    f"ON {SCHEMA}.player_invite (club_id, lower(email)) WHERE status = 'sent';",
    f"CREATE INDEX IF NOT EXISTS ix_player_invite_inviter "
    f"ON {SCHEMA}.player_invite (club_id, inviter_user_id);",
    f"CREATE INDEX IF NOT EXISTS ix_player_invite_booking "
    f"ON {SCHEMA}.player_invite (booking_id) WHERE booking_id IS NOT NULL;",

    # ------------------------------------------------------------------ #
    # 6) community.message — match chat.
    # ------------------------------------------------------------------ #
    # Deliberately minimal: no attachments, no edits, no threads. It exists so two players can agree
    # the details without swapping phone numbers — which is the point, since a community read never
    # returns a phone or an email. `system` rows are the automatic ones ("James joined", "seat paid")
    # so the chat doubles as the game's timeline.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.message (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id     uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        booking_id  uuid NOT NULL REFERENCES diary.booking(id) ON DELETE CASCADE,
        user_id     uuid REFERENCES iam.user(id) ON DELETE SET NULL,   -- NULL for a system line
        body        text NOT NULL,
        system      boolean NOT NULL DEFAULT false,
        created_at  timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE INDEX IF NOT EXISTS ix_message_booking "
    f"ON {SCHEMA}.message (booking_id, created_at);",

    # ------------------------------------------------------------------ #
    # 7) community.match_result — what happened.
    # ------------------------------------------------------------------ #
    # Reported by one player, CONFIRMED by another — an unconfirmed result is not evidence and must
    # never feed a rating. outcome carries 'no_show' because that is the signal that matters most for
    # trust: somebody who accepts games and doesn't turn up poisons the whole feature.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.match_result (
        id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id             uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        booking_id          uuid NOT NULL REFERENCES diary.booking(id) ON DELETE CASCADE,
        reported_by_user_id uuid NOT NULL REFERENCES iam.user(id) ON DELETE CASCADE,
        outcome             text NOT NULL CHECK (outcome IN ('played','cancelled','no_show')),
        winner_user_id      uuid REFERENCES iam.user(id) ON DELETE SET NULL,
        score_text          text,
        confirmed_by_user_id uuid REFERENCES iam.user(id) ON DELETE SET NULL,
        confirmed_at        timestamptz,
        created_at          timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_match_result_booking "
    f"ON {SCHEMA}.match_result (booking_id);",

    # ------------------------------------------------------------------ #
    # 8) community.play_again — the PRIVATE compatibility signal.
    # ------------------------------------------------------------------ #
    # "Would you play them again?" It is never rendered, never aggregated into a public score and
    # never shown to the person it is about. It exists only to weight matching, which is exactly why
    # people answer it honestly. If this is ever surfaced, the answers stop being useful and start
    # being a reputation system nobody asked for.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.play_again (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id         uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        booking_id      uuid NOT NULL REFERENCES diary.booking(id) ON DELETE CASCADE,
        rater_user_id   uuid NOT NULL REFERENCES iam.user(id) ON DELETE CASCADE,
        subject_user_id uuid NOT NULL REFERENCES iam.user(id) ON DELETE CASCADE,
        again           boolean NOT NULL,
        created_at      timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_play_again_once "
    f"ON {SCHEMA}.play_again (booking_id, rater_user_id, subject_user_id);",
    f"CREATE INDEX IF NOT EXISTS ix_play_again_subject "
    f"ON {SCHEMA}.play_again (club_id, subject_user_id);",

    # ------------------------------------------------------------------ #
    # 9) community.favourite — "My Tennis Circle".
    # ------------------------------------------------------------------ #
    # The organic outcome of the whole feature: after a few games you stop searching and just ask the
    # four people you actually enjoy playing. That is the retention loop, and it costs one table.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.favourite (
        id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        club_id           uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
        user_id           uuid NOT NULL REFERENCES iam.user(id) ON DELETE CASCADE,
        favourite_user_id uuid NOT NULL REFERENCES iam.user(id) ON DELETE CASCADE,
        created_at        timestamptz NOT NULL DEFAULT now()
    );
    """,
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_favourite_once "
    f"ON {SCHEMA}.favourite (club_id, user_id, favourite_user_id);",
]


def init(engine=None):
    """Create / update the community lane's schema idempotently. Requires club.*, iam.*, billing.*
    and diary.* to exist first (it ALTERs diary.booking, diary.booking_party, iam.player_profile and
    club.policy) — db.BOOT_MODULES orders community.schema last. Safe on every boot and twice in a
    row."""
    if engine is None:
        from db import get_engine
        engine = get_engine()
    with engine.begin() as conn:
        for stmt in _DDL:
            conn.execute(text(stmt))
    return engine


if __name__ == "__main__":
    init()
    print("community.* schema initialised")

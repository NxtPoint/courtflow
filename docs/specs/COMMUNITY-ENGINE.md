# COMMUNITY ENGINE — Find a Game + the seat-accounting money rule

**Status: IN BUILD (started 2026-08-09). Ships DARK** — `club.policy.community_enabled` and
`club.policy.seat_rule_enforced` both default `false`, so nothing below changes behaviour until a
club turns it on. Lane: **`community/`**.

---

## Why it exists

Two problems that turned out to be one.

**The leak.** An active membership makes court bookings free (`settlement_mode='membership_covered'`).
Nothing in the system knew *who else was on the court*, so one membership could cover a second, third
or fourth player who never paid — two friends, one membership, half price, indefinitely. The existing
entitlement caps (`diary/entitlement.py`: `max_covered_minutes`, `max_covered_per_day`,
`max_courts_per_day`, one concurrent covered court) limit *how much* a member books; they cannot
express *who it was for*.

**The empty court.** ~1,100 members, many of whom want to play and have nobody to play with.

**An unpaid second player and an empty seat are the same object** — a seat nobody has accounted for.
Account for seats and the leak closes; publish the unaccounted seats and you have Find a Game.

---

## The rule

> **THE SEAT RULE.** A court booking has SEATS. Every seat is held by a covered member (free), a payer
> (owes a share), or is OPEN. **The court's price for that duration is split equally among the seats
> that are not covered.** An OPEN seat unfilled at the cutoff **collapses** onto the booking holder as
> a charged seat.

The club banks **exactly one court fee** per court hour unless every player is a member. Membership
decides *who* pays, never *whether* the court is paid for.

| On court (singles, R150/60min) | Member owes | Other(s) owe |
|---|---|---|
| member + member | R0 | R0 |
| member + non-member | R0 | **R150** |
| non-member × 2 | — | **R75 + R75** |
| member + 2 guests (doubles) | R0 | R75 + R75 |
| member, seat unfilled at cutoff | **R150** | — |

**Split lock.** Shares are recomputed on every seat change **while no seat has been paid**. The first
successful payment sets `diary.booking.split_locked_at`; after that shares never move, so nobody who
has paid can be re-billed and nobody rides free off someone else's payment. Only *covered* members may
take the remaining seats after a lock (they owe nothing, so no split changes).

**Confirmation.** The booking stays `held` while any seat whose resolved method is `online` is unpaid,
and confirms when the last settles — the existing single-order online-hold widened to N orders. A seat
settling at the desk or on the tab is a real debt on the statement and does **not** hold the court.

**Rounding.** `split_minor` divides in integer minor units and gives the remainder to the first seat,
so shares re-sum to the court fee **exactly**. A lost cent is a statement fold that stops
reconciling, which is why it is the first scenario owed (see Verification).

---

## As-built so far

### `community/seats.py` — the money core (the only place the split lives)

| Function | Does |
|---|---|
| `policy(session, club_id)` | the club's switches; no policy row → all OFF (a missing config must never start charging members) |
| `split_minor(total, n)` | the exact split; remainder to the first seat |
| `seat_plan(session, …)` | **pure read** — resolves coverage + shares for a booking without writing, so the booking flow, the sweep and the UI all price a game identically (`shown == charged`) |
| `apply_seat_orders(…)` | one `billing."order"` per un-covered seat; idempotent; re-prices an unpaid seat when the split moves |
| `lock_split(…)` | freezes the split on first payment; idempotent against a replayed webhook |
| `all_prepaid_seats_settled(…)` | the gate the booking's confirmation hangs on |
| `collapse_open_seats(…)` | at the cutoff, an unfilled seat becomes the holder's to pay for |

**This module raises; it does not guard.** Every read in `analytics/` and `insights/` is `_guard`-wrapped
so a panel degrades to empty rather than 500ing — right for a dashboard, wrong here. Per
[GOTCHAS § Reads that lie](GOTCHAS.md#reads-that-lie), a seat whose share silently computes to R0 is a
court given away free that nobody would ever see. Money paths raise `SeatError`; only display helpers
may swallow.

**It invents no coverage logic and no pricing.** Coverage delegates entirely to
`diary.entitlement.court_covered`, so a change to membership windows, court-service eligibility or the
daily caps reaches seats automatically. Price resolution mirrors `_create_order_guarded` exactly —
same `product_id`, duration, club-local instant (so **PEAK** applies) and `resource_id` (so a court's
own peak window wins).

**One extra rule seats needed.** `diary.entitlement._has_overlapping_covered` asks whether the member
already holds a covered **booking** (`booked_by_user_id`) — the only shape that existed before seats.
A seat in someone *else's* game is not a booking of theirs, so `_overlapping_covered_seat` adds the
same question for seats: one member cannot be the free second player in three simultaneous games.

### Schema — `community/schema.py`

See [INVENTORY.md](INVENTORY.md) for the full column list. The shape:

- **A GAME IS A BOOKING.** No `community.game` table. An open game is a `diary.booking` with
  `visibility='open'`; **a seat is a `diary.booking_party` row** (which already had `user_id` nullable,
  `party_role`, `guest_name/guest_email`, `price_id` and `attended` — this adds the money columns).
  A parallel game object would have forked the GiST constraint, the diary grid, reschedule/cancel, the
  unified statement, Client-360 and month-end.
- `community.*` holds only genuinely new domain: `player_invite`, `message` (match chat),
  `match_result`, `play_again` (the **private** would-play-again signal — never rendered, matching
  input only), `favourite`.

---

## What it reuses (do not rebuild)

| Need | Reuses |
|---|---|
| Per-head billing on one booking | the semi-private squad pattern — one owed order per client via `order_line.booking_id` |
| Bill the payer, not the player | `diary.bookings._bill_owner` → `iam.guardian_user_id_for` |
| The guest fee | `_create_order_guarded`'s own note: *"Guests are NON-BILLABLE for now… (Phase 2: charge a guest a fixed fee collected FROM THE GUEST)"* — **this is that Phase 2** |
| Coverage | `diary.entitlement.court_covered` |
| Held-then-paid | `held` + `held_until` → Yoco → `_confirm_held_bookings`; `release_expired_holds` voids the abandoned order; a late payment re-instates via `order_void_is_recoverable` |
| The free week for an invited friend | the **existing** 7-day trial membership (`provider='trial'`, court-only, auto-lapses → PAYG), granted only on a genuinely-new account exactly as `auth/principal.py` gates it — no second free-play mechanism to police |
| No-login links | `marketing_crm/signing.py` (HMAC, context-tagged, carries no PII) |
| Add-a-player UI | `CRMUI.addLessonPlayerModal` (name search + child rows + email fallback) |

---

## Privacy

No community read returns a phone number or an email address — the reason match chat exists at all.
Discovery requires the **explicit** `iam.player_profile.visible_in_community` opt-in (default false):
being findable by 1,100 strangers is a choice a member makes, not something they acquire because we
shipped a feature. **Juniors are excluded from discovery entirely** in this phase; guardian-mediated
junior play needs its own consent design.

---

## Still to build

Invites + `/join.html` · open games (create/join/leave) · the sweep cron
(`.github/workflows/open-games.yml` → `POST /api/cron/open-games`, `OPS_KEY`-guarded, idempotent —
**not** a `render.yaml` cron) · `Widgets.Game` + `Widgets.GameList` · the `booking.js` "Who's playing?"
step · matching, chat, results.

**Phase 2 (not now):** dynamic ratings from results, reliability score, Flex leagues, groups, doubles
matchmaking, Smart Match. **Never:** a social feed — an empty feed across 1,100 members reads as a dead
club, and the problem worth solving is the one WhatsApp doesn't ("who around my level wants to play at
10am tomorrow?").

---

## Verification

**The regression contract:** with `seat_rule_enforced=false`, `python -m scripts.test_all` must still
read the current green baseline in [`CLAUDE.md` § Gates](../../CLAUDE.md) unchanged. Any drift means
the rule leaked into the default path. **Verified green 2026-08-09** against the local sandbox
(`courtflow-dev`) at booking 474 / billing 687 / statement 64, with `python -m db` twice a clean
no-op including `community.schema`.

The baseline is quoted in ONE place on purpose — repeating the numbers here is how they drift apart
(this file already carried a stale 659 for an afternoon).

### Written and green (`test_booking_scenarios`, +69 checks)

*The money core, exercised directly:* `sc_seat_split_covers_the_court_exactly` ·
`sc_member_plus_guest_bills_the_guest_in_full` · `sc_two_payg_split_and_both_must_settle` ·
`sc_open_seat_collapses_onto_the_holder_at_cutoff` · `sc_split_locks_on_first_payment` ·
`sc_seat_rule_off_changes_nothing`.

*Through the live booking path:* `sc_seat_rule_bills_through_create_booking` ·
`sc_seat_rule_holds_the_court_until_every_seat_settles` · `sc_cancelling_a_game_voids_every_seat_debt`
· `sc_an_expired_membership_is_an_uncovered_seat`.

The money core was pinned FIRST, before anything called it: it is the part that decides what people
are charged.

**Writing them found FOUR real bugs before a single club saw them**, all in the "looks obviously
fine" category:

1. **`collapse_open_seats` was not idempotent.** A `collapsed` seat did not count as OCCUPYING its
   seat, so `seat_plan` recomputed `open_count` from the live seats, saw the same empty seat again,
   and the **hourly** sweep would have re-billed the holder on every run until the game started. Fixed
   by putting `collapsed` in `_LIVE_SEAT` — and *not* in `_HOLDING_SEAT`, because a collapsed seat is
   a debt, not a reason to lazy-expire the member's court out from under them hours before they play.
2. **A post-lock joiner got a FREE court.** `seat_plan` read a new seat's NULL share as
   `int(None or 0)`. That is exactly the silent zero this module's header refuses, in the module that
   refuses it. A guessed share would bill someone an amount nobody quoted them; zero hands out a
   court off the back of someone else's payment — so the read now reports the seat as **unpriced** and
   `apply_seat_orders` **raises `SPLIT_LOCKED`**, leaving the product decision (close the game, or
   allow a free joiner once the fee is banked) to the join path where it belongs.
3. **The holder's seat could not say why it had been charged.** `covered` was written on the
   new-order branch but not on the re-price branch, so the one seat that rides the booking's own
   order — the holder's — was left NULL. `covered` is the audit answer to "why was this seat free, or
   not?" long after the member's tier has changed, and NULL is not an answer.
4. **The "stable" seat order was ordering by random UUID.** `_seats` sorted by `created_at, id` to
   make the rounding remainder deterministic — but **`now()` is transaction-stable in Postgres**, so
   every seat inserted by one `create_booking` shares a timestamp to the microsecond and the tie fell
   through to `gen_random_uuid()`. Stable per row, arbitrary between people: the odd cent landed on
   whoever's random id sorted first. Now **the host sorts first** — the organiser carries the odd
   cent, which is the one answer that needs no explanation. The same trap made a scenario pass or
   fail on a coin toss, which is how it was found.

### Still owed

*Diary* — a member past their expiry or over their daily cap is an un-covered seat and is billed ·
an invited friend is trialed once and never twice, and never a Wix import · a junior never appears in
discovery.

*Money* (`test_billing_scenarios`) — each seat is one debt and one order, and a cancel voids every
seat order · a refund restores the split · a collapsed seat respects its court service's own
`payment_modes` and can never become an unpayable at-court debt on a card-only court.

These names are deliberately left out of backticks until they exist: `scripts.audit_docs` treats a
named `sc_…` as a claim that it is already guarded, and that check is the whole reason this doc can
be trusted.

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
> (owes **a share**), or is OPEN. **A SHARE IS A FIXED FRACTION OF THE COURT'S PRICE** — `seat_share_pct`,
> default **50%**, rounded — **not a division of the fee among however many happen to be playing.** An
> OPEN seat unfilled at the cutoff **collapses** onto the booking holder as one charged share.

**Why a fixed fraction and not a split** (owner decision, 2026-08-10). The price a player is quoted has
to survive somebody else joining, leaving, or turning out to be a member. A divided fee does not: your
share moves under you. Needing it not to is what forced a lock, a re-price and a refusal into the first
design — **all three are gone**. It is also one sentence: *"you pay half the court."*

At 50%, **two paying players add up to the court price**. With MORE than two payers the club collects
more than one court fee — deliberately: four people use a court more than two do.

**The club's real prices** (R90 / R150 / R210 / R280 for 30/60/90/120), at 50% rounded up to R10:

| Court | Raw 50% | Share charged |
|---|---|---|
| 30 min R90 | R45 | **R50** |
| 60 min R150 | R75 | **R80** |
| 90 min R210 | R105 | **R110** |
| 120 min R280 | R140 | **R140** |

| On court (60 min, share R80) | Member owes | Other(s) owe | Club takes |
|---|---|---|---|
| member + member | R0 | R0 | R0 (membership) |
| member + non-member | R0 | **R80** | R80 |
| non-member × 2 | — | **R80 + R80** | R160 |
| doubles, 1 member + 3 non-members | R0 | R80 × 3 | R240 |
| member, spare seat unfilled at cutoff | **R80** | — | R80 |

⚠️ **Rounding up costs a pair R10.** Two payers settle R160 on a R150 court, not R150 — because 50% of a
price ending in 0 always ends in 0 or 5, so "round up to the nearest ten" is a small, intended price
rise wherever it lands on a 5. Only the 120-min share (R140) is already exact.

**The quote is frozen per game.** The first time a game's seats are priced, the share is written to
`diary.booking.seat_share_minor`. A later change to `seat_share_pct`, to the rounding rule, or to the
court's own price therefore **cannot re-price a game that is already sold** — and a LATE joiner pays
exactly what the people already in it paid. That is why there is no longer anything to refuse.

**Confirmation.** The booking stays `held` while any seat whose resolved method is `online` is unpaid,
and confirms when the last settles — the existing single-order online-hold widened to N orders. A seat
settling at the desk or on the tab is a real debt on the statement and does **not** hold the court.

**Configuration** (Admin → Setup → Community & games): `seat_share_pct` (0–100, default 50) ·
`seat_rounding` (`none` / `up_5` / `up_10` / `nearest_5` / `nearest_10`, default `up_10`). The screen
shows the resulting **rands** for each of the club's own court durations — "50%" is not an amount, and
an owner should not have to do percentages in their head to discover they just set R110.

---

## As-built so far

### `community/seats.py` — the money core (the only place the split lives)

| Function | Does |
|---|---|
| `policy(session, club_id)` | the club's switches; no policy row → all OFF (a missing config must never start charging members) |
| `share_minor(court_price, pct, rounding)` | **what ONE player pays** — a fixed fraction of the court, rounded. No seat count in the call, by design |
| `seat_plan(session, …)` | **pure read** — resolves coverage + shares for a booking without writing, so the booking flow, the sweep and the UI all price a game identically (`shown == charged`) |
| `apply_seat_orders(…)` | one `billing."order"` per un-covered seat; idempotent; freezes the game's quoted share on first run |
| `lock_split(…)` | stamps `split_locked_at` on first payment (an audit marker + it stops coverage being re-resolved after money moves); the SHARE is frozen separately, on the booking, at first pricing |
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

## The rest of the lane (built 2026-08-09)

| Module | What it does |
|---|---|
| `community/invites.py` | invite by email → signed `play_invite` token → `/join.html`; accepting grants **the existing 7-day trial** and claims the held seat |
| `community/games.py` | `list_open_games` (the feed) · `game_detail` · `join_game` · `leave_game` · `set_visibility` |
| `community/matching.py` | `suggest_players` — a **deterministic** score (level 50, availability 20, format 12, play-type 8, history 10) + the 5-question level quiz |
| `community/chat.py` | match chat; only players in the game may read or post |
| `community/results.py` | `record_result` / `confirm_result` (never self-confirm) · the private `play_again` signal · favourites · `reliability` |
| `community/crons.py` | `sweep_open_games` — remind → release → collapse, per club, each game in its own SAVEPOINT |
| `community/routes.py` | 21 endpoints under `/api/community/*` + `POST /api/cron/open-games` |
| `community/repositories.py` | display reads (`_guard`-wrapped) + the player-profile upsert |

Fired by **`.github/workflows/open-games.yml`** (hourly, 07:00–22:00 SAST, `OPS_KEY`-guarded, loops
until `complete`, **fails the job loudly**) — not a `render.yaml` cron.

**The free week needs no new mechanism.** `grant_signup_trial` already refuses anyone who has EVER
held a subscription, so a second invite is worthless and an imported Wix member can never be trialed.
While the trial runs, the friend's seats resolve covered through the *ordinary* entitlement path —
`community/seats.py` has no idea a trial is involved — and when it lapses the seat rule bills them.
That is the whole of "first seven days free, then they pay".

**Frontend** (GOLDEN RULE — one widget per capability): `Widgets.Game` + `Widgets.GameList`
(`frontend/js/widgets/game.js`), the client routes `#/play` and `#/game/<id>`, and `join.html` served
by the never-sleeps web service. Inviting reuses `CRMUI.addLessonPlayerModal`; paying a seat reuses
`Pay.startYocoCheckout` — there is **no second payment path** for community money. Money is rendered
only for the viewer's own seat, and only their own `order_id` is ever returned.

**A gap in the docs gate, worth knowing.** `scripts.audit_docs` finds emitted events by matching a
*literal* first argument. `community/games.py` emits through a helper (`_emit(session, event, …)`), so
its four events (`game_opened`, `game_seat_taken`, `game_full`, `game_seat_released`) are **invisible
to the audit**. They are in `contracts/events.md` because they were added by hand — nothing would have
caught it if they weren't. Any lane emitting via a helper has the same blind spot.

## Admin surfaces (built 2026-08-10)

Two sections under **Admin → Setup**, plus seven `/api/community/admin/*` endpoints gated on the
existing capabilities (`manage_policy` to change anything, `view_master_diary` to read; a **coach**
may also correct a level).

- **Setup → Community & games** — the two switches, the timings (`open_game_cutoff_hours`,
  `seat_pay_hours`, `guest_trial_days`), and a live "Right now" band (open games · invites out ·
  players findable · **seats unpaid**). **This is the only place the seat rule can be switched on.**
  Before it existed the flags were SQL-only — which is *precisely* how the entitlement caps shipped
  correct and then sat inert for weeks, because everyone assumed a shipped feature was a working one.
  The seat-rule toggle carries an explicit warning that it changes what members pay, and the
  seats-per-format card states the doubles denominator rather than leaving the owner to discover it.
- **Setup → Games & invitations** — three tabs. *Games*: every seated game in the next 14 days with
  seats taken/open and **what is still owed**, which is the number an owner actually scans for ("is
  anyone about to play on a court nobody paid for?"); a row opens the existing `#/event/<id>` record.
  *Invites*: the invite log with whether the free week was granted — the answer to "my friend says
  they never got their free week", otherwise unanswerable without SQL — and Revoke.
  *Players & levels*: correct a level; a self-rating and a coach's assessment are never confused
  (`level_source`).

Guarded by `sc_the_seat_rule_can_be_switched_on_without_sql`.

## The discovery layer (built 2026-08-10 — the front end that was missing)

The engine shipped a day before the screens did, which is the "built but not wired" trap
[FEATURE-FLAGS.md](FEATURE-FLAGS.md) §B is a list of: a member could not set a level, say what kind of
tennis they wanted, or become findable **at all**. Now wired:

- **`#/play/profile` — Your tennis profile.** The 5-question level quiz (answers about what you *do*,
  never "how would you rate yourself?"), what you're after, singles/doubles, a day-part grid for when
  you play, and the **opt-in last** — after you can see what you'd be sharing. A coach-set level is
  shown as such and is not self-editable.
- **`#/play` — the feed, now filtered.** Defaults to **games around my level** (`near=1.5`) plus intent
  chips. Crucially it **degrades to everything** for a member with no level yet: an empty feed reads as
  "no games here" rather than "tell us your level", which is the wrong lesson on a first visit.
- **`#/play/players` — Players for you.** The deterministic suggestions, with an empty state that sends
  you to set up your own profile rather than shrugging.
- **A Home card**, rendered only where `community_enabled`.

### The Home is TWO BLOCKS, because they are two MODES (2026-08-10, Tomo's cut)

The first attempt nested "find a match" inside the booking menu as a fourth product. Tomo split it
differently and better — not by product, but by **whether the member already has someone to play
with**:

| Block | Mode | Depends on |
|---|---|---|
| **Book a session** — court · lesson · class · ball machine | **Synchronous.** Pick a time, pay, done in ninety seconds | somebody ELSE being available: a friend who is free, a coach on shift, a class that is scheduled |
| **Need someone to play with?** — the level quiz, or your level + the marketplace | **Asynchronous.** Post and wait, or browse and join; it may resolve tomorrow | nothing but the members |

Mixing a "wait and see" action into a menu of "done in a minute" actions is what made the page feel
muddled, and a nested version would have taxed the ~1,100 repeat bookers who already know what they
want. **The commercial argument is the sharpest thing said about this feature:** BOOK cannot fill a
Sunday, because there is no coach and no class to sell — but the courts are empty and the members are
there. FIND is the only one of the two with no staffing dependency, so it is the only one that can put
people on court at the times nothing else runs.

**BOOK leads while this is being tested**, so nobody who already knows what they want is disturbed.
The order is **ONE CONSTANT** — `PLAY_BLOCK_FIRST` in `client.js` — because Tomo intends to lead with
FIND once it has earned the position. The block is headed with the need in the member's own words
("Need someone to play with?"), never a product name.

**THE SEAM between the blocks** — an **"Open to the club"** button on a member's own upcoming court
bookings. Someone books a court with a friend on Monday and the friend drops out on Thursday; without
this they hold a court they cannot use and the club gets an email. `set_visibility` already existed
server-side — all that was missing was somewhere to press it. This is what stops choosing the BOOK path
from becoming a support message.

### `play_intent` — what kind of tennis (NEW COLUMN)

`diary.booking.play_intent` — `social` / `practice` / `competitive`. **A separate axis from
`play_format`**, and it has to be: `play_format` (singles/doubles) is a **money** field that sets the
seat count, so conflating them would mean *"I just want a relaxed hit"* could only be said by changing
how many people share the court fee.

It is arguably the most useful thing a member can say. Mismatched intent spoils a session as reliably
as mismatched level — turning up for a friendly hit against someone grinding out a practice match is
the fastest way to stop using a feature like this. Set in the booking flow's "Who's playing?" step,
shown on the game card and the game header, and filterable in the feed.
Guarded by `sc_a_game_says_what_kind_of_tennis_it_is` and
`sc_the_feed_defaults_to_games_around_my_level`.

**The wording was wrong until 2026-08-10, and the axis is Tomo's** — *"some people want to go out and
just hit, and not play a match. some want to play a match and never hit."* The original three options
were `Social hit` / `Practice` / `Competitive`, of which the first TWO read as hitting: the only option
that read as a **match** was "Competitive", so a member who wanted a relaxed game with a score had
nothing honest to pick. The ladder now runs **hit → friendly match → competitive match**, with those
two poles at the ends, a real middle, and every rung stating whether a score is kept — the single fact
that decides whether two strangers had a good afternoon:

| Stored key | Label | Hint |
|---|---|---|
| `practice` | **Just a hit** | Rally and drills, no score |
| `social` | **A friendly match** | We play points, nothing serious |
| `competitive` | **Competitive** | Proper sets, keep score |

**Stored keys are unchanged** — this renamed only what a human reads.

**REQUIRED, but only when the game is OPEN.** Booking with a named friend, the two of you already
settled it between yourselves and a forced tap is pure friction; posting to ~1,100 strangers, a blank
is the mismatch the field exists to prevent. Tomo's own first game was posted with no intent at all,
which is how the gap was found. Enforced in `booking.js` (`needsIntent()`), **deliberately NOT
server-side**: `play_intent` is advisory metadata, not money, and the raise-don't-guard rule belongs on
the money paths.

**ONE vocabulary: `window.CFIntent`** (`frontend/js/crm_ui.js`). It was copied into four files and had
already drifted. It lives in `crm_ui.js` and **not** in `widgets/game.js` because `booking.js` asks the
question in all THREE SPAs while `game.js` loads only in the client one. `CFIntent.word()` returns `""`
for an unset intent so it reads as **absent, never as "social"** — a game posted before this change
must not start claiming something its host never said. `CFIntent.format()` owns the OTHER axis for the
same reason: the admin games list printed `play_format` raw, so an owner read *"practice"*, which on
that axis means **on their own** (a seat count of one), not a hit with somebody.

### DARK MEANS DARK — one gate, not per-route

`community_bp` has a `before_request` that refuses the whole lane with `COMMUNITY_DISABLED` unless the
club has `community_enabled`. Two deliberate exemptions: **`/config`** (how the UI asks whether to show
anything) and **`/admin/*`** (how the owner turns it on — gating the switch behind its own flag would
make the feature unreachable).

It used to be checked one action at a time: `join_game` and `set_visibility` asked, the feed, profile,
chat, results and matching did not. A member of a club that had never switched it on could type `#/play`
and find a working-but-empty feature — worse than an absent one, because it looks *broken* rather than
unbuilt. In one place, a new endpoint is covered by default and has to opt out on purpose.
Guarded by `sc_dark_means_dark_for_the_whole_lane`.

### Styling

Three new components in `frontend/app/app.css` — `.cf-banner`/`.cf-banner-warn` (the held-court
notice), `.cf-item-dashed` (the open seat: dashed so it reads as an invitation, not as a row that
failed to load) and `.cf-chat`. **Everything else reuses the existing vocabulary.** The first cut had
invented `.cf-chip-ok` / `.cf-chip-warn` when the design system already spelled those `.cf-chip.ok` /
`.cf-chip.held` — a second spelling of an existing chip is precisely the drift the GOLDEN RULE exists
to stop, and here it was also *functional*: without the classes, "Paid" and "Awaiting payment" rendered
identically.

## Engine present, NO UI — three surfaces nothing can reach (audited 2026-08-10)

These endpoints exist, are correct, and have **no caller anywhere in the frontend**. They are recorded
here rather than deleted because each is one small screen away from working — but until that screen
exists they must not be described as live, and this is the rot `audit_docs` structurally cannot see
(it checks that a route is documented, not that anything invokes it).

| Surface | Engine | Consequence today |
|---|---|---|
| `POST /games/<id>/result/confirm` | `results.confirm_result` | A reported score can never be confirmed — `community.match_result.confirmed_by_user_id` is always NULL |
| `POST /games/<id>/play-again` | `results.play_again` | `community.play_again` is never written |
| `GET`/`POST /favourites` | `results.add_favourite` | `community.favourite` is never written — "My Tennis Circle" has no UI at all |

**The consequence worth knowing:** `matching.py:110` computes its **history** term from those last two
tables, so **10 of the matcher's 100 points are permanently zero.** This is *not* the silent-zero bug
from [GOTCHAS](GOTCHAS.md#reads-that-lie) — the term is neutral across every candidate, so it cannot
skew a ranking, only fail to sharpen one. It is a real limit on match quality and nothing else.

## Still to build

The two owed money scenarios below (a refund restoring the split; a collapsed seat on a card-only
court). The coach and admin apps do not yet mount `Widgets.Game` — the widget is config-ready for them,
nothing calls it. The three unreachable surfaces above. Phase 2 as listed above.

**Not yet exercised by a real second person.** Every WRITE path is unverified end to end: join, leave,
chat, result entry, the level quiz save, invite acceptance and `join.html`. The scenario harnesses call
Python directly — they never speak HTTP and never render DOM — so five of this lane's bugs (CORS
preflight, a detached node, `card()` argument order, the staff bounce, invented CSS classes) were
findable **only** in a browser. Green gates are not a claim about these paths.

**Done since:** the `booking.js` **"Who's playing?"** step — format (singles / doubles / on my own),
named players, and a "let another member take the spare seat" tick. It appears ONLY where the club has
`seat_rule_enforced` on (`GET /api/community/config`), because `create_booking` ignores seats otherwise
and collecting them would be a lie. Where it appears, the old free-guest step is suppressed: an unbilled
"guest" is precisely the leak the seat rule closes, so offering both on one screen would contradict
itself. The step states plainly that an unfilled seat is added to the booker's bill — that charge lands
at the cutoff whether or not anyone read the screen.

**Phase 2 (not now):** dynamic ratings from results, reliability score, Flex leagues, groups, doubles
matchmaking, Smart Match. **Never:** a social feed — an empty feed across 1,100 members reads as a dead
club, and the problem worth solving is the one WhatsApp doesn't ("who around my level wants to play at
10am tomorrow?").

---

## Verification

**The regression contract:** with `seat_rule_enforced=false`, `python -m scripts.test_all` must still
read the current green baseline in [`CLAUDE.md` § Gates](../../CLAUDE.md) unchanged. Any drift means
the rule leaked into the default path. **Verified green 2026-08-09** against the local sandbox
(`courtflow-dev`) at booking 601 / billing 702 / statement 64, with `python -m db` twice a clean
no-op including `community.schema`.

The baseline is quoted in ONE place on purpose — repeating the numbers here is how they drift apart
(this file already carried a stale 659 for an afternoon).

### Written and green (`test_booking_scenarios`, +69 checks)

*The money core, exercised directly:* `sc_a_seat_share_is_a_fixed_fraction_of_the_court` ·
`sc_member_plus_guest_bills_the_guest_in_full` · `sc_two_payg_split_and_both_must_settle` ·
`sc_open_seat_collapses_onto_the_holder_at_cutoff` · `sc_the_quoted_share_is_frozen_for_the_life_of_the_game` ·
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

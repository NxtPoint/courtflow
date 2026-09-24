# PUBLIC-API — CourtFlow as an API any front end can use (court booking first)

**Status: SPEC, not built (2026-09-24).** Decided with Tomo: CourtFlow becomes a real, versioned API.
NextPoint's own screens move onto it first (so we prove it on live money before anyone else relies on
it), then the same API is opened to Ten-Fifty5 for the academy (phase 2 of that customer, after its
1-month Ten-Fifty5 trial). **Court hire first**; lessons, classes and the admin console follow later.

## Why, in one paragraph
The booking and money rules already live on the server — a request that breaks one is refused
whatever screen sent it. What is NOT ready is the contract: today's endpoints were shaped for our own
screens, change whenever those screens change, have no version, four different error shapes, no
written spec, and no way for a partner to name which club it means. A public API is that contract:
a small set of endpoints we promise not to break.

## Principles (the rules for every v1 endpoint)
1. **A thin layer over the existing domain functions — no new booking or money rules.** `v1` calls
   `diary.bookings.create_booking`, `pricing.*`, `availability.compute_availability`, the gateway
   registry. Rules that today sit in a ROUTE (on-behalf roles, the profile gate, checkout ownership)
   are lifted into shared helpers so old routes and v1 use ONE copy, never two.
2. **Rules that today live only in the SCREEN move to the server** (a partner will not have our
   screens): pick a court for "any court"; refuse a duration the service doesn't offer (even when
   covered by a membership or pack); refuse a slot outside the court's opening hours / time-off.
3. **The club is in the URL** — `/api/v1/clubs/{club_slug}/…`. Explicit, cacheable, and it works
   for a framed page, a partner server and a mobile app alike. The caller must be allowed to act in
   that club (membership, or signup into it) — exactly `auth/principal.py`'s rules, including the
   confinement of outside logins to clubs that accept their issuer.
4. **Money is always `{amount_minor, currency}`.** Never a float, never a bare number.
5. **One error shape:** HTTP status + `{"error": {"code": "SLOT_TAKEN", "message": "…", "details": {…}}}`.
   Codes are UPPER_SNAKE, documented, and never renamed within v1.
6. **Writes are idempotent** — `POST` create/checkout require an `Idempotency-Key` header; a retry
   with the same key returns the first result instead of a second booking or a second charge.
7. **Additive only within v1.** New fields and endpoints are fine; removing or renaming one means v2,
   with notice. Unknown fields in responses must be ignored by clients.

## Who can call it
| Caller | Credential | v1 court booking |
|---|---|---|
| A member in NextPoint's app | NextPoint Clerk session token | ✅ |
| A player inside Ten-Fifty5 (framed page or Ten-Fifty5's own screens) | Ten-Fifty5 Clerk token — verified via `AUTH_EXTRA_ISSUERS`, acts only in clubs whose `accepted_login_issuers` lists it, verified email required (**built 2026-09-24**) | ✅ |
| A partner's SERVER with no user (e.g. "list this academy's courts" for a marketing page) | per-partner API key, scoped to named clubs | later — not needed for court booking, which is always on behalf of a person |

## v1 endpoints — court hire
All under `/api/v1/clubs/{club}`. "Today" = the endpoint it replaces for NextPoint.

| Method + path | Does | Today |
|---|---|---|
| `GET /` | Club facts a front end needs: name, timezone, currency, booking window, cancellation cutoff, which payment methods are on, branding colours/logo | `billing/config` + branding |
| `GET /me` | The caller at this club: profile completeness, membership (what it covers, caps, window), packs and their balances | `me/plan`, `bundles/wallets` |
| `PATCH /me` | Complete the minimum profile (first name, surname, phone, marketing consent) — the gate a first booking hits | `_min_profile_gate` |
| `GET /court-services` | Each court service (e.g. Hardcourt, Clay): offered durations with price, peak price and peak windows, payment methods, equipment add-ons | `diary/services`, `durations`, `equipment` |
| `GET /availability?service=&from=&to=&duration=` | Free slots: court, start, end, **the price THIS caller would pay** (covered / pack / PAYG, peak applied) | `diary/availability` |
| `POST /quotes` | Price a specific booking without making it: total, line items (court + equipment), how it can be paid, whether covered — and the same refusals a real booking would give | new (read-only) |
| `POST /bookings` | Book. Body: `service_id`, `court_id` or `"any"`, `starts_at`, `duration_minutes`, `payment_method` (`card`/`at_club`/`account`/`pack`), `addons[]`, `players[]`. Returns the booking + whether payment is due. | `POST diary/bookings` |
| `GET /bookings?from=&to=` | The caller's own bookings | `GET diary/bookings` |
| `GET /bookings/{id}` | One booking with its money story and what the caller may do (`can: {cancel, reschedule, pay}`) | `me/bookings/{id}` |
| `POST /bookings/{id}/reschedule` | Move time and/or court (same money guards as today) | `PATCH diary/bookings/{id}` |
| `POST /bookings/{id}/cancel` | Cancel (fees/refunds per club policy) | `diary/bookings/{id}/cancel` |
| `POST /bookings/{id}/checkout` | Start card payment. Body: `return_url`. Returns `{provider, redirect_url, expires_at}` from the CLUB's own provider | `yoco/checkout` |
| `POST /bookings/{id}/promo` | Apply a promo code | `billing/promo/apply` |

Out of v1 (later versions): lessons, classes, open games / seat splitting, dependants, on-behalf
booking by staff, statements, the admin console.

## Payments — the club's OWN account, whichever provider
- **Provider-agnostic checkout.** `checkout` asks the club's configured gateway (`billing.gateway`
  registry) — Yoco today, **PayPal next** — for a hosted payment page. The front end only ever
  redirects to `redirect_url`; it never talks to a payment provider itself. Confirmation arrives by the
  provider's webhook to CourtFlow (per club), exactly as Yoco works now, so the booking confirms even if
  the player closes the page.
- **Money goes to the club, never to Ten-Fifty5 or the platform.** Ten-Fifty5's PayPal collects only
  the academy's Ten-Fifty5 subscription.
- **Each club connects its own account on its setup screen.** A new `club.payment_account`
  (provider, account details; secrets encrypted with `pgcrypto` under a platform key held in Render
  env). NextPoint's current env keys stay as its fallback so nothing live moves. For PayPal, check
  PayPal's partner onboarding first (the club approves us; we never hold its password) — pasted API
  credentials are the fallback.
- **`return_url` is allow-listed per club** (`club.policy`), or checkout becomes an open redirect.
- Currency comes from the club (`ZAR` for NextPoint); a PayPal club can bill in its own currency.

## How NextPoint moves onto it (gradually)
1. Build v1 beside the old endpoints (nothing removed).
2. `booking.js` court path gets a switch (`club.policy.court_booking_via_api_v1`, default off). Turn
   it on for NextPoint, watch live bookings + card payments for a week.
3. Move the member's "my bookings / cancel / reschedule" the same way.
4. Only then retire the old court-hire endpoints (they stay for lessons/classes until those move).

## Guarding it
- Every v1 endpoint gets harness scenarios that call it **over HTTP** (Flask test client), not the
  Python underneath — the community lane's five browser-only bugs are why.
- A written OpenAPI file (`docs/specs/public-api-v1.yaml`) is the contract; `scripts/audit_docs`
  gains a check that every `/api/v1/` route is in it and vice versa.
- Rate limiting per user/IP on v1 (none exists today).

## Progress
- **Step 2 done (2026-09-24):** member court bookings now enforce opening hours, time-off and the
  length menu on the server; `resource_id="any"` is picked server-side; court playmates are no
  longer dropped by the route (`sc_a_member_court_booking_obeys_the_rules_the_screen_used_to_enforce`).
  The playmates fix is in the ROUTE, so it is not yet under a scenario — the HTTP-level harness (step 3)
  must cover it first. Still open from the gaps below: `billing/config`'s query-string club.

## Gaps found while mapping today's endpoints (fix as part of this)
- **Named playmates on a COURT booking are dropped by the server** — `diary/routes.py` only reads
  `extra_clients` for lessons, while `create_booking` would seat them. Only reachable when the
  community switch is on (it is off in production), but it would under-bill the day it is flipped.
- **Court opening hours / time-off are not checked when a booking is created** (only overlap is).
  The app only offers open slots, so only a hand-built request can book outside hours.
- `GET /api/billing/config` takes the club from the query string, not the caller.
- Error shapes: four different ones today (principle 5 fixes it for v1).

## Order of work
1. This spec agreed → the OpenAPI file for court hire.
2. Lift the route-only rules into shared helpers; add the server-side rules from principle 2
   (fixes both gaps above).
3. Build v1 court endpoints + HTTP-level scenarios.
4. Switch NextPoint's court booking onto v1 (flagged), verify live.
5. `club.payment_account` + setup screen; PayPal gateway.
6. Open to Ten-Fifty5 (their screens or a framed page — both work on the same API).

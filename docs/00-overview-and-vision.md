# 00 — Overview & Vision

## 1. The problem we're solving

NextPoint Tennis (Killarney Country Club, Houghton, Johannesburg) currently runs on **Wix**. Wix
gives them disconnected booking widgets (court bookings, lesson bookings, class bookings are each
separate Wix "services"), no unified diary, weak data ownership, and a marketing site that renders
through JavaScript. We are replacing all of it with a platform we own.

But the real ambition is bigger than one club: **build a tennis‑club management platform we can sell**
to other coaches, academies, and clubs. So we build NextPoint as **tenant #1** of a multi‑tenant
product, not as a one‑off site.

## 2. What "good" looks like (the Playtomic + coaching frame)

Playtomic nailed the *court‑booking* experience for padel: open the app, see live availability, book
and pay a court in seconds. We match that for tennis **and add the two things Playtomic doesn't do
well**:

1. **Book a lesson with a specific coach** — pick a coach, see *that coach's* real availability,
   book a 1:1 or group lesson, get a confirmation.
2. **Join a class** — recurring programmes like **Cardio Tennis**, junior beginner/intermediate
   squads, high‑performance sessions — with capacity, waitlists, and enrolment.

All three booking types live in **one diary** that members, coaches, and admins share.

## 3. What we replicate from the live Wix site

From `www.nextpointtennis.com` (captured 2026‑06‑20), the current offering we must carry over:

**Booking services & pricing tiers**
- **Hard Court — Member Bookings** ("unlimited court bookings from R220/month" — a membership plan).
- **Hard Court — Visitor Bookings** (from R150, 1–2 hr).
- **Member Guest Booking** (from R80, 1–2 hr).
- **The Clay Court** — "the only clay court experience in Gauteng" (a premium/scarce resource — model
  it as a distinct court type with its own price).
- **8 resurfaced hard courts** + the clay court (9 bookable court resources at NextPoint).

**Coaching & lessons**
- One‑on‑one and group lessons ("Expert Coaching", tailored to level).
- **High Performance Program** (ATP‑certified coaches, junior development).
- Coaching team with named coaches: **Neville Godwin** (Program Director, 2017 ATP Coach of the Year),
  **Ross Nemeth** (Head Coach) — coach profiles matter for the "book a named coach" flow.

**Classes / programmes**
- Junior Intermediate Tennis (R150), Junior Beginner Tennis (R120, 30 min).
- Saturday/Wednesday Social.
- **Cardio Tennis** (named by Tomo as a target class type).

**Funnels & content**
- "Claim My FREE Lesson" complimentary‑lesson funnel (lead gen — keep it, wire to Klaviyo).
- Testimonials, coaching‑team page, contact form, vacancies/jobs page.

**Contact / location** (carry to schema as club profile + to schema.org `SportsActivityLocation`):
Killarney Country Club, 60 5th Street, Houghton Estate, Johannesburg 2191 · 076 990 7439 ·
info@nextpointtennis.com.

> ⚠️ Currency: NextPoint prices are **ZAR**. The platform must be multi‑currency per club (1050 is USD).
> Store currency on the club, not hardcoded. See `02-data-model-multitenant.md`.

## 4. Personas

| Persona | Needs | Key flows |
|---|---|---|
| **Member** (pays a monthly membership) | Fast court booking, book lessons/classes, see "my bookings", manage membership | Book court → confirm; book lesson with coach; enrol in Cardio Tennis; cancel/reschedule |
| **Visitor / Guest** (non‑member) | Book and pay for a court without membership, claim free lesson | Visitor court booking; guest booking (lower price, member‑hosted); free‑lesson lead capture |
| **Coach** | See *my* diary, my lessons, my classes; mark attendance; reschedule/cancel; lesson notes | Coach diary view; accept/decline; manage availability; class roster |
| **Club admin** (NextPoint front desk / manager) | Run the whole diary, manage courts/coaches/classes/pricing/members, take pay‑at‑court, end‑of‑month billing | Master diary; resource & price config; member management; invoicing/settlement |
| **Platform admin** (us / Tomo) | Provision new clubs, white‑label, monitor, support | Tenant provisioning; theming; cross‑club ops |

## 5. Scope — MVP vs later

**MVP (Phase 1–3, this build):**
- Multi‑tenant foundation (club #1 = NextPoint), Clerk auth, roles.
- Public marketing site rebuilt on Render with **SEO‑preserving migration**.
- The **unified diary**: court booking, lesson booking (named coach), class enrolment; edit / cancel
  / reschedule; recurrence; conflict prevention; capacity & waitlists.
- Membership / visitor / guest pricing tiers; settlement = **pay‑at‑court + monthly account** at
  launch (no mandatory online card yet).
- **Klaviyo confirmations** for every booking/lesson/class.
- Club admin console + coach diary view.
- Free‑lesson lead funnel → Klaviyo.

**Phase 4 (designed‑in now, built next):**
- **Online payments via Yoco** behind the gateway abstraction (Tomo has keys — can be brought forward
  to MVP as a fast vanilla build if desired).
- PayPal adapter as a second provider.

**Later (Phase 5+):**
- Self‑serve club onboarding & white‑label theming polish; billing the *clubs* (platform subscription).
- Leagues / ladders / tournaments, open‑match / "find a partner" (Playtomic‑style social), ratings
  (UTR sync — note 1050 already stores UTR), packages/credit bundles for lessons, gift vouchers,
  Apple/Google wallet passes, native app wrapper.
- Optional bridge to 1050 video analysis (a member's match video → 1050) — kept as a *future*
  cross‑sell, not a dependency.

## 6. Guiding principles (inherited from 1050, they work)

1. **Idempotent, boot‑time schema** — no migration framework; `init()` + `_ensure_*` on boot.
2. **One normalized event path per concern** — e.g. all payment providers feed one
   `apply_payment_event(provider)`. New provider = new adapter, not new core logic.
3. **Thin presentation views** (gold‑style) for dashboards; never aggregate in the client.
4. **Fire‑and‑forget with reconciliation** for anything slow; crons sweep stragglers.
5. **Build, own the data, keep providers swappable.** Klaviyo for email, Clerk for identity, gateway
   for money — all behind our own boundary so none of them lock us in.
6. **Multi‑tenant by construction** — `club_id` on every domain row; never a global query without it.

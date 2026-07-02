# FEATURES — the white-label feature & function catalogue

A single, plain-language list of **everything the platform does**, grouped by area, for a
white-label tennis-management product sold to clubs & owners. This is the "what can it do" sheet;
for the deep rules see [BUSINESS-RULES.md](BUSINESS-RULES.md), for the exhaustive endpoint/table/page
list see [INVENTORY.md](INVENTORY.md), for what's left see [OUTSTANDING.md](OUTSTANDING.md).

> **White-label principle:** nothing is hardcoded. Every commercial value — prices, durations,
> plans, packs, commission, access windows, branding, policy — is **owner-configured data**, so the
> same engine runs any club under its own brand, domain, currency and rules.

Legend: **✅ automated-test coverage** (a scenario in `scripts/test_*_scenarios.py`) · **🔭 manual/UI
test** (per `TESTING.md`) · **🌐 needs a live key/HTTP** (Yoco webhook, SES, Clerk).

---

## 1. Multi-tenancy & white-label
- Multiple independent clubs on one platform; **every row is `club_id`-scoped** (a club can never
  see another's data). 🔭
- Per-club **branding** (name, colours, logo, OG image), **location**, **currency**, **timezone**,
  **policy** (booking window, cancellation cutoff, guest rules, allowed payment modes). 🔭
- **Host-switched** serving: a marketing host shows the public site; a club host shows that club's
  branded site + portal. 🔭
- **Provision a new tenant** from a template (`scripts.provision_club`). 🔭
- Roles: **platform-admin**, **club-admin/owner**, **coach**, **member**, **guest**. 🔭

## 2. Identity, onboarding & accounts
- **Clerk** sign-in; `iam.user` links by email so an invited/seeded person links on first login. 🌐
- **Auto-member:** any new authenticated user becomes an active member of the club (lands in the
  portal on PAYG). 🔭
- **Owner onboarding wizard** — club profile, location, branding, policy, courts, hours, services &
  prices, invite coaches; gated first-run redirect. 🔭
- **Coach onboarding (4-step)** — profile/photo/bio, languages/qualifications/visibility,
  review-bookings preference, weekly hours (creates their bookable resource), services/rates +
  classes + packs; fully pre-filled on return. 🔭
- **Member account** — profile/demographics (email = identity, read-only); **dependents/children**
  (login-less child players billed to the guardian). 🔭

## 3. The diary — booking engine (the heart)
- Book a **court**, a **lesson** (named or "Any" coach), or **enrol in a class**. ✅
- **No double-booking** — a Postgres GiST exclusion constraint guarantees one booking per resource
  per time; concurrent clashes → exactly one wins (`SLOT_TAKEN`). ✅
- **Lessons reserve a court** — availability = where a coach **and** a court are both free
  (coach ∩ court); a lesson auto-holds a court (two rows, one order). ✅
- **A coach's class blocks their lessons** — a class the coach runs makes them unavailable for a
  lesson at that time (read + write guarded; a class reserves no court). ✅
- **30-minute start cadence** — bookings can start on the hour or half-hour (configurable finer per
  club); duration sets the length. ✅
- **Reschedule** — atomic move, conflict-checked; a failed move preserves the original slot; a
  lesson's court moves with it. ✅
- **Cancel** — frees the slot (coach **and** court for a lesson); policy cancellation-cutoff /
  no-show fee for members; admins/coaches override. ✅
- **Classes** — owner/coach create class types + schedule **recurring or one-off** sessions;
  **capacity + waitlist** (auto-promote the next person on a cancel); rosters + attendance. ✅
- **Book-on-behalf** — a coach/admin books FOR a client (auto-confirms; client can reschedule/cancel).
  **Book-for-a-child** — a parent books for a dependent, billed to the parent. 🔭
- **Booking window / lead time / cancellation cutoff** from club policy. ✅ (window) 🔭 (cutoff UI)
- **Lazy hold-expiry** — abandoned online holds are released on the next availability/booking read
  (no paid cron). ✅ (implicit) 🔭
- **Master diary** — a unified resource-timeline calendar for the owner (courts/coaches/classes). 🔭
- Every booking has a downloadable **`.ics`** ("Add to calendar"). 🔭

## 4. Lesson approval lifecycle (per-coach)
- A coach can require approval of lessons clients book with them (`review_bookings`). ✅
- Client self-books a review-coach → **`requested`**, reserving **nothing** (no court/order/payment). ✅
- Coach **accepts** → court auto-assigned, settles → `confirmed`. ✅
- Coach **proposes** a new time → **`proposed`** (client accepts/declines/withdraws). ✅
- Coach **declines** → `cancelled`. ✅
- On-behalf bookings always auto-confirm (no acceptance step). ✅
- Lifecycle notifications: requested / proposed / accepted / declined. 🔭

## 5. Pricing & the three purchasing models
- **Per-duration PAYG** — one price per offered duration (e.g. court 30/60/90/120; lesson 30/60);
  the booking picker only ever offers durations the owner has priced. ✅
- **Membership (term plans)** — configurable (label, amount, term months); an active membership makes
  **court** bookings free; admin can also grant/revoke manually. ✅
  - **Tiers + access windows** — a tier can be time-boxed (e.g. weekdays 06:00–17:00); coverage is
    enforced **per slot** (free inside the window, PAYG at peak) so peak slots price correctly. ✅
  - **Self-cancel** — a member can cancel a paid membership from their Account (the free trial just
    lapses); their **plan + access window + renew date** show on the profile ("Your plan"). 🔭
  - **Free week** — new members auto-granted a 7-day courts-free trial (one-shot, idempotent,
    auto-lapses). ✅
- **Tokens / bundles (unit/minute packs)** — prepaid packs across court/lesson/class; balance held in
  **minutes** so one pack covers any length (a 90-min booking off a 60-min unit = 1.5 sessions);
  **atomic draw-down**, **idempotent credit-back** on cancel, expiry/use-it-or-lose-it; coaches
  configure their own lesson packs. ✅
- **Catalogue lifecycle** — every price/pack/plan carries a status: **active / dormant** (hidden but
  kept) **/ retired** (soft-deleted); customers only ever see active. 🔭
- **Unified lifecycle (Active / Deactivated / Terminated)** — services, memberships and coaches share
  ONE lifecycle vocabulary, with a filter bar, per-row Deactivate/Reactivate/Terminate actions and
  status chips. Memberships derive their state from their term plans' status. 🔭
- **Real deletes with safe archive** — deleting a **coach** or **court** that has no bookings/financial
  history **hard-deletes** it; one with history is **archived** instead (kept, hidden from the active
  list) so nothing referencing it breaks. 🔭

## 6. Payments & refunds
- **Settlement modes:** at-court (desk), monthly account (ledger tab), online (Yoco), membership-
  covered (R0), token (R0), free/complimentary. ✅
- **Per-service & per-membership-tier payment options** — the owner chooses which modes each lesson/
  court/class **and each membership tier** allows (layered: tier pref → membership default → club's
  globally-enabled methods). 🔭
- **The one payment rule** — more than one allowed mode → the client **chooses**; exactly one
  non-online mode → checkout happens **immediately** (no prompt); online → Yoco. The booking/buy flow
  hides the chooser when there's a single way to pay. ✅
- **Memberships & packs buy offline** — not just online: an at-club / month-end purchase opens an owed
  order and **activates the membership or grants the pack immediately**; online holds until the webhook. ✅
- **Online payments — Yoco** hosted checkout (card + Apple/Google/Samsung Pay); held booking →
  verified webhook → paid + confirmed. ✅ (settlement core) 🌐 (live webhook/signature)
- **Idempotent settlement** — a replayed payment/webhook never double-charges, double-confirms, or
  double-grants. ✅
- **Desk payments** — record cash/card/EFT at the desk; idempotent on a receipt id. ✅
- **Reconciliation** — recover a missed webhook by asking Yoco and replaying the charge. 🌐
- **Receipts** — a printable/PDF receipt for online and desk payments. 🔭
- **Refunds** — admin direct ("refund only" / "refund & cancel" frees the slot) and a **client
  refund-request → admin approve/decline** workflow. ✅ (request lifecycle) 🌐 (Yoco execution)
- **Two gates** for online pay: a global flag + a per-club Settings toggle. 🔭
- **Unified client statement** — ONE reconciled "what you owe" (the sum of unpaid orders, no double-
  count), **grouped by category** (Coaching / Court hire / Classes / Membership / Session packs / Other)
  with +/− drill-down per line (coach name, date). **Pay all** OR **part-settle** by ticking individual
  lines; **settle online anytime** via Yoco. Admins can **void / write-off** a line from the People 360
  drawer; coach arrears and orders stay in lockstep so commission accrues exactly once. ✅

## 7. Commission & coaching-settlement engine
- Monetise each coach via **rent and/or commission %**, freely combinable, per coach. ✅ (%) 🔭 (rent UI)
- **Scoped, dated rules** — resolution `coach+product > product > coach > club > 0`, most-specific
  then latest-effective. ✅
- **Commission accrues on collection** (online at payment; arrears when the coach marks collected);
  **ex-VAT base**; never deducts the gateway fee from the coach; no commission on free courts. ✅
- **Idempotent splits** — a replayed payment writes no second split. ✅
- **Coach statement** (per-client paid + owed = net; mark-collected; **discount / write-off**) and a
  mirrored **client statement**. 🔭 (UI) — engine exercised via commission ✅
- **Owner financial cockpit** — revenue by service, commission owed + rent per coach, membership MRR,
  refund-aware. 🔭
- *(Deferred: refund clawback split, coach-payout objects, scheduled rent accrual — see OUTSTANDING.)*

- **Role-focused nav** — each role lands on and sees only its own surface: members/guests get
  **Home · Account**, coaches get their **Coach** console, owners get **Admin · Settings** (staff no
  longer see the client screens). 🔭

## 8. Self-service consoles — three drill-through SPAs
Each role has its own mobile-first SPA on ONE design system (`frontend/app/app.css`, `cf-*`), rebuilt
2026-07-02. **Drill-through everywhere** — every list row opens its full story, no data dumps.
**Golden rule:** exactly ONE booking capability per app (the "event story"), reused from everywhere
(calendar, client record, money) — never a second booking sheet.
- **Client** (`app.html` + `client.js`, at `/`,`/portal`,`/app`) — **ONE page, no bottom nav**
  (Book from Home tiles; avatar top-right → profile). Green profile ribbon (name + email + membership
  + Edit profile / Manage membership). Home = book tiles + **Your sessions** (all, upcoming + past) +
  **Billing by category** (month nav → category → items → the booking story / receipt) + Plan & credits.
  Every booking/charge drills to its **booking story** (`GET /api/me/bookings/<id>`), every line to its
  order/receipt. My-Bookings needs-attention (accept/decline a proposed time) + **add-to-calendar**. 🔭
- **Coach** (`coach_app.html` + `coach_app.js`, at `/coach`; bottom nav **Home · Schedule · Clients ·
  Money · Setup**):
  - **Home** = business cockpit KPIs (**Total billed** + net-of-commission earnings / lessons / hours /
    fill-rate) + the **lesson approval queue** + today + book-for-a-client. 🔭
  - **Schedule** = a **weekly calendar** (week-of-today, prev/this/next) — tap a lesson → the event
    story; tap a class → its roster. 🔭
  - **Clients** = list → the **full client record**: name + **Total billed**, then **BY SERVICE**
    ("Private lesson · 60 min · 3 · R750") → sessions → each → the event story. Each session shows its
    REAL money state (paid / owed / **written-off** / **discounted** / covered). 🔭
  - **Money** = account balance/rent/net + disputes + per-client rollup → record + activity log.
    **Setup** = Services (lifecycle Deactivate/Reactivate/Terminate + filter) + **Classes**
    (create / schedule / roster) + club-commission card + Edit-profile & Weekly-hours (as pages). 🔭
  - **THE ONE COACH EVENT STORY** (`#/event/:id`, `GET /api/coach/bookings/<id>`): client + contact,
    when, court, charge, **coaching line**, players + attendance, and the actions — accept / propose /
    decline / reschedule / cancel / mark-completed / no-show **+ Mark collected / Discount / Write off**
    (the money is managed right here) + add-to-calendar. 🔭
- **Owner / Admin** (`admin_app.html` + `admin_app.js`, at **`/admin-app`** — **IN PROGRESS**; the
  classic `/admin` console stays live until sign-off). **Responsive**: bottom-nav on mobile, **left
  side-rail on desktop**. Nav **Home · People · Money · Diary · Setup** (+ Insights). **Home = a
  command center** surfacing all four owner focuses, each drilling to its section: **Today at the club**
  (live diary), **Money** (owed to the club / net revenue / coach settlements due / active members),
  **People needing attention** (new signups / pending coach invites / expiring memberships), **To
  approve / decide** (pending refund requests) — via `GET /api/admin/home`. People/Money/Diary/Setup/
  Insights build out per `docs/specs/ADMIN-REDESIGN.md` (People → unified person 360; Money → per-coach
  settlement drill; Diary → the resource-timeline + classes; Setup → all club config in-app; Insights →
  the Overview). The classic Admin console (Operate/Configure + Settings at `/settings.html`) remains
  the full working surface until the SPA reaches parity. 🔭

## 9. Notifications, calendar & CRM
- In-app **bell + inbox** for every member, driven off the event feed: booking confirmed, payment
  receipt, membership active, pack activated, refund requested/decided, class enrolled/waitlisted/
  spot-open, coach invited, lesson requested/proposed/accepted/declined. 🔭
- **Child → guardian** notification routing. 🔭
- Booking **`.ics` calendar** (in-app now; email attachment when SES is live). 🔭
- **Transactional email — per-club branded, multi-tenant SES** (built, dark until keyed 🌐): confirmations
  + invites go out from **one verified CourtFlow domain** but under **each club's own From name and
  Reply-To**, so a new tenant needs no new sender verification; booking emails **attach the `.ics`**
  calendar. Self-gates on creds → until AWS keys land, in-app notification only (see `SES-SETUP.md`). Plus
  **Klaviyo** lifecycle/marketing — same feed, dark until keyed. 🌐
- **Consent** capture; no minor PII in marketing payloads. 🔭

## 10. Business Overview analytics
- Owner dashboard: visits / unique / new-vs-returning, traffic sources, top pages, **by-country /
  device / time-on-site**, customers, bookings, revenue, settlement mix, NPS. 🔭
- **First-party page-view beacon** (no cookies, no third parties); geo via Cloudflare header with
  Accept-Language fallback. 🔭
- Embedded as the admin "Overview" tab + standalone page; platform-admin can filter by club. 🔭

## 11. Public site & SEO
- Host-switched, branded **marketing site** on the design system (photo-rich, conversion-focused). 🔭
- **Blog/SEO** build, sitemap/robots, branded 404, Wix→Render **301 redirect** map for migration. 🔭
- Public **contact form** (emails the club via SES; logs the lead if email is dark). 🌐

## 12. Operations & resilience
- **Idempotent boot DDL** — `python -m db` twice is a no-op; no migration framework. ✅ (gate)
- **Free-tier resilience** — keep-warm pings, 70s frontend API timeout (no endless spinners), lazy
  hold-expiry + on-read accrual + reconcile sweep instead of paid crons. 🔭
- **Cron handlers exist** (reminders / capacity-sweep / monthly-invoice / membership-refill /
  reconcile) — re-enable schedulers off the Free plan. 🔭

---

## 13. Automated test coverage (the scenario harnesses)
Three rollback-only scratch-DB harnesses drive the **real** engine code and assert invariants. Run all:
**`python -m scripts.test_all`** (or each: `test_booking_scenarios`, `test_billing_scenarios`,
`test_statement_reconciliation`).

**Booking engine — `scripts/test_booking_scenarios.py` (43 checks):** court book/cancel/double-book/
reschedule (+ conflict preserves original) · lesson = coach + court rows, collapsed to one line ·
lesson needs a free court · **coach∩class conflict** (read + write) · 30-min slot granularity ·
class enrol/capacity/waitlist/promote · lesson approval lifecycle (request → accept/decline/propose →
client accept).

**Commercial engines — `scripts/test_billing_scenarios.py` (118 checks):** settlement per mode
(at-court desk, online held→paid, monthly-account ledger) · **idempotent payment replay** · commission
30%/40% scoping + accrual + idempotency · token pack buy→activate→**unit/minute draw-down**→credit-back
+ NO_TOKEN · membership coverage (R0) + **access window** inside/outside + trial idempotency · refund-
request lifecycle (create/duplicate/list/decline/NOT_PENDING) · membership/pack **offline buy** + the
per-tier/per-service payment-mode resolution · **refund clawback** split · membership-cancel & cancel-
booking **void the order** · **transaction log** + **dispute routing** (coach vs club) · lockstep
desk-pay & **void clears arrears** · abandoned-checkout **reclaim on read** · the client + coach
**event/booking stories** · the **client BY-SERVICE breakdown** (incl. written-off + discounted per-
session state, billed vs effective, total-billed unchanged by write-off/discount).

**Unified statement — `scripts/test_statement_reconciliation.py` (35 checks):** no double-count
(orders only, never ledger + arrears too) · pay-all-once · **partial settle** (selected lines only) ·
reclaim of an abandoned settlement · membership-covered R0 never owed · **void / write-off** · arrears
↔ orders lockstep (commission once) · pack-offline owed · category + coach-name grouping.

**What the harnesses do NOT cover (tested another way or not yet):**
- **Live HTTP/keys** — Yoco webhook signature verify + real refund execution (offline tests in
  CLAUDE.md "Verifying"), Clerk JWT auth, SES/Klaviyo sends. 🌐
- **Frontend/UI behaviour** — the SPA flows are validated manually via [TESTING.md](TESTING.md). 🔭
- **Reverse class/lesson scheduling guard** (scheduling a class over a coach's existing lesson),
  reminders/scheduled accrual, and the items in [OUTSTANDING.md](OUTSTANDING.md).

> **Honest status:** the core booking + money engines are now under automated regression tests; auth,
> the live gateway round-trip, email, and the UI are validated manually / by the offline Yoco suite.
> When a new bug is found, the fix should add a scenario here so it can't regress.

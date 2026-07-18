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
  lesson at that time (read + write guarded). A class can **optionally reserve a court** too
  (`class_session.court_resource_id` → a court-blocking booking, freed on cancel). ✅
- **Admin can create a walk-up client + issue a membership/pack offline** — People → New client
  (**a name + a valid email are required** — the email is the identity key + pay-link/receipt address)
  + Issue package (membership OR pack, owed/PAYG or mark-paid, start date); reuses the offline-purchase
  engine (no parallel money logic). ✅
- **Admin ad-hoc invoice builder** — Money → New invoice: bill a client for a configured **service ×
  how-many** (price re-derived server-side, tamper-proof) and/or a **custom fee**, less an optional
  rand discount → ONE owed `billing.order` on their unified statement (settleable online), and the
  client is emailed a `/portal` pay link. Not booked to the calendar (`POST /api/admin/clients/<id>/invoice`). ✅
- **Coach/admin back-capture of a PAST session** — the SAME on-behalf booking flow, opened for a past
  date ("Log a past session"): a lesson/class that already happened is billed to the client + credits
  the coach, with no calendar hold. Staff-only (`allow_past`, role-gated); a member can never back-date. ✅
- **Client monthly Activity view** — one screen: the month's bookings, spend by category, and the
  outstanding balance to settle (`GET /api/me/activity`). ✅
- **15-minute start cadence** — bookings can start on any quarter-hour (`BOOKING_GRANULARITY_MIN=15`,
  configurable per club); duration sets the length. ✅
- **Reschedule** — atomic move, conflict-checked; a failed move preserves the original slot; a
  lesson's held court is **auto-reassigned** to a free court at the new time; you can't extend a
  **paid** booking (`PAID_CANNOT_EXTEND`), and moving a **membership-covered** booking to a time the
  plan doesn't cover is blocked (`NOT_COVERED_AT_NEW_TIME`). A **member** rescheduling a lesson must
  land **inside the coach's published hours** (`OUTSIDE_COACH_HOURS`). ✅
- **Cancel** — frees the slot (coach **and** court for a lesson); a late cancellation raises a
  **fee order** when club policy applies; cancelling a **paid** booking prompts the client to request
  a refund; admins/coaches override. A member/guest **can't cancel a lesson/class that has already
  started** (`CANNOT_CANCEL_STARTED`); admins/coaches can. ✅
- **Classes** — owner/coach create class types + schedule **recurring or one-off** sessions;
  **capacity + waitlist** (auto-promote the next person on a cancel); rosters + attendance. ✅
- **Book-on-behalf** — a coach/admin books FOR a client (auto-confirms; client can reschedule/cancel)
  through the **ONE shared booking widget** (golden rule) used across client, coach and admin — coach-
  locked to themselves for a coach, coach-pickable for the owner — with on-behalf **pack auto-draw**
  (lesson = coach-scoped wallet, class = coach-agnostic) and no Yoco redirect. **Book-for-a-child** —
  a parent books for a dependent, billed to the parent. 🔭
- **Semi-private (squad) lessons** — a lesson service can carry **more than one client on one slot**
  (`billing.product.max_clients`), each billed **per head** (one owed order per client at the service
  price, linked to the ONE booking via `order_line.booking_id` — never summed onto a single payer; a
  child's head bills the guardian). Add players **up front** ("add players" step) or **later**
  (`POST /api/diary/bookings/<id>/add-player`, for late squad confirmations), with cap / duplicate /
  non-lesson guards and a **who-can-be-added** security boundary (a member may add club members + their
  OWN kids, never a stranger or another family's child; staff may add any in-club member). Each client
  sees the lesson **once, at their own head**, in their 360; **cancel voids every client's order**. ✅
- **No completing the future** — a booking can't be marked completed / no-showed before it has
  started (`CANNOT_COMPLETE_FUTURE`). ✅
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
  the booking picker only ever offers durations the owner has priced. A billable booking with **no
  configured price is refused** (`PRICE_NOT_CONFIGURED`) rather than silently opening an R0 order. ✅
- **Coach / per-service rate cards applied exactly (strict two-tier)** — a service uses the coach's
  **own** active product if they have one, **else** the shared (NULL-coach) product; the two are
  **never merged**. So a lesson/class always bills that coach's configured rate — a client enrolled on
  one coach's class is never charged another coach's cheaper rate. ✅
- **Membership (term plans)** — configurable (label, amount, term months); an active membership makes
  **court** bookings free; admin can also grant/revoke manually. ✅
  - **Tiers + access windows** — a tier can be time-boxed (e.g. weekdays 06:00–17:00); coverage is
    enforced **per slot** (free inside the window, PAYG at peak) so peak slots price correctly. ✅
  - **Self-cancel** — a member can cancel a paid membership from their Account (the free trial just
    lapses); their **plan + access window + renew date** show on the profile ("Your plan"). 🔭
  - **Free week** — new members auto-granted a 7-day courts-free trial (one-shot, idempotent,
    auto-lapses). ✅
  - **Configurable trial-as-a-tier** — the signup trial can be a membership tier flagged `is_trial` /
    `trial_days` (inherits its window + caps); backward-compatible with the legacy NULL-price trial. ✅
- **Peak PAYG court pricing** — a club **peak window** (`club.policy.peak_*`) + an explicit per-duration
  `billing.price.peak_amount_minor`; shown == charged, and membership coverage still wins. ✅
- **Silent membership entitlement caps** — ONE `diary/entitlement.py` resolver (read by availability
  AND booking): `max_covered_minutes` (over-length durations hidden for members), `max_covered_per_day`,
  `max_courts_per_day`, and a court-service `members_covered=false` (e.g. **clay = PAYG-only**). Every
  cap **downgrades to PAYG, never blocks** — shown == charged == allowed. ✅ (spec:
  [EQUIPMENT-AND-CONSTRAINTS.md](EQUIPMENT-AND-CONSTRAINTS.md))
- **Equipment hire** — a ball machine / racquets / balls as a **flat-fee add-on** on a court booking
  (`diary.resource(kind='equipment')` + `quantity`); **time-based availability** (a single ball machine
  can't double-book), one order / one payment (no double-bill), cancel voids the add-on. ✅
- **Tokens / bundles (unit/minute packs)** — prepaid packs across court/lesson/class; balance held in
  **minutes** so one pack covers any length (a 90-min booking off a 60-min unit = 1.5 sessions);
  **atomic draw-down**, **idempotent credit-back** on cancel, expiry/use-it-or-lose-it; coaches
  configure their own lesson packs. ✅
  - **Per-service, coach-scoped packs** — a pack + wallet carry `product_id` = the SPECIFIC service they
    draw for; scoped by **coach + service** at BOTH the checkout draw AND the buy-wizard ("Save on your
    lessons" shows only that coach/service's packs). A legacy **unscoped** pack can be pinned to a
    specific service in the service editor ("Assign to this service"). ✅
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
- **Payment rules are enforced server-side (not just in the UI)** — every purchase path honours the
  EXACT service's `payment_modes`. A **card-only service** (e.g. clay) **refuses pay-at-court / month-end**
  on a booking (`SETTLEMENT_NOT_ALLOWED`, nothing persists); a **class enrolment** obeys the same rule (a
  member can't post `membership_covered`/`free` to conjure an R0 seat — a membership covers **courts** only —
  and a card-only class refuses at-court); and a **pack inherits its service's rule** (a card-only service
  sells only a card pack, never an owed at-court pack). **Staff keep their override** in every case. ✅
- **Memberships & packs buy offline** — not just online: an at-club / month-end purchase opens an owed
  order and **activates the membership or grants the pack immediately**; online holds until the webhook. ✅
- **Online payments — Yoco** hosted checkout (card + Apple/Google/Samsung Pay); held booking →
  verified webhook → paid + confirmed. ✅ (settlement core) 🌐 (live webhook/signature)
- **Idempotent settlement** — a replayed payment/webhook never double-charges, double-confirms, or
  double-grants. ✅
- **Desk payments** — record cash/card/EFT at the desk; idempotent on a receipt id. Every desk /
  at-court payment records **who took the money** (`billing.payment.recorded_by_user_id`) and **refuses
  an amount that isn't the order balance** (`AMOUNT_MISMATCH`) — no over/under-settling. ✅
- **Reconciliation** — recover a missed webhook by asking Yoco and replaying the charge. 🌐
- **Receipts** — a printable/PDF receipt for online and desk payments. 🔭
- **Refunds** — admin direct ("refund only" / "refund & cancel" frees the slot) and a **client
  refund-request → admin approve/decline** workflow. A **partial** refund keeps the order `paid`
  (recorded as **`part_refunded`**); only a **full** refund flips it to `refunded`. ✅ (request
  lifecycle) 🌐 (Yoco execution)
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
- **Money is the OUTCOME of bookings — one reconciling fold** — every money view (coach, admin, client)
  is the SAME order-status-driven fold, **Billed − Discount − Written-off = Invoiced = Paid + Outstanding**,
  single-sourced in `CRMUI.statementFold` / `moneySummary`; an event's headline is the **sum of its
  transactions** (on the shared `TransactionDetail`), and admin **Money is month-paged** with an
  order-based **earnings-by-service** breakdown. 🔭
- **Owner financial cockpit** — revenue by service, commission owed + rent per coach, membership MRR,
  refund-aware. 🔭
- **Proportional commission clawback on refund** — a refund reverses the coach's accrued commission in
  the same proportion (arrears kept in lockstep). ✅
- **Club ↔ coach payouts** — record a payout in **either direction** (`billing.coach_payout`) that
  **nets the running `coach_ledger` balance** (engine + routes intact). The dedicated **Settlement** tab and
  the coach **"To finalise"** section were **retired 2026-07-17** — each coach's running balance plus the
  commission the club realised (on collected) and projects (on owed) now surface inside the **Club earnings**
  P&L drill; the admin Home still headlines "Coach settlements due". 🔭
- **Money is a club-vs-coach P&L** — admin **Money → Club earnings** is the ONE shared `Widgets.Earnings`:
  the club's earnings = **direct services** (courts, membership) **+ commission from coaches**, drilling
  club → **per-coach P&L** (`sales − discount − write-off = net`, `net = received + owed`, commission
  **−coach / +club** realised on received + projected on owed) → **by client → transaction → the shared
  record**. The coach **Money** tab is the SAME widget, coach-scoped to their own P&L (config, no fork). 🔭
- **Month-end sweep** — `POST /api/cron/month-end` (fired by `.github/workflows/month-end.yml` **on the 25th
  of each month**, the billing day) accrues **arrears + rent** and **emails every client with an open
  balance** — **idempotent per month**. The `OPS_KEY` GitHub Actions secret is now set, so it fires live. 🌐
- *(Deferred: scheduled per-day rent accrual — see OUTSTANDING.)*

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
  + Edit profile / Manage membership). Home reads top-to-bottom **Book (services) → Your sessions →
  Match analysis → Billing + Activity → Plan & credits**, with a **month-navigable month-at-a-glance
  summary**: **sessions played** (lessons / court / classes + **total minutes**) and **billed / paid /
  outstanding** with **spend-by-service** (`GET /api/me/activity-summary`), plus an "R… refunded or
  written off this month" clarity note. **Billing by category** (month nav → category → items → the
  booking story / receipt). Every booking/charge drills to its **booking story**
  (`GET /api/me/bookings/<id>`), every line to its order/receipt. The **Match-analysis** block is a
  distinctive **"AI" gradient panel**. **Emoji removed** throughout (replaced by drawn line-glyphs). The
  **Edit-profile** button on the record returns to **Client 360** on save. My-Bookings needs-attention
  (accept/decline a proposed time) + **add-to-calendar**. 🔭
- **Coach** (`coach_app.html` + `coach_app.js`, at `/coach`; bottom nav **Home · Schedule · Clients ·
  Money · Setup**):
  - **Home** = business cockpit KPIs (**Total billed** + net-of-commission earnings / lessons / hours /
    fill-rate) + the **lesson approval queue** + today + book-for-a-client. 🔭
  - **Schedule** = a **weekly calendar** (week-of-today, prev/this/next) on the shared Calendar widget
    — defaults to **just this coach** but can switch to **all** club bookings; tap a lesson → the event
    story; tap a class → its roster. 🔭
  - **Clients** = list → the **full client record**, drilling **month → client → SERVICE →
    transaction**: name + **Total billed**, then **BY SERVICE** ("Private lesson · 60 min · 3 · R750") →
    sessions → each → the event story. The service accordion opens the **SAME shared
    `Widgets.TransactionDetail`**. Each session shows its REAL money state (paid / owed / **written-off**
    / **discounted** / covered). Fed by the **single `get_client_360` composer** (now **month-aware**,
    `month=`, with a **per-service breakdown**) — every coach client view is a view off it; the parallel
    `coach.get_client` reader was **retired**. The shared **Client 360** record surfaces the same
    **activity + spend rollup** (numbers, no chart). Plus a **"Prepaid packages"** view — the clients who
    hold a pack with this coach and their remaining balance. 🔭
  - **Money** = the coach's slice of the ONE shared **`Widgets.Earnings`** (the SAME P&L widget the owner
    mounts, coach-scoped to their own services) — sales − discount − write-off = net, net = received + owed,
    explicit club commission — drilling by client → transaction → the shared record (`#/txn/<order_id>`).
    **Setup** = Services (lifecycle Deactivate/Reactivate/Terminate + filter) + **Classes**
    (create / schedule / roster) + club-commission card + Edit-profile & Weekly-hours (as pages). 🔭
  - **THE ONE COACH EVENT STORY** (`#/event/:id`, `GET /api/coach/bookings/<id>`): client + contact,
    when, court, charge, **coaching line**, players + attendance, and the actions — accept / propose /
    decline / reschedule / cancel / mark-completed / no-show **+ Mark collected / Discount / Write off**
    (the money is managed right here) + add-to-calendar. 🔭
- **Owner / Admin** (`admin_app.html` + `admin_app.js`, at **`/admin`** — **COMPLETE + LIVE**; the
  classic tab console was **retired 2026-07-18**). **Responsive**: bottom-nav on mobile, **left
  side-rail on desktop**. Nav **Home · People · Money · Diary · Overview · Setup**. **Home = a
  command center** surfacing all four owner focuses, each drilling to its section: **Today at the club**
  (live diary), **Money** (owed to the club / net revenue / coach settlements due / active members),
  **People needing attention** (new signups / pending coach invites / expiring memberships), **To
  approve / decide** (pending refund requests) — via `GET /api/admin/home`. **People** → unified
  **person 360** (`GET /api/admin/people/<id>`: identity + roles + membership grant/revoke + owed +
  payments + bookings; if coach, settlement) → the admin event story. **Money** = the reconciling money band +
  a Setup-style section menu (**New invoice** · **Sales by day** [now split **Online (Yoco) vs Cash/EFT**] ·
  **Club earnings** [the club-vs-coach P&L drill] · **Bookings by day** · **Approvals** · **Club activity**),
  each drilling to the shared transaction record / event story (the old Coach-settlement + Online-payments
  tabs were retired). **Diary** = the shared **Calendar widget** (Day/Week/Month + court/coach
  filters, default today) + Classes + a **Block time** button (time-off → `POST /api/diary/time-off`,
  ported from the retired classic console); only the classic console's drag-to-create/move gesture is gone.
  **Overview** (first-class nav tab since 2026-07-05) = month pager + ECharts sub-tabs
  Traffic/Bookings/Revenue/Members/NPS/Courts (`GET /api/insights/overview`); Traffic splits public-site vs
  member-area + a precise logged-in-visitors metric; Courts = the court-utilisation heatmap.
  **Setup** = all club config in-app (`Widgets.Setup`). Every list bottoms out at the **ONE admin event story** (`#/event/:id`,
  `GET /api/admin/bookings/<id>`, god-view actions). 🔭

## 9. Notifications, calendar & CRM
- In-app **bell + inbox** for every member, driven off the event feed: booking confirmed, payment
  receipt, membership active, pack activated, refund requested/decided, class enrolled/waitlisted/
  spot-open, coach invited, lesson requested/proposed/accepted/declined. 🔭
- **Child → guardian** notification routing. 🔭
- Booking **`.ics` calendar** (in-app add-to-calendar works now; the email attachment is gated OFF via
  `EMAIL_ICS_ENABLED=0` until the interim SES key gains `ses:SendRawEmail`). 🔭
- **Transactional email — per-club branded, multi-tenant SES — LIVE** ✅ (interim via the Ten-Fifty5 AWS
  account, `eu-north-1`, `SES_SENDER=noreply@ten-fifty5.com`): confirmations + invites go out from **one
  verified domain** but under **each club's own From name and Reply-To**, so a new tenant needs no new
  sender verification. Booking confirmations carry a **rich detail block** (client name, contact,
  service, date & **time in SAST**, court, price, payment status); a lesson booking also **BCCs the
  coach** so they get the booking in their inbox. 🌐 The `.ics` attachment is currently OFF (see above); the long-term CourtFlow-domain
  setup is `SES-SETUP.md`. Plus **Klaviyo** lifecycle/marketing — same feed, dark until keyed. 🌐
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

**Booking engine — `scripts/test_booking_scenarios.py` (180 checks):** court book/cancel/double-book/
reschedule (+ conflict preserves original) · lesson = coach + court rows, collapsed to one line ·
lesson needs a free court · **coach∩class conflict** (read + write) · 15-min slot granularity ·
class enrol/capacity/waitlist/promote · lesson approval lifecycle (request → accept/decline/propose →
client accept) · court→service allocation · classes reserve N courts (+ auto-repick) · online class seat
lazy-expiry · peak court pricing · membership entitlement caps → PAYG · configurable trial · equipment
hire · **coach back-capture of a past lesson** (staff-only allow_past, resource from coach_user_id) ·
**semi-private (squad) lessons** — per-head billing (one owed order EACH, both visible in their own 360,
cancel voids all) · **add-a-player-later** (own bill + cap/duplicate/non-lesson guards) · a parent's **two
kids** both billed to the guardian (R800, two players) · the **addable-player guard** (a member may add
club members + their OWN kids, never a stranger or another family's child) · **card-only service refuses
pay-at-court** on the booking path (staff override kept) · **class payment gate** (no free/membership-
covered seat conjured; a card-only class refuses at-court; staff override kept).

**Commercial engines — `scripts/test_billing_scenarios.py` (311 checks)** (the count grew 281→298→311 across
the 2026-07-15 invoicing + pack-bypass sprints — incl. a behavioural guard that reconcile ACTIVATES the
pack/wallet, not just marks it paid)**:** settlement per mode
(at-court desk, online held→paid, monthly-account ledger) · **idempotent payment replay** · commission
30%/40% scoping + accrual + idempotency · token pack buy→activate→**unit/minute draw-down**→credit-back
+ NO_TOKEN · membership coverage (R0) + **access window** inside/outside + trial idempotency · refund-
request lifecycle (create/duplicate/list/decline/NOT_PENDING) · membership/pack **offline buy** + the
per-tier/per-service payment-mode resolution · **refund clawback** split · membership-cancel & cancel-
booking **void the order** · **transaction log** + **dispute routing** (coach vs club) · lockstep
desk-pay & **void clears arrears** · abandoned-checkout **reclaim on read** · the client + coach
**event/booking stories** · the **client BY-SERVICE breakdown** (incl. written-off + discounted per-
session state, billed vs effective, total-billed unchanged by write-off/discount) · the **admin
person-360** + **admin event story** (god-view) · the Phase-2 read-layer (**court-utilisation** heatmap
+ **sales-by-day**) · the 2026-07-08 booking-audit additions: **strict two-tier coach/product-scoped
pricing** (coach's own product ELSE shared, never merged) · **per-service selection** · **class rate-card
fix** (each class bills its own price) · **cancel late-fee + paid-booking resize** (`PAID_CANNOT_EXTEND`) ·
**lesson-reschedule court auto-reassign** · **membership-covered reschedule guard**
(`NOT_COVERED_AT_NEW_TIME`) · **settlement/approval-gate whitelist** (no client `free`; accept coerces
covered/free → at-court) · **online-only** + **off-platform reconcile** · **on-behalf token/pack draw-down** ·
**a pack inherits its service's payment rule** (a card-only service sells only a card pack — no owed at-court
pack; an unrestricted pack still allows pay-at-court).

**Unified statement — `scripts/test_statement_reconciliation.py` (47 checks):** no double-count
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

## 14. Match analysis — embedded Ten-Fifty5 (members-area SSO)
- **AI match analysis & technique, inside the members area** — a member opens **Ten-Fifty5** (the separate
  live 1050 product: upload a match → AI stats, shot breakdowns, technique) in an iframe on the client SPA,
  **signed in automatically with their NextPoint login** (no second account, no second login). The two are
  separate Clerk apps bridged by a `postMessage` token relay + issuer federation; email is the identity key
  (Ten-Fifty5 auto-provisions the member). 🌐 (needs the Ten-Fifty5-side env — see `ENV-STATUS.md`.)
- **Private allowlist-gated test** — currently shown only to `TF5_EMBED_ALLOW_EMAILS`; everyone else sees a
  **"Coming soon"** card. Launch = clear that env (open to all members). 🌐
- **Public marketing funnel** — a "Match analysis" section on the public home page links out to
  ten-fifty5.com (drives discovery traffic, independent of the embed). ✅

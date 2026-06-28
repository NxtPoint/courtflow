# FRONTEND-REDESIGN — radically simpler, one-page-per-role

The front-end simplification effort (started 2026-06-27). Goal: **every feature available, intuitive,
super simple & clean.** Fewer pages, fewer tabs, no duplication. Backend is unchanged — this is purely
the presentation layer. Decisions locked with Tomo (AskUserQuestion, 2026-06-27).

## Locked decisions
- **Client = ONE page** ("Home") with a sticky chip nav → four sections: **Book · My Bookings · Profile ·
  Money**. (No more separate `/my`, `/account` for the client; My Bookings was duplicated on `/portal` +
  `/my` — fixed.)
- **Pricing pages retire.** Replaced by a **post-trial wizard** (free-week ends or "Upgrade" tap →
  *Membership or PAYG?* → choose months / sessions / durations by affordability) **plus** a lightweight
  **Manage plan** view in the Money section (browse/buy/upgrade anytime).
- **Coach = ~5 tabs:** Profile · Services · Schedule · Clients · Reporting & Financials.
- **Admin = same shape, admin options** (detailed once client+coach land).
- **Build order:** Client → Coach → Admin. Blueprint written as we go.

## Principle: nothing is lost
Every existing feature maps to a section. Consolidation must not drop: dependents, refund-requests, the
`.ics` calendar, the lesson approval lifecycle ("needs your attention"), reschedule/cancel, usage/spend
history, membership/pack purchase. Cross-checked against [FEATURES.md](FEATURES.md) + [INVENTORY.md](INVENTORY.md).

---

## Client one-page (Home) — section map
Sticky chips: **[Book] [Bookings] [Profile] [Money]** (click → smooth-scroll to section; active state).

1. **Book** — greeting + plan chip (free-week countdown / Member / PAYG); the trial/plan **nudge** (→
   wizard); 3 service launchers (Court/Lesson/Class → full-screen `/book/<service>`); staff get a small
   row of console links (Coach/Admin/Settings).
2. **My Bookings** — *Needs your attention* (accept/decline a proposed time, withdraw a request) ·
   *Upcoming* (add-to-calendar, reschedule, cancel) · *Past & cancelled*. (was `my.js`)
3. **Profile** — editable details (email read-only) + **Family** (children/dependents add/edit/remove).
   (was `account.js` Profile+Family)
4. **Money** — plan card (+ **Manage plan** → wizard/plan) · usage this month · spend + history · recent
   payments (request refund) · refund requests. (was `account.js` Financials)

**Nav simplification (`portal.js`):** the client nav collapses to **Home** (+ **Book** as the one
deep-link); My Bookings / Plan / Account drop out of the top nav (now sections). Coach/Admin/Statement
links stay for those roles. `/my`, `/account`, `/plan(s)` pages remain reachable as fallback until the new
Home is validated, then they retire (or 301 → `/portal#section`).

## Post-trial wizard (next increment)
Trigger: free-week end (or "Upgrade"). Steps: (1) Membership or PAYG → (2a) membership: pick term
(1/3/6 mo) → Yoco; (2b) PAYG: optionally buy a session pack (pick #sessions/duration) or skip. Replaces
browsing `/plan`. The Money "Manage plan" opens the same wizard for upgrades.

## Coach (next) — 5 tabs
Profile (bio/photo/languages/quals/visibility/review-toggle) · Services (per-duration rates · classes ·
own packs) · Schedule (availability + time-off + **approval queue** + book-for-client) · Clients (360) ·
Reporting & Financials (cockpit + statement). (Reorganises today's single-page `coach.js`.)

## Admin (later) — same shape, admin options
Likely: Diary · People · Services & Pricing · Coaches & Pay · Reporting & Financials · Settings — folding
today's 7 admin tabs + 8 Settings tabs into fewer, role-aware groups (see [PERMISSIONS.md](PERMISSIONS.md)
for any staff-role gating).

## Revision (2026-06-28, from Tomo testing the first cut)
- **Single top menu only** — the lower chip strip is removed (it read as a second menu on mobile).
  Nav = **Home · Account** (+ role consoles), same look/feel all devices.
- **Home** = green greeting (greeting · **name + surname** · email · **[Edit profile] [Add child]**
  popups — no permanent profile form) → **Book** launchers → **My Bookings** (Upcoming/Past **toggle**,
  upcoming **nearest→furthest**, full **edit / cancel / request-refund** per booking + needs-attention).
- **Account** is its own page (`account.js`), **fleshed out**: plan + next charge · **usage over time**
  (12 months, derived client-side from bookings: bookings/month bar chart + totals, court-time hours,
  by-type) · this-month usage · **billing per month** (bar chart + table) · account balance · coaching
  statement (if any) · payments/receipts (request refund) · refund requests.
- Profile/Family editing = **popups** from the greeting (not a permanent section).

## Increments / status
- [x] Blueprint (this doc).
- [x] **Client Home** (greeting+popups · Book · Bookings toggle) + **Account** (rich usage/billing) +
  single-top-menu nav. (v1 chips → v2 per Tomo's feedback.)
- [x] **Plan wizard** (`wizard.js`): the "money moment" — Step 1 PAYG vs Membership (PAYG nudged),
  Step 2 pick a court **pack** (1/3/5/10, prepaid + drawn down) or a membership **tier** (per-month
  framing), Step 3 Yoco checkout. Auto-opens on Home when there's no coverage + no credits (session-
  dismissible); opened by the Home nudge + Account "Manage plan". Seeded default **court packs**
  (1/3/5/10) in `seed_nextpoint` (only when none exist, so owner edits/deletes stick) so PAYG isn't
  "pay everything each time" — packs draw down via the existing token engine. `/plan` (plan.js) stays
  as the detailed fallback page.
- [x] **Membership Tier field** — explicit `billing.price.membership_tier` (admin-configured: a Tier
  input on the Membership-plans form), drives the wizard's tier → term grouping. Seed gives defaults a
  'Standard' tier + backfills. `python -m db` twice clean.
- [x] **Coach console → 5 tabs** (`coach.js`): Schedule (requests · my week · book-for-client · time
  off) · Services (rates · classes · packs) · Clients (360) · Reporting (cockpit + statement) ·
  Profile (clean summary + Edit-profile / Edit-hours POPUPS, no field-wall). Reuses CoachUI/CRMUI/
  ClassUI unchanged; `coach.html` slimmed to a bare shell.
- [x] **Admin** — surfaced the orphaned coach lifecycle (resend invite / remove coach) on Settings →
  Coaches + added the missing API wrappers; tidy 'Club admin' header + ⚙ Settings link. (Kept the
  working 7-tab structure; hard price-delete left out since status-retire is the clean soft-delete.)
- [x] **Coach commission visibility** — `GET /api/coach/commission` (read-only); a greyed
  "Commission · Set by the club" card on the coach Services tab (club % / you-keep + per-service).
- [x] **Month-end loop** — `statement_ready` notification ("Your invoice is ready — Rx due", links
  /account) via the monthly-invoice cron (accrues arrears + notifies clients with a balance) **+**
  client pay-online: `POST /api/me/statement/pay` → Yoco → on payment the linked `coach_arrears`
  settle (commission accrues; idempotent). Account "Money" shows the "Pay Rx online" CTA.
- [x] **Email triggers** — fully wired: every mapped event fires in-app + SES email (self-gating on
  keys). Added `statement_ready` + the **lesson lifecycle** (requested/proposed/accepted/declined).
  Only the SES sender key (Tomo's config) is left to start *sending*.
- [x] **Settings consolidation (start)** — Hours folded into **Courts & hours**; the global payment
  methods (online · at-club · monthly) moved onto **Club profile**. 8 → 6 tabs.

### Consolidation — remaining (the bigger, careful piece; do with Tomo)
Tomo's principle: **all of a thing's config in ONE place; summary blocks + popup edit; no duplicate
screens.** Still to do:
- **Unified Service Editor popup** (the Wix reference): one modal per service managing price +
  **variations** (per-duration) + **payment preference** (which enabled methods this service offers)
  + **packages** (PAYG = session multiples / membership = terms) + **commission** (owner-editable,
  coach **greyed**). The SAME modal opens for the owner (edits all incl. commission) and the coach
  (edits all **except** commission). Needs: a per-service `allowed_settlement_modes` field (new) +
  a per-service payment picker in the booking flow; commission editing moves OUT of the "Coach pay"
  per-service table INTO the service editor (kill the duplication — Coach pay keeps only rent + the
  global/per-coach **default %**).
- **Admin console → client pattern** throughout (summary cards + popup editors), mirroring the coach
  tabs.

- [ ] Retire `/my`, `/account`, `/plans` (301 → Home) once validated.

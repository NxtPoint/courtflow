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

### Coach & owner consoles restructured (2026-07-02, shipped) ✅
Finishing-touches pass — role-focused, business-first, reusing the client-flow learnings + our own
built components (the master-diary grid, the cockpit, the analytics Overview, CRMUI). Front-end only.
- **Role-aware nav + landing** (`portal.js`): the client **Home**/**Account** no longer clutter a
  coach/owner's top bar. member/guest → Home · Account · coach → **Coach** (landing) · Account ·
  owner → **Admin** (landing) · Settings. `landingFor()` sends staff to their console on sign-in;
  `/portal.html?stay=1` bypasses it for testing.
- **Coach console** (`coach.js`) → **Dashboard · Schedule · Clients · Money · Setup**: Dashboard =
  "needs your attention" (approval queue) + the cockpit (net-of-commission KPIs · earnings trend ·
  month-end position · top clients · upcoming); Schedule = a NEW **week timeline** (master-diary grid
  reused, prev/next week, tap lesson → done/no-show, tap class → roster) + book-for-client + time off;
  Money = the settlement statement (supersedes standalone `/statement.html`); Setup = sub-tabbed
  Services & pricing (+ the club-commission card, previously dead code) + classes + Profile. Dead code
  removed (`renderTrend`, old list `loadWeek/renderWeek`).
- **Owner console** (`admin.js`) → **Dashboard · Diary · People · Money · Insights** (+ Settings):
  Dashboard = this-month money KPIs + net-revenue trend + last-30-days growth (analytics: visits/
  visitors/new-customers/bookings/NPS) + quick-action row (review N refund requests, jump to any tab);
  Diary = Timeline + Classes (sub-tabs); Money = Billing + the full financial cockpit; Insights = the
  analytics Business Overview. The old Cockpit/Overview/Classes tabs folded in.

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

### Unified Service Editor — BUILT (2026-06-28, paired) ✅
Golden rule realised: **one service, one API, one editor, edited in one place.**
- **One API:** `services/` lane → `/api/services/*` (both owner + coach call it; the route enforces
  who edits what — coach: name/variations/payment/packages of their OWN service; **never** commission).
- **One read:** `GET /api/services/<id>` composes EVERYTHING (variations · per-service payment
  preference `billing.product.payment_modes` · packages · commission · the club's enabled methods).
- **One component:** `service_editor.js` (`window.ServiceEditor.open(productId)`) — a single modal:
  Pricing & variations · Payment preference · Packages · Commission (owner edits; coach greyed).
- **Wired:** coach **Services** tab + admin **Settings → Services** are both a summary-card list →
  "Manage" → the same editor. The scattered editors (court rates / coach services / packs / per-service
  commission table) collapse into this one place.
- **Enforcement — DONE:** `/api/diary/durations` returns the service's `payment_modes`; `booking.js`
  intersects its pay options, and `create_booking` refuses a crafted member/guest request with a
  method the service doesn't offer (`SETTLEMENT_NOT_ALLOWED`; admins/coaches override; guarded).
  Harness scenario green.
- **Old screens retired — DONE:** Settings `Pricing` → **Memberships** only (court rates + packs moved
  into the Service Editor); **Coach pay** dropped its per-service table (keeps rent + the global/per-coach
  **default %**; per-service override is on the service). `AdminUI.courtRates/bundlePlans/pricingHome`
  remain defined-but-unreferenced (safe to delete later). Membership term-plans stay (club-wide).

### Editor design pass (2026-06-28, paired) — applies to ALL services + memberships
- [x] **Full-screen editors** (no popups) — Service Editor + Membership editor render in place with a
  sticky **Cancel / Save & close** bar.
- [x] **Save & close (batch)** — no inline saves; edits/adds/removes batch in memory and apply on save;
  Cancel discards.
- [x] **Edit · Hide · Delete** on every service/membership summary row (services: Edit + Hide via
  `product.active`, shown greyed + Unhide; memberships: Edit + Hide=dormant + Delete=retire).
- [ ] **Per-day access times** (membership) — NEXT. Make the membership access window **per weekday**,
  each day **seeded from the club's court hours**, then narrowed for limited tiers (e.g. Off-peak).
  Plan: add `billing.price.access_windows jsonb` ({weekday→{start,end}}; NULL = legacy single window,
  so existing memberships are untouched) → extend `diary.pricing.membership_covers` to check the
  booking's weekday window when access_windows is set (else legacy) → membership editor shows a 7-day
  grid defaulting to court hours → reads return access_windows + summary → harness scenario. Additive
  + revenue-critical (gates free courts), so do it as a focused, tested pass.

### Lifecycle UI · unified statement · payment rule (2026-06-28, shipped) ✅
The presentation pass that made the consoles feel like one system. All client-facing, backend untouched
except the additive columns noted in [UNIFIED-STATEMENT.md](UNIFIED-STATEMENT.md) / [INVENTORY.md](INVENTORY.md).
- [x] **Unified "Your statement" card** on the **Account** page (`account.js`) — ONE card replaces the
  split account-balance / coaching-statement. Collapsible **category headings** (Coaching · Court hire ·
  Classes · Membership · Session packs · Other) with a **+/- drill-down** and a per-line **subtotal**;
  every line has a **tick** → **part-settle** (passes the selected `order_ids`; unticked stay owed) and a
  **settle-online** CTA (→ `POST /api/me/statement/pay {order_ids?}` → settlement order → Yoco). Backed by
  `billing/statement.py` (the single "what a client owes" source = unpaid `billing.order` rows).
- [x] **Lifecycle UI everywhere** — ONE vocabulary (Active / Deactivated / Terminated) across services,
  memberships and coaches via shared helpers `UI.lifecycleBar` (filter pills) · `UI.lifeActions`
  (Deactivate / Reactivate / Terminate) · `UI.statusChip` · `UI.subtabs` (in `frontend/js/ui.js`). New CSS
  **`.cf-lifefilter`** (compact pill filter) + **`.cf-subtabs`** (underline in-section tabs) in `app.css`.
- [x] **Full-screen editors with a single "Save & close"** (batch — edits/adds/removes apply on save,
  Cancel discards; no inline saves) + **click-the-block-to-edit** (`.cf-pickable`) + a **green selection**
  state throughout the consoles.
- [x] **Settings → Services** as **sub-tabs** (Lessons / Classes / Courts) with a **coach filter** dropdown
  (admin sees All + each coach; the coach console is self-scoped server-side) and **rich summary cards**
  (each shows the coach name + actual per-duration amounts).
- [x] **Settings → Courts & hours** rebuilt as **click-to-edit per-court blocks** (`AdminUI.courtsManage`)
  — each court owns its weekly playing hours (per-day open/closed + range + slot) via the per-resource
  `GET/PUT /api/admin/hours`. **Settings now lives on the main nav** (`portal.js`) for admins.
- [x] **People category slicer** — Members / Coaches / Guests / Admins / All with live counts (default Members).
- [x] **ONE payment rule on the front end** (`frontend/js/pay.js` — `Pay.purchase` → `Pay.buyMembership` /
  `Pay.buyPack`): more than one allowed mode → the client **chooses**; exactly one non-online mode →
  checkout **immediately** (no payment prompt); online → Yoco. `booking.js` hides the chooser when there's
  a single way to pay. **Off-peak coverage priced per slot** — an availability slot shows **free** only
  inside the membership access window, **price** outside (matches what `create_booking` charges). The
  **profile/Account "Your plan"** card shows the tier + window summary + next-renew, with a
  **Cancel membership** button (paid memberships only → `POST /api/me/membership/cancel`).

### The ONE client record — coach = a scoped filter, not a fork (2026-07-12, shipped) ✅
The person/client-360 across all three SPAs is now the single `Widgets.ClientRecord`, fed by the ONE
`client360.get_client_360(...)` composer (`month=` + `scope`). It renders **headline-first**: WHO
(name · status · contact · kids · Edit — PII behind Edit) → Packages → a **month-paged money block**
(`CRMUI.statementFold` fold — Billed − Discount − Written-off = Invoiced = Paid + Outstanding — with
collapsible SERVICE GROUPS, each reconciling to the fold) → every event drills to the shared
`Widgets.TransactionDetail`. The coach's client view is **the same widget with a strict server-scoped
filter** (`scope='coach'` returns only the coach's own events + own coaching fold + own packages; the
old coach `get_client` fork was retired), never forked render code.

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

### Admin vs Settings — consolidated to "Operate vs Configure" (2026-06-28)
Two surfaces, one hard line, no lost functionality:
- **Admin console (`admin.html`) = OPERATE** — Master diary · Classes (scheduling/rosters/attendance)
  · People · Billing · Cockpit · Overview.
- **Settings (`settings.html`) = CONFIGURE** — Club profile · Courts & hours · Services · Memberships
  · Coaches.
- The read-only **Resources** tab was **retired** (it duplicated Courts & hours / Coaches / Services and
  couldn't edit anything). Courts → Settings → Courts & hours; coaches → Settings → Coaches; classes →
  Admin → Classes (run) + Settings → Services (price).
- **Class pricing/packages/payment** live only in **Settings → Services**; Admin → Classes keeps the
  operational half (create + schedule + rosters + attendance) and links out for pricing.
- People remains the members hub (directory + grant/revoke membership + 360); coach *config* stays in
  Settings → Coaches.

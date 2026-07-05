# Owner / Admin console redesign — design spec

Status: **COMPLETE + LIVE 2026-07-03.** All 7 build steps shipped; `/admin` now serves this SPA
(`frontend/app/admin_app.html` + `frontend/js/admin_app.js`), the classic tab console is preserved at
`/admin-classic`. The third and hardest console redesign, applying the DNA proven on the client
(`frontend/js/client.js`) and coach (`frontend/js/coach_app.js`) apps to the owner/admin surface.
Since the redesign, the whole front end was further standardised onto ONE widget per capability —
see **[FRONTEND-STANDARDISATION.md](FRONTEND-STANDARDISATION.md)** (the enshrined golden rule); the
admin event story, calendar and Setup below are now the shared `Widgets.TransactionDetail` / `Calendar`
/ `Setup`. **As-built delta vs the plan below:** Money is a Setup-style section menu (Sales by day ·
Revenue · Coach settlement · Approvals · Payments · Activity); Diary uses the shared Calendar widget —
its **Day view is now the resource-timeline grid** (courts + coaches as columns, 06:00–22:00 rows;
config-driven via `cfg.grid`, empty coach columns hidden), **Week/Month stay agenda**, and any block drills
into the shared event story; the full drag-timeline **editing** (walk-in/block-time/desk-pay) still lives at
`/admin-classic`. Insights embeds the court-utilisation heatmap + the Business Overview.

Owner brief: "take the learnings from client and coach and apply to admin — admin is the hardest,
needs a lot of thought." Owner answers to framing questions:
- **Device: BOTH** → responsive. Mobile-first cards that reflow into denser desktop layouts
  (bottom-nav on mobile, side-rail nav on desktop — one nav component, CSS-switched).
- **Home focus: ALL FOUR** → Money & settlements · Today at the club · People needing attention ·
  Things to approve/decide. Home is a command center, not a single dashboard.

## The DNA (non-negotiables, carried from client + coach)
1. **One SPA**: shell HTML (`#cf-main`) + one JS file, `Portal.boot` role-gate → `renderShell` →
   hash router. Design system = the shared `cf-*` classes in `frontend/app/app.css` — add classes,
   never inline component styles.
2. **Drill-through everywhere** — no data dumps. Every list row opens its full story. Money and
   People both bottom out at the SAME place: a booking → **the ONE admin event story**.
3. **GOLDEN RULE — one booking capability per app.** Admin gets exactly ONE event story
   (`#/event/:id`, god-view: full actions incl. void/refund/reassign/desk-pay). Home, Diary,
   People, Money all navigate to it — never a second booking sheet. (Each role has its own scoped
   story: client `booking_story`, coach `coach_booking_story`, admin `admin_booking_story` — one
   per app, reused within.)
4. **Reuse, don't rebuild.** Everything already exists (see reuse map). This lane is an IA + UX
   re-skin over the existing `AdminAPI`, `AdminUI`, `CRMUI`, `ClassUI`, `window.API` diary calls,
   and the `overview.js` embed.

## Information architecture — nav sections
Responsive nav (bottom-nav ≤900px, side-rail >900px): **Home · People · Money · Diary · Setup**.
Insights/Reports reachable from a Home tile + `#/insights` route (not in the 5-slot nav).

### Home (`#/home`) — the command center (all four focuses, each drills)
Greeting ribbon (club name + owner) then four live "focus" cards:
1. **Today at the club** — today's bookings/lessons/classes count + the next few + any gaps/
   issues; tap → Diary (today). Source: `API.master` (today) / existing dashboard read.
2. **Money** — Owed to the club (Σ unpaid orders club-wide), Coach settlements due (rent + net),
   takings today/this month; tap → Money. Source: `cockpit_summary` + statement aggregate.
3. **People needing attention** — new signups (7d), memberships expiring soon, coach invites
   pending; tap → People (filtered). Source: `list_people` + membership expiry + coach invite status.
4. **Approvals / decisions** — pending refund requests + open disputes, inline accept/decline or
   tap the item. Source: `refund_requests` + dispute routing.

Backed by a new lean `GET /api/admin/home` composing existing repo reads (keeps Home one fast call).

### People (`#/people`, `#/person/:id`)
Roster with a category slicer (Members / Coaches / Guests / Admins / All) + search → row opens the
**unified person 360** (`#/person/:id`). Today member-360 and coach-360 are two different drawers;
unify them into ONE record page (mirrors coach `get_client`):
- **Header**: name, contact, role/status chips, membership line (+ grant/revoke), Total owed.
- **Money**: what they owe the club (unpaid orders, each Void / Write-off) + online payments.
- **Bookings**: upcoming + history, each → the admin event story.
- **If coach**: their settlement (gross / commission / rent / net / balance) → drill per-client →
  per-service → session → event story (reuse the coach money drill).
New backend `GET /api/admin/people/<user_id>` → unified 360 payload.

### Money (`#/money`)
The financial cockpit, drill-through:
- KPI strip (net revenue, commission kept, net to coaches, rent due, active members, MRR, lessons).
- **Per-coach settlement** table → coach record → per-client → per-service → session → event story.
- Revenue by service (bars + table).
- **Approvals**: refund-request queue (approve/decline, Club-vs-Coach routing chip) + disputes.
- **Online payments** (Refund only / Refund & cancel).
- **Club transaction log** (`activity` → `CRMUI.activityFeed`).

### Diary (`#/diary`)
KEEP the powerful resource-timeline (reuse `renderDiary`: click-to-create, walk-in, block time,
desk-pay) + **Classes** subtab (reuse `ClassUI` — the coach wiring is the blueprint). Any booking
tapped → the admin event story (golden rule).

### Setup (`#/setup`, `#/setup/:section`)
Bring Settings INTO the SPA (retire the separate `/settings.html` jump). Embed the `AdminUI`
editor library as in-SPA pages: Club profile / branding / **policy incl. online-payment toggle** ·
Courts & hours · Services & prices · Membership plans (+access windows) · Session packs · Coaches
(invite / remove / hide / **commission & agreements**) · Classes. All WRITE endpoints already exist.

### Insights (`#/insights`) — reachable from Home
Embed the Business Overview inline (the existing `renderOverview` pattern: build DOM → lazy-load
ECharts + `/js/overview.js` → `Overview.start()`).

## Backend additions (only three; everything else is reuse)
1. `GET /api/admin/people/<user_id>` — unified person 360 (profile + membership + owed + payments +
   bookings; if coach: settlement summary). Mirrors coach `get_client`. → `admin/repositories.get_person`.
2. `GET /api/admin/bookings/<id>` — `diary.bookings.admin_booking_story` (god-view: any booking, full
   actions — accept/reschedule/cancel/mark/void/refund/reassign/desk-pay + charge + client + players).
3. `GET /api/admin/home` — the 4-focus hub payload (compose existing repo reads).

## Reuse map (do NOT rebuild)
- `window.AdminAPI` (`frontend/js/admin_api.js`) — all read/write wrappers, 1:1 to routes.
- `window.AdminUI` (bottom of `admin_api.js`) — the Settings editor components (clubProfile, hours,
  courts, services, coaches, membershipPlans, bundles, commission).
- `window.CRMUI` — greetBand / stats / bars / drawer / activityFeed / sectionHead / statementTable /
  lineItems / requestQueue.
- `window.ClassUI` — class create/schedule/sessions/roster (lazy-load `/js/class_ui.js`).
- `window.API` — diary/booking calls (master, resources, createBooking, cancelBooking,
  setBookingStatus, timeOff, deskPayment, billingConfig).
- `overview.js` — the Insights embed.
- Existing endpoints: full inventory in the admin surface map (see git history / the Explore digest).

## Build order (incremental, commit per step, gates 43/142/35 + py_compile each time)
1. **Shell + responsive nav + Home hub** (compose existing reads or `/api/admin/home`). ← START
2. **People** roster + **unified person 360** (+ `GET /api/admin/people/<id>`).
3. **Admin event story** (`GET /api/admin/bookings/<id>` + `#/event/:id`) — the shared drill target.
4. **Money** cockpit + settlement drill (coach → client → service → session → event).
5. **Diary** timeline + Classes (reuse).
6. **Setup** — embed AdminUI editors as SPA pages.
7. **Insights** embed.

## Files
- New: `frontend/js/admin_app.js` (the SPA), `frontend/app/admin_app.html` (shell). `web_app.py`
  `/admin` → the new shell; keep the old `admin.html`/`admin.js` on disk as fallback until signed off.
- Backend: `admin/routes.py` (+3 routes), `admin/repositories.py` (`get_person`, home composer),
  `diary/bookings.py` (`admin_booking_story`).

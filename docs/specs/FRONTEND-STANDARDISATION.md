# Frontend widget architecture — the GOLDEN RULE (one widget per capability)

Status: **SHIPPED 2026-07-03/04 — this is the enshrined frontend architecture.** Frontend only; the
backend was not touched. Delivered as an incremental, always-green sprint (Waves 1–3, 5, 6; Wave 4
deliberately not merged — see §7). Gates held at booking 43 / billing 142 / statement 35 throughout.

Owner's mandate (verbatim): *"we have too much history and inefficient code … 1 widget for each service
(e.g. calendar) used all over where required — the only difference would be the filters … when we click
through to transaction detail it should be 1 standard page always, no matter where we click from …
keeping all our gold-standard stuff."*

The **Setup tab is the signed-off gold standard** ([[gold-standard-setup-ux]]) — preserved byte-for-byte
and now *shared* by both the owner and coach consoles.

---

## 0. THE GOLDEN RULE (read this first — it governs all new frontend work)

> **Every user-facing capability is rendered by exactly ONE widget, shared across the client, coach and
> admin apps. Role differences are expressed as CONFIGURATION — never as a second copy of the render
> code. A second implementation of "a transaction", "the calendar", "the services list" is a BUG: extend
> the widget's config, do not fork it.**

A widget is a vanilla-JS IIFE that attaches one object to `window.Widgets.<Name>` and exposes a single
`mount(host, cfg) → { refresh, destroy }`. Role variation flows through four config axes and nothing else:

- **`data`** — an adapter the app injects (`cfg.data.get(id)`, `cfg.data.events({from,to})`, …). The
  widget NEVER calls `API`/`AdminAPI`/`CoachAPI`/`TFAuth` directly; the adapter fetches and normalises
  the role's payload so the widget stays dumb.
- **`actions`** — a capability map `{ <key>: { run(ctx), label?, tone?, group?, confirm?, done?, back?,
  manual? } }`. The widget renders a button per key the payload allows AND the app wired; an absent key
  is the gate. Admin passes `{void,refund,reassign,…}`, coach `{collect,discount,…}`, client
  `{pay,request_refund,…}` — same renderer.
- **`fields`** — visibility flags (`{ showCoach:false, showNotes:false }`).
- **`onNavigate` / filters** — the widget never reads or writes `location.hash`; it calls
  `cfg.onNavigate(target)` and the app's router owns the route. This is what guarantees "click a
  transaction from anywhere → the identical detail page."

Five hard rules (the guardrails, §6):
1. **No `if (role === 'admin')` around render logic** — role is a default-picker, config is truth.
2. **Data via adapter only** — endpoint knowledge lives in the app, never the widget.
3. **Actions are a map** — buttons are declared, gated by presence, run returns a promise.
4. **Nav via callback** — widgets don't touch the URL.
5. **Widgets are pure render + events** — inputs = `cfg`; outputs = DOM + adapter/nav calls; no
   module-level mutable state (`mount` state lives in the closure, so a widget can mount twice per page).

One design system only: `frontend/app/app.css` `cf-*` classes; never inline component styles; render
results with `UI.clear(host)` (the `.cf-loading` spinner gotcha). Shared helpers used by ≥2 apps are
promoted to `window.UI`/`window.CRMUI` once and the copies deleted in the same commit — never a new
local `money()`/`card()`/`modal()`.

---

> **Missing from the inventory below until 2026-07-23, all shipped and live:**
> **`Widgets.Earnings`** (`frontend/js/widgets/earnings.js`) - the ONE club-vs-coach P&L across admin +
> coach Money, config-only (the coach app says "You keep", the admin drills coach -> client -> transaction).
> **`CRMUI.rescheduleModal`** - the ONE reschedule UI shared by client/coach/admin/home, which replaced four
> drifted forks, none of which could move a court; role differences are config (`canChangeCourt` is false for
> a member's lesson, true for their court hire and for all staff).
> **`CRMUI.activityBlock` / `spendBlock` / `weekChart`** - the shared month-at-a-glance blocks used by both
> the client Home and the Client 360. **`CRMUI.lineItems`** gained an `onClick` option so a list can route
> instead of acting (the refund-request inbox uses it to open the transaction record).

## 1. As-built widget inventory (what exists on disk)

Shared layers, in load order per shell (`auth_client → ui → crm_ui → *_api → service_editor →
widgets/_registry → widgets/* → <role>_app`):

- **`window.UI`** (`frontend/js/ui.js`) — primitives. Now also the single home of the promoted helpers:
  `card`, `backBar`, `kv`, `modal(title, opts)` (`opts.lg` = the large size), `toLocal`, `addToCalendar`,
  and the ONE role-neutral `statusChip` vocabulary (booking · payment · lifecycle). Plus the pre-existing
  `el / money / fmt* / dateKey / clear / toast / errMsg / subtabs / lifecycleBar / lifeActions`.
- **`window.CRMUI`** (`frontend/js/crm_ui.js`) — composed presenters: `stats · bars · statementTable ·
  lineItems · requestQueue · drawer · sectionHead · activityFeed · greetBand`. **The client shell now
  loads `crm_ui.js`** (it didn't before — the root cause of the client re-implementing money inline).
  **Added under the golden rule (2026-07-14):** **`CRMUI.moneySummary`** + **`CRMUI.statementFold`** —
  the ONE money band/fold (money = the OUTCOME of bookings: Billed − Discount − Written-off = Invoiced =
  Paid + Outstanding), single-sourced across the coach, admin AND client money views so no two consoles
  compute a different "paid"; **`CRMUI.createClientModal`** — the ONE create-client dialog (admin + coach,
  same modal); **`CRMUI.addLessonPlayerModal`** — the ONE add-a-player picker (name / kids list) for
  semi-private squad lessons, used wherever a player is added to a lesson.
- **`window.Widgets`** (`frontend/js/widgets/`):
  - `_registry.js` → `window.Widgets = {}`.
  - `txn_detail.js` → **`Widgets.TransactionDetail`** — the ONE booking/transaction detail ("event
    story"). Used by client, coach AND admin. `cfg = { role, scope:{id}, data:{get(id)}, actions,
    fields, grouped, onNavigate }`. `grouped:true` = category headers (admin/coach god-view);
    `grouped:false` = flat action row (client). Renders a button per `can` flag the payload allows and
    the app wired.
  - `calendar.js` → **`Widgets.Calendar`** — the shared Day/Week/Month agenda calendar with optional
    Court & Coach filters. `cfg = { data:{events({from,to})}, filterBar:{courts,coaches}, onNavigate(ev),
    classicLink, view?, date? }`. Adopted by the **admin Diary**.
  - `setup.js` → **`Widgets.Setup`** (the gold-standard "section menu → focused full-screen editor"
    shell; sections are `{key,label,desc, mount(sectionHost) | href}`, app-supplied + role-scoped) and
    **`Widgets.ServiceList`** (the ONE services list: edit via ServiceEditor + lifecycle
    deactivate/reactivate/terminate + optional create; owner sees ALL, coach sees only OWN;
    `/api/services` enforces who-may-change-what). Adopted by BOTH the owner and coach Setup.
  - `client_record.js` → **`Widgets.ClientRecord`** (added 2026-07-09) — the ONE **client record** across
    all three apps, on the same cfg contract (`data` adapter + `actions` map + `fields` + `onNavigate`). It
    renders identity, membership, packages (admin: adjust/remove), the owed statement (admin: void/write-off/
    discount; client: pay/request-refund), payments (admin: refund), bookings (drill to the event story),
    refunds, dependents and activity — role differences are CONFIG only. Fed by the new **`client360`**
    single-source composer (`GET /api/admin/people/<id>` · `GET /api/coach/clients/<id>/360` ·
    `GET /api/me/360`). Adopted by admin `renderPerson` (full staff actions), coach `renderClient` (coaching
    collect/discount only) and the client `#/activity` record view (pay/request_refund only); the three
    previously hand-built person/client renderers were **DELETED** (see §7 — the reversal). **Reconfirmed
    2026-07-14:** an interim coach-only "lean" client view (spun up during the money-as-an-outcome work)
    was retired again — coach `renderClient` renders through the SAME `Widgets.ClientRecord`, scoped
    **server-side** (the composer returns only the coach's own relationship), so there is no coach fork.
- **`window.ServiceEditor`** (`service_editor.js`, whose `packagesCard` is now the ONE pack editor for
  owner + coach) and **`window.ClassUI`** (`class_ui.js`, lazy) — the single-sourced editors the Setup
  sections mount. **`window.AdminUI`** (bottom of `admin_api.js`) — the owner's config editors (clubProfile,
  courtsManage, membershipServices, coachManage). *(`AdminUI.bundlePlans` was DELETED 2026-07-09 with the
  standalone Session-packs section — see §5.)*
- **`window.BookFlow`** (`booking.js`) — the ONE full-screen booking flow (`start(principal, type, opts)`),
  shared by client self-book and coach/admin on-behalf; role variation is `opts` config (§4a).

**Data adapters are inlined** at each SPA's mount call (e.g. admin passes
`data:{ get:(i)=>AdminAPI.bookingStory(i).then(r=>r.booking) }`) rather than a separate `adapters/`
folder — the contract (adapter object injected into the widget) is what matters, not the file layout.

---

## 2. The event story — `Widgets.TransactionDetail` (owner ask #1)

ONE renderer for `/api/me|coach|admin/bookings/:id` (all three lanes return the same `can{}`-driven
shape). The app injects the role adapter + action handlers; the page is byte-identical whether reached
from admin Money/Diary/People, coach Schedule/Clients, or client Bookings. The client's in-app receipt
still lives at `#/billing/order/:id`; the printable receipt at `/receipt.html?order=` is the "detail" for
non-booking sales. Admin/coach group actions (Approval · Session · Client charge · Coaching charge);
client uses a flat row (`grouped:false`).

## 3. The calendar — `Widgets.Calendar` (owner ask #2)

ONE Day/Week/Month calendar; only the filters + layout mode differ, all config. The **admin Diary**
runs on it (data = `API.master`), and its Classes subtab reuses `ClassUI`.

**As of 2026-07-05 the admin Day view is the resource-timeline GRID** (the classic drag-timeline layout
brought into the new console, owner-preferred) — courts + coaches as columns, 06:00–22:00 rows, bookings
as absolutely-positioned `cf-ev` blocks. It's a **config-driven view mode**, NOT a fork: `cfg.grid:true`
turns the Day view into the grid; without it the Day view is the agenda list (still used as a fallback and
by any self-scoped adapter). **Week/Month stay agenda** (a 7-day × all-resources grid is unreadable).
Grid columns come from `cfg.filterBar` (courts by `resource_id`, coaches by `user_id`); the court/coach
dropdowns filter the columns; **coach columns with no lessons that day are hidden** (courts always shown);
classes get their own column. A **coach filter** narrows the grid to one coach: `cfg.coachId` sets the
initial selection (the coach app defaults it to the signed-in coach = "just me"; clear the dropdown to
"All" for the whole club), collapsing the columns to that coach's used courts and filtering events —
including a lesson's held court (which carries the `coach_user_id`) — to that coach. Every block still drills to the ONE event story via `cfg.onNavigate`
(→ `Widgets.TransactionDetail`) — never the old minimal popup. **Walk-in / block-time / desk-pay editing
now all live in the new console** — walk-in via Book a client → guest name, block-time via a **Block time**
button (`POST /api/diary/time-off`, ported when the classic console was retired 2026-07-18), desk-pay on the
transaction record; only the classic diary's drag-to-create/move gesture is gone.

The **coach Schedule keeps its richer hour time-grid** (plus time-off + book-a-client) and the **client
keeps its Home agenda** — legitimately *different views*, not duplicate renders (see §7). The **critical
diary gotcha:** the widget must send FULL-DAY range bounds (`T00:00:00`→`T23:59:59`); a bare `YYYY-MM-DD`
casts to midnight server-side and collapses a same-day query to a zero-width window that shows nothing.

## 4. Setup — `Widgets.Setup` + `Widgets.ServiceList` (owner ask #3)

ONE gold-standard Setup shell, shared by owner + coach; sections are role-gated by each app's section
list:
- **Owner (`ADMIN_SETUP`):** Club profile & payments · Courts & hours · Services & pricing
  (`ServiceList`, all services) · Memberships · Coaches & commission. **(The standalone "Session packs"
  section was DELETED 2026-07-09** — packs now live under the service editor's packages card; see §5.)
- **Coach (`COACH_SETUP`):** Your profile · Weekly hours (both `href` links to their own routes) ·
  Services & pricing (`ServiceList`, own + create) · Classes (`ClassUI`) · Club commission (read-only).

Packs are now created/edited **only under a service** (the service editor's `packagesCard`, which gained a
label + validity/expiry) — for both owner and coach. The old standalone pack surfaces were removed (§5).

**Owner can edit AND deactivate/terminate any service including coach lessons & classes** (inline
lifecycle actions via `PATCH /api/services/<id> {status}`; `services/routes.py` authorises owner=any,
coach=own). The **coach cannot touch club profile, courts, or memberships** — enforced both by the
absent sections and by the server. The gold-standard menu→focused-editor interaction is unchanged.

## 4a. Booking — `window.BookFlow` (the ONE booking flow, incl. on-behalf)

ONE full-screen booking widget (`frontend/js/booking.js`, `window.BookFlow.start(principal, type, opts)`)
serves **self-book AND on-behalf across all three roles** — a second booking sheet would be a golden-rule
bug. Role differences are the `opts` config, never forked code:
- **Client** self-book: no opts (`{}`).
- **Coach** book-for-client (`coach_app.js` `bookForClient`): `{ onBehalf, coachLock: principal.user_id,
  loadPackages }` — the coach is locked to their own lessons; `loadPackages` draws the client's
  coach-scoped prepaid wallet.
- **Admin/owner** book-for-client (`admin_app.js` `adminBookForClient`): `{ onBehalf, loadPackages }` with
  **NO `coachLock`** — the owner picks the coach.

On-behalf mode posts `for_email` + the chosen `product_id` (charging the selected service exactly),
auto-draws a matching pack the client holds (`onBehalfMatchWallet`), and **skips Yoco** (the client isn't
present to pay). The lesson picker is **coach-first** (no "Any coach") with a per-service dropdown.

---

## 5. What was consolidated (the dedup, by wave)

- **Wave 1 (a412d31):** promoted `card/backBar/kv/modal/toLocal/addToCalendar` into `UI`; the three apps
  alias `window.UI.*` (NB: `window.UI`, not the module-local `UI`, which is only assigned in `start()`).
  Loaded `crm_ui.js` in the client shell. **Deleted the dead classic coach console** (`frontend/js/coach.js`
  + `frontend/app/coach.html` — never served; `/coach` and `/coach.html` route to the new SPA). ≈ −1,526 lines.
- **Wave 2 (45a4b95 + 6599dfa):** `Widgets.TransactionDetail`; admin, coach and client all adopt it.
  `UI.statusChip` upgraded to one role-neutral vocabulary.
- **Wave 3 (58039d2 + 804639f):** admin money adopts `CRMUI.lineItems` (owed-orders list + refund
  queue); `statusChip` single-sourced (coach + client alias `UI.statusChip`).
- **Wave 5 (33b435f):** `Widgets.Calendar`; admin Diary adopts it.
- **Wave 6 (b66f1bb + a1d0f4e):** owner service-terminate fix + `Widgets.Setup` / `Widgets.ServiceList`;
  both consoles adopt the shared Setup. Deleted admin `setupMenu/setupServices/drawSetupServices` +
  coach `serviceRow`.
- **Packs consolidation (2026-07-09) — one place per capability.** Now that a pack belongs to ONE specific
  service (`bundle_plan.product_id`), packs are created/edited **only** under a service (the service editor's
  `packagesCard`, which gained label + validity/expiry). **DELETED:** the standalone Setup → "Session packs"
  section + `AdminUI.bundlePlans`; the coach-onboarding "Packs" step + `CoachUI.packs` (coach onboarding is now
  **Profile / Hours / Services**); `AdminAPI.create/patch/deleteBundlePlan` + the `CoachAPI` bundle-plan
  methods; and the `POST/PATCH/DELETE /api/admin/bundle-plans` + all `/api/coach/bundle-plans` routes.
  **KEPT:** `GET /api/admin/bundle-plans` (the offline "issue a pack" picker); `bundles.create_plan/
  update_plan/deactivate_plan` reached only via the services lane (`POST/PATCH/DELETE /api/services/<product_id>/
  packages`).

Net effect: from ~5 copies of every capability to one widget per capability, ~1,700+ lines lighter, the
gold standard preserved.

---

## 6. Guardrails (best practice for this codebase)

1. **One design system** — only `frontend/app/app.css` `cf-*`; no inline component CSS. `UI.clear(host)`
   before rendering results (the `.cf-loading` spinner gotcha).
2. **One widget per capability** — a second render of the same thing is a bug; extend config.
3. **Role = config, not fork** — no `if (role===…)` around render logic.
4. **Data via adapter only** — endpoint knowledge lives in the app, not the widget.
5. **Nav via callback** — widgets never touch `location.hash`.
6. **Widgets are pure render + events** — `mount` returns `{refresh, destroy}`; runnable twice per page.
7. **Promote shared helpers once** — a helper used by ≥2 apps moves to `UI`/`CRMUI` and the copies are
   deleted in the same commit. Reference `window.UI.*` at module-eval time (the local `UI` is set in
   `start()`).
8. **Shells stay thin(er)** — the role apps route → build the adapter/actions → `mount` the widget.
9. **Additive registries** — a new Setup section / calendar filter / action = one config entry.
10. **Gate every step** — `python -m py_compile` tree + `node --check` + a widget render-smoke (load
    `ui.js` + the widget in Node with a stub DOM and a sample payload; assert it builds a tree without
    throwing) + `python -m scripts.test_all`.

---

## 7. Deliberately NOT merged (legitimate view-differences, not duplication)

- **Person / client record.** ~~Coach `renderClient` is a *month-scoped billing* view of a client (month
  nav + Invoice + by-service accordion); admin `renderPerson` is an *all-time* record (membership
  grant/revoke, coach settlement, owed/payments, bookings). They share only a small header — forcing one
  widget would add config complexity, not remove it. Kept as two focused views.~~ **SUPERSEDED 2026-07-09
  — now unified (the deliberate, owner-approved reversal).** The reason this was kept split was *config
  complexity*, driven by the two views assembling their data differently. The **Client 360 consolidation**
  removed that root cause: a new **`client360.get_client_360`** single-source composer returns ONE scoped
  payload (`admin`/`coach`/`client`) for every lens, so admin `renderPerson`, coach `renderClient` AND the
  client `#/activity` record view now all render through the ONE **`Widgets.ClientRecord`** (role
  differences = the standard `data`/`actions`/`fields` config, no forked render code). The three
  hand-built renderers were deleted — a second render *was* the bug. This was a conscious revisit of the
  §7 exception, not a golden-rule violation: once the data was single-sourced, the config complexity that
  justified the split no longer existed. Gates held at **43 / 195 / 47**.
- **Coach Schedule time-grid** (hour × 7-day grid + time-off + book-a-client) and **client Home agenda**
  — richer, distinct views the audit recommended keeping; they share the event-row + drill-to-event.
- **Client billing-by-category** — intentional, owner-loved bespoke design; not a generic money list.

---

## 8. Verification note

Every step was gated by `py_compile`, `node --check`, dependency-free **render-smokes** (each widget
built a full DOM tree from a sample payload in Node without throwing), and `python -m scripts.test_all`
(43/142/35). A live Clerk-authenticated click-through of all three apps was the one thing not done from
this environment (auth-gated) and is the recommended final acceptance check.

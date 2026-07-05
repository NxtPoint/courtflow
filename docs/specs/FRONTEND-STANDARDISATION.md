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
- **`window.ServiceEditor`** (`service_editor.js`) and **`window.ClassUI`** (`class_ui.js`, lazy) — the
  single-sourced editors the Setup sections mount. **`window.AdminUI`** (bottom of `admin_api.js`) — the
  owner's config editors (clubProfile, courtsManage, membershipServices, bundlePlans, coachManage).

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
classes get their own column. Every block still drills to the ONE event story via `cfg.onNavigate`
(→ `Widgets.TransactionDetail`) — never the old minimal popup. **Walk-in / block-time / desk-pay editing
were deliberately NOT ported** — they remain in the classic diary (`/admin-classic`, via the widget's
`classicLink`); folding them into the grid is a possible post-launch follow-up.

The **coach Schedule keeps its richer hour time-grid** (plus time-off + book-a-client) and the **client
keeps its Home agenda** — legitimately *different views*, not duplicate renders (see §7). The **critical
diary gotcha:** the widget must send FULL-DAY range bounds (`T00:00:00`→`T23:59:59`); a bare `YYYY-MM-DD`
casts to midnight server-side and collapses a same-day query to a zero-width window that shows nothing.

## 4. Setup — `Widgets.Setup` + `Widgets.ServiceList` (owner ask #3)

ONE gold-standard Setup shell, shared by owner + coach; sections are role-gated by each app's section
list:
- **Owner (`ADMIN_SETUP`):** Club profile & payments · Courts & hours · Services & pricing
  (`ServiceList`, all services) · Memberships · Session packs · Coaches & commission.
- **Coach (`COACH_SETUP`):** Your profile · Weekly hours (both `href` links to their own routes) ·
  Services & pricing (`ServiceList`, own + create) · Classes (`ClassUI`) · Club commission (read-only).

**Owner can edit AND deactivate/terminate any service including coach lessons & classes** (inline
lifecycle actions via `PATCH /api/services/<id> {status}`; `services/routes.py` authorises owner=any,
coach=own). The **coach cannot touch club profile, courts, or memberships** — enforced both by the
absent sections and by the server. The gold-standard menu→focused-editor interaction is unchanged.

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

- **Person / client record.** Coach `renderClient` is a *month-scoped billing* view of a client (month
  nav + Invoice + by-service accordion); admin `renderPerson` is an *all-time* record (membership
  grant/revoke, coach settlement, owed/payments, bookings). They share only a small header — forcing one
  widget would add config complexity, not remove it. Kept as two focused views.
- **Coach Schedule time-grid** (hour × 7-day grid + time-off + book-a-client) and **client Home agenda**
  — richer, distinct views the audit recommended keeping; they share the event-row + drill-to-event.
- **Client billing-by-category** — intentional, owner-loved bespoke design; not a generic money list.

---

## 8. Verification note

Every step was gated by `py_compile`, `node --check`, dependency-free **render-smokes** (each widget
built a full DOM tree from a sample payload in Node without throwing), and `python -m scripts.test_all`
(43/142/35). A live Clerk-authenticated click-through of all three apps was the one thing not done from
this environment (auth-gated) and is the recommended final acceptance check.

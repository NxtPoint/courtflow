# Frontend standardisation — one widget per capability

Status: **PLAN — proposed 2026-07-03, awaiting owner sign-off before any code changes.**
Frontend only; the backend is clean and is NOT touched. Scoped from two research passes (a full
frontend divergence audit + a component-architecture design), reconciled here.

Owner's mandate (verbatim): *"we have too much history and inefficient code … I fear we are building
lots of bespoke pages/widgets. Let's standardise and componentise … 1 widget for each service (e.g.
calendar) used all over where required — the only difference would be the filters … when we click
through to transaction detail it should be 1 standard page always, no matter where we click from …
keeping all our gold-standard stuff."*

**The Setup tab is the signed-off gold standard and is preserved byte-for-byte** ([[gold-standard-setup-ux]]).
This sprint converges everything else onto the same "one component, reused everywhere" discipline.

---

## 1. The problem, in numbers (from the audit)

The three role SPAs (`client.js`, `coach_app.js`, `admin_app.js`) each hand-roll their own copy of the
same capabilities. The client shell doesn't even load `crm_ui.js`, so it re-implements every money
widget inline.

| Capability | # implementations today | Should be |
|---|---|---|
| Booking/transaction **detail** ("event story") | **5** (client `renderBookingStory` · coach `renderEvent` · admin `renderEvent` · client `renderOrder` · `receipt.js`) | **1** |
| **Calendar** / diary / agenda | **6** (admin agenda · coach week-grid · classic resource-timeline · client agenda · dashboard mini · dead coach) | **2 views, 1 data+row** |
| **Person / client 360** | **3** (coach `renderClient` · admin `renderPerson` · client `renderProfile`) | **1** (+ client self-edit) |
| **Money / statement** widgets | client inline · admin inline · coach via CRMUI | **1 set (CRMUI)** |
| `card / backBar / kv / loading / set / toLocal / addToCalendar` | **3 each, byte-identical** | **1 (on `UI`)** |
| `money()` / `statusChip()` wrappers | **3–4 each** | use `UI.*` directly |
| `modal()` | **5** (3 SPA copies + `CRMUI.drawer` + `ClassUI.modal`) | **1 `UI.modal`** (+ drawer) |
| month-nav (`shiftMonth/monthLabel/dayLabel`) | **3** | **1 `UI.monthNav`** |
| Refund/dispute queue | **3** | **1 (`CRMUI.requestQueue`)** |
| **Setup / config editors** | already single-sourced ✅ (`AdminUI`, `ServiceEditor`, `ClassUI`) | keep; fold coach's inline editors in |

**Dead files reachable by no live route** (delete): `coach.js` (78k), `my.js`, `account.js`,
`portal.js`, `home.js`. **Orphan-served duplicates** (delete once links confirmed gone): `statement.js`,
`settings.js`. **Kept fallback:** `admin.js` at `/admin-classic` until the diary timeline ports over.

The good news: for every duplicated capability the difference between roles is **not structural** — it
is one of four config axes: **(a)** data source (`/api/me` vs `/api/coach` vs `/api/admin`, parallel
payloads), **(b)** filters/scope (month-scoped, court/coach filters), **(c)** allowed actions (already
server-driven by the `can{}` object in the booking payload), **(d)** visible fields. So **one widget +
a role-config object** covers all three — no forked render code.

---

## 2. The standard: the widget contract

Every widget is an IIFE attaching one object to `window.Widgets.<Name>` with a single `mount`:

```
window.Widgets.TransactionDetail = {
  mount(host, cfg) { /* render */ return { refresh, destroy }; }
};
```

`cfg` — the ONE configuration object, fixed minimal shape:

```
cfg = {
  role,        // "client" | "coach" | "admin" — chooses DEFAULTS only, never branches render logic
  scope,       // identity: { clubId, userId?, coachId?, courtId?, orderId? }
  filters,     // view state: { view:"day", date, coachId, courtId, month }
  actions,     // CAPABILITY MAP: { void:{label,tone,confirm?,run}, refund:{...}, collect:{...} }
  data,        // ADAPTER — the ONLY way the widget reads/writes; never a raw endpoint
  fields,      // optional visible-field allow-list: { showCommission:false, showNotes:false }
  onNavigate,  // fn(target) — target = { kind:"txn"|"person"|"event", id }
}
```

`mount` returns `{ refresh(patch?), destroy() }`. **All state lives in the `mount` closure** (no
module-level mutable state), so a widget can be mounted twice on one page.

Five rules make role = config, not code:
1. **No `if (role==='admin')` around render logic.** Variation flows through `actions` / `fields` /
   `filters` / `data`. `role` only picks defaults.
2. **Data via adapter only.** The widget calls `cfg.data.method()`, never `API`/`AdminAPI`/`CoachAPI`/
   `TFAuth`. Each capability defines one adapter interface; each SPA passes a concrete adapter that
   **normalises its payload** so the widget stays dumb (e.g. `resource_id` vs `court` → one `CalEvent`).
3. **Actions as a capability map.** The widget renders a button per key present (absent key = no button
   = the gate), in a canonical order; `run(ctx)` returns a promise, the widget shows spinner/toast and
   `refresh()`s. Admin passes `{void,refund,reassign}`; coach `{collect,discount}`; client
   `{requestRefund}` — same renderer.
4. **Navigation via callback.** No widget reads/writes `location.hash`; it calls
   `onNavigate({kind:"txn",id})` and the SPA router owns the hash. This is what guarantees "click a
   transaction from anywhere → the identical detail page."
5. **Widgets are pure render + events.** Inputs = `cfg`; outputs = DOM under `host`, adapter calls,
   `onNavigate` calls. No globals mutated, no routing, no `fetch`.

---

## 3. The canonical widget set (~12)

Reuse existing code as the single source wherever it already nails the job.

| Widget | Single source (canonical) | Per-role config |
|---|---|---|
| **TransactionDetail** (event story) | admin `renderEvent` grouped superset + `receipt.js` | admin: void/refund/reassign/desk-pay; coach: collect/discount; client: request-refund + `fields.showCommission=false` |
| **Calendar** | admin `renderDiary` agenda + coach `drawWeek` grid; **one `eventRow` + one events adapter** | admin: court/coach/client filters; coach: `coachId=self` forced; client: `userId=self`, read-only |
| **PersonRecord** | merge coach `renderClient` + admin `renderPerson` | admin: all-time + membership/settlement/void; coach: month-scoped + invoice; client self-edit stays separate |
| **StatementMoney** | `CRMUI.statementTable` / `lineItems` | admin: void/write-off; coach: collect/discount; client: settle/part-settle |
| **ActivityFeed** | `CRMUI.activityFeed` | adapter narrows scope |
| **StatStrip** | `CRMUI.stats` | each role feeds own metrics |
| **RequestQueue** | `CRMUI.requestQueue` | coach: lesson queue; admin: refunds; client: "needs attention" |
| **RosterList** | `ClassUI` | admin/coach manage; client read-own |
| **Setup / EditorFramework** | generalise `AdminUI` + a `SETUP_SECTIONS` registry | see §4 |
| **FilterBar** | promote from admin diary controls | admin: court+coach+client; coach: client+service; client: hidden |
| **MonthNav** | promote (one copy) | identical |
| **Modal / Drawer / Chip** | one `UI.modal` + `CRMUI.drawer` + `UI.statusChip` | identical |

---

## 4. The owner's three explicit asks — how the standard delivers them

**(1) ONE transaction-detail page, identical from everywhere.** `Widgets.TransactionDetail` renders
the `can{}`-driven payload the three lanes already return (`/api/me|coach|admin/bookings/:id` +
`/api/billing/receipt/:id`). The router injects the role adapter; the page is byte-identical whether
reached from admin Money, coach statement, or client account. Client-only `renderOrder` and the
standalone `receipt.js` both collapse into it.

**(2) ONE calendar, only filters differ.** One events **adapter** + one `eventRow` feed the views;
`FilterBar` supplies the role's filters (admin: court/coach/client; coach: self + own clients/services;
client: own). Two visual paradigms are intentionally kept (an agenda overview + a week time-grid) — but
they share the data, the row, and the filter bar, so they're one widget family, not six renders.

**(3) ONE Setup surface, sections exposed per role.** `Widgets.Setup.mount(host,{role})` reads a single
`SETUP_SECTIONS` registry; each entry has a `roles` allow-list and points at an existing editor:

```
SETUP_SECTIONS = [
  { key:"clubProfile", label:"Club profile",     roles:["admin"],          editor: AdminUI.clubProfile },
  { key:"payments",    label:"Payments",         roles:["admin"],          editor: PaymentsEditor },
  { key:"courts",      label:"Courts & hours",   roles:["admin"],          editor: AdminUI.courtsManage },
  { key:"services",    label:"Services & pricing",roles:["coach","admin"], editor: ServiceEditor },
  { key:"classes",     label:"Classes",          roles:["coach","admin"],  editor: ClassUI },
  { key:"memberships", label:"Memberships",      roles:["admin"],          editor: AdminUI.membershipServices },
  { key:"bundles",     label:"Session packs",    roles:["admin"],          editor: AdminUI.bundlePlans },
  { key:"coaches",     label:"Coaches & pay",    roles:["admin"],          editor: AdminUI.coachManage },
];
```

Setup filters by `role`, renders each surviving section as the gold-standard summary-row → full-screen
editor (unchanged). Coach Setup and admin Setup become the **same code path over the same registry**.
Adding a section = one array entry, automatically correct for every role. `settings.js` (a duplicate of
the admin Setup shell) is deleted; coach's parallel inline service/pack editors fold into `AdminUI`.

---

## 5. File / namespace organisation (no bundler)

```
frontend/js/
  ui.js            → window.UI     (primitives — promote the triplicated helpers here)
  crm_ui.js        → window.CRMUI  (composed presenters — extend, adopt in client & admin)
  widgets/
    _registry.js   → window.Widgets = {}  (+ SETUP_SECTIONS)
    txn_detail.js · calendar.js · person.js · money.js · setup.js · roster.js ·
    filterbar.js · monthnav.js · modal.js · requestqueue.js
  adapters/
    admin_adapters.js · coach_adapters.js · client_adapters.js
  client.js · coach_app.js · admin_app.js   (THIN shells: route → build role adapter → mount widget)
```

Load order in each shell (absolute paths): `auth_client → ui → crm_ui → *_api → widgets/_registry →
widgets/* → adapters/<role> → <role>_app`. **The client shell must start loading `crm_ui.js`** (today
it doesn't — the root cause of client re-implementing money widgets). Widgets depend only on
`UI`/`CRMUI` + their injected `cfg`; no widget requires another at load time.

---

## 6. Migration waves (incremental, always-green, reversible)

For each capability: build the widget + adapter → wire the highest-divergence SPA and swap its render to
`Widget.mount` → wire the other two → **delete the per-SPA render only once the widget is verified in the
browser.** Never delete the old path before the new one renders. Gate every step: `py_compile` tree +
`node --check` + a browser smoke of all three apps.

- **Wave 1 — zero-risk consolidation (S).** Lift the byte-identical helpers (`card`, `backBar`, `kv`,
  `loading`, `set`, `toLocal`, `addToCalendar`) to `UI`; replace local `money()`/`statusChip()` with
  `UI.*`; add one `UI.modal` + `UI.monthNav`; **load `crm_ui.js` in the client shell.** Delete the dead
  files (`coach.js`, `my.js`, `account.js`, `portal.js`, `home.js`). Huge line reduction, near-zero risk.
- **Wave 2 — TransactionDetail (M, owner ask #1).** One `Widgets.TransactionDetail` + 3 adapters; the
  single biggest UX consistency win. Collapse client `renderOrder` + `receipt.js` into it.
- **Wave 3 — Money widgets (M).** Adopt `CRMUI.stats/statementTable/lineItems/activityFeed/requestQueue`
  in client + admin (coach already proves the pattern). Delete the inline re-implementations.
- **Wave 4 — PersonRecord (M).** Merge coach + admin into `Widgets.PersonRecord(payload, cfg)`; keep
  client self-edit separate.
- **Wave 5 — Calendar (L).** Extract one events adapter + `eventRow` + `FilterBar`; point all views at
  them; port the classic resource-timeline's walk-in/desk-pay into the new diary, then **retire
  `admin.js` + `/admin-classic`.**
- **Wave 6 — Setup framework (M, last, lowest-risk).** Introduce `Widgets.Setup` + `SETUP_SECTIONS` as
  the shell around the UNCHANGED `AdminUI` editors; fold coach's inline editors into `AdminUI`; delete
  `settings.js`. **The gold-standard interaction stays exactly as it is.**

Highest ROI first: Wave 1 removes the most code for the least risk; Wave 2 is the owner's top ask.

---

## 7. Guardrails (best practice for this codebase)

1. **One design system** — only `frontend/app/app.css` `cf-*`; no inline component styles, no per-widget
   CSS. Render results with `UI.clear(host)` (the `.cf-loading` spinner gotcha).
2. **One widget per capability** — a second render of "a transaction"/"the calendar" is a bug; extend
   config, don't fork.
3. **Role = config, not fork** — no `if (role===…)` around render logic.
4. **Data via adapter only** — endpoint knowledge lives in adapters; payloads normalised there.
5. **Nav via callback** — no widget touches `location.hash`.
6. **Widgets are pure render + events** — `mount` returns `{refresh, destroy}`; runnable twice per page.
7. **Promote shared helpers once** — a helper used by ≥2 SPAs moves to `UI`/`CRMUI` and the copies are
   deleted in the same commit. No new local `money()`/`card()`/`modal()`.
8. **Shells stay thin** — `client/coach/admin_app.js` only: hash → pick widget → build adapter → mount.
9. **Additive registries** — new Setup section / calendar filter / action = one entry with a `roles`
   allow-list, correct for all three apps automatically.
10. **Gate every step** — compile + `db` twice + browser smoke of all three apps before deleting any
    legacy render.

---

## 8. One-line summary

The difference between the three apps is **configuration, not code** — so every capability collapses to
ONE widget (`mount(host, cfg)`) fed by a role adapter, with the gold-standard Setup preserved. Six
always-green waves, starting with a zero-risk helper/dead-code sweep and the owner's one-transaction-page
ask, take the frontend from ~5 copies of everything to one.

# The Unified Transaction Record (design — for sign-off)

**Status: DESIGNED, not yet built.** The owner's mandate: there is ONE and only one place a
transaction is ever seen — identical for client, coach and admin. It opens with a **summary** of the
event and its current status, drills to a **plain-English chronological log**, **reconciles to
billing** (paid vs owed) and lets you **settle the owed** or request a **reversal** — all from inside
the one record. Coach/admin = a list of clients → click a client → **exactly** what the client sees.

> **Golden rule (enshrined):** this is ONE widget — `window.Widgets.TransactionDetail` — extended,
> never forked. Role differences are config (data adapter · actions map · fields), never a second
> render. See [FRONTEND-STANDARDISATION.md](FRONTEND-STANDARDISATION.md).

## 0. The core insight — join two things that already exist
1. **The event story** (`booking_story`/`coach_booking_story`/`admin_booking_story`, `diary/bookings.py`)
   = a rich **summary + charge + actions** block — but **no chronological log**, and keyed on
   `diary.booking.id` so **class enrolments have no record at all**.
2. **The transaction log** (`billing/activity.py` → `CRMUI.activityFeed`, the gross feed on coach/admin
   Money) = a real chronological, plain-English, role-aware feed — but a **club-wide firehose**, not
   per-event, and never shown inside the story.

**Canonical record = the story's summary + `charge`, with the transaction log filtered to THIS one
event spliced in, plus a corrected paid/owed block.** Both halves already read the authoritative money
tables. This is unify + reconcile, not new machinery.

## 1. The record data shape (one contract, all roles, role-adapted by the adapter)
```jsonc
{
  "record_id": "booking:<uuid>" | "enrolment:<uuid>" | "order:<uuid>",   // unified key
  "kind": "court" | "lesson" | "class" | "sale",
  "summary": {
    "title": "60-min lesson with Coach Sam",     // plain-English event line
    "where": { "club_name": "KCC", "court_name": "Court 1", "address": "…" },
    "when":  { "starts_at": "…", "ends_at": "…", "duration_minutes": 60, "is_future": false },
    "status": "confirmed",                        // the EVENT status — the one-glance answer
    "last_action": { "label": "Paid R400 via Yoco", "at": "…" }
  },
  "log": [                                        // chronological, plain-English, ONE event only
    { "at": "…", "actor": {"name":"You","is_you":true}, "kind": "order_created",
      "label": "Booking made", "amount_minor": 15000, "direction": "neutral", "visibility": "all" }
    // … payment / confirmed / cancelled / refunded / written_off / discounted / waitlisted / commission
  ],
  "charge": {                                     // DERIVED from payment ROWS — must tie to the statement
    "currency": "ZAR", "gross_minor": 40000, "paid_minor": 40000, "refunded_minor": 0,
    "net_paid_minor": 40000, "owed_minor": 0, "written_off_minor": 0,
    "settlement_mode": "online", "state": "paid", "order_id": "…"
  },
  "arrears": { "gross_minor": 40000, "status": "written_off" } | null,   // coaching split (lesson only)
  "can": { "pay": false, "receipt": true, "refund": false, "cancel": false, … }
}
```

## 2. The reconciliation invariant (the single truth test)
Per order, straight from the tables (never a cached scalar):
```
gross    = order.amount_minor
paid     = Σ billing.payment  (direction='charge', status='succeeded')
refunded = Σ billing.payment  (direction='refund',  status IN ('refunded','succeeded'))
net_paid = paid − refunded
owed        = gross IF order.status='open'        ELSE 0        # the statement number
written_off = gross IF order.status='written_off' ELSE 0
state    = covered|owed|pending|paid|part_refunded|refunded|written_off|void   # from the sums, NOT order.status alone
```
**INVARIANT (must always hold):** `statement.total_owed_minor == Σ owed over the client's orders
(settled_by_order_id IS NULL)`. Wire this assertion into `scripts/test_statement_reconciliation` so
drift can never ship again.

## 3. The reconciliation bugs to fix FIRST (make paid/owed trustworthy)
| # | Sev | Bug | Where | Fix |
|---|---|---|---|---|
| 1 | **Critical** | Cancelled **class** stays OWED (the reported bug) | `classes.cancel_enrolment` never voids the order (cf. `bookings.py:783`); self-heal skips enrolment lines (`statement.py:88`) | Void the unpaid order in `cancel_enrolment` (mirror `cancel_booking`); extend `_void_phantom_cancelled_orders` to cancelled-enrolment orders (heals existing stuck rows) |
| 2 | High | Partial refund flips whole order to `refunded`, loses net kept | `events.py:178`; `_booking_charge` `bookings.py:1172` | Derive paid/refunded/**net**/state from `billing.payment` sums; add computed `part_refunded` |
| 3 | High | Written-off client orders **vanish** from the monthly view | `me.py:69` status filter | Add `written_off`/`void`; render struck, owed=0 |
| 4 | High | Class enrolments have **no** drill-through record | no `enrolment_story` | Add `enrolment_story` + `record:enrolment:<id>` |
| 5 | Med | "YOU OWE" (all-time) vs monthly breakdown look inconsistent | `client.js` vs `me.py:33` | Owed = all-time statement; relabel breakdown "This month"; drive owed drill from `statement.items` |
| 6 | Med | Per-event log doesn't exist; club-wide log not filterable to one event | `activity.py:49`; `txn_detail.js` | Add event filter; render `log` in the widget |
| 7 | Med | "Refund & cancel" can't cancel a **class** (booking-only) | `yoco routes.py:547` | Fall back to enrolment cancel when no booking line |

## 4. The layout (mobile-first; all `cf-*`, no inline CSS)
Three zones inside the existing widget `wrap`:
- **Summary card:** `[chip · type/duration]  [BIG status chip]` · plain event line · date + time range ·
  venue · a muted **last-action** line · then the **money line**:
  `You owe R150  [ Settle R150 › ]` (owed) · `Paid R400 · R0 outstanding` (paid) ·
  `Covered by your membership` (covered) · `Refunded R400` · `Written off — nothing to pay`.
- **Actions** (existing map; grouped for coach/admin, flat for client).
- **History card:** latest 3 log rows + `Full history ⌄` toggle; each row `[icon] [plain label]
  [±amount chip] [date]`, oldest→newest (reads as a story). Reuses `activityFeed`'s row + `ACT_ICON`,
  promoted to a shared `UI.logRow`.

Example client log (court booked→paid→cancelled→refunded): 🎾 You booked Court 1 · 💳 Paid R150 via
Yoco (+R150) · ✖️ You cancelled · ↩️ Refunded R150 (−R150). Coach/admin see actor names + commission
lines (`＋ Coach commission R280 accrued`); client sees "you" and never commission — **config, not fork**.

## 5. Widget cfg additions (backward-compatible; older payloads degrade to today's render)
```js
fields: { …, showLog:true, logLimit:3, logNewestFirst:false, showCommission: role!=='client' }
actorLabel?: fn(entry)->string      // default: "you" vs actor.name by role
summaryLine?: fn(b)->string         // default composed from type+duration+coach/court
// payload: b.log[] (staff-only lines filtered OUT BY THE ADAPTER for the client, like the endpoint split today)
```
Role filtering of staff-only log lines happens in the **data adapter** (`/api/me` omits `visibility:'staff'`
+ sets `actor.is_you`), never in the widget. Same `render()` for all three apps.

## 6. Refund / reversal — where it lives
- **Client:** `Request refund` → reason → `refund_request` (routed to coach/club review). Log gains
  "📩 You requested a refund · pending review". Never a raw Yoco reversal.
- **Admin:** the actual Yoco reversal = `refund` action → a **modal** ("Refund R400 via Yoco" + checkbox
  "Also cancel the booking + free the slot" = Refund-only vs Refund-&-cancel) → `yocoRefund` → record
  re-mounts, log gains "↩️ Refunded". Single reversal seam, admin-only.
- Money-moving actions use a modal; pure state changes use the widget's one-line confirm. After success
  the record **re-renders in place** — never a second page.

## 7. The ONE endpoint + reuse/delete
- `GET /api/record/<record_id>` (role from principal) returns §1. The three story endpoints become thin
  shims (prefix `booking:`), so the widget contract is unchanged. `enrolment_story` puts classes on equal
  footing; `order:<id>` covers pure sales (memberships/packs).
- **REUSE:** `txn_detail.js` (home of it all), `activityFeed` row builder + `ACT_ICON` (→ `UI.logRow`),
  `UI.statusChip`, `b.charge`/`b.arrears`, the three action maps, `onNavigate` (list→client→same record).
- **DELETE the duplication:** the standalone `activityFeed` *surface* on coach/admin Money is redundant
  once every transaction carries its own log (keep only if a roll-up adds value, via the same `UI.logRow`).
  `renderOrder`/`receipt.html` stay as the **printable** artifact (a different output, not a duplicate view).

## 8. Build order (reconcile first, then present it)
1. **Phase 1 — reconciliation fixes (backend, no UI):** bugs #1–#3, #7 + the invariant in the harness.
   Makes paid/owed correct and self-heals tomos@nedbank's stuck class. *Validate against real rows.*
2. **Phase 2 — record backend:** `log[]` + corrected `charge` on the story payloads; `enrolment_story`;
   the `/api/record` shape (shims). Classes become first-class records.
3. **Phase 3 — the widget UX:** summary band + money line + Settle CTA + History log + expand; promote
   `UI.logRow`; wire all three apps; absorb `activityFeed`.
4. **Phase 4 — refund placement + polish + render-smokes.**

**Gate every phase:** `py_compile` + `node --check` + widget render-smoke (every state: owed/paid/covered/
refunded/written_off/waitlisted) + `python -m scripts.test_all` (43/142/35). Live Clerk click-through =
final acceptance.

# Admin Portal — Phase 2: architecture + backlog

Status: **PLAN — proposed 2026-07-03, awaiting owner sign-off on priorities.** Nothing built yet.
This is the "make the owner/admin portal world-class" phase that follows the Phase-1 admin SPA
redesign (Home · People · Money · Diary · Setup + the event story — steps 1–3 shipped; see
`ADMIN-REDESIGN.md`). It was scoped from three research passes: a competitive benchmark of
world-class club platforms, a codebase reuse audit, and an architecture design — all reconciled here.

Owner's mandate (the design constraint, verbatim):
> "MAKE SURE WE HAVE A SOLID ARCHITECTURE AND THAT EVERYTHING IS COMPONENTISED WITH MAXIMUM REUSE,
> DON'T JUST DUMP CODE AND CREATE MILLIONS OF TABLES."

**The whole of Phase 2 adds exactly ONE new table.** Every theme the owner named — marketing
triggers, website performance, operator alerts, enhanced analytics, drop-off/retention trends,
per-line-of-business performance, customer surveys — is delivered as *configuration* over five
reusable primitives sitting on the existing event bus, notification inbox, and analytics layer.

---

## 1. The core insight

CourtFlow already has the hard parts of a world-class admin portal:

- an **append-only event bus** — `emit(event, payload)` (`marketing_crm/tracking/client.py:37`,
  fire-and-forget, non-fatal) fanning out to `core.usage_event`, notifications, and Klaviyo.
  `emit()` accepts **any** event string, so new signal types need **no schema change**.
- a generic **notification inbox + email ledger** — `core.notification` (kind/title/body/link +
  `data` JSONB), always written in-app, email best-effort (`marketing_crm/notifications.py`).
- a guarded **analytics read-layer** — `analytics/repositories.py` (missing table → empty panel,
  never 500) + a first-party page-view beacon already capturing device/geo/UTM/time-on-site + a
  free-form `props` dict for custom funnel steps (`marketing_crm/tracking/beacon.py`).
- a generic event store — **`core.usage_event`** (`event_type` + `metadata` JSONB + `ref_type/ref_id`),
  plus `core.nps_response`, `core.consent`, `core.acquisition` (UTM) already built.
- a shared render kit — `CRMUI.*` (stats/bars/activityFeed/statementTable), ECharts in `overview.js`,
  and the `focusCard`/`statLine` command-center components in `admin_app.js`.

So Phase 2 is **not** a set of new subsystems. It is five thin primitives that compose these, plus
one config table. A new feature becomes "a new metric key + a dropped-in card" or "a new rule config
+ a new `event_type` string" — never a new pipeline, never a new table.

---

## 2. The five primitives

Mnemonic: **Read · React · Alert · Capture · Render.**

### P1 — Insight read-layer (`insights/`) — ZERO new tables
The single guarded aggregation layer over `core.usage_event` + `billing.*` + `diary.*`. **Every number
in Phase 2 resolves through here** — nothing re-queries raw tables ad hoc, so "revenue" can never be
computed three inconsistent ways.

- **Internal abstraction: a metric registry** — a Python dict `{key → (source, grain, agg builder)}`,
  e.g. `active_members`, `court_utilisation`, `lesson_fill_rate`, `revenue_by_lob`, `nps_rolling`,
  `churn_risk`. A new metric is a dict entry, not a table. Extends the guarded pattern already in
  `analytics/repositories.py` (`_guard`, `new_vs_returning` already computes a first-seen cohort split).
- **API (read-only, `club_id`-scoped, all guarded):**
  - `GET /api/insights/metric/<key>?days=&group_by=&segment=` → `{series:[{t,value}], total, delta_pct}`
  - `GET /api/insights/cohort/<key>?cohort=signup_month&metric=retained` → grid
  - `GET /api/insights/funnel/<name>` → ordered `[{step, count, conv_pct}]` (steps = a list of `event_type`s)
  - `GET /api/insights/breakdown/<key>?dim=lob|source|coach|tier` → `[{label, value, share}]`
- **Powers:** every BI, retention, per-LOB, funnel, benchmark feature.

### P2 — Automation / trigger engine (`automation/`) — the ONE new table
Subscribes to the *existing* `emit()` stream and fires actions (notify / email / tag / grant-discount /
raise-alert). This is the entire "marketing triggers & automation" theme, and it also produces every
operator alert (see P3).

- **The one genuinely-new table** — justified because rules must be owner-editable and audited:
  `automation.rule(id, club_id, name, trigger jsonb, condition jsonb, action jsonb, enabled bool,
  created_at)`.
  - `trigger` = `{event_type}` (event-driven) or `{schedule:'daily'}` (time-driven).
  - `condition` = a small JSON predicate over the event metadata or a P1 metric threshold.
  - `action` = `{type:'notify|email|tag|discount|alert', params}`.
- **No `rule_run` table.** Every firing is a `usage_event` `event_type='automation_fired'`,
  `metadata={rule_id, target}` — reusing the audit log we already have; idempotency = a uniqueness
  check on that event (mirrors `apply_payment_event`).
- **Execution seam:** one `automation.evaluate(session, event, payload)` block added to the `_emit`
  fan-out (`marketing_crm/tracking/client.py:96–118`) — synchronous, non-fatal, exactly how
  notifications were added. **Scheduled** rules run on the free-tier pattern (§5): a free GitHub
  Action → guarded `/api/cron/automations`, or lazy on-read — never a paid cron.
- **API:** `GET/POST/PATCH/DELETE /api/admin/automation/rules`,
  `POST /api/admin/automation/rules/<id>/test` (dry-run against the last 30 days of events).
  Actions delegate to existing services (`notify`→`core.notification`, `email`→SES/Klaviyo,
  `tag`→P4, `discount`→`billing`).
- **Powers:** win-back, at-risk nudges, welcome journeys, review requests, dunning, every operator alert.

### P3 — Alert layer (`alerts` — a thin façade over `core.notification`) — ZERO new tables
Operator alerts are just `core.notification` rows addressed to admin users with `kind='alert.*'` and a
`data` payload carrying `severity` + `metric` + `link`. No new store.

- **API:** `GET /api/admin/alerts?severity=&unread=` (a filtered read of the inbox),
  `POST /api/admin/alerts/<id>/ack` (sets `read_at`).
- Alerts are *produced* by a P2 rule whose action is `alert`, evaluating a P1 metric threshold.
  So "operator alerts" = P2(trigger on metric) → P3(notification): one pipeline, two primitives,
  zero bespoke code. The admin Home already surfaces an "approve/decide" column
  (`admin_app.js` `renderHome`) — the alert feed slots beside it.
- **Powers:** low-fill/utilisation, payment-failure & refund spikes, no-show trends, coach-idle,
  arrears-threshold, website-traffic anomalies.

### P4 — Feedback & tag capture (`feedback/`) — ZERO new tables
Surveys, post-session ratings, drop-off reasons, and lightweight customer tags/segments — **all stored
as events, not bespoke tables.**

- A survey **response** is a `usage_event` `event_type='survey_response'`, `metadata={survey_id, answers}`.
  NPS keeps using `core.nps_response` (already end-to-end: `record_nps` + `nps_submitted` + the `nps`
  analytics panel).
- A **tag** on a customer is a `usage_event` `event_type='tag_applied'`, `metadata={tag, source}`
  (current tags = latest state derived by P1). Segmentation with zero schema.
- A survey **definition** is JSON config (a static registry entry, or a `club.policy` JSONB column for
  owner-authored ones) — no table.
- **API:** `POST /api/feedback/response` (public, from a portal/email link),
  `GET /api/insights/breakdown/survey/<id>` (analysis via P1),
  `POST /api/admin/customers/<id>/tag`.
- **Powers:** post-lesson CSAT, cancellation-reason capture, membership-exit survey, the review funnel,
  tag-driven marketing segments.

### P5 — Frontend insight kit (`crm_ui.js` extensions) — ZERO backend
A small vocabulary so no page hand-rolls a chart. Extends `CRMUI` and the ECharts wiring already in
`overview.js`:

- `CRMUI.insightCard({metric, format, spark})` — KPI tile with delta + sparkline, fed by a P1 metric key.
- `CRMUI.chartPanel({endpoint, type})` — line/bar/donut bound to a P1 endpoint (title, range-picker,
  empty-state, cold-start timeout all built in — set once).
- `CRMUI.funnel({name})` and `CRMUI.cohortGrid({key})` — bound to P1 funnel/cohort endpoints.
- `CRMUI.ruleEditor(...)` — the shared trigger→condition→action builder for P2, reused by every
  automation surface.
- **Discipline:** stay in `cf-*` classes (single `app.css`); render with `UI.clear(box)` before
  appending (the `.cf-loading` spinner gotcha). A new dashboard tab = a layout of `insightCard`s +
  `chartPanel`s pointing at metric keys — declarative, not bespoke.

**Net-new tables across all of Phase 2: ONE (`automation.rule`).** Everything else is new `event_type`
strings, JSON config, and `notification.kind` conventions.

---

## 3. Prioritised feature backlog

Legend — Reuses: P1–P5 · New tables (target 0) · Value: Must / High / Nice · Effort: S / M / L ·
Deps. Ranked within each theme.

### Retention & lifecycle
| # | Feature | One-liner | Reuses | Tbl | Val | Eff | Deps |
|---|---|---|---|---|---|---|---|
| 1 | **At-risk member detection** | Score members on booking-cadence drop-off; flag + auto staff task | P1+P2+P3 | 0 | Must | M | — |
| 2 | Win-back automation | Dormant N days → nudge/offer (expired vs cancelled tracks) | P2+P4+email | 0 | High | M | SES* |
| 3 | Retention cohort grid | Retention by signup month, 30/90/365d | P1+P5 | 0 | High | M | — |
| 4 | Renewal & lapse forecast | Memberships expiring / likely to lapse | P1+P3 | 0 | High | M | — |
| 5 | Lifecycle stage board | lead→trial→active→at-risk→churned (via tags) | P4+P1 | 0 | High | M | — |
| 6 | Welcome / onboarding journey | signup → day-3 → day-7 nudges | P2 | 0 | High | M | SES* |

### Marketing automation
| # | Feature | One-liner | Reuses | Tbl | Val | Eff | Deps |
|---|---|---|---|---|---|---|---|
| 7 | **Rule builder console** | Owner-authored trigger→condition→action rules | P2+P5 | 1 | Must | L | — |
| 8 | Segment / tag manager | Behavioural tags → reusable audiences | P4 | 0 | High | S | — |
| 9 | Promo codes + attribution | Trackable discounts tied to revenue | P1+billing | 0 | High | S | — |
| 10 | Post-purchase upsell | PAYG user → membership offer | P2+billing | 0 | High | M | — |
| 11 | Birthday / anniversary triggers | Automated milestone touches | P2 | 0 | Nice | S | SES* |
| 12 | Klaviyo audience sync | Push P4 segments to Klaviyo | P4→Klaviyo lane | 0 | Nice | M | Klaviyo |

### Alerts & ops
| # | Feature | One-liner | Reuses | Tbl | Val | Eff | Deps |
|---|---|---|---|---|---|---|---|
| 13 | **Operator alert centre** | Severity-ranked inbox of what needs the owner now | P3+P5 | 0 | Must | S | — |
| 14 | Payment-failure / refund-spike alert | Fire when failures or refunds spike | P1+P2+P3 | 0 | Must | S | — |
| 15 | Utilisation / low-fill alert | Court sitting empty vs baseline | P1+P2+P3 | 0 | High | S | — |
| 16 | Coach-idle / no-hours alert | Bookable coach with no upcoming work | P1+P3 | 0 | High | S | — |
| 17 | Arrears-threshold alert | Coach settlement owed crosses a limit | P1+P3 | 0 | High | S | — |
| 18 | **Operator daily digest** | One AM message: revenue, fill, no-shows, failed pays, at-risk | P1+P2 | 0 | High | S | SES* |

### Analytics & BI
| # | Feature | One-liner | Reuses | Tbl | Val | Eff | Deps |
|---|---|---|---|---|---|---|---|
| 19 | **Court-utilisation heatmap** | Occupancy by hour × day × court; find dead slots | P1+P5 | 0 | Must | M | — |
| 20 | Executive KPI board | Revenue, active, utilisation, NPS tiles, compare-period | P1+P5 | 0 | Must | M | — |
| 21 | Customer LTV & spend distribution | Value segments to target campaigns | P1 | 0 | High | M | — |
| 22 | Benchmark ribbons | Metric vs world-class band (utilisation >70%, NPS >50) | P1 (static registry) | 0 | High | S | — |
| 23 | Date-range + compare-period everywhere | One control, all panels | P1+P5 | 0 | High | S | — |
| 24 | CSV / print export of any panel | | P1+P5 | 0 | Nice | S | — |

### Surveys & reputation
| # | Feature | One-liner | Reuses | Tbl | Val | Eff | Deps |
|---|---|---|---|---|---|---|---|
| 25 | **Post-lesson CSAT / star rating** | Auto survey after a session | P4 | 0 | High | S | — |
| 26 | NPS-gated review routing | Promoters → Google CTA; detractors → private manager alert | P2+P4+nps | 0 | High | M | SES* |
| 27 | Cancellation-reason capture | Why they cancelled → drop-off insight | P4 | 0 | High | S | — |
| 28 | Membership-exit survey | Triggered on cancel | P4+P2 | 0 | High | S | — |
| 29 | NPS trend + verbatim wall | Score over time + comments | P1+P5 | 0 | High | S | — |

### Website / funnel performance
| # | Feature | One-liner | Reuses | Tbl | Val | Eff | Deps |
|---|---|---|---|---|---|---|---|
| 30 | **Acquisition funnel** | visit → signup → first booking → member, per source | P1(funnel) | 0 | Must | M | beacon (live) |
| 31 | Traffic-source ROI | UTM → paying customer | P1(breakdown) | 0 | High | M | — |
| 32 | Landing-page & drop-off report | Where visitors leave | P1 | 0 | High | S | — |
| 33 | Traffic-anomaly alert | Auto-flag unusual dips/spikes | P1+P2+P3 | 0 | Nice | S | — |

### Per-line-of-business performance
| # | Feature | One-liner | Reuses | Tbl | Val | Eff | Deps |
|---|---|---|---|---|---|---|---|
| 34 | **Line-of-business scorecard** | Court vs coaching vs classes vs membership vs packs: revenue, volume | P1(breakdown dim=lob)+P5 | 0 | Must | M | — |
| 35 | Coach P&L leaderboard | Fill-rate, revenue, retention, net-of-commission per coach | P1(commission) | 0 | High | M | — |
| 36 | Class fill-rate & waitlist demand | Which classes over/under-subscribe | P1(diary) | 0 | High | S | — |
| 37 | Off-peak vs peak yield | Revenue-per-available-court-hour by shift | P1 | 0 | Nice | S | — |

### Financial ops
| # | Feature | One-liner | Reuses | Tbl | Val | Eff | Deps |
|---|---|---|---|---|---|---|---|
| 38 | **Dunning automation** | Unpaid statement → reminder → escalate | P2+statement | 0 | High | M | SES* |
| 39 | Cashflow / settlement forecast tile | What's due in/out | P1(billing) | 0 | High | S | — |
| 40 | Refund & write-off monitor | Track leakage | P1+P3 | 0 | High | S | — |

*\*SES-dependent features **function in-app today** and light up email the moment `SES_SENDER` is keyed —
no code change (see §5).*

### Differentiators to lead the pack (compose the above)
- **"Ask your club" AI console** — natural-language questions over the owner's own data
  ("which coach has the worst Tuesday fill-rate?"), answer-first (P1 metric registry is the backend).
- **Proactive AI morning brief** — the daily digest (#18) that *recommends* ("Court 3 sat 40% empty —
  push an off-peak code to lapsed members?"), one tap to execute via a P2 rule.
- **Churn-to-action loop** — at-risk detection (#1) that auto-drafts a personalised win-back and routes
  it as a coach task, closing the detect→act gap competitors leave open.
- **Demand-based dynamic court pricing** with a live RevPACH yield metric (#37) — no racquet tool frames
  pricing this way. (Pricing writes are a later, separate concern — the *metric* lands first.)
- **NPS-gated reputation engine** (#26) with a detractor resolution SLA.

Racquet-specific benchmark context: churn-risk scoring, operator digests/anomaly alerts, and our
first-party website funnel are **weak or absent across competitors** — the places CourtFlow can lead.
Court-utilisation heatmaps, per-LOB + per-coach breakouts, lifecycle/win-back automation, dunning,
surveys, and no-show tracking are **table stakes** we must reach. (Sources: CourtReserve, Playtomic
Manager, PlayByPoint, Skedda, Mindbody, Glofox, Wodify, Gymdesk, Momence, Vagaro.)

---

## 4. Sequencing (waves)

**Wave 0 — primitives first (front-load the reuse).** Build P1 (insight read-layer + metric registry),
P5 (component kit), and the P3 alert façade — shipped *with* three showcase features (#20 KPI board,
#13 alert centre, #30 acquisition funnel) so they're proven. The investment is the primitives; after
this, most later features are "add a metric key + drop an `insightCard`."

**Wave 1 — automation spine.** Build P2 (rule engine + `automation.rule` + the `_emit` hook + the
free-tier scheduler) and P4 (feedback/tag capture). Ship #7 rule builder, #8 segments, #1 at-risk,
#25 CSAT. Everything downstream is now config.

**Wave 2 — compose the themes cheaply.** #19 heatmap, #34 LOB scorecard, #3 cohort grid, #5 lifecycle
board, #26 review funnel, #38 dunning, #22 benchmarks, #18 digest. Each is days, not weeks.

**Wave 3 — polish & external.** #12 Klaviyo sync, #24 export, #33/#40 anomaly & leakage monitors, and
the AI console/brief differentiators.

---

## 5. Cross-cutting: free-tier & dark config

- **Time-based work** reuses the existing free pattern: a **free GitHub Action** (like `keep-warm.yml`)
  → a guarded `/api/cron/<job>` handler (`OPS_KEY`) that scans `usage_event`/`billing`/`diary` and calls
  `emit()`/`notifications.deliver()`. Per-user state (e.g. a win-back flag) can instead compute **lazily
  on next load**, mirroring `release_expired_holds`. **No paid Render cron is introduced.** At go-live
  (Starter plan) the four `render.yaml` crons can be uncommented.
- **Graceful degradation is already the house style.** Every P2/P4 action **always** writes the in-app
  `core.notification`; email is the best-effort second leg (`email_status='skipped'` while dark). So
  win-back, dunning, review-asks, welcome journeys, digests and operator alerts all **work in-app now**
  and light up email the instant `SES_SENDER` is set — no code change. Website panels already accrue
  from the live beacon. Klaviyo-dark only silences *marketing* sends; S3-dark affects nothing here.

---

## 6. Anti-patterns explicitly avoided

- **Table sprawl.** Phase 2 adds exactly **one** table (`automation.rule`). Surveys, tags, alerts,
  rule-runs, lifecycle stages are all `usage_event` rows (`survey_response`, `tag_applied`,
  `automation_fired`) or `notification` rows — new behaviour = a new `event_type` string /
  `notification.kind`, zero DDL, inheriting existing indexes (`ix_usage_event_type_time`,
  `ix_usage_event_club_time`).
- **Per-feature bespoke pipelines.** One ingestion path (`emit()`), one automation hook on it.
  "Operator alerts", "marketing triggers", "review requests" are not three systems — three *rule
  configs* over the same P2 engine feeding the same P3 inbox.
- **Duplicated aggregation.** All counting lives in P1's metric registry. A retention number, a
  dashboard tile, an alert threshold, and a benchmark ribbon call the *same* metric key — impossible to
  have "revenue" computed three ways. Centralises the guarded "missing table → empty, never 500"
  discipline.
- **Frontend divergence.** No page hand-rolls ECharts; every chart is a P5 `chartPanel` bound to a P1
  endpoint (styling, empty-states, range-pickers, cold-start timeouts fixed once).
- **Cron dependency (free-tier trap).** Scheduled rules use the GitHub-Action → `/api/cron/*` pattern or
  lazy on-read; no paid cron.

---

## 7. Reuse map (concrete pointers)

| A Phase-2 feature needs… | Reuse this primitive | File (path:symbol) |
|---|---|---|
| Emit a new domain/automation event | `emit()` — accepts any name, non-fatal | `marketing_crm/tracking/client.py:37` |
| Durable store for any signal (survey, funnel step, tag) | `core.usage_event` + `record_usage()` (`metadata` JSONB) | `core/repositories/usage_events.py`; `core/models.py:193` |
| Subscribe the automation engine to all events | New consumer in the `_emit` fan-out (like notifications) | `marketing_crm/tracking/client.py:96–118` |
| Send an operator/admin alert (in-app now, email later) | `notifications.deliver()` / `KIND_MAP` + `core.notification` | `marketing_crm/notifications.py:212,188` |
| Inbox read / bell / mark-read | `list_notifications`/`unread_count`/`mark_read` | `core/repositories/notifications.py`; `me/routes.py` |
| Transactional email (club-branded, .ics) | `ses.send_raw_email` / `_club_identity` (self-gating) | `marketing_crm/email/ses.py`; `notifications.py:273` |
| Marketing / lifecycle sends | `crm_sync.forward_event` (Klaviyo, opt-in gated) | `marketing_crm/tracking/client.py:114` |
| Cohort / retention / funnel / drop-off | Guarded functions beside `new_vs_returning` (read `usage_event`) | `analytics/repositories.py:62,290` |
| Visit/device/geo/UTM/time-on-site + custom funnel tags | Page beacon (`props` dict) | `marketing_crm/tracking/beacon.py`; `frontend/js/analytics.js` |
| NPS capture + scoring | `core.nps_response` + `record_nps` + `nps` panel | `core/repositories/usage_events.py`; `analytics/repositories.py:267` |
| Consent / opt-in state | `core.consent` + `iam.user.marketing_opt_in` | `core/models.py:233`; `iam/schema.py:109` |
| Per-club settings / flags | Add a column to `club.policy` (no new table) | `club/schema.py:79` |
| Attribution / UTM per user | `core.acquisition` | `core/models.py:105` |
| Time-based jobs (digests, win-back, anomaly) | free GitHub Action → guarded `/api/cron/<job>` + `OPS_KEY` | `crons/trigger.py`; `diary/crons.py`; `.github/workflows/keep-warm.yml` |
| Avoid a scheduler entirely (per-user state) | Lazy on-read pattern | `diary.bookings.release_expired_holds` |
| KPI tile / metric card | `CRMUI.stats` / `overview.js:card` / `admin_app.js:statLine` | `frontend/js/crm_ui.js:17`; `overview.js:29` |
| Chart panel | ECharts `lineChart` (or no-lib `CRMUI.bars`) | `frontend/js/overview.js:59`; `crm_ui.js:30` |
| Insight card in the admin SPA | `focusCard` + hash-router drill | `frontend/js/admin_app.js` |
| Config editor (summary-row → full-screen) | `AdminUI.*` editor pattern + lifecycle helpers | `frontend/js/admin_api.js`; `ui.js` |
| Chronological activity/audit feed | `CRMUI.activityFeed` | `frontend/js/crm_ui.js:228` |

---

## 8. One-line summary

Five primitives — **Read** (P1 insight layer) · **React** (P2 automation) · **Alert** (P3 inbox façade)
· **Capture** (P4 feedback/tags) · **Render** (P5 component kit) — turn ~40 world-class features into
*configuration* over one event bus, one inbox, and one aggregation layer, with a single new table.
Build the primitives first; then each theme the owner asked for is days of composition, not weeks of
plumbing.

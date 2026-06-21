# 09 — Build Plan & Multi-Agent Orchestration

> Tomo: *"build it with as many agents as possible in one session … managed seamlessly."* Here's how
> to parallelise safely. The win condition: most workstreams are independent because they sit behind
> clean interfaces (the schema, the gateway protocol, the event contract).

## 1. Phasing (dependency order)

```
Phase 0  Foundation        repo, render.yaml, DB connect, schema bootstrap, Clerk auth port, club resolution
Phase 1  Tenancy + seed    club/iam/* schemas, permissions, seed NextPoint (club #1), template club
Phase 2  Diary engine      diary.* schema + exclusion constraint, availability, booking CRUD, classes, recurrence, waitlist
Phase 3  Settlement (no gateway)  billing.* schema, order/ledger, at_court/monthly/membership/free modes, apply_payment_event + manual provider
Phase 4  CRM + Klaviyo     core.* port, event emit, crm_sync, consent, transactional confirmations (+SES fallback)
Phase 5  Frontends         portal SPAs (member booking wizard, coach diary, club-admin console), marketing site
Phase 6  SEO migration     url inventory, 301 map, cutover (separate, careful, reversible)
Phase 7  Yoco online pay   ✅ DONE — yoco_billing adapter, online mode, checkout UI, config probe, refunds
                            (refund-only / refund-and-cancel), reconciliation, printable receipts. LIVE.
Phase 8  Hardening         RLS, tests on the edge cases (doc 03 §10), analytics cockpit, polish
```

Phase 0→1 are sequential (everything depends on them). After Phase 1, **2/3/4 can run in parallel**;
Phase 5 consumes their APIs; 6/7 are independent tracks.

## 2. Workstream → agent lanes (run in parallel)

Borrow 1050's **lane discipline** (it already uses a pre‑commit hook + path ownership to stop agents
colliding). Assign each agent a path lane; agents touch only their lane.

| Agent | Lane (owns these paths) | Builds |
|---|---|---|
| **A — Foundation/Platform** | `app.py`, `wsgi.py`, `render.yaml`, `db.py`, `iam/`, `auth/` | Repo skeleton, boot/schema runner, Clerk auth port, club resolution, permissions, seed/provision scripts. **Runs first**, others depend on it. |
| **B — Diary engine** | `diary/` | Schema + exclusion constraint, availability, booking/lesson/class CRUD, recurrence, waitlist, crons. The biggest lane. |
| **C — Billing/Settlement** | `billing/`, `yoco_billing/`, `paypal_billing/` | `billing.*` schema, order/ledger, `apply_payment_event` + manual provider, gateway protocol, later Yoco/PayPal adapters. |
| **D — CRM/Klaviyo** | `core/`, `marketing_crm/` | Port `core.*` + tracking + crm_sync + consent; event emitters (hooks B & C call); Klaviyo templates; cockpit views. |
| **E — Frontend/Portal** | `frontend/` | Booking wizard, coach diary, club‑admin console, `/login`. Consumes B/C/D APIs (mock until ready). |
| **F — Marketing/SEO** | `frontend/marketing/`, `build_blog.py`, `migration/` | Host‑switched site, themed pages, blog, sitemap/robots/schema, URL inventory + 301 map. |

**Shared, change‑carefully:** `contracts/events.md`, the schema docs, `render.yaml` env list — treat as
interface files; coordinate edits (Agent A authoritative).

## 3. Interfaces that let lanes run independently

- **Schema** (`02`) is the contract between B, C, D — agree it first (Agent A writes the DDL stubs +
  `init()` runners on boot; B/C/D add their tables to their own module's `init()`).
- **Event contract** (`contracts/events.md`) decouples producers (B, C) from the consumer (D): B/C just
  call `emit(event, payload)`; D owns what happens next.
- **Gateway protocol** (`05` §2) decouples C's core from any provider; adapters are isolated.
- **API responses** let E build against documented shapes (stub/mock endpoints until live).

## 4. How to actually run many agents in one Claude Code session

- Use **git worktrees per agent lane** (or branch‑per‑lane) so parallel edits don't clobber; merge to
  `main` per phase. (`isolation: worktree` if launching sub‑agents.)
- Re‑use 1050's **pre‑commit lane hook** idea: block commits that touch files outside the agent's lane.
- **Sequence the gates:** Agent A lands Phase 0+1 and pushes; THEN fan out B/C/D/F in parallel; THEN E
  integrates. Don't fan out before the schema + boot runner exist.
- Keep each agent's task **closed‑form and verifiable** (clear "done when" + a smoke test).

## 5. Definition of done per phase (verification — don't skip)

- **Phase 0/1:** app boots; `init()` creates all schemas idempotently (run twice = no error); Clerk JWT
  resolves a principal with `club_id`+role; NextPoint seed present.
- **Phase 2:** the **edge cases in `03` §10** pass as automated asserts — esp. concurrent double‑booking
  → exactly one wins; reschedule conflict atomic; capacity/waitlist; cancellation policy.
- **Phase 3:** booking with each settlement mode produces correct order/ledger rows; `apply_payment_event`
  is idempotent (replay = no‑op); manual desk payment records `billing.payment`.
- **Phase 4:** `booking_confirmed` reliably triggers a Klaviyo confirmation (test profile) with SES
  fallback; marketing send blocked without opt‑in; no minor PII in payload.
- **Phase 5:** member can book a court/lesson/class end‑to‑end on mobile; coach sees their diary; admin
  runs the master diary.
- **Phase 6:** every old URL in the inventory 301s 1:1; sitemap submitted; no Coverage errors.
- **Phase 7:** Yoco checkout → webhook → booking confirmed; rollback flag hides online pay cleanly.
- **Phase 8:** RLS prevents cross‑club reads; cockpit numbers reconcile with source rows.

Recommend a dedicated **verification agent** (or the `Plan`/review agents) to run the Phase‑2 and
Phase‑3 edge‑case suite against a scratch DB before merge — booking integrity is where money/trust live.

## 6. Tech defaults (match 1050 so reuse is clean)

Python 3.12 + Flask + Gunicorn; psycopg; Postgres; idempotent boot DDL (no Alembic); vanilla JS SPAs
(no heavy framework) reusing 1050's CSS/chart conventions; Clerk JS on `/login`; Render blueprint
deploy. Add `btree_gist` + `pgcrypto` extensions. A calendar lib for the diary UI is the one place to
add a dependency (evaluate FullCalendar resource‑timeline).

## 7. First‑session realistic target

In one focused multi‑agent session you can land **Phases 0–3 + the diary API + a working member
booking wizard against NextPoint seed data** (book/edit/cancel/reschedule a court & lesson, enrol in a
class, with at‑court/monthly settlement and a confirmation event). That's a demoable platform. Yoco
online pay and the SEO cutover follow as their own focused sessions.

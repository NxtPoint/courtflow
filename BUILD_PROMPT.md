# HANDOVER — pick up work on NextPoint / CourtFlow

> **Paste Section 1 into a fresh Claude Code session at the repo root.** This file replaced the
> original build-kickoff prompt (2026-06-20) on 2026-08-02: the platform has been **live in
> production for over a month**, so a prompt that says "build it" now actively misleads. The build
> phase is over. What follows is how to work on a running system that handles real money.

---

## Section 1 — the prompt

```
You are working on "CourtFlow", a LIVE multi-tenant tennis club management platform.
NextPoint Tennis is club #1, in production at https://nextpointtennis.com with ~1,070 people,
real bookings and real money moving through Yoco daily.

READ FIRST, IN THIS ORDER:
  1. ./CLAUDE.md            — the operating guide. The "Gotchas" section is the accumulated
                              scar tissue of every money bug we have found. Read it properly;
                              most of it was written after something broke in production.
  2. ./docs/specs/README.md — the authoritative current-state index, newest entry first.
  3. ./docs/specs/OUTSTANDING.md — what is actually open right now.

THERE IS NO BUILD PHASE AND NO PYTEST. The gates are:
  1. python -m py_compile $(git ls-files '*.py')
  2. python -m db   TWICE   — the second run must be a clean no-op
  3. python -m scripts.test_all   — three rollback-only scenario harnesses
     Current green baseline: booking 404 / billing 551 / statement 64
  4. node --check on every frontend JS file you touched
  5. python -m scripts.audit_docs  — the DOCS gate. Prose never fails a compile, so docs rot
     silently and get trusted when wrong. It checks the real routes/tables/widgets/events/
     scenarios/scripts from SOURCE against the prose. Currently 0 misses; keep it there.
     RUN IT AT THE END OF ANY SESSION THAT ADDED A SURFACE.

HOW WE WORK — these are not style preferences, they are what has kept the money correct:

  * REPRODUCE BEFORE YOU FIX. Do not reason your way to a diagnosis from the code alone.
    Several of this project's worst bugs looked obvious and were wrong: a refund failure was
    blamed on the Yoco balance, then on the payment method, then on a duplicate checkout,
    before the actual cause (a frozen idempotency key) was found.

  * VERIFY BY RE-BREAKING. After a fix passes, deliberately re-introduce the bug and confirm
    the scenario goes RED. A guard that has never failed is a guard nobody has tested. This
    has caught weak tests repeatedly — most recently a custody test that passed for the wrong
    reason and guarded nothing at all.

  * A SILENT ZERO IS A BUG. "0" and "the read failed" are indistinguishable on screen, and on
    a money surface that is a false all-clear. Say the read failed.

  * NEVER `session.rollback()` INSIDE A COMPOSER. These readers run in the CALLER's
    session_scope. Use `session.begin_nested()` — a savepoint — or one failing block aborts the
    transaction and every later block silently returns zero. This exact bug has now been found
    THREE times (client360, admin_home, coach_settlement).

  * DATE-DEPENDENT ASSERTIONS DERIVE THEIR DATE FROM THE FIXTURE, never now(). The harness
    books days ahead; a now()-based month fails for the last few days of every month and reads
    as a regression.

  * PRODUCTION IS READ-ONLY TO YOU. You may look; Tomo clicks anything that writes. Never
    change DNS. Never touch the Ten-Fifty5 repo/DB at C:\dev\webhook-server except the one
    documented embed exception.

THE IRON RULE: every domain row is club_id-scoped. Never query domain data without it.

Ask Tomo what he wants to work on. If he asks for a review rather than a change, look at the
LIVE screens — every bug found on 2026-07-31 was invisible in the code and obvious on screen.
```

---

## Section 2 — where things stand (2026-08-02)

**Live and working.** Booking (court / lesson / class / semi-private), the three purchasing models,
Yoco payments + refunds, invoicing, the commission engine, month-end on the 25th, transactional
email via SES, the marketing site + blog, GA4/Ads with an offline-conversion loop, and the
Ten-Fifty5 match-analysis embed.

**The money model, in one paragraph.** One debt = one `billing.order`, settled once. Commission
accrues **on collection**, never on billing. `billing.coach_ledger` is signed (**+ = the club owes
the coach, − = the coach owes the club**) and its direction follows **who holds the cash** — the club
can only receive **Yoco and EFT**; anything else on a coaching order is money the coach took
directly. That one rule (`billing.commission.cash_custody_for`) is read by the ledger, the coach
statement and Club earnings, so the three cannot disagree. Read
`docs/specs/01-commission-and-coaching-decisions.md` §D6–D8 before touching any of it.

**What bit us most often**, in rough order: guarded reads that swallow an error and return zero;
two surfaces computing the same number two different ways; and money labels that are broader than
they sound ("collected", "banked", "paid to the club" have each been wrong at least once).

---

## Section 3 — the lanes

Touch only your lane; coordinate on `contracts/events.md`, the schema docs and `render.yaml`
(Foundation owns those). Full map + ownership: `CLAUDE.md` → "Lanes / module ownership map".

| Lane | Owns |
|---|---|
| Foundation | `app.py`, `db.py`, `render.yaml`, `auth/`, `iam/`, `club/`, `core/`, `scripts/`, `crons/` |
| Diary | `diary/` — bookings, classes, availability, the GiST no-double-book constraint |
| Billing | `billing/`, `yoco_billing/` — orders, commission, invoicing, refunds |
| CRM | `core/`, `marketing_crm/`, `offline_conversions/` |
| Client 360 | `client360/` — the ONE cross-lane client read model |
| Admin | `admin/`, `services/`, `insights/` |
| Coach / Client | `coach/`, `me/` |
| Frontend | `frontend/` — three role SPAs on ONE widget layer |
| Marketing/SEO | `frontend/marketing/`, `build_blog.py`, `migration/`, `marketing_digest/` |

**The frontend golden rule:** ONE widget per capability across all three SPAs. A second render of a
capability is a bug — extend the widget's config. Role differences are configuration, never forked
render code. Read `docs/specs/FRONTEND-STANDARDISATION.md` before any UI work.

---

## Section 4 — useful commands

```bash
# gates
python -m py_compile $(git ls-files '*.py')      # PowerShell: (git ls-files '*.py')
python -m db && python -m db                     # 2nd run must be a no-op
python -m scripts.test_all

# run it locally (needs DATABASE_URL = a LOCAL sandbox, never production)
gunicorn wsgi:app            # API
python web_wsgi.py           # web/portal, DB-less, PORT=5060

# read-only diagnostics, safe against production (full index: scripts/README.md)
python -m scripts.verify_live
python -m scripts.diagnose_coach_statement --coach <name> --detail
python -m scripts.diagnose_refund --client <name>
python -m scripts.audit_trials
python -m scripts.reconcile_coach_commission
```

Remediation scripts are **dry-run by default**; `--commit` writes, they append corrections rather
than rewriting history, and they are idempotent on a fixed `ref_id`.

---

## Section 5 — the original build prompt

Replaced 2026-08-02. It described building the platform from scratch with parallel lane agents in
git worktrees — three months stale, and it would send a new session down a path that no longer
exists. Its lane split survives as Section 3 above; its phasing lives in
`docs/09-build-plan-and-agents.md` if the history is ever needed, and `git log -- BUILD_PROMPT.md`
has the original verbatim.

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
  2. python -m scripts.check_frontend_js — the JS PARSE gate. node --check over frontend/js.
     A JS file that does not parse is dead in the browser ENTIRELY, and it presents as a broken
     login, not as a frontend error. Needs no DATABASE_URL, so it runs when nothing else can.
  3. python -m db   TWICE   — the second run must be a clean no-op
  4. python -m scripts.audit_docs  — the DOCS gate. Prose never fails a compile, so docs rot
     silently and get trusted when wrong. It checks the real routes/tables/widgets/events/
     scenarios/scripts from SOURCE against the prose. Currently 0 misses; keep it there.
     RUN IT AT THE END OF ANY SESSION THAT ADDED A SURFACE.
  5. python -m scripts.test_all   — the JS parse gate, then three rollback-only scenario harnesses
     Current green baseline: booking 521 / billing 702 / statement 64

  (Same numbering as CLAUDE.md's "Gates" section — they must not drift apart.)

  Gate 2 replaces the old step "node --check on every frontend JS file you touched". That
  instruction existed and was not enough, twice over: it was MANUAL, and it was scoped to the
  files you touched. On 2026-08-09 a broken admin_app.js shipped and took /admin down for 11
  hours. Automated, and over EVERY file — a gate you have to remember is not a gate.

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

  * TWO DATABASES, AND THEY ANSWER DIFFERENT QUESTIONS — read docs/specs/DATA-ACCESS.md.
    The LOCAL sandbox already exists (docker courtflow-dev on 55432; DATABASE_URL is already
    in the shell — check `env` before saying there is no DB) and all the gates run on it. But
    it holds almost no bookings or orders, so it proves CODE and answers nothing about the
    club's money. Live questions go through the courtflow-api RENDER SHELL, where DATABASE_URL
    never leaves Render: write a dry-run-default script into scripts/, PUSH it (the shell runs
    the DEPLOYED code), and Tomo runs it. DO NOT ASK FOR THE PRODUCTION DATABASE_URL — it has
    been declined, correctly, and asking for a script to be run works just as well.

THE IRON RULE: every domain row is club_id-scoped. Never query domain data without it.

YOUR FIRST JOB, unless Tomo says otherwise: THE PAGE-BY-PAGE FRONT-END WALKTHROUGH — see
Section 2b, which lists the screens still to cover and the five questions to ask on each.
Ten real bugs have come out of looking at live screens and EVERY ONE was invisible in the
code and passing its own tests. Do not go hunting the backlog for a code task instead.
```

---

## Section 2 — where things stand (2026-08-02)

**Live and working.** Booking (court / lesson / class / semi-private), the three purchasing models,
Yoco payments + refunds, invoicing, the commission engine, month-end on the 1st, transactional
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

## Section 2b — PICK UP HERE (as at 2026-08-10)

> ### ▶ THE NEXT SESSION'S JOB IS THE PAGE-BY-PAGE ADMIN WALKTHROUGH. START THERE.
>
> Tomo asked for this explicitly at the 2026-08-10 close-out. Do not open the backlog looking for
> something else to do; this IS the work. Everything else in this section is a distant second.

**Why this, and not a code task.** Ten real bugs have now come out of *looking at live screens*, and
**every single one was invisible in the code and passing its own tests**: `try/except: return 0`
silently reporting zeros (three separate times), a reconcile banner crying wolf, a headline calling
coach-held money "banked" when one coach held R15,950, a 60-min R0.00 price beside a R600 one billing
nothing, a settlement breakdown that didn't add up to the total above it, and a Record-payout modal
that never sent the month it settled — so an owner paid a coach R9,607 against a July figure that
then still read R9,607. Reading the code finds none of these. Opening the page finds them in minutes.

### How to run it

1. **Ask Tomo to drive**, or ask for screenshots — he has the live console open and this is his club's
   real money. Go one screen at a time and *wait* for the screenshot before theorising.
2. **On every screen ask the same five questions:**
   - Does the top **reconcile to the bottom** — do the parts sum to the total?
   - Is any figure **zero that shouldn't be**? A silent zero is a bug (see GOTCHAS "Reads that lie"),
     and a guarded read returns the empty default rather than an error, so it never announces itself.
   - Does every **label say exactly what the number is**? "Collected", "banked", "paid" and "due" have
     each been wrong at least once, and a broader-than-true label is indistinguishable from a
     correct one until someone acts on it.
   - Is one figure the **MONTH** and another **ALL TIME**, side by side, unlabelled? That trap has
     cost a five-figure misread already.
   - Does every **button send what the screen is showing**? The payout modal is the cautionary tale —
     the engine's rule was right and pinned by scenarios for weeks, but no caller exercised it.
     **A rule the engine honours but no caller exercises is not implemented.**
3. **Open the browser console on each screen.** A JS file that doesn't parse is dead in its ENTIRETY
   and presents as a broken login, not as an error.
4. **Fix forward, and guard it** — a scenario per bug, asserting the WRITE PATH where a screen writes.

### Coverage so far — the remaining list IS the task

- **Reviewed 2026-07-31:** Home · Refund requests · Coach statement · Club earnings · People ·
  Setup (menu, club profile, memberships, services).
- **Reviewed 2026-08-08/10:** Club earnings / the coach P&L (merged into ONE card) · the transaction
  record (Un-receipt added) · the client account (now month-by-month) · the Record-payout modal.
- **NOT REVIEWED — start here:** **Diary** · **Overview** · the rest of **Setup** · the **entire coach
  app** · the rest of the **client app** · **mobile widths**.

Use **People → a client → "View as member"** (`/app.html?as=<user_id>`, read-only) to see a member's
real screens without a second account — built 2026-08-09 exactly for this review.

### Then, in order

2. **Root-cause `admin_home`'s failing block.** A query in `admin_home` was aborting the transaction
   so every later block returned zero (People counts read 0/0/0; the refund check errored, which is
   the only reason it was noticed). Each block is now savepointed **and logs its own name**, so the
   cause is named in the Render logs — `grep "admin_home:"`. **The symptom is fixed; the cause is
   not.**

3. **Audit the 51 members showing as on TRIAL** (People → Trial). The trial gives free courts:
   `python -m scripts.audit_trials` (read-only; `--cancel-flagged` reverts wrong grants to PAYG).

4. **Confirm the PEAK PRICES exist.** The peak WINDOW is live (Mon–Thu 17:00–19:00) but peak only
   charges more where a `peak_amount_minor` per duration is set on the court service. Window without
   amounts is inert.

5. **Bring the MONTH ROWS into the admin People record.** The member's own account now reads month by
   month; People → a client still shows the flat invoice list. Two renderings of one capability is
   what the golden rule forbids.

6. **~1,000 Wix imports render as raw email addresses** in People (no first/last name), so the roster
   sorts and reads by email. Cosmetic, but it makes the list hard to use.

**Config Tomo has already done** (do not re-raise): membership caps 1 booking / 90 minutes · all three
payment methods enabled · peak window set · equipment payment options · company + bank details ·
`OPS_KEY` so month-end fires on the 1st · the R0/duplicate-duration price row (and the code guard now
refuses it on **both** the create and edit paths).

**Deliberate, do not "fix"** (2026-08-10): **JP, Tshepo and Wonder pay nothing** — no rent, nothing
owed to the club. An unset `rent_minor` on those three is the decision, not an oversight. The one
open question is *which* free they are, because it decides **who bills their clients**: `commission`
+ no rule means the club bills and keeps 0%; `rent` + R0 means the coach bills their own clients and
their self-booked lessons raise no club charge. Getting that backwards is how four rent coaches
accumulated **R68,000 of phantom "outstanding"**. Read all three with
`python -m scripts.set_coach_billing_model` (no args, read-only).

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
python -m scripts.check_frontend_js              # node --check over frontend/js — no DB, ~1s
python -m db && python -m db                     # 2nd run must be a no-op
python -m scripts.test_all

# run it locally (needs DATABASE_URL = a LOCAL sandbox, never production)
gunicorn wsgi:app            # API
python web_wsgi.py           # web/portal, DB-less, PORT=5060

# read-only diagnostics — RUN THESE IN THE courtflow-api RENDER SHELL, not locally: the local
# sandbox has almost no transactional data, so it answers these with a confident, irrelevant zero.
# (full index: scripts/README.md · the why: docs/specs/DATA-ACCESS.md)
python -m scripts.month_position 2026-07 --chase --dupes   # where a MONTH actually stands
python -m scripts.set_coach_billing_model                  # every coach: commission vs rent
python -m scripts.tag_coach_payout --coach "<name>"        # which month each payout settles
python -m scripts.reconcile_coach_commission               # should read CLEAN
python -m scripts.diagnose_coach_statement --coach <name> --detail
python -m scripts.audit_trials
python -m scripts.audit_zero_prices
python -m scripts.verify_live
```

Remediation scripts are **dry-run by default**; `--commit` writes, they append corrections rather
than rewriting history, and they are idempotent on a fixed `ref_id`.

**A new script must be committed and PUSHED before it exists in production** — the Render shell runs
the deployed code, and forgetting this reads as "the script isn't there".

---

## Section 5 — the original build prompt

Replaced 2026-08-02. It described building the platform from scratch with parallel lane agents in
git worktrees — three months stale, and it would send a new session down a path that no longer
exists. Its lane split survives as Section 3 above; its phasing lives in
`docs/09-build-plan-and-agents.md` if the history is ever needed, and `git log -- BUILD_PROMPT.md`
has the original verbatim.

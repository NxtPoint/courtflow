# DATA ACCESS — the sandbox, live data, and how to ask a question of production

**Read this before writing anything that touches real money.** Two databases exist and they answer
different questions. Confusing them is how an agent either (a) reports confidently on data that
isn't there, or (b) asks for a production credential it must never hold.

| | **Local sandbox** | **Production** |
|---|---|---|
| Where | docker `courtflow-dev`, `localhost:55432`, db `courtflow_dev` | Render Postgres, Frankfurt |
| `DATABASE_URL` | already in the shell env on this machine | **never leaves Render** |
| An agent may connect | yes, freely | **no — not ever** |
| Answers | "does the CODE hold?" | "what does the CLUB actually owe?" |
| Writes | fine; harnesses roll back anyway | only via a reviewed, dry-run-first script |

---

## 1. The local sandbox — proves code, not money

It is already running (`docker ps` → `courtflow-dev`, up for weeks) and `DATABASE_URL` is already
exported in this machine's shell. **Check `env` before saying there is no database** — that mistake
has been made, and it costs a session's worth of "I can't verify this locally".

All four gates run against it:

```
python -m py_compile (git ls-files '*.py')     # PowerShell form of the bash $(…)
python -m scripts.check_frontend_js            # needs no DATABASE_URL at all
python -m db ; python -m db                    # second run must be a clean no-op
python -m scripts.audit_docs --strict
python -m scripts.test_all
```

**What it contains, and the trap.** ~887 users (the Wix import shape) but only a handful of bookings
and orders — the transactional tables are effectively empty. So it will happily run every scenario
and tell you the code is right, while being useless for "is Allon owed R9,607?". A read against it
returns a real, well-formed, *irrelevant* answer. Every money question in this repo's history was
settled against production, never here.

**Do not delete this container.** It is the only thing that makes the gates runnable, and rebuilding
it means re-importing before anything can be verified.

---

## 2. Live data — the Render shell, and why it is shaped that way

Production is reached **only** through the **`courtflow-api` service → Shell** tab in the Render
dashboard. `DATABASE_URL` is already in that environment, so the connection string is never pasted
into a chat, copied to a file, or stored anywhere. That property is the whole design: it is why the
owner can hand an agent full read access to the club's money without handing over a credential.

**An agent must not ask for the production `DATABASE_URL`.** It has been declined, correctly, and
the answer will not change. Ask for a script to be *run* instead — which works just as well, and is
how every live question in this repo has been answered.

### The loop that works

1. **Write the script into `scripts/`** — dry-run by default, read-only unless it genuinely cannot be.
2. **Commit and push.** Render auto-deploys `master`. The shell runs the *deployed* code, so an
   uncommitted script does not exist as far as production is concerned — this is the step that is
   forgotten.
3. **Tomo opens the `courtflow-api` Shell** and runs it from `~/project/src`:
   `python -m scripts.<name> --help`
4. **Read the output**, then decide. For a write: the dry run prints a before/after and rolls back;
   only then re-run with `--commit`.

### What a script that runs there must do

Every rule below was bought with a bug in this repo:

- **Dry-run by default; `--commit` to write.** Never the other way round.
- **Resolve the subject FIRST, then take THEIR club.** `reconcile_coach_cash` picked "the first club
  by `created_at`" — a coin flip in a two-club database, in a script that reports on money.
- **Scope every `UPDATE` by club (and coach/user) as well as id.** The id was read off a console;
  a mistyped uuid must hit nothing rather than somebody else's record.
- **Print the before and the after of anything it changes**, in money, not minor units.
- **Keep output ASCII.** The Render shell is UTF-8, but the operator may re-run the same script from
  a Windows console, where a `·` arrives as `?` and reads like corruption in a financial report.
- **Never print a credential**, including in an error path. `verify_live.py` is the reference: it
  reads a URL and never echoes it.
- **Be idempotent**, or say loudly that it is not.

`scripts/README.md` indexes every one of these with a when-to-run note. Read it before writing a new
one — the audit you need often already exists.

### The other live-read path

`python -m scripts.verify_live` runs from **Tomo's own machine** and reads `DATABASE_URL` from a
gitignored `.env.local` (covered by `.env.*`), read-only, never printing the value. This is Tomo's
tool, not an agent's: an agent has no `.env.local` and must not create one.

---

## 3. Refreshing the sandbox from live — the honest position

**There is no automated restore, by choice.** A local copy of production would mean the production
credential (or a dump of every member's PII) sitting on a workstation, which is exactly what the
Render-shell arrangement avoids.

If a true local copy is ever wanted, **Tomo takes the dump himself** via Render's own backup tooling
and loads it into `courtflow-dev`. An agent does not do this and does not need the URL to ask for it.
Until then, the split stands: **code is proven in the sandbox, money is confirmed on the shell.**

If you find yourself wanting live data locally, the question is almost always answerable by a
read-only script run through §2 — which is faster, leaves an auditable artefact in `scripts/`, and
does not move a single row of PII.

---

## 4. Manual UI testing

Separate concern, separate doc: **[TESTING.md](TESTING.md)** covers the three-profile end-to-end plan
against the live site. The owner can also open any member's own screens read-only — People → a client
→ **View as member** (see [PERMISSIONS.md](PERMISSIONS.md)) — which answers "what does my member
actually see?" without a second account.

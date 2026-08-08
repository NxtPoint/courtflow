# scripts/ — what each is, and whether it's still live

Categorised in the 2026-07-12 close-out (refreshed 2026-07-26). Nothing here is dead code — but several are
**spent one-offs** (their job is done for club #1) kept for provenance + future-tenant reuse. Run any with
`python -m scripts.<name>`.

## Operational playbook — when a query comes in (post-launch, month-end running)
All read-only unless noted; all take `DATABASE_URL` from the Render shell env (or a gitignored `.env.local`).

| A member/coach says… | Run | Then |
|---|---|---|
| "I got a 'pay online' email but no invoice/PDF" | `resend_invoice.py <email>` | re-sends their real invoice (no new number) |
| "I paid but my booking/class shows unpaid" | `diagnose_bookings.py` | look at the S1 section; a stranded class seat → `settle_stranded_class_seats.py --settle` |
| "I paid online but nothing happened" (missed webhook) | `POST /api/cron/reconcile-payments {"hours": 1200}` | recovers + activates; idempotent |
| "before the 25th — who gets billed?" | `preview_month_end.py` | shows the invoice list + money it will skip and why |
| "a class shows the wrong name / no price in the Diary" | `reconcile_class_names.py` | `--commit` / `--link-orphans` if it flags a fix (shouldn't recur — DB trigger) |
| "is coach X being paid correctly?" | `reconcile_coach_commission.py [YYYY-MM]` | should read CLEAN; lists any paid coaching with no split |
| "why isn't coach X's pack on his earnings?" | `diagnose_coach_packs.py <name> [YYYY-MM]` | shows where each pack lands |
| general prod sanity check (safe, read-only) | `verify_live.py` | |

If month-end itself needs a re-run, just re-trigger `.github/workflows/month-end.yml` from the Actions tab —
it's idempotent per `(club,user,period)`, so it skips everyone already invoiced and picks up the rest.

## Gates (run before every merge — KEEP)
- `test_all.py` — runs the three scenario harnesses below. **The merge gate.**
- `test_booking_scenarios.py` · `test_billing_scenarios.py` · `test_statement_reconciliation.py`
  — rollback-only scratch-DB harnesses (**booking 404 / billing 571 / statement 64**).

## Load-bearing at runtime (KEEP — do not touch)
- `seed_nextpoint.py` — re-seeds club #1 on every prod boot (`SEED_NEXTPOINT=1`, imported by `app.py`). Idempotent.
- `provision_club.py` — provisions a new tenant (imported by `seed_nextpoint`).

## Ongoing tools / diagnostics (KEEP — re-runnable)
- `verify_live.py` — read-only check against the real Render Postgres (uses gitignored `.env.local`).
- `diagnose_bookings.py` - READ-ONLY production diagnostic: confirmed-looking-but-unpaid bookings and
  class seats, expired holds never released, orphaned unpaid orders, settlement modes that a service
  does not offer (separating retroactive config changes from real candidates), and a double-charge
  check. Every read is `_guard`-wrapped, so a wrong column reports `[skipped]` rather than failing -
  check the SQL against the schema when a panel looks suspiciously clean.
- `repermission_campaign.py` - the one-off re-permission send for the non-consented members
  (pairs with the token-guarded `/subscribe` page).
- `reconcile_class_names.py` - find + heal class types whose diary name drifted from their
  service (renamed in the service editor before the durable-link fix). Dry-run reports every
  class type's link status; `--commit` applies the SAFE fixes (sync a drifted name, pin an
  unambiguous unlinked resource). Ambiguous cases (linked to a terminated product with a live
  alternative) are reported for a human, never guessed.
- `resend_invoice.py <email>` - re-send a client's EXISTING statement invoice email (PDF + pay-link)
  when they got the bare month-end reminder instead. Looks up the invoice already covering their open
  debt and re-delivers it SYNCHRONOUSLY (no daemon thread) - no new number, nothing billed twice.
- `preview_month_end.py` — READ-ONLY dry run of the month-end sweep: who gets invoiced on the
  25th and for how much, PLUS the money it will skip and why (abandoned checkouts, debt hidden
  behind a live 'Pay all' wrapper, unattributed orders). Run it before every billing day — a
  bare R0 from a hand-written query can't tell "all settled" from "looking at the wrong club".
- `month_position.py` — READ-ONLY: **where a MONTH actually stands** — billed / discount /
  written-off / collected / still-outstanding, who owes it (`--chase` adds contacts), the payments
  that never completed (abandoned checkouts + missed webhooks, flagging any order with >1 checkout),
  how much of the outstanding debt has never been invoiced at all, and the spread of ALL open debt
  by delivery month. Exists because `month_end_targets`/`open_order_ids` filter on `status='open'`
  with **no date bound**, so every built-in view mixes the months and "what is still outstanding for
  July" cannot be answered. Buckets by the month the SERVICE WAS DELIVERED (the rule
  `activity_summary` already follows), resolved through the SAME joins as
  `invoicing._enriched_line_descriptions` — never a second resolver. `--dupes` groups the repeat-`Buy` clusters (`create_bundle_order`/`create_membership_order` INSERT
  unconditionally, so every tap leaves another `awaiting_payment` order) and splits PHANTOM debt from
  the genuinely LOST SALE — an online pack is granted on payment, so an unpaid cluster is a member who
  tried to buy and couldn't, not a debtor. Clean up with `void_orphaned_orders`. `python -m
  scripts.month_position [YYYY-MM] [--chase] [--dupes]`, default = last month. Run it on the Render Shell.
- `settle_stranded_class_seats.py` — remediates class seats stuck in `awaiting_payment`
  forever (seat taken, class played, order never settled). `--settle` turns each into a
  normal owed debt so month-end invoices it; `--void` cancels it. Dry-run by default.
- `test_ses.py` — manual SES send test.
- `audit_trials.py` — audits/cleans the 7-day trial grants.
- `diagnose_coach_packs.py` — READ-ONLY: where each session PACK lands in the coach-earnings roll-up (its selling coach vs the CLUB, sale month, order status, whether it counts). Answers "why isn't coach X's pack showing on his earnings?" Optional args: `<name-needle> [YYYY-MM]`. Uses `DATABASE_URL` from env (Render Shell) or `.env.local`.
- `reconcile_coach_commission.py` — READ-ONLY financial-integrity proof: every PAID lesson/class line (money collected via Yoco / cash / EFT / invoice / 'pay-all' statement) must carry a coach commission_split. Lists any paid coaching with NO split (a coach under-paid) + a covered/uncovered rand tie-out + paid-but-no-coach lines. Should read **CLEAN**. Optional arg `YYYY-MM`. Run monthly before coach payouts.
- `audit_client_data.py` — read-only Client-360 data scorecard.
- `fix_bypassed_packs.py` — remediate the reconcile / pack-bypass billing bugs: (A) activate PENDING pack wallets on paid orders (the reconcile gap) + (B) unwind duplicate OWED lesson orders (draw the pack token + void the owed order → client owes R0) + (C) activate stuck MEMBERSHIPS (paid but subscription left at its 'expired' pending-placeholder — member paid but wasn't covered). **Dry-run by default**; `--commit` to write; `--club`/`--user` to scope. Idempotent (never touches cancelled/lapsed subs). Behind the fixes in commits a244e19+; run once over affected clients, then it's spent.
- `diagnose_refund.py` — **READ-ONLY** (no boot DDL, pure SELECTs): why is a Yoco refund failing? "Insufficient funds" has two indistinguishable causes — (a) the refund aimed at the WRONG checkout (an order the member abandoned once and paid on the retry has several `ch_` ids; fixed 2026-07-28) or (b) the club's **Yoco balance** genuinely can't fund it (refunds draw on the balance, not the bank — no code change helps; refund by EFT and record it). One checkout on the order → (b); several → it was (a). No args lists every order with >1 checkout; `<order_id>` gives the full picture; `--recent N` lists recent online orders. Reads `DATABASE_URL` from a gitignored `.env.local` (never printed).
- `fix_inverted_coach_ledger.py` — one-off remediation for the INVERTED off-platform arrears entry (fixed forward 2026-07-28). `mark_arrears_collected` used to post `+coach_net` — the entry for money the CLUB took — when the coach had in fact collected it themselves, so each such lesson moved the club↔coach balance by the FULL GROSS in the wrong direction ("Coach payouts due" told the owner to pay a coach who owed them). Finds every `commission_earning` whose split carries `basis='arrears_commission'` and appends ONE correcting `adjustment` per coach — it does **not** rewrite history, so the audit trail keeps both. **Dry-run by default**; `--commit` to write; `--club` to scope. Idempotent (fixed `ref_id`). Run once, then it's spent.
- **`fix_desk_cash_coach_ledger.py`** — the SAME correction for coaching settled in CASH or
- **`diagnose_coach_statement.py`** — READ-ONLY: explain a coach's statement figures line by
- **`audit_docs.py`** — READ-ONLY: audit the DOCS against the CODE. Prose does not fail a gate,
  so documentation rots invisibly and is then trusted precisely when it is wrong. Extracts the real
  routes / tables / widgets / emitted events / scenarios / scripts from source and reports what the
  docs have not caught up with, plus broken internal links and disagreeing gate baselines.
  `--verbose` lists every miss; `--strict` exits 1 (usable as a pre-merge gate). Run it at the end
  of any session that added a surface.
  line when a number is disputed. Totals the month FOUR independent ways (what the club banked
  off `billing.payment` · the commission splits by basis+provider · the settlement the statement
  shows · what `coach_ledger` accumulated) plus the sessions delivered, so a disagreement points
  at WHICH view is wrong. `--coach <name>` `--month YYYY-MM` `--detail`.
  card-at-desk. The club can only receive Yoco and EFT, so that money was the coach's, but
  `record_split_for_order` booked it as club-held. Dry-run by default; `--commit` appends one
  correcting adjustment per coach, idempotent.
- `klaviyo_reactivation.py` — sync the dormant opted-in cohort to Klaviyo (dry-run default; **dark until `KLAVIYO_API_KEY`**). A recurring win-back tool — schedule it if/when Klaviyo goes live.

## Spent one-offs (job done for club #1 — kept for provenance / future tenants)
- `klaviyo_trial_cohort.py` — ONE-TIME trial-cohort backfill to Klaviyo (for members trialed before the emit shipped). Only re-run for a NEW cohort/tenant.
- `void_orphaned_orders.py` - TWO passes. (1) unpaid orders whose bookings are ALL cancelled and
  which took no money; the root cause is fixed (`release_expired_holds` voids on expiry), so pass 1
  should stay empty. (2) **abandoned checkouts with NO booking behind them** - memberships, packs and
  class seats, which pass 1 could never see because it joins `order_line.booking_id`; that is where
  most of the value sits and nothing else ever cleans it. Four guards: no live booking, no live
  enrolment, no succeeded payment, older than `--min-age-days` (default 7). Dry-run by default.
  **Run reconcile with a wide `hours` FIRST** so Yoco - not our DB - has confirmed they were never
  paid; voiding a genuinely-paid order would hide real money instead of tidying noise.
- `klaviyo_membership_backfill.py` — ONE-TIME: sets `on_trial=false` + fires `membership_started` for members who converted BEFORE that emit was fixed (2026-07-22). **Run this before sending the Unconverted-trial segment anything** — until it does, that segment still contains paying members. Dry-run by default; `--commit` to push. See `docs/specs/KLAVIYO-MASTER-PLAN.md` §7f.
- `backfill_pack_products.py` — ONE-TIME map of legacy NULL-product packs → their service. Spent for club #1; reusable for a migrated tenant.
- `backfill_person_links.py` — ONE-TIME `iam.user ↔ core.person` backfill (911/911 done). Forward-linking now lives in the app path (`link_person_for_user`).
- **Wix→Render cutover bundle** (supervised, `--dry-run` default; runbook `migration/CUTOVER_RUNBOOK.md`) — `import_wix.py` (core importer) + the three wrappers `import_members.py`, `import_subscriptions.py`, `import_lessons.py`. **Interdependent — treat as a unit.** Spent for NextPoint; the only Wix-migration path for a future club.

> Cleanup note: none of the spent one-offs are imported by any running code path or CI, so they're
> harmless where they are; they're documented rather than deleted because they're cited across `docs/specs/`
> and are the reusable migration path for the next tenant.

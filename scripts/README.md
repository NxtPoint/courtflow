# scripts/ — what each is, and whether it's still live

Categorised in the 2026-07-12 close-out (refreshed 2026-07-26). Nothing here is dead code — but several are
**spent one-offs** (their job is done for club #1) kept for provenance + future-tenant reuse. Run any with
`python -m scripts.<name>`.

> **Where these RUN, and the rules for writing a new one:
> [`docs/specs/DATA-ACCESS.md`](../docs/specs/DATA-ACCESS.md).** Short version: the local sandbox
> proves code and holds almost no transactional data; anything about real money runs in the
> **`courtflow-api` Render shell**, which executes the **deployed** code — so a new script must be
> **committed and pushed** before it exists in production. Dry-run by default, `--commit` to write.
> Never ask for the production `DATABASE_URL`; ask for the script to be run.

## Operational playbook — when a query comes in (post-launch, month-end running)
All read-only unless noted; all take `DATABASE_URL` from the Render shell env (or a gitignored `.env.local`).

| A member/coach says… | Run | Then |
|---|---|---|
| "I got a 'pay online' email but no invoice/PDF" | `resend_invoice.py <email>` | re-sends their real invoice (no new number) |
| "I paid but my booking/class shows unpaid" | `diagnose_bookings.py` | look at the S1 section; a stranded class seat → `settle_stranded_class_seats.py --settle` |
| "I paid online but nothing happened" (missed webhook) | `POST /api/cron/reconcile-payments {"hours": 1200}` | recovers + activates; idempotent |
| "before the 1st — who gets billed?" | `preview_month_end.py` | shows the invoice list + money it will skip and why |
| "a class shows the wrong name / no price in the Diary" | `reconcile_class_names.py` | `--commit` / `--link-orphans` if it flags a fix (shouldn't recur — DB trigger) |
| "is coach X being paid correctly?" | `reconcile_coach_commission.py [YYYY-MM]` | should read CLEAN; lists any paid coaching with no split |
| "why isn't coach X's pack on his earnings?" | `diagnose_coach_packs.py <name> [YYYY-MM]` | shows where each pack lands |
| general prod sanity check (safe, read-only) | `verify_live.py` | |

If month-end itself needs a re-run, just re-trigger `.github/workflows/month-end.yml` from the Actions tab —
it's idempotent per `(club,user,period)`, so it skips everyone already invoiced and picks up the rest.

## Gates (run before every merge — KEEP)
- `test_all.py` — runs the JS parse gate + the three scenario harnesses below. **The merge gate.**
- `check_frontend_js.py` — `node --check` over every `frontend/js/**/*.js`. No DB, no env, ~1s, so
  it runs FIRST. Catches a file that cannot load in the browser AT ALL. Added after 640b2b8 shipped
  real newlines inside a JS string: `admin_app.js` stopped parsing entirely and `/admin` hung on
  "Loading…" for 11 hours, reading as "cannot log in". Fails CLOSED if `node` is missing.
- `test_booking_scenarios.py` · `test_billing_scenarios.py` · `test_statement_reconciliation.py`
  — rollback-only scratch-DB harnesses (**booking 690 / billing 721 / statement 64**).

## Load-bearing at runtime (KEEP — do not touch)
- `seed_nextpoint.py` — re-seeds club #1 on every prod boot (`SEED_NEXTPOINT=1`, imported by `app.py`). Idempotent.
- `provision_club.py` — provisions a new tenant (imported by `seed_nextpoint`).

## Ongoing tools / diagnostics (KEEP — re-runnable)
- `verify_live.py` — read-only check against the real Render Postgres (uses gitignored `.env.local`).
- `verify_dns.py` - DNS-only, no DB, no env: checks a live zone against the target zone file in
  `migration/dns/<domain>.zone`, record for record, and exits 1 on any mismatch. Run it TWICE per
  domain during the Wix decommission - once against Cloudflare's assigned nameservers BEFORE the
  flip (`--ns kate.ns.cloudflare.com`), while Wix is still authoritative and a miss is free, and
  once against public resolvers after. MX, SPF, DMARC and every Clerk/SES DKIM record are marked
  critical and fail loudly, because those are the ones whose loss is silent: mail keeps "working"
  until someone notices it isn't. Shells out to `nslookup` on purpose - a migration tool that needs
  `pip install` first is one that doesn't get run at the moment it's needed.
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
- `community_status.py` - READ-ONLY: why is Find a Game not showing? Checks the community boot DDL
  actually ran, prints the per-club flags (community_enabled / seat_rule_enforced / share / timings)
  and what content exists. Run in the courtflow-api Render shell (docs/specs/DATA-ACCESS.md).
- `resend_invoice.py <email>` - re-send a client's EXISTING statement invoice email (PDF + pay-link)
  when they got the bare month-end reminder instead. Looks up the invoice already covering their open
  debt and re-delivers it SYNCHRONOUSLY (no daemon thread) - no new number, nothing billed twice.
- `preview_month_end.py` — READ-ONLY dry run of the month-end sweep: who gets invoiced on the
  1st and for how much, PLUS the money it will skip and why (abandoned checkouts, debt hidden
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
- `verify_promotion.py <code>` — READ-ONLY: prints a promotion exactly as configured and flags the combinations that are INERT (not active, window not open or already shut, bonus_qty blank so "get a month free" grants nothing, a bonus_period scoped at packs instead of memberships, per-customer cap of 0, max redemptions already reached). A promo is written once in a form and then quoted to hundreds of people in an email; every one of those fields is a way for the campaign to fail at checkout with the customer's card already out. Run it before marketing any code. Matches the code the way the engine does — case-insensitive, stripped.
- `backfill_klaviyo_subscribers.py` — DRY-RUN-DEFAULT: subscribes the people who ALREADY consented to marketing but were never pushed to Klaviyo as subscribers. Consent and subscription are different things and only one was wired: `subscribe_member()` fires solely from the consent endpoint, so anyone whose opt-in came from the Wix import (or the signup default) is opted in HERE and invisible THERE — 455 opted in vs ~40 reachable on 2026-08-23. `sync_all()` does not fix it (upserts profiles, never subscribes, ignores consent). Audience is exactly marketing_opt_in = true; it cannot grant consent. ⚠ switch the target list to SINGLE opt-in first or everyone just gets a confirmation email. `--commit` + typed YES to write.
- `audit_marketing_reach.py` — READ-ONLY: how many people we HAVE vs how many we may legally EMAIL, and whether the 7-day trials ever converted. A headcount is not an audience — on live data 1270 members yielded ~506 mailable, and of 386 trials only 5 (1.3%) ever bought a membership while 93.5% did nothing at all after the trial ended, which turns an "upsell" campaign into a reactivation one. **Every count that claims mailability reads `core.app_user.marketing_opt_in` — the gate `crm_sync` actually consults before it will send — NOT `iam.user`, which is only what the admin screen shows.** The two are different numbers (82 apart on 2026-08-25, then 34 the other way after the consent reconcile), so sizing an audience off the screen is a read that lies in whichever direction it currently leans. Both flags are still printed, because the gap between them is the diagnostic, and section B splits any disagreement by `core.consent` so you can see whether anyone is being mailed without a record that they agreed. Also prints three ready-made campaign segments and a per-month "did new signups arrive marketable" rate. No --commit; every statement is a SELECT, so it is safe on the Render Shell. `--club <name>` when several exist.
- `reconcile_marketing_optin.py` — DRY-RUN-DEFAULT: makes the ADMIN SCREEN tell the truth about consent. `marketing_opt_in` lives on both `iam.user` (what the screen shows) and `core.app_user` (the gate crm_sync reads), and the re-permission page writes only the second — so on 2026-08-25 the live split was 459 vs 506 and **82 people were being mailed while the screen showed them opted out**. `audit_marketing_reach` settled which side was right (`app-only: granted 111 · withdrawn 0 · NONE 0` — every one of them has a dated, granted `core.consent` row), so this moves `iam.user` to match: ON where consent is GRANTED, OFF where it was WITHDRAWN. It writes `iam.user` ONLY and adds nobody to the mailable set — the opposite direction (35 people, `NONE 34`) is Wix-era import data with no consent record and belongs in the re-permission campaign, not a backfill. Idempotent. `--commit` + typed YES to write.
- `audit_peak_and_trial.py` — READ-ONLY: the PEAK hours and MEMBERSHIP rules actually in force, per court and per tier — i.e. what the resolver decides, not what the admin form appears to say. Both of these fail SILENTLY when mis-set (a window that never matches just charges base price for ever; an unflagged trial tier is simply never granted), so "it looks right on the screen" is not evidence. Prints each court's effective windows and their source (own rows / club / legacy column / never-peak), every tier's free hours + caps + whether peak is free, and which tier is the signup trial. `--club <name|id>` only when several exist. Run it on the Render Shell after changing peak or the trial.
- `diagnose_coach_packs.py` — READ-ONLY: where each session PACK lands in the coach-earnings roll-up (its selling coach vs the CLUB, sale month, order status, whether it counts). Answers "why isn't coach X's pack showing on his earnings?" Optional args: `<name-needle> [YYYY-MM]`. Uses `DATABASE_URL` from env (Render Shell) or `.env.local`.
- `reconcile_coach_commission.py` — READ-ONLY financial-integrity proof: every PAID lesson/class line (money collected via Yoco / cash / EFT / invoice / 'pay-all' statement) must carry a coach commission_split. Lists any paid coaching with NO split (a coach under-paid) + a covered/uncovered rand tie-out + paid-but-no-coach lines. Should read **CLEAN**. Optional arg `YYYY-MM`. Run monthly before coach payouts.
- `set_coach_billing_model.py` — show or set how the club monetises a coach: `commission` (default)
  or **`rent`** (they pay rent and invoice their own clients, so a lesson they book against
  themselves raises NO club charge). No args lists every coach and their model; `--who <email|name>
  --model rent [--rent-minor N] --commit` sets one. Dry-run by default. **Deliberately explicit
  rather than inferred from a 0% commission rate:** `commission.resolve_rate` returns 0 when NO rule
  exists, so "0%" and "not configured yet" are the same value — inferring would silently stop
  billing every unconfigured coach's clients.
  **SUPERSEDED for day-to-day use (2026-08-09):** the model is now a field on the **coach editor**
  (Setup → Coaches → a coach), which is where an owner looks. Keep the script as the read-only
  audit — no args lists EVERY coach and their model in one table, which the per-coach editor cannot.
- `void_client_charges.py` — **cancel every still-owed charge for ONE client, in one action.**
  Dry-run by default; `--commit` writes. `--who <email|name>` (ambiguity is refused, never guessed),
  `--period YYYY-MM` scopes to a delivery month, `--reason` is recorded on `void_reason`,
  `--write-off` records forgiven debt instead of never-owed. Exists because voiding an INVOICE
  cancels the DOCUMENT, not the debt — the cleanup is per-charge, and one live account carried 73.
  Loops `void_order` rather than issuing a bulk UPDATE, so each charge still kills any live 'Pay
  all' wrapper and drops its coach_arrears. A PAID order is never touched (that is the refund
  path). Deliberately a script and not an admin button: "wipe this client's balance" is one
  mis-click from erasing a real member's real debt.
- `reverse_payment.py` — **undo a DESK payment recorded in error** — the money never arrived, so the
  debt comes back. NOT a refund (that is money going BACK to the client; recording one as the other
  puts a refund the club never issued into its books). Marks the payment `reversed` (kept, never
  deleted), returns the order to `open`, REVERSES the coach's commission — he was credited the moment
  it was recorded — and un-settles any 'Pay all' wrapper it paid. Refuses a Yoco/PayPal charge by
  name (real money moved: refund it) and any payment that granted a pack or membership (revoking a
  wallet someone may have drawn from is a person's call). `--order <id> [--reason] [--commit]`,
  dry-run by default.
  **SUPERSEDED for day-to-day use (2026-08-09):** the same action is now the **Un-receipt** button on
  the transaction record, beside Receipt and Refund, on all four mounts of `Widgets.TransactionDetail`
  — same repo call, same refusals. Keep the script for a bulk or scripted correction, and for the
  case where the record won't load.
- `reconcile_coach_cash.py` — READ-ONLY: **ties a coach's commission splits back to the CASH that
  produced them**, following `commission_split.payment_id` to the payment and the ORDER it was
  recorded against. Answers the "these two numbers should match and don't" question that
  `diagnose_coach_statement` raises: a split paid via a **'Pay all' wrapper** is real money whose
  payment row hangs on the wrapper — and a wrapper carries no coach, so a per-coach read of
  `billing.payment` cannot see it while the splits can. Classifies every split as paid-directly /
  via-a-wrapper / no-payment-row (an off-platform arrears collection) and prints anything left as
  UNEXPLAINED. `--coach <name|email> [--month YYYY-MM]`. Resolves the coach FIRST and uses THEIR
  club, never "the first club".
- `tag_coach_payout.py` — **says which MONTH a recorded payout settles.** A payout is credited to
  the month it is FOR (`billing.coach_payout.period_label`), not the day the cash moved; an
  UNLABELLED one falls back to `occurred_at`, so July's commission paid on 2 August credits AUGUST
  and July keeps showing the full amount still due — one click from paying a coach twice. The
  Record-payout modal now asks for the month and prefills the card on screen; this is for the
  payouts recorded before it did. Lists a coach's payouts flagging the unlabelled ones, then
  `--id <payout> --period YYYY-MM [--commit]`. **Dry-run by default**; changes no amount and posts
  no ledger entry, so `--period ""` puts it back exactly. Resolves the coach FIRST and uses THEIR
  club, and scopes the UPDATE by club+coach as well as id.
- `audit_zero_prices.py` — READ-ONLY: **prices that silently bill NOTHING.** `pricing.price_for`
  resolves the exact duration then tie-breaks on `amount_minor ASC`, so two active rows for one
  product+duration make the CHEAPER one authoritative, in silence — production ran a coach on
  `60min R0.00` next to `60min R600.00` and billed R0 on R12,680 of coaching. Reports duplicate
  durations, R0 rows on a service that also sells at a real price, and the R0 ORDERS split by
  settlement_mode (token / membership_covered / free are R0 by design; `at_court` and
  `monthly_account` at R0 are work delivered for nothing). Nothing in the code is wrong here —
  the DATA is, which is why no gate catches it. Run it monthly with the commission reconcile.
- `audit_client_data.py` — read-only Client-360 data scorecard.
- `fix_unbilled_seats.py` — void seat charges raised while the club had **charging switched OFF**. `apply_seat_orders` used to leave the `seat_rule_enforced` check to its callers and 3 of 4 forgot, so `join_game` / `set_visibility` / invite-accept billed a share in clubs running only the community half (found live 2026-08-11: "Seats unpaid R110.00" with the switch off). Touches ONLY unpaid seat orders in clubs currently not charging — never a booking's own court order, never a settled one — voids through `void_order` so the statement stays reconciled, and leaves every player their seat. **Dry-run by default**; `--commit` to write. Idempotent. Run once after the fix ships, then it's spent.
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

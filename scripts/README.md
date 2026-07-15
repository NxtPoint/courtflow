# scripts/ — what each is, and whether it's still live

Categorised in the 2026-07-12 close-out. Nothing here is dead code — but several are **spent one-offs**
(their job is done for club #1) kept for provenance + future-tenant reuse. Run any with `python -m scripts.<name>`.

## Gates (run before every merge — KEEP)
- `test_all.py` — runs the three scenario harnesses below. **The merge gate.**
- `test_booking_scenarios.py` · `test_billing_scenarios.py` · `test_statement_reconciliation.py`
  — rollback-only scratch-DB harnesses (**booking 180 / billing 281 / statement 47**).

## Load-bearing at runtime (KEEP — do not touch)
- `seed_nextpoint.py` — re-seeds club #1 on every prod boot (`SEED_NEXTPOINT=1`, imported by `app.py`). Idempotent.
- `provision_club.py` — provisions a new tenant (imported by `seed_nextpoint`).

## Ongoing tools / diagnostics (KEEP — re-runnable)
- `verify_live.py` — read-only check against the real Render Postgres (uses gitignored `.env.local`).
- `test_ses.py` — manual SES send test.
- `audit_trials.py` — audits/cleans the 7-day trial grants.
- `audit_class_packs.py` — reports class-pack vs session price (read-only).
- `audit_client_data.py` — read-only Client-360 data scorecard.
- `cleanup_coachless_classes.py` — soft-retire legacy coachless classes (dry-run by default, reversible).
- `fix_bypassed_packs.py` — remediate the "paid pack bypassed" bug: (A) activate PENDING wallets on paid orders (the reconcile gap) + (B) unwind duplicate OWED lesson orders (draw the pack token + void the owed order → client owes R0). **Dry-run by default**; `--commit` to write; `--club`/`--user` to scope. Idempotent. Behind the fix in commit a244e19; run once over affected clients, then it's spent.
- `klaviyo_reactivation.py` — sync the dormant opted-in cohort to Klaviyo (dry-run default; **dark until `KLAVIYO_API_KEY`**). A recurring win-back tool — schedule it if/when Klaviyo goes live.

## Spent one-offs (job done for club #1 — kept for provenance / future tenants)
- `klaviyo_trial_cohort.py` — ONE-TIME trial-cohort backfill to Klaviyo (for members trialed before the emit shipped). Only re-run for a NEW cohort/tenant.
- `backfill_pack_products.py` — ONE-TIME map of legacy NULL-product packs → their service. Spent for club #1; reusable for a migrated tenant.
- `backfill_person_links.py` — ONE-TIME `iam.user ↔ core.person` backfill (911/911 done). Forward-linking now lives in the app path (`link_person_for_user`).
- **Wix→Render cutover bundle** (supervised, `--dry-run` default; runbook `migration/CUTOVER_RUNBOOK.md`) — `import_wix.py` (core importer) + the three wrappers `import_members.py`, `import_subscriptions.py`, `import_lessons.py`. **Interdependent — treat as a unit.** Spent for NextPoint; the only Wix-migration path for a future club.

> Cleanup note: none of the spent one-offs are imported by any running code path or CI, so they're
> harmless where they are; they're documented rather than deleted because they're cited across `docs/specs/`
> and are the reusable migration path for the next tenant.

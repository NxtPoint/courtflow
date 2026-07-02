# Go-live cutover — running progress log

Durable record of the Wix→Render cutover work (survives chat compaction). Full runbook:
`docs/specs/GO-LIVE-CUTOVER.md`. Target go-live: **Sunday**. Newest entries at top.

## ✅ DONE (agent, committed + pushed to master)
- **`a50fc90` — Wix importer** (`scripts/import_wix.py`): dry-run by default (rolls back);
  `--commit` writes. clients→`iam.user`+member membership; members→`membership_subscription`
  (exact Wix expiry) +optional lesson `token_wallet`; plans→`bundle_plan`. Idempotent, dedup
  by email, validates-not-aborts, prints plan→tier map for sign-off. Dry-run verified on sandbox.
- **`d96d439` — 301 engine wired** (`web_app.py` + `migration/redirects.py`/`.csv`): marketing-host
  only, before routing; chains flattened (each old path → single hop); loader chain-safe. 17/17
  verified single-301. Starter map = placeholders; FINAL map curated Thu from url_inventory.csv.
- **`2efaabe` — GA4/Ads/GSC hooks** (`web_app._inject_head`, `pay_return.js`, `render.yaml`):
  env-gated, DARK by default. gtag injects when IDs set; `window.cfConversion` (safe no-op) fires
  `purchase` on paid. GSC via HTML-file or meta token. Verified dark + configured states.
- **Audit — Fable v3 public site** (`a43b3f5`, parallel agent): PASS, no regressions. All pages keep
  canonical + chrome markers; NAP JSON-LD intact on home/coaches/programs/contact (pricing/careers
  never had it — optional enhancement, not a break).

## ⏳ TOMO TO SUPPLY (supervised — never an agent)
- **Wix CSV exports** → `migration/wix/{clients,members,plans}.csv` (columns in runbook §2c). Then
  agent re-runs import dry-run → you confirm ~10 member tiers → `--commit` to prod.
- **Real Wix plan names → tiers** (if not all "Standard") to fill `PLAN_NAME_TIER_MAP` in import_wix.py.
- **Prod Clerk instance** (pk_live, JWKS, issuer, email-claim JWT template) → swap Render env.
- **5 Google env values** (all blank in render.yaml now): `GA4_MEASUREMENT_ID`, `GOOGLE_ADS_ID`,
  `GOOGLE_ADS_CONVERSIONS` (JSON of Ads labels), `GSC_VERIFICATION_FILE` or `GSC_META_TOKEN`.
- **DNS**: pre-lower TTL to 300s (apex+www only — ⚠️ NEVER `api.nextpointtennis.com`); flip at cutover.
- **AWS locked out (2026-07-02):** Tomo bounced from AWS, reset ticket logged. NON-BLOCKING — SES + S3
  are optional (email stays in-app until sorted). Park SES; wire it post-reset (15 min, SES-SETUP.md).
- **Render**: attach custom domain, bump Free→Starter. **GSC/Ads/GA4 consoles**, pre-cutover client email.

## ⬜ AGENT — STILL TO DO (all BLOCKED on Tomo's weekend crawl/CSV)
- Curate FINAL `migration/redirects.csv` once url_inventory.csv exists.
- Decide (with Tomo) whether old booking/service Wix URLs should 301 to `/pricing` (indexable) instead
  of the noindex `/portal#/book/*`.
- Import: once CSVs land in `migration/wix/`, dry-run → confirm tiers → `--commit`.

## ✅ DONE (later)
- **`build_blog.py` rebuilt** + pushed — blog HTML regenerated so slugs carry over. Court count
  corrected to **7 hard + 1 clay = 8 total** (Tomo-confirmed 2026-07-02).

## ✅ FIXED
- **Seed court resurrection** — `seed_nextpoint.py` now defaults to 7 hard + 1 clay AND seeds courts
  only when the club has none (re-seed can never re-add a court the owner deleted). Verified no-op.

## ⚠️ Notes
- Multiple chats commit to `master` in parallel (this chat = cutover; others = coach/money/public-site).
  Git author is always "Tomo"; distinguish by commit message + timing.
- Rollback at any step = point DNS back to Wix (kept warm, low TTL).

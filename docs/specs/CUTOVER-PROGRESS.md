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
- **AWS locked out (2026-07-02):** Tomo bounced from AWS, reset ticket logged. **SES worked AROUND it**
  by reusing the ten-fifty5 SES account (see DONE below) — email is LIVE. S3 (coach photos) still parked
  until reset (optional; coaches paste a URL meanwhile).
- **Render**: attach custom domain, bump Free→Starter. **GSC/Ads/GA4 consoles**, pre-cutover client email.

## 📌 EMAIL FOLLOW-UPS (post-AWS-reset, non-blocking — email already works)
- **Deliverability / DKIM:** interim mail sends FROM `ten-fifty5.com`, so first sends may land in
  junk/Promotions until reputation warms. Cleaner fix: verify **`nextpointtennis.com`** in SES (Easy DKIM
  → 3 safe CNAMEs, never touches apex/`api.`) and set `SES_SENDER=no-reply@nextpointtennis.com` → DKIM
  aligns to NextPoint, inboxing improves. (Meanwhile: mark the confirmations "Not junk".)
- **Long-term:** move to CourtFlow's own `courtflow.app` SES once the AWS account is back — just repoint
  `SES_SENDER` + drop the `SES_AWS_*` overrides (SES-SETUP.md "proper CourtFlow setup").

## ⬜ AGENT — STILL TO DO (all BLOCKED on Tomo's weekend crawl/CSV)
- Curate FINAL `migration/redirects.csv` once url_inventory.csv exists.
- Decide (with Tomo) whether old booking/service Wix URLs should 301 to `/pricing` (indexable) instead
  of the noindex `/portal#/book/*`.
- Import: once CSVs land in `migration/wix/`, dry-run → confirm tiers → `--commit`.

## ✅ DONE (later)
- **`build_blog.py` rebuilt** + pushed — blog HTML regenerated so slugs carry over. Court count
  corrected to **7 hard + 1 clay = 8 total** (Tomo-confirmed 2026-07-02).
- **📧 SES TRANSACTIONAL EMAIL — LIVE end-to-end (2026-07-03)** via the interim (reuse the ten-fifty5
  SES account; CourtFlow's own AWS still locked). Self-test + a real pay-at-court booking confirmation
  both delivered (branded "NextPoint Tennis", Reply-To `info@nextpointtennis.com`, `.ics` attached →
  proves `SendRawEmail` works too). Config on **courtflow-api**: `SES_AWS_ACCESS_KEY_ID` /
  `SES_AWS_SECRET_ACCESS_KEY` = 1050's AWS keys · `SES_REGION=eu-north-1` (must match where the
  ten-fifty5 identity is verified — the blank-region + missing-keys were the two bugs we hit) ·
  `SES_SENDER=noreply@ten-fifty5.com`. Code: SES now takes its OWN creds (`SES_AWS_*`) so it rides a
  different AWS account from S3; `_sender()` also reads `SES_FROM_EMAIL`. Diagnostic:
  `POST /api/cron/ses-selftest?to=<email>` (OPS-guarded) reports live enabled/sender/region/creds +
  the raw send error.
  - **⚠️ `.ics` attachment is gated by `EMAIL_ICS_ENABLED` (default `0` = OFF).** Booking-confirmation
    emails currently send **without** the calendar attachment (plain HTML+text); the in-app "Add to
    calendar" still works. Flip `EMAIL_ICS_ENABLED=1` on **courtflow-api** to re-enable the MIME
    `SendRawEmail` attachment path once you're confident the interim key carries `ses:SendRawEmail`.

## ✅ FIXED
- **Seed court resurrection** — `seed_nextpoint.py` now defaults to 7 hard + 1 clay AND seeds courts
  only when the club has none (re-seed can never re-add a court the owner deleted). Verified no-op.

## ⚠️ Notes
- Multiple chats commit to `master` in parallel (this chat = cutover; others = coach/money/public-site).
  Git author is always "Tomo"; distinguish by commit message + timing.
- Rollback at any step = point DNS back to Wix (kept warm, low TTL).

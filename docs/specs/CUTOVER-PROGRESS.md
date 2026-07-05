# Go-live cutover — running progress log

Durable record of the Wix→Render cutover work (survives chat compaction). Full runbook:
`docs/specs/GO-LIVE-CUTOVER.md`. **Go-live: 2026-07-05 — ✅ DONE.** Newest entries at top.

## ✅ CUTOVER COMPLETE (2026-07-05)
Live at `https://nextpointtennis.com` (apex canonical, www→apex 301, HTTPS). DNS flipped at Wix (apex A→
`216.24.57.1`, www CNAME→`courtflow-web.onrender.com`; `api.` untouched). Prod Clerk live + **Google login**
via a custom Google OAuth client. **GA4** `G-EKQP47P8M9` + **Google Ads** `AW-17077631191` (purchase label
`84PdCKHjrMscENfxn88_`) on courtflow-web; `TRANSACTIONAL_BCC=info@nextpointtennis.com` on courtflow-api.
Data migrated (878/11/8). SES live. **OPS_KEY rotated (DONE).** The OPS diagnostics `/api/cron/reconcile` +
`/api/cron/yoco-diag` were **deleted** (the SES ops levers + `reconcile-payments` remain). Everything under
"⏳ TOMO TO SUPPLY" below is now complete — kept for history.

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

## 🩺 SES DIAGNOSIS (2026-07-04) — "send_ok but nothing arrives" was the SUPPRESSION TRAP
- Account is HEALTHY / out-of-sandbox / 0 complaints / far under quota — **delivery to FRESH addresses
  works** (verified: dejan.stojakovic1210@gmail.com arrived clean, no bounce). The failures were from
  re-testing 1-2 addresses that got SUPPRESSED early (a bounce or a spam-mark) — and **sending to a
  suppressed address logs a BOUNCE and silently drops it**. Real 900 clients = fresh addresses = fine.
- **Ops levers added** (OPS-guarded, API-only — no AWS console needed):
  - `POST /api/cron/ses-account` — account state (EnforcementStatus, sending/quota, bounce stats, suppression).
  - `POST /api/cron/ses-suppress?email=X&action=check|delete` — check / CLEAR a suppressed address.
  - `POST /api/cron/ses-selftest?to=X` — send a test + report the raw result.
- **⚠️ ROTATE `OPS_KEY`** on courtflow-api after launch (it was pasted in chat during diagnosis).

## 📌 EMAIL FOLLOW-UPS (post-AWS-reset, non-blocking — email already works)
- **Deliverability / DKIM:** interim mail sends FROM `ten-fifty5.com`, so first sends may land in
  junk/Promotions until reputation warms. Cleaner fix: verify **`nextpointtennis.com`** in SES (Easy DKIM
  → 3 safe CNAMEs, never touches apex/`api.`) and set `SES_SENDER=no-reply@nextpointtennis.com` → DKIM
  aligns to NextPoint, inboxing improves. (Meanwhile: mark the confirmations "Not junk".)
- **Long-term:** move to CourtFlow's own `courtflow.app` SES once the AWS account is back — just repoint
  `SES_SENDER` + drop the `SES_AWS_*` overrides (SES-SETUP.md "proper CourtFlow setup").

## ✅ AGENT — DONE
- **FINAL `migration/redirects.csv` curated (2026-07-04)** from the real GSC Performance->Pages (12mo).
  48 rules; caught the high-traffic misses (clay-court-booking 334 clicks, pricing-plans/list 44,
  category/all-products 7). Ranking/booking pages 301 to INDEXABLE pages (/pricing, /programs, /coaches)
  so Google transfers authority; verified 48/48 single-301 to a live 200 page, no chains.
- Data imports DONE (see above).

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

## ✅ DONE — DATA MIGRATION (prod, club bc67c6a1) — ALL THREE COMPLETE 2026-07-04
- **Clients: 878** (866 new + 12 updated) -> 879 active members, 0 trials from import (5 pre-existing
  test-signup trials, unrelated). **Memberships: 11 granted** + 2 extended (Yehuda's 3 rows -> 1),
  all 4 plans matched on `membership_tier` (Adult Anytime Play / Adult Off Peak / Family Plan /
  Junior & Student Membership). **Lessons: 8 coach wallets, 1845 min** (7 Allon + 1 Colbert; Simonne
  holds 2). Mpilonhle's "Monthly Adult - Squad" deliberately excluded (it's a class Tomo makes himself).
- **⚠️ EARLIER SNAG (fixed):** the first import runs silently hit a LOCAL dev DB (club 353ce796) because
  an ambient localhost `DATABASE_URL` overrode the prompt. Scripts now REFUSE a localhost target for a
  prod import (`_is_local_host`) -> re-ran cleanly against the real Render prod DB.
- **Follow-ups for Tomo:** Allon's "10x60 R8500" package should be **10x90** (Robert's pack); create the
  "Monthly Adult - Squad" class; ensure Colbert accepts his coach invite.
- Tools: `scripts/import_members.py` (clients) · `import_subscriptions.py` (memberships, `--list-plans`
  diagnostic) · `import_lessons.py` (lesson wallets) — all: secure DB prompt, dry-run, YES, verify, idempotent.

## ✅ DONE — DATA MIGRATION (superseded note)
- **878 Wix clients imported to PRODUCTION as active members (2026-07-04)** — via `scripts/import_members.py`
  run from the courtflow-api Render shell (DATABASE_URL already in-env, so no connection string handled/
  stored anywhere). Verified: **879 active members, 0 trial subscriptions** (import grants none; the member
  row also suppresses the first-login trial). Source = cleaned `clients.csv` (878 unique, deduped from 919
  Wix contacts; typo `gnail.com`→`gmail.com` fixed). Idempotent — re-run safe when memberships/lessons land.
  Tools: `scripts/import_members.py` (friendly wrapper: secure getpass prompt for the DB URL / dry-run →
  YES → commit → verify), on top of `scripts/import_wix.py`.
  - **STILL TO COME from Tomo (email-keyed files):** (1) **memberships** — the ~23 paid members from
    `wix_subscriptions.csv`; Tomo is creating the plans in the frontend with EXACT matching names so import↔
    service line up. (2) **lessons/packs** — remaining-lesson balances → `token_wallet`. Both import by email.

## ✅ FIXED
- **Seed court resurrection** — `seed_nextpoint.py` now defaults to 7 hard + 1 clay AND seeds courts
  only when the club has none (re-seed can never re-add a court the owner deleted). Verified no-op.

## ⚠️ Notes
- Multiple chats commit to `master` in parallel (this chat = cutover; others = coach/money/public-site).
  Git author is always "Tomo"; distinguish by commit message + timing.
- Rollback at any step = point DNS back to Wix (kept warm, low TTL).

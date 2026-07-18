# FEATURE-FLAGS — built but dark (the switch-on inventory)

Everything in the platform that is **fully built but currently OFF / unwired**, and exactly how to turn
it on. Nothing here is broken — each degrades gracefully (silent no-op / dark / fallback) until switched
on. This is the "we did the work, here's the light switch" list, produced in the 2026-07-12 close-out
sweep. When you activate one, tick it and move the detail into the relevant spec.

> **Rule of thumb:** public IDs (GA4/Ads) can be committed inline in `render.yaml`; secrets
> (`*_API_KEY`, `*_PASS`, AWS creds, `OPS_KEY`) are `sync:false` — set them in the Render dashboard.

---

## A. Env-gated — flip a flag (low-risk; all degrade gracefully)

| # | Feature | Gate (env) | Service | Value | Turn on |
|---|---------|-----------|---------|-------|---------|
| A1 | **Klaviyo** marketing sync + reactivation + trial-cohort flows | `KLAVIYO_API_KEY` (+ `KLAVIYO_MARKETING_LIST`, `KLAVIYO_REACTIVATION_LIST`) | `courtflow-api` | HIGH | Set the key. The event feed already emits; sync goes live. The two cohort scripts (`scripts/klaviyo_reactivation.py`, `scripts/klaviyo_trial_cohort.py`) are **manual** — schedule them if you want them recurring. |
| A2 | **GA4 + Google Ads + GSC** tags (pageviews + `start_free_week`/`booking` conversions) | `GA4_MEASUREMENT_ID`, `GOOGLE_ADS_ID`, `GOOGLE_ADS_CONVERSIONS`, `GSC_VERIFICATION_FILE`/`GSC_META_TOKEN` | `courtflow-web` | HIGH | Fill the IDs (public → inline OK). CTAs already call `window.cfConversion`. Account `AW-17077631191` exists — this is the wiring. **(Live as of 2026-07-11 per GOOGLE-ADS-PLAN.md — verify these are set.)** |
| A3 | **Google Ads offline-conversions CSV feed** (`/feeds/google-ads/offline-conversions.csv`) | `GOOGLE_ADS_FEED_USER` + `GOOGLE_ADS_FEED_PASS` | `courtflow-api` | HIGH | Set both (HTTP Basic). Recorder half is already live (a gclid'd buyer paying ledgers `core.offline_conversion`). Point a Google Ads scheduled upload at the URL; conversion action MUST stay named exactly `Offline purchase`. Returns 404 until set. |
| A4 | **Ten-Fifty5 members-area embed** — widen from test allowlist → all members | `TF5_EMBED_ALLOW_EMAILS` (clear it) | `courtflow-web` | HIGH | **Launch = clear the env** (empty → all members). Depends on the TF5-side env staying set (`AUTH_ISSUERS` on "Sport AI - API call"). Rollback = clear `TF5_EMBED_URL`. |
| A5 | **S3 coach-photo upload** (`/api/coach/photo-presign`) | `S3_BUCKET` + `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` (+ `S3_PUBLIC_BASE_URL`, `AWS_REGION`) | `courtflow-api` | MED | Set them → presigned direct-to-S3 upload. Until then coaches paste a photo URL. |
| A6 | **`.ics` attachment on transactional email** | `EMAIL_ICS_ENABLED=1` | `courtflow-api` | LOW | **Blocker cleared 2026-07-18** — the SES key now carries `AmazonSESFullAccess` (`ses:*`), so `ses:SendRawEmail`/attachments work; just set the flag to turn it on (still OFF by choice). The in-app "Add to calendar" (`/api/diary/bookings/<id>/calendar.ics`) already works regardless. |
| A7 | **HubSpot CRM adapter** (alternate to Klaviyo) | `HUBSPOT_PRIVATE_APP_TOKEN`/`HUBSPOT_API_KEY` | `courtflow-api` | LOW | A complete second CRM sync adapter, fully dark. Only if a club ever prefers HubSpot over Klaviyo. |
| A8 | **Per-club online payments** (new tenants) | `PAYMENTS_ENABLED=1` (global, ON) + `club.policy.allow_online_payment` (per club) | — | — | Club #1 is ON. **New tenants provision with the per-club flag OFF** → Admin → Settings → Payments turns it on (the upsert is INSERT-ONLY so a boot re-seed can't reset it). |

**Recently switched ON (graduated off this list):**
- **`EMAIL_INVOICE_PDF_ENABLED` — ✅ ON / LIVE (verified 2026-07-18).** Issued invoices now email with the PDF
  **attached** (previously the email only linked the in-portal PDF). Unblocked by the SES key gaining
  `AmazonSESFullAccess` (`ses:*`, i.e. `ses:SendRawEmail`) — the SAME unlock that clears A6.
- **`OPS_KEY` GitHub Actions secret — ✅ SET.** The month-end statement sweep now **fires live on the 25th**
  of each month (`.github/workflows/month-end.yml`, `POST /api/cron/month-end`); it previously no-op'd without
  the secret.

## B. Built but not wired to any UI (needs a small front-end or a scheduler)

- **B1 — CRM "cockpit" analytics lane** (`/api/admin/cockpit/*`, `marketing_crm/backoffice/blueprint.py`,
  registered in `app.py`). **No SPA calls it** (the admin "cockpit" UI actually hits `/api/admin/financials/*`
  — name collision). `signups`/`usage`/`consent`/`nps` are LIVE over `core.*`; `occupancy`/`revenue`/
  `coach-utilisation`/`attendance` are **501 stubs**. Overlaps the `insights/` lane — **reconcile before
  investing** (see D). *Switch-on:* a nav tab consuming the four live endpoints + finish the four stubs.
- **B2 — Standalone Business Overview `/overview.html`** (`analytics/`) — fully built, but the admin SPA now
  uses the `insights/` lane for its native Overview tab (the iframe was retired). `/overview.html` is reachable
  by **direct URL only** (+ `/admin-classic`). `GET /api/analytics/clubs` powers a **multi-club** platform-admin
  filter with no home in the single-club nav. *Switch-on:* add a platform-owner link, or fold multi-club into
  the SPA — relevant once there's >1 tenant.
- **B3 — Membership-refill cron** (`diary/crons.py::run_membership_refill`, `/api/cron/membership-refill`) —
  rolls membership periods / marks lapsed. Implemented, **not scheduled**. *Switch-on:* uncomment the
  `render.yaml` cron (with `CRON_API_BASE`+`OPS_KEY`) or add a keep-warm-style GitHub Action. Needed once
  **recurring** (non-manual) memberships go live.
- **B4 — Booking reminders cron** (`diary/crons.py::run_reminders`, T-24h/T-2h, deduped via
  `diary.reminder_log`, `/api/cron/reminders`) — implemented + idempotent, **not scheduled**, so no reminder
  emails go out. *Switch-on:* schedule `python -m crons.trigger reminders` hourly, or a workflow hitting the
  endpoint.
- **B5 — OPS diagnostic endpoints** (`OPS_KEY`-guarded, curl-only by design): `/api/cron/db-fingerprint`,
  `/api/cron/ses-suppress`, `/api/cron/ses-account`, `/api/cron/ses-selftest`. Keep headless; documented here
  rather than wired to UI.

## C. Commented-out / scaffolding

- **C1 — The four `render.yaml` cron services** (`reminders`, `capacity-sweep`, `monthly-invoice`,
  `membership-refill`) are commented out. **capacity-sweep is intentionally never needed** (abandoned holds
  self-release via lazy expiry in `compute_availability`/`create_booking` + the class equivalent). The other
  three are real work waiting on a scheduler; **month-end already rides `.github/workflows/month-end.yml`**
  instead of a Render cron.
- **C2 — 301 redirect engine** (`migration/redirects.py`) — a full, tested engine (`load_redirects`,
  chain-flattening, `register_redirects`) **deliberately not registered** (Wix→Render SEO cutover is
  Tomo-supervised; agents never touch DNS). *Switch-on (supervised):* fill `migration/url_inventory.csv` +
  `redirects.csv` from GSC/Ahrefs/Wix crawl, add `register_redirects(app)` to `web_app.py` **before** the
  catch-all, then cut DNS. This is the one item that materially protects SEO during cutover.

## D. Decisions / consolidation candidates (not dead, but review)

- **`GET /api/admin/bundle-plans`** — kept only for the offline "issue a pack" picker after the standalone
  pack UI + write routes were deleted (packs now live under a service). Verify it's still reached by that flow,
  else retire.
- **`analytics/` vs `insights/` vs CRM `cockpit` overlap** — three read-only aggregation lanes with partly
  duplicated concepts (revenue, occupancy/utilisation). The **CRM cockpit (B1) is the most orphaned** —
  consolidate before building more on it.
- **HubSpot adapter (A7)** — genuinely unused; keep only if HubSpot is a real future option.

## E. Inert until an owner sets a value (data-config — no env, no deploy)

Fully built, shipped and live, but they do nothing until the owner sets a data value in the console — the
defaults keep existing behaviour unchanged (same pattern as the equipment / peak / caps / trial controls in
[EQUIPMENT-AND-CONSTRAINTS.md](EQUIPMENT-AND-CONSTRAINTS.md)).

- **E1 — Semi-private (squad) lessons** — a lesson can seat >1 client with **per-head billing** (one owed
  order per client; a child's head bills the guardian), an add-a-player-later step, and cancel voids every
  order. **Dark until** the owner sets a lesson service's **Max clients > 1** (`billing.product.max_clients`,
  in the service editor). Default `1` = today's single-client lesson, unchanged.
- **E2 — Per-service payment restriction (card-only service)** — every service purchase enforces its OWN
  `billing.product.payment_modes`, so a card-only service refuses pay-at-court (a pack inherits its service's
  modes with no at-court fallback; class enrolment is gated the same way). **Dark until** the owner narrows
  that service's **payment modes** in the service editor; a service offering all modes behaves as before.

---

## Suggested switch-on order (highest owner value first)
1. **Google measurement** (A2 + A3) — verify GA4/Ads IDs set + turn on the offline-conversions feed creds.
2. **Klaviyo** (A1) — one key lights up sync + reactivation/trial cohorts (then schedule the two scripts).
3. **Launch the TF5 embed** (A4) — clear one env var.
4. **Schedule reminders + membership-refill** (B4, B3) — uncomment crons / add a workflow.
5. **Wire the 301 engine** (C2) at supervised SEO cutover.
6. **Surface or retire** the CRM cockpit lane (B1) and `/overview.html` (B2).

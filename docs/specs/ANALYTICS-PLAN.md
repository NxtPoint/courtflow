# Analytics & Reporting — full plan (build after sign-off)

**Status: BUILT (Phases A, B and C shipped 2026-07-18/19 - see sections 8 and 10).** A ground-up rethink of the admin **Overview / Analytics** dashboard so it answers the
questions the owner actually asks — *who's visiting, who's logging in, are members growing, are trials
converting, what's being booked, and is the court full* — every metric **per day**, blending our
**first-party data** with **Google (GA4 + Search Console)**. Supersedes the ad-hoc current tab.

The dashboard is driven by the `insights/` lane (`GET /api/insights/overview?month=`) + `court-utilisation`;
the old platform `/overview.html` (analytics lane) is retired. Numbers reconcile with the Money tab by
construction (same bases).

---

## 0. Two bugs to fix first (quick wins — bundle into Phase A)

1. **Court utilisation is on the wrong timezone.** `insights.court_utilisation` buckets by
   `EXTRACT(HOUR/ISODOW FROM b.starts_at)` — but `starts_at` is `timestamptz` and Postgres extracts in the
   **session TZ (UTC on Render)**, while `diary.availability_rule` uses **local wall-clock** times. So the
   heatmap is shifted ~2h AND booked-vs-available don't line up. **Fix:** extract in club time —
   `EXTRACT(HOUR FROM b.starts_at AT TIME ZONE 'Africa/Johannesburg')` (+ ISODOW the same). Use the club's
   configured timezone with an `Africa/Johannesburg` default. *(The view itself is great — just re-zoned.)*

2. **"Visits" looks 5× too big (~400/day vs ~80 unique).** The headline `visits` counts **every**
   `page_view`, including a signed-in member firing one on **every SPA route change**. That inflates the
   number and mixes public traffic with in-app navigation. The data to separate them already exists
   (`public_visits`, `logged_in_visits`, `vsplit`); the fix is **presentation**: lead with **public unique
   visitors**, and move in-app navigation into its own "Members-area activity" section (§2). Never headline
   the mixed `visits` count again.

---

## 1. What we already have (don't rebuild)

- **First-party beacon** → `core.usage_event` (`page_view`): carries `anon_id`, `path`, `referrer`,
  `utm_*`, `device`, and **`authed`** (`'true'` once Clerk resolves). `club_id` resolved server-side.
- **`insights.overview`** already returns per-day **series** + month **KPIs** for: visits, unique visitors,
  public/member/logged-in visits, bookings (total + **court/lesson/class** + member-covered), revenue
  (gross/net/refunds), new clients, **active members (total)**, NPS; plus breakdowns (sources, top pages,
  devices). → *A lot of what's wanted is computed but under-surfaced.*
- **Court utilisation** heatmap (weekday × hour) — keep (re-zone per bug #1).
- **Google, but only in email:** `marketing_digest/` pulls GA4 (7d) + Search Console (28d) via keyless WIF
  in GitHub Actions and emails it. **Not in the dashboard yet** — §7 brings it in.

**Gaps to build:** active members **by type** (tier / PAYG / trial), the **trial funnel** (start→convert→
fall-off), **logged-in new-vs-returning**, and **Google data in the dashboard**.

---

## 2. The redesigned dashboard — sections (every metric per day)

### A. Website & acquisition — PUBLIC visitors (the top of the funnel)
- **Unique visitors / day** (public only) — THE headline. (`public` distinct `anon_id`.)
- Public page views / day (secondary, context only).
- **New vs returning** visitors / day (first-ever `anon_id` in window = new).
- **Traffic sources / channels** — our UTM/referrer split now; **GA4 channels** (Organic / Paid / Direct /
  Social / Referral / Email) once §7 lands.
- Top landing pages · devices · **geography** (GA4 city/region — SA-focused).
- **Google Ads / GA4 conversions**: `start_free_week`, `booking`, `Offline purchase` (from GA4/Ads).

### B. Members-area activity — LOGGED-IN (separated from public)
- **Logged-in visitors / day** (distinct people, `authed='true'`).
- **New vs returning logged-in** — first-seen-authed in window = a newly-activated member.
- App engagement: sessions / member, most-used in-app pages.
> This is where the "~400" actually lived — in-app navigation. Surfacing it here (not as "website traffic")
> makes both numbers make sense.

### C. Membership growth — the core health metric  ⭐
- **Active members / day, STACKED by type**: each **paid tier** (Student / Family / …) + **PAYG**
  (active client, no membership) + **Trial**. A stacked area = the mix growing over time.
- **New members vs cancellations / day** → net growth line.
- **Membership mix** snapshot (donut): tier shares + PAYG + trial.
- Source: `billing.membership_subscription` (`provider='trial'` = trial; `price_id → price.membership_tier`
  = paid tier; active = period covers the day). PAYG = active `iam.membership`/client with no active paid sub.

### D. Trial funnel — start vs convert vs fall-off  ⭐
- **Trials started / day** vs **converted / day** (`membership_started` after a trial) vs **lapsed / day**
  (trial period ended, no purchase).
- **Rolling conversion rate** (converted ÷ started) + trials **currently live**.
- Optional cohort: of trials started in month M, % converted within 14/30 days.
- Source: `provider='trial'` subs (`period_start`/`current_period_end`) + `membership_started` events +
  `scripts.audit_trials` logic. Ties to the Klaviyo "unconverted trial" segment.

### E. Bookings & court usage
- **Bookings / day by type** (court / lesson / class), stacked — data exists, surface it properly.
- Membership-covered vs PAYG bookings / day.
- **Court utilisation heatmap** (re-zoned) + overall % + peak vs off-peak split.
- (Volume sanity: the query already collapses the lesson's auto-held court row — confirm the UI shows the
  collapsed count, not the raw two-row count, which is likely why "bookings feel wrong".)

### F. Revenue (reconciles with Money tab)
- Gross / net / refunds per day (same basis as `sales_by_day`).
- Revenue **by source** (membership / PAYG court / lessons / classes / packs) — from the `_earnings_cte`.

### G. Experience
- **NPS / day** + score (already wired) + **Google reviews** count/trend (from the feedback engine + GBP).

---

## 3. Google data in the dashboard (GA4 + Search Console)  — the architecture

**Constraint:** the org blocks downloadable SA keys, so the **live app can't call GA4 directly**; only the
GitHub Action can (keyless WIF). **Solution — push, don't pull:** extend `marketing_digest` (which already
has GA4/GSC access) to POST a **daily metrics snapshot** to a new OPS-guarded endpoint, into a new table the
dashboard reads. No Google credentials ever touch Render.

- **New table** `core.web_daily` (club_id, day, source['ga4'|'gsc'], metric, value) — or a wide row per day.
- **New cron endpoint** `POST /api/cron/analytics-ingest` (OPS-guarded, like `marketing-digest-email`):
  upserts the day's GA4 (sessions, engaged sessions, users, channels, conversions, top pages, geo) + GSC
  (clicks, impressions, CTR, avg position, top queries, striking-distance).
- **Digest change:** after building the report, also POST the structured metrics (a small addition; it
  already fetches them). Runs daily (05:00 UTC) — the dashboard shows "Google data as of <date>".
- **Dashboard reads** `core.web_daily` for the acquisition section (§A). First-party beacon stays the
  real-time signal; GA4 adds channels/geo/conversions; GSC adds the SEO funnel.

> This makes the dashboard the **one place** for both first-party and Google insight, and reuses the entire
> keyless-WIF investment. It also unlocks: GA4 sessions vs our beacon visits (a cross-check), and the
> striking-distance queries surfaced in-app (not just email).

---

## 4. Data model & readers (backend)

- Extend `insights.overview` series/KPIs with: `active_by_tier` (per-day map), `payg`, `trial` counts;
  `new_members` / `cancellations`; `trials_started` / `trials_converted` / `trials_lapsed`;
  `logged_in_new` / `logged_in_returning`.
- New reader `insights.membership_breakdown(club_id, month)` → per-day stacked tiers + PAYG + trial.
- New reader `insights.trial_funnel(club_id, month)` → started/converted/lapsed + rate.
- New reader `insights.web_metrics(club_id, days)` → reads `core.web_daily` (GA4/GSC).
- Everything `_guard`-wrapped (partial DB → empty panel, never a 500), `club_id`-scoped, month-bucketed to
  match the existing window. **Re-zone all hour/weekday extraction to club TZ.**

## 5. Frontend (admin `#/overview`)
- Reorganise into the 7 sections above (ECharts, the existing seam). Lead with the **public unique-visitor**
  headline + **membership-growth stacked area** as the two hero charts.
- Reuse the existing chart helpers; add a stacked-area + donut. Keep it mobile-responsive (`cf-*`).
- A date-range/month switcher (already month-scoped) + a "Google data" freshness stamp.
- **Guarded reads hide column typos as ZEROS** (CLAUDE.md gotcha) — when a panel reads 0, check SQL columns
  vs schema first (this is exactly how the old NPS `created_at` bug hid).

## 6. Phasing (proposed)
- **Phase A — first-party fixes + growth (no Google dependency):** court-util TZ fix · visitor headline
  re-lead · **members-by-type stacked** · **trial funnel** · bookings-by-type surfaced · logged-in
  new/returning. *Highest value, lowest risk — all from our own DB.*
- **Phase B — Google ingestion:** `core.web_daily` + `analytics-ingest` cron + digest push + the acquisition
  section (channels / geo / GSC / conversions).
- **Phase C — polish:** membership cohort curves, Google-reviews trend, GA4-vs-beacon cross-check, exports.

## 7. Decisions (2026-07-18)
**LOCKED (Tomo):**
- **Phase order = A → B → C.** Build first-party fixes + growth first (no Google dependency).
- **Timezone = hard-default `Africa/Johannesburg`** (`_CLUB_TZ` in `insights/repositories.py`). A per-club
  `club.timezone` column comes later when a 2nd club with a different zone onboards.

**Still to confirm at build:**
- **PAYG definition** — recommend "active client, no paid membership, **booked in the window**" (a live PAYG
  user, not every dormant account).
- **Google granularity** (Phase B) — daily snapshot via the digest push (recommended).

## 8. Progress
- ✅ **Court-utilisation timezone FIXED** (2026-07-18): `_booked()` now extracts weekday/hour
  `AT TIME ZONE 'Africa/Johannesburg'`, so the heatmap reads in SAST and aligns with availability hours.
- ✅ **PHASE A BUILT** (2026-07-18): all §9 readers + panels shipped.
  - Backend (`insights.overview`): `tier_series` (per-day active subs stacked by tier + `Trial`) +
    `tier_current` donut snapshot · `members_joined`/`members_cancelled` net growth · `trials_started`/
    `trials_lapsed` + rolling `trials_total`/`trials_converted`/`trial_conversion_rate`/`trials_active` ·
    `logged_in_new`/`logged_in_returning` (authed first-seen) · `payg_active` KPI (30-day live PAYG base).
  - Frontend (`admin_app.js` `#/overview`): Members tab now leads with the **membership-composition
    stacked area** (hero) + current-mix **donut** + net-growth chart + **trial-funnel** panel + PAYG/Trials
    KPI tiles; Traffic tab gains **logged-in new-vs-returning**. New helpers `ovTierColors`/`ovPieOption`
    (Trial always amber). Public-visitor headline re-lead + bookings-by-type were already live.
  - Verified: every new query run **unguarded** against the live schema (no silent-zero column bug); a
    seeded rolled-back scenario asserts all aggregates + the per-day invariant **stacked tiers ==
    active_members**. Numbers to be eyeballed on prod `#/overview` after deploy (dev DB has no live data).

## 9. Phase A — implementation brief (turnkey; build + eyeball each panel live)

> **QA rule for this lane:** every `insights` read is `_guard`-wrapped, so a **wrong column name shows as a
> silent 0, not an error** (the old NPS `created_at` bug). After each reader, open `#/overview` on real data
> and confirm the panel is non-zero/plausible before moving on. There is no harness for `insights`.

**Backend — extend `insights.repositories.overview` (reuse the `_fill(rows, *keys)` closure + `p` params):**
1. **Membership composition (stacked) — `tier_series`**: per-day active PAID subs grouped by
   `price.membership_tier`, plus `provider='trial'` bucketed as `Trial`. Query: `generate_series(:s,:e-1d)` ×
   `membership_subscription` on the SAME active predicate the existing `members` block uses
   (`period_start <= g` AND `cancelled_at` null/after AND `current_period_end` null/≥ g), LEFT JOIN
   `billing.price pr`. `CASE WHEN ms.provider='trial' THEN 'Trial' ELSE COALESCE(NULLIF(pr.membership_tier,''),
   'Member') END AS tier`. Pivot to `{tier: [per-day]}` in Python via `pos`.
2. **Net growth — `joined` / `cancelled` per day**: paid subs (`provider<>'trial'`) by `period_start` in
   window; and by `cancelled_at::date` in window.
3. **Trial funnel — `trials_started` / `trials_lapsed` per day + `trial_kpis`**: started = trial subs by
   `period_start`; lapsed = trial subs with `current_period_end` in window AND `NOT EXISTS` an active paid
   sub for that user. KPIs (rolling, lifetime): `active_trials`, `total_triallers` (distinct trial user_ids),
   `converted` (triallers who now hold any paid sub) → `conversion_rate = converted/total_triallers`.
4. **Logged-in new/returning — `li_new`/`li_return` per day**: over `page_view` where
   `metadata->>'authed'='true'`; a day's authed `anon_id` is NEW if its first-ever authed `occurred_at` is
   that day, else returning. (Mirror `analytics.new_vs_returning`, but authed.)
5. Add all to the `series` + `kpis` return dict. Columns to trust: `membership_subscription`(club_id,user_id,
   price_id,status,provider,period_start,cancelled_at,current_period_end), `price.membership_tier`,
   `usage_event.metadata`(anon_id,authed). **Verify each against the live schema first.**

**Frontend — `admin_app.js` `#/overview` (reuse the ECharts seam; add stacked-area + donut helpers):**
- **Re-lead the header:** headline = **public unique visitors** (`kpis.public_visitors`) + **logged-in
  visitors** (`kpis.logged_in_visitors`) as a SEPARATE tile. Demote/relabel raw `visits`. This kills the
  "~400 doesn't make sense" confusion (that number is in-app navigation — put it under "Members-area").
- **Membership composition** = stacked area from `series.tier_series` (paid tiers + Trial) — the hero growth
  chart. + a donut of the current mix. + net-growth line (`joined` vs `cancelled`).
- **Trial funnel** = started vs lapsed per day + a conversion-rate stat tile.
- **Bookings by type** = stacked `bookings_court`/`bookings_lesson`/`bookings_class` (data already in the
  payload — just surface it; the lesson double-count is already collapsed in the query).
- Keep the re-zoned court-utilisation heatmap.

**PAYG (deferred decision):** show a KPI = active PAYG players (distinct clients with a non-covered,
non-token booking in the trailing 30d, no active paid sub) rather than a per-day series — simplest honest
measure. Confirm with Tomo whether a per-day PAYG line is wanted.

## 10. Phase B / C (later)
- ✅ **PHASE B BUILT** (2026-07-19): Google (GA4 + Search Console) now flows INTO the dashboard via
  PUSH (no Google creds on Render).
  - **`core.web_daily`** snapshot store (`core/schema.py`, raw idempotent DDL + guarded club FK): one
    `(source, metric, label, value, meta jsonb)` datum per snapshot `day`; `meta` carries a query's
    position/clicks alongside impressions. Unique `(club,day,source,metric,label)`.
  - **`POST /api/cron/analytics-ingest`** (`diary/routes.py` cron_bp, OPS-guarded): resolves the club
    server-side (slug > host > sole club), then **delete-then-insert replaces the whole (club,day)
    snapshot** so re-runs + shrunk top-lists stay clean. Idempotent.
  - **`insights.web_metrics(club_id)`** reader + **`GET /api/insights/web-metrics`**: latest snapshot →
    `{connected, as_of, ga4:{totals,channels,top_pages,geo,conversions}, gsc:{totals,top_queries,striking}}`,
    guarded → `{connected:false}` until first ingest.
  - **Digest push** (`marketing_digest/digest.py`): `ga4_metrics`/`gsc_metrics` structured fetchers (each
    query guarded, request shapes mirror the proven `*_block` ones) + `ingest_metrics` POST. Only a brand
    with `ingest_host` (NextPoint) is pushed — Ten-Fifty5 stays email-only. **Reuses the digest's existing
    `MARKETING_DIGEST_API` + `OPS_KEY` — NO new secrets**; runs in the same 07:00-SAST daily job.
  - **Frontend**: new **Acquisition** tab in `#/overview` — GA4 totals + a **channels donut** + top-pages +
    geo; GSC totals + top-queries + **striking-distance** list; a "Google data as of <date>" stamp and a
    dark "not connected yet" state. Reuses `ovPieOption`/`ovTierColors`/`ovMetricList` (config, no fork).
  - **Verified locally end-to-end**: the real ingest endpoint driven via Flask test-client (auth-guard 403,
    17-row ingest with 2 invalid dropped, idempotent re-run no-dupes) → read back through `web_metrics`
    (channels sorted, `meta` passthrough, window_days). `db` twice = no-op. The GA4/GSC *extraction* is
    CI-only (no creds locally) — fully guarded, mirrors working shapes; first live run visible in the
    digest's new "📈 Dashboard ingest" section. **GA4 conversions (start_free_week/booking/purchase)
    deferred to Phase C** — they couple to conversion-event config that can't be verified without creds;
    the store already accepts `metric='conversions'` when added.
- ✅ **PHASE C BUILT** (2026-07-19) — polish, mostly first-party:
  - **GA4 conversions** (C1): digest `ga4_metrics.conversions()` fetches key events by name (tries
    `keyEvents` then `conversions`, guarded); stored as `metric='conversions'`, rendered as a
    "Conversions · key events" list in the Acquisition panel. (First live values appear after the next
    digest run; dark until then.)
  - **Trial cohort curves** (C2): `insights.trial_cohorts(club_id, months)` +
    `GET /api/insights/trial-cohorts` — per start-month: started + converted-within-14d/30d/ever + rates;
    a compact table in the Members tab. **Locally verified** (seeded rollback: 3 trials → 14d/30d/ever = 1/2/2, rates 33/67/67%).
  - **GA4-vs-beacon cross-check** (C3): `web_metrics.cross_check` compares GA4 sessions to our own
    first-party beacon (public views/visitors) over the GA4 window — a health ribbon in the Acquisition
    panel (honestly labelled: the two measure slightly different things). **Verified** (authed hits
    excluded; 2 public views / 1 visitor).
  - **CSV export** (C4): a "⤓ CSV" button on the Overview header downloads the month's daily series
    (flat series + membership tiers; money in minor units). Pure frontend.
  - **Feedback & review funnel (C5) — BUILT from real data** (the `/feedback` page is already live and
    writes `core.nps_response`). The Experience tab is now a **review/feedback funnel**: NPS score +
    responses + promoter % KPIs, a **sentiment donut** (promoters → Google review CTA / passives /
    detractors → private form), and the daily responses chart. `overview` KPIs gained `nps_promoters`/
    `nps_detractors`. **Review CLICK-THROUGHS already surface** as the GA4 `review_click` conversion in the
    Acquisition tab (via C1). ⏭️ The **only deferred piece = actual Google review COUNT/rating**, which
    needs a **Google Business Profile API feed** (a `source='gbp'` push into `core.web_daily` — the store +
    reader are already generic enough; only the GBP fetch + SA grant are missing). **← REVIEW/CONFIG item.**
  - Verified: `db` twice no-op; every new reader run against the live schema; the ingest/conversions/
    cross-check driven through the real endpoint via Flask test-client. GA4 conversion *extraction* is
    CI-only (guarded).

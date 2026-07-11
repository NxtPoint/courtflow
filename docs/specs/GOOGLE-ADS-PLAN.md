# Google Ads / Analytics — Plan & Console Steps

> **STATUS 2026-07-11: measured-acquisition stack COMPLETE.** Account `704-275-3564` / `AW-17077631191`
> (`info@nextpointtennis.com`). The account went from *flying blind* (0 conversions, blind bidding, no tag
> firing) to fully instrumented: web tag live, **2 primary web conversions + `Offline purchase`** (gclid CSV),
> bidding fixed, **GA4↔Ads** + **GA4↔Search Console** linked, a remarketing audience, and 6 rebuilt sitelinks.
> The gclid → offline-conversion loop is coded (`offline_conversions/` + `attribution.js`) and the conversion
> action is live. **The only remaining user step is the scheduled CSV upload (needs the feed password) — see
> "FINAL STATE" below.** Ongoing: complete advertiser verification; revert bidding after conversions accrue.

## Live audit (2026-07-11) — what was found
- **0 conversions recorded.** The only goal was a vanity **"Page views"** action with **no primary
  conversion** — Google itself flagged it "cannot be used for optimization."
- **Bidding was broken:** the live **NextPoint – Search – JHB** campaign (R66/day) was on **"Maximize
  Conversions" with 0 conversions** → bidding blind, wasting spend.
- **Google tag not firing:** Google reports **"No tag found for this account"** → `GOOGLE_ADS_ID` is almost
  certainly **not set on `courtflow-web`**, so the gtag isn't on the live site and nothing is measured.
- Agency **Performance Max** ("Aug - Cyborg Digital 2025") is **paused** — leave it paused.
- **Advertiser verification** was pending — Tomo updated business details 2026-07-11; verification in progress.

## What was DONE in the account (2026-07-11)
- ✅ Created **"Free week started"** (Sign-up category, **Primary**, manual event) → `start_free_week`.
- ✅ Created **"Booking completed"** (Purchase category, **Primary**, manual event) → `booking`.
- ✅ Switched **NextPoint – Search – JHB** bidding **Maximize Conversions → Maximize Clicks, max CPC R15**
  (predictable traffic while conversions accrue; revert once ~15–30 conversions land — see below).
- Code (already shipped): `cfConversion('booking')` on the booking success screen; `cfConversion('start_free_week')`
  site-wide on any `/login#/sign-up` (or `a[data-conv]`) CTA click. Both no-op until the env below is set.

## ✅ FINAL STATE (2026-07-11) — what's live + the ONE remaining user step
**Env set on `courtflow-web` (done):** `GA4_MEASUREMENT_ID=G-EKQP47P8M9`, `GOOGLE_ADS_ID=AW-17077631191`,
`GOOGLE_ADS_CONVERSIONS={"start_free_week":"AW-17077631191/rEy7CNKNsc4cENfxn88_","booking":"AW-17077631191/tu5JCNWNsc4cENfxn88_"}`.
**Env for the offline-conversion feed (courtflow-api):** `GOOGLE_ADS_FEED_USER` / `GOOGLE_ADS_FEED_PASS` (set).

**Live in the account:** web tag firing · 2 primary web conversions (start_free_week, booking) · **`Offline
purchase`** conversion action (Purchase, Primary, value-based ZAR, count Every — name MUST match code) ·
bidding = Maximize Clicks R15 cap · GA4↔Ads linked (auto-tagging + Personalized Advertising ON) · GA4↔Search
Console (`nextpointtennis.com` domain property) linked · remarketing audience "High-intent visitors
(booking/pricing)" (90-day) · 6 clean sitelinks (all → live `nextpointtennis.com` paths).

**⭐ THE ONE REMAINING USER STEP — the scheduled CSV upload** (needs the feed password, so Tomo does it):
Google Ads → Goals → Conversions → **Uploads → Schedules → New schedule** → Source **HTTPS**, URL
`https://courtflow-api.onrender.com/feeds/google-ads/offline-conversions.csv` (or the custom club-API domain),
Auth **HTTP Basic** = `GOOGLE_ADS_FEED_USER`/`PASS`, Frequency **Daily**. First run shows **0 imported** (normal
— no gclid'd purchase yet); check Uploads → History only shows *auth/format* errors. *(Set to daily 4am SAST.)*

## Then (Tomo, no rush)
- **Complete advertiser verification** (in progress) — or ads can pause.
- **After ~15–30 conversions accrue** (a week or two), switch **NextPoint – Search – JHB** bidding back
  **Maximize Clicks → Maximize Conversions → Target CPA** — now it optimises toward real members.
- **Verify** with Google **Tag Assistant** on the live site: click a "Start your free week" CTA + complete a
  booking, confirm the two conversions fire.
- (Optional) demote the vanity **"Page views"** action to secondary; leave "Calls from ads" as secondary.

## Live ASSET audit (2026-07-11 pt 2) — what the agency left + the sitelink leak
Full account asset library (via the Ads API), not just the "19 images" one view shows:
- **59 images** (13 landscape, 13 square, 10 portrait, 2 logos, 3 logo-landscape, 9 other) + **5 YouTube
  videos** ("Become a Better Tennis Player") — all agency (Cyborg Digital) creative, sitting in the **paused
  PMax**. Plenty of raw material; **don't commission more creative yet** (measure first — see below).
- **11 callouts** — good ("Only Clay Court in GP", "8 Resurfaced Courts", "ATP-Certified Coaches", "Free
  First Lesson"). Keep.
- **1 structured snippet** (Service catalog) — add 1–2 more.
- **17 sitelinks — THE leak.** Messy, duplicated (Contact Us ×2, three "Book a Lesson" variants) and every
  one points at **dead Wix `copy-of-…` / `service-page/…` / `booking-calendar/…` URLs**. Those slugs do NOT
  exist on the new site — `nextpointtennis.com` is now the CourtFlow app (courtflow-web), where the tag also
  fires. So paid clicks partly land on broken/redirected pages AND miss the tag.

**FIX — replace all 17 sitelinks with these 6 clean ones (new-site paths, where the tag fires):**

| Sitelink | → path on nextpointtennis.com |
|---|---|
| Start Your Free Week | `/login#/sign-up` |
| Free First Lesson | `/free-lesson` |
| Book a Court | `/book` |
| Our Coaches | `/coaches` |
| High Performance | `/programs/high-performance` |
| Pricing & Membership | `/pricing` |

**Cyborg PMax:** leave PAUSED, do NOT delete — it holds the 59 images + 5 videos + learning history for a
future *tracked* PMax. Deleting throws away paid creative.

## Deeper loop — next builds (engineering; say the word)
These close "Google ↔ site, both directions" and are the real ROI compounders:
1. **Completed-signup conversion (accurate)** — fire `sign_up` only when the trial is actually granted (a
   client hook after Clerk sign-up), not just on CTA click. Small.
2. **gclid capture → offline conversion import** — **The biggest ROI lever**: teaches Ads to bid for people
   who actually become paying members, not just clickers.
   - ✅ **Increment 1 SHIPPED (2026-07-11, commit `96a9cf5`)** — first-touch capture live:
     `frontend/js/attribution.js` buffers `gclid`/`gbraid`/`wbraid`/`fbclid` + `utm_*` on landing →
     flushes once via `TFAuth` to `POST /api/me/acquisition` after sign-in →
     `core.repositories.acquisition.record_acquisition()` persists onto `core.acquisition`
     (first-touch wins). gclid now accrues on every ad-driven signup.
   - ⏳ **Increment 2 (needs creds)** — a cron that finds `core.acquisition` rows WITH a `gclid` whose user
     has a qualifying downstream conversion (first booking / membership) not yet uploaded, and uploads the
     ClickConversion to Google Ads via the Ads API. Requires: a NEW **"Offline booking/membership"**
     conversion action in the account (Import → API), plus env `GOOGLE_ADS_DEVELOPER_TOKEN`,
     `GOOGLE_ADS_OAUTH_*` (refresh token), `GOOGLE_ADS_LOGIN_CUSTOMER_ID`. Add an `uploaded_at` marker so
     each conversion uploads once. Enhanced-conversions (hash + send email) layers on cheaply once this
     exists.
3. **Customer Match audiences** — upload hashed member emails to (a) **exclude existing members** from ad spend
   (stop paying to advertise to people who already joined), (b) seed **lookalike** audiences. Medium build.
4. **GA4 conversion events** — mark `booking_completed` + `start_free_week` as GA4 key events (in the GA4
   console) for funnel + attribution reporting.
5. **Enhanced conversions** — hash + send email on conversion for better match rates. Low effort once #2 exists.

## The rest of the Google estate — GA4, Search Console, Business Profile
Beyond Ads, these are the other Google surfaces and what to do with each:

**GA4 (Analytics)** — collecting now that `GA4_MEASUREMENT_ID=G-EKQP47P8M9` is set.
- Mark `booking_completed` + `start_free_week` as **Key events** (Admin → Events).
- **Link GA4 ↔ Google Ads** (GA4 Admin → Product links → Google Ads) → import those key events as Ads
  conversions AND build a **remarketing audience** (site visitors who didn't sign up).
- Note: we ALSO have a first-party, cookieless beacon (`analytics.js` → Business Overview cockpit), so GA4 is
  the *ad-attribution* view, not our only analytics — no lock-in.

**Search Console (GSC)** — the free organic-search truth. `nextpointtennis.com` should be a verified
property (the ten-fifty5 `seo/` engine already holds GSC OAuth that covers this domain). Actions: confirm
the property is verified (domain-level), submit `sitemap.xml` (the site generates one at `/sitemap.xml`),
then feed the striking-distance queries into the content cadence below. Verification meta is wired: set
`GSC_META_TOKEN` to drop the verification `<meta>` on every page (`web_app._gsc_meta`).

**Google Business Profile (Maps)** — for a PHYSICAL club this is the highest-leverage FREE Google asset
(the map pack for "tennis lessons johannesburg", "clay court near me"). Actions: claim/verify the listing,
complete every field (hours, photos of the 8 clay courts, services), keep the website link pointing at
`nextpointtennis.com`, and wire a **review-request** into the post-lesson flow (Klaviyo `lesson_completed`
→ ask happy players for a Google review). Reviews + proximity are what win the map pack. No code needed —
this is an account/ops task, playbook-driven.

**Merchant Center / Shopping** — only relevant if the pro-shop sells online (there's a "Shop" sitelink).
Low priority; revisit if e-commerce becomes a real revenue line.

## SEO (separate track)
The `seo/` engine (shared with ten-fifty5, keyless GSC OAuth) already surfaces striking-distance queries.
Turn that into a **content cadence**: Cowork writes posts targeting those queries → `build_blog.py` publishes.
Run `python -m seo.weekly_seo --site nextpoint` for the report.

## Guardrail
Don't set a conversion action to count **every** page view or click as a conversion — that's the "vanity Page
view" trap that was already caught. Only the two real actions above (+ the deeper ones) should be conversions.

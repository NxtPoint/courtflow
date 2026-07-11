# Google Ads / Analytics — Plan & Console Steps

> The R2k/month Search campaign is well-built, but the account had **no real conversions** — so it was
> flying blind. A live audit + build was done in the account on **2026-07-11** (account `704-275-3564`,
> `AW-17077631191`, owned by `info@nextpointtennis.com` ✓). Two real conversion actions were created and the
> broken bidding was fixed. **One thing remains: set the env on `courtflow-web` so the tag actually fires.**

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

## ⭐ YOUR remaining step — set the env on `courtflow-web` (Render → Environment)
This makes the Google tag fire on the live site (fixing "No tag found") AND maps the conversions:
```
GA4_MEASUREMENT_ID     = G-EKQP47P8M9
GOOGLE_ADS_ID          = AW-17077631191
GOOGLE_ADS_CONVERSIONS = {"start_free_week":"AW-17077631191/rEy7CNKNsc4cENfxn88_","booking":"AW-17077631191/tu5JCNWNsc4cENfxn88_"}
```
(Valid JSON on one line; redeploy.) Within a few hours Google detects the tag and starts recording real
sign-ups + bookings.

## Then (Tomo, no rush)
- **Complete advertiser verification** (in progress) — or ads can pause.
- **After ~15–30 conversions accrue** (a week or two), switch **NextPoint – Search – JHB** bidding back
  **Maximize Clicks → Maximize Conversions → Target CPA** — now it optimises toward real members.
- **Verify** with Google **Tag Assistant** on the live site: click a "Start your free week" CTA + complete a
  booking, confirm the two conversions fire.
- (Optional) demote the vanity **"Page views"** action to secondary; leave "Calls from ads" as secondary.

## Deeper loop — next builds (engineering; say the word)
These close "Google ↔ site, both directions" and are the real ROI compounders:
1. **Completed-signup conversion (accurate)** — fire `sign_up` only when the trial is actually granted (a
   client hook after Clerk sign-up), not just on CTA click. Small.
2. **gclid capture → offline conversion import** — capture `gclid` on landing (column exists in
   `core.acquisition`), store it on the person, then upload the *real* conversion (booking / membership) to
   Google Ads via the Ads API — even when it happens days later or off-device. **The biggest ROI lever**:
   teaches Ads to bid for people who actually become paying members, not just clickers. Bigger build (Ads API
   + a conversion action + a cron).
3. **Customer Match audiences** — upload hashed member emails to (a) **exclude existing members** from ad spend
   (stop paying to advertise to people who already joined), (b) seed **lookalike** audiences. Medium build.
4. **GA4 conversion events** — mark `booking_completed` + `start_free_week` as GA4 key events (in the GA4
   console) for funnel + attribution reporting.
5. **Enhanced conversions** — hash + send email on conversion for better match rates. Low effort once #2 exists.

## SEO (separate track)
The `seo/` engine (shared with ten-fifty5, keyless GSC OAuth) already surfaces striking-distance queries.
Turn that into a **content cadence**: Cowork writes posts targeting those queries → `build_blog.py` publishes.
Run `python -m seo.weekly_seo --site nextpoint` for the report.

## Guardrail
Don't set a conversion action to count **every** page view or click as a conversion — that's the "vanity Page
view" trap that was already caught. Only the two real actions above (+ the deeper ones) should be conversions.

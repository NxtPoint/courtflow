# Google Ads / Analytics — Plan & Console Steps

> The R2k/month Search campaign is well-built, but it was optimising toward **"Calls from ads"** because
> real conversions weren't wired. Engineering has now wired the **real conversions**; the remaining work
> is (a) console config you do in Google Ads, and (b) the deeper attribution loop (offline + audiences).

## What's live (GA4 + Ads)
- **GA4** `G-EKQP47P8M9` + **Google Ads** `AW-17077631191`, injected on every served page (`web_app._google_tag_head`).
- **Search campaign** (2026-06-20): PMax paused, 4 ad groups / 38 keywords / 28 negatives, geo-tight to the 10
  northern suburbs, ~R66/day.
- Conversion plumbing: `window.cfConversion(name, props)` fires the Ads conversion mapped in
  `GOOGLE_ADS_CONVERSIONS`; `cfTrack(name, props)` fires the GA4 event.

## What engineering just wired (fires automatically once you map it)
| Conversion name | Fires when | Where |
|---|---|---|
| `booking` | a member completes a self-booking (not held/awaiting-payment, not staff on-behalf) | booking success screen |
| `start_free_week` | anyone clicks a "Start your free week" / sign-up CTA (`/login#/sign-up`, or any `a[data-conv]`) | site-wide |

Both are **no-ops until you map them** in `GOOGLE_ADS_CONVERSIONS` — so nothing double-fires by accident.

## YOUR steps in Google Ads (this activates it)
1. **Create two conversion actions** — Ads → Goals → Conversions → **+ New** → Website → *set up manually with
   code / gtag*. Name them e.g. **"Free week started"** and **"Booking completed"**. For each, copy its
   **send_to label** (looks like `AW-17077631191/AbCdEfG…`). Category: "Sign-up" and "Purchase"/"Submit lead".
2. **Set the env** on `courtflow-web` (Render → Environment):
   ```
   GOOGLE_ADS_CONVERSIONS = {"start_free_week":"AW-17077631191/AAA…","booking":"AW-17077631191/BBB…"}
   ```
   (valid JSON; redeploy.)
3. **Make them PRIMARY**, demote "Calls from ads" to secondary (keep it, don't delete). Set **"Free week
   started"** as the main optimisation goal to start (higher volume → the algorithm learns faster); "Booking
   completed" is the deeper-value secondary.
4. **Bidding** — keep **Maximize Clicks (max CPC ~R8)** until **~15–30** "Free week started" conversions accrue,
   then switch to **Maximize Conversions → Target CPA**. Don't switch early (not enough data = erratic bidding).
5. **Verify** — Ads → Conversions should show "Recording conversions" within a day; use Google **Tag Assistant**
   on the live site to confirm the tags fire on a sign-up-CTA click + a booking.

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

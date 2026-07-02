# NextPoint Tennis — Google Ads Audit & Optimization Plan

**Account:** NextPoint Tennis Centre (Google Ads ID 704‑275‑3564, `info@nextpointtennis.com`)
**Reviewed:** 2026‑06‑20 · **Data window seen:** 4–31 Aug 2025 · **Reviewer:** Cowork (for Tomo)

---

## Snapshot (what the account is doing)

| Metric | Value |
|---|---|
| Campaigns | 1 — **"Aug – Cyborg Digital 2025"** (agency: Cyborg Digital) |
| Type | **Performance Max** |
| Budget | **R72/day** (~R2,160/mo) |
| Impressions | 18,962 |
| Clicks | 1,355 |
| CTR | 7.15% |
| Avg CPC | R1.57 |
| Spend | R2,132.75 |
| Bid strategy | Maximize conversions |
| **Conversions** | **0 — conversion rate 0.00%** |
| Optimization score | 84% |

## Live 90‑day pull (21 Mar – 19 Jun 2026, via Adspirer API)

| Metric | Value |
|---|---|
| Spend | **R6,598** (~R2,200/mo) |
| Impressions | 96,141 · Clicks 3,650 · CTR 3.8% · CPC R1.81 |
| **Conversions** | **4 in 90 days** · conv. rate 0.11% · **cost/conv R1,650** |
| Impression share | 39.8% · **24.3% of impressions lost to budget** (budget‑constrained) |
| Campaign | Same single PMax "Aug – Cyborg Digital 2025", ENABLED |

**Conversion‑tracking audit (grade C, 76.7/100):** tracking *does* exist, but the primary
conversion actions are **"Page view"** and **"Calls from ads"**. That's the real problem — **a page
view is being counted as a conversion**, so "Maximize conversions" optimises toward cheap traffic that
merely loads a page, not bookings/leads. "Calls from ads" is the only meaningful signal. Its
view‑through attribution window is also too long (30d → should be ≤1d), and enhanced conversions are
unconfirmed.

## The core problem (revised)

1. **Vanity conversion tracking.** Not "zero" as first thought — but the primary conversion is
   essentially **"Page view"**, which is meaningless and actively misdirects the bidding. Only ~4 real
   conversions in 90 days for R6,598 (≈R1,650 each), and those are page‑views/calls, not bookings.
2. **Performance Max is the wrong format** for a small local club: scatters budget across channels,
   hides the true search‑terms report, and is **budget‑constrained** (losing ~24% of impressions) —
   so it's both leaky and capped.

## Fix list (priority order)

1. **Set up conversion tracking (do first).** Track: booking‑page completions, "Book a Court/Lesson" clicks, **phone‑call clicks (076 990 7439)**, "Claim Free Lesson" form, WhatsApp clicks. Via Google Tag / GA4 import. Foundation for everything else.
2. **Shift budget PMax → a tight local Search campaign** (control + visible search terms). Keep PMax only if it later proves out *with* conversion data.
3. **Tighten geo:** radius around Killarney/Houghton + northern Joburg suburbs; targeting = **"presence: people in your area"**, not interest.
4. **High‑intent keywords** (phrase/exact, not broad): tennis lessons Johannesburg · tennis coaching Houghton · tennis court hire Johannesburg · junior tennis lessons Johannesburg · cardio tennis Johannesburg · clay court Johannesburg · holiday tennis camp Johannesburg · high performance tennis academy.
5. **Negative keywords:** jobs · free · rackets/equipment · tennis elbow · tennis bracelet · ATP/WTA scores · live · results · online game · stringing.
6. **Ad assets/extensions:** sitelinks (Book a Court · Book a Lesson · Junior Programs · Free Lesson), call extension (076 number), **location extension linked to Google Business Profile**, callouts (8 courts · only clay court in Gauteng · ATP‑certified coaches), structured snippets.
7. **Landing‑page relevance:** route lesson clicks → lessons booking page, court clicks → court booking page (not homepage). Push the **free‑lesson funnel** as the primary low‑friction conversion.
8. **Google Business Profile:** claim/optimise — for a local club this drives more free high‑intent traffic than paid; ties into the website SEO migration.
9. **Bidding:** R72/day budget is fine. Once conversions track for ~2–4 weeks, let Maximize conversions work on real data, then graduate to Target CPA.

## Questions to put to the agency (Cyborg Digital)

1. Is conversion tracking installed and firing? (The account shows 0 conversions on R2,133 spend.)
2. Why Performance Max instead of Search for a local club?
3. What is the geographic targeting set to?

If #1 can't be answered cleanly, that's the signal on the relationship.

## Next data to pull (when convenient)

- PMax **Insights → search themes / categories** (PMax hides true search terms — a reason to move to Search).
- **Settings → Locations** (confirm geo).
- **Asset groups** (see where PMax is sending spend / which assets exist).
- 90‑day trend (is spend steady, is anything else paused).

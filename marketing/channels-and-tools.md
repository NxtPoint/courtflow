# Marketing Stack, Channels & the SEO-Migration Question

> Plain-English reference for: what each tool actually does, how social/Meta will work, and whether the
> Wix→CourtFlow switch threatens NextPoint's Google rankings. Owner: Tomo + Cowork.

## 1. The tool stack — what does what

**Important mental model:** *tools don't optimise — the loop does.* No single tool "runs your ads." The
optimisation brain = (a) Google's/Meta's own machine bidding *inside* the account, plus (b) us reading
the data and deciding. Adspirer is the **hands**, not the brain.

| Tool | Role | Status |
|---|---|---|
| **Adspirer** | The **agent interface** that lets Claude (Cowork/Claude Code) read + change ad accounts via chat — Google Ads now, **also Meta/TikTok/LinkedIn** when we go there. It executes; it doesn't think for us. | ✅ Connected (Google Ads) — manage in **Claude Code** (can't auth in Cowork). |
| **Google Ads** | The paid-search engine + its ML bidding + conversion tracking. | ✅ Live (Search campaign). |
| **Google Business Profile (GBP)** | Your free Maps/Search listing + **reviews** + NAP. Huge for a local club. Separate from the website. | ⚠️ Confirm claimed/optimised. |
| **Ahrefs** | SEO: keyword research, rank tracking, backlinks, competitor analysis. | ✅ Connected. |
| **Google Search Console (GSC)** | Free: how you rank organically, which queries/pages, crawl/index health. The migration safety net. | ✅ Connected. |
| **Own analytics — `core.usage_event` + page‑view beacon** | CourtFlow's **built‑in** event tracking (ported from 1050's `marketing_crm/tracking`). The source of truth for site + booking behaviour; feeds Klaviyo + Google/Meta conversions. **We use this instead of GA4.** | Built with CourtFlow. |
| **Klaviyo** | Email + automation (booking confirmations + lifecycle). | Planned (CourtFlow). |
| **Meta Business Suite** | Free: schedule FB/Instagram posts + run/manage Meta Ads. | ⏳ When we add social. |

> **Deliberately NOT using GA4, and HubSpot was considered & dropped.** Per the 1050 decision, **we are
> our own CRM + analytics** — our `core.*` event stream is the single source of behavioural truth, which
> we own and can pipe anywhere (Klaviyo, ad conversions). No GA4, no HubSpot. (Note: a product‑analytics
> tool like Amplitude exists as an option but we chose our own tracking — don't add it without a reason.)

**Do we need more tools? No — stack confirmed COMPLETE (2026‑06‑20).** No further tools required for ads,
SEO, email, analytics, or social. Adspirer covers paid (incl. Meta Ads); Meta Business Suite (free)
covers organic posting; Canva covers design; Cowork covers content/strategy. Resist adding tools — they
don't move the needle, the loop does. (Only-if-needed later: a social scheduler like Buffer/Metricool if
posting volume outgrows Meta Business Suite.)

## 2. Social media & Meta — how it'll work (plain English)

Two separate things, often confused:

**A) Organic social (free posts)** — Instagram + Facebook. Content that builds brand/community: court &
clay-court shots, coach tips, junior wins, class schedules, member stories, the free-lesson offer.
- *How:* post via **Meta Business Suite** (free) — one place for FB + IG, schedule ahead.
- *Help:* Cowork can draft a **content calendar + captions**, and generate images (Canva). You are NOT
  on your own here — we make it turnkey.

**B) Paid social (Meta Ads = Facebook/Instagram ads)** — paid placements.
- *Why it's different from Google:* Google = **intent** (people already searching "tennis lessons"
  find you). Meta = **interruption/awareness** (you put a great photo/video in front of parents &
  adults within ~15 km of Houghton who *weren't* searching). Both work; they do different jobs.
- *Best uses for a club:* promote the **free first lesson**, junior programs, cardio tennis; and
  **retargeting** — show ads to people who visited the site but didn't book (very cheap, high ROI).
- *How we run it:* **Adspirer also manages Meta Ads** — so once your **Meta Ads account** is connected,
  Claude builds/optimises Meta campaigns the same way as Google. The **Meta Pixel** (Meta's version of
  conversion tracking) gets wired into the CourtFlow site, exactly like the Google tag.
- *What you'd need (we'll guide):* a Facebook **Page** + Instagram account + a **Meta Business/Ads
  account** + the Pixel on the site.

**Sequencing (don't spread R2k thin):** 1) nail Google + the booking site (intent → bookings); 2) add
**organic** social for brand; 3) layer **Meta retargeting** once CourtFlow tracking is live; 4) scale
Meta prospecting only when the funnel converts. One channel working > three half-funded.

## 3. The big one: will switching Wix → CourtFlow drop our Google rankings?

**Short answer: your equity is mostly safe, the risk is real but managed, and long-term you'll likely
rank BETTER — but a migration done carelessly is the single biggest SEO risk, so we do it by the book.**

**What you KEEP (these are tied to the domain/business, not the website platform):**
- **The domain `nextpointtennis.com`** — stays the same, so its **2 years of age + authority + backlinks
  carry over.** This is the big one. Rankings live with the domain, not Wix.
- **Google Business Profile + your reviews** — completely separate from the site. The migration does
  **not** touch your reviews or Maps listing. (Just make sure GBP points to the new site.)

**Where the risk actually is:** a platform move changes **URLs, page content, and structure** — and if
Google can't map old → new, it can drop rankings. This is why migrations get a bad rap. We neutralise it:
1. **301 redirect every old Wix URL → its new CourtFlow page.** A 301 passes the ranking equity across.
   No URL left behind (we build the full map first — `docs/07` + `migration/`).
2. **Preserve/upgrade content** — keep titles, headings, keywords at least as strong. Wix buried text in
   JavaScript; CourtFlow serves clean, fast, fully-crawlable HTML — which Google **prefers**. Likely a
   net *gain* over a few weeks (same reason we expected gains migrating 1050).
3. **Same NAP** (name/address/phone) + LocalBusiness schema for local SEO.
4. **Submit the new sitemap to GSC, request re-indexing, monitor** Coverage daily.
5. **Reversible cutover:** rollback = point DNS back to Wix. We never let an agent touch DNS unattended.

**Honest expectation:** even with perfect 301s, expect a **small, temporary wobble for ~2–4 weeks**
during re-indexing, then recovery — and probably improvement (faster, crawlable, richer pages + the new
blog for long-tail terms). We've run this exact Wix→Render migration once already (1050), so we have the
playbook.

**Your Google *Ads* are unaffected** by the site move — ads point to whatever URLs we set; we just
repoint them to the new pages at cutover. And conversions get *better* once properly tracked.

> Bottom line: keep the domain, protect with 301s, keep GBP/reviews (untouched), expect a brief dip then
> a likely lift. The full step-by-step is in `docs/07-marketing-site-and-seo-migration.md`.

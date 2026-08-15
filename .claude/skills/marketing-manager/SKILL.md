---
name: marketing-manager
description: Full-monty marketing review + tune across BOTH brands (NextPoint Tennis + Ten-Fifty5) in one session — measurement health, organic/SEO, technical SEO, Google Ads, content (website blog/landing pages + Google Business Profile posts), reviews/GBP. Produces a per-brand scorecard + a prioritized "do this week" list, and EXECUTES the safe tunes live (spend changes need explicit approval). Use for a periodic deep marketing session (e.g. Saturday), or when the user asks to "review/tune our marketing", "check both sites", "how are we doing on ads/seo", "tune both", or "the full monty".
---

# Marketing Manager — cross-brand deep review + tune

The on-demand DEEP-TUNE companion to the automated daily digest (`docs/specs/MARKETING-ENGINE.md`). The
digest measures; THIS reviews everything and *acts*. Run it end-to-end for both brands, then hand the user a
scorecard + a short prioritized action list, and execute the safe changes as you go.

**Style:** be a sharp, honest marketing director. Lead with the 2–3 things that actually move the needle,
not a data dump. Judge ad ROI by **cost-per-member (CPA)**, never the mis-tracked ROAS. Never invent numbers.

## The two brands (config)
| | **NextPoint Tennis** | **Ten-Fifty5** |
|---|---|---|
| Kind | Physical tennis club (Killarney, JHB) | AI tennis-analysis SaaS |
| Site | nextpointtennis.com | ten-fifty5.com |
| Repo | `C:\dev\nextpoint` (courtflow) | `C:\dev\webhook-server` (Ten-Fifty5 — commit with `CLAUDE_CODE=1`) |
| Render origin | courtflow-web.onrender.com | locker-room-26kd.onrender.com |
| GA4 | `G-EKQP47P8M9` | `G-4167EPFS34` |
| Google Ads | `AW-17077631191` · customer `7042753564` · campaign `23964238993` (NextPoint – Search – JHB) | **none (organic-only by choice)** |
| Blog | `frontend/blog/_posts/*.md`, imgs `/img/` | `frontend/blog/_posts/*.md`, imgs `/blog/images/` |
| GBP review link | `https://g.page/r/Ce9nBEAMXHTpEBM/review` | n/a (SaaS, no location) |

**📁 THE PICTURE LIBRARY — `C:\Users\tomos\OneDrive\Documentos\blog`.** This is where Tomo drops every
hero image / infographic, for BOTH brands. **ALWAYS `ls` it before publishing or refreshing a post** — a
matching picture is very often already sitting there, and a post shipped without one has no hero and no
`og:image` (dead social/link previews). Match by filename against the topic (`heatmap.png` → the heatmap
post, `parents.png` → the juniors post). It is the ONLY picture source — never go hunting elsewhere, and
never ship a post as image-less without saying so.

## Tools
- **Adspirer** (Google Ads) — router `mcp__claude_ai_Adspirer__google_ads(action="execute", tool_name=…)`;
  key tools: `get_campaign_performance`, `get_campaign_targeting`, `analyze_wasted_spend`,
  `analyze_search_terms`, `update_bid_strategy`, `update_campaign` (budget), `add_negative_keywords`.
  **FREE plan = 15 calls/month, resets ~the 20th — be economical (batch reads; `search_tools`/`get_tool_schema` are free).**
- **Daily digest reports** — `marketing_digest/reports/latest.md` + dated files (both brands, GA4+GSC). `git pull` first.
- **Ahrefs** — the connector API is "Insufficient plan"; use the **free Ahrefs Webmaster Tools** (Site Audit,
  Keyword Generator) in the web UI + the free DR endpoint (needs a free API key from 1 Aug 2026).
- **Technical crawl** — plain `curl` (redirects, broken links, meta/title lengths, canonical/h1/schema/og, load time).
- **Content** — `python build_blog.py` in the right repo (frontmatter `title/description/date/image:`; filename=slug).
  **Hero recipe (Pillow):** pull the source from the picture library above → `Image.open(p).convert("RGB")` →
  `.save(out, "WEBP", quality=82, method=6)` into the brand's image dir → add `image:` to the frontmatter →
  rebuild. That lands a ~1.6MB PNG at ~130–150KB. **Do NOT crop an infographic to 16:9** — they are dense
  full-canvas designs with headers and footers, so a crop eats content; keep the source aspect (the existing
  heroes are a mix of 1536x1024 and 1600x900, so either is in-convention). Crop to 16:9 only for photos.
- **Browser** — Ads console / GSC / GA4 if the Chrome extension is connected (often flaky — prefer Adspirer + digest).

## The routine — run for BOTH brands (skip a phase if its data is unchanged)

**Phase 1 · Measurement health (~1 min).** `curl` each site + its Render origin, grep for the tag (gtag loader
+ the GA4 id). Dark tag = **priority-1 alarm** (a blank tag ID in render.yaml re-clobbered by a blueprint sync —
see MARKETING-ENGINE.md §1; the fix is committing the real ID inline). This is the retired canary's job.

**Phase 2 · Organic / SEO scorecard (~2 min).** `git pull`; read `marketing_digest/reports/latest.md` and a
report ~7 days older for movement. Per brand pull: GA4 traffic + trend, GSC clicks/impressions/avg-position +
trend, and the **🎯 striking-distance queries** (pos 8–20). Name the **top 2–3 opportunities** (high impressions,
pos ~8–15) per brand — those are the content/GBP targets.

**Phase 3 · Technical SEO (~2 min).** Crawl key pages: redirect chains, broken internal links (font-CDN
preconnects `fonts.g*.com` 404 at root = **false positives, ignore**), meta descriptions ≤ ~158 chars, titles
≤ ~60, and canonical/h1/viewport/schema/og present + sub-0.5s load. Punch-list only real regressions.

**Phase 4 · Google Ads — NextPoint only (~3 min, Adspirer).** `get_campaign_performance` (7–14d: spend,
conversions, **CPA trend**). `analyze_search_terms` / `analyze_wasted_spend` for negatives + waste (**ignore the
ROAS "wasted spend" figure — conversion VALUE is under-tracked; a member is worth far more than the ~R76 CPA**).
Confirm bidding = `MAXIMIZE_CONVERSIONS`. **Scale rule:** if it spends its full daily budget every day AND CPA
holds → recommend bumping budget ~20–30% and re-check next week; never *reduce* a profitable campaign. **Any
spend/bid/budget change = SHOW the exact change and get an explicit "go" BEFORE executing** (`update_bid_strategy`
/ `update_campaign` / `add_negative_keywords`). Ten-Fifty5 has no ads — skip.

**Phase 5 · Content — website pages + GBP posts (~5–8 min).** Two complementary plays off the same
striking-distance queries. Aim to ship a website page AND its matching GBP post together.
- **① Website blog / landing pages (organic — BOTH brands).** Propose 1–2 topics per brand from the 🎯
  queries (+ Ahrefs free Keyword Generator). Draft + publish via `build_blog.py`: create
  `frontend/blog/_posts/<slug>.md` (frontmatter `title/description/date/image:`; filename = the slug;
  description ≤ ~155 chars), optimize the hero to WebP ~16:9 with Pillow, run `python build_blog.py`, then
  commit the new `.md` + generated `.html` + `index.html` (explicit paths). **NextPoint → courtflow** (imgs
  `/img/`); **Ten-Fifty5 → the Ten-Fifty5 repo with `CLAUDE_CODE=1`** (imgs `/blog/images/`; it also has a weekly
  coworker SEO→post workflow — memory `ten-fifty5-weekly-seo-blog-workflow`). **A book/buy-intent query
  ("tennis lessons johannesburg", "book a court") wants a landing-style page with CTAs to `/book`
  `/free-lesson` — NOT a how-to article.**
- **③ Cowork weekly output — publish + DE-DUPE (Ten-Fifty5).** A scheduled Claude **Cowork** task writes a
  researched blog + SEO report + infographic each week: blog `.../local-agent-mode-sessions/.../outputs/blog-*.md`
  + `weekly-seo-report-*.md`; infographic in `C:\Users\tomos\OneDrive\Documentos\blog\`. When one exists:
  **BEFORE publishing, `ls frontend/blog/_posts/` and check for an existing post on the same topic.** New topic →
  publish (hero from the picture library, per the recipe above). **Duplicate topic → REFRESH the existing URL
  instead** (swap in the new infographic + bump the date + optionally enrich) — never create a competing page
  (keyword cannibalization). Cowork = the autonomous weekly research+writer; THIS skill = the publisher /
  de-duper / operator.
  - **DE-DUPE THE DRAFTS AGAINST EACH OTHER TOO, not just against the repo.** If Tomo hands you several output
    folders at once they may be the SAME brief written on different weeks (seen 2026-08-08: two drafts, both
    `slug: how-to-read-a-tennis-heatmap`). Two posts asked for ≠ two posts published. **Merge into ONE page** —
    take the stronger draft as the base and graft in whatever the other one uniquely adds — then say plainly
    why it's one page. Publishing both would cannibalize, and identical slugs silently overwrite at build time.
  - **⚠ VERIFY THE WEEKLY SEO REPORT'S ISSUES AGAINST THE LIVE SITE BEFORE ACTING — it has repeatedly invented
    them** (2026-07-26 and again 2026-08-07). Its recurring failure is asserting a **"Wix template vs custom
    template" split** for `/pricing` `/coaching` `/contact-us`, then deriving a whole action list from that
    fiction (missing meta descriptions, a contact-email typo, a raw `wixstudio.com` CTA, orphaned posts, absent
    JSON-LD). On 2026-08-07 **every single item was false** — all pages are on the one Render stack, all carry
    meta description + og:image + schema, all 11 posts were linked from `/blog`. One `curl` per claim settles
    it (`grep` for `generator`, `wixstudio`, `<meta name="description"`, `ld+json`, `/post/` links). Report the
    fabrication to Tomo rather than filing the work. Its **competitor scan and topic ideas are still useful** —
    those don't depend on the broken site analysis.
- **② Google Business Profile posts (local / map — NextPoint ONLY; Ten-Fifty5 is a SaaS with no GBP).** For a
  physical club, a weekly GBP post freshens the map listing and gives searchers a "Book" button today. **DRAFT
  a ready-to-paste post** (≤ ~1500 chars: a hook + 2–3 ✅ bullets + CTA, a button URL like `/free-lesson` or
  `/book`, and a note to add a photo) — the **user pastes it** (Google Business Profile → their listing → "Add
  update"). **Pair every new website page with a matching GBP post**, and rotate themes week-to-week: free
  week · the clay court · lessons · junior/cardio · floodlit evening play. The website page = the long organic
  game; the GBP post = the immediate local/map game. Ship both.

**Phase 6 · Reviews / GBP — NextPoint only (~1 min).** Reviews drive the "tennis courts near me" map pack.
Check velocity; if slowing, resurface the review-request WhatsApp/email + the gated `/feedback` flow (routes
4–5★ → the GBP link). Aim 40+ reviews.

**Phase 7 · Scorecard + action list.** Output a tight per-brand scorecard — Measurement / Organic / Technical /
Ads / Content / Reviews each as 🟢🟡🔴 with a one-line reason — then a **prioritized "do this week" list (top 3
per brand)** and a note of **what you auto-tuned this session**. That's the deliverable.

## Guardrails
- **Approve before ANY spend change** (bidding, budget, pausing, new campaigns) — show the exact change first.
- **Be economical with Adspirer** (15 calls/month). Reuse the digest for organic data; don't re-pull what you have.
- **Ten-Fifty5 repo** (`C:\dev\webhook-server`): only its `frontend/`, `marketing/`, blog + tag areas; commit with `CLAUDE_CODE=1`; never touch its product/DB code.
- **Never touch DNS.** **Never bulk-remove the dormant Wix scaffolding** (see the Ten-Fifty5 `docs/DE-WIX-DECOMMISSION.md`).
- **Concurrency:** another agent may be in these repos — commit with **explicit file paths**, never `git add -A`.

## Efficiency (short on time?)
Fast path = Phase 1 (health) → Phase 2 (SEO scorecard from the digest) → Phase 4 (Ads: CPA + scale decision) →
Phase 7 (action list). Skip 3/5/6 unless something looks off. The whole fast path is ~5 minutes.

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

## Phase 0 · PRE-FLIGHT (~1 min, do this FIRST — it saved nothing on 2026-08-30 because it did not exist)

**① WHICH LOGIN OWNS WHAT. Check this BEFORE asking Tomo to sign into anything.** On 2026-08-30
three separate tools were opened under the wrong account and each one looked like a broken or empty
system rather than a wrong door. The answers were already written down:

| System | Account that owns it | The trap |
|---|---|---|
| **Cloudflare DNS** (both zones) | **`tomo.stojakovic@gmail.com`** — recorded in `docs/specs/ENV-STATUS.md` §Domains | `info@ten-fifty5.com` and `info@nextpointtennis.com` BOTH have Cloudflare accounts with **zero zones**. Deliberate: a DNS account on a domain's own email locks you out when that domain's mail breaks. |
| **Google Business Profile** | `info@nextpointtennis.com` | Defaults to `info@ten-fifty5.com`, which correctly has no listing (SaaS, no premises) — reads as "0 businesses". Also: **GBP is now managed inside Google Search**, not Business Profile Manager, which shows 0 businesses even for the right account. |
| **Google Ads** | `info@nextpointtennis.com`, customer **704-275-3564** | Needs the Chrome extension granted permission for `ads.google.com` separately. |
| **Klaviyo** | two separate accounts — NextPoint `TckWKM`, Ten-Fifty5 its own | `get_account_details` FIRST, every time, before any write. |

**If a system looks empty, suspect the login before you suspect the system.** An empty Domains list,
"0 businesses", or a segment at zero is far more often the wrong account than a broken one. And
**grep the repo before asking Tomo** — `ENV-STATUS.md` and the `wix-decommission-2026-08` memory
between them hold the Cloudflare account, the nameserver pair, and why the split exists.

**② WHAT SHIPPED LAST WEEK.** `git log --oneline --since="8 days ago"` in BOTH repos, and skim the
last session's scorecard. Sessions otherwise start blind and re-propose work already done, or miss
that something shipped and needs measuring. Anything shipped last week should get a "did it move?"
line in this week's scorecard — a change nobody measures is a change nobody learns from.

## Tools

- **Adspirer** (Google Ads) — router `mcp__claude_ai_Adspirer__google_ads(action="execute", tool_name=…)`;
  key tools: `get_campaign_performance`, `get_campaign_targeting`, `analyze_wasted_spend`,
  `analyze_search_terms`, `update_bid_strategy`, `update_campaign` (budget), `add_negative_keywords`.
  **FREE plan = 15 calls/month, resets ~the 20th — be economical (batch reads).**
  **⚠ `get_tool_schema` and `search_tools` are NOT free — they bill like any other call** (the skill claimed
  they were until 2026-08-15, when a schema lookup ate the LAST remaining call and the approved negative-keyword
  write then failed outright). **Budget the writes FIRST.** If ≤2 calls remain, do not spend one on discovery —
  either call the tool with known-good arguments, or hand the user the Google Ads UI steps instead (a
  negative-keyword list pastes in manually in ~2 minutes and costs no quota at all).
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
- **Browser** — Ads console / GSC / GA4 if the Chrome extension is connected. **It is not "often flaky", it is
  reliably hostile on `ads.google.com` specifically** — expect these, they are not your fault and not worth
  fighting for more than two attempts each:
  - **The SPA freezes.** `Page.captureScreenshot timed out … renderer may be frozen` repeatedly. Wait 10-20s
    and retry; if three attempts fail, navigate away and back. Do not keep hammering.
  - **The viewport re-zooms between your screenshot and your click**, so coordinates go stale mid-batch. When a
    click "misses", this is usually why. **Prefer `find` → click by `ref`** over coordinates.
  - **Custom comboboxes and React inputs ignore typing.** `Ctrl+A` selects the whole PAGE if focus is not in a
    field — and on 2026-08-30 that pasted a blog post into the Google search box. Set values with the native
    setter instead: `Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set` then
    dispatch `input` + `change`. Works on Google Ads and Klaviyo alike.
  - **`fetch` to any `/api/…` with credentials or a query string is BLOCKED** by the extension guard
    ("Cookie/query string data"). The dashboard-API shortcut is not available; use the UI.
  - **Some dialogs are iframed** (GBP post editor) so top-document JS cannot reach them — plain clicks only.
  - **Editors are not always textareas.** Klaviyo's HTML box is **Ace** (`.ace_text-input` is a decoy); drive
    it with `ace.edit(el).setValue(html, -1)`.
- **Images: you CANNOT generate them — Tomo does, in ChatGPT.** Your job is the *conversion* half of the
  hero recipe (Pillow → WebP) on a picture that already exists in the library. Never promise a generated
  image, and never ship a post image-less in silence: if the library has no match, say so and ask for one.
  **Check first whether the hero is already wired** — on 2026-08-15 `parents.png` looked like an unused new
  infographic but was simply the source of the already-live `junior-analytics.webp` (same 1536x1024, converted
  two minutes later). Compare dimensions + mtimes before asking for anything.

**⚠ Two Windows measurement traps that fake findings on this box.** Both bit on 2026-08-15 and both produce
*false* punch-list items, which is worse than finding nothing:
- **`grep -P` fails** ("supports only unibyte and UTF-8 locales"), returning empty — which reads as "NO META
  DESCRIPTION" on a page that has one. **Parse HTML with Python, not `grep -P`.**
- **Console is cp1252**, so a UTF-8 em-dash prints as `â€"` and looks like mojibake in the live site. Set
  `PYTHONIOENCODING=utf-8` before concluding the source is broken.
Rule: **before reporting any technical defect, confirm the tool measured it correctly.**

## The routine — run for BOTH brands (skip a phase if its data is unchanged)

**Phase 1 · Measurement health (~1 min).** `curl` each site + its Render origin, grep for the tag (gtag loader
+ the GA4 id). Dark tag = **priority-1 alarm** (a blank tag ID in render.yaml re-clobbered by a blueprint sync —
see MARKETING-ENGINE.md §1; the fix is committing the real ID inline). This is the retired canary's job.
**Also check the APEX, not just `www`, and check the TLS EXPIRY, not just the status code** — on 2026-08-22
`ten-fifty5.com`'s bare apex had been serving an expired certificate for six days (every visitor got a
browser security warning) while `www` was perfectly healthy and every organic metric looked fine. It
surfaced only because `curl` on the apex failed outright. One line catches it:
`echo | openssl s_client -connect <host>:443 -servername <host> 2>/dev/null | openssl x509 -noout -dates`.
Run it on apex AND www for both brands; anything inside ~14 days of `notAfter` is the finding.

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

**Phase 4b · GEOGRAPHY — check the location targets are still all neighbourhood-scale (NextPoint).**
**⚠ FIRST DUE 2026-09-06.** On 2026-08-30 the Search campaign was targeting TEN locations: nine
neighbourhoods totalling ~198,000 reach (Houghton Estate, Lower Houghton, Parktown, Rosebank,
Hyde Park, Craighall, Northcliff, Blairgowrie, Sandton) **and Randburg as a CITY at 993,000** —
five times the entire rest combined, and the furthest away. Maximize Conversions spends where
conversions are cheapest and Randburg held ~83% of the inventory, which is the likeliest reason
59 of 82 conversions were cheap free-week signups rather than bookings. Randburg, Northcliff and
Blairgowrie were removed (10 → 7 targets).
**What to do:** pull Campaigns → Insights and reports → **Locations** for the 30 days BEFORE
2026-08-30 and confirm Randburg really was eating the budget — the cut was made on the reach
imbalance, NOT on performance data, and that gap was never closed. Then check the 14 days after:
impressions SHOULD be down 50-70% (that is the change working), but **watch average CPC** (was
R5.82) — with a much smaller pool Google still tries to spend the full R115/day by bidding harder.
**If CPC climbs, LOWER THE DAILY BUDGET; do not re-add Randburg.** A dip is the expected shape of
this change and reversing it mid-relearn gives the worst of both.
**The general rule:** a local club's targets should all be the same scale. One city-level entry
among neighbourhoods will quietly swallow the campaign, and reach is the tell — sort by it.

**Phase 5 · Content — website pages + GBP posts (~5–8 min).** Two complementary plays off the same
striking-distance queries. Aim to ship a website page AND its matching GBP post together.
- **⓪ BEFORE WRITING ANYTHING: does this query justify a NEW URL at all?** Tomo caught this on
  2026-08-30 and he was right. A "Tennis Cut vs SwingVision" post was proposed for a query worth
  **5 impressions in 28 days**, against two existing SwingVision pages already carrying 135 and 54
  impressions — one of them the 5th most-viewed page on the site. A third page splits a working
  cluster against itself for nothing.
  **The test: under roughly 20 impressions, REFRESH the page that already ranks. Do not create.**
  Look at what you already have for the topic first (`ls frontend/blog/_posts/` and read the
  TITLES, not the filenames), then ask whether the query's volume justifies a URL of its own.
  Refreshing wins twice — it keeps the ranking signal on one page, and an updated date is itself a
  freshness signal. A same-URL refresh is nearly always the higher-return move.
- **⓪b AFTER PUBLISHING OR REFRESHING: ask Google to index it. This is a real reach lever and it is
  free.** A brand-new URL sitting in the sitemap can wait days or weeks for discovery; a *refreshed*
  page can keep serving its old content in the index for just as long. In Search Console → **URL
  Inspection** → paste the URL → **Request indexing**. Usually crawled within hours. Do it for every
  page you touched, new or updated — publishing and then not telling Google is leaving the whole
  point of the work on the table. Note the sitemap does NOT do this for you; it only helps discovery
  eventually.
- **① Website blog / landing pages (organic — BOTH brands).** Propose 1–2 topics per brand from the 🎯
  queries (+ Ahrefs free Keyword Generator). Draft + publish via `build_blog.py`: create
  `frontend/blog/_posts/<slug>.md` (frontmatter `title/description/date/image:`; filename = the slug;
  description ≤ ~155 chars), optimize the hero to WebP ~16:9 with Pillow, run `python build_blog.py`, then
  commit the new `.md` + generated `.html` + `index.html` (explicit paths). **NextPoint → courtflow** (imgs
  `/img/`); **Ten-Fifty5 → the Ten-Fifty5 repo with `CLAUDE_CODE=1`** (imgs `/blog/images/`; it also has a weekly
  coworker SEO→post workflow — memory `ten-fifty5-weekly-seo-blog-workflow`). **A book/buy-intent query
  ("tennis lessons johannesburg", "book a court") wants a landing-style page with CTAs to `/book`
  `/free-lesson` — NOT a how-to article.**
  **📉 THE CADENCE GAP IS THE STANDING NEXTPOINT FINDING — check it every session.** As of 2026-08-25
  Ten-Fifty5 had **19 posts** and NextPoint **6** (was 13 vs 4 on 2026-08-15 - the gap is WIDENING), and their organic curves match that ratio: Ten-Fifty5
  users +30% / clicks +18% / position 8.9→8.4 in a week, NextPoint clicks −13% over the same stretch.
  Ten-Fifty5's blog is the engine pulling its growth; NextPoint's four posts are not enough to move
  anything. **Default to proposing NextPoint content, not Ten-Fifty5 content** — Ten-Fifty5 already has
  Cowork feeding it weekly, so this skill's marginal value is almost entirely on the NextPoint side.
  Count both `frontend/blog/_posts/` directories each run and report the ratio.
- **③ Cowork weekly output — RETIRED 2026-08-15. Do not go looking for it.** A scheduled Claude **Cowork**
  task used to write a researched blog + SEO report + infographic each week. **Tomo cancelled that routine**
  because it generated more work than it saved: it could see neither the repo (so it re-briefed published
  topics — twice) nor the GSC data (so it picked topics blind), and its SEO report fabricated its findings
  three runs out of three. **Ten-Fifty5 content is now written HERE, in Phase 5①, off the real
  striking-distance queries — same as NextPoint.** No output folder will exist; if none does, that is
  expected, not a failure.
  **The de-dupe discipline below still applies to anything you write yourself** — it is the general rule for
  this blog, not a Cowork-specific one. Keep reading it. Should a leftover draft ever surface: blog
  `.../local-agent-mode-sessions/.../outputs/blog-*.md` + `weekly-seo-report-*.md`; infographic in
  `C:\Users\tomos\OneDrive\Documentos\blog\`. When one exists:
  **BEFORE publishing, `ls frontend/blog/_posts/` and check for an existing post on the same topic.**
  **COMPARE TOPICS, NOT SLUGS — the slugs will not match.** On 2026-08-15 the draft
  `tennis-analytics-for-juniors-and-parents` was the same post as the live
  `tennis-analytics-for-junior-players` published **seven days earlier by this same weekly pipeline** —
  same audience, same thesis, ~75% the same argument, and no slug collision to warn you. Read the titles and
  descriptions of every existing post, not the filenames. **The pipeline re-briefs topics across weeks, so a
  duplicate is the DEFAULT expectation, not the edge case.** New topic →
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
    them** (2026-07-26, 2026-08-07 and **again 2026-08-15 — 6 of its 7 findings were false**). On 2026-08-15 it
    additionally invented a **`info@tenfifty5.com` email typo "appearing twice"** and labelled it *"Do this
    first — it is losing enquiries"* (the live page has 0 occurrences of the typo and 8 of the correct
    address); a **stale `/blog` index listing "only 7 articles" with "four orphaned posts"** (it links all 13);
    and a **`/blog` "Start Free" CTA pointing at a raw `wixstudio.com` portal** (it points at `/login`; 0
    wixstudio refs on the page). The ONE true finding was over-long meta descriptions on `/` and `/academies`.
    **Its confidence is inversely related to its accuracy — the more urgent the framing, the more likely it is
    invented.** Its recurring failure is asserting a **"Wix template vs custom
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

**Phase 6 · Reviews + the rest of the GBP — NextPoint only (~3 min). TREAT THIS AS A GROWTH LEVER, NOT A
HEALTH CHECK.** As of 2026-08-30: **5.0★ from 21 reviews, against a target of 40+** — so roughly half way,
with no reputation risk in asking. Review count is a **direct map-pack ranking factor**, and the map pack is
where `tennis courts near me` lives: **79 impressions, position 8.8, ZERO clicks** — the single biggest
striking-distance query NextPoint has, and one a blog post cannot fix. Reviews and GBP activity can.
- **Do something, don't just count.** The profile has an **"Ask for reviews"** button that generates a
  shareable link — put it in front of active members. The gated `/feedback` flow already routes 4–5★ to the
  GBP link; check it is actually being used, not just built.
- **The rest of the profile is barely touched.** Beyond Posts, the profile carries **Products, Services,
  Bookings, Photos and Q&A** — all of which feed the listing. **Q&A is the most under-used**: you may seed
  your own questions and answer them, and they surface in the map pack. Obvious ones for a club: is there
  parking, do I need to be a member, are there floodlights, can I hire a racket, is there coaching for
  beginners. Each is a query someone is typing.
- **GBP posts go stale in about a week**, which is why the cadence matters. One a week, rotating theme —
  free week · clay court · floodlit evenings · juniors/cardio · lessons. Pair each with whatever website
  page shipped that session.

**Phase 6b · Klaviyo / lifecycle email — BOTH brands (~5 min).** The MCP connector gives direct API access;
use it rather than the browser, which is how a previous session left things half-done and undetectable. Each
brand has its own Klaviyo account — **`get_account_details` FIRST and confirm which one you are in** before
any write. NextPoint = `TckWKM`, sender `info@nextpointtennis.com`. Both repos share the same
`marketing_crm/crm_sync/` package, so a fix to one is nearly always portable to the other — **port it in the
same session, per that repo's idiom, and say so in the report.**

- **① Flow health — the compounding asset, and the one people forget.** A campaign converts once; a flow
  runs for ever. `get_flows` and list every one with its status. **A flow in `draft` is work already paid for
  and switched off** — NextPoint had 5 of 7 in draft for six weeks, including the one built to convert court
  bookers into members. Report drafts as a finding every run until they ship or are deleted.
- **② The duplicate-metric trap. Check this before trusting any flow or segment.** `get_metrics` and look for
  two metrics with the SAME NAME from different integrations — an "API" one (your app) and one from
  "Klaviyo MCP Server" or similar (test events a previous agent fired). A flow or segment bound to the wrong
  copy **never fires and never errors**. Found live 2026-08-23: the "unconverted trial members" segment was
  keyed to the MCP-server `membership_started`, so its "never bought a membership" clause was true for
  everyone, including buyers — that send would have offered a joining discount to existing members.
- **③ Segment sanity, every run.** Pull `profile_count` for each segment a campaign targets. **A segment can
  quietly empty** — `B1 · Court bookers` read 0 because it required 2+ bookings from a subscribed profile and
  almost nobody was subscribed. Cross-check the numbers add up (A + B should equal the parent) and treat any
  segment at 0 as a blocker, not a curiosity. Note: a freshly created segment reports `profile_count: 0` with
  `is_processing: true` for a few minutes — that is "not computed yet", NOT an empty segment.
- **④ Consent ≠ subscription — the gap that makes everything else pointless.** Our DB's `marketing_opt_in`
  and Klaviyo's subscribed state are different facts, and only `subscribe_member()` (the consent endpoint)
  ever sets the second. Anyone opted in via import, admin edit or the signup default is invisible to Klaviyo.
  NextPoint: 455 opted in, ~40 reachable, until `scripts/backfill_klaviyo_subscribers.py` ran. `sync_all()`
  does NOT fix it (upserts profiles, never subscribes, ignores consent). Compare the two counts every run.
- **⑤ Revenue.** Klaviyo reads money from `$value` in MAJOR units only. Both repos now map it in
  `crm_sync/sync.py` from the shared `CONVERSION_MAP`. **If flow/campaign revenue reads zero while sales are
  happening, suspect the mapping, not the campaign** — and check the money event actually carries its amount
  (Ten-Fifty5's `credit_purchased` / `subscription_started` were count-only as of 2026-08-24).
- **⑥ Propose ONE campaign, from behaviour rather than a calendar.** Profile traits now carry
  `last_played_at`, `days_since_played`, `bookings_90d`, `has_upcoming_booking`, `lifetime_spend` — so a
  segment is one clause, not three stacked metric conditions. Good seams: members whose `current_period_end`
  falls in ~30 days; pack-holders with sessions left and no upcoming booking; clay-court regulars who have
  never taken a lesson; parents before a school term; top-decile `lifetime_spend` who are still PAYG.
- **⑦ Sending discipline, when a send is on the table.** Warm segment first, cold behind it — a first big
  send to stale addresses damages the reputation that also carries booking confirmations, receipts and
  invoices. Throttle a cold list (25%/hour). Define segments on a metric (`membership_started == 0`) rather
  than a frozen list so converts drop out automatically. **Send yourself a test and check it on a phone
  before arming anything**, and verify with `get_campaign` + `get_campaign_recipient_estimation` rather than
  trusting a `queued` response — `"Queued without Recipients"` is a normal transient for a scheduled send,
  but only the recipient estimate proves the audience is real.

- **⑧ AUTHENTICATION — check the sending domain is still verified, and READ THE DMARC REPORTS.**
  **⚠ FIRST DUE 2026-09-06, and every session after.** Set up 2026-08-30, when it turned out
  **neither brand had an authenticated Klaviyo sending domain**: campaigns left as
  `info@<brand>` but were signed on Klaviyo's SHARED infrastructure, and each apex SPF named
  only Google, so **DMARC alignment failed on every marketing send since Klaviyo went live**.
  `p=none` meant nothing was blocked and nothing errored — it just pushed mail toward Promotions
  and spam, invisibly, for six weeks. Now fixed: `email.nextpointtennis.com` (id 3399054,
  selectors `mtd1`/`mtd2`) and `email.ten-fifty5.com` (selectors `km1`/`km2`), both **static**
  routing, both **verified**, records committed to `migration/dns/<domain>.zone`.
  **What to do each run:**
  1. `get_sending_domains` per brand — status must still read **verified**. A domain that
     silently reverts is a domain sending unsigned again.
  2. **Read the daily DMARC aggregate reports** (Microsoft/Google, to `info@<brand>` via the
     `rua` tag). They are NOT noise and they are NOT a fault — they are the only evidence the
     fix held. Look for Klaviyo sends now **passing** DKIM+SPF alignment. Tomo had been
     ignoring them; that is the correct default only while nothing has changed.
  3. **A 97% "delivered" rate is NOT a 97% inbox rate.** Delivered means the receiving server
     accepted it — it can accept straight into spam. Never quote delivery as inbox placement.
  4. **Once a full week of reports shows clean alignment, raise `p=none` → `p=quarantine`** on
     both `_dmarc` TXT records. Do NOT do this before the evidence is in, and remember DNS is
     Tomo's to click — propose it, update the zone file, let him publish.
  5. If a campaign's opens look healthy but clicks are ~1%, suspect **placement before
     creative**. That was the 2026-08 Spring Day pattern (538 sends → 6 clicks → 0 memberships)
     and unauthenticated mail was the leading unexcluded cause.

**Phase 7 · Scorecard + action list.** Output a tight per-brand scorecard — Measurement / Organic / Technical /
Ads / Content / Email / Reviews each as 🟢🟡🔴 with a one-line reason — then a **prioritized "do this week" list
(top 3 per brand)** and a note of **what you auto-tuned this session**. That's the deliverable.
**Two additions that make the next session better than this one:** state **whether last week's changes moved
anything** (from Phase 0 ②) — an unmeasured change teaches nobody — and name **which item from "Where the
next 10x is" you looked at**, with what you found. Rotating through that list one item a week is what turns
a weekly health check into compounding growth.

## Where the next 10x is — the standing agenda (review one item per session, in this order)

Written 2026-08-30 after the deepest review the account has had. These are ordered by size of prize,
not by ease. **The first one is worth more than everything else in this skill combined**, and it is
not a marketing problem, which is exactly why a marketing routine kept missing it.

**① THE LOGIN WALL. Every campaign this skill runs points at a door that is locked.**
Every CTA — the Klaviyo emails, the GBP posts, the Google Ads landing paths — resolves to
`/portal#/plan` or `/portal#/play`, and **the portal requires a Clerk login**. So a stranger who
finds NextPoint through "tennis courts near me" cannot see a price, cannot see whether a court is
free at 18:00, and cannot book, until they have created an account. The ask is "sign up" before any
value has been shown.
Look at the evidence and it fits: Spring Day sent **538 emails, produced 6 clicks and 0
memberships**, with the money CTA pointing at that wall. Nine of ten Wix-era members have never
logged in. The `tennis courts near me` query has **79 impressions and zero clicks**.
**This caps the return on every single thing this skill does.** A public page showing real court
availability and prices — book first, account second, or at minimum *see* first — would lift email,
ads, GBP and organic simultaneously. Raise it every session until it is built or explicitly
rejected; nothing else here has the same multiple.

**② THE NUMBER NOBODY HAS: what fraction of free weeks become paying members.**
There were **207 trial grants in August**. Until that conversion rate is known: the Google Ads
values are guesses, CPA means nothing, "is the free week working?" is unanswerable, and nobody can
say what a signup is worth. It is one query against production. **Get it once, then track it
monthly** — it is the number that prices every other decision in this document.

**③ Content velocity is the one gap the data proves.**
Ten-Fifty5 **19 posts**, NextPoint **7** — and the organic curves match that ratio almost exactly.
This is the standing NextPoint finding and it is not subtle. One page a week, off the 🎯 queries,
compounds. Also check **internal linking** while you are in there: 19 and 7 posts are only a cluster
if they actually link to each other, and nobody has ever audited that. Interlinking existing pages
costs nothing and moves rankings.

**④ The offline-conversion loop works, and nobody is watching it.**
`Offline purchase` imported **4 conversions worth R2,200** — real money, ~R550 each, via the
gclid → paid order → CSV feed. It is the only truthful revenue signal in the Ads account. **If it
silently stops, nothing alarms**, ad bidding quietly degrades, and the first symptom is a CPA that
drifts for a month. Check the row is still non-zero every session. Two minutes.

**⑤ Draft flows are paid-for assets left switched off.**
A campaign converts once; a flow runs forever. NextPoint has had **5 of 7 flows in draft since
July**, including the one built to turn court bookers into members. Either ship them or delete
them — but a draft flow is work already done and earning nothing. Note the B1 segment is the
blocker on the best one: it requires **2+ bookings AND subscribed** and holds **3 people**. Loosen
to 1+ booking before switching anything on.

**⑥ Nobody has ever looked at who is beating us.**
For `tennis courts near me` (79 impr, pos 8.8) and `tennis lessons johannesburg`, we have never
once looked at who ranks above us and why. One SERP check per session: who holds the map pack, how
many reviews do they have, what does their landing page do that ours does not. Ahrefs free tier or
just look at the results. Cheapest competitive intelligence available and currently zero.

**⑦ Ideas worth trying that this skill has never attempted.** Pick one when the routine is clean:
- **A public availability/pricing page** (see ① — this is the big one wearing a small hat).
- **Seed the GBP Q&A** — you may ask and answer your own; they show in the map pack.
- **Ask the 21 reviewers for a photo.** Photo-bearing reviews weigh more and the profile is thin.
- **A landing page per intent**, not per topic — `book a court`, `tennis lessons`, `junior coaching`
  as CTA-first pages rather than articles. Buy-intent queries want a page that sells, not one that
  explains. The skill says this already and it has never been done.
- **Ten-Fifty5 has no GBP and no local surface at all** — correct today, but it also means 100% of
  its acquisition rides one blog. Worth asking whether a second channel should exist.

## Guardrails
- **Approve before ANY spend change** (bidding, budget, pausing, new campaigns) — show the exact change first.
- **Be economical with Adspirer** (15 calls/month). Reuse the digest for organic data; don't re-pull what you have.
- **Ten-Fifty5 repo** (`C:\dev\webhook-server`): only its `frontend/`, `marketing/`, blog + tag areas; commit with `CLAUDE_CODE=1`; never touch its product/DB code.
- **Never touch DNS.** **Never bulk-remove the dormant Wix scaffolding** (see the Ten-Fifty5 `docs/DE-WIX-DECOMMISSION.md`).
- **🔴 NEVER MARKET A FEATURE THAT IS SWITCHED OFF.** Before writing a post, a GBP update or an ad about any
  NextPoint capability, **check its flag in the code, not the roadmap.** Community / "Find a Game" ships DARK
  behind `club.policy.community_enabled` + `seat_rule_enforced`, **both defaulting `false`** — and per
  CLAUDE.md no write path has been exercised by a real second person. Driving searchers to a dark feature
  spends real attention on a dead end and burns the launch, which you only get once. Announce it the week it
  is ON, with the flags flipped and one real end-to-end run behind you.
- **🔴 NEVER PROMISE AN OFFER THE BILLING CODE WILL REFUSE.** Same failure as marketing a dark feature, but
  it reaches the desk as an argument with a customer quoting our own page. **The NextPoint 7-day trial is
  COURT-ONLY** (`provider='trial'`, auto-lapses to PAYG) — it does not cover lessons, classes, programmes or
  squads. On 2026-08-22 the live site promised a **free first lesson in ten places** across four posts and
  two CTA blocks, and there has never been such an offer. Two lessons from how that hid for so long:
  **grep the promise, not the phrase** (nine read "first lesson is free", the tenth read "your first one is
  free" and survived the sweep); and **a promise can be made by LAYOUT with no sentence to grep** — an
  eyebrow reading "Free for 7 days" sitting directly above "Book your first lesson." makes the claim just as
  loudly. Check the offer against the trial's actual scope before writing any copy, and re-read CTA blocks as
  a visitor sees them, not as a string search.
- **🔴 THE NEVILLE RULE — a credential is NAMED, never a plural.** Neville Godwin (2017 ATP Coach of the Year,
  coach to Alexei Popyrin) is a **partner in the High Performance Program** and the owner is happy for his
  credentials to be used — but **he takes no general lesson bookings.** So the line is not ATP-vs-no-ATP, it
  is **named vs plural**: "Neville Godwin, coach to Alexei Popyrin" attributes the claim to a person and is
  self-limiting; **"ATP-level coaches", plural and unattached, promises whoever takes your Tuesday lesson is
  one** — and none of the bookable coaches is. Keep ATP claims on the High Performance Program, on `/programs`
  and on his own bio (which states he is unavailable for general bookings); keep them off anything describing
  what a booker gets. Do **not** write a page that drives lesson demand at him — one was published and pulled
  the same day. `ATP-certified` is also wrong wherever it appears: Coach of the Year is an **award**, not a
  certification, and the real credential is stronger anyway.
- **🔵 WHAT WE BUILD FOR ONE BRAND, WE BUILD FOR BOTH.** Standing instruction from Tomo (2026-08-24): a
  capability is not finished when it works for NextPoint. Both repos carry the same
  `marketing_crm/crm_sync/` package and a byte-identical `offline_conversions/recorder.CONVERSION_MAP`,
  so most CRM work ports directly — **port it in the SAME session and report both**, adapted to each
  repo's idiom rather than copy-pasted (their `forward_event` signatures and money keys differ). Where a
  port genuinely does not apply, say why instead of leaving it silently undone.
- **Concurrency:** another agent may be in these repos — commit with **explicit file paths**, never `git add -A`.
  This is not hypothetical: on 2026-08-22 a second agent held uncommitted edits across five Ten-Fifty5
  marketing pages, and on 2026-08-23 pushed to `courtflow` mid-session (rebase, don't force). **Check
  `git status` before editing a shared file** — work you did not write is probably deliberate.

## Efficiency (short on time?)
Fast path = **Phase 0 ② (what shipped last week)** → Phase 1 (health) → Phase 2 (SEO scorecard from the
digest) → Phase 4 (Ads: CPA + scale decision) → Phase 7 (action list). Skip 3/5/6 unless something looks
off. The whole fast path is ~5 minutes.
**Even on the fast path, do two things.** Phase 0 ① (the account map) the moment any login is needed — it
costs seconds and prevents the wrong-account rabbit hole. And **one item from "Where the next 10x is"**,
in order: the routine keeps the machine running, that list is what makes it bigger. A session that only
runs the routine is a session that reports on the same ceiling.

# Marketing & Growth Engine — cross-brand (NextPoint + Ten-Fifty5)

**Status: LIVE (built 2026-07-18).** One config-driven engine measures, monitors and reports organic
growth across BOTH brands. Same DNA as the platform's multi-tenant model and the byte-identical shared
packages (`offline_conversions/`, `analytics/`).

## The repo model (where marketing work lives)
- **`NxtPoint/courtflow` (this repo) = the central marketing engine + NextPoint content.** The
  cross-brand **monitoring/reporting** (daily digest + canary) and the **keyless Google API access**
  (Workload Identity Federation) run from here and cover both brands. NextPoint's marketing site + blog
  live here (`frontend/`, `frontend/blog/`).
- **`NxtPoint/webhook-server` (the 1050 repo) = Ten-Fifty5's site + blog content.** Ten-Fifty5 blog posts
  are authored + built THERE (`frontend/blog/_posts/*.md` → `python build_blog.py`; commit with
  `CLAUDE_CODE=1`). The GA4 tag is injected by `locker_room_app._marketing_head` there.
- So: **engine + reporting = one place (courtflow); brand SITE content = that brand's repo.**

## 1. Measurement (both brands tagged)
The Google tag (GA4 + Ads) is env-gated and injected server-side. **GOTCHA (cost a week of dark
measurement, 2026-07):** the tag IDs must be committed **inline** in `render.yaml`, NEVER as blank
`value:""` — a blank committed value is re-clobbered to empty on every blueprint sync, silently darkening
the tag. Values are public (they're in page source), so commit them.
- **NextPoint** (`web_app._google_tag_head`, courtflow-web): GA4 `G-EKQP47P8M9` + Ads `AW-17077631191`
  + conversions (start_free_week, booking) + `attribution.js` (gclid) + `offline_conversions/`.
- **Ten-Fifty5** (`locker_room_app._marketing_head`, 1050 locker-room svc): GA4 `G-4167EPFS34`, **GA4-only**
  (no paid ads yet, by choice). Injected into every served `.html`.

## 2. Tag-breakage monitoring — the digest IS the monitor (canary RETIRED 2026-07-18)
A GitHub-Actions tag "canary" (`marketing-canary.yml`) was tried and **deleted**: both sites — *and* their
Render origins — sit behind Cloudflare, which blocks GitHub's CI IPs, so it could never reliably fetch the
live tag from Actions (only false-fails). **The daily digest (§3) is the real, reliable monitor:** if a tag
goes dark, that brand's GA4 traffic flatlines to zero in the morning email — a louder, more trustworthy
signal than the canary ever was. (If a fast automated tag check is ever wanted, run it from a non-datacenter
IP or add a Cloudflare WAF skip — don't reintroduce a GitHub-CI checker.)

## 3. Daily digest — the "one console" (`marketing_digest/`)
A daily GitHub Action (`.github/workflows/marketing-digest.yml`, 05:00 UTC) that pulls GA4 + Search Console
for both brands and emails **each brand its own report to its own inbox**:
- NextPoint → `info@nextpointtennis.com` · Ten-Fifty5 → `info@ten-fifty5.com`.
- Auto-discovers whatever GA4 property + GSC site the service account is granted (coverage = per-property
  grants, not code). Reports GA4 traffic/top-pages/channels (7d) + GSC clicks/impressions/top-queries and
  **🎯 striking-distance queries** (avg position 8–20, ranked by impressions = what to write/post next).
- Email delivery reuses the platform's own **SES** via `POST /api/cron/marketing-digest-email` (OPS-guarded,
  recipient-allowlisted). No AWS creds in GitHub; the workflow authorizes with the existing `OPS_KEY` secret.
- **The weekly loop:** open the email → pick one 🎯 striking-distance query → feed it (a GBP post, a page,
  or a blog post) → watch it climb next week.

## 4. Keyless Google API access (Workload Identity Federation)
The org policy `iam.disableServiceAccountKeyCreation` blocks downloadable SA keys, so the digest authenticates
**keyless** via WIF (GitHub OIDC → SA impersonation; nothing to leak).
- GCP project `marketing-engine-502809` (num `329900503340`); SA
  `marketing-engine@marketing-engine-502809.iam.gserviceaccount.com`; pool `github-pool` + provider
  `github-provider` (issuer `token.actions.githubusercontent.com`, condition `repo_owner==NxtPoint`), SA
  impersonation bound to `attribute.repository/NxtPoint/courtflow`.
- APIs on: analyticsadmin, analyticsdata, searchconsole, iamcredentials, sts. WIF provider (non-secret):
  `projects/329900503340/locations/global/workloadIdentityPools/github-pool/providers/github-provider`.
- Coverage is controlled by granting the SA **Viewer** on each GA4 property + **user** on each GSC property.

## 5. Content (SEO) — the blog systems
Both repos run the same `build_blog.py`: a Markdown post in `frontend/blog/_posts/<slug>.md` (frontmatter
`title/description/date/image:`) → `python build_blog.py` → SEO HTML (Article + BreadcrumbList JSON-LD, OG
card, canonical `/post/<slug>`, sitemap auto-include). Filename = slug; hero `image:` also becomes the OG
share card + index thumbnail — **a post without one has dead social/link previews**. NextPoint images at
`/img/`, Ten-Fifty5 at `/blog/images/`. Ten-Fifty5 has a WEEKLY coworker SEO-scan → blog workflow (see the
memory `ten-fifty5-weekly-seo-blog-workflow`).
- **The picture library is `C:\Users\tomos\OneDrive\Documentos\blog`** — where Tomo drops every hero /
  infographic, for BOTH brands. It is the ONLY picture source; `ls` it before publishing or refreshing and
  match by filename against the topic. Convert with Pillow at `quality=82, method=6` (a ~1.6MB PNG lands at
  ~130–150KB).
- **Do NOT crop an infographic to 16:9.** They are dense full-canvas designs with headers and footers, so a
  crop eats content — keep the source aspect (existing heroes are a mix of 1536x1024 and 1600x900, both
  in-convention). Crop to 16:9 only for photographs.

## 6. Google Business Profile (NextPoint only — physical club)
Ten-Fifty5 is a SaaS (no physical location → no GBP). NextPoint's GBP is optimized off the digest's #1
striking-distance query ("tennis courts near me"): primary category **Tennis court**, description, services,
posts, reviews, photos. Reviews + proximity + completeness win the local map pack. Ongoing: 1 post/week +
ask every happy player for a review.
- **Review engine (automated):** the platform's gated `/feedback` page (`marketing_crm/feedback/`,
  `KLAVIYO-MASTER-PLAN.md §4`) turns "ask every happy player" into a flow: a completed lesson emails the
  client a star rating; **4–5★ routes to the GBP review link** (`GOOGLE_REVIEW_URL` = `g.page/r/…`), 1–3★
  goes to a private form. A Google click-through fires a **GA4/Ads `review_click` conversion**, so review
  generation shows up in the digest/measurement alongside bookings.

## The operator — the `/marketing-manager` skill
On-demand deep-tune companion to this automated engine (`.claude/skills/marketing-manager/SKILL.md` — **version-
controlled since 2026-08-09**; `.gitignore` excludes `.claude/*` but re-includes `skills/`, so the operating
procedure is backed up and shared while `settings.local.json` stays private).
ONE command runs the full routine for BOTH brands — measurement health, organic/SEO scorecard (from the digest),
technical SEO crawl, Google Ads review+tune (Adspirer), content (website pages + GBP posts, **de-duping the weekly
Cowork output** against already-published slugs), reviews/GBP — and outputs a per-brand scorecard + prioritized
action list, executing the safe tunes (spend changes are approval-gated). Run it weekly (e.g. Saturday). Ten-Fifty5
also has an **autonomous weekly Claude Cowork task** (deep-research SEO scan + blog + infographic → outputs folder;
the skill publishes/de-dupes it — a duplicate topic REFRESHES the existing post, never a competing new page).

**⚠ Two things about the Cowork output that cost real time if you trust it (both seen 2026-08-08):**
- **It can write the SAME brief on different weeks.** Two handed-over folders held the same article under the
  same `slug: how-to-read-a-tennis-heatmap`. Two drafts ≠ two posts — **de-dupe the drafts against EACH OTHER,
  not just against published slugs**, and merge into ONE page (strongest draft as the base, graft in what the
  other uniquely adds). Identical slugs would silently overwrite at build time anyway.
- **Its weekly SEO report invents issues — VERIFY every claim against the live site before filing the work.**
  It recurrently asserts a **"Wix template vs custom template" split** for `/pricing` `/coaching` `/contact-us`
  and then derives a whole action list from that fiction (missing meta descriptions, a contact-email typo, a
  raw `wixstudio.com` CTA, orphaned posts, absent JSON-LD). **On 2026-08-07 every single item was false** — one
  Render stack, meta description + og:image + schema on all pages, all 11 posts linked from `/blog`. Same false
  positive as 2026-07-26 (a grep matching a Wix *link*, not a page origin). One `curl` per claim settles it.
  Its **competitor scan and topic ideas remain useful** — those don't rest on the broken site analysis.

## Current state (2026-08-09) — the machine, complete ✅
- ✅ Both brands **measured** + **digest emailing daily** (+ dashboard-ingested); NextPoint **GBP live**; content
  engines running (blog + GBP posts + Ten-Fifty5 weekly Cowork + the tennis-reel skill).
- ✅ **NextPoint Google Ads TUNED:** bidding = **Maximize Conversions**; budget **R115/day** (raised from R90
  on 2026-08-08 — it was spending ~R85/day against a R90 cap while CPA improved); 31 negatives (incl. `popyrin`,
  and `padel` phrase + `tennis`/`tennis court` **exact-only**); Cyborg PMax stays paused. **Judge by CPA, NOT
  the ROAS** (conversion values are R1 placeholders except the offline loop). Ten-Fifty5 = **organic-only by choice**.
  - **Two different CPAs — do not conflate them.** *Web-conversion* CPA (free-week signups / booking starts)
    was **R45 over 30 days**, having fallen from **R99 → R30** week-on-week as Maximize Conversions exited its
    learning phase. *Cost-per-MEMBER* — the figure that actually matters, from the offline loop — is the older
    **~R76** and is necessarily higher, since not every signup becomes a paying member. Judge a scale-up against
    R45, expecting some drift up as extra budget buys less-qualified traffic at the margin.
  - **⚠ Do NOT bulk-apply Adspirer's negative-keyword list.** It flags any term with high cost and zero
    conversions, which at this volume means 1–5 clicks of noise. On 2026-08-08 it recommended blocking
    `tennis lessons johannesburg`, `tennis clubs in johannesburg`, `tennis coach johannesburg` — core money
    queries — and `next point`, the brand itself. Worse, `tennis` and `tennis court` as broad/phrase negatives
    would block every search containing those words and switch the campaign off. Vet by hand; exact-match only
    for single generic words.
- ✅ **Offline-conversion loop LIVE + importing** (`offline_conversions/`): gclid capture (`attribution.js`) →
  `core.offline_conversion` → the password-gated CSV feed `/feeds/google-ads/offline-conversions.csv`
  (`GOOGLE_ADS_FEED_USER`/`PASS`) → Google Ads' **daily scheduled upload** → the **"Offline purchase"** conversion
  action (`UPLOAD_CLICKS`, ENABLED, real member value). Already matched **2 ad-clickers → paying members (R360,
  95.7% of all conversion value)** — teaching bidding to chase real members, not clickers. *(Only keep ONE scheduled
  upload in the Ads UI — a duplicate is harmless, Google dedupes, but tidy to one.)*
- ✅ **Content shipped 2026-07-26:** NextPoint (`book-a-tennis-court-in-killarney-johannesburg`,
  `tennis-lessons-in-johannesburg` + matching GBP posts); Ten-Fifty5 (`how-to-reduce-unforced-errors-in-tennis-using-data`,
  refreshed `tennis-return-of-serve-analysis` with a branded infographic). SEO polish: trimmed meta/titles, blog-index schema.
- ✅ **Content shipped 2026-08-08/09:** NextPoint (`tennis-courts-in-johannesburg` — a city-level landing page
  for the head terms `tennis courts johannesburg` / `tennis club johannesburg`, pos ~10–11 with no dedicated
  page until then; + a matching GBP post drafted). Ten-Fifty5 now at **13 posts**: new
  `how-to-read-a-tennis-heatmap` (the two duplicate Cowork drafts merged into one) and
  `tennis-analytics-for-junior-players` (the tennis-PARENT audience — unclaimed by every competitor, and the
  blog's first non-player reader); refreshed `the-complete-guide-to-tennis-rally-analysis` onto definition
  intent (it ranked pos 8.5–15.7 for `tennis rally meaning` with **zero clicks** — the title promised tactical
  analysis, the searcher wanted a definition) and `how-to-film-your-tennis-match-for-analysis` to absorb
  `swingvision stick alternative` rather than give it a competing page. Hero infographics added to all three
  Ten-Fifty5 posts. **All refreshes kept their canonicals — no new URLs.**
- **Optional next levers (not urgent):** set real conversion **VALUES** on the other actions → value-based /
  Target-ROAS bidding; **Customer Match** (exclude existing members from ad spend + seed lookalikes).
- Ten-Fifty5 keeps **dormant DB-coupled Wix scaffolding** — a staged decommission is scoped in the 1050 repo's
  `docs/DE-WIX-DECOMMISSION.md` (**DO NOT rush**). Coworker Ahrefs **free DR endpoint needs a free API key — the
  2026-08-07 report puts the cutoff at 2026-08-10**, so this is overdue-imminent; Domain Rating (11/100) is the
  only live figure in that weekly report and stops flowing without it.

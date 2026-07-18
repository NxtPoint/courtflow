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

## 2. Canary — the tripwire (`.github/workflows/marketing-canary.yml`)
Scheduled GitHub Action (every 2h) that asserts each brand's tag is present on the live site. Matrix per
brand; `enforce` fails the run, `warn` only warns. **Both public hosts are behind Cloudflare, which serves
GitHub's CI IPs a bot-challenge (200, no tag) → false-fail.** Fix: the canary checks the **Render origins**
(`courtflow-web.onrender.com`, `locker-room-26kd.onrender.com`) which bypass the club Cloudflare zone; plus
`--compressed`, a browser UA, and 4×20s retries to outlast a redeploy. A red canary = a real regression.

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
share card + index thumbnail. Optimize images to WebP (Pillow), ~16:9. NextPoint images at `/img/`,
Ten-Fifty5 at `/blog/images/`. Ten-Fifty5 has a WEEKLY coworker SEO-scan → blog workflow (see the memory
`ten-fifty5-weekly-seo-blog-workflow`).

## 6. Google Business Profile (NextPoint only — physical club)
Ten-Fifty5 is a SaaS (no physical location → no GBP). NextPoint's GBP is optimized off the digest's #1
striking-distance query ("tennis courts near me"): primary category **Tennis court**, description, services,
posts, reviews, photos. Reviews + proximity + completeness win the local map pack. Ongoing: 1 post/week +
ask every happy player for a review.

## Current state & open items
- ✅ Both brands measured + guarded; digest emailing daily; NextPoint GBP done; content pipelines live.
- **NextPoint Google Ads**: live but needs tuning (switch bidding Max-Clicks → Max-Conversions after
  ~15–30 conversions accrue with the now-working tag; keep Cyborg PMax paused). Best done via **Adspirer**
  (free plan resets ~20th) or the console. Ten-Fifty5 has **no paid ads by choice** (not ready for scale).
- **Phase 2 (optional):** Google Ads API (needs a developer-token approval) to fold ad spend/conversions
  into the digest; GA4↔Search Console link for Ten-Fifty5 (Admin → Product links); email/Slack niceties.
- The coworker's Ahrefs **free Domain-Rating endpoint needs a free API key before 1 Aug 2026**.

# 07 — Marketing Site & SEO-Preserving Wix → Render Migration

> Tomo: *"we are already well ranked on Google and don't want to lose that … assume we might have to
> reindex etc."* Unlike 1050 (which migrated off Wix from DR 0), **NextPoint has real rankings to
> protect.** This is a careful, reversible cutover — and we've done the Wix→Render move once already
> (see 1050's `_archive/wix-migration-record.md`), so we reuse that playbook.

## 1. Reuse the 1050 marketing engine

Port 1050's static‑site toolkit into `courtflow-web`, made **per‑club themed**:
- Host‑switched serving (`_is_marketing_host()` → club by host), native HTML (no JS‑rendered content).
- Shared nav/footer, club palette from `club.branding`, Inter, WCAG‑AA, WebP, `:focus-visible`.
- `build_blog.py` static blog generator (Article + BreadcrumbList JSON‑LD, OG cards).
- Generated `robots.txt` + `sitemap.xml` (auto‑includes every route + post), branded `404.html`.
- JSON‑LD: **`SportsActivityLocation` / `LocalBusiness`** for NextPoint (address, geo, hours, phone),
  `Service`/`Offer` for each booking service, `FAQPage`, `BreadcrumbList`.

## 2. Step 0 — capture the current SEO footprint BEFORE touching anything

This is the make‑or‑break step for not losing rankings. Build a **complete inventory** of what
currently ranks and every live URL:

- **Pull GSC data** (Google Search Console is connected via MCP): top pages by clicks/impressions,
  top queries, and their landing URLs over the last 12 months. These are the URLs whose paths/redirects
  we must preserve.
- **Pull Ahrefs** (connected): `site-explorer` top pages, organic keywords, and **backlinks**
  (referring domains + their exact target URLs) — every backlinked URL must 301 to its new equivalent
  to keep link equity.
- **Full crawl of `nextpointtennis.com`** (Chrome/Screaming‑Frog‑style) to enumerate every Wix URL,
  title, meta description, H1, and canonical.

Output: `migration/url_inventory.csv` (old_url, title, type, clicks_12mo, top_query, backlinks) →
becomes the redirect map source of truth.

> The current Wix URLs are messy (e.g. `/copy-of-booking-services`,
> `/booking-calendar/hard-court-member-bookings`, `/service-page/hard-court-visitor-bookings`,
> `/high-performance-program`, `/coaching-team`, `/jobs`, `/solutions`,
> `/booking-calendar/complimentary-lesson-1`). We design **clean new URLs** and **301 every old one**.

## 3. Proposed new URL structure (clean, keyword‑aligned)

| Old (Wix) | New (Render) | Notes |
|---|---|---|
| `/` | `/` | Home — keep as the strongest page |
| `/solutions`, `/copy-2-of-offering` | `/services`, `/contact` | consolidate |
| `/copy-of-booking-services`, `/booking-calendar/hard-court-*`, `/service-page/hard-court-*` | `/book/court` | court booking hub |
| `/copy-of-high-performance-program-1` (Book a Lesson) | `/book/lesson` | lesson booking |
| `/copy-of-book-a-lesson` (Book a Class) | `/book/class` | classes |
| `/high-performance-program` | `/programs/high-performance` | keep keyword |
| `/coaching-team` | `/coaches` | coach profiles (Neville, Ross, team) |
| `/booking-calendar/complimentary-lesson-1` | `/free-lesson` | lead funnel |
| `/jobs` | `/careers` | vacancies |
| `/booking-calendar/junior-*`, `/service-page/junior-*` | `/programs/juniors` | junior squads |
| social events | `/programs/social` | Saturday/Wednesday social |

Keep the **highest‑traffic page paths as close to original as data allows** — if GSC shows a Wix URL
pulling real traffic, consider preserving that exact path instead of "cleaning" it. Rankings > tidiness.

## 4. The cutover (reversible at each step — 1050's proven sequence)

1. **Build & stage** the new site on the Render service (on a temp host / onrender URL). Verify every
   page renders natively, titles/metas/canonicals/JSON‑LD correct, sitemap + robots good.
2. **Implement 301 redirects** for the full `url_inventory.csv` map (old Wix path → new path) in
   `courtflow-web` (host‑aware). Self‑canonicals on every new page. No redirect chains.
3. **Lower DNS TTL** on `nextpointtennis.com` a day ahead.
4. **Cutover DNS**: point `www` + apex at the Render service. **Rollback = point DNS back to Wix.**
   ⚠️ Mind the `api.nextpointtennis.com` record used by 1050 — don't touch that record (see `01` §6).
5. **Search Console**: submit the new `sitemap.xml`, use **URL Inspection → Request Indexing** on the
   top 20–30 pages, monitor Coverage for 404s/redirect errors. Do the same in Bing Webmaster.
6. **Watch** GSC daily for 2–4 weeks: impressions/clicks/position per top query, crawl errors, and
   that 301s resolve 1:1. Keep Wix subscription alive a couple of weeks as the rollback path.

## 5. Don't‑lose‑rankings checklist

- [ ] 301 (not 302) every old URL with traffic or backlinks → exact new equivalent.
- [ ] Preserve/improve title tags + meta descriptions; keep primary keywords ("tennis Houghton/
      Johannesburg", "clay court Gauteng", "tennis lessons Johannesburg", "high performance tennis").
- [ ] `LocalBusiness`/`SportsActivityLocation` schema with NAP (name/address/phone) **identical** to
      Google Business Profile — local SEO is huge for a club.
- [ ] Claim/verify **Google Business Profile** points to the new site.
- [ ] Keep H1s and on‑page copy at least as rich as Wix (Wix buried text — we can only improve).
- [ ] `sitemap.xml` submitted; `robots.txt` allows crawl; no accidental `noindex`.
- [ ] Image alt text; fast LCP (WebP, no JS‑blocking) — Core Web Vitals are a ranking factor and Wix
      was slow, so this is a likely *gain*.
- [ ] Internal linking: every service page links to its booking flow and to /coaches.
- [ ] After cutover, re‑pull Ahrefs/GSC weekly; fix any URL that dropped.

## 6. Content carry‑over

Bring across (rewritten as native, content‑first HTML): home, services, coaches (with Neville Godwin
& Ross Nemeth bios), high‑performance program, juniors, socials, free‑lesson, contact, careers,
testimonials. Stand up the blog generator so NextPoint can publish (good for long‑tail local SEO:
"how to book a clay court in Johannesburg", "best junior tennis program Houghton", etc.).

## 7. Tooling we already have connected (use it)

- **Google Search Console MCP** — pull pages/queries/positions for the inventory + monitor post‑cutover.
- **Ahrefs MCP** — top pages, keywords, backlinks (for the redirect map + equity preservation).
- **SearchFit‑SEO / SEO skills** (installed) — run a technical SEO audit + schema generation on the
  new pages before cutover.
- **Chrome MCP** — crawl the live Wix site to enumerate URLs.

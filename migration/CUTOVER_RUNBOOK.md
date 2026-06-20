# Wix → Render SEO Cutover Runbook (NextPoint Tennis)

> Reversible at every step. NextPoint has **real Google rankings to protect** — this
> is a careful cutover, not a flip. Adapted from 1050's proven Wix→Render playbook
> (docs/07). **An agent must NEVER change DNS** — every DNS step below is done by Tomo.

## Roles
- **Agent F** built: the new site (`web_app.py` / `frontend/marketing/`), the blog,
  `robots.txt` / `sitemap.xml`, the redirect engine (`migration/redirects.py`) and
  this runbook + the `url_inventory.csv` / `redirects.csv` maps.
- **Tomo (supervised, manual):** DNS, Search Console, Google Business Profile, the
  go/no-go decision, and rollback.

---

## Step 0 — Capture the current SEO footprint (BEFORE touching anything)
This is the make-or-break step. Build a complete inventory of what ranks + every live URL.

- [ ] **GSC (MCP connected):** export top pages by clicks/impressions, top queries, and
      their landing URLs over the last 12 months → fill `url_inventory.csv`
      (`clicks_12mo`, `impressions_12mo`, `top_query`).
- [ ] **Ahrefs (MCP connected):** Site Explorer top pages, organic keywords, and
      **backlinks** (referring domains + exact target URLs) → fill `backlinks` and flag
      any backlinked URL (every one must 301 to keep link equity).
- [ ] **Full crawl of nextpointtennis.com** (Chrome MCP / Screaming-Frog style):
      enumerate every Wix URL, title, meta description, H1, canonical → fill
      `old_url`, `title`, `type`.
- [ ] Decide, per row, `redirect_status`: `301` (default), `keep` (preserve the exact
      path because it pulls real traffic — rankings > tidiness), or `drop` (dead → 410).

Output: a complete `migration/url_inventory.csv`. Curate `migration/redirects.csv` from it.

## Step 1 — Build & stage the new site
- [x] New site renders natively (no Wix JS) — `web_app.py` + `frontend/marketing/`.
- [x] Titles, meta descriptions, canonicals, JSON-LD (`SportsActivityLocation`,
      `Service`/`Offer`, `FAQPage`, `BreadcrumbList`) present on every page.
- [x] `sitemap.xml` + `robots.txt` generated (host-aware) and correct.
- [ ] Stage on the Render temp/onrender host. Walk every new page. Run the SEO skills
      (SearchFit-SEO technical audit + schema check) over the staged pages.
- [ ] Confirm Core Web Vitals are good (WebP, no JS-blocking) — likely a *gain* over Wix.

## Step 2 — Implement 301 redirects
- [ ] Finalise `migration/redirects.csv` (old Wix path → final new path; **no chains**).
- [ ] Wire the engine: in `web_app.py` add `from migration.redirects import
      register_redirects; register_redirects(app)` (before the catch-all 404).
- [ ] Verify each row resolves 1:1 with a real `301` (use the verify checklist below).
- [ ] Confirm self-canonicals on every new page point to the clean new URL.

## Step 3 — Lower DNS TTL  *(Tomo)*
- [ ] A day ahead, lower the TTL on `nextpointtennis.com` (`www` + apex) so the cutover
      and any rollback propagate fast.

## Step 4 — Cutover DNS  *(Tomo — supervised)*
- [ ] Point `www` + apex at the Render `courtflow-web` service (custom domain attached
      in the Render dashboard).
- [ ] ⚠️ **Do NOT touch `api.nextpointtennis.com`** — that record is LIVE on the 1050
      service (docs/01 §6, render.yaml header). Leave it untouched.
- [ ] **Rollback = point DNS back to Wix.** Keep the Wix subscription alive for ~2 weeks.

## Step 5 — Search Console + Bing  *(Tomo)*
- [ ] Submit the new `sitemap.xml` in GSC and Bing Webmaster.
- [ ] URL Inspection → **Request Indexing** on the top 20–30 pages.
- [ ] Monitor Coverage for 404s / redirect errors.
- [ ] Confirm **Google Business Profile** points to the new site; NAP matches the
      `SportsActivityLocation` schema EXACTLY (name / address / phone).

## Step 6 — Watch (2–4 weeks)  *(Tomo, with MCP)*
- [ ] Daily: GSC impressions / clicks / position per top query; crawl errors; that
      301s resolve 1:1.
- [ ] Weekly: re-pull Ahrefs + GSC; fix any URL that dropped.
- [ ] Only after rankings hold: stand down the Wix rollback path.

---

## Don't-lose-rankings checklist (docs/07 §5)
- [ ] 301 (not 302) every old URL with traffic or backlinks → exact new equivalent.
- [ ] Preserve/improve titles + metas; keep primary keywords ("tennis Houghton/
      Johannesburg", "clay court Gauteng", "tennis lessons Johannesburg",
      "high performance tennis").
- [ ] `SportsActivityLocation` schema NAP identical to Google Business Profile.
- [ ] H1s + on-page copy at least as rich as Wix (Wix buried text — we improve it).
- [ ] `sitemap.xml` submitted; `robots.txt` allows crawl; no accidental `noindex`.
- [ ] Image alt text; fast LCP (WebP, no JS-blocking).
- [ ] Internal linking: every service page links to its booking flow and to /coaches.

## Verify a redirect resolves 1:1 (no chain)
```
curl -sI -H "Host: www.nextpointtennis.com" https://<render-host>/coaching-team
# Expect: HTTP/1.1 301 ... Location: /coaches   (single hop to the FINAL path)
```
Run `python -m migration.redirects` to print the full loaded rule table.
```

## Rollback (any step)
DNS back to Wix (Tomo). Because the new site is additive and DNS-switched, rollback is
a single DNS change — no data migration to undo.

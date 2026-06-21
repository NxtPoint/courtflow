# Public Site Redesign — Build Spec (for Claude Code)

> **Purpose.** This folder is the authoritative spec for rebuilding the NextPoint **public marketing
> site** — the part of `courtflow-web` that lives at `frontend/marketing/` + `frontend/_shared/`.
> It does **not** change the portal/app (`frontend/app/`), the booking engine, billing, or auth.
> Read every file here before writing code.

## ⭐ Approved visual reference (build to match this)
**`docs/public-site/prototype-home.html`** is the owner-approved homepage design (v2). It is the
**visual source of truth** — match its aesthetic, layout, motion and components. Lift its CSS into
`frontend/_shared/theme.css` (as the new `cf-*` components) and its markup into
`frontend/marketing/home.html`. It references the optimized assets in `frontend/img/` (Unsplash stock
for atmosphere with `onerror` fallbacks to real club photos).

**Locked decisions:** premium/immersive, stock-forward photography (v2). Lean 6-page site. 7-day free
trial is the hero hook. Ten-Fifty5 = **homepage band + a Programs mention only** (link out to
ten-fifty5.com); no dedicated page / no "Tennis Lab" rebrand for now (`06`).

## The brief in one line
**SIMPLE · FAST · BEAUTIFUL · LESS IS MORE.** A photo-rich, conversion-focused public site whose job
is to make a stranger sign up. All services are described on the **homepage** with clear CTAs that send
people into the **member portal** (login/signup) where they actually book. We are moving from a
"sell-from-the-front" Wix site (clunky, many pages, one-click-buy plumbing) to a **SaaS model**: the
front is a beautiful shop window; the value lives behind a free sign-up.

## What changes vs. what stays
- **Stays (do not break):** the design-system contract in `frontend/_shared/theme.css` (the `cf-*`
  classes + CSS custom properties — keep names stable, it's a cross-lane contract with the portal),
  the host-switch + chrome-injection mechanism (`web_app.py`, `frontend/_shared/chrome.py`,
  `branding.py`), absolute asset links, the blog generator (`build_blog.py`), and the SEO/redirect
  discipline in `docs/07`.
- **Changes:** the page set shrinks to a **lean ~6 pages** (see `03-sitemap-nav-footer.md`); the
  homepage becomes the hero of the whole site; **real NextPoint photography replaces stock**; the
  coaches page gets **full founder bios + a coach carousel**; nav/footer CTAs drive to **sign-up / the
  portal**, not to per-service "buy" pages; a **7-day free trial** is the headline hook.

## Files in this spec
| File | What it covers |
|---|---|
| `01-strategy-and-principles.md` | Goals, audience, the "behind the curtain" model, the free-trial hook, design principles, best-practice benchmarks. |
| `02-design-system-and-hero.md` | How to reuse/extend `theme.css`, the new components to add (hero, gallery/lightbox, coach carousel, testimonial slider, portal showcase), and the **signature hero** treatment (splash image + optional progressive 3D/motion). |
| `03-sitemap-nav-footer.md` | The lean sitemap, URL map, new nav, new footer, and what to retire/redirect. |
| `04-page-specs.md` | Section-by-section spec for every page: layout, copy, CTAs, links, SEO meta + JSON-LD. |
| `05-assets-and-copy.md` | Image manifest (real photos → where they're used), founder/coach bios, finalized copy blocks, and the image pipeline. |

## Optimized assets are already prepared
A curated, web-optimized image set has been generated into **`frontend/img/`** (WebP, sized, with a
`manifest.json`). You can reference these paths directly — see `05-assets-and-copy.md §1`. Source files
live in the owner's `marketing material` folder (OneDrive) and are listed in the manifest for
reference; do not depend on that folder at build time.

## Build order (suggested)
1. **Extend `theme.css`** with the new components in `02` (additive only — never rename existing
   tokens/classes). Update `frontend/app/styleguide.html` if it documents marketing components.
2. **Update chrome** (`frontend/_shared/chrome.py`): new nav links + footer per `03`.
3. **Rebuild `home.html`** per `04 §1` — this is the priority; it carries ~70% of the value.
4. **Rebuild `coaches.html`** per `04 §2` (founder bios + carousel).
5. **Build `programs.html`** (combined) per `04 §3`; **rebuild `pricing.html`**, `contact.html`.
6. **Retire** the extra pages and add 301s (`03 §4`).
7. **SEO pass**: titles/metas/canonicals/JSON-LD, `sitemap.xml`, `robots.txt`, image `alt`, LCP.
8. **Verify** against the acceptance gates below.

## Acceptance gates (done-when)
- [ ] Homepage describes **all** services (courts, coaching, programs/classes, the clay USP) and every
      service block has a CTA into the portal (`/login` signup) — no dead "buy" buttons.
- [ ] The **7-day free trial** is the primary hero CTA and is repeated at least once lower on the page.
      "Start your free week" links to **`/login#/sign-up`**; "Sign in" links to **`/login`**.
- [ ] Photography is **premium and immersive**: polished stock leads atmospheric/hero/feature sections,
      real NextPoint photos accent founders + gallery (stock-forward, like the Wix look — see `01`
      principle 2). The design feels expensive, not templatey. All images WebP/optimized, sized,
      lazy-loaded below the fold, with descriptive `alt`.
- [ ] The **Ten-Fifty5** AI-analysis cross-sell band is on the homepage (dark/premium, links out to
      ten-fifty5.com) — see `06-ten-fifty5-crosssell.md`.
- [ ] Coaches page: Neville Godwin + Ross Nemeth full bios at top; coach **carousel** below, populated
      from the manifest and trivially extendable.
- [ ] Public nav is the lean set; retired URLs 301 to a live page (no chains, no 404s in the sitemap).
- [ ] **Lighthouse mobile ≥ 90** Performance & ≥ 95 Accessibility/SEO on home + coaches; **LCP < 2.5s**
      on a throttled mobile profile; hero image is `fetchpriority="high"` and preloaded.
- [ ] `prefers-reduced-motion` is respected; any 3D/motion has a static image fallback and is not on the
      LCP path.
- [ ] Web service tests still pass (host-switch, chrome injection, robots/sitemap, branded 404).
- [ ] Nothing in `frontend/app/`, `diary/`, `billing/`, `auth/` changed by this work.

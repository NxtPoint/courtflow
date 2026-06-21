# BUILD PROMPT — Public Site Redesign (paste into Claude Code at repo root)

```
You are rebuilding the NextPoint PUBLIC MARKETING SITE only (the courtflow-web marketing face:
frontend/marketing/ + frontend/_shared/ + the marketing routes in web_app.py). Do NOT touch the
portal/app (frontend/app/), the booking/diary/billing/auth backends, or DNS.

READ FIRST, in full: docs/public-site/README.md, then 01..06 in that folder. The APPROVED VISUAL
SOURCE OF TRUTH is docs/public-site/prototype-home.html (v2) — build the real pages to match it
(lift its CSS into theme.css as cf-* components, its markup into home.html). They are the authoritative
spec. The brief is: SIMPLE, FAST, BEAUTIFUL, LESS IS MORE — a photo-rich site whose job is to convert a
stranger into a free sign-up. Services are described on the homepage with CTAs into the member portal
(/login#/sign-up); the portal owns all booking/payments.

Hard rules:
- Reuse + EXTEND the design system in frontend/_shared/theme.css. Never rename/remove existing cf-*
  classes or --tokens (cross-lane contract with the portal). Add new components additively (02 §2).
- Use the optimized images already in frontend/img/ (see manifest.json + 05 §1). Real club photos only
  on the homepage — no stock.
- Keep the host-switch + apply_chrome injection + absolute asset links + blog generator intact.
- Preserve SEO: clean URLs, titles/metas/canonicals/JSON-LD, sitemap.xml, robots.txt, 301 every retired
  URL (03 §4-§5). Rankings must not drop.
- "Start your free week" -> /login#/sign-up ; "Sign in" -> /login.

Build order:
1. Extend theme.css with the new components (02 §2) + the cinematic hero (02 §3, ship Tier 1 + optional
   Tier 2 motion; Tier 3 WebGL only if it never regresses LCP).
2. Update frontend/_shared/chrome.py: new nav links + CTA + footer (03 §2-§3).
3. Rebuild frontend/marketing/home.html (04 §1) — PRIORITY.
4. Rebuild coaches.html (04 §2: founder bios from 05 §2 + coach carousel).
5. Build programs.html (combined, anchored — 04 §3); rebuild pricing.html (04 §4), contact.html (04 §5).
6. Reconcile web_app.py routes + _MARKETING_URLS and add 301s (03 §5). Retire services/free-lesson/
   per-program pages. Keep careers minimal (footer-only).
7. SEO + perf pass; regenerate sitemap; branded 404.

Done when the acceptance gates in README.md pass (incl. Lighthouse mobile >=90 perf, LCP < 2.5s,
prefers-reduced-motion respected, no portal/backend files changed, web-service tests still green).

Verify with: python -m py_compile $(git ls-files '*.py') ; the web_app.py Flask test-client checks
(host-switch, chrome injection, robots/sitemap, branded 404); and a manual Lighthouse run on / and
/coaches. Report done-vs-pending against the acceptance gates at the end.
```

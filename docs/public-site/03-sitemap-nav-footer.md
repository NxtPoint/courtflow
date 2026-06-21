# 03 — Sitemap, Navigation & Footer

## 1. The lean sitemap (6 public pages + utilities)
Down from ~11. Everything that was a thin page becomes a **section** of a richer page or moves into the
portal.

| Page | URL | Job |
|---|---|---|
| **Home** | `/` | The whole story: hero + free-trial hook + all services + clay USP + portal showcase + founders preview + testimonials + gallery + final CTA. Carries ~70% of conversions. |
| **Coaches** | `/coaches` | Founder bios (Neville, Ross) + coach carousel. The credibility page. |
| **Programs** | `/programs` | One combined page with anchored sections: High Performance, Junior squads, Cardio Tennis, Social play. |
| **Pricing** | `/pricing` | Transparent pricing: pay-as-you-go, membership, the free week. Court/lesson/class rates. |
| **Contact** | `/contact` | Location + map + hours + NAP + short form. |
| **Blog** | `/blog` | SEO long-tail (generator already exists — keep). |

**Utilities (not in primary nav):** `/login` (portal sign-in/up — the conversion target), `/careers`
(minimal page, footer link only — vacancies come and go), `404.html` (branded).

### What gets retired (and where it goes)
| Retired page | Folds into |
|---|---|
| `services.html` | Home "Services" section + `/pricing` |
| `program-high-performance.html` | `/programs#high-performance` |
| `program-juniors.html` | `/programs#juniors` |
| `program-cardio-tennis.html` | `/programs#cardio` |
| `program-social.html` | `/programs#social` |
| `free-lesson.html` | Replaced by the **free-week** hook → `/login` sign-up (no free coaching anymore) |

> Keep the underlying HTML files only if `build_blog.py`/sitemap generation needs them; otherwise delete
> and 301 (next section). Do **not** keep dead pages in the nav.

## 2. New top navigation
The nav's purpose flips from "browse services to buy" → "understand + sign up". Services are behind the
curtain, so the nav sells the story and pushes sign-up.

```
[NextPoint logo]      Coaches   Programs   Pricing   Contact        [Sign in]  [Start free →]
```

- Center links: **Coaches · Programs · Pricing · Contact** (Blog lives in the footer to keep the bar
  clean; add it to nav only if SEO wants it).
- Right: **"Sign in"** (`.cf-btn--ghost`, → `/login`) + **"Start free"** (`.cf-nav-cta` primary green,
  → `/login` in sign-up mode). The lime `--accent` may be used for "Start free" to make it pop.
- Mobile: existing `.cf-nav-toggle` hamburger; both CTAs appear in the drawer.
- Implement by editing `_NAV_LINKS` and the right-side block in `frontend/_shared/chrome.py`.
  Add a second right-side CTA button (currently only "Sign in" exists).

```python
# chrome.py — proposed
_NAV_LINKS = [
    ("Coaches",  "/coaches"),
    ("Programs", "/programs"),
    ("Pricing",  "/pricing"),
    ("Contact",  "/contact"),
]
# right side: <a class="cf-btn cf-btn--ghost" href="/login">Sign in</a>
#             <a class="cf-nav-cta" href="/login#/sign-up">Start free</a>
```

## 3. New footer
Reframe footer columns away from "Book X / Book Y" deep links (those are behind login) toward story +
sign-up + NAP. Edit `footer_html()` in `chrome.py`.

- **Brand column:** name, one-line positioning, address (NAP — must match Google Business Profile
  exactly), phone, email.
- **Explore:** Coaches, Programs, Pricing, Blog, Careers.
- **Get started:** Start your free week (`/login#/sign-up`), Sign in (`/login`), Contact.
- **Bottom row:** © 2026 · Killarney Country Club, Houghton, Johannesburg · social links if any.
- Keep one tasteful "Start your free week" CTA in/above the footer (the `.cf-band`).

## 4. Redirects (preserve SEO — see `docs/07`)
Every retired/old URL must **301** to its live equivalent. Add to the host-aware redirect map in
`courtflow-web`. No chains, self-canonical on every new page. This extends the existing `docs/07 §3`
table; the new-internal redirects to add:

| Old / retired path | 301 → |
|---|---|
| `/services` | `/` (or `/pricing` if GSC shows commercial intent) |
| `/program-high-performance`, `/programs/high-performance` (old) | `/programs#high-performance` |
| `/program-juniors`, `/programs/juniors` | `/programs#juniors` |
| `/program-cardio-tennis`, `/programs/cardio-tennis` | `/programs#cardio` |
| `/program-social`, `/programs/social` | `/programs#social` |
| `/free-lesson` | `/login#/sign-up` (or `/` with the free-week section) |
| Wix legacy (`/copy-of-…`, `/booking-calendar/…`, `/service-page/…`, `/coaching-team`, `/solutions`, `/jobs`, `/high-performance-program`) | per `docs/07 §3` (unchanged) |

> Before finalizing, confirm against **GSC top pages** (per `docs/07 §2`): if any retired URL pulls real
> traffic, redirect it to the closest *content-equivalent* anchor, not just `/`. Rankings > tidiness.

## 5. `web_app.py` route + sitemap changes (exact)
The current `web_app.py` serves each program separately and has `/services` + `/free-lesson` routes.
Reconcile it to the lean set:

- **Add** `GET /programs` → `_marketing("programs.html")` (the new combined page).
- **Change** the four `GET /programs/<x>` handlers to **301** to `/programs#<anchor>` (high-performance,
  juniors, social, cardio). (Or keep serving but `<link rel=canonical>` to `/programs` — 301 is cleaner.)
- **Change** `GET /services` → **301 → `/`** (or `/pricing` per GSC). **Change** `GET /free-lesson` →
  **301 → `/login#/sign-up`**.
- **Update `_MARKETING_URLS`** (the sitemap list ~line 449): drop `/services`, `/free-lesson`, and the
  four `/programs/<x>` entries; **add** `/programs` (priority 0.8). Keep `/`, `/coaches`, `/pricing`,
  `/contact`, `/careers`, blog.
- Keep `/login`, `/portal`, `/book*`, `/my` untouched (portal lane). Keep the `is_marketing_host`
  host-switch and `apply_chrome` injection intact.
- Nav "Start free" CTA href in `chrome.py` is **`/login#/sign-up`** (Clerk sign-up route — confirmed in
  `frontend/login.html`).

# 02 — Design System & the Signature Hero

> **Canonical visual reference:** `docs/public-site/prototype-home.html` (approved v2). The new
> components below are already implemented there — use it as the working source for the CSS and markup,
> then refactor the shared bits into `theme.css`. The v2 → component map: cinematic Ken-Burns hero =
> `.cf-hero--cinematic`; numbered alternating image+text rows = the new feature-row pattern; full-bleed
> clay statement; the faux "cockpit" device = `.cf-showcase`; the dark **Ten-Fifty5** band =
> `.cf-band--tenfifty5`; scroll-reveal via IntersectionObserver (reduced-motion safe).

## 1. Reuse the existing design system — do not reinvent
The site already has a complete, on-brand design system in **`frontend/_shared/theme.css`** (mirrored by
the portal's `frontend/app/app.css`). It is a **cross-lane contract** — class names and CSS custom
property names are stable and consumed by the portal. **Rule: extend it additively. Never rename or
remove an existing `cf-*` class or `--token`.**

### Brand tokens (already defined — use them, don't hardcode hex)
| Token | Value | Use |
|---|---|---|
| `--primary` / `--green` | `#0E7A47` | Primary green — buttons, headings accents, footer base |
| `--primary-d` / `--green-600` | `#0B5C36` | Hover / deeper green |
| `--green-700` | `#08431F` | Footer, darkest |
| `--accent` / `--lime` | `#C8E85C` | Tennis-ball lime — highlight CTAs, eyebrows, accents |
| `--ink` | `#10231A` | Body text |
| `--bg` | `#F5F8F6` | Page canvas |
| `--surface` | `#FFFFFF` | Cards |
| `--muted` / `--dim` | `#5F7268` / `#93A39A` | Secondary text |
| Radius | `--radius` 14px, `--radius-lg` 18px, `--radius-pill` 999px | Cards / heroes / buttons |
| Shadow | `--shadow-sm/-/-lg` | Elevation |
| Font | Inter (300–800) | Everything |

> **Why this palette is perfect for the hero:** the owner's favourite "splash" shot is a **yellow ball
> on court with a green-tinted background** — i.e. it already *is* the NextPoint palette (lime ball,
> green field). Lean into that: green gradients + lime accents over the splash photo.

### Existing components to reuse as-is
`.cf-wrap`, `.cf-section`, `.cf-grid`, `.cf-card` (+`--hover`), `.cf-btn` (`--primary/--accent/--ghost/--lg`),
`.cf-eyebrow`, `.cf-badge`, `.cf-hero`/`.cf-hero--home`, `.cf-pagehero`, `.cf-tile-ic`, `.cf-stat-num`/
`.cf-stat-lbl`, `.cf-nav*`, `.cf-footer*`, `.cf-skip`, `.cf-chart`.

## 2. New components to ADD to `theme.css` (additive)
Add these under a clearly-commented `/* === Public-site v2 additions === */` block. Keep them in the
same token vocabulary.

1. **`.cf-hero--cinematic`** — full-bleed homepage hero variant: min-height ~`82vh` (cap ~720px on
   desktop, ~560px mobile), supports a `<picture>`/`<img>` as the LCP element behind the gradient (see
   §3) rather than a CSS `background-image`, so we can set `fetchpriority="high"` + `srcset`.
2. **`.cf-gallery`** + **`.cf-gallery__item`** — responsive masonry/justified grid for real club photos;
   `.cf-lightbox` (vanilla-JS, no library) to view full size. Keyboard + ESC close, focus trap, respects
   reduced-motion. Lazy-loaded.
3. **`.cf-carousel`** (the **coach carousel**) — horizontal scroll-snap track of coach cards with
   prev/next buttons and swipe on touch. Pure CSS scroll-snap + a tiny JS for the arrow buttons and
   `aria` roles. Each slide = `.cf-coach-card` (photo, name, role badge, one-line cred, "Book" link).
   Must be populatable from a simple array/markup list (so adding a coach = adding one block).
4. **`.cf-coach-card` / `.cf-coach-bio`** — the founder bio layout (large portrait + structured bio:
   highlights, philosophy, "since/lang/residence" meta row) and the smaller carousel card.
5. **`.cf-quote` / `.cf-quotes`** — testimonial slider (scroll-snap, same engine as the carousel) with
   star rating, quote, attribution.
6. **`.cf-showcase`** — the "behind the curtain" portal showcase: split layout (copy + a framed
   screenshot/illustration of the cockpit) with a feature checklist. Used on the homepage to sell the
   member portal.
7. **`.cf-band`** — full-width colored CTA band (green or lime) for the repeated "Start your free week"
   conversion strip.
8. **`.cf-logos`** — optional trust strip (Wilson, Killarney Country Club) — small greyscale logos.

> Keep all new CSS responsive and reduced-motion-safe. No new web fonts. No CSS frameworks. Total new
> CSS budget: keep `theme.css` lean; target < 20KB added (minify-friendly).

## 3. The signature hero (the centrepiece)
The owner wants something **powerful** — loves the splash (yellow ball / green-tinted clay) and is
"sure you can do better", and floated a **3D** idea. Here is the recommended, *fast-first* approach in
three tiers. **Ship Tier 1 first; Tiers 2–3 are progressive enhancements that must never regress LCP.**

### Tier 1 — Cinematic photo hero (ship this; it is the LCP element)
- Full-bleed `.cf-hero--cinematic` using **`/img/hero-splash.webp`** (the green-toned ball-on-court
  splash) as a real `<img>` (not a CSS background) so we can set `fetchpriority="high"`, `width/height`,
  and a `srcset` (640/1024/1600/1920). Preload it in `<head>`.
- Overlay: the existing green gradient (`linear-gradient(115deg, rgba(8,67,31,.92), rgba(14,122,71,.45))`)
  for text legibility, plus a subtle lime glow behind the ball.
- Content: short eyebrow (`Killarney Country Club · Houghton, Johannesburg`), H1 (≤ 8 words), one-line
  subhead, **primary CTA "Start your free week"** + ghost "Sign in", and a 3-item proof row
  (rating / lessons delivered / "only clay court in Gauteng").
- This alone is beautiful and < 2.5s LCP. **Acceptance depends on Tier 1, not the fancy stuff.**

### Tier 2 — Lightweight motion (cheap polish, optional)
Add *one* tasteful motion, CSS/Canvas only, behind `@media (prefers-reduced-motion: no-preference)` and
desktop-width guard:
- Slow **Ken-Burns** zoom on the hero image, **or**
- A thin **Canvas particle layer** of drifting "clay dust"/lime motes over the lower third (tiny,
  capped particle count, pauses when tab hidden). No library; ~2KB JS.

### Tier 3 — Optional WebGL 3D ball (the "wow", strictly opt-in & lazy)
Only if the owner wants the 3D flourish, and only as enhancement:
- A **Three.js** (r128, already CDN-allowed in the project) scene: a single lime tennis ball, slow
  rotation, soft shadow, a puff of dust on load, subtle pointer parallax. Brand-lit (green key light,
  lime rim).
- **Hard rules:** lazy-load the script *after* first paint (`requestIdleCallback`/on-scroll); never
  block LCP; **desktop + non-reduced-motion + sufficient GPU only**; the static `hero-splash.webp`
  remains the guaranteed fallback and the SSR/HTML content is complete without JS. Cap to one canvas,
  dispose on route change, respect `prefers-reduced-motion` (skip entirely). Budget the bundle and
  gate behind a `data-hero="3d"` attribute so it's trivial to toggle off.
- If 3D ever costs us the LCP/Lighthouse gate, **drop to Tier 2**. Speed wins.

> Recommendation to the owner: **ship Tier 1 + Tier 2 at launch** (gorgeous, instant), and treat Tier 3
> as a fast-follow A/B test. A 3D hero is cool but a 2.5s+ LCP on mobile would hurt both conversion and
> the SEO we're protecting.

## 4. Imagery & motion rules
- Hero is the only above-the-fold image; everything else `loading="lazy"` + `decoding="async"`.
- Always set explicit `width`/`height` (or aspect-ratio) to avoid CLS.
- Provide `srcset`/`sizes` for hero, gallery, founders, coach cards.
- Section background photos use a green overlay (existing `.cf-pagehero` pattern) for text contrast.
- Don't autoplay video with sound; if the intro video is used, it's muted, `playsinline`, poster-first,
  and below the fold / click-to-play.

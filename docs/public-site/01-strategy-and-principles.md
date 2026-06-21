# 01 — Strategy & Principles

## The problem we're fixing
The Wix site is visually nice (big photos) but **clunky and bloated**: ~11+ pages that repeat each
other, and services sold *from the front*, which forces a lot of plumbing just to get a "one-click buy".
The result is a maze. NextPoint has now built a full member portal (the "cockpit") where booking,
rescheduling, cancelling, family management, packs, memberships and payments all live. So the public
site no longer needs to *be* the shop — it needs to be the **shop window** that gets people through the
door (sign-up).

## The model: services live behind the curtain
We are deliberately adopting a **SaaS shape**:

- **Front of curtain (this site):** beautiful, fast, photo-led. Explains *what* NextPoint offers and
  *why it's great*. Every service has one job: a CTA that leads to sign-up / the portal.
- **Behind the curtain (the portal):** the whole world of functionality — book a court/lesson/class,
  amend, cancel, manage your family, buy packs/membership, pay online, see coaches' live availability.

This adds **one step** (sign-up) versus the old buy-from-the-front flow. We accept that trade
consciously, and we **earn it** two ways:
1. **Make sign-up free and the payoff obvious.** A logged-in member gets a genuinely nice cockpit and
   far more control. Sell that.
2. **De-risk the step with a free trial** (below).

> Copy principle: never make the extra step feel like a toll gate. Frame it as *"create your free
> account and your whole club is one tap away."*

## The hook: 7 days free, full access
The headline offer is **"Your first week is on us — 7 days of full access, free."** New sign-ups can
book and use the club freely for 7 days, then continue pay-as-you-go or take a membership. This is the
classic SaaS "free to try, pay once you're hooked" motion the owner wants.

- **We no longer give away free coaching.** The giveaway is **court/access**, not lessons.
- Primary CTA wording across the site: **"Start your free week"** (→ `/login#/sign-up`, the Clerk
  sign-up route confirmed in `frontend/login.html`).
- Secondary CTA: **"Sign in"** for existing members (→ `/login`).
- The trial mechanics are enforced in the portal/billing layer (out of scope for this site). The site's
  job is to *message* it clearly and link to sign-up. Keep the copy claim generic enough that billing
  owns the exact rules (don't hard-promise "unlimited" if billing caps it — see `05` copy notes).

## Audience
1. **Casual / returning players & families** (Houghton/Killarney/northern Joburg) who want to book a
   court or get the kids into squads. Primary volume.
2. **Serious / competitive players & parents** evaluating the High Performance Program and the coaching
   pedigree (this is where the founder bios do heavy lifting — they are credible and people read them).
3. **Visitors / tourists** chasing the "only clay court in Gauteng" experience.

## Design principles (hold the line on these)
1. **Less is more.** If a page or section doesn't move someone toward sign-up or answer a real question,
   cut it. Six pages, not eleven. One primary CTA per screen.
2. **Pictures say a thousand words — and they must look expensive.** Lead with large, *polished*
   photography, full-bleed and edge-to-edge, like the old Wix site (the owner wants this look). Use
   **premium stock** (e.g. Unsplash) for atmospheric/hero/feature sections, and weave in **real
   NextPoint photos as authenticity accents** (gallery, founders, a few proof shots) — not as the whole
   story. Every major section is anchored by a strong image; alternate full-bleed, split, and offset
   layouts. Avoid a monotonous wall of identical small cards (that reads "backwards/templatey").
3. **Fast is a feature.** WebP, responsive `srcset`, lazy-load below the fold, preload the hero,
   no render-blocking JS, system-font fallback. Wix was slow — speed is a ranking *and* conversion win.
4. **One clear path.** The eye should always find the green "Start your free week" / "Book" button.
   Value-driven CTA copy beats "Learn more".
5. **Credibility up front.** ATP pedigree, real coaches, real courts, the clay USP, real testimonials —
   visible without scrolling far. Social proof in the first screen or two.
6. **Mobile-first.** Most local traffic is mobile. 44px+ tap targets, no horizontal scroll, legible
   without zoom, hero readable on a phone.
7. **Accessible by default.** WCAG-AA contrast (the palette already meets it), `:focus-visible`,
   semantic headings, `alt` on every image, `prefers-reduced-motion` honored.

## Best-practice benchmarks (what we're copying from the best)
- **Hero with a single, value-driven primary CTA above the fold** drives the largest conversion lifts;
  keep the H1 short (≈ under 8 words) and outcome-focused, one primary CTA, social proof within 3
  seconds (a stat strip / rating). Secondary CTAs repeat mid-page and in the footer.
- **Playtomic** (the visual reference already cited in `theme.css`): the homepage *is* a booking
  funnel — bright, friendly, card-based, "book a court / class / match" front and centre. We mirror the
  clarity and the card system, adapted to "describe service → sign up".
- **Leading club sites**: structured **program cards**, transparent **pricing**, an effortless
  **trial/booking** button, visible phone/location, clean minimal layout with a strong hero + tagline.
- **Form friction**: keep any on-page form (contact) to 3–5 fields. The "real" conversion (sign-up)
  is handed to the portal — don't rebuild auth on the marketing page.

## Non-goals
- No e-commerce/cart on the public site (the portal owns transactions).
- No separate explainer pages for routine actions (e.g. "how to book a court") — the portal is
  self-evident; don't spec pages the brief explicitly doesn't want.
- No DNS/SEO cutover here (supervised, per `docs/07 §4`). We only prepare the new pages + redirect map.

# 04 — Page Specs (section-by-section)

Conventions: every page uses `<!--#include nav-->` / `<!--#include footer-->`, links `/shared/theme.css`,
Inter, the skip link, one `<h1>`, semantic sections, lazy images with `alt`, and the SEO block from
`05 §4`. CTAs use `.cf-btn`. **"Start your free week" → `/login#/sign-up`** (Clerk sign-up; this is the real
URL — `login.html` sets `signUpUrl:"/login#/sign-up"`). **"Sign in" → `/login`**.
Pricing figures must match the billing seed (see `§5` note) — pull live where feasible.

---

## 1. Home (`/`) — the priority build

The homepage tells the whole story top-to-bottom and converts. Section order:

### 1.1 Hero — `.cf-hero--cinematic` (see `02 §3`)
- Background: `/img/hero-splash.webp` as the LCP `<img>` (preloaded, `fetchpriority="high"`, `srcset`),
  green gradient + lime glow overlay.
- Eyebrow: `Killarney Country Club · Houghton, Johannesburg`
- **H1:** `Your club. One tap away.` (alt option: `Tennis, sorted.`) — keep ≤ 8 words.
- Subhead: *"8 resurfaced hard courts and the only clay court in Gauteng — plus ATP-level coaching,
  squads and socials. Book it all from your phone."*
- CTAs: **`Start your free week`** (primary/lime) · **`Sign in`** (ghost, white border).
- Proof row (3 items, `.cf-stat-*` or inline): `★ 5.0 from members` · `1,200+ lessons delivered` ·
  `Only clay court in Gauteng`.

### 1.2 Free-week hook — `.cf-band` (lime or green)
A single, bold strip directly under the hero:
> **Your first week is on us.** Create a free account and get 7 days of full access — book courts, try
> a class, see live coach availability. No commitment. → **`Start your free week`**

Keep the claim aligned with what billing enforces (see `05` copy note — don't over-promise "unlimited").

### 1.3 Services — "Everything NextPoint, in one place" (`.cf-grid` of `.cf-card--hover`)
Describe **all** services as cards. Each card = icon, badge, title, 1–2 line benefit, and a CTA that
goes to **sign-up** (not a per-service buy page). 4 cards:

1. **Court booking** 🎾 — *"8 hard courts + the premium clay court. See live availability and book in
   seconds."* → `Start your free week`
2. **Coaching** 🏆 — *"1:1 and group lessons with named, ATP-level coaches. Pick your coach, see their
   real diary."* → `Meet the coaches` (`/coaches`)
3. **Programs & classes** 👥 — *"High Performance, junior squads, Cardio Tennis and socials — with
   enrolment and waitlists."* → `See programs` (`/programs`)
4. **Membership** ⭐ — *"Go unlimited. Members get free court bookings and member pricing."* →
   `See pricing` (`/pricing`)

### 1.4 Clay-court USP — image-led split section
The standout differentiator. Full-width split: large real/clay image (`/img/club-clay.webp`) + copy.
> **The only clay court experience in Gauteng.** Step onto the surface the pros slide on — European
> feel, match-ready bounce, a genuine tennis bucket-list tick. → `Start your free week`

### 1.5 Portal showcase — "Your cockpit" (`.cf-showcase`)
Sell the behind-the-curtain value (this is what justifies the sign-up step). Split layout: framed
cockpit screenshot/illustration (`/img/portal-cockpit.webp` — see assets note) + a feature checklist:
- Book courts, lessons & classes in seconds
- Reschedule or cancel yourself — no phone calls
- Manage your whole family from one login
- Buy packs or go unlimited with membership
- Pay securely online (or at the desk)
- See every coach's live availability
CTA: `Start your free week`.

### 1.6 Founders preview (teaser → `/coaches`)
Two founder cards (Neville Godwin, Ross Nemeth) with portrait, role, one-line cred, and `Read their
story` → `/coaches`. (Full bios live on the Coaches page; this is the credibility hook.)

### 1.6b Ten-Fifty5 AI-analysis band — `.cf-band--tenfifty5` (dark, premium)
A high-tech cross-sell to NextPoint's sister AI platform. **Full spec in `06-ten-fifty5-crosssell.md`.**
Dark ink/green background, lime accents, a faux data/stat visual (e.g. a serve-% trend sparkline +
"450+ data points · 18 KPIs · 1–2h"). Copy: *"Take your game further with AI match analysis — point-by-
point breakdowns, biomechanical stroke analysis and an AI coach trained on your data. Your first match
is free."* CTA **`Explore Ten-Fifty5 →`** → `https://www.ten-fifty5.com` (new tab, `rel="noopener"`,
track outbound). Place it after the founders/High-Performance content (warm audience).

### 1.7 Testimonials — `.cf-quotes` slider
3–5 real reviews (from `05 §3`), star ratings, attribution. Social proof.

### 1.8 Gallery — `.cf-gallery` (real club photos)
A justified grid of 6–8 real NextPoint shots (courts, juniors, singles, clubhouse, signage, clay) with
lightbox. "Lots of pictures" — this is where the club feels real. All lazy-loaded.

### 1.9 Final CTA band — `.cf-band` (green)
> **Ready to play?** Create your free account and your whole club is one tap away.
> `Start your free week` · `Sign in`

### 1.10 SEO
Title: `NextPoint Tennis — Courts, Coaching & Classes in Houghton, Johannesburg`.
JSON-LD: `SportsActivityLocation` (NAP, geo, hours, phone, priceRange) + `FAQPage` (keep/extend the 3
Q&As already present in the current home). Keep primary keywords: *tennis Houghton/Johannesburg, clay
court Gauteng, tennis lessons Johannesburg, high performance tennis*.

---

## 2. Coaches (`/coaches`) — the credibility page

### 2.1 Page hero (`.cf-pagehero`, real coaching photo)
H1: `Coached by the best.` Sub: *"Meet the team behind the results — from ATP tour coaches to junior
specialists."*

### 2.2 Founders — full bios (`.cf-coach-bio`, one per founder)
Large portrait + structured bio. **Use the real bios in `05 §2`.** Layout per founder:
- Portrait (`/img/coach-neville.webp`, `/img/coach-ross.webp`), name, role badge.
- **Coaching career highlights** (bullet list).
- **Coaching philosophy** (short paragraph).
- Meta row: *Coaching since · Languages · Residence*.
- CTA: `Book with {name}` → `/login#/sign-up` (lessons are booked in the portal).

### 2.3 Coach carousel (`.cf-carousel` of `.cf-coach-card`)
Heading: `The rest of the team`. Horizontal, swipeable, arrow controls. One card per coach from the
manifest (`05 §1`): photo, name, role, one-line bio, `Book` link. **Built to extend trivially** — adding
a coach = one `<article class="cf-coach-card">` (or one array entry if rendered from JS). Populate with
the coaches we have photos for (Allon, Colbert, Dejan, Dudley, Terka, …); placeholders/one-liners in
`05 §2` until the owner supplies final bios.

### 2.4 SEO
Title: `Tennis Coaches in Johannesburg — Book a Lesson | NextPoint Tennis`.
JSON-LD: `ItemList` of `Person` (extend the existing one to include carousel coaches).

---

## 3. Programs (`/programs`) — combined, anchored

One page, four anchored sections (deep-linkable `#high-performance`, `#juniors`, `#cardio`, `#social`).
Each: a real photo, a tight description, who it's for, and a `Start your free week` / `Enquire` CTA.

### 3.1 Hero
H1: `Programs for every player.` Sub: *"From first swing to college scholarship."* In-page anchor chips
to the four sections.

### 3.2 `#high-performance` — High Performance Program
ATP-certified coaching, advanced skill development, mental conditioning, match strategy; for teens
targeting scholarships/tournaments. Photo. CTA `Start your free week` + `Talk to a coach` (`/contact`).

### 3.3 `#juniors` — Junior squads
Beginner & intermediate junior development; structured squads; pathway language. Photo (`/img/club-juniors.webp`).
CTA `Start your free week`.

### 3.4 `#cardio` — Cardio Tennis
High-energy group fitness on court; all levels; fun-first. Photo. CTA `Start your free week`.

### 3.5 `#social` — Social play
Saturday/Wednesday socials; doubles, vibe, community. Photo. CTA `Start your free week`.

### 3.6 SEO
Title: `Tennis Programs in Johannesburg — High Performance, Juniors, Cardio | NextPoint`.
JSON-LD: `Service`/`Offer` per program + `BreadcrumbList`.

---

## 4. Pricing (`/pricing`) — transparent & simple

Lead with the **free week**, then three clean pricing cards, then an FAQ. Keep it honest and skimmable.

### 4.1 Free-week banner
`Start free — 7 days, full access, no card required up front.` (confirm card/no-card with billing).

### 4.2 Pricing cards (`.cf-grid` of `.cf-card`)
Pull from billing seed (`§5`). Suggested 3-card layout:
1. **Pay as you go** — *Court from R90 (30 min) / R150 (60 min). Lessons from R250 (30 min) / R400
   (60 min). Classes priced per session.* → `Start your free week`
2. **Membership** *(featured/"Most popular")* — *Unlimited free court bookings + member pricing, from
   ~R220/month.* → `Start your free week`
3. **Packs & bundles** — *Prepaid lesson/court packs — buy once, draw down as you play.* →
   `Start your free week`

> **Pricing accuracy:** these must match `diary/pricing.py` seed: Court 30/60/90/120 = R90/150/210/280;
> Private lesson 30/60 = R250/400; classes per-session. Membership makes **courts** free (not lessons).
> The Wix "member R0 court tier" is gone.
> **Pricing source:** `/api/billing/config` is payment/provider config **only — it does NOT return
> prices.** Prices live in `diary/pricing.py` (the seed). Either (a) hardcode the figures here with a
> comment `# matches diary/pricing.py seed — keep in sync`, or (b) add a small **public** read endpoint
> (e.g. `GET /api/diary/prices`) that exposes `price_for`/`durations_for` for court + lesson so the page
> never drifts. (b) is preferred but optional; (a) is fine for launch.

### 4.3 Pricing FAQ
Membership vs PAYG, what the free week includes, do members pay for lessons (yes — membership covers
courts only), online vs at-desk payment, family/dependents. Add `FAQPage` JSON-LD.

### 4.4 SEO
Title: `Tennis Court & Lesson Prices in Johannesburg | NextPoint Tennis`.

---

## 5. Contact (`/contact`)

- Hero/intro: club name + one line.
- **NAP block** (must match Google Business Profile exactly): *Killarney Country Club, 60 5th Street,
  Houghton Estate, Johannesburg, 2191 · 076 990 7439 · info@nextpointtennis.com*.
- **Hours:** Mon–Sun 06:00–21:00 (confirm).
- **Map:** embedded Google Map (lazy `<iframe>` / static map image → link out, to protect LCP).
- **Short form** (3–5 fields: name, email, message): posts to the existing contact handler. Keep it
  simple — the real conversion is `Start your free week`, shown alongside.
- JSON-LD: `LocalBusiness` with NAP + `geo` + `openingHours`.
- Title: `Contact NextPoint Tennis — Houghton, Johannesburg`.

---

## 6. Careers (`/careers`) — minimal, footer-linked only
Keep a single simple page: short intro + current vacancies (or "no current vacancies, email us") +
mailto. Not in primary nav. Low priority — do last.

---

## 7. 404 (`frontend/marketing/404.html`)
Keep branded; ensure links point to the new lean nav targets + `Start your free week`.

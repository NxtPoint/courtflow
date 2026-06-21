# 05 — Assets, Bios & Copy

## 1. Image manifest (already generated → `frontend/img/`)
Curated, web-optimized WebP set is committed to **`frontend/img/`** with `frontend/img/manifest.json`.
Reference these absolute paths (assets are served from root, like the rest of the site).

| File | Size | Use |
|---|---|---|
| `hero-splash.webp` (+ `-640/-1024/-1600/-1920`) | up to 1920w | **Homepage hero** (LCP). Use `<img srcset>` with the width variants; preload `-1024`/`-1600`. The green-toned ball-on-court splash — matches brand palette. |
| `coach-neville.webp` | 700² | Neville Godwin founder portrait (Coaches + home preview) |
| `coach-ross.webp` | 700² | Ross Nemeth founder portrait |
| `coach-allon/-colbert/-dejan/-dudley/-terka.webp` | 600² | Coach carousel cards |
| `club-clay.webp` | 1200w | Clay-court USP section + gallery |
| `club-courts.webp` | 1200w | Hard courts (scenic) — gallery / section bg |
| `club-juniors.webp` | 1200w | Junior squads — programs + gallery |
| `club-singles.webp` | 1200w | Singles action — gallery |
| `club-house.webp` | 1200w | Clubhouse/patio — gallery / contact |
| `club-sign.webp` | 1200w | NextPoint signage — gallery / brand |
| `logo.webp` | 480w | Brand logo (⚠ white background) |
| `ball-mark.webp` | 240w | Ball brandmark accent |

### Asset to-dos (flag for owner / build)
- **Transparent logo:** `logo.webp` has a white background. For the nav, export a **transparent SVG/PNG**
  from `Next Point Tennis Centre Logo.ai/.pdf` (in marketing material). Until then the nav can render the
  text logo (current behavior) or the white-bg logo on a white nav.
- **Portrait crops to review:** `coach-dejan.webp` is an action/serve shot (not a headshot);
  `coach-colbert.webp` wears sunglasses. Fine to ship, but owner may want to swap/recrop. Faces are
  center-cropped — re-crop if any head is clipped.
- **`portal-cockpit.webp` (needed):** the homepage portal-showcase (`04 §1.5`) needs a screenshot of the
  member cockpit. Capture one from the live portal (`/portal`, `/book`, `/my`) at go-live, or use a clean
  framed mock. Not auto-generated (the portal is behind auth).
- **Regenerate:** the generator script logic lives in this PR's history; re-run against
  `marketing material` to add/replace images. Keep names stable (referenced in HTML).
- **Intro video (optional):** `marketing material/videos/NextPoint Tennis Intro_v2.mp4` — usable as a
  muted, click-to-play, below-the-fold accent. Transcode to web MP4/WebM + poster first; do not autoplay
  on mobile.

## 2. Coach bios

### Founders (full bios — Coaches page)
Drawn from the owner's brochure (`Coaches.PNG` / Next Point Presentation). **Owner: confirm exact
career stats before publishing** (rankings/years can be sensitive).

**Neville Godwin — Program Director**
- *Highlights:* 2017 ATP Coach of the Year. Has coached top ATP professionals including **Kevin
  Anderson** (to a Grand Slam final and a career-high world No. 5 — best ever by a South African),
  **Hyeon Chung** (Australian Open semi-final, 2018), and **Reilly Opelka**; currently coaching
  **Alexei Popyrin** on tour.
- *Philosophy:* A holistic approach — technical skill, tactical awareness, physical conditioning and
  mental toughness — with training tailored to each player's individual needs.
- *Meta:* Coaching since 2004 · English · Johannesburg, South Africa.

**Ross Nemeth — Head Coach**
- *Highlights:* 30+ years coaching. Has guided several high-level juniors to **National Singles and
  Doubles titles**, and helped many players earn **college scholarships in the USA** (including a player
  to the Junior French Open).
- *Focus:* Technical development (stroke mechanics & footwork), tactical training (game strategy &
  match analysis), physical conditioning (strength, agility, endurance), and mental toughness.
- *Philosophy:* Develops well-rounded players with personalized programs tailored to each player's goals.
- *Meta:* Coaching since 1993 · English · Johannesburg, South Africa.

### Carousel coaches (one-liners — owner to finalize)
Photos exist; bios are **placeholders** for the owner to confirm/replace. Card = photo + name + role +
one line + `Book` → `/login#/sign-up`.
- **Allon** — *Coach.* "Develops technique and match confidence across all levels."
- **Colbert** — *Coach.* "Energetic group and junior coaching."
- **Dejan** — *Coach.* "Match-play and competitive development."
- **Dudley** — *Coach.* "Fundamentals, fitness and fun for every player."
- **Terka** — *Coach.* "Junior development and women's tennis."

> Build the carousel so each coach is a single repeatable block (or one entry in a JS array). Adding a
> coach later must be a one-line change.

## 3. Testimonials (real — from the current site)
- ★★★★★ **Tarryn K** — *"Friendly coaches and a great vibe."* "We joined just for a few casual lessons
  for our kids but ended up signing up properly — the coaches are incredibly patient, and the kids look
  forward to every session."
- ★★★★★ **Gareth R** — *"Best tennis courts in Joburg!"* "The facilities are top quality. These
  resurfaced courts are in another league, booking is easy, and the clay court is a real gem."
- ★★★★★ **Anita R** — *"Professional, yet personal."* "My daughter's been in junior development for six
  months and improved so much. Coach Ross and the team are hands-on and really care."

Trust stats (from current site — owner to keep current): **1,200+ lessons delivered · 96% parent
satisfaction · 8 resurfaced courts · only clay court in Gauteng**.

## 4. Copy & SEO blocks (ready to drop in)

### Hero
- Eyebrow: `Killarney Country Club · Houghton, Johannesburg`
- H1: `Your club. One tap away.`
- Sub: `8 resurfaced hard courts and the only clay court in Gauteng — plus ATP-level coaching, squads and socials. Book it all from your phone.`
- CTAs: `Start your free week` · `Sign in`

### Free-week band
`Your first week is on us — 7 days of full access, free. Create an account, book a court, try a class. No commitment.`
> ⚠ **Copy/billing alignment:** keep the trial claim consistent with what billing enforces. Prefer "full
> access for 7 days" over hard numbers like "unlimited" unless billing guarantees it.

### Clay USP
`The only clay court experience in Gauteng. Step onto the surface the pros slide on — European feel, match-ready bounce, a genuine bucket-list tick.`

### Portal showcase ("Your cockpit")
`Everything NextPoint, in your pocket. Book courts, lessons and classes, reschedule or cancel yourself, manage your family, buy packs or go unlimited — all from one login.`

### Final band
`Ready to play? Create your free account and your whole club is one tap away.`

### Meta titles / descriptions
| Page | Title | Description |
|---|---|---|
| Home | `NextPoint Tennis — Courts, Coaching & Classes in Houghton, Johannesburg` | `Book a tennis court, a lesson with an ATP-level coach, or a class at NextPoint Tennis, Killarney Country Club. 8 hard courts + the only clay court in Gauteng. Start free.` |
| Coaches | `Tennis Coaches in Johannesburg — Book a Lesson | NextPoint Tennis` | `Meet Neville Godwin (2017 ATP Coach of the Year) and Ross Nemeth, plus the NextPoint coaching team. Book a 1:1 or group lesson in Houghton, Johannesburg.` |
| Programs | `Tennis Programs in Johannesburg — High Performance, Juniors, Cardio | NextPoint` | `High Performance, junior squads, Cardio Tennis and social play at NextPoint Tennis, Houghton. Programs for every level — start your free week.` |
| Pricing | `Tennis Court & Lesson Prices in Johannesburg | NextPoint Tennis` | `Transparent pricing: pay-as-you-go courts and lessons, unlimited membership, and prepaid packs. Start with a free week at NextPoint Tennis.` |
| Contact | `Contact NextPoint Tennis — Houghton, Johannesburg` | `Find NextPoint Tennis at Killarney Country Club, 60 5th Street, Houghton Estate, Johannesburg. Call 076 990 7439 or email info@nextpointtennis.com.` |

### NAP (must match Google Business Profile exactly)
`NextPoint Tennis · Killarney Country Club, 60 5th Street, Houghton Estate, Johannesburg, 2191 · 076 990 7439 · info@nextpointtennis.com · Mon–Sun 06:00–21:00` (confirm hours).

## 5. JSON-LD (keep/extend what exists)
- Home: `SportsActivityLocation` (NAP, geo, hours, phone, priceRange `R90–R400`) + `FAQPage`.
- Coaches: `ItemList` of `Person` (founders + carousel coaches).
- Programs: `Service`/`Offer` per program + `BreadcrumbList`.
- Pricing: `FAQPage`.
- Contact: `LocalBusiness` with `geo` + `openingHours`.

## 6. Pricing source of truth
Match `diary/pricing.py` seed (see `04 §4`): Court 30/60/90/120 = **R90/150/210/280**; Private lesson
30/60 = **R250/400**; classes per-session; **membership makes courts free** (not lessons), from ~R220/mo.
Note: `/api/billing/config` returns payment config only — **not prices**. Hardcode to match the seed
(with a "keep in sync" comment) or add a small public prices endpoint (see `04 §4.2`). The Wix "member
R0 court tier" is gone. Confirm the membership monthly figure with the owner (Wix showed ~R220).

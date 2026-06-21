# 06 — Ten-Fifty5 Cross-Sell (market 1050 from NextPoint)

> **DECISION (owner-approved):** integrate via **touch-point 1 (homepage band)** + **touch-point 2
> (Programs mention)** only, linking out to `ten-fifty5.com`. The dedicated bridge page (touch-point 3)
> and the "NextPoint Tennis Lab" rebrand are **deferred** — keep them documented below for later.

## What Ten-Fifty5 is
**Ten-Fifty5** (`ten-fifty5.com`) is NextPoint's sister platform: **AI-powered tennis match analysis**
for serious players and coaches. Upload one match video → point-by-point breakdowns, serve/rally
heatmaps, **biomechanical stroke analysis** (pose + kinetic-chain scoring), and an **AI coach trained on
your own data** (powered by Claude). Career-long KPI trending. "Your first match is free."
Pricing is **USD** (Starter $25 / Standard $40 / Advanced $70 per month, + PAYG credits).

It is a **separate product on a separate domain** with its own login, sign-up and billing. NextPoint's
job is to **introduce and route** qualified players to it — not to rebuild it.

## Why it belongs on the NextPoint site
NextPoint's audience **is** Ten-Fifty5's audience: competitive players, juniors, scholarship-track teens,
and the coaches (Neville/Ross) who already coach at ATP level. The High Performance Program is the
perfect funnel. This is a credible, on-brand upsell — "train here, measure your game with our AI lab."

> Brand note: keep the two identities distinct. NextPoint = bright green/lime club. Ten-Fifty5 = dark,
> premium, data-led (see its live site). The cross-sell module should *feel* like Ten-Fifty5 (dark band,
> lime accents, a data/stat visual) so it reads as "a different, high-tech product by the same team."
> NextPoint also has a "Tennis Lab" brand asset (`Next Point Tennis Lab Logo`) — Ten-Fifty5 can be
> presented as the tech/lab arm if the owner prefers that framing. Confirm naming with the owner.

## How to integrate (three touch-points, low effort → higher)
1. **Homepage band (build now).** One dark, premium full-width section (`.cf-band--tenfifty5`) after the
   founders / High Performance content:
   - Eyebrow: `NextPoint × Ten-Fifty5`
   - H2: `Take your game further with AI match analysis.`
   - Copy: *"Upload a match and get point-by-point breakdowns, biomechanical stroke analysis and an AI
     coach trained on your data — built by the same team behind NextPoint. Your first match is free."*
   - A faux stat visual (e.g. "1st serve % +22%", "450+ data points / match", "18 KPIs") to signal the
     product without screenshots we don't own — or use an OG image from `ten-fifty5.com/og/`.
   - CTA: **`Explore Ten-Fifty5 →`** → `https://www.ten-fifty5.com` (external; `rel="noopener"`; opens
     in new tab). Track as an outbound event.
2. **Programs page mention (build now).** In `#high-performance` (`04 §3.2`), a short callout: *"Serious
   about improvement? Measure it with Ten-Fifty5 AI analysis."* → link out.
3. **Optional dedicated bridge page (`/video-analysis` or `/ten-fifty5`).** A single page that explains
   the offering in NextPoint's voice (what it is, who it's for, how it helps NextPoint players, "first
   match free") and CTAs out to `ten-fifty5.com`. Useful for SEO ("tennis video analysis Johannesburg")
   and as an ad landing page. Keep it thin — the product lives on the 1050 domain. Add to nav only if it
   earns traffic; otherwise footer-link it under "Explore".

## What NOT to do
- Don't duplicate Ten-Fifty5's pricing/checkout on NextPoint — link out; 1050 owns the transaction.
- Don't blur the brands into one — the contrast (bright club vs dark AI lab) is the point and looks
  premium. Two logos, one team.
- Don't promise SSO/shared accounts unless the owner confirms it (they are separate logins today).

## Hooks that already exist in the repo (FYI for later)
The platform already has an **analytics bridge to 1050** (`analytics/bridge.py`, `docs/12-tenfifty5-
bridge.md`) — a platform-admin "business switcher" (CourtFlow · Ten-Fifty5 · All). That's back-office
analytics, *not* the public cross-sell — but it confirms the two businesses are already wired to talk.
The public cross-sell here is purely marketing: a link-out + a great pitch.

## Owner decisions to confirm
- Framing/name on the NextPoint site: **"Ten-Fifty5"** vs **"NextPoint Tennis Lab"** vs both.
- Whether to build the dedicated `/video-analysis` page now or just the homepage band + programs mention.
- Currency note in copy (Ten-Fifty5 prices are USD) — usually fine since we link out, but flag it.

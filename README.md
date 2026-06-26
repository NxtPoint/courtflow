# CourtFlow — NextPoint Tennis (single source of truth)

> **What this repo is.** **CourtFlow** is a white‑label, multi‑tenant **Tennis Management Platform**
> (courts, coaching, classes behind one diary). **NextPoint Tennis** is **club #1**, migrating off Wix.
> Built to be sold cookie‑cutter to other clubs/academies, reusing the proven Ten‑Fifty5 (1050)
> architecture (Render, Clerk, Postgres, AWS S3/SES, Klaviyo, provider‑agnostic payments).
>
> **This README is the master index.** Both **Cowork (chat)** and **Claude Code** work from these docs.
> When picking up fresh, read this file first, then the doc map below.

**Platform name: CourtFlow (confirmed).** "NextPoint Tennis" = the first tenant/club. The name only
appears in marketing copy + repo, never in schema (schema is `club_id`‑scoped).

---

## 📍 Project status (last updated 2026‑06‑26)

| Track | State |
|---|---|
| **Spec** | ✅ Complete — `docs/00`→`11` + `BUILD_PROMPT.md`. **Current as‑built state: `docs/specs/`** (START at `docs/specs/README.md`). |
| **Platform build** | ✅ **LIVE on Render, end‑to‑end.** All three purchasing models, Yoco online pay + refunds, the commission/settlement engine, the **lesson approval lifecycle**, **redesigned client + coach + owner consoles**, and a booking **`.ics` calendar** are built & deployed. Authoritative current state: **`docs/specs/`** + **`CLAUDE.md`**. Remaining: **`docs/specs/OUTSTANDING.md`**. **Still needs Tomo (config):** S3 (coach photos), SES/Klaviyo (email), supervised DNS/SEO cutover. |
| **NextPoint Google Ads** | ✅ Optimised & live (2026‑06‑20) — see `marketing/`. |
| **SEO migration (Wix→Render)** | ⏳ Planned, not executed — `docs/07` + `migration/` (supervised cutover; never auto‑run). |

---

## 🗂️ Where everything lives (the doc map)

**Entry points**
- **`README.md`** (this file) — master index + status + session log.
- **`CLAUDE.md`** — the **live build‑state doc** for Claude Code: current modules, commands, verification gates, what still needs Tomo. The ground truth for *build* status.
- **`BUILD_PROMPT.md`** — copy‑paste master orchestrator + per‑agent lane prompts to build/extend the platform.

**The spec (`docs/`) — read in order**
| Doc | Covers |
|---|---|
| `docs/00-overview-and-vision.md` | Vision, personas, what we replicate from the Wix site, MVP vs later. |
| `docs/01-architecture-and-reuse.md` | Target architecture, services, hosting, env, 1050 reuse map. |
| `docs/02-data-model-multitenant.md` | Multi‑tenant schema (club/iam/diary/billing/core), tenancy isolation. |
| `docs/03-diary-booking-engine.md` | **The heart** — unified diary: availability, court/lesson/class booking, edit/cancel/reschedule, recurrence, conflicts, waitlists. |
| `docs/04-auth-and-roles.md` | Clerk reuse, multi‑tenant identity, roles & permissions. |
| `docs/05-payments-abstraction.md` | Provider‑agnostic gateway, plan catalogue, `apply_payment_event`, Yoco adapter, 3 settlement modes. |
| `docs/06-crm-and-klaviyo.md` | Own‑CRM (`core.*`), event contract, Klaviyo confirmations + lifecycle, consent. |
| `docs/07-marketing-site-and-seo-migration.md` | Public site rebuild + **SEO‑preserving Wix→Render migration** (URL map, 301s, sitemap, GSC). |
| `docs/08-admin-and-club-onboarding.md` | Club admin console + **cookie‑cutter** new‑club onboarding. |
| `docs/09-build-plan-and-agents.md` | Phased build, workstream→agent lanes, parallel multi‑agent runs. |
| `docs/10-reuse-from-1050-port-map.md` | Exact file‑by‑file port map: 1050 source → new module (copy/change/drop). |
| `docs/11-build-readiness-and-decisions.md` | Locked decisions + validated 1050 reuse map (created during the build). |

**Marketing & Ads ops (`marketing/`)**
| Doc | Covers |
|---|---|
| `marketing/google-ads-audit-2025-08.md` | NextPoint Google Ads audit + optimisation plan (incl. live 90‑day data). |
| `marketing/search-campaign-draft.md` | The local Search campaign — **now LIVE** status, structure, copy, outstanding items. |
| `marketing/adspirer-claude-code-setup.md` | How to run/manage the ads via Adspirer in Claude Code (Cowork can't auth it). |
| `marketing/optimization-loop.md` | The standing Measure→Learn→Adjust cadence + "tune toward bookings" rule. |
| `marketing/channels-and-tools.md` | What each tool does (stack confirmed complete), how social/Meta works, and the Wix→CourtFlow SEO‑migration answer. |
| `marketing/social-media-strategy.md` | Instagram (@nxtpnt) strategy — profile setup, content pillars, the tomorrow "content sprint" shot list, 2‑week calendar, captions, hashtags. |
| `marketing/ads-tuning-log.md` | Running tuning log (created by the scheduled day‑5 review onward). |

---

## Decisions locked (planning session)

| # | Decision | Choice |
|---|---|---|
| 1 | Infra boundary | New repo + new Postgres DB, reusing existing Render org / Clerk / AWS / Klaviyo accounts + ~80% of 1050 patterns. |
| 2 | Multi‑tenancy | Multi‑tenant from day one (`club_id` on every row); NextPoint = club #1. |
| 3 | Booking → payment | Multiple settlement types: pay online, pay at court, pay end‑of‑month. Diary launches without mandatory online pay. |
| 4 | Payment gateway | Provider‑agnostic; **Yoco** adapter first; PayPal retained. |
| 5 | CRM | Build (we are our own CRM); **Klaviyo** = marketing/confirmation engine. |
| 6 | Email | Klaviyo for all booking/lesson/class confirmations; SES = hard‑transactional fallback. |

---

## 🪵 Session log

- **2026‑06‑20 — Spec written.** Full `docs/00`→`10` + `BUILD_PROMPT.md` authored (Cowork). NextPoint Wix site + 1050 repo reviewed; decisions locked.
- **2026‑06‑20 — Platform build kicked off (Claude Code).** Phases 0–6 scaffolded + integration‑verified; `docs/11` + `CLAUDE.md` build‑state added. git initialised.
- **2026‑06‑20 — NextPoint Google Ads overhauled (Cowork + Adspirer).** Diagnosed PMax + vanity "Page view" conversion; paused PMax; built live **Search** campaign (R66/day, 4 ad groups, 38 keywords, 28 negatives, full extensions); tightened geo to 10 northern suburbs; demoted "Page view" → Secondary (only "Calls from ads" primary). Verified via Adspirer.
- **2026‑06‑25 (scheduled)** — Day‑5 ads tuning review (search‑terms prune + negatives + report).
- **2026‑06‑21/22 — Platform shipped to LIVE (Claude Code).** Three purchasing models end‑to‑end
  (unit/minute bundles, free week, lifecycle, membership tiers + access windows), Yoco online pay +
  refunds + receipts + reconcile, the Business Overview analytics dashboard, public‑site restyle. The
  `docs/specs/` folder established as the as‑built source of truth.
- **2026‑06‑25/26 — Client + coach + owner experience (Claude Code).** Redesigned client journey
  (action‑first cockpit, full‑screen calendar booking, consolidated `/plan`); the **lesson approval
  lifecycle** (request/propose/accept/decline + per‑coach review; on‑behalf auto‑confirms; client‑side
  accept/decline/withdraw); **coach & owner consoles** (onboarding, approval queue, clients‑360,
  statements with discount/write‑off, per‑service commission, financial cockpits — on the shared
  `crm_ui.js`); booking **`.ics` calendar**. Verified on a scratch Postgres; the `docs/specs/` set + this
  index refreshed.

> **Next big link‑up:** once CourtFlow's booking site is live, wire real conversions (booking completion, free‑lesson form, WhatsApp click) into Google Ads so bidding optimises toward actual leads — the ads and the platform reinforce each other.

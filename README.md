# NextPoint Tennis — Platform Spec & Build Plan

> **What this is.** The complete, buildable specification for migrating NextPoint Tennis off Wix
> and onto a new, multi-tenant **Tennis Management Platform** that reuses the proven Ten‑Fifty5
> (1050) architecture. NextPoint is **club #1**; the platform is designed from day one to be sold,
> cookie‑cutter, to other coaches, academies, and clubs.
>
> **Audience.** Tomo + Claude Code (the build agents). Every doc here is written so an agent can
> implement it without re‑deriving decisions.
>
> **Status.** Spec v1 — 2026‑06‑20. Planning only; no product code written yet.

---

## The one-paragraph vision

NextPoint Tennis becomes the flagship tenant of **"CourtFlow"** *(working name — rename freely)*: a
white‑label SaaS that lets any tennis club or academy put its **courts, coaching, and classes**
online behind one seamless **diary**. Members, visitors, and guests log in, see live availability,
and **book a court, book a lesson with a named coach, or join a class** (e.g. Cardio Tennis) in a few
taps — the Playtomic experience, plus the coaching/lessons layer Playtomic lacks. Coaches and
admins manage the *same single diary*: edit, cancel, reschedule, block time, set recurring sessions.
Every booking fires a **Klaviyo confirmation**. Payment is **provider‑agnostic** — pay online (Yoco),
pay at the court, or settle on a monthly account — with online card payment as the final phase but
**designed‑in from the start**. The whole thing runs on the infrastructure we already trust: Render,
Clerk, Postgres, AWS (S3/SES), Klaviyo — reused, not rebuilt.

---

## Decisions locked for this build (from the planning session)

| # | Decision | Choice |
|---|---|---|
| 1 | Infra boundary | **New repo + new Postgres database**, reusing the existing **Render org, Clerk, AWS (S3/SES), and Klaviyo** accounts and ~80% of 1050's patterns/code. Clean "separate venture", shared ops, built to scale. |
| 2 | Multi‑tenancy | **Multi‑tenant from day one.** Every row is club‑scoped (`club_id`); NextPoint = club #1. Sellable immediately. |
| 3 | Booking → payment | Support **multiple settlement types**: pay online, pay at court, pay end‑of‑month (account/tab). Diary launches without mandatory online payment. |
| 4 | Payment gateway | **Provider‑agnostic abstraction**, **Yoco adapter first** (keys already in hand; Wix is already Yoco‑linked). PayPal adapter retained as a second option. Kept vanilla, like the 1050 PayPal build. |
| 5 | CRM | **Build, don't buy** — we are our own CRM (mirrors 1050's 2026‑06‑18 decision). **Klaviyo** is the marketing/confirmation engine. |
| 6 | Email | **Klaviyo** for all booking/lesson/class confirmations + lifecycle (per Tomo). **SES** retained for hard‑transactional fallback. |

---

## Read in this order

| Doc | What it covers |
|---|---|
| [`docs/00-overview-and-vision.md`](docs/00-overview-and-vision.md) | Product vision, personas, what we replicate from the Wix site, competitive frame (Playtomic + coaching), MVP vs later. |
| [`docs/01-architecture-and-reuse.md`](docs/01-architecture-and-reuse.md) | Target architecture, the 1050 reuse map (what we copy vs build new), services, hosting, env. |
| [`docs/02-data-model-multitenant.md`](docs/02-data-model-multitenant.md) | Multi‑tenant schema: clubs, users, resources, the diary/booking tables, pricing, payments. Tenancy isolation strategy. |
| [`docs/03-diary-booking-engine.md`](docs/03-diary-booking-engine.md) | **The heart.** One unified diary. Availability, court/lesson/class booking, edit/cancel/reschedule, recurrence, conflicts, waitlists, cancellation policy. |
| [`docs/04-auth-and-roles.md`](docs/04-auth-and-roles.md) | Clerk reuse, multi‑tenant identity, roles & permissions (platform‑admin, club‑admin, coach, member, guest). |
| [`docs/05-payments-abstraction.md`](docs/05-payments-abstraction.md) | Provider‑agnostic gateway layer, plan/price catalogue, the `apply_payment_event(provider)` pattern, the Yoco adapter, the three settlement modes. |
| [`docs/06-crm-and-klaviyo.md`](docs/06-crm-and-klaviyo.md) | Own‑CRM (`core.*`), event contract, Klaviyo confirmation + lifecycle flows, consent/opt‑in, transactional vs marketing. |
| [`docs/07-marketing-site-and-seo-migration.md`](docs/07-marketing-site-and-seo-migration.md) | Public site rebuild, **SEO‑preserving Wix→Render migration** (URL map, 301s, sitemap, schema, GSC reindex). |
| [`docs/08-admin-and-club-onboarding.md`](docs/08-admin-and-club-onboarding.md) | Club admin console, white‑label theming, and the **cookie‑cutter onboarding** of a new club. |
| [`docs/09-build-plan-and-agents.md`](docs/09-build-plan-and-agents.md) | Phased build, workstream → agent mapping, and how to run **many Claude Code agents in one session** safely. |
| [`BUILD_PROMPT.md`](BUILD_PROMPT.md) | Copy‑paste **master orchestration prompt** + per‑agent prompts to kick off the build. |

---

## Naming note

The platform/product (the sellable SaaS) needs a name distinct from the first tenant. This spec uses
**"CourtFlow"** as a placeholder for the platform and **"NextPoint Tennis"** as club #1. Swap the
platform name anywhere — it only appears in marketing copy and the repo/readme, never in schema.

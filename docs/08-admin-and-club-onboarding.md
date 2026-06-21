# 08 — Club Admin Console & Cookie-Cutter Onboarding

> Tomo: *"make this fully cookie cutter"* — i.e. onboarding a new club must be a configuration
> exercise, not a code change. This doc defines the admin surface and the new‑club provisioning path.

## 1. Club admin console (the front‑desk app)

A portal SPA (role `club_admin`), built like 1050's dashboards (single page, thin gold‑view APIs).
Sections:

1. **Master diary** — the calendar from `03` §9. The operational core: see/book/edit/cancel/reschedule
   everything, drag bookings, block time, walk‑in bookings, take pay‑at‑court.
2. **Resources** — manage courts (8 hard + clay), coaches (bookable, availability), classes (Cardio
   Tennis, juniors, socials); set capacity, surface, active/inactive, display order.
3. **People** — members (status, profile, juniors/guardians, ledger balance), coaches (invite,
   profile, availability), guests. Create walk‑in members; send Clerk invites.
4. **Pricing** — `billing.product` + `billing.price` per audience (member/visitor/guest), memberships,
   lesson/class prices. ZAR. Effective dates.
5. **Billing & settlement** — open orders, monthly account balances, record desk payments, run/preview
   the monthly statement, and **online refunds (✅ built)**: "Recent online payments" → "Refund only" /
   "Refund & cancel" (`POST /api/billing/yoco/refund`).
6. **Analytics cockpit** — occupancy (court utilisation %), coach utilisation, revenue by
   product/settlement mode, attendance, no‑show rate, membership MRR, lead funnel conversion. Port
   1050's cockpit pattern over thin views. *(This is the per-club OPERATIONAL cockpit.)*
6b. **Business Overview (✅ built, `analytics/`):** a **platform-owner** dashboard at `/overview.html` —
   website visits + unique/new/returning visitors, traffic sources, top pages, by-country (first-party
   beacon `analytics.js` + Cloudflare `CF-IPCountry`), customers + sign-ups, bookings + revenue + settlement
   mix, NPS. `GET /api/analytics/overview` (platform_admin = all/filter; club_admin = own club). Distinct
   from the per-club operational cockpit above. Ten-Fifty5 (separate app+DB) bridges in later.
7. **Settings** — club profile, branding (logo/colours/domain), policies (booking window, cancellation
   cutoff, guest rules, which settlement modes are allowed, online‑payment toggle), Klaviyo list,
   payment gateway keys.

## 2. Coach console (subset)

Role `coach`: my diary, my classes (rosters + mark attendance + lesson notes), my availability/time‑off,
reschedule/cancel my bookings. No pricing/finance/other‑coach access.

## 3. Platform admin (us)

Role `platform_admin`: provision clubs, set branding/domain, impersonate (audited) for support,
cross‑club health (signups, bookings, revenue per club), feature flags per club, and — Phase 5 — bill
the clubs for the platform subscription.

## 4. Cookie‑cutter onboarding: provisioning a new club

The whole point of multi‑tenant. New club = run a guided wizard / seed, **no deploy**:

```
1. Create club          → club.club (slug, name, currency, timezone, locale)
2. Add location(s)      → club.location (NAP, geo)
3. Branding             → club.branding (logo, colours, domain, marketing_hosts, OG)
4. Policies             → club.policy (booking window, cancellation, settlement modes)
5. Resources           → courts, coaches, classes (+ availability rules)
6. Coaches             → invite users, iam.membership(role=coach) + coach_profile
7. Pricing             → products + prices (audiences) — clone from a template club, then edit
8. Identity            → club appears on the platform Clerk app; /login themed by host
9. Payments            → club's gateway keys (their Yoco/PayPal) + allowed settlement modes
10. Marketing          → Klaviyo list/segment + sender domain auth (their domain)
11. Domain/DNS         → point their domain at courtflow-web; SEO migration if replacing a site
12. Go live            → seed demo data optional; flip status='active'
```

Provide a **`provision_club.py`** idempotent script + an admin wizard UI that writes the same rows.
Ship a **"template club"** (sensible defaults: standard court hours, common price shapes, default
class types) that a new club is **cloned** from, then edited — turning onboarding into minutes.

## 5. White‑label boundaries

- **Per‑club:** domain, logo, colours, copy, prices, currency, policies, coaches/classes, Klaviyo list,
  payment keys, email sender.
- **Shared (platform):** the codebase, the Clerk app, the database, the diary engine, the gateway
  abstraction, the event/Klaviyo machinery. One deploy serves all clubs.
- **Theming mechanism:** CSS custom properties driven by `club.branding` (`--primary`, `--accent`,
  logo) injected per host — same "duplicate‑by‑convention then themed" approach as 1050's marketing
  pages, but parameterised.

## 6. What makes it sellable (the pitch, later)

A club gets: online court/lesson/class booking (Playtomic‑class UX), a unified diary their coaches and
front desk actually use, automated confirmations/reminders, member & membership management, pay‑how‑
you‑want settlement, their own branded site with SEO, and an analytics cockpit — on their own domain,
for a monthly platform fee. NextPoint is the live reference customer + case study.

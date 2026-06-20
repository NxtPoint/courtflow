# 04 — Auth & Roles (multi-tenant)

## 1. Identity = Clerk (reused pattern, ported from `auth_v2/`)

Login/identity stays in **Clerk** (Google + email), exactly like 1050. The API verifies the per‑user
JWT against Clerk's JWKS and derives identity **server‑side** (a spoofed client value is ignored).
Port `auth_v2/`'s verifier + principal resolution verbatim, then layer tenancy on top.

Request → principal resolution:
```
Authorization: Bearer <Clerk JWT>
  → verify signature (JWKS, AUTH_JWKS_URL) + issuer (AUTH_ISSUER)
  → extract clerk_user_id (sub) + email claim
  → upsert iam.user (by clerk_user_id)
  → load iam.membership rows for this user
  → resolve active club_id + role  (see §3)
  → Principal{ user_id, club_id, role, email }
```

Keep 1050's **dual‑mode** fallback (`OPS_KEY`/legacy key) only for server‑to‑server/cron/admin
scripts — never as a client path.

## 2. One Clerk instance or a new one?

**Recommendation: a new Clerk application for the platform** (e.g. `clerk.courtflow.app`), separate
from 1050's `clerk.ten-fifty5.com`. Reasons: distinct user base (club members vs 1050 analytics
users), distinct branding on the login page, no risk of cross‑product session bleed. We **reuse the
Clerk account/org** — just a new application within it. (If we ever want single sign‑on across 1050
and NextPoint, revisit; not needed now.)

Per‑club login branding (white‑label) is handled at the **app** layer (the `/login` page reads
`club.branding` by host), not by separate Clerk instances per club — one platform Clerk app serves all
clubs.

## 3. Tenancy resolution (which club am I acting in?)

A user can belong to multiple clubs (a coach who works at two academies; us as platform_admin
everywhere). Resolve the active club by, in order:

1. **Host** — request host → `club.branding.domain`/`marketing_hosts` → `club_id`. (A member on
   `nextpointtennis.com` is acting in NextPoint.) This is the primary signal.
2. **Explicit header/param** `X-Club` (for the multi‑club admin switcher) — validated against the
   user's memberships.
3. **Default** — the user's single membership if they only have one.

The resolved `club_id` is **authoritative server‑side**; all queries scope to it. Set
`app.club_id` (Postgres GUC) per transaction for RLS (doc 02 §1).

## 4. Roles & permissions

| Role | Scope | Can |
|---|---|---|
| `platform_admin` | all clubs (us/Tomo) | Provision clubs, theming, cross‑club support, impersonate (audited), everything below. |
| `club_admin` | one club | Full master diary; manage resources, coaches, classes, prices, members; take pay‑at‑court; run monthly billing; view club analytics. |
| `coach` | one club | Own diary (lessons + classes they run), own availability/time‑off, mark attendance/notes, reschedule/cancel own bookings, view rosters. **Cannot** change prices, see club finances, or manage other coaches. |
| `member` | one club | Book courts/lessons/classes, manage own bookings, manage membership/profile, see own ledger/statements. |
| `guest` | one club | Book a court as visitor/guest (login‑lite), claim free lesson. Minimal profile. |

Permission checks are a small central policy module (`iam/permissions.py`): `can(principal, action,
resource)`. Examples: `can(coach, 'cancel', booking)` → true only if `booking.coach_user_id ==
principal.user_id`. Keep it boring and centralised; every endpoint calls it.

## 5. Minors & guardians

- A `member` may add **junior players** (children) → `iam.player_profile` with `guardian_user_id`.
- Bookings/enrolments for a minor are made by the guardian; the **account/contact is always the
  guardian** (no minor login required for MVP).
- **Parental consent** captured at child‑add time (reuse 1050's consent module) before any processing;
  no minor PII flows to Klaviyo.

## 6. Signup → membership flow

```
Visitor clicks "Sign up / Book" → Clerk sign-up (Google/email)
  → first API call resolves club by host, upserts iam.user
  → create iam.membership(club_id, user_id, role='member', member_status='prospect')
  → consent screen (marketing opt-in + privacy) → core.consent (forward write-path, 1050 pattern)
  → emit account_created (Klaviyo welcome flow, doc 06)
Front-desk path: club_admin can create a member directly (walk-in) → invite email.
Coach path: club_admin invites a coach → iam.membership(role='coach') + iam.coach_profile.
```

## 7. Reuse checklist

- [ ] Port `auth_v2/` verifier + JWKS caching.
- [ ] Add `iam.user` upsert + membership/club resolution.
- [ ] Central `permissions.py`.
- [ ] `/login` page (port 1050's, re‑themed per club by host).
- [ ] Consent capture (port `marketing_crm/consent/` + `consent.js`).
- [ ] New Clerk application; set `AUTH_JWKS_URL`, `AUTH_ISSUER`, `CLERK_PUBLISHABLE_KEY`.

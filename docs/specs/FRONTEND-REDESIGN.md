# FRONTEND-REDESIGN — radically simpler, one-page-per-role

The front-end simplification effort (started 2026-06-27). Goal: **every feature available, intuitive,
super simple & clean.** Fewer pages, fewer tabs, no duplication. Backend is unchanged — this is purely
the presentation layer. Decisions locked with Tomo (AskUserQuestion, 2026-06-27).

## Locked decisions
- **Client = ONE page** ("Home") with a sticky chip nav → four sections: **Book · My Bookings · Profile ·
  Money**. (No more separate `/my`, `/account` for the client; My Bookings was duplicated on `/portal` +
  `/my` — fixed.)
- **Pricing pages retire.** Replaced by a **post-trial wizard** (free-week ends or "Upgrade" tap →
  *Membership or PAYG?* → choose months / sessions / durations by affordability) **plus** a lightweight
  **Manage plan** view in the Money section (browse/buy/upgrade anytime).
- **Coach = ~5 tabs:** Profile · Services · Schedule · Clients · Reporting & Financials.
- **Admin = same shape, admin options** (detailed once client+coach land).
- **Build order:** Client → Coach → Admin. Blueprint written as we go.

## Principle: nothing is lost
Every existing feature maps to a section. Consolidation must not drop: dependents, refund-requests, the
`.ics` calendar, the lesson approval lifecycle ("needs your attention"), reschedule/cancel, usage/spend
history, membership/pack purchase. Cross-checked against [FEATURES.md](FEATURES.md) + [INVENTORY.md](INVENTORY.md).

---

## Client one-page (Home) — section map
Sticky chips: **[Book] [Bookings] [Profile] [Money]** (click → smooth-scroll to section; active state).

1. **Book** — greeting + plan chip (free-week countdown / Member / PAYG); the trial/plan **nudge** (→
   wizard); 3 service launchers (Court/Lesson/Class → full-screen `/book/<service>`); staff get a small
   row of console links (Coach/Admin/Settings).
2. **My Bookings** — *Needs your attention* (accept/decline a proposed time, withdraw a request) ·
   *Upcoming* (add-to-calendar, reschedule, cancel) · *Past & cancelled*. (was `my.js`)
3. **Profile** — editable details (email read-only) + **Family** (children/dependents add/edit/remove).
   (was `account.js` Profile+Family)
4. **Money** — plan card (+ **Manage plan** → wizard/plan) · usage this month · spend + history · recent
   payments (request refund) · refund requests. (was `account.js` Financials)

**Nav simplification (`portal.js`):** the client nav collapses to **Home** (+ **Book** as the one
deep-link); My Bookings / Plan / Account drop out of the top nav (now sections). Coach/Admin/Statement
links stay for those roles. `/my`, `/account`, `/plan(s)` pages remain reachable as fallback until the new
Home is validated, then they retire (or 301 → `/portal#section`).

## Post-trial wizard (next increment)
Trigger: free-week end (or "Upgrade"). Steps: (1) Membership or PAYG → (2a) membership: pick term
(1/3/6 mo) → Yoco; (2b) PAYG: optionally buy a session pack (pick #sessions/duration) or skip. Replaces
browsing `/plan`. The Money "Manage plan" opens the same wizard for upgrades.

## Coach (next) — 5 tabs
Profile (bio/photo/languages/quals/visibility/review-toggle) · Services (per-duration rates · classes ·
own packs) · Schedule (availability + time-off + **approval queue** + book-for-client) · Clients (360) ·
Reporting & Financials (cockpit + statement). (Reorganises today's single-page `coach.js`.)

## Admin (later) — same shape, admin options
Likely: Diary · People · Services & Pricing · Coaches & Pay · Reporting & Financials · Settings — folding
today's 7 admin tabs + 8 Settings tabs into fewer, role-aware groups (see [PERMISSIONS.md](PERMISSIONS.md)
for any staff-role gating).

## Increments / status
- [x] Blueprint (this doc).
- [ ] **Client one-page Home** (Book + Bookings + Profile + Money) + nav simplification. ← building now
- [ ] Post-trial wizard + Manage-plan.
- [ ] Coach 5-tab.
- [ ] Admin restructure.
- [ ] Retire `/my`, `/account`, `/plans` (301 → Home) once validated.

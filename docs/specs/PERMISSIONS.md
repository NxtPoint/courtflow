# PERMISSIONS — roles × screens × actions (review map)

**Purpose:** a deliberate map of *who should see and do what*, screen by screen, so we can (a) confirm
the consoles expose the right things to the right roles and (b) decide whether the white-label product
needs finer **staff sub-roles** beyond today's 5. This is a **review artifact** — read it, mark what's
misplaced, and we build to the corrected version. Nothing here changes behaviour yet.

Grounded in the live code: `iam/permissions.py` (`can()`), the role SPAs `frontend/js/client.js` /
`coach_app.js` / `admin_app.js` (post-login routing + surfaces), and — for the sensitivity map below —
the classic `settings.js` tabs. (The classic `admin.js` survives at `/admin-classic`; `coach.js` was
deleted.) The `can()` boundary and role model are unchanged by the SPA rebuild.

---

## 1. How access is enforced today (3 layers — all real)
1. **Backend `can(principal, action, resource)`** (`iam/permissions.py`) — the security boundary.
   Fail-closed, role-ranked, ownership-scoped. Role comes from the **Clerk JWT server-side**; the
   client can never assert it. Every endpoint gates on it.
2. **Frontend shell gate** — each privileged page calls `Portal.boot({requireRoles:[…]})`; a wrong
   role sees *"not available for your role"* and the console code never runs (`portal.js:110`).
3. **Role-focused nav + landing** — `portal.js` renders a *role-precise* nav (staff no longer see the
   client Home/Account clutter) and `landingFor(role)` sends each role to its own home on sign-in:
   - **member/guest → Home · Account** (Home is the client cockpit).
   - **coach → Coach (landing) · Account.**
   - **club_admin/platform_admin → Admin (landing) · Settings.**
   `home.js` redirects staff off `/portal.html` to their console unless `?stay=1` (a testing bypass).
   The client booking pages still exist and are reachable — staff can **book for themselves** (see §4/§5).

   > **AS-BUILT (2026-07-03/04): redesigned role SPAs — all three COMPLETE + LIVE.** Landing surfaces are
   > now the drill-through SPAs: **member/guest → `/`** (client SPA, `frontend/js/client.js`; also `/portal`,
   > `/app`) · **coach → `/coach`** (coach SPA, `coach_app.js`; non-coaches bounced; the classic `coach.js`
   > was **deleted**) · **club_admin/platform_admin → `/admin`** (the new responsive owner SPA, `admin_app.js`
   > — see [ADMIN-REDESIGN.md](ADMIN-REDESIGN.md); the classic tab console is preserved at **`/admin-classic`**).
   > All three now share ONE widget layer (`Widgets.TransactionDetail`/`Calendar`/`Setup` — see
   > [FRONTEND-STANDARDISATION.md](FRONTEND-STANDARDISATION.md)). Old `/account.html`, `/my.html`, `/book.html`
   > **302 → the client SPA**. The role gate + `can()` boundary below are unchanged — only the surfaces were
   > rebuilt, so the sensitivity map in §4/§5 (written against the classic tabs) still holds screen-for-screen.

**Verdict:** no cross-role leakage. A member/coach cannot use the admin console. The gaps below are
about **granularity** and **surfacing**, not a security hole.

> **A 4th, non-role gate (2026-07-11):** the members-area **Ten-Fifty5 embed** (`#/analysis` in the client
> SPA) is gated by an **email allowlist** (`TF5_EMBED_ALLOW_EMAILS`), not by role — allowlisted members get
> the live embed, everyone else a "Coming soon" card. This is a temporary **private-test** switch (launch =
> clear the env → all members); it sits outside the `can()` / shell-role / nav-landing model above. The embed
> itself is authorised by the member's own Clerk token relayed into the iframe (cross-app SSO), so no extra
> Ten-Fifty5 login/role applies.

## 2. The 5 roles (today)
`platform_admin` > `club_admin` > `coach` > `member` > `guest` (most-privileged first).
- **platform_admin** — cross-club, everything (us, the platform operator).
- **club_admin** — full control of ONE club. **Monolithic: gets every admin tab + all of Settings.**
- **coach** — own diary/availability/clients/statement (services self-scoped — the coach console shows
  only the coach's own services); cannot touch prices/finances/other coaches. **Own commission is
  READ-ONLY** (surfaced, greyed, in Setup). Can **book a court for themselves** (auto-member).
- **member** — book + manage own bookings/profile/plan/financials (incl. own unified statement +
  self-cancel membership).
- **guest** — book as a visitor; minimal profile.

## 3. Page / nav access (who gets which shell) — `portal.js` NAV + `requireRoles`
Nav is now **role-focused**: each role lands on its own home and sees only its own links (the client
Home/Account no longer clutter the staff nav; "Statement" is folded into the coach console's Money tab).
A ✓ = a **nav link** the role sees; a page a role can still *reach* (e.g. staff booking for themselves via
`/book/court`) but that is no longer in their nav is marked `(reach)`.

| Page | Home `/portal` | Book | My Bookings | Plan | Account | **Coach** | **Admin** | **Settings** |
|---|---|---|---|---|---|---|---|---|
| member / guest | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — |
| coach | (reach) | (reach) | (reach) | (reach) | ✓ | ✓ (landing) | — | — |
| club_admin | (reach) | (reach) | (reach) | (reach) | (reach) | (reach) | ✓ (landing) | ✓ |
| platform_admin | (reach) | (reach) | (reach) | (reach) | (reach) | (reach) | ✓ (landing) | ✓ |

*(guest ≈ member minus account depth.)* Staff **land on their console** on sign-in (`landingFor`); the
standalone `/statement.html` is kept as an unlinked fallback (its content now lives in the coach Money tab).

## 4. Owner/Admin surface — what each tab does + sensitivity
*(Described against the classic tab console — now at `/admin-classic`; the new `/admin` SPA reorganises
these same capabilities into Home · People · Money · Diary · Setup · Insights, sensitivity unchanged.)*
**Admin console — 5 tabs (+ ⚙ Settings link); default = Dashboard:**
| Tab | What it does | Sensitivity |
|---|---|---|
| **Dashboard** | Business-health landing: **Today at the club** (today's diary) + this-month money KPIs (net revenue · commission kept · rent due · active members · MRR · lessons paid) + net-revenue trend + last-30-days growth (visits/visitors/new customers/bookings/**NPS**) + **Quick actions** (incl. **Book a court for myself** → `/book/court`) | **financial-ish** |
| **Diary** | Sub-tabbed **Timeline** (master resource-timeline; view/manage all bookings) + **Classes** (create class types, schedule sessions, rosters/attendance) | operational |
| **People** | Member 360 drawer; **grant/revoke membership**; member's unified statement + **void / write-off** an owed order | operational + *financial (grant / write-off)* |
| **Money** | **Billing** (config · recent payments · **refunds** · refund-requests) + the full **financial cockpit** (per-coach settlement, revenue-by-service, commission owed, MRR, range toggle) | **financial** |
| **Insights** | Business analytics / Business Overview (visits/customers/revenue/NPS) | financial-ish |

Court/resource config moved into **Settings → Courts**; the old separate Classes, Resources, Billing,
Cockpit and Overview tabs are folded into the five above (Diary/Money/Insights).

**Settings (`/settings.html`, `settings.js`) — 8 tabs:**
| Tab | What it does | Sensitivity |
|---|---|---|
| **Profile** | Club name/location | config |
| **Hours** | Opening hours | config |
| **Courts** | Courts/resources | config |
| **Pricing** | Court rates · packs · memberships (+ lifecycle/access hours + per-tier **payment options**) | **financial (prices)** |
| **Services** | Service catalogue | config |
| **Coaches** | Invite / (remove) coaches | config + people |
| **Coach pay** | **Per-service commission editor + rent** | **highly sensitive** |
| **Payments** | Online-payment toggle | config |

> **The core question:** today **one** role (`club_admin`) gets *all* of the above. A real club often
> wants staff who can run the desk (Diary/Classes/People) **without** seeing finances, commission, or
> changing prices/branding. That's the "screens that shouldn't be there for everyone."

## 5. Coach surface (`/coach`, `coach_app.js`) — own-scope only, **5 tabs; default = Home:**
- **Dashboard** — "Needs your attention" (approval queue) + the cockpit (net-of-commission KPIs · earnings
  trend · month-end position · top clients · upcoming).
- **Schedule** — a week TIMELINE of the coach's lessons + classes (prev/next-week); tap a lesson →
  completed/no-show, tap a class → roster; + **Book for a client** + **Book for myself** (→ `/book/court`) +
  block time off.
- **Clients** — the 360 (private, derived).
- **Money** — the month-end settlement statement (mark-collected + discount/write-off); supersedes the
  standalone `/statement.html`.
- **Setup** — sub-tabbed **Services & pricing** (own services, self-scoped) **+ the club-commission card
  (READ-ONLY, greyed — surfaced but not editable)** + classes, and **My profile**.

All **ownership-scoped** by `can()` (`_is_coachs_own`); commission is view-only. Looks correctly scoped —
flag anything a coach sees that they shouldn't.

> **AS-BUILT (2026-07-02): coach SPA + the single event story.** The coach console is now the drill-through
> SPA at `/coach` (bottom nav **Home · Schedule · Clients · Money · Setup**). The per-session **money
> actions — Mark collected / Discount / Write off** — now live in **the one coach event story**
> (`GET /api/coach/bookings/<id>`), reached by tapping a session anywhere; they remain coach-own-scoped
> (a coach only ever settles arrears on their own sessions). **Lesson lifecycle** (accept / propose /
> decline) stays gated so **only the awaited party — or an admin — can act** (matches §7: coach on own,
> admin on any), and a coach/admin on-behalf booking always auto-confirms (no acceptance step).

## 6. Client/member surface (`/portal`, `/book`, `/my`, `/plan`, `/account`)
Cockpit + quick-book · full booking · My Bookings (reschedule/cancel/needs-attention/calendar) · Plan
(buy membership/packs) · Account → **Profile · Family (dependents) · Financials** (plan, orders,
**unified statement** — view + **pay / part-pay** owed lines via tick-to-pay, refund-requests, **self-cancel
membership**). All own-scope (gated by `view_own_ledger`). Looks correct.

## 7. Backend `can()` action → minimum role (from `iam/permissions.py`)
| Action(s) | Allowed |
|---|---|
| `manage_club / branding / policy / resources / coaches / prices`, `view_finances`, `run_billing`, `take_pay_at_court`, `view_club_analytics`, `view_master_diary` | club_admin+ |
| `provision_club`, `impersonate`, `cross_club` | platform_admin |
| `cancel/reschedule/edit_booking`, `mark_attendance`, `accept/propose/decline` | admin (any) · coach (own) · member/guest (own) |
| **add a player to a semi-private lesson** (`POST /api/diary/bookings/<id>/add-player`) | **the SAME gate as reschedule** — `can(reschedule_booking)`: admin (any) · coach (own) · the booking's owner. A non-staff booker may only add a **club member** or **their OWN dependent** (`_addable_player_uid`); staff may add any member/child. Each added player is billed their own owed order per-head. |
| `manage_own_availability / time_off`, `view_own_rosters` | club_admin, coach |
| `create_booking`, `book_court/lesson/class` | all roles |
| `manage_own_profile`, `view_own_ledger` (gates own statement view + pay/part-pay + self-cancel membership), `manage_own_membership`, `request_refund` | all roles |
| `add_junior` | club_admin, member |

Note every admin write currently collapses to a **single threshold (`club_admin`)** — that's the knob a
staff-role split would turn.

**Recent (2026-07) staff/config permission facts (not `can()` actions — enforced at the route/repo):**
- **Search members** (`GET /api/diary/members/search`, `GET /api/coach/members/search`) — **staff only**
  (coach / club_admin / platform_admin). A member/guest never gets the member-lookup picker.
- **Create a client** ("New client" — a walk-up/off-system customer) — **admin AND coach**
  (`POST /api/admin/clients`, `POST /api/coach/clients`; the coach route delegates to `admin.create_client`).
  Members can't create other clients.
- **Set a service's `max_clients`** (the semi-private/squad cap) — a **lesson-service config**, so the
  **OWNING coach OR the owner** may set it (`PATCH /api/services/<id>`, guarded by `_load_manageable`, lessons
  only), exactly like the service's name/variations/payment — **never** commission (owner-only, greyed for the
  coach).
- **Payment-mode enforcement** — a member/guest is bound to the **club-enabled ∩ service-offered** modes: the
  EXACT service's `payment_modes` are enforced by `product_id` (a card-only service — e.g. Clay — refuses
  pay-at-court), a pack's purchasable modes = its SERVICE's modes ∩ enabled (`allowed_purchase_modes`; an
  unpayable pack is refused, no at-court fallback), and a class enrolment's mode is gated the same
  (`diary.classes.enrol(role=…)` — a member can't conjure a free or unpayable class seat). Staff on-behalf
  (admin/coach) still settle at-court / offline as before.

---

## 8. Findings & decisions to make

### 8a. Coarse role model (the substantive gap) — STRAW MAN to react to
A possible 3-tier club-staff split (between `coach` and a true `owner`). **Not built — react to it:**
| Surface | `front_desk` | `manager` | `owner` (= today's club_admin) |
|---|---|---|---|
| Diary, Classes, People (view/book) | ✓ | ✓ | ✓ |
| Resources/Courts/Hours config | — | ✓ | ✓ |
| Grant/revoke membership | — | ✓ | ✓ |
| Billing / refunds / payments taken | take-at-court only | ✓ | ✓ |
| Cockpit / Financials / Overview | — | ✓ | ✓ |
| Pricing (rates/packs/plans) | — | — | ✓ |
| **Coach pay / commission** | — | — | ✓ |
| Branding / Policy / Profile | — | — | ✓ |
| Invite/remove coaches | — | ✓? | ✓ |

If you like a split like this, the build is: a `role` value set + optional per-permission grants in
`iam`, `can()` updated to the new thresholds, console tabs gated by role, and an admin UI to assign a
staff member their role. (Decide: fixed presets like above, or fully custom per-permission grants?)

### 8b. Built-but-NOT-surfaced — now WIRED (the earlier quick-win gap is closed)
The four endpoints flagged here are now surfaced, and coach/court deletes are **real** (HARD-delete when
there's no booking/financial history, else archive):
| Endpoint | Surfaced as |
|---|---|
| `POST /api/admin/coaches/<id>/resend-invite` | "Resend invite" on the Coaches list |
| `DELETE /api/admin/coaches/<id>` | "Remove coach" (real delete → `{outcome:'deleted'\|'archived'}`) |
| `DELETE /api/admin/resources/<id>` | "Remove court" (real delete → `{outcome:'deleted'\|'archived'}`) |
| `PATCH /api/admin/products/<id>` + lifecycle `status` | "Edit service" + Deactivate/Reactivate/Terminate |

All are `club_admin+` (Settings = Configure, admin-only). Per-tier membership **payment options**
(`GET/PATCH /api/admin/membership-config`) and the People-drawer **void / write-off**
(`POST /api/admin/orders/<id>/void`) are likewise admin-gated. Coverage is otherwise high with 0 broken
calls; coach/client surfaces 100%.

### 8c. Things to confirm aren't misplaced
- **People → grant/revoke membership** sits in the operational console but is a *financial* grant — should
  it require `manager`+ under a staff split? (flagged above)
- **Insights/analytics** (the admin **Insights** tab) for `club_admin` is own-club only (platform_admin =
  all clubs) — correct.

---

## 9. Mark-up area (your edits)
Note here what's wrong / should move / which staff tiers you want, and we build to it:
- _e.g. "front_desk must NOT see Cockpit"_ …
- _e.g. "merge Settings→Services into Pricing"_ …
- _e.g. "we only need owner + front_desk, drop manager"_ …

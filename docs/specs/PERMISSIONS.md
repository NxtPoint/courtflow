# PERMISSIONS — roles × screens × actions (review map)

**Purpose:** a deliberate map of *who should see and do what*, screen by screen, so we can (a) confirm
the consoles expose the right things to the right roles and (b) decide whether the white-label product
needs finer **staff sub-roles** beyond today's 5. This is a **review artifact** — read it, mark what's
misplaced, and we build to the corrected version. Nothing here changes behaviour yet.

Grounded in the live code: `iam/permissions.py` (`can()`), `frontend/js/portal.js` (nav + shell gate),
`admin.js` / `settings.js` / `coach.js` / `account.js` (console tabs).

---

## 1. How access is enforced today (3 layers — all real)
1. **Backend `can(principal, action, resource)`** (`iam/permissions.py`) — the security boundary.
   Fail-closed, role-ranked, ownership-scoped. Role comes from the **Clerk JWT server-side**; the
   client can never assert it. Every endpoint gates on it.
2. **Frontend shell gate** — each privileged page calls `Portal.boot({requireRoles:[…]})`; a wrong
   role sees *"not available for your role"* and the console code never runs (`portal.js:110`).
3. **Role-filtered nav** — `portal.js:28` only renders the links a role is allowed.

**Verdict:** no cross-role leakage. A member/coach cannot use the admin console. The gaps below are
about **granularity** and **surfacing**, not a security hole.

## 2. The 5 roles (today)
`platform_admin` > `club_admin` > `coach` > `member` > `guest` (most-privileged first).
- **platform_admin** — cross-club, everything (us, the platform operator).
- **club_admin** — full control of ONE club. **Monolithic: gets every admin tab + all of Settings.**
- **coach** — own diary/availability/clients/statement (services self-scoped — the coach console shows
  only the coach's own services); cannot touch prices/finances/other coaches.
- **member** — book + manage own bookings/profile/plan/financials (incl. own unified statement +
  self-cancel membership).
- **guest** — book as a visitor; minimal profile.

## 3. Page / nav access (who gets which shell) — `portal.js` NAV + `requireRoles`
| Page | Home `/portal` | Book | My Bookings | Plan | Account | **Coach** | **Statement** | **Admin** | **Settings** |
|---|---|---|---|---|---|---|---|---|---|
| member | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — | — |
| coach | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| club_admin | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ |
| platform_admin | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ |

*(guest ≈ member minus account depth.)* This part looks correct — flag anything you'd change.

## 4. Owner/Admin surface — what each tab does + sensitivity
**Admin console (`/admin`, `admin.js`) — 7 tabs:**
| Tab | What it does | Sensitivity |
|---|---|---|
| **Diary** | Master resource-timeline; view/manage all bookings | operational |
| **Classes** | Create class types, schedule sessions, rosters/attendance | operational |
| **Resources** | Add/edit/disable courts & resources | config |
| **People** | Member 360 drawer; **grant/revoke membership**; member's unified statement + **void / write-off** an owed order | operational + *financial (grant / write-off)* |
| **Billing** | Recent payments, **refunds**, refund-requests | **financial** |
| **Cockpit** | Per-coach settlement, revenue, commission owed, MRR | **financial** |
| **Overview** | Business analytics (visits/customers/revenue/NPS) | financial-ish |

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

## 5. Coach surface (`/coach`, `coach.js`) — own-scope only
My Week (lessons + classes) · My Classes (manage) · Availability + time-off · My Clients (360, private) ·
Dashboard cockpit · Statement (month-end, mark-collected + discount/write-off). All **ownership-scoped**
by `can()` (`_is_coachs_own`). Looks correctly scoped — flag anything a coach sees that they shouldn't.

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
| `manage_own_availability / time_off`, `view_own_rosters` | club_admin, coach |
| `create_booking`, `book_court/lesson/class` | all roles |
| `manage_own_profile`, `view_own_ledger` (gates own statement view + pay/part-pay + self-cancel membership), `manage_own_membership`, `request_refund` | all roles |
| `add_junior` | club_admin, member |

Note every admin write currently collapses to a **single threshold (`club_admin`)** — that's the knob a
staff-role split would turn.

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
- **Overview/analytics** for `club_admin` is own-club only (platform_admin = all clubs) — correct.

---

## 9. Mark-up area (your edits)
Note here what's wrong / should move / which staff tiers you want, and we build to it:
- _e.g. "front_desk must NOT see Cockpit"_ …
- _e.g. "merge Settings→Services into Pricing"_ …
- _e.g. "we only need owner + front_desk, drop manager"_ …

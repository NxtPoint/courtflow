# Client Self-Service — "My Account" Spec

> **AS-BUILT NOTE (2026-06-26):** this is the original *design* spec — the feature shipped and the UI
> evolved. The booking SPA is now `booking.js` (full-screen calendar; `book.js`/`quickbook.js` retired),
> membership/packs are the consolidated **`/plan`** page, and the client has a statement + a "Needs your
> attention" lesson-proposal surface + "Add to calendar". Read this for design intent; see
> **`BUSINESS-RULES.md`** §7 + **`INVENTORY.md`** for what's live.

Status: **DRAFT / implementation-ready scope.** Lane: primarily **E (frontend)** + **A (iam)** for
profile/dependents, with read-only seams into **C (billing)** for financials and a small **B (diary)**
change to thread dependents through bookings. Nothing here builds the feature — this is the contract a
future engineer builds straight from.

This spec is grounded in the live code as of this branch. Where something already exists it is marked
**REUSE**; where it is missing it is marked **NEW** with a concrete sketch.

---

## 0. The owner's brief (verbatim intent)

> Clients must have all demographics — name, surname, telephone (email pre-populated & **hardcoded as the
> client id**), address details etc. They should be able to **add children**, and that children dropdown
> must be available for **all bookings** (court, lessons, etc.). The profile should also have a **financial
> section**: current plan, usage, spend per month, etc. They should be able to **edit their plan** and
> **request refunds** there. One self-service section = full account maintenance.

One page, `my-account.html` (working name **"My Account"**), with tabs: **Profile · Family · Plan & Billing**.
It joins the existing portal nav (`portal.js` `NAV`) next to **My Bookings** and **Membership** (the
Membership page's purchase card folds into the **Plan & Billing** tab — see §5.4).

---

## 1. What exists vs what's missing (research summary)

| Concern | Today | Gap |
|---|---|---|
| Identity / email-as-id | `iam.user` keyed by `clerk_user_id`, `email` is the link-by-email key (`iam/repositories.py` `upsert_user_by_clerk_id`). `email` is set from the verified Clerk JWT. | `email` is editable in the DDL but must be **read-only in this UI** (it is the client id). |
| Demographics | `iam.user` has `first_name, surname, phone, email`. | **No address, DOB, emergency contact, comms-consent** on `iam.user`. No member-facing `GET/PATCH /api/me/*` at all (only admin `/api/admin/*`). |
| Children / dependents | `iam.player_profile` already exists with `dob, skill_level, guardian_user_id` (minor→parent). `iam/permissions.py` already grants `add_junior` to members + `manage_own_profile`. | No **member CRUD endpoints**, no UI, and **no way for a child to be a booking party**. `diary.booking_party.user_id` requires an `iam.user`; a child without a login has none. |
| Booking parties | `diary.booking_party(user_id, party_role, guest_name, guest_email, price_id, attended)`; `create_booking(parties=[...])`; on-behalf via `booked_for_user_id` (`diary/bookings.py`). `book.js` only sends a single optional guest. | The children dropdown must inject a **party row** for court/lesson and a target for class enrol — without breaking the host/guest model. |
| Financials — plan | `billing.membership_subscription`; `membership_status()` (`billing/membership.py`); `has_active_membership()` (`diary/pricing.py`). | No single member endpoint that returns plan + usage + spend together. |
| Financials — spend/usage | `billing.order` / `billing.order_line` / `billing.payment` / `billing.account_ledger`; `ledger.build_statements()` is **admin/cron-only**. `my.js` already admits "no public per-member statement GET yet". | **No `GET /api/me/financials`**; member cannot see spend history or usage counts. |
| Plan change | Buy: `POST /api/billing/membership/checkout` (Yoco, 1 month) + `membership.js`. Admin grant/revoke: `/api/admin/members/<id>/membership`. | **No member-initiated cancel/downgrade** (admin can revoke; member cannot). |
| Refunds | Admin-only direct refund: `POST /api/billing/yoco/refund` (record-only; booking NOT reversed). | **No client refund *request*** object/flow — the brief wants the client to *request*, an admin to *approve*. |
| Permissions | `can()` already names `manage_own_profile`, `view_own_ledger`, `manage_own_membership`, `add_junior`. | Add `request_refund` (member) + `manage_refund_requests` (admin). |
| 1050 reference | `core.person` (dob, is_minor derived, profile fields, photo) + `core.relationship` (type `parent_junior`, status pending/active/revoked, invite_token). | Confirms our `player_profile`+`guardian_user_id` shape is the right reuse; we copy the *pattern*, not the code (no `core.*` import). |

**Key reuse signals:** the permission verbs are already authored; `iam.player_profile.guardian_user_id`
already models parent→child; `diary.booking_party` already supports non-login participants via
`guest_name`/`guest_email`. The bulk of NEW work is member-facing endpoints + UI, plus two small schema
additions and one targeted `book.js`/booking-payload change.

---

## 2. Profile / demographics

### 2.1 Field list

| Field | Source | Type | Required | Validation / notes |
|---|---|---|---|---|
| `email` | `iam.user.email` | string | — (system) | **READ-ONLY. The client id.** Rendered disabled with helper text "This is your login — contact the club to change it." Never accepted in PATCH. |
| `first_name` | `iam.user.first_name` | string | yes | 1–80 chars, trimmed. |
| `surname` | `iam.user.surname` | string | yes | 1–80 chars, trimmed. |
| `phone` | `iam.user.phone` | string | yes | E.164-ish; allow `+`, digits, spaces; 7–20 chars. Stored as typed. |
| `dob` | **NEW** `iam.user.dob` | date | no | not in the future; sanity floor (e.g. ≥ 1900). Drives age/junior context for the account holder. |
| `address_line1` | **NEW** profile | string | no | ≤ 120. |
| `address_line2` | **NEW** profile | string | no | ≤ 120. |
| `city` | **NEW** profile | string | no | ≤ 80. |
| `postal_code` | **NEW** profile | string | no | ≤ 16. |
| `country` | **NEW** profile | string | no | ISO-3166 alpha-2 preferred; free-text accepted, default club country. |
| `emergency_contact_name` | **NEW** profile | string | no | ≤ 80. |
| `emergency_contact_phone` | **NEW** profile | string | no | same rule as `phone`. |
| `marketing_opt_in` | **NEW** profile/consent | bool | no (default false) | comms/marketing consent; feeds `marketing_crm` (consent already a first-class concept there). |

**Email-is-identity rule.** `email` is the join key for link-by-email at first Clerk login
(`upsert_user_by_clerk_id`) and the payer email in admin views. Allowing the member to edit it would orphan
their Clerk identity and break ledger/people joins. Therefore: **email is display-only here**; changing it is
an admin/Clerk operation out of scope for self-service.

### 2.2 Data model — where each field lives

`first_name, surname, phone` are already on `iam.user`. The rest are **NEW**. Two options; the spec picks **(A)**:

**(A) — `ADD COLUMN IF NOT EXISTS` on `iam.user` (chosen).** Demographics are 1:1 with the human and
cross-club (a person has one home address). Matches the existing idempotent-DDL discipline (no migrations).
`dob` is the only field that overlaps `iam.player_profile.dob`; for the *account holder* we store it on
`iam.user` (player_profile is for richer player/junior detail and may not exist for an adult member).

```sql
-- iam/schema.py  _DDL additions (idempotent, safe every boot)
ALTER TABLE iam.user ADD COLUMN IF NOT EXISTS dob                     date;
ALTER TABLE iam.user ADD COLUMN IF NOT EXISTS address_line1           text;
ALTER TABLE iam.user ADD COLUMN IF NOT EXISTS address_line2           text;
ALTER TABLE iam.user ADD COLUMN IF NOT EXISTS city                    text;
ALTER TABLE iam.user ADD COLUMN IF NOT EXISTS postal_code             text;
ALTER TABLE iam.user ADD COLUMN IF NOT EXISTS country                 text;
ALTER TABLE iam.user ADD COLUMN IF NOT EXISTS emergency_contact_name  text;
ALTER TABLE iam.user ADD COLUMN IF NOT EXISTS emergency_contact_phone text;
ALTER TABLE iam.user ADD COLUMN IF NOT EXISTS marketing_opt_in        boolean NOT NULL DEFAULT false;
```

**(B) — separate `iam.user_profile` table** (rejected for v1): cleaner separation but adds a join and a row
to manage for zero current benefit, since these are all 1:1 with the user. Revisit only if address becomes
multi-valued or club-scoped.

> Multi-tenancy note: `iam.user` is intentionally **global / cross-club** (one human, many clubs). These
> demographics travel with the human, which is correct. The member-facing endpoints are still principal-scoped
> (a user only ever reads/writes *their own* row), so there is no cross-tenant leak.

### 2.3 API — `GET/PATCH /api/me/profile`

New blueprint **`me/`** (lane: A/iam, mirrors `admin/routes.py` structure). `club_id` and `user_id` come
from the principal, never the body. Gate: `can(p, "manage_own_profile")` (already defined; allows
member/coach/club_admin/guest).

```
GET /api/me/profile
200 -> {
  "email": "jo@x.com",            // read-only
  "first_name": "Jo", "surname": "Smith", "phone": "+27 82 …",
  "dob": "1990-04-01" | null,
  "address_line1": …, "address_line2": …, "city": …, "postal_code": …, "country": "ZA",
  "emergency_contact_name": …, "emergency_contact_phone": …,
  "marketing_opt_in": false,
  "role": "member"                // from principal, for the UI
}
```

```
PATCH /api/me/profile
body (all optional; only present keys are written):
  { first_name?, surname?, phone?, dob?, address_line1?, address_line2?, city?,
    postal_code?, country?, emergency_contact_name?, emergency_contact_phone?, marketing_opt_in? }
- email is IGNORED if present (never written).
- validate per §2.1; reject 422 {error:"VALIDATION", fields:{…}} on bad input.
200 -> the same shape as GET (the refreshed row).
```

Repository (plain SQL, `iam/repositories.py`):
```python
def get_profile(session, *, user_id): ...        # SELECT the columns above
def patch_profile(session, *, user_id, **fields): # UPDATE iam.user SET … updated_at=now() WHERE id=:uid
                                                  # whitelist keys; never email/clerk_user_id
```
On `marketing_opt_in` change, best-effort `marketing_crm.tracking.emit("consent_updated", …)` (guarded
import, same pattern as `admin/routes.py::_send_coach_invite_email`).

---

## 3. Children / dependents

### 3.1 Concept

A **dependent** is a child/family member the account holder books on behalf of. They have **no login** of
their own (no `clerk_user_id`). They must be selectable as the *player* on a booking. Two viable models:

- **Model 1 — reuse `diary.booking_party` as a name only.** A child is just text passed as
  `guest_name`/`guest_email`. Zero schema. But it can't be a reusable dropdown, has no DOB, and every booking
  re-types the child. **Rejected** — fails "add children" + "dropdown for all bookings".
- **Model 2 — a real dependent row that mints/links an `iam.user` (chosen).** A dependent is a lightweight
  `iam.user` (login-less: `clerk_user_id` NULL) tied to the parent via `iam.player_profile.guardian_user_id`.
  This makes the child a first-class **bookable party** (`booking_party.user_id`) — it threads through courts,
  lessons, and class enrolment with the existing party/owner machinery, and shows up in rosters/attendance.
  It reuses the 1050 `parent_junior` pattern and our already-present `player_profile.guardian_user_id` +
  `add_junior` permission.

### 3.2 Data model (NEW table + reuse `player_profile`)

We add a thin **`iam.dependent`** row as the canonical "child of this guardian in this club" record, and
back it with a login-less `iam.user` so it can be a booking party. (`player_profile` already carries
`guardian_user_id`; `dependent` is the explicit, queryable parent→child edge the UI lists.)

```sql
CREATE TABLE IF NOT EXISTS iam.dependent (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    club_id           uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
    guardian_user_id  uuid NOT NULL REFERENCES iam.user(id) ON DELETE CASCADE,  -- the parent
    dependent_user_id uuid NOT NULL REFERENCES iam.user(id) ON DELETE CASCADE,  -- login-less child user
    first_name        text NOT NULL,
    surname           text,
    dob               date,
    notes             text,
    is_active         boolean NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (guardian_user_id, dependent_user_id)
);
CREATE INDEX IF NOT EXISTS ix_dependent_guardian ON iam.dependent (club_id, guardian_user_id);
```

**Creating a dependent** (repository, one transaction):
1. `INSERT INTO iam.user (clerk_user_id=NULL, email=NULL, first_name, surname)` → `dependent_user_id`.
   (A login-less human; never resolvable by Clerk, never auto-enrolled — it has no token path.)
2. `INSERT INTO iam.dependent (… guardian_user_id, dependent_user_id …)`.
3. Optionally `INSERT INTO iam.player_profile (user_id=dependent_user_id, dob, guardian_user_id=parent)`
   to carry junior detail (reuses the existing table) — keeps junior detail consistent with the 1050 model.

> Why a login-less `iam.user` and not just a name string: it gives the child a **stable id** to attach to
> `booking_party.user_id`, rosters, attendance, and (future) per-child usage — exactly what a name string
> cannot. The child never authenticates; `resolve_principal` only ever mints/links users with a real
> `clerk_user_id`, so a NULL-clerk row is inert to auth.

### 3.3 Member CRUD endpoints (`me/` blueprint)

Gate: `can(p, "add_junior")` (member/club_admin today — extend to allow `coach` if desired) plus an explicit
ownership check that `guardian_user_id == principal.user_id` for read/update/delete.

```
GET    /api/me/dependents
       200 -> {dependents:[{id, dependent_user_id, first_name, surname, dob, notes, is_active}], count}

POST   /api/me/dependents
       body {first_name(req), surname?, dob?, notes?}
       201 -> {dependent:{id, dependent_user_id, …}}

PATCH  /api/me/dependents/<id>
       body {first_name?, surname?, dob?, notes?}  (404 if not the caller's dependent)
       200 -> {dependent:{…}}

DELETE /api/me/dependents/<id>     -> soft delete (is_active=false); 200 {ok:true}
       (soft so historical bookings/rosters keep a valid party reference)
```
All queries are `club_id`-scoped (from principal) **and** `guardian_user_id = principal.user_id`-scoped.

### 3.4 Threading the child into bookings (the load-bearing change)

The child becomes a **party** on the booking. The booking is still **owned by the account holder**
(`booked_by_user_id = parent`), so it appears in the parent's My Bookings and is billed to the parent —
the child is the *player*, not the owner. This deliberately reuses the existing party model and does NOT use
`booked_for_user_id` (that override re-owns the booking, which we don't want for a child).

**Court / lesson (`POST /api/diary/bookings`).** `book.js` adds a "Who's playing?" dropdown (default
**"Myself"**, plus each active dependent). On submit, when a child is chosen:

```js
// book.js submit() — parties currently carries an optional member-guest.
// ADD: a dependent player party. PRESERVE the existing host/guest logic + the online seam.
var parties = [];
if (state.player && state.player.dependent_user_id) {
  // booking owner stays the parent (booked_by_user_id); the child is the player party.
  parties.push({ party_role: "player", user_id: state.player.dependent_user_id });
}
if (state.guest) {                                   // unchanged guest path
  parties.push({ party_role: "host", user_id: state.principal.user_id });
  parties.push({ party_role: "guest", guest_name: state.guest.name, guest_email: state.guest.email || null });
}
// body, createBooking call, and res.booking.order_id → Pay.startYocoCheckout(...) all UNCHANGED.
```

No backend change is required for court/lesson: `create_booking(parties=[…])` already inserts
`booking_party` rows (`_insert_party`) and prices per party in `_create_order_guarded`. A child party prices
as `audience="member"` (no `guest_name`/`party_role=guest`), so the parent's membership-covered/PAYG logic
applies normally. **Guard to add in `book.js`:** if the chosen player is a dependent AND a guest is also
entered, still inject the parent as `party_role:"host"` so `GUEST_REQUIRES_HOST` is satisfied.

**Class enrolment (`POST /api/diary/classes/:id/enrol`).** Today the route accepts `user_id` only for
admin/coach (`target_user = b.get("user_id") if role in (admin,coach) else p.user_id`). To enrol a child,
the member must be allowed to pass a `dependent_user_id` they own:

```python
# diary/routes.py enrol() — extend the member branch (small, ownership-checked change):
dep = (b.get("dependent_user_id") or "").strip() or None
if dep and _owns_dependent(s, club_id=p.club_id, guardian_user_id=p.user_id, dependent_user_id=dep):
    target_user = dep            # member enrols their own child
else:
    target_user = (b.get("user_id") if p.role in (admin,coach) else p.user_id) or p.user_id
```
`_owns_dependent` is a one-row `iam.dependent` existence check. The enrolment is otherwise unchanged
(capacity/waitlist/pricing all key off `target_user`). The **billing** still flows to whoever the enrolment
charges — confirm with C whether a child enrolment should bill the *guardian's* order; v1 recommendation:
charge the guardian (the enrolment's payer is the booking owner). Flag as open question §8.

**`my.js` display.** When a booking has a dependent player party, render a small "for {child name}" tag on
the row (`getBooking` already returns `parties`; the list endpoint does not — either add the player name to
`list_bookings` or fetch lazily). Minor, list-only polish.

### 3.5 Dropdown availability — "for ALL bookings"

The dropdown lives in `book.js`'s **Preferences** column (court + lesson) and in the class confirm step. It is
populated once from `GET /api/me/dependents` at wizard boot (cache on `state`). For a **class** the same
selection sets `dependent_user_id` on the enrol body. So a single "Who's playing?" control covers court,
lesson, and class — satisfying the brief.

---

## 4. Financial section — read endpoints

One member-facing read powers the whole **Plan & Billing** tab. New `me/` routes; gate
`can(p, "view_own_ledger")` (already defined).

### 4.1 `GET /api/me/financials`

```
200 -> {
  "currency": "ZAR",
  "plan": {
    "type": "membership" | "payg",
    "active": true,
    "name": "Unlimited Courts",
    "current_period_end": "2026-07-20" | null,   // renewal/expiry
    "price_minor": 22000, "sold": true,          // the club's membership offer (for upsell)
    "online_enabled": true                        // can self-serve buy?
  },
  "usage_this_month": {                            // confirmed bookings in the current calendar month
    "court": 4, "lesson": 1, "class": 2, "total": 7
  },
  "spend": {
    "this_month_minor": 81000,                     // sum of PAID order amounts this month
    "history": [                                   // last ~6 months, most recent first
      {"period": "2026-06", "paid_minor": 81000, "bookings": 7},
      {"period": "2026-05", "paid_minor": 45000, "bookings": 4}
    ]
  },
  "account": {                                     // monthly_account tab (if used)
    "balance_minor": 30000,                        // outstanding (charges - payments)
    "open_charges": 2
  },
  "next_charge": {                                 // best-effort
    "kind": "membership_renewal" | null,
    "amount_minor": 22000, "due_date": "2026-07-20"
  }
}
```

### 4.2 Queries behind it (all principal-scoped: `club_id` + `user_id` from the principal)

- **plan** — `membership.membership_status(session, club_id, user_id)` (**REUSE** as-is) +
  `has_active_membership` predicate. `type` = `membership` when active else `payg`.
- **usage_this_month** — group confirmed bookings owned by the user in the month:
  ```sql
  SELECT booking_type, count(*) FROM diary.booking
  WHERE club_id=:c AND booked_by_user_id=:u
    AND status IN ('confirmed','completed')
    AND starts_at >= date_trunc('month', now()) AND starts_at < date_trunc('month', now()) + interval '1 month'
  GROUP BY booking_type;
  ```
- **spend.this_month_minor / history** — sum of the member's PAID orders by month:
  ```sql
  SELECT to_char(date_trunc('month', o.created_at),'YYYY-MM') AS period,
         COALESCE(SUM(o.amount_minor),0) AS paid_minor, count(*) AS orders
  FROM billing."order" o
  WHERE o.club_id=:c AND o.user_id=:u AND o.status='paid'
    AND o.created_at >= now() - interval '6 months'
  GROUP BY 1 ORDER BY 1 DESC;
  ```
  (Spend = settled money. `paid` covers online + membership_covered[=0] + desk-settled. A `bookings` count
  can join `order_line.booking_id` if a per-booking count is wanted.)
- **account.balance_minor** — latest `balance_after_minor` from `billing.account_ledger`:
  ```sql
  SELECT balance_after_minor FROM billing.account_ledger
  WHERE club_id=:c AND user_id=:u ORDER BY created_at DESC LIMIT 1;
  ```
- **next_charge** — if membership active with a `current_period_end`, that date + the membership price;
  else null. (No recurring subscription yet — v1 membership is buy-a-month.)

**Lane note:** these read `billing.*` directly. To respect the lane split, put the SQL in a NEW
`billing/me.py` (`member_financials(session, *, club_id, user_id)`) called by the `me/` route — billing owns
its tables; `me/` just composes. Guard each sub-query (try/except → defaults) exactly like
`diary/pricing.py`, so the tab degrades gracefully if a table is mid-migration.

### 4.3 Optional `GET /api/me/orders` (spend detail / receipts)

For a "recent payments" list on the tab (mirrors admin `list_payments`, but self-scoped):
```sql
SELECT o.id, o.created_at, o.amount_minor, o.currency_code, o.status, o.settlement_mode,
       ol.description
FROM billing."order" o
LEFT JOIN LATERAL (SELECT description FROM billing.order_line WHERE order_id=o.id ORDER BY created_at LIMIT 1) ol ON true
WHERE o.club_id=:c AND o.user_id=:u AND o.status IN ('paid','refunded')
ORDER BY o.created_at DESC LIMIT 50;
```
Each row exposes a **Request refund** action when eligible (§6).

---

## 5. Plan changes (edit your plan)

### 5.1 PAYG → Membership (upgrade) — **REUSE**

Already built: `POST /api/billing/membership/checkout` → `Pay.startYocoCheckout(order_id)` → Yoco hosted →
webhook activates (`activate_membership_for_order`). The Plan & Billing tab embeds the existing
`membership.js` upgrade card (the Buy button) verbatim. No new backend.

### 5.2 Membership → PAYG (cancel/downgrade) — **NEW (member-initiated)**

Admin can already revoke (`revoke_membership`). The member needs a self-serve cancel. v1 semantics
(simplest, honest given buy-a-month): **cancel at period end** — stop covering courts when
`current_period_end` passes; do not refund the current month.

```
POST /api/me/membership/cancel
gate can(p, "manage_own_membership")   (already defined)
- sets the member's active billing.membership_subscription to status='cancelled'
  (REUSE the SQL in admin.repositories.revoke_membership, but self-scoped to principal.user_id).
- v1: courts remain covered until current_period_end (cancellation just stops re-buy/renew intent);
  OR immediate revert (decide — see open question §8). Recommend: keep covered until period end.
200 -> {ok:true, status:"cancelled", covered_until: current_period_end}
```
Repository: `billing/me.py::cancel_own_membership(session, *, club_id, user_id)` — identical UPDATE to
`revoke_membership` but never trusts a `user_id` from the body.

### 5.3 Guardrails

- A member can only cancel **their own** active subscription.
- Re-buying after cancel goes through the existing checkout (no special path).
- The UI must clearly state "one-month membership; cancelling stops renewal, courts stay free until
  {date}." (Reuse `membership.js` copy.)

### 5.4 Membership page consolidation

The standalone `membership.html` becomes the **Plan & Billing** tab's content (keep the file as a thin
redirect or leave it; nav points to My Account). No logic change to `membership.js` — it is embedded.

---

## 6. Refund **requests** (client-initiated, admin-approved)

The brief: the client *requests* a refund from their account; an admin approves. This is **distinct** from the
existing admin direct refund (`POST /api/billing/yoco/refund`, which talks to Yoco and is record-only). The
client never triggers a money movement — they create a **request** an admin actions.

### 6.1 NEW table `billing.refund_request`

```sql
CREATE TABLE IF NOT EXISTS billing.refund_request (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    club_id       uuid NOT NULL REFERENCES club.club(id) ON DELETE CASCADE,
    order_id      uuid NOT NULL REFERENCES billing."order"(id) ON DELETE CASCADE,
    user_id       uuid,                      -- requester (iam.user.id)
    amount_minor  int,                       -- requested amount (default: full order)
    reason        text,
    status        text NOT NULL DEFAULT 'requested'
                    CHECK (status IN ('requested','approved','declined','refunded','cancelled')),
    decided_by    uuid,                      -- admin user_id
    decided_at    timestamptz,
    admin_note    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_refund_request_club_status ON billing.refund_request (club_id, status);
CREATE INDEX IF NOT EXISTS ix_refund_request_order ON billing.refund_request (order_id);
```

### 6.2 Member endpoints (`me/`)

Gate: NEW verb `request_refund` (member+; add to `iam/permissions.py` near `view_own_ledger`). Ownership: the
order must belong to the requester (`order.user_id == principal.user_id`).

```
POST /api/me/refund-requests
  body {order_id (req), amount_minor?, reason?}
  - 404 if order not the caller's; 409 if order.status not in ('paid',) (only paid orders are refundable);
  - 409 if an open ('requested'|'approved') request already exists for the order (one open at a time).
  201 -> {refund_request:{id, order_id, amount_minor, status:"requested", created_at}}

GET  /api/me/refund-requests          -> {requests:[…], count}   (the caller's own)
POST /api/me/refund-requests/<id>/cancel  -> member withdraws a still-'requested' one. 200 {ok}
```

### 6.3 Admin endpoints (`admin/`)

Gate: `take_pay_at_court` (the existing refund/desk verb).

```
GET  /api/admin/refund-requests?status=requested   -> list for the club (join order + payer email)
POST /api/admin/refund-requests/<id>/approve  body {amount_minor?, admin_note?}
     - marks status='approved', stamps decided_by/at;
     - THEN calls the existing gateway refund (POST-equivalent of billing/yoco/refund logic) when the
       order was paid online → on the refund.succeeded webhook, set status='refunded'.
     - For desk/manual orders, approval may just record an adjustment (ledger.post_adjustment) — admin choice.
POST /api/admin/refund-requests/<id>/decline  body {admin_note?}  -> status='declined'.
```

**Important boundary (REUSE the existing decision):** approving never auto-reverses the booking
(docs/05 §8). The refund is a money record; the booking stays unless the admin also cancels it. The
`refund_request` lifecycle ends at `refunded` when the Yoco `refund.succeeded` webhook lands (the same path
`apply_payment_event(kind='refunded')` already handles) — the approve step can stamp `refunded` directly for
manual/desk orders.

### 6.4 Notifications

Best-effort emits (guarded): `refund_requested` (→ admin), `refund_decided` (→ member), via
`marketing_crm.tracking.emit` + SES, mirroring coach-invite. No minor PII in payloads.

---

## 7. UX

### 7.1 Page structure

`my-account.html` (portal shell + `#cf-topbar` + `#cf-main`), boots via `Portal.boot({active:"/my-account.html",
onReady})`. One `MyAccount` controller (`frontend/js/my_account.js`) with a tab bar — **mirror `settings.js`
exactly** (`cf-nav` tab bar, `select(k)` + `history.replaceState`, per-section render, save-per-section).

Tabs:
1. **Profile** — a `cf-card` form: read-only email (disabled input + helper), then the demographics fields in
   a `cf-grid cf-grid-2`, an Address sub-section, Emergency contact sub-section, a marketing-consent
   `cf-row` checkbox (reuse the `settings.js` payments-toggle pattern), and a **Save changes** button →
   `PATCH /api/me/profile`. Inline validation; `UI.toast` on success/error (reuse `UI.errMsg`).
2. **Family** — list of `cf-item` rows (one per dependent: name, age-from-dob, Edit/Remove). An **Add child**
   button opens a `cf-modal` (reuse the `my.js` reschedule modal pattern) with first_name/surname/dob/notes →
   `POST /api/me/dependents`. Empty state: "No children added yet."
3. **Plan & Billing** — composed of:
   - **Plan card**: current plan badge (reuse `membership.js` active/upgrade cards), renewal date, and the
     **Buy / Cancel** actions (§5).
   - **Usage** mini-stats (`cf-tiles` or stat chips): courts/lessons/classes this month.
   - **Spend**: this-month total + a small month-by-month list (`cf-list`); each paid order row gets a
     **Request refund** link → §6 modal (amount prefilled, reason textarea).
   - **Account balance** line if `monthly_account` used (reuse the `my.js` statement copy).

### 7.2 Reuse map (frontend)

| Need | Reuse |
|---|---|
| Tab bar + per-section save | `settings.js` |
| Cards / grid / fields / buttons | `cf-card, cf-grid, cf-field, cf-input, cf-select, cf-btn*` (`frontend/app/app.css`) |
| Modal (add child / refund request) | `my.js` reschedule modal markup |
| Money / dates / toasts / errors | `UI.money, UI.fmtRange, UI.toast, UI.errMsg, UI.CLUB_TZ` (`ui.js`) |
| Membership cards + buy | `membership.js` (embed) |
| Auth/api wrappers | extend `api.js` with `me*` methods; `TFAuth.apiJSON` for the rest |
| Nav entry | `portal.js` `NAV` (add "My Account", roles member/coach/admin/guest) |

### 7.3 Mobile

Single column under the existing app.css breakpoints; `cf-grid-2` collapses to one column; tab bar wraps
(`cf-nav` already wraps). Forms are full-width; modals are already mobile-friendly in `app.css`. No new
responsive primitives needed.

### 7.4 `api.js` additions (sketch)

```js
// identity/profile
getProfile: () => A().apiJSON("/api/me/profile"),
patchProfile: (b) => A().apiJSON("/api/me/profile", {method:"PATCH", body:b}),
// dependents
dependents: () => A().apiJSON("/api/me/dependents"),
addDependent: (b) => A().apiJSON("/api/me/dependents", {method:"POST", body:b}),
patchDependent: (id,b) => A().apiJSON("/api/me/dependents/"+id, {method:"PATCH", body:b}),
removeDependent: (id) => A().apiJSON("/api/me/dependents/"+id, {method:"DELETE"}),
// financials + plan + refunds
financials: () => A().apiJSON("/api/me/financials"),
myOrders: () => A().apiJSON("/api/me/orders"),
cancelMembership: () => A().apiJSON("/api/me/membership/cancel", {method:"POST", body:{}}),
requestRefund: (b) => A().apiJSON("/api/me/refund-requests", {method:"POST", body:b}),
```

---

## 8. Build phasing & open questions

### 8.1 Phasing (ordered; each independently shippable)

**Phase 1 — Profile (quick win, no cross-lane).**
`iam.user` ADD COLUMNs · `iam/repositories.py` get/patch_profile · NEW `me/` blueprint +
`GET/PATCH /api/me/profile` · `MyAccount` page with the Profile tab · nav entry · `api.js` profile methods.
Gate already exists (`manage_own_profile`). No billing/diary changes. **Ship first.**

**Phase 2 — Family/dependents (schema + small diary seam).**
`iam.dependent` DDL + login-less-user creation repo · `me/` dependent CRUD · Family tab + add-child modal ·
`book.js` "Who's playing?" dropdown (court/lesson party injection) · `diary/routes.py` enrol
`dependent_user_id` ownership branch · optional `my.js` "for {child}" tag. **Preserve** the `book.js`
online seam + `GUEST_REQUIRES_HOST` host injection.

**Phase 3 — Financials (read-only billing seam).**
`billing/me.py::member_financials` (guarded queries) · `GET /api/me/financials` (+ optional `/api/me/orders`) ·
Plan & Billing tab usage/spend/balance · embed `membership.js`. No writes.

**Phase 4 — Plan change + refund requests (writes).**
`POST /api/me/membership/cancel` (reuse revoke SQL, self-scoped) · `billing.refund_request` DDL ·
member request endpoints + `request_refund` permission verb · admin approve/decline endpoints +
`manage_refund_requests`/reuse `take_pay_at_court` · refund-request modal + admin list UI · guarded emits.

### 8.2 REUSE vs NEW (consolidated)

| Area | REUSE | NEW |
|---|---|---|
| Profile fields | `iam.user` first_name/surname/phone/email; `manage_own_profile` perm | DOB/address/emergency/consent columns; `me/` blueprint; get/patch_profile |
| Dependents | `iam.player_profile.guardian_user_id`; `add_junior` perm; `booking_party`; 1050 parent_junior pattern | `iam.dependent` table; login-less user mint; dependent CRUD; `book.js` dropdown; enrol seam |
| Financials | `membership_status`, `has_active_membership`, `account_ledger`, `order`/`payment`; admin `list_payments` shape | `billing/me.py`; `GET /api/me/financials`; `/api/me/orders` |
| Plan change | membership checkout + `Pay.startYocoCheckout`; `revoke_membership` SQL; `manage_own_membership` perm | `POST /api/me/membership/cancel` (self-scoped) |
| Refunds | Yoco refund gateway path; `apply_payment_event(kind='refunded')`; record-only/no-reverse decision | `billing.refund_request` table; member request + admin approve/decline; `request_refund` perm |
| UX | `settings.js` tabs, `my.js` modal, `membership.js` cards, `cf-*` + `ui.js` | `my-account.html`, `my_account.js`, `api.js` `me*` methods, nav entry |

### 8.3 Open questions for the owner

1. **Child billing on classes** — should a child's class enrolment bill the **guardian's** order (recommended)
   or require the child to have their own account? (Court/lesson already bill the parent owner.)
2. **Membership cancel timing** — courts covered **until period end** (recommended) vs **immediate** revert?
3. **Refund eligibility window** — any time-limit (e.g. "within 14 days of the booking") or amount cap, or
   admin-discretion only?
4. **Refund of `monthly_account` / desk orders** — approve = ledger adjustment (no gateway), vs only online
   (Yoco) orders are refundable in v1?
5. **Email change** — confirmed out of scope for self-service (admin/Clerk only)? The brief implies yes
   ("hardcoded as the client id").
6. **DOB / address required?** — brief says "demographics … etc." Spec makes only name/surname/phone required;
   confirm address/DOB stay optional.
7. **Dependent age-out** — when a child turns 18, do they convert to a full member (own login), and who
   triggers it? (v1: no automatic conversion.)
8. **Guests vs dependents** — keep the existing one-off **guest** fields on bookings *and* the new dependents
   dropdown (recommended — different use cases), or fold guests into dependents?

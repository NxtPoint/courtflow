# TESTING — end-to-end test plan (3 profiles)

A practical, tick-through checklist to validate every shipped flow with **three profiles**
(**owner/admin**, **coach**, **client/member**). Work top-to-bottom: the owner configures the club,
the coach sets up services, then the client books against them. Expected results are inline.

> **Current as-built state:** [README.md](README.md) → SYSTEM → BUSINESS-RULES → INVENTORY.
> This plan only exercises what's **built & live**. Anything failing that *isn't* in §6 (known
> limitations) is a real bug — log it (§7).

> **Automated gate (separate from this manual plan):** the backend money/booking invariants are also
> proven by scratch-DB scenario harnesses — **`python -m scripts.test_all`** runs **THREE** (each in its
> own scratch club, always rolled back, never persisted):
> - **booking** (`test_booking_scenarios`, **43** checks) — double-book refusal, coach∩court integrity,
>   recurrence/waitlist, lazy hold-expiry.
> - **billing / commercial** (`test_billing_scenarios`, **56** checks) — PAYG/membership/bundle settlement,
>   desk-payment idempotency, refunds, commission.
> - **statement reconciliation** (`test_statement_reconciliation`, **35** checks) — the unified-statement
>   money invariant: a client owes the SUM of unpaid orders with **no double-count** (ledger/arrears never
>   added in), **pay-all** settles every debt **once + idempotent** (replay = no re-charge, no double
>   commission), **partial settle** pays a ticked subset, an **abandoned settlement is reclaimed** (never
>   locks the rest), **membership-covered R0 is never owed**, **void / write-off** clears a line off the
>   balance (a paid order can't be voided), **arrears ↔ orders stay in lockstep** both directions, a **pack
>   bought offline** is usable now + shows owed, and each line carries its **category + coach name**.
>
> The manual checklist below exercises the **UI flows** on top of those proven engines.

---

## 0. Setup & environment

**Where:** the live web service (Render, Frankfurt, Free plan):
- App (after login): **`https://courtflow-web.onrender.com/portal`** (Home/cockpit). Other routes:
  `/book` · `/my` · `/plan` · `/account.html` (client) · `/coach` · `/admin` · `/settings.html` ·
  `/overview.html` · `/login`. (At go-live these move to `nextpointtennis.com`.)
- API: `https://courtflow-api.onrender.com` (the SPA calls it directly).

**The three profiles** (use **three separate Clerk accounts / emails** — a user has one role by default):
- **Owner/Admin** — the seeded platform admin **`info@nextpointtennis.com`** (full admin).
- **Coach** — a separate email, brought in via the owner's **invite** (see §1) and then onboarded.
- **Client/Member** — any other email; **auto-becomes a member** on first login (+ a 7-day free week).

**Before you start:**
- [ ] **Cold start is normal.** Free-tier services sleep after ~15 min idle; the **first call wakes them
      (~30–60s)** — you'll see a spinner then a result, or a clear timeout error (never an endless spinner).
      Not a bug. The keep-warm Action runs 07:00–21:59 SAST.
- [ ] **Payments:** online pay shows only when **Admin → Settings → Payments** is ON (per-club). Yoco is
      live — use a **Yoco test card** if the keys are in test mode, else a real card (refund after).
- [ ] Have the **in-app bell/inbox** open as you go — confirmations land there (email is dark, see §6).

---

## 1. Owner / Admin  (do this FIRST — it defines what coach & client can use)

**Onboarding & club config** (`/admin`, `/settings.html`)
- [ ] First login as owner → **onboarding wizard** (if `onboarding_completed` is false): club profile,
      location, branding, policy, **courts**, **opening hours**, **services & prices**, invite coaches.
      → completing it lands you in the admin console; re-login no longer forces the wizard.
- [ ] **Courts/resources** — add/edit/disable a court → appears in the master diary + booking picker.
- [ ] **Services & prices** — confirm seeded prices (Court 30/60/90/120 = R90/150/210/280; Lesson 30/60 =
      R250/400) and **edit one**; add a new duration → it appears as a bookable chip for the client.
- [ ] **Lifecycle (Active / Deactivated / Terminated)** — services, membership tiers and coaches share ONE
      lifecycle (filter bar + per-row **Deactivate / Reactivate / Terminate** + status chips). **Deactivate**
      a service/plan → it vanishes for clients but stays editable for you; **Terminate** → retired/soft-deleted.
- [ ] **Real delete** — delete a **coach** or **court** with **no** bookings/financial history → it's
      HARD-deleted (gone for good); one **with** history → archived instead (`outcome:'deleted'|'archived'`).
- [ ] **Classes** — create a class type + schedule a recurring/one-off session (capacity) → shows on diary.
- [ ] **Membership plans** — confirm the seeded term plans; optionally set an **access window** ("Access
      hours", e.g. weekdays 06:00–17:00) on a tier.

**Coaches & commission**
- [ ] **Invite a coach** (People/Settings → invite) → an `iam.coach_invite` is created. **Email is dark**,
      so **copy the invite link from the UI** and use it for the coach profile (see §2).
- [ ] **Coach pay** (Settings → Coach pay) — set **rent** and/or a **commission %**: club-wide, per-coach,
      and **per-service** (a lesson AND a class). → saved as `commission_rule`; the **effective %** preview
      resolves `coach+product > product > coach > club`.
- [ ] **Payments toggle** (Settings → Payments) → turn **ON** so the client can pay online.

**People, money, refunds** (after the client has booked/paid — revisit)
- [ ] **People** — open a member's **360 drawer**; **grant** a membership manually → their courts go free;
      **revoke** it.
- [ ] **Void / write-off** — in the 360 drawer's **"Outstanding"** section, **void** a mistaken charge or
      **write off** a forgiven debt → it drops off the client's statement + balance (a **paid** order can't
      be voided).
- [ ] **Financial cockpit** (Overview/Financials) — revenue by service, **commission owed + rent per coach**,
      membership MRR. Confirm a **refund** shows correctly (refunds must NOT zero out — they're counted).
- [ ] **Refunds** — Billing → Recent online payments → **"Refund only"** and **"Refund & cancel"** (the
      latter also frees the slot). Confirm the order/receipt reflect it.
- [ ] **Refund-requests** — approve/decline a client's request (see §4).
- [ ] **Business Overview** (`/overview.html` or the admin "Overview" tab) — visits/sources/**device**/
      **time-on-site**/by-country, customers, bookings, revenue, settlement mix, NPS render (web-traffic
      panels accrue from go-live).

---

## 2. Coach

**Onboarding (4-step)** — open the **invite link** from §1 → log in as the coach email:
- [ ] Step through: **profile** (bio, **photo** — paste a URL, see §6), **languages/qualifications**,
      **visibility** + **"review my bookings"** toggle, **weekly hours** (creates the coach's bookable
      resource), **services/rates** (per-duration) + **classes** + **lesson packs**. → on return, every
      field is **pre-filled**.
- [ ] Leave **review-bookings OFF** for now (test auto-confirm first); you'll flip it ON in §4.

**Services**
- [ ] Add a **second lesson duration** (e.g. 90 min) with a rate → the client sees it as a chip.
- [ ] Create a **lesson pack** (e.g. 10 × 60-min) → appears on the client's `/plan` page.

**Book for a client** (auto-confirms)
- [ ] Coach console → **"Book a session for a client"** → enter the client's email, pick a time →
      **confirms immediately**; the client gets an in-app notification and sees it in **My Bookings**
      (they can reschedule/cancel). *(No "send as proposal" — on-behalf always auto-confirms.)*

**Clients, statement, cockpit**
- [ ] **My Clients** → open a client's **360** → history **+ upcoming** sessions.
- [ ] **Statement** (month-end) → per-client paid (Yoco) + owed (arrears) = net. **Mark an arrear
      collected**; **discount / write-off** an owed line → totals update; **commission accrues** on the
      collection.
- [ ] **Cockpit** → lessons, hours, gross + **net-of-commission** earnings, fill rate, new-vs-returning,
      top clients, trend, **"lessons left on plans"**, **month-end-after-commission**.

---

## 3. Client / Member

**Sign-up & home**
- [ ] First login (new email) → **auto-enrolled as a member** + **"free week — N days left"** banner.
- [ ] **Home (`/portal`)** — action-first cockpit: quick-book, upcoming, nudges.

**Booking (full-screen `/book`)**
- [ ] **Court** — month calendar → pick a day → **inline per-duration chips** show live price (or
      **"Covered by your membership"** during the free week) → **Pay & confirm**.
- [ ] **Off-peak coverage (per slot)** — with a **windowed** membership (e.g. weekdays 06:00–16:00), only
      **in-window** court slots show **R0 / "Covered"**; **out-of-window** slots show the normal PAYG price on
      the SAME day (the display now matches what you're charged).
- [ ] **Lesson** — pick the coach (or "Any") → confirms only if **a coach AND a court are free** (the
      lesson holds a court too). Only **bookable** coaches (hours set) are offered.
- [ ] **Class** — pick a session → enrol; fill a class past capacity → **waitlist** (auto-promote on a cancel).
- [ ] **Pay online (Yoco)** — choose **online** → redirected to Yoco hosted page (card / Apple/Google Pay)
      → on success you return **confirmed** + a **receipt** link. Also try **at-court** and (if a member)
      **membership-covered R0**.
- [ ] **Book for a child** — add a **dependent** (Account → family), pick them in "Who's playing?" → booking
      is **for the child, billed to you**.

**Plan, bookings, money**
- [ ] **Plan (`/plan`)** — buy a **membership** → courts go free; buy a **pack** → wallet shows
      "X of Y sessions left". (Old `/membership` + `/packs` should **301 → /plan**.)
- [ ] **Pay rule / offline buy** — when a tier/pack allows **more than one** method you **choose**; exactly
      **one** non-online method → it checks out **immediately** (no payment prompt); **online** → Yoco. Buy a
      membership/pack **at-court / monthly** → it activates **now** and shows as an owed statement line.
- [ ] **Self-cancel** — on Account, **"Cancel membership"** a paid plan (the free trial just lapses); the
      **"Your plan"** card shows the tier + access-window summary + renew date.
- [ ] **My Bookings (`/my`)** — **reschedule** + **cancel** an upcoming booking (token credit-back / refund
      per policy); **"Add to calendar"** downloads a working **.ics** (imports into Google/Apple/Outlook).
- [ ] **Statement (unified)** — Account → **"Your statement"**: owed lines **grouped by category** (Coaching /
      Court hire / Classes / Membership / Session packs) with a subtotal each. **Pay all** → one Yoco
      settlement (every line clears at once); or **tick a subset** → **part-settle** (unticked lines stay owed).
- [ ] **Refund request** — raise one on an order → owner approves/declines (§1) → you're notified.
- [ ] **Notifications** — the bell shows booking confirmed, receipt, membership/pack, refund decisions.

---

## 4. Cross-role flows (the lesson lifecycle — needs 2 profiles live)

**Flip the coach's "review my bookings" ON** (Coach profile, §2), then:
- [ ] **Client requests a lesson** with that coach → status **`requested`**, reserves **nothing** (no court,
      no charge). Client sees "awaiting coach" in **My Bookings → Needs your attention** (can **withdraw**).
- [ ] **Coach ACCEPTS** (pending queue) → a court is auto-assigned, it settles → **`confirmed`**; client notified.
- [ ] **Coach PROPOSES a new time** on another request → status **`proposed`**; client sees it under
      **Needs your attention** → **Accept** (→ confirmed) or **Decline** (→ cancelled).
- [ ] **Coach DECLINES** a third request → **`cancelled`**; client notified.
- [ ] **Coach books on-behalf** (review ON or OFF) → still **auto-confirms** (no client acceptance step).
- [ ] **Refund round-trip** — client requests a refund → owner approves → Yoco refund executes → both see
      it; if "Refund & cancel", the slot frees.

---

## 5. Suggested order (fastest path to full coverage)
1. **Owner** §1 (config + invite coach + commission + payments ON).
2. **Coach** §2 (onboard + services + pack; review OFF).
3. **Client** §3 (sign up + book court/lesson/class + pay online + plan + my-bookings + calendar).
4. **Cross-role** §4 (flip review ON; run request→accept/propose/decline; on-behalf; refund).
5. **Owner** revisit §1 (cockpit/financials/refunds/People-360 now that there's data).

---

## 6. Known limitations during testing (do NOT log these as bugs)
- **Email is dark** (no SES/Klaviyo key) → confirmations/invites/statements are **in-app only**; the coach
  **invite link is copied from the UI**, not emailed. The `.ics` "Add to calendar" works in-app; the email
  *attachment* lands when SES is wired.
- **Coach photo upload** needs S3 → until then **paste a photo URL**.
- **Gated (review-coach) lessons** settle **pay-at-court** — no online prepay for an unconfirmed lesson.
- **Cold starts** (~30–60s first call after idle) on the Free plan — not a bug.
- **Website-traffic analytics** accrue from go-live (no historical page-views/geo).

## 7. Logging bugs (so the next session can act fast)
For each issue capture: **role** · **page/URL** · **steps** · **expected vs actual** · screenshot · any
console/network error. Drop them in a list (here, an issue tracker, or a `BUGS.md`) and the next chat can
triage straight from it. Backend remaining work is already in [OUTSTANDING.md](OUTSTANDING.md).

# TESTING — end-to-end test plan (3 profiles)

A practical, tick-through checklist to validate every shipped flow with **three profiles**
(**owner/admin**, **coach**, **client/member**). Work top-to-bottom: the owner configures the club,
the coach sets up services, then the client books against them. Expected results are inline.

> **Current as-built state:** [README.md](README.md) → SYSTEM → BUSINESS-RULES → INVENTORY.
> This plan only exercises what's **built & live**. Anything failing that *isn't* in §6 (known
> limitations) is a real bug — log it (§7).

> **Automated gate (separate from this manual plan):** **`python -m scripts.test_all`** runs the
> frontend-JS parse gate first, then THREE scratch-DB harnesses.
>
> - **frontend JS** (`check_frontend_js`) — `node --check` over every `frontend/js/**/*.js`. No DB,
>   no env, ~1s. A file that does not parse is dead in the browser *in its entirety*, so this runs
>   before anything slow. Added after `admin_app.js` shipped with real newlines inside a string and
>   took `/admin` down for 11 hours while auth was healthy the whole time — it read as a login bug.
>
> The three scratch-DB harnesses (each in its own scratch club, always rolled back, never persisted):
> - **booking** (`test_booking_scenarios`, **404** checks) — double-book refusal, coach∩court integrity,
>   recurrence/waitlist, lazy hold-expiry, off-peak per-slot pricing, court→service allocation (per-service
>   courts + pricing), **classes reserve N courts** (held + conflict guard + auto-repick) + editable, the
>   **online class seat held → lazy-expired on abandonment → waitlister promoted** (a paid seat is never
>   expired), plus the 2026-07-13/14 additions: **semi-private (squad) lessons** — per-head billing upfront
>   (one owed order per client, cancel voids all), add-a-player-later (cap/duplicate/non-lesson guards), a
>   parent's **two kids** both billed to the guardian, and the addable-player security guard (a member can't
>   bill a stranger or another family's child) — and the **payment-gate** correctness: a **card-only service**
>   refuses pay-at-court on the booking path (staff override kept), and **class enrolment** respects the
>   service's payment rule (no free/membership-covered seat conjured, card-only refuses at-court).
> - **billing / commercial** (`test_billing_scenarios`, **551** checks) — PAYG/membership/bundle settlement,
>   desk-payment idempotency, refunds, commission, refund clawback, membership-cancel-voids-order, the
>   transaction log, dispute routing, client month-end, void clears arrears, abandoned reclaim on read, the
>   booking + coach event stories, cancel-voids-order + phantom cleanup, the **client by-service breakdown**
>   (incl. the written-off + discounted states, billed vs effective), plus the 2026-07-08 booking-audit
>   additions: **strict two-tier coach/product-scoped pricing** (coach's own product ELSE shared, never
>   merged), per-service selection, **class rate-card fix** (each class bills its own price, not a cheaper
>   coach's), **cancel late-fee + paid-booking resize** (`PAID_CANNOT_EXTEND`), **lesson-reschedule court
>   auto-reassign**, **membership-covered reschedule guard** (`NOT_COVERED_AT_NEW_TIME`), **the settlement
>   whitelist** (no client-chosen `free`), **online-only**
>   and **off-platform reconcile** paths, **on-behalf token/pack draw-down**, and **a pack inherits its
>   service's payment rule** (a card-only service can't sell an owed at-court pack — the leak that let a clay
>   10-pack be taken unpaid is closed; an unrestricted pack still allows pay-at-court).
> - **statement reconciliation** (`test_statement_reconciliation`, **64** checks) — the unified-statement
>   money invariant: a client owes the SUM of unpaid orders with **no double-count** (ledger/arrears never
>   added in), **pay-all** settles every debt **once + idempotent** (replay = no re-charge, no double
>   commission), **partial settle** pays a ticked subset, an **abandoned settlement is reclaimed** (never
>   locks the rest), **membership-covered R0 is never owed**, **void / write-off** clears a line off the
>   balance (a paid order can't be voided), **arrears ↔ orders stay in lockstep** both directions, a **pack
>   bought offline** is usable now + shows owed, and each line carries its **category + coach name**.
>
> **Read-only integrity scripts (run before coach payouts / month-end, against LIVE data).** Two safe
> diagnostics — both READ-ONLY (roll back), reading `DATABASE_URL` from env (Render Shell) or a gitignored
> `.env.local`:
> - `python -m scripts.reconcile_coach_commission [YYYY-MM]` — proves **no coach is short-changed**: every
>   PAID lesson/class line carries its coach `commission_split`. Should read **CLEAN** (any listed line is a
>   collection whose split silently failed).
> - `python -m scripts.diagnose_coach_packs [name] [YYYY-MM]` — pinpoints **where each pack lands in coach
>   earnings** (its selling coach in its SALE month, else the club) — the sale-based model, for "why isn't
>   coach X's pack showing?"
>
> **Transactional email** is now **LIVE** (interim via the Ten-Fifty5 AWS account, `eu-north-1`,
> `SES_SENDER=noreply@ten-fifty5.com`): invites + booking/statement confirmations send from the club's
> From-name + Reply-To. **All 21 email kinds AUDITED + signed off 2026-07-11** — one confirm+receipt email
> per purchase (an online booking's payment email shows the rich booking block, retitled "Booking confirmed";
> pack/class payment emails suppressed for their own), exact tier/pack names, times on every receipt, aligned
> layout, Outlook-safe HTML shell, `/portal` links, coach BCC only on his own lesson/class; one canonical
> status vocabulary shared with Client 360 ([SES-SETUP.md](SES-SETUP.md) has the delivery detail). The
> booking **`.ics` attachment is OFF by choice** (`EMAIL_ICS_ENABLED=0`) — the SES key DOES carry
> `ses:SendRawEmail` (invoice PDF attachments already use it), so it's set-the-flag-to-enable;
> add-to-calendar still works in-app. Long-term CourtFlow-domain setup: [SES-SETUP.md](SES-SETUP.md).
>
> The manual checklist below exercises the **UI flows** on top of those proven engines.

---

## 0. Setup & environment

**Where:** LIVE in production (Render, Frankfurt, **Starter** plan — no cold starts):
- App (after login): **`https://nextpointtennis.com/portal`** (Home/cockpit; `courtflow-web.onrender.com`
  remains as a fallback host). Other routes:
  `/book` · `/my` · `/plan` · `/account.html` (client) · `/coach` · `/admin` · `/settings.html` ·
  `/overview.html` · `/login`. (At go-live these move to `nextpointtennis.com`.)
- API: `https://courtflow-api.onrender.com` (the SPA calls it directly).

> **AS-BUILT (2026-07-03/04): smoke the three redesigned SPAs first — all COMPLETE + LIVE.** The front-end
> is now three drill-through SPAs on ONE shared widget layer — before the flows below, confirm each landing
> surface loads and its lists drill:
> - **Client SPA at `/`** (also `/portal`, `/app`) — one page, no bottom nav; Home tiles → Book;
>   **Your sessions** + **Billing by category** rows each **drill to their full story** (booking story /
>   receipt). Old `/account.html`, `/my.html`, `/book.html` should **302 → the SPA**.
> - **Coach SPA at `/coach`** — bottom nav **Home · Schedule · Clients · Money · Setup**; **Schedule is a
>   weekly calendar** (tap a lesson → the event story, tap a class → its roster); a **Client record drills
>   BY SERVICE** → sessions → the event story. Non-coaches are bounced.
> - **Admin SPA at `/admin`** — responsive (mobile bottom-nav / desktop side-rail), **command-center Home**
>   (Today / Money / People-attention / Approvals) via `GET /api/admin/home`; **People → person 360**;
>   **Money** as Setup-style sections (incl. **Sales by day**); **Diary** on the shared Calendar widget
>   (Day/Week/Month + court/coach filters); **Setup**; **Insights** (court-utilisation heatmap + Overview).
>   Every list drills to the **ONE admin event story**. The classic tab console is at **`/admin-classic`**
  `/book` (302 into the SPA) / `/plan` / the client SPA at `/portal` / `/coach` / `/admin` / `/settings.html` /

**The three profiles** (use **three separate Clerk accounts / emails** — a user has one role by default):
- **Owner/Admin** — the seeded platform admin **`info@nextpointtennis.com`** (full admin).
- **Coach** — a separate email, brought in via the owner's **invite** (see §1) and then onboarded.
- **Client/Member** — any other email; **auto-becomes a member** on first login (+ a 7-day free week).

**Before you start:**
>   Every list drills to the **ONE admin event story**. The classic tab console was RETIRED 2026-07-18
>   (`/admin-classic` 301s to `/admin`). See [ADMIN-REDESIGN.md](ADMIN-REDESIGN.md).
- [ ] **Cold starts do NOT apply** - both web services are on the Render **Starter** plan (`render.yaml`),
      so they do not sleep. The keep-warm Action (07:00-21:59 SAST) stays as belt-and-braces and to keep
      the API awake for the OPS-guarded cron windows. A 70s `apiFetch` timeout guards any slow call.
- [ ] **Payments:** online pay shows only when **Admin -> Settings -> Payments** is ON (per-club). Yoco is

---

## 1. Owner / Admin  (do this FIRST — it defines what coach & client can use)

**Console shape** — the admin SPA (`/admin`) is a responsive drill-through: nav **Home · People · Money ·
Diary · Setup** (+ Insights), landing on **Home**. Your nav is role-focused — you land on Admin, not the
client Home. *(The classic five-tab console is preserved at `/admin-classic` for its drag-timeline; the
client Home. *(The classic five-tab console was RETIRED 2026-07-18; the
- [ ] **Home** — the **command center**: **Today at the club** (live diary) · **Money** (owed to the club ·
      net revenue · coach settlements due · active members) · **People needing attention** (new signups ·
      pending coach invites · expiring memberships) · **To approve / decide** (pending refund requests) —
      each tile drills to its section. Backed by `GET /api/admin/home`.
- [ ] **People** — roster + category slicer → **person 360** (`GET /api/admin/people/<id>`): identity/roles,
      membership grant/revoke, owed + void/write-off, payments, bookings → the event story (if coach, settlement).
- [ ] **Money** — Setup-style sections: **Sales by day** (month filter, grouped by day → txn detail) ·
      New invoice / Sales by day / Club earnings / Bookings by day / Refund requests / Club activity.
- [ ] **Diary** — the shared **Calendar widget** (Day/Week/Month, **default today**, filter per court and/or
      coach) + Classes. Any booking -> the admin event story. (Drag-timeline editing was retired with the classic console.)
- [ ] **Setup** — all club config in-app (`Widgets.Setup`): profile & payments · courts & hours · services &
      pricing · memberships · packs · coaches & commission.
- [ ] **Overview** (first-class nav tab; `#/insights` also routes here) — month pager + sub-tabs
      Traffic/Bookings/Revenue/Members/NPS/Courts (daily graphs); Traffic shows public-vs-member-area +
      logged-in visitors; Courts = the court-utilisation heatmap. Also **Money → Bookings by day**.

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
- [ ] **Invite a coach** (People/Settings -> invite) -> an `iam.coach_invite` is created. SES email is LIVE,
      so the invite emails - but you can also **copy the invite link from the UI** (see section 2).
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

**Console shape** — the coach console is now a **business cockpit**: five tabs **Dashboard · Schedule ·
Clients · Money · Setup**, landing on **Dashboard**. Nav is role-focused — the coach lands on **Coach**,
not the client Home.
- [ ] **Dashboard** — the cockpit (net-of-commission KPIs ·
      earnings trend · month-end position · top clients · upcoming), with a today-glimpse of the day's sessions.
- [ ] **Schedule** — the **week TIMELINE** (a calendar grid of the coach's lessons + classes, prev/next-week
      nav): tap a lesson → completed/no-show; tap a class → roster. Buttons: **Book for a client**, **Book for
      myself** (→ `/book/court`), block time off.
- [ ] **Money** — the month-end settlement statement (this **supersedes** the standalone `/statement.html`).
- [ ] **Setup** — sub-tabbed **Services & pricing** (incl. the club-commission card) + classes, and **My
      profile**.

**Onboarding (4-step)** — open the **invite link** from §1 → log in as the coach email:
- [ ] Step through: **profile** (bio, **photo** — paste a URL, see §6), **languages/qualifications**,
      **visibility** + **"review my bookings"** toggle, **weekly hours** (creates the coach's bookable
      resource), **services/rates** (per-duration) + **classes** + **lesson packs**. → on return, every
      field is **pre-filled**.
- [ ] Set a **preferred court** (honoured when free on a lesson, never a lock).

**Services**
- [ ] Add a **second lesson duration** (e.g. 90 min) with a rate → the client sees it as a chip.
- [ ] Create a **lesson pack** (e.g. 10 × 60-min) → appears on the client's `/plan` page.

**Classes end-to-end (fixed 2026-07-02 — previously "not bookable")**
- [ ] **Setup → Classes** → **create** a class type, then **schedule** a recurring/one-off **session**
      (capacity). → the session appears on the **coach Schedule weekly calendar**.
- [ ] Tap the class on the calendar → its **roster** opens (mark attendance / no-show).
- [ ] **Client books it** — as the client, the class now shows a bookable **session**; enrol via `/book`
      (`POST /api/diary/classes/:id/enrol`). Fill past capacity → **waitlist** (auto-promote on a cancel).
      (Classes only become bookable once a coach has scheduled at least one session.)

**Book for a client** (auto-confirms)
- [ ] Coach console → **"Book a session for a client"** → enter the client's email, pick a time →
      **confirms immediately**; the client gets an in-app notification and sees it in **My Bookings**
      (they can reschedule/cancel). *(No "send as proposal" — on-behalf always auto-confirms.)*

**Clients, statement, cockpit**
- [ ] **My Clients** → open a client's **360** → history **+ upcoming** sessions.
- [ ] **By-service money drill (SPA)** — in a client record, **Total billed** breaks down **by service**
      ("Private lesson · 60 min · 3 · R750") → tap a service → its **sessions** → tap one → **the coach event
      story**. Each session shows its REAL state: **paid / owed / written-off** (struck through) **/ discounted**
      (old price beside new) **/ covered**. The per-session **Mark collected / Discount / Write off** actions
      now live **in the event story**.
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
- [ ] **Lesson** - pick the COACH first (there is no "Any") -> confirms only if **a coach AND a court are free** (the
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
      *(Fixed 2026-07-02 — the .ics now fetches **authenticated** via `TFAuth.apiFetch` in BOTH the client and
      coach apps, so it no longer 404s against the DB-less web host.)*
- [ ] **Statement (unified)** — Account → **"Your statement"**: owed lines **grouped by category** (Coaching /
      Court hire / Classes / Membership / Session packs) with a subtotal each. **Pay all** → one Yoco
      settlement (every line clears at once); or **tick a subset** → **part-settle** (unticked lines stay owed).
- [ ] **Refund request** — raise one on an order → owner approves/declines (§1) → you're notified.
- [ ] **Notifications** — the bell shows booking confirmed, receipt, membership/pack, refund decisions.

---

## 4. Cross-role flows (the lesson + class lifecycle — needs 2 profiles live)

> **There is no approval gate.** The `requested`/`proposed` statuses and the accept / propose / decline
> actions were **deleted 2026-07-29** — a lesson reserves coach ∩ court immediately and the settlement mode
> alone decides `held` vs `confirmed`. If a coach doesn't want a time, he **reschedules or cancels**.
> Do not test for (or restore) an approval queue.

**Lesson**
- [ ] **Client books a lesson** with an at-court coach → **`confirmed`** immediately, court held, order owed.
- [ ] **Client books a lesson** with a **card-only** coach → **`held`** + an `awaiting_payment` order, and the
      client is sent to Yoco. Pay → **`confirmed`**. *(This is the case the old gate made unbookable: a gated
      lesson raised no order, so the client was never sent to checkout.)*
- [ ] **Two clients try the same slot** → exactly one wins (`SLOT_TAKEN`). The old gate reserved nothing, so
      both could hold — and both could pay.
- [ ] **The COACH is emailed `lesson_booked`** — addressed to him ("open it to reschedule or cancel"), once,
      on BOTH paths (at booking when owed; **on payment** when online). He is **not** BCC'd on the client's
      receipt any more — check he gets exactly one mail.
- [ ] **Coach reschedules** it (time and/or court) → client notified; a busy target refuses
      `COURT_NOT_AVAILABLE`, not a bare `SLOT_TAKEN`.
- [ ] **Coach cancels a PAID lesson** → it **refunds itself** (club-initiated). Then **the client cancels
      their own paid lesson** → **not** auto-refunded, but flagged `was_paid` so the club is prompted.
- [ ] **Coach books on-behalf** → auto-confirms, desk-only settlement (skips Yoco).

**Class**
- [ ] **Client enrols** → the **coach** gets `class_booked` (addressed to him, with spots-left). For an
      **online** class he gets it **only when the payment lands**, not on the unpaid hold.
- [ ] **Coach MOVES a session** ("Move" on the sessions table) → the roster is **kept**, every player gets
      `class_rescheduled` with the old AND new time, the old court frees and the new one blocks.
- [ ] **Move onto the coach's own lesson** → refused `COACH_NOT_AVAILABLE`, nothing changes.
- [ ] **Cancel a session with a PAID seat** → the money **comes back** and the email says so. *(It used to
      void — which no-ops on a paid order — so the player lost seat and money under an email promising a
      refund.)*

**Money**
- [ ] **Refund round-trip** — client requests a refund → the request opens the **transaction record**
      (`#/txn/<order_id>`), not a prompt → owner approves via the ONE refund modal → Yoco refund executes.
      Try a **partial** (e.g. R250 of R420) — it must reach Yoco as a partial and leave the order `paid`.
- [ ] **THE COACH STATEMENT** (Money → Coach statement → a coach; and the coach's own Money tab).
      Check the SETTLEMENT arithmetic reads down the page: total collected × commission − what the club
      already holds = net, and the net's SIGN flips on its own (club owes the coach when the club
      collected; the coach owes the club when he did). Check **"└ what that was"** splits the collected
      figure into lessons / class seats / **session packs** — a pack is charged in full at the SALE, so
      without that line the total looks inflated against the lessons you remember.
- [ ] **The reconcile banner must be ABSENT.** If any coach shows the red "commission entries don't
      match the settlement" warning, that is a real discrepancy — do not pay against it. Diagnose with
      `python -m scripts.diagnose_coach_statement --coach <name> --detail`, which totals the month four
      independent ways so you can see WHICH view is wrong.
- [ ] **The two pages must agree.** Money → Club earnings and the coach statement must report the same
      custody split for the same coach and month: `<banked> in your bank · <held> held by them · <owed>
      owed by clients`. Only Yoco + EFT (less reversals) is "in your bank" — anything a coach marked
      collected is HIS, and the club's commission on it is still owed BY him.
- [ ] **Two date bases, on purpose.** A lesson taught this month but paid next month is OUTSTANDING in
      this month's session log and SETTLES next month. That is the rule (commission on funds received),
      not a mismatch — the page says so; check it still does.
- [ ] **Custody direction** — a coach **"Mark collected"** on an owed lesson must move his balance
      **DOWN** (he now owes the club its commission), while a **desk payment** recorded by the owner moves it
      **UP** (the club owes him his net). See BUSINESS-RULES §6.

---

## 5. Suggested order (fastest path to full coverage)
1. **Owner** §1 (config + invite coach + commission + payments ON).
2. **Coach** §2 (onboard + services + pack).
3. **Client** §3 (sign up + book court/lesson/class + pay online + plan + my-bookings + calendar).
4. **Cross-role** §4 (lesson both settlement paths; coach notification; reschedule/cancel; class move +
   paid-cancel refund; refund round-trip incl. a partial; custody direction).
5. **Owner** revisit §1 (cockpit/financials/refunds/People-360 now that there's data).

---

## 5b. Community / Find a Game + the seat rule (2026-08-09/10 — DARK until switched on)

Design: [COMMUNITY-ENGINE.md](COMMUNITY-ENGINE.md). Needs **3 accounts**: an owner, a MEMBER with an
active membership, and a NON-MEMBER (a fresh email that has never existed here — the free week is granted
once, ever, and only to a genuinely new account).

**Switch it on in this order — the second one changes what members pay.**

- [ ] **Owner → Setup → Community & games.** Both switches read OFF. Turn on **Community features** only.
      Check the "Right now" band renders (open games · invites out · players findable · seats unpaid).
- [ ] Read the **Seats per format** card. A doubles game splits the fee **four** ways. If that isn't how
      you want doubles priced, **stop here** — everything below still works, it just prices differently.
- [ ] Turn on **Charge for every seat**. (In real life: tell the members first.)

**The member journey**

- [ ] **Member → Book a court.** A **"Who's playing?"** step appears (singles / doubles / on my own).
      Confirm the old free-guest name/email box is **gone** — an unbilled guest is the leak this closes.
- [ ] Book **singles**, add nobody, leave "let another member take the spare seat" ticked.
      → the booking confirms; the member owes **R0** (covered).
- [ ] **Member → Find a game** (`#/play`). The game is listed with **1 seat open**. Check no email address
      appears anywhere on the card.
- [ ] **Invite by email** (the non-member). They get "You're invited to play at NextPoint" naming the free week.

**The guest journey — the important one**

- [ ] Open the emailed link. `/join.html` shows the inviter's **first name**, the time, and the offer.
      It must NOT show the invitee's own email or the other players' details.
- [ ] Sign up. → seat taken, **free week granted**, "Court time is included until <date>".
- [ ] **Owner → Setup → Games & invitations → Invites.** The row reads `accepted` + **free week granted**.
      (This is the screen that answers "my friend says they never got their free week".)
- [ ] The guest's seat shows **Included** — they are covered by the trial, so **nobody owes anything**. ✅
- [ ] **Chat** on the game from both sides. Then sign in as a THIRD member and confirm the game/chat
      refuses them (`NOT_IN_GAME`).

**The money — do these deliberately**

- [ ] **Guest pays.** Expire the trial (Owner → People → the guest → membership) or use a second
      non-member who was never invited. Book **member + that non-member**:
      → the **non-member owes the whole R150**, the member owes **R0**, and the court reads **HELD**
      until they pay. The member must NOT be sent to a checkout for someone else's debt.
- [ ] **Two non-members.** Book with two PAYG players → **R75 + R75**. Pay ONE seat →
      the court is **still held** (this is the trap). Pay the second → it **confirms**.
- [ ] **The spare seat collapses.** Book a singles game, leave the seat open, then have the owner set
      Setup → Community → *Spare seat closes* to a value that puts the cutoff in the past. Wait for the
      hourly sweep (or run it by hand). → the member gets **"Nobody took the spare seat"** naming the
      amount, and is billed **R150**. Re-run the sweep: they must **NOT** be billed twice.
- [ ] **Cancel a game with an unpaid guest** → nobody is left owing anything.
- [ ] **Owner → Games & invitations → Games.** The **owed** column is the "is anyone about to play on a
      court nobody paid for?" read. Confirm it matches what you just created.
- [ ] **Statement check.** Every seat charge appears on the right person's statement, and Money → Club
      earnings still reconciles (seat orders are new rows in all of those reads).

**Levels & matching**

- [ ] Member → profile → answer the 5 level questions; tick **findable**. Confirm you are NOT discoverable
      until you tick it.
- [ ] Owner/**coach** → Setup → Games & invitations → **Players & levels** → correct a level. It reads
      "set by coach" afterwards, not "self".
- [ ] Confirm a **junior / child account never appears** in Find a Game.

**Turning it back off**

- [ ] Flip **Charge for every seat** off → the next court booking behaves exactly as it always did.
      (Existing seat debts stay — they are real orders; void them if you don't want them.)

---

## 6. Known limitations during testing (do NOT log these as bugs)
- **Email is LIVE** (interim SES via the Ten-Fifty5 AWS account) → confirmations/invites/statements now
  **send** (from the club's From-name + Reply-To) *and* land in-app. The coach invite link is also shown in
  the UI to copy. The `.ics` "Add to calendar" works in-app, but the email **attachment is OFF by choice**
  (`EMAIL_ICS_ENABLED=0`; the SES key has `ses:SendRawEmail` — invoice PDFs already attach — so flip to `1` to enable). **Klaviyo
  marketing** is still dark (no key). Long-term CourtFlow-domain setup: [SES-SETUP.md](SES-SETUP.md).
- **Coach photo upload** needs S3 → until then **paste a photo URL**.
- **Gated (review-coach) lessons** settle **pay-at-court** — no online prepay for an unconfirmed lesson.
- **Cold starts** do not apply on the **Starter** plan (both web services). Not a concern.
- **Website-traffic analytics** accrue from go-live (no historical page-views/geo).

## 7. Logging bugs (so the next session can act fast)
For each issue capture: **role** · **page/URL** · **steps** · **expected vs actual** · screenshot · any
console/network error. Drop them in a list (here, an issue tracker, or a `BUGS.md`) and the next chat can
triage straight from it. Backend remaining work is already in [OUTSTANDING.md](OUTSTANDING.md).

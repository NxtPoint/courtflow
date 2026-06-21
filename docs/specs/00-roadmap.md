# Self-Service & CRM — Master Roadmap (00)

> Synthesis of the four role/foundation specs in this folder. Written 2026-06-21 from parallel
> research where four engineers each role-played client / coach / owner / architect and scoped
> their world against the live codebase + the Ten-Fifty5 (1050) reference. **Read this first**,
> then the detail specs:
> - [`client-self-service-spec.md`](client-self-service-spec.md)
> - [`coach-self-service-spec.md`](coach-self-service-spec.md)
> - [`owner-self-service-spec.md`](owner-self-service-spec.md)
> - [`crm-and-foundations-spec.md`](crm-and-foundations-spec.md)

## The vision (owner's words)
Give the **client** the platform; give the **coach** and **owner** a full **cockpit view of their
world** (the "Uber for coaching" model — the CRM tooling we built in 1050). One self-service section
per role = **full account maintenance**, role-specific. Owner monetises coaches via **rent and/or
commission % on lessons (per lesson type)**.

## The one decision all four agents reached independently
**ONE shared CRM/analytics engine, role-scoped views.** A single cockpit data layer over
`core.usage_event` + `billing.*` + `diary.*`, every query filtered by a `scope_clause(principal)`:
coach → own `coach_user_id`, owner → `club_id`, platform → all clubs. Money/activity come from the
billing+diary **system-of-record** (not events); `core.usage_event` adds engagement/marketing extras.
This is exactly 1050's backoffice shape — we swap its `ADMIN_EMAILS` gate for our `Principal.role`.
The cockpit **UI** is Phase 2; its **foundations are Phase 1**.

## Build order (dependency-ordered)

### Phase A — Shared foundations (prerequisites; unblock every role) ⟵ do first
From `crm-and-foundations-spec.md`. Nothing role-facing ships cleanly without these:
1. **Dependents / children** — `iam.dependent` as a **login-less `iam.user`** (NULL `clerk_user_id`,
   inert to auth) tied via the already-present `player_profile.guardian_user_id`. A child becomes a
   real `booking_party.user_id` → one "Who's playing?" dropdown threads through court/lesson
   (zero backend change — `create_booking(parties=…)` already supports it) + class enrol (small
   ownership-checked branch). Spend rolls to the guardian-payer; activity to the player.
2. **Usage/spend/revenue aggregation** — canonical `crm.vw_*` views + Python helpers over
   `billing.order/payment` (money = SoR, never events) + `diary`. One schema gap: **denormalise
   `coach_user_id` onto `billing.order_line`** so coach revenue is a clean join.
3. **`scope_clause(principal)`** — the role-scoping predicate every cockpit query passes through.
4. **`billing.refund_request`** — client-raised request (pending→approved/declined→refunded),
   DISTINCT from the admin's existing direct Yoco refund (record-only).
5. **Events** — extend `contracts/events.md`: `profile_updated`, `dependent_added`, `plan_changed`,
   `refund_requested/approved/declined/processed`, `attendance_marked`, `commission_accrued`.

### Phase B — Client "My Account" (self-service)
From `client-self-service-spec.md`. Mostly self-contained; quick wins first:
1. **Profile/demographics** — email is **read-only (= the client id / ledger join key)**; everything
   else (DOB, address×5, emergency contact, marketing consent) on `iam.user` via
   `ADD COLUMN IF NOT EXISTS`. `GET/PATCH /api/me/profile`.
2. **Family** — add/edit children (Phase-A model); the "Who's playing?" dropdown in `book.js`.
3. **Financials (read)** — `GET /api/me/financials`: current plan + renewal, usage this month by
   type, spend/month (+ small history), next charge. New `billing/me.py`.
4. **Plan & refunds** — upgrade reuses the membership Yoco checkout; add a self-scoped **cancel**;
   **request refund** raises a `refund_request`.

### Phase C — Coach self-service
From `coach-self-service-spec.md`. Edit-first, cockpit later:
1. **Profile/services edit** — full field list; **FIX a real bug:** `coach.create_service` writes
   `unit='per_hour'` but the booking flow resolves per-duration `per_booking` prices
   (`diary/pricing.py`) → coach rates currently don't surface. Align to per-duration.
2. **Availability/time-off** (GET/DELETE gaps), **lessons/classes** (wire reschedule/cancel).
3. **Clients view** — DERIVED from `diary.booking.booked_by_user_id` (+ enrolments); a coach sees
   only their own slice.

### Phase D — Commission / rental engine (owner) ⟵ greenfield, the commercial core
From `owner-self-service-spec.md`. Neither CourtFlow nor 1050 has ANY commission/rent/payout schema —
this is net-new and is the heart of the business model:
- Tables: `billing.coach_agreement` (rent), `billing.commission_rule` (scoped, dated %),
  `billing.commission_split` (per-payment owner/coach decomposition), `billing.coach_ledger`
  (signed running account), `billing.coach_payout`.
- **Resolution precedence:** `coach+product > product > coach > club > 0` (specificity, then latest
  effective date — mirrors `price_for`). A "lesson type" = `billing.product(kind='lesson')`; commission
  keys off `product_id`.
- **Split computed at payment time** — fanned out inside `apply_payment_event`
  (`charge_succeeded`/`refunded`), savepoint-guarded, idempotent on `(payment_id, order_line_id,
  party_type)`. Rent accrues monthly (lazy-at-read or cron).
- **Config screen** for the owner: rent per coach + commission % (global / per lesson type / per coach).

### Phase E — Cockpits (the CRM, Phase 2 UI)
One engine (Phase A) → four role-scoped dashboards: **owner** (revenue, commissions/rent due per
coach, MRR, occupancy, top coaches), **coach** (lessons, hours, fill rate, revenue net-of-commission,
clients), **client** (their financials), **platform** (all clubs). Reuse the existing `cockpit_bp`
scaffold (its `revenue`/`occupancy`/`coach-utilisation` endpoints are stubs awaiting the SQL the specs
provide) + 1050's `marketing_crm/` patterns. Pick a chart lib.

### Phase F — Notifications / receipts
Turn on the currently-dark channels: booking/payment/membership events → SES transactional + Klaviyo
(needs `KLAVIYO_API_KEY` + an SES verified sender). Event→notification map is in the foundations spec.

## Open questions for the owner (decide before Phase D, ideally now)
**Commission model:** 1) commission on **gross or net/ex-VAT**? 2) Is rent **additive** or **offset**
against commission (whichever is greater)? 3) Do **membership-covered free courts** generate commission?
(proposed: no — gross is R0). 4) Are **classes** commissionable in v1, or lessons only? 5) On a
**refund**, claw the commission back? 6) Who eats the **Yoco gateway fee** — owner, coach, or split?
7) **Payouts** = actually move money (Yoco payouts) or **report-only** (owner settles offline)?

**Plans & refunds:** 8) Membership **cancel** = at period-end or immediate? 9) Refund **eligibility
window** + are desk/monthly orders refundable? 10) Can a **coach** approve refunds for their own
lessons, or owner-only?

**Other:** 11) Dependent **age-out at 18** → transition a child to their own login? 12) **VAT/tax**
registration + invoicing now or later? 13) Coach earnings on **comp/membership-covered** lessons —
separate coach pay-rate?

## Reuse map (1050 → here)
- CRM/cockpit shape, segmentation, Klaviyo, NPS → `marketing_crm/` + `core_db/` (already partly ported;
  cockpit endpoints are stubs to fill).
- Subscriptions/billing patterns → `models_billing.py`, `subscriptions_api.py` (membership + future
  auto-renew).
- **No 1050 commission/payout code exists** — Phase D is genuinely new (design from the owner spec).

## Notes
- Keep the multi-tenant discipline: every new table carries `club_id`; every cockpit query is
  `scope_clause`-filtered.
- No migrations framework — all new schema via idempotent boot DDL (`ADD COLUMN/CREATE TABLE IF NOT
  EXISTS`), boot-twice clean.
- These specs are the design intent; the detail docs carry field lists, endpoint signatures, table DDL,
  and aggregation SQL ready to build from.

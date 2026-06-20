# 06 — CRM & Klaviyo (confirmations + lifecycle)

> Tomo: *"ALL lessons and bookings MUST have confirmation emails using Klaviyo (assume this is still
> the best solution?)."*
>
> **Answer: yes — Klaviyo, with one nuance.** Reuse 1050's decision and machinery: **we are our own
> CRM** (`core.*` + the cockpit), **Klaviyo is the marketing/lifecycle engine**, HubSpot stays
> dormant. The nuance: a *booking confirmation* is **transactional** (must always send, regardless of
> marketing opt‑in). Klaviyo handles this fine via **event‑triggered flows / transactional sends**,
> but we keep **SES as a guaranteed fallback** for the few must‑deliver messages so a Klaviyo issue
> never loses a confirmation. Marketing emails (promos, win‑back) remain **opt‑in only**.

## 1. Architecture (ported from `marketing_crm/`)

```
diary/billing actions ─► emit canonical event ─► core.usage_event (our SoR) ─► crm_sync ─► Klaviyo
                                                          │
                                                          └─► (transactional confirmations also
                                                               fire immediately; SES fallback)
```

Port `marketing_crm/tracking/` (event emit + page beacon), `crm_sync/klaviyo.py` (self‑gates on
`KLAVIYO_API_KEY`), `consent/`, and the cockpit. Add `club_id` to events and Klaviyo profile traits
so flows segment per club (one Klaviyo account, many clubs).

## 2. Canonical events (the contract)

Define in a `contracts/events.md` (1050 pattern). Booking‑platform events:

| Event | Fired when | Drives |
|---|---|---|
| `account_created` | new member signs up | Welcome flow |
| `consent_recorded` | consent captured | sets `marketing_opt_in` |
| `booking_confirmed` | court/lesson booking confirmed | **confirmation email** + add to calendar |
| `booking_cancelled` | booking cancelled | cancellation notice (+ refund/fee note) |
| `booking_rescheduled` | time changed | updated confirmation |
| `booking_reminder` | T‑24h / T‑2h cron | reminder email |
| `class_enrolled` | enrolment confirmed | class confirmation + schedule |
| `class_waitlisted` | over capacity | "you're on the waitlist" |
| `waitlist_slot_open` | slot frees | "a slot opened — claim it" |
| `lesson_completed` | coach marks complete | feedback/NPS prompt (later), rebook nudge |
| `payment_succeeded` | online/desk payment recorded | receipt |
| `monthly_statement_ready` | invoice cron | statement email (+ pay link when online) |
| `membership_started` / `membership_lapsed` | membership lifecycle | onboarding / win‑back |
| `free_lesson_requested` | complimentary‑lesson funnel | lead nurture → conversion |
| `nps_submitted`, `feedback_submitted` | feedback | retention |

Each event carries: `club_id`, `user_id`/email (the **adult** contact), and **non‑PII** booking
details (resource name, time, price) — **never minor PII or guardian‑less child data**.

## 3. Klaviyo flows to build (copy spec lives here; assemble in Flow Builder)

> Klaviyo connector can manage **templates/campaigns via API but cannot create flows** — flows are
> wired in Klaviyo's visual Flow Builder (same constraint 1050 documented). So: build templates via
> connector, then wire triggers/delays in Flow Builder (Cowork can walk Tomo through it, or do it via
> Chrome).

**Transactional (always send):**
1. **Booking Confirmation** — trigger `booking_confirmed`. Subject: "You're booked: {{resource}} at
   {{time}}". Body: what/when/where, coach (if lesson), price + settlement note ("Pay at the court" /
   "Added to your monthly account" / "Paid"), cancel/reschedule link, add‑to‑calendar (.ics).
2. **Class Enrolment Confirmation** — trigger `class_enrolled`.
3. **Reschedule / Cancellation notices** — `booking_rescheduled` / `booking_cancelled`.
4. **Reminders** — `booking_reminder` (24h + 2h).
5. **Payment receipt / Monthly statement** — `payment_succeeded` / `monthly_statement_ready`.
6. **Waitlist** — `class_waitlisted`, `waitlist_slot_open`.

**Marketing (opt‑in only):**
7. **Welcome / activation** — `account_created` → "make your first booking" (adapt 1050's Flow 1).
8. **Free‑lesson nurture** — `free_lesson_requested` → book the lesson → convert to member.
9. **Win‑back** — `membership_lapsed` / no booking in N days.
10. **Coach/class promos** — Cardio Tennis pushes, holiday camps, socials (segmented).

## 4. Transactional vs marketing — the rule

- **Transactional** (1–6): sent regardless of `marketing_opt_in` (legitimate booking comms). Use
  Klaviyo flows triggered on the events; **mirror‑send via SES** for the single most critical
  (booking confirmation) if Klaviyo send fails, so a confirmation is never lost. Keep these out of the
  marketing‑consent gate.
- **Marketing** (7–10): only to `marketing_opt_in = true`. Honour unsubscribes instantly.

## 5. Consent, minors, deliverability (launch blockers — same as 1050)

- **Marketing‑consent capture** must exist before any marketing send (reuse `consent.js`). Set
  `marketing_opt_in` true only on explicit opt‑in.
- **Minors:** contact is always the adult guardian; no child name/DOB/photos into Klaviyo. Parental
  consent recorded at child‑add.
- **Sender setup (Tomo, no code):** authenticate the sending domain in Klaviyo (SPF/DKIM/DMARC), set a
  default sender (`bookings@nextpointtennis.com`), add a physical postal address (legal footer). Per
  club later.
- **POPIA** (South Africa): NextPoint is SA‑based — honour POPIA for consent, purpose limitation, and
  opt‑out. (1050 docs already flag POPIA for SA outreach; reuse that thinking.)

## 6. Segmentation across clubs (one Klaviyo, many clubs)

Tag every profile with a `club` property (`nextpoint`) and/or use a per‑club list
(`club.branding.klaviyo_list_id`). Flows filter on `club` so when club #2 onboards, the same flow
templates serve them, scoped to their members — **cookie‑cutter marketing**.

## 7. Reuse checklist

- [ ] Port `core_db/` (`core.*`) + `marketing_crm/{tracking,crm_sync,consent,backoffice}`.
- [ ] Add `club_id` to events + Klaviyo traits.
- [ ] Define `contracts/events.md` for the booking domain.
- [ ] Emit the events above from diary/billing code paths.
- [ ] Build Klaviyo templates (connector) for the transactional set; wire flows in Flow Builder.
- [ ] SES fallback for booking confirmation.
- [ ] Club‑admin cockpit: occupancy, revenue, coach utilisation, attendance (gold‑view style).

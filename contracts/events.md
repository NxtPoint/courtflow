# Contract: Event taxonomy (booking domain)

The canonical list of CourtFlow product events. **One name per event, everywhere** — DB
(`core.usage_event`), Klaviyo metric triggers, the cockpit. If a name isn't here, it isn't an event
yet; add it here first. Naming: `snake_case`, `object_verb` (past tense).

> **This is the producer/consumer contract.** Producers (Agent B diary, Agent C billing) only call
> `emit(event, payload)` (see below). The consumer (this lane: `core.usage_event` → Klaviyo) never
> reaches back into a producer. A future capability (e.g. Ten-Fifty5 video analysis) plugs in as just
> another producer/consumer of these events — no shared DB tables (`docs/11 §4`).

## The producer interface (LOCKED — B & C code against this)

```python
from marketing_crm.tracking import emit, EVENTS   # EVENTS is the name→constant set

emit("booking_confirmed", {
    "club_id": club_id,            # REQUIRED on every domain event (multi-tenant, docs/02 §1)
    "email":   adult_contact_email, # the ADULT contact (guardian for a minor) — used to resolve
                                    #   core.account/app_user and as the Klaviyo profile key
    # ...non-PII booking details (see each event's payload below)...
})
```

`emit(event: str, payload: dict) -> None` is **fire-and-forget**: it never raises, never blocks the
caller (work runs on a daemon thread). It writes one `core.usage_event` row (our system of record),
then best-effort forwards to Klaviyo. Off-key Klaviyo is a clean no-op. See
`marketing_crm/tracking/__init__.py` for the locked signature.

### Payload conventions (apply to every event)
- **`club_id`** (uuid str) — REQUIRED on every domain event. The tenant discriminator; it lands on
  `core.usage_event.club_id` and on the Klaviyo profile as the **`club` trait** for per-club
  segmentation (`docs/06 §6`, decision D3 — one Klaviyo, many clubs).
- **`email`** — the **adult** contact (the member, or a minor's guardian). Resolves the
  `core.account`/`core.app_user` and is the Klaviyo profile key. Optional only for anonymous events
  (e.g. `page_view`).
- **Money** in **minor units** (ZAR cents) — `amount_minor`, matches `billing.*`.
- **Times** as ISO-8601 strings (`starts_at`, `ends_at`), club-local where shown to the user.
- **NEVER** put **minor PII** (child name, DOB, photo) or guardian-less child data in a payload. The
  contact is always the adult. Non-PII booking facts only: resource name, time, price, coach display
  name, settlement mode.
- Reference linkage: pass `ref_type` + `ref_id` inside the payload (e.g.
  `{"ref_type": "booking", "ref_id": "<booking_uuid>"}`) so the `core.usage_event` row points back at
  the domain row without duplicating PII.

## Events

| event | fired when | payload (beyond `club_id`, `email`) | drives (Klaviyo flow) | txn? |
|---|---|---|---|---|
| `account_created` | new member signs up | `first_name?`, `source?`, `medium?`, `campaign?`, `role?` | **Welcome / activation** | marketing |
| `consent_recorded` | consent captured (any type) | `consent_type`, `marketing_opt_in?` | sets `marketing_opt_in` (no send) | system |
| `booking_confirmed` | court/lesson booking confirmed | `ref_type=booking`, `ref_id`, `booking_type`, `resource_name`, `starts_at`, `ends_at`, `coach_name?`, `amount_minor?`, `currency_code?`, `settlement_mode?`, `cancel_url?`, `ics_url?` | **Booking Confirmation** (+ .ics) — SES fallback | **transactional** |
| `booking_cancelled` | booking cancelled | `ref_type=booking`, `ref_id`, `resource_name`, `starts_at`, `reason?`, `fee_minor?`, `refund_minor?` | **Cancellation notice** | **transactional** |
| `booking_rescheduled` | booking time changed | `ref_type=booking`, `ref_id`, `resource_name`, `old_starts_at`, `starts_at`, `ends_at`, `coach_name?` | **Updated confirmation** | **transactional** |
| `lesson_requested` | a client books a review-coach's lesson (awaiting the coach to accept/propose/decline) | `ref_type=booking`, `ref_id`, `resource_name`, `starts_at`, `ends_at`, `coach_name?` | **Coach: review this request** | **transactional** |
| `lesson_proposed` | a lesson time is proposed to the other party (coach books on-behalf as a proposal, or a counter-proposal of a new time) | `ref_type=booking`, `ref_id`, `resource_name`, `starts_at`, `ends_at`, `coach_name?` | **Other party: accept / propose / decline** | **transactional** |
| `lesson_accepted` | the awaited party accepts a requested/proposed lesson → confirmed | `ref_type=booking`, `ref_id`, `resource_name`, `starts_at`, `ends_at`, `coach_name?` | **Confirmation** | **transactional** |
| `lesson_declined` | the awaited party declines a requested/proposed lesson → cancelled | `ref_type=booking`, `ref_id`, `resource_name`, `starts_at`, `reason?` | **Declined notice** | **transactional** |
| `booking_reminder` | T-24h / T-2h cron | `ref_type=booking`, `ref_id`, `resource_name`, `starts_at`, `coach_name?`, `window` (`24h`\|`2h`) | **Reminder** | **transactional** |
| `class_enrolled` | enrolment confirmed | `ref_type=enrolment`, `ref_id`, `class_name`, `starts_at`, `coach_name?`, `amount_minor?`, `settlement_mode?` | **Class Enrolment Confirmation** | **transactional** |
| `class_waitlisted` | enrolment over capacity | `ref_type=enrolment`, `ref_id`, `class_name`, `starts_at`, `position?` | **"You're on the waitlist"** | **transactional** |
| `waitlist_slot_open` | a slot frees on a waitlisted class/resource | `ref_type=waitlist`, `ref_id`, `class_name?`, `resource_name?`, `starts_at`, `claim_url?`, `claim_deadline?` | **"A slot opened — claim it"** | **transactional** |
| `lesson_completed` | coach marks a lesson complete | `ref_type=booking`, `ref_id`, `coach_name?`, `nps_url?`, `rebook_url?` | feedback/NPS prompt, rebook nudge | marketing |
| `payment_succeeded` | online/desk payment recorded | `ref_type=order`, `ref_id`, `amount_minor`, `currency_code`, `provider`, `for?` (what was paid) | **Payment receipt** | **transactional** |
| `monthly_statement_ready` | monthly-account invoice cron | `ref_type=order`, `ref_id`, `period`, `amount_minor`, `currency_code`, `due_date`, `pay_url?` | **Monthly statement** | **transactional** |
| `membership_started` | membership lifecycle: activated | `ref_type=membership_subscription`, `ref_id`, `plan_name`, `amount_minor?`, `currency_code?` | onboarding | marketing |
| `membership_lapsed` | membership lifecycle: lapsed/cancelled | `ref_type=membership_subscription`, `ref_id`, `plan_name`, `reason?` | **Win-back** | marketing |
| `free_lesson_requested` | complimentary-lesson funnel submitted | `first_name?`, `interest?`, `source?` | **Free-lesson nurture → convert** | marketing |
| `nps_submitted` | NPS survey answered | `score` (0-10), `bucket?` (detractor\|passive\|promoter), `comment?` | detractor follow-up | marketing |
| `feedback_submitted` | in-app feedback | `area?`, `sentiment?`, `comment?` | retention | marketing |

`page_view` is also written by the beacon (`POST /api/track/page`) but is intentionally **not**
forwarded to Klaviyo (too noisy/expensive) — DB only.

## Transactional vs marketing — the send rule (`docs/06 §4`)

The `txn?` column above is the gate the consumer (`crm_sync.forward_event`) enforces:

- **`transactional`** — legitimate booking comms. Forwarded to Klaviyo **regardless** of
  `marketing_opt_in`. `booking_confirmed` additionally has an **SES mirror-send fallback**
  (`marketing_crm/email/ses.py`) so a confirmation is never lost if the Klaviyo send fails.
- **`marketing`** — only forwarded when the resolved profile's `marketing_opt_in = true`. Honour
  unsubscribes instantly.
- **`system`** — `consent_recorded` updates state (flips `marketing_opt_in`); no marketing send is
  triggered by it.

Every event is *always* written to `core.usage_event` (the SoR) irrespective of the gate; the gate
only governs the **Klaviyo forward**.

## Klaviyo profile traits (set on forward)
Every forward upserts the Klaviyo profile keyed by `email` with at minimum:
`email`, **`club`** (= `club_id` — the per-club segmentation trait, D3), `first_name?`,
`marketing_opt_in`. Flows filter on `club` so the same templates serve club #2 scoped to its members
("cookie-cutter marketing", `docs/06 §6`).

## Where each event is emitted
- **DB:** `emit()` → `core.repositories.usage_events.record_usage(...)` → `core.usage_event`.
- **Klaviyo:** downstream of the DB write, inside the same `emit()` thread, via
  `marketing_crm.crm_sync.forward_event(...)` (gated as above). Never emitted from the browser.
- **SES:** `booking_confirmed` only, as a fallback when the Klaviyo send fails.

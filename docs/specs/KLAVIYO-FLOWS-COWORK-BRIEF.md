# Klaviyo Flows — Cowork Build Brief

> **Who does what.** Claude Code (engineering) has wired the **data + triggers**: events fire into
> Klaviyo as metrics, profiles auto-sync with segmentable traits, opted-in members auto-subscribe to
> the marketing list, and guardrails are enforced in code. **Cowork builds the creative**: templates +
> the flows in Klaviyo's visual **Flow Builder** (the API can't create flows). If a flow needs an
> event or trait that isn't listed below, tell Claude Code and it gets added — don't work around it.

## What's already wired (your raw material)

**Marketing list:** `NextPoint Members` (auto-created; new opted-in members land here with consent).
Reactivation cohort is a separate list `NextPoint Reactivation` (the 391 one-off).

**Profile traits on every synced member** (use these to segment):
- `club` = the club id · `first_name` / `last_name`
- `marketing_opt_in` (true/false — **always filter marketing on this**)
- `never_logged_in` (true = imported-but-not-activated — the dormant cohort)
- `member_status` (active / inactive)

**Events (metrics) firing into Klaviyo** — trigger flows on these:

| Metric | When | Type |
|---|---|---|
| `booking_confirmed` | court/lesson booked | transactional |
| `class_enrolled` | class enrolment | transactional |
| `booking_cancelled` / `booking_rescheduled` | booking changed | transactional |
| `class_waitlisted` / `waitlist_slot_open` | waitlist | transactional |
| `payment_succeeded` | payment recorded | transactional |
| `booking_reminder` | T-24h / T-2h | transactional ⚠️ *needs the cron enabled — not live yet; tell Claude Code before building this flow* |
| `lesson_completed` | coach marks a lesson done | marketing (NPS/rebook) |
| `membership_lapsed` / `membership_activated` | membership lifecycle | marketing |
| `account_created` | new signup | data only (not a send trigger) |
| **"Subscribed to `NextPoint Members`"** | member grants marketing consent | **the Welcome-flow trigger** |

## Flows to build (in Flow Builder)

### Transactional (mark the flow "transactional" so it sends regardless of consent)
1. **Booking Confirmation** — trigger `booking_confirmed`. What/when/where, coach (if lesson), price +
   settlement note, cancel/reschedule link, add-to-calendar.
2. **Class Enrolment Confirmation** — trigger `class_enrolled`.
3. **Cancellation / Reschedule** — triggers `booking_cancelled` / `booking_rescheduled`.
4. **Payment Receipt** — trigger `payment_succeeded`.
5. **Waitlist** — `class_waitlisted` ("you're on the list") + `waitlist_slot_open` ("a spot opened").
6. **Reminders** — `booking_reminder` (24h + 2h). ⚠️ **Hold** until Claude Code enables the reminder cron.

> Note: booking confirmations also send via SES (guaranteed fallback), so don't panic if Klaviyo is mid-setup.

### Marketing (opt-in only — Klaviyo auto-respects consent since they're subscribed)
7. **Welcome / Activation** — trigger **"Subscribed to NextPoint Members"**. "You're in — book your first
   court", link to `/login` → `/book`. 2–3 emails over the first week.
8. **Win-back / Lapsed** — trigger `membership_lapsed` OR a segment "no booking in 60 days". (The one-off
   391 reactivation campaign is already built; this is the *ongoing* version.)
9. **Post-lesson NPS / rebook** — trigger `lesson_completed`. Ask for a rating + "book your next".
10. **Membership renewal reminder** — before expiry (segment/date-triggered).

## Segments to build
- **Active + opted-in** — `member_status = active` AND `marketing_opt_in = true` (your main marketing audience).
- **Lapsed** — no booking event in 60/90 days AND `marketing_opt_in = true`.
- **Dormant / never activated** — `never_logged_in = true` AND `marketing_opt_in = true` (the reactivation cohort).
- **New members** — subscribed to NextPoint Members in the last 30 days.
- (Later) by **membership tier**.

## Guardrails — SET THESE before any big send (this is how we don't irritate people)
1. **Frequency cap** — Klaviyo → Settings → **Sending → frequency capping**: max **3 marketing emails / person /
   7 days**. Non-negotiable.
2. **Smart Sending** — ON for every campaign + marketing flow (skips anyone messaged in the last ~16h). Default on.
3. **Sunset unengaged** — build a segment "5+ emails received AND no open/click in 90 days" → **suppress** (or
   drop to a low-frequency track). Protects deliverability + sender reputation.
4. **Always filter marketing on `marketing_opt_in = true`** — build campaigns to a **segment**, not the raw list,
   so a withdrawn member is excluded even if list membership lingers (the trait updates to false on withdrawal).
5. **Quiet hours** — use flow time-delays / smart send-time; no overnight sends.
6. **Consent** — marketing only to subscribed profiles (Klaviyo enforces). Mark transactional flows "transactional".

## Templates
Build a shared **header** (NextPoint logo) + **footer** (physical postal address — legal req + Klaviyo's
auto unsubscribe link). Brand colours + the tennis look. One base template, reuse across flows.

## What NOT to do
- Don't send marketing from the raw list without the `marketing_opt_in = true` filter.
- Don't build the **reminder** flow until the cron is live (tell Claude Code).
- Don't turn off Smart Sending or the frequency cap.
- Don't email the **dormant-but-NOT-opted-in** 453 as marketing — those need a one-off *service/migration notice*
  (legitimate interest), which is a separate decision + path.

## Need an event/trait we don't have?
Tell Claude Code. Adding an emit or a profile trait is a small change — cleaner than hacking around it in Flow Builder.

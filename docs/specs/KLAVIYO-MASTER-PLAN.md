# Klaviyo Master Plan — NextPoint Tennis

> **This is the ONE source of truth for the Klaviyo programme.** It supersedes the three older docs
> (`KLAVIYO-FLOWS-COWORK-BRIEF.md`, `marketing/klaviyo/trial-conversion/TRIAL-CONVERSION-FLOW.md`,
> `…/GO-LIVE-AND-ROADMAP.md`) — those stay only for the email COPY they contain; strategy, ownership,
> and status live HERE. Keep this file current; it's what keeps Code (Tomo) and Posts (Cowork) aligned.

**Owners.** Two lanes, one plan:
- **Code (Tomo + Claude Code)** — events/traits/triggers into Klaviyo, the feedback page, guardrails in code, cohort scripts. The API **cannot** create flows or templates.
- **Posts (Cowork)** — templates + the flows in Klaviyo's visual Flow Builder, copy, sends. If a flow needs an event/trait that doesn't exist yet, **ask Code — don't work around it.**

**North-star.** ~1,000 active clients, a **5,000-email/month** Klaviyo plan. The budget is NOT the
constraint (that's ~5 sends/client/month of headroom) — **relevance + deliverability + consent** are.
Goal: convert trial-takers, re-book lapsed players, upsell court-bookers to membership, and generate
Google reviews — all without irritating anyone into unsubscribing.

---

## 1. The audience — cohorts & the consent split

Two independent axes: **consent** (can we market to them?) and **activation** (have they used the new app?).

| Cohort | Definition (traits) | ~Size | Reachable how |
|---|---|---|---|
| **Marketable** | `marketing_opt_in = true` | ~500 | Full marketing — every flow below |
| **Non-consented** | `marketing_opt_in = false` | ~500 | **Transactional only** + the ONE re-permission ask (§5) |
| **On trial** | `on_trial = true` | live | Trial-conversion flow (transactional — always sends) |
| **Dormant** | `never_logged_in = true` (imported, never logged into the app) | ~453 | Marketing only if ALSO opted-in; else re-permission |
| **Active member** | `member_status = active` | — | Renewal, review-ask, cross-sell |

> Exact live counts: run `python -m scripts.klaviyo_reactivation` (dry-run, reports cohort sizes, pushes nothing).

**The iron consent rule (POPIA + deliverability):** marketing sends **only** to `marketing_opt_in = true`,
and always to a **segment** with that filter, never the raw list. Transactional flows are marked
"transactional" in Flow Builder and send regardless. This is enforced in code too
(`marketing_crm/crm_sync/sync.py::forward_event` suppresses non-transactional events without opt-in).

---

## 2. What's already wired (raw material — don't rebuild)

**Events (metrics) firing into Klaviyo** — trigger flows on these:

| Metric | Fires when | Type | Live? |
|---|---|---|---|
| `trial_started` | 7-day free week granted (genuinely-new member) | transactional | ✅ carries `trial_ends_at` |
| `booking_confirmed` | court/lesson booked | transactional | ✅ |
| `class_enrolled` | class enrolment | transactional | ✅ |
| `booking_cancelled` / `booking_rescheduled` | booking changed | transactional | ✅ |
| `class_waitlisted` / `waitlist_slot_open` | waitlist | transactional | ✅ |
| `payment_succeeded` | payment recorded | transactional | ✅ |
| `membership_started` | member buys membership (conversion/exit signal) | marketing | ✅ emits + **flips `on_trial=false`** |
| `lesson_completed` | coach marks a lesson done | marketing | ✅ emits (carries `feedback_url`) |
| `membership_lapsed` | membership lifecycle | marketing | ✅ **daily GH Action** `membership-refill.yml` |
| `booking_reminder` | T-24h / T-2h | transactional | ✅ **hourly GH Action** `reminders.yml` — **SES sends it** (below) |
| `pack_low` | a pack drops to 1 session left | marketing | ✅ emitted on wallet draw-down (E3 top-up) |
| `promo_redeemed` | a promo code applied at checkout | marketing | ✅ emitted by the promotions engine |
| `nps_submitted` / `feedback_submitted` | member leaves feedback | marketing | ✅ emitted by the feedback page (§4) |
| `account_created` | new signup | data only | ✅ (not a send trigger) |

> **Reminders are SES-owned — do NOT build a Klaviyo reminder flow.** `booking_reminder` is transactional
> and already has a SES template (`marketing_crm.notifications`), so enabling the hourly cron makes reminders
> LIVE via SES immediately (a no-show reducer). The event still flows to Klaviyo for data, but attaching a
> Klaviyo send to it would double-message. Leave `booking_reminder` for segmentation only.

> **Forwarding fix (2026-07-18):** producer events (booking/lesson/membership) carry an iam.user UUID,
> not always an email — `forward_event` now resolves the email from the UUID, so these events actually
> reach Klaviyo (previously the forward was silently dropped when the payload had no email).

**Profile traits on every synced member** (segment on these): `club`, `first_name`/`last_name`,
`marketing_opt_in`, `on_trial`, `trial_ends_at`, `never_logged_in`, `member_status`, `role`, `signup_source`.

**Lists:** `NextPoint Members` (opted-in members auto-subscribe with consent) · `NextPoint Reactivation`
(the dormant one-off). New opted-in members auto-land in `NextPoint Members` → triggers the Welcome flow.

---

## 3. The flow catalogue — every service use-case

Each row is a flow/campaign to run. **Code** = what engineering must ship first (blank = nothing, build now).
**Posts** = Cowork builds it in Flow Builder. Status: ✅ live · ◐ in progress · 📋 queued · ⛔ blocked on code.

### A. Lifecycle (all members)
| # | Flow | Trigger | Consent | Code needed | Posts | Status |
|---|---|---|---|---|---|---|
| A1 | **Trial conversion** (5-email) | `trial_started` + delay to `trial_ends_at`+1d | transactional | — (wired) | copy done; flip Live | ◐ go-live |
| A2 | **Welcome / activation** | "Subscribed to NextPoint Members" | opt-in | — | 2–3 emails, first week → `/login`→`/book` | 📋 |
| A3 | **Re-permission** (non-consented 500) | one-off SES send → §5 cohort | *service notice* | ✅ page + script BUILT | revise copy in script; build A2 | ◐ Tomo to `--commit` |

### B. Court hire (hard + clay)
| # | Flow | Trigger | Consent | Code | Posts | Status |
|---|---|---|---|---|---|---|
| B1 | **Court booker → membership** ("the maths") | segment: ≥2 PAYG court bookings, no membership | opt-in | — | template **`VZ8DiM`** built (R450-vs-R220 maths) | ◐ **wire on 1st `booking_confirmed`** (need it to build the segment) |
| B2 | **Clay-court showcase** | segment: opted-in, never booked clay | opt-in | — | "only clay in Gauteng" cross-sell | 📋 |

### C. Lessons & coaching
| # | Flow | Trigger | Consent | Code | Posts | Status |
|---|---|---|---|---|---|---|
| C1 | **Post-lesson feedback + rebook + review** | `lesson_completed` | opt-in | **feedback page (§4) ✅** | template **`RJDzuj`** built (5 stars → `{{ event.feedback_url }}&score=1..5` + rebook CTA) | ◐ **wire on 1st `lesson_completed`** |
| C2 | **Coaching intro** (court-only members) | segment: member, no lesson in 90d | opt-in | — | "add coaching to your game" | 📋 |

### D. Classes / squads / juniors
| # | Flow | Trigger | Consent | Code | Posts | Status |
|---|---|---|---|---|---|---|
| D1 | **Class re-enrol** (term-based) | segment: enrolled last term, not this | opt-in | — | "new term's open — grab your spot" | 📋 |
| D2 | **Family / juniors cross-sell** | segment: member with dependents OR opted-in parent | opt-in | — | "the whole family, one login" | 📋 |

### E. Membership & packs
| # | Flow | Trigger | Consent | Code | Posts | Status |
|---|---|---|---|---|---|---|
| E1 | **Membership renewal reminder** | date/segment: before period end | opt-in | — | pre-expiry nudge | 📋 |
| E2 | **Membership win-back** | `membership_lapsed` | opt-in | ✅ emit live (daily) | "come back on court" | 📋 Cowork |
| E3 | **Pack running low / top-up** | `pack_low` event | opt-in | ✅ emit live | "1 session left — top up" | 📋 Cowork — build the flow |

### F. Reactivation & reviews
| # | Flow | Trigger | Consent | Code | Posts | Status |
|---|---|---|---|---|---|---|
| F1 | **Lapsed win-back** (ongoing) | segment: no booking in 60/90d + opted-in | opt-in | — | "we've missed you" + soft offer | 📋 |
| F2 | **Dormant reactivation** (opted-in) | list `NextPoint Reactivation` | opt-in | script `--commit` | "your account's ready in the new app" | 📋 |
| F3 | **Google review campaign** | segment: happy active members (§4 gate) | opt-in | feedback gate (§4) | template **`T5Ub7j`** built (direct `g.page` CTA) | ◐ **ready** — needs an audience segment (engaged/active opted-in, or NPS≥4 once feedback data exists) |

**Reminder flow (`booking_reminder`)** — ✅ **now LIVE via SES** (hourly `reminders.yml` cron → T-24h/T-2h,
deduped). **Cowork: do NOT build a Klaviyo reminder flow** — SES already sends the reminder, so a Klaviyo send
would double-message. The event is available for segmentation only.

---

## 4. Google reviews — the gated feedback loop  ✅ BUILT (Code side)

**Review link:** `https://g.page/r/Ce9nBEAMXHTpEBM/review` (env `GOOGLE_REVIEW_URL`, committed in `render.yaml`).

**Why this is a growth lever, not just a widget:** happy raters route to the Google Business Profile →
reviews + recency feed the **local map-pack ranking** (`MARKETING-ENGINE.md §6`) → more Google reach. A
click-through to Google also fires a **GA4/Ads `review_click` conversion** (the page reuses `window.cfConversion`).

**Decision:** build the internal feedback capture so scores land in **our** DB + Client-360, and route by
sentiment. This beats a bare link because unhappy players get caught privately instead of on Google.

**What shipped (Code):**
- **Page:** `courtflow-web` serves `GET /feedback?t=<token>&score=<1-5>` (`frontend/app/feedback.html`) —
  branded, no-login, never-sleeps host so an emailed link never cold-starts. Tapping a star records it and
  routes: **4–5★ → Google review CTA** (+ optional note); **1–3★ → private "how do we fix it?" form**.
- **API:** `marketing_crm/feedback/` — `POST/GET /api/feedback` on `courtflow-api`. Tokens are stateless
  **HMAC-signed** (`OPS_KEY`, no PII in the URL); one upsertable `core.nps_response` per token. 1–5★ maps to
  the table's 0–10 NPS scale (`star*2`) so the existing NPS panel stays valid.
- **Emits** `nps_submitted` / `feedback_submitted` → Klaviyo (gated) + `core.usage_event`.
- **Trigger wired:** `lesson_completed` now carries the client's `email` + a signed `feedback_url` property
  → Cowork's post-lesson flow (C1) uses `{{ event.feedback_url }}` as the star-CTA base (append `&score=N`).

**Cowork TODO:** build the C1 post-lesson email in Flow Builder (trigger `lesson_completed`, star links to
`{{ event.feedback_url }}&score=1..5`). F3 review-ask campaign to known-happy members can use the raw
`GOOGLE_REVIEW_URL` directly (no gating needed for an already-happy audience).

**How it works (C1 post-lesson email + F3 campaign):**
1. Email asks for a 1–5 rating (Klaviyo rating block or tappable stars linking to the page).
2. **4–5 → the Google review CTA** (`g.page` link). **1–3 → a private "how do we fix it?" form** → our DB.
3. Every rating writes `core.nps_response` + emits `feedback_submitted`/`nps_submitted` → appears in
   Client-360 and the admin NPS panel (already reads `core.nps_response`).

> **Google-policy note:** Google discourages pure "review gating" (only steering happy people to Google).
> The safe pattern we'll follow: **show the Google link to everyone**, AND offer the private-feedback
> path — we're not hiding the review link from unhappy users, just also giving them a quieter channel.

**Code (Tomo/Claude Code) — the `/feedback` build:**
- Tokened, no-login `GET/POST /feedback?t=<signed-token>&score=<1-5>` (mint token per-recipient, no PII in URL),
  resolve to `core.person` via the identity bridge, write `core.nps_response` (score + one-line comment).
- Emit `nps_submitted` (+ `feedback_submitted` for the verbatim) so Klaviyo can segment/branch.
- Tiny branded confirm page: 4–5 surfaces the Google CTA; 1–3 shows the "tell us more" box.
- Self-gating, idempotent, non-fatal — same discipline as the existing emit/consent forwarders.
- **Quick win meanwhile:** Cowork can drop the raw `g.page` link into the trial email-5 and any
  "we'd love your review" send TODAY — the page just makes it measured + sentiment-routed.

---

## 5. The non-consented ~500 — re-permission campaign  ✅ BUILT (Code side)

You can't *market* to them, but a **one-off service notice** to existing customers ("NextPoint has moved
to a new app — want to keep hearing from us?") is defensible as legitimate account communication, provided
it's genuinely one-off, clearly identifies us, and offers an easy opt-out. **Tomo confirms comfort with
this basis before the send (`--commit`).**

**What shipped (Code):**
- **`/subscribe` page** (`courtflow-web`, `frontend/app/subscribe.html`) — tokened, no-login. The emailed
  *"Yes, keep me posted"* tap IS the affirmative act, so landing opts them in (writes `marketing_email`
  consent to our DB + subscribes to Klaviyo → the **Welcome flow** fires), shows a "you're back in ✓", and
  **nudges them straight to Book a court** — the whole point is getting them back on court. An **undo** link
  is offered (records a withdrawal).
- **API** `POST/GET /api/subscribe` (`marketing_crm/repermission/`) — verifies the opt-in token (context
  `optin`, signed with `OPS_KEY` via the shared `marketing_crm/signing.py`), reuses
  `consent.grant_marketing_consent` for the write, emits `consent_recorded`.
- **Send script** `scripts/repermission_campaign.py` — assembles the cohort and sends each a tokened notice
  via **our own SES** (not a Klaviyo marketing blast). **Dry-run by default**; `--to you@email.com` sends a
  single test; `--limit N --commit` a batch; `--commit` the full send. **Re-run-safe** — anyone who has since
  opted in is auto-excluded.
- **Cohort:** non-consented (`marketing_opt_in` false/null) EXISTING members (have a membership), with a
  usable email, not already opted-in anywhere; test/admin addresses excluded.

**Outcome:** opt-ins graduate into the ~500 marketable pool (auto via list subscription → Welcome flow).
Non-responders stay transactional-only; **the script is a ONE-OFF — do not re-run/re-send.**

**TODO (Tomo):** confirm comfort with the service-notice basis, then run `--to` a test → eyeball the render →
`--commit`. **Cowork:** the email copy lives in `repermission_campaign.py` (`_email_html`/`_email_text`) —
revise the wording there if you want; build the **Welcome flow (A2)** so opt-ins land somewhere warm.

---

## 6. Code backlog — ✅ CLEARED (every flow is now unblocked)

| # | Task | Unblocks | Status |
|---|---|---|---|
| 1 | `/feedback` page + `nps_submitted` emit + sentiment route (§4) | C1, F3, Google reviews | ✅ BUILT |
| 2 | Re-permission page + send script (§5) | A3 | ✅ BUILT |
| 3 | Flip `on_trial = false` on `membership_started` | A1 "Unconverted" segment | ✅ BUILT |
| 4 | Enable `membership_lapsed` emit (daily `membership-refill.yml`) | E2 win-back | ✅ BUILT |
| 5 | Reminder cron → `booking_reminder` (hourly `reminders.yml`; **SES sends it**) | reminders | ✅ BUILT |
| 6 | `pack_low` event on wallet draw-down | E3 top-up | ✅ BUILT |

**🎉 The Code side of the roadmap is DONE.** Every event/trait/trigger any flow below needs is live. There is
no remaining engineering blocker — the rest is Cowork building templates + flows in Flow Builder.

---

## 6b. ⭐ COWORK BUILD CHECKLIST — everything below is unblocked (build in this order)

> **Message to Cowork:** all data is wired. Nothing here is waiting on Code. Build in priority order; if a
> flow needs an event/trait/property you can't find, add a note here and Code will wire it (don't work around
> it). Reserve the guardrails in §7 before any big send.

**Do first (highest ROI, all triggers live):**
1. **A1 Trial conversion** — flip the 5 emails to Live + "Review and turn on" (copy already approved).
2. **A2 Welcome / activation** — trigger *Subscribed to `NextPoint Members`*. This catches the re-permission
   opt-ins (§5) + every new consented member. Highest urgency: the §5 send already went out.
3. **C1 Post-lesson feedback + rebook** — trigger `lesson_completed`; star links to `{{ event.feedback_url }}
   &score=1..5` (the gated review page does the rest). 
4. **E2 Membership win-back** — trigger `membership_lapsed` (fires daily now).

**Then (segment-based — build the segment, then the campaign/flow):**
5. **F1 Lapsed win-back** — segment: no `booking_confirmed` in 60/90d + `marketing_opt_in`.
6. **B1 Court→membership "maths"** — segment: ≥2 `booking_confirmed` (booking_type=court, settlement≠membership_covered), no `membership_started`.
7. **E1 Membership renewal** — segment/date before period end.
8. **E3 Pack top-up** — trigger `pack_low` (fires at 1 session left).
9. **F3 Google review campaign** — template `T5Ub7j` built; point it at an engaged/active opted-in segment.
10. **C2 Coaching intro · D1 Class re-enrol · D2 Family/juniors · B2 Clay showcase** — segment-based, build as capacity allows.

**Do NOT build:** a Klaviyo **reminder** flow (`booking_reminder` is SES-owned — see §3 note; a Klaviyo send
would double-message).

**Segmentation cheat-sheet** — the properties on the events you'll segment on:
- `booking_confirmed` / `booking_reminder` carry `booking_type` (court/lesson/class) + `settlement_mode`
  (so PAYG vs `membership_covered` is distinguishable) + `starts_at`.
- `membership_started` flips `on_trial=false` → "Unconverted trial" = `on_trial=true AND NOT membership_started`.
- `pack_low` carries `tokens_remaining` + `product_id`.
- `promo_redeemed` carries `code` + `discount_minor` + `scope` (measure a campaign's promo → sales).

---

## 7. Guardrails — SET before any big send (non-negotiable)
1. **Frequency cap:** max **3 marketing emails / person / 7 days** (Klaviyo → Settings → Sending).
2. **Smart Sending** ON for every campaign + marketing flow.
3. **Sunset unengaged:** segment "5+ emails, no open/click in 90d" → suppress. Protects deliverability.
4. **Always filter marketing on `marketing_opt_in = true`** — segment, not raw list.
5. **Quiet hours:** 08:00–19:00 SAST; no overnight sends.
6. **Transactional flows** marked "transactional" so they deliver regardless of consent.
7. **UTMs on every CTA** (`utm_source=klaviyo&utm_medium=email&utm_campaign=<flow>&utm_content=<email>`) so
   conversions show in GA4 / Google Ads. Primary CTA is `https://nextpointtennis.com/login`.

---

## 7b. Copy & brand rules (apply to ALL emails — Posts + any Code-generated content)
1. **No coach names in emails.** Do not name individual coaches (e.g. no "Neville Godwin" / "Ross Nemeth")
   and no personal accolades like "2017 ATP Coach of the Year" — we grow all coaches equally. Use generic
   **"ATP-level coaching" / "tour-level pros and specialist junior coaches."** (The trial-3 and welcome-2
   emails were corrected to this on 2026-07-18.)
2. **Never promise free lessons or free coaching.** Coaching/classes are pay-as-you-go / member-rate. The
   only "free" we claim is *membership makes court bookings free*.
3. Established club, **new app** — never "new business" / "we've launched" / "founding member."
4. Prices in Rand (R). Membership from **R220/month** (covers courts only). Courts from R90, lessons from R250.

## 7c. Posts ↔ Code sync (keep this current — it's our handshake)
- **Posts has built (templates ready in Klaviyo, no Code needed to wire):** A1 trial (live), A2 Welcome (built,
  Draft), Member-Preferences 1-day (built, Draft), preferences one-off campaign (draft). **Pre-built and
  waiting on a trigger/segment:** C1 `RJDzuj`, B1 `VZ8DiM`, F3 `T5Ub7j`.
- **What Posts needs from Code to light these up (in priority):**
  1. **Fire the first `lesson_completed`** (a coach marks any lesson done — even a test) → the metric appears
     in Klaviyo → Posts wires **C1** (the star-rating email uses `{{ event.feedback_url }}`, which Code already
     attaches to `lesson_completed`).
  2. **Fire the first `booking_confirmed`** (one court booking) → Posts builds the **B1** court-booker segment.
  3. `membership_started` + flip `on_trial=false` (§6.3) → unblocks the trial converter-guard + Jan "Unconverted"
     segment.
  4. `membership_lapsed` emit (§6.4) → E2 win-back.
- **Context (2026-07-18):** Code is mid-way on a **promo/discount engine** (coupons) — will return to the
  roadmap after. When coupon support lands, Posts can wire the **Jan 20%-off** campaign (§5/§6) and any offer
  emails. No blocker on Posts' side meanwhile; we keep pre-building templates.
- **Guardrails still to set (Posts, in Klaviyo settings):** frequency cap 3/7d, Smart Sending default,
  sunset-unengaged segment (§7).

## 8. Measurement
- **Conversion:** `membership_started` metric + the `utm_campaign` in GA4. The trial flow's success =
  trial→membership rate.
- **Reviews:** count of `g.page` clicks (Klaviyo) + new Google reviews + `nps_submitted` volume/score.
- **Re-permission:** opt-in rate on the §5 send (new `marketing_opt_in=true` after it).
- **Health:** unsubscribe rate < 0.5%/send, spam complaints ~0, open rate trend.

---

## 9. Status board (keep this current)

| Item | Owner | State |
|---|---|---|
| Trial-conversion 5-email flow (A1) | Posts | ✅ LIVE |
| Member Preferences flow (1-day after signup) | Posts | ✅ built (Draft → flip Live) · trigger `trial_started`→wait 1d→prefs email |
| Preferences one-off to the ~38 trial cohort | Posts | ◐ campaign `01KX8EV9…` ready → Tomo sends |
| Add existing ~38 into the live 5-email flow ("Add past profiles") | Posts | 📋 queued |
| Verified sender `info@nextpointtennis.com` | Tomo | ✅ |
| `/feedback` page + review gate (§4) | Code | ✅ BUILT — Cowork builds the C1 post-lesson flow |
| Re-permission page + send to the 500 (§5) | Code | ✅ BUILT — Tomo: test `--to` → confirm basis → `--commit` |
| `on_trial=false` on conversion (§6.3) | Code | ✅ BUILT |
| `membership_lapsed` emit — daily `membership-refill.yml` (§6.4) | Code | ✅ BUILT |
| Reminders live via SES — hourly `reminders.yml` (booking_reminder) | Code | ✅ BUILT — Cowork: don't build a Klaviyo reminder flow |
| `pack_low` emit on wallet draw-down (E3) | Code | ✅ BUILT — Cowork builds the top-up flow |
| **Code side of the roadmap** | Code | ✅ **DONE — every trigger wired; see §6b checklist** |
| Welcome / activation flow (A2) | Posts | ✅ built (Draft → flip Live) · trigger = Added to `NextPoint Members` → Welcome 1 (immediate) → wait 2d → Welcome 2 |
| Court→membership "maths" (B1) | Posts | 📋 |
| Guardrails (freq cap, sunset, smart send) | Posts | 📋 verify all set |

---

*Detailed email COPY for the trial flow lives in `marketing/klaviyo/trial-conversion/` (email-1…5). New
flow copy: Cowork writes it in Flow Builder; drop a source-of-truth copy alongside if useful. Update the
status board here as things ship.*

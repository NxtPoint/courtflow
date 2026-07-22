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
| B1 | **Court booker → membership** ("the maths") | Added to segment **`SZ3UFX`** | opt-in | — | template **`VZ8DiM`** built (R450-vs-R220 maths) | ✅ **BUILT — flow `Rrs48q`, in DRAFT** (Trigger→Wait 1 day→email; no re-entry; Smart Sending). **Tomo: review + turn on.** |
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
| F3 | **Google review campaign** | segment: happy active members (§4 gate) | opt-in | feedback gate (§4) | template **`T5Ub7j`** built (direct `g.page` CTA) | ✅ **BUILT — campaign `01KXV8ZYTDXHJWYGMZDKMM0QTP`, in DRAFT**; audience = Engaged segment **`YcX4pB`**. **Tomo: review render + count → Send.** |

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

**🎉 The Code side of the roadmap is DONE — WITH ONE EXCEPTION found 2026-07-22.** Every event/trait/trigger
below is live **except `membership_started`, which is wired to a code path that never runs on the live
platform** (proof + consequences in **§7f**). So there IS one remaining engineering blocker, and the
`on_trial=false` conversion flip that depends on it is built-but-dead.

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
6. ✅ **B1 Court→membership "maths"** — **BUILT** (flow `Rrs48q`, Draft; segment `SZ3UFX` = ≥2 `booking_confirmed`, booking_type=court, settlement≠membership_covered — which excludes members + trialists without needing `membership_started`).
7. **E1 Membership renewal** — segment/date before period end.
8. **E3 Pack top-up** — trigger `pack_low` (fires at 1 session left).
9. ✅ **F3 Google review campaign** — **BUILT** (campaign `01KXV8ZYTDXHJWYGMZDKMM0QTP`, Draft; audience = Engaged segment `YcX4pB`). Tomo sends.
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
- **Posts has built (in Klaviyo):** A1 trial (live), A2 Welcome (Draft), Member-Preferences 1-day (Draft),
  preferences one-off campaign (draft), **B1 Court→membership FLOW `Rrs48q` (Draft)** — segment `SZ3UFX` →
  Wait 1 day → email `VZ8DiM`. **Pre-built templates waiting on a trigger:** C1 `RJDzuj`, F3 `T5Ub7j`.
- **Metrics now live in Klaviyo (fired ≥once):** `trial_started`, `booking_confirmed`, `payment_succeeded`,
  `class_enrolled`, consent events, `invoice_issued`, `refund_requested`. **Still NOT fired (so unusable as
  triggers/segments yet):** `lesson_completed`, `membership_started`, `membership_lapsed`.
- **What Posts needs from Code to light up the rest (in priority):**
  1. **Fire the first `lesson_completed`** (a coach marks any lesson done — even a test) → the metric appears
     in Klaviyo → Posts wires **C1** (the star-rating email uses `{{ event.feedback_url }}`, which Code already
     attaches to `lesson_completed`).
  2. ✅ `booking_confirmed` is firing → **B1 built** (segment `SZ3UFX` = `booking_type=court` AND
     `settlement_mode`≠`membership_covered`, count ≥2, email-subscribed — this cleanly excludes members AND
     trialists, whose court bookings are `membership_covered`). No member-exclusion gap; `membership_started`
     not required for B1.
  3. `membership_started` + flip `on_trial=false` (§6.3) → unblocks the trial converter-guard + Jan "Unconverted"
     segment.
  4. `membership_lapsed` emit (§6.4) → E2 win-back.
- **Context (2026-07-18):** Code is mid-way on a **promo/discount engine** (coupons) — will return to the
  roadmap after. When coupon support lands, Posts can wire the **Jan 20%-off** campaign (§5/§6) and any offer
  emails. No blocker on Posts' side meanwhile; we keep pre-building templates.
- **Guardrails still to set (Posts, in Klaviyo settings):** frequency cap 3/7d, Smart Sending default,
  sunset-unengaged segment (§7).

## 7d. Build log — 2026-07-18 evening (Posts / Cowork)
**Big overnight build session. Everything below is DRAFT unless noted — Tomo reviews + turns on in the morning.**

**Fully built & reviewable now:**
- **Flows:** A1 Trial (LIVE) · A2 Welcome (Draft) · Member-Preferences 1-day (Draft) · **B1 Court→membership `Rrs48q` (Draft)**.
- **Campaign (Draft, ready to send):** **F3 Google-review ask** (campaign `01KXV8ZYTDXHJWYGMZDKMM0QTP`) → audience = **Engaged segment `YcX4pB`**, template `T5Ub7j`. Tomo: Campaigns → review render + count → Send.
- **Segments (API-built):** B1 court-bookers `SZ3UFX` · **Unconverted trial `XxUZCt`** (`on_trial`=true AND `membership_started` 0×) · **Engaged `YcX4pB`** (2+ bookings, subscribed) · **Court-players-no-lesson `Rv24hw`** (court ≥1, lesson 0×, subscribed).
- **Templates (all on-brand, no coach names, no free-lesson promises):** C1 post-lesson `RJDzuj` · B1 `VZ8DiM` · F3 `T5Ub7j` · **Court-feedback `VwcB8a`** · **Cross-sell (court→lesson) `VJ5mZP`**.

**UPDATE 2026-07-19 morning (Posts):**
- ✅ **Court-experience feedback BUILT** — flow **`WSWr2C`** (Draft): trigger `booking_confirmed` → re-entry after **21 days** (the throttle) → email `VwcB8a` (the flow's ACTUAL `template_id` is **`RjGvJ4`** — Klaviyo clones a template when it's attached to a flow message; see §7e) ("How's it going at NextPoint?"). **TODO before go-live:** add trigger filter **`booking_type` equals `court`** (the Builder froze on the filter dropdown — 30-sec add when stable) so it doesn't also fire on lesson/class bookings.
- ✅ **Cross-sell (court→lesson) BUILT** — flow **`Rhsfy6`** (Draft): trigger **Added to segment `Rv24hw`** (court players, no lesson) → email `VJ5mZP` ("Loving the courts? Add a lesson."), no re-entry. (Optional: add a 1-day delay before the email — skipped to keep it simple; fires on segment entry.)
- 🗑️ Deleted a mis-built flow (a click landed on the wrong metric row → bound to `class_enrolled`); rebuilt correctly as `WSWr2C`. **Builder tip that finally worked:** the page renders at a viewport that's scaled vs the screenshot, so click a metric row using coordinates = `cssRect × (1568/window.innerWidth)`, and ALWAYS verify the chosen trigger (API `get_flow` `definition.triggers`) before saving — triggers can't be changed after save.

**UPDATE 2026-07-19 midday (Posts, autonomous continuation):**
- ✅ **Guardrail — Sunset segment BUILT** via API: **`XUkJFa`** "Sunset · unengaged 90d (suppress/win-back)" = can-receive-email-marketing AND received ≥1 email in 90d AND opened 0 AND clicked 0 in 90d. Use as a send-exclusion / win-back audience. (Populating now; fills as send history accrues.)
- ✅ **Guardrail — Smart Sending** is ON by default on every email built ("Skip recently emailed profiles" checked) — Klaviyo's per-message dedupe (16h window).
- ⚠️ **Guardrail — account frequency cap (3/7d):** no API for this, and Klaviyo has no universal account-level *email* frequency cap toggle (Smart Sending + the sunset segment are the practical controls). If your plan exposes a Sending-settings frequency cap, set it there — flagged as a manual 1-min check, not code.

**METRIC-SOURCE PROOF for why C1 + converter-guard are blocked (inspected account metrics 2026-07-19):**
Every REAL NextPoint app event flows through the **"API"** integration (`booking_confirmed`, `trial_started`, `payment_succeeded`, `membership_lapsed`, `class_enrolled`, `invoice_issued`, …). But **`lesson_completed`** (`RfeMhj`) and **`membership_started`** (`VZvpc9`) exist **ONLY under the "Klaviyo MCP Server" integration** — i.e. they are my *test* events, not the app's. Binding a flow to those = it listens only to the test source; when the real app finally emits those two via the **API** integration, Klaviyo makes a *separate* same-named metric and the flow (trigger locked at save) never fires. This is the hard blocker, now proven.

**Still blocked — needs Code to emit + one real event (then ~10 min each in the Builder):**
1. **C1 Post-lesson feedback** — shell `Y2YxEZ` (trigger UNCONFIGURED). **Unblock:** Code wires the app to emit `lesson_completed` via the **API integration** (already done for `booking_confirmed` etc.), then mark one real lesson complete → an API-source `lesson_completed` metric appears → wire trigger to THAT → re-entry 21 days → email `RJDzuj`.
2. **Trial converter-guard** — ⚠️ **harder than "fire one real event" (see §7f):** `membership_started` is
   emitted ONLY from the gateway's `subscription_active` branch, which NOTHING produces — a real NextPoint
   membership sale is a one-off order (`charge_succeeded`), never a provider subscription. **Code must move
   the emit onto the real activation path before this is unblockable.** ⚠️ **And do NOT send to the
   Unconverted-trial segment `XxUZCt` until it is fixed** — that segment is `on_trial`=true AND
   `membership_started` 0×, and since NOBODY has that event, **members who DID convert are still in it** and
   would get a "you haven't converted yet" pitch.

**⚠️ Metric-source caveat (Code + Posts, important):** Klaviyo keys a metric by (name, **source/integration**). Code's real events land under the **"API"** integration. To unblock C1's trigger tonight Posts fired **test** `lesson_completed` + `membership_started` events (profile **`cowork-flowtest@nextpointtennis.com`**, `is_test`=true). The first attempt created them under the **"Klaviyo MCP Server"** source (wrong); a second used `service="api"`. **Before turning C1 live, verify its trigger is bound to the SAME `lesson_completed` metric Code emits** (check the metric's source = API) — re-point if needed. Cleanest long-term: wire C1's trigger after Code's FIRST real `lesson_completed` fires, then the source is guaranteed correct.

**🧹 Test artifacts to ignore/clean:** profile `cowork-flowtest@nextpointtennis.com`; any `lesson_completed`/`membership_started` events tagged `is_test:true`; a duplicate MCP-sourced `lesson_completed` metric may exist. None are in marketing segments (test profile isn't subscribed).

## 7e. WSWr2C verification — 2026-07-22 (Claude Code, read-only via the Klaviyo API)

Inspected the saved flow to establish whether the missing trigger filter forces a **rebuild** (§7d warns
triggers can't be changed after save). **It does not — the trigger and the email are both correct. The filter
is the only gap.** Saved state:

```
WSWr2C  "Court feedback (throttled)"   status: draft
  trigger:  metric Snn8dN  ->  booking_confirmed, integration {key:"api", name:"API"}
  filter:   null                        <-- the ONLY problem
  re-entry: 21 days
  action:   send-email WYhFpQ -> template RjGvJ4 ("Court feedback · how's the club"), smart_sending on
```

- ✅ **Trigger metric is the REAL one.** `Snn8dN` is API-sourced `booking_confirmed` — *not* the
  `Klaviyo MCP Server` test source that blocks C1 (§7d). The metric-source trap does not apply here.
- ✅ **Email content is right.** The flow sends `RjGvJ4`, not `VwcB8a` as §7d records. Klaviyo **clones a
  template when you attach it to a flow message**, so `VwcB8a` is the standalone original and `RjGvJ4` is the
  flow's live copy. Editing `VwcB8a` will NOT change what this flow sends — edit `RjGvJ4`.
- ⚠️ **`trigger_filter` is `null`** → as saved, this fires on **every** `booking_confirmed`, i.e. lesson and
  class bookers get a *court*-experience survey. This is why the status board keeps it at ◐, not ✅.

**The fix (UI only — flows are read-only over the API; there is no `update_flow`):**
`https://www.klaviyo.com/flow/WSWr2C/edit` → trigger card → **Trigger Filters** → *Add filter* →
`booking_type` **equals** `court` → Save. Then re-read the flow and confirm `trigger_filter` is no longer
`null` **before** flipping it Live — same verify-before-trusting rule §7d landed on.

> ### ⚠️ Re-checked 2026-07-22 after an attempted fix — IT DID NOT SAVE. Still open.
> `WSWr2C.trigger_filter` is **still `null`**, and `updated` is still **`2026-07-19T08:31:00Z`** — byte-identical
> to before the attempt, so Klaviyo never accepted a write. Listing every flow sorted by `-updated` confirms
> the **whole account's** most recent flow change is `Rhsfy6` at `2026-07-19T08:39:53Z`, i.e. **nothing in
> Klaviyo has been modified since 19 July** — so the edit didn't land on a lookalike flow either (there are
> only 7 flows; no duplicate "Court feedback"). The message-level `additional_filters` is also still `null`,
> so it didn't go in the wrong slot — nothing persisted at all.
>
> **Most likely the Builder froze again** — exactly the failure §7d already recorded on this same dropdown.
> When retrying: after adding the filter, **close the trigger-config panel** (Klaviyo won't enable Save while
> it's open), Save, then **hard-refresh and look at the trigger card again**. The reliable tell is the
> `updated` timestamp — if it hasn't moved, no write happened, whatever the UI shows.

## 7f. `membership_started` is DEAD CODE on the live path — 2026-07-22 (Claude Code)

§7d assumed C1 and the trial converter-guard were blocked only by "nobody has fired a real one yet". For
**`lesson_completed` that is true**. For **`membership_started` it is not** — the emit sits on a branch the
live platform never reaches, so no amount of real membership selling will ever produce it.

**The evidence:**
- `membership_started` is emitted from exactly ONE place: `billing/events.py:234`, inside
  `apply_payment_event` under `elif kind == "subscription_active":`.
- `subscription_active` appears in only three places repo-wide: that branch, a docstring, and the list of
  legal kinds in `billing/gateway.py:29`. **No adapter ever produces it.** NextPoint memberships are sold as
  **one-off orders** — Yoco fires `charge_succeeded`, then `membership.activate_membership_for_order` grants
  the term. There is no provider-managed subscription, so the branch is unreachable.
- `billing/membership.py` — the module that ACTUALLY activates every membership (`create_membership_order`,
  `activate_membership_for_order`, `_apply_term_grant`, `grant_membership`) — **emits nothing at all.**
- ✅ By contrast `lesson_completed` is genuinely fine: `diary/routes.py:534` → `diary.bookings.set_status`,
  which emits it when a lesson is marked completed. That one really does just need a coach to mark one past
  lesson done in prod.

**What this breaks (three things, all currently mis-stated as done):**
1. **The `on_trial=false` conversion flip** (`marketing_crm/crm_sync/sync.py:195`) runs ONLY on
   `event_type == "membership_started"` → it never runs. Trial members stay `on_trial=true` forever, even
   after they pay.
2. **The Unconverted-trial segment `XxUZCt`** (`on_trial`=true AND `membership_started` 0×) therefore
   **still contains members who converted**. ⚠️ **Sending the Jan offer to it as-is pitches "you haven't
   converted" at paying members.** Same class of mis-send as the WSWr2C filter gap.
3. **The trial converter-guard** can't be built on a metric that will never exist under the API source.

**The fix (CODE — deliberately NOT applied unilaterally).** Move/duplicate the emit onto the real activation
path so it fires wherever a membership actually starts — `activate_membership_for_order` (online) **and** the
offline/desk grant, keyed for idempotency the same way the term grant is (an already-active row must not
re-emit, or a webhook replay double-counts a conversion). Needs a decision on whether an **admin manual
grant** and the **7-day trial** should also count as `membership_started` (they arguably should not — the
trial is what the flag tracks). This touches live billing→CRM and, once Klaviyo flows are on, changes who
gets marketing email, so it wants a review + a harness scenario rather than a quick patch.

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
| `on_trial=false` on conversion (§6.3) | Code | ⚠️ **BUILT BUT DEAD** — the flip only runs on `membership_started`, which never fires on the live path (§7f). Fix that emit and this starts working as written |
| `membership_lapsed` emit — daily `membership-refill.yml` (§6.4) | Code | ✅ BUILT |
| Reminders live via SES — hourly `reminders.yml` (booking_reminder) | Code | ✅ BUILT — Cowork: don't build a Klaviyo reminder flow |
| `pack_low` emit on wallet draw-down (E3) | Code | ✅ BUILT — Cowork builds the top-up flow |
| **Code side of the roadmap** | Code | ✅ **DONE — every trigger wired; see §6b checklist** |
| Welcome / activation flow (A2) | Posts | ✅ built (Draft → flip Live) · trigger = Added to `NextPoint Members` → Welcome 1 (immediate) → wait 2d → Welcome 2 |
| Court→membership "maths" (B1) | Posts | ✅ built (Draft → flip Live) · flow `Rrs48q` · trigger = Added to segment `SZ3UFX` → wait 1d → email `VZ8DiM` |
| Google review campaign (F3) | Posts | ✅ built (Draft) · campaign `01KXV8ZYTDXHJWYGMZDKMM0QTP` → audience = Engaged `YcX4pB` · Tomo sends |
| Court-experience feedback | Posts | ◐ built (Draft) · flow `WSWr2C` → email `RjGvJ4`, re-entry 21d · ⚠️ **ONE BLOCKER before Live: add the trigger filter `booking_type` = `court`** — as saved it fires on lesson + class bookings too. Trigger + email VERIFIED correct (§7e) — filter is the only gap; **no rebuild needed** |
| Cross-sell court→lesson | Posts | ✅ built (Draft) · flow `Rhsfy6` · trigger = Added to segment `Rv24hw` (court players, no lesson) → email `VJ5mZP` |
| Guardrails (freq cap, sunset, smart send) | Posts | ✅ Smart Sending on + Sunset segment `XUkJFa` built · ⚠️ freq-cap = manual settings check (no API) |

---

*Detailed email COPY for the trial flow lives in `marketing/klaviyo/trial-conversion/` (email-1…5). New
flow copy: Cowork writes it in Flow Builder; drop a source-of-truth copy alongside if useful. Update the
status board here as things ship.*

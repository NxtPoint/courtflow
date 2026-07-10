# Client-360 & CRM — Data Foundation Plan + Roadmap

> **Status:** PLANNING (analysis only — no code changed). Written from a full read of both repos
> (`C:\dev\nextpoint` + `C:\dev\webhook-server`) on 2026-07-10.
> **Owner ask:** grow 907 people → thousands, convert them, keep courts ~95% full. Data foundation
> first, marketing engine second. People tab = single source of truth. Maximise reuse across
> NextPoint + ten-fifty5 (and future white-label clubs). Kill duplication.
> **Golden-rule pushback is in §1 — read it before the roadmap.**

---

## 0. What was read (confirmation)

**NextPoint (`C:\dev\nextpoint`) — code:** `iam/schema.py` + `iam/repositories.py`; `core/models.py`,
`core/schema.py`, `core/repositories/{accounts,consent,usage_events,notifications}.py`;
`client360/repositories.py`; `admin/repositories.py` (`create_client`, `list_people`, `get_person`) +
`admin/routes.py`; `me/routes.py`; `auth/principal.py` + `auth/verifier.py`; `billing/activity.py`;
`marketing_crm/tracking/{client,events,beacon}.py`, `marketing_crm/crm_sync/{sync,klaviyo,hubspot}.py`,
`marketing_crm/consent/blueprint.py`, `marketing_crm/notifications.py`, `marketing_crm/email/ses.py`,
`marketing_crm/backoffice/blueprint.py`; `analytics/repositories.py` + `analytics/routes.py`;
`insights/repositories.py`; `contracts/events.md`.

**NextPoint — docs:** `CLAUDE.md`; `docs/00,02,06,10,12`; `docs/specs/{README,00-roadmap,OUTSTANDING,
crm-and-foundations-spec,TRANSACTION-RECORD}.md`.

**ten-fifty5 (`C:\dev\webhook-server`) — code + docs:** `CLAUDE.md`; `core_db/{schema,models}.py`;
`analytics/traffic.py`; `auth_v2/verifier.py`; `coach_invite/email_sender.py`;
`marketing_crm/tracking/{beacon,client}.py`; `seo/sites.py`.

---

## 1. Honest reframe (Golden Rule #3 — push back where a better option exists)

The brief describes Mission 1 as if we are building a data foundation from scratch: *"we have nameless
clients, no NPS, we don't track clicks/preferences, expand the ledger into a timeline."* **The as-is is
much further along than that framing** — and mis-framing it costs us months.

**What already exists in NextPoint (feature-complete, live in prod):**
- A CRM **event spine** — `marketing_crm/tracking/client.py::emit(event, payload)` writes `core.usage_event`,
  fires in-app + SES notifications, and forwards to Klaviyo. Booking/class/membership/refund events emit today.
- A **single client read-model** — `client360.get_client_360()` (shipped 2026-07-09) composes identity,
  membership, packages, statement, payments, bookings, dependents, refunds, coaching, activity into one
  payload; `admin.get_person` delegates to it. This *is* the People-tab backbone the brief wants.
- The **CRM spine tables** — `core.account/app_user/person/consent/nps_response/usage_event` (ported from
  1050), plus a full **consent** capture surface (`/api/consent/*`, minors/parental included).
- A **money timeline** — `billing.activity.transaction_log` composes ~7 money tables into one chronological,
  role-scoped, never-drifts feed.
- **Analytics + insights dashboards** — traffic / bookings / revenue / members / NPS / courts, live in the
  admin console (`/api/insights/overview`).
- **Klaviyo** — fully wired, **self-gating on `KLAVIYO_API_KEY`** (i.e. one env var from live).

**So Mission 1 is not "build capture." It is four sharper problems:**

| # | Real problem | Not "build a data lake" but… |
|---|---|---|
| **A** | **Two disconnected identity graphs.** The live People record is `iam.user` (UUID, Clerk). The CRM spine (`core.person/usage_event/consent/nps`) is a *separate* graph (bigint, email-keyed) that `client360` **never reads**. They share no key but an email string. | **Unify identity** — one keystone bridge. |
| **B** | **No minimum-data enforcement.** Every `iam.user` identity column is nullable; email isn't unique. Nameless + phone-less records are created routinely (see §3). | **Enforce + backfill**, not capture-from-zero. |
| **C** | **Two half-timelines, neither is the activity feed.** `billing.activity` = money only; `core.usage_event` = product/marketing events but anonymous + unread by the 360. | **Merge + attribute**, not build-new. |
| **D** | **The rich stuff is wired but dark.** Klaviyo (no key), NPS (table exists, zero callers, no submit UI), interaction depth (beacon captures only `page_view`/`page_leave`). | **Activate + extend**, not architect. |

**Bottom line:** the foundation is ~70% built. The work is *unify → enforce → merge → activate*, measured in
weeks, not months — which lets us reach Mission 2 far sooner. **The keystone is problem A** (§4, Slice 0):
until `iam.user` and `core.person` are one graph, every CRM signal we capture is invisible to the People tab,
and every line of shared CRM code (which is written against `core.*`) stays dark in NextPoint.

**One more reframe on reuse.** The brief says "NextPoint's CRM is the primary instance we retrofit to
ten-fifty5." But the CRM **originated in ten-fifty5** (`core_db/` + `marketing_crm/`) and was **copy-ported**
into NextPoint — there are now two **diverged forks** in every shared subsystem, held together by an
unenforced "edit in lock-step" social contract that has already drifted. "NextPoint as primary" is the right
*target*, but the honest job is **converge two forks behind a real shared-code mechanism**, not "build once,
retrofit." See §6.

---

## 2. The central architecture finding — two identity graphs

```
  LIVE (what the People tab shows)          DARK (what the CRM code writes)
  ┌─────────────────────────────┐           ┌──────────────────────────────┐
  │ iam.user (uuid, Clerk)      │  email    │ core.account (bigint)        │
  │  ├ iam.membership (club,role)│  string   │  ├ core.app_user (bigint)    │
  │  ├ iam.coach_profile         │◀ · · · · ▶│  └ core.person (bigint) ◀────┼── richest profile
  │  ├ iam.player_profile        │  (only    │       (dob/utr/hand/country/ │   (dob,utr,hand,
  │  └ iam.dependent             │   link)   │        skill/notes/photo)    │    country,skill,
  └─────────────────────────────┘           │ core.usage_event (events)    │    photo) — NEVER
     read by: client360, admin,             │ core.consent  core.nps_*     │    read by client360
     me/*, billing.activity                 └──────────────────────────────┘
                                               read by: consent write-path ONLY
```

- **No foreign key** joins the two graphs — only a shared, non-unique, sometimes-NULL `email` string.
- `core.person` is the **richest** profile table in the codebase (dob, utr, dominant_hand, country, area,
  skill_level, notes, photo) — and it is **never surfaced** on the People tab.
- Every CRM capability the brief wants (event timeline, NPS, consent state, Klaviyo traits) is keyed to the
  **dark** graph. So capturing more of it, without the bridge, adds nothing visible to the People record.

**This is the linchpin for BOTH missions AND for cross-product reuse** (see §6): the shared CRM/analytics/
consent code is all written against `core.*`. Bridging `iam.user → core.person` simultaneously (a) completes
the Client-360, and (b) lights up the shared code in NextPoint. One move, three payoffs.

---

## 3. Data-quality gaps (confirmed against live code)

| Gap | Where it happens | Evidence | Severity |
|---|---|---|---|
| **Nameless client** | Clerk email-only signup → NULL name, then auto-enrolled active member | `auth/principal.py:117` `upsert_user_by_clerk_id`; insert `iam/repositories.py:81`; auto-enrol `:141` | High |
| **Nameless client** | Admin "New client" validates **email only** | `admin/routes.py:783`; `admin/repositories.py:773` `first_name=(first or nm) or None` | High |
| **Nameless client** | Wix import — name optional | `scripts/import_wix.py:208` | Medium (historic) |
| **No phone** | Clerk signup **never captures phone at all** (column omitted from insert) | `auth/principal.py` insert | High (phone = the WhatsApp/SMS key) |
| **No phone** | Admin create — phone optional, no check | `admin/routes.py` `b.get("phone")` | High |
| **Duplicate humans** | `iam.user.email` has **no unique constraint** (only `lower(email)` index); Clerk de-dupes by `clerk_user_id`, import by email → same human, two rows | `iam/schema.py:23` | Medium |
| **Consent flag split** | `iam.user.marketing_opt_in` (read by profile) vs `core.app_user.marketing_opt_in` (flipped by consent surface, read by Klaviyo gate) — **two booleans, disconnected** | `me/routes.py` vs `consent/blueprint.py:98` | Medium |

The DB enforces almost nothing; all minimum-data logic is app-side and inconsistent. The **only** client type
with a guaranteed name is a **dependent** (`iam.dependent.first_name NOT NULL` + `_validate_name` at
`me/routes.py:216`) — the model already proves the pattern, it's just not applied to adults.

---

## 4. Field-level Client-360 gap map

**Legend — Reuse:** `NP` = NextPoint-only; `SHARED` = should be shared with ten-fifty5 (via §6 mechanism);
`CLUB` = per-club white-label config.

### 4a. Identity & demographics

| Field | Lives in | Captured at | Missing / gap | How to close | Reuse |
|---|---|---|---|---|---|
| First / surname | `iam.user.first_name/surname` (nullable) | Clerk claim · admin create · import · `me` patch | Not enforced → nameless records | Require name at all 3 create paths; backfill 880 | SHARED |
| Email | `iam.user.email` (nullable, **non-unique**) | Clerk · admin · import · consent | Dup-human risk | Add `UNIQUE(lower(email))` (after de-dup pass); keep NULL allowed for dependents | SHARED |
| **Phone** | `iam.user.phone` (nullable) | admin (optional) · `me` patch | **Never captured at signup** | Add to Clerk signup (custom field) + post-signup completion prompt; make required-on-first-booking | SHARED |
| DOB | `iam.user.dob` **and** `player_profile.dob` **and** `dependent.dob` **and** `core.person.dob` | `me` patch · `create_dependent` | **4 homes**, not surfaced in 360 | Pick `iam.user`/`dependent` as SoT; surface in `client360._identity` | NP |
| Address ×5 | `iam.user.address_*` | `me` patch only | No admin capture; not in 360 | Add to admin editor + surface in 360 | NP |
| Emergency contact | `iam.user.emergency_contact_*` | `me` patch only | Not in 360 | Surface in 360 | NP |
| UTR / hand / skill | `iam.player_profile.*` **and** `core.person.*` | `create_dependent` defaults only | **No live write path**; not in 360 | Add editor; surface; pick one home | NP |
| Profile photo | `coach_profile.photo_url` / `core.person.profile_photo_url` | coach admin | No player photo; needs `S3_BUCKET` | Config (Tomo) + surface | NP |
| **Preferences** | — (**no table exists**) | — | No preference model at all | New `iam.preference` (or `core.person` JSONB): preferred coach/court/times/surface/comms channel | SHARED |

### 4b. CRM signals (all in the dark `core.*` graph — invisible to 360 today)

| Signal | Lives in | Captured at | Missing / gap | How to close | Reuse |
|---|---|---|---|---|---|
| Product/marketing events | `core.usage_event` | `emit()` from diary/billing | account/user/**person_id NULL** for page traffic; anon; **never read by 360** | Bridge (§4 Slice 0) → attribute events to person → add a 360 timeline block | SHARED |
| Page views | `core.usage_event` (`page_view`/`page_leave`) | beacon | Only 2 event types; anon; `anon_id` never stitched to person on login | Stitch `anon_id`→person at login; backfill pre-signup events | SHARED |
| Clicks / funnel / preferences | — | — | **Not captured** (beacon `props` bag unused) | Extend beacon (`props`) or a `track_event` endpoint: `booking_started/abandoned`, coach-profile view, search, filter, preference-change | SHARED |
| Lifecycle events | `core.usage_event` | partial | **`account_created`, `payment_succeeded`, `login` never emitted** → Klaviyo Welcome/receipt flows have no trigger | Emit them from `principal.py` / `apply_payment_event` | SHARED |
| **NPS** | `core.nps_response` (exists!) | — | **Zero callers, no submit route, no UI** | Wire post-lesson prompt → `record_nps()` + emit `nps_submitted`; surface bucket in 360 | SHARED |
| Consent state | `core.consent` | `/api/consent/*` | Not surfaced in 360; not on timeline | Add consent block + timeline entries to 360 | SHARED |
| Email engagement | — | — | No open/click/bounce/unsub (would come from Klaviyo webhooks) | Wire Klaviyo webhooks → `usage_event` | SHARED |
| Acquisition / UTM | `usage_event.metadata.utm_*` (+ `core.acquisition` in 1050) | beacon | Captured but anonymous; no gclid | Stitch to person; **capture `gclid`** for Ads offline conversions (§5) | SHARED |

### 4c. Transaction ledger → activity timeline

- **Today:** `billing.activity.transaction_log` = a read-model over money tables (payments/orders/arrears/
  commission/membership). A *money* timeline that "can never drift because it IS the money tables." Good, but
  money-only.
- **Gap:** no logins, bookings-as-events, consent changes, profile edits, NPS, or marketing touches.
- **Close it:** a **unified activity feed** = `transaction_log` (money) **⊕** `core.usage_event` scoped by
  `person_id` (behaviour) **⊕** `core.notification`/consent history — merged chronologically, surfaced as a
  new `client360` block. This is exactly the "expand the ledger into the Client-360 timeline" ask — but it's a
  **merge of two existing feeds**, gated on the identity bridge, not a new ledger table.

---

## 5. External data (Google now, Meta later) — realistic vs aspirational

**IN (enrich the profile):**
- **GSC** — already pulled by the shared `seo/` engine (keyless OAuth; registry already covers both sites).
  Feeds *content/topic* choice, not per-person enrichment. **Realistic, live.**
- **GA4** — traffic/behaviour aggregate. Per-person attribution comes from our **own** `utm_*` on
  `page_view` (already captured) once anon→person is stitched. **Realistic after Slice 0.**

**OUT (make the ad platforms perform — higher commercial value):**
- **Google Ads offline conversion import** (upload real booking/purchase conversions keyed by **gclid**) —
  teaches Ads which clicks became paying members. **Highest-ROI ad integration. Realistic** but needs: capture
  `gclid` at landing (beacon), store on person, upload via Ads API on `payment_succeeded`. Gated on Slice 0 +
  event emit.
- **Customer Match audiences** (upload hashed member/lapsed emails for targeting + exclusion of existing
  members) — **Realistic**, consent-gated.
- **Enhanced conversions** — **Realistic**, low effort once conversions flow.
- **Meta CAPI** (later) — same pattern.
- **Aspirational:** real-time bidirectional sync, predictive bid feeds — defer.

**Note:** every OUT integration depends on (a) the identity bridge and (b) `gclid`/UTM captured on the person
— i.e. they sit **after** Mission 1, at the front of the Social & Ads phase.

---

## 6. Reuse / de-duplication (Golden Rules #4, #6)

The shared subsystems are **drifted copy-paste**, not shared code. There is **one** true shared runtime
resource (the AWS SES account NextPoint rides via `SES_AWS_*` creds) and **one** correctly-shared tool (the
`seo/` GSC engine, whose `sites.py` registry already lists both sites). Everything else is duplicated and
one edit from silent breakage (the analytics "lock-step" already caused a silent-zeros incident).

**Recommendation: stand up a real shared-code mechanism** (git submodule, a private pip package, or a
CI-synced vendored `shared/`) and converge in payoff order. Prove it on the 4 low-risk modules first.

| # | Module | State | Sharing model | Effort |
|---|---|---|---|---|
| 1 | **Analytics traffic engine** (9 funcs) | byte-identical SQL, 2 files | Single shared module; each repo keeps its own `overview()` composer | **Low** — do first |
| 2 | **Beacon + its metadata contract** | drifted; contract is unenforced comments | Shared beacon body + injected `resolve_club(host)` hook (1050 → None) | Medium |
| 3 | **Auth JWT verifier** | byte-identical `verify_jwt`/JWKS | Shared `verifier.py`; per-repo `principal.py`; normalise env flag (`AUTH_V2_ENABLED`/`AUTH_ENABLED`) | Low-Med |
| 4 | **SES send primitive** | NextPoint's `ses.py` is the better generic base | Adopt it as shared; 1050's `coach_invite` becomes a caller | Medium |
| 5 | **`core.*` CRM spine** (account/person/usage_event/consent/nps/dsar/retention) | shared skeleton, structurally forked (multi-tenant + identity) | One canonical schema **superset**; 1050 accepts nullable `club_id` (sole-club default); product tables stay local | **High** (root) |
| 6 | **crm_sync (Klaviyo/HubSpot)** | near-copies, both key-gated | Single shared package | Low-Med |
| 7 | **tracking client** (`emit`/`track`) | forked signature (Klaviyo vs Amplitude) | Shared core + pluggable forwarder + id-resolver | Med-High |

**Do NOT unify** (correct, deliberate forks): Clerk tenant configs (separate per product), billing/product
tables (`diary`/`yoco` vs `credit_ledger`/Wix-PayPal), the identity model itself (`iam.user` UUID vs
`core.app_user` bigint), and the multi-tenant `club_id`/`club.club` FK layer. SEO/GSC is already shared —
just run it `--all`.

**The elegant convergence:** because the shared CRM code is written against `core.*`, and NextPoint's Slice-0
bridge makes `core.*` live in NextPoint, unifying identity internally **also** makes NextPoint able to consume
the shared `core.*`-based modules. Identity unification and cross-product reuse are the same move.

---

## 7. Roadmap — tied to 907 → thousands / 95% courts

### Mission 1 — Data foundation (the "data lake"). Priority. Prereq for everything.

| Slice | Objective | Reusable components introduced | Depends on | Effort | Needle it moves |
|---|---|---|---|---|---|
| **1.0 Identity spine** ⟵ keystone | `iam.user ↔ core.person` FK; forward-create a `core.person` for every `iam.user`; email-match backfill of the 880; `anon_id`→person stitch at login | The bridge every later slice + all shared `core.*` code needs | — | S-M | Unblocks 360 + all CRM; makes shared code usable |
| **1.1 Minimum-data gate** | Require **name + phone** at all 3 create paths; `UNIQUE(lower(email))` after de-dup; unify the two `marketing_opt_in` flags; **backfill/enrich** the 880 (completion prompt on next login + admin nudge) | A reusable `require_min_fields()` validator + a "profile completeness" metric | — | S-M | Every human reachable by WhatsApp/SMS/email → conversion + retention |
| **1.2 True Client-360** | Surface demographics + preferences + consent state + CRM signals in `client360`; add the **unified activity timeline** (money ⊕ usage_event ⊕ consent) | `client360` timeline block (role-scoped) | 1.0 | M | Staff see the whole client → better service, upsell, retention |
| **1.3 Interaction capture** | Emit the missing lifecycle events (`account_created`, `payment_succeeded`, `login`); capture funnel micro-events + clicks/preferences via the beacon `props`/`track_event`; reconcile the `contracts/events.md` drift | Extended beacon + event taxonomy (SHARED) | 1.0 | M | Behavioural signal → segmentation + ad feedback |
| **1.4 NPS & surveys** | Wire a post-lesson NPS prompt → `record_nps()` + emit `nps_submitted`; surface bucket/verbatim in 360 + cockpit | Reusable feedback widget (SHARED) | 1.0 | S-M | Retention signal + win-back triggers |
| **1.5 Preferences** | New preference model (coach/court/time/surface/comms-channel); capture at booking + profile | `iam.preference` (SHARED) | 1.0 | S | Powers preference-based marketing (Mission 2) |

### Mission 2 — Marketing / CRM engine

| Item | Objective | Components | Depends on | Effort | Needle |
|---|---|---|---|---|---|
| **2.0 Activate Klaviyo** | Set `KLAVIYO_API_KEY`; build the transactional + marketing flows (spec exists in `docs/06`); SES stays the guaranteed transactional fallback | Klaviyo flow templates (per-club lists → white-label) | 1.0-1.4 | S (config) + M (flows) | Welcome/win-back/reminder → activation + reactivation |
| **2.1 Segmentation** | Segments over the unified 360 (tier, lapsed, low-usage, prospect, preference) — computed in **our** `core.*`, Klaviyo is only the send engine | `crm.segment` views (SHARED) | 1.2-1.5 | M | Right message → conversion + fill |
| **2.2 Statistical modelling** | Churn/next-best-action/fill-propensity scores written to the person; drive pushes across email/WhatsApp/SMS | Scoring jobs (SHARED) | 2.1 | M-L | Proactive retention + off-peak fill → 95% |
| **2.3 WhatsApp/SMS** | Add channels (phone now captured); channel = a preference | Channel adapters (SHARED) | 1.1 | M | Higher-open channel for reminders/offers |

**Klaviyo vs build (recommendation):** For **NextPoint** — **use Klaviyo** for delivery + flow-building
(multi-tenant per-club lists, deliverability, visual Flow Builder; already wired, one env var from live). For
**ten-fifty5** — build-not-buy is fine (single-tenant, simpler). **But keep the intelligence ours either way:**
segmentation + scoring live in `core.*` (we own the data, no lock-in — matches "we are our own CRM"); the
vendor is a swappable send engine behind the shared `crm_sync` forwarder. Don't let segmentation logic migrate
into Klaviyo.

### Later — Social & Ads

| Item | Objective | Depends on | Realistic? |
|---|---|---|---|
| Google Ads **offline conversion import** (gclid) | Feed real conversions back → Ads optimises for members, not clicks | 1.0 + gclid capture | ✅ high-ROI |
| **Customer Match** audiences (hashed emails, targeting + member exclusion) | Cheaper acquisition, no wasted spend on existing members | 1.1 + consent | ✅ |
| Enhanced conversions | Better attribution | conversions flowing | ✅ |
| GA4 signal-in (source→signup attribution) | Know which channels convert | 1.0 + UTM stitch | ✅ |
| Meta CAPI + audiences | Same pattern, second channel | above | ⏳ later |
| Real-time bidirectional sync / predictive bid feeds | — | — | ❌ aspirational, defer |

---

## 8. Recommendations (what I'd do differently)

1. **Lead with the identity bridge, not "capture."** The brief's instinct (capture more) adds nothing to the
   People tab until `iam.user ↔ core.person` is one graph. Slice 1.0 is the keystone; sequence everything after it.
2. **Reframe Mission 1 as unify → enforce → merge → activate.** ~70% is built. This is weeks, not months —
   spend the saved time reaching Mission 2 (the revenue engine) sooner.
3. **Stop the bleeding immediately (1.1).** Every day of email-only signups adds phone-less, sometimes
   nameless records to the 907. Minimum-data enforcement is small, high-value, and independent of the bridge —
   it can ship in parallel with 1.0.
4. **Own the intelligence, rent the delivery.** Segmentation + scoring in `core.*`; Klaviyo/WhatsApp/SMS are
   swappable send engines. Protects against lock-in and keeps the CRM genuinely ours.
5. **Fix reuse structurally, not socially.** The "edit in lock-step" contract has already drifted and caused a
   silent-zeros incident. Stand up a real shared-code mechanism and converge the 4 low-risk modules first
   (analytics engine, beacon, verifier, SES) to prove it before the `core.*` schema.
6. **Treat white-label as a first-class constraint now.** Everything marked SHARED should also be `club_id`-
   scoped and per-club-configurable so club #2 is cookie-cutter, not a fork.

### Smallest first slice (once green-lit)

**Slice 0 — "Identity spine + minimum-data gate" (one sprint).** Small, mostly invisible, but it's the
foundation everything else stands on and it can ship as one coherent unit:

1. Add `core.person.iam_user_id uuid` (FK → `iam.user`) — idempotent boot DDL, `club_id`-scoped.
2. **Forward-create**: every `upsert_user_by_clerk_id` / admin create also ensures a linked `core.person`.
3. **Backfill**: one-off email-match script to link the existing ~880 `iam.user` ↔ `core.person`, reporting
   unmatched/duplicate humans (DRY-RUN first, per house style).
4. **Minimum-data gate**: require name + phone at all three create paths; add a post-signup completion prompt
   for anyone missing it; a "profile completeness %" metric on the People roster.
5. **`anon_id` → person stitch** at login (write the localStorage `anon_id` onto the person so behavioural
   history attaches).

**Definition of done:** every human is exactly one linked pair (`iam.user` + `core.person`) with name + phone;
new signups can't be created without them; the People roster shows a completeness metric. This unblocks the
true 360 (1.2), all CRM signal attribution (1.3-1.5), Klaviyo activation (2.0), and cross-product reuse (§6) —
and it's the minimum that does.

---

## 9. Decisions (agreed with Tomo 2026-07-10)

All nine settled. Rationale one-liner each; the load-bearing three (1, 3, 2) define Slice 0 in §10.

1. **Identity direction → Bridge now; `iam.user` is the canonical person, `core.person` is its 1:1 CRM
   satellite. No big merge.** `iam.user` (UUID/Clerk) is correctly the auth identity; `core.person` is
   correctly the rich CRM profile. One FK links them — additive, reversible. The full merge is a high-risk
   rewrite that buys little; lean toward never doing it, just retire `core.app_user`'s separate-identity role
   over time.
2. **Phone → FRICTIONLESS signup (no added fields, not even at Clerk); capture first name + surname + cell as
   the minimum at FIRST-BOOKING CHECKOUT.** (Refined with Tomo 2026-07-10.) Get them in the door with zero
   friction; then, at the first booking — the committed moment — a lightweight "confirm your details" step
   captures name/surname/cell before the booking completes. No Clerk-dashboard change, code-only, and it reads
   as onboarding, not a barrier. Existing base is enriched by the backfill + a one-time nudge. Gets to ~100%
   coverage without a funnel tax.
3. **Duplicate humans → Yes:** `CREATE UNIQUE INDEX … ON iam.user (lower(email)) WHERE email IS NOT NULL`,
   **after** a DRY-RUN de-dup pass. The partial predicate exempts login-less dependents automatically (and
   Postgres treats NULLs as distinct anyway). De-dup/merge collisions first so the index can't fail on boot.
4. **Shared-code mechanism → Private pip package, git-tag-pinned** (`pip install git+ssh://…@v0.x`). Submodules
   are painful on Render/CI; vendored-sync drifts. Versioned package = clean dependency + per-repo rollback,
   fits both `requirements.txt` builds (needs a Render deploy token — one-time). Extract the analytics engine
   first to prove it. (§6.)
5. **Klaviyo → Confirmed for NextPoint delivery; segmentation + scoring stay in `core.*`; SES stays the
   transactional fallback.** Rent the send engine + Flow Builder; own the intelligence. No lock-in, true
   "we are our own CRM". The vendor sits behind the `crm_sync` forwarder (swappable).
6. **WhatsApp/SMS → Twilio to start (behind a channel adapter); Clickatell (SA-based) as the cost swap.**
   Twilio = fastest to market, one API for WhatsApp + SMS, POPIA-compatible with a DPA. The adapter lets us
   move to Clickatell once volume makes local SMS cost matter — no rewrite.
7. **Interaction capture → Semantic funnel + preference events only in v1. No full clickstream.**
   `booking_started/abandoned`, coach-profile view, availability search, filter/preference change, plan
   viewed — the events that drive segmentation + ad feedback. Full clickstream is storage-heavy,
   consent-heavier, low marginal value; add depth only when a real question needs it.
8. **Retention/consent → Anonymous cookieless stream stays consent-exempt; person-attributed behaviour inherits
   consent + a retention window.** Under POPIA the `anon_id` stream is legitimate-interest/exempt, but
   person-linked profiling is personal data: gate it under existing consent (or LI-with-opt-out), add a
   `retention_rule` for `usage_event` (raw ~12–24 mo → then anonymise), cascade DSAR erasure to `usage_event`.
   Document in the privacy decisions; DRY-RUN the retention job.
9. **907 baseline → Treat as the `iam.user` count (distinct humans with a membership); VALIDATE via the Step-0
   audit.** `core.person` is dark, so it isn't "the database"; the live roster is `iam.user`. **Tomo's read:**
   the Wix take-on brought in the full member set with name + email + phone + address for most, so the
   enrichment burden is expected to be **small and concentrated in new Clerk email-only signups**, not the
   historic base. The Step-0 audit (§10) turns that hypothesis into exact numbers and sizes the backfill.
   Confirm the raw count on the Render shell: `python -m scripts.verify_live` (or `SELECT count(*) FROM
   iam.user` vs `iam.membership` vs `core.person`).

---

## 10. Slice 0 — Build spec: "Identity spine + data-quality gate"

> **Goal:** every human is exactly **one** `iam.user` linked 1:1 to a `core.person` CRM satellite, with
> **name + phone** guaranteed going forward, and **no duplicate-email humans**. Nothing user-visible except a
> "profile completeness" number on the People roster. This is the foundation the true 360 (1.2), all CRM
> attribution (1.3–1.5), Klaviyo (2.0), and cross-product reuse (§6) stand on.
>
> **House rules to honour:** idempotent boot DDL (`ADD COLUMN/CREATE … IF NOT EXISTS`), **boot-twice clean**;
> SQLAlchemy Core, **repos never commit** (callers use `db.session_scope()`); every new row `club_id`-scoped;
> scripts **DRY-RUN by default**, `--commit`/typed-`YES` to write; gates = `py_compile` + `python -m db` twice
> + `python -m scripts.test_all`. No migration framework.
>
> **Sequence is strict — measure → link → enforce → constrain.** Do NOT add the unique index (Step 5) before
> the de-dup in Step 3 is committed, or boot will fail.

### Step 0 — Data-quality AUDIT (read-only; ship + run first, decide scope from real numbers)

**New:** `scripts/audit_client_data.py` — read-only, no writes, prints a scorecard. Validates the Q9
hypothesis (Wix data is good) before we build any enrichment.

Reports, per `club_id` and total:
- `iam.user` count; of those: with a `membership`, `clerk_user_id IS NULL` (import/dependent) vs set
  (self-signup) — this is the **Wix-origin vs Clerk-origin** split.
- Missing-field counts: `first_name` NULL/blank, `surname` NULL, `phone` NULL, `email` NULL, `address_line1`
  NULL, `dob` NULL, `marketing_opt_in` distribution.
- **Email collisions:** `SELECT lower(email), count(*) FROM iam.user WHERE email IS NOT NULL GROUP BY 1 HAVING
  count(*)>1` → the de-dup worklist.
- **Bridge state:** how many `core.person` exist, how many already carry (a future) `iam_user_id`, how many
  `iam.user` have an email-matchable `core.person`, how many are orphan `core.person` (no `iam.user`).
- The two `marketing_opt_in` flags (`iam.user` vs `core.app_user`) — agreement/conflict count.

**Output:** a `completeness %` = share of members with {name, phone, email}. This number is the Slice-0
success metric and the go/no-go on how much enrichment (vs pure linking) Steps 1–5 need.

### Step 1 — Schema: the bridge (idempotent boot DDL, in `core/schema.py`)

```sql
-- core.person gains a 1:1 link to the canonical identity (iam.user).
ALTER TABLE core.person ADD COLUMN IF NOT EXISTS iam_user_id uuid;
-- 1:1, but allow multiple NULLs during transition; excludes orphan/consent-only persons cleanly.
CREATE UNIQUE INDEX IF NOT EXISTS uq_person_iam_user
  ON core.person (iam_user_id) WHERE iam_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_person_iam_user ON core.person (iam_user_id);
-- FK last so a partial backfill can't block boot; guarded like the existing _CLUB_FKS pattern.
-- ALTER TABLE core.person ADD CONSTRAINT fk_person_iam_user
--   FOREIGN KEY (iam_user_id) REFERENCES iam.user(id)  -- add once backfill is committed
```

Direction chosen per Q1: link lives on the **satellite** (`core.person`), keyed to the canonical
`iam.user.id`. `iam.user` schema is untouched.

### Step 2 — Forward-create helper (every human gets a satellite)

**New:** `core/repositories/persons.py::link_person_for_user(session, *, iam_user_id, club_id, email,
first_name, surname, phone=None)` — idempotent, safe to call on every login:

1. `SELECT id FROM core.person WHERE iam_user_id = :iam_user_id` → return if present.
2. Else **adopt an orphan by the strongest key available:**
   - **(a) Clerk ID (exact, preferred):** `iam.user.clerk_user_id = core.app_user.auth_provider_uid` →
     `core.person.user_id = app_user.id` — both hold the same Clerk `sub`, so this is a reliable join, not a
     fuzzy one.
   - **(b) Email fallback:** `lower(iam.user.email)` = `lower(core.app_user.email)` (or `core.account.email`;
     `core.person` itself has no email) within the same `club_id`, LIMIT 1.
   - On a hit, set `core.person.iam_user_id` and return. *(This is how any Wix/consent-created `core.person`
     rows get claimed rather than duplicated.)*
3. Else create the satellite via the existing `accounts.ensure_identity(...)` path (account + app_user +
   person, `full_name = "first surname"`), then set `iam_user_id`. Return the id.

Repo does **not** commit. **Wire it at every identity entry point** (all pass `iam_user_id` + `club_id`):
- `auth/principal.py` — after `upsert_user_by_clerk_id` (both new + returning login; idempotent so returning
  logins are a cheap no-op).
- `admin/repositories.py::create_client`.
- `me/routes.py::create_dependent` (a dependent is an `iam.user` → gets its own satellite; guardian link
  already exists).
- `scripts/import_wix.py` (so future imports self-link).

### Step 3 — Backfill + de-dup (DRY-RUN first)

**New:** `scripts/backfill_person_links.py` — DRY-RUN default; `--commit` writes.
- **Pass A (link):** for every `iam.user` with no linked `core.person`, match to an orphan `core.person`
  **by Clerk ID first** (`clerk_user_id = app_user.auth_provider_uid`), **then email** within the same club →
  set `iam_user_id`. Report: linked-by-clerk, linked-by-email, unmatched (→ Pass B), ambiguous (key maps to
  >1 person or >1 user — never auto-write these; list for manual review).
- **Pass B (forward-create):** for unmatched `iam.user`, call `link_person_for_user` to mint a satellite.
- **De-dup report:** list the `iam.user` email collisions from Step 0. **Merging duplicate humans is a
  separate reviewed `--commit` step** (pick surviving row = has `clerk_user_id`, else oldest; repoint
  `membership`/`booking`/`dependent`/`order` FKs; soft-retire the loser). Do this *before* Step 5.

Idempotent + resumable (re-running only touches still-unlinked rows). Prints the same scorecard as Step 0 so
you can watch completeness climb.

### Step 4 — Minimum-data gate at FIRST-BOOKING CHECKOUT (per refined Q2 — stop the bleeding)

**Signup stays frictionless — nothing added, no Clerk-dashboard change.** The single minimum-data capture point
is the **first booking**, where the member is committed and asking feels like onboarding, not a barrier.

**New:** `iam/validation.py::missing_min_fields(*, first_name, surname, phone)` → returns `[]` or a list of
`{field, message}` (the three minimum fields).

- **Signup (`auth/principal.py`)** — persist whatever Clerk provides (Google gives name; email-only gives just
  email), forward-create the satellite (Step 2), **do not block**. Compute `profile_complete` for the roster
  metric (Step 7).
- **First-booking checkout (`diary/bookings.py::create_booking` + the booking SPA)** — before the first
  confirmed booking for a user with incomplete details, require a **"confirm your details"** step: first name +
  surname + **cell**. Server-side: `create_booking` calls `missing_min_fields(...)` and returns `422
  {needs_profile: [...]}` if the SPA hasn't supplied them; the booking widget renders the mini-form inline and
  re-submits. Persist to `iam.user` (+ satellite) before the booking proceeds. Subsequent bookings skip it.
- **Admin `create_client`** — keep it a hard 422 (staff can always get the details).
- **Existing base** — the backfill (Step 3) + a one-time portal nudge for anyone still missing fields.

**Unify the opt-in flags:** make `iam.user.marketing_opt_in` and `core.app_user.marketing_opt_in` a single
source — recommend `core.*` (what the Klaviyo gate reads) is canonical; `iam.user.marketing_opt_in` becomes a
mirror written through the consent path, or is dropped from writes. Reconcile conflicts found in Step 0.

### Step 5 — Email uniqueness (ONLY after Step 3 de-dup is committed)

Add to `iam/schema.py` boot DDL (idempotent; will succeed because collisions are already merged):
```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_email_lower
  ON iam.user (lower(email)) WHERE email IS NOT NULL;
```
Login-less dependents (NULL email) are exempt by the partial predicate. From here, one human = one row.

### Step 6 — `anon_id` → person link (capture now, attribute in 1.3)

**New table** (`core/schema.py`): `core.anon_link (anon_id text, iam_user_id uuid, club_id uuid, first_seen,
linked_at, PRIMARY KEY (anon_id))`. On authenticated page-load the SPA already knows Clerk state — send the
localStorage `anon_id` to a tiny `POST /api/track/identify` (or piggyback the beacon) → upsert `anon_link`.
**Scope for Slice 0 = capture the link only.** The backfill that stamps `usage_event.person_id` from
`metadata->>'anon_id'` belongs to Slice 1.3 (it rides the event-attribution rework) — noted so it isn't lost.

### Step 7 — Profile-completeness metric on the roster

Extend `admin.list_people` (or a thin `core.vw_*`) with a computed `profile_complete` bool + a roster-level
`completeness %` KPI. Read-only, `scope_clause`-filtered. Gives Tomo a live "are we at 100%?" number — the
whole point of Slice 0.

### File-by-file change list

| File | Change | Type |
|---|---|---|
| `scripts/audit_client_data.py` | **new** read-only scorecard (Step 0) | script |
| `core/schema.py` | `core.person.iam_user_id` + indexes (Step 1); `core.anon_link` (Step 6) | boot DDL |
| `core/repositories/persons.py` | **new** `link_person_for_user` (Step 2) | repo |
| `auth/principal.py` | call `link_person_for_user` + `require_min_fields(mode="soft")` (Steps 2, 4) | wiring |
| `admin/repositories.py` / `admin/routes.py` | `require_min_fields(mode="hard")` + forward-create (Steps 2, 4) | wiring |
| `me/routes.py` | forward-create for dependents (Step 2) | wiring |
| `diary/bookings.py` | first-booking hard phone/name gate (Step 4) | wiring |
| `iam/validation.py` | **new** `require_min_fields` (Step 4) | lib |
| `iam/schema.py` | `uq_user_email_lower` partial unique index (Step 5, **after** de-dup) | boot DDL |
| `scripts/backfill_person_links.py` | **new** DRY-RUN link + forward-create + de-dup report (Step 3) | script |
| `scripts/import_wix.py` | self-link on future imports (Step 2) | wiring |
| `marketing_crm/tracking/beacon.py` or new `/api/track/identify` | `anon_link` upsert (Step 6) | endpoint |
| `admin/repositories.py::list_people` (+ optional `core.vw_*`) | completeness metric (Step 7) | read |
| `contracts/events.md`, `docs/specs/CLIENT-360-CRM-PLAN.md` | keep in sync | docs |

### Rollout order (each a mergeable increment)

1. **Step 0 audit** → merge → run on prod-read → read the real numbers (validates Q9 / sizes everything).
2. **Steps 1–3** (bridge + forward-create + DRY-RUN backfill) → merge → run backfill DRY-RUN → review
   ambiguous/dup list with Tomo → `--commit`.
3. **De-dup merge** (reviewed) → confirm zero `iam.user` email collisions.
4. **Step 5** unique index → merge (now safe).
5. **Step 4** min-data gate + Clerk phone field → merge (stops the bleeding).
6. **Steps 6–7** anon-link + completeness metric → merge.

### Definition of Done

- `audit_client_data.py` reports **0** members missing name, **0** missing phone (new signups blocked at
  first booking; base enriched or nudged), **0** `iam.user` email collisions.
- Every `iam.user` with a membership has exactly one linked `core.person` (`iam_user_id` set); orphans and
  ambiguous cases are an explicit, reviewed list — not silent.
- `uq_user_email_lower` live; `python -m db` twice = clean no-op; `python -m scripts.test_all` green.
- People roster shows a live completeness %. **Then, and only then, we build the true 360 (Slice 1.2).**
```

# BUILD_PROMPT — Kick off the NextPoint / CourtFlow build in Claude Code

> Paste **Section 1** into Claude Code at the repo root to start. It will read the full spec in
> `docs/` and orchestrate the lane agents. Sections 2–7 are the per‑agent briefs the orchestrator
> (or you) can dispatch. Keep `docs/` open — it is the source of truth; this prompt just drives it.

---

## Section 1 — Master orchestrator prompt

```
You are the lead engineer building "CourtFlow" — a multi-tenant tennis club management platform.
NextPoint Tennis is tenant/club #1 (migrating off Wix). The complete specification is in ./docs
(00..09) and ./README.md. READ ALL OF IT before writing code.

Reuse the proven Ten-Fifty5 (1050) codebase patterns from C:\dev\webhook-server (READ-ONLY reference):
auth_v2/ (Clerk JWKS verify), models_billing.py/db_init.py (idempotent boot DDL), subscriptions_api.py
(apply_subscription_event = the provider-agnostic billing template), paypal_billing/ (vanilla gateway
adapter shape), marketing_crm/ + core_db/ (own-CRM + Klaviyo sync + consent), locker_room_app.py +
build_blog.py (host-switched marketing + SEO toolkit). Copy patterns, do NOT import 1050's code or
touch its repo/DB.

Hard constraints (from docs):
- New repo (this one), NEW Postgres DB. Reuse the existing Render org, Clerk account, AWS (S3/SES),
  Klaviyo account — new project-scoped values only. Secrets are sync:false in render.yaml.
- Multi-tenant from day one: every domain row has club_id; never query domain data without it.
- Python 3.12 + Flask + Gunicorn + Postgres; idempotent boot DDL (no migration framework); enable
  btree_gist + pgcrypto.
- The diary is the heart: one set of bookings, role-scoped lenses; court + lesson + class; full
  edit/cancel/reschedule; DB-level exclusion constraint prevents double-booking.
- Payments are provider-agnostic (gateway protocol → apply_payment_event). Launch with at_court /
  monthly_account / membership_covered / free settlement; Yoco is the first online adapter (keys
  exist) but online pay is behind a flag — design now, can switch on later. Do NOT hardcode a gateway
  into core.
- Klaviyo sends every booking/lesson/class confirmation (transactional), SES as fallback; marketing
  email is opt-in only; no minor PII into Klaviyo.

Plan of work: follow docs/09 phasing. Do Phase 0 (foundation) and Phase 1 (tenancy + NextPoint seed)
YOURSELF first and commit. THEN fan out parallel agents by lane (docs/09 §2): B-Diary, C-Billing,
D-CRM, E-Frontend, F-Marketing — each in its own git worktree, touching only its lane. Use the
per-agent briefs in BUILD_PROMPT.md §2-§7. After B/C/D land, integrate E. Run the Phase-2 and Phase-3
verification suites (docs/09 §5, esp. the edge cases in docs/03 §10) before merging anything that
touches booking integrity.

Deliver Phase 0-3 + a working member booking wizard against NextPoint seed data this session:
book/edit/cancel/reschedule a court and a lesson, enrol in a class, with at-court + monthly settlement
and a fired booking_confirmed event. Report what's done vs pending against docs/09 §5 at the end.

Start by: (1) reading ./docs fully, (2) confirming the Postgres DATABASE_URL + env you have, (3)
scaffolding the repo (app.py, wsgi.py, render.yaml, db.py, module folders), (4) writing the boot
schema runner, (5) porting auth. Ask me only if a decision isn't covered by the docs.
```

---

## Section 2 — Agent A: Foundation / Platform  (run first, alone)

```
Lane: app.py, wsgi.py, render.yaml, requirements.txt, db.py, iam/, auth/, scripts/ (seed/provision).
Read docs/01, 02, 04, 09. Build:
1. Repo skeleton + render.yaml (2 web services: courtflow-api, courtflow-web; crons). Secrets sync:false.
2. db.py (psycopg pool) + a boot runner that calls every module's init() idempotently; enable
   pgcrypto + btree_gist. Running boot twice must be a no-op.
3. club.* + iam.* schemas (docs/02 §2-§3) via their init().
4. Port auth_v2/: Clerk JWKS verify → principal; add iam.user upsert + membership/club resolution by
   host/X-Club/default (docs/04 §3). Central iam/permissions.py (docs/04 §4).
5. scripts/seed_nextpoint.py (docs/02 §7) + scripts/provision_club.py + a "template club" (docs/08 §4).
Done when: app boots, schemas create idempotently, a Clerk JWT resolves {user_id, club_id, role},
NextPoint club #1 + 9 courts + coaches + class resources + ZAR prices are seeded. Commit + push.
```

## Section 3 — Agent B: Diary engine

```
Lane: diary/. Read docs/03 (whole), 02 §4. Build the booking heart:
- diary.* schema incl. the GiST exclusion constraint on booking (resource_id, tstzrange) WHERE status
  in ('held','confirmed').
- Availability computation (docs/03 §3). Booking creation tx with held→confirmed + 409 SLOT_TAKEN on
  conflict (docs/03 §4). Lesson = coach hold + linked court hold under one order_id.
- Full edit/reschedule/cancel (atomic, policy-aware), recurrence (RRULE), classes + enrolment +
  waitlist + promotion (docs/03 §5-§6). Crons: reminders, capacity-sweep, monthly-invoice trigger.
- /api/diary/* endpoints (docs/03 §8), club_id from principal, role-gated via permissions.py.
- Emit events (booking_confirmed/cancelled/rescheduled/reminder, class_enrolled/waitlisted,
  lesson_completed) via the contract — call emit(); don't implement delivery (Agent D owns that).
Done when: docs/03 §10 edge cases pass as automated asserts against a scratch DB.
```

## Section 4 — Agent C: Billing / Settlement + gateways

```
Lane: billing/, yoco_billing/, paypal_billing/. Read docs/05, 02 §5.
- billing.* schema (product, price, order, order_line, payment, payment_attempt, account_ledger,
  membership_subscription).
- The PaymentGateway protocol + apply_payment_event(normalized) — idempotent via payment_attempt
  event_hash; record-only refunds. Implement the 'manual' provider (desk cash/card) + the four launch
  settlement modes (at_court / monthly_account / membership_covered / free). GET /api/billing/config.
- THEN (flag-gated) yoco_billing/ adapter (create_checkout/verify_webhook/parse_event) + 'online'
  mode. FIRST fetch Yoco's current API docs and build to them (docs/05 §6 warning). Port paypal_billing
  as a 2nd provider to prove the abstraction.
Done when: each settlement mode writes correct order/ledger rows; apply_payment_event replay = no-op;
config probe flips online pay on/off cleanly.
```

## Section 5 — Agent D: CRM / Klaviyo

```
Lane: core/, marketing_crm/. Read docs/06, 02 §6.
- Port core.* (core_db) + marketing_crm/{tracking,crm_sync,consent,backoffice}; add club_id to events
  + Klaviyo profile traits (segment per club).
- contracts/events.md for the booking domain (docs/06 §2). Consume emit() from B/C → core.usage_event
  → crm_sync → Klaviyo. Transactional confirmations always send (SES fallback for booking_confirmed);
  marketing gated on marketing_opt_in; never minor PII.
- Build Klaviyo TEMPLATES via the connector for the transactional set; document the Flow Builder wiring
  (connector can't create flows). Club-admin cockpit views (occupancy/revenue/utilisation/attendance).
Done when: a test booking_confirmed reaches a Klaviyo test profile; opt-in gating verified.
```

## Section 6 — Agent E: Frontend / Portal  (integrate after B/C/D)

```
Lane: frontend/. Read docs/03 §9, 04, 08. Reuse 1050's vanilla-JS SPA + CSS conventions, NextPoint
branding (green palette, logo). Build:
- Member booking wizard (type → court/coach/class → slot → parties → settlement → confirm), mobile-first.
- "My bookings" + cancel/reschedule UI.
- Coach diary (my week, classes/rosters, mark attendance, availability editor).
- Club-admin console: master diary calendar (evaluate FullCalendar resource-timeline), resources,
  people, pricing, billing/settlement, cockpit, settings.
- /login (Clerk, themed by host). Consume /api/diary/* + /api/billing/* + entitlement/config probes.
Done when: a member books a court, a lesson, and a class end-to-end on a phone; coach + admin lenses work.
```

## Section 7 — Agent F: Marketing site + SEO migration

```
Lane: frontend/marketing/, build_blog.py, migration/. Read docs/07.
- Port the host-switched native marketing site + blog generator + sitemap/robots/branded-404, themed
  from club.branding. Pages: home, services, coaches (Neville Godwin/Ross Nemeth bios), high-performance,
  juniors, socials, free-lesson, contact, careers + JSON-LD (LocalBusiness/SportsActivityLocation,
  Service/Offer, FAQ, Breadcrumb).
- migration/: build url_inventory.csv from GSC + Ahrefs + a live crawl; produce the 301 map (old Wix
  URL → new path, docs/07 §3); implement host-aware 301s; self-canonicals.
- Do NOT touch DNS or the api.nextpointtennis.com record. The cutover (docs/07 §4) is a separate,
  supervised step with Tomo.
Done when: site renders natively, every inventoried URL has a 301 target, sitemap validates.
```

---

## Pre-flight checklist for Tomo (before/while agents run)
- [ ] New Postgres provisioned; `DATABASE_URL` ready.
- [ ] New Clerk application created (`clerk.courtflow.app` or similar); JWKS URL + publishable key.
- [ ] AWS S3 bucket + SES sender (`bookings@nextpointtennis.com`) verified; keys to hand.
- [ ] `KLAVIYO_API_KEY`; decide per-club list vs `club` property; authenticate sending domain; postal address.
- [ ] `YOCO_SECRET_KEY` / public / webhook secret (you have these) — for Phase 7.
- [ ] Confirm current DNS for nextpointtennis.com (registrar + what points to Wix vs the 1050 api record).
- [ ] Keep Wix live until SEO cutover is verified (rollback path).
```

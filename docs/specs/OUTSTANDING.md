# OUTSTANDING — what's left to do

The single source of truth for remaining work. Grouped by type. (Everything NOT here is built & live —
see [BUSINESS-RULES.md](BUSINESS-RULES.md) / [INVENTORY.md](INVENTORY.md).)

## A. Config — needs Tomo (not code; flips features from dark → live)
- [ ] **SES verified sender** → transactional **emails** start sending (notifications engine is built &
      waiting; until then in-app inbox only). Also enables invite/confirmation emails.
- [ ] **`KLAVIYO_API_KEY`** → CRM lifecycle/marketing flows go live (event feed already emits).
- [ ] **`S3_BUCKET` + AWS keys** → coach **photo uploads** (until then coaches paste a photo URL).
- [ ] **DNS / SEO cutover** for `nextpointtennis.com` (supervised — never an agent; see `docs/07`,
      `docs/11 §5`). Give the platform its own API host (`api.courtflow.app`) — `api.nextpointtennis.com`
      is the live 1050 service, do not break it.
- [ ] Confirm **Yoco fee accounting** assumption in practice (fees = owner's account, recovered via
      commission — currently not deducted from coach splits).

## B. Build items — remaining functionality
- [ ] **Commission engine tail (Phase D deferrals):**
  - [ ] **Refund clawback** — when a paid lesson/class is refunded, write a *negative* `commission_split`
        so the coach's earned commission reverses. Basis exists; the `refunded` branch isn't wired.
  - [ ] **Coach payout objects** — `coach_payout` records (owner↔coach settlement). Today the cockpit
        *reports* who owes what; settlement is offline.
  - [ ] **Rent auto-accrual** — `accrue_rent_for_club` exists + is idempotent; it runs on-read. A
        scheduled monthly accrual would be cleaner (needs a scheduler — see crons below).
- [ ] **Bundle/arrears edges:** bundle **expiry** policy for unused credits (refund/transfer?); a
      "too-late cancellation forfeits the token" option (today cancel always credits back); optionally a
      Yoco "pay statement" link so a client can pay a coach's arrears invoice online (today off-platform).
- [ ] **Platform / super-admin cockpit** — cross-club view (all clubs' revenue/health) for
      `platform_admin`. Low priority while there's one club; the `scope_clause` design supports it.
- [ ] **Reminders** — booking reminders (the `/api/cron/reminders` handler exists but cron services are
      off). Needs a scheduler: re-enable a Render cron, or an external pinger, or a lazy "due reminders"
      sweep. Same blocker for scheduled rent accrual + the reconcile/membership-refill sweeps.
- [ ] **Reschedule UX polish** — `PATCH /api/diary/bookings/<id>` exists; ensure member/admin
      reschedule flows are smooth + policy-guarded.
- [ ] **My Bookings** — confirm the member `/my.html` cancel path surfaces token credit-back / refund
      clearly.
- [ ] **Self-serve coach/admin role transitions** — e.g. a dependent **aging out at 18** into their own
      login (foundations spec open question).

## C. In progress by another agent (do not duplicate)
- [ ] **Page / traffic analytics** for BOTH sites (visitors, geolocation, channel, device). The
      `analytics/` lane + `/api/analytics/*` + `/overview.html` already exist in the repo and are being
      built by a separate agent. Coordinate; don't touch their lane.

## D. Hardening / pre-launch (later phases, from the original docs)
- [ ] **RLS** (row-level security) on domain tables — Phase 8; today multi-tenant is a query discipline.
- [ ] An automated **test runner** (there's no pytest suite; gates are `py_compile` + boot-twice +
      per-build scratch-DB scripts). Consider formalising the integration scripts.
- [ ] **VAT/tax** registration + invoice formatting (commission base is treated ex-VAT today).
- [ ] **Consent/PII review** for any new email/notification payloads (no minor PII in marketing sends).
- [ ] Revisit the **four `render.yaml` crons** (capacity-sweep / reminders / monthly-invoice /
      membership-refill) if/when off the Free plan — handlers exist; only the schedulers are disabled.

## How to pick up (next session)
1. Read [README.md](README.md) → SYSTEM → BUSINESS-RULES → INVENTORY.
2. Pick an item above. The deep design for most lives in the role specs + `01`/`02` decision docs.
3. Build in a worktree, verify (`py_compile`, `node --check`, `python -m db` twice), merge to `master`,
   confirm the Render deploy. Keep every new table `club_id`-scoped + idempotent.

# INVENTORY — everything that exists

Exhaustive as-built inventory (generated from the live code, 2026-06-21; refreshed 2026-06-26, 2026-07-11). Paths relative to repo root.

## 1. Services (Render, **Frankfurt**, **Starter** plan)
- **`courtflow-api`** (`wsgi:app`) — the Flask API, has the DB. `https://courtflow-api.onrender.com`.
  Boots all schemas (`python -m db`), `SEED_NEXTPOINT=1` re-seeds club #1 on boot. `AUTH_ENABLED=1`.
- **`courtflow-web`** (`web_wsgi:app`) — DB-less; host-switched marketing site + portal SPA shells +
  `/login`. `https://courtflow-web.onrender.com` (an entry in `MARKETING_HOSTS`, so `/` = public site,
  app lives at `/portal`, `/book`, `/admin`, …).
- **Postgres** — `courtflow-db`, a separate Render DB (Frankfurt). **Clerk PRODUCTION app** for auth (`pk_live_...`, `clerk.nextpointtennis.com`) - see ENV-STATUS.md.
- **Region — all three in Frankfurt, co-located** (`region: frankfurt` + `plan: starter` pinned on both web
  services in `render.yaml`; the api uses the DB's internal same-region URL). Until 2026-07-05 the web
  services ran in Oregon while the DB was in Frankfurt (cross-Atlantic ~150ms/query); recreated in-region.
- **Crons** — declared in `render.yaml` but **commented out** (no paid crons). Their HTTP
  handlers exist (see §3 crons) and can be triggered manually; hold-release + waitlist run lazily instead.
- **Keep-warm** — `.github/workflows/keep-warm.yml` (GitHub Action) pings both services every 10 min
  07:00–21:59 SAST so the Free tier doesn't cold-start mid-use; sleeps overnight. Free. (Frontend also has
  a 70s `apiFetch` timeout so a cold/hung call errors instead of spinning forever — `frontend/js/auth_client.js`.)
  Now that both services are on **Starter** (2026-07-05) they no longer sleep, so the keep-warm is redundant
  and can be removed.
- **Month-end sweep** — `.github/workflows/month-end.yml` (GitHub Action) fires `POST /api/cron/month-end`
  (OPS-guarded) so the month-end statement-ready notify runs without an always-on Render cron.

## 2. Code lanes (Python)
| Lane | Owns | Purpose |
|---|---|---|
| `app.py`, `wsgi.py`, `db.py` | boot, app factory, schema runner (`BOOT_MODULES`) | Foundation |
| `auth/` | `principal.py`, `verifier.py` | Clerk JWKS verify → club-scoped `Principal`; **auto-enrol** new users as members |
| `iam/` | `schema.py`, `repositories.py`, `permissions.py` | user, membership, coach_profile, coach_invite, player_profile, **dependent** |
| `club/` | `schema.py` | club, branding, location, policy |
| `core/` | `schema.py`, `repositories/` | core.user/account/person, usage_event, consent, nps, **notification**, **acquisition** (`repositories/acquisition.py` gclid/utm capture; `repositories/persons.py::link_person_for_user` = the iam↔core identity bridge) |
| `offline_conversions/` | `schema.py`, `recorder.py`, `feed.py`, `blueprint.py` | **Google Ads offline-conversion loop** — SHARED/byte-identical with the 1050 repo. `core.offline_conversion` ledger + `GET /feeds/google-ads/offline-conversions.csv`; `recorder.record_from_emit` is a 4th forward in `marketing_crm/tracking` (event `payment_succeeded` → gclid → row). Only per-repo glue = `CONVERSION_MAP`. |
| `diary/` | bookings, availability, classes, recurrence, pricing, **entitlement** (member caps/coverage resolver), **equipment** (hire add-on), routes | The booking engine (the heart) |
| `billing/` | orders, ledger, gateway, membership, bundles, commission, refunds, statement, me, activity, events, routes | Orders/ledger + the commercial engines (`statement.py` = unified client statement; `activity.py::transaction_log` = unified per-client/coach money log; `me.py::billing_summary` = ORDER-based monthly by-category) |
| `yoco_billing/` | client, adapter, routes, reconcile, receipt | Yoco online payments (adapter behind the gateway registry) |
| `marketing_crm/` | tracking (`emit`), notifications (+`_club_identity`), email/ses, klaviyo, consent, cockpit | Event feed + **notifications** + CRM + **club-branded transactional email** |
| `admin/` | routes, repositories, schema | `/api/admin/*` owner self-service + config |
| `coach/` | routes, repositories, schema | `/api/coach/*` coach self-service + cockpit |
| `services/` | routes, repositories | `/api/services/*` — the ONE unified service-edit surface for owner + coach (owner can create a lesson per coach via `POST /api/services`); delegates to `coach/`/`billing/` repos |
| `me/` | routes | `/api/me/*` client self-service (profile, dependents, financials, refund-requests, notifications) |
| `client360/` | `get_client_360` | the ONE cross-lane **client read model** (identity + membership(+status) + packages{active,history} + statement/owed + payments + bookings + dependents + refunds + coaching + activity + notifications-unread + a per-scope `can{}` map). Read-only, reuse-first — composes the existing lane readers (`billing.statement`/`membership`/`bundles`/`commission`/`refunds`/`activity`, core notifications, diary bookings/enrolments, `iam.dependent`), club_id-scoped. **`get_client_360(…, month=None)`** now takes an optional month (default =
this month) that scopes the coaching figures and adds a per-service breakdown (`_service_breakdown`) + a
month-at-a-glance `activity_summary` block (`_activity_summary` → `billing.me.activity_summary`). **Each block runs inside a SAVEPOINT** (`_guard` → `session.begin_nested()`) so a failing block degrades to empty/None and **never rolls back the caller's transaction** (fixed 2026-07-11 — a bare `session.rollback()` was discarding the caller's writes + the harness fixture). Booking rows carry **service + payment status** (same vocabulary as the receipts). Scoped `admin`/`coach`/`client`; a SUPERSET of the old admin person-360, so `admin.repositories.get_person` **delegates** to it (`scope='admin'`). Returns None if the user has no `iam.membership` in the club. |
| `analytics/` | repositories, routes | **Business Overview dashboard** (read-only over `core.usage_event`/`diary`/`billing`); `/api/analytics/*`; the standalone `/overview.html` (rolling `?days=` window). The admin **native Overview tab** now uses the `insights/` lane instead (the old iframe embed was retired 2026-07-05). |
| `insights/` | repositories, routes | **Phase-2 P1 read-layer** (guarded aggregations, no new tables): court-utilisation heatmap · **sales-by-day** · **bookings-by-day** · **overview** (month-scoped daily composer powering the native admin Overview tab — traffic incl. public-vs-member + logged-in split, bookings, revenue, members, NPS; reconciles with the Money lists by construction); `/api/insights/*` |
| `crons/` | trigger | thin dispatcher → `/api/cron/*` |
| `scripts/` | seed_nextpoint, provision_club, **backfill_pack_products** (map legacy NULL-product packs to their service — preview + `--commit`), **audit_class_packs** (report class packs vs their session `price_id`), **audit_trials** (7-day-trial grant audit/cleanup), **cleanup_coachless_classes** (soft-retire legacy empty coachless classes — dry-run + `--commit`), **reconcile_coach_commission** (READ-ONLY: every PAID lesson/class line must carry a coach `commission_split` — lists any that don't [should be NONE] + a covered-rand tie-out; optional `YYYY-MM`), **diagnose_coach_packs** (READ-ONLY: where each session pack lands in coach earnings — its wallet's coach ELSE the club, sale-based; optional `<name> [YYYY-MM]`) | seed/provision tenants + data maintenance |
| `web_app.py`, `frontend/` | host-switch + SPA shells + marketing | The web service |
| `migration/` | Wix→Render URL/301 helper | SEO migration |

## 3. API endpoints (by lane)
**Diary `/api/diary/*`:** `GET availability` (membership coverage priced PER-SLOT — R0 only inside the
access window, now also SILENTLY shaped by the member's entitlement caps + court-service eligibility, and
court PEAK pricing at peak times; `member_user_id` threaded so shown==charged) · **`GET equipment`** (active
equipment items for the court add-on picker — id · name · quantity · feature_on_home · flat price; POST
`/bookings` now also accepts **`addons:[{resource_id,qty}]`** → equipment lines on the booking's order) ·
`GET resources` · `GET durations` · **`GET services`** (`?kind=&coach_id=&audience=`
— bookable SERVICES for a coach: each product [e.g. Private / Semi-private] with its OWN
`durations:[{duration_minutes,amount_minor,price_id}]` + `payment_modes` + `currency_code` + **`max_clients`**
(the squad cap — `services_for` carries it), so the wizard
offers the service name before the duration; `diary/pricing.py::services_for`, STRICT TWO-TIER via
`_coach_has_own_product` — a coach's OWN active product ELSE the shared NULL-coach product, never merged;
also returns **court SERVICES** [e.g. Hardcourt Hire / Clay Hire], each a `product(kind='court_booking')`
with its own price + allocated courts, so the client picks a court service like a lesson service) ·
`GET/POST bookings` (**POST now accepts `product_id`** — the chosen service [lesson, class OR court service]
is priced exactly, passing `coach_user_id`+`product_id` into the order; **POST also accepts
`extra_clients:[…]`** — a SEMI-PRIVATE (squad) lesson shares one slot across >1 client, each billed their OWN
owed order at the service price [per-head; `create_booking(extra_clients=…)` → a `booking_party` role
'partner' per head + a separate order linked via `order_line.booking_id`, billed to the player or their
guardian via `_bill_owner`/`iam.guardian_user_id_for`]; capped by the service's `product.max_clients`;
pricing/availability/`create_booking`
are **court-service-aware** — a court's service resolves via `diary.pricing.court_service_for_resource`
[the court's own `product_id`, else the club default court product, else unscoped], and a court booked under
the wrong service is rejected **`COURT_NOT_IN_SERVICE`**; single-court-service clubs unchanged. **POST also
accepts `allow_past`** — staff BACK-CAPTURE of a lesson/class that already happened: role-gated + ANDed with
on-behalf [`booked_for_user_id`], it bypasses the `IN_THE_PAST` guard, resolves the coach's resource from
`coach_user_id` when no slot carries it, bills the client + credits the coach, holds no calendar slot; a member
can never back-date) ·
`GET bookings/<id>` · `PATCH bookings/<id>` (reschedule — auto-reassigns the held court for a lesson;
re-prices unpaid order lines + `coach_arrears` from the same product on a duration change via
`billing.orders.reprice_booking_order`; **`PAID_CANNOT_EXTEND` (422)** extending a PAID booking,
**`NOT_COVERED_AT_NEW_TIME` (422)** moving a `membership_covered` booking to an uncovered time; a member
reschedule is now held inside the coach's PUBLISHED hours via `diary.availability.resource_hours_cover`) ·
`POST bookings/<id>/cancel` (now **voids the
linked unpaid order** via `billing.statement.void_order` — a cancelled court no longer stays phantom-owed;
raises a late-cancellation **fee order** when policy applies; returns `was_paid`) ·
`POST bookings/<id>/status` · **`POST bookings/<id>/{accept,propose,decline}`** (lesson lifecycle — only
the awaited party; admin always) · **`POST bookings/<id>/add-player`** (add ANOTHER client to an existing
semi-private lesson AFTER booking — squad confirmations land late; `{email}`|`{user_id}` → own owed order
per-head via `diary.bookings.add_lesson_partner`; edit gate = reschedule's [staff OR the booking's owner];
the route helper **`_addable_player_uid`** lets a non-staff booker add only club members + their OWN kids,
staff any) · **`GET members/search`** (`?q=` — **staff-only** picker: matches members by name/email AND
surfaces their dependents/kids as their own rows for the add-player flow; `iam.search_members_with_dependents`) ·
**`GET bookings/<id>/calendar.ics`** (booking .ics) ·
`GET master` · `GET classes` · `POST classes` ·
`GET classes/<rid>/sessions` · `POST classes/<rid>/schedule` · `GET classes/<sid>/roster` ·
`POST classes/<sid>/enrol` (`diary.classes.enrol` now takes a **`role`** + gates the payment mode against
the class service's offered modes / membership_covered / free — a member can't conjure a free or
unpayable seat) · `POST classes/<sid>/cancel-enrolment` · `POST classes/<sid>/attendance` ·
`POST classes/sessions/<sid>/cancel`.

**Billing `/api/billing/*`:** `GET config` · `GET receipt/<order_id>` · `POST desk-payment` ·
`GET bundles` (+`allowed_payment_modes`) · `GET bundles/wallets` · `POST bundles/checkout` (the pack's
allowed modes = its SERVICE modes ∩ club-enabled via pure **`billing.bundles.allowed_purchase_modes`** — no
at-court fallback, an unpayable pack is refused) ·
`GET membership/status` (+`allowed_payment_modes` per plan) · `POST membership/checkout`. The two
`checkout` routes accept `settlement_mode` (offline → 'open'/owed order + grant immediately; online → Yoco).

**Yoco `/api/billing/yoco/*`:** `POST checkout` · `POST webhook` · `POST refund` · `GET order/<id>` ·
`POST reconcile/<order_id>`.

**Admin `/api/admin/*`:** **`GET home`** (the owner **command-center** for the redesigned admin app —
guarded focus cards: today / money (owed-to-club, net revenue, rent due) / people-needing-attention /
approvals; `admin/repositories.py::admin_home`) · `GET/POST onboarding` (+`/complete`) · `GET/PATCH club` · `PUT location` ·
`PATCH branding` · `PATCH policy` · `GET/POST resources` (+`PATCH/DELETE /<id>` — **`PATCH` now also sets a
court's `product_id`** = the court SERVICE it belongs to, the "Court service" picker per court in Setup →
Courts & hours; DELETE now real: hard-delete a court with no bookings/sessions, else soft-archive) · `GET/PUT hours` ·
`GET/POST products` (+`PATCH /<id>`) · `GET/POST prices` (+`PATCH/DELETE /<id>`) ·
`GET coaches` · `POST coaches/invite` · `POST coaches/<id>/resend-invite` ·
**`PATCH coaches/<id>`** (lifecycle status) · **`DELETE coaches/<id>`** (real: hard-delete if no
history, else archive) · `GET people` (roster; `admin.list_people` now also returns **`on_trial`**,
**`has_active_pack`** and **`membership_tier`** alongside `has_membership` — the People segmented-control
holdings slicers: membership-tier · On-trial · Has-pack · No-membership — plus **`owed_minor`**, **`last_seen`**,
**`last_kind`** and **`first_seen`** for the headline-first person cards) · `GET payments` ·
`POST|DELETE members/<id>/membership` ·
**`POST clients`** (create a walk-up/off-system client now — returns `user_id`, idempotent on email; body is
**`first_name`+`surname`** [split] +email +optional phone — `admin.create_client` now takes first/surname) ·
**`PATCH clients/<id>`** (edit a client's contact details from the person-360 — name/surname/phone, email is
the identity key & not editable; `admin.edit_client` → `iam.patch_profile`) ·
**`GET clients/<client_user_id>/packages`** (`?coach_id=` — a client's ACTIVE packs for admin on-behalf
booking to auto-route to a prepaid pack; `coach_id` filters lesson packs to that coach's/coach-agnostic,
class/court always included) ·
**`POST members/<id>/issue`** (issue a **membership OR token pack** offline — `{kind, price_id?|bundle_plan_id?,
start_date?, mark_paid?, pay_provider?}`; reuses the offline-purchase engine → owed order activated now,
`mark_paid` settles immediately) ·
**`POST clients/<client_id>/invoice`** (ad-hoc invoice — `{lines:[{price_id?|amount_minor, description?, qty}],
discount_minor?, reason?}` → ONE owed `billing.order` [monthly_account, on the unified statement, settleable
online] + emails a `/portal` pay link; a service line re-derives its price server-side [tamper-proof], a custom
fee uses the body amount; `admin.repositories.create_invoice`) ·
**`POST clients`** (walk-up client — **requires a name + valid email**, `_EMAIL_RE`-guarded; idempotent on email) ·
**`GET members/<id>/statement`** · **`POST orders/<id>/void`** (`{write_off}` — void/write-off an owed order) ·
**`POST orders/<order_id>/discount`** (`{discount_minor|new_amount_minor, reason}` — reprice ANY OPEN/awaiting
order [court/lesson/class/pack/membership]; multi-line orders split the discount pro-rata [remainder on the
last line], `order_line.original_amount_minor` preserved as the audit, a linked `coach_arrears` line kept in
LOCKSTEP via `commission.adjust_arrears`; a PAID order rejects `NOT_OPEN` — refund is the separate path; no
new debt/settlement row — mutates the ONE debt; `billing.statement.discount_order`) ·
**`POST clients/<client_id>/wallets/<wallet_id>/adjust`** (`{delta_sessions|delta_minutes, reason}` — manually
add/subtract a client's prepaid token wallet [clamped ≥0, a top-up raises `minutes_total`]; `billing.bundles.adjust_wallet`) ·
**`POST clients/<client_id>/wallets/<wallet_id>/expire`** (`{reason}` — SOFT-expire a wallet [status='expired',
balance zeroed, row+ledger kept, never hard-deleted]; `billing.bundles.expire_wallet`) ·
`GET/POST membership-plans` (+`PATCH/DELETE /<id>` — POST/PATCH now also carry the silent caps
`max_covered_minutes`/`max_covered_per_day`/`max_courts_per_day` + the `is_trial`/`trial_days` signup-trial
config) · **`GET/POST equipment`** (+`PATCH/DELETE /<resource_id>`) — equipment-hire CRUD (name · flat fee ·
quantity · feature_on_home); `PATCH /policy` now also carries the `peak_days`/`peak_start_min`/`peak_end_min`
court-peak window; the service editor's `PATCH /api/services/<id>` carries `members_covered` (court service)
and `POST/PATCH .../variations` carry `peak_amount_minor` ·
**`GET/PATCH membership-config`** (per-tier payment options) · **`GET bundle-plans`** (kept for the offline
"issue a pack" picker; the `POST/PATCH/DELETE /api/admin/bundle-plans` **write** routes were REMOVED 2026-07-09
— packs are created/edited ONLY under a service via `POST/PATCH/DELETE /api/services/<product_id>/packages`) ·
`GET coach-agreements` · `PUT coach-agreements/<coach_id>` · `GET/POST commission-rules`
(+`DELETE /<id>`, `GET /preview`) · `GET financials/{summary,revenue,coach-earnings,memberships}` ·
**`GET financials/earnings-by-service`** (`?month=` — the MONEY FOLD by service: the club's
Billed→Collected→Outstanding triad + a club-keeps/payouts-due/standing-debt/members summary band + per-service
rows; order-status-driven so it reconciles; `admin.repositories.earnings_by_service` — retained for the Money
menu band) · **the CLUB-vs-COACH revenue P&L drill** (the ONE shared `Widgets.Earnings`, all off `_earnings_cte`
so they can't drift): **`GET financials/revenue-club`** (`?month=` — the club overview: direct services +
commission earned FROM each coach → per-coach P&L; `revenue_club_overview`) · **`GET financials/
revenue-coach/<coach_user_id>`** (`?month=` — one coach's P&L: sales−discount−write-off=net, net=received+owed,
commission −coach/+club realised on received + projected on owed; `revenue_coach_pnl`) · **`GET financials/
revenue-clients`** (`?category=&earned_by=<coach_uuid|club>&month=` — the per-node → client fold, summing
EXACTLY to the coach/club total; `earnings_clients`) · **`GET financials/transactions`** (`?category=&user_id=
&earned_by=<coach_uuid|club>&month=` — the client/service → transaction drill, same CTE so it sums to the row;
`earnings_transactions`) ·
`GET coach-statement` · `POST coach-statement/arrears/<id>/collected` ·
**`PATCH coach-statement/arrears/<id>`** (discount/write-off) ·
**`GET financials/settlement`** (the "who owes what" aging view: clients bucketed by age + coaches with a
non-zero `coach_ledger` balance — the club↔coach settlement worklist; `commission.settlement_overview`) ·
**`GET coach-payouts`** (`?coach_user_id=` — list recorded settlements) · **`POST coach-payouts`** (record a
club↔coach settlement — `{coach_user_id, amount_minor, direction, method?, reference?, period_label?, note?,
status?}`; a `paid` payout nets the `coach_ledger` balance append-only; `commission.record_coach_payout`) ·
**`PATCH coach-payouts/<id>`** (flip status: draft→paid posts the ledger entry, or void a draft;
`commission.set_payout_status`) · `GET refund-requests` ·
`POST refund-requests/<id>/{approve,decline}` · **`GET people/<id>`** (the unified **person 360** —
profile + all roles + active membership + owed statement + online payments + bookings; if the person is a
coach, a settlement summary; `admin/repositories.py::get_person` — now **delegates to `client360.get_client_360`
`scope='admin'`**, a superset payload, so this endpoint gains the client's packages/wallets + refunds + activity
in one read) · **`GET bookings/<id>`** (the **admin
event story** / god-view — client + coach + charge + coaching-arrears + full action eligibility;
`diary/bookings.py::admin_booking_story`) · **`POST bookings/<id>/reassign-coach`** (move a future/unpaid
lesson to another bookable coach; `admin_reassign_coach`).

**Insights `/api/insights/*` (Phase-2 P1 read-layer, lane `insights/`):** **`GET court-utilisation`**
(`?days=` — booked-vs-available court-hours by weekday×hour + overall % → the Overview → Courts heatmap) ·
**`GET sales-by-day`** (`?month=` — daily NET takings grouped by day (gross − gateway reversals), each sale
= client + service type + amount, now also split per day into **`online_minor`** (Yoco/gateway) vs
**`offline_minor`** (cash / EFT / card-at-desk) → Money → Sales by day) · **`GET bookings-by-day`** (`?month=` — bookings grouped by the day
played, each = client + service + **coach** + status + `booking_id` drill → Money → Bookings by day;
sibling of sales-by-day but over `diary.booking`, so it also shows membership-covered/R0 bookings) ·
**`GET overview`** (`?month=` — the month-scoped **daily** business composer: dense per-day series for
traffic [visits/unique + **public-vs-member-area** + **logged-in**], bookings by type + member-covered,
revenue gross/net, new clients, **active members** [uses the new `period_start`/`cancelled_at`], NPS
[corrected to `submitted_at`] + KPI totals + traffic breakdowns → the native admin **Overview** tab;
revenue reuses the sales-by-day basis and bookings the bookings-by-day basis, so it **reconciles** with
the Money lists by construction). Admin-gated, guarded (missing/empty → empty payload, never 500).
`insights/repositories.py`; registered in `app.py`.

**Coach `/api/coach/*`:** `GET/PATCH profile` · `GET onboarding` · `POST/PATCH services`
(+`POST services/<pid>/rate`, `PATCH/DELETE services/<id>`) · `PUT hours` ·
*(the `/api/coach/bundle-plans` routes were REMOVED 2026-07-09 — a coach edits their packs under a service
via `POST/PATCH/DELETE /api/services/<product_id>/packages`; coach onboarding is now Profile/Hours/Services)* ·
`GET/POST/DELETE time-off` · **`POST clients`** (a coach creates a walk-up/off-system client — SAME as the
admin People "New client"; `first_name`+`surname`+email required, delegates to `admin.create_client`) ·
`GET clients` · **`GET members/search`** (`?q=` type-ahead client lookup for
"book a client", min 2 chars; `coach/repositories.search_members`) · **`GET packages`** (every client
holding an active lesson pack with THIS coach + remaining balance — the coach's "clients with packages"
view; `coach/repositories.py::coach_package_holders`) · **`GET members/<client_user_id>/packages`**
(a client's ACTIVE packs THIS coach can draw — coach-specific to them, or coach-agnostic; lesson filtered
to self, class/court agnostic — so "book a client" auto-routes to a prepaid pack instead of a new charge) ·
**`GET clients/<id>/360`** (`{person}`, `?month=YYYY-MM` — the shared **client read model** for the coach,
`client360.get_client_360` `scope='coach'` with coaching + packages filtered to THIS coach; `?month=` scopes
the coaching figures and adds the per-SERVICE breakdown; feeds `Widgets.ClientRecord`. *The parallel non-360
reader `GET clients/<id>` + `CoachAPI.client` was RETIRED 2026-07-11 — this is the ONE coach client reader,
the by-service breakdown now composes inside the 360*) ·
**`GET bookings/<id>`** (the coach **event story** — client/contact, court, charge, coaching-arrears line,
players+attendance, can-flags for accept/propose/decline/reschedule/cancel/mark-completed/no-show +
mark-collected/discount/write-off; `diary/bookings.py::coach_booking_story`) ·
`GET cockpit` (+ **plan_balances**, month-end-after-commission, + **`billed_minor`** = gross coaching value
for the month before write-off/discount/collection, distinct from collected `gross_minor`;
`coach/repositories.py::_coach_billed`) · **`GET money`** (`?month=` — the coach MONEY FOLD: this-month's
bookings folded to Billed−Discount−WrittenOff=Invoiced=Paid+Outstanding [order-status-driven, reconciles],
explicit club-commission + a per-event log; `coach/repositories.coach_month_money` / `_coach_month_events` /
`_fold_event`) · **the coach's slice of the ONE shared `Widgets.Earnings`** (all delegate to the SAME
`admin.repositories` readers, coach-scoped to this coach's own services): **`GET financials/revenue-me`**
(`?month=` — this coach's P&L, `revenue_coach_pnl` with `coach_scope=True`) · **`GET financials/earnings-by-service`**
(`?month=` — `earnings_by_service` scoped to this coach) · **`GET financials/revenue-clients`**
(`?category=&month=` — `earnings_clients`, coach-scoped) · **`GET financials/transactions`**
(`?category=&user_id=&month=` — `earnings_transactions`, coach-scoped) · **`GET orders/<order_id>/record`**
(the shared transaction record for a coach's own order — `diary.bookings.order_story` `scope='coach'`,
read-only, guarded to what the coach earned) · `POST photo-presign` ·
`GET classes*` (shared) · `POST coach-statement/...` (shared admin route, coach-gated for own).

**Services `/api/services/*`** (`services/routes.py` — the ONE surface a service is edited through by BOTH
owner + coach; the route enforces who may change what): read via `services.repositories.get_service` ·
**`POST /api/services`** — create a lesson: a coach creates one for themselves; the **OWNER creates one FOR
A CHOSEN COACH** (body `coach_user_id`, validated by `admin/repositories.is_club_coach`) → delegates to
`coach.repositories.create_service`. Owner also creates a **court service** here via Services "+ New".
Frontend: `Widgets.ServiceList` `onCreate(kind)` → admin Setup → Services "+ New" coach-picker modal
(`AdminAPI.createService`). · **`POST/PATCH/DELETE /api/services/<product_id>/packages`** — the ONE place a
prepaid pack is now created/edited (owner + coach), scoped to that service; delegates to
`billing.bundles.create_plan`/`update_plan`/`deactivate_plan` (owner+kind inherited from the product). This
REPLACED the removed standalone `/api/{admin,coach}/bundle-plans` write routes. **`PATCH …/packages/<plan_id>`
now also accepts `{adopt:true}`** — assign a legacy unscoped (product_id NULL) pack to THIS service so it
stops cross-showing under the coach's other same-kind services (guarded to `product_id IS NULL`;
`billing.bundles.assign_plan_product`).

**Client `/api/me/*`:** `GET/PATCH profile` · `GET/POST dependents` (+`PATCH/DELETE /<id>`) ·
`GET plan` (current plan + `is_trial`/`trial_days_left` + `membership_window`) ·
**`POST membership/cancel`** (self-cancel a paid membership) · `GET financials` ·
**`GET billing/summary`** (`?month=` — the client SPA's ORDER-based monthly by-category billing view;
`billing/me.py::billing_summary`) ·
**`GET activity`** (`?month=YYYY-MM` — the monthly **Activity** view: that month's bookings + **spend by
category** (money paid that month) + current outstanding; `billing/me.py::spend_by_category` + `statement`) ·
**`GET activity-summary`** (`?month=YYYY-MM` — the month-at-a-glance headline for the client Home + Client
360 rollup: sessions played (lessons/court/classes) + minutes + spend-by-service + billed/paid/outstanding +
the weekly-chart buckets; `billing/me.py::activity_summary`) ·
**`GET 360`** (`{person}` — the shared **client read model** for the client viewing THEMSELVES,
`client360.get_client_360` `scope='client'`; feeds the client `#/activity` record view via `Widgets.ClientRecord`) ·
**`GET bookings/<id>`** (the client **event story** for a booking — `diary/bookings.py::booking_story`) ·
**`GET statement`** (unified statement — unpaid `billing.order` rows, grouped by category) ·
**`POST statement/pay`** (`{order_ids?}` → `create_settlement_order` → Yoco; pay all or a subset) · `GET orders` ·
`GET/POST refund-requests` (+`POST /<id>/cancel`) · `GET notifications` · `POST notifications/read`.

**Web service (`web_app.py`, marketing host):** `GET/POST /contact` (the public contact form posts
here — emails the club via SES, self-gating; logs the lead if SES unset).

**Crons `/api/cron/*`** (OPS_KEY-guarded; every recurring one is fired by a **GitHub Action**, never a
Render cron — the four `render.yaml` crons stay commented out by design): `POST capacity-sweep` (exists but
NEVER needed — holds self-release by lazy expiry) · `POST reminders` · `POST membership-refill` ·
`POST reconcile-payments` (72h lookback by default; `reconcile-deep.yml` sweeps 100 days weekly so nothing
ages out unverified) · `POST analytics-ingest` (the CI push of GA4/GSC metrics into `core.web_daily`) ·
`POST db-fingerprint` · `POST ses-suppress` · `POST ses-account` · **`POST ses-selftest`** (sends a live SES
test + surfaces the real SES error; `diary/routes.py`) · `POST marketing-digest-email` · **`POST month-end`**.
`monthly-invoice` was **RETIRED** — no handler exists (`crons/trigger.py` drops it from `JOB_ROUTES`).

**`POST month-end` is PER-CLIENT-TRANSACTIONAL, TIME-BOXED and RESUMABLE.** It does NOT call
`run_month_end` (that is the single-transaction form kept for one club + the harness). The route drives
`commission.month_end_period` / `month_end_accrue` / `month_end_targets`, then `month_end_client` **in its
own `session_scope()` per client**, stopping at `max_seconds` (default 90, under gunicorn's 120s reaper) and
returning `{ok, complete, remaining, failed, elapsed_seconds}` for the caller to loop. It accrues coach
arrears + rent, then consolidates each owing client's open orders into ONE numbered statement invoice +
pay-link email (`invoice_issued`; else a plain `statement_ready`), idempotent per `(club,user,period)` via
`billing.month_end_notice`. Fired by `.github/workflows/month-end.yml` on the 25th, which loops until
`complete` and FAILS THE JOB on any non-200 / `ok:false`.

**Also live, and previously missing from this list:** `GET/POST /api/feedback` (token-guarded NPS to
Google-review funnel) · `GET/POST /api/subscribe` (token-guarded re-permission opt-in) ·
`GET /api/insights/{overview,bookings-by-day,sales-by-day,court-utilisation,trial-cohorts,web-metrics}` ·
the INVOICE surface `GET /api/billing/invoice/<id>` (+ `/pdf`, `POST .../mark-paid`, `POST .../void`),
`GET /api/me/invoices`, `GET /api/admin/clients/<id>/invoices`, `POST /api/admin/clients/<id>/statement-invoice`,
`GET /api/billing/receipt/<id>/pdf` · the PROMOTIONS surface `/api/admin/promotions*` +
`POST /api/billing/promo/{validate,apply}` · `DELETE /api/diary/time-off/<id>` ·
`POST /api/diary/bookings/<id>/add-player` · `GET /api/diary/members/search`.

**Tables previously missing from section 4:** `club.billing_profile` · `billing.invoice` ·
`billing.invoice_line` · `billing.promotion` · `billing.promotion_code` · `billing.promotion_redemption` ·
`billing.coach_payout` · `billing.month_end_notice` · `core.web_daily` · `core.offline_conversion` ·
`core.acquisition` · `diary.class_session_court`. **Columns added since:** `billing."order".covered_order_ids`
(the immutable "Pay all" snapshot) · `diary.booking.product_id` (the service actually booked) ·
`diary.enrolment.created_by_user_id` (WHO enrolled a player - the actor, distinct from the player and the
payer; enrol() gates payment modes only for member/guest, so without it a staff on-behalf seat was
indistinguishable from a leak).

**Lane files previously missing from section 2:** `billing/invoicing.py`, `billing/invoice_pdf.py`,
`billing/promotions.py`, `marketing_crm/feedback/`, `marketing_crm/repermission/`.

**Analytics `/api/analytics/*`:** `GET overview` (`?days`, `?club_id`) · `GET clubs`. **Tracking:**
`POST /api/track/page` (first-party page-view beacon; geolocation via Cloudflare `CF-IPCountry`) ·
**`POST /api/me/acquisition`** (first-touch gclid/utm capture from `attribution.js` → `core.acquisition`).
**Feeds:** **`GET /feeds/google-ads/offline-conversions.csv`** (`offline_conversions/`; HTTP Basic auth,
404/dark until `GOOGLE_ADS_FEED_USER`/`PASS` set — Google Ads scheduled-upload feed). **Core:** `GET /healthz`
· `GET /api/whoami`.

**Transactional email (`marketing_crm/email/ses.py`)** — no HTTP surface; called from `notifications.deliver`.
**LIVE since 2026-07-03**, riding the **Ten-Fifty5 (1050) AWS account** interim (CourtFlow's own AWS was
locked out): the module takes its OWN creds `SES_AWS_ACCESS_KEY_ID`/`SES_AWS_SECRET_ACCESS_KEY` +
`SES_REGION=eu-north-1` + `SES_SENDER=noreply@ten-fifty5.com` (`SES_FROM_EMAIL` also read). Still self-gates
(no creds → in-app only, never errors). Functions: `_from_source`, `html_wrap`, `send_email(…, from_name,
reply_to)`, `send_raw_email(…, attachments=)`, `send_booking_confirmation`. **NB: the `.ics` email attachment
is currently OFF by choice** (`EMAIL_ICS_ENABLED=0`) — the SES key DOES carry `ses:SendRawEmail`
(`AmazonSESFullAccess`), so it's set-the-flag-to-enable (invoice PDF attachments already use it,
`EMAIL_INVOICE_PDF_ENABLED=1`); the in-app `.ics` download works regardless. **Multi-tenant identity:** each club rides the one sender
with its own From display name (`club.club.name`) + Reply-To (its first `club.location.email`), resolved in
`notifications.py::_club_identity`. Long-term (verify `nextpointtennis.com`/`courtflow.app` DKIM once the
CourtFlow AWS account is back): **`docs/specs/SES-SETUP.md`**. No schema change.
**AUDITED + signed off 2026-07-11 (all 21 `KIND_MAP` kinds):** the rich block builder
`marketing_crm/email/booking_detail.py` (`load`/`_load_booking`/`_load_class`/`_load_order` +
`html_block`/`text_block`) resolves an order-keyed event to its booking/class (rich block) else a purchase
block tagged `order_kind`; `notifications.deliver` makes `payment_succeeded` the SINGLE confirm+receipt
email (retitle "Booking confirmed"/"Membership confirmed", suppress for pack + class orders). Payment-status
wording is single-sourced in **`billing.statement.settlement_status_label(state, mode)`** (email + `client360`
both delegate). `html_wrap` is a full doctype + viewport + table (Outlook-safe); client links → `/portal`;
coach BCC only on his own lesson/class. (`send_booking_confirmation` is legacy; live sends go through
`notifications.deliver`.)

## 4. Database — 5 schemas (idempotent boot DDL)
- **`club`**: `club`, `branding`, `location`, `policy`
- **`iam`**: `user`, `membership`, `coach_profile` (+`review_bookings`), `coach_invite`, `player_profile`, `dependent`
- **`diary`**: `resource` (+`kind='equipment'`, `quantity`, `feature_on_home`), `availability_rule`,
  `booking`, `booking_party`, `time_off`, `class_session`, `enrolment`, `waitlist`, `recurrence`,
  `reminder_log`, **`booking_equipment`**
  - *New (2026-07-12 — equipment hire):* **`diary.booking_equipment`** (`club_id, booking_id, resource_id,
    qty, price_id, amount_minor`) — the equipment items hired on a court booking; drives BOTH the billing
    line(s) on the booking's order AND the TIME-overlap availability count. `diary.resource` gained
    `kind='equipment'` + `quantity` (how many you own) + `feature_on_home` (a client-Home hero tile).
- **`billing`**: `product`, `price`, `order`, `order_line`, `payment`, `payment_attempt`,
  `account_ledger`, `membership_subscription`, `refund_request`, `bundle_plan`, `token_wallet`,
  `token_ledger`, `coach_agreement`, `commission_rule`, `commission_split`, `coach_ledger`,
  `coach_arrears`, **`coach_payout`**, **`month_end_notice`**
  - *New column (2026-07-13 — semi-private/squad lessons):* **`billing.product.max_clients int NOT NULL
    DEFAULT 1`** — the squad cap on a lesson service (1 = a normal private lesson; >1 lets a squad share ONE
    slot, each client billed their own owed order at the service price [per-head]). Only meaningful for a
    lesson product; set via the service editor (owner or the owning coach). See
    [[semi-private-lessons]] (SHIPPED 2026-07-13).
  - *New tables (2026-07-11 — club↔coach settlement + month-end sweep):* **`billing.coach_payout`** — a
    recorded club↔coach settlement (the missing half of the loop; the cockpit REPORTS the running
    `coach_ledger` balance, a payout pays it DOWN): `direction club_to_coach|coach_to_club|offset`,
    `amount_minor` (positive magnitude, ledger sign derived), `method eft|cash|offset`, `reference`,
    `period_label`, `status draft|paid|void`, `note`, `created_by_user_id`, `paid_at`. A `paid` payout posts
    ONE append-only `coach_ledger` `entry_type='payout'` entry, made idempotent by the new partial unique
    index **`ux_coach_ledger_payout`** (`ON billing.coach_ledger (club_id, coach_user_id, ref_id) WHERE
    entry_type='payout'` — one payout entry per payout row). **`billing.month_end_notice`** — the idempotency
    marker for the month-end statement sweep (PK `club_id,user_id,period_label` + `owed_minor`, `sent_at`), so
    a re-run never re-notifies a client. Plus **`billing.payment.recorded_by_user_id`** (cash-audit: who
    recorded a desk / at-court payment).
  - *New columns (2026-07-12 — peak pricing + membership entitlements + trial + equipment):* on
    **`club.policy`**: `peak_days` / `peak_start_min` / `peak_end_min` (the club court-peak window). On
    **`billing.price`**: `peak_amount_minor` (explicit per-duration court peak price), `max_covered_minutes`
    / `max_covered_per_day` / `max_courts_per_day` (silent membership caps), `is_trial` / `trial_days` (the
    tier that IS the signup trial). On **`billing.product`**: `members_covered` (a court service =false is
    PAYG-only for members, e.g. clay). The `kind` CHECKs on `billing.product` (+`'equipment'`) and
    `diary.resource` (+`'equipment'`) were widened (idempotent drop+re-add). Resolver: **`diary/entitlement.py`**
    (`court_covered` / `active_caps` / `service_members_covered` / `availability_context` / `slot_covered`) —
    the single source read by availability AND create_booking. See [EQUIPMENT-AND-CONSTRAINTS.md](EQUIPMENT-AND-CONSTRAINTS.md).
  - *Key recent columns (2026-07-09 — court services + per-service packs):* **`diary.resource.product_id`**
    (the court SERVICE a court belongs to — e.g. Hardcourt Hire vs Clay Hire; resolution = own product → club
    default court product → unscoped, `diary.pricing.court_service_for_resource`); **`billing.bundle_plan.
    product_id`** + **`billing.token_wallet.product_id`** (the SPECIFIC service a pack/wallet draws for —
    owner+kind inherited from the product; `match_wallet` is product-aware + backward-compatible: a
    product-scoped wallet draws only for its product, a legacy NULL-product wallet still matches by coach+kind,
    product-specific wins the tie-break; existing live packs stay NULL=legacy until
    `scripts/backfill_pack_products.py` maps them). Multiple `product(kind='court_booking')` court products are
    now supported (was effectively one).
  - *Key recent columns:* `product.status` + `price.status` (active/dormant/retired — the unified
    3-state lifecycle; `active` boolean kept in sync); `product.payment_modes` + **`price.payment_modes`**
    (per-tier/per-service allowed payment methods, CSV — layered tier→product→club resolution);
    `term_months`/`label` (membership plans) + `access_days`/`access_start_min`/`access_end_min`
    (membership access window); **`order.settled_by_order_id`** (links an owed child order to its
    'pay all' settlement order); **`order.status` now allows `void`/`written_off`** (admin void / debt
    write-off; a paid order can't be voided); `bundle_plan.status`;
    `token_wallet.base_minutes`/`minutes_total`/`minutes_remaining` (the unit
    engine's authoritative minute balance — `tokens_*` are display only); **`token_ledger` now records
    manual admin edits** — a new `kind='adjust'` (top-up/subtract via `adjust_wallet`) / `kind='expire'`
    (soft-expire via `expire_wallet`), each carrying new columns **`reason text`** + **`actor_user_id uuid`**;
    the `token_ledger` unique index was made **PARTIAL** (`WHERE kind <> 'adjust'`) so system draws/credits
    keep their per-(wallet,booking,kind) idempotency while manual adjusts stack freely; a soft-expire keeps
    the wallet row + ledger (never hard-deletes); **`billing.order_line.original_amount_minor`** preserves the
    pre-discount amount when an order is repriced by `discount_order` (the audit); **`booking.status` now allows
    `requested`/`proposed`** (lesson approval lifecycle — NOT in the GiST exclusion, so they hold no
    slot; gated by `iam.coach_profile.review_bookings`); **`class_session.court_resource_id`/`court_booking_id`**
    (a scheduled class can optionally **reserve a court** — `court_booking_id` is a court-blocking
    `diary.booking` reusing the GiST exclusion, freed on cancel).
- **`core`**: account/user/person, `usage_event`, consent, nps, `notification`, **`acquisition`** (gclid/utm,
  1:1 with app_user; `person.iam_user_id` = the iam↔core bridge), **`offline_conversion`** (Google Ads
  offline-conversion ledger, owned by `offline_conversions/schema.py`)
  *(the Business Overview analytics are read-only views over `core.usage_event` — no separate schema)*

Settlement modes on `billing.order`: `at_court`, `monthly_account`, `online`, `membership_covered`,
`token`, `free` (complimentary). Boot order + `BOOT_MODULES` in `db.py`.

## 5. Frontend (host-switched by `web_app.py`)
**Three role SPAs (the 2026-07-02 redesign — mobile-first drill-through; one `cf-*` design system).**
The old tab-based consoles are superseded by three single-page drill-through apps where every list row opens
its full **event story** (GOLDEN RULE: exactly ONE booking capability per app, reused everywhere). Blueprints:
[FRONTEND-REDESIGN.md](FRONTEND-REDESIGN.md) + [ADMIN-REDESIGN.md](ADMIN-REDESIGN.md).

**GOLDEN RULE — one widget per capability** ([FRONTEND-STANDARDISATION.md](FRONTEND-STANDARDISATION.md)):
the shared **`frontend/js/widgets/`** layer — **`Widgets.TransactionDetail`** (the one event story across
all three apps — now carries an **`add_player`** action [gated on `can.add_player`] that opens
`CRMUI.addLessonPlayerModal` to add a squad client to a semi-private lesson), **`Widgets.Calendar`** (the
admin diary — Day view = resource-timeline grid, Week/Month
agenda; see below), **`Widgets.Setup`** + **`Widgets.ServiceList`**
(owner + coach setup), **`Widgets.ClientRecord`** (the ONE client record — identity/membership/packages/owed
statement/payments/bookings/refunds/dependents/activity + the month **MONEY FOLD** (`month_events`/
`statement_fold`) & coach `service_breakdown`, fed by the `client360` composer; adopted by admin
`renderPerson`, coach `renderClient` and the client `#/activity` record view, role diffs = config; the three
hand-built person/client renderers were **deleted** and the coach client fork **retired** — every client view
is now ONE widget off the ONE composer), **`Widgets.Earnings`** (the ONE club-vs-coach earnings P&L —
`frontend/js/widgets/earnings.js`, loaded in `admin_app.html` + `coach_app.html`; admin scope = the whole club
[service → coach/club → client → transaction → the shared record], coach scope = the SAME widget filtered to
that coach, **config-only, no fork**; mounted at admin Money → **Club earnings** and the coach **Money** tab) —
plus promoted `window.UI` helpers (`card/backBar/kv/modal/statusChip/…`) and
`crm_ui.js` (`CRMUI.*`). Role differences = configuration (a data adapter + an actions capability-map +
`fields`), never forked render code.
- **Client** — `frontend/app/app.html` + `frontend/js/client.js`. ONE page, **no bottom nav** (Book from
  Home tiles; avatar top-right → profile). Home = greeting + book tiles + **Your sessions** (all,
  upcoming+past) + **Billing by category** (month nav → category → items → booking story / receipt) + Plan &
  credits. Drills via `GET /api/me/bookings/<id>` + `GET /api/me/billing/summary`. Served at `/`, `/portal`, `/app`.
  **Ten-Fifty5 embed (2026-07-11):** `#/analysis` route + `renderAnalysis()` iframes Ten-Fifty5's portal
  (`__TF5_EMBED_URL`), the member signed in via the `auth_client.js` token relay (no 2nd login); a Home card
  drills to it, **"Coming soon"** for members outside `TF5_EMBED_ALLOW_EMAILS`. `auth_client.js` gained the
  parent `serveChild` origin allowlist + a `mode` field in its status payload; `web_app.py` injects
  `__TF5_EMBED_URL`/`__TF5_EMBED_ALLOW` + substitutes `__TF5_EMBED_ORIGINS__`. Env + Render-service map:
  `ENV-STATUS.md`.
- **Coach** — `frontend/app/coach_app.html` + `frontend/js/coach_app.js`. **Bottom nav Home · Schedule ·
  Clients · Money · Setup.** Schedule = a **weekly calendar** (tap lesson → the event story; tap class →
  roster). Clients → full client record (by-service breakdown). **Money = the ONE shared `Widgets.Earnings`,
  coach scope** — this coach's P&L (service → client → transaction), the SAME widget the admin mounts, just
  filtered to this coach; a transaction drills to the shared record via a new **`#/txn/<order_id>`** route
  (`renderTxn` → `GET /api/coach/orders/<order_id>/record`). Setup = Services (lifecycle) + Classes
  (create/schedule/roster via `ClassUI`) + commission + Edit-profile/Weekly-hours pages. **THE ONE COACH EVENT
  STORY** (`#/event/:id` → `GET /api/coach/bookings/<id>`) carries the arrears actions (mark-collected /
  discount / write-off). Served at `/coach`, `/coach.html` (non-coaches bounced).
- **Admin / Owner — COMPLETE + LIVE** — `frontend/app/admin_app.html` + `frontend/js/admin_app.js`, served
  at **`/admin`** (also `/admin.html`, `/admin-app`). **Responsive:** bottom-nav on mobile, **left
  side-rail on desktop** (`.cf-admin`). Nav Home · People · Money · Diary · **Overview** · Setup. **Home**
  = command-center (4 focus cards, `GET /api/admin/home`) · **People** = roster → the
  **unified person 360** (`#/person/:id`, `GET /api/admin/people/<id>`) · **Money** = the reconciling money band
  + a Setup-style section menu (New invoice · Sales by day · **Club earnings** · Bookings by day · Approvals ·
  Club activity) — **Club earnings** opens the club-vs-coach P&L drill (the ONE shared `Widgets.Earnings`) and
  Sales by day now shows the **Online (Yoco) vs Cash/EFT** split; the standalone **Coach settlement** +
  **Online payments** tabs were **RETIRED** (settlement figures live in the earnings drill, payments in
  Sales / Club activity) ·
  **Diary** = the shared **`Widgets.Calendar`** (court/coach filters) + Classes — the **Day view is the
  resource-timeline GRID** (courts + coaches as columns, 06:00–22:00 rows, `cf-ev` blocks; config-driven via
  `cfg.grid`, empty coach columns hidden, courts always shown), **Week/Month stay agenda**; any block drills
  to the shared `Widgets.TransactionDetail` event story. Walk-in (Book a client → guest name), block-time
  (a **Block time** button → `POST /api/diary/time-off`) and desk-pay (on the transaction record) all live
  in the new console; only the classic diary's drag-to-create/move gesture is gone · **Setup**
  = the shared **`Widgets.Setup`** (Club profile+payments [+ **Peak hours**] · Courts · Services [court
  services carry a **Members-covered?** toggle + a per-duration **peak price**] · Memberships [+ **Member
  limits** + **Signup trial** on the tier editor] · **Equipment hire** · Coaches) · **Overview** (`#/overview`,
  a first-class nav tab as of 2026-07-05; the old `/overview.html`
  iframe retired) = month pager + sub-tabs **Traffic · Bookings · Revenue · Members · NPS · Courts**, all
  **daily** graphs for the month via one shared ECharts seam (`GET /api/insights/overview`); Traffic leads
  with a **public-site vs member-area** split + a **logged-in-visitors** line/tile; Courts = the
  court-utilisation heatmap. The **classic tab console was RETIRED 2026-07-18** (its last unique feature,
  block-time, was ported into the new Diary).
- **Web routes / redirects (`web_app.py`):** `/`,`/portal`,`/app` → `app.html` · `/coach`,`/coach.html` →
  `coach_app.html` · **`/admin`,`/admin.html`,`/admin-app` → `admin_app.html` (the NEW SPA)**. Old standalone pages **302 → the client SPA**
  (`/book.html`→`/portal#/book/court`, `/my`→`/portal#/bookings`, `/account.html`→`/portal#/billing`). The
  classic **coach** console (`coach.html`/`coach.js`) and the classic **owner** console
  (`admin.html`/`admin.js`) were **deleted** (owner retired 2026-07-18); **the `/admin-classic` PATH still
  exists as a 301 to `/admin`** (`web_app.py`) so old bookmarks don't 404.
  Post-login role routing (`client.js`) lands admins on `/admin`, coaches on `/coach`.

**Portal SPA shells** — the EXACT contents of `frontend/app/` (each `cf-*` design system, absolute asset
links). The three role SPAs are **`app`** (client) · **`coach_app`** (+`coach-onboarding`) · **`admin_app`**
(served at `/admin`); the rest are standalone pages: `portal` (shell/dashboard) · `plans` (consolidated
Membership/Packs/PAYG — served at `/plan`; `/membership` + `/packs` 301 here) · `onboarding` (owner) ·
`settings` · `overview` (**Business Overview dashboard**, ECharts) · `invoice` · `receipt` · `pay-return` ·
**`feedback`** (the token-guarded NPS to Google-review funnel) · **`subscribe`** (token-guarded
re-permission opt-in) · `styleguide`.
*Deleted, do not look for them:* `book`, `my`, `account`, `coach`, `admin` and `statement` shells — the
booking flow, my-bookings, account and statement are all ROUTES inside the SPAs now, and the old paths 301
or 302 into them.

**Role-focused nav (`frontend/js/portal.js` + `home.js`).** Nav is role-precise — the client booking Home +
Account no longer show to staff:
- member/guest → **Home · Account**
- coach → **Coach** (landing) · Account
- club_admin/platform_admin → **Admin** (landing) · Settings

Post-login role routing (`client.js`, the SPA entry) lands members on the client Home, coaches on `/coach`,
admins on `/admin`; `Portal.landingFor` is the legacy equivalent for the old `*.html` shells.

**Classic tab consoles — DELETED.** The coach console (`coach.js`/`coach.html`) was **deleted**; the owner
console (`admin.js`/`admin.html`) was **deleted 2026-07-18** (the `/admin-classic` path survives only as a
301 to `/admin`). Its last unique
feature (block time / time-off) was ported into the new admin Diary (a **Block time** button →
`POST /api/diary/time-off`); walk-in (Book a client → guest name) and desk-pay (on the transaction record)
already lived in the new console — only the drag-to-create/move gesture is gone. The live consoles are the
three SPAs above, all on the shared widget layer.

**JS modules** (`frontend/js/*.js`): **`client`** (client SPA — Home/sessions/billing-by-category/event
story) · **`coach_app`** (coach SPA — bottom-nav Home·Schedule·Clients·Money·Setup + the one coach event
story) · **`admin_app`** (admin SPA — COMPLETE + LIVE at `/admin`; responsive shell + command-center Home) ·
`portal` (role-focused nav + `landingFor` + notification bell) ·
`home` (client Home + staff redirect) · `booking` (full-screen; replaced `book`/`quickbook`) ·
`plan` · `coach_api` · `coach_onboarding` · `admin_api` · `class_ui` (`AdminUI.courtsManage` = per-court
click-to-edit hours) · `invoice` · `receipt` · `pay` · `pay_return` · `service_editor` · `notifications` ·
`analytics` · `attribution` · `auth_client` · `api` · `ui` · `wizard` · `overview` · `settings` ·
`onboarding` *(**DELETED, do not look for them:** `my.js`, `account.js`, `coach.js` and `admin.js` — the
5-tab consoles and their pages are gone; their capabilities are routes inside the three SPAs)* ·
**`crm_ui`** (shared CRMUI components for both consoles — now incl. **`CRMUI.activityBlock`/`spendBlock`/
`weekChart`**, the shared month-at-a-glance activity + spend-by-service + weekly-chart blocks rendered on the
client Home AND the Client 360 rollup; plus the MONEY-FOLD renderers **`CRMUI.moneySummary`** (the
Billed→Collected→Outstanding band) + **`CRMUI.statementFold`** (the authoritative fold summary — single
"paid" figure), the **`CRMUI.createClientModal`** "New client" form (admin + coach; first-name/surname split,
+27 country code) and **`CRMUI.addLessonPlayerModal`** (add a squad player to a semi-private lesson)) ·
`settings` · `onboarding` · `notifications` ·
`pay` (`Pay.purchase`/`buyMembership`/`buyPack` — THE payment rule) · `pay_return` · `receipt` ·
`analytics` (page-view beacon) · `overview` (Business Overview dashboard) · `api` · `auth_client` ·
`ui` (+`UI.lifecycleBar`/`lifeActions`/`statusChip`/`subtabs` lifecycle helpers). The grouped
tick-to-pay "Your statement" card is rendered by the client SPA (`client.js`) - `account.js` is gone. **One design system:** `frontend/app/app.css` (all `cf-*`
classes — incl. `.cf-lifefilter`/`.cf-subtabs`/`.cf-cal*`; the SPA redesign added `.cf-bottomnav*`,
`.cf-appbar`, `.cf-avatar`, `.cf-kv*`, `.cf-owe`, `.cf-amountbig`, and `.cf-admin` for the desktop
side-rail). Marketing site: `frontend/marketing/`, `frontend/_shared/`.

## 6. Env / config
**Full reference: `docs/specs/ENV-STATUS.md`** — every var, live/dark status, copy-paste checklist.
Live now: `DATABASE_URL`, `OPS_KEY`, Clerk `AUTH_*`, Yoco (`PAYMENTS_ENABLED=1`, `PAYMENTS_PROVIDER=yoco`,
`YOCO_SECRET_KEY`/`YOCO_PUBLIC_KEY`/`YOCO_WEBHOOK_SECRET`), `APP_BASE_URL`, `SEED_NEXTPOINT=1`,
`MARKETING_HOSTS`, the **Ten-Fifty5 members-area embed** (`TF5_EMBED_URL`, `TF5_EMBED_ORIGINS`,
`TF5_EMBED_ALLOW_EMAILS` — the last gates a private test to one email; its Ten-Fifty5-side counterpart is
`AUTH_ISSUERS` on the "Sport AI - API call" service + `TF_TRUSTED_PARENT_ORIGINS` on `locker-room`), and
**transactional email** — LIVE via the interim Ten-Fifty5 AWS account
(`SES_SENDER=noreply@ten-fifty5.com`, `SES_AWS_ACCESS_KEY_ID`/`SES_AWS_SECRET_ACCESS_KEY`,
`SES_REGION=eu-north-1`; **`EMAIL_ICS_ENABLED=0`** — the .ics attachment is off BY CHOICE, not blocked: the
sending key carries `AmazonSESFullAccess` (`ses:*`, incl. `ses:SendRawEmail`), which is why the invoice-PDF
attachment already sends. Flip the flag to turn .ics on). Dark until keyed: `KLAVIYO_API_KEY`
(CRM/marketing — self-gates), `S3_BUCKET`+AWS keys (photo uploads), Google-tag/GSC vars
(`GA4_MEASUREMENT_ID`/`GOOGLE_ADS_*`/`GSC_*` — set at go-live cutover).
**Note:** the old `*_ENABLED` toggles (`YOCO_/TRACKING_/CONSENT_/CRM_SYNC_`) plus the dead
`BRIDGE_TENFIFTY5_*` trio (`_ADMIN_EMAIL`/`_CLIENT_KEY`/`_URL`) were dead config (never read) — dropped from
the live services on the Frankfurt recreate and **must not be re-added**; those features are always-on or
self-gate on their keys. `render.yaml` now also pins `region: frankfurt` + `plan: starter` on both web
services and declares `SES_REGION=eu-north-1` + `SEED_NEXTPOINT=1`; secrets are still entered in the Render
dashboard (`sync:false`).

## 7. Verify gates (no live infra)
- Compile: `python -m py_compile $(git ls-files '*.py')`.
- Schema idempotency: `python -m db` **twice** → second run a no-op.
- Integration: throwaway `postgres:16` + `python -m scripts.seed_nextpoint`; scenario harnesses
  `python -m scripts.test_all` → **booking 263 / billing 439 / statement 64** (`test_booking_scenarios` /
  `test_billing_scenarios` / **`test_statement_reconciliation`** — no double-count, pay-all-once, partial
  settle, void/write-off, arrears↔orders lockstep, plus coach/per-service two-tier pricing, class rate-card,
  on-behalf pack draw, cancel-fee/paid-resize & covered-reschedule guards, plus **`sc_wallet_adjust`** +
  **`sc_order_discount`** (billing) and **`sc_discount_reconcile`** (statement) for the Client 360 sprint,
  plus the **court-service allocation** checks (booking +18) and the **per-service-packs** scenario
  (billing +44) for the 2026-07-09 court-services + per-service-packs sprint, plus the **semi-private (squad)
  lesson** checks — per-head billing, `add_lesson_partner` full/dup guards, `_addable_player_uid` route gate,
  `allowed_purchase_modes` card-only-service refusal — for the 2026-07-13 squad-lessons sprint).
- Frontend: `node --check <file>.js`.

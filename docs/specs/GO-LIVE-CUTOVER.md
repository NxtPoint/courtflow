# GO-LIVE — NextPoint Wix → Render production cutover + data migration (target: Sunday)

> **STATUS (2026-07-05): this is the PLAN/reference. Execution is largely DONE — see
> [`CUTOVER-PROGRESS.md`](CUTOVER-PROGRESS.md) for actual state and
> [`GO-LIVE-STEPS.md`](GO-LIVE-STEPS.md) for the click-by-click cutover sequence.**
> Done: `scripts/import_wix.py` + `import_members/subscriptions/lessons.py` (data migrated to prod —
> 878 members / 11 memberships / 8 lesson wallets), the 48-rule 301 map (curated from the real GSC crawl),
> transactional email (interim via Ten-Fifty5 SES — working), prod Clerk instance (SSL live, `email`
> claim set), GA4 + Ads tags live. **Remaining = the supervised cutover itself** (attach Render domain,
> swap Clerk env, flip DNS apex+www, move Wix to its free URL, GSC sitemap). The "agent builds …"
> sections below are the original plan and are now COMPLETE.

**This is a REAL production cutover** — NextPoint is LIVE on Wix with **~900 clients** and **~60 visitors/day**.
(Contrast: the Ten-Fifty5 / 1050 cutover was pre-launch with ~0 customers, so it never needed a Wix data
importer or a real 301 map — we do. The proven-reusable parts of 1050 are reused; the two live-data parts
are built fresh.) Timeline: **Thu (today) → Sun go-live.**

## 0. The hard rules (read first)
- ⚠️ **NEVER touch the `api.nextpointtennis.com` DNS record** — it is the **live 1050 API**; breaking it is a
  disaster (CLAUDE.md gotcha, docs/07). We cut over ONLY the apex `nextpointtennis.com` + `www`.
- ⚠️ **Never let an agent change DNS / Google consoles / Clerk console.** Those are **Tomo, supervised**
  (docs/11 §5: "DNS/SEO cutover is supervised, never an agent"). §10 draws the agent-vs-Tomo line.
- **Keep Wix LIVE until the cutover is verified.** Move it to its free `*.wixsite.com` URL; do NOT delete it.
  **Rollback at any step = point DNS back to Wix** (fast, because we pre-lower the TTL).
- **API stays on `courtflow-api.onrender.com`** (or a NEW `api.courtflow.app` later) — do NOT put the API on
  `api.nextpointtennis.com` (1050 owns it). Only the public/portal domain cuts over.
- Everything is **additive + env-gated + reversible** (the 1050 discipline), so every switch is a rollback lever.

## 1. Timeline (Thu → Sun)
- **Thu (today):** ① lower DNS TTL to 300s ② capture the SEO footprint → `migration/url_inventory.csv`
  ③ export Wix data (contacts / members / plans) ④ create the **production** Clerk instance ⑤ agent builds
  `scripts/import_wix.py` and runs it **dry-run** against the local sandbox.
- **Fri:** ⑥ curate `migration/redirects.csv` from the inventory ⑦ wire `register_redirects(app)` ⑧ add
  GA4 / Google Ads / GSC-verification hooks ⑨ import dry-run → review counts → **commit import to the prod DB**
  ⑩ QA on `courtflow-web.onrender.com`.
- **Sat:** ⑪ full staging audit (301s, canonicals, robots, sitemap, NAP/JSON-LD, smoke) ⑫ email the ~900
  clients ("we've moved — sign in fresh with your email") ⑬ content/design freeze.
- **Sun:** ⑭ final deploy ⑮ swap env to the real domain + prod Clerk ⑯ **Tomo flips DNS (www + apex)** ⑰ GSC
  submit sitemap + Request Indexing ⑱ smoke test + monitor. Rollback ready.

---

## 2. Pre-flight (Thursday)

### 2a. Lower the DNS TTL — Tomo (the piece 1050 never recorded; you need it)
In the DNS host for `nextpointtennis.com`, set the TTL on the **apex + www** records to **300s NOW**, so
Sunday's flip (and any rollback) propagates in minutes, not hours. ⚠️ Do **not** touch `api.nextpointtennis.com`.

### 2b. Capture the SEO footprint → `migration/url_inventory.csv` — Tomo + agent (docs/07 Step 0)
Before touching anything, snapshot what ranks: **GSC** (top pages / queries / landing URLs, 12 mo), **Ahrefs**
(top pages, keywords, backlinks), and a **full Wix crawl** (Screaming Frog or similar) of every live URL.
Write `migration/url_inventory.csv` (`old_url, monthly_clicks, current_title`). This is the source for the
301 map. Principle: **rankings > tidiness** — keep the highest-traffic paths as close to their old slug as possible.

### 2c. Export the Wix data — Tomo
- **Contacts** (~900): Wix Dashboard → Contacts → Export → CSV (email, first name, last name, phone, opt-in).
- **Members / Pricing Plans** (~10 members, ~10 lesson plans): Wix → Pricing Plans / Members → export (who
  holds which plan + expiry).
- **Bookings / orders** if available (for history — optional).
- ⚠️ **Wix passwords CANNOT be exported.** Clients therefore sign in **fresh** (Clerk password / magic-link)
  and their new identity **auto-links to their imported record by email** (§6). Set this expectation in the
  pre-cutover email.

### 2d. Create the PRODUCTION Clerk instance — Tomo (docs/11 D4)
Today's Clerk is a **DEV** app (`pk_test_…`, `settling-alien-23.clerk.accounts.dev`). Create a Clerk
**production** instance for `nextpointtennis.com`; collect the **prod publishable key**, **JWKS URL**,
**issuer**, and a **JWT template that emits the `email` claim** (the linking key — see `auth/verifier.py`).

---

## 3. Data migration — agent builds `scripts/import_wix.py`
The big new build (1050 had no Wix importer). **Model the discipline** on 1050's `core_db/backfill.py` +
`billing_import_from_bronze.py`: **dry-run default, idempotent, dedup by `lower(email)`, dry-run→rollback,
club-scoped.** Reuse NextPoint's own insert patterns (all confirmed in the codebase survey):

### Inputs (Wix CSV exports from §2c)
- `clients.csv` — email, first_name, surname, phone, marketing_opt_in (~900).
- `members.csv` — email → active membership + Wix plan name + expiry date (~10).
- `plans.csv` — lesson-plan / pack definitions: label, sessions, duration, price (~10).

### Targets + the exact patterns to reuse
| Wix entity | NextPoint target | Reuse pattern |
|---|---|---|
| every client | `iam.user` (email-keyed, **`clerk_user_id` NULL**) + `iam.membership(role='member', member_status='active')` | `scripts/seed_nextpoint.py::_upsert_iam_user` / `_upsert_membership` (or `iam/repositories.upsert_membership`) |
| active member | `billing.membership_subscription` (`provider='manual'`, `current_period_end` = Wix expiry) | `admin/repositories.py::grant_membership` (resolves the club's membership-product price; idempotent) — **map the Wix plan name → the right NextPoint membership tier/`billing.price`** |
| lesson plan (definition) | `billing.bundle_plan` (`service_kind='lesson'`, label, sessions_count, price_minor) | `scripts/seed_nextpoint.py::_seed_court_packs_if_possible` (same shape, `service_kind='lesson'`) |
| member with a **remaining** lesson balance | `billing.token_wallet` (minutes-based balance) | the wallet shape in `billing/schema.py` (`tokens_total/remaining`, `minutes_total/remaining`, `status='active'`) |
| dependent / child | `iam.dependent` (+ login-less `iam.user`) | `iam/repositories.py::create_dependent` |

### Discipline (non-negotiable)
- `--dry-run` is the **default** — prints exactly what it WOULD create (counts per table), then rolls back.
  `--commit` writes. Run against the **local sandbox first**, eyeball, then the prod DB.
- **Idempotent + re-runnable** — dedup by `lower(email)`; safe to run twice (no duplicate humans/memberships).
- **Validate, don't abort** — bad/blank emails, dupes, unmatched plan names → logged + skipped, with a summary
  report at the end (imported / skipped / errors).
- **Club-scoped** — everything writes `club_id` = NextPoint's club id.
- **Membership caveat:** getting the tier wrong = wrong coverage/price. Dry-run, print the plan-name→tier map,
  and have Tomo confirm the ~10 members before `--commit`.

---

## 4. SEO cutover — agent code + Tomo submit

### 4a. Curate `migration/redirects.csv` — agent (from `url_inventory.csv`)
For every old Wix URL whose slug **changes**, add a row `old_path,new_path,301`. Preserve high-traffic slugs
where possible; **301 not 302**, no chains, self-referential canonicals. (17 starter rules already exist.)

### 4b. Wire the redirect engine — agent, supervised
The engine already exists (`migration/redirects.py`), just not registered. Add to `web_app.py`, **before the
catch-all 404**:
```python
from migration.redirects import register_redirects
register_redirects(app)   # marketing-host 301s ahead of routing
```
(Per `migration/CUTOVER_RUNBOOK.md` step 2.) Verify with `curl -sI https://…/old-path` → `301` → clean path.

### 4c. robots / sitemap — already generated in `web_app.py` (`_MARKETING_URLS` + blog slugs). Confirm they
emit the **real domain** post-cutover (canonical base = the request host). Nothing to build.

### 4d. Canonicals + NAP JSON-LD — CRITICAL, and it intersects with the Fable 5 rebuild
Canonicals + OG + `SportsActivityLocation`/`LocalBusiness` JSON-LD are **hardcoded per marketing page**
(`frontend/marketing/*.html`). **Fable 5 is rebuilding the public site concurrently** — the rebuilt pages MUST
keep: (i) `<link rel="canonical" href="https://www.nextpointtennis.com/…">`, (ii) the `SportsActivityLocation`
JSON-LD with **NAP (name/address/phone) identical to the Google Business Profile**, (iii) the
`<!--#include nav-->` / `<!--#include footer-->` chrome markers, (iv) the `_inject_head` behaviour intact
(theme + analytics + auth config). If Fable drops these, local SEO + analytics break — put this in Fable's brief.

### 4e. Blog — run `python build_blog.py`, commit the generated HTML, so blog slugs are preserved (rankings carry over).

---

## 5. Google — Search Console + Ads + analytics (1050 had NONE of the Google parts; build fresh)

### 5a. Search Console verification — agent serves the token, Tomo verifies
Mirror 1050's HTML-file method: add a route in `web_app.py` returning the GSC verification file (Tomo supplies
the token), OR use a DNS TXT (Tomo). Then GSC → add property (`nextpointtennis.com`) → verify → **submit
`/sitemap.xml`** → **Request Indexing** on the top 20–30 URLs.

### 5b. GA4 + Google Ads conversion tracking — agent adds `gtag`, Tomo supplies IDs
NextPoint's own analytics is a cookieless first-party beacon (`analytics.js` → `/api/track/page`) that powers
the in-app **Insights** dashboard — **keep it**. For **campaign attribution** add the Google tag in the single
injection point `web_app.py::_inject_head` (fires on every page): GA4 (`G-XXXX`) + Google Ads (`AW-XXXX`), IDs
from Tomo. Define conversions: **sign-up · booking-confirmed · membership-purchase** (fire the Ads conversion
on the pay-return / booking-success page). This is the layer that lets you see how the Google Ads campaigns land.

### 5c. Link Google Ads ↔ GA4 — Tomo, in the Google consoles (campaign → conversion visibility).

---

## 6. Auth cutover — Tomo swaps Clerk env; the email-linking is automatic
- Swap Render env (api + web services): `AUTH_JWKS_URL`, `AUTH_ISSUER`, `CLERK_PUBLISHABLE_KEY`, `AUTH_API_BASE`
  → the **prod** Clerk instance (§2d); keep `AUTH_ENABLED=1`; ensure the JWT template emits `email`.
- **Linking is automatic:** an imported `iam.user` (`clerk_user_id NULL`) links to the Clerk identity on the
  client's **first login, by email** (`iam/repositories.py::upsert_user_by_clerk_id` step 2). So a client signs
  up fresh and lands on their existing record + membership. No Clerk bulk-import needed.
- **Pre-cutover email to the ~900 clients (Sat):** "We've upgraded NextPoint. Sign in at nextpointtennis.com
  with your email (set a new password / use the magic link). Your bookings, membership and history are already
  there waiting for you."

---

## 7. Go-live env + infra (Sunday, Render dashboard — Tomo)
- **Attach the custom domain** `nextpointtennis.com` + `www` to the `courtflow-web` service (Render manages SSL).
  `MARKETING_HOSTS` already includes both, and `branding.py` already maps them → NextPoint.
- **Env:** `APP_BASE_URL` + `AUTH_AFTER_LOGIN_URL` → `https://nextpointtennis.com`; `AUTH_API_BASE` → the API
  host (keep `courtflow-api.onrender.com`, or `api.courtflow.app` if you set one — **not** api.nextpointtennis.com).
- **Bump `courtflow-api` + `courtflow-web` from Free → Starter** (never sleep) so real traffic never hits a cold
  start; the keep-warm GitHub Action can be removed.

---

## 8. Cutover sequence (Sunday) — reversible at every step
1. **Final deploy** to Render (redirects wired · GA/Ads/GSC hooks · imported data live · prod-Clerk env).
   Verify on `courtflow-web.onrender.com` first.
2. **Smoke on onrender:** home renders · `/login` on prod Clerk · a test client signs in and **links by email** ·
   a test booking · `robots.txt` + `sitemap.xml` · a few 301s via `curl -sI`.
3. **Tomo flips DNS:** point apex + `www` at the Render web service. ⚠️ **NOT** `api.nextpointtennis.com`. Move
   the Wix site to its free URL (don't delete).
4. **GSC:** submit `sitemap.xml`, Request Indexing on the top 20–30.
5. **Monitor:** first-party beacon + GA4 Realtime + Render logs. Watch 301s resolve and sign-ins linking.
- **Rollback (any step):** point DNS back to Wix (minutes, thanks to the 300s TTL). Nothing else needs a redeploy.

## 9. Smoke tests / post-deploy
- **Web test-client gate (14/14):** host-switch · portal-shell serving · robots/sitemap · branded 404. (Add
  lightweight health endpoints if `web_app.py` lacks them — 1050 had `/__alive` / `/healthz`.)
- **Manual:** sign in as an imported client (links by email) → membership shows; book a court; owner + coach
  consoles load; a 301 from an old Wix URL lands clean; GA4 Realtime registers the visit.

## 10. Agent vs Tomo — the hard line
- **AGENT (code, in the new chat):** build `scripts/import_wix.py` (dry-run first) · curate + wire the 301
  redirects · add the GA4/Ads/GSC-token hooks in `_inject_head` · run `build_blog.py` · verify the gates ·
  make sure the Fable-rebuilt marketing pages keep canonical + NAP JSON-LD + chrome markers.
- **TOMO (supervised — never an agent):** lower the DNS TTL · export the Wix data · create the prod Clerk
  instance · attach the Render custom domain · **the DNS flip** · GSC verify/submit · Google Ads/GA4 console
  linking · the pre-cutover client email · the **go/no-go** + rollback.

## 11. Risks & gotchas
- ⚠️ `api.nextpointtennis.com` = the **live 1050 API** — do not touch that DNS record.
- Wix passwords aren't exportable → clients re-auth (link by email). Manage it with the Saturday email.
- Membership tier mapping (~10 members): wrong tier = wrong coverage/price — dry-run + Tomo confirm.
- **Fable 5 is rebuilding the public site in parallel** — it must preserve canonical + NAP JSON-LD + the
  `_inject_head`/chrome markers, or SEO + analytics + theming break. This is the biggest coordination risk.
- Free-tier cold starts vs ~60/day live traffic → move to Starter before cutover.
- Keep Wix warm + low TTL so rollback is instant.

## 12. Source material (for the executing agent)
- NextPoint assets: `migration/CUTOVER_RUNBOOK.md`, `migration/redirects.csv` + `redirects.py`, `web_app.py`
  (host-switch/robots/sitemap/`_inject_head`), `frontend/_shared/branding.py`, `scripts/seed_nextpoint.py` +
  `scripts/provision_club.py`, `admin/repositories.py::grant_membership`, `iam/repositories.py`, `build_blog.py`,
  `docs/07` (SEO plan), `docs/11 §5` (DNS/go-live decisions), `BUILD_PROMPT.md` (pre-flight).
- 1050 reusable patterns (READ-ONLY `C:\dev\webhook-server`): `locker_room_app.py` (host-switch/robots/sitemap/
  GSC token), `build_blog.py`, `core_db/backfill.py` + `billing_import_from_bronze.py` (idempotent/dry-run
  importer discipline), `docs/business/marketing-and-seo.md` (DNS cutover narrative + the "never click Try
  Again in Wix Domains" gotcha), `docs/business/_archive/wix-migration-record.md` (the phased plan).
</content>

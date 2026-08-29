# ENV-STATUS — every environment variable, what it lights up, and what's set

> **⚡ POST-CUTOVER LIVE VALUES (2026-07-05) — these SUPERSEDE any dev/pending values in the tables below:**
> - **courtflow-api:** `AUTH_ISSUER=https://clerk.nextpointtennis.com` · `AUTH_JWKS_URL=https://clerk.nextpointtennis.com/.well-known/jwks.json`
>   (prod Clerk, NOT the old `settling-alien-23.clerk.accounts.dev`) · `APP_BASE_URL=https://nextpointtennis.com`
>   · **`TRANSACTIONAL_BCC=info@nextpointtennis.com`** (NEW — blind-copies the club on transactional email;
>   committed in `render.yaml`) · `SEED_NEXTPOINT=1` · SES_* interim (ten-fifty5) live.
> - **courtflow-web:** `CLERK_PUBLISHABLE_KEY=pk_live_…` (prod) · **`GA4_MEASUREMENT_ID=G-EKQP47P8M9`** ·
>   **`GOOGLE_ADS_ID=AW-17077631191`** · **`GOOGLE_ADS_CONVERSIONS={"start_free_week":"AW-17077631191/rEy7CNKNsc4cENfxn88_","booking":"AW-17077631191/tu5JCNWNsc4cENfxn88_"}`**
>   (all LIVE, updated 2026-07-11). **courtflow-api:** `GOOGLE_ADS_FEED_USER` / `GOOGLE_ADS_FEED_PASS` (offline-conversion CSV feed). Both services on **Starter** (no cold starts).
> - **Clerk (console, not env):** a **custom Google OAuth** Web client is wired (redirect
>   `https://clerk.nextpointtennis.com/v1/oauth_callback`) so "Continue with Google" works in production.
> - **Klaviyo (marketing email) is LIVE** — `KLAVIYO_API_KEY` set 2026-08; ~500 subscribers, flows +
>   campaigns sending (538 emails went out in the Spring Day sequence, 25-29 Aug 2026).
>   Still dark (key not entered): **S3** (coach photo uploads).

**What this is:** the single source of truth for environment variables. `render.yaml` does **not**
auto-push to Render — you type env into the Render dashboard manually — so this sheet (derived from a
full `os.getenv` scan of the code) is the list to work from.

**Live-audit (2026-06-21):** all code is deployed on both services — every API route answers `401`
(exists, auth-gated), every public page `200`. Nothing is "stuck in yaml". As of 2026-07,
**transactional SES email is LIVE** (interim, via the Ten-Fifty5 AWS account), and as of 2026-08
**Klaviyo marketing email is LIVE too**. The only thing still dark is **S3** (coach photo uploads).

Legend: 🟢 set & working · 🟡 optional, dark until you add the key · ⚪ has a safe default, usually skip.

---

## TL;DR — what's live vs one key away
- 🟢 **Live now (env already set):** the whole app — login, booking, classes, the three purchasing
  models, **Yoco payments + refunds + receipts**, the **Business Overview dashboard + page beacon**, and
  **transactional email** (invites + booking/statement confirmations) via the interim Ten-Fifty5 SES.
- 🟢 **Now live (2026-07-18):** the **month-end statement sweep** — the **`OPS_KEY` GitHub repository secret**
  is **SET** (same value as `courtflow-api`'s `OPS_KEY` env var), so `.github/workflows/month-end.yml` fires the
  OPS-guarded `POST /api/cron/month-end` on the **25th of each month at 08:00 SAST** (`cron: "0 6 25 * *"` — the
  club's billing day, inside the keep-warm window). The **SES sending key now carries `AmazonSESFullAccess`**
  (`ses:*`, which **includes `ses:SendRawEmail`**), so MIME attachments work → the **invoice-PDF email
  attachment is ON** (`EMAIL_INVOICE_PDF_ENABLED=1`, verified 2026-07-18). Admin → Setup → **Company & billing
  details** (incl. bank details) is filled, so issued invoices show EFT instructions.
- 🟡 **One key away (add when you want them):**
  - **Klaviyo email** → `KLAVIYO_API_KEY` *(future — not started, per you)*
  - **Coach photo uploads** → `S3_BUCKET` + `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`
  - **`.ics` email attachment** → optional; the SES key **now has `ses:SendRawEmail`** (via `AmazonSESFullAccess`),
    so it can be turned on the same way as the invoice PDF — set `EMAIL_ICS_ENABLED=1` (still `0` by default;
    add-to-calendar already works in-app).
- 🗑️ **Removed (dead flags, never read by code):** `YOCO_ENABLED`, `TRACKING_ENABLED`,
  `CONSENT_ENABLED`, `CRM_SYNC_ENABLED`, plus the `BRIDGE_TENFIFTY5_*` trio (`_ADMIN_EMAIL` /
  `_CLIENT_KEY` / `_URL`, left over from the deprecated Ten-Fifty5 bridge) — tracking/consent are
  always-on; CRM self-gates on the Klaviyo key; Yoco is gated by `PAYMENTS_ENABLED`. All were dropped
  from the live services on the 2026-07-05 Frankfurt recreate; **don't re-add them.**
- 🌍 **Region:** both web services (`courtflow-api`, `courtflow-web`) and the Postgres DB (`courtflow-db`)
  now run in Render's **Frankfurt** region, co-located (fixed 2026-07-05 — the web services had been in
  Oregon), on the **Starter** plan; `render.yaml` pins `region`/`plan` + declares `SES_REGION=eu-north-1`
  and `SEED_NEXTPOINT=1`.

---

## 🌐 Domains, DNS & registrar (WIX IS GONE — 2026-08-24)

Both brands left Wix entirely on 2026-08-23/24. Wix now holds **nothing**: not the
registration, not the DNS, and no record anywhere points at `185.230.63.x`.

| | Registrar | DNS | Expires |
|---|---|---|---|
| `nextpointtennis.com` | **Porkbun** | **Cloudflare** (`fattouche` + `miki.ns.cloudflare.com`) | 2027-09-23 |
| `ten-fifty5.com` | **Porkbun** | **Cloudflare** (same pair) | 2028-10-21 |

- **Cloudflare account: `tomo.stojakovic@gmail.com`** — deliberately NOT an address on
  either hosted domain. The account controlling MX has to stay recoverable when MX is
  broken.
- **Every record is DNS-only (grey cloud).** Render terminates its own TLS, and
  **proxying Clerk's records breaks Clerk.** Cloudflare will keep suggesting you
  proxy; keep declining.
- **Registrar and DNS are split on purpose.** If the Cloudflare account is ever lost,
  the domains can be repointed from Porkbun in minutes. Moving registration to
  Cloudflare would save ~$0.60/yr and recreate the single point of failure.
- **Auto-renew is ON at Porkbun** for both. `nextpointtennis.com` came within 32 days
  of expiry during this move; that cannot recur.
- **DNSSEC is OFF** at both the registry and Porkbun. It was on at Wix, and a DS record
  pointing at a departed provider's keys is the one failure that takes a domain fully
  dark (SERVFAIL, web and mail, up to 48h). Re-enable only via Cloudflare, which
  signs the zone and hands you a DS to paste at Porkbun.
- **Zone source of truth: `migration/dns/<domain>.zone`** — BIND files Cloudflare
  imports directly. Verify any change with `python -m scripts.verify_dns <domain>`
  (records + DNSSEC + registrar/delegation; run it locally, it needs no server).
- **`api.nextpointtennis.com` → `sport-ai-api-call.onrender.com`** is a NextPoint-branded
  host serving the **Ten-Fifty5** ingester. It looks like a stale duplicate of
  `courtflow-api` and is not. Never delete it.
- **`ten-fifty5.com`'s three SES DKIM keys sign NextPoint's receipts too** — that zone is
  load-bearing for BOTH brands.
- **🔴 KLAVIYO HAS NO AUTHENTICATED SENDING DOMAIN, AND THAT IS A DELIVERABILITY GAP** (found
  2026-08-29). Klaviyo's sending-domains list is EMPTY, so marketing campaigns go out *From*
  `info@nextpointtennis.com` but are signed on Klaviyo's SHARED infrastructure. This domain's SPF is
  `v=spf1 include:_spf.google.com ~all` — Google only, no Klaviyo — so **DMARC alignment fails on every
  campaign**. Nothing is blocked (`p=none`), but Gmail/Outlook weight unaligned mail toward Promotions
  and spam. **A 97% "delivered" rate is NOT a 97% inbox rate** — delivered only means the receiving
  server accepted it, and this is the leading unexcluded explanation for the Spring Day sequence's
  ~1% click rate against a ~60% (Apple-MPP-inflated) open rate. **Fix = Klaviyo → Settings → Domains →
  add `nextpointtennis.com`, then add its 3-4 CNAMEs in Cloudflare (grey cloud) and mirror them into
  `migration/dns/nextpointtennis.com.zone`.** DNS is Tomo's to click, never an agent's.
- **The daily Microsoft/Google DMARC report emails are WORKING AS DESIGNED, not a fault** — they began
  at the Cloudflare cutover because that is when we published our own `_dmarc` TXT (it replaced the
  Wix-hosted CNAME). `rua=mailto:info@nextpointtennis.com` is the request for them. Keep them until the
  Klaviyo domain above is authenticated — they are the evidence it worked.

---

## 🚀 GO-LIVE env changes (historical — done at the 2026-07-05 cutover; kept as the as-run record)
Everything above is already set on the **dev/onrender** config. At cutover, change **exactly these**:
- **`courtflow-web`**
  - `CLERK_PUBLISHABLE_KEY` → the **prod** `pk_live_…` (prod Clerk instance for `nextpointtennis.com`)
  - `GA4_MEASUREMENT_ID` → `G-…` · `GOOGLE_ADS_ID` → `AW-17077631191` (already done tonight; live on onrender)
- **`courtflow-api`**
  - `AUTH_JWKS_URL` → `https://clerk.nextpointtennis.com/.well-known/jwks.json`
  - `AUTH_ISSUER` → `https://clerk.nextpointtennis.com`
  - `APP_BASE_URL` → `https://nextpointtennis.com`
- ⚠️ The prod Clerk token **must emit the `email` claim** (it links imported members) — configured 2026-07-05.
- **`SES_*` stays as-is** (interim via Ten-Fifty5). Post-AWS-unlock: verify `nextpointtennis.com` in SES for
  DKIM-aligned deliverability, then repoint `SES_SENDER` → `no-reply@nextpointtennis.com`.

---

## `courtflow-api` (the API service — has the DB)

### Critical — the app needs these (already set 🟢)
| Var | Status | What it does | Format / example |
|---|---|---|---|
| `DATABASE_URL` | 🟢 | Postgres connection (the whole app) — now the DB's **internal** Frankfurt URL (same-region private network, co-located with the api) | `postgresql://user:pass@host/db` |
| `AUTH_ENABLED` | 🟢 | Turns on Clerk JWT verification | `1` |
| `AUTH_JWKS_URL` | 🟢 | Clerk JWKS for verifying tokens | `https://settling-alien-23.clerk.accounts.dev/.well-known/jwks.json` |
| `AUTH_ISSUER` | 🟢 | Expected token issuer | `https://settling-alien-23.clerk.accounts.dev` |
| `AUTH_AUDIENCE` | 🟢 | Leave **blank** (Clerk default tokens set no `aud`) | *(empty)* |
| `OPS_KEY` | 🟢 | Server-to-server / cron / admin guard | any long secret |

### Payments (Yoco) — already set 🟢
| Var | Status | What it does | Format |
|---|---|---|---|
| `PAYMENTS_ENABLED` | 🟢 | Global online-payments switch | `1` |
| `PAYMENTS_PROVIDER` | 🟢 | Which gateway `/api/billing/config` advertises | `yoco` |
| `YOCO_SECRET_KEY` | 🟢 | Server-side checkout + refund calls | `sk_live_…` |
| `YOCO_PUBLIC_KEY` | 🟢 | Publishable key surfaced to the browser | `pk_live_…` |
| `YOCO_WEBHOOK_SECRET` | 🟢 | Verifies Yoco webhook signatures | `whsec_…` |
| `APP_BASE_URL` | 🟢 | Origin for Yoco return URLs (the web host) | `https://courtflow-web.onrender.com` |

### Transactional email (SES) — LIVE 🟢 (interim via the Ten-Fifty5 AWS account)
| Var | Status | What it does | Value |
|---|---|---|---|
| `SES_SENDER` | 🟢 | Verified From address (per-club From-name + Reply-To layered on) | `noreply@ten-fifty5.com` |
| `SES_AWS_ACCESS_KEY_ID` | 🟢 | **Dedicated** SES credential (separate from the S3 `AWS_*` pair) | access key id |
| `SES_AWS_SECRET_ACCESS_KEY` | 🟢 | Dedicated SES secret | secret key |
| `SES_REGION` | 🟢 | SES region — **pinned `eu-north-1` in `render.yaml`** (was blank; blank fell through to `AWS_REGION=af-south-1` and would break email). Must match the verified SES identity | `eu-north-1` |
| `FEEDBACK_SECRET` | 🟢 | Signs the `/feedback` link token (the token IS the authorization and names the recipient + club) | set |
| `FEEDBACK_BASE_URL` | 🟢 | Base URL used when minting feedback links | prod host |
| `GOOGLE_REVIEW_URL` | 🟢 | The `g.page` link a HAPPY NPS score is routed to | set |
| `PUBLIC_APP_URL` | 🟢 | Public base URL used in emails/links | prod host |
| `ANALYTICS_INGEST_HOST` | 🟢 | Where `marketing-digest.yml` POSTs the GA4/GSC metrics (`/api/cron/analytics-ingest`) | api host |
| `MARKETING_DIGEST_API` | 🟢 | API base the digest emails through | api host |
| `SIGNUP_TRIAL_DAYS` | 🟢 | Length of the signup trial (default 7) | `7` |
| `KLAVIYO_MARKETING_LIST` / `KLAVIYO_REACTIVATION_LIST` | ⚪ | Klaviyo list ids. `KLAVIYO_API_KEY` is now set, but audiences are driven by **segments defined in Klaviyo**, not these vars — verify in Render before assuming either is populated | unset/optional |
| `HUBSPOT_*` | 🔴 | Legacy/unused CRM vars read defensively; not part of the live path | unset |
| `CLUB_FROM_NAME` / `CLUB_REPLY_TO` | 🟢 | Club identity threaded into every transactional email | set |
| `TRANSACTIONAL_BCC` | 🟡 | Optional BCC on transactional email (coach BCC is separate, per own lesson/class) | optional |
| `EMAIL_INVOICE_PDF_ENABLED` | 🟢 | Attach the invoice **PDF** to the `invoice_issued` email (MIME `SendRawEmail`) — **`1`, ON** (verified 2026-07-18; the SES key carries `AmazonSESFullAccess` = `ses:*`, which includes `ses:SendRawEmail`) | `1` |
| `EMAIL_ICS_ENABLED` | 🟡 | Attach the booking `.ics` to emails — **`0` by default** (optional). The SES key **now has** `ses:SendRawEmail`, so flip to `1` anytime to enable it; add-to-calendar already works in-app | `0` |

### Optional integrations — dark until you add the key 🟡
| Var | Status | Lights up | Format |
|---|---|---|---|
| `KLAVIYO_API_KEY` | 🟢 **LIVE** (set 2026-08) | Klaviyo marketing sync (self-gates: no key = silent no-op). Account `TckWKM`, sender `info@nextpointtennis.com` | Klaviyo private key |
| `GOOGLE_ADS_FEED_USER` | 🟢 | HTTP Basic user for `GET /feeds/google-ads/offline-conversions.csv` (Google Ads scheduled upload). Feed is **404/dark until BOTH set** | any string you invent |
| `GOOGLE_ADS_FEED_PASS` | 🟢 | HTTP Basic pass for the offline-conversion feed (paired with the above). `sync:false` | long random string |
| `GOOGLE_ADS_FEED_WINDOW_DAYS` | ⚪ | Rolling days of rows the feed serves (Google accepts clicks < 90d + de-dupes) | `90` |
| `S3_BUCKET` | 🟡 | Coach photo uploads (S3 presign) | bucket name |
| `AWS_ACCESS_KEY_ID` | 🟡 | AWS credential for S3 | access key id |
| `AWS_SECRET_ACCESS_KEY` | 🟡 | AWS credential for S3 | secret key |
| `AWS_REGION` | ⚪ | AWS region for S3 (defaults to `af-south-1`) | `af-south-1` |

### Boot / housekeeping ⚪
| Var | Status | What it does | Default |
|---|---|---|---|
| `SEED_NEXTPOINT` | 🟢 | Re-seed NextPoint (club #1) on boot — idempotent. **Now declared in `render.yaml`** (was dashboard-only) | `1` |
| `PYTHON_VERSION` | 🟢 | Build-time Python | `3.12.3` |
| `AUTH_PROVIDER` ⚪ · `AUTH_JWT_LEEWAY` ⚪ | skip | label / clock-skew | `clerk` / `30` |
| `AWS_PROFILE` · `AWS_ROLE_ARN` · `AWS_WEB_IDENTITY_TOKEN_FILE` · `AWS_DEFAULT_REGION` ⚪ | skip | alt AWS auth (only if not using access keys) | — |
| `S3_PUBLIC_BASE_URL` · `SES_FROM` · `SES_FROM_EMAIL` · `BOOKINGS_FROM_EMAIL` ⚪ | skip | alt sender fallbacks (use `SES_SENDER`) | — |
| `PAYPAL_CLIENT_ID` ⚪ | skip | dormant (PayPal not built) | — |
| `CRON_API_BASE` ⚪ | only if you enable the paid cron services | the API host | `https://courtflow-api.onrender.com` |

---

## `courtflow-web` (the marketing + portal service — no DB) — already set 🟢
| Var | Status | What it does | Value |
|---|---|---|---|
| `AUTH_ENABLED` | 🟢 | Enables the `/login` Clerk widget | `1` |
| `CLERK_PUBLISHABLE_KEY` | 🟢 | Clerk browser key (public by design) | `pk_test_…` / `pk_live_…` |
| `AUTH_API_BASE` | 🟢 | API host the portal calls | `https://courtflow-api.onrender.com` |
| `AUTH_AFTER_LOGIN_URL` | 🟢 | Redirect after sign-in | `/portal` |
| `MARKETING_HOSTS` | 🟢 | Hosts that serve the public site at `/` | `courtflow-web.onrender.com,nextpointtennis.com,www.nextpointtennis.com` |
| `PYTHON_VERSION` | 🟢 | Build-time Python | `3.12.3` |

### Ten-Fifty5 members-area embed (match analysis SSO) — LIVE, private test 🟢
A member opens Ten-Fifty5 inside the portal, signed in with their own NextPoint Clerk token. **This is NOT
the removed `BRIDGE_TENFIFTY5_*` cross-business bridge** — it's a live member-area SSO embed. Full write-up:
root `CLAUDE.md` → "Ten-Fifty5 embed".
| Var | Status | What it does | Value |
|---|---|---|---|
| `TF5_EMBED_URL` | 🟢 | The embed iframe `src` (Ten-Fifty5 portal). Empty → the members-area entry hides | `https://www.ten-fifty5.com/portal?embed=1` |
| `TF5_EMBED_ORIGINS` | 🟢 | Origin(s) the portal will relay a Clerk token to (`auth_client.js` `serveChild`) | `https://www.ten-fifty5.com` |
| `TF5_EMBED_ALLOW_EMAILS` | 🟢 | **Private-test allowlist** — only these emails get the embed; everyone else sees a "Coming soon" card. **EMPTY = all members (launch).** | `tomos@nedbank.co.za` |

> **⚠️ The other half of this feature lives in the Ten-Fifty5 repo (`C:\dev\webhook-server`), on Render
> services whose names DON'T match `render.yaml`:** the live 1050 **API** is the service **"Sport AI - API
> call"** (custom domain `api.nextpointtennis.com`) — set **`AUTH_ISSUERS=https://clerk.ten-fifty5.com,https://clerk.nextpointtennis.com`**
> there (leave `AUTH_JWKS_URLS` unset; use the *plural* `AUTH_ISSUERS`, not `AUTH_ISSUER`). The **`locker-room`**
> service (serves the portal) needs **`TF_TRUSTED_PARENT_ORIGINS=https://nextpointtennis.com,https://www.nextpointtennis.com`**.
> The service literally named `webhook-server` is a **cron**, not the API. Neither repo's `render.yaml`
> auto-syncs env — set it in each dashboard by hand.

### Google marketing tags (injected by `web_app._inject_head`; all env-gated, dark until set)
| Var | Status | What it does | Value |
|---|---|---|---|
| `GA4_MEASUREMENT_ID` | 🟢 | GA4 pageview/analytics tag | `G-…` (set 2026-07-05) |
| `GOOGLE_ADS_ID` | 🟢 | Google Ads global tag (remarketing/pageviews) | `AW-17077631191` |
| `GOOGLE_ADS_CONVERSIONS` | 🟢 | JSON event→Ads `send_to`; `cfConversion('start_free_week')` on sign-up CTAs + `cfConversion('booking')` on booking success. Labels from the Ads console | `{"start_free_week":"AW-17077631191/rEy7CNKNsc4cENfxn88_","booking":"AW-17077631191/tu5JCNWNsc4cENfxn88_"}` (LIVE 2026-07-11) |
| `GSC_VERIFICATION_FILE` | ⚪ | Search Console HTML-file verify (served at `/<file>`) — GSC already verified via existing property | `google….html` |
| `GSC_META_TOKEN` | ⚪ | Alt Search Console meta-tag verify | token |

---

## Copy-paste checklist (everything you might set, grouped)
Tick what you have; leave the rest blank — every blank one degrades gracefully.

```
# courtflow-api — CRITICAL (already set)
DATABASE_URL=...
OPS_KEY=...
AUTH_ENABLED=1
AUTH_JWKS_URL=https://settling-alien-23.clerk.accounts.dev/.well-known/jwks.json
AUTH_ISSUER=https://settling-alien-23.clerk.accounts.dev
AUTH_AUDIENCE=
# courtflow-api — PAYMENTS (already set)
PAYMENTS_ENABLED=1
PAYMENTS_PROVIDER=yoco
YOCO_SECRET_KEY=sk_live_...
YOCO_PUBLIC_KEY=pk_live_...
YOCO_WEBHOOK_SECRET=whsec_...
APP_BASE_URL=https://courtflow-web.onrender.com
SEED_NEXTPOINT=1
# courtflow-api — TRANSACTIONAL EMAIL (LIVE, interim via Ten-Fifty5 AWS)
SES_SENDER=noreply@ten-fifty5.com
SES_AWS_ACCESS_KEY_ID=...      # dedicated SES creds (separate from S3's AWS_*)
SES_AWS_SECRET_ACCESS_KEY=...
SES_REGION=eu-north-1
EMAIL_INVOICE_PDF_ENABLED=1    # ON — SES key has ses:SendRawEmail (AmazonSESFullAccess); invoice PDF attached
EMAIL_ICS_ENABLED=0           # optional — key already has ses:SendRawEmail; flip to 1 to attach the .ics
# courtflow-api — OPTIONAL (add when you want the feature)
KLAVIYO_API_KEY=               # future: Klaviyo email
GOOGLE_ADS_FEED_USER=          # HTTP Basic user for the Google Ads offline-conversion CSV feed (set)
GOOGLE_ADS_FEED_PASS=          # HTTP Basic pass (feed 404/dark until BOTH set)
S3_BUCKET=                     # coach photo uploads
AWS_ACCESS_KEY_ID=             # S3
AWS_SECRET_ACCESS_KEY=
AWS_REGION=af-south-1

# courtflow-web (already set) — swap CLERK key to pk_live at cutover
AUTH_ENABLED=1
CLERK_PUBLISHABLE_KEY=pk_test_c2V0dGxpbmctYWxpZW4tMjMuY2xlcmsuYWNjb3VudHMuZGV2JA   # -> pk_live_… at cutover
AUTH_API_BASE=https://courtflow-api.onrender.com
AUTH_AFTER_LOGIN_URL=/portal
MARKETING_HOSTS=courtflow-web.onrender.com,nextpointtennis.com,www.nextpointtennis.com
# courtflow-web — TEN-FIFTY5 EMBED (members-area match analysis; live, private test)
TF5_EMBED_URL=https://www.ten-fifty5.com/portal?embed=1
TF5_EMBED_ORIGINS=https://www.ten-fifty5.com
TF5_EMBED_ALLOW_EMAILS=tomos@nedbank.co.za   # empty = all members (launch)
# courtflow-web — GOOGLE TAGS (GA4+Ads live; conversions/GSC optional)
GA4_MEASUREMENT_ID=G-...
GOOGLE_ADS_ID=AW-17077631191
GOOGLE_ADS_CONVERSIONS={"start_free_week":"AW-17077631191/rEy7CNKNsc4cENfxn88_","booking":"AW-17077631191/tu5JCNWNsc4cENfxn88_"}
GSC_VERIFICATION_FILE=             # optional (GSC already verified via the nextpointtennis.com domain property; GA4↔GSC linked 2026-07-11)
GSC_META_TOKEN=
```

**Do NOT set** (dead — removed from render.yaml and dropped from the live services on the Frankfurt
recreate): `YOCO_ENABLED`, `TRACKING_ENABLED`, `CONSENT_ENABLED`, `CRM_SYNC_ENABLED`,
`BRIDGE_TENFIFTY5_ADMIN_EMAIL`, `BRIDGE_TENFIFTY5_CLIENT_KEY`, `BRIDGE_TENFIFTY5_URL`.
> The dead `BRIDGE_TENFIFTY5_*` trio was the old **cross-business analytics bridge** — unrelated to the LIVE
> **`TF5_EMBED_*`** members-area SSO embed above. Different feature; don't conflate them.

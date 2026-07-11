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
> - Still dark (keys not entered): **Klaviyo** (marketing email), **S3** (coach photo uploads).

**What this is:** the single source of truth for environment variables. `render.yaml` does **not**
auto-push to Render — you type env into the Render dashboard manually — so this sheet (derived from a
full `os.getenv` scan of the code) is the list to work from.

**Live-audit (2026-06-21):** all code is deployed on both services — every API route answers `401`
(exists, auth-gated), every public page `200`. Nothing is "stuck in yaml". As of 2026-07,
**transactional SES email is LIVE** (interim, via the Ten-Fifty5 AWS account); the only things still
dark are the optional integrations whose **keys aren't entered yet** (Klaviyo, S3).

Legend: 🟢 set & working · 🟡 optional, dark until you add the key · ⚪ has a safe default, usually skip.

---

## TL;DR — what's live vs one key away
- 🟢 **Live now (env already set):** the whole app — login, booking, classes, the three purchasing
  models, **Yoco payments + refunds + receipts**, the **Business Overview dashboard + page beacon**, and
  **transactional email** (invites + booking/statement confirmations) via the interim Ten-Fifty5 SES.
- 🟡 **One key away (add when you want them):**
  - **Klaviyo email** → `KLAVIYO_API_KEY` *(future — not started, per you)*
  - **Coach photo uploads** → `S3_BUCKET` + `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`
  - **`.ics` email attachment** → set `EMAIL_ICS_ENABLED=1` once the SES IAM key gains `ses:SendRawEmail`
    (currently `0` — add-to-calendar still works in-app)
  - **Month-end sweep** → add an **`OPS_KEY` GitHub repository secret** (Settings → Secrets and variables →
    Actions), the **same value** as `courtflow-api`'s `OPS_KEY` env var, so `.github/workflows/month-end.yml`
    can call the OPS-guarded `POST /api/cron/month-end`. Until it's set the workflow **safely no-ops** (a
    missing/wrong key is just rejected — no accrual, no client notifications).
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

## 🚀 GO-LIVE env changes (make these AT the DNS cutover — see GO-LIVE-STEPS.md)
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
| `EMAIL_ICS_ENABLED` | 🟡 | Attach the booking `.ics` to emails — **`0` for now** (interim key lacks `ses:SendRawEmail`); flip to `1` when it does | `0` |

### Optional integrations — dark until you add the key 🟡
| Var | Status | Lights up | Format |
|---|---|---|---|
| `KLAVIYO_API_KEY` | 🟡 *(future)* | Klaviyo email sync (self-gates: no key = silent no-op) | Klaviyo private key |
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
EMAIL_ICS_ENABLED=0            # flip to 1 when the key gains ses:SendRawEmail
# courtflow-api — OPTIONAL (add when you want the feature)
KLAVIYO_API_KEY=               # future: Klaviyo email
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
GOOGLE_ADS_CONVERSIONS=            # {"purchase":"AW-.../label","sign_up":"AW-.../label"}
GSC_VERIFICATION_FILE=             # optional (GSC already verified)
GSC_META_TOKEN=
```

**Do NOT set** (dead — removed from render.yaml and dropped from the live services on the Frankfurt
recreate): `YOCO_ENABLED`, `TRACKING_ENABLED`, `CONSENT_ENABLED`, `CRM_SYNC_ENABLED`,
`BRIDGE_TENFIFTY5_ADMIN_EMAIL`, `BRIDGE_TENFIFTY5_CLIENT_KEY`, `BRIDGE_TENFIFTY5_URL`.
> The dead `BRIDGE_TENFIFTY5_*` trio was the old **cross-business analytics bridge** — unrelated to the LIVE
> **`TF5_EMBED_*`** members-area SSO embed above. Different feature; don't conflate them.

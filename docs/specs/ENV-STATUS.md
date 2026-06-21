# ENV-STATUS — every environment variable, what it lights up, and what's set

**What this is:** the single source of truth for environment variables. `render.yaml` does **not**
auto-push to Render — you type env into the Render dashboard manually — so this sheet (derived from a
full `os.getenv` scan of the code) is the list to work from.

**Live-audit (2026-06-21):** all code is deployed on both services — every API route answers `401`
(exists, auth-gated), every public page `200`. Nothing is "stuck in yaml". The only things dark are
optional integrations whose **keys aren't entered yet** (Klaviyo, S3, SES, the Ten-Fifty5 bridge).

Legend: 🟢 set & working · 🟡 optional, dark until you add the key · ⚪ has a safe default, usually skip.

---

## TL;DR — what's live vs one key away
- 🟢 **Live now (env already set):** the whole app — login, booking, classes, the three purchasing
  models, **Yoco payments + refunds + receipts**, the **Business Overview dashboard + page beacon**.
- 🟡 **One key away (add when you want them):**
  - **Klaviyo email** → `KLAVIYO_API_KEY` *(future — not started, per you)*
  - **Coach photo uploads** → `S3_BUCKET` + `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`
  - **Transactional email fallback** → `SES_SENDER` (+ the AWS creds above)
  - **Ten-Fifty5 column on the dashboard** → `BRIDGE_TENFIFTY5_URL` + `_CLIENT_KEY` + `_ADMIN_EMAIL`
- 🗑️ **Removed (dead flags, never read by code):** `YOCO_ENABLED`, `TRACKING_ENABLED`,
  `CONSENT_ENABLED`, `CRM_SYNC_ENABLED` — tracking/consent are always-on; CRM self-gates on the
  Klaviyo key; Yoco is gated by `PAYMENTS_ENABLED`. Don't set these.

---

## `courtflow-api` (the API service — has the DB)

### Critical — the app needs these (already set 🟢)
| Var | Status | What it does | Format / example |
|---|---|---|---|
| `DATABASE_URL` | 🟢 | Postgres connection (the whole app) | `postgresql://user:pass@host/db` |
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

### Optional integrations — dark until you add the key 🟡
| Var | Status | Lights up | Format |
|---|---|---|---|
| `KLAVIYO_API_KEY` | 🟡 *(future)* | Klaviyo email sync (self-gates: no key = silent no-op) | Klaviyo private key |
| `S3_BUCKET` | 🟡 | Coach photo uploads (S3 presign) | bucket name |
| `AWS_ACCESS_KEY_ID` | 🟡 | AWS credential for S3 + SES | access key id |
| `AWS_SECRET_ACCESS_KEY` | 🟡 | AWS credential for S3 + SES | secret key |
| `AWS_REGION` | ⚪ | AWS region (defaults to `af-south-1`) | `af-south-1` |
| `SES_SENDER` | 🟡 | Transactional email fallback sender | `bookings@nextpointtennis.com` |

### Ten-Fifty5 bridge — set all three to show the "Ten-Fifty5" dashboard column 🟡
| Var | Status | What it does | Format |
|---|---|---|---|
| `BRIDGE_TENFIFTY5_URL` | 🟡 | Ten-Fifty5's API host (connection address) | `https://<ten-fifty5-api-host>` |
| `BRIDGE_TENFIFTY5_CLIENT_KEY` | 🟡 | Ten-Fifty5's `CLIENT_API_KEY` | secret |
| `BRIDGE_TENFIFTY5_ADMIN_EMAIL` | 🟡 | An admin email on Ten-Fifty5 | `info@ten-fifty5.com` |

### Boot / housekeeping ⚪
| Var | Status | What it does | Default |
|---|---|---|---|
| `SEED_NEXTPOINT` | 🟢 | Re-seed NextPoint (club #1) on boot — idempotent | `1` |
| `PYTHON_VERSION` | 🟢 | Build-time Python | `3.12.3` |
| `AUTH_PROVIDER` ⚪ · `AUTH_JWT_LEEWAY` ⚪ | skip | label / clock-skew | `clerk` / `30` |
| `AWS_PROFILE` · `AWS_ROLE_ARN` · `AWS_WEB_IDENTITY_TOKEN_FILE` · `AWS_DEFAULT_REGION` ⚪ | skip | alt AWS auth (only if not using access keys) | — |
| `S3_PUBLIC_BASE_URL` · `SES_FROM` · `BOOKINGS_FROM_EMAIL` · `SES_REGION` ⚪ | skip | extra fallbacks | — |
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
# courtflow-api — OPTIONAL (add when you want the feature)
KLAVIYO_API_KEY=               # future: Klaviyo email
S3_BUCKET=                     # coach photo uploads
AWS_ACCESS_KEY_ID=             # S3 + SES
AWS_SECRET_ACCESS_KEY=
AWS_REGION=af-south-1
SES_SENDER=                    # email fallback
BRIDGE_TENFIFTY5_URL=          # Ten-Fifty5 dashboard column
BRIDGE_TENFIFTY5_CLIENT_KEY=
BRIDGE_TENFIFTY5_ADMIN_EMAIL=info@ten-fifty5.com

# courtflow-web (already set)
AUTH_ENABLED=1
CLERK_PUBLISHABLE_KEY=pk_test_c2V0dGxpbmctYWxpZW4tMjMuY2xlcmsuYWNjb3VudHMuZGV2JA
AUTH_API_BASE=https://courtflow-api.onrender.com
AUTH_AFTER_LOGIN_URL=/portal
MARKETING_HOSTS=courtflow-web.onrender.com,nextpointtennis.com,www.nextpointtennis.com
```

**Do NOT set** (dead — removed from render.yaml): `YOCO_ENABLED`, `TRACKING_ENABLED`,
`CONSENT_ENABLED`, `CRM_SYNC_ENABLED`.

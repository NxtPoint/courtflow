# GO-LIVE — cutover steps (✅ EXECUTED 2026-07-05 — historical as-run record + rollback reference)

> **ALL STEPS BELOW WERE COMPLETED 2026-07-05.** Live at `https://nextpointtennis.com` (apex A→216.24.57.1,
> www CNAME→courtflow-web.onrender.com; `api.` untouched). Prod Clerk + Google login, GA4/Ads/GSC live,
> `TRANSACTIONAL_BCC` set. Retained as the as-run log + the rollback recipe (§ROLLBACK) until the Wix
> rollback window closes (~2 weeks), then archivable.

## (original click-by-click, now executed)

Split into **TONIGHT** (safe prep, no user impact) and **TOMORROW** (the cutover, DNS-sensitive, in order).
⚠️ **NEVER touch the `api.nextpointtennis.com` DNS record** at any point — it's the live 1050 API.
Data migration is DONE (878 clients / 11 memberships / 8 lessons in prod). Email is LIVE.

================================================================
## ✅ DO TONIGHT (all safe — nothing changes for current Wix visitors)
================================================================

### 1. Lower the DNS TTL  (⚠️ apex + www ONLY — the single most important prep)
In whoever hosts `nextpointtennis.com` DNS (registrar / Wix DNS / Cloudflare):
- Set **TTL = 300 seconds** on the **apex (`@`)** record and the **`www`** record.
- Do NOT touch `api.nextpointtennis.com`. Don't change WHERE they point yet — only the TTL.
- Why: tomorrow's flip (and any rollback) then propagates in minutes, not hours.

### 2. Bump `courtflow-web` Free → Starter (no cold starts)
Render → `courtflow-web` → **Settings → Instance Type → Starter**. (`courtflow-api` is already Starter.)

### 3. Create the PRODUCTION Clerk instance  (⚠️ email claim is critical)
Clerk dashboard → create a **Production** application/instance for `nextpointtennis.com`:
- Add the domain `nextpointtennis.com`. Clerk gives you **DNS records** (CNAMEs like `clerk`, `accounts`,
  `clkmail`, `clk._domainkey`…). Add them to `nextpointtennis.com` DNS — these are **safe subdomains**,
  they do NOT touch `api.` or the apex.
- **CRITICAL:** configure the session token / JWT template to include the **`email`** claim. Our whole
  migration links imported members by email on first login — no email claim = members can't reach their
  records. (The current dev Clerk emits it; the prod one must too.)
- Collect these (you'll paste them at cutover, NOT now): **Publishable key** (`pk_live_…`), **Frontend API
  URL** (the issuer), **JWKS URL** (`https://clerk.nextpointtennis.com/.well-known/jwks.json`).
- ❗Do NOT swap Render env to prod Clerk tonight — a prod Clerk is domain-locked and would break login on
  the onrender URL. Swap it TOMORROW after DNS points to the real domain.

### 4. Google Analytics 4 — create + wire (safe; starts collecting on the onrender site)
- analytics.google.com → Admin → Create **Property** "NextPoint Tennis" → **Web** data stream for
  `nextpointtennis.com` → copy the **Measurement ID** `G-XXXXXXXXXX`.
- Render → `courtflow-web` → Environment → set **`GA4_MEASUREMENT_ID`** = `G-XXXXXXXXXX` → save (redeploys).

### 5. Google Ads conversions — only if you run Ads (else skip)
- Google Ads → Tools → **Conversions** → create: **sign-up**, **purchase** (booking/membership). Copy the
  **conversion ID** `AW-XXXXXXXXX` and each conversion's **label**.
- Render → `courtflow-web` env: **`GOOGLE_ADS_ID`** = `AW-XXXXXXXXX`, and **`GOOGLE_ADS_CONVERSIONS`** =
  `{"purchase":"AW-XXXXXXXXX/theLabel","sign_up":"AW-XXXXXXXXX/theLabel"}` (valid JSON).

### 6. Google Search Console — add property + verify by DNS TXT (works before cutover)
- search.google.com/search-console → add property `nextpointtennis.com` (Domain property).
- Choose **DNS TXT** verification → add the TXT record to `nextpointtennis.com` DNS (safe — a TXT record,
  never touches apex/`api.`). Verify. (Sitemap submission happens tomorrow, once the site is live.)
- (Alternative: set `GSC_META_TOKEN` in Render env and verify by meta tag AFTER cutover.)

### 7. Three app fixes (in the admin console — do now)
- Fix **Allon's "10 × 60 · R8500"** package → change to **10 × 90** (that's Robert's real pack).
- Create the **"Monthly Adult - Squad"** class (Mpilonhle's plan; excluded from the membership import).
- **Colbert** to accept his coach invite (or Resend invite) so he can log in.

### 8. Draft the client email (send tomorrow, after cutover verified)
"We've upgraded NextPoint — sign in at nextpointtennis.com with your email (set a new password / magic
link). Your bookings, membership and lessons are already there." ~900 recipients.

================================================================
## 🚀 TOMORROW — cutover sequence (in this order; reversible at every step)
================================================================

1. **Smoke test on `courtflow-web.onrender.com`** — home loads, `/login` works, a test member signs in and
   lands on their record, a test booking, a 301 (`curl -sI .../coaching-team` → `/coaches`).
2. **Attach the custom domain** in Render → `courtflow-web` → Settings → **Custom Domains** → add
   `nextpointtennis.com` + `www.nextpointtennis.com`. Render shows the DNS target(s) to use in step 5.
3. **Swap Render env to prod Clerk** (BOTH services where present):
   - `courtflow-api`: `AUTH_JWKS_URL`, `AUTH_ISSUER` → the prod Clerk values.
   - `courtflow-web`: `CLERK_PUBLISHABLE_KEY` → `pk_live_…`.
   - `courtflow-api`: `APP_BASE_URL` → `https://nextpointtennis.com`; `AUTH_AFTER_LOGIN_URL` fine as `/portal`.
   - (`MARKETING_HOSTS` already includes the real domains. Save → services redeploy.)
4. **Confirm email still sends** on the real domain (a quick `--list-plans`-style booking test) — SES is
   host-independent so it should be unaffected.
5. **Flip DNS** (⚠️ apex + `www` ONLY). **Do NOT touch `api.nextpointtennis.com`.**
   DNS is hosted **at Wix** (nameservers `ns8/ns9.wixdns.net`) and the domain is **connected to the Wix
   site**, so the apex A records (`185.230.63.x`) + `www` CNAME (`cdn1.wixdns.net`) are **Wix-managed and
   likely LOCKED**. To repoint them:
   - In **Wix → Domains**, you'll probably need to **disconnect the domain from the Wix site** (or use
     "point to an external site" / edit-DNS mode) so the apex/www records become editable.
   - Then set them to Render's targets from step 2: **`www` CNAME → `courtflow-web.onrender.com`**, and the
     **apex** via Render's provided **A record / ALIAS** (Render doesn't do apex CNAME — it gives an A/ALIAS,
     or you redirect apex→www; follow Render's custom-domain screen).
   - ⚠️ **Wix gotcha (from the 1050 cutover): do NOT click "Try Again" in Wix Domains** — it can reset things.
   - Leave every OTHER record alone: `api.` (1050 API), the Clerk CNAMEs (`clerk/accounts/clkmail/clk*`),
     the email records (`_dmarc`, `_domainkey`, MX, SPF), and the Clerk/GSC TXT.
6. **Move the Wix site to its free `*.wixsite.com` URL** — do NOT delete it (rollback path).
7. **GSC:** submit `https://nextpointtennis.com/sitemap.xml`; **Request Indexing** on the top 20-30 pages.
8. **Send the client email** (step 8 above).
9. **Monitor:** GA4 Realtime, Render logs, that a real client signs in and links by email, that 301s resolve.

## ⏪ ROLLBACK (any step)
Point the apex + `www` DNS back to Wix. Fast, thanks to the 300s TTL. Nothing else to undo.

## Who does what
- **Tomo (all of the above):** DNS, Clerk console, Google consoles, Render dashboard, the client email.
- **Agent (already done / on standby):** all code + the 3 imports; can curate the final `redirects.csv`
  from a real Wix crawl if you want it before cutover (optional — 17 starter rules already work).

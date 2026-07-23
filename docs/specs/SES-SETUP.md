# SES transactional email — setup + the multi-club model

Status: **LIVE (2026-07-03) — running on an INTERIM setup; this doc is the long-term proper setup.**
Email sends today via the **Ten-Fifty5 AWS account** (`SES_REGION=eu-north-1`,
`SES_SENDER=noreply@ten-fifty5.com`, dedicated `SES_AWS_*` creds) — invites + booking/statement
confirmations go out club-branded. **Two follow-ups:** (1) the `.ics` attachment is gated OFF
(`EMAIL_ICS_ENABLED=0`) **by choice** - the key already carries `ses:SendRawEmail`; (2) move to the **proper
CourtFlow-domain** setup below (verify `courtflow.app` / `nextpointtennis.com` in the CourtFlow AWS
account once it's unlocked). The transactional path self-gates on creds (no keys → in-app only, never
errors); switching accounts/domain is env, not code.

## The model — one verified domain, per-club identity (scales to many clubs)
- **Verify ONE domain in SES: `courtflow.app`** (DKIM + SPF). Done **once, ever** — never per club.
- Every club sends **from that one verified identity** but with its **own display name + Reply-To**:
  `From: "NextPoint Tennis" <no-reply@courtflow.app>`, `Reply-To: info@nextpointtennis.com`.
  A verified *domain* identity lets any local-part + display name through, so a member sees mail "from
  their club" while it all flows through one CourtFlow sender.
- **Adding a future club = zero SES work.** The From-name comes from `club.club.name` and the Reply-To
  from the club's first `club.location.email` (both already in the DB, resolved in
  `marketing_crm/notifications.py::_club_identity`). Just set the club's contact email in
  **Settings → Club profile**.

## INTERIM (go-live) — reuse the ten-fifty5 SES account NOW, no CourtFlow AWS needed
CourtFlow's own AWS is locked (reset pending). But **the code can send NextPoint email TODAY through
the already-verified, out-of-sandbox ten-fifty5 SES account** — branded "NextPoint Tennis" via the
per-club From-name. SES now takes its OWN creds (`SES_AWS_*`), so it can live in a different AWS
account from S3; nothing else changes. Set on `courtflow-api`:
The credentials already exist in the **1050 Render service env** — no AWS console needed (Render is a
separate login). Copy from the ten-fifty5 service into `courtflow-api` (confirmed against 1050's actual
`support_bot/email_sender.py` + `coach_invite/email_sender.py`):
| 1050 Render value | → courtflow-api var |
|---|---|
| `AWS_ACCESS_KEY_ID` | `SES_AWS_ACCESS_KEY_ID` |
| `AWS_SECRET_ACCESS_KEY` | `SES_AWS_SECRET_ACCESS_KEY` |
| `AWS_REGION` (**the real value — likely `eu-north-1`**, NOT the us-east-1 code default) | `SES_REGION` |
| `SES_FROM_EMAIL` (= `noreply@ten-fifty5.com`) | `SES_SENDER` (or just copy `SES_FROM_EMAIL` as-is — code reads it) |

- Members see the display name **"NextPoint Tennis"** (per-club From-name); replies route to
  `info@nextpointtennis.com` (per-club Reply-To — set it in **Settings → Club profile**). Only the raw
  address stays on ten-fifty5.com. (Cleaner-but-optional later: verify `nextpointtennis.com` in that SES
  account via Easy DKIM — 3 safe CNAMEs, never touches the apex/`api.` — and send from `no-reply@nextpointtennis.com`.)
- **Sandbox:** almost certainly NOT an issue — 1050 sends **coach invites to external emails** through this
  same SES in prod, which a sandboxed account can't do. Confirm anyway with `python -m scripts.test_ses --to <gmail>`.

**Verify before trusting it:** `python -m scripts.test_ses --to you@example.com` (with the env pasted in)
reports the config and, on a real send, translates any failure (sandbox / missing `ses:SendEmail` / wrong
region). Post-reset, do the proper CourtFlow setup below and just repoint `SES_SENDER` + drop the `SES_AWS_*`.

## What Tomo does in AWS (one-time — the PROPER CourtFlow setup, post-reset)
1. **SES region = `af-south-1`** (Cape Town — matches `AWS_REGION` in `render.yaml`). Do everything below
   in that region (SES identities are region-scoped).
2. **Verify the domain `courtflow.app`:** SES → Verified identities → Create identity → Domain →
   `courtflow.app` → enable **Easy DKIM**. Add the 3 DKIM CNAMEs + an SPF TXT (`v=spf1 include:amazonses.com -all`)
   to the courtflow.app DNS. Wait for "Verified".
   - (Optional but better deliverability: a **custom MAIL FROM** subdomain, e.g. `mail.courtflow.app`.)
3. **Request production access** (SES → Account dashboard → "Request production access"). Until granted,
   SES is in the **sandbox** and can only send TO verified addresses — fine for testing, not for members.
4. **IAM user** with `ses:SendEmail` + `ses:SendRawEmail`; put its keys in Render as `AWS_ACCESS_KEY_ID`
   / `AWS_SECRET_ACCESS_KEY` (already `sync:false` in `render.yaml`).
5. **Set `SES_SENDER`** in Render to a bare address on the verified domain, e.g. `no-reply@courtflow.app`.
6. **Set each club's contact email** (NextPoint: `info@nextpointtennis.com`) in Settings → Club profile
   so replies route to the club (Reply-To). Optional — with none, mail still sends, just no Reply-To.

That's it — `ses.enabled()` flips true and every mapped event starts emailing.

## What the code does (built)
- `marketing_crm/email/ses.py`
  - `enabled()` — true only with a sender + AWS creds; everything is a silent no-op otherwise.
  - `send_email(to, subject, text, html?, from_name?, reply_to?)` — structured SES send; `from_name`
    → `"Name <SES_SENDER>"`, `reply_to` → `ReplyToAddresses`.
  - `send_raw_email(..., attachments?)` — MIME `SendRawEmail` for **attachments** (the booking **.ics**
    calendar invite — the piece 1050 never had); falls back to `send_email` when there's nothing to attach.
  - `html_wrap(title, body_html, footer?)` — a light brand-consistent HTML shell (cf-* palette).
  - `send_booking_confirmation(payload)` — club-branded, attaches the `.ics`.
- `marketing_crm/notifications.py::deliver` resolves the club identity and every mapped event (bookings,
  lesson lifecycle, payments, statement-ready, packs, refunds…) emails HTML + text, from the club's name,
  Reply-To the club, with a `.ics` attached for booking-type events (`_ICS_KINDS`). Klaviyo still owns any
  richer marketing flows; this is the guaranteed transactional layer.

## Verifying (no AWS needed)
`SES_SENDER=no-reply@courtflow.app AWS_ACCESS_KEY_ID=x AWS_SECRET_ACCESS_KEY=y python -c "…"` proves
`enabled()`, the `From` display-name format, the `.ics` builder, and `_club_identity` (done 2026-07-02).
With real keys, send yourself a test booking confirmation from a verified address while still in the sandbox.

## Ops levers + diagnosing deliverability (added 2026-07-05) — OPS_KEY-guarded, API-only
No AWS console needed (the CourtFlow AWS account is locked). Three probes on `courtflow-api`:
- `POST /api/cron/ses-selftest?to=<email>` — sends a test + reports the raw result (`enabled`, `send_ok`, `sender`, `region`, any error).
- `POST /api/cron/ses-account` — account health: `enforcement_status` (want `HEALTHY`), `sending_enabled`,
  `production_access` (sandbox?), send quota, and `recent_stats` (bounce/complaint/reject counts).
- `POST /api/cron/ses-suppress?email=<addr>&action=check|delete` — check / **clear** an address on the
  account suppression list.
Call with header `X-Ops-Key: <OPS_KEY>` (rotate OPS_KEY after launch).

### ⚠️ The suppression trap (what bit us on 2026-07-04)
SES **accepts** a send (`send_ok:true`) but delivers nothing when the recipient is on the **account
suppression list** — and this account auto-suppresses on `BOUNCE`/`COMPLAINT`. Worse: **sending to a
suppressed address logs another bounce**, so re-testing the same dead address looks like a total failure.
- Real fresh addresses deliver fine (verified: 4/4 real inboxes, 0 bounces). It only trips on
  addresses that bounced once (a bad/corporate/mistyped address, or one you marked spam).
- **Fix a stuck client:** `.../ses-suppress?email=CLIENT&action=delete`.
- **Note:** this SES account is **shared with Ten-Fifty5** — only clear addresses you know are yours;
  don't wipe the whole list (it holds legit 1050 bounces).

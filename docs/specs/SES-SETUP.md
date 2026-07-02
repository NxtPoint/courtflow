# SES transactional email — setup + the multi-club model

Status: **CODE COMPLETE (2026-07-02) — dark until Tomo verifies the domain + sets the keys.** The whole
transactional path is built and self-gates on creds (no keys → in-app notifications only, never errors).
Turning it on is AWS config, not code.

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

## What Tomo does in AWS (one-time)
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

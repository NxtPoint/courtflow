# marketing_crm/email/ses.py — SES fallback for the single most critical transactional send.
#
# Klaviyo flows own normal booking confirmations. THIS is the guaranteed fallback: if a Klaviyo send
# fails (outage, throttle, mis-config), a booking confirmation must NEVER be lost (docs/06 §1,§4).
#
# SELF-GATES on AWS creds + a sender (SES_SENDER / SES_FROM). No creds → silent no-op (False), never
# raises. Region from AWS_REGION / SES_REGION (default eu-west-1). Sends a minimal, non-PII plain-text
# confirmation built from the booking payload (the same non-PII fields the event carries).

import logging
import os

log = logging.getLogger("marketing_crm.email.ses")


def _sender():
    return os.getenv("SES_SENDER") or os.getenv("SES_FROM") or os.getenv("BOOKINGS_FROM_EMAIL")


def _region():
    return os.getenv("SES_REGION") or os.getenv("AWS_REGION") or "eu-west-1"


def _ses_creds():
    """Explicit SES-only credentials (SES_AWS_ACCESS_KEY_ID / SES_AWS_SECRET_ACCESS_KEY), so the
    transactional sender can live in a DIFFERENT AWS account from S3. This is the go-live interim:
    NextPoint email rides the already-verified, out-of-sandbox ten-fifty5 SES account now, while
    CourtFlow's own AWS (S3 photos, future courtflow.app SES) is set up later — the two never
    entangle. When unset, boto3 falls back to the default chain (AWS_* / profile / role), i.e. the
    prior behaviour. Returns kwargs for boto3.client (empty dict = default chain)."""
    kid = os.getenv("SES_AWS_ACCESS_KEY_ID")
    sec = os.getenv("SES_AWS_SECRET_ACCESS_KEY")
    if kid and sec:
        return {"aws_access_key_id": kid, "aws_secret_access_key": sec}
    return {}


def _has_aws_creds():
    # boto3 also resolves an instance/role profile; treat explicit keys OR a configured profile as
    # "credentials present". We still let boto3 be the final arbiter (a send failure is caught).
    return bool(
        (os.getenv("SES_AWS_ACCESS_KEY_ID") and os.getenv("SES_AWS_SECRET_ACCESS_KEY"))
        or (os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"))
        or os.getenv("AWS_PROFILE")
        or os.getenv("AWS_ROLE_ARN")
    )


def enabled():
    return bool(_sender() and _has_aws_creds())


def _from_source(from_name=None):
    """The RFC 5322 From header. MULTI-TENANT: the verified SES identity (SES_SENDER) is ONE
    CourtFlow address (e.g. no-reply@courtflow.app); each club rides it with its OWN display name,
    so a member sees mail 'from' their club. A verified DOMAIN identity lets any local-part +
    display name through — so adding a club needs NO new SES verification, just a name + reply-to."""
    addr = _sender()
    if not addr:
        return None
    if from_name:
        clean = str(from_name).replace('"', "").replace("\n", " ").replace("\r", " ").strip()
        if clean:
            return '"%s" <%s>' % (clean, addr)
    return addr


def _esc(s):
    return (str(s if s is not None else "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def html_wrap(title, body_html, footer=None):
    """A light, brand-consistent HTML shell for a transactional email (the app's cf-* palette).
    `body_html` is trusted markup the caller built; `title`/`footer` are escaped."""
    return (
        '<div style="font-family:Inter,Segoe UI,Arial,sans-serif;max-width:560px;margin:0 auto;color:#10231A">'
        '<div style="background:#0E7A47;color:#fff;padding:16px 20px;border-radius:14px 14px 0 0;'
        'font-weight:800;font-size:18px">' + _esc(title) + '</div>'
        '<div style="border:1px solid #E2E9E5;border-top:0;border-radius:0 0 14px 14px;padding:20px 22px;'
        'background:#fff;font-size:15px;line-height:1.5">' + (body_html or "") +
        ('<p style="color:#5F7268;font-size:12px;margin:18px 0 0">' + _esc(footer) + "</p>" if footer else "") +
        "</div></div>")


def send_email(to_email, subject, body_text, body_html=None, from_name=None, reply_to=None):
    """Low-level SES send (structured Subject/Text/Html). No-op (False) unless enabled(). Never raises.
    `from_name` = the club's display name; `reply_to` = the club's contact (member replies reach them)."""
    if not enabled() or not to_email:
        return False
    src = _from_source(from_name)
    if not src:
        return False
    try:
        import boto3
        client = boto3.client("ses", region_name=_region(), **_ses_creds())
        body = {"Text": {"Data": body_text, "Charset": "UTF-8"}}
        if body_html:
            body["Html"] = {"Data": body_html, "Charset": "UTF-8"}
        kw = dict(
            Source=src,
            Destination={"ToAddresses": [to_email]},
            Message={"Subject": {"Data": subject, "Charset": "UTF-8"}, "Body": body},
        )
        if reply_to:
            kw["ReplyToAddresses"] = [reply_to]
        client.send_email(**kw)
        return True
    except Exception:
        log.exception("ses: send_email failed for %s", to_email)
        return False


def send_raw_email(to_email, subject, body_text, body_html=None, attachments=None,
                   from_name=None, reply_to=None):
    """MIME send (SES SendRawEmail) — like send_email but supports file ATTACHMENTS, e.g. a booking's
    .ics calendar invite. `attachments` = [{"filename", "content" (str|bytes), "mimetype"}]. With no
    attachments it falls back to send_email. No-op (False) unless enabled(). Never raises."""
    if not attachments:
        return send_email(to_email, subject, body_text, body_html=body_html,
                          from_name=from_name, reply_to=reply_to)
    if not enabled() or not to_email:
        return False
    src = _from_source(from_name)
    if not src:
        return False
    try:
        import boto3
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.base import MIMEBase
        from email import encoders

        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = src
        msg["To"] = to_email
        if reply_to:
            msg["Reply-To"] = reply_to
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(body_text or "", "plain", "utf-8"))
        if body_html:
            alt.attach(MIMEText(body_html, "html", "utf-8"))
        msg.attach(alt)
        for a in attachments:
            content = a.get("content")
            if content is None:
                continue
            if isinstance(content, str):
                content = content.encode("utf-8")
            maintype, _, subtype = (a.get("mimetype") or "application/octet-stream").partition("/")
            part = MIMEBase(maintype or "application", subtype or "octet-stream")
            part.set_payload(content)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", 'attachment; filename="%s"' % (a.get("filename") or "attachment"))
            msg.attach(part)
        client = boto3.client("ses", region_name=_region(), **_ses_creds())
        client.send_raw_email(Source=src, Destinations=[to_email], RawMessage={"Data": msg.as_string()})
        return True
    except Exception:
        log.exception("ses: send_raw_email failed for %s", to_email)
        return False


def send_booking_confirmation(payload):
    """Fallback booking-confirmation send from a booking_confirmed payload (contracts/events.md).
    Non-PII only. Returns True on send, False on no-op/failure. Never raises.

    Call this from a producer ONLY when the Klaviyo send for booking_confirmed failed, e.g.:
        ok = klaviyo_send(...)
        if not ok:
            from marketing_crm.email.ses import send_booking_confirmation
            send_booking_confirmation(payload)
    """
    payload = payload or {}
    to_email = (payload.get("email") or "").strip()
    if not to_email or not enabled():
        return False
    resource = payload.get("resource_name") or "your court"
    starts_at = payload.get("starts_at") or ""
    coach = payload.get("coach_name")
    subject = f"You're booked: {resource}" + (f" at {starts_at}" if starts_at else "")
    lines = [
        "Your booking is confirmed.",
        "",
        f"What:  {resource}",
    ]
    if coach:
        lines.append(f"Coach: {coach}")
    if starts_at:
        lines.append(f"When:  {starts_at}")
    if payload.get("ends_at"):
        lines.append(f"Until: {payload['ends_at']}")
    if payload.get("amount_minor") is not None:
        cur = payload.get("currency_code") or "ZAR"
        lines.append(f"Price: {cur} {payload['amount_minor'] / 100:.2f}")
    if payload.get("settlement_mode"):
        lines.append(f"Settlement: {payload['settlement_mode']}")
    if payload.get("cancel_url"):
        lines.append("")
        lines.append(f"Cancel / reschedule: {payload['cancel_url']}")
    club = payload.get("club_name") or payload.get("from_name")
    lines += ["", "See you on court.", (club or "Your club")]
    # Attach the booking's calendar invite (.ics) when we have the times — the piece 1050 never did.
    attachments = _ics_attachment(payload)
    return send_raw_email(to_email, subject, "\n".join(lines),
                          attachments=attachments,
                          from_name=club, reply_to=payload.get("reply_to"))


def _ics_attachment(payload):
    """Build a [.ics] attachment list from a booking_confirmed-style payload (needs starts_at+ends_at).
    Returns [] when the times are absent or the builder is unavailable — never raises."""
    try:
        if not payload.get("starts_at") or not payload.get("ends_at"):
            return []
        from diary.calendar import build_ics
        summary = payload.get("resource_name") or "Court booking"
        if payload.get("coach_name"):
            summary += " with " + payload["coach_name"]
        ics = build_ics(
            uid=str(payload.get("ref_id") or payload.get("booking_id") or payload.get("id") or "booking")
                + "@courtflow",
            summary=summary, starts_at=payload["starts_at"], ends_at=payload["ends_at"],
            location=payload.get("location") or "", url=payload.get("cancel_url") or payload.get("ics_url") or "",
        )
        return [{"filename": "booking.ics", "content": ics, "mimetype": "text/calendar"}]
    except Exception:
        log.debug("ses: could not build .ics attachment", exc_info=False)
        return []

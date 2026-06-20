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


def _has_aws_creds():
    # boto3 also resolves an instance/role profile; treat explicit keys OR a configured profile as
    # "credentials present". We still let boto3 be the final arbiter (a send failure is caught).
    return bool(
        (os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"))
        or os.getenv("AWS_PROFILE")
        or os.getenv("AWS_ROLE_ARN")
    )


def enabled():
    return bool(_sender() and _has_aws_creds())


def send_email(to_email, subject, body_text, body_html=None):
    """Low-level SES send. No-op (False) unless enabled(). Never raises."""
    if not enabled() or not to_email:
        return False
    try:
        import boto3
        client = boto3.client("ses", region_name=_region())
        body = {"Text": {"Data": body_text, "Charset": "UTF-8"}}
        if body_html:
            body["Html"] = {"Data": body_html, "Charset": "UTF-8"}
        client.send_email(
            Source=_sender(),
            Destination={"ToAddresses": [to_email]},
            Message={"Subject": {"Data": subject, "Charset": "UTF-8"}, "Body": body},
        )
        return True
    except Exception:
        log.exception("ses: send_email failed for %s", to_email)
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
    lines += ["", "See you on court.", "NextPoint Tennis"]
    return send_email(to_email, subject, "\n".join(lines))

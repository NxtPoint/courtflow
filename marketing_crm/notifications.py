# marketing_crm/notifications.py — the notifications / communications engine.
#
# Turns the events we ALREADY emit (marketing_crm.tracking.emit → core.usage_event) into
#   (a) an in-app inbox  (core.notification — works NOW, no keys), and
#   (b) transactional email (SES/Klaviyo — dark until keys, degrades gracefully).
#
# DESIGN — driven off the existing event stream. emit() calls deliver_for_event() for any
# event in KIND_MAP, AFTER the usage_event write, all wrapped in try/except so a notification
# failure NEVER touches the booking/payment that triggered it (best-effort + non-fatal).
#
# For each mapped kind we:
#   1. resolve the recipient (iam.user; child→guardian) from the payload's user_id/email,
#   2. render a template (title/body/link) from the non-PII payload,
#   3. ALWAYS insert a core.notification row (the in-app inbox),
#   4. attempt a best-effort transactional email (SES today; Klaviyo flows own the rich send)
#      and record the outcome in email_status ('skipped' with no keys).
#
# NOTHING here raises. With no email keys the inbox still works fully (email_status='skipped').

import logging
import os

from marketing_crm.email import booking_detail

log = logging.getLogger("marketing_crm.notifications")


# ---------------------------------------------------------------------------
# Small money/format helpers (non-PII; ZAR cents → "R123.45").
# ---------------------------------------------------------------------------

def _money(minor, currency=None):
    try:
        n = int(minor)
    except (TypeError, ValueError):
        return None
    sym = {"ZAR": "R", "USD": "$", "GBP": "£", "EUR": "€"}.get(currency or "ZAR", "")
    return f"{sym}{n / 100:.2f}"


def _when(ctx):
    """Intentionally None. The intro SENTENCE must not print a time: these templates are pure (no DB /
    timezone), so any time here is the raw UTC ISO from the payload — which reads as the wrong time
    (SAST is +2h) and looks broken. The correctly-formatted, club-timezone "When" is rendered in the
    booking-detail block instead (booking_detail.fmt_when). Kept as a function so callers are unchanged."""
    return None


def _g(ctx, *keys, default=None):
    for k in keys:
        v = ctx.get(k)
        if v is not None and v != "":
            return v
    return default


# ---------------------------------------------------------------------------
# Templates — each returns (title, body, link). `ctx` is the full emit() payload
# (reserved keys + non-PII metadata). Templates must be defensive: any field can be absent.
# ---------------------------------------------------------------------------

def _booking_noun(ctx, default="your booking"):
    """A booking-type-aware noun for the intro sentence (a lesson isn't 'your court')."""
    res = _g(ctx, "resource_name")
    if res:
        return res
    return {"lesson": "your lesson", "class": "your class",
            "court": "your court"}.get(ctx.get("booking_type"), default)


def _t_booking_confirmed(ctx):
    body = f"Your booking for {_booking_noun(ctx)} is confirmed."
    bid = _g(ctx, "ref_id", "booking_id")
    return ("Booking confirmed", body, "/portal" if bid else None)


def _t_payment_succeeded(ctx):
    amt = _money(_g(ctx, "amount_minor"), _g(ctx, "currency_code", "currency"))
    what = ctx.get("for")
    body = "We received your payment" + (f" of {amt}" if amt else "") + \
           (f" for {what}" if what else "") + ". Your receipt is ready."
    order_id = _g(ctx, "ref_id", "order_id")
    link = f"/receipt.html?order={order_id}" if order_id else None
    return ("Payment received", body, link)


def _t_membership_activated(ctx):
    plan = _g(ctx, "plan_name", default="Membership")
    body = (f"Your {plan} is active. Your court bookings are now covered by your membership."
            if plan != "Membership"
            else "Your membership is active. Your court bookings are now covered.")
    return ("Membership active", body, "/portal")


def _t_bundle_activated(ctx):
    label = _g(ctx, "plan_label", "label", default="session pack")
    n = ctx.get("tokens_total") or ctx.get("sessions_count")
    body = f"Your {label} is ready."
    if n:
        body += f" {n} sessions added to your wallet."
    return ("Pack activated", body, "/portal")


def _t_refund_requested(ctx):
    amt = _money(_g(ctx, "amount_minor"), _g(ctx, "currency_code", "currency"))
    body = ("We've received your refund request" + (f" for {amt}" if amt else "") +
            ". We'll review it and be in touch.")
    return ("Refund requested", body, "/portal")


def _t_refund_decided(ctx):
    decision = (ctx.get("decision") or ctx.get("status") or "").strip().lower()
    amt = _money(_g(ctx, "amount_minor"), _g(ctx, "currency_code", "currency"))
    if decision in ("approved", "approve", "refunded"):
        body = ("Good news — your refund request was approved" +
                (f" ({amt})" if amt else "") + ". The refund is being processed.")
        title = "Refund approved"
    elif decision in ("declined", "decline", "rejected"):
        body = "Your refund request was declined."
        note = ctx.get("note") or ctx.get("reason")
        if note:
            body += f" Reason: {note}."
        title = "Refund declined"
    else:
        body = "There's an update on your refund request."
        title = "Refund update"
    return (title, body, "/portal")


def _t_class_enrolled(ctx):
    cls = _g(ctx, "class_name", default="your class")
    when = _when(ctx)
    body = f"You're enrolled in {cls}" + (f" on {when}" if when else "") + "."
    return ("Class enrolment confirmed", body, "/portal")


def _t_class_waitlisted(ctx):
    cls = _g(ctx, "class_name", default="the class")
    pos = ctx.get("position")
    body = f"You're on the waitlist for {cls}" + (f" (position {pos})" if pos else "") + \
           ". We'll let you know if a spot opens."
    return ("You're on the waitlist", body, "/portal")


def _t_class_promoted(ctx):
    cls = _g(ctx, "class_name", "resource_name", default="your class")
    when = _when(ctx)
    body = f"A spot opened — you're now enrolled in {cls}" + (f" on {when}" if when else "") + "."
    return ("A spot opened — you're in!", body, "/portal")


def _t_coach_invited(ctx):
    body = ("You've been invited to coach. Sign in to set up your profile, hours and "
            "services to start taking bookings.")
    return ("You've been invited to coach", body, "/coach-onboarding.html")


def _t_statement_ready(ctx):
    amt = _money(_g(ctx, "amount_minor"), _g(ctx, "currency_code", "currency"))
    if amt:
        body = (f"You have {amt} outstanding on your account. Pay securely online from your "
                "dashboard — tap to go straight in.")
    else:
        body = ("Your statement for this month is ready. Pay securely online from your "
                "dashboard — tap to go straight in.")
    return ("Your statement is ready", body, "/portal")


def _t_invoice_issued(ctx):
    num = _g(ctx, "invoice_number")
    amt = _money(_g(ctx, "amount_minor"), _g(ctx, "currency_code", "currency"))
    lead = f"Invoice {num}" if num else "Your invoice"
    body = (f"{lead}" + (f" for {amt}" if amt else "") + " is ready. A summary is below and the "
            "full invoice is attached. Pay securely online, or by EFT using the banking details "
            "on the invoice.")
    return ("Your invoice is ready", body, "/portal")


# Lesson approval lifecycle (recipient is set on the emit payload's user_id by diary.bookings).
def _t_lesson_requested(ctx):
    return ("New lesson request", "A client has requested a lesson with you — accept, propose a new "
            "time, or decline.", "/coach")


def _t_lesson_proposed(ctx):
    return ("Your coach proposed a time", "Your coach suggested a time for your lesson — accept or "
            "decline it under “Needs your attention”.", "/portal")


def _t_lesson_accepted(ctx):
    return ("Lesson confirmed", "Your lesson is confirmed — see it in My Bookings.", "/portal")


def _t_lesson_declined(ctx):
    return ("Lesson request declined", "Your coach couldn't take that lesson. Try another time or "
            "coach.", "/portal")


# Booking/money lifecycle — cancellations, edits, refunds, reminders (client-facing, launch-critical).
def _t_booking_cancelled(ctx):
    res = _g(ctx, "resource_name", default="your booking")
    when = _when(ctx)
    body = f"Your booking for {res}" + (f" on {when}" if when else "") + " has been cancelled."
    fee = _money(_g(ctx, "fee_minor"), _g(ctx, "currency_code", "currency"))
    if fee and _g(ctx, "fee_applied"):
        body += f" A cancellation fee of {fee} applies."
    return ("Booking cancelled", body, "/portal")


def _t_booking_rescheduled(ctx):
    res = _g(ctx, "resource_name", default="your booking")
    when = _when(ctx)
    body = f"Your booking for {res} has been moved" + (f" to {when}" if when else "") + \
           ". If this doesn't suit you, you can reschedule or cancel from My Bookings."
    return ("Booking updated", body, "/portal")


def _t_payment_refunded(ctx):
    amt = _money(_g(ctx, "amount_minor"), _g(ctx, "currency_code", "currency"))
    body = ("We've refunded" + (f" {amt}" if amt else " your payment") +
            " to your card. It can take a few business days to appear on your statement.")
    order_id = _g(ctx, "ref_id", "order_id")
    link = f"/receipt.html?order={order_id}" if order_id else "/portal"
    return ("Refund issued", body, link)


def _t_class_cancelled(ctx):
    cls = _g(ctx, "class_name", "resource_name", default="your class")
    when = _when(ctx)
    body = f"{cls}" + (f" on {when}" if when else "") + \
           " has been cancelled. Any payment for it will be refunded or credited."
    return ("Class cancelled", body, "/portal")


def _t_booking_reminder(ctx):
    res = _g(ctx, "resource_name", "class_name", default="your booking")
    when = _when(ctx)
    body = f"Reminder: {res}" + (f" is coming up on {when}" if when else " is coming up") + \
           ". See you on court!"
    coach = ctx.get("coach_name")
    if coach:
        body += f" Coach: {coach}."
    return ("Booking reminder", body, "/portal")


# ---------------------------------------------------------------------------
# KIND_MAP — which usage_event kinds become notifications + their template.
#
# The producer event NAME → (template_fn). We map the canonical kinds the platform already
# emits PLUS the two "promote"/"decided" kinds we want covered:
#   - class_promoted        : waitlist_slot_open already fires on auto-promotion (classes.py
#                             _promote_waitlist emits waitlist_slot_open then class_enrolled).
#                             We treat waitlist_slot_open as the "a spot opened" promotion.
#   - membership_activated  : membership_started (lifecycle activated) + the manual admin grant.
#   - bundle activated      : bundle_activated (NEW emit we add at the pack-activation site).
#   - refund_decided        : NEW emit we add where an admin executes/decides a refund.
# ---------------------------------------------------------------------------

KIND_MAP = {
    "booking_confirmed":     _t_booking_confirmed,
    "payment_succeeded":     _t_payment_succeeded,
    "membership_started":    _t_membership_activated,   # lifecycle: activated
    "membership_activated":  _t_membership_activated,   # explicit alias (admin manual grant)
    "bundle_activated":      _t_bundle_activated,
    "refund_requested":      _t_refund_requested,
    "refund_decided":        _t_refund_decided,
    "class_enrolled":        _t_class_enrolled,
    "class_waitlisted":      _t_class_waitlisted,
    # NOTE: waitlist_slot_open is intentionally NOT mapped to an email. On auto-promotion the diary
    # emits waitlist_slot_open AND class_enrolled back-to-back for the same seat; mapping both sent the
    # promoted player TWO emails. class_enrolled is the single confirmation. The waitlist_slot_open
    # EVENT still fires (kept for CRM/Klaviyo triggers) — it just no longer sends a transactional email.
    "coach_invited":         _t_coach_invited,
    "statement_ready":       _t_statement_ready,         # month-end: balance reminder → pay online
    "invoice_issued":        _t_invoice_issued,          # issued invoice DOCUMENT (summary + PDF + pay-online)
    "lesson_requested":      _t_lesson_requested,        # lesson approval lifecycle (→ coach)
    "lesson_proposed":       _t_lesson_proposed,         # (→ client)
    "lesson_accepted":       _t_lesson_accepted,         # (→ requester)
    "lesson_declined":       _t_lesson_declined,         # (→ requester)
    # Booking/money lifecycle — these WERE emitted but silent (no map entry = no email/inbox).
    "booking_cancelled":     _t_booking_cancelled,       # court/lesson cancel (→ booker)
    "booking_rescheduled":   _t_booking_rescheduled,     # court/lesson moved (→ booker)
    "payment_refunded":      _t_payment_refunded,         # money-back-to-card (→ payer)
    "class_cancelled":       _t_class_cancelled,          # session cancelled (→ each enrolled, fanned out)
    "booking_reminder":      _t_booking_reminder,         # T-24h / T-2h (→ booker)
}


# ---------------------------------------------------------------------------
# delivery
# ---------------------------------------------------------------------------

def deliver(session, *, club_id, user_id, kind, ctx, email=None):
    """Render the template for `kind`, INSERT a core.notification (in-app — ALWAYS), and
    attempt a best-effort transactional email. Resolves the recipient (iam.user; child→
    guardian). Returns the notification id or None. NEVER raises — every failure is logged.

    `user_id`/`email` are the producer's contact hints (iam UUID + adult email). The actual
    inbox owner is resolved via iam.resolve_notification_recipient (so a dependent's booking
    lands in the guardian's inbox)."""
    tmpl = KIND_MAP.get(kind)
    if tmpl is None:
        return None

    from core.repositories import notifications as notif_repo
    from iam import repositories as iam_repo

    # 1) recipient (child→guardian). Need a club_id + an iam user to own the inbox.
    if not club_id:
        log.debug("notification skipped (no club_id) kind=%s", kind)
        return None
    recipient = iam_repo.resolve_notification_recipient(session, user_id=user_id, email=email)
    if recipient is None:
        log.debug("notification skipped (no resolvable recipient) kind=%s", kind)
        return None

    # 2) render template (defensive — never let a template error block the booking path).
    try:
        title, body, link = tmpl(ctx or {})
    except Exception:
        log.exception("notification template failed kind=%s", kind)
        title, body, link = (kind.replace("_", " ").title(), None, None)

    # 2b) Rich detail block (client · service · date+time in club TZ · court · coach · price + payment),
    # looked up by booking_id/class_session_id/order_id so the lean, non-PII emit payload is untouched.
    # Loaded NOW (before the in-app row) so payment_succeeded can be the SINGLE confirm+receipt email:
    # an online booking's payment email shows the SAME rich block as a confirmation, and is SUPPRESSED
    # for pack/class orders (their own "Pack activated" / "enrolment confirmed" email is the one email)
    # so one online purchase never sends two emails. Guarded → None → the plain body is used. For a
    # lesson/class the detail also yields the coach's email, which we BCC so the coach gets a copy.
    detail = None
    if kind in booking_detail.DETAIL_KINDS or kind in _PURCHASE_KINDS:
        try:
            detail = booking_detail.load(session, club_id, ctx or {})
        except Exception:
            detail = None

    # Invoice DOCUMENT block (statement summary + Pay-online box) + the professional PDF
    # attachment (flag-gated on EMAIL_INVOICE_PDF_ENABLED). Loaded here so the invoice email is
    # the single confirm+PDF send. Guarded → None → the plain body is used.
    invoice_doc = None
    if kind == "invoice_issued":
        try:
            from marketing_crm.email import invoice_detail
            invoice_doc = invoice_detail.load(session, club_id, ctx or {})
        except Exception:
            invoice_doc = None

    if kind == "payment_succeeded" and detail:
        if detail.get("is_purchase"):
            ok = detail.get("order_kind")
            if ok == "pack":
                return None                    # the pack's "Pack activated" email is the one email
            if ok == "membership":
                title = "Membership confirmed"
                body = "Your membership is active and paid — the details are below."
        elif detail.get("booking_type") == "class":
            return None                        # the class's "enrolment confirmed" email is the one
        else:
            title = "Booking confirmed"        # court/lesson: this payment email IS the confirmation
            body = "Your booking is confirmed and paid — the details are below."

    # non-PII context for the row's data jsonb (drop control/contact keys).
    _drop = {"email", "user_id", "account_id", "person_id"}
    data = {k: v for k, v in (ctx or {}).items() if k not in _drop}

    # 3) in-app row — ALWAYS (this is the part that works with no keys).
    notif_id = notif_repo.insert_notification(
        session, club_id=club_id, user_id=recipient["user_id"], kind=kind,
        title=title, body=body, link=link, data=(data or None), email_status="skipped",
    )

    # 4) best-effort transactional email. Self-gates on SES creds; no keys → 'skipped'.
    #    MULTI-TENANT: the club's name becomes the email's From display name + signature, and the
    #    club's contact email its Reply-To — all riding one verified CourtFlow SES identity.
    ident = _club_identity(session, club_id)
    bcc = list(filter(None, [ident.get("bcc"), booking_detail.coach_email(detail)]))

    status = _try_email(recipient.get("email"), title, body, recipient.get("name"),
                        from_name=ident.get("from_name"), reply_to=ident.get("reply_to"),
                        bcc=bcc, kind=kind, ctx=ctx, detail=detail, invoice_doc=invoice_doc)
    if status and status != "skipped" and notif_id:
        try:
            notif_repo.set_email_status(session, notification_id=notif_id, email_status=status)
        except Exception:
            log.debug("could not record email_status for notification %s", notif_id)

    return notif_id


# Booking-ish events that carry a start/end time → attach a calendar invite (.ics).
_ICS_KINDS = {"booking_confirmed", "lesson_accepted", "lesson_proposed", "class_enrolled"}

# PURCHASE events (membership / pack / payment receipt) that carry an order_id (or ref_type='order')
# → enrich with the order block: the exact item(s) bought, the amount, and the payment method (paid
# online / pay at court / on monthly account). This is what turns a bare "payment processed" into a
# proper "you bought Adult Anytime Membership — Paid online" confirmation.
_PURCHASE_KINDS = {"payment_succeeded", "membership_started", "membership_activated", "bundle_activated",
                   "payment_refunded"}


def _club_identity(session, club_id):
    """The club's email identity: {from_name, reply_to, bcc} = the club's name + a contact email (its
    first location email). Drives the per-club From display name + Reply-To. The club is BCC'd on every
    transactional email (`bcc` = its contact email) so it keeps an oversight copy of all bookings/edits/
    cancellations/refunds; the global TRANSACTIONAL_BCC env floor (in ses.py) catches the rest.
    Guarded → {} on any failure."""
    try:
        from sqlalchemy import text
        row = session.execute(text(
            "SELECT c.name AS club_name, "
            "(SELECT email FROM club.location WHERE club_id = c.id AND email IS NOT NULL "
            " ORDER BY created_at LIMIT 1) AS reply_to "
            "FROM club.club c WHERE c.id = :c"), {"c": club_id}).mappings().first()
        if not row:
            return {}
        return {"from_name": row["club_name"], "reply_to": row["reply_to"], "bcc": row["reply_to"]}
    except Exception:
        log.debug("club identity lookup failed for %s", club_id, exc_info=False)
        return {}


def _try_email(to_email, title, body, name=None, from_name=None, reply_to=None, bcc=None,
               kind=None, ctx=None, detail=None, invoice_doc=None):
    """Send a transactional email via SES: HTML + plain-text, the club's From-name + Reply-To, and a
    calendar (.ics) attachment for booking-type events. Returns 'sent'|'failed'|'skipped'. With no
    AWS/SES creds → 'skipped' (a clean no-op), so the engine is fully usable with NO keys. NEVER raises.
    Klaviyo flows own the RICH confirmation sends downstream of emit(); this is the guaranteed fallback.

    `detail` (when present) is the rich booking/class block — client · service · date+time (club TZ) ·
    court · coach · price + payment status — rendered under the intro sentence inside the same shell."""
    if not to_email:
        return "skipped"
    try:
        from marketing_crm.email import ses
        if not ses.enabled():
            log.debug("notification email skipped (SES not configured) -> %s", to_email)
            return "skipped"
        sig = from_name or "Your club"
        greeting = ("Hi %s,\n\n" % name) if name else ""
        html_greeting = ("Hi %s,<br><br>" % ses._esc(name)) if name else ""
        intro = body or title
        if invoice_doc:
            # Invoice email: statement summary + Pay-online box in the SAME green shell, PDF attached.
            from marketing_crm.email import invoice_detail
            text_body = "%s%s\n\n%s\n\n%s" % (greeting, intro, invoice_detail.text_block(invoice_doc), sig)
            inner = ("<p style=\"margin:0 0 4px\">%s%s</p>%s"
                     % (html_greeting, ses._esc(intro), invoice_detail.html_block(invoice_doc)))
            html_body = ses.html_wrap(title, inner, footer=sig)
        elif detail:
            text_body = "%s%s\n\n%s\n\n%s" % (greeting, intro, booking_detail.text_block(detail), sig)
            inner = ("<p style=\"margin:0 0 4px\">%s%s</p>%s"
                     % (html_greeting, ses._esc(intro), booking_detail.html_block(detail)))
            html_body = ses.html_wrap(title, inner, footer=sig)
        else:
            text_body = "%s%s\n\n%s" % (greeting, intro, sig)
            html_body = ses.html_wrap(title, "<p>%s%s</p>" % (html_greeting, ses._esc(intro)), footer=sig)
        # Calendar (.ics) attachment is OFF by default: it forces SES SendRawEmail, a SEPARATE IAM
        # permission the interim ten-fifty5 key lacks — which silently dropped confirmations. Plain
        # sends (SendEmail) work. Flip EMAIL_ICS_ENABLED=1 once ses:SendRawEmail is granted (post AWS
        # reset / CourtFlow SES). Even then, send_raw_email falls back to plain send if raw fails.
        attachments = None
        if kind in _ICS_KINDS and ctx and os.getenv("EMAIL_ICS_ENABLED", "0").strip() == "1":
            attachments = ses._ics_attachment(ctx) or None
        # The invoice PDF (flag-gated on EMAIL_INVOICE_PDF_ENABLED → needs ses:SendRawEmail; until
        # then the email links to the in-portal PDF via 'Pay online' and this is a no-op).
        if invoice_doc:
            try:
                from marketing_crm.email import invoice_detail
                inv_att = invoice_detail.pdf_attachment(invoice_doc)
                if inv_att:
                    attachments = (attachments or []) + inv_att
            except Exception:
                pass
        ok = ses.send_raw_email(to_email, title, text_body, body_html=html_body,
                                attachments=attachments, from_name=from_name, reply_to=reply_to,
                                bcc=bcc)
        return "sent" if ok else "failed"
    except Exception:
        log.exception("notification email send failed -> %s", to_email)
        return "failed"


def deliver_for_event(session, event, payload):
    """The single hook emit() calls (inside its own try/except, on the emit() thread) after
    the usage_event write. Maps `event` → a notification when it's in KIND_MAP. Best-effort:
    a failure here is swallowed so the producer's booking/payment path is never affected.

    `payload` is the original emit() payload (carries club_id, email, user_id + the non-PII
    details the template renders from)."""
    if event not in KIND_MAP:
        return None
    try:
        club_id = payload.get("club_id")
        # The contact the producer passed: an iam user UUID (booked_by/payer) + adult email.
        user_id = payload.get("user_id")
        email = (payload.get("email") or "").strip().lower() or None
        return deliver(session, club_id=club_id, user_id=user_id, kind=event,
                       ctx=payload, email=email)
    except Exception:
        log.exception("deliver_for_event failed kind=%s (non-fatal)", event)
        return None

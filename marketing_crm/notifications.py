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
    """A short, non-PII time string if the payload carries one (ISO passthrough)."""
    return ctx.get("starts_at") or ctx.get("when") or None


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

def _t_booking_confirmed(ctx):
    res = _g(ctx, "resource_name", default="your court")
    when = _when(ctx)
    body = f"Your booking for {res}" + (f" on {when}" if when else "") + " is confirmed."
    coach = ctx.get("coach_name")
    if coach:
        body += f" Coach: {coach}."
    bid = _g(ctx, "ref_id", "booking_id")
    return ("Booking confirmed", body, "/my.html" if bid else None)


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
    return ("Membership active", body, "/plan")


def _t_bundle_activated(ctx):
    label = _g(ctx, "plan_label", "label", default="session pack")
    n = ctx.get("tokens_total") or ctx.get("sessions_count")
    body = f"Your {label} is ready."
    if n:
        body += f" {n} sessions added to your wallet."
    return ("Pack activated", body, "/plan")


def _t_refund_requested(ctx):
    amt = _money(_g(ctx, "amount_minor"), _g(ctx, "currency_code", "currency"))
    body = ("We've received your refund request" + (f" for {amt}" if amt else "") +
            ". We'll review it and be in touch.")
    return ("Refund requested", body, "/account.html")


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
    return (title, body, "/account.html")


def _t_class_enrolled(ctx):
    cls = _g(ctx, "class_name", default="your class")
    when = _when(ctx)
    body = f"You're enrolled in {cls}" + (f" on {when}" if when else "") + "."
    return ("Class enrolment confirmed", body, "/my.html")


def _t_class_waitlisted(ctx):
    cls = _g(ctx, "class_name", default="the class")
    pos = ctx.get("position")
    body = f"You're on the waitlist for {cls}" + (f" (position {pos})" if pos else "") + \
           ". We'll let you know if a spot opens."
    return ("You're on the waitlist", body, "/my.html")


def _t_class_promoted(ctx):
    cls = _g(ctx, "class_name", "resource_name", default="your class")
    when = _when(ctx)
    body = f"A spot opened — you're now enrolled in {cls}" + (f" on {when}" if when else "") + "."
    return ("A spot opened — you're in!", body, "/my.html")


def _t_coach_invited(ctx):
    body = ("You've been invited to coach. Sign in to set up your profile, hours and "
            "services to start taking bookings.")
    return ("You've been invited to coach", body, "/coach-onboarding.html")


def _t_statement_ready(ctx):
    amt = _money(_g(ctx, "amount_minor"), _g(ctx, "currency"))
    body = (f"You have {amt} due for coaching this month. Pay securely online from your "
            "dashboard — tap to go straight in.")
    return ("Your invoice is ready", body, "/account.html")


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
    "waitlist_slot_open":    _t_class_promoted,          # a seat freed → "you're in" / claim
    "coach_invited":         _t_coach_invited,
    "statement_ready":       _t_statement_ready,         # month-end: invoice ready → pay online
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

    # non-PII context for the row's data jsonb (drop control/contact keys).
    _drop = {"email", "user_id", "account_id", "person_id"}
    data = {k: v for k, v in (ctx or {}).items() if k not in _drop}

    # 3) in-app row — ALWAYS (this is the part that works with no keys).
    notif_id = notif_repo.insert_notification(
        session, club_id=club_id, user_id=recipient["user_id"], kind=kind,
        title=title, body=body, link=link, data=(data or None), email_status="skipped",
    )

    # 4) best-effort transactional email. Self-gates on SES creds; no keys → 'skipped'.
    status = _try_email(recipient.get("email"), title, body, recipient.get("name"))
    if status and status != "skipped" and notif_id:
        try:
            notif_repo.set_email_status(session, notification_id=notif_id, email_status=status)
        except Exception:
            log.debug("could not record email_status for notification %s", notif_id)

    return notif_id


def _try_email(to_email, title, body, name=None):
    """Send a minimal transactional email via the existing SES path. Returns one of
    'sent'|'failed'|'skipped'. With no AWS/SES creds → 'skipped' (a clean no-op + a log line),
    so the engine is fully usable with NO keys configured. NEVER raises.

    Klaviyo flows already own the RICH confirmation sends downstream of emit(); this is the
    guaranteed transactional fallback (same posture as marketing_crm/email/ses.py)."""
    if not to_email:
        return "skipped"
    try:
        from marketing_crm.email import ses
        if not ses.enabled():
            log.debug("notification email skipped (SES not configured) -> %s", to_email)
            return "skipped"
        greeting = f"Hi {name},\n\n" if name else ""
        text_body = f"{greeting}{body or title}\n\nNextPoint Tennis"
        ok = ses.send_email(to_email, title, text_body)
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

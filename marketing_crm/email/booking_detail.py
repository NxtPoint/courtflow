# marketing_crm/email/booking_detail.py — the rich "booking summary" block for transactional email.
#
# Turns a booking/class event (identified by booking_id or class_session_id on the emit payload)
# into the full, human-readable detail block clubs and clients expect on a confirmation:
#   Client (name · email · cell) · Service · Date & time (in the CLUB's timezone) · Court · Coach ·
#   Duration · Price + Payment status (paid / pay at court / month-end / covered).
#
# It is READ-ONLY and side-effect free (a pure SELECT + the pure _booking_charge read) so it can run
# on the best-effort email path without ever touching the booking/payment that triggered it. Every
# entry point is guarded → returns None on any failure, so the caller cleanly falls back to the
# plain body. The emit payload stays lean/non-PII; this enriches the EMAIL by looking up by id.

import logging
from datetime import timezone, timedelta

log = logging.getLogger("marketing_crm.email.booking_detail")

# Which notification kinds carry a booking/class we can enrich.
DETAIL_KINDS = {
    "booking_confirmed", "booking_cancelled", "booking_rescheduled", "booking_reminder",
    "class_enrolled", "class_waitlisted", "waitlist_slot_open", "class_cancelled",
    "lesson_requested", "lesson_proposed", "lesson_accepted", "lesson_declined",
}


# ---------------------------------------------------------------------------
# formatting helpers (timezone-correct, Windows-safe strftime)
# ---------------------------------------------------------------------------

def _tz(name):
    """The club's tzinfo. Africa/Johannesburg is UTC+2 with NO DST, so a fixed +02:00 is a correct
    fallback when the host has no zoneinfo database (e.g. Windows without tzdata)."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name or "Africa/Johannesburg")
    except Exception:
        return timezone(timedelta(hours=2))


def _as_dt(v):
    if v is None:
        return None
    if hasattr(v, "isoformat") and not isinstance(v, str):
        return v
    try:
        from datetime import datetime
        s = str(v).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _t12(dt):
    """'4:15 PM' — built by hand because strftime('%-I') is not portable to Windows."""
    h = dt.hour % 12 or 12
    return "%d:%02d %s" % (h, dt.minute, "AM" if dt.hour < 12 else "PM")


def _tz_label(dt):
    """'SAST' where the platform provides it, else 'GMT+2' style (matches what clients saw on Wix)."""
    try:
        abbr = dt.strftime("%Z")
        if abbr and not abbr.startswith(("+", "-")) and abbr.upper() != "UTC+02:00":
            return abbr
    except Exception:
        pass
    off = dt.utcoffset() or timedelta(0)
    hours = off.total_seconds() / 3600
    if hours == int(hours):
        return "GMT%+d" % int(hours)
    return "GMT%+.1f" % hours


def fmt_when(starts, ends, tzname):
    """'Saturday, 5 July 2026 · 4:15–5:15 PM (SAST)' in the club timezone. None → None."""
    s = _as_dt(starts)
    if s is None:
        return None
    tz = _tz(tzname)
    s = s.astimezone(tz)
    day = "%s, %d %s" % (s.strftime("%A"), s.day, s.strftime("%B %Y"))
    e = _as_dt(ends)
    if e is not None:
        e = e.astimezone(tz)
        times = "%s–%s" % (_t12(s), _t12(e))
    else:
        times = _t12(s)
    return "%s · %s (%s)" % (day, times, _tz_label(s))


def _money(minor, currency=None):
    try:
        n = int(minor)
    except (TypeError, ValueError):
        return None
    sym = {"ZAR": "R", "USD": "$", "GBP": "£", "EUR": "€"}.get(currency or "ZAR", "")
    return "%s%.2f" % (sym, n / 100)


def _pay_status(settlement_mode, charge):
    """The human payment status — delegates to the ONE canonical vocabulary (billing.statement.
    settlement_status_label) so the email, the receipt, and the client record always say the same
    thing. Resolves the settled `state` from the shared charge reader, then labels it."""
    state = (charge or {}).get("state") or (charge or {}).get("status")
    try:
        from billing.statement import settlement_status_label
        return settlement_status_label(state, settlement_mode)
    except Exception:
        return None


_TYPE_LABEL = {"court": "Court booking", "lesson": "Private lesson", "class": "Class"}


def _clean_service(raw, booking_type=None):
    """A human service label. Prefer the real product/service name; if all we have is the raw
    booking-type word ('court'/'lesson'/'class') — which is what order_line.description stores for a
    booking — map it to a clean label so an email never shows a bare lowercase 'court'."""
    if raw:
        return _TYPE_LABEL.get(str(raw).strip().lower(), raw)
    return _TYPE_LABEL.get(booking_type, "Booking")


def _fmt_day(d):
    """'11 Jul 2026' from a date / datetime / ISO string (day only, no time). None → None."""
    dd = _as_dt(d)
    return ("%d %s" % (dd.day, dd.strftime("%b %Y"))) if dd is not None else None


def _fmt_period(start, end):
    """A membership/pack validity window: '11 Jul 2026 – 11 Jul 2027'. Degrades gracefully to
    'Until <end>' or just the start when only one side is known."""
    a, b = _fmt_day(start), _fmt_day(end)
    if a and b:
        return "%s – %s" % (a, b)
    if b:
        return "Until %s" % b
    return a


# ---------------------------------------------------------------------------
# loaders — return a normalized detail dict, or None (guarded)
# ---------------------------------------------------------------------------

def load(session, club_id, ctx):
    """Load the rich detail for an event's booking/class. Returns a normalized dict or None.
    NEVER raises — any failure (missing row, schema drift, no id) falls back to None."""
    ctx = ctx or {}
    if not club_id:
        return None
    from sqlalchemy import text
    try:
        if ctx.get("booking_id"):
            return _load_booking(session, club_id, ctx)
        if ctx.get("class_session_id"):
            return _load_class(session, club_id, ctx)
        # An order-keyed event (payment_succeeded / refunded / membership / pack). If the order links to
        # a BOOKING or CLASS, show the SAME rich block as the confirmation (Service · When · Duration ·
        # Court · Coach · Payment) so an online booking's payment email IS its confirmation. Only a true
        # PURCHASE (membership / pack) falls through to the flat purchase block.
        oid = ctx.get("order_id") or (ctx.get("ref_id") if ctx.get("ref_type") == "order" else None)
        if oid:
            bk = session.execute(
                text("SELECT booking_id FROM billing.order_line "
                     "WHERE order_id = :o AND booking_id IS NOT NULL ORDER BY created_at LIMIT 1"),
                {"o": str(oid)},
            ).scalar()
            if bk:
                return _load_booking(session, club_id, {"booking_id": str(bk)})
            en = session.execute(
                text("SELECT id, class_session_id, user_id FROM diary.enrolment "
                     "WHERE order_id = :o ORDER BY enrolled_at LIMIT 1"),
                {"o": str(oid)},
            ).mappings().first()
            if en:
                return _load_class(session, club_id, {
                    "class_session_id": str(en["class_session_id"]),
                    "user_id": str(en["user_id"]) if en["user_id"] else None})
            return _load_order(session, club_id, oid)
    except Exception:
        log.debug("booking_detail.load failed", exc_info=False)
    return None


def _club_tzname(session, club_id):
    try:
        from sqlalchemy import text
        row = session.execute(
            text("SELECT timezone FROM club.club WHERE id = :c"), {"c": str(club_id)}
        ).first()
        return (row[0] if row else None) or "Africa/Johannesburg"
    except Exception:
        return "Africa/Johannesburg"


def _load_booking(session, club_id, ctx):
    from sqlalchemy import text
    b = session.execute(
        text("""
            SELECT b.id, b.booking_type, b.status, b.starts_at, b.ends_at,
                   r.name AS resource_name, b.coach_user_id, b.order_id, b.settlement_mode,
                   -- OWNER = the CLIENT the booking is FOR (booked_by_user_id — on-behalf already sets
                   -- this to the client). ACTOR = who performed the action (created_by_user_id) — shown
                   -- as "Booked by" only when it differs from the owner (staff/parent booking).
                   cl.first_name AS cl_first, cl.surname AS cl_surname,
                   cl.email AS cl_email, cl.phone AS cl_phone,
                   ac.first_name AS ac_first, ac.surname AS ac_surname,
                   -- The prepaid pack this booking drew (so "Paid by package" names WHICH one).
                   (SELECT bp.label FROM billing.token_ledger tl
                      JOIN billing.token_wallet tw ON tw.id = tl.wallet_id
                      LEFT JOIN billing.bundle_plan bp ON bp.id = tw.bundle_plan_id
                      WHERE tl.booking_id = b.id AND bp.label IS NOT NULL LIMIT 1) AS pack_label,
                   co.email AS coach_email,
                   COALESCE(cp.display_name,
                            NULLIF(TRIM(COALESCE(co.first_name,'') || ' ' || COALESCE(co.surname,'')),''))
                     AS coach_name,
                   (SELECT cr.name FROM diary.booking cb JOIN diary.resource cr ON cr.id = cb.resource_id
                     WHERE cb.club_id = b.club_id AND cb.order_id = b.order_id
                       AND cb.booking_type = 'court' AND b.order_id IS NOT NULL
                       AND b.booking_type = 'lesson' LIMIT 1) AS held_court,
                   -- Prefer the real service/product NAME (e.g. "Hardcourt Hire", "Private Lesson");
                   -- the order-line description is only the raw booking_type ("court") so it's the
                   -- fallback, cleaned to a label below. Never surfaces a bare lowercase "court".
                   (SELECT COALESCE(p.name, NULLIF(ol.description,''))
                      FROM billing.order_line ol
                      LEFT JOIN billing.price pr ON pr.id = ol.price_id
                      LEFT JOIN billing.product p ON p.id = pr.product_id
                      WHERE ol.booking_id = b.id ORDER BY ol.created_at LIMIT 1) AS service_name,
                   -- Equipment hired on this booking (ball machine / racquets), so the confirmation
                   -- lists it. NULL when none.
                   (SELECT string_agg(er.name || (CASE WHEN be.qty > 1 THEN ' x' || be.qty ELSE '' END), ', ')
                      FROM diary.booking_equipment be JOIN diary.resource er ON er.id = be.resource_id
                      WHERE be.booking_id = b.id) AS equipment
            FROM diary.booking b
            LEFT JOIN diary.resource r ON r.id = b.resource_id
            LEFT JOIN iam."user" cl ON cl.id = b.booked_by_user_id
            LEFT JOIN iam."user" ac ON ac.id = b.created_by_user_id
            LEFT JOIN iam."user" co ON co.id = b.coach_user_id
            LEFT JOIN iam.coach_profile cp ON cp.user_id = b.coach_user_id AND cp.club_id = b.club_id
            WHERE b.id = :bid AND b.club_id = :c
        """),
        {"bid": str(ctx["booking_id"]), "c": str(club_id)},
    ).mappings().first()
    if not b:
        return None

    charge = _charge(session, club_id, b["order_id"], b["settlement_mode"])
    is_lesson = b["booking_type"] == "lesson"
    court = b["held_court"] if is_lesson else b["resource_name"]
    service = _clean_service(b["service_name"], b["booking_type"])
    tzname = _club_tzname(session, club_id)
    # The block shows the OWNER (the client the booking is for = booked_by_user_id). "Booked by" names
    # the ACTOR (created_by_user_id) only when they differ (a staff/parent booking) — a self-book omits it.
    owner_name = " ".join(x for x in [b["cl_first"], b["cl_surname"]] if x).strip() or None
    actor_name = " ".join(x for x in [b["ac_first"], b["ac_surname"]] if x).strip() or None
    return _normalize(
        service=service, booking_type=b["booking_type"],
        starts=b["starts_at"], ends=b["ends_at"], tzname=tzname, court=court,
        coach_name=(b["coach_name"] if is_lesson else None),
        coach_email=(b["coach_email"] if is_lesson else None),
        cl_first=b["cl_first"], cl_surname=b["cl_surname"],
        cl_email=b["cl_email"], cl_phone=b["cl_phone"],
        booked_by_name=(actor_name if (actor_name and actor_name != owner_name) else None),
        pack_label=b.get("pack_label"),
        settlement_mode=b["settlement_mode"], charge=charge,
        equipment=b.get("equipment"),
    )


def _load_class(session, club_id, ctx):
    from sqlalchemy import text
    cs = session.execute(
        text("""
            SELECT cs.id, cs.starts_at, cs.ends_at, cs.court_resource_id,
                   r.name AS class_name,
                   (SELECT cr.name FROM diary.resource cr WHERE cr.id = cs.court_resource_id) AS court_name,
                   co.email AS coach_email,
                   COALESCE(cp.display_name,
                            NULLIF(TRIM(COALESCE(co.first_name,'') || ' ' || COALESCE(co.surname,'')),''))
                     AS coach_name
            FROM diary.class_session cs
            LEFT JOIN diary.resource r ON r.id = cs.resource_id
            LEFT JOIN iam."user" co ON co.id = cs.coach_user_id
            LEFT JOIN iam.coach_profile cp ON cp.user_id = cs.coach_user_id AND cp.club_id = cs.club_id
            WHERE cs.id = :cid AND cs.club_id = :c
        """),
        {"cid": str(ctx["class_session_id"]), "c": str(club_id)},
    ).mappings().first()
    if not cs:
        return None

    # The enrolled player (the recipient) + their charge, resolved from the enrolment/order.
    cl_first = cl_surname = cl_email = cl_phone = None
    charge = None
    settlement_mode = None
    uid = ctx.get("user_id")
    if uid:
        u = session.execute(
            text('SELECT first_name, surname, email, phone FROM iam."user" WHERE id = :u'),
            {"u": str(uid)},
        ).mappings().first()
        if u:
            cl_first, cl_surname = u["first_name"], u["surname"]
            cl_email, cl_phone = u["email"], u["phone"]
        en = session.execute(
            text("SELECT order_id, settlement_mode FROM diary.enrolment "
                 "WHERE class_session_id = :cs AND user_id = :u ORDER BY enrolled_at DESC LIMIT 1"),
            {"cs": str(cs["id"]), "u": str(uid)},
        ).mappings().first()
        if en:
            # Use the enrolment's REAL settlement mode (was hardcoded None → the class Payment line
            # was mislabelled). Feeds both the charge read and the pay-status wording.
            settlement_mode = en["settlement_mode"]
            if en["order_id"]:
                charge = _charge(session, club_id, en["order_id"], settlement_mode)

    tzname = _club_tzname(session, club_id)
    return _normalize(
        service=cs["class_name"] or "Class", booking_type="class",
        starts=cs["starts_at"], ends=cs["ends_at"], tzname=tzname, court=cs["court_name"],
        coach_name=cs["coach_name"], coach_email=cs["coach_email"],
        cl_first=cl_first, cl_surname=cl_surname, cl_email=cl_email, cl_phone=cl_phone,
        settlement_mode=settlement_mode, charge=charge,
    )


def _load_order(session, club_id, order_id):
    """Rich detail for a PURCHASE (membership / session pack / a paid-booking receipt) keyed off the
    billing.order — the exact item(s) bought, the amount, and HOW it's being paid (paid online / pay
    at court / on monthly account). Powers the payment/membership/pack confirmation emails so they say
    WHAT was bought and WHERE it's paid from, not just 'payment processed'. Guarded → None."""
    from sqlalchemy import text
    o = session.execute(
        text('''
            SELECT o.id, o.settlement_mode, o.created_at,
                   cl.first_name AS cl_first, cl.surname AS cl_surname,
                   cl.email AS cl_email, cl.phone AS cl_phone
            FROM billing."order" o
            LEFT JOIN iam."user" cl ON cl.id = o.user_id
            WHERE o.id = :o AND o.club_id = :c
        '''),
        {"o": str(order_id), "c": str(club_id)},
    ).mappings().first()
    if not o:
        return None
    tzname = _club_tzname(session, club_id)
    charge = _charge(session, club_id, str(order_id), o["settlement_mode"])
    service = when = None
    when_label = "When"
    okind = "purchase"

    # WHAT KIND of purchase is this? billing."order" has no type column, so infer from the linked row
    # — the same dispatch order the Yoco webhook uses: membership, else pack, else a booking/class.
    mem = session.execute(
        text('''SELECT ms.period_start, ms.current_period_end, ms.provider,
                       pr.membership_tier, pr.label AS price_label, pr.term_months
                FROM billing.membership_subscription ms
                LEFT JOIN billing.price pr ON pr.id = ms.price_id
                WHERE ms.order_id = :o ORDER BY ms.created_at LIMIT 1'''),
        {"o": str(order_id)},
    ).mappings().first()
    if mem:
        okind = "membership"
        # Name the EXACT membership, mirroring billing.membership_status precedence (tier → label →
        # term → "Membership"); a trial is always the "7 Day Trial Period".
        if (mem["provider"] or "") == "trial":
            service = "7 Day Trial Period"
        else:
            tier = (mem["membership_tier"] or mem["price_label"]
                    or (("%d-month" % mem["term_months"]) if mem["term_months"] else None))
            service = ("%s Membership" % tier) if (tier and "member" not in tier.lower()) \
                else (tier or "Membership")
        when_label, when = "Period", _fmt_period(mem["period_start"], mem["current_period_end"])
    else:
        pack = session.execute(
            text('''SELECT purchased_at, expires_at FROM billing.token_wallet
                    WHERE order_id = :o ORDER BY created_at LIMIT 1'''),
            {"o": str(order_id)},
        ).mappings().first()
        # The item(s). For a PACK the line description IS the pack ("Session pack — 10 sessions") while
        # its price points at the coach's lesson/class PRODUCT ("Private Lesson") — so a pack must
        # prefer the DESCRIPTION or it renders as the bare service (the "package details not showing"
        # bug). A booking prefers the real product name; a bare booking-type word is cleaned to a label.
        rows = session.execute(
            text('''SELECT NULLIF(ol.description,'') AS descr, p.name AS pname
                    FROM billing.order_line ol
                    LEFT JOIN billing.price pr ON pr.id = ol.price_id
                    LEFT JOIN billing.product p ON p.id = pr.product_id
                    WHERE ol.order_id = :o ORDER BY ol.created_at'''),
            {"o": str(order_id)},
        ).mappings().all()
        items = []
        for r in rows:
            it = (r["descr"] or r["pname"]) if pack else (r["pname"] or r["descr"])
            if it:
                items.append(_clean_service(it))
        service = ", ".join(dict.fromkeys(items)) or None
        if pack:
            okind = "pack"
            when_label = "Validity"
            when = _fmt_period(pack["purchased_at"], pack["expires_at"]) if pack["expires_at"] else "No expiry"
        else:
            # A paid booking/class receipt → show WHEN the session is (order → booking, else enrolment).
            t = session.execute(
                text('''SELECT b.starts_at, b.ends_at FROM billing.order_line ol
                        JOIN diary.booking b ON b.id = ol.booking_id
                        WHERE ol.order_id = :o ORDER BY ol.created_at LIMIT 1'''),
                {"o": str(order_id)},
            ).first()
            if not t:
                t = session.execute(
                    text('''SELECT cs.starts_at, cs.ends_at FROM diary.enrolment e
                            JOIN diary.class_session cs ON cs.id = e.class_session_id
                            WHERE e.order_id = :o ORDER BY e.enrolled_at LIMIT 1'''),
                    {"o": str(order_id)},
                ).first()
            if t:
                when = fmt_when(t[0], t[1], tzname)

    name = " ".join(x for x in [o["cl_first"], o["cl_surname"]] if x).strip() or None
    price = None
    if charge and charge.get("amount_minor"):
        price = _money(charge.get("amount_minor"), charge.get("currency"))
    return {
        "is_purchase": True,
        "booking_type": "purchase",
        "order_kind": okind,
        "service": service,
        "when": when, "when_label": when_label, "duration_minutes": None, "court": None,
        "coach": {"name": None, "email": None},
        "client": {"first": o["cl_first"], "surname": o["cl_surname"], "name": name,
                   "email": o["cl_email"], "phone": o["cl_phone"]},
        "price": price,
        "pay_status": _pay_status(o["settlement_mode"], charge),
    }


def _charge(session, club_id, order_id, settlement_mode):
    """The pure price/payment-status read (reuse diary's, so figures never drift). Guarded."""
    try:
        from diary.bookings import _booking_charge
        return _booking_charge(session, club_id, order_id, settlement_mode)
    except Exception:
        return None


def _normalize(*, service, booking_type, starts, ends, tzname, court, coach_name, coach_email,
               cl_first, cl_surname, cl_email, cl_phone, settlement_mode, charge, equipment=None,
               booked_by_name=None, pack_label=None):
    s, e = _as_dt(starts), _as_dt(ends)
    dur = int((e - s).total_seconds() // 60) if (s and e) else None
    # Name is the real name ONLY — never the email as a fallback (that duplicated the email into the
    # "Name" row for imported/name-less clients). No name → the Name row is simply omitted.
    name = " ".join(x for x in [cl_first, cl_surname] if x).strip() or None
    price = None
    if charge and charge.get("amount_minor"):
        price = _money(charge.get("amount_minor"), charge.get("currency"))
    # "Paid by package" — name WHICH pack when the booking drew a prepaid token.
    pay_status = _pay_status(settlement_mode, charge)
    if pack_label and settlement_mode == "token":
        pay_status = "Paid by package: " + pack_label
    return {
        "booking_type": booking_type,
        "service": service,
        "when": fmt_when(starts, ends, tzname),
        "duration_minutes": dur,
        "court": court,
        "equipment": (equipment or None),
        "coach": {"name": coach_name, "email": (coach_email or None)},
        "client": {"first": cl_first, "surname": cl_surname, "name": name,
                   "email": cl_email, "phone": cl_phone},
        "booked_by": booked_by_name,
        "price": price,
        "pay_status": pay_status,
    }


# ---------------------------------------------------------------------------
# renderers — HTML (inside the green-banner shell) + plain text
# ---------------------------------------------------------------------------

def _esc(s):
    return (str(s if s is not None else "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _rows_html(rows):
    out = []
    for label, value in rows:
        if value in (None, ""):
            continue
        # Fixed label-column width so the VALUE columns line up across sections (Client details ↔
        # Booking details) — the owner's alignment ask. width= attribute is email-client-robust.
        out.append(
            '<tr>'
            '<td width="132" style="width:132px;padding:6px 12px 6px 0;color:#5F7268;font-size:13px;'
            'white-space:nowrap;vertical-align:top">%s</td>'
            '<td style="padding:6px 0;color:#10231A;font-size:14px;font-weight:600">%s</td>'
            '</tr>' % (_esc(label), _esc(value)))
    return "".join(out)


def _section(title, rows_html):
    if not rows_html:
        return ""
    return (
        '<div style="margin:16px 0 0">'
        '<div style="font-size:12px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;'
        'color:#0E7A47;border-bottom:1px solid #E2E9E5;padding-bottom:6px;margin-bottom:6px">%s</div>'
        '<table style="border-collapse:collapse;width:100%%">%s</table>'
        '</div>' % (_esc(title), rows_html))


def html_block(d):
    """The rich detail block HTML, to sit under the intro sentence inside the green-banner shell."""
    if not d:
        return ""
    cl = d.get("client") or {}
    client_rows = _rows_html([
        ("Name", cl.get("name")),
        ("Email", cl.get("email")),
        ("Cell", cl.get("phone")),
        ("Booked by", d.get("booked_by")),        # the actor, only when different from the owner above
    ])
    if d.get("is_purchase"):
        purchase_rows = _rows_html([
            ("Item", d.get("service")),
            (d.get("when_label") or "When", d.get("when")),
            ("Amount", d.get("price")),
            ("Payment", d.get("pay_status")),
        ])
        return _section("Client details", client_rows) + _section("Purchase details", purchase_rows)
    dur = ("%d min" % d["duration_minutes"]) if d.get("duration_minutes") else None
    booking_rows = _rows_html([
        ("Service", d.get("service")),
        ("When", d.get("when")),
        ("Duration", dur),
        ("Court", d.get("court")),
        ("Equipment", d.get("equipment")),
        ("Coach", (d.get("coach") or {}).get("name")),
        ("Price", d.get("price")),
        ("Payment", d.get("pay_status")),
    ])
    return _section("Client details", client_rows) + _section("Booking details", booking_rows)


def text_block(d):
    """Plain-text mirror of html_block."""
    if not d:
        return ""
    cl = d.get("client") or {}
    lines = ["CLIENT DETAILS"]
    for label, value in [("Name", cl.get("name")), ("Email", cl.get("email")),
                         ("Cell", cl.get("phone")), ("Booked by", d.get("booked_by"))]:
        if value:
            lines.append("  %s: %s" % (label, value))
    lines.append("")
    if d.get("is_purchase"):
        lines.append("PURCHASE DETAILS")
        for label, value in [("Item", d.get("service")), (d.get("when_label") or "When", d.get("when")),
                             ("Amount", d.get("price")), ("Payment", d.get("pay_status"))]:
            if value:
                lines.append("  %s: %s" % (label, value))
        return "\n".join(lines)
    lines.append("BOOKING DETAILS")
    dur = ("%d min" % d["duration_minutes"]) if d.get("duration_minutes") else None
    for label, value in [("Service", d.get("service")), ("When", d.get("when")),
                         ("Duration", dur), ("Court", d.get("court")),
                         ("Equipment", d.get("equipment")),
                         ("Coach", (d.get("coach") or {}).get("name")),
                         ("Price", d.get("price")), ("Payment", d.get("pay_status"))]:
        if value:
            lines.append("  %s: %s" % (label, value))
    return "\n".join(lines)


def coach_email(d):
    """The coach's email for a lesson/class (for BCC), or None."""
    return ((d or {}).get("coach") or {}).get("email")

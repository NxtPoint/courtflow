# marketing_crm/email/coach_statement_detail.py — the coach month-end statement email's rich block.
#
# WHY THIS EXISTS AT ALL, and why it is not a BCC. The ask was "copy the coach on the monthly
# invoices so they can see who is being billed and at what amount". A month-end invoice is ONE
# consolidated document per CLIENT — court hire, membership, packs and lessons from SEVERAL
# coaches on one page — so there is no single "the coach" to copy, and whoever was copied would
# read another coach's rates and that client's entire financial position. The platform already
# refuses that shape everywhere else: a coach is BCC'd only on their OWN lesson or class.
#
# So the coach gets their own addressed email about their own month instead, which is strictly
# more of what was actually wanted: every client they coached, what each was billed, what has
# been collected, what is still owed, and what they earned.
#
# It INVENTS NO MONEY. Everything here comes from billing.commission.coach_statement — the same
# reader behind the coach's Money screen — so the email and the screen cannot drift apart or
# disagree about a rand. This module only shapes that dict for email.
#
# Mirrors invoice_detail.py: same green section styling, same shell, everything defensive so a
# rendering failure degrades to the plain body and never blocks the month-end sweep.

from __future__ import annotations

import logging

log = logging.getLogger("marketing_crm.email.coach_statement_detail")

_GREEN = "#0E7A47"
_MUTED = "#5F7268"
_RULE = "#E2E9E5"


def _esc(s):
    return (str(s if s is not None else "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _money(minor, currency="ZAR"):
    try:
        n = int(minor or 0)
    except (TypeError, ValueError):
        n = 0
    sym = {"ZAR": "R", "USD": "$", "GBP": "£", "EUR": "€"}.get(currency or "ZAR", "")
    return f"{sym}{n / 100:,.2f}"


def load(session, club_id, ctx):
    """Build the statement dict for the coach named in an emit payload (`coach_user_id`, `month`).

    Delegates wholly to billing.commission.coach_statement so there is exactly ONE definition of a
    coach's month. Returns None on anything unexpected — the email then sends with its plain body,
    which is the correct degradation: a coach still learns their statement is ready."""
    ctx = ctx or {}
    coach_user_id = ctx.get("coach_user_id")
    if not coach_user_id:
        return None
    try:
        from billing.commission import coach_statement
        doc = coach_statement(session, club_id=club_id, coach_user_id=coach_user_id,
                              month=ctx.get("month"))
    except Exception:
        log.debug("coach statement block: could not load", exc_info=False)
        return None
    if not doc:
        return None
    # Drop clients with nothing on them this month — a row of zeros is noise in an email, however
    # reasonable it looks in a table on a screen the coach chose to open.
    doc = dict(doc)
    doc["clients"] = [c for c in (doc.get("clients") or [])
                      if (c.get("lessons") or c.get("paid_minor") or c.get("owed_minor"))]
    return doc


def has_content(doc):
    """True when this coach has anything worth emailing about. A coach with no clients, no money
    and no rent gets NO email: a monthly 'you earned R0.00' is not a statement, it is attrition."""
    if not doc:
        return False
    t = doc.get("totals") or {}
    return bool(doc.get("clients") or t.get("billed_minor") or t.get("paid_minor")
                or t.get("owed_minor") or t.get("rent_minor"))


def _rows(doc):
    """(label, value) summary pairs. Ordered as the money actually flows — what the coaching was
    worth, what reached the club, what has not been collected, what the coach keeps — so the email
    reads as an explanation rather than a list of figures."""
    cur = doc.get("currency") or "ZAR"
    t = doc.get("totals") or {}
    out = [
        ("Coaching billed", _money(t.get("billed_minor"), cur)),
        ("Collected from clients", _money(t.get("collected_minor"), cur)),
    ]
    if t.get("owed_minor"):
        out.append(("Still owed by clients", _money(t.get("owed_minor"), cur)))
    if t.get("written_off_minor"):
        out.append(("Written off", _money(t.get("written_off_minor"), cur)))
    out.append(("Club commission", _money(t.get("commission_minor"), cur)))
    if t.get("rent_minor"):
        out.append(("Court rent", _money(t.get("rent_minor"), cur)))
    out.append(("You earned", _money(t.get("paid_minor"), cur)))
    return out


def text_block(doc):
    """Plain-text half of the email. Deliberately complete on its own: a coach reading this on a
    phone with images off must still be able to check a client's figure without opening the app."""
    if not doc:
        return ""
    cur = doc.get("currency") or "ZAR"
    lines = ["Your clients this month", "-" * 52]
    for c in doc.get("clients") or []:
        name = c.get("client_name") or "Client"
        n = int(c.get("lessons") or 0)
        bits = ["%d session%s" % (n, "" if n == 1 else "s")]
        if c.get("paid_minor"):
            bits.append("you earned %s" % _money(c.get("paid_minor"), cur))
        if c.get("owed_minor"):
            bits.append("%s still owed" % _money(c.get("owed_minor"), cur))
        lines.append("%-28s %s" % (name[:28], " | ".join(bits)))
    lines += ["", "Summary", "-" * 52]
    for label, value in _rows(doc):
        lines.append("%-28s %s" % (label, value))
    if doc.get("arrears_items"):
        owed = [i for i in doc["arrears_items"] if i.get("status") == "owed"]
        if owed:
            lines += ["", "%d session%s not yet collected. The club chases these on your behalf; "
                          "they stay on your tab until they are paid."
                      % (len(owed), "" if len(owed) == 1 else "s")]
    return "\n".join(lines)


def html_block(doc):
    """HTML half — a TABLE, not divs, because Outlook's Word engine ignores layout on a <div>."""
    if not doc:
        return ""
    cur = doc.get("currency") or "ZAR"
    body = []
    clients = doc.get("clients") or []
    if clients:
        body.append(
            '<p style="margin:18px 0 6px;font-weight:700;color:%s">Your clients this month</p>' % _GREEN)
        body.append('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                    'border="0" style="width:100%;font-size:14px">')
        for c in clients:
            n = int(c.get("lessons") or 0)
            sub = "%d session%s" % (n, "" if n == 1 else "s")
            if c.get("owed_minor"):
                sub += " &middot; %s still owed" % _esc(_money(c.get("owed_minor"), cur))
            body.append(
                '<tr><td style="padding:7px 0;border-bottom:1px solid %s">'
                '<span style="font-weight:600">%s</span><br>'
                '<span style="color:%s;font-size:12px">%s</span></td>'
                '<td align="right" style="padding:7px 0;border-bottom:1px solid %s;'
                'white-space:nowrap;font-weight:600">%s</td></tr>'
                % (_RULE, _esc(c.get("client_name") or "Client"), _MUTED, sub, _RULE,
                   _esc(_money(c.get("paid_minor"), cur))))
        body.append("</table>")

    body.append('<p style="margin:18px 0 6px;font-weight:700;color:%s">Summary</p>' % _GREEN)
    body.append('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                'border="0" style="width:100%;font-size:14px">')
    rows = _rows(doc)
    for i, (label, value) in enumerate(rows):
        last = (i == len(rows) - 1)
        weight = "700" if last else "400"
        top = ("border-top:2px solid %s;" % _RULE) if last else ""
        body.append(
            '<tr><td style="padding:6px 0;%scolor:%s">%s</td>'
            '<td align="right" style="padding:6px 0;%sfont-weight:%s;white-space:nowrap">%s</td></tr>'
            % (top, _MUTED if not last else "#10231A", _esc(label), top, weight, _esc(value)))
    body.append("</table>")

    owed = [i for i in (doc.get("arrears_items") or []) if i.get("status") == "owed"]
    if owed:
        body.append(
            '<p style="margin:16px 0 0;padding:10px 12px;background:#FBF6E9;border-radius:8px;'
            'font-size:13px;color:#6B5A2E">%d session%s not yet collected. The club chases these '
            'on your behalf; they stay on your tab until they are paid.</p>'
            % (len(owed), "" if len(owed) == 1 else "s"))
    return "".join(body)

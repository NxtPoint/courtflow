# marketing_crm/email/invoice_detail.py — the invoice email's rich block + PDF attachment.
#
# Mirrors booking_detail.py (same green-section styling, same shell) but for an issued invoice:
# a concise statement SUMMARY in the body (line items + total + amount due), a prominent
# "Pay online" box, an EFT note, and the full professional PDF as an attachment.
#
# The invoice DATA + PDF come from the billing lane (billing.invoicing / billing.invoice_pdf);
# this module only shapes them for email. Everything is defensive → None on any failure so a
# delivery hiccup never blocks the issue path.

from __future__ import annotations

import logging
import os

log = logging.getLogger("marketing_crm.email.invoice_detail")

_GREEN = "#0E7A47"


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
    """Resolve the canonical invoice document from an emit payload carrying `invoice_id`.
    Returns the doc dict (+ a resolved absolute pay_url) or None."""
    invoice_id = (ctx or {}).get("invoice_id")
    if not invoice_id:
        return None
    try:
        from billing import invoicing
        doc = invoicing.build_invoice_document(session, invoice_id=invoice_id, club_id=club_id)
        if not doc:
            return None
        doc["pay_url"] = invoicing.portal_url(session, club_id)
        return doc
    except Exception:
        log.debug("invoice_detail.load failed for %s", invoice_id, exc_info=False)
        return None


def _lines_table(doc):
    ccy = doc.get("currency") or "ZAR"
    rows = []
    for ln in (doc.get("lines") or []):
        qty = ln.get("qty") or 1
        desc = _esc(ln.get("description") or "—")
        if qty and int(qty) > 1:
            desc += ' <span style="color:#8A9A92">×%s</span>' % int(qty)
        rows.append(
            '<tr><td style="padding:6px 0;color:#10231A;font-size:14px">%s</td>'
            '<td align="right" style="padding:6px 0;color:#10231A;font-size:14px;white-space:nowrap">%s</td></tr>'
            % (desc, _esc(_money(ln.get("amount_minor"), ccy))))
    if not rows:
        rows.append('<tr><td colspan="2" style="color:#8A9A92;font-size:13px;padding:6px 0">No line items</td></tr>')
    return "".join(rows)


def html_block(doc):
    """The invoice summary block HTML, to sit under the intro sentence inside the green shell."""
    if not doc:
        return ""
    ccy = doc.get("currency") or "ZAR"
    pay_url = doc.get("pay_url") or "/portal"
    outstanding = int(doc.get("outstanding_minor") or 0)

    meta_rows = []
    for label, value in [("Invoice", doc.get("number")), ("Date", (doc.get("issued_at") or "")[:10]),
                         ("Due", (doc.get("due_date") or "")[:10]), ("Status", doc.get("status_label"))]:
        if value:
            meta_rows.append(
                '<tr><td width="120" style="width:120px;padding:5px 12px 5px 0;color:#5F7268;font-size:13px;'
                'white-space:nowrap;vertical-align:top">%s</td>'
                '<td style="padding:5px 0;color:#10231A;font-size:14px;font-weight:600">%s</td></tr>'
                % (_esc(label), _esc(value)))

    def _sect(title, inner):
        return ('<div style="margin:16px 0 0">'
                '<div style="font-size:12px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;'
                'color:#0E7A47;border-bottom:1px solid #E2E9E5;padding-bottom:6px;margin-bottom:6px">%s</div>'
                '%s</div>' % (_esc(title), inner))

    out = _sect("Invoice details", '<table style="border-collapse:collapse;width:100%%">%s</table>' % "".join(meta_rows))
    out += _sect("Summary",
                 '<table style="border-collapse:collapse;width:100%%">%s'
                 '<tr><td style="border-top:1px solid #E2E9E5;padding:8px 0 0;font-weight:800;font-size:14px">Total</td>'
                 '<td align="right" style="border-top:1px solid #E2E9E5;padding:8px 0 0;font-weight:800;font-size:14px">%s</td></tr>'
                 '%s</table>'
                 % (_lines_table(doc), _esc(_money(doc.get("total_minor"), ccy)),
                    ('<tr><td style="padding:4px 0;color:%s;font-weight:800;font-size:15px">Amount due</td>'
                     '<td align="right" style="padding:4px 0;color:%s;font-weight:800;font-size:15px">%s</td></tr>'
                     % (_GREEN, _GREEN, _esc(_money(outstanding, ccy))) if outstanding > 0 else "")))

    if outstanding > 0:
        # Prominent "Pay online" box (button) + an EFT pointer to the attached PDF.
        out += ('<div style="margin:18px 0 0">'
                '<table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:separate">'
                '<tr><td style="background:%s;border-radius:10px">'
                '<a href="%s" style="display:inline-block;padding:12px 26px;color:#ffffff;font-weight:800;'
                'font-size:15px;text-decoration:none">Pay online</a></td></tr></table>'
                '<div style="color:#5F7268;font-size:12.5px;margin-top:8px">'
                'Prefer EFT? Our banking details are on the attached invoice — use invoice '
                '<b>%s</b> as your reference.</div></div>'
                % (_GREEN, _esc(pay_url), _esc(doc.get("number") or "")))
    return out


def text_block(doc):
    if not doc:
        return ""
    ccy = doc.get("currency") or "ZAR"
    lines = ["Invoice %s" % (doc.get("number") or "")]
    if doc.get("due_date"):
        lines.append("Due: %s" % str(doc["due_date"])[:10])
    lines.append("")
    for ln in (doc.get("lines") or []):
        lines.append("  %s  %s" % ((ln.get("description") or "-"), _money(ln.get("amount_minor"), ccy)))
    lines.append("")
    lines.append("Total: %s" % _money(doc.get("total_minor"), ccy))
    outstanding = int(doc.get("outstanding_minor") or 0)
    if outstanding > 0:
        lines.append("Amount due: %s" % _money(outstanding, ccy))
        lines.append("")
        lines.append("Pay online: %s" % (doc.get("pay_url") or "/portal"))
        lines.append("Or pay by EFT — banking details are on the attached invoice (ref %s)." % (doc.get("number") or ""))
    return "\n".join(lines)


def pdf_attachment(doc):
    """The invoice PDF as an email attachment list, or None. FLAG-GATED on EMAIL_INVOICE_PDF_ENABLED
    because attachments need ses:SendRawEmail (the interim SES key lacks it → attachments silently
    dropped). Until the flag is on, the email still links to the in-portal PDF via 'Pay online'."""
    if os.getenv("EMAIL_INVOICE_PDF_ENABLED", "0").strip() != "1":
        return None
    if not doc:
        return None
    try:
        from billing import invoice_pdf
        pdf = invoice_pdf.render_pdf(doc, pay_online_url=doc.get("pay_url"))
        fname = (doc.get("number") or "invoice").replace("/", "-") + ".pdf"
        return [{"filename": fname, "content": pdf, "mimetype": "application/pdf"}]
    except Exception:
        log.debug("invoice pdf attachment failed", exc_info=False)
        return None

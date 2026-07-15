# billing/invoice_pdf.py — render a canonical invoice/receipt document dict to a PDF (reportlab).
#
# Presentation ONLY. Consumes the canonical shape produced by billing.invoicing
# (build_invoice_document / receipt_to_document) and emits professional A4 PDF bytes with a
# club letterhead, seller identity, bill-to, itemised lines, totals, a Paid/Unpaid status
# stamp, EFT bank-detail instructions (when unpaid), a Pay-online note, and terms/footer.
#
# reportlab is pure-Python (no system deps). Everything is defensive: a missing logo, address,
# or bank block never breaks the render — the document just omits that section.

from __future__ import annotations

import io
import logging
from typing import Any, Dict, Optional

log = logging.getLogger("billing.invoice_pdf")

# Brand palette (matches the app cf-* / email shell).
_GREEN = "#0E7A47"
_INK = "#10231A"
_MUTE = "#5F7268"
_LINE = "#E2E9E5"
_BG = "#F4F7F5"


def _money(minor, currency=None) -> str:
    try:
        n = int(minor or 0)
    except (TypeError, ValueError):
        n = 0
    sym = {"ZAR": "R", "USD": "$", "GBP": "£", "EUR": "€"}.get(currency or "ZAR", "")
    neg = n < 0
    return ("-" if neg else "") + f"{sym}{abs(n) / 100:,.2f}"


def _fmt_date(iso) -> str:
    if not iso:
        return ""
    s = str(iso)
    # canonical ISO → 'YYYY-MM-DD'
    return s[:10]


def _load_logo(url):
    """Best-effort logo → a reportlab ImageReader, or None. Accepts a data: URI or an http(s)
    URL (short-timeout fetch). Never raises."""
    if not url:
        return None
    try:
        from reportlab.lib.utils import ImageReader
        if str(url).startswith("data:"):
            import base64
            b64 = str(url).split(",", 1)[1]
            return ImageReader(io.BytesIO(base64.b64decode(b64)))
        if str(url).startswith(("http://", "https://")):
            import requests
            resp = requests.get(url, timeout=4)
            if resp.ok and resp.headers.get("content-type", "").startswith("image"):
                return ImageReader(io.BytesIO(resp.content))
        return None
    except Exception:
        log.debug("invoice logo load failed for %s", url, exc_info=False)
        return None


def render_pdf(doc: Dict[str, Any], *, pay_online_url: Optional[str] = None) -> bytes:
    """Render the canonical document dict → PDF bytes."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle, Paragraph,
                                    Spacer, Image, HRFlowable)

    seller = doc.get("seller") or {}
    bill_to = doc.get("bill_to") or {}
    currency = doc.get("currency") or "ZAR"
    title = doc.get("title") or ("Receipt" if doc.get("doc_type") == "receipt" else "Invoice")

    buf = io.BytesIO()
    pdf = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=18 * mm,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            title=f"{title} {doc.get('number') or ''}".strip())

    styles = getSampleStyleSheet()
    base = ParagraphStyle("base", parent=styles["Normal"], fontName="Helvetica",
                          fontSize=9.5, textColor=colors.HexColor(_INK), leading=13)
    mute = ParagraphStyle("mute", parent=base, textColor=colors.HexColor(_MUTE), fontSize=8.5, leading=11)
    h_name = ParagraphStyle("hname", parent=base, fontName="Helvetica-Bold", fontSize=15,
                            textColor=colors.HexColor(_GREEN), leading=18)
    label = ParagraphStyle("label", parent=mute, fontName="Helvetica-Bold", fontSize=8,
                           textColor=colors.HexColor(_MUTE))
    story = []

    # --- letterhead: logo/wordmark (left) + document meta (right) ---
    logo = _load_logo(seller.get("logo_url"))
    left_cell = []
    if logo:
        try:
            iw, ih = logo.getSize()
            w = 42 * mm
            h = max(1, w * ih / iw)
            if h > 22 * mm:
                h = 22 * mm
                w = h * iw / ih
            left_cell.append(Image(logo, width=w, height=h, hAlign="LEFT"))
        except Exception:
            left_cell.append(Paragraph(seller.get("name") or "", h_name))
    else:
        left_cell.append(Paragraph(seller.get("name") or "", h_name))

    meta_rows = [f"<b>{_esc(title)}</b>"]
    if doc.get("number"):
        meta_rows.append(f"No. {_esc(doc['number'])}")
    if doc.get("issued_at"):
        meta_rows.append(f"Date: {_fmt_date(doc['issued_at'])}")
    if doc.get("due_date"):
        meta_rows.append(f"Due: {_fmt_date(doc['due_date'])}")
    if doc.get("period_label"):
        meta_rows.append(f"Period: {_esc(doc['period_label'])}")
    right = Paragraph("<br/>".join(meta_rows), ParagraphStyle(
        "meta", parent=base, alignment=2, leading=14))

    head = Table([[left_cell, right]], colWidths=[95 * mm, 79 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(head)
    story.append(Spacer(1, 4 * mm))

    # status stamp
    label_txt = doc.get("status_label") or ("Paid" if doc.get("is_paid") else "Unpaid")
    stamp_color = _GREEN if doc.get("is_paid") else ("#B4232A" if label_txt in ("Unpaid", "Void") else "#B7791F")
    stamp = Table([[Paragraph(f'<font color="white"><b>{_esc(label_txt.upper())}</b></font>', base)]],
                  colWidths=[38 * mm])
    stamp.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(stamp_color)),
                              ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                              ("LEFTPADDING", (0, 0), (-1, -1), 10), ("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    story.append(Table([[stamp, ""]], colWidths=[38 * mm, 136 * mm],
                       style=TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0)])))
    story.append(Spacer(1, 5 * mm))

    # --- seller (from) + bill-to (to) columns ---
    from_lines = []
    for ln in (seller.get("address_lines") or []):
        from_lines.append(_esc(ln))
    if seller.get("company_reg_no"):
        from_lines.append(f"Reg: {_esc(seller['company_reg_no'])}")
    if seller.get("vat_number"):
        from_lines.append(f"VAT: {_esc(seller['vat_number'])}")
    if seller.get("email"):
        from_lines.append(_esc(seller["email"]))
    if seller.get("phone"):
        from_lines.append(_esc(seller["phone"]))

    to_lines = []
    if bill_to.get("name"):
        to_lines.append(f"<b>{_esc(bill_to['name'])}</b>")
    for ln in (bill_to.get("address_lines") or []):
        to_lines.append(_esc(ln))
    if bill_to.get("email"):
        to_lines.append(_esc(bill_to["email"]))
    if bill_to.get("phone"):
        to_lines.append(_esc(bill_to["phone"]))

    from_block = [Paragraph("FROM", label), Paragraph(f"<b>{_esc(seller.get('name') or '')}</b>", base),
                  Paragraph("<br/>".join(from_lines), mute)]
    to_block = [Paragraph("BILL TO", label),
                Paragraph("<br/>".join(to_lines) or '<font color="#999">—</font>', base)]
    parties = Table([[from_block, to_block]], colWidths=[95 * mm, 79 * mm])
    parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(parties)
    story.append(Spacer(1, 6 * mm))

    # --- line items table ---
    data = [[Paragraph('<font color="white"><b>Description</b></font>', base),
             Paragraph('<font color="white"><b>Qty</b></font>', base),
             Paragraph('<font color="white"><b>Amount</b></font>', base)]]
    for ln in (doc.get("lines") or []):
        data.append([Paragraph(_esc(ln.get("description") or "—"), base),
                     Paragraph(str(ln.get("qty") or 1), base),
                     Paragraph(_money(ln.get("amount_minor"), currency), ParagraphStyle(
                         "r", parent=base, alignment=2))])
    if len(data) == 1:
        data.append([Paragraph('<font color="#999">No line items</font>', base), "", ""])
    tbl = Table(data, colWidths=[120 * mm, 18 * mm, 36 * mm], repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_GREEN)),
        ("ALIGN", (1, 0), (1, -1), "CENTER"), ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor(_LINE)),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(tbl)

    # --- totals ---
    total = int(doc.get("total_minor") or 0)
    paid = int(doc.get("paid_minor") or 0)
    outstanding = int(doc.get("outstanding_minor") or 0)
    refunded = int(doc.get("refunded_minor") or 0)
    tot_rows = []
    vat_number = seller.get("vat_number")
    if vat_number and int(seller.get("vat_rate_bps") or 0) > 0:
        rate = int(seller["vat_rate_bps"]) / 10000.0
        if seller.get("prices_include_vat", True):
            vat = round(total - total / (1 + rate))
            net = total - vat
        else:
            net = total
            vat = round(total * rate)
        tot_rows.append(["Subtotal", _money(net, currency)])
        tot_rows.append([f"VAT ({rate * 100:g}%)", _money(vat, currency)])
    tot_rows.append(["Total", _money(total, currency)])
    if refunded > 0:
        tot_rows.append(["Refunded", "-" + _money(refunded, currency)])
    if doc.get("doc_type") == "invoice" or paid or outstanding:
        tot_rows.append(["Paid", _money(paid, currency)])
        tot_rows.append(["Amount due", _money(outstanding, currency)])

    trows = []
    for i, (k, v) in enumerate(tot_rows):
        strong = k in ("Total", "Amount due")
        st = ParagraphStyle(f"t{i}", parent=base, alignment=2,
                            fontName="Helvetica-Bold" if strong else "Helvetica",
                            fontSize=11 if strong else 9.5,
                            textColor=colors.HexColor(_GREEN if k == "Amount due" and outstanding > 0 else _INK))
        trows.append([Paragraph(k, st), Paragraph(v, st)])
    totals = Table(trows, colWidths=[36 * mm, 36 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([("LINEABOVE", (0, len(tot_rows) - (2 if (paid or outstanding) else 1)),
                                 (-1, len(tot_rows) - (2 if (paid or outstanding) else 1)),
                                 0.6, colors.HexColor(_LINE)),
                               ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    story.append(Spacer(1, 3 * mm))
    story.append(totals)
    story.append(Spacer(1, 6 * mm))

    # --- payment instructions (unpaid invoice) OR payments received (receipt/paid) ---
    if not doc.get("is_paid") and outstanding > 0:
        boxes = []
        if pay_online_url:
            boxes.append(_note_box("Pay online",
                                   f"Settle securely by card any time at<br/><b>{_esc(pay_online_url)}</b>",
                                   _GREEN))
        bank = seller.get("bank")
        if bank and bank.get("account_number"):
            bank_lines = []
            for k, lab in [("bank_name", "Bank"), ("account_name", "Account name"),
                           ("account_number", "Account no."), ("branch_code", "Branch code"),
                           ("swift", "SWIFT")]:
                if bank.get(k):
                    bank_lines.append(f"{lab}: <b>{_esc(bank[k])}</b>")
            ref = doc.get("number") or ""
            if ref:
                bank_lines.append(f"Reference: <b>{_esc(ref)}</b>")
            boxes.append(_note_box("Pay by EFT", "<br/>".join(bank_lines), _INK))
        if boxes:
            if len(boxes) == 2:
                row = Table([[boxes[0], boxes[1]]], colWidths=[85 * mm, 85 * mm])
                row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                        ("LEFTPADDING", (0, 0), (0, 0), 0),
                                        ("RIGHTPADDING", (-1, 0), (-1, 0), 0)]))
                story.append(row)
            else:
                story.append(boxes[0])
            story.append(Spacer(1, 5 * mm))
    elif doc.get("payments"):
        pay_lines = []
        for p in doc["payments"]:
            if p.get("direction") == "refund":
                continue
            bits = [p.get("method") or p.get("provider") or "Payment",
                    _money(p.get("amount_minor"), p.get("currency") or currency),
                    _fmt_date(p.get("created_at"))]
            if p.get("reference"):
                bits.append("ref " + str(p["reference"]))
            pay_lines.append(" · ".join(str(b) for b in bits if b))
        if pay_lines:
            story.append(_note_box("Payment received", "<br/>".join(_esc(x) for x in pay_lines), _GREEN))
            story.append(Spacer(1, 5 * mm))

    # --- terms / footer ---
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor(_LINE)))
    story.append(Spacer(1, 3 * mm))
    foot = doc.get("notes") or seller.get("terms") or ""
    thanks = f"Thank you for playing at {_esc(seller.get('trading_name') or seller.get('name') or 'NextPoint Tennis')}."
    story.append(Paragraph(_esc(foot) + (("<br/>" if foot else "") + thanks), mute))

    pdf.build(story)
    return buf.getvalue()


def _note_box(title, body_html, accent):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Table, TableStyle, Paragraph
    from reportlab.lib.units import mm
    base = getSampleStyleSheet()["Normal"]
    t = ParagraphStyle("nbt", parent=base, fontName="Helvetica-Bold", fontSize=9,
                       textColor=colors.HexColor(accent), leading=12)
    b = ParagraphStyle("nbb", parent=base, fontName="Helvetica", fontSize=9,
                       textColor=colors.HexColor(_INK), leading=13)
    inner = Table([[Paragraph(title.upper(), t)], [Paragraph(body_html, b)]])
    inner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(_BG)),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor(_LINE)),
        ("LINEBEFORE", (0, 0), (0, -1), 2.2, colors.HexColor(accent)),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
    ]))
    return inner


def _esc(s) -> str:
    return (str("" if s is None else s)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

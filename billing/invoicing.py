# billing/invoicing.py — the ONE home for invoice & receipt DOCUMENTS.
#
# Consolidates document assembly (previously the misplaced yoco_billing/receipt.py) with a
# proper, numbered, professional invoice document. Pure billing reads + a thin issue-write;
# the reportlab PDF renderer lives next door in billing/invoice_pdf.py (presentation only).
#
# THE INVARIANT (do not break): an invoice is a *document that RENDERS over live orders*,
# NEVER a second debt store. The debt lives on billing."order" and is settled exactly once
# (a client can still card-settle any open order in the real-time statement — issuing an
# invoice does NOT touch an order). An invoice's LINE AMOUNTS are frozen at issue (an
# immutable document + seller/bill-to snapshot), but its PAID / OUTSTANDING status is DERIVED
# LIVE from the orders its lines reference. So a mid-month card payment simply flips the
# invoice to Paid, and double-counting is structurally impossible (one debt store: orders).
#
# Two canonical builders, ONE document shape (so the PDF renderer + email are written once):
#   build_receipt(session, order_id=…)        → a proof-of-payment for a single order
#   build_invoice_document(session, invoice_id) → an issued, numbered invoice document
# plus issue_invoice(...) (create the document over orders) and void_invoice(...).

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import text


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _iso(v) -> Optional[str]:
    if v is None:
        return None
    try:
        return v.isoformat()
    except AttributeError:
        return str(v)


# Friendly payment-method label from the raw billing.payment.provider.
_METHOD_LABEL = {
    "yoco": "Card (online)",
    "cash": "Cash",
    "eft": "EFT",
    "card_at_desk": "Card (at desk)",
    "manual": "Manual",
    "trial": "Trial",
}


def method_label(provider: Optional[str]) -> str:
    return _METHOD_LABEL.get((provider or "").strip().lower(), (provider or "").replace("_", " ").title() or "—")


def _addr_lines(*parts) -> List[str]:
    """Compact a set of address parts into non-empty display lines."""
    return [str(p).strip() for p in parts if p is not None and str(p).strip()]


# ---------------------------------------------------------------------------
# seller (the club's financial identity) + bill-to (the payer)
# ---------------------------------------------------------------------------

def resolve_seller(session, club_id) -> Dict[str, Any]:
    """The club's financial identity for a document letterhead: registered name, address,
    company reg / VAT (VAT only when registered), billing contact, logo, and bank block (for
    EFT-payable invoices). Composed from club.club + club.location + club.branding +
    club.billing_profile. Guarded — a missing billing_profile row is fine (returns the base
    identity with no bank/VAT)."""
    club = session.execute(
        text("SELECT name, legal_name FROM club.club WHERE id = :c"), {"c": str(club_id)},
    ).mappings().first() or {}

    loc = session.execute(
        text("SELECT name, address_line, city, postal_code, country, phone, email "
             "FROM club.location WHERE club_id = :c ORDER BY created_at LIMIT 1"),
        {"c": str(club_id)},
    ).mappings().first() or {}

    logo_url = session.execute(
        text("SELECT logo_url FROM club.branding WHERE club_id = :c"), {"c": str(club_id)},
    ).scalar()

    bp = session.execute(
        text("SELECT registered_name, company_reg_no, vat_number, vat_rate_bps, "
             "prices_include_vat, bank_name, bank_account_name, bank_account_number, "
             "bank_branch_code, bank_swift, billing_email, billing_phone, "
             "invoice_terms, invoice_footer "
             "FROM club.billing_profile WHERE club_id = :c"),
        {"c": str(club_id)},
    ).mappings().first() or {}

    name = (bp.get("registered_name") or club.get("legal_name")
            or club.get("name") or "NextPoint Tennis")

    bank = None
    if bp.get("bank_account_number") or bp.get("bank_name"):
        bank = {
            "bank_name": bp.get("bank_name"),
            "account_name": bp.get("bank_account_name") or name,
            "account_number": bp.get("bank_account_number"),
            "branch_code": bp.get("bank_branch_code"),
            "swift": bp.get("bank_swift"),
        }

    vat_number = bp.get("vat_number") or None
    return {
        "name": name,
        "trading_name": club.get("name"),
        "address_lines": _addr_lines(loc.get("address_line"), loc.get("city"),
                                     loc.get("postal_code"), loc.get("country")),
        "company_reg_no": bp.get("company_reg_no"),
        "vat_number": vat_number,                       # None → not VAT-registered → no VAT line
        "vat_rate_bps": int(bp.get("vat_rate_bps") or 0),
        "prices_include_vat": bool(bp.get("prices_include_vat", True)),
        "email": bp.get("billing_email") or loc.get("email"),
        "phone": bp.get("billing_phone") or loc.get("phone"),
        "logo_url": logo_url,
        "bank": bank,
        "terms": bp.get("invoice_terms"),
        "footer": bp.get("invoice_footer"),
    }


def portal_url(session, club_id) -> str:
    """Absolute 'pay online' URL for a document/email: the club's own domain + /portal
    (falls back to PUBLIC_APP_URL env, else a relative /portal). Guarded."""
    import os
    try:
        domain = session.execute(
            text("SELECT domain FROM club.branding WHERE club_id = :c"), {"c": str(club_id)},
        ).scalar()
    except Exception:
        domain = None
    if domain:
        d = str(domain).strip().rstrip("/")
        if not d.startswith("http"):
            d = "https://" + d
        return d + "/portal"
    base = (os.getenv("PUBLIC_APP_URL") or os.getenv("APP_BASE_URL") or "").strip().rstrip("/")
    return (base + "/portal") if base else "/portal"


def resolve_bill_to(session, club_id, user_id) -> Dict[str, Any]:
    """The payer's display block (name/email/phone). NULL user_id → an empty block."""
    if not user_id:
        return {"name": None, "email": None, "phone": None, "address_lines": []}
    u = session.execute(
        text("SELECT email, first_name, surname, phone FROM iam.user WHERE id = :u"),
        {"u": str(user_id)},
    ).mappings().first() or {}
    name = " ".join(p for p in [u.get("first_name"), u.get("surname")] if p) or None
    return {"name": name, "email": u.get("email"), "phone": u.get("phone"), "address_lines": []}


# ---------------------------------------------------------------------------
# live order state (the single source of paid/owed truth — never the invoice)
# ---------------------------------------------------------------------------

def _order_state(session, order_id) -> Optional[Dict[str, Any]]:
    """Live money state of ONE order, derived from its status + billing.payment rows.
    Returns {status, settlement_mode, currency, amount_minor, paid_minor, refunded_minor,
    net_minor, owed_minor} or None if the order is gone."""
    o = session.execute(
        text('SELECT amount_minor, currency_code, settlement_mode, status '
             'FROM billing."order" WHERE id = :id'),
        {"id": str(order_id)},
    ).mappings().first()
    if not o:
        return None
    paid = 0
    refunded = 0
    for r in session.execute(
        text("SELECT amount_minor, direction, status FROM billing.payment WHERE order_id = :oid"),
        {"oid": str(order_id)},
    ).mappings().all():
        amt = int(r["amount_minor"] or 0)
        if r["direction"] == "refund":
            refunded += amt
        elif r["status"] == "succeeded":
            paid += amt
    amount = int(o["amount_minor"] or 0)
    owed = amount if o["status"] == "open" else 0
    return {
        "status": o["status"],
        "settlement_mode": o["settlement_mode"],
        "currency": o["currency_code"],
        "amount_minor": amount,
        "paid_minor": paid,
        "refunded_minor": refunded,
        "net_minor": paid - refunded,
        "owed_minor": owed,
    }


# ---------------------------------------------------------------------------
# RECEIPT — a proof-of-payment document for a single order (any settlement mode)
# ---------------------------------------------------------------------------

def build_receipt(session, *, order_id: str) -> Optional[Dict[str, Any]]:
    """Backward-compatible receipt dict (superset of the old yoco_billing/receipt.py shape —
    every key receipt.js reads is preserved) PLUS the seller/bank letterhead block and a
    canonical `document` view. None if the order doesn't exist."""
    order = session.execute(
        text('SELECT id, club_id, user_id, amount_minor, currency_code, settlement_mode, '
             'status, created_at FROM billing."order" WHERE id = :id'),
        {"id": str(order_id)},
    ).mappings().first()
    if not order:
        return None

    seller = resolve_seller(session, order["club_id"])
    bill_to = resolve_bill_to(session, order["club_id"], order["user_id"])

    lines = [
        {"description": r["description"], "qty": int(r["qty"] or 1),
         "amount_minor": int(r["amount_minor"] or 0)}
        for r in session.execute(
            text("SELECT description, qty, amount_minor FROM billing.order_line "
                 "WHERE order_id = :oid ORDER BY created_at"),
            {"oid": str(order_id)},
        ).mappings().all()
    ]

    payments: List[Dict[str, Any]] = []
    paid_minor = 0
    refunded_minor = 0
    for r in session.execute(
        text("SELECT provider, provider_payment_id, amount_minor, currency_code, direction, "
             "status, created_at FROM billing.payment WHERE order_id = :oid ORDER BY created_at"),
        {"oid": str(order_id)},
    ).mappings().all():
        amt = int(r["amount_minor"] or 0)
        if r["direction"] == "refund":
            refunded_minor += amt
        elif r["status"] == "succeeded":
            paid_minor += amt
        payments.append({
            "provider": r["provider"],
            "method": method_label(r["provider"]),
            "reference": r["provider_payment_id"],
            "amount_minor": amt,
            "currency": r["currency_code"],
            "direction": r["direction"],
            "status": r["status"],
            "created_at": _iso(r["created_at"]),
        })

    receipt_no = f"NP-{str(order['id']).replace('-', '')[:8].upper()}"
    return {
        # --- legacy keys (receipt.js + existing callers depend on these) ---
        "receipt_no": receipt_no,
        "order_id": str(order["id"]),
        "club_name": seller["name"],
        "issued_at": _iso(order["created_at"]),
        "payer_email": bill_to["email"],
        "currency": order["currency_code"],
        "settlement_mode": order["settlement_mode"],
        "status": order["status"],
        "lines": lines,
        "amount_minor": int(order["amount_minor"] or 0),
        "payments": payments,
        "paid_minor": paid_minor,
        "refunded_minor": refunded_minor,
        "net_minor": paid_minor - refunded_minor,
        # --- new: letterhead identity for a professional printout / PDF ---
        "seller": seller,
        "bill_to": bill_to,
    }


# ---------------------------------------------------------------------------
# INVOICE numbering — gapless per club, allocated atomically inside the caller's txn
# ---------------------------------------------------------------------------

def receipt_to_document(r: Dict[str, Any]) -> Dict[str, Any]:
    """Adapt a build_receipt() dict to the canonical document shape so the ONE PDF renderer
    handles receipts and invoices identically. A receipt is a proof-of-payment for one order."""
    if not r:
        return r
    amount = int(r.get("amount_minor") or 0)
    net = int(r.get("net_minor") or 0)
    is_open = (r.get("status") == "open")
    outstanding = amount if is_open else 0
    if r.get("status") in ("refunded",) or int(r.get("refunded_minor") or 0) > 0:
        status_label = "Refunded" if int(r.get("refunded_minor") or 0) >= net else "Partially refunded"
    elif outstanding <= 0:
        status_label = "Paid"
    else:
        status_label = "Unpaid"
    return {
        "doc_type": "receipt",
        "title": "Receipt",
        "number": r.get("receipt_no"),
        "receipt_no": r.get("receipt_no"),
        "status_label": status_label,
        "is_paid": outstanding <= 0,
        "currency": r.get("currency"),
        "issued_at": r.get("issued_at"),
        "due_date": None,
        "period_label": None,
        "seller": r.get("seller"),
        "bill_to": r.get("bill_to"),
        "lines": r.get("lines") or [],
        "total_minor": amount,
        "paid_minor": net,
        "outstanding_minor": max(0, outstanding),
        "refunded_minor": int(r.get("refunded_minor") or 0),
        "payments": r.get("payments") or [],
        "notes": (r.get("seller") or {}).get("footer") if isinstance(r.get("seller"), dict) else None,
    }


def _next_invoice_number(session, club_id) -> str:
    """Allocate the next gapless invoice number for the club (prefix + zero-padded seq).
    Atomic: increments club.billing_profile.next_invoice_seq under a row lock in the caller's
    transaction, so a rollback un-allocates it (no gaps). Creates a default profile row if
    the club has none yet."""
    session.execute(
        text("INSERT INTO club.billing_profile (club_id) VALUES (:c) "
             "ON CONFLICT (club_id) DO NOTHING"),
        {"c": str(club_id)},
    )
    row = session.execute(
        text("UPDATE club.billing_profile "
             "SET next_invoice_seq = next_invoice_seq + 1, updated_at = now() "
             "WHERE club_id = :c "
             "RETURNING invoice_prefix, next_invoice_seq - 1 AS seq"),
        {"c": str(club_id)},
    ).mappings().first()
    prefix = (row["invoice_prefix"] if row else None) or "INV-"
    seq = int(row["seq"]) if row else 1
    return f"{prefix}{seq:06d}"


def active_invoice_order_ids(session, *, club_id, user_id) -> set:
    """Order ids for this client already covered by an ACTIVE (issued, non-void) invoice —
    so a statement/month-end issue never double-invoices a debt already on a live invoice."""
    rows = session.execute(
        text("SELECT DISTINCT il.order_id "
             "FROM billing.invoice_line il JOIN billing.invoice i ON i.id = il.invoice_id "
             "WHERE i.club_id = :c AND i.user_id = :u AND i.status = 'issued' "
             "AND il.order_id IS NOT NULL"),
        {"c": str(club_id), "u": str(user_id)},
    ).scalars().all()
    return {str(r) for r in rows if r}


# ---------------------------------------------------------------------------
# ISSUE — create an invoice DOCUMENT over a set of orders
# ---------------------------------------------------------------------------

def issue_invoice(session, *, club_id, user_id, order_ids, kind="statement",
                  period_label=None, due_date=None, created_by_user_id=None,
                  notes=None, skip_already_invoiced=None):
    """Create ONE issued invoice document snapshotting the given orders' line items.

    - kind='statement' → covers existing OPEN orders (month-end / intra-month outstanding).
      By default it SKIPS any order already on an active invoice (no double-issue).
    - kind='adhoc' → an admin ad-hoc bill; `order_ids` is the freshly-created order(s).

    The orders are NOT modified (they remain the live debt). Returns
    {ok, invoice_id, invoice_number, total_minor, order_ids} or {ok:False, error}."""
    order_ids = [str(o) for o in (order_ids or [])]
    if not order_ids:
        return {"ok": False, "error": "NO_ORDERS"}

    if skip_already_invoiced is None:
        skip_already_invoiced = (kind == "statement")
    if skip_already_invoiced:
        already = active_invoice_order_ids(session, club_id=club_id, user_id=user_id)
        order_ids = [o for o in order_ids if o not in already]
        if not order_ids:
            return {"ok": False, "error": "ALL_ALREADY_INVOICED"}

    # Snapshot line items from the covered orders' order_lines (full itemisation), club-scoped.
    rows = session.execute(
        text('SELECT ol.order_id, ol.description, ol.qty, ol.amount_minor, o.currency_code '
             'FROM billing.order_line ol JOIN billing."order" o ON o.id = ol.order_id '
             'WHERE ol.order_id = ANY(:ids) AND o.club_id = :c '
             'ORDER BY o.created_at, ol.created_at'),
        {"ids": order_ids, "c": str(club_id)},
    ).mappings().all()
    if not rows:
        return {"ok": False, "error": "NO_LINES"}

    currency = rows[0]["currency_code"]
    total = sum(int(r["amount_minor"] or 0) for r in rows)

    seller = resolve_seller(session, club_id)
    bill_to = resolve_bill_to(session, club_id, user_id)
    number = _next_invoice_number(session, club_id)

    import json
    inv = session.execute(
        text("INSERT INTO billing.invoice "
             "(club_id, invoice_number, user_id, kind, currency_code, total_minor, "
             " due_date, period_label, bill_to, seller, notes, created_by_user_id) "
             "VALUES (:c, :num, :u, :kind, :cur, :total, CAST(:due AS date), :period, "
             " CAST(:bill_to AS jsonb), CAST(:seller AS jsonb), :notes, :by) "
             "RETURNING id"),
        {"c": str(club_id), "num": number, "u": str(user_id), "kind": kind,
         "cur": currency, "total": total, "due": due_date, "period": period_label,
         "bill_to": json.dumps(bill_to), "seller": json.dumps(seller),
         "notes": notes or seller.get("terms"), "by": (str(created_by_user_id) if created_by_user_id else None)},
    ).scalar()

    for r in rows:
        session.execute(
            text("INSERT INTO billing.invoice_line "
                 "(invoice_id, club_id, order_id, description, qty, amount_minor) "
                 "VALUES (:i, :c, :o, :d, :q, :a)"),
            {"i": str(inv), "c": str(club_id), "o": str(r["order_id"]),
             "d": r["description"], "q": int(r["qty"] or 1), "a": int(r["amount_minor"] or 0)},
        )

    return {"ok": True, "invoice_id": str(inv), "invoice_number": number,
            "total_minor": total, "order_ids": order_ids}


def open_order_ids(session, *, club_id, user_id) -> List[str]:
    """A client's currently OWED orders (status 'open', not part of a settlement wrapper),
    oldest first — the debts a statement/month-end invoice should cover."""
    rows = session.execute(
        text('SELECT id FROM billing."order" '
             "WHERE club_id = :c AND user_id = :u AND status = 'open' "
             "AND settled_by_order_id IS NULL "
             "ORDER BY created_at"),
        {"c": str(club_id), "u": str(user_id)},
    ).scalars().all()
    return [str(r) for r in rows]


def issue_statement_invoice(session, *, club_id, user_id, period_label=None, due_date=None,
                            created_by_user_id=None):
    """Consolidate a client's current OPEN orders into ONE statement invoice document
    (month-end auto, or intra-month on demand). Orders already on an active invoice are
    skipped (no double-issue). Returns issue_invoice() result, or {ok:False,error:'NOTHING_OWED'}
    when there is nothing new to invoice."""
    oids = open_order_ids(session, club_id=club_id, user_id=user_id)
    if not oids:
        return {"ok": False, "error": "NOTHING_OWED"}
    return issue_invoice(session, club_id=club_id, user_id=user_id, order_ids=oids,
                         kind="statement", period_label=period_label, due_date=due_date,
                         created_by_user_id=created_by_user_id, skip_already_invoiced=True)


def mark_invoice_paid(session, *, club_id, invoice_id, provider="eft", reference=None,
                      recorded_by=None) -> Dict[str, Any]:
    """Mark an invoice PAID by an off-platform method (EFT / cash / card-at-desk). Settles every
    STILL-OPEN order the invoice covers through the desk-payment core (record_desk_payment) — so
    each writes a billing.payment row, flips its order to 'paid', and emits payment_succeeded
    (→ the client's receipt). The invoice then derives 'Paid' automatically (paid-status is live).
    Idempotent: a per-order stable reference means a double-click is a no-op. Body provider is
    normalised to cash/card_at_desk/eft. Returns {ok, settled, invoice_id} or {ok:False,error}."""
    from billing.orders import record_desk_payment
    inv = session.execute(
        text("SELECT user_id FROM billing.invoice WHERE id = :i AND club_id = :c AND status = 'issued'"),
        {"i": str(invoice_id), "c": str(club_id)},
    ).mappings().first()
    if not inv:
        return {"ok": False, "error": "NOT_FOUND"}

    open_orders = session.execute(
        text('SELECT DISTINCT o.id, o.amount_minor, o.currency_code '
             'FROM billing.invoice_line il JOIN billing."order" o ON o.id = il.order_id '
             "WHERE il.invoice_id = :i AND o.status = 'open'"),
        {"i": str(invoice_id)},
    ).mappings().all()
    if not open_orders:
        return {"ok": True, "settled": 0, "invoice_id": str(invoice_id), "note": "already_paid"}

    settled = 0
    for o in open_orders:
        # A stable, per-order reference: keeps the human EFT ref on the receipt while avoiding the
        # (provider, provider_payment_id) unique collision across a multi-order invoice + making a
        # re-click idempotent.
        pid = (f"{reference}:{str(o['id'])[:8]}" if reference else None)
        res = record_desk_payment(
            session, club_id=club_id, order_id=str(o["id"]),
            amount_minor=int(o["amount_minor"] or 0), provider=provider,
            currency_code=o["currency_code"], provider_payment_id=pid,
            user_id=inv["user_id"], recorded_by=recorded_by)
        if not (isinstance(res, dict) and res.get("error")):
            settled += 1
    return {"ok": True, "settled": settled, "invoice_id": str(invoice_id)}


def void_invoice(session, *, club_id, invoice_id) -> Dict[str, Any]:
    """Mark an invoice document void (it stops covering its orders → they can be re-invoiced).
    Does NOT touch the orders/debt. Only an 'issued' invoice can be voided."""
    n = session.execute(
        text("UPDATE billing.invoice SET status = 'void' "
             "WHERE id = :i AND club_id = :c AND status = 'issued'"),
        {"i": str(invoice_id), "c": str(club_id)},
    ).rowcount
    return {"ok": bool(n)} if n else {"ok": False, "error": "NOT_ISSUED"}


# ---------------------------------------------------------------------------
# BUILD the canonical invoice document (frozen lines + LIVE paid/outstanding)
# ---------------------------------------------------------------------------

def build_invoice_document(session, *, invoice_id, club_id=None) -> Optional[Dict[str, Any]]:
    """Assemble the full document dict for an issued invoice: frozen seller/bill-to/lines
    snapshot + paid/outstanding DERIVED LIVE from the referenced orders. None if not found."""
    q = ('SELECT id, club_id, invoice_number, user_id, kind, status, currency_code, '
         'total_minor, issued_at, due_date, period_label, bill_to, seller, notes '
         'FROM billing.invoice WHERE id = :i')
    params = {"i": str(invoice_id)}
    if club_id is not None:
        q += " AND club_id = :c"
        params["c"] = str(club_id)
    inv = session.execute(text(q), params).mappings().first()
    if not inv:
        return None

    lines = []
    order_ids = []
    for r in session.execute(
        text("SELECT order_id, description, qty, amount_minor FROM billing.invoice_line "
             "WHERE invoice_id = :i ORDER BY created_at"),
        {"i": str(invoice_id)},
    ).mappings().all():
        lines.append({"description": r["description"], "qty": int(r["qty"] or 1),
                      "amount_minor": int(r["amount_minor"] or 0),
                      "order_id": (str(r["order_id"]) if r["order_id"] else None)})
        if r["order_id"]:
            order_ids.append(str(r["order_id"]))

    # LIVE paid/outstanding derivation over the referenced orders (deduped).
    paid_minor = 0
    outstanding_minor = 0
    payments: List[Dict[str, Any]] = []
    for oid in dict.fromkeys(order_ids):          # preserve order, dedupe
        st = _order_state(session, oid)
        if not st:
            continue
        paid_minor += st["net_minor"]
        outstanding_minor += st["owed_minor"]
        for r in session.execute(
            text("SELECT provider, provider_payment_id, amount_minor, currency_code, "
                 "direction, status, created_at FROM billing.payment WHERE order_id = :o "
                 "ORDER BY created_at"),
            {"o": oid},
        ).mappings().all():
            payments.append({
                "provider": r["provider"], "method": method_label(r["provider"]),
                "reference": r["provider_payment_id"], "amount_minor": int(r["amount_minor"] or 0),
                "currency": r["currency_code"], "direction": r["direction"],
                "status": r["status"], "created_at": _iso(r["created_at"]),
            })

    if inv["status"] == "void":
        status_label = "Void"
    elif outstanding_minor <= 0:
        status_label = "Paid"
    elif paid_minor > 0:
        status_label = "Partially paid"
    else:
        status_label = "Unpaid"
    is_paid = (inv["status"] != "void") and outstanding_minor <= 0

    seller = inv["seller"] or resolve_seller(session, inv["club_id"])
    bill_to = inv["bill_to"] or resolve_bill_to(session, inv["club_id"], inv["user_id"])

    return {
        "doc_type": "invoice",
        "title": "Invoice",                         # not VAT-registered → "Invoice" (never "Tax Invoice")
        "invoice_id": str(inv["id"]),
        "number": inv["invoice_number"],
        "receipt_no": inv["invoice_number"],        # convenience alias for shared renderers
        "kind": inv["kind"],
        "doc_status": inv["status"],                # issued|void
        "status_label": status_label,
        "is_paid": is_paid,
        "currency": inv["currency_code"],
        "issued_at": _iso(inv["issued_at"]),
        "due_date": _iso(inv["due_date"]),
        "period_label": inv["period_label"],
        "seller": seller,
        "bill_to": bill_to,
        "lines": lines,
        "total_minor": int(inv["total_minor"] or 0),
        "paid_minor": paid_minor,
        "outstanding_minor": max(0, outstanding_minor),
        "payments": payments,
        "notes": inv["notes"] or (seller.get("footer") if isinstance(seller, dict) else None),
    }

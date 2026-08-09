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

def _enriched_line_descriptions(session, *, club_id, order_ids) -> Dict[str, str]:
    """Human line descriptions for invoice lines, keyed by order_id.

    A booking order_line carries only the bare `booking_type` ('court'/'lesson'/'class') as its
    description — fine internally, but on a customer invoice it reads "court  R180" with no date,
    service or coach. This resolves each booking/class order to a real one-liner:
      lesson → "25 Jul · 60 min Private with Allon Rock"
      court  → "25 Jul · 60 min Hardcourt Hire — KCC - Hard Court 3"   (court, NOT a coach)
      class  → "25 Jul · Cardio Tennis"
    Dates render in the CLUB's timezone (a month-end invoice at 08:00 SAST must not print the UTC
    day). Only bare-booking lines are enriched by the caller; equipment add-ons, fees, memberships
    and packs keep their own descriptions. Guarded end-to-end — a resolve failure just leaves the
    original description, never a broken invoice."""
    out: Dict[str, str] = {}
    if not order_ids:
        return out
    tz = (session.execute(text("SELECT COALESCE(timezone,'Africa/Johannesburg') FROM club.club WHERE id = :c"),
                          {"c": str(club_id)}).scalar()) or "Africa/Johannesburg"
    try:
        # Court / lesson bookings.
        for r in session.execute(
            text("SELECT DISTINCT ON (ol.order_id) ol.order_id, b.booking_type, "
                 "  to_char((b.starts_at AT TIME ZONE :tz), 'DD Mon') AS d, "
                 "  GREATEST(1, round(extract(epoch FROM (b.ends_at - b.starts_at)) / 60))::int AS mins, "
                 "  p.name AS service, r.name AS resource_name, "
                 "  NULLIF(trim(coalesce(cu.first_name,'') || ' ' || coalesce(cu.surname,'')), '') AS coach "
                 "FROM billing.order_line ol "
                 "JOIN diary.booking b ON b.id = ol.booking_id "
                 "LEFT JOIN billing.product p ON p.id = b.product_id "
                 "LEFT JOIN diary.resource r ON r.id = b.resource_id "
                 'LEFT JOIN iam."user" cu ON cu.id = b.coach_user_id '
                 "WHERE ol.order_id = ANY(:ids) AND ol.booking_id IS NOT NULL "
                 "ORDER BY ol.order_id, b.starts_at"),
            {"ids": [str(o) for o in order_ids], "tz": tz},
        ).mappings():
            d, mins = r["d"], r["mins"]
            if r["booking_type"] == "lesson":
                svc = r["service"] or "Lesson"
                txt = f"{d} · {mins} min {svc}" + (f" with {r['coach']}" if r["coach"] else "")
            else:  # court
                svc = r["service"] or "Court hire"
                txt = f"{d} · {mins} min {svc}" + (f" — {r['resource_name']}" if r["resource_name"] else "")
            out[str(r["order_id"])] = txt

        # Class enrolments (no booking row; the order hangs off diary.enrolment).
        for r in session.execute(
            text("SELECT DISTINCT ON (e.order_id) e.order_id, "
                 "  to_char((cs.starts_at AT TIME ZONE :tz), 'DD Mon') AS d, "
                 "  COALESCE(p.name, r.name) AS service "
                 "FROM diary.enrolment e "
                 "JOIN diary.class_session cs ON cs.id = e.class_session_id "
                 "LEFT JOIN diary.resource r ON r.id = cs.resource_id "
                 "LEFT JOIN billing.product p ON p.id = r.product_id "
                 "WHERE e.order_id = ANY(:ids) "
                 "ORDER BY e.order_id, cs.starts_at"),
            {"ids": [str(o) for o in order_ids], "tz": tz},
        ).mappings():
            if str(r["order_id"]) not in out:
                out[str(r["order_id"])] = f"{r['d']} · {r['service'] or 'Class'}"
    except Exception:
        log.info("invoice line enrichment skipped club=%s", club_id, exc_info=False)
    return out


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
    rows = [dict(r) for r in session.execute(
        text('SELECT ol.order_id, ol.description, ol.qty, ol.amount_minor, o.currency_code '
             'FROM billing.order_line ol JOIN billing."order" o ON o.id = ol.order_id '
             'WHERE ol.order_id = ANY(:ids) AND o.club_id = :c '
             'ORDER BY o.created_at, ol.created_at'),
        {"ids": order_ids, "c": str(club_id)},
    ).mappings().all()]

    # EVERY covered order MUST contribute a line. An owed order with no order_line rows (a data gap)
    # would otherwise be silently dropped: its debt vanishes from the invoice TOTAL (under-billing),
    # and if it were the only order the whole issue failed NO_LINES and the client got a bare
    # "pay online" reminder with no PDF instead of an invoice. Synthesise a single line from the
    # order's own amount for any covered order the snapshot missed, so the invoice always ties out.
    seen = {str(r["order_id"]) for r in rows}
    missing = [o for o in order_ids if o not in seen]
    if missing:
        for o in session.execute(
            text('SELECT id, amount_minor, currency_code FROM billing."order" '
                 "WHERE id = ANY(:ids) AND club_id = :c AND amount_minor > 0"),
            {"ids": missing, "c": str(club_id)},
        ).mappings():
            rows.append({"order_id": o["id"], "description": "Charge",
                         "qty": 1, "amount_minor": int(o["amount_minor"] or 0),
                         "currency_code": o["currency_code"]})
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

    # Enrich the bare booking lines ('court'/'lesson'/'class') into a dated, named description; fees,
    # add-ons, memberships and packs already carry a real description and are left untouched.
    enriched = _enriched_line_descriptions(session, club_id=club_id, order_ids=order_ids)
    for r in rows:
        base = r["description"]
        # Enrich a bare booking line ('court'/'lesson'/'class') or a synthesised 'Charge' line;
        # everything with a real description (fees, packs, memberships, add-ons) is left as-is.
        desc = enriched.get(str(r["order_id"])) if base in ("court", "lesson", "class", "Charge") else base
        if not desc:
            desc = (base or "").strip().capitalize() or "Charge"
        session.execute(
            text("INSERT INTO billing.invoice_line "
                 "(invoice_id, club_id, order_id, description, qty, amount_minor) "
                 "VALUES (:i, :c, :o, :d, :q, :a)"),
            {"i": str(inv), "c": str(club_id), "o": str(r["order_id"]),
             "d": desc, "q": int(r["qty"] or 1), "a": int(r["amount_minor"] or 0)},
        )

    return {"ok": True, "invoice_id": str(inv), "invoice_number": number,
            "total_minor": total, "order_ids": order_ids}


# WHEN A CHARGE WAS DELIVERED — the single source of truth, in SQL, as a scalar expression over an
# order aliased `o`. Everything that asks "which month does this belong to?" must use THIS, or the
# invoice, the month-end sweep and the client's own activity summary will each answer differently.
#
# Precedence: the booking's session → the class session → an explicit service_date (an ad-hoc
# catch-up charge, e.g. a July bill for April coaching) → when the charge was raised.
# `order_line.booking_id` (not booking.order_id) is deliberate: a squad partner's own head hangs off
# the LINE, so each player resolves to the session they actually played.
DELIVERED_AT_SQL = """
COALESCE(
  (SELECT min(b.starts_at) FROM billing.order_line ol
     JOIN diary.booking b ON b.id = ol.booking_id WHERE ol.order_id = o.id),
  (SELECT min(cs.starts_at) FROM diary.enrolment e
     JOIN diary.class_session cs ON cs.id = e.class_session_id WHERE e.order_id = o.id),
  o.service_date::timestamptz,
  o.created_at
)"""


def open_order_ids(session, *, club_id, user_id, period_label=None) -> List[str]:
    """A client's currently OWED orders (status 'open', not part of a settlement wrapper), oldest
    first — the debts a statement/month-end invoice should cover.

    `period_label` ('YYYY-MM') scopes to the month the service was DELIVERED, which is what makes an
    invoice closeable. Without it this returned every open order regardless of age, so a "month-end"
    invoice was a photograph of everything owed at the instant it ran: lessons played after the sweep
    were missing from the document while the client's live balance already included them (the invoice
    "not balancing"), and an unpaid June debt rode onto the July invoice, so no month was ever closed
    and "what is still outstanding for July" had no answer. Omitting it preserves the old behaviour
    exactly — the intra-month "invoice the outstanding balance" action still wants everything."""
    where = ("AND to_char(({d} AT TIME ZONE COALESCE((SELECT timezone FROM club.club WHERE id = :c),"
             "'Africa/Johannesburg')), 'YYYY-MM') = :period").format(d=DELIVERED_AT_SQL) \
        if period_label else ""
    params = {"c": str(club_id), "u": str(user_id)}
    if period_label:
        params["period"] = period_label
    rows = session.execute(
        text('SELECT o.id FROM billing."order" o '
             "WHERE o.club_id = :c AND o.user_id = :u AND o.status = 'open' "
             "AND o.settled_by_order_id IS NULL AND o.covered_order_ids IS NULL "
             "AND o.amount_minor > 0 "                    # a R0 covered/free order is not a debt
             f"{where} "
             "ORDER BY o.created_at"),
        params,
    ).scalars().all()
    return [str(r) for r in rows]


def brought_forward_minor(session, *, club_id, user_id, period_label) -> int:
    """What this client still owed from BEFORE `period_label`, at this moment.

    Frozen onto the invoice at issue and shown as "Balance brought forward" — display only. It is
    deliberately NOT an invoice_line and NOT part of total_minor: those older orders are already
    invoiced on their own month's document, so re-billing them here would issue the same debt twice,
    and a line carrying no order_id could never be settled by mark_invoice_paid."""
    return int(session.execute(
        text('SELECT COALESCE(SUM(o.amount_minor), 0) FROM billing."order" o '
             "WHERE o.club_id = :c AND o.user_id = :u AND o.status = 'open' "
             "AND o.settled_by_order_id IS NULL AND o.covered_order_ids IS NULL "
             "AND o.amount_minor > 0 "
             f"AND to_char(({DELIVERED_AT_SQL} AT TIME ZONE "
             "     COALESCE((SELECT timezone FROM club.club WHERE id = :c),'Africa/Johannesburg')),"
             "     'YYYY-MM') < :period"),
        {"c": str(club_id), "u": str(user_id), "period": period_label},
    ).scalar() or 0)


def latest_active_invoice_with_open_debt(session, *, club_id, user_id) -> Optional[str]:
    """The client's most recent ISSUED invoice that still covers at least one OPEN order — i.e. an
    invoice they've been sent but not yet paid. Month-end uses this: when a client's whole balance is
    ALREADY on an active invoice (so there's nothing new to issue), re-send THAT invoice's email —
    with its PDF and pay-link — instead of a bare "pay online" reminder with no document. Returns the
    invoice id or None."""
    return session.execute(
        text("SELECT i.id FROM billing.invoice i "
             "WHERE i.club_id = :c AND i.user_id = :u AND i.status = 'issued' "
             "AND EXISTS (SELECT 1 FROM billing.invoice_line il "
             '            JOIN billing."order" o ON o.id = il.order_id '
             "            WHERE il.invoice_id = i.id AND o.status = 'open') "
             "ORDER BY i.created_at DESC LIMIT 1"),
        {"c": str(club_id), "u": str(user_id)},
    ).scalar()


def issue_statement_invoice(session, *, club_id, user_id, period_label=None, due_date=None,
                            created_by_user_id=None, scope_to_period=False):
    """Consolidate a client's OPEN orders into ONE statement invoice document.

    `scope_to_period=True` (the month-end sweep) bills ONLY what was DELIVERED in `period_label`, and
    freezes the earlier balance as "brought forward" — so each month's invoice stays that month's,
    permanently. `scope_to_period=False` (the intra-month "invoice the outstanding balance" action)
    keeps the original behaviour: bill everything currently owed, because that is exactly what an
    owner asking for a balance invoice mid-month means.

    Orders already on an active invoice are skipped (no double-issue). Returns issue_invoice()'s
    result, or {ok:False, error:'NOTHING_OWED'}."""
    oids = open_order_ids(session, club_id=club_id, user_id=user_id,
                          period_label=(period_label if scope_to_period else None))
    if not oids:
        return {"ok": False, "error": "NOTHING_OWED"}
    res = issue_invoice(session, club_id=club_id, user_id=user_id, order_ids=oids,
                        kind="statement", period_label=period_label, due_date=due_date,
                        created_by_user_id=created_by_user_id, skip_already_invoiced=True)
    if res.get("ok") and scope_to_period and period_label:
        bf = brought_forward_minor(session, club_id=club_id, user_id=user_id,
                                   period_label=period_label)
        if bf:
            session.execute(
                text("UPDATE billing.invoice SET brought_forward_minor = :bf WHERE id = :i"),
                {"bf": bf, "i": res["invoice_id"]})
            res["brought_forward_minor"] = bf
    return res


def mark_invoice_paid(session, *, club_id, invoice_id, provider="eft", reference=None,
                      recorded_by=None, amount_minor=None) -> Dict[str, Any]:
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

    # OLDEST FIRST, by the date the service was DELIVERED — a part payment clears the oldest debt,
    # the way any statement is settled. created_at alone cannot order these: several orders are
    # routinely raised in ONE transaction, where now() is identical for all of them, so the tie-break
    # has to be deterministic (the same trap that made the refund path pick the abandoned checkout).
    open_orders = session.execute(
        text('SELECT DISTINCT o.id, o.amount_minor, o.currency_code, '
             f'       {DELIVERED_AT_SQL} AS delivered_at, o.created_at '
             'FROM billing.invoice_line il JOIN billing."order" o ON o.id = il.order_id '
             "WHERE il.invoice_id = :i AND o.status = 'open' "
             "ORDER BY delivered_at, o.created_at, o.id"),
        {"i": str(invoice_id)},
    ).mappings().all()
    if not open_orders:
        return {"ok": True, "settled": 0, "invoice_id": str(invoice_id), "note": "already_paid"}

    # A PART payment settles whole lines and leaves the rest owed — so the invoice derives
    # "Partially paid" and the client's statement still shows exactly what is left. An order is
    # never part-settled: one debt = one order, settled once, and record_desk_payment refuses a
    # short amount for that reason. Anything that cannot fill the next line is returned as
    # `unallocated_minor` for the admin to place, rather than silently marking a bill paid.
    budget = None if amount_minor is None else max(0, int(amount_minor))
    if budget is not None:
        total_open = sum(int(o["amount_minor"] or 0) for o in open_orders)
        if budget >= total_open:
            budget = None                        # covers everything → a full settlement

    settled = 0
    collected = 0
    currency = None
    for o in open_orders:
        due = int(o["amount_minor"] or 0)
        if budget is not None:
            if due > budget:
                # STOP, never skip ahead to a smaller line. Allocation is oldest-first: paying R800
                # against R500/R300/R200 must clear the R500 and R300 and leave the R200 owed. A
                # heuristic that hunts for a line the remainder happens to fit would settle a NEWER
                # lesson and leave an older one open, which is not how anyone reads a statement.
                break
            budget -= due
        # A stable, per-order reference: keeps the human EFT ref on the receipt while avoiding the
        # (provider, provider_payment_id) unique collision across a multi-order invoice + making a
        # re-click idempotent.
        pid = (f"{reference}:{str(o['id'])[:8]}" if reference else None)
        res = record_desk_payment(
            session, club_id=club_id, order_id=str(o["id"]),
            amount_minor=int(o["amount_minor"] or 0), provider=provider,
            currency_code=o["currency_code"], provider_payment_id=pid,
            user_id=inv["user_id"], recorded_by=recorded_by,
            # ONE receipt for ONE payment. Each settled order still emits payment_succeeded (the
            # usage feed, Klaviyo and the offline-conversion recorder all need it), but naming the
            # batch tells notifications.deliver not to send that order its own email. Settling a
            # 12-lesson invoice used to send the client TWELVE "Booking confirmed" emails and make
            # the owner receipt each payment by hand.
            settlement_batch=str(invoice_id))
        if not (isinstance(res, dict) and res.get("error")):
            settled += 1
            collected += int(o["amount_minor"] or 0)
            currency = currency or o["currency_code"]

    # The ONE receipt, naming what it covers. Emitted only when money actually moved, so a
    # double-click (every order already paid, settled == 0) re-sends nothing.
    if settled:
        try:
            from marketing_crm.tracking import emit
            emit("invoice_paid", {
                "club_id": str(club_id), "user_id": str(inv["user_id"]),
                "invoice_id": str(invoice_id), "amount_minor": collected,
                "currency": currency or "ZAR", "lines": settled,
                "provider": provider, "reference": reference})
        except Exception:
            log.info("invoice_paid emit skipped invoice=%s", invoice_id)
    still_owed = sum(int(o["amount_minor"] or 0) for o in open_orders) - collected
    return {"ok": True, "settled": settled, "collected_minor": collected,
            "unallocated_minor": (int(budget) if budget else 0),
            "outstanding_minor": max(0, still_owed),
            "fully_paid": still_owed <= 0,
            "invoice_id": str(invoice_id)}


def void_invoice(session, *, club_id, invoice_id, cascade=True, reason=None) -> Dict[str, Any]:
    """Void an invoice. By DEFAULT this cancels the charges too — "void" means cancel.

    It used to void the DOCUMENT only and deliberately leave every charge owed, because an invoice
    renders over live orders and is never a second debt store. Technically right, and it read as a
    bug to the person using it: he voided an invoice for R12,680, saw the balance unchanged, and had
    to be told the debt lives somewhere else. Two meanings of one word on the same screen.

    `cascade=False` keeps the old behaviour, and it is not decoration — it is the ONLY way to void a
    document you intend to RE-ISSUE (wrong bill-to, wrong period, a missing line). Cascading there
    would destroy the charges, and nothing un-voids an order: the debt would be gone for good.

    A PAID charge is never touched — void_order no-ops on one, so voiding a part-paid invoice
    cancels what is still owed and leaves the money that arrived alone. Returns
    {ok, charges_voided, amount_minor}."""
    n = session.execute(
        text("UPDATE billing.invoice SET status = 'void' "
             "WHERE id = :i AND club_id = :c AND status = 'issued'"),
        {"i": str(invoice_id), "c": str(club_id)},
    ).rowcount
    if not n:
        return {"ok": False, "error": "NOT_ISSUED"}
    voided, amount = 0, 0
    if cascade:
        from billing.statement import void_order
        rows = session.execute(
            text('SELECT DISTINCT o.id, o.amount_minor FROM billing.invoice_line il '
                 '  JOIN billing."order" o ON o.id = il.order_id '
                 " WHERE il.invoice_id = :i AND o.status IN ('open','awaiting_payment')"),
            {"i": str(invoice_id)},
        ).mappings().all()
        for r in rows:
            if void_order(session, club_id=club_id, order_id=str(r["id"]),
                          reason=(reason or "invoice voided")).get("ok"):
                voided += 1
                amount += int(r["amount_minor"] or 0)
    return {"ok": True, "charges_voided": voided, "amount_minor": amount}


# ---------------------------------------------------------------------------
# BUILD the canonical invoice document (frozen lines + LIVE paid/outstanding)
# ---------------------------------------------------------------------------

def list_invoices(session, *, club_id, user_id, limit=100) -> List[Dict[str, Any]]:
    """A client's invoice documents, newest first, with live outstanding derived from the
    covered orders (one aggregate query). For lists/records — the full doc is build_invoice_document."""
    rows = session.execute(
        text('SELECT i.id, i.invoice_number, i.kind, i.status, i.issued_at, i.due_date, '
             '       i.total_minor, i.currency_code, '
             '       COALESCE(SUM(CASE WHEN o.status = \'open\' THEN o.amount_minor ELSE 0 END),0) AS outstanding '
             'FROM billing.invoice i '
             'LEFT JOIN (SELECT DISTINCT invoice_id, order_id FROM billing.invoice_line '
             '           WHERE order_id IS NOT NULL) il ON il.invoice_id = i.id '
             'LEFT JOIN billing."order" o ON o.id = il.order_id '
             'WHERE i.club_id = :c AND i.user_id = :u '
             'GROUP BY i.id '
             'ORDER BY i.issued_at DESC LIMIT :lim'),
        {"c": str(club_id), "u": str(user_id), "lim": int(limit)},
    ).mappings().all()
    out = []
    for r in rows:
        outstanding = int(r["outstanding"] or 0)
        if r["status"] == "void":
            label = "Void"
        elif outstanding <= 0:
            label = "Paid"
        elif outstanding < int(r["total_minor"] or 0):
            label = "Partially paid"
        else:
            label = "Unpaid"
        out.append({
            "invoice_id": str(r["id"]), "number": r["invoice_number"], "kind": r["kind"],
            "doc_status": r["status"], "issued_at": _iso(r["issued_at"]), "due_date": _iso(r["due_date"]),
            "total_minor": int(r["total_minor"] or 0), "currency": r["currency_code"],
            "outstanding_minor": outstanding, "is_paid": (r["status"] != "void" and outstanding <= 0),
            "status_label": label,
        })
    return out


def build_invoice_document(session, *, invoice_id, club_id=None) -> Optional[Dict[str, Any]]:
    """Assemble the full document dict for an issued invoice: frozen seller/bill-to/lines
    snapshot + paid/outstanding DERIVED LIVE from the referenced orders. None if not found."""
    q = ('SELECT id, club_id, invoice_number, user_id, kind, status, currency_code, '
         'total_minor, issued_at, due_date, period_label, bill_to, seller, notes, '
         'COALESCE(brought_forward_minor,0) AS brought_forward_minor '
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
        st_line = _order_state(session, r["order_id"]) if r["order_id"] else None
        # WHAT THIS LINE IS WORTH NOW. Line amounts FREEZE at issue (an invoice is an immutable
        # document) while paid/outstanding derive LIVE — so discounting or writing off a charge
        # AFTER the invoice went out left the document contradicting itself: total R600, paid R0,
        # outstanding R500, and nothing on the page explaining the missing R100. A voided or
        # written-off line is worth nothing; anything else is worth the order's CURRENT amount.
        frozen = int(r["amount_minor"] or 0)
        if st_line is None:
            live = frozen
        elif st_line["status"] in ("void", "written_off"):
            live = 0
        else:
            live = int(st_line["amount_minor"] or 0)
        lines.append({"description": r["description"], "qty": int(r["qty"] or 1),
                      "amount_minor": frozen,
                      "amount_now_minor": live,
                      "adjustment_minor": frozen - live,
                      "order_id": (str(r["order_id"]) if r["order_id"] else None),
                      # LIVE per-line state. A part payment settles whole lines, so the document has
                      # to say WHICH ones — otherwise "Partially paid" is a number with no story,
                      # and a written-off or discounted line looks unpaid forever.
                      "state": (st_line or {}).get("status"),
                      "settled": bool(st_line and st_line["status"] == "paid"),
                      "written_off": bool(st_line and st_line["status"] == "written_off"),
                      "outstanding_minor": (st_line or {}).get("owed_minor", 0)})
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

    # The document must RECONCILE on its face: total − adjustments = paid + outstanding. Without
    # this an invoice discounted after issue reads as a R600 bill asking for R500, and a fully
    # written-off one reads "Paid" when nobody paid anything.
    adjustments_minor = sum(int(l.get("adjustment_minor") or 0) for l in lines)
    billed_now_minor = max(0, int(inv["total_minor"] or 0) - adjustments_minor)

    if inv["status"] == "void":
        status_label = "Void"
    elif adjustments_minor and billed_now_minor <= 0 and paid_minor <= 0:
        status_label = "Cancelled"
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
        # Arrears from EARLIER months, frozen at issue. Display only — deliberately not in
        # total_minor and not an invoice_line, because those debts are already invoiced on their own
        # month's document (see brought_forward_minor). `total_due_minor` is what the client should
        # actually pay to be square: this month's outstanding PLUS what was already owed.
        # Changed since issue — a discount, void or write-off applied AFTER the document went out.
        # Shown as its own line so the page adds up instead of quietly disagreeing with itself.
        "adjustments_minor": adjustments_minor,
        "billed_now_minor": billed_now_minor,
        "brought_forward_minor": int(inv["brought_forward_minor"] or 0),
        "total_due_minor": max(0, outstanding_minor) + int(inv["brought_forward_minor"] or 0),
        "payments": payments,
        "notes": inv["notes"] or (seller.get("footer") if isinstance(seller, dict) else None),
    }

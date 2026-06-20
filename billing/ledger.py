# billing/ledger.py — the monthly-tab account ledger (docs/05 §5 monthly_account).
#
# account_ledger is a running, append-only journal per (club_id, user_id): a CHARGE when a
# booking is settled on the monthly tab, a PAYMENT when the member settles (EFT/card later),
# an ADJUSTMENT for corrections. Every row stores balance_after_minor so the current balance
# is a single SELECT (and the history is auditable). Sign convention:
#     charge      -> amount_minor POSITIVE  (increases what the member owes)
#     payment     -> amount_minor NEGATIVE  (reduces the balance)
#     adjustment  -> signed (either direction)
#
# Plain-SQL repositories (SQLAlchemy Core text), every fn takes an explicit `session` and
# never commits — callers compose via db.session_scope() (1050 discipline). Never hard-delete.

from sqlalchemy import text


def current_balance_minor(session, *, club_id, user_id) -> int:
    """The member's current tab balance (last balance_after_minor, or 0 if no entries).
    Positive = owed to the club."""
    row = session.execute(
        text("""
            SELECT balance_after_minor
            FROM billing.account_ledger
            WHERE club_id = :club_id AND user_id = :user_id
            ORDER BY created_at DESC, id DESC
            LIMIT 1
        """),
        {"club_id": str(club_id), "user_id": str(user_id) if user_id else None},
    ).scalar_one_or_none()
    return int(row or 0)


def _post_entry(session, *, club_id, user_id, entry_type, amount_minor, order_id=None, note=None):
    """Append a ledger entry, computing balance_after = previous + amount_minor. Internal —
    use post_charge / post_payment / post_adjustment. Returns the new row as a dict."""
    prev = current_balance_minor(session, club_id=club_id, user_id=user_id)
    new_balance = prev + int(amount_minor)
    row = session.execute(
        text("""
            INSERT INTO billing.account_ledger
                (club_id, user_id, order_id, entry_type, amount_minor, balance_after_minor, note)
            VALUES (:club_id, :user_id, :order_id, :entry_type, :amount_minor, :balance_after, :note)
            RETURNING id, balance_after_minor
        """),
        {
            "club_id": str(club_id),
            "user_id": str(user_id) if user_id else None,
            "order_id": str(order_id) if order_id else None,
            "entry_type": entry_type,
            "amount_minor": int(amount_minor),
            "balance_after": new_balance,
            "note": note,
        },
    ).mappings().first()
    return dict(row)


def post_charge(session, *, club_id, user_id, amount_minor, order_id=None, note=None):
    """Add a CHARGE to the tab (member booked on monthly_account). amount_minor is the
    positive magnitude owed; stored positive."""
    return _post_entry(session, club_id=club_id, user_id=user_id, entry_type="charge",
                       amount_minor=abs(int(amount_minor)), order_id=order_id, note=note)


def post_payment(session, *, club_id, user_id, amount_minor, order_id=None, note=None):
    """Record a PAYMENT against the tab (member settled). amount_minor is the positive
    magnitude paid; stored NEGATIVE so the running balance drops."""
    return _post_entry(session, club_id=club_id, user_id=user_id, entry_type="payment",
                       amount_minor=-abs(int(amount_minor)), order_id=order_id, note=note)


def post_adjustment(session, *, club_id, user_id, amount_minor, order_id=None, note=None):
    """Record a signed ADJUSTMENT (correction/write-off/goodwill). Caller controls the sign."""
    return _post_entry(session, club_id=club_id, user_id=user_id, entry_type="adjustment",
                       amount_minor=int(amount_minor), order_id=order_id, note=note)


# ---------------------------------------------------------------------------
# Monthly statement builder (cron logic for /api/cron/monthly-invoice)
# ---------------------------------------------------------------------------

def build_statements(session, *, club_id, period_start=None, period_end=None):
    """Build a per-member statement for the club's outstanding tabs. Pure read + shape —
    the cron route (billing.routes) calls this, then (later) emails the statements.

    Returns a list of dicts: {user_id, opening_minor, charges_minor, payments_minor,
    closing_minor, lines:[ledger entries in the period]}. Members with a zero closing
    balance AND no activity in the period are omitted (nothing to send).

    period_start/period_end are ISO date strings (inclusive/exclusive); if omitted, the
    statement covers all-time up to now (the running balance is always correct regardless)."""
    params = {"club_id": str(club_id)}
    where = "club_id = :club_id"
    if period_start:
        where += " AND created_at >= :ps"
        params["ps"] = period_start
    if period_end:
        where += " AND created_at < :pe"
        params["pe"] = period_end

    # All entries in the period, oldest first, grouped by member in Python.
    rows = session.execute(
        text(f"""
            SELECT user_id, id, order_id, entry_type, amount_minor, balance_after_minor,
                   note, created_at
            FROM billing.account_ledger
            WHERE {where}
            ORDER BY user_id, created_at ASC, id ASC
        """),
        params,
    ).mappings().all()

    by_user = {}
    for r in rows:
        by_user.setdefault(str(r["user_id"]) if r["user_id"] else None, []).append(dict(r))

    statements = []
    for user_id, lines in by_user.items():
        charges = sum(l["amount_minor"] for l in lines if l["entry_type"] == "charge")
        payments = sum(-l["amount_minor"] for l in lines if l["entry_type"] == "payment")
        adjustments = sum(l["amount_minor"] for l in lines if l["entry_type"] == "adjustment")
        closing = lines[-1]["balance_after_minor"] if lines else 0
        # opening = closing - (net change across the period)
        net_change = charges - payments + adjustments
        opening = closing - net_change
        if closing == 0 and not lines:
            continue
        statements.append({
            "user_id": user_id,
            "opening_minor": int(opening),
            "charges_minor": int(charges),
            "payments_minor": int(payments),
            "adjustments_minor": int(adjustments),
            "closing_minor": int(closing),
            "line_count": len(lines),
            "lines": [
                {k: (v.isoformat() if hasattr(v, "isoformat") else
                     (str(v) if k in ("id", "order_id") else v))
                 for k, v in l.items()}
                for l in lines
            ],
        })
    return statements

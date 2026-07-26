"""resend_invoice — re-send a client's EXISTING statement invoice email (PDF + pay-link).

For when a client got the bare "pay online" month-end reminder instead of their actual invoice (e.g.
their balance was already on an issued invoice, so the sweep had nothing NEW to issue). This does NOT
mint a new invoice or a new number — it looks up the invoice already covering their open debt and
re-delivers that same document's email. Nothing is billed twice.

    python -m scripts.resend_invoice lisa.benporath@gmail.com

Delivery runs SYNCHRONOUSLY here (not through emit()'s daemon thread), so the email actually sends
before the process exits.
"""
import sys

from sqlalchemy import text

from db import session_scope
from billing import invoicing
from marketing_crm.notifications import deliver_for_event


def main(argv):
    if not argv:
        print("usage: python -m scripts.resend_invoice <email>")
        return 2
    email = argv[0].strip().lower()

    with session_scope() as s:
        row = s.execute(
            text(
                'SELECT u.id AS user_id, o.club_id, o.currency_code, '
                '       SUM(o.amount_minor) AS owed '
                'FROM billing."order" o '
                'JOIN iam."user" u ON u.id = o.user_id '
                "WHERE lower(u.email) = :e AND o.status = 'open' "
                '  AND o.settled_by_order_id IS NULL '
                'GROUP BY u.id, o.club_id, o.currency_code'
            ),
            {"e": email},
        ).mappings().first()
        if not row:
            print("No open balance for", email)
            return 1

        inv_id = invoicing.latest_active_invoice_with_open_debt(
            s, club_id=row["club_id"], user_id=row["user_id"]
        )
        if not inv_id:
            print("No active invoice covers the open debt — issue one from her transaction record")
            print("(client 360 -> the R-owed order -> New invoice), which will email the PDF.")
            return 1

        payload = {
            "club_id": str(row["club_id"]),
            "user_id": str(row["user_id"]),
            "invoice_id": str(inv_id),
            "amount_minor": int(row["owed"] or 0),
            "currency": row["currency_code"] or "ZAR",
            "email": email,
        }
        res = deliver_for_event(s, "invoice_issued", payload)
        print("Re-sent invoice", inv_id, "to", email, "-> result:", res)
        print("R{:.2f} owed. No new invoice number was minted.".format((row["owed"] or 0) / 100.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

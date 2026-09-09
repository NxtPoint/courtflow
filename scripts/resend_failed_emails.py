# scripts/resend_failed_emails.py — send the money documents the SES outage swallowed.
#
# WHY: between 2026-09-02 12:41 and 2026-09-09 SAST the SES credential was dead (the sibling
# project's security rotation deleted the IAM key this service was holding — see
# scripts/diagnose_email.py). 324 transactional emails were rendered, written to the in-app inbox,
# attempted, refused by AWS and recorded as `email_status='failed'`. Nothing retries them: the send
# is best-effort by design, so a failure is a dead end, not a queue.
#
# MOST OF THOSE 324 SHOULD STAY UNSENT. "Your booking is confirmed" for a court someone played on
# last Thursday is not a courtesy, it is a system announcing its own fault to a customer who had
# already moved on. What genuinely has to arrive late is the MONEY: a receipt, a refund, a
# membership or pack someone paid for. Those are documents people keep, reconcile and occasionally
# need for a dispute, and their value does not expire. So this resends that set and nothing else.
#
# WHAT IT WILL NOT SEND, and this is a judgement worth arguing with rather than assuming:
#   * `refund_requested` — "We've received your refund request, we'll be in touch." Sent seven days
#     after the fact this is worse than the silence it replaces: it either arrives after the money
#     already did, or it promises contact to someone still waiting. Those people need a person, not
#     a template, so the script LISTS them for you to mail by hand and refuses to send.
#   * everything non-money (booking_confirmed, lesson_booked, booking_reminder, class_*, ...) —
#     `--kinds` will override if you disagree, deliberately requiring you to name them.
#
# IDEMPOTENT. A row is only picked up while it is `failed`; a successful resend flips it to `sent`,
# so a second run finds nothing and nobody is emailed twice. A resend that fails again stays
# `failed` and will be retried by the next run, which is what you want.
#
# It renders through `marketing_crm.notifications._try_email` — the SAME renderer the live path
# uses — so a resent receipt is byte-for-byte the email that should have arrived: same shell, same
# rich detail block, same club From-name, Reply-To, BCC and invoice PDF. It re-renders the DETAIL
# block live (exactly as `deliver` does) and reuses the STORED title/body, so the customer gets the
# words they would have got, not a re-run of a template against data that has since moved.
#
# RUN IT (per docs/specs/DATA-ACCESS.md): Render -> courtflow-api -> Shell:
#     python -m scripts.resend_failed_emails                  # DRY RUN: who, what, nothing sent
#     python -m scripts.resend_failed_emails --limit 3 --commit   # a cautious first batch
#     python -m scripts.resend_failed_emails --commit         # the rest
#
# --commit requires a typed YES, because this sends real email to real members and no flag can be
# un-pressed afterwards.

import argparse
import io
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

# The outage window. Defaults to the day the credential died rather than "N days ago", so a run in
# three weeks still means the same thing.
DEFAULT_SINCE = "2026-09-02"

# The money set: a document about money that has moved, or is owed. Each of these is something a
# member may need to produce later, which is what makes a late delivery worth more than the
# embarrassment of sending it.
MONEY_KINDS = [
    "payment_succeeded",    # the confirm+receipt for a paid booking / membership
    "invoice_paid",         # ONE receipt for a batch settlement, however many lines it cleared
    "payment_refunded",     # money returned to a card
    "refund_decided",       # the answer to a refund request - they are waiting for this
    "membership_started",   # they bought a membership and have no record of it
    "bundle_activated",     # they bought a pack and have no record of it
    "invoice_issued",       # an invoice document + pay-link (none in the outage, month-end ran 09-01)
    "statement_ready",      # month-end balance + pay-link
]

# Money-adjacent but deliberately NOT resent - see the header. Listed so the operator can act.
HOLD_BACK_KINDS = ["refund_requested"]

# Prepended to the stored body so a receipt arriving a week late explains itself. Without this the
# member's reasonable reading is "they have just charged me again".
DELAY_NOTICE = ("This receipt is arriving late - a technical fault on our side stopped it sending "
                "at the time. Nothing about your payment or booking has changed; this is the "
                "record of what already happened.")


def _load_env():
    envf = os.path.join(REPO, ".env.local")
    if os.path.isfile(envf) and not os.getenv("DATABASE_URL"):
        for line in io.open(envf, encoding="utf-8"):
            line = line.strip()
            if line.startswith("DATABASE_URL=") and not line.startswith("#"):
                os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not os.getenv("DATABASE_URL"):
        print("DATABASE_URL is not set. On Render it already is; locally use .env.local.")
        sys.exit(2)


def _money(minor, cur="ZAR"):
    if minor is None:
        return ""
    try:
        return "%s %.2f" % (cur or "ZAR", int(minor) / 100.0)
    except Exception:
        return ""


def _fetch(s, kinds, since, limit=None):
    """Failed notifications of the given kinds since `since`, with the recipient's email + name.

    The row's `user_id` is ALREADY the resolved inbox owner (deliver() resolves child->guardian
    before inserting), so this joins straight to iam.user rather than re-running that resolution
    and risking a different answer than the one the original send used."""
    from sqlalchemy import text
    sql = (
        "SELECT n.id, n.club_id, n.user_id, n.kind, n.title, n.body, n.data, "
        "       (n.created_at AT TIME ZONE 'Africa/Johannesburg') AS when_sast, "
        "       u.email, COALESCE(NULLIF(TRIM(u.first_name), ''), u.email) AS name "
        "FROM core.notification n "
        "JOIN iam.user u ON u.id = n.user_id "
        "WHERE n.email_status = 'failed' "
        "  AND n.created_at >= CAST(:since AS date) "
        "  AND n.kind = ANY(:kinds) "
        "  AND u.email IS NOT NULL AND TRIM(u.email) <> '' "
        "ORDER BY n.created_at"
    )
    if limit:
        sql += " LIMIT :lim"
    return s.execute(text(sql), {"since": since, "kinds": list(kinds),
                                 "lim": limit}).mappings().fetchall()


def _resend_one(s, row, notice=True):
    """Render and send ONE stored notification through the live email path. Returns 'sent'/'failed'.

    Reuses `notifications._try_email` rather than rebuilding the email: it is the ONE renderer, and
    a resent receipt that does not match the original is a second version of a financial document.
    The two private imports are deliberate — the alternative is a fork of the email layer."""
    from marketing_crm import notifications as N
    from marketing_crm.email import booking_detail

    ctx = dict(row["data"] or {})
    club_id = row["club_id"]
    kind = row["kind"]

    # The rich block is loaded LIVE, exactly as deliver() does — it is looked up by
    # booking_id/class_session_id/order_id, which the stored ctx carries. Guarded -> None -> the
    # plain body, so a since-deleted booking degrades instead of failing.
    detail = None
    if kind in booking_detail.DETAIL_KINDS or kind in N._PURCHASE_KINDS:
        try:
            detail = booking_detail.load(s, club_id, ctx)
        except Exception:
            detail = None

    invoice_doc = None
    if kind == "invoice_issued":
        try:
            from marketing_crm.email import invoice_detail
            invoice_doc = invoice_detail.load(s, club_id, ctx)
        except Exception:
            invoice_doc = None

    body = row["body"] or row["title"]
    if notice:
        body = DELAY_NOTICE + "\n\n" + body

    ident = N._club_identity(s, club_id)
    bcc = list(filter(None, [ident.get("bcc")]))
    return N._try_email(row["email"], row["title"], body, row["name"],
                        from_name=ident.get("from_name"), reply_to=ident.get("reply_to"),
                        bcc=bcc, kind=kind, ctx=ctx, detail=detail, invoice_doc=invoice_doc)


def _report_held_back(s, since):
    """List the refund_requested rows we refuse to send, so they get a human instead of silence."""
    from sqlalchemy import text
    rows = s.execute(text(
        "SELECT (n.created_at AT TIME ZONE 'Africa/Johannesburg')::date AS d, u.email, n.data "
        "FROM core.notification n JOIN iam.user u ON u.id = n.user_id "
        "WHERE n.email_status = 'failed' AND n.created_at >= CAST(:since AS date) "
        "  AND n.kind = ANY(:kinds) ORDER BY n.created_at"),
        {"since": since, "kinds": HOLD_BACK_KINDS}).mappings().fetchall()
    if not rows:
        return
    print("\nHELD BACK - these are NOT resent, deliberately (%d):" % len(rows))
    print("   An automated 'we have received your request, we will be in touch' arriving a week")
    print("   late is worse than the silence it replaces. Email these people yourself:")
    for r in rows:
        d = dict(r["data"] or {})
        print("     %-12s %-38s %s" % (r["d"], r["email"],
                                       _money(d.get("amount_minor"), d.get("currency_code"))))


def main():
    ap = argparse.ArgumentParser(
        description="Resend the MONEY emails the SES outage swallowed (dry-run by default).")
    ap.add_argument("--since", default=DEFAULT_SINCE,
                    help="only rows on/after this date (default %s, the day the key died)" % DEFAULT_SINCE)
    ap.add_argument("--kinds", help="comma-separated override of the money set (use with care)")
    ap.add_argument("--limit", type=int, help="cap the batch - use for a cautious first run")
    ap.add_argument("--no-notice", action="store_true",
                    help="omit the 'this is arriving late' line from the body")
    ap.add_argument("--sleep", type=float, default=0.2, help="seconds between sends (default 0.2)")
    ap.add_argument("--commit", action="store_true", help="actually send (requires a typed YES)")
    args = ap.parse_args()

    kinds = ([k.strip() for k in args.kinds.split(",") if k.strip()]
             if args.kinds else list(MONEY_KINDS))

    _load_env()
    import db
    from sqlalchemy import text

    mode = "SENDING FOR REAL" if args.commit else "DRY RUN - nothing will be sent"
    print("\nResend of failed money emails            (%s)" % mode)
    print("=" * 78)
    print("   window : failures on/after %s" % args.since)
    print("   kinds  : %s" % ", ".join(kinds))
    print("   notice : %s" % ("omitted" if args.no_notice else "prepended ('arriving late')"))

    with db.session_scope() as s:
        rows = _fetch(s, kinds, args.since, args.limit)
        if not rows:
            print("\n   Nothing to resend. Either it has all been sent already (this is idempotent -")
            print("   a success flips the row to 'sent'), or no money email failed in the window.")
            _report_held_back(s, args.since)
            print("\n" + "=" * 78 + "\n")
            return 0

        print("\n   %-4s%-18s%-22s%-34s%s" % ("#", "when (SAST)", "kind", "recipient", "subject"))
        for i, r in enumerate(rows, 1):
            print("   %-4d%-18s%-22s%-34s%s"
                  % (i, str(r["when_sast"])[:16], r["kind"], (r["email"] or "")[:33],
                     (r["title"] or "")[:34]))
        print("\n   %d email(s) to resend." % len(rows))
        _report_held_back(s, args.since)

        if not args.commit:
            print("\n   DRY RUN - nothing was sent and nothing was written.")
            print("   Re-run with --commit (and optionally --limit N first) to send.")
            print("\n" + "=" * 78 + "\n")
            return 0

        # Refuse rather than grind out N no-ops. _try_email returns 'skipped' with no creds, which
        # sends nothing and marks nothing - correct, but as a batch it reads like N failures and
        # sends the operator hunting the wrong problem.
        from marketing_crm.email import ses
        if not ses.enabled():
            print("\n   [X] SES is not configured on this service, so nothing can be sent and every")
            print("       row would report as skipped. Run `python -m scripts.diagnose_email` first.")
            return 2

        print("\n   This SENDS REAL EMAIL to %d member(s) and cannot be undone." % len(rows))
        try:
            if input("   Type YES to proceed: ").strip() != "YES":
                print("   Aborted. Nothing sent.")
                return 1
        except EOFError:
            print("   No terminal to confirm on. Aborted. Nothing sent.")
            return 1

        sent = failed = 0
        print()
        for i, r in enumerate(rows, 1):
            status = _resend_one(s, r, notice=not args.no_notice)
            if status == "sent":
                # Flip ONLY on success, so this stays idempotent AND a failure is retried next run.
                s.execute(text("UPDATE core.notification SET email_status = 'sent' "
                               "WHERE id = :i AND club_id = :c"),
                          {"i": r["id"], "c": r["club_id"]})
                sent += 1
                print("   [%d/%d] sent    %-34s %s" % (i, len(rows), r["email"], r["kind"]))
            else:
                failed += 1
                why = "SES not configured" if status == "skipped" else "refused"
                print("   [%d/%d] FAILED  %-34s %s  (%s; left as 'failed' - re-run to retry)"
                      % (i, len(rows), r["email"], r["kind"], why))
            if args.sleep:
                time.sleep(args.sleep)

        print("\n   sent %d   failed %d" % (sent, failed))
        if failed:
            print("   Run `python -m scripts.diagnose_email` before retrying - a failure here")
            print("   means the transport is refusing us again, not that these rows are bad.")

    print("\n" + "=" * 78 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

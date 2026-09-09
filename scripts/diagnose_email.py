# scripts/diagnose_email.py — why did the transactional emails stop?
#
# WHY: every transactional send in this platform is BEST-EFFORT AND SILENT. `ses.enabled()`
# self-gates on creds + a sender and returns False with no keys; `send_email` catches every
# exception and returns False; `_try_email` turns both into a status string. Nothing raises,
# nothing 500s, no booking fails. So a revoked IAM key, a deleted SES identity, a dropped env
# var or an account moved back into the SES sandbox all present IDENTICALLY to the operator:
# bookings keep working and the confirmation email simply never arrives.
#
# That silence is deliberate (a member must never lose a booking because SES is down) and it is
# exactly why it needs a purpose-built read: this script asks the three questions that separate
# the causes, in the order that narrows fastest.
#
#   A. CONFIG  — does the app still SEE a sender + credentials? (env dropped / key removed)
#   B. AWS     — do those credentials still WORK, is the identity still verified, is the
#                account still out of the sandbox and still allowed to send? (key rotated,
#                policy narrowed, identity deleted, sending paused)
#   C. LEDGER  — what does core.notification.email_status actually say, per day?
#                'sent' -> it worked · 'failed' -> SES refused us · 'skipped' -> we never
#                even tried because enabled() was False. The DAY the column flips is the day
#                the change landed, which is the fact that identifies the change.
#
# READ-ONLY. Every statement is a SELECT and every AWS call is a GET/DESCRIBE. Nothing is
# written and no email is sent UNLESS you pass --to, which sends exactly one test message.
#
# RUN IT (per docs/specs/DATA-ACCESS.md): Render -> courtflow-api -> Shell:
#     python -m scripts.diagnose_email
#     python -m scripts.diagnose_email --to you@example.com     # one real test send
#     python -m scripts.diagnose_email --days 45 --no-db        # config + AWS only
#
# NEVER PRINTS A CREDENTIAL. The secret key is never read for display; the access key ID is
# shown as its LAST FOUR characters only, because that is the one thing that lets you tell a
# rotated key from a removed one against the IAM console, and four characters authenticate
# nothing. Any AKIA-shaped string inside an AWS error message is masked before printing.

import argparse
import io
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

_AKIA = re.compile(r"\b((?:AKIA|ASIA)[A-Z0-9]{4})[A-Z0-9]{8}([A-Z0-9]{4})\b")


def _mask(s):
    """Mask any AWS access-key-shaped token in free text (AWS error strings can quote one)."""
    return _AKIA.sub(lambda m: m.group(1) + "........" + m.group(2), str(s))


def _tail4(v):
    v = (v or "").strip()
    return ("..." + v[-4:]) if len(v) >= 4 else ("(set)" if v else "(unset)")


def _load_env():
    envf = os.path.join(REPO, ".env.local")
    if os.path.isfile(envf) and not os.getenv("DATABASE_URL"):
        for line in io.open(envf, encoding="utf-8"):
            line = line.strip()
            if line.startswith("DATABASE_URL=") and not line.startswith("#"):
                os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    return bool(os.getenv("DATABASE_URL"))


def _err(e):
    """A botocore error rendered as 'Code: message', masked. Falls back to the class name."""
    code = ""
    try:
        code = (getattr(e, "response", {}) or {}).get("Error", {}).get("Code", "") or ""
    except Exception:
        code = ""
    return _mask(("%s: %s" % (code, e)) if code else ("%s: %s" % (type(e).__name__, e)))


# ---------------------------------------------------------------------------
# A. Config — what the app itself sees. This is ses.enabled() and nothing else.
# ---------------------------------------------------------------------------
def section_config():
    from marketing_crm.email import ses

    print("\nA. CONFIG  (exactly what the running app sees)")
    print("-" * 78)
    sender = ses._sender()
    enabled = ses.enabled()
    own = bool(ses._ses_creds())
    print("   ses.enabled()            : %s" % ("YES" if enabled else "NO  <-- nothing is even attempted"))
    print("   sender (From)            : %s" % (sender or "(unset: SES_SENDER / SES_FROM / SES_FROM_EMAIL)"))
    print("   region                   : %s" % ses._region())
    print("   credentials              : %s" % ("SES_AWS_* (dedicated SES key)" if own else "default AWS_* chain"))
    print("   SES_AWS_ACCESS_KEY_ID    : %s" % _tail4(os.getenv("SES_AWS_ACCESS_KEY_ID")))
    print("   SES_AWS_SECRET_ACCESS_KEY: %s" % ("set" if os.getenv("SES_AWS_SECRET_ACCESS_KEY") else "UNSET"))
    print("   AWS_ACCESS_KEY_ID        : %s" % _tail4(os.getenv("AWS_ACCESS_KEY_ID")))
    print("   AWS_SECRET_ACCESS_KEY    : %s" % ("set" if os.getenv("AWS_SECRET_ACCESS_KEY") else "UNSET"))
    print("   TRANSACTIONAL_BCC        : %s" % (os.getenv("TRANSACTIONAL_BCC") or "(unset)"))
    print("   EMAIL_INVOICE_PDF_ENABLED: %s   EMAIL_ICS_ENABLED: %s"
          % (os.getenv("EMAIL_INVOICE_PDF_ENABLED", "0"), os.getenv("EMAIL_ICS_ENABLED", "0")))
    if not enabled:
        print("\n   [X] The app is NOT attempting any transactional email. A sender or the AWS")
        print("       credentials are missing from this service's environment. Every notification")
        print("       is being written to the in-app inbox with email_status='skipped'.")
    return enabled, sender


# ---------------------------------------------------------------------------
# B. AWS — do the credentials still work, and is the identity still allowed to send?
#    Each probe is reported on its own line: a NARROWED policy can deny the probe while
#    still allowing the send, so a failed GetSendQuota is evidence, never a verdict.
# ---------------------------------------------------------------------------
def section_aws(sender):
    from marketing_crm.email import ses

    print("\nB. AWS  (read-only probes - no email is sent here)")
    print("-" * 78)
    try:
        import boto3
    except Exception as e:
        print("   [X] boto3 is not importable: %s" % _err(e))
        return
    try:
        client = boto3.client("ses", region_name=ses._region(), **ses._ses_creds())
    except Exception as e:
        print("   [X] could not build an SES client: %s" % _err(e))
        return

    # 1) Does the key authenticate at all, and is the account allowed to send?
    try:
        q = client.get_send_quota()
        print("   send quota               : %.0f / 24h, %.0f sent in the last 24h, %.1f/sec"
              % (q.get("Max24HourSend", 0), q.get("SentLast24Hours", 0), q.get("MaxSendRate", 0)))
        if q.get("Max24HourSend") == 200:
            print("       ^ 200/day is the SES SANDBOX default: only VERIFIED recipients get mail.")
    except Exception as e:
        print("   send quota               : FAILED  %s" % _err(e))
        print("       ^ InvalidClientTokenId / SignatureDoesNotMatch = the key is gone or rotated.")
        print("         AccessDenied = the key still exists but its policy no longer allows SES.")

    try:
        on = client.get_account_sending_enabled().get("Enabled")
        print("   account sending          : %s"
              % ("ENABLED" if on else "DISABLED  <-- AWS has paused sending"))
    except Exception as e:
        print("   account sending          : could not read  (%s)" % _err(e))

    # 2) Is the From identity still verified? A security tidy-up can delete an identity.
    addr = (sender or "").strip()
    domain = addr.split("@", 1)[1] if "@" in addr else ""
    want = [x for x in (addr, domain) if x]
    if want:
        try:
            attrs = client.get_identity_verification_attributes(Identities=want).get(
                "VerificationAttributes", {})
            for ident in want:
                st = (attrs.get(ident) or {}).get("VerificationStatus", "NOT FOUND")
                flag = "" if st == "Success" else "   <-- not usable as a From address"
                print("   identity %-16s: %s%s" % (ident, st, flag))
            if not any((attrs.get(i) or {}).get("VerificationStatus") == "Success" for i in want):
                print("       ^ neither the address nor its domain is verified IN THIS REGION.")
                print("         Check SES_REGION matches where the identity lives before anything else.")
        except Exception as e:
            print("   identity check           : could not read  (%s)" % _err(e))
        try:
            dkim = client.get_identity_dkim_attributes(
                Identities=([domain] if domain else want)).get("DkimAttributes", {})
            for ident, d in (dkim or {}).items():
                print("   DKIM %-20s: enabled=%s verification=%s"
                      % (ident, d.get("DkimEnabled"), d.get("DkimVerificationStatus")))
        except Exception as e:
            print("   DKIM check               : could not read  (%s)" % _err(e))


# ---------------------------------------------------------------------------
# C. The ledger — core.notification.email_status is a per-send delivery record.
#    This is the part that DATES the breakage, which is what identifies the change.
# ---------------------------------------------------------------------------
def section_ledger(days):
    from sqlalchemy import text
    import db

    verdict = {"rows": 0, "sent": 0, "failed": 0, "skipped": 0}
    print("\nC. DELIVERY LEDGER  (core.notification.email_status, last %d days)" % days)
    print("-" * 78)
    with db.session_scope() as s:
        rows = s.execute(text(
            "SELECT (created_at AT TIME ZONE 'Africa/Johannesburg')::date AS d, "
            "       count(*) FILTER (WHERE email_status = 'sent')    AS sent, "
            "       count(*) FILTER (WHERE email_status = 'failed')  AS failed, "
            "       count(*) FILTER (WHERE email_status = 'skipped') AS skipped, "
            "       count(*) AS total "
            "FROM core.notification "
            "WHERE created_at >= now() - make_interval(days => :d) "
            "GROUP BY 1 ORDER BY 1"), {"d": days}).fetchall()
        if not rows:
            print("   No notifications at all in the window. Either the club is quiet, or emit()")
            print("   is not reaching the notification engine - which is a DIFFERENT fault from")
            print("   email: check the API logs for 'notification' rather than 'ses'.")
        else:
            print("   %-12s%8s%8s%9s%8s   %s" % ("day", "sent", "failed", "skipped", "total", ""))
            for d, sent, failed, skipped, total in rows:
                note = ""
                if total and not sent:
                    note = "<-- NOTHING sent"
                    if skipped and not failed:
                        note += " (never attempted: no creds)"
                    elif failed:
                        note += " (SES refused every send)"
                print("   %-12s%8d%8d%9d%8d   %s" % (d, sent, failed, skipped, total, note))
                verdict["rows"] += total
                verdict["sent"] += sent
                verdict["failed"] += failed
                verdict["skipped"] += skipped

        last_sent = s.execute(text(
            "SELECT max(created_at AT TIME ZONE 'Africa/Johannesburg') FROM core.notification "
            "WHERE email_status = 'sent'")).scalar()
        first_bad = s.execute(text(
            "SELECT min(created_at AT TIME ZONE 'Africa/Johannesburg') FROM core.notification "
            "WHERE email_status <> 'sent' AND created_at > COALESCE(("
            "  SELECT max(created_at) FROM core.notification WHERE email_status = 'sent'"
            "), '-infinity'::timestamptz)")).scalar()
        print("\n   last SUCCESSFUL send     : %s" % (last_sent or "never"))
        print("   first send after that    : %s"
              % (first_bad or "none - nothing has been attempted since"))
        if last_sent and first_bad:
            print("       ^ the break landed between these two timestamps (SAST). Match that window")
            print("         against the change you made and you have the cause.")

        # What KIND of mail is affected - is it everything, or one template?
        kinds = s.execute(text(
            "SELECT kind, count(*) FILTER (WHERE email_status = 'sent') AS sent, "
            "       count(*) FILTER (WHERE email_status <> 'sent') AS not_sent "
            "FROM core.notification WHERE created_at >= now() - make_interval(days => :d) "
            "GROUP BY 1 ORDER BY 3 DESC, 1 LIMIT 15"), {"d": days}).fetchall()
        if kinds:
            print("\n   by kind (same window) - if EVERY kind is affected it is the transport;")
            print("   if one kind is, it is that template or its trigger:")
            print("   %-34s%8s%10s" % ("kind", "sent", "not sent"))
            for k, sent, bad in kinds:
                print("   %-34s%8d%10d" % (k, sent, bad))

        # The OTHER silent failure with the identical symptom: the hourly reminder sweep is
        # fired by a GitHub Action whose curl is `|| echo`, so a 403 from a rotated OPS_KEY
        # leaves the workflow GREEN and the reminders simply stop. diary.reminder_log is the
        # only record that the sweep actually ran, so it is the one honest check.
        # diary.reminder_log is created LAZILY by diary.crons._ensure_reminder_log, so a missing
        # table is a legitimate state (the sweep has never run here), not an error.
        try:
            if s.execute(text("SELECT to_regclass('diary.reminder_log')")).scalar() is None:
                print("\n   last reminder fired      : table absent - the sweep has never run here")
            else:
                last_rem = s.execute(text(
                    "SELECT max(sent_at AT TIME ZONE 'Africa/Johannesburg') FROM diary.reminder_log"
                )).scalar()
                print("\n   last reminder fired      : %s" % (last_rem or "never"))
            print("       ^ the hourly sweep is an OPS_KEY-guarded GitHub Action whose curl")
            print("         swallows a 403, so a rotated OPS_KEY stops reminders with a GREEN")
            print("         workflow. If this is stale but bookings are current, fix the key.")
        except Exception as e:
            print("\n   reminder log             : could not read  (%s)" % _mask(e))

    return verdict


def print_verdict(enabled, v):
    """Say, in one line, which of the three distinct faults this is - they need different fixes."""
    print("\nVERDICT")
    print("-" * 78)
    if v is None:
        print("   No ledger read, so this is a partial answer: see sections A and B.")
        return
    if not v.get("rows"):
        print("   Nothing to send in the window. Not an email fault - check that bookings are")
        print("   actually being made, then re-run over a longer --days.")
    elif v.get("sent") and not (v.get("failed") or v.get("skipped")):
        print("   SES ACCEPTED EVERY SEND. The platform's side is healthy, so if members are")
        print("   still not receiving mail this is a DELIVERABILITY problem, not a sending one:")
        print("   the mail is landing in spam or being rejected by the receiving server. Check")
        print("   SPF/DKIM/DMARC on the SENDING domain (section A's From address) and the DMARC")
        print("   aggregate reports - NOT this codebase.")
    elif v.get("failed"):
        print("   SES REFUSED our sends. The credentials reached AWS and were rejected, or the")
        print("   identity/permission is gone. Section B names the exact reason; a rotated or")
        print("   deleted IAM key is the usual one. Fix = put a working key in this service's")
        print("   SES_AWS_ACCESS_KEY_ID / SES_AWS_SECRET_ACCESS_KEY, then re-run with --to.")
    elif v.get("skipped") and not enabled:
        print("   WE NEVER TRIED. ses.enabled() is False, so no email was attempted at all - a")
        print("   sender or the AWS credentials are missing from this service's environment.")
        print("   Fix = set them (section A lists which are unset), then re-run with --to.")
    else:
        print("   Mixed result - read sections A to C together; the day the column flips in C")
        print("   is the day the change landed.")


def main():
    ap = argparse.ArgumentParser(
        description="Diagnose why transactional email stopped (read-only unless --to).")
    ap.add_argument("--days", type=int, default=21, help="ledger window in days (default 21)")
    ap.add_argument("--no-db", action="store_true", help="skip the ledger (config + AWS only)")
    ap.add_argument("--to", help="send ONE real test email to this address")
    ap.add_argument("--from-name", default="NextPoint Tennis",
                    help="club display name on the test send")
    args = ap.parse_args()

    print("\nTransactional email - diagnosis            (READ-ONLY%s)"
          % ("" if not args.to else "; one test email WILL be sent"))
    print("=" * 78)

    enabled, sender = section_config()
    section_aws(sender)

    v = None
    if not args.no_db:
        if _load_env():
            try:
                v = section_ledger(args.days)
            except Exception as e:
                print("\nC. DELIVERY LEDGER - could not read: %s" % _mask(e))
        else:
            print("\nC. DELIVERY LEDGER - skipped: no DATABASE_URL (run this on the Render shell).")
    print_verdict(enabled, v)

    if args.to:
        import logging
        from marketing_crm.email import ses
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
        print("\nD. TEST SEND -> %s" % args.to)
        print("-" * 78)
        ok = ses.send_email(
            args.to, "NextPoint - transactional email test",
            "If you can read this, transactional email is working again.",
            body_html=ses.html_wrap("Email test", "<p>If you can read this, transactional email "
                                    "is working again.</p>", footer=args.from_name),
            from_name=args.from_name, reply_to="info@nextpointtennis.com")
        print("   send_email returned: %s" % ok)
        if not ok:
            print("   The exact cause is in the 'ses: send_email failed' traceback above.")

    print("\n" + "=" * 78)
    print("Nothing was written to the database.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

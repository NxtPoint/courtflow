# scripts/repermission_campaign.py — the ONE-OFF re-permission send to non-consented existing members.
#
# The ~500 members who never gave marketing consent can't be marketed to (POPIA). This sends them ONE
# service-framed notice — "NextPoint has moved to a new app, want to keep hearing from us?" — via OUR
# OWN SES (an existing-customer service notice, not a Klaviyo marketing blast). Each email carries a
# signed, per-recipient /subscribe link; a tap opts them in (writes consent to our DB + subscribes to
# Klaviyo → the Welcome flow) and nudges them back to booking. See docs/specs/KLAVIYO-MASTER-PLAN.md §5.
#
# DRY-RUN BY DEFAULT — reports the cohort + a sample, sends NOTHING. Pass --commit to actually send.
# Re-run-safe: anyone who has since opted in (core.app_user.marketing_opt_in) is excluded, so a second
# run won't re-email a converter. This is a ONE-OFF — do not schedule it.
#
#   python -m scripts.repermission_campaign                     # report cohort, send nothing
#   python -m scripts.repermission_campaign --to you@email.com  # send ONE test to yourself (needs --commit)
#   python -m scripts.repermission_campaign --limit 25 --commit # first small batch
#   python -m scripts.repermission_campaign --commit            # the full send
#
# Needs DATABASE_URL (Render shell or .env.local) + SES creds + KLAVIYO_API_KEY on the send target.

import argparse
import os
import sys
import time
from pathlib import Path


def _load_env_local():
    if os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("DB_URL"):
        return
    f = Path(__file__).resolve().parent.parent / ".env.local"
    if not f.exists():
        print("!! No DATABASE_URL in env and no .env.local found. Run on the Render shell, or create\n"
              "   .env.local with: DATABASE_URL=postgresql://courtflow:...@...render.com/courtflow")
        sys.exit(2)
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    if not os.environ.get("DATABASE_URL"):
        print("!! .env.local has no DATABASE_URL line")
        sys.exit(2)


# Excluded from every send (test / admin addresses).
_TEST_EMAILS = ("info@ten-fifty5.com", "eb.stojakovic@gmail.com", "info@nextpointtennis.com")

# Non-consented EXISTING members (has a membership = a real customer relationship), with a usable
# email, who have NOT since opted in anywhere. club_id comes from their earliest membership.
_COHORT_SQL = """
SELECT u.id AS user_id, u.email, u.first_name,
       (SELECT m.club_id FROM iam.membership m WHERE m.user_id = u.id ORDER BY m.joined_at LIMIT 1) AS club_id
FROM iam.user u
WHERE u.email IS NOT NULL AND btrim(u.email) <> ''
  AND (u.marketing_opt_in = false OR u.marketing_opt_in IS NULL)
  AND EXISTS (SELECT 1 FROM iam.membership m WHERE m.user_id = u.id)
  AND NOT EXISTS (SELECT 1 FROM core.app_user au
                  WHERE lower(au.email) = lower(u.email)
                    AND au.marketing_opt_in = true AND au.deleted_at IS NULL)
  AND lower(u.email) <> ALL(:tests)
ORDER BY u.created_at
{limit}
"""

SUBJECT = "NextPoint has moved — a quick hello (and one question)"
FROM_NAME = os.getenv("CLUB_FROM_NAME", "NextPoint Tennis")
REPLY_TO = os.getenv("CLUB_REPLY_TO", "info@nextpointtennis.com")
FOOTER = "NextPoint Tennis · Killarney Country Club, Killarney, Johannesburg · info@nextpointtennis.com"


def _email_html(first_name, optin_url):
    """The re-permission email body. Cowork can revise this wording — it's the one piece of copy the
    send owns (everything else is Klaviyo). Service-framed, ONE clear CTA, honest one-off."""
    from marketing_crm.email.ses import html_wrap
    hi = f"Hi {first_name}," if first_name else "Hi there,"
    body = f"""
      <p style="margin:0 0 14px">{hi}</p>
      <p style="margin:0 0 14px">Big news from the club: <strong>NextPoint has moved to a brand-new
        website and app</strong>. Same courts, same coaches — now you can book a court, a lesson or a
        class straight from your phone.</p>
      <p style="margin:0 0 14px">We'd love to keep you in the loop on court time, coaching and what's on
        at the club — but only if you'd like us to. One tap sorts it:</p>
      <p style="margin:20px 0">
        <a href="{optin_url}"
           style="display:inline-block;background:#C8E85C;color:#26330A;text-decoration:none;
                  font-weight:800;padding:14px 26px;border-radius:10px;font-size:16px">
          Yes, keep me posted &rarr;</a>
      </p>
      <p style="margin:0 0 14px;color:#5B6B62;font-size:13px">Not interested? No problem — just ignore
        this email and you won't hear from us again. You'll still get the essentials about your own
        bookings.</p>
      <p style="margin:18px 0 0">See you on court,<br>The NextPoint team</p>
    """
    return html_wrap("NextPoint has moved", body, footer=FOOTER)


def _email_text(first_name, optin_url):
    hi = f"Hi {first_name}," if first_name else "Hi there,"
    return (f"{hi}\n\n"
            "NextPoint has moved to a brand-new website and app — same courts and coaches, now bookable "
            "from your phone.\n\n"
            "We'd love to keep you posted on court time, coaching and club news — but only if you'd like "
            "us to. Confirm here:\n"
            f"{optin_url}\n\n"
            "Not interested? Just ignore this email and you won't hear from us again. You'll still get the "
            "essentials about your own bookings.\n\n"
            "See you on court,\nThe NextPoint team\n\n" + FOOTER)


def _hdr(t):
    print(f"\n== {t} " + "=" * max(0, 60 - len(t)))


def main():
    ap = argparse.ArgumentParser(description="One-off re-permission send to non-consented members.")
    ap.add_argument("--commit", action="store_true", help="actually send (default: dry-run)")
    ap.add_argument("--limit", type=int, default=0, help="cap the batch (0 = all)")
    ap.add_argument("--to", help="send a SINGLE test email to this address (still needs --commit)")
    ap.add_argument("--sleep", type=float, default=0.15, help="seconds between sends (SES rate courtesy)")
    args = ap.parse_args()
    _load_env_local()

    from db import session_scope
    from sqlalchemy import text
    from marketing_crm.repermission import optin_url_for
    from marketing_crm.email import ses

    # --- single test send -----------------------------------------------------
    if args.to:
        if not args.commit:
            print("--to is a real send; add --commit to actually send the test.")
            return
        if not ses.enabled():
            print("!! SES not configured (SES_SENDER + AWS creds) — cannot send.")
            sys.exit(2)
        # Mint a token against the first real member so the /subscribe link resolves to someone.
        with session_scope() as s:
            row = s.execute(text(_COHORT_SQL.format(limit="LIMIT 1")),
                            {"tests": list(_TEST_EMAILS)}).mappings().first()
        if not row:
            print("!! No cohort member to key the test token to.")
            sys.exit(2)
        url = optin_url_for(row["user_id"], row["club_id"])
        ok = ses.send_email(args.to, "[TEST] " + SUBJECT, _email_text("there", url),
                            body_html=_email_html("there", url), from_name=FROM_NAME, reply_to=REPLY_TO)
        print(("sent" if ok else "FAILED") + f" test → {args.to}")
        return

    # --- cohort ---------------------------------------------------------------
    limit_sql = f"LIMIT {int(args.limit)}" if args.limit and args.limit > 0 else ""
    with session_scope() as s:
        rows = s.execute(text(_COHORT_SQL.format(limit=limit_sql)),
                         {"tests": list(_TEST_EMAILS)}).mappings().all()

    _hdr("Re-permission cohort (non-consented existing members)")
    print(f"   cohort size{' (capped)' if limit_sql else ''}: {len(rows)}")
    for r in rows[:5]:
        print(f"     - {r['email']}  ({(r['first_name'] or '').strip() or 'no name'})")
    if len(rows) > 5:
        print(f"     … and {len(rows) - 5} more")

    if not args.commit:
        print("\n   DRY-RUN — nothing sent. Re-run with --commit to send (or --to you@email.com to test one).")
        return

    if not ses.enabled():
        print("\n!! SES not configured (SES_SENDER + AWS creds) — cannot send. Aborting.")
        sys.exit(2)

    _hdr("Sending")
    sent = failed = skipped = 0
    for r in rows:
        email = (r["email"] or "").strip()
        if not email:
            skipped += 1
            continue
        url = optin_url_for(r["user_id"], r["club_id"])
        if not url:
            skipped += 1
            continue
        fn = (r["first_name"] or "").strip().split(" ")[0] or None
        ok = ses.send_email(email, SUBJECT, _email_text(fn, url),
                            body_html=_email_html(fn, url), from_name=FROM_NAME, reply_to=REPLY_TO)
        if ok:
            sent += 1
        else:
            failed += 1
            print(f"   FAILED → {email}")
        if args.sleep:
            time.sleep(args.sleep)

    _hdr("Done")
    print(f"   sent {sent} · failed {failed} · skipped {skipped}")
    print("   One-off complete. Do NOT re-run for the same cohort (converters are auto-excluded, but "
          "non-responders should not be re-emailed).")


if __name__ == "__main__":
    main()

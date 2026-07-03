# scripts/test_ses.py — verify the SES wiring (keys / permissions / sandbox) in one command.
#
# Use this to de-risk the go-live INTERIM (reuse the ten-fifty5 SES account) BEFORE relying on
# it for real members. It reads the same env the app does (SES_SENDER + SES_AWS_* or AWS_*),
# reports the resolved config, and — with --to — attempts a real send and explains any failure
# (the two invisible risks: the IAM key lacking ses:SendEmail, or the account still in sandbox).
#
# Run locally with the interim env pasted in (fastest feedback loop, no deploy):
#   SES_SENDER=bookings@ten-fifty5.com SES_REGION=us-east-1 \
#   SES_AWS_ACCESS_KEY_ID=AKIA... SES_AWS_SECRET_ACCESS_KEY=... \
#   python -m scripts.test_ses --to you@example.com
#
#   --to <a verified addr>  -> proves the KEY works + has SES permission.
#   --to <an external gmail> -> if THAT arrives too, the account is OUT of the sandbox (safe for members).

import argparse
import logging

from marketing_crm.email import ses


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Verify SES config + optionally send a test email.")
    ap.add_argument("--to", help="send a real test email to this address")
    ap.add_argument("--from-name", default="NextPoint Tennis", help="club display name (From)")
    ap.add_argument("--reply-to", default="info@nextpointtennis.com", help="Reply-To address")
    args = ap.parse_args()

    print("SES config (as the app sees it):")
    print("  enabled :", ses.enabled())
    print("  sender  :", ses._sender() or "(unset — set SES_SENDER)")
    print("  region  :", ses._region())
    print("  creds   :", "SES_AWS_* (own account)" if ses._ses_creds() else "default AWS_* chain")

    if not ses.enabled():
        print("\n[X] Not enabled. Set SES_SENDER + (SES_AWS_ACCESS_KEY_ID/SES_AWS_SECRET_ACCESS_KEY "
              "or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY). Nothing sent.")
        return 1

    if not args.to:
        print("\n[OK] Config looks complete. Re-run with --to you@example.com to send a real test.")
        return 0

    print(f"\nSending test email to {args.to} ...")
    ok = ses.send_email(
        args.to,
        "CourtFlow / NextPoint — SES test",
        "If you can read this, transactional email is working. — NextPoint Tennis",
        body_html=ses.html_wrap("SES test", "<p>If you can read this, transactional email is "
                                "working.</p>", footer="NextPoint Tennis"),
        from_name=args.from_name, reply_to=args.reply_to,
    )
    print("send_email returned:", ok)
    if ok:
        print("[OK] Accepted by SES.")
        print("  - If --to was a VERIFIED address: the key works + has SES permission.")
        print("  - If --to was an UNVERIFIED external inbox and it ARRIVES: you're OUT of the sandbox")
        print("    (safe to email real members). If it does NOT arrive: still sandboxed.")
    else:
        print("[X] Send failed — see the 'ses: send_email failed' log line above for the exact cause:")
        print("  - 'Email address is not verified' / MessageRejected => account is in the SES SANDBOX")
        print("       (only verified recipients get mail; request production access to send to members).")
        print("  - 'AccessDenied' / not authorized => the IAM key lacks ses:SendEmail / ses:SendRawEmail.")
        print("  - connection/region error => SES_REGION doesn't match where the identity is verified.")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

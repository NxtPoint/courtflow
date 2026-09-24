# scripts/provision_api_club.py — create a club whose players book through the PUBLIC API from a
# partner's own app (e.g. an academy booking inside Ten-Fifty5). DRY RUN BY DEFAULT.
#
#   python -m scripts.provision_api_club --slug academy-test --name "Academy (test)" \
#       --issuer https://clerk.ten-fifty5.com --return-origin https://www.ten-fifty5.com \
#       --contact-email bookings@academy.example              # prints the plan, writes nothing
#   ... same + --commit                                      # writes it
#
# Creates, idempotently (keyed on the slug; a re-run changes nothing that exists):
#   - the club scaffolding (scripts.provision_club): club, branding (no domain — its players never
#     visit a CourtFlow site), a location carrying the contact email (the From/Reply-To of its emails)
#   - policy: which outside login it accepts, where checkout may return to, NO free week, marketing
#     consent default OFF, pay-at-club ON, card OFF (until its own payment account is connected)
#   - one court service with a price per length, N courts, and opening hours every day
#   - optionally a club admin (an EXISTING user, by email)
#
# What it deliberately does NOT do: switch on card payments (the club's own Yoco/PayPal account comes
# first — docs/specs/ENV-STATUS.md) or touch any other club. Safe on the Render Shell.

import argparse
import sys

from sqlalchemy import text

from db import session_scope
from scripts.provision_club import provision_club, get_club_by_slug


def _plan(a):
    lengths = [int(x) for x in a.lengths.split(",") if x.strip()]
    o, c = a.hours.split("-")
    return {
        "slug": a.slug.strip().lower(), "name": a.name, "currency": a.currency, "timezone": a.timezone,
        "issuers": [i.rstrip("/") for i in a.issuer], "return_origins": [r.rstrip("/") for r in a.return_origin],
        "contact_email": a.contact_email, "courts": a.courts, "price_minor": a.price_minor,
        "lengths": lengths, "open": o.strip(), "close": c.strip(), "admin_email": a.admin_email,
    }


def provision(s, p):
    existing = get_club_by_slug(s, p["slug"])
    club_id = provision_club(
        s, slug=p["slug"], name=p["name"], currency_code=p["currency"], timezone=p["timezone"],
        branding={},
        policy={"booking_window_days": 14, "allow_pay_at_court": True, "allow_monthly_account": False,
                "allow_online_payment": False},
        locations=[{"name": p["name"], "email": p["contact_email"]}])
    s.execute(text("UPDATE club.policy SET accepted_login_issuers = :i, allowed_return_origins = :r, "
                   "signup_trial_days = COALESCE(signup_trial_days, 0), "
                   "marketing_opt_in_default = false WHERE club_id = :c"),
              {"i": p["issuers"], "r": p["return_origins"], "c": club_id})
    if existing:
        return club_id, "club existed — login issuers and return origins updated, nothing else touched"

    prod = s.execute(text("INSERT INTO billing.product (club_id, kind, name, active) "
                          "VALUES (:c, 'court_booking', 'Court hire', true) RETURNING id"),
                     {"c": club_id}).scalar_one()
    for mins in p["lengths"]:
        s.execute(text("INSERT INTO billing.price (club_id, product_id, audience, amount_minor, "
                       "currency_code, duration_minutes, active) "
                       "VALUES (:c, :p, 'any', :a, :cur, :m, true)"),
                  {"c": club_id, "p": prod, "a": int(p["price_minor"] * mins / 60), "cur": p["currency"],
                   "m": mins})
    for n in range(1, p["courts"] + 1):
        rid = s.execute(text("INSERT INTO diary.resource (club_id, kind, name, surface, rank, product_id) "
                             "VALUES (:c, 'court', :n, 'hard', :r, :p) RETURNING id"),
                        {"c": club_id, "n": f"Court {n}", "r": n, "p": prod}).scalar_one()
        for wd in range(7):
            s.execute(text("INSERT INTO diary.availability_rule (club_id, resource_id, weekday, "
                           "start_time, end_time, slot_minutes) VALUES (:c, :r, :w, :o, :e, 30)"),
                      {"c": club_id, "r": rid, "w": wd, "o": p["open"], "e": p["close"]})
    if p["admin_email"]:
        uid = s.execute(text("SELECT id FROM iam.user WHERE lower(email) = lower(:e)"),
                        {"e": p["admin_email"]}).scalar()
        if uid:
            s.execute(text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
                           "VALUES (:c, :u, 'club_admin', 'active') ON CONFLICT DO NOTHING"),
                      {"c": club_id, "u": uid})
    return club_id, "created"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Create a club whose players book through the public API (dry run by default).")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--issuer", action="append", default=[],
                    help="an outside login service (JWT iss) whose users may book here")
    ap.add_argument("--return-origin", action="append", default=[],
                    help="scheme://host a partner's checkout may send players back to")
    ap.add_argument("--contact-email", required=True, help="From/Reply-To for the club's emails")
    ap.add_argument("--courts", type=int, default=2)
    ap.add_argument("--price-minor", type=int, default=15000, help="price of 60 minutes, in cents")
    ap.add_argument("--lengths", default="60,90", help="bookable lengths in minutes, comma-separated")
    ap.add_argument("--hours", default="06:00-21:00", help="opening hours every day, HH:MM-HH:MM")
    ap.add_argument("--currency", default="ZAR")
    ap.add_argument("--timezone", default="Africa/Johannesburg")
    ap.add_argument("--admin-email", default=None, help="an EXISTING user to make club admin")
    ap.add_argument("--commit", action="store_true", help="write it (default: dry run)")
    a = ap.parse_args(argv)
    p = _plan(a)
    print("PLAN:")
    for k, v in p.items():
        print(f"  {k:15} {v}")
    with session_scope() as s:
        club_id, what = provision(s, p)
        print(f"\nclub {p['slug']} → {club_id}: {what}")
        if not a.commit:
            s.rollback()
            print("DRY RUN — rolled back. Re-run with --commit to write it.")
            return 0
    print("COMMITTED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

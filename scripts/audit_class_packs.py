# scripts/audit_class_packs.py — list CLASS packs with no coach attached (legacy, pre-2026-07-09).
#
# From 2026-07-09 a lesson OR class pack must be tied to the coach who sold it (the coach gets paid).
# Existing class packs/plans were coach-AGNOSTIC (coach_user_id NULL) and pay no coach. This read-only
# report lists them so you can decide case-by-case: assign a coach in Setup (new plans), or let live
# wallets run out. It writes NOTHING and changes no money.
#
# Run in the Render shell on courtflow-api (DATABASE_URL is already set):  python scripts/audit_class_packs.py
# (or locally with a .env.local DATABASE_URL=...). Never prints the password.

import io
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


def _load_env():
    envf = os.path.join(REPO, ".env.local")
    if os.path.isfile(envf) and not os.getenv("DATABASE_URL"):
        for line in io.open(envf, encoding="utf-8"):
            line = line.strip()
            if line.startswith("DATABASE_URL=") and not line.startswith("#"):
                os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    import urllib.parse
    p = urllib.parse.urlparse(os.getenv("DATABASE_URL") or "")
    return "%s / %s" % (p.hostname or "?", (p.path or "").lstrip("/") or "?")


def main():
    where = _load_env()
    if not os.getenv("DATABASE_URL"):
        print("No DATABASE_URL found (set it, or add a .env.local with DATABASE_URL=...).")
        return 2
    print("DB: %s\n" % where)

    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from db import get_engine

    s = Session(get_engine())
    try:
        # (1) Catalogue plans that are class + coachless (these will be rejected on next edit/create).
        plans = s.execute(text(
            "SELECT id, label, sessions_count, price_minor, status "
            "FROM billing.bundle_plan "
            "WHERE service_kind = 'class' AND coach_user_id IS NULL AND status <> 'retired' "
            "ORDER BY created_at DESC")).mappings().all()
        print("Coachless CLASS plans (catalogue) — assign a coach in Setup or retire: %d" % len(plans))
        for p in plans:
            print("  - %-28s %s sessions · R%s · %s"
                  % ((p["label"] or "(no label)")[:28], p["sessions_count"],
                     (p["price_minor"] or 0) // 100, p["status"]))
        print()

        # (2) LIVE coachless class WALLETS held by clients (active balance) — pay no coach on draw.
        wallets = s.execute(text(
            "SELECT tw.id, u.email, u.first_name, u.surname, tw.minutes_remaining, tw.expires_at "
            "FROM billing.token_wallet tw JOIN iam.\"user\" u ON u.id = tw.user_id "
            "WHERE tw.service_kind = 'class' AND tw.coach_user_id IS NULL AND tw.status = 'active' "
            "AND COALESCE(tw.minutes_remaining,0) > 0 "
            "ORDER BY tw.expires_at NULLS LAST")).mappings().all()
        print("Live coachless CLASS wallets (held by clients, still usable): %d" % len(wallets))
        for w in wallets:
            name = ((w["first_name"] or "") + " " + (w["surname"] or "")).strip() or "—"
            print("  - %-26s %-22s %s min left · expires %s"
                  % ((w["email"] or "—")[:26], name[:22], w["minutes_remaining"],
                     w["expires_at"] or "never"))
        print()
        if not plans and not wallets:
            print("Nothing coachless — every class pack is already tied to a coach. ✅")
        else:
            print("These are LEGACY (pre-2026-07-09). New class packs now require a coach and pay them.")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())

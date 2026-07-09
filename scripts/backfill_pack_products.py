# scripts/backfill_pack_products.py — tie EXISTING packs to their specific service (product_id).
#
# From 2026-07-09 a pack (billing.bundle_plan) + wallet (token_wallet) carry product_id = the exact
# service it belongs to (Private vs Semi-private, Clay vs Hardcourt). New packs get it automatically;
# this maps your EXISTING packs. Rule: a plan whose (kind, coach) resolves to EXACTLY ONE active
# product is set to it; a wallet inherits its plan's product. AMBIGUOUS ones (a coach with two
# lesson services, or a legacy court pack now that there are several court services) are LEFT NULL
# and REPORTED — assign those by editing the pack under its service.
#
# READ-ONLY preview by default (writes nothing). To apply:  python scripts/backfill_pack_products.py --commit
# Idempotent — only touches product_id-NULL rows; safe to re-run. Never prints the DB password.
#
# Run in the Render shell on courtflow-api (DATABASE_URL is set):  python scripts/backfill_pack_products.py

import argparse
import io
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

_PRODUCT_KIND = {"court": "court_booking", "lesson": "lesson", "class": "class"}


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


def _resolve_product(s, text, club_id, service_kind, coach_user_id):
    """The single active product for (kind, coach) → its id, or None (zero or many = ambiguous)."""
    ids = s.execute(
        text("SELECT id FROM billing.product WHERE club_id = :c AND active = true "
             "AND kind = :k "
             "AND (coach_user_id = :co OR (:co IS NULL AND coach_user_id IS NULL)) LIMIT 2"),
        {"c": club_id, "k": _PRODUCT_KIND.get(service_kind, service_kind), "co": coach_user_id},
    ).scalars().all()
    return str(ids[0]) if len(ids) == 1 else None


def main():
    ap = argparse.ArgumentParser(description="Backfill product_id on existing packs/wallets.")
    ap.add_argument("--commit", action="store_true", help="Apply the changes (default = preview only).")
    args = ap.parse_args()

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
        plans = s.execute(text(
            "SELECT id, club_id, service_kind, coach_user_id, label, sessions_count "
            "FROM billing.bundle_plan WHERE product_id IS NULL AND status <> 'retired' "
            "ORDER BY club_id, service_kind")).mappings().all()
        resolved, ambiguous = [], []
        for p in plans:
            pid = _resolve_product(s, text, p["club_id"], p["service_kind"],
                                   str(p["coach_user_id"]) if p["coach_user_id"] else None)
            (resolved if pid else ambiguous).append((p, pid))

        print("Legacy packs with no service (product_id NULL): %d total\n" % len(plans))
        print("  Will be ASSIGNED (kind+coach → exactly one service): %d" % len(resolved))
        for p, pid in resolved:
            print("    - %-26s %-7s → product %s" % ((p["label"] or "(no label)")[:26], p["service_kind"], pid[:8]))
        print("\n  AMBIGUOUS (assign manually under the service): %d" % len(ambiguous))
        for p, _ in ambiguous:
            print("    - %-26s %-7s coach=%s (%s services match — pick one in the service editor)"
                  % ((p["label"] or "(no label)")[:26], p["service_kind"],
                     str(p["coach_user_id"])[:8] if p["coach_user_id"] else "—", "0 or >1"))
        print()

        if not args.commit:
            print("Preview only. Re-run with --commit to assign the %d resolvable pack(s) + their wallets."
                  % len(resolved))
            return 0

        # Assign resolvable plans, then let each wallet inherit its plan's product.
        n_plan = 0
        for p, pid in resolved:
            s.execute(text("UPDATE billing.bundle_plan SET product_id = :pid WHERE id = :id "
                           "AND product_id IS NULL"), {"pid": pid, "id": str(p["id"])})
            n_plan += 1
        # Wallets inherit the (now-set) product of their bundle_plan.
        wres = s.execute(text(
            "UPDATE billing.token_wallet w SET product_id = bp.product_id "
            "FROM billing.bundle_plan bp "
            "WHERE w.bundle_plan_id = bp.id AND w.product_id IS NULL AND bp.product_id IS NOT NULL"))
        s.commit()
        print("Assigned %d plan(s); %d wallet(s) inherited their plan's service." % (n_plan, wres.rowcount or 0))
        if ambiguous:
            print("%d ambiguous pack(s) left unassigned — open each under its service to set it." % len(ambiguous))
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())

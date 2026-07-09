# scripts/backfill_pack_products.py — tie EXISTING packs to their specific service (product_id),
# and diagnose the ones that can't be mapped automatically.
#
# From 2026-07-09 a pack (billing.bundle_plan) + wallet (token_wallet) carry product_id = the exact
# service it belongs to. New packs get it automatically; this maps EXISTING packs. A legacy pack is
# resolved by its (kind, coach): if exactly ONE active service matches, it's assigned (and its wallets
# inherit it). The rest are diagnosed into:
#   ORPHAN  — 0 active services match → the pack's service was DELETED/RETIRED. If no client still
#             holds it (0 active wallets) it's dead → --retire-dead cleans it up. If clients DO hold
#             it, it's flagged LOUDLY (they paid for a service that's gone — a refund/credit decision).
#   AMBIGUOUS — >1 active services match (e.g. a coach with Private + Semi) → assign it by hand by
#             opening the service in the editor and adding the pack there.
#
# READ-ONLY preview by default. Actions:
#   python scripts/backfill_pack_products.py                # preview + diagnose (writes nothing)
#   python scripts/backfill_pack_products.py --commit       # assign the resolvable packs + their wallets
#   python scripts/backfill_pack_products.py --retire-dead  # retire ORPHAN packs that no client holds
# Idempotent; safe to re-run. Never prints the DB password. Run in the Render shell on courtflow-api.

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


def main():
    ap = argparse.ArgumentParser(description="Backfill/diagnose product_id on existing packs.")
    ap.add_argument("--commit", action="store_true", help="Assign the resolvable packs (+ their wallets).")
    ap.add_argument("--retire-dead", action="store_true",
                    help="Retire ORPHAN packs (service deleted) that no client still holds.")
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
            "ORDER BY service_kind, coach_user_id")).mappings().all()

        def candidates(club_id, kind, coach):
            return s.execute(text(
                "SELECT id, name FROM billing.product WHERE club_id = :c AND active = true AND kind = :k "
                "AND (coach_user_id = :co OR (:co IS NULL AND coach_user_id IS NULL)) ORDER BY name"),
                {"c": club_id, "k": _PRODUCT_KIND.get(kind, kind), "co": coach}).mappings().all()

        def active_wallets(plan_id):
            return s.execute(text(
                "SELECT count(*) FROM billing.token_wallet WHERE bundle_plan_id = :p "
                "AND status = 'active' AND COALESCE(minutes_remaining, 0) > 0"),
                {"p": str(plan_id)}).scalar() or 0

        def coach_name(coach):
            if not coach:
                return "—"
            r = s.execute(text('SELECT COALESCE(cp.display_name, NULLIF(TRIM(u.first_name||\' \'||'
                               'COALESCE(u.surname,\'\')),\'\'), u.email) AS n FROM iam."user" u '
                               'LEFT JOIN iam.coach_profile cp ON cp.user_id = u.id WHERE u.id = :u'),
                          {"u": str(coach)}).scalar()
            return r or str(coach)[:8]

        resolvable, orphans, ambiguous = [], [], []
        for p in plans:
            coach = str(p["coach_user_id"]) if p["coach_user_id"] else None
            cands = candidates(p["club_id"], p["service_kind"], coach)
            held = active_wallets(p["id"])
            rec = (p, cands, held, coach)
            if len(cands) == 1:
                resolvable.append(rec)
            elif len(cands) == 0:
                orphans.append(rec)
            else:
                ambiguous.append(rec)

        print("Legacy packs with no service: %d  (%d resolvable · %d ORPHAN(deleted service) · %d ambiguous)\n"
              % (len(plans), len(resolvable), len(orphans), len(ambiguous)))

        if resolvable:
            print("RESOLVABLE — one live service matches, will be ASSIGNED on --commit:")
            for p, cands, held, _ in resolvable:
                print("  - %-24s %-6s → %s  (%d client wallet%s)"
                      % ((p["label"] or "(no label)")[:24], p["service_kind"], cands[0]["name"],
                         held, "" if held == 1 else "s"))
            print()

        if orphans:
            print("ORPHAN — NO live service for this coach+kind → the service was DELETED/RETIRED:")
            for p, _, held, coach in orphans:
                tag = ("DEAD — no client holds it → retire it" if held == 0
                       else "⚠ %d client wallet(s) still hold it — refund/credit, do NOT just retire" % held)
                print("  - %-24s %-6s coach=%-18s %s"
                      % ((p["label"] or "(no label)")[:24], p["service_kind"], coach_name(coach)[:18], tag))
            print()

        if ambiguous:
            print("AMBIGUOUS — several live services match; assign by opening the service and adding the pack:")
            for p, cands, held, coach in ambiguous:
                print("  - %-24s %-6s coach=%-18s → one of: %s"
                      % ((p["label"] or "(no label)")[:24], p["service_kind"], coach_name(coach)[:18],
                         ", ".join(c["name"] for c in cands)))
            print()

        did = False
        if args.commit and resolvable:
            for p, cands, _, _ in resolvable:
                s.execute(text("UPDATE billing.bundle_plan SET product_id = :pid WHERE id = :id "
                               "AND product_id IS NULL"), {"pid": str(cands[0]["id"]), "id": str(p["id"])})
            wres = s.execute(text(
                "UPDATE billing.token_wallet w SET product_id = bp.product_id FROM billing.bundle_plan bp "
                "WHERE w.bundle_plan_id = bp.id AND w.product_id IS NULL AND bp.product_id IS NOT NULL"))
            s.commit(); did = True
            print("ASSIGNED %d plan(s); %d wallet(s) inherited their plan's service." % (len(resolvable), wres.rowcount or 0))

        if args.retire_dead:
            dead = [p for p, _, held, _ in orphans if held == 0]
            for p in dead:
                s.execute(text("UPDATE billing.bundle_plan SET status = 'retired', active = false "
                               "WHERE id = :id"), {"id": str(p["id"])})
            s.commit(); did = True
            print("RETIRED %d dead orphan pack(s) (deleted service, no client holds them)." % len(dead))
            live_orphans = [p for p, _, held, _ in orphans if held > 0]
            if live_orphans:
                print("Left %d orphan(s) that clients STILL hold — decide a refund/credit for those." % len(live_orphans))

        if not did:
            print("Preview only. --commit assigns the resolvable; --retire-dead retires the dead orphans.")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())

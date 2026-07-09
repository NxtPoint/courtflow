# scripts/cleanup_coachless_classes.py — retire LEGACY coachless classes that carry no money.
#
# WHY: before 2026-07-09 a class could exist with no coach (coach_user_id NULL). A class lives in TWO
# places — the schedulable diary type (diary.resource kind='class') AND the Services & pricing catalogue
# row (billing.product kind='class') — linked by name. A coachless class pays no coach and, after the
# lockstep fix, is the one that can show blank in Services. This tool RETIRES the empty ones so they stop
# cluttering Diary + Services and can't be edited into the old broken state again.
#
# SAFE BY CONSTRUCTION — it is a SOFT retire (never a hard delete, matching the platform's soft-delete
# rule) and it SKIPS anything that touches money or people:
#   * skips a class with ANY live seat (a non-cancelled enrolment on any session, past or future),
#   * skips a class whose catalogue product still has a LIVE prepaid wallet (active, balance > 0).
# Eligible classes are retired by REUSING the app's own paths: cancel_session (frees held courts +
# voids/credits — a no-op for an empty class) then diary.resource.is_active=false; the matching coachless
# product is set status='terminated' via services.set_service_status. Everything is reversible (un-hide).
#
# DRY-RUN by default (prints the plan, ROLLS BACK). Add --commit to write. Idempotent — re-running after a
# commit finds nothing.
#
#   python scripts/cleanup_coachless_classes.py            # preview only (safe)
#   python scripts/cleanup_coachless_classes.py --commit   # actually retire the eligible ones

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


def _seats(session, *, class_session_ids):
    """Count non-cancelled enrolments (real seats) across a set of session ids."""
    if not class_session_ids:
        return 0
    from sqlalchemy import text
    return session.execute(
        text("SELECT count(*) FROM diary.enrolment "
             "WHERE class_session_id = ANY(:ids) AND status <> 'cancelled'"),
        {"ids": list(class_session_ids)},
    ).scalar() or 0


def _live_wallet_minutes(session, *, club_id, product_id):
    """Minutes still sitting in ACTIVE prepaid wallets tied to this catalogue product (money we must
    not orphan). 0 when the product is None or has no live wallet."""
    if not product_id:
        return 0
    from sqlalchemy import text
    return session.execute(
        text("SELECT COALESCE(sum(COALESCE(minutes_remaining, 0)), 0) FROM billing.token_wallet "
             "WHERE club_id = :c AND product_id = :p AND status = 'active' "
             "  AND COALESCE(minutes_remaining, 0) > 0"),
        {"c": club_id, "p": product_id},
    ).scalar() or 0


def _process_club(session, *, club_id, commit):
    from sqlalchemy import text
    from diary.classes import cancel_session
    from services.repositories import set_service_status

    retired_resources = retired_products = 0

    # ---- (A) coachless class TYPES in the diary (schedulable) -------------------------------------
    resources = session.execute(
        text("SELECT id, name FROM diary.resource "
             "WHERE club_id = :c AND kind = 'class' AND coach_user_id IS NULL AND is_active = true "
             "ORDER BY name"),
        {"c": club_id},
    ).mappings().all()

    for r in resources:
        rid, name = r["id"], r["name"] or "(unnamed)"
        sess_ids = session.execute(
            text("SELECT id FROM diary.class_session WHERE club_id = :c AND resource_id = :r"),
            {"c": club_id, "r": rid},
        ).scalars().all()
        seats = _seats(session, class_session_ids=sess_ids)
        # its catalogue product (coachless, same name) — for the live-wallet guard
        prod_id = session.execute(
            text("SELECT id FROM billing.product WHERE club_id = :c AND kind = 'class' "
                 "AND coach_user_id IS NULL AND lower(name) = lower(:n) AND active = true "
                 "ORDER BY created_at LIMIT 1"),
            {"c": club_id, "n": name},
        ).scalar()
        wallet = _live_wallet_minutes(session, club_id=club_id, product_id=prod_id)

        if seats > 0 or wallet > 0:
            why = []
            if seats > 0:
                why.append("%d live seat(s)" % seats)
            if wallet > 0:
                why.append("%d wallet min" % wallet)
            print("  SKIP  class '%s' — %s (needs manual review)" % (name[:34], ", ".join(why)))
            continue

        future = session.execute(
            text("SELECT id FROM diary.class_session WHERE club_id = :c AND resource_id = :r "
                 "AND status <> 'cancelled' AND starts_at >= now()"),
            {"c": club_id, "r": rid},
        ).scalars().all()
        print("  RETIRE class '%s' — %d empty session(s) (%d upcoming), no coach, no money"
              % (name[:34], len(sess_ids), len(future)))
        if commit:
            for sid in future:
                cancel_session(session, club_id=club_id, session_id=sid)  # frees held courts
            session.execute(
                text("UPDATE diary.resource SET is_active = false, updated_at = now() "
                     "WHERE club_id = :c AND id = :r"),
                {"c": club_id, "r": rid},
            )
            retired_resources += 1

    # ---- (B) coachless class CATALOGUE rows (Services & pricing) --------------------------------
    products = session.execute(
        text("SELECT id, name FROM billing.product "
             "WHERE club_id = :c AND kind = 'class' AND coach_user_id IS NULL AND active = true "
             "ORDER BY name"),
        {"c": club_id},
    ).mappings().all()

    for p in products:
        pid, name = p["id"], p["name"] or "(unnamed)"
        wallet = _live_wallet_minutes(session, club_id=club_id, product_id=pid)
        # seats booked against any same-named coachless class session
        sess_ids = session.execute(
            text("SELECT cs.id FROM diary.class_session cs JOIN diary.resource r ON r.id = cs.resource_id "
                 "WHERE cs.club_id = :c AND r.kind = 'class' AND lower(r.name) = lower(:n)"),
            {"c": club_id, "n": name},
        ).scalars().all()
        seats = _seats(session, class_session_ids=sess_ids)
        if wallet > 0 or seats > 0:
            why = []
            if seats > 0:
                why.append("%d live seat(s)" % seats)
            if wallet > 0:
                why.append("%d wallet min" % wallet)
            print("  SKIP  service '%s' — %s (needs manual review)" % (name[:34], ", ".join(why)))
            continue
        print("  RETIRE service '%s' — coachless catalogue row, no money" % name[:34])
        if commit:
            set_service_status(session, club_id=club_id, product_id=pid, status="terminated")
            retired_products += 1

    return retired_resources, retired_products


def main():
    commit = "--commit" in sys.argv[1:]
    where = _load_env()
    if not os.getenv("DATABASE_URL"):
        print("No DATABASE_URL found (set it, or add a .env.local with DATABASE_URL=...).")
        return 2
    print("DB: %s   mode: %s\n" % (where, "COMMIT (writing)" if commit else "DRY-RUN (rollback)"))

    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from db import get_engine

    s = Session(get_engine())
    try:
        clubs = s.execute(
            text("SELECT id, name FROM club.club WHERE COALESCE(is_template, false) = false ORDER BY name")
        ).mappings().all()
        total_r = total_p = 0
        for c in clubs:
            print("Club: %s" % (c["name"] or c["id"]))
            rr, pp = _process_club(s, club_id=c["id"], commit=commit)
            total_r += rr
            total_p += pp
            print()

        if commit:
            s.commit()
            print("Committed. Retired %d class type(s) + %d catalogue row(s)." % (total_r, total_p))
        else:
            s.rollback()
            print("DRY-RUN — nothing written. Re-run with --commit to retire the RETIRE-marked items above.")
        return 0
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())

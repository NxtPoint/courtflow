# scripts/klaviyo_membership_backfill.py — one-time: clear `on_trial` for members who ALREADY
# converted, and fire `membership_started` for them so Klaviyo's converter segments are truthful.
#
# WHY THIS EXISTS. `membership_started` used to be emitted only from apply_payment_event's
# `subscription_active` branch, which NOTHING produces (NextPoint sells one-off membership orders,
# not provider-managed subscriptions) — so it NEVER fired, and the `on_trial=false` conversion flip in
# marketing_crm/crm_sync/sync.py (which only runs on that event) never ran either. The code fix lives
# in billing.membership.emit_membership_started and is FORWARD-ONLY: it fires for people who convert
# from now on. Everyone who converted BEFORE the fix still shows `on_trial=true` with zero
# membership_started, so Klaviyo's "Unconverted trial" segment (XxUZCt) still contains PAYING members.
# This is the backfill that closes that gap. See docs/specs/KLAVIYO-MASTER-PLAN.md §7f.
#
# ⚠️ Run this BEFORE sending anything to the Unconverted-trial segment, or converted members get a
#    "you haven't converted yet" pitch.
#
# DRY-RUN BY DEFAULT — reports the cohort, pushes NOTHING. Pass --commit to sync + fire the event.
#
#   .venv/Scripts/python -m scripts.klaviyo_membership_backfill            # report who'd be fixed
#   .venv/Scripts/python -m scripts.klaviyo_membership_backfill --commit   # push to Klaviyo
#
# COHORT = members holding a REAL (non-trial) membership_subscription that is active now. Trials are
# excluded by `provider <> 'trial'` — a free week is not a conversion, and clearing on_trial for a
# current trialist would destroy the trial cohort. `membership_started` here is TRANSACTIONAL-ish
# (a factual correction to their own record), but it is gated on the same marketing consent the live
# emit is, because it feeds marketing segmentation — profiles are only upserted, never subscribed.

import argparse
import os
import sys
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


def _hdr(t):
    print(f"\n== {t} " + "=" * max(0, 60 - len(t)))


def _row(label, value, width=44):
    print(f"   {label:<{width}} {value}")


# Members on a REAL membership right now. DISTINCT ON keeps one row per member (someone can hold
# more than one subscription row — e.g. a superseded trial plus the paid plan they upgraded to).
_COHORT_SQL = """
SELECT DISTINCT ON (u.email)
       u.email, ms.club_id, ms.current_period_end, ms.provider
FROM billing.membership_subscription ms
JOIN iam."user" u ON u.id = ms.user_id
WHERE ms.status = 'active'
  AND COALESCE(ms.provider, '') <> 'trial'
  AND (ms.current_period_end IS NULL OR ms.current_period_end >= CURRENT_DATE)
  AND u.email IS NOT NULL AND btrim(u.email) <> ''
ORDER BY u.email, ms.current_period_end DESC NULLS LAST
"""


def main():
    ap = argparse.ArgumentParser(
        description="Backfill on_trial=false + membership_started for already-converted members.")
    ap.add_argument("--commit", action="store_true", help="push to Klaviyo (default: report only)")
    ap.add_argument("--limit", type=int, default=0, help="cap the cohort (0 = no cap; useful for a test run)")
    args = ap.parse_args()

    _load_env_local()
    import db
    from sqlalchemy import text
    from marketing_crm.crm_sync import sync as SYNC, klaviyo

    with db.session_scope() as s:
        rows = s.execute(text(_COHORT_SQL)).mappings().all()
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]

    _hdr("CONVERTED-MEMBER COHORT (on_trial -> false)")
    _row("members on a real membership", len(rows))
    key_set = klaviyo.enabled()
    _row("KLAVIYO_API_KEY present", "yes" if key_set else "NO — set it in Render first")
    _row("mode", "COMMIT (push)" if args.commit else "DRY-RUN (report only)")
    for r in rows[:10]:
        end = r["current_period_end"]
        _row(f"  {r['email']}", f"{r['provider'] or 'n/a'} · until {end if end else 'open-ended'}")
    if len(rows) > 10:
        _row("  …", f"+{len(rows) - 10} more")

    if not args.commit:
        print("\n>>> DRY-RUN — nothing pushed. Re-run with --commit (KLAVIYO_API_KEY set) to sync.\n")
        sys.exit(0)
    if not key_set:
        print("\n!! --commit given but KLAVIYO_API_KEY not set — nothing pushed.\n")
        sys.exit(1)

    pushed = 0
    for r in rows:
        email = r["email"]
        end = r["current_period_end"]
        period_end = end.isoformat() if hasattr(end, "isoformat") else (str(end) if end else None)
        try:
            with db.session_scope() as s:
                traits = SYNC.build_traits(s, email, club_id=r["club_id"]) or {"email": email}
            # THE POINT of this script: they converted, so they are not a trialist any more.
            traits["on_trial"] = False
            klaviyo.upsert_profile(traits)
            klaviyo.track_event(email, "membership_started", {
                "provider": r["provider"], "current_period_end": period_end, "backfill": True})
            pushed += 1
        except Exception as e:
            print(f"   !! {email}: {e.__class__.__name__}")

    _hdr("RESULT")
    _row("profiles corrected + membership_started fired", pushed)
    print("\n>>> DONE. Re-check the Unconverted-trial segment (XxUZCt) — it should now exclude these\n"
          "    members. Only send that segment's offer AFTER this reads clean.\n")
    sys.exit(0)


if __name__ == "__main__":
    main()

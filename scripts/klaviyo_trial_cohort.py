# scripts/klaviyo_trial_cohort.py — one-time: sync the CURRENT 7-day-trial members to Klaviyo and
# fire `trial_started` so the trial-conversion flow (built by Cowork) reaches them. Going forward,
# auth/principal.py emits trial_started automatically at grant; this is the backfill for members whose
# trial was granted BEFORE that emit shipped (e.g. the ~31 activated this week).
#
# DRY-RUN BY DEFAULT — reports the cohort, pushes NOTHING. Pass --commit to sync + fire the event.
#
#   .venv/Scripts/python -m scripts.klaviyo_trial_cohort               # report the trial cohort
#   .venv/Scripts/python -m scripts.klaviyo_trial_cohort --commit      # sync + fire trial_started
#
# "Trial cohort" = members with a provider='trial' membership_subscription whose window ends within
# the last/next `--window` days (default 14 — catches active + just-ended trials this week).
# trial_started is TRANSACTIONAL (service comms about their own trial), so no marketing consent needed.

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


_COHORT_SQL = """
SELECT u.email, ms.club_id, ms.current_period_end
FROM billing.membership_subscription ms
JOIN iam.user u ON u.id = ms.user_id
WHERE ms.provider = 'trial'
  AND u.email IS NOT NULL AND btrim(u.email) <> ''
  AND ms.current_period_end >= (CURRENT_DATE - make_interval(days => :w))
ORDER BY ms.current_period_end
"""


def main():
    ap = argparse.ArgumentParser(description="Sync the current trial cohort + fire trial_started.")
    ap.add_argument("--commit", action="store_true", help="push to Klaviyo (default: report only)")
    ap.add_argument("--window", type=int, default=14, help="days around trial end to include (default 14)")
    args = ap.parse_args()

    _load_env_local()
    import db
    from sqlalchemy import text
    from marketing_crm.crm_sync import sync as SYNC, klaviyo

    with db.session_scope() as s:
        rows = s.execute(text(_COHORT_SQL), {"w": int(args.window)}).mappings().all()

    _hdr("TRIAL COHORT")
    _row("trial members in window", len(rows))
    key_set = klaviyo.enabled()
    _row("KLAVIYO_API_KEY present", "yes" if key_set else "NO — set it in Render first")
    _row("mode", "COMMIT (push)" if args.commit else "DRY-RUN (report only)")
    for r in rows[:10]:
        end = r["current_period_end"]
        _row(f"  {r['email']}", f"trial ends {end}")
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
        trial_ends = end.isoformat() if hasattr(end, "isoformat") else str(end)
        try:
            with db.session_scope() as s:
                traits = SYNC.build_traits(s, email, club_id=r["club_id"]) or {"email": email}
            traits["on_trial"] = True
            traits["trial_ends_at"] = trial_ends
            klaviyo.upsert_profile(traits)
            klaviyo.track_event(email, "trial_started", {"trial_ends_at": trial_ends})
            pushed += 1
        except Exception as e:
            print(f"   !! {email}: {e.__class__.__name__}")

    _hdr("RESULT")
    _row("profiles synced + trial_started fired", pushed)
    print("\n>>> DONE. In Klaviyo, Cowork's flow triggers on the 'trial_started' metric.\n")
    sys.exit(0)


if __name__ == "__main__":
    main()

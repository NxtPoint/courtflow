# crons/trigger.py — the universal thin cron trigger (1050 pattern, docs/10 §7).
#
# A Render cron runs `python -m crons.trigger <job>`. This makes ONE authenticated POST
# to the API and exits non-zero on failure (so Render surfaces it). All real logic lives
# behind the API endpoint — the cron carries no business logic and no DB access.
#
# Endpoints are owned by the lanes that need them (B-Diary: reminders/capacity-sweep/
# membership-refill; C-Billing: monthly-invoice). Until an endpoint exists this trigger
# simply reports the API's response (e.g. 404) and the job is a visible no-op.
#
# Env: CRON_API_BASE (e.g. https://api.courtflow.app), OPS_KEY (server-to-server guard).

import os
import sys

# job name -> API path (POST). Lanes add/own the handlers.
JOB_ROUTES = {
    "reminders":         "/api/cron/reminders",
    "capacity-sweep":    "/api/cron/capacity-sweep",
    # monthly-invoice was retired with the account_ledger monthly tab (the unified statement is the
    # single debt of record; coaches issue per-client month-end statements from their console).
    "membership-refill": "/api/cron/membership-refill",
}


def run(job):
    base = (os.getenv("CRON_API_BASE") or "").rstrip("/")
    ops_key = os.getenv("OPS_KEY") or ""
    path = JOB_ROUTES.get(job)
    if not path:
        print(f"cron: unknown job '{job}' (known: {', '.join(sorted(JOB_ROUTES))})", file=sys.stderr)
        return 2
    if not base:
        print("cron: CRON_API_BASE not set", file=sys.stderr)
        return 2

    import requests  # lazy (only needed at run, keeps import clean)
    url = base + path
    try:
        resp = requests.post(url, headers={"X-Ops-Key": ops_key}, timeout=120)
        print(f"cron {job}: POST {url} -> {resp.status_code}")
        return 0 if resp.ok else 1
    except Exception as e:
        print(f"cron {job}: request failed: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 1


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: python -m crons.trigger <job>", file=sys.stderr)
        return 2
    return run(argv[0])


if __name__ == "__main__":
    sys.exit(main())

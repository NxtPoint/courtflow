# app.py — CourtFlow / NextPoint API Flask app factory + boot init order.
#
# Mirrors 1050's upload_app.py boot discipline: each boot step is individually
# try/except-wrapped so one failing module can't stop the service from starting
# (a fresh DB / a not-yet-built lane should degrade, not crash). The schema runner
# (db.run_boot_init) is itself per-module fault-tolerant.
#
# Boot order:
#   1. create Flask app + CORS
#   2. init auth (logs Clerk/auth state; dark unless AUTH_ENABLED=1)
#   3. run_boot_init() — extensions + every registered schema module's init()
#   4. register blueprints (health now; diary/billing/crm lanes attach later)
#
# Nothing here REQUIRES a DB at import time: get_engine() is lazy, and the boot init
# is wrapped. With no DATABASE_URL the app still imports and /healthz responds.

import logging
import os

from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")


def _truthy(name, default="0"):
    return os.getenv(name, default).strip() == "1"


def create_app():
    app = Flask(__name__)

    # CORS — permissive for the SPA origins during build; tighten per-host later.
    try:
        from flask_cors import CORS
        CORS(app, supports_credentials=True)
    except Exception:
        app.logger.exception("CORS init failed on boot (continuing)")

    # --- auth (dark unless AUTH_ENABLED=1) --------------------------------
    try:
        from auth import init_auth
        init_auth(app)
    except Exception:
        app.logger.exception("auth init failed on boot (requests will fail closed)")

    # --- schema bootstrap (idempotent; per-module fault tolerant) ---------
    # Skip entirely when there is no DB configured so the app still boots for
    # local import / healthcheck (Phase-0 DoD verifies this against a real DB).
    if os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or os.getenv("DB_URL"):
        try:
            from db import run_boot_init
            results = run_boot_init()
            failed = [m for m, s in results if s == "error"]
            if failed:
                app.logger.warning("boot init: modules with errors: %s", failed)
            else:
                app.logger.info("boot init: all schema modules ok")
        except Exception:
            app.logger.exception("run_boot_init() failed on boot — schema may be incomplete")

        # Optional one-time seed of NextPoint (club #1). Handy on Render free tier, which
        # has no Shell to run `python -m scripts.seed_nextpoint`. Idempotent — safe to leave
        # on, but you can remove SEED_NEXTPOINT once you see "seeded" in the logs.
        if _truthy("SEED_NEXTPOINT"):
            try:
                from db import session_scope
                from scripts.seed_nextpoint import seed as _seed_nextpoint
                with session_scope() as s:
                    summary = _seed_nextpoint(s)
                app.logger.info("SEED_NEXTPOINT: seeded NextPoint -> %s", summary)
            except Exception:
                app.logger.exception("SEED_NEXTPOINT seed failed on boot")
    else:
        app.logger.warning("DATABASE_URL not set — skipping schema bootstrap (import-only mode)")

    # --- blueprints -------------------------------------------------------
    _register_health(app)
    # Lanes attach their blueprints here as they land (each try/except-wrapped):
    #   B-Diary: diary.routes ;  C-Billing: billing.routes ;  D-CRM: marketing_crm.*
    _try_register(app, "diary.routes", "diary_bp")          # B: /api/diary/*
    _try_register(app, "diary.routes", "cron_bp")           # B: /api/cron/* (reminders, sweep)
    _try_register(app, "billing.routes", "billing_bp")      # C: /api/billing/* + monthly-invoice cron
    _try_register(app, "marketing_crm.tracking", "page_bp")            # D: POST /api/track/page
    _try_register(app, "marketing_crm.consent.blueprint", "consent_bp")    # D: /api/consent/*
    _try_register(app, "marketing_crm.backoffice.blueprint", "cockpit_bp")  # D: /api/admin/cockpit/*
    _try_register(app, "admin.routes", "admin_bp")          # Admin: /api/admin/* (onboarding + settings)
    _try_register(app, "coach.routes", "coach_bp")          # Coach: /api/coach/* (self-service profile/hours/services)

    return app


def _register_health(app):
    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", service="courtflow-api"), 200

    @app.get("/api/whoami")
    def whoami():
        """Resolve the caller's principal (club-scoped). Returns 401 if unauthenticated.
        This is the endpoint the Phase-0 DoD exercises: a Clerk JWT -> principal with
        club_id + role."""
        from flask import request
        from auth import resolve_principal
        p = resolve_principal(request)
        if p is None or not p.authenticated:
            return jsonify(error="unauthorized"), 401
        return jsonify(
            user_id=p.user_id, club_id=p.club_id, role=p.role,
            email=p.email, method=p.method,
        ), 200


def _try_register(app, module_path, attr):
    """Register a lane blueprint if its module exists yet (B/C/D lanes). Silent no-op
    until the lane lands — keeps Agent A's app booting before fan-out."""
    try:
        mod = __import__(module_path, fromlist=[attr])
        bp = getattr(mod, attr, None)
        if bp is not None:
            app.register_blueprint(bp)
            app.logger.info("registered blueprint: %s.%s", module_path, attr)
    except ImportError:
        pass  # lane not built yet
    except Exception:
        app.logger.exception("blueprint register failed: %s.%s", module_path, attr)


# Module-level app for gunicorn (wsgi:app imports this).
app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=_truthy("FLASK_DEBUG"))

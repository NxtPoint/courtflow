# auth — per-user IdP (Clerk) token auth + club-scoped principal resolution.
#
# Ported from 1050 auth_v2/. DARK BY DEFAULT: nothing changes request handling unless
# AUTH_ENABLED=1, and importing the package is side-effect free.
#
# Public surface:
#   resolve_principal(request) -> Principal | None   (verify JWT, resolve club_id + role)
#   Principal                                          (user_id, club_id, role, email, method)
#   is_enabled(), provider()
#   init_auth(app)                                     (boot hook — logs auth state)

import logging

from auth.principal import Principal, resolve_principal
from auth.verifier import is_enabled, provider

__all__ = ["Principal", "resolve_principal", "is_enabled", "provider", "init_auth"]

log = logging.getLogger("auth")


def init_auth(app):
    """Boot hook — logs auth state so it's visible in Render logs. Safe to call
    unconditionally; does nothing observable when AUTH_ENABLED!=1."""
    if is_enabled():
        app.logger.info("auth ENABLED (provider=%s) — per-user Clerk JWT auth active", provider())
    else:
        app.logger.info("auth dark (AUTH_ENABLED!=1) — OPS_KEY server-to-server path only")
    return app

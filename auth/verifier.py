# auth/verifier.py — provider-agnostic JWT (OIDC) verification via JWKS.
#
# Ported near-verbatim from 1050 auth_v2/verifier.py. Verifies a per-user Clerk session
# token against the platform's NEW Clerk app (AUTH_JWKS_URL / AUTH_ISSUER). This module
# NEVER touches the DB and is fully inert unless AUTH_ENABLED=1.
#
# Clerk specifics (non-obvious, documented):
#   - Clerk's default session token sets `iss` to your Frontend API URL and does NOT set
#     `aud`. So we verify signature + iss + exp, and treat audience as OPTIONAL (only
#     enforced when AUTH_AUDIENCE is configured).
#   - The stable user id is the `sub` claim (e.g. "user_2ab..."). Map it to
#     iam.user.clerk_user_id.
#   - Email is not in the default token; add it via a Clerk JWT template (custom claim
#     `email`). We read `email` (falling back to a few common keys).
#
# Env (all blank/dark until the NEW Clerk app exists):
#   AUTH_ENABLED      "1" to enable any of this (else verify_jwt() returns None)
#   AUTH_PROVIDER     clerk | auth0 | cognito  (informational; default "clerk")
#   AUTH_JWKS_URL     JWKS endpoint, e.g. https://<frontend-api>/.well-known/jwks.json
#   AUTH_ISSUER       expected `iss`, e.g. https://<frontend-api>
#   AUTH_AUDIENCE     expected `aud` (OPTIONAL — leave blank for Clerk default tokens)
#   AUTH_EXTRA_ISSUERS comma-separated OUTSIDE login services also trusted (e.g. Ten-Fifty5's
#                     Clerk, whose players book inside Ten-Fifty5). Their JWKS is <iss>/.well-known/
#                     jwks.json. Trusting an issuer here lets its tokens VERIFY; it does not let them
#                     act anywhere — auth/principal.py confines them to clubs that list the issuer
#                     in club.policy.accepted_login_issuers.
#   AUTH_JWT_LEEWAY   clock-skew tolerance in seconds (default 30)

import logging
import os

log = logging.getLogger("auth.verifier")

# Lazily-built singletons (a JWKS client caches signing keys + does its own HTTP).
_jwks_client = None
_jwks_url_cached = None


def is_enabled():
    return os.getenv("AUTH_ENABLED", "0") == "1"


def provider():
    return (os.getenv("AUTH_PROVIDER") or "clerk").strip().lower()


def _leeway():
    try:
        return int(os.getenv("AUTH_JWT_LEEWAY", "30"))
    except ValueError:
        return 30


def looks_like_jwt(token):
    """Cheap structural check so a Bearer value can be disambiguated from an OPS_KEY
    without importing jwt. A compact JWS has three base64url segments separated by
    dots; an opaque server key does not."""
    if not token or token.count(".") != 2:
        return False
    return all(seg for seg in token.split("."))


def _get_jwks_client():
    """Build (once) a PyJWKClient for AUTH_JWKS_URL. Rebuilt if the env URL changes.
    Imports PyJWT lazily so this module imports even if the dep is somehow missing on a
    box that never enables auth."""
    global _jwks_client, _jwks_url_cached
    jwks_url = (os.getenv("AUTH_JWKS_URL") or "").strip()
    if not jwks_url:
        return None
    if _jwks_client is not None and _jwks_url_cached == jwks_url:
        return _jwks_client
    from jwt import PyJWKClient  # lazy
    # cache signing keys for an hour; PyJWKClient refetches on cache miss
    _jwks_client = PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)
    _jwks_url_cached = jwks_url
    return _jwks_client


_extra_clients = {}   # issuer -> PyJWKClient for AUTH_EXTRA_ISSUERS


def _norm_iss(v):
    return (v or "").strip().rstrip("/")


def primary_issuer():
    return _norm_iss(os.getenv("AUTH_ISSUER")) or None


def extra_issuers():
    return [i for i in (_norm_iss(x) for x in (os.getenv("AUTH_EXTRA_ISSUERS") or "").split(",")) if i]


def is_primary(claims):
    """True for the platform's OWN login (or when no issuer is configured, as in the old
    single-issuer setup). Everything else is an outside login with a confined principal."""
    p = primary_issuer()
    return p is None or _norm_iss((claims or {}).get("iss")) == p


def _unverified_iss(token):
    try:
        import jwt
        return _norm_iss(jwt.decode(token, options={"verify_signature": False}).get("iss"))
    except Exception:
        return ""


def _client_for(iss):
    """(client, expected issuer) for a token's claimed issuer, or (None, None) if untrusted.
    The claimed iss only CHOOSES which trusted key set to check against; the signature and the
    iss claim are then verified against that choice, so a forged iss simply fails."""
    primary = primary_issuer()
    if primary is None or not iss or iss == primary:
        return _get_jwks_client(), (os.getenv("AUTH_ISSUER") or "").strip() or None
    if iss in extra_issuers():
        if iss not in _extra_clients:
            from jwt import PyJWKClient
            _extra_clients[iss] = PyJWKClient(iss + "/.well-known/jwks.json",
                                              cache_keys=True, lifespan=3600)
        return _extra_clients[iss], iss
    return None, None


def verify_jwt(token):
    """Verify a compact JWS and return its claims dict, or None on any failure.
    Returns None (never raises) so callers fail closed. No-op (None) unless AUTH_ENABLED=1."""
    if not is_enabled():
        return None
    if not looks_like_jwt(token):
        return None

    audience = (os.getenv("AUTH_AUDIENCE") or "").strip() or None

    try:
        import jwt  # lazy import (PyJWT)
        client, issuer = _client_for(_unverified_iss(token))
        if client is None:
            log.info("auth: JWT from an untrusted issuer, or AUTH_JWKS_URL not set")
            return None
        signing_key = client.get_signing_key_from_jwt(token).key
        options = {
            # aud is optional for Clerk default tokens — only require it when configured
            "require": ["exp", "iss"],
            "verify_aud": audience is not None,
        }
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience,
            leeway=_leeway(),
            options=options,
        )
        return claims
    except Exception as e:  # InvalidTokenError, JWKS fetch failure, etc.
        log.info("auth: JWT verification failed: %s", e.__class__.__name__)
        return None


def claim_uid(claims):
    """The stable external user id -> iam.user.clerk_user_id. `sub` for all providers."""
    return claims.get("sub") if claims else None


def claim_email(claims):
    """Best-effort email from the token. Clerk needs a JWT template exposing `email`."""
    if not claims:
        return None
    for k in ("email", "email_address", "primary_email", "https://email"):
        v = claims.get(k)
        if v:
            return str(v).strip().lower()
    return None


def claim_email_verified(claims):
    if not claims:
        return None
    v = claims.get("email_verified")
    if v is None:
        return None
    return bool(v) if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes")


def claim_str(claims, *keys):
    """First non-empty string claim among `keys` (e.g. given_name / first_name)."""
    if not claims:
        return None
    for k in keys:
        v = claims.get(k)
        if v:
            return str(v).strip()
    return None

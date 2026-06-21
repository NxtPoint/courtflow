# auth/principal.py — resolve an authenticated, club-scoped principal from a request.
#
# Heavy-adapt of 1050 auth_v2/principal.py. Two auth methods, tried in order:
#   1. JWT (per-user Clerk token) — ONLY when AUTH_ENABLED=1. Verify the Bearer token,
#      upsert iam.user (by clerk_user_id, link-by-email), load iam.membership, then
#      resolve the active club_id + role (docs/04 §3). The client can NEVER assert a
#      different club_id — it is derived server-side.
#   2. OPS_KEY — server-to-server / cron / admin only (header-only, hmac.compare_digest).
#      This is NOT a client path (decision D6: 1050's shared CLIENT_API_KEY client path
#      and hardcoded ADMIN_EMAILS are dropped). An OPS principal is platform_admin and
#      carries no club_id (it resolves its own scope explicitly).
#
# Disambiguation: a Bearer value that structurally looks like a JWT is treated as a JWT
# (verified or rejected — never silently downgraded). With AUTH_ENABLED=0 the JWT branch
# is skipped entirely.
#
# Tenancy resolution (docs/04 §3), in order:
#   1. Host        -> club.branding.domain / marketing_hosts -> club_id   (primary signal)
#   2. X-Club header (multi-club admin switcher) -> validated against the user's memberships
#   3. Default     -> the user's single membership if they have exactly one
# The resolved (club_id, role) MUST come from a membership the user actually holds.

import hmac
import logging
import os
from dataclasses import dataclass
from typing import Optional

from auth import verifier

log = logging.getLogger("auth.principal")


@dataclass
class Principal:
    user_id: Optional[str] = None       # iam.user.id (uuid as str) — None for OPS
    club_id: Optional[str] = None       # resolved active club (uuid as str)
    role: Optional[str] = None          # platform_admin|club_admin|coach|member|guest
    email: Optional[str] = None
    method: str = "none"                # "jwt" | "ops" | "none"
    memberships: tuple = ()             # all (club_id, role, member_status) the user holds

    @property
    def authenticated(self) -> bool:
        return self.method in ("jwt", "ops")

    @property
    def is_platform_admin(self) -> bool:
        return self.role == "platform_admin"


def _ops_key():
    return os.environ.get("OPS_KEY", "").strip()


def _bearer(request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return ""


def _request_host(request):
    """Best-effort request host (prefer an explicit forwarded host on Render)."""
    return (request.headers.get("X-Forwarded-Host")
            or request.headers.get("Host")
            or getattr(request, "host", None)
            or "")


def resolve_principal(request) -> Optional[Principal]:
    """Return an authenticated, club-scoped Principal, or None if unauthorized.
    Never raises — verify/DB failures fail CLOSED (None -> caller returns 401/403)."""
    # 1) JWT path — only when explicitly enabled.
    if verifier.is_enabled():
        token = _bearer(request)
        if token and verifier.looks_like_jwt(token):
            claims = verifier.verify_jwt(token)
            if not claims:
                return None  # a JWT was presented but invalid — reject, don't downgrade
            try:
                return _principal_from_claims(claims, request)
            except Exception as e:
                log.warning("auth: principal resolution failed: %s", e.__class__.__name__)
                return None

    # 2) OPS_KEY server-to-server path (header-only). Never a browser/client path.
    return _ops_principal(request)


def _ops_principal(request) -> Optional[Principal]:
    key = _ops_key()
    if not key:
        return None
    supplied = (request.headers.get("X-Ops-Key") or "").strip()
    if not supplied or not hmac.compare_digest(supplied, key):
        return None
    # OPS is platform_admin with no implicit club — crons/admin set their own club scope.
    return Principal(method="ops", role="platform_admin")


def _principal_from_claims(claims, request) -> Optional[Principal]:
    """Verified-token -> upsert iam.user -> load memberships -> resolve (club_id, role)."""
    uid = verifier.claim_uid(claims)
    email = verifier.claim_email(claims)
    if not uid:
        return None

    from db import session_scope
    from iam import repositories as iam_repo

    host = _request_host(request)
    x_club = (request.headers.get("X-Club") or "").strip() or None

    # Capture primitives inside the txn (DB rows expire after commit).
    with session_scope() as s:
        user = iam_repo.upsert_user_by_clerk_id(
            s,
            clerk_user_id=uid,
            email=email,
            first_name=verifier.claim_str(claims, "given_name", "first_name"),
            surname=verifier.claim_str(claims, "family_name", "surname", "last_name"),
        )
        user_id = str(user["id"])
        resolved_email = (user.get("email") or email or None)

        memberships = iam_repo.memberships_for_user(s, user["id"])
        host_club_id = iam_repo.resolve_club_by_host(s, host)
        # Auto-enrol: any authenticated user with NO membership becomes an active 'member' of
        # the target club (the host's club, else the single club if this deployment has one).
        # New sign-ups land in the portal as members (they then choose PAYG or buy a
        # membership) instead of hitting "No active club". Admins/coaches are seeded/invited,
        # so they already hold a row and skip this.
        if not memberships:
            default_club = host_club_id or iam_repo.sole_club_id(s)
            if default_club:
                iam_repo.upsert_membership(s, club_id=default_club, user_id=user["id"],
                                           role="member", member_status="active")
                memberships = iam_repo.memberships_for_user(s, user["id"])
                # Signup gift: a free week of COURT access. Grant a time-boxed trial membership
                # (provider='trial') — courts become free via the membership engine and it lapses
                # on its own. Guarded + one-shot (idempotent: never granted twice). SIGNUP_TRIAL_DAYS
                # tunes the length (default 7; 0 disables).
                try:
                    from billing.membership import grant_signup_trial
                    days = int(os.getenv("SIGNUP_TRIAL_DAYS", "7") or 0)
                    if days > 0:
                        grant_signup_trial(s, club_id=default_club, user_id=user["id"], days=days)
                except Exception:
                    log.debug("signup trial grant skipped (billing absent/benign)", exc_info=False)

    club_id, role = _resolve_active_club(memberships, host_club_id, x_club)

    return Principal(
        user_id=user_id,
        club_id=str(club_id) if club_id else None,
        role=role,
        email=resolved_email,
        method="jwt",
        memberships=tuple((str(m["club_id"]), m["role"], m["member_status"]) for m in memberships),
    )


def _resolve_active_club(memberships, host_club_id, x_club):
    """Pick the active (club_id, role) from the user's memberships (docs/04 §3).
    Returns (club_id, role) where role may be None if no membership matches.

    A platform_admin membership wins for whatever club is targeted (host/X-Club) and,
    failing that, acts platform-wide with club_id=None."""
    if not memberships:
        return (None, None)

    by_club = {}
    for m in memberships:
        by_club.setdefault(str(m["club_id"]), []).append(m["role"])

    def role_for(club_id):
        """Best (most-privileged) role the user holds in that club."""
        order = ("platform_admin", "club_admin", "coach", "member", "guest")
        roles = by_club.get(str(club_id), [])
        for r in order:
            if r in roles:
                return r
        return None

    # 2) explicit X-Club header (admin switcher) — only if the user is a member there.
    if x_club and str(x_club) in by_club:
        return (x_club, role_for(x_club))

    # 1) host -> club — only if the user is a member of that club.
    if host_club_id and str(host_club_id) in by_club:
        return (str(host_club_id), role_for(host_club_id))

    # 3) default — exactly one membership: act in that club. Covers a single-club user
    # (admin/coach/member) on a non-club host (e.g. the onrender URL, where the host does
    # not map to a club). MUST precede the platform-admin wildcard below so a single-club
    # platform_admin lands in their own club instead of club_id=None.
    if len(memberships) == 1:
        m = memberships[0]
        return (str(m["club_id"]), role_for(m["club_id"]))

    # A platform_admin with MULTIPLE memberships and no host/X-Club target acts platform-wide
    # (club_id=None until they pick a club via host or the X-Club switcher).
    platform_clubs = [c for c, rs in by_club.items() if "platform_admin" in rs]
    if platform_clubs:
        target = (str(x_club) if x_club else (str(host_club_id) if host_club_id else None))
        return (target, "platform_admin")

    # Multiple memberships but neither host nor X-Club disambiguated -> no active club.
    return (None, None)

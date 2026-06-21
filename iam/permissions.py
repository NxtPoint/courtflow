# iam/permissions.py — the central, boring authorization policy.  can(principal, action, resource)
#
# Per docs/04 §4. Every endpoint calls can(); the logic lives ONLY here so the role
# model is in one place (replaces 1050's hardcoded ADMIN_EMAILS, per decision D6).
#
# Roles (most-privileged first): platform_admin > club_admin > coach > member > guest.
#   - platform_admin: cross-club, everything.
#   - club_admin: full control within their one club.
#   - coach: own diary/availability/rosters; cannot touch prices/finances/other coaches.
#   - member: book + manage own bookings/profile/ledger.
#   - guest: book a court as a visitor; minimal profile.
#
# `principal` is auth.principal.Principal (carries user_id, club_id, role, email).
# `resource` is an optional dict-like carrying ownership keys we check (e.g.
# {'club_id', 'coach_user_id', 'booked_by_user_id', 'user_id'}). Ownership checks are
# explicit and conservative: unknown action -> deny.

ROLES = ("platform_admin", "club_admin", "coach", "member", "guest")
_RANK = {r: i for i, r in enumerate(reversed(ROLES))}  # guest=0 ... platform_admin=4


def _role(principal):
    return getattr(principal, "role", None)


def _rank(role):
    return _RANK.get(role, -1)


def _rget(resource, key):
    """Read a key from a dict-like or attr-bearing resource. None if absent."""
    if resource is None:
        return None
    if isinstance(resource, dict):
        return resource.get(key)
    return getattr(resource, key, None)


def _same_club(principal, resource):
    """A resource is in-scope only if it belongs to the principal's resolved club.
    platform_admin is exempt (cross-club). If the resource carries no club_id we treat
    it as in-scope (the caller is responsible for scoping the query by club_id)."""
    if _role(principal) == "platform_admin":
        return True
    rc = _rget(resource, "club_id")
    if rc is None:
        return True
    return str(rc) == str(getattr(principal, "club_id", None))


def _owns_booking(principal, resource):
    uid = str(getattr(principal, "user_id", None))
    for key in ("booked_by_user_id", "user_id"):
        v = _rget(resource, key)
        if v is not None and str(v) == uid:
            return True
    return False


def _is_coachs_own(principal, resource):
    v = _rget(resource, "coach_user_id")
    return v is not None and str(v) == str(getattr(principal, "user_id", None))


# action -> minimum role that may perform it club-wide (ownership exceptions handled below).
_MIN_ROLE = {
    # club configuration / finances (admin-only)
    "manage_club":        "club_admin",
    "manage_branding":    "club_admin",
    "manage_policy":      "club_admin",
    "manage_resources":   "club_admin",
    "manage_coaches":     "club_admin",
    "manage_prices":      "club_admin",
    "view_finances":      "club_admin",
    "run_billing":        "club_admin",
    "take_pay_at_court":  "club_admin",
    "view_club_analytics": "club_admin",
    "view_master_diary":  "club_admin",
    # provisioning (platform-only)
    "provision_club":     "platform_admin",
    "impersonate":        "platform_admin",
    "cross_club":         "platform_admin",
}


def can(principal, action, resource=None):
    """Return True iff `principal` may perform `action` on `resource`. Fail-closed:
    a None principal, an unknown action, or an out-of-club resource -> False."""
    if principal is None or _role(principal) not in ROLES:
        return False
    role = _role(principal)

    # platform_admin can do everything.
    if role == "platform_admin":
        return True

    # Everything else is confined to the principal's club.
    if not _same_club(principal, resource):
        return False

    # --- ownership-scoped actions (checked before the flat role table) -------
    if action in ("cancel_booking", "reschedule_booking", "edit_booking",
                  "mark_attendance", "add_notes"):
        if role == "club_admin":
            return True
        if role == "coach":
            return _is_coachs_own(principal, resource)
        if role == "member":
            return _owns_booking(principal, resource)
        if role == "guest":
            return _owns_booking(principal, resource)
        return False

    if action in ("manage_own_availability", "manage_own_time_off", "view_own_rosters"):
        return role in ("club_admin", "coach")

    if action in ("create_booking", "book_court", "book_lesson", "book_class"):
        return role in ("club_admin", "coach", "member", "guest")

    if action in ("manage_own_profile", "view_own_ledger", "manage_own_membership",
                  "request_refund"):  # request_refund: a client raises a refund REQUEST (admin approves)
        return role in ("club_admin", "coach", "member", "guest")

    if action == "add_junior":  # a member adds a child (guardian path, docs/04 §5)
        return role in ("club_admin", "member")

    # --- flat role-threshold actions ----------------------------------------
    min_role = _MIN_ROLE.get(action)
    if min_role is not None:
        return _rank(role) >= _rank(min_role)

    # Unknown action -> deny.
    return False

# iam/validation.py — minimum-data validation for Client-360 capture points (Slice-0 Step 4).
#
# The People tab is the single source of truth; a member record must carry at least a name and a
# cell (the key to WhatsApp/SMS + booking reminders). Signup stays frictionless — we capture these
# at the first booking (the committed moment). See docs/specs/CLIENT-360-CRM-PLAN.md §10 Step 4.

# (field, human label) — the minimum a member profile must carry. Order = display order.
MIN_FIELDS = (
    ("first_name", "First name"),
    ("surname", "Surname"),
    ("phone", "Cell number"),
)


def _blank(v):
    return not (v is not None and str(v).strip())


def missing_min_fields(profile):
    """Return [{'field','label'}] for each minimum field (first_name, surname, phone) blank in
    `profile` (a dict, e.g. from iam.repositories.get_profile). Empty list == complete."""
    profile = profile or {}
    return [{"field": f, "label": label} for f, label in MIN_FIELDS if _blank(profile.get(f))]

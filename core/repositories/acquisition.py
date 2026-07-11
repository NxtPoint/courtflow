# core/repositories/acquisition.py — signup ad/UTM attribution capture (Client-360 + Google Ads).
#
# record_acquisition() upserts the 1:1 core.acquisition row for a signed-in iam.user, keyed via the
# email -> core.app_user bridge (ensure_identity, idempotent — it adopts the row the signup
# link_person_for_user already created). FIRST-TOUCH WINS: a column is only written while still
# NULL, so the original ad click (gclid/utm) is never overwritten by a later organic visit.
#
# This is the storage half of the Google Ads offline-conversion loop: the client captures the
# gclid on the landing URL (frontend/js/attribution.js) and flushes it here at signup; a later cron
# uploads the REAL downstream conversion (first booking / membership) to Google Ads by gclid — so
# Ads bids for people who become members, not just clickers. See docs/specs/GOOGLE-ADS-PLAN.md.
#
# Repo convention: takes an explicit `session`, never commits (callers compose via session_scope()).

from datetime import datetime

from core.models import Acquisition
from core.repositories.accounts import ensure_identity

# Incoming attr key -> Acquisition column. All values are trimmed + length-capped on write.
_FIELD_MAP = {
    "source": "source", "medium": "medium", "campaign": "campaign",
    "term": "term", "content": "content", "referrer": "referrer",
    "landing_page": "landing_page", "gclid": "gclid", "fbclid": "fbclid",
}
_CAP = 512


def _clean(v):
    if v is None:
        return None
    v = str(v).strip()
    return v[:_CAP] or None


def record_acquisition(session, *, iam_user_id, email, club_id=None, attr=None):
    """Upsert the caller's core.acquisition row (first-touch wins).

    Returns the Acquisition row, or None when there is nothing worth storing (no ad/UTM
    params) or no email to key a core.app_user (login-less dependents never get here)."""
    attr = attr or {}
    # Nothing to attribute (pure organic, no gclid/utm) -> skip entirely; keeps the row absent
    # for organic signups so "has gclid" is a clean filter for the offline-upload cron.
    if not any(_clean(attr.get(k)) for k in _FIELD_MAP):
        return None
    if not (email and email.strip()):
        return None

    # email -> core.app_user (idempotent; adopts the account/user/person made at signup).
    _acct, user, _person = ensure_identity(session, email=email.strip(),
                                           full_name=email.strip(), club_id=club_id)

    acq = session.get(Acquisition, user.id)
    if acq is None:
        now = datetime.utcnow()
        acq = Acquisition(user_id=user.id, first_seen_at=now, signed_up_at=now)
        session.add(acq)

    # First-touch wins: only fill a column while it is still empty.
    for src_key, col in _FIELD_MAP.items():
        val = _clean(attr.get(src_key))
        if val and not getattr(acq, col, None):
            setattr(acq, col, val)

    session.flush()
    return acq

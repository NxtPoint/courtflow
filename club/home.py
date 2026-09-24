# club/home.py — which club the platform's ONE-PER-PLATFORM settings belong to.
#
# A handful of things exist once for the whole platform, as env vars, because the platform began as
# one club: the transactional blind copy (TRANSACTIONAL_BCC), the Klaviyo account (KLAVIYO_API_KEY)
# and the signup free week (SIGNUP_TRIAL_DAYS). They are NextPoint's. With a second club on the
# platform, applying them to every club would copy NextPoint on another club's customers' emails,
# put those customers into NextPoint's marketing list, and hand out a free week the other club never
# offered. So each is scoped to the HOME club; any other club must opt in through its own settings.
#
#   HOME_CLUB_SLUG   the club those env settings belong to (default "nextpoint")

import logging
import os

log = logging.getLogger("club.home")

_SLUG_BY_ID = {}


def home_club_slug():
    return (os.getenv("HOME_CLUB_SLUG") or "nextpoint").strip().lower()


def _slug(club_id):
    cid = str(club_id)
    if cid not in _SLUG_BY_ID:
        from sqlalchemy import text
        from db import session_scope
        with session_scope() as s:
            slug = s.execute(text("SELECT slug FROM club.club WHERE id::text = :c"), {"c": cid}).scalar()
        if slug is None:
            return None               # unknown club: don't cache a miss
        _SLUG_BY_ID[cid] = str(slug).lower()
    return _SLUG_BY_ID[cid]


def is_home_club(club_id):
    """True for the home club. A send with NO club (a platform email, a coach invite) is the home
    club's, as it always was. An unknown club, or a failed lookup, is NOT home — the safe direction
    is to withhold NextPoint's settings, never to apply them to someone else's customers."""
    if not club_id:
        return True
    try:
        return _slug(club_id) == home_club_slug()
    except Exception:
        log.warning("home-club lookup failed for %s — treating as NOT home", club_id, exc_info=True)
        return False

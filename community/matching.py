# community/matching.py — "find me someone to play".
#
# A DETERMINISTIC compatibility score, not a model. The percentage on the card has to be explicable
# to the member who was shown it ("you're both around level 5 and you both play weekday evenings"),
# and it has to be reproducible when someone asks why they keep being matched with the same person.
# Every term below is a plain number with a stated weight; nothing here learns.
#
# The single biggest determinant of whether this feature works is LEVEL. People stop using these
# products the moment they are repeatedly matched with opponents far too strong or too weak — so
# level dominates the score and everything else only breaks ties.
#
# PRIVACY: this returns first names and levels. Never an email, never a phone number, and never a
# junior. Discovery requires the explicit iam.player_profile.visible_in_community opt-in.

import logging

from sqlalchemy import text

log = logging.getLogger("community.matching")

# Weights sum to 100. Level is half the score on purpose.
W_LEVEL = 50
W_AVAILABILITY = 20
W_FORMAT = 12
W_PLAY_TYPE = 8
W_HISTORY = 10


def _level_score(a, b):
    """1.0 at the same level, decaying to 0 by 3 whole levels apart. A 1-level gap is still a good
    game; a 3-level gap is a lesson for one of them and a chore for the other."""
    if a is None or b is None:
        return 0.5           # unknown level is neutral, never disqualifying
    gap = abs(float(a) - float(b))
    return max(0.0, 1.0 - (gap / 3.0))


def _overlap_score(a, b):
    """Shared preferred playing times, as a fraction of the seeker's own availability."""
    sa, sb = set(a or []), set(b or [])
    if not sa or not sb:
        return 0.5
    return len(sa & sb) / float(len(sa))


def _format_score(a, b):
    if not a or not b or a == "both" or b == "both":
        return 1.0
    return 1.0 if a == b else 0.0


def _play_score(a, b):
    if not a or not b:
        return 0.5
    return 1.0 if a == b else 0.3


def suggest_players(session, *, club_id, user_id, limit=12):
    """Up to `limit` players this member would probably enjoy playing, best first.

    Geography is deliberately absent: at a single-venue club every member is already at the same
    courts, so distance — the axis PlayYourCourt spends most of its matching on — carries no
    information here."""
    me = session.execute(
        text("SELECT level_num, prefers_format, prefers_play, prefers_times "
             "FROM iam.player_profile WHERE club_id = :c AND user_id = :u"),
        {"c": str(club_id), "u": str(user_id)},
    ).mappings().first()
    me = dict(me) if me else {}

    rows = session.execute(
        text("""
            SELECT pp.user_id, pp.level_num, pp.prefers_format, pp.prefers_play, pp.prefers_times,
                   u.first_name,
                   (SELECT count(*) FROM community.play_again pa
                     WHERE pa.club_id = pp.club_id AND pa.rater_user_id = CAST(:me AS uuid)
                       AND pa.subject_user_id = pp.user_id AND pa.again = true) AS liked,
                   (SELECT count(*) FROM community.play_again pa
                     WHERE pa.club_id = pp.club_id AND pa.rater_user_id = CAST(:me AS uuid)
                       AND pa.subject_user_id = pp.user_id AND pa.again = false) AS disliked
              FROM iam.player_profile pp
              JOIN iam."user" u ON u.id = pp.user_id
             WHERE pp.club_id = :c
               AND pp.visible_in_community = true
               AND pp.user_id <> CAST(:me AS uuid)
               -- JUNIORS ARE EXCLUDED FROM DISCOVERY. A junior-to-junior introduction needs guardian
               -- mediation, which this phase does not build — so they are not offered at all rather
               -- than offered with a caveat nobody reads.
               AND pp.guardian_user_id IS NULL
               AND NOT EXISTS (SELECT 1 FROM iam.dependent d
                                WHERE d.dependent_user_id = pp.user_id AND d.is_active = true)
             LIMIT 400
        """),
        {"c": str(club_id), "me": str(user_id)},
    ).mappings().all()

    out = []
    for r in rows:
        # A player they have explicitly said they would rather not play again is dropped, not merely
        # ranked down. The signal is private and it is the strongest one we have.
        if int(r["disliked"] or 0) > 0 and int(r["liked"] or 0) == 0:
            continue
        score = (W_LEVEL * _level_score(me.get("level_num"), r["level_num"])
                 + W_AVAILABILITY * _overlap_score(me.get("prefers_times"), r["prefers_times"])
                 + W_FORMAT * _format_score(me.get("prefers_format"), r["prefers_format"])
                 + W_PLAY_TYPE * _play_score(me.get("prefers_play"), r["prefers_play"]))
        # Two good games with someone is full history credit. The favourite table used to carry
        # double weight here; it was deleted 2026-08-12 (built engine-first, never given a screen,
        # empty everywhere), so history now rests entirely on the signal that DOES have a UI.
        history = min(1.0, int(r["liked"] or 0) / 2.0)
        score += W_HISTORY * history
        out.append({
            "user_id": str(r["user_id"]),
            "name": r["first_name"],
            "level": float(r["level_num"]) if r["level_num"] is not None else None,
            "prefers_format": r["prefers_format"],
            "prefers_play": r["prefers_play"],
            "match_pct": int(round(max(0.0, min(100.0, score)))),
        })
    out.sort(key=lambda x: (-x["match_pct"], x["name"] or ""))
    return out[:int(limit or 12)]


ONBOARDING_QUESTIONS = [
    # Five questions, scored to a starting NextPoint Level. Deliberately about WHAT THEY DO rather
    # than what they'd call themselves — "how would you rate yourself?" is how everybody becomes
    # advanced, which is the failure mode that kills the matching.
    {"key": "years", "q": "How long have you played?",
     "options": [("Just starting", 0), ("Under 2 years", 1), ("2–5 years", 2), ("5+ years", 3)]},
    {"key": "serve", "q": "Your second serve…",
     "options": [("I often double fault", 0), ("Goes in, but sits up", 1),
                 ("Reliable with some spin", 2), ("A weapon I can place", 3)]},
    {"key": "rally", "q": "In a rally you can…",
     "options": [("Keep 2–3 balls in", 0), ("Rally from the baseline", 1),
                 ("Change direction on purpose", 2), ("Construct and finish points", 3)]},
    {"key": "match", "q": "Competitive matches?",
     "options": [("Never", 0), ("Socially", 1), ("Club leagues/box", 2), ("Tournaments", 3)]},
    {"key": "frequency", "q": "How often do you play?",
     "options": [("Occasionally", 0), ("Once a week", 1), ("2–3 times a week", 2), ("Most days", 3)]},
]


def level_from_answers(answers):
    """Map the five answers (0–3 each, 15 max) onto the NextPoint Level 1.0–10.0.

    Coaches and admins can override any result (level_source='coach'), which is the real safety net:
    a five-question quiz is a starting point, not a rating."""
    if not answers:
        return None
    total = 0
    for q in ONBOARDING_QUESTIONS:
        v = answers.get(q["key"])
        try:
            total += max(0, min(3, int(v)))
        except (TypeError, ValueError):
            continue
    # 0 -> 1.0, 15 -> 9.0. Elite (10) is never self-assessed.
    return round(1.0 + (total / 15.0) * 8.0, 1)

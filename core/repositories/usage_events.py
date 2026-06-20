# core/repositories/usage_events.py — the canonical event-stream writer.
#
# Thin write-path into core.usage_event (the system of record that crm_sync forwards to
# Klaviyo, docs/06). Ported from 1050 core_db/repositories/matches.record_usage, narrowed
# to the booking domain + multi-tenant (club_id threaded through).
#
# Like every core repository: takes an explicit `session`, never commits — the caller
# composes the transaction via db.session_scope(). NPS lives here too (it writes to a
# core.* table and is small).

from datetime import datetime, timezone

from core.models import NpsResponse, UsageEvent


def _now():
    return datetime.now(timezone.utc)


def record_usage(session, *, event_type, club_id=None, account_id=None, user_id=None,
                 person_id=None, ref_type=None, ref_id=None, metadata=None, occurred_at=None):
    """Insert one core.usage_event row. The single durable record of a product event.

    `metadata` maps to the physical `metadata` JSONB column (ORM attr `event_metadata`).
    Never raises on a None metadata; callers pass non-PII payloads only (see contracts/events.md).
    Returns the inserted row (flushed, not committed)."""
    row = UsageEvent(
        event_type=event_type,
        club_id=club_id,
        account_id=account_id,
        user_id=user_id,
        person_id=person_id,
        ref_type=ref_type,
        ref_id=(str(ref_id) if ref_id is not None else None),
        event_metadata=(metadata or None),
        occurred_at=occurred_at or _now(),
    )
    session.add(row)
    session.flush()
    return row


def record_nps(session, *, score, club_id=None, account_id=None, user_id=None, comment=None,
               survey_id=None):
    """Insert an NPS response (0-10) + derive the bucket. Used by the feedback path."""
    try:
        score = int(score)
    except (TypeError, ValueError):
        raise ValueError("nps score must be an integer 0-10")
    if not (0 <= score <= 10):
        raise ValueError("nps score must be 0-10")
    bucket = "promoter" if score >= 9 else ("passive" if score >= 7 else "detractor")
    row = NpsResponse(
        score=score, bucket=bucket, comment=comment, survey_id=survey_id,
        club_id=club_id, account_id=account_id, user_id=user_id,
    )
    session.add(row)
    session.flush()
    return row

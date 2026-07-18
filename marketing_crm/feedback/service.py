# marketing_crm/feedback/service.py — record a feedback rating/comment against a verified token.
#
# Reuse-first: writes core.nps_response (the SAME store the admin NPS panel + Client-360 read) and
# emits nps_submitted / feedback_submitted through the ONE emit() funnel (→ core.usage_event + a
# marketing-gated Klaviyo forward). No new tables. The 1-5 star rating is mapped to the table's 0-10
# NPS scale (star*2, losslessly reversible) so the existing NPS metric (promoters score>=9,
# detractors score<=6) stays valid; survey_id = 'feedback:<jti>' tags the source + dedupes one
# response per email (upsert on the token nonce, so a re-tap or a later comment updates one row).
#
# Repos never commit — the caller composes via db.session_scope().

import logging

from sqlalchemy import text

from marketing_crm.feedback.tokens import review_url

log = logging.getLogger("marketing_crm.feedback.service")


def _star_to_nps(star: int) -> int:
    return max(0, min(10, star * 2))          # 1..5 -> 2..10


def _bucket(nps: int) -> str:
    return "promoter" if nps >= 9 else ("passive" if nps >= 7 else "detractor")


def record_feedback(session, payload, *, score=None, comment=None):
    """Record a rating and/or comment for the recipient in a VERIFIED token payload.
    Returns {recorded, sentiment, review_url, first_name}. `sentiment` = promoter (4-5★) | detractor
    (1-3★) | None (comment-only). Never raises for a benign miss; the caller has the token."""
    iam_uid = payload.get("u")
    club_id = payload.get("c")
    jti = payload.get("j") or "na"
    ctx = payload.get("x") or "feedback"
    survey = f"feedback:{jti}"
    out = {"recorded": False, "sentiment": None, "review_url": review_url(), "first_name": None}

    # Resolve the person server-side (email/name never travel in the URL).
    email = first_name = None
    account_id = user_id_core = None
    try:
        row = session.execute(
            text("SELECT email, first_name FROM iam.user WHERE id = CAST(:u AS uuid)"),
            {"u": iam_uid},
        ).mappings().first()
        if row:
            email = (row["email"] or "").strip().lower() or None
            first_name = (row["first_name"] or "").strip() or None
        if email:
            from core.repositories import accounts
            a = accounts.get_account_by_email(session, email)
            account_id = a.id if a else None
            u = accounts.get_user_by_email(session, email)
            user_id_core = u.id if u else None
    except Exception:
        log.exception("record_feedback: identity resolution failed")
    out["first_name"] = (first_name.split(" ")[0] if first_name else None)

    star = None
    if score is not None:
        try:
            star = max(1, min(5, int(score)))
        except (TypeError, ValueError):
            star = None
    comment = (comment or "").strip() or None

    # Upsert ONE nps_response per token nonce (re-tap changes the score; a later comment fills it in).
    if star is not None or comment is not None:
        nps = _star_to_nps(star) if star is not None else None
        existing = session.execute(
            text("SELECT id, score FROM core.nps_response WHERE survey_id = :s ORDER BY id LIMIT 1"),
            {"s": survey},
        ).mappings().first()
        if existing:
            session.execute(text("""
                UPDATE core.nps_response
                   SET score      = COALESCE(:sc, score),
                       bucket     = CASE WHEN :sc IS NULL THEN bucket ELSE :bk END,
                       comment    = COALESCE(:cm, comment),
                       submitted_at = now()
                 WHERE id = :id
            """), {"sc": nps, "bk": (_bucket(nps) if nps is not None else None),
                   "cm": comment, "id": existing["id"]})
        else:
            session.execute(text("""
                INSERT INTO core.nps_response
                    (club_id, account_id, user_id, score, bucket, comment, survey_id)
                VALUES (CAST(:c AS uuid), :a, :u, :sc, :bk, :cm, :s)
            """), {"c": club_id, "a": account_id, "u": user_id_core,
                   "sc": (nps if nps is not None else 0),
                   "bk": (_bucket(nps) if nps is not None else None),
                   "cm": comment, "s": survey})
        out["recorded"] = True

    if star is not None:
        out["sentiment"] = "promoter" if star >= 4 else "detractor"

    # Emit for the marketing funnel (usage_event always; Klaviyo forward gated on opt-in). Fire-and-
    # forget on its own thread — never blocks the request, never sends PII (no raw comment).
    try:
        from marketing_crm.tracking import emit
        if star is not None:
            emit("nps_submitted", {"club_id": club_id, "email": email, "user_id": iam_uid,
                                   "score": star, "sentiment": out["sentiment"], "context": ctx})
        if comment is not None:
            emit("feedback_submitted", {"club_id": club_id, "email": email, "user_id": iam_uid,
                                        "has_comment": True, "context": ctx})
    except Exception:
        log.exception("record_feedback: emit failed (non-fatal)")

    return out

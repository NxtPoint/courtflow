# marketing_crm/feedback/blueprint.py — the public, token-guarded feedback API (feedback_bp).
#
# No login: the signed token IS the authorization (it names the recipient + club). The branded page
# (courtflow-web /feedback) calls these; a bad/expired token 400s. Club scope comes from the token.
#
#   GET  /api/feedback?t=<token>            -> validate + greet   {ok, first_name, review_url}
#   POST /api/feedback  {token, score?, comment?} -> record       {ok, recorded, sentiment, review_url}
#
# Writes go through db.session_scope() (the API service has a DB). Self-contained + fault-tolerant.

import logging

from flask import Blueprint, jsonify, request

from marketing_crm.feedback import tokens
from marketing_crm.feedback.service import record_feedback

log = logging.getLogger("marketing_crm.feedback.blueprint")

feedback_bp = Blueprint("feedback", __name__)


@feedback_bp.get("/api/feedback")
def feedback_validate():
    payload = tokens.verify(request.args.get("t", ""))
    if not payload:
        return jsonify(ok=False, error="invalid_or_expired"), 400
    # Greet by first name without recording anything (the page records on star tap / comment submit).
    first_name = None
    try:
        from db import session_scope
        from sqlalchemy import text
        with session_scope() as s:
            row = s.execute(
                text("SELECT first_name FROM iam.user WHERE id = CAST(:u AS uuid)"),
                {"u": payload.get("u")},
            ).mappings().first()
            if row and row["first_name"]:
                first_name = str(row["first_name"]).split(" ")[0]
    except Exception:
        log.exception("feedback_validate: name lookup failed (non-fatal)")
    return jsonify(ok=True, first_name=first_name, review_url=tokens.review_url())


@feedback_bp.post("/api/feedback")
def feedback_record():
    data = request.get_json(silent=True) or {}
    payload = tokens.verify(data.get("token") or request.args.get("t", ""))
    if not payload:
        return jsonify(ok=False, error="invalid_or_expired"), 400
    score = data.get("score")
    comment = data.get("comment")
    if score is None and not (comment and str(comment).strip()):
        return jsonify(ok=False, error="nothing_to_record"), 400
    # Clamp the comment so a runaway paste can't bloat the row.
    if comment is not None:
        comment = str(comment)[:2000]
    try:
        from db import session_scope
        with session_scope() as s:
            out = record_feedback(s, payload, score=score, comment=comment)
    except Exception:
        log.exception("feedback_record failed")
        return jsonify(ok=False, error="server_error"), 500
    return jsonify(ok=True, recorded=out["recorded"], sentiment=out["sentiment"],
                   review_url=out["review_url"], first_name=out["first_name"])

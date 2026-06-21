# core/repositories/notifications.py — core.notification CRUD (the in-app inbox).
#
# Plain-SQL repositories (no ORM model for core.notification — it FKs across to iam.user +
# club.club, owned by raw DDL in core/schema.py). Every function takes an explicit `session`
# and never commits; callers compose via db.session_scope(). Every read/write is scoped by
# (club_id, user_id) — multi-tenant + per-recipient.

import json

from sqlalchemy import text


def insert_notification(session, *, club_id, user_id, kind, title, body=None,
                        link=None, data=None, email_status="skipped"):
    """Insert one in-app notification row. Returns its id (str UUID). Scoped to the
    recipient (user_id) within a club (club_id). `data` is non-PII context (jsonb)."""
    row = session.execute(
        text("""
            INSERT INTO core.notification
                (club_id, user_id, kind, title, body, link, data, email_status)
            VALUES (:club_id, :user_id, :kind, :title, :body, :link,
                    CAST(:data AS jsonb), :email_status)
            RETURNING id
        """),
        {
            "club_id": str(club_id),
            "user_id": str(user_id),
            "kind": kind,
            "title": title,
            "body": body,
            "link": link,
            "data": json.dumps(data) if data is not None else None,
            "email_status": email_status,
        },
    ).mappings().first()
    return str(row["id"]) if row else None


def set_email_status(session, *, notification_id, email_status):
    """Update the delivery outcome (sent|failed|pending|skipped) after the email attempt."""
    session.execute(
        text("UPDATE core.notification SET email_status = :s WHERE id = :id"),
        {"s": email_status, "id": str(notification_id)},
    )


def list_notifications(session, *, club_id, user_id, unread_only=False, limit=30):
    """The recipient's most-recent notifications (scoped to club_id + user_id). When
    unread_only, only rows with read_at IS NULL. Returns list of dicts."""
    where = ["club_id = :c", "user_id = :u"]
    params = {"c": str(club_id), "u": str(user_id), "lim": int(limit)}
    if unread_only:
        where.append("read_at IS NULL")
    rows = session.execute(
        text("SELECT id, kind, title, body, link, data, read_at, email_status, created_at "
             "FROM core.notification WHERE " + " AND ".join(where) +
             " ORDER BY created_at DESC LIMIT :lim"),
        params,
    ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["read_at"] = d["read_at"].isoformat() if d.get("read_at") else None
        d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
        out.append(d)
    return out


def unread_count(session, *, club_id, user_id):
    """Count unread notifications for the recipient (scoped)."""
    return int(session.execute(
        text("SELECT count(*) FROM core.notification "
             "WHERE club_id = :c AND user_id = :u AND read_at IS NULL"),
        {"c": str(club_id), "u": str(user_id)},
    ).scalar() or 0)


def mark_read(session, *, club_id, user_id, notification_id=None, all_unread=False):
    """Mark one (notification_id) or ALL the recipient's unread notifications read. Always
    scoped to (club_id, user_id) so a caller can only ever touch their own rows. Returns the
    number of rows updated."""
    if all_unread:
        res = session.execute(
            text("UPDATE core.notification SET read_at = now() "
                 "WHERE club_id = :c AND user_id = :u AND read_at IS NULL"),
            {"c": str(club_id), "u": str(user_id)},
        )
        return res.rowcount or 0
    if notification_id:
        res = session.execute(
            text("UPDATE core.notification SET read_at = now() "
                 "WHERE id = :id AND club_id = :c AND user_id = :u AND read_at IS NULL"),
            {"id": str(notification_id), "c": str(club_id), "u": str(user_id)},
        )
        return res.rowcount or 0
    return 0

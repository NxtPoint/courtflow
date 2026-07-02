# billing/activity.py — the UNIFIED TRANSACTION LOG (one transparent "what happened" feed).
#
# WHY: money lived across ~7 tables (payment, order, coach_arrears, commission_split, coach_ledger,
# account_ledger, membership_subscription) with no single chronological view — so a client, coach or
# owner could not see WHAT happened to their money and WHEN. This module composes those AUTHORITATIVE
# tables into one time-ordered, role-scoped feed. It is a pure READ MODEL: no new writes, nothing to
# keep in lockstep, and it works retroactively on all existing data (it can never drift from the money
# because it IS the money tables). Each source is guarded so a missing table degrades to [] not a 500.
#
# Scope (who sees what):
#   client — their own orders/payments + the coaching (arrears) they were billed for + their memberships
#   coach  — the lessons they earned commission on (incl. refund clawbacks) + their per-client arrears
#   owner  — everything club-wide (all payments, order lifecycle, arrears, commission, memberships)
#
# One entry shape (minor units; amount_minor is SIGNED — + money in / earned, - money out / clawed):
#   {at, kind, title, detail, amount_minor, currency, direction('in'|'out'|'neutral'), ref_type, ref_id}

from __future__ import annotations

import logging
from typing import Any, Dict, List

from sqlalchemy import text

log = logging.getLogger(__name__)


def _names(session, club_id, ids) -> Dict[str, str]:
    ids = [str(i) for i in ids if i]
    if not ids:
        return {}
    out: Dict[str, str] = {}
    try:
        for r in session.execute(
            text('SELECT id, first_name, surname, email FROM iam."user" WHERE id = ANY(:ids)'),
            {"ids": ids},
        ).mappings().all():
            full = " ".join(x for x in [r["first_name"], r["surname"]] if x).strip()
            out[str(r["id"])] = full or r["email"] or "Someone"
    except Exception:
        pass
    return out


def _iso(dt):
    return dt.isoformat() if hasattr(dt, "isoformat") else (dt if dt else None)


def transaction_log(session, *, club_id, scope, user_id=None, limit=120) -> List[Dict[str, Any]]:
    """Compose the chronological transaction log for one role. `scope` in {'client','coach','owner'};
    `user_id` is required for client/coach (their own id). Returns newest-first, capped at `limit`."""
    scope = (scope or "").lower()
    entries: List[Dict[str, Any]] = []
    uid = str(user_id) if user_id else None
    if scope in ("client", "coach") and not uid:
        return []

    # ---- 1. payments (money in / out) — client + owner ---------------------
    # A charge is money IN from the client; a refund is money OUT back to them.
    if scope in ("client", "owner"):
        try:
            where = "p.club_id = :c"
            params: Dict[str, Any] = {"c": str(club_id)}
            if scope == "client":
                where += " AND o.user_id = :u"
                params["u"] = uid
            rows = session.execute(
                text(f"""
                    SELECT p.created_at, p.direction, p.status, p.amount_minor, p.currency_code,
                           p.order_id, p.provider, o.user_id
                    FROM billing.payment p
                    LEFT JOIN billing."order" o ON o.id = p.order_id
                    WHERE {where}
                    ORDER BY p.created_at DESC LIMIT :lim
                """),
                {**params, "lim": limit},
            ).mappings().all()
            for r in rows:
                is_refund = r["direction"] == "refund"
                amt = int(r["amount_minor"] or 0)
                entries.append({
                    "at": _iso(r["created_at"]),
                    "kind": "refund" if is_refund else "payment",
                    "title": ("Refund issued" if is_refund else "Payment received"),
                    "detail": (r["provider"] or "").replace("_", " ").strip() or None,
                    "amount_minor": (-amt if is_refund else amt),
                    "currency": r["currency_code"] or "ZAR",
                    "direction": "out" if is_refund else "in",
                    "ref_type": "order",
                    "ref_id": str(r["order_id"]) if r["order_id"] else None,
                })
        except Exception:
            log.info("activity: payments source skipped club=%s", club_id)

    # ---- 2. order lifecycle (owed / voided / written off) — client + owner --
    # Paid/refunded orders are already covered by the payment rows above; here we surface the debt
    # being CREATED (owed) and being CLEARED without payment (void = mistake, written_off = forgiven).
    if scope in ("client", "owner"):
        try:
            where = "o.club_id = :c"
            params = {"c": str(club_id)}
            if scope == "client":
                where += " AND o.user_id = :u"
                params["u"] = uid
            rows = session.execute(
                text(f"""
                    SELECT o.id, o.user_id, o.status, o.amount_minor, o.currency_code,
                           o.created_at, o.updated_at, o.settlement_mode,
                           (SELECT description FROM billing.order_line
                             WHERE order_id = o.id ORDER BY created_at LIMIT 1) AS descr
                    FROM billing."order" o
                    WHERE {where} AND o.settled_by_order_id IS NULL
                    ORDER BY o.created_at DESC LIMIT :lim
                """),
                {**params, "lim": limit},
            ).mappings().all()
            for r in rows:
                descr = r["descr"] or "Charge"
                amt = int(r["amount_minor"] or 0)
                # The order was raised (a debt / an owed line was created).
                if amt > 0 and r["settlement_mode"] not in ("membership_covered", "free"):
                    entries.append({
                        "at": _iso(r["created_at"]), "kind": "order_created",
                        "title": "Charge raised", "detail": descr,
                        "amount_minor": amt, "currency": r["currency_code"] or "ZAR",
                        "direction": "neutral", "ref_type": "order", "ref_id": str(r["id"]),
                    })
                if r["status"] in ("void", "written_off"):
                    entries.append({
                        "at": _iso(r["updated_at"]),
                        "kind": ("order_written_off" if r["status"] == "written_off" else "order_voided"),
                        "title": ("Charge written off" if r["status"] == "written_off" else "Charge cancelled"),
                        "detail": descr, "amount_minor": amt, "currency": r["currency_code"] or "ZAR",
                        "direction": "neutral", "ref_type": "order", "ref_id": str(r["id"]),
                    })
        except Exception:
            log.info("activity: orders source skipped club=%s", club_id)

    # ---- 3. coaching arrears (accrued / collected / written off) -----------
    # client sees it as "coaching with <coach>"; coach sees it as "<client>"; owner sees both.
    if scope in ("client", "coach", "owner"):
        try:
            where = "a.club_id = :c"
            params = {"c": str(club_id)}
            if scope == "client":
                where += " AND a.client_user_id = :u"; params["u"] = uid
            elif scope == "coach":
                where += " AND a.coach_user_id = :u"; params["u"] = uid
            rows = session.execute(
                text(f"""
                    SELECT a.id, a.coach_user_id, a.client_user_id, a.gross_minor, a.currency,
                           a.status, a.note, a.created_at, a.collected_at, a.updated_at
                    FROM billing.coach_arrears a
                    WHERE {where}
                    ORDER BY a.created_at DESC LIMIT :lim
                """),
                {**params, "lim": limit},
            ).mappings().all()
            nm = _names(session, club_id,
                        {r["coach_user_id"] for r in rows} | {r["client_user_id"] for r in rows})

            def _who(r):
                if scope == "coach":
                    return nm.get(str(r["client_user_id"]), "a client")
                return nm.get(str(r["coach_user_id"]), "your coach")

            for r in rows:
                cur = r["currency"] or "ZAR"
                amt = int(r["gross_minor"] or 0)
                entries.append({
                    "at": _iso(r["created_at"]), "kind": "arrears_accrued",
                    "title": "Coaching lesson", "detail": _who(r),
                    "amount_minor": amt, "currency": cur, "direction": "neutral",
                    "ref_type": "arrears", "ref_id": str(r["id"]),
                })
                if r["status"] == "collected":
                    entries.append({
                        "at": _iso(r["collected_at"] or r["updated_at"]), "kind": "arrears_collected",
                        "title": "Lesson paid", "detail": _who(r),
                        "amount_minor": amt, "currency": cur, "direction": "neutral",
                        "ref_type": "arrears", "ref_id": str(r["id"]),
                    })
                elif r["status"] == "written_off":
                    entries.append({
                        "at": _iso(r["updated_at"]), "kind": "arrears_written_off",
                        "title": "Lesson written off",
                        "detail": (r["note"] or _who(r)),
                        "amount_minor": amt, "currency": cur, "direction": "neutral",
                        "ref_type": "arrears", "ref_id": str(r["id"]),
                    })
        except Exception:
            log.info("activity: arrears source skipped club=%s", club_id)

    # ---- 4. commission (earned / clawed back) — coach + owner --------------
    if scope in ("coach", "owner"):
        try:
            where = "cs.club_id = :c AND cs.party_type = 'coach'"
            params = {"c": str(club_id)}
            if scope == "coach":
                where += " AND cs.coach_user_id = :u"; params["u"] = uid
            rows = session.execute(
                text(f"""
                    SELECT cs.id, cs.coach_user_id, cs.basis, cs.amount_minor, cs.currency,
                           cs.occurred_at, b.booked_by_user_id AS client_user_id
                    FROM billing.commission_split cs
                    LEFT JOIN diary.booking b ON b.id = cs.booking_id
                    WHERE {where}
                    ORDER BY cs.occurred_at DESC LIMIT :lim
                """),
                {**params, "lim": limit},
            ).mappings().all()
            nm = _names(session, club_id,
                        {r["coach_user_id"] for r in rows} | {r["client_user_id"] for r in rows})
            for r in rows:
                clawed = r["basis"] == "refund_clawback"
                who = (nm.get(str(r["client_user_id"])) if r["client_user_id"] else None)
                coach = nm.get(str(r["coach_user_id"]), "coach")
                entries.append({
                    "at": _iso(r["occurred_at"]),
                    "kind": ("refund_clawback" if clawed else "commission_earned"),
                    "title": ("Commission reversed (refund)" if clawed else "Commission earned"),
                    "detail": (who or (coach if scope == "owner" else None)),
                    "amount_minor": int(r["amount_minor"] or 0),   # clawback rows are already negative
                    "currency": r["currency"] or "ZAR",
                    "direction": ("out" if clawed else "in"),
                    "ref_type": "commission", "ref_id": str(r["id"]),
                })
        except Exception:
            log.info("activity: commission source skipped club=%s", club_id)

    # ---- 5. memberships (started / cancelled) — client + owner -------------
    if scope in ("client", "owner"):
        try:
            where = "m.club_id = :c"
            params = {"c": str(club_id)}
            if scope == "client":
                where += " AND m.user_id = :u"; params["u"] = uid
            rows = session.execute(
                text(f"""
                    SELECT m.id, m.user_id, m.status, m.provider, m.created_at, m.updated_at
                    FROM billing.membership_subscription m
                    WHERE {where}
                    ORDER BY m.created_at DESC LIMIT :lim
                """),
                {**params, "lim": limit},
            ).mappings().all()
            trial = lambda r: (r["provider"] == "trial")
            for r in rows:
                entries.append({
                    "at": _iso(r["created_at"]), "kind": "membership_started",
                    "title": ("Free week started" if trial(r) else "Membership started"),
                    "detail": None, "amount_minor": 0, "currency": "ZAR",
                    "direction": "neutral", "ref_type": "membership", "ref_id": str(r["id"]),
                })
                if r["status"] == "cancelled":
                    entries.append({
                        "at": _iso(r["updated_at"]), "kind": "membership_cancelled",
                        "title": "Membership cancelled", "detail": None,
                        "amount_minor": 0, "currency": "ZAR", "direction": "neutral",
                        "ref_type": "membership", "ref_id": str(r["id"]),
                    })
        except Exception:
            log.info("activity: memberships source skipped club=%s", club_id)

    # newest first; a missing timestamp sorts last.
    entries.sort(key=lambda e: (e["at"] or ""), reverse=True)
    return entries[:limit]

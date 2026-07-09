# billing/bundles.py — the generic Token / Bundle engine (prepaid session packs), docs/specs/02.
#
# A member buys a configurable PACK of N prepaid sessions (tokens) upfront via Yoco; booking a
# matching service DRAWS one token (settling the order at R0); cancelling CREDITS one back. It is
# the count-based sibling of PAYG (per-use) and membership (time-based), and is GENERIC across
# court / lesson / class and FULLY configurable (any service, duration, price, count, validity).
#
# NOTHING is hardcoded — a pack is a billing.bundle_plan row. The purchase flow mirrors
# billing/membership.py (a pending wallet + an awaiting_payment online order linked by order_id;
# activation in the Yoco webhook). The booking seam mirrors membership_covered
# (settlement_mode='token'). Commission reuses the existing engine via a proper purchase order line.
#
# CAREFUL CORE — atomic, idempotent, no double-spend, no lost tokens:
#   * match_wallet locks the chosen wallet `SELECT … FOR UPDATE` (serialise concurrent draws).
#   * draw_token / credit_token only move tokens_remaining WHEN the token_ledger row actually
#     inserts (ON CONFLICT (wallet_id, booking_id, kind) DO NOTHING RETURNING) — so a replay is a
#     strict no-op (a draw and a credit are each recorded at most once per booking).
#   * draw runs INSIDE the caller's booking transaction → a failed booking un-draws the token, a
#     burned token always has a confirmed booking. tokens_remaining CHECK (>= 0) is the backstop.
#
# Pure SQL via SQLAlchemy Core text(); every fn takes an explicit `session` and NEVER commits
# (callers compose). Every query is club_id-scoped (multi-tenant).

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

log = logging.getLogger("billing.bundles")

# service_kind ('court'|'lesson'|'class') <-> billing.product.kind. The bundle engine speaks the
# diary's booking-type vocabulary; the commission/product side speaks product kinds.
_PRODUCT_KIND_BY_SERVICE = {"court": "court_booking", "lesson": "lesson", "class": "class"}
_SERVICE_BY_PRODUCT_KIND = {"court_booking": "court", "lesson": "lesson", "class": "class"}


def _club_currency(session, *, club_id) -> str:
    try:
        cur = session.execute(
            text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": str(club_id)},
        ).scalar()
        return cur or "ZAR"
    except Exception:
        return "ZAR"


# ---------------------------------------------------------------------------
# plans — owner-configured offers (CRUD) + member-facing listing
# ---------------------------------------------------------------------------

def _plan_label(label, service_kind, sessions_count) -> str:
    """Display label: the explicit label, else derive ('10 court sessions')."""
    if label:
        return label
    n = int(sessions_count or 0)
    noun = {"court": "court session", "lesson": "lesson", "class": "class"}.get(service_kind, "session")
    return f"{n} {noun}{'s' if n != 1 else ''}"


def _plan_dict(row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "service_kind": row["service_kind"],
        "coach_user_id": str(row["coach_user_id"]) if row["coach_user_id"] else None,
        # The SPECIFIC service this pack is tied to (Private vs Semi-private); NULL = legacy unscoped.
        "product_id": (str(row["product_id"]) if ("product_id" in row.keys() and row["product_id"])
                       else None),
        "label": _plan_label(row["label"], row["service_kind"], row["sessions_count"]),
        "sessions_count": int(row["sessions_count"] or 0),
        "duration_minutes": int(row["duration_minutes"]) if row["duration_minutes"] is not None else None,
        "price_minor": int(row["price_minor"] or 0),
        "currency": row["currency_code"],
        "validity_days": int(row["validity_days"]) if row["validity_days"] is not None else None,
        "active": bool(row["active"]),
        "status": row["status"] if "status" in row.keys() else ("active" if row["active"] else "retired"),
    }


def list_plans(session, *, club_id, service_kind=None, active_only=True,
               coach_user_id=None) -> List[Dict[str, Any]]:
    """The club's configured bundle plans (active by default), cheapest-first. Optionally filter to
    one service_kind and/or a coach (for the coach console's own lesson packs). Each plan = the
    _plan_dict shape."""
    where = ["club_id = :c"]
    params: Dict[str, Any] = {"c": str(club_id)}
    if service_kind:
        where.append("service_kind = :sk")
        params["sk"] = service_kind
    if coach_user_id:
        where.append("coach_user_id = :coach")
        params["coach"] = str(coach_user_id)
    if active_only:
        where.append("active = true")
    rows = session.execute(
        text("SELECT id, club_id, service_kind, coach_user_id, product_id, label, sessions_count, "
             "       duration_minutes, price_minor, currency_code, validity_days, active, status "
             "FROM billing.bundle_plan WHERE " + " AND ".join(where) + " "
             "ORDER BY active DESC, price_minor ASC, created_at ASC"),
        params,
    ).mappings().all()
    return [_plan_dict(r) for r in rows]


def get_plan(session, *, club_id, plan_id) -> Optional[Dict[str, Any]]:
    return _plan_dict(session.execute(
        text("SELECT id, club_id, service_kind, coach_user_id, product_id, label, sessions_count, "
             "       duration_minutes, price_minor, currency_code, validity_days, active, status "
             "FROM billing.bundle_plan WHERE club_id = :c AND id = :id"),
        {"c": str(club_id), "id": str(plan_id)},
    ).mappings().first())


def create_plan(session, *, club_id, service_kind=None, sessions_count, price_minor,
                label=None, duration_minutes=None, coach_user_id=None,
                validity_days=None, product_id=None) -> Dict[str, Any]:
    """Owner adds a bundle plan. Nothing hardcoded — all of it is the owner's input.

    PER-SERVICE (new): pass `product_id` = the SPECIFIC billing.product (Private lesson, Clay court)
    the pack belongs to. The product is AUTHORITATIVE — we DERIVE service_kind (from product.kind) and
    coach_user_id (from product.coach_user_id) FROM it, and store the product_id so this pack ONLY
    draws for that exact service. BACKWARD-COMPATIBLE: a caller that passes only service_kind (+coach,
    no product_id) creates a legacy unscoped pack exactly as before (product_id NULL → matches by
    kind+coach until a backfill sets it).

    OWNER RULE (money-correctness): a LESSON or CLASS pack ALWAYS belongs to the coach who sold it
    (that coach gets paid on the sale), so a coach is REQUIRED for both — DERIVED from the product when
    given, else the coach_user_id arg; a missing coach raises ValueError('COACH_REQUIRED'). A COURT
    pack is coachless (courts have no coach) → coach NULL."""
    prod_id = None
    if product_id:
        prow = session.execute(
            text("SELECT id, kind, coach_user_id FROM billing.product "
                 "WHERE club_id = :c AND id = :p"),
            {"c": str(club_id), "p": str(product_id)},
        ).mappings().first()
        if not prow:
            raise ValueError("PRODUCT_NOT_FOUND")
        prod_id = str(prow["id"])
        # The product is authoritative: derive kind + coach from it (owner-inherited).
        service_kind = _SERVICE_BY_PRODUCT_KIND.get(prow["kind"], service_kind)
        coach_user_id = str(prow["coach_user_id"]) if prow["coach_user_id"] else None
    if service_kind not in ("court", "lesson", "class"):
        raise ValueError(f"bad service_kind '{service_kind}'")
    if service_kind in ("lesson", "class"):
        if not (coach_user_id and str(coach_user_id).strip()):
            raise ValueError("COACH_REQUIRED")
        coach = str(coach_user_id)
    else:
        coach = None
    pid = session.execute(
        text("""
            INSERT INTO billing.bundle_plan
                (club_id, service_kind, coach_user_id, product_id, label, sessions_count,
                 duration_minutes, price_minor, currency_code, validity_days, active)
            VALUES (:c, :sk, :coach, :prod, :label, :n, :dur, :price, :cur, :validity, true)
            RETURNING id
        """),
        {"c": str(club_id), "sk": service_kind, "coach": coach, "prod": prod_id,
         "label": (label or "").strip() or None, "n": int(sessions_count),
         "dur": int(duration_minutes) if duration_minutes else None,
         "price": int(price_minor), "cur": _club_currency(session, club_id=club_id),
         "validity": int(validity_days) if validity_days else None},
    ).scalar_one()
    return get_plan(session, club_id=club_id, plan_id=pid)


def update_plan(session, *, club_id, plan_id, label=None, sessions_count=None,
                duration_minutes=None, price_minor=None, coach_user_id=None,
                validity_days=None, active=None, status=None, _clear_coach=False,
                _clear_duration=False, _clear_validity=False) -> Optional[Dict[str, Any]]:
    """COALESCE partial update of a plan. Pass _clear_* to null a nullable field, or `status`
    (active|dormant|retired) to move the plan through its lifecycle. `active` is kept in sync with
    status (active = status='active') so every customer-facing read (active=true) Just Works. Scoped
    to the club. Past purchases (wallets) are untouched — they carry their own denormalised terms."""
    if status is not None and status not in ("active", "dormant", "retired"):
        raise ValueError(f"bad status '{status}'")
    # OWNER RULE: a lesson/class pack must ALWAYS keep its coach (they get paid) — refuse to clear it.
    if _clear_coach:
        cur = get_plan(session, club_id=club_id, plan_id=plan_id)
        if cur and cur["service_kind"] in ("lesson", "class"):
            raise ValueError("COACH_REQUIRED")
    res = session.execute(
        text("""
            UPDATE billing.bundle_plan SET
                label            = CASE WHEN :lbl_set THEN :label ELSE label END,
                sessions_count   = COALESCE(:n, sessions_count),
                duration_minutes = CASE WHEN :clr_dur THEN NULL
                                        ELSE COALESCE(:dur, duration_minutes) END,
                price_minor      = COALESCE(:price, price_minor),
                coach_user_id    = CASE WHEN :clr_coach THEN NULL
                                        ELSE COALESCE(:coach, coach_user_id) END,
                validity_days    = CASE WHEN :clr_val THEN NULL
                                        ELSE COALESCE(:validity, validity_days) END,
                status           = COALESCE(:status, status),
                active           = CASE WHEN :status IS NOT NULL THEN (:status = 'active')
                                        ELSE COALESCE(:active, active) END,
                updated_at       = now()
            WHERE club_id = :c AND id = :id
            RETURNING id
        """),
        {"c": str(club_id), "id": str(plan_id),
         "lbl_set": label is not None, "label": (label or "").strip() or None,
         "n": int(sessions_count) if sessions_count is not None else None,
         "clr_dur": bool(_clear_duration),
         "dur": int(duration_minutes) if duration_minutes else None,
         "price": int(price_minor) if price_minor is not None else None,
         "clr_coach": bool(_clear_coach),
         "coach": str(coach_user_id) if coach_user_id else None,
         "clr_val": bool(_clear_validity),
         "validity": int(validity_days) if validity_days else None,
         "active": active, "status": status},
    ).mappings().first()
    if not res:
        return None
    return get_plan(session, club_id=club_id, plan_id=plan_id)


def set_plan_status(session, *, club_id, plan_id, status) -> Optional[Dict[str, Any]]:
    """Move a plan to active | dormant (configured but hidden from customers) | retired."""
    return update_plan(session, club_id=club_id, plan_id=plan_id, status=status)


def deactivate_plan(session, *, club_id, plan_id) -> bool:
    """Soft-delete = retire: stop offering the plan (hidden from customers). Past wallets stand."""
    return set_plan_status(session, club_id=club_id, plan_id=plan_id, status="retired") is not None


# ---------------------------------------------------------------------------
# lazy expiry — flip past-expires_at active wallets to expired (no cron)
# ---------------------------------------------------------------------------

def expire_due(session, *, club_id) -> int:
    """Lazy expiry (NO cron, like diary.release_expired_holds): flip any active wallet whose
    expires_at has passed to 'expired', and log an 'expire' ledger row (idempotent on the unique
    (wallet, NULL, 'expire')). Called opportunistically before matching/availability. Cheap."""
    expired = session.execute(
        text("UPDATE billing.token_wallet "
             "SET status = 'expired', updated_at = now() "
             "WHERE club_id = :c AND status = 'active' "
             "  AND expires_at IS NOT NULL AND expires_at < CURRENT_DATE "
             "RETURNING id"),
        {"c": str(club_id)},
    ).mappings().all()
    for w in expired:
        session.execute(
            text("INSERT INTO billing.token_ledger (club_id, wallet_id, booking_id, kind, delta, reason) "
                 "VALUES (:c, :w, NULL, 'expire', 0, 'past expires_at') "
                 "ON CONFLICT (wallet_id, booking_id, kind) WHERE kind <> 'adjust' DO NOTHING"),
            {"c": str(club_id), "w": str(w["id"])},
        )
    return len(expired)


# ---------------------------------------------------------------------------
# match — the best active wallet to draw from (FOR UPDATE)
# ---------------------------------------------------------------------------

def match_wallet(session, *, club_id, user_id, service_kind, duration_minutes=None,
                 coach_user_id=None, product_id=None) -> Optional[Dict[str, Any]]:
    """The best ACTIVE wallet to draw from for this booking, or None.

    Unit model (docs/specs/02): the balance is held in MINUTES, so ONE pack covers any duration —
    a booking draws minutes proportional to its length (a 90-min court off a 60-min unit = 1.5).
    Match rules (generic across court/lesson/class):
      * service_kind equal,
      * wallet coach_user_id equals the booking's OR is NULL (any coach),
      * PER-SERVICE (backward-compatible): the wallet's product_id equals the booking's SPECIFIC
        service, OR the wallet has NO product (legacy unscoped — draws by kind+coach as before), OR
        the booking has no product_id. So a Private-lesson pack (product=Private) NEVER draws for a
        Semi-private lesson; a legacy NULL-product pack still draws for any service of its kind+coach.
      * status = 'active', minutes_remaining > 0, not past expires_at.
    Duration is NO LONGER a match gate — any positive balance can be drawn against any duration
    (the draw computes the cost; the customer-wins tail lets the last credit cover any length).
    Preference: a PRODUCT-SPECIFIC wallet beats a legacy unscoped one, then the wallet EXPIRING
    SOONEST (use-it-or-lose-it; NULL expiry last), then the one with the FEWEST minutes left (drain
    partial packs first), then oldest.

    `SELECT … FOR UPDATE` locks the chosen wallet row so two concurrent draws for the same member
    serialise — combined with the token_ledger unique + the minutes_remaining>=0 CHECK, a wallet can
    NEVER go below zero or be double-spent. Returns {id, base_minutes, minutes_remaining, ...} or None.
    """
    # Lazily expire before matching so an expired wallet is never selected.
    expire_due(session, club_id=club_id)
    row = session.execute(
        text("""
            SELECT id, club_id, user_id, service_kind, coach_user_id, product_id, duration_minutes,
                   base_minutes, tokens_total, tokens_remaining,
                   minutes_total, minutes_remaining, status, expires_at
            FROM billing.token_wallet
            WHERE club_id = :c
              AND user_id = :u
              AND service_kind = :sk
              AND status = 'active'
              AND minutes_remaining > 0
              AND (expires_at IS NULL OR expires_at >= CURRENT_DATE)
              AND (coach_user_id IS NULL OR CAST(:coach AS uuid) IS NULL
                   OR coach_user_id = CAST(:coach AS uuid))
              AND (product_id IS NULL OR CAST(:product AS uuid) IS NULL
                   OR product_id = CAST(:product AS uuid))
            ORDER BY (product_id IS NULL) ASC,
                     (expires_at IS NULL) ASC, expires_at ASC,
                     minutes_remaining ASC, created_at ASC
            LIMIT 1
            FOR UPDATE
        """),
        {"c": str(club_id), "u": str(user_id) if user_id else None, "sk": service_kind,
         "dur": int(duration_minutes) if duration_minutes is not None else None,
         "coach": str(coach_user_id) if coach_user_id else None,
         "product": str(product_id) if product_id else None},
    ).mappings().first()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# draw / credit — the careful, idempotent token movements
# ---------------------------------------------------------------------------

def draw_token(session, *, club_id, wallet, booking_id, reason="booking",
               duration_minutes=None) -> bool:
    """Draw a booking's worth of MINUTES from `wallet` for `booking_id`. MUST run inside the caller's
    booking tx, against a wallet locked by match_wallet (FOR UPDATE).

    Cost = the booking's own duration (court/lesson), or ONE full unit (base_minutes) when duration
    isn't applicable (a class = one session). CUSTOMER-WINS TAIL: we never draw more than the balance
    — `LEAST(want, minutes_remaining)` — so the last credit covers a booking of any length and the
    wallet lands exactly at 0 (never blocked while a positive balance remains).

    Insert a ('draw', -minutes) ledger row guarded by the unique (wallet_id, booking_id, 'draw') — a
    re-run for the SAME booking inserts nothing (idempotent). ONLY when the row inserts do we draw the
    minutes down (and flip active->exhausted at 0). tokens_remaining is kept as a customer-favourable
    CEIL of the remaining sessions (a half-unit still shows as 1). Returns True iff minutes were
    consumed on THIS call (False = already drawn for this booking)."""
    wallet_id = wallet["id"] if isinstance(wallet, dict) else wallet
    base = int(wallet.get("base_minutes") or 0) if isinstance(wallet, dict) else 0
    remaining = int(wallet.get("minutes_remaining") or 0) if isinstance(wallet, dict) else 0
    # how many minutes this booking wants: its duration, else one full unit (class/per-session).
    want = int(duration_minutes) if duration_minutes else (base or 60)
    drawn = min(want, remaining) if remaining > 0 else want  # customer-wins tail
    inserted = session.execute(
        text("""
            INSERT INTO billing.token_ledger
                (club_id, wallet_id, booking_id, kind, delta, reason)
            VALUES (:c, :w, :b, 'draw', :delta, :reason)
            ON CONFLICT (wallet_id, booking_id, kind) WHERE kind <> 'adjust' DO NOTHING
            RETURNING id
        """),
        {"c": str(club_id), "w": str(wallet_id), "delta": -int(drawn),
         "b": str(booking_id) if booking_id else None, "reason": reason},
    ).first()
    if not inserted:
        return False  # already drawn for this booking — idempotent no-op
    session.execute(
        text("""
            UPDATE billing.token_wallet
            SET minutes_remaining = GREATEST(minutes_remaining - :drawn, 0),
                tokens_remaining = CEIL(GREATEST(minutes_remaining - :drawn, 0)::numeric
                                        / NULLIF(base_minutes, 0)),
                status = CASE WHEN minutes_remaining - :drawn <= 0 THEN 'exhausted' ELSE status END,
                updated_at = now()
            WHERE id = :w
        """),
        {"w": str(wallet_id), "drawn": int(drawn)},
    )
    return True


def credit_token(session, *, club_id, booking_id, reason="cancellation") -> bool:
    """Credit ONE token BACK for a previously token-settled `booking_id` (cancellation). Idempotent.

    Find the wallet that DREW for this booking (the 'draw' ledger row); insert a ('credit', +1) row
    guarded by the unique (wallet_id, booking_id, 'credit') — so a re-cancel credits NOTHING. ONLY
    when the credit row inserts do we increment tokens_remaining and reactivate exhausted->active
    (when the wallet hasn't expired). Returns True iff a token was credited on THIS call.
    """
    draw = session.execute(
        text("SELECT wallet_id, delta FROM billing.token_ledger "
             "WHERE club_id = :c AND booking_id = :b AND kind = 'draw' LIMIT 1"),
        {"c": str(club_id), "b": str(booking_id) if booking_id else None},
    ).mappings().first()
    if not draw:
        return False  # this booking was never token-settled — nothing to credit
    wallet_id = draw["wallet_id"]
    credited = abs(int(draw["delta"]))  # restore EXACTLY the minutes this booking drew (tail-safe)
    inserted = session.execute(
        text("""
            INSERT INTO billing.token_ledger
                (club_id, wallet_id, booking_id, kind, delta, reason)
            VALUES (:c, :w, :b, 'credit', :delta, :reason)
            ON CONFLICT (wallet_id, booking_id, kind) WHERE kind <> 'adjust' DO NOTHING
            RETURNING id
        """),
        {"c": str(club_id), "w": str(wallet_id), "delta": credited,
         "b": str(booking_id) if booking_id else None, "reason": reason},
    ).first()
    if not inserted:
        return False  # already credited for this booking — idempotent no-op
    session.execute(
        text("""
            UPDATE billing.token_wallet
            SET minutes_remaining = LEAST(minutes_remaining + :credited, minutes_total),
                tokens_remaining = CEIL(LEAST(minutes_remaining + :credited, minutes_total)::numeric
                                        / NULLIF(base_minutes, 0)),
                status = CASE
                    WHEN status = 'exhausted'
                         AND (expires_at IS NULL OR expires_at >= CURRENT_DATE) THEN 'active'
                    ELSE status END,
                updated_at = now()
            WHERE id = :w
        """),
        {"w": str(wallet_id), "credited": credited},
    )
    return True


# ---------------------------------------------------------------------------
# admin manual wallet ops — adjust balance / expire a pack (money-adjacent, audited)
# ---------------------------------------------------------------------------

def adjust_wallet(session, *, club_id, wallet_id, delta_minutes, reason,
                  actor_user_id) -> Dict[str, Any]:
    """MANUAL admin balance change on a prepaid pack wallet — money-adjacent, fully audited.

    Add or remove MINUTES from a wallet (delta_minutes signed). The wallet row is SELECT … FOR UPDATE
    locked (serialise against a concurrent draw), scoped by club_id AND wallet_id. A delta of 0 is
    rejected ('NO_CHANGE') so the ledger never carries empty noise; an unknown/wrong-club wallet raises
    'WALLET_NOT_FOUND'.

    Clamp: new_remaining is floored at 0 (never negative). A genuine TOP-UP (delta pushes remaining
    above the pack total) raises minutes_total to match (and the display tokens_total with it), so an
    owner can legitimately add sessions to a pack. tokens_remaining/total (display) are recomputed as a
    customer-favourable CEIL of minutes ÷ base_minutes (the same rounding draw/credit use).

    Status: 0 remaining -> 'exhausted'; >0 remaining and was exhausted/expired -> 'active' (match_wallet
    still gates draws on expires_at, so a date-expired wallet stays unusable). Writes ONE token_ledger
    kind='adjust' row (booking_id NULL, delta=delta_minutes, reason, actor_user_id) — deliberately NOT
    idempotency-guarded (the partial unique index excludes 'adjust'), so repeated manual adjusts stack.

    Returns {wallet_id, minutes_remaining, minutes_total, tokens_remaining, status, delta_minutes}.
    Never commits (caller composes via db.session_scope())."""
    delta = int(delta_minutes or 0)
    if delta == 0:
        raise ValueError("NO_CHANGE")  # reject an empty adjust so the audit ledger stays meaningful
    row = session.execute(
        text("""
            SELECT id, club_id, base_minutes, tokens_total, tokens_remaining,
                   minutes_total, minutes_remaining, status, expires_at
            FROM billing.token_wallet
            WHERE club_id = :c AND id = :w
            FOR UPDATE
        """),
        {"c": str(club_id), "w": str(wallet_id)},
    ).mappings().first()
    if not row:
        raise ValueError("WALLET_NOT_FOUND")

    base = int(row["base_minutes"] or 0) or 60           # unit length; guard divide-by-zero
    cur_remaining = int(row["minutes_remaining"] or 0)
    cur_total = int(row["minutes_total"] or 0)

    new_remaining = max(cur_remaining + delta, 0)          # clamp at 0 (never negative)
    new_total = max(cur_total, new_remaining)              # a top-up beyond total raises the total
    # display counts = customer-favourable CEIL of minutes / base (matches draw/credit rounding)
    new_tokens_remaining = (new_remaining + base - 1) // base if new_remaining > 0 else 0
    new_tokens_total = (new_total + base - 1) // base if new_total > 0 else 0

    new_status = row["status"]
    if new_remaining == 0:
        new_status = "exhausted"
    elif row["status"] in ("exhausted", "expired"):
        new_status = "active"

    session.execute(
        text("""
            UPDATE billing.token_wallet
            SET minutes_remaining = :mr,
                minutes_total     = :mt,
                tokens_remaining  = :tr,
                tokens_total      = :tt,
                status            = :st,
                updated_at        = now()
            WHERE id = :w
        """),
        {"mr": new_remaining, "mt": new_total, "tr": new_tokens_remaining,
         "tt": new_tokens_total, "st": new_status, "w": str(wallet_id)},
    )
    # Audit row — NO ON CONFLICT: the partial unique index excludes kind='adjust', so repeated
    # manual adjustments on the same wallet stack (each is a distinct deliberate action).
    session.execute(
        text("""
            INSERT INTO billing.token_ledger
                (club_id, wallet_id, booking_id, kind, delta, reason, actor_user_id)
            VALUES (:c, :w, NULL, 'adjust', :delta, :reason, :actor)
        """),
        {"c": str(club_id), "w": str(wallet_id), "delta": delta,
         "reason": (reason or None),
         "actor": str(actor_user_id) if actor_user_id else None},
    )
    return {"wallet_id": str(wallet_id), "minutes_remaining": new_remaining,
            "minutes_total": new_total, "tokens_remaining": new_tokens_remaining,
            "status": new_status, "delta_minutes": delta}


def expire_wallet(session, *, club_id, wallet_id, reason,
                  actor_user_id) -> Dict[str, Any]:
    """SOFT-expire a prepaid pack (admin): status -> 'expired' and the remaining balance is zeroed
    (cleaner "no longer usable" than leaving a dangling balance). NEVER hard-deletes — the wallet row
    and its ledger stay for audit.

    The wallet is SELECT … FOR UPDATE locked, scoped by club_id AND wallet_id ('WALLET_NOT_FOUND' if
    absent/wrong club). Writes an audited kind='expire' ledger row with delta = -(minutes forfeited)
    (the truthful balance change, not 0), reason + actor_user_id. Because a LAZY auto-expire
    (expire_due) may already have logged a (wallet, NULL, 'expire') row, the insert is
    ON CONFLICT … DO UPDATE — it stamps the manual actor/reason/delta onto that row and re-expiring is
    an idempotent re-stamp (no duplicate rows, balance already 0).

    Returns {wallet_id, minutes_remaining, minutes_total, tokens_remaining, status, delta_minutes}.
    Never commits (caller composes)."""
    row = session.execute(
        text("""
            SELECT id, base_minutes, minutes_total, minutes_remaining, tokens_remaining, status
            FROM billing.token_wallet
            WHERE club_id = :c AND id = :w
            FOR UPDATE
        """),
        {"c": str(club_id), "w": str(wallet_id)},
    ).mappings().first()
    if not row:
        raise ValueError("WALLET_NOT_FOUND")
    forfeited = int(row["minutes_remaining"] or 0)

    session.execute(
        text("""
            UPDATE billing.token_wallet
            SET status = 'expired',
                minutes_remaining = 0,
                tokens_remaining = 0,
                updated_at = now()
            WHERE id = :w
        """),
        {"w": str(wallet_id)},
    )
    # Audited expire row. ON CONFLICT … DO UPDATE (partial-index predicate) so a manual expire stamps
    # the actor/reason even if expire_due already logged a system 'expire' row; re-expiring re-stamps.
    session.execute(
        text("""
            INSERT INTO billing.token_ledger
                (club_id, wallet_id, booking_id, kind, delta, reason, actor_user_id)
            VALUES (:c, :w, NULL, 'expire', :delta, :reason, :actor)
            ON CONFLICT (wallet_id, booking_id, kind) WHERE kind <> 'adjust'
            DO UPDATE SET delta         = EXCLUDED.delta,
                          reason        = EXCLUDED.reason,
                          actor_user_id = EXCLUDED.actor_user_id
        """),
        {"c": str(club_id), "w": str(wallet_id), "delta": -forfeited,
         "reason": (reason or "admin expired"),
         "actor": str(actor_user_id) if actor_user_id else None},
    )
    return {"wallet_id": str(wallet_id), "minutes_remaining": 0,
            "minutes_total": int(row["minutes_total"] or 0), "tokens_remaining": 0,
            "status": "expired", "delta_minutes": -forfeited}


# ---------------------------------------------------------------------------
# member-facing wallet listing
# ---------------------------------------------------------------------------

def wallets_for(session, *, club_id, user_id, service_kind=None,
                active_only=False) -> List[Dict[str, Any]]:
    """A member's token wallets (remaining + expiry), most-recently-purchased first. Runs a lazy
    expire_due first so the statuses are current. active_only -> only drawable wallets."""
    expire_due(session, club_id=club_id)
    where = ["w.club_id = :c", "w.user_id = :u"]
    params: Dict[str, Any] = {"c": str(club_id), "u": str(user_id) if user_id else None}
    if service_kind:
        where.append("w.service_kind = :sk")
        params["sk"] = service_kind
    if active_only:
        where.append("w.status = 'active' AND w.tokens_remaining > 0")
    rows = session.execute(
        text("SELECT w.id, w.service_kind, w.coach_user_id, w.product_id, w.duration_minutes, "
             "       w.base_minutes, w.tokens_total, w.tokens_remaining, w.minutes_total, "
             "       w.minutes_remaining, w.status, w.expires_at, w.purchased_at, w.bundle_plan_id, "
             "       bp.label "
             "FROM billing.token_wallet w "
             "LEFT JOIN billing.bundle_plan bp ON bp.id = w.bundle_plan_id "
             "WHERE " + " AND ".join(where) + " "
             "ORDER BY w.created_at DESC"),
        params,
    ).mappings().all()
    out = []
    for r in rows:
        base = int(r["base_minutes"] or 0) or 60
        mins_left = int(r["minutes_remaining"] or 0)
        out.append({
            "id": str(r["id"]),
            "service_kind": r["service_kind"],
            "coach_user_id": str(r["coach_user_id"]) if r["coach_user_id"] else None,
            "product_id": str(r["product_id"]) if r["product_id"] else None,
            "duration_minutes": int(r["duration_minutes"]) if r["duration_minutes"] is not None else None,
            "base_minutes": base,
            "tokens_total": int(r["tokens_total"] or 0),       # nominal session count ("of N")
            "tokens_remaining": int(r["tokens_remaining"] or 0),  # legacy/display (ceil of sessions)
            # Precise remaining, for the UI ("4.5 of 10 sessions left"): minutes ÷ unit length.
            "minutes_total": int(r["minutes_total"] or 0),
            "minutes_remaining": mins_left,
            "sessions_remaining": round(mins_left / base, 2) if base else 0,
            "status": r["status"],
            "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
            "label": _plan_label(r["label"], r["service_kind"], r["tokens_total"]),
        })
    return out


def has_matching_wallet(session, *, club_id, user_id, service_kind, duration_minutes=None,
                        coach_user_id=None, product_id=None) -> Optional[Dict[str, Any]]:
    """Read-only probe for the UI: is there a drawable wallet for this service+duration(+coach)?
    Returns {wallet_id, tokens_remaining} or None. Does NOT lock (no FOR UPDATE) — purely
    advisory; the real draw re-matches under a lock at booking time. Product-aware + backward-
    compatible, mirroring match_wallet (a product-scoped wallet only counts for that product)."""
    expire_due(session, club_id=club_id)
    row = session.execute(
        text("""
            SELECT id, base_minutes, minutes_remaining, tokens_remaining
            FROM billing.token_wallet
            WHERE club_id = :c AND user_id = :u AND service_kind = :sk
              AND status = 'active' AND minutes_remaining > 0
              AND (expires_at IS NULL OR expires_at >= CURRENT_DATE)
              AND (coach_user_id IS NULL OR CAST(:coach AS uuid) IS NULL
                   OR coach_user_id = CAST(:coach AS uuid))
              AND (product_id IS NULL OR CAST(:product AS uuid) IS NULL
                   OR product_id = CAST(:product AS uuid))
            ORDER BY (product_id IS NULL) ASC,
                     (expires_at IS NULL) ASC, expires_at ASC, minutes_remaining ASC
            LIMIT 1
        """),
        {"c": str(club_id), "u": str(user_id) if user_id else None, "sk": service_kind,
         "coach": str(coach_user_id) if coach_user_id else None,
         "product": str(product_id) if product_id else None},
    ).mappings().first()
    if not row:
        return None
    base = int(row["base_minutes"] or 0) or 60
    mins = int(row["minutes_remaining"] or 0)
    return {"wallet_id": str(row["id"]), "tokens_remaining": int(row["tokens_remaining"] or 0),
            "minutes_remaining": mins, "base_minutes": base,
            "sessions_remaining": round(mins / base, 2) if base else 0}


# ---------------------------------------------------------------------------
# purchase — create an online order + a pending wallet linked by order_id
# ---------------------------------------------------------------------------

def _coach_service_product(session, *, club_id, coach_user_id, kind):
    """The coach's billing.product of `kind` ('lesson'|'class') (+ a price_id to hang the pack order
    line on) so a LESSON or CLASS pack purchase carries the coach/product → the commission engine
    attributes the collected payment (lesson_commission / class_commission, keyed off product.kind).
    Returns (product_id, price_id) or (None, None). Guarded: billing.product.coach_user_id is added
    by the coach lane; absent in isolation → (None, None)."""
    try:
        row = session.execute(
            text("""
                SELECT pr.id AS product_id,
                       (SELECT p.id FROM billing.price p
                        WHERE p.product_id = pr.id AND p.active = true
                        ORDER BY p.amount_minor DESC LIMIT 1) AS price_id
                FROM billing.product pr
                WHERE pr.club_id = :c AND pr.kind = :kind AND pr.coach_user_id = :coach
                ORDER BY pr.created_at LIMIT 1
            """),
            {"c": str(club_id), "coach": str(coach_user_id), "kind": kind},
        ).mappings().first()
        if row:
            return (str(row["product_id"]) if row["product_id"] else None,
                    str(row["price_id"]) if row["price_id"] else None)
    except Exception:
        log.debug("coach %s product lookup suppressed (coach col absent)", kind, exc_info=False)
    return (None, None)


_BUNDLE_PAY_MODES = ("online", "at_court", "monthly_account")


def create_bundle_order(session, *, club_id, user_id, bundle_plan_id,
                        settlement_mode="online") -> Optional[Dict[str, Any]]:
    """Create an order for a bundle plan + a token_wallet linked by order_id. `settlement_mode`:
      online           -> 'awaiting_payment'; pay via Yoco; the webhook activates the wallet.
      at_court/monthly -> 'open' order (owed, on the unified statement); the wallet is granted
                          IMMEDIATELY so the member can use the pack now (collect at desk / month-end).
    For a COACH LESSON or CLASS pack the order line carries the coach's own product/price of that
    kind so the commission fan-out accrues to the selling coach on the collected purchase (a lesson
    pack pays lesson_commission, a class pack class_commission). Returns {order_id, amount_minor, currency, plan,
    settlement_mode, needs_checkout, activated} or None (plan missing/inactive)."""
    mode = (settlement_mode or "online").strip().lower()
    if mode not in _BUNDLE_PAY_MODES:
        mode = "online"
    online = (mode == "online")

    plan = get_plan(session, club_id=club_id, plan_id=bundle_plan_id)
    if not plan or not plan["active"]:
        return None
    amount = int(plan["price_minor"] or 0)
    currency = plan["currency"]

    order_id = session.execute(
        text("""
            INSERT INTO billing."order"
                (club_id, user_id, amount_minor, currency_code, settlement_mode, status)
            VALUES (:c, :u, :amt, :cur, :mode, :st)
            RETURNING id
        """),
        {"c": str(club_id), "u": str(user_id) if user_id else None,
         "amt": amount, "cur": currency, "mode": mode,
         "st": ("awaiting_payment" if online else "open")},
    ).scalar_one()
    order_id = str(order_id)

    # The order line documents the purchase (powers admin payments + receipts). For a coach LESSON
    # OR CLASS pack, carry the coach's own product/price of that kind so the commission fan-out
    # attributes the collected payment to the selling coach (lesson_commission / class_commission).
    # The line amount stays the pack price — the price_id only resolves product/coach, exactly as
    # lessons already do (the pack, not the single-session rate, is what's charged).
    price_id = None
    if plan["service_kind"] in ("lesson", "class"):
        if plan.get("product_id"):
            # PER-SERVICE pack: hang the order line on THIS exact service's price so the commission
            # fan-out attributes the collected purchase to precisely that product (+ its coach).
            price_id = session.execute(
                text("SELECT id FROM billing.price WHERE club_id = :c AND product_id = :p "
                     "AND active = true ORDER BY amount_minor DESC LIMIT 1"),
                {"c": str(club_id), "p": plan["product_id"]},
            ).scalar()
        elif plan["coach_user_id"]:
            _prod, price_id = _coach_service_product(
                session, club_id=club_id, coach_user_id=plan["coach_user_id"],
                kind=_PRODUCT_KIND_BY_SERVICE.get(plan["service_kind"], plan["service_kind"]))
    session.execute(
        text("""
            INSERT INTO billing.order_line
                (order_id, club_id, description, price_id, qty, amount_minor)
            VALUES (:oid, :c, :desc, :pid, 1, :amt)
        """),
        {"oid": order_id, "c": str(club_id),
         "desc": f"Session pack — {plan['label']}", "pid": price_id, "amt": amount},
    )

    # PENDING wallet, linked by order_id, carrying the plan's denormalised terms. status 'pending'
    # is NOT drawable (match_wallet requires 'active'); activation flips it to 'active' + grants.
    session.execute(
        text("""
            INSERT INTO billing.token_wallet
                (club_id, user_id, bundle_plan_id, order_id, service_kind, coach_user_id, product_id,
                 duration_minutes, base_minutes, tokens_total, tokens_remaining,
                 minutes_total, minutes_remaining, status)
            VALUES (:c, :u, :plan, :oid, :sk, :coach, :prod, :dur, :base, 0, 0, 0, 0, 'pending')
        """),
        {"c": str(club_id), "u": str(user_id) if user_id else None,
         "plan": plan["id"], "oid": order_id, "sk": plan["service_kind"],
         "coach": plan["coach_user_id"], "prod": plan.get("product_id"),
         "dur": plan["duration_minutes"],
         "base": int(plan["duration_minutes"]) if plan["duration_minutes"] else 60},
    )

    out = {"order_id": order_id, "amount_minor": amount, "currency": currency, "plan": plan,
           "settlement_mode": mode, "needs_checkout": online, "activated": False}
    if not online:
        # Offline pack: usable now; the 'open' order is settled at the desk / month-end via the statement.
        out["activation"] = _grant_wallet_now(session, order_id=order_id, provider="manual")
        out["activated"] = True
    return out


def is_bundle_order(session, *, order_id) -> bool:
    """True if this order is a bundle purchase (has a linked token_wallet)."""
    row = session.execute(
        text("SELECT 1 FROM billing.token_wallet WHERE order_id = :oid LIMIT 1"),
        {"oid": str(order_id)},
    ).first()
    return row is not None


def activate_wallet_for_order(session, *, order_id, provider="yoco") -> Dict[str, Any]:
    """Activate the PENDING wallet linked to a PAID bundle order. Called by the Yoco webhook AFTER
    apply_payment_event marks the order 'paid'. Defence-in-depth paid-gate, then grants. Returns
    {ok, status: 'granted'|'already_active'|'no_bundle_order'|'order_not_paid', ...}."""
    paid = session.execute(
        text('SELECT 1 FROM billing."order" WHERE id = :oid AND status = :s'),
        {"oid": str(order_id), "s": "paid"},
    ).first()
    if not paid:
        return {"ok": True, "status": "order_not_paid"}
    return _grant_wallet_now(session, order_id=order_id, provider=provider)


def _grant_wallet_now(session, *, order_id, provider="yoco") -> Dict[str, Any]:
    """Grant the PENDING wallet for an order — NO paid-gate, so an OFFLINE pack (pay-at-club /
    month-end) is usable immediately while its 'open' order sits on the statement. Grants the plan's
    sessions_count + sets expires_at. IDEMPOTENT keyed off order_id (an already-active wallet is a
    no-op)."""
    wallet = session.execute(
        text("SELECT w.id, w.club_id, w.user_id, w.status, w.bundle_plan_id, w.tokens_total "
             "FROM billing.token_wallet w WHERE w.order_id = :oid "
             "ORDER BY w.created_at LIMIT 1"),
        {"oid": str(order_id)},
    ).mappings().first()
    if not wallet:
        return {"ok": True, "status": "no_bundle_order"}

    # Idempotency guard: an already-active (or exhausted/expired — already granted) wallet for this
    # order is a no-op. We only grant a wallet still 'pending'.
    if wallet["status"] != "pending":
        return {"ok": True, "status": "already_active", "wallet_id": str(wallet["id"]),
                "tokens_total": int(wallet["tokens_total"] or 0)}

    plan = session.execute(
        text("SELECT sessions_count, validity_days, label, duration_minutes "
             "FROM billing.bundle_plan WHERE id = :p"),
        {"p": str(wallet["bundle_plan_id"])},
    ).mappings().first()
    n = int(plan["sessions_count"]) if plan else 0
    # The pack's UNIT length (the divisor). A pack always has a base now; default 60 for a legacy
    # 'any duration' plan. Total credit = sessions × base, held in minutes.
    base = int(plan["duration_minutes"]) if (plan and plan["duration_minutes"]) else 60
    minutes = n * base
    validity = int(plan["validity_days"]) if (plan and plan["validity_days"] is not None) else None

    row = session.execute(
        text("""
            UPDATE billing.token_wallet
            SET status = 'active',
                base_minutes = :base,
                tokens_total = :n,
                tokens_remaining = :n,
                minutes_total = :minutes,
                minutes_remaining = :minutes,
                purchased_at = now(),
                expires_at = CASE WHEN CAST(:validity AS int) IS NULL THEN NULL
                                  ELSE (CURRENT_DATE
                                        + make_interval(days => CAST(:validity AS int)))::date END,
                updated_at = now()
            WHERE id = :w
            RETURNING expires_at
        """),
        {"n": n, "base": base, "minutes": minutes, "validity": validity, "w": str(wallet["id"])},
    ).mappings().first()

    # Audit: a single 'grant' ledger row in MINUTES (idempotent on the unique (wallet, NULL, 'grant')).
    session.execute(
        text("INSERT INTO billing.token_ledger (club_id, wallet_id, booking_id, kind, delta, reason) "
             "VALUES (:c, :w, NULL, 'grant', :minutes, :reason) "
             "ON CONFLICT (wallet_id, booking_id, kind) WHERE kind <> 'adjust' DO NOTHING"),
        {"c": str(wallet["club_id"]), "w": str(wallet["id"]), "minutes": minutes,
         "reason": f"{provider} bundle purchase"},
    )

    exp = row["expires_at"] if row else None
    return {"ok": True, "status": "granted", "wallet_id": str(wallet["id"]),
            "user_id": str(wallet["user_id"]) if wallet.get("user_id") else None,
            "label": (plan["label"] if plan else None),
            "tokens_total": n, "tokens_remaining": n,
            "expires_at": exp.isoformat() if hasattr(exp, "isoformat") else exp}

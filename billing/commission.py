# billing/commission.py — the commission / coaching-settlement engine (Phase D).
#
# THE commercial core: the owner monetises coaches via rent AND/OR commission % (additive,
# per coach); commission accrues on COLLECTED coaching revenue (online = at payment; arrears
# = when the coach marks the off-platform invoice collected). Everything is ex-VAT net.
# Nothing is hardcoded — every rate/rent is owner-configured data (docs/specs/01).
#
# Pure SQL via SQLAlchemy Core text(); every fn takes an explicit `session` and NEVER commits
# (callers compose). Every query is club_id-scoped (multi-tenant). The split-on-collection
# fan-out (record_split_for_order) is called from apply_payment_event's charge_succeeded
# branch — savepoint-guarded and idempotent on (payment_id, order_line_id, party_type), so a
# re-delivered webhook adds NO second split and apply_payment_event's semantics are untouched.
#
# Public surface:
#   resolve_commission_pct(...)   -> Decimal   (coach+product > product > coach > club > 0)
#   record_split_for_order(...)               (the payment-success fan-out; idempotent)
#   accrue_arrears_for_club(...)              (lazy: confirmed-unpaid lessons -> coach_arrears)
#   mark_arrears_collected(...)               (coach marks an arrears item collected -> accrues)
#   coach_balance(...)            -> int      (signed ledger balance, minor units)
#   coach_statement(...)         -> dict     (per-client paid/owed/net for a month)
#
# Money math (docs/specs/01): base = ex-VAT net line gross; owner_cut = round(gross*pct/100);
# coach_net = gross - owner_cut. Rent is additive, accrued separately (never netted here).
# Gateway fees are the OWNER's account — never deducted from the coach.

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from sqlalchemy import text

log = logging.getLogger("billing.commission")

# Lesson/class basis labels keyed off the product kind.
_BASIS_BY_KIND = {"lesson": "lesson_commission", "class": "class_commission"}


# ---------------------------------------------------------------------------
# resolution — most-specific active rule wins (mirrors diary/pricing.price_for)
# ---------------------------------------------------------------------------

def resolve_commission_pct(session, *, club_id, product_id=None, coach_user_id=None,
                           at=None) -> Decimal:
    """Resolve the commission % the CLUB keeps for a lesson/class, by specificity then date.

    Precedence (highest -> lowest):
        coach + product  (this coach, this lesson type)   score 3
        product          (any coach, this lesson type)    score 1
        coach            (this coach, any lesson type)     score 2
        club             (default for the whole club)      score 0
        (no rule)        -> 0   (coach keeps 100%, club takes nothing)

    Among candidates we keep only active rules whose effective window contains `at`
    (effective_from <= at AND (effective_to IS NULL OR effective_to > at)). The winner is the
    highest-specificity, then latest effective_from, then highest id (deterministic). Returns a
    Decimal in [0, 100]. `at` defaults to now() (server-side) when None.
    """
    rows = session.execute(
        text("""
            SELECT id, product_id, coach_user_id, commission_pct, effective_from
            FROM billing.commission_rule
            WHERE club_id = :club
              AND active
              AND effective_from <= COALESCE(:at, now())
              AND (effective_to IS NULL OR effective_to > COALESCE(:at, now()))
              AND (product_id    IS NULL OR product_id    = :product)
              AND (coach_user_id IS NULL OR coach_user_id = :coach)
        """),
        {"club": club_id, "at": at, "product": product_id, "coach": coach_user_id},
    ).mappings().all()
    if not rows:
        return Decimal("0")

    def score(r):
        s = 0
        if r["coach_user_id"] is not None:
            s += 2
        if r["product_id"] is not None:
            s += 1
        return (s, r["effective_from"], r["id"])

    best = max(rows, key=score)
    return Decimal(str(best["commission_pct"] or 0))


def _resolve_rule_id(session, *, club_id, product_id, coach_user_id, at=None) -> Optional[str]:
    """Return the winning rule's id (for split.rule_id snapshot), or None if no rule applies."""
    rows = session.execute(
        text("""
            SELECT id, product_id, coach_user_id, commission_pct, effective_from
            FROM billing.commission_rule
            WHERE club_id = :club AND active
              AND effective_from <= COALESCE(:at, now())
              AND (effective_to IS NULL OR effective_to > COALESCE(:at, now()))
              AND (product_id    IS NULL OR product_id    = :product)
              AND (coach_user_id IS NULL OR coach_user_id = :coach)
        """),
        {"club": club_id, "at": at, "product": product_id, "coach": coach_user_id},
    ).mappings().all()
    if not rows:
        return None

    def score(r):
        s = (2 if r["coach_user_id"] is not None else 0) + (1 if r["product_id"] is not None else 0)
        return (s, r["effective_from"], r["id"])

    return str(max(rows, key=score)["id"])


def _split_minor(gross_minor: int, pct: Decimal) -> int:
    """Owner cut = round(gross * pct / 100), HALF_UP, on ex-VAT gross. Returns the OWNER cut
    (minor units). coach_net = gross - owner_cut (computed by the caller)."""
    g = Decimal(int(gross_minor or 0))
    owner = (g * pct / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(owner)


# ---------------------------------------------------------------------------
# the split fan-out — called from apply_payment_event on charge_succeeded
# ---------------------------------------------------------------------------

def record_split_for_order(session, *, club_id, order_id, payment_id, at=None) -> Dict[str, Any]:
    """For each lesson/class order line of a PAID order, resolve the commission rate and write
    an owner + coach commission_split pair plus a coach_ledger earning. The on-COLLECTION
    accrual for online payments (docs/specs/01).

    IDEMPOTENT: every split insert is guarded by ON CONFLICT DO NOTHING on the unique
    (payment_id, order_line_id, party_type); the coach_ledger earning is guarded by the unique
    (entry_type='commission_earning', ref_id=split.id). A replayed webhook re-enters this and
    writes nothing new. SKIPS membership-covered / zero-gross lines (gross R0 -> nothing to
    split). Never deducts a gateway fee from the coach. Returns {splits, earnings, skipped}.

    Resolution of the line's coach + product (no service_id on diary.booking):
      order_line.price_id -> billing.price.product_id -> billing.product (kind, coach_user_id).
      coach = product.coach_user_id, else the booking's denormalised coach_user_id.
    """
    lines = session.execute(
        text("""
            SELECT ol.id AS order_line_id, ol.amount_minor, ol.booking_id, ol.qty,
                   pr.id AS product_id, pr.kind AS product_kind,
                   pr.coach_user_id AS product_coach,
                   b.coach_user_id  AS booking_coach,
                   b.booking_type
            FROM billing.order_line ol
            LEFT JOIN billing.price   p  ON p.id  = ol.price_id
            LEFT JOIN billing.product pr ON pr.id = p.product_id
            LEFT JOIN diary.booking   b  ON b.id  = ol.booking_id
            WHERE ol.order_id = :order_id AND ol.club_id = :club
        """),
        {"order_id": str(order_id), "club": club_id},
    ).mappings().all()

    splits = 0
    earnings = 0
    skipped = 0
    currency = session.execute(
        text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": club_id},
    ).scalar() or "ZAR"

    for ln in lines:
        kind = (ln["product_kind"] or ln["booking_type"] or "").strip().lower()
        basis = _BASIS_BY_KIND.get(kind)
        if basis is None:
            # court / membership / unknown — no coach commission.
            continue
        gross = int(ln["amount_minor"] or 0)
        if gross <= 0:
            # membership-covered / free lesson -> gross R0, nothing to split.
            skipped += 1
            continue
        coach = ln["product_coach"] or ln["booking_coach"]
        product_id = ln["product_id"]
        pct = resolve_commission_pct(session, club_id=club_id, product_id=product_id,
                                     coach_user_id=coach, at=at)
        rule_id = _resolve_rule_id(session, club_id=club_id, product_id=product_id,
                                   coach_user_id=coach, at=at)
        owner_cut = _split_minor(gross, pct)
        coach_net = gross - owner_cut

        wrote = _write_split_pair(
            session, club_id=club_id, payment_id=payment_id,
            order_line_id=ln["order_line_id"], booking_id=ln["booking_id"],
            coach_user_id=coach, product_id=product_id, rule_id=rule_id,
            basis=basis, gross_minor=gross, pct=pct,
            owner_minor=owner_cut, coach_minor=coach_net, currency=currency, at=at)
        splits += wrote["splits"]
        earnings += wrote["earnings"]

    # LOCKSTEP: a lesson whose commission just accrued on a real charge (desk OR online) must also
    # drop off the coach's OWED tab — otherwise it reads as BOTH paid and owed and could be
    # re-collected, stacking a second (arrears) split on top of this charge split. Status-only; the
    # commission accrued above is the single accrual. Runs for the desk-pay path (via the
    # charge_succeeded fan-out) AND the 'pay all' settlement path (per child), so no path settles an
    # order without clearing its arrears.
    session.execute(
        text("UPDATE billing.coach_arrears SET status = 'collected', collected_at = now(), "
             "updated_at = now() WHERE club_id = :club AND status = 'owed' AND order_line_id IN "
             "(SELECT id FROM billing.order_line WHERE order_id = :oid)"),
        {"club": club_id, "oid": str(order_id)},
    )
    return {"ok": True, "splits": splits, "earnings": earnings, "skipped": skipped}


def _write_split_pair(session, *, club_id, payment_id, order_line_id, booking_id,
                      coach_user_id, product_id, rule_id, basis, gross_minor, pct,
                      owner_minor, coach_minor, currency, at=None) -> Dict[str, int]:
    """Insert the owner + coach commission_split rows (idempotent) and, on a fresh coach
    split, post the coach_ledger commission_earning (idempotent on ref_id=split.id).
    `payment_id` may be None for arrears collection (the unique index uses NULLS NOT
    DISTINCT so it still dedupes on order_line_id+party_type)."""
    splits = 0
    earnings = 0
    pairs = (("owner", owner_minor), ("coach", coach_minor))
    coach_split_id = None
    for party, amount in pairs:
        row = session.execute(
            text("""
                INSERT INTO billing.commission_split
                    (club_id, payment_id, order_line_id, booking_id, coach_user_id, product_id,
                     rule_id, party_type, basis, gross_minor, commission_pct, amount_minor,
                     currency, occurred_at)
                VALUES (:club, :pay, :line, :booking, :coach, :product, :rule, :party, :basis,
                        :gross, :pct, :amount, :cur, COALESCE(:at, now()))
                ON CONFLICT (payment_id, order_line_id, party_type) DO NOTHING
                RETURNING id
            """),
            {"club": club_id, "pay": str(payment_id) if payment_id else None,
             "line": str(order_line_id) if order_line_id else None,
             "booking": str(booking_id) if booking_id else None,
             "coach": str(coach_user_id) if coach_user_id else None,
             "product": str(product_id) if product_id else None,
             "rule": rule_id, "party": party, "basis": basis,
             "gross": int(gross_minor), "pct": str(pct), "amount": int(amount),
             "cur": currency, "at": at},
        ).mappings().first()
        if row:
            splits += 1
            if party == "coach":
                coach_split_id = row["id"]

    # Post the coach's earning to the signed ledger (only when a fresh coach split was written
    # AND the coach is known). Idempotent on ref_id = the split id.
    if coach_split_id is not None and coach_user_id is not None:
        led = session.execute(
            text("""
                INSERT INTO billing.coach_ledger
                    (club_id, coach_user_id, entry_type, amount_minor, currency,
                     ref_type, ref_id, note, occurred_at)
                VALUES (:club, :coach, 'commission_earning', :amount, :cur,
                        'split', :ref, :note, COALESCE(:at, now()))
                ON CONFLICT (club_id, coach_user_id, ref_id)
                    WHERE entry_type = 'commission_earning'
                DO NOTHING
                RETURNING id
            """),
            {"club": club_id, "coach": str(coach_user_id),
             "amount": int(coach_minor), "cur": currency,
             "ref": str(coach_split_id), "note": f"{basis} earning", "at": at},
        ).mappings().first()
        if led:
            earnings += 1
    return {"splits": splits, "earnings": earnings}


def record_refund_clawback(session, *, club_id, order_id, refund_payment_id,
                           refund_minor, at=None) -> Dict[str, Any]:
    """On a REFUND of an online-paid order, reverse the coach's commission PROPORTIONALLY.

    The club already absorbs the customer refund (the negative billing.payment row); WITHOUT this
    the coach keeps 100% of their commission on a lesson that was refunded and the club eats the
    whole loss. Policy (docs/specs/owner-self-service-spec §10, confirmed): proportional clawback —
    a full refund reverses the full commission, a half refund reverses half.

    For each coach+owner commission_split of the refunded order it writes a NEGATIVE
    'refund_clawback' split (proportion = refund / original charge) and, for the coach leg, a
    negative coach_ledger adjustment so the coach's balance drops. IDEMPOTENT on the refund payment
    (unique (payment_id, order_line_id, party_type) keyed to the REFUND payment id, so a replayed
    refund webhook writes nothing new; the ledger entry is gated on a fresh split). Returns
    {clawbacks, ledger, proportion} — {clawbacks:0, reason} when there's nothing to reverse
    (court/membership refund, not online-paid, or no coach commission)."""
    charge_total = int(session.execute(
        text("SELECT amount_minor FROM billing.payment WHERE order_id = :o "
             "AND direction = 'charge' AND status = 'succeeded' ORDER BY created_at LIMIT 1"),
        {"o": str(order_id)},
    ).scalar() or 0)
    if charge_total <= 0:
        return {"clawbacks": 0, "reason": "no_online_charge"}
    refund_minor = int(refund_minor or 0)
    if refund_minor <= 0:
        refund_minor = charge_total          # Yoco sends NO amount for a FULL refund → treat as full
    p = Decimal(refund_minor) / Decimal(charge_total)
    if p <= 0:
        return {"clawbacks": 0, "reason": "zero_refund"}
    if p > 1:
        p = Decimal(1)

    splits = session.execute(
        text("""
            SELECT cs.id, cs.order_line_id, cs.booking_id, cs.coach_user_id, cs.product_id,
                   cs.rule_id, cs.party_type, cs.gross_minor, cs.commission_pct,
                   cs.amount_minor, cs.currency
            FROM billing.commission_split cs
            JOIN billing.order_line ol ON ol.id = cs.order_line_id
            WHERE cs.club_id = :club
              AND cs.basis IN ('lesson_commission','class_commission','arrears_commission')
              AND (ol.order_id = :o
                   OR ol.order_id IN (SELECT id FROM billing."order" WHERE settled_by_order_id = :o))
            ORDER BY cs.order_line_id, cs.party_type
        """),
        {"o": str(order_id), "club": club_id},
    ).mappings().all()
    if not splits:
        return {"clawbacks": 0, "reason": "no_commission"}

    def _neg(v):
        return -int((Decimal(int(v or 0)) * p).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    clawbacks = 0
    ledger = 0
    for s in splits:
        neg_amount = _neg(s["amount_minor"])
        neg_gross = _neg(s["gross_minor"])
        if neg_amount == 0 and neg_gross == 0:
            continue
        row = session.execute(
            text("""
                INSERT INTO billing.commission_split
                    (club_id, payment_id, order_line_id, booking_id, coach_user_id, product_id,
                     rule_id, party_type, basis, gross_minor, commission_pct, amount_minor,
                     currency, occurred_at)
                VALUES (:club, :pay, :line, :booking, :coach, :product, :rule, :party,
                        'refund_clawback', :gross, :pct, :amount, :cur, COALESCE(:at, now()))
                ON CONFLICT (payment_id, order_line_id, party_type) DO NOTHING
                RETURNING id
            """),
            {"club": club_id, "pay": str(refund_payment_id) if refund_payment_id else None,
             "line": str(s["order_line_id"]) if s["order_line_id"] else None,
             "booking": str(s["booking_id"]) if s["booking_id"] else None,
             "coach": str(s["coach_user_id"]) if s["coach_user_id"] else None,
             "product": str(s["product_id"]) if s["product_id"] else None,
             "rule": s["rule_id"], "party": s["party_type"],
             "gross": neg_gross, "pct": str(s["commission_pct"] or 0),
             "amount": neg_amount, "cur": s["currency"] or "ZAR", "at": at},
        ).mappings().first()
        if not row:
            continue                        # already clawed back for this refund payment (idempotent)
        clawbacks += 1
        # Reverse the coach's earning on the signed ledger. Plain INSERT — the fresh-split gate above
        # makes this idempotent (a replay writes no split, so never reaches here).
        if s["party_type"] == "coach" and s["coach_user_id"] and neg_amount != 0:
            session.execute(
                text("""
                    INSERT INTO billing.coach_ledger
                        (club_id, coach_user_id, entry_type, amount_minor, currency,
                         ref_type, ref_id, note, occurred_at)
                    VALUES (:club, :coach, 'adjustment', :amount, :cur,
                            'split', :ref, 'refund clawback', COALESCE(:at, now()))
                """),
                {"club": club_id, "coach": str(s["coach_user_id"]),
                 "amount": neg_amount, "cur": s["currency"] or "ZAR",
                 "ref": str(row["id"]), "at": at},
            )
            ledger += 1
    return {"clawbacks": clawbacks, "ledger": ledger, "proportion": float(p)}


# ---------------------------------------------------------------------------
# arrears — off-platform lessons posted to the coach's per-client tab
# ---------------------------------------------------------------------------

def accrue_arrears_for_club(session, *, club_id) -> int:
    """Lazily populate billing.coach_arrears from confirmed lesson bookings that have NOT been
    paid online (no succeeded charge payment on their order) and are not membership-covered.
    Each unpaid lesson posts to the coach's per-client tab (status='owed'). Idempotent on the
    source booking (ux_coach_arrears_booking) — re-running adds nothing for already-tracked
    bookings. Returns the count of NEW arrears rows. Guarded so a missing diary.* degrades to 0.

    The 'unpaid' test: the booking's order has settlement_mode in (at_court, monthly_account)
    OR has no succeeded charge payment, and the order line carries a positive ex-VAT gross.
    Online-paid lessons settle via record_split_for_order instead and are excluded here.
    """
    try:
        res = session.execute(
            text("""
                INSERT INTO billing.coach_arrears
                    (club_id, coach_user_id, client_user_id, booking_id, order_line_id,
                     product_id, gross_minor, currency, status)
                SELECT b.club_id,
                       COALESCE(pr.coach_user_id, b.coach_user_id) AS coach_user_id,
                       b.booked_by_user_id AS client_user_id,
                       b.id AS booking_id,
                       ol.id AS order_line_id,
                       pr.id AS product_id,
                       ol.amount_minor AS gross_minor,
                       o.currency_code AS currency,
                       'owed'
                FROM diary.booking b
                JOIN billing.order_line ol ON ol.booking_id = b.id AND ol.club_id = b.club_id
                JOIN billing."order" o     ON o.id = ol.order_id
                LEFT JOIN billing.price   p  ON p.id  = ol.price_id
                LEFT JOIN billing.product pr ON pr.id = p.product_id
                WHERE b.club_id = :club
                  AND b.booking_type = 'lesson'
                  AND b.status IN ('confirmed','completed')
                  AND ol.amount_minor > 0
                  AND o.settlement_mode <> 'membership_covered'
                  AND o.status NOT IN ('paid','void','written_off')   -- already settled/cleared (incl. via a 'pay all' settlement order whose payment sits on the parent)
                  AND COALESCE(pr.coach_user_id, b.coach_user_id) IS NOT NULL
                  AND NOT EXISTS (
                        SELECT 1 FROM billing.payment pay
                        WHERE pay.order_id = o.id AND pay.direction = 'charge'
                          AND pay.status = 'succeeded')
                ON CONFLICT (club_id, booking_id) WHERE booking_id IS NOT NULL
                DO NOTHING
            """),
            {"club": club_id},
        )
        return res.rowcount or 0
    except Exception:
        session.rollback()
        log.info("accrue_arrears_for_club skipped (diary.* unavailable) club=%s", club_id)
        return 0


def mark_arrears_collected(session, *, club_id, arrears_id, coach_user_id=None,
                           collected_by=None) -> Dict[str, Any]:
    """The coach (or admin) marks an arrears item collected (off-platform EFT received).
    Sets status='collected' then accrues its commission: writes an owner+coach split
    (basis='arrears_commission', payment_id NULL) + the coach earning — idempotent on the
    arrears' order_line. If `coach_user_id` is given the row must belong to that coach (the
    coach self-service guard). Returns {ok, status, splits} or {ok:False, error}.
    """
    row = session.execute(
        text("""
            SELECT id, coach_user_id, client_user_id, booking_id, order_line_id, product_id,
                   gross_minor, currency, status
            FROM billing.coach_arrears
            WHERE club_id = :club AND id = :id
        """),
        {"club": club_id, "id": str(arrears_id)},
    ).mappings().first()
    if row is None:
        return {"ok": False, "error": "NOT_FOUND"}
    if coach_user_id is not None and str(row["coach_user_id"]) != str(coach_user_id):
        return {"ok": False, "error": "FORBIDDEN"}
    if row["status"] == "collected":
        return {"ok": True, "status": "already_collected", "splits": 0}

    # GUARD (double-commission): if the linked order was ALREADY settled or cleared elsewhere — a
    # desk/online charge, a 'pay all' settlement, or a void/write-off — the commission has either
    # already accrued on that charge or the debt was forgiven. Do NOT accrue again; just reconcile
    # the arrears status to match. This makes a stray "mark collected" a no-op regardless of lockstep
    # state (the unique index can't dedupe an arrears split (payment NULL) vs a charge split).
    order_status = None
    if row["order_line_id"]:
        order_status = session.execute(
            text('SELECT o.status FROM billing."order" o '
                 "JOIN billing.order_line ol ON ol.order_id = o.id WHERE ol.id = :olid"),
            {"olid": str(row["order_line_id"])},
        ).scalar()
    if order_status in ("paid", "refunded"):
        session.execute(
            text("UPDATE billing.coach_arrears SET status='collected', collected_at=now(), "
                 "collected_by=:by, updated_at=now() WHERE club_id=:club AND id=:id"),
            {"club": club_id, "id": str(arrears_id), "by": str(collected_by) if collected_by else None})
        return {"ok": True, "status": "reconciled", "splits": 0}
    if order_status in ("void", "written_off"):
        session.execute(
            text("UPDATE billing.coach_arrears SET status='written_off', updated_at=now() "
                 "WHERE club_id=:club AND id=:id"), {"club": club_id, "id": str(arrears_id)})
        return {"ok": True, "status": "reconciled", "splits": 0}

    session.execute(
        text("""
            UPDATE billing.coach_arrears
            SET status = 'collected', collected_at = now(), collected_by = :by, updated_at = now()
            WHERE club_id = :club AND id = :id
        """),
        {"club": club_id, "id": str(arrears_id),
         "by": str(collected_by) if collected_by else None},
    )
    # Keep the client's unified statement in lockstep: collecting off-platform clears the client's
    # owed ORDER too (status-only — the money came in off-gateway; commission accrues below). Without
    # this the client would still see the lesson as owed after the coach was paid.
    if row["order_line_id"]:
        session.execute(
            text('UPDATE billing."order" SET status = \'paid\', updated_at = now() '
                 "WHERE club_id = :club AND status IN ('open','awaiting_payment') "
                 "AND id = (SELECT order_id FROM billing.order_line WHERE id = :olid)"),
            {"club": club_id, "olid": str(row["order_line_id"])},
        )

    gross = int(row["gross_minor"] or 0)
    coach = row["coach_user_id"]
    product_id = row["product_id"]
    pct = resolve_commission_pct(session, club_id=club_id, product_id=product_id,
                                 coach_user_id=coach)
    rule_id = _resolve_rule_id(session, club_id=club_id, product_id=product_id,
                               coach_user_id=coach)
    owner_cut = _split_minor(gross, pct)
    coach_net = gross - owner_cut
    wrote = _write_split_pair(
        session, club_id=club_id, payment_id=None,
        order_line_id=row["order_line_id"], booking_id=row["booking_id"],
        coach_user_id=coach, product_id=product_id, rule_id=rule_id,
        basis="arrears_commission", gross_minor=gross, pct=pct,
        owner_minor=owner_cut, coach_minor=coach_net, currency=row["currency"] or "ZAR")
    return {"ok": True, "status": "collected", "splits": wrote["splits"],
            "owner_cut_minor": owner_cut, "coach_net_minor": coach_net,
            "commission_pct": str(pct)}


def client_service_breakdown(session, *, club_id, coach_user_id, client_user_id, month=None) -> Dict[str, Any]:
    """One client's coaching grouped BY SERVICE (product + duration): e.g. 'Private lesson · 45 min ·
    3 · R750', with the individual sessions (booking_id → the event story). Composes diary.booking +
    order_line + price + product + coach_arrears, scoped to this coach + client. `month` (YYYY-MM)
    filters by the lesson date; omit for all-time.

    Each session carries its REAL money state (not just the order status): a written-off or DISCOUNTED
    lesson shows as such — derived from coach_arrears (write-off status; a gross_minor that differs from
    what was billed = discounted). Returns {total_minor (effective), billed_minor (gross, pre-discount/
    write-off), services:[{key,label,count,total_minor,billed_minor,items:[{booking_id,starts_at,
    billed_minor,amount_minor(effective),status}]}]}. status ∈ paid|owed|written_off|discounted|
    covered|pending|refunded. Guarded → empty."""
    try:
        accrue_arrears_for_club(session, club_id=club_id)   # so an owed lesson carries an arrears row
    except Exception:
        pass
    where_month = "AND to_char(b.starts_at,'YYYY-MM') = :ym" if month else ""
    params: Dict[str, Any] = {"c": club_id, "coach": str(coach_user_id), "client": str(client_user_id)}
    if month:
        params["ym"] = month
    try:
        rows = session.execute(
            text("""
                SELECT b.id AS booking_id, b.starts_at, b.ends_at, b.status AS bstatus,
                       ol.amount_minor, ol.original_amount_minor, o.status AS ostatus, o.settlement_mode,
                       pr.duration_minutes AS price_dur, prod.id AS product_id, prod.name AS product_name,
                       ca.status AS arr_status, ca.gross_minor AS arr_gross
                FROM diary.booking b
                LEFT JOIN billing.order_line ol ON ol.booking_id = b.id
                LEFT JOIN billing."order" o ON o.id = ol.order_id
                LEFT JOIN billing.price pr ON pr.id = ol.price_id
                LEFT JOIN billing.product prod ON prod.id = pr.product_id
                LEFT JOIN billing.coach_arrears ca ON ca.booking_id = b.id AND ca.club_id = b.club_id
                WHERE b.club_id = :c AND b.coach_user_id = :coach AND b.booked_by_user_id = :client
                  AND b.booking_type = 'lesson'
                  AND b.status IN ('confirmed','held','completed','no_show')
                  """ + where_month + """
                ORDER BY b.starts_at DESC
            """),
            params,
        ).mappings().all()
    except Exception:
        log.debug("client_service_breakdown suppressed", exc_info=False)
        rows = []

    _ORD = {"paid": "paid", "open": "owed", "awaiting_payment": "pending", "refunded": "refunded",
            "void": "cancelled", "written_off": "written_off"}
    groups: Dict[str, Any] = {}
    total = 0
    billed_total = 0
    for r in rows:
        dur = int(r["price_dur"] or 0)
        if not dur and r["starts_at"] and r["ends_at"]:
            dur = int((r["ends_at"] - r["starts_at"]).total_seconds() // 60)
        name = r["product_name"] or "Lesson"
        key = (str(r["product_id"]) if r["product_id"] else "x") + "-" + str(dur)
        label = name + ((" · " + str(dur) + " min") if dur else "")
        # "billed" = the ORIGINAL charge (order_line.original_amount_minor is set on a discount; else the
        # current amount_minor IS the original). "eff"/arr_gross is the current (discounted) figure owed.
        billed = int(r["original_amount_minor"]) if r["original_amount_minor"] is not None else int(r["amount_minor"] or 0)
        covered = r["settlement_mode"] in ("membership_covered", "free", "token")
        arr_status = r["arr_status"]
        arr_gross = int(r["arr_gross"]) if r["arr_gross"] is not None else None
        # Derive the REAL per-session state + its effective (current) amount.
        if covered and billed == 0:
            status, eff = "covered", 0
        elif arr_status == "written_off":
            status, eff = "written_off", 0
        elif arr_status == "collected":
            status, eff = "paid", (arr_gross if arr_gross is not None else billed)
        elif arr_status == "owed":
            eff = arr_gross if arr_gross is not None else billed
            status = "discounted" if (arr_gross is not None and arr_gross != billed) else "owed"
        else:
            status = _ORD.get(r["ostatus"], r["ostatus"] or "—")
            eff = 0 if status in ("written_off", "cancelled") else billed
        g = groups.setdefault(key, {"key": key, "label": label, "count": 0,
                                    "total_minor": 0, "billed_minor": 0, "items": []})
        g["count"] += 1
        g["total_minor"] += eff
        g["billed_minor"] += billed
        total += eff
        billed_total += billed
        g["items"].append({"booking_id": str(r["booking_id"]),
                           "starts_at": r["starts_at"].isoformat() if r["starts_at"] else None,
                           "billed_minor": billed, "amount_minor": eff, "status": status})
    services = sorted(groups.values(), key=lambda x: -x["billed_minor"])
    return {"total_minor": total, "billed_minor": billed_total, "services": services}


def client_invoice_data(session, *, club_id, coach_user_id, client_user_id, month=None) -> Dict[str, Any]:
    """Build ONE client's coaching invoice for a month: the coach's lessons/classes with this client,
    each line paid / owed / written-off, plus totals. Coach-scoped (only this coach's coaching — never
    the client's court/membership spend). Drives both the printable invoice and the issue-invoice notify.
    """
    try:
        accrue_arrears_for_club(session, club_id=club_id)   # so a not-yet-tracked owed lesson shows
    except Exception:
        pass
    ym = month or session.execute(text("SELECT to_char(now(),'YYYY-MM')")).scalar()
    currency = session.execute(
        text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": club_id}).scalar() or "ZAR"
    club_name = session.execute(
        text("SELECT name FROM club.club WHERE id = :c"), {"c": club_id}).scalar() or "Your club"

    def _name(uid):
        r = session.execute(
            text('SELECT first_name, surname, email FROM iam."user" WHERE id = :id'),
            {"id": str(uid)}).mappings().first()
        if not r:
            return (None, None)
        full = " ".join(x for x in [r["first_name"], r["surname"]] if x).strip()
        return (full or r["email"] or "—", r["email"])

    coach_name, _ = _name(coach_user_id)
    client_name, client_email = _name(client_user_id)

    lines: List[Dict[str, Any]] = []
    # Paid this month (online or collected off-platform) — coach commission split rows.
    for r in session.execute(
        text("""
            SELECT cs.gross_minor, cs.occurred_at, b.starts_at, b.booking_type
            FROM billing.commission_split cs
            LEFT JOIN diary.booking b ON b.id = cs.booking_id
            WHERE cs.club_id = :c AND cs.coach_user_id = :coach AND cs.party_type = 'coach'
              AND cs.basis IN ('lesson_commission','class_commission','arrears_commission')
              AND b.booked_by_user_id = :cu
              AND to_char(cs.occurred_at,'YYYY-MM') = :ym
            ORDER BY COALESCE(b.starts_at, cs.occurred_at)
        """),
        {"c": club_id, "coach": str(coach_user_id), "cu": str(client_user_id), "ym": ym},
    ).mappings().all():
        lines.append({
            "at": (r["starts_at"] or r["occurred_at"]).isoformat() if (r["starts_at"] or r["occurred_at"]) else None,
            "description": ("Class" if r["booking_type"] == "class" else "Lesson"),
            "gross_minor": int(r["gross_minor"] or 0), "status": "paid",
        })
    # Owed + written-off (the running tab — not month-bound; a written-off line stays visible).
    for r in session.execute(
        text("""
            SELECT a.gross_minor, a.status, a.note, b.starts_at, a.created_at
            FROM billing.coach_arrears a
            LEFT JOIN diary.booking b ON b.id = a.booking_id
            WHERE a.club_id = :c AND a.coach_user_id = :coach AND a.client_user_id = :cu
              AND a.status IN ('owed','written_off')
            ORDER BY COALESCE(b.starts_at, a.created_at)
        """),
        {"c": club_id, "coach": str(coach_user_id), "cu": str(client_user_id)},
    ).mappings().all():
        lines.append({
            "at": (r["starts_at"] or r["created_at"]).isoformat() if (r["starts_at"] or r["created_at"]) else None,
            "description": "Lesson" + (" — written off" if r["status"] == "written_off" else ""),
            "gross_minor": int(r["gross_minor"] or 0), "status": r["status"], "note": r["note"] or None,
        })

    paid = sum(l["gross_minor"] for l in lines if l["status"] == "paid")
    owed = sum(l["gross_minor"] for l in lines if l["status"] == "owed")
    woff = sum(l["gross_minor"] for l in lines if l["status"] == "written_off")
    return {
        "month": ym, "currency": currency, "club_name": club_name,
        "coach_name": coach_name, "client_name": client_name, "client_email": client_email,
        "lines": lines,
        "totals": {"paid_minor": paid, "owed_minor": owed, "written_off_minor": woff},
    }


def issue_client_invoice(session, *, club_id, coach_user_id, client_user_id, month=None) -> Dict[str, Any]:
    """Month-end: send THIS client their coaching statement/invoice. Builds the invoice, and if the
    client still owes something, emits a `statement_ready` notification (in-app now, email once SES is
    keyed) with the owed amount + a pay link to their unified statement (which they settle online to
    zero). Returns {invoice, owed_minor, notified}. Notify is best-effort — never raises."""
    inv = client_invoice_data(session, club_id=club_id, coach_user_id=coach_user_id,
                              client_user_id=client_user_id, month=month)
    owed = int(inv["totals"]["owed_minor"] or 0)
    notified = False
    if owed > 0:
        try:
            from marketing_crm.tracking import emit
            emit("statement_ready", {
                "club_id": str(club_id), "user_id": str(client_user_id),
                "amount_minor": owed, "currency": inv["currency"]})
            notified = True
        except Exception:
            log.info("issue_client_invoice: notify skipped (tracking unavailable) client=%s", client_user_id)
    return {"invoice": inv, "owed_minor": owed, "notified": notified}


def adjust_arrears(session, *, club_id, arrears_id, coach_user_id=None,
                   gross_minor=None, status=None, actor_user_id=None, reason=None) -> Dict[str, Any]:
    """Edit an OWED arrears line before collection: DISCOUNT (set a new gross_minor) and/or
    WRITE IT OFF (status='written_off' — the coach waives the lesson; no commission accrues and it
    leaves the outstanding tab). A coach may only edit their OWN arrears (self-service guard); a
    collected line is immutable here. Commission later accrues on the (possibly discounted) amount
    when the line is marked collected, so a discount correctly reduces both the bill and the cut.
    """
    row = session.execute(
        text("SELECT id, coach_user_id, status FROM billing.coach_arrears "
             "WHERE club_id = :c AND id = :id"),
        {"c": club_id, "id": str(arrears_id)},
    ).mappings().first()
    if row is None:
        return {"ok": False, "error": "NOT_FOUND"}
    if coach_user_id is not None and str(row["coach_user_id"]) != str(coach_user_id):
        return {"ok": False, "error": "FORBIDDEN"}
    if row["status"] != "owed":
        return {"ok": False, "error": "NOT_EDITABLE", "status": row["status"]}

    sets = ["updated_at = now()"]
    params = {"c": club_id, "id": str(arrears_id)}
    if gross_minor is not None:
        try:
            g = int(gross_minor)
        except (TypeError, ValueError):
            return {"ok": False, "error": "BAD_AMOUNT"}
        if g < 0:
            return {"ok": False, "error": "BAD_AMOUNT"}
        sets.append("gross_minor = :g"); params["g"] = g
    if status is not None:
        if status != "written_off":
            return {"ok": False, "error": "BAD_STATUS"}
        sets.append("status = 'written_off'")
        sets.append("collected_by = :by"); params["by"] = str(actor_user_id) if actor_user_id else None
    # Persist the reason (discount OR write-off) so the audit trail shows WHY — visible on every
    # statement. A blank reason leaves any prior note intact.
    if reason is not None and str(reason).strip():
        sets.append("note = :note"); params["note"] = str(reason).strip()[:500]

    session.execute(
        text("UPDATE billing.coach_arrears SET " + ", ".join(sets) + " WHERE club_id = :c AND id = :id"),
        params)
    # DISCOUNT lockstep: the client's OWED order drops to the NEW amount too, so they owe the discounted
    # figure. Keep the ORIGINAL on the order_line (first discount only) so the by-service view still
    # shows "was → now". Only an OPEN/awaiting order is re-priced (a paid lesson needs a refund, not this).
    if gross_minor is not None and status != "written_off":
        session.execute(
            text("UPDATE billing.order_line SET "
                 "  original_amount_minor = COALESCE(original_amount_minor, amount_minor), amount_minor = :g "
                 "WHERE id = (SELECT order_line_id FROM billing.coach_arrears WHERE id = :aid) "
                 "  AND order_id IN (SELECT id FROM billing.\"order\" WHERE status IN ('open','awaiting_payment'))"),
            {"g": g, "aid": str(arrears_id)})
        session.execute(
            text('UPDATE billing."order" o SET amount_minor = '
                 "(SELECT COALESCE(SUM(amount_minor),0) FROM billing.order_line WHERE order_id = o.id), updated_at = now() "
                 "WHERE o.status IN ('open','awaiting_payment') AND o.id IN "
                 "(SELECT ol.order_id FROM billing.order_line ol JOIN billing.coach_arrears a ON a.order_line_id = ol.id WHERE a.id = :aid)"),
            {"aid": str(arrears_id)})
    # LOCKSTEP: writing off the coaching ALSO forgives the CLIENT's order for that lesson — one lesson
    # is one debt viewed two ways (mirror void_order, which writes off the arrears when the order is
    # written off). Otherwise the client is still billed for a lesson the coach waived. void_order
    # no-ops on a PAID order (a paid lesson stays paid — you'd refund, not write off).
    if status == "written_off":
        oid = session.execute(
            text("SELECT ol.order_id FROM billing.coach_arrears a "
                 "JOIN billing.order_line ol ON ol.id = a.order_line_id WHERE a.id = :id"),
            {"id": str(arrears_id)}).scalar()
        if oid:
            try:
                from billing.statement import void_order
                void_order(session, club_id=club_id, order_id=oid, write_off=True,
                           reason=(reason or "coaching written off"))
            except Exception:
                log.debug("write-off order-void skipped", exc_info=False)
    out = session.execute(
        text("SELECT id, status, gross_minor FROM billing.coach_arrears WHERE club_id = :c AND id = :id"),
        {"c": club_id, "id": str(arrears_id)},
    ).mappings().first()
    return {"ok": True, "arrears": {"id": str(out["id"]), "status": out["status"],
                                    "gross_minor": int(out["gross_minor"] or 0)}}


def client_statement(session, *, club_id, user_id, month=None) -> Dict[str, Any]:
    """The CLIENT's coaching statement — the mirror of coach_statement, so a client and coach see
    the SAME end-of-month picture from opposite sides. Per COACH: lessons paid this month + what
    the client still OWES (arrears on the tab). Runs the lazy arrears accrual first so every
    unpaid lesson shows. Returns a dict the client statement view renders."""
    accrue_arrears_for_club(session, club_id=club_id)
    ym = month or session.execute(text("SELECT to_char(now(),'YYYY-MM')")).scalar()
    try:
        currency = session.execute(
            text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": club_id}).scalar() or "ZAR"
    except Exception:
        currency = "ZAR"

    paid = session.execute(
        text("""
            SELECT cs.coach_user_id,
                   count(*) FILTER (WHERE cs.basis <> 'refund_clawback') AS lesson_count,
                   COALESCE(SUM(cs.gross_minor),0) AS paid_minor
            FROM billing.commission_split cs
            JOIN diary.booking b ON b.id = cs.booking_id
            WHERE cs.club_id = :club AND cs.party_type = 'coach'
              -- refund_clawback (negative gross) nets a refunded lesson out of the client's paid-this-
              -- month, mirroring coach_statement so the two sides agree; count excludes it (not a lesson).
              AND cs.basis IN ('lesson_commission','class_commission','arrears_commission','refund_clawback')
              AND b.booked_by_user_id = :u
              AND to_char(cs.occurred_at,'YYYY-MM') = :ym
            GROUP BY 1
        """),
        {"club": club_id, "u": str(user_id), "ym": ym},
    ).mappings().all()

    owed = session.execute(
        text("""
            SELECT coach_user_id, count(*) AS lesson_count,
                   COALESCE(SUM(gross_minor),0) AS owed_minor
            FROM billing.coach_arrears
            WHERE club_id = :club AND client_user_id = :u AND status = 'owed'
            GROUP BY 1
        """),
        {"club": club_id, "u": str(user_id)},
    ).mappings().all()

    items = session.execute(
        text("""
            SELECT a.id, a.coach_user_id, a.gross_minor, a.status, a.note,
                   a.created_at, b.starts_at
            FROM billing.coach_arrears a
            LEFT JOIN diary.booking b ON b.id = a.booking_id
            WHERE a.club_id = :club AND a.client_user_id = :u
              AND a.status IN ('owed','written_off')
            ORDER BY (a.status = 'owed') DESC, a.created_at DESC
        """),
        {"club": club_id, "u": str(user_id)},
    ).mappings().all()

    coach_ids = set()
    for r in list(paid) + list(owed) + list(items):
        if r["coach_user_id"]:
            coach_ids.add(str(r["coach_user_id"]))
    names: Dict[str, str] = {}
    if coach_ids:
        for n in session.execute(
            text('SELECT id, first_name, surname, email FROM iam."user" WHERE id = ANY(:ids)'),
            {"ids": list(coach_ids)},
        ).mappings().all():
            full = " ".join(x for x in [n["first_name"], n["surname"]] if x).strip()
            names[str(n["id"])] = full or n["email"] or "Coach"

    by_coach: Dict[str, Dict[str, Any]] = {}

    def _slot(cid):
        key = str(cid) if cid else "_unknown"
        if key not in by_coach:
            by_coach[key] = {"coach_user_id": (str(cid) if cid else None),
                             "coach_name": names.get(str(cid), "Coach"),
                             "lessons": 0, "paid_minor": 0, "owed_minor": 0, "net_minor": 0}
        return by_coach[key]

    for r in paid:
        s = _slot(r["coach_user_id"]); s["lessons"] += int(r["lesson_count"] or 0)
        s["paid_minor"] += int(r["paid_minor"] or 0)
    for r in owed:
        s = _slot(r["coach_user_id"]); s["lessons"] += int(r["lesson_count"] or 0)
        s["owed_minor"] += int(r["owed_minor"] or 0)
    for s in by_coach.values():
        s["net_minor"] = s["paid_minor"] + s["owed_minor"]

    arrears_items = [{
        "id": str(r["id"]),
        "coach_user_id": (str(r["coach_user_id"]) if r["coach_user_id"] else None),
        "coach_name": names.get(str(r["coach_user_id"]), "Coach"),
        "gross_minor": int(r["gross_minor"] or 0),
        "status": r["status"],                                  # 'owed' | 'written_off'
        "note": r["note"] or None,                              # why it was written off
        "starts_at": (r["starts_at"].isoformat() if r["starts_at"] else None),
    } for r in items]

    written_off_minor = sum(int(r["gross_minor"] or 0) for r in items if r["status"] == "written_off")
    totals = {"paid_minor": sum(s["paid_minor"] for s in by_coach.values()),
              "owed_minor": sum(s["owed_minor"] for s in by_coach.values()),
              "written_off_minor": written_off_minor}   # forgiven — informational, NOT in net
    totals["net_minor"] = totals["paid_minor"] + totals["owed_minor"]
    return {"month": ym, "currency": currency,
            "coaches": sorted(by_coach.values(), key=lambda x: -x["net_minor"]),
            "arrears_items": arrears_items, "totals": totals}


# ---------------------------------------------------------------------------
# rent accrual (per coach per month) — idempotent on ref_id='YYYY-MM'
# ---------------------------------------------------------------------------

def accrue_rent_for_club(session, *, club_id, year_month=None) -> int:
    """Post a rent_charge coach_ledger entry (NEGATIVE — owed BY the coach to the club) per
    active agreement with rent_minor > 0 for the given month (default current). Idempotent on
    (coach, ref_id=year_month). Rent is additive — it accrues regardless of lessons taught
    (docs/specs/01 open-question default). Returns the count of NEW rent charges.
    """
    ym = year_month or session.execute(text("SELECT to_char(now(),'YYYY-MM')")).scalar()
    res = session.execute(
        text("""
            INSERT INTO billing.coach_ledger
                (club_id, coach_user_id, entry_type, amount_minor, currency,
                 ref_type, ref_id, note)
            SELECT ca.club_id, ca.coach_user_id, 'rent_charge',
                   -ca.rent_minor, ca.rent_currency, 'rent_period', :ym, 'monthly rent'
            FROM billing.coach_agreement ca
            WHERE ca.club_id = :club AND ca.status = 'active' AND ca.effective_to IS NULL
              AND ca.rent_minor > 0
            ON CONFLICT (club_id, coach_user_id, ref_id)
                WHERE entry_type = 'rent_charge'
            DO NOTHING
        """),
        {"club": club_id, "ym": ym},
    )
    return res.rowcount or 0


# ---------------------------------------------------------------------------
# balances + statement
# ---------------------------------------------------------------------------

def coach_balance(session, *, club_id, coach_user_id) -> int:
    """Signed lifetime ledger balance (minor units): positive = club owes coach,
    negative = coach owes club (net rent)."""
    return int(session.execute(
        text("SELECT COALESCE(SUM(amount_minor),0) FROM billing.coach_ledger "
             "WHERE club_id = :club AND coach_user_id = :coach"),
        {"club": club_id, "coach": str(coach_user_id)},
    ).scalar() or 0)


def coach_statement(session, *, club_id, coach_user_id, month=None) -> Dict[str, Any]:
    """The coach month-end statement (docs/specs/01 — the coach's most-wanted surface).
    For the given month (YYYY-MM, default current), per CLIENT:
        lessons (count + value), paid_via_yoco, owed (arrears), net_balance.
    Plus the coach's running ledger balance + period rent. First runs the lazy arrears
    accrual so every unpaid lesson is on the tab. Returns a dict the statement page renders.
    """
    accrue_arrears_for_club(session, club_id=club_id)
    ym = month or session.execute(text("SELECT to_char(now(),'YYYY-MM')")).scalar()

    # Paid-online lessons this month (from succeeded charge splits — coach party = net). A
    # refund_clawback is a NEGATIVE coach split: include it in coach_net_minor so a refunded lesson
    # reduces what the coach earned this month, but don't count it as a lesson/gross.
    paid = session.execute(
        text("""
            SELECT b.booked_by_user_id AS client_user_id,
                   count(*) FILTER (WHERE cs.basis <> 'refund_clawback') AS lesson_count,
                   COALESCE(SUM(cs.gross_minor) FILTER (WHERE cs.basis <> 'refund_clawback'),0)
                       AS gross_minor,
                   COALESCE(SUM(cs.amount_minor),0) AS coach_net_minor
            FROM billing.commission_split cs
            LEFT JOIN diary.booking b ON b.id = cs.booking_id
            WHERE cs.club_id = :club AND cs.coach_user_id = :coach
              AND cs.party_type = 'coach'
              -- arrears_commission = a lesson the coach collected OFF-platform (at court). It counts as
              -- earned just like an online-paid lesson_commission — omitting it hid those from the
              -- coach's OWN cockpit/statement while the owner + client still saw them (four surfaces
              -- disagreed). refund_clawback stays (nets a refunded lesson out of the coach's net).
              AND cs.basis IN ('lesson_commission','class_commission','arrears_commission','refund_clawback')
              AND to_char(cs.occurred_at,'YYYY-MM') = :ym
            GROUP BY 1
        """),
        {"club": club_id, "coach": str(coach_user_id), "ym": ym},
    ).mappings().all()

    # Owed (arrears) currently on the tab (status='owed') per client — not month-bound (a tab).
    owed = session.execute(
        text("""
            SELECT client_user_id,
                   count(*) AS lesson_count,
                   COALESCE(SUM(gross_minor),0) AS owed_minor
            FROM billing.coach_arrears
            WHERE club_id = :club AND coach_user_id = :coach AND status = 'owed'
            GROUP BY 1
        """),
        {"club": club_id, "coach": str(coach_user_id)},
    ).mappings().all()

    # Per-arrears line items (for the statement detail + the mark-collected buttons). Include
    # WRITTEN-OFF lines so a waived lesson stays visible (badged, no action) instead of vanishing —
    # transparency for coach, client and owner. Owed lines still drive the collect/discount/write-off
    # buttons; written-off lines are read-only. (Collected lines are covered by the paid rollup above.)
    items = session.execute(
        text("""
            SELECT a.id, a.client_user_id, a.gross_minor, a.currency, a.status,
                   a.note, a.created_at, a.updated_at, b.starts_at,
                   u.first_name, u.surname, u.email
            FROM billing.coach_arrears a
            LEFT JOIN diary.booking b ON b.id = a.booking_id
            LEFT JOIN iam."user" u    ON u.id = a.client_user_id
            WHERE a.club_id = :club AND a.coach_user_id = :coach
              AND a.status IN ('owed','written_off')
            ORDER BY (a.status = 'owed') DESC, a.created_at DESC
        """),
        {"club": club_id, "coach": str(coach_user_id)},
    ).mappings().all()

    # Resolve client display names in one pass.
    client_ids = set()
    for r in paid:
        if r["client_user_id"]:
            client_ids.add(str(r["client_user_id"]))
    for r in owed:
        if r["client_user_id"]:
            client_ids.add(str(r["client_user_id"]))
    names = {}
    if client_ids:
        nrows = session.execute(
            text('SELECT id, first_name, surname, email FROM iam."user" WHERE id = ANY(:ids)'),
            {"ids": list(client_ids)},
        ).mappings().all()
        for n in nrows:
            full = " ".join(x for x in [n["first_name"], n["surname"]] if x).strip()
            names[str(n["id"])] = full or n["email"] or "Client"

    # Merge paid + owed into one per-client row.
    by_client: Dict[str, Dict[str, Any]] = {}

    def _slot(cid):
        key = str(cid) if cid else "_unknown"
        if key not in by_client:
            by_client[key] = {
                "client_user_id": (str(cid) if cid else None),
                "client_name": names.get(str(cid), "Walk-in / unknown") if cid else "Walk-in / unknown",
                "lessons": 0, "paid_minor": 0, "owed_minor": 0, "net_minor": 0}
        return by_client[key]

    for r in paid:
        s = _slot(r["client_user_id"])
        s["lessons"] += int(r["lesson_count"] or 0)
        s["paid_minor"] += int(r["coach_net_minor"] or 0)
    for r in owed:
        s = _slot(r["client_user_id"])
        s["lessons"] += int(r["lesson_count"] or 0)
        s["owed_minor"] += int(r["owed_minor"] or 0)
    for s in by_client.values():
        s["net_minor"] = s["paid_minor"] + s["owed_minor"]

    arrears_items = []
    written_off_minor = 0
    for it in items:
        full = " ".join(x for x in [it["first_name"], it["surname"]] if x).strip()
        if it["status"] == "written_off":
            written_off_minor += int(it["gross_minor"] or 0)
        arrears_items.append({
            "id": str(it["id"]),
            "client_user_id": str(it["client_user_id"]) if it["client_user_id"] else None,
            "client_name": full or it["email"] or "Client",
            "gross_minor": int(it["gross_minor"] or 0),
            "currency": it["currency"] or "ZAR",
            "status": it["status"],                                  # 'owed' | 'written_off'
            "note": it["note"] or None,                              # why it was written off / discounted
            "starts_at": it["starts_at"].isoformat() if it["starts_at"] else None,
        })

    currency = session.execute(
        text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": club_id},
    ).scalar() or "ZAR"

    period_rent = int(session.execute(
        text("SELECT COALESCE(-SUM(amount_minor),0) FROM billing.coach_ledger "
             "WHERE club_id = :club AND coach_user_id = :coach "
             "AND entry_type = 'rent_charge' AND ref_id = :ym"),
        {"club": club_id, "coach": str(coach_user_id), "ym": ym},
    ).scalar() or 0)

    clients = sorted(by_client.values(), key=lambda r: -r["net_minor"])
    return {
        "month": ym,
        "currency": currency,
        "clients": clients,
        "arrears_items": arrears_items,
        "totals": {
            "paid_minor": sum(c["paid_minor"] for c in clients),
            "owed_minor": sum(c["owed_minor"] for c in clients),
            "net_minor": sum(c["net_minor"] for c in clients),
            "written_off_minor": written_off_minor,     # forgiven — informational, NOT in net
            "rent_minor": period_rent,
            "balance_minor": coach_balance(session, club_id=club_id, coach_user_id=coach_user_id),
        },
    }

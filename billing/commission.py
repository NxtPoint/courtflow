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

    session.execute(
        text("""
            UPDATE billing.coach_arrears
            SET status = 'collected', collected_at = now(), collected_by = :by, updated_at = now()
            WHERE club_id = :club AND id = :id
        """),
        {"club": club_id, "id": str(arrears_id),
         "by": str(collected_by) if collected_by else None},
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


def adjust_arrears(session, *, club_id, arrears_id, coach_user_id=None,
                   gross_minor=None, status=None, actor_user_id=None) -> Dict[str, Any]:
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

    session.execute(
        text("UPDATE billing.coach_arrears SET " + ", ".join(sets) + " WHERE club_id = :c AND id = :id"),
        params)
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
            SELECT cs.coach_user_id, count(*) AS lesson_count,
                   COALESCE(SUM(cs.gross_minor),0) AS paid_minor
            FROM billing.commission_split cs
            JOIN diary.booking b ON b.id = cs.booking_id
            WHERE cs.club_id = :club AND cs.party_type = 'coach'
              AND cs.basis IN ('lesson_commission','class_commission','arrears_commission')
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
            SELECT a.id, a.coach_user_id, a.gross_minor, a.status, a.created_at, b.starts_at
            FROM billing.coach_arrears a
            LEFT JOIN diary.booking b ON b.id = a.booking_id
            WHERE a.club_id = :club AND a.client_user_id = :u AND a.status = 'owed'
            ORDER BY a.created_at DESC
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
        "starts_at": (r["starts_at"].isoformat() if r["starts_at"] else None),
    } for r in items]

    totals = {"paid_minor": sum(s["paid_minor"] for s in by_coach.values()),
              "owed_minor": sum(s["owed_minor"] for s in by_coach.values())}
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

    # Paid-online lessons this month (from succeeded charge splits — coach party = net).
    paid = session.execute(
        text("""
            SELECT b.booked_by_user_id AS client_user_id,
                   count(*) AS lesson_count,
                   COALESCE(SUM(cs.gross_minor),0) AS gross_minor,
                   COALESCE(SUM(cs.amount_minor),0) AS coach_net_minor
            FROM billing.commission_split cs
            LEFT JOIN diary.booking b ON b.id = cs.booking_id
            WHERE cs.club_id = :club AND cs.coach_user_id = :coach
              AND cs.party_type = 'coach' AND cs.basis IN ('lesson_commission','class_commission')
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

    # Per-arrears line items (for the statement detail + the mark-collected buttons).
    items = session.execute(
        text("""
            SELECT a.id, a.client_user_id, a.gross_minor, a.currency, a.status,
                   a.created_at, b.starts_at,
                   u.first_name, u.surname, u.email
            FROM billing.coach_arrears a
            LEFT JOIN diary.booking b ON b.id = a.booking_id
            LEFT JOIN iam."user" u    ON u.id = a.client_user_id
            WHERE a.club_id = :club AND a.coach_user_id = :coach AND a.status = 'owed'
            ORDER BY a.created_at DESC
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
    for it in items:
        full = " ".join(x for x in [it["first_name"], it["surname"]] if x).strip()
        arrears_items.append({
            "id": str(it["id"]),
            "client_user_id": str(it["client_user_id"]) if it["client_user_id"] else None,
            "client_name": full or it["email"] or "Client",
            "gross_minor": int(it["gross_minor"] or 0),
            "currency": it["currency"] or "ZAR",
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
            "rent_minor": period_rent,
            "balance_minor": coach_balance(session, club_id=club_id, coach_user_id=coach_user_id),
        },
    }

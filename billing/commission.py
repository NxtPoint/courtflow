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

# ---------------------------------------------------------------------------
# WHO HOLDS THE CASH — the ONE rule, used by the ledger AND every statement
# ---------------------------------------------------------------------------

# The club can only actually RECEIVE money two ways: a Yoco charge, or an EFT into its account.
# Everything else recorded against a coaching order is cash that never reached the club — the coach
# took it from the client directly. The club has no facility for taking money on a coach's behalf
# (owner rule, 2026-07-29; docs/specs/01 §D6).
#
# This matters far beyond a label: it decides the DIRECTION of the coach_ledger entry. Club-held →
# `+coach_net` (the club owes the coach his share). Coach-held → `−owner_cut` (the coach is holding
# the club's commission). Getting it backwards is wrong by the whole gross, which is exactly what
# happened to off-platform collections before 2026-07-28.
CLUB_BANKED_PROVIDERS = ("yoco", "eft")


def cash_custody_for(provider) -> str:
    """'club' | 'coach' — who is holding the money this payment represents.

    Provider-driven because that is the only thing that is FACTUALLY known: a Yoco charge and an EFT
    demonstrably landed in the club's account; cash or a card taken at the court did not land
    anywhere the club can see. `recorded_by_user_id` cannot help here — `POST /api/billing/desk-payment`
    is `club_admin`-only, so every desk payment is admin-recorded whoever actually took the note.

    A payment with NO provider at all (`mark_arrears_collected` writes no `billing.payment` row) is
    coach-held by definition, and reaches the same answer through the default."""
    return "club" if (provider or "").strip().lower() in CLUB_BANKED_PROVIDERS else "coach"


def _provider_of_payment(session, payment_id):
    """The provider on a billing.payment row, or None. Guarded → None (which classifies coach-held,
    the conservative direction: it books the club's commission as still owed rather than assuming
    the club is holding the coach's money)."""
    if not payment_id:
        return None
    try:
        return session.execute(
            text("SELECT provider FROM billing.payment WHERE id = :p"), {"p": str(payment_id)},
        ).scalar()
    except Exception:
        return None


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
    held_by = cash_custody_for(_provider_of_payment(session, payment_id))
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
        # WHO EARNED IT: the line's own product, else the booking's denormalised coach, else — for a
        # PACK SALE — the coach on the wallet this order granted. That third fallback matters and was
        # missing: a pack's order line is hung on a lesson/class PRICE so the commission attributes to
        # the selling coach, but if that service is a SHARED (coach-less) one the product carries no
        # coach and a pack has no booking either, so the split was written with `coach_user_id = NULL`
        # — commission accrued to NOBODY, and the coach's statement could not see the sale at all.
        # `_earnings_cte` has always resolved a pack via the wallet (which is why the Money tab showed
        # the revenue against the coach while his own statement did not); these two now agree.
        coach = ln["product_coach"] or ln["booking_coach"] or _wallet_coach_for_order(session, order_id)
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
            owner_minor=owner_cut, coach_minor=coach_net, currency=currency, at=at,
            # The club only ever RECEIVES Yoco and EFT. A cash/desk-card payment recorded against a
            # coaching order is money the COACH took from the client — the club has no facility for
            # collecting on a coach's behalf — so the ledger must book the club's commission as owed
            # BY him, not book his net as owed TO him. This used to hard-code 'club' for every
            # payment path, which was wrong by the whole gross on every desk-collected lesson.
            cash_held_by=held_by)
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


def _wallet_coach_for_order(session, order_id):
    """The coach on the token_wallet this order granted — how a PACK SALE knows whose sale it was.
    Mirrors `_earnings_cte`'s pack branch exactly. Guarded → None (the split stays club-attributed,
    which is the pre-existing behaviour, never an exception)."""
    try:
        return session.execute(
            text("SELECT coach_user_id FROM billing.token_wallet "
                 "WHERE order_id = :o AND coach_user_id IS NOT NULL LIMIT 1"),
            {"o": str(order_id)},
        ).scalar()
    except Exception:
        return None


def _write_split_pair(session, *, club_id, payment_id, order_line_id, booking_id,
                      coach_user_id, product_id, rule_id, basis, gross_minor, pct,
                      owner_minor, coach_minor, currency, at=None,
                      cash_held_by="club") -> Dict[str, int]:
    """Insert the owner + coach commission_split rows (idempotent) and, on a fresh coach split, post
    the matching coach_ledger entry (idempotent on ref_id=split.id).
    `payment_id` may be None for arrears collection (the unique index uses NULLS NOT
    DISTINCT so it still dedupes on order_line_id+party_type).

    `cash_held_by` decides the DIRECTION of the ledger entry, and it is the whole point:

      'club'  — the club took the money (Yoco / desk / EFT / invoice). The club holds the gross and
                owes the coach their net share  →  +coach_net as 'commission_earning'.
      'coach' — the coach collected it themselves, off-platform (docs/specs/01: "the coach chases the
                EFT himself"). They already hold the gross, so the club is owed its commission
                →  −owner_cut as 'commission_due'.

    Both paths used to post +coach_net. On a R400 lesson at 20% that meant the ledger read
    "club owes the coach R320" when the truth was "the coach owes the club R80" — wrong by the full
    R400, in the wrong direction, on every off-platform lesson. It surfaced as "Coach payouts due"
    telling the owner to pay a coach who was in fact holding the club's money.

    The commission_split rows are IDENTICAL either way — the sale was split the same, whoever held
    the cash — so commission reporting (cockpit_coach_earnings, the Money P&L) is untouched. Only the
    running balance changes, which is exactly the thing that was lying."""
    splits = 0
    earnings = 0
    coach_holds = (cash_held_by == "coach")
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

    # Post to the signed ledger (only when a fresh coach split was written AND the coach is known).
    # Idempotent on ref_id = the split id, per entry_type. Which entry — and which SIGN — depends on
    # who is actually holding the cash (see the docstring).
    if coach_split_id is not None and coach_user_id is not None:
        entry_type = "commission_due" if coach_holds else "commission_earning"
        amount = -int(owner_minor) if coach_holds else int(coach_minor)
        note = (f"{basis} — coach collected, club commission due" if coach_holds
                else f"{basis} earning")
        # The entry type is INLINED, not bound: ON CONFLICT infers its partial unique index from the
        # predicate at plan time, so a bind parameter there matches no index and the upsert fails.
        # Safe to interpolate — it's one of two internal literals chosen just above, never input.
        led = session.execute(
            text(f"""
                INSERT INTO billing.coach_ledger
                    (club_id, coach_user_id, entry_type, amount_minor, currency,
                     ref_type, ref_id, note, occurred_at)
                VALUES (:club, :coach, '{entry_type}', :amount, :cur,
                        'split', :ref, :note, COALESCE(:at, now()))
                ON CONFLICT (club_id, coach_user_id, ref_id)
                    WHERE entry_type = '{entry_type}'
                DO NOTHING
                RETURNING id
            """),
            {"club": club_id, "coach": str(coach_user_id),
             "amount": amount, "cur": currency,
             "ref": str(coach_split_id), "note": note, "at": at},
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
    """Lazily populate billing.coach_arrears from confirmed lesson bookings AND enrolled class
    seats that have NOT been paid online (no succeeded charge payment on their order) and are not
    membership-covered. Each unpaid lesson/class posts to the coach's per-client tab (status='owed').
    Idempotent on the source (ux_coach_arrears_booking for lessons; ux_coach_arrears_enrolment for
    classes) — re-running adds nothing for already-tracked lessons/enrolments. Returns the count of
    NEW arrears rows. Guarded so a missing diary.* degrades to 0.

    The 'unpaid' test: the order has settlement_mode <> membership_covered, is not already
    settled/cleared, and has no succeeded charge payment, and the order line carries a positive ex-VAT
    gross. Online-paid lessons/classes settle via record_split_for_order instead and are excluded here.

    OWNER RULE (2026-07): a class enrolment pays the coach who runs it EXACTLY like a lesson. A class
    has no diary.booking (it keys off diary.enrolment), so its arrears row carries enrolment_id (not
    booking_id), coach = class_session.coach_user_id, client = the order's payer, gross = the class
    order line. A class with no coach can't be attributed and is skipped.
    """
    try:
        # (a) LESSONS — one arrears row per source booking (booking_id dedupe).
        res_l = session.execute(
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
        # (b) CLASSES — one arrears row per enrolled seat (enrolment_id dedupe). booking_id stays NULL;
        # coach = the class's coach; client = the order's payer (o.user_id); gross = the class line.
        res_c = session.execute(
            text("""
                INSERT INTO billing.coach_arrears
                    (club_id, coach_user_id, client_user_id, booking_id, enrolment_id,
                     order_line_id, product_id, gross_minor, currency, status)
                SELECT cs.club_id,
                       cs.coach_user_id AS coach_user_id,
                       o.user_id        AS client_user_id,
                       NULL::uuid       AS booking_id,
                       e.id             AS enrolment_id,
                       ol.id            AS order_line_id,
                       pr.id            AS product_id,
                       ol.amount_minor  AS gross_minor,
                       o.currency_code  AS currency,
                       'owed'
                FROM diary.enrolment e
                JOIN diary.class_session cs ON cs.id = e.class_session_id AND cs.club_id = e.club_id
                JOIN billing.order_line ol  ON ol.enrolment_id = e.id AND ol.club_id = e.club_id
                JOIN billing."order" o      ON o.id = ol.order_id
                LEFT JOIN billing.price   p  ON p.id  = ol.price_id
                LEFT JOIN billing.product pr ON pr.id = p.product_id
                WHERE cs.club_id = :club
                  AND e.status IN ('enrolled','attended','no_show')
                  AND ol.amount_minor > 0
                  AND o.settlement_mode <> 'membership_covered'
                  AND o.status NOT IN ('paid','void','written_off')
                  AND cs.coach_user_id IS NOT NULL
                  AND NOT EXISTS (
                        SELECT 1 FROM billing.payment pay
                        WHERE pay.order_id = o.id AND pay.direction = 'charge'
                          AND pay.status = 'succeeded')
                ON CONFLICT (club_id, enrolment_id) WHERE enrolment_id IS NOT NULL
                DO NOTHING
            """),
            {"club": club_id},
        )
        return (res_l.rowcount or 0) + (res_c.rowcount or 0)
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
        owner_minor=owner_cut, coach_minor=coach_net, currency=row["currency"] or "ZAR",
        # THE COACH holds this money — arrears is off-platform by definition (docs/specs/01: the coach
        # chases the EFT into their own account). So the ledger records the club's commission as OWED
        # BY the coach, not the coach's share as owed to them.
        cash_held_by="coach")
    return {"ok": True, "status": "collected", "splits": wrote["splits"],
            "owner_cut_minor": owner_cut, "coach_net_minor": coach_net,
            "commission_pct": str(pct), "cash_held_by": "coach",
            # What this collection did to the running balance: the coach now owes the club its cut.
            "ledger_delta_minor": -owner_cut}


def client_service_breakdown(session, *, club_id, coach_user_id, client_user_id, month=None) -> Dict[str, Any]:
    """One client's coaching grouped BY SERVICE (product + duration): e.g. 'Private lesson · 45 min ·
    3 · R750', with the individual sessions (booking_id/enrolment_id → the event story). Composes
    diary.booking + diary.enrolment/class_session + order_line + price + product + coach_arrears,
    scoped to this coach + client. `month` (YYYY-MM) filters by the session date; omit for all-time.

    OWNER RULE (2026-07): a CLASS the coach runs is a first-class coaching-money citizen alongside a
    lesson — it appears here as its own service group with the SAME real money state. A class has no
    diary.booking, so it keys off diary.enrolment (booking_id NULL, enrolment_id set) and its arrears
    join is on enrolment_id.

    Each session carries its REAL money state (not just the order status): a written-off or DISCOUNTED
    session shows as such — derived from coach_arrears (write-off status; a gross_minor that differs from
    what was billed = discounted). Returns {total_minor (effective), billed_minor (gross, pre-discount/
    write-off), services:[{key,label,count,total_minor,billed_minor,items:[{booking_id,enrolment_id,
    starts_at,billed_minor,amount_minor(effective),status}]}]}. status ∈ paid|owed|written_off|
    discounted|covered|pending|refunded. Guarded → empty."""
    try:
        accrue_arrears_for_club(session, club_id=club_id)   # so an owed lesson/class carries an arrears row
    except Exception:
        pass
    lesson_month = "AND to_char(b.starts_at,'YYYY-MM') = :ym" if month else ""
    class_month = "AND to_char(cs.starts_at,'YYYY-MM') = :ym" if month else ""
    params: Dict[str, Any] = {"c": club_id, "coach": str(coach_user_id), "client": str(client_user_id)}
    if month:
        params["ym"] = month
    try:
        rows = session.execute(
            text("""
                SELECT b.id AS booking_id, NULL::uuid AS enrolment_id,
                       b.starts_at, b.ends_at, b.status AS bstatus,
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
                  """ + lesson_month + """
                UNION ALL
                SELECT NULL::uuid AS booking_id, e.id AS enrolment_id,
                       cs.starts_at, cs.ends_at, e.status AS bstatus,
                       ol.amount_minor, ol.original_amount_minor, o.status AS ostatus, o.settlement_mode,
                       pr.duration_minutes AS price_dur, prod.id AS product_id, prod.name AS product_name,
                       ca.status AS arr_status, ca.gross_minor AS arr_gross
                FROM diary.enrolment e
                JOIN diary.class_session cs ON cs.id = e.class_session_id AND cs.club_id = e.club_id
                LEFT JOIN billing.order_line ol ON ol.enrolment_id = e.id
                LEFT JOIN billing."order" o ON o.id = ol.order_id
                LEFT JOIN billing.price pr ON pr.id = ol.price_id
                LEFT JOIN billing.product prod ON prod.id = pr.product_id
                LEFT JOIN billing.coach_arrears ca ON ca.enrolment_id = e.id AND ca.club_id = e.club_id
                WHERE cs.club_id = :c AND cs.coach_user_id = :coach AND o.user_id = :client
                  AND e.status IN ('enrolled','attended','no_show')
                  """ + class_month + """
                ORDER BY starts_at DESC
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
        is_class = r["booking_id"] is None
        name = r["product_name"] or ("Class" if is_class else "Lesson")
        key = (str(r["product_id"]) if r["product_id"] else ("cls" if is_class else "x")) + "-" + str(dur)
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
        g["items"].append({"booking_id": str(r["booking_id"]) if r["booking_id"] else None,
                           "enrolment_id": str(r["enrolment_id"]) if r["enrolment_id"] else None,
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
            SELECT cs.gross_minor, cs.occurred_at, b.starts_at,
                   COALESCE(b.booking_type, 'class') AS booking_type
            FROM billing.commission_split cs
            LEFT JOIN diary.booking b   ON b.id  = cs.booking_id
            LEFT JOIN billing.order_line ol ON ol.id = cs.order_line_id
            LEFT JOIN billing."order" o  ON o.id  = ol.order_id
            WHERE cs.club_id = :c AND cs.coach_user_id = :coach AND cs.party_type = 'coach'
              AND cs.basis IN ('lesson_commission','class_commission','arrears_commission')
              AND COALESCE(b.booked_by_user_id, o.user_id) = :cu
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
            SELECT a.gross_minor, a.status, a.note,
                   COALESCE(b.starts_at, cs.starts_at) AS starts_at, a.created_at
            FROM billing.coach_arrears a
            LEFT JOIN diary.booking b ON b.id = a.booking_id
            LEFT JOIN diary.enrolment e ON e.id = a.enrolment_id
            LEFT JOIN diary.class_session cs ON cs.id = e.class_session_id
            WHERE a.club_id = :c AND a.coach_user_id = :coach AND a.client_user_id = :cu
              AND a.status IN ('owed','written_off')
            ORDER BY COALESCE(b.starts_at, cs.starts_at, a.created_at)
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

    # A CLASS split has no booking_id — resolve the client via the order's payer so a class the client
    # paid shows on THEIR statement too (LEFT JOIN + COALESCE, matching coach_statement so both agree).
    paid = session.execute(
        text("""
            SELECT cs.coach_user_id,
                   count(*) FILTER (WHERE cs.basis <> 'refund_clawback') AS lesson_count,
                   COALESCE(SUM(cs.gross_minor),0) AS paid_minor
            FROM billing.commission_split cs
            LEFT JOIN diary.booking b   ON b.id  = cs.booking_id
            LEFT JOIN billing.order_line ol ON ol.id = cs.order_line_id
            LEFT JOIN billing."order" o  ON o.id  = ol.order_id
            WHERE cs.club_id = :club AND cs.party_type = 'coach'
              -- refund_clawback (negative gross) nets a refunded lesson out of the client's paid-this-
              -- month, mirroring coach_statement so the two sides agree; count excludes it (not a lesson).
              AND cs.basis IN ('lesson_commission','class_commission','arrears_commission','refund_clawback')
              AND COALESCE(b.booked_by_user_id, o.user_id) = :u
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
                   a.created_at, COALESCE(b.starts_at, cs.starts_at) AS starts_at
            FROM billing.coach_arrears a
            LEFT JOIN diary.booking b ON b.id = a.booking_id
            LEFT JOIN diary.enrolment e ON e.id = a.enrolment_id
            LEFT JOIN diary.class_session cs ON cs.id = e.class_session_id
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


# ---------------------------------------------------------------------------
# club <-> coach SETTLEMENT (payouts) — the other half of the loop: the cockpit REPORTS the
# running coach_ledger balance; a payout is how it's actually paid DOWN. Append-only: a payout
# posts ONE 'payout' coach_ledger entry (idempotent on ref_id=payout.id) that nets the balance.
# ---------------------------------------------------------------------------

def _payout_ledger_delta(direction, amount, balance):
    """The SIGNED coach_ledger delta a payout posts. amount is a positive magnitude.
    club_to_coach -> -amount (club paid the coach; reduces what the club owes).
    coach_to_club -> +amount (coach paid the club; reduces what the coach owes).
    offset        -> net against the live balance (sign = toward zero)."""
    if direction == "club_to_coach":
        return -amount
    if direction == "coach_to_club":
        return amount
    return -amount if balance > 0 else amount   # offset


def _post_payout_ledger(session, *, club_id, coach_user_id, payout_id, delta, note):
    """Post the append-only 'payout' coach_ledger entry (idempotent on ref_id=payout.id)."""
    session.execute(
        text("""
            INSERT INTO billing.coach_ledger
                (club_id, coach_user_id, entry_type, amount_minor, currency, ref_type, ref_id, note)
            VALUES (:c, :coach, 'payout', :amt, 'ZAR', 'payout', :ref, :note)
            ON CONFLICT (club_id, coach_user_id, ref_id) WHERE entry_type = 'payout' DO NOTHING
        """),
        {"c": club_id, "coach": str(coach_user_id), "amt": int(delta),
         "ref": str(payout_id), "note": note},
    )


def record_coach_payout(session, *, club_id, coach_user_id, amount_minor, direction,
                        method="eft", reference=None, period_label=None, note=None,
                        created_by=None, status="paid") -> Dict[str, Any]:
    """Record a club<->coach settlement and, when status='paid', post the matching coach_ledger entry
    that nets the running balance (append-only, never an edit). `amount_minor` is a POSITIVE magnitude;
    `direction` (club_to_coach|coach_to_club|offset) decides the ledger sign (see _payout_ledger_delta).
    A 'draft' records the intent without moving the balance (flip it with set_payout_status). Returns
    {ok, payout_id, ledger_delta, balance_minor} or {ok:False, error}."""
    amt = abs(int(amount_minor or 0))
    if amt <= 0:
        return {"ok": False, "error": "AMOUNT_REQUIRED"}
    direction = (direction or "").strip().lower()
    if direction not in ("club_to_coach", "coach_to_club", "offset"):
        return {"ok": False, "error": "BAD_DIRECTION"}
    method = (method or "eft").strip().lower()
    if method not in ("eft", "cash", "offset"):
        method = "eft"
    status = (status or "paid").strip().lower()
    if status not in ("draft", "paid"):
        status = "paid"
    pid = session.execute(
        text("""
            INSERT INTO billing.coach_payout
                (club_id, coach_user_id, direction, amount_minor, method, reference,
                 period_label, status, note, created_by_user_id, paid_at)
            VALUES (:c, :coach, :dir, :amt, :method, :ref, :period, :status, :note, :by,
                    CASE WHEN :status = 'paid' THEN now() ELSE NULL END)
            RETURNING id
        """),
        {"c": club_id, "coach": str(coach_user_id), "dir": direction, "amt": amt, "method": method,
         "ref": reference, "period": period_label, "status": status, "note": note,
         "by": str(created_by) if created_by else None},
    ).scalar()
    delta = 0
    if status == "paid":
        bal = coach_balance(session, club_id=club_id, coach_user_id=coach_user_id)
        delta = _payout_ledger_delta(direction, amt, bal)
        _post_payout_ledger(session, club_id=club_id, coach_user_id=coach_user_id, payout_id=pid,
                            delta=delta, note=note or ("payout " + direction))
    return {"ok": True, "payout_id": str(pid), "ledger_delta": delta,
            "balance_minor": coach_balance(session, club_id=club_id, coach_user_id=coach_user_id)}


def set_payout_status(session, *, club_id, payout_id, status) -> Dict[str, Any]:
    """Flip a payout's status. 'draft'->'paid' posts the ledger entry (idempotent); 'void' on a
    still-'draft' payout just marks it void (no ledger). Voiding a PAID payout is refused here (a
    reversal would need its own compensating entry — out of scope). Returns {ok, status, balance}."""
    status = (status or "").strip().lower()
    if status not in ("paid", "void"):
        return {"ok": False, "error": "BAD_STATUS"}
    row = session.execute(
        text("SELECT coach_user_id, direction, amount_minor, status, note "
             "FROM billing.coach_payout WHERE club_id = :c AND id = :id"),
        {"c": club_id, "id": str(payout_id)},
    ).mappings().first()
    if row is None:
        return {"ok": False, "error": "NOT_FOUND"}
    if row["status"] == "paid" and status == "void":
        return {"ok": False, "error": "CANNOT_VOID_PAID"}
    if row["status"] == status:
        return {"ok": True, "status": status,
                "balance_minor": coach_balance(session, club_id=club_id, coach_user_id=row["coach_user_id"])}
    coach = row["coach_user_id"]
    if status == "paid":
        session.execute(
            text("UPDATE billing.coach_payout SET status='paid', paid_at=now() "
                 "WHERE club_id=:c AND id=:id"), {"c": club_id, "id": str(payout_id)})
        bal = coach_balance(session, club_id=club_id, coach_user_id=coach)
        delta = _payout_ledger_delta(row["direction"], abs(int(row["amount_minor"] or 0)), bal)
        _post_payout_ledger(session, club_id=club_id, coach_user_id=coach, payout_id=payout_id,
                            delta=delta, note=row["note"] or ("payout " + row["direction"]))
    else:  # void a draft
        session.execute(
            text("UPDATE billing.coach_payout SET status='void' WHERE club_id=:c AND id=:id"),
            {"c": club_id, "id": str(payout_id)})
    return {"ok": True, "status": status,
            "balance_minor": coach_balance(session, club_id=club_id, coach_user_id=coach)}


def list_coach_payouts(session, *, club_id, coach_user_id=None, limit=100) -> List[Dict[str, Any]]:
    """Recorded club<->coach settlements, newest first (optionally one coach)."""
    where = "club_id = :c" + (" AND coach_user_id = :coach" if coach_user_id else "")
    params: Dict[str, Any] = {"c": club_id, "lim": int(limit)}
    if coach_user_id:
        params["coach"] = str(coach_user_id)
    rows = session.execute(
        text(f"SELECT id, coach_user_id, direction, amount_minor, currency, method, reference, "
             f"period_label, status, note, created_at, paid_at "
             f"FROM billing.coach_payout WHERE {where} ORDER BY created_at DESC LIMIT :lim"),
        params,
    ).mappings().all()
    return [dict(r) for r in rows]


def month_end_period(session, period_label=None) -> str:
    """The month the sweep bills — by default the month JUST ENDED, because the sweep now runs on the
    1st. It used to run on the 25th and default to the CURRENT month, which is why an invoice could
    never be complete: five or six days of lessons were still to come when the document was issued,
    and the client's live balance moved away from it immediately."""
    return period_label or session.execute(
        text("SELECT to_char(now() - interval '1 month','YYYY-MM')")).scalar()


def month_end_accrue(session, *, club_id, period) -> int:
    """Phase 1 — coach arrears + rent for the period. Cheap and idempotent; safe to repeat."""
    try:
        accrue_arrears_for_club(session, club_id=club_id)
    except Exception:
        log.info("run_month_end: arrears accrual skipped club=%s", club_id)
    try:
        return accrue_rent_for_club(session, club_id=club_id, year_month=period)
    except Exception:
        log.info("run_month_end: rent accrual skipped club=%s", club_id)
        return 0


def month_end_targets(session, *, club_id, period=None):
    """Phase 2 — every client to invoice for `period`: those with an OPEN balance DELIVERED in that
    month. Read-only, so it is safe to take once and then process client-by-client in separate
    transactions.

    The period bound is the whole point. Without it this returned everyone with any open order, of
    any age, so the sweep billed "everything owed right now" — the invoice went stale the moment the
    next lesson was played, and no month could ever be closed. Anything still owed from an earlier
    month is not re-billed here; it is already on its own month's invoice and shows on this one as
    "brought forward" (billing.invoicing.brought_forward_minor). Omitting `period` keeps the old
    behaviour for callers that genuinely want everything."""
    where = ""
    params = {"c": club_id}
    if period:
        from billing.invoicing import DELIVERED_AT_SQL
        where = (f"AND to_char(({DELIVERED_AT_SQL} AT TIME ZONE "
                 "COALESCE((SELECT timezone FROM club.club WHERE id = :c),'Africa/Johannesburg')),"
                 "'YYYY-MM') = :period")
        params["period"] = period
    return [dict(r) for r in session.execute(
        text('SELECT o.user_id, COALESCE(SUM(o.amount_minor),0) AS owed, '
             '       MIN(o.currency_code) AS cur '
             'FROM billing."order" o WHERE o.club_id = :c AND o.status = \'open\' '
             '  AND o.settled_by_order_id IS NULL AND o.covered_order_ids IS NULL '
             '  AND o.user_id IS NOT NULL AND o.amount_minor > 0 '
             f'  {where} '
             'GROUP BY o.user_id HAVING COALESCE(SUM(o.amount_minor),0) > 0'),
        params,
    ).mappings().all()]


def month_end_client(session, *, club_id, period, user_id, owed, cur, reissue=False) -> str:
    """Phase 3 — ONE client: claim the idempotency row, issue their consolidated statement invoice,
    then notify. Returns 'notified' | 'already'.

    `reissue=True` ignores the month_end_notice claim so a month swept before it ended can still be
    completed (see below). Returns 'notified' | 'already'.

    THIS IS THE UNIT OF WORK THAT MUST COMMIT ON ITS OWN. It allocates a GAPLESS invoice number and
    emits an email — and emit() dispatches on a background thread with its own session, so the email
    goes out immediately and does NOT roll back with us. Run the whole club in one transaction (as
    this did) and a timeout at client #400 rolls back 400 invoices whose numbered emails have already
    been delivered, while a re-run allocates different numbers. Per-client commits mean a failure
    costs exactly one client, and month_end_notice makes the re-run skip everyone already done."""
    fresh = session.execute(
        text("INSERT INTO billing.month_end_notice (club_id, user_id, period_label, owed_minor) "
             "VALUES (:c, :u, :p, :owed) "
             "ON CONFLICT (club_id, user_id, period_label) DO NOTHING RETURNING user_id"),
        {"c": club_id, "u": user_id, "p": period, "owed": int(owed or 0)},
    ).first()
    if not fresh and not reissue:
        return "already"
    # REISSUE is for closing a month that was swept BEFORE it ended. July 2026 is the live example:
    # the sweep ran on the 25th, and 55 orders delivered on the 26th-31st were never invoiced at all
    # — the notice row says "this client was notified for 2026-07", so an ordinary re-run skips them
    # and that debt is never billed by anything. Re-issuing is safe because issue_invoice already
    # skips any order on an ACTIVE invoice: a second pass covers only what is still uninvoiced, which
    # is a supplementary invoice, not a duplicate.
    # Consolidate this client's open orders into ONE numbered statement invoice document (orders
    # already on an active invoice are skipped). If there's genuinely nothing new to invoice (all
    # already invoiced intra-month), fall back to a plain balance reminder so the client is still
    # nudged. The orders themselves are never modified (still card-settleable live).
    invoice_id = None
    try:
        from billing import invoicing
        res = invoicing.issue_statement_invoice(
            session, club_id=club_id, user_id=str(user_id), period_label=period,
            scope_to_period=True)
        if res.get("ok"):
            invoice_id = res.get("invoice_id")
        elif reissue and not fresh:
            # A REISSUE exists to bill what arrived AFTER the first sweep. If there is nothing new,
            # this client already holds the right document and re-sending it is noise, not service —
            # closing July would otherwise email ~21 people an invoice they already have. The
            # ordinary sweep still re-sends (below): there, a client owing money with no fresh
            # document genuinely needs one.
            return "already"
        else:
            # Nothing NEW to invoice because the balance is already on an active invoice. Don't send a
            # contentless "pay online" reminder — re-send that EXISTING invoice (PDF + pay-link), so
            # every owing client gets an actual document. Only fall through to statement_ready when
            # there is genuinely no invoice at all.
            invoice_id = invoicing.latest_active_invoice_with_open_debt(
                session, club_id=club_id, user_id=str(user_id))
    except Exception:
        log.info("run_month_end: invoice issue skipped user=%s", user_id)
    try:
        from marketing_crm.tracking import emit
        if invoice_id:
            emit("invoice_issued", {"club_id": str(club_id), "user_id": str(user_id),
                                    "invoice_id": invoice_id, "amount_minor": int(owed or 0),
                                    "currency": cur or "ZAR"})
        else:
            emit("statement_ready", {"club_id": str(club_id), "user_id": str(user_id),
                                     "amount_minor": int(owed or 0), "currency": cur or "ZAR"})
    except Exception:
        log.info("run_month_end: notify skipped user=%s", user_id)
    return "notified"


def run_month_end(session, *, club_id, period_label=None, reissue=False) -> Dict[str, Any]:
    """The month-end sweep (C3, OPS-triggered — no always-on cron, fired by the keep-warm Action):
    (1) accrue coach arrears + rent for the period so the coach tabs are current, then (2) notify
    EVERY client who owes an open statement balance with a `statement_ready` message (in-app + email
    best-effort). Idempotent per (club, user, period) via billing.month_end_notice, so a re-run never
    re-notifies. Respects the LIVE-statement model (soft snapshot + notify, never a hard month lock).
    Returns {period, clients_owing, notified, already, rent_charges}. Never raises per-user.

    SINGLE-TRANSACTION orchestration, for one club and for the harness. The CRON ROUTE does NOT use
    this — it drives month_end_client per client in its own transaction, so a timeout costs one
    client instead of rolling back a whole club's already-emailed invoices (see month_end_client)."""
    period = month_end_period(session, period_label)
    rent_charges = month_end_accrue(session, club_id=club_id, period=period)
    rows = month_end_targets(session, club_id=club_id, period=period)
    notified = 0
    already = 0
    for r in rows:
        outcome = month_end_client(session, club_id=club_id, period=period,
                                   user_id=r["user_id"], owed=r["owed"], cur=r["cur"],
                                   reissue=reissue)
        if outcome == "already":
            already += 1
        else:
            notified += 1
    return {"period": period, "clients_owing": len(rows), "notified": notified,
            "already": already, "rent_charges": rent_charges}


def settlement_overview(session, *, club_id) -> Dict[str, Any]:
    """The admin 'who owes what' aging view (C4). Two ledgers, kept apart:
      - clients: everyone with an OPEN statement balance, bucketed by age (0-30 / 31-60 / 61+ days
        from the oldest open order), + per-bucket totals.
      - coaches: every coach with a non-zero coach_ledger balance (+ = club owes coach, - = coach
        owes club) — the club<->coach settlement worklist.
    Read-only + guarded (a degraded DB returns empty lists, never a 500)."""
    empty = {"clients": [], "client_totals": {"0-30": 0, "31-60": 0, "61+": 0},
             "coaches": [], "total_owed_minor": 0}
    try:
        clients = session.execute(
            text('SELECT o.user_id, u.first_name, u.surname, '
                 '       COALESCE(SUM(o.amount_minor),0) AS owed, '
                 '       EXTRACT(DAY FROM now() - MIN(o.created_at))::int AS age_days '
                 'FROM billing."order" o JOIN iam.user u ON u.id = o.user_id '
                 "WHERE o.club_id = :c AND o.status = 'open' AND o.settled_by_order_id IS NULL "
                 'GROUP BY o.user_id, u.first_name, u.surname '
                 'HAVING COALESCE(SUM(o.amount_minor),0) > 0 ORDER BY age_days DESC'),
            {"c": club_id},
        ).mappings().all()
    except Exception:
        return empty

    def _bucket(days):
        return "0-30" if days <= 30 else ("31-60" if days <= 60 else "61+")
    client_rows = []
    totals = {"0-30": 0, "31-60": 0, "61+": 0}
    for r in clients:
        days = int(r["age_days"] or 0)
        b = _bucket(days)
        owed = int(r["owed"] or 0)
        totals[b] += owed
        client_rows.append({"user_id": str(r["user_id"]),
                            "name": ((r["first_name"] or "") + " " + (r["surname"] or "")).strip(),
                            "owed_minor": owed, "age_days": days, "bucket": b})
    coach_rows = []
    try:
        coaches = session.execute(
            text("SELECT l.coach_user_id, u.first_name, u.surname, "
                 "       COALESCE(SUM(l.amount_minor),0) AS balance "
                 "FROM billing.coach_ledger l LEFT JOIN iam.user u ON u.id = l.coach_user_id "
                 "WHERE l.club_id = :c "
                 "GROUP BY l.coach_user_id, u.first_name, u.surname "
                 "HAVING COALESCE(SUM(l.amount_minor),0) <> 0 ORDER BY balance DESC"),
            {"c": club_id},
        ).mappings().all()
        coach_rows = [{"coach_user_id": str(r["coach_user_id"]),
                       "name": ((r["first_name"] or "") + " " + (r["surname"] or "")).strip() or "Coach",
                       "balance_minor": int(r["balance"] or 0)} for r in coaches]
    except Exception:
        coach_rows = []
    return {"clients": client_rows, "client_totals": totals, "coaches": coach_rows,
            "total_owed_minor": sum(c["owed_minor"] for c in client_rows)}


def coach_sessions_by_day(session, *, club_id, coach_user_id, month=None) -> Dict[str, Any]:
    """THE WORK LOG: every session this coach delivered in the month, by CLIENT, by DAY.

    Bounded on the **SESSION's own date** (`diary.booking.starts_at` / `class_session.starts_at`), not
    the order date \u2014 "what did I teach in July" is a question about when the lesson happened. A lesson
    booked in June for July belongs to July; a back-captured past lesson belongs to the day it ran.
    (The SETTLEMENT half of the statement is bounded on when the MONEY arrived instead \u2014 see
    `coach_settlement`. The two deliberately differ, and the statement says so.)

    Each row carries what it cost, whether it is settled, and \u2014 the point of the exercise \u2014 WHERE THAT
    MONEY IS: with the club, with the coach, or not yet collected. Guarded \u2192 empty."""
    ym = month or session.execute(text("SELECT to_char(now(),'YYYY-MM')")).scalar()
    try:
      with session.begin_nested():
        rows = session.execute(
            text("""
                WITH src AS (
                    -- LESSONS the coach delivers (his own resource / denormalised coach_user_id).
                    SELECT b.starts_at, b.ends_at,
                           -- booked_by_user_id IS the client: an on-behalf booking sets it to the
                           -- client, not the acting staff member (created_by_user_id is the actor).
                           b.booked_by_user_id AS client_user_id,
                           ol.order_id, ol.amount_minor, 'lesson' AS kind,
                           COALESCE(pr.name, ol.description) AS service
                    FROM diary.booking b
                    JOIN billing.order_line ol ON ol.booking_id = b.id
                    LEFT JOIN billing.price   p2 ON p2.id = ol.price_id
                    LEFT JOIN billing.product pr ON pr.id = p2.product_id
                    WHERE b.club_id = :club AND b.coach_user_id = CAST(:coach AS uuid)
                      AND b.booking_type = 'lesson'
                      AND b.status IN ('confirmed','completed','held')
                      AND to_char(b.starts_at, 'YYYY-MM') = :ym
                    UNION ALL
                    -- CLASSES he runs: one row per enrolled seat (that is what he is paid on).
                    SELECT cls.starts_at, cls.ends_at, e.user_id AS client_user_id,
                           ol.order_id, ol.amount_minor, 'class' AS kind,
                           COALESCE(pr.name, ol.description) AS service
                    FROM diary.class_session cls
                    JOIN diary.enrolment e ON e.class_session_id = cls.id
                                          AND e.status IN ('enrolled','attended')
                    JOIN billing.order_line ol ON ol.enrolment_id = e.id
                    LEFT JOIN billing.price   p2 ON p2.id = ol.price_id
                    LEFT JOIN billing.product pr ON pr.id = p2.product_id
                    WHERE cls.club_id = :club AND cls.coach_user_id = CAST(:coach AS uuid)
                      AND cls.status <> 'cancelled'
                      AND to_char(cls.starts_at, 'YYYY-MM') = :ym
                )
                SELECT src.*, o.status AS order_status, o.settlement_mode,
                       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.surname)),''),
                                u.email, 'Walk-in') AS client_name,
                       -- Did any REAL money land on the platform for this order? That is what
                       -- separates "the club has it" from "the coach collected it himself":
                       -- mark_arrears_collected flips the order to paid with NO payment row at all.
                       -- ONLY Yoco and EFT ever reach the club. Anything else recorded against a
                       -- coaching order is money the coach took from the client himself (the club
                       -- has no facility for collecting on his behalf) -- so there is no "desk"
                       -- bucket here to be ambiguous about.
                       COALESCE((SELECT SUM(pm.amount_minor) FROM billing.payment pm
                                  WHERE pm.order_id = o.id AND pm.direction = 'charge'
                                    AND pm.status = 'succeeded'
                                    AND pm.provider IN ('yoco','eft')), 0) AS to_bank
                FROM src
                LEFT JOIN billing."order" o ON o.id = src.order_id
                LEFT JOIN iam."user" u ON u.id = src.client_user_id
                ORDER BY client_name, src.starts_at
            """),
            {"club": club_id, "coach": str(coach_user_id), "ym": ym},
        ).mappings().all()
    except Exception:
        # NOT session.rollback(): this runs inside the CALLER's session_scope, so a full rollback
        # discards their work AND aborts every later block in this payload. The begin_nested above
        # scopes the failure to this block. (Same discipline as client360._guard.)
        log.exception("coach_sessions_by_day failed")
        rows = []

    clients: Dict[str, Dict[str, Any]] = {}
    totals = {"sessions": 0, "billed_minor": 0, "to_club_minor": 0,
              "with_coach_minor": 0, "outstanding_minor": 0, "not_charged_minor": 0}

    for r in rows:
        amt = int(r["amount_minor"] or 0)
        st = (r["order_status"] or "").lower()
        mode = (r["settlement_mode"] or "").lower()
        # WHERE IS THE MONEY? Order status alone can't say (see BUSINESS-RULES §6). The club only
        # ever RECEIVES Yoco and EFT; a coach's own collection is either cash recorded at the court
        # or an arrears collection with no payment row at all. So: paid + money in the club's
        # account -> the club has it; paid any other way -> the coach does.
        if mode in ("membership_covered", "free", "token") or amt <= 0:
            custody, label = "not_charged", "No charge"
        elif st == "paid" and int(r["to_bank"] or 0) > 0:
            custody, label = "to_club", "Paid to club"
        elif st == "paid":
            # Paid, but nothing reached the club's account: cash/card at the court, or an arrears
            # collection that never wrote a payment row at all. Either way the coach is holding it.
            custody, label = "with_coach", "Collected by coach"
        elif st in ("written_off", "refunded", "void"):
            custody, label = "not_charged", st.replace("_", " ").title()
        else:
            custody, label = "outstanding", "Outstanding"

        key = str(r["client_user_id"]) if r["client_user_id"] else "_walkin"
        c = clients.setdefault(key, {
            "client_user_id": (str(r["client_user_id"]) if r["client_user_id"] else None),
            "client_name": r["client_name"], "rows": [],
            "totals": {"sessions": 0, "billed_minor": 0, "to_club_minor": 0,
                       "with_coach_minor": 0, "outstanding_minor": 0, "not_charged_minor": 0}})
        c["rows"].append({
            "date": r["starts_at"].date().isoformat() if r["starts_at"] else None,
            "starts_at": r["starts_at"].isoformat() if r["starts_at"] else None,
            "kind": r["kind"],
            "service": r["service"] or ("Class" if r["kind"] == "class" else "Lesson"),
            "amount_minor": amt,
            "order_id": str(r["order_id"]) if r["order_id"] else None,
            "order_status": st or None,
            "custody": custody,
            "custody_label": label,
        })
        for scope in (c["totals"], totals):
            scope["sessions"] += 1
            scope["billed_minor"] += amt
            scope[custody + "_minor"] += amt

    out = sorted(clients.values(),
                 key=lambda c: (-c["totals"]["outstanding_minor"], c["client_name"] or ""))
    return {"month": ym, "clients": out, "totals": totals}


def coach_settlement(session, *, club_id, coach_user_id, month=None) -> Dict[str, Any]:
    """THE SETTLEMENT MATH \u2014 what the club and the coach owe each other for the month.

        total collected (club-held + coach-held)  \u00d7 commission  =  owed to the club
        \u2212 what the club is already holding
        =  NET  (+ club pays the coach \u00b7 \u2212 the coach pays the club)

    Bounded on when the MONEY ARRIVED (`commission_split.occurred_at`), never the sale date \u2014 the
    owner's rule is that commission is paid on funds RECEIVED (docs/specs/01 \u00a7D7), so a lesson taught
    in July and paid in August settles in August.

    **CUSTODY IS THE PAYMENT PROVIDER** (`cash_custody_for`): the club only ever receives Yoco and
    EFT, so those are club-held and everything else is the coach's. `arrears_commission` carries no
    payment row at all (`mark_arrears_collected` is off-platform by definition) and lands coach-held
    through the same rule. Basis alone is NOT enough \u2014 a cash payment recorded at the desk against a
    coaching order writes `lesson_commission` but the coach is the one holding the note.

    The net computed here is, by construction, the same figure the `coach_ledger` accumulates:
    a club-held collection posts `+coach_net`, a coach-held one posts `\u2212owner_cut`, and
    `\u03a3(gross_club \u2212 owner_cut_club) \u2212 \u03a3 owner_cut_coach == gross_club \u2212 \u03a3 owner_cut`. `reconciles`
    asserts exactly that against the ledger, so a drift shows up on the document instead of hiding.
    Guarded \u2192 zeroes."""
    ym = month or session.execute(text("SELECT to_char(now(),'YYYY-MM')")).scalar()
    z = {"club_held_minor": 0, "coach_held_minor": 0, "total_collected_minor": 0,
         "commission_minor": 0, "clawback_minor": 0, "net_minor": 0, "effective_pct": None}
    try:
      with session.begin_nested():
        r = session.execute(
            text("""
                WITH sp AS (
                    SELECT cs.*,
                           -- WHO HOLDS IT: only a Yoco charge or an EFT ever reaches the club. A desk
                           -- cash/card payment on a coaching order, and an arrears collection (which
                           -- writes no payment row at all), are both money the COACH is holding.
                           (pm.provider IN ('yoco','eft')) AS club_banked
                    FROM billing.commission_split cs
                    LEFT JOIN billing.payment pm ON pm.id = cs.payment_id
                    WHERE cs.club_id = :club AND cs.coach_user_id = CAST(:coach AS uuid)
                      AND cs.party_type = 'owner'
                      AND to_char(cs.occurred_at, 'YYYY-MM') = :ym
                )
                SELECT
                  -- The OWNER side carries the club's cut; gross_minor is the sale it was cut from.
                  COALESCE(SUM(gross_minor) FILTER (
                      WHERE basis <> 'refund_clawback' AND club_banked), 0)         AS club_gross,
                  COALESCE(SUM(gross_minor) FILTER (
                      WHERE basis <> 'refund_clawback'
                        AND COALESCE(club_banked, false) = false), 0)               AS coach_gross,
                  COALESCE(SUM(amount_minor) FILTER (
                      WHERE basis <> 'refund_clawback'), 0)                         AS commission,
                  COALESCE(SUM(amount_minor) FILTER (
                      WHERE basis = 'refund_clawback'), 0)                          AS clawback,
                  COALESCE(SUM(gross_minor) FILTER (
                      WHERE basis = 'refund_clawback'), 0)                          AS refunded_gross,
                  COALESCE(SUM(gross_minor) FILTER (
                      WHERE basis = 'refund_clawback' AND club_banked), 0)          AS refunded_club
                FROM sp
            """),
            {"club": club_id, "coach": str(coach_user_id), "ym": ym},
        ).mappings().first() or {}
    except Exception:
        log.exception("coach_settlement splits failed")     # savepoint above scopes the failure
        r = {}

    g = lambda k: int((r or {}).get(k) or 0)                                   # noqa: E731
    # A refund reverses a collection: money the club banked and gave back is no longer held, so it
    # must come off club_held too, not just off the commission. (refunded_club is negative-signed
    # gross on the clawback rows.)
    club_held = g("club_gross") + g("refunded_club")
    coach_held = g("coach_gross")
    total = club_held + coach_held
    # A clawback is a NEGATIVE owner split (commission returned on a refund) — it reduces what the
    # club is owed, so it belongs in the commission figure rather than beside it.
    commission = g("commission") + g("clawback")
    net = club_held - commission
    st = {"club_held_minor": club_held, "coach_held_minor": coach_held,
          "total_collected_minor": total, "commission_minor": commission,
          "clawback_minor": g("clawback"), "refunded_gross_minor": g("refunded_gross"),
          "net_minor": net,
          "effective_pct": (round(commission * 100.0 / total, 2) if total else None)}

    # --- the ledger side: rent, payouts and adjustments this month, + the running balance ---------
    led = {"rent_minor": 0, "payouts_minor": 0, "adjustments_minor": 0, "clawback_minor": 0,
           "commission_entries_minor": 0, "balance_minor": 0, "entries": []}
    try:
      with session.begin_nested():
        rows = session.execute(
            text("SELECT entry_type, amount_minor, note, ref_type, ref_id, occurred_at "
                 "FROM billing.coach_ledger "
                 "WHERE club_id = :club AND coach_user_id = CAST(:coach AS uuid) "
                 "  AND to_char(occurred_at,'YYYY-MM') = :ym "
                 "ORDER BY occurred_at"),
            {"club": club_id, "coach": str(coach_user_id), "ym": ym},
        ).mappings().all()
        for e in rows:
            amt = int(e["amount_minor"] or 0)
            if e["entry_type"] == "rent_charge":
                led["rent_minor"] += amt
            elif e["entry_type"] == "payout":
                led["payouts_minor"] += amt
            elif e["entry_type"] == "adjustment":
                # A REFUND CLAWBACK IS A COMMISSION REVERSAL, not a manual correction — it just has
                # to be written as an `adjustment` because `commission_earning` carries a UNIQUE
                # index on ref_id and the clawback references the same split. It belongs on the
                # commission side of the reconciliation: the SPLITS already net it out of the
                # settlement, so counting it as an adjustment made `reconciles` fail by exactly the
                # clawback every time a coach had a refund — a warning banner on a document whose
                # money was correct, which is the fastest way to make people stop believing the
                # warning. `ref_type='split'` is what distinguishes it from a real manual entry.
                if (e["ref_type"] or "") == "split":
                    led["clawback_minor"] += amt
                    led["commission_entries_minor"] += amt
                else:
                    led["adjustments_minor"] += amt
            else:                                     # commission_earning | commission_due
                led["commission_entries_minor"] += amt
            led["entries"].append({
                "entry_type": e["entry_type"], "amount_minor": amt,
                "note": e["note"], "ref_id": e["ref_id"],
                "occurred_at": e["occurred_at"].isoformat() if e["occurred_at"] else None})
        led["balance_minor"] = int(session.execute(
            text("SELECT COALESCE(SUM(amount_minor),0) FROM billing.coach_ledger "
                 "WHERE club_id = :club AND coach_user_id = CAST(:coach AS uuid)"),
            {"club": club_id, "coach": str(coach_user_id)}).scalar() or 0)
    except Exception:
        log.exception("coach_settlement ledger failed")     # savepoint above scopes the failure

    # The document's own audit line. The month's commission ENTRIES must equal the net derived from
    # the splits — they are two views of the same event, so a mismatch means something wrote one
    # without the other and the statement should SAY so rather than quietly present a wrong number.
    # WHAT THE COLLECTED MONEY WAS. Without this the statement says "Paid to the club R17,000" and
    # leaves the coach to reconcile it against the lessons he remembers teaching — which will never
    # agree, because the figure legitimately also contains CLASS seats and PACK SALES. A lesson/class
    # pack carries the coach's own lesson/class price_id (so commission attributes to him), which
    # means the FULL pack price lands here at the moment of sale, not spread over the sessions drawn
    # from it. That is deliberate — pack revenue is sale-based — but it has to be visible, or the
    # headline reads as a threefold error.
    st["by_kind"] = _settlement_by_kind(session, club_id=club_id, coach_user_id=coach_user_id, ym=ym)
    st["ledger_commission_minor"] = led["commission_entries_minor"]
    st["reconciles"] = (led["commission_entries_minor"] == net)
    # What actually changes hands after rent and anything already settled this month.
    # `adjustments_minor` is now MANUAL corrections only (the clawback moved to the commission side,
    # where the splits already accounted for it) — so this cannot double-count it.
    st["due_now_minor"] = net + led["rent_minor"] + led["adjustments_minor"] + led["payouts_minor"]
    return {"month": ym, "settlement": st, "ledger": led,
            "currency": _club_currency_code(session, club_id)}


def _settlement_by_kind(session, *, club_id, coach_user_id, ym):
    """The month's collected gross split by WHAT IT WAS — lesson / class / pack — and by who holds it.

    `commission_split.basis` cannot tell a pack from a lesson: a lesson pack is deliberately hung on
    the coach's own lesson price so the commission attributes to him, so it writes `lesson_commission`
    too. A pack is identified the way `_earnings_cte` identifies one — the order granted a
    `billing.token_wallet`. Guarded → {} (the statement just omits the breakdown)."""
    try:
        rows = session.execute(
            text("""
                SELECT CASE
                         WHEN EXISTS (SELECT 1 FROM billing.token_wallet w WHERE w.order_id = o.id)
                           THEN 'pack'
                         -- A clawback inherits the KIND of what it reverses, which basis alone
                         -- cannot say (it is always 'refund_clawback'); resolve it from the
                         -- original line's product instead.
                         WHEN cs.basis = 'class_commission' THEN 'class'
                         WHEN cs.basis = 'refund_clawback' AND pr.kind = 'class' THEN 'class'
                         ELSE 'lesson'
                       END AS kind,
                       (pm.provider IN ('yoco','eft')) AS club_banked,
                       (cs.basis = 'refund_clawback') AS is_clawback,
                       -- A clawback carries NEGATIVE gross, so it nets the refund out of its own
                       -- kind and the breakdown still sums to `total_collected`. It must NOT be
                       -- counted as a session though — it is the reversal of one.
                       COUNT(*) FILTER (WHERE cs.basis <> 'refund_clawback') AS n,
                       COALESCE(SUM(cs.gross_minor), 0) AS gross
                FROM billing.commission_split cs
                LEFT JOIN billing.payment pm ON pm.id = cs.payment_id
                LEFT JOIN billing.order_line ol ON ol.id = cs.order_line_id
                LEFT JOIN billing."order" o ON o.id = ol.order_id
                LEFT JOIN billing.price p2 ON p2.id = ol.price_id
                LEFT JOIN billing.product pr ON pr.id = p2.product_id
                WHERE cs.club_id = :club AND cs.coach_user_id = CAST(:coach AS uuid)
                  AND cs.party_type = 'owner'
                  AND to_char(cs.occurred_at, 'YYYY-MM') = :ym
                GROUP BY 1, 2, 3
            """),
            {"club": club_id, "coach": str(coach_user_id), "ym": ym},
        ).mappings().all()
    except Exception:
        session.rollback()
        log.debug("settlement by-kind skipped", exc_info=False)
        return {}
    out = {}
    for r in rows:
        k = out.setdefault(r["kind"], {"club_minor": 0, "coach_minor": 0, "n": 0})
        k["n"] += int(r["n"] or 0)
        k["club_minor" if r["club_banked"] else "coach_minor"] += int(r["gross"] or 0)
    return out


def _club_currency_code(session, club_id):
    try:
        return session.execute(text("SELECT currency_code FROM club.club WHERE id = :c"),
                               {"c": club_id}).scalar() or "ZAR"
    except Exception:
        return "ZAR"


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
    # A CLASS split carries NO booking_id (classes are enrolments), so resolve the client via the
    # order's payer (order_line -> order.user_id) — mirrors how class arrears set client_user_id, so
    # a class's paid + owed merge into the SAME per-client row exactly like a lesson does.
    paid = session.execute(
        text("""
            SELECT COALESCE(b.booked_by_user_id, o.user_id) AS client_user_id,
                   count(*) FILTER (WHERE cs.basis <> 'refund_clawback') AS lesson_count,
                   COALESCE(SUM(cs.gross_minor) FILTER (WHERE cs.basis <> 'refund_clawback'),0)
                       AS gross_minor,
                   COALESCE(SUM(cs.amount_minor),0) AS coach_net_minor
            FROM billing.commission_split cs
            LEFT JOIN diary.booking b   ON b.id  = cs.booking_id
            LEFT JOIN billing.order_line ol ON ol.id = cs.order_line_id
            LEFT JOIN billing."order" o  ON o.id  = ol.order_id
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
                   a.note, a.created_at, a.updated_at,
                   COALESCE(b.starts_at, cs.starts_at) AS starts_at,
                   u.first_name, u.surname, u.email
            FROM billing.coach_arrears a
            LEFT JOIN diary.booking b ON b.id = a.booking_id
            LEFT JOIN diary.enrolment e ON e.id = a.enrolment_id
            LEFT JOIN diary.class_session cs ON cs.id = e.class_session_id
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

    # --- The money triad for the coach summary band (billed → collected → outstanding) ----------
    # BILLED (gross) = the value of the coaching DELIVERED/SOLD this month, before any (non-)
    # collection — lessons + class seats, using the ORIGINAL amount so a discount doesn't shrink
    # "billed". Excludes 'held' (an abandoned online hold isn't real business). Mirrors the client
    # record's "billed" so coach and client never disagree.
    billed_minor = _coach_billed_month(session, club_id=club_id, coach_user_id=coach_user_id, ym=ym)
    # COMMISSION to the club = the owner's cut accrued this month (on what was collected). NET to the
    # coach is the coach party (already summed into paid_minor per client). COLLECTED (gross) is the
    # two put back together = what the client actually paid on this coach's sessions this month.
    commission_minor = int(session.execute(
        text("""
            SELECT COALESCE(SUM(amount_minor),0)
            FROM billing.commission_split
            WHERE club_id = :club AND coach_user_id = :coach AND party_type = 'owner'
              AND basis IN ('lesson_commission','class_commission','arrears_commission','refund_clawback')
              AND to_char(occurred_at,'YYYY-MM') = :ym
        """),
        {"club": club_id, "coach": str(coach_user_id), "ym": ym},
    ).scalar() or 0)

    clients = sorted(by_client.values(), key=lambda r: -r["net_minor"])
    net_collected = sum(c["paid_minor"] for c in clients)   # coach net collected this month
    owed_total = sum(c["owed_minor"] for c in clients)
    return {
        "month": ym,
        "currency": currency,
        "clients": clients,
        "arrears_items": arrears_items,
        "totals": {
            "paid_minor": net_collected,
            "owed_minor": owed_total,
            "net_minor": sum(c["net_minor"] for c in clients),
            "written_off_minor": written_off_minor,     # forgiven — informational, NOT in net
            "rent_minor": period_rent,
            "balance_minor": coach_balance(session, club_id=club_id, coach_user_id=coach_user_id),
            # The reconciling triad (all GROSS, this month's coaching):
            "billed_minor": billed_minor,                        # value of coaching delivered/sold
            "commission_minor": commission_minor,                # the club's cut (on collected)
            "collected_minor": net_collected + commission_minor,  # gross the client actually paid
        },
    }


def _coach_billed_month(session, *, club_id, coach_user_id, ym) -> int:
    """GROSS coaching billed in month `ym` (YYYY-MM) for this coach — lessons + class seats, at each
    line's ORIGINAL amount (pre-discount). Excludes 'held' (unpaid online holds). Guarded → 0. Kept
    here (not imported from coach.repositories) to avoid a lane import cycle; mirrors `_coach_billed`."""
    try:
        v = session.execute(
            text("""
                SELECT COALESCE(SUM(billed), 0) FROM (
                    SELECT COALESCE(ol.original_amount_minor, ol.amount_minor) AS billed
                    FROM diary.booking b
                    JOIN billing.order_line ol ON ol.booking_id = b.id AND ol.club_id = b.club_id
                    WHERE b.club_id = :c AND b.coach_user_id = :u AND b.booking_type = 'lesson'
                      AND to_char(b.starts_at,'YYYY-MM') = :ym
                      AND b.status IN ('confirmed','completed','no_show')
                    UNION ALL
                    SELECT COALESCE(ol.original_amount_minor, ol.amount_minor) AS billed
                    FROM diary.class_session cs
                    JOIN diary.enrolment e ON e.class_session_id = cs.id AND e.club_id = cs.club_id
                    JOIN billing.order_line ol ON ol.enrolment_id = e.id AND ol.club_id = cs.club_id
                    WHERE cs.club_id = :c AND cs.coach_user_id = :u
                      AND to_char(cs.starts_at,'YYYY-MM') = :ym
                      AND e.status IN ('enrolled','attended','no_show')
                ) x
            """),
            {"c": club_id, "u": str(coach_user_id), "ym": ym},
        ).scalar()
        return int(v or 0)
    except Exception:
        return 0

# billing/promotions.py — the Promotions Engine (specials with promo codes, redeemed at checkout).
#
# A promotion is an OFFER + a redeemable CODE. Redeeming it DELEGATES to billing.statement.discount_order
# (reduce the order total, pro-rata multi-line, coach-commission lockstep, "was → now" audit) — it NEVER
# invents a second debt store (one debt = one order). See docs/specs/PROMOTIONS-ENGINE.md.
#
# Phase 1: percent_off / amount_off, one shared code per promo, eligibility (scope/window/caps/min-spend/
# first-time/stacking). Phase 2 adds bonus_period (3+1 months) + unique per-recipient Klaviyo codes.
#
# Repos never commit — callers compose via db.session_scope().

import logging
from typing import Any, Dict, Optional

from sqlalchemy import text

log = logging.getLogger("billing.promotions")

# Scope kinds a promotion can target (mirror the order classifier below).
SCOPE_KINDS = ("membership", "pack", "court", "lesson", "class")

# The canonical order classifier (reused from billing.me.activity_summary) — a single order's "kind".
_ORDER_KIND_SQL = """
SELECT COALESCE(
         CASE WHEN EXISTS (SELECT 1 FROM billing.token_wallet w WHERE w.order_id = o.id) THEN 'pack'
              WHEN EXISTS (SELECT 1 FROM billing.membership_subscription ms WHERE ms.order_id = o.id) THEN 'membership'
         END,
         b.booking_type,
         CASE WHEN pr.kind = 'court_booking' THEN 'court' ELSE pr.kind END) AS kind,
       p.product_id AS product_id,
       o.amount_minor AS amount_minor,
       o.status AS status,
       o.user_id AS user_id
FROM billing."order" o
LEFT JOIN LATERAL (SELECT booking_id, price_id FROM billing.order_line
                   WHERE order_id = o.id ORDER BY created_at LIMIT 1) ol ON true
LEFT JOIN diary.booking b ON b.id = ol.booking_id
LEFT JOIN billing.price p ON p.id = ol.price_id
LEFT JOIN billing.product pr ON pr.id = p.product_id
WHERE o.club_id = :c AND o.id = :o
"""


# ---------------------------------------------------------------------------
# Lookup + eligibility
# ---------------------------------------------------------------------------

def _resolve_code(session, club_id, code):
    """Resolve a typed code to (promotion_row, code_row). A SHARED code lives on promotion.code
    (code_row is None); a UNIQUE per-recipient code lives on promotion_code (code_row is that row,
    carrying its own cap + optional bound recipient). Returns (None, None) if unknown/archived."""
    if not code:
        return None, None
    code = str(code).strip()
    promo = session.execute(text(
        "SELECT * FROM billing.promotion "
        "WHERE club_id = :c AND lower(code) = lower(:code) AND status <> 'archived' "
        "ORDER BY created_at DESC LIMIT 1"),
        {"c": str(club_id), "code": code}).mappings().first()
    if promo:
        return promo, None
    # Not a shared code — try a unique per-recipient code.
    child = session.execute(text(
        "SELECT * FROM billing.promotion_code "
        "WHERE club_id = :c AND lower(code) = lower(:code) LIMIT 1"),
        {"c": str(club_id), "code": code}).mappings().first()
    if not child:
        return None, None
    promo = session.execute(text(
        "SELECT * FROM billing.promotion WHERE id = :p AND status <> 'archived'"),
        {"p": str(child["promotion_id"])}).mappings().first()
    return (promo, child) if promo else (None, None)


def _compute_discount(promo, amount_minor) -> int:
    """The discount (minor units) this promo yields on `amount_minor`. Never exceeds the amount."""
    amount_minor = int(amount_minor or 0)
    if amount_minor <= 0:
        return 0
    if promo["kind"] == "percent_off":
        disc = (amount_minor * int(promo["percent_bps"] or 0) + 5000) // 10000
    else:  # amount_off
        disc = int(promo["value_minor"] or 0)
    return max(0, min(disc, amount_minor))


def _scope_ok(promo, kind, product_id) -> bool:
    at = promo["applies_to"]
    if at == "all":
        return True
    if at == "product":
        return bool(promo["product_id"]) and str(product_id or "") == str(promo["product_id"])
    return kind == at


def _redemption_counts(session, promo_id, user_id):
    """(global_applied, this_user_applied) — 'applied' redemptions only (reversed frees the slot)."""
    g = session.execute(text(
        "SELECT count(*) FROM billing.promotion_redemption "
        "WHERE promotion_id = :p AND status = 'applied'"), {"p": str(promo_id)}).scalar() or 0
    u = 0
    if user_id:
        u = session.execute(text(
            "SELECT count(*) FROM billing.promotion_redemption "
            "WHERE promotion_id = :p AND status = 'applied' AND user_id = :u"),
            {"p": str(promo_id), "u": str(user_id)}).scalar() or 0
    return int(g), int(u)


def _has_prior_purchase(session, club_id, user_id, kind) -> bool:
    """True if the user already has a PAID order of this scope (for first_time_only). 'all'/'product'
    fall back to any paid order in the club."""
    if not user_id:
        return False
    if kind in SCOPE_KINDS:
        # Count this user's PAID orders of the given kind, via the classifier as a subquery.
        cnt = session.execute(text(
            "SELECT count(*) FROM (" +
            _ORDER_KIND_SQL.replace("WHERE o.club_id = :c AND o.id = :o",
                                    "WHERE o.club_id = :c AND o.user_id = :u AND o.status = 'paid'") +
            ") sub WHERE sub.kind = :k"),
            {"c": str(club_id), "u": str(user_id), "k": kind}).scalar() or 0
        return int(cnt) > 0
    cnt = session.execute(text(
        "SELECT count(*) FROM billing.\"order\" WHERE club_id = :c AND user_id = :u AND status = 'paid'"),
        {"c": str(club_id), "u": str(user_id)}).scalar() or 0
    return int(cnt) > 0


def _bonus_months_for_order(session, order_id) -> int:
    """Extra membership months from an applied bonus_period promo on this order (0 if none). Read by the
    membership activation path so an ONLINE 3+1 grants term+bonus in one shot."""
    v = session.execute(text(
        "SELECT COALESCE(SUM(pr.bonus_qty),0) "
        "FROM billing.promotion_redemption r JOIN billing.promotion pr ON pr.id = r.promotion_id "
        "WHERE r.order_id = :o AND r.status = 'applied' AND pr.kind = 'bonus_period'"),
        {"o": str(order_id)}).scalar()
    return int(v or 0)


def _bonus_units_for_order(session, order_id) -> int:
    """Extra pack SESSIONS from an applied bonus_units promo on this order (0 if none). Read by the pack
    grant so an ONLINE 'buy 10 get 12' adds the free sessions at activation."""
    v = session.execute(text(
        "SELECT COALESCE(SUM(pr.bonus_qty),0) "
        "FROM billing.promotion_redemption r JOIN billing.promotion pr ON pr.id = r.promotion_id "
        "WHERE r.order_id = :o AND r.status = 'applied' AND pr.kind = 'bonus_units'"),
        {"o": str(order_id)}).scalar()
    return int(v or 0)


def _is_bonus(promo) -> bool:
    return promo["kind"] in ("bonus_period", "bonus_units")


def _check(session, club_id, promo, code_row=None, *, kind, product_id, amount_minor, user_id):
    """Shared eligibility → {ok, discount_minor, is_bonus} or {ok:False, error, reason}. Does NOT check
    stacking (that needs the order); apply_to_order adds it. `code_row` = the per-recipient code (or None
    for a shared code) — its own cap + recipient binding are enforced here."""
    if not promo:
        return {"ok": False, "error": "PROMO_NOT_FOUND", "reason": "That code isn't valid."}
    if promo["status"] != "active":
        return {"ok": False, "error": "INACTIVE", "reason": "This promotion isn't running."}
    # CAST both bounds (psycopg AmbiguousParameter on a bare `:p IS NULL` — CLAUDE.md gotcha).
    within = session.execute(text(
        "SELECT (CAST(:s AS timestamptz) IS NULL OR CAST(:s AS timestamptz) <= now()) "
        "   AND (CAST(:e AS timestamptz) IS NULL OR now() <= CAST(:e AS timestamptz))"),
        {"s": promo["starts_at"], "e": promo["ends_at"]}).scalar()
    if not within:
        return {"ok": False, "error": "EXPIRED", "reason": "This promotion isn't available right now."}
    # Unique per-recipient code: its own single-use cap + optional recipient binding.
    if code_row is not None:
        if code_row["status"] != "active":
            return {"ok": False, "error": "CODE_REVOKED", "reason": "That code is no longer valid."}
        if int(code_row["used_count"] or 0) >= int(code_row["max_uses"] or 1):
            return {"ok": False, "error": "CODE_USED", "reason": "This code has already been used."}
        if code_row["user_id"] and user_id and str(code_row["user_id"]) != str(user_id):
            return {"ok": False, "error": "CODE_NOT_YOURS", "reason": "This code is registered to another member."}
    if not _scope_ok(promo, kind, product_id):
        return {"ok": False, "error": "NOT_ELIGIBLE_SCOPE",
                "reason": "This code doesn't apply to what you're buying."}
    if promo["min_spend_minor"] and int(amount_minor or 0) < int(promo["min_spend_minor"]):
        return {"ok": False, "error": "MIN_SPEND",
                "reason": "Spend a little more to use this code."}
    if promo["first_time_only"] and _has_prior_purchase(session, club_id, user_id, kind):
        return {"ok": False, "error": "NOT_FIRST_TIME", "reason": "This code is for first-time purchases."}
    g, u = _redemption_counts(session, promo["id"], user_id)
    if promo["max_redemptions"] is not None and g >= int(promo["max_redemptions"]):
        return {"ok": False, "error": "LIMIT_REACHED", "reason": "This promotion has been fully claimed."}
    # Per-customer cap applies to a SHARED code; a unique code is single-use via its own cap above.
    if user_id and code_row is None and u >= int(promo["per_customer_cap"] or 1):
        return {"ok": False, "error": "ALREADY_USED", "reason": "You've already used this code."}
    if _is_bonus(promo):
        if int(promo["bonus_qty"] or 0) <= 0:
            return {"ok": False, "error": "NO_BONUS", "reason": "This offer isn't configured."}
        return {"ok": True, "discount_minor": 0, "is_bonus": True}
    disc = _compute_discount(promo, amount_minor)
    if disc <= 0:
        return {"ok": False, "error": "NO_DISCOUNT", "reason": "Nothing to discount on this order."}
    return {"ok": True, "discount_minor": disc, "is_bonus": False}


# ---------------------------------------------------------------------------
# Public: validate (preview, no write) + apply (to a real order)
# ---------------------------------------------------------------------------

def validate(session, *, club_id, code, applies_to="all", amount_minor=0, product_id=None, user_id=None):
    """Preview a code against an intended purchase (scope + amount) WITHOUT writing anything. The
    checkout UI calls this to show "you'll save RX". Returns {ok, discount_minor, label} or
    {ok:False, error, reason}."""
    promo, code_row = _resolve_code(session, club_id, code)
    res = _check(session, club_id, promo, code_row,
                 kind=applies_to, product_id=product_id, amount_minor=amount_minor, user_id=user_id)
    if not res.get("ok"):
        return res
    out = {"ok": True, "discount_minor": res["discount_minor"], "label": promo["name"],
           "promotion_id": str(promo["id"]), "is_bonus": bool(res.get("is_bonus"))}
    if res.get("is_bonus"):
        out["bonus_qty"] = int(promo["bonus_qty"] or 0)
        out["bonus_unit"] = "month" if promo["kind"] == "bonus_period" else "session"
    return out


def apply_to_order(session, *, club_id, code, order_id, user_id=None, actor_user_id=None):
    """Validate a code against a REAL open order and apply it: discount_order + record a redemption +
    emit promo_redeemed. Returns {ok, discount_minor, new_total_minor, promotion_id, label} or
    {ok:False, error, reason}. The order must be open/awaiting_payment (discount_order guards that)."""
    from billing import statement

    promo, code_row = _resolve_code(session, club_id, code)
    if not promo:
        return {"ok": False, "error": "PROMO_NOT_FOUND", "reason": "That code isn't valid."}

    order = session.execute(text(_ORDER_KIND_SQL), {"c": str(club_id), "o": str(order_id)}).mappings().first()
    if not order:
        return {"ok": False, "error": "ORDER_NOT_FOUND", "reason": "Order not found."}
    if order["status"] not in ("open", "awaiting_payment"):
        return {"ok": False, "error": "NOT_OPEN", "reason": "This order can no longer take a code."}

    buyer = user_id or order["user_id"]
    res = _check(session, club_id, promo, code_row, kind=order["kind"], product_id=order["product_id"],
                 amount_minor=order["amount_minor"], user_id=buyer)
    if not res.get("ok"):
        return res
    is_bonus = bool(res.get("is_bonus"))

    # Stacking guard: unless the promo is stackable, refuse if the order already carries ANY promo or an
    # admin discount (order_line.original_amount_minor set once by discount_order).
    if not promo["stackable"]:
        already = session.execute(text(
            "SELECT (EXISTS (SELECT 1 FROM billing.promotion_redemption r "
            "                WHERE r.order_id = :o AND r.status = 'applied')) "
            "    OR (EXISTS (SELECT 1 FROM billing.order_line l "
            "                WHERE l.order_id = :o AND l.original_amount_minor IS NOT NULL))"),
            {"o": str(order_id)}).scalar()
        if already:
            return {"ok": False, "error": "NOT_STACKABLE",
                    "reason": "This order already has a discount."}

    typed = (code_row["code"] if code_row else promo["code"]) or promo["name"]
    dr = None
    applied = 0
    if not is_bonus:
        # Delegate the money move to the ONE discount primitive (pro-rata + coach lockstep + audit).
        dr = statement.discount_order(session, club_id=club_id, order_id=str(order_id),
                                      discount_minor=int(res["discount_minor"]),
                                      reason=f"Promo {typed}", actor_user_id=actor_user_id)
        if isinstance(dr, dict) and dr.get("ok") is False:
            return {"ok": False, "error": dr.get("error") or "DISCOUNT_FAILED",
                    "reason": "Couldn't apply the code to this order."}
        applied = int((dr or {}).get("discount_minor") or res["discount_minor"])

    # Record the redemption. The FRESH insert (RETURNING) gates the one-time side-effects (code usage +
    # the offline bonus grant), so a re-apply to the same order is a clean no-op.
    fresh = None
    try:
        fresh = session.execute(text(
            "INSERT INTO billing.promotion_redemption "
            "  (club_id, promotion_id, order_id, user_id, discount_minor) "
            "VALUES (:c, :p, :o, :u, :d) "
            "ON CONFLICT (promotion_id, order_id) DO NOTHING RETURNING id"),
            {"c": str(club_id), "p": str(promo["id"]), "o": str(order_id),
             "u": str(buyer) if buyer else None, "d": applied}).first()
    except Exception:
        log.exception("promotions: redemption insert failed for order %s", order_id)

    if fresh:
        if code_row is not None:
            session.execute(text(
                "UPDATE billing.promotion_code SET used_count = used_count + 1 WHERE id = :id"),
                {"id": str(code_row["id"])})
        # bonus_period on a membership: if the linked sub is ALREADY active (an OFFLINE buy), extend it
        # now by the bonus months. If it's still pending (ONLINE, awaiting payment), the membership
        # activation path adds the bonus later via _bonus_months_for_order — so it's never double-granted.
        if promo["kind"] == "bonus_period" and order["kind"] == "membership":
            bq = int(promo["bonus_qty"] or 0)
            if bq > 0:
                session.execute(text(
                    "UPDATE billing.membership_subscription "
                    "SET current_period_end = (GREATEST(COALESCE(current_period_end, CURRENT_DATE), "
                    "     CURRENT_DATE) + make_interval(months => :bq))::date, updated_at = now() "
                    "WHERE order_id = :o AND status = 'active'"),
                    {"bq": bq, "o": str(order_id)})
        # bonus_units on a pack: if the wallet is ALREADY active (OFFLINE buy), add the free sessions now
        # via adjust_wallet. If it's still 'pending' (ONLINE), the pack grant adds them at activation via
        # _bonus_units_for_order — never double-granted.
        elif promo["kind"] == "bonus_units" and order["kind"] == "pack":
            bq = int(promo["bonus_qty"] or 0)
            if bq > 0:
                w = session.execute(text(
                    "SELECT id, base_minutes, status FROM billing.token_wallet "
                    "WHERE order_id = :o ORDER BY created_at LIMIT 1"),
                    {"o": str(order_id)}).mappings().first()
                if w and w["status"] != "pending":
                    try:
                        from billing import bundles
                        bundles.adjust_wallet(session, club_id=club_id, wallet_id=w["id"],
                                              delta_minutes=bq * int(w["base_minutes"] or 60),
                                              reason="Promo bonus sessions", actor_user_id=actor_user_id)
                    except Exception:
                        log.exception("promotions: bonus_units offline grant failed for order %s", order_id)

    # Marketing funnel: promo_redeemed → usage_event + Klaviyo (measures which campaign drove it).
    try:
        from marketing_crm.tracking import emit
        emit("promo_redeemed", {"club_id": str(club_id), "user_id": str(buyer) if buyer else None,
                                "code": typed, "promotion": promo["name"],
                                "discount_minor": applied, "scope": order["kind"],
                                "is_bonus": is_bonus})
    except Exception:
        log.exception("promotions: emit failed (non-fatal)")

    out = {"ok": True, "discount_minor": applied, "promotion_id": str(promo["id"]),
           "label": promo["name"], "is_bonus": is_bonus}
    if is_bonus:
        out["bonus_qty"] = int(promo["bonus_qty"] or 0)
        out["bonus_unit"] = "month" if promo["kind"] == "bonus_period" else "session"
        out["new_total_minor"] = int(order["amount_minor"] or 0)   # bonus doesn't change the price
    else:
        out["new_total_minor"] = int((dr or {}).get("new_total_minor") or 0)
    return out


def reverse_for_order(session, order_id) -> int:
    """Mark an order's applied redemptions 'reversed' (frees the usage slot) — call on refund/void.
    Returns the number reversed. Best-effort; never raises."""
    try:
        r = session.execute(text(
            "UPDATE billing.promotion_redemption SET status = 'reversed' "
            "WHERE order_id = :o AND status = 'applied'"), {"o": str(order_id)})
        return r.rowcount or 0
    except Exception:
        log.exception("promotions: reverse_for_order failed for %s", order_id)
        return 0


# ---------------------------------------------------------------------------
# Admin CRUD
# ---------------------------------------------------------------------------

_EDITABLE = ("code", "name", "description", "kind", "percent_bps", "value_minor", "bonus_qty",
             "applies_to", "product_id", "min_spend_minor", "first_time_only", "max_redemptions",
             "per_customer_cap", "stackable", "starts_at", "ends_at", "status")


def _clean(fields: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: fields[k] for k in _EDITABLE if k in fields}
    if "code" in out:
        out["code"] = (str(out["code"]).strip() or None) if out["code"] is not None else None
    return out


def create(session, *, club_id, created_by=None, **fields):
    f = _clean(fields)
    if not (f.get("name") or "").strip():
        return {"ok": False, "error": "NAME_REQUIRED"}
    f.setdefault("kind", "percent_off")
    f.setdefault("applies_to", "all")
    cols = ["club_id", "created_by"] + list(f.keys())
    vals = {"club_id": str(club_id), "created_by": str(created_by) if created_by else None}
    vals.update(f)
    placeholders = ", ".join(":" + c for c in cols)
    try:
        row = session.execute(text(
            f'INSERT INTO billing.promotion ({", ".join(cols)}) VALUES ({placeholders}) RETURNING id'),
            vals).mappings().first()
    except Exception as e:
        log.exception("promotions.create failed")
        return {"ok": False, "error": "DUPLICATE_CODE" if "uq_promotion_code" in str(e) else "CREATE_FAILED"}
    return {"ok": True, "id": str(row["id"])}


def update(session, *, club_id, promo_id, **fields):
    f = _clean(fields)
    if not f:
        return {"ok": False, "error": "NO_CHANGES"}
    sets = ", ".join(f"{k} = :{k}" for k in f) + ", updated_at = now()"
    params = {"c": str(club_id), "id": str(promo_id)}
    params.update(f)
    try:
        r = session.execute(text(
            f"UPDATE billing.promotion SET {sets} WHERE club_id = :c AND id = :id"), params)
    except Exception as e:
        log.exception("promotions.update failed")
        return {"ok": False, "error": "DUPLICATE_CODE" if "uq_promotion_code" in str(e) else "UPDATE_FAILED"}
    return {"ok": bool(r.rowcount), "error": None if r.rowcount else "NOT_FOUND"}


def set_status(session, *, club_id, promo_id, status):
    if status not in ("active", "paused", "archived"):
        return {"ok": False, "error": "BAD_STATUS"}
    r = session.execute(text(
        "UPDATE billing.promotion SET status = :s, updated_at = now() WHERE club_id = :c AND id = :id"),
        {"s": status, "c": str(club_id), "id": str(promo_id)})
    return {"ok": bool(r.rowcount), "error": None if r.rowcount else "NOT_FOUND"}


def list_promotions(session, *, club_id):
    """All non-archived promos + their live redemption count and total discounted."""
    rows = session.execute(text(
        "SELECT pr.*, "
        "  (SELECT count(*) FROM billing.promotion_redemption r "
        "     WHERE r.promotion_id = pr.id AND r.status = 'applied') AS redemptions, "
        "  (SELECT COALESCE(sum(r.discount_minor),0) FROM billing.promotion_redemption r "
        "     WHERE r.promotion_id = pr.id AND r.status = 'applied') AS discounted_minor "
        "FROM billing.promotion pr "
        "WHERE pr.club_id = :c AND pr.status <> 'archived' ORDER BY pr.created_at DESC"),
        {"c": str(club_id)}).mappings().all()
    return [dict(r) for r in rows]


def get(session, *, club_id, promo_id):
    row = session.execute(text(
        "SELECT * FROM billing.promotion WHERE club_id = :c AND id = :id"),
        {"c": str(club_id), "id": str(promo_id)}).mappings().first()
    return dict(row) if row else None


def list_redemptions(session, *, club_id, promo_id, limit=200):
    rows = session.execute(text(
        "SELECT r.id, r.order_id, r.user_id, r.discount_minor, r.status, r.redeemed_at, "
        "       u.first_name, u.surname, u.email "
        "FROM billing.promotion_redemption r "
        "LEFT JOIN iam.user u ON u.id = r.user_id "
        "WHERE r.club_id = :c AND r.promotion_id = :p "
        "ORDER BY r.redeemed_at DESC LIMIT :lim"),
        {"c": str(club_id), "p": str(promo_id), "lim": int(limit)}).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Unique per-recipient codes (Phase 2) — mint a batch, list them, revoke one.
# A campaign mints one code per member, embeds each as a Klaviyo profile property, and the code
# redeems exactly once. Codes are unguessable (secrets) + unique per club.
# ---------------------------------------------------------------------------

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no ambiguous 0/O/1/I


def _mint_code_str(prefix, n=6):
    import secrets
    body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(n))
    pfx = "".join(ch for ch in (prefix or "").upper() if ch.isalnum())[:12]
    return (pfx + "-" + body) if pfx else body


def generate_codes(session, *, club_id, promo_id, count=1, prefix=None, max_uses=1):
    """Mint `count` unique single-use codes for a promotion. Returns {ok, codes:[...]}. Codes are
    unique per club (retries on the rare collision). The promotion should have NO shared `code`."""
    promo = get(session, club_id=club_id, promo_id=promo_id)
    if not promo:
        return {"ok": False, "error": "NOT_FOUND"}
    count = max(1, min(int(count or 1), 2000))
    out = []
    for _ in range(count):
        for _attempt in range(6):
            code = _mint_code_str(prefix)
            row = session.execute(text(
                "INSERT INTO billing.promotion_code (club_id, promotion_id, code, max_uses) "
                "VALUES (:c, :p, :code, :mu) "
                "ON CONFLICT (club_id, lower(code)) DO NOTHING RETURNING code"),
                {"c": str(club_id), "p": str(promo_id), "code": code, "mu": int(max_uses or 1)}).first()
            if row:
                out.append(row[0]); break
    return {"ok": True, "codes": out, "count": len(out)}


def list_codes(session, *, club_id, promo_id, limit=2000):
    rows = session.execute(text(
        "SELECT code, user_id, max_uses, used_count, status, created_at "
        "FROM billing.promotion_code WHERE club_id = :c AND promotion_id = :p "
        "ORDER BY created_at DESC LIMIT :lim"),
        {"c": str(club_id), "p": str(promo_id), "lim": int(limit)}).mappings().all()
    return [dict(r) for r in rows]


def revoke_code(session, *, club_id, code):
    r = session.execute(text(
        "UPDATE billing.promotion_code SET status = 'revoked' "
        "WHERE club_id = :c AND lower(code) = lower(:code)"),
        {"c": str(club_id), "code": str(code).strip()})
    return {"ok": bool(r.rowcount)}

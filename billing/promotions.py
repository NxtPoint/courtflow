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

def _find_by_code(session, club_id, code):
    if not code:
        return None
    return session.execute(text(
        "SELECT * FROM billing.promotion "
        "WHERE club_id = :c AND lower(code) = lower(:code) AND status <> 'archived' "
        "ORDER BY created_at DESC LIMIT 1"),
        {"c": str(club_id), "code": str(code).strip()},
    ).mappings().first()


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


def _check(session, club_id, promo, *, kind, product_id, amount_minor, user_id, now_sql="now()"):
    """Shared eligibility → {ok, discount_minor} or {ok:False, error, reason}. Does NOT check stacking
    (that needs the order); apply_to_order adds it."""
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
    if user_id and u >= int(promo["per_customer_cap"] or 1):
        return {"ok": False, "error": "ALREADY_USED", "reason": "You've already used this code."}
    disc = _compute_discount(promo, amount_minor)
    if disc <= 0:
        return {"ok": False, "error": "NO_DISCOUNT", "reason": "Nothing to discount on this order."}
    return {"ok": True, "discount_minor": disc}


# ---------------------------------------------------------------------------
# Public: validate (preview, no write) + apply (to a real order)
# ---------------------------------------------------------------------------

def validate(session, *, club_id, code, applies_to="all", amount_minor=0, product_id=None, user_id=None):
    """Preview a code against an intended purchase (scope + amount) WITHOUT writing anything. The
    checkout UI calls this to show "you'll save RX". Returns {ok, discount_minor, label} or
    {ok:False, error, reason}."""
    promo = _find_by_code(session, club_id, code)
    res = _check(session, club_id, promo,
                 kind=applies_to, product_id=product_id, amount_minor=amount_minor, user_id=user_id)
    if not res.get("ok"):
        return res
    return {"ok": True, "discount_minor": res["discount_minor"],
            "label": promo["name"], "promotion_id": str(promo["id"])}


def apply_to_order(session, *, club_id, code, order_id, user_id=None, actor_user_id=None):
    """Validate a code against a REAL open order and apply it: discount_order + record a redemption +
    emit promo_redeemed. Returns {ok, discount_minor, new_total_minor, promotion_id, label} or
    {ok:False, error, reason}. The order must be open/awaiting_payment (discount_order guards that)."""
    from billing import statement

    promo = _find_by_code(session, club_id, code)
    if not promo:
        return {"ok": False, "error": "PROMO_NOT_FOUND", "reason": "That code isn't valid."}

    order = session.execute(text(_ORDER_KIND_SQL), {"c": str(club_id), "o": str(order_id)}).mappings().first()
    if not order:
        return {"ok": False, "error": "ORDER_NOT_FOUND", "reason": "Order not found."}
    if order["status"] not in ("open", "awaiting_payment"):
        return {"ok": False, "error": "NOT_OPEN", "reason": "This order can no longer take a code."}

    res = _check(session, club_id, promo, kind=order["kind"], product_id=order["product_id"],
                 amount_minor=order["amount_minor"], user_id=(user_id or order["user_id"]))
    if not res.get("ok"):
        return res

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

    # Delegate the money move to the ONE discount primitive (pro-rata + coach lockstep + audit).
    dr = statement.discount_order(session, club_id=club_id, order_id=str(order_id),
                                  discount_minor=int(res["discount_minor"]),
                                  reason=f"Promo {promo['code'] or promo['name']}",
                                  actor_user_id=actor_user_id)
    if isinstance(dr, dict) and dr.get("ok") is False:
        return {"ok": False, "error": dr.get("error") or "DISCOUNT_FAILED",
                "reason": "Couldn't apply the code to this order."}
    applied = int((dr or {}).get("discount_minor") or res["discount_minor"])

    # Record the redemption (unique on (promotion, order) — a re-apply to the same order is a no-op error).
    try:
        session.execute(text(
            "INSERT INTO billing.promotion_redemption "
            "  (club_id, promotion_id, order_id, user_id, discount_minor) "
            "VALUES (:c, :p, :o, :u, :d) "
            "ON CONFLICT (promotion_id, order_id) DO NOTHING"),
            {"c": str(club_id), "p": str(promo["id"]), "o": str(order_id),
             "u": str(user_id or order["user_id"]) if (user_id or order["user_id"]) else None,
             "d": applied})
    except Exception:
        log.exception("promotions: redemption insert failed for order %s", order_id)

    # Marketing funnel: promo_redeemed → usage_event + Klaviyo (measures which campaign drove it).
    try:
        from marketing_crm.tracking import emit
        buyer = user_id or order["user_id"]
        emit("promo_redeemed", {"club_id": str(club_id), "user_id": str(buyer) if buyer else None,
                                "code": promo["code"], "promotion": promo["name"],
                                "discount_minor": applied, "scope": order["kind"]})
    except Exception:
        log.exception("promotions: emit failed (non-fatal)")

    return {"ok": True, "discount_minor": applied,
            "new_total_minor": int((dr or {}).get("new_total_minor") or 0),
            "promotion_id": str(promo["id"]), "label": promo["name"]}


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

_EDITABLE = ("code", "name", "description", "kind", "percent_bps", "value_minor", "applies_to",
             "product_id", "min_spend_minor", "first_time_only", "max_redemptions",
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

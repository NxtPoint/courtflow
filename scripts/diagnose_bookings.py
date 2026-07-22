# scripts/diagnose_bookings.py — READ-ONLY production diagnostic for the two booking symptoms the
# owner reported. Writes NOTHING, ever: no --commit flag exists, every statement is a SELECT.
#
#   S1  "class bookings come through as confirmed/booked but payment was NOT made"
#         -> an enrolment (or booking) sitting in a CONFIRMED-looking state while its billing.order
#            is still 'awaiting_payment'. Also surfaces seats whose hold has EXPIRED but which were
#            never released (lazy expiry never ran because nothing read that session).
#   S2  "a service set to ONLINE came back as pay-at-court"
#         -> an order whose settlement_mode is NOT one of its service's configured payment_modes.
#            Names WHO created each one and their ROLE, so a legitimate staff override is instantly
#            distinguishable from a real leak.
#
#   Run on the Render shell (DATABASE_URL already set):
#       python -m scripts.diagnose_bookings
#       python -m scripts.diagnose_bookings --days 90     # widen the window (default 60)
#       python -m scripts.diagnose_bookings --all         # no date window at all
#
# Locally it falls back to .env.local like the other scripts. Every check is independently guarded —
# a column that doesn't exist on an older DB reports "skipped", it never kills the run.

import argparse
import os
import sys
from pathlib import Path


def _load_env_local():
    if os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("DB_URL"):
        return
    f = Path(__file__).resolve().parent.parent / ".env.local"
    if not f.exists():
        print("!! No DATABASE_URL in env and no .env.local found. Run this on the Render shell.")
        sys.exit(2)
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _hdr(t):
    print("\n" + "=" * 78 + "\n== " + t + "\n" + "=" * 78)


def _rows(session, label, sql, params, empty_msg, cols):
    """Run one guarded read. Prints a compact table. Returns the row count (-1 = skipped)."""
    from sqlalchemy import text
    try:
        rows = session.execute(text(sql), params).mappings().all()
    except Exception as e:
        print("   [skipped] %s — %s" % (label, e.__class__.__name__))
        return -1
    if not rows:
        print("   OK  %s — %s" % (label, empty_msg))
        return 0
    print("   !!  %s — %d row(s):" % (label, len(rows)))
    print("       " + " | ".join(cols))
    print("       " + "-" * 70)
    for r in rows[:40]:
        print("       " + " | ".join("" if r.get(c) is None else str(r.get(c))[:28] for c in cols))
    if len(rows) > 40:
        print("       … +%d more" % (len(rows) - 40))
    return len(rows)


# The MONEY settlement modes — the only ones a service's payment_modes governs. 'membership_covered',
# 'token' and 'free' are COVERAGE outcomes resolved server-side, not payment choices, so comparing
# them against payment_modes would produce pure false positives.
_MONEY_MODES = "('online','at_court','monthly_account')"

_WINDOW = "AND {col} >= now() - make_interval(days => :days)"


def main():
    ap = argparse.ArgumentParser(description="Read-only diagnostic for the booking payment symptoms.")
    ap.add_argument("--days", type=int, default=60, help="how far back to look (default 60)")
    ap.add_argument("--all", action="store_true", help="no date window")
    args = ap.parse_args()
    _load_env_local()
    import db

    days = args.days
    win = (lambda col: "" if args.all else _WINDOW.format(col=col))
    p = {"days": days}
    total = 0

    with db.session_scope() as s:
        _hdr("S1 — CONFIRMED-LOOKING BUT UNPAID")

        total += max(0, _rows(s, "class seats 'enrolled' while the order is awaiting_payment", """
            SELECT e.id AS enrolment_id, cs.starts_at, u.email AS member, e.status AS seat,
                   o.status AS order_status, o.settlement_mode AS mode,
                   (o.amount_minor / 100.0) AS amount, e.held_until
            FROM diary.enrolment e
            JOIN diary.class_session cs ON cs.id = e.class_session_id
            LEFT JOIN iam."user" u ON u.id = e.user_id
            JOIN billing."order" o ON o.id = e.order_id
            WHERE e.status IN ('enrolled', 'attended')
              AND o.status = 'awaiting_payment'
              """ + win("cs.starts_at") + """
            ORDER BY cs.starts_at DESC
        """, p, "none — no unpaid class seat is showing as enrolled",
            ["enrolment_id", "starts_at", "member", "seat", "order_status", "mode", "amount", "held_until"]))

        total += max(0, _rows(s, "class seats whose HOLD EXPIRED but were never released", """
            SELECT e.id AS enrolment_id, cs.starts_at, u.email AS member, e.status AS seat,
                   e.held_until, o.status AS order_status
            FROM diary.enrolment e
            JOIN diary.class_session cs ON cs.id = e.class_session_id
            LEFT JOIN iam."user" u ON u.id = e.user_id
            LEFT JOIN billing."order" o ON o.id = e.order_id
            WHERE e.status = 'enrolled' AND e.held_until IS NOT NULL AND e.held_until < now()
              """ + win("cs.starts_at") + """
            ORDER BY e.held_until DESC
        """, p, "none — lazy expiry is keeping up",
            ["enrolment_id", "starts_at", "member", "seat", "held_until", "order_status"]))

        total += max(0, _rows(s, "court/lesson bookings CONFIRMED while the order is awaiting_payment", """
            SELECT b.id AS booking_id, b.booking_type, b.starts_at, b.status AS booking_status,
                   o.status AS order_status, o.settlement_mode AS mode,
                   (o.amount_minor / 100.0) AS amount, u.email AS client
            FROM diary.booking b
            JOIN billing."order" o ON o.id = b.order_id
            LEFT JOIN iam."user" u ON u.id = b.booked_by_user_id
            WHERE b.status = 'confirmed' AND o.status = 'awaiting_payment'
              """ + win("b.starts_at") + """
            ORDER BY b.starts_at DESC
        """, p, "none — no confirmed booking has an unpaid online order",
            ["booking_id", "booking_type", "starts_at", "booking_status", "order_status", "mode", "amount", "client"]))

        total += max(0, _rows(s, "ORPHANED unpaid orders (booking cancelled/expired, order still open)", """
            SELECT o.id AS order_id, o.status AS order_status, o.settlement_mode AS mode,
                   (o.amount_minor / 100.0) AS amount, b.status AS booking_status,
                   b.booking_type, b.starts_at, u.email AS client
            FROM billing."order" o
            JOIN billing.order_line ol ON ol.order_id = o.id
            JOIN diary.booking b ON b.id = ol.booking_id
            LEFT JOIN iam."user" u ON u.id = o.user_id
            WHERE o.status IN ('open', 'awaiting_payment')
              AND b.status IN ('cancelled', 'expired')
              """ + win("b.starts_at") + """
            ORDER BY b.starts_at DESC
        """, p, "none — cancelled bookings are voiding their orders",
            ["order_id", "order_status", "mode", "amount", "booking_status", "booking_type", "starts_at", "client"]))

        _hdr("S2 — SETTLEMENT MODE vs THE SERVICE'S CONFIGURED PAYMENT OPTIONS")
        print("   (payment_modes is a CSV on billing.product. Only the MONEY modes are compared —")
        print("    membership_covered / token / free are coverage outcomes, not payment choices.)")

        # RETROACTIVE NOISE IS THE TRAP HERE: this compares TODAY's payment_modes against bookings
        # made at any time, so every legitimate at-court booking taken BEFORE an owner switched the
        # service to online-only shows up as a "violation". `when_booked` (b.created_at, not
        # starts_at — the session date tells you nothing about when the rule applied) and
        # `service_changed` (the product row's last edit) let you separate the two: only rows where
        # when_booked > service_changed are candidate REAL bypasses. Roles are aggregated in a
        # scalar subquery because a person can hold several iam.membership rows (member AND coach),
        # which previously duplicated every one of their bookings.
        total += max(0, _rows(s, "BOOKINGS whose settlement mode is NOT offered by their service", """
            SELECT b.id AS booking_id, b.booking_type,
                   b.created_at AS when_booked, pr.updated_at AS service_changed,
                   CASE WHEN b.created_at > pr.updated_at THEN 'REAL?' ELSE 'pre-change' END AS verdict,
                   pr.name AS service, pr.payment_modes AS allowed, o.settlement_mode AS used,
                   o.status AS order_status,
                   client.email AS client, actor.email AS booked_by,
                   COALESCE((SELECT string_agg(DISTINCT mem.role, '/')
                               FROM iam.membership mem
                              WHERE mem.club_id = b.club_id
                                AND mem.user_id = b.created_by_user_id), 'unknown') AS booked_by_role
            FROM diary.booking b
            JOIN billing."order" o ON o.id = b.order_id
            JOIN LATERAL (SELECT price_id FROM billing.order_line
                           WHERE order_id = o.id AND price_id IS NOT NULL
                           ORDER BY created_at LIMIT 1) ol ON true
            JOIN billing.price p ON p.id = ol.price_id
            JOIN billing.product pr ON pr.id = p.product_id
            LEFT JOIN iam."user" client ON client.id = b.booked_by_user_id
            LEFT JOIN iam."user" actor ON actor.id = b.created_by_user_id
            WHERE pr.payment_modes IS NOT NULL AND btrim(pr.payment_modes) <> ''
              AND o.settlement_mode IN """ + _MONEY_MODES + """
              AND NOT (o.settlement_mode = ANY(
                       string_to_array(replace(pr.payment_modes, ' ', ''), ',')))
              AND b.created_at > pr.updated_at        -- ONLY post-config-change bookings
              """ + win("b.created_at") + """
            ORDER BY b.created_at DESC
        """, p, "none — every booking made SINCE its service was configured used an allowed mode",
            ["booking_id", "booking_type", "when_booked", "service", "allowed", "used",
             "order_status", "client", "booked_by", "booked_by_role"]))

        # The same comparison WITHOUT the post-change filter, counted only — so you can see how much
        # of the raw number is retroactive noise rather than a live leak.
        _rows(s, "…of which are PRE-CONFIG-CHANGE (booked when the mode was still allowed)", """
            SELECT count(*) AS retroactive_rows,
                   count(*) FILTER (WHERE b.created_at > pr.updated_at) AS real_candidates
            FROM diary.booking b
            JOIN billing."order" o ON o.id = b.order_id
            JOIN LATERAL (SELECT price_id FROM billing.order_line
                           WHERE order_id = o.id AND price_id IS NOT NULL
                           ORDER BY created_at LIMIT 1) ol ON true
            JOIN billing.price p ON p.id = ol.price_id
            JOIN billing.product pr ON pr.id = p.product_id
            WHERE pr.payment_modes IS NOT NULL AND btrim(pr.payment_modes) <> ''
              AND o.settlement_mode IN """ + _MONEY_MODES + """
              AND NOT (o.settlement_mode = ANY(
                       string_to_array(replace(pr.payment_modes, ' ', ''), ',')))
        """, {}, "nothing to split", ["retroactive_rows", "real_candidates"])

        total += max(0, _rows(s, "CLASS ENROLMENTS whose settlement mode is NOT offered by their service", """
            SELECT e.id AS enrolment_id, cs.starts_at, r.name AS class_name,
                   pr.name AS service, pr.payment_modes AS allowed,
                   e.settlement_mode AS used, o.status AS order_status, u.email AS member
            FROM diary.enrolment e
            JOIN diary.class_session cs ON cs.id = e.class_session_id
            LEFT JOIN diary.resource r ON r.id = cs.resource_id
            LEFT JOIN iam."user" u ON u.id = e.user_id
            LEFT JOIN billing."order" o ON o.id = e.order_id
            JOIN LATERAL (SELECT price_id FROM billing.order_line
                           WHERE order_id = e.order_id AND price_id IS NOT NULL
                           ORDER BY created_at LIMIT 1) ol ON true
            JOIN billing.price p ON p.id = ol.price_id
            JOIN billing.product pr ON pr.id = p.product_id
            WHERE e.status IN ('enrolled', 'attended')
              AND pr.payment_modes IS NOT NULL AND btrim(pr.payment_modes) <> ''
              AND e.settlement_mode IN """ + _MONEY_MODES + """
              AND NOT (e.settlement_mode = ANY(
                       string_to_array(replace(pr.payment_modes, ' ', ''), ',')))
              """ + win("cs.starts_at") + """
            ORDER BY cs.starts_at DESC
        """, p, "none — every enrolment used a mode its class service offers",
            ["enrolment_id", "starts_at", "class_name", "service", "allowed", "used", "order_status", "member"]))

        _hdr("CONTEXT — how each service is currently configured")
        _rows(s, "services with a payment-mode restriction", """
            SELECT pr.kind, pr.name AS service, pr.payment_modes AS allowed,
                   COALESCE(c.display_name, 'shared / no coach') AS coach
            FROM billing.product pr
            LEFT JOIN iam.coach_profile c ON c.user_id = pr.coach_user_id AND c.club_id = pr.club_id
            WHERE pr.payment_modes IS NOT NULL AND btrim(pr.payment_modes) <> ''
            ORDER BY pr.kind, pr.name
        """, {}, "no service restricts its payment modes (everything allows the club defaults)",
            ["kind", "service", "allowed", "coach"])

    _hdr("SUMMARY")
    if total == 0:
        print("   Nothing anomalous found in the window. If you SAW the symptom, re-run with --all\n"
              "   (it may be older than the window) and send me the output either way.")
    else:
        print("   %d anomalous row(s) found above. Send me this output — the 'booked_by_role'\n"
              "   column on the S2 table tells us immediately whether a staff override explains it." % total)
    print()


if __name__ == "__main__":
    main()

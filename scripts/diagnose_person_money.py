# scripts/diagnose_person_money.py — one person's COMPLETE money trail, including what the
# Client-360 record deliberately hides.
#
# WHY: two symptoms get reported over and over and they are the same shape — "the screen and the
# money disagree about this person". They cannot be settled from a screen, because the screen is
# exactly what is under suspicion, and they cannot be settled from a single table, because a debt
# (billing."order"), the thing it was for (diary.booking / diary.enrolment), the cash
# (billing.payment) and the coach's share (billing.commission_split) are four rows that can each
# be right while the set of them is wrong.
#
#   S1  "a cancelled booking does not show on the client's record, but its money shows in
#        Sales by day."
#        -> `client360._bookings` filters `bk.status <> 'cancelled'` UNCONDITIONALLY, while
#           Sales-by-day counts billing.payment rows, which know nothing about booking status.
#           When a cancel VOIDS or REFUNDS the order the two agree and all is well. When a
#           cancellation keeps the money — a late cancel, a cancel after payment with no refund —
#           the payment is real revenue that the client's own record cannot account for, and the
#           fold's Paid total exceeds the events listed under it. THIS SCRIPT NAMES THAT MONEY:
#           see the INVISIBLE MONEY section, which is the whole reason it exists.
#
#   S2  "they paid, I marked it paid, and it still shows as owed."
#        -> almost always a SECOND debt. `record_desk_payment` refuses anything but an exact match
#           on an 'open'/'awaiting_payment' order, so a payment that was accepted DID close the
#           order it named. The one still owed is therefore a different row: a duplicate order, a
#           'Pay all' wrapper whose children were not settled (or a child whose wrapper was), or a
#           per-head order on a semi-private lesson. Printing every order side by side with its
#           lines and payments is what makes that visible in one look.
#
#   It also answers the question that always follows S2 — "did the coach get paid on it?" — by
#   showing, per paid coaching line, whether a billing.commission_split exists and to whom.
#   `reconcile_coach_commission` proves this club-wide; this proves it for the person in front of you.
#
# READ-ONLY. Every statement is a SELECT. There is no --commit because there is nothing to commit.
#
# RUN IT (per docs/specs/DATA-ACCESS.md): Render -> courtflow-api -> Shell:
#     python -m scripts.diagnose_person_money "jonah"
#     python -m scripts.diagnose_person_money drudman07@gmail.com --month 2026-08
#
# The needle matches email, first name or surname, case-insensitively. The CLUB is taken from the
# person that matches, never picked first-by-created_at, so a two-club database cannot silently
# answer about the wrong one.

import argparse
import io
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


def _load_env():
    envf = os.path.join(REPO, ".env.local")
    if os.path.isfile(envf) and not os.getenv("DATABASE_URL"):
        for line in io.open(envf, encoding="utf-8"):
            line = line.strip()
            if line.startswith("DATABASE_URL=") and not line.startswith("#"):
                os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not os.getenv("DATABASE_URL"):
        print("DATABASE_URL is not set. On Render it already is; locally use .env.local.")
        sys.exit(2)


def _r(minor, cur="R"):
    try:
        return "%s%.2f" % (cur, int(minor or 0) / 100.0)
    except Exception:
        return "%s0.00" % cur


def _short(u):
    return str(u)[:8] if u else "-"


def _find_person(s, needle):
    from sqlalchemy import text
    rows = s.execute(text(
        "SELECT DISTINCT u.id, u.email, "
        "       NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.surname)), '') AS name, "
        "       m.club_id, c.name AS club_name "
        "FROM iam.user u "
        "JOIN iam.membership m ON m.user_id = u.id "
        "JOIN club.club c ON c.id = m.club_id "
        "WHERE lower(u.email) LIKE :n OR lower(COALESCE(u.first_name,'')) LIKE :n "
        "   OR lower(COALESCE(u.surname,'')) LIKE :n "
        "ORDER BY name NULLS LAST LIMIT 20"),
        {"n": "%%%s%%" % needle.strip().lower()}).mappings().fetchall()
    return rows


def _orders(s, club_id, user_id, month):
    """Every order this person OWNS. `month` filters on the SERVICE date where one is recorded,
    falling back to created_at, which is how invoicing decides what a month contains."""
    from sqlalchemy import text
    clause = ("AND to_char(COALESCE(o.service_date, o.created_at::date), 'YYYY-MM') = :ym"
              if month else "")
    return s.execute(text(
        'SELECT o.id, o.status, o.amount_minor, o.settlement_mode, o.currency_code, '
        '       o.settled_by_order_id, o.covered_order_ids, o.service_date, o.void_reason, '
        '       (o.created_at AT TIME ZONE \'Africa/Johannesburg\') AS created '
        'FROM billing."order" o '
        'WHERE o.club_id = :c AND o.user_id = :u ' + clause + ' '
        'ORDER BY o.created_at'), {"c": club_id, "u": user_id, "ym": month}).mappings().fetchall()


def _lines(s, order_id):
    from sqlalchemy import text
    return s.execute(text(
        "SELECT ol.id, ol.description, ol.amount_minor, ol.booking_id, ol.enrolment_id "
        "FROM billing.order_line ol WHERE ol.order_id = :o ORDER BY ol.created_at"),
        {"o": order_id}).mappings().fetchall()


def _payments(s, order_id):
    from sqlalchemy import text
    return s.execute(text(
        "SELECT p.id, p.provider, p.direction, p.status, p.amount_minor, "
        "       p.recorded_by_user_id, (p.created_at AT TIME ZONE 'Africa/Johannesburg') AS created "
        "FROM billing.payment p WHERE p.order_id = :o ORDER BY p.created_at"),
        {"o": order_id}).mappings().fetchall()


def section_orders(s, club_id, user_id, month):
    """Every debt, its lines, and every payment against it. S2 is answered by reading this list:
    the order still showing as owed is here, next to the one the payment actually closed."""
    print("\nORDERS  (every debt this person owns%s)" % (" in " + month if month else ""))
    print("-" * 78)
    rows = _orders(s, club_id, user_id, month)
    if not rows:
        print("   none.")
        return rows
    owed_total = paid_total = 0
    for o in rows:
        flag = ""
        if o["settled_by_order_id"]:
            flag = "  <- settled by 'Pay all' wrapper %s" % _short(o["settled_by_order_id"])
        if o["covered_order_ids"]:
            flag = "  <- this IS a 'Pay all' wrapper over %d debt(s)" % len(o["covered_order_ids"])
        print("   %s  %-16s %-10s %10s  %s%s"
              % (_short(o["id"]), str(o["created"])[:16], o["status"],
                 _r(o["amount_minor"]), o["settlement_mode"] or "", flag))
        if o["void_reason"]:
            print("        void reason: %s" % o["void_reason"])
        for ln in _lines(s, o["id"]):
            what = "booking %s" % _short(ln["booking_id"]) if ln["booking_id"] else (
                   "enrolment %s" % _short(ln["enrolment_id"]) if ln["enrolment_id"] else "-")
            print("        line  %-42s %9s  %s"
                  % ((ln["description"] or "")[:42], _r(ln["amount_minor"]), what))
        for p in _payments(s, o["id"]):
            who = ("  recorded by %s" % _short(p["recorded_by_user_id"])) if p["recorded_by_user_id"] else ""
            print("        PAY   %-10s %-9s %-10s %9s  %s%s"
                  % (p["provider"], p["direction"], p["status"], _r(p["amount_minor"]),
                     str(p["created"])[:16], who))
        if o["status"] in ("open", "awaiting_payment"):
            owed_total += int(o["amount_minor"] or 0)
        elif o["status"] == "paid":
            paid_total += int(o["amount_minor"] or 0)
    print("\n   paid %s   still owed %s" % (_r(paid_total), _r(owed_total)))
    if owed_total:
        print("   ^ If they have paid and something is still owed, the debt above is a DIFFERENT")
        print("     row from the one the payment closed. record_desk_payment only accepts an exact")
        print("     amount on an open order, so a payment that went through DID close its order.")
    return rows


def section_bookings(s, club_id, user_id, month):
    """Every booking INCLUDING cancelled, marked with whether the Client-360 record shows it.

    This is the S1 answer. The record's query ends `AND bk.status <> 'cancelled'`, so a cancelled
    booking vanishes from the person's own history whether or not its money was given back."""
    from sqlalchemy import text
    clause = "AND to_char(bk.starts_at, 'YYYY-MM') = :ym" if month else ""
    rows = s.execute(text(
        "SELECT bk.id, bk.booking_type, bk.status, bk.notes, "
        "       (bk.starts_at AT TIME ZONE 'Africa/Johannesburg') AS starts, "
        "       (SELECT COALESCE(SUM(ol.amount_minor),0) FROM billing.order_line ol "
        "          JOIN billing.\"order\" ou ON ou.id = ol.order_id "
        "         WHERE ol.booking_id = bk.id AND ou.user_id = :u) AS amount_minor, "
        "       (SELECT o2.status FROM billing.\"order\" o2 "
        "         WHERE o2.id IN (SELECT ol.order_id FROM billing.order_line ol "
        "                          WHERE ol.booking_id = bk.id) "
        "           AND o2.user_id = :u ORDER BY o2.created_at LIMIT 1) AS order_status, "
        "       (SELECT COALESCE(SUM(CASE WHEN p.direction='charge' THEN p.amount_minor "
        "                                 ELSE -p.amount_minor END),0) "
        "          FROM billing.payment p "
        "         WHERE p.order_id IN (SELECT ol.order_id FROM billing.order_line ol "
        "                               WHERE ol.booking_id = bk.id) "
        "           AND p.status IN ('succeeded','refunded')) AS net_paid_minor "
        "FROM diary.booking bk "
        "WHERE bk.club_id = :c "
        "  AND (bk.booked_by_user_id = :u "
        "       OR EXISTS(SELECT 1 FROM diary.booking_party bp WHERE bp.booking_id = bk.id "
        "                   AND bp.user_id = :u AND bp.party_role <> 'guest')) "
        + clause + " ORDER BY bk.starts_at"),
        {"c": club_id, "u": user_id, "ym": month}).mappings().fetchall()

    print("\nBOOKINGS  (INCLUDING cancelled - the client record shows only the ones marked 'yes')")
    print("-" * 78)
    if not rows:
        print("   none.")
        return []
    print("   %-17s%-8s%-12s%-13s%10s%10s  %s"
          % ("when (SAST)", "type", "booking", "order", "billed", "net paid", "on record?"))
    hidden_money = []
    for b in rows:
        phantom = (b["booking_type"] == "court" and (b["notes"] or "") == "(court held for lesson)")
        shown = b["status"] != "cancelled" and not phantom
        why = "yes" if shown else ("no - phantom court row" if phantom else "NO - cancelled")
        print("   %-17s%-8s%-12s%-13s%10s%10s  %s"
              % (str(b["starts"])[:16], b["booking_type"], b["status"],
                 b["order_status"] or "-", _r(b["amount_minor"]), _r(b["net_paid_minor"]), why))
        if not shown and not phantom and int(b["net_paid_minor"] or 0) > 0:
            hidden_money.append(b)

    print("\nINVISIBLE MONEY  (money kept on a booking the client's record does not show)")
    print("-" * 78)
    if not hidden_money:
        print("   None. Every cancelled booking either had no money on it or was refunded/voided,")
        print("   so the record and the sales figures agree.")
    else:
        tot = sum(int(b["net_paid_minor"] or 0) for b in hidden_money)
        for b in hidden_money:
            print("   %-17s%-8s cancelled, but %s was collected and NOT returned"
                  % (str(b["starts"])[:16], b["booking_type"], _r(b["net_paid_minor"])))
        print("\n   %s is real revenue - it is in Sales by day and in this person's Paid total," % _r(tot))
        print("   but no row on their record accounts for it, so the page cannot be reconciled by")
        print("   eye. Either the cancellation should have refunded (money owed back), or the")
        print("   record should show cancelled-but-charged bookings. It is a display gap, not a")
        print("   missing payment: the money is banked either way.")
    return rows


def section_shared_bookings(s, club_id, user_id, month):
    """EVERY order on every booking this person appears on - INCLUDING orders owned by someone else.

    This section exists because its absence produced a confidently wrong answer. `section_orders`
    filters `o.user_id = :u`, which is the right question for "what does this person owe" and the
    WRONG one for "why does this say paid over here and owed over there". A lesson can carry more
    than one order -- semi-private bills PER HEAD, a dependent's head bills the GUARDIAN, and a
    booking made on someone's behalf raises the debt against the payer -- so the money that settled
    a booking routinely belongs to a different user from the one you are looking at.

    Read this way: a booking with ONE order is ordinary. A booking with TWO orders at the SAME
    amount, one paid and one open, is a phantom debt -- the money is banked, the coach has accrued,
    and the open row is the one that should never have been raised."""
    from sqlalchemy import text
    clause = "AND to_char(bk.starts_at, 'YYYY-MM') = :ym" if month else ""
    rows = s.execute(text(
        "WITH mine AS ("
        "  SELECT DISTINCT bk.id, bk.starts_at, bk.booking_type, bk.status "
        "  FROM diary.booking bk "
        "  WHERE bk.club_id = :c "
        "    AND (bk.booked_by_user_id = :u "
        "         OR EXISTS(SELECT 1 FROM diary.booking_party bp "
        "                    WHERE bp.booking_id = bk.id AND bp.user_id = :u) "
        "         OR EXISTS(SELECT 1 FROM billing.order_line ol JOIN billing.\"order\" o2 "
        "                     ON o2.id = ol.order_id "
        "                    WHERE ol.booking_id = bk.id AND o2.user_id = :u)) "
        + clause + ") "
        "SELECT m.id AS booking_id, "
        "       (m.starts_at AT TIME ZONE 'Africa/Johannesburg') AS starts, "
        "       m.booking_type, m.status AS booking_status, "
        "       o.id AS order_id, o.status AS order_status, o.amount_minor, o.user_id AS owner_id, "
        "       NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.surname)), '') AS owner_name, u.email "
        "FROM mine m "
        "JOIN billing.order_line ol ON ol.booking_id = m.id "
        "JOIN billing.\"order\" o ON o.id = ol.order_id "
        "LEFT JOIN iam.user u ON u.id = o.user_id "
        "ORDER BY m.starts_at, o.created_at"),
        {"c": club_id, "u": user_id, "ym": month}).mappings().fetchall()

    print("\nWHO PAID FOR WHAT  (every order on every booking they appear on, WHOEVER owns it)")
    print("-" * 78)
    if not rows:
        print("   none.")
        return
    by_booking = {}
    for r in rows:
        by_booking.setdefault(r["booking_id"], []).append(r)
    phantom = []
    for bid, orders in by_booking.items():
        head = orders[0]
        print("   %s  %-7s %-10s  %d order(s)"
              % (str(head["starts"])[:16], head["booking_type"], head["booking_status"], len(orders)))
        for o in orders:
            mine = " (this person)" if str(o["owner_id"]) == str(user_id) else ""
            print("        %s  %-9s %9s  owner: %s%s"
                  % (_short(o["order_id"]), o["order_status"], _r(o["amount_minor"]),
                     (o["owner_name"] or o["email"] or "?")[:26], mine))
        paid = [o for o in orders if o["order_status"] == "paid"]
        open_ = [o for o in orders if o["order_status"] in ("open", "awaiting_payment")]
        if paid and open_ and any(int(p["amount_minor"] or 0) == int(q["amount_minor"] or 0)
                                  for p in paid for q in open_):
            phantom.extend(open_)
            print("        ^^ ONE booking, one amount, paid AND owed. The paid row is the real"
                  " money;\n           the open row is a duplicate debt that should be voided.")

    if phantom:
        tot = sum(int(o["amount_minor"] or 0) for o in phantom)
        print("\n   PHANTOM DEBT: %s across %d order(s). The money is banked and the coach has"
              % (_r(tot), len(phantom)))
        print("   accrued on the PAID row; these open rows LOOK like duplicates. Confirm how many")
        print("   people actually PLAYED before clearing any of them - two orders at one price is a")
        print("   duplicate when one person played and a correct SQUAD bill when two did.")
        print("   `python -m scripts.audit_duplicate_heads` lists every booking in this shape and")
        print("   voids only ids you name. NOT void_orphaned_orders: that one requires every booking")
        print("   on the order to be cancelled, so it can never touch a completed lesson.")


def section_commission(s, club_id, user_id, month):
    """Per PAID coaching line, does a commission_split exist - i.e. did the coach get their share?

    SCOPED BY THE BOOKINGS THIS PERSON PLAYED, NOT THE ORDERS THEY OWN. Scoping by owner reported
    "no paid coaching lines" for a player whose lessons are paid for by a parent or a booker -- and
    the honest reading of that sentence is "the coach was not paid", which was false and was said
    out loud. The coach accrues on whoever's order carried the money; the question "did the coach
    get paid for THIS person's lessons" is therefore a question about the booking, never the payer.
    """
    from sqlalchemy import text
    clause = ("AND to_char(COALESCE(o.service_date, o.created_at::date), 'YYYY-MM') = :ym"
              if month else "")
    rows = s.execute(text(
        'SELECT ol.id AS line_id, ol.description, ol.amount_minor, o.status, '
        '       bk.coach_user_id, '
        '       NULLIF(TRIM(CONCAT_WS(\' \', pu.first_name, pu.surname)), \'\') AS payer_name, '
        '       COALESCE(cp.display_name, NULLIF(TRIM(CONCAT_WS(\' \', cu.first_name, cu.surname)), \'\'), '
        '                cu.email) AS coach_name, '
        '       (SELECT count(*) FROM billing.commission_split cs '
        '         WHERE cs.order_line_id = ol.id AND cs.club_id = :c) AS splits, '
        '       (SELECT COALESCE(SUM(cs.amount_minor),0) FROM billing.commission_split cs '
        '         WHERE cs.order_line_id = ol.id AND cs.club_id = :c '
        '           AND cs.party_type = \'coach\') AS coach_minor '
        'FROM billing.order_line ol '
        'JOIN billing."order" o ON o.id = ol.order_id '
        'LEFT JOIN diary.booking bk ON bk.id = ol.booking_id '
        'LEFT JOIN iam.user cu ON cu.id = bk.coach_user_id '
        'LEFT JOIN iam.user pu ON pu.id = o.user_id '
        'LEFT JOIN iam.coach_profile cp ON cp.user_id = bk.coach_user_id AND cp.club_id = :c '
        'WHERE o.club_id = :c AND o.status = \'paid\' '
        '  AND bk.coach_user_id IS NOT NULL '
        '  AND (bk.booked_by_user_id = :u '
        '       OR EXISTS(SELECT 1 FROM diary.booking_party bp '
        '                  WHERE bp.booking_id = bk.id AND bp.user_id = :u) '
        '       OR o.user_id = :u) ' + clause + ' '
        'ORDER BY o.created_at'), {"c": club_id, "u": user_id, "ym": month}).mappings().fetchall()

    print("\nCOACH SHARE  (paid coaching lines - did the coach accrue on this money?)")
    print("-" * 78)
    if not rows:
        print("   No paid coaching lines in scope (court hire is 100% club, so it has no split).")
        return
    print("   %-34s%9s%10s  %-18s%s" % ("line", "billed", "coach got", "coach", "paid by"))
    missing = 0
    for r in rows:
        flag = ""
        if not r["splits"]:
            flag = "   <-- NO SPLIT: this coach was not paid on it"
            missing += 1
        print("   %-34s%9s%10s  %-18s%s%s"
              % ((r["description"] or "")[:34], _r(r["amount_minor"]), _r(r["coach_minor"]),
                 (r["coach_name"] or "?")[:17], (r["payer_name"] or "?")[:18], flag))
    if missing:
        print("\n   %d paid coaching line(s) carry NO commission split. Confirm club-wide with" % missing)
        print("   `python -m scripts.reconcile_coach_commission` before paying anyone out.")
    else:
        print("\n   Every paid coaching line above accrued to its coach.")


def main():
    ap = argparse.ArgumentParser(
        description="One person's complete money trail, including what the record hides (read-only).")
    ap.add_argument("needle", help="email, first name or surname (case-insensitive, partial)")
    ap.add_argument("--month", help="restrict to YYYY-MM")
    args = ap.parse_args()

    _load_env()
    import db

    print("\nPerson money trail            (READ-ONLY)")
    print("=" * 78)
    with db.session_scope() as s:
        people = _find_person(s, args.needle)
        if not people:
            print("   Nobody matches %r." % args.needle)
            return 2
        if len(people) > 1:
            print("   %d people match %r - narrow it:" % (len(people), args.needle))
            for p in people:
                print("     %-32s %s" % (p["name"] or "(no name)", p["email"]))
            return 2
        p = people[0]
        print("   %s  <%s>" % (p["name"] or "(no name)", p["email"]))
        print("   club: %s   user: %s%s"
              % (p["club_name"], _short(p["id"]), ("   month: " + args.month) if args.month else ""))

        section_orders(s, p["club_id"], p["id"], args.month)
        section_bookings(s, p["club_id"], p["id"], args.month)
        # Runs BEFORE the coach section on purpose: a debt owned by somebody else explains most
        # "paid here, owed there" reports, and the coach question is meaningless until you know
        # which order actually carried the money.
        section_shared_bookings(s, p["club_id"], p["id"], args.month)
        section_commission(s, p["club_id"], p["id"], args.month)

    print("\n" + "=" * 78)
    print("Nothing was written.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

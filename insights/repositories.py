# insights/repositories.py — Phase 2 P1 insight read-layer (docs/specs/ADMIN-PHASE2.md).
#
# Guarded, club_id-scoped, read-only aggregations over the EXISTING diary/billing/core data — NO
# new tables. Mirrors the analytics/repositories.py discipline: every read is wrapped so a missing
# table / empty data degrades to an empty payload, never a 500. This is the ONE place a given metric
# is computed, so a dashboard tile, an alert threshold and a benchmark ribbon can never disagree.
#
# First metric: court_utilisation (the "find dead slots" heatmap — a Phase-2 Must-have and the top
# benchmark gap vs world-class club software). More metrics slot in beside it as guarded functions.

from datetime import timedelta

from sqlalchemy import text

# A page_view is "member area" (an authenticated / logged-in-only surface) when its path's first
# segment is one of the portal SPA shells — vs the public marketing site. Path-based because the
# beacon captures no per-account identity (analytics.js sends no email), so this is the reliable
# "did someone reach the member section" signal. Static literal (no user input) — safe to inline.
_MEMBER_AREA = ("lower(split_part(coalesce(metadata->>'path','/'),'/',2)) IN "
                "('portal','app','admin','coach','plan','dashboard','book','my','account','login')")


def _guard(fn, default):
    try:
        return fn()
    except Exception:
        return default


def _club_currency(session, club_id):
    try:
        return session.execute(
            text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": str(club_id)}
        ).scalar() or "ZAR"
    except Exception:
        return "ZAR"


def sales_by_day(session, *, club_id, month=None):
    """Daily takings for ONE month — successful CHARGES minus Yoco REVERSALS (refunds), so each day
    shows GROSS / reversed / NET income. Each line carries the client, service type (court / lesson /
    class / membership / pack), amount (NEGATIVE for a reversal) and a link to the standard transaction
    record: booking-backed -> #/event (booking_id); class -> #/class (enrolment_id); everything else ->
    #/txn (order_id). `month` = 'YYYY-MM' (default current). Guarded -> empty. club_id-scoped."""
    b = session.execute(
        text("""
            SELECT to_char(COALESCE(to_date(:m, 'YYYY-MM'), date_trunc('month', now())), 'YYYY-MM') AS ym,
                   date_trunc('month', COALESCE(to_date(:m, 'YYYY-MM'), now()))::timestamptz AS start_d,
                   (date_trunc('month', COALESCE(to_date(:m, 'YYYY-MM'), now()))
                    + interval '1 month')::timestamptz AS end_d
        """),
        {"m": month},
    ).mappings().first()
    ym, start_d, end_d = b["ym"], b["start_d"], b["end_d"]

    def _rows():
        return session.execute(
            text("""
                SELECT p.id AS payment_id, p.order_id, p.amount_minor, p.currency_code, p.created_at,
                       p.direction, p.provider,
                       COALESCE(NULLIF(TRIM(COALESCE(u.first_name,'') || ' ' || COALESCE(u.surname,'')), ''),
                                u.email, 'Walk-in') AS client_name,
                       (SELECT COALESCE(bk.booking_type, pr.kind, 'other')
                          FROM billing.order_line ol
                          LEFT JOIN diary.booking bk ON bk.id = ol.booking_id
                          LEFT JOIN billing.price prc ON prc.id = ol.price_id
                          LEFT JOIN billing.product pr ON pr.id = prc.product_id
                         WHERE ol.order_id = p.order_id ORDER BY ol.created_at LIMIT 1) AS service_type,
                       (SELECT ol.booking_id FROM billing.order_line ol
                         WHERE ol.order_id = p.order_id AND ol.booking_id IS NOT NULL LIMIT 1) AS booking_id,
                       (SELECT ol.enrolment_id FROM billing.order_line ol
                         WHERE ol.order_id = p.order_id AND ol.enrolment_id IS NOT NULL LIMIT 1) AS enrolment_id,
                       (SELECT ol.description FROM billing.order_line ol
                         WHERE ol.order_id = p.order_id ORDER BY ol.created_at LIMIT 1) AS description
                FROM billing.payment p
                JOIN billing."order" o ON o.id = p.order_id
                LEFT JOIN iam."user" u ON u.id = o.user_id
                WHERE p.club_id = :c
                  AND ((p.direction = 'charge' AND p.status = 'succeeded')
                       OR (p.direction = 'refund' AND p.status IN ('succeeded','refunded')))
                  AND p.created_at >= :s AND p.created_at < :e
                ORDER BY p.created_at DESC
            """),
            {"c": str(club_id), "s": start_d, "e": end_d},
        ).mappings().all()

    rows = _guard(_rows, [])
    days = {}
    gross = 0
    refunded = 0
    currency = None
    for r in rows:
        currency = currency or r["currency_code"]
        amt = int(r["amount_minor"] or 0)
        is_refund = (r["direction"] == "refund")
        if is_refund:
            refunded += amt
        else:
            gross += amt
        dkey = r["created_at"].date().isoformat()
        d = days.setdefault(dkey, {"date": dkey, "gross_minor": 0, "refunded_minor": 0,
                                   "net_minor": 0, "total_minor": 0, "sales": []})
        if is_refund:
            d["refunded_minor"] += amt
        else:
            d["gross_minor"] += amt
        d["net_minor"] = d["gross_minor"] - d["refunded_minor"]
        d["total_minor"] = d["net_minor"]                  # headline per day = NET
        d["sales"].append({
            "payment_id": str(r["payment_id"]),
            "order_id": str(r["order_id"]) if r["order_id"] else None,
            "booking_id": str(r["booking_id"]) if r["booking_id"] else None,
            "enrolment_id": str(r["enrolment_id"]) if r["enrolment_id"] else None,
            "client_name": r["client_name"],
            "service_type": r["service_type"],
            "description": r["description"],
            "direction": r["direction"],
            "provider": r["provider"],
            "amount_minor": (-amt if is_refund else amt),   # NEGATIVE for a reversal
            "at": r["created_at"].isoformat(),
        })
    day_list = sorted(days.values(), key=lambda x: x["date"], reverse=True)
    net = gross - refunded
    return {
        "month": ym,
        "currency": currency or _club_currency(session, club_id),
        "total_minor": net,                # headline = NET income (gross − reversals)
        "gross_minor": gross,
        "refunded_minor": refunded,
        "net_minor": net,
        "count": len(rows),
        "days": day_list,
    }


def bookings_by_day(session, *, club_id, month=None):
    """Bookings MADE (not money received) for ONE month, grouped by the day they're played
    (starts_at) — the owner's daily diary at a glance. The sibling of sales_by_day, but over
    diary.booking rather than billing.payment, so it also carries the COACH (which a payment
    doesn't) and surfaces bookings that carry no charge (membership-covered / R0). Each row
    links to the standard event-story widget via booking_id (-> #/event/<id> = admin
    booking_story). `month` = 'YYYY-MM' (default = current month). Guarded -> empty. club_id-scoped.

    Counts real bookings only: status IN ('confirmed','completed','no_show') — 'held'/'requested'/
    'proposed'/'cancelled' hold no confirmed slot and are excluded (keeps this reconciling with the
    Overview 'bookings per day' series, which reads the same function)."""
    b = session.execute(
        text("""
            SELECT to_char(COALESCE(to_date(:m, 'YYYY-MM'), date_trunc('month', now())), 'YYYY-MM') AS ym,
                   date_trunc('month', COALESCE(to_date(:m, 'YYYY-MM'), now()))::timestamptz AS start_d,
                   (date_trunc('month', COALESCE(to_date(:m, 'YYYY-MM'), now()))
                    + interval '1 month')::timestamptz AS end_d
        """),
        {"m": month},
    ).mappings().first()
    ym, start_d, end_d = b["ym"], b["start_d"], b["end_d"]

    def _rows():
        return session.execute(
            text("""
                SELECT bk.id AS booking_id, bk.booking_type, bk.status, bk.starts_at, bk.ends_at,
                       COALESCE(NULLIF(TRIM(COALESCE(cl.first_name,'') || ' ' || COALESCE(cl.surname,'')), ''),
                                cl.email, 'Walk-in') AS client_name,
                       NULLIF(TRIM(COALESCE(co.first_name,'') || ' ' || COALESCE(co.surname,'')), '') AS coach_name,
                       res.name AS court_name,
                       (SELECT ol.description FROM billing.order_line ol
                         WHERE ol.order_id = bk.order_id ORDER BY ol.created_at LIMIT 1) AS description
                FROM diary.booking bk
                LEFT JOIN iam."user" cl ON cl.id = bk.booked_by_user_id
                LEFT JOIN iam."user" co ON co.id = bk.coach_user_id
                LEFT JOIN diary.resource res ON res.id = bk.resource_id
                WHERE bk.club_id = :c
                  AND bk.status IN ('confirmed', 'completed', 'no_show')
                  -- Exclude the auto-held court row of a lesson (it shares the lesson's order and is
                  -- not a separate booking) so one lesson counts ONCE, not as lesson + a phantom court.
                  AND (bk.booking_type <> 'court' OR bk.notes IS DISTINCT FROM '(court held for lesson)')
                  AND bk.starts_at >= :s AND bk.starts_at < :e
                ORDER BY bk.starts_at DESC
            """),
            {"c": str(club_id), "s": start_d, "e": end_d},
        ).mappings().all()

    rows = _guard(_rows, [])
    days = {}
    counts = {"court": 0, "lesson": 0, "class": 0}
    for r in rows:
        bt = r["booking_type"] or "court"
        if bt in counts:
            counts[bt] += 1
        dkey = r["starts_at"].date().isoformat()
        d = days.setdefault(dkey, {"date": dkey, "count": 0, "bookings": []})
        d["count"] += 1
        d["bookings"].append({
            "booking_id": str(r["booking_id"]),
            "booking_type": bt,
            "status": r["status"],
            "client_name": r["client_name"],
            "coach_name": r["coach_name"],
            "court_name": r["court_name"],
            "description": r["description"],
            "starts_at": r["starts_at"].isoformat(),
        })
    day_list = sorted(days.values(), key=lambda x: x["date"], reverse=True)
    return {
        "month": ym,
        "count": len(rows),
        "by_type": counts,
        "days": day_list,
    }


def court_utilisation(session, *, club_id, days=30):
    """Court occupancy over the trailing `days`, bucketed by weekday (0=Mon..6=Sun) x hour-of-day:
    booked court-hours vs available (open) court-hours, plus an overall utilisation %. Reads
    diary.booking (court) + diary.availability_rule (open hours) — nothing new. Guarded per
    sub-query so a partial DB yields an empty grid rather than erroring. Bucketing is by the
    booking's START hour (a 90-min booking counts in its start bucket) — fine for a heatmap.

    Returns {days, overall_pct, booked_hours, available_hours,
             cells:[{weekday,hour,booked_hours,available_hours,pct}]}."""
    days = max(1, min(int(days or 30), 365))

    # Booked court-hours per (weekday, hour) over the window.
    def _booked():
        rows = session.execute(
            text("""
                SELECT (EXTRACT(ISODOW FROM b.starts_at)::int - 1) AS weekday,
                       EXTRACT(HOUR FROM b.starts_at)::int AS hour,
                       COALESCE(SUM(EXTRACT(EPOCH FROM (b.ends_at - b.starts_at)) / 3600.0), 0) AS booked
                FROM diary.booking b
                JOIN diary.resource r ON r.id = b.resource_id AND r.kind = 'court'
                WHERE b.club_id = :c AND b.booking_type = 'court'
                  AND b.status IN ('confirmed', 'held', 'completed', 'no_show')
                  AND b.starts_at >= now() - make_interval(days => :d) AND b.starts_at < now()
                GROUP BY 1, 2
            """),
            {"c": str(club_id), "d": days},
        ).mappings().all()
        return {(int(r["weekday"]), int(r["hour"])): float(r["booked"] or 0) for r in rows}

    # Open court-hours per (weekday, hour) for ONE occurrence of that weekday (court rules expanded
    # into hour buckets). count(*) = number of courts open in that bucket.
    def _avail_per_occ():
        rows = session.execute(
            text("""
                SELECT ar.weekday AS weekday, gs.hour AS hour, COUNT(*) AS courts
                FROM diary.availability_rule ar
                JOIN diary.resource r ON r.id = ar.resource_id AND r.kind = 'court'
                CROSS JOIN LATERAL generate_series(EXTRACT(HOUR FROM ar.start_time)::int,
                                                   EXTRACT(HOUR FROM ar.end_time)::int - 1) AS gs(hour)
                WHERE ar.club_id = :c
                GROUP BY 1, 2
            """),
            {"c": str(club_id)},
        ).mappings().all()
        return {(int(r["weekday"]), int(r["hour"])): int(r["courts"]) for r in rows}

    # How many times each weekday occurs in the window (so per-occurrence availability scales to it).
    def _occ():
        rows = session.execute(
            text("""
                SELECT (EXTRACT(ISODOW FROM d)::int - 1) AS weekday, COUNT(*) AS occ
                FROM generate_series((now() - make_interval(days => :d))::date,
                                     (now())::date - 1, interval '1 day') d
                GROUP BY 1
            """),
            {"d": days},
        ).mappings().all()
        return {int(r["weekday"]): int(r["occ"]) for r in rows}

    booked = _guard(_booked, {})
    avail_per_occ = _guard(_avail_per_occ, {})
    occ = _guard(_occ, {})

    cells = []
    total_booked = 0.0
    total_avail = 0.0
    for key in set(booked.keys()) | set(avail_per_occ.keys()):
        wd, hr = key
        b = booked.get(key, 0.0)
        a = avail_per_occ.get(key, 0) * occ.get(wd, 0)
        total_booked += b
        total_avail += a
        pct = round(min(100.0, b / a * 100.0), 1) if a > 0 else None
        cells.append({"weekday": wd, "hour": hr, "booked_hours": round(b, 2),
                      "available_hours": round(a, 2), "pct": pct})
    cells.sort(key=lambda x: (x["weekday"], x["hour"]))
    overall = round(min(100.0, total_booked / total_avail * 100.0), 1) if total_avail > 0 else None
    return {
        "days": days,
        "overall_pct": overall,
        "booked_hours": round(total_booked, 1),
        "available_hours": round(total_avail, 1),
        "cells": cells,
    }


def _month_bounds(session, month):
    """Resolve 'YYYY-MM' (default current) -> (ym, start_date, end_date) with the month computed
    server-side, exactly like sales_by_day/bookings_by_day so all three agree on the window."""
    b = session.execute(
        text("""
            SELECT to_char(COALESCE(to_date(:m, 'YYYY-MM'), date_trunc('month', now())), 'YYYY-MM') AS ym,
                   date_trunc('month', COALESCE(to_date(:m, 'YYYY-MM'), now()))::date AS start_d,
                   (date_trunc('month', COALESCE(to_date(:m, 'YYYY-MM'), now()))
                    + interval '1 month')::date AS end_d
        """),
        {"m": month},
    ).mappings().first()
    return b["ym"], b["start_d"], b["end_d"]


def overview(session, *, club_id, month=None):
    """Month-scoped, day-bucketed business overview for the admin 'Overview' tab. ONE guarded read
    powering every panel, so the numbers RECONCILE with the Money lists by construction:
      • revenue_gross uses the SAME basis as sales_by_day (charge/succeeded) -> monthly total ties out;
      • bookings use the SAME basis as bookings_by_day (confirmed/completed/no_show) -> ties out.
    Every sub-query is _guard-wrapped (partial DB -> zeros, never a 500) and club_id-scoped. Returns a
    DENSE per-day series (every calendar day of the month, zero-filled) + KPI totals + a few month-scoped
    traffic breakdowns. `month`='YYYY-MM' (default current).

    NB the NPS read here uses core.nps_response.submitted_at — the analytics lane's nps() erroneously
    filtered on a non-existent created_at (silently returning zeros); this is the corrected source."""
    ym, start_d, end_d = _month_bounds(session, month)
    c = str(club_id)

    # Dense calendar-day skeleton for the month (so a quiet day shows a real 0, not a gap).
    days = []
    d = start_d
    while d < end_d:
        days.append(d.isoformat())
        d = d + timedelta(days=1)
    pos = {day: i for i, day in enumerate(days)}

    def _fill(rows, *value_keys):
        """rows -> {key: [per-day values]} aligned to `days`, zero-filled. Each row must expose a
        'day' (date) plus the named value columns."""
        out = {k: [0 for _ in days] for k in value_keys}
        for r in rows:
            dk = r["day"].isoformat() if hasattr(r["day"], "isoformat") else str(r["day"])
            i = pos.get(dk)
            if i is None:
                continue
            for k in value_keys:
                out[k][i] = int(r[k] or 0)
        return out

    p = {"c": c, "s": start_d, "e": end_d}

    # --- Traffic (core.usage_event page_view) — visits + unique visitors per day ------------------
    traffic = _guard(lambda: _fill(session.execute(text("""
        SELECT occurred_at::date AS day, count(*) AS visits,
               count(DISTINCT metadata->>'anon_id') AS uniques
        FROM core.usage_event
        WHERE event_type = 'page_view' AND club_id = :c
          AND occurred_at >= :s AND occurred_at < :e
        GROUP BY 1
    """), p).mappings().all(), "visits", "uniques"), {"visits": [0]*len(days), "uniques": [0]*len(days)})

    # --- Access split: public-site vs member-area (logged-in-only pages) visits per day -----------
    # `member` = path-based proxy (reached a logged-in-only page); `logged_in` = the PRECISE signal
    # (metadata.authed=true, set client-side via window.cfAuthed once Clerk resolves).
    access = _guard(lambda: _fill(session.execute(text(f"""
        SELECT occurred_at::date AS day,
               count(*) FILTER (WHERE {_MEMBER_AREA})       AS member,
               count(*) FILTER (WHERE NOT ({_MEMBER_AREA}))  AS public,
               count(*) FILTER (WHERE metadata->>'authed' = 'true') AS logged_in
        FROM core.usage_event
        WHERE event_type = 'page_view' AND club_id = :c
          AND occurred_at >= :s AND occurred_at < :e
        GROUP BY 1
    """), p).mappings().all(), "member", "public", "logged_in"),
        {"member": [0]*len(days), "public": [0]*len(days), "logged_in": [0]*len(days)})
    # Distinct visitors (anon_id) reaching each surface this month — "how many people".
    vsplit = _guard(lambda: session.execute(text(f"""
        SELECT count(DISTINCT metadata->>'anon_id') FILTER (WHERE {_MEMBER_AREA})       AS member_visitors,
               count(DISTINCT metadata->>'anon_id') FILTER (WHERE NOT ({_MEMBER_AREA})) AS public_visitors,
               count(DISTINCT metadata->>'anon_id') FILTER (WHERE metadata->>'authed' = 'true') AS logged_in_visitors
        FROM core.usage_event
        WHERE event_type = 'page_view' AND club_id = :c
          AND occurred_at >= :s AND occurred_at < :e
    """), p).mappings().first(), {"member_visitors": 0, "public_visitors": 0, "logged_in_visitors": 0})

    # --- Bookings per day (SAME basis as bookings_by_day) — total + by type + member-covered -------
    bookings = _guard(lambda: _fill(session.execute(text("""
        SELECT starts_at::date AS day, count(*) AS total,
               count(*) FILTER (WHERE booking_type = 'court')  AS court,
               count(*) FILTER (WHERE booking_type = 'lesson') AS lesson,
               count(*) FILTER (WHERE booking_type = 'class')  AS class,
               count(*) FILTER (WHERE settlement_mode = 'membership_covered') AS member
        FROM diary.booking
        WHERE club_id = :c AND status IN ('confirmed','completed','no_show')
          -- one lesson = one booking: drop the auto-held court row (shares the lesson's order).
          AND (booking_type <> 'court' OR notes IS DISTINCT FROM '(court held for lesson)')
          AND starts_at >= :s AND starts_at < :e
        GROUP BY 1
    """), p).mappings().all(), "total", "court", "lesson", "class", "member"),
        {k: [0]*len(days) for k in ("total", "court", "lesson", "class", "member")})

    # --- Revenue per day: gross (SAME basis as sales_by_day) + refunds -> net ----------------------
    # Refund rows carry status='refunded' (CLAUDE.md gotcha) so they're caught explicitly.
    revenue = _guard(lambda: _fill(session.execute(text("""
        SELECT created_at::date AS day,
               COALESCE(sum(amount_minor) FILTER (WHERE direction='charge' AND status='succeeded'),0) AS gross,
               COALESCE(sum(amount_minor) FILTER (WHERE direction='refund'
                        AND status IN ('succeeded','refunded')),0) AS refunded
        FROM billing.payment
        WHERE club_id = :c AND created_at >= :s AND created_at < :e
        GROUP BY 1
    """), p).mappings().all(), "gross", "refunded"),
        {"gross": [0]*len(days), "refunded": [0]*len(days)})
    net = [revenue["gross"][i] - revenue["refunded"][i] for i in range(len(days))]

    # --- New clients per day (core.account) -------------------------------------------------------
    clients = _guard(lambda: _fill(session.execute(text("""
        SELECT created_at::date AS day, count(*) AS signups
        FROM core.account
        WHERE club_id = :c AND deleted_at IS NULL AND created_at >= :s AND created_at < :e
        GROUP BY 1
    """), p).mappings().all(), "signups"), {"signups": [0]*len(days)})

    # --- Active members per day (uses the new period_start / cancelled_at columns) -----------------
    members = _guard(lambda: _fill(session.execute(text("""
        SELECT g::date AS day,
               (SELECT count(*) FROM billing.membership_subscription ms
                 WHERE ms.club_id = :c
                   AND ms.period_start <= g::date
                   AND (ms.cancelled_at IS NULL OR ms.cancelled_at::date > g::date)
                   AND (ms.current_period_end IS NULL OR ms.current_period_end >= g::date)) AS active
        FROM generate_series(:s, :e - interval '1 day', interval '1 day') g
    """), p).mappings().all(), "active"), {"active": [0]*len(days)})

    # --- NPS per day (core.nps_response.submitted_at — the CORRECTED source) -----------------------
    nps_daily = _guard(lambda: _fill(session.execute(text("""
        SELECT submitted_at::date AS day, count(*) AS responses,
               count(*) FILTER (WHERE score >= 9) AS promoters,
               count(*) FILTER (WHERE score <= 6) AS detractors
        FROM core.nps_response
        WHERE club_id = :c AND submitted_at >= :s AND submitted_at < :e
        GROUP BY 1
    """), p).mappings().all(), "responses", "promoters", "detractors"),
        {"responses": [0]*len(days), "promoters": [0]*len(days), "detractors": [0]*len(days)})

    # ---- KPI totals (month) ----------------------------------------------------------------------
    visits_t = sum(traffic["visits"])
    uniques_t = _guard(lambda: int(session.execute(text("""
        SELECT count(DISTINCT metadata->>'anon_id') FROM core.usage_event
        WHERE event_type='page_view' AND club_id=:c AND occurred_at>=:s AND occurred_at<:e
          AND metadata->>'anon_id' IS NOT NULL
    """), p).scalar() or 0), 0)
    nps_total = sum(nps_daily["responses"])
    nps_prom = sum(nps_daily["promoters"])
    nps_detr = sum(nps_daily["detractors"])
    nps_score = round(((nps_prom - nps_detr) / nps_total) * 100) if nps_total else None
    total_clients = _guard(lambda: int(session.execute(text("""
        SELECT count(*) FROM core.account WHERE club_id=:c AND deleted_at IS NULL
          AND created_at < :e
    """), p).scalar() or 0), 0)
    currency = _club_currency(session, club_id)

    # ---- Month-scoped traffic breakdowns (top lists) ---------------------------------------------
    def _breakdown(sql, label):
        return _guard(lambda: [
            {"label": r["label"] or "—", "visits": int(r["visits"] or 0)}
            for r in session.execute(text(sql), p).mappings().all()], [])

    sources = _breakdown("""
        SELECT CASE WHEN COALESCE(metadata->>'utm_source','')<>'' THEN metadata->>'utm_source'
                    WHEN COALESCE(metadata->>'referrer','')<>''
                      THEN split_part(split_part(metadata->>'referrer','//',2),'/',1)
                    ELSE 'direct' END AS label, count(*) AS visits
        FROM core.usage_event
        WHERE event_type='page_view' AND club_id=:c AND occurred_at>=:s AND occurred_at<:e
        GROUP BY 1 ORDER BY 2 DESC LIMIT 8""", "source")
    top_pages = _breakdown("""
        SELECT metadata->>'path' AS label, count(*) AS visits FROM core.usage_event
        WHERE event_type='page_view' AND club_id=:c AND occurred_at>=:s AND occurred_at<:e
          AND metadata->>'path' IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC LIMIT 8""", "path")
    by_device = _breakdown("""
        SELECT COALESCE(NULLIF(metadata->>'device',''),'unknown') AS label, count(*) AS visits
        FROM core.usage_event
        WHERE event_type='page_view' AND club_id=:c AND occurred_at>=:s AND occurred_at<:e
        GROUP BY 1 ORDER BY 2 DESC""", "device")

    return {
        "month": ym,
        "currency": currency,
        "days": days,
        "series": {
            "visits": traffic["visits"],
            "unique_visitors": traffic["uniques"],
            "public_visits": access["public"],
            "member_visits": access["member"],
            "logged_in_visits": access["logged_in"],
            "bookings": bookings["total"],
            "bookings_court": bookings["court"],
            "bookings_lesson": bookings["lesson"],
            "bookings_class": bookings["class"],
            "member_bookings": bookings["member"],
            "revenue_gross_minor": revenue["gross"],
            "revenue_net_minor": net,
            "refunded_minor": revenue["refunded"],
            "new_clients": clients["signups"],
            "active_members": members["active"],
            "nps_responses": nps_daily["responses"],
        },
        "kpis": {
            "visits": visits_t,
            "unique_visitors": uniques_t,
            "public_visitors": int((vsplit or {}).get("public_visitors") or 0),
            "member_visitors": int((vsplit or {}).get("member_visitors") or 0),
            "logged_in_visitors": int((vsplit or {}).get("logged_in_visitors") or 0),
            "public_visits": sum(access["public"]),
            "member_visits": sum(access["member"]),
            "logged_in_visits": sum(access["logged_in"]),
            "bookings": sum(bookings["total"]),
            "member_bookings": sum(bookings["member"]),
            "revenue_gross_minor": sum(revenue["gross"]),
            "revenue_net_minor": sum(net),
            "refunded_minor": sum(revenue["refunded"]),
            "new_clients": sum(clients["signups"]),
            "total_clients": total_clients,
            "active_members": members["active"][-1] if members["active"] else 0,
            "nps_score": nps_score,
            "nps_responses": nps_total,
        },
        "breakdowns": {"sources": sources, "top_pages": top_pages, "by_device": by_device},
    }

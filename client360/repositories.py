# client360/repositories.py — the unified Client-360 read-model composer.
#
# Discipline (matches admin/repositories.py + the lane repos): SQLAlchemy Core text(), every fn
# takes an explicit `session` and NEVER commits/rolls-back the caller's transaction (callers compose
# via db.session_scope()). club_id is ALWAYS passed in and scopes every query. READ-ONLY throughout.
#
# get_client_360 is the SINGLE SOURCE OF TRUTH read behind every client view. It is a SUPERSET of
# what admin.repositories.get_person historically returned (which now delegates here), so existing
# consumers keep working, PLUS new cross-lane blocks (packages, dependents, refunds, coaching,
# activity, notifications, profile/consent/events, capability map).
#
# GUARDING — each optional block runs inside a SAVEPOINT via _guard(): a failing block (bad SQL /
# schema drift / a lane reader raising) rolls back ONLY that block and degrades to an empty/None
# default — it NEVER rolls back the caller's transaction. This matters because the composer runs
# inside the caller's session_scope: a bare session.rollback() here would nuke the caller's pending
# writes (e.g. an admin action that then re-reads the 360) and, in the scenario harness, the fixture
# club itself (which is exactly the sc_person_360 FK break this pattern fixes).

from sqlalchemy import text


# ---------------------------------------------------------------------------
# serialization + guard helpers (local copies — no import from admin, to avoid a
# cycle: admin.repositories delegates INTO this module)
# ---------------------------------------------------------------------------

def _row(row):
    """Map a Row -> dict, stringifying uuid/uuid-ish fields and isoformatting datetimes."""
    if row is None:
        return None
    d = dict(row)
    for k, v in list(d.items()):
        if v is None:
            continue
        if (k == "id" or k.endswith("_id")) and not isinstance(v, (str, int)):
            d[k] = str(v)
        elif hasattr(v, "isoformat") and not isinstance(v, str):
            d[k] = v.isoformat()
    return d


def _rows(rows):
    return [_row(r) for r in rows]


def _guard(session, thunk, default):
    """Run a read `thunk()` inside a SAVEPOINT so a failure rolls back ONLY this block — never the
    caller's transaction. The composer runs inside the caller's session_scope, so a bare
    session.rollback() would discard the caller's pending work; a savepoint rollback recovers the
    aborted-statement state while preserving everything before it. Returns `default` on any error."""
    sp = session.begin_nested()
    try:
        result = thunk()
        sp.commit()
        return result
    except Exception:
        sp.rollback()
        return default


def _club_currency(session, *, club_id):
    cur = session.execute(
        text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": club_id},
    ).scalar()
    return cur or "ZAR"


_KIND_LABEL = {"court": "Court booking", "lesson": "Private lesson", "class": "Class"}


def _service_label(raw, kind):
    """A clean per-booking service label: the real product/service name, else the booking-type label
    (never a bare lowercase 'court')."""
    if raw:
        return _KIND_LABEL.get(str(raw).strip().lower(), raw)
    return _KIND_LABEL.get(kind, kind)


def _pay_label(order_status, settlement_mode):
    """A booking's payment status via the ONE canonical vocabulary (billing.statement.
    settlement_status_label), so a booking row on the client record says exactly what the receipt/email
    says. Maps the booking's order.status → the settled `state`, then labels it. None when the booking
    raised no order at all (e.g. a membership-free court with no debt)."""
    sm = settlement_mode or ""
    if not (order_status or sm):
        return None
    if sm in ("membership_covered", "token"):
        state = "covered"
    else:
        state = {"paid": "paid", "open": "owed", "awaiting_payment": "pending",
                 "refunded": "refunded", "void": "void",
                 "written_off": "written_off"}.get(order_status or "", "owed")
    try:
        from billing.statement import settlement_status_label
        return settlement_status_label(state, settlement_mode)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# identity (scoping guard — no membership row ⇒ not a client of this club ⇒ None)
# ---------------------------------------------------------------------------

def _identity(session, *, club_id, user_id):
    """The identity/roles/member_status base block. Returns None when the user has no iam.membership
    row in this club — mirrors get_person's scoping guard (that's what makes them a client/member)."""
    ident = session.execute(
        text("""
            SELECT u.id AS user_id, u.email, u.first_name, u.surname, u.phone, u.created_at,
                   cp.display_name,
                   array_agg(DISTINCT m.role) AS roles,
                   bool_or(m.member_status = 'active') AS any_active,
                   max(m.member_status) AS a_status
            FROM iam.membership m
            JOIN iam.user u ON u.id = m.user_id
            LEFT JOIN iam.coach_profile cp ON cp.user_id = u.id AND cp.club_id = m.club_id
            WHERE m.club_id = :c AND m.user_id = :u
            GROUP BY u.id, u.email, u.first_name, u.surname, u.phone, u.created_at, cp.display_name
        """),
        {"c": club_id, "u": user_id},
    ).mappings().first()
    if ident is None:
        return None
    roles = [r for r in (ident["roles"] or []) if r]
    is_coach = "coach" in roles
    name = (ident["display_name"]
            or " ".join(x for x in [ident["first_name"], ident["surname"]] if x).strip()
            or ident["email"] or "Member")
    return {
        "user_id": str(ident["user_id"]),
        "email": ident["email"],
        "first_name": ident["first_name"],
        "surname": ident["surname"],
        "phone": ident["phone"],
        "name": name,
        "display_name": ident["display_name"],
        "roles": roles,
        "is_coach": is_coach,
        "member_status": "active" if ident["any_active"] else (ident["a_status"] or "inactive"),
        "created_at": ident["created_at"].isoformat() if hasattr(ident["created_at"], "isoformat") else ident["created_at"],
        "currency": _club_currency(session, club_id=club_id),
    }


# ---------------------------------------------------------------------------
# back-compat blocks (copied verbatim from get_person so the scope='admin' payload
# — and the sc_person_360 harness — stays byte-identical)
# ---------------------------------------------------------------------------

def _membership_line(session, *, club_id, user_id, currency):
    """The simple active-membership line grant/revoke acts on (get_person's inline shape)."""
    def _run():
        ms = session.execute(
            text("""
                SELECT ms.status, ms.current_period_end, ms.provider,
                       -- Show the SERVICE/tier NAME (not the term). For a trial → the fixed name;
                       -- else the tier, then the product/service name, then the price label (term)
                       -- only as a last resort. The term itself lives in the 360 detail.
                       CASE WHEN ms.provider = 'trial' THEN '7 Day Trial Period'
                            ELSE COALESCE(pr.membership_tier, prod.name, pr.label) END AS plan_label,
                       pr.label AS term_label,
                       (ms.provider = 'trial') AS is_trial
                FROM billing.membership_subscription ms
                LEFT JOIN billing.price pr ON pr.id = ms.price_id
                LEFT JOIN billing.product prod ON prod.id = pr.product_id
                WHERE ms.club_id = :c AND ms.user_id = :u AND ms.status = 'active'
                  AND (ms.current_period_end IS NULL OR ms.current_period_end >= CURRENT_DATE)
                ORDER BY ms.current_period_end DESC NULLS LAST LIMIT 1
            """),
            {"c": club_id, "u": user_id},
        ).mappings().first()
        return _row(ms) if ms else None
    return _guard(session, _run, None)


def _payments(session, *, club_id, user_id):
    """Online payments by this person (succeeded charges + refund flag), newest 50. Guarded → []."""
    def _run():
        pays = session.execute(
            text("""
                SELECT p.id, p.order_id, p.provider, p.amount_minor, p.currency_code, p.status,
                       p.created_at, o.settlement_mode,
                       EXISTS(SELECT 1 FROM billing.payment r
                              WHERE r.order_id = p.order_id AND r.direction = 'refund') AS refunded
                FROM billing.payment p
                JOIN billing."order" o ON o.id = p.order_id
                WHERE p.club_id = :c AND o.user_id = :u
                  AND p.direction = 'charge' AND p.status = 'succeeded'
                ORDER BY p.created_at DESC LIMIT 50
            """),
            {"c": club_id, "u": user_id},
        ).mappings().all()
        return _rows(pays)
    return _guard(session, _run, [])


def _bookings(session, *, club_id, user_id):
    """Bookings (as the party) + class enrolments, split upcoming/history. Copied from get_person so
    the phantom '(court held for lesson)' exclusion + cancelled filter + row shape are identical."""
    def _run():
        rows = session.execute(
            text("""
                SELECT bk.id AS booking_id, NULL::uuid AS enrolment_id, bk.booking_type AS kind, bk.starts_at, bk.ends_at,
                       bk.status, (bk.starts_at >= now()) AS is_upcoming, r.name AS resource_name,
                       COALESCE(cp.display_name,
                                NULLIF(TRIM(CONCAT_WS(' ', cu.first_name, cu.surname)), ''),
                                cu.email) AS coach_name,
                       (SELECT COALESCE(p.name, NULLIF(ol.description,''))
                          FROM billing.order_line ol
                          LEFT JOIN billing.price prc ON prc.id = ol.price_id
                          LEFT JOIN billing.product p ON p.id = prc.product_id
                          WHERE ol.booking_id = bk.id ORDER BY ol.created_at LIMIT 1) AS service_raw,
                       ob.status AS order_status, ob.settlement_mode AS settlement_mode
                FROM diary.booking bk
                LEFT JOIN diary.resource r ON r.id = bk.resource_id
                LEFT JOIN iam.user cu ON cu.id = bk.coach_user_id
                LEFT JOIN iam.coach_profile cp ON cp.user_id = bk.coach_user_id AND cp.club_id = bk.club_id
                LEFT JOIN billing."order" ob ON ob.id = bk.order_id
                WHERE bk.club_id = :c AND bk.booked_by_user_id = :u
                  AND bk.status <> 'cancelled'
                  AND (bk.booking_type <> 'court' OR bk.notes IS DISTINCT FROM '(court held for lesson)')
                UNION ALL
                SELECT NULL::uuid AS booking_id, e.id AS enrolment_id, 'class' AS kind, cs.starts_at, cs.ends_at,
                       e.status, (cs.starts_at >= now()) AS is_upcoming, r.name AS resource_name,
                       COALESCE(cp.display_name,
                                NULLIF(TRIM(CONCAT_WS(' ', cu.first_name, cu.surname)), ''),
                                cu.email) AS coach_name,
                       (SELECT COALESCE(p.name, NULLIF(ol.description,''))
                          FROM billing.order_line ol
                          LEFT JOIN billing.price prc ON prc.id = ol.price_id
                          LEFT JOIN billing.product p ON p.id = prc.product_id
                          WHERE ol.enrolment_id = e.id ORDER BY ol.created_at LIMIT 1) AS service_raw,
                       oe.status AS order_status, oe.settlement_mode AS settlement_mode
                FROM diary.enrolment e
                JOIN diary.class_session cs ON cs.id = e.class_session_id AND cs.club_id = e.club_id
                LEFT JOIN diary.resource r ON r.id = cs.resource_id
                LEFT JOIN iam.user cu ON cu.id = cs.coach_user_id
                LEFT JOIN iam.coach_profile cp ON cp.user_id = cs.coach_user_id AND cp.club_id = cs.club_id
                LEFT JOIN billing."order" oe ON oe.id = e.order_id
                WHERE e.club_id = :c AND e.user_id = :u AND e.status <> 'cancelled'
                ORDER BY starts_at DESC LIMIT 100
            """),
            {"c": club_id, "u": user_id},
        ).mappings().all()
        return _rows(rows)
    allb = _guard(session, _run, [])
    # Complete the record: each booking row now carries WHAT it was (service) + whether it's PAID —
    # the same vocabulary the receipt/email uses — so the client record answers "is this paid?" too.
    for b in allb:
        b["service"] = _service_label(b.pop("service_raw", None), b.get("kind"))
        b["pay_status"] = _pay_label(b.pop("order_status", None), b.pop("settlement_mode", None))
    upcoming = [b for b in allb if b.get("is_upcoming")]
    upcoming.reverse()  # soonest-first
    return upcoming, [b for b in allb if not b.get("is_upcoming")], len(allb)


def _settlement(session, *, club_id, user_id):
    """Coach settlement summary (gross/commission/rent/net/balance) from cockpit earnings. Guarded."""
    default = {"gross_lesson_minor": 0, "commission_earned_minor": 0, "coach_earning_minor": 0,
               "rent_due_minor": 0, "net_to_coach_minor": 0, "lifetime_balance_minor": 0,
               "lesson_count": 0}

    def _run():
        # Lazy import to avoid the import cycle (admin.repositories delegates into this module).
        from admin.repositories import cockpit_coach_earnings
        uid = str(user_id)
        match = next((r for r in cockpit_coach_earnings(session, club_id=club_id)
                      if str(r.get("coach_user_id")) == uid), None)
        return match or default
    return _guard(session, _run, default)


# ---------------------------------------------------------------------------
# NEW cross-lane blocks (reuse-first — call the lane readers)
# ---------------------------------------------------------------------------

def _packages(session, *, club_id, user_id, coach_user_id=None):
    """A member's token/bundle wallets (active + history), each enriched with the coach's name.
    Reuses billing.bundles.wallets_for (active_only=False → includes exhausted/expired). For coach
    scope, filter to that coach's relevance (their own lesson packs + coach-agnostic packs)."""
    def _fetch():
        from billing import bundles as BN
        return BN.wallets_for(session, club_id=club_id, user_id=user_id, active_only=False)
    wallets = _guard(session, _fetch, None)
    if wallets is None:
        return {"active": [], "history": []}
    if coach_user_id is not None:
        cid = str(coach_user_id)
        wallets = [w for w in wallets
                   if w.get("coach_user_id") in (cid, None)]
    # Resolve coach display names for the wallets that name a coach (own savepoint — a names failure
    # must not lose the wallet list).
    coach_ids = sorted({w["coach_user_id"] for w in wallets if w.get("coach_user_id")})
    names = {}
    if coach_ids:
        def _names():
            out = {}
            for n in session.execute(
                text("""SELECT u.id, COALESCE(cp.display_name,
                                NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.surname)), ''),
                                u.email) AS coach_name
                        FROM iam.user u
                        LEFT JOIN iam.coach_profile cp ON cp.user_id = u.id AND cp.club_id = :c
                        WHERE u.id = ANY(:ids)"""),
                {"c": club_id, "ids": coach_ids},
            ).mappings().all():
                out[str(n["id"])] = n["coach_name"]
            return out
        names = _guard(session, _names, {})
    active, history = [], []
    for w in wallets:
        item = {
            "wallet_id": w.get("id"),
            "label": w.get("label"),
            "service_kind": w.get("service_kind"),
            "coach_user_id": w.get("coach_user_id"),
            "coach_name": names.get(w.get("coach_user_id")) if w.get("coach_user_id") else None,
            "sessions_remaining": w.get("sessions_remaining"),
            "minutes_remaining": w.get("minutes_remaining"),
            "minutes_total": w.get("minutes_total"),
            "base_minutes": w.get("base_minutes"),
            "expires_at": w.get("expires_at"),
            "status": w.get("status"),
        }
        if w.get("status") == "active" and int(w.get("minutes_remaining") or 0) > 0:
            active.append(item)
        else:
            history.append(item)
    return {"active": active, "history": history}


def _dependents(session, *, club_id, user_id):
    """Guardian-managed dependents (login-less children who can be booked for). is_minor +
    can_self_book included (list_dependents omits can_self_book, so query direct)."""
    def _run():
        rows = session.execute(
            text("""SELECT dependent_user_id, first_name, surname, dob, relationship,
                           is_minor, can_self_book
                    FROM iam.dependent
                    WHERE club_id = :c AND guardian_user_id = :u AND is_active = true
                    ORDER BY created_at"""),
            {"c": club_id, "u": user_id},
        ).mappings().all()
        return _rows(rows)
    return _guard(session, _run, [])


def _refunds(session, *, club_id, user_id):
    """The client's own refund requests (newest first). Reuses billing.refunds.list_refund_requests."""
    def _run():
        from billing import refunds as RF
        return RF.list_refund_requests(session, club_id=club_id, user_id=user_id)
    return _guard(session, _run, [])


def _coaching(session, *, club_id, user_id, coach_user_id=None, month=None):
    """The client's coaching statement (per-coach paid/owed/net + arrears items). Reuses
    billing.commission.client_statement. For coach scope, filter to just that coach. `month`
    (YYYY-MM) scopes the paid/collected figures to that month (owed is always current); None =
    all-time (the default, backward-compatible)."""
    default = {"month": month, "currency": None, "coaches": [], "arrears_items": [],
               "totals": {"paid_minor": 0, "owed_minor": 0, "written_off_minor": 0, "net_minor": 0}}

    def _run():
        from billing import commission as CM
        return CM.client_statement(session, club_id=club_id, user_id=user_id, month=month)
    cs = _guard(session, _run, default)
    if coach_user_id is not None:
        cid = str(coach_user_id)
        coaches = [c for c in cs.get("coaches", []) if str(c.get("coach_user_id")) == cid]
        arrears = [a for a in cs.get("arrears_items", []) if str(a.get("coach_user_id")) == cid]
        totals = {
            "paid_minor": sum(int(c.get("paid_minor") or 0) for c in coaches),
            "owed_minor": sum(int(c.get("owed_minor") or 0) for c in coaches),
            "written_off_minor": sum(int(a.get("gross_minor") or 0)
                                     for a in arrears if a.get("status") == "written_off"),
        }
        totals["net_minor"] = totals["paid_minor"] + totals["owed_minor"]
        cs = {**cs, "coaches": coaches, "arrears_items": arrears, "totals": totals}
    return cs


def _service_breakdown(session, *, club_id, coach_user_id, user_id, month=None):
    """One client's coaching grouped BY SERVICE (product + duration) for a coach — the middle tier of
    the coach's month → client → SERVICE → transaction drill. Reuses
    billing.commission.client_service_breakdown (product-aware, month-scoped). Guarded → empty."""
    default = {"month": month, "services": [], "currency": None}

    def _run():
        from billing import commission as CM
        return CM.client_service_breakdown(session, club_id=club_id, coach_user_id=coach_user_id,
                                           client_user_id=user_id, month=month)
    return _guard(session, _run, default)


def _activity(session, *, club_id, user_id):
    """The client's chronological transaction log. Reuses billing.activity.transaction_log. Heavily
    guarded (composes several lanes)."""
    def _run():
        from billing import activity as ACT
        return ACT.transaction_log(session, scope="client", club_id=club_id, user_id=user_id)
    return _guard(session, _run, [])


def _membership_status(session, *, club_id, user_id):
    """The richer member-facing membership status (trial, window, plans). Reuses
    billing.membership.membership_status. Guarded → None."""
    def _run():
        from billing import membership as MB
        return MB.membership_status(session, club_id=club_id, user_id=user_id)
    return _guard(session, _run, None)


def _notifications_unread(session, *, club_id, user_id):
    """Unread in-app notification count. Reuses core.repositories.notifications.unread_count."""
    def _run():
        from core.repositories import notifications as NOT
        return NOT.unread_count(session, club_id=club_id, user_id=user_id)
    return _guard(session, _run, 0)


# ---------------------------------------------------------------------------
# Mission 1.2 — data now reachable via the iam.user <-> core.person bridge: full
# demographics, consent state, and the CRM/behavioural event stream. All guarded (a
# missing/empty core.* degrades to a safe default), reuse-first, additive to the payload.
# ---------------------------------------------------------------------------

def _profile(session, *, user_id):
    """Full member demographics (dob, address, emergency contact, marketing_opt_in) from iam.user.
    Reuses iam.repositories.get_profile. Guarded → {}."""
    def _run():
        from iam.repositories import get_profile
        return get_profile(session, user_id=user_id) or {}
    return _guard(session, _run, {})


def _consent(session, *, user_id):
    """Latest consent state per type (marketing_email / privacy / parental) for this human, joined
    via the bridge (iam.user → core.person → core.consent). Guarded → []."""
    def _run():
        rows = session.execute(text("""
            SELECT c.consent_type, c.status, c.policy_version, c.granted_at, c.withdrawn_at
            FROM core.person p
            JOIN core.consent c ON c.subject_person_id = p.id
            WHERE p.iam_user_id = :u
            ORDER BY c.consent_type, c.granted_at DESC NULLS LAST
        """), {"u": str(user_id)}).mappings().all()
        seen, out = set(), []
        for r in rows:
            t = r["consent_type"]
            if t in seen:
                continue
            seen.add(t)
            out.append({
                "consent_type": t, "status": r["status"], "policy_version": r["policy_version"],
                "granted_at": r["granted_at"].isoformat() if hasattr(r["granted_at"], "isoformat") else r["granted_at"],
                "withdrawn_at": r["withdrawn_at"].isoformat() if hasattr(r["withdrawn_at"], "isoformat") else r["withdrawn_at"],
            })
        return out
    return _guard(session, _run, [])


def _events(session, *, user_id, limit=50):
    """The CRM/behavioural event stream (booking/payment/lifecycle/marketing) for this human — the
    behavioural half of the activity timeline, now attributable via the bridge (iam.user →
    core.person → core.usage_event by account). billing.activity remains the money half.
    Guarded → []. (Sparse historically — account_id was NULL before the Slice-0 backfill — and
    fills going forward as emit() resolves the now-existing accounts.)"""
    def _run():
        rows = session.execute(text("""
            SELECT ue.event_type, ue.ref_type, ue.ref_id, ue.occurred_at
            FROM core.person p
            JOIN core.usage_event ue ON ue.account_id = p.account_id
            WHERE p.iam_user_id = :u AND p.account_id IS NOT NULL
            ORDER BY ue.occurred_at DESC
            LIMIT :lim
        """), {"u": str(user_id), "lim": int(limit)}).mappings().all()
        return [{
            "event_type": r["event_type"], "ref_type": r["ref_type"], "ref_id": r["ref_id"],
            "at": r["occurred_at"].isoformat() if hasattr(r["occurred_at"], "isoformat") else r["occurred_at"],
        } for r in rows]
    return _guard(session, _run, [])


# ---------------------------------------------------------------------------
# capability map (per scope) — booleans the frontend gates actions on
# ---------------------------------------------------------------------------

def _can(scope):
    if scope == "admin":
        return {"void": True, "write_off": True, "discount": True, "wallet_adjust": True,
                "wallet_expire": True, "grant_membership": True, "revoke_membership": True,
                "refund": True, "issue": True}
    if scope == "coach":
        return {"discount": True, "collect": True}
    # client
    return {"pay": True, "request_refund": True}


# ---------------------------------------------------------------------------
# the composer
# ---------------------------------------------------------------------------

def get_client_360(session, *, club_id, user_id, scope="admin", coach_user_id=None, month=None):
    """The SINGLE cross-lane Client-360 read every client view derives from. Returns a dict, or None
    when the user has no iam.membership row in this club (scoping guard).

    scope in {'admin','coach','client'} governs which optional blocks are included and the `can`
    capability map. For scope='coach', pass coach_user_id — the coaching statement + packages are
    filtered to that coach's relevance, and a per-SERVICE breakdown is added (the month → client →
    service → transaction drill). `month` (YYYY-MM) scopes the coaching paid/collected figures to
    that month (owed is always current); None = all-time (backward-compatible). Every optional block
    runs inside its own SAVEPOINT (_guard), so a partial DB degrades block-by-block rather than
    500-ing OR rolling back the caller's transaction. club_id-scoped throughout.

    The payload is a SUPERSET of the legacy admin get_person shape (which delegates here), so
    existing consumers keep working."""
    scope = (scope or "admin").lower()
    out = _identity(session, club_id=club_id, user_id=user_id)
    if out is None:
        return None
    is_coach = out["is_coach"]

    # --- back-compat blocks (present in every scope; keep get_person's exact shapes) ---
    out["membership"] = _membership_line(session, club_id=club_id, user_id=user_id,
                                         currency=out["currency"])

    def _stmt():
        from billing import statement as ST
        return ST.statement(session, club_id=club_id, user_id=user_id)
    stmt = _guard(session, _stmt,
                  {"items": [], "count": 0, "total_owed_minor": 0, "currency": out["currency"]})
    out["statement"] = stmt
    out["owed_minor"] = int(stmt.get("total_owed_minor") or 0)

    out["payments"] = _payments(session, club_id=club_id, user_id=user_id)

    upcoming, history, count = _bookings(session, club_id=club_id, user_id=user_id)
    out["upcoming"] = upcoming
    out["history"] = history
    out["bookings_count"] = count

    if is_coach:
        out["settlement"] = _settlement(session, club_id=club_id, user_id=user_id)

    # --- NEW cross-lane blocks (reuse-first) ---
    pack_coach = coach_user_id if scope == "coach" else None
    out["membership_status"] = _membership_status(session, club_id=club_id, user_id=user_id)
    out["packages"] = _packages(session, club_id=club_id, user_id=user_id, coach_user_id=pack_coach)
    out["dependents"] = _dependents(session, club_id=club_id, user_id=user_id)
    out["refunds"] = _refunds(session, club_id=club_id, user_id=user_id)
    out["activity"] = _activity(session, club_id=club_id, user_id=user_id)
    out["notifications_unread"] = _notifications_unread(session, club_id=club_id, user_id=user_id)
    # Mission 1.2 — surface the now-linked data on the 360 (via the iam.user<->core.person bridge):
    # full demographics, consent state, and the CRM/behavioural event stream.
    out["profile"] = _profile(session, user_id=user_id)
    out["consent"] = _consent(session, user_id=user_id)
    out["events"] = _events(session, user_id=user_id)

    # Coaching statement — coach + admin scopes only (the coach's per-client coaching view).
    if scope in ("coach", "admin"):
        out["coaching"] = _coaching(session, club_id=club_id, user_id=user_id,
                                    coach_user_id=(coach_user_id if scope == "coach" else None),
                                    month=month)
        # The month → client → SERVICE → transaction middle tier (coach scope: their own services).
        if scope == "coach" and coach_user_id is not None:
            out["service_breakdown"] = _service_breakdown(
                session, club_id=club_id, coach_user_id=coach_user_id, user_id=user_id, month=month)

    out["month"] = month
    out["scope"] = scope
    out["can"] = _can(scope)
    return out

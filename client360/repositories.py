# client360/repositories.py — the unified Client-360 read-model composer.
#
# Discipline (matches admin/repositories.py + the lane repos): SQLAlchemy Core text(), every fn
# takes an explicit `session` and NEVER commits (callers compose via db.session_scope()). club_id
# is ALWAYS passed in and scopes every query. READ-ONLY throughout.
#
# get_client_360 is the SINGLE SOURCE OF TRUTH read behind every client view. It is a SUPERSET of
# what admin.repositories.get_person historically returned (which now delegates here), so existing
# consumers keep working, PLUS new cross-lane blocks (packages, dependents, refunds, coaching,
# activity, notifications, capability map). Every optional block is wrapped in try/except with
# session.rollback() on failure so a broken block degrades to empty/None and never 500s.

from sqlalchemy import text


# ---------------------------------------------------------------------------
# serialization helpers (local copies — no import from admin, to avoid a cycle:
# admin.repositories delegates INTO this module)
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


def _club_currency(session, *, club_id):
    cur = session.execute(
        text("SELECT currency_code FROM club.club WHERE id = :c"), {"c": club_id},
    ).scalar()
    return cur or "ZAR"


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
    try:
        ms = session.execute(
            text("""
                SELECT ms.status, ms.current_period_end, ms.provider,
                       COALESCE(pr.label, pr.membership_tier) AS plan_label
                FROM billing.membership_subscription ms
                LEFT JOIN billing.price pr ON pr.id = ms.price_id
                WHERE ms.club_id = :c AND ms.user_id = :u AND ms.status = 'active'
                  AND (ms.current_period_end IS NULL OR ms.current_period_end >= CURRENT_DATE)
                ORDER BY ms.current_period_end DESC NULLS LAST LIMIT 1
            """),
            {"c": club_id, "u": user_id},
        ).mappings().first()
        return _row(ms) if ms else None
    except Exception:
        session.rollback()
        return None


def _payments(session, *, club_id, user_id):
    """Online payments by this person (succeeded charges + refund flag), newest 50. Guarded → []."""
    try:
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
    except Exception:
        session.rollback()
        return []


def _bookings(session, *, club_id, user_id):
    """Bookings (as the party) + class enrolments, split upcoming/history. Copied from get_person so
    the phantom '(court held for lesson)' exclusion + cancelled filter + row shape are identical."""
    try:
        rows = session.execute(
            text("""
                SELECT bk.id AS booking_id, NULL::uuid AS enrolment_id, bk.booking_type AS kind, bk.starts_at, bk.ends_at,
                       bk.status, (bk.starts_at >= now()) AS is_upcoming, r.name AS resource_name,
                       COALESCE(cp.display_name,
                                NULLIF(TRIM(CONCAT_WS(' ', cu.first_name, cu.surname)), ''),
                                cu.email) AS coach_name
                FROM diary.booking bk
                LEFT JOIN diary.resource r ON r.id = bk.resource_id
                LEFT JOIN iam.user cu ON cu.id = bk.coach_user_id
                LEFT JOIN iam.coach_profile cp ON cp.user_id = bk.coach_user_id AND cp.club_id = bk.club_id
                WHERE bk.club_id = :c AND bk.booked_by_user_id = :u
                  AND bk.status <> 'cancelled'
                  AND (bk.booking_type <> 'court' OR bk.notes IS DISTINCT FROM '(court held for lesson)')
                UNION ALL
                SELECT NULL::uuid AS booking_id, e.id AS enrolment_id, 'class' AS kind, cs.starts_at, cs.ends_at,
                       e.status, (cs.starts_at >= now()) AS is_upcoming, r.name AS resource_name,
                       COALESCE(cp.display_name,
                                NULLIF(TRIM(CONCAT_WS(' ', cu.first_name, cu.surname)), ''),
                                cu.email) AS coach_name
                FROM diary.enrolment e
                JOIN diary.class_session cs ON cs.id = e.class_session_id AND cs.club_id = e.club_id
                LEFT JOIN diary.resource r ON r.id = cs.resource_id
                LEFT JOIN iam.user cu ON cu.id = cs.coach_user_id
                LEFT JOIN iam.coach_profile cp ON cp.user_id = cs.coach_user_id AND cp.club_id = cs.club_id
                WHERE e.club_id = :c AND e.user_id = :u AND e.status <> 'cancelled'
                ORDER BY starts_at DESC LIMIT 100
            """),
            {"c": club_id, "u": user_id},
        ).mappings().all()
        allb = _rows(rows)
    except Exception:
        session.rollback()
        allb = []
    upcoming = [b for b in allb if b.get("is_upcoming")]
    upcoming.reverse()  # soonest-first
    return upcoming, [b for b in allb if not b.get("is_upcoming")], len(allb)


def _settlement(session, *, club_id, user_id):
    """Coach settlement summary (gross/commission/rent/net/balance) from cockpit earnings. Guarded."""
    settle = {"gross_lesson_minor": 0, "commission_earned_minor": 0, "coach_earning_minor": 0,
              "rent_due_minor": 0, "net_to_coach_minor": 0, "lifetime_balance_minor": 0,
              "lesson_count": 0}
    try:
        # Lazy import to avoid the import cycle (admin.repositories delegates into this module).
        from admin.repositories import cockpit_coach_earnings
        uid = str(user_id)
        match = next((r for r in cockpit_coach_earnings(session, club_id=club_id)
                      if str(r.get("coach_user_id")) == uid), None)
        if match:
            settle = match
    except Exception:
        session.rollback()
    return settle


# ---------------------------------------------------------------------------
# NEW cross-lane blocks (reuse-first — call the lane readers)
# ---------------------------------------------------------------------------

def _packages(session, *, club_id, user_id, coach_user_id=None):
    """A member's token/bundle wallets (active + history), each enriched with the coach's name.
    Reuses billing.bundles.wallets_for (active_only=False → includes exhausted/expired). For coach
    scope, filter to that coach's relevance (their own lesson packs + coach-agnostic packs)."""
    try:
        from billing import bundles as BN
        wallets = BN.wallets_for(session, club_id=club_id, user_id=user_id, active_only=False)
    except Exception:
        session.rollback()
        return {"active": [], "history": []}
    if coach_user_id is not None:
        cid = str(coach_user_id)
        wallets = [w for w in wallets
                   if w.get("coach_user_id") in (cid, None)]
    # Resolve coach display names for the wallets that name a coach.
    coach_ids = sorted({w["coach_user_id"] for w in wallets if w.get("coach_user_id")})
    names = {}
    if coach_ids:
        try:
            for n in session.execute(
                text("""SELECT u.id, COALESCE(cp.display_name,
                                NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.surname)), ''),
                                u.email) AS coach_name
                        FROM iam.user u
                        LEFT JOIN iam.coach_profile cp ON cp.user_id = u.id AND cp.club_id = :c
                        WHERE u.id = ANY(:ids)"""),
                {"c": club_id, "ids": coach_ids},
            ).mappings().all():
                names[str(n["id"])] = n["coach_name"]
        except Exception:
            session.rollback()
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
    try:
        rows = session.execute(
            text("""SELECT dependent_user_id, first_name, surname, dob, relationship,
                           is_minor, can_self_book
                    FROM iam.dependent
                    WHERE club_id = :c AND guardian_user_id = :u AND is_active = true
                    ORDER BY created_at"""),
            {"c": club_id, "u": user_id},
        ).mappings().all()
        return _rows(rows)
    except Exception:
        session.rollback()
        return []


def _refunds(session, *, club_id, user_id):
    """The client's own refund requests (newest first). Reuses billing.refunds.list_refund_requests."""
    try:
        from billing import refunds as RF
        return RF.list_refund_requests(session, club_id=club_id, user_id=user_id)
    except Exception:
        session.rollback()
        return []


def _coaching(session, *, club_id, user_id, coach_user_id=None):
    """The client's coaching statement (per-coach paid/owed/net + arrears items). Reuses
    billing.commission.client_statement. For coach scope, filter to just that coach."""
    try:
        from billing import commission as CM
        cs = CM.client_statement(session, club_id=club_id, user_id=user_id, month=None)
    except Exception:
        session.rollback()
        return {"month": None, "currency": None, "coaches": [], "arrears_items": [],
                "totals": {"paid_minor": 0, "owed_minor": 0, "written_off_minor": 0, "net_minor": 0}}
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


def _activity(session, *, club_id, user_id):
    """The client's chronological transaction log. Reuses billing.activity.transaction_log. Heavily
    guarded (composes several lanes)."""
    try:
        from billing import activity as ACT
        return ACT.transaction_log(session, scope="client", club_id=club_id, user_id=user_id)
    except Exception:
        session.rollback()
        return []


def _membership_status(session, *, club_id, user_id):
    """The richer member-facing membership status (trial, window, plans). Reuses
    billing.membership.membership_status. Guarded → None."""
    try:
        from billing import membership as MB
        return MB.membership_status(session, club_id=club_id, user_id=user_id)
    except Exception:
        session.rollback()
        return None


def _notifications_unread(session, *, club_id, user_id):
    """Unread in-app notification count. Reuses core.repositories.notifications.unread_count."""
    try:
        from core.repositories import notifications as NOT
        return NOT.unread_count(session, club_id=club_id, user_id=user_id)
    except Exception:
        session.rollback()
        return 0


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

def get_client_360(session, *, club_id, user_id, scope="admin", coach_user_id=None):
    """The SINGLE cross-lane Client-360 read every client view derives from. Returns a dict, or None
    when the user has no iam.membership row in this club (scoping guard).

    scope in {'admin','coach','client'} governs which optional blocks are included and the `can`
    capability map. For scope='coach', pass coach_user_id — the coaching statement + packages are
    filtered to that coach's relevance. Every optional block is guarded (session.rollback() on
    failure → empty/None) so a partial DB degrades rather than 500s. club_id-scoped throughout.

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

    try:
        from billing import statement as ST
        stmt = ST.statement(session, club_id=club_id, user_id=user_id)
    except Exception:
        session.rollback()
        stmt = {"items": [], "count": 0, "total_owed_minor": 0, "currency": out["currency"]}
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

    # Coaching statement — coach + admin scopes only (the coach's per-client coaching view).
    if scope in ("coach", "admin"):
        out["coaching"] = _coaching(session, club_id=club_id, user_id=user_id,
                                    coach_user_id=(coach_user_id if scope == "coach" else None))

    out["scope"] = scope
    out["can"] = _can(scope)
    return out

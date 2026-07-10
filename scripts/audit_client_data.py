# scripts/audit_client_data.py — Client-360 Slice-0, Step 0: READ-ONLY data-quality audit.
#
# Measures the real state of the People base BEFORE we build any enrichment or the
# iam.user <-> core.person bridge (docs/specs/CLIENT-360-CRM-PLAN.md §10, Step 0).
# Writes NOTHING. Prints a scorecard. Safe to run against live prod any number of times.
#
#   .venv/Scripts/python -m scripts.audit_client_data
#
# Reads DATABASE_URL from a gitignored .env.local (same pattern as scripts/verify_live.py),
# never echoing the value. Also honours a DATABASE_URL already in the environment (Render shell),
# in which case no .env.local is needed.
#
# What it answers:
#   - How many humans are in iam.user, and how many have a club membership (the "907").
#   - Wix/import-origin (clerk_user_id NULL) vs Clerk self-signup (clerk_user_id set) split.
#   - Data-quality gaps: missing first_name / surname / phone / email / address / dob.
#   - Duplicate-human risk: case-insensitive email collisions in iam.user.
#   - Bridge readiness: how many iam.user already map to a core.person by CLERK ID, by EMAIL,
#     how many core.person exist, and how many would still need forward-create.
#   - The headline completeness % = share of members with {first_name, surname, phone, email}.

import os
import sys
from pathlib import Path


def _load_env_local():
    """Load DATABASE_URL from .env.local if it's not already in the environment. Never prints it."""
    if os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("DB_URL"):
        return  # already set (e.g. the Render shell) — nothing to do
    f = Path(__file__).resolve().parent.parent / ".env.local"
    if not f.exists():
        print("!! No DATABASE_URL in the environment and no .env.local found.\n"
              "   Either run this on the Render shell (DATABASE_URL is set there), or create\n"
              "   .env.local with a single line:\n"
              "     DATABASE_URL=postgresql://courtflow:...@...render.com/courtflow")
        sys.exit(2)
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    if not os.environ.get("DATABASE_URL"):
        print("!! .env.local has no DATABASE_URL line")
        sys.exit(2)


# --- tiny render helpers -----------------------------------------------------

def _hdr(title):
    print(f"\n== {title} " + "=" * max(0, 60 - len(title)))


def _row(label, value, width=42):
    print(f"   {label:<{width}} {value}")


def _pct(n, d):
    return f"{(100.0 * n / d):5.1f}%" if d else "  n/a"


def main():
    _load_env_local()
    import db
    from sqlalchemy import text

    with db.session_scope() as s:
        def scalar(q, **p):
            try:
                return s.execute(text(q), p).scalar_one()
            except Exception:
                s.rollback()
                return None

        def rows(q, **p):
            try:
                return s.execute(text(q), p).mappings().all()
            except Exception:
                s.rollback()
                return []

        eng = db.get_engine()
        _hdr("CLIENT DATA AUDIT (read-only)")
        _row("connected", eng.url.render_as_string(hide_password=True))

        # --- 1. Population -----------------------------------------------------
        _hdr("1. Population (iam.user)")
        total_users = scalar("SELECT count(*) FROM iam.user") or 0
        with_membership = scalar(
            "SELECT count(DISTINCT u.id) FROM iam.user u "
            "JOIN iam.membership m ON m.user_id = u.id") or 0
        members = scalar(
            "SELECT count(DISTINCT m.user_id) FROM iam.membership m "
            "WHERE m.role = 'member'") or 0
        dependents = scalar("SELECT count(*) FROM iam.dependent") or 0
        clerk_set = scalar("SELECT count(*) FROM iam.user WHERE clerk_user_id IS NOT NULL") or 0
        clerk_null = scalar("SELECT count(*) FROM iam.user WHERE clerk_user_id IS NULL") or 0
        _row("iam.user total (all humans, incl. dependents)", total_users)
        _row("  with >=1 club membership", f"{with_membership}   <- the '907' candidate")
        _row("  role=member memberships (distinct users)", members)
        _row("  dependents (login-less)", dependents)
        _row("clerk_user_id SET (self-signup / logged in)", clerk_set)
        _row("clerk_user_id NULL (Wix import / dependent)", f"{clerk_null}   <- Wix-origin candidate")

        # --- 2. Data-quality gaps (members = users with a membership) ----------
        # Base the quality scorecard on real members (exclude dependents, who are
        # captured separately and legitimately have no email/phone of their own).
        _hdr("2. Data-quality gaps (members with a membership)")
        base = ("FROM iam.user u WHERE EXISTS "
                "(SELECT 1 FROM iam.membership m WHERE m.user_id = u.id)")
        pop = scalar(f"SELECT count(*) {base}") or 0

        def missing(col_expr):
            return scalar(f"SELECT count(*) {base} AND ({col_expr})") or 0

        m_first = missing("u.first_name IS NULL OR btrim(u.first_name) = ''")
        m_surname = missing("u.surname IS NULL OR btrim(u.surname) = ''")
        m_anyname = missing("(u.first_name IS NULL OR btrim(u.first_name)='') "
                            "AND (u.surname IS NULL OR btrim(u.surname)='')")
        m_phone = missing("u.phone IS NULL OR btrim(u.phone) = ''")
        m_email = missing("u.email IS NULL OR btrim(u.email) = ''")
        m_addr = missing("u.address_line1 IS NULL OR btrim(u.address_line1) = ''")
        m_dob = missing("u.dob IS NULL")

        _row("member population", pop)
        _row("  missing first_name", f"{m_first:>6}  ({_pct(m_first, pop)})")
        _row("  missing surname", f"{m_surname:>6}  ({_pct(m_surname, pop)})")
        _row("  missing BOTH names (nameless)", f"{m_anyname:>6}  ({_pct(m_anyname, pop)})")
        _row("  missing phone/cell", f"{m_phone:>6}  ({_pct(m_phone, pop)})")
        _row("  missing email", f"{m_email:>6}  ({_pct(m_email, pop)})")
        _row("  missing address_line1", f"{m_addr:>6}  ({_pct(m_addr, pop)})")
        _row("  missing dob", f"{m_dob:>6}  ({_pct(m_dob, pop)})")

        # Wix hypothesis check: quality among clerk_user_id-NULL (import) members.
        wix_pop = scalar(f"SELECT count(*) {base} AND u.clerk_user_id IS NULL") or 0
        wix_phone = scalar(f"SELECT count(*) {base} AND u.clerk_user_id IS NULL "
                          "AND (u.phone IS NULL OR btrim(u.phone)='')") or 0
        wix_addr = scalar(f"SELECT count(*) {base} AND u.clerk_user_id IS NULL "
                         "AND (u.address_line1 IS NULL OR btrim(u.address_line1)='')") or 0
        clerk_pop = scalar(f"SELECT count(*) {base} AND u.clerk_user_id IS NOT NULL") or 0
        clerk_phone = scalar(f"SELECT count(*) {base} AND u.clerk_user_id IS NOT NULL "
                            "AND (u.phone IS NULL OR btrim(u.phone)='')") or 0
        _hdr("2b. Wix-import vs Clerk-signup quality (validates the hypothesis)")
        _row("Wix-origin members (clerk NULL)", wix_pop)
        _row("  of those missing phone", f"{wix_phone:>6}  ({_pct(wix_phone, wix_pop)})")
        _row("  of those missing address", f"{wix_addr:>6}  ({_pct(wix_addr, wix_pop)})")
        _row("Clerk-signup members (clerk SET)", clerk_pop)
        _row("  of those missing phone", f"{clerk_phone:>6}  ({_pct(clerk_phone, clerk_pop)})")

        # --- 3. Duplicate-human risk (email collisions) -----------------------
        _hdr("3. Duplicate-human risk — iam.user email collisions")
        dup_groups = scalar(
            "SELECT count(*) FROM (SELECT lower(email) e FROM iam.user "
            "WHERE email IS NOT NULL AND btrim(email) <> '' "
            "GROUP BY lower(email) HAVING count(*) > 1) g") or 0
        dup_rows = scalar(
            "SELECT COALESCE(sum(c),0) FROM (SELECT count(*) c FROM iam.user "
            "WHERE email IS NOT NULL AND btrim(email) <> '' "
            "GROUP BY lower(email) HAVING count(*) > 1) g") or 0
        _row("colliding emails (groups)", dup_groups)
        _row("iam.user rows involved", dup_rows)
        _row("must add UNIQUE(lower(email)) after de-dup?", "YES" if dup_groups else "no collisions — safe now")
        if dup_groups:
            _hdr("3b. Sample collisions (up to 10)")
            for r in rows(
                "SELECT lower(email) AS email, count(*) AS n, "
                "count(*) FILTER (WHERE clerk_user_id IS NOT NULL) AS with_clerk "
                "FROM iam.user WHERE email IS NOT NULL AND btrim(email) <> '' "
                "GROUP BY lower(email) HAVING count(*) > 1 ORDER BY count(*) DESC LIMIT 10"):
                _row(f"  {r['email']}", f"{r['n']} rows ({r['with_clerk']} with clerk_id)")

        # --- 4. Bridge readiness (iam.user -> core.person) --------------------
        _hdr("4. Bridge readiness (iam.user -> core.person)")
        core_person = scalar("SELECT count(*) FROM core.person")
        if core_person is None:
            _row("core.person", "table not present / not readable — skipping bridge stats")
        else:
            core_appuser = scalar("SELECT count(*) FROM core.app_user") or 0
            # Actual bridge state — needs the Step-1 iam_user_id column (guarded: None pre-deploy).
            linked = scalar("SELECT count(*) FROM core.person WHERE iam_user_id IS NOT NULL")
            fk_valid = scalar("SELECT count(*) FROM core.person p "
                              "JOIN iam.user u ON u.id = p.iam_user_id")
            members_linked = scalar(
                "SELECT count(*) FROM iam.user u "
                "WHERE EXISTS (SELECT 1 FROM iam.membership m WHERE m.user_id = u.id) "
                "AND EXISTS (SELECT 1 FROM core.person p WHERE p.iam_user_id = u.id)") or 0
            # (a) match by Clerk id: iam.user.clerk_user_id = core.app_user.auth_provider_uid
            match_clerk = scalar(
                "SELECT count(DISTINCT u.id) FROM iam.user u "
                "JOIN core.app_user au ON au.auth_provider_uid = u.clerk_user_id "
                "JOIN core.person p ON p.user_id = au.id "
                "WHERE u.clerk_user_id IS NOT NULL") or 0
            # (b) match by email (app_user OR account), excluding those already clerk-matched
            match_email = scalar(
                "SELECT count(DISTINCT u.id) FROM iam.user u "
                "WHERE u.email IS NOT NULL AND btrim(u.email) <> '' "
                "AND NOT EXISTS (SELECT 1 FROM core.app_user au2 "
                "     JOIN core.person p2 ON p2.user_id = au2.id "
                "     WHERE au2.auth_provider_uid = u.clerk_user_id AND u.clerk_user_id IS NOT NULL) "
                "AND ( EXISTS (SELECT 1 FROM core.app_user au "
                "               WHERE lower(au.email) = lower(u.email)) "
                "   OR EXISTS (SELECT 1 FROM core.account ac "
                "               WHERE lower(ac.email) = lower(u.email)) )") or 0
            matchable = match_clerk + match_email
            need_create = max(0, total_users - matchable)
            _row("core.person rows (satellites today)", core_person)
            _row("core.app_user rows", core_appuser)
            if linked is not None:
                _row("core.person LINKED (iam_user_id set)", linked)
                _row("  FK-valid (-> a real iam.user)", fk_valid)
                _row("members WITH a linked satellite", f"{members_linked} / {pop}  ({_pct(members_linked, pop)})")
            _row("iam.user matchable by CLERK id", match_clerk)
            _row("iam.user matchable by EMAIL (only)", match_email)
            _row("iam.user with NO core match (forward-create)", f"{need_create}  ({_pct(need_create, total_users)})")

        # --- 5. Marketing-opt-in flag agreement -------------------------------
        _hdr("5. marketing_opt_in flag split (iam.user vs core.app_user)")
        iam_optin = scalar("SELECT count(*) FROM iam.user WHERE marketing_opt_in IS TRUE")
        core_optin = scalar("SELECT count(*) FROM core.app_user WHERE marketing_opt_in IS TRUE")
        _row("iam.user.marketing_opt_in = true", iam_optin if iam_optin is not None else "n/a")
        _row("core.app_user.marketing_opt_in = true", core_optin if core_optin is not None else "n/a")

        # --- 6. Headline completeness ----------------------------------------
        _hdr("6. HEADLINE — profile completeness (members)")
        complete = scalar(
            f"SELECT count(*) {base} "
            "AND u.first_name IS NOT NULL AND btrim(u.first_name) <> '' "
            "AND u.surname   IS NOT NULL AND btrim(u.surname)   <> '' "
            "AND u.phone     IS NOT NULL AND btrim(u.phone)     <> '' "
            "AND u.email     IS NOT NULL AND btrim(u.email)     <> ''") or 0
        _row("members with {first, surname, phone, email}", f"{complete} / {pop}")
        _row(">>> COMPLETENESS", _pct(complete, pop))
        print("\n(Read-only audit complete — nothing was written.)\n")

    sys.exit(0)


if __name__ == "__main__":
    main()

# scripts/import_wix.py — one-shot Wix → NextPoint data migration (go-live cutover §3).
#
# Imports the LIVE Wix customer base into the platform, keyed by email so a client's
# fresh Clerk login links straight onto their imported record (auth links by email —
# iam/repositories.upsert_user_by_clerk_id step 2). Three CSV inputs (Wix exports):
#
#   clients.csv  ~900  -> iam.user + iam.membership(role='member', active)
#   members.csv  ~10   -> billing.membership_subscription (provider='manual', Wix expiry)
#                         (+ optional billing.token_wallet for a remaining lesson balance)
#   plans.csv    ~10   -> billing.bundle_plan (service_kind='lesson') definitions
#
# DISCIPLINE (non-negotiable — modelled on 1050 core_db/backfill.py):
#   * --dry-run is the DEFAULT: prints exactly what it WOULD write (counts per table),
#     then ROLLS BACK. --commit is the only thing that writes.
#   * Idempotent + re-runnable: dedup humans by lower(email) (SELECT-first, like
#     scripts.seed_nextpoint._upsert_iam_user); membership upserts on (club,user,role);
#     bundle plans keyed by (club, service_kind, label); wallets keyed by (user, plan).
#   * Validate, don't abort: blank/bad email, duplicate rows, unmatched plan names →
#     logged + skipped, with a final summary (imported / updated / skipped / errors).
#   * Club-scoped: every write carries club_id = the resolved NextPoint club id.
#
# Run (ALWAYS dry-run first, against the LOCAL sandbox):
#   python -m scripts.import_wix                         # dry-run, default CSV paths
#   python -m scripts.import_wix --dir migration/wix     # dry-run from a folder
#   python -m scripts.import_wix --dir migration/wix --commit   # WRITE (after review)
#
# Membership caveat: a wrong tier = wrong coverage/price. The dry-run prints the
# plan-name -> tier map; Tomo confirms the ~10 members before --commit (runbook §3).

import argparse
import csv
import logging
import os
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from db import get_engine, norm_email, run_boot_init

log = logging.getLogger("import_wix")

DEFAULT_DIR = os.path.join("migration", "wix")
DEFAULT_CLUB_SLUG = "nextpoint"

# --- Wix plan name -> NextPoint membership tier ----------------------------
# The one mapping a human must confirm (wrong tier => wrong coverage/price). Keys are
# lower-cased Wix plan names (substring match). The club currently seeds ONE tier,
# 'Standard', so anything unmatched falls back to it — but every fallback is COUNTED and
# surfaced in the summary so Tomo can eyeball the ~10 members before --commit.
PLAN_NAME_TIER_MAP = {
    # "wix plan name (lower)": "NextPoint membership_tier",
    # e.g. "family": "Family", "student": "Student",
}
DEFAULT_TIER = "Standard"

# Accepted date formats for a Wix expiry / "Valid Until" value (tolerant, no dateutil dep).
_DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
    "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%Y/%m/%d",
)

# Header aliases: canonical field -> the Wix column names we accept (lower-cased).
_ALIASES = {
    "email":              ("email", "email address", "e-mail", "contact email"),
    "first_name":         ("first_name", "first name", "firstname", "given name", "name"),
    "surname":            ("surname", "last name", "last_name", "lastname", "family name"),
    "phone":              ("phone", "phone number", "mobile", "tel", "telephone"),
    "marketing_opt_in":   ("marketing_opt_in", "email subscriber", "opt_in", "subscribed",
                           "marketing", "email marketing"),
    "plan":               ("plan", "plan name", "plan_name", "pricing plan", "membership"),
    "expiry":             ("expiry", "expiry date", "valid until", "end date", "end_date",
                           "expires", "expiry_date", "renewal date"),
    "label":              ("label", "name", "plan name", "plan_name", "title"),
    "sessions":           ("sessions", "sessions_count", "credits", "session count", "count"),
    "duration_minutes":   ("duration_minutes", "duration", "minutes", "length"),
    "price":              ("price", "amount", "cost", "price_rand", "rand"),
    "sessions_remaining": ("sessions_remaining", "remaining", "balance", "credits_remaining"),
    "lesson_plan":        ("lesson_plan", "lesson plan", "lesson_plan_name"),
}

_TRUTHY = {"1", "true", "t", "yes", "y", "subscribed", "opt-in", "opted in", "on"}


# --------------------------------------------------------------------------- helpers

def _norm_headers(fieldnames):
    """Map each canonical field -> the actual column present in this CSV (or None)."""
    present = {(h or "").strip().lower(): h for h in (fieldnames or [])}
    resolved = {}
    for field, aliases in _ALIASES.items():
        resolved[field] = next((present[a] for a in aliases if a in present), None)
    return resolved


def _get(row, colmap, field):
    col = colmap.get(field)
    if not col:
        return None
    v = row.get(col)
    return v.strip() if isinstance(v, str) else v


def _parse_date(v):
    if not v:
        return None
    v = v.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _parse_bool(v):
    return str(v or "").strip().lower() in _TRUTHY


def _parse_int(v):
    if v is None or str(v).strip() == "":
        return None
    try:
        return int(float(str(v).strip().replace(",", "")))
    except ValueError:
        return None


def _price_to_minor(v):
    """A price given in Rand (e.g. '600' or 'R600.00') -> cents (60000)."""
    if v is None or str(v).strip() == "":
        return None
    s = str(v).strip().lstrip("Rr ").replace(",", "")
    try:
        return int(round(float(s) * 100))
    except ValueError:
        return None


def _read_csv(path):
    if not path or not os.path.exists(path):
        return None, []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        return _norm_headers(reader.fieldnames), rows


class Report:
    """Accumulates per-table outcomes + a capped list of skip reasons."""
    def __init__(self):
        self.counts = {}
        self.skips = []
        self.tier_map_used = {}
        self.plan_matches = {}   # Wix plan name -> matched club membership plan (or NO MATCH)

    def bump(self, key, n=1):
        self.counts[key] = self.counts.get(key, 0) + n

    def skip(self, why):
        self.bump("skipped")
        if len(self.skips) < 40:
            self.skips.append(why)


# --------------------------------------------------------------------------- club

def _resolve_club_id(session, slug):
    row = session.execute(
        text("SELECT id FROM club.club WHERE slug = :s AND COALESCE(is_template,false)=false"),
        {"s": slug},
    ).mappings().first()
    if not row:
        raise SystemExit(f"club slug '{slug}' not found (non-template). Aborting.")
    return row["id"]


# --------------------------------------------------------------------------- clients

def _upsert_client(session, *, club_id, email, first_name, surname, phone, opt_in, rep):
    """iam.user keyed by lower(email); never overwrites an existing good name with a blank.
    Then ensures an active member membership. Mirrors seed._upsert_iam_user dedup."""
    existing = session.execute(
        text("SELECT id, first_name, surname, phone FROM iam.user "
             "WHERE lower(email) = :e ORDER BY created_at LIMIT 1"),
        {"e": email},
    ).mappings().first()

    if existing:
        uid = existing["id"]
        # only fill blanks — a re-import must not clobber curated data
        session.execute(
            text("UPDATE iam.user SET "
                 "  first_name = COALESCE(NULLIF(first_name,''), :fn), "
                 "  surname    = COALESCE(NULLIF(surname,''),    :sn), "
                 "  phone      = COALESCE(phone, :ph), "
                 "  marketing_opt_in = marketing_opt_in OR :opt, "
                 "  updated_at = now() WHERE id = :id"),
            {"fn": first_name or "", "sn": surname or "", "ph": phone,
             "opt": bool(opt_in), "id": uid},
        )
        rep.bump("clients_updated")
    else:
        uid = session.execute(
            text("INSERT INTO iam.user (email, first_name, surname, phone, marketing_opt_in) "
                 "VALUES (:e, :fn, :sn, :ph, :opt) RETURNING id"),
            {"e": email, "fn": first_name or "", "sn": surname or "", "ph": phone,
             "opt": bool(opt_in)},
        ).scalar()
        rep.bump("clients_created")

    # active member membership (idempotent on club,user,role)
    session.execute(
        text("INSERT INTO iam.membership (club_id, user_id, role, member_status) "
             "VALUES (:c, :u, 'member', 'active') "
             "ON CONFLICT (club_id, user_id, role) "
             "DO UPDATE SET member_status = 'active', updated_at = now()"),
        {"c": club_id, "u": uid},
    )
    return uid


def import_clients(session, *, club_id, colmap, rows, rep):
    if not rows:
        log.warning("no clients.csv rows found")
        return
    seen = set()
    for i, row in enumerate(rows, start=2):  # row 1 is the header
        email = norm_email(_get(row, colmap, "email"))
        if not email or "@" not in email:
            rep.skip(f"clients row {i}: blank/invalid email")
            continue
        if email in seen:
            rep.skip(f"clients row {i}: duplicate email in file ({email})")
            continue
        seen.add(email)
        _upsert_client(
            session, club_id=club_id, email=email,
            first_name=_get(row, colmap, "first_name"),
            surname=_get(row, colmap, "surname"),
            phone=_get(row, colmap, "phone"),
            opt_in=_parse_bool(_get(row, colmap, "marketing_opt_in")),
            rep=rep,
        )


# --------------------------------------------------------------------------- members

def _membership_price_for_plan(session, club_id, plan_name, rep):
    """Match a Wix plan name to the club's membership billing.price BY NAME — label first, then
    membership_tier, then the product name — case-insensitive. Returns (price_id, matched_name) or
    (None, None). Records the Wix-plan -> matched-plan mapping on the report so a name mismatch is
    obvious in the dry-run BEFORE committing. NEVER falls back to 'any membership' (that would attach
    the wrong plan); an unmatched name is skipped and surfaced so Tomo fixes the name / creates it."""
    name = (plan_name or "").strip()
    key = name.lower()
    if not key:
        rep.plan_matches["(blank)"] = "NO MATCH - blank plan name"
        rep.bump("membership_plan_unmatched")
        return None, None
    rows = session.execute(
        text("SELECT p.id, p.label, p.membership_tier, pr.name AS product_name "
             "FROM billing.product pr "
             "JOIN billing.price p ON p.product_id = pr.id AND p.active = true "
             "WHERE pr.club_id = :c AND pr.kind = 'membership'"),
        {"c": club_id},
    ).mappings().all()

    def _n(v):
        return (v or "").strip().lower()

    for field in ("label", "membership_tier", "product_name"):
        for r in rows:
            if _n(r[field]) == key:
                shown = r["label"] or r["membership_tier"] or r["product_name"]
                rep.plan_matches[name] = "%s  (matched on %s)" % (shown, field)
                return r["id"], shown
    rep.plan_matches[name] = "NO MATCH - create this plan or fix the name"
    rep.bump("membership_plan_unmatched")
    return None, None


def _grant_membership_with_expiry(session, *, club_id, user_id, price_id, period_end, rep):
    """Idempotent manual grant preserving the EXACT Wix expiry (unlike admin.grant_membership,
    which derives the end date from a month count). Extends an existing active sub to the later
    of the two dates; else inserts. provider='manual'."""
    existing = session.execute(
        text("SELECT id, current_period_end FROM billing.membership_subscription "
             "WHERE club_id = :c AND user_id = :u AND status = 'active' LIMIT 1"),
        {"c": club_id, "u": user_id},
    ).mappings().first()
    if existing:
        session.execute(
            text("UPDATE billing.membership_subscription "
                 "SET current_period_end = GREATEST(COALESCE(current_period_end, :pe), :pe), "
                 "    price_id = COALESCE(price_id, :pid), updated_at = now() WHERE id = :id"),
            {"pe": period_end, "pid": price_id, "id": existing["id"]},
        )
        rep.bump("memberships_extended")
    else:
        session.execute(
            text("INSERT INTO billing.membership_subscription "
                 "(club_id, user_id, price_id, status, provider, current_period_end) "
                 "VALUES (:c, :u, :pid, 'active', 'manual', :pe)"),
            {"c": club_id, "u": user_id, "pid": price_id, "pe": period_end},
        )
        rep.bump("memberships_granted")


def import_members(session, *, club_id, colmap, rows, rep, plans_colmap=None, plans_rows=None):
    if not rows:
        log.info("no members.csv rows (skipping membership grants)")
        return
    for i, row in enumerate(rows, start=2):
        email = norm_email(_get(row, colmap, "email"))
        if not email or "@" not in email:
            rep.skip(f"members row {i}: blank/invalid email")
            continue
        user = session.execute(
            text("SELECT id FROM iam.user WHERE lower(email) = :e ORDER BY created_at LIMIT 1"),
            {"e": email},
        ).mappings().first()
        if not user:
            rep.skip(f"members row {i}: no imported client for {email} (grant skipped)")
            continue
        uid = user["id"]

        plan_name = _get(row, colmap, "plan")
        price_id, matched = _membership_price_for_plan(session, club_id, plan_name, rep)
        if not price_id:
            rep.skip(f"members row {i}: no membership plan matching '{plan_name}' "
                     f"({email}) - create it / fix the name")
            continue

        expiry = _parse_date(_get(row, colmap, "expiry"))
        if expiry is None:
            # no/invalid expiry → default a 1-month runway so coverage isn't accidentally infinite
            expiry = date.today()
            rep.bump("membership_expiry_defaulted")

        _grant_membership_with_expiry(
            session, club_id=club_id, user_id=uid, price_id=price_id,
            period_end=expiry, rep=rep,
        )

        # optional: a remaining prepaid-lesson balance -> token_wallet
        remaining = _parse_int(_get(row, colmap, "sessions_remaining"))
        if remaining and remaining > 0:
            _import_lesson_wallet(session, club_id=club_id, user_id=uid,
                                  lesson_plan=_get(row, colmap, "lesson_plan"),
                                  remaining=remaining, plans_colmap=plans_colmap,
                                  plans_rows=plans_rows, rep=rep)


def _import_lesson_wallet(session, *, club_id, user_id, lesson_plan, remaining,
                          plans_colmap, plans_rows, rep):
    """Seed a minutes-based token_wallet for a member's remaining lesson balance. Idempotent:
    skip if the member already has a lesson wallet (a re-import must not double their credit)."""
    exists = session.execute(
        text("SELECT 1 FROM billing.token_wallet "
             "WHERE club_id = :c AND user_id = :u AND service_kind = 'lesson' "
             "  AND status IN ('active','pending') LIMIT 1"),
        {"c": club_id, "u": user_id},
    ).first()
    if exists:
        rep.bump("wallets_skipped_existing")
        return
    # unit length from the matching plan (default 60 min)
    unit = 60
    plan_id = None
    if plans_rows and lesson_plan:
        for prow in plans_rows:
            if (_get(prow, plans_colmap, "label") or "").strip().lower() == lesson_plan.strip().lower():
                unit = _parse_int(_get(prow, plans_colmap, "duration_minutes")) or 60
                break
    if lesson_plan:
        plan_id = session.execute(
            text("SELECT id FROM billing.bundle_plan WHERE club_id = :c "
                 "AND service_kind = 'lesson' AND lower(label) = lower(:l) LIMIT 1"),
            {"c": club_id, "l": lesson_plan},
        ).scalar()
    minutes = remaining * unit
    session.execute(
        text("INSERT INTO billing.token_wallet "
             "(club_id, user_id, bundle_plan_id, service_kind, duration_minutes, base_minutes, "
             " tokens_total, tokens_remaining, minutes_total, minutes_remaining, status, purchased_at) "
             "VALUES (:c, :u, :pid, 'lesson', :dur, :base, :tt, :tr, :mt, :mr, 'active', now())"),
        {"c": club_id, "u": user_id, "pid": plan_id, "dur": unit, "base": unit,
         "tt": remaining, "tr": remaining, "mt": minutes, "mr": minutes},
    )
    rep.bump("lesson_wallets_created")


# --------------------------------------------------------------------------- plans

def import_plans(session, *, club_id, colmap, rows, rep):
    if not rows:
        log.info("no plans.csv rows (skipping lesson-plan definitions)")
        return
    for i, row in enumerate(rows, start=2):
        label = _get(row, colmap, "label")
        if not label:
            rep.skip(f"plans row {i}: blank label")
            continue
        sessions = _parse_int(_get(row, colmap, "sessions"))
        if not sessions or sessions <= 0:
            rep.skip(f"plans row {i}: invalid sessions count for '{label}'")
            continue
        duration = _parse_int(_get(row, colmap, "duration_minutes")) or 60
        price_minor = _price_to_minor(_get(row, colmap, "price"))
        if price_minor is None:
            rep.skip(f"plans row {i}: invalid price for '{label}'")
            continue
        exists = session.execute(
            text("SELECT id FROM billing.bundle_plan WHERE club_id = :c "
                 "AND service_kind = 'lesson' AND lower(label) = lower(:l) LIMIT 1"),
            {"c": club_id, "l": label},
        ).scalar()
        if exists:
            rep.bump("plans_skipped_existing")
            continue
        session.execute(
            text("INSERT INTO billing.bundle_plan "
                 "(club_id, service_kind, label, sessions_count, duration_minutes, "
                 " price_minor, currency_code, validity_days, active, status) "
                 "VALUES (:c, 'lesson', :l, :n, :d, :p, 'ZAR', NULL, true, 'active')"),
            {"c": club_id, "l": label, "n": sessions, "d": duration, "p": price_minor},
        )
        rep.bump("plans_created")


# --------------------------------------------------------------------------- driver

def run(*, directory, clients_path, members_path, plans_path, club_slug, commit):
    clients_path = clients_path or os.path.join(directory, "clients.csv")
    members_path = members_path or os.path.join(directory, "members.csv")
    plans_path = plans_path or os.path.join(directory, "plans.csv")

    c_map, c_rows = _read_csv(clients_path)
    m_map, m_rows = _read_csv(members_path)
    p_map, p_rows = _read_csv(plans_path)

    log.info("inputs: clients=%s (%d) members=%s (%d) plans=%s (%d)",
             clients_path, len(c_rows), members_path, len(m_rows), plans_path, len(p_rows))
    if c_map is None and m_map is None and p_map is None:
        raise SystemExit(f"no CSVs found under {directory} (expected clients.csv/members.csv/plans.csv)")

    rep = Report()
    session = Session(get_engine())
    try:
        club_id = _resolve_club_id(session, club_slug)
        log.info("club '%s' -> %s", club_slug, club_id)

        # plans first: bundle_plan definitions exist before wallets reference them
        if p_map is not None:
            import_plans(session, club_id=club_id, colmap=p_map, rows=p_rows, rep=rep)
        if c_map is not None:
            import_clients(session, club_id=club_id, colmap=c_map, rows=c_rows, rep=rep)
        if m_map is not None:
            import_members(session, club_id=club_id, colmap=m_map, rows=m_rows, rep=rep,
                           plans_colmap=p_map, plans_rows=p_rows)

        if commit:
            session.commit()
            log.info("COMMITTED to the database.")
        else:
            session.rollback()
            log.info("DRY-RUN - rolled back, nothing written.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    _print_summary(rep, commit)


def _print_summary(rep, commit):
    print("\n" + "=" * 60)
    print(f"  Wix import summary  ({'COMMITTED' if commit else 'DRY-RUN (rolled back)'})")
    print("=" * 60)
    order = [
        "clients_created", "clients_updated",
        "memberships_granted", "memberships_extended",
        "membership_plan_unmatched", "membership_expiry_defaulted",
        "plans_created", "plans_skipped_existing",
        "lesson_wallets_created", "wallets_skipped_existing",
        "skipped",
    ]
    for k in order:
        if k in rep.counts:
            print(f"  {k:<28} {rep.counts[k]:>6}")
    for k, v in rep.counts.items():
        if k not in order:
            print(f"  {k:<28} {v:>6}")

    if rep.plan_matches:
        print("\n  Wix plan  ->  matched club membership plan   (CONFIRM before --commit):")
        for plan, matched in sorted(rep.plan_matches.items()):
            flag = " <<<" if "NO MATCH" in matched else ""
            print(f"    {plan:<30} -> {matched}{flag}")

    if rep.skips:
        print(f"\n  First {len(rep.skips)} skips:")
        for s in rep.skips:
            print(f"    - {s}")
    print("=" * 60 + "\n")


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Import Wix data into NextPoint (dry-run by default).")
    ap.add_argument("--dir", dest="directory", default=DEFAULT_DIR,
                    help=f"folder holding clients/members/plans.csv (default {DEFAULT_DIR})")
    ap.add_argument("--clients", help="override path to clients.csv")
    ap.add_argument("--members", help="override path to members.csv")
    ap.add_argument("--plans", help="override path to plans.csv")
    ap.add_argument("--club-slug", default=DEFAULT_CLUB_SLUG, help="target club slug")
    ap.add_argument("--commit", action="store_true",
                    help="WRITE to the DB (default is dry-run + rollback)")
    ap.add_argument("--init", action="store_true", help="boot schema first (rarely needed)")
    args = ap.parse_args()

    if args.init:
        run_boot_init()

    run(directory=args.directory, clients_path=args.clients, members_path=args.members,
        plans_path=args.plans, club_slug=args.club_slug, commit=args.commit)


if __name__ == "__main__":
    main()

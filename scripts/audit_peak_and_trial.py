# scripts/audit_peak_and_trial.py -- READ-ONLY. What peak hours and which membership rules are
# actually in force, per court and per tier.
#
#   python -m scripts.audit_peak_and_trial
#
# Why it exists: both of these are set on a screen and consumed deep in pricing, and BOTH fail
# silently when they are wrong. A peak window that never matches simply charges the base price for
# ever; a trial tier that is not flagged is simply never granted. Nothing errors, nothing is logged,
# and the form looks correctly filled in either way. So this prints what the RESOLVER would decide,
# not what the screen appears to say.
#
# It writes nothing and takes no --commit: there is no state here to change.
#
# Peak resolution, mirrored from diary.pricing._peak_windows so this reports the truth:
#   - a court with peak_override -> ITS OWN windows, including NONE (= never peak)
#   - otherwise                  -> the CLUB's windows
#   - a scope with no diary.peak_window rows falls back to its legacy single columns
import argparse
import io
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

DAY = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}


def _load_env():
    """Load DATABASE_URL from .env.local if present. NEVER prints the value (DATA-ACCESS.md)."""
    envf = os.path.join(REPO, ".env.local")
    if os.path.isfile(envf) and not os.getenv("DATABASE_URL"):
        for line in io.open(envf, encoding="utf-8"):
            line = line.strip()
            if line.startswith("DATABASE_URL=") and not line.startswith("#"):
                os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    import urllib.parse
    try:
        p = urllib.parse.urlparse(os.getenv("DATABASE_URL") or "")
        return "%s / %s" % (p.hostname or "?", (p.path or "").lstrip("/") or "?")
    except Exception:
        return "(unparseable url)"


def _hhmm(x):
    if x is None:
        return "--:--"
    return "%02d:%02d" % (int(x) // 60, int(x) % 60)


def _days(csv):
    """'1,2,3,4' -> 'Mon-Thu' when contiguous, else 'Mon/Wed'. NULL/empty means every day."""
    if csv is None or str(csv).strip() == "":
        return "every day"
    try:
        ns = sorted({int(x) for x in str(csv).split(",") if str(x).strip()})
    except ValueError:
        return str(csv)
    if not ns:
        return "every day"
    if len(ns) > 2 and ns == list(range(ns[0], ns[-1] + 1)):
        return "%s-%s" % (DAY.get(ns[0], "?"), DAY.get(ns[-1], "?"))
    return "/".join(DAY.get(n, "?") for n in ns)


def _win(days, a, b):
    return "%s %s-%s" % (_days(days), _hhmm(a), _hhmm(b))


def _resolve_club(s, text, wanted):
    """The subject FIRST, then its club. Never 'the first club by created_at' -- that is a coin flip
    in a two-club database, and DATA-ACCESS.md records the bug that bought this rule."""
    rows = [dict(r) for r in s.execute(
        text("SELECT id, name FROM club.club ORDER BY name")).mappings()]
    if wanted:
        for r in rows:
            if str(r["id"]) == wanted or (r["name"] or "").lower() == wanted.lower():
                return r
        print("!! no club matches %r. Known: %s"
              % (wanted, ", ".join((r["name"] or "?") for r in rows)))
        sys.exit(2)
    if len(rows) == 1:
        return rows[0]
    print("!! %d clubs here - pass --club <name|id>: %s"
          % (len(rows), ", ".join((r["name"] or "?") for r in rows)))
    sys.exit(2)


def _report_peak(s, text, cid):
    club_rows = [dict(r) for r in s.execute(
        text("SELECT days, start_min, end_min FROM diary.peak_window "
             " WHERE club_id = :c AND resource_id IS NULL ORDER BY start_min NULLS FIRST"),
        {"c": cid}).mappings()]
    pol = s.execute(
        text("SELECT peak_days, peak_start_min, peak_end_min FROM club.policy WHERE club_id = :c"),
        {"c": cid}).mappings().first()

    print("")
    print("== CLUB-WIDE PEAK (what a court inherits) ==")
    if club_rows:
        for w in club_rows:
            print("   %s" % _win(w["days"], w["start_min"], w["end_min"]))
        print("   source: diary.peak_window (%d window(s))" % len(club_rows))
    elif pol and (pol["peak_start_min"] is not None or pol["peak_end_min"] is not None):
        print("   %s" % _win(pol["peak_days"], pol["peak_start_min"], pol["peak_end_min"]))
        print("   source: LEGACY single columns - still honoured, but only ONE window is possible")
    else:
        print("   (none - no club-wide peak)")

    # A WINDOW WITHOUT A PEAK PRICE CHARGES NOTHING EXTRA. diary.pricing only applies peak when the
    # matched price row HAS a peak_amount_minor:
    #     is_peak = (at_local is not None and peak is not None and in_peak_window(...))
    # So the window and the AMOUNT are two separate settings, on two separate screens (the court,
    # and the court SERVICE under Setup -> Services). Reporting the window alone reads as "this
    # court is charged peak" when it may be entirely inert — which is exactly the mistake this
    # script exists to prevent, made by the script itself.
    courts = [dict(r) for r in s.execute(
        text("SELECT r.id, r.name, r.peak_override, r.peak_days, r.peak_start_min, r.peak_end_min, "
             "       pr.name AS service, "
             "       (SELECT count(*) FROM billing.price p "
             "         WHERE p.product_id = r.product_id AND p.active "
             "           AND p.peak_amount_minor IS NOT NULL) AS peak_priced, "
             "       (SELECT count(*) FROM billing.price p "
             "         WHERE p.product_id = r.product_id AND p.active "
             "           AND p.duration_minutes IS NOT NULL) AS durations "
             "FROM diary.resource r "
             "LEFT JOIN billing.product pr ON pr.id = r.product_id "
             "WHERE r.club_id = :c AND r.kind = 'court' "
             "  AND COALESCE(r.is_active, true) ORDER BY r.rank, r.name"),
        {"c": cid}).mappings()]
    by_res = {}
    for r in s.execute(
        text("SELECT resource_id, days, start_min, end_min FROM diary.peak_window "
             " WHERE club_id = :c AND resource_id IS NOT NULL ORDER BY start_min NULLS FIRST"),
            {"c": cid}).mappings():
        by_res.setdefault(str(r["resource_id"]), []).append(dict(r))

    print("")
    print("== PEAK PER COURT (what the resolver actually applies) ==")
    for c in courts:
        own = by_res.get(str(c["id"]), [])
        if not c["peak_override"]:
            src = "inherits the club"
            wins = ["(club) " + _win(w["days"], w["start_min"], w["end_min"]) for w in club_rows]
            if not wins and pol and pol["peak_start_min"] is not None:
                wins = ["(club, legacy) "
                        + _win(pol["peak_days"], pol["peak_start_min"], pol["peak_end_min"])]
        elif own:
            src = "own windows"
            wins = [_win(w["days"], w["start_min"], w["end_min"]) for w in own]
        elif c["peak_start_min"] is not None or c["peak_end_min"] is not None:
            src = "own LEGACY column - only ONE window is possible"
            wins = [_win(c["peak_days"], c["peak_start_min"], c["peak_end_min"])]
        else:
            src = "overrides with NONE = never peak"
            wins = []
        print("   %-26s %s" % ((c["name"] or "?"), src))
        for w in (wins or ["(no peak - always base price)"]):
            print("      %s" % w)
        # The other half of the answer. Without it a window reads as a charge that may not exist.
        svc = c["service"] or "(default court service)"
        if wins and not int(c["peak_priced"] or 0):
            print("      -> NOT CHARGED: service '%s' has no peak price on any duration, so the"
                  % svc)
            print("         base price applies and this window does nothing.")
        elif wins:
            print("      -> charged: '%s' has a peak price on %d of %d duration(s)"
                  % (svc, int(c["peak_priced"] or 0), int(c["durations"] or 0)))
        elif int(c["peak_priced"] or 0):
            print("      -> service '%s' HAS peak prices, but no window ever matches this court."
                  % svc)


def _report_memberships(s, text, cid):
    print("")
    print("== MEMBERSHIP TIERS ==")
    plans = [dict(r) for r in s.execute(
        text("SELECT p.membership_tier, p.label, p.term_months, p.active, "
             "       p.access_days, p.access_start_min, p.access_end_min, "
             "       p.max_covered_minutes, p.max_covered_per_day, p.max_courts_per_day, "
             "       p.is_trial, p.trial_days, p.covers_peak "
             "FROM billing.product pr JOIN billing.price p ON p.product_id = pr.id "
             "WHERE pr.club_id = :c AND pr.kind = 'membership' AND p.term_months IS NOT NULL "
             "ORDER BY p.membership_tier, p.term_months"),
        {"c": cid}).mappings()]
    # A TIER IS SEVERAL PRICES -- one per term (1 / 3 / 12 months) -- and this used to report the
    # FIRST row it saw. A tier whose shortest term had been retired therefore read as "inactive"
    # while the tier was plainly on sale, which is a false alarm in a report whose whole job is to
    # be trusted over the screen. A tier is ACTIVE if ANY of its terms is, and the rules are read
    # from an ACTIVE term when there is one.
    groups = {}
    order = []
    for p in plans:
        tier = p["membership_tier"] or p["label"] or "(unnamed)"
        if tier not in groups:
            groups[tier] = []
            order.append(tier)
        groups[tier].append(p)

    for tier in order:
        rows = groups[tier]
        live = [r for r in rows if r["active"]]
        p = (live or rows)[0]
        state = ("ACTIVE" if live else "inactive")
        terms = "%d term(s)" % len(rows)
        if live and len(live) != len(rows):
            terms += ", %d retired/dormant" % (len(rows) - len(live))
        free = ("any time"
                if (p["access_days"] is None and p["access_start_min"] is None
                    and p["access_end_min"] is None)
                else _win(p["access_days"], p["access_start_min"], p["access_end_min"]))
        caps = "%s min / %s per day / %s court(s) per day" % (
            p["max_covered_minutes"] if p["max_covered_minutes"] is not None else "no cap",
            p["max_covered_per_day"] if p["max_covered_per_day"] is not None else "no cap",
            p["max_courts_per_day"] if p["max_courts_per_day"] is not None else "NO CAP")
        print("   %-30s %-8s (%s)" % (tier, state, terms))
        print("      free hours : %s" % free)
        print("      caps       : %s" % caps)
        print("      peak       : %s" % ("FREE at peak" if p["covers_peak"]
                                         else "CHARGED at peak"))
        # The rules live on every term, so a term that disagrees is a real inconsistency: the tier
        # then behaves differently depending on which term the member happens to have bought.
        odd = [r for r in rows if bool(r["covers_peak"]) != bool(p["covers_peak"])
               or r["max_courts_per_day"] != p["max_courts_per_day"]
               or r["access_start_min"] != p["access_start_min"]]
        if odd:
            print("      !! %d term(s) of this tier carry DIFFERENT rules - a member gets whichever"
                  % len(odd))
            print("         term they bought. Re-save the tier to write them all the same.")
        if any(r["is_trial"] for r in rows):
            t = [r for r in rows if r["is_trial"]][0]
            print("      TRIAL      : this tier is the signup trial, %s day(s)"
                  % (t["trial_days"] if t["trial_days"] is not None else "?"))
    return plans


def _report_trial(s, text, cid, plans):
    print("")
    print("== SIGNUP TRIAL ==")
    trials = [p for p in plans if p["is_trial"]]
    if not trials:
        print("   NO tier is flagged as the signup trial.")
        print("   -> new members get the LEGACY trial: no linked price, so it covers ANY time,")
        print("      including peak, and inherits no caps.")
        return
    names = sorted({(t["membership_tier"] or t["label"] or "?") for t in trials})
    t = trials[0]
    print("   tier          : %s" % ", ".join(names))
    print("   length        : %s day(s)" % (t["trial_days"] if t["trial_days"] is not None else "?"))
    print("   peak          : %s" % ("FREE at peak - trialists play prime time for nothing"
                                     if t["covers_peak"] else "CHARGED at peak"))
    if len(names) > 1:
        print("   !! more than one TIER is flagged - only one should be")
    legacy = s.execute(
        text("SELECT count(*) FROM billing.membership_subscription "
             " WHERE club_id = :c AND provider = 'trial' AND status = 'active' "
             "   AND price_id IS NULL"), {"c": cid}).scalar()
    print("   in flight     : %d active trial(s) still on the LEGACY grant (no linked price)."
          % int(legacy or 0))
    print("                   Those keep their old any-time rules until they lapse; only NEW")
    print("                   signups pick up the tier above.")


def main():
    ap = argparse.ArgumentParser(
        description="Read-only: the peak windows and membership rules actually in force.")
    ap.add_argument("--club", default=None, help="club name or id (only needed if several exist)")
    args = ap.parse_args()

    where = _load_env()
    if not os.getenv("DATABASE_URL"):
        print("!! DATABASE_URL is not set. Run this from the courtflow-api Shell, where it already is.")
        sys.exit(2)
    print("DB: %s" % where)

    from sqlalchemy import text
    import db as _db

    with _db.session_scope() as s:
        club = _resolve_club(s, text, args.club)
        cid = str(club["id"])
        print("Club: %s" % (club["name"] or cid))
        _report_peak(s, text, cid)
        plans = _report_memberships(s, text, cid)
        _report_trial(s, text, cid, plans)
    print("")
    print("(read-only - nothing was changed)")


if __name__ == "__main__":
    main()

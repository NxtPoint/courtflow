#!/usr/bin/env python
"""Mark how the club monetises a coach: 'commission' (default) or 'rent'. DRY-RUN BY DEFAULT.

    python -m scripts.set_coach_billing_model                       # show every coach's model
    python -m scripts.set_coach_billing_model --who rossnem@mweb.co.za --model rent
    python -m scripts.set_coach_billing_model --who ... --model rent --commit

WHY NOT JUST INFER IT FROM 0% COMMISSION. Because `commission.resolve_rate` returns 0 when NO rule
exists at all:

    (no rule)        -> 0   (coach keeps 100%, club takes nothing)

so "0% commission" and "nobody has configured this coach yet" are the SAME VALUE. Inferring rent
from a 0% rate would silently stop billing every unconfigured coach's clients — invisibly, because
nothing errors; the lessons simply stop producing revenue. This codebase has been bitten by
absent-vs-zero twice already (a duplicate R0 price row that billed R12,680 of coaching at nothing,
and a NULL entitlement cap read as "unconstrained" that cancelled a paid tier's limits).

0% also does not distinguish two real arrangements: a coach who bills clients DIRECTLY (rent — the
club never touches the money) from one who uses CLUB billing and pays no commission (the club
collects and remits 100%, less rent). Those need opposite behaviour at booking time.

So it stays explicit. A flag you set once per coach beats a guess that fails silently — this script
is the whole administrative burden, and there is no toggle to build.
"""
import argparse
import os
import sys
from pathlib import Path

RAND = "R{:,.2f}".format


def _load_env():
    if os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("DB_URL"):
        return
    f = Path(__file__).resolve().parent.parent / ".env.local"
    if not f.exists():
        sys.exit("!! No DATABASE_URL in env and no .env.local. Run this on the Render shell.")
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_COACHES = """
SELECT u.id, u.email,
       COALESCE(NULLIF(trim(coalesce(u.first_name,'')||' '||coalesce(u.surname,'')),''),'(unnamed)') AS name,
       a.id AS agreement_id,
       COALESCE(a.billing_model,'commission') AS model,
       COALESCE(a.rent_minor,0) AS rent_minor
  FROM iam.coach_profile cp
  JOIN iam."user" u ON u.id = cp.user_id
  LEFT JOIN billing.coach_agreement a
         ON a.coach_user_id = u.id AND a.club_id = :c AND a.status = 'active'
        AND (a.effective_to IS NULL OR a.effective_to >= CURRENT_DATE)
 WHERE cp.club_id = :c
 ORDER BY name
"""


def main():
    ap = argparse.ArgumentParser(description="Show or set a coach's billing model (dry-run default).")
    ap.add_argument("--who", help="coach email, or part of their name (omit to list everyone)")
    ap.add_argument("--model", choices=("commission", "rent"), help="the model to set")
    ap.add_argument("--rent-minor", type=int, help="optionally set the monthly rent, in cents")
    ap.add_argument("--commit", action="store_true", help="actually write (default: report only)")
    args = ap.parse_args()
    if args.model and not args.who:
        sys.exit("!! --model needs --who")
    _load_env()

    import db
    from sqlalchemy import text
    from billing import commission as CM

    with db.session_scope() as s:
        clubs = s.execute(text("SELECT id, name FROM club.club ORDER BY created_at")).mappings().all()
        for club in clubs:
            rows = s.execute(text(_COACHES), {"c": str(club["id"])}).mappings().all()
            if not rows:
                continue
            print(f"\n== {club['name']}")
            # SHOW BOTH SIDES OF THE ARRANGEMENT. An earlier version printed rent only, and a rent of
            # R0.00 got read back as "0% commission" — two different columns with opposite meanings.
            # The club % is resolved through the SAME function billing uses at settlement
            # (resolve_commission_pct), so this table can never quote a rate the money does not.
            print(f"   {'coach':<26} {'model':<12} {'club %':>8} {'rent/mo':>11}   email")
            for r in rows:
                if args.who and args.who.lower() not in (r["name"] + " " + (r["email"] or "")).lower():
                    continue
                # "no rule at all" and "a rule that says 0%" BOTH resolve to 0 — the exact ambiguity
                # that keeps the rent flag explicit rather than inferred. Say which one it is.
                has_rule = s.execute(
                    text("SELECT 1 FROM billing.commission_rule "
                         " WHERE club_id = :c AND active "
                         "   AND (coach_user_id IS NULL OR coach_user_id = :u) "
                         "   AND effective_from <= now() "
                         "   AND (effective_to IS NULL OR effective_to > now()) LIMIT 1"),
                    {"c": str(club["id"]), "u": str(r["id"])}).first()
                try:
                    pct = CM.resolve_commission_pct(s, club_id=str(club["id"]),
                                                    coach_user_id=str(r["id"]))
                    pct_s = f"{float(pct):g}%" if has_rule else "no rule"
                except Exception:
                    pct_s = "?"
                flag = "  <<" if (args.who and args.model) else ""
                print(f"   {r['name'][:26]:<26} {r['model']:<12} {pct_s:>8} "
                      f"{RAND(r['rent_minor'] / 100):>11}   {r['email']}{flag}")

            if not (args.who and args.model):
                continue
            targets = [r for r in rows
                       if args.who.lower() in (r["name"] + " " + (r["email"] or "")).lower()]
            if not targets:
                continue
            if len(targets) > 1:
                print(f"   !! {len(targets)} coaches match {args.who!r} — be more specific.")
                continue
            t = targets[0]
            if t["model"] == args.model and args.rent_minor is None:
                print(f"   -> {t['name']} is already '{args.model}'. Nothing to do.")
                continue
            if not args.commit:
                print(f"   >>> DRY-RUN: would set {t['name']} to '{args.model}'"
                      + (f" with rent {RAND(args.rent_minor / 100)}/mo" if args.rent_minor is not None else "")
                      + ". Re-run with --commit.")
                continue
            if t["agreement_id"]:
                s.execute(
                    text("UPDATE billing.coach_agreement SET billing_model = :m, "
                         "       rent_minor = COALESCE(:rent, rent_minor), updated_at = now() "
                         " WHERE id = :id"),
                    {"m": args.model, "rent": args.rent_minor, "id": t["agreement_id"]})
            else:
                # No agreement yet — create the active one this coach was missing.
                s.execute(
                    text("INSERT INTO billing.coach_agreement "
                         "(club_id, coach_user_id, rent_minor, billing_model, status) "
                         "VALUES (:c, :u, COALESCE(:rent,0), :m, 'active')"),
                    {"c": str(club["id"]), "u": str(t["id"]), "rent": args.rent_minor,
                     "m": args.model})
            print(f"   -> {t['name']} is now '{args.model}'.")
    print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:                                            # noqa: BLE001
        print(f"FAILED: {type(e).__name__}: {e}")
        sys.exit(1)

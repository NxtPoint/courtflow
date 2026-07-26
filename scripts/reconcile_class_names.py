"""reconcile_class_names — find and heal class types whose diary name drifted from their service.

A class service is two linked rows: billing.product (the "service" the editor renames) and
diary.resource (the "class type" the diary lists + schedules). They link by diary.resource.product_id.
Renaming in the service editor USED TO change the product only, so the diary kept the old name and,
because the diary list joined by name, showed a blank price/length. The code now (a) resolves the
diary list by product_id and (b) syncs the resource name on rename — but classes renamed BEFORE that
fix are still drifted. This heals them.

    python -m scripts.reconcile_class_names            # dry run — report only (default)
    python -m scripts.reconcile_class_names --commit   # apply the SAFE fixes

Two SAFE fixes are applied with --commit:
  1. LINKED-BUT-DRIFTED — resource.product_id points at an active product whose name differs:
     set resource.name = product.name (the service name is the source of truth).
  2. UNLINKED-BUT-UNAMBIGUOUS — resource.product_id IS NULL and EXACTLY ONE active class product
     matches by (name, coach): pin resource.product_id to it.

AMBIGUOUS cases are REPORTED, never guessed: a resource linked to a terminated product while a new
active product exists (is the new one the same class, or a genuinely new one?) is a human call.
Read-only until --commit.
"""
import sys

from sqlalchemy import text

from db import session_scope

REPORT = """
SELECT r.id AS resource_id, r.club_id, r.name AS resource_name, r.product_id,
       r.coach_user_id,
       p.id AS linked_product_id, p.name AS linked_product_name, p.active AS linked_active,
       (SELECT count(*) FROM diary.class_session cs
          WHERE cs.resource_id = r.id AND cs.status = 'scheduled' AND cs.starts_at >= now())
          AS upcoming
FROM diary.resource r
LEFT JOIN billing.product p ON p.id = r.product_id
WHERE r.kind = 'class' AND r.is_active = true
ORDER BY r.club_id, r.name
"""

# Active class products matching an UNLINKED resource by (name, coach).
MATCH = """
SELECT id, name FROM billing.product
WHERE club_id = :c AND kind = 'class' AND active = true
  AND lower(name) = lower(:n)
  AND coach_user_id IS NOT DISTINCT FROM :coach
"""

# Any OTHER active class product for this coach (to flag the ambiguous "new class?" case).
OTHER_ACTIVE = """
SELECT id, name FROM billing.product
WHERE club_id = :c AND kind = 'class' AND active = true
  AND coach_user_id IS NOT DISTINCT FROM :coach
"""


def main(argv):
    commit = "--commit" in argv
    drifted = pinned = ambiguous = ok = 0

    with session_scope() as s:
        rows = [dict(r) for r in s.execute(text(REPORT)).mappings().all()]
        print("%d active class type(s)\n" % len(rows))
        for r in rows:
            rn, pn = r["resource_name"], r["linked_product_name"]
            tag = ""
            if r["product_id"] and r["linked_active"] and pn and pn.strip().lower() != (rn or "").strip().lower():
                # (1) linked-but-drifted → sync the resource name to the service.
                tag = "DRIFTED  '%s' (diary) -> '%s' (service)" % (rn, pn)
                drifted += 1
                if commit:
                    s.execute(text("UPDATE diary.resource SET name = :n, updated_at = now() WHERE id = :r"),
                              {"n": pn, "r": r["resource_id"]})
            elif r["product_id"] and not r["linked_active"]:
                # linked to a terminated/deactivated product — check for a live replacement.
                others = [o for o in s.execute(text(OTHER_ACTIVE),
                          {"c": r["club_id"], "coach": r["coach_user_id"]}).mappings().all()
                          if str(o["id"]) != str(r["product_id"])]
                if others:
                    tag = ("AMBIGUOUS  linked to INACTIVE '%s'; active alternative(s): %s "
                           "-> relink is a human call" % (pn, ", ".join(o["name"] for o in others)))
                    ambiguous += 1
                else:
                    tag = "linked to INACTIVE '%s' (no active alternative) — leave as-is" % pn
            elif not r["product_id"]:
                # (2) unlinked → pin if exactly one (name,coach) match.
                m = s.execute(text(MATCH), {"c": r["club_id"], "n": rn,
                                            "coach": r["coach_user_id"]}).mappings().all()
                if len(m) == 1:
                    tag = "UNLINKED -> pin to '%s'" % m[0]["name"]
                    pinned += 1
                    if commit:
                        s.execute(text("UPDATE diary.resource SET product_id = :p, updated_at = now() "
                                       "WHERE id = :r"), {"p": m[0]["id"], "r": r["resource_id"]})
                elif len(m) == 0:
                    tag = "UNLINKED, no name match — human call"
                    ambiguous += 1
                else:
                    tag = "UNLINKED, %d name matches — human call" % len(m)
                    ambiguous += 1
            else:
                ok += 1
                tag = "ok"

            print("  [%s] %-28s coach=%s upcoming=%s  %s" % (
                "FIX " if tag.split()[0] in ("DRIFTED", "UNLINKED") else "    ",
                (rn or "?")[:28], str(r["coach_user_id"])[:8] if r["coach_user_id"] else "-",
                r["upcoming"], tag))

        print("\n%d drifted, %d unlinked-pinnable, %d ambiguous (human), %d already ok"
              % (drifted, pinned, ambiguous, ok))
        if not commit and (drifted or pinned):
            print(">>> DRY RUN — re-run with --commit to apply the %d safe fix(es)." % (drifted + pinned))
        elif commit:
            print(">>> Applied. Ambiguous rows were NOT touched — resolve those in the UI.")
        else:
            print(">>> Nothing to auto-fix.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

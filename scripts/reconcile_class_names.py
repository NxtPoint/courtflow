"""reconcile_class_names — find and heal class types whose diary name drifted from their service.

A class service is two linked rows: billing.product (the "service" the editor renames) and
diary.resource (the "class type" the diary lists + schedules). They link by diary.resource.product_id.
Renaming in the service editor USED TO change the product only, so the diary kept the old name and,
because the diary list joined by name, showed a blank price/length. The code now (a) resolves the
diary list by product_id and (b) syncs the resource name on rename — but classes renamed BEFORE that
fix are still drifted. This heals them.

    python -m scripts.reconcile_class_names               # dry run — report only (default)
    python -m scripts.reconcile_class_names --commit      # apply the SAFE fixes
    python -m scripts.reconcile_class_names --link-orphans # ALSO pair a renamed class to its service

Two SAFE fixes are applied with --commit:
  1. LINKED-BUT-DRIFTED — resource.product_id points at an active product whose name differs:
     set resource.name = product.name (the service name is the source of truth).
  2. UNLINKED-BUT-UNAMBIGUOUS — resource.product_id IS NULL and EXACTLY ONE active class product
     matches by (name, coach): pin resource.product_id to it.

--link-orphans additionally pairs a RENAMED class back to its service: an unlinked class resource
whose name no longer matches ANY product, where the coach has EXACTLY ONE active class product that
no resource points to. That is the Allon case — "Cardio Tennis" (33 sessions, no product) + "Cardio
Bootcamp Tennis" (a service with no class type). It links them, renames the resource to the service,
and reprices FUTURE unpriced sessions to the service's active price so members can enrol. It acts
ONLY when the pairing is 1:1 for that coach; anything ambiguous is still left for a human. It crosses
a rename, so it is a separate, explicit flag — not part of --commit.

AMBIGUOUS cases are REPORTED, never guessed. Read-only until --commit / --link-orphans.
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

# Active class products for this coach that NO resource links to — a "service with no class type",
# the other half of a renamed-and-split class.
ORPHAN_PRODUCTS = """
SELECT p.id, p.name,
       (SELECT amount_minor FROM billing.price pr WHERE pr.product_id = p.id AND pr.active = true
          ORDER BY created_at LIMIT 1) AS price,
       (SELECT id FROM billing.price pr WHERE pr.product_id = p.id AND pr.active = true
          ORDER BY created_at LIMIT 1) AS price_id
FROM billing.product p
WHERE p.club_id = :c AND p.kind = 'class' AND p.active = true
  AND p.coach_user_id IS NOT DISTINCT FROM :coach
  AND NOT EXISTS (SELECT 1 FROM diary.resource r WHERE r.product_id = p.id)
"""


def _link_orphan(session, *, resource_id, product, do_commit):
    """Link an unlinked resource to an orphan product: pin product_id, rename the resource to the
    service, and reprice FUTURE unpriced sessions to the service's active price. Returns a summary."""
    repriced = 0
    if do_commit:
        session.execute(
            text("UPDATE diary.resource SET product_id = :p, name = :n, updated_at = now() WHERE id = :r"),
            {"p": product["id"], "n": product["name"], "r": resource_id},
        )
        if product["price_id"]:
            repriced = session.execute(
                text("UPDATE diary.class_session SET price_id = :pr, updated_at = now() "
                     "WHERE resource_id = :r AND status = 'scheduled' AND starts_at >= now() "
                     "AND price_id IS NULL"),
                {"pr": product["price_id"], "r": resource_id},
            ).rowcount
    else:
        repriced = session.execute(
            text("SELECT count(*) FROM diary.class_session WHERE resource_id = :r "
                 "AND status = 'scheduled' AND starts_at >= now() AND price_id IS NULL"),
            {"r": resource_id},
        ).scalar()
    return repriced


def main(argv):
    commit = "--commit" in argv
    link_orphans = "--link-orphans" in argv
    drifted = pinned = ambiguous = ok = linked = 0

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
                    # No product shares this resource's (renamed-away) name. Look for the other half:
                    # an active class product for this coach that no resource links to.
                    orphans = s.execute(text(ORPHAN_PRODUCTS),
                                        {"c": r["club_id"], "coach": r["coach_user_id"]}).mappings().all()
                    if len(orphans) == 1:
                        op = dict(orphans[0])
                        n = _link_orphan(s, resource_id=r["resource_id"], product=op,
                                         do_commit=link_orphans)
                        priced = ("R%.2f" % ((op["price"] or 0) / 100.0)) if op["price"] else "no price!"
                        if link_orphans:
                            tag = "LINKED -> '%s' (%s); repriced %d future session(s)" % (op["name"], priced, n)
                            linked += 1
                        else:
                            tag = ("ORPHAN-PAIR -> would link to service '%s' (%s) + reprice %d future "
                                   "session(s)  [needs --link-orphans]" % (op["name"], priced, n))
                            ambiguous += 1
                    else:
                        tag = ("UNLINKED, no name match; %d orphan service(s) for this coach — human call"
                               % len(orphans))
                        ambiguous += 1
                else:
                    tag = "UNLINKED, %d name matches — human call" % len(m)
                    ambiguous += 1
            else:
                ok += 1
                tag = "ok"

            flag = tag.split()[0]
            marker = "FIX " if flag in ("DRIFTED", "UNLINKED", "ORPHAN-PAIR", "LINKED") else "    "
            print("  [%s] %-28s coach=%s upcoming=%s  %s" % (
                marker, (rn or "?")[:28],
                str(r["coach_user_id"])[:8] if r["coach_user_id"] else "-",
                r["upcoming"], tag))

        print("\n%d drifted, %d unlinked-pinnable, %d linked, %d ambiguous (human), %d already ok"
              % (drifted, pinned, linked, ambiguous, ok))
        if not commit and not link_orphans and (drifted or pinned):
            print(">>> DRY RUN — re-run with --commit to apply the %d safe fix(es)." % (drifted + pinned))
        if not link_orphans:
            print(">>> If an ORPHAN-PAIR is shown above (a renamed class + its lone service), re-run")
            print("    with --link-orphans to link them, rename the class, and reprice its sessions.")
        else:
            print(">>> Applied. Ambiguous rows were NOT touched — resolve those in the UI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

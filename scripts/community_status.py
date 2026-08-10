"""community_status — READ-ONLY: why is Find a Game not showing?

Run in the **courtflow-api → Shell** tab (DATABASE_URL is already in that environment, so no
credential ever leaves Render — see docs/specs/DATA-ACCESS.md):

    python -m scripts.community_status

It answers, in order, the questions that actually gate the feature appearing on the member's Home:

  1. Do the community columns EXIST on club.policy? (i.e. did `python -m db` run the community
     schema on this database at all — if the boot DDL never ran, every flag reads as absent and the
     admin screen would have had nothing to write to.)
  2. What are the flags set to, per club?
  3. Is there anything to see yet — open games, invites, discoverable players?

Writes nothing. Prints no secrets. Safe to run at any time, including during a match.
"""
import sys

from sqlalchemy import text

from db import session_scope

_COLS = ("community_enabled", "seat_rule_enforced", "open_game_cutoff_hours",
         "seat_pay_hours", "guest_trial_days", "seat_share_pct", "seat_rounding")


def main():
    with session_scope() as s:
        # 1) Does the schema even exist here? A missing column is a completely different problem
        # from a false flag, and they look identical from the browser.
        have = {r[0] for r in s.execute(text(
            "SELECT column_name FROM information_schema.columns "
            " WHERE table_schema = 'club' AND table_name = 'policy'")).all()}
        missing = [c for c in _COLS if c not in have]
        print("=== 1. schema ===")
        if missing:
            print("  MISSING COLUMNS on club.policy:", ", ".join(missing))
            print("  -> the community boot DDL has NOT run on this database.")
            print("     The API applies it on boot (db.BOOT_MODULES), so this means the service has")
            print("     not restarted since the deploy, or the boot init failed. Check the deploy log")
            print("     for 'boot init ok: community.schema'.")
            return 1
        print("  ok - all community columns present on club.policy")

        has_community_schema = s.execute(text(
            "SELECT count(*) FROM information_schema.schemata WHERE schema_name = 'community'"),
        ).scalar()
        print(f"  community.* schema present: {'yes' if has_community_schema else 'NO'}")

        # 2) The flags, per club. This is the answer 9 times out of 10.
        print("\n=== 2. flags (per club) ===")
        rows = s.execute(text(
            "SELECT c.name, c.slug, p.community_enabled, p.seat_rule_enforced, "
            "       p.seat_share_pct, p.seat_rounding, p.open_game_cutoff_hours, p.seat_pay_hours "
            "  FROM club.club c LEFT JOIN club.policy p ON p.club_id = c.id "
            " ORDER BY c.name")).mappings().all()
        if not rows:
            print("  no clubs found (?)")
            return 1
        for r in rows:
            if r["community_enabled"] is None:
                print(f"  {r['name']}: NO club.policy ROW — nothing has ever been saved for this club.")
                continue
            print(f"  {r['name']} ({r['slug']}):")
            print(f"      community_enabled  = {r['community_enabled']}"
                  + ("   <-- must be True for anything to show on Home"
                     if not r["community_enabled"] else "   ok"))
            print(f"      seat_rule_enforced = {r['seat_rule_enforced']}"
                  + ("   (money rule OFF - expected until you switch it)"
                     if not r["seat_rule_enforced"] else "   <-- MEMBERS ARE BEING CHARGED PER SEAT"))
            print(f"      share {r['seat_share_pct']}% rounded {r['seat_rounding']}"
                  f" · seat closes {r['open_game_cutoff_hours']}h before · {r['seat_pay_hours']}h to pay")

        # 3) Is there anything to look at yet?
        print("\n=== 3. content ===")
        for label, sql in (
            ("open games (upcoming)",
             "SELECT count(*) FROM diary.booking WHERE visibility = 'open' "
             "  AND status IN ('held','confirmed') AND starts_at > now()"),
            ("seated bookings (any)",
             "SELECT count(*) FROM diary.booking WHERE seats IS NOT NULL"),
            ("live invitations",
             "SELECT count(*) FROM community.player_invite WHERE status = 'sent'"),
            ("players who opted in",
             "SELECT count(*) FROM iam.player_profile WHERE visible_in_community = true"),
            ("players with a level",
             "SELECT count(*) FROM iam.player_profile WHERE level_num IS NOT NULL"),
        ):
            try:
                print(f"  {label:24} {s.execute(text(sql)).scalar()}")
            except Exception as e:
                print(f"  {label:24} (unreadable: {type(e).__name__})")

        print("\n=== what to do ===")
        on = any(r["community_enabled"] for r in rows if r["community_enabled"] is not None)
        if not on:
            print("  community_enabled is FALSE everywhere. The Home card is gated on it, so nothing")
            print("  will show. Admin -> Setup -> Community & games -> tick 'Community features'.")
            print("  If you already ticked it and this still says False, the save did not reach the")
            print("  database — say so and I'll look at the write path rather than the read path.")
        else:
            print("  community_enabled is TRUE. The Home card should render. If it does not, the")
            print("  problem is in the browser, not the data — open DevTools -> Console on the member")
            print("  Home and send me any red error, plus the Network row for /api/community/config.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

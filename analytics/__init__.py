# analytics/ — platform-owner "Business Overview" dashboard (read-only, independent lane).
#
# A platform_admin (or a club_admin for their own club) view over data that already exists:
#   - website traffic from core.usage_event (event_type='page_view': visits, unique visitors via
#     anon_id, new-vs-returning, traffic source from utm/referrer, top pages, country from the
#     geo header the beacon now captures)
#   - customers + signups from core.account
#   - bookings + revenue + settlement mix from diary.* / billing.*
#   - NPS from core.nps_response
#
# NOTHING here mutates; every aggregation is a guarded SELECT (a missing/empty table yields an
# empty panel, never a 500). All queries are club-scoped when a club_id is given (platform_admin
# may pass ?club_id= to filter, or omit it for platform-wide). Designed so a future "1050" column
# can be bridged in by a separate read endpoint without touching this core.
#
# Wiring: app.py registers analytics.routes:analytics_bp (one line, try/except-wrapped).

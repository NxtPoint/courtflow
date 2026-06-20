# coach — the coach self-service write APIs (what a logged-in COACH does: set their own
# profile, weekly working hours, and lesson services/rates). Blueprint coach_bp serves
# /api/coach/*, gated to coach + club_admin + platform_admin. A coach only ever sees/edits
# THEIR OWN data (scoped by principal.user_id); club_admin/platform_admin may act on their
# own coach_profile too. Owns ONLY the coach.* surface; any schema additions live in
# coach/schema.py as idempotent ALTER/CREATE (never edits another lane's schema module).
#
# Public surface:
#   coach.schema.init(engine=None)   — idempotent boot DDL (registered in db.BOOT_MODULES)
#   coach.routes.coach_bp            — the Flask blueprint (registered in app.py)

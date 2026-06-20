# admin — the owner self-service write APIs (the save/edit layer behind onboarding +
# club settings). Blueprint admin_bp serves /api/admin/*, gated to club_admin +
# platform_admin. Owns ONLY the admin.* surface; any schema additions live in
# admin/schema.py as idempotent ALTER/CREATE (never edits another lane's schema module).
#
# Public surface:
#   admin.schema.init(engine=None)   — idempotent boot DDL (registered in db.BOOT_MODULES)
#   admin.routes.admin_bp            — the Flask blueprint (registered in app.py)

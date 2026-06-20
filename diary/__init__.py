# diary — the booking engine (the heart of CourtFlow). Agent B's lane.
#
# One diary, many lenses (docs/03 §1): court / lesson / class bookings are all
# diary.booking rows sharing one lifecycle (held -> confirmed -> completed|cancelled|
# no_show). The crown jewel is the GiST exclusion constraint on diary.booking that makes
# double-booking physically impossible (docs/02 §8, docs/03 §4).
#
# Public surface (what the orchestrator wires — see this lane's report):
#   diary.schema.init(engine=None)   -> append "diary.schema" to db.BOOT_MODULES
#   diary.routes.diary_bp            -> app.register_blueprint(diary_bp) (already auto-wired
#                                       by app._try_register("diary.routes", "diary_bp"))
#   diary.routes.cron_bp             -> app.register_blueprint(cron_bp)  (cron endpoints)
#
# Cross-lane calls are LAZY + GUARDED so this lane self-verifies without C/D present:
#   billing.orders.create_order_for_booking(...)   (Agent C)  — diary/bookings.py
#   marketing_crm.tracking.emit(event, payload)    (Agent D)  — diary/events.py

__all__ = ["schema"]

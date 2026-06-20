# billing — provider-agnostic payment core + settlement modes (Agent C lane).
#
# Public surface (what other lanes / app.py use):
#   billing.schema.init(engine)               -> idempotent boot DDL (registered in db.BOOT_MODULES)
#   billing.routes.billing_bp                 -> Flask blueprint (registered in app.py)
#   billing.orders.create_order_for_booking() -> the interface Agent B (diary) calls
#   billing.events.apply_payment_event()      -> the single grant/settlement path (provider-independent)
#
# Launch ships WITHOUT a payment gateway (docs/05 §9, decision D8): the at_court /
# monthly_account / membership_covered / free settlement modes + the ManualGateway
# (desk/cash/card/eft) cover MVP. Yoco/PayPal adapters are Phase 7 and slot in behind
# the same PaymentGateway protocol with NO change to the core grant path.

__all__ = ["schema", "gateway", "events", "orders", "ledger", "routes"]

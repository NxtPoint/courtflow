# yoco_billing/ — the Yoco payment gateway adapter (docs/05 §6).
#
# Implements billing.gateway.PaymentGateway over Yoco's hosted Checkout API + Standard-
# Webhooks. The core settlement path (billing.events.apply_payment_event) is untouched; this
# package only translates Yoco's REST/webhooks into the normalized wire shapes and exposes
# /api/billing/yoco/* routes.
#
# Wiring: app.py does _try_register(app, "yoco_billing.routes", "yoco_bp"), which imports
# yoco_billing.routes -> yoco_billing.adapter, and the adapter calls register_gateway("yoco").
#
# Provider-agnostic by design: a future provider (FastPay, PayPal) is another package
# implementing the same Protocol — the core never changes.

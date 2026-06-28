# services/ — the unified SERVICE lane (golden rule: ONE place a service is defined + edited).
#
# A "service" = a billing.product (kind court_booking|lesson|class) with everything that makes it
# work hanging off it: variations (per-duration billing.price), payment preference (product.payment_modes),
# packages (billing.bundle_plan), and commission (billing.commission_rule). This lane exposes ONE API
# (/api/services/*) that BOTH the owner and the coach call — the route enforces who may change what
# (the coach edits everything EXCEPT commission). No schema of its own (it composes billing.*).

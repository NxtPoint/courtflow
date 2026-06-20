# billing/gateway.py — the provider-agnostic payment gateway interface (docs/05 §2).
#
# ONE grant path, MANY providers. Core code (billing.events.apply_payment_event) consumes
# ONLY the NORMALIZED shape (NormalizedPaymentEvent) — no provider specifics leak in.
# Each provider is a thin adapter implementing the PaymentGateway Protocol.
#
# Shipped now:
#   - PaymentGateway        Protocol (the contract every adapter satisfies)
#   - NormalizedPaymentEvent / CheckoutIntent / RefundResult  dataclasses (the wire shapes)
#   - ManualGateway         concrete provider for desk settlement (cash / card_at_desk / eft) —
#                           so LAUNCH works with NO external provider (decision D8).
#
# OUT OF SCOPE (Phase 7, docs/05 §6-§7): the Yoco and PayPal adapters. They will live in
# their own packages (yoco_billing/, paypal_billing/) and implement this same Protocol; the
# core never changes when they land. The extension point is `register_gateway()` +
# `get_gateway()` below — an adapter calls register_gateway(name, instance) on import.

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, runtime_checkable

# NormalizedPaymentEvent.kind — the only event kinds the core grant path understands.
EVENT_KINDS = (
    "charge_succeeded",
    "charge_failed",
    "refunded",
    "subscription_active",
    "subscription_cancelled",
)

# Manual (desk) provider labels — money taken at the club, no external gateway.
MANUAL_PROVIDERS = ("cash", "card_at_desk", "eft", "manual")


# ---------------------------------------------------------------------------
# Wire shapes (what flows between adapters and the core)
# ---------------------------------------------------------------------------

@dataclass
class CheckoutIntent:
    """Returned by create_checkout — what the frontend needs to start a payment."""
    provider: str
    intent_id: str
    redirect_url: Optional[str] = None      # hosted-checkout redirect (Yoco/PayPal)
    client_token: Optional[str] = None      # or a popup/client token
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedPaymentEvent:
    """The provider-independent event the core consumes (docs/05 §2). Every adapter's
    parse_event() returns THIS; apply_payment_event() never sees provider specifics."""
    provider: str                           # 'yoco'|'paypal'|'cash'|'card_at_desk'|'eft'|'manual'
    kind: str                               # one of EVENT_KINDS
    order_ref: Optional[str] = None         # our billing.order.id (set as metadata at checkout)
    provider_payment_id: Optional[str] = None
    amount_minor: int = 0
    currency: Optional[str] = None
    status: str = "succeeded"               # provider's terminal status, normalized
    direction: str = "charge"               # 'charge' | 'refund'
    # subscription events carry these:
    provider_subscription_id: Optional[str] = None
    user_id: Optional[str] = None
    price_id: Optional[str] = None
    current_period_end: Optional[str] = None   # ISO date string
    club_id: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def event_hash(self) -> str:
        """Deterministic idempotency key (1050 sha256 pattern). A replay of the same
        provider event hashes identically, so payment_attempt(event_hash) dedupes it."""
        key = "|".join([
            str(self.provider or "").strip().lower(),
            str(self.kind or "").strip().lower(),
            str(self.order_ref or "").strip(),
            str(self.provider_payment_id or "").strip(),
            str(self.provider_subscription_id or "").strip(),
            str(self.amount_minor or 0),
            str(self.direction or "charge"),
            str(self.status or "").strip().lower(),
        ])
        return hashlib.sha256(key.encode("utf-8")).hexdigest()


@dataclass
class RefundResult:
    provider: str
    provider_refund_id: Optional[str]
    amount_minor: int
    status: str = "succeeded"
    raw: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# The Protocol every provider implements (docs/05 §2)
# ---------------------------------------------------------------------------

@runtime_checkable
class PaymentGateway(Protocol):
    name: str  # 'yoco' | 'paypal' | 'manual'

    def create_checkout(self, *, order, success_url: str, cancel_url: str) -> CheckoutIntent: ...
    def verify_webhook(self, request) -> bool: ...
    def parse_event(self, payload) -> NormalizedPaymentEvent: ...
    def refund(self, *, payment, amount_minor: int) -> RefundResult: ...


# ---------------------------------------------------------------------------
# ManualGateway — the desk/cash provider (launch needs no external gateway)
# ---------------------------------------------------------------------------

class ManualGateway:
    """Desk settlement: an admin records a payment taken in person (cash / card at the
    desk / EFT). There is no redirect, no webhook signature — the 'checkout' is the desk,
    and the 'event' is built directly from the recorded payment. Satisfies PaymentGateway
    so the SAME apply_payment_event core handles a desk payment exactly like a gateway one.

    `provider` selects the money-source label written to billing.payment.provider."""

    def __init__(self, provider: str = "cash"):
        provider = (provider or "cash").strip().lower()
        if provider not in MANUAL_PROVIDERS:
            provider = "cash"
        self.name = provider

    def create_checkout(self, *, order, success_url: str = "", cancel_url: str = "") -> CheckoutIntent:
        """No external checkout — settlement happens at the desk. Returned only for
        interface symmetry; callers use record_desk_payment() (billing.orders) instead."""
        oid = _order_id(order)
        return CheckoutIntent(provider=self.name, intent_id=f"manual:{oid}", redirect_url=None,
                              client_token=None, extra={"order_id": oid, "mode": "desk"})

    def verify_webhook(self, request) -> bool:
        # No webhooks for desk payments; authorization is the admin's role (iam.permissions).
        return True

    def parse_event(self, payload) -> NormalizedPaymentEvent:
        """Build a normalized charge_succeeded event from a desk-payment dict. Lets a desk
        payment flow through the same apply_payment_event path as a gateway charge."""
        payload = payload or {}
        provider = (payload.get("provider") or self.name or "cash").strip().lower()
        if provider not in MANUAL_PROVIDERS:
            provider = "cash"
        return NormalizedPaymentEvent(
            provider=provider,
            kind="charge_succeeded",
            order_ref=str(payload.get("order_ref") or payload.get("order_id") or "") or None,
            provider_payment_id=(payload.get("provider_payment_id") or None),
            amount_minor=int(payload.get("amount_minor") or 0),
            currency=(payload.get("currency") or payload.get("currency_code") or None),
            status="succeeded",
            direction="charge",
            club_id=(payload.get("club_id") or None),
            raw=dict(payload),
        )

    def refund(self, *, payment, amount_minor: int) -> RefundResult:
        """Record-only desk refund (cash back / card reversal at the desk). No auto-reverse
        of the booking (docs/05 §8)."""
        return RefundResult(provider=self.name, provider_refund_id=None,
                            amount_minor=int(amount_minor or 0), status="succeeded")


def _order_id(order) -> str:
    if order is None:
        return ""
    if isinstance(order, dict):
        return str(order.get("id") or order.get("order_id") or "")
    return str(getattr(order, "id", "") or getattr(order, "order_id", ""))


# ---------------------------------------------------------------------------
# Gateway registry — the extension point for Yoco/PayPal (Phase 7)
# ---------------------------------------------------------------------------
# An adapter registers itself on import: register_gateway('yoco', YocoGateway()).
# Core code asks for one by name; nothing here knows a provider's internals.
_REGISTRY: Dict[str, PaymentGateway] = {}


def register_gateway(name: str, gateway: PaymentGateway) -> None:
    _REGISTRY[(name or "").strip().lower()] = gateway


def get_gateway(name: str) -> Optional[PaymentGateway]:
    """Return a registered gateway by name. Manual providers are always available
    (constructed on demand) so desk settlement never depends on registration."""
    name = (name or "").strip().lower()
    if name in MANUAL_PROVIDERS:
        return ManualGateway(provider=name)
    return _REGISTRY.get(name)

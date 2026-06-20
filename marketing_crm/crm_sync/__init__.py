# marketing_crm.crm_sync — push core.* → Klaviyo (the marketing/lifecycle feed).
#
# core.* is the source of truth; Klaviyo is a one-way downstream mirror. This module is the data
# feed the Klaviyo flows depend on: profile traits (incl. the per-club `club` trait) and forwarded
# events (Klaviyo metrics → flow triggers).
#
# Self-gating: everything no-ops unless KLAVIYO_API_KEY is set. Privacy boundary (hard,
# contracts/events.md): never sync minor PII; marketing events forward only when marketing_opt_in
# is true (transactional booking comms always send). HubSpot stays dormant.

from marketing_crm.crm_sync.sync import (  # noqa: F401
    enabled, build_traits, sync_profile, forward_event, sync_all,
)

__all__ = ["enabled", "build_traits", "sync_profile", "forward_event", "sync_all"]

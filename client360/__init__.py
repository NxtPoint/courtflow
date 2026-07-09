# client360 — the single cross-lane read-model for a "Client 360".
#
# This lane owns ONE composer, get_client_360, that every client-facing view (admin person-360,
# coach client record, client self-service) derives from. It is READ-ONLY and reuse-first: it
# calls the existing lane readers (billing.statement / membership / bundles / commission / refunds
# / activity, diary bookings, core notifications, iam dependents) rather than re-querying raw
# domain tables when a reader exists. Every block is guarded so a partial DB degrades to empty
# instead of 500ing, and every query is club_id-scoped (multi-tenant discipline).
#
# admin.repositories.get_person delegates here (scope='admin') so there is exactly ONE assembly.

from .repositories import get_client_360

__all__ = ["get_client_360"]

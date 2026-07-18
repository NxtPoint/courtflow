# marketing_crm/repermission — the one-off re-permission opt-in flow.
#
# The ~500 existing customers who never gave marketing consent can't be marketed to (POPIA). This
# flow sends them ONE service-framed notice ("NextPoint has moved to a new app — want to keep hearing
# from us?") via our own SES; a tapped "Yes, keep me posted" lands on the no-login /subscribe page,
# which writes marketing consent to our DB AND subscribes them to the Klaviyo list (→ Welcome flow) —
# graduating them into the marketable pool. See docs/specs/KLAVIYO-MASTER-PLAN.md §5.
#
# Public API:
#   optin_url_for(iam_user_id, club_id) -> an absolute, signed /subscribe URL (put it in the email CTA)
# The send is scripts/repermission_campaign.py; the endpoint + page do the opt-in.

from marketing_crm.repermission.tokens import optin_url_for  # noqa: F401

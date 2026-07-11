# offline_conversions/ — Google Ads offline-conversion CSV feed (gclid → paying customer).
#
# SHARED, PORTABLE PACKAGE — kept in lock-step across the CourtFlow (nextpoint) and ten-fifty5 repos,
# exactly like the analytics beacon engine. It closes the loop opened by the gclid capture on
# core.acquisition: when a gclid'd visitor becomes a PAYING customer, we ledger the conversion and
# expose it as a Google Ads "scheduled upload" CSV — teaching Ads to bid for people who actually pay,
# not just click. NO developer token / manager account needed (that's the whole point of the CSV
# route): Google fetches the feed over HTTPS on a schedule.
#
# Pieces:
#   schema.py    core.offline_conversion table (raw DDL; registered in db.BOOT_MODULES).
#   recorder.py  record_from_emit(): hook in the shared emit() funnel; money-event → conversion row.
#   feed.py      build_csv(): pure Google-Ads-format renderer.
#   blueprint.py GET /feeds/google-ads/offline-conversions.csv (HTTP Basic auth; dark until env set).
#
# Per-repo glue is ONLY recorder.CONVERSION_MAP (which events are conversions + their value keys).

from offline_conversions.blueprint import register, offline_conv_bp  # noqa: F401

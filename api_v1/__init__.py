# api_v1/ — CourtFlow's PUBLIC, versioned API (docs/specs/PUBLIC-API.md). Court hire first.
#
# A thin layer over the lanes' domain functions: it adds NO booking or money rule of its own. What it
# adds is the contract — the club in the URL, one error shape, idempotent writes, money as
# {amount_minor, currency} — and the promise not to break it within v1.

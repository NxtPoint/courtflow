# frontend/_shared/branding.py — per-club branding resolver for courtflow-web.
#
# courtflow-web has NO DATABASE (see render.yaml: it only installs flask/gunicorn/
# requests). So branding is resolved from a PLUGGABLE source, parameterised by the
# request host:
#
#   1) A small static per-club registry (CLUBS below) — enough to theme NextPoint
#      (club #1) today with zero infra.
#   2) MARKETING_HOSTS env override — lets ops add/repoint a host without a deploy
#      (mirrors 1050's MARKETING_HOSTS pattern).
#
# THE SEAM (documented, deliberately not wired yet): when the platform has many
# clubs, replace `resolve_branding(host)` internals with a cached fetch of
# `club.branding` from courtflow-api (e.g. GET {AUTH_API_BASE}/api/public/branding
# ?host=<host>), keyed by host, TTL-cached in-process. The function signature and
# the returned Branding shape stay identical, so web_app.py never changes. The
# fetch helper stub is `_fetch_branding_from_api()` below.
#
# A Branding is the white-label surface from docs/02 §2 (club.branding):
#   slug, name, primary_color, accent_color, logo_url, favicon_url, domain,
#   marketing_hosts, og_image_url  (+ NAP/contact for schema.org LocalBusiness).

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Branding:
    slug: str
    name: str
    primary_color: str = "#0B5D1E"
    accent_color: str = "#F2C200"
    ink: str = "#13241b"          # text colour
    logo_url: Optional[str] = None
    favicon_url: str = "/favicon.svg"
    domain: str = ""
    marketing_hosts: tuple = ()
    og_image_url: Optional[str] = None
    # Contact / NAP — powers schema.org LocalBusiness + footer (docs/00 §3).
    legal_name: str = ""
    address_line: str = ""
    city: str = ""
    postal_code: str = ""
    country: str = "South Africa"
    phone: str = ""
    email: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    currency_code: str = "ZAR"
    locale: str = "en-ZA"
    # Where the logged-in app lives (CTAs point here). Empty = same-origin app paths.
    app_base: str = ""


# --- Static per-club registry (the "config not code" source for now) ---------
# Values mirror scripts/seed_nextpoint.py BRANDING + docs/00 §3 contact block so
# the public site matches the seeded club row exactly.
NEXTPOINT = Branding(
    slug="nextpoint",
    name="NextPoint Tennis",
    legal_name="NextPoint Tennis",
    primary_color="#0B5D1E",
    accent_color="#F2C200",
    ink="#13241b",
    domain="nextpointtennis.com",
    marketing_hosts=("nextpointtennis.com", "www.nextpointtennis.com"),
    logo_url="/img/logo.webp",   # real NextPoint brandmark (nav); footer uses the text name
    favicon_url="/favicon.svg",
    address_line="Killarney Country Club, 60 5th Street, Houghton Estate",
    city="Johannesburg",
    postal_code="2191",
    country="South Africa",
    phone="076 990 7439",
    email="info@nextpointtennis.com",
    currency_code="ZAR",
    locale="en-ZA",
    app_base="",   # same-origin app paths (/portal, /book, ...)
)

# Registry keyed by slug. New club = add a Branding here (or, later, it comes from
# the API). Keep NextPoint first — it's the default fallback club.
CLUBS = {
    "nextpoint": NEXTPOINT,
}

DEFAULT_CLUB = NEXTPOINT


def _env_marketing_hosts() -> set:
    """Extra marketing hosts from the env (comma-separated), ops override."""
    raw = os.environ.get("MARKETING_HOSTS", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _host_index() -> dict:
    """Build {host -> Branding} from the registry. Env MARKETING_HOSTS attach to
    the DEFAULT club (single-tenant convenience; multi-tenant uses the API seam)."""
    idx = {}
    for b in CLUBS.values():
        for h in b.marketing_hosts:
            idx[h.lower()] = b
        if b.domain:
            idx[b.domain.lower()] = b
    for h in _env_marketing_hosts():
        idx.setdefault(h, DEFAULT_CLUB)
    return idx


def all_marketing_hosts() -> set:
    """Every host that should render the marketing site (registry + env)."""
    return set(_host_index().keys())


def _fetch_branding_from_api(host: str) -> Optional[Branding]:
    """SEAM (not active yet): fetch club.branding for `host` from courtflow-api.

    To activate multi-tenant branding without a redeploy of new clubs:
      base = os.environ.get("AUTH_API_BASE", "").rstrip("/")
      r = requests.get(f"{base}/api/public/branding", params={"host": host}, timeout=2)
      data = r.json()  # {slug,name,primary_color,...} mirroring club.branding
      return Branding(**_map(data))   # cache by host with a short TTL
    Returns None today so we fall through to the static registry."""
    return None


def resolve_branding(host: str) -> Branding:
    """Resolve the club Branding for a request host. Host is matched against the
    static registry first, then the API seam (disabled), then DEFAULT_CLUB. The
    returned shape is stable so web_app.py is source-agnostic."""
    h = (host or "").split(":")[0].strip().lower()
    idx = _host_index()
    if h in idx:
        return idx[h]
    api = _fetch_branding_from_api(h)
    if api is not None:
        return api
    return DEFAULT_CLUB


def is_marketing_host(host: str) -> bool:
    """True when the request host should see the public marketing site at `/`."""
    h = (host or "").split(":")[0].strip().lower()
    return h in all_marketing_hosts()


def theme_css_vars(b: Branding) -> str:
    """The per-club CSS custom properties injected into every served page so the
    shared design system (frontend/_shared/theme.css) reskins by config.
    These names are the DESIGN-SYSTEM CONTRACT consumed by Agent E + marketing."""
    return (
        ":root{"
        f"--primary:{b.primary_color};"
        f"--accent:{b.accent_color};"
        f"--ink:{b.ink};"
        "}"
    )

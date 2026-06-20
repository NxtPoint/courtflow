# frontend/_shared/chrome.py — shared nav + footer for marketing pages.
#
# Marketing pages drop two markers — <!--#include nav--> and <!--#include footer-->
# — and web_app._html() replaces them at serve time with the branded nav/footer
# below. This keeps every marketing page DRY (one nav/footer to maintain) and
# auto-themed per club, with NO build step and NO template engine.
#
# Portal SPA shells (frontend/app/, Agent E) do NOT use these markers; Agent E
# ships its own in-app chrome. Only public marketing pages include them.

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from frontend._shared.branding import Branding

NAV_MARKER = "<!--#include nav-->"
FOOTER_MARKER = "<!--#include footer-->"

# Public marketing nav links (label, href). Clean URLs from docs/07 §3.
_NAV_LINKS = [
    ("Courts", "/book/court"),
    ("Coaching", "/coaches"),
    ("Programs", "/programs/high-performance"),
    ("Classes", "/programs/cardio-tennis"),
    ("Pricing", "/pricing"),
    ("Blog", "/blog"),
    ("Contact", "/contact"),
]


def _logo(b: "Branding") -> str:
    if b.logo_url:
        return f'<img src="{b.logo_url}" alt="{b.name}">'
    return b.name


def nav_html(b: "Branding") -> str:
    links = "".join(f'<a href="{href}">{label}</a>' for label, href in _NAV_LINKS)
    # Highlight the active link client-side (matches by pathname).
    active_js = (
        "<script>(function(){try{var p=(location.pathname||'/').replace(/\\/+$/,'')||'/';"
        "document.querySelectorAll('.cf-nav-links a').forEach(function(a){"
        "var ap=new URL(a.href).pathname.replace(/\\/+$/,'')||'/';"
        "if(ap===p||(p.indexOf(ap)===0&&ap!=='/'))a.classList.add('active');});}catch(e){}})();</script>"
    )
    return f"""<nav class="cf-nav">
  <div class="cf-nav-inner">
    <a href="/" class="cf-logo">{_logo(b)}</a>
    <div class="cf-nav-links">{links}</div>
    <div class="cf-nav-right">
      <a href="/login" class="cf-nav-cta">Sign in</a>
      <button class="cf-nav-toggle" aria-label="Toggle menu" onclick="document.querySelector('.cf-nav-links').classList.toggle('open')">&#9776;</button>
    </div>
  </div>
</nav>{active_js}"""


def footer_html(b: "Branding") -> str:
    addr = ", ".join(x for x in (b.address_line, b.city, b.postal_code) if x)
    year = 2026
    return f"""<footer class="cf-footer">
  <div class="cf-footer-inner">
    <div class="cf-footer-brand">
      <div class="cf-footer-brand-name">{b.name}</div>
      <p>Court booking, coaching and classes at {b.city or 'our club'}. Book a court, a lesson with a named coach, or join Cardio Tennis and junior squads.</p>
      <p style="margin-top:10px">{addr}</p>
    </div>
    <div class="cf-footer-col"><h5>Book</h5><ul>
      <li><a href="/book/court">Book a court</a></li>
      <li><a href="/coaches">Book a lesson</a></li>
      <li><a href="/programs/cardio-tennis">Cardio Tennis</a></li>
      <li><a href="/programs/juniors">Junior squads</a></li>
      <li><a href="/free-lesson">Free lesson</a></li>
    </ul></div>
    <div class="cf-footer-col"><h5>Club</h5><ul>
      <li><a href="/services">Our courts</a></li>
      <li><a href="/programs/high-performance">High Performance</a></li>
      <li><a href="/pricing">Pricing</a></li>
      <li><a href="/careers">Careers</a></li>
      <li><a href="mailto:{b.email}">{b.email}</a></li>
      <li><a href="tel:{b.phone.replace(' ', '')}">{b.phone}</a></li>
    </ul></div>
  </div>
  <div class="cf-footer-bottom">
    <span>&copy; {year} {b.legal_name or b.name}. All rights reserved.</span>
    <span>{b.city}{', ' + b.country if b.country else ''}</span>
  </div>
</footer>"""


def apply_chrome(html: str, b: "Branding") -> str:
    """Replace the nav/footer markers in a marketing page with branded chrome."""
    if NAV_MARKER in html:
        html = html.replace(NAV_MARKER, nav_html(b))
    if FOOTER_MARKER in html:
        html = html.replace(FOOTER_MARKER, footer_html(b))
    return html

# web_app.py — courtflow-web: the host-switched public site + portal SPA shell.
#
# This is the `courtflow-web` Flask service (render.yaml entrypoint web_wsgi:app).
# It has NO DATABASE — it only serves static HTML/JS and resolves per-club branding
# from a pluggable host->Branding source (frontend/_shared/branding.py). Mirrors
# 1050's locker_room_app.py, made multi-tenant + parameterised by host.
#
# Two faces, switched by request host (frontend/_shared/branding.is_marketing_host):
#
#   MARKETING HOST (e.g. www.nextpointtennis.com):
#     /                  -> marketing home (native, crawlable HTML)
#     /services /coaches /programs/* /pricing /free-lesson /contact /careers
#     /blog /post/<slug> -> the static blog (build_blog.py output)
#     /robots.txt /sitemap.xml -> generated, host-aware
#
#   ANY HOST (app paths — these are the portal SPA shells Agent E builds):
#     /portal /book /book/<kind> /my /coach /admin /dashboard
#     /login             -> Clerk sign-in door (frontend/login.html, this lane)
#     /auth_client.js    -> shared auth helper (frontend/js/, Agent E owns content)
#     /app/<file>        -> SPA assets from frontend/app/ + frontend/js/ (Agent E)
#
# Theming: every served HTML page gets, injected into <head>:
#   - the per-club CSS theme vars (--primary/--accent/--ink) for this host,
#   - window.__API_BASE, window.__CLERK_PUBLISHABLE_KEY, window.__CLUB_SLUG.
# So one deploy serves every club; a new club reskins by config (or, later, by the
# branding.py API seam).
#
# Start command (render.yaml): gunicorn web_wsgi:app

import os
import glob
import json
from flask import Flask, send_file, jsonify, request, Response, abort

# The shared branding resolver lives in the design-system package. Support both
# running as a package and as a loose script (Render runs from repo root).
def _load_shared(modname, filename):
    import importlib, importlib.util
    try:
        return importlib.import_module(f"frontend._shared.{modname}")
    except ImportError:  # pragma: no cover - loose-script fallback
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "frontend", "_shared", filename)
        spec = importlib.util.spec_from_file_location(f"cf_{modname}", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

_branding_mod = _load_shared("branding", "branding.py")
_chrome_mod = _load_shared("chrome", "chrome.py")
Branding = _branding_mod.Branding
resolve_branding = _branding_mod.resolve_branding
is_marketing_host = _branding_mod.is_marketing_host
theme_css_vars = _branding_mod.theme_css_vars
apply_chrome = _chrome_mod.apply_chrome

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
MARKETING_DIR = os.path.join(FRONTEND_DIR, "marketing")
BLOG_DIR = os.path.join(FRONTEND_DIR, "blog")
APP_DIR = os.path.join(FRONTEND_DIR, "app")     # Agent E's portal SPA shells
JS_DIR = os.path.join(FRONTEND_DIR, "js")       # Agent E's shared JS
SHARED_DIR = os.path.join(FRONTEND_DIR, "_shared")

# Public, browser-safe config injected into every page (all sync:false secrets
# stay server-side on courtflow-api; these are the public values by design).
API_BASE = os.environ.get("AUTH_API_BASE", "").strip().rstrip("/")
CLERK_PUBLISHABLE_KEY = os.environ.get("CLERK_PUBLISHABLE_KEY", "").strip()
AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "0").strip()
AFTER_LOGIN_URL = os.environ.get("AUTH_AFTER_LOGIN_URL", "/portal").strip()


# ---------------------------------------------------------------------------
# HTML serving with per-club theme + config injection
# ---------------------------------------------------------------------------

def _branding() -> Branding:
    """Resolve the club branding for the current request host."""
    return resolve_branding(request.host or "")


def _inject_head(html: str, b: Branding) -> str:
    """Inject the per-club theme vars + public config into <head>. Idempotent:
    only injects if our marker isn't already present."""
    if "__CF_INJECTED__" in html:
        return html
    site_base = f"https://{b.domain}" if b.domain else ""
    cfg = {
        "__CF_INJECTED__": True,
        "__API_BASE": API_BASE,
        "__CLERK_PUBLISHABLE_KEY": CLERK_PUBLISHABLE_KEY,
        "__AUTH_ENABLED": AUTH_ENABLED == "1",
        "__AFTER_LOGIN_URL": AFTER_LOGIN_URL,
        "__CLUB_SLUG": b.slug,
        "__CLUB_NAME": b.name,
        "__SITE_BASE": site_base,
    }
    head = (
        f"<style id=\"cf-theme\">{theme_css_vars(b)}</style>\n"
        f"<script>window.__CF=Object.assign(window.__CF||{{}},{json.dumps(cfg)});"
        f"window.__API_BASE={json.dumps(API_BASE)};"
        f"window.__CLERK_PUBLISHABLE_KEY={json.dumps(CLERK_PUBLISHABLE_KEY)};</script>\n"
    )
    if "</head>" in html:
        return html.replace("</head>", head + "</head>", 1)
    return head + html


def _html(rel_path: str):
    """Serve an HTML file with branding/config injected. `rel_path` is relative to
    FRONTEND_DIR. Non-HTML files are streamed as-is."""
    path = os.path.join(FRONTEND_DIR, rel_path)
    if not os.path.isfile(path):
        abort(404)
    if not path.endswith(".html"):
        return send_file(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        b = _branding()
        html = apply_chrome(html, b)   # shared nav/footer markers (marketing pages)
        html = _inject_head(html, b)
        return Response(html, mimetype="text/html")
    except Exception:
        return send_file(path)


def _marketing(name: str):
    return _html(os.path.join("marketing", name))


def _app_shell(name: str):
    """Serve a portal SPA shell from frontend/app/ (Agent E). If Agent E hasn't
    built it yet, fall back to a themed placeholder so the route still resolves."""
    path = os.path.join(APP_DIR, name)
    if os.path.isfile(path):
        return _html(os.path.join("app", name))
    return _app_placeholder(name)


def _app_placeholder(name: str):
    """Themed 'coming soon' shell for app routes Agent E hasn't shipped yet. Keeps
    the route contract live (so links don't 404 during the parallel build)."""
    b = _branding()
    title = name.replace(".html", "").replace("_", " ").title()
    html = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex, nofollow">
<title>{title} · {b.name}</title>
<link rel="stylesheet" href="/shared/theme.css">
</head><body>
<div class="cf-wrap" style="padding:120px 28px;text-align:center;max-width:560px">
  <div class="cf-eyebrow" style="justify-content:center">{b.name}</div>
  <h1 style="font-size:2rem;margin:18px 0 12px">This area is being built</h1>
  <p style="color:var(--muted);margin-bottom:26px">The {title} app loads here once it's ready.</p>
  <a class="cf-btn cf-btn--primary" href="/">Back to home</a>
</div></body></html>"""
    return Response(_inject_head(html, b), mimetype="text/html")


# ---------------------------------------------------------------------------
# Host-switched root + marketing pages
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    if is_marketing_host(request.host or ""):
        return _marketing("home.html")
    # Non-marketing host (the onrender URL / app host) -> the portal shell.
    return _app_shell("portal.html")


# The clean marketing URL structure from docs/07 §3.
@app.get("/services")
def services():
    return _marketing("services.html")


@app.get("/coaches")
def coaches():
    return _marketing("coaches.html")


@app.get("/programs/high-performance")
def program_high_performance():
    return _marketing("program-high-performance.html")


@app.get("/programs/juniors")
def program_juniors():
    return _marketing("program-juniors.html")


@app.get("/programs/social")
def program_social():
    return _marketing("program-social.html")


@app.get("/programs/cardio-tennis")
def program_cardio():
    return _marketing("program-cardio.html")


@app.get("/pricing")
def pricing():
    if is_marketing_host(request.host or ""):
        return _marketing("pricing.html")
    return _app_shell("plans.html")


@app.get("/free-lesson")
def free_lesson():
    return _marketing("free-lesson.html")


@app.get("/contact")
def contact():
    return _marketing("contact.html")


@app.get("/careers")
def careers():
    return _marketing("careers.html")


# --- Blog (build_blog.py output) ---
@app.get("/blog")
def blog_index():
    return _html(os.path.join("blog", "index.html"))


@app.get("/post/<slug>")
def blog_post(slug: str):
    if "/" in slug or "\\" in slug or slug.startswith("."):
        abort(404)
    return _html(os.path.join("blog", f"{slug}.html"))


@app.get("/blog/images/<filename>")
def blog_image(filename: str):
    if "/" in filename or "\\" in filename or filename.startswith("."):
        abort(404)
    return _html(os.path.join("blog", "images", filename))


# ---------------------------------------------------------------------------
# Portal SPA shells (Agent E) — served on ANY host at fixed app paths.
# These are NON host-switched: a marketing host links into them via CTAs.
# ---------------------------------------------------------------------------

@app.get("/portal")
def portal():
    return _app_shell("portal.html")


@app.get("/book")
def book():
    return _app_shell("book.html")


@app.get("/book/<kind>")
def book_kind(kind: str):
    # court / lesson / class — single SPA shell, the client reads the path.
    if "/" in kind or "\\" in kind or kind.startswith("."):
        abort(404)
    return _app_shell("book.html")


@app.get("/my")
def my_area():
    return _app_shell("my.html")


@app.get("/coach")
def coach_console():
    return _app_shell("coach.html")


@app.get("/admin")
def admin_console():
    return _app_shell("admin.html")


@app.get("/dashboard")
def dashboard():
    # Non host-switched dashboard shell (portal nav loads this, not '/').
    return _app_shell("dashboard.html")


@app.get("/app/<path:filename>")
def app_asset(filename: str):
    """Serve SPA assets Agent E ships under frontend/app/ then frontend/js/.
    Path-traversal guarded; HTML gets themed, everything else streamed."""
    if ".." in filename or filename.startswith("/") or filename.startswith("\\"):
        abort(404)
    for root, rel_root in ((APP_DIR, "app"), (JS_DIR, "js")):
        candidate = os.path.normpath(os.path.join(root, filename))
        if not candidate.startswith(os.path.normpath(root)):
            abort(404)
        if os.path.isfile(candidate):
            return _html(os.path.join(rel_root, filename))
    abort(404)


@app.get("/js/<path:filename>")
def js_asset(filename: str):
    """Agent E's portal shells reference shared JS as ../js/<file>, which resolves to
    /js/<file> from the app routes (/portal, /book, ...). Serve from frontend/js/, with
    the public auth placeholders substituted in any .js (so auth_client.js works here too).
    Path-traversal guarded."""
    if ".." in filename or filename.startswith("/") or filename.startswith("\\"):
        abort(404)
    candidate = os.path.normpath(os.path.join(JS_DIR, filename))
    if not candidate.startswith(os.path.normpath(JS_DIR)) or not os.path.isfile(candidate):
        abort(404)
    with open(candidate, "r", encoding="utf-8") as f:
        js = f.read()
    js = (js.replace("__CLERK_PUBLISHABLE_KEY__", CLERK_PUBLISHABLE_KEY)
            .replace("__AUTH_ENABLED__", AUTH_ENABLED)
            .replace("__AUTH_API_BASE__", API_BASE)
            .replace("__AUTH_AFTER_LOGIN__", AFTER_LOGIN_URL))
    return Response(js, mimetype="application/javascript")


@app.get("/app.css")
def app_css():
    """Agent E's portal shells link app.css relatively -> /app.css from /portal etc."""
    path = os.path.join(APP_DIR, "app.css")
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, mimetype="text/css")


@app.get("/auth_client.js")
def auth_client_js():
    """Shared auth helper. Content is owned by Agent E (frontend/js/auth_client.js);
    we serve it with the public Clerk config substituted from env. Until E ships it,
    return a minimal stub exposing window.__CF auth config."""
    path = os.path.join(JS_DIR, "auth_client.js")
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            js = f.read()
        js = (js
              .replace("__CLERK_PUBLISHABLE_KEY__", CLERK_PUBLISHABLE_KEY)
              .replace("__AUTH_ENABLED__", AUTH_ENABLED)
              .replace("__AUTH_API_BASE__", API_BASE)
              .replace("__AUTH_AFTER_LOGIN__", AFTER_LOGIN_URL))
        return Response(js, mimetype="application/javascript")
    stub = (
        "/* courtflow auth_client stub — Agent E ships the real one in frontend/js. */\n"
        f"window.__CF=window.__CF||{{}};window.__CF.authEnabled={json.dumps(AUTH_ENABLED == '1')};"
        f"window.__CF.apiBase={json.dumps(API_BASE)};"
        f"window.__CF.clerkKey={json.dumps(CLERK_PUBLISHABLE_KEY)};\n"
    )
    return Response(stub, mimetype="application/javascript")


# ---------------------------------------------------------------------------
# Login door (this lane owns frontend/login.html)
# ---------------------------------------------------------------------------

@app.get("/login")
def login_page():
    """Clerk sign-in/sign-up, themed per host, config injected from env. Dark until
    AUTH_ENABLED=1 + CLERK_PUBLISHABLE_KEY set (page shows a graceful notice)."""
    path = os.path.join(FRONTEND_DIR, "login.html")
    if not os.path.isfile(path):
        abort(404)
    b = _branding()
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    html = (html
            .replace("__CLERK_PUBLISHABLE_KEY__", CLERK_PUBLISHABLE_KEY)
            .replace("__AUTH_AFTER_LOGIN__", AFTER_LOGIN_URL)
            .replace("__AUTH_ENABLED__", AUTH_ENABLED)
            .replace("__AUTH_API_BASE__", API_BASE)
            .replace("__CLUB_NAME__", b.name)
            .replace("__SUPPORT_EMAIL__", b.email or "info@nextpointtennis.com"))
    html = _inject_head(html, b)
    return Response(html, mimetype="text/html")


# ---------------------------------------------------------------------------
# Shared design system + favicon + OG assets
# ---------------------------------------------------------------------------

@app.get("/shared/theme.css")
def shared_theme_css():
    path = os.path.join(SHARED_DIR, "theme.css")
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, mimetype="text/css")


@app.get("/shared/<path:filename>")
def shared_asset(filename: str):
    if ".." in filename or filename.startswith("/"):
        abort(404)
    candidate = os.path.normpath(os.path.join(SHARED_DIR, filename))
    if not candidate.startswith(os.path.normpath(SHARED_DIR)) or not os.path.isfile(candidate):
        abort(404)
    return send_file(candidate)


@app.get("/favicon.svg")
def favicon_svg():
    return _html("favicon.svg")


@app.get("/favicon.ico")
def favicon_ico():
    path = os.path.join(FRONTEND_DIR, "favicon.ico")
    if os.path.isfile(path):
        return send_file(path)
    # Fall back to the SVG favicon if no .ico is shipped.
    return _html("favicon.svg")


@app.get("/og/<filename>")
def og_image(filename: str):
    if "/" in filename or "\\" in filename or filename.startswith("."):
        abort(404)
    return _html(os.path.join("og", filename))


# ---------------------------------------------------------------------------
# Crawl infrastructure (host-aware; canonical from club.branding.domain)
# ---------------------------------------------------------------------------

# Public marketing routes for the sitemap (loc, changefreq, priority).
_MARKETING_URLS = [
    ("/", "weekly", "1.0"),
    ("/services", "monthly", "0.9"),
    ("/coaches", "monthly", "0.9"),
    ("/programs/high-performance", "monthly", "0.8"),
    ("/programs/juniors", "monthly", "0.8"),
    ("/programs/cardio-tennis", "monthly", "0.8"),
    ("/programs/social", "monthly", "0.7"),
    ("/pricing", "monthly", "0.8"),
    ("/free-lesson", "monthly", "0.7"),
    ("/blog", "weekly", "0.7"),
    ("/contact", "yearly", "0.4"),
    ("/careers", "monthly", "0.4"),
]


def _site_base() -> str:
    """Canonical site base for robots/sitemap. Must match the page <canonical>/OG
    host exactly so search engines don't see a sitemap/canonical mismatch. We prefer
    the actual marketing request host (e.g. www.nextpointtennis.com) when it's a
    known marketing host; otherwise the branding domain; else the request origin
    (onrender URL while staging)."""
    host = (request.host or "").split(":")[0].lower()
    if host and is_marketing_host(host):
        return f"https://{host}".rstrip("/")
    b = _branding()
    if b.domain:
        return f"https://{b.domain}".rstrip("/")
    return request.url_root.rstrip("/")


def _blog_slugs():
    if not os.path.isdir(BLOG_DIR):
        return []
    out = []
    for p in sorted(glob.glob(os.path.join(BLOG_DIR, "*.html"))):
        name = os.path.splitext(os.path.basename(p))[0]
        if name != "index":
            out.append(name)
    return out


@app.get("/robots.txt")
def robots_txt():
    base = _site_base()
    body = f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n"
    return Response(body, mimetype="text/plain")


@app.get("/sitemap.xml")
def sitemap_xml():
    base = _site_base()
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, freq, prio in _MARKETING_URLS:
        parts += ["  <url>", f"    <loc>{base}{path}</loc>",
                  f"    <changefreq>{freq}</changefreq>",
                  f"    <priority>{prio}</priority>", "  </url>"]
    for slug in _blog_slugs():
        parts += ["  <url>", f"    <loc>{base}/post/{slug}</loc>",
                  "    <changefreq>monthly</changefreq>",
                  "    <priority>0.6</priority>", "  </url>"]
    parts.append("</urlset>")
    return Response("\n".join(parts), mimetype="application/xml")


@app.get("/__alive")
def alive():
    return jsonify({"ok": True, "service": "courtflow-web"})


# ---------------------------------------------------------------------------
# Branded 404
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    """Branded HTML 404 for humans; JSON for API/ops paths + JSON clients."""
    path = request.path or ""
    is_api = path.startswith("/api") or path.startswith("/ops")
    # Serve the branded HTML 404 for ordinary page requests. Only fall back to JSON
    # for API/ops paths or when the client explicitly prefers JSON over HTML.
    accept = request.accept_mimetypes
    prefers_json = accept["application/json"] > accept["text/html"]
    if not is_api and not prefers_json:
        page = os.path.join(MARKETING_DIR, "404.html")
        if os.path.isfile(page):
            try:
                with open(page, "r", encoding="utf-8") as f:
                    html = _inject_head(f.read(), _branding())
                return Response(html, mimetype="text/html"), 404
            except Exception:
                return send_file(page), 404
    return jsonify({"ok": False, "error": "not_found", "service": "courtflow-web"}), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5060))
    app.run(host="0.0.0.0", port=port, debug=False)

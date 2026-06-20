# build_blog.py — dependency-free static blog generator for courtflow-web.
#
# Ported from 1050's build_blog.py, made PER-CLUB THEMED. Converts Markdown posts
# in frontend/blog/_posts/*.md into native, SEO-ready HTML at frontend/blog/<slug>.html,
# plus a blog index at frontend/blog/index.html.
#
# Each post is native crawlable HTML (no Wix JS) with Article + BreadcrumbList
# JSON-LD, Open Graph cards, a canonical at /post/<slug>, internal links, and uses
# the shared design system (/shared/theme.css) so it themes per club automatically.
# web_app.sitemap_xml() auto-includes every generated post.
#
# Theming: the generator pulls the club's name/domain/colours from
# frontend/_shared/branding.py (DEFAULT_CLUB). For a second club, pass --club <slug>
# (its Branding must exist in the registry). The page links to /shared/theme.css and
# is re-themed at serve time by web_app's injected CSS vars, so colours stay correct
# even though the file is generated once.
#
# Workflow for a new post:
#   1) Drop frontend/blog/_posts/<slug>.md with frontmatter:
#         ---
#         title: My Post Title
#         description: One-line meta description (~150 chars).
#         date: 2026-06-15
#         image: /blog/images/hero.webp   (optional)
#         ---
#         Body in Markdown...
#   2) Run:  python build_blog.py
#   3) Commit the generated frontend/blog/*.html.
#
# Supported Markdown: ##/### headings, paragraphs, - / * bullet lists, **bold**,
# *italic*, [text](url) links, and GFM pipe tables. (Deliberately minimal.)

import os
import re
import sys
import glob
import json as _jsonmod
import html as _html
import importlib.util

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BLOG_DIR = os.path.join(BASE_DIR, "frontend", "blog")
POSTS_DIR = os.path.join(BLOG_DIR, "_posts")


def _load_branding():
    """Import the Branding registry from frontend/_shared/branding.py without
    requiring the package to be importable (script runs from repo root)."""
    p = os.path.join(BASE_DIR, "frontend", "_shared", "branding.py")
    spec = importlib.util.spec_from_file_location("cf_branding_build", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- Markdown -> HTML (minimal) ----------------------------------------------

def _inline(text):
    out = _html.escape(text, quote=False)
    out = re.sub(r'\[([^\]]+)\]\((https?://[^)\s]+|/[^)\s]*)\)', r'<a href="\2">\1</a>', out)
    out = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', out)
    out = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<em>\1</em>', out)
    return out


def _table(rows):
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    header, body = cells[0], cells[2:]
    out = ['<div class="table-wrap"><table>', "<thead><tr>"]
    out += [f"<th>{_inline(c)}</th>" for c in header]
    out.append("</tr></thead><tbody>")
    for row in body:
        out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def md_to_html(md):
    lines = md.split("\n")
    blocks, i = [], 0
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        m = re.match(r'^(#{2,4})\s+(.*)$', s)
        if m:
            level = len(m.group(1))
            blocks.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue
        if s.startswith("|") and i + 1 < len(lines) and re.match(r'^\|[\s:\-|]+\|?$', lines[i + 1].strip()):
            tbl = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                tbl.append(lines[i]); i += 1
            blocks.append(_table(tbl)); continue
        if re.match(r'^[-*]\s+', s):
            items = []
            while i < len(lines) and re.match(r'^[-*]\s+', lines[i].strip()):
                items.append(f"<li>{_inline(re.sub(r'^[-*]\s+', '', lines[i].strip()))}</li>"); i += 1
            blocks.append("<ul>" + "".join(items) + "</ul>"); continue
        para = [s]; i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r'^(#{2,4}\s|[-*]\s|\|)', lines[i].strip()):
            para.append(lines[i].strip()); i += 1
        blocks.append("<p>" + _inline(" ".join(para)) + "</p>")
    return "\n".join(blocks)


def parse_post(path):
    raw = open(path, encoding="utf-8").read()
    meta, body = {}, raw
    if raw.startswith("---"):
        _, fm, body = raw.split("---", 2)
        for ln in fm.strip().split("\n"):
            if ":" in ln:
                k, v = ln.split(":", 1)
                val = v.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1].strip()
                meta[k.strip()] = val
    meta["slug"] = os.path.splitext(os.path.basename(path))[0]
    meta["body_html"] = md_to_html(body.strip())
    return meta


# ---- Article-specific styles (layered on top of /shared/theme.css) ------------

ARTICLE_CSS = """
.post-head{padding:80px 0 26px;background:linear-gradient(180deg,var(--primary-bg),transparent)}
.post-head h1{font-size:clamp(2rem,4.6vw,3rem);font-weight:800;letter-spacing:-.02em;line-height:1.1;margin-top:16px}
.post-meta{margin-top:16px;color:var(--muted);font-size:.9rem}
.post-hero{max-width:820px;margin:0 auto;padding:24px 28px 0}
.post-hero img{width:100%;border-radius:12px;border:1px solid var(--line);box-shadow:var(--shadow)}
.article{padding:32px 0 68px}
.article .wrap{max-width:820px;margin:0 auto;padding:0 28px}
.article h2{font-size:1.55rem;font-weight:700;margin:40px 0 14px}
.article h3{font-size:1.2rem;font-weight:700;margin:28px 0 10px}
.article p{margin:0 0 18px;font-size:1.05rem;color:#27303a}
.article ul{margin:0 0 20px;padding-left:24px}
.article li{margin:0 0 9px;font-size:1.05rem;color:#27303a}
.article strong{color:var(--ink);font-weight:700}
.table-wrap{overflow-x:auto;margin:0 0 24px}
.article table{border-collapse:collapse;width:100%;font-size:.95rem}
.article th,.article td{border:1px solid var(--line);padding:10px 12px;text-align:left;vertical-align:top}
.article th{background:var(--primary-bg);font-weight:700}
.cta-band{margin:44px 0 0;padding:32px 30px;background:var(--primary-d);border-radius:12px;color:#fff;text-align:center}
.cta-band h3{color:#fff;font-size:1.3rem;margin-bottom:10px}
.cta-band p{color:rgba(255,255,255,.78);margin-bottom:20px}
.cta-band a.cf-btn{background:#fff;color:var(--primary)}
.backlink{display:inline-block;margin-top:28px;color:var(--primary);font-weight:600}
.idx-head{padding:80px 0 22px;background:linear-gradient(180deg,var(--primary-bg),transparent)}
.idx-head h1{font-size:clamp(2.2rem,5vw,3.2rem);font-weight:800;letter-spacing:-.02em;margin-top:16px}
.idx-head p{margin-top:14px;color:var(--muted);font-size:1.1rem;max-width:560px}
.post-list{padding:30px 0 70px;display:grid;gap:4px;max-width:820px;margin:0 auto}
.post-card{display:grid;grid-template-columns:230px 1fr;gap:26px;align-items:center;padding:24px 0;border-bottom:1px solid var(--line)}
.post-card:hover{text-decoration:none}
.post-card .thumb{aspect-ratio:16/10;border-radius:10px;overflow:hidden;background:linear-gradient(150deg,var(--primary),var(--primary-d));border:1px solid var(--line)}
.post-card .thumb img{width:100%;height:100%;object-fit:cover}
.post-card .date{color:var(--muted);font-size:.82rem;text-transform:uppercase;letter-spacing:.08em}
.post-card h2{font-size:1.35rem;font-weight:700;color:var(--ink);margin:7px 0 8px}
.post-card:hover h2{color:var(--primary)}
.post-card p{color:var(--muted);font-size:.98rem}
@media(max-width:600px){.post-card{grid-template-columns:1fr;gap:14px}}
"""

FONT = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">')


def _json(s):
    return _jsonmod.dumps(s or "")


_MONTHS = ["", "January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]


def _fmt_date(d):
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', d or "")
    if not m:
        return ""
    y, mo, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return f"{day} {_MONTHS[mo]} {y}"


class Site:
    """Per-club site context for the generator (derived from Branding)."""
    def __init__(self, b):
        self.name = b.name
        self.base = f"https://{b.domain}".rstrip("/") if b.domain else ""
        self.og = b.og_image_url or f"{self.base}/og/og-default.png"
        self.email = b.email


# Shared nav/footer (static, since the file is generated once; theme.css recolours).
def _nav(site):
    return f"""<nav class="cf-nav"><div class="cf-nav-inner">
  <a href="/" class="cf-logo">{_html.escape(site.name)}</a>
  <div class="cf-nav-links">
    <a href="/book/court">Courts</a><a href="/coaches">Coaching</a>
    <a href="/programs/high-performance">Programs</a><a href="/pricing">Pricing</a>
    <a href="/blog" class="active">Blog</a><a href="/contact">Contact</a>
  </div>
  <div class="cf-nav-right"><a href="/login" class="cf-nav-cta">Sign in</a>
    <button class="cf-nav-toggle" aria-label="Toggle menu" onclick="document.querySelector('.cf-nav-links').classList.toggle('open')">&#9776;</button>
  </div></div></nav>"""


def _footer(site):
    return f"""<footer class="cf-footer"><div class="cf-footer-inner">
  <div class="cf-footer-brand"><div class="cf-footer-brand-name">{_html.escape(site.name)}</div>
    <p>Court booking, coaching and classes. Book a court, a lesson with a named coach, or join Cardio Tennis and junior squads.</p></div>
  <div class="cf-footer-col"><h5>Book</h5><ul>
    <li><a href="/book/court">Book a court</a></li><li><a href="/coaches">Book a lesson</a></li>
    <li><a href="/programs/cardio-tennis">Cardio Tennis</a></li><li><a href="/free-lesson">Free lesson</a></li></ul></div>
  <div class="cf-footer-col"><h5>Club</h5><ul>
    <li><a href="/pricing">Pricing</a></li><li><a href="/blog">Blog</a></li>
    <li><a href="/contact">Contact</a></li><li><a href="mailto:{site.email}">{site.email}</a></li></ul></div>
  </div>
  <div class="cf-footer-bottom"><span>&copy; 2026 {_html.escape(site.name)}. All rights reserved.</span></div>
</footer>"""


def _cta_band(site):
    return f"""<div class="cta-band">
  <h3>Ready to play?</h3>
  <p>Book a court, a lesson with a named coach, or claim a free lesson at {_html.escape(site.name)}.</p>
  <a class="cf-btn" href="/free-lesson">Claim a free lesson</a>
</div>"""


def render_post(p, site):
    url = f"{site.base}/post/{p['slug']}"
    title = p.get("title", p["slug"])
    desc = p.get("description", "")
    date = p.get("date", "")
    image = p.get("image", "")
    og_img = (f"{site.base}{image}" if image.startswith("/") else (image or site.og))
    hero = (f'<figure class="post-hero"><img src="{image}" alt="{_html.escape(title)}" '
            f'width="900" height="506" fetchpriority="high" decoding="async"></figure>\n') if image else ""
    article_ld = (
        '{"@context":"https://schema.org","@type":"Article",'
        f'"headline":{_json(title)},"description":{_json(desc)},'
        f'"datePublished":{_json(date)},"image":{_json(og_img)},'
        f'"author":{{"@type":"Organization","name":{_json(site.name)}}},'
        f'"publisher":{{"@type":"Organization","name":{_json(site.name)}}},'
        f'"mainEntityOfPage":{_json(url)}}}'
    )
    breadcrumb_ld = (
        '{"@context":"https://schema.org","@type":"BreadcrumbList","itemListElement":['
        f'{{"@type":"ListItem","position":1,"name":"Blog","item":"{site.base}/blog"}},'
        f'{{"@type":"ListItem","position":2,"name":{_json(title)},"item":{_json(url)}}}]}}'
    )
    return f"""<!DOCTYPE html>
<html lang="en-ZA">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>{_html.escape(title)} | {_html.escape(site.name)}</title>
<meta name="description" content="{_html.escape(desc)}">
<link rel="canonical" href="{url}">
<meta name="robots" content="index, follow">
<meta property="og:type" content="article">
<meta property="og:site_name" content="{_html.escape(site.name)}">
<meta property="og:title" content="{_html.escape(title)}">
<meta property="og:description" content="{_html.escape(desc)}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{og_img}">
<meta name="twitter:card" content="summary_large_image">
<script type="application/ld+json">{article_ld}</script>
<script type="application/ld+json">{breadcrumb_ld}</script>
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
{FONT}
<link rel="stylesheet" href="/shared/theme.css">
<style>{ARTICLE_CSS}</style>
</head>
<body>
<a class="cf-skip" href="#main">Skip to content</a>
{_nav(site)}
<span id="main" tabindex="-1"></span>
<header class="post-head"><div class="article"><div class="wrap" style="padding-top:0;padding-bottom:0">
  <a class="cf-eyebrow" href="/blog">{_html.escape(site.name)} Blog</a>
  <h1>{_html.escape(title)}</h1>
  <div class="post-meta">{_fmt_date(date)}</div>
</div></div></header>
{hero}<main class="article"><div class="wrap">
{p['body_html']}
{_cta_band(site)}
<a class="backlink" href="/blog">&larr; All articles</a>
</div></main>
{_footer(site)}
</body>
</html>"""


def render_index(posts, site):
    cards = []
    for p in posts:
        img = p.get("image", "")
        thumb = (f'<div class="thumb"><img src="{img}" alt="" loading="lazy" width="230" height="144"></div>'
                 if img else '<div class="thumb"></div>')
        cards.append(
            f'<a class="post-card" href="/post/{p["slug"]}">{thumb}<div>'
            f'<div class="date">{_fmt_date(p.get("date",""))}</div>'
            f'<h2>{_html.escape(p.get("title", p["slug"]))}</h2>'
            f'<p>{_html.escape(p.get("description",""))}</p></div></a>'
        )
    return f"""<!DOCTYPE html>
<html lang="en-ZA">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>Blog — Tennis Tips & News | {_html.escape(site.name)}</title>
<meta name="description" content="Tennis tips, coaching insight and club news from {_html.escape(site.name)} in Johannesburg.">
<link rel="canonical" href="{site.base}/blog">
<meta name="robots" content="index, follow">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{_html.escape(site.name)}">
<meta property="og:title" content="{_html.escape(site.name)} Blog">
<meta property="og:url" content="{site.base}/blog">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
{FONT}
<link rel="stylesheet" href="/shared/theme.css">
<style>{ARTICLE_CSS}</style>
</head>
<body>
<a class="cf-skip" href="#main">Skip to content</a>
{_nav(site)}
<span id="main" tabindex="-1"></span>
<header class="idx-head"><div class="cf-wrap" style="max-width:820px">
  <div class="cf-eyebrow">Blog</div>
  <h1>Tennis, on and off the court.</h1>
  <p>Coaching tips, booking guides and club news from {_html.escape(site.name)}.</p>
</div></header>
<main><div class="cf-wrap"><div class="post-list">
{chr(10).join(cards)}
</div></div></main>
{_footer(site)}
</body>
</html>"""


def main():
    club_slug = None
    if "--club" in sys.argv:
        idx = sys.argv.index("--club")
        if idx + 1 < len(sys.argv):
            club_slug = sys.argv[idx + 1]
    bmod = _load_branding()
    b = bmod.CLUBS.get(club_slug, bmod.DEFAULT_CLUB) if club_slug else bmod.DEFAULT_CLUB
    site = Site(b)

    os.makedirs(POSTS_DIR, exist_ok=True)
    paths = sorted(glob.glob(os.path.join(POSTS_DIR, "*.md")))
    posts = [parse_post(p) for p in paths]
    posts.sort(key=lambda p: p.get("date", ""), reverse=True)
    for p in posts:
        out = os.path.join(BLOG_DIR, f"{p['slug']}.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(render_post(p, site))
        print(f"  wrote {os.path.relpath(out, BASE_DIR)}")
    with open(os.path.join(BLOG_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_index(posts, site))
    print(f"  wrote frontend/blog/index.html ({len(posts)} posts, club={site.name})")


if __name__ == "__main__":
    main()

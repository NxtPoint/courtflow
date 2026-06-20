# migration/redirects.py — the reversible 301 layer for the Wix -> Render cutover.
#
# STATUS: scaffolding, NOT wired into web_app yet (by design). The SEO cutover is a
# SUPERVISED, reversible step (docs/07 §4, CLAUDE.md: agents never change DNS). This
# module gives Tomo a ready, tested redirect engine to switch on at cutover time —
# nothing here runs until it's explicitly registered.
#
# WHAT IT DOES
#   Loads migration/redirects.csv (old_path -> new_path, status) and exposes a Flask
#   blueprint that 301-redirects every old Wix path to its clean new equivalent. Only
#   active on a marketing host (so app paths are never shadowed). No redirect chains:
#   each old path maps STRAIGHT to its final destination.
#
# TO ACTIVATE (at supervised cutover, after the new site is staged + verified):
#   1) Fill migration/url_inventory.csv from GSC + Ahrefs + a full Wix crawl.
#   2) Regenerate/curate migration/redirects.csv from it.
#   3) In web_app.py add:
#          from migration.redirects import register_redirects
#          register_redirects(app)
#      (registered BEFORE the catch-all so old paths resolve to a 301, not the 404.)
#   4) Lower DNS TTL a day ahead, then cut DNS to courtflow-web. Rollback = DNS back
#      to Wix. Submit the new sitemap + request indexing on the top 20-30 pages.
#
# It is deliberately import-clean so `python -c "import migration.redirects"` works
# in CI without changing live behaviour.

import os
import csv

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REDIRECTS_CSV = os.path.join(_THIS_DIR, "redirects.csv")


def load_redirects(path: str = REDIRECTS_CSV) -> dict:
    """Parse redirects.csv -> {old_path: (new_path, status)}. Skips comment/blank
    rows and the header. Normalises trailing slashes so '/x' and '/x/' both match."""
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row:
                continue
            first = row[0].strip()
            if not first or first.startswith("#") or first == "old_path":
                continue
            if len(row) < 2:
                continue
            old = _norm(first)
            new = row[1].strip()
            status = int(row[2]) if len(row) > 2 and row[2].strip().isdigit() else 301
            out[old] = (new, status)
    return out


def _norm(p: str) -> str:
    p = (p or "/").strip()
    if len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/")
    return p or "/"


def register_redirects(app, marketing_only: bool = True):
    """Register the 301 layer on a Flask app. Call at cutover only (see header).

    marketing_only=True restricts redirects to marketing hosts so the portal app
    paths on other hosts are never shadowed. Uses before_request so it runs ahead
    of normal routing and the 404 handler."""
    from flask import request, redirect

    table = load_redirects()

    # Resolve is_marketing_host lazily to avoid a hard import cycle with web_app.
    def _is_marketing():
        try:
            from frontend._shared.branding import is_marketing_host
        except ImportError:  # pragma: no cover
            import importlib.util
            p = os.path.join(os.path.dirname(_THIS_DIR), "frontend", "_shared", "branding.py")
            spec = importlib.util.spec_from_file_location("cf_branding_redir", p)
            m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
            is_marketing_host = m.is_marketing_host
        return is_marketing_host(request.host or "")

    @app.before_request
    def _apply_redirect():
        if marketing_only and not _is_marketing():
            return None
        dest = table.get(_norm(request.path))
        if dest:
            new_path, status = dest
            return redirect(new_path, code=status)
        return None

    return table


if __name__ == "__main__":
    t = load_redirects()
    print(f"{len(t)} redirect rules loaded from {os.path.relpath(REDIRECTS_CSV)}")
    for old, (new, status) in sorted(t.items()):
        print(f"  {status}  {old}  ->  {new}")

#!/usr/bin/env python
"""READ-ONLY: audit the docs against the CODE, and report what the docs have missed.

Prose does not fail a gate. `py_compile` and the harnesses say nothing about a doc that describes a
feature we deleted, or stays silent about one we added — so documentation rots invisibly and is then
trusted by the next person (or the next session) precisely when it is wrong.

This is the check that would have caught, on the day: an approval lifecycle documented as LIVE in six
files two days after it was deleted; a brand-new shared widget missing from the golden-rule register
that exists to stop people forking widgets; and a whole money surface absent from "the exhaustive
list". Each of those was found late, by hand, by accident.

Every check is CODE -> DOCS: extract the real thing from the source, then look for it in the prose.

    python -m scripts.audit_docs             # summary
    python -m scripts.audit_docs --verbose   # list every miss, not just the first few
    python -m scripts.audit_docs --strict    # exit 1 if anything is missing (for a pre-merge gate)

It reports MISSES, not failures: some absences are deliberate (a doc may reasonably not enumerate
every static marketing route). Read the list and decide. It is a prompt to look, not a verdict.
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _tracked(*globs):
    out = subprocess.run(["git", "ls-files", *globs], cwd=ROOT,
                         capture_output=True, text=True).stdout.split()
    return [ROOT / f for f in out]


def _read(paths):
    buf = []
    for p in paths:
        try:
            buf.append(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return "\n".join(buf)


# The prose we hold the code against. `docs/00..12` are FROZEN design docs (docs/specs is the
# as-built truth), so they are deliberately not part of the corpus — holding them to current code
# would report hundreds of false misses.
DOC_GLOBS = ["docs/specs/*.md", "CLAUDE.md", "README.md", "BUILD_PROMPT.md",
             "scripts/README.md", "contracts/*.md"]


# Routes the docs deliberately do NOT enumerate: the static/marketing surface (every blog post,
# programme page and favicon). INVENTORY documents the API; listing these would be noise, and an
# audit that always reports the same known-fine misses is an audit people stop reading.
_ROUTE_ALLOWLIST = re.compile(
    r"^/(favicon\.|robots\.txt|sitemap\.xml|blog|img/|shared/|programs|careers|free-lesson"
    r"|membership\.html|packs\.html|my\.html|<page>\.html|__alive)")


def check_routes(docs):
    """Every registered API route should appear in the inventory (static pages excepted)."""
    routes = set()
    for f in _tracked("*.py"):
        for m in re.finditer(r'@(\w+)\.(get|post|patch|put|delete|route)\(\s*[\'"]([^\'"]+)',
                             f.read_text(encoding="utf-8", errors="ignore")):
            routes.add(m.group(3))
    missing = []
    for r in sorted(routes):
        # Match on the distinctive tail: docs write "GET coach-statement" under a lane heading
        # rather than repeating the full "/api/admin/..." prefix every time. Take the last LITERAL
        # segment — a route that STARTS with a param ("/<product_id>/variations") has an empty first
        # segment, and treating that as "no tail" reported every one of them as undocumented.
        if _ROUTE_ALLOWLIST.match(r):
            continue
        segs = [s for s in r.split("/") if s and not s.startswith("<")]
        tail = segs[-1] if segs else ""
        if r not in docs and (not tail or tail not in docs):
            missing.append(r)
    return "API routes", len(routes), missing


def check_tables(docs):
    tables = set()
    for f in _tracked("*/schema.py", "schema.py"):
        src = f.read_text(encoding="utf-8", errors="ignore")
        tables |= set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+\{?SCHEMA\}?\.?(\w+)", src))
        tables |= {m[1] for m in re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)\.(\w+)", src)}
    return "DB tables", len(tables), sorted(t for t in tables if t not in docs)


def check_widgets(docs):
    """A shared widget missing from FRONTEND-STANDARDISATION is how a fork gets built."""
    fe = _read([ROOT / "docs/specs/FRONTEND-STANDARDISATION.md"])
    names = set()
    for f in _tracked("frontend/js/widgets/*.js"):
        names |= set(re.findall(r"window\.Widgets\.(\w+)\s*=", f.read_text(encoding="utf-8", errors="ignore")))
    return "Shared widgets (vs the GOLDEN RULE doc)", len(names), sorted(n for n in names if n not in fe)


def check_events(docs):
    """Every emitted event should be in the producer/consumer contract."""
    contract = _read([ROOT / "contracts/events.md"])
    names = set()
    for f in _tracked("*.py"):
        src = f.read_text(encoding="utf-8", errors="ignore")
        names |= set(re.findall(r'emit\(\s*["\'](\w+)["\']', src))
    return "Emitted events (vs contracts/events.md)", len(names), sorted(
        n for n in names if n not in contract)


def check_scenarios(docs):
    """Docs must not name a scenario that no longer exists."""
    harness = _read(_tracked("scripts/test_*.py"))
    real = set(re.findall(r"def (sc_\w+)", harness))
    named = set(re.findall(r"`(sc_\w+)`", docs))
    return "Scenarios NAMED in docs but absent from a harness", len(named), sorted(named - real)


def check_scripts(docs):
    have = {p.stem for p in (ROOT / "scripts").glob("*.py")}
    named = {a or b for a, b in re.findall(r"scripts[/.](\w+)\.py|scripts\.(\w+)\b", docs)} - {"README"}
    return "Scripts REFERENCED by docs but absent", len(named), sorted(named - have)


def check_undocumented_scripts(docs):
    idx = _read([ROOT / "scripts/README.md"])
    have = {p.stem for p in (ROOT / "scripts").glob("*.py")
            if p.stem not in ("__init__", "test_all")}
    return "Scripts present but NOT in scripts/README", len(have), sorted(
        s for s in have if s not in idx)


def check_doc_links(docs_paths):
    """A relative link to a doc that does not exist."""
    bad = []
    n = 0
    for p in docs_paths:
        src = p.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"\[[^\]]+\]\(([^)#:]+\.md)[^)]*\)", src):
            n += 1
            target = (p.parent / m.group(1)).resolve()
            if not target.exists():
                bad.append(f"{p.relative_to(ROOT)} -> {m.group(1)}")
    return "Internal doc links", n, sorted(set(bad))


def check_baselines(docs_paths):
    """Every doc claiming the CURRENT gate baseline must agree with the others.

    Dated history ("Gated green at the time: 43/142/35") is legitimate and must NOT be flagged, so
    only lines that assert the present are considered."""
    claims = {}
    for p in docs_paths:
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.search(r"booking (\d+) ?/ ?billing (\d+) ?/ ?statement (\d+)", line)
            if not m:
                continue
            low = line.lower()
            if any(w in low for w in ("at the time", "was ", "previously", "baseline at merge",
                                      "new gate baseline", "gated green:", "-> booking")):
                continue
            if any(w in low for w in ("current", "baseline:", "harnesses (")):
                claims.setdefault(m.group(1, 2, 3), []).append(str(p.relative_to(ROOT)))
    misses = []
    if len(claims) > 1:
        for k, where in claims.items():
            misses.append(f"{'/'.join(k)} claimed 'current' in {', '.join(where)}")
    return "Current-gate-baseline agreement", len(claims), misses


def main(argv):
    verbose = "--verbose" in argv
    strict = "--strict" in argv
    doc_paths = _tracked(*DOC_GLOBS)
    docs = _read(doc_paths)

    print(f"DOC AUDIT — {len(doc_paths)} docs vs the codebase   (read-only)\n")
    results = [
        check_routes(docs), check_tables(docs), check_widgets(docs), check_events(docs),
        check_scenarios(docs), check_scripts(docs), check_undocumented_scripts(docs),
        check_doc_links(doc_paths), check_baselines(doc_paths),
    ]
    total = 0
    for label, n, missing in results:
        total += len(missing)
        mark = "ok " if not missing else "MISS"
        print(f"[{mark}] {label}: {len(missing)} of {n}")
        for x in (missing if verbose else missing[:8]):
            print(f"         {x}")
        if not verbose and len(missing) > 8:
            print(f"         ... +{len(missing) - 8} more (--verbose)")
    print(f"\n{total} thing(s) the docs have not caught up with.")
    print("Not every miss is a bug — some absences are deliberate. Read them and decide.")
    return 1 if (strict and total) else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:                                          # noqa: BLE001
        print(f"FAILED: {e}")
        sys.exit(1)

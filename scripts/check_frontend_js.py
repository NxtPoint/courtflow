# scripts/check_frontend_js.py — the GATE that every frontend JS file actually PARSES.
#
#   python -m scripts.check_frontend_js        (no DB, no env, ~1s)
#
# Runs `node --check` over frontend/js/**/*.js. Needs no DATABASE_URL, so it runs FIRST in
# scripts.test_all and fails before any DB work.
#
# Why this exists (2026-08-09): 640b2b8 wrote a confirm prompt with REAL newlines inside a
# double-quoted string. JavaScript has no multi-line string literal, so admin_app.js (173KB)
# stopped parsing ENTIRELY — AdminApp was never defined, AdminApp.start() threw, and /admin sat
# on "Loading…" for 11 hours. It read as "cannot log in": auth was fine (whoami 200), the page
# simply died before issuing a single request. py_compile gates the Python; nothing gated the
# JS, so a file that cannot load at all shipped clean.
#
# Parsing stops at the FIRST bad token, so a file can hide a second error behind the first —
# this reports per-file and re-runs to exhaustion is not needed (node reports the first, you fix,
# you re-run). Fix every file it names, then run it again until it is silent.
#
# Deliberately NOT a linter: no style rules, no config, no dependency beyond node itself. It
# answers exactly one question — will the browser be able to load this file at all?

import re
import shutil
import subprocess
import sys
from pathlib import Path

# repo root = the parent of scripts/
ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / "frontend" / "js"

_RESULTS = []


def check(name, cond, detail=""):
    _RESULTS.append((name, bool(cond), detail))
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail and not cond else ''}")


def _node():
    """Path to node, or None. Fails CLOSED in main() — a gate that cannot verify must not pass."""
    return shutil.which("node")


# ---------------------------------------------------------------------------
# Shared-helper SHAPE check — the bug `node --check` cannot see
# ---------------------------------------------------------------------------
# A file can parse perfectly and still be dead on arrival because a shared helper was called with the
# wrong ARGUMENT SHAPE. Not hypothetical: `UI.card(children, extra)` takes CHILDREN first and has no
# title parameter, but it reads like `card(title, body)` — so seven calls were written as
# `card("Who's playing", seats)`. Every one threw `children.forEach is not a function` the moment it
# ran (el() does `(children || []).forEach(...)`, and a string has no forEach), which killed the Home
# card, the entire player-profile screen and the game detail screen.
#
# node --check passed all seven. So did 572 backend scenarios. The only thing that found them was
# opening the page — so the cheapest honest fix is a tripwire on the exact mistake.
#
# Keep this list SHORT. It is not a linter; it is a memory of a specific bug. Add an entry only for a
# helper whose first argument is a node-or-array AND which reads like it takes a title.
_SHAPE_TRAPS = [
    (
        "UI.card",
        # card("…"  or  card('…'  — a string literal in the children position.
        re.compile(r"""(?<![\w.])card\(\s*["']"""),
        'card(children, extra) takes CHILDREN first — there is no title argument. '
        'Use: card([el("h2", { style: "margin:0 0 8px", text: "Title" }), body])',
    ),
]


# A file that declares its OWN helper of the same name is not calling the shared one, and its
# signature is its own business. `service_editor.js` defines `function card(title, hint)` — which
# genuinely does take a title — so a naive regex flagged four perfectly correct calls. A check that
# cries wolf is a check people learn to skip, which is worse than not having it.
_LOCAL_DECL = re.compile(r"function\s+%s\s*\(|(?:var|let|const)\s+%s\s*=\s*function")


def check_helper_shapes(files):
    """Flag calls to a SHARED UI helper with an argument shape that throws at runtime."""
    bad = []
    for f in files:
        src = f.read_text(encoding="utf-8", errors="ignore")
        for name, rx, why in _SHAPE_TRAPS:
            short = name.split(".")[-1]
            if re.search(_LOCAL_DECL.pattern % (short, short), src):
                continue                      # this file has its own — not our business
            for lineno, line in enumerate(src.splitlines(), 1):
                s = line.strip()
                if s.startswith("//") or s.startswith("*"):
                    continue
                if rx.search(line):
                    bad.append((f"{f.relative_to(ROOT)}".replace("\\", "/") + f":{lineno}",
                                name, why))
    return bad


def main():
    node = _node()
    if not node:
        print("node not found on PATH — cannot verify the frontend JS parses.")
        print("Install Node (https://nodejs.org) or run this gate on a box that has it.")
        print("Failing CLOSED: an unverified gate must never report success.")
        return 1

    files = sorted(JS_DIR.rglob("*.js"))
    if not files:
        print(f"No .js found under {JS_DIR} — expected the frontend bundle. Check the path.")
        return 1

    print(f"node --check over {len(files)} file(s) under frontend/js/")
    for f in files:
        proc = subprocess.run(
            [node, "--check", str(f)],
            capture_output=True, text=True,
        )
        ok = proc.returncode == 0
        detail = ""
        if not ok:
            # node prints "<path>:<line>\n<source>\n<caret>\n\nSyntaxError: ...". Keep the
            # SyntaxError line plus the location, which is what actually locates the bug.
            lines = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()]
            loc = next((ln for ln in lines if ln.startswith(str(f))), "")
            err = next((ln for ln in lines if "Error" in ln), "parse failed")
            detail = f"{err.strip()} ({loc.split(':')[-1]})" if loc else err.strip()
        check(str(f.relative_to(ROOT)).replace("\\", "/"), ok, detail)

    shape_bad = check_helper_shapes(files)

    total = len(_RESULTS)
    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    print(f"\n{passed}/{total} frontend JS files parse")
    if passed != total:
        print("\nA file that does not parse is DEAD IN THE BROWSER — the whole file, not just")
        print("the bad line. Nothing it defines will exist. Fix these before pushing:")
        for n, ok, d in _RESULTS:
            if not ok:
                print(f"  - {n}  {d}")
    if shape_bad:
        print(f"\n{len(shape_bad)} call(s) to a shared helper with the WRONG ARGUMENT SHAPE.")
        print("These PARSE and then throw the moment the code runs — which is precisely why this")
        print("check sits beside `node --check` instead of trusting it:")
        seen = set()
        for where, name, why in shape_bad:
            print(f"  - {where}  {name}")
            if name not in seen:
                print(f"      {why}")
                seen.add(name)
    return 0 if (passed == total and not shape_bad) else 1


if __name__ == "__main__":
    sys.exit(main())

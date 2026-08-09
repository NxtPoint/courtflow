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

    total = len(_RESULTS)
    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    print(f"\n{passed}/{total} frontend JS files parse")
    if passed != total:
        print("\nA file that does not parse is DEAD IN THE BROWSER — the whole file, not just")
        print("the bad line. Nothing it defines will exist. Fix these before pushing:")
        for n, ok, d in _RESULTS:
            if not ok:
                print(f"  - {n}  {d}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

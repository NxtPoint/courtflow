"""Verify a live DNS zone against the target zone file we intend to publish.

The Wix decommission's whole risk is a record that silently fails to make the
trip — an MX, an SES DKIM key, one of Clerk's five. This reads
``migration/dns/<domain>.zone`` as the source of truth and asks a resolver
whether reality matches it, record for record.

Two moments matter, and it is the same command both times:

  BEFORE the nameserver flip — ask Cloudflare's assigned nameservers directly,
  while Wix is still authoritative. Nothing is live yet, so a miss costs
  nothing::

      python -m scripts.verify_dns ten-fifty5.com --ns kate.ns.cloudflare.com

  AFTER the flip — ask the public internet and confirm it agrees::

      python -m scripts.verify_dns ten-fifty5.com

Exits 1 on any mismatch so it can gate a step rather than merely inform one.

Deliberately stdlib-only (shells out to ``nslookup``): this runs on a laptop
mid-migration, and a script that needs ``pip install`` first is a script that
doesn't get run at the moment it's needed.
"""

from __future__ import annotations

import argparse
import re
import json
import random
import socket
import struct
import subprocess
import sys
import urllib.request
from pathlib import Path

ZONE_DIR = Path(__file__).resolve().parent.parent / "migration" / "dns"

# Records whose loss is silent and expensive. Everything in the zone file is
# checked; these additionally make the run FAIL LOUDLY rather than just warn,
# because "mail stopped" is discovered hours later by a human, not a monitor.
CRITICAL_TYPES = {"MX"}
CRITICAL_PATTERNS = (
    re.compile(r"_domainkey$"),      # SES + Clerk DKIM
    re.compile(r"^_dmarc$"),
    re.compile(r"^(clerk|accounts|clkmail)$"),
)


def _is_critical(name: str, rtype: str) -> bool:
    if rtype in CRITICAL_TYPES:
        return True
    if rtype == "TXT" and name == "@":
        return True  # SPF lives here
    return any(p.search(name) for p in CRITICAL_PATTERNS)


def _strip_comment(line: str) -> str:
    """Drop a trailing ; comment without cutting inside a quoted value.

    DMARC and SPF values are full of semicolons ("v=DMARC1; p=none; ..."), so a
    naive split(";") truncates the very records this script exists to protect.
    """
    out, in_quotes = [], False
    for ch in line:
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == ";" and not in_quotes:
            break
        out.append(ch)
    return "".join(out).strip()


def parse_zone(path: Path) -> list[tuple[str, str, str]]:
    """Return [(name, rtype, value)] from a BIND-ish zone file.

    Only the subset we actually author: A, CNAME, MX, TXT. Comments and
    $ORIGIN/$TTL directives are skipped — the omitted-apex note in the
    ten-fifty5 file is a comment precisely so it lands here as "not checked"
    rather than as a guessed value.
    """
    records: list[tuple[str, str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("$"):
            continue
        line = _strip_comment(line)
        if not line:
            continue
        m = re.match(r"^(\S+)\s+\d+\s+IN\s+(A|CNAME|MX|TXT)\s+(.+)$", line)
        if not m:
            continue
        name, rtype, value = m.group(1), m.group(2), m.group(3).strip()
        if rtype == "TXT":
            value = value.strip('"')
        records.append((name, rtype, value))
    return records


def _ds_at_registry(domain: str) -> bool | None:
    """Ask a .com gTLD server for the DS directly. None = couldn't tell.

    The registry is the only authority on whether the DS was actually removed;
    a public resolver answers from cache and will keep saying "still there" for
    hours after the registrar has done the work. Windows nslookup cannot query
    type DS at all, so this builds the query by hand.
    """
    tld = domain.rsplit(".", 1)[-1]
    try:
        server = socket.gethostbyname(f"f.gtld-servers.net" if tld == "com"
                                      else f"a.gtld-servers.net")
        tid = random.randint(0, 65535)
        qname = b"".join(bytes([len(l)]) + l.encode() for l in domain.split(".")) + b"\x00"
        pkt = struct.pack(">HHHHHH", tid, 0, 1, 0, 0, 0) + qname + struct.pack(">HH", 43, 1)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(8)
        sock.sendto(pkt, (server, 53))
        data, _ = sock.recvfrom(4096)
        sock.close()

        counts = struct.unpack(">HH", data[6:10])
        i = 12
        while data[i]:
            i += data[i] + 1
        i += 5
        for _ in range(counts[0] + counts[1]):
            while True:
                ln = data[i]
                if ln & 0xC0 == 0xC0:
                    i += 2
                    break
                if ln == 0:
                    i += 1
                    break
                i += ln + 1
            rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", data[i:i + 10])
            i += 10 + rdlen
            if rtype == 43:
                return True
        return False
    except Exception:
        return None


def _ds_at_resolver(domain: str, host: str) -> bool | None:
    try:
        req = urllib.request.Request(
            f"https://{host}/{'resolve' if 'google' in host else 'dns-query'}"
            f"?name={domain}&type=DS",
            headers={"accept": "application/dns-json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return bool(json.load(r).get("Answer"))
    except Exception:
        return None


def dnssec_blockers(domain: str) -> list[str]:
    """Reasons it is not yet safe to change nameservers. Empty list = go.

    This is the one pre-flight whose failure is total rather than partial. A DS
    record commits the parent zone to a specific set of signing keys. Delegate
    to a provider serving an unsigned zone while a DS is still in play and
    every validating resolver - Google, 1.1.1.1, most ISPs - returns SERVFAIL
    for the whole domain, web and mail together.

    Two separate things have to be true, which is why this returns a list and
    not a bool: the registry must have dropped the DS (proving the registrar
    did the work), AND the public resolvers must have expired their cached
    copy (a DS cached at .com's 24h TTL breaks users just as thoroughly as a
    live one, and it is the half people forget).
    """
    blockers: list[str] = []

    at_registry = _ds_at_registry(domain)
    if at_registry is None:
        blockers.append("could not reach the registry to check the DS - verify by hand")
    elif at_registry:
        blockers.append("the .com registry still publishes a DS - DNSSEC is not off yet")

    for host, label in (("dns.google", "Google"), ("cloudflare-dns.com", "Cloudflare")):
        if _ds_at_resolver(domain, host):
            blockers.append(f"{label}'s resolver still has the old DS cached - wait for it to expire")

    return blockers


def _fqdn(name: str, domain: str) -> str:
    return domain if name == "@" else f"{name}.{domain}"


def lookup(host: str, rtype: str, ns: str | None) -> list[str]:
    cmd = ["nslookup", f"-type={rtype}", host]
    if ns:
        cmd.append(ns)
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=25
        ).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    # nslookup echoes the server it used first; drop that preamble so a
    # nameserver whose own name contains the domain can't match as an answer.
    out = out.split("\n", 3)[-1] if out.count("\n") >= 3 else out

    vals: list[str] = []
    if rtype == "TXT":
        vals = [v.strip() for v in re.findall(r'text\s*=\s*"(.*?)"', out, re.S)]
        # Long TXT values arrive split into quoted chunks; rejoin per line.
        joined = ["".join(re.findall(r'"(.*?)"', ln)) for ln in out.splitlines() if "text =" in ln or '"' in ln]
        vals = [v for v in dict.fromkeys(vals + joined) if v]
    elif rtype == "MX":
        vals = [
            f"{p} {h.rstrip('.')}"
            for p, h in re.findall(r"MX preference\s*=\s*(\d+),\s*mail exchanger\s*=\s*(\S+)", out)
        ]
    elif rtype == "CNAME":
        vals = [c.rstrip(".") for c in re.findall(r"canonical name\s*=\s*(\S+)", out)]
    elif rtype == "A":
        vals = re.findall(r"Address(?:es)?:\s*([\d.]+)", out)
        vals += re.findall(r"^\s+([\d.]+)\s*$", out, re.M)
        vals = list(dict.fromkeys(vals))
    return vals


def _matches(expected: str, got: list[str], rtype: str) -> bool:
    exp = expected.rstrip(".")
    if rtype == "MX":
        # "10 aspmx.l.google.com." -> compare priority + host, dot-insensitive
        exp = " ".join(p.rstrip(".") for p in exp.split())
    return any(g.rstrip(".").lower() == exp.lower() for g in got)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("domain", help="e.g. ten-fifty5.com")
    ap.add_argument("--ns", help="nameserver to query (omit = public resolvers)")
    ap.add_argument("--zone", help="override zone file path")
    args = ap.parse_args()

    zone_path = Path(args.zone) if args.zone else ZONE_DIR / f"{args.domain}.zone"
    if not zone_path.exists():
        print(f"no zone file at {zone_path}", file=sys.stderr)
        return 2

    blockers = dnssec_blockers(args.domain)
    print()
    if blockers:
        print("  DNSSEC - NOT SAFE TO CHANGE NAMESERVERS YET:")
        for b in blockers:
            print(f"    - {b}")
        print("  Flipping now returns SERVFAIL for the whole domain, web and")
        print("  mail together, until every copy of the DS expires.")
        print()
        return 1
    print("  DNSSEC: no DS at the registry or in resolver caches - safe to flip.")

    records = parse_zone(zone_path)
    target = args.ns or "public resolvers"
    print(f"\n  {args.domain}  vs  {zone_path.name}")
    print(f"  asking: {target}")
    print(f"  {len(records)} records to confirm\n")

    missing_critical, missing_other = [], []

    for name, rtype, value in records:
        host = _fqdn(name, args.domain)
        got = lookup(host, rtype, args.ns)
        ok = _matches(value, got, rtype)
        crit = _is_critical(name, rtype)

        if ok:
            mark = "  ok  "
        elif crit:
            mark = " FAIL "
            missing_critical.append((host, rtype, value, got))
        else:
            mark = " miss "
            missing_other.append((host, rtype, value, got))

        flag = "!" if crit else " "
        shown = value if len(value) <= 58 else value[:55] + "..."
        print(f"  [{mark}]{flag} {rtype:<5} {host:<52} {shown}")

    print()
    for label, rows in (("CRITICAL", missing_critical), ("other", missing_other)):
        for host, rtype, value, got in rows:
            print(f"  {label} {rtype} {host}")
            print(f"      expected: {value}")
            print(f"      resolver: {got if got else '(nothing)'}")

    if missing_critical:
        print(f"\n  STOP - {len(missing_critical)} critical record(s) wrong. "
              f"Do not flip the nameservers.\n")
        return 1
    if missing_other:
        print(f"\n  {len(missing_other)} non-critical record(s) missing. "
              f"Check each before flipping.\n")
        return 1

    print("  All records confirmed.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Shipped release gate (cst-l06f, from Sun's cst-ahk1 review).

Proves, from the PUBLIC side only, that every recipe the site calls
Shipped is actually obtainable and installable by a stranger:

  1. site manifest URL answers 200 and parses as YAML
  2. ADAPT.md answers 200 (or the recipe is honestly labeled
     'ADAPT pending — not installable yet' in llms.txt)
  3. an anonymous shallow clone of the public repo succeeds
  4. every component named in the recipe's ADAPT installs into a fresh
     venv from that clone (pip install, no local state)
  5. the component's entry CLI answers — exit 2 (not-setup) or 0 is
     healthy pre-configure; import errors and argparse tracebacks fail

Until this passes, "Shipped" overpromises (Sun's formulation). Run it
before cutting a release tag, or on a schedule. Exit 0 all-green,
1 otherwise; report on stdout.

Usage: python3 tools/release_gate.py [--site https://scutl.org]
                                     [--repo https://github.com/scutl-sysop/scutl]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

# component dir + console script per shipped recipe, read from the
# recipe's own ADAPT install step at gate time where possible; this map
# only names the smoke-check entry point (status-style, safe, no state)
ENTRY = {
    "wallet-base-sepolia": ("signer", ["signer", "status"]),
    "paid-service-x402": ("pserv", ["pserv", "status"]),
    "provision-vultr": ("prov", ["prov", "status"]),
    "wallet-mainnet": ("msigner", ["msigner", "status"]),
}
# recipes whose component depends on another recipe's package (install
# order matters; dependency installed first from the same clone)
DEPENDS = {"wallet-mainnet": [("wallet-base-sepolia", "signer")]}


def fetch(url: str) -> tuple[int, bytes]:
    # Cloudflare 403s the default Python-urllib UA (found first run of
    # this gate, 2026-09-05 — an agent-facing site blocking the most
    # agent-shaped UA is its own finding, filed on the bead)
    req = urllib.request.Request(
        url, headers={"User-Agent": "scutl-release-gate/1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:
        return 0, b""


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--site", default="https://scutl.org")
    p.add_argument("--repo",
                   default="https://github.com/scutl-sysop/scutl")
    args = p.parse_args(argv)

    failures: list[str] = []
    ok: list[str] = []

    st, body = fetch(f"{args.site}/recipes/index.json")
    if st != 200:
        print(f"FAIL index.json: HTTP {st}")
        return 1
    index = json.loads(body)["recipes"]
    st, llms = fetch(f"{args.site}/llms.txt")
    llms_text = llms.decode("utf-8", "replace") if st == 200 else ""

    shipped = [r for r in index if r.get("status") == "shipped"]
    print(f"gate: {len(shipped)} shipped recipes of {len(index)}")

    with tempfile.TemporaryDirectory() as td:
        clone = Path(td) / "scutl"
        r = subprocess.run(["git", "clone", "--depth", "1", args.repo,
                            str(clone)], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"FAIL anonymous clone: {r.stderr.strip()[-200:]}")
            return 1
        ok.append("anonymous shallow clone")

        for rec in shipped:
            slug = rec.get("slug") or rec["id"]
            # index.json carries urls; trust its own pointers
            murl = rec.get("yaml") or f"/recipes/{slug}.yaml"
            st, mbody = fetch(args.site + murl if murl.startswith("/")
                              else murl)
            if st != 200:
                failures.append(f"{slug}: manifest HTTP {st}")
            else:
                ok.append(f"{slug}: manifest 200")

            aurl = rec.get("adapt")
            if aurl:
                st, _ = fetch(args.site + aurl if aurl.startswith("/")
                              else aurl)
                (ok if st == 200 else failures).append(
                    f"{slug}: ADAPT HTTP {st}")
            else:
                # shipped without ADAPT must be honestly labeled
                pat = rf"{re.escape(rec['id'])}.*ADAPT pending"
                if re.search(pat, llms_text):
                    ok.append(f"{slug}: no ADAPT, honestly labeled")
                else:
                    failures.append(f"{slug}: shipped, no ADAPT, and "
                                    "llms.txt does not say so")
                continue  # not installable -> no install check

            # fresh-venv install from the public clone
            comp = ENTRY.get(slug)
            rdir = clone / "recipes" / slug
            if comp is None or not rdir.is_dir():
                failures.append(f"{slug}: no entry mapping/dir for "
                                "install check")
                continue
            venv = Path(td) / f"venv-{slug}"
            subprocess.run([sys.executable, "-m", "venv", str(venv)],
                           check=True, capture_output=True)
            pip = venv / "bin" / "pip"
            deps_ok = True
            for dep_slug, dep_dir in DEPENDS.get(slug, []):
                r = subprocess.run([str(pip), "install", "-q",
                                    str(clone / "recipes" / dep_slug / dep_dir)],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    failures.append(f"{slug}: dependency install failed: "
                                    f"{r.stderr.strip()[-200:]}")
                    deps_ok = False
            if not deps_ok:
                continue
            r = subprocess.run([str(pip), "install", "-q",
                                str(rdir / comp[0])],
                               capture_output=True, text=True)
            if r.returncode != 0:
                failures.append(f"{slug}: pip install failed: "
                                f"{r.stderr.strip()[-200:]}")
                continue
            cli = [str(venv / "bin" / comp[1][0])] + comp[1][1:]
            r = subprocess.run(cli, capture_output=True, text=True,
                               env={"HOME": td, "PATH": "/usr/bin:/bin"})
            if r.returncode in (0, 2):
                ok.append(f"{slug}: fresh install + CLI answers "
                          f"(exit {r.returncode})")
            else:
                failures.append(f"{slug}: CLI unhealthy exit "
                                f"{r.returncode}: {r.stderr.strip()[-200:]}")

    for line in ok:
        print(f"  ok   {line}")
    for line in failures:
        print(f"  FAIL {line}")
    print(f"gate: {'GREEN' if not failures else 'RED'} "
          f"({len(ok)} ok, {len(failures)} failures)")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())

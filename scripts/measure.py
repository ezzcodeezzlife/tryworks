#!/usr/bin/env python3
"""Measure what a requirement costs to install: resolved packages, download size, advisories.

    python scripts/measure.py "unstructured[docx,pptx,xlsx,md,csv,pdf]" "tryworks[pdf] @ ."

Each requirement is resolved exactly with ``uv pip compile`` for CPython 3.12 on Linux x86-64
(the typical container or serverless target). Download size is the sum of the wheels that would
be installed. Advisory counts come from OSV.dev and are reported two ways:

- "open": advisories that affect the exact versions resolved today;
- "historical": every advisory ever published for those packages, a rough measure of how much
  scanner and triage work a dependency tree generates over its lifetime.

Requires ``uv`` on PATH and network access. ``--json`` writes machine-readable results.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

PLATFORM = "x86_64-manylinux_2_28"
PYTHON = "3.12"


def resolve(requirement: str, root: Path) -> list[tuple[str, str]]:
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv is required: https://docs.astral.sh/uv/")
    if requirement.endswith("@ ."):
        requirement = requirement[:-1] + root.resolve().as_uri()
    with tempfile.TemporaryDirectory() as tmp:
        req_file = Path(tmp) / "requirements.in"
        req_file.write_text(requirement + "\n", encoding="utf-8")
        out = subprocess.run(
            [uv, "pip", "compile", str(req_file), "--python-version", PYTHON, "--python-platform", PLATFORM,
             "--no-header", "--no-annotate", "--quiet"],
            capture_output=True, text=True, check=True,
        ).stdout
    pins = []
    for line in out.splitlines():
        line = line.strip()
        match = re.match(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?==([^\s;]+)", line)
        if match:
            pins.append((match.group(1).lower(), match.group(2)))
        elif " @ file:" in line:
            pins.append((line.split(" @ ")[0].split("[")[0].lower(), "local"))
    return pins


def _get_json(url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": "tryworks-measure"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def wheel_size(name: str, version: str, root: Path) -> int:
    if version == "local":
        wheels = sorted((root / "dist").glob("*.whl"))
        return wheels[-1].stat().st_size if wheels else 0
    files = _get_json(f"https://pypi.org/pypi/{name}/{version}/json")["urls"]
    wheels = [f for f in files if f["packagetype"] == "bdist_wheel"]

    def score(f: dict) -> int:
        fn = f["filename"]
        return (4 if "manylinux" in fn and "x86_64" in fn else 0) + (3 if "none-any" in fn else 0) + (2 if "cp312" in fn else 0) + (1 if "abi3" in fn else 0)

    chosen = max(wheels, key=score) if wheels else (files[0] if files else None)
    return chosen["size"] if chosen else 0


def advisories(pins: list[tuple[str, str]]) -> tuple[int, int, dict[str, int]]:
    published = [(n, v) for n, v in pins if v != "local"]
    if not published:
        return 0, 0, {}
    batch = _get_json(
        "https://api.osv.dev/v1/querybatch",
        {"queries": [{"package": {"name": n, "ecosystem": "PyPI"}, "version": v} for n, v in published]},
    )
    open_count = sum(len(r.get("vulns") or []) for r in batch.get("results", []))

    def historical(name: str) -> tuple[str, int]:
        result = _get_json("https://api.osv.dev/v1/query", {"package": {"name": name, "ecosystem": "PyPI"}})
        return name, len(result.get("vulns") or [])

    with concurrent.futures.ThreadPoolExecutor(12) as pool:
        per_package = dict(pool.map(historical, [n for n, _ in published]))
    return open_count, sum(per_package.values()), per_package


def measure(requirement: str, root: Path) -> dict:
    pins = resolve(requirement, root)
    with concurrent.futures.ThreadPoolExecutor(16) as pool:
        sizes = dict(zip(pins, pool.map(lambda p: wheel_size(p[0], p[1], root), pins)))
    open_count, historical_count, per_package = advisories(pins)
    largest = sorted(((size, name) for (name, _), size in sizes.items()), reverse=True)[:5]
    return {
        "requirement": requirement,
        "packages": len(pins),
        "download_mb": round(sum(sizes.values()) / 1048576, 1),
        "largest": [{"package": name, "mb": round(size / 1048576, 1)} for size, name in largest],
        "open_advisories": open_count,
        "historical_advisories": historical_count,
        "most_historical": sorted(per_package.items(), key=lambda kv: -kv[1])[:5],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("requirements", nargs="+")
    parser.add_argument("--json", type=Path, help="also write results to this file")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    results = []
    for requirement in args.requirements:
        r = measure(requirement, root)
        results.append(r)
        largest = ", ".join("{} {} MB".format(x["package"], x["mb"]) for x in r["largest"])
        print(r["requirement"])
        print(f"  packages: {r['packages']}   download: {r['download_mb']} MB")
        print(f"  largest:  {largest}")
        print(f"  advisories: {r['open_advisories']} open for resolved versions, {r['historical_advisories']} historical")
    if args.json:
        args.json.write_text(json.dumps(results, indent=1), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())

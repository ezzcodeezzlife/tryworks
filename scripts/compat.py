#!/usr/bin/env python3
"""Measure how closely tryworks reproduces unstructured's output on the same documents.

Run the dump step once in an environment with unstructured and once with tryworks, then compare:

    python scripts/compat.py dump unstructured CORPUS_DIR upstream.json
    python scripts/compat.py dump tryworks CORPUS_DIR tryworks.json
    python scripts/compat.py compare upstream.json tryworks.json --markdown compat/REPORT.md

Elements are aligned by their normalized text. For each document the report gives text
similarity, how many elements line up, and how often aligned elements agree on type, id and
key metadata. The dump step uses only the standard library plus the implementation under test.
"""

from __future__ import annotations

import argparse
import difflib
import json
import platform
import sys
import time
from pathlib import Path

COMPARED_FIELDS = (
    "category_depth",
    "emphasized_text_contents",
    "filetype",
    "header_footer_type",
    "languages",
    "link_urls",
    "page_name",
    "page_number",
    "parent_id",
    "sent_from",
    "subject",
    "text_as_html",
)


def dump(impl: str, corpus: Path, out: Path) -> None:
    started = time.perf_counter()
    if impl == "unstructured":
        from unstructured.__version__ import __version__ as version
        from unstructured.partition.auto import partition
    elif impl == "tryworks":
        from tryworks.__version__ import __version__ as version
        from tryworks.partition.auto import partition
    else:
        raise SystemExit(f"unknown implementation {impl!r}")
    import_seconds = time.perf_counter() - started

    files = {}
    for path in sorted(p for p in corpus.iterdir() if p.is_file()):
        kwargs = {"strategy": "fast"} if path.suffix.lower() == ".pdf" else {}
        t0 = time.perf_counter()
        try:
            elements = partition(filename=str(path), **kwargs)
            files[path.name] = {
                "seconds": round(time.perf_counter() - t0, 4),
                "elements": [e.to_dict() for e in elements],
            }
        except Exception as e:  # noqa: BLE001 -- record every failure in the report
            files[path.name] = {"error": f"{type(e).__name__}: {e}"}
    payload = {
        "impl": impl,
        "version": version,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "import_seconds": round(import_seconds, 3),
        "files": files,
    }
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out} ({len(files)} files, import {import_seconds:.2f}s)")


def _norm(text: str) -> str:
    return " ".join(text.split())


def _content(elements: list[dict]) -> list[dict]:
    return [e for e in elements if e.get("type") != "PageBreak"]


def compare_file(upstream: dict, candidate: dict) -> dict:
    if "error" in upstream or "error" in candidate:
        return {"error": upstream.get("error") or candidate.get("error"), "failed": "upstream" if "error" in upstream else "tryworks"}
    ours, theirs = _content(upstream["elements"]), _content(candidate["elements"])
    text_a, text_b = [_norm(e["text"]) for e in ours], [_norm(e["text"]) for e in theirs]
    text_similarity = difflib.SequenceMatcher(None, " ".join(text_a), " ".join(text_b), autojunk=False).ratio()
    matcher = difflib.SequenceMatcher(None, text_a, text_b, autojunk=False)
    pairs = [
        (ours[block.a + k], theirs[block.b + k])
        for block in matcher.get_matching_blocks()
        for k in range(block.size)
    ]
    id_map = {a["element_id"]: b["element_id"] for a, b in pairs}
    fields: dict[str, list[int]] = {}
    for a, b in pairs:
        ma, mb = a.get("metadata", {}), b.get("metadata", {})
        for name in COMPARED_FIELDS:
            if name not in ma and name not in mb:
                continue
            va, vb = ma.get(name), mb.get(name)
            if name == "parent_id":
                va = id_map.get(va, va) if va else None
                agree = (va is None and vb is None) or (va is not None and va == vb)
            else:
                agree = va == vb
            counts = fields.setdefault(name, [0, 0])
            counts[0] += agree
            counts[1] += 1

    differences = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for a, b in zip(ours[i1:i2], theirs[j1:j2]):
                label = f"{_norm(a['text'])[:60]!r}"
                if a["type"] != b["type"]:
                    differences.append(f"type    {a['type']} -> {b['type']}: {label}")
                ma, mb = a.get("metadata", {}), b.get("metadata", {})
                for name in COMPARED_FIELDS:
                    va, vb = ma.get(name), mb.get(name)
                    if name == "parent_id":
                        va = id_map.get(va, va) if va else None
                    if va != vb:
                        differences.append(f"field   {name}: upstream={json.dumps(va)[:70]} tryworks={json.dumps(vb)[:70]} on {label}")
        else:
            removed = " | ".join(f"{e['type']}:{_norm(e['text'])[:60]!r}" for e in ours[i1:i2])
            added = " | ".join(f"{e['type']}:{_norm(e['text'])[:60]!r}" for e in theirs[j1:j2])
            differences.append(f"{tag:7} upstream [{removed}] tryworks [{added}]")

    return {
        "upstream_elements": len(ours),
        "tryworks_elements": len(theirs),
        "text_similarity": round(text_similarity, 4),
        "aligned": len(pairs),
        "type_agreement": sum(a["type"] == b["type"] for a, b in pairs),
        "id_agreement": sum(a["element_id"] == b["element_id"] for a, b in pairs),
        "fields": fields,
        "differences": differences,
        "upstream_seconds": upstream.get("seconds"),
        "tryworks_seconds": candidate.get("seconds"),
    }


def _pct(part: int, whole: int) -> str:
    return "n/a" if not whole else f"{100 * part / whole:.0f}%"


def compare(upstream_path: Path, candidate_path: Path, markdown: Path | None, summary: Path | None) -> None:
    upstream = json.loads(upstream_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    results = {
        name: compare_file(upstream["files"][name], candidate["files"].get(name, {"error": "missing"}))
        for name in upstream["files"]
    }

    total_up = sum(r.get("upstream_elements", 0) for r in results.values())
    total_aligned = sum(r.get("aligned", 0) for r in results.values())
    total_types = sum(r.get("type_agreement", 0) for r in results.values())
    total_ids = sum(r.get("id_agreement", 0) for r in results.values())
    field_totals: dict[str, list[int]] = {}
    for r in results.values():
        for name, (agree, seen) in r.get("fields", {}).items():
            t = field_totals.setdefault(name, [0, 0])
            t[0] += agree
            t[1] += seen

    lines = [
        "# Compatibility report",
        "",
        f"Upstream: unstructured {upstream['version']} (Python {upstream['python']}, {upstream['platform']}), "
        f"import {upstream['import_seconds']}s.  ",
        f"Candidate: tryworks {candidate['version']} (Python {candidate['python']}, {candidate['platform']}), "
        f"import {candidate['import_seconds']}s.",
        "",
        "Elements are aligned by normalized text; PageBreak elements are ignored. PDFs use the fast strategy.",
        "",
        f"**Overall:** {_pct(total_aligned, total_up)} of upstream elements have an identical-text counterpart; "
        f"of those, {_pct(total_types, total_aligned)} have the same type and {_pct(total_ids, total_aligned)} the same element id.",
        "",
        "| Document | Upstream elements | tryworks elements | Text similarity | Aligned | Same type | Same id | Upstream s | tryworks s |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, r in results.items():
        if "error" in r:
            lines.append(f"| {name} | {r['failed']} failed: {r['error'][:80]} | | | | | | | |")
            continue
        lines.append(
            f"| {name} | {r['upstream_elements']} | {r['tryworks_elements']} | {100 * r['text_similarity']:.1f}% "
            f"| {r['aligned']} | {_pct(r['type_agreement'], r['aligned'])} | {_pct(r['id_agreement'], r['aligned'])} "
            f"| {r['upstream_seconds']} | {r['tryworks_seconds']} |"
        )
    lines += ["", "## Metadata agreement on aligned elements", "", "| Field | Agree | Compared |", "|---|---:|---:|"]
    for name in sorted(field_totals):
        agree, seen = field_totals[name]
        lines.append(f"| `{name}` | {_pct(agree, seen)} | {seen} |")
    lines += ["", "## Differences", ""]
    for name, r in results.items():
        diffs = r.get("differences") or []
        if not diffs:
            continue
        lines.append(f"### {name}")
        lines.append("")
        lines.append("```")
        lines.extend(diffs[:25])
        if len(diffs) > 25:
            lines.append(f"... {len(diffs) - 25} more")
        lines.append("```")
        lines.append("")

    report = "\n".join(lines) + "\n"
    if markdown:
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(report, encoding="utf-8")
        print(f"wrote {markdown}")
    else:
        sys.stdout.write(report)
    if summary:
        summary.write_text(
            json.dumps(
                {
                    "upstream_version": upstream["version"],
                    "tryworks_version": candidate["version"],
                    "aligned_pct": round(100 * total_aligned / total_up, 1) if total_up else None,
                    "type_agreement_pct": round(100 * total_types / total_aligned, 1) if total_aligned else None,
                    "id_agreement_pct": round(100 * total_ids / total_aligned, 1) if total_aligned else None,
                    "files": {k: {kk: vv for kk, vv in v.items() if kk != "differences"} for k, v in results.items()},
                },
                indent=1,
            ),
            encoding="utf-8",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("dump", help="partition every file in a corpus directory and save the elements")
    d.add_argument("impl", choices=["unstructured", "tryworks"])
    d.add_argument("corpus", type=Path)
    d.add_argument("out", type=Path)
    c = sub.add_parser("compare", help="compare two dumps")
    c.add_argument("upstream", type=Path)
    c.add_argument("candidate", type=Path)
    c.add_argument("--markdown", type=Path)
    c.add_argument("--summary", type=Path)
    args = parser.parse_args()
    if args.command == "dump":
        dump(args.impl, args.corpus, args.out)
    else:
        compare(args.upstream, args.candidate, args.markdown, args.summary)


if __name__ == "__main__":
    main()

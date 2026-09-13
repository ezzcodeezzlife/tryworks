"""Command line: ``tryworks report.pdf`` prints the document's elements as JSON."""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from tryworks.__version__ import __version__


def _render(elements: list, fmt: str) -> str:
    from tryworks.staging import base

    if fmt == "json":
        return base.elements_to_json(elements, indent=2)
    if fmt == "ndjson":
        return base.elements_to_ndjson(elements)
    if fmt == "markdown":
        return base.elements_to_md(elements)
    if fmt == "csv":
        return base.convert_to_csv(elements)
    return base.convert_to_text(elements)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tryworks",
        description="Partition a document (pdf, docx, pptx, xlsx, html, md, txt, csv, tsv, eml, json) into elements.",
    )
    parser.add_argument("source", help="path to a document, or an http(s) URL")
    parser.add_argument("-f", "--format", choices=["json", "ndjson", "text", "markdown", "csv"], default="json")
    parser.add_argument("-o", "--output", help="write to this file instead of standard output")
    parser.add_argument("--content-type", help="MIME type, when the extension does not say")
    parser.add_argument("--chunking-strategy", choices=["basic", "by_title"], help="chunk the elements")
    parser.add_argument("--max-characters", type=int, help="chunk size limit (default 500)")
    parser.add_argument("--include-page-breaks", action="store_true", help="emit PageBreak elements")
    parser.add_argument("--version", action="version", version=f"tryworks {__version__}")
    args = parser.parse_args(argv)

    from tryworks.partition.auto import UnsupportedFileFormatError, partition

    kwargs: dict = {"content_type": args.content_type}
    if args.chunking_strategy:
        kwargs["chunking_strategy"] = args.chunking_strategy
    if args.max_characters:
        kwargs["max_characters"] = args.max_characters
    if args.include_page_breaks:
        kwargs["include_page_breaks"] = True
    source = args.source
    try:
        if source.lower().startswith(("http://", "https://")):
            elements = partition(url=source, **kwargs)
        else:
            elements = partition(filename=source, **kwargs)
    except (UnsupportedFileFormatError, FileNotFoundError, ValueError, NotImplementedError, ImportError) as e:
        print(f"tryworks: {e}", file=sys.stderr)
        return 2

    rendered = _render(elements, args.format)
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="") as f:
            f.write(rendered)
    else:
        sys.stdout.buffer.write(rendered.encode("utf-8"))
        if not rendered.endswith("\n"):
            sys.stdout.buffer.write(b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

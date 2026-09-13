"""Partition CSV and TSV files into a single Table element."""

from __future__ import annotations

import csv
import io
from typing import IO, Any, Optional

from tryworks.documents.elements import Element, ElementMetadata, Table
from tryworks.partition._html_table import htmlify_matrix_of_cell_texts, table_text
from tryworks.partition.common import FileType, apply_metadata, decode_bytes, exactly_one, read_bytes

# -- guard against pathological inputs; a CSV larger than this is not a "table" for an LLM --
csv.field_size_limit(16 * 1024 * 1024)


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _read_rows(text: str, delimiter: Optional[str]) -> list[list[str]]:
    delimiter = delimiter or _sniff_delimiter(text[:65536])
    rows = [row for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)]
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    width = max((len(row) for row in rows), default=0)
    return [row + [""] * (width - len(row)) for row in rows]


def _partition_delimited(
    filename: Optional[str],
    file: Optional[IO[Any]],
    encoding: Optional[str],
    infer_table_structure: bool,
    delimiter: Optional[str],
) -> list[Element]:
    exactly_one(filename=filename, file=file)
    data = read_bytes(filename=filename, file=file)
    rows = _read_rows(decode_bytes(data, encoding)[1], delimiter)
    text = table_text(rows)
    html = htmlify_matrix_of_cell_texts(rows)
    return [
        Table(
            text=text,
            metadata=ElementMetadata(text_as_html=html if infer_table_structure else None),
            detection_origin="csv",
        )
    ]


@apply_metadata(FileType.CSV)
def partition_csv(
    filename: Optional[str] = None,
    *,
    file: Optional[IO[Any]] = None,
    encoding: Optional[str] = None,
    include_header: bool = False,
    infer_table_structure: bool = True,
    **kwargs: Any,
) -> list[Element]:
    """Partition a CSV file. The delimiter (comma, semicolon, tab or pipe) is detected.

    Every row, including a header row, contributes to the table text. Cell values are kept as
    written in the file, so "0042" stays "0042" rather than becoming the number 42.
    """
    return _partition_delimited(filename, file, encoding, infer_table_structure, kwargs.get("delimiter"))


@apply_metadata(FileType.TSV)
def partition_tsv(
    filename: Optional[str] = None,
    *,
    file: Optional[IO[Any]] = None,
    encoding: Optional[str] = None,
    include_header: bool = False,
    infer_table_structure: bool = True,
    **kwargs: Any,
) -> list[Element]:
    """Partition a tab-separated file."""
    return _partition_delimited(filename, file, encoding, infer_table_structure, "\t")

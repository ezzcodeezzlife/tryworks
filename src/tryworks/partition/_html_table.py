"""Render table cell text as the compact HTML used in ``metadata.text_as_html``."""

from __future__ import annotations

import html
from typing import Sequence


def _normalize(cell_text: str) -> str:
    return " ".join(cell_text.split())


def _td(cell_text: str, colspan: int = 1, rowspan: int = 1) -> str:
    escaped = "<br/>".join(html.escape(cell_text).split("\n"))
    text = " ".join(escaped.split())
    attrs = (f' colspan="{colspan}"' if colspan > 1 else "") + (f' rowspan="{rowspan}"' if rowspan > 1 else "")
    return f"<td{attrs}>{text}</td>" if text else f"<td{attrs}/>"


def htmlify_matrix_of_cell_texts(matrix: Sequence[Sequence[str]]) -> str:
    """Like ``<table><tr><td>a</td><td/></tr></table>``; empty string for an empty matrix."""
    if not matrix:
        return ""
    rows = "".join(f"<tr>{''.join(_td(cell) for cell in row)}</tr>" for row in matrix)
    return f"<table>{rows}</table>"


def htmlify_matrix_of_spanned_cells(matrix: Sequence[Sequence[tuple[str, int, int]]]) -> str:
    """Same as ``htmlify_matrix_of_cell_texts`` for (text, colspan, rowspan) cells.

    A row whose cells are all covered by a rowspan from above is still emitted as ``<tr></tr>``
    so row counts stay correct.
    """
    if not matrix:
        return ""
    rows = "".join(f"<tr>{''.join(_td(t, c, r) for t, c, r in row)}</tr>" for row in matrix)
    return f"<table>{rows}</table>"


def table_text(matrix: Sequence[Sequence[str]]) -> str:
    """All non-empty cell texts joined by single spaces, row by row."""
    return " ".join(_normalize(cell) for row in matrix for cell in row if _normalize(cell))

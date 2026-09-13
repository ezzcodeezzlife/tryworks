"""Partition .xlsx workbooks with the standard library.

Each worksheet is split into sub-tables: groups of non-empty cells connected horizontally or
vertically, merged when their row ranges overlap. Within a sub-table, leading and trailing rows
holding a single value (captions, notes) become text elements; the rest becomes a Table.
``metadata.page_number`` is the sheet's position and ``metadata.page_name`` its name.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from typing import IO, Any, Iterator, Optional

from tryworks.cleaners.core import clean_bullets
from tryworks.documents.elements import (
    Element,
    ElementMetadata,
    ListItem,
    NarrativeText,
    Table,
    Text,
    Title,
)
from tryworks.partition._html_table import htmlify_matrix_of_cell_texts, table_text
from tryworks.partition._ooxml import (
    REL_SHARED_STRINGS,
    REL_STYLES,
    OoxmlPackage,
    UnsafeDocumentError,
    qn,
)
from tryworks.partition.common import FileType, apply_metadata, exactly_one, read_bytes
from tryworks.partition.text_type import (
    is_bulleted_text,
    is_possible_narrative_text,
    is_possible_numbered_list,
    is_possible_title,
)

_CELL_REF_RE = re.compile(r"^([A-Z]+)(\d+)$")
_DATE_FORMAT_IDS = frozenset([14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47])
_DATE_CODE_RE = re.compile(r"[dmyhs]", re.IGNORECASE)
_EXCEL_EPOCH = dt.datetime(1899, 12, 30)


def _max_cells() -> int:
    try:
        return int(os.environ.get("TRYWORKS_MAX_SHEET_CELLS", 5_000_000))
    except ValueError:
        return 5_000_000


def _column_index(letters: str) -> int:
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - 64)
    return index - 1


def _text_of(node) -> str:
    """Concatenate ``<t>`` runs of a shared or inline string, skipping phonetic runs."""
    parts = []
    for child in node.iter():
        if child.tag == qn("s:rPh"):
            continue
        if child.tag == qn("s:t") and child.text:
            parts.append(child.text)
    return "".join(parts)


class _Workbook:
    def __init__(self, package: OoxmlPackage):
        self._pkg = package
        self.workbook_part = package.main_part() or "xl/workbook.xml"
        rels = package.rels(self.workbook_part)
        self.shared_strings = self._load_shared_strings(rels)
        self.date_styles = self._load_date_styles(rels)
        root = package.xml(self.workbook_part)
        if root is None:
            raise ValueError("Workbook part is missing; not a valid .xlsx file.")
        self.date1904 = any(
            (pr.get("date1904") or "").lower() in ("1", "true") for pr in root.iter(qn("s:workbookPr"))
        )
        self.sheets: list[tuple[str, str]] = []
        for sheet in root.iter(qn("s:sheet")):
            rel = rels.get(sheet.get(qn("r:id")) or "")
            if rel and not rel[2]:
                self.sheets.append((sheet.get("name") or "", rel[0]))

    def _load_shared_strings(self, rels: dict[str, tuple[str, str, bool]]) -> list[str]:
        part = next((t for t, typ, ext in rels.values() if typ == REL_SHARED_STRINGS and not ext), None)
        root = self._pkg.xml(part) if part else None
        return [] if root is None else [_text_of(si) for si in root.findall(qn("s:si"))]

    def _load_date_styles(self, rels: dict[str, tuple[str, str, bool]]) -> set[int]:
        part = next((t for t, typ, ext in rels.values() if typ == REL_STYLES and not ext), None)
        root = self._pkg.xml(part) if part else None
        if root is None:
            return set()
        custom_date_ids = set()
        for fmt in root.iter(qn("s:numFmt")):
            code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", fmt.get("formatCode") or "")
            if _DATE_CODE_RE.search(code):
                custom_date_ids.add(int(fmt.get("numFmtId") or -1))
        cell_xfs = root.find(qn("s:cellXfs"))
        date_styles = set()
        if cell_xfs is not None:
            for index, xf in enumerate(cell_xfs.findall(qn("s:xf"))):
                fmt_id = int(xf.get("numFmtId") or 0)
                if fmt_id in _DATE_FORMAT_IDS or fmt_id in custom_date_ids:
                    date_styles.add(index)
        return date_styles

    def _format_number(self, raw: str, style: int) -> str:
        try:
            number = float(raw)
        except ValueError:
            return raw
        if style in self.date_styles:
            epoch = dt.datetime(1904, 1, 1) if self.date1904 else _EXCEL_EPOCH
            try:
                return (epoch + dt.timedelta(days=number)).strftime("%Y-%m-%d %H:%M:%S")
            except OverflowError:
                return raw
        if number.is_integer() and abs(number) < 1e16:
            return str(int(number))
        return repr(number)

    def read_sheet(self, part: str) -> dict[tuple[int, int], str]:
        root = self._pkg.xml(part)
        cells: dict[tuple[int, int], str] = {}
        if root is None:
            return cells
        limit = _max_cells()
        for row_index, row in enumerate(root.iter(qn("s:row"))):
            row_number = int(row.get("r") or row_index + 1) - 1
            next_col = 0
            for c in row.findall(qn("s:c")):
                ref = _CELL_REF_RE.match(c.get("r") or "")
                col = _column_index(ref.group(1)) if ref else next_col
                next_col = col + 1
                value = self._cell_value(c)
                if value is not None and value.strip():
                    cells[(row_number, col)] = value
                    if len(cells) > limit:
                        raise UnsafeDocumentError(
                            f"Worksheet {part!r} has more than {limit} non-empty cells (TRYWORKS_MAX_SHEET_CELLS)."
                        )
        return cells

    def _cell_value(self, c) -> Optional[str]:
        kind = c.get("t") or "n"
        if kind == "inlineStr":
            is_node = c.find(qn("s:is"))
            return None if is_node is None else _text_of(is_node)
        v = c.find(qn("s:v"))
        raw = v.text if v is not None and v.text is not None else None
        if raw is None:
            return None
        if kind == "s":
            try:
                return self.shared_strings[int(raw)]
            except (ValueError, IndexError):
                return None
        if kind == "b":
            return "True" if raw.strip() in ("1", "true") else "False"
        if kind in ("str", "e"):
            return raw
        return self._format_number(raw, int(c.get("s") or 0))


def _components(cells: dict[tuple[int, int], str]) -> list[tuple[int, int, int, int]]:
    """Bounding boxes (min_row, max_row, min_col, max_col) of connected, row-overlapping groups."""
    seen: set[tuple[int, int]] = set()
    boxes = []
    for start in sorted(cells):
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        min_r = max_r = start[0]
        min_c = max_c = start[1]
        while stack:
            r, c = stack.pop()
            min_r, max_r, min_c, max_c = min(min_r, r), max(max_r, r), min(min_c, c), max(max_c, c)
            for nb in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                if nb in cells and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        boxes.append((min_r, max_r, min_c, max_c))
    boxes.sort()
    merged: list[list[int]] = []
    for box in boxes:
        if merged and box[0] <= merged[-1][1]:
            last = merged[-1]
            last[1], last[2], last[3] = max(last[1], box[1]), min(last[2], box[2]), max(last[3], box[3])
        else:
            merged.append(list(box))
    return [tuple(b) for b in merged]  # type: ignore[misc]


def _single_value_element(text: str) -> Element:
    if is_bulleted_text(text):
        return ListItem(text=clean_bullets(text))
    if is_possible_numbered_list(text):
        return ListItem(text=text)
    if is_possible_narrative_text(text):
        return NarrativeText(text=text)
    if is_possible_title(text):
        return Title(text=text)
    return Text(text=text)


def _subtable_elements(
    cells: dict[tuple[int, int], str], box: tuple[int, int, int, int], infer_table_structure: bool
) -> Iterator[Element]:
    min_r, max_r, min_c, max_c = box
    matrix = [[cells.get((r, c), "") for c in range(min_c, max_c + 1)] for r in range(min_r, max_r + 1)]
    matrix = [row for row in matrix if any(v.strip() for v in row)]
    single = [sum(1 for v in row if v.strip()) == 1 for row in matrix]
    first = next((i for i, s in enumerate(single) if not s), len(matrix))
    last = next((i for i in range(len(matrix) - 1, -1, -1) if not single[i]), -1)
    for row in matrix[:first]:
        yield _single_value_element(next(v for v in row if v.strip()).strip())
    if first <= last:
        core = matrix[first : last + 1]
        yield Table(
            text=table_text(core),
            metadata=ElementMetadata(text_as_html=htmlify_matrix_of_cell_texts(core) if infer_table_structure else None),
        )
        for row in matrix[last + 1 :]:
            yield _single_value_element(next(v for v in row if v.strip()).strip())


@apply_metadata(FileType.XLSX)
def partition_xlsx(
    filename: Optional[str] = None,
    *,
    file: Optional[IO[Any]] = None,
    find_subtable: bool = True,
    include_header: bool = False,
    infer_table_structure: bool = True,
    starting_page_number: int = 1,
    **kwargs: Any,
) -> list[Element]:
    """Partition an Excel workbook into Table (and caption text) elements, sheet by sheet."""
    exactly_one(filename=filename, file=file)
    workbook = _Workbook(OoxmlPackage(read_bytes(filename=filename, file=file)))
    elements: list[Element] = []
    for page_number, (sheet_name, part) in enumerate(workbook.sheets, start=starting_page_number):
        cells = workbook.read_sheet(part)
        if not cells:
            continue
        if find_subtable:
            sheet_elements = [
                e for box in _components(cells) for e in _subtable_elements(cells, box, infer_table_structure)
            ]
        else:
            rows = sorted({r for r, _ in cells})
            cols = sorted({c for _, c in cells})
            matrix = [[cells.get((r, c), "") for c in range(cols[0], cols[-1] + 1)] for r in rows]
            sheet_elements = [
                Table(
                    text=table_text(matrix),
                    metadata=ElementMetadata(
                        text_as_html=htmlify_matrix_of_cell_texts(matrix) if infer_table_structure else None
                    ),
                )
            ]
        for element in sheet_elements:
            element.metadata.page_name = sheet_name
            element.metadata.page_number = page_number
            elements.append(element)
    return elements

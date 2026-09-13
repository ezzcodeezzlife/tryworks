"""Partition PDFs that contain embedded text, using PDFium (``pip install tryworks[pdf]``).

This is the equivalent of unstructured's "fast" strategy: text is read from the PDF itself,
grouped into lines and blocks by position, and ordered with a recursive XY-cut so multi-column
pages read column by column. Blocks in the top or bottom 7% of the page become Header/Footer.

Scanned pages have no embedded text and need OCR, which tryworks does not do. Asking for the
"hi_res" or "ocr_only" strategies raises an error rather than silently returning less.
"""

from __future__ import annotations

import os
import warnings
from typing import IO, Any, Iterator, Optional

from tryworks.documents.coordinates import PixelSpace
from tryworks.documents.elements import Element, Footer, Header, PageBreak
from tryworks.partition.common import FileType, apply_metadata, exactly_one, read_bytes
from tryworks.partition.text import element_from_text
from tryworks.partition.text_type import is_bulleted_text

HEADER_FOOTER_THRESHOLD = 0.07
# -- on one baseline, a gap wider than this many font-heights separates columns --
_COLUMN_GAP = 1.5


class _Box:
    __slots__ = ("left", "bottom", "right", "top", "text")

    def __init__(self, left: float, bottom: float, right: float, top: float, text: str = ""):
        self.left, self.bottom, self.right, self.top, self.text = left, bottom, right, top, text

    @property
    def height(self) -> float:
        return max(self.top - self.bottom, 0.1)


def _bbox(boxes: list[_Box]) -> _Box:
    return _Box(
        min(b.left for b in boxes), min(b.bottom for b in boxes), max(b.right for b in boxes), max(b.top for b in boxes)
    )


def _max_pages() -> int:
    try:
        return int(os.environ.get("TRYWORKS_MAX_PDF_PAGES", 10000))
    except ValueError:
        return 10000


def _group_lines(segments: list[_Box]) -> list[_Box]:
    """Join text segments sharing a baseline into lines, keeping separate columns apart."""
    rows: list[list[_Box]] = []
    for seg in sorted(segments, key=lambda s: (-s.top, s.left)):
        for row in rows:
            ref = row[0]
            if min(ref.top, seg.top) - max(ref.bottom, seg.bottom) >= 0.5 * min(ref.height, seg.height):
                row.append(seg)
                break
        else:
            rows.append([seg])

    lines: list[_Box] = []
    for row in rows:
        row.sort(key=lambda s: s.left)
        current = [row[0]]
        for seg in row[1:]:
            prev = current[-1]
            if seg.left - prev.right > _COLUMN_GAP * max(prev.height, seg.height):
                lines.append(_join(current))
                current = [seg]
            else:
                current.append(seg)
        lines.append(_join(current))
    return lines


def _join(segments: list[_Box]) -> _Box:
    text = segments[0].text
    for prev, seg in zip(segments, segments[1:]):
        gap = seg.left - prev.right
        if gap > 0.1 * min(prev.height, seg.height) and not text.endswith(" ") and not seg.text.startswith(" "):
            text += " "
        text += seg.text
    box = _bbox(segments)
    box.text = text
    return box


def _group_blocks(lines: list[_Box], line_margin: float) -> list[list[_Box]]:
    """Stack lines into blocks: a line joins the block directly above it when the vertical gap is
    small and the two overlap horizontally. A bulleted line always starts a new block."""
    blocks: list[list[_Box]] = []
    for line in sorted(lines, key=lambda b: (-b.top, b.left)):
        best: Optional[list[_Box]] = None
        best_gap = float("inf")
        if not is_bulleted_text(line.text):
            for block in blocks:
                last = block[-1]
                gap = last.bottom - line.top
                overlaps = min(last.right, line.right) - max(last.left, line.left) > 0
                limit = line_margin * max(last.height, line.height)
                if overlaps and -0.5 * line.height <= gap <= limit and gap < best_gap:
                    best, best_gap = block, gap
        if best is None:
            blocks.append([line])
        else:
            best.append(line)
    return blocks


def _split_intervals(items: list[tuple[_Box, Any]], axis: str) -> Optional[list[list[tuple[_Box, Any]]]]:
    """Group items whose extents overlap on one axis; None when everything is one group."""
    if axis == "y":
        extent = lambda b: (b.bottom, b.top)  # noqa: E731
    else:
        extent = lambda b: (b.left, b.right)  # noqa: E731
    groups: list[list[tuple[_Box, Any]]] = []
    high = 0.0
    for item in sorted(items, key=lambda it: extent(it[0])[0]):
        lo, hi = extent(item[0])
        if groups and lo < high:
            groups[-1].append(item)
            high = max(high, hi)
        else:
            groups.append([item])
            high = hi
    if len(groups) <= 1:
        return None
    # -- rows read top to bottom, which is descending y in PDF space --
    return list(reversed(groups)) if axis == "y" else groups


def _xy_cut(blocks: list[list[_Box]]) -> list[list[_Box]]:
    """Reading order: split into rows on horizontal gaps, rows into columns on vertical gaps."""
    if len(blocks) <= 1:
        return blocks
    items = [(_bbox(b), b) for b in blocks]
    for axis in ("y", "x"):
        groups = _split_intervals(items, axis)
        if groups is not None:
            return [block for group in groups for block in _xy_cut([b for _, b in group])]
    return [b for _, b in sorted(items, key=lambda it: (-it[0].top, it[0].left))]


def _page_elements(page: Any, page_number: int, line_margin: float) -> list[Element]:
    width, height = page.get_size()
    textpage = page.get_textpage()
    try:
        segments = []
        for index in range(textpage.count_rects()):
            left, bottom, right, top = textpage.get_rect(index)
            text = textpage.get_text_bounded(left, bottom, right, top).replace("\r", "").replace("\n", " ")
            if text.strip():
                segments.append(_Box(left, bottom, right, top, text))
    finally:
        textpage.close()

    system = PixelSpace(width=width, height=height)
    elements: list[Element] = []
    for block in _xy_cut(_group_blocks(_group_lines(segments), line_margin)):
        text = " ".join(" ".join(line.text for line in block).split())
        if not text:
            continue
        box = _bbox(block)
        # -- PDF y grows upward from the bottom edge; PixelSpace y grows downward from the top --
        y_top, y_bottom = height - box.top, height - box.bottom
        points = ((box.left, y_top), (box.left, y_bottom), (box.right, y_bottom), (box.right, y_top))
        center = (y_top + y_bottom) / 2 / height if height else 0.5
        if center < HEADER_FOOTER_THRESHOLD:
            element: Element = Header(text=text, coordinates=points, coordinate_system=system)
        elif center > 1 - HEADER_FOOTER_THRESHOLD:
            element = Footer(text=text, coordinates=points, coordinate_system=system)
        else:
            element = element_from_text(text, coordinates=points, coordinate_system=system)
        element.metadata.page_number = page_number
        element.metadata.detection_origin = "pdfium"
        elements.append(element)
    return elements


def _iter_pdf_elements(
    data: bytes, password: Optional[str], include_page_breaks: bool, starting_page_number: int, line_margin: float
) -> Iterator[Element]:
    try:
        import pypdfium2 as pdfium
    except ImportError as e:
        raise ImportError("PDF support needs PDFium bindings: pip install 'tryworks[pdf]'") from e

    try:
        document = pdfium.PdfDocument(data, password=password)
    except pdfium.PdfiumError as e:
        raise ValueError(f"Could not open PDF ({e}). If it is encrypted, pass password=.") from e
    try:
        page_count = len(document)
        if page_count > _max_pages():
            raise ValueError(f"PDF has {page_count} pages, over the limit of {_max_pages()} (TRYWORKS_MAX_PDF_PAGES).")
        empty_pages = 0
        for index in range(page_count):
            if index > 0 and include_page_breaks:
                yield PageBreak("")
            page = document[index]
            try:
                page_elements = _page_elements(page, starting_page_number + index, line_margin)
            finally:
                page.close()
            empty_pages += not page_elements
            yield from page_elements
        if page_count and empty_pages == page_count:
            warnings.warn(
                "No embedded text found in this PDF; it is probably scanned. tryworks does not OCR "
                "images, so no elements were extracted.",
                stacklevel=4,
            )
    finally:
        document.close()


@apply_metadata(FileType.PDF)
def partition_pdf(
    filename: Optional[str] = None,
    file: Optional[IO[Any]] = None,
    include_page_breaks: bool = False,
    strategy: str = "auto",
    infer_table_structure: bool = False,
    ocr_languages: Optional[str] = None,
    languages: Optional[list[str]] = None,
    detect_language_per_element: bool = False,
    metadata_last_modified: Optional[str] = None,
    chunking_strategy: Optional[str] = None,
    hi_res_model_name: Optional[str] = None,
    extract_images_in_pdf: bool = False,
    extract_image_block_types: Optional[list[str]] = None,
    extract_image_block_output_dir: Optional[str] = None,
    extract_image_block_to_payload: bool = False,
    starting_page_number: int = 1,
    extract_forms: bool = False,
    form_extraction_skip_tables: bool = True,
    password: Optional[str] = None,
    pdfminer_line_margin: Optional[float] = None,
    pdfminer_char_margin: Optional[float] = None,
    pdfminer_line_overlap: Optional[float] = None,
    pdfminer_word_margin: Optional[float] = 0.185,
    **kwargs: Any,
) -> list[Element]:
    """Partition a PDF with embedded text into elements (the "fast" strategy)."""
    exactly_one(filename=filename, file=file)
    if strategy in ("hi_res", "ocr_only"):
        raise NotImplementedError(
            f"strategy={strategy!r} needs layout models and OCR, which tryworks does not include. "
            "Use strategy='fast' for PDFs with embedded text, or unstructured's hi_res strategy "
            "(or an OCR service) for scanned documents."
        )
    if strategy not in ("auto", "fast"):
        raise ValueError(f"Unknown strategy {strategy!r}; tryworks supports 'auto' and 'fast'.")
    data = read_bytes(filename=filename, file=file)
    line_margin = pdfminer_line_margin if pdfminer_line_margin is not None else 0.5
    return list(_iter_pdf_elements(data, password, include_page_breaks, starting_page_number, line_margin))

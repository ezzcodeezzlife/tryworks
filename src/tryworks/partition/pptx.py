"""Partition PowerPoint .pptx presentations with the standard library.

Each slide is one page. Shapes are read top-to-bottom, then left-to-right. The slide title
becomes Title; explicitly bulleted paragraphs become ListItem (``category_depth`` is the
indent level); other text is classified as NarrativeText, Title or Text; tables become Table.
Speaker notes are included as NarrativeText when ``include_slide_notes=True``.
"""

from __future__ import annotations

from typing import IO, Any, Iterator, Optional
from xml.etree.ElementTree import Element as XmlElement

from tryworks.documents.elements import (
    Element,
    ElementMetadata,
    EmailAddress,
    ListItem,
    NarrativeText,
    PageBreak,
    Table,
    Text,
    Title,
)
from tryworks.partition._html_table import htmlify_matrix_of_cell_texts, table_text
from tryworks.partition._ooxml import REL_NOTES, OoxmlPackage, qn
from tryworks.partition.common import FileType, apply_metadata, exactly_one, read_bytes
from tryworks.partition.text_type import is_email_address, is_possible_narrative_text, is_possible_title

_TITLE_PLACEHOLDERS = frozenset(["title", "ctrTitle", "vertTitle"])
_BULLET_TAGS = (qn("a:buChar"), qn("a:buAutoNum"), qn("a:buBlip"))


def _paragraph_text(p: XmlElement) -> str:
    parts = []
    for child in p:
        if child.tag in (qn("a:r"), qn("a:fld")):
            t = child.find(qn("a:t"))
            parts.append(t.text or "" if t is not None else "")
        elif child.tag == qn("a:br"):
            parts.append("\v")
    return "".join(parts)


def _is_bulleted(p: XmlElement) -> bool:
    ppr = p.find(qn("a:pPr"))
    return ppr is not None and any(ppr.find(tag) is not None for tag in _BULLET_TAGS)


def _level(p: XmlElement) -> int:
    ppr = p.find(qn("a:pPr"))
    try:
        return int(ppr.get("lvl") or 0) if ppr is not None else 0
    except ValueError:
        return 0


def _placeholder_type(shape: XmlElement) -> Optional[str]:
    for nv in shape:
        ph = nv.find(f"{qn('p:nvPr')}/{qn('p:ph')}")
        if ph is not None:
            return ph.get("type") or "body"
    return None


def _offset(shape: XmlElement) -> tuple[int, int, int, int]:
    """(top, left, height, width) of a shape in EMU, zeros when not positioned."""
    for xfrm in (shape.find(f"{qn('p:spPr')}/{qn('a:xfrm')}"), shape.find(qn("p:xfrm")), shape.find(f"{qn('p:grpSpPr')}/{qn('a:xfrm')}")):
        if xfrm is None:
            continue
        off, ext = xfrm.find(qn("a:off")), xfrm.find(qn("a:ext"))
        try:
            y = int(off.get("y") or 0) if off is not None else 0
            x = int(off.get("x") or 0) if off is not None else 0
            cy = int(ext.get("cy") or 0) if ext is not None else 0
            cx = int(ext.get("cx") or 0) if ext is not None else 0
        except ValueError:
            return 0, 0, 0, 0
        return y, x, cy, cx
    return 0, 0, 0, 0


class _PptxPartitioner:
    def __init__(self, pkg: OoxmlPackage, include_page_breaks: bool, include_slide_notes: bool, infer_table_structure: bool, starting_page_number: int):
        self.pkg = pkg
        self.include_page_breaks = include_page_breaks
        self.include_slide_notes = include_slide_notes
        self.infer_table_structure = infer_table_structure
        self.page_number = starting_page_number
        self.part = pkg.main_part() or "ppt/presentation.xml"
        root = pkg.xml(self.part)
        if root is None:
            raise ValueError("Presentation part is missing; not a valid .pptx file.")
        size = root.find(qn("p:sldSz"))
        self.slide_width = int(size.get("cx") or 0) if size is not None else 0
        self.slide_height = int(size.get("cy") or 0) if size is not None else 0
        rels = pkg.rels(self.part)
        self.slide_parts = []
        for sld_id in root.iter(qn("p:sldId")):
            rel = rels.get(sld_id.get(qn("r:id")) or "")
            if rel and not rel[2]:
                self.slide_parts.append(rel[0])

    def _metadata(self, category_depth: Optional[int] = None) -> ElementMetadata:
        return ElementMetadata(category_depth=category_depth, page_number=self.page_number)

    def iter_elements(self) -> Iterator[Element]:
        for index, part in enumerate(self.slide_parts):
            if index > 0:
                self.page_number += 1
                if self.include_page_breaks:
                    yield PageBreak("", detection_origin="pptx")
            slide = self.pkg.xml(part)
            if slide is None:
                continue
            tree = slide.find(f"{qn('p:cSld')}/{qn('p:spTree')}")
            if tree is not None:
                shapes = sorted(self._iter_shapes(tree), key=lambda s: _offset(s)[:2])
                title_shape = next((s for s in shapes if _placeholder_type(s) in _TITLE_PLACEHOLDERS), None)
                for shape in shapes:
                    yield from self._shape_elements(shape, shape is title_shape)
            if self.include_slide_notes:
                yield from self._notes_elements(part)

    def _iter_shapes(self, container: XmlElement) -> Iterator[XmlElement]:
        for shape in container:
            if shape.tag == qn("p:grpSp"):
                yield from self._iter_shapes(shape)
            elif shape.tag in (qn("p:sp"), qn("p:graphicFrame")):
                yield shape

    def _off_slide(self, shape: XmlElement) -> bool:
        top, left, height, width = _offset(shape)
        if not self.slide_width or not self.slide_height:
            return False
        return top + height < 0 or left + width < 0 or top > self.slide_height or left > self.slide_width

    def _shape_elements(self, shape: XmlElement, is_title: bool) -> Iterator[Element]:
        if shape.tag == qn("p:graphicFrame"):
            yield from self._table_elements(shape)
            return
        body = shape.find(qn("p:txBody"))
        if body is None or self._off_slide(shape):
            return
        for p in body.findall(qn("a:p")):
            text = _paragraph_text(p)
            if not text.strip():
                continue
            level = _level(p)
            if _is_bulleted(p):
                yield ListItem(text=text, metadata=self._metadata(level), detection_origin="pptx")
            elif is_email_address(text):
                yield EmailAddress(text=text, metadata=self._metadata(), detection_origin="pptx")
            elif is_title:
                yield Title(text=text, metadata=self._metadata(0), detection_origin="pptx")
            elif is_possible_narrative_text(text):
                yield NarrativeText(text=text, metadata=self._metadata(level), detection_origin="pptx")
            elif is_possible_title(text):
                yield Title(text=text, metadata=self._metadata(level + 1), detection_origin="pptx")
            else:
                yield Text(text=text, metadata=self._metadata(level), detection_origin="pptx")

    def _table_elements(self, frame: XmlElement) -> Iterator[Element]:
        tbl = frame.find(f"{qn('a:graphic')}/{qn('a:graphicData')}/{qn('a:tbl')}")
        if tbl is None:
            return
        matrix = [
            ["\n".join(_paragraph_text(p) for p in tc.iter(qn("a:p"))) for tc in tr.findall(qn("a:tc"))]
            for tr in tbl.findall(qn("a:tr"))
        ]
        text = table_text(matrix)
        if not matrix or not text:
            return
        metadata = self._metadata()
        metadata.text_as_html = htmlify_matrix_of_cell_texts(matrix) if self.infer_table_structure else None
        yield Table(text=text, metadata=metadata, detection_origin="pptx")

    def _notes_elements(self, slide_part: str) -> Iterator[Element]:
        notes_part = next((t for t, typ, ext in self.pkg.rels(slide_part).values() if typ == REL_NOTES and not ext), None)
        notes = self.pkg.xml(notes_part) if notes_part else None
        if notes is None:
            return
        for shape in notes.iter(qn("p:sp")):
            if _placeholder_type(shape) != "body":
                continue
            body = shape.find(qn("p:txBody"))
            text = "\n".join(_paragraph_text(p) for p in body.findall(qn("a:p"))).strip() if body is not None else ""
            if text:
                yield NarrativeText(text=text, metadata=self._metadata(), detection_origin="pptx")
            return


@apply_metadata(FileType.PPTX)
def partition_pptx(
    filename: Optional[str] = None,
    *,
    file: Optional[IO[Any]] = None,
    include_page_breaks: bool = True,
    include_slide_notes: Optional[bool] = None,
    infer_table_structure: bool = True,
    starting_page_number: int = 1,
    strategy: str = "fast",
    **kwargs: Any,
) -> list[Element]:
    """Partition a PowerPoint .pptx presentation into elements, one page per slide."""
    exactly_one(filename=filename, file=file)
    partitioner = _PptxPartitioner(
        OoxmlPackage(read_bytes(filename=filename, file=file)),
        include_page_breaks=include_page_breaks,
        include_slide_notes=bool(include_slide_notes),
        infer_table_structure=infer_table_structure,
        starting_page_number=starting_page_number,
    )
    return list(partitioner.iter_elements())

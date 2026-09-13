"""Partition Word .docx documents with the standard library.

Element rules follow unstructured's DOCX partitioner:

- Paragraph styles map to elements ("Heading 1"-"Heading 9", "Title", "Subtitle" -> Title;
  "List ..." styles and numbered/bulleted paragraphs -> ListItem); other paragraphs are
  classified by their text.
- Tables become Table elements whose ``text_as_html`` keeps merged cells as colspan/rowspan.
- Section headers and footers become Header/Footer elements with ``header_footer_type``.
- Page numbers come from Word's rendered page breaks and are only set when the file has them.
"""

from __future__ import annotations

import itertools
from typing import IO, Any, Iterator, Optional
from xml.etree.ElementTree import Element as XmlElement

from tryworks.cleaners.core import clean_bullets
from tryworks.documents.elements import (
    Address,
    Element,
    ElementMetadata,
    EmailAddress,
    Footer,
    Header,
    ListItem,
    NarrativeText,
    PageBreak,
    Table,
    Text,
    Title,
)
from tryworks.partition._html_table import htmlify_matrix_of_spanned_cells
from tryworks.partition._ooxml import (
    REL_HYPERLINK,
    REL_SETTINGS,
    REL_STYLES,
    OoxmlPackage,
    qn,
)
from tryworks.partition.common import FileType, apply_metadata, exactly_one, read_bytes
from tryworks.partition.text_type import (
    is_bulleted_text,
    is_email_address,
    is_possible_narrative_text,
    is_us_city_state_zip,
)

STYLE_TO_ELEMENT_MAPPING: dict[str, type[Text]] = {
    "Caption": Text,
    **{f"Heading {n}": Title for n in range(1, 10)},
    "List": ListItem,
    "List 2": ListItem,
    "List 3": ListItem,
    "List Bullet": ListItem,
    "List Bullet 2": ListItem,
    "List Bullet 3": ListItem,
    "List Continue": ListItem,
    "List Continue 2": ListItem,
    "List Continue 3": ListItem,
    "List Number": ListItem,
    "List Number 2": ListItem,
    "List Number 3": ListItem,
    "List Paragraph": ListItem,
    "Subtitle": Title,
    "Title": Title,
}

W_P, W_TBL, W_R, W_HYPERLINK = qn("w:p"), qn("w:tbl"), qn("w:r"), qn("w:hyperlink")
W_PPR, W_RPR, W_SECTPR = qn("w:pPr"), qn("w:rPr"), qn("w:sectPr")
W_LRPB = qn("w:lastRenderedPageBreak")
W_VAL, R_ID = qn("w:val"), qn("r:id")

# -- Word stores built-in style names in lowercase; show them the way Word's UI does --
_BUILTIN_STYLE_NAMES = {"caption", "footer", "header", "normal", "subtitle", "title"} | {
    f"heading {n}" for n in range(1, 10)
}


def _on(prop: Optional[XmlElement]) -> bool:
    """A toggle property like ``<w:b/>`` or ``<w:b w:val="true"/>`` that is present and not off."""
    if prop is None:
        return False
    return (prop.get(W_VAL) or "true").lower() not in ("0", "false", "off", "none")


class _Run:
    """A run's text with its direct formatting, and the hyperlink (if any) that contains it."""

    __slots__ = ("text", "bold", "italic", "link")

    def __init__(self, text: str, bold: bool, italic: bool, link: Optional[str]):
        self.text, self.bold, self.italic, self.link = text, bold, italic, link


_PAGE_BREAK = object()


class _Document:
    def __init__(self, package: OoxmlPackage):
        self.pkg = package
        self.part = package.main_part() or "word/document.xml"
        root = package.xml(self.part)
        if root is None:
            raise ValueError("Document part is missing; not a valid .docx file.")
        self.root = root
        self.body = root.find(qn("w:body"))
        if self.body is None:
            raise ValueError("Document has no body.")
        self.rels = package.rels(self.part)
        self.style_names = self._load_style_names()
        self.even_and_odd_headers = self._load_even_and_odd_setting()

    def _related_part(self, rel_type: str) -> Optional[str]:
        return next((t for t, typ, ext in self.rels.values() if typ == rel_type and not ext), None)

    def _load_style_names(self) -> dict[str, str]:
        part = self._related_part(REL_STYLES)
        root = self.pkg.xml(part) if part else None
        names: dict[str, str] = {}
        if root is None:
            return names
        for style in root.findall(qn("w:style")):
            name_el = style.find(qn("w:name"))
            name = name_el.get(W_VAL) if name_el is not None else None
            if not name:
                continue
            if name.lower() in _BUILTIN_STYLE_NAMES:
                name = name.title()
            names[style.get(qn("w:styleId")) or ""] = name
        return names

    def _load_even_and_odd_setting(self) -> bool:
        part = self._related_part(REL_SETTINGS)
        root = self.pkg.xml(part) if part else None
        return root is not None and _on(root.find(qn("w:evenAndOddHeaders")))

    def style_name(self, p: XmlElement) -> str:
        ppr = p.find(W_PPR)
        style = ppr.find(qn("w:pStyle")) if ppr is not None else None
        style_id = style.get(W_VAL) if style is not None else None
        return self.style_names.get(style_id or "", "Normal") if style_id else "Normal"

    def hyperlink_url(self, hyperlink: XmlElement) -> Optional[str]:
        rel = self.rels.get(hyperlink.get(R_ID) or "")
        if rel and rel[1] == REL_HYPERLINK:
            return rel[0]
        return None


def _run_text(run: XmlElement) -> str:
    parts = []
    for child in run:
        tag = child.tag
        if tag == qn("w:t"):
            parts.append(child.text or "")
        elif tag in (qn("w:tab"), qn("w:ptab")):
            parts.append("\t")
        elif tag == qn("w:br"):
            if (child.get(qn("w:type")) or "textWrapping") == "textWrapping":
                parts.append("\n")
        elif tag == qn("w:cr"):
            parts.append("\n")
        elif tag == qn("w:noBreakHyphen"):
            parts.append("-")
    return "".join(parts)


def _iter_run_items(doc: _Document, p: XmlElement) -> Iterator[Any]:
    """Runs of a paragraph in order, with rendered page breaks yielded as ``_PAGE_BREAK``."""
    for child in p:
        if child.tag == W_R:
            yield from _split_run(child, None)
        elif child.tag == W_HYPERLINK:
            url = doc.hyperlink_url(child)
            has_break = False
            for run in child.findall(W_R):
                for item in _split_run(run, url):
                    if item is _PAGE_BREAK:
                        has_break = True
                    else:
                        yield item
            # -- a page break inside a hyperlink applies after the whole hyperlink --
            if has_break:
                yield _PAGE_BREAK


def _split_run(run: XmlElement, link: Optional[str]) -> Iterator[Any]:
    rpr = run.find(W_RPR)
    bold = _on(rpr.find(qn("w:b"))) if rpr is not None else False
    italic = _on(rpr.find(qn("w:i"))) if rpr is not None else False
    pending: list[XmlElement] = []
    for child in run:
        if child.tag == W_LRPB:
            text = _run_text(_fake_run(pending))
            if text:
                yield _Run(text, bold, italic, link)
            pending = []
            yield _PAGE_BREAK
        elif child.tag != W_RPR:
            pending.append(child)
    text = _run_text(_fake_run(pending))
    if text:
        yield _Run(text, bold, italic, link)


def _fake_run(children: list[XmlElement]) -> XmlElement:
    run = XmlElement(W_R)
    run.extend(children)
    return run


def _paragraph_text(doc: _Document, p: XmlElement) -> str:
    return "".join(item.text for item in _iter_run_items(doc, p) if item is not _PAGE_BREAK)


class _DocxPartitioner:
    def __init__(self, doc: _Document, include_page_breaks: bool, infer_table_structure: bool, starting_page_number: int):
        self.doc = doc
        self.include_page_breaks = include_page_breaks
        self.infer_table_structure = infer_table_structure
        self.page_number = starting_page_number
        self.has_page_breaks = any(True for _ in doc.body.iter(W_LRPB))

    @property
    def metadata_page_number(self) -> Optional[int]:
        return self.page_number if self.has_page_breaks else None

    def _increment_page(self) -> Iterator[Element]:
        self.page_number += 1
        if self.include_page_breaks:
            yield PageBreak("", detection_origin="docx")

    # -- document structure --

    def iter_elements(self) -> Iterator[Element]:
        body = self.doc.body
        sections = [p.find(W_PPR).find(W_SECTPR) for p in body.findall(W_P) if p.find(W_PPR) is not None and p.find(W_PPR).find(W_SECTPR) is not None]
        final = body.find(W_SECTPR)
        if final is not None:
            sections.append(final)
        if not sections:
            for block in body:
                yield from self._block_elements(block)
            return

        section_iter = iter(enumerate(sections))
        index, section = next(section_iter)
        yield from self._section_start(index, section)
        for block in body:
            yield from self._block_elements(block)
            ppr = block.find(W_PPR) if block.tag == W_P else None
            if ppr is not None and ppr.find(W_SECTPR) is section:
                yield from self._section_footers(section)
                nxt = next(section_iter, None)
                if nxt is None:
                    return
                index, section = nxt
                yield from self._section_start(index, section)
        yield from self._section_footers(section)

    def _block_elements(self, block: XmlElement) -> Iterator[Element]:
        if block.tag == W_P:
            yield from self._paragraph_elements(block)
        elif block.tag == W_TBL:
            yield from self._table_elements(block)

    def _section_start(self, index: int, section: XmlElement) -> Iterator[Element]:
        start = section.find(qn("w:type"))
        start_type = start.get(W_VAL) if start is not None else "nextPage"
        if start_type == "evenPage" and self.page_number % 2 == 1:
            yield from self._increment_page()
        elif start_type == "oddPage" and index > 0 and self.page_number % 2 == 0:
            yield from self._increment_page()
        yield from self._header_footer_elements(section, "w:headerReference", Header)

    def _section_footers(self, section: XmlElement) -> Iterator[Element]:
        yield from self._header_footer_elements(section, "w:footerReference", Footer)

    def _header_footer_elements(self, section: XmlElement, ref_tag: str, cls: type[Text]) -> Iterator[Element]:
        refs = {ref.get(qn("w:type")) or "default": ref.get(R_ID) for ref in section.findall(qn(ref_tag))}
        wanted = [("default", "primary")]
        if _on(section.find(qn("w:titlePg"))):
            wanted.append(("first", "first_page"))
        if self.doc.even_and_odd_headers:
            wanted.append(("even", "even_page"))
        for word_type, header_footer_type in wanted:
            rel = self.doc.rels.get(refs.get(word_type) or "")
            if not rel or rel[2]:
                continue
            root = self.doc.pkg.xml(rel[0])
            if root is None:
                continue
            texts = []
            for block in root:
                if block.tag == W_P:
                    texts.append(_paragraph_text(self.doc, block).strip())
                elif block.tag == W_TBL:
                    texts.append(" ".join(self._table_texts(block)))
            text = "\n".join(t for t in texts if t)
            if text:
                yield cls(
                    text=text,
                    detection_origin="docx",
                    metadata=ElementMetadata(header_footer_type=header_footer_type, category_depth=0),
                )

    # -- paragraphs --

    def _paragraph_elements(self, p: XmlElement) -> Iterator[Element]:
        fragment: list[_Run] = []
        for item in _iter_run_items(self.doc, p):
            if item is _PAGE_BREAK:
                yield from self._classify(p, fragment)
                fragment = []
                yield from self._increment_page()
            else:
                fragment.append(item)
        yield from self._classify(p, fragment)

    def _is_list_item(self, p: XmlElement, text: str) -> bool:
        if is_bulleted_text(text):
            return True
        ppr = p.find(W_PPR)
        return ppr is not None and ppr.find(qn("w:numPr")) is not None

    def _category_depth(self, p: XmlElement, style_name: str) -> int:
        ppr = p.find(W_PPR)
        ilvl = ppr.find(qn("w:numPr")).find(qn("w:ilvl")) if ppr is not None and ppr.find(qn("w:numPr")) is not None else None
        if ilvl is not None and ilvl.get(W_VAL) is not None:
            try:
                return round(float(ilvl.get(W_VAL) or 0))
            except ValueError:
                pass
        parts = style_name.split()
        last_number = int(parts[-1]) - 1 if parts and parts[-1].isdigit() else 0
        if style_name.startswith("Heading"):
            return max(last_number, 0)
        if style_name == "Subtitle":
            return 1
        if style_name.startswith("List"):
            return max(last_number, 0)
        return 0

    def _classify(self, p: XmlElement, runs: list[_Run]) -> Iterator[Element]:
        text = "".join(r.text for r in runs)
        if not text.strip():
            return
        style_name = self.doc.style_name(p)
        metadata = self._paragraph_metadata(p, runs, style_name)
        if self._is_list_item(p, text):
            clean = clean_bullets(text).strip()
            if clean:
                yield ListItem(text=clean, metadata=metadata, detection_origin="docx")
            return
        cls = STYLE_TO_ELEMENT_MAPPING.get(style_name)
        if cls is None:
            stripped = text.strip()
            if len(stripped) < 2:
                cls = None
            elif is_us_city_state_zip(stripped):
                cls = Address
            elif is_email_address(stripped):
                cls = EmailAddress
            elif is_possible_narrative_text(stripped):
                cls = NarrativeText
        yield (cls or Text)(text=text, metadata=metadata, detection_origin="docx")

    def _paragraph_metadata(self, p: XmlElement, runs: list[_Run], style_name: str) -> ElementMetadata:
        contents, tags = [], []
        for run in runs:
            stripped = run.text.strip()
            if not stripped:
                continue
            if run.bold:
                contents.append(stripped)
                tags.append("b")
            if run.italic:
                contents.append(stripped)
                tags.append("i")
        links = []
        offset = 0
        for url, group in itertools.groupby(runs, key=lambda r: r.link):
            group_text = "".join(r.text for r in group)
            if url and group_text:
                links.append({"text": group_text, "url": url, "start_index": offset})
            offset += len(group_text)
        return ElementMetadata(
            category_depth=self._category_depth(p, style_name),
            emphasized_text_contents=contents or None,
            emphasized_text_tags=tags or None,
            link_texts=[link["text"] for link in links] or None,
            link_urls=[link["url"] for link in links] or None,
            links=links or None,
            page_number=self.metadata_page_number,
        )

    # -- tables --

    def _cell_paragraph_texts(self, tc: XmlElement) -> Iterator[tuple[str, Optional[XmlElement]]]:
        for block in tc:
            if block.tag == W_P:
                yield _paragraph_text(self.doc, block), None
            elif block.tag == W_TBL:
                yield "", block

    def _table_texts(self, tbl: XmlElement) -> Iterator[str]:
        for tr in tbl.findall(qn("w:tr")):
            for tc in tr.findall(qn("w:tc")):
                if self._vmerge(tc) == "continue":
                    continue
                for text, nested in self._cell_paragraph_texts(tc):
                    if nested is not None:
                        yield from self._table_texts(nested)
                    elif text.strip():
                        yield text.strip()

    @staticmethod
    def _tc_property(tc: XmlElement, name: str) -> Optional[XmlElement]:
        tcpr = tc.find(qn("w:tcPr"))
        return tcpr.find(qn(name)) if tcpr is not None else None

    def _vmerge(self, tc: XmlElement) -> Optional[str]:
        vmerge = self._tc_property(tc, "w:vMerge")
        if vmerge is None:
            return None
        return vmerge.get(W_VAL) or "continue"

    def _cell_text(self, tc: XmlElement) -> str:
        pieces = []
        for text, nested in self._cell_paragraph_texts(tc):
            pieces.extend(self._table_texts(nested) if nested is not None else [text])
        return " ".join(" ".join(pieces).split())

    def _table_html(self, tbl: XmlElement) -> str:
        grid: list[list[tuple[str, object]]] = []
        for tr in tbl.findall(qn("w:tr")):
            trpr = tr.find(qn("w:trPr"))
            before = trpr.find(qn("w:gridBefore")) if trpr is not None else None
            after = trpr.find(qn("w:gridAfter")) if trpr is not None else None
            row: list[tuple[str, object]] = [("", object()) for _ in range(int((before.get(W_VAL) if before is not None else 0) or 0))]
            for tc in tr.findall(qn("w:tc")):
                span_el = self._tc_property(tc, "w:gridSpan")
                span = int(span_el.get(W_VAL) or 1) if span_el is not None else 1
                col = len(row)
                if self._vmerge(tc) == "continue" and grid and col < len(grid[-1]):
                    cell = grid[-1][col]
                else:
                    cell = (self._cell_text(tc), object())
                row.extend([cell] * max(span, 1))
            row.extend(("", object()) for _ in range(int((after.get(W_VAL) if after is not None else 0) or 0)))
            grid.append(row)

        covered: set[tuple[int, int]] = set()
        matrix = []
        for r, row in enumerate(grid):
            cells = []
            for c, (text, key) in enumerate(row):
                if (r, c) in covered:
                    continue
                colspan = 1
                while c + colspan < len(row) and row[c + colspan][1] is key:
                    colspan += 1
                rowspan = 1
                while r + rowspan < len(grid) and c < len(grid[r + rowspan]) and grid[r + rowspan][c][1] is key:
                    rowspan += 1
                for dr in range(rowspan):
                    for dc in range(colspan):
                        covered.add((r + dr, c + dc))
                cells.append((text, colspan, rowspan))
            matrix.append(cells)
        return htmlify_matrix_of_spanned_cells(matrix)

    def _table_elements(self, tbl: XmlElement) -> Iterator[Element]:
        contents, tags = [], []
        for run in tbl.iter(W_R):
            text = _run_text(run).strip()
            rpr = run.find(W_RPR)
            if not text or rpr is None:
                continue
            if _on(rpr.find(qn("w:b"))):
                contents.append(text)
                tags.append("b")
            if _on(rpr.find(qn("w:i"))):
                contents.append(text)
                tags.append("i")
        yield Table(
            " ".join(self._table_texts(tbl)),
            detection_origin="docx",
            metadata=ElementMetadata(
                text_as_html=self._table_html(tbl) if self.infer_table_structure else None,
                page_number=self.metadata_page_number,
                emphasized_text_contents=contents or None,
                emphasized_text_tags=tags or None,
            ),
        )


@apply_metadata(FileType.DOCX)
def partition_docx(
    filename: Optional[str] = None,
    *,
    file: Optional[IO[Any]] = None,
    include_page_breaks: bool = True,
    infer_table_structure: bool = True,
    starting_page_number: int = 1,
    strategy: Optional[str] = None,
    **kwargs: Any,
) -> list[Element]:
    """Partition a Word .docx document into elements."""
    exactly_one(filename=filename, file=file)
    document = _Document(OoxmlPackage(read_bytes(filename=filename, file=file)))
    partitioner = _DocxPartitioner(document, include_page_breaks, infer_table_structure, starting_page_number)
    return list(partitioner.iter_elements())

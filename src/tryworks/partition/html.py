"""Partition HTML into elements using only the standard library's HTML tokenizer.

Element rules follow unstructured's default ("v1") HTML parser:

- ``h1``-``h6`` become Title (``category_depth`` 0-5), ``li`` becomes ListItem (depth is the
  number of enclosing lists), ``pre`` becomes CodeSnippet, ``table`` becomes Table with
  ``text_as_html``, ``img`` becomes Image.
- Other text is classified by content: ListItem (bulleted), Address, EmailAddress,
  NarrativeText or Text.
- Navigation, forms, ``figure``, ``details``, ``dl``, buttons and labels are dropped as
  boilerplate; unrecognized tags (``script``, ``style``, ``title`` ...) contribute no text.
- Bold/italic runs and links are recorded in ``emphasized_text_*`` and ``link_*`` metadata.
"""

from __future__ import annotations

import re
import ssl
import urllib.request
from collections import deque
from html.parser import HTMLParser
from typing import IO, Any, Callable, Iterator, Optional, Union

from tryworks.cleaners.core import clean_bullets
from tryworks.documents.elements import (
    Address,
    CodeSnippet,
    Element,
    ElementMetadata,
    EmailAddress,
    Image,
    ListItem,
    NarrativeText,
    Table,
    Text,
    Title,
)
from tryworks.partition._html_table import htmlify_matrix_of_cell_texts
from tryworks.partition.common import FileType, apply_metadata, decode_bytes, exactly_one, read_bytes
from tryworks.partition.text_type import (
    is_bulleted_text,
    is_email_address,
    is_possible_narrative_text,
    is_us_city_state_zip,
)

_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_FLOW = frozenset("address article aside blockquote body center div footer header hgroup main section".split())
_HEADINGS = {f"h{n}": n - 1 for n in range(1, 7)}
_LISTS = frozenset(["ol", "ul"])
_PHRASING = frozenset(
    """abbr bdi bdo big cite code data dfn kbd mark meter q s samp small span strike sub sup time
    tt u var wbr""".split()
)
_BOLD = frozenset(["b", "strong"])
_ITALIC = frozenset(["em", "i"])
_REMOVED_PHRASING = frozenset(["button", "label"])
_REMOVED_BLOCK = frozenset("details dl dd dt figure hr nav template form input summary".split())
_BLOCKS = _FLOW | set(_HEADINGS) | _LISTS | _REMOVED_BLOCK | {"p", "pre", "li", "img", "table"}

# -- opening one of these implicitly closes an open <p> --
_CLOSES_P = (_FLOW | set(_HEADINGS) | _LISTS) - {"body"} | {
    "details", "dl", "fieldset", "figcaption", "figure", "form", "hr", "nav", "p", "pre", "table",
}
_BASE64_IMAGE_RE = re.compile(r"^data:(image/[^;]+);base64,(.*)", re.DOTALL)
_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.IGNORECASE)


# -- tree building ---------------------------------------------------------------------------


class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str], parent: Optional[_Node]):
        self.tag = tag
        self.attrs = attrs
        self.children: list[Union[str, _Node]] = []
        self.parent = parent

    def itertext(self) -> Iterator[str]:
        for child in self.children:
            if isinstance(child, str):
                yield child
            else:
                yield from child.itertext()

    def ancestors(self) -> Iterator[_Node]:
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class _TreeBuilder(HTMLParser):
    """Build a forgiving element tree, applying the implied end tags browsers apply.

    Elements nested deeper than ``MAX_DEPTH`` are flattened into their deepest allowed ancestor,
    as libxml2 does, so hostile markup cannot exhaust the stack.
    """

    MAX_DEPTH = 200

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self._stack = [self.root]
        self._flattened: dict[str, int] = {}

    @property
    def _current(self) -> _Node:
        return self._stack[-1]

    def _open_index(self, tags: frozenset[str] | set[str], stop: frozenset[str] | set[str] = frozenset()) -> int:
        for index in range(len(self._stack) - 1, 0, -1):
            tag = self._stack[index].tag
            if tag in tags:
                return index
            if tag in stop:
                return -1
        return -1

    def _close_to(self, index: int) -> None:
        if index > 0:
            del self._stack[index:]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in _CLOSES_P:
            self._close_to(self._open_index({"p"}, stop={"li", "td", "th", "button", "table"} | _FLOW))
        if tag == "li":
            self._close_to(self._open_index({"li"}, stop=_LISTS))
        elif tag in ("dt", "dd"):
            self._close_to(self._open_index({"dt", "dd"}, stop={"dl"}))
        elif tag in ("td", "th"):
            self._close_to(self._open_index({"td", "th"}, stop={"tr", "table"}))
        elif tag == "tr":
            self._close_to(self._open_index({"tr"}, stop={"table"}))
        elif tag in ("thead", "tbody", "tfoot"):
            self._close_to(self._open_index({"thead", "tbody", "tfoot"}, stop={"table"}))
        elif tag == "option":
            self._close_to(self._open_index({"option"}, stop={"select"}))
        if tag not in _VOID and len(self._stack) > self.MAX_DEPTH:
            self._flattened[tag] = self._flattened.get(tag, 0) + 1
            return
        node = _Node(tag, {k: (v or "") for k, v in attrs}, self._current)
        self._current.children.append(node)
        if tag not in _VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self._current.children.append(_Node(tag, {k: (v or "") for k, v in attrs}, self._current))

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            return
        if self._flattened.get(tag):
            self._flattened[tag] -= 1
            return
        index = self._open_index({tag})
        if index > 0:
            self._close_to(index)

    def handle_data(self, data: str) -> None:
        children = self._current.children
        if children and isinstance(children[-1], str):
            children[-1] += data
        else:
            children.append(data)


def _find_body(root: _Node) -> _Node:
    """The ``<body>`` element; failing that the ``<html>`` element; failing that the fragment root."""
    node = root
    while True:
        children = [c for c in node.children if isinstance(c, _Node)]
        body = next((c for c in children if c.tag == "body"), None)
        if body is not None:
            return body
        html_node = next((c for c in children if c.tag == "html"), None)
        if html_node is None:
            return node
        node = html_node


# -- element generation ----------------------------------------------------------------------

Annotation = dict[str, list]


class _Segment:
    __slots__ = ("text", "annotation")

    def __init__(self, text: str, annotation: Optional[Annotation] = None):
        self.text = text
        self.annotation = annotation or {}


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _consolidate(annotations: Iterator[Annotation] | list[Annotation]) -> Annotation:
    combined: Annotation = {}
    for annotation in annotations:
        for key, value in annotation.items():
            combined.setdefault(key, []).extend(value)
    return combined


def _emphasis_annotation(text: str, emphasis: str) -> Annotation:
    normalized = _normalize(text)
    if normalized and emphasis:
        return {"emphasized_text_contents": [normalized], "emphasized_text_tags": [emphasis]}
    return {}


def _derive_element_type(text: str) -> Optional[type[Text]]:
    if is_bulleted_text(text):
        return ListItem
    if is_us_city_state_zip(text):
        return Address
    if is_email_address(text):
        return EmailAddress
    if len(text) < 2:
        return None
    if is_possible_narrative_text(text):
        return NarrativeText
    return Text


class _HtmlPartitioner:
    def __init__(self, html_text: str, skip_headers_and_footers: bool = False):
        builder = _TreeBuilder()
        builder.feed(html_text)
        builder.close()
        self._body = _find_body(builder.root)
        self._removed_blocks = _REMOVED_BLOCK | ({"header", "footer"} if skip_headers_and_footers else frozenset())

    def iter_elements(self) -> Iterator[Element]:
        yield from self._flow_elements(self._body)

    # -- classification helpers --

    def _is_phrasing(self, item: Union[str, _Node]) -> bool:
        return isinstance(item, str) or item.tag not in _BLOCKS and item.tag not in self._removed_blocks

    @staticmethod
    def _page_number(node: _Node) -> Optional[int]:
        for candidate in (node, *node.ancestors()):
            value = candidate.attrs.get("data-page-number")
            if value is not None:
                try:
                    return int(value)
                except ValueError:
                    return None
        return None

    # -- flow content --

    def _node_elements(self, node: _Node) -> Iterator[Element]:
        if node.tag in self._removed_blocks:
            return
        if node.tag == "img":
            yield from self._image_elements(node)
        elif node.tag == "table":
            yield from self._table_elements(node)
        else:
            yield from self._flow_elements(node)

    def _flow_elements(self, node: _Node) -> Iterator[Element]:
        if node.tag in _HEADINGS:
            own_cls: Optional[type[Text]] = Title
        elif node.tag == "li":
            own_cls = ListItem
        elif node.tag == "pre":
            own_cls = CodeSnippet
        else:
            own_cls = None
        queue: deque[Union[str, _Node]] = deque(node.children)
        element_cls = own_cls
        while True:
            yield from self._phrasing_run(node, queue, element_cls)
            if not queue:
                return
            block = queue.popleft()
            assert isinstance(block, _Node)
            yield from self._node_elements(block)
            # -- text following a nested block is classified by content, even inside li/h1 --
            element_cls = CodeSnippet if node.tag == "pre" else None

    def _phrasing_run(
        self, node: _Node, queue: deque[Union[str, _Node]], element_cls: Optional[type[Text]]
    ) -> Iterator[Element]:
        segments: list[_Segment] = []
        while queue and self._is_phrasing(queue[0]):
            item = queue.popleft()
            for piece in self._phrasing_pieces(item, ""):
                if isinstance(piece, _Segment):
                    segments.append(piece)
                else:
                    yield from self._flush(node, segments, element_cls)
                    segments = []
                    yield piece
        yield from self._flush(node, segments, element_cls)

    def _flush(self, node: _Node, segments: list[_Segment], element_cls: Optional[type[Text]]) -> Iterator[Element]:
        if not segments:
            return
        raw = "".join(s.text for s in segments)
        if node.tag == "pre":
            text = raw[1:] if raw.startswith("\n") else raw
            text = text[:-1] if text.endswith("\n") else text
        else:
            text = _normalize(raw)
        if not text:
            return
        cls = element_cls
        if cls is None:
            cls = _derive_element_type(text)
            if cls is None:
                return
            if cls is ListItem:
                text = clean_bullets(text)
                if not text:
                    return
        if cls is Title:
            depth: Optional[int] = _HEADINGS.get(node.tag, 0)
        elif cls is ListItem:
            depth = sum(1 for a in node.ancestors() if a.tag in ("dl", "ol", "ul")) if node.tag in ("li", "dd") else 0
        else:
            depth = None
        yield cls(
            text,
            metadata=ElementMetadata(
                **_consolidate(s.annotation for s in segments),
                category_depth=depth,
                page_number=self._page_number(node),
            ),
        )

    # -- phrasing content --

    def _phrasing_pieces(self, item: Union[str, _Node], emphasis: str) -> Iterator[Union[_Segment, Element]]:
        if isinstance(item, str):
            yield _Segment(item, _emphasis_annotation(item, emphasis))
            return
        tag = item.tag
        if tag == "br":
            yield _Segment("\n")
            return
        if tag in _REMOVED_PHRASING:
            return
        if tag == "a":
            yield from self._anchor_pieces(item, emphasis)
            return
        if tag in _BOLD:
            emphasis = "".join(sorted(set(emphasis + "b")))
        elif tag in _ITALIC:
            emphasis = "".join(sorted(set(emphasis + "i")))
        elif tag not in _PHRASING:
            # -- unrecognized tag (script, style, font ...): its content is dropped --
            return
        for child in item.children:
            if isinstance(child, _Node) and not self._is_phrasing(child):
                yield from self._node_elements(child)
            else:
                yield from self._phrasing_pieces(child, emphasis)

    def _anchor_pieces(self, anchor: _Node, emphasis: str) -> Iterator[Union[_Segment, Element]]:
        href = anchor.attrs.get("href")
        groups: list[Union[list[_Segment], Element]] = []
        phrase: list[_Segment] = []
        for child in anchor.children:
            if isinstance(child, _Node) and not self._is_phrasing(child):
                if phrase:
                    groups.append(phrase)
                    phrase = []
                groups.extend(self._node_elements(child))
            else:
                for piece in self._phrasing_pieces(child, emphasis):
                    if isinstance(piece, _Segment):
                        phrase.append(piece)
                    else:
                        if phrase:
                            groups.append(phrase)
                            phrase = []
                        groups.append(piece)
        if phrase:
            groups.append(phrase)

        annotated = False
        for group in groups:
            if isinstance(group, Element):
                if not annotated and group.text and href:
                    group.metadata.link_texts = (group.metadata.link_texts or []) + [group.text]
                    group.metadata.link_urls = (group.metadata.link_urls or []) + [href]
                    annotated = True
                yield group
                continue
            consolidated = "".join(s.text for s in group)
            link_text = _normalize(consolidated)
            if not annotated and link_text and href:
                annotation = _consolidate(
                    [{"link_texts": [link_text], "link_urls": [href]}, *(s.annotation for s in group)]
                )
                yield _Segment(consolidated, annotation)
                annotated = True
            else:
                yield from group

    # -- special blocks --

    def _image_elements(self, img: _Node) -> Iterator[Element]:
        src = (img.attrs.get("data-src") or "").strip() or (img.attrs.get("src") or "").strip()
        if not src:
            return
        match = _BASE64_IMAGE_RE.match(src)
        yield Image(
            text=(img.attrs.get("alt") or "").strip(),
            metadata=ElementMetadata(
                image_mime_type=match.group(1) if match else None,
                image_base64=match.group(2) if match else None,
                image_url=None if match else src,
                page_number=self._page_number(img),
            ),
        )

    def _table_elements(self, table: _Node) -> Iterator[Element]:
        rows: list[_Node] = []
        for child in table.children:
            if not isinstance(child, _Node):
                continue
            if child.tag == "tr":
                rows.append(child)
            elif child.tag in ("thead", "tbody", "tfoot"):
                rows.extend(c for c in child.children if isinstance(c, _Node) and c.tag == "tr")
        if not rows:
            return
        matrix = [
            [
                " ".join(t.strip() for t in cell.itertext() if t.strip())
                for cell in row.children
                if isinstance(cell, _Node) and cell.tag in ("td", "th")
            ]
            for row in rows
        ]
        text = " ".join(" ".join(t for t in row if t) for row in matrix).strip()
        if not text:
            return
        yield Table(
            text,
            metadata=ElementMetadata(
                text_as_html=htmlify_matrix_of_cell_texts(matrix), page_number=self._page_number(table)
            ),
        )


# -- input -----------------------------------------------------------------------------------


def _decode_html(data: bytes, encoding: Optional[str]) -> str:
    if not encoding:
        declared = _META_CHARSET_RE.search(data[:4096])
        if declared:
            try:
                return data.decode(declared.group(1).decode("ascii"))
            except (LookupError, UnicodeDecodeError):
                pass
    return decode_bytes(data, encoding)[1]


def fetch_url(
    url: str,
    headers: Optional[dict[str, str]] = None,
    ssl_verify: bool = True,
    timeout: float = 30.0,
) -> tuple[str, str]:
    """GET an http(s) URL, returning ``(content_type, decoded_text)``."""
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("Only http and https URLs can be fetched.")
    request = urllib.request.Request(url, headers=dict(headers or {}))
    context = None
    if not ssl_verify:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        content_type = response.headers.get("Content-Type", "")
        charset = response.headers.get_content_charset()
        data = response.read()
    return content_type, _decode_html(data, charset)


@apply_metadata(FileType.HTML)
def partition_html(
    filename: Optional[str] = None,
    *,
    file: Optional[IO[Any]] = None,
    text: Optional[str] = None,
    encoding: Optional[str] = None,
    url: Optional[str] = None,
    headers: dict[str, str] = {},
    ssl_verify: bool = True,
    skip_headers_and_footers: bool = False,
    detection_origin: Optional[str] = None,
    html_parser_version: str = "v1",
    image_alt_mode: Optional[str] = "to_text",
    extract_image_block_to_payload: bool = False,
    extract_image_block_types: Optional[list[str]] = None,
    languages: Optional[list[str]] = None,
    detect_language_per_element: bool = False,
    language_fallback: Optional[Callable[[str], Optional[list[str]]]] = None,
    **kwargs: Any,
) -> list[Element]:
    """Partition an HTML document from a path, file, string or URL."""
    if text is not None and text.strip() == "" and not file and not filename and not url:
        return []
    exactly_one(filename=filename, file=file, text=text, url=url)
    if html_parser_version != "v1":
        raise ValueError("tryworks implements html_parser_version='v1' only.")
    if url is not None:
        content_type, html_text = fetch_url(url, headers=headers, ssl_verify=ssl_verify)
        if content_type and not content_type.lower().startswith(("text/html", "application/xhtml")):
            raise ValueError(f"Expected content type text/html. Got {content_type}.")
    elif text is not None:
        html_text = str(text)
    elif file is not None:
        if hasattr(file, "seek"):
            file.seek(0)
        raw = file.read()
        html_text = raw if isinstance(raw, str) else _decode_html(bytes(raw), encoding)
    else:
        html_text = _decode_html(read_bytes(filename=filename), encoding)
    if not html_text.strip():
        return []
    # -- browsers normalize line endings before parsing; <pre> text depends on it --
    html_text = html_text.replace("\r\n", "\n").replace("\r", "\n")
    return list(_HtmlPartitioner(html_text, skip_headers_and_footers).iter_elements())

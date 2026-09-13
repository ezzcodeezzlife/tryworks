"""Chunking machinery shared by the "basic" and "by_title" strategies.

Chunking runs in three passes:

1. Pre-chunking groups consecutive elements into the largest runs that fit ``max_characters``,
   starting a new run at every semantic boundary (a Title for "by_title", optionally a page
   change) and giving each Table a run of its own.
2. Combining (by_title only) merges a small run of text into the next one while the first is
   under ``combine_text_under_n_chars`` and the result still fits.
3. Chunking turns each run into a CompositeElement (texts joined by a blank line) and splits a
   run that is still too long at a newline, else a space. Tables pass through whole when they
   fit, otherwise they are split row by row into TableChunk elements.
"""

from __future__ import annotations

import copy
import html
import re
from typing import Any, Callable, Iterable, Iterator, Optional

from tryworks.documents.elements import (
    CompositeElement,
    Element,
    ElementMetadata,
    PageBreak,
    Table,
    TableChunk,
    Text,
    Title,
)

CHUNK_MAX_CHARS_DEFAULT = 500
CHUNK_MULTI_PAGE_DEFAULT = True
TEXT_SEPARATOR = "\n\n"

BoundaryPredicate = Callable[[Element], bool]

# -- how each metadata field is merged when several elements become one chunk --
_DROP = frozenset(
    """category_depth coordinates detection_class_prob detection_origin header_footer_type image_url
    image_path image_base64 image_mime_type is_continuation is_extracted link_start_indexes links
    max_characters orig_elements parent_id routing routing_score table_id chunk_index
    num_carried_over_header_rows segment_start_seconds segment_end_seconds key_value_pairs""".split()
)
_LIST_CONCATENATE = frozenset(["emphasized_text_contents", "emphasized_text_tags", "link_texts", "link_urls"])
_LIST_UNIQUE = frozenset(["languages"])
_STRING_CONCATENATE = frozenset(["text_as_html"])

_TR_RE = re.compile(r"<tr\b[^>]*>.*?</tr>|<tr\s*/>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


class ChunkingOptions:
    """Validated chunking parameters, with unstructured's defaults."""

    def __init__(
        self,
        *,
        max_characters: Optional[int] = None,
        new_after_n_chars: Optional[int] = None,
        overlap: Optional[int] = None,
        overlap_all: Optional[bool] = None,
        combine_text_under_n_chars: Optional[int] = None,
        include_orig_elements: Optional[bool] = None,
        repeat_table_headers: Optional[bool] = None,
        skip_table_chunking: Optional[bool] = None,
        isolate_table: Optional[bool] = None,
        max_tokens: Optional[int] = None,
        new_after_n_tokens: Optional[int] = None,
        tokenizer: Optional[Any] = None,
        combine_default_to_max: bool = False,
    ):
        if max_tokens is not None or new_after_n_tokens is not None or tokenizer is not None:
            raise NotImplementedError("Token-based chunking is not supported by tryworks; use max_characters.")
        self.hard_max = CHUNK_MAX_CHARS_DEFAULT if max_characters is None else max_characters
        if self.hard_max <= 0:
            raise ValueError(f"'max_characters' argument must be > 0, got {self.hard_max}")
        if new_after_n_chars is not None and new_after_n_chars < 0:
            raise ValueError(f"'new_after_n_chars' argument must be >= 0, got {new_after_n_chars}")
        self.soft_max = self.hard_max if new_after_n_chars is None else min(new_after_n_chars, self.hard_max)
        self.overlap = overlap or 0
        if self.overlap < 0 or self.overlap >= self.hard_max:
            raise ValueError(f"'overlap' argument must be >= 0 and less than `max_characters`, got {self.overlap}")
        self.inter_chunk_overlap = self.overlap if overlap_all else 0
        if combine_text_under_n_chars is None:
            combine_text_under_n_chars = self.hard_max if combine_default_to_max else 0
        if combine_text_under_n_chars < 0:
            raise ValueError(f"'combine_text_under_n_chars' argument must be >= 0, got {combine_text_under_n_chars}")
        if combine_text_under_n_chars > self.hard_max:
            raise ValueError(
                "'combine_text_under_n_chars' argument must not exceed `max_characters` value, "
                f"got {combine_text_under_n_chars}"
            )
        self.combine_text_under_n_chars = combine_text_under_n_chars
        self.include_orig_elements = True if include_orig_elements is None else bool(include_orig_elements)
        # -- accepted for API compatibility; tryworks' partitioners never mark header rows --
        self.repeat_table_headers = True if repeat_table_headers is None else bool(repeat_table_headers)
        self.skip_table_chunking = bool(skip_table_chunking)
        self.isolate_table = True if isolate_table is None else bool(isolate_table)
        if self.skip_table_chunking and not self.isolate_table:
            raise ValueError("'skip_table_chunking=True' requires 'isolate_table=True' (the default).")


def _is_table(element: Element) -> bool:
    return isinstance(element, Table)


def _joined_length(elements: list[Element]) -> int:
    return len(TEXT_SEPARATOR.join(e.text for e in elements if e.text))


class PreChunk:
    """A run of elements that will become one chunk (or several, if it must be split)."""

    def __init__(self, elements: list[Element], opts: ChunkingOptions):
        self.elements = elements
        self.opts = opts
        self.overlap_prefix = ""

    @property
    def is_table(self) -> bool:
        return len(self.elements) == 1 and _is_table(self.elements[0])

    @property
    def contains_table(self) -> bool:
        return any(_is_table(e) for e in self.elements)

    @property
    def text(self) -> str:
        segments = ([self.overlap_prefix] if self.overlap_prefix else []) + [e.text for e in self.elements if e.text]
        return TEXT_SEPARATOR.join(segments)

    def overlap_tail(self) -> str:
        n = self.opts.inter_chunk_overlap
        return self.text[-n:].strip() if n else ""

    def can_combine(self, other: PreChunk) -> bool:
        if _joined_length(self.elements) >= self.opts.combine_text_under_n_chars:
            return False
        if self.opts.isolate_table and (self.contains_table or other.contains_table):
            return False
        return _joined_length(self.elements + other.elements) <= self.opts.hard_max

    def combine(self, other: PreChunk) -> PreChunk:
        return PreChunk(self.elements + other.elements, self.opts)


def iter_pre_chunks(
    elements: Iterable[Element], opts: ChunkingOptions, boundaries: tuple[BoundaryPredicate, ...]
) -> Iterator[PreChunk]:
    current: list[Element] = []

    def fits(element: Element) -> bool:
        if not current:
            return True
        if opts.isolate_table and (_is_table(element) or any(_is_table(e) for e in current)):
            return False
        length = _joined_length(current)
        if length >= opts.soft_max:
            return False
        return _joined_length(current + [element]) <= opts.hard_max

    for element in elements:
        if not isinstance(element, Text):
            continue  # -- CheckBox and other text-less elements are dropped --
        # -- evaluate every predicate: stateful ones (page tracking) must see each element --
        new_unit = any([predicate(element) for predicate in boundaries])
        if current and (new_unit or not fits(element)):
            yield PreChunk(current, opts)
            current = []
        current.append(element)
    if current:
        yield PreChunk(current, opts)


def combine_pre_chunks(pre_chunks: Iterable[PreChunk], opts: ChunkingOptions) -> Iterator[PreChunk]:
    accumulated: Optional[PreChunk] = None
    for pre_chunk in pre_chunks:
        if accumulated is None:
            accumulated = pre_chunk
        elif accumulated.can_combine(pre_chunk):
            accumulated = accumulated.combine(pre_chunk)
        else:
            yield accumulated
            accumulated = pre_chunk
    if accumulated is not None:
        yield accumulated


def split_text(s: str, maxlen: int, overlap: int = 0) -> tuple[str, str]:
    """Split ``s`` into a fragment of at most ``maxlen`` characters and the remainder.

    The split is made at the last newline before ``maxlen``, else the last space, else exactly at
    ``maxlen``. The remainder begins up to ``overlap`` characters before the split point, moved
    forward to a word boundary.
    """
    if len(s) <= maxlen:
        return s, ""
    for separator in ("\n", " "):
        cut = s.rfind(separator, 0, maxlen + 1)
        fragment = s[:cut].rstrip() if cut > 0 else ""
        if not fragment:
            continue
        rest_start = cut + 1
        if overlap:
            back = max(cut - overlap, 0)
            boundary = s.find(" ", back, cut)
            rest_start = boundary + 1 if boundary != -1 else back
        return fragment, s[rest_start:].lstrip()
    return s[:maxlen], s[maxlen - overlap :]


def consolidate_metadata(elements: list[Element]) -> ElementMetadata:
    """Merge metadata of several elements: first value, concatenation or union, per field."""
    merged: dict[str, Any] = {}
    for element in elements:
        for name, value in element.metadata.fields.items():
            if name in _DROP:
                continue
            if name in _LIST_CONCATENATE:
                merged.setdefault(name, []).extend(value)
            elif name in _LIST_UNIQUE:
                bucket = merged.setdefault(name, [])
                bucket.extend(v for v in value if v not in bucket)
            elif name in _STRING_CONCATENATE:
                merged[name] = merged.get(name, "") + value
            elif name == "enrichment_origins":
                target = merged.setdefault(name, {})
                for key, records in value.items():
                    existing = target.setdefault(key, [])
                    existing.extend(r for r in records if r not in existing)
            elif name not in merged:
                merged[name] = copy.deepcopy(value)
    metadata = ElementMetadata()
    for name, value in merged.items():
        setattr(metadata, name, value)
    return metadata


def _orig_elements(elements: list[Element]) -> list[Element]:
    out = []
    for element in elements:
        clone = copy.deepcopy(element)
        clone.metadata.orig_elements = None
        out.append(clone)
    return out


def _iter_text_chunks(pre_chunk: PreChunk, opts: ChunkingOptions) -> Iterator[Element]:
    metadata = consolidate_metadata([e for e in pre_chunk.elements if not isinstance(e, PageBreak)])
    if opts.include_orig_elements:
        metadata.orig_elements = _orig_elements(pre_chunk.elements)
    remainder = pre_chunk.text
    index = 0
    while remainder:
        fragment, remainder = split_text(remainder, opts.hard_max, opts.overlap)
        chunk_metadata = copy.deepcopy(metadata)
        if index:
            chunk_metadata.is_continuation = True
        yield CompositeElement(text=fragment, metadata=chunk_metadata)
        index += 1


def _row_text(rows: list[str]) -> str:
    return " ".join(html.unescape(" ".join(_TAG_RE.sub(" ", row).split())) for row in rows).strip()


def _iter_table_chunks(table: Table, opts: ChunkingOptions, overlap_prefix: str) -> Iterator[Element]:
    text = f"{overlap_prefix} {table.text}".strip() if overlap_prefix else table.text
    table_html = table.metadata.text_as_html
    if opts.skip_table_chunking or (len(text) <= opts.hard_max and (table_html is None or len(table_html) <= opts.hard_max)):
        whole = copy.deepcopy(table)
        whole.text = text
        whole._element_id = None
        if opts.include_orig_elements:
            whole.metadata.orig_elements = _orig_elements([table])
        yield whole
        return

    pieces: list[tuple[str, Optional[str]]] = []

    def add_text_pieces(value: str) -> None:
        remainder = value
        while remainder:
            fragment, remainder = split_text(remainder, opts.hard_max, opts.overlap)
            pieces.append((fragment, None))

    rows = _TR_RE.findall(table_html) if table_html else []
    if rows:
        batch: list[str] = []
        for row in rows:
            if len(f"<table>{row}</table>") > opts.hard_max or len(_row_text([row])) > opts.hard_max:
                if batch:
                    pieces.append((_row_text(batch), f"<table>{''.join(batch)}</table>"))
                    batch = []
                add_text_pieces(_row_text([row]))
                continue
            candidate = batch + [row]
            too_big = len(f"<table>{''.join(candidate)}</table>") > opts.hard_max or len(_row_text(candidate)) > opts.hard_max
            if batch and too_big:
                pieces.append((_row_text(batch), f"<table>{''.join(batch)}</table>"))
                batch = [row]
            else:
                batch = candidate
        if batch:
            pieces.append((_row_text(batch), f"<table>{''.join(batch)}</table>"))
    else:
        add_text_pieces(text)

    base = copy.deepcopy(table.metadata)
    base.orig_elements = None
    for index, (piece_text, piece_html) in enumerate(pieces):
        metadata = copy.deepcopy(base)
        metadata.text_as_html = piece_html
        metadata.table_id = table.id
        metadata.chunk_index = index
        metadata.is_continuation = True if index else None
        if opts.include_orig_elements:
            metadata.orig_elements = _orig_elements([table])
        yield TableChunk(text=piece_text, metadata=metadata)


def iter_chunks(pre_chunks: Iterable[PreChunk], opts: ChunkingOptions) -> Iterator[Element]:
    previous_tail = ""
    for pre_chunk in pre_chunks:
        pre_chunk.overlap_prefix = previous_tail if opts.inter_chunk_overlap else ""
        if pre_chunk.is_table and opts.isolate_table:
            yield from _iter_table_chunks(pre_chunk.elements[0], opts, pre_chunk.overlap_prefix)  # type: ignore[arg-type]
        else:
            yield from _iter_text_chunks(pre_chunk, opts)
        pre_chunk.overlap_prefix = ""
        previous_tail = pre_chunk.overlap_tail()


def is_title(element: Element) -> bool:
    return isinstance(element, Title)


def is_on_next_page() -> BoundaryPredicate:
    """A stateful predicate that is True for the first element on each new page."""
    state: dict[str, Optional[int]] = {"page": None}

    def predicate(element: Element) -> bool:
        page = element.metadata.page_number
        if page is None:
            return False
        if state["page"] is None:
            state["page"] = page
            return False
        if page != state["page"]:
            state["page"] = page
            return True
        return False

    return predicate

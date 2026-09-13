"""Chunk elements into sections that start at each Title, same API as ``unstructured.chunking.title``."""

from __future__ import annotations

from typing import Any, Iterable, Iterator, Optional

from tryworks.chunking.base import (
    CHUNK_MULTI_PAGE_DEFAULT,
    ChunkingOptions,
    combine_pre_chunks,
    is_on_next_page,
    is_title,
    iter_chunks,
    iter_pre_chunks,
)
from tryworks.documents.elements import Element


def iter_chunks_by_title(
    elements: Iterable[Element],
    *,
    combine_text_under_n_chars: Optional[int] = None,
    include_orig_elements: Optional[bool] = None,
    max_characters: Optional[int] = None,
    max_tokens: Optional[int] = None,
    multipage_sections: Optional[bool] = None,
    new_after_n_chars: Optional[int] = None,
    new_after_n_tokens: Optional[int] = None,
    overlap: Optional[int] = None,
    overlap_all: Optional[bool] = None,
    tokenizer: Optional[Any] = None,
    repeat_table_headers: Optional[bool] = None,
    skip_table_chunking: Optional[bool] = None,
    isolate_table: Optional[bool] = None,
) -> Iterator[Element]:
    """Lazily yield chunks; see ``chunk_by_title``."""
    opts = ChunkingOptions(
        max_characters=max_characters,
        new_after_n_chars=new_after_n_chars,
        overlap=overlap,
        overlap_all=overlap_all,
        combine_text_under_n_chars=combine_text_under_n_chars,
        include_orig_elements=include_orig_elements,
        repeat_table_headers=repeat_table_headers,
        skip_table_chunking=skip_table_chunking,
        isolate_table=isolate_table,
        max_tokens=max_tokens,
        new_after_n_tokens=new_after_n_tokens,
        tokenizer=tokenizer,
        combine_default_to_max=True,
    )
    multipage = CHUNK_MULTI_PAGE_DEFAULT if multipage_sections is None else multipage_sections
    boundaries = (is_title,) if multipage else (is_title, is_on_next_page())
    pre_chunks = iter_pre_chunks(elements, opts, boundaries)
    if opts.combine_text_under_n_chars > 0:
        pre_chunks = combine_pre_chunks(pre_chunks, opts)
    yield from iter_chunks(pre_chunks, opts)


def chunk_by_title(elements: Iterable[Element], **kwargs: Any) -> list[Element]:
    """Group elements into chunks of up to ``max_characters`` (default 500), one section per Title.

    ``new_after_n_chars`` closes a chunk early once it reaches that size.
    ``combine_text_under_n_chars`` (default ``max_characters``) merges small sections.
    ``multipage_sections=False`` also starts a new section on every page.
    ``overlap`` repeats that many characters across split chunks (all chunks with ``overlap_all``).
    ``include_orig_elements`` (default True) keeps the source elements in ``metadata.orig_elements``.
    """
    return list(iter_chunks_by_title(elements, **kwargs))

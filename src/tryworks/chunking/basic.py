"""Fill chunks greedily up to a size limit, same API as ``unstructured.chunking.basic``."""

from __future__ import annotations

from typing import Any, Iterable, Iterator, Optional

from tryworks.chunking.base import ChunkingOptions, iter_chunks, iter_pre_chunks
from tryworks.documents.elements import Element


def iter_chunk_elements(
    elements: Iterable[Element],
    *,
    include_orig_elements: Optional[bool] = None,
    max_characters: Optional[int] = None,
    max_tokens: Optional[int] = None,
    new_after_n_chars: Optional[int] = None,
    new_after_n_tokens: Optional[int] = None,
    overlap: Optional[int] = None,
    overlap_all: Optional[bool] = None,
    tokenizer: Optional[Any] = None,
    repeat_table_headers: Optional[bool] = None,
    skip_table_chunking: Optional[bool] = None,
    isolate_table: Optional[bool] = None,
) -> Iterator[Element]:
    """Lazily yield chunks; see ``chunk_elements``."""
    opts = ChunkingOptions(
        max_characters=max_characters,
        new_after_n_chars=new_after_n_chars,
        overlap=overlap,
        overlap_all=overlap_all,
        include_orig_elements=include_orig_elements,
        repeat_table_headers=repeat_table_headers,
        skip_table_chunking=skip_table_chunking,
        isolate_table=isolate_table,
        max_tokens=max_tokens,
        new_after_n_tokens=new_after_n_tokens,
        tokenizer=tokenizer,
    )
    yield from iter_chunks(iter_pre_chunks(elements, opts, ()), opts)


def chunk_elements(elements: Iterable[Element], **kwargs: Any) -> list[Element]:
    """Pack consecutive elements into chunks of up to ``max_characters`` (default 500).

    Sections are not respected; tables still get chunks of their own.
    """
    return list(iter_chunk_elements(elements, **kwargs))

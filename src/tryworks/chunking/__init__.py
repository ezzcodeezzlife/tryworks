"""Chunking strategies: "basic" and "by_title"."""

from __future__ import annotations

from typing import Any, Iterable

from tryworks.chunking.base import CHUNK_MAX_CHARS_DEFAULT, CHUNK_MULTI_PAGE_DEFAULT
from tryworks.documents.elements import Element

__all__ = ["CHUNK_MAX_CHARS_DEFAULT", "CHUNK_MULTI_PAGE_DEFAULT", "chunk"]

_TITLE_ONLY = ("combine_text_under_n_chars", "multipage_sections")


def chunk(elements: Iterable[Element], chunking_strategy: str, **kwargs: Any) -> list[Element]:
    """Apply a named chunking strategy, as ``partition(..., chunking_strategy=...)`` does."""
    if chunking_strategy == "by_title":
        from tryworks.chunking.title import chunk_by_title

        return chunk_by_title(elements, **kwargs)
    if chunking_strategy == "basic":
        from tryworks.chunking.basic import chunk_elements

        return chunk_elements(elements, **{k: v for k, v in kwargs.items() if k not in _TITLE_ONLY})
    raise ValueError(f"Unsupported chunking strategy {chunking_strategy!r}; use 'basic' or 'by_title'.")

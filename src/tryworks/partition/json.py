"""Load elements previously serialized to JSON (by tryworks or unstructured)."""

from __future__ import annotations

import json
from typing import IO, Any, Optional

from tryworks.documents.elements import Element
from tryworks.partition.common import FileType, apply_metadata, exactly_one, read_text
from tryworks.staging.base import elements_from_dicts


@apply_metadata(FileType.JSON)
def partition_json(
    filename: Optional[str] = None,
    *,
    file: Optional[IO[Any]] = None,
    text: Optional[str] = None,
    encoding: Optional[str] = None,
    **kwargs: Any,
) -> list[Element]:
    """Read a JSON array of element dicts, as written by ``elements_to_json``."""
    exactly_one(filename=filename, file=file, text=text)
    content = read_text(filename=filename, file=file, text=text, encoding=encoding)
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Not valid JSON: {e}") from e
    if not isinstance(data, list) or not all(isinstance(d, dict) and "type" in d for d in data):
        raise ValueError("JSON must be an array of element objects with a 'type' key, as written by elements_to_json.")
    return elements_from_dicts(data)

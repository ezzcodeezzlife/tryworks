"""Serialize elements to and from JSON, text, Markdown and CSV.

Same function names and output formats as ``unstructured.staging.base``; JSON written by either
library can be read by the other, including chunk ``orig_elements``.
"""

from __future__ import annotations

import base64
import copy
import csv
import io
import json
import zlib
from typing import Any, Iterable, Optional, Sequence

from tryworks.documents.coordinates import PixelSpace
from tryworks.documents.elements import (
    TYPE_TO_TEXT_ELEMENT_MAP,
    CheckBox,
    Element,
    ElementMetadata,
    Image,
    Table,
    TableChunk,
    Title,
)

# -- refuse to inflate more than this from an orig_elements payload --
MAX_DECOMPRESSED_SIZE = 200 * 1024 * 1024


class DecompressedSizeExceededError(ValueError):
    def __init__(self, max_size: int = MAX_DECOMPRESSED_SIZE):
        super().__init__(f"Decompressed orig_elements exceed {max_size} bytes.")


def _fix_metadata_field_precision(elements: Iterable[Element]) -> list[Element]:
    out = []
    for element in elements:
        el = copy.deepcopy(element)
        coords = el.metadata.coordinates
        if coords and coords.points:
            digits = 1 if isinstance(coords.system, PixelSpace) else 2
            coords.points = tuple((round(x, digits), round(y, digits)) for x, y in coords.points)
        if el.metadata.detection_class_prob:
            el.metadata.detection_class_prob = round(el.metadata.detection_class_prob, 5)
        out.append(el)
    return out


def elements_to_dicts(elements: Iterable[Element]) -> list[dict[str, Any]]:
    """Convert elements to JSON-ready dicts."""
    return [e.to_dict() for e in elements]


convert_to_dict = elements_to_dicts


def elements_from_dicts(element_dicts: Iterable[dict[str, Any]]) -> list[Element]:
    """Rebuild elements from dicts made by ``to_dict()``; unknown types are skipped."""
    elements: list[Element] = []
    for item in element_dicts:
        element_id = item.get("element_id")
        raw_meta = item.get("metadata")
        metadata = ElementMetadata() if raw_meta is None else ElementMetadata.from_dict(raw_meta)
        typ = item.get("type")
        if typ in TYPE_TO_TEXT_ELEMENT_MAP:
            cls = TYPE_TO_TEXT_ELEMENT_MAP[typ]
            elements.append(cls(text=item["text"], element_id=element_id, metadata=metadata))
        elif typ == "TableChunk":
            elements.append(TableChunk(text=item["text"], element_id=element_id, metadata=metadata))
        elif typ == "CheckBox":
            elements.append(CheckBox(checked=item["checked"], element_id=element_id, metadata=metadata))
    return elements


dict_to_elements = elements_from_dicts


def elements_to_json(
    elements: Iterable[Element],
    filename: Optional[str] = None,
    indent: int = 4,
    encoding: str = "utf-8",
) -> str:
    """Serialize elements as a JSON array, also writing it to ``filename`` when given."""
    json_str = json.dumps(
        elements_to_dicts(_fix_metadata_field_precision(elements)), indent=indent, sort_keys=True
    )
    if filename is not None:
        with open(filename, "w", encoding=encoding) as f:
            f.write(json_str)
    return json_str


def elements_from_json(filename: str = "", text: str = "", encoding: str = "utf-8") -> list[Element]:
    """Load elements from a JSON file or string (exactly one of the two)."""
    if bool(filename) == bool(text):
        raise ValueError("Exactly one of filename and text must be specified.")
    if filename:
        with open(filename, encoding=encoding) as f:
            return elements_from_dicts(json.load(f))
    return elements_from_dicts(json.loads(text))


def elements_to_ndjson(
    elements: Iterable[Element], filename: Optional[str] = None, encoding: str = "utf-8"
) -> str:
    """Serialize elements as newline-delimited JSON."""
    lines = [
        json.dumps(d, sort_keys=True) for d in elements_to_dicts(_fix_metadata_field_precision(elements))
    ]
    ndjson_str = "\n".join(lines) + ("\n" if lines else "")
    if filename is not None:
        with open(filename, "w", encoding=encoding) as f:
            f.write(ndjson_str)
    return ndjson_str


def elements_from_ndjson(filename: str = "", text: str = "", encoding: str = "utf-8") -> list[Element]:
    if bool(filename) == bool(text):
        raise ValueError("Exactly one of filename and text must be specified.")
    if filename:
        with open(filename, encoding=encoding) as f:
            text = f.read()
    return elements_from_dicts(json.loads(line) for line in text.splitlines() if line.strip())


def elements_to_base64_gzipped_json(elements: Iterable[Element]) -> str:
    """Compact form used for ``metadata.orig_elements``: zlib-deflated JSON, base64-encoded."""
    json_bytes = json.dumps(
        elements_to_dicts(_fix_metadata_field_precision(elements)), sort_keys=True
    ).encode("utf-8")
    return base64.b64encode(zlib.compress(json_bytes)).decode("utf-8")


def elements_from_base64_gzipped_json(b64_encoded_elements: str) -> list[Element]:
    decompressor = zlib.decompressobj()
    json_bytes = decompressor.decompress(base64.b64decode(b64_encoded_elements), MAX_DECOMPRESSED_SIZE)
    if not decompressor.eof:
        if len(json_bytes) >= MAX_DECOMPRESSED_SIZE:
            raise DecompressedSizeExceededError()
        raise zlib.error("Incomplete or corrupted compressed data")
    return elements_from_dicts(json.loads(json_bytes.decode("utf-8")))


def convert_to_text(elements: Iterable[Element]) -> str:
    """Concatenate element texts, one per line."""
    return "\n".join(e.text for e in elements if getattr(e, "text", None))


def elements_to_text(
    elements: Iterable[Element], filename: Optional[str] = None, encoding: str = "utf-8"
) -> Optional[str]:
    text = convert_to_text(elements)
    if filename is not None:
        with open(filename, "w", encoding=encoding) as f:
            f.write(text)
        return None
    return text


def element_to_md(element: Element, exclude_binary_image_data: bool = False, **_: Any) -> str:
    """Render one element as Markdown: titles as headings, tables as HTML, images as links."""
    meta = element.metadata
    if isinstance(element, Title):
        return f"# {element.text}"
    if isinstance(element, Table) and meta.text_as_html is not None:
        return meta.text_as_html
    if isinstance(element, Image):
        if meta.image_base64 is not None and not exclude_binary_image_data:
            mime = meta.image_mime_type or "image/*"
            return f"![{element.text}](data:{mime};base64,{meta.image_base64})"
        if meta.image_url is not None:
            return f"![{element.text}]({meta.image_url})"
    return element.text


def elements_to_md(
    elements: Iterable[Element],
    filename: Optional[str] = None,
    exclude_binary_image_data: bool = False,
    encoding: str = "utf-8",
    **kwargs: Any,
) -> str:
    content = "\n".join(element_to_md(e, exclude_binary_image_data) for e in elements)
    if filename is not None:
        with open(filename, "w", encoding=encoding) as f:
            f.write(content)
    return content


def flatten_dict(
    dictionary: dict[str, Any],
    parent_key: str = "",
    separator: str = "_",
    flatten_lists: bool = False,
    remove_none: bool = False,
    keys_to_omit: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """Flatten nested dicts (and optionally lists) into ``parent_child`` keys."""
    keys_to_omit = list(keys_to_omit or [])
    flat: dict[str, Any] = {}
    for key, value in dictionary.items():
        new_key = f"{parent_key}{separator}{key}" if parent_key else key
        if new_key in keys_to_omit:
            flat[new_key] = value
        elif value is None and remove_none:
            continue
        elif isinstance(value, dict):
            flat.update(flatten_dict(value, new_key, separator, flatten_lists, remove_none, keys_to_omit))
        elif isinstance(value, (list, tuple)) and flatten_lists:
            for index, item in enumerate(value):
                flat.update(
                    flatten_dict(
                        {f"{new_key}{separator}{index}": item}, "", separator, flatten_lists,
                        remove_none, keys_to_omit,
                    )
                )
        else:
            flat[new_key] = value
    return flat


_CSV_BASE_FIELDS = ["type", "text", "element_id", "filename", "page_number", "url", "sender", "subject"]


def convert_to_csv(elements: Iterable[Element]) -> str:
    """One CSV row per element with the common metadata columns."""
    rows = elements_to_dicts(elements)
    fieldnames = list(_CSV_BASE_FIELDS)
    for row in rows:
        metadata = row.pop("metadata", {})
        for key, value in flatten_dict(metadata).items():
            if key in fieldnames:
                row[key] = value
        sent_from = metadata.get("sent_from")
        if sent_from:
            row["sender"] = sent_from[0] if isinstance(sent_from, list) else sent_from
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def filter_element_types(
    elements: Iterable[Element],
    include_element_types: Optional[Sequence[type[Element]]] = None,
    exclude_element_types: Optional[Sequence[type[Element]]] = None,
) -> list[Element]:
    """Keep only (or drop) elements whose exact class is listed."""
    if bool(include_element_types) == bool(exclude_element_types):
        raise ValueError("Exactly one of include_element_types and exclude_element_types must be specified.")
    if include_element_types:
        return [e for e in elements if type(e) in include_element_types]
    return [e for e in elements if type(e) not in exclude_element_types]

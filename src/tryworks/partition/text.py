"""Partition plain text into elements."""

from __future__ import annotations

import re
from typing import IO, Any, Callable, Literal, Optional, Union

from tryworks.cleaners.core import auto_paragraph_grouper, clean_bullets
from tryworks.documents.coordinates import CoordinateSystem
from tryworks.documents.elements import (
    Address,
    Element,
    EmailAddress,
    ListItem,
    NarrativeText,
    Text,
    Title,
)
from tryworks.partition.common import FileType, apply_metadata, exactly_one, read_text
from tryworks.partition.text_type import (
    is_bulleted_text,
    is_email_address,
    is_possible_narrative_text,
    is_possible_numbered_list,
    is_possible_title,
    is_us_city_state_zip,
)

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


@apply_metadata(FileType.TXT)
def partition_text(
    filename: Optional[str] = None,
    *,
    file: Optional[IO[Any]] = None,
    encoding: Optional[str] = None,
    text: Optional[str] = None,
    paragraph_grouper: Union[Callable[[str], str], Literal[False], None] = None,
    detection_origin: Optional[str] = "text",
    **kwargs: Any,
) -> list[Element]:
    """Split a .txt document (or a string) into elements.

    Paragraphs are found with ``auto_paragraph_grouper`` unless ``paragraph_grouper`` is given
    (``False`` disables grouping). Each paragraph becomes a ListItem, EmailAddress, Address,
    NarrativeText, Title or plain Text depending on its content.
    """
    if text is not None and text.strip() == "" and not file and not filename:
        return []
    exactly_one(filename=filename, file=file, text=text)
    file_text = read_text(filename=filename, file=file, text=text, encoding=encoding)

    if paragraph_grouper is None:
        file_text = auto_paragraph_grouper(file_text)
    elif paragraph_grouper is not False:
        file_text = paragraph_grouper(file_text)

    elements: list[Element] = []
    for paragraph in _PARAGRAPH_SPLIT_RE.split(file_text):
        paragraph = paragraph.strip()
        if not paragraph or _is_empty_bullet(paragraph):
            continue
        element = element_from_text(paragraph)
        element.metadata.detection_origin = detection_origin
        elements.append(element)
    return elements


def _is_empty_bullet(text: str) -> bool:
    return is_bulleted_text(text) and not clean_bullets(text).strip()


def element_from_text(
    text: str,
    coordinates: Optional[tuple[tuple[float, float], ...]] = None,
    coordinate_system: Optional[CoordinateSystem] = None,
) -> Element:
    """Choose the element type for one paragraph of text."""
    kwargs: dict[str, Any] = {"coordinates": coordinates, "coordinate_system": coordinate_system}
    if is_bulleted_text(text):
        return ListItem(text=clean_bullets(text), **kwargs)
    if is_email_address(text):
        return EmailAddress(text=text)
    if is_us_city_state_zip(text):
        return Address(text=text, **kwargs)
    if is_possible_numbered_list(text):
        return ListItem(text=text, **kwargs)
    if is_possible_narrative_text(text):
        return NarrativeText(text=text, **kwargs)
    if is_possible_title(text):
        return Title(text=text, **kwargs)
    return Text(text=text, **kwargs)

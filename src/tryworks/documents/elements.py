"""Document elements and their metadata, API-compatible with ``unstructured.documents.elements``.

An element is one semantically coherent piece of a document (a title, a paragraph, a table)
carrying its text and sparse metadata. ``to_dict()`` produces the same JSON shape as
unstructured, so stored element JSON can move between the two libraries.
"""

from __future__ import annotations

import abc
import copy
import hashlib
import os
import pathlib
import uuid
from typing import Any, Callable, Iterable, Optional, TypedDict

from tryworks.documents.coordinates import SYSTEM_BY_NAME, CoordinateSystem

Point = tuple[float, float]
Points = tuple[Point, ...]


class DataSourceMetadata:
    """Where a document came from when it was pulled from a remote source."""

    _FIELDS = (
        "url", "version", "record_locator", "date_created", "date_modified", "date_processed",
        "permissions_data",
    )

    def __init__(self, **kwargs: Any):
        unknown = set(kwargs) - set(self._FIELDS)
        if unknown:
            raise TypeError(f"unexpected DataSourceMetadata fields: {sorted(unknown)}")
        for name in self._FIELDS:
            setattr(self, name, kwargs.get(name))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, DataSourceMetadata) and self.to_dict() == other.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self._FIELDS if getattr(self, name) is not None}

    @classmethod
    def from_dict(cls, input_dict: dict[str, Any]) -> DataSourceMetadata:
        return cls(**{k: v for k, v in input_dict.items() if k in cls._FIELDS})


class CoordinatesMetadata:
    """Corner points of an element's bounding region plus the system they are expressed in."""

    def __init__(self, points: Optional[Points], system: Optional[CoordinateSystem]):
        if (points is None) != (system is None):
            raise ValueError(
                "Coordinates points should not exist without coordinates system and vice versa."
            )
        self.points = points
        self.system = system

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, CoordinatesMetadata)
            and self.points == other.points
            and self.system == other.system
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "points": self.points,
            "system": None if self.system is None else type(self.system).__name__,
            "layout_width": None if self.system is None else self.system.width,
            "layout_height": None if self.system is None else self.system.height,
        }

    @classmethod
    def from_dict(cls, input_dict: dict[str, Any]) -> CoordinatesMetadata:
        raw_points = input_dict.get("points")
        points = None if raw_points is None else tuple(tuple(p) for p in raw_points)
        system = None
        system_cls = SYSTEM_BY_NAME.get(input_dict.get("system") or "")
        if system_cls is not None:
            if system_cls.__name__ == "RelativeCoordinateSystem":
                system = system_cls()
            else:
                system = system_cls(input_dict.get("layout_width"), input_dict.get("layout_height"))
        return cls(points=points, system=system)


class Link(TypedDict):
    text: Optional[str]
    url: str
    start_index: int


def _include_debug_metadata() -> bool:
    return os.environ.get("UNSTRUCTURED_INCLUDE_DEBUG_METADATA", "").lower() in ("1", "true")


class ElementMetadata:
    """Sparse metadata for an element.

    Unset fields read as ``None`` and are omitted from ``to_dict()``. Assigning ``None`` removes
    a field. Fields outside the known list may be added ad hoc and are serialized too.
    """

    DEBUG_FIELD_NAMES = frozenset(["detection_origin"])

    _known_field_names = frozenset(
        [
            "attached_to_filename", "bcc_recipient", "category_depth", "cc_recipient",
            "coordinates", "data_source", "detection_class_prob", "detection_origin",
            "enrichment_origins", "emphasized_text_contents", "emphasized_text_tags",
            "file_directory", "filename", "filetype", "header_footer_type", "image_base64",
            "image_mime_type", "image_url", "image_path", "is_continuation", "is_extracted",
            "key_value_pairs", "languages", "last_modified", "link_texts", "link_urls",
            "link_start_indexes", "links", "email_message_id", "orig_elements", "page_name",
            "page_number", "parent_id", "routing", "routing_score", "sent_from", "sent_to",
            "signature", "subject", "table_as_cells", "table_extraction_method", "table_id",
            "chunk_index", "num_carried_over_header_rows", "text_as_html", "url",
            "segment_end_seconds", "segment_start_seconds",
        ]
    )

    def __init__(self, **fields: Any) -> None:
        unknown = set(fields) - self._known_field_names
        if unknown:
            raise TypeError(f"ElementMetadata got unexpected keyword arguments: {sorted(unknown)}")
        filename = fields.pop("filename", None)
        file_directory = fields.pop("file_directory", None)
        if isinstance(filename, pathlib.Path):
            filename = str(filename)
        directory_path, file_name = os.path.split(filename or "")
        self.file_directory = file_directory or directory_path or None
        self.filename = file_name or None
        for name, value in fields.items():
            setattr(self, name, value)

    def __getattr__(self, attr_name: str) -> Any:
        # -- only called when normal lookup fails, i.e. for an unset field --
        if attr_name in ElementMetadata._known_field_names:
            return None
        raise AttributeError(f"'ElementMetadata' object has no attribute '{attr_name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        if value is None:
            self.__dict__.pop(name, None)
            return
        if name in self.DEBUG_FIELD_NAMES and not _include_debug_metadata():
            return
        self.__dict__[name] = value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ElementMetadata) and self.fields == other.fields

    def __repr__(self) -> str:
        pairs = ", ".join(f"{k}={v!r}" for k, v in self.fields.items())
        return f"ElementMetadata({pairs})"

    def __deepcopy__(self, memo: dict) -> ElementMetadata:
        clone = ElementMetadata.__new__(ElementMetadata)
        clone.__dict__.update(copy.deepcopy(self.__dict__, memo))
        return clone

    @property
    def fields(self) -> dict[str, Any]:
        """Every populated field, known or ad hoc."""
        return dict(self.__dict__)

    @property
    def known_fields(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k in self._known_field_names}

    def update(self, other: ElementMetadata) -> None:
        """Copy every populated field of ``other`` onto this metadata, like ``dict.update()``."""
        for name, value in other.fields.items():
            setattr(self, name, value)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict with no ``None`` values, empty lists or empty dicts."""
        from tryworks.staging.base import elements_to_base64_gzipped_json

        meta = {k: v for k, v in self.fields.items() if k not in ("coordinates", "data_source", "orig_elements")}
        meta = copy.deepcopy(meta)
        for name in self.DEBUG_FIELD_NAMES:
            meta.pop(name, None)
        meta = {k: v for k, v in meta.items() if v != [] and v != {}}
        if self.coordinates is not None:
            meta["coordinates"] = self.coordinates.to_dict()
        if self.data_source is not None:
            meta["data_source"] = self.data_source.to_dict()
        if self.orig_elements is not None:
            meta["orig_elements"] = elements_to_base64_gzipped_json(self.orig_elements)
        return meta

    @classmethod
    def from_dict(cls, meta_dict: dict[str, Any]) -> ElementMetadata:
        """Rebuild metadata from a dict made by ``to_dict()``."""
        from tryworks.staging.base import elements_from_base64_gzipped_json

        meta = cls()
        for name, value in copy.deepcopy(meta_dict).items():
            if name == "coordinates" and value is not None:
                value = CoordinatesMetadata.from_dict(value)
            elif name == "data_source" and value is not None:
                value = DataSourceMetadata.from_dict(value)
            elif name == "orig_elements" and value is not None:
                value = elements_from_base64_gzipped_json(value)
            setattr(meta, name, value)
        return meta


class ElementType:
    TITLE = "Title"
    TEXT = "Text"
    UNCATEGORIZED_TEXT = "UncategorizedText"
    NARRATIVE_TEXT = "NarrativeText"
    BULLETED_TEXT = "BulletedText"
    PARAGRAPH = "Paragraph"
    ABSTRACT = "Abstract"
    THREADING = "Threading"
    FORM = "Form"
    FIELD_NAME = "Field-Name"
    VALUE = "Value"
    LINK = "Link"
    COMPOSITE_ELEMENT = "CompositeElement"
    IMAGE = "Image"
    PICTURE = "Picture"
    FIGURE_CAPTION = "FigureCaption"
    FIGURE = "Figure"
    CAPTION = "Caption"
    LIST = "List"
    LIST_ITEM = "ListItem"
    LIST_ITEM_OTHER = "List-item"
    CHECKED = "Checked"
    UNCHECKED = "Unchecked"
    CHECK_BOX_CHECKED = "CheckBoxChecked"
    CHECK_BOX_UNCHECKED = "CheckBoxUnchecked"
    RADIO_BUTTON_CHECKED = "RadioButtonChecked"
    RADIO_BUTTON_UNCHECKED = "RadioButtonUnchecked"
    ADDRESS = "Address"
    EMAIL_ADDRESS = "EmailAddress"
    PAGE_BREAK = "PageBreak"
    FORMULA = "Formula"
    TABLE = "Table"
    HEADER = "Header"
    HEADLINE = "Headline"
    SUB_HEADLINE = "Subheadline"
    PAGE_HEADER = "Page-header"
    SECTION_HEADER = "Section-header"
    FOOTER = "Footer"
    FOOTNOTE = "Footnote"
    PAGE_FOOTER = "Page-footer"
    PAGE_NUMBER = "PageNumber"
    CODE_SNIPPET = "CodeSnippet"
    FORM_KEYS_VALUES = "FormKeysValues"
    DOCUMENT_DATA = "DocumentData"

    @classmethod
    def to_dict(cls) -> dict[str, str]:
        return {k: v for k, v in vars(cls).items() if not k.startswith("_") and isinstance(v, str)}


class Element(abc.ABC):
    """Base class for all document elements."""

    category = "UncategorizedText"

    def __init__(
        self,
        element_id: Optional[str] = None,
        coordinates: Optional[Points] = None,
        coordinate_system: Optional[CoordinateSystem] = None,
        metadata: Optional[ElementMetadata] = None,
        detection_origin: Optional[str] = None,
    ):
        if element_id is not None and not isinstance(element_id, str):
            raise ValueError("element_id must be of type str or None.")
        self._element_id = element_id
        self.metadata = ElementMetadata() if metadata is None else metadata
        if coordinates is not None or coordinate_system is not None:
            self.metadata.coordinates = CoordinatesMetadata(points=coordinates, system=coordinate_system)
        self.metadata.detection_origin = detection_origin
        if not hasattr(self, "text"):
            self.text = ""

    def __str__(self) -> str:
        return self.text

    @property
    def id(self) -> str:
        if self._element_id is None:
            self._element_id = str(uuid.uuid4())
        return self._element_id

    def id_to_hash(self, sequence_number: int) -> str:
        """Replace the id with a deterministic hash of filename, text, page and position on page."""
        data = f"{self.metadata.filename}{self.text}{self.metadata.page_number}{sequence_number}"
        self._element_id = hashlib.sha256(data.encode()).hexdigest()[:32]
        return self.id

    def convert_coordinates_to_new_system(
        self, new_system: CoordinateSystem, in_place: bool = True
    ) -> Optional[Points]:
        coords = self.metadata.coordinates
        if coords is None or coords.system is None or coords.points is None:
            return None
        new_points = tuple(
            coords.system.convert_coordinates_to_new_system(new_system, x, y) for x, y in coords.points
        )
        if in_place:
            coords.points = new_points
            coords.system = new_system
        return new_points

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": None,
            "element_id": self.id,
            "text": self.text,
            "metadata": self.metadata.to_dict(),
        }


class CheckBox(Element):
    """A check box, typically found in forms."""

    def __init__(
        self,
        element_id: Optional[str] = None,
        coordinates: Optional[Points] = None,
        coordinate_system: Optional[CoordinateSystem] = None,
        checked: bool = False,
        metadata: Optional[ElementMetadata] = None,
        detection_origin: Optional[str] = None,
    ):
        super().__init__(
            element_id=element_id,
            coordinates=coordinates,
            coordinate_system=coordinate_system,
            metadata=metadata,
            detection_origin=detection_origin,
        )
        self.checked = checked

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, CheckBox)
            and self.checked == other.checked
            and self.metadata.coordinates == other.metadata.coordinates
        )

    def to_dict(self) -> dict[str, Any]:
        out = super().to_dict()
        out["type"] = "CheckBox"
        out["checked"] = self.checked
        return out


class Text(Element):
    """An element holding free text."""

    def __init__(
        self,
        text: str,
        element_id: Optional[str] = None,
        coordinates: Optional[Points] = None,
        coordinate_system: Optional[CoordinateSystem] = None,
        metadata: Optional[ElementMetadata] = None,
        detection_origin: Optional[str] = None,
        embeddings: Optional[list[float]] = None,
    ):
        self.text: str = text
        self.embeddings = embeddings
        super().__init__(
            element_id=element_id,
            coordinates=coordinates,
            coordinate_system=coordinate_system,
            metadata=metadata if metadata else ElementMetadata(),
            detection_origin=detection_origin,
        )

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Text)
            and self.text == other.text
            and self.metadata.coordinates == other.metadata.coordinates
            and self.category == other.category
            and self.embeddings == other.embeddings
        )

    def __repr__(self) -> str:
        preview = self.text if len(self.text) <= 60 else self.text[:57] + "..."
        return f"<{type(self).__name__}: {preview!r}>"

    def apply(self, *cleaners: Callable[[str], str]) -> None:
        """Run each cleaner over the text, in order."""
        cleaned = self.text
        for cleaner in cleaners:
            cleaned = cleaner(cleaned)
        if not isinstance(cleaned, str):
            raise ValueError("Cleaner produced a non-string output.")
        self.text = cleaned

    def to_dict(self) -> dict[str, Any]:
        out = super().to_dict()
        out["type"] = self.category
        if self.embeddings:
            out["embeddings"] = self.embeddings
        return out


class Formula(Text):
    category = "Formula"


class CompositeElement(Text):
    """A chunk made by combining several elements."""

    category = "CompositeElement"


class FigureCaption(Text):
    category = "FigureCaption"


class NarrativeText(Text):
    """Prose: sentences and paragraphs."""

    category = "NarrativeText"


class Form(Text):
    category = "Form"


class ListItem(Text):
    category = "ListItem"


class Title(Text):
    """A heading or title."""

    category = "Title"


class Address(Text):
    category = "Address"


class EmailAddress(Text):
    category = "EmailAddress"


class Image(Text):
    category = "Image"


class PageBreak(Text):
    category = "PageBreak"


class Table(Text):
    category = "Table"


class TableChunk(Table):
    """A piece of a table that was too large for one chunk."""

    category = "TableChunk"


class Header(Text):
    category = "Header"


class Footer(Text):
    category = "Footer"


class CodeSnippet(Text):
    category = "CodeSnippet"


class PageNumber(Text):
    category = "PageNumber"


class FormKeysValues(Text):
    category = "FormKeysValues"


class DocumentData(Text):
    category = "DocumentData"


TYPE_TO_TEXT_ELEMENT_MAP: dict[str, type[Text]] = {
    ElementType.TITLE: Title,
    ElementType.SECTION_HEADER: Title,
    ElementType.HEADLINE: Title,
    ElementType.SUB_HEADLINE: Title,
    ElementType.FIELD_NAME: Title,
    ElementType.UNCATEGORIZED_TEXT: Text,
    ElementType.COMPOSITE_ELEMENT: CompositeElement,
    ElementType.TEXT: NarrativeText,
    ElementType.NARRATIVE_TEXT: NarrativeText,
    ElementType.PARAGRAPH: NarrativeText,
    ElementType.ABSTRACT: NarrativeText,
    ElementType.THREADING: NarrativeText,
    ElementType.FORM: Form,
    ElementType.VALUE: NarrativeText,
    ElementType.LINK: NarrativeText,
    ElementType.LIST_ITEM: ListItem,
    ElementType.BULLETED_TEXT: ListItem,
    ElementType.LIST_ITEM_OTHER: ListItem,
    ElementType.HEADER: Header,
    ElementType.PAGE_HEADER: Header,
    ElementType.FOOTER: Footer,
    ElementType.PAGE_FOOTER: Footer,
    ElementType.FOOTNOTE: Footer,
    ElementType.FIGURE_CAPTION: FigureCaption,
    ElementType.CAPTION: FigureCaption,
    ElementType.IMAGE: Image,
    ElementType.FIGURE: Image,
    ElementType.PICTURE: Image,
    ElementType.TABLE: Table,
    ElementType.ADDRESS: Address,
    ElementType.EMAIL_ADDRESS: EmailAddress,
    ElementType.FORMULA: Formula,
    ElementType.PAGE_BREAK: PageBreak,
    ElementType.CODE_SNIPPET: CodeSnippet,
    ElementType.PAGE_NUMBER: PageNumber,
    ElementType.FORM_KEYS_VALUES: FormKeysValues,
    ElementType.DOCUMENT_DATA: DocumentData,
}


def assign_and_map_hash_ids(elements: Iterable[Element]) -> list[Element]:
    """Give each element a deterministic hash id and remap ``parent_id`` references to match."""
    elements = list(elements)
    seq_by_page: dict[Optional[int], int] = {}
    id_map: dict[str, str] = {}
    for element in elements:
        page = element.metadata.page_number
        seq = seq_by_page.get(page, 0)
        old_id = element.id
        id_map[old_id] = element.id_to_hash(seq)
        seq_by_page[page] = seq + 1
    for element in elements:
        parent = element.metadata.parent_id
        if parent is not None and parent in id_map:
            element.metadata.parent_id = id_map[parent]
    return elements

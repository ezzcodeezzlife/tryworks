from __future__ import annotations

import hashlib
import json

import pytest

from tryworks.documents.coordinates import PixelSpace, PointSpace
from tryworks.documents.elements import (
    CheckBox,
    CompositeElement,
    ElementMetadata,
    Image,
    NarrativeText,
    Table,
    Text,
    Title,
    assign_and_map_hash_ids,
)
from tryworks.staging import base as staging


class DescribeElementMetadata:
    def it_is_sparse_and_splits_the_filename(self):
        meta = ElementMetadata(filename="docs/q3/report.pdf", page_number=2, languages=[])
        assert meta.filename == "report.pdf"
        assert meta.file_directory == "docs/q3"
        assert meta.subject is None
        assert meta.to_dict() == {"file_directory": "docs/q3", "filename": "report.pdf", "page_number": 2}

    def it_removes_a_field_assigned_none(self):
        meta = ElementMetadata(page_number=3)
        meta.page_number = None
        assert meta.fields == {}

    def it_rejects_unknown_constructor_fields_but_accepts_ad_hoc_attributes(self):
        with pytest.raises(TypeError):
            ElementMetadata(pages=3)
        meta = ElementMetadata()
        meta.source_system = "crm"
        assert meta.to_dict() == {"source_system": "crm"}
        with pytest.raises(AttributeError):
            meta.never_set  # noqa: B018

    def it_does_not_serialize_debug_fields(self):
        meta = ElementMetadata(detection_origin="docx", page_number=1)
        assert meta.to_dict() == {"page_number": 1}

    def it_updates_like_a_dict(self):
        meta = ElementMetadata(page_number=1, filetype="text/html")
        meta.update(ElementMetadata(page_number=2, subject="Hi"))
        assert meta.to_dict() == {"filetype": "text/html", "page_number": 2, "subject": "Hi"}


class DescribeElement:
    def it_serializes_to_the_unstructured_shape(self):
        assert Title("Q3 Results", element_id="abc").to_dict() == {
            "type": "Title",
            "element_id": "abc",
            "text": "Q3 Results",
            "metadata": {},
        }
        assert Text("x").category == "UncategorizedText"

    def it_hashes_ids_from_filename_text_page_and_sequence(self):
        element = NarrativeText("Hello", metadata=ElementMetadata(filename="a.txt", page_number=1))
        expected = hashlib.sha256(b"a.txtHello10").hexdigest()[:32]
        assert element.id_to_hash(0) == expected
        assert element.id == expected

    def it_remaps_parent_ids_when_hashing(self):
        title = Title("Heading")
        body = NarrativeText("Body text here.", metadata=ElementMetadata(parent_id=title.id))
        assign_and_map_hash_ids([title, body])
        assert len(title.id) == 32
        assert body.metadata.parent_id == title.id

    def it_numbers_sequence_per_page(self):
        a = Text("same", metadata=ElementMetadata(page_number=1))
        b = Text("same", metadata=ElementMetadata(page_number=2))
        assign_and_map_hash_ids([a, b])
        # -- first element on each page gets sequence number 0, so ids differ only by page --
        assert a.id == hashlib.sha256(b"Nonesame10").hexdigest()[:32]
        assert b.id == hashlib.sha256(b"Nonesame20").hexdigest()[:32]

    def it_applies_cleaners(self):
        element = NarrativeText("  lots   of   space ")
        element.apply(str.strip, lambda s: " ".join(s.split()))
        assert element.text == "lots of space"
        with pytest.raises(ValueError):
            element.apply(lambda s: 42)

    def it_converts_coordinates(self):
        element = Text("x", coordinates=((10.0, 20.0),), coordinate_system=PixelSpace(100, 200))
        points = element.convert_coordinates_to_new_system(PointSpace(100, 200))
        assert points == ((10.0, 180.0),)


class DescribeStaging:
    def it_round_trips_elements_through_json(self):
        elements = [
            Title("Inventory", metadata=ElementMetadata(filename="inv.xlsx", page_number=1)),
            Table(
                "a b",
                metadata=ElementMetadata(text_as_html="<table><tr><td>a</td><td>b</td></tr></table>"),
                coordinates=((1.234, 2.345), (3.0, 4.0)),
                coordinate_system=PixelSpace(612, 792),
            ),
            Image("A crane", metadata=ElementMetadata(image_url="https://example.com/c.png")),
            CheckBox(checked=True, element_id="cb1"),
        ]
        restored = staging.elements_from_json(text=staging.elements_to_json(elements))
        assert [type(e) for e in restored] == [Title, Table, Image, CheckBox]
        assert restored[0].metadata.filename == "inv.xlsx"
        assert restored[1].metadata.text_as_html.startswith("<table>")
        assert restored[1].metadata.coordinates.points == ((1.2, 2.3), (3.0, 4.0))
        assert isinstance(restored[1].metadata.coordinates.system, PixelSpace)
        assert restored[3].checked is True

    def it_writes_sorted_indented_json(self):
        out = json.loads(staging.elements_to_json([Title("T", element_id="x")]))
        assert out == [{"element_id": "x", "metadata": {}, "text": "T", "type": "Title"}]

    def it_round_trips_orig_elements(self):
        parts = [Title("A"), NarrativeText("Some text goes here.")]
        chunk = CompositeElement("A\n\nSome text goes here.", metadata=ElementMetadata(orig_elements=parts))
        encoded = chunk.to_dict()["metadata"]["orig_elements"]
        assert isinstance(encoded, str)
        assert [e.text for e in staging.elements_from_base64_gzipped_json(encoded)] == ["A", "Some text goes here."]

    def it_refuses_to_inflate_oversized_orig_elements(self, monkeypatch):
        encoded = staging.elements_to_base64_gzipped_json([NarrativeText("x" * 5000)])
        monkeypatch.setattr(staging, "MAX_DECOMPRESSED_SIZE", 100)
        with pytest.raises(staging.DecompressedSizeExceededError):
            staging.elements_from_base64_gzipped_json(encoded)

    def it_renders_text_markdown_and_csv(self):
        elements = [
            Title("Guide"),
            NarrativeText("Read this first."),
            Table("a", metadata=ElementMetadata(text_as_html="<table><tr><td>a</td></tr></table>")),
        ]
        assert staging.convert_to_text(elements) == "Guide\nRead this first.\na"
        assert staging.elements_to_md(elements) == "# Guide\nRead this first.\n<table><tr><td>a</td></tr></table>"
        assert staging.convert_to_csv(elements).splitlines()[0] == "type,text,element_id,filename,page_number,url,sender,subject"

    def it_filters_by_element_type(self):
        elements = [Title("T"), NarrativeText("N"), Table("x")]
        assert [e.text for e in staging.filter_element_types(elements, include_element_types=[Title, Table])] == ["T", "x"]
        assert [e.text for e in staging.filter_element_types(elements, exclude_element_types=[Title])] == ["N", "x"]
        with pytest.raises(ValueError):
            staging.filter_element_types(elements)

    def it_flattens_dicts(self):
        assert staging.flatten_dict({"a": {"b": 1, "c": [1, 2]}}, flatten_lists=True) == {"a_b": 1, "a_c_0": 1, "a_c_1": 2}

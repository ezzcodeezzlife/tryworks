from __future__ import annotations

import pytest

from tryworks.chunking import chunk
from tryworks.chunking.basic import chunk_elements
from tryworks.chunking.title import chunk_by_title
from tryworks.documents.elements import (
    CompositeElement,
    ElementMetadata,
    NarrativeText,
    Table,
    TableChunk,
    Title,
)
from tryworks.partition.auto import partition


def _table(rows: int) -> Table:
    cells = [(f"row {i}", f"value {i * 7}") for i in range(rows)]
    html = "<table>" + "".join(f"<tr><td>{a}</td><td>{b}</td></tr>" for a, b in cells) + "</table>"
    return Table(" ".join(f"{a} {b}" for a, b in cells), metadata=ElementMetadata(text_as_html=html))


class DescribeChunkByTitle:
    def it_starts_a_chunk_at_each_title(self):
        elements = [Title("Intro"), NarrativeText("a" * 100), Title("Details"), NarrativeText("b" * 100)]
        chunks = chunk_by_title(elements, combine_text_under_n_chars=0)
        assert [c.text for c in chunks] == ["Intro\n\n" + "a" * 100, "Details\n\n" + "b" * 100]
        assert all(isinstance(c, CompositeElement) for c in chunks)

    def it_combines_small_sections_by_default(self):
        elements = [Title("Intro"), NarrativeText("a" * 100), Title("Details"), NarrativeText("b" * 100)]
        (only,) = chunk_by_title(elements)
        assert only.text == "Intro\n\n" + "a" * 100 + "\n\nDetails\n\n" + "b" * 100

    def it_never_exceeds_max_characters(self):
        elements = [Title("Long"), NarrativeText("word " * 300)]
        chunks = chunk_by_title(elements, max_characters=200)
        assert len(chunks) > 5
        assert all(len(c.text) <= 200 for c in chunks)
        # -- the title does not fit beside the long paragraph, so it is a chunk of its own; only
        # -- the second and later pieces of the split paragraph are continuations --
        assert chunks[0].text == "Long"
        assert chunks[1].metadata.is_continuation is None
        assert all(c.metadata.is_continuation for c in chunks[2:])

    def it_closes_chunks_early_after_new_after_n_chars(self):
        elements = [NarrativeText("x" * 60) for _ in range(4)]
        chunks = chunk_by_title(elements, max_characters=500, new_after_n_chars=100, combine_text_under_n_chars=0)
        assert [len(c.text) for c in chunks] == [122, 122]

    def it_gives_tables_their_own_chunk(self):
        elements = [NarrativeText("before the table"), _table(2), NarrativeText("after the table")]
        chunks = chunk_by_title(elements)
        assert [type(c) for c in chunks] == [CompositeElement, Table, CompositeElement]
        assert chunks[1].metadata.text_as_html.startswith("<table>")

    def it_splits_oversized_tables_by_row(self):
        table = _table(40)
        chunks = chunk_by_title([table], max_characters=200)
        assert len(chunks) > 3 and all(isinstance(c, TableChunk) for c in chunks)
        assert [c.metadata.chunk_index for c in chunks] == list(range(len(chunks)))
        assert {c.metadata.table_id for c in chunks} == {table.id}
        assert all(c.metadata.text_as_html.startswith("<table><tr>") and len(c.metadata.text_as_html) <= 200 for c in chunks)
        assert " ".join(c.text for c in chunks) == table.text
        assert chunks[1].metadata.is_continuation is True

    def it_can_break_sections_at_page_changes(self):
        elements = [
            NarrativeText("first page text", metadata=ElementMetadata(page_number=1)),
            NarrativeText("second page text", metadata=ElementMetadata(page_number=2)),
        ]
        assert len(chunk_by_title(elements, multipage_sections=False, combine_text_under_n_chars=0)) == 2
        assert len(chunk_by_title(elements)) == 1

    def it_overlaps_split_chunks(self):
        text = " ".join(f"w{i:03d}" for i in range(200))
        chunks = chunk_by_title([NarrativeText(text)], max_characters=100, overlap=20)
        first_tail = chunks[0].text[-15:]
        assert first_tail.split()[-1] in chunks[1].text.split()[:4]

    def it_keeps_orig_elements_and_merges_metadata(self):
        elements = [
            Title("T", metadata=ElementMetadata(page_number=4, languages=["eng"], link_urls=["a"])),
            NarrativeText("body", metadata=ElementMetadata(page_number=5, languages=["deu"], link_urls=["b"], category_depth=2)),
        ]
        (only,) = chunk_by_title(elements)
        meta = only.metadata
        assert (meta.page_number, meta.languages, meta.link_urls, meta.category_depth) == (4, ["eng", "deu"], ["a", "b"], None)
        assert [e.text for e in meta.orig_elements] == ["T", "body"]
        assert isinstance(only.to_dict()["metadata"]["orig_elements"], str)
        (bare,) = chunk_by_title(elements, include_orig_elements=False)
        assert bare.metadata.orig_elements is None

    @pytest.mark.parametrize(
        ("kwargs", "error"),
        [
            ({"max_characters": 0}, ValueError),
            ({"max_characters": 100, "overlap": 100}, ValueError),
            ({"max_characters": 100, "combine_text_under_n_chars": 200}, ValueError),
            ({"max_tokens": 50}, NotImplementedError),
        ],
    )
    def it_validates_options(self, kwargs, error):
        with pytest.raises(error):
            chunk_by_title([NarrativeText("x")], **kwargs)


class DescribeBasicChunking:
    def it_ignores_section_boundaries(self):
        elements = [Title("Intro"), NarrativeText("a" * 100), Title("Details"), NarrativeText("b" * 100)]
        (only,) = chunk_elements(elements)
        assert only.text.count("\n\n") == 3

    def it_is_available_by_name(self):
        elements = [Title("Intro"), NarrativeText("text")]
        assert chunk(elements, "basic")[0].text == "Intro\n\ntext"
        with pytest.raises(ValueError):
            chunk(elements, "by_page")


def test_partition_can_chunk_directly(corpus):
    chunks = partition(filename=str(corpus["docx"]), chunking_strategy="by_title", max_characters=300)
    assert chunks and all(isinstance(c, (CompositeElement, Table, TableChunk)) for c in chunks)
    assert all(len(c.text) <= 300 for c in chunks)
    assert chunks[0].metadata.filename == "memo.docx"

from __future__ import annotations

import io

import pytest

from conftest import kinds
from tryworks.documents.coordinates import PixelSpace
from tryworks.documents.elements import Footer, Header, PageBreak, Table, Title
from tryworks.partition.csv import partition_csv, partition_tsv
from tryworks.partition.docx import partition_docx
from tryworks.partition.email import partition_email
from tryworks.partition.html import partition_html
from tryworks.partition.json import partition_json
from tryworks.partition.md import markdown_to_html, partition_md
from tryworks.partition.pptx import partition_pptx
from tryworks.partition.text import partition_text
from tryworks.partition.xlsx import partition_xlsx
from tryworks.staging.base import elements_to_json


def _no_breaks(elements):
    return [e for e in elements if not isinstance(e, PageBreak)]


class DescribeHtml:
    def it_maps_structure_to_elements(self, corpus):
        elements = partition_html(filename=str(corpus["html"]))
        assert kinds(elements) == [
            ("Title", "Harbor Operations Update"),
            (
                "NarrativeText",
                "The harbor authority extended night shifts after a surge in container traffic. "
                "Crews now work until two in the morning on weekdays.",
            ),
            ("Title", "What changed"),
            ("ListItem", "Night shifts run five days a week"),
            ("ListItem", "Two cranes were added"),
            ("ListItem", "One arrives in October"),
            ("Table", "Terminal Moves per hour North 31 South 27"),
            ("CodeSnippet", "berth_schedule --week 37\n  north: 14 vessels"),
            ("NarrativeText", "Read the full notice for shift details."),
            ("Image", "New cranes at the north terminal"),
            ("NarrativeText", "Published by the harbor authority press office."),
        ]

    def it_records_depth_emphasis_links_and_tables(self, corpus):
        elements = partition_html(filename=str(corpus["html"]))
        by_text = {e.text: e for e in elements}
        assert by_text["What changed"].metadata.category_depth == 1
        assert by_text["One arrives in October"].metadata.category_depth == 2
        para = elements[1].metadata
        assert para.emphasized_text_contents == ["extended", "two in the morning"]
        assert para.emphasized_text_tags == ["b", "i"]
        link = by_text["Read the full notice for shift details."].metadata
        assert (link.link_texts, link.link_urls) == (["full notice"], ["https://example.com/notice"])
        assert elements[6].metadata.text_as_html == (
            "<table><tr><td>Terminal</td><td>Moves per hour</td></tr>"
            "<tr><td>North</td><td>31</td></tr><tr><td>South</td><td>27</td></tr></table>"
        )
        assert by_text["New cranes at the north terminal"].metadata.image_url == "https://example.com/cranes.png"
        assert all(e.metadata.filetype == "text/html" and e.metadata.filename == "harbor.html" for e in elements)
        assert by_text["What changed"].metadata.parent_id == elements[0].id

    def it_can_skip_headers_and_footers(self, corpus):
        elements = partition_html(filename=str(corpus["html"]), skip_headers_and_footers=True)
        assert "Published by the harbor authority press office." not in [e.text for e in elements]

    def it_handles_fragments_and_sloppy_markup(self):
        elements = partition_html(text="<p>First paragraph has a verb in it<p>Second <b>item <i>label</b> text</i><li>stray item")
        assert kinds(elements) == [
            ("NarrativeText", "First paragraph has a verb in it"),
            ("UncategorizedText", "Second item label text"),
            ("ListItem", "stray item"),
        ]
        assert elements[1].metadata.emphasized_text_contents == ["item", "label"]

    def it_decodes_declared_charsets(self):
        html = "<html><head><meta charset='windows-1252'></head><body><p>Caf\xe9 prices rose this week.</p></body></html>"
        elements = partition_html(file=io.BytesIO(html.encode("cp1252")))
        assert elements[0].text == "Café prices rose this week."

    def it_returns_nothing_for_empty_input(self):
        assert partition_html(text="   ") == []

    def it_only_fetches_http_urls(self):
        with pytest.raises(ValueError):
            partition_html(url="file:///etc/passwd")


class DescribeMarkdown:
    def it_partitions_like_rendered_html(self, corpus):
        elements = partition_md(filename=str(corpus["md"]))
        assert kinds(elements) == [
            ("Title", "Onboarding Guide"),
            ("NarrativeText", "Welcome to the procurement team. This guide explains how purchase orders move through approval."),
            ("Title", "Approval steps"),
            ("ListItem", "Submit the order in the purchasing portal"),
            ("ListItem", "Your manager approves orders under ten thousand euros"),
            ("ListItem", "Finance reviews anything larger"),
            ("ListItem", "Keep receipts for every order"),
            ("ListItem", "Tag urgent orders with priority"),
            ("Table", "Amount Approver Under 10k Manager 10k and over Finance"),
            ("CodeSnippet", "po submit --vendor 42 --amount 9800"),
            ("NarrativeText", "Questions go to the procurement desk."),
        ]
        assert all(e.metadata.filetype == "text/markdown" for e in elements)
        assert elements[-1].metadata.link_urls == ["https://example.com/desk"]

    def it_renders_common_markdown(self):
        rendered = markdown_to_html("Setext\n======\n\n> quoted *text*\n\n    indented code\n\nA `code <b>` span\n---\n")
        assert "<h1>Setext</h1>" in rendered
        assert "<blockquote>" in rendered and "<em>text</em>" in rendered
        assert "<pre><code>indented code\n</code></pre>" in rendered
        assert "<code>code &lt;b&gt;</code>" in rendered


class DescribeText:
    def it_classifies_paragraphs(self, corpus):
        elements = partition_text(filename=str(corpus["txt"]))
        assert kinds(elements) == [
            ("Title", "SHIPPING POLICY"),
            ("NarrativeText", "Orders placed before noon ship the same business day. Orders placed after noon ship the next business day."),
            ("ListItem", "Standard delivery takes three to five days"),
            ("ListItem", "Express delivery takes one to two days"),
            ("NarrativeText", "Returns are accepted within thirty days of delivery if the item is unused and in its original packaging."),
            ("EmailAddress", "support@example.com"),
            ("Address", "Doylestown, PA 18901"),
        ]
        assert elements[1].metadata.parent_id == elements[0].id
        assert elements[0].metadata.languages == ["eng"]

    def it_accepts_strings_files_and_custom_groupers(self):
        assert kinds(partition_text(text="one line here\nsecond line here", paragraph_grouper=False)) == [
            ("Title", "one line here\nsecond line here")
        ]
        assert [e.text for e in partition_text(text="one line here\nsecond line here")] == [
            "one line here",
            "second line here",
        ]
        elements = partition_text(file=io.BytesIO("Caf\xe9 notes".encode("cp1252")))
        assert elements[0].text == "Café notes"
        assert partition_text(text="") == []

    def it_detects_other_languages(self):
        elements = partition_text(text="Die Lieferung kommt am Montag und die Rechnung ist schon bezahlt worden.")
        assert elements[0].metadata.languages == ["deu"]


class DescribeCsv:
    def it_makes_one_table(self, corpus):
        (table,) = partition_csv(filename=str(corpus["csv"]))
        assert isinstance(table, Table)
        assert table.text.startswith("Vessel Port Arrival Tons Marlin Rotterdam 2026-08-01 42.5")
        assert "<td>Albatross, II</td>" in table.metadata.text_as_html
        assert table.metadata.filetype == "text/csv"

    def it_detects_semicolons_and_reads_tsv(self):
        (table,) = partition_csv(file=io.BytesIO(b"a;b\n1;2\n"))
        assert table.metadata.text_as_html == "<table><tr><td>a</td><td>b</td></tr><tr><td>1</td><td>2</td></tr></table>"
        (tsv,) = partition_tsv(file=io.BytesIO(b"a\tb\n0042\tx\n"))
        assert tsv.text == "a b 0042 x"


class DescribeEmail:
    def it_partitions_body_and_attachments(self, corpus):
        elements = partition_email(filename=str(corpus["eml"]))
        assert kinds(elements) == [
            ("UncategorizedText", "Hi team,"),
            ("NarrativeText", "The dry dock slot for the Marlin moved to the second week of October."),
            ("UncategorizedText", "Thanks, Dana"),
            ("Title", "Dry dock checklist"),
            ("NarrativeText", "Drain the ballast tanks before the vessel enters the dock."),
        ]
        meta = elements[1].metadata
        assert meta.sent_from == ["Dana Whitfield <dana@example.com>"]
        assert meta.sent_to == ["ops@example.com", "Lee Park <lee@example.com>"]
        assert meta.cc_recipient == ["finance@example.com"]
        assert meta.subject == "Dry dock schedule for October"
        assert meta.email_message_id == "schedule-1234@example.com"
        assert meta.last_modified == "2026-09-08T07:30:00+00:00"
        assert meta.filetype == "message/rfc822"
        attachment = elements[3].metadata
        assert attachment.filename == "checklist.txt"
        assert attachment.attached_to_filename == "schedule.eml"
        assert attachment.last_modified == meta.last_modified
        # -- attachment ids come from the attachment's own pass: its filename, sequence from 0 --
        import hashlib

        assert elements[3].id == hashlib.sha256(b"checklist.txtDry dock checklistNone0").hexdigest()[:32]
        assert elements[4].metadata.parent_id == elements[3].id

    def it_can_prefer_plain_text_and_skip_attachments(self, corpus):
        elements = partition_email(filename=str(corpus["eml"]), content_source="text/plain", process_attachments=False)
        assert [e.text for e in elements] == [
            "Hi team,",
            "The dry dock slot for the Marlin moved to the second week of October.",
            "Thanks,",
            "Dana",
        ]
        with pytest.raises(ValueError):
            partition_email(filename=str(corpus["eml"]), content_source="text/rtf")


class DescribeJson:
    def it_reloads_serialized_elements(self, corpus):
        original = partition_text(filename=str(corpus["txt"]))
        restored = partition_json(text=elements_to_json(original))
        assert kinds(restored) == kinds(original)
        with pytest.raises(ValueError):
            partition_json(text='{"not": "elements"}')


class DescribeDocx:
    def it_follows_styles_sections_and_page_breaks(self, corpus):
        elements = partition_docx(filename=str(corpus["docx"]))
        assert kinds(elements) == [
            ("Header", "Keel & Rope Supply Co. | Board memo"),
            ("Title", "Quarterly Operations Memo"),
            ("Title", "Summary"),
            ("NarrativeText", "Revenue grew eleven percent year over year, driven by marine hardware sales across the northern ports."),
            ("NarrativeText", "The full figures are in the third quarter report that finance published last week."),
            ("Title", "Priorities"),
            ("ListItem", "Expand the Rotterdam warehouse"),
            ("ListItem", "Hire two procurement analysts"),
            ("ListItem", "Close the third quarter audit"),
            ("Table", "Warehouse totals Units Rotterdam KR-1001 1200 Antwerp KR-2040 860"),
            ("PageBreak", ""),
            ("NarrativeText", "The outlook for the fourth quarter remains positive despite higher fuel costs."),
            ("Title", "Contacts"),
            ("EmailAddress", "operations@example.com"),
            ("Footer", "Internal distribution only"),
        ]

    def it_records_formatting_links_depth_and_merged_cells(self, corpus):
        elements = partition_docx(filename=str(corpus["docx"]))
        by_text = {e.text: e for e in elements}
        assert by_text["Priorities"].metadata.category_depth == 1
        revenue = elements[3].metadata
        assert revenue.emphasized_text_contents == ["eleven percent", "marine hardware"]
        assert revenue.emphasized_text_tags == ["b", "i"]
        link = elements[4].metadata
        assert link.link_urls == ["https://example.com/q3-report"]
        assert link.links == [{"text": "third quarter report", "url": "https://example.com/q3-report", "start_index": 28}]
        table = elements[9].metadata
        assert table.text_as_html.startswith('<table><tr><td colspan="2">Warehouse totals</td><td>Units</td></tr>')
        assert [e.metadata.page_number for e in elements if e.category == "Title"] == [1, 1, 1, 2]
        assert elements[0].metadata.header_footer_type == "primary"

    def it_reads_file_objects_and_omits_page_breaks_on_request(self, corpus):
        with open(corpus["docx"], "rb") as fp:
            elements = partition_docx(file=fp, metadata_filename="board.docx", include_page_breaks=False)
        assert not any(isinstance(e, PageBreak) for e in elements)
        assert elements[1].metadata.filename == "board.docx"

    def it_rejects_non_zip_input(self):
        with pytest.raises(ValueError):
            partition_docx(file=io.BytesIO(b"not a docx"))


class DescribePptx:
    def it_reads_slides_in_order(self, corpus):
        elements = partition_pptx(filename=str(corpus["pptx"]))
        assert kinds(_no_breaks(elements)) == [
            ("Title", "Fleet Maintenance Review"),
            ("NarrativeText", "Prepared by the operations team for the September planning cycle"),
            ("Title", "Priorities"),
            ("ListItem", "Replace worn rigging on three vessels"),
            ("ListItem", "Schedule dry dock inspections"),
            ("NarrativeText", "The inspection backlog has grown since March and now affects twelve vessels."),
            ("Title", "Budget"),
            ("Table", "Item Cost Rigging $42,000 Dry dock $118,500"),
        ]
        assert sum(isinstance(e, PageBreak) for e in elements) == 2
        assert [e.metadata.page_number for e in _no_breaks(elements)] == [1, 1, 2, 2, 2, 2, 3, 3]
        assert elements[5].metadata.category_depth == 1

    def it_includes_notes_on_request(self, corpus):
        elements = partition_pptx(filename=str(corpus["pptx"]), include_slide_notes=True, include_page_breaks=False)
        assert elements[-1].text == "Mention that the dry dock quote expires in October."
        assert elements[-1].metadata.page_number == 3


class DescribeXlsx:
    def it_splits_sheets_into_subtables(self, corpus):
        elements = partition_xlsx(filename=str(corpus["xlsx"]))
        assert kinds(elements) == [
            ("Title", "Inventory by warehouse"),
            ("Table", "Warehouse SKU Units Rotterdam KR-1001 1200 Antwerp KR-2040 860 Hamburg KR-3300 415"),
            ("NarrativeText", "Counts reflect the August cycle count."),
            ("Table", "Date Vessel Tons 2026-08-01 00:00:00 Marlin 42.5 2026-08-03 00:00:00 Petrel 38"),
        ]
        assert elements[3].metadata.parent_id == elements[0].id
        assert [(e.metadata.page_number, e.metadata.page_name) for e in elements] == [
            (1, "Inventory"), (1, "Inventory"), (1, "Inventory"), (2, "Shipments"),
        ]

    def it_can_keep_each_sheet_whole(self, corpus):
        elements = partition_xlsx(filename=str(corpus["xlsx"]), find_subtable=False)
        assert [e.category for e in elements] == ["Table", "Table"]
        assert elements[0].text.startswith("Inventory by warehouse Warehouse SKU Units")


class DescribePdf:
    @pytest.fixture(autouse=True)
    def _needs_pdfium(self):
        pytest.importorskip("pypdfium2")

    def it_groups_lines_into_blocks_in_reading_order(self, corpus):
        from tryworks.partition.pdf import partition_pdf

        elements = partition_pdf(filename=str(corpus["pdf"]))
        assert kinds(elements) == [
            ("Header", "Keel & Rope Supply Co. | Operations Report"),
            ("Title", "Operations Report"),
            (
                "NarrativeText",
                "The third quarter closed with record throughput at the Rotterdam warehouse. Order volume rose "
                "eleven percent while fulfilment times fell to under two days for most customers.",
            ),
            ("ListItem", "Rotterdam throughput up eleven percent"),
            ("ListItem", "Two new procurement analysts hired"),
            ("Title", "Next Steps"),
            ("NarrativeText", "The board approved funding for a second warehouse in Antwerp. Construction is expected to begin in the spring."),
            ("Footer", "Page 1 of 2"),
            ("Title", "Regional Detail"),
            ("NarrativeText", "Northern revenue grew steadily through the quarter as contracts with ferry operators came online in July."),
            ("NarrativeText", "Southern margins improved after the team renegotiated fuel surcharges with three of its largest shipping partners."),
            ("Footer", "Page 2 of 2"),
        ]

    def it_sets_pages_and_coordinates(self, corpus):
        from tryworks.partition.pdf import partition_pdf

        elements = partition_pdf(filename=str(corpus["pdf"]), include_page_breaks=True)
        assert sum(isinstance(e, PageBreak) for e in elements) == 1
        title = next(e for e in elements if isinstance(e, Title))
        coords = title.metadata.coordinates
        assert isinstance(coords.system, PixelSpace) and coords.system.width == 612
        (x0, y0), _, (x1, y1), _ = coords.points
        assert 60 < x0 < x1 and 60 < y0 < y1 < 100
        assert isinstance(elements[0], Header) and isinstance(elements[-1], Footer)
        assert elements[-1].metadata.page_number == 2

    def it_refuses_ocr_strategies_and_warns_on_scans(self, corpus):
        from corpus import build_pdf
        from tryworks.partition.pdf import partition_pdf

        with pytest.raises(NotImplementedError):
            partition_pdf(filename=str(corpus["pdf"]), strategy="hi_res")
        with pytest.warns(UserWarning, match="no embedded text|No embedded text"):
            assert partition_pdf(file=io.BytesIO(build_pdf([[]]))) == []
        with pytest.raises(ValueError):
            partition_pdf(file=io.BytesIO(b"%PDF-1.4 broken"))

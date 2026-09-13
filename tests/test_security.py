"""Hostile-input handling: zip bombs, entity expansion, resource limits."""

from __future__ import annotations

import io
import zipfile

import pytest

from tryworks.partition._ooxml import UnsafeDocumentError
from tryworks.partition.docx import partition_docx
from tryworks.partition.html import partition_html
from tryworks.partition.xlsx import partition_xlsx


def _rewrite_zip(data: bytes, replace: dict[str, bytes] | None = None, add: dict[str, bytes] | None = None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            payload = (replace or {}).get(info.filename, src.read(info.filename))
            dst.writestr(info.filename, payload)
        for name, payload in (add or {}).items():
            dst.writestr(name, payload)
    return out.getvalue()


def test_rejects_entity_declarations(corpus):
    original = corpus["docx"].read_bytes()
    with zipfile.ZipFile(io.BytesIO(original)) as zf:
        document = zf.read("word/document.xml")
    laughs = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>'
    hostile = _rewrite_zip(original, replace={"word/document.xml": laughs + document.split(b"?>", 1)[1]})
    with pytest.raises(UnsafeDocumentError, match="DTD"):
        partition_docx(file=io.BytesIO(hostile))


def test_rejects_highly_compressed_parts(corpus):
    bomb = _rewrite_zip(corpus["docx"].read_bytes(), add={"word/media/padding.bin": b"\0" * (4 << 20)})
    with pytest.raises(UnsafeDocumentError, match="compression ratio"):
        partition_docx(file=io.BytesIO(bomb))


def test_part_size_limit_is_configurable(corpus, monkeypatch):
    monkeypatch.setenv("TRYWORKS_MAX_PART_BYTES", "1000")
    with pytest.raises(UnsafeDocumentError, match="TRYWORKS_MAX_PART_BYTES"):
        partition_docx(filename=str(corpus["docx"]))


def test_sheet_cell_limit(corpus, monkeypatch):
    monkeypatch.setenv("TRYWORKS_MAX_SHEET_CELLS", "5")
    with pytest.raises(UnsafeDocumentError, match="TRYWORKS_MAX_SHEET_CELLS"):
        partition_xlsx(filename=str(corpus["xlsx"]))


def test_pdf_page_limit(corpus, monkeypatch):
    pytest.importorskip("pypdfium2")
    from tryworks.partition.pdf import partition_pdf

    monkeypatch.setenv("TRYWORKS_MAX_PDF_PAGES", "1")
    with pytest.raises(ValueError, match="TRYWORKS_MAX_PDF_PAGES"):
        partition_pdf(filename=str(corpus["pdf"]))


def test_script_style_and_template_content_never_becomes_text():
    html = (
        "<html><head><title>Title text</title><style>.x{color:red}</style></head><body>"
        "<script>alert('secret token')</script><template><p>hidden template</p></template>"
        "<noscript>enable javascript</noscript><p>Visible paragraph is shown here.</p></body></html>"
    )
    assert [e.text for e in partition_html(text=html)] == ["Visible paragraph is shown here."]


def test_deeply_nested_html_does_not_overflow():
    html = "<div>" * 5000 + "<p>Deep text still gets extracted.</p>" + "</div>" * 5000
    assert [e.text for e in partition_html(text=html)] == ["Deep text still gets extracted."]

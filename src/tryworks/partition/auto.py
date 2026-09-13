"""Detect a document's type and route it to the matching partitioner."""

from __future__ import annotations

import io
import os
import urllib.request
import zipfile
from typing import IO, Any, Callable, Optional

from tryworks.documents.elements import Element

EXTENSION_TO_TYPE = {
    ".csv": "csv", ".tsv": "tsv", ".tab": "tsv", ".docx": "docx", ".eml": "eml", ".htm": "html",
    ".html": "html", ".xhtml": "html", ".json": "json", ".md": "md", ".markdown": "md",
    ".pdf": "pdf", ".pptx": "pptx", ".txt": "txt", ".text": "txt", ".log": "txt", ".xlsx": "xlsx",
}
MIME_TO_TYPE = {
    "text/csv": "csv", "application/csv": "csv", "text/tsv": "tsv", "text/tab-separated-values": "tsv",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "message/rfc822": "eml", "text/html": "html", "application/xhtml+xml": "html",
    "application/json": "json", "text/markdown": "md", "text/x-markdown": "md",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/plain": "txt",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
}
_UNSUPPORTED_HINTS = {
    ".doc": "Convert legacy .doc files to .docx (e.g. with LibreOffice) first.",
    ".ppt": "Convert legacy .ppt files to .pptx first.",
    ".xls": "Convert legacy .xls files to .xlsx first.",
    ".msg": "Outlook .msg files are not supported; export the message as .eml.",
    ".rtf": "RTF is not supported; convert to .docx or .html first.",
    ".odt": "OpenDocument text is not supported; convert to .docx first.",
    ".epub": "EPUB is not supported; convert to .html first.",
    ".png": "Images need OCR, which tryworks does not do.",
    ".jpg": "Images need OCR, which tryworks does not do.",
    ".jpeg": "Images need OCR, which tryworks does not do.",
    ".tiff": "Images need OCR, which tryworks does not do.",
    ".heic": "Images need OCR, which tryworks does not do.",
}


class UnsupportedFileFormatError(ValueError):
    """The document type is not one tryworks can partition."""


def _sniff_type(head: bytes) -> Optional[str]:
    """Guess the type from the first bytes of the content."""
    stripped = head.lstrip()
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    lower = stripped[:512].lower()
    if lower.startswith((b"<!doctype html", b"<html")) or b"<body" in lower or b"<head" in lower:
        return "html"
    if stripped[:1] in (b"[", b"{"):
        return "json"
    first_lines = head[:2048].split(b"\n", 12)
    header_names = (b"from:", b"received:", b"mime-version:", b"return-path:", b"message-id:", b"delivered-to:")
    if sum(1 for line in first_lines if line.lower().startswith(header_names)) >= 2:
        return "eml"
    return "txt" if _looks_like_text(head[:4096]) else None


_TEXT_CONTROL_BYTES = frozenset(b"\t\n\r\f\b")


def _looks_like_text(sample: bytes) -> bool:
    """No NUL bytes, few other control characters, and decodable as UTF-8 or a single-byte charset."""
    if not sample:
        return False
    if b"\x00" in sample:
        return False
    controls = sum(1 for byte in sample if byte < 32 and byte not in _TEXT_CONTROL_BYTES)
    return controls / len(sample) < 0.05


def _zip_office_type(data: bytes) -> Optional[str]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile:
        return None
    if "word/document.xml" in names:
        return "docx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    return None


def detect_filetype(
    filename: Optional[str] = None,
    file: Optional[IO[Any]] = None,
    content_type: Optional[str] = None,
    metadata_filename: Optional[str] = None,
) -> str:
    """Return a short type name like "pdf" or "docx"; raise UnsupportedFileFormatError otherwise."""
    if content_type:
        mime = content_type.split(";")[0].strip().lower()
        if mime in MIME_TO_TYPE:
            return MIME_TO_TYPE[mime]
    name = filename or metadata_filename
    extension = os.path.splitext(str(name))[1].lower() if name else ""
    if extension in EXTENSION_TO_TYPE:
        return EXTENSION_TO_TYPE[extension]
    if extension in _UNSUPPORTED_HINTS:
        raise UnsupportedFileFormatError(f"Unsupported file type {extension}. {_UNSUPPORTED_HINTS[extension]}")

    if filename is not None:
        with open(filename, "rb") as fp:
            data = fp.read()
    elif file is not None:
        if hasattr(file, "seek"):
            file.seek(0)
        data = file.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
        if hasattr(file, "seek"):
            file.seek(0)
    else:
        raise ValueError("Exactly one of filename, file or url must be specified.")
    sniffed = _sniff_type(bytes(data[:8192]))
    if sniffed == "zip":
        sniffed = _zip_office_type(bytes(data))
    if sniffed is None:
        raise UnsupportedFileFormatError(
            f"Could not determine the type of {name or 'the input'}; pass content_type= to specify it."
        )
    return sniffed


def _partitioner(filetype: str) -> Callable[..., list[Element]]:
    if filetype == "csv":
        from tryworks.partition.csv import partition_csv as fn
    elif filetype == "tsv":
        from tryworks.partition.csv import partition_tsv as fn
    elif filetype == "docx":
        from tryworks.partition.docx import partition_docx as fn
    elif filetype == "eml":
        from tryworks.partition.email import partition_email as fn
    elif filetype == "html":
        from tryworks.partition.html import partition_html as fn
    elif filetype == "json":
        from tryworks.partition.json import partition_json as fn
    elif filetype == "md":
        from tryworks.partition.md import partition_md as fn
    elif filetype == "pdf":
        from tryworks.partition.pdf import partition_pdf as fn
    elif filetype == "pptx":
        from tryworks.partition.pptx import partition_pptx as fn
    elif filetype == "txt":
        from tryworks.partition.text import partition_text as fn
    elif filetype == "xlsx":
        from tryworks.partition.xlsx import partition_xlsx as fn
    else:
        raise UnsupportedFileFormatError(f"Unsupported file type: {filetype}")
    return fn


def partition(
    filename: Optional[str] = None,
    *,
    file: Optional[IO[Any]] = None,
    encoding: Optional[str] = None,
    content_type: Optional[str] = None,
    url: Optional[str] = None,
    headers: dict[str, str] = {},
    ssl_verify: bool = True,
    request_timeout: Optional[int] = None,
    strategy: str = "auto",
    skip_infer_table_types: list[str] = ["pdf", "jpg", "png", "heic"],
    ocr_languages: Optional[str] = None,
    languages: Optional[list[str]] = None,
    detect_language_per_element: bool = False,
    language_fallback: Optional[Callable[[str], Optional[list[str]]]] = None,
    pdf_infer_table_structure: bool = False,
    extract_images_in_pdf: bool = False,
    extract_image_block_types: Optional[list[str]] = None,
    extract_image_block_output_dir: Optional[str] = None,
    extract_image_block_to_payload: bool = False,
    data_source_metadata: Optional[Any] = None,
    metadata_filename: Optional[str] = None,
    hi_res_model_name: Optional[str] = None,
    model_name: Optional[str] = None,
    starting_page_number: int = 1,
    **kwargs: Any,
) -> list[Element]:
    """Partition any supported document: pdf, docx, pptx, xlsx, html, md, txt, csv, tsv, eml, json.

    The type comes from ``content_type``, then the file extension, then the content itself.
    Extra keyword arguments (``chunking_strategy``, ``max_characters``, ``include_page_breaks``
    ...) are passed through to the partitioner.
    """
    given = [x for x in (filename, file, url) if x is not None]
    if len(given) != 1:
        raise ValueError("Exactly one of filename, file or url must be specified.")

    if url is not None:
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("Only http and https URLs can be fetched.")
        request = urllib.request.Request(url, headers=dict(headers or {}))
        with urllib.request.urlopen(request, timeout=request_timeout or 30) as response:
            content_type = content_type or response.headers.get("Content-Type")
            file = io.BytesIO(response.read())
        metadata_filename = metadata_filename or os.path.basename(url.split("?")[0]) or None

    filetype = detect_filetype(filename=filename, file=file, content_type=content_type, metadata_filename=metadata_filename)
    infer_table_structure = filetype not in skip_infer_table_types
    common: dict[str, Any] = dict(
        languages=languages,
        detect_language_per_element=detect_language_per_element,
        language_fallback=language_fallback,
        metadata_filename=metadata_filename,
        data_source_metadata=data_source_metadata,
        **kwargs,
    )
    if filename is not None:
        common["filename"] = filename
    else:
        common["file"] = file
    if url is not None:
        common["url"] = url

    if filetype == "pdf":
        return _partitioner("pdf")(
            strategy=strategy,
            infer_table_structure=pdf_infer_table_structure,
            extract_images_in_pdf=extract_images_in_pdf,
            hi_res_model_name=hi_res_model_name or model_name,
            starting_page_number=starting_page_number,
            **common,
        )
    if filetype in ("docx", "pptx", "xlsx"):
        return _partitioner(filetype)(
            infer_table_structure=infer_table_structure, starting_page_number=starting_page_number, **common
        )
    if filetype in ("csv", "tsv"):
        return _partitioner(filetype)(encoding=encoding, infer_table_structure=infer_table_structure, **common)
    if filetype in ("html", "txt", "json"):
        return _partitioner(filetype)(encoding=encoding, **common)
    if filetype == "md":
        return _partitioner("md")(**common)
    return _partitioner(filetype)(**common)

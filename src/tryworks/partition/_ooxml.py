"""Read Office Open XML packages (.docx, .pptx, .xlsx) defensively, using only the stdlib.

Documents are untrusted input. Before any XML is parsed this module rejects:

- zip bombs: parts whose declared size or compression ratio is implausible, and parts whose
  actual decompressed size exceeds the limit even when the zip header lies;
- DTDs and entity declarations: OOXML never uses them, so their presence means an entity-
  expansion ("billion laughs") or external-entity (XXE) attempt.

Limits can be raised through ``TRYWORKS_MAX_PART_BYTES`` and ``TRYWORKS_MAX_PACKAGE_BYTES``.
"""

from __future__ import annotations

import io
import os
import posixpath
import zipfile
from typing import Optional
from xml.etree import ElementTree as ET

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}

_REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
REL_HYPERLINK = _REL_BASE + "hyperlink"
REL_HEADER = _REL_BASE + "header"
REL_FOOTER = _REL_BASE + "footer"
REL_SLIDE = _REL_BASE + "slide"
REL_NOTES = _REL_BASE + "notesSlide"
REL_WORKSHEET = _REL_BASE + "worksheet"
REL_SHARED_STRINGS = _REL_BASE + "sharedStrings"
REL_STYLES = _REL_BASE + "styles"
REL_OFFICE_DOCUMENT = _REL_BASE + "officeDocument"
REL_NUMBERING = _REL_BASE + "numbering"
REL_SETTINGS = _REL_BASE + "settings"

_MAX_RATIO = 200
_RATIO_CHECK_MIN_BYTES = 1 << 20


class UnsafeDocumentError(ValueError):
    """The document looks malicious or exceeds configured size limits."""


def _limit(env_name: str, default: int) -> int:
    try:
        return int(os.environ.get(env_name, default))
    except ValueError:
        return default


def qn(tag: str) -> str:
    """Expand a prefixed tag like ``w:p`` into ElementTree's ``{namespace}p`` form."""
    prefix, local = tag.split(":", 1)
    return f"{{{NS[prefix]}}}{local}"


class OoxmlPackage:
    """A zip-based Office document opened for safe, size-limited reading."""

    def __init__(self, data: bytes):
        self.max_part_bytes = _limit("TRYWORKS_MAX_PART_BYTES", 256 << 20)
        self.max_package_bytes = _limit("TRYWORKS_MAX_PACKAGE_BYTES", 1 << 30)
        try:
            self._zip = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise ValueError("File is not a valid Office Open XML (zip) package.") from e
        self._infos = {info.filename: info for info in self._zip.infolist()}
        self._consumed = 0
        self._check_declared_sizes()

    def _check_declared_sizes(self) -> None:
        total = 0
        for info in self._infos.values():
            total += info.file_size
            if info.file_size > self.max_part_bytes:
                raise UnsafeDocumentError(
                    f"Part {info.filename!r} declares {info.file_size} bytes, over the "
                    f"{self.max_part_bytes}-byte limit (TRYWORKS_MAX_PART_BYTES)."
                )
            if (
                info.file_size > _RATIO_CHECK_MIN_BYTES
                and info.compress_size
                and info.file_size / info.compress_size > _MAX_RATIO
            ):
                raise UnsafeDocumentError(
                    f"Part {info.filename!r} has a compression ratio above {_MAX_RATIO}:1."
                )
        if total > self.max_package_bytes:
            raise UnsafeDocumentError(
                f"Package declares {total} uncompressed bytes, over the {self.max_package_bytes}-byte "
                "limit (TRYWORKS_MAX_PACKAGE_BYTES)."
            )

    def has(self, name: str) -> bool:
        return name in self._infos

    def names(self) -> list[str]:
        return list(self._infos)

    def read(self, name: str) -> bytes:
        """Read a part, enforcing size limits on the bytes actually decompressed."""
        chunks = []
        size = 0
        with self._zip.open(name) as fp:
            while True:
                chunk = fp.read(1 << 16)
                if not chunk:
                    break
                size += len(chunk)
                self._consumed += len(chunk)
                if size > self.max_part_bytes or self._consumed > self.max_package_bytes:
                    raise UnsafeDocumentError(f"Part {name!r} decompresses past the size limit.")
                chunks.append(chunk)
        return b"".join(chunks)

    def xml(self, name: str) -> Optional[ET.Element]:
        """Parse a part as XML, or return None when the part does not exist."""
        if not self.has(name):
            return None
        data = self.read(name)
        head = data[:4096].lower()
        if b"<!doctype" in head or b"<!entity" in data.lower():
            raise UnsafeDocumentError(f"Part {name!r} contains a DTD or entity declaration.")
        try:
            return ET.fromstring(data)
        except ET.ParseError as e:
            raise ValueError(f"Part {name!r} is not well-formed XML: {e}") from e

    def rels(self, part_name: str) -> dict[str, tuple[str, str, bool]]:
        """Relationships of a part as ``{rId: (target, type, is_external)}``.

        Internal targets are resolved to package paths; external targets (like hyperlink URLs)
        are returned unchanged.
        """
        directory, filename = posixpath.split(part_name)
        rels_root = self.xml(posixpath.join(directory, "_rels", filename + ".rels"))
        out: dict[str, tuple[str, str, bool]] = {}
        if rels_root is None:
            return out
        for rel in rels_root.findall(qn("rel:Relationship")):
            target = rel.get("Target", "")
            external = rel.get("TargetMode") == "External"
            if not external:
                if target.startswith("/"):
                    target = target.lstrip("/")
                else:
                    target = posixpath.normpath(posixpath.join(directory, target))
            out[rel.get("Id", "")] = (target, rel.get("Type", ""), external)
        return out

    def main_part(self, rel_type: str = REL_OFFICE_DOCUMENT) -> Optional[str]:
        """Package path of the main document part (``word/document.xml`` and so on)."""
        for target, typ, external in self.rels("").values():
            if typ == rel_type and not external:
                return target
        return None

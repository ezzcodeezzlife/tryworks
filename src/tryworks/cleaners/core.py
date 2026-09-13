"""Text cleaners. Same function names and semantics as ``unstructured.cleaners.core``."""

from __future__ import annotations

import quopri
import re
import sys
import unicodedata
from typing import Optional

from tryworks._patterns import (
    BLANK_LINE_SPLIT_RE,
    BULLET_PREFIX_RE,
    BULLET_RE,
    LINE_SPLIT_RE,
    UNICODE_BULLETS,
)

_PUNCTUATION = "".join(
    chr(i) for i in range(sys.maxunicode + 1) if unicodedata.category(chr(i)).startswith("P")
)
_PUNCT_TRANSLATION = str.maketrans("", "", _PUNCTUATION)

_LIGATURES = {
    "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ﬀ": "ff",
    "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "ft",
    "ﬆ": "st", "ĳ": "ij", "Ĳ": "IJ", "ʪ": "ls", "ʫ": "lz",
}

# -- replaced in order, so multi-character mojibake sequences come before single characters --
_QUOTES = (
    # -- UTF-8 punctuation bytes that were mis-decoded as Latin-1 --
    ("â", "'"),
    ("â", "'"),
    ("â", '"'),
    ("â", '"'),
    ("â", "—"),
    ("â", "–"),
    ("â¦", "…"),
    # -- Windows-1252 quote bytes that were mis-decoded as Latin-1 --
    ("\x91", "'"),
    ("\x92", "'"),
    ("\x93", '"'),
    ("\x94", '"'),
    ("&apos;", "'"),
    # -- typographic quotes --
    ("‘", "'"),
    ("’", "'"),
    ("“", '"'),
    ("”", '"'),
)

_DASHES_RE = re.compile("[-‐‑‒–—―]")
_ORDERED_TOKEN_RE = re.compile(r"^[A-Za-z0-9]{1,2}(?:\.[A-Za-z0-9]{1,2}){0,2}\.?$")
_BULLET_SPLIT_RE = re.compile(r"\s*(?=[" + UNICODE_BULLETS + "])")


def clean_non_ascii_chars(text: str) -> str:
    """Remove every non-ASCII character."""
    return text.encode("ascii", "ignore").decode()


def clean_bullets(text: str) -> str:
    """Remove a leading bullet, e.g. "● An excellent point!" -> "An excellent point!"."""
    if not BULLET_RE.match(text):
        return text
    return BULLET_PREFIX_RE.sub("", text, count=1).strip()


def clean_ordered_bullets(text: str) -> str:
    """Remove a leading outline number, e.g. "1.1 A very important point" -> "A very important point".

    Only the first token is considered, and only when it contains a dot and has at most three
    parts of one or two characters, so "3.14159 is pi" keeps its number.
    """
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return text
    head, rest = parts
    if "." not in head or ".." in head or not _ORDERED_TOKEN_RE.match(head):
        return text
    return rest


def clean_ligatures(text: str) -> str:
    """Replace typographic ligatures, e.g. "ﬁnance" -> "finance"."""
    for ligature, replacement in _LIGATURES.items():
        text = text.replace(ligature, replacement)
    return text


def group_bullet_paragraph(paragraph: str) -> list:
    """Split a paragraph holding several bullets into one line of text per bullet."""
    items = []
    for chunk in _BULLET_SPLIT_RE.split(paragraph):
        joined = " ".join(line.strip() for line in LINE_SPLIT_RE.split(chunk) if line.strip())
        if joined:
            items.append(joined)
    return items


def group_broken_paragraphs(
    text: str,
    line_split: re.Pattern = LINE_SPLIT_RE,
    paragraph_split: re.Pattern = BLANK_LINE_SPLIT_RE,
) -> str:
    """Rejoin paragraphs that were hard-wrapped for display.

    Blank lines separate paragraphs. Inside a paragraph, wrapped lines are joined with a space,
    unless every line is short (under five words, like an address block), in which case each
    line stays its own paragraph. A bulleted paragraph becomes one paragraph per bullet.
    """
    out = []
    for paragraph in paragraph_split.split(text):
        if not paragraph.strip():
            continue
        lines = [line for line in line_split.split(paragraph) if line.strip()]
        if BULLET_RE.match(paragraph.strip()):
            out.extend(group_bullet_paragraph(paragraph))
        elif all(len(line.split()) < 5 for line in lines):
            out.extend(line.strip() for line in lines)
        else:
            out.append(" ".join(line.strip() for line in lines))
    return "\n\n".join(out)


def new_line_grouper(text: str, paragraph_split: re.Pattern = LINE_SPLIT_RE) -> str:
    """Treat every non-empty line as its own paragraph."""
    return "\n\n".join(line.strip() for line in paragraph_split.split(text) if line.strip())


def blank_line_grouper(text: str, paragraph_split: re.Pattern = BLANK_LINE_SPLIT_RE) -> str:
    """Treat blank lines as paragraph separators and rejoin wrapped lines."""
    return group_broken_paragraphs(text, paragraph_split=paragraph_split)


def auto_paragraph_grouper(
    text: str,
    line_split: re.Pattern = LINE_SPLIT_RE,
    max_line_count: int = 2000,
    threshold: float = 0.1,
) -> str:
    """Pick a paragraph grouper from how often blank lines occur.

    When fewer than ``threshold`` of the first ``max_line_count`` lines are blank, the document
    is read as one paragraph per line; otherwise blank lines separate paragraphs.
    """
    lines = line_split.split(text)[:max_line_count]
    if not lines:
        return text
    blank_ratio = sum(1 for line in lines if not line.strip()) / len(lines)
    if blank_ratio < threshold:
        return new_line_grouper(text)
    return blank_line_grouper(text)


def replace_unicode_quotes(text: str) -> str:
    """Normalize curly quotes and common mojibake quote sequences to ASCII quotes."""
    for bad, good in _QUOTES:
        text = text.replace(bad, good)
    return text


def remove_punctuation(s: str) -> str:
    """Remove every Unicode punctuation character."""
    return s.translate(_PUNCT_TRANSLATION)


def remove_sentence_punctuation(s: str, exclude_punctuation: Optional[list]) -> str:
    """Remove punctuation except the characters listed in ``exclude_punctuation``."""
    keep = set(exclude_punctuation or [])
    table = str.maketrans("", "", "".join(c for c in _PUNCTUATION if c not in keep))
    return s.translate(table)


def clean_extra_whitespace(text: str) -> str:
    """Collapse runs of whitespace (newlines and no-break spaces included) into single spaces."""
    return re.sub(r"\s+", " ", text).strip()


def clean_dashes(text: str) -> str:
    """Replace dash characters with spaces, e.g. "ITEM 1. -BUSINESS" -> "ITEM 1.  BUSINESS"."""
    return _DASHES_RE.sub(" ", text).strip()


def clean_trailing_punctuation(text: str) -> str:
    """Strip trailing ".", ",", ":" and ";"."""
    return text.strip().rstrip(".,:;")


def replace_mime_encodings(text: str, encoding: str = "utf-8") -> str:
    """Decode quoted-printable sequences such as "=E2=80=99"."""
    return quopri.decodestring(text.encode(encoding)).decode(encoding)


def clean_prefix(text: str, pattern: str, ignore_case: bool = False, strip: bool = True) -> str:
    """Remove a regex ``pattern`` anchored at the start of ``text``."""
    flags = re.IGNORECASE if ignore_case else 0
    cleaned = re.sub(f"^{pattern}", "", text, flags=flags)
    return cleaned.lstrip() if strip else cleaned


def clean_postfix(text: str, pattern: str, ignore_case: bool = False, strip: bool = True) -> str:
    """Remove a regex ``pattern`` anchored at the end of ``text``."""
    flags = re.IGNORECASE if ignore_case else 0
    cleaned = re.sub(f"{pattern}$", "", text, flags=flags)
    return cleaned.rstrip() if strip else cleaned


def clean(
    text: str,
    extra_whitespace: bool = False,
    dashes: bool = False,
    bullets: bool = False,
    trailing_punctuation: bool = False,
    lowercase: bool = False,
) -> str:
    """Apply the selected cleaners in a fixed order."""
    cleaned = text.lower() if lowercase else text
    cleaned = clean_trailing_punctuation(cleaned) if trailing_punctuation else cleaned
    cleaned = clean_dashes(cleaned) if dashes else cleaned
    cleaned = clean_extra_whitespace(cleaned) if extra_whitespace else cleaned
    cleaned = clean_bullets(cleaned) if bullets else cleaned
    return cleaned.strip()


def bytes_string_to_string(text: str, encoding: str = "utf-8") -> str:
    """Decode a str whose code points are raw bytes (a common mis-decoding) into real text."""
    return bytes(ord(ch) for ch in text).decode(encoding)

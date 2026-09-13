"""Plumbing shared by all partitioners: reading input, file types, metadata, ids, hierarchy."""

from __future__ import annotations

import codecs
import copy
import datetime as dt
import functools
import inspect
import os
import pathlib
import re
import unicodedata
from typing import IO, Any, Callable, Iterable, Iterator, Optional, Sequence, Union

from tryworks.documents.elements import Element, ElementMetadata, assign_and_map_hash_ids

FilePath = Union[str, "os.PathLike[str]"]


class FileType:
    """MIME types written to ``metadata.filetype``, matching unstructured."""

    CSV = "text/csv"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    EML = "message/rfc822"
    HTML = "text/html"
    JSON = "application/json"
    MD = "text/markdown"
    PDF = "application/pdf"
    PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    TSV = "text/tsv"
    TXT = "text/plain"
    XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# -- input handling --------------------------------------------------------------------------


def exactly_one(**kwargs: Any) -> None:
    """Raise ValueError unless exactly one keyword argument is not None (or non-empty)."""
    given = [name for name, value in kwargs.items() if value is not None and value != ""]
    if len(given) != 1:
        names = ", ".join(kwargs)
        if not given:
            raise ValueError(f"Exactly one of {names} must be specified.")
        raise ValueError(f"Exactly one of {names} must be specified; got {', '.join(given)}.")


def read_bytes(filename: Optional[FilePath] = None, file: Optional[IO[Any]] = None) -> bytes:
    """Return the full content of a path or a file-like object as bytes."""
    if filename is not None:
        with open(os.fspath(filename), "rb") as fp:
            return fp.read()
    if file is None:
        raise ValueError("Either filename or file must be provided.")
    if hasattr(file, "seek"):
        try:
            file.seek(0)
        except (OSError, ValueError):
            pass
    data = file.read()
    if isinstance(data, str):
        return data.encode("utf-8")
    return bytes(data)


_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def decode_bytes(data: bytes, encoding: Optional[str] = None) -> tuple[str, str]:
    """Decode bytes, returning ``(encoding_used, text)``.

    An explicit encoding is honored. Otherwise a byte-order mark wins, then strict UTF-8, then
    Windows-1252, which accepts nearly any byte sequence without mangling Western text.
    """
    if encoding:
        return encoding, data.decode(encoding)
    for bom, name in _BOMS:
        if data.startswith(bom):
            return name, data.decode(name)
    try:
        return "utf-8", data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return "cp1252", data.decode("cp1252")
    except UnicodeDecodeError:
        return "latin-1", data.decode("latin-1")


def read_text(
    filename: Optional[FilePath] = None,
    file: Optional[IO[Any]] = None,
    text: Optional[str] = None,
    encoding: Optional[str] = None,
) -> str:
    if text is not None:
        return str(text)
    if file is not None:
        if hasattr(file, "seek"):
            try:
                file.seek(0)
            except (OSError, ValueError):
                pass
        data = file.read()
        if isinstance(data, str):
            return data
        return decode_bytes(bytes(data), encoding)[1]
    return decode_bytes(read_bytes(filename=filename), encoding)[1]


def get_last_modified_date(filename: FilePath) -> Optional[str]:
    """Modification time of a local file as ``YYYY-MM-DDTHH:MM:SS``, or None."""
    path = os.fspath(filename)
    if not os.path.isfile(path):
        return None
    return dt.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%dT%H:%M:%S")


# -- language detection ----------------------------------------------------------------------

_STOPWORDS = {
    "eng": "the and of to in is that it for was on are with as be this by at have from or not but an they which you were",
    "deu": "der die und das ist nicht ein eine zu den mit sich des auf für von dem im sie es auch als wird werden",
    "fra": "le la les et des est une un du que en dans pour qui pas sur au avec ce il elle sont par plus",
    "spa": "el la de que y los las en un una es por con para del se al lo como pero sus su más",
    "ita": "il di che la e le un una per non sono del della gli con si da nel alla anche come più",
    "por": "o a de que e os as do da em um uma para com não se na no por mais como dos das",
    "nld": "de het een en van is dat op te in niet met zijn voor die er aan ook als maar bij",
    "swe": "och att det som en är av för på med den till inte har de ett om men var",
    "dan": "og at det er en af til på med den for ikke som har de et om men var jeg",
    "nor": "og å det er en av til på med den for ikke som har de et om men var jeg",
    "fin": "ja on ei että se oli ovat mutta kuin tai myös ole joka jos niin kun hän",
    "pol": "i w na z że się nie do jest to jak co ale o po od przez dla są",
    "ces": "a v na se je že to s z do o k jako ale jsou by pro od",
    "ron": "și de la în că cu pe un o nu este sunt din pentru se care mai",
    "tur": "ve bir bu da de için ile çok ne olarak daha gibi ama değil en",
    "hun": "a az és hogy nem is egy van meg de ez azt csak mint vagy",
    "ind": "dan yang di ini itu dengan untuk dari tidak ada pada akan juga",
    "vie": "và của là có không những được cho trong một các người đã với",
}
_STOPWORD_SETS = {lang: frozenset(words.split()) for lang, words in _STOPWORDS.items()}

_SCRIPT_LANGS = (
    ("HANGUL", "kor"),
    ("HIRAGANA", "jpn"),
    ("KATAKANA", "jpn"),
    ("CJK", "zho"),
    ("CYRILLIC", "rus"),
    ("ARABIC", "ara"),
    ("HEBREW", "heb"),
    ("GREEK", "ell"),
    ("THAI", "tha"),
    ("DEVANAGARI", "hin"),
)
_ASCII_RE = re.compile(r"^[\x00-\x7f]*$")
_LETTERS_RE = re.compile(r"[^\W\d_]+")

_LANGUAGE_ALIASES = {
    "en": "eng", "english": "eng", "de": "deu", "ger": "deu", "german": "deu", "fr": "fra",
    "fre": "fra", "french": "fra", "es": "spa", "spanish": "spa", "it": "ita", "italian": "ita",
    "pt": "por", "portuguese": "por", "nl": "nld", "dut": "nld", "dutch": "nld", "sv": "swe",
    "da": "dan", "no": "nor", "nb": "nor", "fi": "fin", "pl": "pol", "cs": "ces", "cze": "ces",
    "ro": "ron", "rum": "ron", "tr": "tur", "hu": "hun", "id": "ind", "vi": "vie", "zh": "zho",
    "chi": "zho", "chi_sim": "zho", "chi_tra": "zho", "ja": "jpn", "ko": "kor", "ru": "rus",
    "ar": "ara", "he": "heb", "el": "ell", "gre": "ell", "th": "tha", "hi": "hin", "uk": "ukr",
}


def _detect_language(text: str) -> Optional[str]:
    script_counts: dict[str, int] = {}
    latin = 0
    for ch in text[:20000]:
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        if name.startswith("LATIN"):
            latin += 1
            continue
        for prefix, lang in _SCRIPT_LANGS:
            if name.startswith(prefix):
                script_counts[lang] = script_counts.get(lang, 0) + 1
                break
    if script_counts:
        if script_counts.get("jpn"):
            script_counts["jpn"] += script_counts.pop("zho", 0)
        lang, count = max(script_counts.items(), key=lambda kv: kv[1])
        if count >= latin:
            if lang == "rus" and any(ch in text for ch in "іїєґІЇЄҐ"):
                return "ukr"
            return lang
    words = [w.lower() for w in _LETTERS_RE.findall(text[:20000])]
    if not words:
        return None
    scores = {lang: sum(1 for w in words if w in stops) for lang, stops in _STOPWORD_SETS.items()}
    best, best_score = max(scores.items(), key=lambda kv: kv[1])
    return best if best_score > 0 else None


def detect_languages(
    text: str,
    languages: Optional[list[str]] = None,
    language_fallback: Optional[Callable[[str], Optional[list[str]]]] = None,
) -> Optional[list[str]]:
    """ISO 639-3 codes for ``text``: detected when ``languages`` is None or contains "auto",
    otherwise the given languages normalized. Short ASCII text defaults to English."""
    if languages is None:
        languages = ["auto"]
    if not isinstance(languages, list):
        raise TypeError('The language parameter must be a list of language codes as strings, ex. ["eng"]')
    if not languages or languages[0] == "" or not text.strip():
        return None
    if _ASCII_RE.match(text) and len(text.split()) < 5:
        if language_fallback is not None:
            result = language_fallback(text)
            return [code for code in result if isinstance(code, str) and len(code) == 3] or None if result else None
        return ["eng"]
    if "auto" not in languages:
        normalized = []
        for lang in languages:
            code = _LANGUAGE_ALIASES.get(lang.lower(), lang.lower()[:3])
            if code not in normalized:
                normalized.append(code)
        return normalized
    detected = _detect_language(text)
    return [detected] if detected else None


def apply_lang_metadata(
    elements: Iterable[Element],
    languages: Optional[list[str]],
    detect_language_per_element: bool = False,
    language_fallback: Optional[Callable[[str], Optional[list[str]]]] = None,
) -> list[Element]:
    """Set ``metadata.languages`` on each element, per document unless asked per element."""
    elements = list(elements)
    if languages == [""]:
        return elements
    full_text = " ".join(e.text for e in elements if getattr(e, "text", None))
    detected = detect_languages(full_text, languages, language_fallback)
    if detected is not None and len(detected) == 1 and not detect_language_per_element:
        for e in elements:
            e.metadata.languages = list(detected)
        return elements
    for e in elements:
        if hasattr(e, "text"):
            e.metadata.languages = detect_languages(e.text or "", languages, language_fallback)
    return elements


# -- hierarchy -------------------------------------------------------------------------------

_BODY_CATEGORIES = [
    "Text", "UncategorizedText", "NarrativeText", "ListItem", "BulletedText", "Table",
    "FigureCaption", "CheckBox",
]
HIERARCHY_RULE_SET = {
    "Title": list(_BODY_CATEGORIES),
    "Header": ["Title", *_BODY_CATEGORIES],
}


def set_element_hierarchy(
    elements: Sequence[Element], ruleset: dict[str, list[str]] = HIERARCHY_RULE_SET
) -> list[Element]:
    """Assign ``metadata.parent_id`` from category precedence, then ``category_depth``.

    An element's parent is the nearest preceding element that either has the same category and
    a smaller depth, or has a category whose rule lists this element's category.
    """
    stack: list[Element] = []
    for element in elements:
        if element.metadata.parent_id is not None:
            continue
        category = getattr(element, "category", None)
        if not category:
            continue
        depth = element.metadata.category_depth or 0
        parent_id = None
        while stack:
            top = stack[-1]
            top_depth = top.metadata.category_depth or 0
            if (top.category == category and top_depth < depth) or (
                top.category != category and category in ruleset.get(top.category, [])
            ):
                parent_id = top.id
                break
            stack.pop()
        element.metadata.parent_id = parent_id
        stack.append(element)
    return list(elements)


# -- the post-processing decorator -----------------------------------------------------------


def _call_args(func: Callable[..., Any], args: tuple, kwargs: dict) -> dict[str, Any]:
    signature = inspect.signature(func)
    bound = signature.bind(*args, **kwargs)
    bound.apply_defaults()
    call_args = dict(bound.arguments)
    for param in signature.parameters.values():
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            call_args.update(call_args.pop(param.name, None) or {})
    return call_args


def _uniqueify(elements: Iterable[Element]) -> list[Element]:
    """Copy elements or metadata objects that appear more than once, so mutation stays local."""
    seen_elements: set[int] = set()
    seen_metadata: set[int] = set()
    out = []
    for element in elements:
        if id(element) in seen_elements:
            element = copy.deepcopy(element)
        seen_elements.add(id(element))
        if id(element.metadata) in seen_metadata:
            element.metadata = copy.deepcopy(element.metadata)
        seen_metadata.add(id(element.metadata))
        out.append(element)
    return out


def apply_metadata(file_type: Optional[str] = None) -> Callable[[Callable[..., list[Element]]], Callable[..., list[Element]]]:
    """Decorate a partitioner with the metadata steps every partitioner shares.

    In order: language detection, ``filetype`` / ``filename`` / ``last_modified`` / ``url``,
    deterministic hash ids (unless ``unique_element_ids=True``), ``parent_id`` hierarchy, and
    finally chunking when ``chunking_strategy`` is passed.
    """

    def decorator(func: Callable[..., list[Element]]) -> Callable[..., list[Element]]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> list[Element]:
            call_args = _call_args(func, args, kwargs)
            elements = _uniqueify(func(*args, **kwargs))
            elements = apply_lang_metadata(
                elements,
                languages=call_args.get("languages"),
                detect_language_per_element=call_args.get("detect_language_per_element", False),
                language_fallback=call_args.get("language_fallback"),
            )

            fields: dict[str, Any] = {}
            mime = call_args.get("metadata_file_type") or file_type
            if mime:
                fields["filetype"] = mime
            filename = call_args.get("metadata_filename") or call_args.get("filename")
            if filename:
                fields["filename"] = os.fspath(filename) if isinstance(filename, pathlib.PurePath) else filename
            # -- an explicit metadata_last_modified wins; the file's mtime only fills gaps, so a
            # -- date the partitioner found itself (like an email's Date header) is kept --
            if call_args.get("metadata_last_modified"):
                fields["last_modified"] = call_args["metadata_last_modified"]
            file_mtime = (
                get_last_modified_date(call_args["filename"])
                if call_args.get("filename") and not isinstance(call_args["filename"], (bytes, bytearray))
                else None
            )
            if call_args.get("url"):
                fields["url"] = call_args["url"]
            data_source = call_args.get("data_source_metadata")
            if data_source is not None:
                fields["data_source"] = data_source
            update = ElementMetadata(**fields)
            for element in elements:
                if element.metadata.attached_to_filename:
                    continue
                element.metadata.update(update)
                # -- document headers and footers carry no modification date, as in unstructured --
                if file_mtime and element.metadata.last_modified is None and element.metadata.header_footer_type is None:
                    element.metadata.last_modified = file_mtime

            if not call_args.get("unique_element_ids", False):
                # -- attachment elements keep the ids from their own partitioning pass --
                assign_and_map_hash_ids([e for e in elements if not e.metadata.attached_to_filename])
            elements = set_element_hierarchy(elements)

            strategy = call_args.get("chunking_strategy")
            if strategy:
                from tryworks.chunking import chunk

                elements = chunk(elements, strategy, **_chunking_kwargs(call_args))
            return elements

        wrapper.__wrapped_partitioner__ = func  # type: ignore[attr-defined]
        return wrapper

    return decorator


_CHUNKING_ARGS = (
    "combine_text_under_n_chars", "include_orig_elements", "max_characters", "multipage_sections",
    "new_after_n_chars", "overlap", "overlap_all",
)


def _chunking_kwargs(call_args: dict[str, Any]) -> dict[str, Any]:
    return {name: call_args[name] for name in _CHUNKING_ARGS if call_args.get(name) is not None}


def iter_nonempty(texts: Iterable[str]) -> Iterator[str]:
    for text in texts:
        if text and text.strip():
            yield text

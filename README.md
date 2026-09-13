# tryworks

[![CI](https://github.com/ezzcodeezzlife/tryworks/actions/workflows/ci.yml/badge.svg)](https://github.com/ezzcodeezzlife/tryworks/actions/workflows/ci.yml)
![Python 3.10–3.14](https://img.shields.io/badge/python-3.10%E2%80%933.14-3776ab)
![Dependencies: 0](https://img.shields.io/badge/dependencies-0-2ea44f)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

**Turn PDFs, Word, PowerPoint, Excel, HTML, Markdown, email and text into LLM-ready elements, with
zero required dependencies.**

tryworks implements the document-partitioning call path of
[unstructured](https://github.com/Unstructured-IO/unstructured): the same `partition()` function,
the same element types and metadata, the same chunking, the same JSON. It leaves out what most
pipelines never call, namely OCR, layout models and ingest connectors, and the 2 GB of
dependencies they bring.

```python
from tryworks.partition.auto import partition
from tryworks.chunking.title import chunk_by_title

elements = partition("quarterly-report.docx")
chunks = chunk_by_title(elements, max_characters=1000)

for chunk in chunks:
    print(chunk.metadata.page_number, chunk.text[:80])
```

> The name: tryworks were the brick furnaces on whaling ships that rendered raw blubber into oil.

## Why

Measured on Linux x86-64 with Python 3.12, reading the same document types:

|  | `unstructured[docx,pptx,xlsx,md,csv,pdf]` | `tryworks[pdf]` |
|---|---:|---:|
| Packages installed | 149 | **2** |
| Download size | 3,257 MB | **3.6 MB** |
| Installed size (unstructured with CPU-only torch) | 2.0 GB | **9 MB** |
| Importing `partition` plus the PDF and DOCX partitioners | 4.4–6.2 s | **0.15–0.39 s** |
| Partitioning the 9-document test corpus, first call included | 7.2 s | **0.59 s** |
| Security advisories ever published for the installed packages | 701 | **0** |
| Lines of library code | 34,171 | 5,449 |

Base `tryworks` without the PDF extra is a single 80 KB wheel with no dependencies.

In practice that means:

- **It fits in AWS Lambda.** The zip deployment limit is 250 MB unpacked, and it cannot be raised.
- **Cold starts are fast**, and since August 2025 AWS bills the Lambda init phase.
- **Security review is short.** Two packages to approve, pin and scan, where unstructured needs
  149. Air-gapped and regulated environments review each dependency individually.
- **The supply-chain surface is small.** Most advisories in a large tree come from its transitive
  dependencies, not from the library you asked for.

Numbers come from `scripts/measure.py` (exact resolution with `uv pip compile`, advisory counts
from OSV.dev) and from real installs; see [Reproducing the numbers](#reproducing-the-numbers).
Neither tree had an open advisory against the versions resolved when this was measured; the
701 are historical, which is what a scanner and a triage process work through over time.

## Install

```bash
pip install "tryworks @ git+https://github.com/ezzcodeezzlife/tryworks"
pip install "tryworks[pdf] @ git+https://github.com/ezzcodeezzlife/tryworks"   # PDF support
```

Python 3.10 or newer. PDF support adds [pypdfium2](https://github.com/pypdfium2-team/pypdfium2),
bindings to PDFium, the PDF engine used in Chrome. Everything else uses the standard library.

## Use

### Partition

```python
from tryworks.partition.auto import partition

elements = partition("report.pdf")                      # type from extension or content
elements = partition(file=open("memo.docx", "rb"))      # file objects work too
elements = partition(url="https://example.com/post")    # http(s) URLs

for el in elements:
    print(el.category, el.metadata.page_number, el.text)
# Title 1 Quarterly Operations Memo
# NarrativeText 1 Revenue grew eleven percent year over year, ...
# ListItem 1 Expand the Rotterdam warehouse
# Table 1 Warehouse totals Units Rotterdam KR-1001 1200 ...
```

Each format also has its own function with unstructured's signature: `partition_pdf`,
`partition_docx`, `partition_pptx`, `partition_xlsx`, `partition_html`, `partition_md`,
`partition_text`, `partition_csv`, `partition_tsv`, `partition_email`, `partition_json`.

### Chunk

```python
from tryworks.chunking.title import chunk_by_title
from tryworks.chunking.basic import chunk_elements

chunks = chunk_by_title(elements, max_characters=1000, overlap=100)
chunks = partition("report.pdf", chunking_strategy="by_title", max_characters=1000)
```

Sections start at each Title, small sections are combined, long text is split at newlines or
spaces, and tables get chunks of their own. Tables too large for one chunk are split by row
into `TableChunk` elements that keep valid HTML in `metadata.text_as_html`.

### Serialize

```python
from tryworks.staging.base import elements_to_json, elements_from_json, elements_to_md

json_text = elements_to_json(elements)         # same JSON as unstructured, readable by it
elements = elements_from_json(text=json_text)
markdown = elements_to_md(elements)
```

### Command line

```bash
tryworks report.pdf                                   # JSON
tryworks memo.docx --format markdown
tryworks https://example.com/post --format text --chunking-strategy by_title --max-characters 800
```

## Moving from unstructured

Change the package name in your imports:

```diff
- from unstructured.partition.auto import partition
- from unstructured.chunking.title import chunk_by_title
+ from tryworks.partition.auto import partition
+ from tryworks.chunking.title import chunk_by_title
```

For code you can't edit, such as a third-party library that imports `unstructured` itself, register
tryworks under the `unstructured` import names before that code is imported:

```python
import tryworks
tryworks.alias_as_unstructured()
```

This only affects imports in the current process. It installs nothing named `unstructured`.

## Formats

| Format | Extensions | Implementation | What you get |
|---|---|---|---|
| PDF | `.pdf` | PDFium via `pypdfium2` | Text blocks in reading order (multi-column aware), titles, lists, headers and footers, coordinates, page numbers |
| Word | `.docx` | `zipfile` + `ElementTree` | Style-based titles and lists, tables with merged cells, section headers and footers, links, bold/italic, page breaks |
| PowerPoint | `.pptx` | `zipfile` + `ElementTree` | One page per slide, titles, bullets with levels, tables, speaker notes |
| Excel | `.xlsx` | `zipfile` + `ElementTree` | Each sheet split into sub-tables, captions as text, dates and numbers formatted |
| HTML | `.html`, `.htm` | `html.parser` | Headings, paragraphs, nested lists, tables, code, images, links, emphasis; navigation and forms dropped |
| Markdown | `.md` | Built-in renderer | Same elements as HTML |
| Text | `.txt` | Standard library | Paragraphs classified as titles, prose, list items, emails, addresses |
| CSV, TSV | `.csv`, `.tsv` | `csv` | One table, delimiter detected |
| Email | `.eml` | `email` | Body with sender, recipients, subject and date; attachments partitioned |
| Elements | `.json` | `json` | Elements saved by `elements_to_json`, from either library |

## Compatibility with unstructured

A generated corpus with one realistic document per format is partitioned by both libraries and
compared element by element ([report](compat/REPORT.md), [script](scripts/compat.py)). A
[workflow](.github/workflows/compat.yml) repeats the comparison weekly against the latest
unstructured release.

Against unstructured 0.27.5:

- **97%** of upstream elements have a tryworks element with identical text,
- **97%** of those have the same element type,
- **94%** have the same element id (ids hash filename, text, page and position, so this also
  checks ordering),
- `parent_id`, `filetype`, `page_number`, `page_name`, `link_urls`, `emphasized_text_contents`,
  `header_footer_type`, `sent_from` and `subject` agree on every aligned element.

Every remaining difference in the report is one of the deliberate differences listed below.

### Deliberate differences

| Where | unstructured | tryworks | Why |
|---|---|---|---|
| Markdown lists | Python-Markdown joins a numbered list with the bullet list after it and turns items next to blank lines into paragraphs | Separate lists, every item a `ListItem` | CommonMark behavior; bullets stay bullets |
| PDF bullets | Consecutive bullet lines can merge into one `ListItem` | One `ListItem` per bullet | Each bullet is its own item |
| PDF columns | Text of a right-hand column can come after the page footer | Left column, right column, then footer | Reading order |
| PDF coordinates | pdfminer glyph boxes | PDFium glyph boxes | Within about 1 pt |
| DOCX merged cells | 0.27.5 repeats the text in every merged cell | `colspan` / `rowspan` | Matches unstructured's main branch |
| Language | `langdetect` | Script detection plus stopword profiles for 18 languages | No model dependency; results can differ on tables and fragments |

### Not included

- **OCR and layout models.** `strategy="hi_res"` and `"ocr_only"` raise `NotImplementedError`, and
  scanned PDFs without embedded text produce a warning and no elements. Use unstructured or an OCR
  service for those.
- **Formats:** images, audio, `.doc`, `.ppt`, `.xls`, `.msg`, `.rtf`, `.odt`, `.epub`, `.rst`,
  `.org`, `.xml`. Unsupported files raise `UnsupportedFileFormatError`, with a conversion hint.
- **PDF table structure and image extraction.** PDF tables come out as text blocks.
- **Pictures in DOCX and PPTX** are not emitted as `Image` elements.
- **Token-based chunking** (`max_tokens`) raises `NotImplementedError`; use `max_characters`.
- Ingest connectors, the hosted API client, and staging bricks for labeling tools.

Text classification uses the same rules as unstructured, but detects verbs with a compact
lexicon instead of NLTK. On borderline text such as short fragments, a paragraph can come out as
`UncategorizedText` where unstructured says `NarrativeText`, or the reverse.

## Untrusted documents

Documents are parsed defensively: zip-bomb checks on Office files, rejection of XML entity
declarations, and caps on spreadsheet cells, PDF pages, HTML nesting and chunk payloads, all
adjustable through environment variables. Only http(s) URLs are fetched. See
[SECURITY.md](SECURITY.md).

## Reproducing the numbers

```bash
# install size, packages and advisories (needs uv)
python scripts/measure.py "unstructured[docx,pptx,xlsx,md,csv,pdf]" "tryworks[pdf] @ ."

# element-by-element comparison
python tests/corpus.py compat/out/corpus
python scripts/compat.py dump tryworks compat/out/corpus compat/out/tryworks.json
python scripts/compat.py dump unstructured compat/out/corpus compat/out/upstream.json   # in an env with unstructured
python scripts/compat.py compare compat/out/upstream.json compat/out/tryworks.json --markdown compat/REPORT.md
```

## Development

```bash
pip install -e ".[dev]"
python -m pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md). The one hard rule: the base install stays
dependency-free.

## How this was built

tryworks was written with [Claude Code](https://claude.com/claude-code) under human direction. It
is an independent implementation of unstructured's public API; no unstructured source code was
copied. Confidence in it comes from things you can check yourself: the test suite, which runs on
Linux, macOS and Windows, and the differential comparison against unstructured above.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). tryworks is not affiliated with or
endorsed by Unstructured Technologies, Inc.

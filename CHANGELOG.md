# Changelog

## 0.1.1

- README: removed the "How this was built" section. No code changes.

## 0.1.0

First release.

- `partition()` with type detection by content type, extension or content.
- Partitioners for PDF (embedded text, via the `pdf` extra), DOCX, PPTX, XLSX, HTML, Markdown,
  plain text, CSV, TSV, EML and element JSON.
- `chunk_by_title` and `chunk_elements`, including table splitting into `TableChunk` elements.
- Element classes, metadata, deterministic ids, `parent_id` hierarchy and JSON serialization
  compatible with unstructured.
- Text cleaners and text-type classifiers without NLTK or spaCy.
- `tryworks` command line tool.
- `alias_as_unstructured()` for code that imports `unstructured` directly.
- Size and nesting limits for untrusted documents (see SECURITY.md).

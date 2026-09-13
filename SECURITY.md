# Security

tryworks parses untrusted documents, so parser safety is part of its job.

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** on this repository (Security tab →
"Report a vulnerability"). Include a sample document if you can. You should hear back within a
week. Please don't open a public issue for a vulnerability.

## What tryworks defends against

| Threat | Defense | Limit (environment variable) |
|---|---|---|
| Zip bombs in .docx/.pptx/.xlsx | Declared sizes and compression ratios are checked before reading; actual decompressed bytes are counted while reading | `TRYWORKS_MAX_PART_BYTES` (256 MB), `TRYWORKS_MAX_PACKAGE_BYTES` (1 GB) |
| XML entity expansion and external entities (XXE) | Any DTD or `<!ENTITY` in an Office part is rejected; OOXML never needs them | — |
| Huge spreadsheets | Non-empty cells per sheet are capped | `TRYWORKS_MAX_SHEET_CELLS` (5,000,000) |
| Huge PDFs | Page count is capped | `TRYWORKS_MAX_PDF_PAGES` (10,000) |
| Deeply nested HTML | Nesting past 200 levels is flattened | — |
| Oversized chunk payloads | `orig_elements` refuses to inflate past 200 MB | — |
| Local file access through URLs | Only `http://` and `https://` URLs are fetched | — |

The base package has no third-party dependencies. PDF support adds one: `pypdfium2`, which bundles
PDFium, the PDF engine in Chrome.

## Supported versions

Security fixes go into the latest release.

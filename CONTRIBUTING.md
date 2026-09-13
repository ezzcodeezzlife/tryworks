# Contributing

## Setup

```bash
uv venv
uv pip install -e ".[dev]"
python -m pytest
```

Plain `pip install -e ".[dev]"` works too.

## Ground rules

- **No new required dependencies.** The base install must stay dependency-free; CI enforces it.
  Optional format support may add an extra, and the bar is high: one well-maintained package.
- **Match unstructured's behavior, and prove it.** If you change how elements are produced, run
  the compatibility comparison and include the before/after numbers in the pull request:

  ```bash
  python tests/corpus.py compat/out/corpus
  python scripts/compat.py dump tryworks compat/out/corpus compat/out/tryworks.json
  # in an environment with unstructured installed:
  python scripts/compat.py dump unstructured compat/out/corpus compat/out/upstream.json
  python scripts/compat.py compare compat/out/upstream.json compat/out/tryworks.json
  ```

  The "Upstream compatibility" workflow runs the same comparison on GitHub.
- **Write the code yourself.** tryworks implements unstructured's public API; it does not copy its
  source. Don't paste code from unstructured into a pull request.
- **Test documents are generated.** Add cases to `tests/corpus.py` or build files inside tests;
  don't commit documents you don't have the rights to.

## Reporting a mismatch

If tryworks and unstructured disagree on a document, open an issue with the document (or a
minimal reproduction) and both outputs. That's the most useful bug report this project can get.

"""Serve tryworks under the ``unstructured`` import names, for code you cannot edit.

Libraries such as LangChain's document loaders import ``unstructured.partition.auto`` directly.
Calling ``alias_as_unstructured()`` before they are imported makes those imports resolve to
tryworks. It only registers module aliases in this Python process; no package named
``unstructured`` is installed.
"""

from __future__ import annotations

import importlib
import sys

MODULE_ALIASES = {
    "unstructured": "tryworks",
    "unstructured.__version__": "tryworks.__version__",
    "unstructured.documents": "tryworks.documents",
    "unstructured.documents.coordinates": "tryworks.documents.coordinates",
    "unstructured.documents.elements": "tryworks.documents.elements",
    "unstructured.partition": "tryworks.partition",
    "unstructured.partition.auto": "tryworks.partition.auto",
    "unstructured.partition.csv": "tryworks.partition.csv",
    "unstructured.partition.docx": "tryworks.partition.docx",
    "unstructured.partition.email": "tryworks.partition.email",
    "unstructured.partition.html": "tryworks.partition.html",
    "unstructured.partition.json": "tryworks.partition.json",
    "unstructured.partition.md": "tryworks.partition.md",
    "unstructured.partition.pdf": "tryworks.partition.pdf",
    "unstructured.partition.pptx": "tryworks.partition.pptx",
    "unstructured.partition.text": "tryworks.partition.text",
    "unstructured.partition.text_type": "tryworks.partition.text_type",
    "unstructured.partition.tsv": "tryworks.partition.csv",
    "unstructured.partition.xlsx": "tryworks.partition.xlsx",
    "unstructured.chunking": "tryworks.chunking",
    "unstructured.chunking.base": "tryworks.chunking.base",
    "unstructured.chunking.basic": "tryworks.chunking.basic",
    "unstructured.chunking.title": "tryworks.chunking.title",
    "unstructured.cleaners": "tryworks.cleaners",
    "unstructured.cleaners.core": "tryworks.cleaners.core",
    "unstructured.staging": "tryworks.staging",
    "unstructured.staging.base": "tryworks.staging.base",
}


def alias_as_unstructured(force: bool = False) -> None:
    """Make ``import unstructured...`` resolve to the matching tryworks modules.

    Raises RuntimeError when the real unstructured package is already imported in this process,
    unless ``force=True``, because mixing the two would produce elements of different classes.
    """
    existing = sys.modules.get("unstructured")
    if existing is not None and not getattr(existing, "__name__", "").startswith("tryworks") and not force:
        raise RuntimeError(
            "The real 'unstructured' package is already imported; call alias_as_unstructured() "
            "before anything imports it, or pass force=True."
        )
    for alias, target in MODULE_ALIASES.items():
        sys.modules[alias] = importlib.import_module(target)

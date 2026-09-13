"""tryworks: partition documents into LLM-ready elements with zero required dependencies.

    from tryworks.partition.auto import partition
    from tryworks.chunking.title import chunk_by_title

    elements = partition("report.docx")
    chunks = chunk_by_title(elements, max_characters=1000)

The module layout mirrors ``unstructured``: replace ``unstructured.`` with ``tryworks.`` in imports,
or call ``tryworks.alias_as_unstructured()`` to serve code you cannot edit.
"""

from tryworks.__version__ import __version__
from tryworks.compat import alias_as_unstructured

__all__ = ["__version__", "alias_as_unstructured"]

from __future__ import annotations

from pathlib import Path

import pytest

from corpus import build_corpus


@pytest.fixture(scope="session")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return build_corpus(tmp_path_factory.mktemp("corpus"))


def kinds(elements) -> list[tuple[str, str]]:
    return [(e.category, e.text) for e in elements]

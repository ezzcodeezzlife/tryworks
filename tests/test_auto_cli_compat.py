from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tryworks.partition.auto import UnsupportedFileFormatError, detect_filetype, partition

SRC = Path(__file__).resolve().parents[1] / "src"


def _run(*args: str, code: str | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONIOENCODING": "utf-8"}
    cmd = [sys.executable, "-c", code] if code else [sys.executable, "-m", "tryworks", *args]
    return subprocess.run(cmd, capture_output=True, env=env, timeout=120)


class DescribeDetection:
    def it_uses_extensions(self, corpus):
        assert {kind: detect_filetype(filename=str(path)) for kind, path in corpus.items()} == {
            "html": "html", "md": "md", "txt": "txt", "csv": "csv", "eml": "eml",
            "pdf": "pdf", "docx": "docx", "pptx": "pptx", "xlsx": "xlsx",
        }

    @pytest.mark.parametrize("kind", ["pdf", "docx", "pptx", "xlsx", "html", "eml", "txt"])
    def it_sniffs_content_without_a_name(self, corpus, kind):
        data = corpus[kind].read_bytes()
        assert detect_filetype(file=io.BytesIO(data)) == kind

    def it_prefers_an_explicit_content_type(self):
        assert detect_filetype(file=io.BytesIO(b"a,b\n1,2\n"), content_type="text/csv; charset=utf-8") == "csv"

    def it_explains_unsupported_formats(self, tmp_path):
        legacy = tmp_path / "old.doc"
        legacy.write_bytes(b"\xd0\xcf\x11\xe0")
        with pytest.raises(UnsupportedFileFormatError, match="docx"):
            partition(filename=str(legacy))
        with pytest.raises(UnsupportedFileFormatError):
            partition(file=io.BytesIO(b"\x00\x01\x02\x03" * 100))


class DescribePartition:
    def it_routes_file_objects(self, corpus):
        elements = partition(file=io.BytesIO(corpus["docx"].read_bytes()), metadata_filename="board.docx")
        assert elements[1].text == "Quarterly Operations Memo"
        assert elements[1].metadata.filename == "board.docx"
        assert elements[1].metadata.filetype.endswith("wordprocessingml.document")

    def it_requires_exactly_one_source(self, corpus):
        with pytest.raises(ValueError):
            partition()
        with pytest.raises(ValueError):
            partition(filename=str(corpus["txt"]), file=io.BytesIO(b"x"))

    def it_refuses_non_http_urls(self):
        with pytest.raises(ValueError):
            partition(url="file:///etc/passwd")

    def it_can_use_random_ids(self, corpus):
        a = partition(filename=str(corpus["txt"]))
        b = partition(filename=str(corpus["txt"]), unique_element_ids=True)
        assert a[0].id == partition(filename=str(corpus["txt"]))[0].id
        assert len(b[0].id) == 36 and b[0].id != a[0].id


class DescribeCli:
    def it_prints_json_by_default(self, corpus):
        result = _run(str(corpus["md"]))
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data[0] == {**data[0], "type": "Title", "text": "Onboarding Guide"}

    def it_prints_text_and_chunks(self, corpus):
        result = _run(str(corpus["html"]), "--format", "text", "--chunking-strategy", "by_title", "--max-characters", "120")
        assert result.returncode == 0, result.stderr
        assert "Harbor Operations Update" in result.stdout.decode("utf-8")

    def it_reports_errors_with_exit_code_2(self, tmp_path):
        missing = _run(str(tmp_path / "missing.pdf"))
        assert missing.returncode == 2 and b"tryworks:" in missing.stderr


class DescribeAlias:
    def it_serves_unstructured_imports(self, corpus):
        code = (
            "import tryworks; tryworks.alias_as_unstructured()\n"
            "from unstructured.partition.auto import partition\n"
            "from unstructured.chunking.title import chunk_by_title\n"
            "from unstructured.staging.base import elements_to_json\n"
            "import unstructured.documents.elements as E\n"
            f"els = partition(filename={str(corpus['txt'])!r})\n"
            "assert isinstance(els[0], E.Title)\n"
            "print(type(els[0]).__module__, len(chunk_by_title(els)))\n"
        )
        result = _run(code=code)
        assert result.returncode == 0, result.stderr
        assert result.stdout.decode().split() == ["tryworks.documents.elements", "1"]

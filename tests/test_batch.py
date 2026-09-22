import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from local_doc_converter.batch import BatchProcessor
from local_doc_converter.converter import DocumentConverter
from local_doc_converter.models import UploadedDocument
from test_converter import FakePandoc


def test_batch_of_five_and_zip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    uploads = [UploadedDocument(f"示例{i}.txt", f"第{i}章 内容".encode()) for i in range(1, 6)]
    processor = BatchProcessor(DocumentConverter(pandoc=FakePandoc()))

    progress = []
    result = processor.process_uploads(
        uploads,
        "markdown",
        fake_home / "output",
        on_progress=lambda completed, total, name: progress.append((completed, total, name)),
    )

    assert result.successful_count == 5
    with zipfile.ZipFile(BytesIO(result.zip_bytes)) as archive:
        names = archive.namelist()
    assert "batch_report.json" in names
    assert len([name for name in names if name.startswith("outputs/")]) == 5
    assert progress[0] == (1, 5, "示例1.txt")
    assert progress[-1] == (5, 5, "示例5.txt")


def test_same_format_is_skipped_not_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    processor = BatchProcessor(DocumentConverter(pandoc=FakePandoc()))

    result = processor.process_uploads(
        [UploadedDocument("已经是文本.txt", "内容".encode())],
        "txt",
        fake_home / "output",
    )

    assert result.successful_count == 0
    assert result.skipped_count == 1
    assert result.failed_count == 0
    assert result.results[0].report.error is None

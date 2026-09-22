import base64
import hashlib
import shutil
from pathlib import Path

import pytest
from docx import Document

from local_doc_converter.converter import DocumentConverter


pytestmark = pytest.mark.skipif(shutil.which("pandoc") is None, reason="本机未安装 Pandoc")

_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_local_image_round_trip_and_media_extraction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    source_dir = fake_home / "inputs"
    source_dir.mkdir()
    image = source_dir / "pixel.png"
    image.write_bytes(_PNG_1X1)
    markdown = source_dir / "with-image.md"
    markdown.write_text("# 图片示例\n\n![像素图片](pixel.png)\n", encoding="utf-8")
    source_hash = hashlib.sha256(markdown.read_bytes()).hexdigest()

    converter = DocumentConverter()
    to_docx = converter.convert(markdown, "docx", fake_home / "output")
    assert to_docx.report.success, to_docx.report.error
    assert to_docx.report.stats.images == 1
    assert to_docx.output_path is not None
    assert len(Document(to_docx.output_path).inline_shapes) == 1

    to_markdown = converter.convert(to_docx.output_path, "markdown", fake_home / "output")
    assert to_markdown.report.success, to_markdown.report.error
    assert to_markdown.asset_paths
    assert any(path.is_file() for path in to_markdown.asset_paths[0].rglob("*"))
    assert to_markdown.output_path is not None
    assert "assets" in to_markdown.output_path.read_text(encoding="utf-8")
    assert hashlib.sha256(markdown.read_bytes()).hexdigest() == source_hash

import json
from pathlib import Path

import pytest

from local_doc_converter.converter import DocumentConverter


class FakePandoc:
    def require(self):
        return "fake-pandoc"

    def read_to_ast(self, source_path, source_format, ast_path, *, cwd, extract_media=None):
        document = {
            "pandoc-api-version": [1, 23, 1],
            "meta": {},
            "blocks": [{"t": "Header", "c": [1, ["", [], []], [{"t": "Str", "c": "标题"}]]}],
        }
        ast_path.write_text(json.dumps(document), encoding="utf-8")
        return document

    def write_from_ast(self, ast_path, target_format, output_path, *, cwd, resource_paths=None):
        output_path.write_text("# 标题\n", encoding="utf-8")


def test_txt_to_markdown_report_and_no_overwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    source = fake_home / "input.txt"
    source.write_text("第一章 测试\n\n正文。", encoding="utf-8")
    output = fake_home / "output"
    converter = DocumentConverter(pandoc=FakePandoc())

    first = converter.convert(source, "markdown", output)
    second = converter.convert(source, "markdown", output)

    assert first.report.success
    assert first.report.detected_encoding in {"utf-8", "utf-8-sig"}
    assert first.report.stats.headings == 1
    assert first.output_path and first.output_path.read_text(encoding="utf-8") == "# 标题\n"
    assert first.output_path.name == "input.md"
    assert second.output_path and second.output_path != first.output_path
    assert second.output_path.name == "input_2.md"
    assert source.read_text(encoding="utf-8").startswith("第一章")

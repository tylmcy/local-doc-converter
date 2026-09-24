import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

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


class PandocMustNotRun:
    def require(self):
        raise AssertionError("PDF → TXT 不应调用 Pandoc")


def _write_text_pdf(path: Path) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 14 Tf 72 720 Td (Native PDF content for TXT output.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    with path.open("wb") as stream:
        writer.write(stream)


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


def test_unsafe_docx_fails_before_pandoc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    source = fake_home / "unsafe.docx"
    source.write_text("not a docx zip", encoding="utf-8")

    result = DocumentConverter(pandoc=FakePandoc()).convert(source, "markdown", fake_home / "output")

    assert not result.report.success
    assert result.output_path is None
    assert result.report_path is not None
    assert "不是有效的 DOCX ZIP" in (result.report.error or "")


def test_text_pdf_to_txt_does_not_require_pandoc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    source = fake_home / "original-name.pdf"
    _write_text_pdf(source)

    result = DocumentConverter(pandoc=PandocMustNotRun()).convert(
        source, "txt", fake_home / "output"
    )

    assert result.report.success
    assert result.output_path is not None
    assert result.output_path.name == "original-name.txt"
    assert result.output_path.read_text(encoding="utf-8") == "Native PDF content for TXT output.\n"
    assert result.report.details["native_page_numbers"] == [1]
    assert result.report.details["ocr_page_numbers"] == []


def test_pdf_rejects_non_txt_target_before_processing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    source = fake_home / "sample.pdf"
    _write_text_pdf(source)

    result = DocumentConverter(pandoc=PandocMustNotRun()).convert(
        source, "markdown", fake_home / "output"
    )

    assert not result.report.success
    assert "仅支持转换为 TXT" in (result.report.error or "")

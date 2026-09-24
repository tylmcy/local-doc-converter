import hashlib
import json
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_doc_converter.batch import BatchProcessor
from local_doc_converter.converter import DocumentConverter
from local_doc_converter.errors import OcrUnavailableError, PdfExtractionError
from local_doc_converter.models import UploadedDocument
from test_converter import PandocMustNotRun, _write_text_pdf


def _result(text: str = "完整文字\n") -> SimpleNamespace:
    return SimpleNamespace(
        text=text, table_count=0, image_count=0, warnings=(),
        report_details=lambda: {"page_count": 2, "ocr_page_numbers": [1, 2]},
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (PdfExtractionError("PDF 第 2 页渲染/OCR 失败：识别错误"), "第 2 页"),
        (PdfExtractionError("PDF 第 2 页渲染/OCR 超过 60 秒"), "第 2 页"),
        (OcrUnavailableError("本地模型未就绪"), "本地模型未就绪"),
    ],
)
def test_later_ocr_failure_never_delivers_partial_txt_or_zip_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, expected: str
):
    monkeypatch.setenv("HOME", str(tmp_path))

    class PdfConverter:
        def convert(self, path: Path):
            if path.name == "失败.pdf":
                # 模拟前一页已经完成，后一页失败；调用者不得写出前页文本。
                partial = "第一页已识别"
                assert partial
                raise error
            return _result()

    source = tmp_path / "source.pdf"
    _write_text_pdf(source)
    pdf_bytes = source.read_bytes()
    batch = BatchProcessor(DocumentConverter(pandoc=PandocMustNotRun(), pdf_converter=PdfConverter()))
    result = batch.process_uploads(
        [UploadedDocument("失败.pdf", pdf_bytes), UploadedDocument("正常.pdf", pdf_bytes)],
        "txt", tmp_path / "output",
    )

    assert result.failed_count == 1
    assert result.successful_count == 1
    assert expected in (result.results[0].report.error or "")
    assert result.results[0].output_path is None
    assert result.results[0].report.target_file == ""
    assert not (tmp_path / "output" / "失败.txt").exists()
    with zipfile.ZipFile(BytesIO(result.zip_bytes)) as archive:
        assert "outputs/失败.txt" not in archive.namelist()
        assert "outputs/正常.txt" in archive.namelist()
        report = json.loads(archive.read("batch_report.json"))
        assert report["failed"] == 1


def test_output_write_error_preserves_existing_file_and_leaves_no_partial_txt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "original.pdf"
    _write_text_pdf(source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / "output"
    output.mkdir()
    old = output / "original.txt"
    old.write_text("旧结果", encoding="utf-8")
    old_hash = hashlib.sha256(old.read_bytes()).hexdigest()

    import local_doc_converter.converter as module

    original_link = module.os.link

    def fail_txt_link(source_path, destination_path):
        if Path(destination_path).suffix == ".txt":
            raise OSError("模拟磁盘写入中断")
        return original_link(source_path, destination_path)

    monkeypatch.setattr(module.os, "link", fail_txt_link)
    result = DocumentConverter(pandoc=PandocMustNotRun(), pdf_converter=SimpleNamespace(convert=lambda path: _result())).convert(source, "txt", output)

    assert not result.report.success
    assert result.output_path is None
    assert "磁盘写入中断" in (result.report.error or "")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    assert hashlib.sha256(old.read_bytes()).hexdigest() == old_hash
    assert not (output / "original_2.txt").exists()
    assert not list(output.glob(".local_doc_stage_*"))


def test_report_write_failure_withdraws_new_txt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "original.pdf"
    _write_text_pdf(source)

    import local_doc_converter.converter as module

    original_link = module.os.link

    def fail_report_link(source_path, destination_path):
        if str(destination_path).endswith("_report.json"):
            raise OSError("模拟报告写入失败")
        return original_link(source_path, destination_path)

    monkeypatch.setattr(module.os, "link", fail_report_link)
    result = DocumentConverter(pandoc=PandocMustNotRun(), pdf_converter=SimpleNamespace(convert=lambda path: _result())).convert(source, "txt", tmp_path / "output")

    assert not result.report.success
    assert "报告写入失败" in (result.report.error or "")
    assert result.output_path is None
    assert result.report_path is None
    assert not list((tmp_path / "output").glob("*.txt"))
    assert not list((tmp_path / "output").glob(".local_doc_stage_*"))


def test_pdf_name_collision_keeps_old_result_and_uses_next_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "original.pdf"
    _write_text_pdf(source)
    output = tmp_path / "output"
    output.mkdir()
    old = output / "original.txt"
    old.write_text("旧结果", encoding="utf-8")

    result = DocumentConverter(pandoc=PandocMustNotRun(), pdf_converter=SimpleNamespace(convert=lambda path: _result())).convert(source, "txt", output)

    assert result.report.success
    assert result.output_path is not None and result.output_path.name == "original_2.txt"
    assert result.report.target_file == "original_2.txt"
    assert old.read_text(encoding="utf-8") == "旧结果"

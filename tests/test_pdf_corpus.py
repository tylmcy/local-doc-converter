import json
from pathlib import Path

import pytest

from local_doc_converter.errors import ValidationError
from local_doc_converter.pdf.corpus import load_pdf_corpus_manifest, verify_pdf_corpus_file
from local_doc_converter.pdf.layout import extract_page_layout
from local_doc_converter.pdf.pipeline import PdfToTextConverter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = PROJECT_ROOT / "quality" / "pdf_corpus" / "manifest.json"


def test_real_pdf_corpus_manifest_is_valid_and_pinned():
    manifest = load_pdf_corpus_manifest(MANIFEST)

    assert len(manifest.samples) == 9
    assert {sample.language for sample in manifest.samples} == {
        "en",
        "zh-Hans",
        "zh-Hans-en",
        "zh-Hant-en",
    }
    for sample in manifest.samples:
        if sample.source_revision_kind == "git-commit":
            assert sample.source_revision in sample.source_url
        assert sample.redistribution == "local-only"
        assert sample.manual_review["status"] in {
            "pass",
            "pass-with-loss",
            "known-issue",
            "needs-review",
        }

    scan = next(sample for sample in manifest.samples if sample.sample_id == "wikimedia-chinese-scan")
    assert scan.acceptance["expected_pdf_kind"] == "scanned"
    assert scan.acceptance["expected_ocr_pages"] == 5
    modern = next(sample for sample in manifest.samples if sample.sample_id == "yunnan-modern-scan")
    assert modern.acceptance["expected_ocr_pages"] == 4


def test_manifest_rejects_path_traversal(tmp_path: Path):
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["samples"][0]["filename"] = "../outside.pdf"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="文件名不安全"):
        load_pdf_corpus_manifest(path)


def test_manifest_rejects_unpinned_source_revision(tmp_path: Path):
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["samples"][0]["source_revision"] = "stable"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="固定源码版本无效"):
        load_pdf_corpus_manifest(path)


def test_integrity_check_rejects_wrong_content(tmp_path: Path):
    sample = load_pdf_corpus_manifest(MANIFEST).samples[0]
    path = tmp_path / sample.filename
    path.write_bytes(b"%PDF-wrong")

    errors = verify_pdf_corpus_file(sample, path)

    assert any("文件大小不一致" in error for error in errors)
    assert any("SHA-256 不一致" in error for error in errors)


def test_local_federal_three_column_regression():
    source = PROJECT_ROOT / "quality" / "pdf_corpus" / "downloads" / "federal-register-2020-17221.pdf"
    if not source.is_file():
        pytest.skip("真实 PDF 样例未显式下载")
    import pdfplumber

    with pdfplumber.open(source) as document:
        first = extract_page_layout(document.pages[0])
        sixth = extract_page_layout(document.pages[5])
        seventh = extract_page_layout(document.pages[6])
    assert (first.column_count, sixth.column_count, seventh.column_count) == (3, 3, 3)
    assert first.structured_table_count == 0
    assert first.text.index("This section of the FEDERAL REGISTER") < first.text.index(
        "Federal eRulemaking Portal"
    ) < first.text.index("proposal, explain the reason")
    assert "replace the existing Airspeed Unreliable\nparagraph with the information" in seventh.text


def test_local_tsinghua_cross_page_duplicate_regression():
    source = PROJECT_ROOT / "quality" / "pdf_corpus" / "downloads" / "tsinghua-fmba-guide.pdf"
    if not source.is_file():
        pytest.skip("真实 PDF 样例未显式下载")
    result = PdfToTextConverter().convert(source)
    for marker in ("6. 在职证明", "12. 百分制成绩与等级成绩对照说明", "13. 学校更名证明"):
        assert result.text.count(marker) == 1
    assert sum("完全重复" in warning for warning in result.warnings) == 3

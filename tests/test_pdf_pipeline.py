from pathlib import Path

from local_doc_converter.pdf.classify import (
    PdfClassification,
    PdfPageClassification,
    PdfPageKind,
)
from local_doc_converter.pdf.extract import NativePageText, NativePdfText
from local_doc_converter.pdf.ocr import OcrPageResult
from local_doc_converter.pdf.ocr_worker import IsolatedOcrResult
from local_doc_converter.pdf.pipeline import (
    PdfToTextConverter,
    remove_repeated_adjacent_page_prefixes,
    remove_repeated_headers_and_footers,
    remove_repeated_vertical_marginals,
)
from local_doc_converter.pdf.preflight import PdfInspection


def _classification(kinds: list[PdfPageKind]) -> PdfClassification:
    inspection = PdfInspection("1.7", 100, len(kinds), 10, 612, 792)
    pages = tuple(
        PdfPageClassification(
            page_number=index,
            kind=kind,
            text_char_count=0 if kind is PdfPageKind.SCANNED else 40,
            image_count=1 if kind in {PdfPageKind.SCANNED, PdfPageKind.MIXED} else 0,
            image_coverage_ratio=1.0 if kind in {PdfPageKind.SCANNED, PdfPageKind.MIXED} else 0.0,
            width_points=612,
            height_points=792,
        )
        for index, kind in enumerate(kinds, start=1)
    )
    return PdfClassification(
        document_kind=PdfPageKind.MIXED,
        page_count=len(pages),
        text_pages=sum(page.kind is PdfPageKind.TEXT for page in pages),
        scanned_pages=sum(page.kind is PdfPageKind.SCANNED for page in pages),
        mixed_pages=sum(page.kind is PdfPageKind.MIXED for page in pages),
        empty_pages=sum(page.kind is PdfPageKind.EMPTY for page in pages),
        pages=pages,
        inspection=inspection,
    )


class FakeOcr:
    def __init__(self):
        self.calls: list[int] = []

    def recognize_page(self, pdf_path: Path, page_number: int) -> OcrPageResult:
        self.calls.append(page_number)
        return OcrPageResult(page_number, f"OCR 第 {page_number} 页", 1, 0.92)


def test_uses_native_text_and_only_falls_back_for_required_pages():
    classification = _classification(
        [PdfPageKind.TEXT, PdfPageKind.SCANNED, PdfPageKind.TEXT, PdfPageKind.EMPTY]
    )
    native = NativePdfText(
        pages=(
            NativePageText(1, "正常原生文字第一页，内容足够。", 16, 1, 0, 0),
            NativePageText(2, "", 0, 0, 0, 1),
            NativePageText(3, "技" * 30, 30, 1, 0, 0),
            NativePageText(4, "", 0, 0, 0, 0),
        ),
        table_count=0,
        image_count=1,
    )
    ocr = FakeOcr()
    converter = PdfToTextConverter(
        ocr_engine=ocr,
        classifier=lambda path: classification,
        native_extractor=lambda path: native,
    )

    result = converter.convert(Path("sample.pdf"))

    assert result.native_pages == (1,)
    assert result.ocr_pages == (2, 3)
    assert result.empty_pages == (4,)
    assert ocr.calls == [2, 3]
    assert result.ocr_isolated is False
    assert result.text == "正常原生文字第一页，内容足够。\n\nOCR 第 2 页\n\nOCR 第 3 页\n"
    assert any("高频重复" in warning for warning in result.warnings)


def test_batches_all_ocr_pages_into_one_isolated_runner_call():
    classification = _classification([PdfPageKind.SCANNED, PdfPageKind.SCANNED])
    native = NativePdfText(
        pages=(
            NativePageText(1, "", 0, 0, 0, 1),
            NativePageText(2, "", 0, 0, 0, 1),
        ),
        table_count=0,
        image_count=2,
    )
    calls: list[tuple[int, ...]] = []

    def runner(path: Path, pages: tuple[int, ...]) -> IsolatedOcrResult:
        calls.append(pages)
        return IsolatedOcrResult(
            pages=tuple(OcrPageResult(page, f"隔离 OCR {page}", 1, 0.9) for page in pages),
            worker_pid=12345,
            duration_seconds=1.25,
            startup_timeout_seconds=30,
            page_timeout_seconds=60,
        )

    result = PdfToTextConverter(
        ocr_runner=runner,
        classifier=lambda path: classification,
        native_extractor=lambda path: native,
    ).convert(Path("sample.pdf"))

    assert calls == [(1, 2)]
    assert result.text == "隔离 OCR 1\n\n隔离 OCR 2\n"
    assert result.ocr_isolated is True
    assert result.ocr_duration_seconds == 1.25
    assert result.ocr_startup_timeout_seconds == 30
    assert result.ocr_page_timeout_seconds == 60


def test_removes_only_repeated_marginal_lines_from_three_or_more_pages():
    cleaned, warnings = remove_repeated_headers_and_footers(
        [
            "项目报告\n第一页正文\n1",
            "项目报告\n第二页正文\n2",
            "项目报告\n第三页正文\n3",
        ]
    )

    assert cleaned == ["第一页正文\n1", "第二页正文\n2", "第三页正文\n3"]
    assert warnings == ("已删除多页重复的页眉候选文本。",)


def test_page_results_match_final_cleaned_output():
    classification = _classification([PdfPageKind.SCANNED] * 3)
    native = NativePdfText(
        pages=tuple(NativePageText(index, "", 0, 0, 0, 1) for index in range(1, 4)),
        table_count=0,
        image_count=3,
    )

    class RepeatedHeaderOcr:
        def recognize_page(self, pdf_path: Path, page_number: int) -> OcrPageResult:
            return OcrPageResult(page_number, f"重复页眉\n第 {page_number} 页正文", 2, 0.9)

    result = PdfToTextConverter(
        ocr_engine=RepeatedHeaderOcr(),
        classifier=lambda path: classification,
        native_extractor=lambda path: native,
    ).convert(Path("sample.pdf"))

    assert [page.text for page in result.pages] == ["第 1 页正文", "第 2 页正文", "第 3 页正文"]
    assert result.text == "第 1 页正文\n\n第 2 页正文\n\n第 3 页正文\n"


def test_removes_long_adjacent_page_prefix_even_when_it_repeats_prior_start():
    repeated = [f"第 {index} 条较长的跨页重复内容，包含足够文字用于保守检测。" for index in range(12)]
    previous = "\n".join([*repeated, "上一页独有结尾"])
    current = "\n".join([*repeated, "下一页新内容甲", "下一页新内容乙", "下一页新内容丙"])

    cleaned, warnings = remove_repeated_adjacent_page_prefixes(
        [previous, current], ["native", "native"]
    )

    assert cleaned[0] == previous
    assert cleaned[1] == "下一页新内容甲\n下一页新内容乙\n下一页新内容丙"
    assert "12 行前缀" in warnings[0]


def test_keeps_short_repetition_and_ocr_page_content():
    repeated = [f"第 {index} 条相同说明" for index in range(4)]
    previous = "\n".join([*repeated, *[f"前页内容{index}" for index in range(10)]])
    current = "\n".join([*repeated, *[f"本页内容{index}" for index in range(10)]])

    assert remove_repeated_adjacent_page_prefixes(
        [previous, current], ["native", "native"]
    ) == ([previous, current], ())
    assert remove_repeated_adjacent_page_prefixes(
        [previous, current], ["native", "ocr"]
    ) == ([previous, current], ())


def test_removes_fuzzy_repeated_vertical_headers_but_keeps_unique_title():
    pages = {
        1: OcrPageResult(1, "附\n錄\n公禱書四百年之紀念\n本页独有标题\n正文甲", 5, 0.8, marginal_line_indices=(0, 1, 2, 3)),
        2: OcrPageResult(2, "附\n錄\n公祷书四百年之纪念\n正文乙", 4, 0.8, marginal_line_indices=(0, 1, 2)),
        3: OcrPageResult(3, "附\n錄\n公禱書四百年之纪念\n正文丙", 4, 0.8, marginal_line_indices=(0, 1, 2)),
    }

    cleaned, warnings = remove_repeated_vertical_marginals(pages)

    assert cleaned[1] == "本页独有标题\n正文甲"
    assert cleaned[2] == "正文乙"
    assert cleaned[3] == "正文丙"
    assert set(warnings) == {1, 2, 3}

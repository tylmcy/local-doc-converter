"""PDF → TXT 的按页原生提取与 OCR 回退编排。"""

from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from functools import partial
from pathlib import Path
from typing import Callable

from ..errors import PdfExtractionError
from .classify import PdfClassification, PdfPageKind
from .extract import NativePdfText
from .ocr import OCR_PROFILES, OcrEngine, OcrPageResult
from .ocr_worker import IsolatedOcrResult, OcrBatchRunner, ocr_pdf_pages_isolated
from .parse_worker import IsolatedPdfParseResult, parse_pdf_isolated
from .quality import NativeTextQuality, assess_native_text


@dataclass(frozen=True, slots=True)
class PdfPageTextResult:
    page_number: int
    source: str
    text: str
    native_quality: NativeTextQuality | None = None
    ocr_confidence: float | None = None
    warnings: tuple[str, ...] = ()
    column_count: int | None = None
    structured_table_count: int = 0
    complex_table_count: int = 0
    table_fallback_count: int = 0
    printed_page_numbers: tuple[str, ...] = ()
    ocr_recognition_model: str | None = None
    ocr_preprocessing_profile: str | None = None


@dataclass(frozen=True, slots=True)
class PdfTextResult:
    text: str
    classification: PdfClassification
    pages: tuple[PdfPageTextResult, ...]
    native_pages: tuple[int, ...]
    ocr_pages: tuple[int, ...]
    empty_pages: tuple[int, ...]
    table_count: int
    image_count: int
    double_column_pages: tuple[int, ...]
    complex_table_count: int
    table_fallback_count: int
    warnings: tuple[str, ...]
    parser_isolated: bool
    parse_duration_seconds: float
    ocr_isolated: bool | None
    ocr_duration_seconds: float
    ocr_startup_timeout_seconds: float | None
    ocr_page_timeout_seconds: float | None
    ocr_profile: str = "standard"

    def report_details(self) -> dict[str, object]:
        return {
            "pdf_kind": self.classification.document_kind.value,
            "page_count": self.classification.page_count,
            "text_pages": self.classification.text_pages,
            "scanned_pages": self.classification.scanned_pages,
            "mixed_pages": self.classification.mixed_pages,
            "empty_page_count": self.classification.empty_pages,
            "native_page_numbers": list(self.native_pages),
            "ocr_page_numbers": list(self.ocr_pages),
            "skipped_empty_page_numbers": list(self.empty_pages),
            "double_column_page_numbers": list(self.double_column_pages),
            "structured_table_count": self.table_count,
            "complex_table_count": self.complex_table_count,
            "table_fallback_count": self.table_fallback_count,
            "parser_isolated": self.parser_isolated,
            "parse_duration_seconds": self.parse_duration_seconds,
            "ocr_isolated": self.ocr_isolated,
            "ocr_duration_seconds": self.ocr_duration_seconds,
            "ocr_startup_timeout_seconds": self.ocr_startup_timeout_seconds,
            "ocr_page_timeout_seconds": self.ocr_page_timeout_seconds,
            "ocr_profile": self.ocr_profile,
            "pages": [
                {
                    "page_number": page.page_number,
                    "source": page.source,
                    "native_quality_score": (
                        page.native_quality.score if page.native_quality is not None else None
                    ),
                    "ocr_mean_confidence": page.ocr_confidence,
                    "column_count": page.column_count,
                    "structured_table_count": page.structured_table_count,
                    "complex_table_count": page.complex_table_count,
                    "table_fallback_count": page.table_fallback_count,
                    "printed_page_numbers": list(page.printed_page_numbers),
                    "ocr_recognition_model": page.ocr_recognition_model,
                    "ocr_preprocessing_profile": page.ocr_preprocessing_profile,
                    "warnings": list(page.warnings),
                }
                for page in self.pages
            ],
        }


def _deduplicate(items: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in items if item))


def _marginal_line(text: str, *, first: bool) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    line = lines[0] if first else lines[-1]
    return line if len(line) <= 120 else None


def remove_repeated_headers_and_footers(
    page_texts: list[str],
) -> tuple[list[str], tuple[str, ...]]:
    """仅删除 3 页以上、超过半数页完全相同的首行或尾行。"""
    if len(page_texts) < 3:
        return page_texts, ()
    threshold = max(2, math.ceil(len(page_texts) * 0.5))
    header_counts = Counter(
        line for text in page_texts if (line := _marginal_line(text, first=True)) is not None
    )
    footer_counts = Counter(
        line for text in page_texts if (line := _marginal_line(text, first=False)) is not None
    )
    repeated_headers = {line for line, count in header_counts.items() if count >= threshold}
    repeated_footers = {line for line, count in footer_counts.items() if count >= threshold}
    if not repeated_headers and not repeated_footers:
        return page_texts, ()

    cleaned: list[str] = []
    for text in page_texts:
        lines = text.splitlines()
        nonempty = [index for index, line in enumerate(lines) if line.strip()]
        if nonempty and lines[nonempty[0]].strip() in repeated_headers:
            lines[nonempty[0]] = ""
        nonempty = [index for index, line in enumerate(lines) if line.strip()]
        if nonempty and lines[nonempty[-1]].strip() in repeated_footers:
            lines[nonempty[-1]] = ""
        cleaned.append("\n".join(lines).strip())

    warnings: list[str] = []
    if repeated_headers:
        warnings.append("已删除多页重复的页眉候选文本。")
    if repeated_footers:
        warnings.append("已删除多页重复的页脚候选文本。")
    return cleaned, tuple(warnings)


def remove_repeated_adjacent_page_prefixes(
    page_texts: list[str], page_sources: list[str]
) -> tuple[list[str], tuple[str, ...]]:
    """只移除相邻原生文字页中大块、逐行完全相同的当前页前缀。"""
    if len(page_texts) != len(page_sources):
        raise ValueError("页文本和来源数量不一致。")
    cleaned = list(page_texts)
    warnings: list[str] = []
    for index in range(1, len(cleaned)):
        if page_sources[index - 1] != "native" or page_sources[index] != "native":
            continue
        previous = [line.strip() for line in cleaned[index - 1].splitlines() if line.strip()]
        current = [line.strip() for line in cleaned[index].splitlines() if line.strip()]
        if len(previous) < 10 or len(current) < 13:
            continue
        longest = 0
        for start in range(len(previous)):
            length = 0
            while (
                length < len(current)
                and start + length < len(previous)
                and current[length] == previous[start + length]
            ):
                length += 1
            longest = max(longest, length)
        # 单行页眉或合法的重复说明不应触发；保留当前页至少三行新内容。
        if longest < 10 or len(current) - longest < 3:
            continue
        if sum(len(line) for line in current[:longest]) < 200:
            continue
        cleaned[index] = "\n".join(current[longest:])
        warnings.append(
            f"第 {index + 1} 页已移除与上一页完全重复的 {longest} 行前缀文本。"
        )
    return cleaned, tuple(warnings)


def remove_repeated_vertical_marginals(
    ocr_results: dict[int, OcrPageResult],
) -> tuple[dict[int, str], dict[int, str]]:
    """仅删除跨至少三页重复的竖排边缘文字，保留每页独有的边缘标题。"""
    texts = {number: result.text for number, result in ocr_results.items()}
    candidates: list[tuple[int, int, str]] = []
    for page_number, result in ocr_results.items():
        lines = result.text.splitlines()
        for line_index in result.marginal_line_indices:
            if 0 <= line_index < len(lines):
                normalized = "".join(character for character in lines[line_index] if character.isalnum())
                if normalized:
                    candidates.append((page_number, line_index, normalized))
    if len(ocr_results) < 3 or not candidates:
        return texts, {}

    threshold = max(3, math.ceil(len(ocr_results) * 0.5))
    removed: dict[int, set[int]] = {}
    for page_number, line_index, normalized in candidates:
        matching_pages = {
            other_page
            for other_page, _, other_text in candidates
            if SequenceMatcher(None, normalized, other_text).ratio()
            >= (0.65 if min(len(normalized), len(other_text)) >= 8 else 0.9)
        }
        if len(matching_pages) >= threshold:
            removed.setdefault(page_number, set()).add(line_index)

    warnings: dict[int, str] = {}
    for page_number, indices in removed.items():
        lines = texts[page_number].splitlines()
        texts[page_number] = "\n".join(
            line for index, line in enumerate(lines) if index not in indices
        )
        warnings[page_number] = f"第 {page_number} 页已移除跨页重复的竖排页边标题。"
    return texts, warnings


class PdfToTextConverter:
    def __init__(
        self,
        ocr_engine: OcrEngine | None = None,
        *,
        ocr_runner: OcrBatchRunner | None = None,
        parser: Callable[[Path], IsolatedPdfParseResult] | None = None,
        classifier: Callable[[Path], PdfClassification] | None = None,
        native_extractor: Callable[[Path], NativePdfText] | None = None,
        ocr_profile: str = "standard",
    ) -> None:
        if ocr_profile not in OCR_PROFILES:
            raise ValueError(f"不支持的 OCR 预处理模式：{ocr_profile}")
        if (classifier is None) != (native_extractor is None):
            raise ValueError("测试注入时必须同时提供 classifier 和 native_extractor。")
        if parser is not None and classifier is not None:
            raise ValueError("parser 不能与 classifier/native_extractor 同时注入。")
        if ocr_engine is not None and ocr_runner is not None:
            raise ValueError("ocr_engine 不能与 ocr_runner 同时注入。")
        self._ocr_engine = ocr_engine
        self._ocr_runner = ocr_runner or partial(ocr_pdf_pages_isolated, profile=ocr_profile)
        self._ocr_profile = ocr_profile
        self._classifier = classifier
        self._native_extractor = native_extractor
        self._parser = parser or parse_pdf_isolated

    def convert(self, path: Path) -> PdfTextResult:
        path = Path(path)
        if self._classifier is not None and self._native_extractor is not None:
            # 单元测试可注入纯内存结果；生产路径默认始终使用子进程。
            classification = self._classifier(path)
            native = self._native_extractor(path)
            parser_isolated = False
            parse_duration_seconds = 0.0
        else:
            parsed = self._parser(path)
            classification = parsed.classification
            native = parsed.native_text
            parser_isolated = True
            parse_duration_seconds = parsed.duration_seconds
        if len(native.pages) != classification.page_count:
            raise PdfExtractionError("PDF 分类与文字提取得到的页数不一致。")

        native_by_page = {page.page_number: page for page in native.pages}
        warnings = list(classification.inspection.warnings) + list(native.warnings)
        native_pages: list[int] = []
        ocr_pages: list[int] = []
        empty_pages: list[int] = []
        mode_by_page: dict[int, str] = {}
        quality_by_page: dict[int, NativeTextQuality] = {}
        warnings_by_page: dict[int, list[str]] = {}

        for page_info in classification.pages:
            page_number = page_info.page_number
            native_page = native_by_page[page_number]
            page_warnings = list(page_info.warnings) + list(native_page.warnings)

            if page_info.kind is PdfPageKind.EMPTY:
                empty_pages.append(page_number)
                mode_by_page[page_number] = "empty"
                warnings_by_page[page_number] = page_warnings
                continue

            quality = assess_native_text(native_page.text)
            quality_by_page[page_number] = quality
            needs_ocr = page_info.kind is PdfPageKind.SCANNED or not quality.acceptable
            if not needs_ocr:
                native_pages.append(page_number)
                mode_by_page[page_number] = "native"
                if page_info.kind is PdfPageKind.MIXED and page_info.image_count:
                    page_warnings.append(
                        f"第 {page_number} 页的原生文字层质量正常，"
                        "未对页内图片重复 OCR；图片中额外文字可能未提取。"
                    )
                warnings_by_page[page_number] = page_warnings
                continue

            ocr_pages.append(page_number)
            mode_by_page[page_number] = "ocr"
            if page_info.kind is not PdfPageKind.SCANNED:
                page_warnings.extend(
                    f"第 {page_number} 页：{warning}已改用离线 OCR。"
                    for warning in quality.warnings
                )
            warnings_by_page[page_number] = page_warnings

        ocr_results: dict[int, OcrPageResult] = {}
        ocr_isolated: bool | None = None
        ocr_duration_seconds = 0.0
        ocr_startup_timeout_seconds: float | None = None
        ocr_page_timeout_seconds: float | None = None
        if ocr_pages:
            if self._ocr_profile == "old_print":
                warnings.append(
                    "已启用实验性旧印刷体增强（300 DPI 与轻度去噪）；"
                    "请与默认模式逐页对照，不能仅凭 OCR 置信度判断准确率。"
                )
            if self._ocr_engine is not None:
                started = time.monotonic()
                for page_number in ocr_pages:
                    ocr_results[page_number] = self._ocr_engine.recognize_page(path, page_number)
                ocr_duration_seconds = round(time.monotonic() - started, 3)
                ocr_isolated = False
            else:
                isolated: IsolatedOcrResult = self._ocr_runner(path, tuple(ocr_pages))
                ocr_results = {page.page_number: page for page in isolated.pages}
                if tuple(ocr_results) != tuple(ocr_pages):
                    raise PdfExtractionError("OCR 子进程返回的页码与请求不一致。")
                ocr_isolated = True
                ocr_duration_seconds = isolated.duration_seconds
                ocr_startup_timeout_seconds = isolated.startup_timeout_seconds
                ocr_page_timeout_seconds = isolated.page_timeout_seconds

        page_results: list[PdfPageTextResult] = []
        cleaned_ocr_texts, vertical_marginal_warnings = remove_repeated_vertical_marginals(
            ocr_results
        )
        for page_info in classification.pages:
            page_number = page_info.page_number
            mode = mode_by_page[page_number]
            page_warnings = warnings_by_page[page_number]
            if mode == "empty":
                page_result = PdfPageTextResult(
                    page_number,
                    "empty",
                    "",
                    warnings=tuple(page_warnings),
                )
            elif mode == "native":
                page_result = PdfPageTextResult(
                    page_number,
                    "native",
                    native_by_page[page_number].text,
                    native_quality=quality_by_page[page_number],
                    warnings=tuple(page_warnings),
                    column_count=native_by_page[page_number].column_count,
                    structured_table_count=native_by_page[page_number].structured_table_count,
                    complex_table_count=native_by_page[page_number].complex_table_count,
                    table_fallback_count=native_by_page[page_number].table_fallback_count,
                )
            else:
                ocr_result = ocr_results[page_number]
                page_warnings.extend(ocr_result.warnings)
                if page_number in vertical_marginal_warnings:
                    page_warnings.append(vertical_marginal_warnings[page_number])
                page_result = PdfPageTextResult(
                    page_number,
                    "ocr",
                    cleaned_ocr_texts[page_number],
                    native_quality=quality_by_page[page_number],
                    ocr_confidence=ocr_result.mean_confidence,
                    warnings=tuple(page_warnings),
                    printed_page_numbers=ocr_result.printed_page_numbers,
                    ocr_recognition_model=ocr_result.recognition_model,
                    ocr_preprocessing_profile=ocr_result.preprocessing_profile,
                    structured_table_count=ocr_result.structured_table_count,
                )
            page_results.append(page_result)
            warnings.extend(page_warnings)

        cleaned_texts, overlap_warnings = remove_repeated_adjacent_page_prefixes(
            [page.text for page in page_results], [page.source for page in page_results]
        )
        warnings.extend(overlap_warnings)
        cleaned_texts, marginal_warnings = remove_repeated_headers_and_footers(cleaned_texts)
        warnings.extend(marginal_warnings)
        # 页级结果必须与最终 TXT 使用同一份清理后的文字，供逐页质量评分复用。
        page_results = [
            replace(page, text=cleaned_texts[index])
            for index, page in enumerate(page_results)
        ]
        text = "\n\n".join(page_text for page_text in cleaned_texts if page_text.strip())
        if text:
            text += "\n"

        return PdfTextResult(
            text=text,
            classification=classification,
            pages=tuple(page_results),
            native_pages=tuple(native_pages),
            ocr_pages=tuple(ocr_pages),
            empty_pages=tuple(empty_pages),
            table_count=native.table_count + sum(
                page.structured_table_count for page in ocr_results.values()
            ),
            image_count=native.image_count,
            double_column_pages=native.double_column_pages,
            complex_table_count=native.complex_table_count,
            table_fallback_count=native.table_fallback_count,
            warnings=_deduplicate(warnings),
            parser_isolated=parser_isolated,
            parse_duration_seconds=parse_duration_seconds,
            ocr_isolated=ocr_isolated,
            ocr_duration_seconds=ocr_duration_seconds,
            ocr_startup_timeout_seconds=ocr_startup_timeout_seconds,
            ocr_page_timeout_seconds=ocr_page_timeout_seconds,
            ocr_profile=self._ocr_profile,
        )

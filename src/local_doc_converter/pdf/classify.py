"""根据文字层与图片覆盖情况，对 PDF 文档逐页分类。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pdfplumber

from ..errors import ValidationError
from .preflight import PdfInspection, inspect_pdf


class PdfPageKind(StrEnum):
    TEXT = "text"
    SCANNED = "scanned"
    MIXED = "mixed"
    EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class PdfPageClassification:
    page_number: int
    kind: PdfPageKind
    text_char_count: int
    image_count: int
    image_coverage_ratio: float
    width_points: float
    height_points: float
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PdfClassification:
    document_kind: PdfPageKind
    page_count: int
    text_pages: int
    scanned_pages: int
    mixed_pages: int
    empty_pages: int
    pages: tuple[PdfPageClassification, ...]
    inspection: PdfInspection


def _clipped_image_area(image: dict[str, object], width: float, height: float) -> float:
    try:
        x0 = max(0.0, min(width, float(image["x0"])))
        x1 = max(0.0, min(width, float(image["x1"])))
        top = max(0.0, min(height, float(image["top"])))
        bottom = max(0.0, min(height, float(image["bottom"])))
    except (KeyError, TypeError, ValueError, OverflowError):
        return 0.0
    values = (x0, x1, top, bottom)
    if not all(math.isfinite(value) for value in values):
        return 0.0
    return max(0.0, x1 - x0) * max(0.0, bottom - top)


def classify_page_metrics(
    *,
    text_char_count: int,
    image_count: int,
    image_coverage_ratio: float,
    min_text_chars: int = 20,
    dominant_image_ratio: float = 0.5,
) -> PdfPageKind:
    """把已统计的页面特征转成类别，便于独立测试阈值。"""
    if text_char_count < 0 or image_count < 0:
        raise ValueError("页面文字数和图片数不能为负数。")
    if not 0.0 <= image_coverage_ratio <= 1.0:
        raise ValueError("图片覆盖率必须位于 0 到 1 之间。")
    if min_text_chars <= 0 or not 0.0 <= dominant_image_ratio <= 1.0:
        raise ValueError("页面分类阈值无效。")

    if text_char_count == 0:
        return PdfPageKind.SCANNED if image_count else PdfPageKind.EMPTY
    if image_count and image_coverage_ratio >= dominant_image_ratio:
        if text_char_count < min_text_chars:
            return PdfPageKind.SCANNED
        return PdfPageKind.MIXED
    return PdfPageKind.TEXT


def _document_kind(pages: list[PdfPageClassification]) -> PdfPageKind:
    non_empty = {page.kind for page in pages if page.kind is not PdfPageKind.EMPTY}
    if not non_empty:
        return PdfPageKind.EMPTY
    if len(non_empty) == 1:
        return next(iter(non_empty))
    return PdfPageKind.MIXED


def classify_pdf(
    path: Path,
    *,
    min_text_chars: int = 20,
    dominant_image_ratio: float = 0.5,
) -> PdfClassification:
    """预检后逐页读取文字框和图片框；不解码图片，也不运行 OCR。"""
    path = Path(path)
    # 分类始终自行执行预检，避免调用方错误复用其他文件的检查结果而绕过限额。
    inspection = inspect_pdf(path)
    pages: list[PdfPageClassification] = []

    try:
        with pdfplumber.open(path) as document:
            if len(document.pages) != inspection.page_count:
                raise ValidationError("PDF 两次解析得到的页数不一致，已停止处理。")

            for page_number, page in enumerate(document.pages, start=1):
                width = abs(float(page.width))
                height = abs(float(page.height))
                area = width * height
                if not math.isfinite(area) or area <= 0:
                    raise ValidationError(f"PDF 第 {page_number} 页尺寸无效，无法分类。")

                text_char_count = sum(
                    len(str(character.get("text", "")).strip()) for character in page.chars
                )
                images = list(page.images)
                image_area = sum(_clipped_image_area(image, width, height) for image in images)
                # 图片可能重叠；预检只需要近似覆盖率，因此把相加结果截断为 100%。
                image_coverage = min(1.0, image_area / area)
                kind = classify_page_metrics(
                    text_char_count=text_char_count,
                    image_count=len(images),
                    image_coverage_ratio=image_coverage,
                    min_text_chars=min_text_chars,
                    dominant_image_ratio=dominant_image_ratio,
                )

                warnings: list[str] = []
                if kind is PdfPageKind.MIXED:
                    warnings.append("页面同时包含文字层和大面积图片，后续需要检查文字层质量。")
                elif kind is PdfPageKind.SCANNED:
                    warnings.append("页面缺少足够文字层，需要离线 OCR。")
                elif kind is PdfPageKind.EMPTY:
                    warnings.append("页面未检测到文字或图片。")

                pages.append(
                    PdfPageClassification(
                        page_number=page_number,
                        kind=kind,
                        text_char_count=text_char_count,
                        image_count=len(images),
                        image_coverage_ratio=round(image_coverage, 4),
                        width_points=width,
                        height_points=height,
                        warnings=tuple(warnings),
                    )
                )
    except ValidationError:
        raise
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        raise ValidationError(f"PDF 页面分类失败：{exc}") from exc
    except Exception as exc:
        raise ValidationError(
            f"PDF 页面分类遇到不支持的异常结构：{type(exc).__name__}: {exc}"
        ) from exc

    counts = {kind: sum(page.kind is kind for page in pages) for kind in PdfPageKind}
    return PdfClassification(
        document_kind=_document_kind(pages),
        page_count=len(pages),
        text_pages=counts[PdfPageKind.TEXT],
        scanned_pages=counts[PdfPageKind.SCANNED],
        mixed_pages=counts[PdfPageKind.MIXED],
        empty_pages=counts[PdfPageKind.EMPTY],
        pages=tuple(pages),
        inspection=inspection,
    )

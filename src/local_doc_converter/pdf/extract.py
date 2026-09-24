"""从文本型 PDF 的原生文字层提取可读文本。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from ..errors import PdfExtractionError

_HORIZONTAL_SPACE_RE = re.compile(r"[\t \u00a0]+")
_EXCESS_BLANK_LINES_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class NativePageText:
    page_number: int
    text: str
    character_count: int
    word_count: int
    table_count: int
    image_count: int
    warnings: tuple[str, ...] = ()
    column_count: int = 1
    structured_table_count: int = 0
    complex_table_count: int = 0
    table_fallback_count: int = 0


@dataclass(frozen=True, slots=True)
class NativePdfText:
    pages: tuple[NativePageText, ...]
    table_count: int
    image_count: int
    warnings: tuple[str, ...] = ()
    double_column_pages: tuple[int, ...] = ()
    structured_table_count: int = 0
    complex_table_count: int = 0
    table_fallback_count: int = 0


def normalize_extracted_text(text: str | None) -> str:
    """清理 PDF 常见隐形字符，同时保留行和段落边界。"""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u200b", "").replace("\ufeff", "")
    lines: list[str] = []
    for raw_line in normalized.split("\n"):
        # PDF 中的空格多由坐标推断，连续多空格并不可靠表示精确版式。
        line = _HORIZONTAL_SPACE_RE.sub(" ", raw_line).strip()
        lines.append(line)
    return _EXCESS_BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()


def extract_native_text(path: Path) -> NativePdfText:
    """一次打开 PDF，按页提取文字、单词、表格与图片统计。"""
    path = Path(path)
    pages: list[NativePageText] = []
    document_warnings: list[str] = []

    try:
        with pdfplumber.open(path) as document:
            for page_number, page in enumerate(document.pages, start=1):
                warnings: list[str] = []
                # 延迟导入避免 layout.py 与本模块的文本清理函数形成循环导入。
                from .layout import extract_page_layout

                layout = extract_page_layout(page)
                text = layout.text
                warnings.extend(f"第 {page_number} 页：{warning}" for warning in layout.warnings)

                try:
                    word_count = len(page.extract_words(keep_blank_chars=False))
                except Exception as exc:
                    word_count = 0
                    warnings.append(f"第 {page_number} 页单词边界统计失败：{type(exc).__name__}。")

                pages.append(
                    NativePageText(
                        page_number=page_number,
                        text=text,
                        character_count=len(text),
                        word_count=word_count,
                        table_count=layout.structured_table_count,
                        image_count=len(page.images),
                        warnings=tuple(warnings),
                        column_count=layout.column_count,
                        structured_table_count=layout.structured_table_count,
                        complex_table_count=layout.complex_table_count,
                        table_fallback_count=layout.table_fallback_count,
                    )
                )
                document_warnings.extend(warnings)
    except PdfExtractionError:
        raise
    except Exception as exc:
        raise PdfExtractionError(
            f"PDF 原生文字层提取失败：{type(exc).__name__}: {exc}"
        ) from exc

    return NativePdfText(
        pages=tuple(pages),
        table_count=sum(page.table_count for page in pages),
        image_count=sum(page.image_count for page in pages),
        warnings=tuple(document_warnings),
        double_column_pages=tuple(page.page_number for page in pages if page.column_count == 2),
        structured_table_count=sum(page.structured_table_count for page in pages),
        complex_table_count=sum(page.complex_table_count for page in pages),
        table_fallback_count=sum(page.table_fallback_count for page in pages),
    )

"""在解析正文或渲染页面前，对不可信 PDF 做只读安全预检。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ..config import (
    MAX_FILE_SIZE,
    PDF_MAX_OBJECTS,
    PDF_MAX_PAGE_DIMENSION_POINTS,
    PDF_MAX_PAGES,
)
from ..errors import ValidationError

_HEADER_PREFIX = b"%PDF-"
_HEADER_LENGTH = 8
_PDF_VERSION_RE = re.compile(r"^\d\.\d$")
_EOF_SCAN_SIZE = 4096
_SCAN_CHUNK_SIZE = 1024 * 1024
_SCAN_OVERLAP = 64

# 这些名称本身不会被本项目执行；检测结果用于报告风险与格式损失。
_ACTIVE_CONTENT_TOKENS = {
    b"/JavaScript": "PDF 包含 JavaScript 动作；转换不会执行脚本。",
    b"/JS": "PDF 包含 JavaScript 动作；转换不会执行脚本。",
    b"/OpenAction": "PDF 包含打开文件时自动触发的动作；转换不会执行该动作。",
    b"/AA": "PDF 包含附加动作；转换不会执行该动作。",
    b"/Launch": "PDF 包含启动外部程序的动作；转换不会执行该动作。",
    b"/SubmitForm": "PDF 包含表单提交动作；转换不会提交数据或联网。",
    b"/RichMedia": "PDF 包含富媒体内容；转换只处理静态文字。",
    b"/XFA": "PDF 包含 XFA 动态表单；动态表单结构可能丢失。",
    b"/AcroForm": "PDF 包含交互式表单；转换只提取可见文字。",
    b"/EmbeddedFiles": "PDF 包含嵌入文件；转换不会打开或提取附件。",
    b"/URI": "PDF 包含外部链接；转换不会主动联网访问。",
    b"/GoToR": "PDF 包含远程文档跳转；转换不会打开外部文档。",
}

_EXECUTABLE_ACTION_TOKENS = {
    b"/JavaScript",
    b"/JS",
    b"/OpenAction",
    b"/AA",
    b"/Launch",
    b"/SubmitForm",
    b"/RichMedia",
    b"/XFA",
}
_EXTERNAL_LINK_TOKENS = {b"/URI", b"/GoToR", b"/SubmitForm"}


@dataclass(frozen=True, slots=True)
class PdfSafetyLimits:
    """PDF 预检限额；测试可注入更小阈值。"""

    max_file_size: int = MAX_FILE_SIZE
    max_pages: int = PDF_MAX_PAGES
    max_objects: int = PDF_MAX_OBJECTS
    max_page_dimension_points: float = PDF_MAX_PAGE_DIMENSION_POINTS


@dataclass(frozen=True, slots=True)
class PdfInspection:
    pdf_version: str
    file_size: int
    page_count: int
    object_count: int
    max_page_width_points: float
    max_page_height_points: float
    has_active_content: bool = False
    has_embedded_files: bool = False
    has_external_links: bool = False
    warnings: tuple[str, ...] = ()


def _read_header(path: Path) -> str:
    with path.open("rb") as stream:
        header = stream.read(_HEADER_LENGTH)
    if not header.startswith(_HEADER_PREFIX):
        raise ValidationError("文件扩展名为 PDF，但文件头不是有效的 %PDF- 标记。")
    version_bytes = header[len(_HEADER_PREFIX) :].splitlines()[0]
    try:
        version = version_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValidationError("PDF 版本标记不是有效的 ASCII 内容。") from exc
    if not _PDF_VERSION_RE.fullmatch(version):
        raise ValidationError(f"PDF 版本标记无效：{version or '空'}")
    return version


def _has_eof_marker(path: Path) -> bool:
    size = path.stat().st_size
    with path.open("rb") as stream:
        stream.seek(max(0, size - _EOF_SCAN_SIZE))
        tail = stream.read()
    return b"%%EOF" in tail


def _scan_risk_tokens(path: Path) -> set[bytes]:
    """分块扫描名称标记，避免为风险提示一次性复制整个 PDF 到内存。"""
    found: set[bytes] = set()
    tail = b""
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(_SCAN_CHUNK_SIZE)
            if not chunk:
                break
            probe = tail + chunk
            for token in _ACTIVE_CONTENT_TOKENS:
                if token in probe:
                    found.add(token)
            if len(found) == len(_ACTIVE_CONTENT_TOKENS):
                break
            tail = probe[-_SCAN_OVERLAP:]
    return found


def _count_objects(reader: PdfReader) -> int:
    """统计交叉引用表中的间接对象；兼容普通 xref 与对象流。"""
    object_keys: set[tuple[int, int]] = set()
    for generation, entries in reader.xref.items():
        object_keys.update((int(generation), int(object_id)) for object_id in entries)
    object_stream_entries = getattr(reader, "xref_objStm", {})
    object_keys.update((0, int(object_id)) for object_id in object_stream_entries)
    return len(object_keys)


def _effective_page_size(page: object) -> tuple[float, float]:
    media_box = page.mediabox
    width = abs(float(media_box.width))
    height = abs(float(media_box.height))
    raw_user_unit = page.get("/UserUnit", 1)
    try:
        user_unit = float(raw_user_unit)
    except (TypeError, ValueError) as exc:
        raise ValidationError("PDF 页面 UserUnit 不是有效数字。") from exc
    if not math.isfinite(user_unit) or user_unit <= 0:
        raise ValidationError("PDF 页面 UserUnit 必须是正的有限数字。")
    width *= user_unit
    height *= user_unit
    if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
        raise ValidationError("PDF 页面尺寸无效。")
    return width, height


def inspect_pdf(path: Path, limits: PdfSafetyLimits | None = None) -> PdfInspection:
    """只读检查 PDF 结构，不解码页面图片、不执行动作、不提取附件。"""
    limits = limits or PdfSafetyLimits()
    path = Path(path)
    try:
        if not path.is_file():
            raise ValidationError("PDF 输入文件不存在或不是普通文件。")
        size = path.stat().st_size
        if size <= 0:
            raise ValidationError("PDF 文件内容为空。")
        if size > limits.max_file_size:
            raise ValidationError(
                f"PDF 文件过大：{size / 1024 / 1024:.1f} MB，"
                f"上限为 {limits.max_file_size / 1024 / 1024:.1f} MB。"
            )

        version = _read_header(path)
        if not _has_eof_marker(path):
            raise ValidationError("PDF 缺少文件结束标记 %%EOF，文件可能被截断或损坏。")
        risk_tokens = _scan_risk_tokens(path)
    except ValidationError:
        raise
    except OSError as exc:
        raise ValidationError(f"无法读取 PDF 文件：{exc}") from exc

    warnings = list(
        dict.fromkeys(
            message for token, message in _ACTIVE_CONTENT_TOKENS.items() if token in risk_tokens
        )
    )

    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            raise ValidationError("PDF 已加密或受密码保护，当前版本暂不处理加密 PDF。")

        object_count = _count_objects(reader)
        if object_count > limits.max_objects:
            raise ValidationError(
                f"PDF 间接对象过多：{object_count}，上限为 {limits.max_objects}。"
            )

        page_count = len(reader.pages)
        if page_count <= 0:
            raise ValidationError("PDF 不包含可处理的页面。")
        if page_count > limits.max_pages:
            raise ValidationError(f"PDF 页数过多：{page_count}，上限为 {limits.max_pages} 页。")

        max_width = 0.0
        max_height = 0.0
        for page_number, page in enumerate(reader.pages, start=1):
            width, height = _effective_page_size(page)
            if width > limits.max_page_dimension_points or height > limits.max_page_dimension_points:
                raise ValidationError(
                    f"PDF 第 {page_number} 页尺寸异常：{width:.1f} × {height:.1f} 点，"
                    f"单边上限为 {limits.max_page_dimension_points:g} 点。"
                )
            max_width = max(max_width, width)
            max_height = max(max_height, height)

        return PdfInspection(
            pdf_version=version,
            file_size=size,
            page_count=page_count,
            object_count=object_count,
            max_page_width_points=max_width,
            max_page_height_points=max_height,
            has_active_content=bool(risk_tokens & _EXECUTABLE_ACTION_TOKENS),
            has_embedded_files=b"/EmbeddedFiles" in risk_tokens,
            has_external_links=bool(risk_tokens & _EXTERNAL_LINK_TOKENS),
            warnings=tuple(warnings),
        )
    except ValidationError:
        raise
    except (PdfReadError, OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        raise ValidationError(f"PDF 结构损坏或无法安全解析：{exc}") from exc
    except Exception as exc:
        raise ValidationError(f"PDF 预检遇到不支持的异常结构：{type(exc).__name__}: {exc}") from exc

"""PaddleOCR PP-OCRv5 的本地、延迟加载适配层。"""

from __future__ import annotations

import importlib.util
import math
import os
import re
from dataclasses import dataclass, replace
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from statistics import median
from typing import Any, Protocol

from ..config import (
    DEFAULT_PADDLE_MODEL_DIR,
    PADDLE_MODEL_DIR_ENV,
    PADDLE_MODEL_NAMES,
    PDF_MAX_RENDER_PIXELS,
    PDF_RENDER_DPI,
)
from ..errors import OcrUnavailableError, PdfExtractionError

OCR_PROFILES = ("standard", "old_print")
OLD_PRINT_RENDER_DPI = 300


@dataclass(frozen=True, slots=True)
class OcrPageResult:
    page_number: int
    text: str
    line_count: int
    mean_confidence: float | None
    low_confidence_count: int = 0
    orientation_degrees: int | None = None
    warnings: tuple[str, ...] = ()
    marginal_line_indices: tuple[int, ...] = ()
    printed_page_numbers: tuple[str, ...] = ()
    recognition_model: str = "mobile"
    structured_table_count: int = 0
    preprocessing_profile: str = "standard"


class OcrEngine(Protocol):
    def recognize_page(self, pdf_path: Path, page_number: int) -> OcrPageResult:
        """识别一页；page_number 从 1 开始。"""


@dataclass(frozen=True, slots=True)
class OcrEnvironmentStatus:
    ready: bool
    package_installed: bool
    supported_versions: bool
    model_root: Path | None
    missing_models: tuple[str, ...]
    message: str


@dataclass(frozen=True, slots=True)
class _OcrTextBlock:
    """PaddleOCR 文字块及其页面坐标。"""

    top: float
    left: float
    bottom: float
    right: float
    text: str
    score: float | None
    source_index: int
    has_geometry: bool

    @property
    def width(self) -> float:
        return max(self.right - self.left, 1.0)

    @property
    def height(self) -> float:
        return max(self.bottom - self.top, 1.0)

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2


def configured_model_root() -> Path:
    raw = os.environ.get(PADDLE_MODEL_DIR_ENV, "").strip()
    configured = Path(raw).expanduser() if raw else DEFAULT_PADDLE_MODEL_DIR
    return configured.resolve(strict=False)


def inspect_ocr_environment(model_root: Path | None = None) -> OcrEnvironmentStatus:
    """只检查包和模型目录，不导入 Paddle，不发起网络请求。"""
    package_installed = all(
        importlib.util.find_spec(name) is not None for name in ("paddle", "paddleocr")
    )
    paddle_version: str | None = None
    paddleocr_version: str | None = None
    if package_installed:
        try:
            paddle_version = version("paddlepaddle")
            paddleocr_version = version("paddleocr")
        except PackageNotFoundError:
            package_installed = False
    supported_versions = paddle_version == "3.3.0" and paddleocr_version == "3.7.0"
    root = (
        Path(model_root).expanduser().resolve(strict=False)
        if model_root
        else configured_model_root()
    )
    missing = tuple(
        name
        for name in PADDLE_MODEL_NAMES
        if root is None or not (root / name / "inference.yml").is_file()
    )
    ready = package_installed and supported_versions and root is not None and not missing

    if ready:
        message = "PaddleOCR PP-OCRv5 与本地模型已就绪。"
    elif not package_installed:
        message = "未安装已验证的 PaddlePaddle 3.3.0 / PaddleOCR 3.7.0。"
    elif not supported_versions:
        message = (
            f"OCR 依赖版本不匹配：PaddlePaddle {paddle_version or '未知'} / "
            f"PaddleOCR {paddleocr_version or '未知'}；需要 3.3.0 / 3.7.0。"
        )
    else:
        message = f"本地模型目录 {root} 不完整，缺少：" + "、".join(missing)
    return OcrEnvironmentStatus(
        ready,
        package_installed,
        supported_versions,
        root,
        missing,
        message,
    )


def _missing_ocr_message(status: OcrEnvironmentStatus) -> str:
    return (
        "该 PDF 需要离线 OCR，但 PaddleOCR 环境未就绪。"
        f"{status.message} "
        "请先按 README 的“PaddleOCR 可选安装”完成本机配置；"
        "文本型 PDF 的原生提取不需要 OCR。"
    )


def build_configured_ocr_engine(profile: str = "standard") -> "PaddleOcrEngine":
    status = inspect_ocr_environment()
    if not status.ready or status.model_root is None:
        raise OcrUnavailableError(_missing_ocr_message(status))
    return PaddleOcrEngine(status.model_root, profile=profile)


def preprocess_ocr_image(image: Any, profile: str) -> Any:
    """旧印刷体模式仅作轻度中值去噪；默认模式保持原图不变。"""
    if profile == "standard":
        return image
    if profile == "old_print":
        from PIL import ImageFilter

        return image.filter(ImageFilter.MedianFilter(size=3))
    raise ValueError(f"不支持的 OCR 预处理模式：{profile}")


def _result_payload(item: Any) -> dict[str, Any]:
    raw = getattr(item, "json", item)
    if callable(raw):
        raw = raw()
    if not isinstance(raw, dict):
        return {}
    payload = raw.get("res", raw)
    return payload if isinstance(payload, dict) else {}


def _polygon_metrics(
    polygon: Any, fallback_index: int
) -> tuple[float, float, float, float, bool]:
    try:
        points = list(polygon)
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        if not xs or not all(math.isfinite(value) for value in xs + ys):
            raise ValueError
        return min(ys), min(xs), max(ys), max(xs), True
    except (TypeError, ValueError, IndexError):
        position = float(fallback_index)
        return position, 0.0, position + 1.0, 1.0, False


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x323AF
    )


def _cjk_count(text: str) -> int:
    return sum(_is_cjk(character) for character in text)


def _looks_like_vertical_cjk(blocks: list[_OcrTextBlock]) -> bool:
    """仅在竖排特征充分时改变默认横排顺序。"""
    cjk_blocks = [
        block for block in blocks if block.has_geometry and _cjk_count(block.text) >= 2
    ]
    vertical = [
        block
        for block in cjk_blocks
        if block.height >= max(block.width * 2.0, 24.0)
    ]
    if len(vertical) < 4:
        return False

    total_cjk = sum(_cjk_count(block.text) for block in cjk_blocks)
    vertical_cjk = sum(_cjk_count(block.text) for block in vertical)
    if vertical_cjk < 20 or vertical_cjk < total_cjk * 0.55:
        return False

    aspect_ratios = [block.height / block.width for block in vertical]
    return median(aspect_ratios) >= 3.0


_PAGE_NUMBER_RE = re.compile(r"[一二三四五六七八九十百〇零0-9ＯO]{1,4}")


def _looks_like_page_number(text: str) -> bool:
    if _PAGE_NUMBER_RE.fullmatch(text):
        return True
    # 旧书的单个数字常被误识为近形汉字；只在页边下部允许这类短候选。
    return len(text) <= 2 and all(_is_cjk(character) for character in text)


def _sort_vertical_blocks(
    blocks: list[_OcrTextBlock],
) -> tuple[list[_OcrTextBlock], set[int], set[int]]:
    """按繁体竖排常见规则：列从右到左，同列文字块从上到下。"""
    vertical_widths = [
        block.width
        for block in blocks
        if block.has_geometry and block.height >= block.width * 2.0
    ]
    typical_width = median(vertical_widths) if vertical_widths else 32.0
    narrow_limit = max(24.0, typical_width * 1.8)
    cluster_distance = max(8.0, min(24.0, typical_width * 0.35))

    # OCR 可能把一个竖列切成多块；先按水平中心聚成列，
    # 避免因左右轻微抖动把同列文字插入相邻列。
    columns: list[list[_OcrTextBlock]] = []
    centers: list[float] = []
    ordered = sorted(blocks, key=lambda block: (-block.center_x, block.top))
    for block in ordered:
        if not block.has_geometry or block.width > narrow_limit:
            columns.append([block])
            centers.append(block.center_x)
            continue

        matching_index: int | None = None
        matching_distance = math.inf
        for index, center in enumerate(centers):
            if any(item.width > narrow_limit for item in columns[index]):
                continue
            distance = abs(block.center_x - center)
            if distance <= cluster_distance and distance < matching_distance:
                matching_index = index
                matching_distance = distance
        if matching_index is None:
            columns.append([block])
            centers.append(block.center_x)
            continue

        columns[matching_index].append(block)
        centers[matching_index] = sum(
            item.center_x for item in columns[matching_index]
        ) / len(columns[matching_index])

    column_order = sorted(range(len(columns)), key=lambda index: -centers[index])
    page_bottom = max((block.bottom for block in blocks if block.has_geometry), default=0.0)
    marginal_indices: set[int] = set()
    page_number_indices: set[int] = set()
    # 只在左右最外侧的列寻找页边信息；独有标题仍要经过跨页重复验证。
    outer_columns = set(column_order[:1] + column_order[-1:])
    for index in outer_columns:
        for block in columns[index]:
            if not block.has_geometry:
                continue
            if _looks_like_page_number(block.text) and block.top >= page_bottom * 0.67:
                page_number_indices.add(block.source_index)
            elif (
                len(block.text) <= 24
                and page_bottom * 0.12 <= block.top
                and block.bottom <= page_bottom * 0.65
            ):
                marginal_indices.add(block.source_index)
    result: list[_OcrTextBlock] = []
    for index in column_order:
        result.extend(
            sorted(
                columns[index],
                key=lambda block: (block.top, block.left, block.source_index),
            )
        )
    return result, marginal_indices, page_number_indices


def parse_paddle_results(raw_results: Any, page_number: int) -> OcrPageResult:
    """把 PaddleOCR 3.x 结果转成稳定的页级结构。"""
    records: list[_OcrTextBlock] = []
    orientation: int | None = None
    for item in list(raw_results or []):
        payload = _result_payload(item)
        texts = list(payload.get("rec_texts") or [])
        scores = list(payload.get("rec_scores") or [])
        polygons = list(payload.get("rec_polys") or payload.get("dt_polys") or [])
        preprocessing = payload.get("doc_preprocessor_res")
        if isinstance(preprocessing, dict):
            raw_angle = preprocessing.get("angle")
            if isinstance(raw_angle, (int, float)):
                orientation = int(raw_angle)

        for index, raw_text in enumerate(texts):
            text = str(raw_text).strip()
            if not text:
                continue
            score: float | None = None
            if index < len(scores):
                try:
                    parsed_score = float(scores[index])
                    score = parsed_score if math.isfinite(parsed_score) else None
                except (TypeError, ValueError):
                    pass
            top, left, bottom, right, has_geometry = _polygon_metrics(
                polygons[index] if index < len(polygons) else None,
                len(records),
            )
            records.append(
                _OcrTextBlock(
                    top,
                    left,
                    bottom,
                    right,
                    text,
                    score,
                    len(records),
                    has_geometry,
                )
            )

    vertical_layout = _looks_like_vertical_cjk(records)
    marginal_indices: set[int] = set()
    page_number_indices: set[int] = set()
    if vertical_layout:
        records, marginal_indices, page_number_indices = _sort_vertical_blocks(records)
    else:
        records.sort(key=lambda record: (record.top, record.left, record.source_index))
    page_numbers = tuple(
        record.text for record in records if record.source_index in page_number_indices
    )
    visible_records = [
        record for record in records if record.source_index not in page_number_indices
    ]
    lines = [record.text for record in visible_records]
    marginal_line_indices = tuple(
        index
        for index, record in enumerate(visible_records)
        if record.source_index in marginal_indices
    )
    scores = [record.score for record in records if record.score is not None]
    low_confidence = sum(score < 0.6 for score in scores)
    warnings: list[str] = []
    if not lines:
        warnings.append(f"第 {page_number} 页 OCR 未识别到文字。")
    if low_confidence:
        warnings.append(f"第 {page_number} 页有 {low_confidence} 个 OCR 文字块置信度低于 0.60。")
    if vertical_layout:
        warnings.append(f"第 {page_number} 页检测到竖排文字，已按从右到左的列顺序重排。")
    if page_numbers:
        warnings.append(f"第 {page_number} 页已将页边页码候选移出正文，原识别值已记录在报告中。")

    return OcrPageResult(
        page_number=page_number,
        text="\n".join(lines),
        line_count=len(lines),
        mean_confidence=round(sum(scores) / len(scores), 4) if scores else None,
        low_confidence_count=low_confidence,
        orientation_degrees=orientation,
        warnings=tuple(warnings),
        marginal_line_indices=marginal_line_indices,
        printed_page_numbers=page_numbers,
    )


class PaddleOcrEngine:
    """优先使用已显式准备的高精度识别模型，否则使用 mobile 模型。"""

    def __init__(
        self,
        model_root: Path,
        *,
        dpi: int | None = None,
        max_render_pixels: int = PDF_MAX_RENDER_PIXELS,
        profile: str = "standard",
    ) -> None:
        if profile not in OCR_PROFILES:
            raise ValueError(f"不支持的 OCR 预处理模式：{profile}")
        self.model_root = Path(model_root).expanduser().resolve(strict=False)
        self.profile = profile
        self.dpi = dpi if dpi is not None else (
            OLD_PRINT_RENDER_DPI if profile == "old_print" else PDF_RENDER_DPI
        )
        self.max_render_pixels = max_render_pixels
        status = inspect_ocr_environment(self.model_root)
        if not status.ready:
            raise OcrUnavailableError(_missing_ocr_message(status))
        server_model = self.model_root / "PP-OCRv5_server_rec"
        self.recognition_model_name = (
            "PP-OCRv5_server_rec"
            if (server_model / "inference.yml").is_file()
            else "PP-OCRv5_mobile_rec"
        )
        self._predictor: Any | None = None

    def _load_predictor(self) -> Any:
        if self._predictor is not None:
            return self._predictor

        # 指定缓存根目录，避免 PaddleX 把隐式文件写到不可控位置。
        os.environ["PADDLE_PDX_CACHE_HOME"] = str(self.model_root.parent / "paddlex-cache")
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise OcrUnavailableError("无法导入 PaddleOCR，请按 README 重新安装 OCR 可选依赖。") from exc

        try:
            self._predictor = PaddleOCR(
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_detection_model_dir=str(self.model_root / "PP-OCRv5_mobile_det"),
                text_recognition_model_name=self.recognition_model_name,
                text_recognition_model_dir=str(self.model_root / self.recognition_model_name),
                doc_orientation_classify_model_name="PP-LCNet_x1_0_doc_ori",
                doc_orientation_classify_model_dir=str(self.model_root / "PP-LCNet_x1_0_doc_ori"),
                use_doc_orientation_classify=True,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                device="cpu",
            )
        except Exception as exc:
            raise OcrUnavailableError(
                f"PaddleOCR 本地模型初始化失败：{type(exc).__name__}: {exc}"
            ) from exc
        return self._predictor

    def prepare(self) -> None:
        """显式完成模型初始化，便于子进程区分启动与单页超时。"""
        self._load_predictor()

    def _render_page(self, pdf_path: Path, page_number: int) -> Any:
        try:
            import pypdfium2 as pdfium

            document = pdfium.PdfDocument(str(pdf_path))
            try:
                if not 1 <= page_number <= len(document):
                    raise PdfExtractionError(f"PDF 不存在第 {page_number} 页。")
                page = document[page_number - 1]
                try:
                    width_points, height_points = page.get_size()
                    width_pixels = math.ceil(width_points * self.dpi / 72)
                    height_pixels = math.ceil(height_points * self.dpi / 72)
                    if width_pixels * height_pixels > self.max_render_pixels:
                        raise PdfExtractionError(
                            f"PDF 第 {page_number} 页渲染像素过多："
                            f"{width_pixels} × {height_pixels}，已停止 OCR。"
                        )
                    bitmap = page.render(scale=self.dpi / 72)
                    try:
                        return bitmap.to_pil().convert("RGB")
                    finally:
                        bitmap.close()
                finally:
                    page.close()
            finally:
                document.close()
        except PdfExtractionError:
            raise
        except Exception as exc:
            raise PdfExtractionError(
                f"PDF 第 {page_number} 页渲染失败：{type(exc).__name__}: {exc}"
            ) from exc

    def recognize_page(self, pdf_path: Path, page_number: int) -> OcrPageResult:
        image = self._render_page(Path(pdf_path), page_number)
        prepared = None
        try:
            import numpy as np

            prepared = preprocess_ocr_image(image, self.profile)
            predictor = self._load_predictor()
            raw_results = predictor.predict(np.asarray(prepared))
            result = parse_paddle_results(raw_results, page_number)
            if result.orientation_degrees in (None, 0) and not any(
                "竖排文字" in warning for warning in result.warnings
            ):
                from .ocr_tables import reconstruct_ruled_table

                table = reconstruct_ruled_table(image, raw_results)
                if table is not None:
                    result = replace(
                        result,
                        text=table.text,
                        line_count=len(table.text.splitlines()),
                        structured_table_count=1,
                        warnings=result.warnings + (
                            f"第 {page_number} 页已按清晰网格线恢复 1 个扫描表格；"
                            "合并单元格和无框表格仍需人工核对。",
                        ),
                    )
            return replace(
                result,
                recognition_model=(
                    "server" if self.recognition_model_name == "PP-OCRv5_server_rec" else "mobile"
                ),
                preprocessing_profile=self.profile,
            )
        except (OcrUnavailableError, PdfExtractionError):
            raise
        except Exception as exc:
            raise PdfExtractionError(
                f"PDF 第 {page_number} 页 OCR 失败：{type(exc).__name__}: {exc}"
            ) from exc
        finally:
            if prepared is not None and prepared is not image:
                prepared.close()
            image.close()

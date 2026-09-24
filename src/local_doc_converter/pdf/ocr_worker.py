"""PDFium 渲染与 PaddleOCR 的短生命周期子进程。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from ..config import PDF_OCR_PAGE_TIMEOUT_SECONDS, PDF_OCR_STARTUP_TIMEOUT_SECONDS
from ..errors import ConverterError, OcrUnavailableError, PdfExtractionError, ValidationError
from .ocr import OCR_PROFILES, OcrEngine, OcrPageResult, build_configured_ocr_engine
from .preflight import inspect_pdf
from .process_control import (
    read_json_payload,
    start_worker,
    terminate_process_group,
    write_json_payload,
)


@dataclass(frozen=True, slots=True)
class IsolatedOcrResult:
    pages: tuple[OcrPageResult, ...]
    worker_pid: int
    duration_seconds: float
    startup_timeout_seconds: float
    page_timeout_seconds: float


class OcrBatchRunner(Protocol):
    def __call__(self, pdf_path: Path, page_numbers: tuple[int, ...]) -> IsolatedOcrResult:
        """在隔离进程中识别指定页面。"""


def _page_to_dict(page: OcrPageResult) -> dict[str, Any]:
    return {
        "page_number": page.page_number,
        "text": page.text,
        "line_count": page.line_count,
        "mean_confidence": page.mean_confidence,
        "low_confidence_count": page.low_confidence_count,
        "orientation_degrees": page.orientation_degrees,
        "warnings": list(page.warnings),
        "marginal_line_indices": list(page.marginal_line_indices),
        "printed_page_numbers": list(page.printed_page_numbers),
        "recognition_model": page.recognition_model,
        "structured_table_count": page.structured_table_count,
        "preprocessing_profile": page.preprocessing_profile,
    }


def _page_from_dict(data: dict[str, Any]) -> OcrPageResult:
    raw_confidence = data.get("mean_confidence")
    raw_orientation = data.get("orientation_degrees")
    return OcrPageResult(
        page_number=int(data["page_number"]),
        text=str(data["text"]),
        line_count=int(data["line_count"]),
        mean_confidence=float(raw_confidence) if raw_confidence is not None else None,
        low_confidence_count=int(data.get("low_confidence_count", 0)),
        orientation_degrees=int(raw_orientation) if raw_orientation is not None else None,
        warnings=tuple(str(item) for item in data.get("warnings", [])),
        marginal_line_indices=tuple(int(item) for item in data.get("marginal_line_indices", [])),
        printed_page_numbers=tuple(str(item) for item in data.get("printed_page_numbers", [])),
        recognition_model=str(data.get("recognition_model", "mobile")),
        structured_table_count=int(data.get("structured_table_count", 0)),
        preprocessing_profile=str(data.get("preprocessing_profile", "standard")),
    )


def _validate_page_numbers(page_numbers: tuple[int, ...], page_count: int | None = None) -> None:
    if not page_numbers:
        raise ValidationError("OCR 至少需要一个页码。")
    if any(page_number <= 0 for page_number in page_numbers):
        raise ValidationError("OCR 页码必须从 1 开始。")
    if len(set(page_numbers)) != len(page_numbers):
        raise ValidationError("OCR 页码不能重复。")
    if tuple(sorted(page_numbers)) != page_numbers:
        raise ValidationError("OCR 页码必须按升序排列。")
    if page_count is not None and page_numbers[-1] > page_count:
        raise ValidationError(f"PDF 仅有 {page_count} 页，不存在第 {page_numbers[-1]} 页。")


def _write_progress(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    write_json_payload(temporary, payload)
    temporary.replace(path)


def _worker_payload(
    path: Path,
    page_numbers: tuple[int, ...],
    progress_path: Path,
    engine_factory: Callable[[], OcrEngine] | None = None,
    profile: str = "standard",
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        inspection = inspect_pdf(path)
        _validate_page_numbers(page_numbers, inspection.page_count)
        engine = (
            engine_factory() if engine_factory is not None
            else build_configured_ocr_engine(profile)
        )
        prepare = getattr(engine, "prepare", None)
        if callable(prepare):
            prepare()
        _write_progress(progress_path, {"status": "ready", "completed_pages": []})

        results: list[OcrPageResult] = []
        for page_number in page_numbers:
            try:
                results.append(engine.recognize_page(path, page_number))
            except Exception as exc:
                raise PdfExtractionError(
                    f"PDF 第 {page_number} 页渲染/OCR 失败：{exc}"
                ) from exc
            _write_progress(
                progress_path,
                {
                    "status": "running",
                    "completed_pages": [page.page_number for page in results],
                },
            )
        return {
            "ok": True,
            "worker_pid": os.getpid(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "pages": [_page_to_dict(page) for page in results],
        }
    except ConverterError as exc:
        return {"ok": False, "error_kind": type(exc).__name__, "message": str(exc)}
    except Exception as exc:
        return {
            "ok": False,
            "error_kind": "UnexpectedError",
            "message": f"PDF 渲染/OCR 子进程发生未预期错误：{type(exc).__name__}: {exc}",
        }


def build_worker_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("source", type=Path)
    parser.add_argument("result", type=Path)
    parser.add_argument("progress", type=Path)
    parser.add_argument("pages")
    parser.add_argument("profile", choices=OCR_PROFILES)
    return parser


def worker_main() -> int:
    args = build_worker_parser().parse_args()
    try:
        raw_pages = json.loads(args.pages)
        if not isinstance(raw_pages, list):
            raise TypeError
        page_numbers = tuple(int(page) for page in raw_pages)
        write_json_payload(
            args.result,
            _worker_payload(args.source, page_numbers, args.progress, profile=args.profile),
        )
    except Exception:
        return 3
    return 0


def _raise_worker_error(payload: dict[str, Any]) -> None:
    message = str(payload.get("message") or "PDF 渲染/OCR 子进程未返回错误详情。")
    error_kind = payload.get("error_kind")
    if error_kind == "ValidationError":
        raise ValidationError(message)
    if error_kind == "OcrUnavailableError":
        raise OcrUnavailableError(message)
    raise PdfExtractionError(message)


def _read_completed_pages(progress_path: Path) -> tuple[bool, tuple[int, ...]]:
    if not progress_path.is_file():
        return False, ()
    try:
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        if not isinstance(progress, dict):
            return False, ()
        completed = tuple(int(page) for page in progress.get("completed_pages", []))
        return progress.get("status") in {"ready", "running"}, completed
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False, ()


def ocr_pdf_pages_isolated(
    pdf_path: Path,
    page_numbers: tuple[int, ...],
    *,
    startup_timeout_seconds: float = PDF_OCR_STARTUP_TIMEOUT_SECONDS,
    page_timeout_seconds: float = PDF_OCR_PAGE_TIMEOUT_SECONDS,
    profile: str = "standard",
) -> IsolatedOcrResult:
    """在单个文件级子进程中串行渲染并 OCR 指定页面。"""
    if startup_timeout_seconds <= 0 or page_timeout_seconds <= 0:
        raise ValueError("OCR 启动和单页超时必须大于 0 秒。")
    if profile not in OCR_PROFILES:
        raise ValueError(f"不支持的 OCR 预处理模式：{profile}")
    page_numbers = tuple(page_numbers)
    _validate_page_numbers(page_numbers)
    pdf_path = Path(pdf_path).resolve(strict=False)

    with tempfile.TemporaryDirectory(prefix="local_doc_pdf_ocr_") as temporary:
        work_dir = Path(temporary)
        result_path = work_dir / "result.json"
        progress_path = work_dir / "progress.json"
        command = [
            sys.executable,
            "-c",
            (
                "from local_doc_converter.pdf.ocr_worker import worker_main; "
                "raise SystemExit(worker_main())"
            ),
            str(pdf_path),
            str(result_path),
            str(progress_path),
            json.dumps(page_numbers),
            profile,
        ]
        process = start_worker(command, stage_label="PDF 渲染/OCR", capture_output=False)
        ready = False
        completed_pages: tuple[int, ...] = ()
        deadline = time.monotonic() + startup_timeout_seconds

        while process.poll() is None:
            current_ready, current_completed = _read_completed_pages(progress_path)
            if current_ready and not ready:
                ready = True
                deadline = time.monotonic() + page_timeout_seconds
            if len(current_completed) > len(completed_pages):
                completed_pages = current_completed
                deadline = time.monotonic() + page_timeout_seconds
            if time.monotonic() > deadline:
                next_page = page_numbers[min(len(completed_pages), len(page_numbers) - 1)]
                terminate_process_group(process)
                if not ready:
                    raise PdfExtractionError(
                        f"PaddleOCR 模型初始化超过 {startup_timeout_seconds:g} 秒，"
                        "已终止独立子进程。"
                    )
                raise PdfExtractionError(
                    f"PDF 第 {next_page} 页渲染/OCR 超过 {page_timeout_seconds:g} 秒，"
                    "已终止独立子进程。"
                )
            time.sleep(0.05)

        process.wait()
        if process.returncode != 0:
            raise PdfExtractionError(
                f"PDF 渲染/OCR 子进程异常退出（退出码 {process.returncode}）。"
            )

        payload = read_json_payload(result_path, stage_label="PDF 渲染/OCR")
        if not payload.get("ok"):
            _raise_worker_error(payload)
        try:
            pages = tuple(_page_from_dict(page) for page in payload["pages"])
            returned_numbers = tuple(page.page_number for page in pages)
            if returned_numbers != page_numbers:
                raise ValueError("OCR 返回页码与请求不一致")
            return IsolatedOcrResult(
                pages=pages,
                worker_pid=int(payload["worker_pid"]),
                duration_seconds=float(payload["duration_seconds"]),
                startup_timeout_seconds=startup_timeout_seconds,
                page_timeout_seconds=page_timeout_seconds,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PdfExtractionError(f"PDF 渲染/OCR 子进程返回了无效结果：{exc}") from exc


if __name__ == "__main__":
    raise SystemExit(worker_main())

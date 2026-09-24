"""PDF 解析短生命周期子进程与父进程超时控制。"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import PDF_PARSE_TIMEOUT_SECONDS
from ..errors import ConverterError, PdfExtractionError, ValidationError
from .classify import (
    PdfClassification,
    PdfPageClassification,
    PdfPageKind,
    classify_pdf,
)
from .extract import NativePageText, NativePdfText, extract_native_text
from .preflight import PdfInspection
from .process_control import (
    read_json_payload,
    start_worker,
    terminate_process_group,
    write_json_payload,
)


@dataclass(frozen=True, slots=True)
class IsolatedPdfParseResult:
    classification: PdfClassification
    native_text: NativePdfText
    worker_pid: int
    duration_seconds: float


def _inspection_to_dict(inspection: PdfInspection) -> dict[str, Any]:
    return {
        "pdf_version": inspection.pdf_version,
        "file_size": inspection.file_size,
        "page_count": inspection.page_count,
        "object_count": inspection.object_count,
        "max_page_width_points": inspection.max_page_width_points,
        "max_page_height_points": inspection.max_page_height_points,
        "has_active_content": inspection.has_active_content,
        "has_embedded_files": inspection.has_embedded_files,
        "has_external_links": inspection.has_external_links,
        "warnings": list(inspection.warnings),
    }


def _classification_to_dict(classification: PdfClassification) -> dict[str, Any]:
    return {
        "document_kind": classification.document_kind.value,
        "page_count": classification.page_count,
        "text_pages": classification.text_pages,
        "scanned_pages": classification.scanned_pages,
        "mixed_pages": classification.mixed_pages,
        "empty_pages": classification.empty_pages,
        "pages": [
            {
                "page_number": page.page_number,
                "kind": page.kind.value,
                "text_char_count": page.text_char_count,
                "image_count": page.image_count,
                "image_coverage_ratio": page.image_coverage_ratio,
                "width_points": page.width_points,
                "height_points": page.height_points,
                "warnings": list(page.warnings),
            }
            for page in classification.pages
        ],
        "inspection": _inspection_to_dict(classification.inspection),
    }


def _native_to_dict(native: NativePdfText) -> dict[str, Any]:
    return {
        "pages": [
            {
                "page_number": page.page_number,
                "text": page.text,
                "character_count": page.character_count,
                "word_count": page.word_count,
                "table_count": page.table_count,
                "image_count": page.image_count,
                "warnings": list(page.warnings),
                "column_count": page.column_count,
                "structured_table_count": page.structured_table_count,
                "complex_table_count": page.complex_table_count,
                "table_fallback_count": page.table_fallback_count,
            }
            for page in native.pages
        ],
        "table_count": native.table_count,
        "image_count": native.image_count,
        "warnings": list(native.warnings),
        "double_column_pages": list(native.double_column_pages),
        "structured_table_count": native.structured_table_count,
        "complex_table_count": native.complex_table_count,
        "table_fallback_count": native.table_fallback_count,
    }


def _inspection_from_dict(data: dict[str, Any]) -> PdfInspection:
    return PdfInspection(
        pdf_version=str(data["pdf_version"]),
        file_size=int(data["file_size"]),
        page_count=int(data["page_count"]),
        object_count=int(data["object_count"]),
        max_page_width_points=float(data["max_page_width_points"]),
        max_page_height_points=float(data["max_page_height_points"]),
        has_active_content=bool(data.get("has_active_content", False)),
        has_embedded_files=bool(data.get("has_embedded_files", False)),
        has_external_links=bool(data.get("has_external_links", False)),
        warnings=tuple(str(item) for item in data.get("warnings", [])),
    )


def _classification_from_dict(data: dict[str, Any]) -> PdfClassification:
    pages = tuple(
        PdfPageClassification(
            page_number=int(page["page_number"]),
            kind=PdfPageKind(str(page["kind"])),
            text_char_count=int(page["text_char_count"]),
            image_count=int(page["image_count"]),
            image_coverage_ratio=float(page["image_coverage_ratio"]),
            width_points=float(page["width_points"]),
            height_points=float(page["height_points"]),
            warnings=tuple(str(item) for item in page.get("warnings", [])),
        )
        for page in data["pages"]
    )
    return PdfClassification(
        document_kind=PdfPageKind(str(data["document_kind"])),
        page_count=int(data["page_count"]),
        text_pages=int(data["text_pages"]),
        scanned_pages=int(data["scanned_pages"]),
        mixed_pages=int(data["mixed_pages"]),
        empty_pages=int(data["empty_pages"]),
        pages=pages,
        inspection=_inspection_from_dict(data["inspection"]),
    )


def _native_from_dict(data: dict[str, Any]) -> NativePdfText:
    pages = tuple(
        NativePageText(
            page_number=int(page["page_number"]),
            text=str(page["text"]),
            character_count=int(page["character_count"]),
            word_count=int(page["word_count"]),
            table_count=int(page["table_count"]),
            image_count=int(page["image_count"]),
            warnings=tuple(str(item) for item in page.get("warnings", [])),
            column_count=int(page.get("column_count", 1)),
            structured_table_count=int(page.get("structured_table_count", page["table_count"])),
            complex_table_count=int(page.get("complex_table_count", 0)),
            table_fallback_count=int(page.get("table_fallback_count", 0)),
        )
        for page in data["pages"]
    )
    return NativePdfText(
        pages=pages,
        table_count=int(data["table_count"]),
        image_count=int(data["image_count"]),
        warnings=tuple(str(item) for item in data.get("warnings", [])),
        double_column_pages=tuple(int(item) for item in data.get("double_column_pages", [])),
        structured_table_count=int(data.get("structured_table_count", data["table_count"])),
        complex_table_count=int(data.get("complex_table_count", 0)),
        table_fallback_count=int(data.get("table_fallback_count", 0)),
    )


def _worker_payload(path: Path) -> dict[str, Any]:
    started = time.monotonic()
    try:
        classification = classify_pdf(path)
        native = extract_native_text(path)
        return {
            "ok": True,
            "worker_pid": os.getpid(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "classification": _classification_to_dict(classification),
            "native_text": _native_to_dict(native),
        }
    except ConverterError as exc:
        return {
            "ok": False,
            "error_kind": type(exc).__name__,
            "message": str(exc),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_kind": "UnexpectedError",
            "message": f"PDF 解析子进程发生未预期错误：{type(exc).__name__}: {exc}",
        }


def build_worker_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("source", type=Path)
    parser.add_argument("result", type=Path)
    return parser


def worker_main() -> int:
    args = build_worker_parser().parse_args()
    try:
        write_json_payload(args.result, _worker_payload(args.source))
    except Exception:
        return 3
    return 0


def _raise_worker_error(payload: dict[str, Any]) -> None:
    message = str(payload.get("message") or "PDF 解析子进程未返回错误详情。")
    if payload.get("error_kind") == "ValidationError":
        raise ValidationError(message)
    raise PdfExtractionError(message)


def parse_pdf_isolated(
    path: Path,
    *,
    timeout_seconds: float = PDF_PARSE_TIMEOUT_SECONDS,
) -> IsolatedPdfParseResult:
    """在一次性子进程中完成 PDF 预检、分类和原生文字提取。"""
    if timeout_seconds <= 0:
        raise ValueError("PDF 解析超时必须大于 0 秒。")
    path = Path(path).resolve(strict=False)

    with tempfile.TemporaryDirectory(prefix="local_doc_pdf_parse_") as temporary:
        result_path = Path(temporary) / "result.json"
        command = [
            sys.executable,
            "-c",
            (
                "from local_doc_converter.pdf.parse_worker import worker_main; "
                "raise SystemExit(worker_main())"
            ),
            str(path),
            str(result_path),
        ]
        process = start_worker(command, stage_label="PDF 解析")

        try:
            process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            terminate_process_group(process)
            raise PdfExtractionError(
                f"PDF 解析超过 {timeout_seconds:g} 秒，已终止独立子进程。"
            ) from exc

        if process.returncode != 0:
            raise PdfExtractionError(
                f"PDF 解析子进程异常退出（退出码 {process.returncode}）。"
            )
        try:
            payload = read_json_payload(result_path, stage_label="PDF 解析")
            if not payload.get("ok"):
                _raise_worker_error(payload)
            return IsolatedPdfParseResult(
                classification=_classification_from_dict(payload["classification"]),
                native_text=_native_from_dict(payload["native_text"]),
                worker_pid=int(payload["worker_pid"]),
                duration_seconds=float(payload["duration_seconds"]),
            )
        except ConverterError:
            raise
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise PdfExtractionError(f"PDF 解析子进程返回了无效结果：{exc}") from exc


if __name__ == "__main__":
    raise SystemExit(worker_main())

"""运行真实 PDF 样例集并生成可机器读取的质量报告。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from local_doc_converter.converter import DocumentConverter  # noqa: E402
from local_doc_converter.errors import ValidationError  # noqa: E402
from local_doc_converter.pdf.corpus import (  # noqa: E402
    PdfCorpusSample,
    load_pdf_corpus_manifest,
    verify_pdf_corpus_file,
)

DEFAULT_MANIFEST = PROJECT_ROOT / "quality" / "pdf_corpus" / "manifest.json"
DEFAULT_INPUT = PROJECT_ROOT / "quality" / "pdf_corpus" / "downloads"
DEFAULT_RESULTS = PROJECT_ROOT / "quality" / "pdf_corpus" / "results"
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="评估真实 PDF → TXT 质量样例")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--sample", action="append", dest="sample_ids", help="只评估指定样例 ID，可重复")
    return parser


def _selected(samples: tuple[PdfCorpusSample, ...], requested: list[str] | None) -> tuple[PdfCorpusSample, ...]:
    if not requested:
        return samples
    index = {sample.sample_id: sample for sample in samples}
    missing = sorted(set(requested) - set(index))
    if missing:
        raise ValidationError(f"未找到 PDF 样例：{', '.join(missing)}")
    return tuple(index[sample_id] for sample_id in dict.fromkeys(requested))


def _acceptance_failures(sample: PdfCorpusSample, report: dict[str, Any], output_text: str) -> list[str]:
    failures: list[str] = []
    if not report.get("success"):
        failures.append(str(report.get("error") or "转换失败"))
        return failures
    details = report.get("details") or {}
    if details.get("page_count") != sample.expected_page_count:
        failures.append(
            f"页数期望 {sample.expected_page_count}，实际 {details.get('page_count')}。"
        )
    acceptance = sample.acceptance
    expected_pdf_kind = acceptance.get("expected_pdf_kind")
    if expected_pdf_kind and details.get("pdf_kind") != expected_pdf_kind:
        failures.append(f"PDF 类型期望 {expected_pdf_kind}，实际 {details.get('pdf_kind')}。")
    page_count_checks = {
        "expected_native_pages": len(details.get("native_page_numbers", [])),
        "expected_ocr_pages": len(details.get("ocr_page_numbers", [])),
        "expected_empty_pages": int(details.get("empty_page_count", 0)),
    }
    for key, actual in page_count_checks.items():
        if key in acceptance and actual != int(acceptance[key]):
            failures.append(f"{key} 期望 {acceptance[key]}，实际 {actual}。")
    if len(output_text) < int(acceptance.get("min_output_characters", 1)):
        failures.append("输出可见字符数低于阈值。")
    cjk_characters = len(_CJK_RE.findall(output_text))
    latin_characters = len(_LATIN_RE.findall(output_text))
    if cjk_characters < int(acceptance.get("min_cjk_characters", 0)):
        failures.append("输出中日韩统一表意文字数低于阈值。")
    if latin_characters < int(acceptance.get("min_latin_characters", 0)):
        failures.append("输出拉丁字母数低于阈值。")
    if int(details.get("structured_table_count", 0)) < int(acceptance.get("min_structured_tables", 0)):
        failures.append("结构化表格数低于阈值。")
    if int(details.get("complex_table_count", 0)) < int(acceptance.get("min_complex_tables", 0)):
        failures.append("复杂表格数低于阈值。")
    if "min_ocr_mean_confidence" in acceptance:
        confidences = [
            float(page["ocr_mean_confidence"])
            for page in details.get("pages", [])
            if page.get("ocr_mean_confidence") is not None
        ]
        mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        if mean_confidence < float(acceptance["min_ocr_mean_confidence"]):
            failures.append(
                f"OCR 平均置信度低于阈值：期望至少 {acceptance['min_ocr_mean_confidence']}，"
                f"实际 {mean_confidence:.4f}。"
            )
    required_warning = acceptance.get("required_warning_contains")
    if required_warning and not any(required_warning in warning for warning in report.get("warnings", [])):
        failures.append(f"未生成期望警告：{required_warning}。")
    return failures


def main() -> int:
    args = build_parser().parse_args()
    try:
        manifest = load_pdf_corpus_manifest(args.manifest)
        samples = _selected(manifest.samples, args.sample_ids)
    except ValidationError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    # 微秒后缀避免连续执行或并行触发时撞到同一个结果目录。
    run_id = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    run_dir = args.results / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    converter = DocumentConverter()
    records: list[dict[str, Any]] = []
    failed = 0

    for sample in samples:
        source = args.input / sample.filename
        integrity_errors = verify_pdf_corpus_file(sample, source)
        if integrity_errors:
            failed += 1
            records.append(
                {
                    "id": sample.sample_id,
                    "status": "failed",
                    "failures": list(integrity_errors),
                    "manual_review": sample.manual_review,
                }
            )
            continue

        sample_output = run_dir / sample.sample_id
        result = converter.convert(source, "txt", sample_output)
        report = result.report.to_dict()
        output_text = result.output_path.read_text(encoding="utf-8") if result.output_path else ""
        failures = _acceptance_failures(sample, report, output_text)
        failed += bool(failures)
        records.append(
            {
                "id": sample.sample_id,
                "status": "failed" if failures else "passed",
                "failures": failures,
                "output_characters": len(output_text),
                "language_metrics": {
                    "cjk_characters": len(_CJK_RE.findall(output_text)),
                    "latin_characters": len(_LATIN_RE.findall(output_text)),
                },
                "report": report,
                "manual_review": sample.manual_review,
            }
        )
        print(f"{sample.sample_id}: {'FAILED' if failures else 'PASSED'}")

    summary = {
        "schema_version": 1,
        "run_id": run_id,
        "sample_count": len(records),
        "automated_passed": len(records) - failed,
        "automated_failed": failed,
        "records": records,
    }
    summary_path = run_dir / "quality_report.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"质量报告：{summary_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

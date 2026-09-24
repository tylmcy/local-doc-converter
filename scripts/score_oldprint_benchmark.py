"""在本地冻结参考文本上运行固定五页旧印刷体正式评分。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_doc_converter.pdf.accuracy import (  # noqa: E402
    normalize_for_scoring,
    score_pages,
)
from local_doc_converter.pdf.pipeline import PdfToTextConverter  # noqa: E402

SOURCE = ROOT / "quality/pdf_corpus/downloads/chinese-churchman-1949-scan.pdf"
BENCHMARK_DIR = ROOT / "quality/pdf_corpus/local-oldprint-benchmark"
METADATA_PATH = BENCHMARK_DIR / "draft-metadata.json"
EXPECTED_SOURCE_SHA256 = "42a672355b951c91324ce4f5ae7b1ddbced6c096fe5adca69c9f828c91a7a04f"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _diagnostic_samples(reference: str, output: str, *, limit: int = 12) -> list[dict[str, str]]:
    """提取可追溯差异片段；正式分数仍以 Levenshtein 为准。"""
    expected = normalize_for_scoring(reference)
    actual = normalize_for_scoring(output)
    matcher = SequenceMatcher(None, expected, actual, autojunk=False)
    samples: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        samples.append(
            {
                "kind": {"replace": "替换", "delete": "漏字", "insert": "多字"}[tag],
                "reference": expected[i1:i2],
                "output": actual[j1:j2],
                "reference_context": expected[max(0, i1 - 8) : min(len(expected), i2 + 8)],
                "output_context": actual[max(0, j1 - 8) : min(len(actual), j2 + 8)],
            }
        )
        if len(samples) >= limit:
            break
    return samples


def _load_frozen_references() -> tuple[list[str], dict[str, object], list[str]]:
    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    if metadata.get("status") != "human_reference_frozen":
        raise RuntimeError("本地参考文本尚未冻结，拒绝正式评分。")
    expected_hashes = metadata.get("reference_sha256")
    expected_counts = metadata.get("reference_normalized_characters")
    if not isinstance(expected_hashes, list) or len(expected_hashes) != 5:
        raise RuntimeError("冻结元数据缺少五页参考哈希。")
    if not isinstance(expected_counts, list) or len(expected_counts) != 5:
        raise RuntimeError("冻结元数据缺少五页参考字数。")

    references: list[str] = []
    actual_hashes: list[str] = []
    for number in range(1, 6):
        path = BENCHMARK_DIR / f"page-{number}.txt"
        digest = _sha256(path)
        if digest != expected_hashes[number - 1]:
            raise RuntimeError(f"第 {number} 页参考文本已在冻结后改变，拒绝评分。")
        text = path.read_text(encoding="utf-8")
        if len(normalize_for_scoring(text)) != expected_counts[number - 1]:
            raise RuntimeError(f"第 {number} 页参考文本字数与冻结元数据不一致。")
        references.append(text)
        actual_hashes.append(digest)
    return references, metadata, actual_hashes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=("standard", "old_print"),
        default="standard",
        help="OCR 预处理配置；发布门槛默认使用生产配置 standard。",
    )
    args = parser.parse_args()

    if not SOURCE.is_file() or _sha256(SOURCE) != EXPECTED_SOURCE_SHA256:
        print("固定五页 PDF 缺失或 SHA-256 不符。", file=sys.stderr)
        return 1
    try:
        references, metadata, reference_hashes = _load_frozen_references()
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1

    conversion = PdfToTextConverter(ocr_profile=args.profile).convert(SOURCE)
    if len(conversion.pages) != 5:
        print("转换结果不是预期的五个 PDF 页面。", file=sys.stderr)
        return 1
    outputs = [page.text for page in conversion.pages]
    score = score_pages(references, outputs)

    page_records: list[dict[str, object]] = []
    output_hashes: list[str] = []
    for page, reference, output in zip(score.pages, references, outputs):
        output_path = BENCHMARK_DIR / f"output-{args.profile}-page-{page.page_number}.txt"
        output_path.write_text(output.rstrip() + "\n", encoding="utf-8")
        output_hash = _sha256(output_path)
        output_hashes.append(output_hash)
        page_result = conversion.pages[page.page_number - 1]
        page_records.append(
            {
                "page_number": page.page_number,
                "reference_characters": page.reference_characters,
                "output_characters": page.output_characters,
                "edit_distance": page.edit_distance,
                "accuracy": page.accuracy,
                "reference_sha256": reference_hashes[page.page_number - 1],
                "output_sha256": output_hash,
                "ocr_recognition_model": page_result.ocr_recognition_model,
                "ocr_preprocessing_profile": page_result.ocr_preprocessing_profile,
                "diagnostic_diff_samples": _diagnostic_samples(reference, output),
            }
        )

    result = {
        "evaluated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_sha256": EXPECTED_SOURCE_SHA256,
        "reference_status": metadata["status"],
        "reference_policy": metadata["reference_policy"],
        "reference_frozen_at": metadata["frozen_at"],
        "scoring_version": score.scoring_version,
        "ocr_profile": conversion.ocr_profile,
        "recognition_models": sorted(
            {
                page.ocr_recognition_model
                for page in conversion.pages
                if page.ocr_recognition_model is not None
            }
        ),
        "reference_characters": score.reference_characters,
        "edit_distance": score.edit_distance,
        "accuracy": score.accuracy,
        "passes_release_gate": score.passes_release_gate,
        "pages": page_records,
        "notes": [
            "正式分数使用逐页 Levenshtein；差异样例仅用于诊断，不参与计分。",
            "参考文本、机器输出和完整结果均位于 Git 忽略目录。",
        ],
    }
    result_path = BENCHMARK_DIR / f"formal-score-{args.profile}.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "result": str(result_path),
        "accuracy": score.accuracy,
        "passes_release_gate": score.passes_release_gate,
        "pages": [
            {
                "page_number": page.page_number,
                "accuracy": page.accuracy,
                "edit_distance": page.edit_distance,
                "reference_characters": page.reference_characters,
            }
            for page in score.pages
        ],
        "output_sha256": output_hashes,
    }, ensure_ascii=False, indent=2))
    return 0 if score.passes_release_gate else 2


if __name__ == "__main__":
    raise SystemExit(main())

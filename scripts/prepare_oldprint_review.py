"""生成仅供人工逐页校对的旧印刷体 OCR 草稿；不生成标准答案。"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_doc_converter.pdf.pipeline import PdfToTextConverter  # noqa: E402

SOURCE = ROOT / "quality/pdf_corpus/downloads/chinese-churchman-1949-scan.pdf"
OUTPUT = ROOT / "quality/pdf_corpus/local-oldprint-benchmark"
EXPECTED_SHA256 = "42a672355b951c91324ce4f5ae7b1ddbced6c096fe5adca69c9f828c91a7a04f"


def main() -> int:
    if not SOURCE.is_file() or hashlib.sha256(SOURCE.read_bytes()).hexdigest() != EXPECTED_SHA256:
        print("固定五页 PDF 缺失或 SHA-256 不符。", file=sys.stderr)
        return 1
    page_paths = [OUTPUT / f"page-{number}.txt" for number in range(1, 6)]
    if any(path.exists() for path in page_paths):
        print("本地逐页草稿已存在；为保护人工修改，拒绝覆盖。", file=sys.stderr)
        return 1

    result = PdfToTextConverter().convert(SOURCE)
    if len(result.pages) != 5 or tuple(page.page_number for page in result.pages) != (1, 2, 3, 4, 5):
        print("转换结果不是预期的五个 PDF 页面。", file=sys.stderr)
        return 1

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for path, page in zip(page_paths, result.pages):
        # 这是 OCR 生成的待校对草稿；不能直接作为独立人工标准答案评分。
        path.write_text(page.text.rstrip() + "\n", encoding="utf-8")
    metadata = {
        "source_sha256": EXPECTED_SHA256,
        "page_count": 5,
        "status": "ocr_draft_not_human_verified",
        "ocr_profile": result.ocr_profile,
        "recognition_models": [page.ocr_recognition_model for page in result.pages],
        "reference_policy": "docs/validation/old-print-benchmark-protocol.md",
    }
    (OUTPUT / "draft-metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已生成五份待校对草稿：{OUTPUT}")
    print("警告：这是 OCR 草稿，不是标准答案；人工逐页复核并冻结前不得正式评分。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

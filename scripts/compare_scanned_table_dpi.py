"""对自制扫描表格进行 150/200/300 DPI 的离线 OCR 对照。"""

from __future__ import annotations

import argparse
import json
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_doc_converter.pdf.ocr import PaddleOcrEngine, inspect_ocr_environment  # noqa: E402

FIXTURES = ROOT / "quality" / "pdf_corpus" / "generated"
TRUTH = ROOT / "quality" / "pdf_corpus" / "scanned_table_truth.json"
RESULTS = ROOT / "quality" / "pdf_corpus" / "results"


def _compact(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def score_table(text: str, truth: dict) -> dict:
    """分别评估文字召回、行序和 Tab 列结构，不用置信度代替质量。"""
    compact = _compact(text)
    cells = [cell for row in [truth["headers"], *truth["rows"]] for cell in row]
    found = [_compact(cell) in compact for cell in cells]
    row_positions = [compact.find(_compact(row[1])) for row in truth["rows"]]
    structured_lines = [line.split("\t") for line in text.splitlines() if "\t" in line]
    column_count = len(truth["headers"])
    tabular_rows = sum(
        len(fields) == column_count and sum(bool(field.strip()) for field in fields) >= 2
        for fields in structured_lines
    )
    return {
        "matched_cells": sum(found),
        "total_cells": len(cells),
        "cell_recall": round(sum(found) / len(cells), 4),
        "missing_cells": [cell for cell, matched in zip(cells, found) if not matched],
        "project_row_order_ok": all(
            left >= 0 and right > left
            for left, right in zip(row_positions, row_positions[1:])
        ),
        "tabular_rows": tabular_rows,
        "tabular_structure_restored": tabular_rows >= len(truth["rows"]) + 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=FIXTURES)
    parser.add_argument("--truth", type=Path, default=TRUTH)
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--dpi", type=int, action="append", dest="dpis")
    args = parser.parse_args()
    dpis = tuple(dict.fromkeys(args.dpis or (150, 200, 300)))
    if any(dpi < 72 or dpi > 400 for dpi in dpis):
        parser.error("DPI 必须在 72–400 之间。")
    status = inspect_ocr_environment()
    if not status.ready or status.model_root is None:
        parser.error(status.message)
    truth = json.loads(args.truth.read_text(encoding="utf-8"))
    run_id = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    output = args.results / f"scanned-table-dpi-{run_id}"
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for ppi in (150, 300):
        source = args.fixtures / f"scanned-table-{ppi}ppi.pdf"
        if not source.is_file():
            parser.error(f"缺少扫描表格样例：{source}；先运行生成脚本。")
        for dpi in dpis:
            engine = PaddleOcrEngine(status.model_root, dpi=dpi)
            started = time.monotonic()
            result = engine.recognize_page(source, 1)
            duration = round(time.monotonic() - started, 2)
            name = f"{ppi}ppi-{dpi}dpi"
            (output / f"{name}.txt").write_text(result.text + "\n", encoding="utf-8")
            record = {
                "source_ppi": ppi,
                "render_dpi": dpi,
                "recognition_model": result.recognition_model,
                "duration_seconds_including_model_init": duration,
                "line_count": result.line_count,
                "mean_confidence": result.mean_confidence,
                **score_table(result.text, truth),
            }
            records.append(record)
            print(
                f"{name}: {record['matched_cells']}/{record['total_cells']} cells, "
                f"row_order={record['project_row_order_ok']}, "
                f"confidence={result.mean_confidence}, {duration}s",
                flush=True,
            )
    report = {"schema_version": 1, "run_id": run_id, "records": records}
    (output / "comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"完整结果：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

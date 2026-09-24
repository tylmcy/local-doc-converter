"""便于自动化与演示的命令行入口。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .batch import BatchProcessor
from .config import TARGET_EXTENSIONS
from .converter import DocumentConverter
from .errors import ConverterError
from .pdf.ocr import OCR_PROFILES
from .pdf.pipeline import PdfToTextConverter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="本地离线 TXT、Markdown、DOCX 互转与 PDF 转 TXT 工具"
    )
    parser.add_argument("inputs", nargs="+", type=Path, help="一个或多个输入文件")
    parser.add_argument("--to", required=True, choices=TARGET_EXTENSIONS, dest="target", help="目标格式")
    parser.add_argument("--output", type=Path, default=Path.cwd() / "converted", help="输出目录")
    parser.add_argument(
        "--ocr-profile", choices=OCR_PROFILES, default="standard",
        help="扫描 PDF 的 OCR 参数：standard 或实验性 old_print（300 DPI + 中值去噪）",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        converter = DocumentConverter(pdf_converter=PdfToTextConverter(ocr_profile=args.ocr_profile))
        result = BatchProcessor(converter).process_paths(args.inputs, args.target, args.output)
    except (ConverterError, OSError) as exc:
        print(f"错误：{exc}")
        return 2

    print(json.dumps({
        "success": result.successful_count,
        "failed": result.failed_count,
        "reports": [item.report.to_dict() for item in result.results],
    }, ensure_ascii=False, indent=2))
    return 0 if result.failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

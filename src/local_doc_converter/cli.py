"""便于自动化与演示的命令行入口。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .batch import BatchProcessor
from .config import TARGET_EXTENSIONS
from .errors import ConverterError
from .pandoc import PandocRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本地离线 TXT、Markdown、DOCX 文档互转工具")
    parser.add_argument("inputs", nargs="+", type=Path, help="一个或多个输入文件")
    parser.add_argument("--to", required=True, choices=TARGET_EXTENSIONS, dest="target", help="目标格式")
    parser.add_argument("--output", type=Path, default=Path.cwd() / "converted", help="输出目录")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    pandoc = PandocRunner()
    try:
        print(pandoc.version())
        result = BatchProcessor().process_paths(args.inputs, args.target, args.output)
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

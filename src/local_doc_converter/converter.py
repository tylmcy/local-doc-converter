"""单文件转换编排。"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path

from .ast_tools import collect_stats, inspect_and_secure_images, validate_ast
from .config import TARGET_EXTENSIONS
from .docx_safety import inspect_docx
from .docx_style import apply_basic_docx_styles
from .encoding import decode_text
from .errors import ConverterError, ValidationError
from .models import ConversionReport, ConversionResult
from .output_cleanup import normalize_markdown
from .pandoc import PandocRunner
from .security import ensure_output_dir, source_format_for, unique_path, validate_file_size, validate_target
from .text_parser import parse_txt_structure


def _possible_losses(source_format: str, target_format: str) -> list[str]:
    losses: list[str] = []
    if target_format == "txt":
        losses.append("TXT 不保留字体、颜色、精确列表样式、表格边框和图片本体。")
    if source_format == "docx":
        losses.append("页眉页脚、批注、修订记录、文本框、宏、SmartArt 和精确分页可能丢失。")
    if source_format == "docx" and target_format == "markdown":
        losses.append("复杂表格、浮动图片位置和 Word 专有样式可能被简化。")
    if source_format == "markdown" and target_format == "docx":
        losses.append("部分 Markdown 扩展语法和 HTML/CSS 样式可能被简化。")
    return losses


class DocumentConverter:
    def __init__(self, pandoc: PandocRunner | None = None) -> None:
        self.pandoc = pandoc or PandocRunner()

    def convert(self, source_path: Path, target_format: str, output_dir: Path | str) -> ConversionResult:
        """转换一个文件；预期错误会进入报告，不让批次中的其他文件中断。"""
        started = time.monotonic()
        source_path = Path(source_path).resolve(strict=False)
        source_name = source_path.name or "未知文件"
        output_directory: Path | None = None
        output_path: Path | None = None
        report_path: Path | None = None
        copied_assets: list[Path] = []
        source_format = "unknown"
        report = ConversionReport(
            source_file=source_name,
            target_file="",
            source_format=source_format,
            target_format=target_format,
        )

        try:
            output_directory = ensure_output_dir(output_dir)
            if not source_path.is_file():
                raise ValidationError("输入文件不存在或不是普通文件。")
            validate_file_size(source_path.stat().st_size)
            source_format = source_format_for(source_path)
            report.source_format = source_format
            report.skipped = source_format == target_format
            validate_target(source_format, target_format)
            if source_format == "docx":
                inspection = inspect_docx(source_path)
                report.warnings.extend(inspection.warnings)
            self.pandoc.require()

            output_name = f"{source_path.stem}{TARGET_EXTENSIONS[target_format]}"
            output_path = unique_path(output_directory, output_name)
            report.target_file = output_path.name

            with tempfile.TemporaryDirectory(prefix="local_doc_convert_") as temporary:
                work_dir = Path(temporary)
                ast_path = work_dir / "document.json"
                input_for_pandoc = source_path
                extract_media_name: str | None = None
                base_dir = source_path.parent

                if source_format == "txt":
                    decoded = decode_text(source_path.read_bytes())
                    report.detected_encoding = decoded.encoding
                    parsed = parse_txt_structure(decoded.text)
                    report.warnings.extend(parsed.warnings)
                    input_for_pandoc = work_dir / "normalized.md"
                    input_for_pandoc.write_text(parsed.markdown, encoding="utf-8")
                    base_dir = source_path.parent

                if source_format == "docx" and target_format == "markdown":
                    extract_media_name = f"{output_path.stem}_assets"
                    suffix = 2
                    while (output_directory / extract_media_name).exists():
                        extract_media_name = f"{output_path.stem}_assets_{suffix}"
                        suffix += 1
                    base_dir = work_dir

                document = self.pandoc.read_to_ast(
                    input_for_pandoc,
                    source_format if source_format != "txt" else "txt",
                    ast_path,
                    cwd=work_dir,
                    extract_media=extract_media_name,
                )
                document = validate_ast(document)
                report.stats = collect_stats(document)
                report.warnings.extend(inspect_and_secure_images(document, base_dir, target_format))
                ast_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

                staged_output = work_dir / output_path.name
                resource_paths = [base_dir]
                self.pandoc.write_from_ast(
                    ast_path,
                    target_format,
                    staged_output,
                    cwd=work_dir,
                    resource_paths=resource_paths,
                )
                if target_format == "markdown":
                    normalize_markdown(staged_output)
                if target_format == "docx":
                    try:
                        apply_basic_docx_styles(staged_output)
                    except Exception as exc:  # 样式补充失败不应丢掉已经成功的转换结果。
                        report.warnings.append(f"DOCX 已生成，但基础中文样式补充失败：{exc}")

                shutil.copy2(staged_output, output_path)
                if extract_media_name:
                    staged_assets = work_dir / extract_media_name
                    if staged_assets.exists():
                        final_assets = output_directory / extract_media_name
                        shutil.copytree(staged_assets, final_assets)
                        copied_assets.append(final_assets)

            report.success = True
            report.possible_losses = _possible_losses(source_format, target_format)
        except (ConverterError, OSError, ValueError) as exc:
            if report.skipped:
                report.warnings.append("源格式与目标格式相同，已跳过转换。")
                report.error = None
            else:
                report.error = str(exc)
            report.success = False
            if output_path and output_path.exists():
                output_path.unlink(missing_ok=True)
            for asset in copied_assets:
                if asset.exists():
                    shutil.rmtree(asset)
            output_path = None
            copied_assets = []
        except Exception as exc:  # 不把内部堆栈暴露到网页，但保留可理解的错误类别。
            report.error = f"转换时发生未预期错误：{type(exc).__name__}: {exc}"
            report.success = False
            if output_path and output_path.exists():
                output_path.unlink(missing_ok=True)
            for asset in copied_assets:
                if asset.exists():
                    shutil.rmtree(asset)
            output_path = None
            copied_assets = []
        finally:
            report.duration_seconds = round(time.monotonic() - started, 3)
            if output_directory is not None:
                safe_stem = source_path.stem or "conversion"
                report_path = unique_path(output_directory, f"{safe_stem}_report.json")
                try:
                    report_path.write_text(
                        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except OSError:
                    report_path = None

        return ConversionResult(
            report=report,
            output_path=output_path if report.success else None,
            report_path=report_path,
            asset_paths=copied_assets,
        )

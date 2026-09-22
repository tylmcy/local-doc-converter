"""批量上传、隔离临时文件与 ZIP 打包。"""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable

from .converter import DocumentConverter
from .models import BatchResult, ConversionResult, UploadedDocument
from .security import safe_filename, unique_path, validate_batch

ProgressCallback = Callable[[int, int, str], None]


class BatchProcessor:
    def __init__(self, converter: DocumentConverter | None = None) -> None:
        self.converter = converter or DocumentConverter()

    def process_uploads(
        self,
        uploads: list[UploadedDocument],
        target_format: str,
        output_dir: str | Path,
        on_progress: ProgressCallback | None = None,
    ) -> BatchResult:
        validate_batch([len(upload.data) for upload in uploads])
        results: list[ConversionResult] = []

        # 上传内容仅在此上下文存活；退出时 TemporaryDirectory 会递归清理。
        with tempfile.TemporaryDirectory(prefix="local_doc_batch_") as temporary:
            input_dir = Path(temporary) / "inputs"
            input_dir.mkdir()
            total = len(uploads)
            for index, upload in enumerate(uploads, start=1):
                name = safe_filename(upload.name)
                path = unique_path(input_dir, name)
                path.write_bytes(upload.data)
                results.append(self.converter.convert(path, target_format, output_dir))
                if on_progress:
                    on_progress(index, total, name)

        return self._build_batch_result(results)

    def process_paths(
        self,
        paths: list[Path],
        target_format: str,
        output_dir: str | Path,
        on_progress: ProgressCallback | None = None,
    ) -> BatchResult:
        validate_batch([path.stat().st_size for path in paths])
        results: list[ConversionResult] = []
        total = len(paths)
        for index, path in enumerate(paths, start=1):
            results.append(self.converter.convert(path, target_format, output_dir))
            if on_progress:
                on_progress(index, total, path.name)
        return self._build_batch_result(results)

    def _build_batch_result(self, results: list[ConversionResult]) -> BatchResult:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for result in results:
                if result.output_path and result.output_path.exists():
                    archive.write(result.output_path, arcname=f"outputs/{result.output_path.name}")
                if result.report_path and result.report_path.exists():
                    archive.write(result.report_path, arcname=f"reports/{result.report_path.name}")
                for asset_root in result.asset_paths:
                    if asset_root.is_dir():
                        for asset in asset_root.rglob("*"):
                            if asset.is_file():
                                archive.write(asset, arcname=f"outputs/{asset.relative_to(asset_root.parent)}")
            batch_report = {
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "total": len(results),
                "successful": sum(item.report.success for item in results),
                "skipped": sum(item.report.skipped for item in results),
                "failed": sum(not item.report.success and not item.report.skipped for item in results),
                "reports": [item.report.to_dict() for item in results],
            }
            archive.writestr("batch_report.json", json.dumps(batch_report, ensure_ascii=False, indent=2))

        return BatchResult(results=results, zip_bytes=buffer.getvalue(), zip_name=f"converted_{timestamp}.zip")

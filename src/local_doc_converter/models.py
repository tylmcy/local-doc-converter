"""转换流程使用的数据模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class DocumentStats:
    headings: int = 0
    lists: int = 0
    tables: int = 0
    code_blocks: int = 0
    links: int = 0
    images: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(slots=True)
class ConversionReport:
    source_file: str
    target_file: str
    source_format: str
    target_format: str
    success: bool = False
    skipped: bool = False
    duration_seconds: float = 0.0
    detected_encoding: str | None = None
    stats: DocumentStats = field(default_factory=DocumentStats)
    warnings: list[str] = field(default_factory=list)
    possible_losses: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["stats"] = self.stats.to_dict()
        return data


@dataclass(slots=True)
class ConversionResult:
    report: ConversionReport
    output_path: Path | None = None
    report_path: Path | None = None
    asset_paths: list[Path] = field(default_factory=list)


@dataclass(slots=True)
class UploadedDocument:
    name: str
    data: bytes


@dataclass(slots=True)
class BatchResult:
    results: list[ConversionResult]
    zip_bytes: bytes
    zip_name: str

    @property
    def successful_count(self) -> int:
        return sum(result.report.success for result in self.results)

    @property
    def failed_count(self) -> int:
        return len(self.results) - self.successful_count - self.skipped_count

    @property
    def skipped_count(self) -> int:
        return sum(result.report.skipped for result in self.results)

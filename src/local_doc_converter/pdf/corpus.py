"""真实 PDF 质量样例清单的加载与文件完整性校验。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..errors import ValidationError

_SAMPLE_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GIT_REVISION_RE = re.compile(r"[0-9a-f]{40}")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_REVISION_KINDS = {"git-commit", "upstream-sha1", "retrieval-date"}


@dataclass(frozen=True, slots=True)
class PdfCorpusSample:
    sample_id: str
    filename: str
    source_url: str
    source_page_url: str
    source_repository: str
    source_revision: str
    source_revision_kind: str
    source_license: str
    redistribution: str
    sha256: str
    file_size: int
    language: str
    categories: tuple[str, ...]
    expected_page_count: int
    acceptance: dict[str, Any]
    manual_review: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PdfCorpusManifest:
    schema_version: int
    samples: tuple[PdfCorpusSample, ...]


def _required_text(data: dict[str, Any], key: str, *, sample_id: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"PDF 样例 {sample_id} 的 {key} 必须是非空字符串。")
    return value.strip()


def _required_https_url(data: dict[str, Any], key: str, *, sample_id: str) -> str:
    value = _required_text(data, key, sample_id=sample_id)
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValidationError(f"PDF 样例 {sample_id} 的 {key} 必须使用 HTTPS。")
    return value


def load_pdf_corpus_manifest(path: Path) -> PdfCorpusManifest:
    """以严格但轻量的方式加载质量集清单。"""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"无法读取 PDF 样例清单：{exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValidationError("PDF 样例清单 schema_version 必须为 1。")
    raw_samples = payload.get("samples")
    if not isinstance(raw_samples, list) or not raw_samples:
        raise ValidationError("PDF 样例清单至少需要一个样例。")

    samples: list[PdfCorpusSample] = []
    seen_ids: set[str] = set()
    seen_filenames: set[str] = set()
    for index, data in enumerate(raw_samples, start=1):
        if not isinstance(data, dict):
            raise ValidationError(f"PDF 样例清单第 {index} 项必须是对象。")
        sample_id = _required_text(data, "id", sample_id=f"#{index}")
        if not _SAMPLE_ID_RE.fullmatch(sample_id) or sample_id in seen_ids:
            raise ValidationError(f"PDF 样例 ID 无效或重复：{sample_id}。")
        filename = _required_text(data, "filename", sample_id=sample_id)
        if Path(filename).name != filename or Path(filename).suffix.lower() != ".pdf":
            raise ValidationError(f"PDF 样例 {sample_id} 的文件名不安全。")
        if filename in seen_filenames:
            raise ValidationError(f"PDF 样例文件名重复：{filename}。")
        source_url = _required_https_url(data, "source_url", sample_id=sample_id)
        source_page_url = _required_https_url(data, "source_page_url", sample_id=sample_id)
        source_revision = _required_text(data, "source_revision", sample_id=sample_id).lower()
        source_revision_kind = data.get("source_revision_kind", "git-commit")
        if source_revision_kind not in _REVISION_KINDS:
            raise ValidationError(f"PDF 样例 {sample_id} 的版本固定方式无效。")
        if source_revision_kind == "git-commit" and (
            not _GIT_REVISION_RE.fullmatch(source_revision) or source_revision not in source_url
        ):
            raise ValidationError(f"PDF 样例 {sample_id} 的固定源码版本无效。")
        if source_revision_kind == "upstream-sha1" and not _GIT_REVISION_RE.fullmatch(source_revision):
            raise ValidationError(f"PDF 样例 {sample_id} 的上游 SHA-1 无效。")
        if source_revision_kind == "retrieval-date" and not _DATE_RE.fullmatch(source_revision):
            raise ValidationError(f"PDF 样例 {sample_id} 的获取日期无效。")
        sha256 = _required_text(data, "sha256", sample_id=sample_id).lower()
        if not _SHA256_RE.fullmatch(sha256):
            raise ValidationError(f"PDF 样例 {sample_id} 的 SHA-256 无效。")
        file_size = data.get("file_size")
        expected_page_count = data.get("expected_page_count")
        categories = data.get("categories")
        acceptance = data.get("acceptance")
        manual_review = data.get("manual_review")
        if not isinstance(file_size, int) or file_size <= 0:
            raise ValidationError(f"PDF 样例 {sample_id} 的文件大小无效。")
        if not isinstance(expected_page_count, int) or expected_page_count <= 0:
            raise ValidationError(f"PDF 样例 {sample_id} 的页数无效。")
        if not isinstance(categories, list) or not categories or not all(
            isinstance(item, str) and item for item in categories
        ):
            raise ValidationError(f"PDF 样例 {sample_id} 的 categories 无效。")
        if not isinstance(acceptance, dict) or not isinstance(manual_review, dict):
            raise ValidationError(f"PDF 样例 {sample_id} 缺少验收或人工复核信息。")

        samples.append(
            PdfCorpusSample(
                sample_id=sample_id,
                filename=filename,
                source_url=source_url,
                source_page_url=source_page_url,
                source_repository=_required_text(data, "source_repository", sample_id=sample_id),
                source_revision=source_revision,
                source_revision_kind=source_revision_kind,
                source_license=_required_text(data, "source_license", sample_id=sample_id),
                redistribution=_required_text(data, "redistribution", sample_id=sample_id),
                sha256=sha256,
                file_size=file_size,
                language=_required_text(data, "language", sample_id=sample_id),
                categories=tuple(categories),
                expected_page_count=expected_page_count,
                acceptance=dict(acceptance),
                manual_review=dict(manual_review),
            )
        )
        seen_ids.add(sample_id)
        seen_filenames.add(filename)
    return PdfCorpusManifest(schema_version=1, samples=tuple(samples))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_pdf_corpus_file(sample: PdfCorpusSample, path: Path) -> tuple[str, ...]:
    """检查文件名、大小、文件头和哈希，不进入 PDF 解析器。"""
    path = Path(path)
    errors: list[str] = []
    if path.name != sample.filename:
        errors.append("文件名与清单不一致。")
    try:
        stat = path.stat()
        if not path.is_file():
            return ("路径不是普通文件。",)
        if stat.st_size != sample.file_size:
            errors.append(f"文件大小不一致：期望 {sample.file_size}，实际 {stat.st_size}。")
        with path.open("rb") as stream:
            if not stream.read(5).startswith(b"%PDF-"):
                errors.append("文件头不是 PDF。")
        actual_hash = sha256_file(path)
        if actual_hash != sample.sha256:
            errors.append(f"SHA-256 不一致：{actual_hash}。")
    except OSError as exc:
        errors.append(f"无法读取文件：{exc}")
    return tuple(errors)

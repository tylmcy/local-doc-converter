"""在 Pandoc 读取前对 DOCX 的 ZIP 容器做只读安全预检。"""

from __future__ import annotations

import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import (
    DOCX_MAX_ENTRIES,
    DOCX_MAX_MEMBER_COMPRESSION_RATIO,
    DOCX_MAX_MEMBER_UNCOMPRESSED_SIZE,
    DOCX_MAX_TOTAL_COMPRESSION_RATIO,
    DOCX_MAX_TOTAL_UNCOMPRESSED_SIZE,
)
from .errors import ValidationError

_REQUIRED_MEMBERS = frozenset({"[Content_Types].xml", "word/document.xml"})
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_EXTERNAL_RELATION_RE = re.compile(rb"targetmode\s*=\s*['\"]external['\"]", re.IGNORECASE)
_READ_CHUNK_SIZE = 1024 * 1024
_RELATION_SCAN_OVERLAP = 256


@dataclass(frozen=True, slots=True)
class DocxSafetyLimits:
    """DOCX 容器限额；测试可注入小阈值，生产使用配置中的默认值。"""

    max_entries: int = DOCX_MAX_ENTRIES
    max_total_uncompressed_size: int = DOCX_MAX_TOTAL_UNCOMPRESSED_SIZE
    max_member_uncompressed_size: int = DOCX_MAX_MEMBER_UNCOMPRESSED_SIZE
    max_total_compression_ratio: float = DOCX_MAX_TOTAL_COMPRESSION_RATIO
    max_member_compression_ratio: float = DOCX_MAX_MEMBER_COMPRESSION_RATIO


@dataclass(frozen=True, slots=True)
class DocxInspection:
    entry_count: int
    total_uncompressed_size: int
    warnings: tuple[str, ...] = ()


def _compression_ratio(uncompressed_size: int, compressed_size: int) -> float:
    if uncompressed_size <= 0:
        return 0.0
    if compressed_size <= 0:
        return float("inf")
    return uncompressed_size / compressed_size


def _validate_member_path(info: zipfile.ZipInfo) -> None:
    """拒绝可能被不同 ZIP 实现解释为目录穿越的条目名。"""
    name = info.filename
    if not name or "\x00" in name:
        raise ValidationError("DOCX 包含空名称或空字节 ZIP 条目。")
    if "\\" in name:
        raise ValidationError(f"DOCX ZIP 条目使用了不安全的反斜杠路径：{name}")
    path = PurePosixPath(name)
    if path.is_absolute() or name.startswith("/") or _WINDOWS_DRIVE_RE.match(name):
        raise ValidationError(f"DOCX ZIP 条目使用了绝对路径：{name}")
    if ".." in path.parts:
        raise ValidationError(f"DOCX ZIP 条目包含路径穿越：{name}")

    unix_mode = (info.external_attr >> 16) & 0xFFFF
    if unix_mode and stat.S_ISLNK(unix_mode):
        raise ValidationError(f"DOCX ZIP 条目是符号链接，已拒绝处理：{name}")
    if info.flag_bits & 0x1:
        raise ValidationError(f"DOCX 包含加密 ZIP 条目，无法安全离线检查：{name}")


def _validate_declared_limits(infos: list[zipfile.ZipInfo], limits: DocxSafetyLimits) -> None:
    if len(infos) > limits.max_entries:
        raise ValidationError(
            f"DOCX ZIP 条目过多：{len(infos)}，上限为 {limits.max_entries}。"
        )

    total_uncompressed = 0
    total_compressed = 0
    seen_names: set[str] = set()
    for info in infos:
        _validate_member_path(info)
        if info.filename in seen_names:
            raise ValidationError(f"DOCX ZIP 包含重复条目：{info.filename}")
        seen_names.add(info.filename)
        if info.is_dir():
            continue

        if info.file_size > limits.max_member_uncompressed_size:
            raise ValidationError(
                f"DOCX 条目解压后过大：{info.filename}，"
                f"上限为 {limits.max_member_uncompressed_size // 1024 // 1024} MB。"
            )
        ratio = _compression_ratio(info.file_size, info.compress_size)
        if ratio > limits.max_member_compression_ratio:
            raise ValidationError(
                f"DOCX 条目压缩比异常：{info.filename}（{ratio:.1f} 倍），"
                f"上限为 {limits.max_member_compression_ratio:g} 倍。"
            )
        total_uncompressed += info.file_size
        total_compressed += info.compress_size

    if total_uncompressed > limits.max_total_uncompressed_size:
        raise ValidationError(
            "DOCX 解压后总体积过大："
            f"{total_uncompressed / 1024 / 1024:.1f} MB，"
            f"上限为 {limits.max_total_uncompressed_size // 1024 // 1024} MB。"
        )
    total_ratio = _compression_ratio(total_uncompressed, total_compressed)
    if total_ratio > limits.max_total_compression_ratio:
        raise ValidationError(
            f"DOCX 总压缩比异常：{total_ratio:.1f} 倍，"
            f"上限为 {limits.max_total_compression_ratio:g} 倍。"
        )


def inspect_docx(
    path: Path,
    limits: DocxSafetyLimits | None = None,
) -> DocxInspection:
    """只读检查 DOCX ZIP 容器；不提取文件，也不解析 Word 文档语义。"""
    limits = limits or DocxSafetyLimits()
    if not zipfile.is_zipfile(path):
        raise ValidationError("文件扩展名为 DOCX，但内容不是有效的 DOCX ZIP 容器。")

    warnings: list[str] = []
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            _validate_declared_limits(infos, limits)
            names = {info.filename for info in infos}
            missing = sorted(_REQUIRED_MEMBERS - names)
            if missing:
                raise ValidationError("DOCX 缺少必要结构文件：" + "、".join(missing))

            lower_names = {name.lower() for name in names}
            if any(name.endswith("/vbaproject.bin") or name == "vbaproject.bin" for name in lower_names):
                warnings.append("DOCX 包含宏项目；转换不会执行宏，宏内容可能丢失。")
            if any(name.startswith("word/embeddings/") for name in lower_names):
                warnings.append("DOCX 包含 OLE 嵌入对象；转换不会执行嵌入对象，相关内容可能丢失。")

            actual_total = 0
            has_external_relation = False
            for info in infos:
                if info.is_dir():
                    continue
                actual_member = 0
                relation_tail = b""
                with archive.open(info, "r") as member:
                    while True:
                        chunk = member.read(_READ_CHUNK_SIZE)
                        if not chunk:
                            break
                        actual_member += len(chunk)
                        actual_total += len(chunk)
                        if actual_member > limits.max_member_uncompressed_size:
                            raise ValidationError(
                                f"DOCX 条目实际解压体积超过限制：{info.filename}"
                            )
                        if actual_total > limits.max_total_uncompressed_size:
                            raise ValidationError("DOCX 实际解压总体积超过安全限制。")

                        if info.filename.lower().endswith(".rels") and not has_external_relation:
                            probe = relation_tail + chunk
                            if _EXTERNAL_RELATION_RE.search(probe):
                                has_external_relation = True
                            relation_tail = probe[-_RELATION_SCAN_OVERLAP:]

                if actual_member != info.file_size:
                    raise ValidationError(f"DOCX ZIP 条目大小与目录记录不一致：{info.filename}")

            if has_external_relation:
                warnings.append("DOCX 包含外部链接或资源关系；转换不会主动下载远程内容。")

            return DocxInspection(
                entry_count=len(infos),
                total_uncompressed_size=actual_total,
                warnings=tuple(dict.fromkeys(warnings)),
            )
    except ValidationError:
        raise
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise ValidationError(f"DOCX ZIP 容器损坏或使用了不支持的压缩方式：{exc}") from exc

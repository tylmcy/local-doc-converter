"""文件名、输出目录与大小限制。"""

from __future__ import annotations

import re
from pathlib import Path

from .config import MAX_BATCH_FILES, MAX_BATCH_SIZE, MAX_FILE_SIZE, SUPPORTED_EXTENSIONS, TARGET_EXTENSIONS
from .errors import ValidationError

_SAFE_NAME_RE = re.compile(r"[^\w\-. ()\u4e00-\u9fff]", re.UNICODE)


def safe_filename(filename: str) -> str:
    """拒绝路径成分，并把不适合作为本地文件名的字符替换掉。"""
    if not filename or "\x00" in filename:
        raise ValidationError("文件名为空或包含非法字符。")
    if Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise ValidationError(f"文件名包含路径成分：{filename}")
    if filename.startswith("."):
        raise ValidationError("不接受隐藏文件。")
    cleaned = _SAFE_NAME_RE.sub("_", filename).strip(" .")
    if not cleaned or cleaned.startswith("."):
        raise ValidationError("不接受空文件名或隐藏文件。")
    return cleaned


def source_format_for(path_or_name: str | Path) -> str:
    suffix = Path(path_or_name).suffix.lower()
    try:
        return SUPPORTED_EXTENSIONS[suffix]
    except KeyError as exc:
        raise ValidationError(f"不支持的输入格式：{suffix or '无扩展名'}") from exc


def validate_target(source_format: str, target_format: str) -> None:
    if target_format not in TARGET_EXTENSIONS:
        raise ValidationError(f"不支持的目标格式：{target_format}")
    if source_format == target_format:
        raise ValidationError("源格式与目标格式相同，无需转换。")


def validate_file_size(size: int) -> None:
    if size <= 0:
        raise ValidationError("文件内容为空。")
    if size > MAX_FILE_SIZE:
        raise ValidationError(f"单个文件不能超过 {MAX_FILE_SIZE // 1024 // 1024} MB。")


def validate_batch(sizes: list[int]) -> None:
    if not sizes:
        raise ValidationError("请至少选择一个文件。")
    if len(sizes) > MAX_BATCH_FILES:
        raise ValidationError(f"一次最多处理 {MAX_BATCH_FILES} 个文件。")
    for size in sizes:
        validate_file_size(size)
    if sum(sizes) > MAX_BATCH_SIZE:
        raise ValidationError(f"批次总大小不能超过 {MAX_BATCH_SIZE // 1024 // 1024} MB。")


def ensure_output_dir(raw_path: str | Path) -> Path:
    """本地应用仅允许写入用户主目录，避免误写系统目录。"""
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    resolved = path.resolve(strict=False)
    home = Path.home().resolve()
    if resolved == home or home not in resolved.parents:
        raise ValidationError("输出目录必须位于当前用户主目录的子目录中。")
    resolved.mkdir(parents=True, exist_ok=True)
    if not resolved.is_dir():
        raise ValidationError("输出路径不是目录。")
    return resolved


def unique_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    index = 2
    while True:
        candidate = directory / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1

"""与本地桌面系统交互的少量跨平台辅助函数。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .errors import ConverterError


def open_directory(path: Path) -> None:
    """使用当前系统的文件管理器打开一个已经验证过的目录。"""
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ConverterError("输出目录不存在。")

    try:
        if sys.platform == "darwin":
            subprocess.Popen(
                ["open", str(resolved)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif sys.platform == "win32":
            os.startfile(resolved)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(
                ["xdg-open", str(resolved)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except OSError as exc:
        raise ConverterError(f"无法打开输出目录：{exc}") from exc

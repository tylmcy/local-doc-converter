"""对 Pandoc 输出做不改变语义的最小规范化。"""

from __future__ import annotations

import re
from pathlib import Path

_TABLE_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")


def normalize_markdown(path: Path) -> None:
    """缩短 GFM 表格分隔线，避免出现大量无意义的连续连字符。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    normalized: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and all(_TABLE_SEPARATOR_CELL.fullmatch(cell) for cell in cells):
                compact_cells: list[str] = []
                for cell in cells:
                    left, right = cell.startswith(":"), cell.endswith(":")
                    compact_cells.append(f"{':' if left else ''}---{':' if right else ''}")
                line = "| " + " | ".join(compact_cells) + " |"
        normalized.append(line.rstrip())
    path.write_text("\n".join(normalized).rstrip() + "\n", encoding="utf-8")

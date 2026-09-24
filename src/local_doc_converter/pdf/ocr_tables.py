"""对有清晰网格线的扫描表格做保守的 TXT 行列恢复。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True, slots=True)
class RuledTableResult:
    text: str
    row_count: int
    column_count: int


def _line_centers(values: np.ndarray, minimum_gap: int) -> list[int]:
    """把抗锯齿造成的连续像素行合并为一条网格线。"""
    if not len(values):
        return []
    groups: list[list[int]] = [[int(values[0])]]
    for value in values[1:]:
        value = int(value)
        if value - groups[-1][-1] <= minimum_gap:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [round(sum(group) / len(group)) for group in groups]


def reconstruct_ruled_table(image: Any, raw_results: Any) -> RuledTableResult | None:
    """网格线、OCR 多行多列同时成立时才恢复；不猜测无框表格。"""
    gray = np.asarray(image.convert("L"))
    height, width = gray.shape
    dark = gray < 110
    horizontal = _line_centers(np.flatnonzero(dark.sum(axis=1) >= width * 0.42), 3)
    if len(horizontal) < 4:
        return None
    top, bottom = horizontal[0], horizontal[-1]
    if bottom - top < height * 0.08:
        return None
    vertical = _line_centers(
        np.flatnonzero(dark[top : bottom + 1].sum(axis=0) >= (bottom - top) * 0.65),
        3,
    )
    if len(vertical) < 4:
        return None
    left, right = vertical[0], vertical[-1]
    if right - left < width * 0.35:
        return None
    # 要求真实网格线在交点附近存在，避免把正文横线和竖线误组合为表格。
    for y in horizontal:
        if sum(
            bool(dark[max(0, y - 2) : min(height, y + 3), max(0, x - 2) : min(width, x + 3)].any())
            for x in vertical
        ) < len(vertical) * 0.7:
            return None

    records: list[tuple[float, float, str]] = []
    for item in list(raw_results or []):
        payload = getattr(item, "json", item)
        if callable(payload):
            payload = payload()
        if not isinstance(payload, dict):
            continue
        payload = payload.get("res", payload)
        texts = payload.get("rec_texts") or []
        polygons = payload.get("rec_polys") or []
        for index, text in enumerate(texts):
            if not str(text).strip() or index >= len(polygons):
                continue
            try:
                xs = [float(point[0]) for point in polygons[index]]
                ys = [float(point[1]) for point in polygons[index]]
            except (TypeError, ValueError, IndexError):
                continue
            records.append(((min(ys) + max(ys)) / 2, (min(xs) + max(xs)) / 2, str(text).strip()))

    rows = len(horizontal) - 1
    columns = len(vertical) - 1
    if rows > 100 or columns > 20:
        return None
    cells: list[list[list[tuple[float, float, str]]]] = [
        [[] for _ in range(columns)] for _ in range(rows)
    ]
    outside: list[tuple[float, float, str]] = []
    for record in records:
        y, x, _ = record
        row = next((i for i in range(rows) if horizontal[i] < y < horizontal[i + 1]), None)
        col = next((i for i in range(columns) if vertical[i] < x < vertical[i + 1]), None)
        if row is None or col is None:
            outside.append(record)
        else:
            cells[row][col].append(record)
    populated_rows = sum(sum(bool(cell) for cell in row) >= 2 for row in cells)
    if populated_rows < 3 or sum(bool(cell) for row in cells for cell in row) < 9:
        return None

    table_lines = []
    for row in cells:
        fields = [
            " ".join(record[2] for record in sorted(cell))
            for cell in row
        ]
        table_lines.append("\t".join(fields))
    # 按垂直位置插入表格，其余标题、正文仍保留原 OCR 块。
    outside.append((float(top), float(left), "\n".join(table_lines)))
    text = "\n".join(record[2] for record in sorted(outside))
    return RuledTableResult(text, rows, columns)

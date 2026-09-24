"""PDF 文本型页面的阅读顺序与表格结构恢复。

这一层只使用 PDF 文字/线条的坐标，不引入网络服务或模型。规则刻意保守：
只在中缝和左右栏证据都足够时重排为双栏，无框表格也必须通过额外校验。
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .extract import normalize_extracted_text

_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_NUMBER_RE = re.compile(r"[-+]?(?:\d[\d,]*(?:\.\d+)?|\d+%)")


@dataclass(frozen=True, slots=True)
class LayoutBlock:
    """可参与阅读顺序排序的文本块。"""

    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    kind: str = "text"
    table_strategy: str | None = None

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)


@dataclass(frozen=True, slots=True)
class PageLayoutResult:
    text: str
    column_count: int
    structured_table_count: int
    complex_table_count: int
    table_fallback_count: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _TableCandidate:
    block: LayoutBlock
    rows: tuple[tuple[str, ...], ...]
    complex_structure: bool


def _finite_number(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if math.isfinite(result) else default


def _bbox_overlap_ratio(first: LayoutBlock, second: LayoutBlock) -> float:
    width = max(0.0, min(first.x1, second.x1) - max(first.x0, second.x0))
    height = max(0.0, min(first.bottom, second.bottom) - max(first.top, second.top))
    intersection = width * height
    smaller = min(first.width * max(0.0, first.bottom - first.top), second.width * max(0.0, second.bottom - second.top))
    return intersection / smaller if smaller > 0 else 0.0


def _normalize_cell(value: object) -> str:
    if value is None:
        return ""
    # 单元格内换行在 TXT 中收敛为空格，避免破坏一行一条记录的结构。
    return " ".join(normalize_extracted_text(str(value)).splitlines()).strip()


def _normalize_table_rows(raw_rows: Sequence[Sequence[object]]) -> tuple[tuple[str, ...], ...]:
    if not raw_rows:
        return ()
    width = max((len(row) for row in raw_rows), default=0)
    rows = [tuple(_normalize_cell(cell) for cell in row) + ("",) * (width - len(row)) for row in raw_rows]
    rows = [row for row in rows if any(row)]
    if not rows:
        return ()

    # 删除整列为空的伪单元格，但保留合并单元格在局部产生的空占位。
    useful_columns = [index for index in range(width) if any(row[index] for row in rows)]
    return tuple(tuple(row[index] for index in useful_columns) for row in rows)


def _looks_like_table(
    rows: tuple[tuple[str, ...], ...],
    *,
    strategy: str,
    bbox: tuple[float, float, float, float],
    page_width: float,
    page_height: float,
    visual_column_count: int | None = None,
) -> bool:
    if len(rows) < 2 or max((len(row) for row in rows), default=0) < 2:
        return False
    nonempty_counts = [sum(bool(cell) for cell in row) for row in rows]
    if sum(count >= 2 for count in nonempty_counts) < 2:
        return False
    cells = [cell for row in rows for cell in row if cell]
    if len(cells) < 4:
        return False
    if strategy == "lines":
        return True

    # 无框表格只接受至少三个稳定视觉列。两列文本与双栏正文过于相似，
    # 强行恢复的误报代价高，因此留给普通阅读顺序处理。
    if visual_column_count is None or visual_column_count < 3:
        return False

    column_count = max(len(row) for row in rows)
    occupancy = len(cells) / (len(rows) * column_count)
    median_length = statistics.median(len(cell) for cell in cells)
    numeric_ratio = sum(bool(_NUMBER_RE.fullmatch(cell.replace(" ", ""))) for cell in cells) / len(cells)
    width_ratio = max(0.0, bbox[2] - bbox[0]) / max(page_width, 1.0)
    height_ratio = max(0.0, bbox[3] - bbox[1]) / max(page_height, 1.0)

    # 三栏正文在文字对齐策略下可能被误识为一张贯穿整页的“表格”，
    # 这种误判会切碎词语并破坏全部阅读顺序，宁可放弃过长的无框表格。
    if height_ratio > 0.75:
        return False

    # 最容易误报的是“整页双栏正文”：两列、高且宽、单元格是长段落。
    if column_count == 2 and width_ratio > 0.65 and height_ratio > 0.35 and len(rows) >= 5:
        if numeric_ratio < 0.2:
            return False
    return occupancy >= 0.5 and median_length <= 80 and (column_count >= 3 or numeric_ratio >= 0.1 or median_length <= 32)


def _visual_column_count(page: Any, bbox: tuple[float, float, float, float]) -> int:
    words = [
        word
        for word in page.extract_words(x_tolerance=2, y_tolerance=3, keep_blank_chars=False)
        if bbox[0] <= (_finite_number(word.get("x0")) + _finite_number(word.get("x1"))) / 2 <= bbox[2]
        and bbox[1] <= (_finite_number(word.get("top")) + _finite_number(word.get("bottom"))) / 2 <= bbox[3]
    ]
    if not words:
        return 0
    lines: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: (_finite_number(item.get("top")), _finite_number(item.get("x0")))):
        center = (_finite_number(word.get("top")) + _finite_number(word.get("bottom"))) / 2
        if not lines:
            lines.append([word])
            continue
        previous_center = statistics.mean(
            (_finite_number(item.get("top")) + _finite_number(item.get("bottom"))) / 2 for item in lines[-1]
        )
        if abs(center - previous_center) <= 3:
            lines[-1].append(word)
        else:
            lines.append([word])
    counts: list[int] = []
    for line in lines:
        ordered = sorted(line, key=lambda item: _finite_number(item.get("x0")))
        groups = 1
        for previous, current in zip(ordered, ordered[1:]):
            if _finite_number(current.get("x0")) - _finite_number(previous.get("x1")) >= 18:
                groups += 1
        counts.append(groups)
    # 至少两行都呈现相同列数，才认为是稳定的无框表格对齐。
    return max((count for count in set(counts) if counts.count(count) >= 2), default=0)


def _table_candidate(
    table: Any,
    *,
    page: Any,
    strategy: str,
    page_width: float,
    page_height: float,
) -> _TableCandidate | None:
    bbox = tuple(_finite_number(value) for value in table.bbox)
    if len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    raw_rows = table.extract(x_tolerance=2, y_tolerance=3)
    rows = _normalize_table_rows(raw_rows)
    if not _looks_like_table(
        rows,
        strategy=strategy,
        bbox=bbox,
        page_width=page_width,
        page_height=page_height,
        visual_column_count=_visual_column_count(page, bbox) if strategy == "text" else None,
    ):
        return None

    raw_widths = {len(row) for row in raw_rows}
    complex_structure = (
        len(raw_widths) > 1
        or any(cell is None for row in raw_rows for cell in row)
        or any("\n" in str(cell) for row in raw_rows for cell in row if cell is not None)
    )
    # 保留行末空单元格的 Tab，这些占位是合并表头等结构的唯一纯文本线索。
    text = "\n".join("\t".join(row) for row in rows).strip("\n")
    return _TableCandidate(
        block=LayoutBlock(text, bbox[0], bbox[1], bbox[2], bbox[3], "table", strategy),
        rows=rows,
        complex_structure=complex_structure,
    )


def _extract_tables(page: Any) -> tuple[list[_TableCandidate], int, list[str]]:
    page_width = _finite_number(page.width, 1.0)
    page_height = _finite_number(page.height, 1.0)
    accepted: list[_TableCandidate] = []
    fallback_count = 0
    warnings: list[str] = []
    settings = (
        (
            "lines",
            {
                "vertical_strategy": "lines",
                "horizontal_strategy": "lines",
                "snap_tolerance": 3,
                "join_tolerance": 3,
                "intersection_tolerance": 4,
            },
        ),
        (
            "text",
            {
                "vertical_strategy": "text",
                "horizontal_strategy": "text",
                "min_words_vertical": 2,
                "min_words_horizontal": 2,
                "text_x_tolerance": 2,
                "text_y_tolerance": 3,
                "intersection_tolerance": 5,
            },
        ),
    )
    for strategy, table_settings in settings:
        try:
            found = page.find_tables(table_settings)
        except Exception as exc:
            if strategy == "lines":
                fallback_count += 1
                warnings.append(f"线框表格检测失败：{type(exc).__name__}。")
            continue
        for table in found:
            try:
                candidate = _table_candidate(
                    table,
                    page=page,
                    strategy=strategy,
                    page_width=page_width,
                    page_height=page_height,
                )
            except Exception as exc:
                fallback_count += 1
                warnings.append(f"表格单元格恢复失败：{type(exc).__name__}，已保留普通文本。")
                continue
            if candidate is None:
                continue
            if any(_bbox_overlap_ratio(candidate.block, existing.block) >= 0.7 for existing in accepted):
                continue
            accepted.append(candidate)
    accepted.sort(key=lambda item: (item.block.top, item.block.x0))
    return accepted, fallback_count, warnings


def _inside_table(word: dict[str, Any], tables: Sequence[_TableCandidate]) -> bool:
    center_x = (_finite_number(word.get("x0")) + _finite_number(word.get("x1"))) / 2
    center_y = (_finite_number(word.get("top")) + _finite_number(word.get("bottom"))) / 2
    return any(
        table.block.x0 - 1 <= center_x <= table.block.x1 + 1
        and table.block.top - 1 <= center_y <= table.block.bottom + 1
        for table in tables
    )


def _join_words(words: Sequence[dict[str, Any]]) -> str:
    if not words:
        return ""
    ordered = sorted(words, key=lambda word: _finite_number(word.get("x0")))
    parts = [str(ordered[0].get("text", "")).strip()]
    previous = ordered[0]
    for word in ordered[1:]:
        text = str(word.get("text", "")).strip()
        if not text:
            continue
        previous_text = parts[-1]
        gap = _finite_number(word.get("x0")) - _finite_number(previous.get("x1"))
        height = max(1.0, _finite_number(word.get("bottom")) - _finite_number(word.get("top")))
        both_cjk = bool(_CJK_RE.search(previous_text[-1:]) and _CJK_RE.search(text[:1]))
        separator = "" if both_cjk and gap <= height * 0.55 else " "
        parts.append(separator + text)
        previous = word
    return "".join(parts).strip()


def _line_fragments(words: Sequence[dict[str, Any]], *, page_width: float) -> list[LayoutBlock]:
    ordered = sorted(words, key=lambda word: (_finite_number(word.get("top")), _finite_number(word.get("x0"))))
    lines: list[list[dict[str, Any]]] = []
    for word in ordered:
        center = (_finite_number(word.get("top")) + _finite_number(word.get("bottom"))) / 2
        if not lines:
            lines.append([word])
            continue
        last = lines[-1]
        last_center = statistics.mean(
            (_finite_number(item.get("top")) + _finite_number(item.get("bottom"))) / 2 for item in last
        )
        tolerance = max(2.5, (_finite_number(word.get("bottom")) - _finite_number(word.get("top"))) * 0.35)
        if abs(center - last_center) <= tolerance:
            last.append(word)
        else:
            lines.append([word])

    gap_threshold = max(18.0, page_width * 0.028)
    fragments: list[LayoutBlock] = []
    for line in lines:
        line = sorted(line, key=lambda word: _finite_number(word.get("x0")))
        groups: list[list[dict[str, Any]]] = [[]]
        for word in line:
            if groups[-1]:
                gap = _finite_number(word.get("x0")) - _finite_number(groups[-1][-1].get("x1"))
                if gap >= gap_threshold:
                    groups.append([])
            groups[-1].append(word)
        for group in groups:
            text = _join_words(group)
            if not text:
                continue
            fragments.append(
                LayoutBlock(
                    text=text,
                    x0=min(_finite_number(word.get("x0")) for word in group),
                    top=min(_finite_number(word.get("top")) for word in group),
                    x1=max(_finite_number(word.get("x1")) for word in group),
                    bottom=max(_finite_number(word.get("bottom")) for word in group),
                )
            )
    return fragments


def _column_split(blocks: Sequence[LayoutBlock], *, page_width: float) -> float | None:
    text_blocks = [block for block in blocks if block.kind == "text" and len(block.text) >= 2]
    if len(text_blocks) < 6:
        return None
    gutter_half = max(5.0, page_width * 0.012)
    best: tuple[float, float] | None = None
    for step in range(31):
        split = page_width * (0.35 + step * 0.01)
        left = [block for block in text_blocks if block.x1 <= split - gutter_half]
        right = [block for block in text_blocks if block.x0 >= split + gutter_half]
        crossing = [block for block in text_blocks if block not in left and block not in right]
        if len(left) < 2 or len(right) < 2:
            continue
        left_weight = sum(len(block.text) for block in left)
        right_weight = sum(len(block.text) for block in right)
        total_weight = left_weight + right_weight + sum(len(block.text) for block in crossing)
        if min(left_weight, right_weight) / max(left_weight, right_weight) < 0.2:
            continue
        separated_ratio = (left_weight + right_weight) / max(total_weight, 1)
        if separated_ratio < 0.68:
            continue
        # 用中位边界抑制单个跨栏标题/图注对中缝的干扰。
        left_edge = statistics.median(block.x1 for block in left)
        right_edge = statistics.median(block.x0 for block in right)
        gutter = right_edge - left_edge
        if gutter < max(12.0, page_width * 0.02):
            continue
        score = separated_ratio + min(gutter / page_width, 0.12) - len(crossing) * 0.005
        if best is None or score > best[0]:
            # 返回真实文本边界之间的中点，而不是扫描时命中的第一个空白坐标。
            # 否则较宽中缝会把页面居中标题错归到右栏。
            best = (score, (left_edge + right_edge) / 2)
    return best[1] if best else None


def _three_column_splits(
    words: Sequence[dict[str, Any]], *, page_width: float, page_height: float,
    short_band: bool = False,
) -> tuple[float, float] | None:
    """用持续的两条空白走廊识别三栏，避免把短表格误当成正文。"""
    body = [
        word for word in words
        if page_height * (0.06 if short_band else 0.08)
        <= _finite_number(word.get("top"))
        <= page_height * (0.12 if short_band else 0.9)
        and 0 <= _finite_number(word.get("x0")) < _finite_number(word.get("x1")) <= page_width
    ]
    if len(body) < (35 if short_band else 70):
        return (
            _three_column_splits(words, page_width=page_width, page_height=page_height, short_band=True)
            if not short_band and len(words) < 160 else None
        )
    width = math.ceil(page_width)
    coverage = [0] * (width + 1)
    for word in body:
        start = max(0, int(_finite_number(word.get("x0"))))
        end = min(width, math.ceil(_finite_number(word.get("x1"))))
        for position in range(start, end):
            coverage[position] += 1

    # 中间 60% 页面宽度内寻找至少 6pt 宽、近乎无字的走廊。
    corridors: list[tuple[int, int]] = []
    start: int | None = None
    for position in range(int(page_width * 0.2), int(page_width * 0.8) + 1):
        if coverage[position] <= (0 if short_band else 2):
            if start is None:
                start = position
        elif start is not None:
            if position - start >= 6:
                corridors.append((start, position))
            start = None
    if start is not None and int(page_width * 0.8) + 1 - start >= 6:
        corridors.append((start, int(page_width * 0.8) + 1))
    if len(corridors) < 2:
        return None

    centers = [(left + right) / 2 for left, right in corridors if right - left <= page_width * 0.065]
    for first in centers:
        for second in centers:
            if second - first < page_width * 0.21:
                continue
            if min(first, second - first, page_width - second) < page_width * 0.19:
                continue
            lanes = [[], [], []]
            for word in body:
                center = (_finite_number(word.get("x0")) + _finite_number(word.get("x1"))) / 2
                lanes[0 if center < first else 1 if center < second else 2].append(word)
            if all(
                len(lane) >= (8 if short_band else 15)
                and max(_finite_number(word.get("top")) for word in lane)
                - min(_finite_number(word.get("top")) for word in lane)
                >= page_height * (0.018 if short_band else 0.1)
                for lane in lanes
            ):
                return first, second
    return (
        _three_column_splits(words, page_width=page_width, page_height=page_height, short_band=True)
        if not short_band and len(words) < 160 else None
    )


def _three_column_body_top(
    words: Sequence[dict[str, Any]], splits: tuple[float, float], *, page_height: float
) -> float:
    """找三栏同时开始密集正文的位置，把跨栏页眉留在正文之前。"""
    for top in range(0, int(page_height * 0.55), 8):
        counts = [0, 0, 0]
        for word in words:
            y = _finite_number(word.get("top"))
            if top <= y < top + 22:
                x = (_finite_number(word.get("x0")) + _finite_number(word.get("x1"))) / 2
                counts[0 if x < splits[0] else 1 if x < splits[1] else 2] += 1
        if min(counts) >= 3:
            return float(top)
    return page_height * 0.12


def _three_column_order(
    words: Sequence[dict[str, Any]], tables: Sequence[_TableCandidate],
    *, splits: tuple[float, float], page_width: float, page_height: float,
) -> list[LayoutBlock]:
    if not tables and len(words) < 160 and sum(
        page_height * 0.15 < _finite_number(word.get("top")) < page_height * 0.9
        for word in words
    ) < 50:
        # 短页可能只有顶部三栏摘要，下面是跨栏图片/稀疏说明；
        # 不能把下半页继续强制按三栏分组。
        header_cut = page_height * 0.06
        short_end = page_height * 0.12
        header = _line_fragments(
            [word for word in words if _finite_number(word.get("top")) < header_cut],
            page_width=page_width,
        )
        top_lanes: list[list[dict[str, Any]]] = [[], [], []]
        for word in words:
            y = _finite_number(word.get("top"))
            if header_cut <= y <= short_end:
                x = (_finite_number(word.get("x0")) + _finite_number(word.get("x1"))) / 2
                top_lanes[0 if x < splits[0] else 1 if x < splits[1] else 2].append(word)
        tail = _line_fragments(
            [word for word in words if short_end < _finite_number(word.get("top")) < page_height * 0.94],
            page_width=page_width,
        )
        tail_left = [block for block in tail if block.x1 < splits[0]]
        tail_middle = [block for block in tail if splits[0] <= block.x0 and block.x1 < splits[1]]
        tail_other = [block for block in tail if block not in tail_left and block not in tail_middle]
        tail_ordered = (
            [*_sort_blocks(tail_left), *_sort_blocks(tail_middle)]
            if len(tail_left) >= 2 and len(tail_middle) >= 2 and not tail_other
            else _sort_blocks(tail)
        )
        footer = _line_fragments(
            [word for word in words if _finite_number(word.get("top")) >= page_height * 0.94],
            page_width=page_width,
        )
        return [
            *_sort_blocks(header),
            *(block for lane in top_lanes for block in _sort_blocks(_line_fragments(lane, page_width=page_width))),
            *tail_ordered,
            *_sort_blocks([*footer, *(table.block for table in tables)]),
        ]
    body_top = _three_column_body_top(words, splits, page_height=page_height)
    leading_tables = [table for table in tables if table.block.top < page_height * 0.25]
    if leading_tables:
        body_top = max(body_top, max(table.block.bottom for table in leading_tables) + 4)
    body_bottom = page_height * 0.94
    header_words = [word for word in words if _finite_number(word.get("top")) < body_top]
    footer_words = [word for word in words if _finite_number(word.get("top")) >= body_bottom]
    lanes: list[list[LayoutBlock]] = [[], [], []]
    for lane_index, lower, upper in (
        (0, -math.inf, splits[0]), (1, splits[0], splits[1]), (2, splits[1], math.inf)
    ):
        lane_words = [
            word for word in words
            if body_top <= _finite_number(word.get("top")) < body_bottom
            and lower <= (_finite_number(word.get("x0")) + _finite_number(word.get("x1"))) / 2 < upper
        ]
        lanes[lane_index].extend(_line_fragments(lane_words, page_width=page_width))

    span_tables: list[LayoutBlock] = []
    for table in tables:
        block = table.block
        if block.top < body_top:
            continue
        lane_index = next(
            (index for index, (lower, upper) in enumerate(
                ((-math.inf, splits[0]), (splits[0], splits[1]), (splits[1], math.inf))
            ) if lower <= block.x0 and block.x1 <= upper), None
        )
        if lane_index is None:
            span_tables.append(block)
        else:
            lanes[lane_index].append(block)

    header = _sort_blocks([
        *_line_fragments(header_words, page_width=page_width),
        *(table.block for table in tables if table.block.top < body_top),
    ])
    ordered = list(header)
    lower_bound = body_top
    for span in _sort_blocks(span_tables):
        for lane in lanes:
            ordered.extend(_sort_blocks(block for block in lane if lower_bound <= block.top < span.top))
        ordered.append(span)
        lower_bound = span.bottom
    for lane in lanes:
        ordered.extend(_sort_blocks(block for block in lane if block.top >= lower_bound))
    ordered.extend(_sort_blocks(_line_fragments(footer_words, page_width=page_width)))
    return ordered


def _side(block: LayoutBlock, split: float, page_width: float) -> str:
    margin = max(4.0, page_width * 0.008)
    if block.x1 <= split - margin:
        return "left"
    if block.x0 >= split + margin:
        return "right"
    return "span"


def _sort_blocks(blocks: Iterable[LayoutBlock]) -> list[LayoutBlock]:
    return sorted(blocks, key=lambda block: (round(block.top, 1), block.x0, block.bottom))


def _column_order(blocks: Sequence[LayoutBlock], *, split: float, page_width: float) -> list[LayoutBlock]:
    remaining = list(blocks)
    spans = _sort_blocks(block for block in remaining if _side(block, split, page_width) == "span")
    ordered: list[LayoutBlock] = []
    lower_bound = -math.inf
    for span in spans:
        segment = [
            block
            for block in remaining
            if block is not span
            and _side(block, split, page_width) != "span"
            and block.top >= lower_bound
            and block.top < span.top
        ]
        ordered.extend(_sort_blocks(block for block in segment if _side(block, split, page_width) == "left"))
        ordered.extend(_sort_blocks(block for block in segment if _side(block, split, page_width) == "right"))
        ordered.append(span)
        for block in segment:
            remaining.remove(block)
        remaining.remove(span)
        lower_bound = span.bottom

    ordered.extend(_sort_blocks(block for block in remaining if _side(block, split, page_width) == "left"))
    ordered.extend(_sort_blocks(block for block in remaining if _side(block, split, page_width) == "right"))
    ordered.extend(_sort_blocks(block for block in remaining if _side(block, split, page_width) == "span"))
    return ordered


def _render_blocks(blocks: Sequence[LayoutBlock]) -> str:
    pieces: list[str] = []
    previous_kind: str | None = None
    for block in blocks:
        text = block.text.strip()
        if not text:
            continue
        if pieces and (block.kind == "table" or previous_kind == "table"):
            pieces.append("")
        pieces.extend(text.splitlines())
        previous_kind = block.kind
    # 表格使用 Tab 表示列，不能再调用会合并横向空白的通用清理函数。
    text = "\n".join(pieces).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def extract_page_layout(page: Any) -> PageLayoutResult:
    """提取一页的表格和阅读顺序，输出适合 TXT 的结构化文本。"""
    page_width = _finite_number(page.width, 1.0)
    tables, fallback_count, warnings = _extract_tables(page)
    try:
        words = page.extract_words(
            x_tolerance=2,
            y_tolerance=3,
            keep_blank_chars=False,
            use_text_flow=False,
        )
    except Exception as exc:
        warnings.append(f"词级坐标提取失败：{type(exc).__name__}，已回退到普通文本顺序。")
        raw_text = page.extract_text(x_tolerance=2, y_tolerance=3, layout=False, keep_blank_chars=False)
        return PageLayoutResult(
            text=normalize_extracted_text(raw_text),
            column_count=1,
            structured_table_count=0,
            complex_table_count=0,
            table_fallback_count=fallback_count + 1,
            warnings=tuple(warnings),
        )

    upright_count = sum(word.get("upright", True) for word in words)
    outside_words = [
        word for word in words
        if not _inside_table(word, tables)
        and not (
            upright_count >= 50
            and not word.get("upright", True)
            and (
                _finite_number(word.get("x1")) < page_width * 0.07
                or _finite_number(word.get("x0")) > page_width * 0.93
            )
        )
    ]
    three_splits = _three_column_splits(
        outside_words, page_width=page_width, page_height=_finite_number(page.height, 1.0)
    )
    if three_splits is not None:
        ordered = _three_column_order(
            outside_words, tables, splits=three_splits,
            page_width=page_width, page_height=_finite_number(page.height, 1.0),
        )
        column_count = 3
        warnings.append("检测到稳定三栏，已按左、中、右栏恢复阅读顺序。")
    else:
        blocks = _line_fragments(outside_words, page_width=page_width)
        blocks.extend(table.block for table in tables)
        split = _column_split(blocks, page_width=page_width)
        if split is None:
            ordered = _sort_blocks(blocks)
            column_count = 1
        else:
            ordered = _column_order(blocks, split=split, page_width=page_width)
            column_count = 2
            warnings.append("检测到稳定双栏，已按左栏后右栏恢复阅读顺序。")

    if any(table.block.table_strategy == "text" for table in tables):
        warnings.append("已通过文字对齐恢复无框表格，制表符列边界建议人工复核。")
    complex_count = sum(table.complex_structure for table in tables)
    if complex_count:
        warnings.append("表格包含合并或多行单元格，已用空占位和单行文本降级表达。")

    return PageLayoutResult(
        text=_render_blocks(ordered),
        column_count=column_count,
        structured_table_count=len(tables),
        complex_table_count=complex_count,
        table_fallback_count=fallback_count,
        warnings=tuple(dict.fromkeys(warnings)),
    )

"""把缺少显式标记的 TXT 整理为结构化 Markdown。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_CHAPTER_RE = re.compile(r"^第[零〇一二三四五六七八九十百千万两\d]+([章节篇部卷])(?:[：:、.．\s]+)?(.+)?$")
_CHINESE_ENUM_RE = re.compile(r"^([一二三四五六七八九十百千万]+)、\s*(.+)$")
_PAREN_ENUM_RE = re.compile(r"^[（(]([一二三四五六七八九十百千万\d]+)[）)]\s*(.+)$")
_DECIMAL_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)+)[.、]?\s+(.+)$")
_NUMBERED_RE = re.compile(r"^(\d+)[.、]\s*(.+)$")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.+)$")
_PIPE_SEPARATOR_RE = re.compile(r"^:?-{3,}:?$")
_SENTENCE_END = tuple("。！？；.!?;")


@dataclass(slots=True)
class TextParseResult:
    markdown: str
    warnings: list[str] = field(default_factory=list)


def _pipe_cells(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")]


def _is_pipe_table(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines) or "|" not in lines[index] or "|" not in lines[index + 1]:
        return False
    separator = _pipe_cells(lines[index + 1])
    return bool(separator) and all(_PIPE_SEPARATOR_RE.match(cell) for cell in separator)


def _tab_table_end(lines: list[str], index: int) -> int:
    rows: list[list[str]] = []
    cursor = index
    while cursor < len(lines) and "\t" in lines[cursor] and lines[cursor].strip():
        rows.append(lines[cursor].split("\t"))
        cursor += 1
    if len(rows) >= 2 and len(rows[0]) >= 2 and all(len(row) == len(rows[0]) for row in rows):
        return cursor
    return index


def _escape_table_cell(value: str) -> str:
    return value.strip().replace("|", "\\|")


def _is_same_chinese_sequence(line: str) -> bool:
    return bool(_CHINESE_ENUM_RE.match(line.strip()))


def _is_short_title(line: str, previous_blank: bool, next_blank: bool) -> bool:
    value = line.strip()
    if not (previous_blank and next_blank) or not 2 <= len(value) <= 24:
        return False
    if value.endswith(_SENTENCE_END) or value.startswith(("http://", "https://")):
        return False
    if _BULLET_RE.match(value) or _NUMBERED_RE.match(value):
        return False
    # 至少包含中文、英文或数字，避免把纯标点当成标题。
    return bool(re.search(r"[\w\u4e00-\u9fff]", value))


def _join_paragraph(lines: list[str]) -> str:
    result = ""
    for value in (line.strip() for line in lines):
        if not result:
            result = value
        elif re.search(r"[\u4e00-\u9fff]$", result) and re.match(r"^[\u4e00-\u9fff]", value):
            result += value
        else:
            result += " " + value
    return result


def parse_txt_structure(text: str) -> TextParseResult:
    """使用保守的离线规则识别中文 TXT 结构，并输出规范化 Markdown。"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list[str] = []
    paragraph: list[str] = []
    warnings: list[str] = []
    inferred_short_titles = 0

    def flush_paragraph() -> None:
        if paragraph:
            output.extend([_join_paragraph(paragraph), ""])
            paragraph.clear()

    index = 0
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        previous_blank = index == 0 or not lines[index - 1].strip()
        next_blank = index == len(lines) - 1 or not lines[index + 1].strip()

        if not stripped:
            flush_paragraph()
            index += 1
            continue

        # 已有围栏代码块直接保留，避免再次解释其中内容。
        if stripped.startswith("```"):
            flush_paragraph()
            fence = stripped
            output.append(fence)
            index += 1
            while index < len(lines):
                output.append(lines[index])
                if lines[index].strip().startswith("```"):
                    index += 1
                    break
                index += 1
            output.append("")
            continue

        if _is_pipe_table(lines, index):
            flush_paragraph()
            while index < len(lines) and lines[index].strip() and "|" in lines[index]:
                output.append(lines[index].strip())
                index += 1
            output.append("")
            continue

        table_end = _tab_table_end(lines, index)
        if table_end > index:
            flush_paragraph()
            rows = [[_escape_table_cell(cell) for cell in line.split("\t")] for line in lines[index:table_end]]
            output.append("| " + " | ".join(rows[0]) + " |")
            output.append("| " + " | ".join("---" for _ in rows[0]) + " |")
            output.extend("| " + " | ".join(row) + " |" for row in rows[1:])
            output.append("")
            index = table_end
            continue

        if raw.startswith(("    ", "\t")):
            flush_paragraph()
            output.append("```")
            while index < len(lines) and (lines[index].startswith(("    ", "\t")) or not lines[index].strip()):
                output.append(lines[index][1:] if lines[index].startswith("\t") else lines[index][4:])
                index += 1
            output.extend(["```", ""])
            continue

        chapter = _CHAPTER_RE.match(stripped)
        if chapter:
            flush_paragraph()
            level = 2 if chapter.group(1) == "节" else 1
            output.extend(["#" * level + " " + stripped, ""])
            index += 1
            continue

        decimal = _DECIMAL_HEADING_RE.match(stripped)
        if decimal:
            flush_paragraph()
            level = min(decimal.group(1).count(".") + 2, 6)
            output.extend(["#" * level + " " + stripped, ""])
            index += 1
            continue

        chinese = _CHINESE_ENUM_RE.match(stripped)
        chinese_sequence = (
            (index > 0 and _is_same_chinese_sequence(lines[index - 1]))
            or (index + 1 < len(lines) and _is_same_chinese_sequence(lines[index + 1]))
        )
        if chinese and not chinese_sequence:
            flush_paragraph()
            output.extend(["## " + stripped, ""])
            index += 1
            continue

        paren = _PAREN_ENUM_RE.match(stripped)
        if paren:
            flush_paragraph()
            output.extend(["### " + stripped, ""])
            index += 1
            continue

        numbered = _NUMBERED_RE.match(stripped)
        if numbered and previous_blank and next_blank and len(stripped) <= 30 and not stripped.endswith(_SENTENCE_END):
            flush_paragraph()
            output.extend(["## " + stripped, ""])
            index += 1
            continue

        if _BULLET_RE.match(stripped) or numbered or chinese_sequence:
            flush_paragraph()
            if chinese_sequence and chinese:
                output.append(f"1. {chinese.group(2)}")
            else:
                output.append(stripped)
            index += 1
            if index >= len(lines) or not lines[index].strip():
                output.append("")
            continue

        if _is_short_title(stripped, previous_blank, next_blank):
            flush_paragraph()
            output.extend(["## " + stripped, ""])
            inferred_short_titles += 1
            index += 1
            continue

        paragraph.append(raw)
        index += 1

    flush_paragraph()
    if inferred_short_titles:
        warnings.append(f"通过短行规则推断了 {inferred_short_titles} 个标题，请核对标题层级。")
    markdown = "\n".join(output).strip() + "\n"
    return TextParseResult(markdown=markdown, warnings=warnings)

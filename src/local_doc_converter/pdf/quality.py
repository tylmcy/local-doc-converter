"""PDF 原生文字层质量判定。"""

from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NativeTextQuality:
    acceptable: bool
    score: float
    usable_char_count: int
    replacement_ratio: float
    control_ratio: float
    private_use_ratio: float
    dominant_char_ratio: float
    warnings: tuple[str, ...] = ()


def _ratio(count: int, total: int) -> float:
    return count / total if total else 0.0


def assess_native_text(text: str, *, minimum_chars: int = 3) -> NativeTextQuality:
    """检查文字层是否值得信任。

    不依赖单一指标：同时检查可用字符数、替换符、控制符、
    Unicode 私用区字符和异常单字重复。这可以拦住缺失 ToUnicode
    映射时常见的乱码，但不尝试判断文本语义。
    """
    visible = [character for character in text if not character.isspace()]
    total = len(visible)
    replacements = sum(character == "\ufffd" for character in visible)
    controls = sum(unicodedata.category(character) == "Cc" for character in visible)
    private_use = sum(unicodedata.category(character) == "Co" for character in visible)
    usable = total - replacements - controls - private_use

    meaningful = [
        character
        for character in visible
        if character != "\ufffd" and unicodedata.category(character) not in {"Cc", "Co"}
    ]
    dominant_ratio = 0.0
    if meaningful:
        dominant_ratio = Counter(meaningful).most_common(1)[0][1] / len(meaningful)

    replacement_ratio = _ratio(replacements, total)
    control_ratio = _ratio(controls, total)
    private_use_ratio = _ratio(private_use, total)
    warnings: list[str] = []
    score = 1.0

    if usable < minimum_chars:
        warnings.append("文字层中可用字符过少。")
        score -= 0.65
    if replacement_ratio > 0.01:
        warnings.append("文字层包含较多 Unicode 替换符，可能已乱码。")
        score -= min(0.6, replacement_ratio * 4)
    if control_ratio > 0.005:
        warnings.append("文字层包含异常控制字符。")
        score -= min(0.5, control_ratio * 6)
    if private_use_ratio > 0.02:
        warnings.append("文字层大量使用 Unicode 私用区字符，字体映射可能不完整。")
        score -= min(0.6, private_use_ratio * 4)
    if len(meaningful) >= 20 and dominant_ratio > 0.45:
        warnings.append("文字层出现异常的单字高频重复，字体映射可能损坏。")
        score -= 0.55

    score = round(max(0.0, min(1.0, score)), 3)
    return NativeTextQuality(
        acceptable=not warnings and score >= 0.7,
        score=score,
        usable_char_count=max(0, usable),
        replacement_ratio=round(replacement_ratio, 4),
        control_ratio=round(control_ratio, 4),
        private_use_ratio=round(private_use_ratio, 4),
        dominant_char_ratio=round(dominant_ratio, 4),
        warnings=tuple(warnings),
    )

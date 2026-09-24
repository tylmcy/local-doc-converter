"""固定 PDF 样例的离线逐页字符准确率评测。"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Sequence


SCORING_VERSION = "pdf-character-accuracy-v1"


@dataclass(frozen=True, slots=True)
class PageAccuracy:
    page_number: int
    reference_characters: int
    output_characters: int
    edit_distance: int
    accuracy: float


@dataclass(frozen=True, slots=True)
class AccuracyResult:
    pages: tuple[PageAccuracy, ...]
    reference_characters: int
    edit_distance: int
    accuracy: float
    scoring_version: str = SCORING_VERSION

    @property
    def passes_release_gate(self) -> bool:
        return self.accuracy >= 0.9


def normalize_for_scoring(text: str) -> str:
    """只忽略排版空白；标点、英文、数字和繁简差异仍计错。"""
    return "".join(character for character in unicodedata.normalize("NFKC", text) if not character.isspace())


def levenshtein_distance(reference: str, output: str) -> int:
    """线性内存计算插入、删除和替换的最小编辑距离。"""
    if len(output) > len(reference):
        reference, output = output, reference
    previous = list(range(len(output) + 1))
    for row, expected in enumerate(reference, start=1):
        current = [row]
        for column, actual in enumerate(output, start=1):
            current.append(
                min(
                    current[column - 1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (expected != actual),
                )
            )
        previous = current
    return previous[-1]


def score_pages(reference_pages: Sequence[str], output_pages: Sequence[str]) -> AccuracyResult:
    """保持页边界，避免跨页错序被全文拼接评分掩盖。"""
    if not reference_pages or len(reference_pages) != len(output_pages):
        raise ValueError("标准答案与输出必须包含相同数量的页面，且至少一页。")
    pages: list[PageAccuracy] = []
    for number, (reference, output) in enumerate(zip(reference_pages, output_pages), start=1):
        expected = normalize_for_scoring(reference)
        actual = normalize_for_scoring(output)
        if not expected:
            raise ValueError(f"第 {number} 页标准答案为空，不能计算准确率。")
        distance = levenshtein_distance(expected, actual)
        pages.append(
            PageAccuracy(number, len(expected), len(actual), distance, max(0.0, 1 - distance / len(expected)))
        )
    total_characters = sum(page.reference_characters for page in pages)
    total_distance = sum(page.edit_distance for page in pages)
    return AccuracyResult(
        tuple(pages), total_characters, total_distance,
        max(0.0, 1 - total_distance / total_characters),
    )

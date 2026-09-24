import pytest

from local_doc_converter.pdf.accuracy import (
    levenshtein_distance,
    normalize_for_scoring,
    score_pages,
)


def test_normalization_removes_only_layout_whitespace():
    assert normalize_for_scoring("Ａ 字\n１，。\t") == "A字1,。"
    assert normalize_for_scoring("繁體") != normalize_for_scoring("繁体")


@pytest.mark.parametrize(
    ("output", "distance"),
    [("甲乙丙丁", 0), ("甲乙丙", 1), ("甲乙丙丁戊", 1), ("甲乙己丁", 1)],
)
def test_edit_distance_counts_all_error_types(output: str, distance: int):
    assert levenshtein_distance("甲乙丙丁", output) == distance


def test_page_boundary_catches_cross_page_disorder():
    result = score_pages(["甲乙", "丙丁"], ["丙丁", "甲乙"])
    assert result.edit_distance == 4
    assert result.accuracy == 0


def test_ninety_percent_is_inclusive_and_aggregate_is_weighted():
    passing = score_pages(["甲" * 9, "乙"], ["甲" * 9, "丙"])
    assert passing.accuracy == pytest.approx(0.9)
    assert passing.passes_release_gate
    assert passing.pages[1].accuracy == 0
    failing = score_pages(["甲" * 9, "乙"], ["甲" * 8, "丙"])
    assert failing.accuracy == pytest.approx(0.8)
    assert not failing.passes_release_gate


def test_reference_pages_cannot_be_empty_or_mismatched():
    with pytest.raises(ValueError):
        score_pages(["正文"], [])
    with pytest.raises(ValueError):
        score_pages([""], ["正文"])

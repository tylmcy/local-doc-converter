from pathlib import Path

from local_doc_converter.ast_tools import collect_stats, inspect_and_secure_images


def sample_ast():
    return {
        "pandoc-api-version": [1, 23, 1],
        "meta": {},
        "blocks": [
            {"t": "Header", "c": [1, ["", [], []], [{"t": "Str", "c": "标题"}]]},
            {"t": "BulletList", "c": []},
            {"t": "Table", "c": []},
            {"t": "CodeBlock", "c": [["", [], []], "x = 1"]},
            {"t": "Para", "c": [
                {"t": "Link", "c": [["", [], []], [{"t": "Str", "c": "链接"}], ["https://example.com", ""]]},
                {"t": "Image", "c": [["", [], []], [{"t": "Str", "c": "图"}], ["https://example.com/a.png", ""]]},
            ]},
        ],
    }


def test_collect_stats():
    stats = collect_stats(sample_ast())
    assert stats.headings == 1
    assert stats.lists == 1
    assert stats.tables == 1
    assert stats.code_blocks == 1
    assert stats.links == 1
    assert stats.images == 1


def test_remote_image_is_downgraded_for_docx(tmp_path: Path):
    document = sample_ast()
    warnings = inspect_and_secure_images(document, tmp_path, "docx")
    assert "远程图片未下载" in warnings[0]
    assert document["blocks"][-1]["c"][-1]["t"] == "Link"

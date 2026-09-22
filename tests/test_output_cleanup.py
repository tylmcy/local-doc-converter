from pathlib import Path

from local_doc_converter.output_cleanup import normalize_markdown


def test_compacts_markdown_table_separator(tmp_path: Path):
    output = tmp_path / "table.md"
    output.write_text(
        "| 名称     | 数量       |\n|:---------|-----------:|\n| 苹果     | 2          |\n",
        encoding="utf-8",
    )

    normalize_markdown(output)

    assert output.read_text(encoding="utf-8") == (
        "| 名称     | 数量       |\n| :--- | ---: |\n| 苹果     | 2          |\n"
    )

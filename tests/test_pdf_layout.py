from pathlib import Path

import pdfplumber
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from local_doc_converter.pdf.extract import extract_native_text
from local_doc_converter.pdf.layout import _looks_like_table, extract_page_layout


def _pdf_text(x: float, y: float, text: str, size: int = 11) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return f"BT /F1 {size} Tf {x} {y} Td ({escaped}) Tj ET\n".encode()


def _write_pdf(path: Path, content_bytes: bytes) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    content = DecodedStreamObject()
    content.set_data(content_bytes)
    page[NameObject("/Contents")] = writer._add_object(content)
    with path.open("wb") as stream:
        writer.write(stream)


def test_restores_two_column_reading_order(tmp_path: Path):
    path = tmp_path / "two-columns.pdf"
    commands = [_pdf_text(236, 748, "TWO COLUMN REPORT", 15)]
    for index, y in enumerate((700, 680, 660, 640), start=1):
        # 故意先写入右栏，验证结果依赖坐标而非 PDF 内容流的写入顺序。
        commands.append(_pdf_text(332, y, f"Right column line {index}"))
        commands.append(_pdf_text(60, y, f"Left column line {index}"))
    _write_pdf(path, b"".join(commands))

    result = extract_native_text(path)

    assert result.pages[0].column_count == 2
    assert result.double_column_pages == (1,)
    lines = result.pages[0].text.splitlines()
    assert lines[0] == "TWO COLUMN REPORT"
    assert lines[1:5] == [f"Left column line {index}" for index in range(1, 5)]
    assert lines[5:] == [f"Right column line {index}" for index in range(1, 5)]
    assert any("左栏后右栏" in warning for warning in result.warnings)


def test_recovers_merged_and_multiline_bordered_table(tmp_path: Path):
    path = tmp_path / "complex-table.pdf"
    commands = [_pdf_text(72, 742, "Sales Summary", 15)]
    # 外边框与横线。顶行不画内部竖线，形成跨三列的合并表头。
    for y in (700, 660, 620, 570):
        commands.append(f"72 {y} m 500 {y} l S\n".encode())
    commands.extend(
        [
            b"72 570 m 72 700 l S\n",
            b"500 570 m 500 700 l S\n",
            b"220 570 m 220 660 l S\n",
            b"350 570 m 350 660 l S\n",
        ]
    )
    commands.extend(
        [
            _pdf_text(210, 675, "Quarterly Results"),
            _pdf_text(82, 638, "Product"),
            _pdf_text(240, 638, "H1"),
            _pdf_text(370, 638, "H2"),
            _pdf_text(82, 598, "Alpha"),
            _pdf_text(240, 598, "10"),
            _pdf_text(370, 598, "12"),
            _pdf_text(82, 588, "continued"),
        ]
    )
    _write_pdf(path, b"".join(commands))

    with pdfplumber.open(path) as document:
        result = extract_page_layout(document.pages[0])

    assert result.structured_table_count == 1
    assert result.complex_table_count == 1
    assert "Quarterly Results\t\t" in result.text
    assert "Product\tH1\tH2" in result.text
    assert "Alpha continued\t10\t12" in result.text
    assert any("合并或多行单元格" in warning for warning in result.warnings)


def test_plain_single_column_paragraph_is_not_misclassified_as_table_or_columns(tmp_path: Path):
    path = tmp_path / "single-column.pdf"
    commands = [
        _pdf_text(72, 720 - index * 18, f"This is an ordinary paragraph line number {index}.")
        for index in range(8)
    ]
    _write_pdf(path, b"".join(commands))

    result = extract_native_text(path)

    assert result.pages[0].column_count == 1
    assert result.table_count == 0
    assert result.pages[0].text.count("ordinary paragraph") == 8


def test_recovers_conservative_three_column_borderless_table(tmp_path: Path):
    path = tmp_path / "borderless-table.pdf"
    rows = (
        ("Item", "Quantity", "Amount"),
        ("Notebook", "2", "18.00"),
        ("Pencil", "5", "6.50"),
    )
    commands: list[bytes] = []
    for row_index, row in enumerate(rows):
        y = 700 - row_index * 24
        for x, value in zip((72, 280, 440), row):
            commands.append(_pdf_text(x, y, value))
    _write_pdf(path, b"".join(commands))

    result = extract_native_text(path)

    assert result.table_count == 1
    assert "Item\tQuantity\tAmount" in result.pages[0].text
    assert "Notebook\t2\t18.00" in result.pages[0].text
    assert any("无框表格" in warning for warning in result.warnings)


def test_three_column_prose_reads_entire_lanes_before_next_lane():
    class FakePage:
        width = 612
        height = 792

        def find_tables(self, _settings):
            return []

        def extract_words(self, **_kwargs):
            words = []
            for row in range(25):
                for name, left, right in (
                    ("left", 45, 210), ("middle", 222, 388), ("right", 399, 565)
                ):
                    words.append({
                        "text": f"{name}-{row:02}",
                        "x0": left,
                        "x1": right,
                        "top": 140 + row * 20,
                        "bottom": 150 + row * 20,
                        "upright": True,
                    })
            return words

    result = extract_page_layout(FakePage())

    assert result.column_count == 3
    lines = result.text.splitlines()
    assert lines[:25] == [f"left-{row:02}" for row in range(25)]
    assert lines[25:50] == [f"middle-{row:02}" for row in range(25)]
    assert lines[50:] == [f"right-{row:02}" for row in range(25)]


def test_does_not_treat_tall_three_column_prose_as_borderless_table():
    rows = tuple(("first paragraph", "second paragraph", "third paragraph") for _ in range(25))
    assert not _looks_like_table(
        rows,
        strategy="text",
        bbox=(45, 50, 565, 760),
        page_width=612,
        page_height=792,
        visual_column_count=3,
    )

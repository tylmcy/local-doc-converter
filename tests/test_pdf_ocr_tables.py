from PIL import Image, ImageDraw
from pathlib import Path
from pypdf import PdfReader

from local_doc_converter.pdf.ocr_tables import reconstruct_ruled_table
from local_doc_converter.pdf.ocr import OcrPageResult
from local_doc_converter.pdf.ocr_worker import _page_from_dict, _page_to_dict


def _grid_and_ocr():
    image = Image.new("RGB", (600, 400), "white")
    draw = ImageDraw.Draw(image)
    xs = [40, 180, 320, 460, 560]
    ys = [60, 130, 200, 270, 340]
    for x in xs:
        draw.line((x, ys[0], x, ys[-1]), fill="black", width=3)
    for y in ys:
        draw.line((xs[0], y, xs[-1], y), fill="black", width=3)
    texts = []
    polys = []
    for row in range(4):
        for column in range(4):
            texts.append(f"r{row}c{column}")
            x, y = xs[column] + 10, ys[row] + 10
            polys.append([[x, y], [x + 40, y], [x + 40, y + 20], [x, y + 20]])
    texts.append("正文")
    polys.append([[20, 10], [70, 10], [70, 30], [20, 30]])
    return image, [{"res": {"rec_texts": texts, "rec_polys": polys}}]


def test_restores_clear_ruled_table_as_tab_separated_rows():
    image, raw = _grid_and_ocr()

    result = reconstruct_ruled_table(image, raw)

    assert result is not None
    assert result.row_count == 4
    assert result.column_count == 4
    assert result.text.splitlines() == [
        "正文",
        "r0c0\tr0c1\tr0c2\tr0c3",
        "r1c0\tr1c1\tr1c2\tr1c3",
        "r2c0\tr2c1\tr2c2\tr2c3",
        "r3c0\tr3c1\tr3c2\tr3c3",
    ]


def test_does_not_infer_table_without_grid():
    image, raw = _grid_and_ocr()
    blank = Image.new("RGB", image.size, "white")

    assert reconstruct_ruled_table(blank, raw) is None


def test_committed_scan_fixtures_have_no_text_layer():
    root = Path(__file__).resolve().parents[1] / "quality" / "pdf_corpus" / "generated"
    for ppi in (150, 300):
        page = PdfReader(root / f"scanned-table-{ppi}ppi.pdf").pages[0]
        assert page.extract_text() == ""
        assert 590 < float(page.mediabox.width) < 600


def test_ocr_table_count_survives_worker_serialization():
    page = OcrPageResult(1, "甲\t乙", 1, 0.9, structured_table_count=1)
    assert _page_from_dict(_page_to_dict(page)).structured_table_count == 1

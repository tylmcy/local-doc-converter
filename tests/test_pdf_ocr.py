from pathlib import Path

import pytest

from local_doc_converter.errors import OcrUnavailableError, PdfExtractionError
from local_doc_converter.pdf.ocr import (
    OcrEnvironmentStatus,
    PaddleOcrEngine,
    build_configured_ocr_engine,
    inspect_ocr_environment,
    parse_paddle_results,
    preprocess_ocr_image,
)


def test_parses_paddle_results_in_reading_order_and_reports_low_confidence():
    raw = [
        {
            "res": {
                "rec_texts": ["第二行", "第一行"],
                "rec_scores": [0.55, 0.98],
                "rec_polys": [
                    [[10, 40], [80, 40], [80, 55], [10, 55]],
                    [[10, 10], [80, 10], [80, 25], [10, 25]],
                ],
                "doc_preprocessor_res": {"angle": 90},
            }
        }
    ]

    result = parse_paddle_results(raw, 2)

    assert result.text == "第一行\n第二行"
    assert result.mean_confidence == pytest.approx(0.765)
    assert result.low_confidence_count == 1
    assert result.orientation_degrees == 90
    assert "0.60" in result.warnings[0]


def test_render_pixel_limit_rejects_before_bitmap_allocation(tmp_path: Path):
    from pypdf import PdfWriter

    source = tmp_path / "large-page.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with source.open("wb") as stream:
        writer.write(stream)

    engine = object.__new__(PaddleOcrEngine)
    engine.dpi = 200
    engine.max_render_pixels = 100
    with pytest.raises(PdfExtractionError, match="第 1 页渲染像素过多"):
        engine._render_page(source, 1)


def test_old_print_profile_changes_only_explicit_ocr_preprocessing(tmp_path: Path, monkeypatch):
    from PIL import Image
    from local_doc_converter.pdf import ocr

    monkeypatch.setattr(
        ocr,
        "inspect_ocr_environment",
        lambda model_root=None: OcrEnvironmentStatus(True, True, True, tmp_path, (), "ready"),
    )
    assert PaddleOcrEngine(tmp_path).dpi == 200
    enhanced = PaddleOcrEngine(tmp_path, profile="old_print")
    assert enhanced.dpi == 300

    image = Image.new("RGB", (5, 5), "white")
    image.putpixel((2, 2), (0, 0, 0))
    assert preprocess_ocr_image(image, "standard") is image
    filtered = preprocess_ocr_image(image, "old_print")
    assert filtered.getpixel((2, 2)) == (255, 255, 255)
    assert image.getpixel((2, 2)) == (0, 0, 0)
    filtered.close()
    image.close()

    with pytest.raises(ValueError, match="OCR 预处理模式"):
        PaddleOcrEngine(tmp_path, profile="unknown")


def test_restores_vertical_chinese_columns_from_right_to_left():
    raw = [
        {
            "res": {
                "rec_texts": [
                    "左页左列",
                    "右页左列",
                    "右页右列",
                    "左页右列上",
                    "左页右列下",
                ],
                "rec_scores": [0.91, 0.92, 0.93, 0.94, 0.95],
                "rec_polys": [
                    [[110, 10], [125, 10], [125, 130], [110, 130]],
                    [[310, 10], [325, 10], [325, 130], [310, 130]],
                    [[360, 10], [375, 10], [375, 130], [360, 130]],
                    [[160, 10], [175, 10], [175, 70], [160, 70]],
                    [[162, 75], [177, 75], [177, 135], [162, 135]],
                ],
            }
        }
    ]

    result = parse_paddle_results(raw, 1)

    assert result.text.splitlines() == [
        "右页右列",
        "右页左列",
        "左页右列上",
        "左页右列下",
        "左页左列",
    ]
    assert "从右到左" in result.warnings[0]


def test_does_not_reorder_horizontal_chinese_when_one_tall_block_exists():
    raw = [
        {
            "res": {
                "rec_texts": ["第一行", "第二行", "竖排标识"],
                "rec_scores": [0.9, 0.9, 0.9],
                "rec_polys": [
                    [[10, 10], [100, 10], [100, 25], [10, 25]],
                    [[10, 40], [100, 40], [100, 55], [10, 55]],
                    [[130, 10], [145, 10], [145, 100], [130, 100]],
                ],
            }
        }
    ]

    result = parse_paddle_results(raw, 1)

    assert result.text.splitlines() == ["第一行", "竖排标识", "第二行"]
    assert not any("竖排文字" in warning for warning in result.warnings)


def test_vertical_page_number_is_recorded_outside_body():
    texts = ["正文第一列内容", "正文第二列内容", "正文第三列内容", "正文第四列内容", "附", "錄", "公禱書四百年之紀念", "世"]
    positions = [300, 260, 220, 180, 30, 30, 30, 30]
    tops = [100, 100, 100, 100, 280, 360, 440, 1010]
    heights = [800, 800, 800, 800, 30, 30, 240, 50]
    raw = [{"res": {
        "rec_texts": texts,
        "rec_scores": [0.9] * len(texts),
        "rec_polys": [
            [[x, y], [x + 22, y], [x + 22, y + height], [x, y + height]]
            for x, y, height in zip(positions, tops, heights)
        ],
    }}]

    result = parse_paddle_results(raw, 1)

    assert "世" not in result.text.splitlines()
    assert result.printed_page_numbers == ("世",)
    assert {result.text.splitlines()[index] for index in result.marginal_line_indices} == {
        "附", "錄", "公禱書四百年之紀念"
    }


def test_prepared_server_recognition_model_is_preferred(tmp_path: Path, monkeypatch):
    from local_doc_converter.pdf import ocr

    monkeypatch.setattr(
        ocr,
        "inspect_ocr_environment",
        lambda model_root=None: OcrEnvironmentStatus(True, True, True, tmp_path, (), "ready"),
    )
    assert PaddleOcrEngine(tmp_path).recognition_model_name == "PP-OCRv5_mobile_rec"
    model_dir = tmp_path / "PP-OCRv5_server_rec"
    model_dir.mkdir()
    (model_dir / "inference.yml").write_text("model", encoding="utf-8")
    assert PaddleOcrEngine(tmp_path).recognition_model_name == "PP-OCRv5_server_rec"


def test_missing_local_environment_fails_without_downloading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    missing_root = tmp_path / "missing"
    monkeypatch.setenv("LOCAL_DOC_CONVERTER_PADDLE_MODEL_DIR", str(missing_root))
    status = inspect_ocr_environment(missing_root)
    assert not status.ready
    assert len(status.missing_models) == 3

    with pytest.raises(OcrUnavailableError, match="需要离线 OCR"):
        build_configured_ocr_engine()

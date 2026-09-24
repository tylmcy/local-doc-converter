from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from local_doc_converter.pdf.classify import PdfPageKind, classify_page_metrics, classify_pdf


def _add_page(
    writer: PdfWriter,
    *,
    include_text: bool = False,
    include_full_page_image: bool = False,
) -> None:
    width, height = 612, 792
    page = writer.add_blank_page(width=width, height=height)
    resources = DictionaryObject()
    commands: list[bytes] = []

    if include_text:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        resources[NameObject("/Font")] = DictionaryObject(
            {NameObject("/F1"): writer._add_object(font)}
        )
        commands.append(
            b"BT /F1 14 Tf 72 720 Td "
            b"(This page contains enough searchable text for classification.) Tj ET"
        )

    if include_full_page_image:
        image = DecodedStreamObject()
        image.set_data(b"\x80")
        image.update(
            {
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(1),
                NameObject("/Height"): NumberObject(1),
                NameObject("/ColorSpace"): NameObject("/DeviceGray"),
                NameObject("/BitsPerComponent"): NumberObject(8),
            }
        )
        resources[NameObject("/XObject")] = DictionaryObject(
            {NameObject("/Im1"): writer._add_object(image)}
        )
        commands.insert(0, b"q 612 0 0 792 0 0 cm /Im1 Do Q")

    page[NameObject("/Resources")] = resources
    if commands:
        content = DecodedStreamObject()
        content.set_data(b"\n".join(commands))
        page[NameObject("/Contents")] = writer._add_object(content)


def _write_classification_pdf(path: Path) -> None:
    writer = PdfWriter()
    _add_page(writer)
    _add_page(writer, include_text=True)
    _add_page(writer, include_full_page_image=True)
    _add_page(writer, include_text=True, include_full_page_image=True)
    with path.open("wb") as stream:
        writer.write(stream)


@pytest.mark.parametrize(
    ("text_chars", "image_count", "coverage", "expected"),
    [
        (0, 0, 0.0, PdfPageKind.EMPTY),
        (0, 1, 0.1, PdfPageKind.SCANNED),
        (5, 1, 0.8, PdfPageKind.SCANNED),
        (30, 1, 0.8, PdfPageKind.MIXED),
        (5, 0, 0.0, PdfPageKind.TEXT),
        (30, 1, 0.1, PdfPageKind.TEXT),
    ],
)
def test_classify_page_metrics(
    text_chars: int,
    image_count: int,
    coverage: float,
    expected: PdfPageKind,
):
    assert (
        classify_page_metrics(
            text_char_count=text_chars,
            image_count=image_count,
            image_coverage_ratio=coverage,
        )
        is expected
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"text_char_count": -1, "image_count": 0, "image_coverage_ratio": 0.0},
        {"text_char_count": 0, "image_count": -1, "image_coverage_ratio": 0.0},
        {"text_char_count": 0, "image_count": 0, "image_coverage_ratio": 1.1},
    ],
)
def test_classify_page_metrics_rejects_invalid_values(kwargs: dict[str, float]):
    with pytest.raises(ValueError):
        classify_page_metrics(**kwargs)


def test_classifies_empty_text_scanned_and_mixed_pages(tmp_path: Path):
    path = tmp_path / "pages.pdf"
    _write_classification_pdf(path)

    result = classify_pdf(path)

    assert result.document_kind is PdfPageKind.MIXED
    assert result.page_count == 4
    assert result.empty_pages == 1
    assert result.text_pages == 1
    assert result.scanned_pages == 1
    assert result.mixed_pages == 1
    assert [page.kind for page in result.pages] == [
        PdfPageKind.EMPTY,
        PdfPageKind.TEXT,
        PdfPageKind.SCANNED,
        PdfPageKind.MIXED,
    ]
    assert result.pages[2].image_coverage_ratio == pytest.approx(1.0)
    assert "离线 OCR" in result.pages[2].warnings[0]
    assert "文字层质量" in result.pages[3].warnings[0]

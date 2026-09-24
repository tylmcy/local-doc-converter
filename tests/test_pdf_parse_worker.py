import os
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from local_doc_converter.errors import PdfExtractionError, ValidationError
from local_doc_converter.pdf.parse_worker import parse_pdf_isolated


def _write_text_pdf(path: Path) -> None:
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
    content.set_data(b"BT /F1 14 Tf 72 720 Td (Isolated PDF parser process.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    with path.open("wb") as stream:
        writer.write(stream)


def test_parses_in_a_different_short_lived_process(tmp_path: Path):
    source = tmp_path / "isolated.pdf"
    _write_text_pdf(source)

    result = parse_pdf_isolated(source, timeout_seconds=10)

    assert result.worker_pid != os.getpid()
    assert result.duration_seconds >= 0
    assert result.classification.page_count == 1
    assert result.native_text.pages[0].text == "Isolated PDF parser process."


def test_preserves_understandable_validation_errors(tmp_path: Path):
    source = tmp_path / "fake.pdf"
    source.write_bytes(b"not a pdf")

    with pytest.raises(ValidationError, match="文件头"):
        parse_pdf_isolated(source, timeout_seconds=10)


def test_terminates_parser_when_timeout_expires(tmp_path: Path):
    source = tmp_path / "timeout.pdf"
    _write_text_pdf(source)

    with pytest.raises(PdfExtractionError, match="超过.*已终止"):
        parse_pdf_isolated(source, timeout_seconds=0.000001)

    assert not list(tmp_path.glob("local_doc_pdf_parse_*"))

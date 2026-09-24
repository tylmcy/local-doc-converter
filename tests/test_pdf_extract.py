from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from local_doc_converter.pdf.extract import extract_native_text, normalize_extracted_text


def _write_text_pdf(path: Path, text: bytes = b"Native PDF text extraction works.") -> None:
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
    content.set_data(b"BT /F1 14 Tf 72 720 Td (" + text + b") Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    with path.open("wb") as stream:
        writer.write(stream)


def test_extracts_native_text_without_ocr(tmp_path: Path):
    path = tmp_path / "native.pdf"
    _write_text_pdf(path)

    result = extract_native_text(path)

    assert len(result.pages) == 1
    assert result.pages[0].text == "Native PDF text extraction works."
    assert result.pages[0].word_count == 5
    assert result.image_count == 0


def test_normalizes_invisible_characters_and_spacing():
    assert normalize_extracted_text(" 中\u200b文   text\r\n\r\n\r\n下一段 ") == "中文 text\n\n下一段"

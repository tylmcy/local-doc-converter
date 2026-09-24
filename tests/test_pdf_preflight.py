from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import FloatObject, NameObject

from local_doc_converter.errors import ValidationError
from local_doc_converter.pdf.preflight import PdfSafetyLimits, _effective_page_size, inspect_pdf


def _write_pdf(
    path: Path,
    *,
    pages: int = 1,
    width: float = 612,
    height: float = 792,
    user_unit: float | None = None,
    password: str | None = None,
) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=width, height=height)
        if user_unit is not None:
            page[NameObject("/UserUnit")] = FloatObject(user_unit)
    if password:
        writer.encrypt(password)
    with path.open("wb") as stream:
        writer.write(stream)


def test_valid_pdf_passes_without_writing_files(tmp_path: Path):
    path = tmp_path / "normal.pdf"
    _write_pdf(path, pages=2)

    result = inspect_pdf(path)

    assert result.pdf_version.startswith("1.")
    assert result.file_size == path.stat().st_size
    assert result.page_count == 2
    assert result.object_count > 0
    assert result.max_page_width_points == pytest.approx(612)
    assert result.max_page_height_points == pytest.approx(792)
    assert not result.warnings
    assert list(tmp_path.iterdir()) == [path]


def test_rejects_empty_fake_truncated_and_page_less_pdf(tmp_path: Path):
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    with pytest.raises(ValidationError, match="为空"):
        inspect_pdf(empty)

    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"not a pdf")
    with pytest.raises(ValidationError, match="文件头"):
        inspect_pdf(fake)

    truncated = tmp_path / "truncated.pdf"
    _write_pdf(truncated)
    truncated.write_bytes(truncated.read_bytes().replace(b"%%EOF", b""))
    with pytest.raises(ValidationError, match="%%EOF"):
        inspect_pdf(truncated)

    page_less = tmp_path / "page-less.pdf"
    with page_less.open("wb") as stream:
        PdfWriter().write(stream)
    with pytest.raises(ValidationError, match="不包含可处理的页面"):
        inspect_pdf(page_less)


def test_rejects_encrypted_pdf(tmp_path: Path):
    path = tmp_path / "encrypted.pdf"
    _write_pdf(path, password="secret")

    with pytest.raises(ValidationError, match="加密|密码"):
        inspect_pdf(path)


def test_enforces_file_page_object_and_dimension_limits(tmp_path: Path):
    normal = tmp_path / "normal.pdf"
    _write_pdf(normal, pages=2)

    with pytest.raises(ValidationError, match="文件过大"):
        inspect_pdf(normal, PdfSafetyLimits(max_file_size=10))
    with pytest.raises(ValidationError, match="页数过多"):
        inspect_pdf(normal, PdfSafetyLimits(max_pages=1))
    with pytest.raises(ValidationError, match="对象过多"):
        inspect_pdf(normal, PdfSafetyLimits(max_objects=1))

    huge = tmp_path / "huge.pdf"
    _write_pdf(huge, width=1000, height=1000, user_unit=20)
    with pytest.raises(ValidationError, match="尺寸异常"):
        inspect_pdf(huge, PdfSafetyLimits(max_page_dimension_points=10_000))


def test_reports_active_content_attachments_and_external_links(tmp_path: Path):
    path = tmp_path / "active.pdf"
    _write_pdf(path)
    marker_comment = (
        b"% /JavaScript /OpenAction /Launch /SubmitForm "
        b"/RichMedia /XFA /AcroForm /EmbeddedFiles /URI /GoToR\n"
    )
    path.write_bytes(path.read_bytes() + marker_comment)

    result = inspect_pdf(path)

    assert result.has_active_content
    assert result.has_embedded_files
    assert result.has_external_links
    assert any("JavaScript" in warning for warning in result.warnings)
    assert any("启动外部程序" in warning for warning in result.warnings)
    assert any("嵌入文件" in warning for warning in result.warnings)
    assert any("外部链接" in warning for warning in result.warnings)


@pytest.mark.parametrize("user_unit", [0, -1])
def test_rejects_invalid_user_unit(tmp_path: Path, user_unit: float):
    path = tmp_path / "bad-unit.pdf"
    _write_pdf(path, user_unit=user_unit)

    with pytest.raises(ValidationError, match="UserUnit"):
        inspect_pdf(path)


@pytest.mark.parametrize("user_unit", [float("inf"), float("nan")])
def test_rejects_non_finite_user_unit_without_serializing_invalid_pdf(user_unit: float):
    class Box:
        width = 612
        height = 792

    class Page:
        mediabox = Box()

        @staticmethod
        def get(key: str, default: object) -> float:
            assert key == "/UserUnit"
            return user_unit

    with pytest.raises(ValidationError, match="UserUnit"):
        _effective_page_size(Page())

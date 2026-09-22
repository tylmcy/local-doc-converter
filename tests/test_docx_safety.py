import stat
import warnings
import zipfile
from pathlib import Path

import pytest

from local_doc_converter.docx_safety import DocxSafetyLimits, inspect_docx
from local_doc_converter.errors import ValidationError

_CONTENT_TYPES = b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
_DOCUMENT_XML = b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'


def _write_docx(path: Path, extras: list[tuple[str | zipfile.ZipInfo, bytes]] | None = None) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("word/document.xml", _DOCUMENT_XML)
        for name, content in extras or []:
            archive.writestr(name, content)


def test_valid_docx_passes_without_extracting(tmp_path: Path):
    path = tmp_path / "normal.docx"
    _write_docx(path)

    result = inspect_docx(path)

    assert result.entry_count == 2
    assert result.total_uncompressed_size == len(_CONTENT_TYPES) + len(_DOCUMENT_XML)
    assert not result.warnings
    assert list(tmp_path.iterdir()) == [path]


def test_rejects_non_zip_and_missing_required_members(tmp_path: Path):
    fake = tmp_path / "fake.docx"
    fake.write_text("not a zip", encoding="utf-8")
    with pytest.raises(ValidationError, match="不是有效"):
        inspect_docx(fake)

    incomplete = tmp_path / "incomplete.docx"
    with zipfile.ZipFile(incomplete, "w") as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
    with pytest.raises(ValidationError, match="word/document.xml"):
        inspect_docx(incomplete)


@pytest.mark.parametrize("unsafe_name", ["../outside.xml", "/absolute.xml", "word\\evil.xml", "C:/evil.xml"])
def test_rejects_unsafe_member_paths(tmp_path: Path, unsafe_name: str):
    path = tmp_path / "unsafe.docx"
    _write_docx(path, [(unsafe_name, b"x")])

    with pytest.raises(ValidationError):
        inspect_docx(path)


def test_rejects_symbolic_link_member(tmp_path: Path):
    path = tmp_path / "symlink.docx"
    link = zipfile.ZipInfo("word/media/link.png")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    _write_docx(path, [(link, b"../../outside")])

    with pytest.raises(ValidationError, match="符号链接"):
        inspect_docx(path)


def test_rejects_encrypted_member_flag(tmp_path: Path):
    path = tmp_path / "encrypted-flag.docx"
    _write_docx(path)
    archive_data = bytearray(path.read_bytes())
    local_header = archive_data.index(b"PK\x03\x04")
    central_header = archive_data.index(b"PK\x01\x02")
    archive_data[local_header + 6 : local_header + 8] = (1).to_bytes(2, "little")
    archive_data[central_header + 8 : central_header + 10] = (1).to_bytes(2, "little")
    path.write_bytes(archive_data)

    with pytest.raises(ValidationError, match="加密 ZIP 条目"):
        inspect_docx(path)


def test_rejects_duplicate_member(tmp_path: Path):
    path = tmp_path / "duplicate.docx"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        _write_docx(path, [("word/document.xml", b"duplicate")])

    with pytest.raises(ValidationError, match="重复条目"):
        inspect_docx(path)


def test_enforces_entry_size_and_total_limits(tmp_path: Path):
    path = tmp_path / "limits.docx"
    _write_docx(path, [("word/media/data.bin", b"abcdefghij")])

    with pytest.raises(ValidationError, match="条目过多"):
        inspect_docx(path, DocxSafetyLimits(max_entries=2))
    with pytest.raises(ValidationError, match="条目解压后过大"):
        inspect_docx(path, DocxSafetyLimits(max_member_uncompressed_size=5))
    with pytest.raises(ValidationError, match="总体积过大"):
        inspect_docx(path, DocxSafetyLimits(max_total_uncompressed_size=20))


def test_enforces_member_and_total_compression_ratio(tmp_path: Path):
    path = tmp_path / "ratio.docx"
    _write_docx(path, [("word/media/repeated.bin", b"A" * 4096)])

    with pytest.raises(ValidationError, match="条目压缩比异常"):
        inspect_docx(path, DocxSafetyLimits(max_member_compression_ratio=2))
    with pytest.raises(ValidationError, match="总压缩比异常"):
        inspect_docx(
            path,
            DocxSafetyLimits(
                max_member_compression_ratio=10_000,
                max_total_compression_ratio=2,
            ),
        )


def test_warns_for_macro_ole_and_external_relationship(tmp_path: Path):
    path = tmp_path / "active-content.docx"
    relationships = b'<Relationships><Relationship TargetMode="External" Target="https://example.com"/></Relationships>'
    _write_docx(
        path,
        [
            ("word/vbaProject.bin", b"macro"),
            ("word/embeddings/oleObject1.bin", b"ole"),
            ("word/_rels/document.xml.rels", relationships),
        ],
    )

    result = inspect_docx(path)

    assert len(result.warnings) == 3
    assert any("宏项目" in warning for warning in result.warnings)
    assert any("OLE" in warning for warning in result.warnings)
    assert any("外部链接" in warning for warning in result.warnings)


def test_rejects_bad_crc(tmp_path: Path):
    path = tmp_path / "bad-crc.docx"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("word/document.xml", _DOCUMENT_XML)

    corrupted = bytearray(path.read_bytes())
    offset = corrupted.index(_DOCUMENT_XML)
    corrupted[offset] = ord("X")
    path.write_bytes(corrupted)

    with pytest.raises(ValidationError, match="损坏"):
        inspect_docx(path)

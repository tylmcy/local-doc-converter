from pathlib import Path

import pytest

from local_doc_converter.errors import ValidationError
from local_doc_converter.security import ensure_output_dir, safe_filename, unique_path


@pytest.mark.parametrize("name", ["../a.txt", "a/b.txt", "a\\b.txt", ".hidden"])
def test_rejects_unsafe_filename(name: str):
    with pytest.raises(ValidationError):
        safe_filename(name)


def test_unique_path_does_not_overwrite(tmp_path: Path):
    (tmp_path / "result.md").write_text("existing", encoding="utf-8")
    assert unique_path(tmp_path, "result.md").name == "result_2.md"


def test_output_dir_must_be_below_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    allowed = ensure_output_dir(fake_home / "Documents" / "converted")
    assert allowed.is_dir()
    with pytest.raises(ValidationError):
        ensure_output_dir(fake_home)

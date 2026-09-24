from pathlib import Path

import pytest
from pypdf import PdfWriter

from local_doc_converter.errors import OcrUnavailableError, PdfExtractionError, ValidationError
from local_doc_converter.pdf.ocr import OcrPageResult
from local_doc_converter.pdf import ocr_worker
from local_doc_converter.pdf.ocr_worker import _worker_payload, ocr_pdf_pages_isolated


def _write_blank_pdf(path: Path, pages: int = 2) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as stream:
        writer.write(stream)


class FakePreparedOcr:
    def __init__(self) -> None:
        self.prepared = False
        self.calls: list[int] = []

    def prepare(self) -> None:
        self.prepared = True

    def recognize_page(self, pdf_path: Path, page_number: int) -> OcrPageResult:
        assert self.prepared
        self.calls.append(page_number)
        return OcrPageResult(page_number, f"page {page_number}", 1, 0.95)


def test_worker_reuses_one_prepared_engine_for_ordered_pages(tmp_path: Path):
    source = tmp_path / "pages.pdf"
    progress = tmp_path / "progress.json"
    _write_blank_pdf(source)
    engine = FakePreparedOcr()

    payload = _worker_payload(source, (1, 2), progress, engine_factory=lambda: engine)

    assert payload["ok"] is True
    assert engine.calls == [1, 2]
    assert [page["page_number"] for page in payload["pages"]] == [1, 2]
    assert progress.is_file()


def test_worker_passes_selected_profile_to_configured_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "page.pdf"
    _write_blank_pdf(source, pages=1)
    selected: list[str] = []

    def configured(profile: str):
        selected.append(profile)
        return FakePreparedOcr()

    monkeypatch.setattr(ocr_worker, "build_configured_ocr_engine", configured)
    payload = _worker_payload(
        source, (1,), tmp_path / "progress.json", profile="old_print"
    )

    assert payload["ok"] is True
    assert selected == ["old_print"]


def test_worker_reports_later_failed_page_without_returning_partial_pages(tmp_path: Path):
    source = tmp_path / "pages.pdf"
    _write_blank_pdf(source)

    class FailingSecondPage(FakePreparedOcr):
        def recognize_page(self, pdf_path: Path, page_number: int) -> OcrPageResult:
            if page_number == 2:
                raise RuntimeError("模拟识别崩溃")
            return super().recognize_page(pdf_path, page_number)

    payload = _worker_payload(
        source, (1, 2), tmp_path / "progress.json", engine_factory=FailingSecondPage
    )

    assert payload["ok"] is False
    assert "第 2 页" in payload["message"]
    assert "pages" not in payload


@pytest.mark.parametrize("pages", [(), (0,), (2, 1), (1, 1), (1, 3)])
def test_worker_rejects_invalid_page_requests(tmp_path: Path, pages: tuple[int, ...]):
    source = tmp_path / "pages.pdf"
    _write_blank_pdf(source)

    if pages == (1, 3):
        payload = _worker_payload(
            source,
            pages,
            tmp_path / "progress.json",
            engine_factory=FakePreparedOcr,
        )
        assert payload["ok"] is False
        assert payload["error_kind"] == "ValidationError"
    else:
        with pytest.raises(ValidationError):
            ocr_pdf_pages_isolated(source, pages)


def test_child_reports_missing_local_ocr_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "page.pdf"
    _write_blank_pdf(source, pages=1)
    monkeypatch.setenv("LOCAL_DOC_CONVERTER_PADDLE_MODEL_DIR", str(tmp_path / "missing"))

    with pytest.raises(OcrUnavailableError, match="OCR.*未就绪|模型目录"):
        ocr_pdf_pages_isolated(
            source,
            (1,),
            startup_timeout_seconds=10,
            page_timeout_seconds=10,
        )


def test_terminates_ocr_process_when_startup_timeout_expires(tmp_path: Path):
    source = tmp_path / "page.pdf"
    _write_blank_pdf(source, pages=1)

    with pytest.raises(PdfExtractionError, match="初始化超过.*已终止"):
        ocr_pdf_pages_isolated(
            source,
            (1,),
            startup_timeout_seconds=0.000001,
            page_timeout_seconds=10,
        )


def test_terminates_current_page_when_heartbeat_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "page.pdf"
    _write_blank_pdf(source, pages=1)
    terminated: list[bool] = []

    class FakeRunningProcess:
        returncode = None

        @staticmethod
        def poll():
            return None

    def fake_start(command, *, stage_label, capture_output):
        assert stage_label == "PDF 渲染/OCR"
        assert not capture_output
        Path(command[-3]).write_text(
            '{"status":"ready","completed_pages":[]}',
            encoding="utf-8",
        )
        return FakeRunningProcess()

    monkeypatch.setattr(ocr_worker, "start_worker", fake_start)
    monkeypatch.setattr(
        ocr_worker,
        "terminate_process_group",
        lambda process: terminated.append(True),
    )

    with pytest.raises(PdfExtractionError, match="第 1 页渲染/OCR.*已终止"):
        ocr_pdf_pages_isolated(
            source,
            (1,),
            startup_timeout_seconds=10,
            page_timeout_seconds=0.000001,
        )

    assert terminated == [True]

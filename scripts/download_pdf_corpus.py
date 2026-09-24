"""按固定哈希显式下载真实 PDF 质量样例。"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from local_doc_converter.errors import ValidationError  # noqa: E402
from local_doc_converter.pdf.corpus import (  # noqa: E402
    PdfCorpusSample,
    load_pdf_corpus_manifest,
    sha256_file,
    verify_pdf_corpus_file,
)

DEFAULT_MANIFEST = PROJECT_ROOT / "quality" / "pdf_corpus" / "manifest.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "quality" / "pdf_corpus" / "downloads"
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="下载并验证真实 PDF 质量样例")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sample", action="append", dest="sample_ids", help="只下载指定样例 ID，可重复")
    return parser


def _select_samples(samples: tuple[PdfCorpusSample, ...], requested: list[str] | None) -> tuple[PdfCorpusSample, ...]:
    if not requested:
        return samples
    index = {sample.sample_id: sample for sample in samples}
    missing = sorted(set(requested) - set(index))
    if missing:
        raise ValidationError(f"未找到 PDF 样例：{', '.join(missing)}")
    return tuple(index[sample_id] for sample_id in dict.fromkeys(requested))


def download_sample(sample: PdfCorpusSample, output_dir: Path) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = (output_dir / sample.filename).resolve(strict=False)
    if destination.parent != output_dir.resolve(strict=False):
        raise ValidationError(f"样例 {sample.sample_id} 的输出路径不安全。")
    if destination.exists():
        errors = verify_pdf_corpus_file(sample, destination)
        if not errors:
            return "cached"
        raise ValidationError(f"本地样例 {sample.sample_id} 校验失败：{' '.join(errors)}")

    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    request = urllib.request.Request(
        sample.source_url,
        headers={"User-Agent": "local-doc-converter-pdf-corpus/1"},
    )
    downloaded = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("xb") as stream:
            while chunk := response.read(1024 * 1024):
                downloaded += len(chunk)
                if downloaded > MAX_DOWNLOAD_BYTES:
                    raise ValidationError(f"样例 {sample.sample_id} 超过 50 MB 下载限制。")
                stream.write(chunk)
        if temporary.stat().st_size != sample.file_size:
            raise ValidationError(f"样例 {sample.sample_id} 下载大小与清单不一致。")
        with temporary.open("rb") as stream:
            if not stream.read(5).startswith(b"%PDF-"):
                raise ValidationError(f"样例 {sample.sample_id} 的文件头不是 PDF。")
        if sha256_file(temporary) != sample.sha256:
            raise ValidationError(f"样例 {sample.sample_id} 的 SHA-256 与清单不一致。")
        # os.replace 只在所有校验通过后暴露最终文件名。
        os.replace(temporary, destination)
        errors = verify_pdf_corpus_file(sample, destination)
        if errors:
            destination.unlink(missing_ok=True)
            raise ValidationError(f"样例 {sample.sample_id} 最终校验失败：{' '.join(errors)}")
        return "downloaded"
    except (OSError, urllib.error.URLError) as exc:
        raise ValidationError(f"下载样例 {sample.sample_id} 失败：{exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    args = build_parser().parse_args()
    try:
        manifest = load_pdf_corpus_manifest(args.manifest)
        samples = _select_samples(manifest.samples, args.sample_ids)
        for sample in samples:
            state = download_sample(sample, args.output)
            print(f"{sample.sample_id}: {state}")
    except ValidationError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

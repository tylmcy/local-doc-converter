"""中文文本编码检测。"""

from __future__ import annotations

from dataclasses import dataclass

from charset_normalizer import from_bytes

from .errors import EncodingDetectionError


@dataclass(frozen=True, slots=True)
class DecodedText:
    text: str
    encoding: str
    confidence: float


def decode_text(data: bytes) -> DecodedText:
    """优先处理 BOM/UTF-8，再由 charset-normalizer 判断，最后尝试 GB18030。"""
    if not data:
        raise EncodingDetectionError("文本文件为空。")

    utf8_candidates = ("utf-8-sig",) if data.startswith(b"\xef\xbb\xbf") else ("utf-8",)
    for encoding in utf8_candidates:
        try:
            text = data.decode(encoding)
            return DecodedText(text=text, encoding=encoding, confidence=1.0)
        except UnicodeDecodeError:
            pass

    best = from_bytes(data).best()
    if best is not None and best.encoding:
        try:
            text = str(best)
            # coherence 可能为 0（短文本），此时编码仍可由严格解码兜底确认。
            if "\ufffd" not in text:
                return DecodedText(text=text, encoding=best.encoding, confidence=float(best.percent_coherence) / 100)
        except (UnicodeError, AttributeError):
            pass

    try:
        return DecodedText(text=data.decode("gb18030"), encoding="gb18030", confidence=0.5)
    except UnicodeDecodeError as exc:
        raise EncodingDetectionError("无法识别文本编码，请将文件另存为 UTF-8 后重试。") from exc

"""对 Pandoc 生成的 DOCX 应用轻量、可预期的中文基础样式。"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt


def apply_basic_docx_styles(path: Path) -> None:
    document = Document(path)
    styles = document.styles

    normal = styles["Normal"]
    normal.font.name = "PingFang SC"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "PingFang SC")
    normal.font.size = Pt(11)

    for level in range(1, 7):
        style_name = f"Heading {level}"
        if style_name not in styles:
            continue
        style = styles[style_name]
        style.font.name = "PingFang SC"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "PingFang SC")

    document.save(path)

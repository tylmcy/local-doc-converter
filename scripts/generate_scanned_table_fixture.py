"""生成可再分发的中文扫描表格 PDF（不包含可提取文字层）。"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "quality" / "pdf_corpus" / "generated"
TRUTH = ROOT / "quality" / "pdf_corpus" / "scanned_table_truth.json"

HEADERS = ["编号", "项目", "数量", "金额（元）", "备注"]
ROWS = [
    ["01", "办公用纸", "12箱", "1,680.00", "已验收"],
    ["02", "激光墨盒", "8支", "2,400.00", "待入库"],
    ["03", "网络交换机", "3台", "3,150.00", "含税"],
    ["04", "档案整理", "5批", "925.50", "已完成"],
    ["05", "设备维护", "2次", "1,200.00", "九月"],
    ["06", "培训资料", "30册", "450.00", "双面印刷"],
    ["合计", "六项采购", "—", "9,805.50", "人民币"],
]


def _font_path(explicit: Path | None) -> Path:
    if explicit:
        return explicit
    result = subprocess.run(
        ["fc-match", "-f", "%{file}", "PingFang SC"],
        check=True,
        capture_output=True,
        text=True,
    )
    path = Path(result.stdout.strip())
    if not path.is_file():
        raise FileNotFoundError("未找到中文字体；请传 --font 指定本机中文字体。")
    return path


def build_page(font_path: Path) -> Image.Image:
    """先在 300 PPI 绘制，再降采样模拟不同扫描分辨率。"""
    image = Image.new("RGB", (2481, 3507), "white")
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.truetype(str(font_path), 64)
    cell_font = ImageFont.truetype(str(font_path), 47)
    draw.text((135, 150), "采购明细表 2024年09月", font=title_font, fill="#111111")
    draw.text((135, 260), "单位：元    记录编号：TEST-2024-09", font=cell_font, fill="#333333")
    x_edges = [135, 365, 1070, 1320, 1840, 2350]
    y_edges = [420 + 165 * index for index in range(len(ROWS) + 2)]
    for x in x_edges:
        draw.line((x, y_edges[0], x, y_edges[-1]), fill="#222222", width=3)
    for y in y_edges:
        draw.line((x_edges[0], y, x_edges[-1], y), fill="#222222", width=3)
    for row_index, row in enumerate([HEADERS, *ROWS]):
        y = y_edges[row_index] + 55
        for column_index, value in enumerate(row):
            draw.text((x_edges[column_index] + 18, y), value, font=cell_font, fill="#111111")
    draw.text((135, 2010), "说明：此页为程序生成的扫描表格测试样例。", font=cell_font, fill="#333333")
    return image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, help="本机中文字体路径；默认用 fc-match 查找")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    font_path = _font_path(args.font)
    if not font_path.is_file():
        parser.error(f"字体不存在：{font_path}")
    args.output.mkdir(parents=True, exist_ok=True)
    page = build_page(font_path)
    for ppi in (150, 300):
        scan = page if ppi == 300 else page.resize((1241, 1754), Image.Resampling.LANCZOS)
        path = args.output / f"scanned-table-{ppi}ppi.pdf"
        scan.save(path, "PDF", resolution=ppi, quality=90)
        if scan is not page:
            scan.close()
        print(path)
    page.close()
    TRUTH.write_text(
        json.dumps({"headers": HEADERS, "rows": ROWS}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

"""显式下载并初始化本项目固定的 PP-OCRv5 本地模型。"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    default = Path.home() / "Library" / "Application Support" / "LocalDocConverter" / "models"
    parser = argparse.ArgumentParser(
        description="为本地文档互转工具准备 PaddleOCR PP-OCRv5 模型（本命令需要联网）。"
    )
    parser.add_argument("--model-dir", type=Path, default=default, help=f"模型根目录，默认：{default}")
    parser.add_argument(
        "--high-accuracy",
        action="store_true",
        help="额外准备约 81 MB 的 PP-OCRv5_server_rec；存在时转换优先使用它。",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = args.model_dir.expanduser().resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(root.parent / "paddlex-cache")

    try:
        from paddlex.inference.utils.official_models import official_models
        from paddleocr import PaddleOCR
    except ImportError:
        print("错误：尚未安装 PaddlePaddle 3.3.0 和 PaddleOCR 3.7.0，请先按 README 安装。")
        return 2

    print(f"正在准备模型：{root}")
    print("缺少模型时会访问 Paddle 官方模型源；准备完成后日常转换不再需联网。")
    expected = [
        "PP-OCRv5_mobile_det",
        "PP-OCRv5_mobile_rec",
        "PP-LCNet_x1_0_doc_ori",
    ]
    if args.high_accuracy:
        expected.append("PP-OCRv5_server_rec")
    try:
        for name in expected:
            target = root / name
            if (target / "inference.yml").is_file():
                print(f"已存在，跳过下载：{name}")
                continue
            if target.exists():
                print(f"错误：模型目录已存在但不完整：{target}")
                print("请人工确认后移走该目录，再重新执行本脚本。")
                return 2
            # PaddleX 的官方模型管理器负责校验下载包并解压到缓存。
            # 然后复制到本项目的显式模型根目录，日常转换只读该目录。
            downloaded = Path(official_models[name])
            shutil.copytree(downloaded, target)
            print(f"已准备：{name}")

        PaddleOCR(
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_detection_model_dir=str(root / "PP-OCRv5_mobile_det"),
            text_recognition_model_name=(
                "PP-OCRv5_server_rec" if args.high_accuracy else "PP-OCRv5_mobile_rec"
            ),
            text_recognition_model_dir=str(
                root / ("PP-OCRv5_server_rec" if args.high_accuracy else "PP-OCRv5_mobile_rec")
            ),
            doc_orientation_classify_model_name="PP-LCNet_x1_0_doc_ori",
            doc_orientation_classify_model_dir=str(root / "PP-LCNet_x1_0_doc_ori"),
            use_doc_orientation_classify=True,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device="cpu",
        )
    except Exception as exc:
        print(f"错误：模型准备失败：{type(exc).__name__}: {exc}")
        return 2

    missing = [name for name in expected if not (root / name / "inference.yml").is_file()]
    if missing:
        print("错误：初始化后仍缺少模型目录：" + "、".join(missing))
        return 2
    print("已准备 PP-OCRv5 检测、识别和页面方向模型。")
    if args.high_accuracy:
        print("高精度识别模型已就绪，后续离线转换会优先使用它。")
    print("使用默认目录时无需额外配置；自定义目录请设置 LOCAL_DOC_CONVERTER_PADDLE_MODEL_DIR。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

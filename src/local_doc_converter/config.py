"""集中保存应用配置与支持格式。"""

from pathlib import Path

SUPPORTED_EXTENSIONS = {
    ".txt": "txt",
    ".md": "markdown",
    ".markdown": "markdown",
    ".docx": "docx",
    ".pdf": "pdf",
}
TARGET_EXTENSIONS = {"txt": ".txt", "markdown": ".md", "docx": ".docx"}
TARGET_LABELS = {"txt": "TXT", "markdown": "Markdown", "docx": "DOCX"}
FORMAT_LABELS = {**TARGET_LABELS, "pdf": "PDF"}

# Streamlit 会把上传文件保存在内存中，因此对单文件与批次同时限流。
MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_BATCH_SIZE = 200 * 1024 * 1024
MAX_BATCH_FILES = 100

# DOCX 是 ZIP 容器；以下限制约束解压后的资源消耗，避免小体积异常文件耗尽内存或磁盘。
DOCX_MAX_ENTRIES = 5_000
DOCX_MAX_TOTAL_UNCOMPRESSED_SIZE = 500 * 1024 * 1024
DOCX_MAX_MEMBER_UNCOMPRESSED_SIZE = 100 * 1024 * 1024
DOCX_MAX_TOTAL_COMPRESSION_RATIO = 200.0
DOCX_MAX_MEMBER_COMPRESSION_RATIO = 1_000.0

# PDF 预检只读取结构，不渲染页面。页数、对象数与物理尺寸限制用于提前拒绝
# 明显异常文件；后续 OCR 阶段还会增加像素总量和单页处理超时。
PDF_MAX_PAGES = 1_000
PDF_MAX_OBJECTS = 200_000
PDF_MAX_PAGE_DIMENSION_POINTS = 14_400.0  # 200 英寸，包含 UserUnit 缩放。
PDF_RENDER_DPI = 200
PDF_MAX_RENDER_PIXELS = 30_000_000
PDF_PARSE_TIMEOUT_SECONDS = 60.0
PDF_PROCESS_TERMINATE_GRACE_SECONDS = 1.0
PDF_OCR_STARTUP_TIMEOUT_SECONDS = 30.0
PDF_OCR_PAGE_TIMEOUT_SECONDS = 60.0

# PaddleOCR 模型始终从显式的本地目录加载，转换期间不触发下载。
PADDLE_MODEL_DIR_ENV = "LOCAL_DOC_CONVERTER_PADDLE_MODEL_DIR"
DEFAULT_PADDLE_MODEL_DIR = (
    Path.home() / "Library" / "Application Support" / "LocalDocConverter" / "models"
)
PADDLE_MODEL_NAMES = (
    "PP-OCRv5_mobile_det",
    "PP-OCRv5_mobile_rec",
    "PP-LCNet_x1_0_doc_ori",
)

DEFAULT_OUTPUT_DIR = Path.cwd() / "converted"

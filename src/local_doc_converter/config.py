"""集中保存应用配置与支持格式。"""

from pathlib import Path

SUPPORTED_EXTENSIONS = {".txt": "txt", ".md": "markdown", ".markdown": "markdown", ".docx": "docx"}
TARGET_EXTENSIONS = {"txt": ".txt", "markdown": ".md", "docx": ".docx"}
TARGET_LABELS = {"txt": "TXT", "markdown": "Markdown", "docx": "DOCX"}

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

DEFAULT_OUTPUT_DIR = Path.cwd() / "converted"

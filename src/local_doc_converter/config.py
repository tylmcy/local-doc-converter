"""集中保存应用配置与支持格式。"""

from pathlib import Path

SUPPORTED_EXTENSIONS = {".txt": "txt", ".md": "markdown", ".markdown": "markdown", ".docx": "docx"}
TARGET_EXTENSIONS = {"txt": ".txt", "markdown": ".md", "docx": ".docx"}
TARGET_LABELS = {"txt": "TXT", "markdown": "Markdown", "docx": "DOCX"}

# Streamlit 会把上传文件保存在内存中，因此对单文件与批次同时限流。
MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_BATCH_SIZE = 200 * 1024 * 1024
MAX_BATCH_FILES = 100
DEFAULT_OUTPUT_DIR = Path.cwd() / "converted"

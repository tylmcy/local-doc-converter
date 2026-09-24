#!/bin/zsh
set -e

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
cd "$PROJECT_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "未找到 uv。请先运行：brew install uv"
  exit 1
fi

if ! command -v pandoc >/dev/null 2>&1; then
  echo "未找到 Pandoc：PDF → TXT 仍可使用；其他格式请先运行：brew install pandoc"
fi

exec uv run streamlit run app.py --server.address=127.0.0.1

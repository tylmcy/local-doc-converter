"""Pandoc 可用性检查与安全的参数化调用。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .errors import PandocExecutionError, PandocNotFoundError


class PandocRunner:
    def __init__(self, executable: str | None = None, timeout: int = 120) -> None:
        self.executable = executable or shutil.which("pandoc")
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.executable)

    def require(self) -> str:
        if not self.executable:
            raise PandocNotFoundError(
                "未找到 Pandoc。请在 macOS 终端运行 `brew install pandoc`，安装后重新启动应用。"
            )
        return self.executable

    def version(self) -> str:
        result = self._run(["--version"])
        return result.stdout.splitlines()[0] if result.stdout else "Pandoc（版本未知）"

    def _run(self, args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        executable = self.require()
        try:
            result = subprocess.run(
                [executable, *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PandocExecutionError(f"Pandoc 处理超过 {self.timeout} 秒，已终止。") from exc
        except OSError as exc:
            raise PandocExecutionError(f"无法启动 Pandoc：{exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "未知错误").strip()[:1500]
            raise PandocExecutionError(f"Pandoc 转换失败：{detail}")
        return result

    def read_to_ast(
        self,
        source_path: Path,
        source_format: str,
        ast_path: Path,
        *,
        cwd: Path,
        extract_media: str | None = None,
    ) -> dict:
        readers = {
            "txt": "markdown+pipe_tables+fenced_code_blocks",
            # 普通 Markdown 把单换行视为空格；本工具面向文档互转，保留用户可见换行更符合预期。
            "markdown": "markdown+pipe_tables+fenced_code_blocks+strikeout+task_lists+hard_line_breaks",
            "docx": "docx",
        }
        args = [str(source_path), f"--from={readers[source_format]}", "--to=json", f"--output={ast_path}"]
        if extract_media:
            args.append(f"--extract-media={extract_media}")
        self._run(args, cwd=cwd)
        try:
            return json.loads(ast_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PandocExecutionError("无法读取 Pandoc 生成的中间结构。") from exc

    def write_from_ast(
        self,
        ast_path: Path,
        target_format: str,
        output_path: Path,
        *,
        cwd: Path,
        resource_paths: list[Path] | None = None,
    ) -> None:
        writers = {"txt": "plain", "markdown": "gfm", "docx": "docx"}
        args = [str(ast_path), "--from=json", f"--to={writers[target_format]}", f"--output={output_path}"]
        if target_format == "txt":
            args.append("--wrap=none")
        if target_format == "docx":
            args.append("--standalone")
        if resource_paths:
            joined = ":".join(str(path) for path in resource_paths)
            args.append(f"--resource-path={joined}")
        self._run(args, cwd=cwd)

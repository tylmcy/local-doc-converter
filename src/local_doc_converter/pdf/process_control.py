"""PDF 各阶段隔离子进程共用的终止与 JSON 结果工具。"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Any

from ..config import PDF_PROCESS_TERMINATE_GRACE_SECONDS
from ..errors import PdfExtractionError


def write_json_payload(path: Path, payload: dict[str, Any]) -> None:
    # ASCII 转义可容忍畸形 PDF 产生的孤立 Unicode surrogate。
    path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")


def terminate_process_group(process: subprocess.Popen[str]) -> None:
    """终止子进程及其可能创建的后代，并回收进程状态。"""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    else:
        process.terminate()
    try:
        process.communicate(timeout=PDF_PROCESS_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.communicate()


def read_json_payload(path: Path, *, stage_label: str) -> dict[str, Any]:
    if not path.is_file():
        raise PdfExtractionError(f"{stage_label}子进程未生成结果。")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PdfExtractionError(f"{stage_label}子进程返回了无效 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise PdfExtractionError(f"{stage_label}子进程结果根节点不是对象。")
    return payload


def start_worker(
    command: list[str],
    *,
    stage_label: str,
    capture_output: bool = True,
) -> subprocess.Popen[str]:
    try:
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            text=True,
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        raise PdfExtractionError(f"无法启动{stage_label}子进程：{exc}") from exc

"""Pandoc JSON AST 的轻量清理、统计与离线安全处理。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from .models import DocumentStats


def validate_ast(document: object) -> dict:
    if not isinstance(document, dict) or not isinstance(document.get("blocks"), list):
        raise ValueError("Pandoc 返回了无效的文档结构。")
    if not isinstance(document.get("meta"), dict):
        document["meta"] = {}
    return document


def collect_stats(document: dict) -> DocumentStats:
    stats = DocumentStats()

    def visit(node: object) -> None:
        if isinstance(node, dict):
            node_type = node.get("t")
            if node_type == "Header":
                stats.headings += 1
            elif node_type in {"BulletList", "OrderedList", "DefinitionList"}:
                stats.lists += 1
            elif node_type == "Table":
                stats.tables += 1
            elif node_type == "CodeBlock":
                stats.code_blocks += 1
            elif node_type == "Link":
                stats.links += 1
            elif node_type == "Image":
                stats.images += 1
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(document.get("blocks", []))
    return stats


def inspect_and_secure_images(document: dict, base_dir: Path, target_format: str) -> list[str]:
    """DOCX 输出前将远程/缺失图片降级为链接，保证 Pandoc 不会尝试联网。"""
    warnings: list[str] = []

    def visit(node: object) -> None:
        if isinstance(node, dict):
            if node.get("t") == "Image" and isinstance(node.get("c"), list) and len(node["c"]) >= 3:
                target = node["c"][2]
                source = target[0] if isinstance(target, list) and target else ""
                parsed = urlparse(source)
                remote = bool(parsed.scheme and parsed.scheme not in {"file", "data"})
                candidate = (base_dir / source).resolve(strict=False) if source and not parsed.scheme else None
                outside_base = bool(
                    candidate
                    and candidate != base_dir.resolve()
                    and base_dir.resolve() not in candidate.parents
                )
                missing = bool(candidate and not candidate.exists())
                if remote:
                    warnings.append(f"远程图片未下载，已保留为链接：{source}")
                elif parsed.scheme == "file" or outside_base:
                    warnings.append(f"图片路径超出文档目录，已阻止读取：{source}")
                elif missing:
                    warnings.append(f"找不到本地图片，已保留引用：{source}")
                if target_format == "docx" and (remote or missing or parsed.scheme == "file" or outside_base):
                    node["t"] = "Link"
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(document.get("blocks", []))
    # 去重但保持发现顺序，避免报告被同一图片刷屏。
    return list(dict.fromkeys(warnings))

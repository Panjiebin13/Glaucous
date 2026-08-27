"""只读搜索工具：grep（纯 Python re 实现）。

不调用系统 grep 子进程：跨平台（Linux 一等公民 + Windows 兼容）、
规避 shell 注入面；性能以大小/数量上限兜底（Plan §5 决策）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import Tool, ToolResult
from .files import UTF8, ReadFileTool

# 输出防护上限与扫描跳过规则
MAX_MATCHES = 200
MAX_FILE_BYTES = 5 * 1024 * 1024  # 5MB
SKIP_DIR_NAMES = {".git", "__pycache__", ".venv", "venv", "node_modules", ".glaucous"}


class GrepTool(Tool):
    """按正则逐行搜索工作区内文本文件，输出 path:line:content。"""

    name = "grep"
    description = (
        "在工作区内按正则表达式逐行搜索文件内容，输出「相对路径:行号:该行内容」。"
        "默认从工作区根目录递归搜索；pattern 为 Python 正则语法。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式"},
            "path": {"type": "string", "description": "搜索起始目录或文件，默认工作区根"},
        },
        "required": ["pattern"],
    }

    def __init__(self, workspace: Path, reader: ReadFileTool | None = None):
        self._workspace = workspace
        self._reader = reader

    def _resolve(self, path: str) -> Path:
        if self._reader is not None:
            return self._reader.resolve(path)
        p = Path(path) if path else self._workspace
        return p if p.is_absolute() else self._workspace / p

    async def execute(self, pattern: str = "", path: str = "", **_: Any) -> ToolResult:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return ToolResult(ok=False, content=f"正则表达式非法: {exc}")

        root = self._resolve(path)
        if not root.exists():
            return ToolResult(ok=False, content=f"路径不存在: {root}")

        files = [root] if root.is_file() else self._walk(root)
        matches: list[str] = []
        for file_path in files:
            self._search_file(regex, file_path, matches)
            if len(matches) >= MAX_MATCHES:
                break

        if not matches:
            return ToolResult(ok=True, content=f"未找到匹配: {pattern}")
        truncated = len(matches) >= MAX_MATCHES
        body = "\n".join(matches)
        if truncated:
            body += f"\n（已达 {MAX_MATCHES} 条命中上限，结果可能不完整，请缩小 path 范围或细化 pattern）"
        return ToolResult(ok=True, content=body)

    def _walk(self, root: Path) -> list[Path]:
        """收集待搜索文件：跳过 SKIP_DIR_NAMES 目录与超大/不可解码文件。"""
        collected: list[Path] = []
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                for entry in current.iterdir():
                    if entry.is_dir():
                        if entry.name not in SKIP_DIR_NAMES:
                            stack.append(entry)
                    elif entry.is_file():
                        collected.append(entry)
            except (PermissionError, OSError):
                continue  # 无权限子树整体跳过，不影响其余部分
        return collected

    def _search_file(self, regex: re.Pattern[str], file_path: Path, matches: list[str]) -> None:
        try:
            if file_path.stat().st_size > MAX_FILE_BYTES:
                return
            text = file_path.read_text(encoding=UTF8)
        except (UnicodeDecodeError, OSError, PermissionError):
            return  # 二进制/无权限文件静默跳过
        rel = file_path.relative_to(self._workspace) if file_path.is_relative_to(self._workspace) else file_path
        for line_no, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                matches.append(f"{rel.as_posix()}:{line_no}:{line.strip()}")
                if len(matches) >= MAX_MATCHES:
                    return

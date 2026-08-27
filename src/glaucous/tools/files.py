"""只读文件工具：read_file / list_dir。

路径约定（Plan §4.3）：相对路径一律相对 workspace 解析，绝对路径原样——
为 M1 工作区沙箱（realpath + 前缀校验）预留统一入口；Day 1 暂无沙箱。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Tool, ToolResult

# 输出防护上限：L0 截断（M2）前的最小上下文防爆措施，截断时显式标注
MAX_READ_LINES = 2000
UTF8 = "utf-8"


class ReadFileTool(Tool):
    """读取文本文件，输出带行号内容。"""

    name = "read_file"
    description = (
        "读取文本文件内容，输出带行号。"
        "path 支持相对路径（相对工作区）与绝对路径；"
        "offset/limit 用于分段读取大文件。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "offset": {"type": "integer", "minimum": 1, "description": "起始行号（从 1 开始）"},
            "limit": {"type": "integer", "minimum": 1, "description": "最多读取行数"},
        },
        "required": ["path"],
    }

    def __init__(self, workspace: Path):
        self._workspace = workspace

    def resolve(self, path: str) -> Path:
        """统一路径解析入口（M1 沙箱将在此追加边界校验）。"""
        p = Path(path)
        return p if p.is_absolute() else self._workspace / p

    async def execute(self, path: str = "", offset: int = 1, limit: int | None = None, **_: Any) -> ToolResult:
        target = self.resolve(path)
        if not target.exists():
            return ToolResult(ok=False, content=f"路径不存在: {target}")
        if not target.is_file():
            return ToolResult(ok=False, content=f"不是文件（可能是目录）: {target}")
        try:
            text = target.read_text(encoding=UTF8)
        except UnicodeDecodeError:
            return ToolResult(ok=False, content=f"无法以 UTF-8 解码（可能是二进制文件）: {target}")

        all_lines = text.splitlines()
        total = len(all_lines)
        start = max(1, offset)
        # 未指定 limit 时应用默认 2000 行上限（Plan §4.3：L0 截断前的最小上下文防爆措施）
        effective_limit = limit if limit is not None else MAX_READ_LINES
        end = min(total + 1, start + effective_limit)
        shown = all_lines[start - 1 : end - 1]

        if not shown and total:
            return ToolResult(ok=False, content=f"offset {start} 超出文件总行数 {total}")

        body = "\n".join(f"{i:>6}: {line}" for i, line in enumerate(shown, start=start))
        truncated = (start > 1) or (end - 1 < total)
        if truncated:
            shown_note = f"（已截断：显示第 {start}–{end - 1} 行，共 {total} 行；可调整 offset/limit 读取其余部分）"
            body = f"{body}\n{shown_note}" if body else shown_note
        return ToolResult(ok=True, content=body or "（空文件）")


class ListDirTool(Tool):
    """列出目录内容：目录以 / 结尾排在文件前，按名排序。"""

    name = "list_dir"
    description = "列出目录内容。目录项以 / 结尾并排在文件之前，按名称排序。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录路径，默认工作区根目录"},
        },
    }

    def __init__(self, workspace: Path, reader: ReadFileTool | None = None):
        self._workspace = workspace
        self._reader = reader

    async def execute(self, path: str = ".", **_: Any) -> ToolResult:
        target = self._reader.resolve(path) if self._reader else (self._workspace / path if path else self._workspace)
        if not target.exists():
            return ToolResult(ok=False, content=f"路径不存在: {target}")
        if not target.is_dir():
            return ToolResult(ok=False, content=f"不是目录: {target}")
        try:
            entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return ToolResult(ok=False, content=f"无权限访问目录: {target}")
        lines = [f"{entry.name}/" if entry.is_dir() else entry.name for entry in entries]
        return ToolResult(ok=True, content="\n".join(lines) if lines else "（空目录）")

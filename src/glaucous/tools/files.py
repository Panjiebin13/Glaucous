"""文件工具：read_file / list_dir / write_file / edit_file。

路径约定（M1 任务 1.1）：工具注入 Workspace 实例（permission/workspace.py），
相对路径相对工作区解析，统一 resolve() 规范化；沙箱逃逸校验 + 只读白名单
由 Workspace.classify_path 提供（Day3 Plan §4.5）。

写工具设计（Day3 Plan §4.3/§4.5）：
- 仅 Build 模式可用（modes={"build"}，声明层隐藏 + 执行层兜底）；
- 写前 diff 展示与审批**收敛到 dispatch 前的 approval.gate 管线**（三选项决策）；
  工具内不再内嵌 y/n 回调（移除 ApproveCallback，Day3 B3 修复）；
- edit_file 唯一匹配约束（概设 §5.6）：old 必须在文件中恰好出现一次，
  0 处/多处歧义均回喂引导而非静默选择，强迫先 read 后 edit。
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from ..permission.approval import ApprovalAction
from ..permission.risk import Risk
from ..permission.workspace import Workspace
from .base import MODE_BUILD, Tool, ToolResult

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

    def __init__(self, workspace: Workspace):
        self._workspace = workspace

    def resolve(self, path: str) -> Path:
        """统一路径解析入口（沙箱已内置逃逸校验 + 只读白名单）。"""
        return self._workspace.resolve(path)

    def build_approval(self, args: dict[str, Any], mode: str) -> ApprovalAction | None:
        """区外读触发审批（kind=file_read，FR-13「读取工作区外配置仍需单独同意」）。

        区内/只读白名单 = SAFE 免审；区外 = WRITE 走审批（auto-approve 下仍单独确认）；
        受保护目录（.glaucous/）读 = DANGEROUS（与 bash 分类器一致，防审计/会话被无感读取，S-C 修复）。
        """
        path = str(args.get("path", ""))
        if not path:
            return None
        resolved = self._workspace.resolve(path)
        if self._workspace.is_protected(resolved):
            return ApprovalAction(kind="file_read", target=path, detail=f"read_file {path}", risk=Risk.DANGEROUS)
        risk = self._workspace.classify_path(path)
        if risk == Risk.SAFE:
            return None
        return ApprovalAction(kind="file_read", target=path, detail=f"read_file {path}", risk=Risk.WRITE)

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

    def __init__(self, workspace: Workspace, reader: ReadFileTool | None = None):
        self._workspace = workspace
        self._reader = reader

    def build_approval(self, args: dict[str, Any], mode: str) -> ApprovalAction | None:
        """区外目录浏览触发审批（kind=file_read 语义，FR-13）。

        受保护目录（.glaucous/）浏览 = DANGEROUS（S-C 修复，与 bash 一致）。
        """
        path = str(args.get("path", "."))
        resolved = self._workspace.resolve(path)
        if self._workspace.is_protected(resolved):
            return ApprovalAction(kind="file_read", target=path, detail=f"list_dir {path}", risk=Risk.DANGEROUS)
        risk = self._workspace.classify_path(path)
        if risk == Risk.SAFE:
            return None
        return ApprovalAction(kind="file_read", target=path, detail=f"list_dir {path}", risk=Risk.WRITE)

    async def execute(self, path: str = ".", **_: Any) -> ToolResult:
        target = self._reader.resolve(path) if self._reader else self._workspace.resolve(path if path else ".")
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


# ---------------------------------------------------------------------------
# 写工具（仅 Build 模式；写前 diff 审批，任务 0.10/0.13）
# ---------------------------------------------------------------------------


class WriteFileTool(Tool):
    """写入文件（新建或覆盖整文件）。

    审批已收敛到 dispatch 前的 approval.gate（三选项决策，Day3 B3 修复）——
    工具只负责生成 diff 供审批展示与执行落地；.glaucous/ 写排除在 execute 兜底。
    """

    name = "write_file"
    description = (
        "写入文件（新建或整文件覆盖）。父目录不存在时自动创建。"
        "写入前会向用户展示 diff 并请求确认。仅 Build 模式可用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目标文件路径"},
            "content": {"type": "string", "description": "完整文件内容"},
        },
        "required": ["path", "content"],
    }
    modes = frozenset({MODE_BUILD})
    risk = Risk.WRITE

    def __init__(self, workspace: Workspace, reader: ReadFileTool | None = None):
        self._workspace = workspace
        self._reader = reader

    def build_approval(self, args: dict[str, Any], mode: str) -> ApprovalAction | None:
        """构造审批动作：区内写 WRITE；区外写 DANGEROUS（守卫优先级，不可批量豁免）。

        detail 携带 unified diff（写操作审批展示用，Day3 Plan §4.3）。
        """
        path = str(args.get("path", ""))
        content = str(args.get("content", ""))
        # 区内写 = WRITE（走审批）；区外/受保护/只读白名单写 = DANGEROUS（守卫优先级，
        # 概设 §5.5 写区外）。注意不能用 classify_path 返回值（区内返回 SAFE 会污染审计标注），
        # 也不能用 is_outside（白名单路径 is_read_only=True 会被误判为区内）
        resolved = self._workspace.resolve(path)
        if self._workspace.is_within(resolved) and not self._workspace.is_protected(resolved):
            risk = Risk.WRITE
        else:
            risk = Risk.DANGEROUS
        target = self._resolve(path)
        existing = self._read_existing(target)
        old_lines = existing.splitlines() if existing is not None else []
        diff = _make_diff(
            old_lines,
            content.splitlines(),
            str(target),
            f"{target}（修改后）",
            exists_before=existing is not None,
        )
        return ApprovalAction(
            kind="file_write",
            target=path,
            detail=diff or f"write_file {path}",
            risk=risk,
        )

    @staticmethod
    def _read_existing(target: Path) -> str | None:
        """读取已有内容（diff 基线）；不存在返回 None（新建场景）。"""
        if not target.exists():
            return None
        try:
            return target.read_text(encoding=UTF8)
        except UnicodeDecodeError:
            return None  # 二进制文件覆盖时 diff 退化为「整体替换」

    async def execute(self, path: str = "", content: str = "", **_: Any) -> ToolResult:
        target = self._resolve(path)
        if self._workspace.is_protected(target):
            # 运行期目录（.glaucous/ 审计/会话）不可被 agent 写（防篡改，Day3 §4.6 S4）
            return ToolResult(ok=False, content=f"禁止写入受保护目录: {target}")

        # 父目录自动创建：对齐业界工具行为，减少模型先 mkdir 的往返
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding=UTF8, newline="\n")
        line_count = content.count("\n") + 1 if content else 0
        return ToolResult(ok=True, content=f"已写入 {path}（{line_count} 行）")

    def _resolve(self, path: str) -> Path:
        if self._reader is not None:
            return self._reader.resolve(path)
        return self._workspace.resolve(path)


class EditFileTool(Tool):
    """精确编辑文件：old 文本唯一匹配替换为 new。

    唯一匹配约束（概设 §5.6）：old 恰好出现一次才执行；
    0 处/多处歧义回喂引导（强迫先 read 后 edit），replace_all=true 时全部替换。
    审批收敛到 dispatch 前的 approval.gate；.glaucous/ 写排除在 execute 兜底。
    """

    name = "edit_file"
    description = (
        "精确文本替换：将文件中 old 文本替换为 new。old 必须在文件中恰好出现一次；"
        "出现多处时需提供更长上下文使其唯一，或传 replace_all=true 全部替换。"
        "编辑前会向用户展示 diff 并请求确认。仅 Build 模式可用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目标文件路径"},
            "old": {"type": "string", "description": "要被替换的文本（需唯一匹配）"},
            "new": {"type": "string", "description": "替换后的文本"},
            "replace_all": {"type": "boolean", "description": "替换全部匹配处，默认 false"},
        },
        "required": ["path", "old", "new"],
    }
    modes = frozenset({MODE_BUILD})
    risk = Risk.WRITE

    def __init__(self, workspace: Workspace, reader: ReadFileTool | None = None):
        self._workspace = workspace
        self._reader = reader

    def build_approval(self, args: dict[str, Any], mode: str) -> ApprovalAction | None:
        """构造审批动作：区内写 WRITE；区外写 DANGEROUS（守卫优先级）。

        detail 携带替换前后 diff（审批展示用）。
        """
        path = str(args.get("path", ""))
        # 区内写 = WRITE；区外/受保护/只读白名单写 = DANGEROUS（S-A 修复，见 WriteFileTool）
        resolved = self._workspace.resolve(path)
        if self._workspace.is_within(resolved) and not self._workspace.is_protected(resolved):
            risk = Risk.WRITE
        else:
            risk = Risk.DANGEROUS
        target = self._resolve(path)
        old = str(args.get("old", ""))
        new = str(args.get("new", ""))
        replace_all = bool(args.get("replace_all", False))
        diff = ""
        # 空 old 防御：避免 text.count("") 恒 >0 产生误导性 diff（execute 层会拒绝）
        if old and target.is_file():
            try:
                text = target.read_text(encoding=UTF8)
            except UnicodeDecodeError:
                text = ""
            occurrences = text.count(old)
            if occurrences > 0:
                new_text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
                diff = _make_diff(
                    text.splitlines(),
                    new_text.splitlines(),
                    str(target),
                    f"{target}（修改后）",
                    exists_before=True,
                )
        return ApprovalAction(
            kind="file_write",
            target=path,
            detail=diff or f"edit_file {path}",
            risk=risk,
        )

    async def execute(
        self, path: str = "", old: str = "", new: str = "", replace_all: bool = False, **_: Any
    ) -> ToolResult:
        # 空串防御：空文件时 text.count("") 恒为 1 会通过唯一匹配校验，
        # 且 replace("", new, 1) 会产生意外的插入语义——直接回喂
        if not old:
            return ToolResult(ok=False, content="old 不能为空，请提供要替换的文本。")
        target = self._resolve(path)
        if self._workspace.is_protected(target):
            return ToolResult(ok=False, content=f"禁止写入受保护目录: {target}")
        if not target.exists():
            return ToolResult(ok=False, content=f"文件不存在: {path}。新建文件请使用 write_file。")
        if not target.is_file():
            return ToolResult(ok=False, content=f"不是文件: {target}")

        try:
            text = target.read_text(encoding=UTF8)
        except UnicodeDecodeError:
            # 与 read_file 惯例一致：二进制文件明确回喂而非替换出错
            return ToolResult(ok=False, content=f"无法以 UTF-8 解码（可能是二进制文件）: {target}")

        occurrences = text.count(old)
        if occurrences == 0:
            return ToolResult(
                ok=False,
                content=f"未找到匹配文本（{path}）。请先 read_file 确认文件当前内容后重试。",
            )
        if occurrences > 1 and not replace_all:
            return ToolResult(
                ok=False,
                content=(
                    f"匹配文本出现 {occurrences} 处（{path}），存在歧义。"
                    "请提供更长上下文使匹配唯一，或传 replace_all=true 全部替换。"
                ),
            )

        new_text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        target.write_text(new_text, encoding=UTF8, newline="\n")
        if replace_all and occurrences > 1:
            return ToolResult(ok=True, content=f"已修改 {path}：替换 {occurrences} 处")
        return ToolResult(ok=True, content=f"已修改 {path}")

    def _resolve(self, path: str) -> Path:
        if self._reader is not None:
            return self._reader.resolve(path)
        return self._workspace.resolve(path)


def _make_diff(
    old_lines: list[str], new_lines: list[str], old_label: str, new_label: str, *, exists_before: bool
) -> str:
    """生成 unified diff（写操作审批展示用）。

    新建文件的旧侧标注 (新建)——difflib 对空旧侧不输出行，标注保持语义清晰。
    """
    diff_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"{old_label}{' (新建)' if not exists_before else ''}",
            tofile=new_label,
            lineterm="",
        )
    )
    return "\n".join(diff_lines) if diff_lines else "（无内容差异）"


"""L0 回取工具：read_output 分段读取落盘的完整工具输出（任务 2.5，FR-24）。

设计要点（Day4 Plan §4.5）：
- 与 safety/output_limit.py 配对：截断落盘的输出经本工具按行分段回取，
  「大输出通常只有头尾有用，模型自主决定是否回取」（概设 §4.2）；
- 路径由系统自 outputs_dir 派生（call_id 经同规则净化），模型只传 id/offset，
  无沙箱面（Day4 Plan 决策 D8）；
- 单次回取上限 1000 行：防止回取行为本身重新挤爆上下文。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..safety.output_limit import sanitize_call_id
from .base import Tool, ToolResult

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000


class ReadOutputTool(Tool):
    """分段回取 L0 截断时落盘的完整工具输出。"""

    name = "read_output"
    description = (
        "分段读取此前被截断落盘的工具完整输出（提示行中给出的 call_id）。"
        "用 offset/limit 控制读取区间，通常先看头部，需要时再取后续区间。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "call_id": {"type": "string", "description": "被截断工具调用的 call_id"},
            "offset": {"type": "integer", "description": "起始行（从 0 计，默认 0）"},
            "limit": {"type": "integer", "description": "本次读取行数（默认 200，上限 1000）"},
        },
        "required": ["call_id"],
    }

    def __init__(self, outputs_dir: Path):
        self._outputs_dir = outputs_dir

    async def execute(
        self, call_id: str = "", offset: int = 0, limit: int = DEFAULT_LIMIT, **_: Any
    ) -> ToolResult:
        if not call_id.strip():
            return ToolResult(ok=False, content="call_id 不能为空。")
        path = self._outputs_dir / f"{sanitize_call_id(call_id.strip())}.log"
        if not path.is_file():
            return ToolResult(
                ok=False,
                content=f"未找到 call_id={call_id} 对应的落盘输出（该调用可能未被截断落盘）。",
            )
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return ToolResult(ok=False, content=f"读取落盘输出失败：{exc}")
        offset = max(0, int(offset))
        limit = min(max(1, int(limit)), MAX_LIMIT)
        window = lines[offset : offset + limit]
        if not window:
            return ToolResult(
                ok=False,
                content=f"区间越界：共 {len(lines)} 行，offset={offset} 已超出范围。",
            )
        header = f"共 {len(lines)} 行，当前显示第 {offset + 1}–{offset + len(window)} 行"
        if offset + len(window) < len(lines):
            header += "（还有后续内容，可增大 offset 继续）"
        return ToolResult(ok=True, content=header + "\n" + "\n".join(window))

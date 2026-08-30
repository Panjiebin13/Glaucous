"""spawn_agent 工具（v1.1-M2，FR-60；概设 §8.1）。

契约：父 agent 经本工具派发子任务（task 必填 + context 可选），子 agent
串行执行（await 到返回），结果仅以结构化报告形态回到父对话（FR-61/63）。
- 风险 SAFE：派发本身无副作用；子任务内部操作照常走权限管线（FR-62）；
- 全模式可用：Plan 下可派发只读评审任务；子 agent 模式取派发时父模式快照；
- 仅主 agent 注册（cli.build_registry）；子 registry 派生时排除本工具，
  声明层不可见 + 执行层「工具不存在」双保险防嵌套（FR-64）。

执行委托 SubagentRunner（agent/subagent.py）：工具层不感知 loop/权限细节。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..permission.modes import ALL_MODES
from ..permission.risk import Risk
from .base import Tool, ToolResult

if TYPE_CHECKING:
    from ..agent.subagent import SubagentRunner


class SpawnAgentTool(Tool):
    """派发子任务给子 agent 串行执行（仅主 agent 注册）。"""

    name = "spawn_agent"
    description = (
        "派发子任务给子 agent 串行执行（如：独立评审、隔离上下文的大块探索）。"
        "子 agent 拥有独立上下文与权限管线，完成后仅回传结构化报告"
        "（任务结果摘要/修改文件清单/验证结果/风险与遗留）；禁止嵌套派发。"
    )
    parameters = {
        "type": "object",
        "required": ["task"],
        "properties": {
            "task": {"type": "string", "description": "子任务描述（完整、自包含，含完成标准）"},
            "context": {
                "type": "string",
                "description": "补充上下文（可选：相关文件、约束、父任务背景）",
            },
        },
    }
    modes = ALL_MODES   # 子 agent 模式取派发时父模式快照（独立副本，spec §4.3）
    risk = Risk.SAFE    # 派发本身无副作用；子任务内部操作照常走权限管线

    def __init__(self, runner: "SubagentRunner") -> None:
        self._runner = runner

    async def execute(self, task: str, context: str = "") -> ToolResult:
        return await self._runner.run(task, context)

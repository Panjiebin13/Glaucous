"""skill 惰性加载工具：load_skill（任务 3.5，FR-28，概设 §7.3）。

设计要点（Day5 Plan §4.5）：
- 两段式加载的取正文通道：正文经工具结果入史，会话内持续可见、
  跨会话自然失效（「仅本会话生效」无需额外机制）；
- 两模式可用、risk=SAFE（读取的是注册表资产，无沙箱面）；
- 未知名回喂可用技能清单（与幻觉工具同款引导范式）。
"""

from __future__ import annotations

from typing import Any

from ..extensions.skills import SkillRegistry
from .base import Tool, ToolResult


class LoadSkillTool(Tool):
    """按名称加载已注册技能的详细步骤（启动时仅注入了名称与描述索引）。"""

    name = "load_skill"
    description = (
        "加载指定技能的详细步骤（会话内生效）。"
        "当任务与技能索引中某项描述相关时调用；一次任务通常只需加载一个最相关的技能。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "技能名称（须与技能索引中的名称完全一致）",
            },
        },
        "required": ["name"],
    }

    def __init__(self, registry: SkillRegistry):
        self._registry = registry

    async def execute(self, name: str = "", **_: Any) -> ToolResult:
        name = name.strip()
        if not name:
            return ToolResult(ok=False, content="name 不能为空，请给出技能索引中的技能名称。")
        body = self._registry.load(name)
        if body is None:
            infos = self._registry.infos()
            if not infos:
                return ToolResult(ok=False, content="当前未注册任何技能（无可加载项）。")
            available = "、".join(info.name for info in infos)
            return ToolResult(
                ok=False,
                content=f"技能 {name!r} 不存在。可用技能：{available}",
            )
        return ToolResult(ok=True, content=f"# 技能：{name}\n\n{body}")

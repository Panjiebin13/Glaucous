"""Plan 模式方案产出工具：submit_plan（Day 2 任务 0.12）。

设计要点（Day2 Plan §4.4，概设 §5.2/§7.4）：
- 仅 Plan 模式声明（modes={"plan"}）——Build 下幻觉调用被执行层回喂；
- 方案模板由 system prompt 引导（目标/澄清/设计/步骤/风险，简单任务轻量产出）；
- confirm 回调呈现三选一（①每次请求权限 ②同意所有权限 ③继续讨论），
  回调内部改 SessionState（闭包注入），决策结构化回喂让模型理解决策；
- Day 2 简化：方案全文经对话历史天然保留，不做 .glaucous/plans/<id>.md
  落盘与轻量锚（属 M2 上下文管理任务，Day2 Plan §5 已登记决策）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .base import MODE_PLAN, Tool, ToolResult

# 三选一决策常量（概设 §5.2 授权策略语义）
CHOICE_BUILD_PER_ACTION = "1"
CHOICE_BUILD_AUTO_APPROVE = "2"
CHOICE_KEEP_PLANNING = "3"


@dataclass
class PlanDecision:
    """三选一决策结果：choice 为 "1"/"2"/"3"；③ 可附用户反馈文字。"""

    choice: str
    feedback: str | None = None


# confirm 回调：入参方案全文，出参用户决策（CLI 实现为「打印方案 + 三选一输入」）
ConfirmCallback = Callable[[str], PlanDecision]


class SubmitPlanTool(Tool):
    """提交结构化方案并请求用户三选一确认，驱动 Plan→Build 切换。"""

    name = "submit_plan"
    description = (
        "在探索完成、方案明确后调用：向用户提交方案全文并请求确认。"
        "方案应包含：目标（需求复述与边界）、步骤（任务清单）、风险（可能的坑与回退方式），"
        "复杂任务可补充澄清记录与设计要点。用户将三选一："
        "①开始构建·每次请求权限 ②开始构建·同意所有权限 ③继续讨论。"
        "仅 Plan 模式可用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "plan": {"type": "string", "description": "方案全文（Markdown，含目标/步骤/风险）"},
        },
        "required": ["plan"],
    }
    modes = frozenset({MODE_PLAN})

    def __init__(self, confirm: ConfirmCallback):
        self._confirm = confirm

    async def execute(self, plan: str = "", **_: Any) -> ToolResult:
        if not plan.strip():
            return ToolResult(ok=False, content="plan 不能为空，请产出完整方案后再次提交。")

        decision = self._confirm(plan)

        if decision.choice == CHOICE_BUILD_PER_ACTION:
            # 决策回喂文案与 SessionState.enter_build 的调用方（CLI 闭包）约定一致
            return ToolResult(ok=True, content="用户选择①：开始构建（每次请求权限），已切换 Build 模式。请按方案步骤执行，写操作将逐项请求用户确认。")
        if decision.choice == CHOICE_BUILD_AUTO_APPROVE:
            return ToolResult(ok=True, content="用户选择②：开始构建（同意所有权限），已切换 Build 模式。请按方案步骤执行，写操作将自动放行。")
        # ③ 继续讨论：结构化回喂反馈，模型据此修订方案而非原样重提
        feedback = (decision.feedback or "").strip()
        feedback_part = f"用户反馈：{feedback}" if feedback else "用户未附加反馈。"
        return ToolResult(
            ok=True,
            content=f"用户选择③：继续讨论，仍处于 Plan 模式。{feedback_part}请根据反馈修订方案后再次提交。",
        )

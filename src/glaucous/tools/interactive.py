"""交互工具：ask_user 环境求助（任务 2.3，FR-17/18/19，概设 §7.1）。

设计要点（Day4 Plan §4.3）：
- 与 submit_plan 的 confirm 回调同构：工具持 AskCallback，CLI 注入终端实现
  （tools 层无 UI 依赖，分层约定同 Day2）；
- EOF/Ctrl+C 返回 None → 工具回喂「用户未响应」控制信号（非交互环境不挂死，
  模型据此改道而非无限等待）；
- options 的「0–6 个字符串」约束由 execute 内自校验（base.py 轻量 schema
  校验子集不支持 items/maxItems，Day4 Plan §4.3）；
- 求助节奏（先重试 2 次再求助）由 system prompt 引导，不在工具层强制。
"""

from __future__ import annotations

from typing import Any, Callable

from .base import Tool, ToolResult

# 单次提问候选上限：防止模型塞入超长选项列表挤占终端
MAX_OPTIONS = 6

# AskCallback：入参（question, options），返回用户回答原文；未响应/中断返回 None
AskCallback = Callable[[str, list[str]], str | None]


class AskUserTool(Tool):
    """环境难题求助：向用户提出具体问题并附候选答案，挂起等待回答。"""

    name = "ask_user"
    description = (
        "遇到自身无法解决的问题时向用户提问（找不到 JDK/解释器、缺凭证、任务歧义等）。"
        "问题必须具体可答，并附候选选项。注意：环境类失败应先自行重试 2 次，"
        "仍无果再调用本工具；获得答案后如包含环境事实，应调用 memory_save 沉淀。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "要请教的问题：说明缺什么、已尝试什么，具体可答",
            },
            "options": {
                "type": "array",
                "description": "候选答案列表（可选，最多 6 个，用户也可自由回答）",
            },
        },
        "required": ["question"],
    }

    def __init__(self, ask: AskCallback):
        self._ask = ask

    async def execute(
        self, question: str = "", options: list[Any] | None = None, **_: Any
    ) -> ToolResult:
        if not question.strip():
            return ToolResult(ok=False, content="question 不能为空，请描述清楚需要请教的问题。")
        # options 自校验（S-06）：过滤空/非字符串元素，截断到 MAX_OPTIONS
        opts = [str(opt).strip() for opt in (options or []) if str(opt).strip()][:MAX_OPTIONS]

        answer = self._ask(question.strip(), opts)
        if answer is None:
            # 用户未响应：控制信号回喂（非错误），模型应记录阻塞点并推进其他工作
            return ToolResult(
                ok=True,
                content="用户未响应（可能不在终端旁）。请记录该阻塞点，先继续其他可推进的工作，稍后再试或调整方案。",
            )
        answer = answer.strip()
        if not answer:
            return ToolResult(ok=True, content="用户未给出明确回答。请先按最常见的方式继续，并记录该假设。")
        note = "（用户从候选中选择了该答案）" if answer in opts else ""
        return ToolResult(ok=True, content=f"用户回答：{answer}{note}")

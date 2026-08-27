"""Agent 主循环 v0：请求 → tool_calls → 执行 → 回喂 → 终止。

主循环骨架（概设 §4.1）：
    push_user → 循环 { 守卫检查 → LLM 请求 → 无 tool_calls 即自然终止
                        → push_assistant → 逐个 dispatch → push_tool }

Day 1 终止条件（Plan §4.4）：
①自然终止（无 tool_calls，正常完成）
②步数上限（默认 50，防死循环硬熔断）
③解析失败熔断（ParseCircuitBroken 异常终止：loop 捕获后为悬空
  call_id 补推 ok=False 的 ToolMessage 保证 History 序列合法，
  再返回诊断文本——否则 REPL 后续请求会因 tool_call_id 悬空被 API 400 拒绝）

守卫检查点固定在「每次请求 LLM 之前」，保证无论循环从哪条路径
回来都会重新评估终止条件（概设 §4.1）。
"""

from __future__ import annotations

from typing import Any, Callable

from ..context.history import History, ToolMessage
from ..llm.client import LLMClient
from ..tools.base import ParseCircuitBroken, ToolRegistry

# loop → CLI 的事件契约（Plan §4.4）：Day 1 纯文本渲染，M3 升级 rich 主题
# 事件类型：text（流式正文）/ tool_start / tool_end / diagnostic（终止诊断，必达）
LoopEvent = Callable[[str, dict[str, Any]], None]


class AgentLoop:
    """单会话主循环。CLI 与 loop 共享同一 History 实例，跨轮次累积。"""

    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        history: History,
        max_steps: int = 50,
        on_event: LoopEvent | None = None,
    ):
        self._llm = llm
        self._registry = registry
        self._history = history
        self._max_steps = max_steps
        self._on_event = on_event

    async def run(self, task: str) -> str:
        """执行一轮用户任务，返回最终文本（自然终答 / 终止诊断）。"""
        self._registry.reset_parse_counter()  # 熔断语义限定在单任务内（Plan §4.1）
        self._history.push_user(task)
        steps = 0
        while True:
            # 守卫：任何 LLM 请求之前评估终止条件
            if steps >= self._max_steps:
                return self._terminate(
                    f"已达步数上限 {self._max_steps}，本轮终止。"
                    "已完成的部分保留在对话历史中，可继续提问或调整任务。"
                )
            steps += 1

            msg = await self._llm.chat(
                self._history.view(),
                tools=self._registry.tool_schemas(),
                on_text=self._emit_text,
            )

            if not msg.tool_calls:
                # ① 自然终止：终答入史（REPL 多轮语义：下一轮能看到本轮结论）
                self._history.push_assistant(msg)
                return msg.text or ""

            # assistant(tool_calls) 先入史：tool 消息配对的前提（OpenAI 协议硬约束）
            self._history.push_assistant(msg)

            # 已完成 dispatch 的 call 集合：异常时据此识别悬空 call_id
            dispatched: set[str] = set()
            try:
                for call in msg.tool_calls:
                    self._emit("tool_start", {"call": call})
                    result = await self._registry.dispatch(call)
                    dispatched.add(call.id)
                    self._history.push_tool(call, result)
                    self._emit("tool_end", {"call": call, "result": result})
            except ParseCircuitBroken as exc:
                # ③ 解析失败熔断：为悬空 call_id 补推 ok=False 结果，
                # 保证 History 序列合法后再终止（Plan §4.1 善后策略）
                self._salvage_dangling_calls(msg, dispatched, f"解析熔断终止，该调用未执行：{exc}")
                return self._terminate(f"任务终止：{exc}")
            except BaseException as exc:  # noqa: BLE001 —— 含 KeyboardInterrupt 等
                # 任何异常路径都不允许留下悬空 tool_call_id：一旦入史，
                # 共享 History 的后续每轮请求都会被 API 400 拒绝，会话静默报废。
                # 先善后（补推 ok=False 结果）再向上抛出，交由 REPL 决定中断语义。
                reason = type(exc).__name__ if not str(exc) else str(exc)
                self._salvage_dangling_calls(msg, dispatched, f"本轮中断，该调用未执行：{reason}")
                raise

    def _terminate(self, diagnostic: str) -> str:
        """终止路径的统一交付：诊断文本经事件通道显式通知（不依赖返回值推断）。

        步数上限/熔断路径往往发生在多步循环后，期间可能有中间步正文
        已流式输出——若仅靠 run() 返回值打印，CLI 无法区分「自然终答
        已流式交付」与「诊断未交付」；diagnostic 事件保证必达。
        """
        self._emit("diagnostic", {"text": diagnostic})
        return diagnostic

    def _salvage_dangling_calls(self, msg: Any, dispatched: set[str], reason: str) -> None:
        """为已入史但未完成 dispatch 的悬空 call_id 补推 ok=False 的 ToolMessage。

        OpenAI 协议要求 assistant.tool_calls 中每个 call 都必须有配对的
        tool 消息；否则 History 序列非法，后续请求被 API 400 拒绝。
        """
        for call in msg.tool_calls:
            if call.id not in dispatched:
                self._history.push_raw_tool(
                    ToolMessage(call_id=call.id, name=call.name, content=reason, ok=False)
                )

    # -- 事件与流式输出 ----------------------------------------------------

    def _emit_text(self, text: str) -> None:
        if self._on_event is not None:
            self._on_event("text", {"text": text})

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self._on_event is not None:
            self._on_event(event, payload)

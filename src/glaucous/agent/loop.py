"""Agent 主循环：请求 → tool_calls → 执行 → 回喂 → 终止（Day 2 叠加双模式）。

主循环骨架（概设 §4.1）：
    push_user → 循环 { 守卫检查 → mode 快照 → LLM 请求（tool_schemas(mode)）
                        → 无 tool_calls 即自然终止
                        → push_assistant → 逐个 dispatch(call, mode) → push_tool }

终止条件（Day 2 沿用 Day 1 三类）：
①自然终止（无 tool_calls，正常完成）——v1.1 起常驻 Build，不再回归 Plan
②步数上限（默认 50，防死循环硬熔断）
③解析失败熔断（ParseCircuitBroken 异常终止：loop 捕获后为悬空
  call_id 补推 ok=False 的 ToolMessage 保证 History 序列合法，
  再返回诊断文本——否则 REPL 后续请求会因 tool_call_id 悬空被 API 400 拒绝）

mode 快照语义（Day2 Plan §4.5）：每次 LLM 请求前取 state.mode 快照，
本轮声明层与执行层都用快照——submit_plan 轮中切换后，同轮后续幻觉的
写调用仍按 Plan 快照拦截回喂，下一轮起 Build 声明生效，消除
「声明层与执行层同轮不一致」的窗口。

mode_changed 统一出口（v1.1-M1 r1-B1 裁决：保留）：每轮 dispatch 结束后比对
state.mode 与快照，不一致即 emit——服务 submit_plan 批准回 Build 的
模式切换反馈；自然终止回归已随 v1.1 退役而不再发生。

守卫检查点固定在「每次请求 LLM 之前」，保证无论循环从哪条路径
回来都会重新评估终止条件（概设 §4.1）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..context import budget, compactor
from ..context.compactor import L1_KEEP_RECENT_ROUNDS, MAX_L2_FAILURES
from ..context.history import History, ToolMessage
from ..llm.client import LLMClient
from ..safety.output_limit import truncate_output
from ..tools.base import ParseCircuitBroken, ToolRegistry, ToolResult
from .state import SessionState

# loop → CLI 的事件契约（Day2 Plan §4.5）：Day 1 纯文本渲染，M3 升级 rich 主题
# 事件类型：text（流式正文）/ tool_start / tool_end / diagnostic（终止诊断，必达）
#          / mode_changed（模式切换或回归，payload: mode/policy/reason）
LoopEvent = Callable[[str, dict[str, Any]], None]


class AgentLoop:
    """单会话主循环。CLI 与 loop 共享同一 History/SessionState 实例，跨轮次累积。"""

    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        history: History,
        state: SessionState,
        max_steps: int = 50,
        on_event: LoopEvent | None = None,
        context_limit: int = 128_000,
        outputs_dir: Path | None = None,
        plans_dir: Path | None = None,
    ):
        self._llm = llm
        self._registry = registry
        self._history = history
        self._state = state
        self._max_steps = max_steps
        self._on_event = on_event
        # M2 上下文管理依赖（Day4 Plan §4.9）：预算上限 / L0 落盘目录 / 方案锚目录
        self._context_limit = context_limit
        self._outputs_dir = outputs_dir
        self._plans_dir = plans_dir
        self._l2_failures = 0  # L2 连续失败计数（达上限且仍 critical → 终止③，防压缩循环）

    async def run(self, task: str) -> str:
        """执行一轮用户任务，返回最终文本（自然终答 / 终止诊断）。"""
        self._registry.reset_parse_counter()  # 熔断语义限定在单任务内（Day1 Plan §4.1）
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

            # 守卫（M2 上下文管线）：预算评估 → L1 裁剪 → L2 压缩 → 预算耗尽终止③
            budget_abort = await self._enforce_budget()
            if budget_abort is not None:
                return self._terminate(budget_abort)

            # mode 快照：本轮声明层与执行层保持一致（Day2 Plan §4.5）
            mode_snapshot = self._state.mode

            msg = await self._llm.chat(
                self._history.view(),
                tools=self._registry.tool_schemas(mode_snapshot),
                on_text=self._emit_text,
            )

            if not msg.tool_calls:
                # ① 自然终止：终答入史（REPL 多轮语义：下一轮能看到本轮结论）。
                # v1.1 常驻 Build：不再回归 Plan（原 return_to_plan 分支退役）
                self._history.push_assistant(msg)
                self._emit_budget()
                return msg.text or ""

            # assistant(tool_calls) 先入史：tool 消息配对的前提（OpenAI 协议硬约束）
            self._history.push_assistant(msg)

            # 已完成 dispatch 的 call 集合：异常时据此识别悬空 call_id
            dispatched: set[str] = set()
            try:
                for call in msg.tool_calls:
                    self._emit("tool_start", {"call": call})
                    result = await self._registry.dispatch(call, mode_snapshot)
                    dispatched.add(call.id)
                    result = self._truncate_if_needed(call.id, result)
                    self._history.push_tool(call, result)
                    self._emit("tool_end", {"call": call, "result": result})
            except ParseCircuitBroken as exc:
                # ③ 解析失败熔断：为悬空 call_id 补推 ok=False 结果，
                # 保证 History 序列合法后再终止（Day1 Plan §4.1 善后策略）
                self._salvage_dangling_calls(msg, dispatched, f"解析熔断终止，该调用未执行：{exc}")
                return self._terminate(f"任务终止：{exc}")
            except BaseException as exc:  # noqa: BLE001 —— 含 KeyboardInterrupt/CancelledError
                # 任何异常路径都不允许留下悬空 tool_call_id：一旦入史，
                # 共享 History 的后续每轮请求都会被 API 400 拒绝，会话静默报废。
                # 先善后（补推 ok=False 结果）再向上抛出，交由 REPL 决定中断语义。
                reason = type(exc).__name__ if not str(exc) else str(exc)
                self._salvage_dangling_calls(msg, dispatched, f"本轮中断，该调用未执行：{reason}")
                raise

            # submit_plan 批准在 dispatch 内改 state（v1.1 二选：批准执行）——
            # 比对快照统一 emit mode_changed（Day2 Plan §4.5 统一出口；
            # v1.1 仅服务 PLAN 下批准回 Build 的切换反馈，BUILD 下批准比对为假不发射）
            if self._state.mode != mode_snapshot:
                self._emit(
                    "mode_changed",
                    {
                        "mode": self._state.mode,
                        "policy": self._state.approval_policy,
                        "reason": f"用户确认方案，已切换 {self._state.mode} 模式",
                    },
                )

    def _truncate_if_needed(self, call_id: str, result: ToolResult) -> ToolResult:
        """L0 输出截断（任务 2.5）：入史前替换超长正文，完整输出落盘可回取。

        metadata 不动——lines 等 记账字段保持原始值（UI 摘要/后续 L1 摘要用原始行数）；
        未配置 outputs_dir（测试/内嵌场景）时跳过截断。
        """
        if self._outputs_dir is None:
            return result
        content, _truncated = truncate_output(result.content, call_id, self._outputs_dir)
        if content != result.content:
            result.content = content
        return result

    async def _enforce_budget(self) -> str | None:
        """守卫点内的上下文预算管线（任务 2.4~2.7，概设 §4.2）。

        low：直接放行；>70%：L1 裁剪（本地派生摘要，幂等）；>85%：追加 L2 模型
        压缩（失败降级 L1 加深，连续失败达 MAX_L2_FAILURES 且仍 critical →
        终止③防压缩调用循环）；压缩后仍 ≥100% → 预算耗尽优雅终止（终止条件③）。
        返回 None 表示继续本轮请求，返回字符串为终止诊断。
        """
        report = budget.build_report(self._history.view(), self._context_limit)
        if report.level == "low":
            self._l2_failures = 0  # 占用回落，非连续失败（S-01：降回阈值即清零）
            return None
        compactor.trim_history(self._history.messages)
        report = budget.build_report(self._history.view(), self._context_limit)
        self._emit(
            "compressed",
            {
                "stage": "L1",
                "ok": True,
                "used": report.used,
                "limit": report.limit,
                "percent": round(report.percent, 4),
            },
        )
        if report.level != "critical":
            self._l2_failures = 0  # L1 已把占用压回阈值内：非连续失败，清零
            return None
        compressed = await compactor.compact_history(
            self._history.messages, self._llm, plans_dir=self._plans_dir
        )
        if compressed:
            self._l2_failures = 0
        else:
            self._l2_failures += 1
            compactor.trim_history(
                self._history.messages,
                keep_recent=max(1, L1_KEEP_RECENT_ROUNDS - self._l2_failures),
            )
        report = budget.build_report(self._history.view(), self._context_limit)
        self._emit(
            "compressed",
            {
                "stage": "L2",
                "ok": compressed,
                "used": report.used,
                "limit": report.limit,
                "percent": round(report.percent, 4),
            },
        )
        exhausted = report.percent >= 1.0
        # L2 反复失败且占用仍超阈值：继续重试只会每轮空转压缩调用（S-01/D12）
        l2_loop = (
            not compressed
            and self._l2_failures >= MAX_L2_FAILURES
            and report.level == "critical"
        )
        if exhausted or l2_loop:
            return (
                "上下文已达上限，压缩后仍超限。已完成部分保留在会话文件中，"
                "可 /exit 后 --resume 继续。"
            )
        return None

    def _emit_budget(self) -> None:
        """每轮结束的占用条事件（任务 2.4，FR-25）：数据与压缩管线同一来源（概设 §4.2）。"""
        report = budget.build_report(self._history.view(), self._context_limit)
        self._emit(
            "budget",
            {
                "used": report.used,
                "limit": report.limit,
                "percent": round(report.percent, 4),
                "level": report.level,
            },
        )

    def _terminate(self, diagnostic: str) -> str:
        """终止路径的统一交付：诊断文本经事件通道显式通知（不依赖返回值推断）。

        步数上限/熔断路径往往发生在多步循环后，期间可能有中间步正文
        已流式输出——若仅靠 run() 返回值打印，CLI 无法区分「自然终答
        已流式交付」与「诊断未交付」；diagnostic 事件保证必达。
        """
        self._emit("diagnostic", {"text": diagnostic})
        self._emit_budget()
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

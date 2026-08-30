"""子 AgentLoop 构造与串行执行（v1.1-M2，FR-61/63/64；概设 §8.2）。

生命周期（概设 §8.2）：
- 独立 History（.glaucous/agents/ 会话文件，不进会话索引）+ 独立 system prompt
  （角色 + 工作区 + glaucous.md 规则 + 任务描述；不注入记忆/技能索引）；
- SessionState 独立实例、快照复制父当前值（mode/policy/approved_types）——
  子内状态变化不回流父、父侧变化不影响进行中的子任务（spec §4.3）；
- ToolRegistry = 父 registry 全集去掉 spawn_agent → 防嵌套（FR-64）；
  工具实例共享（无 per-agent 状态；解析熔断计数在 registry 层天然隔离）；
- 复用父 LLMClient（usage 计入父轮 turn_usage 统计面；预算执行面独立）；
- 执行：父 dispatch spawn_agent 时 await（串行，FR-64）；中间过程经主
  on_event 以 sub_* 事件渲染（UI 可见、可折叠），不回传父史；
- 终结：终答 = 结构化报告 → ToolResult 回传（父史仅增 assistant+tool 两条，
  上下文零污染）；子会话文件保留（可追溯），审计记录归属（FR-62）。

归属切换（spec §4.4）：run 期间替换 ctx.active_state / active_agent /
active_task（submit_plan 批准副作用隔离 + 三类交互卡标注），finally 恢复
None/主 agent/"" 哨兵——永不捕获 state 实例（/clear、/resume 整体替换
后仍正确，D8）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..context.history import History
from ..llm.client import LLMClient
from ..permission.approval import ApprovalPipeline, AuditLog, DecisionCallback
from ..permission.modes import SessionState
from ..safety.output_limit import sanitize_call_id
from ..tools.base import ToolRegistry, ToolResult
from ..ui.prompts import build_sub_agent_prompt
from .loop import AgentLoop, LoopEvent

if TYPE_CHECKING:
    from ..commands import ReplContext

# 报告规范（概设 §4.4：四段；上限 1000 字——v1.1 用户决策 2026-08-30，由 400 放宽）
REPORT_MAX_CHARS = 1000
_REPORT_SECTIONS = ("【任务结果摘要】", "【修改文件清单】", "【验证结果】", "【风险与遗留】")
# 修改文件清单的采集源（write/edit 两写工具的 tool_end 事件）
_WRITE_TOOL_NAMES = frozenset({"write_file", "edit_file"})


@dataclass
class SubAgentInfo:
    """一次子 agent 派发的描述性信息（审计/调试/事件载荷用）。"""

    agent_id: str          # "child-<N>"（自增序号，与审计 agent 字段同源）
    task: str
    session_file: Path


def build_sub_registry(parent_registry: ToolRegistry) -> ToolRegistry:
    """父 registry 工具全集去掉 spawn_agent（FR-64 防嵌套），新建 registry 实例。

    工具实例共享（工具自身无 per-agent 状态）；解析熔断计数在 registry 层，
    新实例天然隔离；审批管线由调用方 set_approval_pipeline 注入子实例。
    """
    registry = ToolRegistry()
    for tool in parent_registry.all_tools():
        if tool.name == "spawn_agent":
            continue
        registry.register(tool)
    return registry


def build_report(
    answer: str,
    modified_files: list[str],
    outputs_dir: Path | None = None,
    call_id: str = "",
) -> str:
    """结构化报告拼装（FR-63；入史上限 REPORT_MAX_CHARS 字）。

    子终答已含四段标题 → 原样采用；否则按四段拼装（摘要=终答、
    修改文件=采集清单、验证结果/风险遗留缺省）。
    超限截断时（v1.1 用户决策 2026-08-30，外置型对齐 L0/read_output 机制）：
    完整报告落盘 .glaucous/outputs/<call_id>.log，尾注附真实回取提示——
    父模型按既有 L0 惯例即可取回全文（修复 e2e 实测的「猜 call_id 落空」缺陷）。
    """
    text = (answer or "").strip()
    if all(section in text for section in _REPORT_SECTIONS):
        full = text
    else:
        summary = text if text else "（子 agent 无文本输出）"
        files = "、".join(modified_files) if modified_files else "无"
        full = (
            f"【任务结果摘要】{summary}\n"
            f"【修改文件清单】{files}\n"
            f"【验证结果】未执行验证\n"
            f"【风险与遗留】无"
        )
    if len(full) <= REPORT_MAX_CHARS:
        return full
    body = full[:REPORT_MAX_CHARS]
    archive_note = "（报告已截断；完整报告存档失败，可请用户查看子会话文件）"
    if outputs_dir is not None and call_id:
        safe_id = sanitize_call_id(call_id)
        try:
            outputs_dir.mkdir(parents=True, exist_ok=True)
            (outputs_dir / f"{safe_id}.log").write_text(full, encoding="utf-8", newline="\n")
            archive_note = (
                f"（报告已截断；完整报告已存档 .glaucous/outputs/{safe_id}.log，"
                f'可调用 read_output(call_id="{call_id}") 分段查看）'
            )
        except OSError:
            pass  # 落盘失败尽力而为：保留截断正文，不阻断回传
    return body + archive_note


class SubagentRunner:
    """子 agent 串行执行器：构造子 loop 全套（registry/History/pipeline）并运行。"""

    # agent_id 类级自增计数：/clear、/resume 重建 runner 后 child-N 仍进程内唯一（r1-S2）
    _agent_seq = 0

    def __init__(
        self,
        llm: LLMClient,
        parent_registry: ToolRegistry,
        state: SessionState,                     # 父 state：构造时快照复制，绝不共享实例
        audit: AuditLog,
        decision_callback: DecisionCallback | None,   # 与父 pipeline 同源
        workspace: Path,
        rules: str,                              # glaucous.md 规则全文（接线时 load_rules 传入）
        max_steps: int,
        context_limit: int,
        outputs_dir: Path | None,
        plans_dir: Path | None,
        on_event: LoopEvent | None,              # 主 on_event（包装为 sub_* 事件发出）
        ctx: "ReplContext",                      # 归属切换载体（active_state/active_agent/active_task）
    ) -> None:
        self._llm = llm
        self._parent_registry = parent_registry
        self._state = state
        self._audit = audit
        self._decision_callback = decision_callback
        self._workspace = workspace
        self._rules = rules
        self._max_steps = max_steps
        self._context_limit = context_limit
        self._outputs_dir = outputs_dir
        self._plans_dir = plans_dir
        self._on_event = on_event
        self._ctx = ctx

    async def run(self, task: str, context: str = "") -> ToolResult:
        """派发一次子任务（串行：await 到报告返回才回到父 loop，FR-64）。"""
        # 入口校验：schema required 仅拦缺失不拦空串，runner 兜底（spec §七）
        if not task.strip():
            return ToolResult(
                ok=False,
                content="子任务描述不能为空。",
                metadata={"tool": "spawn_agent", "sub_agent": None, "modified_files": []},
            )

        SubagentRunner._agent_seq += 1
        agent_id = f"child-{SubagentRunner._agent_seq}"
        # 子 state 副本：快照复制父当前值，此后与父完全隔离（概设 §8.2/§8.3）
        child_state = SessionState(
            mode=self._state.mode,
            approval_policy=self._state.approval_policy,
            approved_types=set(self._state.approved_types),
        )
        sub_prompt = build_sub_agent_prompt(task, context, self._workspace, self._rules)
        sub_history = History.create(sub_prompt, self._workspace, subdir="agents")
        sub_pipeline = ApprovalPipeline(
            child_state,
            callback=self._decision_callback,
            audit=self._audit,
            agent_label=agent_id,
            agent_task=task,
        )
        sub_registry = build_sub_registry(self._parent_registry)
        sub_registry.set_approval_pipeline(sub_pipeline)

        # 修改文件清单采集（write/edit 的 tool_end 事件，保序去重）+ 事件透传包装
        modified: list[str] = []

        def sub_on_event(event: str, payload: dict[str, Any]) -> None:
            if event == "tool_end":
                call = payload.get("call")
                if call is not None and call.name in _WRITE_TOOL_NAMES:
                    try:
                        args = json.loads(call.arguments) if call.arguments.strip() else {}
                    except json.JSONDecodeError:
                        args = {}
                    path = args.get("path") if isinstance(args, dict) else None
                    if isinstance(path, str) and path not in modified:
                        modified.append(path)
            # 中间过程经主 on_event 以 sub_event 渲染（UI 可见、可折叠），不回传父史
            if self._on_event is not None:
                self._on_event(
                    "sub_event", {"agent_id": agent_id, "event": event, "payload": payload}
                )

        sub_loop = AgentLoop(
            self._llm,
            sub_registry,
            sub_history,
            child_state,
            max_steps=self._max_steps,
            on_event=sub_on_event,
            context_limit=self._context_limit,
            outputs_dir=self._outputs_dir,
            plans_dir=self._plans_dir,
        )

        self._emit("sub_start", {"agent_id": agent_id, "task": task})
        # 归属切换（try/finally 恢复哨兵；D8：不捕获 state 实例，spec §4.4）
        self._ctx.active_state = child_state
        self._ctx.active_agent = agent_id
        self._ctx.active_task = task
        try:
            # 任务注入：task 正文 + 可选 context 段（spec §3.1 步骤 9）
            user_text = task + (f"\n\n[补充上下文]\n{context.strip()}" if context.strip() else "")
            answer = await sub_loop.run(user_text)
        finally:
            self._ctx.active_state = None
            self._ctx.active_agent = "主 agent"
            self._ctx.active_task = ""

        report = build_report(
            answer,
            modified,
            outputs_dir=self._outputs_dir,
            call_id=f"spawn_agent-{agent_id}",
        )
        brief = report.splitlines()[0][:80] if report else ""
        self._emit("sub_end", {"agent_id": agent_id, "brief": brief, "ok": True})
        return ToolResult(
            ok=True,
            content=report,
            metadata={
                "sub_agent": agent_id,
                "session_file": str(sub_history.session_file or ""),
                "modified_files": list(modified),
            },
        )

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self._on_event is not None:
            self._on_event(event, payload)
